"""A bot's task-keyed turns run side by side in their own lanes, only on a runner that runs lanes."""
import json

from backend.store import H
from backend.tests.test_api import api, assign, claim, get, post, runner  # noqa: F401  (api is a fixture)


def task_bot(api, lanes, capability="task_lanes_v2"):
    """`ops` keyed on tasks, assigned to a runner that does or does not advertise lanes."""
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0])
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'", (json.dumps({**config, "session": "task"}),))
    machine = runner(api)
    assign(api, machine, "ops")
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {"ops": True},
                                    "capabilities": [capability] if lanes else []}, token=machine["token"])
    return machine


def new_task(api, title):
    return post(api, "tasks", {"owner": "ops", "title": title, "body": "Please do " + title + "."})


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
    assert post(api, "jobs/claim", {"busy_lanes": ["ops|task:" + third["id"]]}, token=machine["token"])["attempt"] is None
    assert claim(api, machine)["lane"] == "task:" + third["id"]


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
    post(api, "bots/ops/stop", {"attempt_id": two["id"]})
    post(api, f"attempts/{one['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."},
         machine["token"])
    with api.app.state.store.read() as c:
        status = H.status(c, "ops")
    # No count and no task: the other turn may be on a task this status's readers cannot see.
    assert (status["state"], status["focus"], status["task_id"]) == ("running", "Working on other tasks", "")
    post(api, f"attempts/{two['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, machine["token"])
    with api.app.state.store.read() as c:
        assert H.status(c, "ops")["state"] == "idle"
    assert get(api, "bots/ops/execution-review")["jobs"] == []


def test_a_usage_limit_in_one_lane_holds_the_bot_when_another_lane_finishes(api):
    machine = task_bot(api, lanes=True)
    new_task(api, "first"), new_task(api, "second")
    one, two = claim(api, machine), claim(api, machine)
    for attempt in (one, two):
        post(api, f"attempts/{attempt['id']}/started", {"thread_id": attempt["lane"]}, machine["token"])
    post(api, f"attempts/{one['id']}/complete", {"outcome": "failed", "last_seq": 0, "limited": True,
                                                 "text": "You've hit your usage limit."}, machine["token"])
    post(api, f"attempts/{two['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."},
         machine["token"])
    with api.app.state.store.read() as c:
        assert H.status(c, "ops")["state"] == "limited"
    assert claim(api, machine) is None, "the limited lane's job waits out the cooldown"


def test_an_older_runner_advertising_retired_assignment_capabilities_still_heartbeats_and_claims(api):
    """Temporary assignments are gone (#420); a runner from before still names them in its heartbeat."""
    machine = runner(api)
    assign(api, machine, "ops")
    post(api, "runners/heartbeat", {"version": "old", "platform": "test", "readiness": {"ops": True},
                                    "capabilities": ["assignment_instances_v1", "assignment_cleanup_v1", "task_lanes_v1"]},
         token=machine["token"])
    post(api, "chat/ops", {"text": "Still there?"})
    assert claim(api, machine)["bot"] == "ops"
