"""Helpers that other tests share, moved out of test_finance_tab.py so they import no test file."""

from backend.finance import actions as FA
from backend.finance.store import banking as SK
from backend.tests.helpers.finance_fixtures import AT, OWNER, account, act


def _bank(c, at=AT):
    """Acme's bank account and card program, two cards and a few feed rows (eve's card spend posted, gus's posted, a
    bank fee new)."""
    book = str(c.execute("SELECT id FROM fin_books").fetchone()[0])
    cards_gl = str(
        act(c, OWNER, "account_create", {"code": "2100", "name": "Credit cards", "type": "liability"}).result[
            "account_id"
        ]
    )
    base = {"book_id": book, "provider": "mercury", "created_at": at, "created_by": "system:finance"}
    bank = SK.insert(
        c,
        "fin_money_accounts",
        {
            **base,
            "kind": "bank",
            "subtype": "checking",
            "name": "Acme checking",
            "account_id": account(c, "1000"),
            "last4": "0001",
            "balance": 90_000,
            "balance_currency": "USD",
            "balance_as_of": at,
        },
    )
    program = SK.insert(
        c,
        "fin_money_accounts",
        {**base, "kind": "card_program", "name": "Acme IO", "holds_cash": 0, "account_id": cards_gl, "last4": "0002"},
    )
    card = {
        "provider": "mercury",
        "money_account_id": program,
        "kind": "person",
        "currency": "USD",
        "created_at": at,
        "created_by": "system:finance",
    }
    eve = SK.insert(c, "fin_cards", {**card, "holder": "human:eve", "last4": "1111", "limit_amount": 50_000})
    gus = SK.insert(c, "fin_cards", {**card, "holder": "human:gus", "last4": "2222"})
    feed = {"currency": "USD", "created_at": at, "created_by": "system:finance", "status": "posted"}
    for pid, holder, cid, amount, day in (
        ("t1", "human:eve", eve, 1_200, "2026-10-08"),
        ("t2", "human:gus", gus, 3_400, "2026-10-09"),
    ):
        team = "web" if holder == "human:eve" else "sales"
        entry = FA.post_entry(
            c,
            {
                "date": day,
                "source": "card",
                "source_id": pid,
                "lines": [
                    {"account_id": account(c, "6900"), "amount": amount, "tags": {"teammate": holder, "team": team}},
                    {"account_id": cards_gl, "amount": -amount},
                ],
            },
            at=at,
        )
        SK.insert(
            c,
            "fin_feed_txns",
            {
                **feed,
                "money_account_id": program,
                "provider_id": pid,
                "txn_date": day,
                "amount": -amount,
                "counterparty": "Acme Supplies",
                "card_id": cid,
                "entry_id": entry["entry_id"],
                "raw": '{"secret": "raw"}',
            },
        )
    SK.insert(
        c,
        "fin_feed_txns",
        {
            **feed,
            "status": "new",
            "money_account_id": bank,
            "provider_id": "t3",
            "txn_date": "2026-10-11",
            "amount": -500,
            "counterparty": "Bank fee",
        },
    )
    return {"bank": bank, "program": program, "eve": eve, "gus": gus, "cards_gl": cards_gl}
