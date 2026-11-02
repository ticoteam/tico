"""The Finance tab's money reads keep private lines private and follow the current view: a payroll feed row never
shows its amount or payee to a teammate who sees the bank line but not payroll detail, a payment to a private line is
left out (of lists and of the balance sheet's due-out totals), and a payer whose grant was revoked sees only that their payment exists and its status."""
from datetime import date

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import budgeting, tab
from backend.finance import reads as FR
from backend.finance.store import actions as SX
from backend.finance.context import load
from backend.tests.test_finance_fixtures import (  # noqa: F401
    ACME_ADDRESS, AT, MERCURY, OWNER, FakeMercury, account, act, books, connect, finance_template, store, sync_once)

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
                                               "account_number": "000011112222", "routing_number": "000000000",
                                               "address": ACME_ADDRESS})
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
LATER = "2026-10-15T12:30:00.000000Z"
OCT_END = "2026-10-31T12:00:00.000000Z"


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


def _other_budget(c, at: str = NOV) -> str:
    made = act(c, OWNER, "budget_create", {"name": "Other expenses", "account_id": account(c, "6900"),
                                           "amount": 10_000}, at=at)
    assert made.status == "committed", made
    return str(made.result["budget_id"])


def test_a_budget_on_other_expenses_never_shows_a_payroll_payment_to_a_viewer_without_detail(books):
    """A budget on Other expenses counts the October debit and November's reclass of it to a private payroll bill. A
    holder without payroll detail sees neither in the actual, the owner sees both, and enforcement reads the true
    actual (only the read is suppressed)."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        _october_debit_paid_by_a_november_payroll_bill(c)
        budget_id = _other_budget(c)
        made = act(c, OWNER, "budget_create", {"name": "Cloud", "account_id": account(c, "6100"), "amount": 10_000},
                   at=NOV)
        assert made.status == "committed", made
    with books.read() as c:
        for who, months, excluded in ((OWNER, [4_000, -4_000], False), ("human:gus", [0, 0], True)):
            read = FR.budget_vs_actual(c, who, "2026-10-01", "2026-11-30", "month", at=NOV)
            (row,) = [r for r in read["budgets"] if r["id"] == budget_id]
            assert row["actual"] == months, who
            assert row["excluded"] is excluded and read["excluded"] is excluded, who
            (cloud_row,) = [r for r in read["budgets"] if r["name"] == "Cloud"]
            assert cloud_row["excluded"] is False, who
            (state,) = [r for r in FR.budgets(c, who, at=NOV)["budgets"] if r["id"] == budget_id]
            assert state["state"]["actual"] == months[1], who
        ctx = load(c, NOV)
        b = ctx.budgets()[budget_id]
        assert budgeting.actual(ctx, b, "2026-10-01", "2026-10-31") == 4_000
        assert budgeting.actual(ctx, b, "2026-11-01", "2026-11-30") == -4_000


def _state_on(c, who: str, budget_id: str, on: str) -> dict:
    (row,) = [r for r in FR.budgets(c, who, on=on, at=NOV)["budgets"] if r["id"] == budget_id]
    return row["state"]


def _card_charge_paid_by_a_november_payroll_bill(store, fake) -> str:
    """Other expenses, on which eve's card (a 5,000 allocation) charges 40.00 in October. Payroll bills 4,000 in
    November, paid from that charge's feed row, so the "Pays bill" reclass posts in November. Returns the budget id."""
    with store.transaction() as c:
        payroll = _payroll_line(c)
        made = act(c, OWNER, "budget_create", {"name": "Other expenses", "account_id": account(c, "6900"),
                                               "amount": 10_000})
        assert made.status == "committed", made
        budget_id = str(made.result["budget_id"])
        card = act(c, OWNER, "card_create", {"holder": "human:eve", "budget_id": budget_id, "amount": 5_000,
                                             "provider_user_id": "user-eve"})
        assert card.status == "committed", card
    sync_once(store, fake)                  # Mercury makes the card (card-eve); its allocation follows the sync
    fake.txns["txn-card-oct"] = {**MERCURY["txn_card_posted"], "id": "txn-card-oct", "amount": -40.0}
    fake.events_list.append({"id": "evt-card-oct", "resourceType": "transaction", "resourceId": "txn-card-oct",
                             "operationType": "create", "resourceVersion": 1})
    sync_once(store, fake, LATER)
    with store.transaction() as c:
        assert act(c, OWNER, "close_period", {"month": "2026-10"}, at=NOV).status == "committed"
        vendor = act(c, OWNER, "contact_create", {"name": "Acme Payroll", "kind": "vendor"}, at=NOV)
        made = act(c, OWNER, "bill_create", {"vendor_id": str(vendor.result["contact_id"]), "bill_date": "2026-11-03",
                                             "lines": [{"account_id": payroll, "amount": 4_000}], "number": "PR-7"},
                   at=NOV)
        assert made.status == "committed", made
        bid = str(made.result["bill_id"])
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}, at=NOV).status == "committed"
        feed = str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-card-oct'").fetchone()[0])
        paid = act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "feed_id": feed}, at=NOV)
        assert paid.status == "committed" and paid.result["from"] == "feed", paid
        assert act(c, OWNER, "view_grant", {"subject": "human:gus", "what": "pnl"}, at=NOV).status == "committed"
    return budget_id


