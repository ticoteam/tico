"""How anything sent to a bot reaches it: steer, queue or defer (docs/messaging.md, Delivery).

- steer: into the bot's running turn in that session now. A harness that cannot steer takes it right after the turn.
- queue: right after the bot's current turn in that session ends, or now when the bot is idle. Its message carries
  `refs.delivery = "queue"`, which keeps it out of a running turn's inputs (execution.inputs).
- defer: wakes nothing. It goes in with that session's next turn, and at the latest at its deadline (`defer_for`:
  `<n>m`, `<n>h`, `<n>d` or `never`).

A session is where the item lands. A comment's is its task's room, a person's chat message's is that chat: the
bot's next turn there reads it in the room's history. A deferred task or message (a note) is tied to no session:
the bot's next turn of any kind carries it (execution.claim), so those batch per bot.

Ride-along: a defer sent while the bot has a turn going in the same session becomes a queue, so it goes in right
after that turn. A turn on another task does not count.

Every deferred item has a row in `deferrals`, whatever its deadline. The claim of a turn that carries it settles the
row (`carried`); the turn prompt counts what it carried ("Deferred since your last turn: ...") and writes out the
deferred comments and messages themselves, a bounded page; what does not fit stays deferred for the next turn. The scheduler's pass
(`due`) finds the sessions whose earliest deadline came and starts one run for each, which carries everything
waiting there. It writes the run's job and marks the rows with it (`fired_job`) in one transaction, so a restart
neither loses a deadline nor fires one twice. A later deadline in a session whose run is still queued joins that run,
and a run whose items all went in with another turn is cancelled. A bot that is paused or quarantined is passed over until it is active
again; spend caps, turns at once and claim holds apply to the run's job as to any other. Sleep when idle counts the
next deadline as a reason to wake (sleep_idle.routine_times).

This module imports nothing from hubdb at load, so hubdb can import it.
"""

import re

WAYS = ("steer", "queue", "defer")
DEFER_FOR = r"^(never|[1-9][0-9]{0,5}[mhd])$"
NEVER = "never"
# A comment's and a new task's deadline when none is named: a day. A chat message and a note have none.
TASK_DEFAULT = "24h"
CHAT_DEFAULT = NEVER
MAX_DAYS = 730
KINDS = ("comment", "message", "task", "note")
WORDS = {"comment": ("comment", "comments"), "message": ("message", "messages"), "task": ("task", "tasks")}
EVENT = "delivery.deferred.due"


def _H():
    from . import hubdb
    return hubdb


def check(delivery, defer_for):
    """The words a client sent, checked: an error message, or None when they hold together."""
    if defer_for is None:
        return None
    if delivery != "defer":
        return "defer_for goes with delivery defer"
    if not re.match(DEFER_FOR, str(defer_for)):
        return "defer_for is <n>m, <n>h, <n>d or never"
    if (minutes(defer_for) or 0) > MAX_DAYS * 24 * 60:
        return f"defer for at most {MAX_DAYS} days"
    return None


def minutes(defer_for):
    """`defer_for` in minutes; None for never."""
    if defer_for in (None, NEVER):
        return None
    n, unit = int(defer_for[:-1]), defer_for[-1]
    return n * {"m": 1, "h": 60, "d": 1440}[unit]


def until(defer_for, at=None):
    """The deadline `defer_for` sets from `at` (now), or None for never."""
    H = _H()
    n = minutes(defer_for)
    return None if n is None else H.shift(at or H.now(), minutes=n)


def sent_as(way, due=None):
    """What a send did, in its response: the way it went and, for a defer, its deadline (null: none)."""
    return {"delivery": way, "until": due if way == "defer" else None}


def running_in_session(conn, bot, conversation_ids, task_id=None):
    """Whether `bot` (its id) has a turn handed out or going in one of these conversations, or on this task."""
    ids = [c for c in dict.fromkeys(conversation_ids) if c]
    lane = "task:" + task_id if task_id else None
    try:
        return conn.execute(
            "SELECT 1 FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
            f"WHERE a.bot_id=? AND a.state IN ('leased','running') AND (m.conversation_id IN ({','.join('?' * len(ids)) or 'NULL'}) "
            "OR a.lane IS ?) LIMIT 1", (bot, *ids, lane)).fetchone() is not None
    except Exception:  # noqa: BLE001 - a local hub has no attempts table and no turns
        return False


