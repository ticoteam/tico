"""Invoice emails (backend/finance/delivery.py): a reminder or an invoice reaches the customer only under a Tico `send`
approval that Finance issues when the action commits, for these recipients, this exact text and this document. The
Bookkeeper sends it; Finance never does. A bot cannot issue or decide one; a failed send raises one Needs-you, and so does
one no report covers for a day; and no Bookkeeper mailbox refuses a reminder at planning, before its stage is used.

An ask for an invoice email holds the email's intent (its addresses, subject, text, document and mailbox), resolved when
the action is planned. A change to any of it before the ask is decided voids the ask and asks again, with the new intent:
no send approval is issued for the changed email. A reminder is also checked against its due date when it is decided."""
import json

import pytest

from backend import bot_settings as BS
from backend import hubdb as H
from backend import mailbox_access as MA
from backend.finance import actions as FA
from backend.finance import delivery
from backend.finance import jobs
from backend.finance.core.errors import CONFLICT, DENIED, NOT_AVAILABLE, FinanceError
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401
from connectors.mail import policy as pl

BOT = "bot:bookkeeper"
EMAIL = "ap@acme-retail.example"
NEW_EMAIL = "ap@acme-new.example"
BOX = "books@acme.example"
OTHER_BOX = "billing@acme.example"
ISSUED = "2026-08-01"           # AT is 2026-10-15: due 2026-08-31 is 45 days late, so the stages 1, 7 and 30 are reached
DUE = "2026-08-31"
LATER = "2026-10-16T13:00:00.000000Z"


def _mailbox(c, can=("read", "send"), box=BOX) -> None:
    tools = [{"service": "gmail", "identity": box, "can": list(can)}]
    BS.write(c, "bookkeeper", {"team": "eng", "tools": tools})
    row = MA.ensure_mailbox(c, box, kind="shared", created_by=OWNER)
    MA.grant(c, OWNER, row["id"], "bot", "bookkeeper", "read_write" if "send" in can else "read")
    MA.grant(c, OWNER, row["id"], "person", "ana", "read_write")


def _customer(c, emails=(EMAIL,)) -> str:
    made = act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer", "emails": list(emails)})
    return str(made.result["contact_id"])


def _draft(c, customer: str, amount: int = 30_000) -> str:
    """A draft invoice to the customer, issued 2026-08-01 and due 2026-08-31."""
    payload = {"customer_id": customer, "invoice_date": ISSUED, "due_date": DUE,
               "lines": [{"account_id": account(c, "4000"), "amount": amount}]}
    return str(act(c, OWNER, "invoice_create", payload).result["invoice_id"])


def _sent_invoice(c, emails=(EMAIL,)) -> str:
    """A sent invoice of 300.00 to a customer with these emails, made as a person (no bot mailbox yet)."""
    FA.grant_defaults(c, BOT, at=AT)            # the rights a hired Bookkeeper comes with
    act(c, OWNER, "view_grant", {"subject": BOT, "what": "everything"})
    iid = _draft(c, _customer(c, emails))
    assert act(c, OWNER, "invoice_send", {"invoice_id": iid}).status == "committed"
    return iid


def _ask_mail(c, approval_id: str) -> dict:
    """The invoice email an ask names: what the person decides on (its shown payload's `mail`)."""
    return H.approval(c, approval_id)["payload"]["payload"]["mail"]


def _send_approvals(c) -> list[dict]:
    """Every send approval Finance has issued, as its payload names the email."""
    return [json.loads(row[0]) for row in c.execute("SELECT payload FROM approvals WHERE kind='send'").fetchall()]


def _approved_reminder(c, stage=7) -> tuple[str, str, str]:
    """A bot's reminder for the stage, asked, then approved by the person it was sent to: (invoice id, send approval id,
    the exact text)."""
    iid = _sent_invoice(c)
    _mailbox(c)
    asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": stage})
    assert asked.status == "needs_approval" and asked.approver == OWNER, asked
    H.approval_decide(c, OWNER, asked.approval_id, "approved")
    done = FA.approval_decided(c, asked.approval_id, AT)
    assert done.status == "committed", done
    return iid, str(done.result["send_approval_id"]), str(done.result["text"])


