"""Finance phase 2 fix-check: one regression per finding. A combined row past the search cap waits for a person (and
feed_match links the whole set); a bill's payment and its bank row hide a private bill from a balance-sheet viewer; a
queued bill payment meets its budgets again before it goes; a combined row arriving after a close moves the bank once
in every period. Synthetic fixtures (Acme)."""
from backend.finance import actions as FA
from backend.finance import payments as FP
from backend.finance import reads as FR
from backend.finance.context import load
from backend.finance.store import banking as SK
from backend.tests.test_finance_ap import _balance, _bill, _events, _ingest, _txn, _vendor
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, FakeMercury, account, act, books, connect, finance_template, store)
from backend.tests.test_finance_payments import _checking, _payee

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
