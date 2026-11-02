"""Finance phase 2 review fixes: one regression per finding. Bills paid by Finance payments are checked against the bill
and its budgets; payments marked paid meet their bank rows once (combined, ambiguous or renamed); card charges and
receipts settle documents without a second expense; revised rows, closed months and retags keep the cash view right;
and the reads cut nested lists, private amounts and unseen accounts. Synthetic fixtures (Acme)."""
import json

from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance.store import ledger as SL
from backend.tests.test_finance_ap import LATER, _balance, _bill, _events, _ingest, _pnl, _txn, _vendor
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, account, act, books, connect, finance_template, store, sync_once)
from backend.tests.test_finance_payments import HOURS, _checking, _payee

NOV = "2026-11-05T12:00:00.000000Z"


def _approved(c, vendor, amount, code="6100", **more) -> str:
    bid = _bill(c, vendor, [{"account_id": account(c, code), "amount": amount, **more.pop("line", {})}], **more)
    assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
    return bid


def _mark(c, bid, **more):
    done = act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "money_account_id": _checking(c), **more})
    assert done.status == "committed", done
    return done


def test_a_bill_payment_must_pay_the_bill_to_ap_and_asks_when_its_budget_is_spent(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        vendor = _vendor(c, recipient_id=rid)
        bid = _approved(c, vendor, 10_000)
        pay = {"recipient_id": rid, "money_account_id": _checking(c), "obligation": f"bill:{bid}",
               "account_id": account(c, "2000"), "amount": 10_000}
        # Codex 5: the expense again, more than it owes, or a bill outside these books are refused.
        assert act(c, OWNER, "payment_create", {**pay, "account_id": account(c, "6100")}).code == "invalid"
        assert act(c, OWNER, "payment_create", {**pay, "amount": 10_001}).code == "invalid"
        assert act(c, OWNER, "payment_create", {**pay, "obligation": "bill:nope"}).code == "not_found"
        FA.grant_defaults(c, "bot:seller", at=AT)
        act(c, OWNER, "view_grant", {"subject": "bot:seller", "what": "everything"})
        before = act(c, "bot:seller", "payment_create", pay, dry_run=True)
        # Codex 6: a hold_payment budget already past its amount sends the bill's payment to a finance admin.
        assert act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 5_000,
                                               "at_limit": "hold_payment"}).status == "committed"
        asked = act(c, "bot:seller", "payment_create", pay)
        assert (before.status, before.rule, asked.status, asked.rule[:7]) == ("committed", "", "needs_approval", "budget:"), asked
        assert c.execute("SELECT count(*) FROM fin_payments").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM fin_holds WHERE source='payment'").fetchone()[0] == 0


