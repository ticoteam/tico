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

BOT = "bot:finance-owner"
EMAIL = "ap@acme-retail.example"
NEW_EMAIL = "ap@acme-new.example"
BOX = "books@acme.example"
OTHER_BOX = "billing@acme.example"
ISSUED = "2026-07-01"  # AT is 2026-10-15: due 2026-08-01 is 75 days late, so the stages 1, 7, 30 and 60 are reached
DUE = "2026-08-01"
FINAL = 60  # a stage at the final notice: a bot's reminder asks a finance admin, and its email a person's OK
EARLY_DUE = "2026-09-01"  # 44 days late on AT: the early stages are reached, the final notice is not
LATER = "2026-10-16T13:00:00.000000Z"


def _mailbox(c, can=("read", "send"), box=BOX) -> None:
    tools = [{"service": "gmail", "identity": box, "can": list(can)}]
    BS.write(c, "finance-owner", {"team": "eng", "tools": tools})
    row = MA.ensure_mailbox(c, box, kind="shared", created_by=OWNER)
    MA.grant(c, OWNER, row["id"], "bot", "finance-owner", "read_write" if "send" in can else "read")
    MA.grant(c, OWNER, row["id"], "person", "ana", "read_write")


def _customer(c, emails=(EMAIL,)) -> str:
    made = act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer", "emails": list(emails)})
    return str(made.result["contact_id"])


def _draft(c, customer: str, amount: int = 30_000, due: str = DUE) -> str:
    """A draft invoice to the customer, issued 2026-07-01 and due 2026-08-01 (or `due`)."""
    payload = {
        "customer_id": customer,
        "invoice_date": ISSUED,
        "due_date": due,
        "lines": [{"account_id": account(c, "4000"), "amount": amount}],
    }
    return str(act(c, OWNER, "invoice_create", payload).result["invoice_id"])


def _sent_invoice(c, emails=(EMAIL,), due=DUE) -> str:
    """A sent invoice of 300.00 to a customer with these emails, made as a person (no bot mailbox yet)."""
    FA.grant_defaults(c, BOT, at=AT)  # the rights a hired Bookkeeper comes with
    act(c, OWNER, "view_grant", {"subject": BOT, "what": "everything"})
    iid = _draft(c, _customer(c, emails), due=due)
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


def _approved_reminder(c, stage=FINAL) -> tuple[str, str, str]:
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


def test_a_bots_final_stage_reminder_asks_a_person_and_records_nothing_until_they_say_yes(books):
    with books.transaction() as c:
        iid = _sent_invoice(c)
        _mailbox(c)
        asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": FINAL})
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
        queued = c.execute("SELECT * FROM outbound_email WHERE id=?", (found["subject"].removeprefix("email_send:"),))
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


def _company_sends_on_its_own(c, **asks) -> None:
    """A company sending policy that lets senders send new emails to new addresses with no OK (Settings → Mail), with
    no ask about money emails and no second review unless `asks` turns one on."""
    settings = {
        "mode": "on_their_own",
        "what": "replies_and_new",
        "ask_new_address": False,
        "ask_legal_money": False,
        "second_review": False,
        **asks,
    }
    c.execute(
        "INSERT OR REPLACE INTO email_sending_policies VALUES('company',?,?,?)",
        (json.dumps(settings), AT, OWNER.split(":")[1]),
    )


@pytest.mark.parametrize("stage", [1, 7, 30])
def test_a_bots_early_reminder_needs_no_finance_ok_and_waits_only_where_the_sending_policy_asks(books, stage):
    """Out of the box (Ask first) the email still waits for the policy's OK of its preview, but Finance asks nobody. The
    reminder is a payment demand: it is classified as money, for the policy's ask about money emails."""
    with books.transaction() as c:
        iid = _sent_invoice(c, due=EARLY_DUE)
        _mailbox(c)
        made = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": stage})
        assert made.status == "committed", made
        assert made.result["delivery"] == "send_approval" and made.result["send_approval_id"], made.result
        queued = c.execute(
            "SELECT state, classification FROM outbound_email WHERE operation_key=?", (f"invoice:{iid}:{stage}",)
        )
        row = queued.fetchone()
        assert row["state"] == "waiting_approval" and json.loads(row["classification"])["legal_or_money"] is True


