"""Side stages (_brief-side-stages.md, "Spec" and "Contract"): a work stage with `side` sits right after the main stage it
belongs to. The main path skips it, tasks move in and out of it by hand as plain moves, reopen and came_from know it,
and pipeline settings check it.

Synthetic rows only (backend/tests/helpers/engine.py): an Engineering pipeline with side stages, a builder, a reviewer
and a manager bot, and Ana, who files the tasks.
"""

import importlib
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from backend import never_stuck as NS
from backend import pipeline_settings as PS
from backend import task_calls
from backend import task_engine as E
from backend import task_requirements as R
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.migrations import fresh, snapshot

ANA = X.ANA
BUILDER, REVIEWER, MANAGER = "bot:builder", "bot:reviewer", "bot:manager"
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)
MIGRATION = "backend.migrations.20261117205926_side_stages"

IN_PROGRESS = {"name": "In progress", "owner": BUILDER, "requirements": [{"kind": "owner_done"}]}
REJECTED = {"name": "Rejected", "side": True, "owner": REVIEWER}
BOT_QA = {"name": "Bot QA", "owner": REVIEWER, "requirements": [{"kind": "owner_done"}]}
RELEASE = (
    IN_PROGRESS,
    REJECTED,
    BOT_QA,
    {"name": "Release hold", "side": True, "owner": MANAGER},
    {"name": "Release ready", "owner": REVIEWER},
)


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        for slug in ("builder", "reviewer", "manager"):
            X.bot(conn, slug)
        yield conn


@pytest.fixture
def later(c, time_machine):
    """Move the clock on and run one never-stuck pass. Returns what the pass did, without the zeros."""
    time_machine.move_to(START, tick=False)

    def step(**delta):
        time_machine.shift(timedelta(**delta))
        done = NS.check(c)
        assert NS.rules_hold(c) == []
        return {key: n for key, n in done.items() if n}

    return step


def _pipeline(c, stages=(IN_PROGRESS, REJECTED, BOT_QA)):
    """The Engineering pipeline, built by the engine helper. Its side stages are marked the way the settings save marks
    them (the helper's rows have no `side` column): by hand, and never asked to move on."""
    pipe = X.pipeline(c, "eng", "Engineering", stages, manager=MANAGER)
    for spec in stages:
        if spec.get("side"):
            c.execute(
                "UPDATE stages SET side=1, by_hand=1, requests_move_on=0 WHERE id=?",
                (pipe["stages"][spec["name"]],),
            )
    return pipe


def _move(c, task_id, to, by, why=None):
    """A move sent with the version the task has now, as a bot sends it (a bot's move without one is refused)."""
    return E.move(c, task_id, to, by, why, seen_version=E.task(c, task_id)["version"])


