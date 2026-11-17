"""Every writer asks a question as a message marked ❓ (backend/question_marks.py): the engine refuses a question ask, a
bot's OK shows what it approves, and the migration takes back the questions still open. Synthetic rows only: the
fictional company's people and bots (backend/tests/helpers/engine.py)."""

import importlib
import json

import pytest

from backend import question_marks as Q
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
BEN = "human:ben"
BUILDER = "bot:builder"
MIGRATION = importlib.import_module("backend.migrations.20261117062043_withdraw_open_questions")


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


@pytest.fixture
def general(c):
    for slug in ("builder", "reviewer", "manager"):
        X.bot(c, slug)
    return X.pipeline(c, manager="bot:manager")


def _seen(c, task_id):
    return E.task(c, task_id)["version"]


def test_a_question_is_refused_as_an_ask_and_an_ok_is_the_only_kind(c, general):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    with pytest.raises(H.Refused, match="tico task ask"):
        E.add_requirement(c, task_id, BUILDER, "answer", ANA, "Which tone?", seen_version=_seen(c, task_id))
    with pytest.raises(H.Refused, match="Only an OK"):
        E.add_requirement(c, task_id, ANA, "page", BEN, "Look at this", seen_version=_seen(c, task_id))
    assert c.execute("SELECT count(*) FROM requirements WHERE visit_id IS NOT NULL").fetchone()[0] == 0


def test_a_bots_ok_shows_what_it_approves_and_a_persons_need_not(c, general):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    with pytest.raises(H.Refused, match="--show"):
        E.add_requirement(c, task_id, BUILDER, "ok", ANA, "OK the note?", seen_version=_seen(c, task_id))
    rid = E.add_requirement(c, task_id, ANA, "ok", BEN, "OK the note", seen_version=_seen(c, task_id))["requirement_id"]
    row = c.execute("SELECT kind, person_id FROM requirements WHERE id=?", (rid,)).fetchone()
    assert (row[0], row[1]) == ("ok", BEN)


def _old_question(c, task_id, by=BUILDER, person=ANA):
    """A question the older engine added: a `requirements` row of kind `answer` on the task's visit."""
    visit = E.task(c, task_id)["visit"]["id"]
    rid = H.new_id()
    c.execute(
        "INSERT INTO requirements (id, visit_id, kind, who_oks, person_id, added_by_id, by_bot, line, choices, settings, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            rid,
            visit,
            "answer",
            "people_with_exceptions",
            person,
            by,
            1,
            "Which tone?",
            json.dumps(["A", "B"]),
            "{}",
            H.now(),
        ),
    )
    c.execute("UPDATE visits SET level='person', tries=3, person_id=?, next_try_at=NULL WHERE id=?", (person, visit))
    return rid, visit


def test_the_migration_takes_back_each_open_question_and_starts_its_ladder_again_from_the_owner(c, general):
    asked = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    open_rid, visit = _old_question(c, asked)
    answered = X.new_task(c, ANA, "Pick the colour", assignee=BUILDER)
    done_rid, done_visit = _old_question(c, answered["id"])
    X.answer(c, answered, done_rid, "A", ANA)
    closed = X.new_task(c, ANA, "Order the paper", assignee=BUILDER)["id"]
    closed_rid, _ = _old_question(c, closed)
    H.task_close(c, ANA, closed, "Not needed.", _seen(c, closed))

    MIGRATION.up(c)

    def withdrawn(rid):
        return c.execute("SELECT withdrawn_at FROM requirements WHERE id=?", (rid,)).fetchone()[0]

    assert withdrawn(open_rid) and not withdrawn(done_rid)
    note = c.execute(
        "SELECT actor_id, old, note FROM history_events WHERE subject_id=? AND kind='requirement_withdrawn'", (asked,)
    ).fetchall()
    assert [(row[0], row[1], row[2]) for row in note] == [("keeper", open_rid, MIGRATION.NOTE)]
    took = "SELECT count(*) FROM history_events WHERE subject_id=? AND note=?"
    assert c.execute(took, (closed, MIGRATION.NOTE)).fetchone()[0] == 0
    reset = c.execute("SELECT level, tries, person_id, next_try_at FROM visits WHERE id=?", (visit,)).fetchone()
    assert (reset[0], reset[1], reset[2]) == ("owner", 0, None) and reset[3]
    kept = c.execute("SELECT level, person_id FROM visits WHERE id=?", (done_visit,)).fetchone()
    assert (kept[0], kept[1]) == ("person", ANA)

    before = c.execute("SELECT count(*) FROM history_events").fetchone()[0]
    MIGRATION.up(c)
    assert c.execute("SELECT count(*) FROM history_events").fetchone()[0] == before, "a second run changes nothing"
    assert Q.on_task(c, asked) == []
