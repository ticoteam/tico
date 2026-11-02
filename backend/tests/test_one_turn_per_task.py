"""At most one live turn per bot and task: while a bot's turn on a task is out, every later wake for that bot on that
task (a person's comment, a keeper notice) goes into that turn or waits for the next one on the task. It never starts
a second turn there, even when the task's lane changed under the turn. The bot's own comment wakes only the other side,
whose turn on the task is its own. The same bot's other tasks still run beside it."""
from backend.store import H
from backend.tests.test_api import api, claim, post  # noqa: F401  (api is a fixture)
from backend.tests.test_task_lanes import new_task, task_bot


def ops_turns(api, task_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM turns WHERE bot='ops' AND task_id=?", (task_id,)).fetchone()[0]


def queued(api, bot="ops"):
    with api.app.state.store.read() as c:
        return [m for (m,) in c.execute("SELECT message_id FROM jobs WHERE bot=? AND state='queued' ORDER BY created_at",
                                        (bot,))]


def inputs(api, machine, attempt):
    return [m["id"] for m in post(api, f"attempts/{attempt['id']}/inputs", {}, machine["token"])["messages"]]


def busy_on(api, machine, task_id):
    """ops's turn on `task_id`, started."""
    active = claim(api, machine)
    assert (active["bot"], H.message_task_id(active["message"])) == ("ops", task_id)
    post(api, f"attempts/{active['id']}/started", {"thread_id": active["lane"]}, machine["token"])
    return active


def test_wakes_on_a_task_with_a_live_turn_join_it_and_only_the_other_side_starts_its_own(api):
    machine = task_bot(api, lanes=True, others=("finance",))
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:finance", "Install", "Install the admin page.", "bot:ops", lint=False)
    active = busy_on(api, machine, task["id"])
    # The owner's status comment wakes its requester, another bot, whose turn on the task is its own; never the owner.
    status = post(api, f"tasks/{task['id']}/comments", {"text": "Install started; receipt follows.", "needs_action": True},
                  token=active["token"])["comment"]
    assert queued(api) == [] and queued(api, "finance") == [status["id"]]
    assert (claim(api, machine) or {}).get("bot") == "finance"
    # A person's comment and Tico's own notice wake the owner; while its turn runs, each waits for that turn.
    person = post(api, f"tasks/{task['id']}/comments", {"text": "One installer only."})["comment"]
    with api.app.state.store.transaction() as c:
        notice = H._wake(c, H.task(c, task["id"]), "bot:ops", "A related task moved.", {"wake": "update"})
    assert queued(api) == [person["id"], notice["id"]] and claim(api, machine) is None
    assert inputs(api, machine, active) == [person["id"], notice["id"]]
    assert ops_turns(api, task["id"]) == 1
    # The same bot's other task still starts beside it.
    other = new_task(api, "release notes")
    assert (claim(api, machine) or {}).get("lane") == "task:" + other["id"]


def test_a_task_moved_under_another_mid_turn_still_gets_no_second_turn(api):
    machine = task_bot(api, lanes=True)
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capacity": 8, "readiness": {"ops": True},
                                    "capabilities": ["task_lanes_v2", "session_roots_v1"]}, token=machine["token"])
    parent = new_task(api, "launch")
    first = busy_on(api, machine, parent["id"])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Planned."},
         machine["token"])
    step = new_task(api, "install")
    active = busy_on(api, machine, step["id"])
    assert active["lane"] == "task:" + step["id"]
    # Filed under the bot's other task, its later wakes run in that task's lane, which is free: they still wait.
    post(api, f"tasks/{step['id']}/relations", {"task": parent["id"], "kind": "parent"})
    person = post(api, f"tasks/{step['id']}/comments", {"text": "Use the owner-only transport."})["comment"]
    assert person["id"] in queued(api)
    assert claim(api, machine) is None
    assert ops_turns(api, step["id"]) == 1
    # Held, not dropped: the next turn on the task starts once this one ends.
    post(api, f"attempts/{active['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."},
         machine["token"])
    after = claim(api, machine)
    assert (after["lane"], H.message_task_id(after["message"])) == ("task:" + parent["id"], step["id"])


def test_a_wake_the_live_turn_never_took_is_the_next_turn_on_the_task(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "install")
    active = busy_on(api, machine, task["id"])
    person = post(api, f"tasks/{task['id']}/comments", {"text": "Use the owner-only transport."})["comment"]
    assert claim(api, machine) is None
    # The turn ends before it asked for inputs (a runtime that cannot steer): the comment is not lost.
    post(api, f"attempts/{active['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."},
         machine["token"])
    after = claim(api, machine)
    assert (after["lane"], after["message"]["id"]) == ("task:" + task["id"], person["id"])
    assert ops_turns(api, task["id"]) == 2
