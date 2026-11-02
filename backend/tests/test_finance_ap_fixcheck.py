"""Finance phase 2 fix-check: one regression per finding. A combined row past the search cap waits for a person (and
feed_match links the whole set); a bill's payment and its bank row hide a private bill from a balance-sheet viewer; a
queued bill payment meets its budgets again before it goes; a combined row arriving after a close moves the bank once
in every period. Synthetic fixtures (Acme)."""
import pytest

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import documents as FD
from backend.finance import payments as FP
from backend.finance import reads as FR
from backend.finance.context import load
from backend.finance.core.errors import CONFLICT
from backend.finance.store import banking as SK
from backend.tests.test_finance_ap import _balance, _bill, _events, _ingest, _txn, _vendor
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, FakeMercury, account, act, books, connect, finance_template, store, sync_once)
from backend.tests.test_finance_payments import HOURS, _checking, _payee

NOV = "2026-11-05T12:00:00.000000Z"
DEC = "2026-12-03T12:00:00.000000Z"


def _approved(c, vendor, amount, code="6100", **more) -> str:
    bid = _bill(c, vendor, [{"account_id": account(c, code), "amount": amount}], **more)
    assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
    return bid


def _mark(c, bid, day, at=AT):
    done = act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "money_account_id": _checking(c), "date": day}, at=at)
    assert done.status == "committed", done


def _bank_on(c, day) -> int:
    return int(c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
                         "JOIN fin_accounts a ON a.id=l.account_id WHERE a.code='1000' AND e.entry_date<=?",
                         (day,)).fetchone()[0])


def test_a_combined_row_past_the_search_cap_waits_and_feed_match_links_the_whole_set(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        vendor = _vendor(c)
        bank = _balance(c, "1000")
        for i in range(13):
            _mark(c, _approved(c, vendor, 1_000, number=f"D-{i}"), "2026-10-13")
        row = _ingest(c, _txn("txn-13", -130.0, who="MERCURY ACH"))
        # Fix-check 1: 13 candidates are more than the search checks, so nothing posts a second 130.00.
        assert c.execute("SELECT status, entry_id FROM fin_feed_txns WHERE id=?", (row,)).fetchone()[:] == ("new", None)
        assert row in {s for s, _ in _events(c, "finance.feed.needs_category")}
        assert _balance(c, "1000") - bank == -13_000 and _balance(c, "6900") == 0
        listed = FR.feed_expected(c, OWNER, row, at=AT)["expected"]
        assert len(listed) == 13
        done = act(c, OWNER, "feed_match", {"feed_id": row, "settlement_ids": [x["settlement_id"] for x in listed]})
        assert done.status == "committed", done
        assert _balance(c, "1000") - bank == -13_000 and (_balance(c, "2000"), _balance(c, "6100")) == (0, 13_000)


def test_a_private_bills_payment_and_bank_row_stay_hidden_from_a_balance_sheet_viewer(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        payroll = str(act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                                       "parent_id": account(c, "6000")}).result["account_id"])
        act(c, OWNER, "private_lines_set", {"account_id": payroll, "others_see": "totals"})
        vendor = _vendor(c, recipient_id=rid)
        hidden, shown = _approved(c, vendor, 10_000, code="6200"), _approved(c, vendor, 2_500)
        for bid in (hidden, shown):
            made = act(c, OWNER, "payment_create", {"recipient_id": rid, "money_account_id": _checking(c),
                                                    "obligation": f"bill:{bid}", "account_id": account(c, "2000"),
                                                    "amount": 10_000 if bid == hidden else 2_500})
            assert made.status == "committed", made
        act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "balance_sheet"})
        act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "payables"})
        act(c, OWNER, "view_grant", {"subject": "human:gus", "what": "balance_sheet"})
        # The bank side of a private bill marked paid (no Finance payment) is cut too.
        late = _approved(c, _vendor(c, "Acme Payroll"), 4_000, code="6200")
        _mark(c, late, "2026-10-13")
        row = _ingest(c, _txn("txn-pay", -40.0, who="Acme Payroll"))
        assert c.execute("SELECT status FROM fin_feed_txns WHERE id=?", (row,)).fetchone()[0] == "posted"
    with books.read() as c:
        amounts = {p["obligation"]: p["amount"] for p in FR.payments(c, OWNER)["payments"]}
        assert amounts == {f"bill:{hidden}": 10_000, f"bill:{shown}": 2_500}
        dan = [p["amount"] for p in FR.payments(c, "human:dan")["payments"]]
        assert dan == [2_500]                                                            # fix-check 2
        assert [p["amount"] for p in FR.payments(c, "human:gus")["payments"]] == []      # no payables view
        due = [s["due_out"]["amount"] for a in FR.balance_sheet(c, "human:dan")["assets"] for s in a["sources"]]
        assert sum(due) == 2_500                                                         # due-out totals too
        assert row in {x["id"] for x in FR.transactions(c, OWNER)["rows"]}
        assert row not in {x["id"] for x in FR.transactions(c, "human:dan")["rows"]}


