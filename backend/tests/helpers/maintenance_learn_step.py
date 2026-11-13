"""Helpers that other tests share, moved out of test_maintenance_learn_step.py so they import no test file."""

from backend import updates
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, post, ready, runner


def queue(c, bot_id="ops", day=None):
    day = day or updates.today()
    updates.build_queue(c, day, updates.kind_for(day))
    return c.execute("SELECT * FROM update_queue WHERE bot=? AND day=?", (bot_id, day)).fetchone()


def start(api, row, machine=None):
    if machine is None:
        machine = runner(api)
    # Enrollment can already place a templated bot on this computer.
    placed = get(api, f"bots/{row['bot']}")["assignment"]
    if not placed or placed["runner_id"] != machine["runner_id"]:
        assign(api, machine, row["bot"], generation=placed["generation"] if placed else 0)
    ready(api, machine, [row["bot"]])
    with api.app.state.store.transaction() as c:
        # Creation wakes are over; claim the Update request itself.
        c.execute(
            "UPDATE jobs SET state='completed' WHERE bot_id=? AND message_id<>? AND state='queued'",
            (row["bot"], row["message_id"]),
        )
        # The request waits for a warm session (backend/wake_presets.py); let it go as the release check would.
        c.execute(
            "UPDATE jobs SET held=0,released=json_object('warm',1,'waited',0,'at',?) "
            "WHERE message_id=? AND held=1 AND state='queued' AND hold_reason IS NULL",
            (H.now(), row["message_id"]),
        )
    attempt = claim(api, machine, row["bot"])
    assert attempt is not None
    with api.app.state.store.read() as c:
        job = c.execute("SELECT message_id FROM jobs WHERE attempt_id=?", (attempt["id"],)).fetchone()
        assert job[0] == row["message_id"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "update-turn"}, machine["token"])
    return machine, attempt


def complete(api, turn, text):
    machine, attempt = turn
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "text": text, "last_seq": 0},
        machine["token"],
    )
