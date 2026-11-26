"""Which bots a thread is waiting on, and why (docs/dev/threads-contract.md §4.10): the status rows under a chat.

Every row is a wake: a person's message here that woke a bot (a queue or steer delivery, partial index
message_deliveries_open_bots), whose message the viewer reads, with what became of it. A client draws the rows as they
are and infers nothing. A bot has one row per run of its that took a wake here (naming the newest such message it
reads) and one for its wakes no run took yet (naming the newest), in plain words:

- `up_next`: its work here waits behind other work: "Finishing <task title>" or "Finishing #<thread>" when the viewer
  reads that place, else "Busy with other work"; "Starting" while nothing holds it.
- `cant_reply`: something stops it: its computer is offline, its claim is refused (queue_refusals, the claim's own
  rule), it is paused, out of budget, or the loop guard holds it. `fix` links to where a person fixes it.
- `working`: a run took it and is going; `run_id` and `detail` (the run's current step) only when the viewer reads
  the run as the `runs` stream would send it (events._runs: the bot's activity, the run's source thread, the run).
- `replied`, `no_reply`, `stopped`: how it ended, for ENDED_S after the run ended. `replied` only when the run that took
  it posted after it: its reply (`refs.turn_id`) or a tool post the run made (`refs.by_run`, hubdb._write_message).
  A run that answered with a tool post is `replied` while it is still going.

At most MAX_BOTS bots, oldest wait first. Read for one root at a time, in a fixed number of queries plus a few per
listed bot: the root's wakes with their job and run, the viewer's check of their messages, and the runs' replies.
"""

import json

from . import runner_online
from .harnesses import EXTERNAL_HARNESSES
from .store import H, bot_readiness, readiness_document

MAX_BOTS = 5
ENDED_S = 60  # how long a wake whose run ended still says how it ended
STEP_EVENTS = 60  # a run's newest events read for its current step
STEP_CHARS = 80
LIVE_JOBS = ("queued", "leased", "running", "input")
RUNNING = ("leased", "running")
SIGN_IN_REASONS = ("not_ready", "readiness_unknown", "sign_in_rejected", "sign_in_missing", "login_upgrade", "profile")
BUDGET_REASONS = ("limit", "out_of_credit", "budget", "cloud_locked")
BUSY_REASONS = ("bot_limit", "lane_held", "maintaining", "no_slot", "busy", "handed_back")
SIGN_IN_STATES = ("missing", "failed", "rejected", "unknown")
OFFLINE = "Its computer is offline"
PAUSED = "Paused"
OUT_OF_BUDGET = "Out of budget"
OUT_OF_CREDIT = "Out of credit: its cloud machine can't start until credit is added"
OUT_OF_CREDIT_MODELS = "Out of credit: its Tico model waits until credit is added"
BILLING = "#/settings/billing"
CAPPED = "Waiting: too many bot turns this hour"
BUSY = "Busy with other work"
STARTING = "Starting"
THINKING = "Thinking"
KEYS = ("actor", "name", "state", "detail", "since", "run_id", "fix", "message_id", "code")
ENDED = ("replied", "no_reply", "stopped")

