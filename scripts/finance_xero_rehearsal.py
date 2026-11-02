#!/usr/bin/env python3
"""Bring your own books, Xero, rehearsed end to end on this computer: a Tico demo in a new data folder, the real books
worker (backend/finance/books_sync.py) and the real Xero mirror (backend/finance/mirror.py, backend/finance/xero.py) against
the fake Xero (scripts/fake_xero.py) over HTTP. Nothing here reaches the network: the demo refuses every non-loopback
connection while it runs (backend/demo.py network_blocked), and HQ is answered by an in-process fake.

This is a REHEARSAL. It finds what would break on a real run. It is not proof: no real Xero organisation, no HQ OAuth
consent, no HQ token exchange, no Gateway and no real Mercury are involved, and the fake is written from Xero's shapes as
the client and parser read them, not from a recording of Xero.

    python3 scripts/finance_xero_rehearsal.py              # exit 0 only when every check passed
    python3 scripts/finance_xero_rehearsal.py --keep       # keep the demo's data folder

Ports: demo 18871, fake Xero 18872 (the QuickBooks rehearsal uses 18821 to 18823). The run is one child process with its
own new data folder (deleted at the end unless --keep); the child stops its fake by PID and its demo before it exits.

What runs for real: the demo app (create_app), its HTTP API for every Tico-side action and read, the books worker
(books_sync.start / pass_once / delivery), mirror.sync_once with the default page sizes, the Xero client and parser, and
the fake over HTTP.

What is replaced, and where (each is listed again in the report):
  - HQ OAuth broker: books_link._call is answered by an in-process fake HQ (finance_books_rehearsal.FakeHQ). It issues
    tokens, refreshes them and takes the grant off. The consent page and the browser return are not run.
  - Connect route: the demo refuses the HTTP connect route (company_server.off), so connect and reconnect are called
    in-process through books_link.start / finish / connect_claim.
  - Gateway key: hook_relay.link_of returns a fixed Link, so the worker has a key to hand to the broker fake.
  - Webhooks: a relayed Xero envelope is handed to books_sync.delivery (the call triggers.py makes after the trigger's
    signature check); the HQ relay and its signature check are not run.
  - Disconnect: the HTTP disconnect route is tried first; if the demo refuses it, books_link.drop is called in-process.
  - Lost replies and expired tokens: the fake's /_fake/lose and /_fake/expire controls.
  - Provider data: the fake's rows are fetched through its API; expectations are worked out from those rows by the
    functions in this file, which read the same Xero fields the parser reads.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from finance_books_rehearsal import (COMPANY, NO_PROXY, OWNER_ACTOR, XERO_OTHER_TENANT, XERO_TENANT,  # noqa: E402
                                     FakeHQ, Results, http, wait_until)

DEMO_PORT, FAKE_PORT = 18871, 18872
API = f"http://127.0.0.1:{FAKE_PORT}/api.xro/2.0"
ENDPOINT_PATH = {"Accounts": "Accounts", "Contacts": "Contacts", "Items": "Items", "TrackingCategories": "TrackingCategories",
                 "Invoices": "Invoices", "Payments": "Payments", "ManualJournals": "ManualJournals"}
TOKEN = "rehearsal-token-not-a-secret"
MONTH_SEPT, MONTH_OCT, MONTH_NOV = "2026-09", "2026-10", "2026-11"
WINDOW = ("2026-09-01", "2026-11-30")          # the P&L window every check reads: Sept (closed), Oct, Nov (open)
BALANCE_DAY = "2026-11-01"                     # the balance sheet as of today
TODAY = "2026-11-01"
BAL_MONTHS = (MONTH_SEPT, MONTH_OCT, MONTH_NOV)


# ------------------------------------------------------------------------------------------------ Xero's shapes
def xms(day: dt.date, hour: int = 9) -> int:
    return int(dt.datetime(day.year, day.month, day.day, hour, tzinfo=dt.timezone.utc).timestamp() * 1000)


def xdate(day: dt.date) -> str:
    return f"/Date({xms(day)}+0000)/"


def dated(day: dt.date, key: str = "Date") -> dict[str, str]:
    """A Xero date as the API sends it: the Microsoft date and the plain text beside it."""
    return {key: xdate(day), f"{key}String": f"{day.isoformat()}T00:00:00"}


def account(aid: str, code: str, name: str, cls: str, typ: str, system: str = "", status: str = "ACTIVE") -> dict[str, Any]:
    row: dict[str, Any] = {"AccountID": aid, "Code": code, "Name": name, "Class": cls, "Type": typ, "Status": status,
                           "UpdatedDateUTC": xdate(dt.date(2026, 8, 31))}
    if system:
        row["SystemAccount"] = system
    return row


ACCOUNTS = [
    account("acc-bank", "090", "Business Bank Account", "ASSET", "BANK"),
    account("acc-savings", "091", "Savings Account", "ASSET", "BANK"),
    account("acc-ar", "610", "Accounts Receivable", "ASSET", "CURRENT", "DEBTORS"),
    account("acc-equip", "150", "Office Equipment", "ASSET", "FIXED"),
    account("acc-ap", "800", "Accounts Payable", "LIABILITY", "CURRLIAB", "CREDITORS"),
    account("acc-gst", "825", "GST", "LIABILITY", "CURRLIAB", "GST"),
    account("acc-loan", "820", "Bank Loan", "LIABILITY", "TERLIAB"),
    account("acc-cap", "3000", "Owner's Capital", "EQUITY", "EQUITY"),
    account("acc-re", "3100", "Retained Earnings", "EQUITY", "RETAINEDEARNINGS"),
    account("acc-inc-cons", "200", "Consulting Income", "REVENUE", "SALES"),
    account("acc-inc-inst", "201", "Installation Income", "REVENUE", "SALES"),
    account("acc-inc-int", "210", "Interest Income", "REVENUE", "OTHERINCOME"),
    account("acc-exp-adv", "400", "Advertising", "EXPENSE", "OVERHEADS"),
    account("acc-exp-bank", "404", "Bank Fees", "EXPENSE", "OVERHEADS"),
    account("acc-exp-contr", "408", "Contractors", "EXPENSE", "OVERHEADS"),
    account("acc-exp-ins", "412", "Insurance", "EXPENSE", "OVERHEADS"),
    account("acc-exp-meals", "416", "Meals", "EXPENSE", "OVERHEADS"),
    account("acc-exp-office", "420", "Office Supplies", "EXPENSE", "OVERHEADS"),
    account("acc-exp-rent", "424", "Rent", "EXPENSE", "OVERHEADS"),
    account("acc-exp-soft", "425", "Software", "EXPENSE", "OVERHEADS"),
    account("acc-exp-travel", "429", "Travel", "EXPENSE", "OVERHEADS"),
    account("acc-exp-util", "433", "Utilities", "EXPENSE", "OVERHEADS"),
    account("acc-exp-dep", "449", "Depreciation", "EXPENSE", "DEPRECIATN"),
    account("acc-exp-cogs", "500", "Cost of Goods Sold", "EXPENSE", "DIRECTCOSTS"),
    account("acc-exp-pay", "460", "Payroll Expenses", "EXPENSE", "OVERHEADS"),
    account("acc-exp-subs", "470", "Subscriptions", "EXPENSE", "OVERHEADS"),
    account("acc-exp-old", "499", "Old Expense Code", "EXPENSE", "OVERHEADS", status="ARCHIVED"),
    account("acc-inc-old", "290", "Old Income Code", "REVENUE", "SALES", status="ARCHIVED"),
    account("acc-exp-misc", "480", "Miscellaneous", "EXPENSE", "OVERHEADS"),
]
CLASS = {row["AccountID"]: row["Class"] for row in ACCOUNTS}
BANK = "acc-bank"
ACC_CODE_TO_ID = {row["Code"]: row["AccountID"] for row in ACCOUNTS}

CONTACTS = [("con-acme", "Acme Retail", True, False, "ACTIVE"), ("con-north", "Northwind Traders", True, False, "ACTIVE"),
            ("con-job12", "Job 12", True, False, "ACTIVE"), ("con-blue", "Blue Ridge Hosting", False, True, "ACTIVE"),
            ("con-pine", "Pine Design Co", False, True, "ACTIVE"), ("con-summit", "Summit Legal", False, True, "ARCHIVED")]
CUSTOMERS = ["con-acme", "con-north", "con-job12"]

ITEMS = [("item-cons", "CONS", "Consulting", "200"), ("item-inst", "INST", "Installation", "201")]
TRACKING = [("tc-region", "Region", [("opt-east", "East", "ACTIVE"), ("opt-west", "West", "ACTIVE"),
                                     ("opt-legacy", "Legacy", "ARCHIVED")]),
            ("tc-office", "Office", [("opt-denver", "Denver", "ACTIVE"), ("opt-austin", "Austin", "ACTIVE")])]

# Bills (Xero's ACCPAY invoices): id, date, contact, number, [(account, cents)], Xero status, tracking on line 1 or None
BILLS = [
    ("bill-101", dt.date(2026, 9, 3), "con-blue", "BILL-101", [("acc-exp-soft", 12000)], "AUTHORISED", None),
    ("bill-102", dt.date(2026, 9, 8), "con-pine", "BILL-102", [("acc-exp-rent", 150000), ("acc-exp-office", 2500)], "PAID", None),
    ("bill-103", dt.date(2026, 9, 14), "con-summit", "BILL-103", [("acc-exp-contr", 80000)], "PAID", None),
    ("bill-104", dt.date(2026, 9, 21), "con-blue", "BILL-104", [("acc-exp-ins", 45000)], "AUTHORISED", None),
    ("bill-105", dt.date(2026, 9, 28), "con-pine", "BILL-105", [("acc-exp-office", 3300), ("acc-exp-travel", 8900)], "AUTHORISED", None),
    ("bill-106", dt.date(2026, 10, 2), "con-blue", "BILL-106", [("acc-exp-soft", 12000)], "PAID", None),
    ("bill-107", dt.date(2026, 10, 6), "con-summit", "BILL-107", [("acc-exp-contr", 125000)], "PAID", None),
    ("bill-108", dt.date(2026, 10, 9), "con-pine", "BILL-108", [("acc-exp-meals", 4150)], "VOIDED", None),
    ("bill-109", dt.date(2026, 10, 13), "con-blue", "BILL-109", [("acc-exp-bank", 2750), ("acc-exp-util", 19900)], "AUTHORISED", "East"),
    ("bill-110", dt.date(2026, 10, 19), "con-pine", "BILL-110", [("acc-exp-adv", 60000)], "AUTHORISED", None),
    ("bill-111", dt.date(2026, 10, 23), "con-summit", "BILL-111", [("acc-exp-contr", 45000)], "AUTHORISED", None),
    ("bill-112", dt.date(2026, 10, 29), "con-blue", "BILL-112", [("acc-exp-office", 7720)], "AUTHORISED", None),
    ("bill-113", dt.date(2026, 10, 20), "con-blue", "BILL-113", [("acc-exp-soft", 8000)], "AUTHORISED", None),
]
# Bill payments (Xero ACCPAYPAYMENT): id, date, bill id, cents (the bill's whole total)
BILL_PAYMENTS = [("bpay-301", dt.date(2026, 9, 25), "bill-102", 152500), ("bpay-302", dt.date(2026, 10, 5), "bill-103", 80000),
                 ("bpay-303", dt.date(2026, 10, 16), "bill-106", 12000), ("bpay-304", dt.date(2026, 10, 27), "bill-107", 125000)]
# Manual journals (POSTED): id, date, narration, [(account, signed cents: debit +)]
JOURNALS = [
    ("jnl-701", dt.date(2026, 9, 1), "Opening balance", [("acc-bank", 5000000), ("acc-cap", -5000000)]),
    ("jnl-702", dt.date(2026, 9, 15), "Insurance premium", [("acc-exp-ins", 30000), ("acc-bank", -30000)]),
    ("jnl-703", dt.date(2026, 9, 30), "Bank service charge", [("acc-exp-bank", 1500), ("acc-bank", -1500)]),
    ("jnl-704", dt.date(2026, 10, 15), "October rent by transfer", [("acc-exp-rent", 20000), ("acc-bank", -20000)]),
    ("jnl-705", dt.date(2026, 10, 21), "Interest received", [("acc-bank", 8000), ("acc-inc-int", -8000)]),
    ("jnl-706", dt.date(2026, 10, 26), "Owner contribution", [("acc-bank", 100000), ("acc-cap", -100000)]),
]


def invoice_rows() -> list[dict[str, Any]]:
    """104 sales invoices across September and October (Xero pages at 100 rows), each with one or two lines on the two
    income accounts. Every third is paid in full a few days later, every fourth-plus-one part paid."""
    rng = random.Random(20260901)
    rows: list[dict[str, Any]] = []
    for i in range(1, 105):
        day = dt.date(2026, 9, 1) + dt.timedelta(days=(i * 61) // 105)
        lines = [("acc-inc-cons" if i % 2 else "acc-inc-inst", rng.randrange(20000, 300000))]
        if i % 5 == 0:
            lines.append(("acc-inc-inst", rng.randrange(5000, 60000)))
        rows.append({"InvoiceID": f"inv-{i:03d}", "InvoiceNumber": f"INV-{i:04d}", "Type": "ACCREC", "day": day,
                     "contact": CUSTOMERS[i % 3], "lines": lines, "Status": "AUTHORISED", "Reference": ""})
    return rows


def doc_row(kind: str, ident: str, number: str, day: dt.date, contact: str, lines: list[tuple[str, int]], status: str,
            tracking: str | None = None) -> dict[str, Any]:
    items = []
    for n, (account_id, cents) in enumerate(lines, 1):
        item: dict[str, Any] = {"Description": f"Line {n} on {number}", "Quantity": 1, "UnitAmount": f"{cents / 100:.2f}",
                                "LineAmount": f"{cents / 100:.2f}", "AccountID": account_id}
        if tracking and n == 1:
            item["Tracking"] = [{"Name": "Region", "Option": tracking}]
        items.append(item)
    total = sum(c for _, c in lines)
    return {"InvoiceID": ident, "InvoiceNumber": number, "Type": kind, "Contact": {"ContactID": contact},
            **dated(day), "DueDate": xdate(day), "Status": status, "LineAmountTypes": "NoTax", "LineItems": items,
            "Total": f"{total / 100:.2f}", "CurrencyCode": "USD", "UpdatedDateUTC": xdate(day), "Reference": ""}


def seed_rows() -> dict[str, list[dict[str, Any]]]:
    """Xero's records for the first sync, as its API answers them: accounts, contacts, items, tracking categories,
    sales invoices, bills (both are Xero invoices), payments and manual journals."""
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in ENDPOINT_PATH}
    out["Accounts"] = [dict(row) for row in ACCOUNTS]
    for cid, name, customer, supplier, status in CONTACTS:
        out.setdefault("Contacts", []).append({"ContactID": cid, "Name": name, "IsCustomer": customer,
                                              "IsSupplier": supplier, "ContactStatus": status,
                                              "UpdatedDateUTC": xdate(dt.date(2026, 8, 31))})
    for iid, code, name, income in ITEMS:
        out["Items"].append({"ItemID": iid, "Code": code, "Name": name, "IsSold": True, "IsPurchased": False,
                             "IsTrackedAsInventory": False, "SalesDetails": {"UnitPrice": "1800.00", "AccountCode": income},
                             "PurchaseDetails": {"AccountCode": "425"}, "UpdatedDateUTC": xdate(dt.date(2026, 8, 31))})
    for tid, name, options in TRACKING:
        out["TrackingCategories"].append({
            "TrackingCategoryID": tid, "Name": name, "Status": "ACTIVE",
            "Options": [{"TrackingOptionID": oid, "Name": oname, "Status": ostatus} for oid, oname, ostatus in options],
            "UpdatedDateUTC": xdate(dt.date(2026, 8, 31))})
    for row in invoice_rows():
        day = row["day"]
        total = sum(c for _, c in row["lines"])
        paid = (int(row["InvoiceID"][4:]) % 3 == 0)
        status = "PAID" if paid else row["Status"]
        out["Invoices"].append(doc_row("ACCREC", row["InvoiceID"], row["InvoiceNumber"], day, row["contact"], row["lines"], status))
        if paid:
            paid_on = min(day + dt.timedelta(days=3), dt.date(2026, 10, 31))
            out["Payments"].append(payment_row(f"rcpt-{row['InvoiceID'][4:]}", "ACCRECPAYMENT", paid_on,
                                               row["InvoiceID"], "ACCREC", BANK, total, f"RCPT-{row['InvoiceID'][4:]}"))
        elif int(row["InvoiceID"][4:]) % 4 == 1:
            half = total // 2
            out["Payments"].append(payment_row(f"rcpt-{row['InvoiceID'][4:]}", "ACCRECPAYMENT", day + dt.timedelta(days=2),
                                               row["InvoiceID"], "ACCREC", BANK, half, f"RCPT-{row['InvoiceID'][4:]}"))
    out["Invoices"].append({"InvoiceID": "inv-draft", "InvoiceNumber": "INV-DRAFT", "Type": "ACCREC",
                            "Contact": {"ContactID": "con-acme"}, **dated(dt.date(2026, 9, 10)), "Status": "DELETED",
                            "LineAmountTypes": "NoTax", "LineItems": [{"Description": "draft", "LineAmount": "999.00",
                                                                       "AccountID": "acc-inc-cons"}],
                            "Total": "999.00", "UpdatedDateUTC": xdate(dt.date(2026, 9, 10))})
    for bid, day, contact, number, lines, status, tracking in BILLS:
        out["Invoices"].append(doc_row("ACCPAY", bid, number, day, contact, lines, status, tracking))
    for pid, day, bill, cents in BILL_PAYMENTS:
        out["Payments"].append(payment_row(pid, "ACCPAYPAYMENT", day, bill, "ACCPAY", BANK, cents, pid.upper()))
    for jid, day, narration, lines in JOURNALS:
        out["ManualJournals"].append({"ManualJournalID": jid, "Narration": narration, **dated(day), "Status": "POSTED",
                                      "LineAmountTypes": "NoTax", "ShowOnCashBasisReports": True,
                                      "UpdatedDateUTC": xdate(day),
                                      "JournalLines": [{"AccountID": a, "LineAmount": f"{c / 100:.2f}",
                                                        "Description": narration} for a, c in lines]})
    return out


def payment_row(pid: str, kind: str, day: dt.date, target: str, target_kind: str, bank: str, cents: int,
                reference: str) -> dict[str, Any]:
    return {"PaymentID": pid, "PaymentType": kind, "Invoice": {"InvoiceID": target, "Type": target_kind},
            "Account": {"AccountID": bank}, **dated(day), "Amount": f"{cents / 100:.2f}", "Status": "AUTHORISED",
            "Reference": reference, "UpdatedDateUTC": xdate(day)}


# ------------------------------------------------------------------------------------------------ expectations, from rows
def cents(value: object) -> int:
    return int((Decimal(str(value)) * 100).to_integral_value())


def live_doc(row: dict[str, Any]) -> bool:
    return row.get("Status") in ("AUTHORISED", "PAID")


def pnl_of(rows: dict[str, list[dict[str, Any]]], month: str) -> tuple[int, int]:
    """Income and expense, in minor units, that the accrual P&L shows for one month: what the live documents and the posted
    journals dated in it say. Income is positive; a journal's credit to an income account is income."""
    income = expense = 0
    for inv in rows.get("Invoices", []):
        if not live_doc(inv) or inv["DateString"][:7] != month:
            continue
        for line in inv["LineItems"]:
            amount = cents(line["LineAmount"])
            if inv["Type"] == "ACCREC":
                income += amount
            else:
                expense += amount
    for jrn in rows.get("ManualJournals", []):
        if jrn.get("Status") != "POSTED" or jrn["DateString"][:7] != month:
            continue
        for line in jrn["JournalLines"]:
            amount, cls = cents(line["LineAmount"]), CLASS[line["AccountID"]]
            if cls == "REVENUE":
                income -= amount
            elif cls == "EXPENSE":
                expense += amount
    return income, expense


