"""Finance phase 2: bills, invoices, contacts, settlements and the cash view, posting rules, the remaining defaults,
the new events, the close, and the reads that show them (search, report, metric, inbox) cut to each viewer."""
import json
import random
import time

import pytest

from backend.credentials import Vault
from backend.finance import actions as FA
from backend.finance import sync
from backend.finance import reads as FR
from backend.finance.core import feeds as F
from backend.finance.store import ledger as SL
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, account, act, books, connect, finance_template, store, sync_once)
from backend.tests.test_finance_payments import HOURS, _checking, _payee

PLAIN = "human:eve"          # a web teammate with no grants or rights
LATER = "2026-10-20T12:00:00.000000Z"


def _vendor(c, name="Acme Supplies", **more) -> str:
    made = act(c, OWNER, "contact_create", {"name": name, "kind": "vendor", **more})
    assert made.status == "committed", made
    return str(made.result["contact_id"])


def _bill(c, vendor, lines, who=OWNER, day="2026-10-10", **more) -> str:
    made = act(c, who, "bill_create", {"vendor_id": vendor, "bill_date": day, "lines": lines, **more})
    assert made.status == "committed", made
    return str(made.result["bill_id"])


def _balance(c, code) -> int:
    return int(c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id "
                         "WHERE a.code=?", (code,)).fetchone()[0])


def _pnl(c, basis, start="2026-10-01", end="2026-12-31") -> dict[str, int]:
    found = FR.pnl(c, OWNER, start, end, "year", basis, AT)
    return {str(r["code"]): int(r["total"]) for r in found["rows"] if r["total"]}


def _events(c, name) -> list[tuple[str, str]]:
    return [tuple(r) for r in c.execute("SELECT subject, period_key FROM fin_events WHERE name=? ORDER BY created_at",
                                        (name,))]


def _ingest(c, raw, at=AT):
    from backend.finance import banking
    from backend.finance.context import load
    cid = str(c.execute("SELECT id FROM fin_connections").fetchone()[0])
    return banking.ingest(load(c, at), cid, F.mercury_txn(raw, 0))


def _txn(raw_id, amount, who="Acme Supplies", day="2026-10-14", kind="outgoingPayment", **extra):
    return {**MERCURY["txn_bank_fee"], "id": raw_id, "accountId": "acct-chk", "amount": amount, "kind": kind,
            "createdAt": f"{day}T10:00:00Z", "postedAt": f"{day}T10:00:00Z", "counterpartyName": who, **extra}


def test_every_new_read_answers_on_empty_books_and_refuses_what_the_viewer_cannot_see(books):  # noqa: F811
    with books.read() as c:
        assert FR.bills(c, OWNER)["bills"] == [] and FR.invoices(c, OWNER)["open"]["remaining"] == 0
        assert FR.bills(c, OWNER)["bills_email"] is None
        assert FR.contacts(c, OWNER)["contacts"] == []
        assert FR.search(c, OWNER, kind=None, q="acme")["results"] == []
        assert FR.inbox(c, OWNER)["items"] == []
        for kind in FR.REPORTS:
            assert FR.report(c, OWNER, kind, start="2026-10-01", end="2026-10-31", compare="prior")["kind"] == kind
        for name in FR.METRICS:
            if not name.startswith("budget_"):
                assert FR.metric(c, OWNER, name, at=AT)["as_of"] == AT
        assert FR.close_checklist(c, OWNER, "2026-09")["month"] == "2026-09"
        for read in (FR.bills, FR.invoices, FR.contacts, FR.chart, FR.pay_from):
            with pytest.raises(FA.FinanceError):
                read(c, PLAIN)
        with pytest.raises(FA.FinanceError):
            FR.metric(c, PLAIN, "cash")
        assert FR.search(c, PLAIN, kind=None)["results"] == []
        assert FA.help("bill_create")["fields"][0]["name"] == "vendor_id"
        codes = {a["number"] for a in FR.chart(c, OWNER, for_="bill")["accounts"]}
        assert "6100" in codes and "2000" not in codes and "4000" not in codes
    with books.transaction() as c:
        FA.set_bills_email(c, "bills@acme.example")
        assert FR.bills(c, OWNER)["bills_email"] == "bills@acme.example"


def test_a_bill_holds_budget_posts_ap_once_and_its_scheduled_payment_clears_ap_without_a_second_expense(books):
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        vendor = _vendor(c, recipient_id=rid)
        bid = _bill(c, vendor, [{"account_id": account(c, "6100"), "amount": 40_000, "tags": {"team": "eng"}},
                                {"account_id": account(c, "6100"), "amount": 10_000}], due_date="2026-10-15")
        assert [tuple(r) for r in c.execute("SELECT amount, status FROM fin_holds WHERE source='bill'")] == \
            [(40_000, "held"), (10_000, "held")]
        assert _events(c, "finance.bill.received") == [(bid, "")]
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).code == "conflict"          # entered once
        assert {tuple(r) for r in c.execute("SELECT status FROM fin_holds WHERE source='bill'")} == {("settled",)}
        assert (_balance(c, "6100"), _balance(c, "2000")) == (50_000, -50_000)
        assert act(c, OWNER, "bill_schedule", {"bill_id": bid, "pay_on": "2026-10-15",
                                               "money_account_id": _checking(c)}).status == "committed"
        assert FA.watch_documents(c, AT) >= 1
        pay = c.execute("SELECT id, amount, obligation, status FROM fin_payments").fetchone()
        assert (pay[1], pay[2]) == (50_000, f"bill:{bid}")
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "account_id": account(c, "1000")}).code == "conflict"
    sync_once(books, fake, HOURS[1])
    fake.txns["txn-pay"] = _txn("txn-pay", -500.0, requestId="req-1", postedAt="2026-10-16T10:00:00Z",
                                createdAt="2026-10-16T10:00:00Z")
    fake.events_list.append({"id": "evt-pay", "resourceType": "transaction", "resourceId": "txn-pay",
                             "operationType": "create", "resourceVersion": 1})
    sync_once(books, fake, HOURS[3])
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_payments").fetchone()[0] == "settled"
        bill = FR.bill(c, OWNER, bid)
        assert (bill["status"], bill["remaining"]) == ("paid", 0)
        assert (_balance(c, "6100"), _balance(c, "2000"), _balance(c, "6900")) == (50_000, 0, 0)
        assert _pnl(c, "cash").get("6100") == _pnl(c, "accrual").get("6100") == 50_000


def _scheduled_today(c, ask=False) -> str:
    """Acme Supplies by ACH (not at Mercury yet), a 500.00 bill from them entered and scheduled for today. With `ask`,
    paying a new recipient asks the finance approver, dan (the default rule)."""
    if ask:
        FA.setup(c, OWNER, AT, finance_approver="human:dan")
    else:
        assert act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"}).status == "committed"
    added = act(c, OWNER, "recipient_add", {"name": "Acme Supplies", "method": "ach",
                                            "account_number": "000099998888", "routing_number": "000000000"})
    assert added.status == "committed", added
    bid = _bill(c, _vendor(c, recipient_id=str(added.result["recipient_id"])),
                [{"account_id": account(c, "6100"), "amount": 50_000}], due_date="2026-10-20")
    assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
    done = act(c, OWNER, "bill_schedule", {"bill_id": bid, "pay_on": AT[:10], "money_account_id": _checking(c)})
    assert done.status == "committed", done
    return bid


def test_a_scheduled_bills_payment_reaches_mercury_on_commit_without_waiting_for_a_sweep(books):
    """B: the hourly job's payment and its new recipient ring the bank worker after the commit (never before: the
    worker would find nothing and sleep until its sweep), and the payment waiting on its recipient goes out in the pass
    that creates the recipient at Mercury."""
    fake = FakeMercury()
    connect(books, fake)
    sync.start(books, Vault(books), make=lambda _token: fake)
    try:
        time.sleep(0.5)                                               # the worker's first pass, then its doorbell
        rung = sync.BELL.rung
        with books.transaction() as c:
            bid = _scheduled_today(c)
            FA.watch_documents(c, AT)
            assert sync.BELL.rung == rung                             # not before the commit
        assert sync.BELL.rung > rung
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not fake.sent("request_send_money"):
            time.sleep(0.05)
        assert len(fake.sent("create_recipient")) == 1 and len(fake.sent("request_send_money")) == 1
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with books.read() as c:
                bill = FR.bill(c, OWNER, bid)
            if bill["payment"]["status"] == "awaiting_bank_approval":
                break
            time.sleep(0.05)
        assert (bill["status"], bill["pay_on"], bill["payment"]["status"]) == \
            ("scheduled", AT[:10], "awaiting_bank_approval")
    finally:
        sync.stop()


