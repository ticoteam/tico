"""Helpers that other tests share, moved out of test_files.py so they import no test file."""

from backend.tests.helpers.api import assign, claim, headers, post, ready, runner


def turn(api, bot="ops", who="ben-test", machine=None):
    """A person chats with the bot and a runner claims it: (machine, the attempt with the bot's token)."""
    machine = machine or runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    post(api, "chat/" + bot, {"text": "Write the plan"}, who)
    return machine, claim(api, machine, bot)


def publish(api, attempt, name="plan.md", text="# Plan", **fields):
    return api.post(
        "/api/v2/files/uploads", json={"name": name, "text": text, **fields}, headers=headers(attempt["token"])
    )
