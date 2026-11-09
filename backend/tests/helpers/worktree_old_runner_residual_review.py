"""Helpers that other tests share, moved out of test_worktree_old_runner_residual_review.py so they import no test file."""

from backend.tests.helpers.worktrees import post


def heartbeat(api, link, state=None):
    report = (
        []
        if state is None
        else [{"link_id": link["link_id"], "state": state, "branch": link["branch"], "repo": "Acme/product"}]
    )
    response = post(
        api,
        "runners/heartbeat",
        {
            "version": "0.2.35",
            "platform": "darwin",
            "readiness": {"schema_version": 1, "worktrees": True},
            "worktrees": report,
        },
        "runner-test",
    )
    assert response.status_code == 200, response.text
    return response.json()["worktree_actions"]
