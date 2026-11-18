"""What keep-warm left behind, cleared when it was removed (migration 20261116124618_keep_warm_removed). A keep-warm
turn sent a task's session a hidden notice (no seq) and its "ok" was posted to the task's room as a visible answer to
it, fifteen a day burying the bot's questions. The migration takes those replies out of the thread read (their seq is
cleared, as the notice's), moots their waiting deliveries and points the room's counters at what is left, while an
ordinary reply stays; it cancels a keep-warm job still queued, and takes finished keep-warm runs off their task so
they are not counted as its runs. The setting's table and column stay, unread.

The rows are written as keep-warm wrote them (backend/keep_warm.py before its removal); synthetic, the fictional
company only."""

import importlib
import json

import pytest

from backend import task_engine as E
from backend import task_wakes
from backend import threads
from backend.store import H
from backend.tests.helpers import engine as X

MIGRATION = "backend.migrations.20261116124618_keep_warm_removed"
BOT = "bot:cpo"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        X.bot(conn, "cpo")
        X.place(conn, "cpo")
        yield conn


def _room(c):
    """cpo's task from Ana's chat (its replies reach her in the task's own room), with one ordinary reply in it."""
    task_id = X.new_task(c, X.ANA, "Add the export button", assignee=BOT)["id"]
    H.event(c, H.TICO_ACTOR, "task.room", task_id, {})
    room = E.task(c, task_id)["conversation_id"]
    H.say(c, BOT, X.ANA, "Fixed the button's label.", conversation_id=room)
    c.execute("DELETE FROM jobs")
    return task_id, room


def _notice(c, task_id, room, state):
    """A keep-warm notice to cpo as keep_warm.send wrote it (no seq; the room's counters as they were) and its job."""
    counters = c.execute("SELECT last_seq, last_unread_seq FROM conversations WHERE id=?", (room,)).fetchone()
    mid, job = H.new_id(), H.new_id()
    refs = {"task": task_id, "wake": "keep_warm", "trigger": "keep_warm", "purpose": "keep warm"}
    c.execute(
        "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at,revision,restricted) "
        "VALUES(?,?,?,?,'notice','Keep-warm turn: reply with one word: ok.',?,?,0,1)",
        (mid, room, H.TICO_ACTOR, BOT, json.dumps(refs), H.now()),
    )
    c.execute("UPDATE messages SET seq=NULL WHERE id=?", (mid,))
    c.execute("UPDATE conversations SET last_seq=?, last_unread_seq=? WHERE id=?", (counters[0], counters[1], room))
    c.execute(
        "INSERT INTO jobs(id,message_id,bot_id,state,created_at) VALUES(?,?,?,?,?)", (job, mid, "cpo", state, H.now())
    )
    return mid, job


def _kept_warm(c, task_id, room, said="ok"):
    """A finished keep-warm run whose reply was posted the old way: in the room, answering the notice, stamped with the
    run (execution.stamp_reply). Returns (attempt id, reply id)."""
    mid, job = _notice(c, task_id, room, "completed")
    run, now = H.new_id(), H.now()
    c.execute(
        "INSERT INTO attempts (id, job_id, message_id, bot_id, runner_id, generation, token_hash, lease_until, state, "
        "created_at, started_at, finished_at, task_id, purpose, trigger_kind, final_text, writes) "
        "VALUES (?,?,?,?,?,0,?,?,'completed',?,?,?,?,'keep warm','keep_warm',?,1)",
        (run, job, mid, "cpo", "r1", H.new_id(), now, now, now, now, task_id, said),
    )
    reply = H.say(c, BOT, X.ANA, said, conversation_id=room, in_reply_to=mid)
    refs = {
        **(H.message(c, reply["id"]).get("refs") or {}),
        "run": {"job_id": job, "attempt_id": run},
        "answers": [mid],
    }
    c.execute("UPDATE messages SET refs=? WHERE id=?", (json.dumps(refs), reply["id"]))
    return run, reply["id"]


def _shown(c, room):
    """cpo's messages in the room as Ana reads it: the read the chat, the task page and Flow share."""
    rows = threads.timeline(c, threads.actor_viewer(c, X.ANA), room, flat=True)["messages"]
    return [m.get("body") for m in rows if m.get("sender_id") == BOT]


def _counters(c, room):
    return tuple(c.execute("SELECT last_seq, last_unread_seq FROM conversations WHERE id=?", (room,)).fetchone())


def test_keep_warm_replies_already_posted_leave_the_thread_read_and_its_unread_mark(c):
    task_id, room = _room(c)
    real = c.execute("SELECT max(seq) FROM messages WHERE conversation_id=?", (room,)).fetchone()[0]
    run, ok = _kept_warm(c, task_id, room)
    ok_seq = c.execute("SELECT seq FROM messages WHERE id=?", (ok,)).fetchone()[0]
    assert _shown(c, room) == ["Fixed the button's label.", "ok"]
    assert _counters(c, room) == (ok_seq, ok_seq)  # unread, with only an "ok" to read
    migration = importlib.import_module(MIGRATION)
    migration.up(c)
    migration.up(c)  # again: nothing more
    assert _shown(c, room) == ["Fixed the button's label."]
    assert _counters(c, room) == (real, real)
    states = {s for (s,) in c.execute("SELECT state FROM message_deliveries WHERE message_id=?", (ok,))}
    assert states <= {"moot", "read"}
    assert c.execute("SELECT final_text FROM attempts WHERE id=?", (run,)).fetchone()[0] == "ok"  # kept on the run


def test_removal_cancels_a_queued_turn_and_takes_old_runs_off_their_task(c):
    task_id, room = _room(c)
    run, _ = _kept_warm(c, task_id, room)
    _, queued = _notice(c, task_id, room, "queued")
    other = H.say(c, X.ANA, BOT, "Is the button done?", conversation_id=room)
    job = c.execute("SELECT id FROM jobs WHERE message_id=?", (other["id"],)).fetchone()[0]
    before = task_wakes.runs_since(c, E.task(c, task_id))
    migration = importlib.import_module(MIGRATION)
    migration.up(c)
    migration.up(c)  # again: nothing more
    states = dict(c.execute("SELECT id, state FROM jobs WHERE id IN (?, ?)", (queued, job)).fetchall())
    assert states == {queued: "canceled", job: "queued"}  # an ordinary queued job is left alone
    row = c.execute(
        "SELECT task_id, message_id, job_id, purpose, final_text FROM attempts WHERE id=?", (run,)
    ).fetchone()
    assert tuple(row) == (None, None, None, "keep warm", "ok")  # history, on no task
    assert task_wakes.runs_since(c, E.task(c, task_id)) == before - 1


def test_old_stage_settings_are_emptied_so_their_stage_can_still_be_deleted(c):
    """A stage's own keep-warm hours named it (REFERENCES stages(id), no cascade) and were deleted with it; nothing
    does that any more, so the migration empties the table, and the stage deletes as any other."""
    stage = c.execute("SELECT id FROM stages ORDER BY rowid LIMIT 1").fetchone()[0]
    c.execute("INSERT INTO stage_keep_warm (stage_id, hours) VALUES (?, 4)", (stage,))
    importlib.import_module(MIGRATION).up(c)
    assert c.execute("SELECT count(*) FROM stage_keep_warm").fetchone()[0] == 0
    assert c.execute("PRAGMA foreign_key_check(stage_keep_warm)").fetchall() == []
