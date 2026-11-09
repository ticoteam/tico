"""Helpers that other tests share, moved out of test_subtask_results.py so they import no test file."""

from backend.store import H
from backend.tests.helpers.api import claim, post


def finish(api, machine, attempt, text="Work recorded."):
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": text},
        machine["token"],
    )


def start(api, machine, task, thread):
    turn = claim(api, machine)
    assert (turn["task"]["id"], turn["lane"]) == (task, "task:" + task)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": thread}, machine["token"])
    return turn


def queued_in(c, bot):
    return [
        H.message(c, r[0])
        for r in c.execute("SELECT message_id FROM jobs WHERE bot_id=? AND state='queued' ORDER BY rowid", (bot,))
    ]