def test_payments_marked_paid_meet_one_combined_row_and_an_unclear_row_waits_instead_of_posting(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        vendor = _vendor(c)
        bank = _balance(c, "1000")
        one, two = _approved(c, vendor, 10_000, number="C-1"), _approved(c, vendor, 20_000, number="C-2")
        _mark(c, one, date="2026-10-13")
        _mark(c, two, date="2026-10-13")
        # Codex 3: one 300.00 debit named for the rail, not the vendor, pays both marked payments: the bank moves once.
        row = _ingest(c, _txn("txn-c", -300.0, who="MERCURY ACH"))
        assert _balance(c, "1000") - bank == -30_000
        assert (_balance(c, "2000"), _balance(c, "6900"), _balance(c, "6100")) == (0, 0, 30_000)
        assert {str(r[0]) for r in c.execute("SELECT from_id FROM fin_settlements WHERE voided_at IS NULL")} == {row}
        # Two equal payments and an unnamed match: the row waits for a person and posts nothing.
        three, four = _approved(c, vendor, 5_000, number="C-3"), _approved(c, vendor, 5_000, number="C-4")
        _mark(c, three, date="2026-10-14")
        _mark(c, four, date="2026-10-14")
        waits = _ingest(c, _txn("txn-d", -50.0, who="MERCURY ACH"))
        assert c.execute("SELECT status, entry_id FROM fin_feed_txns WHERE id=?", (waits,)).fetchone()[:] == ("new", None)
        assert waits in {s for s, _ in _events(c, "finance.feed.needs_category")}
        assert _balance(c, "1000") - bank == -40_000 and _balance(c, "6900") == 0
        # A person picks: the row's expected list names both, and feed_match links it like the automatic match.
        listed = FR.feed_expected(c, OWNER, waits, at=AT)["expected"]
        assert sorted(x["number"] for x in listed) == ["C-3", "C-4"]
        pick = {"feed_id": waits, "settlement_ids": [listed[0]["settlement_id"]]}
        assert act(c, OWNER, "feed_match", {**pick, "settlement_ids": [x["settlement_id"] for x in listed]}).code == \
            "invalid"                                                                    # 100.00 is not the row's 50.00
        assert act(c, "human:eve", "feed_match", pick).code in ("denied", "not_found")
        assert act(c, OWNER, "feed_match", pick, dry_run=True).status == "committed"
        linked = act(c, OWNER, "feed_match", pick)
        assert linked.status == "committed", linked
        assert c.execute("SELECT status FROM fin_feed_txns WHERE id=?", (waits,)).fetchone()[0] == "posted"
        assert _balance(c, "1000") - bank == -40_000 and FR.feed_expected(c, OWNER, waits, at=AT)["expected"] == []


def _unclear_row(c, who: str) -> tuple[str, str]:
    """Two equal payments marked paid from checking wait for a 50.00 debit that `who` names: no name picks one of them,
    so the row stays new. Returns the row and a third open bill owing the same amount to another vendor."""
    vendor = _vendor(c)
    one, two = _approved(c, vendor, 5_000, number="W-1"), _approved(c, vendor, 5_000, number="W-2")
    _mark(c, one, date="2026-10-13")
    _mark(c, two, date="2026-10-13")
    row = _ingest(c, _txn("txn-w", -50.0, who=who))
    assert _unbooked(c, row)
    return row, _approved(c, _vendor(c, "Northwind"), 5_000, number="W-3")


def _unbooked(c, row) -> bool:
    return tuple(c.execute("SELECT status, entry_id FROM fin_feed_txns WHERE id=?", (row,)).fetchone()) == ("new", None)


def _money(c) -> tuple[int, int, int]:
    return (_balance(c, "1000"), _balance(c, "2000"), _balance(c, "6900"))


def test_an_unbooked_row_that_marked_payments_wait_for_cannot_pay_another_bill(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        row, other = _unclear_row(c, "MERCURY ACH")
        before = _money(c)
        refused = act(c, OWNER, "bill_mark_paid", {"bill_id": other, "feed_id": row})
        dry = act(c, OWNER, "bill_mark_paid", {"bill_id": other, "feed_id": row}, dry_run=True)
        assert (refused.code, dry.code) == ("conflict", "conflict"), (refused, dry)
        assert "feed_match" in refused.reason
        assert FR.bill(c, OWNER, other)["status"] == "open" and _unbooked(c, row)
        assert _money(c) == before


def test_categorizing_an_unclear_row_is_refused_while_marked_payments_wait_for_it(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        # A posting rule for the counterparty does not post the row at ingest: the payments it may settle come first.
        assert act(c, OWNER, "rule_create", {
            "kind": "posting", "name": "Mercury", "when": {"counterparty": {"matches": "MERCURY"}},
            "outcome": {"outcome": "post", "account_id": account(c, "6100")},
            "examples": [{"input": {"counterparty": "MERCURY ACH"}, "expect": "post"}]}).status == "committed"
        row, _ = _unclear_row(c, "MERCURY ACH")
        before = _money(c)
        for dry in (True, False):
            tagged = act(c, OWNER, "categorize", {"feed_id": row, "account_id": account(c, "6900")}, dry_run=dry)
            assert (tagged.status, tagged.code) == ("failed", "conflict"), tagged
            assert "feed_match" in tagged.reason
        assert _unbooked(c, row) and _money(c) == before


def test_a_transfer_row_of_that_amount_is_still_categorized_while_payments_wait(books):  # noqa: F811
    connect(books, FakeMercury())
    with books.transaction() as c:
        _unclear_row(c, "MERCURY ACH")
        # A move between our own accounts is never matched to payments at ingest, so it is not held back either.
        move = _ingest(c, _txn("txn-move", -50.0, who="Savings", kind="internalTransfer"))
        assert _unbooked(c, move)
        tagged = act(c, OWNER, "categorize", {"feed_id": move, "account_id": account(c, "6900")})
        assert tagged.status == "committed", tagged


def test_a_payment_from_the_account_never_takes_a_transfer_row(books):  # noqa: F811
    connect(books, FakeMercury())
    with books.transaction() as c:
        bill = _approved(c, _vendor(c), 5_000, number="T-1")
        # One side of a move between our accounts, with no name: it waits for its other side, never pays a bill.
        move = _ingest(c, _txn("txn-move", -50.0, who="", kind="internalTransfer"))
        _mark(c, bill, date="2026-10-14")
        assert _unbooked(c, move)

def _paid_from_the_account_while_its_row_is_unclear(c, payment: dict[str, object]) -> None:
    """A bill paid from the account waits as an outstanding payment instead of taking the unclear row: the bank moves
    once for the payment, the row stays new, and a person links it (feed_match) to the payment it is."""
    row, other = _unclear_row(c, "Northwind")
    before = _money(c)
    payload = {"bill_id": other, "date": "2026-10-14", **payment}
    dry = act(c, OWNER, "bill_mark_paid", payload, dry_run=True)
    assert dry.status == "committed" and _money(c) == before, dry
    done = act(c, OWNER, "bill_mark_paid", payload)
    assert done.status == "committed", done
    assert (done.result["from"], done.result["feed_id"]) == ("expected", None), done
    assert _unbooked(c, row) and _balance(c, "1000") - before[0] == -5_000
    listed = FR.feed_expected(c, OWNER, row, at=AT)["expected"]
    assert sorted(x["number"] for x in listed) == ["W-1", "W-2", "W-3"]
    pick = next(x["settlement_id"] for x in listed if x["number"] == "W-3")
    assert act(c, OWNER, "feed_match", {"feed_id": row, "settlement_ids": [pick]}).status == "committed"
    assert _balance(c, "1000") - before[0] == -5_000 and FR.bill(c, OWNER, other)["status"] == "paid"


def test_a_bill_paid_by_money_account_waits_instead_of_taking_an_unclear_row(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        _paid_from_the_account_while_its_row_is_unclear(c, {"money_account_id": _checking(c)})


def test_a_bill_paid_on_the_accounts_line_waits_instead_of_taking_an_unclear_row(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        _paid_from_the_account_while_its_row_is_unclear(c, {"account_id": account(c, "1000")})


def test_a_payment_still_takes_its_unbooked_row_when_no_other_payment_waits_for_it(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        row = _ingest(c, _txn("txn-r", 300.0, who="Northwind", kind="externalTransfer"))
        assert _unbooked(c, row)
        customer = str(act(c, OWNER, "contact_create", {"name": "Northwind", "kind": "customer"}).result["contact_id"])
        inv = str(act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": "2026-10-01",
                                                   "lines": [{"account_id": account(c, "4000"), "amount": 30_000}]}
                      ).result["invoice_id"])
        assert act(c, OWNER, "invoice_send", {"invoice_id": inv}).status == "committed"
        done = act(c, OWNER, "invoice_record_payment", {"invoice_id": inv, "money_account_id": _checking(c),
                                                        "date": "2026-10-14"})
        assert (done.status, done.result["from"], done.result["feed_id"], done.result["status"]) == \
            ("committed", "feed", row, "paid"), done


def test_a_tico_card_charge_paying_an_entered_bill_clears_ap_instead_of_a_second_expense(books):  # noqa: F811
    from backend.tests.test_finance_banking import _arrives, _card_for_eve, _card_holds, _card_use
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
    with books.read() as c:
        assert FR.bill(c, OWNER, bid)["status"] == "paid"
        assert (_balance(c, "6100"), _balance(c, "2000"), _balance(c, "2100")) == (4_500, 0, -4_500)   # Codex 4
        assert _card_use(c, card_id, "2026-10") == 4_500 and _card_holds(c) == [(4_250, "settled")]


def test_one_receipt_settles_two_invoices_and_a_posted_rows_excess_moves_to_ap_unapplied(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}).result["contact_id"])
        invoices = []
        for amount in (10_000, 20_000):
            made = act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": "2026-10-01",
                                                    "lines": [{"account_id": account(c, "4000"), "amount": amount}]})
            assert act(c, OWNER, "invoice_send", {"invoice_id": made.result["invoice_id"]}).status == "committed"
            invoices.append(str(made.result["invoice_id"]))
        # Codex 8: an uncategorized 300.00 receipt applied to both (the first puts it all in AR).
        got = _ingest(c, _txn("txn-in", 300.0, who="Someone", kind="externalTransfer"))
        for iid in invoices:
            done = act(c, OWNER, "invoice_record_payment", {"invoice_id": iid, "feed_id": got})
            assert done.status == "committed" and done.result["status"] == "paid", done
        assert _balance(c, "1200") == 0
        # Grok 2: a 500.00 debit ingest left in Other expenses pays a 300.00 bill; the other 200.00 waits in AP for the
        # next bill. A row a person or a posting rule categorized keeps the rest where they put it.
        vendor = _vendor(c)
        first, second = _approved(c, vendor, 30_000, number="G-1"), _approved(c, vendor, 20_000, number="G-2")
        out = _ingest(c, _txn("txn-out", -500.0, who="Someone Else"))
        assert _balance(c, "6900") == 50_000
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": first, "feed_id": out}).status == "committed"
        assert (_balance(c, "6900"), _balance(c, "2000")) == (0, 20_000 - 20_000)
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": second, "feed_id": out}).status == "committed"
        assert (_balance(c, "6900"), _balance(c, "2000"), _balance(c, "6100")) == (0, 0, 50_000)
        assert FR.bill(c, OWNER, second)["status"] == "paid"
        by_hand = _ingest(c, _txn("txn-hand", -500.0, who="Someone Else"))
        assert act(c, OWNER, "categorize", {"feed_id": by_hand, "account_id": account(c, "6000")}).status == "committed"
        assert act(c, OWNER, "rule_create", {
            "kind": "posting", "name": "Tools", "when": {"counterparty": {"matches": "Toolshop"}},
            "outcome": {"outcome": "post", "account_id": account(c, "5000")},
            "examples": [{"input": {"counterparty": "Acme Toolshop"}, "expect": "post"}]}).status == "committed"
        by_rule = _ingest(c, _txn("txn-rule", -500.0, who="Acme Toolshop"))
        before = (_balance(c, "6000"), _balance(c, "5000"))
        for row in (by_hand, by_rule):
            bid = _approved(c, vendor, 30_000, number=f"H-{row[:4]}")
            assert act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "feed_id": row}).status == "committed"
        assert (_balance(c, "6000"), _balance(c, "5000")) == (before[0] - 30_000, before[1] - 30_000)
        assert _balance(c, "2000") == 0 and _balance(c, "6900") == 0