def test_a_payment_rejected_in_mercury_leaves_the_bill_unpaid_and_says_so(books):
    """The bill shows its payment's state at each step and keeps its pay day; rejected in Mercury, the bill is open
    and owes it all, shows the rejection, and can be scheduled again."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        bid = _scheduled_today(c)
        FA.watch_documents(c, AT)
        FA.watch_documents(c, AT)
        assert c.execute("SELECT count(*) FROM fin_payments").fetchone()[0] == 1     # asked once per schedule
        bill = FR.bill(c, OWNER, bid)
        assert (bill["status"], bill["pay_on"], bill["payment"]["status"]) == ("scheduled", AT[:10], "queued")
        listed = FR.bills(c, OWNER)["bills"][0]
        assert (listed["pay_on"], listed["payment"]["status"]) == (AT[:10], "queued")
    sync_once(books, fake, HOURS[1])                                  # recipient, then its payment, in one pass
    assert len(fake.sent("create_recipient")) == 1 and len(fake.sent("request_send_money")) == 1
    with books.read() as c:
        assert FR.bill(c, OWNER, bid)["payment"]["status"] == "awaiting_bank_approval"
    fake.requests["req-1"]["status"] = "rejected"
    sync_once(books, fake, HOURS[2])
    with books.transaction() as c:
        bill = FR.bill(c, OWNER, bid)
        assert (bill["status"], bill["remaining"], bill["pay_on"]) == ("open", 50_000, None)
        assert (bill["payment"]["status"], bill["payment"]["error"]) == ("cancelled", "rejected in Mercury")
        assert _balance(c, "2000") == -50_000
        assert c.execute("SELECT status FROM fin_holds WHERE source='payment'").fetchall() == []
        again = act(c, OWNER, "bill_schedule", {"bill_id": bid, "pay_on": "2026-10-16", "money_account_id": _checking(c)})
        assert again.status == "committed", again


def test_a_scheduled_payment_denied_withdrawn_or_expired_reopens_the_bill_and_says_so(books):
    """A payment a scheduled bill asked for that waits on approval and never runs leaves no "Scheduled" bill behind:
    the bill is open with no schedule, its row says how the ask ended, and a new schedule asks again (fresh key)."""
    from backend import hubdb as H

    def asked(c, bid):
        FA.watch_documents(c, AT)
        bill = FR.bill(c, OWNER, bid)
        assert (bill["status"], bill["pay_on"], bill["payment"]) == ("scheduled", AT[:10], None)
        waiting = FA.action(c, str(bill["schedule"]["action_id"]))
        assert waiting.status == "needs_approval", waiting
        return waiting

    def reopened(c, bid, status):
        bill = FR.bill(c, OWNER, bid)
        assert (bill["status"], bill["pay_on"], bill["remaining"]) == ("open", None, 50_000)
        assert (bill["payment"]["id"], bill["payment"]["status"], bill["payment"]["amount"]) == (None, status, 50_000)
        assert bill["payment"]["error"]
        assert FR.bills(c, OWNER)["bills"][0]["payment"]["status"] == status
        assert c.execute("SELECT count(*) FROM fin_payments").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM fin_holds WHERE status='held'").fetchone()[0] == 0

    def reschedule(c, bid):
        assert act(c, OWNER, "bill_schedule", {"bill_id": bid, "pay_on": AT[:10],
                                               "money_account_id": _checking(c)}).status == "committed"

    connect(books, FakeMercury())
    with books.transaction() as c:
        bid = _scheduled_today(c, ask=True)
        first = asked(c, bid)
        H.approval_decide(c, str(first.approver), str(first.approval_id), "declined")
        assert FA.approval_decided(c, str(first.approval_id), AT).status == "denied"
        reopened(c, bid, "denied")
        reschedule(c, bid)
        assert FR.bill(c, OWNER, bid)["payment"] is None                # a new schedule clears how the last one ended
        second = asked(c, bid)
        assert second.action_id != first.action_id
        H.approval_withdraw(c, str(second.approval_id), "Withdrawn.")
        assert [r.status for r in FA.approvals_sweep(c, AT)] == ["withdrawn"]
        reopened(c, bid, "withdrawn")
        reschedule(c, bid)
        third = asked(c, bid)
        c.execute("UPDATE approvals SET created='2026-09-01T00:00:00.000000Z' WHERE id=?", (third.approval_id,))
        H.approval_withdraw(c, str(third.approval_id), "No decision in 14 days.")
        assert [r.status for r in FA.approvals_sweep(c, AT)] == ["expired"]
        reopened(c, bid, "expired")


def test_vendor_labels_are_contact_names_and_never_ids(books):
    """F: lines a bill, an invoice write-off or any document posts carry the contact's id as their vendor key; the
    drill-down, spend-by and transactions show the contact's name, a plain vendor name as the text it was posted under, and an id with no
    contact behind it as nothing."""
    from backend.tests.test_finance_fixtures import spend
    stray = "0b6a4a52-5d0e-4c1a-9a43-2f2f3f6d1e10"
    with books.transaction() as c:
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}).result["contact_id"])
        iid = str(act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": "2026-10-01", "lines": [
            {"account_id": account(c, "4000"), "amount": 30_000}]}).result["invoice_id"])
        assert act(c, OWNER, "invoice_send", {"invoice_id": iid}).status == "committed"
        assert act(c, OWNER, "invoice_write_off", {"invoice_id": iid, "amount": 10_000}).status == "committed"
        bid = _bill(c, _vendor(c), [{"account_id": account(c, "6900"), "amount": 20_000}])
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
        spend(c, "6900", 5_000, vendor="Northwind")
        spend(c, "6900", 1_000, vendor=stray)
        detail = FR.line_detail(c, OWNER, account(c, "6900"), "2026-10-01", "2026-10-31")
        want = {"Acme Retail": 10_000, "Acme Supplies": 20_000, "Northwind": 5_000, "": 1_000}
        assert {r["label"]: r["amount"] for r in detail["by_vendor"]} == want
        assert {e["vendor_name"] for e in detail["entries"]} == set(want)
        by = FR.spend_by(c, OWNER, "vendor", "2026-10-01", "2026-10-31")
        assert {r["label"] for r in by["rows"]} >= {"Acme Retail", "Acme Supplies", "Northwind", ""}
        rows = FR.transactions(c, OWNER, start="2026-10-01", end="2026-10-31")["rows"]
        assert not any(customer in str(r.get("vendor_name")) or stray in str(r.get("vendor_name")) for r in rows)
        assert "Acme Retail" in {r.get("vendor_name") for r in rows}


def test_a_feed_row_pays_bills_in_parts_and_together_and_never_posts_expense_twice(books):
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        vendor = _vendor(c)
        one = _bill(c, vendor, [{"account_id": account(c, "6100"), "amount": 30_000}], number="B-1")
        two = _bill(c, vendor, [{"account_id": account(c, "6000"), "amount": 20_000}], number="B-2")
        three = _bill(c, vendor, [{"account_id": account(c, "6100"), "amount": 12_345}], number="B-3")
        for b in (one, two, three):
            assert act(c, OWNER, "bill_approve", {"bill_id": b}).status == "committed"
        # The one open bill owing exactly 123.45 from the named vendor: matched, settled, posted to AP only.
        fid = _ingest(c, _txn("txn-b3", -123.45))
        assert FR.bill(c, OWNER, three)["status"] == "paid"
        # One row paying two bills (combined), the second only in part; then the row has nothing left.
        both = _ingest(c, _txn("txn-both", -400.0, who="Acme Supplies Ltd"))
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": one, "feed_id": both}).status == "committed"
        part = act(c, OWNER, "bill_mark_paid", {"bill_id": two, "feed_id": both, "amount": 10_000})
        assert part.status == "committed" and part.result["remaining"] == 10_000, part
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": two, "feed_id": both}).code == "invalid"
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": two, "feed_id": fid}).code == "invalid"   # used up
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": two, "account_id": account(c, "1000"),
                                                "amount": 10_001}).code == "invalid"                # overpaid
        assert (_balance(c, "6100"), _balance(c, "6000"), _balance(c, "6900")) == (42_345, 20_000, 0)
        assert _balance(c, "2000") == -10_000                                    # bill two's unpaid half
        assert _pnl(c, "cash") == {"6100": 42_345, "6000": 10_000}
        # The bank cancels the combined row: what it paid is owed again, in the books and the cash view.
        _ingest(c, {**_txn("txn-both", -400.0, who="Acme Supplies Ltd"), "status": "cancelled"}, LATER)
        assert FR.bill(c, OWNER, one)["status"] == "open" and FR.bill(c, OWNER, two)["remaining"] == 20_000
        assert _pnl(c, "cash", end="2026-10-31") == {"6100": 12_345}
        assert _balance(c, "2000") == -50_000


def test_cash_and_accrual_agree_exactly_once_documents_are_paid_and_partials_recognize_what_was_paid(books):
    rng = random.Random(7)
    with books.transaction() as c:
        vendor = _vendor(c)
        lines = [account(c, code) for code in ("6000", "6100", "5000")]
        paid_total = 0
        for n in range(12):
            parts = [{"account_id": rng.choice(lines), "amount": rng.randint(1, 99_999)} for _ in range(rng.randint(1, 4))]
            bid = _bill(c, vendor, parts, day=f"2026-10-{rng.randint(1, 14):02d}")
            assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
            total = sum(int(p["amount"]) for p in parts)
            left = total if n % 3 else rng.randint(1, total)                 # every third paid only in part
            while left:
                amount = min(left, rng.randint(1, total))
                done = act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "account_id": account(c, "1000"),
                                                        "amount": amount, "date": f"2026-11-{rng.randint(1, 28):02d}"})
                assert done.status == "committed", done
                left -= amount
                paid_total += amount
            if n % 3 == 0:
                cash, accrual = _pnl(c, "cash"), _pnl(c, "accrual")
                assert sum(cash.values()) == paid_total
                assert sum(accrual.values()) - sum(cash.values()) == _balance(c, "2000") * -1
        unpaid = [str(r[0]) for r in c.execute("SELECT id FROM fin_bills WHERE status='open'")]
        for bid in unpaid:
            assert act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "account_id": account(c, "1000")}).status == \
                "committed"
        assert _pnl(c, "cash") == _pnl(c, "accrual") and _balance(c, "2000") == 0
        # A hand reversal of one payment entry puts that bill back to owing, in AP and the cash view alike.
        entry = c.execute("SELECT entry_id FROM fin_settlements WHERE voided_at IS NULL LIMIT 1").fetchone()[0]
        assert act(c, OWNER, "journal", {"reverses": entry, "date": "2026-11-30"}).status == "committed"
        assert sum(_pnl(c, "accrual").values()) - sum(_pnl(c, "cash").values()) == -_balance(c, "2000") > 0
        bill_entry = c.execute("SELECT entry_id FROM fin_bills LIMIT 1").fetchone()[0]
        assert act(c, OWNER, "journal", {"reverses": bill_entry}).code == "conflict"     # void the bill instead


def test_an_invoice_paid_in_part_and_written_off_leaves_ar_at_zero_and_cash_income_at_what_came_in(books):
    with books.transaction() as c:
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}).result["contact_id"])
        made = act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": "2026-10-01",
                                                "terms_days": 10, "lines": [{"account_id": account(c, "4000"),
                                                                             "amount": 100_000}]})
        iid = str(made.result["invoice_id"])
        assert made.result["number"] == "INV-0001"
        sent = act(c, OWNER, "invoice_send", {"invoice_id": iid})
        assert sent.status == "committed" and sent.result["document"]["total"] == 100_000
        got = act(c, OWNER, "invoice_record_payment", {"invoice_id": iid, "amount": 60_000,
                                                       "account_id": account(c, "1000"), "date": "2026-10-12"})
        assert got.result["status"] == "partial" and got.result["remaining"] == 40_000
        off = act(c, OWNER, "invoice_write_off", {"invoice_id": iid})
        assert off.result["status"] == "paid" and off.result["written_off"] == 40_000
        assert act(c, OWNER, "invoice_void", {"invoice_id": iid}).code == "conflict"
        assert _balance(c, "1200") == 0
        assert _pnl(c, "accrual") == {"4000": 100_000, "6900": 40_000}
        assert _pnl(c, "cash") == {"4000": 60_000}
        # Overdue reminders: once per stage, however often the job looks.
        for at in ("2026-10-12T12:00:00.000000Z", "2026-10-13T12:00:00.000000Z", "2026-10-19T12:00:00.000000Z"):
            other = act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": "2026-09-01",
                                                     "due_date": "2026-10-11", "lines": [
                                                         {"account_id": account(c, "4000"), "amount": 5_000}]},
                        key=f"inv-late") if at.startswith("2026-10-12") else None
            if other is not None:
                assert act(c, OWNER, "invoice_send", {"invoice_id": other.result["invoice_id"]}).status == "committed"
            FA.watch_documents(c, at)
            FA.watch_documents(c, at)
        assert [k for _, k in _events(c, "finance.invoice.overdue")] == ["overdue_1", "overdue_7"]


def test_posting_rules_need_passing_examples_run_by_priority_and_ask_raises_needs_category(books):
    fake = FakeMercury()
    connect(books, fake)
    when = {"counterparty": {"matches": "OPENROUTER|ANTHROPIC"}}
    rule = {"kind": "posting", "name": "AI", "when": when,
            "outcome": {"outcome": "post", "account_id": "", "tags": {"team": "eng"}},
            "examples": [{"input": {"counterparty": "OPENROUTER INC"}, "expect": "post"},
                         {"input": {"counterparty": "Acme Cafe"}, "expect": "none"}]}
    with books.transaction() as c:
        rule["outcome"]["account_id"] = account(c, "6100")
        bad = {**rule, "examples": [{"input": {"counterparty": "Acme Cafe"}, "expect": "post"}]}
        assert act(c, OWNER, "rule_create", bad).code == "invalid"
        assert act(c, OWNER, "rule_create", {**rule, "outcome": {"outcome": "post", "account_id": account(c, "2000")}}
                   ).code == "not_found"                                  # never straight to payables
        assert act(c, OWNER, "rule_create", {**rule, "priority": 50}).status == "committed"
        split = {**rule, "name": "AI split", "priority": 10, "outcome": {"outcome": "post", "split": [
            {"account_id": account(c, "6100"), "share": 7_500}, {"account_id": account(c, "6000"), "share": 2_500}]}}
        assert act(c, OWNER, "rule_create", split).status == "committed"
        ask = {"kind": "posting", "name": "Cafe asks", "when": {"counterparty": "Acme Cafe"}, "outcome": {"outcome": "ask"},
               "examples": [{"input": {"counterparty": "Acme Cafe"}, "expect": "ask"}]}
        assert act(c, OWNER, "rule_create", ask).status == "committed"
        tested = act(c, OWNER, "rule_test", {"kind": "posting", "when": when, "outcome": rule["outcome"]})
        assert tested.status == "committed"
        _ingest(c, _txn("txn-ai", -100.01, who="ANTHROPIC PBC", kind="debitCardTransaction"))
        _ingest(c, _txn("txn-cafe", -12.0, who="Acme Cafe", kind="debitCardTransaction"))
        assert (_balance(c, "6100"), _balance(c, "6000")) == (7_500, 2_501)          # the split, rounding to the last
        assert _balance(c, "6900") == 1_200
        cafe = c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-cafe'").fetchone()[0]
        assert (cafe, "") in _events(c, "finance.feed.needs_category")
        ai = c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-ai'").fetchone()[0]
        assert (ai, "") not in _events(c, "finance.feed.needs_category")
        # A bot's rule waits for a finance admin, even with finance.rules.
        assert act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.rules"}).status == "committed"
        web = {**rule, "name": "Bot AI", "outcome": {**rule["outcome"], "tags": {"team": "web"}}}
        act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "expenses", "whose": "team", "whose_team": "web"})
        assert act(c, "bot:coder", "rule_create", web).status == "needs_approval"


def test_a_bill_line_naming_an_item_keeps_the_item_when_a_posting_rule_supplies_its_account(books):
    with books.transaction() as c:
        vendor = str(act(c, OWNER, "contact_create", {"name": "ACME PARTS", "kind": "vendor"}).result["contact_id"])
        item = str(act(c, OWNER, "item_create", {"name": "Field visit", "kind": "service",
                                                  "income_account_id": account(c, "4000")}).result["item_id"])
        rule = {"kind": "posting", "name": "Parts", "when": {"counterparty": {"matches": "ACME PARTS"}},
                "outcome": {"outcome": "post", "account_id": account(c, "6100")},
                "examples": [{"input": {"counterparty": "ACME PARTS"}, "expect": "post"},
                             {"input": {"counterparty": "Other"}, "expect": "none"}]}
        assert act(c, OWNER, "rule_create", rule).status == "committed"
        made = act(c, OWNER, "bill_create", {"vendor_id": vendor, "number": "B-9", "bill_date": "2026-10-05",
                                              "due_date": "2026-11-05",
                                              "lines": [{"item_id": item, "amount": 5000, "description": "visit"}]})
        assert made.status == "committed", made
        lines = c.execute("SELECT account_id, item_id, amount FROM fin_bill_lines WHERE bill_id=?",
                          (made.result["bill_id"],)).fetchall()
        assert [(str(r[0]), str(r[1]), int(r[2])) for r in lines] == [(account(c, "6100"), item, 5000)]


def test_defaults_are_seeded_once_stay_as_edited_and_guard_bots(books):
    with books.transaction() as c:
        ids = [str(r[0]) for r in c.execute("SELECT id FROM fin_rules WHERE id LIKE 'rule:bot-%' ORDER BY id")]
        assert ids == ["rule:bot-books-over-5000", "rule:bot-card-single-use-over-200", "rule:bot-card-standing",
                       "rule:bot-new-account"]
        assert act(c, OWNER, "rule_update", {"rule_id": "rule:bot-books-over-5000", "when": {
            "kind": ["categorize", "journal"], "actor.kind": "bot", "amount": {"gt": 1_000_000}}, "examples": [
            {"input": {"kind": "journal", "actor.kind": "bot", "amount": 1_000_001}, "expect": "needs_approval"}]}
                   ).status == "committed"
        FA.setup(c, OWNER, LATER)
        assert c.execute("SELECT count(*) FROM fin_rules WHERE id LIKE 'rule:bot-%'").fetchone()[0] == 4
        assert "1000000" in c.execute("SELECT when_json FROM fin_rules WHERE id='rule:bot-books-over-5000'"
                                      ).fetchone()[0]
        assert FA.grant_defaults(c, "bot:coder", at=AT) == ["finance.book", "finance.ap", "finance.ar", "finance.pay",
                                                            "finance.cards"]
        assert FA.grant_defaults(c, "bot:coder", at=AT) == []
        entry = {"date": "2026-10-10", "lines": [{"account_id": account(c, "6100"), "amount": 900_000,
                                                  "tags": {"team": "web"}},
                                                 {"account_id": account(c, "1000"), "amount": -900_000}]}
        act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "expenses", "whose": "team", "whose_team": "web"})
        act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "bs_lines", "account_ids": [account(c, "1000")]})
        assert act(c, "bot:coder", "journal", entry).status == "committed"                # under the edited limit
        big = {**entry, "lines": [{**entry["lines"][0], "amount": 1_000_001}, {**entry["lines"][1], "amount": -1_000_001}]}
        assert act(c, "bot:coder", "journal", big).status == "needs_approval"
        assert act(c, "bot:coder", "account_create", {"code": "6150", "name": "Tools", "type": "expense"}
                   ).status == "needs_approval"


def test_rights_and_views_cut_every_new_kind_and_read(books):
    with books.transaction() as c:
        assert act(c, PLAIN, "contact_create", {"name": "Acme Parts", "kind": "vendor"}).code == "denied"
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.ar"}).status == "committed"
        assert act(c, "human:dan", "contact_create", {"name": "Acme Parts", "kind": "vendor"}).code == "denied"
        customer = act(c, "human:dan", "contact_create", {"name": "Acme Shop", "kind": "customer", "tax_id": "12-3456789"})
        assert customer.status == "committed", customer
        vendor = _vendor(c, tax_id="98-7654321")
        assert act(c, PLAIN, "bill_create", {"vendor_id": vendor, "bill_date": "2026-10-10", "lines": [
            {"account_id": account(c, "6100"), "amount": 100}]}).code == "denied"
        for n in range(3):
            _bill(c, vendor, [{"account_id": account(c, "6100"), "amount": 1_000 + n}], number=f"A-{n}")
        assert "98-7654321" not in str(c.execute("SELECT * FROM fin_actions").fetchall())   # sealed, never stored
        FA.grant_defaults(c, "bot:seller", at=AT)
    with books.read() as c:                     # a right is an action only: seeing is the view (§7.4, §7.5)
        for read in (FR.bills, FR.contacts):
            with pytest.raises(FA.FinanceError):
                read(c, "bot:seller")
        with pytest.raises(FA.FinanceError):
            FR.invoices(c, "human:dan")
        assert FR.search(c, "bot:seller", kind="bill", q="A-")["results"] == []
    with books.transaction() as c:
        for who, what in (("bot:seller", "payables"), ("human:dan", "receivables")):
            assert act(c, OWNER, "view_grant", {"subject": who, "what": what}).status == "committed"
    with books.read() as c:
        assert FR.contacts(c, "human:dan")["contacts"][0]["tax_id_last4"] is None       # last 4 for admins only
        assert {x["tax_id_last4"] for x in FR.contacts(c, OWNER)["contacts"]} == {"6789", "4321"}
        page = FR.bills(c, OWNER, limit=2)
        assert len(page["bills"]) == 2 and page["next_cursor"]
        assert len(FR.bills(c, OWNER, cursor=page["next_cursor"])["bills"]) == 1
        for who in ("human:ben", "bot:coder", "human:dan"):                              # manager, plain bot, AR only
            with pytest.raises(FA.FinanceError):
                FR.bills(c, who)
        assert len(FR.bills(c, "bot:seller")["bills"]) == 3                              # a bot with the grants
        assert FR.invoices(c, "human:dan")["invoices"] == []
        assert FR.pay_from(c, "bot:seller")["accounts"] == []                            # nothing connected
        assert FR.search(c, "human:ben", kind="bill", q="A-")["results"] == []
        assert len(FR.search(c, "bot:seller", kind="bill", q="A-")["results"]) == 3


def test_due_soon_cash_below_and_period_ready_fire_once_per_stage_crossing_and_month(books, monkeypatch):
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        bid = _bill(c, _vendor(c), [{"account_id": account(c, "6100"), "amount": 5_000}], due_date="2026-10-20")
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
        assert act(c, OWNER, "payment_settings", {"cash_below": 999_999_999}).status == "committed"
        assert act(c, "human:ben", "payment_settings", {"cash_below": 1}).code == "denied"
        monkeypatch.setattr(FR, "ready_months", lambda c, at=None: ["2026-09"])
        for at in (AT, AT, "2026-10-19T12:00:00.000000Z", "2026-10-19T13:00:00.000000Z"):
            FA.watch_documents(c, at)
        assert [k for _, k in _events(c, "finance.bill.due_soon")] == ["due_in_7", "due_in_1"]
        assert _events(c, "finance.cash.below") == [("company", "n1")]
        assert _events(c, "finance.period.ready") == [("2026-09", "")]
        checklist = FR.close_checklist(c, OWNER, "2026-09")
        assert not checklist["ready"] and {x["key"] for x in checklist["checks"]} >= {"reconciled", "draft_bills"}
        FA.drain_events(c, LATER)
        assert c.execute("SELECT count(*) FROM fin_events WHERE drained_at IS NULL").fetchone()[0] == 0


def test_marked_paid_from_checking_then_its_bank_row_arrives_posts_cash_once_and_clears_ap_once(books):
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        vendor = _vendor(c)
        bank = _balance(c, "1000")
        # A row the bank showed before anyone marked the bill paid: Other expenses, until the mark-paid links it.
        early = _ingest(c, _txn("txn-m0", -99.0, day="2026-10-12"))
        one, two, early_bill, outside = (
            _bill(c, vendor, [{"account_id": account(c, "6100"), "amount": n}], number=f"M-{n}")
            for n in (25_000, 7_000, 9_900, 1_000))
        for b in (one, two, early_bill, outside):
            assert act(c, OWNER, "bill_approve", {"bill_id": b}).status == "committed"
        paid = act(c, OWNER, "bill_mark_paid", {"bill_id": one, "money_account_id": _checking(c), "date": "2026-10-13"})
        assert paid.status == "committed", paid
        assert (paid.result["from"], paid.result["status"]) == ("expected", "paid")
        named = act(c, OWNER, "bill_mark_paid", {"bill_id": two, "account_id": account(c, "1000"), "date": "2026-10-13"})
        assert named.result["from"] == "expected"                      # the account's own line names the account
        assert _balance(c, "1000") - bank == -32_000 - 9_900
        rows = [_ingest(c, _txn("txn-m1", -250.0, day="2026-10-14")), _ingest(c, _txn("txn-m2", -70.0, day="2026-10-15"))]
        linked = [c.execute("SELECT entry_id, status FROM fin_feed_txns WHERE id=?", (r,)).fetchone() for r in rows]
        assert [tuple(x) for x in linked] == [(paid.result["entry_id"], "posted"), (named.result["entry_id"], "posted")]
        late = act(c, OWNER, "bill_mark_paid", {"bill_id": early_bill, "money_account_id": _checking(c)})
        assert (late.result["from"], late.result["feed_id"]) == ("feed", early)
        assert _balance(c, "1000") - bank == -32_000 - 9_900                # every bank row posted once
        assert (_balance(c, "2000"), _balance(c, "6900"), _balance(c, "6100")) == (-1_000, 0, 42_900)
        assert not {r for r, _ in _events(c, "finance.feed.needs_category")} & set(rows)
        # Paid outside every connected account: a clearing line the feed never touches.
        away = act(c, OWNER, "bill_mark_paid", {"bill_id": outside})
        assert away.result["from"] == "account" and _balance(c, "1050") == -1_000 and _balance(c, "2000") == 0
        # The bank cancels a linked row: the bill owes it again and its bank line is given back.
        _ingest(c, {**_txn("txn-m1", -250.0, day="2026-10-14"), "status": "cancelled"}, LATER)
        assert FR.bill(c, OWNER, one)["status"] == "open"
        assert (_balance(c, "2000"), _balance(c, "1000") - bank) == (-25_000, -7_000 - 9_900)


def test_a_posting_rule_categorizes_a_tico_card_charge_and_keeps_the_holder_card_use_and_liability(books):
    from backend.tests.test_finance_banking import _arrives, _card_for_eve, _card_use, _card_holds
    fake = FakeMercury()
    fake.balances["credit-io"] = 0
    connect(books, fake)
    _, card_id = _card_for_eve(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        rule = {"kind": "posting", "name": "Hosting", "when": {"counterparty": {"matches": "Cloud"}},
                "outcome": {"outcome": "post", "account_id": account(c, "5100"),
                            "tags": {"teammate": "human:gus", "project": "site"}},
                "examples": [{"input": {"counterparty": "Acme Cloud"}, "expect": "post"}]}
        assert act(c, OWNER, "rule_create", rule).status == "committed"
    _arrives(fake, MERCURY["txn_card_pending"], 1, "evt-0001")
    sync_once(books, fake, HOURS[1])
    _arrives(fake, MERCURY["txn_card_posted"], 2, "evt-0002")
    sync_once(books, fake, HOURS[2])
    with books.read() as c:
        entry = c.execute("SELECT entry_id FROM fin_feed_txns WHERE card_id=?", (card_id,)).fetchone()[0]
        codes = {str(r[0]): str(r[1]) for r in c.execute("SELECT id, code FROM fin_accounts")}
        lines = [(codes[x.account_id], dict(x.tags), x.amount) for _, x in SL.entry_lines(c, entry)]
        assert ("5100", {"teammate": "human:eve", "team": "web", "project": "site"}, 4_500) in lines
        assert [a for code, _, a in lines if code == "2100"] == [-4_500]        # still on the card liability
        assert _card_use(c, card_id, "2026-10") == 4_500 and _card_holds(c) == [(4_250, "settled")]
        assert not _events(c, "finance.feed.needs_category")


def test_a_vendor_has_one_key_across_its_bills_and_card_rows(books):
    """Spec §3, §4.4: a card row whose merchant is a vendor contact's name (any case) posts under the contact's id, so
    one vendor budget counts the vendor's bill line and card row once each, and spend-by vendor shows one row. The
    budget was made on the plain name before the contact existed; it matches once the contact does."""
    from backend.finance import budgeting
    from backend.tests.test_finance_banking import _arrives, _card_for_eve
    fake = FakeMercury()
    fake.balances["credit-io"] = 0
    connect(books, fake)
    cloud, _ = _card_for_eve(books, fake)                              # a Cloud budget on 6100, eve's card on it
    sync_once(books, fake)
    with books.transaction() as c:
        line = account(c, "6100")
        by_name = act(c, OWNER, "budget_create", {"name": "Acme Cloud", "account_id": line, "vendor": "acme cloud",
                                                  "parent_id": cloud, "amount": 50_000})
        assert by_name.status == "committed", by_name
        vendor = _vendor(c, "Acme Cloud")
        rule = {"kind": "posting", "name": "Hosting", "when": {"counterparty": {"matches": "CLOUD"}},
                "outcome": {"outcome": "post", "account_id": line},
                "examples": [{"input": {"counterparty": "ACME CLOUD"}, "expect": "post"}]}
        assert act(c, OWNER, "rule_create", rule).status == "committed"
        bid = _bill(c, vendor, [{"account_id": line, "amount": 20_000}])
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
    shouted = {"counterpartyNickname": "ACME CLOUD"}                    # the contact's name in another case
    _arrives(fake, {**MERCURY["txn_card_pending"], **shouted}, 1, "evt-0001")
    sync_once(books, fake, HOURS[1])
    _arrives(fake, {**MERCURY["txn_card_posted"], **shouted}, 2, "evt-0002")
    sync_once(books, fake, HOURS[2])
    with books.read() as c:
        assert c.execute("SELECT counterparty FROM fin_feed_txns WHERE card_id IS NOT NULL").fetchone()[0] == \
            "ACME CLOUD"
        keys = [str(r[0]) for r in c.execute("SELECT l.contact_id FROM fin_lines l JOIN fin_accounts a ON "
                                             "a.id=l.account_id WHERE a.code='6100' ORDER BY l.amount")]
        assert keys == [vendor, vendor]                                # the card row and the bill line, one key
        assert budgeting.budget_state(c, str(by_name.result["budget_id"]), AT).actual == 24_500
        rows = FR.spend_by(c, OWNER, "vendor", "2026-10-01", "2026-10-31", account_id=line)["rows"]
        assert [(r["key"], r["label"], r["amount"]) for r in rows] == [(vendor, "Acme Cloud", 24_500)]


def test_vendor_keys_fold_names_aliases_keep_history_and_inactive_contacts_capture_nothing_new(books):
    """Spec §3, §4.4 through fin_vendor_aliases: a plain name is keyed folded (ÉCOLE is école); a budget on a name keeps
    its actual as a contact takes the name and is renamed (the old name stays an alias); a budget on a contact's name
    stores its id; siblings on one vendor are refused, and so is a rename that would make two count the same spend;
    an inactive contact's name captures no new bank row; a merchant with no contact reads as its newest row's text."""
    from backend.finance import budgeting
    from backend.tests.test_finance_fixtures import spend
    connect(books, FakeMercury())

    def stored(c, bid):
        return c.execute("SELECT vendor FROM fin_budgets WHERE id=?", (bid,)).fetchone()[0]

    def actual(c, bid):
        return budgeting.budget_state(c, bid, AT).actual

    with books.transaction() as c:
        line = account(c, "6100")
        root = str(act(c, OWNER, "budget_create", {"name": "Cloud", "account_id": line, "amount": 100_000}
                       ).result["budget_id"])

        def budget(name, vendor):
            return act(c, OWNER, "budget_create", {"name": name, "account_id": line, "vendor": vendor,
                                                   "parent_id": root, "amount": 10_000})

        ecole = str(budget("Ecole", "école").result["budget_id"])
        spend(c, "6100", 3_000, vendor="ÉCOLE ")
        assert (stored(c, ecole), actual(c, ecole)) == ("école", 3_000)
        acme = budget("Acme", "acme cloud")
        assert acme.status == "committed", acme
        twin = budget("Twin", "Acme Cloud")
        assert (twin.status, twin.reason) == ("failed", "it overlaps budget Acme")
        bid = str(acme.result["budget_id"])
        spend(c, "6100", 10_000, vendor="ACME CLOUD")
        assert actual(c, bid) == 10_000
        vendor = _vendor(c, "Acme Cloud")
        assert actual(c, bid) == 10_000                                  # the contact takes the name, and its history
        assert act(c, OWNER, "contact_update", {"contact_id": vendor, "name": "Acme Hosting"}).status == "committed"
        assert actual(c, bid) == 10_000                                  # renamed: the old name still maps
        assert act(c, OWNER, "contact_create", {"name": "acme cloud", "kind": "vendor"}).code == "conflict"
        north = _vendor(c, "Northwind")
        on_north = budget("North", "NORTHWIND")
        assert on_north.status == "committed" and stored(c, on_north.result["budget_id"]) == north
        clash = act(c, OWNER, "contact_update", {"contact_id": north, "name": "École"})
        assert (clash.status, clash.code) == ("failed", "conflict") and "Ecole and North" in clash.reason
        gone = _vendor(c, "Globex")
        assert act(c, OWNER, "contact_update", {"contact_id": gone, "active": False}).status == "committed"
        for who in ("GLOBEX", "northwind", "github"):
            rule = {"kind": "posting", "name": who, "when": {"counterparty": {"matches": who}},
                    "outcome": {"outcome": "post", "account_id": line},
                    "examples": [{"input": {"counterparty": who}, "expect": "post"}]}
            assert act(c, OWNER, "rule_create", rule).status == "committed"
        _ingest(c, _txn("txn-globex", -20.0, who="GLOBEX", kind="debitCardTransaction"))
        _ingest(c, _txn("txn-north", -30.0, who="northwind ", kind="debitCardTransaction"))
        _ingest(c, _txn("txn-gh-new", -4.0, who="GitHub", kind="debitCardTransaction"))
        _ingest(c, _txn("txn-gh-old", -4.0, who="GITHUB", day="2026-10-13", kind="debitCardTransaction"))
        keys = {str(r[0]) for r in c.execute("SELECT l.contact_id FROM fin_lines l JOIN fin_entries e ON "
                                             "e.id=l.entry_id WHERE e.source IN ('bank', 'card') AND l.contact_id<>''")}
        assert keys == {"globex", north, "github"}                       # inactive: its name only; active: its id
        rows = FR.spend_by(c, OWNER, "vendor", "2026-10-01", "2026-10-31", account_id=line)["rows"]
        labels = {r["label"]: r["amount"] for r in rows}
        assert labels["GitHub"] == 800 and labels["Globex"] == 2_000    # the newest row's text; the alias's contact


def test_phase_one_vendor_names_still_count_after_migration_66_and_keys_are_whole_and_never_contact_ids(books):
    """Codex review: phase 1 posted vendor names as written; migration 66 records them (fin_vendor_legacy), so a
    budget on the name, then the contact that takes it, still count those lines, and the name labels its key. A key is
    folded whole (two long names never meet by truncation) and folding it again changes nothing. A contact's id is
    never another vendor's name, and a stored contact id never goes by an alias."""
    from backend.finance import budgeting
    from backend.finance.core.ledger import name_key
    from backend.finance.store import budgets as SB
    from backend.finance.store import schema
    from backend.tests.test_finance_fixtures import spend
    assert name_key("A" * 79 + "ß") != name_key("A" * 79 + "s")
    assert all(name_key(name_key(x)) == name_key(x) for x in (" ÉCOLE ", "A" * 79 + "ß", "ﬃ STRASSE "))
    with books.transaction() as c:
        line, misc = account(c, "6100"), account(c, "6900")

        def budget(name, on, vendor=""):
            root = act(c, OWNER, "budget_create", {"name": f"{name} line", "account_id": on, "amount": 90_000})
            return str(act(c, OWNER, "budget_create", {"name": name, "account_id": on, "vendor": vendor, "amount": 5_000,
                                                       "parent_id": root.result["budget_id"]}).result["budget_id"])

        bid = budget("Acme", line, "acme cloud")
        spend(c, "6100", 10_000, vendor="ACME CLOUD")
        # As phase 1 left it: the line, its rollups and the budget under the name as written, no label.
        c.execute("DROP TRIGGER fin_lines_no_update")
        c.execute("UPDATE fin_lines SET contact_id='ACME CLOUD' WHERE contact_id='acme cloud'")
        for table in ("fin_rollups", "fin_cash_rollups"):
            c.execute(f"UPDATE {table} SET vendor='ACME CLOUD' WHERE vendor='acme cloud'")
        c.execute("UPDATE fin_budgets SET vendor='ACME CLOUD' WHERE id=?", (bid,))
        c.execute("DELETE FROM fin_vendor_labels")
        schema.create(c)
        assert budgeting.budget_state(c, bid, AT).actual == 0                 # what the upgrade would lose
        schema.phase2(c)
        assert c.execute("SELECT vendor FROM fin_budgets WHERE id=?", (bid,)).fetchone()[0] == "acme cloud"
        assert budgeting.budget_state(c, bid, AT).actual == 10_000
        rows = FR.spend_by(c, OWNER, "vendor", "2026-10-01", "2026-10-31", account_id=line)["rows"]
        assert [(r["label"], r["amount"]) for r in rows] == [("ACME CLOUD", 10_000)]
        vendor = _vendor(c, "Acme Cloud")
        assert budgeting.budget_state(c, bid, AT).actual == 10_000             # through the contact's alias too
        held = budget("Acme Co", misc, vendor)
        took = act(c, OWNER, "contact_create", {"name": vendor.upper(), "kind": "vendor"})
        assert (took.status, took.code) == ("failed", "invalid")
        book = str(c.execute("SELECT id FROM fin_books").fetchone()[0])
        other = _vendor(c, "Globex")
        c.execute("INSERT INTO fin_vendor_aliases VALUES(?,?,?,?)", (book, vendor, other, AT))   # as if it got in
        assert SB.budgets(c, book)[held].vendor == vendor


