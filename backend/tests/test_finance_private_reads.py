"""The Finance tab's money reads keep private lines private and follow the current view: a payroll feed row never
shows its amount or payee to a teammate who sees the bank line but not payroll detail, a payment to a private line is
left out (of lists and of the balance sheet's due-out totals), and a payer whose grant was revoked sees only that their payment exists and its status."""
from backend.finance import reads as FR
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, account, act, books, connect, finance_template, store, sync_once)

SALARY = -812_345


def _payroll_line(c) -> str:
    made = act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                            "parent_id": account(c, "6000")})
    pay = str(made.result["account_id"])
    assert act(c, OWNER, "private_lines_set", {"account_id": pay, "others_see": "totals"}).status == "committed"
    return pay


def _checking(c) -> str:
    return str(c.execute("SELECT id FROM fin_money_accounts WHERE provider_account_id='acct-chk'").fetchone()[0])


def _payee(store, fake) -> str:
    with store.transaction() as c:
        made = act(c, OWNER, "recipient_add", {"name": "Eve Example", "method": "ach",
                                               "account_number": "000011112222", "routing_number": "000000000"})
        assert made.status == "committed", made
    sync_once(store, fake)
    return str(made.result["recipient_id"])


def test_a_payroll_feed_row_never_shows_to_a_bank_line_viewer_without_payroll_detail(books):
    fake = FakeMercury()
    connect(books, fake)
    gusto = {**MERCURY["txn_bank_fee"], "id": "txn-gusto", "amount": SALARY / 100, "counterpartyName": "Gusto",
             "bankDescription": "GUSTO PAYROLL EVE EXAMPLE", "kind": "externalTransfer"}
    for raw in (gusto, MERCURY["txn_deposit"]):
        fake.txns[str(raw["id"])] = dict(raw)
    sync_once(books, fake)
    with books.transaction() as c:
        pay = _payroll_line(c)
        feed = str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-gusto'").fetchone()[0])
        done = act(c, OWNER, "categorize", {"feed_id": feed, "account_id": pay, "tags": {"teammate": "human:eve"}})
        assert done.status == "committed", done
        act(c, OWNER, "view_grant", {"subject": "human:cara", "what": "bs_lines", "account_ids": [account(c, "1000")]})
        act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "everything"})

        def feed_rows(who):
            return [r for r in FR.transactions(c, who, at=AT)["rows"] if r["kind"] == "feed"]
        assert {r["amount"] for r in feed_rows(OWNER)} == {SALARY, 50_000}
        for who in ("human:cara", "human:dan"):                 # a bank line grant; everything but payroll detail
            rows = feed_rows(who)
            assert [r["amount"] for r in rows] == [50_000], who
            every = FR.transactions(c, who, at=AT)["rows"]           # feed rows and entries, earlier postings too
            assert "gusto" not in repr(every).lower() and all(abs(int(r["amount"])) != -SALARY for r in every)
        act(c, OWNER, "private_lines_set", {"account_id": pay, "others_see": "totals", "full_detail": ["human:dan"]})
        assert {r["amount"] for r in feed_rows("human:dan")} == {SALARY, 50_000}


