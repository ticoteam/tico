"""A task's turn gets a run of its own. A person's message about the task claimed ahead of it (a person's chat goes
first) is answered in its own run, and the task's wake is never steered into that run, where it read as news and the
run ended with the stage's work not begun. It starts the next run. A run started by the task's turn still takes the
person's messages as they come.

Synthetic rows only: the fictional company's bot `ops` and Ana."""

from backend import task_engine as E
from backend import task_wakes
from backend.store import H
from backend.tests.helpers.api import assign, claim, post, ready, runner

TURN = "PP-06 is in Draft. Make the cut."


def _start(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    return machine


def _task(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Make the cut", "body": "From the storyboard."})
    with api.app.state.store.transaction() as c:
        H.event(c, H.TICO_ACTOR, "task.room", task["id"], {})
        room = E.task(c, task["id"])["conversation_id"]
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    return task, room


def _turn(api, task_id):
    """The task's turn, as a stage entered hands it to the owner bot."""
    with api.app.state.store.transaction() as c:
        row = E.task(c, task_id)
        return task_wakes.add(c, row, "bot:ops", row["visit"], TURN, try_n=1)


def _inputs(api, machine, attempt):
    return [m["id"] for m in post(api, f"attempts/{attempt['id']}/inputs", {}, machine["token"])["messages"]]


def _finish(api, machine, attempt):
    body = {"outcome": "completed", "last_seq": 0, "text": "Yes, you approved it."}
    post(api, f"attempts/{attempt['id']}/complete", body, machine["token"], key="done")


def test_a_persons_question_runs_first_and_the_tasks_turn_runs_next(api):
    machine = _start(api)
    task, room = _task(api)
    wake = _turn(api, task["id"])
    asked = post(api, f"conversations/{room}/messages", {"text": "Did you get my OK?"})["message"]["id"]
    chat = claim(api, machine)
    assert chat["message"]["id"] == asked  # a person's chat goes first
    post(api, f"attempts/{chat['id']}/started", {"thread_id": "fixture-thread"}, machine["token"])
    assert wake not in _inputs(api, machine, chat)
    _finish(api, machine, chat)
    work = claim(api, machine)
    assert work["message"]["id"] == wake
    assert TURN in work["message"]["body"]


def test_a_run_started_by_the_tasks_turn_takes_a_persons_message(api):
    machine = _start(api)
    task, room = _task(api)
    wake = _turn(api, task["id"])
    work = claim(api, machine)
    assert work["message"]["id"] == wake
    post(api, f"attempts/{work['id']}/started", {"thread_id": "fixture-thread"}, machine["token"])
    asked = post(api, f"conversations/{room}/messages", {"text": "Did you get my OK?"})["message"]["id"]
    assert asked in _inputs(api, machine, work)
