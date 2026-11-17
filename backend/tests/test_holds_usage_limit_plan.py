"""The login holds' reads (backend/login_holds.py, claim_loads.open_holds) plan from the holds indexes whatever
sqlite_stat1 says. `holds` keeps every released hold as history, so a scan of the table grows with it; the open holds
are a few dozen rows in holds_open and holds_bot (partial indexes on released_at IS NULL)."""

import sqlite3

import pytest

from backend import claim_loads, holds, login_holds
from backend.config import Settings
from backend.store import Store

# Stats under which the planner scans the whole table for "every open hold" (SQLite 3.54.1), and the analysed-small
# table a new server has.
BIG = [
    ("holds", None, "100000"),
    ("holds", "holds_open", "100000 2 2 1 1"),
    ("holds", "holds_bot", "100000 100000"),
    ("holds", "holds_released", "100000 50000 1 1 1"),
]
SKEW = [
    ("holds", None, "10"),
    ("holds", "holds_open", "5000 5000 5000 5000 5000"),
    ("holds", "holds_bot", "5000 5000"),
    ("holds", "holds_released", "1 1 1 1 1"),
]
TINY = [("holds", None, "1"), ("holds", "holds_open", "1 1 1 1 1"), ("holds", "holds_bot", "1 1")]


@pytest.mark.parametrize("stats", [BIG, SKEW, TINY], ids=["big", "skew", "tiny"])
def test_login_hold_reads_search_the_open_holds_indexes_even_with_stale_stats(tmp_path, stats):
    path = tmp_path / "hub.sqlite"
    Store(Settings(db_path=path)).initialize(seed_market=False)
    with sqlite3.connect(path) as c:
        c.execute("ANALYZE")
        c.execute("DELETE FROM sqlite_stat1 WHERE tbl='holds'")
        c.executemany("INSERT INTO sqlite_stat1(tbl,idx,stat) VALUES(?,?,?)", stats)
    c = sqlite3.connect(path)  # a fresh connection loads the stale stats
    c.row_factory = sqlite3.Row
    seen: list[str] = []
    c.set_trace_callback(seen.append)
    try:
        assert claim_loads.open_holds(c) == {}
        assert login_holds.every(c) == []
        assert login_holds.on_runner(c, "mac") == []
        assert not login_holds.for_attempt(c, "a1")
        assert login_holds.get(c, "mac", "default", "claude") == {}
        assert not c.execute(holds.ANY_LOGIN_HOLD_SQL).fetchone()
        login_holds.clear(c, "mac", "default", "claude")
    finally:
        c.set_trace_callback(None)
        c.rollback()
    reads = sorted({s for s in seen if s.startswith(("SELECT", "UPDATE holds")) and " holds " in s})
    assert len(reads) >= 6 and any(s.startswith("UPDATE holds") for s in reads), reads
    for sql in reads:
        plan = " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, ("x",) * sql.count("?")))
        assert "holds_open" in plan or "holds_bot" in plan, (sql, plan)
        assert "SCAN holds" not in plan or "USING INDEX holds_bot" in plan, (sql, plan)
