"""The Needs you summary answers with the list's own count, split by kind and by the bot that put each item there."""

from backend import hubdb as H
from backend.tests.helpers.api import get, post  # noqa: F401


def test_summary_total_is_the_lists_count_and_splits_by_bot(api):
    with api.app.state.store.transaction() as c:
        H.task_create(c, "bot:finance", "Give finance the payroll connector", "x", "human:ana")
        H.task_create(c, "bot:finance", "Pick a payroll vendor", "x", "human:ana")
    post(api, "tasks", {"title": "Renew the domain", "body": "x", "owner": "human:ana"})

    summary = get(api, "needs-you/summary")
    assert summary["actor"] == "human:ana"
    assert summary["total"] == get(api, "needs-you?count=1")["count"] == 3
    assert summary["by_kind"] == {"task": 3}
    by_bot = {b["bot"]: b for b in summary["by_bot"]}
    assert by_bot["bot:finance"]["count"] == 2 and by_bot["other"]["count"] == 1
    capped = get(api, "needs-you/summary?limit=1")
    assert all(len(b["items"]) <= 1 for b in capped["by_bot"]) and capped["bots_left"] >= 0


def test_another_persons_private_task_is_not_counted_for_you(api):
    post(
        api,
        "tasks",
        {"title": "Ben's private plan", "body": "x", "owner": "human:ben", "private": True},
        token="ben-test",
    )
    cara = get(api, "needs-you/summary", "cara-test")
    assert cara["total"] == get(api, "needs-you?count=1", "cara-test")["count"] == 0


def test_a_stage_ok_on_two_tasks_is_two_items_each_its_own(api):
    """A stage's OK is one requirement on every task in the stage: each task's ask is its own item (its visit and the
    requirement), answered by the requirement on that task. Its live mark and a mend by `ids` reach that one alone."""
    from backend.tests.helpers import engine as X

    with api.app.state.store.transaction() as c:
        stage_ok = X.requirement(c, "general-todo", "ok", person="human:ana", line="OK the spend")
        ads = H.task_create(c, "human:ana", "Run the ads", "x", "bot:finance", lint=False)
        launch = H.task_create(c, "human:ana", "Run the launch ads", "x", "bot:finance", lint=False)
    items = [i for i in get(api, "needs-you")["items"] if i["kind"] == "ask"]
    assert sorted(i["task_id"] for i in items) == sorted([ads["id"], launch["id"]])
    assert len({i["id"] for i in items}) == 2 and {i["requirement_id"] for i in items} == {stage_ok}
    assert all(i["ask"] and i["ask"]["requirement_id"] == stage_ok for i in items)
    one = next(i for i in items if i["task_id"] == ads["id"])
    mended = get(api, f"needs-you?ids={one['id']}")
    assert [i["task_id"] for i in mended["items"]] == [ads["id"]] and list(mended["positions"]) == [one["id"]]
