"""Helpers that other tests share, moved out of test_stop_waiting.py so they import no test file."""

from backend import bot_settings as BS
from backend.tests.helpers.api import assign, claim, post, ready, runner
from backend.tests.helpers.task_lanes import RUNNER_CAPABILITIES


def task_attempt(api, session=None):
    """A claimed turn on a task `ops` owns. A turn's lane is keyed on the bot's `session` at claim: `bot` puts every
    task in the one shared lane; unset, each task has its own."""
    if session:
        with api.app.state.store.transaction() as c:
            BS.write(c, "ops", {**BS.read(c, "ops"), "session": session})
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"], capabilities=RUNNER_CAPABILITIES)
    task = post(api, "tasks", {"owner": "ops", "title": "Count the words", "body": "Count them."})
    return machine, task, claim(api, machine)
