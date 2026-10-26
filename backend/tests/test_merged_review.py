"""A Review 1 or 2 asked after the PR merged judges the merged head (Dev 569)."""
import json
import uuid
from types import SimpleNamespace

import pytest

from backend import hubdb as H
from backend.tests.test_github import PR, api, get, headers, hook, pr_event  # noqa: F401
from backend.tests.test_review_gate import HEAD, OLD, ticket


def merged(api, receipt=None, state="merged"):
    task = ticket(api, receipt)
    if state == "merged":
        hook(api, "pull_request", pr_event("closed", merged=True, head={"sha": HEAD}, merge_commit_sha="d" * 40))
    else:
        hook(api, "pull_request", pr_event("closed", state="closed", head={"sha": HEAD}))
    return get(api, "tasks/" + task["id"])["task"]


def github(api, monkeypatch, runs, status=200, heads=None):
    """A synthetic GitHub whose PR stays at HEAD with these check runs, or that answers every read with `status`.
    `heads` gives other PR numbers their own head; `runs` keyed by head gives each head its own check runs."""
    service = api.app.state.github_app
    monkeypatch.setattr(service, "row", lambda c: {"org": "ticoteam"})
    monkeypatch.setattr(service, "mint", lambda *a: ("synthetic", "later"))
    calls = []
    def fetch(method, path, **kw):
        calls.append(path)
        number = path.rsplit("/pulls/", 1)[-1] if "/pulls/" in path else None
        sha = path.split("/commits/")[-1].split("/")[0] if "/commits/" in path else None
        at = runs.get(sha, {}) if sha and all(len(k) == 40 for k in runs) else runs
        data = ({"head": {"sha": (heads or {}).get(number, HEAD)}} if number else
                {"check_runs": [{"id": i, "name": n, "conclusion": v} for i, (n, v) in enumerate(at.items())]}
                if path.endswith("check-runs") else [])
        return SimpleNamespace(status_code=status, json=lambda: data)
    monkeypatch.setattr(service, "_call", fetch)
    return calls


def review(api, task, title="Review 2: PR #412", owner="cmo"):
    return api.post("/api/v2/tasks", headers=headers(), json={
        "owner": owner, "title": title, "body": f"Delta check of merged PR #412 head {HEAD}",
        "relations": [{"task": task["id"], "kind": "parent"}]})


def second_pr(api, task, state, head):
    """Link PR #413 to the ticket beside #412, in `state` at `head`."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO task_links(id,task_id,kind,url,state,created,repo,number,detail_json,updated) "
                  "VALUES(?,?,'pr',?,?,?,'ticoteam/tico',413,?,?)",
                  (str(uuid.uuid4()), task["id"], PR[:-3] + "413", state, H.now(), json.dumps({"head_sha": head}), H.now()))


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
    assert "retries this move" not in r.text, "only a refused step move is retried, never a review request"
    assert not woke(api)


def test_a_merged_head_github_cannot_read_is_refused_as_merged(api, monkeypatch):
    task = merged(api, f"Tests: head {HEAD}: 9 passed, 0 failed")
    github(api, monkeypatch, {"python": "success"}, status=502)
    r = review(api, task)
    assert r.status_code == 422 and "is merged" in r.text and "Cannot verify that head" in r.text, r.text
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
    assert "retries this move" not in r.text
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


def test_open_and_merged_prs_together_judge_only_the_open_one(api, monkeypatch):
    task = merged(api)
    second_pr(api, task, "open", OLD)
    github(api, monkeypatch, {HEAD: {"python": "failure"}, OLD: {"python": "success"}}, heads={"413": OLD})
    r = review(api, task)
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        judged = [row[0] for row in c.execute("SELECT new FROM task_events WHERE task_id=? AND field='review_head'",
                                              (r.json()["task"]["id"],))]
    assert judged == [OLD], "the open follow-up is judged; the merged PR's failing head is history"


def test_every_merged_head_is_judged_when_no_pr_is_open(api, monkeypatch):
    task = merged(api)
    second_pr(api, task, "merged", OLD)
    github(api, monkeypatch, {HEAD: {"python": "success"}, OLD: {"python": "failure"}}, heads={"413": OLD})
    r = review(api, task)
    assert r.status_code == 422 and "pull/413 is merged" in r.text and OLD in r.text and "CI failing" in r.text, r.text
    assert not woke(api)


def test_a_refused_review_of_a_merged_pr_holds_the_ticket_until_github_is_seen_again(api, monkeypatch):
    """A fresh CI refusal at the merged head can be newer than the cached link; the hold it leaves must not clear
    from that older cache, only from a later GitHub observation."""
    from backend import review_gate, task_blocked
    task = merged(api, f"Tests: head {HEAD}: 9 passed, 0 failed")
    github(api, monkeypatch, {"python": "failure"})
    assert review(api, task).status_code == 422
    with api.app.state.store.transaction() as c:
        held = H.task(c, task["id"])
        assert held["blocked_kind"] == "gate" and held["status"] == "waiting"
        task_blocked.reconcile(c, task["id"])
        assert H.task(c, task["id"])["blocked_since"], "the older cache cannot clear a fresher refusal"
        pr = review_gate.review_links(c, held)[0]
        c.execute("UPDATE task_links SET updated=?,detail_json=? WHERE id=?", (
            H.shift(H.now(), seconds=1), json.dumps({"head_sha": HEAD, "checks": {"python": "passing"}}), pr["id"]))
        task_blocked.reconcile(c, task["id"])
        resumed = H.task(c, task["id"])
        assert resumed["step_id"] == task["step_id"] and resumed["blocked_since"] is None
