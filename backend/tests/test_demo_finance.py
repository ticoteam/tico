"""Demo finance: Tico's own books for October 2026 balance and show up in the P&L, the budgets, the invoices and bills,
and the rules. The bookkeeper's rule proposal waits for Ana."""

import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import demo
from backend.app import create_app
from backend.demo_sections.finance import CONTENT as C

NOW = datetime(2026, 11, 2, 18, 30, tzinfo=timezone.utc)      # the books cover October; the demo is a few days in


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return demo.build(tmp_path_factory.mktemp("demo-finance"), now=NOW)


def signed_in(settings):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    client.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    return client


def october_by_account():
    """Each P&L line's October total, from the figures the seed posts."""
    payouts = C["marketplace"]["payouts"]
    totals = {
        "4100": sum(week["amount"] for week in C["store_weeks"]),
        "4200": sum(payout["gross"] for payout in payouts),
        "4500": sum(amount for _, amount in C["subscriptions"]["lines"]),
        "5100": sum(amount for _, amount in C["usage"]["by_teammate"]),
        "6200": sum(payout["fees"] for payout in payouts),
    }
    for item in C["invoices"] + C["bills"]:
        totals[item["account"]] = totals.get(item["account"], 0) + item["amount"]
    return totals


def test_books_cover_october_and_balance(built):
    with signed_in(built) as api:
        pnl = api.get("/api/v2/finance/pnl",
                      params={"start": "2026-10-01", "end": "2026-10-31", "basis": "accrual"}).json()
        sheet = api.get("/api/v2/finance/balance-sheet").json()
    totals = {row["code"]: row["total"] for row in pnl["rows"]}
    for code, amount in october_by_account().items():
        assert abs(totals[code]) == amount, code
    assert sheet["totals"]["asset"] == sheet["totals"]["liability"] + sheet["totals"]["equity"]


def test_budgets_show_spend_and_only_paid_advertising_is_near_its_limit(built):
    with signed_in(built) as api:
        budgets = {b["name"]: b for b in api.get("/api/v2/finance/budgets").json()["budgets"]}
    for bud in C["budgets"]:
        state = budgets[bud["name"]]
        assert state["owner"] == bud["owner"]
        assert state["state"]["amount"] == bud["amount"]
        assert state["state"]["period_start"] == "2026-10-01"
    near = [name for name, b in budgets.items() if b["state"].get("crossed_warn")]
    assert near == ["Paid advertising, Q4"]


def test_invoices_one_paid_and_one_overdue_that_the_chase_task_covers(built):
    with signed_in(built) as api:
        invoices = {i["number"]: i for i in api.get("/api/v2/finance/invoices").json()["invoices"]}
        tasks = api.get("/api/v2/tasks", params={"owner": "bot:ar-followup"}).json()["tasks"]
    assert set(invoices) == {inv["number"] for inv in C["invoices"]}
    assert [n for n, i in invoices.items() if i["status"] == "paid"] == ["ACME-1001"]
    overdue = [n for n, i in invoices.items() if i["remaining"] > 0 and i["days_late"] > 0]
    assert overdue == ["ACME-1003"]
    assert C["chase"]["title"] in [t["title"] for t in tasks]


def test_bills_are_drafted_by_accounts_payable_and_approved_by_ana(built):
    with signed_in(built) as api:
        bills = {b["number"]: b for b in api.get("/api/v2/finance/bills").json()["bills"]}
    assert set(bills) == {bill["number"] for bill in C["bills"]}
    manufacturer = bills["LA-20417"]
    assert (manufacturer["created_by"], manufacturer["approved_by"], manufacturer["status"]) == (
        "bot:accounts-payable", "human:ana", "paid")
    freight = bills["KF-88213"]
    assert (freight["created_by"], freight["approved_by"], freight["status"]) == (
        "bot:accounts-payable", "human:ana", "open")


def test_rules_are_in_force_and_the_bookkeepers_proposal_waits_for_ana(built):
    with signed_in(built) as api:
        rules = {r["name"]: r for r in api.get("/api/v2/finance/rules").json()["rules"]}
    assert rules["Spend over $500 needs Ana"]["active"]
    assert rules["Marketplace fees go to 6200"]["active"]
    assert "Freight bills go to 5400" not in rules          # not in force until Ana approves it
    with sqlite3.connect(built.db_path) as db:
        waiting = db.execute("SELECT kind, actor, approval_id FROM fin_actions WHERE status = 'needs_approval'").fetchall()
    assert [(kind, actor) for kind, actor, _ in waiting] == [("rule_create", "bot:bookkeeper")]
    with signed_in(built) as api:
        assert api.get(f"/api/v2/approvals/{waiting[0][2]}").status_code == 200
