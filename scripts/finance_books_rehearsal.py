#!/usr/bin/env python3
"""Bring your own books, rehearsed end to end on this computer: a Tico demo (Acme) in a new data folder, the real books
worker (backend/finance/books_sync.py) and the real mirror (backend/finance/mirror.py), against a fake QuickBooks Online
(scripts/fake_quickbooks.py) or a fake Xero (scripts/fake_xero.py). Nothing here reaches the network: the demo refuses
every non-loopback connection while it runs (backend/demo.py network_blocked), and HQ is answered by a fake.

This is a REHEARSAL. It finds what would break on a real run. It is not the proof: no real QuickBooks or Xero company,
no HQ OAuth consent, no HQ token exchange, no Gateway, and no real Mercury are involved.

    python3 scripts/finance_books_rehearsal.py                    # both providers, one after the other
    python3 scripts/finance_books_rehearsal.py --provider quickbooks
    python3 scripts/finance_books_rehearsal.py --provider xero --keep    # keep the demo's data folder

Ports: demo 18821, fake QuickBooks 18822, fake Xero 18823. Each provider runs in its own child process with its own new
data folder (deleted at the end unless --keep). Exit 0 only when every check passed. Each provider's child stops its own
fake and demo by PID when it ends.

What runs for real: the demo app (create_app), its HTTP API for every Tico-side action and read, the books worker
(books_sync.start / pass_once / delivery), mirror.sync_once, the QuickBooks and Xero clients, and the fakes over HTTP.

What is replaced, and where (each is listed again in the report):
  - HQ OAuth broker: books_link._call is answered by an in-process fake HQ. It issues the tokens, refreshes them and
    takes the grant off. The consent page and the browser return are not run. The demo refuses the HTTP connect route
    (company_server.off), so connect/return/disconnect are called in-process through books_link.start / finish / drop.
  - Gateway key: hook_relay.link_of returns a fixed Link, so the worker has a key to hand to the broker fake.
  - Webhooks: a relayed QuickBooks or Xero envelope is handed to books_sync.delivery (the call triggers.py makes after
    the trigger's signature check); the HQ relay itself is not run.
  - Page size: mirror.sync_once's backfill size is lowered (keyword default) so backfill spans pages with ~40 records.
  - Direct DB reads or writes: none. Every check reads the API.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
DEMO_PORT, QBO_PORT, XERO_PORT = 18821, 18822, 18823
COMPANY = "5a0c7e21-9d4b-4f3e-8b2a-6e1d0c9f7a42"          # a fictional company id (HQ's)
OWNER_ACTOR = "human:ana"
QBO_REALM, QBO_OTHER_REALM = "1234567", "7654321"
XERO_TENANT, XERO_OTHER_TENANT = "acme-tenant", "other-tenant"
# The whole book, through the open periods (a late edit to a closed month posts in the current open period, §4.1).
WINDOW = ("2026-09-01", "2027-12-31")
SEPT = ("2026-09-01", "2026-09-30")
DAY = "2026-10-30"                                   # the day QuickBooks writes in steps 4 to 7 (October is open)
RETRY_REHEARSAL = (1, 2, 4, 8)                       # mirror.RETRY for this rehearsal (production: 60 s, 5 min, ...)
# Steps 4 to 7, in cents. Software is the 21 account in QuickBooks; Office supplies is 24.
B1, I1, B2, J1, J2 = 150000, 200000, 2500, 5000, 1000
BUDGET, B3, B4, B5 = 1000000, 400000, 500000, 1000
SOFTWARE_OCT_BEFORE = 12000 + B1 + B2                 # bill 106 (Oct, Software) + the step 4 bills
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Results:
    """Every check, in order. A check that fails does not stop the run; the exit status says whether all passed."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.rows: list[dict[str, Any]] = []
        self.skips: list[str] = []
        self.step = "setup"

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append({"step": self.step, "name": name, "ok": bool(ok), "detail": detail})
        line = f"[{self.provider}] {'PASS' if ok else 'FAIL'} {self.step}: {name}"
        print(line + (f" -- {detail}" if detail else ""), flush=True)
        return bool(ok)

    def skip(self, what: str) -> None:
        self.skips.append(f"{self.step}: {what}")
        print(f"[{self.provider}] SKIP {self.step}: {what}", flush=True)

    @property
    def ok(self) -> bool:
        return bool(self.rows) and all(r["ok"] for r in self.rows)


def http(method: str, url: str, body: object = None, token: str = "", key: str = "") -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if method != "GET":
        headers["Idempotency-Key"] = key or str(uuid.uuid4())
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with NO_PROXY.open(request, timeout=30) as reply:
            raw = reply.read()
            return reply.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw[:400]


def wait_until(predicate: Callable[[], bool], seconds: float, every: float = 0.5) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(every)
    return predicate()


def cents(value: float) -> int:
    return int(round(value * 100))


def rows_of(answer: Any, key: str = "") -> list[dict[str, Any]]:
    """The records in a read's answer: the list under `key`, else the first list of objects it holds."""
    if isinstance(answer, dict):
        if key and isinstance(answer.get(key), list):
            return [r for r in answer[key] if isinstance(r, dict)]
        for value in answer.values():
            if isinstance(value, list) and (not value or isinstance(value[0], dict)):
                return [r for r in value if isinstance(r, dict)]
    if isinstance(answer, list):
        return [r for r in answer if isinstance(r, dict)]
    return []


def moved(before: dict[str, int], deltas: dict[str, int]) -> dict[str, int]:
    """The books' totals after the deltas, every total in minor units."""
    return {k: before[k] + deltas.get(k, 0) for k in before}


def qbo_writes_delta() -> dict[str, int]:
    """Steps 4: the bill paid in full (B1), the invoice paid in full (I1), the unpaid bill (B2) and two journals (J1, J2)."""
    expense = B1 + B2 + J1 + J2
    income = I1
    return {"income": income, "expense": expense, "net": income - expense,
            "bank": -B1 + I1 - J1 - J2, "ar": I1 - I1, "ap": B1 + B2 - B1}


def qbo_budget_delta() -> dict[str, int]:
    """Step 6: two bills the budget holds and then shows as actuals (B3 and B4), both unpaid."""
    return {"expense": B3 + B4, "net": -(B3 + B4), "ap": B3 + B4}


