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


def _state(c, message_id):
    return c.execute("SELECT state FROM jobs WHERE id=?", (message_id,)).fetchone()[0]


def test_a_wake_may_start_only_for_a_bot_whose_turn_it_is(c, general):
    from backend import task_engine as E

    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    start = c.execute("SELECT id FROM messages WHERE wake_key LIKE ?", (row["id"] + ":%",)).fetchone()[0]
    assert task_wakes.may_start(c, start) and _state(c, start) == "queued"
    stray = task_wakes.add(c, row, REVIEWER, row["visit"], "Look at this.", wake_key=f"{row['id']}:stray")
    assert not task_wakes.may_start(c, {"message_id": stray}) and _state(c, stray) == "canceled"  # no turn of its own
    look = task_wakes.add(
        c, row, MANAGER, row["visit"], "Late: look once.", wake_key=f"{row['id']}:{row['visit']['id']}:late"
    )
    assert task_wakes.may_start(c, look)  # Late's one look for the manager bot needs no turn
    chat = _chat_run(c, row["conversation_id"], REVIEWER)
    assert task_wakes.may_start(c, chat)  # not a wake about a task: not the engine's to hold
    E.snooze(c, row["id"], ANA)
    c.execute("UPDATE jobs SET state='queued' WHERE id=?", (start,))
    assert not task_wakes.may_start(c, start) and _state(c, start) == "canceled"  # a snoozed task wakes nobody


def _start(company, c, message_id):
    """The claim's preparation of a queued job (execution.Execution.start_claimed), as `claim` calls it in its write."""
    from backend.execution import Execution

    job = c.execute("SELECT id, message_id, bot_id FROM jobs WHERE id=?", (message_id,)).fetchone()
    row = {"id": job[0], "message_id": job[1], "bot_id": job[2], "generation": 1}
    return Execution(company, None).start_claimed(c, None, None, None, row)


def test_a_claim_starts_no_run_the_engine_holds_and_the_check_takes_the_task_on(company, c, general, monkeypatch):
    """The claim asks may_start in its own write: a refused wake is canceled and nothing is leased, and the never-stuck
    check takes the task on from there (here: 7 runs since the change, so a person has it)."""
    from backend import execution
    from backend import never_stuck as NS

    # Whether the bot may read the message is the claim's check before this one, and not what is tested here.
    monkeypatch.setattr(execution, "job_message_readable", lambda conn, bot, message: True)

    other = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    stray = task_wakes.add(c, other, REVIEWER, other["visit"], "Look at this.", wake_key=f"{other['id']}:stray")
    assert _start(company, c, stray) == {"attempt": None}  # the reviewer has no turn on that task
    assert _state(c, stray) == "canceled"
    assert c.execute("SELECT COUNT(*) FROM attempts WHERE job_id=?", (stray,)).fetchone()[0] == 0
    mine = X.new_task(c, ANA, "Plan the week", assignee=BUILDER)
    start = c.execute(
        "SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued' AND m.wake_key LIKE ?",
        (mine["id"] + ":%",),
    ).fetchone()[0]
    for _ in range(task_wakes.RUNS):  # seven runs since the change, and its wake is queued again
        X.ran(c, mine["id"], BUILDER)
        c.execute("UPDATE jobs SET state='queued' WHERE id=?", (start,))
    assert _start(company, c, start) == {"attempt": None}  # an eighth run never starts
    assert _state(c, start) == "canceled"
    assert c.execute("SELECT COUNT(*) FROM attempts WHERE job_id=?", (start,)).fetchone()[0] == task_wakes.RUNS
    at = H.now()
    for _ in range(6):
        at = H.shift(at, hours=12)
        NS.check(c, at)
    assert c.execute("SELECT level FROM visits WHERE id=?", (mine["visit"]["id"],)).fetchone()[0] == "person"


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
    return message


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
        return task_wakes.runs_since(c, row)

    assert (runs(first), runs(second)) == (0, 1)
    assert task_wakes.last_run(c, first, BUILDER) is None
    _chat_run(c, first["conversation_id"], BUILDER)  # two open tasks have that conversation: it is neither's
    assert (runs(first), runs(second)) == (0, 1)
    E.move(c, second["id"], E.CLOSED, ANA, "Not needed")
    assert runs(E.task(c, first["id"])) == 1  # now the conversation is one open task's


def test_a_private_task_with_no_owner_wakes_its_pipelines_manager_bot_who_then_reads_it(c, general):
    stages = ({"name": "Triage", "requirements": [{"kind": "owner_done"}]},)  # nobody owns a task in this stage
    X.pipeline(c, "ops", "Ops", stages, manager=MANAGER)
    made = H.task_create(c, ANA, "Sort the March invoices", "Who takes it?", ANA, pipeline="ops", private=True, lint=False)
    row = c.execute("SELECT owner_id, private, readers FROM tasks WHERE id=?", (made["id"],)).fetchone()
    assert row[0] is None and row[1]
    assert _queued(c, made["id"]) == [MANAGER]  # told to pick an owner, not skipped because it could not read it
    assert H.task_readers({"readers": row[2]}) == [MANAGER]  # it reads the task now, as a task event says
    assert not H.task_private_readable(c, BUILDER, H.task(c, made["id"]))  # nobody else is brought in
