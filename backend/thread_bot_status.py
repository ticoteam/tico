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
STEP_EVENTS = 60  # a run's newest events read for its current step
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
THINKING = "Thinking"

# The root's open wakes from a person's message. A bot's post in the root after the message closes it: each bot's
# latest post in the root is read once (from the oldest open wake's seq on, by messages_thread_seq), never once per
# wake. The partial index message_deliveries_open_bots (migration 0068) holds exactly the open rows. Each bot keeps its
# newest OPEN_PER_BOT, so one bot's old wakes never crowd out another's.
OPEN_PER_BOT = 50
_OPEN_SQL = (
    "WITH open AS (SELECT d.recipient, d.state, d.carrier_id, d.message_id, d.created_at, m.seq FROM message_deliveries d "
    "JOIN messages m ON m.id=d.message_id WHERE d.root_id=?1 AND d.recipient LIKE 'bot:%' "
    "AND d.mode IN ('queue','steer') AND d.state IN ('pending','delivered','capped') AND d.cause='human'), "
    "posted AS (SELECT p.sender_id, MAX(p.seq) AS seq FROM messages p WHERE p.conversation_id IN "
    "(SELECT id FROM conversations WHERE root_id=?1 OR id=?1) AND p.seq>(SELECT MIN(seq) FROM open) "
    "AND p.sender_id IN (SELECT recipient FROM open) GROUP BY p.sender_id) "
    "SELECT recipient, state, carrier_id, message_id, created_at FROM (SELECT o.*, ROW_NUMBER() OVER "
    "(PARTITION BY o.recipient ORDER BY o.created_at DESC) AS n FROM open o LEFT JOIN posted q ON q.sender_id=o.recipient "
    "WHERE q.seq IS NULL OR o.seq>q.seq) WHERE n<=?2 ORDER BY created_at"
)
_RUNS_SQL = (
    "SELECT a.id, a.bot_id, a.runner_id, a.state, COALESCE(a.started_at, a.created_at) AS since FROM attempts a "
    "JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id JOIN conversations t ON t.id=m.conversation_id "
    "WHERE a.state IN ('leased','running') AND t.root_id=? ORDER BY a.created_at"
)


# The running turns and their roots (the partial index one_active_attempt_per_lane holds exactly these).
_RUNNING_SQL = (
    "SELECT a.id, COALESCE(t.root_id, t.id) FROM attempts a JOIN jobs j ON j.id=a.job_id "
    "JOIN messages m ON m.id=j.message_id JOIN conversations t ON t.id=m.conversation_id "
    "WHERE a.state IN ('leased','running')"
)
# A turn's newest step, walking its UNIQUE(attempt_id, seq) key back from the end: it reads only the deltas since that
# step, never the run's history.
_NEWEST_STEP_SQL = (
    "SELECT created_at FROM attempt_events WHERE attempt_id=? AND (kind IN ('tool','message') OR "
    "(kind='delta' AND json_extract(payload,'$.delta_kind')='thought')) ORDER BY seq DESC LIMIT 1"
)
_LAST_STATUS_SQL = "SELECT MAX(created_at) FROM changes WHERE topic='threads' AND kind='bot_status' AND subject_id=?"
_QUIET = "strftime('%Y-%m-%dT%H:%M:%fZ','now','-5 seconds')"


def trailing_roots(c):
    """The roots whose running turn took a step after their last `bot_status` change, once that one is 5 s old: a step
    the throttle (events.py changes_threads_bot_status_steps) held back. A read: a few indexed lookups per running
    turn, whatever its length."""
    quiet = c.execute(f"SELECT {_QUIET}").fetchone()[0]
    roots, last = [], {}
    for attempt_id, root in c.execute(_RUNNING_SQL).fetchall():
        if not root or root in roots:
            continue
        if root not in last:
            last[root] = c.execute(_LAST_STATUS_SQL, (root,)).fetchone()[0]
        if not last[root] or last[root] >= quiet:
            continue
        step = c.execute(_NEWEST_STEP_SQL, (attempt_id,)).fetchone()
        if step and step[0] and step[0] > last[root]:
            roots.append(root)
    return roots