def test_a_card_allocation_never_recovers_a_settled_payroll_charge_for_a_viewer_without_detail(books):
    """The card's October charge paid a private November bill. A holder without payroll detail sees the card's 5,000
    allocation as held in full (none of it is left over to recover the 4,000); the owner sees the true 1,000 held.
    Enforcement reads the true actual and reserve."""
    fake = FakeMercury()
    connect(books, fake)
    budget_id = _card_charge_paid_by_a_november_payroll_bill(books, fake)
    with books.read() as c:
        owner = _state_on(c, OWNER, budget_id, "2026-10-15")
        gus = _state_on(c, "human:gus", budget_id, "2026-10-15")
        assert (owner["actual"], owner["held"], owner["remaining"]) == (4_000, 1_000, 5_000)
        assert (gus["actual"], gus["held"], gus["remaining"]) == (0, 5_000, 5_000)
        assert owner["excluded"] is False and gus["excluded"] is True
        st = budgeting.state(load(c, NOV), budget_id, date(2026, 10, 15))
        assert (st.actual, st.held) == (4_000, 1_000)


def test_a_gateway_key_allocation_never_recovers_a_settled_payroll_charge_for_a_viewer_without_detail(books):
    """A run on the company's key holds 4,000 against Other expenses, settled to the October debit that a November
    payroll bill paid. The holder without payroll detail sees the whole 5,000 key allocation held; the owner sees 1,000."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        budget_id = _other_budget(c, at=AT)
        _october_debit_paid_by_a_november_payroll_bill(c)
        assert act(c, OWNER, "allocation_set", {"budget_id": budget_id, "target_kind": "gateway_key",
                                                "target_id": "gw-1", "amount": 5_000}, at=AT).status == "committed"
        entry = str(c.execute("SELECT entry_id FROM fin_feed_txns WHERE provider_id='txn-pay'").fetchone()[0])
        currency = load(c, AT).book.base_currency
        held = budgeting.hold_place(c, budget_scope=budget_id, amount=4_000, currency=currency, source="run",
                                    source_id="run-1", expires_at=None, target="gateway_key:gw-1", at=AT, by=OWNER)
        assert held.allowed and held.hold_id, held
        assert budgeting.hold_settle(c, str(held.hold_id), 4_000, entry, at=AT)
    with books.read() as c:
        owner = _state_on(c, OWNER, budget_id, "2026-10-15")
        gus = _state_on(c, "human:gus", budget_id, "2026-10-15")
        assert (owner["actual"], owner["held"], owner["remaining"]) == (4_000, 1_000, 5_000)
        assert (gus["actual"], gus["held"], gus["remaining"]) == (0, 5_000, 5_000)
        assert owner["excluded"] is False and gus["excluded"] is True


def test_an_allocation_refusal_states_a_budget_figure_only_when_the_viewer_sees_that_figure(books):
    """Other expenses has 6,000 left for the owner (the October debit counts) and 10,000 for a viewer without payroll
    detail. Setting aside 8,000 is refused on the owner's figure. The viewer's figures differ from the true ones, so
    their request goes to approval whatever the amount: the outcome never tells them the 6,000 they cannot check."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        budget_id = _other_budget(c, at=AT)
        _october_debit_paid_by_a_november_payroll_bill(c)
        ask = {"budget_id": budget_id, "target_kind": "gateway_key", "target_id": "gw-1", "amount": 8_000}
        owner = act(c, OWNER, "allocation_set", ask, at=AT)
        gus = act(c, "human:gus", "allocation_set", ask, at=AT)
    assert (owner.code, owner.reason) == ("invalid", "it does not fit: Other expenses has 6000 left")
    assert gus.status == "needs_approval", gus