def balances_of(rows: dict[str, list[dict[str, Any]]]) -> tuple[int, int, int]:
    """Bank, accounts receivable and accounts payable, in minor units, as the live documents, the payments and the posted
    journals leave them today."""
    bank = ar = ap = 0
    for inv in rows.get("Invoices", []):
        if not live_doc(inv):
            continue
        if inv["Type"] == "ACCREC":
            ar += cents(inv["Total"])
        else:
            ap += cents(inv["Total"])
    for pay in rows.get("Payments", []):
        if pay.get("Status") != "AUTHORISED":
            continue
        amount = cents(pay["Amount"])
        if pay["PaymentType"] == "ACCRECPAYMENT":
            bank += amount
            ar -= amount
        else:
            bank -= amount
            ap -= amount
    for jrn in rows.get("ManualJournals", []):
        if jrn.get("Status") == "POSTED":
            bank += sum(cents(line["LineAmount"]) for line in jrn["JournalLines"] if line["AccountID"] == BANK)
    return bank, ar, ap


def months_of(rows: dict[str, list[dict[str, Any]]], month: str, pre: dict[str, list[dict[str, Any]]]) -> dict[str, tuple[int, int]]:
    """What Tico's P&L must show per month once the Sept close holds and the provider's changes are in. Sept is frozen at
    its close (`pre`). Oct is open, so it shows the live October documents. A change to a Sept document posts its reversal
    and replacement in the open period (Nov), so Nov is the Nov documents plus the Sept change (today minus the close)."""
    sept_now, sept_pre = pnl_of(rows, MONTH_SEPT), pnl_of(pre, MONTH_SEPT)
    nov_docs = pnl_of(rows, MONTH_NOV)
    late = (sept_now[0] - sept_pre[0], sept_now[1] - sept_pre[1])
    return {MONTH_SEPT: sept_pre, MONTH_OCT: pnl_of(rows, MONTH_OCT),
            MONTH_NOV: (nov_docs[0] + late[0], nov_docs[1] + late[1])}


