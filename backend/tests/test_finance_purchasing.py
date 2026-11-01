"""Purchase orders and estimates: an approved order holds its unbilled lines, a bill takes over the part it bills,
billing the rest closes the order, closing or voiding releases what is held, a large order asks the owner, and an
accepted estimate becomes one invoice draft once (a declined one never does)."""
import pytest

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import budgeting as BG
from backend.finance import payments as FP
from backend.finance import documents as DOC
from backend.finance import reads as FR
from backend.finance.context import load
from backend.finance.core import qbo as Q
from backend.finance.core.errors import NOT_FOUND, FinanceError
from backend.finance.store import ap as SAP
from backend.finance.store import banking as SK
from backend.records import store as RS
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, FakeMercury, account, act, books, budget, connect, finance_template, store)

EVE = "human:eve"
FAY = "human:fay"


def _vendor(c, name="Acme Supplies", **more) -> str:
    made = act(c, OWNER, "contact_create", {"name": name, "kind": "vendor", **more})
    assert made.status == "committed", made
    return str(made.result["contact_id"])


def _software(c, amount=100_000) -> None:
    made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": amount,
                                           "at_limit": "decline", "parent_id": None})
    assert made.status == "committed", made


def _line(c, amount, **more) -> dict[str, object]:
    return {"account_id": account(c, "6100"), "amount": amount, **more}


def _order(c, vendor, lines, who=OWNER, approve=True) -> str:
    made = act(c, who, "po_create", {"vendor_id": vendor, "po_date": "2026-10-10", "lines": lines})
    assert made.status == "committed", made
    po = str(made.result["po_id"])
    if approve:
        assert act(c, who, "po_send", {"po_id": po}).status == "committed"
        assert act(c, OWNER, "po_approve", {"po_id": po}).status == "committed"
    return po


def _po_lines(c, po) -> list[str]:
    return [str(r[0]) for r in c.execute("SELECT id FROM fin_po_lines WHERE po_id=? ORDER BY line_no", (po,))]


def _status(c, po) -> tuple[str, str]:
    row = c.execute("SELECT status, closed_by FROM fin_purchase_orders WHERE id=?", (po,)).fetchone()
    return str(row[0]), str(row[1])


def _holds(c, po) -> list[tuple[str, int]]:
    """The live holds an order's lines hold, by line, as (hold id, amount)."""
    return [(str(r[0]), int(r[1])) for r in c.execute(
        "SELECT id, amount FROM fin_holds WHERE source='purchase_order' AND status='held' AND source_id LIKE ? "
        "ORDER BY source_id", (f"{po}:%",))]


def _expense(c, code: str) -> str:
    made = act(c, OWNER, "account_create", {"code": code, "name": f"Expense {code}", "type": "expense",
                                            "parent_id": account(c, "6000")})
    assert made.status == "committed", made
    return str(made.result["account_id"])


def _state(c) -> tuple[int, int, int]:
    s = FA.budget_state(c, budget(c, "Software"), AT)
    return s.actual, s.held, s.remaining


def _bill(c, vendor, lines, who=OWNER):
    return act(c, who, "bill_create", {"vendor_id": vendor, "bill_date": "2026-10-10", "lines": lines})


def _bill_id(c) -> str:
    return str(c.execute("SELECT id FROM fin_bills ORDER BY created_at DESC LIMIT 1").fetchone()[0])


def test_an_approved_order_holds_its_lines_and_a_partial_bill_takes_over_its_part(books):
    with books.transaction() as c:
        _software(c)
        vendor = _vendor(c)
        draft = _order(c, vendor, [_line(c, 40_000, tags={"team": "eng"}), _line(c, 30_000)], approve=False)
        assert _state(c) == (0, 0, 100_000)                                   # a draft holds nothing
        po = _order(c, vendor, [_line(c, 40_000, tags={"team": "eng"}), _line(c, 30_000)])
        assert _state(c) == (0, 70_000, 30_000)
        first, _ = _po_lines(c, po)
        assert _bill(c, vendor, [{"po_line_id": first, "amount": 30_000}]).status == "committed"
        assert _status(c, po) == ("approved", "")                             # stored: the bill makes it partial
        assert FR.order(c, OWNER, po)["status"] == "partial"
        assert _state(c) == (0, 70_000, 30_000)                               # the bill's 30,000 + the order's 40,000
        assert act(c, OWNER, "bill_approve", {"bill_id": _bill_id(c)}).status == "committed"
        assert _state(c) == (30_000, 40_000, 30_000)
        again = act(c, OWNER, "po_approve", {"po_id": po})                    # approving again changes nothing
        assert again.status == "committed" and again.result["status"] == "partial"
        assert _state(c) == (30_000, 40_000, 30_000)
        assert c.execute("SELECT COUNT(*) FROM fin_holds WHERE source='purchase_order' AND status='held'"
                         ).fetchone()[0] == 2
        assert _status(c, draft) == ("draft", "")


def test_billing_the_rest_closes_the_order_and_voiding_the_bill_reopens_it(books):
    with books.transaction() as c:
        _software(c)
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 40_000, tags={"team": "eng"}), _line(c, 30_000)])
        first, second = _po_lines(c, po)
        bill = _bill(c, vendor, [{"po_line_id": first, "amount": 40_000}, {"po_line_id": second, "amount": 30_000}])
        assert bill.status == "committed", bill
        assert _status(c, po) == ("closed", "bills")
        assert _state(c) == (0, 70_000, 30_000)                               # only the bill holds now
        assert act(c, OWNER, "bill_void", {"bill_id": str(bill.result["bill_id"])}).status == "committed"
        assert _status(c, po) == ("approved", "")
        assert _state(c) == (0, 70_000, 30_000)                               # the order holds again