def _visits(c, task_id):
    """The task's visits, oldest first: (stage name, how it was left, how it was entered)."""
    rows = c.execute(
        "SELECT s.name, v.how, v.entered_how FROM visits v JOIN stages s ON s.id = v.stage_id "
        "WHERE v.task_id=? ORDER BY v.rowid",
        (task_id,),
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def test_advance_skips_a_side_stage_and_finished_work_goes_to_bot_qa(c):
    _pipeline(c)
    row = X.new_task(c, ANA, "Build the importer", "eng")
    X.say_done(c, row)
    assert E.advance(c, row["id"])["stage"]["name"] == "Bot QA"
    assert _visits(c, row["id"]) == [("In progress", "met", "new"), ("Bot QA", None, "met")]


def test_a_bot_moves_in_progress_to_rejected_with_no_why_though_in_progress_is_not_met(c):
    _pipeline(c)
    row = X.new_task(c, ANA, "Build the importer", "eng")
    assert R.unmet(c, row)  # In progress is not done
    assert _move(c, row["id"], "Rejected", BUILDER)["stage"]["name"] == "Rejected"
    assert _visits(c, row["id"]) == [("In progress", "side", "new"), ("Rejected", None, "side")]


def test_rejected_moves_to_bot_qa_or_in_progress_as_plain_moves_with_no_why(c):
    _pipeline(c)
    to_qa = X.new_task(c, ANA, "Build the importer", "eng")
    _move(c, to_qa["id"], "Rejected", BUILDER)
    assert _move(c, to_qa["id"], "Bot QA", ANA)["stage"]["name"] == "Bot QA"
    to_build = X.new_task(c, ANA, "Build the exporter", "eng")
    _move(c, to_build["id"], "Rejected", BUILDER)
    assert _move(c, to_build["id"], "In progress", BUILDER)["stage"]["name"] == "In progress"
    assert _visits(c, to_qa["id"])[-1] == ("Bot QA", None, "side")
    assert _visits(c, to_build["id"])[-1] == ("In progress", None, "side")


def test_a_main_stage_task_limit_still_refuses_a_move_out_of_a_side_stage(c):
    _pipeline(c, (IN_PROGRESS, REJECTED, dict(BOT_QA, task_limit=1)))
    first = X.new_task(c, ANA, "Build the importer", "eng")
    X.say_done(c, first)
    assert E.advance(c, first["id"])["stage"]["name"] == "Bot QA"  # takes the only slot
    second = X.new_task(c, ANA, "Build the exporter", "eng")
    _move(c, second["id"], "Rejected", BUILDER)
    with pytest.raises(H.Refused, match="task_stage_full"):
        _move(c, second["id"], "Bot QA", ANA, "It is urgent")  # a reason does not push past a limit
    assert E.task(c, second["id"])["stage"]["name"] == "Rejected"


def test_next_from_a_side_stage_goes_to_the_first_main_stage_after_it(c):
    _pipeline(c, RELEASE)
    row = X.new_task(c, ANA, "Ship the importer", "eng")
    _move(c, row["id"], "Rejected", BUILDER)
    assert _move(c, row["id"], E.NEXT, ANA)["stage"]["name"] == "Bot QA"
    _move(c, row["id"], "Release hold", ANA)
    assert _move(c, row["id"], E.NEXT, ANA)["stage"]["name"] == "Release ready"


def test_done_from_a_side_stage_needs_nothing(c):
    _pipeline(c)
    row = X.new_task(c, ANA, "Build the importer", "eng")
    _move(c, row["id"], "Rejected", BUILDER)  # In progress's requirement is still unmet, and nothing asks for it
    done = _move(c, row["id"], E.DONE, ANA)
    assert (done["status"], done["stage"]["name"]) == ("done", "Done")


def test_reopen_returns_a_task_closed_in_a_side_stage_to_that_side_stage(c):
    _pipeline(c)
    row = X.new_task(c, ANA, "Build the importer", "eng")
    _move(c, row["id"], "Rejected", BUILDER)
    assert _move(c, row["id"], E.CLOSED, ANA, "Not needed")["status"] == "closed"
    reopened = _move(c, row["id"], E.REOPEN, ANA, "Needed after all")
    assert (reopened["status"], reopened["stage"]["name"]) == ("open", "Rejected")


def test_a_task_in_a_side_stage_came_from_the_last_main_stage_and_other_tasks_read_null(c):
    pipe = _pipeline(c)
    row = X.new_task(c, ANA, "Build the importer", "eng")
    _move(c, row["id"], "Rejected", BUILDER)
    rejected = task_calls.task_view(c, row["id"], ANA)
    assert rejected["stage"]["name"] == "Rejected"
    assert rejected["came_from"] == {"id": pipe["stages"]["In progress"], "name": "In progress"}
    _move(c, row["id"], E.NEXT, ANA)  # Bot QA is a main stage: nothing came from anywhere
    assert task_calls.task_view(c, row["id"], ANA)["came_from"] is None
    _move(c, row["id"], "Rejected", ANA)
    assert task_calls.task_view(c, row["id"], ANA)["came_from"] == {"id": pipe["stages"]["Bot QA"], "name": "Bot QA"}


def test_stage_objects_carry_side_on_the_pipeline_record_and_on_the_task_read(c):
    _pipeline(c)
    row = X.new_task(c, ANA, "Build the importer", "eng")
    assert [s["side"] for s in PS.read(c, "eng")["stages"]] == [False, True, False]
    _move(c, row["id"], "Rejected", BUILDER)
    assert task_calls.task_view(c, row["id"], ANA)["stage"]["side"] is True


def _stage(name, **fields):
    """One stage of a settings record, with every field a caller sends."""
    return {
        "name": name,
        "purpose": "",
        "owner": BUILDER,
        "by_hand": False,
        "requests_move_on": False,
        "task_limit": None,
        "expected_minutes": None,
        "expected_bots_only": False,
        "on_enter": {},
        "requirements": [],
        **fields,
    }


def _record(*stages):
    return {
        "name": "Side test",
        "goal": "",
        "prefix": "SD",
        "manager_id": MANAGER,
        "owner_id": ANA,
        "private": False,
        "factory_id": None,
        "preset": None,
        "stages": list(stages),
    }


def test_a_side_stage_is_by_hand_and_never_asks_to_move_on_whatever_the_record_says(c):
    record = _record(
        _stage("In progress"),
        _stage("Rejected", side=True, by_hand=False, requests_move_on=True, owner=REVIEWER),
    )
    assert PS.check(c, record) == []
    saved = PS.save(c, ANA, record)
    assert saved["stages"][0]["side"] is False
    rejected = saved["stages"][1]
    assert (rejected["side"], rejected["by_hand"], rejected["requests_move_on"]) == (True, True, False)


def test_the_first_work_stage_of_a_pipeline_cannot_be_a_side_stage(c):
    record = _record(_stage("Rejected", side=True, owner=REVIEWER), _stage("In progress"))
    assert any("cannot be a side stage" in p for p in PS.check(c, record))
    with pytest.raises(H.Refused, match="task_settings"):
        PS.save(c, ANA, record)


def test_a_side_stage_cannot_have_requirements(c):
    record = _record(
        _stage("In progress"),
        _stage("Rejected", side=True, owner=REVIEWER, requirements=[{"kind": "owner_done"}]),
    )
    assert any("A side stage cannot have requirements" in p for p in PS.check(c, record))
    with pytest.raises(H.Refused, match="task_settings"):
        PS.save(c, ANA, record)


def test_the_side_stages_migration_run_twice_changes_nothing(tmp_path):
    path = fresh(tmp_path)
    before = snapshot(path)
    migration = importlib.import_module(MIGRATION)
    with closing(sqlite3.connect(path)) as conn:
        migration.up(conn)
        migration.up(conn)  # again: nothing more changes
        conn.commit()
    assert snapshot(path) == before


def test_never_stuck_never_picks_up_a_task_sitting_in_a_side_stage(c, later):
    _pipeline(c)
    row = X.new_task(c, ANA, "Build the importer", "eng")
    _move(c, row["id"], "Rejected", BUILDER)
    assert later(hours=5) == {}  # by hand: no ladder, no move on, nothing late
    assert later(days=3) == {}
    assert E.task(c, row["id"])["stage"]["name"] == "Rejected"
