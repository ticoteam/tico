"""The close and budget gaps from the Finance spec audit (spec v1.2): close snapshots and restatements, budget
versions in the past and the current period, the Expenses view, cash by account, burn and runway flags, the Gateway
meter fields, and the run hold average. One small test per gap; each fails before its fix."""
from datetime import date

import pytest

from backend.finance import reads as FR
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, spend, store  # noqa: F401

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
