"""Helpers that other tests share, moved out of test_threads_private_execution.py so they import no test file."""

import pytest
from backend.store import H
from backend.tests.helpers.api import assign, claim, ready, runner


def task(api, *, requester="human:ben", owner="human:ana", private=True, title="Sensitive packet"):
    with api.app.state.store.transaction() as c:
        return H.task_create(
            c, requester, title, "Confidential attachment and comment.", owner, private=private, lint=False
        )


def private_run(api, bot="ops"):
    """A private task owned by the bot, and the bot's run on it: (task row, its attempt)."""
    row = task(api, requester="human:ben", owner="bot:" + bot)
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    attempt = claim(api, machine, bot)
    with api.app.state.store.transaction() as c:  # the run carries its private task (its work)
        c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (attempt["id"], row["id"]))
    return row, attempt


@pytest.fixture(autouse=True)
def private_tasks_have_an_unrelated_manager(api):
    from backend.tests.helpers.api import unrelated_task_managers

    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