def test_a_long_vendor_name_whose_key_folds_longer_still_reclassifies_and_settles(books):
    """Codex fix check: 79 A's and ß fold to an 81-character key (names are capped at 80 before folding, keys never
    after). A reconciled debit under that name, found as a lost payment, moves to the payment's line and settles:
    lines take back the stored folded key."""
    from backend.finance.core.ledger import name_key
    from backend.tests.test_finance_payments import _debit, _lost_send, _reconcile
    from backend.finance.core import ledger as L
    from backend.finance.core.errors import FinanceError
    long = "A" * 79 + "ß"
    assert len(name_key(long)) == 81
    assert L.line({"account_id": "a", "amount": 1, "contact_id": name_key(long)}, "USD").contact_id == name_key(long)
    with pytest.raises(FinanceError, match="longer than 80"):
        L.line({"account_id": "a", "amount": 1, "contact_id": "A" * 81}, "USD")    # a name as typed stays capped
    fake = FakeMercury()
    connect(books, fake)
    pid, key = _lost_send(books, fake)
    _debit(fake, "txn-lost", "req-9", "evt-lost", counterpartyName=long)
    sync_once(books, fake, HOURS[2])
    with books.transaction() as c:
        fid = _reconcile(c, "txn-lost")
    fake.land({"recipientId": "rcp-1", "amount": 500.0, "externalMemo": f"tico:{key} Acme"}, "req-9", "approved")
    sync_once(books, fake, "2026-11-02T13:30:00.000000Z")
    with books.read() as c:
        assert tuple(c.execute("SELECT status, provider_txn_id FROM fin_payments WHERE id=?", (pid,)).fetchone()) \
            == ("settled", fid)
        assert c.execute("SELECT count(*) FROM fin_entries WHERE source='bank' AND source_id=?",
                         (f"{fid}:reclass",)).fetchone()[0] == 1
        keys = {str(r[0]) for r in c.execute("SELECT contact_id FROM fin_lines WHERE contact_id<>''")}
        assert name_key(long) in keys and "acme supplies" in keys
        assert c.execute("SELECT label FROM fin_vendor_labels WHERE name_key=?", (name_key(long),)).fetchone()[0] \
            == long


