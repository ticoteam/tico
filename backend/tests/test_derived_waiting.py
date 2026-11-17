"""Who a task waits on is derived, not stored: a bot's ❓ names a person, the task's turn is theirs while it is
open, and being asked gives that person the reply and nothing more. The rest of `waiting_on` is tested in
test_waiting_on.py and test_task_calls.py."""

import pytest

from backend import question_marks as Q
from backend.store import H
from backend.tests.helpers import engine as X

ANA, BEN, BUILDER = "human:ana", "human:ben", "bot:builder"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        X.bot(conn, "builder")
        yield conn


def test_the_person_a_bots_task_waits_on_answers_it_but_cannot_close_or_finish_the_bots_task(c):
    row = H.task_create(c, ANA, "Renew the domain", "Renew it for a year.", BUILDER, lint=False)
    Q.ask(c, BUILDER, row["id"], BEN, "Renew it for a year?")
    assert "waiting_on" not in H.task(c, row["id"])  # nothing stored on the task
    view = H.task_view(c, row["id"], BEN)
    assert (view["turn"]["state"], view["turn"]["who"]) == ("person", BEN)
    with pytest.raises(H.Refused, match="identity"):
        H.task_close(c, BEN, row["id"], "Not now")
    with pytest.raises(H.Refused, match="identity"):
        H.task_done(c, BEN, row["id"], "Done.")
    assert H.task(c, row["id"])["status"] == "open"
    H.task_comment(c, BEN, row["id"], "Yes")  # the reply clears the ❓
    assert H.task(c, row["id"])["status"] == "open"
    assert H.task_view(c, row["id"], ANA)["turn"]["who"] == BUILDER
