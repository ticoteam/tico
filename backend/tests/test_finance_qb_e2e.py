"""A local end-to-end run at the QuickBooks boundary: the real QuickBooks client talks HTTP to scripts/fake_quickbooks.py,
started here on its own port and stopped by its PID. The backfill reads items, locations, classes and jobs with their
refs, and the P&L reads by location. Closing a purchase order is refused on a mirrored book (the provider is not sent
that yet), which is checked here; purchase orders and estimates written to the fake are checked in
test_an_order_and_an_estimate_are_stored_with_their_location_class_and_item. The native order leg runs in
test_finance_purchasing.py."""
import json
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from backend.finance import qbo as QC
from backend.finance import reads as FR
from backend.finance.core.errors import NOT_AVAILABLE
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401
from backend.tests.test_finance_mirror import LATER, _connection, _drain, _mirror

FAKE = Path(__file__).resolve().parents[2] / "scripts" / "fake_quickbooks.py"
WINDOW = ("2026-10-01", "2026-10-31")
SEED: dict[str, list[dict[str, Any]]] = {
    "Account": [
        {"Id": "10", "Name": "Checking", "AccountType": "Bank", "AcctNum": "1010", "Active": True},
        {"Id": "11", "Name": "Accounts Payable", "AccountType": "Accounts Payable", "AcctNum": "2010", "Active": True},
        {"Id": "12", "Name": "Consulting income", "AccountType": "Income", "AcctNum": "4010", "Active": True},
        {"Id": "13", "Name": "Software", "AccountType": "Expense", "AcctNum": "6110", "Active": True},
    ],
    "Department": [{"Id": "20", "Name": "Denver", "Active": True}],
    "Class": [{"Id": "30", "Name": "Web", "Active": True}],
    "Item": [{"Id": "40", "Name": "Widget", "Type": "Service", "Active": True,
              "IncomeAccountRef": {"value": "12", "name": "Consulting income"}}],
    "Customer": [
        {"Id": "50", "DisplayName": "Acme Retail", "Active": True, "Job": False},
        {"Id": "51", "DisplayName": "Job 12", "Active": True, "Job": True, "ParentRef": {"value": "50"}},
    ],
    "Vendor": [{"Id": "60", "DisplayName": "Acme Supplies", "Active": True}],
    "JournalEntry": [{"Id": "70", "SyncToken": "0", "TxnDate": "2026-10-10", "PrivateNote": "Supplies", "Line": [
        {"Id": "1", "DetailType": "JournalEntryLineDetail", "Amount": 1000, "Description": "Supplies",
         "JournalEntryLineDetail": {"PostingType": "Debit", "AccountRef": {"value": "13"},
                                    "DepartmentRef": {"value": "20"}, "ClassRef": {"value": "30"}}},
        {"Id": "2", "DetailType": "JournalEntryLineDetail", "Amount": 1000,
         "JournalEntryLineDetail": {"PostingType": "Credit", "AccountRef": {"value": "10"}}}]}],
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _read(base: str, sql: str) -> dict[str, Any]:
    """A query as the QuickBooks client sends it (bearer token and all), answered with the rows the fake stored."""
    url = f"{base}/v3/company/1234567/query?" + urllib.parse.urlencode({"query": sql})
    with _direct.open(urllib.request.Request(url, headers={"Authorization": "Bearer local-token"}), timeout=10) as reply:
        return json.loads(reply.read())


def _fake(base: str, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with _direct.open(req, timeout=10) as reply:
        return json.loads(reply.read() or b"{}")


@pytest.fixture
def fake_quickbooks():
    base = f"http://127.0.0.1:{_free_port()}"
    proc = subprocess.Popen([sys.executable, str(FAKE), "--port", base.rsplit(":", 1)[1]],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):
            try:
                _fake(base, "GET", "/_fake/calls")
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("the fake QuickBooks did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_an_invoice_keeps_its_estimate_link_through_quickbooks(fake_quickbooks):
    _fake(fake_quickbooks, "POST", "/_fake/seed", SEED)
    client = QC.QuickBooks("1234567", lambda force: "local-token", base=fake_quickbooks)
    body = {"day": "2026-10-06", "memo": "", "party": "Customer:50", "number": "", "due": "", "linked_estimate": "900",
            "lines": [{"item": "Item:40", "amount": 150_000, "description": "work", "project": ""}]}
    made = client.send("invoice.create", body, "invoice:linked-estimate")
    assert made.ok and made.external_id, made
    stored = _read(fake_quickbooks, "SELECT * FROM Invoice STARTPOSITION 1 MAXRESULTS 10")["QueryResponse"]["Invoice"]
    assert [(row["Id"], row["LinkedTxn"]) for row in stored] == \
        [(made.external_id, [{"TxnId": "900", "TxnType": "Estimate"}])]


def test_an_order_and_an_estimate_are_stored_with_their_location_class_and_item(fake_quickbooks):
    _fake(fake_quickbooks, "POST", "/_fake/seed", SEED)
    client = QC.QuickBooks("1234567", lambda force: "local-token", base=fake_quickbooks)
    line = {"account": "Account:12", "amount": 150_000, "description": "work", "item": "Item:40",
            "location": "Department:20", "class": "30", "project": ""}
    order = client.send("purchaseorder.create", {"party": "Vendor:60", "number": "PO-1", "day": "2026-10-10", "due": "",
                                                 "memo": "", "lines": [{**line, "account": "Account:13"}]},
                        "purchaseorder:one")
    estimate = client.send("estimate.create",
                           {"party": "Customer:50", "number": "EST-1", "day": "2026-10-10", "due": "", "memo": "",
                            "lines": [line]}, "estimate:one")
    assert order.ok and estimate.ok, (order, estimate)
    po = _read(fake_quickbooks, "SELECT * FROM PurchaseOrder STARTPOSITION 1 MAXRESULTS 10")
    po = po["QueryResponse"]["PurchaseOrder"]
    est = _read(fake_quickbooks, "SELECT * FROM Estimate STARTPOSITION 1 MAXRESULTS 10")["QueryResponse"]["Estimate"]
    assert po[0]["DepartmentRef"] == {"value": "20"} and po[0]["POStatus"] == "Open"
    assert po[0]["Line"][0]["ItemBasedExpenseLineDetail"]["ItemRef"] == {"value": "40"}
    assert po[0]["Line"][0]["ItemBasedExpenseLineDetail"]["ClassRef"] == {"value": "30"}
    assert est[0]["DepartmentRef"] == {"value": "20"} and est[0]["CustomerRef"] == {"value": "50"}
    assert est[0]["Line"][0]["SalesItemLineDetail"]["ItemRef"] == {"value": "40"}
    assert est[0]["Line"][0]["SalesItemLineDetail"]["ClassRef"] == {"value": "30"}


def test_a_connected_book_backfills_from_quickbooks_and_its_pnl_reads_by_location(books, fake_quickbooks):  # noqa: F811
    _fake(fake_quickbooks, "POST", "/_fake/seed", SEED)
    client = QC.QuickBooks("1234567", lambda force: "local-token", base=fake_quickbooks)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, client, cid) == []
    with books.read() as c:
        assert [str(r[0]) for r in c.execute("SELECT name FROM locations")] == ["Denver"]
        assert [str(r[0]) for r in c.execute("SELECT name FROM items")] == ["Widget"]
        assert [(str(r[0]), str(r[1])) for r in c.execute(
            "SELECT p.name, k.name FROM projects p JOIN fin_contacts k ON k.id=p.customer_id")] == \
            [("Job 12", "Acme Retail")]
        assert [str(r[0]) for r in c.execute("SELECT name FROM fin_dimensions WHERE kind='class'")] == ["Web"]
        by_location = FR.pnl_by(c, OWNER, "location", *WINDOW, basis="accrual", top_n=50, at=AT)
        plain = FR.pnl(c, OWNER, *WINDOW, basis="accrual", at=AT)
        assert by_location["expense"] == sum(plain["expense"]) == 100_000       # QuickBooks says 1000.00 dollars
        assert {str(r["label"]): int(r["expense"]) for r in by_location["rows"]}["Denver"] == 100_000
        refused = act(c, OWNER, "po_close", {"po_id": "x"})
        assert refused.status == "denied" and refused.code == NOT_AVAILABLE, refused
    calls = _fake(fake_quickbooks, "GET", "/_fake/calls")["calls"]
    queried = {str(call["query"].get("query", "")).split()[3] for call in calls
               if call["method"] == "GET" and "query" in call["query"] and "FROM" in call["query"].get("query", "")}
    assert {"Item", "Department", "Class"} <= queried


def test_a_purchase_order_and_an_estimate_the_provider_holds_come_in_on_backfill_and_follow_its_changes(
        books, fake_quickbooks):  # noqa: F811
    """The orders QuickBooks holds are read in the backfill (a purchase order to its vendor, an estimate to its customer),
    and an edit QuickBooks makes afterwards comes in on the change feed."""
    _fake(fake_quickbooks, "POST", "/_fake/seed", SEED)
    _fake(fake_quickbooks, "POST", "/_fake/seed", {
        "PurchaseOrder": [{"Id": "80", "SyncToken": "0", "DocNumber": "PO-80", "TxnDate": "2026-10-10",
                           "POStatus": "Open", "TotalAmt": 1000, "VendorRef": {"value": "60"},
                           "DepartmentRef": {"value": "20"}, "Line": [{
                               "Id": "1", "DetailType": "AccountBasedExpenseLineDetail", "Amount": 1000,
                               "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "13"}}}]}],
        "Estimate": [{"Id": "81", "SyncToken": "0", "DocNumber": "EST-81", "TxnDate": "2026-10-10",
                      "TxnStatus": "Pending", "TotalAmt": 1000, "CustomerRef": {"value": "50"}, "Line": [{
                          "Id": "1", "DetailType": "SalesItemLineDetail", "Amount": 1000,
                          "SalesItemLineDetail": {"ItemRef": {"value": "40"}}}]}]})
    client = QC.QuickBooks("1234567", lambda force: "local-token", base=fake_quickbooks)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, client, cid) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT number, status FROM fin_purchase_orders").fetchone()) == ("PO-80", "approved")
        assert c.execute("SELECT COUNT(*) FROM fin_po_lines").fetchone()[0] == 1
        assert tuple(c.execute("SELECT number, status FROM fin_estimates").fetchone()) == ("EST-81", "sent")
        assert c.execute("SELECT COUNT(*) FROM fin_estimate_lines").fetchone()[0] == 1
    _fake(fake_quickbooks, "POST", "/_fake/change", {"PurchaseOrder": [{
        "Id": "80", "SyncToken": "1", "DocNumber": "PO-80", "TxnDate": "2026-10-10", "POStatus": "Closed",
        "TotalAmt": 1000, "VendorRef": {"value": "60"}, "DepartmentRef": {"value": "20"}, "Line": [{
            "Id": "1", "DetailType": "AccountBasedExpenseLineDetail", "Amount": 1000,
            "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "13"}}}]}]})
    assert _drain(books, client, cid, now=LATER) == []
    with books.read() as c:
            assert tuple(c.execute("SELECT status, closed_by FROM fin_purchase_orders").fetchone()) == ("closed", "hand")
