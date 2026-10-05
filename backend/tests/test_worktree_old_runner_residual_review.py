"""Old-runner heartbeat and human-requested restore compatibility during rollout."""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.tests.test_worktrees import api, gh, prepared, post, auth  # noqa: F401


def heartbeat(api, link, state=None):
    report = [] if state is None else [{"link_id": link["link_id"], "state": state,
                                        "branch": link["branch"], "repo": "Acme/product"}]
    response = post(api, "runners/heartbeat", {
        "version": "0.2.35", "platform": "darwin",
        "readiness": {"schema_version": 1, "worktrees": True}, "worktrees": report,
    }, "runner-test")
    assert response.status_code == 200, response.text
    return response.json()["worktree_actions"]


@pytest.mark.parametrize(("saved_state", "report_state"), [("present", "missing"), ("unknown", "present")])
def test_old_heartbeat_retains_legacy_missing_and_unknown_semantics(prepared, saved_state, report_state):
    api, tid, _ = prepared
    link = post(api, f"tasks/{tid}/worktrees", {"repo": "Acme/product"}).json()
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state=?,detail_json=? WHERE id=?", (
            saved_state, json.dumps({"owner": "bot:cmo", "missing_since": old}), link["link_id"]))
    for _ in range(3):
        assert heartbeat(api, link, report_state) == []
        with api.app_state.store.read() as c:
            row = c.execute("SELECT state,detail_json FROM task_links WHERE id=?", (link["link_id"],)).fetchone()
            detail = json.loads(row["detail_json"])
            assert row["state"] == report_state
            assert not any(detail.get(k) for k in ("expected_head", "checkout_target", "expected_base"))
            assert detail.get("checkout_state") is None
    with api.app_state.store.read() as c:
        wakes = c.execute("SELECT count(*) FROM messages WHERE body LIKE 'Worktree missing for a day:%'").fetchone()[0]
        assert wakes == (1 if report_state == "missing" else 0)
        if report_state == "missing":
            assert detail["missing_since"] == old


@pytest.mark.parametrize("new_request", [False, True])
def test_old_human_restore_and_new_request_accept_setup_report_without_repeating(prepared, new_request):
    api, tid, _ = prepared
    link = post(api, f"tasks/{tid}/worktrees", {"repo": "Acme/product"}).json()
    if not new_request:
        with api.app_state.store.transaction() as c:
            c.execute("UPDATE task_links SET state='pending',detail_json=? WHERE id=?",
                      (json.dumps({"owner": "bot:cmo"}), link["link_id"]))
    actions = heartbeat(api, link)
    assert len(actions) == 1 and actions[0]["action"] == "restore"
    response = api.patch(f'/api/v2/tasks/{tid}/links/{link["link_id"]}',
                         json={"state": "present", "setup_pending": True},
                         headers={**auth("runner-test"), "Idempotency-Key": uuid.uuid4().hex})
    assert response.status_code == 200, response.text
    for _ in range(3):
        assert heartbeat(api, link, "present") == []
        with api.app_state.store.read() as c:
            row = c.execute("SELECT state,detail_json FROM task_links WHERE id=?", (link["link_id"],)).fetchone()
            detail = json.loads(row["detail_json"])
            assert row["state"] == "present"
            assert detail["setup_pending"] is True
            assert detail.get("checkout_state") != "ready"
            assert not any(detail.get(k) for k in ("expected_head", "checkout_target", "expected_base"))