def _mail_reads(c, monkeypatch) -> None:
    """The mail connector reads a Tico approval through the hub; here it reads this test's database."""
    monkeypatch.setattr(pl, "HUB_GET", lambda path: H.approval(c, path.split("/", 1)[1]))


def test_a_bots_reminder_asks_a_person_and_records_nothing_until_they_say_yes(books):
    with books.transaction() as c:
        iid = _sent_invoice(c)
        _mailbox(c)
        asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": 7})
        assert asked.status == "needs_approval" and asked.approver == OWNER, asked
        assert c.execute("SELECT COUNT(*) FROM fin_invoice_reminders").fetchone()[0] == 0


def test_finance_yes_records_the_reminder_but_the_frozen_email_still_needs_its_own_preview(books):
    with books.transaction() as c:
        iid, send_id, text = _approved_reminder(c)
        appr = H.approval(c, send_id)
        assert appr["kind"] == "send" and appr["decision"] is None, appr
        payload = appr["payload"]
        assert payload["to"] == [EMAIL] and payload["cc"] == [] and payload["mailbox"] == BOX, payload
        assert payload["body_sha256"] == delivery.digest(text)
        queued = c.execute("SELECT * FROM email_sends WHERE id=?", (payload["email_send_id"],)).fetchone()
        assert queued["state"] == "waiting_approval" and payload["content_sha256"] == queued["content_sha256"]
        assert payload["bcc"] == [] and not c.execute("SELECT * FROM email_send_previews").fetchone()
        assert c.execute("SELECT send_approval_id FROM fin_invoice_reminders WHERE invoice_id=?",
                         (iid,)).fetchone()[0] == send_id


def test_the_mail_connector_sends_the_exact_text_to_the_exact_recipients_and_nothing_else(books, monkeypatch):
    with books.transaction() as c:
        _iid, send_id, text = _approved_reminder(c)
        H.approval_decide(c, OWNER, send_id, "approved")
        _mail_reads(c, monkeypatch)
        exact = pl.approval_check(send_id, [EMAIL], [], "", slug="bookkeeper", body=text)
        assert exact["full"] is True, exact
        changed = pl.approval_check(send_id, [EMAIL], [], "", slug="bookkeeper", body=text.replace("300", "30"))
        assert changed["full"] is False and "another text" in changed["detail"], changed
        other = pl.approval_check(send_id, ["dan@other.example"], [], "", slug="bookkeeper", body=text)
        assert other["full"] is False and "names to=" in other["detail"], other
        extra = pl.approval_check(send_id, [EMAIL, "dan@other.example"], [], "", slug="bookkeeper", body=text)
        assert extra["full"] is False, extra


def test_a_bot_cannot_issue_or_decide_a_send_approval(books):
    with books.transaction() as c:
        with pytest.raises(FinanceError) as refused:
            delivery.issue(c, BOT, BOT, to=[EMAIL], subject="Invoice", text="Invoice", box=BOX, document={}, ref="x")
        assert refused.value.code == DENIED
        asked = H.approval_request(c, BOT, "send", {"to": [EMAIL], "cc": [], "subject": "Invoice",
                                                    "body_sha256": delivery.digest("Invoice"), "mailbox": BOX},
                                   to=OWNER)
        with pytest.raises(H.Refused):
            H.approval_decide(c, BOT, asked["id"], "approved")


def test_no_bookkeeper_mailbox_refuses_the_reminder_at_planning_and_uses_no_stage(books):
    with books.transaction() as c:
        iid = _sent_invoice(c)
        refused = act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 7})
        assert refused.status == "denied" and refused.code == NOT_AVAILABLE and "mailbox" in refused.reason, refused
        _mailbox(c, can=("read",))
        assert "mailbox" in act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 7}).reason
        assert c.execute("SELECT COUNT(*) FROM fin_invoice_reminders").fetchone()[0] == 0
        _mailbox(c)
        assert act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 7}).status == "committed"


