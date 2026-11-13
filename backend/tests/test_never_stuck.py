"""The never-stuck check (backend/never_stuck.py, docs/pipelines-v3.md "The never-stuck check").

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company's person, three bots on one computer, a
General pipeline whose stage expects a bot to take a day, and a clock the test moves.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend import answers as A
from backend import never_stuck as NS
from backend import task_engine as E
from backend import task_wakes
from backend import waiting_on as W
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


@pytest.fixture
def later(c, time_machine):
    """Move the clock on and run one pass. The seven rules hold after every pass. Returns what the pass did, without
    the zeros."""

    def step(**delta):
        time_machine.shift(timedelta(**delta))
        done = NS.check(c)
        assert NS.rules_hold(c) == []
        return {key: n for key, n in done.items() if n}

    return step


def _visit(c, task_id):
    return dict(H._one(c, "SELECT * FROM visits WHERE task_id=? AND left_at IS NULL", (task_id,)))


def _settled(c):
    """Every wake so far has had its run, which ended: what is left queued is what a test adds. A run that never started
    is no run, so each wake gets one that did."""
    rows = c.execute(
        "SELECT DISTINCT substr(m.wake_key, 1, instr(m.wake_key, ':') - 1), j.bot_id FROM jobs j "
        "JOIN messages m ON m.id = j.message_id WHERE j.state='queued' AND m.wake_key IS NOT NULL"
    ).fetchall()
    for task_id, bot in rows:
        X.ran(c, task_id, H.bot_actor(bot))
    X.settled(c)


def _ladder(c, task_id):
    visit = _visit(c, task_id)
    return visit["level"], visit["tries"]


def _states(c, task_id):
    return [(wait.state, wait.who) for wait in W.waiting_on(c, task_id)]


def _queued(c, task_id):
    """Who has a wake for the task that no run has taken yet."""
    rows = c.execute(
        "SELECT j.bot_id FROM jobs j JOIN messages m ON m.id = j.message_id WHERE j.state='queued' AND m.wake_key LIKE ?",
        (task_id + ":%",),
    ).fetchall()
    return sorted(H.bot_actor(row[0]) for row in rows)


def _said(c, task_id, actor):
    """The first line of each wake a bot got for a task, oldest first."""
    rows = c.execute(
        "SELECT body FROM messages WHERE wake_key LIKE ? AND recipient_id=? ORDER BY created_at, rowid",
        (task_id + ":%", actor),
    ).fetchall()
    return [str(row[0]).split("\n")[0] for row in rows]


def test_a_bot_that_stops_early_gets_three_tries_then_its_manager_bot_then_a_person(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    assert later(minutes=30) == {}  # its first wake still waits for a run: nothing more is sent
    X.ran(c, task_id, BUILDER)  # the run ended and nothing changed
    assert later(seconds=119) == {}
    assert later(seconds=1) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)
    X.ran(c, task_id, BUILDER)
    assert later(seconds=899) == {}
    assert later(seconds=1) == {"woken": 1} and _ladder(c, task_id) == ("owner", 2)
    X.ran(c, task_id, BUILDER)
    assert later(seconds=3599) == {}
    assert later(seconds=1) == {"woken": 1} and _ladder(c, task_id) == ("owner", 3)
    X.ran(c, task_id, BUILDER)
    assert later(seconds=119) == {}
    assert later(seconds=1) == {"to_manager": 1, "woken": 1} and _ladder(c, task_id) == ("manager", 1)
    assert _states(c, task_id) == [("bot", MANAGER)]
    X.ran(c, task_id, MANAGER)
    assert later(seconds=900) == {"woken": 1}
    X.ran(c, task_id, MANAGER)
    assert later(seconds=3600) == {"woken": 1} and _ladder(c, task_id) == ("manager", 3)
    X.ran(c, task_id, MANAGER)
    assert later(seconds=120) == {"to_person": 1} and _ladder(c, task_id)[0] == "person"
    assert [(w.who, w.line) for w in W.waiting_on(c, task_id)] == [
        (ANA, "Stuck in To do"),
        (ANA, "Say when the work is done"),
    ]
    assert [(n["kind"], n["task_id"]) for n in W.needs(c, ANA)] == [("task", task_id)]
    assert later(hours=5) == {} and later(days=9) == {"late": 1}  # it is hers: no bot is woken again
    assert _said(c, task_id, BUILDER)[1:] == [
        f"You stopped before this task was finished. Try {n} of 3." for n in (1, 2, 3)
    ]
    assert _said(c, task_id, MANAGER) == [
        "builder stopped 3 times without finishing it. It is yours now: do it, reassign it, ask a person, snooze it "
        "or close it. Try 1 of 3.",
        "You stopped before this task was finished. Try 2 of 3.",
        "You stopped before this task was finished. Try 3 of 3.",
    ]
    assert len(X.wakes(c, task_id)) == 7  # one start, three tries, three for the manager bot
    kinds = X.events(c, task_id)
    assert kinds.count("woken_again") == 6 and kinds.count("ladder") == 2


def test_an_owner_who_never_answers_the_stages_question_climbs_the_same_ladder(c, general, later):
    settings = {"name": "confidence", "type": "number", "min": 0, "max": 100, "question": "How sure are you?"}
    stages = ({"name": "Build", "owner": BUILDER, "requirements": [{"kind": "answer", "settings": settings}]},)
    X.pipeline(c, "eng", "Engineering", stages, manager=MANAGER)
    task_id = X.new_task(c, ANA, "Build the importer", "eng")["id"]
    for seconds in (120, 900, 3600):
        X.ran(c, task_id, BUILDER)
        assert later(seconds=seconds) == {"woken": 1}
    X.ran(c, task_id, BUILDER)
    assert later(seconds=120) == {"to_manager": 1, "woken": 1} and _ladder(c, task_id) == ("manager", 1)
    for seconds in (900, 3600):
        X.ran(c, task_id, MANAGER)
        assert later(seconds=seconds) == {"woken": 1}
    X.ran(c, task_id, MANAGER)
    assert later(seconds=120) == {"to_person": 1} and _ladder(c, task_id)[0] == "person"
    assert (ANA, "How sure are you?") in [(w.who, w.line) for w in W.waiting_on(c, task_id)]


def test_try_again_hands_it_back_to_the_owner_with_the_count_at_zero(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    c.execute(
        "UPDATE visits SET level='person', person_id=?, tries=3, next_try_at=NULL WHERE task_id=?", (ANA, task_id)
    )
    _settled(c)
    assert _states(c, task_id) == [("person", ANA), ("person", ANA)]
    E.try_again(c, task_id, ANA)
    assert _ladder(c, task_id) == ("owner", 0) and _states(c, task_id) == [("bot", BUILDER)]
    assert _said(c, task_id, BUILDER)[-1] == "Try this task again."
    assert later(hours=1) == {}  # the wake waits for a run


def test_try_again_is_a_persons_and_a_bot_is_told_what_it_can_do(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    c.execute(
        "UPDATE visits SET level='person', person_id=?, tries=3, next_try_at=NULL WHERE task_id=?", (ANA, task_id)
    )
    for bot in (MANAGER, BUILDER):
        with pytest.raises(H.Refused, match="task_needs_person") as refused:
            E.try_again(c, task_id, bot)
        assert "reassign it, ask a person, snooze it or close it" in str(refused.value)
    assert _ladder(c, task_id) == ("person", 3) and _states(c, task_id) == [("person", ANA), ("person", ANA)]


@pytest.mark.parametrize("via", ["assistant", "botops", "token"])
def test_try_again_and_a_change_after_late_need_a_persons_own_hand(c, general, later, via):
    """The Assistant, BotOps and a personal token act in a person's name, but restarting the ladder is the person's own
    act in Tico: Try again is refused with what to do, and a change they make on a task Late brought to the person
    leaves it with the person."""
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    c.execute(
        "UPDATE visits SET level='person', person_id=?, tries=3, next_try_at=NULL, late_person_at=? WHERE task_id=?",
        (ANA, H.now(), task_id),
    )
    mark = H.BY_TOKEN.set(True) if via == "token" else H.VIA.set(via)
    try:
        with pytest.raises(H.Refused, match="task_needs_person") as refused:
            E.try_again(c, task_id, ANA)
        assert "Ask them to press it" in str(refused.value)
        E._reset_ladder(c, _visit(c, task_id), H.now(), ANA)
    finally:
        (H.BY_TOKEN if via == "token" else H.VIA).reset(mark)
    assert _ladder(c, task_id) == ("person", 3)
    E.try_again(c, task_id, ANA)  # her own hand
    assert _ladder(c, task_id) == ("owner", 0)


def test_a_task_that_changed_starts_the_count_again_and_the_next_wake_is_not_a_try(c, general, later):
    stages = (
        {
            "name": "Draft",
            "owner": BUILDER,
            "requirements": [{"kind": "owner_done"}, {"kind": "ok", "person": ANA, "line": "OK the draft"}],
        },
    )
    notes = X.pipeline(c, "notes", "Notes", stages, manager=MANAGER)
    row = X.new_task(c, ANA, "Write the launch note", "notes")
    task_id = row["id"]
    X.ran(c, task_id, BUILDER)
    assert later(seconds=120) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)
    X.ran(c, task_id, BUILDER)
    # Something changed that nobody announced: here, an OK written straight into the table.
    X.answer(c, E.task(c, task_id), notes["requirements"]["Draft"][1], "ok", ANA)
    assert later(seconds=10) == {} and _ladder(c, task_id) == ("owner", 0)
    assert later(seconds=119) == {}
    assert later(seconds=1) == {"woken": 1} and _ladder(c, task_id) == ("owner", 0)  # woken, and no try counted
    assert _said(c, task_id, BUILDER)[-1] == "This task is not finished. Carry on."
    X.ran(c, task_id, BUILDER)
    assert later(seconds=120) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)
    assert _said(c, task_id, BUILDER)[-1] == "You stopped before this task was finished. Try 1 of 3."


def test_a_new_version_alone_is_not_progress(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    X.ran(c, task_id, BUILDER)
    assert later(seconds=120) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)
    X.ran(c, task_id, BUILDER)  # it stopped early again
    mark = _visit(c, task_id)["progress_mark"]
    c.execute("UPDATE tasks SET version = version + 1, title='Write the launch note today' WHERE id=?", (task_id,))
    assert E.progress_mark(c, _visit(c, task_id)["id"]) == mark
    assert later(seconds=10) == {} and _ladder(c, task_id) == ("owner", 1)  # no change: the count goes on
    assert _visit(c, task_id)["progress_mark"] == mark
    assert later(seconds=890) == {"woken": 1} and _ladder(c, task_id) == ("owner", 2)
    assert _said(c, task_id, BUILDER)[-1] == "You stopped before this task was finished. Try 2 of 3."


def test_nothing_is_sent_while_the_bot_runs_and_a_run_that_was_cut_off_is_a_try(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    job_id, message_id = c.execute(
        "SELECT j.id, j.message_id FROM jobs j JOIN messages m ON m.id = j.message_id WHERE m.wake_key LIKE ?",
        (task_id + ":%",),
    ).fetchone()
    c.execute(
        "INSERT INTO attempts (id, job_id, bot_id, runner_id, generation, token_hash, state, lease_until, created_at, "
        "started_at, message_id, task_id) VALUES ('run1', ?, 'builder', 'r1', 1, 'run1', 'running', ?, ?, ?, ?, ?)",
        (job_id, H.shift(H.now(), days=1), H.now(), H.now(), message_id, task_id),
    )
    c.execute("UPDATE jobs SET state='running', attempt_id='run1' WHERE id=?", (job_id,))
    assert later(hours=6) == {} and _ladder(c, task_id) == ("owner", 0)  # its computer still checks in
    c.execute("UPDATE attempts SET state='expired', finished_at=?, lease_until=? WHERE id='run1'", (H.now(), H.now()))
    c.execute("UPDATE jobs SET state='uncertain' WHERE id=?", (job_id,))
    # Stopped partway after it may have acted: Paused from that moment (the claim holds the task's work for it), with
    # no wake the bot could not take, until someone runs it again or leaves it.
    assert [(w.state, w.line) for w in W.waiting_on(c, task_id)] == [("paused", "builder " + task_wakes.RUN_HELD)]
    assert later(minutes=10) == {} and _queued(c, task_id) == []
    c.execute("UPDATE jobs SET state='canceled' WHERE id=?", (job_id,))  # left (POST jobs/{id}/reconcile)
    assert later(seconds=10) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)
    assert _said(c, task_id, BUILDER)[-1] == "Your last run on this task was cut off. Try 1 of 3."


def test_a_bot_that_cannot_run_is_not_woken_and_its_tasks_show_one_row_for_the_cause(c, general, later):
    first = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    X.ran(c, first, BUILDER)  # before the next task is made: making one settles every wake still queued
    second = X.new_task(c, ANA, "Fix the footer", assignee=BUILDER)["id"]
    X.ran(c, second, BUILDER)
    c.execute("UPDATE bots SET state='paused' WHERE id='builder'")
    assert later(days=5) == {"late": 2}  # nobody is woken, and the tasks do not land in a list one by one
    assert _ladder(c, first) == ("owner", 0)
    assert [(w.state, w.line) for w in W.waiting_on(c, first)] == [("paused", "builder is paused")]
    paused = [n for n in W.needs(c, ANA) if n["kind"] == "paused"]
    assert [(n["line"], n["bots"], sorted(n["tasks"])) for n in paused] == [
        ("builder is paused", [BUILDER], sorted([first, second]))
    ]
    c.execute("UPDATE bots SET state='active' WHERE id='builder'")
    assert later(seconds=10) == {"woken": 2}  # it carries on by itself
    assert _ladder(c, first) == ("owner", 1)


def test_a_bots_queued_wake_on_its_task_is_dropped_once_it_waits_on_a_task_it_filed(c, general, later):
    """Filing a sub-task makes the parent wait on it, so the parent's owner has no turn: a wake it still had queued for
    the parent goes (task_engine._drop_stale), as on any other change of turn. Left queued, the claim would drop it
    then (task_wakes.may_start) and could answer nothing; Health would count it as work handed out."""
    parent = X.new_task(c, ANA, "Plan the launch", assignee=BUILDER)
    assert _queued(c, parent["id"]) == [BUILDER]
    X.new_task(c, BUILDER, "Draft the launch post", assignee=REVIEWER, parent=parent["id"])
    assert _states(c, parent["id"])[0][0] == W.TASKS
    assert _queued(c, parent["id"]) == []


def test_a_run_held_for_a_check_pauses_its_task_and_says_so_to_the_bots_owner(c, general, later):
    """A run whose lease lapsed after it may have acted (a tool call or a write, even before it started) is held for a
    check (execution.auto_reconcile, `job.held`), and the bot claims nothing more on that task until someone resumes or
    dismisses it (execution.unfenced_sql). The task said it was still the bot's turn, Needs you was empty and the
    never-stuck pass kept writing wakes the bot could not take: a silent hold for up to 4 hours. Now it is Paused with
    the cause, on the bot's owner's Needs you when no BotOps reviews it, and carries on once the run is settled."""
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    X.ran(c, task_id, BUILDER, state="expired")  # its job is uncertain
    attempt, job = c.execute("SELECT id, job_id FROM attempts WHERE task_id=?", (task_id,)).fetchone()
    H.event(c, H.KEEPER, "job.held", attempt, {"job_id": job, "tools": ["Bash"]})
    line = "builder " + task_wakes.RUN_HELD
    assert [(w.state, w.line) for w in W.waiting_on(c, task_id)] == [("paused", line)]
    assert later(hours=2) == {} and _queued(c, task_id) == []  # nobody is woken meanwhile
    paused = [n for n in W.needs(c, ANA) if n["kind"] == "paused"]
    assert [(n["line"], n["bots"], n["tasks"]) for n in paused] == [(line, [BUILDER], [task_id])]
    X.bot(c, "botops")  # BotOps reviews held runs (its review task): no person row then
    assert [n for n in W.needs(c, ANA) if n["kind"] == "paused"] == []
    c.execute("UPDATE jobs SET state='canceled' WHERE id=?", (job,))  # dismissed (POST jobs/{id}/reconcile)
    assert _states(c, task_id) == [("bot", BUILDER)]
    assert later(seconds=10) == {"woken": 1}


def test_a_run_that_stopped_partway_is_never_a_silent_wait(c, general, later):
    from backend import health

    """Nov 12, Tico Team: a Codex reviewer's turn ended "interrupted" (a steer race) and its job was held for a person to
    run it again. For the quarter hour before auto_reconcile records `job.held`, the task still said it was the
    reviewer's turn, nothing was on Needs you and the never-stuck reading counted nothing. From the moment the run stops
    the task is Paused with the wait, the bot's owner has it on Needs you (no BotOps here) and the reading counts it."""
    task_id = X.new_task(c, ANA, "Review the footer fix", assignee=REVIEWER)["id"]
    X.ran(c, task_id, REVIEWER, state="interrupted")  # its job is uncertain, with no `job.held` yet
    line = "reviewer " + task_wakes.RUN_HELD
    assert line == "reviewer stopped partway: waiting on a person to run it again"
    assert W.headline(c, task_id).line == line and _states(c, task_id) == [("paused", REVIEWER)]
    paused = [n for n in W.needs(c, ANA) if n["kind"] == "paused"]
    assert [(n["line"], n["bots"], n["tasks"]) for n in paused] == [(line, [REVIEWER], [task_id])]
    reading = health.never_stuck_reading(c)
    assert reading["cant_run"] == 1 and task_id in reading["tasks"]["cant_run"]
    assert later(minutes=10) == {} and _queued(c, task_id) == []
    # Tico's own choice that waits only for the bot's lane (an update cut) runs by itself: nobody's wait.
    job, attempt = c.execute("SELECT id, attempt_id FROM jobs WHERE state='uncertain'").fetchone()
    c.execute(
        "INSERT INTO job_recovery(job_id, attempt_id, decision, note, actor, created_at) VALUES (?,?,?,?,?,?)",
        (job, attempt, "resume", "", H.KEEPER, H.now()),
    )
    assert _states(c, task_id) == [("bot", REVIEWER)]


