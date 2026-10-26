"""Needs you on a person with many tasks, and on Product roadmap items, which change only by a step move."""

from backend.store import H
from backend.tests.test_ask_resolve import question
from backend.tests.test_tasks_board import api, get, post  # noqa: F401

ANA, OPS = "human:ana", "bot:ops"


def test_needs_you_lists_every_owned_active_task_past_five_hundred(api):
    with api.app.state.store.transaction() as c:
        first = H.task_create(c, OPS, "Review the vendor list", "x", ANA)
        row = dict(c.execute("SELECT * FROM tasks WHERE id=?", (first["id"],)).fetchone())
        for n in range(520):
            row.update(id=f"bulk-{n:03}", title=f"Review vendor {n}", number=None, rank=None)
            c.execute(f"INSERT INTO tasks ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
        listed = {t["id"] for t in H.needs_you(c, ANA)["tasks"]}
    assert len(listed) == 521 and "bulk-519" in listed


def test_a_declined_roadmap_item_stays_off_its_requesters_list_and_a_roadmap_card_says_it_moves_by_step(api):
    with api.app.state.store.transaction() as c:
        idea = H.task_create(c, ANA, "Dark mode for the public roadmap", "x", OPS, type="product-roadmap",
                             step="Suggested")
        plain = H.task_create(c, ANA, "Book the venue", "x", OPS)
        H.task_update(c, OPS, plain["id"], status="declined", note="No budget.")
        H.task_update(c, H.KEEPER, idea["id"], step="Declined")
        assert H.task(c, idea["id"])["status"] == "declined"
        declined = {t["id"] for t in H.needs_you(c, ANA)["declined"]}
        asked = H.task_create(c, ANA, "Bulk export for roadmap items", "x", OPS, type="product-roadmap",
                              step="Suggested")
        question(c, OPS, asked["id"], "Which format should the export use?")
    assert plain["id"] in declined and idea["id"] not in declined
    items = {i["id"]: i for i in get(api, "needs-you", token="ana-test")["items"]}
    assert items[asked["id"]]["steps_only"] is True and items[plain["id"]]["steps_only"] is False
