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
    c.execute(f"INSERT INTO stage_rules ({', '.join(rule)}) VALUES ({', '.join('?' * len(rule))})", tuple(rule.values()))


def test_quiet_bot_tasks_tells_each_manager_once_for_all_its_tasks_with_no_model_call(c, time_machine, jev_calls):
    assert SR.QUIET_BOT_TASKS in SR.ensure_defaults(c)
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
    assert [r[0] for r in history] == ["Rule Quiet bot tasks: Chief was told"] or "Quiet bot tasks" in history[0][0]
    events = c.execute("SELECT count(*) FROM events WHERE action='stage_rule.told'").fetchone()[0]
    assert events == 7

    time_machine.shift(timedelta(minutes=10))
    NS.check(c)
    assert len(_told(c)) == 2  # the same quiet stretch is told once


def test_a_task_already_listed_as_late_is_not_listed_again(c, time_machine):
    SR.ensure_defaults(c)
    late = _task(c, "eng", "Build", "bot:builder", "Already Late")
    quiet = _task(c, "eng", "Build", "bot:builder", "Only quiet")
    c.execute("UPDATE visits SET late_told_at=? WHERE task_id=? AND left_at IS NULL", (H.now(), late))
    time_machine.shift(timedelta(hours=6))
    T.sweep(c)
    (who, body), = _told(c)
    assert who == "bot:lead" and body.startswith("Stage rules: 1 task.")
    assert _title(c, quiet) in body and _title(c, late) not in body


def test_matches_in_the_same_hour_wait_for_the_managers_next_wake(c, time_machine):
    SR.ensure_defaults(c)
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
    SR.ensure_defaults(c)
    task_id = _task(c, "eng", "Build", "bot:builder", "Talked about")
    time_machine.shift(timedelta(hours=5))
    conv = H.conversation(c, H.task(c, task_id)["conversation_id"])
    H._write_message(c, ANA, "bot:builder", "How is it going?", conv, "say", {"task": task_id}, None, None)
    time_machine.shift(timedelta(hours=2))
    T.sweep(c)
    assert _told(c) == []
    time_machine.shift(timedelta(hours=4))
    T.sweep(c)
    assert len(_told(c)) == 1


def test_move_back_puts_the_task_in_on_deck_with_no_owner_tells_the_manager_and_sets_off_no_other_rule(
    c, time_machine
):
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
    (who, body), = _told(c)
    assert who == "bot:lead" and _title(c, task_id) in body
    assert _ran(c, task_id) == 1  # and not on the rule's move
    assert c.execute("SELECT count(*) FROM events WHERE action='stage_rule.moved_back'").fetchone()[0] == 1
