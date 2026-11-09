"""Helpers that other tests share, moved out of test_stop_waiting.py so they import no test file."""

from backend.tests.helpers.api import assign, claim, post, ready, runner


def task_attempt(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Count the words", "body": "Count them."})
    return machine, task, claim(api, machine)