_OPEN = (
    "d.root_id=?1 AND d.recipient LIKE 'bot:%' AND d.mode IN ('queue','steer') "
    "AND d.state IN ('pending','delivered','capped') AND d.cause='human'"
)  # word for word the partial index message_deliveries_open_bots
# Each SQL list below is word for word the partial index it reads: jobs_live_created, attempts_live_lease, and
# attempts_finished_state's states for a run that ended.
_JOB_LIVE = "j.state IN ('queued','leased','running','input')"
_RUN_LIVE = "a.state IN ('leased','running')"
_RUN_ENDED = "a.state IN ('input','completed','failed','interrupted','expired') AND a.finished_at>=?2"
# The root's wakes that can still say something, read from the few rows that can: capped ones, those whose job is live
# or whose run is going or ended in the last ENDED_S (the jobs it holds, or its inputs by the waking message),
# and an outside agent's that it has not read yet (its carrier, read from the reader's unread index). A wake whose job
# and run ended long ago is never read, however long the root's history. CROSS JOIN and INDEXED BY keep each lookup
# going from the few live rows to the root's, never the other way.
_PICKED_SQL = (
    "SELECT d.id FROM message_deliveries d INDEXED BY message_deliveries_capped WHERE {open} AND d.state='capped' "
    "UNION SELECT d.id FROM jobs j CROSS JOIN message_deliveries d INDEXED BY message_deliveries_carrier "
    "ON d.carrier_id=j.message_id "
    f"WHERE {_JOB_LIVE} AND {{open}} "
    "UNION SELECT d.id FROM jobs j CROSS JOIN message_deliveries d ON d.message_id=j.message_id "
    f"AND d.recipient='bot:'||j.bot_id WHERE {_JOB_LIVE} AND {{open}} "
    "UNION SELECT d.id FROM took a CROSS JOIN jobs j ON j.attempt_id=a.id CROSS JOIN message_deliveries d "
    "INDEXED BY message_deliveries_carrier ON d.carrier_id=j.message_id WHERE {open} "
    "UNION SELECT d.id FROM took a CROSS JOIN jobs j ON j.attempt_id=a.id CROSS JOIN message_deliveries d "
    "ON d.message_id=j.message_id AND d.recipient='bot:'||j.bot_id WHERE {open} "
    "UNION SELECT d.id FROM took a CROSS JOIN attempt_inputs i ON i.attempt_id=a.id CROSS JOIN message_deliveries d "
    "ON d.message_id=i.message_id AND d.recipient='bot:'||a.bot_id WHERE {open} "
    "UNION SELECT d.id FROM outside o CROSS JOIN messages m ON m.recipient_id=o.bot "
    "CROSS JOIN message_deliveries d INDEXED BY message_deliveries_carrier ON d.carrier_id=m.id "
    "WHERE (m.delivered_at IS NULL OR m.read_at IS NULL) "
    "AND m.deleted_at IS NULL AND m.read_at IS NULL AND {open}"
).format(open=_OPEN)
# The bots run by an outside agent (enqueue.external_bot_sql): they take their work with no job here, when they read it.
_OUTSIDE_SQL = "SELECT 'bot:'||bot_id AS bot FROM bot_settings WHERE harness IN ({})".format(
    ",".join("'" + h + "'" for h in EXTERNAL_HARNESSES)
)
# Those wakes with the job that carries each and the run that took it (its job's run, else the running turn it went
# into as an input), and whether it waits on an outside agent's read.
_WAKES_SQL = (
    f"WITH outside(bot) AS ({_OUTSIDE_SQL}), "
    f"took AS (SELECT a.id, a.bot_id FROM attempts a WHERE {_RUN_LIVE} "
    f"UNION ALL SELECT a.id, a.bot_id FROM attempts a WHERE {_RUN_ENDED}), "
    f"picked(id) AS ({_PICKED_SQL}) "
    "SELECT d.recipient, d.state, d.message_id, d.created_at, j.state AS job, a.id AS run, a.state AS run_state, "
    "COALESCE(a.started_at, a.created_at) AS started, a.finished_at, "
    "(SELECT m.conversation_id FROM jobs s JOIN messages m ON m.id=s.message_id WHERE s.id=a.job_id) AS run_thread, "
    "(d.recipient IN (SELECT bot FROM outside) AND EXISTS (SELECT 1 FROM messages c WHERE c.id=d.carrier_id "
    "AND c.read_at IS NULL)) AS outside_unread FROM message_deliveries d "
    "LEFT JOIN jobs j ON j.message_id=COALESCE(d.carrier_id, d.message_id) AND 'bot:'||j.bot_id=d.recipient "
    "LEFT JOIN attempts a ON a.id=COALESCE(j.attempt_id, "
    "(SELECT i.attempt_id FROM attempt_inputs i WHERE i.message_id=d.message_id)) AND 'bot:'||a.bot_id=d.recipient "
    "WHERE d.id IN (SELECT id FROM picked) ORDER BY d.created_at"
)
# Each post of these runs in the root by the run's own bot: its reply or a tool post it made. The run a run-token
# post names is its own (`by_run`, which the server stamps), whatever `turn_id` its caller wrote; another bot's post
# naming the run is not its answer.
_REPLIES_SQL = (
    "SELECT run, id FROM (SELECT COALESCE(json_extract(refs,'$.by_run'), json_extract(refs,'$.turn_id')) AS run, "
    "id, sender_id FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE id=?1 OR root_id=?1) "
    "AND seq>?2 AND sender_id LIKE 'bot:%' AND deleted_at IS NULL) p "
    "WHERE run IN (SELECT value FROM json_each(?3)) AND sender_id=(SELECT 'bot:'||bot_id FROM attempts WHERE id=p.run)"
)
# The root's newest `bot_status` change, the newest of its time first (the partial index changes_bot_status walked
# back one step), its seq the read's version.
_LAST_STATUS_SQL = (
    "SELECT created_at, seq FROM changes WHERE topic='threads' AND kind='bot_status' AND subject_id=? "
    "ORDER BY created_at DESC, seq DESC LIMIT 1"
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
_QUIET = "strftime('%Y-%m-%dT%H:%M:%fZ','now','-1 seconds')"


def trailing_roots(c):
    """The roots whose running turn took a step after their last `bot_status` change, once that one is 1 s old: a step
    the throttle (events.py changes_threads_bot_status_steps) held back. A read: a few indexed lookups per running
    turn, whatever its length."""
    quiet = c.execute(f"SELECT {_QUIET}").fetchone()[0]
    roots, last = [], {}
    for attempt_id, root in c.execute(_RUNNING_SQL).fetchall():
        if not root or root in roots:
            continue
        if root not in last:
            last[root] = (c.execute(_LAST_STATUS_SQL, (root,)).fetchone() or [None])[0]
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
        # The triggers' own clock (events._NOW), and again quiet for 1 s: a trigger may have sent one since the read.
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


def version(c, root):
    """The seq of the root's newest `bot_status` change (the newest by its time, then by seq), 0 before any: a read made
    later never has a smaller one, and two changes in the same millisecond still differ, so a client keeps the newest
    read's rows and drops an older read that lands after it. Read before the rows, in the same transaction, so a read
    never claims a newer version than its rows."""
    root_id = _root(c, root)
    row = c.execute(_LAST_STATUS_SQL, (root_id,)).fetchone() if root_id else None
    return int(row[1]) if row else 0


def candidates(c, v, root_id):
    """The root's wakes that still say something to viewer `v`, oldest first: each a _WAKES_SQL row whose message `v`
    reads, with `kind` (up_next, capped, working, replied, no_reply or stopped) and `run` (the run that took it, None
    while none has). The viewer's check comes first: no wake `v` cannot read is counted or limited."""
    since = H.shift(H.now(), seconds=-ENDED_S)
    rows = [dict(r) for r in c.execute(_WAKES_SQL, (root_id, since))]
    seqs = _readable(c, v, [r["message_id"] for r in rows])
    # Two wakes of the same instant keep the order of their messages: `status` says the newest ending first, and a
    # chat draws an agent's first row.
    rows = sorted(
        (r for r in rows if r["message_id"] in seqs), key=lambda r: (r["created_at"] or "", seqs[r["message_id"]])
    )
    runs = sorted({r["run"] for r in rows if r["run"]})
    replies = {}
    if runs:
        first = min(seqs[r["message_id"]] for r in rows if r["run"])
        posts = c.execute(_REPLIES_SQL, (root_id, first, json.dumps(runs))).fetchall()
        # only a reply the viewer reads answers the wake for them (a post in a sub-thread hidden from them does not)
        shown = _readable(c, v, [mid for _, mid in posts])
        for run, mid in posts:
            if mid in shown:
                replies[run] = max(replies.get(run, 0), shown[mid])
    out = []
    for r in rows:
        run, seq = r["run"], seqs[r["message_id"]]
        going = r["run_state"] in RUNNING
        recent = bool(run) and (going or (r["finished_at"] or "") >= since)
        if r["state"] == "capped":
            kind = "capped"
        elif r["job"] == "queued":
            kind = "up_next"  # its job waits again (deferred out of a run, or its run gave it back): no run has it now
        elif recent and (replies.get(run) or 0) > seq:
            kind = "replied"  # its run posted after it: going or not, the reply is in the chat
        elif recent and going:
            kind = "working"
        elif r["job"] in LIVE_JOBS:
            kind = "up_next"  # its job is live, and no run of it is going yet
        elif recent:
            kind = "no_reply" if r["run_state"] == "completed" else "stopped"
        elif r["outside_unread"]:
            kind = "up_next"  # an outside agent takes it with no job here, when it reads it
        else:
            continue
        out.append({**r, "kind": kind, "run": run if kind in ("working", *ENDED) else None})
    return out


def _readable(c, v, ids):
    """{message id: seq} for the messages, of `ids`, that viewer `v` reads: read in one query however many, each
    checked as a timeline checks it."""
    from . import threads

    ids = sorted({i for i in ids if i})
    out = {}
    if not ids:
        return out
    H.prime_messages(c, ids)  # the restricted checks look each message up again: one read for all of them
    for row in c.execute("SELECT * FROM messages WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(ids),)):
        message = {**dict(row), "refs": H._json(row["refs"], {}) or {}}
        if threads.message_readable(c, v, message):
            out[message["id"]] = message["seq"] or 0
    return out


def _runs_shown(c, v, wakes):
    """The runs of these wakes that viewer `v` reads as the `runs` stream sends them (events._runs): the bot's activity
    (one access map for the read), the thread the run started in, then the run (its provenance primed for all at once)."""
    from . import task_privacy, threads

    runs = {w["run"]: w for w in wakes}
    if not runs:
        return set()
    auth = getattr(v, "auth", None)
    access = auth.bot_accesses(c, v.who) if auth is not None else {}
    task_privacy.prime_attempts(c, sorted(runs))
    principal = task_privacy.actor(v.who)
    out = set()
    for run, w in runs.items():
        if auth is not None and not access.get(H.actor_id(w["recipient"]), auth.FULL)["read"]:
            continue
        if w["run_thread"] and not threads.thread_readable(c, v, w["run_thread"]):
            continue
        if task_privacy.attempt_readable(c, principal, run):
            out.add(run)
    return out


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


def _fix(href, label="Fix"):
    return {"label": label, "href": href} if href else None


def _limit_words(payer):
    """ "It hit its Antigravity usage limit (resets Nov 26 17:40 UTC)" from a refusal's recorded payer, or ""."""
    from .model_subscriptions import _when

    plan, until = (payer or {}).get("plan"), (payer or {}).get("until")
    return f"It hit its {plan} usage limit (resets {_when(until)})" if plan and until else ""


def _blocked(c, bot, location, refusal):
    """(detail, fix, code) when something stops the bot, else None; `refusal` is the claim's latest recorded refusal.
    The claim's refusal is read before "asleep": a stopped cloud machine is not woken for work a refusal holds, so the
    refusal is the cause (#3967). Its words and fix come from backend/wait_reasons.py; a code with none shows itself."""
    from . import wait_reasons
    from .cloud_machines import OUT_OF_CREDIT_WAIT, STALE, resting
    from .models_preset import LOGIN_NAMES

    row = H.bot(c, bot) or {}
    slug = row.get("slug") or bot
    settings_href = "#/bot/" + slug + "/settings"
    machine = location.get("machine") or {}
    computer = machine.get("runner_id")
    # Settings > Devices lists every computer, with its sign-ins: there is no page for one computer.
    computer_href = "#/settings/computers" if computer else None
    if (
        row.get("state") != "active"
        or c.execute("SELECT 1 FROM bot_control WHERE bot=? AND draining=1", (bot,)).fetchone()
    ):
        return PAUSED, _fix("#/bot/" + slug + "/more"), "paused"
    if location.get("external"):
        return None if location.get("online") else (OFFLINE, _fix("#/bot/" + slug + "/more"), "offline")
    reason = (refusal or {}).get("reason")
    if reason and (refusal or {}).get("runner_id") not in (None, computer):
        reason = None
    asleep = computer in resting(c)
    if not computer or not location.get("online") or asleep:
        from .views import cloud_machine_wait

        cloud = cloud_machine_wait(c, bot)
        if cloud == OUT_OF_CREDIT_WAIT:
            # Not "starting for this turn": nothing starts until credit lands, so the chat says so.
            return OUT_OF_CREDIT, _fix(BILLING), "out_of_credit"
        if computer and cloud:
            if not asleep:
                return None  # its cloud machine is starting for this turn
            if not reason or reason in STALE or reason in BUSY_REASONS:
                # Woken for this work: a stale report's refusal is what a wake clears.
                return cloud[0].upper() + cloud[1:], None, "asleep"
            # Else the refusal below holds the work, and the machine is not woken for it.
        else:
            return OFFLINE, _fix(computer_href or "#/bot/" + slug + "/more"), "offline"
    if not reason or reason in BUSY_REASONS:
        return None
    if reason == "draining":
        return PAUSED, _fix("#/bot/" + slug + "/more"), reason
    if reason == "out_of_credit":
        return OUT_OF_CREDIT_MODELS, _fix(BILLING), reason
    if reason in ("payer_wait", "cooling_off"):
        # A plan at its usage limit: name it and its reset, with Switch model (the chat adds Wait).
        words = _limit_words(refusal.get("payer")) or wait_reasons.say(reason)[0]
        return words, _fix(settings_href, "Switch model"), reason
    if reason in BUDGET_REASONS:
        return OUT_OF_BUDGET, _fix("#/finance", "Open Finance"), reason
    label = machine.get("label") or "its computer"
    if reason in SIGN_IN_REASONS:
        check = location.get("readiness") or {}
        runtime = check.get("runtime") or ""
        if str(check.get("sign_in") or "") in SIGN_IN_STATES or reason in ("sign_in_rejected", "sign_in_missing"):
            name = LOGIN_NAMES.get(runtime) or str(runtime or "Model").title()
            return f"{name} sign-in needed on {label}", _fix(computer_href, "Open Computers"), reason
    words, where = wait_reasons.say(reason, computer=label, home=_home(c, refusal))
    if where == wait_reasons.COMPUTERS:
        return words, _fix(computer_href, where[0]), reason
    if where == wait_reasons.BOT_SETTINGS:
        return words, _fix("#/bot/" + slug + "/more", where[0]), reason
    return words, _fix("#/" + "/".join(part for part in where[1:] if part).removeprefix("#/"), where[0]), reason


def _home(c, refusal):
    """The computer that holds a refused task's checkout or a turn's saved session, by label, or ""."""
    if (refusal or {}).get("reason") not in ("worktree_home", "worktree_conflict", "continuation_home"):
        return ""
    from .execution import continuation_run

    job = c.execute("SELECT * FROM jobs WHERE id=?", ((refusal or {}).get("job"),)).fetchone()
    if not job:
        return ""
    homes = set()
    if refusal["reason"] == "continuation_home":
        saved = continuation_run(c, job["id"])
        homes = {saved["runner_id"]} if saved else set()
    else:
        from .worktrees import worktree_homes

        from .execution import work_task_id

        message = H.message(c, job["message_id"])
        task_id = work_task_id(message, H.conversation(c, message["conversation_id"])) if message else None
        if task_id:
            homes = worktree_homes(c, job["bot_id"], task_id, task_id, None) or set()
    names = [
        found["label"]
        for home in sorted(homes)[:3]
        if (found := c.execute("SELECT label FROM runners WHERE id=?", (home,)).fetchone())
    ]
    return ", ".join(names)


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
    online = bool(not row["revoked_at"] and runner_online.is_online(row["last_seen_at"]))
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
    v = threads.viewer(None, v)
    by_bot = {}
    for wake in candidates(c, v, root_id):
        by_bot.setdefault(wake["recipient"], []).append(wake)
    order = sorted(by_bot.items(), key=lambda kv: (kv[1][0]["created_at"] or "", kv[0]))[:MAX_BOTS]
    if not order:
        return []
    shown = _runs_shown(c, v, [w for _, wakes in order for w in wakes if w["kind"] == "working"])
    refusals = queue_refusals.states(c, [H.actor_id(actor) for actor, _ in order])
    from .names import lookup

    names = lookup(c, [actor for actor, _ in order])
    out = []
    for actor, wakes in order:
        bot = H.actor_id(actor)
        base = {
            "actor": actor,
            "name": names.get(actor) or H.actor_name(c, actor),
            "run_id": None,
            "fix": None,
            "code": None,
        }
        runs, waiting = {}, None
        for w in wakes:  # oldest first: each row names the newest wake it stands for
            if w["run"]:
                at = runs.setdefault(
                    (w["run"], w["kind"]), {**base, "state": w["kind"], "detail": "", "since": w["created_at"]}
                )
                if w["kind"] == "working" and w["run"] in shown:
                    at.update(run_id=w["run"], since=w["started"])
            else:
                waiting = waiting or {**base, "since": w["created_at"], "capped": False}
                waiting["capped"] = waiting["capped"] or w["kind"] == "capped"
                at = waiting
            at["message_id"] = w["message_id"]
        working = [r for r in runs.values() if r["state"] == "working"]
        for r in working:
            if r["run_id"]:
                r["detail"] = _step(c, r["run_id"])
        out.extend(working)
        if waiting:
            if waiting["capped"]:
                waiting.update(state="cant_reply", detail=CAPPED)
            else:
                blocked = _blocked(c, bot, _location(c, bot), refusals.get(bot))
                if blocked:
                    waiting.update(state="cant_reply", detail=blocked[0], fix=blocked[1], code=blocked[2])
                else:
                    busy = BUSY if working else STARTING
                    waiting.update(state="up_next", detail=_elsewhere(c, v, bot, root_id) or busy)
            out.append(waiting)
        out.extend(reversed([r for r in runs.values() if r["state"] in ENDED]))  # the newest ending first
    return [{k: row[k] for k in KEYS} for row in out]
