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


def test_a_wake_whose_key_does_not_start_with_its_task_is_never_written(c, general, caplog):
    """The run a wake starts is the task its key starts with (`message_task`): a key that starts with anything else
    (C10's old `room:<stage>:...`, another task's id) would start runs on a task that is not this one, refused at every
    start. Such a wake is refused and logged by its kind alone."""
    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    other = X.new_task(c, ANA, "Plan the week", assignee=BUILDER)
    visit = row["visit"]["id"]
    for bad in ("room:general-todo:start", f"{other['id']}:{visit}:room:general-todo:0", f"{row['id']}0:{visit}:x"):
        assert task_wakes.add(c, row, BUILDER, row["visit"], "Words.", wake_key=bad) is None, bad
        assert not c.execute("SELECT 1 FROM messages WHERE wake_key=?", (bad,)).fetchone(), bad
    assert "(room)" in caplog.text and row["id"] not in caplog.text and "Words" not in caplog.text
    assert task_wakes.add(c, row, BUILDER, row["visit"], "Words.", wake_key=f"{row['id']}:{visit}:room:general-todo:0")


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


def test_a_bots_word_to_another_bot_about_a_persons_task_starts_no_run(c, general):
    """docs/pipelines-v3.md "It only wakes whoever has the turn": a task a person owns wakes no bot, so a bot's message
    to another bot about it starts no run. It stays in the task's chat for that bot's next run, and the check owes the
    other bot nothing (rule 2 holds: it has no turn)."""
    from backend import never_stuck as NS

    row = X.new_task(c, ANA, "Write the launch note")
    said = H.say(
        c,
        BUILDER,
        REVIEWER,
        "The draft is in the folder.",
        conversation_id=row["conversation_id"],
        refs={"task": row["id"]},
    )
    job = c.execute("SELECT id FROM jobs WHERE message_id=?", (said["id"],)).fetchone()
    assert not task_wakes.may_start(c, said)
    if job:
        assert _state(c, job[0]) == "canceled"
    NS.check(c, H.shift(H.now(), minutes=30))
    assert _queued(c, row["id"]) == []
    assert H.message(c, said["id"])


def _occurrence(c, task_id):
    """A schedule's notice to the bot that the next occurrence was absorbed into this open task
    (automations.absorb_into_open_task): automatic, about the task, and not one of the engine's wakes."""
    said = H.notice(
        c, H.KEEPER, BUILDER, "Scheduled occurrence absorbed.", refs={"task": task_id, "wake": "occurrence"}
    )
    assert _state(c, said["id"]) == "queued"
    return said["id"]


def test_every_automatic_start_about_a_task_is_held_by_its_turn_and_its_seven_runs(c, general):
    from backend import never_stuck as NS

    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    first = _occurrence(c, row["id"])
    assert task_wakes.may_start(c, first)  # the builder has the turn and runs to spare
    c.execute("UPDATE jobs SET state='completed' WHERE id=?", (first,))
    for _ in range(task_wakes.RUNS):
        X.ran(c, row["id"], BUILDER)
        task_wakes.add(c, row, BUILDER, row["visit"], "Carry on.", wake_key=f"{row['id']}:{H.new_id()}")
    notice = _occurrence(c, row["id"])
    assert not task_wakes.may_start(c, notice) and _state(c, notice) == "canceled"  # an eighth run never starts
    c.execute("UPDATE jobs SET state='queued' WHERE id=?", (notice,))  # its retry
    assert not task_wakes.may_start(c, notice) and _state(c, notice) == "canceled"
    c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")  # what the claim held
    at = H.now()
    for _ in range(6):
        at = H.shift(at, hours=12)
        NS.check(c, at)
    assert c.execute("SELECT level FROM visits WHERE id=?", (row["visit"]["id"],)).fetchone()[0] == "person"
    c.execute(
        "UPDATE visits SET last_change_attempt=(SELECT MAX(rowid) FROM attempts) WHERE id=?", (row["visit"]["id"],)
    )
    notice = _occurrence(c, row["id"])  # no runs since: the person has it all the same
    assert not task_wakes.may_start(c, notice) and _state(c, notice) == "canceled"
    asked = H.say(c, ANA, BUILDER, "What is left on this?", refs={"task": row["id"]})
    assert task_wakes.may_start(c, asked) and _state(c, asked["id"]) == "queued"  # a person's own words still start


