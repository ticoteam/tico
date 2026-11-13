"""A bot's task-keyed turns run side by side in their own lanes, only on a runner that runs lanes."""

import json

import pytest

from backend.store import H
from backend.tests.helpers.api import assign, claim, get, post, runner  # noqa: F401  (api is a fixture)
from backend.tests.helpers.task_lanes import new_task, task_bot


def test_task_lanes_run_side_by_side_and_each_lane_stays_serial(api):
    machine = task_bot(api, lanes=True)
    first, second = new_task(api, "first"), new_task(api, "second")
    one, two = claim(api, machine), claim(api, machine)
    assert {one["lane"], two["lane"]} == {"task:" + first["id"], "task:" + second["id"]}
    # A chat with no task is the main lane, free beside them; a second chat waits behind it.
    post(api, "chat/ops", {"text": "Quick question."})
    chat = claim(api, machine)
    assert chat["lane"] == "main"
    post(api, "chat/ops", {"text": "And another."})
    assert claim(api, machine) is None
    # The runner's own busy list holds a lane the server does not know is taken yet.
    third = new_task(api, "third")
    assert (
        post(api, "jobs/claim", {"busy_lanes": ["ops|task:" + third["id"]]}, token=machine["token"])["attempt"] is None
    )
    assert claim(api, machine)["lane"] == "task:" + third["id"]


def test_a_bot_uses_all_places_and_next_place_goes_to_the_bot_with_fewest(api):
    machine = task_bot(api, lanes=True, capacity=2, others=("finance",))
    tasks = [new_task(api, title) for title in ("first", "second", "third", "fourth", "fifth")]
    # No per-bot cap or permanently empty place: every short start may be used. Twice the two places is the claim
    # bound, so four turns lease and the fifth task waits for a free place.
    assert [claim(api, machine)["bot"] for _ in range(4)] == ["ops"] * 4
    assert claim(api, machine) is None
    assert get(api, "tasks/" + tasks[4]["id"])["waiting_for"] == "Waiting for a free turn on Test Mac"
    review = new_task(api, "review", owner="finance")
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capacity": 8,
            "readiness": {"ops": True, "finance": True},
            "capabilities": ["task_lanes_v2"],
        },
        token=machine["token"],
    )
    assert claim(api, machine)["bot"] == "finance"
    assert get(api, "tasks/" + review["id"])["waiting_for"] == ""
    post(api, "chat/ops", {"text": "Quick question."})
    assert claim(api, machine)["lane"] == "main"
    new_task(api, "release", owner="finance")
    assert claim(api, machine)["bot"] == "finance"
    assert claim(api, machine)["lane"] == "task:" + tasks[4]["id"]


def test_a_runner_without_lanes_runs_one_turn_per_bot(api):
    machine = task_bot(api, lanes=False)
    new_task(api, "first"), new_task(api, "second")
    assert claim(api, machine)["lane"] == "main"
    assert claim(api, machine) is None


def test_a_main_built_runner_advertising_task_lanes_v1_runs_serially(api):
    """Its session key reads an unset `session` as the bot thread, so its lanes would share one thread."""
    machine = task_bot(api, lanes=True, capability="task_lanes_v1")
    new_task(api, "first"), new_task(api, "second")
    assert claim(api, machine)["lane"] == "main"
    assert claim(api, machine) is None


