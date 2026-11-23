"""Stage rules whose When is a timer (backend/stage_rule_timers.py) on the never-stuck pass: Quiet bot tasks tells
each manager once for all its tasks, with no model call; Move back moves the task with no owner and sets off no other
rule (Stage rules spec, criteria 5 and 6).

Synthetic rows only (backend/tests/helpers/engine.py): two pipelines, each with its own manager bot, and a clock the
test moves.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import never_stuck as NS
from backend import stage_rule_timers as T
from backend import stage_rules as SR
from backend import task_depends
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)
STAGES = (
    {"name": "On deck", "owner": "assignee"},
    {"name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "Review", "owner": "assignee", "requirements": [{"kind": "ok", "person": ANA, "line": "OK it"}]},
)


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        for slug in ("builder", "fixer", "lead", "chief"):
            X.bot(conn, slug)
        X.pipeline(conn, "eng", "Eng", STAGES, manager="bot:lead")
        X.pipeline(conn, "ops", "Ops", STAGES, manager="bot:chief")
        yield conn


@pytest.fixture
def jev_calls(monkeypatch):
    """Every call a stage rule makes to Jev. Timers never make one."""
    calls = []
    monkeypatch.setattr(SR, "JEV", lambda *args: calls.append(args) or {"answers": {}})
    return calls


def _task(c, pipeline, stage, owner, title):
    row = X.new_task(c, owner, title, pipeline_id=pipeline, assignee=owner)
    if stage != "On deck":
        H.task_move(c, ANA, row["id"], f"{pipeline}-{stage.lower().replace(' ', '-')}", "set up for the test")
    return row["id"]


def _title(c, task_id):
    return f"- {H.task(c, task_id)['title']} ("


def _ran(c, task_id):
    """Task Trigger runs on the task, not counting those skipped as a rule's own change."""
    rows = c.execute(
        "SELECT note FROM history_events WHERE subject_id=? AND kind='rule' AND note NOT LIKE '%Skipped%'", (task_id,)
    ).fetchall()
    return len(rows)


