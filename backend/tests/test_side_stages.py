"""Side stages (_brief-side-stages.md, "Spec" and "Contract"): a work stage with `side` sits right after the main stage it
belongs to. The main path skips it, tasks move in and out of it by hand as plain moves, reopen and came_from know it,
and pipeline settings check it.

Synthetic rows only (backend/tests/helpers/engine.py): an Engineering pipeline with side stages, a builder, a reviewer
and a manager bot, and Ana, who files the tasks.
"""

import importlib
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from backend import migrations, rules
from backend import never_stuck as NS
from backend import pipeline_settings as PS
from backend import stage_rules as SR
from backend import task_calls
from backend import task_engine as E
from backend import task_requirements as R
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
BUILDER, REVIEWER, MANAGER = "bot:builder", "bot:reviewer", "bot:manager"
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)
MIGRATION = "20261117205926_side_stages"

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


def test_an_enters_block_rule_refuses_the_proposed_owner_in_both_directions(c):
    pipe = _pipeline(c)
    rejected, qa = (pipe["stages"][name] for name in ("Rejected", "Bot QA"))
    rule = SR.add(
        c,
        ANA,
        {
            "name": "A person holds this stage",
            "pipeline_id": pipe["id"],
            "stages": [rejected, qa],
            "event": "enters",
            "cond": {"owner_kind": "bot"},
            "then": "block",
        },
    )
    task_id = X.new_task(c, ANA, "Build the importer", "eng")["id"]
    for destination in (rejected, qa):
        moved = _move(c, task_id, destination, ANA)
        assert moved["stage_id"] == destination and moved["owner_id"] is None
    logged = c.execute(
        "SELECT new FROM history_events WHERE subject_id=? AND kind='stage_rule' ORDER BY rowid", (task_id,)
    ).fetchall()
    assert [row[0] for row in logged] == [rule["id"], rule["id"]]


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


def test_done_and_advance_leave_a_side_stage_in_place_but_a_rule_can_move_it(c):
    _pipeline(c)
    task_id = X.new_task(c, ANA, "Build the importer", "eng")["id"]
    held = _move(c, task_id, "Rejected", BUILDER)

    assert H.task_done(c, H.KEEPER, task_id) == held
    assert E.advance(c, task_id) is None
    assert E.task(c, task_id) == held

    rules.TASK_RUNNERS["move"](c, task_id, {"stage": "next"}, "Rule: send to QA")
    assert E.task(c, task_id)["stage"]["name"] == "Bot QA"


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


def test_the_side_stages_migration_preserves_existing_visits_and_schema_objects(tmp_path):
    directory = tmp_path / "migrations"
    directory.mkdir()
    for migration in migrations.files():
        if migration.version < MIGRATION:
            shutil.copy(migration.path, directory)
    for name in (migrations.BASELINE, migrations.RETIRED_LIST):
        shutil.copy(migrations.DIRECTORY / name, directory)
    path = tmp_path / "hub.sqlite"
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        migrations.apply(conn, directory)
        conn.execute("PRAGMA foreign_keys=ON")
        assert "side" not in [row[1] for row in conn.execute("PRAGMA table_info(stages)")]
        pipe = X.pipeline(conn, "upgrade", "Upgrade", ({"name": "In progress"}, {"name": "QA"}))
        conn.execute("INSERT INTO tasks (id, title) VALUES ('t1', 'Send the invoice')")
        conn.execute(
            "INSERT INTO visits (rowid, id, task_id, stage_id, entered_at, entered_how, left_at, how) "
            "VALUES (10, 'v1', 't1', ?, '2026-11-16', 'new', '2026-11-17', 'met'), "
            "(20, 'v2', 't1', ?, '2026-11-17', 'met', NULL, NULL)",
            (pipe["stages"]["In progress"], pipe["stages"]["QA"]),
        )
        conn.execute(
            "INSERT INTO requirements (id, visit_id, kind, created_at) VALUES ('r1', 'v1', 'ok', '2026-11-16')"
        )
        conn.execute("CREATE VIEW open_visits AS SELECT id FROM visits WHERE left_at IS NULL")
        visit_sql = "SELECT rowid, * FROM visits ORDER BY rowid"
        objects_sql = (
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE type IN ('trigger', 'view') OR (type='index' AND tbl_name='visits') ORDER BY type, name"
        )
        visits = conn.execute(visit_sql).fetchall()
        objects = conn.execute(objects_sql).fetchall()

        shutil.copy(migrations.DIRECTORY / f"{MIGRATION}.py", directory)
        migrations.apply(conn, directory)
        assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert conn.execute("PRAGMA foreign_key_check(requirements)").fetchall() == []
        assert conn.execute("SELECT DISTINCT side FROM stages").fetchall() == [(0,)]
        assert conn.execute(visit_sql).fetchall() == visits
        assert conn.execute(objects_sql).fetchall() == objects
        assert conn.execute("SELECT id FROM open_visits").fetchall() == [("v2",)]

        conn.execute("UPDATE visits SET left_at='2026-11-18', how='side' WHERE id='v2'")
        conn.execute(
            "INSERT INTO visits (id, task_id, stage_id, entered_at, entered_how) VALUES ('v3', 't1', ?, '2026-11-18', 'side')",
            (pipe["stages"]["In progress"],),
        )
        assert conn.execute("SELECT last_change_attempt FROM visits WHERE id='v3'").fetchone() == (0,)
        assert conn.execute("SELECT id FROM open_visits").fetchall() == [("v3",)]

        visits = conn.execute(visit_sql).fetchall()
        schema_version = conn.execute("PRAGMA schema_version").fetchone()
        changes = conn.total_changes
        importlib.import_module(f"backend.migrations.{MIGRATION}").up(conn)
        assert conn.execute("PRAGMA schema_version").fetchone() == schema_version
        assert conn.total_changes == changes
        assert conn.execute(visit_sql).fetchall() == visits


def test_never_stuck_never_picks_up_a_task_sitting_in_a_side_stage(c, later):
    _pipeline(c)
    row = X.new_task(c, ANA, "Build the importer", "eng")
    _move(c, row["id"], "Rejected", BUILDER)
    assert later(hours=5) == {}  # by hand: no ladder, no move on, nothing late
    assert later(days=3) == {}
    assert E.task(c, row["id"])["stage"]["name"] == "Rejected"
