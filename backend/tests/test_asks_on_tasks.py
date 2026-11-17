"""Asks on one task, where the old question tasks were: a bot's question is a ❓ on the task's chat
(question_marks.ask), one open ❓ per bot per chat, so asking again replaces it (C15); a person's ask is an OK, with
no such limit; and an ask leaves Needs you with the task it is on, however far up the closing started."""

import pytest

from backend import question_marks as Q
from backend import task_engine as E
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.old_asks import old_answer, old_ask

ANA, BEN = "human:ana", "human:ben"
OPS, CPO = "bot:ops", "bot:cpo"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        for slug in ("ops", "cpo"):
            X.bot(conn, slug)
        yield conn


def _new(c, by=ANA, owner=OPS, title="Plan the offsite", **more):
    return H.task_create(c, by, title, "What to do and how to tell it is done.", owner, lint=False, **more)


def _ask(c, bot, task_id, line, person=ANA):
    """A bot's question to a person: a ❓ on the task's chat."""
    return Q.ask(c, bot, task_id, person, line)


def _ok(c, by, task_id, line, person=ANA):
    """A person's ask of another person: an OK, which needs nothing shown."""
    return E.add_requirement(c, task_id, by, "ok", person, line)


def _questions(c, person=ANA):
    return [(n["task_id"], n["id"]) for n in W.needs(c, person) if n["kind"] == "question"]


def _asks(c, person=ANA):
    return [(n["task_id"], n["requirement_id"]) for n in W.needs(c, person) if n["kind"] == "ask"]


def test_a_bot_asking_again_on_the_same_task_replaces_its_open_ask(c):
    row = _new(c)
    first = _ask(c, OPS, row["id"], "Run the vendor trial once?")
    again = _ask(c, OPS, row["id"], "Run the vendor trial this week or next month?")
    assert Q.get(c, first["id"])["cleared_reason"] == "replaced"
    assert _questions(c) == [(row["id"], Q.wait_id(again))], "one card for Ana, with the latest wording"
    assert [m["line"] for m in Q.on_task(c, row["id"])] == ["Run the vendor trial this week or next month?"]


def test_another_bot_or_another_task_or_a_person_asks_on_its_own(c):
    row = _new(c, by=CPO)  # CPO asked for it, so it may ask on it too
    other = _new(c, title="Book the venue")
    mine = _ask(c, OPS, row["id"], "Which month should the offsite be?")
    theirs = _ask(c, CPO, row["id"], "Which budget should the offsite use?")  # another bot on the task
    elsewhere = _ask(c, OPS, other["id"], "Which venue should we book?")  # the same bot on another task
    hers = _new(c, by=BEN, owner=BEN, title="Plan the dinner")
    first = _ok(c, BEN, hers["id"], "OK hosting the dinner")
    second = _ok(c, BEN, hers["id"], "OK giving the opening talk")  # people have no limit
    assert set(_questions(c)) == {
        (row["id"], Q.wait_id(mine)),
        (row["id"], Q.wait_id(theirs)),
        (other["id"], Q.wait_id(elsewhere)),
    }
    assert set(_asks(c)) == {(hers["id"], first["requirement_id"]), (hers["id"], second["requirement_id"])}


def test_once_answered_the_bots_next_ask_is_a_new_one_and_the_answer_stays_on_record(c):
    row = _new(c)
    first = _ask(c, OPS, row["id"], "Give Builder a backup model?")
    reply = H.task_comment(c, ANA, row["id"], "Yes")
    second = _ask(c, OPS, row["id"], "Give Analyst a backup model too?")
    assert second["id"] != first["id"]
    assert _questions(c) == [(row["id"], Q.wait_id(second))]
    kept = Q.get(c, first["id"])
    assert (kept["cleared_reason"], kept["cleared_by_id"], kept["cleared_message_id"]) == ("replied", ANA, reply["id"])
    assert H.message(c, reply["id"])["body"] == "Yes"


def test_an_ask_leaves_needs_you_when_its_task_or_one_above_it_closes(c):
    top = _new(c)
    mid = _new(c, by=OPS, owner=OPS, title="Book the venue", parent_id=top["id"])
    sub = _new(c, by=OPS, owner=CPO, title="Pick the menu", parent_id=mid["id"])
    other = _new(c, by=OPS, owner=OPS, title="Pick the hotel", parent_id=mid["id"])
    on_sub = _ask(c, CPO, sub["id"], "Which menu should the dinner use?")
    on_other = _ask(c, OPS, other["id"], "Which hotel should we book?")
    before = H.status_counts(c, CPO)
    assert set(_questions(c)) == {(sub["id"], Q.wait_id(on_sub)), (other["id"], Q.wait_id(on_other))}
    H.task_close(c, ANA, sub["id"], "Catering is booked already.")  # its own task closed: the ask goes with it
    assert _questions(c) == [(other["id"], Q.wait_id(on_other))]
    assert H.status_counts(c, CPO)[1] == before[1] - 1, "the asking bot's count follows"
    H.task_close(c, ANA, top["id"], "The offsite is off.")  # the top closes the open tree under it
    assert H.task(c, other["id"])["status"] == "closed"
    assert _questions(c) == []


def test_a_task_with_a_stored_answered_ask_still_lists_the_ask_and_its_answer(c):
    """Asks and answers written before step 6c stay as history: the asks migration leaves answered and stale ones as
    they are, so a task that holds them still lists both, with the ask's question and who answered."""
    row = _new(c)
    ask = old_ask(
        c,
        OPS,
        ANA,
        "@ana Which month should the offsite be?",
        conversation_id=row["conversation_id"],
        refs={"task": row["id"], "questions": ["Which month?"], "who": ANA},
    )
    answer = old_answer(c, ANA, ask, "June, if the venue is free.", refs={"task": row["id"]})
    listed = {m["id"]: m for m in H.task_comments(c, row["id"], actor=ANA)}
    assert listed[ask["id"]]["kind"] == "ask"
    assert listed[ask["id"]]["answered_by_id"] == ANA
    assert listed[ask["id"]]["ask"]["questions"] == ["Which month?"], "the ask's question is shown"
    assert listed[answer["id"]]["kind"] == "answer"
    assert listed[answer["id"]]["body"] == "June, if the venue is free."
