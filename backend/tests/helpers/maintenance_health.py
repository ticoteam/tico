"""Helpers that other tests share, moved out of test_maintenance_health.py so they import no test file."""

import json
from backend.store import H


def turn(api, runner_id, result, minutes_ago, bot="assistant", state=None):
    """A finished turn on `runner_id` with the runner's result (backend/execution.py stores it as result)."""
    with api.app.state.store.transaction() as c:
        at = H.shift(H.now(), minutes=-minutes_ago)
        mid = "m-" + H.new_id()
        c.execute(
            "INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES(?,'human:ana',?,'chat','hi',?)",
            (mid, "bot:" + bot, at),
        )
        c.execute(
            "INSERT OR IGNORE INTO jobs(id,message_id,bot_id,state,created_at) VALUES(?,?,?,'completed',?)",
            ("j-" + mid, mid, bot, at),
        )
        job = c.execute("SELECT id FROM jobs WHERE message_id=?", (mid,)).fetchone()["id"]
        aid = "a-" + mid
        c.execute(
            "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,finished_at,"
            "result) VALUES(?,?,?,?,1,?,?,?,?,?,?)",
            (aid, job, bot, runner_id, "hash-" + aid, state or result["outcome"], at, at, at, json.dumps(result)),
        )
