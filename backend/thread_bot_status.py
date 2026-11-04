"""Which bots a thread is waiting on, and why (docs/dev/threads-contract.md §4.10): the status rows under a chat.

A bot is listed for a root when a person's message there woke it (an open queue or steer delivery: pending, delivered
or capped) and the bot has not posted in that root since, or when one of its runs is going in that root. Each row says
what it is doing in plain words:

- `working`: its run here is going; `detail` is the run's current step, as the chat's live reply shows it.
- `up_next`: its work here waits behind other work: "Finishing <task title>" or "Finishing #<thread>" when the viewer
  reads that place, else "Busy with other work"; "Starting" while nothing holds it.
- `cant_reply`: something stops it: its computer is offline, its claim is refused (queue_refusals, the claim's own
  rule), it is paused, out of budget, or the loop guard holds it. `fix` links to where a person fixes it.

At most MAX_BOTS rows, oldest wait first. Read for one root at a time: the root's open deliveries and its running
attempts, then a few reads per listed bot.
"""

import json

from .store import H, bot_readiness, readiness_document

MAX_BOTS = 5
STEP_EVENTS = 60          # a run's newest events read for its current step
STEP_CHARS = 80
LIVE_JOBS = ("queued", "leased", "running", "input")
SIGN_IN_REASONS = ("not_ready", "readiness_unknown", "sign_in_rejected", "sign_in_missing", "login_upgrade", "profile")
BUDGET_REASONS = ("limit", "out_of_credit", "cooling_off", "budget", "cloud_locked")
BUSY_REASONS = ("bot_limit", "lane_held", "maintaining", "no_slot", "busy", "handed_back")
SIGN_IN_STATES = ("missing", "failed", "rejected", "unknown")
OFFLINE = "Its computer is offline"
PAUSED = "Paused"
OUT_OF_BUDGET = "Out of budget"
CAPPED = "Waiting: too many bot turns this hour"
BUSY = "Busy with other work"
STARTING = "Starting"

# The root's open wakes from a person's message: one row per bot, its oldest. A bot's post in the root after the
# message closes it. The partial index message_deliveries_open_bots (migration 0065) holds exactly these rows.
_OPEN_SQL = (
    "SELECT d.recipient, d.state, d.carrier_id, d.message_id, d.created_at FROM message_deliveries d "
    "JOIN messages m ON m.id=d.message_id WHERE d.root_id=? AND d.recipient LIKE 'bot:%' "
    "AND d.mode IN ('queue','steer') AND d.state IN ('pending','delivered','capped') AND d.cause='human' "
    "AND NOT EXISTS (SELECT 1 FROM messages p WHERE p.conversation_id IN "
    "(SELECT id FROM conversations WHERE root_id=d.root_id) AND p.sender_id=d.recipient AND p.seq>m.seq) "
    "ORDER BY d.created_at LIMIT 200")
_RUNS_SQL = (
    "SELECT a.id, a.bot_id, a.runner_id, a.state, COALESCE(a.started_at, a.created_at) AS since FROM attempts a "
    "JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id JOIN conversations t ON t.id=m.conversation_id "
    "WHERE a.state IN ('leased','running') AND t.root_id=? ORDER BY a.created_at")


def _root(c, root):
    row = root if isinstance(root, dict) else H.conversation(c, root)
    return (row or {}).get("root_id") or (row or {}).get("id")


def _job_live(c, message_ids):
    """{message or carrier id: (job state, attempt id or None)} for the jobs these messages started."""
    ids = sorted({i for i in message_ids if i})
    out = {}
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        for r in c.execute(f"SELECT message_id, state, attempt_id FROM jobs WHERE message_id IN ({','.join('?' * len(part))})",
                           part):
            out[r[0]] = (r[1], r[2])
    return out


def _inputs_live(c, message_ids):
    """The messages a running turn took in (a steer): {message id: attempt id}."""
    ids = sorted({i for i in message_ids if i})
    out = {}
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        for r in c.execute("SELECT i.message_id, a.id FROM attempt_inputs i JOIN attempts a ON a.id=i.attempt_id "
                           f"WHERE i.message_id IN ({','.join('?' * len(part))}) AND a.state IN ('leased','running')",
                           part):
            out[r[0]] = r[1]
    return out


