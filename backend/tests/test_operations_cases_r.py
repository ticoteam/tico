"""Operations cases for restaurants, small manufacturers, multi-location services and online brands (R2, R1, R4, M3, M1,
E1, S1, S2 in the Operations test cases). Each test covers a gap the existing suites leave: the purchase-order threshold a
teammate meets per store, per-store bills in the P&L, owned equipment at a site with its recurring service, the assembly
mark kept through an import, a warehouse as the only location, service and inventory items sold per studio, and the vendor
renewal that should open a task. Fictional Acme data; QuickBooks is the in-memory fake the mirror tests use. The clock is AT."""
import pytest

from backend.finance import actions as FA
from backend.finance import jobs as FJ
from backend.finance import reads as FR
from backend.finance.core import mirror as M
from backend.tests.test_due_dates import _due, _tasks, _tick
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401
from backend.tests.test_finance_mirror import FakeBooks, LATER, _connection, _drain, _local, _mirror
from backend.tests.test_finance_mirror_records import LATER2, _records

WINDOW = ("2026-10-01", "2026-10-31")
BILL_DAY = "2026-10-10"
TEAMMATE = "human:dan"


def _vendor(c, name: str, kind: str = "vendor") -> str:
    made = act(c, OWNER, "contact_create", {"name": name, "kind": kind})
    assert made.status == "committed", made.reason
    return str(made.result["contact_id"])


def _location(c, name: str, kind: str = "store") -> str:
    made = act(c, OWNER, "location_create", {"name": name, "kind": kind})
    assert made.status == "committed", made.reason
    return str(made.result["location_id"])


def _expense(c, code: str, name: str, parent: str) -> str:
    made = act(c, OWNER, "account_create", {"code": code, "name": name, "type": "expense", "parent_id": parent})
    assert made.status == "committed", made.reason
    return str(made.result["account_id"])


def _budget(c, name: str, account_id: str, amount: int) -> None:
    """A budget on one expense account, so the spend is held by a budget that covers it rather than asking."""
    made = act(c, OWNER, "budget_create", {"name": name, "account_id": account_id, "amount": amount,
                                           "at_limit": "decline", "parent_id": None})
    assert made.status == "committed", made.reason


def _item(c, name: str, kind: str, price: int) -> str:
    made = act(c, OWNER, "item_create", {"name": name, "kind": kind, "price": price,
                                         "income_account_id": account(c, "4000")})
    assert made.status == "committed", made.reason
    return str(made.result["item_id"])


# ------------------------------------------------------------------------------------------------------------- R2
def test_r2_a_600_produce_order_per_store_needs_no_approval_and_a_1200_one_asks(books):  # noqa: F811
    """R2: a teammate's weekly produce order for each store, $600 each, is approved with no question; a $1,200 order asks
    the owner under the po-over-1000 default rule, and stays sent until the owner decides."""
    with books.transaction() as c:
        food = _expense(c, "5010", "Food and paper", account(c, "5000"))
        _budget(c, "Food and paper", food, 1_000_000)
        assert act(c, OWNER, "right_grant", {"teammate": TEAMMATE, "right": "finance.ap"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": TEAMMATE, "what": "everything",
                                            "whose": "company"}).status == "committed"
        produce = _vendor(c, "Acme Produce")
        north, south = _location(c, "Acme Kitchen North"), _location(c, "Acme Kitchen South")

        def order(amount: int, store: str) -> str:
            made = act(c, TEAMMATE, "po_create", {"vendor_id": produce, "po_date": BILL_DAY, "lines": [
                {"account_id": food, "amount": amount, "tags": {"location": store}}]})
            assert made.status == "committed", made.reason
            po = str(made.result["po_id"])
            assert act(c, TEAMMATE, "po_send", {"po_id": po}).status == "committed"
            return po

        for po in (order(60_000, north), order(60_000, south)):
            approved = act(c, TEAMMATE, "po_approve", {"po_id": po})
            assert (approved.status, approved.approver) == ("committed", None), approved
        big = order(120_000, north)
        asked = act(c, TEAMMATE, "po_approve", {"po_id": big})
        assert (asked.status, asked.approver) == ("needs_approval", OWNER), asked
        assert c.execute("SELECT status FROM fin_purchase_orders WHERE id=?", (big,)).fetchone()[0] == "sent"


