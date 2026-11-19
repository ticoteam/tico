"""A pull request closed without merging after its task finished leaves Health's aged_prs on the next pass (Engineering
#2398: PR #4381 duplicated #4382, which merged and finished the task; #4381 closed later and Health kept telling the
Release Owner to merge it, because only open tasks' links are read again)."""

import json
from types import SimpleNamespace

from backend import github as G
from backend.store import H
from backend.tests.helpers.github import post
from backend.tests.helpers.api import headers

OLD = H.shift(H.now(), hours=-5)
PR_OLD = H.shift(H.now(), hours=-13)


def check(body, key):
    return next((x for x in body["checks"] if x["id"] == key), None)


def health(api):
    response = api.get("/api/v2/health", headers=headers())
    assert response.status_code == 200, response.text
    return response.json(), 0


def _github(api, monkeypatch, answers):
    """Fake GitHub reads: `answers` maps a PR number to (status code, body). Returns the paths read."""
    service = api.app.state.github_app
    with service.store.transaction() as c:
        c.execute(
            "INSERT INTO repositories(full_name,reachable,enabled,added_by,updated_at,default_branch) "
            "VALUES('acme/app',1,1,'human:ana',?,'main')",
            (H.now(),),
        )
    seen = []

    def fetch(method, path, headers=None, **kw):
        seen.append(path)
        code, body = answers[int(path.rsplit("/", 1)[1])]
        return SimpleNamespace(status_code=code, json=lambda: body)

    monkeypatch.setattr(service, "mint", lambda *a, **k: ("t", "later"))
    monkeypatch.setattr(service, "_call", fetch)
    monkeypatch.setattr(G, "_finished_retry", {})
    return service, seen


def reviews(api, prefix, statuses):
    """One bot task per status, each with an acme/app PR #1, #2, ... green and waiting for review for 13 hours.
    A finished task is inserted open and finished through the engine's move(), the only way its status changes."""
    from backend import task_engine

    made = [post(api, "tasks", {"owner": "cpo", "title": f"PR {prefix}{n}", "body": "x"}) for n in range(len(statuses))]
    with api.app.state.store.transaction() as c:
        for n, status in enumerate(statuses):
            tid = made[n]["id"]
            detail = {"head_sha": f"{prefix}{n}", "health_wait": {"reason": "review", "since": PR_OLD}}
            c.execute(
                "INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,mergeable,checks,"
                "review_state,detail,number,repo,branch,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"{prefix}-{n}-link",
                    tid,
                    "pr",
                    f"https://github.com/acme/app/pull/{n + 1}",
                    "PR",
                    "open",
                    "bot:few-00",
                    OLD,
                    "clean",
                    "passing",
                    "",
                    json.dumps(detail),
                    n + 1,
                    "acme/app",
                    f"b{prefix}{n}",
                    OLD,
                ),
            )
            if status != "open":
                end = task_engine.DONE if status == "done" else task_engine.CLOSED
                task_engine.move(c, tid, end, "human:ana", "test: the task finished")
    return [t["id"] for t in made]


def _refresh(service, monkeypatch):
    """One pass, as the installed app for acme (only for the pass: Health reads the real app row)."""
    with monkeypatch.context() as m:
        m.setattr(service, "row", lambda c=None: {"slug": "test-app", "org": "acme"})
        return G.refresh_finished_task_prs(service)


def _link(api, tid):
    with api.app.state.store.read() as c:
        row = dict(c.execute("SELECT * FROM task_links WHERE task_id=? AND kind='pr'", (tid,)).fetchone())
    return {**row, "detail": H._json(row["detail"], {})}


def _named(api):
    found = check(health(api)[0], "aged_prs")
    return sorted(i["name"] for i in found["items"]) if found else []


def test_a_pr_closed_after_its_task_finished_leaves_aged_prs(api, monkeypatch):
    t = reviews(api, "old", ["done", "done", "open"])  # PRs #1, #2, #3, green and waiting 13 h, on bot tasks
    assert _named(api) == ["PR #1", "PR #2", "PR #3"]  # the stale link is still named: the bug
    service, seen = _github(
        api,
        monkeypatch,
        {
            1: (200, {"state": "closed", "merged": False}),  # closed without merging (the #4381 case)
            2: (200, {"state": "open", "merged": False, "draft": False}),  # still open on a finished task
            3: (200, {"state": "closed", "merged": False}),  # its task is open: refresh_conflicts' read, not this one
        },
    )
    burst_before = _burst_keys(api)
    assert _refresh(service, monkeypatch) == 2
    assert sorted(seen) == ["/repos/acme/app/pulls/1", "/repos/acme/app/pulls/2"]
    closed = _link(api, t[0])
    assert closed["state"] == "closed" and "health_wait" not in closed["detail"]
    assert _link(api, t[1])["state"] == "open"
    assert _link(api, t[2])["state"] == "open"
    assert _named(api) == ["PR #2", "PR #3"]
    assert _burst_keys(api) == burst_before  # a finished task's owner is not woken
    # The still-open one is not read again until it is stale; the closed one is never read again.
    assert _refresh(service, monkeypatch) == 0


def test_a_missing_or_merged_pr_on_a_finished_task_settles_and_a_failed_read_waits(api, monkeypatch):
    t = reviews(api, "gone", ["done", "closed", "done"])
    service, seen = _github(
        api,
        monkeypatch,
        {
            1: (404, {}),
            2: (200, {"state": "closed", "merged": True, "merge_commit_sha": "c" * 40, "merged_at": H.now()}),
            3: (502, {}),
        },
    )
    assert _refresh(service, monkeypatch) == 2
    assert _link(api, t[0])["state"] == "closed"
    merged = _link(api, t[1])
    assert merged["state"] == "merged" and merged["pr_sha"] == "c" * 40
    assert _link(api, t[2])["state"] == "open"
    assert _named(api) == ["PR #3"]
    reads = len(seen)
    assert _refresh(service, monkeypatch) == 0 and len(seen) == reads  # the failed one backs off


def _burst_keys(api):
    with api.app.state.store.read() as c:
        return sorted(r[0] for r in c.execute("SELECT key FROM registry_metadata WHERE key LIKE 'github-task-wake:%'"))