def test_a_failed_send_raises_one_needs_you_and_a_retry_that_delivers_clears_it(books):
    with books.transaction() as c:
        iid, send_id, _text = _approved_reminder(c)
        assert [item["approval_id"] for item in delivery.pending(c)] == [send_id]
        for reason in ("mail send failed: 550 no such user", "mail send failed: 550 again"):
            report = act(c, BOT, "invoice_delivery", {"invoice_id": iid, "stage": 7, "delivered": False,
                                                       "reason": reason})
            assert report.status == "committed", report
        titles = [r[0] for r in c.execute("SELECT title FROM tasks WHERE title LIKE '%not sent%'")]
        assert len(titles) == 1, titles
        assert [item["approval_id"] for item in delivery.pending(c)] == [send_id]  # the same approval; nothing remade
        assert act(c, BOT, "invoice_delivery", {"invoice_id": iid, "stage": 7, "delivered": True}).status == "committed"
        assert tuple(c.execute("SELECT delivered_at IS NOT NULL, delivery_reason FROM fin_invoice_reminders WHERE "
                               "invoice_id=?", (iid,)).fetchone()) == (1, None)
        assert delivery.pending(c) == []


def test_a_reminder_no_report_covers_for_a_day_raises_one_needs_you(books):
    with books.transaction() as c:
        _iid, _send_id, _text = _approved_reminder(c)
        jobs.watch(c, LATER)
        jobs.watch(c, LATER)
        titles = [r[0] for r in c.execute("SELECT title FROM tasks WHERE title LIKE '%no delivery report%'")]
        assert len(titles) == 1, titles