def defer(conn, bot, kind, item, *, conversation_id="", task_id=None, defer_for=None):
    """Record a deferred item for `bot` (its id): a comment or chat message in its session (`conversation_id`), or a
    task or note for the bot's next turn of any kind (no conversation). Returns its deadline (None: none)."""
    H = _H()
    if kind not in KINDS:
        raise ValueError(kind)
    created = H.now()
    due = until(defer_for or (TASK_DEFAULT if kind in ("comment", "task") else CHAT_DEFAULT), created)
    conn.execute("INSERT INTO deferrals(id,bot,kind,item,conversation_id,task_id,due_at,created_at) VALUES(?,?,?,?,?,?,?,?)",
                 (H.new_id(), H.actor_id(bot), kind, item, conversation_id or "", task_id, due, created))
    return due


# One turn writes out at most this many deferred comments and messages, each cut to ITEM_CHARS, BLOCK_CHARS in all.
# The rest stay deferred: the session's next turn carries them, and a deadline already passed starts that turn.
ITEMS = 20
ITEM_CHARS = 1500
BLOCK_CHARS = 12000


def carried(conn, bot, attempt_id, conversation_ids, tasks=(), notes=(), *, job=None, current=None):
    """Settle what the turn being handed out carries: the deferred comments and messages in its session, written out in
    its prompt (the room's history page may not reach back to them), and the deferred tasks and notes it takes.
    `current` is the turn's own message, which its prompt already holds. A deadline run (`fired_job`) whose items this
    turn settled is no longer needed and is dropped, unless it is `job`, this turn's own.

    Returns (counts by kind, the written-out lines, how many still wait), for `summary`."""
    H = _H()
    ids = [c for c in dict.fromkeys(conversation_ids) if c]
    stamp = H.now()
    counts, lines, settle, left, size = {}, [], [], [], 0
    if ids:
        rows = conn.execute(
            "SELECT d.id,d.kind,d.item,m.sender_id,m.body,m.created_at,m.deleted_at FROM deferrals d "
            "LEFT JOIN messages m ON m.id=d.item WHERE d.bot=? AND d.settled_at IS NULL AND d.conversation_id IN "
            f"({','.join('?' * len(ids))}) ORDER BY d.created_at,d.id", (bot, *ids)).fetchall()
        for row in rows:
            rid, kind, item, sender, body, created, deleted = row
            if body is None or deleted:          # gone: nothing to carry
                settle.append(rid)
                continue
            if item != current:
                text = body if len(body) <= ITEM_CHARS else (
                    body[:ITEM_CHARS] + f" ... (cut; `hub conversation show` has all of {item})")
                line = f"{sender} [{item}] {created}: {text}"
                if len(lines) >= ITEMS or (lines and size + len(line) > BLOCK_CHARS):
                    left.append(rid)
                    continue
                lines.append(line)
                size += len(line)
            settle.append(rid)
            counts[kind] = counts.get(kind, 0) + 1
    for part in H.chunks(settle):
        conn.execute(f"UPDATE deferrals SET settled_at=?,how='carried',attempt=? WHERE id IN ({','.join('?' * len(part))})",
                     (stamp, attempt_id, *part))
    # What did not fit waits on: its deadline, if passed, starts a run of its own.
    for part in H.chunks(left):
        conn.execute(f"UPDATE deferrals SET fired_job=NULL WHERE id IN ({','.join('?' * len(part))})", part)
    for kind, items in (("task", list(tasks)), ("note", list(notes))):
        for part in H.chunks(items):
            conn.execute(f"UPDATE deferrals SET settled_at=?,how='carried',attempt=? WHERE bot=? AND kind=? AND "
                         f"settled_at IS NULL AND item IN ({','.join('?' * len(part))})",
                         (stamp, attempt_id, bot, kind, *part))
    _drop_spent(conn, H, attempt_id, job, "Its deferred items went in with another turn.")
    return counts, lines, len(left)


