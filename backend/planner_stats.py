# pyright: strict
"""SQLite's planner statistics (sqlite_stat1), by which it picks an index for each read. A file without them guesses,
and on a large company a guess can step through a whole table that an index would have found: the hosted TIDY company
had none on Nov 19 (1.9 GB), and one read scanned 29k messages per row.

The scheduler calls `refresh` on its first pass after a start, so every release a company takes refreshes every table
(`every`), and hourly after that for the tables PRAGMA optimize names: never analysed, or grown or shrunk tenfold since.
Each table is analysed in its own write transaction, so the write lock is held for one table's scan at a time; on a
fictional 1.8 GB company of TIDY's shape the longest hold (events, 364k rows, seven indexes) was 0.19 s and the whole
pass 0.6 s warm (1.4 s and 3.4 s from a cold page cache), and an hourly pass with nothing stale 9 ms. No analysis_limit: a limit of 1000 rows put the rows per key of events' actor index at 69,
where there are 1354.

A connection reads the statistics when it opens and keeps them, so after a refresh that analysed anything the store's
pooled connections are retired: each is closed when it is next given back, and new reads open new ones."""

import re
import sqlite3
from contextlib import AbstractContextManager
from typing import Protocol

# Below this the file is read from memory whatever plan a read takes, and its statistics wait for it to grow: a test's
# database (a few MB) and a new company skip the pass.
MIN_BYTES = 16 * 1024 * 1024
# 0x10000: every table, not only those this connection read; 0x02: ANALYZE where it helps; 0x01: list those statements
# instead of running them, so each can run in a transaction of its own.
LIST = "PRAGMA optimize=0x10003"
_LISTED = re.compile(r'ANALYZE "main"\."((?:[^"]|"")+)"')


class Store(Protocol):
    """What a refresh uses of backend/store.py's Store."""

    def read(self) -> AbstractContextManager[sqlite3.Connection]: ...
    def transaction(self) -> AbstractContextManager[sqlite3.Connection]: ...
    def retire_pool(self) -> None: ...


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def stale(c: sqlite3.Connection, every: bool = False) -> list[str]:
    """The tables to analyse, each holding at least one row (an empty table gets no statistics, so optimize would name
    it again every hour): all of them with `every`, else those PRAGMA optimize names."""
    if every:
        names: list[str] = [
            str(row[0])
            for row in c.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND sql NOT LIKE 'CREATE VIRTUAL TABLE%' ORDER BY name"
            )
        ]
    else:
        listed = (str(row[0]) for row in c.execute(LIST))
        names = [m.group(1).replace('""', '"') for s in listed if (m := _LISTED.fullmatch(s))]
    return [name for name in names if c.execute(f"SELECT 1 FROM {_quote(name)} LIMIT 1").fetchone()]


def refresh(store: Store, every: bool = False, min_bytes: int = MIN_BYTES) -> int:
    """Analyse the stale tables (all of them with `every`), one write transaction each; returns how many."""
    with store.read() as c:
        size = c.execute("PRAGMA page_count").fetchone()[0] * c.execute("PRAGMA page_size").fetchone()[0]
        names = stale(c, every) if size >= min_bytes else []
    for name in names:
        with store.transaction() as c:
            c.execute(f"ANALYZE main.{_quote(name)}")
    if names:
        store.retire_pool()
    return len(names)
