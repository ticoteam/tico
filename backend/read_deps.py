"""What each message's and run's readability depends on, kept per row so a SQL read of history is not a walk per row.

`ReadIndex.message` and `ReadIndex.attempt` (backend/privacy_index.py) walk a row's provenance on every read: its
refs, its room's task, the messages it replies to, answers or took in, and its run. Nearly every row's answer comes
down to one question: does that walk reach a task the reader may not read? So the walk runs once per row, here, with
every task treated as private, and records every node it touched:

  t:<string>  a string the walk asked about as a task (a task id, a `task:` reference, a task URL), whether or not a
              task has that id yet; a message id named this way (a moved input) is touched as one too
  m:<id>      a message it read (the row itself, a parent, a run's message or input), present or missing
  a:<id>      a run it read
  c:<id>      a room it read

A row is settled (`read_index`) when the walk finished with no meeting provenance, no error, and no message it read
naming a file (`names_file`: a reply inherits the file refusals above it, and a run those of its messages, which are
per reader). A reader then reads a settled row exactly when no `t:` node is one of their denied tasks: the walk's
private sources are its `t:` nodes that are private, and denied tasks are private. Everything else, and every row not
settled yet, keeps the walk.

Triggers keep it current from every writer: a change to a node's source rows (the same columns the provenance clock
watches, backend/provenance_clock.py) unsettles every row that touched it and queues it, and a new message or run is
queued. A task turning private changes no node: denial is worked out per read. `sweep` settles queued rows in short
write transactions beside the server, and the rows no pass has seen yet (history an older server wrote). SQLite
refuses to drop a column a trigger names, so a migration dropping one of these columns rewrites the triggers here
first; a rename is carried.
"""

import time

from . import hubdb as H
from .store import Problem

# A walk that read a message naming a file records this node; such a row is not settled, and the node is not kept.
FILE = "file:"
BATCH = 100
SPAN = 20000
HOLD_MS = 200  # a sweep write gives the lock back once a row ends past this long, and queues again behind any waiter


def names_file(refs):
    """Whether stored `refs` may name a file (task_privacy.PAGE_MESSAGE_KEYS), read as the SQL page gate reads them: any
    key's quoted name, or an escape, counts. Wider than the file check itself, so a row it misses names no file."""
    from .task_privacy import PAGE_MESSAGE_KEYS

    text = refs if isinstance(refs, str) or refs is None else H._dump(refs)
    if text in (None, "", "{}"):
        return False
    return "\\" in text or any(f'"{key}"' in text for key in PAGE_MESSAGE_KEYS)


class _Rooms:
    """`conversation_tasks` for the walk: a room's task by its key, read when the walk first asks about the room and
    kept for the write. Records the rooms the walk reads."""

    def __init__(self, c, nodes):
        self.c, self.nodes, self.found = c, nodes, {}

    def get(self, cid, default=None):
        self.nodes.add("c:" + str(cid))
        if not isinstance(cid, (str, bytes)):
            return default  # a stored key is text or a blob: equal by key exactly when equal in Python
        if cid not in self.found:
            row = self.c.execute("SELECT task_id FROM conversations WHERE id=?", (cid,)).fetchone()
            self.found[cid] = (row[0],) if row else None
        hit = self.found[cid]
        return hit[0] if hit else default


class _Tasks:
    """`known_task` for the walk: whether a task has this id, read by its key when first asked and kept for the write.
    Records every string asked about as a task."""

    def __init__(self, c, nodes):
        self.c, self.nodes, self.found = c, nodes, {}

    def __call__(self, tid):
        self.nodes.add("t:" + str(tid))
        if not isinstance(tid, (str, bytes)):
            return False  # a stored key is text or a blob: equal by key exactly when equal in Python
        if tid not in self.found:
            self.found[tid] = self.c.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone() is not None
        return self.found[tid]


class _Everything:
    """`private` for the walk: every string it asks about is a candidate task, recorded."""

    def __init__(self, nodes):
        self.nodes = nodes

    def __contains__(self, value):
        self.nodes.add("t:" + str(value))
        return True


