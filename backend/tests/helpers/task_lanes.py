"""Helpers that other tests share, moved out of test_task_lanes.py so they import no test file."""

from backend.tests.helpers.api import assign, post, runner
from backend import bot_settings as BS


def task_bot(api, lanes, capability="task_lanes_v2", capacity=8, others=()):
    """`ops` keyed on tasks, assigned to a runner that does or does not advertise lanes."""
    with api.app.state.store.transaction() as c:
        for bot in ("ops", *others):
            config = BS.read(c, bot)
            BS.write(c, bot, {**config, 'session': 'task'})
    machine = runner(api)
    for bot in ("ops", *others):
        assign(api, machine, bot)
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capacity": capacity,
            "readiness": {bot: True for bot in ("ops", *others)},
            "capabilities": [capability] if lanes else [],
        },
        token=machine["token"],
    )
    return machine


def new_task(api, title, owner="ops"):
    return post(api, "tasks", {"owner": owner, "title": title, "body": "Please do " + title + "."})
