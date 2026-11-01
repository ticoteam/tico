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

A row is settled (`read_index`) when the walk finished with no meeting provenance and no error. A reader then reads
a settled row exactly when no `t:` node is one of their denied tasks: the walk's private sources are its `t:` nodes
that are private, and denied tasks are private. Everything else, and every row not settled yet, keeps the walk.

Triggers keep it current from every writer: a change to a node's source rows (the same columns the provenance clock
watches, backend/provenance_clock.py) unsettles every row that touched it and queues it, and a new message or run is
queued. A task turning private changes no node: denial is worked out per read. `sweep` settles queued rows in short
write transactions beside the server, and the rows no pass has seen yet (history an older server wrote). SQLite
refuses to drop a column a trigger names, so a migration dropping one of these columns rewrites the triggers here
first; a rename is carried.
"""
import json
import sqlite3
import threading
import time

from . import hubdb as H
from .store import Problem

BATCH = 100
SPAN = 20000
SLICE = 20          # rows walked between looks at the clock
HOLD_MS = 200       # a sweep write gives the lock back after about this long, and queues again behind any waiter
RUN_EVENTS = "('task.next-run.carried','attempt.input.moved')"


def _touch(node):
    """Unsettle and queue every row that touched `node` (an SQL expression)."""
    return (f"INSERT OR IGNORE INTO read_dirty SELECT key FROM read_deps WHERE dep={node}; "
            f"DELETE FROM read_index WHERE key IN (SELECT key FROM read_deps WHERE dep={node}); ")


def _touch_all(nodes):
    """Unsettle and queue every row that touched one of `nodes` (an SQL SELECT of node strings)."""
    return (f"INSERT OR IGNORE INTO read_dirty SELECT key FROM read_deps WHERE dep IN ({nodes}); "
            f"DELETE FROM read_index WHERE key IN (SELECT key FROM read_deps WHERE dep IN ({nodes})); ")


TURN = ("id", "meeting_id", "bot")


def _turn_rooms(side):
    """The rooms of the keeper inputs naming this turn, as nodes; the partial index holds only such inputs."""
    turn = "json_extract(m.refs,'$.live_meeting.turn_id')"
    return f"SELECT 'c:'||m.conversation_id FROM messages m WHERE {turn} IS NOT NULL AND {turn}={side}.id"


def _changed(columns):
    return " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in columns)


MESSAGE = ("id", "conversation_id", "refs", "in_reply_to", "from_actor", "to_actor")
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS read_index(key TEXT PRIMARY KEY) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS read_deps(dep TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY(dep, key)) WITHOUT ROWID",
    "CREATE INDEX IF NOT EXISTS read_deps_key ON read_deps(key)",
    "CREATE TABLE IF NOT EXISTS read_dirty(key TEXT PRIMARY KEY) WITHOUT ROWID",
    # A keeper input naming a live meeting turns its room into a meeting room for every message that reads it.
    "CREATE TRIGGER IF NOT EXISTS read_deps_message_insert AFTER INSERT ON messages BEGIN "
    "INSERT OR IGNORE INTO read_dirty VALUES('m:'||NEW.id); " + _touch("'m:'||NEW.id") + _touch("'t:'||NEW.id") +
    "INSERT OR IGNORE INTO read_dirty SELECT key FROM read_deps WHERE dep='c:'||NEW.conversation_id "
    "AND instr(coalesce(NEW.refs,''),'live_meeting')>0; "
    "DELETE FROM read_index WHERE instr(coalesce(NEW.refs,''),'live_meeting')>0 AND key IN "
    "(SELECT key FROM read_deps WHERE dep='c:'||NEW.conversation_id); END",
    f"CREATE TRIGGER IF NOT EXISTS read_deps_message_update AFTER UPDATE OF {','.join(MESSAGE)} ON messages "
    f"WHEN {_changed(MESSAGE)} BEGIN INSERT OR IGNORE INTO read_dirty VALUES('m:'||NEW.id); "
    + _touch("'m:'||OLD.id") + _touch("'m:'||NEW.id") + _touch("'t:'||OLD.id") + _touch("'t:'||NEW.id")
    + _touch("'c:'||OLD.conversation_id")
    + _touch("'c:'||NEW.conversation_id") + "END",
    "CREATE TRIGGER IF NOT EXISTS read_deps_message_delete AFTER DELETE ON messages BEGIN "
    + _touch("'m:'||OLD.id") + _touch("'t:'||OLD.id") + "END",
    "CREATE TRIGGER IF NOT EXISTS read_deps_conversation_insert AFTER INSERT ON conversations BEGIN "
    + _touch("'c:'||NEW.id") + "END",
    "CREATE TRIGGER IF NOT EXISTS read_deps_conversation_update AFTER UPDATE OF id, task_id, room_key ON conversations "
    f"WHEN {_changed(('id', 'task_id', 'room_key'))} BEGIN " + _touch("'c:'||OLD.id") + _touch("'c:'||NEW.id") + "END",
    "CREATE TRIGGER IF NOT EXISTS read_deps_conversation_delete AFTER DELETE ON conversations BEGIN "
    + _touch("'c:'||OLD.id") + "END",
    "CREATE TRIGGER IF NOT EXISTS read_deps_task_insert AFTER INSERT ON tasks BEGIN "
    + _touch("'t:'||NEW.id") + _touch("'a:'||NEW.carried_by") + "END",
    "CREATE TRIGGER IF NOT EXISTS read_deps_task_update AFTER UPDATE OF id, carried_by ON tasks "
    f"WHEN {_changed(('id', 'carried_by'))} BEGIN " + _touch("'t:'||OLD.id") + _touch("'t:'||NEW.id")
    + _touch("'a:'||OLD.carried_by") + _touch("'a:'||NEW.carried_by") + "END",
    "CREATE TRIGGER IF NOT EXISTS read_deps_task_delete AFTER DELETE ON tasks BEGIN "
    + _touch("'t:'||OLD.id") + _touch("'a:'||OLD.carried_by") + "END",
    "CREATE TRIGGER IF NOT EXISTS read_deps_attempt_insert AFTER INSERT ON attempts BEGIN "
    "INSERT OR IGNORE INTO read_dirty VALUES('a:'||NEW.id); " + _touch("'a:'||NEW.id") + "END",
    "CREATE TRIGGER IF NOT EXISTS read_deps_attempt_update AFTER UPDATE OF id, bot_id, job_id, state ON attempts "
    f"WHEN {_changed(('id', 'bot', 'job_id', 'state'))} BEGIN " + _touch("'a:'||OLD.id") + _touch("'a:'||NEW.id") + "END",
    "CREATE TRIGGER IF NOT EXISTS read_deps_attempt_delete AFTER DELETE ON attempts BEGIN " + _touch("'a:'||OLD.id") + "END",
    "CREATE TRIGGER IF NOT EXISTS read_deps_input_insert AFTER INSERT ON attempt_inputs BEGIN "
    + _touch("'a:'||NEW.attempt_id") + "END",
    "CREATE TRIGGER IF NOT EXISTS read_deps_input_update AFTER UPDATE ON attempt_inputs BEGIN "
    + _touch("'a:'||OLD.attempt_id") + _touch("'a:'||NEW.attempt_id") + "END",
    "CREATE TRIGGER IF NOT EXISTS read_deps_input_delete AFTER DELETE ON attempt_inputs BEGIN "
    + _touch("'a:'||OLD.attempt_id") + "END",
    *(f"CREATE TRIGGER IF NOT EXISTS read_deps_job_{op.lower()} AFTER {op}{' OF id, message_id' if op == 'UPDATE' else ''} "
      f"ON jobs BEGIN " + "".join(
          f"INSERT OR IGNORE INTO read_dirty SELECT d.key FROM read_deps d JOIN attempts a ON d.dep='a:'||a.id "
          f"WHERE a.job_id={side}.id; DELETE FROM read_index WHERE key IN (SELECT d.key FROM read_deps d "
          f"JOIN attempts a ON d.dep='a:'||a.id WHERE a.job_id={side}.id); "
          for side in ({"INSERT": ("NEW",), "UPDATE": ("OLD", "NEW"), "DELETE": ("OLD",)}[op])) + "END"
      for op in ("INSERT", "UPDATE", "DELETE")),
    *(f"CREATE TRIGGER IF NOT EXISTS read_deps_event_{op.lower()} AFTER {op} ON events WHEN "
      + " OR ".join(f"{side}.action IN {RUN_EVENTS}" for side in sides) + " BEGIN "
      + "".join(_touch(f"'a:'||{side}.target") for side in sides) + "END"
      for op, sides in (("INSERT", ("NEW",)), ("UPDATE", ("OLD", "NEW")), ("DELETE", ("OLD",)))),
    # A turn makes a room a meeting room through the keeper inputs that name it (live_privacy `_conversation_uncached`):
    # only those rooms' readers change, and a claim, reply or skip changes none of the columns that check reads.
    *(f"CREATE TRIGGER IF NOT EXISTS read_deps_meeting_turn_{op.lower()} AFTER {op}"
      f"{' OF ' + ','.join(TURN) if op == 'UPDATE' else ''} ON live_meeting_turns "
      f"{'WHEN ' + _changed(TURN) + ' ' if op == 'UPDATE' else ''}BEGIN "
      + "".join(_touch_all(_turn_rooms(side)) for side in sides) + "END"
      for op, sides in (("INSERT", ("NEW",)), ("UPDATE", ("OLD", "NEW")), ("DELETE", ("OLD",)))),
)


def ensure(conn):
    """Tables and triggers; idempotent."""
    for statement in SCHEMA:
        conn.execute(statement)


class _Recorded(dict):
    """`conversation_tasks` that records the rooms the walk reads."""
    def __init__(self, rows, nodes):
        super().__init__(rows)
        self.nodes = nodes

    def get(self, cid, default=None):
        self.nodes.add("c:" + str(cid))
        return super().get(cid, default)


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
        self.index = ReadIndex(c, Identity("keeper", "owner"), rooms=True)
        self.c, self.nodes = c, set()
        index = self.index
        index.private = _Everything(self.nodes)
        index.denied = set()
        tasks = set(index.task_ids)
        index.known_task = lambda tid: self.nodes.add("t:" + str(tid)) or tid in tasks
        index.conversation_tasks = _Recorded(dict(c.execute("SELECT id,task_id FROM conversations")), self.nodes)
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
        return H.message(self.c, mid, include_deleted=True)

    def _attempt(self, aid):
        known = self.runs.get(aid)
        if known is None:
            outer = set(self.nodes)
            self.nodes.clear()          # the same set the index's recorders hold, so cleared, not replaced
            try:
                self.nodes.add("a:" + str(aid))
                sources = self.index._attempt_sources(aid)
                # The meeting check also walks each input moved off this run (live_privacy `_attempt_from`), its room
                # and its ancestors, where the task walk only reads the moved event: walk them here so a change there
                # unsettles the run.
                for detail, in self.c.execute("SELECT detail FROM events WHERE action='attempt.input.moved' "
                                              "AND target=?", (aid,)):
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
        msg = {"id": row["id"], "conversation_id": row["conversation_id"], "refs": row["refs"],
               "in_reply_to": row["in_reply_to"]}
        try:
            self.index.sources(msg)
            meeting = bool(self.index.meetings.message(msg))
        except (ValueError, Problem):
            meeting = True
        return not meeting, set(self.nodes)

    def attempt(self, aid):
        self.nodes.clear()
        try:
            self._attempt(aid)
            meeting = bool(self.index.meetings.attempt(aid))
        except (ValueError, Problem):
            meeting = True
        return not meeting, set(self.nodes)


def settle(conn, keys, walk=None):
    """Walk these rows now and record what they touched; the caller holds the write transaction. `walk`: one Walk
    for the whole transaction, so its runs are walked once."""
    keys = list(dict.fromkeys(keys))
    if not keys:
        return
    walk = walk or Walk(conn)
    for start in range(0, len(keys), 500):
        chunk = keys[start:start + 500]
        marks = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM read_deps WHERE key IN ({marks})", chunk)
        conn.execute(f"DELETE FROM read_index WHERE key IN ({marks})", chunk)
        conn.execute(f"DELETE FROM read_dirty WHERE key IN ({marks})", chunk)
    deps, settled = [], []
    messages = [k[2:] for k in keys if k.startswith("m:")]
    for start in range(0, len(messages), 500):
        chunk = messages[start:start + 500]
        for row in conn.execute("SELECT id,conversation_id,refs,in_reply_to FROM messages WHERE id IN (%s)"
                                % ",".join("?" * len(chunk)), chunk):
            ok, nodes = walk.message(row)
            deps.extend((node, "m:" + row["id"]) for node in nodes)
            if ok:
                settled.append(("m:" + row["id"],))
    attempts = [k[2:] for k in keys if k.startswith("a:")]
    for start in range(0, len(attempts), 500):
        chunk = attempts[start:start + 500]
        for aid, in conn.execute("SELECT id FROM attempts WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk):
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
    """Settle the first of `keys` in one write, SLICE rows at a time, until about HOLD_MS has passed; returns how many.

    The walks run under the lock (a walk outside it could miss a change landing before its write), so a write is cut
    short by time instead: a claim or heartbeat waiting at the gate goes before the next one (TK-INSRN53V)."""
    settled = [0]

    def work():
        start, walk = time.monotonic(), Walk(conn)
        while settled[0] < len(keys):
            piece = keys[settled[0]:settled[0] + SLICE]
            settle(conn, piece, walk)
            settled[0] += len(piece)
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
            keys = [prefix + r[0] for r in conn.execute(
                f"SELECT id FROM {table} t WHERE rowid>? AND rowid<=? AND NOT EXISTS "
                f"(SELECT 1 FROM read_deps d WHERE d.key='{prefix}'||t.id) ORDER BY rowid", (after, after + span))]
            after += span
            while keys:
                if stopped():
                    return done
                settled = _settle_some(conn, keys[:batch], gate)
                keys, done = keys[settled:], done + settled
            if searched is not None:
                searched[table] = min(after, top)
    return done


def loop(store, stop, seconds=10):
    """`sweep` on its own connection beside the server until it stops; a failed pass is retried on the next."""
    import logging
    searched, file = {}, None
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
