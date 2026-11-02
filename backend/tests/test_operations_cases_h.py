"""Operations test cases H5, H7, H10c, C1, C2 and C3 (reports/2026-11-01-operations-test-cases.md): the cases the existing
suites did not prove through the action path. H5: a procurement bot's draft purchase order is asked of the owner before
it holds money. H7: a bot's plate draft saves at the customer's site once a person confirms it. H10c: equipment archived
by its own action opens no task and takes no new due date. C1 and C3: a job's income, costs and profit come from its
tagged invoice and bills, and a 1099 subcontractor's bill on a job still counts on the 1099. C2: a job's estimate read
holds the original and the change order. Fictional Acme data only."""
from datetime import datetime

from backend import due_date_flow as DF
from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import equipment as EQ
from backend.finance import reads as FR
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401

DAY = "2026-10-15"          # AT's day in the company zone
PHOTO = "lib-file-data-plate"
TIN = "00-0000001"          # a fictional tax id for the 1099 workpaper


def _tick(store) -> dict:
    with store.transaction() as c:
        return DF.tick(c, datetime.fromisoformat(AT.replace("Z", "+00:00")))


def _tasks(store, owner: str) -> list[dict]:
    with store.read() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, title, body, status FROM tasks WHERE owner=? AND title LIKE 'Due dates%' ORDER BY created",
            (owner,))]


def _due(c, subject_type: str, subject_id: str, name: str, assignee: str) -> str:
    made = act(c, OWNER, "due_date_create", {"name": name, "subject_type": subject_type, "subject_id": subject_id,
                                             "repeats": "once", "starts_on": DAY, "lead_days": 0,
                                             "assignee_id": assignee})
    assert made.status == "committed", made.reason
    return str(made.result["due_date_id"])


def _held(c, po: str) -> int:
    return int(c.execute("SELECT COALESCE(SUM(amount), 0) FROM fin_holds WHERE source='purchase_order' AND "
                         "status='held' AND source_id LIKE ?", (f"{po}:%",)).fetchone()[0])


# ----------------------------------------------------------------------------------------------------------------- H5
def test_h5_a_procurement_bots_draft_po_carries_its_item_lines_and_holds_nothing_until_the_owner_approves(books):
    """H5, the action side: a bot with the AP right drafts a PO from item lines tagged with a shop and a job. Its send
    asks the owner, so nothing is sent until a person approves; its approval of an order over $1,000 asks the owner
    too, and the owner's approval is what places the hold."""
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.ap"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "everything",
                                            "whose": "company"}).status == "committed"
        shop = str(act(c, OWNER, "location_create", {"name": "Acme North shop", "kind": "store"}).result["location_id"])
        job = str(act(c, OWNER, "project_create", {"name": "Install"}).result["project_id"])
        vendor = str(act(c, OWNER, "contact_create", {"name": "Acme Supplies", "kind": "vendor"}).result["contact_id"])
        condenser = str(act(c, OWNER, "item_create", {"name": "Condenser", "kind": "non_inventory", "price": 150_000,
                                                       "expense_account_id": account(c, "6100")}).result["item_id"])
        assert act(c, OWNER, "budget_create", {"name": "Install materials", "account_id": account(c, "6100"),
                                               "amount": 500_000, "at_limit": "decline",
                                               "parent_id": None}).status == "committed"
        made = act(c, "bot:coder", "po_create", {"vendor_id": vendor, "po_date": DAY, "lines": [
            {"item_id": condenser, "amount": 150_000, "tags": {"location": shop, "project": job}}]})
        assert made.status == "committed", made.reason
        po = str(made.result["po_id"])
        assert FR.order(c, OWNER, po)["lines"][0]["tags"] == {"location": shop, "project": job}
        # approval asked, not sent: the owner's decision is what marks the order sent
        sent_asked = act(c, "bot:coder", "po_send", {"po_id": po})
        assert (sent_asked.status, sent_asked.approver) == ("needs_approval", OWNER), sent_asked
        assert FR.order(c, OWNER, po)["status"] == "draft"
        H.approval_decide(c, OWNER, str(sent_asked.approval_id), "approved")
        assert FA.approval_decided(c, str(sent_asked.approval_id), AT).status == "committed"
        assert FR.order(c, OWNER, po)["status"] == "sent"
        asked = act(c, "bot:coder", "po_approve", {"po_id": po})
        assert (asked.status, asked.approver) == ("needs_approval", OWNER), asked
        assert FR.order(c, OWNER, po)["status"] == "sent" and _held(c, po) == 0
        H.approval_decide(c, OWNER, str(asked.approval_id), "approved")
        assert FA.approval_decided(c, str(asked.approval_id), AT).status == "committed"
        assert FR.order(c, OWNER, po)["status"] == "approved" and _held(c, po) == 150_000