def test_a_queued_bill_payment_asks_when_its_budget_is_lowered_past_actuals(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        budget = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"),
                                                 "amount": 50_000, "at_limit": "hold_payment"})
        bid = _approved(c, _vendor(c, recipient_id=rid), 10_000)
        FA.grant_defaults(c, "bot:seller", at=AT)
        act(c, OWNER, "view_grant", {"subject": "bot:seller", "what": "everything"})
        made = act(c, "bot:seller", "payment_create", {"recipient_id": rid, "money_account_id": _checking(c),
                                                       "obligation": f"bill:{bid}", "account_id": account(c, "2000"),
                                                       "amount": 10_000})
        assert made.status == "committed", made
        pid = str(made.result["payment_id"])
        assert FP.recheck(load(c, AT), SK.get(c, "fin_payments", pid)) == ""
        assert act(c, OWNER, "budget_update", {"budget_id": budget.result["budget_id"],
                                               "amount": 5_000}).status == "committed"
        # Fix-check 3: the bill's budget is past its amount now, so the queued payment waits for a person.
        assert FP.recheck(load(c, AT), SK.get(c, "fin_payments", pid)) == "wait"
        pay = SK.get(c, "fin_payments", pid)
        assert (pay["status"], pay["last_error"]) == ("queued", "the Software budget is over")


