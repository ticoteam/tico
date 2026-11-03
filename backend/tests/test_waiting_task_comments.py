"""A person's comment on a bot's Waiting task wakes the bot at once, whatever the task waits on (docs/tasks.md,
Comments): a person's message is on the always-wake floor (backend/wake_rules.py), and the claim's task waits
(auto_tasks.refusal) only hold the keeper's notices, never a person's message (execution.job_refusal)."""

import pytest

from backend import milestone_waits
from backend import task_relations as TR
from backend.store import H
from backend.tests.test_api import api, claim, post  # noqa: F401
from backend.tests.test_task_lanes import task_bot


def finish(api, machine, attempt, text="Done."):
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": text, "last_seq": 0},
         machine["token"])


def park(c, tid, wait):
    """Put the bot's task in Waiting on `wait`, as its own turn would."""
    if wait == "child":
        H.task_create(c, "bot:ops", "Review receipts", "Review #1994.", "bot:cpo", parent_id=tid, lint=False)
    elif wait == "blocked_by":
        other = H.task_create(c, "bot:ops", "Review receipts", "Review #1994.", "bot:cpo", lint=False)
        TR.relate(c, "bot:ops", tid, other["id"], "blocked_by")
    elif wait == "person":
        # A bot's wait on a person is a card they own that blocks the task (hubdb._ask_in_subtask): parked.
        H.task_update(c, "bot:ops", tid, status="waiting", waiting_on="human:ben", note="Ben signs off first")
        return
    H.task_update(c, "bot:ops", tid, status="waiting", note="Waiting")
    if wait == "milestone":
        milestone_waits.add(c, "bot:ops", H.task(c, tid), "release:ticoteam/tico@v0.5.22")
    elif wait == "snooze":
        c.execute("UPDATE tasks SET snoozed_until=? WHERE id=?", (H.shift(H.now(), hours=4), tid))


def waiting_task(api, machine, wait, requester="human:ana"):
    """A task the bot worked once and left in Waiting on `wait`."""
    with api.app.state.store.transaction() as c:
        tid = H.task_create(c, requester, "Release v0.5.22 checklist", "Cut the release.", "bot:ops", lint=False)["id"]
    first = claim(api, machine)
    assert first["task"]["id"] == tid
    post(api, f"attempts/{first['id']}/started", {"thread_id": "release"}, machine["token"])
    with api.app.state.store.transaction() as c:
        park(c, tid, wait)
        assert H.task(c, tid)["status"] == "waiting"
    finish(api, machine, first, "Waiting.")
    return tid


@pytest.mark.parametrize("wait", ["nothing", "child", "blocked_by", "person", "milestone", "snooze"])
@pytest.mark.parametrize("requester", ["human:ana", "bot:ops"])
def test_a_persons_comment_on_a_waiting_task_starts_the_bots_turn(api, wait, requester):
    machine = task_bot(api, lanes=True, others=("cpo",))
    tid = waiting_task(api, machine, wait, requester)
    sent = post(api, f"tasks/{tid}/comments", {"text": "Please cut the release now."})
    assert sent["woke"] is True and sent["sent_as"] == {"delivery": "steer", "until": None}
    turn = claim(api, machine)
    assert turn and turn["message"]["id"] == sent["comment"]["id"], "the comment is the bot's next turn"
    assert turn["task"]["id"] == tid


def test_a_persons_comment_goes_first_once_the_bots_one_turn_ends(api):
    # A runner without lanes runs one turn of the bot at a time: the comment waits for the running turn, then goes
    # ahead of the bot's other queued work, however much there is.
    machine = task_bot(api, lanes=False, others=("cpo",))
    tid = waiting_task(api, machine, "nothing")
    with api.app.state.store.transaction() as c:
        H.task_create(c, "human:ana", "Long job", "Do it.", "bot:ops", lint=False)
    running = claim(api, machine)
    post(api, f"attempts/{running['id']}/started", {"thread_id": "long"}, machine["token"])
    with api.app.state.store.transaction() as c:
        for i in range(3):
            # Top of the queue: they outrank the waiting task, so only a person's message can put it first.
            H.task_create(c, "human:ana", f"Other {i}", "Do it.", "bot:ops", top=True, lint=False)
    sent = post(api, f"tasks/{tid}/comments", {"text": "Please cut the release now."})
    assert claim(api, machine) is None, "one turn of the bot at a time on this runner"
    finish(api, machine, running)
    turn = claim(api, machine)
    assert turn and turn["message"]["id"] == sent["comment"]["id"]