def candidates(c, root_id):
    """{bot actor: {"since", "capped", "run"}} for the bots this root waits on, before they are explained."""
    found = {}
    rows = [dict(r) for r in c.execute(_OPEN_SQL, (root_id,))]
    keys = [r["carrier_id"] or r["message_id"] for r in rows]
    jobs = _job_live(c, keys)
    inputs = _inputs_live(c, keys + [r["message_id"] for r in rows])
    for r in rows:
        key = r["carrier_id"] or r["message_id"]
        job = jobs.get(key)
        run = inputs.get(key) or inputs.get(r["message_id"]) or (job[1] if job and job[0] in ("leased", "running")
                                                                  else None)
        if r["state"] != "capped" and not run and not (job and job[0] in LIVE_JOBS):
            continue                     # its turn ended without a post here: nothing waits any more
        at = found.setdefault(r["recipient"], {"since": r["created_at"], "capped": False, "run": None})
        at["capped"] = at["capped"] or r["state"] == "capped"
        at["since"] = min(at["since"], r["created_at"])
        if run:
            at["run"] = run
    for r in c.execute(_RUNS_SQL, (root_id,)):
        actor = "bot:" + r["bot_id"]
        at = found.setdefault(actor, {"since": r["since"], "capped": False, "run": None})
        at["run"] = at["run"] or r["id"]
    return found


def _step(c, attempt_id):
    """The run's current step in a person's words: its latest progress line or tool, else that it is writing."""
    from .turns import reply_parts
    rows = []
    for kind, payload, created in c.execute(
            "SELECT kind, payload, created_at FROM attempt_events WHERE attempt_id=? AND kind IN ('delta','message','tool') "
            "ORDER BY seq DESC LIMIT ?", (attempt_id, STEP_EVENTS)):
        try:
            value = json.loads(payload)
        except (TypeError, ValueError):
            value = {}
        rows.append((kind, value if isinstance(value, dict) else {}, created))
    parts = reply_parts(list(reversed(rows)))
    if not parts:
        return STARTING
    last = parts[-1]
    if last["kind"] == "reply":
        return "Writing a reply"
    text = " ".join(str(last["text"]).split())
    return text if len(text) <= STEP_CHARS else text[:STEP_CHARS - 1] + "…"


def _elsewhere(c, v, bot_id, root_id):
    """What the bot is finishing outside this root, said only as far as the viewer reads it."""
    from . import task_privacy, threads
    row = c.execute(
        "SELECT m.*, t.task_id AS thread_task, t.subject AS thread_subject, t.parent_id AS thread_parent, "
        "COALESCE(t.root_id, t.id) AS thread_root FROM attempts a JOIN jobs j ON j.id=a.job_id "
        "JOIN messages m ON m.id=j.message_id LEFT JOIN conversations t ON t.id=m.conversation_id "
        "WHERE a.bot_id=? AND a.state IN ('leased','running') ORDER BY a.created_at LIMIT 1", (bot_id,)).fetchone()
    if not row:
        return None
    row = dict(row)
    if row.get("thread_root") == root_id:
        return None
    message = {**row, "refs": H._json(row.get("refs"), {}) or {}}
    task_id = H.message_task_id(message, {"task_id": row.get("thread_task")})
    if task_id:
        task = H.task(c, task_id)
        try:
            if task and task_privacy.readable(c, task_privacy.actor(v.who), [task_id]):
                return "Finishing " + str(task.get("title") or "a task")
        except Exception:  # noqa: BLE001, S110 - an unreadable task is said as other work
            pass
        return BUSY
    root = row.get("thread_root")
    subject = (H.conversation(c, root) or {}).get("subject") if root else None
    if root and subject and threads.thread_readable(c, v, root):
        return "Finishing #" + str(subject)
    return BUSY


def _fix(href):
    return {"label": "Fix", "href": href} if href else None