def test_a_combined_row_after_a_close_moves_the_bank_once_in_every_period(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        vendor = _vendor(c)
        start = _bank_on(c, "2026-12-31")
        _mark(c, _approved(c, vendor, 1_000, number="E-1"), "2026-10-30")
        _mark(c, _approved(c, vendor, 2_000, number="E-2"), "2026-10-30")
        assert act(c, OWNER, "close_period", {"month": "2026-10"}, at=NOV).status == "committed"
        raw = _txn("txn-e", -30.0, who="MERCURY ACH", day="2026-11-01")
        row = _ingest(c, raw, at=DEC)
        entry = c.execute("SELECT entry_id FROM fin_feed_txns WHERE id=?", (row,)).fetchone()[0]
        assert entry and c.execute("SELECT entry_date FROM fin_entries WHERE id=?", (entry,)).fetchone()[0] == \
            "2026-12-03"
        # Fix-check 4: November never shows the bank moving twice; the reversals and the row share December.
        for day in ("2026-10-31", "2026-11-30", "2026-12-31"):
            assert _bank_on(c, day) - start == -3_000, day
        _ingest(c, raw, at=DEC)                                              # the same row again changes nothing
        assert c.execute("SELECT entry_id FROM fin_feed_txns WHERE id=?", (row,)).fetchone()[0] == entry
        assert _bank_on(c, "2026-11-30") - start == -3_000


def test_a_row_owing_the_same_as_more_bills_than_are_looked_at_waits_for_a_person(books, monkeypatch):  # noqa: F811
    monkeypatch.setattr(FD, "OWING_MOST", 3)
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        acme = _vendor(c)
        for i in range(3):
            _approved(c, acme, 50_000, number=f"F-{i}")
        north = _approved(c, _vendor(c, "Northwind"), 50_000, number="N-1")
        before = (_balance(c, "1000"), _balance(c, "2000"))
        row = _ingest(c, _txn("txn-n", -500.0, who="Northwind"))
        # Grok 1: four bills owe 500.00, more than the search reads: no new expense, the row waits and asks.
        assert c.execute("SELECT status, entry_id FROM fin_feed_txns WHERE id=?", (row,)).fetchone()[:] == ("new", None)
        assert row in {s for s, _ in _events(c, "finance.feed.needs_category")}
        assert (_balance(c, "1000"), _balance(c, "2000"), _balance(c, "6900")) == (*before, 0)
        done = act(c, OWNER, "bill_mark_paid", {"bill_id": north, "feed_id": row})       # a person links it
        assert done.status == "committed", done
        assert FR.bill(c, OWNER, north)["status"] == "paid"
        assert (_balance(c, "1000") - before[0], _balance(c, "2000") - before[1]) == (-50_000, 50_000)


def test_a_combined_search_that_stops_early_is_unclear(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        acme, freight, north = _vendor(c), _vendor(c, "Northwind Freight"), _vendor(c, "Northwind")
        bank = _balance(c, "1000")
        n = 0
        for vendor, amounts in ((acme, (3_000, 7_000, 3_000, 2_000, 8_000)), (freight, (5_000, 5_000)),
                                (north, (6_000, 4_000))):
            for amount in amounts:
                n += 1
                _mark(c, _approved(c, vendor, amount, number=f"G-{n}"), "2026-10-13")
        row = _ingest(c, _txn("txn-g", -100.0, who="Northwind"))
        # Grok 2: the search found four sets (one Northwind Freight's) and stopped before Northwind's own: unclear.
        assert c.execute("SELECT status, entry_id FROM fin_feed_txns WHERE id=?", (row,)).fetchone()[:] == ("new", None)
        assert row in {s for s, _ in _events(c, "finance.feed.needs_category")}
        assert _balance(c, "1000") - bank == -43_000 and _balance(c, "6900") == 0


def test_the_inbox_pages_each_kind_past_its_cap(books, monkeypatch):  # noqa: F811
    import backend.finance.reads as reads
    monkeypatch.setattr(reads, "INBOX_EACH", 3)
    with books.transaction() as c:
        acme = _vendor(c)
        made = [_bill(c, acme, [{"account_id": account(c, "6100"), "amount": 1_000 + i}], number=f"H-{i}")
                for i in range(8)]
    with books.read() as c:
        whole = FR.inbox(c, OWNER, at=AT)
        assert whole["counts"]["bill_to_enter"] == 3 and "bill_to_enter" in whole["more"]
        # Grok 3: the kind pages from its own query, so following next_cursor reaches every draft.
        seen, cursor = [], None
        while True:
            page = FR.inbox(c, OWNER, kind="bill_to_enter", limit=3, cursor=cursor, at=AT)
            seen += [x["bill_id"] for x in page["items"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert len(seen) == 8 and sorted(seen) == sorted(made) and page["more"] == []


def test_a_card_charge_past_the_bill_search_waits_then_settles_its_hold_when_linked(books, monkeypatch):  # noqa: F811
    from backend.tests.test_finance_banking import _arrives, _card_for_eve, _card_holds, _card_use
    from backend.tests.test_finance_fixtures import MERCURY, sync_once
    from backend.tests.test_finance_payments import HOURS
    monkeypatch.setattr(FD, "OWING_MOST", 0)
    fake = FakeMercury()
    fake.balances["credit-io"] = 0
    connect(books, fake)
    _, card_id = _card_for_eve(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        bid = _approved(c, _vendor(c, "Acme Cloud"), 4_500)
    _arrives(fake, MERCURY["txn_card_pending"], 1, "evt-0001")
    sync_once(books, fake, HOURS[1])
    _arrives(fake, MERCURY["txn_card_posted"], 2, "evt-0002")
    sync_once(books, fake, HOURS[2])
    with books.transaction() as c:
        row = c.execute("SELECT id, status, entry_id FROM fin_feed_txns WHERE card_id IS NOT NULL").fetchone()
        assert (row["status"], row["entry_id"]) == ("new", None)                         # Grok 1, card side
        assert (_balance(c, "2100"), _balance(c, "6100")) == (0, 4_500)
        done = act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "feed_id": str(row["id"])})
        assert done.status == "committed", done
        assert (_balance(c, "6100"), _balance(c, "2000"), _balance(c, "2100")) == (4_500, 0, -4_500)
        assert _card_use(c, card_id, "2026-10") == 4_500 and _card_holds(c) == [(4_250, "settled")]


def _payroll(c) -> str:
    made = act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                            "parent_id": account(c, "6000")})
    act(c, OWNER, "private_lines_set", {"account_id": made.result["account_id"], "others_see": "totals"})
    return str(made.result["account_id"])


def test_a_private_bills_lost_send_debit_matched_by_its_note_stays_hidden(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        _payroll(c)
        bid = _approved(c, _vendor(c, recipient_id=rid), 10_000, code="6200")
        made = act(c, OWNER, "payment_create", {"recipient_id": rid, "money_account_id": _checking(c),
                                                "obligation": f"bill:{bid}", "account_id": account(c, "2000"),
                                                "amount": 10_000})
        pid = str(made.result["payment_id"])
        c.execute("UPDATE fin_payments SET status='unknown', provider_request_id=NULL WHERE id=?", (pid,))
        key = c.execute("SELECT idempotency_key FROM fin_payments WHERE id=?", (pid,)).fetchone()[0]
        act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "balance_sheet"})
        row = _ingest(c, {**_txn("txn-lost", -100.0, who="Acme Supplies"), "requestId": None, "note": f"tico:{key}"})
        assert c.execute("SELECT request_id FROM fin_feed_txns WHERE id=?", (row,)).fetchone()[0] in (None, "")
        assert FR.bill(c, OWNER, bid)["status"] == "paid"
    with books.read() as c:
        assert row in {x["id"] for x in FR.transactions(c, OWNER)["rows"]}
        assert row not in {x["id"] for x in FR.transactions(c, "human:dan")["rows"]}          # Codex r3 1


