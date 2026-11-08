"""What a run says for the optional login queue (runner-rs/src/login_turns.rs). Codex opts out: its bots run
concurrently on one ChatGPT login, and old queue events must not show a current wait.

The runner streams a `status` event `{"waiting": line, "login": label, "holder": bot, "holder_attempt", "holder_task",
"since", "position"}` while the turn waits for the login and `{"waiting": ""}` once the login is its own, a `status`
`{"yielded": line}` when a turn gave the login up at its hold cap, and a `diagnostic` `{"text", "fallback": harness,
"busy": {"login", "holder"}}` when the bot's own fallback took the turn instead. The chat, the task's Runs, the bot's
status and Health read them here. The bot that holds the login is named only to a reader who may see that bot (anyone
else reads "another bot"), and its task only to a reader who may read the holder's run. Every event goes out raw on
the changes feed and the run's own event lists to whoever reads the waiting run, so a wait is stored without the
holder's task or the runner's worded line (stored); the task is looked up from the holder's run when it is worded.
"""

import json
from datetime import datetime, timezone

from . import task_privacy as privacy
from .harnesses import harness_caps
from .store import H
from .turns import clip

# How long after the holder's run ended a waiter may still name it (its next status is a tick and a flush away).
HOLDER_GONE_S = 120
# What a run that gave its login up at the cap says when the runner's own line is missing.
PAUSED = "Paused so another turn could use the ChatGPT login; continues right after it"


def _payload(c, aid, kind, field):
    row = c.execute("SELECT payload FROM attempt_events WHERE attempt_id=? AND kind=? "
                    f"AND json_extract(payload,'$.{field}') IS NOT NULL ORDER BY seq DESC LIMIT 1",
                    (aid, kind)).fetchone()
    try:
        payload = json.loads(row[0]) if row else {}
    except (TypeError, ValueError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _holder(c, who, auth, holder, bot):
    if holder == bot:
        return "another turn of this bot"
    if who is None or auth is not None and auth.visible_bot(c, who, holder):
        return holder
    return "another bot"


def _named(value):
    return clip(value, 80) if isinstance(value, str) and value.strip() else ""


def _clock(epoch):
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).strftime("%H:%MZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def stored(kind, payload):
    """A runner event as it is kept: a login wait loses the holder's task and the runner's line, which name what a
    reader of the waiting run may not see; `waiting()` words it for each reader."""
    if kind != "status" or not isinstance(payload, dict) or not payload.get("waiting"):
        return payload
    login = _named(payload.get("login"))
    kept = {k: v for k, v in payload.items() if k != "holder_task"}
    kept["waiting"] = f"Waiting for {login} to be free" if login else "Waiting for its ChatGPT login to be free"
    return kept


def _task(c, run):
    """The task the holder's run works on, "#<number> <title>" as the runner names it, or ""."""
    row = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (run,)).fetchone()
    msg = H.message(c, row[0], include_deleted=True) if row else None
    task_id = H.message_task_id(msg, H.conversation(c, msg["conversation_id"])) if msg else None
    task = H.task(c, task_id) if task_id else None
    if not task:
        return ""
    return _named(f"#{task['number']} {task['title']}" if task.get("number") else task.get("title"))


def wait(c, aid):
    """The attempt's newest login wait as the runner reported it, or None once the login is its own."""
    # This event contract describes the ChatGPT queue. Once Codex opts out, ignore even the last wait an
    # older runner reported; it must not label a running bot as waiting or raise an unknown-holder finding.
    if not harness_caps("codex").get("one_turn_per_login"):
        return None
    payload = _payload(c, aid, "status", "waiting")
    return payload if payload.get("waiting") else None


def waiting(c, aid, bot, who=None, auth=None):
    """"Waiting for <login> (held by <bot> on <task> since <HH:MMZ>; position N)" while the attempt waits for its
    login, else None. `who` None is the hub itself (every name shown); with `who` and no `auth`, the holder is never
    named. A runner that sends no place yet says "Waiting for <login> to be free (busy with <bot>)"."""
    payload = wait(c, aid)
    if not payload:
        return None
    login, holder = _named(payload.get("login")), _named(payload.get("holder"))
    if not login or not holder:
        return "Waiting for its ChatGPT login to be free"
    named = _holder(c, who, auth, holder, bot)
    position = payload.get("position")
    if not isinstance(position, int) or isinstance(position, bool) or position < 1:
        return f"Waiting for {login} to be free (busy with {named})"
    run = payload.get("holder_attempt")
    task = ""
    # Visibility is of the holder bot itself, before "another turn of this bot" words it.
    if isinstance(run, str) and run and (who is None or auth is not None and auth.visible_bot(c, who, holder)
                                         and privacy.attempt_readable(c, privacy.actor(who), run)):
        task = _task(c, run)
    since = _clock(payload.get("since"))
    return (f"Waiting for {login} (held by {named}{' on ' + task if task else ''}"
            f"{' since ' + since if since else ''}; position {position})")


def unknown_holder(c, aid):
    """Whether the run this attempt waits behind is not a live run Tico knows (the holder named no attempt, or one
    that is gone or ended more than HOLDER_GONE_S ago): a login held by nothing a person can find, for Health."""
    payload = wait(c, aid)
    if not payload or not payload.get("position"):
        return False
    run = payload.get("holder_attempt")
    row = c.execute("SELECT state,finished_at FROM attempts WHERE id=?", (run,)).fetchone() if isinstance(run, str) and run else None
    if row and row["state"] in ("leased", "running"):
        return False
    return not row or not row["finished_at"] or row["finished_at"] < H.shift(H.now(), seconds=-HOLDER_GONE_S)


def paused(c, aid, state, result_json):
    """Why a run stopped to let another turn have its shared login (it continues next), or None."""
    if state != "interrupted":
        return None
    try:
        result = json.loads(result_json or "{}") or {}
    except (TypeError, ValueError):
        result = {}
    if not isinstance(result, dict) or not result.get("yielded"):
        return None
    line = _payload(c, aid, "status", "yielded").get("yielded")
    return clip(line, 200) if isinstance(line, str) and line.strip() else PAUSED


def moved(c, aid, bot, who=None, auth=None):
    """Where the attempt went instead of its own harness and why, from its newest fallback note: "ChatGPT login
    busy with <bot>; ran this turn on <harness>", or the runner's own line for a usage limit or an unavailable
    harness (which names only harnesses). None when it never moved."""
    payload = _payload(c, aid, "diagnostic", "fallback")
    fallback = _named(payload.get("fallback"))
    if not fallback:
        return None
    busy = payload.get("busy")
    busy = busy if isinstance(busy, dict) else {}
    login, holder = _named(busy.get("login")), _named(busy.get("holder"))
    if login and holder:
        login = login.removeprefix("this computer's ")
        return f"{login[:1].upper()}{login[1:]} busy with {_holder(c, who, auth, holder, bot)}; ran this turn on {fallback}"
    text = payload.get("text")
    return clip(text, 280) if isinstance(text, str) and text.strip() else f"Ran this turn on {fallback}"
