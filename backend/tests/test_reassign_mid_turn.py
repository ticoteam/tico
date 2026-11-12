"""One task has one turn of its work at a time, keyed on the task, not on the bot (#171). Beside
test_one_turn_per_task.py, which keeps one bot to one turn per task, this keeps two bots to one.

Nov 12 stress run: a task's Build turn was out with one Builder when a person reassigned it to a second Builder. The
reassign canceled only wakes no run had taken yet, so the second Builder's wake started a run beside the first: two
Build sessions, two pull requests. Now the claim holds the new owner's turn until the first has ended. Synthetic rows only: the test company's bots on one test computer.
"""

from backend import execution
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers.api import assign, claim, post, ready, runner


def _setup(api):
    machine = runner(api, "ana")
    for bot in ("ops", "cpo"):
        assign(api, machine, bot)
    ready(api, machine, ["ops", "cpo"])
    return machine


def _bot(api, aid):
    with api.app.state.store.read() as c:
        return c.execute("SELECT bot_id FROM attempts WHERE id=?", (aid,)).fetchone()[0]


def _version(api, tid):
    with api.app.state.store.read() as c:
        return E.task(c, tid)["version"]


def test_reassigning_a_task_while_its_owners_turn_is_out_starts_one_turn_not_two(api):
    machine = _setup(api)
    token = machine["token"]
    tid = post(api, "tasks", {"owner": "ops", "title": "Fix the billing line", "body": "Build it."})["id"]
    first = claim(api, machine)
    assert first and _bot(api, first["id"]) == "ops"
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t-" + first["id"]}, token)
    # A person hands the task to another bot while the first one's turn is under way.
    post(api, f"tasks/{tid}/owner", {"owner": "cpo", "why": "cpo builds this", "version": _version(api, tid)})
    with api.app.state.store.read() as c:
        queued = c.execute("SELECT id FROM jobs WHERE bot_id='cpo' AND state='queued'").fetchone()
        assert queued, "the new owner is woken"
    assert claim(api, machine) is None, "no second turn on the task while the first is out"
    with api.app.state.store.read() as c:
        assert execution.bot_claimable(c, api.app.state.store.settings, machine["runner_id"], "cpo")[0] == (
            "task_turn_out"
        )
    post(api, f"attempts/{first['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, token)
    second = claim(api, machine)
    assert second and _bot(api, second["id"]) == "cpo", "the new owner's turn starts once the first has ended"


def test_a_bot_that_hands_its_own_task_on_keeps_its_turn_and_the_new_owner_waits_for_it(api):
    """A bot reassigning its own task from its turn: the new owner starts only once that turn has ended."""
    machine = _setup(api)
    token = machine["token"]
    tid = post(api, "tasks", {"owner": "ops", "title": "Plan the week", "body": "Draft it."})["id"]
    first = claim(api, machine)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t-" + first["id"]}, token)
    with api.app.state.store.transaction() as c:
        reset = H.RUN.set(first["id"])
        try:
            E.reassign(c, tid, "bot:cpo", "bot:ops", "cpo plans this", E.task(c, tid)["version"])
        finally:
            H.RUN.reset(reset)
    assert claim(api, machine) is None
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "last_seq": 0}, token)
    second = claim(api, machine)
    assert second and _bot(api, second["id"]) == "cpo"


def test_a_bot_asked_on_the_task_runs_beside_the_owner(api):
    """A turn the task still gives another bot (asked for an OK or an answer) never holds the owner's: only a turn
    that moved on to someone else does."""
    machine = _setup(api)
    token = machine["token"]
    tid = post(api, "tasks", {"owner": "ops", "title": "Price the plan", "body": "Draft it."})["id"]
    first = claim(api, machine)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t-" + first["id"]}, token)
    post(api, f"tasks/{tid}/asks", {"to": "cpo", "line": "Which tier?", "version": _version(api, tid)})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM jobs WHERE bot_id='cpo' AND state='queued'").fetchone()
    second = claim(api, machine)
    assert second and _bot(api, second["id"]) == "cpo"
