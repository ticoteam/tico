"""Shared records in a mirrored book (spec §4.4 and §10): QuickBooks items, departments, classes and jobs come in once,
transactions carry their place, class, job and item, renames and deletions change the records without reversing an
entry, and what the books send carries the same references back to the provider."""
import json
from types import SimpleNamespace
from typing import Any

import pytest

from backend.finance import mirror
from backend.finance import qbo as QC
from backend.finance.core import mirror as M
from backend.finance.core import qbo as Q
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401
from backend.tests.test_finance_mirror import (  # noqa: F401
    FakeBooks, LATER, _connection, _drain, _local, _mirror, _sent, acct)


def _records(fake: FakeBooks) -> None:
    """Accounts, a vendor and a customer, and a QuickBooks item, department, class and job (a job of customer 4)."""
    fake.records["accounts"] = [acct(1, "1000", "Bank", "asset"), acct(2, "6100", "Software"),
                                acct(3, "4000", "Consulting", "income")]
    fake.records["parties"] = [M.Party(M.Ext("Vendor", "9", "1"), "Acme Software", "vendor"),
                               M.Party(M.Ext("Customer", "4", "1"), "Beta Client", "customer")]
    fake.records["records"] = [
        M.Item(M.Ext("Item", "5", "1"), "Consulting", kind="service", price=15000, income="Account:3",
               expense="Account:2"),
        M.Place(M.Ext("Department", "3", "1"), "Austin"),
        M.Dimension(M.Ext("Class", "7", "1"), "Field"),
        M.Project(M.Ext("Project", "12", "1"), "Kitchen remodel", customer="Customer:4"),
    ]


def _bill(total: int = 1500, rev: str = "1") -> M.Document:
    """A bill from Acme whose one line is for the item, in the department, class and job."""
    tags = (("place", "Department:3"), ("class", "Class:7"), ("project", "Customer:12"))
    return M.Document(M.Ext("Bill", "77", rev), party="Vendor:9", number="B-77", day="2026-10-05", due="2026-11-05",
                      total=total, lines=(M.EntryLine("Account:2", total, description="seats", key="7", tags=tags,
                                                      item="Item:5"),))


def _counts(store: Any) -> tuple[int, int, int, int, int]:
    """Rows in the item, department, class and job records, and external refs."""
    with store.read() as c:
        def n(sql: str) -> int:
            return int(c.execute(sql).fetchone()[0])
        return (n("SELECT COUNT(*) FROM rec_items"), n("SELECT COUNT(*) FROM rec_places"),
                n("SELECT COUNT(*) FROM fin_dimensions WHERE kind='class'"), n("SELECT COUNT(*) FROM rec_projects"),
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


def test_an_imported_bill_carries_its_place_class_job_and_item_and_its_books_balance(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["documents"] = [_bill()]
    cid = _connection(books)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        bill = _local(c, "Bill:77", "fin_bills")
        place, cls = _local(c, "Department:3", "rec_places"), _local(c, "Class:7", "fin_dimensions")
        job, item = _local(c, "Project:12", "rec_projects"), _local(c, "Item:5", "rec_items")
        row = c.execute("SELECT item_id, tags_json FROM fin_bill_lines WHERE bill_id=?", (bill,)).fetchone()
        assert row[0] == item
        assert json.loads(row[1]) == {"place": place, "class": cls, "project": job}
        entry = str(c.execute("SELECT entry_id FROM fin_bills WHERE id=?", (bill,)).fetchone()[0])
        tags = {str(k): str(v) for k, v in c.execute(
            "SELECT t.kind, t.value FROM fin_line_tags t JOIN fin_lines l ON l.id=t.line_id "
            "WHERE l.entry_id=? AND l.account_id=? AND t.kind!='team'", (entry, _local(c, "Account:2", "fin_accounts")))}
        assert tags == {"place": place, "class": cls, "project": job}
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
        assert tags == {"project": _local(c, "Project:12", "rec_projects")}


def test_a_renamed_or_deactivated_record_updates_in_place_and_reverses_nothing(books):
    fake = FakeBooks()
    _records(fake)
    fake.records["documents"] = [_bill()]
    cid = _connection(books)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        entries = c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0]
        item = _local(c, "Item:5", "rec_items")
    fake.changes = [
        M.Item(M.Ext("Item", "5", "2"), "Advisory", kind="service", price=20000, income="Account:3",
               expense="Account:2", active=False),
        M.Place(M.Ext("Department", "3", "2"), "Austin HQ"),
        M.Dimension(M.Ext("Class", "7", "2"), "Field", active=False),
        M.Ext("Customer", "12", "2", True),                 # the job is deleted at the provider
    ]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT name, price, active FROM rec_items WHERE id=?", (item,)).fetchone()) == \
            ("Advisory", 20000, 0)
        assert c.execute("SELECT name FROM rec_places WHERE id=?", (_local(c, "Department:3", "rec_places"),)
                         ).fetchone()[0] == "Austin HQ"
        assert c.execute("SELECT active FROM fin_dimensions WHERE id=?", (_local(c, "Class:7", "fin_dimensions"),)
                         ).fetchone()[0] == 0
        assert c.execute("SELECT active FROM rec_projects WHERE id=?", (_local(c, "Project:12", "rec_projects"),)
                         ).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0] == entries
        assert c.execute("SELECT status FROM fin_entries WHERE id=(SELECT entry_id FROM fin_bills WHERE number='B-77')"
                         ).fetchone()[0] == "posted"