def _card_charge_in_october_paid_by_a_november_payroll_bill(store, fake, holder: str, between=None) -> None:
    """Other expenses on a card held by `holder` (a 5,000 allocation) charges 40.00 on 14 October. The holder has the
    P&L grant from the start; in November a 4,000 payroll bill is paid from that charge's feed row, closing October
    first, so the "Pays bill" reclass posts in November. `between(store)` runs once the card exists, before its charge."""
    with store.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Other expenses", "account_id": account(c, "6900"),
                                               "amount": 10_000})
        assert made.status == "committed", made
        budget_id = str(made.result["budget_id"])
        card = act(c, OWNER, "card_create", {"holder": holder, "budget_id": budget_id, "amount": 5_000,
                                             "provider_user_id": "user-" + holder.split(":")[1]})
        assert card.status == "committed", card
        assert act(c, OWNER, "view_grant", {"subject": holder, "what": "pnl"}).status == "committed"
    sync_once(store, fake)                  # Mercury makes the card (card-eve); its allocation follows the sync
    if between is not None:
        between(store)
    fake.txns["txn-card-oct"] = {**MERCURY["txn_card_posted"], "id": "txn-card-oct", "amount": -40.0}
    fake.events_list.append({"id": "evt-card-oct", "resourceType": "transaction", "resourceId": "txn-card-oct",
                             "operationType": "create", "resourceVersion": 1})
    sync_once(store, fake, LATER)
    with store.transaction() as c:
        payroll = _payroll_line(c)
        assert act(c, OWNER, "close_period", {"month": "2026-10"}, at=NOV).status == "committed"
        vendor = act(c, OWNER, "contact_create", {"name": "Acme Payroll", "kind": "vendor"}, at=NOV)
        made = act(c, OWNER, "bill_create", {"vendor_id": str(vendor.result["contact_id"]), "bill_date": "2026-11-03",
                                             "lines": [{"account_id": payroll, "amount": 4_000}], "number": "PR-8"},
                   at=NOV)
        assert made.status == "committed", made
        bid = str(made.result["bill_id"])
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}, at=NOV).status == "committed"
        feed = str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-card-oct'").fetchone()[0])
        paid = act(c, OWNER, "bill_mark_paid", {"bill_id": bid, "feed_id": feed}, at=NOV)
        assert paid.status == "committed" and paid.result["from"] == "feed", paid


def test_a_cards_spent_never_counts_a_charge_that_paid_a_private_payroll_bill_for_a_viewer_without_detail(books):
    """The holder's October charge paid a private payroll bill in November, and the reclass is a bank entry, so the
    card's own lines show no private account. Read in October, the owner sees the 4,000 it spent; the holder, who has
    the P&L but no payroll detail, sees 0."""
    fake = FakeMercury()
    connect(books, fake)
    _card_charge_in_october_paid_by_a_november_payroll_bill(books, fake, "human:gus")
    with books.read() as c:
        owner = next(r for r in tab.cards(c, OWNER, at=OCT_END)["cards"] if r["holder"] == "human:gus")
        gus = next(r for r in tab.cards(c, "human:gus", at=OCT_END)["cards"] if r["holder"] == "human:gus")
    assert owner["spent"] == 4_000
    assert gus["spent"] == 0
    assert owner["excluded"] is False and gus["excluded"] is True


def test_a_refusal_read_by_id_names_the_budget_figure_only_where_the_reader_sees_that_figure(books):
    """The owner's refused set-aside stores its reason without the figure. Read by id, the owner sees the 6,000 that
    refused it; a P&L grantee without payroll detail sees only that it does not fit in Other expenses."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        budget_id = _other_budget(c, at=AT)
        _october_debit_paid_by_a_november_payroll_bill(c)
        refused = act(c, OWNER, "allocation_set", {"budget_id": budget_id, "target_kind": "gateway_key",
                                                   "target_id": "gw-1", "amount": 8_000}, at=AT)
        assert refused.code == "invalid" and refused.action_id, refused
    with books.read() as c:
        assert "6000" not in str(SX.by_id(c, refused.action_id)["reason"])
        assert FR.action_for(c, OWNER, refused.action_id)["reason"] == "it does not fit: Other expenses has 6000 left"
        assert FR.action_for(c, "human:gus", refused.action_id)["reason"] == "it does not fit in Other expenses"


def test_cash_flow_for_a_balance_sheet_grantee_is_the_bank_total_with_no_payroll_line(books):
    """The bank total is true for a balance-sheet grantee. Nothing isolates the payroll payment: it sits in the same
    net as the owner's, and the report has no line naming payroll or its payee."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        _october_debit_paid_by_a_november_payroll_bill(c)
        assert act(c, OWNER, "view_grant", {"subject": "human:gus", "what": "balance_sheet"},
                   at=NOV).status == "committed"
    with books.read() as c:
        window = {"start": "2026-10-01", "end": "2026-11-30", "period": "month", "at": NOV}
        mine = FR.report(c, "human:gus", "cash_flow", **window)
        theirs = FR.report(c, OWNER, "cash_flow", **window)
        assert theirs["net"] == [-4_000, 0]
        assert (mine["opening"], mine["net"], mine["closing"]) == (theirs["opening"], theirs["net"], theirs["closing"])
        assert mine["accounts"] == theirs["accounts"]
        assert set(mine) == set(theirs)
        assert "Payroll" not in repr(mine) and "Acme Payroll" not in repr(mine) and "PR-7" not in repr(mine)