def _told(c):
    """The managers' Stage rules wakes: (recipient, body)."""
    rows = c.execute(
        "SELECT recipient_id, body FROM messages WHERE json_extract(refs, '$.wake') = 'stage_rules' ORDER BY rowid"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _owner_told(c, task_id, rule_name):
    """The wakes a task's owner bot was sent for one stage rule: (recipient, body)."""
    rows = c.execute(
        "SELECT recipient_id, body FROM messages WHERE json_extract(refs, '$.task') = ? AND body LIKE ? ORDER BY rowid",
        (task_id, f"%Rule {rule_name}%"),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _history(c, task_id):
    """The Stage rules lines on a task's history, as a person reads them."""
    return [
        r[0]
        for r in c.execute(
            "SELECT note FROM history_events WHERE subject_id=? AND kind='stage_rule' ORDER BY rowid", (task_id,)
        )
    ]


def _rule(c, **fields):
    rule = {
        "id": fields.pop("id", "test-rule"),
        "name": fields.pop("name", "Test rule"),
        "pipeline_id": fields.pop("pipeline_id", ""),
        "stages": json.dumps(fields.pop("stages", [])),
        "event": "timer",
        "timer": json.dumps(fields.pop("timer")),
        "cond": json.dumps(fields.pop("cond", {})),
        "then_do": fields.pop("then"),
        "then_params": json.dumps(fields.pop("then_params", {})),
        "reason": fields.pop("reason", ""),
        "created_by": ANA,
        "created_at": H.now(),
        "updated_at": H.now(),
    }
    assert not fields
    c.execute(
        f"INSERT INTO stage_rules ({', '.join(rule)}) VALUES ({', '.join('?' * len(rule))})", tuple(rule.values())
    )


def test_quiet_bot_tasks_passes_over_a_task_whose_owner_is_mid_run(c, time_machine, jev_calls):
    working = _task(c, "eng", "Build", "bot:builder", "Long build")
    idle = _task(c, "eng", "Build", "bot:builder", "Forgotten build")
    X.settled(c)
    time_machine.shift(timedelta(hours=6))
    queued = c.execute(
        "SELECT j.id, j.message_id FROM jobs j JOIN messages m ON m.id = j.message_id "
        "WHERE j.bot_id='builder' AND m.wake_key LIKE ? LIMIT 1",
        (working + ":%",),
    ).fetchone()
    assert queued, "the task's wake made no job"
    run = H.new_id()
    c.execute(
        "INSERT INTO attempts (id, job_id, bot_id, runner_id, generation, token_hash, state, lease_until, created_at, "
        "started_at, message_id, task_id) VALUES (?,?,?,?,1,?,'running',?,?,?,?,?)",
        (run, queued[0], "builder", "r1", run, H.shift(H.now(), minutes=1), H.now(), H.now(), queued[1], working),
    )
    c.execute("UPDATE jobs SET state='running', attempt_id=? WHERE id=?", (run, queued[0]))
    NS.check(c)
    body = dict(_told(c))["bot:lead"]
    assert _title(c, idle) in body
    assert _title(c, working) not in body


def test_quiet_bot_tasks_tells_each_manager_once_for_all_its_tasks_with_no_model_call(c, time_machine, jev_calls):
    assert SR.get(c, SR.QUIET_BOT_TASKS)["status"] == "active"  # seeded at start, as every default rule
    eng = [_task(c, "eng", "Build", "bot:builder", f"Eng {n}") for n in range(5)]
    ops = [_task(c, "ops", "Build", "bot:fixer", f"Ops {n}") for n in range(2)]
    persons_turn = _task(c, "eng", "Review", "bot:builder", "Waits on Ana's OK")
    persons_own = _task(c, "eng", "Build", ANA, "Ana's own")
    X.settled(c)

    time_machine.shift(timedelta(hours=5, minutes=59))
    NS.check(c)
    assert _told(c) == []  # not 6 h quiet yet

    time_machine.shift(timedelta(minutes=1))
    NS.check(c)
    told = _told(c)
    assert [who for who, _ in told] == ["bot:lead", "bot:chief"] or [who for who, _ in told] == [
        "bot:chief",
        "bot:lead",
    ]
    bodies = dict(told)
    assert bodies["bot:lead"].startswith("Stage rules: 5 tasks.")
    assert bodies["bot:chief"].startswith("Stage rules: 2 tasks.")
    for task_id in eng:
        assert _title(c, task_id) in bodies["bot:lead"]
    for task_id in (persons_turn, persons_own):
        assert _title(c, task_id) not in bodies["bot:lead"]
    assert jev_calls == []

    history = c.execute(
        "SELECT note FROM history_events WHERE subject_id=? AND kind='stage_rule'", (ops[0],)
    ).fetchall()
    assert [r[0] for r in history] == ["Rule Quiet bot tasks: chief was told"]  # no display name, so the slug
    events = c.execute("SELECT count(*) FROM events WHERE action='stage_rule.told'").fetchone()[0]
    assert events == 7

    time_machine.shift(timedelta(minutes=10))
    NS.check(c)
    assert len(_told(c)) == 2  # the same quiet stretch is told once


def test_matches_in_the_same_hour_wait_for_the_managers_next_wake(c, time_machine):
    first = _task(c, "eng", "Build", "bot:builder", "First")
    time_machine.shift(timedelta(minutes=20))
    second = _task(c, "eng", "Build", "bot:builder", "Second")
    time_machine.shift(timedelta(hours=5, minutes=40))
    T.sweep(c)
    assert len(_told(c)) == 1  # the first one only: the second is not quiet yet
    time_machine.shift(timedelta(minutes=20))
    T.sweep(c)
    assert len(_told(c)) == 1  # the second matched, and waits: the manager was told less than an hour ago
    time_machine.shift(timedelta(minutes=40))
    T.sweep(c)
    told = _told(c)
    assert len(told) == 2 and told[1][1].startswith("Stage rules: 1 task.")
    assert _title(c, second) in told[1][1] and _title(c, first) not in told[1][1]


def test_a_comment_starts_the_quiet_timer_again(c, time_machine):
    task_id = _task(c, "eng", "Build", "bot:builder", "Talked about")
    time_machine.shift(timedelta(hours=5))
    H.task_comment(c, ANA, task_id, "How is it going?")
    time_machine.shift(timedelta(hours=2))
    T.sweep(c)
    assert _told(c) == []
    time_machine.shift(timedelta(hours=4))
    T.sweep(c)
    assert len(_told(c)) == 1


def test_move_back_puts_the_task_in_on_deck_with_no_owner_tells_the_manager_and_sets_off_no_other_rule(c, time_machine):
    c.execute("UPDATE stage_rules SET retired_at=? WHERE id=?", (H.now(), SR.QUIET_BOT_TASKS))  # this rule alone
    _rule(
        c,
        name="Quiet in Build",
        pipeline_id="eng",
        stages=["eng-build"],
        timer={"kind": "quiet", "hours": 12},
        then="move_back",
        then_params={"stage": "eng-on-deck"},
    )
    # A Task Trigger on every stage change, which a person's move would set off.
    c.execute(
        "INSERT INTO rules (id, scope_kind, scope_id, event, condition, then_outcome, params, created_by, action_by, "
        "created_at, updated_at) VALUES ('on-move','team','','task.step_changed','{}','action',?,?,?,?,?)",
        (json.dumps({"action": "note", "text": "moved"}), ANA, ANA, H.now(), H.now()),
    )
    task_id = _task(c, "eng", "Build", "bot:builder", "Stalled build")
    assert _ran(c, task_id) == 1  # the trigger runs on a person's move
    time_machine.shift(timedelta(hours=11, minutes=59))
    T.sweep(c)
    assert H.task(c, task_id)["stage_id"] == "eng-build"

    time_machine.shift(timedelta(minutes=1))
    done = T.sweep(c)
    row = H.task(c, task_id)
    assert done["moved"] == 1
    assert row["stage_id"] == "eng-on-deck" and not row["owner_id"]
    notes = [
        r[0] for r in c.execute("SELECT note FROM history_events WHERE subject_id=? AND kind='stage_rule'", (task_id,))
    ]
    assert notes[0].startswith("Rule Quiet in Build:")
    ((who, body),) = _told(c)
    assert who == "bot:lead" and _title(c, task_id) in body
    assert _ran(c, task_id) == 1  # and not on the rule's move
    assert c.execute("SELECT count(*) FROM events WHERE action='stage_rule.moved_back'").fetchone()[0] == 1


def test_a_snooze_is_not_quiet_time_the_timer_starts_when_it_ends(c, time_machine):
    task_id = _task(c, "eng", "Build", "bot:builder", "Snoozed one")
    X.settled(c)
    time_machine.shift(timedelta(hours=1))
    until = H.shift(H.now(), hours=24)
    E.snooze(c, task_id, "bot:builder", "waiting for the v0.6.0 release", until, H.task(c, task_id)["version"])
    time_machine.shift(timedelta(hours=24, minutes=1))
    NS.check(c)  # the snooze's date came: it ends and the owner is woken
    NS.check(c)
    assert _told(c) == []  # the bot was just woken to pick it up: not quiet
    time_machine.shift(timedelta(hours=6))
    NS.check(c)
    assert len(_told(c)) == 1


def test_a_block_that_ends_starts_the_quiet_hours_again(c, time_machine):
    """A task blocked by a blocker that is not quiet yet is left alone; when the block ends its owner is woken, and the
    quiet hours start again from there."""
    from backend import task_relations as TR

    _rule(c, timer={"kind": "quiet", "hours": 6}, then="tell_manager")

    blocker = _task(c, "eng", "Build", "bot:fixer", "The fix")
    task_id = _task(c, "eng", "Build", "bot:builder", "Blocked one")
    TR.relate(c, ANA, task_id, blocker, "depends_on")
    X.settled(c)
    time_machine.shift(timedelta(hours=5))
    NS.check(c)
    assert not any(_title(c, task_id) in body for _, body in _told(c))  # its blocker is not quiet for 6 h yet
    E.move(c, blocker, E.DONE, ANA, "Fixed")
    NS.check(c)
    assert not any(_title(c, task_id) in body for _, body in _told(c))  # just woken to pick it up: not quiet
    time_machine.shift(timedelta(hours=6, minutes=1))
    NS.check(c)
    assert any(_title(c, task_id) in body for _, body in _told(c))


def test_a_manager_who_cannot_be_woken_is_not_said_to_be_told(c, time_machine):
    task_id = _task(c, "eng", "Build", "bot:builder", "Nobody hears")
    c.execute("UPDATE bots SET state='archived' WHERE slug='lead'")
    time_machine.shift(timedelta(hours=6))
    T.sweep(c)
    assert _told(c) == []
    notes = [
        r[0] for r in c.execute("SELECT note FROM history_events WHERE subject_id=? AND kind='stage_rule'", (task_id,))
    ]
    assert not any("was told" in n for n in notes)
    assert c.execute("SELECT count(*) FROM events WHERE action='stage_rule.told'").fetchone()[0] == 0


def test_a_quiet_task_the_manager_owns_is_not_reported_to_that_manager(c, time_machine):
    _task(c, "eng", "Build", "bot:lead", "The manager's own")
    time_machine.shift(timedelta(hours=6))
    T.sweep(c)
    assert _told(c) == []


def test_waiting_on_a_person_tells_the_owner_once(c, time_machine):
    c.execute("UPDATE stage_rules SET retired_at=? WHERE id=?", (H.now(), SR.QUIET_BOT_TASKS))  # this rule alone
    _rule(
        c,
        name="Chase the OK",
        pipeline_id="eng",
        stages=["eng-review", "eng-build"],
        timer={"kind": "waiting_person", "hours": 2},
        then="tell_owner",
    )
    waiting = _task(c, "eng", "Review", "bot:builder", "Waits on Ana")
    working = _task(c, "eng", "Build", "bot:builder", "Bot's own work")  # in the rule's stages, waits on no person
    X.settled(c)
    time_machine.shift(timedelta(hours=1, minutes=59))
    assert T.sweep(c)["told"] == 0
    time_machine.shift(timedelta(minutes=1))
    assert T.sweep(c)["told"] == 1
    told = _owner_told(c, waiting, "Chase the OK")
    assert [who for who, _ in told] == ["bot:builder"] and "Rule Chase the OK" in told[0][1]
    assert _owner_told(c, working, "Chase the OK") == []
    time_machine.shift(timedelta(hours=1))
    assert T.sweep(c)["told"] == 0  # the same wait is told once
    assert len(_owner_told(c, waiting, "Chase the OK")) == 1


def test_a_task_that_ends_before_the_wake_is_dropped_not_told(c, time_machine):
    first = _task(c, "eng", "Build", "bot:builder", "Told")
    time_machine.shift(timedelta(minutes=30))
    gone = _task(c, "eng", "Build", "bot:builder", "Closed while waiting")
    named = _task(c, "eng", "Build", "bot:builder", "Still open")
    time_machine.shift(timedelta(hours=5, minutes=30))
    T.sweep(c)
    time_machine.shift(timedelta(minutes=30))
    T.sweep(c)  # gone and named wait for the next wake
    H.task_close(c, ANA, gone, "Not needed")
    time_machine.shift(timedelta(minutes=31))
    T.sweep(c)
    told = _told(c)
    assert len(told) == 2 and told[1][1].startswith("Stage rules: 1 task.") and _title(c, named) in told[1][1]
    assert any("was told" in note for note in _history(c, first))
    assert any("was told" in note for note in _history(c, named))
    assert not any("was told" in note for note in _history(c, gone))


# ----------------------------------------------------------------------------- Late, a stage rule
def _late(c, stage="Build", minutes=24 * 60, pipeline="eng"):
    """The stage's Late rule, as a preset or the release migration writes it."""
    stage_id = f"{pipeline}-{stage.lower()}"
    assert SR.add_late(c, stage_id, stage, pipeline, minutes)
    return f"sr_late_{stage_id}"


def _wake_refs(c, task_id, rule_name):
    rows = c.execute(
        "SELECT refs FROM messages WHERE json_extract(refs, '$.task') = ? AND body LIKE ? ORDER BY rowid",
        (task_id, f"%Rule {rule_name}%"),
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


def _answer(c, task_id):
    """A person's answer on the task's open visit (an OK or a question answered)."""
    visit = E.task(c, task_id)["visit"]
    requirement = c.execute("SELECT id FROM requirements WHERE stage_id=? LIMIT 1", (visit["stage_id"],)).fetchone()[0]
    c.execute(
        "INSERT INTO answers (id, visit_id, requirement_id, by_id, answer, created_at) VALUES (?,?,?,?,?,?)",
        (H.new_id(), visit["id"], requirement, ANA, "ok", H.now()),
    )


def _block(c, task_id, blocker_id):
    from backend import task_relations as TR

    assert TR.relate(c, ANA, task_id, blocker_id, "depends_on")


def test_late_is_a_stage_rule_in_the_stages_rules_with_the_same_words_everywhere(c):
    rule_id = _late(c)
    rule = SR.get(c, rule_id)
    assert (rule["name"], rule["pipeline_id"], rule["stages"], rule["status"]) == (
        "Late",
        "eng",
        ["eng-build"],
        "active",
    )
    assert rule["timer"] == {"kind": "in_stage", "hours": 24} and rule["then"] == "tell_owner"
    assert rule["cond"] == {"owner_kind": "bot", "turn": "bot"} and rule["builtin"] == SR.LATE
    shown = {r["name"]: r["reason"] for r in SR.applying(c, "eng", "eng-build")}
    assert shown["Late"] == "24 h in Build on the bot's turn"  # the run header's "Rules on this stage" line
    assert shown["Quiet bot tasks"] == "6 h quiet on the bot's turn, blocked tasks included"
    assert "Late" not in {r["name"] for r in SR.applying(c, "eng", "eng-review")}
    assert not SR.add_late(c, "eng-build", "Build", "eng", 60)  # once: a stage keeps the Late rule it has
    assert "Late" not in {item["name"] for item in SR.BUILT_IN}  # no longer a rule "in code"


def test_an_edited_late_rule_wakes_the_owner_once_naming_the_rule(c, time_machine):
    rule_id = _late(c)
    SR.edit(c, ANA, rule_id, {"timer": {"kind": "in_stage", "hours": 2}, "reason": "2 h in Build on the bot's turn"})
    task_id = _task(c, "eng", "Build", "bot:builder", "Quick build")
    X.settled(c)
    time_machine.shift(timedelta(hours=1, minutes=59))
    T.sweep(c)
    assert _owner_told(c, task_id, "Late") == []
    time_machine.shift(timedelta(minutes=1))
    T.sweep(c)
    ((who, body),) = _owner_told(c, task_id, "Late")
    assert who == "bot:builder" and "Rule Late: 2 h in Build." in body
    (refs,) = _wake_refs(c, task_id, "Late")
    assert (refs["stage_rule"], refs["stage_rule_name"], refs["stage_rules"]) == (
        rule_id,
        "Late",
        [rule_id],
    )  # wakes count per rule
    assert _history(c, task_id) == ["Rule Late: 2 h in Build."]
    time_machine.shift(timedelta(hours=5))
    T.sweep(c)
    assert len(_owner_told(c, task_id, "Late")) == 1  # once per visit


def test_a_persons_answer_restarts_the_late_clock_and_a_persons_turn_is_never_late(c, time_machine):
    _late(c, minutes=120)
    _late(c, stage="Review", minutes=120)
    building = _task(c, "eng", "Build", "bot:builder", "Build with a question")
    waiting = _task(c, "eng", "Review", "bot:builder", "Waits on Ana's OK")
    X.settled(c)
    time_machine.shift(timedelta(hours=1, minutes=30))
    _answer(c, building)  # the bot's whole time again from here
    time_machine.shift(timedelta(hours=1))
    T.sweep(c)
    assert _owner_told(c, building, "Late") == []  # 2.5 h in Build, but 1 h since the answer
    time_machine.shift(timedelta(hours=1))
    T.sweep(c)
    assert len(_owner_told(c, building, "Late")) == 1
    assert _owner_told(c, waiting, "Late") == []  # 3.5 h in Review, all of it Ana's turn


def test_a_dead_blocker_is_caught_once_a_live_one_is_left_alone(c, time_machine):
    _late(c, minutes=24 * 60)
    blocked = _task(c, "eng", "Build", "bot:builder", "Waits on the API")
    blocker = _task(c, "ops", "Build", "bot:fixer", "The API")
    live_blocked = _task(c, "eng", "Build", "bot:builder", "Waits on the schema")
    live_blocker = _task(c, "ops", "Build", "bot:fixer", "The schema")
    _block(c, blocked, blocker)
    _block(c, live_blocked, live_blocker)
    X.settled(c)
    time_machine.shift(timedelta(hours=35))
    E.changed(c, live_blocker, ANA)  # it moves within the rules' hours
    time_machine.shift(timedelta(hours=1))
    T.sweep(c)
    ((who, body),) = _owner_told(c, blocked, "Late")
    named = task_depends.ref(E.task(c, blocker))
    assert who == "bot:builder" and f"Rule Late: 24 h in Build. Blocked by {named}, no change on it for 36 h." in body
    assert "Chase it, take the link off, reassign or re-plan." in body
    assert _owner_told(c, live_blocked, "Late") == []  # its blocker changed an hour ago
    # Quiet bot tasks sees blocked tasks too: the manager hears of the dead blocker, not of the live one.
    body = dict(_told(c))["bot:lead"]
    assert _title(c, blocked) in body and _title(c, live_blocked) not in body


def test_a_blocked_task_is_caught_once_per_visit_however_often_its_blocker_goes_quiet(c, time_machine):
    _late(c, minutes=6 * 60)
    blocked = _task(c, "eng", "Build", "bot:builder", "Waits on the API")
    blocker = _task(c, "ops", "Build", "bot:fixer", "The API")
    _block(c, blocked, blocker)
    X.settled(c)
    time_machine.shift(timedelta(hours=7))
    T.sweep(c)
    assert len(_owner_told(c, blocked, "Late")) == 1
    E.changed(c, blocker, ANA)
    time_machine.shift(timedelta(hours=7))
    T.sweep(c)
    assert len(_owner_told(c, blocked, "Late")) == 1  # its blocker went quiet again: not on this visit


def test_a_timer_never_messages_a_person(c, time_machine):
    _rule(c, id="tell-anyone", name="Tell anyone", timer={"kind": "quiet", "hours": 1}, then="tell_owner")
    _rule(c, id="tell-boss", name="Tell the boss", timer={"kind": "quiet", "hours": 1}, then="tell_manager")
    c.execute("UPDATE pipelines SET manager_id=? WHERE id='ops'", (ANA,))  # a person manages Ops
    mine = _task(c, "eng", "Build", ANA, "Ana's own")
    managed = _task(c, "ops", "Build", "bot:fixer", "Managed by a person")
    X.settled(c)
    before = c.execute("SELECT count(*) FROM messages WHERE recipient_id=?", (ANA,)).fetchone()[0]
    time_machine.shift(timedelta(hours=2))
    T.sweep(c)
    time_machine.shift(timedelta(hours=2))
    T.sweep(c)
    assert c.execute("SELECT count(*) FROM messages WHERE recipient_id=?", (ANA,)).fetchone()[0] == before
    assert _owner_told(c, mine, "Tell anyone") == []
    assert [who for who, _ in _owner_told(c, managed, "Tell anyone")] == ["bot:fixer"]  # the owner bot still hears


def test_late_shows_while_nothing_changed_since_a_rule_caught_it(c, time_machine):
    from backend import task_reads

    rule_id = _late(c, minutes=60)
    task_id = _task(c, "eng", "Build", "bot:builder", "Slow build")
    X.settled(c)
    time_machine.shift(timedelta(minutes=30))
    assert T.caught(c, [task_id]) == {}
    other = _task(c, "eng", "Build", "bot:builder", "Fresh build")
    X.settled(c)
    time_machine.shift(timedelta(minutes=31))
    T.sweep(c)
    assert T.caught(c, [task_id, other]) == {task_id: {"since": H.now(), "rule": "Late"}}
    (row,) = task_reads.brief(c, [{"id": task_id, "owner_id": "bot:builder", "status": "open"}])
    assert row["late"] == {"since": H.now(), "rule": "Late"}
    assert H.task_view(c, task_id, ANA)["late"]["rule"] == "Late"
    matched = c.execute(f"SELECT id FROM tasks t WHERE {T.CAUGHT_SQL % 't'}").fetchall()
    assert [r[0] for r in matched] == [task_id]  # the Late filter
    time_machine.shift(timedelta(minutes=5))
    E.changed(c, task_id, "bot:builder")  # the bot moved it on
    assert T.caught(c, [task_id]) == {}
    assert SR.get(c, rule_id)["name"] == "Late"
