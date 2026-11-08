"""The Directory's structured rules on a payment (backend/payment_rules.py): a bot paying a payee a Block rule reaches
is held, and no money moves; a check that cannot complete holds it too (fail closed); an ordinary payee, and a person's
own payment, go on as before. The payee is found from the address of the vendor its recipient pays (the Directory knows
Acme Supplies by its domain, supplies.example)."""

import json

import pytest

from backend import payment_rules as PR
from backend.entities import store as ES
from backend.tests.test_finance_fixtures import (  # noqa: F401
    ACME_ADDRESS,
    AT,
    OWNER,
    FakeMercury,
    account,
    act,
    books,
    connect,
    finance_template,
    store,
    sync_once,
)

BOT = "bot:seller"
RULE = "No payments to Acme Supplies until the contract is signed."


def _recipient(store, fake, name, email, number):
    with store.transaction() as c:
        made = act(
            c,
            OWNER,
            "recipient_add",
            {
                "name": name,
                "method": "ach",
                "account_number": number,
                "routing_number": "000000000",
                "address": ACME_ADDRESS,
            },
        )
        assert made.status == "committed", made
    rid = str(made.result["recipient_id"])
    with store.transaction() as c:  # the vendor the recipient pays for, with its address
        vendor = act(
            c, OWNER, "contact_create", {"name": name, "kind": "vendor", "emails": [email], "recipient_id": rid}
        )
        assert vendor.status == "committed", vendor
    sync_once(store, fake, AT)
    return rid


@pytest.fixture
def paying(books):
    """Mercury connected, the bot and Ben may pay, two payees, and a Block rule on the seller bot toward Acme Supplies."""
    fake = FakeMercury()
    connect(books, fake)
    blocked = _recipient(books, fake, "Acme Supplies", "ap@supplies.example", "000099998888")
    plain = _recipient(books, fake, "Jane Rivera", "jane@example.test", "000011112222")
    with books.transaction() as c:
        _grants(c)
        c.execute(
            "INSERT INTO entities (id, type, name, name_key, primary_domain, created_by, created_at, updated_at) "
            "VALUES ('organization/acme-supplies', 'organization', 'Acme Supplies', 'acme supplies', "
            "'supplies.example', 'seed', ?, ?)",
            (AT, AT),
        )
        c.execute(
            "INSERT INTO org_rules (id, kind, text, who, with_scope, if_broken, status, suggested_by, created_at, "
            "updated_at, toward_scope) VALUES ('r-acme-pay', 'internal', ?, ?, '{}', 'block', 'active', 'human:ana', "
            "?, ?, ?)",
            (RULE, json.dumps({"bots": ["seller"]}), AT, AT, json.dumps(["entity:organization/acme-supplies"])),
        )
    return books, blocked, plain


def _grants(c):
    from backend.finance import actions as FA

    FA.grant_defaults(c, BOT, at=AT)
    act(c, OWNER, "view_grant", {"subject": BOT, "what": "everything"})
    act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.pay"})


def _pay(c, who, rid):
    tags = {"team": "eng"} if who.startswith("human:") else {}
    return act(
        c,
        who,
        "payment_create",
        {
            "recipient_id": rid,
            "amount": 20_000,
            "account_id": account(c, "6100"),
            "tags": tags,
            "money_account_id": str(
                c.execute("SELECT id FROM fin_money_accounts WHERE provider_account_id='acct-chk'").fetchone()[0]
            ),
        },
    )


def _money(c):
    """What a payment would have written: payment rows and their Finance actions."""
    return (
        c.execute("SELECT count(*) FROM fin_payments").fetchone()[0],
        c.execute("SELECT count(*) FROM fin_actions WHERE kind='payment_create'").fetchone()[0],
    )


def _holds(c):
    return [dict(r) for r in c.execute("SELECT * FROM org_rule_holds WHERE operation='payment'")]


def test_a_bot_paying_a_blocked_payee_is_held_and_no_money_moves(paying):
    books, blocked, _plain = paying
    with books.transaction() as c:
        held = _pay(c, BOT, blocked)
        again = _pay(c, BOT, blocked)
        assert held.status == "held" and held.code == "held", held
        assert held.result["hold"]["rules"] == [{"id": "r-acme-pay", "text": RULE}]
        assert _money(c) == (0, 0)
        holds = _holds(c)
        assert len(holds) == 1 and holds[0]["check_kind"] == "structured" and holds[0]["author"] == BOT
        assert json.loads(holds[0]["rule_ids"]) == ["r-acme-pay"]
        assert again.status == "held" and again.result["hold"]["id"] == holds[0]["id"]  # one payment, one hold


@pytest.mark.parametrize("broken", ["walk", "payee"])
def test_a_check_that_raises_holds_the_payment(paying, monkeypatch, broken):
    books, _blocked, plain = paying

    def boom(*_a, **_k):
        raise RuntimeError("the check failed")

    monkeypatch.setattr(*((ES, "walk") if broken == "walk" else (PR, "payee")), boom)
    with books.transaction() as c:
        held = _pay(c, BOT, plain)
        assert held.status == "held", held
        assert "RuntimeError" in held.reason
        assert _money(c) == (0, 0)
        [hold] = _holds(c)
        assert json.loads(hold["verdict"])["error"] == "RuntimeError"


def test_an_ordinary_payee_and_a_persons_own_payment_go_on(paying):
    books, blocked, plain = paying
    with books.transaction() as c:
        assert _pay(c, BOT, plain).status in ("committed", "needs_approval")
        assert _pay(c, "human:ben", blocked).status in ("committed", "needs_approval")  # as a person's own mail
        assert _holds(c) == []
        assert _money(c)[1] == 2
