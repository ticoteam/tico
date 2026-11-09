"""Structured holds (backend/org_rule_check.py with backend/entities/rules.py): a write the Directory's structured rules hold
is held on the same path as a judged hold, with check_kind `structured`. Its Send anyway is a waiver by whoever may
change the rule (its editor or an Admin), recorded once; a check that cannot complete holds the write (fail closed).

Acme is the fictional company (backend/tests/test_org_rules.py's org): Ana owns it and wrote the structured rule, Ops is
a bot, Cara is on the support team and may not change Ops's rules. The organization Acme Lights carries the domain
acme.example, so a chat to Ana (an acme.example address) reaches it through the domain."""

import json

import pytest

from backend.entities import store as ES
from backend.store import H
from backend.tests.helpers.api import get, post  # noqa: F401
from backend.tests.helpers.org_rule_check import COLOR, Judge, color_rule, events, hold_row, ruled, say, sql  # noqa: F401
from backend.tests.helpers.org_rules import api, edit, org, rule  # noqa: F401

ACME = "Nothing goes to Acme Lights."


def _acme(api):
    """The organization the rule names: Acme Lights, whose primary domain is the one Ana's address is on."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO entities (id, type, name, name_key, primary_domain, created_by, created_at, updated_at) "
            "VALUES ('organization/acme-lights', 'organization', 'Acme Lights', 'acme lights', 'acme.example', "
            "'seed', ?, ?)",
            (H.now(), H.now()),
        )


@pytest.fixture
def blocked(ruled):
    api, judge = ruled
    _acme(api)
    made = rule(
        api,
        {
            "kind": "internal",
            "text": ACME,
            "who": {"bots": ["ops"]},
            "toward_scope": ["entity:organization/acme-lights"],
            "if_broken": "block",
        },
    )
    return api, judge, made["id"]


def _messages(api, text):
    return sql(api, "SELECT id FROM messages WHERE body LIKE ?", (f"%{text}%",))


def test_chat_to_a_person_in_a_blocked_org_is_held_as_a_structured_hold(blocked):
    api, judge, rule_id = blocked
    held = say(api, "Quote for the Acme Lights job")["held"]
    row = hold_row(api, held["id"])
    assert row["check_kind"] == "structured"
    assert json.loads(row["rule_ids"]) == [rule_id]
    assert held["rules"][0]["text"] == ACME
    assert judge.calls == []  # no judged rule applies: nothing is asked of the engine
    assert _messages(api, "Acme Lights job") == []


def test_authorized_send_anyway_sends_once_and_records_the_waiver(blocked):
    api, _judge, _rule_id = blocked
    held = say(api, "Quote for the Acme Lights job")["held"]
    view = get(api, f"rule-holds/{held['id']}")
    assert view["can_send"]
    sent = post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]})
    assert sent["hold"]["state"] == "sent"
    post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]}, expected=409)
    assert len(_messages(api, "Acme Lights job")) == 1
    row = hold_row(api, held["id"])
    assert row["decided_by"] == "human:ana"
    waived = events(api, "org_rule_hold.waived")
    assert [e["actor"] for e in waived] == ["human:ana"]


def test_a_person_who_may_not_change_the_rule_cannot_waive_it(blocked):
    api, _judge, _rule_id = blocked
    held = say(api, "Quote for the Acme Lights job")["held"]
    get(api, f"rule-holds/{held['id']}", token="cara-test", expected=404)
    nonce = hold_row(api, held["id"])["nonce"]
    post(api, f"rule-holds/{held['id']}/send", {"nonce": nonce}, token="cara-test", expected=404)
    assert hold_row(api, held["id"])["state"] == "held"
    assert _messages(api, "Acme Lights job") == []


def test_a_check_that_errors_holds_the_write_with_the_error_class(blocked, monkeypatch):
    api, _judge, _rule_id = blocked

    def broken(*args, **kwargs):
        raise RuntimeError("the walk failed")

    monkeypatch.setattr(ES, "walk", broken)
    held = say(api, "Quote for the Acme Lights job")["held"]
    row = hold_row(api, held["id"])
    assert row["check_kind"] == "structured"
    assert json.loads(row["verdict"])["error"] == "RuntimeError"
    assert "RuntimeError" in held["reason"]
    assert _messages(api, "Acme Lights job") == []


def test_a_judged_hold_is_unchanged(ruled):
    api, judge = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    held = say(api, "Our favorite color is gold")["held"]
    row = hold_row(api, held["id"])
    assert row["check_kind"] == "judged"
    assert "check" not in json.loads(row["verdict"])
    assert len(judge.calls) == 1
    assert held["rules"][0]["text"] == COLOR


def test_a_rule_takes_toward_scope_and_a_condition_through_the_api(api):
    org(api)
    made = rule(
        api,
        {
            "kind": "internal",
            "text": ACME,
            "who": {"bots": ["ops"]},
            "toward_scope": ["entity:organization/acme-lights", "type:person"],
            "applies_when": "self customer_of organization/acme-lights",
            "if_broken": "escalate",
            "reviewer": "ben",
        },
    )
    with api.app.state.store.read() as c:
        stored = dict(
            c.execute("SELECT toward_scope, applies_when FROM org_rules WHERE id=?", (made["id"],)).fetchone()
        )
    assert json.loads(stored["toward_scope"]) == ["entity:organization/acme-lights", "type:person"]
    assert stored["applies_when"] == "self customer_of organization/acme-lights"
    rule(
        api, {"kind": "internal", "text": ACME, "who": {"bots": ["ops"]}, "toward_scope": ["sideways:x"]}, expected=422
    )
    rule(
        api,
        {"kind": "internal", "text": ACME, "who": {"bots": ["ops"]}, "applies_when": "self customer_of x"},
        expected=422,
    )


def test_the_third_structured_block_in_a_day_escalates_with_one_review_task(blocked):
    api, _judge, rule_id = blocked
    outcomes = [say(api, f"Quote {n} for the Acme Lights job")["held"]["outcome"] for n in range(4)]
    assert outcomes == ["blocked", "blocked", "escalated", "escalated"]
    holds = sql(api, "SELECT rule_ids, verdict, task_id FROM org_rule_holds ORDER BY created_at")
    assert [json.loads(h["verdict"])["outcome"] for h in holds] == ["block", "block", "escalate", "escalate"]
    assert all(json.loads(h["rule_ids"]) == [rule_id] for h in holds)
    assert [e["actor"] for e in events(api, "rules.escalated")] == ["bot:ops"] * 2
    assert len(events(api, "rules.blocked")) == 2
    assert len(sql(api, "SELECT id FROM tasks WHERE title = 'Rule review'")) == 1


# ---- letters: a do-not-contact by letter holds a bot's letter (backend/letters.py) --------------------------------
from backend.tests.helpers.letters import RECIPIENT, grant, hq, quote, send  # noqa: F401

JANE_DNC = "Do not contact Jane Doe by letter."


@pytest.fixture
def jane_letter(ruled, hq):
    api, _judge = ruled
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO entities (id, type, name, name_key, created_by, created_at, updated_at) "
            "VALUES ('person/jane-doe', 'person', 'Jane Doe', 'jane doe', 'seed', ?, ?)",
            (H.now(), H.now()),
        )
    rule(
        api,
        {
            "kind": "internal",
            "text": JANE_DNC,
            "who": {"bots": ["ops"]},
            "toward_scope": ["entity:person/jane-doe", "channel:letter"],
            "if_broken": "block",
        },
    )
    grant(api, "ops")
    return api, hq, quote(api, token="ops-test", recipient={**RECIPIENT, "name": "Jane Doe"})


def test_a_letter_to_a_person_with_a_letter_dnc_is_held_and_nothing_is_mailed(jane_letter):
    api, hq, row = jane_letter
    sent = send(api, row, token="ops-test")
    hold = hold_row(api, sent["held"]["id"])
    assert hold["operation"] == "letter" and hold["check_kind"] == "structured"
    assert sent["held"]["rules"][0]["text"] == JANE_DNC
    assert sent["status"] == "quoted" and not sent["approval_id"] and hq.provider_submits == 0
    # The same rule on another channel does not reach a letter: only the letter channel is named.
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE org_rules SET toward_scope=? WHERE text=?", ('["entity:person/jane-doe","channel:sms"]', JANE_DNC)
        )
    assert "held" not in send(api, row, token="ops-test")


def test_a_letter_check_that_errors_holds_the_letter(jane_letter, monkeypatch):
    api, hq, row = jane_letter

    def broken(*args, **kwargs):
        raise RuntimeError("the walk failed")

    monkeypatch.setattr(ES, "walk", broken)
    sent = send(api, row, token="ops-test")
    assert json.loads(hold_row(api, sent["held"]["id"])["verdict"])["error"] == "RuntimeError"
    assert sent["status"] == "quoted" and hq.provider_submits == 0
