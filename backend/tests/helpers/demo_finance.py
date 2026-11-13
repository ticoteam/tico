"""Helpers that other tests share, moved out of test_demo_finance.py so they import no test file: the demo finance
checks (CHECKS), each taking `book`, a (settings, now) pair."""

import sqlite3
from datetime import timedelta

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.bookkeeper import VIEW_TITLE
from backend.demo_sections.finance import FINANCE_OWNER, CONTENT as C


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


def _default_month_has_this_months_numbers_and_last_month_is_complete(book):
    settings, now = book
    prev_start, this_start = months(now)
    with signed_in(settings) as api:
        default = api.get("/api/v2/finance/pnl").json()
        last_month = api.get(
            "/api/v2/finance/pnl",
            params={
                "start": prev_start.isoformat(),
                "end": (this_start - timedelta(days=1)).isoformat(),
                "basis": "accrual",
            },
        ).json()
        sheet = api.get("/api/v2/finance/balance-sheet").json()
    current = {row["code"]: abs(row["total"]) for row in default["rows"]}
    for code, amount in this_month_by_account().items():
        assert current.get(code) == amount, code
    totals = {row["code"]: abs(row["total"]) for row in last_month["rows"]}
    for code, amount in previous_month_by_account().items():
        assert totals[code] == amount, code
    assert sheet["totals"]["asset"] == sheet["totals"]["liability"] + sheet["totals"]["equity"]


def _budgets_are_monthly_and_only_paid_advertising_is_near_its_limit(book):
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


def _invoices_one_paid_and_one_overdue_that_the_chase_task_covers(book):
    settings, _ = book
    with signed_in(settings) as api:
        invoices = {i["number"]: i for i in api.get("/api/v2/finance/invoices").json()["invoices"]}
        tasks = api.get("/api/v2/tasks", params={"owner": "bot:accounts-receivable-specialist"}).json()["tasks"]
    assert set(invoices) == {inv["number"] for inv in C["invoices"]}
    assert [n for n, i in invoices.items() if i["status"] == "paid"] == ["ACME-1001"]
    overdue = [n for n, i in invoices.items() if i["remaining"] > 0 and i["days_late"] > 0]
    assert overdue == ["ACME-1003"]
    assert C["chase"]["title"] in [t["title"] for t in tasks]


def _bills_are_drafted_by_accounts_payable_and_approved_by_ana(book):
    settings, _ = book
    with signed_in(settings) as api:
        bills = {b["number"]: b for b in api.get("/api/v2/finance/bills").json()["bills"]}
    assert set(bills) == {bill["number"] for bill in C["bills"]}
    manufacturer = bills["LA-20417"]
    assert (manufacturer["created_by"], manufacturer["approved_by"], manufacturer["status"]) == (
        "bot:accounts-payable-specialist",
        "human:ana",
        "paid",
    )
    freight = bills["KF-88213"]
    assert (freight["created_by"], freight["approved_by"], freight["status"]) == (
        "bot:accounts-payable-specialist",
        "human:ana",
        "open",
    )


def _rules_are_in_force_and_the_bookkeepers_proposal_waits_for_ana(book):
    settings, _ = book
    with signed_in(settings) as api:
        rules = {r["name"]: r for r in api.get("/api/v2/finance/rules").json()["rules"]}
    for name in ("Spend over $500 needs Ana", "Marketplace fees go to 6200"):
        assert rules[name]["active"], name
    assert not rules.get(C["proposal"]["name"], {}).get("active")
    with sqlite3.connect(settings.db_path) as db:
        [(status, approval_id)] = db.execute(
            "SELECT status, approval_id FROM fin_actions WHERE kind = 'rule_create' AND actor = ?", (FINANCE_OWNER,)
        ).fetchall()
        [(kind,)] = db.execute("SELECT kind FROM requirements WHERE id = ?", (approval_id,)).fetchall()
    assert (status, kind) == ("needs_approval", "ok")


def _the_finance_owner_sees_the_books_by_default_and_a_start_does_not_ask_or_grant_again(book):
    settings, _ = book
    with signed_in(settings) as api:
        access = api.get("/api/v2/finance/access").json()
    bot = next(t for t in access["teammates"] if t["teammate"] == FINANCE_OWNER)
    assert [(g["what"], g["status"]) for g in bot["grants"]] == [("everything", "active")]
    with TestClient(create_app(settings)):  # a start of the app, as the demo is opened
        pass
    with sqlite3.connect(settings.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM tasks WHERE title = ?", (VIEW_TITLE,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM events WHERE action = 'bookkeeper.view_granted'").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT COUNT(*) FROM fin_view_grants WHERE subject = ? AND what = 'everything'", (FINANCE_OWNER,)
            ).fetchone()[0]
            == 1
        )


# Every check takes `book` (settings, now). test_demo_finance.py runs them on its early-month build and
# test_demo_other_moments.py on a mid-month build.
CHECKS = (
    _default_month_has_this_months_numbers_and_last_month_is_complete,
    _budgets_are_monthly_and_only_paid_advertising_is_near_its_limit,
    _invoices_one_paid_and_one_overdue_that_the_chase_task_covers,
    _bills_are_drafted_by_accounts_payable_and_approved_by_ana,
    _rules_are_in_force_and_the_bookkeepers_proposal_waits_for_ana,
    _the_finance_owner_sees_the_books_by_default_and_a_start_does_not_ask_or_grant_again,
)