def test_a_bill_paid_by_a_bank_row_already_on_other_expenses_counts_once_on_its_own_line_in_both_bases(books):
    """E2E re-test: a 30.00 Software bill, approved, marked paid from checking after its bank row posted to Other
    expenses. The row's "Pays bill" entry moves the 30.00 off Other expenses into AP, so the P&L counts the bill once,
    on Software, accrual or cash; Other expenses nets to nothing."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        vendor = _vendor(c, "Acme Cloud")
        fid = _ingest(c, _txn("txn-ac105", -30.0, who="ACME CLOUD", day="2026-10-14"))
        assert _balance(c, "6900") == 3_000                                  # the automatic default, until linked
        bid = _bill(c, vendor, [{"account_id": account(c, "6100"), "amount": 3_000}], number="AC-105")
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
        paid = act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "money_account_id": _checking(c)})
        assert (paid.result["from"], paid.result["feed_id"], paid.result["status"]) == ("feed", fid, "paid"), paid
        for basis in ("accrual", "cash"):
            got = _pnl(c, basis)
            assert (got.get("6100"), got.get("6900")) == (3_000, None), (basis, got)
        assert (_balance(c, "2000"), _balance(c, "6900"), _balance(c, "1000")) == (0, 0, -3_000)
        rows = FR.transactions(c, OWNER, start="2026-10-01", end="2026-10-31", source="entry")["rows"]
        moved = [(r["account"], r["amount"]) for r in rows if r["memo"] == "Pays bill AC-105"]
        assert moved == [("Bill AC-105", -3_000)]                          # labelled with the bill it pays


def _no_number(rows, number):
    return [r for r in rows if number in f"{r.get('account')} {r.get('description')} {r.get('memo')}"]


def test_a_pays_bill_line_never_names_the_bill_to_a_teammate_without_payables(books):
    """Grok fix check: gus sees the P&L but not payables. The row's "Pays bill" line still shows, on its own line,
    with no bill number in its line, description or memo; in Transactions and in the line's detail."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        _ingest(c, _txn("txn-ac105", -30.0, who="ACME CLOUD", day="2026-10-14"))
        bid = _bill(c, _vendor(c, "Acme Cloud"), [{"account_id": account(c, "6100"), "amount": 3_000}],
                    number="AC-105")
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "money_account_id": _checking(c)}).status == \
            "committed"
        assert act(c, OWNER, "view_grant", {"subject": "human:gus", "what": "pnl"}).status == "committed"
    with books.read() as c:
        rows = FR.transactions(c, "human:gus", start="2026-10-01", end="2026-10-31", source="entry")["rows"]
        moved = [(r["account"], r["amount"]) for r in rows if r["entry_id"] and r["amount"] == -3_000]
        assert moved == [("Other expenses", -3_000)], rows
        assert _no_number(rows, "AC-105") == [], _no_number(rows, "AC-105")
        detail = FR.line_detail(c, "human:gus", account(c, "6900"), "2026-10-01", "2026-10-31", at=AT)
        assert len(detail["entries"]) == 2 and _no_number(detail["entries"], "AC-105") == []
        mine = FR.line_detail(c, OWNER, account(c, "6900"), "2026-10-01", "2026-10-31", at=AT)
        assert "Pays bill AC-105" in {e["memo"] for e in mine["entries"]}