@pytest.mark.parametrize("via", ["assistant", "botops", "token"])
def test_only_words_a_person_typed_themselves_start_a_run_past_the_seven(c, general, via):
    """The 7 runs hold "until a person acts": a message the Assistant or BotOps writes in a person's name, or one sent
    with a personal token, is no person's act, and neither is the keeper's carrier of one. A message typed in Slack is
    the person's own."""
    from backend import threads

    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    for _ in range(task_wakes.RUNS):
        X.ran(c, row["id"], BUILDER)
        task_wakes.add(c, row, BUILDER, row["visit"], "Carry on.", wake_key=f"{row['id']}:{H.new_id()}")
    c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")
    mark = H.BY_TOKEN.set(True) if via == "token" else H.VIA.set(via)
    try:
        written = H.say(c, ANA, BUILDER, "Keep going on this.", refs={"task": row["id"]})
    finally:
        (H.BY_TOKEN if via == "token" else H.VIA).reset(mark)
    assert H.delegated(written["refs"]) and not H.typed_by_person(written) and H.in_person_name(written)
    assert not task_wakes.may_start(c, written) and _state(c, written["id"]) == "canceled"
    carrier = threads._carrier(c, H.conversation(c, written["conversation_id"]), written, BUILDER, "mention", None)
    assert carrier and not H.typed_by_person(H.message(c, carrier))
    assert not task_wakes.may_start(c, carrier) and _state(c, carrier) == "canceled"
    slack = H.say(c, ANA, BUILDER, "Typed in Slack.", refs={"task": row["id"], "via": "slack"})
    assert task_wakes.may_start(c, slack) and _state(c, slack["id"]) == "queued"


def test_a_quiet_message_whose_job_was_queued_is_held_like_any_other(c, general):
    """Quiet keeps a message from queueing a run; it never lets a run that was queued anyway (a deferred delivery's
    deadline, delivery._fire) past the turn and the 7 runs."""
    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    for _ in range(task_wakes.RUNS):
        X.ran(c, row["id"], BUILDER)
        task_wakes.add(c, row, BUILDER, row["visit"], "Carry on.", wake_key=f"{row['id']}:{H.new_id()}")
    c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")
    quiet = H.new_id()
    c.execute(
        "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, created_at) "
        "VALUES (?,?,?,?,'notice','The build went green.',?,?)",
        (quiet, row["conversation_id"], H.KEEPER, BUILDER, H._dump({"task": row["id"], "quiet": True}), H.now()),
    )
    c.execute("INSERT INTO jobs(id,message_id,bot_id,created_at) VALUES(?,?,?,?)", (quiet, quiet, "builder", H.now()))
    assert not task_wakes.may_start(c, quiet) and _state(c, quiet) == "canceled"  # an eighth run never starts