class Walk:
    """ReadIndex's own provenance walk, uncached so each row's nodes are complete, with every task private."""

    def __init__(self, c):
        from .auth import Identity
        from .privacy_index import ReadIndex

        # Built under the sweep's write lock, once per write, so it reads nothing up front: no per-task readability
        # (every task is private here, and nothing is denied), no meeting-room scan (the walk asks `meetings` room by
        # room), and no table of every task id and room (`_Tasks`, `_Rooms` read the ones the walk reaches by key). A
        # Walk that worked out every private task's readers and read every room held the lock 0.25 s on TIDY before
        # its first row, seconds on a busy host. The shared task-and-room map that replaced it is never shared inside a
        # write transaction (backend/row_clock.py), so each write reread all 37,000 rows: TIDY logged 500 sweep writes
        # holding the lock 5-40 s in two days, most of them caught in that read.
        self.index = ReadIndex(c, Identity("keeper", "owner"), denials=False)
        self.c, self.nodes = c, set()
        index = self.index
        index.private = _Everything(self.nodes)
        index.denied = set()
        index.known_task = _Tasks(c, self.nodes)
        index.conversation_tasks = _Rooms(c, self.nodes)
        index.target_message = self._message
        index.string_sources = index._string_sources
        index.message_sources = index._message_sources
        index.attempt_sources = self._attempt
        # aid -> (sources, nodes). Every message of a turn names its run, and a run's state change re-queues them all:
        # each walked the run again, two events reads a message (1.4M a day on Tico Team). A Walk lives for one write
        # transaction (`_settle_some`), where no source row changes, so a run is walked once and its nodes replayed.
        self.runs = {}

    def _message(self, mid):
        self.nodes.add("m:" + str(mid))
        found = H.message(self.c, mid, include_deleted=True)
        if found and names_file(found.get("refs")):
            self.nodes.add(FILE)
        return found

    def _attempt(self, aid):
        known = self.runs.get(aid)
        if known is None:
            outer = set(self.nodes)
            self.nodes.clear()  # the same set the index's recorders hold, so cleared, not replaced
            try:
                self.nodes.add("a:" + str(aid))
                sources = self.index._attempt_sources(aid)
                # The meeting check also walks each input moved off this run (live_privacy `_attempt_from`), its room
                # and its ancestors, where the task walk only reads the moved event: walk them here so a change there
                # unsettles the run.
                for (detail,) in self.c.execute(
                    "SELECT detail FROM events WHERE action='attempt.input.moved' AND target=?", (aid,)
                ):
                    detail = H._json(detail, {}) or {}
                    if isinstance(detail, dict) and isinstance(detail.get("message_id"), str):
                        self.index.sources(self._message(detail["message_id"]), include_run=False)
                known = self.runs[aid] = (sources, frozenset(self.nodes))
            finally:
                self.nodes.update(outer)
        self.nodes.update(known[1])
        return known[0]

    def message(self, row):
        """(settled, nodes) for one messages row."""
        self.nodes.clear()
        self.nodes.add("m:" + row["id"])
        self.nodes.add("c:" + str(row["conversation_id"]))
        msg = {
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "refs": row["refs"],
            "in_reply_to": row["in_reply_to"],
        }
        try:
            self.index.sources(msg)
            meeting = bool(self.index.meetings.message(msg))
        except (ValueError, Problem):
            meeting = True
        return self._result(meeting)

    def _result(self, meeting):
        """(settled, nodes): not settled with meeting provenance or a message read that names a file."""
        nodes = set(self.nodes)
        file = FILE in nodes
        nodes.discard(FILE)
        return not meeting and not file, nodes

    def attempt(self, aid):
        self.nodes.clear()
        try:
            self._attempt(aid)
            meeting = bool(self.index.meetings.attempt(aid))
        except (ValueError, Problem):
            meeting = True
        return self._result(meeting)