def test_a_scheduled_bill_is_matched_by_an_early_vendor_pull_and_pay_due_skips_it(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        bid = _approved(c, _vendor(c, recipient_id=rid), 12_000)
        assert act(c, OWNER, "bill_schedule", {"bill_id": bid, "pay_on": "2026-10-20",
                                               "money_account_id": _checking(c)}).status == "committed"
        _ingest(c, _txn("txn-pull", -120.0))                                            # Grok 1
        assert FR.bill(c, OWNER, bid)["status"] == "paid" and _balance(c, "6900") == 0
        FA.watch_documents(c, LATER)
        assert c.execute("SELECT count(*) FROM fin_payments").fetchone()[0] == 0


def test_a_revised_payment_row_settles_its_new_amount(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        bid = _approved(c, _vendor(c, recipient_id=rid), 50_000)
        assert act(c, OWNER, "bill_schedule", {"bill_id": bid, "pay_on": "2026-10-15",
                                               "money_account_id": _checking(c)}).status == "committed"
        FA.watch_documents(c, AT)
    sync_once(books, fake, HOURS[1])
    paid = _txn("txn-pay", -500.0, requestId="req-1", day="2026-10-16")
    with books.transaction() as c:
        _ingest(c, paid)
        assert FR.bill(c, OWNER, bid)["status"] == "paid"
        _ingest(c, {**paid, "amount": -400.0}, LATER)                                    # Codex 9
        bill = FR.bill(c, OWNER, bid)
        assert bill["remaining"] == 10_000 and bill["status"] != "paid"
        assert (_balance(c, "2000"), _balance(c, "6100")) == (-10_000, 50_000)
        assert _pnl(c, "cash").get("6100") == 40_000


def test_a_late_payment_for_a_closed_month_recognizes_cash_in_the_open_one(books):  # noqa: F811
    with books.transaction() as c:
        bid = _approved(c, _vendor(c), 7_000)
        assert act(c, OWNER, "close_period", {"month": "2026-10"}, at=NOV).status == "committed"
        done = act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "account_id": account(c, "1000"),
                                                "date": "2026-10-20"}, at=NOV)
        assert done.status == "committed", done
        assert _pnl(c, "cash", end="2026-10-31") == {}                                   # Codex 10
        assert _pnl(c, "cash", start="2026-11-01") == {"6100": 7_000}


