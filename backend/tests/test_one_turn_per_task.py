"""At most one live turn per bot and task: while a bot's turn on a task is out, every later wake for that bot on that
task (a person's comment, Tico's own notice) goes into that turn or waits for the next one on the task. It never starts
a second turn there. The bot's own comment never wakes itself. The same bot's other tasks still run beside it."""

from backend.store import H
from backend.tests.helpers.api import claim, post  # noqa: F401  (api is a fixture)
from backend.tests.helpers.task_lanes import new_task, task_bot


def ops_turns(api, task_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM attempts WHERE bot_id='ops' AND task_id=?", (task_id,)).fetchone()[0]


def queued(api, bot="ops"):
    with api.app.state.store.read() as c:
        return [
            m
            for (m,) in c.execute(
                "SELECT message_id FROM jobs WHERE bot_id=? AND state='queued' ORDER BY created_at", (bot,)
            )
        ]


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
    # The owner's own status comment @mentioning its requester never wakes the owner. The requester bot has no turn on
    # the task (docs/pipelines-v3.md: only a bot whose turn it is starts a run on a task), so no run of its starts.
    post(
        api,
        f"tasks/{task['id']}/comments",
        {"text": "@finance Install started; receipt follows.", "needs_action": True},
        token=active["token"],
    )
    assert queued(api) == [] and claim(api, machine) is None
    # A person's comment and Tico's own notice wake the owner; while its turn runs, each waits for that turn.
    person = post(api, f"tasks/{task['id']}/comments", {"text": "One installer only."})["comment"]
    with api.app.state.store.transaction() as c:
        notice = H._wake(c, H.task(c, task["id"]), "bot:ops", "A related task moved.", {"wake": "update"})
    assert queued(api) == [person["id"], notice["id"]]
    # The person's words also reach the requester bot, which follows the task: that is the other side's own run (a
    # person's message always gets an answer). The owner's turn still starts nothing new.
    other_side = claim(api, machine)
    assert other_side["bot"] == "finance" and "One installer only." in other_side["message"]["body"]
    assert claim(api, machine) is None
    assert inputs(api, machine, active) == [person["id"], notice["id"]]
    assert ops_turns(api, task["id"]) == 1
    # The same bot's other task still starts beside it.
    other = new_task(api, "release notes")
    assert (claim(api, machine) or {}).get("lane") == "task:" + other["id"]


def test_a_wake_the_live_turn_never_took_is_the_next_turn_on_the_task(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "install")
    active = busy_on(api, machine, task["id"])
    person = post(api, f"tasks/{task['id']}/comments", {"text": "Use the owner-only transport."})["comment"]
    assert claim(api, machine) is None
    # The turn ends before it asked for inputs (a runtime that cannot steer): the comment is not lost.
    post(
        api,
        f"attempts/{active['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Done."},
        machine["token"],
    )
    after = claim(api, machine)
    assert (after["lane"], after["message"]["id"]) == ("task:" + task["id"], person["id"])
    assert ops_turns(api, task["id"]) == 2
