"""The claim's read of open `failing` holds (claim_loads._failing), one bot's (holds.get) and the held list
(run_failures.held) seek holds_open, never scan holds, whatever sqlite_stat1 says: holds keeps every release as history,
so it only grows (PR #3973 moved run_backoff, one row per bot, into it)."""

import sqlite3

import pytest

from backend import claim_loads
from backend import holds
from backend import run_failures
from backend.config import Settings
from backend.store import Store

# No stats (Tico never runs ANALYZE), stats that call holds tiny, and stats that call holds_open unselective.
STATS = {
    "none": None,
    "tiny": [("holds", None, "10"), ("holds", "holds_open", "10 10 10 10 10")],
    "unselective": [("holds", None, "100000"), ("holds", "holds_open", "50 50 25 5 5")],
}


@pytest.mark.parametrize("stats", list(STATS.values()), ids=list(STATS))
def test_failing_hold_reads_seek_holds_open(tmp_path, stats):
    path = tmp_path / "hub.sqlite"
    Store(Settings(db_path=path)).initialize(seed_market=False)
    if stats:
        c = sqlite3.connect(path)
        c.execute("ANALYZE")
        c.execute("DELETE FROM sqlite_stat1")
        c.executemany("INSERT INTO sqlite_stat1(tbl, idx, stat) VALUES (?,?,?)", stats)
        c.commit()
        c.close()
    c = sqlite3.connect(path)  # a fresh connection loads the stats
    c.row_factory = sqlite3.Row
    seen: list[str] = []
    c.set_trace_callback(seen.append)
    try:
        assert claim_loads._failing(c, ["ops", "web"]) == {"ops": None, "web": None}
        assert holds.get(c, holds.FAILING, "bot", "ops") is None
        assert run_failures.held(c) == []
    finally:
        c.set_trace_callback(None)
    reads = [s for s in seen if "FROM holds" in s]
    assert len(reads) == 3, reads
    for sql in reads:  # the trace has the values written in
        plan = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql)]
        assert "USING INDEX holds_open (kind=? AND holdable_type=?" in plan[0], (sql, plan)
        assert not any(p.startswith("SCAN holds") for p in plan), (sql, plan)
