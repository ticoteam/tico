"""A feature that needs a person to act asks them a question (a ❓ on the task's chat), so it is in their Needs you
(backend/person_asks.py):
Needs you holds only what is asked of a person, and a plain task handed to them would be in no list they are told of.
One test per feature here; the Strategy Owner's asks (test_goal_asks.py, test_strategy.py, test_strategy_bot_acts.py),
legal mail (test_postal_mail.py) and a bot's task for a person through the API (test_ask_ingest.py) are in their own.

Synthetic rows only (backend/tests/helpers/engine.py).
"""

from types import SimpleNamespace

import pytest

from backend import ask_shape
from backend import person_asks as PA
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, headers  # noqa: F401
from backend.tests.helpers.ask_ingest import gate  # noqa: F401

ANA = X.ANA


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        X.bot(conn, "builder")
        X.bot(conn, "botops")  # the built-in bot that asks for Tico's own notices
        X.pipeline(conn)
        yield conn


def asked(c, person, task_id, by="bot:botops"):
    """The ❓ on `task_id` in `person`'s Needs you, each marked by `by` (a bot, never Tico: only bots write
    questions) on its own message, whose words, its line and then its why, pass the checks a bot's question passes
    (ask_shape.line_refusal)."""
    found = [n for n in W.needs(c, person) if n["kind"] == "question" and n["task_id"] == task_id]
    for item in found:
        author, message_id = c.execute(
            "SELECT bot_id, message_id FROM question_marks WHERE id=?", (item["mark_id"],)
        ).fetchone()
        assert author == by, author
        message = H.message(c, message_id)
        assert message["sender_id"] == by
        line, _, why = message["body"].partition("\n\n")
        assert line == item["line"]
        assert why and ask_shape.line_refusal(message["body"]) is None, message["body"]
        assert H.task_view(c, task_id, person), "the person reads the task"
    return found


def test_a_person_asked_on_a_bots_task_finds_it_in_needs_you_and_the_bot_holds_it(c):
    made = PA.ask(
        c,
        "bot:builder",
        ANA,
        "Grant the read key",
        "x",
        "Can you grant the read key?",
        "It is stuck.",
        bot="bot:builder",
    )
    assert made["owner_id"] == "bot:builder" and len(asked(c, ANA, made["id"], by="bot:builder")) == 1


def test_with_no_such_bot_the_task_is_the_persons_and_agent_ops_asks(c):
    made = PA.ask(
        c, H.TICO_ACTOR, ANA, "Check the lease", "x", "Can you check the lease?", "It ends soon.", bot="bot:ghost"
    )
    assert made["owner_id"] == ANA and len(asked(c, ANA, made["id"])) == 1


def test_a_review_of_agent_ops_refused_writes_asks_a_person(c):
    H._review_task(
        c, ANA, "Review Agent Ops's refused writes", "12 writes from Agent Ops were turned down today. More."
    )
    task_id = c.execute("SELECT id FROM tasks WHERE title=?", ("Review Agent Ops's refused writes",)).fetchone()[0]
    assert len(asked(c, ANA, task_id)) == 1


def test_an_org_rule_hold_asks_its_reviewer(c):
    from backend import org_rule_check

    task_id = org_rule_check._review_task(c, "hold-1", "bot:builder", "No Markdown", ANA)
    assert len(asked(c, ANA, task_id)) == 1


def test_a_finance_notice_asks_the_person(c):
    from backend.finance.store import tico

    task_id = tico.needs_you(c, ANA, "Add the missing receipt", "A card charge has no receipt.")
    assert task_id and len(asked(c, ANA, task_id)) == 1


def test_a_stopped_bot_asks_its_operator(c):
    from backend import bot_stops

    c.execute("UPDATE bot_config SET owner_id='ana' WHERE bot='builder'")
    task_id = bot_stops.raise_item(c, SimpleNamespace(owner_email=""), "builder", "Builder stopped", "It hit a limit.")
    assert task_id and len(asked(c, ANA, task_id)) == 1


def test_market_insights_ask_the_company_owner_on_the_curators_task(c):
    from backend import market

    made = market._file_owner_task(c, "bot:builder", ANA, "Decide these market insights.\n\n> one: a claim")
    assert made["owner_id"] == "bot:builder" and len(asked(c, ANA, made["id"], by="bot:builder")) == 1


def test_a_bots_task_for_a_person_through_the_api_is_its_own_with_a_question_to_them(gate):  # noqa: F811
    api, _model, token = gate
    r = api.post(
        "/api/v2/tasks", json={"owner": "ben", "title": "Pick the payroll vendor", "body": "x"}, headers=headers(token)
    )
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["owner_id"] == "bot:ops"
    items = get(api, "needs-you", "ben-test")["items"]
    [item] = [i for i in items if i.get("task_id") == task["id"]]
    with api.app.state.store.read() as c:
        author = c.execute("SELECT bot_id FROM question_marks WHERE id=?", (item["mark_id"],)).fetchone()[0]
    assert (item["kind"], item["from_id"], author) == ("question", "bot:ops", "bot:ops")  # the bot that filed it asks


def test_a_question_on_the_persons_own_task_ends_when_they_finish_it(c):
    made = PA.ask(c, H.TICO_ACTOR, ANA, "Check the lease", "x", "Can you check the lease?", "It ends soon.")
    assert asked(c, ANA, made["id"])
    from backend import task_engine as E

    X.say_done(c, E.task(c, made["id"]))
    E.advance(c, made["id"])
    assert H.task(c, made["id"])["status"] == "done" and not asked(c, ANA, made["id"])


def test_a_bot_that_cannot_run_has_agent_ops_ask_for_it_and_with_no_bot_nobody_asks(c):
    X.bot(c, "curator", computer=False)  # here, with nowhere to run
    made = PA.ask(
        c, "bot:curator", ANA, "Pick a vendor", "x", "Which vendor should we use?", "It is due.", bot="bot:curator"
    )
    assert made["owner_id"] == "bot:curator" and len(asked(c, ANA, made["id"])) == 1
    c.execute("UPDATE bots SET state='archived' WHERE id IN ('botops','builder')")
    alone = PA.ask(c, H.TICO_ACTOR, ANA, "Check the lease", "x", "Can you check the lease?", "It ends soon.")
    # Tico never writes the question: with no bot to ask, the task is only in her Tasks.
    assert alone["owner_id"] == ANA and not asked(c, ANA, alone["id"])
    assert (
        c.execute(
            "SELECT count(*) FROM requirements r JOIN visits v ON v.id=r.visit_id WHERE v.task_id=?", (alone["id"],)
        ).fetchone()[0]
        == 0
    )
    assert c.execute("SELECT count(*) FROM question_marks WHERE task_id=?", (alone["id"],)).fetchone()[0] == 0


def test_a_question_that_would_fail_the_ask_checks_is_asked_in_plain_words(c):
    made = PA.ask(c, H.TICO_ACTOR, ANA, "Fix the webhook", "x", "Can you fix backend/hooks/orders.py?", "It failed.")
    [item] = asked(c, ANA, made["id"])
    assert item["line"] == PA.PLAIN_ASK


def test_a_held_item_is_asked_by_agent_ops_never_its_sender(c):
    from backend import screening

    row = {"source": "message", "actor": "bot:builder", "bot": "builder"}
    made = screening._open_task(c, None, row, None, ANA, "Held: refund request", "x")
    assert made["owner_id"] == ANA and len(asked(c, ANA, made["id"])) == 1