def test_a_reversed_pays_bill_entry_still_never_names_the_bill_and_search_cannot_find_it(books):
    """Fix check: the "Pays bill AC-105" entry is reversed, which voids its settlement. To gus (P&L, no payables) the
    entry and its reversal still show no bill number, and searching for it finds nothing; the owner still finds it."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        _ingest(c, _txn("txn-ac105", -30.0, who="ACME CLOUD", day="2026-10-14"))
        bid = _bill(c, _vendor(c, "Acme Cloud"), [{"account_id": account(c, "6100"), "amount": 3_000}],
                    number="AC-105")
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "money_account_id": _checking(c)}).status == \
            "committed"
        pays = str(c.execute("SELECT id FROM fin_entries WHERE memo='Pays bill AC-105'").fetchone()[0])
        back = act(c, OWNER, "journal", {"reverses": pays})
        assert back.status == "committed", back
        assert c.execute("SELECT count(*) FROM fin_settlements WHERE entry_id=? AND voided_at IS NULL",
                         (pays,)).fetchone()[0] == 0
        assert act(c, OWNER, "view_grant", {"subject": "human:gus", "what": "pnl"}).status == "committed"
    with books.read() as c:
        rows = FR.transactions(c, "human:gus", start="2026-10-01", end="2026-10-31", source="entry")["rows"]
        assert sorted(r["amount"] for r in rows if r["account_id"] == account(c, "6900")) == [-3_000, 3_000, 3_000]
        assert _no_number(rows, "AC-105") == [], _no_number(rows, "AC-105")
        detail = FR.line_detail(c, "human:gus", account(c, "6900"), "2026-10-01", "2026-10-31", at=AT)
        assert len(detail["entries"]) == 3 and _no_number(detail["entries"], "AC-105") == []
        assert FR.transactions(c, "human:gus", start="2026-10-01", end="2026-10-31", q="AC-105")["rows"] == []
        found = FR.transactions(c, OWNER, start="2026-10-01", end="2026-10-31", source="entry", q="AC-105")["rows"]
        assert {r["memo"] for r in found} >= {"Pays bill AC-105"}


def test_line_detail_hides_a_pays_bill_line_for_a_private_bill_amount_and_all(books):
    """Fix check: a payroll bill (private line) marked paid from a bank row already on Other expenses. Its "Pays
    bill" line moves the spend off Other expenses; to gus (P&L totals, no payroll detail) neither that line nor the
    row's own line shows in Other expenses' detail, as in Transactions. The owner sees both."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        made = act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                                "parent_id": account(c, "6000")})
        payroll = str(made.result["account_id"])
        act(c, OWNER, "private_lines_set", {"account_id": payroll, "others_see": "totals"})
        _ingest(c, _txn("txn-pay", -40.0, who="ACME PAYROLL", day="2026-10-14"))
        bid = _bill(c, _vendor(c, "Acme Payroll"), [{"account_id": payroll, "amount": 4_000}], number="PR-7")
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
        paid = act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "money_account_id": _checking(c)})
        assert paid.result["from"] == "feed", paid
        assert act(c, OWNER, "view_grant", {"subject": "human:gus", "what": "pnl"}).status == "committed"
    with books.read() as c:
        mine = FR.line_detail(c, OWNER, account(c, "6900"), "2026-10-01", "2026-10-31", at=AT)
        assert sorted(e["amount"] for e in mine["entries"]) == [-4_000, 4_000]
        gus = FR.line_detail(c, "human:gus", account(c, "6900"), "2026-10-01", "2026-10-31", at=AT)
        assert gus["entries"] == [], gus["entries"]
        rows = FR.transactions(c, "human:gus", start="2026-10-01", end="2026-10-31", source="entry")["rows"]
        assert [r for r in rows if abs(r["amount"]) == 4_000] == []


