"""Needs you on a person with many tasks, and on Product roadmap items, which change only by a step move."""

from backend import question_tasks
from backend.store import H
from backend.tests.helpers.tasks_board import api, get, post  # noqa: F401

ANA, OPS = "human:ana", "bot:ops"
PICK = [
    {
        "id": "pick",
        "header": "Vendor",
        "question": "Which vendor?",
        "multi": False,
        "other": True,
        "options": [{"label": "Acme", "description": ""}, {"label": "Beta", "description": ""}],
    }
]


def test_needs_you_lists_every_owned_active_task_past_five_hundred(api):
    with api.app.state.store.transaction() as c:
        first = H.task_create(c, OPS, "Review the vendor list", "x", ANA)
        row = dict(c.execute("SELECT * FROM tasks WHERE id=?", (first["id"],)).fetchone())
        for n in range(520):
            row.update(id=f"bulk-{n:03}", title=f"Review vendor {n}", number=None, rank=None)
            c.execute(f"INSERT INTO tasks ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
        listed = {t["id"] for t in H.needs_you(c, ANA)["tasks"]}
    assert len(listed) == 521 and "bulk-519" in listed


def test_a_declined_roadmap_item_stays_off_its_requesters_list(api):
    with api.app.state.store.transaction() as c:
        idea = H.task_create(
            c, ANA, "Dark mode for the public roadmap", "x", OPS, type="product-roadmap", step="Suggested"
        )
        plain = H.task_create(c, ANA, "Book the venue", "x", OPS)
        H.task_update(c, OPS, plain["id"], status="declined", note="No budget.")
        H.task_update(c, H.KEEPER, idea["id"], step="Declined")
        assert H.task(c, idea["id"])["status"] == "declined"
        declined = {t["id"] for t in H.needs_you(c, ANA)["declined"]}
    assert plain["id"] in declined and idea["id"] not in declined
    items = {i["id"]: i for i in get(api, "needs-you", token="ana-test")["items"]}
    assert items[plain["id"]]["steps_only"] is False


def test_a_question_on_a_product_lane_or_numbered_task_is_on_the_persons_list_and_the_same_tasks_without_one_are_not(
    api,
):
    with api.app.state.store.transaction() as c:
        vendors = H.type_create(c, H.KEEPER, "Vendor tickets", [{"name": "Todo", "status": "open"}], numbered=True)
        plain_product = H.task_create(c, OPS, "Product backlog item", "x", ANA)
        c.execute("UPDATE tasks SET lane='product' WHERE id=?", (plain_product["id"],))
        asked_product = H.task_create(c, OPS, "Product vendor question", "x", ANA)
        c.execute("UPDATE tasks SET lane='product' WHERE id=?", (asked_product["id"],))
        plain_numbered = H.task_create(c, OPS, "Vendor ticket", "x", ANA, type=vendors["id"])
        asked_numbered = H.task_create(c, OPS, "Vendor question", "x", ANA, type=vendors["id"])
        for task in (asked_product, asked_numbered):
            question_tasks.store(c, task["id"], {"questions": PICK})
        listed = {t["id"] for t in H.needs_you(c, ANA)["tasks"]}
    assert {asked_product["id"], asked_numbered["id"]} <= listed
    assert not {plain_product["id"], plain_numbered["id"]} & listed
    items = {i["id"] for i in get(api, "needs-you", token="ana-test")["items"]}
    assert {asked_product["id"], asked_numbered["id"]} <= items
    assert not {plain_product["id"], plain_numbered["id"]} & items
