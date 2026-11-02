"""Gateway credit top-ups (spec §9): money moved from a bank account onto the Gateway credit prepaid asset that usage
draws down. A top-up moves assets only, so neither P&L moves. It is refused for a card or other non-bank money
account, for an amount that is not positive, for a book that is a provider's, and while 1100 is missing or inactive."""
from decimal import Decimal

from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance.store import ledger as SL
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, FakeMercury, act, books, connect, finance_template, spend, store)
from backend.tests.test_finance_payments import _checking

PLAIN = "human:eve"          # a web teammate with no grants or rights


def _balance(c, code) -> int:
    return int(c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id "
                         "WHERE a.code=?", (code,)).fetchone()[0])


def _pnl(c, basis, start="2026-10-01", end="2026-10-31") -> dict[str, int]:
    found = FR.pnl(c, OWNER, start, end, "year", basis, AT)
    return {str(r["code"]): int(r["total"]) for r in found["rows"] if r["total"]}


def _bank_code(c, money_id: str) -> str:
    return str(c.execute("SELECT a.code FROM fin_money_accounts m JOIN fin_accounts a ON a.id=m.account_id "
                         "WHERE m.id=?", (money_id,)).fetchone()[0])


def _entries(c) -> int:
    return int(c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0])


def _meter(as_of, **prices):
    rows = [{"model": model, "category": "image" if model.endswith("image") else "text", "charged": True, "calls": 1,
             "price_usd": Decimal(price)} for model, price in prices.items()]
    return {"month": "2026-10", "rows": rows, "as_of": as_of}


def test_a_top_up_posts_gateway_credit_against_the_bank_account(books):
    connect(books, FakeMercury())
    with books.transaction() as c:
        money = _checking(c)
        bank = _bank_code(c, money)
        before = _balance(c, bank)
        made = act(c, OWNER, "gateway_topup", {"money_account_id": money, "amount": 10_000, "date": "2026-10-20",
                                               "memo": "October top-up"})
        assert made.status == "committed", made
        assert _balance(c, "1100") == 10_000
        assert _balance(c, bank) == before - 10_000
        entry = str(made.result["entry_id"])
        assert [(str(r[0]), int(r[1])) for r in c.execute(
            "SELECT a.code, l.amount FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id WHERE l.entry_id=? "
            "ORDER BY l.amount DESC", (entry,))] == [("1100", 10_000), (bank, -10_000)]
        assert tuple(c.execute("SELECT entry_date, memo, source FROM fin_entries WHERE id=?", (entry,)).fetchone()) == \
            ("2026-10-20", "October top-up", "bank")
        # No date given: the day the action runs.
        made = act(c, OWNER, "gateway_topup", {"money_account_id": money, "amount": 2_500})
        assert made.status == "committed", made
        assert c.execute("SELECT entry_date FROM fin_entries WHERE id=?", (made.result["entry_id"],)).fetchone()[0] \
            == "2026-10-15"
        assert _balance(c, "1100") == 12_500


def test_a_top_up_changes_neither_the_cash_nor_the_accrual_pnl(books):
    connect(books, FakeMercury())
    with books.transaction() as c:
        spend(c, "6100", 5_000)                       # some expense this month, so the check is not on zeros
        cash, accrual = _pnl(c, "cash"), _pnl(c, "accrual")
        assert cash == accrual == {"6100": 5_000}
        made = act(c, OWNER, "gateway_topup", {"money_account_id": _checking(c), "amount": 10_000,
                                               "date": "2026-10-20"})
        assert made.status == "committed", made
        assert (_pnl(c, "cash"), _pnl(c, "accrual")) == (cash, accrual)


def test_usage_draws_the_gateway_credit_down_after_a_top_up(books):
    connect(books, FakeMercury())
    with books.transaction() as c:
        made = act(c, OWNER, "gateway_topup", {"money_account_id": _checking(c), "amount": 10_000,
                                               "date": "2026-10-15"})
        assert made.status == "committed", made
        assert FA.usage_snapshot(c, _meter("2026-10-15T16:00:00Z", **{"tico-medium": "0.50"}),
                                 fetched_at="2026-10-15T16:01:00.000000Z")
        assert [(p["line"], p["amount"]) for p in FA.post_usage(c, "2026-10-15T16:05:00.000000Z")] == [("ai", 50)]
        assert (_balance(c, "1100"), _balance(c, "5100")) == (9_950, 50)


def test_a_top_up_refuses_a_card_an_unknown_account_a_zero_or_negative_amount_and_a_bad_field(books):
    connect(books, FakeMercury())
    with books.transaction() as c:
        bank = _checking(c)
        card = str(c.execute("SELECT id FROM fin_money_accounts WHERE kind='card_program'").fetchone()[0])
        made = act(c, OWNER, "gateway_topup", {"money_account_id": bank, "amount": 10_000})
        assert made.status == "committed", made      # the action exists, so the refusals below are the only reason
        entries, credit = _entries(c), _balance(c, "1100")
        for payload in ({"money_account_id": card, "amount": 10_000},
                        {"money_account_id": "no-such-account", "amount": 10_000},
                        {"money_account_id": bank, "amount": 0},
                        {"money_account_id": bank, "amount": -500},
                        {"money_account_id": bank},
                        {"money_account_id": bank, "amount": 100.0},
                        {"money_account_id": bank, "amount": 100, "memo": "x" * 141},
                        {"money_account_id": bank, "amount": 100, "expense": True}):
            got = act(c, OWNER, "gateway_topup", payload)
            assert (got.status, got.code) == ("failed", "invalid"), (payload, got)
        assert (_entries(c), _balance(c, "1100")) == (entries, credit)
        got = act(c, PLAIN, "gateway_topup", {"money_account_id": bank, "amount": 100})
        assert (got.status, got.code) == ("denied", "denied"), got      # a teammate without the books right


def test_a_top_up_is_refused_while_the_books_are_a_providers(books):
    cid = connect(books, FakeMercury())["connection_id"]
    with books.transaction() as c:
        money = _checking(c)
        made = act(c, OWNER, "gateway_topup", {"money_account_id": money, "amount": 10_000})
        assert made.status == "committed", made
        SL.set_book_mode(c, SL.book(c).id, "mirror", str(cid))  # type: ignore[union-attr]
        got = act(c, OWNER, "gateway_topup", {"money_account_id": money, "amount": 10_000})
        assert (got.status, got.code) == ("denied", "not_available"), got
        assert _balance(c, "1100") == 10_000


def test_a_top_up_is_refused_when_the_gateway_credit_account_is_inactive(books):
    connect(books, FakeMercury())
    with books.transaction() as c:
        money = _checking(c)
        assert act(c, OWNER, "gateway_topup", {"money_account_id": money, "amount": 10_000}).status == "committed"
        c.execute("UPDATE fin_accounts SET active=0 WHERE code='1100'")
        got = act(c, OWNER, "gateway_topup", {"money_account_id": money, "amount": 10_000})
        assert (got.status, got.code) == ("failed", "not_found"), got
        assert _balance(c, "1100") == 10_000


def test_a_top_up_is_refused_when_the_books_have_no_gateway_credit_account_and_none_is_made(books):
    connect(books, FakeMercury())
    with books.transaction() as c:
        c.execute("DELETE FROM fin_accounts WHERE code='1100'")
        got = act(c, OWNER, "gateway_topup", {"money_account_id": _checking(c), "amount": 10_000})
        assert (got.status, got.code) == ("failed", "not_found"), got
        assert c.execute("SELECT COUNT(*) FROM fin_accounts WHERE code='1100'").fetchone()[0] == 0
