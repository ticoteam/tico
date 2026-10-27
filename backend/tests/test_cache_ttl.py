"""A task's Claude prompt-cache lifetime belongs to its session (tasks.cache_ttl).

A subtask that continues the parent uses the session root's value. A subtask that is its own
session keeps its own. The runner never applies this to subagents inside the session.
"""

from backend.store import H
from backend.tests.test_api import api, claim, get, post  # noqa: F401
from backend.tests.test_subtask_sessions import roots_runner, tasks


def set_ttl(api, tid, ttl):
    version = get(api, "tasks/" + tid)["task"]["version"]
    body = post(api, "tasks/" + tid, {"version": version, "cache_ttl": ttl})
    return body["task"] if isinstance(body, dict) and "task" in body and "id" not in body else body


def finish(api, machine, attempt, text):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-parent"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete",
         {"outcome": "completed", "last_seq": 0, "text": text}, machine["token"])


def test_the_session_root_sets_the_lifetime_and_an_own_session_keeps_its_own(api):
    machine = roots_runner(api, ["task_lanes_v2", "session_roots_v1"])
    parent, shared = tasks(api, ("bot:ops", None), ("bot:ops", 0))
    assert set_ttl(api, parent, "1h")["cache_ttl"] == "1h"
    assert set_ttl(api, shared, "5m")["cache_ttl"] == "5m"
    version = get(api, "tasks/" + parent)["task"]["version"]
    post(api, "tasks/" + parent, {"version": version, "cache_ttl": "12h"}, expected=422)
    first = claim(api, machine)
    assert first["task"]["id"] == parent and first["task"]["cache_ttl"] == "1h"
    finish(api, machine, first, "Planned.")
    turn = claim(api, machine)
    assert turn["task"]["id"] == shared and turn["task"]["session_task_id"] == parent
    assert turn["task"]["cache_ttl"] == "1h", "a subtask in the parent's session uses the parent's lifetime"
    finish(api, machine, turn, "Shared step drafted.")
    with api.app.state.store.transaction() as c:
        own = H.task_create(c, "bot:ops", "Build the export", "Its own work.", "bot:ops",
                            parent_id=parent, lint=False)["id"]
    assert set_ttl(api, own, "5m")["cache_ttl"] == "5m"
    own_turn = claim(api, machine)
    assert own_turn["task"]["id"] == own and own_turn["task"]["session_task_id"] == own
    assert own_turn["task"]["cache_ttl"] == "5m"
    assert set_ttl(api, parent, "")["cache_ttl"] is None
