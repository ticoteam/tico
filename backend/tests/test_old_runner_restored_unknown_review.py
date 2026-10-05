"""Legacy human restore remains compatible after an offline/unknown transition."""
import json
import uuid

from backend.tests.test_worktrees import api, gh, prepared, post, auth  # noqa: F401
from backend.tests.test_worktree_old_runner_residual_review import heartbeat


def test_old_human_restore_recovers_unknown_without_demoting_or_inventing_proof(prepared):
    api, tid, _ = prepared
    link = post(api, f"tasks/{tid}/worktrees", {"repo": "Acme/product"}).json()
    assert heartbeat(api, link)[0]["action"] == "restore"
    response = api.patch(f'/api/v2/tasks/{tid}/links/{link["link_id"]}',
                         json={"state": "present", "setup_pending": True},
                         headers={**auth("runner-test"), "Idempotency-Key": uuid.uuid4().hex})
    assert response.status_code == 200, response.text
    # The server marks known trees unknown when worktree capability is temporarily absent.
    response = post(api, "runners/heartbeat", {
        "version": "0.2.35", "platform": "darwin",
        "readiness": {"schema_version": 1, "worktrees": False}, "worktrees": [],
    }, "runner-test")
    assert response.status_code == 200, response.text
    with api.app_state.store.read() as c:
        assert c.execute("SELECT state FROM task_links WHERE id=?", (link["link_id"],)).fetchone()[0] == "unknown"
    for _ in range(3):
        assert heartbeat(api, link, "present") == []
        with api.app_state.store.read() as c:
            row = c.execute("SELECT state,detail_json FROM task_links WHERE id=?", (link["link_id"],)).fetchone()
            detail = json.loads(row["detail_json"])
            assert row["state"] == "present"
            assert detail["setup_pending"] is True
            assert detail.get("checkout_state") is None
            assert not any(detail.get(k) for k in ("expected_head", "checkout_target", "expected_base"))
