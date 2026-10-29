"""Share the roster's whole-table reads, every bot, every bot's settings and the task step definitions, between requests
until one of them changes.

Most requests read all of `bots` and `bot_config` once in their snapshot (backend/read_cache.py), and those rows change
a few times an hour at most: on a busy server the two reads were a tenth of all SQL time. SQLite triggers count every
change from every connection, as provenance_clock does, so the rows read at one count are the rows at that count.
These are neutral stored rows, never a permission result.
"""
import sqlite3

TABLES = ("bots", "bot_config", "task_steps")
STATEMENTS = frozenset({"SELECT * FROM bot_config", "SELECT * FROM bots ORDER BY slug",
                        "SELECT * FROM task_steps ORDER BY position,id"})


def ensure(c):
    c.execute("CREATE TABLE IF NOT EXISTS roster_clock(id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL)")
    c.execute("INSERT OR IGNORE INTO roster_clock VALUES(1,0)")
    for table in TABLES:
        for operation in ("INSERT", "UPDATE", "DELETE"):
            c.execute(f"CREATE TRIGGER IF NOT EXISTS roster_clock_{table}_{operation.lower()} AFTER {operation} "
                      f"ON {table} BEGIN UPDATE roster_clock SET value=value+1 WHERE id=1; END")


def _clock(c):
    return c.execute("SELECT value FROM roster_clock WHERE id=1").fetchone()[0]


def rows(c, statement):
    """`statement`'s rows, one of STATEMENTS. A miss reads the clock and the rows in one snapshot (the caller's read
    transaction, or one opened here), so the rows stored under a count are exactly the rows at that count."""
    shared = getattr(c, "roster_rows", None)
    # A connection that has written may hold uncommitted roster changes, which can roll back to a count a later write
    # reuses: it reads its own rows and shares none (provenance_clock.cached).
    if shared is None or c.total_changes != getattr(c, "provenance_changes", None):
        return tuple(c.execute(statement))
    try:
        clock = _clock(c)
    except (sqlite3.OperationalError, TypeError):
        # Not made yet (a file opened before Store.initialize): read without sharing. Asking sqlite_master instead costs
        # a statement on every fresh connection.
        return tuple(c.execute(statement))
    hit = shared.get(statement)
    if hit is not None and hit[0] == clock:
        return hit[1]
    if c.in_transaction:
        found = tuple(c.execute(statement))
    else:
        c.execute("BEGIN")
        try:
            clock = _clock(c)
            found = tuple(c.execute(statement))
        except BaseException:
            c.rollback()
            raise
        c.commit()
    if not getattr(c, "write_transaction", False):
        shared[statement] = (clock, found)
    return found
