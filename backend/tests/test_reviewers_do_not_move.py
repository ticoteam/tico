"""Reviewers and Security give a verdict and never move a Dev ticket; QA, people and the Builder still do."""
import pytest

from backend.hubdb import Refused
from backend.store import H
from backend.tests.test_api import api  # noqa: F401


def ticket_with_checks(c):
    typ = H.type_create(c, H.KEEPER, "Dev tickets", [
        {"name": "In Progress", "status": "doing"}, {"name": "Review 1", "status": "review"},
        {"name": "QA Fixes", "status": "doing"}, {"name": "Done", "status": "done"}], bots="work")
    ticket = H.task_create(c, "human:ana", "Improve messages", "Build.", "bot:cpo", type=typ["id"],
                           step="In Progress", lint=False)
    review = H.task_create(c, "bot:cpo", "Look over messages", "Review.", "bot:finance", parent_id=ticket["id"], lint=False)
    # Named as the gate names it after the fact: filing a Review 1 child needs a linked pull request.
    c.execute("UPDATE tasks SET title='Review 1: messages' WHERE id=?", (review["id"],))
    security = H.task_create(c, "bot:cpo", "Security check of messages", "Check.", "bot:inbox", parent_id=ticket["id"], lint=False)
    qa = H.task_create(c, "bot:cpo", "QA: messages", "Test.", "bot:ops", parent_id=ticket["id"], lint=False)
    return ticket, review, security, qa


@pytest.mark.parametrize("who", ["bot:finance", "bot:inbox"])
def test_a_reviewer_or_security_cannot_move_the_ticket_and_is_told_to_give_a_verdict(api, who):
    with api.app.state.store.transaction() as c:
        ticket, review, security, _ = ticket_with_checks(c)
        for kwargs in ({"step": "Review 1"}, {"status": "done"}):
            with pytest.raises(Refused, match="Finish your review task"):
                H.task_update(c, who, ticket["id"], **kwargs)
        assert H.task(c, ticket["id"])["status"] == "doing"
        H.task_update(c, who, review["id"] if who == "bot:finance" else security["id"], status="done", note="Fine.")


def test_qa_the_builder_and_people_still_move_the_ticket(api):
    with api.app.state.store.transaction() as c:
        ticket, _, _, _ = ticket_with_checks(c)
        H.task_update(c, "bot:ops", ticket["id"], step="QA Fixes")
        H.task_update(c, "bot:cpo", ticket["id"], step="In Progress")
        H.task_update(c, "human:ana", ticket["id"], step="Review 1")
        assert H.task(c, ticket["id"])["step_id"]


def test_a_reviewer_of_one_ticket_moves_an_unrelated_ticket_it_may_work_on(api):
    with api.app.state.store.transaction() as c:
        ticket_with_checks(c)
        typ = c.execute("SELECT id FROM task_types WHERE name='Dev tickets'").fetchone()[0]
        other = H.task_create(c, "human:ana", "Other work", "Build.", "bot:cpo", type=typ, step="In Progress", lint=False)
        H.task_update(c, "bot:finance", other["id"], step="Review 1")
