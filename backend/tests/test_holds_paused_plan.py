"""The pauses' reads and bots.state switch (backend/company_pause.py, holds._switch) plan from the holds indexes
whatever sqlite_stat1 says. `holds` keeps every released hold as history, so a scan of the table grows with it; the
open holds are a few dozen rows in holds_open (a partial index on released_at IS NULL)."""

import sqlite3

import pytest

from backend import company_pause
from backend.config import Settings
from backend.store import H, Store

# Stats a server analysed while `holds` was small, or skewed toward its history (SQLite 3.54.1).
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
def test_pause_reads_and_the_state_switch_search_holds_open_even_with_stale_stats(tmp_path, stats):
    path = tmp_path / "hub.sqlite"
    Store(Settings(db_path=path)).initialize(seed_market=False)
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO bots(id,slug,state,created_at) VALUES('ops','ops','active',?)", (H.now(),))
        c.execute("ANALYZE")
        c.execute("DELETE FROM sqlite_stat1 WHERE tbl='holds'")
        c.executemany("INSERT INTO sqlite_stat1(tbl,idx,stat) VALUES(?,?,?)", stats)
    c = sqlite3.connect(path)  # a fresh connection loads the stale stats
    c.row_factory = sqlite3.Row
    seen: list[str] = []
    c.set_trace_callback(seen.append)
    try:
        assert company_pause.record(c) is None
        company_pause.pause_bot(c, "human:ana", "ops")
        company_pause.resume_bot(c, "human:ana", "ops")
        assert c.execute("SELECT state FROM bots WHERE id='ops'").fetchone()[0] == "active"
    finally:
        c.set_trace_callback(None)
        c.rollback()
    reads = sorted(
        {s for s in seen if ("FROM holds" in s or s.startswith("UPDATE holds")) and not s.startswith("EXPLAIN")}
    )
    assert any(s.lstrip().upper().startswith("UPDATE BOTS") for s in reads), reads  # the switch's NOT EXISTS
    assert len(reads) >= 3, reads
    for sql in reads:
        plan = " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql))
        assert "SEARCH holds USING INDEX holds_open" in plan, (sql, plan)
        assert "SCAN holds" not in plan, (sql, plan)