def settle(conn, keys, walk=None):
    """Walk these rows now and record what they touched; the caller holds the write transaction. `walk`: one Walk
    for the whole transaction, so its runs are walked once."""
    keys = list(dict.fromkeys(keys))
    if not keys:
        return
    walk = walk or Walk(conn)
    for start in range(0, len(keys), 500):
        chunk = keys[start : start + 500]
        marks = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM read_deps WHERE key IN ({marks})", chunk)
        conn.execute(f"DELETE FROM read_index WHERE key IN ({marks})", chunk)
        conn.execute(f"DELETE FROM read_dirty WHERE key IN ({marks})", chunk)
    deps, settled = [], []
    messages = [k[2:] for k in keys if k.startswith("m:")]
    for start in range(0, len(messages), 500):
        chunk = messages[start : start + 500]
        for row in conn.execute(
            "SELECT id,conversation_id,refs,in_reply_to FROM messages WHERE id IN (%s)" % ",".join("?" * len(chunk)),
            chunk,
        ):
            ok, nodes = walk.message(row)
            deps.extend((node, "m:" + row["id"]) for node in nodes)
            if ok:
                settled.append(("m:" + row["id"],))
    attempts = [k[2:] for k in keys if k.startswith("a:")]
    for start in range(0, len(attempts), 500):
        chunk = attempts[start : start + 500]
        for (aid,) in conn.execute("SELECT id FROM attempts WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk):
            ok, nodes = walk.attempt(aid)
            deps.extend((node, "a:" + aid) for node in nodes)
            if ok:
                settled.append(("a:" + aid,))
    conn.executemany("INSERT OR IGNORE INTO read_deps VALUES(?,?)", deps)
    conn.executemany("INSERT OR IGNORE INTO read_index VALUES(?)", settled)


def _write(conn, work, gate=None):
    from .flight import begin

    done = begin(conn, gate=gate)
    try:
        work()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        done()


def _settle_some(conn, keys, gate=None):
    """Settle the first of `keys` in one write, row by row, until HOLD_MS has passed (the row that passes it is the
    last); returns how many.

    The walks run under the lock (a walk outside it could miss a change landing before its write), so a write is cut
    short by time instead: a claim or heartbeat waiting at the gate goes before the next one (TK-INSRN53V). The clock is
    read after every row, so a starved write overruns the budget by one row at most."""
    settled = [0]

    def work():
        start, walk = time.monotonic(), Walk(conn)
        for key in keys:
            settle(conn, [key], walk)
            settled[0] += 1
            if (time.monotonic() - start) * 1000 >= HOLD_MS:
                break

    _write(conn, work, gate)
    return settled[0]


def sweep(conn, batch=BATCH, span=SPAN, stop=None, gate=None, searched=None):
    """Settle the queued rows, then every message and run no pass has walked yet (a row any pass walked has a node of
    its own in `read_deps`). Each `batch` is walked and written in short write transactions (`_settle_some`), each
    admitted through `gate` (the server's writer queue) when one is given; the search reads `span` rowids at a time
    outside the write lock. Returns how many rows it walked.

    `searched` ({table: rowid}), kept by the caller between passes, is how far a pass has walked every row. A walked
    row keeps its node, and a row inserted later is queued by its trigger, so the next pass searches only past it
    rather than every row again (about 90 ms per 20,000 rows, each pass)."""

    def stopped():
        return stop is not None and stop.is_set()

    done = 0
    while not stopped():
        keys = [r[0] for r in conn.execute("SELECT key FROM read_dirty LIMIT ?", (batch,))]
        if not keys:
            break
        done += _settle_some(conn, keys, gate)
    for table, prefix in (("messages", "m:"), ("attempts", "a:")):
        after = (searched or {}).get(table, 0)
        top = conn.execute(f"SELECT coalesce(max(rowid), 0) FROM {table}").fetchone()[0]
        while after < top and not stopped():
            keys = [
                prefix + r[0]
                for r in conn.execute(
                    f"SELECT id FROM {table} t WHERE rowid>? AND rowid<=? AND NOT EXISTS "
                    f"(SELECT 1 FROM read_deps d WHERE d.key='{prefix}'||t.id) ORDER BY rowid",
                    (after, after + span),
                )
            ]
            after += span
            while keys:
                if stopped():
                    return done
                settled = _settle_some(conn, keys[:batch], gate)
                keys, done = keys[settled:], done + settled
            if searched is not None:
                searched[table] = min(after, top)
    return done


def loop(store, stop, seconds=10, first_wait=60):
    """`sweep` on its own connection beside the server until it stops; a failed pass is retried on the next. The first
    pass waits `first_wait` seconds: a server that has just started has every runner replaying its queued writes at
    once, and a pass started then competes with that burst for the CPU and the writer lock."""
    import logging

    searched, file = {}, None
    stop.wait(first_wait)
    while not stop.is_set():
        try:
            with store.read() as conn:
                if getattr(conn, "pool_file", None) != file:
                    # A restored or replaced database file is searched from its first row again.
                    searched, file = {}, getattr(conn, "pool_file", None)
                sweep(conn, stop=stop, gate=store.writer, searched=searched)
        except Exception as exc:
            logging.getLogger("tico.read_deps").error("Settling history reads failed: %s", type(exc).__name__)
        stop.wait(seconds)
