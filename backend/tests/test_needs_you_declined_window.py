"""A declined task leaves its requester's Needs you a day after its last change: a notice from weeks ago is not
something the requester still has to act on."""

from backend.store import H
from backend.tests.test_tasks_board import api, get, post  # noqa: F401

ANA, OPS = "human:ana", "bot:ops"


def test_a_declined_task_leaves_its_requesters_list_a_day_after_its_last_change(api):
    with api.app.state.store.transaction() as c:
        recent = H.task_create(c, ANA, "Book the venue", "x", OPS)
        edge = H.task_create(c, ANA, "Order the signs", "x", OPS)
        old = H.task_create(c, ANA, "Hire a caterer", "x", OPS)
        for task in (recent, edge, old):
            H.task_update(c, OPS, task["id"], status="declined", note="Duplicate - no answer needed here.")
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), hours=-(H.DECLINED_NEEDS_HOURS - 1)), edge["id"]))
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), days=-21), old["id"]))
        declined = {t["id"] for t in H.needs_you(c, ANA)["declined"]}
    assert {recent["id"], edge["id"]} <= declined and old["id"] not in declined
