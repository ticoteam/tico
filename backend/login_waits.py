"""What a run says when its ChatGPT login is busy with another turn (runner-rs/src/login_turns.rs: one Codex turn at a
time per ChatGPT login, as OpenAI's terms allow it).

The runner streams a `status` event `{"waiting": line, "login": label, "holder": bot}` while the turn waits for the
login and `{"waiting": ""}` once the login is its own, and a `diagnostic` `{"text", "fallback": harness, "busy": {"login",
"holder"}}` when the bot's own fallback took the turn instead. The chat and the task's Runs read them here. The bot that
holds the login is named only to a reader who may see that bot; anyone else reads "another bot".
"""

import json

from .turns import clip


def _payload(c, aid, kind, field):
    row = c.execute("SELECT payload_json FROM attempt_events WHERE attempt_id=? AND kind=? "
                    f"AND json_extract(payload_json,'$.{field}') IS NOT NULL ORDER BY seq DESC LIMIT 1",
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


def waiting(c, aid, bot, who=None, auth=None):
    """"Waiting for <login> to be free (busy with <bot>)" while the attempt waits for its login, else None.
    `who` None is the hub itself (every name shown); with `who` and no `auth`, the holder is never named."""
    payload = _payload(c, aid, "status", "waiting")
    if not payload.get("waiting"):
        return None
    login, holder = _named(payload.get("login")), _named(payload.get("holder"))
    if not login or not holder:
        return "Waiting for its ChatGPT login to be free"
    return f"Waiting for {login} to be free (busy with {_holder(c, who, auth, holder, bot)})"


def moved(c, aid, bot, who=None, auth=None):
    """Where the attempt went instead of its own harness and why, from its newest fallback note: "ChatGPT login
    busy with <bot>; ran this turn on <harness>", or the runner's own line for a usage limit or an unavailable
    harness (which names only harnesses). None when it never moved."""
    payload = _payload(c, aid, "diagnostic", "fallback")
    fallback = _named(payload.get("fallback"))
    if not fallback:
        return None
    busy = payload.get("busy") if isinstance(payload.get("busy"), dict) else {}
    login, holder = _named(busy.get("login")), _named(busy.get("holder"))
    if login and holder:
        login = login.removeprefix("this computer's ")
        return f"{login[:1].upper()}{login[1:]} busy with {_holder(c, who, auth, holder, bot)}; ran this turn on {fallback}"
    text = payload.get("text")
    return clip(text, 280) if isinstance(text, str) and text.strip() else f"Ran this turn on {fallback}"
