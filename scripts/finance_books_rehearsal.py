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
import uuid
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
DEMO_PORT, QBO_PORT, XERO_PORT = 18821, 18822, 18823
COMPANY = "5a0c7e21-9d4b-4f3e-8b2a-6e1d0c9f7a42"          # a fictional company id (HQ's)
OWNER_ACTOR = "human:ana"
QBO_REALM, QBO_OTHER_REALM = "1234567", "7654321"
XERO_TENANT, XERO_OTHER_TENANT = "acme-tenant", "other-tenant"
WINDOW = ("2026-09-01", "2026-10-31")
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
        """Wakes the books worker and waits until a predicate about the books holds."""
        self.ring()
        return wait_until(predicate, seconds, every=1.0)

    def close(self) -> None:
        self.stop_demo()
        self.stop_fake()
        if not self.keep:
            shutil.rmtree(self.data, ignore_errors=True)

    # -- the steps
    def step_connect(self) -> None:
        from backend.finance import books_link, books_sync
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
        res.skip("a record deleted before the first sync: the query feed never lists it, so there is nothing to see "
                 "here; deletion is checked in step 3 through the change feed")
        del mirror

    def step_changes(self) -> None:
        """Provider edits, a void and deletes arrive by webhook and the change feed; Tico restates. A closed month takes
        the restatement and flags it on its close snapshot."""
        res = self.res
        res.step = "3 changes"
        expected_before = self.totals()
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
        close = self.get("/finance/close/2026-09")
        res.check("the closed month takes the restatement and flags it on its close snapshot",
                  "restat" in json.dumps(close).lower(), json.dumps(close)[:200])
        bills = json.dumps(self.get("/finance/bills?status=void"))
        res.check("the voided bill stays void after the change", "B-1005" in bills)

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

    def run(self) -> bool:
        self.start_fake()
        try:
            self.start_demo()
            self.step_connect()
            self.step_first_sync()
            self.step_changes()
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
