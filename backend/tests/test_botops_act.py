"""A person's task lends their rights to BotOps until someone else reopens it after it ended (backend/botops_act.py
lend_lapsed), read from the engine's move rows."""

from backend import botops_act as BA
from backend.store import H
from backend.tests.helpers import engine as X

ANA = "human:ana"


def test_a_task_someone_else_reopened_never_lends_the_persons_rights_again(company):
    with company.transaction() as c:
        X.bot(c, "botops")
        own = H.task_create(c, ANA, "Add a computer for Ops", "Ops needs one.", "bot:botops", lint=False)["id"]
        assert BA.lend_lapsed(c, H.task(c, own)) == ""
        H.task_close(c, ANA, own, "Not now.")
        H.task_move(c, ANA, own, H.TASK_REOPEN, "Now it is needed.")
        assert [r["actor"] for r in BA.reopened_by(c, own)] == [ANA]
        assert BA.lend_lapsed(c, H.task(c, own)) == ""  # the person reopened it herself
        H.task_close(c, ANA, own, "Done.")
        H.task_move(c, H.KEEPER, own, H.TASK_REOPEN, "One more.")
        assert "someone other than the person reopened it" in BA.lend_lapsed(c, H.task(c, own))