def test_per_kind_inbox_pages_never_skip_an_item_after_a_hidden_one(books):  # noqa: F811
    days = ["2026-10-01T09:00:00.000000Z", "2026-10-02T09:00:00.000000Z", "2026-10-03T09:00:00.000000Z",
            "2026-10-04T09:00:00.000000Z"]
    with books.transaction() as c:
        payroll = _payroll(c)
        acme = _vendor(c)
        made = []
        for n, at in enumerate(days):
            code = payroll if n == 0 else account(c, "6100")
            done = act(c, OWNER, "bill_create", {"vendor_id": acme, "bill_date": "2026-10-01", "number": f"K-{n}",
                                                 "lines": [{"account_id": code, "amount": 1_000 + n}]}, at=at)
            made.append(str(done.result["bill_id"]))
        act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "payables"})
    with books.read() as c:
        seen, cursor = [], None
        for _ in range(10):
            page = FR.inbox(c, "human:dan", kind="bill_to_enter", limit=2, cursor=cursor, at=AT)
            seen += [x["bill_id"] for x in page["items"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert seen == made[1:]                                                           # Codex r3 2


def test_an_approved_over_budget_bill_payment_sends_and_a_later_drop_does_not_hold_it(books):  # noqa: F811
    from backend import hubdb as H
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        budget = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"),
                                                 "amount": 5_000, "at_limit": "hold_payment"})
        bid = _approved(c, _vendor(c, recipient_id=rid), 15_000)
        FA.grant_defaults(c, "bot:seller", at=AT)
        act(c, OWNER, "view_grant", {"subject": "bot:seller", "what": "everything"})
        asked = act(c, "bot:seller", "payment_create", {"recipient_id": rid, "money_account_id": _checking(c),
                                                        "obligation": f"bill:{bid}", "amount": 15_000,
                                                        "account_id": account(c, "2000")})
        assert (asked.status, asked.rule) == ("needs_approval", f"budget:{budget.result['budget_id']}"), asked
        H.approval_decide(c, OWNER, asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == "committed", done
        pid = str(done.result["payment_id"])
        # Grok r3: approved for that budget, so every recheck lets it send (it is not held again and again).
        assert FP.recheck(load(c, AT), SK.get(c, "fin_payments", pid)) == ""
        assert act(c, OWNER, "budget_update", {"budget_id": budget.result["budget_id"],
                                               "amount": 1_000}).status == "committed"
        assert FP.recheck(load(c, AT), SK.get(c, "fin_payments", pid)) == ""          # the approval covers it
        assert SK.get(c, "fin_payments", pid)["status"] == "queued"


def test_an_approval_for_one_budget_never_covers_another_now_over(books):  # noqa: F811
    from backend import hubdb as H
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        a = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 5_000,
                                            "at_limit": "hold_payment"}).result["budget_id"]
        made = act(c, OWNER, "budget_create", {"name": "Freight", "account_id": account(c, "6900"), "amount": 50_000,
                                               "at_limit": "hold_payment"})
        assert made.status == "committed", made
        b = made.result["budget_id"]
        bid = _bill(c, _vendor(c, recipient_id=rid), [{"account_id": account(c, "6100"), "amount": 10_000},
                                                       {"account_id": account(c, "6900"), "amount": 5_000}])
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
        FA.grant_defaults(c, "bot:seller", at=AT)
        act(c, OWNER, "view_grant", {"subject": "bot:seller", "what": "everything"})
        asked = act(c, "bot:seller", "payment_create", {"recipient_id": rid, "money_account_id": _checking(c),
                                                        "obligation": f"bill:{bid}", "amount": 15_000,
                                                        "account_id": account(c, "2000")})
        assert (asked.status, asked.rule) == ("needs_approval", f"budget:{a}"), asked
        H.approval_decide(c, OWNER, asked.approval_id, "approved")
        pid = str(FA.approval_decided(c, asked.approval_id, AT).result["payment_id"])
        assert FP.recheck(load(c, AT), SK.get(c, "fin_payments", pid)) == ""
        act(c, OWNER, "budget_update", {"budget_id": a, "amount": 100_000})               # A recovers
        act(c, OWNER, "budget_update", {"budget_id": b, "amount": 1_000})                 # B is over now
        # Codex r4: the approval was for budget A; B asks a person again (the bot may not approve it itself).
        assert FP.recheck(load(c, AT), SK.get(c, "fin_payments", pid)) == "wait"
        assert SK.get(c, "fin_payments", pid)["last_error"] == "the Freight budget is over"