def test_a_bill_cannot_bill_more_than_is_left_or_another_vendors_or_a_draft_order(books):
    with books.transaction() as c:
        _software(c)
        vendor, other = _vendor(c), _vendor(c, "Other Supplies")
        po = _order(c, vendor, [_line(c, 40_000)])
        line = _po_lines(c, po)[0]
        draft = _order(c, vendor, [_line(c, 5_000)], approve=False)
        draft_line = _po_lines(c, draft)[0]
        over = _bill(c, vendor, [{"po_line_id": line, "amount": 40_001}])
        assert over.code == "invalid", over
        wrong = _bill(c, other, [{"po_line_id": line, "amount": 1_000}])
        assert wrong.code == "invalid", wrong
        assert _bill(c, vendor, [{"po_line_id": draft_line, "amount": 1_000}]).code == "conflict"
        assert _bill(c, vendor, [{"po_line_id": "no-such-line", "amount": 1_000}]).code == "not_found"
        assert c.execute("SELECT COUNT(*) FROM fin_bills").fetchone()[0] == 0
        assert _state(c) == (0, 40_000, 60_000)
        assert _bill(c, vendor, [{"po_line_id": line, "amount": 40_000}]).status == "committed"
        assert _status(c, po) == ("closed", "bills")


def test_closing_or_voiding_an_order_releases_what_it_holds(books):
    with books.transaction() as c:
        _software(c)
        vendor = _vendor(c)
        closed = _order(c, vendor, [_line(c, 40_000)])
        assert act(c, OWNER, "po_close", {"po_id": closed}).status == "committed"
        assert _status(c, closed) == ("closed", "hand")
        assert _state(c) == (0, 0, 100_000)
        assert act(c, OWNER, "po_approve", {"po_id": closed}).status == "committed"   # a closed order stays closed
        assert _status(c, closed) == ("closed", "hand") and _state(c) == (0, 0, 100_000)
        voided = _order(c, vendor, [_line(c, 20_000)])
        assert act(c, OWNER, "po_void", {"po_id": voided}).status == "committed"
        assert _status(c, voided) == ("void", "") and _state(c) == (0, 0, 100_000)
        partly = _order(c, vendor, [_line(c, 20_000), _line(c, 10_000)])
        first, _ = _po_lines(c, partly)
        assert _bill(c, vendor, [{"po_line_id": first, "amount": 10_000}]).status == "committed"
        assert act(c, OWNER, "po_void", {"po_id": partly}).code == "conflict"
        assert act(c, OWNER, "po_close", {"po_id": partly}).status == "committed"
        assert _status(c, partly) == ("closed", "hand")
        assert _state(c) == (0, 10_000, 90_000)                               # the bill's part only


def test_a_large_order_asks_the_owner_and_the_owner_approval_runs_it(books):
    with books.transaction() as c:
        _software(c, 500_000)
        vendor = _vendor(c)
        assert act(c, OWNER, "right_grant", {"teammate": EVE, "right": "finance.ap"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": EVE, "what": "everything", "whose": "company"}).status == \
            "committed"
        big = _order(c, vendor, [_line(c, 150_000)], who=EVE, approve=False)
        assert act(c, EVE, "po_send", {"po_id": big}).status == "committed"
        asked = act(c, EVE, "po_approve", {"po_id": big})
        assert (asked.status, asked.approver) == ("needs_approval", OWNER), asked
        assert _status(c, big) == ("sent", "") and _state(c) == (0, 0, 500_000)
        H.approval_decide(c, str(asked.approver), str(asked.approval_id), "approved")
        done = FA.approval_decided(c, str(asked.approval_id), AT)
        assert done is not None and done.status == "committed", done
        assert _status(c, big) == ("approved", "") and _state(c) == (0, 150_000, 350_000)
        small = _order(c, vendor, [_line(c, 50_000)], who=EVE)
        assert _status(c, small) == ("approved", "") and _state(c) == (0, 200_000, 300_000)


def _budget_at(c, amount, at_limit) -> None:
    made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": amount,
                                           "at_limit": at_limit, "parent_id": None})
    assert made.status == "committed", made


def test_an_order_past_a_warn_budget_is_held_and_warns(books):
    with books.transaction() as c:
        _budget_at(c, 50_000, "warn")
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 80_000)])
        assert _status(c, po) == ("approved", "") and _state(c) == (0, 80_000, -30_000)
        assert c.execute("SELECT COUNT(*) FROM fin_events WHERE name='finance.budget.warn'").fetchone()[0] == 1


def test_an_order_past_an_ask_budget_asks_a_finance_admin_and_holds_once_approved(books):
    with books.transaction() as c:
        _budget_at(c, 50_000, "ask")
        assert act(c, OWNER, "right_grant", {"teammate": EVE, "right": "finance.ap"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": EVE, "what": "everything", "whose": "company"}).status == \
            "committed"
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 80_000)], who=EVE, approve=False)
        assert act(c, EVE, "po_send", {"po_id": po}).status == "committed"
        asked = act(c, EVE, "po_approve", {"po_id": po})
        assert asked.status == "needs_approval", asked
        assert _status(c, po) == ("sent", "") and _state(c) == (0, 0, 50_000)
        H.approval_decide(c, str(asked.approver), str(asked.approval_id), "approved")
        done = FA.approval_decided(c, str(asked.approval_id), AT)
        assert done is not None and done.status == "committed", done
        assert _status(c, po) == ("approved", "") and _state(c) == (0, 80_000, -30_000)


def test_an_order_past_a_decline_budget_is_refused_with_the_reason(books):
    with books.transaction() as c:
        _budget_at(c, 50_000, "decline")
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 80_000)], approve=False)
        assert act(c, OWNER, "po_send", {"po_id": po}).status == "committed"
        refused = act(c, OWNER, "po_approve", {"po_id": po})
        assert refused.status == "denied" and "Software" in str(refused.reason), refused
        assert _status(c, po) == ("sent", "") and _state(c) == (0, 0, 50_000)


