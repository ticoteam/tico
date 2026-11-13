"""Engineering at release (spec "A stage picks its owner from several", AC-12): the migration that sets the Engineering
pipeline's On deck and Build to pick from the first two Builders, and its two Reviews from the first two Reviewers, and
has each open On deck or Build task with no owner picked once.

Synthetic rows only (backend/tests/helpers): the fictional company with its role bots in their seats, a second Builder,
and the Engineering preset as ensure_presets makes it.
"""

import importlib
import json

import pytest

from backend import never_stuck
from backend import pipeline_settings as PS
from backend import stage_pick
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.product_engineering_presets import _answers, _role_bot, _seat, _team

MIGRATION = importlib.import_module("backend.migrations.20261113033639_engineering_picks_at_release")
ANA = X.ANA
ON_DECK, BUILD = "engineering-on-deck", "engineering-build"
REVIEW_1, REVIEW_2 = "engineering-review-1", "engineering-review-2"
FIXES_1, FIXES_2 = "engineering-fixes-1", "engineering-fixes-2"
BUILDERS = ["bot:builder", "bot:builder-2"]
REVIEWERS = ["bot:rev-a", "bot:rev-b"]  # by seat title: Reviewer 1, then Reviewer 2


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def _engineering(c, builders=2):
    """The role bots seated, `builders` Builders, and the Engineering preset made from them (settings version 1)."""
    _team(c)
    if builders > 1:
        _role_bot(c, "builder-2", "builder")
        _seat(c, "Builder 2", "builder", "builder-2")
    _answers(c)
    PS.ensure_presets(c)
    assert E.stage(c, BUILD)["owner"] == "bot:builder"


def _stage(c, sid):
    row = c.execute("SELECT owner, pick FROM stages WHERE id=?", (sid,)).fetchone()
    return row[0], (json.loads(row[1]) if row[1] else None)


def _version(c):
    return c.execute("SELECT settings_version FROM pipelines WHERE id='engineering'").fetchone()[0]


def _file(c, title, owner=None):
    made = H.task_create(
        c, ANA, title, "What to do.", owner or ANA, pipeline="engineering", lint=False, unassigned=owner is None
    )
    return made["id"]


def _ownerless(c, task_id, stage_id):
    """Make the task an open one with no owner in `stage_id`, as a task filed before release can be."""
    c.execute("UPDATE task_move_guards SET open=1, task_id=? WHERE id=1", (task_id,))
    c.execute("UPDATE tasks SET stage_id=?, owner_id=NULL, assignee_id=NULL WHERE id=?", (stage_id, task_id))
    c.execute("UPDATE task_move_guards SET open=0, task_id=NULL WHERE id=1")
    c.execute(
        "UPDATE visits SET stage_id=?, pick_state=NULL, pick_why=NULL WHERE task_id=? AND left_at IS NULL",
        (stage_id, task_id),
    )


def _snapshot(c):
    return (
        c.execute("SELECT id, owner, pick, updated_at FROM stages ORDER BY id").fetchall(),
        c.execute("SELECT id, settings_version, updated_at FROM pipelines ORDER BY id").fetchall(),
        c.execute("SELECT id, pick_state, pick_why FROM visits ORDER BY id").fetchall(),
        c.execute("SELECT id, owner_id, assignee_id, version FROM tasks ORDER BY id").fetchall(),
        c.execute("SELECT COUNT(*) FROM events").fetchone(),
    )


def test_engineering_picks_from_two_builders_and_two_reviewers_and_fixes_stay_as_they_are(c):
    _engineering(c)
    fixes = (_stage(c, FIXES_1), _stage(c, FIXES_2))
    MIGRATION.up(c)
    build = {"bots": BUILDERS, "how": "turns", "limit_each": None, "prefer": "anyone", "owns_task": True}
    review = {"bots": REVIEWERS, "how": "turns", "limit_each": None, "prefer": "new", "owns_task": False}
    assert _stage(c, ON_DECK) == ("pick", build)
    assert _stage(c, BUILD) == ("pick", build)
    assert _stage(c, REVIEW_1) == ("pick", review)
    assert _stage(c, REVIEW_2) == ("pick", review)
    assert (_stage(c, FIXES_1), _stage(c, FIXES_2)) == fixes
    assert stage_pick.config(E.stage(c, BUILD)) == stage_pick.Pick(tuple(BUILDERS), owns_task=True)
    # An untouched preset stays at version 1, so a role bot that appears later is still filled in (PS._fill).
    assert _version(c) == 1
    saved = c.execute(
        "SELECT detail FROM events WHERE action='pipeline.save' AND target='engineering' "
        "AND json_extract(detail, '$.stages') IS NOT NULL"
    ).fetchall()
    assert len(saved) == 1 and sorted(json.loads(saved[0][0])["stages"]) == [BUILD, ON_DECK, REVIEW_1, REVIEW_2]


