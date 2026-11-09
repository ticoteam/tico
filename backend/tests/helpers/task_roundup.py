"""Helpers that other tests share, moved out of test_task_roundup.py so they import no test file."""

import json
import pytest
from backend.enqueue import enqueue_stored
from backend import task_roundup
from backend.store import H


DAY = "2026-11-03T14:00:00.000000Z"  # the 6am run (company time is Pacific, PST on this date); fixed for the tests


NOON = H.shift(DAY, hours=6)  # the noon run, 20:00 UTC


@pytest.fixture(autouse=True)
def build_clock(monkeypatch):
    """A list is built at its run's time unless a test moves the build clock, so the checkpoints the tests read are the runs'."""
    monkeypatch.setattr(task_roundup, "_built_at", lambda: DAY)


def ago(**kw):
    return H.shift(DAY, **{k: -v for k, v in kw.items()})


def task(c, title, owner="bot:finance"):
    """Created at the fixed DAY clock, so the runs' cutoffs do not depend on the real date."""
    made = H.task_create(c, H.human_actor("ana"), title, f"{title}, for the roundup.", owner)
    c.execute("UPDATE tasks SET created_at=? WHERE id=?", (DAY, made["id"]))
    return made


def settle(c, tid, slug, turned):
    """The task as it stood before its bot's last turn on it at `turned`: its own row, and its messages and history a day
    older, so nothing has happened on it since the turn."""
    conv = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (turned, tid))
    c.execute("UPDATE messages SET created_at=? WHERE conversation_id=?", (H.shift(turned, days=-1), conv))
    c.execute(
        "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?",
        (H.shift(turned, days=-1), tid),
    )
    c.execute(
        "INSERT INTO attempts(id,bot_id,created_at,settled_at,trigger,message_id,task_id,exit,summary) "
        "VALUES(?,?,?,?,'schedule',NULL,?,'done','')",
        (H.new_id(), slug, turned, turned, tid),
    )
    started(c, tid, slug, turned)  # the turn was a run of the bot on the task


def started(c, tid, slug, when):
    """A run of the bot that carried the task at `when`: its attempt makes the task started, so it is not an assignment the
    bot has yet to start (backend/assigned_start.py). The message that started the run is written before the turn."""
    c.execute(
        "INSERT OR IGNORE INTO runners(id,label,owner_id,token_hash,created_at) VALUES('r-roundup','box','ana','h',?)",
        (when,),
    )
    conv = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    # Created a day before the turn, as settle dates its history: the task's assignment floor is its creation.
    c.execute("UPDATE tasks SET created_at=? WHERE id=?", (H.shift(when, days=-1), tid))
    mid = H.new_id()
    c.execute(
        "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at) "
        "VALUES(?,?,?,?,'task','',?,?)",
        (mid, conv, H.human_actor("ana"), H.bot_actor(slug), json.dumps({"task": tid}), H.shift(when, days=-1)),
    )
    enqueue_stored(c, mid)
    if not c.execute("SELECT 1 FROM jobs WHERE message_id=?", (mid,)).fetchone():
        # An external inbox gets no job (backend/enqueue.py): its run still needs one to attach to.
        c.execute(
            "INSERT INTO jobs(id,message_id,bot_id,state,created_at) VALUES(?,?,?,'completed',?)",
            (mid, mid, slug, when),
        )
    job = c.execute("SELECT id FROM jobs WHERE message_id=?", (mid,)).fetchone()[0]
    c.execute("UPDATE jobs SET state='completed' WHERE id=?", (job,))
    c.execute(
        "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
        "VALUES(?,?,?,'r-roundup',1,?,'completed',?,?)",
        (H.new_id(), job, slug, H.new_id(), when, when),
    )


def blocks(c, blocker, tid, when):
    c.execute(
        "INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) VALUES(?,?,'blocks','human:ana',?)",
        (blocker, tid, when),
    )


def complete_jobs(c, slug="finance"):
    """The creation wakes ran: nothing is queued for the bot."""
    c.execute("UPDATE jobs SET state='completed' WHERE bot_id=?", (slug,))


def lists(c, slug="finance"):
    return [
        row[0]
        for row in c.execute(
            "SELECT body FROM messages WHERE recipient_id=? AND refs LIKE '%task_roundup%' ORDER BY created_at",
            (H.bot_actor(slug),),
        ).fetchall()
    ]


def only_list(c, slug="finance"):
    found = lists(c, slug)
    assert len(found) == 1, found
    return found[0]


def named_in(body):
    """How many task lines a list names (not the 'and K more' line)."""
    return len([line for line in body.split("\n") if line.startswith("- ") and not line.startswith("- and ")])
