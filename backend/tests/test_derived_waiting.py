"""Who a task waits on is derived, not stored: a bot's ask names a person, the task's turn is theirs while it is
open, and being asked gives that person the answer and nothing more. The rest of `waiting_on` is tested in
test_waiting_on.py and test_task_calls.py."""

import pytest

from backend.store import H
from backend.tests.helpers import engine as X

ANA, BEN, BUILDER = "human:ana", "human:ben", "bot:builder"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        X.bot(conn, "builder")
        yield conn


def _version(c, task_id):
    return H.task(c, task_id)["version"]


def test_the_person_a_bots_task_waits_on_answers_it_but_cannot_close_or_finish_the_bots_task(c):
    row = H.task_create(c, ANA, "Renew the domain", "Renew it for a year.", BUILDER, lint=False)
    asked = H.task_ask(c, BUILDER, row["id"], BEN, "Renew it for a year?", seen_version=_version(c, row["id"]))
    assert "waiting_on" not in H.task(c, row["id"])  # nothing stored on the task
    view = H.task_view(c, row["id"], BEN)
    assert (view["turn"]["state"], view["turn"]["who"]) == ("person", BEN)
    with pytest.raises(H.Refused, match="identity"):
        H.task_close(c, BEN, row["id"], "Not now")
    with pytest.raises(H.Refused, match="identity"):
        H.task_done(c, BEN, row["id"], "Done.")
    assert H.task(c, row["id"])["status"] == "open"
    H.task_answer(c, BEN, row["id"], asked["requirement_id"], "Yes")
    assert H.task(c, row["id"])["status"] == "open"
    assert H.task_view(c, row["id"], ANA)["turn"]["who"] == BUILDER
