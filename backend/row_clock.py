"""Share neutral reads between requests until a change counter moves: the private tasks' parties rows
(privacy_index.ReadIndex, once per privacy-checked request), the owner/access/rules registry rows (auth.sync_access,
once per request), and the task ids and room-to-task map (ReadIndex, once per SQL request and per task list).

Both are stored rows, never a permission result: each request still decides what its caller may read from them. The
counters move only for the columns these reads name, so a task's status or an unrelated metadata row bumps nothing.
SQLite triggers count writes from every connection. A connection that has written in its current checkout, or is in
a write transaction, reads its own rows and shares none: its uncommitted changes can roll back to a count another
connection later reuses (roster_clock, provenance_clock.cached).
"""

import sqlite3
from threading import Event, Lock
from types import MappingProxyType

# tasks_private_status holds these columns (and two more) under this WHERE, word for word: the read never touches a body.
PARTIES = (
    "SELECT id,requester_id,owner_id,readers,private FROM tasks INDEXED BY tasks_private_status WHERE private IS NOT 0"
)
ACCESS = "SELECT key,value FROM registry_metadata WHERE key IN ('owner','access','rules')"
_IN_ACCESS = "IN ('owner','access','rules')"
# Every task id, and every room's task. Only the existence of a task and a room's task reference are shared: a status
# change or a renamed room moves nothing.
TASK_IDS = "SELECT id FROM tasks"
ROOM_TASKS = "SELECT id,task_id FROM conversations INDEXED BY conversations_id_task"


class Cache:
    """{name: (clock, rows)} for one Store, replaced when its database file is replaced. `building` holds the read in
    progress per (name, clock): readers that miss at the same clock wait for it instead of each reading the same rows
    (tens of thousands of task ids at TIDY's size) at once."""

    def __init__(self):
        self.values, self.lock, self.building = {}, Lock(), {}


class _Flight:
    """One build of a name at a clock, which the readers that missed at that clock wait on."""

    def __init__(self):
        self.done, self.found, self.ok = Event(), None, False


def ensure(c):
    c.execute("CREATE TABLE IF NOT EXISTS row_clock(name TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    c.execute("INSERT OR IGNORE INTO row_clock VALUES('parties',0),('access',0),('refs',0)")
    bump = "UPDATE row_clock SET value=value+1 WHERE name='%s'"
    triggers = (
        ("row_clock_parties_insert", "AFTER INSERT ON tasks WHEN NEW.private IS NOT 0", bump % "parties"),
        ("row_clock_parties_delete", "AFTER DELETE ON tasks WHEN OLD.private IS NOT 0", bump % "parties"),
        (
            "row_clock_parties_update",
            "AFTER UPDATE OF requester_id,owner_id,readers,private ON tasks WHEN "
            "(OLD.requester_id IS NOT NEW.requester_id OR OLD.owner_id IS NOT NEW.owner_id OR OLD.readers IS NOT NEW.readers "
            "OR OLD.private IS NOT NEW.private) AND (OLD.private IS NOT 0 OR NEW.private IS NOT 0)",
            bump % "parties",
        ),
        ("row_clock_access_insert", f"AFTER INSERT ON registry_metadata WHEN NEW.key {_IN_ACCESS}", bump % "access"),
        ("row_clock_access_delete", f"AFTER DELETE ON registry_metadata WHEN OLD.key {_IN_ACCESS}", bump % "access"),
        (
            "row_clock_access_update",
            f"AFTER UPDATE OF key,value ON registry_metadata WHEN OLD.key {_IN_ACCESS} OR NEW.key {_IN_ACCESS}",
            bump % "access",
        ),
        ("row_clock_refs_task_insert", "AFTER INSERT ON tasks", bump % "refs"),
        ("row_clock_refs_task_delete", "AFTER DELETE ON tasks", bump % "refs"),
        ("row_clock_refs_room_insert", "AFTER INSERT ON conversations", bump % "refs"),
        ("row_clock_refs_room_delete", "AFTER DELETE ON conversations", bump % "refs"),
        (
            "row_clock_refs_room_update",
            "AFTER UPDATE OF id,task_id ON conversations WHEN OLD.id IS NOT NEW.id OR OLD.task_id IS NOT NEW.task_id",
            bump % "refs",
        ),
    )
    for name, when, action in triggers:
        c.execute(f"CREATE TRIGGER IF NOT EXISTS {name} {when} BEGIN {action}; END")


def _clock(c, name):
    return c.execute("SELECT value FROM row_clock WHERE name=?", (name,)).fetchone()[0]


def _shared(c, name, statement, build):
    cache = getattr(c, "row_cache", None)
    if (
        cache is None
        or getattr(c, "write_transaction", False)
        or c.total_changes != getattr(c, "provenance_changes", None)
    ):
        return build(c, statement)
    try:
        clock = _clock(c, name)
    except (sqlite3.OperationalError, TypeError):
        # Not made yet (a file opened before Store.initialize): read without sharing.
        return build(c, statement)
    key = (name, clock)
    while True:
        with cache.lock:
            hit = cache.values.get(name)
            if hit is not None and hit[0] == clock:
                return hit[1]
            flight = cache.building.get(key)
            if flight is None:
                flight = cache.building[key] = _Flight()
                break
        flight.done.wait()
        if flight.ok:
            # The same clock is the same committed rows, so the leader's read is this reader's too.
            return flight.found
        # The leader's read failed or was not this clock's: one of its waiters reads next, the others wait for it.
    try:
        flight.found = build(c, statement)
        # Outside a snapshot the clock and the rows are two reads: the rows are this clock's only if no change
        # committed between them, as the counters only go up. Otherwise this reader keeps its rows and shares none.
        flight.ok = c.in_transaction or _clock(c, name) == clock
    finally:
        with cache.lock:
            del cache.building[key]
            # The counters only go up: a slow read at an older clock never replaces a newer one's entry.
            current = cache.values.get(name)
            if flight.ok and (current is None or current[0] < clock):
                cache.values[name] = (clock, flight.found)
        flight.done.set()
    return flight.found


def _rows(c, statement):
    return tuple(dict(r) for r in c.execute(statement))


def parties(c):
    """Every private task's (id, requester, owner, readers, private), as dicts the caller must not mutate."""
    return _shared(c, "parties", PARTIES, _rows)


def access_rows(c):
    """The owner, access and rules registry rows, sorted, as (key, value_json) tuples."""
    return _shared(
        c, "access", ACCESS, lambda conn, statement: tuple(sorted(tuple(r) for r in conn.execute(statement)))
    )


def _refs_rows(conn, _statement):
    return (frozenset(row[0] for row in conn.execute(TASK_IDS)), MappingProxyType(dict(conn.execute(ROOM_TASKS))))


def refs(c):
    """(task ids, {room: task}), as read-only values the caller must not change."""
    return _shared(c, "refs", TASK_IDS + " | " + ROOM_TASKS, _refs_rows)
