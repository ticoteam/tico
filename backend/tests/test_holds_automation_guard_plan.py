"""The run guard reads its holds from the holds table (backend/holds.py) by its indexes, whatever sqlite_stat1 says: the
automation's open hold, the counts and task it changes on that hold, where a run goes when it can run again, and the last
release the guard counts from each seek holds_open or holds_released, never a scan of holds."""

import re
import sqlite3

import pytest

from backend import automations, holds
from backend.config import Settings
from backend.store import Store

# A company with many released holds and a few open ones (the PR's bench seed).
BIG = [
    ("holds", None, "2000"),
    ("holds", "holds_open", "50 25 2 1 1"),
    ("holds", "holds_bot", "50 1"),
    ("holds", "holds_released", "1950 2 1 1 1"),
    ("holds", "holds_bot_kind", "2000 10 5 2"),
    ("automation_runs", None, "50000"),
]
# Stats from when holds was nearly empty, the shape that once sent another read to a scan (test_addressed_plan.py).
STALE = [("holds", None, "1"), ("holds", "holds_bot_kind", "1 1 1 1"), ("automation_runs", None, "1")]


@pytest.fixture(params=[BIG, STALE], ids=["big", "stale"])
def c(tmp_path, request):
    path = tmp_path / "hub.sqlite"
    Store(Settings(db_path=path)).initialize(seed_market=False)
    with sqlite3.connect(path) as setup:
        setup.execute("ANALYZE")
        setup.execute("DELETE FROM sqlite_stat1")
        setup.executemany("INSERT INTO sqlite_stat1(tbl,idx,stat) VALUES(?,?,?)", request.param)
    conn = sqlite3.connect(path)  # a fresh connection loads the stats
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def plan(c, sql):
    return [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, ("a",) * sql.count("?"))]


def seeks(steps, alias, index, columns):
    assert [
        p for p in steps if re.match(rf"SEARCH {alias} USING (COVERING )?INDEX {index} \({re.escape(columns)}", p)
    ], steps
    assert not any(re.match(r"SCAN (g|holds)\b", p) for p in steps), steps


OPEN = "kind=? AND holdable_type=? AND holdable_id=?"


def test_the_automations_open_hold_is_one_seek(c):
    seeks(plan(c, holds.OPEN_SQL), "holds", "holds_open", OPEN)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE holds INDEXED BY holds_open SET details=json_set(details,'$.arrivals',1),updated_at=? "
        f"WHERE {automations.OPEN_HOLD}",
        f"UPDATE holds INDEXED BY holds_open SET upkeep_task_id=?,updated_at=? WHERE {automations.OPEN_HOLD}",
    ],
    ids=["counted", "task"],
)
def test_what_a_hold_counts_changes_its_open_row_alone(c, sql):
    seeks(plan(c, sql), "holds", "holds_open", "kind=? AND holdable_type=? AND holdable_id=?")


def test_where_a_run_goes_next_seeks_its_automations_open_hold(c):
    steps = plan(
        c,
        f"UPDATE automation_runs SET status={automations.RUNNABLE},wake_at=NULL WHERE status='waiting' AND wake_at<=?",
    )
    seeks(steps, "g", "holds_open", "kind=? AND holdable_type=? AND holdable_id=?")


def test_the_guard_counts_from_the_last_release_by_one_seek(c):
    seeks(
        plan(c, automations.LAST_RELEASE_SQL), "holds", "holds_released", "holdable_type=? AND holdable_id=? AND kind=?"
    )
