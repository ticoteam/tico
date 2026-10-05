"""The task change feed: a board learns each change with who made it, never a task it may not see."""

from backend.tests.test_tasks_board import api, get, post  # noqa: F401  (the fixture)


def changes(api, after=None, token="ana-test"):
    return get(api, "task-changes" + ("" if after is None else f"?after={after}"), token=token)


def test_a_change_reaches_the_feed_signed_and_a_private_task_reaches_only_its_parties(api):
    start = changes(api)["seq"]
    task = post(api, "tasks", {"owner": "ben", "title": "Review the launch copy", "body": "Please."})
    secret = post(api, "tasks", {"owner": "ana", "title": "Review the salary bands", "body": "Please.", "private": True})

    moved = post(api, "tasks/" + task["id"], {"version": task["version"], "status": "doing"}, token="ben-test")
    seen = {c["id"]: c for c in changes(api, start, token="priya-test")["changes"]}
    assert seen[task["id"]]["task"]["status"] == "doing"
    assert seen[task["id"]]["task"]["version"] == moved["version"]
    assert seen[task["id"]]["actor"] == "human:ben"
    assert secret["id"] not in seen
    assert secret["id"] in {c["id"] for c in changes(api, start)["changes"]}

    # Resuming from the last change number sends only what came after it.
    cursor = changes(api, start, token="priya-test")["seq"]
    assert changes(api, cursor, token="priya-test")["changes"] == []


def test_the_feed_is_for_people(api):
    from backend.tests.test_tasks_board import bot_token
    r = api.get("/api/v2/task-changes?after=1", headers={"Authorization": "Bearer " + bot_token(api)})
    assert r.status_code == 403