def test_an_order_past_a_hold_payment_budget_is_held_and_its_bill_waits_to_be_paid(books):
    connect(books, FakeMercury())
    with books.transaction() as c:
        _budget_at(c, 50_000, "hold_payment")
        money = str(c.execute("SELECT id FROM fin_money_accounts WHERE provider_account_id='acct-chk'").fetchone()[0])
        rid = str(act(c, OWNER, "recipient_add", {"name": "Acme Supplies", "method": "ach",
                                                  "account_number": "000099998888",
                                                  "routing_number": "000000000"}).result["recipient_id"])
        vendor = _vendor(c, recipient_id=rid)
        po = _order(c, vendor, [_line(c, 80_000)])
        assert _status(c, po) == ("approved", "") and _state(c) == (0, 80_000, -30_000)
        billed = _bill(c, vendor, [{"po_line_id": _po_lines(c, po)[0], "amount": 80_000}])
        assert billed.status == "committed", billed
        assert act(c, OWNER, "bill_approve", {"bill_id": billed.result["bill_id"]}).status == "committed"
        paid = act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 80_000, "money_account_id": money,
                                                "obligation": f"bill:{billed.result['bill_id']}",
                                                "account_id": account(c, "2000")})
        assert paid.status == "denied" and "waits until it fits" in str(paid.reason), paid


def test_a_queued_bill_payment_past_a_hold_payment_budget_waits_and_marking_the_bill_paid_is_refused(books):
    connect(books, FakeMercury())
    with books.transaction() as c:
        budget = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"),
                                                 "amount": 100_000, "at_limit": "hold_payment", "parent_id": None})
        assert budget.status == "committed", budget
        money = str(c.execute("SELECT id FROM fin_money_accounts WHERE provider_account_id='acct-chk'").fetchone()[0])
        rid = str(act(c, OWNER, "recipient_add", {"name": "Acme Supplies", "method": "ach",
                                                  "account_number": "000099998888",
                                                  "routing_number": "000000000"}).result["recipient_id"])
        vendor = _vendor(c, recipient_id=rid)
        po = _order(c, vendor, [_line(c, 80_000)])
        bill = str(_bill(c, vendor, [{"po_line_id": _po_lines(c, po)[0], "amount": 80_000}]).result["bill_id"])
        assert act(c, OWNER, "bill_approve", {"bill_id": bill}).status == "committed"
        queued = act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 80_000, "money_account_id": money,
                                                  "obligation": f"bill:{bill}", "account_id": account(c, "2000")})
        assert queued.status == "committed", queued
        pid = str(queued.result["payment_id"])
        assert act(c, OWNER, "budget_update", {"budget_id": budget.result["budget_id"],
                                               "amount": 50_000}).status == "committed"
        c.execute("UPDATE fin_recipients SET provider_recipient_id='rcp-acme' WHERE id=?", (rid,))
        # Queued at Mercury, the payment waits (no send, no approval asked of a person) until the budget fits.
        assert FP.recheck(load(c, AT), SK.get(c, "fin_payments", pid)) == "wait"
        assert SK.get(c, "fin_payments", pid)["status"] == "queued"
        paid = act(c, OWNER, "bill_mark_paid", {"bill_id": bill})
        assert paid.status == "denied" and "waits until it fits" in str(paid.reason), paid
        assert act(c, OWNER, "budget_update", {"budget_id": budget.result["budget_id"],
                                               "amount": 100_000}).status == "committed"
        assert FP.recheck(load(c, AT), SK.get(c, "fin_payments", pid)) == ""


def test_an_order_no_budget_covers_is_approved_and_reports_it_not_held(books):
    with books.transaction() as c:
        c.execute("UPDATE fin_budgets SET active=0")
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 40_000)], approve=False)
        assert act(c, OWNER, "po_send", {"po_id": po}).status == "committed"
        approved = act(c, OWNER, "po_approve", {"po_id": po})
        assert approved.status == "committed" and approved.result.get("not_held"), approved
        assert _status(c, po) == ("approved", "")


def test_an_estimate_becomes_one_invoice_draft_once_and_a_declined_one_never_does(books):
    with books.transaction() as c:
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}).result["contact_id"])
        made = act(c, OWNER, "estimate_create", {"customer_id": customer, "estimate_date": "2026-10-10", "lines": [
            {"account_id": account(c, "4000"), "amount": 90_000, "description": "Design"}]})
        assert made.status == "committed", made
        eid = str(made.result["estimate_id"])
        assert act(c, OWNER, "estimate_convert", {"estimate_id": eid}).code == "conflict"     # not accepted yet
        assert act(c, OWNER, "estimate_send", {"estimate_id": eid}).status == "committed"
        assert act(c, OWNER, "estimate_convert", {"estimate_id": eid}).code == "conflict"     # sent, not accepted
        assert act(c, OWNER, "estimate_accept", {"estimate_id": eid}).status == "committed"
        done = act(c, OWNER, "estimate_convert", {"estimate_id": eid})
        assert done.status == "committed", done
        invoice = str(done.result["invoice_id"])
        assert tuple(c.execute("SELECT status, invoice_id FROM fin_estimates WHERE id=?", (eid,)).fetchone()) == \
            ("accepted", invoice)
        assert FR.estimate(c, OWNER, eid)["status"] == "invoiced"
        assert act(c, OWNER, "estimate_convert", {"estimate_id": eid}).code == "conflict"     # once
        assert c.execute("SELECT COUNT(*) FROM fin_invoices").fetchone()[0] == 1
        assert [int(r[0]) for r in c.execute("SELECT amount FROM fin_invoice_lines")] == [90_000]

        declined = act(c, OWNER, "estimate_create", {"customer_id": customer, "estimate_date": "2026-10-10", "lines": [
            {"account_id": account(c, "4000"), "amount": 5_000}]}).result["estimate_id"]
        assert act(c, OWNER, "estimate_send", {"estimate_id": str(declined)}).status == "committed"
        assert act(c, OWNER, "estimate_decline", {"estimate_id": str(declined)}).status == "committed"
        refused = act(c, OWNER, "estimate_convert", {"estimate_id": str(declined)})
        assert refused.code == "conflict" and "declined" in refused.reason, refused
        assert c.execute("SELECT COUNT(*) FROM fin_invoices").fetchone()[0] == 1


