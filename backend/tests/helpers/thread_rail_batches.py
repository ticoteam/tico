"""Helpers that other tests and the rail bench share, moved out of test_thread_rail_batches.py so they import no test
file: bot replies in a bot's Home, each from its own run."""

import pytest

from backend import threads
from backend.store import H
from backend.tests.helpers.api import headers


@pytest.fixture(autouse=True)
def runner(api):
    """The runner every seeded run names."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO runners(id,label,owner_id,token_hash,created_at) VALUES('rn','Mac','ana','th',?)", (H.now(),)
        )


INPUTS = 3  # each run reads this many messages


def home(api, bot, token="ben-test"):
    return api.get(f"/api/v2/threads/home/{bot}", headers=headers(token)).json()


def run(c, key, job_message, inputs, *, task_id=None):
    """A completed run of bot:ops woken by `job_message` that read `inputs`; its id."""
    aid = "run-" + key
    c.execute(
        "INSERT INTO jobs(id,message_id,bot_id,state,created_at,attempt_id) VALUES(?,?,'ops','completed',?,?)",
        ("job-" + key, job_message, H.now(), aid),
    )
    c.execute(
        "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
        "VALUES(?,?,'ops','rn',0,?,'completed',?,?)",
        (aid, "job-" + key, aid, H.now(), H.now()),
    )
    for mid in inputs:
        c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (aid, mid))
    if task_id:
        c.execute("UPDATE attempts SET trigger='task',task_id=? WHERE id=?", (task_id, aid))
    return aid


def seed_replies(api, home_id, n, tag, *, private_task=None):
    """`n` replies from bot:ops to Ben in its Home, each from its own run that read INPUTS messages Ben wrote there
    (the first run read a comment on `private_task` too, when one is given)."""
    with api.app.state.store.transaction() as c:
        for i in range(n):
            key = f"{tag}-{i}"
            asked = threads.post(c, "human:ben", home_id, f"Question {key}")
            inputs = [threads.post(c, "human:ben", home_id, f"Input {key}.{k}")["id"] for k in range(INPUTS)]
            task_id = None
            if private_task and i == 0:
                inputs.append(H.task_comment(c, "human:ana", private_task, "Secret " + key, wake=False)["id"])
                task_id = private_task
            aid = run(c, key, asked["id"], inputs, task_id=task_id)
            threads.post(c, "bot:ops", home_id, f"Answer {key}", in_reply_to=asked["id"], refs={"turn_id": aid})
