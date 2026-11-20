"""Planner statistics (backend/planner_stats.py): a fresh file gets sqlite_stat1, reads after a refresh plan with it,
the hourly pass analyses only what is stale, and no table holds the write lock past HOLD_MAX."""

import sqlite3
from contextlib import closing

from backend import planner_stats
from backend.config import Settings
from backend.store import Store

# y has two values, x one per row: with statistics the planner takes x's index for a narrow x range; without them it
# guesses that y=? is the better key and steps through half the table.
PLAN = "EXPLAIN QUERY PLAN SELECT * FROM t WHERE y=1 AND x>49990"


def store(tmp_path):
    path = tmp_path / "hub.sqlite"
    with closing(sqlite3.connect(path)) as c:
        c.execute("CREATE TABLE t(x INTEGER, y INTEGER)")
        c.execute("CREATE INDEX t_y ON t(y)")
        c.execute("CREATE INDEX t_x ON t(x)")
        c.execute("CREATE TABLE empty(z)")
        c.execute(
            "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<50000) "
            "INSERT INTO t SELECT i, i%2 FROM n"
        )
        c.commit()
    return Store(Settings(db_path=path))


def plan(c):
    return " ".join(row[3] for row in c.execute(PLAN))


def test_a_fresh_file_gets_statistics_and_every_read_after_plans_with_them(tmp_path):
    s = store(tmp_path)
    assert planner_stats.refresh(s) == 0  # a few MB: below MIN_BYTES, nothing to gain
    with s.read() as idle:
        assert "t_y" in plan(idle)
    with s.read() as held:  # out of the pool while the refresh runs
        assert planner_stats.refresh(s, min_bytes=0) == 1  # `t`; the empty table gets no statistics
        assert "t_y" in plan(held)  # an open connection keeps what it read when it opened
    with s.read() as after:
        assert after is not idle and after is not held
        assert "t_x" in plan(after)
        assert after.execute("SELECT stat FROM sqlite_stat1 WHERE idx='t_x'").fetchone()[0] == "50000 1"
    s.close()


def test_the_hourly_pass_keeps_the_pool_when_nothing_is_stale(tmp_path):
    s = store(tmp_path)
    assert planner_stats.refresh(s, every=True, min_bytes=0) == 1
    with s.read() as kept:
        pass
    assert planner_stats.refresh(s, min_bytes=0) == 0
    with s.read() as c:
        assert c is kept
    s.close()


def test_a_table_never_analysed_is_found_hourly_and_a_scan_past_its_deadline_lets_go_of_the_lock(tmp_path, monkeypatch):
    s = store(tmp_path)
    assert planner_stats.refresh(s, every=True, min_bytes=0) == 1
    with s.transaction() as c:
        c.execute("INSERT INTO empty VALUES(1)")
    with s.read() as c:
        assert planner_stats.stale(c) == ["empty"]
    monkeypatch.setattr(planner_stats, "HOLD_MAX", 0.0)
    monkeypatch.setattr(planner_stats, "STEPS", 1)
    assert planner_stats.refresh(s, every=True, min_bytes=0) == 0  # each scan, full and sampled, is given up
    with s.transaction() as c:  # the lock is free again
        c.execute("INSERT INTO empty VALUES(2)")
    with s.read() as c:
        assert c.execute("SELECT stat FROM sqlite_stat1 WHERE idx='t_x'").fetchone()[0] == "50000 1"  # as it was
        assert planner_stats.stale(c) == ["empty"]
    s.close()
