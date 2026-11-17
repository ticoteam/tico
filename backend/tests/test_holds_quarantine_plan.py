"""Quarantines read from the holds table (backend/holds.py) by its indexes, whatever sqlite_stat1 says: the claim's queued
jobs and a waiting note seek a bot's open holds (holds_bot or holds_bot_kind), the cooldown sweep the open quarantines
(holds_open), and a refusal's escalation the bot's holds of the day (holds_bot_kind). The bot list reads bots alone."""

import re
import sqlite3

import pytest

from backend import execution
from backend import hubdb as H
from backend import task_quarantine
from backend.config import Settings
from backend.store import Store

# A company with many released holds and a few open ones, bots and jobs to match (the PR's bench seed).
BIG = [
    ("holds", None, "2000"),
    ("holds", "holds_open", "50 25 2 1 1"),
    ("holds", "holds_bot", "50 1"),
    ("holds", "holds_released", "1950 2 1 1"),
    ("holds", "holds_bot_kind", "2000 10 5 2"),
    ("bots", None, "200"),
    ("jobs", None, "50000"),
]
# Stats from when holds was nearly empty, the shape that once sent another read to a scan (test_addressed_plan.py).
STALE = [("holds", None, "1"), ("holds", "holds_bot_kind", "1 1 1 1"), ("bots", None, "1"), ("jobs", None, "1")]


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
    return [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, ("ops",) * sql.count("?"))]


def seeks_open_holds(steps):
    """A step seeks the bot's open holds by one of the bot's indexes (the planner may take either: both are a seek)."""
    found = [p for p in steps if re.match(r"SEARCH qh (EXISTS )?USING INDEX holds_bot(_kind)? \(bot_id=\?", p)]
    assert found, steps


def no_holds_scan(steps):
    assert not any(p.startswith(("SCAN qh", "SCAN holds", "SCAN h")) for p in steps), steps


@pytest.mark.parametrize("bots", [None, ["ops"], [f"b{n:03}" for n in range(200)]], ids=["claim", "one", "prime"])
def test_the_claims_queued_jobs_seek_the_bots_open_holds(c, bots):
    steps = plan(c, execution.queued_jobs_sql(bots))
    seeks_open_holds(steps)
    no_holds_scan(steps)


def test_a_waiting_note_seeks_its_recipients_open_holds(c):
    steps = plan(c, "SELECT 1 FROM notes n WHERE n.to_actor=? AND " + task_quarantine.notes_free_sql())
    seeks_open_holds(steps)
    no_holds_scan(steps)


def test_an_escalation_seeks_the_bots_holds_of_the_day(c):
    steps = plan(c, H.QUARANTINE_SCOPES_SQL)
    assert any(p.startswith("SEARCH holds USING INDEX holds_bot_kind (bot_id=? AND kind=?") for p in steps), steps
    no_holds_scan(steps)


def test_the_cooldown_sweep_seeks_open_quarantines(c):
    steps = plan(c, H.COOLED_BOTS_SQL)
    assert any(p.startswith("SEARCH h USING INDEX holds_open (kind=? AND holdable_type=?)") for p in steps), steps
    assert not any(p.startswith(("SCAN h", "SCAN b")) for p in steps), steps


def test_the_bot_list_reads_bots_alone(c):
    seen = []
    c.set_trace_callback(seen.append)
    try:
        H.bots(c)
    finally:
        c.set_trace_callback(None)
    assert seen == ["SELECT * FROM bots ORDER BY slug"], seen