def test_a_card_limit_refusal_names_the_card_figure_only_where_the_reader_sees_it(books):
    """The holder's October charge paid a private payroll bill in November, so the owner's figure for the card is 4,000
    and the holder's is 0. Lowering the limit below what the card used is refused with the figure only for the owner;
    the holder's limit change and allocation on the card both ask for approval, with no figure."""
    fake = FakeMercury()
    connect(books, fake)
    _card_charge_in_october_paid_by_a_november_payroll_bill(books, fake, "human:gus")
    with books.transaction() as c:
        card_id, budget_id = (str(x) for x in c.execute(
            "SELECT id, budget_id FROM fin_cards WHERE holder='human:gus'").fetchone())
        assert act(c, OWNER, "right_grant", {"teammate": "human:gus", "right": "finance.cards"}, at=OCT_END).status == \
            "committed"
        owner = act(c, OWNER, "card_update_limit", {"card_id": card_id, "amount": 1_000}, at=OCT_END)
        held = act(c, "human:gus", "card_update_limit", {"card_id": card_id, "amount": 1_000}, at=OCT_END)
        asked = act(c, "human:gus", "allocation_set", {"budget_id": budget_id, "target_kind": "card",
                                                       "target_id": card_id, "amount": 1_000}, at=OCT_END)
    assert "40.00" in owner.reason, owner
    assert held.status == "needs_approval" and "40.00" not in held.reason, held
    assert asked.status == "needs_approval" and "40.00" not in asked.reason, asked


def test_a_pnl_grantee_gets_one_outcome_for_every_set_aside_amount(books):
    """A P&L grantee without payroll detail dry-runs set-asides on a budget whose October debit a November payroll bill
    paid. Their figure is not the true one, so every amount gets the same outcome: the approver decides it on the true
    figures. The amounts either side of the true remaining must not tell the 4,000 apart."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        budget_id = _other_budget(c, at=AT)
        _october_debit_paid_by_a_november_payroll_bill(c)
        assert act(c, OWNER, "view_grant", {"subject": "human:gus", "what": "pnl"}, at=AT).status == "committed"
        outcomes = {amount: act(c, "human:gus", "allocation_set", {"budget_id": budget_id, "target_kind": "gateway_key",
                                                                   "target_id": "gw-1", "amount": amount},
                                f"dry-{amount}", dry_run=True, at=AT).status
                    for amount in (3_000, 9_000, 9_001, 12_000)}
    assert set(outcomes.values()) == {"needs_approval"}, outcomes


def test_a_card_limit_change_gets_one_outcome_for_every_amount_the_holder_cannot_check(books):
    """The holder's figure for the card differs from the true one (a settled payroll part is netted out of it). Every
    limit the holder asks for, below or above what the card used, asks the owner for approval, with no figure in it."""
    fake = FakeMercury()
    connect(books, fake)
    _card_charge_in_october_paid_by_a_november_payroll_bill(books, fake, "human:gus")
    with books.transaction() as c:
        card_id = str(c.execute("SELECT id FROM fin_cards WHERE holder='human:gus'").fetchone()[0])
        assert act(c, OWNER, "right_grant", {"teammate": "human:gus", "right": "finance.cards"}, at=OCT_END).status == \
            "committed"
        outcomes = {amount: act(c, "human:gus", "card_update_limit", {"card_id": card_id, "amount": amount},
                                f"limit-{amount}", dry_run=True, at=OCT_END)
                    for amount in (500, 3_999, 4_000, 4_001, 5_000, 9_000)}
    assert {r.status for r in outcomes.values()} == {"needs_approval"}, {a: r.status for a, r in outcomes.items()}
    assert not any(ch.isdigit() for r in outcomes.values() for ch in r.reason), outcomes


def test_a_card_creation_gets_one_outcome_for_every_amount_the_reader_cannot_check(books):
    """A P&L grantee without payroll detail (and without the budgets right) asks for a card on a budget whose October
    debit a November payroll bill paid. Their figure is not the true one, so every amount gets the same outcome."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        budget_id = _other_budget(c, at=AT)
        _october_debit_paid_by_a_november_payroll_bill(c)
        assert act(c, OWNER, "view_grant", {"subject": "human:gus", "what": "pnl"}, at=AT).status == "committed"
        assert act(c, OWNER, "right_grant", {"teammate": "human:gus", "right": "finance.cards"}, at=AT).status == \
            "committed"
        outcomes = {amount: act(c, "human:gus", "card_create", {"holder": "human:eve", "budget_id": budget_id,
                                                                "amount": amount, "provider_user_id": "user-eve"},
                                f"card-{amount}", dry_run=True, at=AT).status
                    for amount in (3_000, 6_000, 6_001, 8_000, 12_000)}
    assert set(outcomes.values()) == {"needs_approval"}, outcomes