def test_an_estimate_line_keeps_its_taxable_flag_onto_the_invoice(books):
    with books.transaction() as c:
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}).result["contact_id"])
        made = act(c, OWNER, "estimate_create", {"customer_id": customer, "estimate_date": "2026-10-10", "lines": [
            {"account_id": account(c, "4000"), "amount": 90_000, "taxable": True},
            {"account_id": account(c, "4000"), "amount": 10_000, "taxable": False}]})
        assert made.status == "committed", made
        eid = str(made.result["estimate_id"])
        assert [tuple(r) for r in c.execute("SELECT amount, taxable FROM fin_estimate_lines ORDER BY amount")] == \
            [(10_000, 0), (90_000, 1)]
        for kind in ("estimate_send", "estimate_accept"):
            assert act(c, OWNER, kind, {"estimate_id": eid}).status == "committed"
        assert act(c, OWNER, "estimate_convert", {"estimate_id": eid}).status == "committed"
        assert [tuple(r) for r in c.execute("SELECT amount, taxable FROM fin_invoice_lines ORDER BY amount")] == \
            [(10_000, 0), (90_000, 1)]


def test_orders_and_estimates_read_behind_the_finance_right_and_the_view(books):
    with books.transaction() as c:
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 40_000)])
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}).result["contact_id"])
        eid = str(act(c, OWNER, "estimate_create", {"customer_id": customer, "estimate_date": "2026-10-10", "lines": [
            {"account_id": account(c, "4000"), "amount": 9_000, "taxable": False}]}).result["estimate_id"])
        with pytest.raises(FinanceError) as refused:
            FR.orders(c, EVE)
        assert refused.value.code == "denied"
        for right in ("finance.ap", "finance.ar"):
            assert act(c, OWNER, "right_grant", {"teammate": EVE, "right": right}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": EVE, "what": "everything", "whose": "company"}).status == \
            "committed"
        assert [o["id"] for o in FR.orders(c, EVE, status="approved")["orders"]] == [po]
        assert FR.order(c, EVE, po)["lines"][0]["amount"] == 40_000
        assert [e["id"] for e in FR.estimates(c, EVE)["estimates"]] == [eid]
        assert FR.estimate(c, EVE, eid)["lines"][0]["taxable"] is False
        assert [r["id"] for r in FR.search(c, EVE, kind="po")["results"]] == [po]


def test_an_estimate_sent_by_a_teammate_asks_the_owner(books):
    with books.transaction() as c:
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}).result["contact_id"])
        eid = str(act(c, OWNER, "estimate_create", {"customer_id": customer, "estimate_date": "2026-10-10", "lines": [
            {"account_id": account(c, "4000"), "amount": 9_000}]}).result["estimate_id"])
        assert act(c, OWNER, "right_grant", {"teammate": EVE, "right": "finance.ar"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": EVE, "what": "everything", "whose": "company"}).status == \
            "committed"
        asked = act(c, EVE, "estimate_send", {"estimate_id": eid})
        assert (asked.status, asked.approver) == ("needs_approval", OWNER), asked
        assert c.execute("SELECT status FROM fin_estimates WHERE id=?", (eid,)).fetchone()[0] == "draft"


def test_a_qbo_invoice_made_from_an_estimate_links_to_it():
    body = {"day": "2026-10-06", "memo": "", "party": "Customer:4", "number": "", "due": "",
            "lines": [{"item": "Item:5", "amount": 1500, "description": "work"}]}
    assert Q.invoice_body({**body, "linked_estimate": "77"}, {})["LinkedTxn"] == [{"TxnId": "77", "TxnType": "Estimate"}]
    assert "LinkedTxn" not in Q.invoice_body(body, {})


def test_a_bill_on_an_order_line_posts_to_the_order_lines_account_and_refuses_another(books):
    with books.transaction() as c:
        _software(c)
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 40_000)])
        other = _expense(c, "6200")
        refused = _bill(c, vendor, [{"po_line_id": _po_lines(c, po)[0], "amount": 1_000, "account_id": other}])
        assert refused.code == "invalid" and "account" in str(refused.reason), refused
        assert c.execute("SELECT COUNT(*) FROM fin_bills").fetchone()[0] == 0


def test_an_order_whose_lines_pass_a_budget_together_is_refused_at_approval(books):
    with books.transaction() as c:
        _software(c, amount=50_000)
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 40_000), _line(c, 40_000)], approve=False)
        assert act(c, OWNER, "po_send", {"po_id": po}).status == "committed"
        refused = act(c, OWNER, "po_approve", {"po_id": po})
        assert refused.status == "denied" and "Software" in str(refused.reason), refused
        assert _status(c, po) == ("sent", "") and _state(c) == (0, 0, 50_000)


def test_a_partly_billed_order_keeps_the_hold_on_what_is_left(books):
    with books.transaction() as c:
        _software(c)
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 40_000), _line(c, 40_000)])
        before = _holds(c, po)
        assert _bill(c, vendor, [{"po_line_id": _po_lines(c, po)[0], "amount": 10_000}]).status == "committed"
        assert [h for h, _ in _holds(c, po)] == [h for h, _ in before]        # the same holds, shrunk
        assert [a for _, a in _holds(c, po)] == [30_000, 40_000]