def test_a_bots_early_reminder_goes_on_its_own_where_the_sending_policy_allows_and_is_recorded_delivered(books):
    from backend import email_sending as S
    from backend.auth import Auth

    with books.transaction() as c:
        iid = _sent_invoice(c, due=EARLY_DUE)
        _mailbox(c)
        _company_sends_on_its_own(c)
        made = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": 7})
        assert made.status == "committed", made
        assert made.result["delivery"] == "sending_policy" and made.result["send_approval_id"] is None, made.result
        send_id = made.result["send_id"]
        row = c.execute("SELECT * FROM outbound_email WHERE id=?", (send_id,)).fetchone()
        assert row["state"] == "pending" and row["approval_id"] == "" and row["via"] == "finance", dict(row)
        assert _send_approvals(c) == []
        follow = c.execute("SELECT send_approval_id FROM fin_invoice_reminders WHERE invoice_id=?", (iid,)).fetchone()
        assert follow[0] == delivery.SENT_PREFIX + send_id and not delivery.asked(follow[0])
        ((waiting,),) = [[(e["approval_id"], e["send_id"])] for e in delivery.pending(c)]
        assert waiting == (None, send_id)  # never a send id where an OK's id goes
        found = S.decision(c, Auth(books), row, dispatch=True)
        assert found["outcome"] == "allow", found
        S._permit(c, row, found)
        S._finish(c, row, "sent", "msg-1")
        assert c.execute(
            "SELECT delivered_at IS NOT NULL FROM fin_invoice_reminders WHERE invoice_id=?", (iid,)
        ).fetchone()[0]
        assert delivery.pending(c) == []


def test_a_bots_final_stage_reminder_still_waits_for_a_finance_admin_and_its_email_for_an_ok(books):
    """Even where the sending policy lets mail go on its own, the 60 and 90 day stages (the final notice) keep both asks."""
    with books.transaction() as c:
        iid = _sent_invoice(c)
        _mailbox(c)
        _company_sends_on_its_own(c)
        asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": FINAL})
        assert asked.status == "needs_approval" and asked.rule == "default:bot-invoice-email", asked
        give(c, OWNER, asked.approval_id)
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == "committed" and done.result["delivery"] == "send_approval", done
        row = c.execute(
            "SELECT state FROM outbound_email WHERE operation_key=?", (f"invoice:{iid}:{FINAL}",)
        ).fetchone()
        assert row["state"] == "waiting_approval"


@pytest.mark.parametrize(
    ("ask", "reason"), [("ask_legal_money", "legal_or_money_or_unclassified"), ("second_review", "second_review")]
)
def test_a_bots_early_reminder_waits_where_the_company_asks_about_money_or_wants_a_second_review(books, ask, reason):
    """The reminder is Tico's fixed text, but a bot asked for it: the policy's ask about money emails and its second
    review apply as they do to any of the bot's email."""
    with books.transaction() as c:
        iid = _sent_invoice(c, due=EARLY_DUE)
        _mailbox(c)
        _company_sends_on_its_own(c, **{ask: True})
        made = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": 7})
        assert made.status == "committed" and made.result["delivery"] == "send_approval", made
        row = c.execute("SELECT state, decision FROM outbound_email WHERE id=?", (_sent_ids(c)[0],)).fetchone()
        assert row["state"] == "waiting_approval" and reason in json.loads(row["decision"])["reasons"], dict(row)


def _sent_ids(c) -> list[str]:
    return [str(r[0]) for r in c.execute("SELECT id FROM outbound_email ORDER BY created_at, rowid")]


def _permit_send(c, row, found):
    from backend import email_sending as S, outbound_email as OE

    now = H.now()
    OE.queue(c, row["id"], now, S.dump(found))
    assert OE.claim(c, row, now, S.SERVER, S.dump(found), "external")
    leased = OE.get(c, row["id"])
    assert S._permit(c, leased, found)
    return leased


