"""A computer back from a silence, and what its queue says meanwhile.

On Nov 18 the Shared cloud machine of Tico Team was silent for about seven minutes while the v0.6.52 update restarted
it. Two people's messages queued in that time started only two minutes after it was back: a machine out of contact
settled for AWAKE_SETTLE before any claim was given work, a rule meant for a Mac's few-second dark wake. And its
Computers row read "2 running, 45 queued" with the CPU at 6%: the queue count took the notices and routine runs that
their release policy holds (jobs.held) for work a claim could take."""

import pytest

from backend import start_places
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, post, ready, runner, settled  # noqa: F401


def back_from_an_update(api, platform):
    """A computer running finance, silent for seven minutes while a person writes to finance, then checking in again."""
    computer = runner(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET platform=? WHERE id=?", (platform, computer["runner_id"]))
    assign(api, computer, "finance")
    ready(api, computer, ["finance"])
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE runner_states SET last_seen_at=?,awake_since=NULL WHERE runner_id=?",
            (H.shift(H.now(), minutes=-7), computer["runner_id"]),
        )
    post(api, "chat/finance", {"text": "Are you there?"})
    ready(api, computer, ["finance"])  # the restarted runner's first check-in
    return computer


def test_a_linux_computer_back_from_an_update_starts_a_persons_message_on_its_first_claim(api):
    computer = back_from_an_update(api, "linux")
    attempt = claim(api, computer, "finance")
    assert attempt is not None, "a Linux computer back from an update held a person's message for AWAKE_SETTLE"
    assert post(api, f"attempts/{attempt['id']}/started", {"thread_id": "answer"}, computer["token"])["started"]


@pytest.mark.parametrize("platform", ["darwin", None])
def test_a_mac_back_from_a_silence_still_settles_before_it_takes_work(api, platform):
    computer = back_from_an_update(api, platform)
    assert claim(api, computer, "finance") is None  # it may be a dark wake, gone again in seconds
    settled(api, computer)
    assert claim(api, computer, "finance") is not None


def test_work_its_release_policy_holds_is_a_wait_on_the_computer_not_queued_work(api):
    computer = runner(api)
    assign(api, computer, "finance")
    ready(api, computer, ["finance"])
    post(api, "chat/finance", {"text": "First"})
    post(api, "chat/finance", {"text": "Second"})
    with api.app.state.store.transaction() as c:
        # One of the two waits for its release, as a batched notice or a routine run before its Due does.
        first = c.execute("SELECT id FROM jobs WHERE bot_id='finance' AND state='queued' ORDER BY created_at LIMIT 1")
        c.execute("UPDATE jobs SET held=1 WHERE id=?", (first.fetchone()["id"],))
    machine = next(m for m in get(api, "operations")["machines"] if m["id"] == computer["runner_id"])
    counts = machine["turn_counts"]
    assert (counts["queued"], counts["waiting"]) == (1, 1), machine["turn_summary"]
    assert [w["reason"] for w in counts["waits"]] == [start_places.HELD_WAIT]
    assert "1 waiting for a scheduled release" in machine["turn_summary"]
