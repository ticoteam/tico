"""Every writer asks a question as a message marked ❓ (backend/question_marks.py): the engine refuses a question ask, a
bot's OK shows what it approves, and a question still open from before becomes a ❓ (backend/old_questions.py). Synthetic rows only: the
fictional company's people and bots (backend/tests/helpers/engine.py)."""

import json

import pytest

from backend import never_stuck as NS
from backend import old_questions as OQ
from backend import question_marks as Q
from backend import task_engine as E
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
BEN = "human:ben"
BUILDER = "bot:builder"


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


def _queued(c, task_id):
    return c.execute(
        "SELECT j.bot_id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued' "
        "AND json_extract(m.refs, '$.task')=?",
        (task_id,),
    ).fetchall()


def _withdrawn(c, rid):
    return c.execute("SELECT withdrawn_at FROM requirements WHERE id=?", (rid,)).fetchone()[0]


def test_an_open_question_from_before_becomes_the_bots_question_mark_and_the_rest_stay(c, general):
    asked = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    open_rid, visit = _old_question(c, asked)
    answered = X.new_task(c, ANA, "Pick the colour", assignee=BUILDER)
    done_rid, _ = _old_question(c, answered["id"])
    X.answer(c, answered, done_rid, "A", ANA)
    closed = X.new_task(c, ANA, "Order the paper", assignee=BUILDER)["id"]
    _old_question(c, closed)
    H.task_close(c, ANA, closed, "Not needed.", _seen(c, closed))

    assert OQ.carry_over(c) == {"marked": 1, "taken_back": 0}

    assert _withdrawn(c, open_rid) and not _withdrawn(c, done_rid)
    took = "SELECT count(*) FROM history_events WHERE subject_id=? AND note IN (?, ?)"
    assert c.execute(took, (closed, OQ.NOTE, OQ.NOTE_AGAIN)).fetchone()[0] == 0 and Q.on_task(c, closed) == []
    [mark] = Q.on_task(c, asked)
    assert (mark["bot_id"], mark["person_id"], mark["line"]) == (BUILDER, ANA, "Which tone?")
    said = c.execute("SELECT sender_id, body FROM messages WHERE id=?", (mark["message_id"],)).fetchone()
    assert (said[0], said[1]) == (BUILDER, "Which tone?\n- A\n- B")
    note = c.execute(
        "SELECT actor_id, old, note FROM history_events WHERE subject_id=? AND kind='requirement_withdrawn'", (asked,)
    ).fetchall()
    assert [(row[0], row[1], row[2]) for row in note] == [(H.KEEPER, open_rid, OQ.NOTE)]
    assert [n["kind"] for n in W.needs(c, ANA) if n.get("task_id") == asked] == ["question"]

    before = c.execute("SELECT count(*) FROM history_events").fetchone()[0]
    assert OQ.carry_over(c) == {"marked": 0, "taken_back": 0}, "a second run changes nothing"
    assert c.execute("SELECT count(*) FROM history_events").fetchone()[0] == before


def test_a_question_carried_over_on_a_by_hand_stage_or_under_the_bots_snooze_is_still_answered(c, general):
    # Neither a by-hand stage nor the bot's own snooze runs the ladder: the ❓ in Needs you is how it goes on, and the
    # reply wakes the bot.
    for slug in ("builder", "manager"):
        X.bot(c, slug)
    X.pipeline(
        c,
        pid="manual",
        name="Manual review",
        manager="bot:manager",
        stages=[{"name": "Review", "owner": "assignee", "by_hand": True, "requirements": [{"kind": "owner_done"}]}],
    )
    manual = X.new_task(c, ANA, "Choose what to review", pipeline_id="manual", assignee=BUILDER)["id"]
    _old_question(c, manual)
    snoozed = X.new_task(c, ANA, "Wait for the answer", assignee=BUILDER)["id"]
    _old_question(c, snoozed)
    E.snooze(c, snoozed, BUILDER, "Waiting for Ana", None, seen_version=_seen(c, snoozed))
    X.settled(c)
    assert OQ.carry_over(c) == {"marked": 2, "taken_back": 0}
    NS.check(c, now=H.shift(H.now(), days=365))
    for task_id in (manual, snoozed):
        assert [n["kind"] for n in W.needs(c, ANA) if n.get("task_id") == task_id] == ["question"], task_id
        H.task_comment(c, ANA, task_id, "Tone A.")
        assert Q.on_task(c, task_id) == [], task_id
        assert ("builder",) in [tuple(r) for r in _queued(c, task_id)], task_id
    assert not E.task(c, snoozed).get("snooze"), "the reply ends the bot's own snooze"


def test_a_question_that_cannot_be_a_question_mark_is_taken_back_and_its_bot_woken_whatever_holds_it(c, general):
    X.pipeline(
        c,
        pid="manual",
        name="Manual review",
        manager="bot:manager",
        stages=[{"name": "Review", "owner": "assignee", "by_hand": True, "requirements": [{"kind": "owner_done"}]}],
    )
    manual = X.new_task(c, ANA, "Choose what to review", pipeline_id="manual", assignee=BUILDER)["id"]
    rid, _ = _old_question(c, manual, person="bot:reviewer")  # a bot was asked: a ❓ is for a person
    by_person = X.new_task(c, ANA, "Plan the launch", assignee=BUILDER)["id"]
    person_rid, _ = _old_question(c, by_person, by=BEN)
    snoozed = X.new_task(c, ANA, "Wait for the reviewer", assignee=BUILDER)["id"]
    _old_question(c, snoozed, person="bot:reviewer")
    E.snooze(c, snoozed, BUILDER, "Waiting for the reviewer", None, seen_version=_seen(c, snoozed))
    X.settled(c)
    assert OQ.carry_over(c) == {"marked": 0, "taken_back": 3}
    assert [tuple(r) for r in _queued(c, snoozed)] == [("builder",)], "the bot's own snooze does not hold the wake"
    assert _withdrawn(c, rid) and _withdrawn(c, person_rid)
    assert Q.on_task(c, manual) == [] and [tuple(r) for r in _queued(c, manual)] == [("builder",)]
    assert _queued(c, by_person) == [], "a person's own question has no bot to ask again"
    note = "SELECT note FROM history_events WHERE subject_id=? AND kind='requirement_withdrawn'"
    assert c.execute(note, (manual,)).fetchone()[0] == OQ.NOTE_AGAIN


def test_the_open_questions_read_their_answers_by_visit(c, general):
    plan = " ".join(str(row[-1]) for row in c.execute("EXPLAIN QUERY PLAN " + OQ.OPEN))
    assert "answers_visit" in plan and "SCAN a" not in plan, plan