def test_a_bot_marking_a_bill_paid_past_a_budget_waits_for_a_person_as_its_payment_does(books):  # noqa: F811
    from backend import hubdb as H
    connect(books, FakeMercury())
    with books.transaction() as c:
        budget = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"),
                                                 "amount": 5_000, "at_limit": "ask"})
        assert budget.status == "committed", budget
        bid = _approved(c, _vendor(c), 15_000)
        FA.grant_defaults(c, "bot:seller", at=AT)
        act(c, OWNER, "view_grant", {"subject": "bot:seller", "what": "everything"})
        want = ("needs_approval", f"budget:{budget.result['budget_id']}")
        dry = act(c, "bot:seller", "bill_mark_paid", {"bill_id": bid}, dry_run=True)
        assert (dry.status, dry.rule) == want, dry
        asked = act(c, "bot:seller", "bill_mark_paid", {"bill_id": bid})
        assert (asked.status, asked.rule) == want, asked
        assert FR.bill(c, OWNER, bid)["status"] == "open"
        H.approval_decide(c, OWNER, asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == "committed", done
        assert FR.bill(c, OWNER, bid)["status"] == "paid"


def test_a_bill_waiting_for_a_payment_is_not_asked_again_by_a_mark_paid_or_a_payment(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        budget = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"),
                                                 "amount": 5_000, "at_limit": "ask"})
        assert budget.status == "committed", budget
        vendor = _vendor(c, recipient_id=rid)
        paid_first = _approved(c, vendor, 15_000, number="B-1")
        marked_first = _approved(c, vendor, 15_000, number="B-2")
        FA.grant_defaults(c, "bot:seller", at=AT)
        act(c, OWNER, "view_grant", {"subject": "bot:seller", "what": "everything"})
        pay = {"recipient_id": rid, "money_account_id": _checking(c), "amount": 15_000,
               "account_id": account(c, "2000")}
        asked = act(c, "bot:seller", "payment_create", {**pay, "obligation": f"bill:{paid_first}"})
        assert asked.status == "needs_approval", asked
        before = c.execute("SELECT COUNT(*) FROM fin_actions WHERE status='needs_approval'").fetchone()[0]
        # One ask per bill: a mark-paid waits behind the payment asked for its bill, and the reverse too.
        mark = act(c, "bot:seller", "bill_mark_paid", {"bill_id": paid_first})
        assert (mark.status, mark.code) == ("failed", CONFLICT), mark
        assert "waiting for approval" in mark.reason
        mark_asked = act(c, "bot:seller", "bill_mark_paid", {"bill_id": marked_first})
        assert mark_asked.status == "needs_approval", mark_asked
        again = act(c, "bot:seller", "payment_create", {**pay, "obligation": f"bill:{marked_first}"})
        assert (again.status, again.code) == ("failed", CONFLICT), again
        assert c.execute("SELECT COUNT(*) FROM fin_actions WHERE status='needs_approval'").fetchone()[0] == before + 1
        assert FR.bill(c, OWNER, paid_first)["status"] == "open"