# ------------------------------------------------------------------------------------------------ the fake HQ
class FakeHQ:
    """HQ's books broker in place of the network. Called as books_link._call(link, path, body)."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.refreshes = 0
        self.issued = 0
        self.realm = QBO_REALM if provider == "quickbooks" else XERO_TENANT
        self.refuse_next: list[Exception] = []

    def tokens(self, realm: str | None = None) -> dict[str, Any]:
        self.issued += 1
        expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: dict[str, Any] = {"access_token": f"acc-{self.issued}", "refresh_token": f"ref-{self.issued}",
                               "expires_at": expires, "refresh_expires_at": None, "realm_id": None, "tenant_id": None}
        if self.provider == "quickbooks":
            out["realm_id"] = realm or QBO_REALM
        else:
            out["tenant_id"] = realm or XERO_TENANT
        return out

    def __call__(self, link: object, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((path, dict(body)))
        if self.refuse_next:
            raise self.refuse_next.pop(0)
        verb = path.rsplit("/", 1)[1]
        if verb == "connect":
            return {"url": "https://consent.example.invalid/rehearsal", "expires_in": 900}
        if verb == "claim":
            return self.tokens(self.realm)
        if verb == "refresh":
            self.refreshes += 1
            return self.tokens(self.realm)
        if verb in ("disconnect", "revoke"):
            return {"ok": True, "revoked": True}
        raise RuntimeError("the fake HQ has no answer for " + path)


# ------------------------------------------------------------------------------------------------ the seed
ACCOUNTS = [  # id, name, QuickBooks AccountType, AcctNum
    ("10", "Checking", "Bank", "1010"), ("11", "Savings", "Bank", "1020"),
    ("12", "Accounts Receivable", "Accounts Receivable", "1210"), ("13", "Accounts Payable", "Accounts Payable", "2010"),
    ("14", "Credit Card", "Credit Card", "2110"), ("15", "Undeposited Funds", "Other Current Asset", "1150"),
    ("16", "Owner Equity", "Equity", "3010"), ("17", "Retained Earnings", "Equity", "3110"),
    ("18", "Consulting income", "Income", "4010"), ("19", "Installation income", "Income", "4110"),
    ("20", "Interest income", "Other Income", "4910"), ("21", "Software", "Expense", "6120"),
    ("22", "Rent", "Expense", "6210"), ("23", "Contractors", "Expense", "6310"), ("24", "Office supplies", "Expense", "6410"),
    ("25", "Travel", "Expense", "6510"), ("26", "Meals", "Expense", "6610"), ("27", "Insurance", "Expense", "6710"),
    ("28", "Bank fees", "Expense", "6810"), ("29", "Utilities", "Expense", "6920"), ("30", "Advertising", "Expense", "6930"),
    ("31", "Equipment", "Fixed Asset", "1510"), ("32", "Cost of goods sold", "Cost of Goods Sold", "5510"),
    ("33", "Loan payable", "Long Term Liability", "2510"), ("34", "Sales tax payable", "Other Current Liability", "2210"),
    ("35", "Depreciation", "Expense", "6990"), ("36", "Payroll expenses", "Expense", "6400"),
    ("37", "Other income", "Other Income", "4990"),
]
BILLS = [  # id, date, vendor, number, [(expense account, cents)], voided
    ("101", "2026-09-03", "60", "B-1001", [("21", 12000)], False),
    ("102", "2026-09-08", "61", "B-1002", [("22", 150000), ("24", 2500)], False),
    ("103", "2026-09-14", "63", "B-1003", [("23", 80000)], False),
    ("104", "2026-09-21", "62", "B-1004", [("27", 45000)], False),
    ("105", "2026-09-28", "60", "B-1005", [("24", 3300), ("25", 8900)], False),
    ("106", "2026-10-02", "61", "B-1006", [("21", 12000)], False),
    ("107", "2026-10-06", "63", "B-1007", [("23", 125000)], False),
    ("108", "2026-10-09", "60", "B-1008", [("26", 4150)], True),          # voided in QuickBooks
    ("109", "2026-10-13", "61", "B-1009", [("28", 2750), ("29", 19900)], False),
    ("110", "2026-10-19", "62", "B-1010", [("30", 60000)], False),
    ("111", "2026-10-23", "63", "B-1011", [("23", 45000)], False),
    ("112", "2026-10-29", "60", "B-1012", [("24", 7720)], False),
]
BILL_PAYMENTS = [  # id, date, vendor, [(bill id, cents)]
    ("301", "2026-09-18", "60", [("101", 12000)]),
    ("302", "2026-09-25", "61", [("102", 152500)]),
    ("303", "2026-10-05", "63", [("103", 80000)]),
    ("304", "2026-10-16", "61", [("106", 12000)]),
    ("305", "2026-10-27", "63", [("107", 125000)]),
]
INVOICES = [  # id, date, customer, number, [(item, cents)]
    ("201", "2026-09-05", "50", "I-2001", [("40", 250000)]),
    ("202", "2026-09-12", "52", "I-2002", [("41", 180000), ("40", 20000)]),
    ("203", "2026-09-19", "51", "I-2003", [("40", 95000)]),
    ("204", "2026-09-26", "50", "I-2004", [("41", 60000)]),
    ("205", "2026-10-03", "52", "I-2005", [("40", 310000)]),
    ("206", "2026-10-10", "50", "I-2006", [("41", 75000)]),
    ("207", "2026-10-17", "51", "I-2007", [("40", 140000)]),
    ("208", "2026-10-24", "52", "I-2008", [("41", 99900)]),
]
ITEMS = {"40": ("Consulting", "18"), "41": ("Installation", "19")}   # item id -> (name, income account id)
PAYMENTS = [  # id, date, customer, deposit account, [(invoice id, cents)]
    ("401", "2026-09-30", "50", "10", [("201", 250000)]),
    ("402", "2026-10-08", "52", "10", [("202", 200000)]),
    ("403", "2026-10-20", "51", "10", [("203", 95000)]),
    ("404", "2026-10-28", "52", "10", [("205", 155000)]),
]
JOURNALS = [  # id, date, memo, [(account, signed cents: debit +)], optional DepartmentRef
    ("701", "2026-09-01", "Opening balance", [("10", 5000000), ("16", -5000000)], ""),
    ("702", "2026-09-15", "Insurance premium", [("27", 30000), ("10", -30000)], "20"),
    ("703", "2026-09-30", "Bank service charge", [("28", 1500), ("10", -1500)], ""),
    ("704", "2026-10-15", "October rent by transfer", [("22", 20000), ("10", -20000)], "21"),
    ("705", "2026-10-21", "Interest received", [("10", 8000), ("20", -8000)], ""),
    ("706", "2026-10-26", "Owner contribution", [("10", 100000), ("16", -100000)], ""),
]
VENDORS = [("60", "Acme Supplies", True), ("61", "Blue Ridge Hosting", True), ("62", "Summit Legal", False),
           ("63", "Pine Design Co", True)]                            # id, name, active (62 is archived)
CUSTOMERS = [("50", "Acme Retail", None), ("51", "Job 12", "50"), ("52", "Northwind Traders", None)]
DEPARTMENTS = [("20", "Denver"), ("21", "Austin")]
CLASSES = [("30", "Web"), ("31", "Ops")]


def qbo_money(units: int) -> float:
    return round(units / 100, 2)


def qbo_seed() -> dict[str, list[dict[str, Any]]]:
    """QuickBooks records as its query answers them (amounts in dollars, as QuickBooks sends them)."""
    seed: dict[str, list[dict[str, Any]]] = {"Account": [], "Vendor": [], "Customer": [], "Item": [],
                                             "Department": [], "Class": [], "Bill": [], "BillPayment": [],
                                             "Invoice": [], "Payment": [], "JournalEntry": []}
    for aid, name, kind, num in ACCOUNTS:
        seed["Account"].append({"Id": aid, "SyncToken": "0", "Name": name, "AccountType": kind, "AcctNum": num,
                                "Active": True})
    for vid, name, active in VENDORS:
        seed["Vendor"].append({"Id": vid, "SyncToken": "0", "DisplayName": name, "Active": active})
    for cid, name, parent in CUSTOMERS:
        row: dict[str, Any] = {"Id": cid, "SyncToken": "0", "DisplayName": name, "Active": True, "Job": bool(parent)}
        if parent:
            row["ParentRef"] = {"value": parent}
        seed["Customer"].append(row)
    for iid, (name, income) in ITEMS.items():
        seed["Item"].append({"Id": iid, "SyncToken": "0", "Name": name, "Type": "Service", "Active": True,
                             "IncomeAccountRef": {"value": income, "name": ""}})
    for did, name in DEPARTMENTS:
        seed["Department"].append({"Id": did, "SyncToken": "0", "Name": name, "Active": True})
    for kid, name in CLASSES:
        seed["Class"].append({"Id": kid, "SyncToken": "0", "Name": name, "Active": True})
    for bid, day, vendor, number, lines, voided in BILLS:
        rows = [{"Id": f"{bid}-{n}", "DetailType": "AccountBasedExpenseLineDetail", "Amount": qbo_money(c),
                 "Description": f"line {n}", "AccountBasedExpenseLineDetail": {"AccountRef": {"value": a}}}
                for n, (a, c) in enumerate(lines, 1)]
        total = sum(c for _, c in lines)
        row = {"Id": bid, "SyncToken": "0", "TxnDate": day, "DueDate": day, "DocNumber": number,
               "VendorRef": {"value": vendor}, "APAccountRef": {"value": "13"}, "Line": rows,
               "TotalAmt": 0 if voided else qbo_money(total), "CurrencyRef": {"value": "USD"}}
        if voided:
            for r in rows:
                r["Amount"] = 0
            row["PrivateNote"] = "Voided"
        seed["Bill"].append(row)
    for pid, day, vendor, applied in BILL_PAYMENTS:
        total = sum(c for _, c in applied)
        seed["BillPayment"].append({
            "Id": pid, "SyncToken": "0", "TxnDate": day, "VendorRef": {"value": vendor}, "TotalAmt": qbo_money(total),
            "PayType": "Check", "CheckPayment": {"BankAccountRef": {"value": "10"}},
            "Line": [{"Amount": qbo_money(c), "LinkedTxn": [{"TxnId": b, "TxnType": "Bill"}]} for b, c in applied]})
    for iid, day, customer, number, lines in INVOICES:
        total = sum(c for _, c in lines)
        seed["Invoice"].append({
            "Id": iid, "SyncToken": "0", "TxnDate": day, "DueDate": day, "DocNumber": number,
            "CustomerRef": {"value": customer}, "ARAccountRef": {"value": "12"},
            "Line": [{"Id": f"{iid}-{n}", "DetailType": "SalesItemLineDetail", "Amount": qbo_money(c),
                      "Description": "sale", "SalesItemLineDetail": {"ItemRef": {"value": i}, "Qty": 1,
                                                                     "UnitPrice": qbo_money(c)}}
                     for n, (i, c) in enumerate(lines, 1)],
            "TotalAmt": qbo_money(total), "CurrencyRef": {"value": "USD"}})
    for pid, day, customer, deposit, applied in PAYMENTS:
        total = sum(c for _, c in applied)
        seed["Payment"].append({
            "Id": pid, "SyncToken": "0", "TxnDate": day, "CustomerRef": {"value": customer},
            "DepositToAccountRef": {"value": deposit}, "TotalAmt": qbo_money(total),
            "Line": [{"Amount": qbo_money(c), "LinkedTxn": [{"TxnId": i, "TxnType": "Invoice"}]} for i, c in applied]})
    for jid, day, memo, lines, department in JOURNALS:
        rows = []
        for n, (account, signed) in enumerate(lines, 1):
            detail: dict[str, Any] = {"PostingType": "Debit" if signed > 0 else "Credit",
                                      "AccountRef": {"value": account}}
            if department and account in ("27", "22"):
                detail["DepartmentRef"] = {"value": department}
                detail["ClassRef"] = {"value": "30"}
            rows.append({"Id": f"{jid}-{n}", "DetailType": "JournalEntryLineDetail", "Amount": qbo_money(abs(signed)),
                         "Description": memo, "JournalEntryLineDetail": detail})
        seed["JournalEntry"].append({"Id": jid, "SyncToken": "0", "TxnDate": day, "PrivateNote": memo, "Line": rows})
    return seed


def qbo_expected() -> dict[str, int]:
    """What the books must say for the seed, in minor units, worked out from the seed lists alone."""
    income = expense = 0
    bank = ar = ap = 0
    for _, day, _, _, lines, voided in BILLS:
        if not voided:
            expense += sum(c for _, c in lines)
            ap += sum(c for _, c in lines)
    for _, _, _, applied in BILL_PAYMENTS:
        ap -= sum(c for _, c in applied)
        bank -= sum(c for _, c in applied)
    for _, _, _, _, lines in INVOICES:
        income += sum(c for _, c in lines)
        ar += sum(c for _, c in lines)
    for _, _, _, deposit, applied in PAYMENTS:
        ar -= sum(c for _, c in applied)
        bank += sum(c for _, c in applied)
    for _, _, _, lines, _ in JOURNALS:
        for account, signed in lines:
            if account == "10":
                bank += signed
            if account in ("27", "28", "22"):
                expense += signed
            if account == "20":
                income += -signed
    return {"income": income, "expense": expense, "net": income - expense, "bank": bank, "ar": ar, "ap": ap}


def qbo_bill_record(bid: str, lines: list[tuple[str, int]], voided: bool = False) -> dict[str, Any]:
    """A bill as QuickBooks sends it after a change: revision 1 with the lines it now has (none when voided)."""
    _, day, vendor, number, _, _ = next(b for b in BILLS if b[0] == bid)
    rows = [{"Id": f"{bid}-{n}", "DetailType": "AccountBasedExpenseLineDetail", "Amount": qbo_money(c),
             "Description": f"line {n}", "AccountBasedExpenseLineDetail": {"AccountRef": {"value": a}}}
            for n, (a, c) in enumerate(lines, 1)]
    row: dict[str, Any] = {"Id": bid, "SyncToken": "1", "TxnDate": day, "DueDate": day, "DocNumber": number,
                           "VendorRef": {"value": vendor}, "APAccountRef": {"value": "13"}, "Line": rows,
                           "TotalAmt": qbo_money(sum(c for _, c in lines)), "CurrencyRef": {"value": "USD"}}
    if voided:
        row["PrivateNote"] = "Voided"
    return row


def qbo_invoice_record(iid: str, lines: list[tuple[str, int]]) -> dict[str, Any]:
    _, day, customer, number, _ = next(i for i in INVOICES if i[0] == iid)
    return {"Id": iid, "SyncToken": "1", "TxnDate": day, "DueDate": day, "DocNumber": number,
            "CustomerRef": {"value": customer}, "ARAccountRef": {"value": "12"},
            "Line": [{"Id": f"{iid}-{n}", "DetailType": "SalesItemLineDetail", "Amount": qbo_money(c),
                      "Description": "sale", "SalesItemLineDetail": {"ItemRef": {"value": i}, "Qty": 1,
                                                                     "UnitPrice": qbo_money(c)}}
                     for n, (i, c) in enumerate(lines, 1)],
            "TotalAmt": qbo_money(sum(c for _, c in lines)), "CurrencyRef": {"value": "USD"}}


def qbo_expected_after_changes(before: dict[str, int]) -> dict[str, int]:
    """The totals after the step-3 changes, as deltas on what the books said before them:
    bill 101 120.00 -> 150.00 (expense and AP +30.00); bill 105 voided (expense and AP -122.00); bill 112 deleted
    (expense and AP -77.20); invoice 203 950.00 -> 1200.00 (income and AR +250.00); journal 706 deleted (bank -1000.00)."""
    expense_delta = 3000 - 12200 - 7720
    deltas = {"income": 25000, "expense": expense_delta, "net": 25000 - expense_delta, "bank": -100000,
              "ar": 25000, "ap": expense_delta}
    return {k: before[k] + deltas[k] for k in deltas}


# ------------------------------------------------------------------------------------------------ one provider
class Rehearsal:
    def __init__(self, provider: str, keep: bool) -> None:
        self.provider = provider
        self.keep = keep
        self.res = Results(provider)
        self.data = Path(tempfile.mkdtemp(prefix=f"tico-books-rehearsal-{provider}-"))
        self.fake_proc: subprocess.Popen | None = None
        self.server: Any = None
        self.server_thread: threading.Thread | None = None
        self.hq = FakeHQ(provider)
        self.base = f"http://127.0.0.1:{DEMO_PORT}/api/v2"
        self.fake = f"http://127.0.0.1:{QBO_PORT if provider == 'quickbooks' else XERO_PORT}"
        self.token = ""
        self.cid = ""
        self.app: Any = None
        self.settings: Any = None

    # -- the fake provider, started and stopped by PID
    def start_fake(self) -> None:
        script = ROOT / ("scripts/fake_quickbooks.py" if self.provider == "quickbooks" else "scripts/fake_xero.py")
        port = QBO_PORT if self.provider == "quickbooks" else XERO_PORT
        args = [sys.executable, str(script), "--port", str(port)]
        if self.provider == "xero":
            args += ["--tenant", XERO_TENANT]
        self.fake_proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"[{self.provider}] fake {self.provider} pid {self.fake_proc.pid} on {self.fake}", flush=True)
        if not wait_until(lambda: self.fake_up(), 15):
            raise RuntimeError(f"the fake {self.provider} did not start")

    def fake_up(self) -> bool:
        try:
            http("GET", self.fake + "/_fake/calls")
            return True
        except OSError:
            return False

    def fake_call(self, path: str, body: object = None) -> Any:
        status, answer = http("POST" if body is not None else "GET", self.fake + path, body)
        if status != 200:
            raise RuntimeError(f"fake {path}: {status} {answer}")
        return answer

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
        from backend.finance import books_link, mirror
        self.hook_relay, self.books_link = hook_relay, books_link
        # Rehearsal-only page sizes, so the ~40 seeded records span pages. Production values are unchanged elsewhere.
        mirror.sync_once.__kwdefaults__["size"] = 7          # Tico's backfill rows per pass (default 1000)
        if self.provider == "quickbooks":
            from backend.finance import qbo as qbo_client
            qbo_client.PAGE = 12                              # QuickBooks query page (default 1000)
            mirror.RETRY = RETRY_REHEARSAL                    # a lost answer is retried in seconds, not minutes
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
        token_file = self.data / "local-owner.token"
        self.token = token_file.read_text().strip()
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

    def books_rows(self) -> list[dict[str, Any]]:
        groups = self.get("/finance/sources")["groups"]
        return [r for g in groups if g["group"] == "accounting" for r in g["rows"]]

    def accounting_row(self) -> dict[str, Any]:
        rows = self.books_rows()
        return rows[0] if rows else {}

    def ring(self) -> None:
        from backend.finance import books_sync
        books_sync.ring()

    def settle(self, predicate: Callable[[], bool], seconds: float = 120) -> bool:
        """Wakes the books worker, and wakes it again every few seconds, until a predicate about the books holds. A write
        due for a retry is only sent on the worker's pass, so one ring is not enough to wait out a retry."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.ring()
            if wait_until(predicate, 5, every=1.0):
                return True
        return predicate()

    def close(self) -> None:
        self.stop_demo()
        self.stop_fake()
        if not self.keep:
            shutil.rmtree(self.data, ignore_errors=True)

    # -- the steps
    def step_connect(self) -> None:
        from backend.finance import books_link
        res = self.res
        res.step = "1 connect"
        status, body = http("POST", self.base + "/finance/books/connect", {"provider": self.provider},
                            token=self.token)
        res.check("the HTTP connect route refuses a demo copy (company_server.off), as designed",
                  status == 409, f"HTTP {status}")
        res.skip("HTTP consent: the browser and HQ's consent page, and the return route, are not run")
        flow_start = len(self.hq.calls)
        books_link.start(self.store, self.link, provider=self.provider, company_id=COMPANY, user=OWNER_ACTOR,
                         return_base=self.settings.public_url + "/api/v2/finance/books/return")
        return_to = str(self.hq.calls[flow_start][1]["return_to"])
        flow = return_to.rsplit("/", 1)[1]
        self.hq.realm = QBO_REALM if self.provider == "quickbooks" else XERO_TENANT
        self.cid = books_link.finish(self.store, self.vault, self.link, flow=flow, user=OWNER_ACTOR,
                                     outcome="connected", reason="", claim_id="clm_rehearsal_1")
        row = self.accounting_row()
        sync = row.get("sync", {})
        res.check("the connection is recorded and active", bool(self.cid) and sync.get("status") == "active"
                  and row.get("provider") == self.provider, f"status={sync.get('status')}")
        res.check("exactly one books connection on this install", len(self.books_rows()) == 1)
        res.check("the sources page never shows a token", "acc-" not in json.dumps(self.get("/finance/sources")))
        # One books org per install: a second organisation is refused (HQ answers already_connected).
        self.hq.refuse_next.append(books_link.AlreadyConnected(None))
        refused = False
        try:
            books_link.connect_claim(self.store, self.vault, self.link, provider=self.provider,
                                     company_id=COMPANY, claim_id="clm_rehearsal_2", name="second",
                                     by=OWNER_ACTOR, now="2026-11-01T12:00:00.000000Z")
        except books_link.AlreadyConnected:
            refused = True
        res.check("a second organisation on the same install is refused (already_connected)", refused)
        res.check("the refused organisation makes no second connection", len(self.books_rows()) == 1)
        # Reconnecting the same organisation repairs its connection; it does not make another.
        same_cid = books_link.connect_claim(self.store, self.vault, self.link, provider=self.provider,
                                            company_id=COMPANY, claim_id="clm_rehearsal_3", name="again",
                                            by=OWNER_ACTOR, now="2026-11-01T12:01:00.000000Z")
        res.check("reconnecting the same organisation keeps the same connection", same_cid == self.cid)

    def step_first_sync(self) -> None:
        from backend.finance import books_sync, mirror
        res = self.res
        res.step = "2 first sync"
        self.seed_provider()
        # The Tico side switches to the provider's books (a preview first, then the switch).
        status, dry = self.act("mirror_start", {"connection_id": self.cid}, dry_run=True)
        res.check("the mirror switch previews", status == 200 and dry.get("status") == "committed",
                  f"HTTP {status} impact={dry.get('impact')}")
        status, live = self.act("mirror_start", {"connection_id": self.cid})
        res.check("the mirror switch is committed", status == 200 and live.get("status") == "committed",
                  f"HTTP {status} {live.get('code') or live.get('reason') or ''}")
        books_sync.start(self.store, self.vault, self.settings, rehearsal=False)
        expected = qbo_expected()

        def totals_match() -> bool:
            return self.totals() == expected
        done = self.settle(totals_match, 150)
        res.check("the backfill finishes and the P&L and balance sheet match the seeded totals to the cent",
                  done, f"want {expected} got {self.totals()}")
        bs = self.get("/finance/balance-sheet?as_of=%s" % WINDOW[1])
        sides = {k: sum(int(r["balance"]) for r in bs[k]) for k in ("assets", "liabilities", "equity")}
        res.check("the balance sheet balances (assets = liabilities + equity)",
                  sides["assets"] == sides["liabilities"] + sides["equity"], str(sides))
        pages = self.page_reads()
        res.check("the backfill read more than one page of at least one entity",
                  any(n > 1 for n in pages.values()), f"pages read: {pages}")
        row = self.accounting_row()
        res.check("the sync reports a successful read", bool(row.get("sync", {}).get("last_ok_at")),
                  str(row.get("sync", {}).get("last_error")))
        # QuickBooks sends the voided bill at zero (TotalAmt 0, PrivateNote Voided). Tico has no void state for a bill
        # the provider zeroed: _doc_state labels it "paid" (paid 0 >= total 0). The totals above prove it adds nothing.
        listed = json.dumps(self.get("/finance/bills?limit=200"))
        at = listed.find("B-1008")
        res.check("the voided bill (B-1008) is in the books and adds nothing to P&L or AP (totals above)", at >= 0,
                  listed[max(0, at - 20):at + 260] if at >= 0 else "not in the bills list")
        archived = json.dumps(self.get("/finance/contacts?kind=vendor&active=false"))
        res.check("the archived vendor comes in inactive (Summit Legal)", "Summit Legal" in archived)
        if self.provider == "quickbooks":
            names = Counter(str(r.get("Name", "")).strip().lower() for r in self.fake_rows("Account"))
            clashes = sorted(n for n, k in names.items() if k > 1 and n)
            res.check("the switch adds no account beside the provider's own with the same name (§10: their chart is "
                      "the source of truth)", not clashes,
                      f"{len(self.fake_rows('Account'))} accounts in QuickBooks; names held twice: {clashes}; "
                      f"the preview said impact={dry.get('impact')}")
        res.skip("a record deleted before the first sync: the query feed never lists it, so there is nothing to see "
                 "here; deletion is checked in step 3 through the change feed")
        del mirror

    def step_changes(self) -> None:
        """Provider edits, a void and deletes arrive by webhook and the change feed; Tico restates. A closed month takes
        the restatement and flags it on its close snapshot."""
        res = self.res
        res.step = "3 changes"
        expected_before = self.totals()
        sept_before = self.month_pnl(SEPT)
        status, body = self.act("close_period", {"month": "2026-09"})
        res.check("September closes in Tico before the provider edits it", status == 200,
                  f"HTTP {status} {str(body)[:200]}")
        self.fake_call("/_fake/change", {
            "Bill": [qbo_bill_record("101", [("21", 15000)]),                 # edited: 120.00 -> 150.00 (Sept)
                     qbo_bill_record("105", [], voided=True)],               # voided (Sept)
            "Invoice": [qbo_invoice_record("203", [("40", 120000)])],        # edited: 950.00 -> 1200.00 (Sept)
            "JournalEntry": [],
        })
        self.fake_call("/_fake/change", {"Bill": [{"Id": "112", "status": "Deleted"}],   # deleted (Oct)
                                         "JournalEntry": [{"Id": "706", "status": "Deleted"}]})
        entities = [{"name": "Bill", "id": "101", "operation": "Update"},
                    {"name": "Bill", "id": "105", "operation": "Update"},
                    {"name": "Bill", "id": "112", "operation": "Delete"},
                    {"name": "Invoice", "id": "203", "operation": "Update"},
                    {"name": "JournalEntry", "id": "706", "operation": "Delete"}]
        notice = {"realmId": QBO_REALM, "dataChangeEvent": {"entities": entities}}
        envelope = {"type": "tico.finance.webhook", "provider": "quickbooks", "external_id": "evt-rehearsal-1",
                    "company_id": COMPANY, "payload": {"eventNotifications": [notice]}}
        from backend.finance import books_sync as BS
        with self.store.transaction() as c:
            kept = BS.delivery(c, "intuit", envelope)
        res.check("a relayed QuickBooks webhook is kept for the worker", kept == "kept", kept)
        with self.store.transaction() as c:
            again = BS.delivery(c, "intuit", envelope)
        res.check("the same webhook event twice is kept once", again == "seen", again)
        expected = qbo_expected_after_changes(expected_before)
        done = self.settle(lambda: self.totals() == expected, 150)
        cdc = [c for c in self.fake_call("/_fake/calls")["calls"] if c["path"].endswith("/cdc")]
        print(f"[{self.provider}] cdc entities read: {[c['query'].get('entities') for c in cdc]}", flush=True)
        res.check("the provider's edit, void and deletes land in Tico and the P&L and balance sheet match to the cent",
                  done, f"want {expected} got {self.totals()}; cdc reads={len(cdc)}; "
                        f"sync={self.accounting_row().get('sync')}; inbox={json.dumps(self.get('/finance/inbox'))[:700]}")
        # §10: their edit inside a closed month is accepted and flagged on that month's close snapshot. The P&L of a closed
        # month reads its snapshot, and carries the marker as snapshot.restatements.
        sept = self.get("/finance/pnl?start=%s&end=%s&period=month&basis=accrual" % SEPT)
        marker = sept.get("snapshot") or {}
        res.check("the closed month takes the restatement and flags it on its close snapshot (§10)",
                  int(marker.get("restatements") or 0) > 0, json.dumps(marker)[:200])
        res.check("September's own P&L is unchanged: the late edits post in the open period (§4.1)",
                  self.month_pnl(SEPT) == sept_before, f"before={sept_before} after={self.month_pnl(SEPT)}")
        bill_rows = rows_of(self.get("/finance/bills?limit=200"), "bills")
        b101 = next((r for r in bill_rows if r.get("number") == "B-1001"), {})
        res.check("the edited bill B-1001 is 150.00 with 120.00 paid, so 30.00 open",
                  (b101.get("total"), b101.get("paid"), b101.get("status")) == (15000, 12000, "open"),
                  f"total={b101.get('total')} paid={b101.get('paid')} status={b101.get('status')}")
        voids = {r.get("number") for r in rows_of(self.get("/finance/bills?status=void&limit=200"), "bills")}
        res.check("the voided bills stay void: B-1005 (voided in this step) and B-1008 (voided in the seed)",
                  {"B-1005", "B-1008"} <= voids, f"void bills: {sorted(v for v in voids if v)}")

    def month_pnl(self, window: tuple[str, str]) -> tuple[int, int]:
        pnl = self.get("/finance/pnl?start=%s&end=%s&period=month&basis=accrual" % window)
        income = sum(pnl["income"]) if isinstance(pnl["income"], list) else int(pnl["income"])
        expense = sum(pnl["expense"]) if isinstance(pnl["expense"], list) else int(pnl["expense"])
        return income, expense

    def seed_provider(self) -> None:
        self.fake_call("/_fake/seed", self.seed_body())

    def seed_body(self) -> dict[str, Any]:
        return qbo_seed()

    def totals(self) -> dict[str, int]:
        pnl = self.get("/finance/pnl?start=%s&end=%s&period=month&basis=accrual" % WINDOW)
        bs = self.get("/finance/balance-sheet?as_of=%s" % WINDOW[1])
        income = sum(pnl["income"]) if isinstance(pnl["income"], list) else int(pnl["income"])
        expense = sum(pnl["expense"]) if isinstance(pnl["expense"], list) else int(pnl["expense"])

        def named(rows: list[dict[str, Any]], name: str) -> int:
            # Matched by the account's name: the mirror keeps the provider's chart (QuickBooks 1010 Checking, not 1000).
            return sum(int(r["balance"]) for r in rows if r.get("name") == name)
        return {"income": income, "expense": expense, "net": int(pnl["net"]) if not isinstance(pnl["net"], list)
                else sum(pnl["net"]), "bank": named(bs["assets"], "Checking"),
                "ar": named(bs["assets"], "Accounts Receivable"), "ap": named(bs["liabilities"], "Accounts Payable")}

    def liability_lines(self) -> list[tuple[str, int]]:
        """Every liability line with a balance, by name (the balance sheet as the books show it)."""
        bs = self.get("/finance/balance-sheet?as_of=%s" % WINDOW[1])
        return [(str(r.get("name")), int(r.get("balance") or 0)) for r in bs["liabilities"] if int(r.get("balance") or 0)]

    def live_books(self) -> list[dict[str, Any]]:
        """The sources page's books rows that are not disconnected: the active books connections."""
        return [r for r in self.books_rows() if (r.get("sync") or {}).get("status") != "disconnected"]

    def payable_lines(self) -> list[tuple[str, int]]:
        """The liability lines with a balance whose name says payable: one is expected, the provider's."""
        return [(n, b) for n, b in self.liability_lines() if "payable" in n.lower()]

    def page_reads(self) -> dict[str, int]:
        """Query pages read per entity, from the fake's call log (QuickBooks query form)."""
        calls = self.fake_call("/_fake/calls")["calls"]
        out: dict[str, int] = {}
        for call in calls:
            query = str(call.get("query", {}).get("query", ""))
            if query.startswith("SELECT * FROM ") and "COUNT(*)" not in query:
                entity = query.split()[3]
                out[entity] = out.get(entity, 0) + 1
        return out

    # -- QuickBooks steps 4 to 7 (the Xero steps are a separate set; none is written in this revision)
    def outbox_open(self) -> int:
        counts = self.accounting_row().get("outbox") or {}
        return sum(int(counts.get(k, 0)) for k in ("pending", "failed", "unknown"))

    def fake_rows(self, entity: str) -> list[dict[str, Any]]:
        """The provider's own records, read through its query: the check is against the fake, not against Tico."""
        query = urllib.parse.quote(f"SELECT * FROM {entity}")
        status, body = http("GET", f"{self.fake}/v3/company/{QBO_REALM}/query?query={query}", token="rehearsal")
        if status != 200:
            raise RuntimeError(f"fake query {entity}: {status}")
        return list((body.get("QueryResponse") or {}).get(entity) or [])

    def fake_posts(self) -> list[dict[str, Any]]:
        return [c for c in self.fake_call("/_fake/calls")["calls"] if c["method"] == "POST"]

    def local_doc(self, path: str, number: str) -> dict[str, Any] | None:
        return next((r for r in rows_of(self.get(path + "?limit=200"), path.rsplit("/", 1)[1])
                     if r.get("number") == number), None)

    def budget(self, name: str, on: str) -> dict[str, Any]:
        """The budget by name with its figures flat: amount, actual, held and remaining are under its `state`."""
        rows = rows_of(self.get("/finance/budgets?limit=200&at=%s" % on), "budgets")
        row = next((r for r in rows if r.get("name") == name), None)
        return {**row, **(row.get("state") or {})} if row else {}

    def ids(self) -> dict[str, str]:
        """Tico's own ids for the rehearsal's accounts, parties and money account, found by name."""
        def coded(rows: list[dict[str, Any]], code: str, name: str) -> str:
            found = [r for r in rows if str(r.get("number")) == code]
            if len(found) != 1 or found[0].get("name") != name:
                raise RuntimeError(f"no account {code} {name!r} in the books; {[(r.get('number'), r.get('name')) for r in found]}")
            return str(found[0]["id"])
        bill_lines = rows_of(self.get("/finance/chart?for=bill"))
        invoice_lines = rows_of(self.get("/finance/chart?for=invoice"))
        journal_lines = rows_of(self.get("/finance/chart?for=journal"))
        vendors = rows_of(self.get("/finance/contacts?kind=vendor&limit=200"), "contacts")
        customers = rows_of(self.get("/finance/contacts?kind=customer&limit=200"), "contacts")
        def named(rows: list[dict[str, Any]], name: str) -> str:
            found = [r for r in rows if r.get("name") == name]
            if not found:
                raise RuntimeError(f"no {name!r} in the books")
            return str(found[0]["id"])
        return {"software": coded(bill_lines, "6120", "Software"),
                "consulting": coded(invoice_lines, "4010", "Consulting income"),
                "office": coded(journal_lines, "6410", "Office supplies"),
                "checking_account": coded(journal_lines, "1010", "Checking"),
                "vendor": named(vendors, "Acme Supplies"), "customer": named(customers, "Acme Retail")}

    def qbo_step_writes(self) -> None:
        """Tico writes through the outbox: a bill, an invoice, two payments and two journals, each confirmed by QuickBooks
        with the same amounts and accounts; a retry after a lost answer makes no duplicate; a 401 gets one refresh."""
        from backend.finance import mirror
        res = self.res
        res.step = "4 writes"
        ids = self.ids()
        before = self.totals()
        a1 = self.act("bill_create", {"vendor_id": ids["vendor"], "number": "R-BILL-1", "bill_date": DAY,
                                      "due_date": DAY, "lines": [{"account_id": ids["software"], "amount": B1,
                                                                  "description": "rehearsal bill"}]})
        a2 = self.act("invoice_create", {"customer_id": ids["customer"], "number": "R-INV-1", "invoice_date": DAY,
                                         "due_date": DAY, "lines": [{"account_id": ids["consulting"], "amount": I1,
                                                                     "description": "rehearsal invoice"}]})
        a3 = self.act("journal", {"date": DAY, "memo": "R-JE-1 rehearsal", "lines": [
            {"account_id": ids["office"], "amount": J1}, {"account_id": ids["checking_account"], "amount": -J1}]})
        statuses = [a.get("status") or a.get("reason") for _, a in (a1, a2, a3)]
        res.check("the bill, the invoice and the journal are accepted as writes out",
                  all(s == 200 and a.get("status") == "committed" for s, a in (a1, a2, a3)), str(statuses))
        landed = self.settle(lambda: self.local_doc("/finance/bills", "R-BILL-1") is not None
                             and self.local_doc("/finance/invoices", "R-INV-1") is not None
                             and self.outbox_open() == 0, 150)
        res.check("QuickBooks confirms the bill and the invoice, and Tico takes them in", landed,
                  f"outbox={self.accounting_row().get('outbox')}")
        bill = self.local_doc("/finance/bills", "R-BILL-1") or {}
        inv = self.local_doc("/finance/invoices", "R-INV-1") or {}
        p1 = self.act("bill_mark_paid", {"bill_id": str(bill.get("id", "")), "amount": B1, "date": DAY,
                                         "account_id": ids["checking_account"]})
        p2 = self.act("invoice_record_payment", {"invoice_id": str(inv.get("id", "")), "amount": I1, "date": DAY,
                                                 "account_id": ids["checking_account"]})
        res.check("the bill payment and the invoice payment are accepted as writes out",
                  all(s == 200 and a.get("status") == "committed" for s, a in (p1, p2)),
                  str([a.get("status") or a.get("reason") for _, a in (p1, p2)]))
        self.settle(lambda: self.outbox_open() == 0, 150)
        bill_rows = [r for r in self.fake_rows("Bill") if r.get("DocNumber") == "R-BILL-1"]
        bill_ok = len(bill_rows) == 1 and float(bill_rows[0].get("TotalAmt", -1)) == qbo_money(B1) and [
            ln["AccountBasedExpenseLineDetail"]["AccountRef"]["value"] for ln in bill_rows[0].get("Line", [])
            if ln.get("DetailType") == "AccountBasedExpenseLineDetail"] == ["21"]
        inv_rows = [r for r in self.fake_rows("Invoice") if r.get("DocNumber") == "R-INV-1"]
        items = {str(r["Id"]): r for r in self.fake_rows("Item")}
        inv_ok = len(inv_rows) == 1 and float(inv_rows[0].get("TotalAmt", -1)) == qbo_money(I1) and all(
            items.get(str(ln["SalesItemLineDetail"]["ItemRef"]["value"]), {}).get("IncomeAccountRef", {}).get("value")
            == "18" for ln in inv_rows[0].get("Line", []) if ln.get("DetailType") == "SalesItemLineDetail")
        paid_bill = [r for r in self.fake_rows("BillPayment") if r.get("TxnDate") == DAY
                     and float(r.get("TotalAmt", -1)) == qbo_money(B1)]
        paid_inv = [r for r in self.fake_rows("Payment") if r.get("TxnDate") == DAY
                    and float(r.get("TotalAmt", -1)) == qbo_money(I1)]
        je = [r for r in self.fake_rows("JournalEntry") if "R-JE-1" in json.dumps(r)]
        je_ok = len(je) == 1 and sorted(
            (ln["JournalEntryLineDetail"]["AccountRef"]["value"], ln["JournalEntryLineDetail"]["PostingType"],
             float(ln["Amount"])) for ln in je[0].get("Line", [])) == [("10", "Credit", 50.0), ("24", "Debit", 50.0)]
        bill_seen = [(r.get("TotalAmt"), [ln.get("AccountBasedExpenseLineDetail", {}).get("AccountRef")
                                          for ln in r.get("Line", [])]) for r in bill_rows]
        res.check("QuickBooks holds the four writes with the same amounts and accounts as Tico",
                  bill_ok and inv_ok and len(paid_bill) == 1 and len(paid_inv) == 1 and je_ok,
                  f"bill={bill_ok} (QuickBooks has {bill_seen}) invoice={inv_ok} billpayment={len(paid_bill)} "
                  f"payment={len(paid_inv)} journal={je_ok}")
        # A retry after a lost answer: the bill is written, its answer never arrives, and the retry must not duplicate it.
        lost_before = self.fake_call("/_fake/calls")["lost"]
        self.fake_call("/_fake/lose", {"path": "bill", "count": 1})
        a4 = self.act("bill_create", {"vendor_id": ids["vendor"], "number": "R-BILL-2", "bill_date": DAY,
                                      "due_date": DAY, "lines": [{"account_id": ids["software"], "amount": B2,
                                                                  "description": "lost answer"}]})
        done = self.settle(lambda: self.local_doc("/finance/bills", "R-BILL-2") is not None and self.outbox_open() == 0,
                           150)
        lost = self.fake_call("/_fake/calls")["lost"] - lost_before
        copies = [r for r in self.fake_rows("Bill") if r.get("DocNumber") == "R-BILL-2"]
        tico_copies = [r for r in rows_of(self.get("/finance/bills?limit=200"), "bills") if r.get("number") == "R-BILL-2"]
        posts = [c for c in self.fake_posts() if c["path"].endswith("/bill")
                 and c["body"].get("DocNumber") == "R-BILL-2"]
        request_ids = {c["query"].get("requestid") for c in posts}
        res.check("a lost answer for a bill is retried and makes no duplicate (one in QuickBooks, one in Tico)",
                  a4[0] == 200 and done and lost == 1 and len(copies) == 1 and len(tico_copies) == 1,
                  f"lost answers={lost} copies in QuickBooks={len(copies)} in Tico={len(tico_copies)} "
                  f"posts={len(posts)} request ids={len(request_ids)} outbox={self.accounting_row().get('outbox')}")
        # A 401 from QuickBooks: one new token from HQ, then the call is tried again and the write lands.
        refreshes = self.hq.refreshes
        refused = self.fake_call("/_fake/calls")["refused"]
        self.fake_call("/_fake/expire", {})
        a5 = self.act("journal", {"date": DAY, "memo": "R-JE-2 after expiry", "lines": [
            {"account_id": ids["office"], "amount": J2}, {"account_id": ids["checking_account"], "amount": -J2}]})
        landed5 = self.settle(lambda: self.outbox_open() == 0 and any(
            "R-JE-2" in json.dumps(r) for r in self.fake_rows("JournalEntry")), 150)
        refused = self.fake_call("/_fake/calls")["refused"] - refused
        res.check("a 401 gets one token refresh from HQ and the write still lands",
                  a5[0] == 200 and landed5 and refused >= 1 and self.hq.refreshes > refreshes,
                  f"401s given={refused} HQ refreshes={self.hq.refreshes - refreshes}")
        expected = moved(before, qbo_writes_delta())
        done_totals = self.settle(lambda: self.totals() == expected, 150)
        res.check("after the writes, the P&L and the balance sheet match the writes to the cent", done_totals,
                  f"want {expected} got {self.totals()}")
        payables = self.payable_lines()
        res.check("the bills' payables post to the provider's Accounts Payable, and no second payable line holds a balance",
                  [name for name, _ in payables] == ["Accounts Payable"],
                  f"payable lines with a balance: {payables}; the books' liabilities: {self.liability_lines()}")
        del mirror

    def qbo_step_refusals(self) -> None:
        """Writes the mirrored books refuse get a plain reason and write nothing: no outbox row, no call to QuickBooks."""
        from backend.finance import mirror
        res = self.res
        res.step = "5 refusals"
        ids = self.ids()
        bill = self.local_doc("/finance/bills", "R-BILL-2") or {}
        inv = self.local_doc("/finance/invoices", "R-INV-1") or {}
        outbox_before, posts_before, totals_before = self.outbox_open(), len(self.fake_posts()), self.totals()
        payloads: dict[str, dict[str, Any]] = {
            "bill_update": {"bill_id": str(bill.get("id", "")), "memo": "refused"},
            "bill_approve": {"bill_id": str(bill.get("id", ""))},
            "bill_schedule": {"bill_id": str(bill.get("id", "")), "pay_on": DAY, "money_account_id": ids["checking_account"]},
            "invoice_send": {"invoice_id": str(inv.get("id", ""))},
            "contact_create": {"kind": "vendor", "name": "Refused Vendor"},
            "contact_update": {"contact_id": ids["vendor"], "name": "Refused"},
            "account_create": {"code": "9900", "name": "Refused account", "type": "expense"},
            "account_update": {"account_id": ids["software"], "name": "Refused"},
            "recipient_add": {"name": "Acme Supplies", "method": "ach", "details_fp": "fp-rehearsal",
                              "details_ref": "vault-ref-rehearsal", "last4": "4321", "contact_id": ids["vendor"]},
        }
        rows = []
        for kind in mirror.REFUSED:
            status, answer = self.act(kind, payloads.get(kind, {}))
            reason = str(answer.get("reason") or answer.get("raw") or "")
            rows.append((kind, status, answer.get("status"), answer.get("code"), reason[:160]))
        time.sleep(3)
        for kind, status, state, code, reason in rows:
            print(f"[quickbooks] refusal {kind}: HTTP {status} status={state} code={code} reason={reason!r}", flush=True)
        # The product answers a refused kind with status denied, code not_available (failed is accepted too: the reason is the test).
        refused_ok = [kind for kind, _, state, _, reason in rows
                      if state in ("denied", "failed") and "mirror mode" in reason]
        res.check("each refused write says it is not available while the books are a provider's",
                  len(refused_ok) == len(mirror.REFUSED),
                  f"{len(refused_ok)}/{len(mirror.REFUSED)} refused with the mirror reason; not refused with it: "
                  f"{[k for k in mirror.REFUSED if k not in refused_ok]}")
        res.check("the refused writes add no outbox row and make no call to QuickBooks",
                  self.outbox_open() == outbox_before and len(self.fake_posts()) == posts_before,
                  f"outbox {outbox_before}->{self.outbox_open()}, posts {posts_before}->{len(self.fake_posts())}")
        res.check("the refused writes change no totals", self.totals() == totals_before)
        del mirror

    def qbo_step_budgets(self) -> None:
        """A budget on a mirrored expense line sees the provider's actuals; a queued bill holds its budget until the
        provider confirms it; actuals post over the limit and show over; paying a bill over a hold_payment limit waits."""
        from backend.finance import mirror
        res = self.res
        res.step = "6 budgets"
        ids = self.ids()
        status, made = self.act("budget_create", {"name": "Software", "period": "month", "account_id": ids["software"],
                                                  "amount": BUDGET, "at_limit": "hold_payment", "warn_pct": 80,
                                                  "effective_from": "2026-10-01"})
        res.check("the Software budget is made (October, 10000.00, hold_payment at the limit)",
                  status == 200 and made.get("status") == "committed", f"HTTP {status} {made.get('reason') or ''}")
        now = self.budget("Software", DAY)
        res.check("the budget sees the mirrored actuals on its line (bill 106 and the step 4 bills)",
                  int(now.get("actual") or 0) == SOFTWARE_OCT_BEFORE and int(now.get("amount") or 0) == BUDGET,
                  f"budget={now}")
        before = self.totals()
        mirror.RETRY = (8,)                                   # a lost answer is retried after 8 s: the bill waits meanwhile
        try:
            self.fake_call("/_fake/lose", {"path": "bill", "count": 1})
            s3, a3 = self.act("bill_create", {"vendor_id": ids["vendor"], "number": "R-BILL-3", "bill_date": DAY,
                                              "due_date": DAY, "lines": [{"account_id": ids["software"],
                                                                          "amount": B3, "description": "held"}]})
            time.sleep(1)
            waiting = self.budget("Software", DAY)
            res.check("a queued bill holds its Software budget until QuickBooks confirms it (§4.4)",
                      s3 == 200 and int(waiting.get("held") or 0) == B3,
                      f"held={waiting.get('held')} (want {B3}); outbox={self.accounting_row().get('outbox')}")
            confirmed = self.settle(lambda: self.outbox_open() == 0 and self.local_doc("/finance/bills", "R-BILL-3")
                                    is not None, 90)
        finally:
            mirror.RETRY = RETRY_REHEARSAL
        after3 = self.budget("Software", DAY)
        res.check("once confirmed, the bill is actual and its hold is gone",
                  confirmed and int(after3.get("actual") or 0) == SOFTWARE_OCT_BEFORE + B3
                  and int(after3.get("held") or 0) == 0, f"actual={after3.get('actual')} held={after3.get('held')}")
        s4, a4 = self.act("bill_create", {"vendor_id": ids["vendor"], "number": "R-BILL-4", "bill_date": DAY,
                                          "due_date": DAY, "lines": [{"account_id": ids["software"], "amount": B4,
                                                                      "description": "over the limit"}]})
        over_done = self.settle(lambda: self.outbox_open() == 0 and self.local_doc("/finance/bills", "R-BILL-4")
                                is not None, 150)
        over = self.budget("Software", DAY)
        res.check("a bill over the limit still posts (actuals always post) and the budget shows over",
                  s4 == 200 and over_done and int(over.get("actual") or 0) == SOFTWARE_OCT_BEFORE + B3 + B4
                  and int(over.get("remaining") or 0) < 0, f"actual={over.get('actual')} remaining={over.get('remaining')}")
        bill4 = self.local_doc("/finance/bills", "R-BILL-4") or {}
        sp, dry = self.act("bill_mark_paid", {"bill_id": str(bill4.get("id", "")), "amount": B4, "date": DAY,
                                              "account_id": ids["checking_account"]}, dry_run=True)
        res.check("paying that bill over a hold_payment limit waits for a person",
                  sp == 200 and dry.get("status") == "needs_approval", f"dry run: {dry.get('status')} {dry.get('reason') or ''}")
        expected = moved(before, qbo_budget_delta())
        res.check("the P&L and the balance sheet match the two budget bills to the cent",
                  self.settle(lambda: self.totals() == expected, 90), f"want {expected} got {self.totals()}")
        del mirror

    def qbo_step_disconnect(self) -> None:
        """Disconnect: the data stays, no write reaches QuickBooks; reconnect the same organisation: nothing is duplicated.
        The HTTP disconnect route refuses a demo copy (as the connect route does), so the disconnect and the reconnect are
        called in-process: books_link.drop and books_link.connect_claim, which the routes call."""
        from backend.finance import books_link
        res = self.res
        res.step = "7 disconnect"
        ids = self.ids()
        before = self.totals()
        calls_before = len(self.hq.calls)
        try:
            books_link.drop(self.store, self.vault, self.link, self.cid, by=OWNER_ACTOR)
            dropped = "dropped"
        except Exception as exc:
            dropped = f"{type(exc).__name__}: {exc}"
        active_after_drop = self.live_books()
        hq_paths = [path for path, _ in self.hq.calls[calls_before:]]
        res.check("the books connection disconnects: HQ's grant is revoked and no books connection stays active",
                  dropped == "dropped" and not active_after_drop and any(p.endswith(("/revoke", "/disconnect"))
                                                                         for p in hq_paths),
                  f"drop={dropped}; active books rows={len(active_after_drop)}; HQ calls={hq_paths}")
        time.sleep(3)
        res.check("the data stays: the P&L and the balance sheet are as they were", self.totals() == before,
                  f"want {before} got {self.totals()}")
        posts_before = len(self.fake_posts())
        bills_before = len(self.fake_rows("Bill"))
        s5, a5 = self.act("bill_create", {"vendor_id": ids["vendor"], "number": "R-BILL-5", "bill_date": DAY,
                                          "due_date": DAY, "lines": [{"account_id": ids["software"], "amount": B5,
                                                                      "description": "while disconnected"}]})
        time.sleep(6)
        res.check("no write reaches QuickBooks while disconnected",
                  len(self.fake_posts()) == posts_before and len(self.fake_rows("Bill")) == bills_before,
                  f"write: HTTP {s5} status={a5.get('status')} reason={str(a5.get('reason') or '')[:120]}; "
                  f"outbox={self.accounting_row().get('outbox')}")
        queued = s5 == 200 and a5.get("status") == "committed"
        try:
            cid = books_link.connect_claim(self.store, self.vault, self.link, provider="quickbooks",
                                           company_id=COMPANY, claim_id="clm_rehearsal_4", name="back",
                                           by=OWNER_ACTOR, now="2026-11-01T12:05:00.000000Z")
        except Exception as exc:
            cid = f"{type(exc).__name__}: {exc}"
        active = self.live_books()
        res.check("reconnecting the same organisation leaves exactly one active books connection",
                  len(active) == 1 and isinstance(cid, str) and ":" not in cid,
                  f"cid={cid} (before the disconnect: {self.cid}) active books rows={len(active)}")
        self.settle(lambda: self.outbox_open() == 0 and (not queued or any(
            r.get("DocNumber") == "R-BILL-5" for r in self.fake_rows("Bill"))), 150)
        copies = [r for r in self.fake_rows("Bill") if r.get("DocNumber") == "R-BILL-5"]
        numbers = Counter(str(r.get("number")) for r in rows_of(self.get("/finance/bills?limit=200"), "bills")
                           if str(r.get("number", "")).startswith("R-BILL-"))
        expected = moved(before, {"expense": B5, "net": -B5, "ap": B5} if queued else {})
        res.check("after reconnect nothing is duplicated: each write is in QuickBooks once and in Tico once",
                  len(copies) == (1 if queued else 0) and all(n == 1 for n in numbers.values()),
                  f"R-BILL-5 in QuickBooks={len(copies)} (queued={queued}); Tico numbers={dict(numbers)}; "
                  f"outbox={self.accounting_row().get('outbox')}")
        res.check("after reconnect the totals are the writes that were accepted, to the cent",
                  self.settle(lambda: self.totals() == expected, 120), f"want {expected} got {self.totals()}")
        res.check("the sources page shows the connection active again",
                  self.accounting_row().get("sync", {}).get("status") == "active",
                  str(self.accounting_row().get("sync", {}).get("status")))

    def run(self) -> bool:
        self.start_fake()
        try:
            self.start_demo()
            self.step_connect()
            self.step_first_sync()
            self.step_changes()
            if self.provider == "quickbooks":
                self.qbo_step_writes()
                self.qbo_step_refusals()
                self.qbo_step_budgets()
                self.qbo_step_disconnect()
        finally:
            self.close()
        print(f"[{self.provider}] {'PASSED' if self.res.ok else 'FAILED'}: "
              f"{sum(r['ok'] for r in self.res.rows)}/{len(self.res.rows)} checks; skips: {self.res.skips}", flush=True)
        return self.res.ok