def _sends(books, c, send_id: str) -> None:
    """The queue lets a send the policy allowed go, and the provider takes it."""
    from backend import email_sending as S
    from backend.auth import Auth

    long_ago = "2000-01-01T00:00:00.000000Z"  # earlier sends went out well before: no pacing wait
    c.execute(
        "UPDATE outbound_actions SET provider_attempted_at=?, claimed_at=? WHERE channel='email' AND state='done'",
        (long_ago, long_ago),
    )
    row = c.execute("SELECT * FROM outbound_email WHERE id=?", (send_id,)).fetchone()
    found = S.decision(c, Auth(books), row, dispatch=True)
    assert found["outcome"] == "allow", found
    row = _permit_send(c, row, found)
    S._finish(c, row, "sent", "msg-" + send_id)


def test_each_stage_is_its_own_email_day_1_then_7_then_60(books):
    """The same balance and addresses at every stage: each stage still queues and sends its own email. Day 1 and day 7
    go on their own under the policy; day 60 waits for a finance admin and its own send approval."""
    due = "2026-10-14"  # AT (2026-10-15) is day 1
    with books.transaction() as c:
        iid = _sent_invoice(c, due=due)
        _mailbox(c)
        _company_sends_on_its_own(c)
        first = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": 1})
        assert first.status == "committed" and first.result["delivery"] == "sending_policy", first
        _sends(books, c, first.result["send_id"])
        week = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": 7}, at="2026-10-21T12:00:00.000000Z")
        assert week.status == "committed" and week.result["delivery"] == "sending_policy", week
        assert week.result["send_id"] != first.result["send_id"]
        assert week.result["subject"] != first.result["subject"]
        _sends(books, c, week.result["send_id"])
        final_at = "2026-12-13T12:00:00.000000Z"  # day 60
        asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": FINAL}, at=final_at)
        assert asked.status == "needs_approval" and asked.rule == "default:bot-invoice-email", asked
        give(c, OWNER, asked.approval_id)
        done = FA.approval_decided(c, asked.approval_id, final_at)
        assert done.status == "committed" and done.result["delivery"] == "send_approval", done
        states = [r[0] for r in c.execute("SELECT state FROM outbound_email ORDER BY created_at, rowid")]
        assert states == ["sent", "sent", "waiting_approval"], states
        delivered = c.execute("SELECT stage FROM fin_invoice_reminders WHERE delivered_at IS NOT NULL ORDER BY stage")
        assert [r[0] for r in delivered] == [1, 7]


def test_an_early_stage_first_run_once_the_invoice_is_60_days_late_waits_like_the_final_notice(books):
    """A day-30 task first run on day 75: the invoice is at the final notice, so whatever stage was asked, the bot asks
    a finance admin and the email waits for a person's OK, even where the policy lets mail go on its own."""
    with books.transaction() as c:
        iid = _sent_invoice(c)  # 75 days late on AT
        _mailbox(c)
        _company_sends_on_its_own(c)
        asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": 30})
        assert asked.status == "needs_approval" and asked.rule == "default:bot-invoice-email", asked
        give(c, OWNER, asked.approval_id)
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == "committed" and done.result["delivery"] == "send_approval", done
        assert c.execute("SELECT state FROM outbound_email").fetchone()[0] == "waiting_approval"


def test_an_early_reminder_asked_before_day_60_and_decided_after_it_asks_a_finance_admin_again(books):
    """Days late are judged again when an approval is decided: a day-30 reminder an owner's rule held on day 55 and
    approved on day 65 now needs the final notice's ask of a finance admin, which that approval did not cover, so it is
    asked again and nothing is queued."""
    with books.transaction() as c:
        iid = _sent_invoice(c, due=EARLY_DUE)
        _mailbox(c)
        _company_sends_on_its_own(c)
        rule = act(
            c,
            OWNER,
            "rule_create",
            {
                "kind": "gate",
                "name": "Reminders ask the owner",
                "when": {"kind": "invoice_remind"},
                "outcome": {"outcome": "needs_approval", "who": "owner"},
                "examples": [{"input": {"kind": "invoice_remind"}, "expect": "needs_approval"}],
            },
        )
        assert rule.status == "committed", rule
        day_55 = "2026-10-26T12:00:00.000000Z"
        asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": 30}, at=day_55)
        assert asked.status == "needs_approval" and asked.rule == rule.result["rule_id"], asked
        give(c, OWNER, asked.approval_id)
        again = FA.approval_decided(c, asked.approval_id, "2026-11-05T12:00:00.000000Z")  # day 65
        assert again.status == "needs_approval" and again.approval_id != asked.approval_id, again
        assert c.execute("SELECT count(*) FROM outbound_email").fetchone()[0] == 0