def _pay_bill(c, rid, bid, who="bot:seller"):
    return act(c, who, "payment_create", {"recipient_id": rid, "money_account_id": _checking(c), "amount": 15_000,
                                          "account_id": account(c, "2000"), "obligation": f"bill:{bid}"})


def test_a_bill_pays_once_across_a_declined_ask_a_second_ask_and_its_approval(books):  # noqa: F811
    """Item 3: a declined ask ends its wait, so the bill may be asked again; while that second one waits no third ask is
    taken, and approving it pays the bill once. Nothing pays it a second time (the live payment refuses another)."""
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        assert act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 5_000,
                                               "at_limit": "ask"}).status == "committed"
        bid = _approved(c, _vendor(c, recipient_id=rid), 15_000)
        FA.grant_defaults(c, "bot:seller", at=AT)
        act(c, OWNER, "view_grant", {"subject": "bot:seller", "what": "everything"})
        first = _pay_bill(c, rid, bid)
        assert first.status == "needs_approval", first
        H.approval_decide(c, OWNER, first.approval_id, "declined")
        assert FA.approval_decided(c, first.approval_id, AT).status != "committed"
        assert FR.bill(c, OWNER, bid)["status"] == "open"
        second = _pay_bill(c, rid, bid)
        assert second.status == "needs_approval", second
        third = _pay_bill(c, rid, bid)
        assert (third.status, third.code) == ("failed", CONFLICT), third
        assert "waiting for approval" in third.reason
        H.approval_decide(c, OWNER, second.approval_id, "approved")
        done = FA.approval_decided(c, second.approval_id, AT)
        assert done.status == "committed", done
        again = _pay_bill(c, rid, bid)
        assert (again.status, again.code) == ("failed", CONFLICT), again
        assert "live" in again.reason
        assert c.execute("SELECT COUNT(*) FROM fin_payments WHERE obligation=?", (f"bill:{bid}",)).fetchone()[0] == 1
    sync_once(books, fake, HOURS[1])
    assert [b["amount"] for b in fake.sent("request_send_money")] == [150]


