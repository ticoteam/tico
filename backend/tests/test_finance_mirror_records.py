"""Shared records in a mirrored book (spec §4.4 and §10): QuickBooks items, departments, classes and jobs come in once,
transactions carry their location, class, job and item, renames and deletions change the records without reversing an
entry, and what the books send carries the same references back to the provider."""
import json
from types import SimpleNamespace
from typing import Any

import pytest

from backend.finance import mirror
from backend.finance import qbo as QC
from backend.finance.core import mirror as M
from backend.finance.core import qbo as Q
from backend.finance.core import xero as X
from backend.finance.core.errors import CONFLICT, NOT_FOUND, FinanceError
from backend.finance.store import banking as SK
from backend.finance.store import mirror as SX
from backend.finance.store import purchasing as SP
from backend.records import reads as RR
from backend.records import store as RS
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401
from backend.tests.test_finance_mirror import (  # noqa: F401
    FakeBooks, LATER, _connection, _drain, _entry_of, _local, _mirror, _sent, acct)


def _records(fake: FakeBooks) -> None:
    """Accounts, a vendor and a customer, and a QuickBooks item, department, class and job (a job of customer 4)."""
    fake.records["accounts"] = [acct(1, "1000", "Bank", "asset"), acct(2, "6100", "Software"),
                                acct(3, "4000", "Consulting", "income")]
    fake.records["parties"] = [M.Party(M.Ext("Vendor", "9", "1"), "Acme Software", "vendor"),
                               M.Party(M.Ext("Customer", "4", "1"), "Beta Client", "customer")]
    fake.records["records"] = [
        M.Item(M.Ext("Item", "5", "1"), "Consulting", kind="service", price=15000, income="Account:3",
               expense="Account:2"),
        M.Location(M.Ext("Department", "3", "1"), "Austin"),
        M.Dimension(M.Ext("Class", "7", "1"), "Field"),
        M.Project(M.Ext("Project", "12", "1"), "Kitchen remodel", customer="Customer:4"),
    ]


def _bill(total: int = 1500, rev: str = "1") -> M.Document:
    """A bill from Acme whose one line is for the item, in the department, class and job."""
    tags = (("location", "Department:3"), ("class", "Class:7"), ("project", "Customer:12"))
    return M.Document(M.Ext("Bill", "77", rev), party="Vendor:9", number="B-77", day="2026-10-05", due="2026-11-05",
                      total=total, lines=(M.EntryLine("Account:2", total, description="seats", key="7", tags=tags,
                                                      item="Item:5"),))


def _counts(store: Any) -> tuple[int, int, int, int, int]:
    """Rows in the item, department, class and job records, and external refs."""
    with store.read() as c:
        def n(sql: str) -> int:
            return int(c.execute(sql).fetchone()[0])
        return (n("SELECT COUNT(*) FROM items"), n("SELECT COUNT(*) FROM locations"),
                n("SELECT COUNT(*) FROM fin_dimensions WHERE kind='class'"), n("SELECT COUNT(*) FROM projects"),
                n("SELECT COUNT(*) FROM fin_external_refs"))


