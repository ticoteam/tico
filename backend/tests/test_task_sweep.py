"""One sweep for stuck tasks: instead of a daily run per bot, BotOps lists
every bot's open work that has not moved in a day and waits on nobody, and starts it."""

from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_tasks_board import bot_token


def age(api, tid, hours=30):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), seconds=-hours * 3600), tid))


def stuck(api, token="ana-test"):
    return [t["id"] for t in get(api, "tasks/stuck", token=token)["tasks"]]


def test_botops_may_sweep_and_start_another_bots_task_and_other_bots_may_not(api, monkeypatch):
    task = post(api, "tasks", {"owner_id": "cpo", "title": "Write the pricing brief", "body": "x"})
    age(api, task["id"])
    monkeypatch.setattr(H, "FLEET_MAINTAINER", "ops")
    token = bot_token(api, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='cpo'")
    assert task["id"] in stuck(api, token)
    r = api.post(f"/api/v2/tasks/{task['id']}/run-now", json={}, headers=headers(token))
    assert r.status_code == 200 and r.json()["queued"] is True, r.text
    monkeypatch.setattr(H, "FLEET_MAINTAINER", "botops")
    assert api.get("/api/v2/tasks/stuck", headers=headers(token)).status_code == 403
    assert api.post(f"/api/v2/tasks/{task['id']}/run-now", json={}, headers=headers(token)).status_code == 403
