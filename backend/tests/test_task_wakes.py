"""A bot's wake about a task (backend/task_wakes.py): what it carries, how wakes fold, and how runs are counted.

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company's person, three bots on one computer and a
General pipeline.
"""

from datetime import datetime, timezone

import pytest

from backend import task_wakes
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
BUILDER, MANAGER, REVIEWER = "bot:builder", "bot:manager", "bot:reviewer"
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        yield conn


@pytest.fixture
def general(c):
    for slug in ("builder", "reviewer", "manager"):
        X.bot(c, slug)
    return X.pipeline(c, manager=MANAGER)


def _queued(c, task_id):
    """Who has a wake for the task that no run has taken yet."""
    rows = c.execute(
        "SELECT j.bot_id FROM jobs j JOIN messages m ON m.id = j.message_id WHERE j.state='queued' AND m.wake_key LIKE ?",
        (task_id + ":%",),
    ).fetchall()
    return sorted(H.bot_actor(row[0]) for row in rows)


def _refs(c, message_id):
    return H._json(c.execute("SELECT refs FROM messages WHERE id=?", (message_id,)).fetchone()[0], {})


def test_a_callers_refs_never_set_what_is_the_engines(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    assert _queued(c, row["id"]) == [BUILDER]  # its start
    said = {"quiet": True, "job": False, "task": "another", "visit": "v0", "level": "manager", "automation_run": "r7"}
    message = task_wakes.add(c, row, BUILDER, row["visit"], "More words.", wake_key=f"{row['id']}:x", refs=said)
    refs = _refs(c, message)
    assert (refs["task"], refs["visit"], refs["level"]) == (row["id"], row["visit"]["id"], "owner")
    assert "quiet" not in refs and refs["job"] is True  # from the engine's own state: the wake queued a run
    assert refs["automation_run"] == "r7"  # the caller's own facts ride on
    assert _queued(c, row["id"]) == [BUILDER]  # folded into one, and the run still starts


def test_a_fold_never_leaves_a_bot_that_runs_jobs_without_a_queued_run(c, general, monkeypatch):
    from backend import enqueue

    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    monkeypatch.setattr(enqueue, "enqueue", lambda conn, message: None)  # this one wake queues nothing
    message = task_wakes.add(c, row, BUILDER, row["visit"], "More words.", wake_key=f"{row['id']}:x")
    assert message and "job" not in _refs(c, message)
    assert _queued(c, row["id"]) == [BUILDER]  # the wake it would have folded is still queued