def test_retagging_a_bill_line_moves_its_cash_attribution(books):  # noqa: F811
    def cash_by_mate(c):
        out: dict[str, int] = {}
        for table in ("fin_rollups", "fin_cash_rollups"):
            for mate, amount in c.execute(f"SELECT teammate, SUM(amount) FROM {table} WHERE account_id=? "
                                          "GROUP BY teammate", (account(c, "6100"),)):
                out[mate] = out.get(mate, 0) + int(amount)
        return {k: v for k, v in out.items() if v}

    with books.transaction() as c:
        bid = _approved(c, _vendor(c), 10_000, line={"tags": {"teammate": "human:eve"}})
        act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "account_id": account(c, "1000"), "amount": 4_000})
        entry = c.execute("SELECT entry_id FROM fin_bills WHERE id=?", (bid,)).fetchone()[0]
        line = next(r for r, x in SL.entry_lines(c, entry) if x.account_id == account(c, "6100"))
        done = act(c, OWNER, "categorize", {"mode": "replace", "lines": [
            {"line_id": str(line["id"]), "tags": {"teammate": "human:gus"}}]})
        assert done.status == "committed", done
        assert cash_by_mate(c) == {"human:gus": 4_000}                                  # Codex 11
        assert FR.bill(c, OWNER, bid)["lines"][0]["tags"]["teammate"] == "human:gus"
        act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "account_id": account(c, "1000")})
        assert cash_by_mate(c) == {"human:gus": 10_000}


