"""Start failures read from the holds table (backend/holds.py) by its indexes, whatever sqlite_stat1 says: a run's claim
error wait (the fold and input reads), every job's open start-failure holds, the task holds' reads, the bot's Needs-you
item, Health's list, and the two triggers that end the holds (migration 20261117104652_holds_start_failures) each seek
holds_open or holds_bot, never a scan of holds or jobs."""

import importlib
import re
import sqlite3

import pytest

from backend import bot_stops, execution, holds
from backend.config import Settings
from backend.store import Store

MIGRATION = importlib.import_module("backend.migrations.20261117104652_holds_start_failures")

# A company with many released holds and a few open ones, bots and jobs to match (the PR's bench seed).
BIG = [
    ("holds", None, "2000"),
    ("holds", "holds_open", "50 25 2 1 1"),
    ("holds", "holds_bot", "50 1"),
    ("holds", "holds_released", "1950 2 1 1"),
    ("holds", "holds_bot_kind", "2000 10 5 2"),
    ("bots", None, "200"),
    ("jobs", None, "50000"),
    ("tasks", None, "20000"),
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


def seeks(steps, alias, index, columns):
    found = [p for p in steps if re.match(rf"SEARCH {alias} USING (COVERING )?INDEX {index} \({re.escape(columns)}", p)]
    assert found, steps


def no_scan(steps):
    assert not any(re.match(r"SCAN (h|h2|holds|j|j2|jobs)\b", p) for p in steps), steps


def trigger_update(trigger):
    """The UPDATE a trigger runs, with the row it fires on as a parameter."""
    return trigger.split(" BEGIN ", 1)[1].rsplit("; END", 1)[0].replace("NEW.id", "?")


WAITING = f"j.state='queued' AND j.held=0 AND NOT {execution.CLAIM_WAITING_SQL}"


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT 1 FROM jobs j WHERE j.bot_id=? AND {WAITING}",  # the fold and the inputs a running turn takes
        # The claim's bots with work (Execution.candidate): one seek per bot's first queued run.
        "SELECT a.bot FROM assignments a JOIN bots b ON b.id=a.bot WHERE a.runner_id=? AND b.state='active' "
        f"AND EXISTS(SELECT 1 FROM jobs j WHERE j.bot_id=a.bot AND {WAITING})",
    ],
    ids=["jobs", "claim_bots"],
)
def test_a_runs_claim_error_wait_is_one_seek_of_its_open_hold(c, sql):
    steps = plan(c, sql)
    seeks(steps, "j( EXISTS)?", "jobs_queue", "bot_id=? AND state=?")
    seeks(steps, "h", "holds_open", "kind=? AND holdable_type=? AND holdable_id=?")
    no_scan(steps)


@pytest.mark.parametrize(
    "sql",
    [holds.JOB_HOLDS_SQL, holds.claim_held_sql(), holds.claim_held_sql(many=True)],
    ids=["job_holds", "claim_held", "claim_held_many"],
)
def test_the_job_holds_reads_start_from_the_open_ones(c, sql):
    steps = plan(c, sql)
    seeks(steps, "(h|holds)", "holds_open", "kind=? AND holdable_type=?")
    no_scan(steps)


@pytest.mark.parametrize("many", [False, True], ids=["one", "many"])
def test_a_tasks_cant_start_holds_seek_the_open_ones(c, many):
    steps = plan(c, holds.cant_start_sql(many))
    seeks(steps, "h", "holds_open", "kind=? AND holdable_type=?")
    seeks(steps, "r", "sqlite_autoindex_tasks_1", "id=?")
    no_scan(steps)


def test_the_bots_needs_you_item_seeks_its_open_holds(c):
    steps = plan(c, bot_stops.CLAIM_ITEM_SQL)
    seeks(steps, "holds", "holds_bot", "bot_id=?")
    no_scan(steps)


@pytest.mark.parametrize("trigger", [MIGRATION.JOB_ENDS, MIGRATION.TASK_CLOSES], ids=["job_ends", "task_closes"])
def test_the_triggers_that_end_the_holds_seek_the_open_ones(c, trigger):
    steps = plan(c, trigger_update(trigger))
    seeks(steps, "holds", "holds_open", "kind=? AND holdable_type=?")
    no_scan(steps)


def test_a_jobs_claim_error_open_or_last_released_is_one_seek_of_each_partial_index(c):
    steps = plan(c, bot_stops.JOB_CLAIM_SQL.replace("?1", "?"))
    seeks(steps, "holds", "holds_open", "kind=? AND holdable_type=? AND holdable_id=?")
    seeks(steps, "holds", "holds_released", "holdable_type=? AND holdable_id=? AND kind=?")
    no_scan(steps)


def test_health_reads_the_open_claim_errors_alone(c):
    statements = []
    c.set_trace_callback(statements.append)
    bot_stops.claim_holds(c)
    c.set_trace_callback(None)
    assert len(statements) == 2, statements
    for sql in statements:
        steps = plan(c, sql)
        seeks(steps, "h", "holds_open", "kind=? AND holdable_type=?")
        no_scan(steps)