def test_an_order_line_on_a_private_account_hides_the_order_from_a_teammate_who_sees_totals(books):
    with books.transaction() as c:
        payroll = _expense(c, "6200")
        assert act(c, OWNER, "private_lines_set", {"account_id": payroll, "others_see": "totals"}).status == \
            "committed"
        vendor = _vendor(c)
        po = _order(c, vendor, [{"account_id": payroll, "amount": 4_000}])
        assert act(c, OWNER, "right_grant", {"teammate": EVE, "right": "finance.ap"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": EVE, "what": "payables"}).status == "committed"
        assert FR.orders(c, EVE)["orders"] == []
        with pytest.raises(FinanceError) as hidden:
            FR.order(c, EVE, po)
        assert hidden.value.code == NOT_FOUND


def test_a_bill_past_a_hold_payment_budget_waits_even_when_a_stricter_budget_is_past_too(books):
    with books.transaction() as c:
        parent = act(c, OWNER, "budget_create", {"name": "Operations", "account_id": account(c, "6000"),
                                                 "amount": 100_000, "at_limit": "hold_payment", "parent_id": None})
        assert parent.status == "committed", parent
        child = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"),
                                                "amount": 100_000, "at_limit": "decline",
                                                "parent_id": parent.result["budget_id"]})
        assert child.status == "committed", child
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 80_000)])
        bill = str(_bill(c, vendor, [{"po_line_id": _po_lines(c, po)[0], "amount": 80_000}]).result["bill_id"])
        assert act(c, OWNER, "bill_approve", {"bill_id": bill}).status == "committed"
        for budget_id, name in ((child.result["budget_id"], "Software"), (parent.result["budget_id"], "Operations")):
            made = act(c, OWNER, "budget_update", {"budget_id": budget_id, "amount": 50_000})
            assert made.status == "committed", name
        assert "Operations" in DOC.payment_waits(load(c, AT), SAP.get(c, "fin_bills", bill))


def test_an_item_names_its_account_and_a_bill_line_cannot_post_elsewhere(books):
    with books.transaction() as c:
        vendor = _vendor(c)
        other = _expense(c, "6200")
        item = str(act(c, OWNER, "item_create", {"name": "Consulting", "kind": "service",
                                                 "expense_account_id": account(c, "6100")}).result["item_id"])
        refused = _bill(c, vendor, [{"item_id": item, "account_id": other, "amount": 1_000}])
        assert refused.code == "invalid" and "account" in str(refused.reason), refused
        assert c.execute("SELECT COUNT(*) FROM fin_bills").fetchone()[0] == 0


def test_the_owner_reads_orders_and_estimates_without_a_finance_grant(books):
    with books.transaction() as c:
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 40_000)])
        assert [o["id"] for o in FR.orders(c, OWNER)["orders"]] == [po]
        assert FR.order(c, OWNER, po)["lines"][0]["amount"] == 40_000


def test_orders_page_by_cursor_without_skipping_or_repeating(books):
    with books.transaction() as c:
        vendor = _vendor(c)
        pos = [_order(c, vendor, [_line(c, 1_000 + n)]) for n in range(3)]
        first = FR.orders(c, OWNER, limit=2)
        assert len(first["orders"]) == 2 and first["next_cursor"]
        second = FR.orders(c, OWNER, limit=2, cursor=first["next_cursor"])
        ids = [o["id"] for o in first["orders"]] + [o["id"] for o in second["orders"]]
        assert sorted(ids) == sorted(pos) and second["next_cursor"] is None


def test_estimates_page_by_cursor_without_skipping_or_repeating(books):
    with books.transaction() as c:
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind":
        "customer"}).result["contact_id"])
        made = []
        for n in range(3):
            done = act(c, OWNER, "estimate_create", {"customer_id": customer, "estimate_date": "2026-10-10", "lines": [
                {"account_id": account(c, "4000"), "amount": 1_000 + n, "description": "Design"}]})
            assert done.status == "committed", done.reason
            made.append(str(done.result["estimate_id"]))
        first = FR.estimates(c, OWNER, limit=2)
        assert len(first["estimates"]) == 2 and first["next_cursor"]
        second = FR.estimates(c, OWNER, limit=2, cursor=first["next_cursor"])
        ids = [e["id"] for e in first["estimates"]] + [e["id"] for e in second["estimates"]]
        assert sorted(ids) == sorted(made) and second["next_cursor"] is None


def test_estimate_search_matches_the_memo_and_the_customer_name(books):
    with books.transaction() as c:
        acme = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}).result["contact_id"])
        beta = str(act(c, OWNER, "contact_create", {"name": "Beta Client", "kind": "customer"}).result["contact_id"])
        kitchen = str(act(c, OWNER, "estimate_create", {"customer_id": acme, "estimate_date": "2026-10-10",
                                                        "memo": "Kitchen remodel", "lines": [
            {"account_id": account(c, "4000"), "amount": 90_000, "description": "Design"}]}).result["estimate_id"])
        deck = str(act(c, OWNER, "estimate_create", {"customer_id": beta, "estimate_date": "2026-10-10",
                                                     "memo": "Deck", "lines": [
            {"account_id": account(c, "4000"), "amount": 40_000, "description": "Build"}]}).result["estimate_id"])
        assert [r["id"] for r in FR.search(c, OWNER, kind="estimate", q="kitchen")["results"]] == [kitchen]
        assert [r["id"] for r in FR.search(c, OWNER, kind="estimate", q="beta")["results"]] == [deck]