def test_a_failed_read_of_the_records_resumes_where_it_stopped_and_a_rerun_makes_no_duplicates(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    fake.fail.add(("records", 3))
    problems = [p for _ in range(12) for p in mirror.sync_once(books, fake, cid, AT, size=2)]
    assert problems == ["backfill records from 3: the provider is down"]
    assert _counts(books) == (1, 1, 1, 1, 9)
    assert _drain(books, fake, cid) == []
    assert _counts(books) == (1, 1, 1, 1, 9)


def test_an_imported_bill_carries_its_location_class_job_and_item_and_its_books_balance(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["documents"] = [_bill()]
    cid = _connection(books)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        bill = _local(c, "Bill:77", "fin_bills")
        location, cls = _local(c, "Department:3", "locations"), _local(c, "Class:7", "fin_dimensions")
        job, item = _local(c, "Project:12", "projects"), _local(c, "Item:5", "items")
        row = c.execute("SELECT item_id, tags_json FROM fin_bill_lines WHERE bill_id=?", (bill,)).fetchone()
        assert row[0] == item
        assert json.loads(row[1]) == {"location": location, "class": cls, "project": job}
        entry = str(c.execute("SELECT entry_id FROM fin_bills WHERE id=?", (bill,)).fetchone()[0])
        tags = {str(k): str(v) for k, v in c.execute(
            "SELECT t.kind, t.value FROM fin_line_tags t JOIN fin_lines l ON l.id=t.line_id "
            "WHERE l.entry_id=? AND l.account_id=? AND t.kind!='team'", (entry, _local(c, "Account:2", "fin_accounts")))}
        assert tags == {"location": location, "class": cls, "project": job}
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE entry_id=?", (entry,)).fetchone()[0] == 0


def test_an_invoice_to_a_job_is_to_its_parent_customer_and_its_lines_are_tagged_with_the_job(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["documents"] = [M.Document(
        M.Ext("Invoice", "30", "1"), party="Customer:12", number="I-30", day="2026-10-06", due="2026-11-06",
        total=1500, lines=(M.EntryLine("Account:3", 1500, description="work", key="8", item="Item:5"),))]
    cid = _connection(books)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        invoice = _local(c, "Invoice:30", "fin_invoices")
        assert c.execute("SELECT customer_id FROM fin_invoices WHERE id=?", (invoice,)).fetchone()[0] == \
            _local(c, "Customer:4", "fin_contacts")
        tags = json.loads(c.execute("SELECT tags_json FROM fin_invoice_lines WHERE invoice_id=?",
                                    (invoice,)).fetchone()[0])
        assert tags == {"project": _local(c, "Project:12", "projects")}


def test_a_renamed_or_deactivated_record_updates_in_location_and_reverses_nothing(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["documents"] = [_bill()]
    cid = _connection(books)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        entries = c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0]
        item = _local(c, "Item:5", "items")
    fake.changes = [
        M.Item(M.Ext("Item", "5", "2"), "Advisory", kind="service", price=20000, income="Account:3",
               expense="Account:2", active=False),
        M.Location(M.Ext("Department", "3", "2"), "Austin HQ"),
        M.Dimension(M.Ext("Class", "7", "2"), "Field", active=False),
        M.Ext("Customer", "12", "2", True),                 # the job is deleted at the provider
    ]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        row = c.execute("SELECT name, price, archived_at IS NULL FROM items WHERE id=?", (item,)).fetchone()
        assert tuple(row) == ("Advisory", 20000, 0)
        assert c.execute("SELECT name FROM locations WHERE id=?", (_local(c, "Department:3", "locations"),)
                         ).fetchone()[0] == "Austin HQ"
        assert c.execute("SELECT active FROM fin_dimensions WHERE id=?", (_local(c, "Class:7", "fin_dimensions"),)
                         ).fetchone()[0] == 0
        assert c.execute("SELECT archived_at IS NULL FROM projects WHERE id=?", (_local(c, "Project:12", "projects"),)
                         ).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0] == entries
        assert c.execute("SELECT status FROM fin_entries WHERE id=(SELECT entry_id FROM fin_bills WHERE number='B-77')"
                         ).fetchone()[0] == "posted"


def test_a_bill_made_here_is_sent_with_its_location_class_job_and_item_and_booked_once_the_provider_confirms(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        tags = {"location": _local(c, "Department:3", "locations"), "class": _local(c, "Class:7", "fin_dimensions"),
                "project": _local(c, "Project:12", "projects")}
        made = act(c, OWNER, "bill_create", {
            "vendor_id": _local(c, "Vendor:9", "fin_contacts"), "number": "B-78", "bill_date": "2026-10-05",
            "due_date": "2026-11-05",
            "lines": [{"item_id": _local(c, "Item:5", "items"), "amount": 1500, "description": "seats",
                       "tags": tags}]})
        assert made.status == "committed", made
        assert c.execute("SELECT COUNT(*) FROM fin_bills").fetchone()[0] == 0
    assert _drain(books, fake, cid, now=LATER) == []
    body = _sent(fake, "bill.create")
    assert body["lines"] == [{"account": "Account:2", "amount": 1500, "description": "seats", "item": "Item:5",
                              "item_code": "Consulting", "location": "Department:3", "class": "7",
                              "project": "Customer:12"}]
    out = Q.bill_body(body)
    assert out["DepartmentRef"] == {"value": "3"}
    assert out["Line"][0]["ItemBasedExpenseLineDetail"] == {
        "ItemRef": {"value": "5"}, "Qty": 1, "UnitPrice": "15.00", "ClassRef": {"value": "7"},
        "CustomerRef": {"value": "12"}}
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_bills WHERE number='B-78'").fetchone()[0] == 1


def test_a_quickbooks_revision_gives_a_bill_line_the_item_it_names_and_no_other(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item = _local(c, "Item:5", "items")
        made = act(c, OWNER, "bill_create", {
            "vendor_id": _local(c, "Vendor:9", "fin_contacts"), "number": "B-79", "bill_date": "2026-10-05",
            "due_date": "2026-11-05", "lines": [{"item_id": item, "amount": 1500, "description": "seats"}]})
        assert made.status == "committed", made
    fake.replies = [mirror.Sent(True, kind="Bill", external_id="77", revision="1", lines=("L1",))]
    assert _drain(books, fake, cid, now=LATER) == []

    def line_item() -> str | None:
        with books.read() as c:
            return c.execute("SELECT l.item_id FROM fin_bill_lines l JOIN fin_bills b ON b.id = l.bill_id "
                             "WHERE b.number='B-79'").fetchone()[0]
    assert line_item() == item
    # QuickBooks revises the bill and its line still names the item: the line keeps it
    fake.changes = [M.Document(M.Ext("Bill", "77", "2"), party="Vendor:9", number="B-79", day="2026-10-05",
                               due="2026-11-05", total=1600,
                               lines=(M.EntryLine("Account:2", 1600, description="seats", key="L1", item="Item:5"),))]
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.read() as c:
        assert c.execute("SELECT total FROM fin_bills WHERE number='B-79'").fetchone()[0] == 1600
    assert line_item() == item
    # a revision whose line names no item leaves the line with none: QuickBooks states the item of each line
    fake.changes = [M.Document(M.Ext("Bill", "77", "3"), party="Vendor:9", number="B-79", day="2026-10-05",
                               due="2026-11-05", total=1600,
                               lines=(M.EntryLine("Account:2", 1600, description="seats", key="L1"),))]
    assert _drain(books, fake, cid, now=LATER3) == []
    assert line_item() is None


def test_a_mirrored_sale_of_an_inventory_item_posts_its_income_and_no_cost_of_goods_sold(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["accounts"] += [acct(4, "9300", "Inventory", "asset"), acct(5, "9500", "Cost of goods sold")]
    fake.records["records"] = [M.Item(M.Ext("Item", "6", "1"), "Widget", kind="inventory", income="Account:3",
                                      expense="Account:5", asset="Account:4")]
    fake.records["documents"] = [M.Document(
        M.Ext("Invoice", "31", "1"), party="Customer:4", number="I-31", day="2026-10-06", due="2026-11-06",
        total=2000, lines=(M.EntryLine("Account:3", 2000, description="widget", key="9", item="Item:6"),))]
    cid = _connection(books)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_invoices WHERE number='I-31'").fetchone()[0] == 1
        cogs = c.execute("SELECT COUNT(*) FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id "
                         "WHERE a.code='9500'").fetchone()[0]
        assert cogs == 0


def test_a_lines_own_class_is_sent_ahead_of_a_class_its_team_maps_to():
    assert M.class_refs({"team": "eng"}, {"team": {"eng": "99"}}) == {"team": "99"}
    assert M.class_refs({"team": "eng"}, {"team": {"eng": "99"}}, own="7") == {"class": "7"}


def test_quickbooks_items_departments_classes_and_jobs_map_to_their_records_and_a_plain_customer_stays_a_party():
    item = {"Id": "5", "SyncToken": "2", "Name": "Consulting", "Type": "Service", "UnitPrice": 150,
            "IncomeAccountRef": {"value": "33"}, "ExpenseAccountRef": {"value": "44"}, "Active": False}
    assert Q.records("Item", [item]) == [M.Item(M.Ext("Item", "5", "2"), "Consulting", kind="service", price=15000,
                                                income="Account:33", expense="Account:44", active=False)]
    assert Q.records("Department", [{"Id": "3", "SyncToken": "1", "Name": "Austin"}]) == [
        M.Location(M.Ext("Department", "3", "1"), "Austin")]
    job = {"Id": "12", "SyncToken": "1", "DisplayName": "Kitchen remodel", "Job": True, "ParentRef": {"value": "4"}}
    customer = {"Id": "4", "SyncToken": "3", "DisplayName": "Beta Client", "Job": False}
    website = {"Id": "30", "SyncToken": "0", "DisplayName": "Website", "IsProject": True}
    assert Q.records("Customer", [job, customer, website]) == [
        M.Project(M.Ext("Project", "12", "1"), "Kitchen remodel", customer="Customer:4"),
        M.Party(M.Ext("Customer", "4", "3"), "Beta Client", "customer"),
        M.Project(M.Ext("Project", "30", "0"), "Website", customer="")]


def test_an_invoice_the_provider_refuses_with_its_estimate_link_is_sent_again_without_it(monkeypatch):
    sent: list[dict[str, Any]] = []

    def call(self: Any, method: str, path: str, **kw: Any) -> Any:
        sent.append(kw["body"])
        if "LinkedTxn" in kw["body"]:
            return SimpleNamespace(status=400, body={"Fault": {"Error": [{"Message": "LinkedTxn is not valid"}]}})
        return SimpleNamespace(status=200, body={"Invoice": {"Id": "9", "SyncToken": "0"}})

    monkeypatch.setattr(QC.QuickBooks, "_call", call)
    monkeypatch.setattr(QC.QuickBooks, "_item_accounts", lambda self: {"Item:5": {"income": "Account:33"}})
    client = QC.QuickBooks("1234567", lambda force: "local-token", base="http://127.0.0.1:1")
    body = {"day": "2026-10-06", "memo": "", "party": "Customer:4", "number": "", "due": "", "linked_estimate": "7",
            "lines": [{"item": "Item:5", "amount": 1500, "description": "work", "project": ""}]}
    made = client.send("invoice.create", body, "invoice:one")
    assert made.ok and made.external_id == "9", made
    assert ["LinkedTxn" in x for x in sent] == [True, False]


def test_a_bill_names_its_item_class_job_and_one_location_and_refuses_two_locations():
    line = {"item": "Item:5", "amount": 1500, "description": "seats", "class": "7", "project": "Customer:12",
            "location": "Department:3"}
    body = {"day": "2026-10-05", "memo": "", "party": "Vendor:9", "number": "B-1", "due": "", "lines": [line]}
    out = Q.bill_body(body)
    assert out["DepartmentRef"] == {"value": "3"}
    assert out["Line"][0]["ItemBasedExpenseLineDetail"] == {
        "ItemRef": {"value": "5"}, "Qty": 1, "UnitPrice": "15.00", "ClassRef": {"value": "7"},
        "CustomerRef": {"value": "12"}}
    with pytest.raises(ValueError):
        Q.bill_body({**body, "lines": [line, {**line, "location": "Department:4"}]})


def test_an_invoice_for_a_job_is_to_the_job_and_a_journal_line_names_its_location_and_class():
    invoice = Q.invoice_body({"day": "2026-10-06", "memo": "", "party": "Customer:4", "number": "", "due": "",
                              "lines": [{"item": "Item:5", "amount": 1500, "description": "work",
                                         "project": "Customer:12"}]}, {})
    assert invoice["CustomerRef"] == {"value": "12"}
    assert invoice["Line"][0]["SalesItemLineDetail"]["ItemRef"] == {"value": "5"}
    entry = Q.journal_body({"date": "2026-10-06", "memo": "", "source": "sync", "lines": [
        {"account": "Account:2", "party": "", "amount": 1500, "description": "", "role": "",
         "classes": {"class": "7"}, "location": "Department:3", "project": "Customer:12"}]})
    detail = entry["Line"][0]["JournalEntryLineDetail"]
    assert detail["DepartmentRef"] == {"value": "3"}
    assert detail["ClassRef"] == {"value": "7"}
    assert detail["Entity"] == {"Type": "Customer", "EntityRef": {"type": "Customer", "value": "12"}}


def test_a_record_made_here_waits_for_the_provider_and_its_row_takes_the_id_it_was_sent_under(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        customer = _local(c, "Customer:4", "fin_contacts")
        made = act(c, OWNER, "item_create", {"name": "Design", "kind": "service", "price": 9000})
        located = act(c, OWNER, "location_create", {"name": "Dallas"})
        job = act(c, OWNER, "project_create", {"name": "Deck build", "customer_id": customer})
        assert [made.status, located.status, job.status] == ["committed"] * 3, (made, located, job)
        assert c.execute("SELECT COUNT(*) FROM items WHERE name='Design'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM locations WHERE name='Dallas'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM projects WHERE name='Deck build'").fetchone()[0] == 0
    assert _drain(books, fake, cid, now=LATER) == []
    assert _sent(fake, "item.create")["price"] == 9000
    with books.read() as c:
        assert c.execute("SELECT price FROM items WHERE id=?", (made.result["item_id"],)).fetchone()[0] == 9000
        located_id = located.result["location_id"]
        assert c.execute("SELECT name FROM locations WHERE id=?", (located_id,)).fetchone()[0] == "Dallas"
        assert c.execute("SELECT name FROM projects WHERE id=?",
                         (job.result["project_id"],)).fetchone()[0] == "Deck build"


def test_a_job_made_here_is_sent_to_quickbooks_as_a_job_under_its_customer(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "project_create",
                   {"name": "Deck build", "customer_id": _local(c, "Customer:4", "fin_contacts")})
        assert made.status == "committed", made
    assert _drain(books, fake, cid, now=LATER) == []
    kind, path, out, _ = QC._write_of("project.create", _sent(fake, "project.create"), {})
    assert (kind, path) == ("Customer", "customer")
    assert out["Job"] is True and out["DisplayName"] == "Deck build"
    assert out["ParentRef"] == {"value": "4"}


def test_an_item_change_in_a_mirrored_book_leaves_the_row_as_it_was_until_the_provider_confirms_or_refuses(
        books, monkeypatch):
    """X9: a refused price keeps the local price, asks once with the provider's reason, and lands once fixed."""
    asks: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mirror.tico, "needs_you", lambda c, person, title, body: asks.append((person, title, body)))
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item = _local(c, "Item:5", "items")
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.records"}).status == "committed"
        # Dan sees the item's accounts, so a price change of his is asked of an admin rather than found missing.
        assert act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "everything",
                                            "whose": "company"}).status == "committed"
        assert act(c, "human:dan", "item_update", {"item_id": item, "price": 20_000}).status == "needs_approval"
        assert act(c, OWNER, "item_update", {"item_id": item, "price": 20_000}).status == "committed"
        assert c.execute("SELECT price FROM items WHERE id=?", (item,)).fetchone()[0] == 15_000
    fake.replies.append(mirror.Sent(False, error="Price is not allowed on this item", final=True))
    problems = _drain(books, fake, cid, now=LATER)
    assert any("Price is not allowed on this item" in p for p in problems), problems
    with books.read() as c:
        assert c.execute("SELECT price FROM items WHERE id=?", (item,)).fetchone()[0] == 15_000
        status, error = c.execute("SELECT status, last_error FROM fin_outbox WHERE op='item.update'").fetchone()
        assert (status, error) == ("failed", "Price is not allowed on this item")
    assert len(asks) == 1 and "Price is not allowed on this item" in asks[0][2], asks
    with books.transaction() as c:
        assert act(c, OWNER, "item_update", {"item_id": item, "price": 21_000}).status == "committed"
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert c.execute("SELECT price FROM items WHERE id=?", (item,)).fetchone()[0] == 21_000


def test_two_changes_to_an_item_before_the_provider_confirms_both_reach_the_provider_and_the_row(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item = _local(c, "Item:5", "items")
        assert act(c, OWNER, "item_update", {"item_id": item, "name": "Consulting Pro"}).status == "committed"
        assert act(c, OWNER, "item_update", {"item_id": item, "price": 20_000}).status == "committed"
    assert _drain(books, fake, cid, now=LATER) == []
    # The second write carries the first one's rename: the provider is never sent the old name after it.
    sent = [(body["name"], body["price"]) for op, body, _ in fake.sent if op == "item.update"]
    assert sent[-1] == ("Consulting Pro", 20_000), sent
    with books.read() as c:
        assert tuple(c.execute("SELECT name, price FROM items WHERE id=?", (item,)).fetchone()) == \
            ("Consulting Pro", 20_000)


def test_two_changes_to_a_location_before_the_provider_confirms_both_reach_the_row(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        location = _local(c, "Department:3", "locations")
        assert act(c, OWNER, "location_update", {"location_id": location, "name": "Austin HQ"}).status == "committed"
        assert act(c, OWNER, "location_update", {"location_id": location,
                                                 "address": {"line1": "1 Main St"}}).status == "committed"
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        name, address = c.execute("SELECT name, address FROM locations WHERE id=?", (location,)).fetchone()
    assert (name, json.loads(address)) == ("Austin HQ", {"line1": "1 Main St"})


def _item_sends(fake: FakeBooks) -> list[tuple[str, int]]:
    return [(body["name"], body["price"]) for op, body, _ in fake.sent if op == "item.update"]


def test_a_change_made_after_a_write_is_read_and_before_it_is_claimed_is_sent_with_that_write(books, monkeypatch):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item = _local(c, "Item:5", "items")
        assert act(c, OWNER, "item_update", {"item_id": item, "name": "Consulting Pro"}).status == "committed"
    real_claim = SK.outbox_claim
    added: list[str] = []

    def claim_after_a_change(c: Any, outbox_id: str, at: str) -> bool:
        # The worker read this write from the queue; a price change lands before the worker takes its claim.
        if not added:
            added.append(outbox_id)
            assert act(c, OWNER, "item_update", {"item_id": item, "price": 20_000}).status == "committed"
        return real_claim(c, outbox_id, at)

    monkeypatch.setattr(SK, "outbox_claim", claim_after_a_change)
    assert _drain(books, fake, cid, now=LATER) == []
    assert added, "the price change was never made between the read and the claim"
    assert _item_sends(fake)[-1] == ("Consulting Pro", 20_000), _item_sends(fake)
    with books.read() as c:
        assert tuple(c.execute("SELECT name, price FROM items WHERE id=?", (item,)).fetchone()) == \
            ("Consulting Pro", 20_000)


def test_a_price_change_made_while_a_rename_waits_to_retry_is_sent_after_the_rename_and_never_reverts_it(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item = _local(c, "Item:5", "items")
        assert act(c, OWNER, "item_update", {"item_id": item, "name": "Consulting Pro"}).status == "committed"
    fake.replies.append(mirror.Sent(False, error="the provider is busy"))
    assert _drain(books, fake, cid) == ["item.update: the provider is busy"]
    with books.transaction() as c:
        assert act(c, OWNER, "item_update", {"item_id": item, "price": 20_000}).status == "committed"
        pending = c.execute("SELECT COUNT(*) FROM fin_outbox WHERE op='item.update' AND status='pending'").fetchone()
        # The rename was attempted, so the price change is a write of its own behind it, not a change to the rename.
        assert pending[0] == 2
    # The rename is still backing off: the price change must not go out ahead of it and then be undone by its retry.
    assert _drain(books, fake, cid) == []
    assert _drain(books, fake, cid, now=LATER) == []
    assert _item_sends(fake)[-1] == ("Consulting Pro", 20_000), _item_sends(fake)
    with books.read() as c:
        assert tuple(c.execute("SELECT name, price FROM items WHERE id=?", (item,)).fetchone()) == \
            ("Consulting Pro", 20_000)


def test_a_change_made_after_a_rename_reached_the_provider_with_its_answer_lost_is_not_sent_under_the_renames_key(
        books, monkeypatch):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    # The provider keeps what it was sent under each idempotency key: the same key with another body is refused.
    held: dict[str, dict[str, Any]] = {}
    send = fake.send

    def keyed(op: str, body: Any, key: str) -> mirror.Sent:
        if held.setdefault(key, dict(body)) != body:
            fake.sent.append((op, dict(body), key))
            return mirror.Sent(False, error="the provider holds another request under this key", final=True)
        return send(op, body, key)

    monkeypatch.setattr(fake, "send", keyed)
    with books.transaction() as c:
        item = _local(c, "Item:5", "items")
        assert act(c, OWNER, "item_update", {"item_id": item, "name": "Consulting Pro"}).status == "committed"
    fake.replies.append(mirror.Sent(False, error="the answer was lost"))
    assert _drain(books, fake, cid) == ["item.update: the answer was lost"]
    with books.transaction() as c:
        assert act(c, OWNER, "item_update", {"item_id": item, "price": 20_000}).status == "committed"
    assert _drain(books, fake, cid) == []
    assert _drain(books, fake, cid, now=LATER) == []
    assert _item_sends(fake) == [("Consulting Pro", 15_000), ("Consulting Pro", 15_000),
                                 ("Consulting Pro", 20_000)], _item_sends(fake)
    with books.read() as c:
        assert tuple(c.execute("SELECT name, price FROM items WHERE id=?", (item,)).fetchone()) == \
            ("Consulting Pro", 20_000)


def test_a_change_made_while_a_write_is_in_flight_waits_behind_it_and_is_sent_after_it(books, monkeypatch):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item = _local(c, "Item:5", "items")
        assert act(c, OWNER, "item_update", {"item_id": item, "name": "Consulting Pro"}).status == "committed"
    fake.replies.append(mirror.Sent(False, error="the provider is busy"))
    real_send = mirror._send
    changed: list[str] = []

    def send_while_a_price_change_is_made(books_: Any, o: Any, provider: Any) -> Any:
        # The rename is out at the provider; a price change is made before its answer comes back.
        if not changed and str(o["op"]) == "item.update":
            changed.append(str(o["id"]))
            with books.transaction() as c:
                assert act(c, OWNER, "item_update", {"item_id": item, "price": 20_000}).status == "committed"
        return real_send(books_, o, provider)

    monkeypatch.setattr(mirror, "_send", send_while_a_price_change_is_made)
    assert _drain(books, fake, cid) == ["item.update: the provider is busy"]
    assert changed, "the price change was never made while the rename was in flight"
    assert _drain(books, fake, cid, now=LATER) == []
    # The rename goes first, carrying only its own change; the price change follows it and carries both.
    assert _item_sends(fake) == [("Consulting Pro", 15_000), ("Consulting Pro", 15_000), ("Consulting Pro", 20_000)]
    with books.read() as c:
        assert tuple(c.execute("SELECT name, price FROM items WHERE id=?", (item,)).fetchone()) == \
            ("Consulting Pro", 20_000)


def test_a_teammate_without_the_estimate_lines_is_denied_converting_it_and_not_told_it_is_invoiced(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "estimate_create", {
            "customer_id": _local(c, "Customer:4", "fin_contacts"), "estimate_date": "2026-10-05", "number": "EST-9",
            "lines": [{"item_id": _local(c, "Item:5", "items"), "amount": 1500, "description": "seats"}]})
        assert made.status == "committed", made
    estimate = str(made.result["estimate_id"])
    assert _drain(books, fake, cid, now=LATER) == []
    with books.transaction() as c:
        assert act(c, OWNER, "estimate_send", {"estimate_id": estimate}).status == "committed"
        assert act(c, OWNER, "estimate_accept", {"estimate_id": estimate}).status == "committed"
        assert act(c, OWNER, "estimate_convert", {"estimate_id": estimate}).status == "committed"
    assert _drain(books, fake, cid, now=LATER) == []
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.ar"}).status == "committed"
        refused = act(c, "human:dan", "estimate_convert", {"estimate_id": estimate})
        assert (refused.status, refused.result) == ("denied", {}), refused


def test_a_teammate_without_the_detail_of_a_private_account_cannot_order_on_it_in_a_mirrored_book(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["accounts"].append(acct(4, "6200", "Payroll", "expense"))
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        payroll = _local(c, "Account:4", "fin_accounts")
        assert act(c, OWNER, "private_lines_set", {"account_id": payroll, "others_see": "totals"}).status == \
            "committed"
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.ap"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "expenses",
                                            "whose": "company"}).status == "committed"
        made = act(c, "human:dan", "po_create", {
            "vendor_id": _local(c, "Vendor:9", "fin_contacts"), "po_date": "2026-10-05",
            "lines": [{"account_id": payroll, "amount": 4_000}]}, dry_run=True)
        assert (made.status, made.code, made.result) == ("failed", NOT_FOUND, {}), made


def test_a_purchase_order_made_here_is_written_to_quickbooks_and_its_row_comes_in_once_the_provider_confirms(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        tags = {"location": _local(c, "Department:3", "locations"), "class": _local(c, "Class:7", "fin_dimensions")}
        made = act(c, OWNER, "po_create", {
            "vendor_id": _local(c, "Vendor:9", "fin_contacts"), "po_date": "2026-10-05", "number": "PO-78",
            "lines": [{"item_id": _local(c, "Item:5", "items"), "amount": 1500, "description": "seats",
                       "tags": tags}]})
        assert made.status == "committed", made
        assert c.execute("SELECT COUNT(*) FROM fin_purchase_orders").fetchone()[0] == 0
    assert _drain(books, fake, cid, now=LATER) == []
    assert _sent(fake, "purchaseorder.create")["lines"] == [
        {"account": "Account:2", "amount": 1500, "description": "seats", "item": "Item:5", "item_code": "Consulting",
         "location": "Department:3", "class": "7", "project": ""}]
    with books.read() as c:
        assert tuple(c.execute("SELECT number, status, total FROM fin_purchase_orders").fetchone()) == \
            ("PO-78", "draft", 1500)
        assert c.execute("SELECT COUNT(*) FROM fin_po_lines").fetchone()[0] == 1
        ext = c.execute(
            "SELECT external_id FROM fin_external_refs WHERE local_table='fin_purchase_orders'").fetchone()[0]
        assert str(ext).startswith("PurchaseOrder:")


def test_an_estimate_converted_here_links_its_invoice_and_is_invoiced_once_the_provider_confirms(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "estimate_create", {
            "customer_id": _local(c, "Customer:4", "fin_contacts"), "estimate_date": "2026-10-05", "number": "EST-7",
            "lines": [{"item_id": _local(c, "Item:5", "items"), "amount": 1500, "description": "seats"}]})
        assert made.status == "committed", made
    assert _drain(books, fake, cid, now=LATER) == []
    estimate = str(made.result["estimate_id"])
    with books.transaction() as c:
        assert act(c, OWNER, "estimate_send", {"estimate_id": estimate}).status == "committed"
        assert act(c, OWNER, "estimate_accept", {"estimate_id": estimate}).status == "committed"
        converted = act(c, OWNER, "estimate_convert", {"estimate_id": estimate})
        assert converted.status == "committed", converted
        again = act(c, OWNER, "estimate_convert", {"estimate_id": estimate})
        assert again.status != "committed", again
        assert c.execute("SELECT status FROM fin_estimates WHERE id=?", (estimate,)).fetchone()[0] == "accepted"
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        ext = c.execute("SELECT external_id FROM fin_external_refs WHERE local_table='fin_estimates'").fetchone()[0]
        assert _sent(fake, "invoice.create")["linked_estimate"] == str(ext).partition(":")[2]
        status, invoice = c.execute("SELECT status, invoice_id FROM fin_estimates WHERE id=?", (estimate,)).fetchone()
        assert status == "accepted" and invoice is not None
        assert c.execute("SELECT COUNT(*) FROM fin_invoices WHERE id=?", (invoice,)).fetchone()[0] == 1


def test_a_purchase_order_the_provider_refuses_is_not_booked_here_and_the_reason_is_kept(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "po_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"),
                                           "po_date": "2026-10-05", "lines": [{"account_id": _local(
                                               c, "Account:2", "fin_accounts"), "amount": 900, "description": "pens"}]})
        assert made.status == "committed", made
    fake.replies.append(mirror.Sent(False, error="Vendor is inactive", final=True))
    problems = _drain(books, fake, cid, now=LATER)
    assert any("Vendor is inactive" in p for p in problems), problems
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_purchase_orders").fetchone()[0] == 0
        row = c.execute("SELECT status, last_error FROM fin_outbox WHERE op='purchaseorder.create'").fetchone()
        assert tuple(row) == ("failed", "Vendor is inactive")


def test_a_mirrored_item_needs_a_price_and_keeps_the_price_the_provider_holds(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item = _local(c, "Item:5", "items")
        unpriced = act(c, OWNER, "item_create", {"name": "Design", "kind": "service"})
        cleared = act(c, OWNER, "item_update", {"item_id": item, "price": None})
        assert (unpriced.status, unpriced.code) == ("failed", "invalid"), unpriced
        assert (cleared.status, cleared.code) == ("failed", "invalid"), cleared
    assert _drain(books, fake, cid, now=LATER) == []
    assert [op for op, _, _ in fake.sent if op in ("item.create", "item.update")] == []
    with books.read() as c:
        assert c.execute("SELECT price FROM items WHERE id=?", (item,)).fetchone()[0] == 15_000


def test_a_mirrored_item_keeps_the_accounts_the_provider_holds(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["accounts"].append(acct(4, "6200", "Travel"))
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item = _local(c, "Item:5", "items")
        travel = _local(c, "Account:4", "fin_accounts")
        moved = act(c, OWNER, "item_update", {"item_id": item, "expense_account_id": travel})
        cleared = act(c, OWNER, "item_update", {"item_id": item, "expense_account_id": None})
        income_cleared = act(c, OWNER, "item_update", {"item_id": item, "income_account_id": None})
        assert [(r.status, r.code) for r in (moved, cleared, income_cleared)] == [("failed", "invalid")] * 3, moved
        assert "change them in the books" in moved.reason, moved
        assert c.execute("SELECT COUNT(*) FROM fin_outbox WHERE op='item.update'").fetchone()[0] == 0
    assert _drain(books, fake, cid, now=LATER) == []
    assert [op for op, _, _ in fake.sent if op == "item.update"] == []
    with books.read() as c:
        accounts = c.execute("SELECT income_account_id, expense_account_id FROM items WHERE id=?", (item,)).fetchone()
        assert tuple(accounts) == (_local(c, "Account:3", "fin_accounts"), _local(c, "Account:2", "fin_accounts"))
    # the provider's own change to the item's accounts still reaches the row
    fake.changes = [M.Item(M.Ext("Item", "5", "2"), "Consulting", kind="service", price=15000, income="Account:3",
                           expense="Account:4")]
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.read() as c:
        assert c.execute("SELECT expense_account_id FROM items WHERE id=?", (item,)).fetchone()[0] == \
            _local(c, "Account:4", "fin_accounts")


def test_a_mirrored_item_keeps_the_sku_the_provider_holds_and_takes_another(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["records"][0] = M.Item(M.Ext("Item", "5", "1"), "Consulting", sku="CONS-1", kind="service",
                                        price=15000, income="Account:3", expense="Account:2")
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item = _local(c, "Item:5", "items")
        cleared = act(c, OWNER, "item_update", {"item_id": item, "sku": ""})
        assert (cleared.status, cleared.code) == ("failed", "invalid"), cleared
        assert c.execute("SELECT sku FROM items WHERE id=?", (item,)).fetchone()[0] == "CONS-1"
    assert _drain(books, fake, cid, now=LATER) == []
    assert [op for op, _, _ in fake.sent if op == "item.update"] == []
    with books.transaction() as c:
        changed = act(c, OWNER, "item_update", {"item_id": item, "sku": "CONS-2"})
        assert changed.status == "committed", changed
    assert _drain(books, fake, cid, now=LATER) == []
    assert _sent(fake, "item.update")["sku"] == "CONS-2"


def test_renaming_an_imported_job_in_a_mirrored_book_is_written_to_the_provider_before_the_books_change(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        job = _local(c, "Project:12", "projects")
        renamed = act(c, OWNER, "project_update", {"project_id": job, "name": "Kitchen remodel 2"})
        assert renamed.status == "committed", renamed
    with books.read() as c:
        assert c.execute("SELECT name FROM projects WHERE id=?", (job,)).fetchone()[0] == "Kitchen remodel"
    fake.replies.append(mirror.Sent(True, kind="Project", external_id="12", revision="2"))
    assert _drain(books, fake, cid, now=LATER) == []
    sent = [(op, body) for op, body, _ in fake.sent if op == "project.update"]
    assert [(body["name"], body["ext"], body["revision"]) for _, body in sent] == \
        [("Kitchen remodel 2", "Project:12", "1")]
    with books.read() as c:
        assert c.execute("SELECT name FROM projects WHERE id=?", (job,)).fetchone()[0] == "Kitchen remodel 2"
        assert c.execute("SELECT revision FROM fin_external_refs WHERE external_id='Project:12'").fetchone()[0] == "2"


def test_a_job_rename_the_provider_refuses_leaves_the_local_name_and_asks_the_owner(books):
    """X9 for a job: the provider's refusal is the reason a person reads; the local job keeps its name until it is
    accepted."""
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        job = _local(c, "Project:12", "projects")
        act(c, OWNER, "project_update", {"project_id": job, "name": "Kitchen remodel 2"})
    fake.replies.append(mirror.Sent(False, error="Customer name is in use", final=True))
    assert any("Customer name is in use" in p for p in _drain(books, fake, cid, now=LATER))
    with books.read() as c:
        assert c.execute("SELECT name FROM projects WHERE id=?", (job,)).fetchone()[0] == "Kitchen remodel"
        assert c.execute("SELECT status FROM fin_outbox WHERE op='project.update'").fetchone()[0] == "failed"


def test_a_kind_change_across_the_item_type_the_books_hold_is_refused_and_one_inside_it_stays_here(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        stock = act(c, OWNER, "item_create", {"name": "Widget", "kind": "inventory", "price": 500})
        rod = act(c, OWNER, "item_create", {"name": "Steel rod", "kind": "non_inventory", "price": 500})
        assert (stock.status, rod.status) == ("committed", "committed"), (stock, rod)
    fake.replies = [mirror.Sent(True, kind="Item", external_id="6", revision="0"),
                    mirror.Sent(True, kind="Item", external_id="7", revision="0")]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.transaction() as c:
        widget, steel = _local(c, "Item:6", "items"), _local(c, "Item:7", "items")
        refused = [act(c, OWNER, "item_update", {"item_id": widget, "kind": "service"}),
                   act(c, OWNER, "item_update", {"item_id": _local(c, "Item:5", "items"), "kind": "non_inventory"}),
                   act(c, OWNER, "item_update", {"item_id": steel, "kind": "inventory"})]
        inside = act(c, OWNER, "item_update", {"item_id": steel, "kind": "raw_material"})
    assert [(r.status, r.code) for r in refused] == [("failed", "invalid")] * 3, refused
    assert "books hold as Inventory" in refused[0].reason and "books hold as Service" in refused[1].reason, refused
    assert inside.status == "committed", inside
    assert _drain(books, fake, cid, now=LATER2) == []
    assert [body["kind"] for op, body, _ in fake.sent if op == "item.update"] == ["raw_material"]
    with books.read() as c:
        kinds = dict(c.execute("SELECT name, kind FROM items WHERE name IN ('Widget', 'Consulting', 'Steel rod')"))
    assert kinds == {"Widget": "inventory", "Consulting": "service", "Steel rod": "raw_material"}, kinds


def test_a_record_brought_in_first_keeps_its_row_and_its_write_resolves_to_it(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "item_create", {"name": "Design", "kind": "service", "price": 9000})
        assert made.status == "committed", made
        held = RS.insert(c, "items", {"name": "Design", "kind": "service", "price": 9000, "created_at": AT,
                                      "created_by_id": OWNER, "updated_at": AT, "updated_by_id": OWNER})
        SX.ref_insert(c, cid=cid, local_table="items", local_id=held, kind="Item", external_id="Item:99",
                      revision="1", status="active", at=AT, by=OWNER)
    fake.replies.append(mirror.Sent(True, kind="Item", external_id="99", revision="1"))
    assert _drain(books, fake, cid, now=LATER) == []
    kept = str(made.result["item_id"])
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM items WHERE name='Design'").fetchone()[0] == 1
        assert c.execute("SELECT price FROM items WHERE id=?", (held,)).fetchone()[0] == 9000
        assert c.execute("SELECT record_id FROM record_aliases WHERE alias_id=?", (kept,)).fetchone()[0] == held
        assert RR.get(c, "items", kept)["id"] == held
        ref = SX.ref(c, cid, "items", "Item:99")
        assert (ref["local_id"], ref["status"]) == (held, "active")


def test_a_location_the_feed_brought_in_first_is_not_deleted_when_its_write_is_confirmed(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        held = _local(c, "Department:3", "locations")
        made = act(c, OWNER, "location_create", {"name": "Dallas"})
        assert made.status == "committed", made
    fake.replies.append(mirror.Sent(True, kind="Department", external_id="3", revision="2"))
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM locations WHERE id=?", (held,)).fetchone()[0] == 1
        assert c.execute("SELECT record_id FROM record_aliases WHERE alias_id=?",
                         (str(made.result["location_id"]),)).fetchone()[0] == held
        ref = SX.ref(c, cid, "locations", "Department:3")
        assert (ref["local_id"], ref["status"]) == (held, "active")


def test_a_bill_queued_on_an_item_the_feed_brought_in_still_sends_after_the_items_write_is_confirmed(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        held = _local(c, "Item:5", "items")
        made = act(c, OWNER, "item_create", {"name": "Consulting", "kind": "service", "price": 15000})
        assert made.status == "committed", made
        billed = act(c, OWNER, "bill_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"),
                                               "bill_date": "2026-10-05", "number": "B-90",
                                               "lines": [{"item_id": held, "amount": 1500}]})
        assert billed.status == "committed", billed
    fake.replies.append(mirror.Sent(True, kind="Item", external_id="5", revision="2"))
    assert _drain(books, fake, cid, now=LATER) == []
    assert _sent(fake, "bill.create")["lines"][0]["item"] == "Item:5"
    with books.read() as c:
        assert c.execute("SELECT id FROM items WHERE id=?", (held,)).fetchone() is not None
        assert c.execute("SELECT status FROM fin_outbox WHERE op='bill.create'").fetchone()[0] == "sent"


def test_a_journal_line_for_a_job_is_to_the_jobs_customer_and_tagged_with_the_job(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["entries"] = [M.Journal(M.Ext("JournalEntry", "81", "1"), "2026-10-05", "Deck labor", "sync", (
        M.EntryLine("Account:2", 1500, party="Customer:12", description="labor", key="1"),
        M.EntryLine("Account:1", -1500, key="2")))]
    cid = _connection(books)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        entry = _entry_of(c, "JournalEntry:81")
        line_id, contact = c.execute("SELECT id, contact_id FROM fin_lines WHERE entry_id=? AND account_id=?",
                                     (entry, _local(c, "Account:2", "fin_accounts"))).fetchone()
        assert contact == _local(c, "Customer:4", "fin_contacts")
        tags = {(str(k), str(v)) for k, v in c.execute(
            "SELECT kind, value FROM fin_line_tags WHERE line_id=?", (line_id,))}
        assert tags == {("project", _local(c, "Project:12", "projects"))}


def test_a_payment_for_a_job_is_booked_to_the_jobs_customer(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["documents"] = [M.Document(
        M.Ext("Invoice", "31", "1"), party="Customer:4", number="I-31", day="2026-10-06", due="2026-11-06",
        total=1500, lines=(M.EntryLine("Account:3", 1500, description="work", key="8", item="Item:5"),))]
    fake.records["payments"] = [M.Settlement(
        M.Ext("Payment", "40", "1"), day="2026-10-12", memo="Deck payment", party="Customer:12", bank="Account:1",
        total=1500, links=(("Invoice:31", 1500),))]
    cid = _connection(books)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        pay = _entry_of(c, "Payment:40")
        contacts = {str(r[0]) for r in c.execute(
            "SELECT contact_id FROM fin_lines WHERE entry_id=? AND contact_id <> ''", (pay,))}
        assert contacts == {_local(c, "Customer:4", "fin_contacts")}


def test_a_bill_made_here_against_a_purchase_order_line_bills_it_and_closes_the_order_once_confirmed(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        tags = {"location": _local(c, "Department:3", "locations"), "class": _local(c, "Class:7", "fin_dimensions")}
        made = act(c, OWNER, "po_create", {
            "vendor_id": _local(c, "Vendor:9", "fin_contacts"), "po_date": "2026-10-05", "number": "PO-79",
            "lines": [{"item_id": _local(c, "Item:5", "items"), "amount": 1500, "description": "seats",
                       "tags": tags}]})
        assert made.status == "committed", made
    assert _drain(books, fake, cid, now=LATER) == []
    po = str(made.result["po_id"])
    with books.transaction() as c:
        assert act(c, OWNER, "po_send", {"po_id": po}).status == "committed"
        assert act(c, OWNER, "po_approve", {"po_id": po}).status == "committed"
        line = str(c.execute("SELECT id FROM fin_po_lines WHERE po_id=?", (po,)).fetchone()[0])
        billed = act(c, OWNER, "bill_create", {
            "vendor_id": _local(c, "Vendor:9", "fin_contacts"), "bill_date": "2026-10-05", "number": "B-79",
            "lines": [{"po_line_id": line, "amount": 1500}]})
        assert billed.status == "committed", billed
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert c.execute("SELECT l.po_line_id FROM fin_bill_lines l JOIN fin_bills b ON b.id=l.bill_id "
                         "WHERE b.number='B-79'").fetchone()[0] == line
        assert c.execute("SELECT status FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()[0] == "closed"


LATER2 = "2026-10-15T14:00:00.000000Z"
LATER3 = "2026-10-15T15:00:00.000000Z"


def _approved_order(books, fake: FakeBooks, cid: str, amount: int) -> tuple[str, str]:
    """A purchase order made here, confirmed by the provider and approved: (order id, its one line id)."""
    with books.transaction() as c:
        made = act(c, OWNER, "po_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"), "po_date": "2026-10-05",
                                           "lines": [{"item_id": _local(c, "Item:5", "items"), "amount": amount,
                                                      "description": "seats"}]})
        assert made.status == "committed", made
    assert _drain(books, fake, cid, now=LATER) == []
    po = str(made.result["po_id"])
    with books.transaction() as c:
        assert act(c, OWNER, "po_send", {"po_id": po}).status == "committed"
        assert act(c, OWNER, "po_approve", {"po_id": po}).status == "committed"
        line = str(c.execute("SELECT id FROM fin_po_lines WHERE po_id=?", (po,)).fetchone()[0])
    return po, line


def _bill_body(c, po_line: str, amount: int, number: str) -> dict[str, Any]:
    """A queued bill's payload (local ids), billing an order line."""
    return {"party": _local(c, "Vendor:9", "fin_contacts"), "number": number, "day": "2026-10-05", "due": "", "memo":
    "",
            "lines": [{"account_id": _local(c, "Account:2", "fin_accounts"), "amount": amount, "description": "seats",
                       "item_id": None, "tags": {}, "po_line_id": po_line}]}


def test_two_bills_made_here_against_one_order_line_cannot_bill_more_than_it_has_left(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    _, line = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        first = act(c, OWNER, "bill_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"),
                                              "bill_date": "2026-10-05", "number": "B-81",
                                              "lines": [{"po_line_id": line, "amount": 6_000}]})
        assert first.status == "committed", first
        second = act(c, OWNER, "bill_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"),
                                               "bill_date": "2026-10-05", "number": "B-82",
                                               "lines": [{"po_line_id": line, "amount": 6_000}]})
        assert second.code == "invalid" and "40.00" in str(second.reason), second


def test_a_write_sent_after_another_took_the_same_order_line_room_is_refused_when_its_turn_comes(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    _, line = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=_bill_body(c, line, 6_000, "B-81"),
                      key="bill.create:race-1", by=OWNER, at=AT)
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=_bill_body(c, line, 6_000, "B-82"),
                      key="bill.create:race-2", by=OWNER, at=AT)
    assert _drain(books, fake, cid, now=LATER2) == []
    assert [body["number"] for op, body, _ in fake.sent if op == "bill.create"] == ["B-81"]
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_outbox WHERE idempotency_key='bill.create:race-2'").fetchone()[0] == \
            "failed"
        assert c.execute("SELECT COALESCE(SUM(amount), 0) FROM fin_bill_lines WHERE po_line_id=?",
                         (line,)).fetchone()[0] == 6_000


def test_a_write_that_may_have_landed_keeps_its_order_line_room_from_the_write_waiting_behind_it(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    _, line = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=_bill_body(c, line, 6_000, "B-91"),
                      key="bill.create:unknown-1", by=OWNER, at=AT)
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=_bill_body(c, line, 6_000, "B-92"),
                      key="bill.create:unknown-2", by=OWNER, at=AT)
    # The provider did not answer for the first write, so it may have billed the line.
    fake.replies = [mirror.Sent(False, final=True, unknown=True, error="the provider did not answer")]
    _drain(books, fake, cid)
    assert [body["number"] for op, body, _ in fake.sent if op == "bill.create"] == ["B-91"]
    with books.read() as c:
        assert tuple(c.execute("SELECT status, unknown FROM fin_outbox WHERE idempotency_key='bill.create:unknown-1'"
                               ).fetchone()) == ("failed", 1)
        row = c.execute("SELECT status FROM fin_outbox WHERE idempotency_key='bill.create:unknown-2'").fetchone()
        assert row[0] == "failed"


def test_a_new_bill_cannot_take_order_line_room_an_unknown_write_may_have_billed(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    _, line = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=_bill_body(c, line, 6_000, "B-93"),
                      key="bill.create:unknown-3", by=OWNER, at=AT)
    fake.replies = [mirror.Sent(False, final=True, unknown=True, error="the provider did not answer")]
    _drain(books, fake, cid)
    with books.transaction() as c:
        second = act(c, OWNER, "bill_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"),
                                               "bill_date": "2026-10-05", "number": "B-94",
                                               "lines": [{"po_line_id": line, "amount": 6_000}]})
        assert second.code == "invalid" and "40.00" in str(second.reason), second


def test_a_bill_found_at_a_newer_revision_on_retry_takes_its_order_line_room_when_it_is_recorded(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    _, line = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=_bill_body(c, line, 6_000, "B-95"),
                      key="bill.create:found-1", by=OWNER, at=AT)
    # The first call was cut off after the provider took the bill, and the bill was changed there before the retry.
    fake.replies = [mirror.Sent(False, error="QuickBooks did not answer")]
    _drain(books, fake, cid, now=LATER2)
    fake.found["bill.create:found-1"] = mirror.Sent(True, kind="Bill", external_id="79", revision="2", lines=("L1",))
    assert _drain(books, fake, cid, now=LATER3) == []
    assert [op for op, _, _ in fake.sent if op == "bill.create"] == ["bill.create"]
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_outbox WHERE idempotency_key='bill.create:found-1'").fetchone()[0] == \
            "sent"
        assert c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_bill_lines l JOIN fin_bills b ON b.id=l.bill_id "
                         "WHERE b.number='B-95' AND l.po_line_id=?", (line,)).fetchone()[0] == 6_000
    with books.transaction() as c:
        second = act(c, OWNER, "bill_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"),
                                               "bill_date": "2026-10-05", "number": "B-96",
                                               "lines": [{"po_line_id": line, "amount": 6_000}]})
        assert second.code == "invalid" and "40.00" in str(second.reason), second
    # The change feed then reads the newer record in: it replaces the bill and keeps the order line it was made against.
    fake.changes = [M.Document(M.Ext("Bill", "79", "2"), party="Vendor:9", number="B-95", day="2026-10-05", due="",
                               total=6_000, lines=(M.EntryLine("Account:2", 6_000, description="seats", key="L1"),))]
    assert _drain(books, fake, cid, now=LATER3) == []
    with books.read() as c:
        assert c.execute("SELECT COALESCE(SUM(amount), 0) FROM fin_bill_lines WHERE po_line_id=?",
                         (line,)).fetchone()[0] == 6_000


def test_a_bill_the_provider_bills_for_less_keeps_its_order_line_billed_until_the_bill_is_deleted(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": _local(c, "Account:2", "fin_accounts"),
                                               "amount": 10_000, "at_limit": "decline", "parent_id": None})
        assert made.status == "committed", made
    po, line = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=_bill_body(c, line, 4_000, "B-88"),
                      key="bill.create:less-1", by=OWNER, at=AT)
    fake.replies = [mirror.Sent(True, kind="Bill", external_id="88", revision="1", lines=("L1",))]
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.read() as c:
        assert c.execute("SELECT amount FROM fin_holds WHERE source='purchase_order' AND status='held' AND "
                         "source_id LIKE ?", (f"{po}:%",)).fetchone()[0] == 6_000
    # The provider now holds the bill at 2,000, but a revision never gives room back: the line still holds 6,000 of
    # its 10,000 until the bill is deleted.
    fake.changes = [M.Document(M.Ext("Bill", "88", "2"), party="Vendor:9", number="B-88", day="2026-10-05", due="",
                               total=2_000, lines=(M.EntryLine("Account:2", 2_000, description="seats", key="L1"),))]
    assert _drain(books, fake, cid, now=LATER3) == []
    with books.read() as c:
        assert c.execute("SELECT amount FROM fin_holds WHERE source='purchase_order' AND status='held' AND "
                         "source_id LIKE ?", (f"{po}:%",)).fetchone()[0] == 6_000
        assert SP.billed(c, line) == 4_000
        assert c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_bill_lines l JOIN fin_bills b ON b.id=l.bill_id "
                         "WHERE b.number='B-88'").fetchone()[0] == 2_000
    fake.changes = [M.Ext("Bill", "88", "3", True)]
    assert _drain(books, fake, cid, now="2026-10-15T16:00:00.000000Z", passes=1) == []
    with books.read() as c:
        assert SP.line_room(c, line) == 10_000


