"""A bot's wake about a task (backend/task_wakes.py): what it carries, how wakes fold, and how runs are counted.

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company's person, three bots on one computer and a
General pipeline.
"""

from datetime import datetime, timezone

import pytest

from backend import task_wakes
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
BUILDER, MANAGER, REVIEWER = "bot:builder", "bot:manager", "bot:reviewer"
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        yield conn


@pytest.fixture
def general(c):
    for slug in ("builder", "reviewer", "manager"):
        X.bot(c, slug)
    return X.pipeline(c, manager=MANAGER)


def _queued(c, task_id):
    """Who has a wake for the task that no run has taken yet."""
    rows = c.execute(
        "SELECT j.bot_id FROM jobs j JOIN messages m ON m.id = j.message_id WHERE j.state='queued' AND m.wake_key LIKE ?",
        (task_id + ":%",),
    ).fetchall()
    return sorted(H.bot_actor(row[0]) for row in rows)


def _refs(c, message_id):
    return H._json(c.execute("SELECT refs FROM messages WHERE id=?", (message_id,)).fetchone()[0], {})


def test_a_callers_refs_never_set_what_is_the_engines(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    assert _queued(c, row["id"]) == [BUILDER]  # its start
    said = {"quiet": True, "job": False, "task": "another", "visit": "v0", "level": "manager", "automation_run": "r7"}
    message = task_wakes.add(c, row, BUILDER, row["visit"], "More words.", wake_key=f"{row['id']}:x", refs=said)
    refs = _refs(c, message)
    assert (refs["task"], refs["visit"], refs["level"]) == (row["id"], row["visit"]["id"], "owner")
    assert "quiet" not in refs and refs["job"] is True  # from the engine's own state: the wake queued a run
    assert refs["automation_run"] == "r7"  # the caller's own facts ride on
    assert _queued(c, row["id"]) == [BUILDER]  # folded into one, and the run still starts


def test_a_fold_never_leaves_a_bot_that_runs_jobs_without_a_queued_run(c, general, monkeypatch):
    from backend import enqueue

    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    monkeypatch.setattr(enqueue, "enqueue", lambda conn, message: None)  # this one wake queues nothing
    message = task_wakes.add(c, row, BUILDER, row["visit"], "More words.", wake_key=f"{row['id']}:x")
    assert message and "job" not in _refs(c, message)
    assert _queued(c, row["id"]) == [BUILDER]  # the wake it would have folded is still queued


def _chat_run(c, conversation_id, bot):
    """A run of `bot` on a message in a conversation that names no task: a person wrote to it there."""
    message, run = H.new_id(), H.new_id()
    c.execute(
        "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, created_at) "
        "VALUES (?,?,?,?,'say','Any news?','{}',?)",
        (message, conversation_id, ANA, bot, H.now()),
    )
    c.execute(
        "INSERT INTO attempts (id, bot_id, created_at, started_at, finished_at, state, message_id) "
        "VALUES (?,?,?,?,?,'completed',?)",
        (run, H.actor_id(bot), H.now(), H.now(), H.now(), message),
    )


def test_a_run_counts_for_the_task_it_names_and_by_its_conversation_only_when_one_open_task_has_it(c, general):
    from backend import task_engine as E

    first = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    second = X.new_task(c, ANA, "Fix the footer", assignee=BUILDER)
    c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (first["conversation_id"], second["id"]))
    c.execute(
        "UPDATE messages SET conversation_id=? WHERE wake_key LIKE ?", (first["conversation_id"], second["id"] + ":%")
    )
    X.ran(c, second["id"], BUILDER)  # the run names the footer task, in the conversation the two share
    first, second = E.task(c, first["id"]), E.task(c, second["id"])

    def runs(row):
        return task_wakes.runs_since(c, row, row["visit"]["entered_at"])

    assert (runs(first), runs(second)) == (0, 1)
    assert task_wakes.last_run(c, first, BUILDER) is None
    _chat_run(c, first["conversation_id"], BUILDER)  # two open tasks have that conversation: it is neither's
    assert (runs(first), runs(second)) == (0, 1)
    E.move(c, second["id"], E.CLOSED, ANA, "Not needed")
    assert runs(E.task(c, first["id"])) == 1  # now the conversation is one open task's