def _blocked(c, bot, location, refusal):
    """(detail, fix) when something stops the bot, else None; `refusal` is the claim's latest recorded refusal."""
    from .health import BOT_SETTINGS, COMPUTERS, HELD
    from .models_preset import LOGIN_NAMES
    row = H.bot(c, bot) or {}
    slug = row.get("slug") or bot
    machine = location.get("machine") or {}
    computer = machine.get("runner_id")
    computer_href = "#/computer/" + computer if computer else None
    if row.get("state") != "active" or c.execute("SELECT 1 FROM bot_control WHERE bot=? AND draining=1",
                                                 (bot,)).fetchone():
        return PAUSED, _fix("#/bot/" + slug + "/more")
    if location.get("external"):
        return None if location.get("online") else (OFFLINE, _fix("#/bot/" + slug + "/more"))
    if not computer or not location.get("online"):
        from .views import cloud_machine_wait
        if computer and cloud_machine_wait(c, bot):
            return None                  # its cloud machine is starting for this turn
        return OFFLINE, _fix(computer_href or "#/bot/" + slug + "/more")
    reason = (refusal or {}).get("reason")
    if not reason or (refusal or {}).get("runner_id") not in (None, computer):
        return None
    if reason in BUSY_REASONS:
        return None
    if reason == "draining":
        return PAUSED, _fix("#/bot/" + slug + "/more")
    if reason in BUDGET_REASONS:
        return OUT_OF_BUDGET, _fix("#/finance")
    label = machine.get("label") or "its computer"
    if reason in SIGN_IN_REASONS:
        check = location.get("readiness") or {}
        runtime = check.get("runtime") or ""
        if str(check.get("sign_in") or "") in SIGN_IN_STATES or reason in ("sign_in_rejected", "sign_in_missing"):
            name = LOGIN_NAMES.get(runtime) or str(runtime or "Model").title()
            return f"{name} sign-in needed on {label}", _fix(computer_href)
    words, where = HELD.get(reason, ("Its computer can't start it", COMPUTERS))
    href = computer_href if where == COMPUTERS else "#/bot/" + slug + "/more" if where == BOT_SETTINGS else None
    return words.format(computer=label), _fix(href)


def _location(c, bot):
    """Where the bot runs (views.machine): its computer, whether it is in contact, and its readiness row there. An
    outside agent has no computer here: it is offline while it has not reported in lately."""
    from .agents import presence
    external = presence(c, bot)
    if external:
        return {"online": bool(external.get("online")), "machine": None, "readiness": {}, "external": True}
    row = c.execute("SELECT a.runner_id, r.label, r.last_seen_at, r.revoked_at, r.readiness FROM assignments a "
                    "JOIN runners_with_state r ON r.id=a.runner_id WHERE a.bot=?", (bot,)).fetchone()
    if not row:
        return {"online": False, "machine": None, "readiness": {}}
    from .readiness import ONLINE_SECONDS
    online = bool(not row["revoked_at"] and row["last_seen_at"]
                  and row["last_seen_at"] > H.shift(H.now(), seconds=-ONLINE_SECONDS))
    return {"online": online, "machine": {"runner_id": row["runner_id"], "label": row["label"]},
            "readiness": bot_readiness(readiness_document(row["readiness"]), bot)}


def status(c, v, root):
    """The bot status rows of this root for viewer `v`, who reads it (the caller checked)."""
    from . import queue_refusals
    root_id = _root(c, root)
    if not root_id:
        return []
    found = candidates(c, root_id)
    if not found:
        return []
    order = sorted(found.items(), key=lambda kv: (kv[1]["since"] or "", kv[0]))[:MAX_BOTS]
    bots = [H.actor_id(actor) for actor, _ in order]
    refusals = queue_refusals.states(c, bots)
    from .names import lookup
    names = lookup(c, [actor for actor, _ in order])
    out = []
    for actor, at in order:
        bot = H.actor_id(actor)
        row = {"actor": actor, "name": names.get(actor) or H.actor_name(c, actor), "since": at["since"],
               "run_id": None, "fix": None}
        if at["run"]:
            started = c.execute("SELECT COALESCE(started_at, created_at) FROM attempts WHERE id=?",
                                (at["run"],)).fetchone()
            row.update(state="working", detail=_step(c, at["run"]), run_id=at["run"],
                       since=started[0] if started else at["since"])
        elif at["capped"]:
            row.update(state="cant_reply", detail=CAPPED)
        else:
            blocked = _blocked(c, bot, _location(c, bot), refusals.get(bot))
            if blocked:
                row.update(state="cant_reply", detail=blocked[0], fix=blocked[1])
            else:
                row.update(state="up_next", detail=_elsewhere(c, v, bot, root_id) or STARTING)
        out.append({k: row[k] for k in ("actor", "name", "state", "detail", "since", "run_id", "fix")})
    return out
