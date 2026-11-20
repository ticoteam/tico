"""Helpers that other tests share, moved out of test_task_readers.py so they import no test file."""

from backend import task_relations as TR
from backend.store import H
from backend.tests.helpers.api import assign, claim, headers, post, ready, runner


ASK = {"title": "Approve the zip triage plan", "body": "Please approve the triage plan: reply yes or no on this task."}


def task(api, requester, owner, title, parent=None):
    with api.app.state.store.transaction() as c:
        row = H.task_create(c, requester, title, f"Confidential: {title}.", owner, private=True, lint=False)
        if parent:
            TR._insert(c, H.TICO_ACTOR, row["id"], parent, "parent", H.now())
        return row


def run_on(api, bot="ops"):
    """A run of `bot` claimed from its queue, on the oldest work waiting for it. One computer runs the bot; its
    previous run, if any, is finished first."""
    machine = getattr(api, "machine", None)
    if machine is None:
        machine = api.machine = runner(api)
        assign(api, machine, bot)
        ready(api, machine, [bot])
    if getattr(api, "attempt", None):
        post(api, f"attempts/{api.attempt['id']}/started", {"thread_id": "run-" + api.attempt["id"]}, machine["token"])
        post(
            api,
            f"attempts/{api.attempt['id']}/complete",
            {"outcome": "completed", "text": "Done.", "last_seq": 0},
            machine["token"],
        )
    api.attempt = claim(api, machine, bot)
    return api.attempt


def subtask(parent, owner, **extra):
    return {"owner": owner, **ASK, "parent": parent, **extra}


def dry_run(api, body, token):
    return post(api, "tasks/dry-run", body, token)["problems"]


def carry(api, attempt, row):
    """The run also carries `row`, as when its prompt brought that task in: a message on that task is one of the
    run's inputs (task_privacy.attempt_tasks)."""
    from backend.store import H

    with api.app.state.store.transaction() as c:
        said = H.task_comment(c, H.TICO_ACTOR, row["id"], "Look at this task too.", wake=False)
        c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (attempt["id"], said["id"]))
