"""Demo finance: Tico's own books balance, show the previous full month and this month to date by default, and carry the
budgets, invoices, bills and rules the demo tells. The bookkeeper's books view is granted and its first rule proposal
approved before the build, so nothing about them is waiting in Ana's queue.

The demo is built at two moments, early in a month and mid-month in a later quarter. Finance reads its default month
from its clock, so the test pins that clock to the build's moment, as the demo is shown the day it is built."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
import time_machine
from fastapi.testclient import TestClient

from backend import demo
from backend.app import create_app
from backend.bookkeeper import VIEW_TITLE
from backend.demo_sections.finance import BOOKKEEPER, CONTENT as C

MOMENTS = {
    "early-month": datetime(2026, 11, 2, 18, 30, tzinfo=timezone.utc),
    "mid-month": datetime(2027, 1, 15, 12, 0, tzinfo=timezone.utc),
}


@pytest.fixture(scope="module", params=list(MOMENTS), ids=list(MOMENTS))
def book(request, tmp_path_factory):
    now = MOMENTS[request.param]
    with time_machine.travel(now, tick=False):
        settings = demo.build(tmp_path_factory.mktemp("demo-finance"), now=now)
        yield settings, now


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


def test_rules_are_in_force_and_the_bookkeepers_proposal_was_approved_two_days_ago(book):
    settings, now = book
    with signed_in(settings) as api:
        rules = {r["name"]: r for r in api.get("/api/v2/finance/rules").json()["rules"]}
        queue = api.get("/api/v2/needs-you").json()["items"]
    for name in ("Spend over $500 needs Ana", "Marketplace fees go to 6200", C["proposal"]["name"]):
        assert rules[name]["active"], name
    with sqlite3.connect(settings.db_path) as db:
        [(status, approval_id)] = db.execute(
            "SELECT status, approval_id FROM fin_actions WHERE kind = 'rule_create' AND actor = ?", (BOOKKEEPER,)).fetchall()
        waiting = db.execute("SELECT COUNT(*) FROM fin_actions WHERE status = 'needs_approval'").fetchone()[0]
        decision, decided_by, decided_at = db.execute(
            "SELECT decision, decided_by_id, decided_at FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    assert status == "committed"
    assert waiting == 0
    assert (decision, decided_by) == ("approved", "human:ana")
    assert decided_at[:10] == (now - timedelta(days=2)).date().isoformat()
    assert approval_id not in [item["id"] for item in queue]


def test_the_bookkeepers_view_was_granted_six_days_ago_and_a_start_does_not_ask_again(book):
    settings, now = book
    with signed_in(settings) as api:
        access = api.get("/api/v2/finance/access").json()
    bot = next(t for t in access["teammates"] if t["teammate"] == BOOKKEEPER)
    assert [(g["what"], g["status"]) for g in bot["grants"]] == [("everything", "active")]
    with sqlite3.connect(settings.db_path) as db:
        tasks = db.execute("SELECT owner_id, status, created_at FROM tasks WHERE title = ?", (VIEW_TITLE,)).fetchall()
    assert [(owner, status) for owner, status, _ in tasks] == [("human:ana", "done")]
    assert tasks[0][2][:10] == (now - timedelta(days=6)).date().isoformat()
    with TestClient(create_app(settings)):      # a start of the app, as the demo is opened
        pass
    with sqlite3.connect(settings.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM tasks WHERE title = ?", (VIEW_TITLE,)).fetchone()[0] == 1