def test_order_search_matches_the_memo_and_not_only_the_number(books):
    with books.transaction() as c:
        vendor = _vendor(c)
        lumber = str(act(c, OWNER, "po_create", {"vendor_id": vendor, "po_date": "2026-10-10",
                                                 "memo": "Lumber for the deck", "lines": [_line(c, 5_000)]}
                         ).result["po_id"])
        act(c, OWNER, "po_create", {"vendor_id": vendor, "po_date": "2026-10-10", "memo": "Seats",
                                    "lines": [_line(c, 6_000)]})
        assert [r["id"] for r in FR.search(c, OWNER, kind="po", q="lumber")["results"]] == [lumber]


def test_order_lines_that_each_fit_a_budget_are_refused_together_whatever_their_teammates(books):
    with books.transaction() as c:
        _software(c, amount=50_000)
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 40_000, tags={"teammate": "ana"}),
                                _line(c, 40_000, tags={"teammate": "ben"})], approve=False)
        assert act(c, OWNER, "po_send", {"po_id": po}).status == "committed"
        refused = act(c, OWNER, "po_approve", {"po_id": po})
        assert refused.status == "denied" and "Software" in str(refused.reason), refused
        assert _status(c, po) == ("sent", "") and _state(c) == (0, 0, 50_000)


def test_a_bill_on_an_order_line_takes_over_that_lines_hold_in_one_step(books):
    with books.transaction() as c:
        _budget_at(c, 50_000, "hold_payment")
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 40_000), _line(c, 40_000)])
        assert _state(c) == (0, 80_000, -30_000)
        assert _bill(c, vendor, [{"po_line_id": _po_lines(c, po)[0], "amount": 10_000}]).status == "committed"
        # The order's 30,000 and the bill's 10,000 are the same 40,000 the first line held: nothing is checked again.
        assert _state(c) == (0, 80_000, -30_000)
        assert [a for _, a in _holds(c, po)] == [30_000, 40_000]
        assert c.execute("SELECT COUNT(*) FROM fin_holds WHERE source='bill' AND status='held' AND amount=10000"
                         ).fetchone()[0] == 1


def test_a_bill_line_on_an_order_line_with_other_tags_is_refused_and_one_with_none_is_not(books):
    with books.transaction() as c:
        _software(c)
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 40_000, tags={"team": "eng"})])
        line = _po_lines(c, po)[0]
        other = _bill(c, vendor, [{"po_line_id": line, "amount": 10_000, "tags": {"team": "ops"}}])
        assert other.code == "invalid" and "tags" in str(other.reason), other
        assert _bill(c, vendor, [{"po_line_id": line, "amount": 10_000}]).status == "committed"


def test_voiding_a_draft_bill_gives_its_order_line_its_hold_back_even_once_the_budget_is_full(books):
    with books.transaction() as c:
        _budget_at(c, 10_000, "decline")
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 10_000)])
        bill = _bill(c, vendor, [{"po_line_id": _po_lines(c, po)[0], "amount": 10_000}])
        assert bill.status == "committed", bill
        assert _holds(c, po) == []                                              # the bill holds the line's budget
        other = _bill(c, vendor, [{"account_id": account(c, "6100"), "amount": 2_000}])
        assert other.status == "committed", other
        assert act(c, OWNER, "bill_approve", {"bill_id": str(other.result["bill_id"])}).status == "committed"
        assert act(c, OWNER, "bill_void", {"bill_id": str(bill.result["bill_id"])}).status == "committed"
        # The budget now has 2,000 spent, which a fresh check would refuse 10,000 on top of; the line's own hold
        # returns.
        assert _status(c, po) == ("approved", "")
        assert [amount for _, amount in _holds(c, po)] == [10_000]


def test_a_posted_bill_voided_gives_its_order_line_back_to_the_teammate_its_hold_was_made_for(books):
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 20_000,
                                               "at_limit": "decline", "parent_id": None})
        assert made.status == "committed", made
        budget = str(c.execute("SELECT id FROM fin_budgets WHERE name='Software'").fetchone()[0])
        assert act(c, OWNER, "allocation_set", {"budget_id": budget, "target_kind": "teammate", "target_id": EVE,
                                                "amount": 10_000}).status == "committed"
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 10_000, tags={"teammate": EVE})])
        bill = _bill(c, vendor, [{"po_line_id": _po_lines(c, po)[0], "amount": 10_000}])
        assert bill.status == "committed", bill
        bill_id = str(bill.result["bill_id"])
        assert act(c, OWNER, "bill_approve", {"bill_id": bill_id}).status == "committed"
        assert act(c, OWNER, "bill_void", {"bill_id": bill_id}).status == "committed"
        # The line's room comes back on the hold it was made for, not an untargeted one beside Eve's allocation.
        held = [tuple(r) for r in c.execute("SELECT amount, target FROM fin_holds WHERE source='purchase_order' "
                                            "AND status='held' AND source_id LIKE ?", (f"{po}:%",)).fetchall()]
        assert held == [(10_000, f"teammate:{EVE}")]
        state = BG.budget_state(c, budget)
        assert (state.actual, state.held, state.remaining) == (0, 10_000, 10_000)
        # A bill still in draft takes the line's hold over, and voiding it gives the hold back to the same teammate.
        draft = _bill(c, vendor, [{"po_line_id": _po_lines(c, po)[0], "amount": 10_000}])
        assert draft.status == "committed", draft
        assert act(c, OWNER, "bill_void", {"bill_id": str(draft.result["bill_id"])}).status == "committed"
        assert [tuple(r) for r in c.execute("SELECT amount, target FROM fin_holds WHERE source='purchase_order' "
                                            "AND status='held' AND source_id LIKE ?", (f"{po}:%",)).fetchall()] \
            == [(10_000, f"teammate:{EVE}")]