def test_a_line_tagged_to_a_site_kept_in_tico_keeps_the_site_when_the_provider_revises_the_bill(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "location_create", {"name": "Acme Site", "kind": "customer_site"})
        assert made.status == "committed", made
        site = str(made.result["location_id"])
        billed = act(c, OWNER, "bill_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"),
                                               "bill_date": "2026-10-05", "number": "B-90",
                                               "lines": [{"account_id": _local(c, "Account:2", "fin_accounts"),
                                                          "amount": 4_000, "tags": {"location": site}}]})
        assert billed.status == "committed", billed
    fake.replies = [mirror.Sent(True, kind="Bill", external_id="90", revision="0", lines=("L1",))]
    assert _drain(books, fake, cid, now=LATER2) == []
    # The provider's memo edit comes in as a newer revision. It names no location, and the site is not in the books.
    fake.changes = [M.Document(M.Ext("Bill", "90", "1"), party="Vendor:9", number="B-90", day="2026-10-05", due="",
                               total=4_000, memo="edited in the provider",
                               lines=(M.EntryLine("Account:2", 4_000, description="seats", key="L1"),))]
    assert _drain(books, fake, cid, now=LATER3) == []
    with books.read() as c:
        bill = _local(c, "Bill:90", "fin_bills")
        assert c.execute("SELECT revision FROM fin_external_refs WHERE external_id='Bill:90'").fetchone()[0] == "1"
        assert [json.loads(r[0]) for r in c.execute("SELECT tags_json FROM fin_bill_lines WHERE bill_id=?",
                                                    (bill,))] == [{"location": site}]
        assert c.execute("SELECT SUM(l.amount) FROM fin_lines l JOIN fin_line_tags t ON t.line_id=l.id "
                         "WHERE t.kind='location' AND t.value=?", (site,)).fetchone()[0] == 4_000


