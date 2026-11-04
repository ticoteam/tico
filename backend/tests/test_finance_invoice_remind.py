"""Invoice reminders (backend/finance/documents.py invoice_remind): an unpaid sent invoice is reminded once per stage (1, 7,
30, 60 or 90 days late), with fixed text to the customer's email. Recorded on commit under a send approval for that text
(backend/finance/delivery.py); the Bookkeeper sends it. Paid, draft, emailless, out-of-list and no-mailbox cases are
refused with the reason. A stage is for an invoice that is that many days past its due date, judged on the action's own
day: no due date, a due date not yet passed, and a stage not yet reached are each refused before any stage is used."""

from backend import bot_settings as BS
from backend.finance.core.errors import CONFLICT
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, account, act, books, finance_template, store)

EMAIL = "ap@acme-retail.example"
BOX = "books@acme.example"
ISSUED = "2026-08-01"           # AT is 2026-10-15: due 2026-08-31 is 45 days late, so the stages 1, 7 and 30 are reached
DUE = "2026-08-31"


def _mailbox(c) -> None:
    """The Bookkeeper's stored gmail tool that can send (what a real install is given)."""
    tools = [{"service": "gmail", "identity": BOX, "can": ["read", "send"]}]
    BS.write(c, "bookkeeper", {"team": "eng", "tools": tools})


def _invoice(c, *, emails=(EMAIL,), due=DUE, issued=ISSUED, link="") -> tuple[str, str]:
    """A sent invoice of 300.00 to a customer (with these emails), with this due date (none when None); returns (invoice
    id, customer name)."""
    made = act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer", "emails": list(emails)})
    customer = str(made.result["contact_id"])
    payload: dict[str, object] = {"customer_id": customer, "invoice_date": issued,
                                  "lines": [{"account_id": account(c, "4000"), "amount": 30_000}]}
    if due is not None:
        payload["due_date"] = due
    if link:
        payload["payment_link"] = link
    iid = str(act(c, OWNER, "invoice_create", payload).result["invoice_id"])
    assert act(c, OWNER, "invoice_send", {"invoice_id": iid}).status == "committed"
    return iid, "Acme Retail"


def test_a_stage_reminder_is_recorded_once_with_the_fixed_text_and_the_payment_link(books):
    with books.transaction() as c:
        iid, _ = _invoice(c, link="https://pay.example/inv-1")
        _mailbox(c)
        made = act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 7})
        assert made.status == "committed", made
        assert made.result["status"] == "reminded" and made.result["delivery"] == "send_approval"
        assert made.result["send_approval_id"] and made.result["mailbox"] == BOX
        assert made.result["subject"] == f"Invoice {made.result['document']['number']} is unpaid"
        assert made.result["to"] == [EMAIL]
        text = made.result["text"]
        assert DUE in text and "still unpaid" in text and "Pay here: https://pay.example/inv-1" in text
        again = act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 7})
        assert again.status != "committed" and again.code == CONFLICT and "already made" in again.reason, again
        assert act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 30}).status == "committed"
        assert c.execute("SELECT COUNT(*) FROM fin_invoice_reminders WHERE invoice_id=?", (iid,)).fetchone()[0] == 2


def test_only_an_unpaid_sent_invoice_is_reminded(books):
    with books.transaction() as c:
        made = act(c, OWNER, "contact_create", {"name": "Acme Draft", "kind": "customer", "emails": [EMAIL]})
        draft = str(act(c, OWNER, "invoice_create", {
            "customer_id": str(made.result["contact_id"]), "invoice_date": "2026-10-01", "due_date": "2026-10-31",
            "lines": [{"account_id": account(c, "4000"), "amount": 30_000}]}).result["invoice_id"])
        refused = act(c, OWNER, "invoice_remind", {"invoice_id": draft, "stage": 1})
        assert refused.status != "committed" and refused.code == CONFLICT, refused
        iid, _ = _invoice(c)
        assert act(c, OWNER, "invoice_record_payment", {"invoice_id": iid, "amount": 30_000}).status == "committed"
        paid = act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 1})
        assert paid.status != "committed" and paid.code == CONFLICT and "paid" in paid.reason, paid


def test_a_customer_with_no_email_is_refused_with_the_reason_and_nothing_is_recorded(books):
    with books.transaction() as c:
        iid, name = _invoice(c, emails=())
        refused = act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 1})
        assert refused.status != "committed" and refused.code == CONFLICT, refused
        assert name in refused.reason and "no email" in refused.reason, refused.reason
        assert c.execute("SELECT COUNT(*) FROM fin_invoice_reminders").fetchone()[0] == 0


def test_a_partly_paid_invoice_is_reminded_for_what_is_left(books):
    with books.transaction() as c:
        iid, _ = _invoice(c)
        _mailbox(c)
        assert act(c, OWNER, "invoice_record_payment", {"invoice_id": iid, "amount": 10_000}).status == "committed"
        made = act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 7})
        assert made.status == "committed", made
        assert "for 200.00 USD" in made.result["text"], made.result["text"]


def test_a_stage_outside_the_list_is_refused(books):
    with books.transaction() as c:
        iid, _ = _invoice(c)
        refused = act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 5})
        assert refused.status != "committed", refused
        assert "1, 7, 30, 60 or 90" in refused.reason, refused.reason


def test_an_invoice_with_no_due_date_is_not_late_so_no_stage_is_reminded(books):
    with books.transaction() as c:
        iid, _ = _invoice(c, due=None)
        _mailbox(c)
        refused = act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 1})
        assert refused.status == "failed" and refused.code == CONFLICT and "no due date" in refused.reason, refused
        assert c.execute("SELECT COUNT(*) FROM fin_invoice_reminders").fetchone()[0] == 0


def test_an_invoice_not_yet_due_is_refused_and_uses_no_stage(books):
    with books.transaction() as c:
        iid, _ = _invoice(c, due="2026-11-30", issued="2026-10-01")
        _mailbox(c)
        refused = act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 1})
        assert refused.status == "failed" and refused.code == CONFLICT and "not late yet" in refused.reason, refused
        assert c.execute("SELECT COUNT(*) FROM fin_invoice_reminders").fetchone()[0] == 0


def test_an_invoice_due_today_is_not_yet_past_due_until_the_next_day(books):
    with books.transaction() as c:
        iid, _ = _invoice(c, due="2026-10-15", issued="2026-10-01")
        _mailbox(c)
        refused = act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 1})
        assert refused.status == "failed" and refused.code == CONFLICT and "not late yet" in refused.reason, refused
        assert c.execute("SELECT COUNT(*) FROM fin_invoice_reminders").fetchone()[0] == 0
        day_after = act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 1}, at="2026-10-16T12:00:00.000000Z")
        assert day_after.status == "committed", day_after


def test_a_stage_not_yet_reached_is_refused_and_uses_no_stage_until_it_is_reached(books):
    with books.transaction() as c:
        iid, _ = _invoice(c, due="2026-10-10", issued="2026-10-01")          # 5 days late on AT
        _mailbox(c)
        early = act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 7})
        assert early.status == "failed" and early.code == CONFLICT and "waits until 2026-10-17" in early.reason, early
        assert c.execute("SELECT COUNT(*) FROM fin_invoice_reminders WHERE stage=7").fetchone()[0] == 0
        assert act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 1}).status == "committed"
        seventh = act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 7}, at="2026-10-17T12:00:00.000000Z")
        assert seventh.status == "committed", seventh