def test_a_bill_line_naming_an_item_by_an_alias_stores_the_item_and_posts_its_account(books):
    with books.transaction() as c:
        vendor = _vendor(c)
        made = act(c, OWNER, "item_create", {"name": "Seats", "kind": "service",
                                             "expense_account_id": account(c, "6100")})
        assert made.status == "committed", made
        item = str(made.result["item_id"])
        RS.alias(c, "items", "item-merged-away", item, AT)
        billed = _bill(c, vendor, [{"item_id": "item-merged-away", "amount": 2_000}])
        assert billed.status == "committed", billed
        row = c.execute("SELECT item_id, account_id FROM fin_bill_lines WHERE bill_id=?",
                        (billed.result["bill_id"],)).fetchone()
        assert (row[0], row[1]) == (item, account(c, "6100"))


def test_a_draft_bill_that_takes_an_order_line_moves_its_hold_once(books):
    with books.transaction() as c:
        _software(c, 200_000)
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 100_000)])
        assert _state(c) == (0, 100_000, 100_000)
        bill = _bill(c, vendor, [_line(c, 40_000)])
        assert bill.status == "committed", bill
        bid = str(bill.result["bill_id"])
        assert _state(c) == (0, 140_000, 60_000)
        linked = act(c, OWNER, "bill_update", {"bill_id": bid, "lines": [
            {"po_line_id": _po_lines(c, po)[0], "amount": 40_000}]})
        assert linked.status == "committed", linked
        # The order's 60,000 and the bill's 40,000 are the 100,000 the order held: the room moves once.
        assert _state(c) == (0, 100_000, 100_000)
        assert [a for _, a in _holds(c, po)] == [60_000]
        assert c.execute("SELECT amount FROM fin_holds WHERE source='bill' AND status='held'").fetchone()[0] == 40_000


def test_a_teammate_who_cannot_see_an_order_learns_nothing_from_approving_or_closing_it(books):
    with books.transaction() as c:
        _software(c)
        vendor = _vendor(c)
        approved = _order(c, vendor, [_line(c, 40_000)])
        closed = _order(c, vendor, [_line(c, 20_000)])
        assert act(c, OWNER, "po_close", {"po_id": closed}).status == "committed"
        assert act(c, OWNER, "right_grant", {"teammate": EVE, "right": "finance.ap"}).status == "committed"
        # Eve holds payables but no view of these lines: neither the order's state nor its number comes back.
        for kind, po, dry in (("po_approve", approved, False), ("po_approve", approved, True),
                              ("po_approve", closed, False), ("po_approve", closed, True),
                              ("po_send", approved, False), ("po_close", closed, False), ("po_void", closed, False)):
            made = act(c, EVE, kind, {"po_id": po}, dry_run=dry)
            assert made.status == "denied", (kind, dry, made)
            assert made.result == {}, (kind, made)
        assert _status(c, approved) == ("approved", "") and _status(c, closed) == ("closed", "hand")


def test_a_teammate_who_cannot_see_an_estimate_learns_nothing_from_its_state(books):
    with books.transaction() as c:
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}).result["contact_id"])

        def estimate(amount: int) -> str:
            made = act(c, OWNER, "estimate_create", {"customer_id": customer, "estimate_date": "2026-10-10", "lines": [
                {"account_id": account(c, "4000"), "amount": amount}]})
            assert made.status == "committed", made
            return str(made.result["estimate_id"])

        draft, sent, declined = estimate(9_000), estimate(8_000), estimate(7_000)
        assert act(c, OWNER, "estimate_send", {"estimate_id": sent}).status == "committed"
        assert act(c, OWNER, "estimate_send", {"estimate_id": declined}).status == "committed"
        assert act(c, OWNER, "estimate_decline", {"estimate_id": declined}).status == "committed"
        assert act(c, OWNER, "right_grant", {"teammate": EVE, "right": "finance.ar"}).status == "committed"
        for kind, eid in (("estimate_send", sent), ("estimate_accept", draft), ("estimate_convert", sent),
                          ("estimate_convert", declined), ("estimate_convert", draft)):
            made = act(c, EVE, kind, {"estimate_id": eid})
            assert made.status == "denied", (kind, made)
            assert made.result == {}, (kind, made)
        assert c.execute("SELECT COUNT(*) FROM fin_invoices").fetchone()[0] == 0


