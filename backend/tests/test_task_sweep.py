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


def test_run_now_on_a_snoozed_task_ends_the_snooze_in_the_pressers_name_and_runs_it(api):
    """Tico Team, Nov 11: Run now on a snoozed task answered queued false and left it snoozed, so nothing ran."""
    from backend import task_wakes

    task = post(api, "tasks", {"owner": "cpo", "title": "Write the pricing brief", "body": "x"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='cpo'")
    post(api, f"tasks/{task['id']}/snooze", {"until": "2099-01-05", "why": "After the release"})
    out = post(api, f"tasks/{task['id']}/run-now", {})
    assert out["queued"] is True and out["task"]["snooze"] is None
    with api.app.state.store.transaction() as c:
        assert c.execute("SELECT ended_by_id FROM snoozes WHERE task_id=?", (task["id"],)).fetchone()[0] == "human:ana"
        (message,) = c.execute("SELECT message_id FROM jobs WHERE bot_id='cpo' AND state='queued'").fetchall()
        assert task_wakes.may_start(c, message[0])  # the engine lets the run start
