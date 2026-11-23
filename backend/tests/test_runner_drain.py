"""A runner update returns unstarted claims, then resumes capped turns without publishing partial answers."""

from backend import execution, runner_versions
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, post, ready, runner  # noqa: F401
from backend.tests.helpers.task_lanes import RUNNER_CAPABILITIES


def test_drain_requeues_three_unstarted_claims_and_waits_only_for_the_started_turn(api):
    machine = runner(api)
    bots = ("ops", "finance", "doc-updater", "inbox")
    attempts, tasks = [], []
    for bot in bots:
        assign(api, machine, bot)
    ready(api, machine, bots, capabilities=RUNNER_CAPABILITIES)
    for bot in bots:
        tasks.append(
            post(api, "tasks", {"owner": bot, "title": "Check the synthetic report", "body": "Use only fixture data."})
        )
        attempts.append(claim(api, machine, bot))
    running = attempts[-1]
    post(api, f"attempts/{running['id']}/started", {"thread_id": "midturn"}, machine["token"])
    for attempt in attempts[:3]:
        line = "Handed back before starting because the computer is updating; queued to continue"
        post(
            api,
            f"attempts/{attempt['id']}/events",
            {"events": [{"seq": 1, "kind": "status", "payload": {"yielded": line}}]},
            machine["token"],
        )
        post(
            api,
            f"attempts/{attempt['id']}/complete",
            {"outcome": "interrupted", "yielded": True, "last_seq": 1},
            machine["token"],
        )
        turn = next(t for t in get(api, f"bots/{attempt['bot']}/turns") if t["id"] == attempt["id"])
        assert turn["end_reason"] == line
    with api.app.state.store.read() as c:
        assert [c.execute("SELECT state FROM jobs WHERE id=?", (a["job_id"],)).fetchone()[0] for a in attempts] == [
            "queued",
            "queued",
            "queued",
            "running",
        ]
        assert [
            c.execute("SELECT started_at FROM attempts WHERE id=?", (a["id"],)).fetchone()[0] for a in attempts[:3]
        ] == [None, None, None]
        # Updating is a planned yield, so repeated updates cannot exhaust the pre-start failure budget.
        assert [c.execute("SELECT state FROM attempts WHERE id=?", (a["id"],)).fetchone()[0] for a in attempts[:3]] == [
            "interrupted",
            "interrupted",
            "interrupted",
        ]
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "release": "0.6.0-rc.1",
            "readiness": {b: True for b in bots},
            "update": {"state": "waiting", "running": 1, "handed_back": 3},
        },
        machine["token"],
    )
    assert (
        get(api, f"tasks/{tasks[0]['id']}")["waiting_for"]
        == "Waiting for the computer to update: 1 running, 3 handed back"
    )
    with api.app.state.store.read() as c:
        assert runner_versions.holding_since(c, machine["runner_id"])
    post(api, f"attempts/{running['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    # The replacement runner reports no active drain; the three original jobs can be claimed again.
    ready(api, machine, bots, capabilities=RUNNER_CAPABILITIES)
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "release": "0.6.0-rc.1",
            "readiness": {b: True for b in bots},
            "update": {"state": "idle"},
        },
        machine["token"],
    )
    for bot, attempt in zip(bots[:3], attempts[:3]):
        assert claim(api, machine, bot)["job_id"] == attempt["job_id"]
    assert "waiting for the computer" not in get(api, f"tasks/{tasks[0]['id']}")["waiting_for"].lower()


