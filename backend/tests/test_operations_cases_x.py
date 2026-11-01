"""Operations test cases that no existing test proved on their own (sections X, P, V and B of the Operations test list,
phases O1 and O2). Each test names its case in its docstring. Fictional Acme data; no outside service is called.

X3 hiding the Equipment tab leaves its due dates opening tasks; X5a a parent shown again keeps its child hidden; X5c a
retail company hides Operations and its P&L by store still answers; X8 an archived location keeps its past P&L and takes
no new lines until it is unarchived; X10b a second estimate conversion is refused and makes no second invoice; X10c a
withdrawn approval leaves the order as it was with no budget hold; X10d 2,000 QuickBooks items import in pages and the
list pages through all of them; X13 a bot's ask goes to the right holder; X16 the table CHECKs refuse kinds and
statuses outside their lists, including the derived statuses partial and invoiced (X17); P4 a customer project's income
and tagged costs give its profit."""
import sqlite3

import pytest

from backend import hubdb as H
from backend import sidebar
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance.core import mirror as M
from backend.finance.core.errors import CONFLICT
from backend.tests import test_due_dates as DD
from backend.tests import test_finance_mirror as MI
from backend.tests import test_finance_mirror_records as MR
from backend.tests import test_finance_purchasing as PU
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401

EVE = "human:eve"
WINDOW = ("2026-10-01", "2026-10-31")
LATER = "2026-10-15T13:00:00.000000Z"


def _ok(answer):
    assert answer.status == "committed", answer.reason
    return answer


def _spend(c, amount: int, tags: dict[str, str], day: str = "2026-10-10") -> None:
    made = act(c, OWNER, "journal", {"date": day, "memo": "Spend", "lines": [
        {"account_id": account(c, "5100"), "amount": amount, "tags": tags},
        {"account_id": account(c, "1000"), "amount": -amount}]})
    assert made.status == "committed", made.reason


def test_x3_hiding_the_equipment_tab_leaves_its_due_dates_opening_their_tasks(books):
    """X3: the Equipment tab is hidden in the company's sidebar; a due date on a unit still opens its task."""
    with books.transaction() as c:
        sidebar.set_settings(c, OWNER, sidebar.SidebarSettings(hidden_sections=["operations.equipment"]))
        assert sidebar.hidden_now(sidebar.stored(c)["hidden_sections"]) == ["operations.equipment"]
        unit = str(_ok(act(c, OWNER, "equipment_create", {"name": "Hidden tab unit", "kind": "owned"}))
                   .result["equipment_id"])
        DD._due(c, "equipment", unit, name="Hidden tab check", assignee="bot:coder")
    DD._tick(books)
    [task] = DD._tasks(books, "bot:coder")
    assert "Hidden tab check" in task["body"]


def test_x5a_a_parent_shown_again_keeps_its_child_hidden_and_strategy_leaves_growth_shown(books):
    """X5a: hiding a parent keeps each child's own choice; showing the parent again leaves the child hidden; Strategy
    hidden does not hide Growth, which is a separate key."""
    with books.transaction() as c:
        sidebar.set_settings(c, OWNER, sidebar.SidebarSettings(hidden_sections=["operations", "operations.equipment"]))
        assert sidebar.hidden_now(sidebar.stored(c)["hidden_sections"]) == ["operations", "operations.equipment"]
        sidebar.set_settings(c, OWNER, sidebar.SidebarSettings(hidden_sections=["operations.equipment"]))
        assert sidebar.hidden_now(sidebar.stored(c)["hidden_sections"]) == ["operations.equipment"]
        sidebar.set_settings(c, OWNER, sidebar.SidebarSettings(hidden_sections=["strategy"]))
        assert sidebar.hidden_now(sidebar.stored(c)["hidden_sections"]) == ["strategy"]


def test_x5c_a_retail_company_hides_operations_and_its_pnl_by_store_still_answers(books):
    """X5c: a company that picks Retail or online store stores the type, hides Operations, and its P&L by store still
    adds up with its store's lines."""
    with books.transaction() as c:
        sidebar.set_business_type(c, OWNER, "retail")
        assert sidebar.stored(c) == {"business_type": "retail", "hidden_sections": ["operations"]}
        store_id = str(_ok(act(c, OWNER, "location_create", {"name": "Acme Store 1", "kind": "store"}))
                       .result["location_id"])
        _spend(c, 2_000, {"location": store_id})
        by_store = FR.pnl_by(c, OWNER, "location", *WINDOW, basis="accrual", at=AT)
        assert {r["label"]: r["expense"] for r in by_store["rows"]} == {"Acme Store 1": 2_000}
        assert by_store["expense"] == 2_000