def test_a_location_made_here_keeps_its_kind_and_address_when_the_sync_imported_it_first(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "location_create", {"name": "Acme Depot", "kind": "warehouse",
                                                 "address": {"city": "Denver"}})
        assert made.status == "committed", made
        site = str(made.result["location_id"])
        key = str(c.execute("SELECT idempotency_key FROM fin_outbox WHERE op='location.create'").fetchone()[0])
    # The call was cut off after the provider made the department.
    fake.replies = [mirror.Sent(False, error="QuickBooks did not answer")]
    _drain(books, fake, cid, now=LATER2)
    # The change feed brings the department in first, with the books' defaults: no kind and no address.
    fake.changes = [M.Location(M.Ext("Department", "3", "0"), name="Acme Depot")]
    assert _drain(books, fake, cid, now=LATER2) == []
    fake.found[key] = mirror.Sent(True, kind="Department", external_id="3", revision="0")
    assert _drain(books, fake, cid, now=LATER3) == []
    with books.read() as c:
        row = c.execute("SELECT kind, address FROM locations WHERE id=?",
                        (_local(c, "Department:3", "locations"),)).fetchone()
        assert (row[0], json.loads(row[1])) == ("warehouse", {"city": "Denver"})
        assert RS.resolve(c, "locations", site) == _local(c, "Department:3", "locations")