def test_payments_hide_private_lines_and_a_revoked_payer_keeps_only_the_status(books):
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        pay = _payroll_line(c)
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.pay"})
        grant = act(c, OWNER, "view_grant", {"subject": "human:ben", "what": "expenses", "whose": "team",
                                             "whose_team": "sales"})
        made = act(c, "human:ben", "payment_create", {"recipient_id": rid, "amount": 9_000,
                                                      "account_id": account(c, "6100"), "tags": {"team": "sales"},
                                                      "money_account_id": _checking(c), "memo": "Acme parts"})
        assert made.status == "committed", made
        made = act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 812_345, "account_id": pay,
                                                "tags": {"teammate": "human:eve", "team": "web"},
                                                "money_account_id": _checking(c), "memo": "Eve salary"})
        assert made.status == "committed", made

        def payments(who):
            return FR.payments(c, who, at=AT)["payments"]
        assert sorted(p["amount"] for p in payments(OWNER)) == [9_000, 812_345]
        assert [p["amount"] for p in payments("human:cara")] == []          # leads Web, but no payroll detail
        assert [p["amount"] for p in payments("human:ben")] == [9_000]

        def due_out(who):                                   # N4: the balance sheet's due-out follows the same cut
            sheet = FR.balance_sheet(c, who, at=AT)
            return [s["due_out"] for a in sheet["assets"] for s in a["sources"] if s["money_account_id"] ==
                    _checking(c)][0]
        act(c, OWNER, "view_grant", {"subject": "human:cara", "what": "bs_lines", "account_ids": [account(c, "1000")]})
        act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "everything"})
        assert due_out(OWNER) == {"count": 2, "amount": 821_345}
        assert due_out("human:dan") == {"count": 1, "amount": 9_000}       # everything but payroll detail
        assert due_out("human:cara") == {"count": 0, "amount": 0}          # the bank line only
        act(c, OWNER, "view_revoke", {"grant_id": grant.result["grant_id"]})
        (left,) = payments("human:ben")
        assert left["status"] == "queued" and left["created_by"] == "human:ben"
        assert left["amount"] is None and left["recipient"] is None and left["account"] is None
        assert left["memo"] == "" and left["obligation"] is None and left["error"] is None
        assert set(left) == set(payments(OWNER)[0])


def test_a_cards_spent_total_is_not_shown_when_it_holds_a_private_line(books):
    """Codex round 6 #3: a 100.00 card charge re-categorized onto a private line, attributed to someone else. The
    holder's Transactions leave it out, so their Cards read shows no spent total either (it would give the amount
    away); the owner, with full detail, still sees it."""
    from backend.tests.test_finance_banking import LATER, _arrives, _card_for_eve, _txn
    fake = FakeMercury()
    connect(books, fake)
    _, card_id = _card_for_eve(books, fake)
    sync_once(books, fake)
    _arrives(fake, _txn("txn-c20", "credit-io", -100.0, "creditCardTransaction", cardId="card-eve"), 1, "evt-c20-1")
    sync_once(books, fake, LATER)

    def spent(c, who):
        return next(x["spent"] for x in FR.cards(c, who, at=AT)["cards"] if x["id"] == card_id)
    with books.transaction() as c:
        assert spent(c, "human:eve") == 10_000
        pay = _payroll_line(c)
        feed = str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-c20'").fetchone()[0])
        done = act(c, OWNER, "categorize", {"feed_id": feed, "account_id": pay, "tags": {"teammate": "human:fay"}})
        assert done.status == "committed", done
        assert [r for r in FR.transactions(c, "human:eve", at=AT)["rows"] if r.get("id") == feed] == []
        assert spent(c, "human:eve") is None
        assert spent(c, OWNER) == 10_000


NOV = "2026-11-05T12:00:00.000000Z"


def _october_debit_paid_by_a_november_payroll_bill(c) -> None:
    """A 40.00 debit posted to Other expenses in October; October closed in November; a private payroll bill dated
    November is paid from that row, so the "Pays bill" reclass posts in November. Gus sees the P&L, not payroll."""
    from backend.tests.test_finance_ap import _ingest, _txn
    made = act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                            "parent_id": account(c, "6000")})
    payroll = str(made.result["account_id"])
    assert act(c, OWNER, "private_lines_set", {"account_id": payroll, "others_see": "totals"}).status == "committed"
    _ingest(c, _txn("txn-pay", -40.0, who="ACME PAYROLL", day="2026-10-14"))
    assert act(c, OWNER, "close_period", {"month": "2026-10"}, at=NOV).status == "committed"
    vendor = act(c, OWNER, "contact_create", {"name": "Acme Payroll", "kind": "vendor"}, at=NOV)
    made = act(c, OWNER, "bill_create", {"vendor_id": str(vendor.result["contact_id"]), "bill_date": "2026-11-03",
                                         "lines": [{"account_id": payroll, "amount": 4_000}], "number": "PR-7"},
               at=NOV)
    assert made.status == "committed", made
    bid = str(made.result["bill_id"])
    assert act(c, OWNER, "bill_approve", {"bill_id": bid}, at=NOV).status == "committed"
    feed = str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-pay'").fetchone()[0])
    paid = act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "feed_id": feed}, at=NOV)
    assert paid.status == "committed" and paid.result["from"] == "feed", paid
    assert act(c, OWNER, "view_grant", {"subject": "human:gus", "what": "pnl"}, at=NOV).status == "committed"


