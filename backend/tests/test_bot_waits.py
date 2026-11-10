"""A task waits on another bot's work through a sub-task it filed (docs/pipelines-v3.md, "A task waits a day on a
sub-task"): while the sub-task is open the parent's bot is not woken and the parent names what it waits on; when the
sub-task ends, the parent's owner is woken once with how it ended. There is no Blocked and no `waiting_on` field: a
task waits on a person, a task it filed, or room, and a bot asks a bot in a DM."""

import pytest

from backend import task_engine as E
from backend import waiting_on as W
from backend.tests.helpers import engine as X

ANA = X.ANA


@pytest.fixture
def c(company):
    """The builder builds, the reviewer does the prerequisite, in General."""
    with company.transaction() as conn:
        for slug in ("builder", "reviewer", "manager"):
            X.bot(conn, slug)
        X.pipeline(conn, manager="bot:manager")
        yield conn


def parked(c, tag=""):
    """The builder's task and the sub-task it filed for the reviewer in its run on the task (`tag` keeps titles
    apart)."""
    task = X.new_task(c, ANA, "Ship the receipt gate" + tag, assignee="bot:builder")
    X.ran(c, task["id"], "bot:builder")
    pre = X.new_task(c, "bot:builder", "Review the receipt gate fix" + tag, assignee="bot:reviewer", parent=task["id"])
    return task, pre


def _states(c, task_id):
    return [(w.state, w.who) for w in W.waiting_on(c, task_id)]


def test_a_sub_task_another_bot_owns_holds_the_task_and_names_that_bot(c):
    task, pre = parked(c)
    assert _states(c, task["id"]) == [("tasks", pre["id"])]  # nothing stored: what it waits on is derived
    assert _states(c, pre["id"]) == [("bot", "bot:reviewer")]
    # Held while it is open: nothing starts the builder on it.
    assert W.turn(c, E.task(c, task["id"]), W.of(c, E.task(c, task["id"]))) is None
    queued = c.execute(
        "SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued' AND m.wake_key LIKE ?",
        (task["id"] + ":%",),
    ).fetchone()[0]
    assert queued == 0


def test_the_sub_task_ending_wakes_the_owner_once_with_how_it_ended(c):
    for outcome, words in [(E.DONE, "Done"), (E.CLOSED, "Closed")]:
        task, pre = parked(c, tag=f" ({outcome})")
        X.settled(c)
        pre = E.task(c, pre["id"])
        if outcome == E.DONE:
            X.say_done(c, pre)
            E.advance(c, pre["id"])
        else:
            E.move(c, pre["id"], E.CLOSED, "bot:reviewer", "Not needed after all", seen_version=pre["version"])
        woke = [key for key, _ in X.wakes(c, task["id"], "bot:builder") if ":filed:" in key]
        assert len(woke) == 1, woke
        body = c.execute(
            "SELECT m.body FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued' AND m.wake_key=?",
            (woke[0],),
        ).fetchone()[0]
        assert f"Review the receipt gate fix ({outcome}): {words}" in body
        assert _states(c, task["id"]) == [("bot", "bot:builder")]  # eligible again: nothing holds it