def test_a_location_made_here_keeps_its_id_alias_and_fields_when_it_is_recovered_at_a_newer_revision(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "location_create", {"name": "Acme Depot", "kind": "warehouse",
                                                 "address": {"city": "Denver"}})
        assert made.status == "committed", made
        site = str(made.result["location_id"])
        key = str(c.execute("SELECT idempotency_key FROM fin_outbox WHERE op='location.create'").fetchone()[0])
    fake.replies = [mirror.Sent(False, error="QuickBooks did not answer")]
    _drain(books, fake, cid, now=LATER2)
    # The feed brings the department in first, under its own id, and the provider has changed it since the write.
    fake.changes = [M.Location(M.Ext("Department", "3", "0"), name="Acme Depot")]
    assert _drain(books, fake, cid, now=LATER2) == []
    fake.found[key] = mirror.Sent(True, kind="Department", external_id="3", revision="1")
    assert _drain(books, fake, cid, now=LATER3) == []
    with books.read() as c:
        held = _local(c, "Department:3", "locations")
        row = c.execute("SELECT kind, address FROM locations WHERE id=?", (held,)).fetchone()
        assert (row[0], json.loads(row[1])) == ("warehouse", {"city": "Denver"})
        assert RS.resolve(c, "locations", site) == held


def test_an_import_after_a_local_kind_change_keeps_the_location_kind(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        changed = act(c, OWNER, "location_update", {"location_id": _local(c, "Department:3", "locations"),
                                                    "kind": "warehouse"})
        assert changed.status == "committed", changed
    assert _drain(books, fake, cid, now=LATER) == []
    fake.changes = [M.Location(M.Ext("Department", "3", "2"), "Austin HQ")]
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.read() as c:
        row = c.execute("SELECT name, kind FROM locations WHERE id=?", (_local(c, "Department:3", "locations"),)
                        ).fetchone()
        assert tuple(row) == ("Austin HQ", "warehouse")


def test_a_customer_site_made_in_a_mirrored_book_is_sent_nowhere_and_a_bill_line_naming_it_sends_no_department(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        site = act(c, OWNER, "location_create", {"name": "Dallas site", "kind": "customer_site"})
        assert site.status == "committed", site
        made = act(c, OWNER, "bill_create", {
            "vendor_id": _local(c, "Vendor:9", "fin_contacts"), "number": "B-79", "bill_date": "2026-10-05",
            "due_date": "2026-11-05",
            "lines": [{"item_id": _local(c, "Item:5", "items"), "amount": 1500, "description": "site visit",
                       "tags": {"location": site.result["location_id"]}}]})
        assert made.status == "committed", made
        renamed = act(c, OWNER, "location_update", {"location_id": site.result["location_id"], "name": "Depot"})
        assert renamed.status == "committed", renamed
        found = c.execute("SELECT name FROM locations WHERE id=?", (site.result["location_id"],)).fetchone()
        assert found[0] == "Depot"
    assert _drain(books, fake, cid, now=LATER) == []
    assert [op for op, _, _ in fake.sent if op.startswith("location.")] == []
    body = _sent(fake, "bill.create")
    assert body["lines"][0]["location"] == ""
    assert "DepartmentRef" not in repr(Q.bill_body(body))


def test_a_raw_material_item_goes_to_the_books_as_a_plain_item_and_keeps_its_label(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "item_create", {"name": "Steel rod", "kind": "raw_material", "price": 500})
        assert made.status == "committed", made
    fake.replies = [mirror.Sent(True, kind="Item", external_id="6", revision="0")]
    assert _drain(books, fake, cid) == []
    body = _sent(fake, "item.create")
    assert Q.item_write(body)["Type"] == "NonInventory"
    assert X.item_body(body)["Name"] == "Steel rod"
    with books.read() as c:
        row = c.execute("SELECT name, kind FROM items WHERE id=?", (_local(c, "Item:6", "items"),)).fetchone()
        assert tuple(row) == ("Steel rod", "raw_material")


def test_a_raw_material_item_keeps_its_label_when_the_books_later_hold_it_as_a_plain_item(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "item_create", {"name": "Steel rod", "kind": "raw_material", "price": 500})
        assert made.status == "committed", made
    fake.replies = [mirror.Sent(True, kind="Item", external_id="6", revision="0")]
    assert _drain(books, fake, cid) == []
    # The books hold a raw material as a non-inventory item, and a later provider revision renames it there.
    fake.changes = [M.Item(M.Ext("Item", "6", "1"), "Steel rod, cut", kind="non_inventory", price=500)]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        row = c.execute("SELECT name, kind FROM items WHERE id=?", (_local(c, "Item:6", "items"),)).fetchone()
        assert tuple(row) == ("Steel rod, cut", "raw_material")


def test_a_raw_material_item_keeps_its_label_when_the_sync_imports_it_before_the_write_is_recovered(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "item_create", {"name": "Steel rod", "kind": "raw_material", "price": 500})
        assert made.status == "committed", made
        key = str(c.execute("SELECT idempotency_key FROM fin_outbox WHERE op='item.create'").fetchone()[0])
    # The call was cut off after the provider made the item.
    fake.replies = [mirror.Sent(False, error="QuickBooks did not answer")]
    _drain(books, fake, cid, now=LATER2)
    # The change feed brings the item in first, as the books hold a raw material: a plain non-inventory item.
    fake.changes = [M.Item(M.Ext("Item", "6", "0"), "Steel rod", kind="non_inventory", price=500)]
    assert _drain(books, fake, cid, now=LATER2) == []
    fake.found[key] = mirror.Sent(True, kind="Item", external_id="6", revision="0")
    assert _drain(books, fake, cid, now=LATER3) == []
    with books.read() as c:
        row = c.execute("SELECT name, kind FROM items WHERE id=?", (_local(c, "Item:6", "items"),)).fetchone()
        assert tuple(row) == ("Steel rod", "raw_material")


def test_a_bill_the_feed_brought_in_before_its_write_was_confirmed_takes_the_order_line_it_was_sent_with(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    _, line = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=_bill_body(c, line, 4_000, "B-77"),
                      key="bill.create:link-1", by=OWNER, at=AT)
    # The provider took the bill and the change feed reads it in before the answer to the write is recorded.
    fake.changes = [M.Document(M.Ext("Bill", "77", "0"), party="Vendor:9", number="B-77", day="2026-10-05", due="",
                               total=4_000, lines=(M.EntryLine("Account:2", 4_000, description="seats", key="L1"),))]
    fake.replies = [mirror.Sent(True, kind="Bill", external_id="77", revision="0", lines=("L1",))]
    assert mirror.sync_once(books, fake, cid, LATER2) == []
    with books.read() as c:
        assert c.execute("SELECT l.po_line_id FROM fin_bill_lines l JOIN fin_bills b ON b.id=l.bill_id "
                         "WHERE b.number='B-77'").fetchone()[0] == line
        assert c.execute("SELECT status FROM fin_outbox WHERE idempotency_key='bill.create:link-1'").fetchone()[0] == \
            "sent"


def test_a_provider_edit_keeps_the_order_line_a_confirmed_bill_was_made_against(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    po, line = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=_bill_body(c, line, 4_000, "B-77"),
                      key="bill.create:edit-1", by=OWNER, at=AT)
    fake.replies = [mirror.Sent(True, kind="Bill", external_id="77", revision="0", lines=("L1",))]
    assert _drain(books, fake, cid, now=LATER2, passes=1) == []
    fake.changes = [M.Document(M.Ext("Bill", "77", "1"), party="Vendor:9", number="B-77", day="2026-10-05", due="",
                               total=4_000, memo="edited in the provider",
                               lines=(M.EntryLine("Account:2", 4_000, description="seats", key="L1"),))]
    assert _drain(books, fake, cid, now=LATER3, passes=1) == []
    with books.read() as c:
        assert c.execute("SELECT l.po_line_id FROM fin_bill_lines l JOIN fin_bills b ON b.id=l.bill_id "
                         "WHERE b.number='B-77'").fetchone()[0] == line
        assert c.execute("SELECT COALESCE(SUM(amount), 0) FROM fin_bill_lines WHERE po_line_id=?",
                         (line,)).fetchone()[0] == 4_000
        assert c.execute("SELECT status FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()[0] == "approved"


def test_a_revision_that_changes_an_unkeyed_bill_line_keeps_its_order_line_and_bills_the_new_amount(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    _, line = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=_bill_body(c, line, 4_000, "B-103"),
                      key="bill.create:unkeyed-1", by=OWNER, at=AT)
    fake.replies = [mirror.Sent(True, kind="Bill", external_id="103", revision="0")]
    assert _drain(books, fake, cid, now=LATER2, passes=1) == []
    # The provider changes the amount and sends the same one line without an id: it is still that line.
    fake.changes = [M.Document(M.Ext("Bill", "103", "1"), party="Vendor:9", number="B-103", day="2026-10-05", due="",
                               total=3_000, lines=(M.EntryLine("Account:2", 3_000, description="seats"),))]
    assert _drain(books, fake, cid, now=LATER3, passes=1) == []
    with books.read() as c:
        assert [tuple(r) for r in c.execute("SELECT l.po_line_id, l.amount FROM fin_bill_lines l JOIN fin_bills b "
                                            "ON b.id=l.bill_id WHERE b.number='B-103'")] == [(line, 3_000)]
        assert c.execute("SELECT COUNT(*) FROM events WHERE action='finance.mirror.order_link_unmatched'"
                         ).fetchone()[0] == 0


def test_a_revision_that_splits_a_bill_line_it_cannot_match_keeps_the_order_line_billed_and_audits_the_link(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": _local(c, "Account:2", "fin_accounts"),
                                               "amount": 10_000, "at_limit": "decline", "parent_id": None})
        assert made.status == "committed", made
    _, line = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=_bill_body(c, line, 6_000, "B-104"),
                      key="bill.create:split-1", by=OWNER, at=AT)
    fake.replies = [mirror.Sent(True, kind="Bill", external_id="104", revision="0", lines=("L1",))]
    assert _drain(books, fake, cid, now=LATER2, passes=1) == []
    # The provider splits the line into two under new ids: neither is the line it had, so the order line keeps the
    # 6,000 the bill billed on it.
    fake.changes = [M.Document(M.Ext("Bill", "104", "1"), party="Vendor:9", number="B-104", day="2026-10-05", due="",
                               total=6_000, lines=(M.EntryLine("Account:2", 3_000, description="seats", key="A"),
                                                   M.EntryLine("Account:2", 3_000, description="seats", key="B")))]
    assert _drain(books, fake, cid, now=LATER3, passes=1) == []
    with books.transaction() as c:
        assert SP.billed(c, line) == 6_000
        second = act(c, OWNER, "bill_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"),
                                               "bill_date": "2026-10-05", "number": "B-105",
                                               "lines": [{"po_line_id": line, "amount": 6_000}]})
        assert second.code == "invalid" and "40.00" in str(second.reason), second
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE action='finance.mirror.order_link_unmatched'"
                         ).fetchone()[0] == 1


def test_a_bill_the_provider_deletes_after_a_split_gives_back_all_the_order_line_it_kept(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": _local(c, "Account:2", "fin_accounts"),
                                               "amount": 10_000, "at_limit": "decline", "parent_id": None})
        assert made.status == "committed", made
    _, line = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=_bill_body(c, line, 6_000, "B-107"),
                      key="bill.create:gone-1", by=OWNER, at=AT)
    fake.replies = [mirror.Sent(True, kind="Bill", external_id="107", revision="0", lines=("L1",))]
    assert _drain(books, fake, cid, now=LATER2, passes=1) == []
    fake.changes = [M.Document(M.Ext("Bill", "107", "1"), party="Vendor:9", number="B-107", day="2026-10-05", due="",
                               total=6_000, lines=(M.EntryLine("Account:2", 3_000, description="seats", key="A"),
                                                   M.EntryLine("Account:2", 3_000, description="seats", key="B")))]
    assert _drain(books, fake, cid, now=LATER3, passes=1) == []
    fake.changes = [M.Ext("Bill", "107", "2", True)]
    assert _drain(books, fake, cid, now="2026-10-15T16:00:00.000000Z", passes=1) == []
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_bills WHERE number='B-107'").fetchone()[0] == "void"
        assert SP.billed(c, line) == 0
        assert SP.line_room(c, line) == 10_000


def test_a_bill_recovered_from_the_feed_keeps_each_line_by_its_provider_id_when_the_lines_are_reordered(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    _, first = _approved_order(books, fake, cid, 10_000)
    _, second = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        payload = _bill_body(c, first, 6_000, "B-106")
        payload["lines"].append({"account_id": _local(c, "Account:2", "fin_accounts"), "amount": 4_000,
                                 "description": "seats", "item_id": None, "tags": {}, "po_line_id": second})
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=payload,
                      key="bill.create:recover-2", by=OWNER, at=AT)
        ref = M.reference("bill.create:recover-2")
    # The provider took the bill, and the change feed reads it in before the answer to the write is recorded.
    fake.replies = [mirror.Sent(True, kind="Bill", external_id="106", revision="0", lines=("L1", "L2"))]
    fake.changes = [M.Document(M.Ext("Bill", "106", "0", reference=ref), party="Vendor:9", number="B-106",
                               day="2026-10-05", due="", total=10_000,
                               lines=(M.EntryLine("Account:2", 6_000, description="seats", key="L1"),
                                      M.EntryLine("Account:2", 4_000, description="seats", key="L2")))]
    assert _drain(books, fake, cid, now=LATER2, passes=1) == []
    # The provider then lists the same two lines the other way round.
    fake.changes = [M.Document(M.Ext("Bill", "106", "1"), party="Vendor:9", number="B-106", day="2026-10-05", due="",
                               total=10_000,
                               lines=(M.EntryLine("Account:2", 4_000, description="seats", key="L2"),
                                      M.EntryLine("Account:2", 6_000, description="seats", key="L1")))]
    assert _drain(books, fake, cid, now=LATER3, passes=1) == []
    with books.read() as c:
        assert [tuple(r) for r in c.execute("SELECT l.po_line_id, l.external_line, l.amount FROM fin_bill_lines l "
                                            "JOIN fin_bills b ON b.id=l.bill_id WHERE b.number='B-106' "
                                            "ORDER BY l.amount")] == [(second, "L2", 4_000), (first, "L1", 6_000)]


def test_a_matched_revision_after_a_split_keeps_the_order_line_billed_that_the_split_kept(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": _local(c, "Account:2", "fin_accounts"),
                                               "amount": 10_000, "at_limit": "decline", "parent_id": None})
        assert made.status == "committed", made
    _, line = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=_bill_body(c, line, 6_000, "B-108"),
                      key="bill.create:kept-1", by=OWNER, at=AT)
    fake.replies = [mirror.Sent(True, kind="Bill", external_id="108", revision="0", lines=("L1",))]
    assert _drain(books, fake, cid, now=LATER2, passes=1) == []
    split = (M.EntryLine("Account:2", 3_000, description="seats", key="A"),
             M.EntryLine("Account:2", 3_000, description="seats", key="B"))
    fake.changes = [M.Document(M.Ext("Bill", "108", "1"), party="Vendor:9", number="B-108", day="2026-10-05", due="",
                               total=6_000, lines=split)]
    assert _drain(books, fake, cid, now=LATER3, passes=1) == []
    # The next revision names the same two lines and changes nothing: the order line keeps the 6,000 it billed.
    fake.changes = [M.Document(M.Ext("Bill", "108", "2"), party="Vendor:9", number="B-108", day="2026-10-05", due="",
                               total=6_000, lines=split)]
    assert _drain(books, fake, cid, now="2026-10-15T16:00:00.000000Z", passes=1) == []
    with books.read() as c:
        assert SP.billed(c, line) == 6_000
        assert SP.line_room(c, line) == 4_000