@pytest.mark.parametrize("via", ["", "assistant"])
def test_a_persons_own_message_on_a_snoozed_task_starts_a_run(c, general, monkeypatch, via):
    """A person wrote on a snoozed task, or the Assistant did in their name, and the keeper carries it to the bot:
    their words get an answer, at the claim's first check (execution.job_refusal) as in may_start. Only the seven
    runs need a person's own hand; lifting a snooze is theirs in their name too."""
    from backend import execution
    from backend import task_engine as E
    from backend.enqueue import enqueue

    monkeypatch.setattr(execution, "job_message_readable", lambda conn, bot, message: True)
    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    E.snooze(c, row["id"], ANA)
    said, carrier = H.new_id(), H.new_id()
    c.execute(
        "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, created_at) "
        "VALUES (?,?,?,?,'say','Is the note ready?',?,?)",
        (said, row["conversation_id"], ANA, BUILDER, H._dump({"via": via} if via else {}), H.now()),
    )
    refs = H._dump(
        {"carries": said, "wake": "thread", "reason": "mention", "from": ANA, **({"via": via} if via else {})}
    )
    c.execute(
        "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, created_at) "
        "VALUES (?,?,?,?,'notice','Is the note ready?',?,?)",
        (carrier, row["conversation_id"], H.KEEPER, BUILDER, refs, H.now()),
    )
    enqueue(c, {"id": carrier, "sender_id": H.KEEPER, "recipient_id": BUILDER, "refs": refs, "created_at": H.now()})
    job = dict(c.execute("SELECT * FROM jobs WHERE id=?", (carrier,)).fetchone())
    assert task_wakes.may_start(c, carrier)
    assert execution.job_refusal(c, job, {}, set(), set(), False, False) is None


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
    assert _start(company, c, stray)["attempt"] is None  # the reviewer has no turn on that task
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
    assert _start(company, c, start)["attempt"] is None  # an eighth run never starts
    assert _state(c, start) == "canceled"
    assert c.execute("SELECT COUNT(*) FROM attempts WHERE job_id=?", (start,)).fetchone()[0] == task_wakes.RUNS
    at = H.now()
    for _ in range(6):
        at = H.shift(at, hours=12)
        NS.check(c, at)
    assert c.execute("SELECT level FROM visits WHERE id=?", (mine["visit"]["id"],)).fetchone()[0] == "person"


def _carrier(c, task_id, bot, by):
    """A bot's post in a chat naming the task, and the keeper's carrier of it to `bot` (threads._carrier): the carrier
    names no task itself, only the message it carries, and its chat is no task's."""
    from backend.enqueue import enqueue

    chat = H._pair_conversation(c, by, bot, "chat")["id"]
    post = H.new_id()
    c.execute(
        "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, created_at) "
        "VALUES (?,?,?,?,'say','The launch note needs a footer.',?,?)",
        (post, chat, by, bot, H._dump({"task": task_id}), H.now()),
    )
    carrier, refs = H.new_id(), H._dump({"carries": post, "wake": "thread", "reason": "mention", "from": by})
    c.execute(
        "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, created_at) "
        "VALUES (?,?,?,?,'notice','The launch note needs a footer.',?,?)",
        (carrier, chat, H.KEEPER, bot, refs, H.now()),
    )
    enqueue(c, {"id": carrier, "sender_id": H.KEEPER, "recipient_id": bot, "refs": refs, "created_at": H.now()})
    assert _state(c, carrier) == "queued" and H.conversation(c, chat).get("task_id") is None
    return carrier


def test_a_carrier_of_a_bots_post_about_a_snoozed_task_never_starts(c, general):
    from backend import task_engine as E

    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    E.snooze(c, row["id"], ANA)
    held = _carrier(c, row["id"], BUILDER, REVIEWER)
    assert task_wakes.message_task(c, held) == row["id"]  # the task of the message it carries
    assert not task_wakes.may_start(c, held) and _state(c, held) == "canceled"


