"""A bot that only owns a check filed under a task (a review, a security check) never moves that task: it finishes its
own. The task's owner and people still move it (task_calls.can_work)."""

import pytest

from backend.hubdb import Refused
from backend.store import H
from backend.tests.helpers import engine as X

DEV = (
    {"name": "In Progress", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "Review 1", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "QA Fixes", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
)


def task_with_checks(c):
    X.pipeline(c, "dev", "Dev", DEV)
    task = X.new_task(c, "human:ana", "Improve messages", "dev", assignee="bot:cpo")
    review = H.task_create(
        c, "bot:cpo", "Review 1: messages", "Review.", "bot:finance", parent_id=task["id"], lint=False
    )
    security = H.task_create(
        c, "bot:cpo", "Security check of messages", "Check.", "bot:inbox", parent_id=task["id"], lint=False
    )
    return H.task(c, task["id"]), review, security


@pytest.mark.parametrize("who", ["bot:finance", "bot:inbox"])
def test_a_reviewer_or_security_cannot_move_the_task_and_finishes_its_own(api, who):
    with api.app.state.store.transaction() as c:
        task, review, security = task_with_checks(c)
        with pytest.raises(Refused, match="worked by its owner"):
            H.task_move(c, who, task["id"], "Review 1", "Looks fine", task["version"])
        with pytest.raises(Refused, match="worked by its owner"):
            H.task_done(c, who, task["id"], "Looks fine", task["version"])
        assert H.task(c, task["id"])["status"] == "open"
        mine = H.task(c, review["id"] if who == "bot:finance" else security["id"])
        assert H.task_done(c, who, mine["id"], "Fine.", mine["version"])["status"] == "done"


def test_the_owner_and_people_still_move_the_task(api):
    with api.app.state.store.transaction() as c:
        task, _, _ = task_with_checks(c)
        task = H.task_move(c, "bot:cpo", task["id"], "Review 1", "Built it", task["version"])
        task = H.task_move(c, "human:ana", task["id"], "QA Fixes", "Reviewed", task["version"])
        task = H.task_move(c, "bot:cpo", task["id"], "In Progress", "Back to building", task["version"])
        assert H.task(c, task["id"])["stage_id"] == "dev-in-progress"


def test_a_reviewer_of_one_task_moves_its_own_unrelated_task(api):
    with api.app.state.store.transaction() as c:
        task_with_checks(c)
        other = X.new_task(c, "human:ana", "Other work", "dev", assignee="bot:finance")
        moved = H.task_move(c, "bot:finance", other["id"], "Review 1", "Built it", other["version"])
        assert moved["stage_id"] == "dev-review-1"
