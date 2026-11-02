"""Demo finance: Tico's own books balance, show the previous full month and this month to date by default, and carry the
budgets, invoices, bills and rules the demo tells. The bookkeeper's rule proposal waits in Ana's approval queue.

The demo is built at two moments, early in a month and mid-month in a later quarter. Finance reads its default month
from its clock, so the test pins that clock to the build's moment, as the demo is shown the day it is built."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import demo
from backend.app import create_app
from backend.demo_sections.finance import CONTENT as C
from backend.finance import context as finance_context

MOMENTS = {
    "early-month": datetime(2026, 11, 2, 18, 30, tzinfo=timezone.utc),
    "mid-month": datetime(2027, 1, 15, 12, 0, tzinfo=timezone.utc),
}


@pytest.fixture(scope="module", params=list(MOMENTS), ids=list(MOMENTS))
def book(request, tmp_path_factory):
    now = MOMENTS[request.param]
    settings = demo.build(tmp_path_factory.mktemp("demo-finance"), now=now)
    patch = pytest.MonkeyPatch()
    patch.setattr(finance_context, "now", lambda: now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    yield settings, now
    patch.undo()


def signed_in(settings):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    client.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    return client


def months(now):
    """The first day of the previous full month and of this month, from the build's moment."""
    this = now.date().replace(day=1)
    return (this - timedelta(days=1)).replace(day=1), this


def previous_month_by_account():
    """Each P&L line's total for the previous full month (accrual), from the figures the seed posts."""
    payouts = [p for p in C["marketplace"]["payouts"] if p["date"][0] == "prev"]
    totals = {
        "4100": sum(week["amount"] for week in C["store_weeks"]),
        "4200": sum(payout["gross"] for payout in payouts),
        "4500": sum(amount for _, amount in C["subscriptions"]["lines"]),
        "5100": sum(amount for _, amount in C["usage"]["by_teammate"]),
        "6200": sum(payout["fees"] for payout in payouts),
    }
    for item in C["invoices"] + C["bills"]:
        if item["date"][0] == "prev":
            totals[item["account"]] = totals.get(item["account"], 0) + item["amount"]
    return totals


def this_month_by_account():
    """The lines this month has so far on the default (cash) P&L: the store takings and the marketplace payout."""
    payout = next(p for p in C["marketplace"]["payouts"] if p["date"][0] == "this")
    return {"4100": C["store_this_month"]["amount"], "4200": payout["gross"], "6200": payout["fees"]}


def test_default_month_has_this_months_numbers_and_last_month_is_complete(book):
    settings, now = book
    prev_start, this_start = months(now)
    with signed_in(settings) as api:
        default = api.get("/api/v2/finance/pnl").json()
        last_month = api.get("/api/v2/finance/pnl", params={
            "start": prev_start.isoformat(), "end": (this_start - timedelta(days=1)).isoformat(),
            "basis": "accrual"}).json()
        sheet = api.get("/api/v2/finance/balance-sheet").json()
    current = {row["code"]: abs(row["total"]) for row in default["rows"]}
    for code, amount in this_month_by_account().items():
        assert current.get(code) == amount, code
    totals = {row["code"]: abs(row["total"]) for row in last_month["rows"]}
    for code, amount in previous_month_by_account().items():
        assert totals[code] == amount, code
    assert sheet["totals"]["asset"] == sheet["totals"]["liability"] + sheet["totals"]["equity"]


def test_budgets_are_monthly_and_only_paid_advertising_is_near_its_limit(book):
    settings, now = book
    _, this_start = months(now)
    with signed_in(settings) as api:
        budgets = {b["name"]: b for b in api.get("/api/v2/finance/budgets").json()["budgets"]}
    for bud in C["budgets"]:
        state = budgets[bud["name"]]
        assert state["owner"] == bud["owner"]
        assert state["state"]["amount"] == bud["amount"]
        assert state["state"]["period_start"] == this_start.isoformat()
    near = [name for name, b in budgets.items() if b["state"].get("crossed_warn")]
    assert near == ["Paid advertising"]


def test_invoices_one_paid_and_one_overdue_that_the_chase_task_covers(book):
    settings, _ = book
    with signed_in(settings) as api:
        invoices = {i["number"]: i for i in api.get("/api/v2/finance/invoices").json()["invoices"]}
        tasks = api.get("/api/v2/tasks", params={"owner": "bot:ar-followup"}).json()["tasks"]
    assert set(invoices) == {inv["number"] for inv in C["invoices"]}
    assert [n for n, i in invoices.items() if i["status"] == "paid"] == ["ACME-1001"]
    overdue = [n for n, i in invoices.items() if i["remaining"] > 0 and i["days_late"] > 0]
    assert overdue == ["ACME-1003"]
    assert C["chase"]["title"] in [t["title"] for t in tasks]


def test_bills_are_drafted_by_accounts_payable_and_approved_by_ana(book):
    settings, _ = book
    with signed_in(settings) as api:
        bills = {b["number"]: b for b in api.get("/api/v2/finance/bills").json()["bills"]}
    assert set(bills) == {bill["number"] for bill in C["bills"]}
    manufacturer = bills["LA-20417"]
    assert (manufacturer["created_by"], manufacturer["approved_by"], manufacturer["status"]) == (
        "bot:accounts-payable", "human:ana", "paid")
    freight = bills["KF-88213"]
    assert (freight["created_by"], freight["approved_by"], freight["status"]) == (
        "bot:accounts-payable", "human:ana", "open")


def test_rules_are_in_force_and_the_bookkeepers_proposal_waits_for_ana(book):
    settings, _ = book
    with signed_in(settings) as api:
        rules = {r["name"]: r for r in api.get("/api/v2/finance/rules").json()["rules"]}
    assert rules["Spend over $500 needs Ana"]["active"]
    assert rules["Marketplace fees go to 6200"]["active"]
    assert "Freight bills go to 5400" not in rules          # not in force until Ana approves it
    with sqlite3.connect(settings.db_path) as db:
        waiting = db.execute("SELECT kind, actor, approval_id FROM fin_actions WHERE status = 'needs_approval'").fetchall()
    assert [(kind, actor) for kind, actor, _ in waiting] == [("rule_create", "bot:bookkeeper")]
    approval_id = waiting[0][2]
    with signed_in(settings) as api:
        assert api.get(f"/api/v2/approvals/{approval_id}").status_code == 200
        queue = api.get("/api/v2/needs-you").json()["items"]
    assert approval_id in [item["id"] for item in queue if item["kind"] == "approval"]