def test_a_carriers_run_is_about_the_task_of_the_message_it_carries_and_counts_for_it(api):
    """Claimed, the carrier's run names the task of the message it carries, so it is one of the task's runs."""
    from backend import task_engine as E
    from backend.tests.helpers.api import assign, claim, post, ready, runner

    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid = post(api, "tasks", {"owner": "ops", "title": "Plan the week", "body": "Draft it."})["id"]
    with api.app.state.store.transaction() as c:
        X.bot(c, "reviewer", computer=False)
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")
        carried = _carrier(c, tid, "bot:ops", REVIEWER)
        before = task_wakes.runs_since(c, E.task(c, tid))
    aid = claim(api, machine)["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "t-" + aid}, machine["token"])
    with api.app.state.store.read() as c:
        assert tuple(c.execute("SELECT job_id, task_id FROM attempts WHERE id=?", (aid,)).fetchone()) == (carried, tid)
        assert task_wakes.runs_since(c, E.task(c, tid)) == before + 1


def _carrier_in(c, conversation_id, about, bot, by):
    """A bot's post in task A's conversation that names task B, and the keeper's carrier of it to `bot`."""
    from backend.enqueue import enqueue

    post_id, carrier = H.new_id(), H.new_id()
    c.execute(
        "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, created_at) "
        "VALUES (?,?,?,?,'say','The footer task needs a look.',?,?)",
        (post_id, conversation_id, by, bot, H._dump({"task": about}), H.now()),
    )
    refs = H._dump({"carries": post_id, "wake": "thread", "reason": "mention", "from": by})
    c.execute(
        "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, created_at) "
        "VALUES (?,?,?,?,'notice','The footer task needs a look.',?,?)",
        (carrier, conversation_id, H.KEEPER, bot, refs, H.now()),
    )
    enqueue(c, {"id": carrier, "sender_id": H.KEEPER, "recipient_id": bot, "refs": refs, "created_at": H.now()})
    return carrier


def test_a_carrier_in_one_tasks_chat_of_a_post_about_another_is_held_and_counted_by_the_other(api):
    """A carrier sits in task A's conversation and carries a bot's post about task B. Its run is B's: claimed with six
    runs on B, it is B's seventh and A's count does not move; claimed again at B's seven, it is refused and nothing is
    leased."""
    from backend import task_engine as E
    from backend.tests.helpers.api import assign, claim, post, ready, runner

    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    a = post(api, "tasks", {"owner": "ops", "title": "Plan the week", "body": "Draft it."})["id"]
    b = post(api, "tasks", {"owner": "ops", "title": "Add the footer", "body": "Draft it."})["id"]
    with api.app.state.store.transaction() as c:
        X.bot(c, "reviewer", computer=False)
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")
        row_b = E.task(c, b)
        for n in range(task_wakes.RUNS - 1):
            task_wakes.add(c, row_b, "bot:ops", row_b["visit"], "Carry on.", wake_key=f"{b}:more:{n}")
            X.ran(c, b, "bot:ops", runner=machine["runner_id"])
        conv_a = E.task(c, a)["conversation_id"]
        carried = _carrier_in(c, conv_a, b, "bot:ops", REVIEWER)
        assert task_wakes.message_task(c, carried) == b  # the carried post's task, before A's conversation
        before_a = task_wakes.runs_since(c, E.task(c, a))
    aid = claim(api, machine)["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "t-" + aid}, machine["token"])
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    with api.app.state.store.transaction() as c:
        assert tuple(c.execute("SELECT job_id, task_id FROM attempts WHERE id=?", (aid,)).fetchone()) == (carried, b)
        assert task_wakes.runs_since(c, E.task(c, b)) == task_wakes.RUNS  # B is charged
        assert task_wakes.runs_since(c, E.task(c, a)) == before_a  # A is not
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")
        held = _carrier_in(c, conv_a, b, "bot:ops", REVIEWER)
    assert claim(api, machine) is None  # nothing leased
    with api.app.state.store.read() as c:
        assert _state(c, held) == "canceled"  # B is at its 7: refused
        assert c.execute("SELECT COUNT(*) FROM attempts WHERE job_id=?", (held,)).fetchone()[0] == 0
        assert task_wakes.runs_since(c, E.task(c, a)) == before_a


def test_a_run_whose_task_changed_between_its_claim_and_its_start_never_starts(api):
    """The claim let the run through; then, before its computer started it, the task was snoozed, closed, handed to a
    person or given another owner. The start asks the engine again (task_wakes.may_start, its own lease not counted):
    `started` is refused as a wait, the computer's next ask for a place is told to stop, and the run it then reports
    interrupted settles as skipped, its job canceled rather than queued again."""
    from backend import task_engine as E
    from backend.tests.helpers.api import assign, claim, post, ready, runner

    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    token = machine["token"]
    changes = {
        "snoozed": lambda c, row: E.snooze(c, row["id"], ANA),
        "closed": lambda c, row: E.move(c, row["id"], E.CLOSED, ANA, "Not needed"),
        "a person's": lambda c, row: c.execute("UPDATE visits SET level='person' WHERE id=?", (row["visit"]["id"],)),
        "owned by a person": lambda c, row: E.reassign(c, row["id"], ANA, ANA),
    }
    for change, made in changes.items():
        tid = post(api, "tasks", {"owner": "ops", "title": "Plan the week " + change, "body": "Draft it."})["id"]
        attempt = claim(api, machine)
        aid = attempt["id"]
        with api.app.state.store.transaction() as c:
            job = c.execute("SELECT job_id FROM attempts WHERE id=?", (aid,)).fetchone()[0]
            assert task_wakes.may_start(c, job, attempt=aid), change  # claimed with the engine's leave
            made(c, E.task(c, tid))
        refused = post(api, f"attempts/{aid}/started", {"thread_id": "t-" + aid}, token, expected=409)
        assert refused["error"]["code"] == "start_wait", change
        assert post(api, f"attempts/{aid}/start-place", {}, token).get("stop") is True, change
        post(api, f"attempts/{aid}/complete", {"outcome": "interrupted", "last_seq": 0}, token)
        with api.app.state.store.read() as c:
            ran = c.execute("SELECT state, started_at FROM attempts WHERE id=?", (aid,)).fetchone()
            assert ran[1] is None, change  # it never started
            assert c.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()[0] == "canceled", change
            assert not c.execute("SELECT 1 FROM jobs WHERE state='queued' AND bot_id='ops'").fetchone(), change


def test_a_run_claimed_before_a_change_and_started_after_it_counts_for_the_change_it_started_in(api):
    """The claim leased the run; then the task changed (a requirement came); then its computer started it. That start
    is one of the 7 the new change allows, by the order the starts came in, not by when the lease was written: six more
    runs and the visit has used all 7, and an eighth never starts."""
    from backend import task_engine as E
    from backend.tests.helpers.api import assign, claim, post, ready, runner

    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    token = machine["token"]
    tid = post(api, "tasks", {"owner": "ops", "title": "Plan the week", "body": "Draft it."})["id"]
    aid = claim(api, machine)["id"]
    with api.app.state.store.transaction() as c:
        E.changed(c, tid, ANA)  # after the claim, before the start
    post(api, f"attempts/{aid}/started", {"thread_id": "t-" + aid}, token)
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "last_seq": 0}, token)
    with api.app.state.store.transaction() as c:
        row = E.task(c, tid)
        assert task_wakes.runs_since(c, row) == 1  # it started after the change: it is this change's
        computer = c.execute("SELECT runner_id FROM attempts WHERE id=?", (aid,)).fetchone()[0]
        for n in range(task_wakes.RUNS - 1):
            task_wakes.add(c, row, "bot:ops", row["visit"], "Carry on.", wake_key=f"{tid}:more:{n}")
            X.ran(c, tid, "bot:ops", runner=computer)
        assert task_wakes.runs_since(c, E.task(c, tid)) == task_wakes.RUNS
        eighth = task_wakes.add(c, row, "bot:ops", row["visit"], "Carry on.", wake_key=f"{tid}:more:eighth")
        assert eighth and not task_wakes.may_start(c, eighth)  # an eighth run never starts