def test_a_pays_invoice_line_never_names_an_invoice_with_a_private_line(books):
    """Grok fix check: an invoice with a line on a private account is cut from dan's receivables (keep_docs), so its
    "Pays invoice" line shows the line it moved off, with no invoice number, to dan; the owner sees the invoice.
    Private lines are expense lines today, so the income line is made private in the store, as a later rule might."""
    from backend.finance.store import access as SA
    connect(books, FakeMercury())
    with books.transaction() as c:
        made = act(c, OWNER, "account_create", {"code": "4100", "name": "Advisory", "type": "income",
                                                "parent_id": account(c, "4000")})
        private = str(made.result["account_id"])
        SA.set_private(c, private, "totals", [], OWNER, AT)
        SA.bump_view(c, f"private {private}", AT)
        row = _ingest(c, _txn("txn-inv", 500.0, who="ACME RETAIL", day="2026-10-14", kind="incomingDomesticWire"))
        assert act(c, OWNER, "categorize", {"feed_id": row, "account_id": account(c, "4000")}).status == "committed"
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}).result["contact_id"])
        made = act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": "2026-10-01",
                                                "terms_days": 30, "lines": [{"account_id": private, "amount": 50_000}]})
        assert made.status == "committed", made
        iid = str(made.result["invoice_id"])
        assert act(c, OWNER, "invoice_send", {"invoice_id": iid}).status == "committed"
        got = act(c, OWNER, "invoice_record_payment", {"invoice_id": iid, "amount": 50_000,
                                                       "account_id": account(c, "1000")})
        assert (got.result["from"], got.result["status"]) == ("feed", "paid"), got
        for what in ("pnl", "receivables"):
            assert act(c, OWNER, "view_grant", {"subject": "human:dan", "what": what}).status == "committed"
    with books.read() as c:
        assert FR.invoices(c, "human:dan")["invoices"] == []                    # cut from dan's receivables
        mine = FR.transactions(c, OWNER, start="2026-10-01", end="2026-10-31", source="entry")["rows"]
        assert [r["account"] for r in mine if r["memo"] == "Pays invoice INV-0001"] == ["Invoice INV-0001"]
        rows = FR.transactions(c, "human:dan", start="2026-10-01", end="2026-10-31", source="entry")["rows"]
        assert [r for r in rows if r["entry_id"] and r["amount"] == 50_000], rows
        assert _no_number(rows, "INV-0001") == [], rows