def test_h5_a_procurement_bot_with_the_ap_right_and_a_company_expenses_view_drafts_a_tagged_po_line(books):
    """H5, the setup gap: a Procurement Manager holds the AP right and a company view of expenses, not everything. Its
    draft PO line naming an account and tags is refused outside its view until the expenses view is given."""
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.ap"}).status == "committed"
        shop = str(act(c, OWNER, "location_create", {"name": "Acme North shop", "kind": "store"}).result["location_id"])
        job = str(act(c, OWNER, "project_create", {"name": "Install"}).result["project_id"])
        vendor = str(act(c, OWNER, "contact_create", {"name": "Acme Supplies", "kind": "vendor"}).result["contact_id"])
        line = {"account_id": account(c, "6100"), "amount": 150_000, "description": "condenser",
                "tags": {"location": shop, "project": job}}
        refused = act(c, "bot:coder", "po_create", {"vendor_id": vendor, "po_date": DAY, "lines": [line]})
        assert refused.status != "committed" and "outside your view" in str(refused.reason), refused
        assert act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "expenses",
                                            "whose": "company"}).status == "committed"
        made = act(c, "bot:coder", "po_create", {"vendor_id": vendor, "po_date": DAY, "lines": [line]})
        assert made.status == "committed", made.reason


# ----------------------------------------------------------------------------------------------------------------- H7
def test_h7_a_bots_plate_draft_saves_at_the_customers_site_with_its_photo_once_a_person_confirms(books, monkeypatch):
    """H7 (gate side): the draft names the customer and the site it sits at; nothing is saved before the owner
    approves, and the approved record keeps the site, the customer, the data and the photo."""
    monkeypatch.setattr(EQ, "FILE_READABLE", lambda c, who, file_id: file_id == PHOTO)
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.records"}).status == "committed"
        smith = str(act(c, OWNER, "contact_create", {"name": "Smith Homes", "kind": "customer"}).result["contact_id"])
        site = str(act(c, OWNER, "location_create", {"name": "Smith Site", "kind": "customer_site",
                                                     "customer_id": smith}).result["location_id"])
        draft = act(c, "bot:coder", "equipment_create", {
            "name": "Furnace", "kind": "customer", "customer_id": smith, "location_id": site, "make": "Acme",
            "model": "H-90", "serial": "SN-1", "photo_file_id": PHOTO})
        assert (draft.status, draft.approver) == ("needs_approval", OWNER), draft
        assert c.execute("SELECT COUNT(*) FROM equipment").fetchone()[0] == 0
        H.approval_decide(c, OWNER, str(draft.approval_id), "approved")
        assert FA.approval_decided(c, str(draft.approval_id), AT).status == "committed"
        saved = c.execute("SELECT customer_id, location_id, make, model, serial, photo_file_id FROM equipment").fetchone()
        assert tuple(saved) == (smith, site, "Acme", "H-90", "SN-1", PHOTO)


# --------------------------------------------------------------------------------------------------------------- H10c
def test_h10c_equipment_archived_by_its_action_opens_no_task_and_takes_no_new_due_date(books):
    """H10c through the real archive action (the existing case sets archived_at by hand): the pending due date opens
    nothing on the next pass, and a new due date on the archived piece is refused."""
    with books.transaction() as c:
        press = str(act(c, OWNER, "equipment_create", {"name": "Old press", "kind": "owned"}).result["equipment_id"])
        _due(c, "equipment", press, "Press service", "bot:coder")
        assert act(c, OWNER, "equipment_update", {"equipment_id": press, "active": False}).status == "committed"
        refused = act(c, OWNER, "due_date_create", {"name": "Press check", "subject_type": "equipment",
                                                    "subject_id": press, "repeats": "once", "starts_on": DAY,
                                                    "lead_days": 0, "assignee_id": "bot:coder"})
        assert refused.status != "committed" and refused.code == "invalid" and "archived" in refused.reason
    assert _tick(books)["tasks"] == []
    assert _tasks(books, "bot:coder") == []