def test_a_refused_start_stays_refused_for_its_lease_even_once_the_task_wants_it_again(api):
    """`started` was refused (the task was snoozed after the claim). The snooze ends before the computer asks again:
    the refusal is on record, so its lease never starts. `started` is refused again, renew and start-place say stop,
    and the run it reports interrupted settles as skipped, its job canceled and not handed back. A fresh wake, on a new
    lease, is what runs."""
    from backend import task_engine as E
    from backend.tests.helpers.api import assign, claim, post, ready, runner

    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    token = machine["token"]
    tid = post(api, "tasks", {"owner": "ops", "title": "Plan the week", "body": "Draft it."})["id"]
    aid = claim(api, machine)["id"]
    with api.app.state.store.transaction() as c:
        job = c.execute("SELECT job_id FROM attempts WHERE id=?", (aid,)).fetchone()[0]
        E.snooze(c, tid, ANA)
    refused = post(api, f"attempts/{aid}/started", {"thread_id": "t-" + aid}, token, expected=409)
    assert refused["error"]["code"] == "start_wait"
    with api.app.state.store.transaction() as c:
        E.unsnooze(c, tid, ANA)
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued' AND id<>?", (job,))
        place = c.execute("SELECT start_until FROM start_places WHERE attempt_id=?", (aid,)).fetchone()
        assert place is None or place[0] is None  # its place was let go with the refusal
    assert post(api, f"attempts/{aid}/renew", {}, token).get("stop") is True
    assert post(api, f"attempts/{aid}/start-place", {}, token).get("stop") is True
    again = post(api, f"attempts/{aid}/started", {"thread_id": "t-" + aid}, token, expected=409)
    assert again["error"]["code"] == "start_wait"
    post(api, f"attempts/{aid}/complete", {"outcome": "interrupted", "last_seq": 0}, token)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT started_at FROM attempts WHERE id=?", (aid,)).fetchone()[0] is None
        assert c.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()[0] == "canceled"  # skipped
    with api.app.state.store.transaction() as c:
        row = E.task(c, tid)
        task_wakes.add(c, row, "bot:ops", row["visit"], "Carry on.", wake_key=f"{tid}:fresh")
    fresh = claim(api, machine)["id"]
    assert fresh != aid
    assert post(api, f"attempts/{fresh}/started", {"thread_id": "t-" + fresh}, token)["started"] is True