def test_a_capped_turn_resumes_its_session_without_checkpointing_partial_output_and_stop_still_wins(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"], capabilities=RUNNER_CAPABILITIES)
    task = post(api, "tasks", {"owner": "ops", "title": "Prepare a synthetic report", "body": "Use only fixture data."})
    attempt = claim(api, machine, "ops")
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "saved-thread"}, machine["token"])
    line = "Paused at the computer's drain time limit; work and session saved, queued to continue"
    post(
        api,
        f"attempts/{attempt['id']}/events",
        {
            "events": [
                {"seq": 1, "kind": "message", "payload": {"text": "Unfinished analysis"}},
                {"seq": 2, "kind": "status", "payload": {"yielded": line}},
            ]
        },
        machine["token"],
    )
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "interrupted", "yielded": True, "text": "", "last_seq": 2},
        machine["token"],
    )
    turn = next(t for t in get(api, "bots/ops/turns") if t["id"] == attempt["id"])
    assert turn["end_reason"] == line
    again = claim(api, machine, "ops")
    assert again["job_id"] == attempt["job_id"]
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT checkpoint FROM tasks WHERE id=?", (task["id"],)).fetchone()[0]
        saved = c.execute("SELECT thread_id,final_text FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
        assert tuple(saved) == ("saved-thread", "")
    post(api, "bots/ops/stop", {"run_id": again["id"]})
    post(
        api,
        f"attempts/{again['id']}/complete",
        {"outcome": "interrupted", "yielded": True, "last_seq": 0},
        machine["token"],
    )
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (again["job_id"],)).fetchone()[0] == "canceled"
    turn = next(t for t in get(api, "bots/ops/turns") if t["id"] == again["id"])
    assert turn["end_reason"] == "Canceled by human:ana"


def test_only_three_planned_returns_bypass_pre_start_backoff(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"], capabilities=RUNNER_CAPABILITIES)
    post(api, "tasks", {"owner": "ops", "title": "Check the report", "body": "Use fixture data."})
    attempt = claim(api, machine, "ops")
    job_id = attempt["job_id"]
    aid = attempt["id"]
    # A lost completion still uses one of the three hand-backs when the runner's yield event reached Tico.
    post(
        api,
        f"attempts/{aid}/events",
        {
            "events": [
                {"seq": 1, "kind": "status", "payload": {"yielded": "Draining before start", "planned_drain": True}},
            ]
        },
        machine["token"],
    )
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.shift(H.now(), seconds=-120), aid))
        api.app.state.execution.expire(c, attempt_ids=[aid])
        assert execution.failed_starts(c, job_id) == 0
    for failed in (0, 0, 1):
        again = claim(api, machine, "ops")
        assert again["job_id"] == job_id and again["id"] != aid
        aid = again["id"]
        post(
            api,
            f"attempts/{aid}/events",
            {
                "events": [
                    {"seq": 1, "kind": "status", "payload": {"yielded": "Draining before start", "planned_drain": True}}
                ]
            },
            machine["token"],
        )
        post(
            api,
            f"attempts/{aid}/complete",
            {"outcome": "interrupted", "yielded": True, "last_seq": 1},
            machine["token"],
        )
        with api.app.state.store.read() as c:
            assert execution.failed_starts(c, job_id) == failed
    assert claim(api, machine, "ops") is None


def test_legacy_runner_can_keep_yielding_without_spending_the_planned_drain_budget(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"], capabilities=RUNNER_CAPABILITIES)
    post(api, "tasks", {"owner": "ops", "title": "Check the report", "body": "Use fixture data."})
    job = None
    for _ in range(5):
        attempt = claim(api, machine, "ops")
        job = job or attempt["job_id"]
        assert attempt["job_id"] == job
        post(
            api,
            f"attempts/{attempt['id']}/complete",
            {"outcome": "interrupted", "yielded": True, "last_seq": 0},
            machine["token"],
        )
    with api.app.state.store.read() as c:
        assert execution.failed_starts(c, job) == 0


def test_stopping_a_planned_hand_back_before_its_lease_expires_preserves_the_reason(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"], capabilities=RUNNER_CAPABILITIES)
    post(api, "tasks", {"owner": "ops", "title": "Check the report", "body": "Use fixture data."})
    attempt = claim(api, machine, "ops")
    aid = attempt["id"]
    post(
        api,
        f"attempts/{aid}/events",
        {"events": [{"seq": 1, "kind": "status", "payload": {"yielded": "Draining", "planned_drain": True}}]},
        machine["token"],
    )
    post(api, "bots/ops/stop", {"run_id": aid})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.shift(H.now(), seconds=-120), aid))
        api.app.state.execution.expire(c, attempt_ids=[aid])
        job_id = attempt["job_id"]
        assert c.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "canceled"
        assert execution.failed_starts(c, job_id) == 0
