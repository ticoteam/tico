"""Which messages could be about a task, kept per message so a task's page does not read its whole room.

A message's tasks (backend/task_privacy.py `message_tasks`) come from three places: what its own refs
name, its parents' tasks (`in_reply_to`, `refs.answers`, `refs.inputs`), and the run it reports on
(`refs.turn_id`, `refs.run.attempt_id`). The first two are fixed by the row; a run's tasks grow after
the message is written (a carried task, a moved input). So a message keeps its static links here:

  task     a string in its refs that could be a task id
  parent   a message whose tasks it inherits
  attempt  the run whose tasks it inherits

and `candidates` works out the rest at read time (`named`, for a task's comments, needs only the first).
The answer is a superset: the page still checks
every candidate with `message_tasks` and `readable`, so a stale or extra link can only cost a check,
and a missing one would hide a message. To keep links from going missing:

- triggers mark every inserted or re-pointed message dirty in the same statement, whoever writes it;
- `Store.transaction` refreshes the dirty rows before it commits, and `candidates` reads a dirty row's
  links from the row itself, so a write outside `Store.transaction` is still seen;
- `message_links_seen` records every message whose links were worked out, and every start backfills the
  messages missing from it, so rows an older server wrote (a rollback then an upgrade) get their links, even
  where SQLite reused the rowid of a deleted one; until the first full pass has finished a page reads its room.
"""
import json
import re
import sqlite3

from . import hubdb as H

READY = "message_links.ready"
BATCH = 2000
SPAN = 20000    # rowids the backfill reads per look, outside the write lock
DEPTH = 100
SHAPE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS message_links(message_id TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL, "
    "PRIMARY KEY(kind, target, message_id)) WITHOUT ROWID",
    "CREATE INDEX IF NOT EXISTS message_links_message ON message_links(message_id)",
    "CREATE TABLE IF NOT EXISTS message_links_dirty(message_id TEXT PRIMARY KEY) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS message_links_seen(message_id TEXT PRIMARY KEY) WITHOUT ROWID",
    "CREATE TRIGGER IF NOT EXISTS message_links_insert AFTER INSERT ON messages BEGIN "
    "INSERT OR IGNORE INTO message_links_dirty VALUES(NEW.id); END",
    "CREATE TRIGGER IF NOT EXISTS message_links_update AFTER UPDATE OF id, refs, in_reply_to ON messages BEGIN "
    "INSERT OR IGNORE INTO message_links_dirty VALUES(NEW.id); END",
    "CREATE TRIGGER IF NOT EXISTS message_links_delete AFTER DELETE ON messages BEGIN "
    "DELETE FROM message_links WHERE message_id=OLD.id; DELETE FROM message_links_dirty WHERE message_id=OLD.id; "
    "DELETE FROM message_links_seen WHERE message_id=OLD.id; END",
    "CREATE INDEX IF NOT EXISTS conversations_task ON conversations(task_id) WHERE task_id IS NOT NULL",
    # The few run events that name tasks (task_privacy.RUN_TASK_EVENTS_SQL), read on every task page: here, with
    # the links, so it never depends on the start-up index on all events. Covering their detail: a run's provenance
    # reads them per run (1.4M a day on Tico Team), and `candidates` searches every one's detail (2.1 s on TIDY when
    # each was a table row). It replaces events_run_task (action, target), dropped here on every start.
    "CREATE INDEX IF NOT EXISTS events_run_detail ON events(action, target, detail) "
    "WHERE action IN ('task.next-run.carried','attempt.input.moved')",
    "DROP INDEX IF EXISTS events_run_task",
)


def ensure(conn):
    """Tables and triggers; idempotent. A file with no messages yet has nothing to backfill, so it is ready."""
    fresh = not conn.execute("SELECT 1 FROM sqlite_master WHERE name='message_links'").fetchone()
    for statement in SCHEMA:
        conn.execute(statement)
    if fresh and not conn.execute("SELECT 1 FROM messages LIMIT 1").fetchone():
        _set(conn, READY, True)