@pytest.mark.parametrize("first", ["payment", "scheduled"])
def test_a_bill_with_a_scheduled_pay_and_a_payment_both_waiting_is_paid_once_whichever_is_approved(books, first):
    """Item 3: two asks for one bill can both wait when one is a scheduled pay (bill_schedule) and the other a payment
    (the one-ask guard covers payments and mark-paid asks). Approving both, in either order, pays the bill once: the
    payment is live, or it is withdrawn by the schedule, and the scheduled day never pays it a second time."""
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        bid = _approved(c, _vendor(c, recipient_id=rid), 15_000)
        obligation = f"bill:{bid}"
        FA.grant_defaults(c, "bot:seller", at=AT)
        act(c, OWNER, "view_grant", {"subject": "bot:seller", "what": "everything"})
        # A bot's scheduling is allowed by default: a finance rule makes it wait for a finance admin (a waiting ask).
        assert act(c, OWNER, "rule_create", {
            "kind": "gate", "name": "Bill schedules", "when": {"kind": "bill_schedule"},
            "outcome": {"outcome": "needs_approval", "who": "finance_admin"},
            "examples": [{"input": {"kind": "bill_schedule"}, "expect": "needs_approval"}]},
            at=AT).status == "committed"
        sched = act(c, "bot:seller", "bill_schedule", {"bill_id": bid, "pay_on": "2026-11-05",
                                                       "money_account_id": _checking(c)})
        assert sched.status == "needs_approval", sched
        pay = _pay_bill(c, rid, bid)
        assert pay.status == "needs_approval", pay
        for ask in (pay, sched) if first == "payment" else (sched, pay):
            status = c.execute("SELECT status FROM fin_actions WHERE id=?", (ask.action_id,)).fetchone()[0]
            if status != "needs_approval":
                continue                  # the schedule withdrew the payment's ask: its approval is gone
            H.approval_decide(c, OWNER, ask.approval_id, "approved")
            FA.approval_decided(c, ask.approval_id, AT)
        assert c.execute("SELECT status FROM fin_actions WHERE id=?", (pay.action_id,)).fetchone()[0] == (
            "committed" if first == "payment" else "withdrawn")
        assert c.execute("SELECT COUNT(*) FROM fin_payments WHERE obligation=?", (obligation,)).fetchone()[0] == (
            1 if first == "payment" else 0)
    sync_once(books, fake, HOURS[1])
    assert [b["amount"] for b in fake.sent("request_send_money")] == ([150] if first == "payment" else [])
    # The scheduled day: a live payment stops it; without one it asks again, once (a waiting ask, never a second pay).
    with books.transaction() as c:
        FA.watch_documents(c, "2026-11-05T13:00:00.000000Z")         # the hourly look: pay_due on the scheduled day
    with books.transaction() as c:
        asks = c.execute("SELECT id, approval_id FROM fin_actions WHERE status='needs_approval' "
                         "AND kind='payment_create' AND payload_json LIKE ?", (f"%{obligation}%",)).fetchall()
        paid = c.execute("SELECT COUNT(*) FROM fin_payments WHERE obligation=?", (obligation,)).fetchone()[0]
        assert paid + len(asks) == 1, (paid, asks)
        for _, approval in asks:
            H.approval_decide(c, OWNER, approval, "approved")
            assert FA.approval_decided(c, approval, AT).status == "committed"
    sync_once(books, fake, "2026-11-05T14:00:00.000000Z")
    assert [b["amount"] for b in fake.sent("request_send_money")] == [150]
