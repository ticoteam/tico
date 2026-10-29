"""Share the roster's whole-table reads, every bot and every bot's settings, between requests until either changes.

Most requests read all of `bots` and `bot_config` once in their snapshot (backend/read_cache.py), and those rows change
a few times an hour at most: on a busy server the two reads were a tenth of all SQL time. SQLite triggers count every
change from every connection, as provenance_clock does, so the rows read at one count are the rows at that count.
These are neutral stored rows, never a permission result.
"""
from . import hubdb as H

TABLES = ("bots", "bot_config")
STATEMENTS = frozenset({"SELECT * FROM bot_config", "SELECT * FROM bots ORDER BY slug"})


def ensure(c):
    c.execute("CREATE TABLE IF NOT EXISTS roster_clock(id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL)")
    c.execute("INSERT OR IGNORE INTO roster_clock VALUES(1,0)")
    for table in TABLES:
        for operation in ("INSERT", "UPDATE", "DELETE"):
            c.execute(f"CREATE TRIGGER IF NOT EXISTS roster_clock_{table}_{operation.lower()} AFTER {operation} "
                      f"ON {table} BEGIN UPDATE roster_clock SET value=value+1 WHERE id=1; END")


def rows(c, statement):
    """`statement`'s rows, one of STATEMENTS, in the caller's read transaction: the rows another connection read at
    the same count when there are some."""
    shared = getattr(c, "roster_rows", None)
    # A connection that has written may hold uncommitted roster changes, which can roll back to a count a later write
    # reuses: it reads its own rows and shares none (provenance_clock.cached).
    if (shared is None or c.total_changes != getattr(c, "provenance_changes", None)
            or not H._has_table(c, "roster_clock")):
        return tuple(c.execute(statement))
    clock = c.execute("SELECT value FROM roster_clock WHERE id=1").fetchone()[0]
    hit = shared.get(statement)
    if hit is not None and hit[0] == clock:
        return hit[1]
    found = tuple(c.execute(statement))
    if not getattr(c, "write_transaction", False):
        shared[statement] = (clock, found)
    return found
