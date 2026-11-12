"""What a wake rule's `ask` and `action` outcomes do to the message's task (backend/rules.py `carry_out`): each is one
task call as Tico, with the rule's words as its why. A refused call is undone and the message wakes the bot instead
(`fail_open`). The decision is handed in as the engine would make it; the message path itself is test_rules."""

import json

import pytest

from backend import hubdb as H, rules as R
from backend.tests.helpers.api import unrelated_task_managers

ANA, BEN = "human:ana", "human:ben"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def task(c, by=ANA, owner="bot:cpo", title="Plan the launch", **kw):
    return H.task_create(c, by, title, "Plan it", owner, lint=False, deduplicate=False, **kw)


def carry(c, row, outcome, params=None, by=ANA, body="Step news"):
    decision = R.Decision(outcome, "rule_x", None, {"action": None, **(params or {})}, by)
    return R.carry_out(c, decision, H.KEEPER, row["owner_id"], "notice", {"task": row["id"]}, body, None)


def asks(c, tid):
    return [w for w in H.task_waits(c, [tid])[tid] if w.ask]


def test_an_ask_puts_one_ask_on_the_task_for_its_person_until_it_is_answered(c):
    row = task(c)
    assert carry(c, row, R.ASK, body="Is the date still right?").outcome == R.NOTE
    [wait] = asks(c, row["id"])
    assert (wait.state, wait.who) == ("person", ANA)
    assert "rule_x" in wait.line and "Is the date still right?" in wait.line
    carry(c, row, R.ASK)  # still waiting: no second ask
    assert len(asks(c, row["id"])) == 1
    H.task_answer(c, ANA, row["id"], wait.requirement_id, "Yes, the 12th")
    assert not asks(c, row["id"])
    carry(c, row, R.ASK)  # answered: the rule may ask again
    assert len(asks(c, row["id"])) == 1


def test_a_bots_request_asks_the_owner_bots_operator_and_with_none_wakes_the_bot(c):
    c.execute("UPDATE bot_config SET owner_id='nobody' WHERE bot='cpo'")  # an operator who is not on the roster
    row = task(c, by="bot:ops")
    assert (carry(c, row, R.ASK).outcome, carry(c, row, R.ASK).rule) == (R.WAKE, R.FAIL_OPEN)
    assert not asks(c, row["id"])
    c.execute("UPDATE bot_config SET owner_id='ben' WHERE bot='cpo'")
    assert carry(c, row, R.ASK).outcome == R.NOTE
    assert [w.who for w in asks(c, row["id"])] == [BEN]


def test_a_refused_move_is_undone_and_the_message_wakes_the_bot(c):
    row = task(c)
    found = carry(c, row, R.ACTION, {"action": "move", "stage": "done"})  # the owner has not said it is done
    assert (found.outcome, found.rule) == (R.WAKE, R.FAIL_OPEN)
    assert H.task(c, row["id"])["status"] == "open"
    assert not c.execute("SELECT 1 FROM events WHERE action='rule.action' AND target=?", (row["id"],)).fetchone()
    found = carry(c, row, R.ACTION, {"action": "move", "stage": "No such stage"})
    assert (found.outcome, found.rule) == (R.WAKE, R.FAIL_OPEN)


def test_an_action_rule_asks_a_named_person(c):
    row = task(c)
    carry(c, row, R.ACTION, {"action": "ask", "person": BEN, "line": "Can Ben review the plan?"})
    [wait] = asks(c, row["id"])
    assert (wait.who, wait.line) == (BEN, "Can Ben review the plan?")


def no_task(c, decision_by=ANA, room="room-1", body="Can someone check the Acme invoice?"):
    """An ask rule's decision on a message to cpo that names no task, in thread `room`."""
    decision = R.Decision(R.ASK, "rule_x", None, {}, decision_by)
    return R.carry_out(c, decision, H.KEEPER, "bot:cpo", "notice", {}, body, {"id": room} if room else None)


def rule_tasks(c):
    return c.execute(
        "SELECT t.id, t.owner_id, t.status, t.title, t.body FROM events e JOIN tasks t ON t.id=e.target "
        "WHERE e.action='rule.ask' ORDER BY e.rowid"
    ).fetchall()


def test_an_ask_with_no_task_files_one_open_task_for_the_bots_operator_per_thread(c):
    c.execute("UPDATE bot_config SET owner_id='ben' WHERE bot='cpo'")
    assert no_task(c).outcome == R.NOTE
    assert no_task(c).outcome == R.NOTE  # the same rule in the same thread: the open task stands
    [made] = rule_tasks(c)
    assert (made["owner_id"], made["status"]) == (BEN, "open")
    assert "rule_x" in made["body"] and "Acme invoice" in made["body"]
    no_task(c, room="room-2")  # another thread is another task
    assert len(rule_tasks(c)) == 2
    H.task_done(c, BEN, made["id"], "Checked")
    no_task(c)  # the first one ended: the rule may file again
    assert len(rule_tasks(c)) == 3


def test_an_ask_with_no_task_and_no_operator_files_it_for_the_rules_maker(c):
    c.execute("UPDATE bot_config SET owner_id='nobody' WHERE bot='cpo'")
    assert no_task(c, decision_by=ANA).outcome == R.NOTE
    assert [r["owner_id"] for r in rule_tasks(c)] == [ANA]
    found = no_task(c, decision_by="bot:ops", room="room-3")  # nobody to own it: the bot is woken instead
    assert (found.outcome, found.rule) == (R.WAKE, R.FAIL_OPEN)


def test_a_move_gives_the_rules_reason_for_a_reopen_a_close_or_a_move_back_and_none_forward(c):
    from backend.tests.helpers import engine as X

    row = task(c)
    H.task_done(c, ANA, row["id"], "Shipped")
    carry(c, H.task(c, row["id"]), R.ACTION, {"action": "move", "stage": "To do", "note": "Customer reopened it"})
    assert H.task(c, row["id"])["status"] == "open", "a done task moved to a work stage is reopened, with the reason"
    X.pipeline(c, "launch", "Launch", [{"name": "Draft"}, {"name": "Review"}])
    two = H.task_create(c, ANA, "Launch note", "Write it", "bot:cpo", lint=False, pipeline="launch")
    found = carry(c, two, R.ACTION, {"action": "move", "stage": "Review"})  # forward: never pushed past
    assert H.task(c, two["id"])["stage_id"] == "launch-review", found
    carry(c, H.task(c, two["id"]), R.ACTION, {"action": "move", "stage": "Draft", "note": "Needs a rewrite"})
    assert H.task(c, two["id"])["stage_id"] == "launch-draft", "a move back gives the reason"
    last = c.execute(
        "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='move' ORDER BY rowid DESC",
        (two["id"],),
    ).fetchone()
    assert last[0] == "Rule rule_x: Needs a rewrite"