def test_a_bill_made_here_is_sent_with_its_place_class_job_and_item_and_booked_once_the_provider_confirms(books):
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        tags = {"place": _local(c, "Department:3", "rec_places"), "class": _local(c, "Class:7", "fin_dimensions"),
                "project": _local(c, "Project:12", "rec_projects")}
        made = act(c, OWNER, "bill_create", {
            "vendor_id": _local(c, "Vendor:9", "fin_contacts"), "number": "B-78", "bill_date": "2026-10-05",
            "due_date": "2026-11-05",
            "lines": [{"item_id": _local(c, "Item:5", "rec_items"), "amount": 1500, "description": "seats",
                       "tags": tags}]})
        assert made.status == "committed", made
        assert c.execute("SELECT COUNT(*) FROM fin_bills").fetchone()[0] == 0
    assert _drain(books, fake, cid, now=LATER) == []
    body = _sent(fake, "bill.create")
    assert body["lines"] == [{"account": "Account:2", "amount": 1500, "description": "seats", "item": "Item:5",
                              "place": "Department:3", "class": "7", "project": "Customer:12"}]
    out = Q.bill_body(body)
    assert out["DepartmentRef"] == {"value": "3"}
    assert out["Line"][0]["ItemBasedExpenseLineDetail"] == {
        "ItemRef": {"value": "5"}, "Qty": 1, "UnitPrice": "15.00", "ClassRef": {"value": "7"},
        "CustomerRef": {"value": "12"}}
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_bills WHERE number='B-78'").fetchone()[0] == 1


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
        M.Place(M.Ext("Department", "3", "1"), "Austin")]
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
            "place": "Department:3"}
    body = {"day": "2026-10-05", "memo": "", "party": "Vendor:9", "number": "B-1", "due": "", "lines": [line]}
    out = Q.bill_body(body)
    assert out["DepartmentRef"] == {"value": "3"}
    assert out["Line"][0]["ItemBasedExpenseLineDetail"] == {
        "ItemRef": {"value": "5"}, "Qty": 1, "UnitPrice": "15.00", "ClassRef": {"value": "7"},
        "CustomerRef": {"value": "12"}}
    with pytest.raises(ValueError):
        Q.bill_body({**body, "lines": [line, {**line, "place": "Department:4"}]})


def test_an_invoice_for_a_job_is_to_the_job_and_a_journal_line_names_its_location_and_class():
    invoice = Q.invoice_body({"day": "2026-10-06", "memo": "", "party": "Customer:4", "number": "", "due": "",
                              "lines": [{"item": "Item:5", "amount": 1500, "description": "work",
                                         "project": "Customer:12"}]}, {})
    assert invoice["CustomerRef"] == {"value": "12"}
    assert invoice["Line"][0]["SalesItemLineDetail"]["ItemRef"] == {"value": "5"}
    entry = Q.journal_body({"date": "2026-10-06", "memo": "", "source": "sync", "lines": [
        {"account": "Account:2", "party": "", "amount": 1500, "description": "", "role": "",
         "classes": {"class": "7"}, "place": "Department:3", "project": "Customer:12"}]})
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
        placed = act(c, OWNER, "place_create", {"name": "Dallas"})
        job = act(c, OWNER, "project_create", {"name": "Deck build", "customer_id": customer})
        assert [made.status, placed.status, job.status] == ["committed"] * 3, (made, placed, job)
        assert c.execute("SELECT COUNT(*) FROM rec_items WHERE name='Design'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM rec_places WHERE name='Dallas'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM rec_projects WHERE name='Deck build'").fetchone()[0] == 0
    assert _drain(books, fake, cid, now=LATER) == []
    assert _sent(fake, "item.create")["price"] == 9000
    with books.read() as c:
        assert c.execute("SELECT price FROM rec_items WHERE id=?", (made.result["item_id"],)).fetchone()[0] == 9000
        assert c.execute("SELECT name FROM rec_places WHERE id=?", (placed.result["place_id"],)).fetchone()[0] == "Dallas"
        assert c.execute("SELECT name FROM rec_projects WHERE id=?",
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
    asks: list[tuple[str, str]] = []
    monkeypatch.setattr(mirror.tico, "needs_you", lambda c, person, title, body: asks.append((person, title)))
    fake = FakeBooks()
    _records(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        item = _local(c, "Item:5", "rec_items")
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.book"}).status == "committed"
        assert act(c, "human:dan", "item_update", {"item_id": item, "price": 20_000}).status == "needs_approval"
        assert act(c, OWNER, "item_update", {"item_id": item, "price": 20_000}).status == "committed"
        assert c.execute("SELECT price FROM rec_items WHERE id=?", (item,)).fetchone()[0] == 15_000
    fake.replies.append(mirror.Sent(False, error="Price is not allowed on this item", final=True))
    problems = _drain(books, fake, cid, now=LATER)
    assert any("Price is not allowed on this item" in p for p in problems), problems
    with books.read() as c:
        assert c.execute("SELECT price FROM rec_items WHERE id=?", (item,)).fetchone()[0] == 15_000
        status, error = c.execute("SELECT status, last_error FROM fin_outbox WHERE op='item.update'").fetchone()
        assert (status, error) == ("failed", "Price is not allowed on this item")
    assert len(asks) == 1, asks
    with books.transaction() as c:
        assert act(c, OWNER, "item_update", {"item_id": item, "price": 21_000}).status == "committed"
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert c.execute("SELECT price FROM rec_items WHERE id=?", (item,)).fetchone()[0] == 21_000
