"""Invoice emails (backend/finance/delivery.py): a reminder or an invoice reaches the customer only under a Tico `send`
approval that Finance issues when the action commits, for these recipients, this exact text and this document. The
Bookkeeper sends it; Finance never does. A bot cannot issue or decide one; a failed send raises one Needs-you, and so does
one no report covers for a day; and no Bookkeeper mailbox refuses a reminder at planning, before its stage is used."""
import json

import pytest

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import delivery
from backend.finance import jobs
from backend.finance.core.errors import DENIED, NOT_AVAILABLE, FinanceError
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401
from connectors.mail import policy as pl

BOT = "bot:bookkeeper"
EMAIL = "ap@acme-retail.example"
BOX = "books@acme.example"
LATER = "2026-10-16T13:00:00.000000Z"


def _mailbox(c, can=("read", "send")) -> None:
    tools = [{"service": "gmail", "identity": BOX, "can": list(can)}]
    c.execute("UPDATE bot_config SET config_json=? WHERE bot='bookkeeper'",
              (json.dumps({"team": "eng", "tools": tools}),))


def _sent_invoice(c, emails=(EMAIL,)) -> str:
    """A sent invoice of 300.00 to a customer with these emails, made as a person (no bot mailbox yet)."""
    FA.grant_defaults(c, BOT, at=AT)            # the rights a hired Bookkeeper comes with
    act(c, OWNER, "view_grant", {"subject": BOT, "what": "everything"})
    made = act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer", "emails": list(emails)})
    payload = {"customer_id": str(made.result["contact_id"]), "invoice_date": "2026-10-01", "due_date": "2026-10-31",
               "lines": [{"account_id": account(c, "4000"), "amount": 30_000}]}
    iid = str(act(c, OWNER, "invoice_create", payload).result["invoice_id"])
    assert act(c, OWNER, "invoice_send", {"invoice_id": iid}).status == "committed"
    return iid


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


def test_a_person_yes_records_the_reminder_and_issues_a_send_approval_for_its_exact_text(books):
    with books.transaction() as c:
        iid, send_id, text = _approved_reminder(c)
        appr = H.approval(c, send_id)
        assert appr["kind"] == "send" and appr["decision"] == "approved" and appr["decided_by"] == OWNER, appr
        payload = appr["payload"]
        assert payload["to"] == [EMAIL] and payload["cc"] == [] and payload["mailbox"] == BOX, payload
        assert payload["body_sha256"] == delivery.digest(text)
        assert c.execute("SELECT send_approval_id FROM fin_invoice_reminders WHERE invoice_id=?",
                         (iid,)).fetchone()[0] == send_id


def test_the_mail_connector_sends_the_exact_text_to_the_exact_recipients_and_nothing_else(books, monkeypatch):
    with books.transaction() as c:
        _iid, send_id, text = _approved_reminder(c)
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
