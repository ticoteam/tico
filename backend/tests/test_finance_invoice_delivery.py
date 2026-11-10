"""Invoice emails (backend/finance/delivery.py): a reminder or an invoice reaches the customer only under an OK of its
frozen send (backend/task_asks.py, `email_send:<id>`) that Finance asks for when the action commits, for these
recipients and this exact text. The
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
from backend import task_asks
from backend.finance import actions as FA
from backend.finance import delivery
from backend.finance import jobs
from backend.finance.core.errors import CONFLICT, DENIED, NOT_AVAILABLE, FinanceError
from backend.tests.helpers.finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401
from backend.tests.helpers.finance_oks import give

BOT = "bot:bookkeeper"
EMAIL = "ap@acme-retail.example"
NEW_EMAIL = "ap@acme-new.example"
BOX = "books@acme.example"
OTHER_BOX = "billing@acme.example"
ISSUED = "2026-08-01"  # AT is 2026-10-15: due 2026-08-31 is 45 days late, so the stages 1, 7 and 30 are reached
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
    payload = {
        "customer_id": customer,
        "invoice_date": ISSUED,
        "due_date": DUE,
        "lines": [{"account_id": account(c, "4000"), "amount": amount}],
    }
    return str(act(c, OWNER, "invoice_create", payload).result["invoice_id"])


def _sent_invoice(c, emails=(EMAIL,)) -> str:
    """A sent invoice of 300.00 to a customer with these emails, made as a person (no bot mailbox yet)."""
    FA.grant_defaults(c, BOT, at=AT)  # the rights a hired Bookkeeper comes with
    act(c, OWNER, "view_grant", {"subject": BOT, "what": "everything"})
    iid = _draft(c, _customer(c, emails))
    assert act(c, OWNER, "invoice_send", {"invoice_id": iid}).status == "committed"
    return iid


def _ask_mail(c, approval_id: str) -> dict:
    """The invoice email an ask names: what the person decides on (its shown payload's `mail`)."""
    return task_asks.grant(c, approval_id)["content"]["payload"]["mail"]


def _send_ok(c, send_id: str) -> dict:
    """What an email's send OK is for (email_sending.content): the mailbox, recipients, subject and hashes."""
    return task_asks.grant(c, send_id)["content"]


def _send_approvals(c) -> list[dict]:
    """Every send OK asked for, as its content names the email."""
    rows = c.execute("SELECT requirement_id FROM ok_grants WHERE subject LIKE 'email_send:%'").fetchall()
    return [_send_ok(c, str(row[0])) for row in rows]


def _approved_reminder(c, stage=7) -> tuple[str, str, str]:
    """A bot's reminder for the stage, asked, then approved by the person it was sent to: (invoice id, send approval id,
    the exact text)."""
    iid = _sent_invoice(c)
    _mailbox(c)
    asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": stage})
    assert asked.status == "needs_approval" and asked.approver == OWNER, asked
    give(c, OWNER, asked.approval_id)
    done = FA.approval_decided(c, asked.approval_id, AT)
    assert done.status == "committed", done
    return iid, str(done.result["send_approval_id"]), str(done.result["text"])


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
        found = task_asks.grant(c, send_id)
        assert found["subject"].startswith("email_send:") and task_asks.standing(c, send_id) == task_asks.WAITING
        payload = found["content"]
        assert payload["recipients"] == [EMAIL] and payload["mailbox"] == BOX, payload
        assert payload["body_sha256"] == delivery.digest(text)
        queued = c.execute("SELECT * FROM email_sends WHERE id=?", (found["subject"].removeprefix("email_send:"),))
        queued = queued.fetchone()
        assert queued["state"] == "waiting_approval" and payload["content_sha256"] == queued["content_sha256"]
        assert not c.execute("SELECT * FROM email_send_previews").fetchone()
        assert (
            c.execute("SELECT send_approval_id FROM fin_invoice_reminders WHERE invoice_id=?", (iid,)).fetchone()[0]
            == send_id
        )


def test_a_bot_cannot_issue_or_decide_a_send_approval(books):
    with books.transaction() as c:
        with pytest.raises(FinanceError) as refused:
            delivery.issue(c, BOT, BOT, to=[EMAIL], subject="Invoice", text="Invoice", box=BOX, document={}, ref="x")
        assert refused.value.code == DENIED
        _, send_id, _ = _approved_reminder(c)
        with pytest.raises(H.Refused):
            give(c, BOT, send_id)


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


def test_finance_does_not_queue_email_when_the_requester_lacks_inbox_send_access(books):
    with books.transaction() as c:
        iid = _sent_invoice(c)
        _mailbox(c)
        row = c.execute("SELECT id FROM mailboxes WHERE address=?", (BOX,)).fetchone()
        c.execute("DELETE FROM mailbox_accesses WHERE mailbox_id=? AND grantee_type='person'", (row[0],))
        c.execute("UPDATE mailboxes SET kind='personal',person_id='ben' WHERE id=?", (row[0],))
        refused = act(c, OWNER, "invoice_remind", {"invoice_id": iid, "stage": 7})
        assert refused.status == "failed" and "sender cannot send" in refused.reason
        assert c.execute("SELECT count(*) FROM email_sends").fetchone()[0] == 0


def test_a_failed_send_raises_one_needs_you_and_a_retry_that_delivers_clears_it(books):
    with books.transaction() as c:
        iid, send_id, _text = _approved_reminder(c)
        assert [item["approval_id"] for item in delivery.pending(c)] == [send_id]
        for reason in ("mail send failed: 550 no such user", "mail send failed: 550 again"):
            report = act(
                c, BOT, "invoice_delivery", {"invoice_id": iid, "stage": 7, "delivered": False, "reason": reason}
            )
            assert report.status == "committed", report
        titles = [r[0] for r in c.execute("SELECT title FROM tasks WHERE title LIKE '%not sent%'")]
        assert len(titles) == 1, titles
        assert [item["approval_id"] for item in delivery.pending(c)] == [send_id]  # the same approval; nothing remade
        assert act(c, BOT, "invoice_delivery", {"invoice_id": iid, "stage": 7, "delivered": True}).status == "committed"
        assert tuple(
            c.execute(
                "SELECT delivered_at IS NOT NULL, delivery_reason FROM fin_invoice_reminders WHERE invoice_id=?", (iid,)
            ).fetchone()
        ) == (1, None)
        assert delivery.pending(c) == []


def _sent_by_the_queue(books, c, send_id: str, status: str, detail: str = "") -> dict:
    """The person OKs the frozen email from its preview, the queue claims the OK and hands the email to the provider,
    and the provider answers `status` (email_sending: _permit, then _finish)."""
    from backend import email_sending as S
    from backend.auth import Auth, Identity

    auth = Auth(books)
    sid = task_asks.grant(c, send_id)["subject"].removeprefix("email_send:")
    row = c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone()
    shown = S.shown_to(c, Identity(OWNER, "human"), row)
    task_asks.give_ok(c, OWNER, send_id, shown, own_click=True, ctx=task_asks.Context(auth=auth))
    row = c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone()
    found = S.decision(c, auth, row, dispatch=True)
    assert found["approved"], found
    S._permit(c, row, found)
    S._finish(c, row, status, "msg-1" if status == "sent" else "", detail)
    return dict(c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone())


def test_the_queue_records_a_delivered_invoice_email_on_the_invoice(books):
    with books.transaction() as c:
        iid, send_id, _text = _approved_reminder(c)
        _sent_by_the_queue(books, c, send_id, "sent")
        assert tuple(
            c.execute(
                "SELECT delivered_at IS NOT NULL, delivery_reason FROM fin_invoice_reminders WHERE invoice_id=?", (iid,)
            ).fetchone()
        ) == (1, None)
        assert delivery.pending(c) == []
        assert H.task(c, task_asks.task_of(c, send_id))["status"] == "done"


@pytest.mark.parametrize("status", ["failed", "unknown"])
def test_a_send_the_provider_did_not_take_is_recorded_and_its_ok_does_not_look_sent(books, status):
    """The OK was claimed before the provider was called, so it lets nothing else go. The provider's failure is
    recorded on the invoice (one Needs-you), and the OK's task ends closed with what happened, never done as sent."""
    with books.transaction() as c:
        iid, send_id, _text = _approved_reminder(c)
        _sent_by_the_queue(books, c, send_id, status, "550 no such user" if status == "failed" else "")
        reason = c.execute("SELECT delivery_reason FROM fin_invoice_reminders WHERE invoice_id=?", (iid,)).fetchone()[0]
        assert reason and (("550" in reason) if status == "failed" else ("confirm" in reason)), reason
        assert len([r for r in c.execute("SELECT title FROM tasks WHERE title LIKE '%not sent%'")]) == 1
        assert task_asks.standing(c, send_id) == task_asks.USED
        task = H.task(c, task_asks.task_of(c, send_id))
        assert task["status"] == "closed" and "Sent the email." not in (task.get("note") or ""), task


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
        draft = str(
            act(
                c,
                OWNER,
                "invoice_create",
                {
                    "customer_id": str(
                        c.execute("SELECT customer_id FROM fin_invoices WHERE id=?", (iid,)).fetchone()[0]
                    ),
                    "invoice_date": "2026-10-02",
                    "due_date": "2026-11-01",
                    "lines": [{"account_id": account(c, "4000"), "amount": 12_000}],
                },
            ).result["invoice_id"]
        )
        _mailbox(c)
        asked = act(c, BOT, "invoice_send", {"invoice_id": draft})
        assert asked.status == "needs_approval" and asked.approver == OWNER, asked
        give(c, OWNER, asked.approval_id)
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == "committed", done
        number = c.execute("SELECT number FROM fin_invoices WHERE id=?", (draft,)).fetchone()[0]
        assert done.result["delivery"] == "send_approval" and done.result["subject"] == f"Invoice {number}"
        sent = _send_ok(c, done.result["send_approval_id"])
        assert sent["body_sha256"] == delivery.digest(done.result["text"])
        assert sent["recipients"] == [EMAIL]


def test_an_unchanged_ask_sends_under_a_send_approval_for_exactly_the_intent_it_named(books):
    with books.transaction() as c:
        FA.grant_defaults(c, BOT, at=AT)
        act(c, OWNER, "view_grant", {"subject": BOT, "what": "everything"})
        draft = _draft(c, _customer(c))
        _mailbox(c)
        asked = act(c, BOT, "invoice_send", {"invoice_id": draft})
        named = _ask_mail(c, asked.approval_id)
        give(c, OWNER, asked.approval_id)
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == "committed" and done.result["delivery"] == "send_approval", done
        issued = _send_ok(c, done.result["send_approval_id"])
        same = ("subject", "body_sha256", "mailbox")
        assert {k: issued[k] for k in same} == {k: named[k] for k in same}, (issued, named)
        assert issued["recipients"] == named["to"] and named["cc"] == []
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
        give(c, OWNER, asked.approval_id)
        again = FA.approval_decided(c, asked.approval_id, AT)
        assert again.status == "needs_approval" and again.approval_id not in (None, asked.approval_id), again
        assert _ask_mail(c, again.approval_id)["to"] == [NEW_EMAIL]
        assert not any(NEW_EMAIL in appr["recipients"] for appr in _send_approvals(c)), _send_approvals(c)
        assert tuple(
            c.execute("SELECT status, send_approval_id FROM fin_invoices WHERE id=?", (draft,)).fetchone()
        ) == ("draft", None)
        # The fresh ask is decided like any other: approved, the email goes under a send approval for the new address.
        give(c, OWNER, again.approval_id)
        sent = FA.approval_decided(c, again.approval_id, AT)
        assert sent.status == "committed" and sent.result["delivery"] == "send_approval", sent
        assert _send_ok(c, sent.result["send_approval_id"])["recipients"] == [NEW_EMAIL]


def test_a_reminder_asked_for_asks_again_when_a_partial_payment_changes_its_text(books):
    with books.transaction() as c:
        iid = _sent_invoice(c)
        _mailbox(c)
        asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": 7})
        assert asked.status == "needs_approval", asked
        assert act(c, OWNER, "invoice_record_payment", {"invoice_id": iid, "amount": 10_000}).status == "committed"
        give(c, OWNER, asked.approval_id)
        again = FA.approval_decided(c, asked.approval_id, AT)
        assert again.status == "needs_approval" and again.approval_id != asked.approval_id, again
        assert "200.00" in _ask_mail(c, again.approval_id)["text"]  # 300.00 less the 100.00 paid
        assert c.execute("SELECT COUNT(*) FROM fin_invoice_reminders").fetchone()[0] == 0
        assert _send_approvals(c) == []


def test_a_reminder_asked_for_asks_again_when_the_sending_mailbox_changes(books):
    with books.transaction() as c:
        iid = _sent_invoice(c)
        _mailbox(c)
        asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": 7})
        assert asked.status == "needs_approval", asked
        _mailbox(c, box=OTHER_BOX)
        give(c, OWNER, asked.approval_id)
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
        give(c, OWNER, asked.approval_id)
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == "failed" and done.code == CONFLICT, done
        assert c.execute("SELECT COUNT(*) FROM fin_invoice_reminders").fetchone()[0] == 0
        assert _send_approvals(c) == []