def test_a_pipeline_someone_changed_gets_a_new_settings_version(c):
    _engineering(c)
    c.execute("UPDATE pipelines SET settings_version=4 WHERE id='engineering'")
    MIGRATION.up(c)
    assert _version(c) == 5


def test_an_ownerless_on_deck_task_is_picked_once_and_owned_tasks_are_untouched(c):
    _engineering(c)
    waiting = _file(c, "Nobody has it")
    owned = _file(c, "Builder has it", owner="bot:builder")
    in_fixes = _file(c, "In Fixes 1 with nobody")
    _ownerless(c, waiting, ON_DECK)
    _ownerless(c, in_fixes, FIXES_1)
    before_owned = E.task(c, owned)
    MIGRATION.up(c)
    assert E.task(c, waiting)["visit"]["pick_state"] == "full"
    assert E.task(c, in_fixes)["visit"]["pick_state"] is None
    after_owned = E.task(c, owned)
    assert after_owned["visit"]["pick_state"] is None
    assert (after_owned["owner_id"], after_owned["version"]) == (before_owned["owner_id"], before_owned["version"])
    never_stuck.check(c)
    picked = E.task(c, waiting)
    assert picked["owner_id"] in BUILDERS and picked["assignee_id"] == picked["owner_id"]
    assert picked["visit"]["pick_state"] is None
    lines = c.execute(
        "SELECT COUNT(*) FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='pick'", (waiting,)
    ).fetchone()[0]
    assert lines == 1
    assert E.task(c, owned)["owner_id"] == "bot:builder"
    assert E.task(c, in_fixes)["owner_id"] is None
    # Picked once: another pass gives it to nobody else.
    never_stuck.check(c)
    assert E.task(c, waiting)["owner_id"] == picked["owner_id"]


def test_running_it_again_changes_nothing(c):
    _engineering(c)
    _ownerless(c, _file(c, "Nobody has it"), ON_DECK)
    MIGRATION.up(c)
    once = _snapshot(c)
    MIGRATION.up(c)
    assert _snapshot(c) == once


def test_a_stage_without_two_such_bots_is_left_alone(c):
    _engineering(c, builders=1)
    waiting = _file(c, "Nobody has it")
    _ownerless(c, waiting, ON_DECK)
    MIGRATION.up(c)
    assert _stage(c, ON_DECK) == ("bot:builder", None)
    assert _stage(c, BUILD) == ("bot:builder", None)
    assert _stage(c, REVIEW_1)[0] == "pick"
    assert E.task(c, waiting)["visit"]["pick_state"] is None


def test_a_stage_a_person_already_set_to_pick_is_kept(c):
    _engineering(c)
    mine = {"bots": ["bot:builder-2"], "how": "random", "limit_each": 2, "prefer": "anyone", "owns_task": True}
    c.execute("UPDATE stages SET owner='pick', pick=? WHERE id=?", (json.dumps(mine), BUILD))
    MIGRATION.up(c)
    assert _stage(c, BUILD) == ("pick", mine)
    assert _stage(c, ON_DECK)[0] == "pick"


def test_a_database_without_the_tables_is_skipped(tmp_path):
    import sqlite3

    conn = sqlite3.connect(tmp_path / "empty.db")
    MIGRATION.up(conn)
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0


def test_an_ownerless_build_task_is_picked_once_too(c):
    _engineering(c)
    waiting = _file(c, "Nobody has it in Build")
    _ownerless(c, waiting, BUILD)
    MIGRATION.up(c)
    assert E.task(c, waiting)["visit"]["pick_state"] == "full"
    never_stuck.check(c)
    picked = E.task(c, waiting)
    assert picked["stage_id"] == BUILD
    assert picked["owner_id"] in BUILDERS and picked["assignee_id"] == picked["owner_id"]
    assert picked["visit"]["pick_state"] is None