def test_a_budget_change_gets_one_outcome_for_every_amount_the_reader_cannot_check(books):
    """A holder of the budgets right without payroll detail lowers or raises Other expenses. Their figure for the budget
    is not the true one, so every amount asks the owner for approval, and no amount tells them the true remaining."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        budget_id = _other_budget(c, at=AT)
        _october_debit_paid_by_a_november_payroll_bill(c)
        assert act(c, OWNER, "view_grant", {"subject": "human:gus", "what": "pnl"}, at=AT).status == "committed"
        assert act(c, OWNER, "right_grant", {"teammate": "human:gus", "right": "finance.budgets"}, at=AT).status == \
            "committed"
        outcomes = {amount: act(c, "human:gus", "budget_update", {"budget_id": budget_id, "amount": amount},
                                f"budget-{amount}", dry_run=True, at=AT)
                    for amount in (3_000, 6_000, 6_001, 7_000, 12_000)}
    assert {r.status for r in outcomes.values()} == {"needs_approval"}, {a: r.status for a, r in outcomes.items()}
    assert not any(ch.isdigit() for r in outcomes.values() for ch in r.reason), outcomes


def test_a_budget_lowered_below_its_cards_gets_one_outcome_for_a_reader_who_cannot_check_it(books):
    """Lowering a budget below what its cards reserve names the reserve only where the reader's own figure is the true
    one. A holder who lacks payroll detail (their figure nets a settled payroll part out) gets the same needs_approval for
    every amount, and no amount in the reason: the reserve check does not run up front for them."""
    fake = FakeMercury()
    connect(books, fake)
    _card_charge_in_october_paid_by_a_november_payroll_bill(books, fake, "human:gus")
    with books.transaction() as c:
        budget_id = str(c.execute("SELECT budget_id FROM fin_cards WHERE holder='human:gus'").fetchone()[0])
        assert act(c, OWNER, "right_grant", {"teammate": "human:gus", "right": "finance.budgets"},
                   at=NOV).status == "committed"
        outcomes = {amount: act(c, "human:gus", "budget_update", {"budget_id": budget_id, "amount": amount},
                                f"low-{amount}", dry_run=True, at=NOV)
                    for amount in (1_000, 3_000, 4_000, 6_000)}
    assert {r.status for r in outcomes.values()} == {"needs_approval"}, {a: r.status for a, r in outcomes.items()}
    assert not any(ch.isdigit() for r in outcomes.values() for ch in r.reason), outcomes


def test_a_card_limit_approval_that_no_longer_fits_stores_no_figure(books):
    """A teammate asks a 1,000 limit on a card they have not yet been charged on, and a rule asks the owner for approval.
    A 40.00 charge lands before the approval, so the recheck refuses. The refusal is stored with the card id and no
    figure; reads render the figure only for a reader whose own figure is the true one and who still sees the card (the
    owner, and the teammate once they have payroll detail and while the pnl view stands)."""
    fake = FakeMercury()
    connect(books, fake)
    asked = {}

    def ask_before_the_charge(store):
        with store.transaction() as c:
            assert act(c, OWNER, "right_grant", {"teammate": "human:cara", "right": "finance.cards"}, at=AT).status == \
                "committed"
            pnl = act(c, OWNER, "view_grant", {"subject": "human:cara", "what": "pnl"}, at=AT)
            assert pnl.status == "committed", pnl
            assert act(c, OWNER, "rule_create", {
                "kind": "gate", "name": "Card limits", "when": {"kind": "card_update_limit"},
                "outcome": {"outcome": "needs_approval", "who": "finance_admin"},
                "examples": [{"input": {"kind": "card_update_limit"}, "expect": "needs_approval"}]},
                at=AT).status == "committed"
            card_id = str(c.execute("SELECT id FROM fin_cards WHERE holder='human:gus'").fetchone()[0])
            held = act(c, "human:cara", "card_update_limit", {"card_id": card_id, "amount": 1_000}, "limit-ask", at=AT)
            assert (held.status, held.approver) == ("needs_approval", OWNER), held
            asked.update(card_id=card_id, held=held, grant=str(pnl.result["grant_id"]))
    _card_charge_in_october_paid_by_a_november_payroll_bill(books, fake, "human:gus", between=ask_before_the_charge)
    held = asked["held"]
    with books.transaction() as c:
        assert act(c, OWNER, "private_lines_set", {"account_id": account(c, "6200"), "others_see": "totals",
                                                   "full_detail": ["human:cara"]}, at=OCT_END).status == "committed"
        H.approval_decide(c, OWNER, held.approval_id, "approved")
        done = FA.approval_decided(c, held.approval_id, OCT_END)
        assert done.status in ("failed", "denied") and done.code == "invalid", done
        reason, result = c.execute("SELECT reason, result_json FROM fin_actions WHERE id=?",
                                   (str(held.action_id),)).fetchone()
        assert not any(ch.isdigit() for ch in str(reason)) and asked["card_id"] in str(result), (reason, result)
        for reader in (OWNER, "human:cara"):
            assert "40.00" in FR.action_for(c, reader, held.action_id, at=OCT_END)["reason"], reader
        # the teammate asked, then lost sight of the card: they get the sentence with no figure, the owner still the figure
        assert act(c, OWNER, "view_revoke", {"grant_id": asked["grant"]}, at=OCT_END).status == "committed"
        assert "40.00" not in FR.action_for(c, "human:cara", held.action_id, at=OCT_END)["reason"]
        assert "40.00" in FR.action_for(c, OWNER, held.action_id, at=OCT_END)["reason"]


def test_a_payroll_journal_entry_is_netted_from_a_budget_for_a_reader_without_payroll_detail(books):
    """Other expenses (10,000) has a private payroll line under it, and a 4,000 journal entry for one person (not a feed
    row). A reader with the P&L and finance.budgets, but no payroll detail, nets it out of their figure as the P&L does
    (spec §7.5): their figure is not the true 6,000, so every amount they ask for gets the same outcome."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        payroll = str(act(c, OWNER, "account_create", {"code": "6910", "name": "Payroll", "type": "expense",
                                                       "parent_id": account(c, "6900")}).result["account_id"])
        assert act(c, OWNER, "private_lines_set", {"account_id": payroll, "others_see": "totals"}).status == "committed"
        budget_id = _other_budget(c, at=AT)
        posted = act(c, OWNER, "journal", {"date": "2026-10-03", "memo": "Payroll", "lines": [
            {"account_id": payroll, "amount": 4_000, "tags": {"teammate": "human:eve"}},
            {"account_id": account(c, "1000"), "amount": -4_000}]}, at=AT)
        assert posted.status == "committed", posted
        assert act(c, OWNER, "view_grant", {"subject": "human:gus", "what": "pnl"}, at=AT).status == "committed"
        assert act(c, OWNER, "right_grant", {"teammate": "human:gus", "right": "finance.budgets"},
                   at=AT).status == "committed"
        ask = {"budget_id": budget_id, "target_kind": "gateway_key", "target_id": "gw-1"}
        owner = {amount: act(c, OWNER, "allocation_set", {**ask, "amount": amount}, at=AT, dry_run=True).status
                 for amount in (6_000, 6_001)}
        gus = [act(c, "human:gus", "allocation_set", {**ask, "amount": amount}, at=AT, dry_run=True)
               for amount in (6_000, 6_001)]
        true = budgeting.state(load(c, AT), budget_id, date(2026, 10, 15))
        gus_view = load(c, AT).view("human:gus")
        seen = budgeting.state(load(c, AT), budget_id, date(2026, 10, 15), read=budgeting.Netted(gus_view))
    assert owner == {6_000: "committed", 6_001: "failed"}, owner
    assert [g.status for g in gus] == ["needs_approval", "needs_approval"], gus
    assert gus[0].reason == gus[1].reason and not any(ch.isdigit() for ch in gus[0].reason or ""), gus
    assert (true.actual, seen.actual) == (4_000, 0), (true, seen)


