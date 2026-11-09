"""Helpers that other tests share, moved out of test_task_models.py so they import no test file."""

from backend.tests.helpers.api import assign, claim, get, post, runner
from backend.tests.helpers.check_models import company, heartbeat
from backend import bot_settings as BS


def revision(api, bot="ops"):
    return get(api, f"bots/{bot}/access")["revision"]


def set_task_model(api, choice, bot="ops", expected=200):
    return post(
        api,
        f"bots/{bot}/task-model",
        {"task_model": choice, "expected_revision": revision(api, bot)},
        expected=expected,
    )


def ops_on_sonnet(api):
    """ops on Claude Sonnet at high effort, on a computer signed in to Claude and Grok."""
    company(api, "anthropic", "xai")
    with api.app.state.store.transaction() as c:
        config = BS.read(c, 'ops')
        config.update(runtime="claude", harness="claude", model="claude-sonnet-5-5", reasoning_effort="high")
        BS.write(c, 'ops', config)
    machine = runner(api)
    assign(api, machine, "ops")
    heartbeat(api, machine)
    return machine


def task_turn(api, machine, title="Tidy the changelog"):
    task = post(api, "tasks", {"owner": "ops", "title": title, "body": "Sort the entries."})
    turn = claim(api, machine)
    assert turn["task"]["id"] == task["id"]
    return task, turn
