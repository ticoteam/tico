"""One open Review 1, Review 2 or QA subtask per ticket and pull request head."""
import json

import pytest

from backend import hubdb as H
from backend.tests.test_github import api, get, headers, hook, pr_event  # noqa: F401
from backend.tests.test_review_gate import HEAD, OLD, ticket


def child(api, task, title, owner="cmo", expected=200):
    r = api.post("/api/v2/tasks", headers=headers(), json={
        "owner": owner, "title": title, "body": f"Check PR #412 head {HEAD}",
        "relations": [{"task": task["id"], "kind": "parent"}]})
    assert r.status_code == expected, r.text
    return r.json()


def test_a_second_request_for_the_same_check_and_head_points_at_the_open_one(api):
    task = ticket(api, f"Tests: base {'c' * 40} head {HEAD}: 9 passed, 0 failed")
    first = child(api, task, "Review 1: PR #412")["task"]
    with api.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM jobs").fetchone()[0]
    again = child(api, task, "Review 1: PR #412 again", owner="cpo", expected=422)
    assert first["id"] in str(again) and "already open" in str(again)
    # Other checks on the same ticket are different requests.
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs").fetchone()[0] == before, "the refused request woke nobody"
    child(api, task, "QA: PR #412")
    child(api, task, "Security review of PR #412")


def test_a_new_head_or_a_finished_check_allows_the_next_request(api):
    task = ticket(api)
    qa = child(api, task, "QA: PR #412")["task"]
    child(api, task, "QA: again", expected=422)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.head_sha',?) WHERE task_id=? AND kind='pr'",
                  (OLD, task["id"]))
    second = child(api, task, "QA: the new head", owner="cpo")["task"]
    assert second["id"] != qa["id"]
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:cmo", qa["id"], status="done", note="Done.", verdict="pass", verdict_head=HEAD)
        H.task_update(c, "bot:cpo", second["id"], status="done", note="Done.", verdict="pass", verdict_head=OLD)
    child(api, task, "QA: once more", expected=200)


def test_an_unknown_head_counts_as_the_same_one(api):
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail='{}' WHERE task_id=? AND kind='pr'", (task["id"],))
    child(api, task, "QA: first")
    child(api, task, "QA: second", expected=422)
