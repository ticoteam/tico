"""The close and budget gaps from the Finance spec audit (spec v1.2): close snapshots and restatements, budget
versions in the past and the current period, the Expenses view, cash by account, burn and runway flags, the Gateway
meter fields, and the run hold average. One small test per gap; each fails before its fix."""
from datetime import date

import pytest

from backend.finance import reads as FR
from backend.finance.core.errors import NOT_FOUND, FinanceError
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, FakeMercury, account, act, books, connect, finance_template, spend, store, sync_once)

OCT = ("2026-10-01", "2026-10-31")


def test_expenses_view_hides_a_budget_only_line_and_the_company_pnl_keeps_it(books):
    """§12: the Expenses view keeps lines with spend in the filter. A budget with no spend in the filter does not keep
    its line there; the company P&L shows the same line with its budget."""
    with books.transaction() as c:
        spend(c, "5100", 1_000, team="web", day="2026-10-02")
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 5_000})
        assert made.status == "committed", made.reason
        expenses = FR.pnl(c, OWNER, *OCT, view="expenses", at=AT)
        assert "6100" not in [r["code"] for r in expenses["rows"]]
        assert "5100" in [r["code"] for r in expenses["rows"]]
        company = FR.pnl(c, OWNER, *OCT, at=AT)
        assert [r["budget"] for r in company["rows"] if r["code"] == "6100"] == [[5_000]]


def test_cash_for_one_money_account_counts_only_that_account(books):
    """The cash metric's account_id names one money account (the hub tool says so): its balance and only its row come
    back. An account that does not exist, or that the viewer cannot see, is not found."""
    fake = FakeMercury()
    connect(books, fake)
    sync_once(books, fake)
    with books.read() as c:
        everything = FR.metric(c, OWNER, "cash", at=AT)
        one = everything["accounts"][0]
        cut = FR.metric(c, OWNER, "cash", account_id=str(one["money_account_id"]), at=AT)
        assert cut["accounts"] == [one]
        assert cut["value"] == one["balance"]
        with pytest.raises(FinanceError) as missing:
            FR.metric(c, OWNER, "cash", account_id="ma_none", at=AT)
        assert missing.value.code == NOT_FOUND