def test_a_later_revision_that_bills_the_order_line_again_carries_what_the_bill_kept_into_the_new_link(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": _local(c, "Account:2", "fin_accounts"),
                                               "amount": 10_000, "at_limit": "decline", "parent_id": None})
        assert made.status == "committed", made
    _, line = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=_bill_body(c, line, 6_000, "B-109"),
                      key="bill.create:carry-1", by=OWNER, at=AT)
    fake.replies = [mirror.Sent(True, kind="Bill", external_id="109", revision="0", lines=("L1",))]
    assert _drain(books, fake, cid, now=LATER2, passes=1) == []
    split = (M.EntryLine("Account:2", 3_000, description="seats", key="A"),
             M.EntryLine("Account:2", 3_000, description="seats", key="B"))
    fake.changes = [M.Document(M.Ext("Bill", "109", "1"), party="Vendor:9", number="B-109", day="2026-10-05", due="",
                               total=6_000, lines=split)]
    assert _drain(books, fake, cid, now=LATER3, passes=1) == []
    # The provider drops the first half, so the bill no longer names the order line, which keeps all 6,000 it billed.
    fake.changes = [M.Document(M.Ext("Bill", "109", "2"), party="Vendor:9", number="B-109", day="2026-10-05", due="",
                               total=3_000, lines=(M.EntryLine("Account:2", 3_000, description="seats", key="B"),))]
    assert _drain(books, fake, cid, now="2026-10-15T16:00:00.000000Z", passes=1) == []
    with books.read() as c:
        assert SP.billed(c, line) == 6_000
    # The provider adds a line, so the bill names the order line again: the 3,000 it kept there stays with that link.
    fake.changes = [M.Document(M.Ext("Bill", "109", "3"), party="Vendor:9", number="B-109", day="2026-10-05", due="",
                               total=6_000, lines=(M.EntryLine("Account:2", 3_000, description="seats", key="B"),
                                                   M.EntryLine("Account:2", 3_000, description="seats", key="C")))]
    assert _drain(books, fake, cid, now="2026-10-15T17:00:00.000000Z", passes=1) == []
    with books.read() as c:
        assert SP.billed(c, line) == 6_000
        assert c.execute("SELECT l.amount FROM fin_bill_lines l JOIN fin_bills b ON b.id=l.bill_id "
                         "WHERE b.number='B-109' AND l.po_line_id=?", (line,)).fetchone()[0] == 3_000


def test_a_bill_recovered_at_a_newer_revision_matches_provider_line_ids_by_content(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    _, first = _approved_order(books, fake, cid, 10_000)
    _, second = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        site = _local(c, "Department:3", "locations")
        account = _local(c, "Account:2", "fin_accounts")
        payload = {"party": _local(c, "Vendor:9", "fin_contacts"), "number": "B-110", "day": "2026-10-05", "due": "",
                   "memo": "", "lines": [
                       {"account_id": account, "amount": 6_000, "description": "seats", "item_id": None,
                        "tags": {"location": site}, "po_line_id": first},
                       {"account_id": account, "amount": 4_000, "description": "seats", "item_id": None,
                        "tags": {}, "po_line_id": second}]}
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=payload,
                      key="bill.create:newer-1", by=OWNER, at=AT)
        ref = M.reference("bill.create:newer-1")
    # The provider changed the bill before its answer was recorded, and it lists the two lines the other way round.
    fake.replies = [mirror.Sent(True, kind="Bill", external_id="110", revision="0", lines=("L1", "L2"))]
    fake.changes = [M.Document(M.Ext("Bill", "110", "1", reference=ref), party="Vendor:9", number="B-110",
                               day="2026-10-05", due="", total=10_000,
                               lines=(M.EntryLine("Account:2", 4_000, description="seats", key="L2"),
                                      M.EntryLine("Account:2", 6_000, description="seats", key="L1",
                                                  tags=(("location", "Department:3"),))))]
    assert _drain(books, fake, cid, now=LATER2, passes=1) == []
    with books.read() as c:
        assert [tuple(r) for r in c.execute(
            "SELECT l.po_line_id, l.external_line, l.amount FROM fin_bill_lines l JOIN fin_bills b ON b.id=l.bill_id "
            "WHERE b.number='B-110' ORDER BY l.amount")] == [(second, "L2", 4_000), (first, "L1", 6_000)]


def test_a_bill_recovered_at_a_newer_revision_keeps_a_line_it_cannot_match_billed_until_the_next_revision(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    _, first = _approved_order(books, fake, cid, 10_000)
    _, second = _approved_order(books, fake, cid, 10_000)
    with books.transaction() as c:
        account = _local(c, "Account:2", "fin_accounts")
        payload = {"party": _local(c, "Vendor:9", "fin_contacts"), "number": "B-111", "day": "2026-10-05", "due": "",
                   "memo": "", "lines": [
                       {"account_id": account, "amount": 6_000, "description": "seats", "item_id": None,
                        "tags": {}, "po_line_id": first},
                       {"account_id": account, "amount": 4_000, "description": "seats", "item_id": None,
                        "tags": {}, "po_line_id": second}]}
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=payload,
                      key="bill.create:newer-2", by=OWNER, at=AT)
        ref = M.reference("bill.create:newer-2")
    # The provider changed the second line's amount and lists the two lines the other way round.
    fake.replies = [mirror.Sent(True, kind="Bill", external_id="111", revision="0", lines=("L1", "L2"))]
    fake.changes = [M.Document(M.Ext("Bill", "111", "1", reference=ref), party="Vendor:9", number="B-111",
                               day="2026-10-05", due="", total=9_500,
                               lines=(M.EntryLine("Account:2", 3_500, description="seats", key="L2"),
                                      M.EntryLine("Account:2", 6_000, description="seats", key="L1")))]
    assert _drain(books, fake, cid, now=LATER2, passes=1) == []
    with books.read() as c:
        assert SP.billed(c, first) == 6_000
        assert SP.billed(c, second) == 4_000
    # The next revision lists them in order: the second line keeps the 4,000 the bill billed on it.
    fake.changes = [M.Document(M.Ext("Bill", "111", "2"), party="Vendor:9", number="B-111", day="2026-10-05", due="",
                               total=9_500, lines=(M.EntryLine("Account:2", 6_000, description="seats", key="L1"),
                                                   M.EntryLine("Account:2", 3_500, description="seats", key="L2")))]
    assert _drain(books, fake, cid, now=LATER3, passes=1) == []
    with books.read() as c:
        assert SP.billed(c, first) == 6_000
        assert SP.billed(c, second) == 4_000
        assert [tuple(r) for r in c.execute(
            "SELECT l.external_line, l.amount FROM fin_bill_lines l JOIN fin_bills b ON b.id=l.bill_id "
            "WHERE b.number='B-111' ORDER BY l.amount")] == [("L2", 3_500), ("L1", 6_000)]


def test_a_recovered_bill_create_keeps_its_line_ids_when_the_next_revision_changes_the_line(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        payload = {"party": _local(c, "Vendor:9", "fin_contacts"), "number": "B-112", "day": "2026-10-05", "due": "",
                   "memo": "", "lines": [{"account_id": _local(c, "Account:2", "fin_accounts"), "amount": 1_000,
                                          "description": "seats", "item_id": None, "tags": {"teammate": OWNER}}]}
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=payload,
                      key="bill.create:found-2", by=OWNER, at=AT)
    # The first call was cut off after the provider took the bill. The retry finds it at its first revision, whose line
    # id comes with the find answer and no record.
    fake.replies = [mirror.Sent(False, error="QuickBooks did not answer")]
    _drain(books, fake, cid, now=LATER2)
    fake.found["bill.create:found-2"] = mirror.Sent(True, kind="Bill", external_id="112", revision="1", lines=("L1",))
    assert _drain(books, fake, cid, now=LATER3) == []
    # The provider then changes the line's amount before the change feed reads the bill in. The line keeps its id, so
    # its tag carries over. A teammate tag is not a provider dimension, so the next revision naming no location keeps it.
    fake.changes = [M.Document(M.Ext("Bill", "112", "2"), party="Vendor:9", number="B-112", day="2026-10-05", due="",
                               total=2_000, lines=(M.EntryLine("Account:2", 2_000, description="seats", key="L1"),))]
    assert _drain(books, fake, cid, now="2026-10-15T16:00:00.000000Z", passes=1) == []
    with books.read() as c:
        bill = _local(c, "Bill:112", "fin_bills")
        assert [(r[0], r[1], json.loads(r[2])) for r in c.execute(
            "SELECT external_line, amount, tags_json FROM fin_bill_lines WHERE bill_id=?", (bill,))] == [
            ("L1", 2_000, {"teammate": OWNER})]


def test_voiding_a_bill_made_here_gives_the_order_its_hold_and_its_status_back(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": _local(c, "Account:2", "fin_accounts"),
                                               "amount": 100_000, "at_limit": "decline", "parent_id": None})
        assert made.status == "committed", made
    po, line = _approved_order(books, fake, cid, 10_000)
    with books.read() as c:
        assert [r[0] for r in c.execute("SELECT amount FROM fin_holds WHERE source='purchase_order' AND "
                                        "status='held' AND source_id LIKE ?", (f"{po}:%",)).fetchall()] == [10_000]
    with books.transaction() as c:
        billed = act(c, OWNER, "bill_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"),
                                               "bill_date": "2026-10-05", "number": "B-83",
                                               "lines": [{"po_line_id": line, "amount": 10_000}]})
        assert billed.status == "committed", billed
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.transaction() as c:
        assert c.execute("SELECT status FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()[0] == "closed"
        bill = str(c.execute("SELECT id FROM fin_bills WHERE number='B-83'").fetchone()[0])
        assert act(c, OWNER, "bill_void", {"bill_id": bill}).status == "committed"
    assert _drain(books, fake, cid, now=LATER3) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, closed_by FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()) == \
            ("approved", "")
        assert [r[0] for r in c.execute("SELECT amount FROM fin_holds WHERE source='purchase_order' AND "
                                        "status='held' AND source_id LIKE ?", (f"{po}:%",)).fetchall()] == [10_000]


def test_an_update_made_under_the_id_a_record_was_brought_in_under_reaches_the_row_kept(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        held = _local(c, "Department:3", "locations")
        made = act(c, OWNER, "location_create", {"name": "Dallas"})
        assert made.status == "committed", made
    kept = str(made.result["location_id"])
    fake.replies.append(mirror.Sent(True, kind="Department", external_id="3", revision="2"))
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert RR.get(c, "locations", kept)["id"] == held
    with books.transaction() as c:
        assert act(c, OWNER, "location_update", {"location_id": kept, "name": "Dallas Two"}).status == "committed"
    assert _drain(books, fake, cid, now=LATER2) == []
    assert _sent(fake, "location.update")["ext"] == "Department:3"
    with books.read() as c:
        assert c.execute("SELECT name FROM locations WHERE id=?", (held,)).fetchone()[0] == "Dallas Two"


def test_a_write_queued_under_an_id_is_repointed_to_the_row_kept_when_the_feed_brings_the_record_in_first(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        held = _local(c, "Department:3", "locations")
        made = act(c, OWNER, "location_create", {"name": "Dallas"})
        assert made.status == "committed", made
        kept = str(made.result["location_id"])
        SK.outbox_add(c, connection_id=cid, action_id="", op="location.update",
                      payload={"local_id": kept, "name": "Dallas Two", "archived_at": None, "day": "2026-10-05"},
                      key="location.update:queued-under-the-id", by=OWNER, at=LATER)
    fake.replies.extend([mirror.Sent(True, kind="Department", external_id="3", revision="2"),
                         mirror.Sent(True, kind="Department", external_id="3", revision="3")])
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.read() as c:
        assert c.execute("SELECT name FROM locations WHERE id=?", (held,)).fetchone()[0] == "Dallas Two"
        payload = SK.loads(c.execute("SELECT payload_json FROM fin_outbox WHERE op='location.update'").fetchone()[0])
        assert payload["local_id"] == held
    assert _sent(fake, "location.update")["ext"] == "Department:3"



def test_a_purchase_order_found_edited_on_retry_is_linked_here_and_confirmed(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "po_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"), "po_date": "2026-10-05",
                                           "number": "PO-79", "lines": [{"account_id": _local(
                                               c, "Account:2", "fin_accounts"), "amount": 1500, "description": "seats"}]})
        assert made.status == "committed", made
        key = str(c.execute("SELECT idempotency_key FROM fin_outbox WHERE op='purchaseorder.create'").fetchone()[0])
    # The first call was cut off after QuickBooks took the order, and someone edited it there before the retry.
    fake.replies = [mirror.Sent(False, error="QuickBooks did not answer")]
    _drain(books, fake, cid, now=LATER)
    fake.found[key] = mirror.Sent(True, kind="PurchaseOrder", external_id="80", revision="1")
    assert _drain(books, fake, cid, now=LATER2) == []
    assert [op for op, _, _ in fake.sent if op == "purchaseorder.create"] == ["purchaseorder.create"]
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_outbox WHERE idempotency_key=?", (key,)).fetchone()[0] == "sent"
        assert tuple(c.execute("SELECT number, status, total FROM fin_purchase_orders").fetchone()) == \
            ("PO-79", "draft", 1500)
        assert c.execute("SELECT external_id FROM fin_external_refs WHERE local_table='fin_purchase_orders'"
                         ).fetchone()[0] == "PurchaseOrder:80"