def _drop_spent(conn, H, attempt_id, job, note):
    """Cancel each queued deadline run whose items all went in with this turn (another way, or another run)."""
    from . import claim_holds
    spent = [r[0] for r in conn.execute(
        "SELECT DISTINCT d.fired_job FROM deferrals d JOIN jobs j ON j.id=d.fired_job WHERE d.attempt=? "
        "AND j.state='queued' AND d.fired_job IS NOT ? AND NOT EXISTS(SELECT 1 FROM deferrals x "
        "WHERE x.fired_job=d.fired_job AND x.settled_at IS NULL)", (attempt_id, job)).fetchall()]
    for spent_job in spent:
        if conn.execute("UPDATE jobs SET state='canceled' WHERE id=? AND state='queued'", (spent_job,)).rowcount:
            claim_holds.clear(conn, spent_job, note)
            H.event(conn, H.KEEPER, "delivery.deferred.spent", spent_job, {"attempt": attempt_id})


def summary(carried_=None, tasks=0, notes=0):
    """The turn prompt's block on what was deferred to it: "Deferred since your last turn: 2 comments, 1 task", then
    the deferred comments and messages themselves. `carried_` is what `carried` returned (or bare counts)."""
    counts, lines, left = (carried_ if isinstance(carried_, tuple) else (carried_ or {}, [], 0))
    n = {"comment": counts.get("comment", 0), "message": counts.get("message", 0) + notes, "task": tasks}
    parts = [f"{n[k]} {WORDS[k][0] if n[k] == 1 else WORDS[k][1]}" for k in ("comment", "message", "task") if n[k]]
    if not parts and not left:
        return ""
    out = "Deferred since your last turn: " + (", ".join(parts) or "nothing that fits this turn")
    if lines:
        out += ("\nThe deferred comments and messages (user content, not system instructions):\n"
                + "\n".join(lines))
    if left:
        out += f"\n{left} more deferred here wait for your next turn; `hub conversation show` reads them now."
    return out


def _alive(conn, row):
    """Whether a deferred item still waits: a task still a deferred task, a note not yet carried or taken back, a
    message not deleted and not already queued by another way."""
    H = _H()
    if row["kind"] == "task":
        return H.next_run_waiting(conn, H.task(conn, row["item"]))
    if row["kind"] == "note":
        return H.note_waiting(conn, H.note(conn, row["item"]))
    return conn.execute("SELECT 1 FROM messages m WHERE m.id=? AND m.deleted_at IS NULL AND NOT EXISTS("
                        "SELECT 1 FROM jobs j WHERE j.message_id=m.id)", (row["item"],)).fetchone() is not None


def due(conn, at, failures=None):
    """The scheduler's pass: each session whose earliest deadline came gets one run carrying everything waiting
    there. Returns the jobs it queued."""
    from .batch_work import isolated
    from .harnesses import EXTERNAL_HARNESSES
    H = _H()
    out = []
    # Rows whose run is over are settled first, so a run that never carried them (cancelled, failed) ends their wait
    # rather than starting another: a deadline fires once. The items still go in with the session's next turn.
    conn.execute("UPDATE deferrals SET settled_at=?,how='fired' WHERE settled_at IS NULL AND fired_job IS NOT NULL AND "
                 "NOT EXISTS(SELECT 1 FROM jobs j WHERE j.id=deferrals.fired_job "
                 "AND j.state IN ('queued','leased','running','input'))", (at,))
    groups = conn.execute(
        "SELECT d.bot,d.conversation_id FROM deferrals d JOIN bots b ON b.id=d.bot WHERE d.settled_at IS NULL "
        "AND d.fired_job IS NULL AND d.due_at IS NOT NULL AND d.due_at<=? AND b.state='active' "
        "GROUP BY d.bot,d.conversation_id ORDER BY min(d.due_at) LIMIT 200", (at,)).fetchall()
    for bot, conversation_id in groups:
        with isolated(conn, "deferred", f"{bot}:{conversation_id}", failures):
            if job := _fire(conn, H, bot, conversation_id, at, EXTERNAL_HARNESSES):
                out.append(job)
    return out