def _get(conn, key):
    try:
        row = conn.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return json.loads(row[0]) if row else None


def _set(conn, key, value):
    conn.execute("CREATE TABLE IF NOT EXISTS registry_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value)))


def links_for(row):
    """The (kind, target) links of one messages row, from the row alone. Only id-shaped strings are kept as
    task links; a task whose id is not id-shaped is never looked up here (`candidates`). A deleted message
    keeps the links of its current refs: its replies still inherit its tasks."""
    from .task_privacy import _candidates
    refs = H._json(row["refs"], {}) or {}
    links = set()
    if row["in_reply_to"]:
        links.add(("parent", row["in_reply_to"]))
    if not isinstance(refs, dict):
        return links
    names = _candidates(refs, set())
    for key in ("task", "task_id"):
        names.add(H._task_ref(refs.get(key)) or "")
    links.update(("task", name) for name in names if SHAPE.fullmatch(name))
    for key in ("answers", "inputs"):
        if isinstance(refs.get(key), list):
            links.update(("parent", mid) for mid in refs[key] if isinstance(mid, str) and mid)
    run = refs.get("run")
    run_id = refs.get("turn_id") or (run.get("attempt_id") if isinstance(run, dict) else None)
    if isinstance(run_id, str) and run_id:
        links.add(("attempt", run_id))
    return links


def refresh(conn, ids):
    """Rewrite these messages' links from their rows, mark them seen and clear them from the dirty set."""
    ids = list(ids)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM message_links WHERE message_id IN ({marks})", chunk)
        rows = conn.execute(f"SELECT id, refs, in_reply_to FROM messages WHERE id IN ({marks})", chunk).fetchall()
        conn.executemany("INSERT OR IGNORE INTO message_links VALUES(?,?,?)",
                         [(row["id"], kind, target) for row in rows for kind, target in links_for(row)])
        conn.execute(f"DELETE FROM message_links_seen WHERE message_id IN ({marks})", chunk)
        conn.executemany("INSERT INTO message_links_seen VALUES(?)", [(row["id"],) for row in rows])
        conn.execute(f"DELETE FROM message_links_dirty WHERE message_id IN ({marks})", chunk)


def refresh_dirty(conn):
    """Called inside a write transaction just before it commits (backend/store.py `transaction`)."""
    try:
        ids = [r[0] for r in conn.execute("SELECT message_id FROM message_links_dirty")]
    except sqlite3.OperationalError:
        return
    if ids:
        refresh(conn, ids)


def backfill(conn, batch=BATCH, span=SPAN):
    """Links for every message not yet seen, at every start: rows an older server wrote, whatever their rowid.
    The search reads `span` rowids at a time outside the write lock; each `batch` of unseen rows is refreshed in
    its own short write transaction. Idempotent. Returns how many rows it refreshed."""
    done, after = 0, 0
    top = conn.execute("SELECT coalesce(max(rowid), 0) FROM messages").fetchone()[0]
    while after < top:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM messages m WHERE rowid>? AND rowid<=? AND NOT EXISTS "
            "(SELECT 1 FROM message_links_seen s WHERE s.message_id=m.id) ORDER BY rowid", (after, after + span))]
        after += span
        for start in range(0, len(ids), batch):
            _write(conn, lambda: refresh(conn, ids[start:start + batch]))
        done += len(ids)
    # Rows written while it ran are dirty (the triggers) or were refreshed by their own transaction.
    _write(conn, lambda: (refresh_dirty(conn), _set(conn, READY, True)))
    return done


def _write(conn, work):
    from .flight import begin
    done = begin(conn)
    try:
        work()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        done()


def _fresh(conn):
    """Dirty messages' links read from their rows: a write that has not been refreshed yet."""
    ids = [r[0] for r in conn.execute("SELECT message_id FROM message_links_dirty")]
    out = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for row in conn.execute("SELECT id, refs, in_reply_to FROM messages WHERE id IN (%s)"
                                % ",".join("?" * len(chunk)), chunk):
            out[row["id"]] = links_for(row)
    return set(ids), out


