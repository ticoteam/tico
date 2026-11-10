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


def test_close_and_reassign_go_through_the_task_calls_with_the_rule_as_why(c):
    row = task(c)
    carry(c, row, R.ACTION, {"action": "assign", "owner": "bot:ops"})
    moved = c.execute(
        "SELECT actor_id, old, new, note FROM history_events WHERE subject_type='task' AND subject_id=? "
        "AND kind='reassign'",
        (row["id"],),
    ).fetchone()
    assert tuple(moved) == (H.KEEPER, "bot:cpo", "bot:ops", "Rule rule_x")
    carry(c, H.task(c, row["id"]), R.ACTION, {"action": "close", "note": "Not needed"})
    after = H.task(c, row["id"])
    assert after["status"] == "closed"
    closed = c.execute(
        "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='move' ORDER BY rowid DESC",
        (row["id"],),
    ).fetchone()
    assert closed[0] == "Rule rule_x: Not needed"
    events = [
        json.loads(r[0])
        for r in c.execute(
            "SELECT detail FROM events WHERE action='rule.action' AND target=? ORDER BY rowid", (row["id"],)
        )
    ]
    assert [e["action"] for e in events] == ["assign", "close"]


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


def test_a_rule_never_changes_a_private_task_its_writer_cannot_read(c):
    unrelated_task_managers(c)
    row = task(c, by="bot:cpo", private=True)
    assert not H.task_private_readable(c, BEN, row)
    found = carry(c, row, R.ACTION, {"action": "close", "note": "Not needed"}, by=BEN)
    assert (found.outcome, found.rule) == (R.WAKE, R.FAIL_OPEN)
    assert H.task(c, row["id"])["status"] == "open"
