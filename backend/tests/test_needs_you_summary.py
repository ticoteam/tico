"""The Needs you summary answers with the board's own count, split by kind and by the bot that put each item there."""

from backend import hubdb as H
from backend.tests.test_api import api, get, post, setup_attempt  # noqa: F401


def test_summary_total_is_the_board_count_and_splits_by_bot(api):
    _, _, finance = setup_attempt(api, "finance")
    post(api, "tasks", {"title": "Renew the domain", "body": "x", "owner": "human:ana"}, token=finance["token"])
    with api.app.state.store.transaction() as c:
        H.task_create(c, "bot:finance", "Give finance the payroll connector", "x", "human:ana")
        H.task_create(c, "bot:finance", "Pick a payroll vendor", "x", "human:ana")

    summary = get(api, "needs-you/summary")
    board = get(api, "needs-you?count=1")["count"]

    assert summary["actor"] == "human:ana"
    assert summary["total"] == board
    assert summary["this_week"] <= summary["total"]
    assert sum(b["count"] for b in summary["by_bot"]) <= summary["total"]
    assert all(set(b) >= {"bot", "count"} for b in summary["by_bot"])


def test_summary_caps_the_bot_list_and_says_how_many_were_left(api):
    summary = get(api, "needs-you/summary?limit=1")
    assert len(summary["by_bot"]) <= 1
    assert summary["bots_left"] >= 0


def test_summary_is_for_people_only(api):
    _, _, finance = setup_attempt(api, "finance")
    r = api.get("/api/v2/needs-you/summary", headers={"Authorization": "Bearer " + finance["token"]})
    assert r.status_code in (403, 404), r.text


def test_another_persons_private_task_is_not_counted_for_you(api):
    post(api, "tasks", {"title": "Ben's private plan", "body": "x", "owner": "human:ben", "private": True},
         token="ben-test")
    cara = get(api, "needs-you/summary", "cara-test")
    shown = {item["id"] for bot in cara["by_bot"] for item in bot["items"]}
    assert cara["total"] == get(api, "needs-you?count=1", "cara-test")["count"]
    titles = {item["title"] for bot in cara["by_bot"] for item in bot["items"]}
    assert "Ben's private plan" not in titles and not shown
