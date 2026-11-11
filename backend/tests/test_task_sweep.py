"""BotOps starts another bot's stalled task now (run-now); another bot may not. The stuck-task list went with the old
engine (the plan's "Routes that go"): Late on a stage is how a task that sat too long is seen now."""

from backend.store import H
from backend.tests.helpers.api import headers, post  # noqa: F401
from backend.tests.helpers.tasks_board import bot_token


def test_botops_may_start_another_bots_task_and_other_bots_may_not(api, monkeypatch):
    task = post(api, "tasks", {"owner": "cpo", "title": "Write the pricing brief", "body": "x"})
    monkeypatch.setattr(H, "FLEET_MAINTAINER", "ops")
    token = bot_token(api, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='cpo'")
    r = api.post(f"/api/v2/tasks/{task['id']}/run-now", json={}, headers=headers(token))
    assert r.status_code == 200 and r.json()["queued"] is True, r.text
    monkeypatch.setattr(H, "FLEET_MAINTAINER", "botops")
    assert api.post(f"/api/v2/tasks/{task['id']}/run-now", json={}, headers=headers(token)).status_code == 403