# ------------------------------------------------------------------------------------------------------------- R1
def test_r1_food_and_paper_bills_tagged_by_store_show_each_stores_expense_and_net_in_the_pnl_by_location(books):  # noqa: F811
    """R1: bills for food and paper, tagged by store, land on that store's row of the P&L by location. A store with no
    sales shows its cost as a negative net."""
    with books.transaction() as c:
        food = _expense(c, "5010", "Food and paper", account(c, "5000"))
        _budget(c, "Food and paper", food, 1_000_000)
        north, south = _location(c, "Acme Kitchen North"), _location(c, "Acme Kitchen South")
        farm = _vendor(c, "Acme Farm Supply")
        bill = act(c, OWNER, "bill_create", {"vendor_id": farm, "bill_date": BILL_DAY, "lines": [
            {"account_id": food, "amount": 60_000, "tags": {"location": north}},
            {"account_id": food, "amount": 45_000, "tags": {"location": south}}]})
        assert bill.status == "committed", bill.reason
        assert act(c, OWNER, "bill_approve", {"bill_id": str(bill.result["bill_id"])}).status == "committed"
        sale = act(c, OWNER, "journal", {"date": BILL_DAY, "memo": "Sales", "lines": [
            {"account_id": account(c, "4000"), "amount": -100_000, "tags": {"location": north}},
            {"account_id": account(c, "1000"), "amount": 100_000}]})
        assert sale.status == "committed", sale.reason
    with books.read() as c:
        by_store = FR.pnl_by(c, OWNER, "location", *WINDOW, basis="accrual", top_n=50, at=AT)
    assert by_store["expense"] == 105_000
    assert {r["label"]: (r["income"], r["expense"], r["net"]) for r in by_store["rows"]} == {
        "Acme Kitchen North": (100_000, 60_000, 40_000), "Acme Kitchen South": (0, 45_000, -45_000)}


# ---------------------------------------------------------------------------------------------- R4 and M3 (equipment)
@pytest.mark.parametrize("site_kind, site, unit, due_name, repeats, every, starts_on", [
    # R4: a hood at a restaurant store, cleaned every 90 days by the vendor the due date names.
    ("store", "Acme Kitchen North", "Hood", "Hood cleaning by Acme Hood Services", "every_days", 90, BILL_DAY),
    # M3: a CNC mill at the plant, serviced every quarter.
    ("plant", "Acme Works Plant", "CNC mill", "CNC quarterly service", "quarterly", None, "2026-10-01"),
])
def test_r4_m3_owned_equipment_at_a_site_opens_its_recurring_service_task_and_is_found_at_that_site(
        books, site_kind, site, unit, due_name, repeats, every, starts_on):  # noqa: F811
    """R4 and M3: an owned piece kept at a site is listed under that site, and its recurring service opens one task for
    the teammate who works it, naming the piece and the day it fell due."""
    with books.transaction() as c:
        where = _location(c, site, site_kind)
        made = act(c, OWNER, "equipment_create", {"name": unit, "kind": "owned", "location_id": where})
        assert made.status == "committed", made.reason
        equipment = str(made.result["equipment_id"])
        _due(c, "equipment", equipment, name=due_name, assignee="bot:coder", repeats=repeats, starts_on=starts_on,
             every=every)
    _tick(books)
    [task] = _tasks(books, "bot:coder")
    assert f"{due_name} at {unit}" in task["body"] and f"due {starts_on}" in task["body"]
    with books.read() as c:
        at_site = FR.records(c, OWNER, kind="equipment", filters={"location_id": where})["equipment"]
    assert [r["name"] for r in at_site] == [unit]