def test_reads_cut_private_amounts_unseen_accounts_and_nested_lists(books):  # noqa: F811
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        pay = str(act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                                   "parent_id": account(c, "6000")}).result["account_id"])
        act(c, OWNER, "private_lines_set", {"account_id": pay, "others_see": "totals"})
        vendor = _vendor(c, "Acme Payroll")
        _approved(c, vendor, 10_000, code="6200")
        shown = _approved(c, vendor, 2_500, code="6100")
        for who, what in (("human:dan", "payables"), ("human:dan", "receivables")):
            act(c, OWNER, "view_grant", {"subject": who, "what": what})
        act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.ap"})
        act(c, OWNER, "right_grant", {"teammate": "human:cara", "right": "finance.book"})
        act(c, OWNER, "right_grant", {"teammate": "human:cara", "right": "finance.pay"})
        act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "expenses", "whose": "team", "whose_team": "web"})
        act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.book"})
        for n in range(2):
            act(c, OWNER, "attach", {"subject_kind": "bill", "subject_id": shown,
                                     "file_ids": [f"file-{n}-{i}" for i in range(30)]})
        act(c, OWNER, "attach", {"subject_kind": "unmatched", "file_ids": ["file-loose"]})
        rule = act(c, OWNER, "rule_create", {
            "kind": "posting", "name": "Hosting", "priority": 0, "when": {"counterparty": {"matches": "Cloud"}},
            "outcome": {"outcome": "post", "account_id": account(c, "6100")},
            "examples": [{"input": {"counterparty": "Acme Cloud"}, "expect": "post"}]})
        assert act(c, OWNER, "rule_update", {"rule_id": rule.result["rule_id"], "name": "Cloud"}).status == "committed"
        assert c.execute("SELECT priority FROM fin_rules WHERE id=?", (rule.result["rule_id"],)).fetchone()[0] == 0
        act(c, OWNER, "payment_settings", {"cash_below": 999_999_999})
        FA.watch_documents(c, AT)
    with books.read() as c:
        owed = {x["name"]: x["owed"]["bills"] for x in FR.contacts(c, "human:dan")["contacts"]}
        assert owed["Acme Payroll"] == 2_500                                             # Codex 2
        assert {x["name"]: x["owed"]["bills"] for x in FR.contacts(c, OWNER)["contacts"]}["Acme Payroll"] == 12_500
        bill = FR.bill(c, OWNER, shown)                                                  # Codex 14
        assert (len(bill["attachments"]), bill["attachment_count"], bill["attachments_more"]) == (50, 60, True)
        checklist = FR.close_checklist(c, "human:cara", "2026-09")                       # Codex 12
        keys = {x["key"] for x in checklist["checks"]}
        assert "unmatched_receipts" not in keys and "draft_bills" not in keys
        assert next(x for x in checklist["checks"] if x["key"] == "reconciled")["accounts"] == []
        assert not [i for i in FR.inbox(c, "human:cara")["items"] if i.get("file_id") == "file-loose"]
        assert FR.pay_from(c, "human:cara")["accounts"] == []                            # Grok 3
        assert len(FR.pay_from(c, OWNER)["accounts"]) == 2
        data = c.execute("SELECT payload FROM fin_events WHERE name='finance.cash.below'").fetchall()
        assert all("cash" not in json.loads(r[0]) for r in data)                         # Codex 7
        line = next(x for x in FR.chart(c, "bot:coder", for_="bill")["accounts"] if x["number"] == "6100")
        assert not line["company_wide"] and {"team": "web"} in line["attributable"]
        mine = [x for x in line["attributable"] if x == {"teammate": "bot:coder"}]
        assert mine and line["attributable"][0] == mine[0]                                # their own tag first
        assert all(x.get("attributable") is None for x in FR.chart(c, OWNER)["accounts"])
        report = FR.report(c, OWNER, "pnl", start="2026-10-01", end="2026-10-31", top_n=1)
        assert len([r for r in report["rows"] if r.get("key") != FR.OTHER]) == 1
        assert report["rows_total"] > 1 and any(str(r.get("label", "")).startswith("Other · ") for r in report["rows"])
