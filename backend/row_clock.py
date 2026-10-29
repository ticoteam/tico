"""Share two neutral reads between requests until a change counter moves: the private tasks' parties rows
(privacy_index.ReadIndex, once per privacy-checked request) and the owner/access/rules registry rows (auth.sync_access,
once per request).

Both are stored rows, never a permission result: each request still decides what its caller may read from them. The
counters move only for the columns these reads name, so a task's status or an unrelated metadata row bumps nothing.
SQLite triggers count writes from every connection. A connection that has written in its current checkout, or is in
a write transaction, reads its own rows and shares none: its uncommitted changes can roll back to a count another
connection later reuses (roster_clock, provenance_clock.cached).
"""
import sqlite3
from threading import Lock

PARTIES = "SELECT id,requester,owner,guest,private FROM tasks INDEXED BY tasks_private_parties WHERE private IS NOT 0"
ACCESS = "SELECT key,value_json FROM registry_metadata WHERE key IN ('owner','access','rules')"
_IN_ACCESS = "IN ('owner','access','rules')"


class Cache:
    """{name: (clock, rows)} for one Store, replaced when its database file is replaced."""
    def __init__(self):
        self.values, self.lock = {}, Lock()


def ensure(c):
    c.execute("CREATE TABLE IF NOT EXISTS row_clock(name TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    c.execute("INSERT OR IGNORE INTO row_clock VALUES('parties',0),('access',0)")
    bump = "UPDATE row_clock SET value=value+1 WHERE name='%s'"
    triggers = (
        ("row_clock_parties_insert", "AFTER INSERT ON tasks WHEN NEW.private IS NOT 0", bump % "parties"),
        ("row_clock_parties_delete", "AFTER DELETE ON tasks WHEN OLD.private IS NOT 0", bump % "parties"),
        ("row_clock_parties_update", "AFTER UPDATE OF requester,owner,guest,private ON tasks WHEN "
         "(OLD.requester IS NOT NEW.requester OR OLD.owner IS NOT NEW.owner OR OLD.guest IS NOT NEW.guest "
         "OR OLD.private IS NOT NEW.private) AND (OLD.private IS NOT 0 OR NEW.private IS NOT 0)", bump % "parties"),
        ("row_clock_access_insert", f"AFTER INSERT ON registry_metadata WHEN NEW.key {_IN_ACCESS}", bump % "access"),
        ("row_clock_access_delete", f"AFTER DELETE ON registry_metadata WHEN OLD.key {_IN_ACCESS}", bump % "access"),
        ("row_clock_access_update", f"AFTER UPDATE OF key,value_json ON registry_metadata WHEN "
         f"OLD.key {_IN_ACCESS} OR NEW.key {_IN_ACCESS}", bump % "access"),
    )
    for name, when, action in triggers:
        c.execute(f"CREATE TRIGGER IF NOT EXISTS {name} {when} BEGIN {action}; END")


def _shared(c, name, statement, build):
    cache = getattr(c, "row_cache", None)
    if cache is None or getattr(c, "write_transaction", False) or \
            c.total_changes != getattr(c, "provenance_changes", None):
        return build(c, statement)
    try:
        clock = c.execute("SELECT value FROM row_clock WHERE name=?", (name,)).fetchone()[0]
    except (sqlite3.OperationalError, TypeError):
        # Not made yet (a file opened before Store.initialize): read without sharing.
        return build(c, statement)
    with cache.lock:
        hit = cache.values.get(name)
    if hit is not None and hit[0] == clock:
        return hit[1]
    found = build(c, statement)
    with cache.lock:
        cache.values[name] = (clock, found)
    return found


def _rows(c, statement):
    return tuple(dict(r) for r in c.execute(statement))


def parties(c):
    """Every private task's (id, requester, owner, guest, private), as dicts the caller must not mutate."""
    return _shared(c, "parties", PARTIES, _rows)


def access_rows(c):
    """The owner, access and rules registry rows, sorted, as (key, value_json) tuples."""
    return _shared(c, "access", ACCESS, lambda conn, statement: tuple(sorted(
        tuple(r) for r in conn.execute(statement))))