def test_an_estimate_found_edited_on_retry_is_linked_here_and_confirmed(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "estimate_create", {
            "customer_id": _local(c, "Customer:4", "fin_contacts"), "estimate_date": "2026-10-05", "number": "EST-7",
            "lines": [{"item_id": _local(c, "Item:5", "items"), "amount": 1500, "description": "seats"}]})
        assert made.status == "committed", made
        key = str(c.execute("SELECT idempotency_key FROM fin_outbox WHERE op='estimate.create'").fetchone()[0])
    fake.replies = [mirror.Sent(False, error="QuickBooks did not answer")]
    _drain(books, fake, cid, now=LATER)
    fake.found[key] = mirror.Sent(True, kind="Estimate", external_id="81", revision="1")
    assert _drain(books, fake, cid, now=LATER2) == []
    assert [op for op, _, _ in fake.sent if op == "estimate.create"] == ["estimate.create"]
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_outbox WHERE idempotency_key=?", (key,)).fetchone()[0] == "sent"
        assert tuple(c.execute("SELECT number, status, total FROM fin_estimates").fetchone()) == ("EST-7", "draft", 1500)
        assert c.execute("SELECT external_id FROM fin_external_refs WHERE local_table='fin_estimates'"
                         ).fetchone()[0] == "Estimate:81"


def test_an_order_the_provider_holds_that_cannot_be_linked_here_stays_failed_and_visible(books, monkeypatch):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "po_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"), "po_date": "2026-10-05",
                                           "number": "PO-80", "lines": [{"account_id": _local(
                                               c, "Account:2", "fin_accounts"), "amount": 1500, "description": "pens"}]})
        assert made.status == "committed", made
        key = str(c.execute("SELECT idempotency_key FROM fin_outbox WHERE op='purchaseorder.create'").fetchone()[0])

    def refuse(*args: object, **kwargs: object) -> None:
        raise FinanceError(CONFLICT, "the order's vendor is not in the books")
    monkeypatch.setattr(mirror, "_confirm_order", refuse)
    fake.replies = [mirror.Sent(False, error="QuickBooks did not answer")]
    _drain(books, fake, cid, now=LATER)
    fake.found[key] = mirror.Sent(True, kind="PurchaseOrder", external_id="82", revision="1")
    _drain(books, fake, cid, now=LATER2)
    with books.read() as c:
        status, last_error = c.execute("SELECT status, last_error FROM fin_outbox WHERE idempotency_key=?",
                                       (key,)).fetchone()
        assert status == "failed" and "could not be linked here" in last_error, (status, last_error)
        assert c.execute("SELECT COUNT(*) FROM fin_purchase_orders").fetchone()[0] == 0


def test_a_purchase_order_and_an_estimate_come_in_with_their_lines_and_follow_the_provider_on_the_change_feed(books):
    """Orders are read in backfill and in the change feed: a purchase order's status and lines come in as the provider
    holds them, no entry is posted for either, and a later revision replaces the lines."""
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    tags = (("location", "Department:3"),)
    po = M.Order(M.Ext("PurchaseOrder", "40", "1"), "Vendor:9", "PO-40", "2026-10-05", 1500,
                 (M.EntryLine("Account:2", 1500, item="Item:5", tags=tags, key="1"),), status="approved")
    estimate = M.Order(M.Ext("Estimate", "41", "1"), "Customer:4", "EST-41", "2026-10-06", 9000,
                       (M.EntryLine("Account:3", 9000, item="Item:5", key="2"),), status="sent")
    fake.records["documents"] += [po, estimate]
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        row = c.execute("SELECT vendor_id, number, status, total, closed_by FROM fin_purchase_orders").fetchone()
        assert tuple(row) == (_local(c, "Vendor:9", "fin_contacts"), "PO-40", "approved", 1500, "")
        line = c.execute("SELECT account_id, item_id, amount, tags_json FROM fin_po_lines").fetchall()
        assert [tuple(x)[1:3] for x in line] == [(_local(c, "Item:5", "items"), 1500)]
        assert json.loads(line[0]["tags_json"]) == {"location": _local(c, "Department:3", "locations")}
        est = c.execute("SELECT customer_id, status, total FROM fin_estimates").fetchone()
        assert tuple(est) == (_local(c, "Customer:4", "fin_contacts"), "sent", 9000)
        assert c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0] == 0
    closed = M.Order(M.Ext("PurchaseOrder", "40", "2"), "Vendor:9", "PO-40", "2026-10-05", 1800,
                     (M.EntryLine("Account:2", 1800, item="Item:5", tags=tags, key="1"),), status="closed")
    fake.changes.append(closed)
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        row = c.execute("SELECT status, closed_by, total FROM fin_purchase_orders").fetchone()
        assert tuple(row) == ("closed", "hand", 1800)
        assert [int(r[0]) for r in c.execute("SELECT amount FROM fin_po_lines")] == [1800]
        assert c.execute("SELECT revision FROM fin_external_refs WHERE external_id='PurchaseOrder:40'").fetchone()[0] == "2"


def test_an_inventory_item_marked_raw_material_keeps_the_mark_through_the_next_import_m1(books):
    """M1: a QuickBooks inventory item comes in as inventory; a person marks it raw material, and the next import of it
    keeps the mark while its name and price still come in."""
    fake = FakeBooks()
    _records(fake)
    fake.records["records"].append(M.Item(M.Ext("Item", "6", "1"), "Resin", kind="inventory", price=800,
                                          income="Account:3", expense="Account:2"))
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        resin = _local(c, "Item:6", "items")
        marked = act(c, OWNER, "item_update", {"item_id": resin, "kind": "raw_material"})
        assert marked.status == "committed", marked
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert c.execute("SELECT kind FROM items WHERE id=?", (resin,)).fetchone()[0] == "raw_material"
    fake.changes.append(M.Item(M.Ext("Item", "6", "2"), "Resin pellets", kind="inventory", price=900,
                               income="Account:3", expense="Account:2"))
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT name, kind, price FROM items WHERE id=?", (resin,)).fetchone()) == \
            ("Resin pellets", "raw_material", 900)


def test_a_service_fee_change_that_needs_approval_reaches_the_provider_before_tico_changes_h6(books):
    """H6: a service call fee of $89 going to $99 asks for approval; once approved, QuickBooks takes the new price
    first, and Tico's row changes only when QuickBooks confirms it."""
    from backend import hubdb as H
    from backend.finance import actions as FA
    fake = FakeBooks()
    _records(fake)
    fake.records["records"][0] = M.Item(M.Ext("Item", "5", "1"), "Service call", kind="service", price=8900,
                                        income="Account:3", expense="Account:2")
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item = _local(c, "Item:5", "items")
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.records"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "everything",
                                            "whose": "company"}).status == "committed"
        asked = act(c, "human:dan", "item_update", {"item_id": item, "price": 9900})
        assert asked.status == "needs_approval", asked
        H.approval_decide(c, str(asked.approver), str(asked.approval_id), "approved")
        assert FA.approval_decided(c, str(asked.approval_id), LATER).status == "committed"
    with books.read() as c:
        assert c.execute("SELECT price FROM items WHERE id=?", (item,)).fetchone()[0] == 8900
    fake.replies.append(mirror.Sent(True, kind="Item", external_id="5", revision="2"))
    assert _drain(books, fake, cid, now=LATER) == []
    assert [body.get("price") for op, body, _ in fake.sent if op == "item.update"] == [9900]
    with books.read() as c:
        assert c.execute("SELECT price FROM items WHERE id=?", (item,)).fetchone()[0] == 9900


