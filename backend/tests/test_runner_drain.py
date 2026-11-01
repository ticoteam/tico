"""A runner update returns unstarted claims, then resumes capped turns without publishing partial answers."""

from backend import runner_versions
from backend.tests.test_api import api, assign, claim, get, post, ready, runner  # noqa: F401


def test_drain_requeues_three_unstarted_claims_and_waits_only_for_the_started_turn(api):
    machine = runner(api)
    bots = ("ops", "finance", "doc-updater", "inbox")
    attempts, tasks = [], []
    for bot in bots:
        assign(api, machine, bot)
    ready(api, machine, bots)
    for bot in bots:
        tasks.append(post(api, "tasks", {"owner_id": bot, "title": "Check the synthetic report", "body": "Use only fixture data."}))
        attempts.append(claim(api, machine, bot))
    running = attempts[-1]
    post(api, f"attempts/{running['id']}/started", {"thread_id": "midturn"}, machine["token"])
    for attempt in attempts[:3]:
        line = "Handed back before starting because the computer is updating; queued to continue"
        post(api, f"attempts/{attempt['id']}/events", {"events": [
            {"seq": 1, "kind": "status", "payload": {"yielded": line}}]}, machine["token"])
        post(api, f"attempts/{attempt['id']}/complete",
             {"outcome": "interrupted", "yielded": True, "last_seq": 1}, machine["token"])
    with api.app.state.store.read() as c:
        assert [c.execute("SELECT state FROM jobs WHERE id=?", (a["job_id"],)).fetchone()[0]
                for a in attempts] == ["queued", "queued", "queued", "running"]
        assert [c.execute("SELECT started_at FROM attempts WHERE id=?", (a["id"],)).fetchone()[0]
                for a in attempts[:3]] == [None, None, None]
        # Updating is a planned yield, so repeated updates cannot exhaust the pre-start failure budget.
        assert [c.execute("SELECT state FROM attempts WHERE id=?", (a["id"],)).fetchone()[0]
                for a in attempts[:3]] == ["interrupted", "interrupted", "interrupted"]
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "release": "0.4.0", "readiness": {b: True for b in bots},
         "update": {"state": "waiting", "running": 1, "handed_back": 3}}, machine["token"])
    assert get(api, f"tasks/{tasks[0]['id']}")["waiting_for"] == "Waiting for the computer to update: 1 running, 3 handed back"
    with api.app.state.store.read() as c:
        assert runner_versions.holding_since(c, machine["runner_id"])
    post(api, f"attempts/{running['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    # The replacement runner reports no active drain; the three original jobs can be claimed again.
    ready(api, machine, bots)
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "release": "0.4.0", "readiness": {b: True for b in bots},
         "update": {"state": "idle"}}, machine["token"])
    for bot, attempt in zip(bots[:3], attempts[:3]):
        assert claim(api, machine, bot)["job_id"] == attempt["job_id"]
    assert "waiting for the computer" not in get(api, f"tasks/{tasks[0]['id']}")["waiting_for"].lower()


def test_a_capped_turn_resumes_its_session_without_checkpointing_partial_output_and_stop_still_wins(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    task = post(api, "tasks", {"owner_id": "ops", "title": "Prepare a synthetic report", "body": "Use only fixture data."})
    attempt = claim(api, machine, "ops")
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "saved-thread"}, machine["token"])
    line = "Paused at the computer's drain time limit; work and session saved, queued to continue"
    post(api, f"attempts/{attempt['id']}/events", {"events": [
        {"seq": 1, "kind": "message", "payload": {"text": "Unfinished analysis"}},
        {"seq": 2, "kind": "status", "payload": {"yielded": line}}]}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "interrupted", "yielded": True,
         "text": "", "last_seq": 2}, machine["token"])
    again = claim(api, machine, "ops")
    assert again["job_id"] == attempt["job_id"]
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT checkpoint FROM tasks WHERE id=?", (task["id"],)).fetchone()[0]
        saved = c.execute("SELECT thread_id,final_text FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
        assert tuple(saved) == ("saved-thread", "")
    post(api, "bots/ops/stop", {"run_id": again["id"]})
    post(api, f"attempts/{again['id']}/complete", {"outcome": "interrupted", "yielded": True,
         "last_seq": 0}, machine["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (again["job_id"],)).fetchone()[0] == "canceled"