def test_x8_an_archived_location_keeps_its_past_pnl_and_takes_no_new_lines_until_unarchived(books):
    """X8: archiving a location with posted lines hides it from lists by default, keeps its lines in past P&L, refuses
    a new line tagged with it, and unarchiving lets new lines post again."""
    with books.transaction() as c:
        denver = str(_ok(act(c, OWNER, "location_create", {"name": "Denver", "kind": "store"})).result["location_id"])
        _spend(c, 1_000, {"location": denver})
        _ok(act(c, OWNER, "location_update", {"location_id": denver, "active": False}))
    with books.transaction() as c:
        assert [r["name"] for r in FR.records(c, OWNER, kind="location", at=AT)["locations"]] == []
        assert [r["name"] for r in FR.records(c, OWNER, kind="location", archived="only", at=AT)["locations"]] == \
            ["Denver"]
        past = FR.pnl_by(c, OWNER, "location", *WINDOW, basis="accrual", at=AT)
        assert {r["label"]: r["expense"] for r in past["rows"]} == {"Denver": 1_000}
        refused = act(c, OWNER, "journal", {"date": "2026-10-11", "memo": "Late", "lines": [
            {"account_id": account(c, "5100"), "amount": 500, "tags": {"location": denver}},
            {"account_id": account(c, "1000"), "amount": -500}]})
        assert refused.status != "committed" and refused.code == "invalid", refused
    with books.transaction() as c:
        _ok(act(c, OWNER, "location_update", {"location_id": denver, "active": True}))
        assert [r["name"] for r in FR.records(c, OWNER, kind="location", at=AT)["locations"]] == ["Denver"]
        _spend(c, 700, {"location": denver}, day="2026-10-12")
        again = FR.pnl_by(c, OWNER, "location", *WINDOW, basis="accrual", at=AT)
        assert {r["label"]: r["expense"] for r in again["rows"]} == {"Denver": 1_700}


def test_x10b_a_second_conversion_of_an_estimate_is_refused_and_makes_no_second_invoice(books):
    """X10b: an accepted estimate converts once. The second conversion is refused as already invoiced and leaves one
    invoice. Sequential, not a race: the writer gate runs one conversion at a time and the claim refuses the second."""
    with books.transaction() as c:
        customer = str(_ok(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}))
                       .result["contact_id"])
        estimate = str(_ok(act(c, OWNER, "estimate_create", {
            "customer_id": customer, "estimate_date": "2026-10-05",
            "lines": [{"account_id": account(c, "4000"), "amount": 1_240_000}]})).result["estimate_id"])
        _ok(act(c, OWNER, "estimate_send", {"estimate_id": estimate}))
        _ok(act(c, OWNER, "estimate_accept", {"estimate_id": estimate}))
        _ok(act(c, OWNER, "estimate_convert", {"estimate_id": estimate}))
        second = act(c, OWNER, "estimate_convert", {"estimate_id": estimate})
        assert second.status != "committed" and second.code == CONFLICT, second
        assert c.execute("SELECT COUNT(*) FROM fin_invoices").fetchone()[0] == 1