# ------------------------------------------------------------------------------------------------------------- M1
def test_m1_inventory_items_come_in_as_inventory_and_keep_their_raw_material_and_assembly_marks_through_the_next_import(
        books):  # noqa: F811
    """M1: QuickBooks inventory items arrive as inventory; a person marks the resin raw material and the chair an
    assembly; the next import renames and reprices both and keeps the marks."""
    fake = FakeBooks()
    _records(fake)
    fake.records["records"] += [
        M.Item(M.Ext("Item", "6", "1"), "Resin", kind="inventory", price=800, income="Account:3", expense="Account:2"),
        M.Item(M.Ext("Item", "7", "1"), "Finished chair", kind="inventory", price=24_000, income="Account:3",
               expense="Account:2")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        resin, chair = _local(c, "Item:6", "items"), _local(c, "Item:7", "items")
        assert dict(c.execute("SELECT name, kind FROM items WHERE id IN (?, ?)", (resin, chair)).fetchall()) == \
            {"Resin": "inventory", "Finished chair": "inventory"}
    with books.transaction() as c:
        assert act(c, OWNER, "item_update", {"item_id": resin, "kind": "raw_material"}).status == "committed"
        assert act(c, OWNER, "item_update", {"item_id": chair, "kind": "assembly"}).status == "committed"
    assert _drain(books, fake, cid, now=LATER) == []
    fake.changes += [
        M.Item(M.Ext("Item", "6", "2"), "Resin pellets", kind="inventory", price=900, income="Account:3",
               expense="Account:2"),
        M.Item(M.Ext("Item", "7", "2"), "Finished chair", kind="inventory", price=25_000, income="Account:3",
               expense="Account:2")]
    assert _drain(books, fake, cid, now=LATER2) == []
    with books.read() as c:
        rows = c.execute("SELECT name, kind, price FROM items WHERE id IN (?, ?) ORDER BY name", (resin, chair))
        assert [tuple(r) for r in rows] == [("Finished chair", "assembly", 25_000),
                                            ("Resin pellets", "raw_material", 900)]


# ------------------------------------------------------------------------------------------------------------- E1
def test_e1_a_warehouse_can_be_the_only_location_and_carries_its_costs_in_the_pnl_by_location(books):  # noqa: F811
    """E1: an online brand with one 3PL warehouse and no stores: the warehouse is a location of its kind, and a fee bill
    tagged to it shows on its row of the P&L by location."""
    with books.transaction() as c:
        dock = _location(c, "Acme 3PL Dock", "warehouse")
        fulfillment = _vendor(c, "Acme Fulfillment")
        fees_account = _expense(c, "6300", "Fulfillment fees", account(c, "6000"))
        _budget(c, "Fulfillment fees", fees_account, 1_000_000)
        fees = act(c, OWNER, "bill_create", {"vendor_id": fulfillment, "bill_date": BILL_DAY, "lines": [
            {"account_id": fees_account, "amount": 25_000, "tags": {"location": dock}}]})
        assert fees.status == "committed", fees.reason
        assert act(c, OWNER, "bill_approve", {"bill_id": str(fees.result["bill_id"])}).status == "committed"
    with books.read() as c:
        listed = FR.records(c, OWNER, kind="location")["locations"]
        by_location = FR.pnl_by(c, OWNER, "location", *WINDOW, basis="accrual", top_n=50, at=AT)
    assert ("Acme 3PL Dock", "warehouse") in [(r["name"], r["kind"]) for r in listed]
    assert {r["label"]: r["expense"] for r in by_location["rows"]} == {"Acme 3PL Dock": 25_000}


# ------------------------------------------------------------------------------------------------------------- S1
def test_s1_service_and_inventory_items_sold_to_each_studio_add_up_per_studio_in_the_pnl_by_studio(books):  # noqa: F811
    """S1: a haircut (service) and a shampoo (inventory) sold at three studios: each studio's invoice lines carry its
    location, and the P&L by location gives each studio its own income and no studio the other's."""
    with books.transaction() as c:
        north, south = _location(c, "Acme Studio North"), _location(c, "Acme Studio South")
        haircut = _item(c, "Haircut", "service", 4_500)
        shampoo = _item(c, "Shampoo", "inventory", 1_200)
        client = _vendor(c, "Acme Client", "customer")
        for studio, sold in ((north, [(haircut, 4_500), (shampoo, 1_200)]), (south, [(haircut, 4_500)])):
            made = act(c, OWNER, "invoice_create", {"customer_id": client, "invoice_date": BILL_DAY,
                                                    "lines": [{"item_id": item, "amount": amount,
                                                               "tags": {"location": studio}}
                                                              for item, amount in sold]})
            assert made.status == "committed", made.reason
            assert act(c, OWNER, "invoice_send", {"invoice_id": str(made.result["invoice_id"])}).status == "committed"
    with books.read() as c:
        by_studio = FR.pnl_by(c, OWNER, "location", *WINDOW, basis="accrual", top_n=50, at=AT)
    assert by_studio["income"] == 10_200
    assert {r["label"]: r["income"] for r in by_studio["rows"]} == {
        "Acme Studio North": 5_700, "Acme Studio South": 4_500}


# ------------------------------------------------------------------------------------------------------------- S2
@pytest.mark.xfail(strict=True, reason="finance.vendor.renewal_due is drained to rules, but no routine or default rule "
                   "turns it into a task (documents.py:1654, jobs.py:55); plan P3 and S2 expect a task")
def test_s2_a_vendor_renewal_with_notice_opens_a_task_that_names_the_vendor_before_the_cancel_by_day(books):  # noqa: F811
    """S2: a laser service contract renews on 14 December with 60 days' notice, so its cancel-by day is today. The
    renewal stage is raised, and a task should name the vendor so someone acts before the notice runs out."""
    with books.transaction() as c:
        vendor = _vendor(c, "Laser Service Co")
        assert act(c, OWNER, "contact_update", {"contact_id": vendor, "renewal_on": "2026-12-14",
                                                "renewal_notice_days": 60}).status == "committed"
    with books.transaction() as c:
        FA.watch_documents(c, AT)
        FJ.drain(c, AT)
    with books.read() as c:
        named = c.execute("SELECT title FROM tasks WHERE body LIKE ? OR title LIKE ?",
                          ("%Laser Service Co%", "%Laser Service Co%")).fetchall()
    assert named, "no task names the vendor's renewal"