def test_a_card_limit_refusal_keeps_a_private_charge_hidden_when_read_in_a_later_period(books):
    """The holder's October charge (40.00) sits on a private payroll line. A teammate without payroll detail asks for a
    lower card limit; the owner approves in October, and the recheck refuses it (the card has used 40.00). Read in
    November, when the card has nothing spent, the asker still gets no amount: the period the refusal was about decides
    what they may see, not today's period."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        payroll = _payroll_line(c)
        budget_id = _other_budget(c, at=AT)
        card = act(c, OWNER, "card_create", {"holder": "human:eve", "budget_id": budget_id, "amount": 5_000,
                                             "provider_user_id": "user-eve"}, at=AT)
        assert card.status == "committed", card
    sync_once(books, fake)
    fake.txns["txn-card-oct"] = {**MERCURY["txn_card_posted"], "id": "txn-card-oct", "amount": -40.0}
    fake.events_list.append({"id": "evt-card-oct", "resourceType": "transaction", "resourceId": "txn-card-oct",
                             "operationType": "create", "resourceVersion": 1})
    sync_once(books, fake, LATER)
    with books.transaction() as c:
        feed = str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-card-oct'").fetchone()[0])
        spent = act(c, OWNER, "categorize", {"feed_id": feed, "account_id": payroll,
                                             "tags": {"teammate": "human:eve"}}, at=LATER)
        assert spent.status == "committed", spent
        assert act(c, OWNER, "view_grant", {"subject": "human:gus", "what": "pnl"}, at=OCT_END).status == "committed"
        assert act(c, OWNER, "right_grant", {"teammate": "human:gus", "right": "finance.budgets"},
                   at=OCT_END).status == "committed"
        assert act(c, OWNER, "right_grant", {"teammate": "human:gus", "right": "finance.cards"},
                   at=OCT_END).status == "committed"
        card_id = str(c.execute("SELECT id FROM fin_cards WHERE holder='human:eve'").fetchone()[0])
        asked = act(c, "human:gus", "card_update_limit", {"card_id": card_id, "amount": 3_000}, at=OCT_END,
                    key="gus-limit")
        assert asked.status == "needs_approval" and asked.action_id and asked.approval_id, asked
        H.approval_decide(c, str(asked.approver), asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, OCT_END)
    assert done is not None and (done.status, done.code) == ("failed", "invalid"), done and done.reason
    with books.read() as c:
        assert "40.00" in FR.action_for(c, OWNER, asked.action_id)["reason"]
        later = FR.action_for(c, "human:gus", asked.action_id, at=NOV)["reason"]
    assert "40.00" not in later and "4000" not in later, later


def test_a_once_budget_over_a_private_payroll_account_reads_for_a_viewer_without_its_detail(books):
    """A once budget runs to 9999-12-31. Over a private payroll account, a viewer without that account's detail reads the
    budget list, so its netted figure expands the window to whole quarters: the window must stop at the last posting or
    today, and no quarter may be built past 9999-12-31."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        _payroll_line(c)
        made = act(c, OWNER, "budget_create", {"name": "Once", "account_id": account(c, "6000"), "amount": 10_000,
                                               "period": "once"}, at=AT)
        assert made.status == "committed", made
        assert act(c, OWNER, "view_grant", {"subject": "human:gus", "what": "pnl"}, at=AT).status == "committed"
        assert act(c, OWNER, "right_grant", {"teammate": "human:gus", "right": "finance.budgets"},
                   at=AT).status == "committed"
        rows = FR.budgets(c, "human:gus", at=AT)["budgets"]
        FR.budget_vs_actual(c, "human:gus", at=AT)
    assert [r["name"] for r in rows if r["name"] == "Once"] == ["Once"], rows