def test_x10c_a_withdrawn_approval_leaves_the_order_as_it_was_and_places_no_hold(books):
    """X10c: an order over $1,000 waits for the owner. The approval is withdrawn before anyone decides; the order keeps
    its draft status and holds no budget."""
    with books.transaction() as c:
        PU._software(c, 500_000)
        vendor = PU._vendor(c)
        assert act(c, OWNER, "right_grant", {"teammate": EVE, "right": "finance.ap"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": EVE, "what": "everything", "whose": "company"}).status == \
            "committed"
        po = PU._order(c, vendor, [PU._line(c, 150_000)], who=EVE, approve=False)
        asked = act(c, EVE, "po_approve", {"po_id": po})
        assert (asked.status, asked.approver) == ("needs_approval", OWNER), asked
        assert PU._status(c, po)[0] == "draft"
        H.approval_withdraw(c, asked.approval_id, "Withdrawn before a decision.")
        done = {r.action_id: r.status for r in FA.approvals_sweep(c, AT)}
        assert done == {asked.action_id: "withdrawn"}
        assert PU._status(c, po)[0] == "draft"
        assert PU._holds(c, po) == []
        assert PU._state(c) == (0, 0, 500_000)


def test_x10d_two_thousand_quickbooks_items_import_in_pages_and_the_list_pages_through_all_of_them(books):
    """X10d: QuickBooks returns at most 1,000 records a read, so 2,000 items take several reads. The import brings in
    every one, and the item list and search reach each of them through the cursor."""
    fake = MI.FakeBooks()
    MR._records(fake)
    fake.records["records"] += [M.Item(M.Ext("Item", str(n), "1"), f"Part {n:05d}", kind="service", price=100 + n,
                                       income="Account:3") for n in range(100, 2100)]
    cid = MI._connection(books)
    MI._mirror(books, cid)
    assert MI._drain(books, fake, cid, passes=10) == []
    with books.read() as c:
        imported = c.execute("SELECT COUNT(*) FROM items WHERE name LIKE 'Part %' AND archived_at IS NULL").fetchone()[0]
    assert imported == 2000
    with books.transaction() as c:
        names: list[str] = []
        page = FR.records(c, OWNER, kind="item", limit=200, at=AT)
        while True:
            names += [str(r["name"]) for r in page["items"]]
            if not page["next_cursor"]:
                break
            page = FR.records(c, OWNER, kind="item", limit=200, cursor=page["next_cursor"], at=AT)
        assert len(names) == len(set(names)) == 2001
        found = FR.search(c, OWNER, kind="item", q="Part 01999", at=AT)["results"]
        assert [r["label"] for r in found] == ["Part 01999"]


def test_x13_the_approval_a_bot_without_the_records_right_asks_goes_to_the_right_holder(books):
    """X13: a bot without the records right asks for an item, and the approval goes to the owner, who holds the right by
    default. Nothing is written until the approval is decided."""
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.book"}).status == "committed"
        asked = act(c, "bot:coder", "item_create", {"name": "Hose", "kind": "service"})
        assert (asked.status, asked.approver) == ("needs_approval", OWNER), asked
        assert c.execute("SELECT COUNT(*) FROM items WHERE name='Hose'").fetchone()[0] == 0


def test_x16_the_tables_refuse_kinds_and_statuses_outside_their_checks_including_the_derived_ones(books):
    """X16 and X17: the database refuses a location, item, project, due date or order kind or status outside its list.
    `partial` (an order part billed) and `invoiced` (an estimate with its invoice) are derived, never stored."""
    with books.transaction() as c:
        location = str(_ok(act(c, OWNER, "location_create", {"name": "Acme shop", "kind": "store"})).result["location_id"])
        item = str(_ok(act(c, OWNER, "item_create", {"name": "Gasket", "kind": "service", "price": 425}))
                   .result["item_id"])
        project = str(_ok(act(c, OWNER, "project_create", {"name": "Job 1"})).result["project_id"])
        due = str(_ok(act(c, OWNER, "due_date_create", {
            "name": "Permit", "subject_type": "location", "subject_id": location, "repeats": "once",
            "starts_on": "2026-12-01", "lead_days": 0, "assignee_id": OWNER})).result["due_date_id"])
        vendor = PU._vendor(c)
        order = PU._order(c, vendor, [PU._line(c, 10_000)], approve=False)
        estimate = str(_ok(act(c, OWNER, "estimate_create", {
            "customer_id": str(_ok(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}))
                               .result["contact_id"]),
            "estimate_date": "2026-10-05", "lines": [{"account_id": account(c, "4000"), "amount": 5_000}]}))
            .result["estimate_id"])
    refused = [
        ("UPDATE locations SET kind='garage' WHERE id=?", location),
        ("UPDATE items SET kind='bogus' WHERE id=?", item),
        ("UPDATE projects SET status='paused' WHERE id=?", project),
        ("UPDATE due_dates SET repeats='hourly' WHERE id=?", due),
        ("UPDATE due_dates SET subject_type='vehicle' WHERE id=?", due),
        ("UPDATE fin_purchase_orders SET status='partial' WHERE id=?", order),
        ("UPDATE fin_estimates SET status='invoiced' WHERE id=?", estimate),
    ]
    for sql, row_id in refused:
        with pytest.raises(sqlite3.IntegrityError):
            with books.transaction() as c:
                c.execute(sql, (row_id,))
    with books.read() as c:
        assert c.execute("SELECT kind FROM locations WHERE id=?", (location,)).fetchone()[0] == "store"
        assert c.execute("SELECT status FROM fin_purchase_orders WHERE id=?", (order,)).fetchone()[0] == "draft"
        assert c.execute("SELECT status FROM fin_estimates WHERE id=?", (estimate,)).fetchone()[0] == "draft"


def test_p4_hosting_and_model_costs_tagged_to_a_customer_project_give_its_profit(books):
    """P4: a customer onboarding project carries its customer. Its income and its hosting and model costs, tagged to the
    project, give the project's profit, which is the profit for that customer."""
    with books.transaction() as c:
        customer = str(_ok(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}))
                       .result["contact_id"])
        project = str(_ok(act(c, OWNER, "project_create", {"name": "Onboarding Acme Retail", "customer_id": customer}))
                      .result["project_id"])
        _ok(act(c, OWNER, "journal", {"date": "2026-10-09", "memo": "Onboarding fee", "lines": [
            {"account_id": account(c, "1000"), "amount": 10_000},
            {"account_id": account(c, "4000"), "amount": -10_000, "tags": {"project": project}}]}))
        _spend(c, 2_000, {"project": project})
        _spend(c, 700, {"project": project}, day="2026-10-12")
        by_project = FR.pnl_by(c, OWNER, "project", *WINDOW, basis="accrual", at=AT)
        assert [(r["label"], r["income"], r["expense"], r["net"]) for r in by_project["rows"]] == \
            [("Onboarding Acme Retail", 10_000, 2_700, 7_300)]
        assert by_project["net"] == 7_300