def test_a_teammate_without_the_detail_of_a_private_order_gets_not_found_from_every_order_action(books):
    with books.transaction() as c:
        payroll = _expense(c, "6200")
        assert act(c, OWNER, "private_lines_set", {"account_id": payroll, "others_see": "totals"}).status == \
            "committed"
        vendor = _vendor(c)
        draft = _order(c, vendor, [{"account_id": payroll, "amount": 4_000}], approve=False)
        approved = _order(c, vendor, [{"account_id": payroll, "amount": 5_000}])
        assert act(c, OWNER, "right_grant", {"teammate": EVE, "right": "finance.ap"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": EVE, "what": "payables"}).status == "committed"
        # Company expenses are in her view, so the payroll line passes the view check: only its private detail hides it.
        assert act(c, OWNER, "view_grant", {"subject": EVE, "what": "expenses", "whose": "company"}).status == \
            "committed"
        new_po = {"vendor_id": vendor, "po_date": "2026-10-10", "lines": [{"account_id": payroll, "amount": 1}]}
        for kind, payload, dry in (
                ("po_send", {"po_id": draft}, False), ("po_approve", {"po_id": draft}, True),
                ("po_approve", {"po_id": approved}, True), ("po_close", {"po_id": approved}, True),
                ("po_void", {"po_id": approved}, True), ("po_create", new_po, True)):
            made = act(c, EVE, kind, payload, dry_run=dry)
            assert (made.status, made.code, made.result) == ("failed", NOT_FOUND, {}), (kind, dry, made)
        # A bill that bills the hidden order's line is refused the same way, and the order's room is not taken.
        billed = act(c, EVE, "bill_create", {"vendor_id": vendor, "bill_date": "2026-10-10",
                                             "lines": [{"po_line_id": _po_lines(c, approved)[0], "amount": 1}]},
                     dry_run=True)
        assert (billed.status, billed.code, billed.result) == ("failed", NOT_FOUND, {}), billed
        assert _status(c, draft) == ("draft", "") and _status(c, approved) == ("approved", "")


def test_a_spend_within_the_room_set_aside_for_its_teammate_fits_the_budget_and_an_untargeted_one_does_not(books):
    with books.transaction() as c:
        _software(c, amount=100_000)
        assert act(c, OWNER, "allocation_set", {"budget_id": budget(c, "Software"), "target_kind": "teammate",
                                                "target_id": EVE, "amount": 100_000}).status == "committed"
        vendor = _vendor(c)
        untargeted = _order(c, vendor, [_line(c, 40_000)], approve=False)
        assert act(c, OWNER, "po_send", {"po_id": untargeted}).status == "committed"
        refused = act(c, OWNER, "po_approve", {"po_id": untargeted})
        assert refused.status == "denied" and "Software" in str(refused.reason), refused
        targeted = _order(c, vendor, [_line(c, 40_000, tags={"teammate": EVE})], approve=False)
        assert act(c, OWNER, "po_send", {"po_id": targeted}).status == "committed"
        approved = act(c, OWNER, "po_approve", {"po_id": targeted})
        assert approved.status == "committed", approved
        assert [a for _, a in _holds(c, targeted)] == [40_000]
        assert _status(c, targeted) == ("approved", "")


def test_teammates_each_within_their_own_set_aside_are_approved_together_when_the_order_fits_the_budget(books):
    with books.transaction() as c:
        _software(c, amount=100_000)
        for teammate in (EVE, FAY):
            assert act(c, OWNER, "allocation_set", {"budget_id": budget(c, "Software"), "target_kind": "teammate",
                                                    "target_id": teammate, "amount": 50_000}).status == "committed"
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 40_000, tags={"teammate": EVE}),
                                _line(c, 40_000, tags={"teammate": FAY})], approve=False)
        assert act(c, OWNER, "po_send", {"po_id": po}).status == "committed"
        approved = act(c, OWNER, "po_approve", {"po_id": po})
        assert approved.status == "committed", approved
        assert [a for _, a in _holds(c, po)] == [40_000, 40_000]
        assert _status(c, po) == ("approved", "")


def test_a_teammate_past_their_own_set_aside_is_refused_while_another_teammates_set_aside_is_reserved(books):
    with books.transaction() as c:
        _software(c, amount=100_000)
        for teammate in (EVE, FAY):
            assert act(c, OWNER, "allocation_set", {"budget_id": budget(c, "Software"), "target_kind": "teammate",
                                                    "target_id": teammate, "amount": 50_000}).status == "committed"
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 60_000, tags={"teammate": EVE}),
                                _line(c, 30_000, tags={"teammate": FAY})], approve=False)
        assert act(c, OWNER, "po_send", {"po_id": po}).status == "committed"
        refused = act(c, OWNER, "po_approve", {"po_id": po})
        assert refused.status == "denied" and "Software" in str(refused.reason), refused
        assert _status(c, po) == ("sent", "")


def test_an_order_whose_line_fits_only_because_a_teammates_set_aside_on_another_budget_is_left_out_is_refused(books):
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Expenses", "account_id": account(c, "6000"),
                                               "amount": 100_000, "at_limit": "decline", "parent_id": None})
        assert made.status == "committed", made
        parent = budget(c, "Expenses")
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"),
                                               "parent_id": parent, "at_limit": "decline"})
        assert made.status == "committed", made
        for teammate in (EVE, FAY):
            assert act(c, OWNER, "allocation_set", {"budget_id": budget(c, "Software"), "target_kind": "teammate",
                                                    "target_id": teammate, "amount": 40_000}).status == "committed"
        vendor = _vendor(c)
        po = _order(c, vendor, [_line(c, 60_000, tags={"teammate": EVE}),
                                {"account_id": _expense(c, "6200"), "amount": 20_000, "tags": {"teammate": FAY}}],
                    approve=False)
        assert act(c, OWNER, "po_send", {"po_id": po}).status == "committed"
        refused = act(c, OWNER, "po_approve", {"po_id": po})
        assert refused.status == "denied", refused
        assert _status(c, po) == ("sent", "") and _holds(c, po) == []


def test_a_teammates_bill_inside_their_set_aside_is_not_held_by_a_spend_that_overran_the_unallocated_room(books):
    with books.transaction() as c:
        _budget_at(c, 100_000, "warn")
        software = budget(c, "Software")
        assert act(c, OWNER, "allocation_set", {"budget_id": software, "target_kind": "teammate", "target_id": EVE,
                                                "amount": 50_000}).status == "committed"
        vendor = _vendor(c)
        untargeted = _bill(c, vendor, [_line(c, 55_000)])
        assert untargeted.status == "committed", untargeted
        mine = _bill(c, vendor, [_line(c, 40_000, tags={"teammate": EVE})])
        assert mine.status == "committed", mine
        assert act(c, OWNER, "budget_update", {"budget_id": software, "at_limit": "hold_payment"}).status == "committed"
        # Eve's bill is inside what was set aside for her; the untargeted bill took the room that was not set aside.
        assert DOC.bill_budget(load(c, AT), str(mine.result["bill_id"])).outcome == "allow"
        assert DOC.bill_budget(load(c, AT), str(untargeted.result["bill_id"])).outcome == "needs_approval"
