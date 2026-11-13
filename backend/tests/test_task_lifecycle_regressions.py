"""Task bug-hunt regressions, using only disposable fictional team data: GitHub's computed news is not a change to a
task, and a task whose worktree is not ready says so."""

import threading
from types import SimpleNamespace

import pytest

from backend import github as G, hubdb as H, worktree_waits as WW
from backend.tests.helpers import engine as X
from backend.tests.helpers.github import api, get, post, pr_event, PR  # noqa: F401

AT = "2026-10-26T12:00:00.000000Z"
OLD = "2026-10-25T11:00:00.000000Z"


@pytest.fixture
def c(api, monkeypatch):
    monkeypatch.setattr(H, "now", lambda: AT)
    with api.app.state.store.transaction() as conn:
        yield conn


def create(c, title, owner="bot:cmo", **kw):
    return H.task_create(c, "human:ana", title, "Synthetic regression fixture", owner, lint=False, **kw)


def age(c, task_id):
    c.execute("UPDATE tasks SET created_at=?,updated_at=?,done_at=? WHERE id=?", (OLD, OLD, OLD, task_id))
    c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?", (OLD, task_id))
    c.execute(
        "UPDATE messages SET created_at=? WHERE conversation_id=(SELECT conversation_id FROM tasks WHERE id=?)",
        (OLD, task_id),
    )


@pytest.mark.parametrize("merged", [False, True])
def test_opening_task_refreshes_links_without_touching_tasks(api, monkeypatch, merged):
    tasks = [
        post(api, "tasks", {"owner": "cpo", "title": title, "body": "x", "links": [PR]})
        for title in ("Ship export", "Review export")
    ]
    service = api.app.state.github_app
    with service.store.transaction() as c:
        c.execute(
            "INSERT INTO repositories(full_name,reachable,enabled,added_by,updated_at) VALUES(?,1,1,?,?)",
            ("ticoteam/tico", "human:ana", H.now()),
        )
        G.pull_request(c, pr_event("synchronize", head={"sha": "head", "ref": "export"}, state="open"))
        for task in tasks:
            H.task_update(c, "human:ana", task["id"], status="done")
            age(c, task["id"])
    pr = {
        "state": "closed" if merged else "open",
        "merged": merged,
        "draft": False,
        "mergeable": True,
        "head": {"sha": "head", "ref": "export"},
        "merge_commit_sha": "merge" if merged else None,
        "merged_at": AT if merged else None,
    }
    monkeypatch.setattr(service, "row", lambda c=None: {"slug": "example-app", "org": "ticoteam"})
    monkeypatch.setattr(service, "mint", lambda *a, **kw: ("synthetic-token", "later"))
    monkeypatch.setattr(
        service,
        "_call",
        lambda method, path, **kw: SimpleNamespace(
            status_code=200 if "/pulls/" in path else 404, json=lambda: dict(pr)
        ),
    )
    get(api, "tasks/" + tasks[0]["id"])
    for worker in threading.enumerate():
        if worker.name == "tico-pr-refresh":
            worker.join(5)
    assert not service.pr_refresh_running
    with service.store.read() as c:
        for task in tasks:
            row = H.task(c, task["id"])
            link = H.task_links(c, task["id"])[0]
            assert link["state"] == ("merged" if merged else "open")
            assert link["mergeable"] == "clean"
            assert row["updated_at"] == OLD
            history = c.execute(
                "SELECT id, subject_id AS task_id, created_at, actor_id AS actor, kind AS field, old, new, note, action_id, via FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='github' AND created_at>?",
                (task["id"], OLD),
            ).fetchall()
            assert len(history) == int(merged)
            if merged:
                assert "GitHub" in history[0]["note"] and "merged" in history[0]["new"]


@pytest.mark.parametrize("mergeable", [True, False])
def test_a_computed_mergeability_webhook_never_counts_as_task_activity(c, mergeable):
    task = create(c, "Build export")
    H.task_link(c, "human:ana", task["id"], PR, kind="pr", mover=True)
    G.pull_request(c, pr_event("synchronize", head={"sha": "head", "ref": "export"}))
    c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (OLD, task["id"]))
    G.pull_request(c, pr_event("opened", head={"sha": "head", "ref": "export"}, mergeable=mergeable))
    assert H.task_links(c, task["id"])[0]["mergeable"] == ("clean" if mergeable else "conflict")
    assert H.task(c, task["id"])["updated_at"] == OLD
    G.pull_request(c, pr_event("closed", merged=True))
    assert H.task(c, task["id"])["updated_at"] == AT
    assert c.execute(
        "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='github'", (task["id"],)
    ).fetchone()
