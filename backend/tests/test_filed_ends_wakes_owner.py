"""A bot's task waits on a person's OK and on a review sub-task it filed for another bot (Tico Team, Nov 12: a spec's
review sub-task finished and the spec's owner was never woken). While the sub-task is open the person is not asked for
the OK on what the review is about; when it ends, the parent's owner bot is woken on the parent with how it ended, and
that wake's run may start though the task now waits on the person."""

import pytest

from backend import task_engine as E
from backend import task_wakes
from backend import waiting_on as W
from backend.tests.helpers import engine as X

ANA = X.ANA
SPEC = ({"name": "Spec", "owner": "bot:owner", "requirements": [{"kind": "ok", "person": ANA, "line": "OK the spec"}]},)


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        for slug in ("owner", "reviewer", "manager"):
            X.bot(conn, slug)
        X.pipeline(conn, manager="bot:manager")
        X.pipeline(conn, "spec", "Specs", SPEC, manager="bot:manager")
        yield conn


def filed(c):
    """Bot A's spec in Spec, and the review sub-task A filed for bot B under it in its run on the spec."""
    spec = X.new_task(c, ANA, "Stage rules spec", pipeline_id="spec")
    assert spec["owner_id"] == "bot:owner"
    X.ran(c, spec["id"], "bot:owner")
    review = X.new_task(c, "bot:owner", "Review the stage rules spec", assignee="bot:reviewer", parent=spec["id"])
    X.settled(c)
    return spec, review


def _states(c, task_id):
    return [(w.state, w.who) for w in W.waiting_on(c, task_id)]


def test_the_person_is_not_asked_for_the_ok_while_the_review_it_waits_on_is_open(c):
    spec, review = filed(c)
    assert _states(c, spec["id"]) == [("tasks", review["id"])]
    assert ANA not in {wait.who for wait in W.waiting_on(c, spec["id"])}


@pytest.mark.parametrize("outcome", [E.DONE, E.CLOSED])
def test_bot_b_finishing_the_review_wakes_bot_a_on_the_parent(c, outcome):
    spec, review = filed(c)
    review = E.task(c, review["id"])
    if outcome == E.DONE:
        X.say_done(c, review)
        E.advance(c, review["id"])
    else:
        E.move(c, review["id"], E.CLOSED, "bot:reviewer", "Nothing to add", seen_version=review["version"])
    assert E.task(c, review["id"])["status"] == outcome
    woke = c.execute(
        "SELECT m.id, m.body FROM messages m JOIN jobs j ON j.message_id = m.id "
        "WHERE j.state='queued' AND m.recipient_id='bot:owner' AND m.wake_key LIKE ?",
        (f"{spec['id']}:%:filed:%",),
    ).fetchall()
    assert len(woke) == 1, woke
    words = "Done" if outcome == E.DONE else "Closed"
    assert f"Review the stage rules spec: {words}" in woke[0][1]
    # The OK is the person's again, and the owner's wake about what it filed still gets its run.
    assert _states(c, spec["id"]) == [("person", ANA)]
    assert task_wakes.may_start(c, woke[0][0])


def test_a_parent_a_person_snoozed_is_not_woken_and_the_log_says_why(c, caplog):
    spec, review = filed(c)
    E.snooze(c, spec["id"], ANA, "Later")
    review = E.task(c, review["id"])
    with caplog.at_level("INFO", logger="tico.task_engine"):
        E.move(c, review["id"], E.CLOSED, "bot:reviewer", "Nothing to add", seen_version=review["version"])
    assert not [key for key, _ in X.wakes(c, spec["id"], "bot:owner") if f":{task_wakes.FILED}:" in key]
    assert f"parent wake skipped: snoozed parent={spec['id']} filed={review['id']}" in caplog.text