def fmt(pair: tuple[int, int]) -> str:
    return f"income={pair[0]} expense={pair[1]}"


# ------------------------------------------------------------------------------------------------ the rehearsal
NOT_AVAILABLE = "not_available"      # the error code a refused kind answers with (core/errors.py)

# A payload that is valid for its kind, so that the refusal (not a validation error) is what the check sees. A payee by
# check needs no account numbers, so nothing is sealed for it.
REFUSAL_PAYLOADS: dict[str, dict[str, Any]] = {
    "recipient_add": {"name": "Refusal check payee", "method": "check",
                      "address": {"line1": "1 Main St", "city": "Austin", "region": "TX", "postal_code": "78701"}},
}


class XeroRehearsal:
    def __init__(self, keep: bool) -> None:
        self.keep = keep
        self.res = Results("xero")
        self.data = Path(tempfile.mkdtemp(prefix="tico-books-rehearsal-xero-"))
        self.hq = FakeHQ("xero")
        self.base = f"http://127.0.0.1:{DEMO_PORT}/api/v2"
        self.token = ""
        self.cid = ""
        self.fake_proc: subprocess.Popen | None = None
        self.server: Any = None
        self.server_thread: threading.Thread | None = None
        self.app: Any = None
        self.settings: Any = None
        self.store: Any = None
        self.vault: Any = None
        self.link: Any = None
        self.pre: dict[str, list[dict[str, Any]]] = {}
        self.notes: dict[str, Any] = {}

    # -- the fake Xero, started and stopped by PID
    def start_fake(self) -> None:
        args = [sys.executable, str(ROOT / "scripts/fake_xero.py"), "--port", str(FAKE_PORT), "--tenant", XERO_TENANT]
        self.fake_proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"[xero] fake xero pid {self.fake_proc.pid} on 127.0.0.1:{FAKE_PORT}", flush=True)
        if not wait_until(self.fake_up, 15):
            raise RuntimeError("the fake Xero did not start")

    def fake_up(self) -> bool:
        try:
            self.fake_get("/_fake/calls")
            return True
        except OSError:
            return False

    def fake_get(self, path: str) -> Any:
        request = urllib.request.Request(f"http://127.0.0.1:{FAKE_PORT}{path}", headers={
            "Authorization": "Bearer " + TOKEN, "xero-tenant-id": XERO_TENANT})
        with NO_PROXY.open(request, timeout=30) as reply:
            return json.loads(reply.read() or b"{}")

    def fake_post(self, path: str, body: object) -> Any:
        status, answer = http("POST", f"http://127.0.0.1:{FAKE_PORT}{path}", body)
        if status != 200:
            raise RuntimeError(f"fake {path}: {status} {str(answer)[:200]}")
        return answer

    def fake_calls(self) -> list[dict[str, Any]]:
        return list(self.fake_get("/_fake/calls")["calls"])

    def fake_writes(self) -> list[dict[str, Any]]:
        return [c for c in self.fake_calls() if c["method"] in ("PUT", "POST") and c["path"].startswith("/api.xro/2.0/")]

    def fake_rows(self) -> dict[str, list[dict[str, Any]]]:
        """Every row the fake holds, read through its API page by page (the provider's own state)."""
        out: dict[str, list[dict[str, Any]]] = {}
        for endpoint in ("Invoices", "Payments", "ManualJournals", "Contacts", "Accounts", "Items", "TrackingCategories"):
            rows: list[dict[str, Any]] = []
            page = 1
            while True:
                reply = self.fake_get(f"/api.xro/2.0/{endpoint}?page={page}")
                chunk = list(reply.get(endpoint, []))
                rows += chunk
                if endpoint in ("Accounts", "TrackingCategories") or len(chunk) < 100:
                    break
                page += 1
            out[endpoint] = rows
        return out

    def fake_row(self, endpoint: str, ident: str) -> dict[str, Any]:
        key = self.id_key(endpoint)
        for row in self.fake_rows()[endpoint]:
            if row.get(key) == ident:
                return dict(row)
        raise RuntimeError(f"no {endpoint} {ident} in the fake")

    @staticmethod
    def id_key(endpoint: str) -> str:
        return {"Invoices": "InvoiceID", "Payments": "PaymentID", "ManualJournals": "ManualJournalID"}[endpoint]

    def edit(self, endpoint: str, ident: str, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        """A provider edit: the row as the fake holds it, changed, and sent back as a change (its time is now)."""
        row = self.fake_row(endpoint, ident)
        change(row)
        self.fake_post("/_fake/change", {endpoint: [row]})
        return row

    def stop_fake(self) -> None:
        if self.fake_proc is not None and self.fake_proc.poll() is None:
            self.fake_proc.terminate()
            try:
                self.fake_proc.wait(10)
            except subprocess.TimeoutExpired:
                self.fake_proc.kill()
                self.fake_proc.wait(10)
        self.fake_proc = None

    # -- the demo, in this process, served on its own port
    def start_demo(self) -> None:
        import uvicorn
        from backend import demo, hook_relay
        from backend.app import create_app
        from backend.finance import books_link
        self.books_link = books_link
        self.settings = demo.build(self.data, url=f"http://127.0.0.1:{DEMO_PORT}")
        self.app = create_app(self.settings)
        self.link = hook_relay.Link("gk_rehearsal_not_a_secret", "https://hq.invalid.example/v1/gateway", "fp-rehearsal")
        hook_relay.link_of = lambda c, vault, settings: self.link       # the worker's Gateway key (a fixed Link)
        books_link._call = self.hq                                          # HQ answered by the fake
        config = uvicorn.Config(self.app, host="127.0.0.1", port=DEMO_PORT, log_level="warning", access_log=False)
        self.server = uvicorn.Server(config)
        self.server_thread = threading.Thread(target=self.server.run, name="demo-http", daemon=True)
        self.server_thread.start()
        if not wait_until(lambda: self.server.started, 60):
            raise RuntimeError("the demo did not start")
        self.token = (self.data / "local-owner.token").read_text().strip()
        self.store, self.vault = self.app.state.store, self.app.state.vault

    def stop_demo(self) -> None:
        from backend.finance import books_sync
        try:
            books_sync.stop()
        except Exception:
            pass
        if self.server is not None:
            self.server.should_exit = True
            if self.server_thread is not None:
                self.server_thread.join(15)

    def get(self, path: str) -> Any:
        status, body = http("GET", self.base + path, token=self.token)
        if status != 200:
            raise RuntimeError(f"GET {path}: {status} {str(body)[:300]}")
        return body

    def act(self, kind: str, payload: dict[str, Any], dry_run: bool = False) -> tuple[int, dict[str, Any]]:
        body = {"kind": kind, "payload": payload, "dry_run": dry_run}
        status, answer = http("POST", self.base + "/finance/actions", body, token=self.token)
        return status, answer if isinstance(answer, dict) else {"raw": answer}

    def sources_row(self) -> dict[str, Any]:
        for group in self.get("/finance/sources")["groups"]:
            if group["group"] == "accounting" and group["rows"]:
                return dict(group["rows"][0])
        return {}

    def books_rows(self) -> list[dict[str, Any]]:
        return [r for g in self.get("/finance/sources")["groups"] if g["group"] == "accounting" for r in g["rows"]]

    def outbox(self) -> dict[str, int]:
        return dict(self.sources_row().get("outbox") or {})

    def writes_settled(self) -> bool:
        counts = self.outbox()
        return all(int(counts.get(k, 0)) == 0 for k in ("pending", "unknown", "failed"))

    def ring(self) -> None:
        from backend.finance import books_sync
        books_sync.ring()

    def settle(self, predicate: Callable[[], bool], seconds: float, ring_every: float = 20.0) -> bool:
        """Wakes the books worker now and every `ring_every` seconds until the predicate holds or the time is up."""
        deadline, last = time.time() + seconds, 0.0
        while True:
            if time.time() - last >= ring_every:
                self.ring()
                last = time.time()
            try:
                if predicate():
                    return True
            except Exception:
                pass
            if time.time() > deadline:
                return False
            time.sleep(1.0)

    def close(self) -> None:
        self.stop_demo()
        self.stop_fake()
        if not self.keep:
            shutil.rmtree(self.data, ignore_errors=True)

    # -- the Tico reads the checks use
    def pnl_months(self) -> dict[str, tuple[int, int]]:
        pnl = self.get(f"/finance/pnl?start={WINDOW[0]}&end={WINDOW[1]}&period=month&basis=accrual")
        income, expense = list(pnl["income"]), list(pnl["expense"])
        months = [MONTH_SEPT, MONTH_OCT, MONTH_NOV]
        return {m: (int(income[i]), int(expense[i])) for i, m in enumerate(months)}

    def balances(self) -> tuple[int, int, int]:
        bs = self.get(f"/finance/balance-sheet?as_of={BALANCE_DAY}")

        def named(rows: list[dict[str, Any]], name: str) -> int:
            return sum(int(r["balance"]) for r in rows if r.get("name") == name)
        return (named(bs["assets"], "Business Bank Account"), named(bs["assets"], "Accounts Receivable"),
                named(bs["liabilities"], "Accounts Payable"))

    # -- the steps
    def step_connect(self) -> None:
        res = self.res
        res.step = "1 connect"
        status, _ = http("POST", self.base + "/finance/books/connect", {"provider": "xero"}, token=self.token)
        res.check("the HTTP connect route refuses a demo copy (company_server.off), as designed", status == 409, f"HTTP {status}")
        res.skip("HTTP consent: the browser, HQ's consent page and the return route are not run")
        flow_start = len(self.hq.calls)
        self.books_link.start(self.store, self.link, provider="xero", company_id=COMPANY, user=OWNER_ACTOR,
                              return_base=self.settings.public_url + "/api/v2/finance/books/return")
        return_to = str(self.hq.calls[flow_start][1]["return_to"])
        flow = return_to.rsplit("/", 1)[1]
        self.hq.realm = XERO_TENANT
        self.cid = self.books_link.finish(self.store, self.vault, self.link, flow=flow, user=OWNER_ACTOR,
                                          outcome="connected", reason="", claim_id="clm_rehearsal_xero_1")
        row = self.sources_row()
        sync = row.get("sync", {})
        res.check("the connection is recorded and active", bool(self.cid) and sync.get("status") == "active"
                  and row.get("provider") == "xero", f"status={sync.get('status')} provider={row.get('provider')}")
        res.check("exactly one books connection on this install", len(self.books_rows()) == 1)
        leaked = [f"{p}-{i}" for p in ("acc", "ref") for i in range(1, self.hq.issued + 1)]
        shown = json.dumps(self.get("/finance/sources"))
        res.check("the sources page shows no Xero token", not any(t in shown for t in leaked),
                  f"tokens issued by the fake HQ: {self.hq.issued}")
        # A second Xero organisation on the same install: HQ refuses it (spec §3.4). The refusal is HQ's; here HQ is the fake.
        self.hq.realm = XERO_OTHER_TENANT
        self.hq.refuse_next.append(self.books_link.AlreadyConnected(None))
        refused = False
        try:
            self.books_link.connect_claim(self.store, self.vault, self.link, provider="xero", company_id=COMPANY,
                                          claim_id="clm_rehearsal_xero_2", name="second", by=OWNER_ACTOR,
                                          now="2026-11-01T12:00:00.000000Z")
        except self.books_link.AlreadyConnected:
            refused = True
        res.check("a second Xero organisation on the same install is refused (already_connected from HQ)", refused)
        res.check("the refused organisation makes no second connection", len(self.books_rows()) == 1)
        self.hq.realm = XERO_TENANT
        same = self.books_link.connect_claim(self.store, self.vault, self.link, provider="xero", company_id=COMPANY,
                                             claim_id="clm_rehearsal_xero_3", name="again", by=OWNER_ACTOR,
                                             now="2026-11-01T12:01:00.000000Z")
        res.check("reconnecting the same organisation keeps the same connection", same == self.cid, f"{same} vs {self.cid}")

    def step_first_sync(self) -> None:
        from backend.finance import books_sync
        res = self.res
        res.step = "2 first sync"
        seed = seed_rows()
        self.fake_post("/_fake/seed", seed)
        status, dry = self.act("mirror_start", {"connection_id": self.cid}, dry_run=True)
        res.check("the mirror switch previews", status == 200, f"HTTP {status} impact={str(dry.get('impact'))[:200]}")
        status, live = self.act("mirror_start", {"connection_id": self.cid})
        res.check("the mirror switch is committed", status == 200 and live.get("status") == "committed",
                  f"HTTP {status} {live.get('code') or live.get('reason') or ''}")
        books_sync.start(self.store, self.vault, self.settings, rehearsal=False)
        seeded = {"pnl": pnl_of(seed, MONTH_SEPT), "oct": pnl_of(seed, MONTH_OCT)}
        exp_inc = pnl_of(seed, MONTH_SEPT)[0] + pnl_of(seed, MONTH_OCT)[0]
        exp_exp = pnl_of(seed, MONTH_SEPT)[1] + pnl_of(seed, MONTH_OCT)[1]
        exp_bank, exp_ar, exp_ap = balances_of(seed)

        def totals() -> tuple[int, int, int, int, int, int]:
            months = self.pnl_months()
            income = months[MONTH_SEPT][0] + months[MONTH_OCT][0]
            expense = months[MONTH_SEPT][1] + months[MONTH_OCT][1]
            return (income, expense, *self.balances())
        want = (exp_inc, exp_exp, exp_bank, exp_ar, exp_ap)
        done = self.settle(lambda: totals()[:2] == want[:2] and totals()[2:] == want[2:], 600)
        got = totals()
        res.check("the backfill finishes and the P&L and balance sheet match the seeded totals to the cent",
                  done and got == (*want[:2], *want[2:]), f"want income,expense,bank,ar,ap={want} got={got}")
        res.check("the seed has more than 100 sales invoices (Xero pages are 100 rows)",
                  sum(1 for r in seed["Invoices"] if r["Type"] == "ACCREC" and r["Status"] != "DELETED") > 100)
        pages = [c for c in self.fake_calls() if c["method"] == "GET" and c["path"].endswith("/Invoices")
                 and int(c["query"].get("page", 1)) >= 2]
        res.check("the backfill read Invoices across more than one page", len(pages) >= 1, f"page reads past 1: {len(pages)}")
        row = self.sources_row()
        res.check("the sync reports a successful read", bool(row.get("sync", {}).get("last_ok_at")),
                  str(row.get("sync", {}).get("last_error")))
        bs = self.balances()
        res.check("bank, receivable and payable balances on the balance sheet match the seed to the cent",
                  bs == balances_of(seed), f"want={balances_of(seed)} got={bs}")
        archived = json.dumps(self.get("/finance/contacts?kind=vendor&active=false"))
        res.check("the archived vendor comes in inactive (Summit Legal)", "Summit Legal" in archived)
        invoices = json.dumps(self.get("/finance/invoices?limit=200"))
        res.check("the deleted draft invoice (INV-DRAFT) adds nothing and is not listed", "INV-DRAFT" not in invoices)
        bills_raw = self.get("/finance/bills?limit=200")
        found = walk_for(bills_raw, "number", "BILL-108")
        res.check("the voided bill (BILL-108) is not an open bill in Tico (it adds nothing to P&L or AP)",
                  found is None or found.get("status") == "void", f"found status={found and found.get('status')}")
        res.check("bill BILL-109 (Region: East on line 1) is in Tico (presence only; the tag is not read here)",
                  walk_for(bills_raw, "number", "BILL-109") is not None)
        self.notes["seeded_totals"] = want

    def step_changes(self) -> None:
        res = self.res
        res.step = "3 changes"
        self.pre = self.fake_rows()
        months_before = self.pnl_months()
        expect_pre = months_of(self.pre, MONTH_SEPT, self.pre)
        res.check("Tico's P&L by month matches the provider's rows before the changes (Sept, Oct, Nov)",
                  all(months_before[m] == expect_pre[m] for m in BAL_MONTHS),
                  f"tico={ {m: fmt(months_before[m]) for m in BAL_MONTHS} } want={ {m: fmt(expect_pre[m]) for m in BAL_MONTHS} }")
        status, body = self.act("close_period", {"month": MONTH_SEPT})
        res.check("September closes in Tico before the provider edits it", status == 200 and body.get("status") == "committed",
                  f"HTTP {status} {str(body)[:160]}")
        status, body = self.act("journal", {"date": "2026-09-15", "memo": "Late Sept entry",
                                            "lines": [{"account_id": self.acc("425"), "amount": 1000},
                                                      {"account_id": self.acc("090"), "amount": -1000}]})
        res.check("a Tico entry dated in the closed month is refused (§4.1: no posting into a closed period)",
                  body.get("status") == "failed" and "closed" in str(body.get("reason", "")).lower(),
                  str(body.get("reason"))[:200])
        sept_before = self.pnl_months()[MONTH_SEPT]
        res.check("the closed month reads the same after the refused entry", sept_before == expect_pre[MONTH_SEPT],
                  fmt(sept_before))

        # Polled changes: Xero sends no webhook for a payment or a manual journal, so only the change feed brings them.
        calls_before = len(self.fake_calls())
        receipt = next(r for r in self.pre["Payments"] if r["PaymentType"] == "ACCRECPAYMENT"
                       and r["DateString"][:7] == MONTH_SEPT)
        self.edit("Payments", receipt["PaymentID"], lambda r: r.update({"Status": "DELETED"}))
        # Xero reopens an invoice whose receipt is deleted; the invoice row moves too.
        self.edit("Invoices", receipt["Invoice"]["InvoiceID"],
                  lambda r: r.update({"Status": "AUTHORISED"}) if r.get("Status") == "PAID" else None)
        self.edit("ManualJournals", "jnl-704", lambda r: r.update({"Status": "VOIDED"}))
        rows = self.fake_rows()
        want = months_of(rows, MONTH_SEPT, self.pre)
        want_bs = balances_of(rows)
        done = self.settle(lambda: self.pnl_months() == want and self.balances() == want_bs, 720)
        polled = [c for c in self.fake_calls()[calls_before:] if c["method"] == "GET"
                  and c["path"].rsplit("/", 1)[-1] in ("Payments", "ManualJournals") and c["if_modified_since"]]
        res.check("the change feed (modified-since) brings the polled payment delete and journal void, with no webhook",
                  done and len(polled) >= 2, f"modified-since reads of those lists: {len(polled)}")
        self.notes["polled_settled"] = done

        # Webhook changes: invoice and bill edits, a void and a delete, each relayed as a Xero event.
        inv_oct = next(r["InvoiceID"] for r in self.pre["Invoices"] if r["Type"] == "ACCREC" and r["Status"] == "AUTHORISED"
                       and r["DateString"][:7] == MONTH_OCT)
        self.edit("Invoices", inv_oct, lambda r: bump_invoice(r, 5000))
        self.edit("Invoices", "bill-101", lambda r: set_bill_amount(r, 15000))
        self.edit("Invoices", "bill-105", lambda r: r.update({"Status": "VOIDED"}))
        self.edit("Invoices", "bill-112", lambda r: r.update({"Status": "VOIDED"}))
        # Xero sends CREATE and UPDATE events only, so a void arrives as an UPDATE. Xero deletes only drafts and payments;
        # the deleted payment is in the polled path above.
        events = [("Invoices", inv_oct, "UPDATE"), ("Invoices", "bill-101", "UPDATE"),
                  ("Invoices", "bill-105", "UPDATE"), ("Invoices", "bill-112", "UPDATE"),
                  ("Invoices", receipt["Invoice"]["InvoiceID"], "UPDATE")]
        envelope = webhook_envelope("xero-rehearsal-1", events)
        from backend.finance import books_sync as BS
        with self.store.transaction() as c:
            kept = BS.delivery(c, "xero", envelope)
        res.check("a relayed Xero webhook is kept for the worker", kept == "kept", kept)
        with self.store.transaction() as c:
            again = BS.delivery(c, "xero", envelope)
        res.check("the same Xero webhook event twice is kept once", again == "seen", again)
        rows = self.fake_rows()
        want = months_of(rows, MONTH_SEPT, self.pre)
        want_bs = balances_of(rows)
        done = self.settle(lambda: self.pnl_months() == want and self.balances() == want_bs, 300)
        got = self.pnl_months()
        res.check("the provider's edits, voids and deletes land: Sept frozen, Oct and Nov as the spec says, balance sheet today",
                  done, f"want={ {m: fmt(want[m]) for m in BAL_MONTHS} } got={ {m: fmt(got[m]) for m in BAL_MONTHS} } "
                        f"bs want={want_bs} got={self.balances()}")
        res.check("September itself stays as it closed (the frozen month)", got[MONTH_SEPT] == expect_pre[MONTH_SEPT],
                  f"sept now {fmt(got[MONTH_SEPT])}, at close {fmt(expect_pre[MONTH_SEPT])}")
        # the close snapshot's marker shows on a closed month's P&L read (the close checklist does not carry it)
        sept = self.get(f"/finance/pnl?start={MONTH_SEPT}-01&end={MONTH_SEPT}-30&period=month&basis=accrual")
        restatements = (sept.get("snapshot") or {}).get("restatements")
        res.check("the closed month takes the restatement and flags it on its close snapshot",
                  restatements is not None and int(restatements) >= 1,
                  f"September's close snapshot (its P&L read) restatements={restatements}")
        tx = self.get("/finance/transactions?limit=200")
        res.check("a late item posts in the open period with its original date in the description (§4.1)",
                  any_text(tx, "2026-09-03"), "searched the transactions list text for the original date 2026-09-03 (bill-101)")

    def acc(self, code: str) -> str:
        found = walk_for(self.get("/finance/chart"), "number", code)
        if found is None:
            raise RuntimeError(f"no account {code} in Tico's chart")
        return str(found.get("id") or found.get("account_id"))

    def step_writes(self) -> None:
        res = self.res
        res.step = "4 writes"
        self.wait_settled()
        vendor = self.contact("Blue Ridge Hosting", "vendor")
        customer = self.contact("Acme Retail", "customer")
        soft, cons, rent, bank = self.acc("425"), self.acc("200"), self.acc("424"), self.acc("090")
        before = self.fake_rows()
        status, body = self.act("bill_create", {"vendor_id": vendor, "number": "T-BILL-1", "bill_date": TODAY,
                                                "lines": [{"account_id": soft, "amount": 4500, "description": "Tico bill"}]},)
        res.check("a bill from Tico is queued and goes out through the outbox", status == 200 and body.get("status") != "failed",
                  f"HTTP {status} {str(body)[:160]}")
        self.wait_settled()
        ok = self.fake_has("Invoices", "InvoiceNumber", "T-BILL-1", lines=[("acc-exp-soft", 4500)])
        res.check("the bill is confirmed locally with the same amount and account (Software 425)", ok and self.tico_has_bill("T-BILL-1", 4500, "425"))
        status, body = self.act("invoice_create", {"customer_id": customer, "number": "T-INV-1", "invoice_date": TODAY,
                                                   "lines": [{"account_id": cons, "amount": 25000, "description": "Tico invoice"}]})
        res.check("an invoice from Tico is queued", status == 200 and body.get("status") != "failed", str(body)[:160])
        self.wait_settled()
        res.check("the invoice is confirmed in the provider with the same amount and account (Consulting 200)",
                  self.fake_has("Invoices", "InvoiceNumber", "T-INV-1", lines=[("acc-inc-cons", 25000)]) and
                  self.tico_has_invoice("T-INV-1", 25000, "200"))
        open_inv = next(r for r in before["Invoices"] if r["Type"] == "ACCREC" and r["Status"] == "AUTHORISED"
                        and not any(p["Invoice"]["InvoiceID"] == r["InvoiceID"] for p in before["Payments"]))
        tico_inv = self.tico_invoice_id(open_inv["InvoiceNumber"])
        status, body = self.act("invoice_record_payment", {"invoice_id": tico_inv, "amount": 10000, "date": TODAY,
                                                           "account_id": bank})
        res.check("a payment on an invoice is queued", status == 200 and body.get("status") != "failed", str(body)[:160])
        self.wait_settled()
        res.check("the payment is confirmed in the provider with the same amount and bank account",
                  self.fake_payment(open_inv["InvoiceID"], 10000))
        status, body = self.act("journal", {"date": TODAY, "memo": "Tico journal", "source": "manual",
                                            "lines": [{"account_id": rent, "amount": 10000},
                                                      {"account_id": bank, "amount": -10000}]})
        res.check("a journal from Tico is queued", status == 200 and body.get("status") != "failed", str(body)[:160])
        self.wait_settled()
        res.check("the journal is confirmed in the provider with the same amounts and accounts (Rent 424 and Checking)",
                  self.fake_journal("Tico journal", [("acc-exp-rent", 10000), ("acc-bank", -10000)]))
        # A lost reply: the write lands at the provider, its answer does not reach the install, the retry must not add a copy.
        self.fake_post("/_fake/lose", {"times": 1})
        status, body = self.act("bill_create", {"vendor_id": vendor, "number": "T-BILL-2", "bill_date": TODAY,
                                                "lines": [{"account_id": soft, "amount": 3000, "description": "retry bill"}]})
        res.check("the bill whose reply is lost is queued", status == 200 and body.get("status") != "failed")
        settled = self.settle(lambda: self.writes_settled() and self.fake_count("Invoices", "InvoiceNumber", "T-BILL-2") >= 1, 240)
        copies = self.fake_count("Invoices", "InvoiceNumber", "T-BILL-2")
        res.check("after a lost reply the retry makes no duplicate (one bill T-BILL-2 at the provider)",
                  settled and copies == 1 and self.tico_has_bill("T-BILL-2", 3000, "425"),
                  f"provider copies={copies} settled={settled} outbox={self.outbox()}")
        self.notes["lost_reply_writes"] = self.fake_count("Invoices", "InvoiceNumber", "T-BILL-2")
        # An expired token: the provider refuses once with 401, the install asks HQ for a new token and tries again.
        refreshes = self.hq.refreshes
        self.fake_post("/_fake/expire", {})
        status, body = self.act("journal", {"date": TODAY, "memo": "Tico journal after expiry", "source": "manual",
                                            "lines": [{"account_id": rent, "amount": 2000},
                                                      {"account_id": bank, "amount": -2000}]})
        self.wait_settled()
        res.check("a 401 from the provider refreshes the token through HQ, then the write goes through",
                  self.hq.refreshes > refreshes and self.fake_journal("Tico journal after expiry",
                                                                       [("acc-exp-rent", 2000), ("acc-bank", -2000)]),
                  f"refreshes {refreshes} -> {self.hq.refreshes}")

    def step_refusals(self) -> None:
        from backend.finance import mirror
        res = self.res
        res.step = "5 refused writes"
        self.wait_settled()
        before_writes = len(self.fake_writes())
        before_outbox = self.outbox()
        seen: list[str] = []
        clear = True
        known = set(self.kinds())
        for kind in mirror.REFUSED:
            if kind not in known:
                seen.append(f"{kind}: not an action kind in this build (skipped)")
                continue
            status, body = self.act(kind, REFUSAL_PAYLOADS.get(kind, {}))
            reason = str(body.get("reason") or "")
            ok = body.get("status") == "failed" and body.get("code") == NOT_AVAILABLE and "mirror mode" in reason
            clear = clear and ok
            seen.append(f"{kind}: {body.get('status')} {body.get('code')} {reason[:110]}")
        res.check("every kind §10 keeps provider-only is refused with the mirror-mode reason", clear, "; ".join(seen))
        # Purchase orders and estimates are not in §10's list, so mirror mode plans them and the fake has no shape for them.
        # The check asks only that an answer is clear (a refusal with a reason, or a write); the answer is recorded.
        for kind in ("po_create", "estimate_create", "estimate_convert"):
            if kind not in known:
                continue
            status, body = self.act(kind, {})
            reason = str(body.get("reason") or "")
            res.check(f"{kind} against Xero gives a clear answer (refusal with a reason, or a write)",
                      body.get("status") != "failed" or bool(reason),
                      f"status={body.get('status')} code={body.get('code')} {reason[:140]}")
        self.settle(lambda: False, 10)
        res.check("the refused writes add no outbox row", self.outbox() == before_outbox,
                  f"before={before_outbox} after={self.outbox()}")
        res.check("the refused writes send nothing to the provider", len(self.fake_writes()) == before_writes,
                  f"writes before={before_writes} after={len(self.fake_writes())}")

    def step_budgets(self) -> None:
        res = self.res
        res.step = "6 budgets and holds"
        self.wait_settled()
        software = self.acc("425")
        status, body = self.act("budget_create", {"name": "Software (Nov)", "period": "month", "account_id": software,
                                                  "amount": 100000, "at_limit": "decline", "effective_from": TODAY})
        res.check("a monthly budget on the mirrored Software line is set", status == 200 and body.get("status") == "committed",
                  f"HTTP {status} {str(body)[:200]}")
        budget = self.budget("Software (Nov)")
        rows = self.fake_rows()
        # November's Software actual: the November bills at the provider (Tico's own bills included), plus the change to
        # bill-101 from September, which posts into November because September is closed.
        late_soft = soft_of(rows["Invoices"], MONTH_SEPT) - soft_of(self.pre["Invoices"], MONTH_SEPT)
        want_actual = soft_of(rows["Invoices"], MONTH_NOV) + late_soft
        state = budget.get("state", {}) if budget else {}
        got_actual = int(state.get("actual", -1)) if state else -1
        res.check("the budget on a mirrored expense line sees the mirrored actuals to the cent",
                  budget is not None and got_actual == want_actual, f"want {want_actual} got state={str(state)[:240]}")
        # A payment held while its reply is lost: the second payment is refused beyond what is left.
        target = next(r for r in self.fake_rows()["Invoices"] if r["Type"] == "ACCREC" and r["Status"] == "AUTHORISED"
                      and not any(p["Invoice"]["InvoiceID"] == r["InvoiceID"] for p in self.fake_rows()["Payments"]))
        total = cents(target["Total"])
        first = total // 2
        tico_id = self.tico_invoice_id(target["InvoiceNumber"])
        bank = self.acc("090")
        writes_before = len(self.fake_writes())
        self.fake_post("/_fake/lose", {"times": 1})
        status, body = self.act("invoice_record_payment", {"invoice_id": tico_id, "amount": first, "date": TODAY,
                                                           "account_id": bank})
        sent = self.settle(lambda: len(self.fake_writes()) > writes_before, 90)
        left = total - first
        status2, body2 = self.act("invoice_record_payment", {"invoice_id": tico_id, "amount": left + 1, "date": TODAY,
                                                             "account_id": bank})
        res.check("while a payment's reply is lost (held), a second payment beyond what is left is refused",
                  sent and body2.get("status") == "failed" and f"({left})" in str(body2.get("reason", "")),
                  f"left should be {left}; first status={body.get('status')}; second: {body2.get('status')} "
                  f"{str(body2.get('reason'))[:160]}")
        self.wait_settled()
        res.check("the held payment lands once: one payment at the provider, no duplicate after its retry",
                  self.fake_payment_count(target["InvoiceID"], first) == 1, f"copies={self.fake_payment_count(target['InvoiceID'], first)}")
        # A pending mirrored bill is a hold source in §4.4; this asks whether it holds the budget while it is pending.
        self.fake_post("/_fake/lose", {"times": 1})
        status, body = self.act("bill_create", {"vendor_id": self.contact("Blue Ridge Hosting", "vendor"), "number": "T-BILL-3",
                                                "bill_date": TODAY,
                                                "lines": [{"account_id": software, "amount": 9000, "description": "held bill"}]})
        self.settle(lambda: self.fake_count("Invoices", "InvoiceNumber", "T-BILL-3") >= 1, 60)
        state = self.budget("Software (Nov)").get("state", {})
        res.check("a mirrored bill still waiting for the provider holds its budget (§4.4 bill holds)",
                  int(state.get("held", 0)) == 9000, f"held={state.get('held')} state={str(state)[:200]}")
        self.wait_settled()
        status, body = self.act("bill_create", {"vendor_id": self.contact("Blue Ridge Hosting", "vendor"), "number": "T-BILL-OVER",
                                                "bill_date": TODAY,
                                                "lines": [{"account_id": software, "amount": 95000, "description": "over"}]})
        res.check("a bill past a decline budget's limit is refused (§4.4: new requests refused at limit)",
                  body.get("status") == "failed", f"status={body.get('status')} reason={str(body.get('reason'))[:160]}")
        self.wait_settled()

    def step_disconnect(self) -> None:
        res = self.res
        res.step = "7 disconnect"
        self.wait_settled()
        before = self.pnl_months(), self.balances()
        writes_before = len(self.fake_writes())
        status, body = http("POST", self.base + f"/finance/books/{self.cid}/disconnect", {}, token=self.token)
        via = "HTTP"
        if status != 200:
            self.books_link.drop(self.store, self.vault, self.link, self.cid, by=OWNER_ACTOR)
            via = f"in-process books_link.drop (HTTP said {status}: {str(body)[:120]})"
        res.check("the books connection is disconnected", True, via)
        row = self.sources_row()
        res.check("after disconnect the connection is recorded as disconnected and the books stay", row.get("sync", {}).get("status")
                  in ("disconnected", None) and len(self.books_rows()) == 1 and (self.pnl_months(), self.balances()) == before,
                  f"sync={row.get('sync')}")
        status, body = self.act("bill_create", {"vendor_id": self.contact("Blue Ridge Hosting", "vendor"), "number": "T-BILL-4",
                                                "bill_date": TODAY,
                                                "lines": [{"account_id": self.acc("425"), "amount": 1000, "description": "after"}]})
        self.settle(lambda: False, 90)
        res.check("after disconnect no write reaches the provider",
                  len(self.fake_writes()) == writes_before and self.fake_count("Invoices", "InvoiceNumber", "T-BILL-4") == 0,
                  f"action={body.get('status')} {str(body.get('reason'))[:120]}; provider writes "
                  f"before={writes_before} after={len(self.fake_writes())}")
        self.hq.realm = XERO_TENANT
        again = self.books_link.connect_claim(self.store, self.vault, self.link, provider="xero", company_id=COMPANY,
                                              claim_id="clm_rehearsal_xero_7", name="back", by=OWNER_ACTOR,
                                              now="2026-11-01T13:00:00.000000Z")
        # Spec 3.4 covers reconnecting while connected (the tokens are replaced, same connection). After a disconnect the
        # books record a new connection and keep the old one as disconnected; this check asserts one active connection.
        active = [(r.get("sync") or {}).get("connection_id") for r in self.books_rows()
                  if str((r.get("sync") or {}).get("status")) == "active"]
        res.check("after a disconnect, reconnecting leaves exactly one active books connection (the new one)",
                  active == [again], f"active={active} new={again} disconnected={self.cid}; rows={len(self.books_rows())}")
        self.settle(lambda: self.fake_count("Invoices", "InvoiceNumber", "T-BILL-4") >= 1 and self.writes_settled(), 300)
        copies = self.fake_count("Invoices", "InvoiceNumber", "T-BILL-4")
        res.check("after reconnect no write is duplicated at the provider", copies <= 1,
                  f"T-BILL-4 copies={copies} (0: refused while disconnected, 1: held and sent once); outbox={self.outbox()}")
        rows = self.fake_rows()
        want = months_of(rows, MONTH_SEPT, self.pre)
        want_bs = balances_of(rows)
        got = self.pnl_months()
        res.check("after reconnect the books equal the provider's rows (every month and the balance sheet)",
                  got == want and self.balances() == want_bs, f"got={ {m: fmt(got[m]) for m in BAL_MONTHS} } "
                                                             f"want={ {m: fmt(want[m]) for m in BAL_MONTHS} }")

    # -- helpers for the steps
    def kinds(self) -> list[str]:
        body = self.get("/finance/action-kinds")
        found: list[str] = []
        walk_kinds(body, found)
        return found

    def contact(self, name: str, kind: str) -> str:
        found = walk_for(self.get(f"/finance/contacts?kind={kind}&active=true"), "name", name)
        if found is None:
            found = walk_for(self.get(f"/finance/contacts?kind={kind}&active=false"), "name", name)
        if found is None:
            raise RuntimeError(f"no {kind} named {name} in Tico")
        return str(found.get("id"))

    def budget(self, name: str) -> dict[str, Any] | None:
        return walk_for(self.get(f"/finance/budgets?at={TODAY}"), "name", name)

    def tico_invoice_id(self, number: str) -> str:
        found = walk_for(self.get("/finance/invoices?limit=200"), "number", number)
        if found is None:
            raise RuntimeError(f"invoice {number} not in Tico")
        return str(found.get("id"))

    def tico_has_bill(self, number: str, amount: int, code: str) -> bool:
        found = walk_for(self.get("/finance/bills?limit=200"), "number", number)
        return found is not None and int(found.get("total", -1)) == amount

    def tico_has_invoice(self, number: str, amount: int, code: str) -> bool:
        found = walk_for(self.get("/finance/invoices?limit=200"), "number", number)
        return found is not None and int(found.get("total", -1)) == amount

    def fake_count(self, endpoint: str, field: str, value: str) -> int:
        return sum(1 for row in self.fake_rows()[endpoint] if str(row.get(field)) == value)

    def fake_has(self, endpoint: str, field: str, value: str, lines: list[tuple[str, int]] | None = None,
                 amount: int | None = None) -> bool:
        for row in self.fake_rows()[endpoint]:
            if str(row.get(field)) != value:
                continue
            if lines is not None:
                got = [(line["AccountID"], cents(line["LineAmount"])) for line in row["LineItems"]]
                return got == lines
            if amount is not None:
                return cents(row.get("Amount", 0)) == amount
            return True
        return False

    def fake_payment(self, invoice_id: str, amount: int) -> bool:
        return self.fake_payment_count(invoice_id, amount) == 1

    def fake_payment_count(self, invoice_id: str, amount: int) -> int:
        return sum(1 for p in self.fake_rows()["Payments"] if p.get("Invoice", {}).get("InvoiceID") == invoice_id
                   and cents(p["Amount"]) == amount and p.get("Status") == "AUTHORISED"
                   and p.get("Account", {}).get("AccountID") == BANK)

    def fake_journal(self, narration: str, lines: list[tuple[str, int]]) -> bool:
        for jrn in self.fake_rows()["ManualJournals"]:
            if jrn.get("Narration") == narration:
                got = sorted((line["AccountID"], cents(line["LineAmount"])) for line in jrn["JournalLines"])
                return got == sorted(lines)
        return False

    def wait_settled(self) -> None:
        self.settle(self.writes_settled, 240)

    def run(self) -> bool:
        self.start_fake()
        try:
            self.start_demo()
            for step in (self.step_connect, self.step_first_sync, self.step_changes, self.step_writes,
                         self.step_refusals, self.step_budgets, self.step_disconnect):
                try:
                    step()
                except Exception as exc:  # a step that cannot run is a failed check, and the later steps still run
                    self.res.check("the step ran to the end", False, f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            self.close()
        print(f"[xero] {'PASSED' if self.res.ok else 'FAILED'}: "
              f"{sum(r['ok'] for r in self.res.rows)}/{len(self.res.rows)} checks; skips: {self.res.skips}", flush=True)
        return self.res.ok


# ------------------------------------------------------------------------------------------------ small helpers
def walk_for(node: object, field: str, value: object) -> dict[str, Any] | None:
    """The first dict under `node` whose `field` holds `value`."""
    if isinstance(node, dict):
        if str(node.get(field)) == str(value):
            return node
        for child in node.values():
            found = walk_for(child, field, value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for child in node:
            found = walk_for(child, field, value)
            if found is not None:
                return found
    return None


def walk_for_key(node: object, key: str) -> object | None:
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for child in node.values():
            found = walk_for_key(child, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for child in node:
            found = walk_for_key(child, key)
            if found is not None:
                return found
    return None


def walk_kinds(node: object, out: list[str]) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("kind"), str):
            out.append(node["kind"])
        for child in node.values():
            walk_kinds(child, out)
    elif isinstance(node, list):
        for child in node:
            if isinstance(child, str):
                out.append(child)
            walk_kinds(child, out)


def any_text(node: object, needle: str) -> bool:
    """True when some string under `node` contains `needle`."""
    if isinstance(node, dict):
        return any(any_text(v, needle) for v in node.values())
    if isinstance(node, list):
        return any(any_text(v, needle) for v in node)
    return isinstance(node, str) and needle in node


def soft_of(docs: list[dict[str, Any]], month: str) -> int:
    """Software (425) bill lines in cents, for live bills dated in one month."""
    return sum(cents(line["LineAmount"]) for inv in docs if inv["Type"] == "ACCPAY" and live_doc(inv)
               and inv["DateString"][:7] == month for line in inv["LineItems"] if line["AccountID"] == "acc-exp-soft")


def bump_invoice(row: dict[str, Any], cents_more: int) -> None:
    line = row["LineItems"][0]
    line["LineAmount"] = f"{(cents(line['LineAmount']) + cents_more) / 100:.2f}"
    row["Total"] = f"{sum(cents(x['LineAmount']) for x in row['LineItems']) / 100:.2f}"


def set_bill_amount(row: dict[str, Any], cents_total: int) -> None:
    line = row["LineItems"][0]
    line["LineAmount"] = f"{cents_total / 100:.2f}"
    row["Total"] = f"{sum(cents(x['LineAmount']) for x in row['LineItems']) / 100:.2f}"


def webhook_envelope(external_id: str, events: list[tuple[str, str, str]]) -> dict[str, Any]:
    """A Xero webhook as HQ relays it: an `events` list, one event per changed record."""
    out = []
    for category, ident, kind in events:
        out.append({"resourceUrl": f"https://api.xero.com/api.xro/2.0/{category}/{ident}", "resourceId": ident,
                    "eventDateUtc": "2026-11-01T12:00:00.000Z", "eventType": kind,
                    "eventCategory": "INVOICE" if category == "Invoices" else category.upper(), "tenantId": XERO_TENANT,
                    "tenantType": "ORGANISATION"})
    return {"type": "tico.finance.webhook", "provider": "xero", "external_id": external_id, "company_id": COMPANY,
            "payload": {"events": out, "firstEventSequence": 1, "lastEventSequence": len(out), "entropy": "rehearsal"}}


def run_child(keep: bool) -> int:
    os.environ.setdefault("TICO_UPDATE_CHECK", "off")
    os.environ["TICO_XERO_BASE"] = f"http://127.0.0.1:{FAKE_PORT}/api.xro/2.0"
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from backend import demo
    rehearsal = XeroRehearsal(keep)
    with demo.network_blocked():
        try:
            ok = rehearsal.run()
        except Exception as exc:
            rehearsal.res.check("the rehearsal ran to the end", False, f"{type(exc).__name__}: {exc}"[:400])
            rehearsal.close()
            ok = False
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--keep", action="store_true", help="keep the demo's data folder")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        return run_child(args.keep)
    command = [sys.executable, str(Path(__file__).resolve()), "--child"] + (["--keep"] if args.keep else [])
    code = subprocess.call(command)
    print(f"== xero: {'passed' if code == 0 else 'failed'} (exit {code})", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
