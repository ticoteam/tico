"""Finance phase 2: bills, invoices, contacts, settlements and the cash view, posting rules, the remaining defaults,
the new events, the close, and the reads that show them (search, report, metric, inbox) cut to each viewer."""
import random

import pytest

from backend.finance import actions as FA
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