# ------------------------------------------------------------------------------------------------------------ C1, C3
def test_c1_a_jobs_income_costs_and_profit_come_from_its_tagged_invoice_and_bill(books):
    """C1: an invoice and a bill tagged to the job; the job's P&L row shows the income, the cost and the profit, and the
    totals are the same two amounts."""
    with books.transaction() as c:
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Homes", "kind": "customer"}).result["contact_id"])
        vendor = str(act(c, OWNER, "contact_create", {"name": "Acme Supplies", "kind": "vendor"}).result["contact_id"])
        job = str(act(c, OWNER, "project_create", {"name": "Kitchen remodel", "customer_id": customer}).result["project_id"])
        sold = act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": DAY, "lines": [
            {"account_id": account(c, "4000"), "amount": 600_000, "tags": {"project": job}}]})
        assert sold.status == "committed", sold.reason
        assert act(c, OWNER, "invoice_send", {"invoice_id": str(sold.result["invoice_id"])}).status == "committed"
        bought = act(c, OWNER, "bill_create", {"vendor_id": vendor, "bill_date": DAY, "lines": [
            {"account_id": account(c, "6100"), "amount": 200_000, "tags": {"project": job}}]})
        assert bought.status == "committed", bought.reason
        assert act(c, OWNER, "bill_approve", {"bill_id": str(bought.result["bill_id"])}).status == "committed"
        pnl = FR.pnl_by(c, OWNER, "project", "2026-10-01", "2026-10-31", basis="accrual", at=AT)
    [row] = [r for r in pnl["rows"] if r["key"] == job]
    assert (row["income"], row["expense"], row["net"]) == (600_000, 200_000, 400_000)
    assert (pnl["income"], pnl["expense"], pnl["net"]) == (600_000, 200_000, 400_000)


def test_c3_a_1099_subcontractor_bill_on_a_job_is_its_jobs_cost_and_counts_on_the_1099(books):
    """C3: a 1099 subcontractor's bill tagged to the job is the job's cost once it is paid, and the same paid amount
    lands on that vendor's 1099 row."""
    with books.transaction() as c:
        job = str(act(c, OWNER, "project_create", {"name": "Kitchen remodel"}).result["project_id"])
        sub = str(act(c, OWNER, "contact_create", {"name": "Acme Drywall", "kind": "vendor", "is_1099": True,
                                                   "tax_id": TIN}).result["contact_id"])
        bill = act(c, OWNER, "bill_create", {"vendor_id": sub, "bill_date": "2026-05-01", "lines": [
            {"account_id": account(c, "6100"), "amount": 250_000, "tags": {"project": job}}]})
        assert bill.status == "committed", bill.reason
        bill_id = str(bill.result["bill_id"])
        assert act(c, OWNER, "bill_approve", {"bill_id": bill_id}).status == "committed"
        paid = act(c, OWNER, "bill_mark_paid", {"bill_id": bill_id, "date": "2026-06-01",
                                                "account_id": account(c, "1000")})
        assert paid.status == "committed", paid.reason
        report = FR.form_1099_report(c, OWNER, 2026, AT)
        pnl = FR.pnl_by(c, OWNER, "project", "2026-01-01", "2026-12-31", basis="accrual", at=AT)
    [row] = [r for r in report["rows"] if r["vendor"] == "Acme Drywall"]
    assert (row["cash_paid"], row["tin_on_file"]) == (250_000, True)
    [cost] = [r for r in pnl["rows"] if r["key"] == job]
    assert cost["expense"] == 250_000


def test_c2_a_change_order_is_a_second_estimate_on_the_job_and_both_open_ones_read_by_the_job(books):
    """C2: the job's estimate read returns the original estimate and the change order, and the job panel shows open
    estimates (draft, sent, accepted), so both stay on it while open; an estimate on another job stays off."""
    with books.transaction() as c:
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Homes", "kind": "customer"}).result["contact_id"])
        job = str(act(c, OWNER, "project_create", {"name": "Kitchen remodel", "customer_id": customer}).result["project_id"])
        other = str(act(c, OWNER, "project_create", {"name": "Bath remodel", "customer_id": customer}).result["project_id"])

        def estimate(project: str, amount: int) -> str:
            made = act(c, OWNER, "estimate_create", {"customer_id": customer, "estimate_date": DAY, "lines": [
                {"account_id": account(c, "4000"), "amount": amount, "tags": {"project": project}}]})
            assert made.status == "committed", made.reason
            return str(made.result["estimate_id"])
        original = estimate(job, 1_240_000)
        assert act(c, OWNER, "estimate_send", {"estimate_id": original}).status == "committed"
        change_order = estimate(job, 150_000)
        estimate(other, 90_000)
        listed = FR.estimates(c, OWNER, project_id=job)["estimates"]
        assert sorted((e["id"], e["status"]) for e in listed) == sorted([(original, "sent"), (change_order, "draft")])