def test_a_live_run_on_one_task_never_hides_a_lost_wake_on_another_in_the_same_conversation(c, general):
    """Two open tasks share a conversation. The builder's live run on the footer task is not a run on the launch note:
    the note's wake that never ran is lost, and the never-stuck check wakes the builder for it again."""
    from backend import never_stuck as NS
    from backend import task_engine as E

    first = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    second = X.new_task(c, ANA, "Fix the footer", assignee=BUILDER)
    c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (first["conversation_id"], second["id"]))
    c.execute(
        "UPDATE messages SET conversation_id=? WHERE wake_key LIKE ?", (first["conversation_id"], second["id"] + ":%")
    )
    X.ran(c, second["id"], BUILDER, state="running")  # under way on the footer, its lease renewed
    c.execute(
        "UPDATE attempts SET finished_at=NULL, lease_until=? WHERE task_id=?",
        (H.shift(H.now(), minutes=5), second["id"]),
    )
    c.execute(
        "UPDATE jobs SET state='running' WHERE attempt_id IN (SELECT id FROM attempts WHERE task_id=?)", (second["id"],)
    )
    c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")  # the launch note's start never ran: lost
    first, second = E.task(c, first["id"]), E.task(c, second["id"])
    assert task_wakes.live_run(c, second, BUILDER) and not task_wakes.live_run(c, first, BUILDER)
    NS.check(c, H.shift(H.now(), seconds=10))
    assert _queued(c, first["id"]) == [BUILDER]  # woken again for the note


def test_a_live_run_that_names_the_task_from_another_conversation_is_on_the_task(c, general):
    """A schedule's notice about the task sits in the bot's own chat, not the task's: its live run names the task,
    counts for it, and is the bot being on it, read from the pass's one read of live runs."""
    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")
    chat = H._pair_conversation(c, H.KEEPER, BUILDER, "chat")["id"]
    assert chat != row["conversation_id"]
    notice, run = H.new_id(), H.new_id()
    c.execute(
        "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, created_at) "
        "VALUES (?,?,?,?,'notice','Scheduled occurrence absorbed.',?,?)",
        (notice, chat, H.KEEPER, BUILDER, H._dump({"task": row["id"], "wake": "occurrence"}), H.now()),
    )
    c.execute(
        "INSERT INTO jobs(id,message_id,bot_id,created_at,state) VALUES(?,?,?,?,'running')",
        (notice, notice, "builder", H.now()),
    )
    c.execute(
        "INSERT INTO attempts (id, job_id, bot_id, runner_id, generation, token_hash, state, lease_until, created_at, "
        "started_at, message_id, task_id) VALUES (?,?,?,'r1',1,?,'running',?,?,?,?,?)",
        (run, notice, "builder", run, H.shift(H.now(), minutes=5), H.now(), H.now(), notice, row["id"]),
    )
    task_wakes.started(c, run)
    c.execute("UPDATE jobs SET attempt_id=? WHERE id=?", (run, notice))
    from backend import task_engine as E

    row = E.task(c, row["id"])
    assert task_wakes.runs_since(c, row) == 1  # it counts for the task
    live = task_wakes.live_bots(c)
    assert task_wakes.live_run(c, row, BUILDER, live=live)  # and the bot is on it
    assert task_wakes.live_run(c, row, BUILDER)


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