def test_a_policy_sent_reminder_that_the_queue_later_asks_about_shows_that_ok(books):
    """delivery.pending never puts a send id where an OK's id goes: a reminder the policy let go has no approval_id,
    and when the policy tightens before it goes, the queue's new OK is the one shown."""
    from backend import email_sending as S
    from backend.auth import Auth

    with books.transaction() as c:
        iid = _sent_invoice(c, due=EARLY_DUE)
        _mailbox(c)
        _company_sends_on_its_own(c)
        made = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": 7})
        send_id = made.result["send_id"]
        assert [(e["approval_id"], e["send_id"]) for e in delivery.pending(c)] == [(None, send_id)]
        _company_sends_on_its_own(c, mode="ask_first")
        c.execute("UPDATE outbound_actions SET send_after=? WHERE id=?", ("2000-01-01T00:00:00.000000Z", send_id))
        S._lease(c, Auth(books), BOX, "r1")
        approval = c.execute("SELECT approval_id FROM outbound_email WHERE id=?", (send_id,)).fetchone()[0]
        assert approval and [(e["approval_id"], e["send_id"]) for e in delivery.pending(c)] == [(approval, send_id)]


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
        assert c.execute("SELECT count(*) FROM outbound_email").fetchone()[0] == 0


def test_a_failed_send_raises_one_needs_you_and_a_retry_that_delivers_clears_it(books):
    with books.transaction() as c:
        iid, send_id, _text = _approved_reminder(c)
        assert [item["approval_id"] for item in delivery.pending(c)] == [send_id]
        for reason in ("mail send failed: 550 no such user", "mail send failed: 550 again"):
            report = act(
                c, BOT, "invoice_delivery", {"invoice_id": iid, "stage": FINAL, "delivered": False, "reason": reason}
            )
            assert report.status == "committed", report
        titles = [r[0] for r in c.execute("SELECT title FROM tasks WHERE title LIKE '%not sent%'")]
        assert len(titles) == 1, titles
        assert [item["approval_id"] for item in delivery.pending(c)] == [send_id]  # the same approval; nothing remade
        assert (
            act(c, BOT, "invoice_delivery", {"invoice_id": iid, "stage": FINAL, "delivered": True}).status
            == "committed"
        )
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
    row = c.execute("SELECT * FROM outbound_email WHERE id=?", (sid,)).fetchone()
    shown = S.shown_to(c, Identity(OWNER, "human"), row)
    task_asks.give_ok(c, OWNER, send_id, shown, own_click=True, ctx=task_asks.Context(auth=auth))
    row = c.execute("SELECT * FROM outbound_email WHERE id=?", (sid,)).fetchone()
    found = S.decision(c, auth, row, dispatch=True)
    assert found["approved"], found
    row = _permit_send(c, row, found)
    S._finish(c, row, status, "msg-1" if status == "sent" else "", detail)
    return dict(c.execute("SELECT * FROM outbound_email WHERE id=?", (sid,)).fetchone())


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
        asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": FINAL})
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
        asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": FINAL})
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
        asked = act(c, BOT, "invoice_remind", {"invoice_id": iid, "stage": FINAL})
        assert asked.status == "needs_approval", asked
        c.execute("UPDATE fin_invoices SET due_date=? WHERE id=?", (due, iid))
        give(c, OWNER, asked.approval_id)
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == "failed" and done.code == CONFLICT, done
        assert c.execute("SELECT COUNT(*) FROM fin_invoice_reminders").fetchone()[0] == 0
        assert _send_approvals(c) == []
