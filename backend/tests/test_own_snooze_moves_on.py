"""A bot's own snooze does not hold a task that is ready to move on (backend/github.py end_own_snooze,
task_engine.move_on_past_own_snooze, never_stuck._ready_snoozes).

Nov 12, Tico Team's Engineering pipeline: the Builder snoozed its Build tasks "waiting for PR checks ... Build leaves by
itself when they pass". The checks passed, everything Build asks was met and Review 1 has no limit, yet each task sat
for hours with "Moving to Review 1" until a person moved it: the finished checks ended the snooze only on the owner's
turn, and with everything met it is nobody's turn, so the snooze stayed and the engine never moves a snoozed task.
"""

from datetime import datetime, timezone

import pytest

from backend import github as G
from backend import never_stuck as NS
from backend import task_engine as E
from backend import waiting_on as W
from backend.repositories import metadata, save_metadata
from backend.store import H
from backend.tests.helpers import engine as X

BUILDER = "bot:builder"
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)


def _stages(limit=None):
    return (
        {"name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
        {"name": "Review 1", "owner": "assignee", "task_limit": limit, "requirements": [{"kind": "owner_done"}]},
    )


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        X.bot(conn, "builder")
        X.bot(conn, "manager")
        yield conn


def _snoozed_and_ready(c, pid="eng", limit=None, by=BUILDER):
    """A Build task the Builder owns, snoozed by `by` "until its checks pass", then everything Build asks is met (the
    checks passed while it slept)."""
    X.pipeline(c, pid=pid, name=pid, stages=_stages(limit), manager="bot:manager")
    row = X.new_task(c, BUILDER, "Fix the flaky test", pipeline_id=pid)
    E.snooze(
        c,
        row["id"],
        by,
        "Waiting for PR checks: a finished check run wakes me; Build leaves by itself when they pass",
        until=H.shift(H.now(), days=1),
        seen_version=row["version"],
    )
    X.say_done(c, E.task(c, row["id"]), by=BUILDER)
    return row["id"]


def _stage(c, task_id):
    return E.task(c, task_id)["stage"]["name"]


def _checks_finish(c, task_id, result="pass"):
    G.queue_checks_done(c, E.task(c, task_id), "a" * 40, result, "Checks finished on acme/app#7: passed", False)
    key = "github-task-wake:" + task_id
    save_metadata(c, key, {**metadata(c, key), "due": H.shift(H.now(), seconds=-1)})
    return G.flush_wakes(c)


def test_finished_checks_move_a_ready_task_on_at_once(c):
    task_id = _snoozed_and_ready(c)
    assert [(w.state, w.line) for w in W.waiting_on(c, task_id)][-1] == (W.ROOM, "Moving to Review 1")
    assert _checks_finish(c, task_id) == [task_id]
    row = E.task(c, task_id)
    assert row["stage"]["name"] == "Review 1" and not row.get("snooze")


def test_finished_checks_end_the_snooze_of_a_ready_task_with_no_room_and_it_waits_for_room(c):
    task_id = _snoozed_and_ready(c, limit=1)
    full = X.new_task(c, BUILDER, "Already in review", pipeline_id="eng")
    X.say_done(c, full, by=BUILDER)
    assert E.advance(c, full["id"])
    assert _checks_finish(c, task_id) == [task_id]
    row = E.task(c, task_id)
    assert row["stage"]["name"] == "Build" and not row.get("snooze")


def test_the_never_stuck_pass_moves_a_ready_task_its_owner_snoozed(c):
    """No event ended it (the checks finished before the snooze was written, or the result was lost): the next pass
    does."""
    task_id = _snoozed_and_ready(c)
    done = NS.check(c)
    assert done["moved"] == 1 and done["snoozes_ended"] == 1
    assert _stage(c, task_id) == "Review 1" and not E.task(c, task_id).get("snooze")
    assert NS.rules_hold(c) == []


def test_a_snooze_someone_else_set_or_a_task_still_missing_something_stays(c):
    by_person = _snoozed_and_ready(c, by=X.ANA)
    X.pipeline(c, pid="other", name="other", stages=_stages(), manager="bot:manager")
    missing = X.new_task(c, BUILDER, "Still building", pipeline_id="other")
    E.snooze(
        c, missing["id"], BUILDER, "Waiting for checks", until=H.shift(H.now(), days=1), seen_version=missing["version"]
    )
    NS.check(c)
    for task_id in (by_person, missing["id"]):
        assert _stage(c, task_id) == "Build" and E.task(c, task_id).get("snooze")


def test_a_refused_automatic_move_keeps_the_snooze_and_is_logged(c, monkeypatch, caplog):
    task_id = _snoozed_and_ready(c)

    def refuse(conn, tid, *args, **kwargs):
        H.refuse(conn, E.SYSTEM, "task_stage_rule", "Rule Reviewer: token=abc123 not allowed", task=tid)

    monkeypatch.setattr(E, "move", refuse)
    with caplog.at_level("WARNING", logger="tico.task_engine"):
        NS.check(c)
    row = E.task(c, task_id)
    assert row["stage"]["name"] == "Build" and row.get("snooze")
    [line] = [r.getMessage() for r in caplog.records if "automatic move refused" in r.getMessage()]
    assert "category=task_stage_rule" in line and f"task={task_id}" in line and "abc123" not in line