def _linked(conn, kind, targets, dirty, fresh):
    """Messages with a `kind` link to any of `targets`; a dirty message counts by its row, not its stored links."""
    targets, found = list(targets), set()
    for start in range(0, len(targets), 500):
        chunk = targets[start:start + 500]
        found.update(r[0] for r in conn.execute(
            "SELECT message_id FROM message_links WHERE kind=? AND target IN (%s)" % ",".join("?" * len(chunk)),
            [kind, *chunk]))
    if dirty:
        found -= dirty
        wanted = set(targets)
        found.update(mid for mid, links in fresh.items() if any(k == kind and t in wanted for k, t in links))
    return found


def _in(conn, sql, values):
    values, found = list(values), set()
    for start in range(0, len(values), 500):
        chunk = values[start:start + 500]
        found.update(r[0] for r in conn.execute(sql % ",".join("?" * len(chunk)), chunk))
    return found


def _usable(conn, task_id, cid):
    """Whether the links can answer for `task_id` in `cid`: not when the index is not ready, the id is not
    id-shaped, or `cid` is the task's own room, where every message is about it."""
    return (SHAPE.fullmatch(task_id or "") and _get(conn, READY)
            and not conn.execute("SELECT 1 FROM conversations WHERE id=? AND task_id=?", (cid, task_id)).fetchone())


def named(conn, task_id, cid):
    """Ids of the messages in `cid` whose own row names `task_id` (H.message_task_id among them), or None
    when the room has to be read whole."""
    if not _usable(conn, task_id, cid):
        return None
    try:
        dirty, fresh = _fresh(conn)
    except sqlite3.OperationalError:
        return None
    return _restrict(conn, cid, _linked(conn, "task", [task_id], dirty, fresh))


def candidates(conn, task_id, cid):
    """Ids of the messages in conversation `cid` that could be about `task_id`, or None when the room has to be
    read whole: the index is not ready, or `cid` is the task's own room, where every message names it."""
    from .task_privacy import RUN_TASK_EVENTS_SQL
    if not _usable(conn, task_id, cid):
        return None
    try:
        dirty, fresh = _fresh(conn)
    except sqlite3.OperationalError:
        return None
    # Messages whose own row names the task, then every message that inherits from one of them.
    about = _linked(conn, "task", [task_id], dirty, fresh)
    about.update(r[0] for r in conn.execute(
        "SELECT m.id FROM conversations cv JOIN messages m ON m.conversation_id=cv.id WHERE cv.task_id=?", (task_id,)))
    frontier = set(about)
    for _ in range(DEPTH + 1):
        frontier = _linked(conn, "parent", frontier, dirty, fresh) - about
        if not frontier:
            break
        about |= frontier
    # Runs that took the task in: one started by or reading such a message, one it was carried to, one whose
    # moved input had it. Messages reporting on those runs inherit it.
    runs = _in(conn, "SELECT a.id FROM jobs j JOIN attempts a ON a.job_id=j.id WHERE j.message_id IN (%s)", about)
    runs |= _in(conn, "SELECT attempt_id FROM attempt_inputs WHERE message_id IN (%s)", about)
    runs.update(r[0] for r in conn.execute("SELECT carried_by FROM tasks WHERE id=? AND carried_by IS NOT NULL",
                                           (task_id,)))
    runs.update(r[0] for r in conn.execute(
        "SELECT target FROM events WHERE action IN (" + RUN_TASK_EVENTS_SQL + ") AND instr(detail, ?)>0",
        (task_id,)))
    about |= _linked(conn, "attempt", runs, dirty, fresh)
    return _restrict(conn, cid, about)


def _restrict(conn, cid, ids):
    ids, found = list(ids), set()
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        found.update(r[0] for r in conn.execute(
            "SELECT id FROM messages WHERE conversation_id=? AND id IN (%s)" % ",".join("?" * len(chunk)), [cid, *chunk]))
    return found