def test_an_invoice_send_by_a_bot_waits_for_a_person_then_its_email_goes_under_a_send_approval(books):
    with books.transaction() as c:
        iid = _sent_invoice(c)
        draft = str(act(c, OWNER, "invoice_create", {
            "customer_id": str(c.execute("SELECT customer_id FROM fin_invoices WHERE id=?", (iid,)).fetchone()[0]),
            "invoice_date": "2026-10-02", "due_date": "2026-11-01",
            "lines": [{"account_id": account(c, "4000"), "amount": 12_000}]}).result["invoice_id"])
        _mailbox(c)
        asked = act(c, BOT, "invoice_send", {"invoice_id": draft})
        assert asked.status == "needs_approval" and asked.approver == OWNER, asked
        H.approval_decide(c, OWNER, asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == "committed", done
        number = c.execute("SELECT number FROM fin_invoices WHERE id=?", (draft,)).fetchone()[0]
        assert done.result["delivery"] == "send_approval" and done.result["subject"] == f"Invoice {number}"
        appr = H.approval(c, done.result["send_approval_id"])
        assert appr["payload"]["body_sha256"] == delivery.digest(done.result["text"])
        assert appr["payload"]["to"] == [EMAIL]


def test_an_unchanged_ask_sends_under_a_send_approval_for_exactly_the_intent_it_named(books):
    with books.transaction() as c:
        FA.grant_defaults(c, BOT, at=AT)
        act(c, OWNER, "view_grant", {"subject": BOT, "what": "everything"})
        draft = _draft(c, _customer(c))
        _mailbox(c)
        asked = act(c, BOT, "invoice_send", {"invoice_id": draft})
        named = _ask_mail(c, asked.approval_id)
        H.approval_decide(c, OWNER, asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == "committed" and done.result["delivery"] == "send_approval", done
        issued = H.approval(c, done.result["send_approval_id"])["payload"]
        same = ("to", "cc", "subject", "body_sha256", "mailbox", "document_sha256")
        assert {k: issued[k] for k in same} == {k: named[k] for k in same}, (issued, named)
        assert named["to"] == [EMAIL] and named["mailbox"] == BOX and named["text"] == done.result["text"]


def test_a_retry_of_the_same_invoice_ask_replays_its_key(books):
    with books.transaction() as c:
        FA.grant_defaults(c, BOT, at=AT)
        act(c, OWNER, "view_grant", {"subject": BOT, "what": "everything"})
        draft = _draft(c, _customer(c))
        _mailbox(c)
        first = act(c, BOT, "invoice_send", {"invoice_id": draft}, key="invoice-send-retry")
        again = act(c, BOT, "invoice_send", {"invoice_id": draft}, key="invoice-send-retry")
        assert first.status == "needs_approval", first
        assert again.replayed and again.action_id == first.action_id and again.approval_id == first.approval_id


def test_an_invoice_email_asked_for_asks_again_when_the_customer_email_changes(books):
    """The ask names the old address. Changing the customer's email (contact_update) while it waits, then approving the
    ask, voids it: no send approval is issued for the new address, and the fresh ask names it instead."""
    with books.transaction() as c:
        FA.grant_defaults(c, BOT, at=AT)
        act(c, OWNER, "view_grant", {"subject": BOT, "what": "everything"})
        customer = _customer(c)
        draft = _draft(c, customer)
        _mailbox(c)
        asked = act(c, BOT, "invoice_send", {"invoice_id": draft})
        assert asked.status == "needs_approval" and asked.approver == OWNER, asked
        assert _ask_mail(c, asked.approval_id)["to"] == [EMAIL]
        assert act(c, OWNER, "contact_update", {"contact_id": customer, "emails": [NEW_EMAIL]}).status == "committed"
        H.approval_decide(c, OWNER, asked.approval_id, "approved")
        again = FA.approval_decided(c, asked.approval_id, AT)
        assert again.status == "needs_approval" and again.approval_id not in (None, asked.approval_id), again
        assert _ask_mail(c, again.approval_id)["to"] == [NEW_EMAIL]
        assert not any(NEW_EMAIL in appr["to"] for appr in _send_approvals(c)), _send_approvals(c)
        assert tuple(c.execute("SELECT status, send_approval_id FROM fin_invoices WHERE id=?",
                               (draft,)).fetchone()) == ("draft", None)
        # The fresh ask is decided like any other: approved, the email goes under a send approval for the new address.
        H.approval_decide(c, OWNER, again.approval_id, "approved")
        sent = FA.approval_decided(c, again.approval_id, AT)
        assert sent.status == "committed" and sent.result["delivery"] == "send_approval", sent
        assert H.approval(c, sent.result["send_approval_id"])["payload"]["to"] == [NEW_EMAIL]


def test_a_reminder_asked_for_asks_again_when_a_partial_payment_changes_its_text(books):
    with books.transaction() as c:
        iid = _sent_invoice(c)
        _mailbox(c)
        asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": 7})
        assert asked.status == "needs_approval", asked
        assert act(c, OWNER, "invoice_record_payment", {"invoice_id": iid, "amount": 10_000}).status == "committed"
        H.approval_decide(c, OWNER, asked.approval_id, "approved")
        again = FA.approval_decided(c, asked.approval_id, AT)
        assert again.status == "needs_approval" and again.approval_id != asked.approval_id, again
        assert "200.00" in _ask_mail(c, again.approval_id)["text"]          # 300.00 less the 100.00 paid
        assert c.execute("SELECT COUNT(*) FROM fin_invoice_reminders").fetchone()[0] == 0
        assert _send_approvals(c) == []


def test_a_reminder_asked_for_asks_again_when_the_sending_mailbox_changes(books):
    with books.transaction() as c:
        iid = _sent_invoice(c)
        _mailbox(c)
        asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": 7})
        assert asked.status == "needs_approval", asked
        _mailbox(c, box=OTHER_BOX)
        H.approval_decide(c, OWNER, asked.approval_id, "approved")
        again = FA.approval_decided(c, asked.approval_id, AT)
        assert again.status == "needs_approval" and again.approval_id != asked.approval_id, again
        assert _ask_mail(c, again.approval_id)["mailbox"] == OTHER_BOX
        assert c.execute("SELECT COUNT(*) FROM fin_invoice_reminders").fetchone()[0] == 0
        assert _send_approvals(c) == []


@pytest.mark.parametrize("due", [None, "2026-12-01"], ids=["due date cleared", "due date moved past today"])
def test_a_reminder_is_checked_against_its_due_date_again_when_it_is_approved(books, due):
    """No action edits a due date, so the test sets it directly: the ask was made while the invoice was late, and the
    approval is decided after the due date no longer says so. The reminder is refused at consume, not sent."""
    with books.transaction() as c:
        iid = _sent_invoice(c)
        _mailbox(c)
        asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": 7})
        assert asked.status == "needs_approval", asked
        c.execute("UPDATE fin_invoices SET due_date=? WHERE id=?", (due, iid))
        H.approval_decide(c, OWNER, asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == "failed" and done.code == CONFLICT, done
        assert c.execute("SELECT COUNT(*) FROM fin_invoice_reminders").fetchone()[0] == 0
        assert _send_approvals(c) == []