def test_sync_now_polls_payments_waiting_on_the_bank_so_a_rejection_lands_at_once(books):
    """E2E re-test: Sync now on a connection also makes its payments waiting in Mercury's approval queue due for a
    poll, so a rejection there cancels the payment on that sync, not on the poll cadence."""
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        made = act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 7_000, "account_id": account(c, "6100"),
                                                "money_account_id": _checking(c)})
        pid = str(made.result["payment_id"])
    sync_once(books, fake, HOURS[1])

    def status(c):
        return str(c.execute("SELECT status FROM fin_payments WHERE id=?", (pid,)).fetchone()[0])
    with books.read() as c:
        assert status(c) == "awaiting_bank_approval"
        request = str(c.execute("SELECT provider_request_id FROM fin_payments WHERE id=?", (pid,)).fetchone()[0])
    fake.requests[request] = {**fake.requests[request], "status": "rejected"}
    soon = "2026-10-15T14:31:00.000000Z"
    sync_once(books, fake, soon)
    with books.transaction() as c:
        assert status(c) == "awaiting_bank_approval"                      # not yet due on its own cadence
        cid = str(c.execute("SELECT id FROM fin_connections").fetchone()[0])
        assert act(c, OWNER, "connection_sync", {"connection_id": cid}, at=soon).status == "committed"
    sync_once(books, fake, soon)
    with books.read() as c:
        assert status(c) == "cancelled"


