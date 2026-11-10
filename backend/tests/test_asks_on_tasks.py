"""Asks added to one task (H.task_ask), where the old question tasks were: a bot has one open ask of its own on a task
and asking again replaces it (C15), people have no such limit, and an ask leaves Needs you with the task it is on,
however far up the closing started."""

import pytest

from backend import task_requirements as R
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X

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


def _ask(c, by, task_id, line, person=ANA, **more):
    return H.task_ask(c, by, task_id, person, line, seen_version=H.task(c, task_id)["version"], **more)


def _asks(c, person=ANA):
    return [(n["task_id"], n["requirement_id"]) for n in W.needs(c, person) if n["kind"] == "ask"]


def test_a_bot_asking_again_on_the_same_task_replaces_its_open_ask(c):
    row = _new(c)
    first = _ask(c, OPS, row["id"], "Run the vendor trial once?")
    again = _ask(c, OPS, row["id"], "Run the vendor trial this week?", choices=["This week", "Next month"])
    assert R.get(c, first["requirement_id"])["withdrawn_at"]
    assert _asks(c) == [(row["id"], again["requirement_id"])], "one card for Ana, with the latest wording"
    shown = [r for r in H.task_view(c, row["id"], ANA)["to_leave"] if r["kind"] == "answer"]
    assert [(r["line"], r["choices"]) for r in shown] == [
        ("Run the vendor trial this week?", ["This week", "Next month"])
    ]


def test_another_bot_or_another_task_or_a_person_asks_on_its_own(c):
    row = _new(c, by=CPO)  # CPO asked for it, so it may ask on it too
    other = _new(c, title="Book the venue")
    mine = _ask(c, OPS, row["id"], "Which month should the offsite be?")
    theirs = _ask(c, CPO, row["id"], "Which budget should the offsite use?")  # another bot on the task
    elsewhere = _ask(c, OPS, other["id"], "Which venue should we book?")  # the same bot on another task
    hers = _new(c, by=BEN, owner=BEN, title="Plan the dinner")
    first = _ask(c, BEN, hers["id"], "Can you host the dinner?")
    second = _ask(c, BEN, hers["id"], "Can you give the opening talk?")  # people have no limit
    assert set(_asks(c)) == {
        (row["id"], mine["requirement_id"]),
        (row["id"], theirs["requirement_id"]),
        (other["id"], elsewhere["requirement_id"]),
        (hers["id"], first["requirement_id"]),
        (hers["id"], second["requirement_id"]),
    }


def test_once_answered_the_bots_next_ask_is_a_new_one_and_the_answer_stays_on_record(c):
    row = _new(c)
    first = _ask(c, OPS, row["id"], "Give Builder a backup model?", choices=["Yes", "No"])
    H.task_answer(c, ANA, row["id"], first["requirement_id"], "Yes")
    second = _ask(c, OPS, row["id"], "Give Analyst a backup model too?", choices=["Yes", "No"])
    assert second["requirement_id"] != first["requirement_id"]
    assert _asks(c) == [(row["id"], second["requirement_id"])]
    kept = c.execute(
        "SELECT by_id, answer FROM answers WHERE requirement_id=? AND undone_at IS NULL", (first["requirement_id"],)
    ).fetchall()
    assert [tuple(k) for k in kept] == [(ANA, "Yes")]


def test_an_ask_leaves_needs_you_when_its_task_or_one_above_it_closes(c):
    top = _new(c)
    mid = _new(c, by=OPS, owner=OPS, title="Book the venue", parent_id=top["id"])
    sub = _new(c, by=OPS, owner=CPO, title="Pick the menu", parent_id=mid["id"])
    other = _new(c, by=OPS, owner=OPS, title="Pick the hotel", parent_id=mid["id"])
    on_sub = _ask(c, CPO, sub["id"], "Which menu should the dinner use?")
    on_other = _ask(c, OPS, other["id"], "Which hotel should we book?")
    before = H.status_counts(c, CPO)
    assert set(_asks(c)) == {(sub["id"], on_sub["requirement_id"]), (other["id"], on_other["requirement_id"])}
    H.task_close(c, ANA, sub["id"], "Catering is booked already.")  # its own task closed: the ask goes with it
    assert _asks(c) == [(other["id"], on_other["requirement_id"])]
    assert H.status_counts(c, CPO)[1] == before[1] - 1, "the asking bot's count follows"
    H.task_close(c, ANA, top["id"], "The offsite is off.")  # the top closes the open tree under it
    assert H.task(c, other["id"])["status"] == "closed"
    assert _asks(c) == []