DEC = "2026-12-15T12:00:00.000000Z"


def _gateway_hold_on_the_october_debit(c, budget_id: str) -> None:
    """A 40.00 Gateway key hold on Other expenses, settled by the October debit (the feed row a private payroll bill
    paid). The owner's Gateway reserve for October holds it; a reader without payroll detail nets it out."""
    debit = str(c.execute("SELECT entry_id FROM fin_feed_txns WHERE provider_id='txn-pay'").fetchone()[0])
    placed = FA.hold_place(c, budget_scope=budget_id, amount=4_000, currency="USD", source="run",
                           source_id="gw-run", expires_at=None, target="gateway_key:gw-1", at=AT)
    assert placed.allowed, placed
    assert FA.hold_settle(c, str(placed.hold_id), 4_000, debit, at=AT)


def _gateway_allocation(c, budget_id: str, amount: int = 1) -> None:
    assert act(c, OWNER, "allocation_set", {"budget_id": budget_id, "target_kind": "gateway_key", "target_id": "gw-1",
                                            "amount": amount}, at=AT).status == "committed"


def _readers_grants(c, *rights: str) -> None:
    assert act(c, OWNER, "view_grant", {"subject": "human:gus", "what": "pnl"}, at=AT).status == "committed"
    for right in rights:
        assert act(c, OWNER, "right_grant", {"teammate": "human:gus", "right": right}, at=AT).status == "committed"


def test_a_child_budget_under_a_parent_with_a_hidden_figure_gets_one_outcome_for_every_amount(books):
    """A P&L grantee with the budgets right and no payroll detail creates a child of Other expenses. Their figure for the
    parent is not the true one (a settled private payroll part is netted out of it), so every amount gets the same
    outcome and no figure. The owner still gets the true fit: 6,000 fits, 6,001 does not, and the reserve is named."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        budget_id = _other_budget(c, at=AT)
        _october_debit_paid_by_a_november_payroll_bill(c)
        _gateway_allocation(c, budget_id)
        _gateway_hold_on_the_october_debit(c, budget_id)
        _readers_grants(c, "finance.budgets")

        def child(amount: int, who: str, key: str) -> FA.ActionResult:
            return act(c, who, "budget_create", {"name": "Sales meals", "account_id": account(c, "6900"), "team": "sales",
                                                 "parent_id": budget_id, "amount": amount}, key, dry_run=True, at=AT)
        owner = {amount: child(amount, OWNER, f"own-{amount}") for amount in (6_000, 6_001)}
        gus = {amount: child(amount, "human:gus", f"gus-{amount}") for amount in (3_000, 6_000, 6_001, 12_000)}
    assert owner[6_000].status == "committed", owner[6_000]
    assert owner[6_001].status == "failed" and "40.00" in owner[6_001].reason, owner[6_001]
    assert {r.status for r in gus.values()} == {"needs_approval"}, {a: r.status for a, r in gus.items()}
    assert not any(ch.isdigit() for r in gus.values() for ch in r.reason), gus


def test_a_retroactive_budget_change_gets_one_outcome_for_a_reader_whose_figure_for_that_period_is_not_true(books):
    """A P&L grantee with the budgets right changes Other expenses back to October, in December. Their December figure is
    true, but October's is not, and the fit reads October: every amount gets the owner's decision and no figure. The
    owner still gets October's true fit (its 40.00 reserve is named below 4,000)."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        budget_id = _other_budget(c, at=AT)
        _october_debit_paid_by_a_november_payroll_bill(c)
        _gateway_allocation(c, budget_id)
        _gateway_hold_on_the_october_debit(c, budget_id)
        _readers_grants(c, "finance.budgets")

        def change(amount: int, who: str, key: str) -> FA.ActionResult:
            return act(c, who, "budget_update", {"budget_id": budget_id, "amount": amount, "effective_from": "2026-10-01"},
                       key, dry_run=True, at=DEC)
        owner = {amount: change(amount, OWNER, f"own-{amount}") for amount in (3_999, 4_000)}
        gus = {amount: change(amount, "human:gus", f"gus-{amount}") for amount in (3_999, 4_000, 4_001, 9_000)}
    assert owner[3_999].status == "failed" and "40.00" in owner[3_999].reason, owner[3_999]
    assert owner[4_000].status == "committed", owner[4_000]
    assert {r.status for r in gus.values()} == {"needs_approval"}, {a: r.status for a, r in gus.items()}
    assert not any(ch.isdigit() for r in gus.values() for ch in r.reason), gus


