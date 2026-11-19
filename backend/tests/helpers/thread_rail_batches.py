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


def run(c, key, job_message, inputs, *, task_id=None, bot="ops"):
    """A completed run of `bot` woken by `job_message` that read `inputs`; its id."""
    aid = "run-" + key
    c.execute(
        "INSERT INTO jobs(id,message_id,bot_id,state,created_at,attempt_id) VALUES(?,?,?,'completed',?,?)",
        ("job-" + key, job_message, bot, H.now(), aid),
    )
    c.execute(
        "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
        "VALUES(?,?,?,'rn',0,?,'completed',?,?)",
        (aid, "job-" + key, bot, aid, H.now(), H.now()),
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


EARLIER = "2000-01-01T00:00:00Z"  # before any task in a test finished


def bot_reply(c, bot, thread_id, key, parent, *, task_id, inputs=()):
    """`bot`'s reply to `parent` in `thread_id`, from its own run woken by `parent` (that also read `inputs`), with the
    refs a production reply carries: the run (`turn_id` and `run`), the message it answers and the task."""
    aid = run(c, key, parent["id"], [parent["id"], *inputs], task_id=task_id, bot=bot)
    refs = {"turn_id": aid, "run": {"attempt_id": aid, "job_id": "job-" + key}, "answers": [parent["id"]]}
    return threads.post(
        c, "bot:" + bot, thread_id, f"Reply {key}", in_reply_to=parent["id"], refs={**refs, "task": task_id}
    )


def seed_finished_task_replies(api, n, tag, *, owner="human:ben", private_input=None):
    """`n` tasks of `owner`, each finished with a bot-to-bot reply chain on its thread: Ben asks, bot:doc-updater
    answers from its run, the task is done, then bot:ops replies to that answer from a run the answer woke. Only the
    last reply comes after the finish, so each thread's revival rests on bot:ops's reply alone (`threads.revived_roots`).
    `private_input`: a private task whose comment the first chain's last run also read. The ids of the tasks."""
    out = []
    with api.app.state.store.transaction() as c:
        for i in range(n):
            key = f"{tag}-{i}"
            # No body: a task's body is now its first chat message, which would land after the backdated finish.
            task = H.task_create(c, "human:ana", f"Finished {key}", "", owner, lint=False)
            [thread_id] = c.execute(
                "SELECT id FROM conversations WHERE task_id=? AND parent_id IS NULL", (task["id"],)
            ).fetchone()
            asked = threads.post(c, owner, thread_id, f"Status {key}?")
            answer = bot_reply(c, "doc-updater", thread_id, "d" + key, asked, task_id=task["id"])
            H.task_done(c, "human:ana", task["id"], "Done.", seen_version=H.task(c, task["id"])["version"])
            # The ask, the first answer and the finish all lie in the past: only bot:ops's reply comes after it.
            marks = ",".join("?" * 2)
            c.execute(f"UPDATE messages SET created_at=? WHERE id IN ({marks})", (EARLIER, asked["id"], answer["id"]))
            c.execute("UPDATE history_events SET created_at=? WHERE subject_id=?", (EARLIER, task["id"]))
            inputs = []
            if private_input and i == 0:
                inputs.append(H.task_comment(c, "human:ana", private_input, "Secret " + key, wake=False)["id"])
            bot_reply(c, "ops", thread_id, "o" + key, answer, task_id=task["id"], inputs=inputs)
            out.append(task["id"])
    return out