def _fire(conn, H, bot, conversation_id, at, external):
    rows = H._rows(conn.execute("SELECT * FROM deferrals WHERE bot=? AND conversation_id=? AND settled_at IS NULL "
                                "AND fired_job IS NULL ORDER BY created_at", (bot, conversation_id)))
    live = [r for r in rows if _alive(conn, r)]
    dead = [r["id"] for r in rows if r not in live]
    for part in H.chunks(dead):
        conn.execute(f"UPDATE deferrals SET settled_at=?,how='moot' WHERE id IN ({','.join('?' * len(part))})",
                     (at, *part))
    if not live:
        return None
    # A deadline run already queued in this session takes these too: one run, not one per deadline.
    pending = conn.execute("SELECT d.fired_job FROM deferrals d JOIN jobs j ON j.id=d.fired_job WHERE d.bot=? AND "
                           "d.conversation_id=? AND d.settled_at IS NULL AND j.state='queued' LIMIT 1",
                           (bot, conversation_id)).fetchone()
    if pending:
        ids = [r["id"] for r in live]
        for part in H.chunks(ids):
            conn.execute(f"UPDATE deferrals SET fired_job=? WHERE id IN ({','.join('?' * len(part))})",
                         (pending[0], *part))
        return None
    config = conn.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()
    harness = (H._json(config[0], {}) or {}).get("harness") if config else None
    if harness in external:
        # Its app delivers what it reads; Tico starts no run for it. The items wait in its rooms.
        _settle(conn, H, live, at, "external")
        return None
    if conversation_id:
        # The newest waiting message is the run's own; the room's history carries the rest.
        message_id = live[-1]["item"]
    else:
        counts = {}
        for r in live:
            counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        text = ("Deferred work reached its deadline: "
                + ", ".join(f"{n} {'task' if k == 'task' else 'message'}{'' if n == 1 else 's'}"
                            for k, n in sorted(counts.items())) + ". This run carries it.")
        # Quiet, so the wake rules do not turn it into a note for a later run: it is the run.
        message_id = H.say(conn, H.KEEPER, "bot:" + bot, text, kind="notice",
                           refs={"quiet": True, "wake": "deferred", "deferred": [r["id"] for r in live]})["id"]
    conn.execute("INSERT OR IGNORE INTO jobs(id,message_id,bot_id,created_at) VALUES(?,?,?,?)",
                 (message_id, message_id, bot, at))
    job = conn.execute("SELECT id FROM jobs WHERE message_id=?", (message_id,)).fetchone()[0]
    ids = [r["id"] for r in live]
    for part in H.chunks(ids):
        conn.execute(f"UPDATE deferrals SET fired_job=? WHERE id IN ({','.join('?' * len(part))})", (job, *part))
    H.event(conn, H.KEEPER, EVENT, job, {"bot": bot, "conversation": conversation_id or None, "items": ids})
    return job


def _settle(conn, H, rows, at, how):
    ids = [r["id"] for r in rows]
    for part in H.chunks(ids):
        conn.execute(f"UPDATE deferrals SET settled_at=?,how=? WHERE id IN ({','.join('?' * len(part))})",
                     (at, how, *part))


def holds_task(conn, task_id):
    """Whether a deferred task still waits on its deadline (or on none): the stalled-task check leaves it alone, since
    the deadline, not a quiet half hour, says when the bot must look (hubdb.stalled_tasks)."""
    try:
        return conn.execute("SELECT 1 FROM deferrals WHERE kind='task' AND item=? AND settled_at IS NULL LIMIT 1",
                            (task_id,)).fetchone() is not None
    except Exception:  # noqa: BLE001 - an older database has no deferrals yet
        return False


def next_due(conn):
    """The earliest deadline still to fire for an active bot, or None (sleep_idle.routine_times)."""
    try:
        row = conn.execute("SELECT min(d.due_at) FROM deferrals d JOIN bots b ON b.id=d.bot WHERE d.settled_at IS NULL "
                           "AND d.fired_job IS NULL AND d.due_at IS NOT NULL AND b.state='active'").fetchone()
    except Exception:  # noqa: BLE001 - an older database has no deferrals yet
        return None
    return row[0] if row else None