def test_every_budget_and_card_fit_gives_a_reader_who_cannot_check_one_outcome_and_no_figure(books):
    """Each action that runs a budget or card fit (create, update, a set-aside, a card's creation and its limit) gives a
    P&L grantee with the budgets and cards rights, and no payroll detail, one outcome for every amount and no figure in
    the reason. The check runs over every period the fit reads, before any fit runs."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        budget_id = _other_budget(c, at=AT)
        _october_debit_paid_by_a_november_payroll_bill(c)
        _gateway_allocation(c, budget_id)
        _gateway_hold_on_the_october_debit(c, budget_id)
        assert act(c, OWNER, "card_create", {"holder": "human:gus", "budget_id": budget_id, "amount": 1_000,
                                             "provider_user_id": "user-gus"}, at=AT).status == "committed"
        card_id = str(c.execute("SELECT id FROM fin_cards WHERE holder='human:gus'").fetchone()[0])
        _readers_grants(c, "finance.budgets", "finance.cards")

        def walk(kind: str, body: dict[str, object], amounts: tuple[int, ...]) -> list[FA.ActionResult]:
            return [act(c, "human:gus", kind, {**body, "amount": amount}, f"{kind}-{amount}", dry_run=True, at=AT)
                    for amount in amounts]
        walks = {
            "budget_create": walk("budget_create", {"name": "Sales meals", "account_id": account(c, "6900"),
                                                    "team": "sales", "parent_id": budget_id}, (3_000, 6_000, 6_001, 12_000)),
            "budget_update": walk("budget_update", {"budget_id": budget_id}, (3_000, 6_000, 6_001, 12_000)),
            "allocation_set": walk("allocation_set", {"budget_id": budget_id, "target_kind": "gateway_key",
                                                      "target_id": "gw-1"}, (3_000, 9_000, 9_001, 12_000)),
            "card_create": walk("card_create", {"holder": "human:eve", "budget_id": budget_id,
                                                "provider_user_id": "user-eve"}, (3_000, 6_000, 6_001, 12_000)),
            "card_update_limit": walk("card_update_limit", {"card_id": card_id}, (500, 3_999, 4_000, 4_001, 9_000)),
        }
    for kind, results in walks.items():
        assert {r.status for r in results} == {"needs_approval"}, (kind, [(r.status, r.reason) for r in results])
        assert not any(ch.isdigit() for r in results for ch in r.reason), (kind, [r.reason for r in results])


def test_a_hidden_child_budget_is_judged_on_the_true_figures_when_its_owner_approves_it(books):
    """A P&L grantee's child asks are all sent to the owner with one outcome. Approval runs the fit on the true figures:
    6,001 does not fit the 40.00 reserve and fails at approval, and 6,000 fits and commits."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        budget_id = _other_budget(c, at=AT)
        _october_debit_paid_by_a_november_payroll_bill(c)
        _gateway_allocation(c, budget_id)
        _gateway_hold_on_the_october_debit(c, budget_id)
        _readers_grants(c, "finance.budgets")
        asked = {}
        for amount in (6_001, 6_000):
            asked[amount] = act(c, "human:gus", "budget_create", {"name": f"Sales {amount}",
                                                                  "account_id": account(c, "6900"), "team": "sales",
                                                                  "parent_id": budget_id, "amount": amount},
                                f"ask-{amount}", at=AT)
            assert (asked[amount].status, asked[amount].approver) == ("needs_approval", OWNER), asked[amount]
        H.approval_decide(c, OWNER, asked[6_001].approval_id, "approved")
        too_big = FA.approval_decided(c, asked[6_001].approval_id, AT)
        assert too_big.status in ("failed", "denied") and too_big.code == "invalid", too_big
        H.approval_decide(c, OWNER, asked[6_000].approval_id, "approved")
        fits = FA.approval_decided(c, asked[6_000].approval_id, AT)
        assert fits.status == "committed", fits
