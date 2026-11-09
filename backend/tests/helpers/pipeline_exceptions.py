"""Shared fixtures for the pipeline exception tests (test_pipeline_exceptions.py, test_stuck_alarm.py): a Dev tickets
board with its manager, a ticket of a given age, one finished attempt of its owner, and the list the manager reads."""

from backend import bot_settings as BS
from backend import pipeline_exceptions as PE
from backend.store import H
from backend.tests.helpers.dev_ticket_rules import dev_type


def board(c, profile="manager"):
    found = c.execute("SELECT id FROM task_types WHERE name='Dev tickets'").fetchone()
    typ = H.type_get(c, found[0]) if found else dev_type(c)
    c.execute("UPDATE task_types SET manager='coo' WHERE id=?", (typ["id"],))
    BS.write(c, "coo", {"name": "coo", "runtime": "fake", "status": "active", "profile": profile})
    if not c.execute("SELECT 1 FROM runners WHERE id='r1'").fetchone():
        c.execute(
            "INSERT INTO runners(id,label,owner_id,token_hash,created_at) VALUES('r1','box','ana','h',?)", (H.now(),)
        )
        c.execute("UPDATE runner_states SET last_seen_at=? WHERE runner_id='r1'", (H.now(),))
    return H.type_get(c, typ["id"])


def settled(c):
    """Every wake so far has had its run: what is left queued is what a test adds."""
    c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")


def ticket(c, typ, title="Fix the connection", step="In Progress", minutes=0):
    row = H.task_create(c, "bot:coo", title, "Build.", "bot:cpo", type=typ["id"], step=step, lint=False)
    settled(c)
    if minutes:
        then = H.shift(H.now(), minutes=-minutes)
        c.execute("UPDATE tasks SET created_at=?, updated_at=? WHERE id=?", (then, then, row["id"]))
        c.execute(
            "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?", (then, row["id"])
        )
    return H.task(c, row["id"])


def run(c, row, state, minutes_ago, stop=None):
    """One finished attempt of the owner's on the task's lane, `minutes_ago`."""
    when = H.shift(H.now(), minutes=-minutes_ago)
    msg = H._write_message(
        c,
        "human:ana",
        row["owner_id"],
        "go",
        H.conversation(c, row["conversation_id"]),
        "say",
        {"task": row["id"]},
        None,
        None,
    )
    job = c.execute("SELECT id FROM jobs WHERE message_id=?", (msg["id"],)).fetchone()[0]
    c.execute("UPDATE jobs SET state='completed' WHERE id=?", (job,))
    aid = H.new_id()
    c.execute(
        "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,started_at,"
        "finished_at,lane,stop_requested_at) VALUES(?,?,?,'r1',1,?,?,?,?,?,?,?,?)",
        (aid, job, H.actor_id(row["owner_id"]), aid, state, when, when, when, when, "task:" + row["id"], stop),
    )
    return when


def listed(c, typ, kind=None):
    return [e for e in PE.build(c, typ) if kind is None or e["kind"] == kind]