def test_padded_ids_are_stored_trimmed_so_a_reschedule_still_withdraws_the_ask(books):
    """Codex fix check: a payment_create for " bill:<id> " waits on dan; a reschedule sent with " <id> " withdraws it,
    and both payloads are stored with the trimmed ids the planners read."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        bid = _scheduled_today(c, ask=True)
        rid = str(c.execute("SELECT id FROM fin_recipients").fetchone()[0])
        ask = act(c, OWNER, "payment_create", {"recipient_id": f" {rid} ", "amount": 50_000, "obligation": f" bill:{bid} ",
                                               "account_id": account(c, "2000"), "money_account_id": _checking(c)})
        assert ask.status == "needs_approval", ask
        moved = act(c, OWNER, "bill_schedule", {"bill_id": f"  {bid} ", "pay_on": "2026-11-15",
                                                "money_account_id": f"{_checking(c)} "})
        assert moved.status == "committed", moved
        assert FA.action(c, ask.action_id).status == "withdrawn"

        def stored(aid):
            return json.loads(c.execute("SELECT payload_json FROM fin_actions WHERE id=?", (aid,)).fetchone()[0])
        assert (stored(ask.action_id)["obligation"], stored(ask.action_id)["recipient_id"]) == (f"bill:{bid}", rid)
        assert stored(moved.action_id)["bill_id"] == bid
        assert FR.bill(c, OWNER, bid)["status"] == "scheduled"


def test_a_request_stored_with_padded_ids_before_trimming_still_replays(books):
    """Fix check: an action stored before ids were trimmed (its payload holds " <id> ") replays for the same key,
    padded or not, rather than answering that the key was used for a different request."""
    with books.transaction() as c:
        vendor = _vendor(c)
        first = act(c, OWNER, "bill_create", {"vendor_id": vendor, "bill_date": "2026-10-10",
                                              "lines": [{"account_id": account(c, "6100"), "amount": 1_000}]}, key="old")
        assert first.status == "committed", first
        row = json.loads(c.execute("SELECT payload_json FROM fin_actions WHERE id=?", (first.action_id,)).fetchone()[0])
        c.execute("UPDATE fin_actions SET payload_json=? WHERE id=?",
                  (json.dumps({**row, "vendor_id": f" {vendor} "}, sort_keys=True), first.action_id))
        for sent in (f" {vendor} ", vendor):
            again = act(c, OWNER, "bill_create", {"vendor_id": sent, "bill_date": "2026-10-10",
                                                  "lines": [{"account_id": account(c, "6100"), "amount": 1_000}]},
                        key="old")
            assert (again.replayed, again.action_id) == (True, first.action_id), again


def test_rescheduling_a_bill_withdraws_its_waiting_pay_approval_so_the_old_day_never_pays(books):
    """Codex fix check: a scheduled bill's payment waits on dan's approval; the bill is rescheduled to Nov 15. The
    waiting payment_create is withdrawn with its approval, so approving it later runs nothing, the bill stays
    scheduled for its new day, and that day asks afresh. A live payment still refuses a reschedule."""
    from backend import hubdb as H
    connect(books, FakeMercury())
    with books.transaction() as c:
        bid = _scheduled_today(c, ask=True)
        FA.watch_documents(c, AT)
        old = FA.action(c, str(FR.bill(c, OWNER, bid)["schedule"]["action_id"]))
        assert old.status == "needs_approval", old
        moved = act(c, OWNER, "bill_schedule", {"bill_id": bid, "pay_on": "2026-11-15", "money_account_id": _checking(c)})
        assert moved.status == "committed", moved
        assert FA.action(c, old.action_id).status == "withdrawn"
        assert H.approval(c, str(old.approval_id))["decision"] == "withdrawn"
        assert FA.approval_decided(c, str(old.approval_id), AT).status == "withdrawn"     # approving runs nothing
        bill = FR.bill(c, OWNER, bid)
        assert (bill["status"], bill["pay_on"], bill["payment"]) == ("scheduled", "2026-11-15", None)
        FA.watch_documents(c, AT)
        assert c.execute("SELECT count(*) FROM fin_payments").fetchone()[0] == 0
        FA.watch_documents(c, "2026-11-15T12:00:00.000000Z")
        again = FA.action(c, str(FR.bill(c, OWNER, bid)["schedule"]["action_id"]))
        assert again.action_id != old.action_id and again.status == "needs_approval"
        H.approval_decide(c, str(again.approver), str(again.approval_id), "approved")
        assert FA.approval_decided(c, str(again.approval_id), "2026-11-15T12:00:00.000000Z").status == "committed"
        refused = act(c, OWNER, "bill_schedule", {"bill_id": bid, "pay_on": "2026-11-20", "money_account_id": _checking(c)},
                      at="2026-11-15T12:00:00.000000Z")
        assert (refused.status, refused.code) == ("failed", "conflict"), refused


def _settled(c, table: str, row_id: str) -> tuple[dict[str, object], int]:
    """A document's whole row and the number of journal entries: what a refused settlement must leave as it was."""
    row = dict(c.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone())
    return row, int(c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0])


def test_an_explicit_zero_settles_nothing_on_an_invoice_write_off_or_payment(books):
    """An amount of 0 (or less) is refused, never read as the whole balance left: the write-off and the payment fail
    before anything posts, and the invoice and the journal stay exactly as they were."""
    with books.transaction() as c:
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}).result["contact_id"])
        iid = str(act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": "2026-10-01", "lines": [
            {"account_id": account(c, "4000"), "amount": 30_000}]}).result["invoice_id"])
        assert act(c, OWNER, "invoice_send", {"invoice_id": iid}).status == "committed"
        before = _settled(c, "fin_invoices", iid)
        for amount in (0, -1):
            off = act(c, OWNER, "invoice_write_off", {"invoice_id": iid, "amount": amount})
            paid = act(c, OWNER, "invoice_record_payment", {"invoice_id": iid, "amount": amount,
                                                            "account_id": account(c, "1000")})
            assert (off.status, off.code) == ("failed", "invalid"), off
            assert (paid.status, paid.code) == ("failed", "invalid"), paid
        assert _settled(c, "fin_invoices", iid) == before


def test_an_explicit_zero_pays_nothing_of_a_bill(books):
    """bill_mark_paid with an amount of 0 (or less) is refused before anything posts: the bill stays open with its
    whole balance, and the journal is unchanged."""
    with books.transaction() as c:
        bid = _bill(c, _vendor(c), [{"account_id": account(c, "6100"), "amount": 20_000}])
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
        before = _settled(c, "fin_bills", bid)
        for amount in (0, -1):
            got = act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "account_id": account(c, "1000"), "amount": amount})
            assert (got.status, got.code) == ("failed", "invalid"), got
        assert _settled(c, "fin_bills", bid) == before


def test_a_voided_document_reverses_on_its_own_date_and_a_closed_date_rolls_to_the_open_period(books):
    """§4.1: a void reverses on the document's own date. Only a date in a closed period moves to the current open one
    (AT is 2026-10-15), so a September bill's and invoice's reversals stay in September and an August bill's rolls."""
    with books.transaction() as c:
        vendor = _vendor(c)
        bill = _bill(c, vendor, [{"account_id": account(c, "6100"), "amount": 5_000}], day="2026-09-10")
        assert act(c, OWNER, "bill_approve", {"bill_id": bill}).status == "committed"
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}).result["contact_id"])
        inv = str(act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": "2026-09-12", "lines": [
            {"account_id": account(c, "4000"), "amount": 7_000}]}).result["invoice_id"])
        assert act(c, OWNER, "invoice_send", {"invoice_id": inv}).status == "committed"
        assert act(c, OWNER, "bill_void", {"bill_id": bill}).status == "committed"
        assert act(c, OWNER, "invoice_void", {"invoice_id": inv}).status == "committed"
        assert [r[0] for r in c.execute("SELECT entry_date FROM fin_entries WHERE memo LIKE 'Void %' "
                                        "ORDER BY entry_date").fetchall()] == ["2026-09-10", "2026-09-12"]
        august = _bill(c, vendor, [{"account_id": account(c, "6100"), "amount": 3_000}], day="2026-08-10")
        assert act(c, OWNER, "bill_approve", {"bill_id": august}).status == "committed"
        assert act(c, OWNER, "close_period", {"month": "2026-08"}).status == "committed"
        assert act(c, OWNER, "bill_void", {"bill_id": august}).status == "committed"
        assert [r[0] for r in c.execute("SELECT entry_date FROM fin_entries WHERE memo LIKE 'Void bill %' AND "
                                        "entry_date>'2026-09-30' ORDER BY entry_date").fetchall()] == ["2026-10-15"]