def send_trailing(c, roots=None):
    """One `bot_status` change for each root whose running turn moved on since its last one was sent (`trailing_roots`,
    read before the write when `roots` is None): readers would otherwise stay on an earlier step until the turn ends.
    The scheduler's tick reads the roots outside its write lock and passes them. Returns how many it wrote."""
    roots = trailing_roots(c) if roots is None else roots
    wrote = 0
    for root in roots:
        # The triggers' own clock (events._NOW), and again quiet for 5 s: a trigger may have sent one since the read.
        wrote += c.execute(
            "INSERT INTO changes(topic,kind,subject_id,conversation_id,created_at) "
            "SELECT 'threads','bot_status',?1,?1,strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            f"WHERE NOT EXISTS (SELECT 1 FROM changes WHERE topic='threads' AND kind='bot_status' "
            f"AND subject_id=?1 AND created_at>={_QUIET})",
            (root,),
        ).rowcount
    return wrote


def _root(c, root):
    row = root if isinstance(root, dict) else H.conversation(c, root)
    return (row or {}).get("root_id") or (row or {}).get("id")


def _job_live(c, message_ids):
    """{message or carrier id: (job state, attempt id or None)} for the jobs these messages started."""
    ids = sorted({i for i in message_ids if i})
    out = {}
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        for r in c.execute(
            f"SELECT message_id, state, attempt_id FROM jobs WHERE message_id IN ({','.join('?' * len(part))})", part
        ):
            out[r[0]] = (r[1], r[2])
    return out


def _inputs_live(c, message_ids):
    """The messages a running turn took in (a steer): {(message id, bot actor): attempt id}. Keyed by the bot too: a
    message to one bot that names another is one bot's input, never the other's run."""
    ids = sorted({i for i in message_ids if i})
    out = {}
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        for r in c.execute(
            "SELECT i.message_id, a.bot_id, a.id FROM attempt_inputs i JOIN attempts a ON a.id=i.attempt_id "
            f"WHERE i.message_id IN ({','.join('?' * len(part))}) AND a.state IN ('leased','running')",
            part,
        ):
            out[(r[0], "bot:" + str(r[1]))] = r[2]
    return out


def candidates(c, root_id):
    """{bot actor: {"since", "capped", "run"}} for the bots this root waits on, before they are explained."""
    found = {}
    rows = [dict(r) for r in c.execute(_OPEN_SQL, (root_id, OPEN_PER_BOT))]
    keys = [r["carrier_id"] or r["message_id"] for r in rows]
    jobs = _job_live(c, keys)
    inputs = _inputs_live(c, keys + [r["message_id"] for r in rows])
    outside = {}
    for r in rows:
        key = r["carrier_id"] or r["message_id"]
        job = jobs.get(key)
        bot = r["recipient"]
        run = (
            inputs.get((key, bot))
            or inputs.get((r["message_id"], bot))
            or (job[1] if job and job[0] in ("leased", "running") else None)
        )
        if r["state"] != "capped" and not run and not (job and job[0] in LIVE_JOBS) and not _outside(c, bot, outside):
            continue  # its turn ended without a post here: nothing waits any more
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


def _outside(c, actor, seen):
    """Whether the bot is an outside agent (agents.presence): it takes its work with no job here, so its wake stays open
    until it posts. `seen` caches the answer per bot."""
    if actor not in seen:
        from .agents import external_harness

        seen[actor] = bool(external_harness(c, H.actor_id(actor)))
    return seen[actor]


_STEP_SQL = (
    "SELECT e.seq, e.kind, b.brief, CASE WHEN b.brief IS NULL THEN e.payload END FROM attempt_events e "
    "LEFT JOIN attempt_event_briefs b ON b.attempt_id=e.attempt_id AND b.seq=e.seq "
    "WHERE e.attempt_id=? AND e.kind IN ('delta','message','tool') ORDER BY e.seq DESC LIMIT ?"
)


def _step(c, attempt_id):
    """The run's current step in a person's words: its latest progress line or tool, else that it is writing, thinking or
    starting.

    The same word as turns.reply_parts over the run's newest STEP_EVENTS events, without decoding a big event: one with a
    brief (step_briefs) is read from it, any other from its payload. Only the newest piece decides the word."""
    from . import step_briefs

    rows = []
    for seq, kind, brief, payload in c.execute(_STEP_SQL, (attempt_id, STEP_EVENTS)):
        if brief is not None:
            rows.append((seq, kind, json.loads(brief)))
            continue
        try:
            value = json.loads(payload)
        except (TypeError, ValueError):
            value = {}
        value = value if isinstance(value, dict) else {}
        # A tool whose item id or label is too odd to keep is read as the replay reads it.
        rows.append((seq, kind, step_briefs.info(kind, value, keep_text=True) or {"raw": value}))
    # A run whose newest event is a thought is thinking now, whatever it did before; one with nothing yet is starting.
    if rows and rows[0][1] == "delta" and rows[0][2].get("t"):
        return THINKING
    last = _last_piece(c, attempt_id, list(reversed(rows)))
    if last is None:
        return STARTING
    if last["kind"] == "reply":
        return "Writing a reply"
    text = " ".join(last["label"].split())
    return text if len(text) <= STEP_CHARS else text[: STEP_CHARS - 1] + "…"


def _last_piece(c, attempt_id, rows):
    """turns.reply_parts's newest piece, from (seq, kind, step_briefs.info) rows, oldest first: a reply piece keeps the
    events that hold its text, a tool piece its label. A piece's text is blank when each of its events' is, and two
    texts compare by their stripped digest; only a piece typed across several events, one of them big, is read again
    to compare (a closing message that repeats it after a tool call)."""
    from .turns import TOOL_ENDED, tool_label

    pieces, typing, called = [], None, set()
    for seq, kind, row in rows:
        if "raw" in row:
            value = row["raw"]
            status = str(value.get("status") or value.get("state") or value.get("phase") or "").lower()
            row = {"s": status, "i": value.get("item_id"), "l": tool_label(value), "p": value.get("parent_id")}
        if kind == "delta":
            if row.get("t"):
                continue
            if typing is None:
                typing = {"kind": "reply", "events": [], "blank": True}
                pieces.append(typing)
            typing["events"].append((seq, row))
            typing["blank"] = typing["blank"] and bool(row["b"])
        elif kind == "message":
            if typing is not None:
                if not row["b"]:
                    typing.update(events=[(seq, row)], blank=False)
                typing = None
            elif not row["b"]:
                last = next((p for p in reversed(pieces) if p["kind"] != "tool"), None)
                if (
                    not last
                    or last["blank"]
                    or _digest(c, attempt_id, last["events"]) != _digest(c, attempt_id, [(seq, row)])
                ):
                    pieces.append({"kind": "reply", "events": [(seq, row)], "blank": False})
        elif kind == "tool":
            key = row.get("i")
            if row["s"] in TOOL_ENDED or (key and key in called) or row.get("p"):
                continue  # a subagent's own calls are its work, not the reply's
            called.add(key)
            typing = None
            pieces.append({"kind": "tool", "label": row["l"], "blank": not row["l"].strip()})
    return next((p for p in reversed(pieces) if not p["blank"]), None)


def _digest(c, attempt_id, events):
    """The stripped digest of a reply piece's text from its [(seq, info)] events, reading again only the big events of
    a piece typed across several.

    That re-read is the one place the status line can decode more than 64 KiB: a piece typed across several big
    deltas, ended by a tool call and then repeated by a closing message. Streamed deltas are small, so it stays rare."""
    from . import step_briefs

    if len(events) == 1:
        one = events[0][1]
        return one["h"] if "h" in one else step_briefs.digest(one.get("x", ""))
    missing = [seq for seq, one in events if "x" not in one]
    texts = {}
    if missing:
        for seq, payload in c.execute(
            "SELECT seq, payload FROM attempt_events WHERE attempt_id=? AND seq IN (%s)" % ",".join("?" * len(missing)),
            (attempt_id, *missing),
        ):
            try:
                value = json.loads(payload)
            except (TypeError, ValueError):
                value = {}
            texts[seq] = str((value if isinstance(value, dict) else {}).get("text", ""))
    return step_briefs.digest("".join(one["x"] if "x" in one else texts.get(seq, "") for seq, one in events))


def _elsewhere(c, v, bot_id, root_id):
    """What the bot is finishing outside this root, said only as far as the viewer reads it."""
    from . import task_privacy, threads

    row = c.execute(
        "SELECT m.*, t.task_id AS thread_task, t.subject AS thread_subject, t.parent_id AS thread_parent, "
        "COALESCE(t.root_id, t.id) AS thread_root FROM attempts a JOIN jobs j ON j.id=a.job_id "
        "JOIN messages m ON m.id=j.message_id LEFT JOIN conversations t ON t.id=m.conversation_id "
        "WHERE a.bot_id=? AND a.state IN ('leased','running') ORDER BY a.created_at LIMIT 1",
        (bot_id,),
    ).fetchone()
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
    from .cloud_machines import resting

    row = H.bot(c, bot) or {}
    slug = row.get("slug") or bot
    machine = location.get("machine") or {}
    computer = machine.get("runner_id")
    # Settings > Devices lists every computer, with its sign-ins: there is no page for one computer.
    computer_href = "#/settings/computers" if computer else None
    if (
        row.get("state") != "active"
        or c.execute("SELECT 1 FROM bot_control WHERE bot=? AND draining=1", (bot,)).fetchone()
    ):
        return PAUSED, _fix("#/bot/" + slug + "/more")
    if location.get("external"):
        return None if location.get("online") else (OFFLINE, _fix("#/bot/" + slug + "/more"))
    asleep = computer in resting(c)
    if not computer or not location.get("online") or asleep:
        from .views import cloud_machine_wait

        cloud = cloud_machine_wait(c, bot)
        if computer and cloud:
            if asleep:
                return cloud[0].upper() + cloud[1:], None
            return None  # its cloud machine is starting for this turn
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
    row = c.execute(
        "SELECT a.runner_id, r.label, r.last_seen_at, r.revoked_at, r.readiness FROM assignments a "
        "JOIN runners_with_state r ON r.id=a.runner_id WHERE a.bot=?",
        (bot,),
    ).fetchone()
    if not row:
        return {"online": False, "machine": None, "readiness": {}}
    from .readiness import ONLINE_SECONDS

    online = bool(
        not row["revoked_at"]
        and row["last_seen_at"]
        and row["last_seen_at"] > H.shift(H.now(), seconds=-ONLINE_SECONDS)
    )
    return {
        "online": online,
        "machine": {"runner_id": row["runner_id"], "label": row["label"]},
        "readiness": bot_readiness(readiness_document(row["readiness"]), bot),
    }


def status(c, v, root):
    """The bot status rows of this root for viewer `v`, who reads it (the caller checked). Nothing for one who reads
    only a migrated Home's old history: what its bots do now is not theirs to see."""
    from . import queue_refusals, threads

    root_id = _root(c, root)
    if not root_id:
        return []
    row = root if isinstance(root, dict) and root.get("id") == root_id else H.conversation(c, root_id)
    if row and threads.legacy_reader_only(c, threads.viewer(None, v), row):
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
        row = {
            "actor": actor,
            "name": names.get(actor) or H.actor_name(c, actor),
            "since": at["since"],
            "run_id": None,
            "fix": None,
        }
        if at["run"]:
            started = c.execute(
                "SELECT COALESCE(started_at, created_at) FROM attempts WHERE id=?", (at["run"],)
            ).fetchone()
            row.update(
                state="working",
                detail=_step(c, at["run"]),
                run_id=at["run"],
                since=started[0] if started else at["since"],
            )
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