def test_a_purchase_order_billed_in_two_parts_is_partial_then_closed_at_the_provider_m2(books):
    """M2: a resin order billed in two bills is partial; closing it sends the close to QuickBooks, and the order
    closes here once QuickBooks confirms it, with what was not billed released."""
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    po, line = _approved_order(books, fake, cid, 10_000)
    for number, amount, now in (("B-81", 6_000, LATER2), ("B-82", 3_000, LATER3)):
        with books.transaction() as c:
            billed = act(c, OWNER, "bill_create", {
                "vendor_id": _local(c, "Vendor:9", "fin_contacts"), "bill_date": "2026-10-05", "number": number,
                "lines": [{"po_line_id": line, "amount": amount}]})
            assert billed.status == "committed", billed
        assert _drain(books, fake, cid, now=now) == []
    with books.read() as c:
        status, total = c.execute("SELECT status, total FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()
        assert SP.shown_po(str(status), SP.billed_total(c, po), int(total)) == "partial"
        po_ext = str(c.execute("SELECT external_id FROM fin_external_refs WHERE local_table='fin_purchase_orders' "
                               "AND local_id=?", (po,)).fetchone()[0])
    with books.transaction() as c:
        assert act(c, OWNER, "po_close", {"po_id": po}).status == "committed"
    fake.replies.append(mirror.Sent(True, kind="PurchaseOrder", external_id=po_ext.split(":")[1], revision="2"))
    assert _drain(books, fake, cid, now="2026-10-15T16:00:00.000000Z") == []
    assert [op for op, _, _ in fake.sent if op == "purchaseorder.close"] == ["purchaseorder.close"]
    with books.read() as c:
        assert tuple(c.execute("SELECT status, closed_by FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()) == \
            ("closed", "hand")


def test_a_description_a_person_wrote_survives_an_import_that_has_none_item_7(books):
    """Item 7: a provider item with no description keeps the one Tico holds when the provider renames it."""
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item = _local(c, "Item:5", "items")
        c.execute("UPDATE items SET description=? WHERE id=?", ("Hourly rate, onsite", item))
    fake.changes.append(M.Item(M.Ext("Item", "5", "2"), "Consulting (renamed)", kind="service", price=15000,
                               income="Account:3", expense="Account:2"))
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT name, description FROM items WHERE id=?", (item,)).fetchone()) == \
            ("Consulting (renamed)", "Hourly rate, onsite")


def test_a_parent_a_person_set_on_a_department_survives_an_import_that_renames_it_item_7(books):
    """Item 7: a department's parent (a Tico field the books do not send back) survives the provider renaming it."""
    fake = FakeBooks()
    _records(fake)
    fake.records["records"].append(M.Location(M.Ext("Department", "4", "1"), "Region West"))
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        austin = _local(c, "Department:3", "locations")
        region = _local(c, "Department:4", "locations")
        c.execute("UPDATE locations SET parent_id=? WHERE id=?", (region, austin))
    fake.changes.append(M.Location(M.Ext("Department", "3", "2"), "Austin East"))
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT name, parent_id FROM locations WHERE id=?", (austin,)).fetchone()) == \
            ("Austin East", region)


def test_a_location_kind_a_person_set_survives_an_import_that_renames_it_x20(books):
    """X20, the import side: the kind is set on create and never by an import, so a kind a person set afterwards
    survives every later import. The upgrade that moves existing `location` rows to `other` is the records builder's."""
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        austin = _local(c, "Department:3", "locations")
        assert c.execute("SELECT kind FROM locations WHERE id=?", (austin,)).fetchone()[0] == "other"
        c.execute("UPDATE locations SET kind='warehouse' WHERE id=?", (austin,))
    fake.changes.append(M.Location(M.Ext("Department", "3", "2"), "Austin East"))
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT name, kind FROM locations WHERE id=?", (austin,)).fetchone()) == \
            ("Austin East", "warehouse")


# Orders in the mirror: tax, the status a provider revision leaves, and budget holds that follow the provider.

def _tax_account_record(fake: FakeBooks) -> None:
    fake.records["accounts"].append(M.ChartAccount(M.Ext("Account", "9", "1"), "2300", "Sales tax payable", "liability",
                                                   subtype=M.SALES_TAX))


def _ref_of(books, table: str, local: str) -> tuple[str, str, str]:
    """The provider's (kind, id, a revision newer than the reference) for a record the books hold."""
    with books.read() as c:
        external, revision = c.execute("SELECT external_id, revision FROM fin_external_refs WHERE local_table=? AND "
                                       "local_id=? AND status='active'", (table, local)).fetchone()
    kind, _, key = str(external).partition(":")
    return kind, key, str(int(revision) + 1)


def _held(books, po: str) -> int:
    """What budget holds on a purchase order's lines now."""
    with books.read() as c:
        return int(c.execute("SELECT COALESCE(SUM(amount), 0) FROM fin_holds WHERE source='purchase_order' AND "
                             "status='held' AND source_id LIKE ?", (f"{po}:%",)).fetchone()[0])


def _budget(books, amount: int = 100_000) -> None:
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": _local(c, "Account:2", "fin_accounts"),
                                               "amount": amount, "at_limit": "decline", "parent_id": None})
        assert made.status == "committed", made


def _number(books, po: str) -> str:
    with books.read() as c:
        return str(c.execute("SELECT number FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()[0])


def test_a_taxed_estimate_comes_in_with_its_tax_on_the_sales_tax_account(books):
    fake = FakeBooks()
    _records(fake)
    _tax_account_record(fake)
    fake.records["documents"] += [M.Order(M.Ext("Estimate", "41", "1"), "Customer:4", "EST-41", "2026-10-06", 9150,
                                          (M.EntryLine("Account:3", 9000, item="Item:5", key="2"),), status="sent",
                                          tax=150)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        tax = c.execute("SELECT id FROM fin_accounts WHERE subtype=?", (M.SALES_TAX,)).fetchone()[0]
        assert c.execute("SELECT total FROM fin_estimates").fetchone()[0] == 9150
        lines = c.execute("SELECT account_id, amount FROM fin_estimate_lines").fetchall()
        assert sorted(tuple(x) for x in lines) == sorted([(tax, 150), (_local(c, "Account:3", "fin_accounts"), 9000)])
        assert c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0] == 0


def test_a_taxed_purchase_order_closes_once_its_expense_line_is_billed(books):
    """A provider order's tax is not a line a bill bills, so the tax line must not keep the order open."""
    fake = FakeBooks()
    _records(fake)
    _tax_account_record(fake)
    fake.records["documents"] += [M.Order(M.Ext("PurchaseOrder", "40", "1"), "Vendor:9", "PO-40", "2026-10-05", 1650,
                                          (M.EntryLine("Account:2", 1500, item="Item:5", key="1"),), status="approved",
                                          tax=150)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        po = _local(c, "PurchaseOrder:40", "fin_purchase_orders")
        line = str(c.execute("SELECT id FROM fin_po_lines WHERE po_id=? AND amount=1500", (po,)).fetchone()[0])
        billed = act(c, OWNER, "bill_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"),
                                               "bill_date": "2026-10-05", "number": "B-40",
                                               "lines": [{"po_line_id": line, "amount": 1500}]})
        assert billed.status == "committed", billed
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, closed_by FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()) == \
            ("closed", "bills")


def test_a_discounted_order_waits_with_the_same_reason_as_a_discounted_invoice(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["documents"] += [
        M.Order(M.Ext("Estimate", "41", "1"), "Customer:4", "EST-41", "2026-10-06", 9000,
                (M.EntryLine("Account:3", 8000, item="Item:5", key="2"),), status="sent"),
        M.Document(M.Ext("Invoice", "12", "1"), party="Customer:4", number="I-12", day="2026-10-05", due="2026-11-05",
                   total=9000, lines=(M.EntryLine("Account:3", 8000, description="consulting"),))]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        assert sorted(r[0] for r in c.execute("SELECT external_id FROM fin_external_refs WHERE status='unmapped'")) == \
            ["Estimate:41", "Invoice:12"]
        assert c.execute("SELECT COUNT(*) FROM fin_estimates").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM fin_invoices").fetchone()[0] == 0
        for ext in ("Estimate:41", "Invoice:12"):
            why = c.execute("SELECT detail_json FROM events WHERE action='finance.mirror.unmapped' AND target=?",
                            (ext,)).fetchone()[0]
            assert "discounts are not taken in yet" in why, why


def test_a_provider_revision_keeps_the_status_the_books_set_and_moves_an_estimate_sent_to_accepted(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["documents"] += [
        M.Order(M.Ext("Estimate", "41", "1"), "Customer:4", "EST-41", "2026-10-06", 9000,
                (M.EntryLine("Account:3", 9000, item="Item:5", key="2"),), status="sent"),
        M.Order(M.Ext("Estimate", "42", "1"), "Customer:4", "EST-42", "2026-10-06", 9000,
                (M.EntryLine("Account:3", 9000, item="Item:5", key="3"),), status="sent")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "po_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"),
                                           "po_date": "2026-10-05", "number": "PO-79",
                                           "lines": [{"item_id": _local(c, "Item:5", "items"), "amount": 1500,
                                                      "description": "seats"}]})
        assert made.status == "committed", made
    assert _drain(books, fake, cid, now=LATER) == []
    po = str(made.result["po_id"])
    with books.transaction() as c:
        assert act(c, OWNER, "po_send", {"po_id": po}).status == "committed"
    kind, key, rev = _ref_of(books, "fin_purchase_orders", po)
    fake.changes.append(M.Order(M.Ext(kind, key, rev), "Vendor:9", "PO-79", "2026-10-05", 1500,
                                (M.EntryLine("Account:2", 1500, item="Item:5", key="1"),), status="approved"))
    fake.changes.append(M.Order(M.Ext("Estimate", "41", "2"), "Customer:4", "EST-41", "2026-10-06", 9000,
                                (M.EntryLine("Account:3", 9000, item="Item:5", key="2"),), status="accepted"))
    fake.changes.append(M.Order(M.Ext("Estimate", "42", "2"), "Customer:4", "EST-42", "2026-10-06", 9000,
                                (M.EntryLine("Account:3", 9000, item="Item:5", key="3"),), status="declined"))
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.read() as c:
        # The PO stays sent: the provider's Open is not a move the books take. A rejection in the provider does not
        # move a sent estimate either (only an accepted one moves).
        assert c.execute("SELECT status FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()[0] == "sent"
        assert dict(c.execute("SELECT number, status FROM fin_estimates").fetchall()) == \
            {"EST-41": "accepted", "EST-42": "sent"}


def test_a_provider_reopening_a_purchase_order_closed_by_hand_waits_here(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["documents"] += [M.Order(M.Ext("PurchaseOrder", "40", "1"), "Vendor:9", "PO-40", "2026-10-05", 1500,
                                          (M.EntryLine("Account:2", 1500, item="Item:5", key="1"),), status="approved")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        po = _local(c, "PurchaseOrder:40", "fin_purchase_orders")
    fake.changes.append(M.Order(M.Ext("PurchaseOrder", "40", "2"), "Vendor:9", "PO-40", "2026-10-05", 1500,
                                (M.EntryLine("Account:2", 1500, item="Item:5", key="1"),), status="closed"))
    assert _drain(books, fake, cid, now=LATER) == []
    fake.changes.append(M.Order(M.Ext("PurchaseOrder", "40", "3"), "Vendor:9", "PO-40", "2026-10-05", 1500,
                                (M.EntryLine("Account:2", 1500, item="Item:5", key="1"),), status="approved"))
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, closed_by FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()) == \
            ("closed", "hand")
        revision = c.execute("SELECT revision FROM fin_external_refs WHERE external_id='PurchaseOrder:40'").fetchone()
        assert revision[0] == "2"
        why = c.execute("SELECT detail_json FROM events WHERE action='finance.mirror.unmapped' AND "
                        "target='PurchaseOrder:40' ORDER BY ts DESC LIMIT 1").fetchone()[0]
        assert "reopened in QuickBooks" in why, why


def test_a_purchase_order_closed_here_by_its_bills_stays_closed_while_the_provider_has_it_open(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["documents"] += [M.Order(M.Ext("PurchaseOrder", "40", "1"), "Vendor:9", "PO-40", "2026-10-05", 1500,
                                          (M.EntryLine("Account:2", 1500, item="Item:5", key="1"),), status="approved")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        po = _local(c, "PurchaseOrder:40", "fin_purchase_orders")
        line = str(c.execute("SELECT id FROM fin_po_lines WHERE po_id=?", (po,)).fetchone()[0])
        billed = act(c, OWNER, "bill_create", {"vendor_id": _local(c, "Vendor:9", "fin_contacts"),
                                               "bill_date": "2026-10-05", "number": "B-40",
                                               "lines": [{"po_line_id": line, "amount": 1500}]})
        assert billed.status == "committed", billed
    assert _drain(books, fake, cid, now=LATER) == []
    fake.changes.append(M.Order(M.Ext("PurchaseOrder", "40", "2"), "Vendor:9", "PO-40", "2026-10-05", 1500,
                                (M.EntryLine("Account:2", 1500, item="Item:5", key="1"),), status="approved"))
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, closed_by FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()) == \
            ("closed", "bills")


def test_a_provider_revision_of_an_invoiced_estimate_keeps_its_invoice_and_accepted_status(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "estimate_create", {
            "customer_id": _local(c, "Customer:4", "fin_contacts"), "estimate_date": "2026-10-05", "number": "EST-7",
            "lines": [{"item_id": _local(c, "Item:5", "items"), "amount": 1500, "description": "seats"}]})
        assert made.status == "committed", made
    assert _drain(books, fake, cid, now=LATER) == []
    estimate = str(made.result["estimate_id"])
    with books.transaction() as c:
        assert act(c, OWNER, "estimate_send", {"estimate_id": estimate}).status == "committed"
        assert act(c, OWNER, "estimate_accept", {"estimate_id": estimate}).status == "committed"
        assert act(c, OWNER, "estimate_convert", {"estimate_id": estimate}).status == "committed"
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        invoice = c.execute("SELECT invoice_id FROM fin_estimates WHERE id=?", (estimate,)).fetchone()[0]
    assert invoice is not None
    kind, key, rev = _ref_of(books, "fin_estimates", estimate)
    fake.changes.append(M.Order(M.Ext(kind, key, rev), "Customer:4", "EST-7", "2026-10-05", 1500,
                                (M.EntryLine("Account:3", 1500, item="Item:5", key="1"),), status="accepted"))
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, invoice_id FROM fin_estimates WHERE id=?", (estimate,)).fetchone()) == \
            ("accepted", invoice)


def test_a_purchase_order_its_provider_closes_gives_its_budget_hold_back(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    _budget(books)
    po, _ = _approved_order(books, fake, cid, 10_000)
    assert _held(books, po) == 10_000
    kind, key, rev = _ref_of(books, "fin_purchase_orders", po)
    fake.changes.append(M.Order(M.Ext(kind, key, rev), "Vendor:9", _number(books, po), "2026-10-05", 10_000,
                                (M.EntryLine("Account:2", 10_000, item="Item:5", key="1"),), status="closed"))
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, closed_by FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()) == \
            ("closed", "hand")
    assert _held(books, po) == 0


def test_a_purchase_order_its_provider_deletes_is_voided_here_and_gives_its_budget_hold_back(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    _budget(books)
    po, _ = _approved_order(books, fake, cid, 10_000)
    assert _held(books, po) == 10_000
    kind, key, rev = _ref_of(books, "fin_purchase_orders", po)
    fake.changes.append(M.Ext(kind, key, rev, True))
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()[0] == "void"
    assert _held(books, po) == 0


def test_a_purchase_order_whose_lines_change_at_the_provider_holds_its_new_amount(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    _budget(books)
    po, _ = _approved_order(books, fake, cid, 10_000)
    assert _held(books, po) == 10_000
    kind, key, rev = _ref_of(books, "fin_purchase_orders", po)
    fake.changes.append(M.Order(M.Ext(kind, key, rev), "Vendor:9", _number(books, po), "2026-10-05", 7_000,
                                (M.EntryLine("Account:2", 7_000, item="Item:5", key="1"),), status="approved"))
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.read() as c:
        assert c.execute("SELECT total FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()[0] == 7_000
    assert _held(books, po) == 7_000


def test_a_purchase_order_that_came_in_approved_without_a_hold_gets_none_when_its_lines_change(books):
    """A provider order the books placed no hold for (it came in approved from the provider) never gains one through a
    provider revision, even with a budget that would cover it."""
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    _budget(books)
    fake.changes.append(M.Order(M.Ext("PurchaseOrder", "40", "1"), "Vendor:9", "PO-40", "2026-10-05", 1500,
                                (M.EntryLine("Account:2", 1500, item="Item:5", key="1"),), status="approved"))
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        po = _local(c, "PurchaseOrder:40", "fin_purchase_orders")
    assert _held(books, po) == 0
    fake.changes.append(M.Order(M.Ext("PurchaseOrder", "40", "2"), "Vendor:9", "PO-40", "2026-10-05", 7_000,
                                (M.EntryLine("Account:2", 7_000, item="Item:5", key="1"),), status="approved"))
    assert _drain(books, fake, cid, now=LATER2) == []
    assert _held(books, po) == 0
    with books.read() as c:
        assert c.execute("SELECT total FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()[0] == 7_000


def test_a_purchase_order_voided_here_says_to_delete_it_in_quickbooks_and_a_deletion_there_voids_it(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["documents"] += [M.Order(M.Ext("PurchaseOrder", "40", "1"), "Vendor:9", "PO-40", "2026-10-05", 1500,
                                          (M.EntryLine("Account:2", 1500, item="Item:5", key="1"),), status="approved")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        po = _local(c, "PurchaseOrder:40", "fin_purchase_orders")
        refused = act(c, OWNER, "po_void", {"po_id": po})
        assert refused.status != "committed" and refused.reason == "Delete it in QuickBooks; it voids here.", refused
    fake.changes.append(M.Ext("PurchaseOrder", "40", "2", True))
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()[0] == "void"


def test_a_purchase_order_whose_lines_name_two_locations_is_refused_at_plan_time_and_the_dry_run_shows_it(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["records"].append(M.Location(M.Ext("Department", "4", "1"), "Dallas"))
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item, vendor = _local(c, "Item:5", "items"), _local(c, "Vendor:9", "fin_contacts")
        austin, dallas = _local(c, "Department:3", "locations"), _local(c, "Department:4", "locations")
        refused = act(c, OWNER, "po_create", {
            "vendor_id": vendor, "po_date": "2026-10-05",
            "lines": [{"item_id": item, "amount": 1500, "description": "seats", "tags": {"location": austin}},
                      {"item_id": item, "amount": 900, "description": "pens", "tags": {"location": dallas}}]},
            dry_run=True)
        assert (refused.status, refused.code) == ("failed", "invalid"), refused
        assert refused.reason == "a purchase order is in one location: its lines name the same one", refused
        assert c.execute("SELECT COUNT(*) FROM fin_outbox WHERE op='purchaseorder.create'").fetchone()[0] == 0


def test_a_purchase_order_whose_lines_name_two_jobs_is_refused_at_plan_time_with_the_job_wording(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["records"].append(M.Project(M.Ext("Project", "13", "1"), "Bath remodel", customer="Customer:4"))
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item, vendor = _local(c, "Item:5", "items"), _local(c, "Vendor:9", "fin_contacts")
        kitchen, bath = _local(c, "Project:12", "projects"), _local(c, "Project:13", "projects")
        refused = act(c, OWNER, "po_create", {
            "vendor_id": vendor, "po_date": "2026-10-05",
            "lines": [{"item_id": item, "amount": 1500, "description": "seats", "tags": {"project": kitchen}},
                      {"item_id": item, "amount": 900, "description": "pens", "tags": {"project": bath}}]},
            dry_run=True)
        assert (refused.status, refused.code) == ("failed", "invalid"), refused
        assert refused.reason == "the lines name more than one job; QuickBooks takes one per transaction", refused
        assert c.execute("SELECT COUNT(*) FROM fin_outbox WHERE op='purchaseorder.create'").fetchone()[0] == 0


def test_a_bill_and_an_invoice_whose_lines_name_two_locations_or_two_jobs_are_refused_at_plan_time(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["records"] += [M.Location(M.Ext("Department", "4", "1"), "Dallas"),
                                M.Project(M.Ext("Project", "13", "1"), "Bath remodel", customer="Customer:4")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item, vendor = _local(c, "Item:5", "items"), _local(c, "Vendor:9", "fin_contacts")
        customer = _local(c, "Customer:4", "fin_contacts")
        austin, dallas = _local(c, "Department:3", "locations"), _local(c, "Department:4", "locations")
        kitchen, bath = _local(c, "Project:12", "projects"), _local(c, "Project:13", "projects")
        bill = act(c, OWNER, "bill_create", {
            "vendor_id": vendor, "number": "B-79", "bill_date": "2026-10-05", "due_date": "2026-11-05",
            "lines": [{"item_id": item, "amount": 1500, "description": "seats", "tags": {"location": austin}},
                      {"item_id": item, "amount": 900, "description": "pens", "tags": {"location": dallas}}]},
            dry_run=True)
        assert (bill.status, bill.code) == ("failed", "invalid"), bill
        assert bill.reason == "the lines name more than one location; QuickBooks takes one per transaction", bill
        invoice = act(c, OWNER, "invoice_create", {
            "customer_id": customer, "invoice_date": "2026-10-05",
            "lines": [{"item_id": item, "amount": 1500, "description": "seats", "tags": {"project": kitchen}},
                      {"item_id": item, "amount": 900, "description": "pens", "tags": {"project": bath}}]},
            dry_run=True)
        assert (invoice.status, invoice.code) == ("failed", "invalid"), invoice
        assert invoice.reason == "the lines name more than one job; QuickBooks takes one per transaction", invoice
        assert c.execute("SELECT COUNT(*) FROM fin_outbox WHERE op IN ('bill.create', 'invoice.create')"
                         ).fetchone()[0] == 0
