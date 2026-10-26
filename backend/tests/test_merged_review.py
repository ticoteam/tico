"""A Review 1 or 2 asked after the PR merged judges the merged head (Dev 569)."""
from types import SimpleNamespace

import pytest

from backend.tests.test_github import api, get, headers, hook, pr_event  # noqa: F401
from backend.tests.test_review_gate import HEAD, ticket


def merged(api, receipt=None, state="merged"):
    task = ticket(api, receipt)
    if state == "merged":
        hook(api, "pull_request", pr_event("closed", merged=True, head={"sha": HEAD}, merge_commit_sha="d" * 40))
    else:
        hook(api, "pull_request", pr_event("closed", state="closed", head={"sha": HEAD}))
    return get(api, "tasks/" + task["id"])["task"]


def github(api, monkeypatch, runs):
    """A synthetic GitHub whose PR stays at HEAD with these check runs."""
    service = api.app.state.github_app
    monkeypatch.setattr(service, "row", lambda c: {"org": "ticoteam"})
    monkeypatch.setattr(service, "mint", lambda *a: ("synthetic", "later"))
    calls = []
    def fetch(method, path, **kw):
        calls.append(path)
        data = ({"head": {"sha": HEAD}} if "/pulls/" in path else
                {"check_runs": [{"id": i, "name": n, "conclusion": v} for i, (n, v) in enumerate(runs.items())]}
                if path.endswith("check-runs") else [])
        return SimpleNamespace(status_code=200, json=lambda: data)
    monkeypatch.setattr(service, "_call", fetch)
    return calls


def review(api, task, title="Review 2: PR #412", owner="cmo"):
    return api.post("/api/v2/tasks", headers=headers(), json={
        "owner": owner, "title": title, "body": f"Delta check of merged PR #412 head {HEAD}",
        "relations": [{"task": task["id"], "kind": "parent"}]})


def woke(api, bot="cmo"):
    return jobs(api, bot) > 0


def jobs(api, bot):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM jobs WHERE bot=?", (bot,)).fetchone()[0]


@pytest.mark.parametrize("title", ["Review 1: PR #412", "Review 2: PR #412"])
def test_a_review_of_a_merged_pr_with_green_checks_judges_the_merged_head(api, monkeypatch, title):
    task = merged(api)
    calls = github(api, monkeypatch, {"python": "success", "caddy": "skipped"})
    r = review(api, task, title)
    assert r.status_code == 200, r.text
    assert woke(api)
    assert any("/commits/" + HEAD + "/check-runs" in p for p in calls), "CI is read at the merged head"
    with api.app.state.store.read() as c:
        judged = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='review_head'",
                           (r.json()["task"]["id"],)).fetchone()
    assert judged and judged[0] == HEAD


@pytest.mark.parametrize("runs,receipt,says", [
    ({"python": "failure"}, None, "CI failing at that head: python"),
    ({"python": "success", "browser": None}, None, "browser (1) still pending"),
    ({}, None, "No passing GitHub check"),
    ({"python": "success"}, f"Tests: head {HEAD}: 3 failed", "failed test receipt")])
def test_a_merged_head_with_unfinished_or_failed_evidence_is_refused(api, monkeypatch, runs, receipt, says):
    task = merged(api, receipt)
    github(api, monkeypatch, runs)
    r = review(api, task)
    assert r.status_code == 422, r.text
    assert says in r.text and "is merged" in r.text and HEAD in r.text, r.text
    assert "no linked PR" not in r.text
    assert not woke(api)


@pytest.mark.parametrize("receipt,allowed", [(None, False), (f"Tests: head {HEAD}: 9 passed, 0 failed", True)])
def test_without_a_fresh_github_read_a_merged_head_still_needs_a_receipt(api, receipt, allowed):
    task = merged(api, receipt)
    r = review(api, task)
    assert r.status_code == (200 if allowed else 422), r.text
    if not allowed:
        assert "is merged" in r.text and "naming the merged head" in r.text and "no linked PR" not in r.text
    assert woke(api) is allowed


def test_a_pr_closed_without_merging_still_refuses_a_review(api, monkeypatch):
    task = merged(api, f"Tests: head {HEAD}: 9 passed, 0 failed", state="closed")
    github(api, monkeypatch, {"python": "success"})
    r = review(api, task)
    assert r.status_code == 422 and "closed without merging" in r.text, r.text
    assert not woke(api)


def test_a_second_review_at_the_same_merged_head_is_a_duplicate(api, monkeypatch):
    task = merged(api)
    github(api, monkeypatch, {"python": "success"})
    first = review(api, task)
    assert first.status_code == 200, first.text
    before = jobs(api, "cpo")
    again = review(api, task, "Review 2: PR #412 again", owner="cpo")
    assert again.status_code == 422 and "already open" in again.text and first.json()["task"]["id"] in again.text
    assert jobs(api, "cpo") == before, "the refused request woke nobody"


def test_an_open_pr_is_judged_before_a_merged_one(api, monkeypatch):
    """A follow-up PR still open on the ticket is what a review judges; the merged one is history."""
    task = merged(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='open' WHERE task_id=? AND kind='pr'", (task["id"],))
    github(api, monkeypatch, {"python": "success", "browser": None})
    r = review(api, task)
    assert r.status_code == 422 and "is merged" not in r.text and "test receipt" in r.text, r.text