class _Recorder:
    """A connection that keeps every statement it runs, to read their plans."""

    def __init__(self, conn):
        self.conn, self.seen = conn, []

    def execute(self, sql, args=()):
        self.seen.append((sql, tuple(args)))
        return self.conn.execute(sql, args)


def _run_plans(c, read, row):
    """The plan of each statement `read` runs on `attempts`."""
    rec = _Recorder(c)
    read(rec, row)
    return [
        " | ".join(str(r[-1]) for r in c.execute("EXPLAIN QUERY PLAN " + sql, args))
        for sql, args in rec.seen
        if "FROM attempts" in sql
    ]


def test_a_run_count_reads_the_runs_since_the_change_and_looks_up_each_ones_start(c, general):
    """The never-stuck pass counts runs for every task that is due: the count must not gather every start in the
    company since the task's change or read every run there ever was (`SCAN a`). It reads the task's own runs (each
    found by an index from the task: attempts_task, its wakes, its messages) and looks up each one's start by its unique
    attempt id."""
    from backend import task_engine as E

    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    for n in range(3):
        assert task_wakes.add(c, row, BUILDER, row["visit"], "Carry on.", wake_key=f"{row['id']}:more:{n}")
        X.ran(c, row["id"], BUILDER)
    E.changed(c, row["id"], ANA)
    row = E.task(c, row["id"])
    assert task_wakes.add(c, row, BUILDER, row["visit"], "Carry on.", wake_key=f"{row['id']}:more:after")
    X.ran(c, row["id"], BUILDER)
    row = E.task(c, row["id"])
    statements: list[str] = []
    c.set_trace_callback(statements.append)
    try:
        assert task_wakes.runs_since(c, row) == 1  # the three before the change are the old change's
    finally:
        c.set_trace_callback(None)
    (counted,) = [s for s in statements if s.lstrip().startswith("SELECT COUNT(*) FROM attempts")]
    plan = [str(step[3]) for step in c.execute(f"EXPLAIN QUERY PLAN {counted}").fetchall()]
    assert "SEARCH a USING INTEGER PRIMARY KEY (rowid=?)" in plan and any("attempts_task" in s for s in plan), plan
    # Only the one-row table of the task's values and the list of its runs are scanned.
    assert not [
        s
        for s in plan
        if s.startswith("SCAN") and s not in ("SCAN p", "SCAN CONSTANT ROW") and not s.startswith("SCAN (subquery")
    ], plan
    assert not [s for s in plan if "run_starts" in s and "(attempt_id=?)" not in s], plan
    assert any("run_starts" in step and "(attempt_id=?)" in step for step in plan), plan


def test_the_cleanup_ends_every_run_still_waiting_on_an_old_room_wake(c, general):
    """Migration 20261112200917: a wake keyed `room:...` (before its key named its task) started runs no start would
    take. Each such job still queued or leased is canceled, its lease ended; every other wake is left as it was."""
    import importlib

    cleanup = importlib.import_module("backend.migrations.20261112200917_end_room_wakes")
    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    other = X.new_task(c, ANA, "Plan the week", assignee=REVIEWER)
    old = [
        task_wakes.add(c, row, BUILDER, row["visit"], "Spec has room.", wake_key=f"{row['id']}:old"),
        task_wakes.add(c, other, REVIEWER, other["visit"], "Spec has room.", wake_key=f"{other['id']}:old"),
    ]
    for n, message in enumerate(old):
        c.execute("UPDATE messages SET wake_key=? WHERE id=?", (f"room:general-todo:{n}", message))
    c.execute("UPDATE jobs SET state='leased' WHERE id=?", (old[1],))
    third = X.new_task(c, ANA, "Fix the footer", assignee=BUILDER)
    kept = c.execute(
        "SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued' AND m.wake_key LIKE ?",
        (third["id"] + ":%",),
    ).fetchone()[0]
    for _ in range(2):  # a second run finds nothing left to end
        cleanup.up(c)
        assert [_state(c, message) for message in old] == ["canceled", "canceled"]
        assert _state(c, kept) == "queued"