def test_a_finished_lane_leaves_the_bot_running_and_stop_names_the_turn(api):
    machine = task_bot(api, lanes=True)
    new_task(api, "first"), new_task(api, "second")
    one, two = claim(api, machine), claim(api, machine)
    for attempt in (one, two):
        post(api, f"attempts/{attempt['id']}/started", {"thread_id": attempt["lane"]}, machine["token"])
    # Two turns out: a stop without the attempt would be a guess.
    refused = post(api, "bots/ops/stop", {}, expected=409)
    assert refused["error"]["code"] == "ambiguous"
    post(api, "bots/ops/stop", {"run_id": two["id"]})
    post(
        api,
        f"attempts/{one['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Done."},
        machine["token"],
    )
    with api.app.state.store.read() as c:
        status = H.status(c, "ops")
    # No count and no task: the other turn may be on a task this status's readers cannot see.
    assert (status["state"], status["focus"], status["task_id"]) == ("running", "Working on other tasks", "")
    post(api, f"attempts/{two['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, machine["token"])
    with api.app.state.store.read() as c:
        assert H.status(c, "ops")["state"] == "idle"
    assert get(api, "bots/ops/execution-review")["jobs"] == []


@pytest.mark.parametrize("success_first", [False, True])
def test_a_quota_reset_in_one_lane_holds_when_a_pre_limit_lane_finishes(api, monkeypatch, success_first):
    from backend.execution import cooling_off

    machine = task_bot(api, lanes=True)
    new_task(api, "first"), new_task(api, "second")
    one, two = claim(api, machine), claim(api, machine)
    for attempt in (one, two):
        post(api, f"attempts/{attempt['id']}/started", {"thread_id": attempt["lane"]}, machine["token"])
    now = H.now()
    reset = H.shift(now, days=4)[:19] + "Z"

    def finish_other():
        post(
            api,
            f"attempts/{two['id']}/complete",
            {"outcome": "completed", "last_seq": 0, "text": "Done."},
            machine["token"],
        )

    if success_first:
        finish_other()
    post(
        api,
        f"attempts/{one['id']}/events",
        {
            "events": [
                {
                    "seq": 1,
                    "kind": "error",
                    "payload": {
                        "error": f"Subscription quota exhausted. Your usage window resets at {reset}.",
                        "limit": True,
                    },
                }
            ]
        },
        machine["token"],
    )
    post(api, f"attempts/{one['id']}/complete", {"outcome": "failed", "last_seq": 1, "limited": True}, machine["token"])
    if not success_first:
        finish_other()
    monkeypatch.setattr(H, "now", lambda: H.shift(now, hours=3))
    with api.app.state.store.read() as c:
        assert H.status(c, "ops")["state"] == "limited"
        assert cooling_off(c, "ops"), "a pre-limit lane finishing cannot discard the provider's reset"


@pytest.mark.parametrize("restore", [False, True])
def test_a_pre_limit_lane_start_or_restore_cannot_release_the_cooldown(api, monkeypatch, restore):
    from backend.execution import cooling_off

    machine = task_bot(api, lanes=True)
    for title in ("first", "second", "queued"):
        new_task(api, title)
    one, two = claim(api, machine), claim(api, machine)
    before = H.now()
    if restore:
        post(api, f"attempts/{two['id']}/started", {"thread_id": two["lane"]}, machine["token"])
        # A lease which lapsed while its worker continued; the runner still owns its lane.
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE attempts SET state='expired',finished_at=? WHERE id=?", (before, two["id"]))
            c.execute("UPDATE jobs SET state='uncertain' WHERE id=?", (two["job_id"],))
    monkeypatch.setattr(H, "now", lambda: H.shift(before, seconds=1))
    post(api, f"attempts/{one['id']}/started", {"thread_id": one["lane"]}, machine["token"])
    post(
        api,
        f"attempts/{one['id']}/complete",
        {"outcome": "failed", "last_seq": 0, "limited": True, "fallback": "codex"},
        machine["token"],
    )
    monkeypatch.setattr(H, "now", lambda: H.shift(before, seconds=2))
    if restore:
        post(api, f"attempts/{two['id']}/renew", {}, machine["token"])
    else:
        post(api, f"attempts/{two['id']}/started", {"thread_id": two["lane"]}, machine["token"])
    with api.app.state.store.read() as c:
        assert cooling_off(c, "ops"), "starting an already-leased lane must not lift another lane's limit"
    assert claim(api, machine) is None
    post(
        api,
        f"attempts/{two['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Done."},
        machine["token"],
    )
    with api.app.state.store.read() as c:
        assert cooling_off(c, "ops"), "the lane was leased before the limit even if it started afterwards"
        assert c.execute("SELECT state FROM jobs WHERE id=?", (one["job_id"],)).fetchone()[0] == "queued"
    assert claim(api, machine) is None
    # The existing explicit recovery path remains usable, without discarding the waiting work.
    post(api, "bots/ops/limit/retry", {})
    assert claim(api, machine)


def test_an_older_runner_advertising_retired_assignment_capabilities_still_heartbeats_and_claims(api):
    """Temporary assignments are gone (#420); a runner from before still names them in its heartbeat."""
    machine = runner(api)
    assign(api, machine, "ops")
    post(
        api,
        "runners/heartbeat",
        {
            "version": "old",
            "platform": "test",
            "readiness": {"ops": True},
            "capabilities": ["assignment_instances_v1", "assignment_cleanup_v1", "task_lanes_v1"],
        },
        token=machine["token"],
    )
    post(api, "chat/ops", {"text": "Still there?"})
    assert claim(api, machine)["bot"] == "ops"


def _bot_chat(api, text, minutes_ago=0):
    """A queued message to ops with no task, not in a person's name (so no chat priority), queued `minutes_ago`."""
    post(api, "chat/ops", {"text": text})
    with api.app.state.store.transaction() as c:
        job = c.execute(
            "SELECT id,message_id FROM jobs WHERE bot_id='ops' AND state='queued' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        c.execute("UPDATE messages SET sender_id='finance' WHERE id=?", (job["message_id"],))
        c.execute("UPDATE jobs SET created_at=? WHERE id=?", (H.shift(H.now(), minutes=-minutes_ago), job["id"]))


def _more_places(api, machine, capacity):
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capacity": capacity,
            "readiness": {"ops": True},
            "capabilities": ["task_lanes_v2"],
        },
        token=machine["token"],
    )


def test_a_message_with_no_task_waiting_long_is_claimed_ahead_of_newer_task_work(api):
    """Nov 13: a bot at its limit left task-less messages queued 97+ minutes while newer task jobs took every freed
    turn, since a job with no task sorts last by rank. Past QUEUE_STARVE_S it goes first."""
    machine = task_bot(api, lanes=True, capacity=1)
    new_task(api, "first"), new_task(api, "second")
    assert [claim(api, machine)["bot"] for _ in range(2)] == ["ops"] * 2
    _bot_chat(api, "Did the export finish?", minutes_ago=97)
    third = new_task(api, "third")
    new_task(api, "fourth")
    assert claim(api, machine) is None  # at its limit
    _more_places(api, machine, 2)  # a turn frees
    assert claim(api, machine)["lane"] == "main"
    assert claim(api, machine)["lane"] == "task:" + third["id"]


def test_a_fresh_message_with_no_task_still_waits_behind_task_work(api):
    machine = task_bot(api, lanes=True, capacity=1)
    new_task(api, "first"), new_task(api, "second")
    assert [claim(api, machine)["bot"] for _ in range(2)] == ["ops"] * 2
    _bot_chat(api, "Did the export finish?")
    third = new_task(api, "third")
    _more_places(api, machine, 2)
    assert claim(api, machine)["lane"] == "task:" + third["id"]