def _other_months(c, who, basis: str = "accrual") -> list[int]:
    rows = FR.pnl(c, who, "2026-10-01", "2026-11-30", "month", basis, NOV)["rows"]
    return next(r["actual"] for r in rows if r["code"] == "6900")


def test_a_debit_moved_by_a_november_payroll_payment_leaves_no_amount_in_any_total(books):
    """Cross-month: October's 4,000 on Other expenses and November's reclass of it to a private payroll bill. A
    viewer without payroll detail sees no amount in the P&L, the line's total, its by-vendor breakdown, or the spend
    report, and the totals say they exclude payroll. The owner still sees both months."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        _october_debit_paid_by_a_november_payroll_bill(c)
    with books.read() as c:
        other = account(c, "6900")
        assert _other_months(c, OWNER) == [4_000, -4_000]
        assert _other_months(c, "human:gus") == [0, 0]
        assert _other_months(c, "human:gus", "cash") == [0, 0]
        pnl = FR.pnl(c, "human:gus", "2026-10-01", "2026-11-30", "month", "accrual", NOV)
        assert pnl["excluded"] is True
        detail = FR.line_detail(c, "human:gus", other, "2026-10-01", "2026-11-30", at=NOV)
        assert detail["total"] == 0 and detail["entries"] == [] and detail["excluded"] is True
        assert [r["amount"] for k in ("by_vendor", "by_team", "by_teammate") for r in detail[k]] == []
        spend = FR.spend_by(c, "human:gus", "vendor", account_id=other, start="2026-10-01", end="2026-11-30", at=NOV)
        assert spend["total"] == 0 and [r["amount"] for r in spend["rows"]] == []
        assert "Acme Payroll" not in repr(spend)
        mine = FR.line_detail(c, OWNER, other, "2026-10-01", "2026-11-30", at=NOV)
        assert mine["total"] == 0 and sorted(e["amount"] for e in mine["entries"]) == [-4_000, 4_000]


def test_reversing_the_pays_bill_entry_never_re_exposes_the_amount(books):
    """Reversing the November "Pays bill" entry voids its settlement, but the feed row and both entries stay private
    through the durable links (voided settlements, reverses_id), so no total, line or Transactions row shows 4,000,
    -4,000 or the reversal's 4,000 to a viewer without payroll detail."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        _october_debit_paid_by_a_november_payroll_bill(c)
        pays = str(c.execute("SELECT id FROM fin_entries WHERE memo='Pays bill PR-7'").fetchone()[0])
        assert act(c, OWNER, "journal", {"reverses": pays}, at=NOV).status == "committed"
    with books.read() as c:
        other = account(c, "6900")
        assert _other_months(c, "human:gus") == [0, 0]
        assert FR.line_detail(c, "human:gus", other, "2026-10-01", "2026-11-30", at=NOV)["total"] == 0
        rows = FR.transactions(c, "human:gus", start="2026-10-01", end="2026-11-30", source="entry")["rows"]
        assert [r["amount"] for r in rows if abs(r["amount"]) == 4_000] == []
        mine = FR.line_detail(c, OWNER, other, "2026-10-01", "2026-11-30", at=NOV)
        assert sorted(e["amount"] for e in mine["entries"]) == [-4_000, 4_000, 4_000]