def run_child(provider: str, keep: bool) -> int:
    os.environ.setdefault("TICO_UPDATE_CHECK", "off")
    os.environ["TICO_QUICKBOOKS_BASE"] = f"http://127.0.0.1:{QBO_PORT}"
    os.environ["TICO_XERO_BASE"] = f"http://127.0.0.1:{XERO_PORT}/api.xro/2.0"
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from backend import demo
    rehearsal = Rehearsal(provider, keep)
    with demo.network_blocked():
        try:
            ok = rehearsal.run()
        except Exception as exc:  # the run's own error is a failed check, not a crash with no report
            rehearsal.res.check("the rehearsal ran to the end", False, f"{type(exc).__name__}: {exc}"[:400])
            rehearsal.close()
            ok = False
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--provider", choices=("quickbooks", "xero", "all"), default="all")
    parser.add_argument("--keep", action="store_true", help="keep each demo's data folder")
    parser.add_argument("--child", choices=("quickbooks", "xero"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        return run_child(args.child, args.keep)
    providers = ["quickbooks", "xero"] if args.provider == "all" else [args.provider]
    status = 0
    for provider in providers:
        if provider == "xero":
            # The Xero steps are not written yet. Say so and fail, rather than report a pass for steps that never ran.
            print("== xero: not run in this revision (no Xero steps are implemented); counted as failed", flush=True)
            status = status or 1
            continue
        command = [sys.executable, str(Path(__file__).resolve()), "--child", provider]
        if args.keep:
            command.append("--keep")
        code = subprocess.call(command)
        print(f"== {provider}: {'passed' if code == 0 else 'failed'} (exit {code})", flush=True)
        status = status or code
    return status


if __name__ == "__main__":
    sys.exit(main())
