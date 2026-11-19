# pyright: strict
"""SQLite's planner statistics (sqlite_stat1), by which it picks an index for each read. A file without them guesses,
and on a large company a guess can step through a whole table that an index would have found: the hosted TIDY company
had none on Nov 19 (1.9 GB), and one read scanned 29k messages per row.

The scheduler calls `refresh` on its first pass after a start, so every release a company takes refreshes every table
(`every`), and hourly after that for the tables PRAGMA optimize names: never analysed, or grown or shrunk tenfold since.
Each table is analysed in its own write transaction, so the write lock is held for one table's scan at a time; on a
fictional 1.8 GB company of TIDY's shape the longest hold (events, 364k rows, seven indexes) was 0.19 s and the whole
pass 0.6 s warm (1.4 s and 3.4 s from a cold page cache), and an hourly pass with nothing stale 9 ms. No
analysis_limit unless a table's full scan would pass HOLD_MAX: a limit of 1000 rows put the rows per key of events' actor index at 69,
where there are 1354.

A connection reads the statistics when it opens and keeps them, so after a refresh that analysed anything the store's
pooled connections are retired: each is closed when it is next given back, and new reads open new ones."""

import logging
import re
import sqlite3
import time
from contextlib import AbstractContextManager
from typing import Protocol

# Below this the file is read from memory whatever plan a read takes, and its statistics wait for it to grow: a test's
# database (a few MB) and a new company skip the pass.
MIN_BYTES = 16 * 1024 * 1024
# 0x10000: every table, not only those this connection read; 0x02: ANALYZE where it helps; 0x01: list those statements
# instead of running them, so each can run in a transaction of its own.
LIST = "PRAGMA optimize=0x10003"
_LISTED = re.compile(r'ANALYZE "main"\."((?:[^"]|"")+)"')
# A table's ANALYZE holds the write lock at most this long, well inside the store's WRITE_WAIT (10 s) that queued writers
# wait: past it the scan is interrupted and the table is analysed again from a sample of LIMITED rows per index.
HOLD_MAX = 3.0
LIMITED = 1000
STEPS = 10_000  # SQLite VM steps between deadline checks
LOG = logging.getLogger(__name__)


class Store(Protocol):
    """What a refresh uses of backend/store.py's Store."""

    def read(self) -> AbstractContextManager[sqlite3.Connection]: ...
    def transaction(self) -> AbstractContextManager[sqlite3.Connection]: ...
    def retire_pool(self) -> None: ...


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def stale(c: sqlite3.Connection, every: bool = False) -> list[str]:
    """The tables to analyse, each holding at least one row (an empty table gets no statistics, so it would be named
    again every hour): all of them with `every`, else those with no statistics yet and those PRAGMA optimize names
    (changed tenfold; SQLite before 3.46 names none, and the next start's full pass catches them)."""
    tables = [
        str(row[0])
        for row in c.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND sql NOT LIKE 'CREATE VIRTUAL TABLE%' ORDER BY name"
        )
    ]
    if not every:
        has = c.execute("SELECT 1 FROM sqlite_schema WHERE name='sqlite_stat1'").fetchone()
        analysed: set[str] = (
            {str(row[0]) for row in c.execute("SELECT DISTINCT tbl FROM sqlite_stat1")} if has else set()
        )
        listed = (str(row[0]) for row in c.execute(LIST))
        changed = {m.group(1).replace('""', '"') for s in listed if (m := _LISTED.fullmatch(s))}
        tables = [name for name in tables if name not in analysed or name in changed]
    return [name for name in tables if c.execute(f"SELECT 1 FROM {_quote(name)} LIMIT 1").fetchone()]


def _analyse(store: Store, name: str, limit: int) -> bool:
    """ANALYZE one table in its own write transaction, rolled back at HOLD_MAX; False when it was."""
    deadline = time.monotonic() + HOLD_MAX
    try:
        with store.transaction() as c:
            # The progress handler taints the pooled connection, so it is closed after this write, not reused.
            c.set_progress_handler(lambda: time.monotonic() > deadline, STEPS)
            c.execute(f"PRAGMA analysis_limit={int(limit)}")
            c.execute(f"ANALYZE main.{_quote(name)}")
            c.set_progress_handler(None, 0)
    except sqlite3.OperationalError as exc:
        if "interrupt" not in str(exc):
            raise
        return False
    return True


def refresh(store: Store, every: bool = False, min_bytes: int = MIN_BYTES) -> int:
    """Analyse the stale tables (all of them with `every`), one write transaction each; returns how many. A table whose
    full scan would hold the write lock past HOLD_MAX is analysed from a sample (LIMITED rows per index) instead, and
    skipped if even that takes too long."""
    with store.read() as c:
        size = c.execute("PRAGMA page_count").fetchone()[0] * c.execute("PRAGMA page_size").fetchone()[0]
        names = stale(c, every) if size >= min_bytes else []
    done = 0
    try:
        for name in names:
            if _analyse(store, name, 0) or _analyse(store, name, LIMITED):
                done += 1
            else:
                LOG.warning("planner statistics: %s not analysed within %.0f s", name, HOLD_MAX)
    finally:
        if done:
            store.retire_pool()
    return done