def test_with_no_manager_bot_that_can_run_the_task_goes_straight_to_a_person(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    c.execute("UPDATE visits SET tries=3, next_try_at=? WHERE task_id=?", (H.now(), task_id))
    X.ran(c, task_id, BUILDER)
    c.execute("UPDATE bots SET state='paused' WHERE id='manager'")
    assert later(seconds=120) == {"to_person": 1}
    assert _states(c, task_id) == [("person", ANA), ("person", ANA)]


def test_a_late_task_goes_to_the_manager_bot_once_and_at_three_times_the_time_to_a_person(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    assert later(hours=23, minutes=59) == {}  # the first wake still waits for a run, and a day is expected
    assert later(minutes=1) == {"late": 1, "to_manager": 1, "woken": 1}
    assert _ladder(c, task_id) == ("manager", 1) and _states(c, task_id) == [("bot", MANAGER)]
    assert _said(c, task_id, MANAGER)[0].startswith("Late: 24 hours in To do, expected 24 hours. builder has it.")
    assert later(days=1) == {}  # once per visit
    assert later(days=1) == {"to_person": 1}  # three days: one person's list, once
    assert W.headline(c, task_id).line == "Late in To do"
    assert [(n["kind"], n["task_id"]) for n in W.needs(c, ANA)] == [("task", task_id)]
    assert later(days=30) == {}
    # It stays hers until a person acts: a bot changing the task does not take it out of her list.
    E.add_requirement(
        c, task_id, MANAGER, "answer", REVIEWER, "OK the note", seen_version=E.task(c, task_id)["version"]
    )
    assert _ladder(c, task_id)[0] == "person" and ("person", ANA) in _states(c, task_id)
    E.try_again(c, task_id, ANA)
    assert _ladder(c, task_id) == ("owner", 0)
    assert later(seconds=10) == {"woken": 1}  # a person acted: the reviewer's OK reaches it now
    assert later(days=30) == {}  # late once per visit: it is not handed up again


def test_an_ask_added_while_a_person_has_the_task_wakes_nobody_until_a_person_acts(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    assert later(days=1).get("to_manager") == 1 and later(days=2) == {"to_person": 1}  # Late took it to Ana
    # A run of the manager bot that was still going asks the reviewer for an OK. Only Ana has a turn: no bot is woken.
    E.add_requirement(
        c, task_id, MANAGER, "answer", REVIEWER, "OK the note", seen_version=E.task(c, task_id)["version"]
    )
    assert _ladder(c, task_id)[0] == "person" and X.wakes(c, task_id, REVIEWER) == []
    assert later(days=1) == {} and _queued(c, task_id) == []
    # Ana acts: every bot that has a turn now is woken, the reviewer named on that OK too.
    E.try_again(c, task_id, ANA)
    assert later(seconds=10) == {"woken": 1} and _queued(c, task_id) == [BUILDER, REVIEWER]
    assert _said(c, task_id, REVIEWER) == ["manager asks you: OK the note"]
    assert later(seconds=10) == {}  # once


def test_a_hand_over_takes_back_the_wakes_no_run_has_taken(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    assert _queued(c, task_id) == [BUILDER]
    assert later(days=1) == {"late": 1, "to_manager": 1, "woken": 1}
    assert _queued(c, task_id) == [MANAGER]  # the builder's wake would only start work that is no longer its own
    assert later(days=2) == {"to_person": 1}
    assert _queued(c, task_id) == []  # a person has it: no bot is still on its way
    assert [(n["kind"], n["task_id"]) for n in W.needs(c, ANA)] == [("task", task_id)]
    live = X.new_task(c, ANA, "Fix the footer", assignee=BUILDER)["id"]
    c.execute("UPDATE jobs SET state='running' WHERE state='queued'")
    assert task_wakes.drop(c, live) == 0  # a run already going is left alone
    assert c.execute("SELECT COUNT(*) FROM jobs WHERE state='running'").fetchone()[0] == 1


def test_a_late_task_that_waits_on_a_person_gets_one_look_from_the_manager_bot(c, general, later):
    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    task_id = row["id"]
    X.ran(c, task_id, BUILDER)
    E.add_requirement(c, task_id, BUILDER, "answer", ANA, "Card or invoice?", seen_version=row["version"])
    assert _states(c, task_id) == [("person", ANA)]
    assert later(hours=23) == {}  # a person's turn: no bot is woken
    assert later(hours=1) == {"late": 1, "woken": 1} and _ladder(c, task_id) == ("owner", 0)
    said = _said(c, task_id, MANAGER)[0]
    assert said.startswith("Late: 24 hours in To do, expected 24 hours. It waits on: Card or")
    # Its one move is to push past an ask it may push past: never a snooze that would set the ask aside.
    assert "It stays in Ana's list." in said and "push past it with a reason" in said and "snooze" not in said
    assert later(days=5) == {}  # already a person's turn: nothing is added
    assert [(n["kind"], n["line"]) for n in W.needs(c, ANA)] == [("ask", "Card or invoice?")]
    mine = X.new_task(c, ANA, "Call the bank", assignee=ANA)["id"]
    assert later(days=30) == {} and _visit(c, mine)["late_told_at"] is None  # the day is for bots only


SIGN_OFF = (
    {
        "name": "Sign off",
        "owner": "assignee",
        "expected_minutes": 1440,
        "expected_bots_only": 1,
        "requirements": [{"kind": "ok", "who_oks": "people_only", "person": ANA, "line": "OK to ship"}],
    },
)


def test_a_late_task_that_waits_only_on_a_people_only_ok_wakes_no_manager_bot(c, general, later):
    """Acme rc.5 (Nov 11): a Late wake offered the manager bot "snooze it" on a task that waited only on a person's
    people-only OK, and its snooze took the OK out of the person's list. A bot may not push past that OK, so the
    manager bot has no move: Late is the label only, and the OK stays in her list."""
    X.pipeline(c, "ship", "Ship", SIGN_OFF, manager=MANAGER)
    task_id = X.new_task(c, ANA, "Ship the launch note", "ship", assignee=BUILDER)["id"]
    assert _states(c, task_id) == [("person", ANA)]
    assert later(hours=24) == {"late": 1}
    assert _visit(c, task_id)["late_told_at"] and X.wakes(c, task_id, MANAGER) == []
    assert later(days=5) == {}  # 3 times the time: already her turn, nothing is added
    assert [(n["kind"], n["line"]) for n in W.needs(c, ANA)] == [("ask", "OK to ship")]


def test_a_bots_snooze_never_hides_an_ask_and_the_answer_ends_it(c, general, later):
    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    task_id = row["id"]
    X.ran(c, task_id, BUILDER)
    asked = E.add_requirement(c, task_id, BUILDER, "answer", ANA, "Card or invoice?", seen_version=row["version"])
    E.snooze(c, task_id, MANAGER, "Waiting on Ana", until=H.shift(H.now(), days=2), seen_version=asked["version"])
    assert _states(c, task_id) == [("snoozed", MANAGER), ("person", ANA)]
    assert [(n["kind"], n["line"]) for n in W.needs(c, ANA)] == [("ask", "Card or invoice?")]
    assert later(hours=1) == {} and _queued(c, task_id) == []  # set aside: no wake, and the rules hold (rule 3)
    # Her answer is what the snooze waited for: it ends, and the task goes on now, not in two days.
    A.answer(c, task_id, asked["requirement_id"], ANA, "Card")
    assert not E.task(c, task_id).get("snooze") and _states(c, task_id) == [("bot", BUILDER)]
    assert _said(c, task_id, BUILDER)[-1] == 'Ana answered "Card or invoice?": Card'
    # A person's own snooze still sets the task aside, asks and all.
    other = X.new_task(c, ANA, "Fix the footer", assignee=BUILDER)
    X.ran(c, other["id"], BUILDER)
    E.add_requirement(c, other["id"], BUILDER, "answer", ANA, "Which footer?", seen_version=other["version"])
    E.snooze(c, other["id"], ANA, until=H.shift(H.now(), days=2))
    assert [n["task_id"] for n in W.needs(c, ANA)] == []
    assert later(hours=1) == {}


def test_a_bot_takes_back_only_an_ask_a_bot_added(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    mine = E.add_requirement(c, task_id, ANA, "ok", "human:ben", "OK the launch date")
    for bot in (BUILDER, MANAGER):
        with pytest.raises(H.Refused, match="task_needs_person"):
            E.withdraw_requirement(c, mine["requirement_id"], bot, "Not needed")
    asked = E.add_requirement(
        c, task_id, BUILDER, "answer", ANA, "Card or invoice?", seen_version=E.task(c, task_id)["version"]
    )
    E.withdraw_requirement(c, asked["requirement_id"], MANAGER, "Found it in the docs")  # a bot's ask: a bot may
    E.withdraw_requirement(c, mine["requirement_id"], ANA)
    assert [n["kind"] for n in W.needs(c, ANA)] == []


def test_a_persons_task_is_late_only_past_its_due_date_and_nobody_is_chased(c, general, later):
    # A stage that expects everyone, people too, to take a minute: a person's own task still goes by its due date (C6).
    stage = {"name": "Do", "owner": ANA, "expected_minutes": 1, "requirements": [{"kind": "owner_done"}]}
    X.pipeline(c, "ops", "Operations", (stage,), manager=MANAGER)
    due = (START + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    mine = X.new_task(c, ANA, "Renew the lease", "ops", assignee=ANA)["id"]
    c.execute("UPDATE tasks SET due_at=? WHERE id=?", (due, mine))
    undated = X.new_task(c, ANA, "Call the bank", "ops", assignee=ANA)["id"]
    assert later(minutes=2) == {} and _visit(c, mine)["late_told_at"] is None
    assert later(days=29, hours=23) == {}
    assert later(hours=1) == {"late": 1}  # past its due date: the label only
    assert _visit(c, mine)["late_told_at"] and _ladder(c, mine) == ("owner", 0)
    assert X.wakes(c, mine, MANAGER) == [] and _queued(c, mine) == []  # nobody is woken about a person's task
    assert later(days=30) == {}  # once
    assert _visit(c, undated)["late_told_at"] is None  # no due date: never Late
    assert _states(c, mine) == [("person", ANA)]


def test_a_late_look_whose_wake_was_lost_is_written_again_once(c, general, later):
    """Being told Late is not the look: when the manager bot's wake for it goes missing, the next pass writes it again,
    with the same words, once, on the same budget and only to a manager bot that reads the task."""
    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    task_id = row["id"]
    X.ran(c, task_id, BUILDER)
    E.add_requirement(c, task_id, BUILDER, "answer", ANA, "Card or invoice?", seen_version=row["version"])
    assert later(hours=24) == {"late": 1, "woken": 1}
    first = _said(c, task_id, MANAGER)
    _lose(c, task_id)
    assert _queued(c, task_id) == []
    assert later(seconds=10) == {"woken": 1} and _queued(c, task_id) == [MANAGER]
    assert _said(c, task_id, MANAGER) == first * 2  # the same words
    assert later(seconds=10) == {}  # queued again: nothing more
    _lose(c, task_id)
    assert later(seconds=10) == {} and _queued(c, task_id) == []  # once: Late's 3 times still takes it to a person
    # A look the manager bot took is not lost.
    other = X.new_task(c, ANA, "Fix the footer", assignee=BUILDER)
    X.ran(c, other["id"], BUILDER)
    E.add_requirement(c, other["id"], BUILDER, "answer", ANA, "Which footer?", seen_version=other["version"])
    assert later(hours=24) == {"late": 1, "woken": 1}
    X.ran(c, other["id"], MANAGER)
    assert later(seconds=10) == {} and _queued(c, other["id"]) == []


def test_an_answer_wakes_the_bot_that_asked_and_the_count_starts_again(c, general, later):
    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    task_id = row["id"]
    X.ran(c, task_id, BUILDER)
    assert later(seconds=120) == {"woken": 1}
    X.ran(c, task_id, BUILDER)
    asked = E.add_requirement(
        c, task_id, BUILDER, "answer", ANA, "Card or invoice?", choices=["Card", "Invoice"], seen_version=row["version"]
    )
    assert later(hours=3) == {}
    A.answer(c, task_id, asked["requirement_id"], ANA, "Card")
    assert _ladder(c, task_id) == ("owner", 0) and _states(c, task_id) == [("bot", BUILDER)]
    assert _said(c, task_id, BUILDER)[-1] == 'Ana answered "Card or invoice?": Card'
    assert later(hours=1) == {}  # that wake waits for its run
    X.ran(c, task_id, BUILDER)
    assert later(seconds=120) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)


@pytest.mark.parametrize("via", [None, "token"])
def test_a_persons_comment_that_wakes_the_owner_starts_the_count_again_so_a_run_that_stops_early_is_tried_soon(
    c, general, later, via
):
    """The drill: two tries spent, a person writes to the bot, its run ends with "I'll install the test dependencies
    and continue". The next look is the first gap away, not the hour a third try waits, and it is the bot's own try,
    not a hand-over to the manager bot for runs spent before the person spoke."""
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    for n in (1, 2):
        X.ran(c, task_id, BUILDER)
        assert later(seconds=(120, 900)[n - 1]) == {"woken": 1} and _ladder(c, task_id) == ("owner", n)
    X.ran(c, task_id, BUILDER)
    later(minutes=20)
    mark = H.BY_TOKEN.set(True) if via else None
    try:
        H.task_comment(c, ANA, task_id, "pytest is there now, carry on")
    finally:
        if mark:
            H.BY_TOKEN.reset(mark)
    assert _ladder(c, task_id) == ("owner", 0)
    assert X.ran(c, task_id, BUILDER, comments=True) == 1  # it said it would carry on, and stopped
    assert later(seconds=119) == {}
    assert later(seconds=1) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)
    assert _said(c, task_id, BUILDER)[-1] == "You stopped before this task was finished. Try 1 of 3."


@pytest.mark.parametrize("who", ["assistant", "quiet", "person_level"])
def test_a_comment_the_assistant_writes_or_one_that_wakes_no_bot_or_on_a_persons_task_keeps_the_count(
    c, general, later, who
):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    X.ran(c, task_id, BUILDER)
    assert later(seconds=120) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)
    X.ran(c, task_id, BUILDER)
    if who == "person_level":
        c.execute(
            "UPDATE visits SET level='person', person_id=?, tries=3, next_try_at=NULL WHERE task_id=?", (ANA, task_id)
        )
    before = _ladder(c, task_id)
    mark = H.VIA.set("assistant") if who == "assistant" else None
    try:
        H.task_comment(c, ANA, task_id, "A note for later", wake=who != "quiet")
    finally:
        if mark:
            H.VIA.reset(mark)
    assert _ladder(c, task_id) == before


def test_a_bot_named_for_an_ok_is_on_the_same_ladder(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=ANA)["id"]
    E.add_requirement(c, task_id, ANA, "answer", REVIEWER, "OK the wording")
    assert _said(c, task_id, REVIEWER) == ["Ana asks you: OK the wording"]
    X.ran(c, task_id, REVIEWER)
    assert later(seconds=120) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)
    c.execute("UPDATE visits SET tries=3, next_try_at=? WHERE task_id=?", (H.now(), task_id))
    X.ran(c, task_id, REVIEWER)
    assert later(seconds=120) == {"to_manager": 1, "woken": 1}
    # The task itself stays Ana's: only the OK went up.
    assert _states(c, task_id) == [("person", ANA), ("bot", REVIEWER)]
    assert [(n["kind"], n["task_id"]) for n in W.needs(c, ANA)] == [("task", task_id)]


def test_a_snooze_is_not_a_change_its_end_is_due_at_once_and_snoozed_time_is_not_late(c, general, later):
    task_id = X.new_task(c, ANA, "Chase the vendor", assignee=BUILDER)["id"]
    X.ran(c, task_id, BUILDER)  # its start: it stopped early
    mark = _visit(c, task_id)["progress_mark"]
    E.snooze(c, task_id, ANA, until=H.shift(H.now(), days=10))
    assert later(days=9) == {}  # set aside: no wake, no try, not late
    assert later(days=1) == {"snoozes_ended": 1}  # no wake is written when it ends
    assert len(X.wakes(c, task_id, BUILDER)) == 1 and _ladder(c, task_id) == ("owner", 0)
    assert _visit(c, task_id)["progress_mark"] in (None, mark)  # setting it aside was no change
    assert _states(c, task_id) == [("bot", BUILDER)]
    # Due at once: the next pass wakes the builder, as its next try. The gap since its run is not waited out again.
    assert later(seconds=10) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)
    assert _said(c, task_id, BUILDER)[-1] == "You stopped before this task was finished. Try 1 of 3."
    assert later(hours=23) == {}  # ten days went by snoozed: it has been in the stage an hour short of a day
    assert later(hours=2) == {"late": 1, "to_manager": 1, "woken": 1}


def test_a_bots_fourth_snooze_in_a_stage_goes_to_the_manager_bot_on_the_same_count(c, general, later):
    task_id = X.new_task(c, ANA, "Chase the vendor", assignee=BUILDER)["id"]
    X.ran(c, task_id, BUILDER)  # its start
    for n in range(1, E.BOT_SNOOZES + 1):
        E.snooze(c, task_id, BUILDER, "The vendor is away", seen_version=E.task(c, task_id)["version"])
        assert later(seconds=10) == {} and _queued(c, task_id) == []  # nothing wakes for a snoozed task
        E.unsnooze(c, task_id, ANA)
        assert later(seconds=10) == {"woken": 1} and _ladder(c, task_id) == ("owner", n)  # its next try, at once
        if n < E.BOT_SNOOZES:
            X.ran(c, task_id, BUILDER)
    # Its third try is still queued. Ana asks the reviewer for an OK: the reviewer's wake is queued too.
    E.add_requirement(c, task_id, ANA, "answer", REVIEWER, "OK the reply")
    assert _queued(c, task_id) == [BUILDER, REVIEWER]
    out = E.snooze(c, task_id, BUILDER, "The vendor is away", seen_version=E.task(c, task_id)["version"])
    assert out["note"] and not out.get("snooze")
    # Handed to the manager bot: the builder's wake is taken back, the reviewer's stays: its OK still waits on it.
    assert _queued(c, task_id) == [REVIEWER]
    assert later(seconds=10) == {"woken": 1} and _ladder(c, task_id) == ("manager", 1)
    assert _said(c, task_id, MANAGER)[-1].startswith("builder could not take this task further. It is yours now")
    assert _queued(c, task_id) == [MANAGER, REVIEWER]


def _runs(c, task_id, since):
    return c.execute("SELECT COUNT(*) FROM attempts WHERE task_id=? AND started_at >= ?", (task_id, since)).fetchone()[
        0
    ]


def _run_down(c, task_id, later, bots=(BUILDER, REVIEWER, MANAGER)):
    """Every queued wake about the task has its run, which stops early, and the clock goes on, until a person has it."""
    for _ in range(60):
        if _ladder(c, task_id)[0] == "person":
            return
        for bot in bots:
            X.ran(c, task_id, bot)
        later(minutes=10)
    raise AssertionError("the ladder never reached a person")


def test_seven_runs_between_two_changes_count_every_bot_that_ran(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    later(seconds=10)
    # Ana asks the reviewer for an OK: a change, so the count starts over, and the reviewer is on the same count.
    since = H.now()
    E.add_requirement(c, task_id, ANA, "answer", REVIEWER, "OK the wording")
    assert sorted(_queued(c, task_id)) == [BUILDER, REVIEWER]  # the builder's start is still its turn
    later(seconds=10)
    _run_down(c, task_id, later)
    assert _runs(c, task_id, since) == 7  # the builder's start and 3 tries were 4 with the reviewer's run in them
    assert later(hours=6) == {} and _queued(c, task_id) == []


def test_a_run_is_on_the_side_of_a_change_it_came_on_whatever_the_clock_says(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    # Its start runs on a computer whose clock is a minute behind: the run reads as started before the visit began.
    X.ran(c, task_id, BUILDER, at=H.shift(H.now(), minutes=-1))
    assert later(seconds=120) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)  # a try, not a free start
    assert _said(c, task_id, BUILDER)[-1] == "You stopped before this task was finished. Try 1 of 3."
    # A change written with the clock gone back still starts the count over, by order.
    E.add_requirement(c, task_id, ANA, "ok", "human:ben", "OK the wording")
    c.execute("UPDATE visits SET last_change_at=? WHERE task_id=?", (H.shift(H.now(), minutes=-5), task_id))
    assert _runs_since_change(c, task_id) == 0
    X.ran(c, task_id, BUILDER, at=H.shift(H.now(), minutes=-10))
    assert _runs_since_change(c, task_id) == 1


def _runs_since_change(c, task_id):
    return task_wakes.runs_since(c, E.task(c, task_id))


def _job(c, task_id, actor):
    found = c.execute(
        "SELECT j.id FROM jobs j JOIN messages m ON m.id = j.message_id WHERE j.state='queued' AND j.bot_id=? "
        "AND m.wake_key LIKE ?",
        (H.actor_id(actor), task_id + ":%"),
    ).fetchone()
    return found[0] if found else None


def test_no_eighth_run_starts_even_from_a_wake_queued_before_the_seventh(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    E.add_requirement(c, task_id, ANA, "answer", REVIEWER, "OK the wording")  # the builder still has work
    waiting = _job(c, task_id, REVIEWER)
    assert task_wakes.may_start(c, waiting) and _job(c, task_id, REVIEWER) == waiting  # it may start now
    # Its wake stays queued while the builder runs four times and the manager bot three.
    for _ in range(60):
        for bot in (BUILDER, MANAGER):
            X.ran(c, task_id, bot)
            if _runs_since_change(c, task_id) == NS.RUNS:
                break
        if _runs_since_change(c, task_id) == NS.RUNS:
            break
        later(minutes=10)
    assert _runs_since_change(c, task_id) == NS.RUNS and _job(c, task_id, REVIEWER) == waiting
    assert not task_wakes.may_start(c, waiting)  # seven runs since the change: it may not start an eighth
    assert _job(c, task_id, REVIEWER) is None  # and it is canceled with that answer


def test_only_whoever_has_the_turn_is_woken_when_a_task_enters_a_stage(c, general, later):
    stages = (
        {"name": "Write", "owner": BUILDER, "requirements": [{"kind": "owner_done"}]},
        {"name": "Check", "owner": BUILDER, "requirements": [{"kind": "ok", "person": REVIEWER, "line": "OK it"}]},
    )
    X.pipeline(c, "copy", "Copy", stages, manager=MANAGER)
    row = X.new_task(c, ANA, "Write the launch note", "copy")
    done = c.execute("SELECT id FROM requirements WHERE stage_id='copy-write'").fetchone()[0]
    A.answer(c, row["id"], done, BUILDER, "done", seen_version=row["version"])
    moved = E.task(c, row["id"])
    assert moved["stage"]["name"] == "Check"
    # In Check the builder has nothing of its own to do: only the reviewer's OK is missing, so only it is woken.
    assert [key.split(":")[1] for key, _ in X.wakes(c, row["id"], BUILDER)] == [row["visit"]["id"]]  # Write only
    assert _queued(c, row["id"]) == [REVIEWER]
    assert _said(c, row["id"], REVIEWER)[-1] == "You are asked: OK it"


def test_an_answer_for_a_bot_with_no_turn_goes_to_whoever_has_it(c, general, later):
    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    task_id = row["id"]
    X.ran(c, task_id, BUILDER)
    c.execute("UPDATE visits SET level='manager', tries=1 WHERE task_id=?", (task_id,))
    asked = E.add_requirement(c, task_id, MANAGER, "answer", ANA, "Card or invoice?", seen_version=row["version"])
    _settled(c)
    A.answer(c, task_id, asked["requirement_id"], ANA, "Card")  # a change: the ladder starts over, with the builder
    assert _ladder(c, task_id) == ("owner", 0)
    assert _queued(c, task_id) == [BUILDER]
    assert _said(c, task_id, BUILDER)[-1] == 'Ana answered "Card or invoice?", asked by manager: Card'
    assert not any("Card" in line for line in _said(c, task_id, MANAGER))


def _lose(c, task_id):
    """Every wake still queued for the task goes missing, as a lost job does."""
    c.execute(
        "DELETE FROM jobs WHERE state='queued' AND message_id IN (SELECT id FROM messages WHERE wake_key LIKE ?)",
        (task_id + ":%",),
    )


def test_a_lost_wake_is_written_again_on_the_next_pass_and_is_not_a_try(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    first = _said(c, task_id, BUILDER)
    _lose(c, task_id)
    assert later(seconds=10) == {"woken": 1} and _ladder(c, task_id) == ("owner", 0)  # this pass, not after a gap
    assert _queued(c, task_id) == [BUILDER] and _said(c, task_id, BUILDER) == first * 2  # the same words
    assert later(seconds=10) == {}  # once: it is queued again
    X.ran(c, task_id, BUILDER)
    assert later(seconds=120) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)  # a run, then a try
    _lose(c, task_id)  # try 1's wake goes missing too: written again, and still try 1
    assert later(seconds=10) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)
    assert _said(c, task_id, BUILDER)[-1] == "You stopped before this task was finished. Try 1 of 3."
    # A wake that never queued a run (a bot that reads its own inbox) is not lost: nothing is written again.
    X.ran(c, task_id, BUILDER)
    c.execute("UPDATE messages SET refs=json_remove(refs, '$.job') WHERE wake_key LIKE ?", (task_id + ":%",))
    assert later(seconds=900).get("woken") == 1 and _ladder(c, task_id) == ("owner", 2)
    c.execute("UPDATE messages SET refs=json_remove(refs, '$.job') WHERE wake_key LIKE ?", (task_id + ":%",))
    _lose(c, task_id)
    assert later(seconds=10) == {} and _ladder(c, task_id) == ("owner", 2)


def _never_started(c, task_id, actor, times=1):
    """The bot's queued wakes for a task are taken by its computer `times` times each and never start: every lease
    fails before the run begins, and then the job fails."""
    rows = c.execute(
        "SELECT j.id, j.message_id FROM jobs j JOIN messages m ON m.id = j.message_id "
        "WHERE j.state='queued' AND j.bot_id=? AND m.wake_key LIKE ?",
        (H.actor_id(actor), task_id + ":%"),
    ).fetchall()
    for job_id, message_id in rows:
        for _ in range(times):
            run = H.new_id()
            c.execute(
                "INSERT INTO attempts (id, job_id, bot_id, runner_id, generation, token_hash, state, lease_until, "
                "created_at, finished_at, message_id, task_id) VALUES (?,?,?,'r1',1,?,'failed',?,?,?,?,?)",
                (run, job_id, H.actor_id(actor), run, H.now(), H.now(), H.now(), message_id, task_id),
            )
        c.execute("UPDATE jobs SET state='failed', attempt_id=? WHERE id=?", (run, job_id))
    return len(rows)


def test_a_run_that_never_started_is_not_a_run_and_its_wake_is_written_again_once_per_try(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    first = _said(c, task_id, BUILDER)
    assert _never_started(c, task_id, BUILDER, times=4) == 1  # four leases, and none of them started
    assert _runs_since_change(c, task_id) == 0
    assert later(seconds=10) == {"woken": 1} and _ladder(c, task_id) == ("owner", 0)  # the same words, no try
    assert _said(c, task_id, BUILDER) == first * 2
    _never_started(c, task_id, BUILDER)  # the replacement never starts either: this try is spent
    assert later(seconds=10) == {}
    assert later(seconds=100) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)  # at its time, no sooner
    assert _said(c, task_id, BUILDER)[-1] == "You stopped before this task was finished. Try 1 of 3."
    # A bot that can never start still climbs the ladder to its manager bot and then a person.
    for _ in range(40):
        if _ladder(c, task_id)[0] == "person":
            break
        for bot in (BUILDER, MANAGER):
            _never_started(c, task_id, bot)
        later(minutes=10)
    assert _ladder(c, task_id)[0] == "person" and _runs_since_change(c, task_id) == 0
    assert any(line.startswith("builder stopped 3 times") for line in _said(c, task_id, MANAGER))


def _reads_its_inbox(c, slug):
    """The bot is run by an outside agent that reads its own inbox: its wakes queue no job."""
    c.execute("UPDATE bot_settings SET harness='hermes' WHERE bot_id=?", (slug,))


def _read(c, task_id, actor):
    """The bot reads its wakes about the task, as an outside agent does through its inbox."""
    c.execute(
        "UPDATE messages SET read_at=? WHERE wake_key LIKE ? AND recipient_id=? AND read_at IS NULL",
        (H.now(), task_id + ":%", actor),
    )


def test_a_bot_that_reads_its_own_inbox_is_on_its_way_until_it_reads_its_wake(c, general, later):
    _reads_its_inbox(c, "builder")
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    assert _queued(c, task_id) == [] and len(_said(c, task_id, BUILDER)) == 1  # its start, in its inbox
    assert later(minutes=30) == {} and _ladder(c, task_id) == ("owner", 0)  # not read yet: nothing more is sent
    assert len(_said(c, task_id, BUILDER)) == 1
    _read(c, task_id, BUILDER)  # it read its wake, and did nothing
    assert later(seconds=119) == {}
    assert later(seconds=1) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)  # a try, not a free wake
    assert _said(c, task_id, BUILDER)[-1] == "You stopped before this task was finished. Try 1 of 3."
    assert _runs_since_change(c, task_id) == 0  # nothing ran
    # It never reads again: the ladder waits on it, and Late takes the task to its manager bot and then a person.
    assert later(hours=23) == {}
    assert later(hours=1) == {"late": 1, "to_manager": 1, "woken": 1} and _ladder(c, task_id) == ("manager", 1)
    assert later(days=2) == {"to_person": 1}


def test_a_bot_that_runs_jobs_gets_one_free_wake_per_change_and_then_its_try(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    X.ran(c, task_id, BUILDER)  # its start
    E.add_requirement(c, task_id, ANA, "ok", "human:ben", "OK the wording")  # a change, and no run since
    assert later(seconds=120) == {"woken": 1} and _ladder(c, task_id) == ("owner", 0)
    assert _said(c, task_id, BUILDER)[-1] == "This task is not finished. Carry on."
    _lose(c, task_id)
    assert later(seconds=10) == {"woken": 1} and _ladder(c, task_id) == ("owner", 0)  # lost: written again
    _lose(c, task_id)
    assert later(seconds=10) == {}  # lost twice: the try is spent, at its time
    assert later(seconds=100) == {"woken": 1} and _ladder(c, task_id) == ("owner", 1)
    assert _said(c, task_id, BUILDER).count("This task is not finished. Carry on.") == 2  # the free one, and again


def test_every_bot_with_a_turn_gets_back_a_wake_it_lost_not_only_the_one_the_ladder_tries(c, general, later):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    E.add_requirement(c, task_id, ANA, "answer", REVIEWER, "OK the wording")  # the builder still has its own work
    asked = _said(c, task_id, REVIEWER)
    c.execute("DELETE FROM jobs WHERE bot_id='reviewer' AND state='queued'")  # the reviewer's wake goes missing
    assert later(seconds=10) == {"woken": 1} and _queued(c, task_id) == [BUILDER, REVIEWER]
    assert _said(c, task_id, REVIEWER) == asked * 2 and _ladder(c, task_id) == ("owner", 0)  # same words, no try
    assert later(seconds=10) == {}  # once


def test_a_second_wake_for_one_bot_and_task_folds_into_one(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    first = task_wakes.add(c, row, BUILDER, row["visit"], "First words.", wake_key=f"{row['id']}:a")
    second = task_wakes.add(c, row, BUILDER, row["visit"], "Second words.", wake_key=f"{row['id']}:b")
    assert first and second and _queued(c, row["id"]) == [BUILDER]  # one run starts
    body = c.execute("SELECT body FROM messages WHERE id=?", (second,)).fetchone()[0]
    assert body.index("First words.") < body.index("Second words.")  # nothing said is lost, in order
    assert task_wakes.add(c, row, BUILDER, row["visit"], "Again.", wake_key=f"{row['id']}:b") is None
    quiet = task_wakes.add(c, row, BUILDER, row["visit"], "A note.", wake_key=f"{row['id']}:c", quiet=True)
    assert quiet and _queued(c, row["id"]) == [BUILDER]  # a quiet note folds nothing and starts nothing


def test_a_late_task_with_a_bots_turn_goes_to_the_manager_bot_even_when_a_person_is_asked_too(c, general, later):
    row = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
    task_id = row["id"]
    X.ran(c, task_id, BUILDER)
    E.add_requirement(c, task_id, ANA, "ok", "human:ben", "OK the wording")  # Ben is asked; the builder still has work
    assert _states(c, task_id)[0] == ("person", "human:ben")
    _settled(c)
    assert later(hours=24).get("late") == 1 and _ladder(c, task_id)[0] == "manager"
    assert all("It waits on" not in line for line in _said(c, task_id, MANAGER))  # not a look: the manager has it


def test_a_task_with_everything_met_moves_on_the_next_pass_and_takes_a_free_slot(c, general, later, time_machine):
    stages = (
        {"name": "On deck"},
        {"name": "Build", "owner": BUILDER, "task_limit": 1, "requirements": [{"kind": "owner_done"}]},
    )
    X.pipeline(c, "eng", "Engineering", stages, manager=MANAGER)
    first = X.new_task(c, ANA, "Build the importer", "eng")["id"]
    time_machine.shift(timedelta(seconds=1))  # the clock stands still here: without this the two enter at once
    second = X.new_task(c, ANA, "Build the exporter", "eng")["id"]
    assert later(seconds=10) == {"moved": 1}  # On deck needs nothing, and Build has room for one
    assert E.task(c, first)["stage"]["name"] == "Build" and _states(c, second) == [("room", "eng-build")]
    X.say_done(c, E.task(c, first))  # written straight into the table: nothing told the engine
    assert later(seconds=10) == {"moved": 1}
    assert later(seconds=10) == {"moved": 1}  # the slot it left is filled on the next pass
    assert E.task(c, first)["status"] == "done" and E.task(c, second)["stage"]["name"] == "Build"
    assert _states(c, second) == [("bot", BUILDER)] and X.wakes(c, second, BUILDER)


def test_a_task_the_check_cannot_work_out_is_left_as_it_was_and_the_rest_still_happen(c, general, later, monkeypatch):
    broken = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    fine = X.new_task(c, ANA, "Fix the footer", assignee=BUILDER)["id"]
    for task_id in (broken, fine):
        X.ran(c, task_id, BUILDER)
    real = NS.W.of

    def of(conn, row, *rest):
        if row["id"] == broken:
            conn.execute("UPDATE visits SET tries=2 WHERE task_id=?", (broken,))
            raise ValueError("no such column")
        return real(conn, row, *rest)

    monkeypatch.setattr(NS.W, "of", of)
    failures = []
    later_at = H.shift(H.now(), seconds=120)
    done = NS.check(c, later_at, failures)
    assert [(task_id, type(error).__name__) for task_id, error in failures] == [(broken, "ValueError")]
    assert done["woken"] == 1 and _ladder(c, fine) == ("owner", 1)
    assert _ladder(c, broken) == ("owner", 0)  # what it half did was rolled back
    with pytest.raises(ValueError):
        NS.check(c, later_at)  # with nowhere to report it, the error is raised


def test_a_deleted_snoozed_task_leaves_nothing_the_check_trips_on(c, general, later):
    from backend import task_delete

    task_id = X.new_task(c, ANA, "Chase the vendor", assignee=BUILDER)["id"]
    X.ran(c, task_id, BUILDER)
    E.snooze(c, task_id, ANA, until=H.shift(H.now(), minutes=10))
    assert task_delete.delete_tasks(c, [task_id], apply=True, keep_work=True)["applied"]
    assert c.execute("SELECT COUNT(*) FROM snoozes WHERE task_id=? AND ended_at IS NULL", (task_id,)).fetchone()[0] == 0
    failures: list[tuple[str, Exception]] = []
    assert NS.check(c, H.shift(H.now(), minutes=11), failures) == dict.fromkeys(
        ("snoozes_ended", "moved", "woken", "to_manager", "to_person", "late"), 0
    )
    assert failures == []
    # A snooze a delete left open before it ended them, and the deleted task's open visit, are no task's: every read
    # passes them by.
    c.execute("UPDATE snoozes SET ended_at=NULL, ended_by_id=NULL WHERE task_id=?", (task_id,))
    assert later(minutes=11) == {} and NS.rules_hold(c) == []


def test_each_rule_that_breaks_is_named(c, general, later, monkeypatch):
    stages = ({"name": "Build", "owner": BUILDER, "task_limit": 1, "requirements": [{"kind": "owner_done"}]},)
    X.pipeline(c, "eng", "Engineering", stages, manager=MANAGER)
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    mine = X.new_task(c, ANA, "Call the bank", assignee=ANA)["id"]
    built = X.new_task(c, ANA, "Build the importer", "eng")["id"]
    assert NS.rules_hold(c) == []

    def broken():
        return sorted({line.split(":")[0] for line in NS.rules_hold(c)})

    # 2: a bot's turn with no run, no wake waiting and no next try.
    _settled(c)
    c.execute("UPDATE visits SET next_try_at=NULL WHERE task_id=?", (task_id,))
    assert NS.rules_hold(c) == [f"2: task {task_id} is {BUILDER}'s turn with no run, no wake and no next try"]
    assert later(seconds=1) == {} and NS.rules_hold(c) == []  # the next pass gives it a next try
    # 7: a wake waiting for a snoozed task.
    E.snooze(c, task_id, ANA)
    c.execute(
        "UPDATE jobs SET state='queued' WHERE message_id IN (SELECT id FROM messages WHERE wake_key LIKE ?)",
        (task_id + ":%",),
    )
    assert broken() == ["7"]
    _settled(c)
    # 6: a stage over its limit by forward moves.
    c.execute("UPDATE visits SET entered_how='met' WHERE task_id=?", (built,))
    c.execute("UPDATE stages SET task_limit=NULL WHERE id='eng-build'")
    assert broken() == []  # no limit
    other = X.new_task(c, ANA, "Build the exporter", "eng")["id"]
    c.execute("UPDATE visits SET entered_how='met' WHERE task_id=?", (other,))
    c.execute("UPDATE stages SET task_limit=1 WHERE id='eng-build'")
    assert broken() == ["6"]
    c.execute("UPDATE stages SET task_limit=NULL WHERE id='eng-build'")
    # 4: the guard left open, and a second open visit cannot exist at all.
    c.execute("UPDATE task_move_guards SET open=1, task_id=? WHERE id=1", (mine,))
    assert broken() == ["4"]
    c.execute("UPDATE task_move_guards SET open=0, task_id=NULL WHERE id=1")
    # 3: a person's turn that is not in their list exactly once.
    monkeypatch.setattr(NS.W, "needs_by_person", lambda conn: {})
    assert NS.rules_hold(c) == [f"3: task {mine} shows 0 times in {ANA}'s Needs you, not 1"]
    monkeypatch.undo()
    # 1: an open task that waits on nothing.
    monkeypatch.setattr(NS.W, "of", lambda conn, row, *rest: [])
    assert "1" in broken()
    monkeypatch.undo()
    # 5: two handoffs from one visit.
    visit = _visit(c, built)["id"]
    for n, to in enumerate((mine, other)):
        c.execute(
            "INSERT INTO links (id, from_task_id, to_task_id, visit_id, kind, created_at) VALUES (?,?,?,?,?,?)",
            (f"link{n}", built, to, visit, "handoff", H.now()),
        )
    assert broken() == ["5"]


def test_the_heartbeat_runs_the_check_and_reports_a_task_it_could_not_check(api, time_machine, monkeypatch):
    from backend import heartbeat as HB
    from backend import scheduler as S
    from backend.scheduler import Scheduler

    time_machine.move_to(START, tick=False)
    with api.app.state.store.transaction() as c:
        X.bot(c, "builder")
        X.pipeline(c)
        task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
        X.ran(c, task_id, BUILDER)
    scheduler = Scheduler(api.app.state.store, api.app.state.execution)
    at = START + timedelta(seconds=120)
    time_machine.move_to(at, tick=False)
    words, quiet = S.step_never_stuck(HB.Context(scheduler, at))
    assert (words, quiet) == ("1 woken", False)
    words, quiet = S.step_never_stuck(HB.Context(scheduler, at))
    assert quiet  # doing it twice is harmless
    monkeypatch.setattr(NS.W, "of", lambda *a: (_ for _ in ()).throw(ValueError("no such column")))
    ctx = HB.Context(scheduler, at)
    with pytest.raises(HB.Failed, match="1 task could not be checked"):
        S.step_never_stuck(ctx)
    # The pass's failures reach service_health, which everyone reads: the error type and a count, never the task's id.
    assert ctx.failures == [{"never_stuck": "ValueError", "tasks": "1"}]
    assert task_id not in repr(ctx.failures)


def test_a_private_rows_failure_keeps_its_id_and_words_out_of_service_health(c):
    """service_health is readable by everyone (sql.py). Work on a task (its id may be a private task's, its error may
    quote the title) records the error type only, under a digest of the row; the row clears it when it next works."""
    import json

    from backend.batch_work import isolated

    secret = "task-secret-id"
    failures: list[dict[str, str]] = []
    with isolated(c, "ship_deployed", secret, failures, private=True):
        raise ValueError("Acquire Initech: the title")
    rows = c.execute(
        "SELECT service, last_error, detail FROM service_health WHERE service LIKE 'background:%'"
    ).fetchall()
    assert len(rows) == 1 and failures == [{"ship_deployed": "ValueError"}]
    shown = json.dumps([tuple(row) for row in rows])
    assert secret not in shown and "Initech" not in shown and "ValueError" in shown
    with isolated(c, "ship_deployed", secret, failures, private=True):
        pass
    assert not c.execute("SELECT 1 FROM service_health WHERE service LIKE 'background:%'").fetchone()


def _private(c, title, owner, pipeline="general"):
    made = H.task_create(c, ANA, title, "Only us.", owner, pipeline=pipeline, private=True, lint=False)
    assert H.task_private(c, H.task(c, made["id"])) and H.task_readers(H.task(c, made["id"])) == []
    return made["id"]


def test_a_private_task_never_gains_a_reader_and_goes_to_a_person_who_reads_it_not_a_manager_bot_who_cannot(
    c, general, later
):
    """Private tasks: same engine, and who can see them does not change. The engine never adds a reader: the manager
    bot's look and its tries are skipped when it cannot read the task, which goes to a person who can. Health names no
    task's title."""
    from backend import health

    stages = ({"name": "Draft", "owner": ANA, "expected_minutes": 60, "requirements": [{"kind": "owner_done"}]},)
    X.pipeline(c, "ops", "Ops", stages, manager=MANAGER)
    hers = _private(c, "Sort the March invoices", ANA, "ops")
    assert H.task(c, hers)["owner_id"] == ANA
    assert later(minutes=61) == {}  # her own task is never chased (no due date: never Late); no bot looks
    assert later(hours=3) == {}  # her own turn already: nothing is added
    assert H.task_readers(H.task(c, hers)) == [] and X.wakes(c, hers, MANAGER) == []
    X.pipeline(c, "intake", "Intake", ({"name": "Triage", "requirements": [{"kind": "owner_done"}]},), manager=MANAGER)
    nobodys = _private(c, "Sort the April invoices", ANA, "intake")  # no owner: the manager bot's turn to pick one
    assert later(seconds=10) == {"to_person": 1} and _visit(c, nobodys)["person_id"] == ANA
    assert H.task_readers(H.task(c, nobodys)) == [] and X.wakes(c, nobodys, MANAGER) == []
    built = _private(c, "Draft the salary review", BUILDER)
    for gap in (0, 120, 900, 3600):
        if gap:
            assert later(seconds=gap) == {"woken": 1}
        X.ran(c, built, BUILDER)
    assert later(seconds=120) == {"to_person": 1}  # three tries: the manager bot cannot read it, so its requester
    assert _ladder(c, built)[0] == "person" and _visit(c, built)["person_id"] == ANA
    assert H.task_readers(H.task(c, built)) == [] and X.wakes(c, built, MANAGER) == []
    shared = _private(c, "Draft the bonus plan", BUILDER)
    H.set_task_readers(c, ANA, shared, add=[MANAGER])  # a person shared it with the manager bot
    for gap in (0, 120, 900, 3600):
        if gap:
            assert later(seconds=gap) == {"woken": 1}
        X.ran(c, shared, BUILDER)
    assert later(seconds=120) == {"to_manager": 1, "woken": 1} and _ladder(c, shared) == ("manager", 1)
    reading = repr(health.never_stuck_reading(c))
    assert not any(title in reading for title in ("invoices", "salary review", "bonus plan"))


def test_a_bot_whose_ok_a_stage_needs_is_woken_beside_the_owner_and_a_lost_wake_of_it_breaks_a_rule(c, general, later):
    """A stage needs the owner's work and the reviewer's OK: both have a turn from the start, so both are woken, the
    reviewer once for the stage's ask (keyed by its requirement). The next pass writes a lost wake again, and a bot
    with a turn that was never told breaks rule 2 though the owner has a next try."""
    stages = (
        {
            "name": "Check",
            "owner": BUILDER,
            "requirements": [{"kind": "owner_done"}, {"kind": "ok", "person": REVIEWER, "line": "OK it"}],
        },
    )
    X.pipeline(c, "copy", "Copy", stages, manager=MANAGER)
    row = X.new_task(c, ANA, "Write the launch note", "copy")
    assert _queued(c, row["id"]) == [BUILDER, REVIEWER]
    assert _said(c, row["id"], REVIEWER) and _said(c, row["id"], REVIEWER)[-1] == "You are asked: OK it"
    asked = c.execute("SELECT id FROM requirements WHERE stage_id='copy-check' AND kind='ok'").fetchone()[0]
    assert [key for key, _ in X.wakes(c, row["id"], REVIEWER)] == [f"{row['id']}:{row['visit']['id']}:ask:{asked}"]
    lose = "DELETE FROM jobs WHERE state='queued' AND bot_id='reviewer' AND message_id IN (SELECT id FROM messages "
    lose += "WHERE wake_key LIKE ?)"
    c.execute(lose, (row["id"] + ":%",))
    later(seconds=10)
    assert _queued(c, row["id"]) == [BUILDER, REVIEWER]  # a lost wake is written again
    c.execute(lose, (row["id"] + ":%",))
    c.execute("DELETE FROM messages WHERE recipient_id=? AND wake_key LIKE ?", (REVIEWER, row["id"] + ":%"))
    # Never told at all (as before, when entry woke only the owner): the owner's next try does not cover it.
    assert NS.rules_hold(c) == [f"2: task {row['id']} is {REVIEWER}'s turn with no run, no wake and no next try"]


def test_the_rules_check_reads_a_persons_needs_you_once_and_finds_each_task_in_it_by_its_id(c, general, monkeypatch):
    """Speed: one person with 5,000 tasks in Needs you. The check reads the list once per reading and indexes it by
    task, so each task is found by its id: a scan of the whole list for every task was 25 million looks."""
    tasks = [X.new_task(c, ANA, title, assignee=ANA)["id"] for title in ("Call the bank", "Sign the lease", "Pay rent")]
    assert NS.rules_hold(c) == []
    reads = {"needs": 0, "items": 0}

    class Item(dict):
        def get(self, key, default=None):
            reads["items"] += 1
            return super().get(key, default)

        def __getitem__(self, key):
            reads["items"] += 1
            return super().__getitem__(key)

    real = W.needs_by_person

    def needs(conn):
        reads["needs"] += 1
        found = [Item(item) for item in real(conn)[ANA]]
        return {ANA: found + [Item(kind="task", task_id=f"other-{n}") for n in range(5_000 - len(found))]}

    monkeypatch.setattr(NS.W, "needs_by_person", needs)
    assert NS.rules_hold(c) == []
    assert reads["needs"] == 1
    assert reads["items"] <= 5_000 + 10 * len(tasks), reads  # each item looked at once, not once per task


def _company_where_only_a_group_reads(c):
    """A real company shape: every bot reports to Bo, who can no longer sign in. Ana owns the company and is above
    nobody's bots; Cy is in the Ops group. Neither reads a private task of the bots' until `group:ops` is on its reader
    list, and then Cy does, through the group only."""
    from backend import access as Access
    from backend.views import roster

    for slug, name in (("bo", "Bo"), ("cy", "Cy")):
        c.execute("INSERT INTO humans(id,name,email) VALUES(?,?,?)", (slug, name, f"{slug}@acme.example"))
    whole = roster(c)
    people = [*whole["people"], {"id": "bo", "name": "Bo"}, {"id": "cy", "name": "Cy", "team": "ops"}]
    Access.save_roster(c, {**whole, "people": people, "org_groups": {"ops": {"name": "Ops"}}})
    c.execute("UPDATE bot_config SET reports_to='human:bo'")
    X.no_sign_in(c, "human:bo")


def test_a_private_task_no_person_can_read_goes_to_no_one_and_health_counts_it_without_naming_it(
    c, general, time_machine
):
    """A bot asked for it, a bot has it, and no person who can act reads it (the bots' person, Bo, cannot sign in).
    When the ladder runs out it goes to no person: nobody is made a reader, it is in nobody's Needs you, no bot is woken
    again, and the rules check reports it held for that cause. Health tells the company's owner and Admins only how
    many, never which. Once the Ops group, Cy's, is on its reader list, the next pass names Cy's turn."""
    from backend import health

    def step(**delta):
        time_machine.shift(timedelta(**delta))
        return {key: n for key, n in NS.check(c).items() if n}

    _company_where_only_a_group_reads(c)
    made = H.task_create(c, REVIEWER, "Draft the salary review", "Only us.", BUILDER, private=True, lint=False)
    task_id = made["id"]
    assert H.task_readers(H.task(c, task_id)) == [] and not NS._reads(c, ANA, E.task(c, task_id))
    for gap in (0, 120, 900, 3600):
        if gap:
            assert step(seconds=gap) == {"woken": 1}
        X.ran(c, task_id, BUILDER)
    assert step(seconds=120) == {"to_person": 1}  # three tries; the manager bot cannot run it to a person
    visit = _visit(c, task_id)
    assert visit["level"] == "person" and visit["person_id"] is None
    assert H.task_readers(H.task(c, task_id)) == [] and X.wakes(c, task_id, MANAGER) == []
    assert all(n.get("task_id") != task_id for n in W.needs(c, ANA))  # its title is in nobody's list
    assert _states(c, task_id)[0] == ("person", None)
    assert "woken" not in step(days=30) and _queued(c, task_id) == []  # no bot is woken again
    broken = NS.rules_hold(c)
    assert broken == [f"3: private task {task_id} has no one who reads it to move it"]
    assert getattr(broken[0], "held", False)  # held for a cause, not a broken rule
    health.record_never_stuck(_Store(c))
    check = health._never_stuck_check(c)
    assert check["counts"]["no_one"] == 1 and check["counts"]["broken"] == 0 and check["status"] == "warn"
    assert "1 private task has no one to move it." in check["summary"] and "salary" not in repr(check)
    # A person who reads it only through a group: found through the group's members, on the next pass.
    c.execute("UPDATE tasks SET readers=? WHERE id=?", (H._dump(["group:ops"]), task_id))
    cy = "human:cy"
    assert NS._reads(c, cy, E.task(c, task_id)) and not NS._reads(c, ANA, E.task(c, task_id))
    assert W.person_for(c, E.task(c, task_id)) == cy and NS._person(c, E.task(c, task_id)) == cy
    step(minutes=10)
    assert _states(c, task_id)[0] == ("person", cy)
    assert any(n.get("task_id") == task_id for n in W.needs(c, cy))
    assert all(n.get("task_id") != task_id for n in W.needs(c, ANA))
    assert NS.rules_hold(c) == []
    assert H.task_readers(H.task(c, task_id)) == ["group:ops"]  # nobody was added
    # The reading is hourly: a task closed since it was taken no longer counts, and an old reading says when it was
    # read (Globex rc.4: "14 can't run" stood after all 14 were done).
    E.move(c, task_id, E.CLOSED, REVIEWER, "Not needed", seen_version=H.task(c, task_id)["version"])
    time_machine.shift(timedelta(minutes=1))
    closed = health._never_stuck_check(c)
    assert closed["counts"]["no_one"] == 0 and closed["status"] == "ok" and closed["at"] == check["at"]
    assert "Read at" in closed["summary"] and "Read at" not in check["summary"]


class _Store:
    """One open connection as a store, for a reading recorded inside a test's transaction."""

    def __init__(self, conn):
        self.conn = conn

    def read(self):
        return _Here(self.conn)

    transaction = read


class _Here:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


def test_a_private_task_that_waits_on_someone_who_cannot_read_it_is_stuck_at_once_and_goes_to_a_person_who_can(
    c, general, later
):
    """Codex review 5, after creation: a stage edited to ask Ben, or a private task Ben is taken off, waits on someone
    who may not open it. That is stuck now, with no time to wait out: the next pass hands it to a person who reads it
    (the ladder's own person), whose Needs you says whom to add under Who can see. Ben is told nothing: no wake, no
    Needs you row, no title."""
    stages = ({"name": "Draft", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},)
    payroll = X.pipeline(c, "payroll", "Payroll", stages, manager=MANAGER)
    ben = "human:ben"
    made = H.task_create(c, ANA, "Salary bands", "Only us.", ANA, pipeline="payroll", private=True, lint=False)
    hers = made["id"]
    assert later(seconds=10) == {}
    # The pipeline is edited: Draft now asks Ben for an OK. Nothing tells the editor a private task is there.
    X.requirement(c, payroll["stages"]["Draft"], "ok", person=ben, line="OK the bands", position=1)
    assert later(seconds=10) == {"to_person": 1}
    assert _visit(c, hers)["level"] == "person" and _visit(c, hers)["person_id"] == ANA
    line = next(n["line"] for n in W.needs(c, ANA) if n.get("task_id") == hers)
    assert line == "Stuck in Draft: add Ben under Who can see"
    assert all(n.get("task_id") != hers for n in W.needs(c, ben))
    assert X.wakes(c, hers, ben) == [] and H.task_readers(H.task(c, hers)) == []
    assert later(minutes=10) == {}  # once
    # A bot's private task Ben could read, until Ben is taken off it.
    asks = (
        {"name": "Check", "owner": BUILDER, "requirements": [{"kind": "owner_done"}, {"kind": "ok", "person": ben}]},
    )
    X.pipeline(c, "pay", "Pay", asks, manager=MANAGER)
    made = H.task_create(c, ANA, "Bonus pool", "Only us.", BUILDER, pipeline="pay", private=True, readers=[ben])
    shared = made["id"]
    assert any(n.get("task_id") == shared for n in W.needs(c, ben))
    later(seconds=10)
    assert _visit(c, shared)["level"] == "owner"
    H.set_task_readers(c, ANA, shared, remove=[ben])
    assert all(n.get("task_id") != shared for n in W.needs(c, ben))
    assert later(seconds=10) == {"to_person": 1} and _visit(c, shared)["person_id"] == ANA
    line = next(n["line"] for n in W.needs(c, ANA) if n.get("task_id") == shared)
    assert line == "Stuck in Check: add Ben under Who can see"
    assert NS.rules_hold(c) == []
    H.set_task_readers(c, ANA, shared, add=[ben])  # Ana shares it again: Ben's OK is his
    assert any(n.get("task_id") == shared and n["kind"] == "ask" for n in W.needs(c, ben))
