"""Cards, payments and finance gate rules against a fake Mercury: a card's provider limit is its allocation and cards
racing for a budget never pass it; a decline budget at cutoff freezes its cards (and the watch-rule actions do it as
the rule's maker); a payment that times out is `unknown`, keeps its hold, blocks a second payment for the same bill
and is found again by its one key; rejected in Mercury releases the hold; settled posts once. Gate rules: strictest
wins, examples must pass, a bot's rule needs a finance admin, limit counters hold under concurrency, stale data asks.
The new reads, as a manager, a plain bot and a bot with grants."""
import json
import sqlite3
import threading

import pytest

from backend import hubdb as H
from backend import rules as TR
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance import context as FC
from backend.finance.store import tico as FT
from backend.finance.actions import FinanceError
from backend.finance.mercury import Answer
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, account, act, books, connect, finance_template, spend, store, sync_once)
from backend.tests.test_api import api, get, post  # noqa: F401

HOURS = [f"2026-10-15T{h:02d}:30:00.000000Z" for h in range(13, 24)]


def _budget_and_card(store, amount=30_000, card=20_000, holder="human:eve"):
    with store.transaction() as c:
        b = act(c, OWNER, "budget_create", {"name": "Cloud", "account_id": account(c, "6100"), "amount": amount,
                                            "at_limit": "decline"})
        made = act(c, OWNER, "card_create", {"holder": holder, "budget_id": b.result["budget_id"], "amount": card,
                                             "provider_user_id": "user-eve"})
        assert made.status == "committed", made
    return str(b.result["budget_id"]), str(made.result["card_id"])


def _card(c, card_id):
    return c.execute("SELECT status, limit_amount, provider_limit, sync_status FROM fin_cards WHERE id=?",
                     (card_id,)).fetchone()


def _race(store, n, work):
    """Run `work(c, i)` in n threads at once, each in its own write transaction; the results."""
    barrier, out = threading.Barrier(n), []

    def one(i):
        barrier.wait()
        with store.transaction() as c:
            out.append(work(c, i))
    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return out


def test_a_cards_limit_is_its_allocation_and_racing_cards_never_pass_the_budget(books):
    fake = FakeMercury()
    connect(books, fake)
    budget_id, eve = _budget_and_card(books)
    sync_once(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "card_update_limit", {"card_id": eve, "amount": 22_000}).status == "committed"
        assert act(c, OWNER, "card_update_limit", {"card_id": eve, "amount": 30_001}).code == "invalid"
    sync_once(books, fake, HOURS[0])
    assert fake.sent("card_limit") == [("card-eve", 22_000)]
    with books.read() as c:
        assert tuple(_card(c, eve)) == ("active", 22_000, 22_000, "ok")
    done = _race(books, 2, lambda c, i: act(c, OWNER, "card_create", {
        "holder": ("human:fay", "human:gus")[i], "budget_id": budget_id, "amount": 6_000, "provider_user_id": "u"}))
    assert sorted(r.status for r in done) == ["committed", "failed"]
    with books.read() as c:
        total = c.execute("SELECT sum(amount) FROM fin_allocations WHERE budget_id=?", (budget_id,)).fetchone()[0]
        assert total == 28_000 <= 30_000


def test_a_decline_budget_at_cutoff_freezes_its_cards_and_unfreezing_waits_for_room(books):
    fake = FakeMercury()
    connect(books, fake)
    budget_id, eve = _budget_and_card(books)
    sync_once(books, fake)
    with books.transaction() as c:
        spend(c, "6100", 30_000)
        FA.drain_events(c, AT)
        assert _card(c, eve)["status"] == "frozen"
        assert act(c, OWNER, "card_unfreeze", {"card_id": eve}).code == "denied"
        frozen_by = c.execute("SELECT actor FROM fin_actions WHERE kind='card_freeze'").fetchone()[0]
        assert frozen_by == "system:finance"
    sync_once(books, fake, HOURS[0])
    assert fake.sent("card_state") == [("card-eve", "freeze")]
    with books.transaction() as c:
        # 60.00 leaves room (30.00 spent, 20.00 reserved), so the cutoff freeze lifts with the raise (§8.2).
        assert act(c, OWNER, "budget_update", {"budget_id": budget_id, "amount": 60_000}).status == "committed"
        assert _card(c, eve)["status"] == "active"
        assert act(c, OWNER, "card_unfreeze", {"card_id": eve}).code == "conflict"
    sync_once(books, fake, HOURS[1])
    assert ("card-eve", "unfreeze") in fake.sent("card_state")


def test_a_card_holder_who_cannot_see_the_budget_is_refused_without_its_name(books):
    fake = FakeMercury()
    connect(books, fake)
    _budget_id, eve = _budget_and_card(books)
    sync_once(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": "human:eve", "right": "finance.cards"}).status == "committed"
        spend(c, "6100", 30_000)
        FA.drain_events(c, AT)
        refused = act(c, "human:eve", "card_unfreeze", {"card_id": eve})
    assert refused.code == "denied" and "Cloud" not in refused.reason, refused


def test_watch_rule_actions_run_as_the_rules_maker_once_per_event(books, monkeypatch):
    # The rule runner passes no `at`, so the action is planned and stamped on the clock (finance.context.now,
    # store.tico.now). The other fixtures pass AT, so pin both: the pause then lands in the period of AT that is read.
    monkeypatch.setattr(FC, "now", lambda: AT)
    monkeypatch.setattr(FT, "now", lambda: AT)
    fake = FakeMercury()
    connect(books, fake)
    budget_id, eve = _budget_and_card(books)
    freeze, pause, act_ = (TR.EVENT_RUNNERS[k] for k in ("finance_freeze_cards", "finance_pause_budget", "finance_act"))
    values = {"data": {"budget_id": budget_id, "event_id": "e1"}}
    with books.transaction() as c:
        first = freeze(c, "rule-1", OWNER, {}, values, event="finance.budget.warn", subject="e1", revision=None)
        again = freeze(c, "rule-1", OWNER, {}, values, event="finance.budget.warn", subject="e1", revision=None)
        assert first["ok"] and again["ok"] and _card(c, eve)["status"] == "frozen"
        assert c.execute("SELECT count(*) FROM fin_actions WHERE kind='card_freeze'").fetchone()[0] == 1
        by_bot = act_(c, "rule-2", "bot:coder", {"kind": "card_unfreeze", "payload": {"card_id": eve}}, values,
                      event="finance.budget.warn", subject="e1", revision=None)
        assert not by_bot["ok"] and _card(c, eve)["status"] == "frozen"         # a bot's rule has only the bot's rights
        paused = pause(c, "rule-3", OWNER, {}, values, event="finance.budget.warn", subject="e2", revision=None)
        assert paused["ok"]
        assert FA.budget_state(c, budget_id).amount == 0       # the pause runs now, so the budget is read now
        assert _card(c, eve)["status"] == "frozen"         # a pause freezes the budget's cards, keeping allocations
        rule_of = c.execute("SELECT on_behalf_of, actor FROM fin_actions WHERE kind='budget_update'").fetchone()
        assert tuple(rule_of)[1] == OWNER


def _payee(store, fake, *, at=AT):
    """Acme Supplies by ACH, synced to Mercury."""
    with store.transaction() as c:
        made = act(c, OWNER, "recipient_add", {"name": "Acme Supplies", "method": "ach",
                                               "account_number": "000099998888", "routing_number": "000000000"})
        assert made.status == "committed", made
    sync_once(store, fake, at)
    with store.read() as c:
        payload = c.execute("SELECT payload_json FROM fin_outbox WHERE op='recipient_create'").fetchone()[0]
        assert "000099998888" not in payload                                     # erased once Mercury has it
    return str(made.result["recipient_id"])


def _checking(c):
    return str(c.execute("SELECT id FROM fin_money_accounts WHERE provider_account_id='acct-chk'").fetchone()[0])


def _holds(c, source="payment"):
    return [tuple(r) for r in c.execute("SELECT amount, status FROM fin_holds WHERE source=?", (source,))]


def _hold_of(c, payment_id):
    """The payment's own hold (holds list in no fixed order: fin_holds_source sorts by the payment id)."""
    return tuple(c.execute("SELECT amount, status FROM fin_holds WHERE source='payment' AND source_id=?",
                           (payment_id,)).fetchone())


def _asks(c, title):
    return c.execute("SELECT count(*) FROM tasks WHERE title LIKE ?", (f"{title}%",)).fetchone()[0]


def test_an_unknown_payment_is_never_sent_again_and_is_found_by_memo_recipient_and_amount(books):
    """B: a timed-out request is never re-POSTed. It is found in Mercury's paged list only by its key in the memo with
    the same recipient and amount; missing for 30 minutes it asks a finance admin once, who may then cancel it."""
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    body = {"recipient_id": rid, "amount": 50_000, "account_id": None, "obligation": "bill-7", "tags": {"team": "eng"}}
    with books.transaction() as c:
        body["account_id"], body["money_account_id"] = account(c, "6100"), _checking(c)
        made = act(c, OWNER, "payment_create", body)
        assert made.status == "committed" and made.result["status"] == "queued", made
        pid = str(made.result["payment_id"])
        assert _holds(c) == [(50_000, "held")]
    fake.queue["request_send_money"] = [Answer(None, {})]                       # sent, but no answer came back
    sync_once(books, fake, HOURS[1])
    with books.transaction() as c:
        status, key = c.execute("SELECT status, idempotency_key FROM fin_payments WHERE id=?", (pid,)).fetchone()
        assert status == "unknown" and _holds(c) == [(50_000, "held")]
        assert act(c, OWNER, "payment_create", body).code == "conflict"         # one live payment per bill
        assert act(c, OWNER, "payment_cancel", {"payment_id": pid}).code == "conflict"   # not looked for long yet
    sent = {"recipientId": "rcp-1", "amount": 500.0, "note": f"tico:{key}", "externalMemo": f"tico:{key} Acme"}
    fake.land({**sent, "amount": 499.0}, "req-other-amount")                    # its key, another amount
    fake.land({**sent, "recipientId": "rcp-9"}, "req-other-payee")              # its key, another payee
    fake.page_size = 1
    sync_once(books, fake, HOURS[2])                                             # an hour on: not there
    sync_once(books, fake, HOURS[3])
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_payments WHERE id=?", (pid,)).fetchone()[0] == "unknown"
        assert _asks(c, "Check Mercury") == 1                                    # asked once, not every pass
    assert len(fake.sent("request_send_money")) == 1                             # never sent again
    fake.land(sent, "req-9")                                                     # it was there after all, page 3
    sync_once(books, fake, HOURS[4])
    assert len(fake.sent("request_send_money")) == 1
    assert any(after for _, after in fake.sent("send_requests"))                # it read past the first page
    with books.read() as c:
        assert c.execute("SELECT status, provider_request_id FROM fin_payments WHERE id=?", (pid,)).fetchone()[:] == \
            ("awaiting_bank_approval", "req-9")
    assert all(str(b["externalMemo"]).startswith("tico:pay-") for b in fake.sent("request_send_money"))
    fake.requests["req-9"] = {**fake.requests["req-9"], "status": "rejected"}
    sync_once(books, fake, HOURS[6])
    with books.transaction() as c:
        assert c.execute("SELECT status FROM fin_payments WHERE id=?", (pid,)).fetchone()[0] == "cancelled"
        assert _holds(c) == [(50_000, "released")]
        again = act(c, OWNER, "payment_create", body)                             # the bill may be paid again
        assert again.status == "committed"
    fake.queue["request_send_money"] = [Answer(502, {})]
    sync_once(books, fake, HOURS[7])
    sync_once(books, fake, HOURS[9])                                             # not found after two hours
    with books.transaction() as c:
        assert act(c, "human:ben", "payment_cancel", {"payment_id": again.result["payment_id"]}).status != "committed"
        done = act(c, OWNER, "payment_cancel", {"payment_id": again.result["payment_id"]})
        assert done.status == "committed", done                                  # a finance admin, once asked
        assert _hold_of(c, again.result["payment_id"]) == (50_000, "held")      # ...and Mercury's lookup decides
    sync_once(books, fake, HOURS[10])
    with books.transaction() as c:
        assert c.execute("SELECT status FROM fin_payments WHERE id=?",
                         (again.result["payment_id"],)).fetchone()[0] == "cancelled"
        assert _hold_of(c, again.result["payment_id"]) == (50_000, "released")
    assert len(fake.sent("request_send_money")) == 2


def test_a_queued_payment_is_cancelled_here_and_one_in_mercury_ends_cancelled_when_rejected_there(books):
    """Item 2 (spec §9): payment_cancel works while a payment is queued, and ends it at once with its hold released. Once
    it is in Mercury's approval queue there is no cancel API: the cancel is refused, and a rejection in Mercury ends it
    cancelled on the poll, releasing its hold."""
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)

    def body(c, obligation):
        return {"recipient_id": rid, "amount": 20_000, "obligation": obligation, "tags": {"team": "eng"},
                "account_id": account(c, "6100"), "money_account_id": _checking(c)}
    with books.transaction() as c:
        queued = act(c, OWNER, "payment_create", body(c, "bill-20"))
        assert queued.status == "committed" and queued.result["status"] == "queued", queued
        assert act(c, OWNER, "payment_cancel", {"payment_id": queued.result["payment_id"]}).status == "committed"
        assert _hold_of(c, queued.result["payment_id"]) == (20_000, "released")
    with books.transaction() as c:
        sent = act(c, OWNER, "payment_create", body(c, "bill-21"))
        assert sent.status == "committed", sent
        pid = str(sent.result["payment_id"])
    sync_once(books, fake, HOURS[1])
    with books.transaction() as c:
        assert c.execute("SELECT status FROM fin_payments WHERE id=?", (pid,)).fetchone()[0] == \
            "awaiting_bank_approval"
        refused = act(c, OWNER, "payment_cancel", {"payment_id": pid})
        assert refused.code == "conflict" and "reject it in Mercury" in refused.reason, refused
        assert _hold_of(c, pid) == (20_000, "held")
    fake.requests[next(iter(fake.requests))]["status"] = "rejected"              # rejected in Mercury's queue
    sync_once(books, fake, HOURS[2])
    with books.transaction() as c:
        assert c.execute("SELECT status FROM fin_payments WHERE id=?", (pid,)).fetchone()[0] == "cancelled"
        assert _hold_of(c, pid) == (20_000, "released")


def _replace_payee(c, rid):
    """The payee's bank details change: a new recipient version replaces the old one (recipient_add with `replaces`)."""
    made = act(c, OWNER, "recipient_add", {"replaces": rid, "name": "Acme Supplies", "method": "ach",
                                           "account_number": "000011112222", "routing_number": "000000000"})
    assert made.status == "committed", made
    return str(made.result["recipient_id"])


def test_a_pending_approval_is_void_once_its_recipient_details_change(books):
    """Item 4 (spec §5): an approval's payload hash includes the recipient-details version. Changed details between the
    ask and the decision void it: the payment is asked again for the new details, and is not paid on the old approval."""
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.pay"})
        _rule(c, OWNER, "Payments ask", {"kind": "payment_create"}, {"outcome": "needs_approval", "who": "owner"},
              [{"input": {"kind": "payment_create"}, "expect": "needs_approval"}])
        asked = act(c, "human:ben", "payment_create", {"recipient_id": rid, "amount": 20_000, "obligation": "bill-41",
                                                 "tags": {"team": "eng"}, "account_id": account(c, "6100"),
                                                 "money_account_id": _checking(c)})
        assert asked.status == "needs_approval" and asked.approval_id, asked
        first = str(asked.approval_id)
        _replace_payee(c, rid)
        H.approval_decide(c, OWNER, first, "approved")
        again = FA.approval_decided(c, first, AT)
        assert again.status == "needs_approval" and again.approval_id and again.approval_id != first, again
        assert c.execute("SELECT count(*) FROM fin_payments").fetchone()[0] == 0


def _unknown_and_flagged(books, fake, obligation):
    """A payment whose send got no answer, looked for by its key for an hour: flagged for a finance admin."""
    rid = _payee(books, fake)
    body = {"recipient_id": rid, "amount": 50_000, "obligation": obligation, "tags": {"team": "eng"}}
    with books.transaction() as c:
        body["account_id"], body["money_account_id"] = account(c, "6100"), _checking(c)
        made = act(c, OWNER, "payment_create", body)
        assert made.status == "committed", made
        pid = str(made.result["payment_id"])
    fake.queue["request_send_money"] = [Answer(None, {})]
    sync_once(books, fake, HOURS[1])
    sync_once(books, fake, HOURS[2])
    with books.read() as c:
        key, flagged = c.execute("SELECT idempotency_key, flagged_at FROM fin_payments WHERE id=?", (pid,)).fetchone()
    assert flagged, "the admin is asked once the key is not found for 30 minutes"
    return pid, key


def test_an_admin_cancel_of_an_unknown_payment_is_not_a_cancel_until_a_fresh_key_lookup_finds_nothing(books):
    """Item 1 (spec §9): an unknown payment is resolved by polling by its idempotency key, never retried under a fresh
    key. A finance admin's cancel of a flagged one asks the worker to look Mercury up by the key now; by itself it does
    not cancel. A request carrying the key that turns up instead keeps the payment going, and it is not sent again."""
    fake = FakeMercury()
    connect(books, fake)
    pid, key = _unknown_and_flagged(books, fake, "bill-8")
    lookups = len(fake.sent("send_requests"))
    with books.transaction() as c:
        asked = act(c, OWNER, "payment_cancel", {"payment_id": pid})
        assert asked.status == "committed", asked
        assert c.execute("SELECT status FROM fin_payments WHERE id=?", (pid,)).fetchone()[0] == "unknown"
        assert _holds(c) == [(50_000, "held")]
    assert len(fake.sent("send_requests")) == lookups                            # no lookup happened in the click
    fake.land({"recipientId": "rcp-1", "amount": 500.0, "note": f"tico:{key}", "externalMemo": f"tico:{key} Acme"},
              "req-late")
    sync_once(books, fake, HOURS[3])                                             # the lookup finds it after all
    with books.read() as c:
        assert c.execute("SELECT status, provider_request_id FROM fin_payments WHERE id=?",
                         (pid,)).fetchone()[:] == ("awaiting_bank_approval", "req-late")
    assert len(fake.sent("request_send_money")) == 1                             # never sent again under a fresh key


def test_an_admin_cancel_of_an_unknown_payment_ends_it_when_the_fresh_lookup_finds_no_request_with_its_key(books):
    """Item 1: the cancel goes through once a complete lookup by the key, made after the cancel, finds nothing."""
    fake = FakeMercury()
    connect(books, fake)
    pid, _ = _unknown_and_flagged(books, fake, "bill-9")
    with books.transaction() as c:
        assert act(c, OWNER, "payment_cancel", {"payment_id": pid}).status == "committed"
    sync_once(books, fake, HOURS[3])
    with books.transaction() as c:
        assert c.execute("SELECT status FROM fin_payments WHERE id=?", (pid,)).fetchone()[0] == "cancelled"
        assert _holds(c) == [(50_000, "released")]
    assert len(fake.sent("request_send_money")) == 1


def test_a_409_with_the_request_is_accepted_and_a_cut_off_send_is_unknown(books):
    """B: a 409 that carries the request stores its id and status. A: a payment claimed and never recorded (the
    worker stopped mid-call) becomes unknown, never sent again; a cancel can't take a claimed payment back."""
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 10_000, "account_id": account(c, "6100"),
                                         "money_account_id": _checking(c)})
        two = act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 20_000, "account_id": account(c, "6100"),
                                               "money_account_id": _checking(c)})
    fake.queue["request_send_money"] = [Answer(409, {"requestId": "req-dup", "status": "pendingApproval"}),
                                        Answer(None, {})]
    with books.transaction() as c:
        # The second is claimed by a worker that then stops before recording anything.
        from backend.finance.store import banking as SK
        assert SK.payment_claim(c, str(two.result["payment_id"]), HOURS[0], HOURS[0])
        assert act(c, OWNER, "payment_cancel", {"payment_id": two.result["payment_id"]}).code == "conflict"
    sync_once(books, fake, HOURS[1])
    with books.read() as c:
        rows = dict(c.execute("SELECT amount, status || ':' || coalesce(provider_request_id, '') FROM fin_payments"))
    assert rows == {10_000: "awaiting_bank_approval:req-dup", 20_000: "unknown:"}
    assert [b["amount"] for b in fake.sent("request_send_money")] == [100]       # the cut-off one is not resent


def test_a_payment_settles_once_when_its_bank_transaction_posts_and_direct_send_is_refused_while_off(books):
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        made = act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 50_000,
                                                "account_id": account(c, "6100"), "tags": {"team": "eng"},
                                                "money_account_id": _checking(c)})
        pid = str(made.result["payment_id"])
        direct = act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 1, "account_id": account(c, "6100"),
                                                  "mode": "direct", "money_account_id": _checking(c)})
        assert direct.code == "not_available" and "off" in direct.reason
        assert act(c, "human:ben", "payment_settings", {"direct_send": True}).code == "denied"
        assert act(c, OWNER, "payment_settings", {"direct_send": True}).status == "committed"
        direct = act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 1, "account_id": account(c, "6100"),
                                                  "mode": "direct", "money_account_id": _checking(c)})
        assert direct.code == "denied" and "paid before" in direct.reason     # on, but this payee is not known yet
        assert act(c, "human:ben", "payment_settings", {"direct_send": False}).code == "denied"
    sync_once(books, fake, HOURS[1])
    paid = {**MERCURY["txn_bank_fee"], "id": "txn-pay", "amount": -500.0, "kind": "outgoingPayment",
            "counterpartyName": "Acme Supplies", "requestId": "req-1", "postedAt": "2026-10-16T10:00:00Z"}
    fake.txns["txn-pay"] = paid
    fake.events_list.append({"id": "evt-pay", "resourceType": "transaction", "resourceId": "txn-pay",
                             "operationType": "create", "resourceVersion": 1})
    sync_once(books, fake, HOURS[3])
    sync_once(books, fake, HOURS[6])
    with books.read() as c:
        assert tuple(c.execute("SELECT status, last_error FROM fin_payments WHERE id=?", (pid,)).fetchone()) == ("settled", None)
        assert _holds(c) == [(50_000, "settled")]
        lines = sorted(tuple(r) for r in c.execute(
            "SELECT a.code, l.amount FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id JOIN fin_entries e "
            "ON e.id=l.entry_id WHERE e.source='bank'"))
        assert lines == [("1000", -50_000), ("6100", 50_000)]
        assert FR.recipients(c, OWNER, at=AT)["recipients"][0]["known"] is True


def _rule(c, who, name, when, outcome, examples, key=None):
    return act(c, who, "rule_create", {"name": name, "when": when, "outcome": outcome, "examples": examples}, key)


def test_rules_strictest_wins_examples_must_pass_and_a_bots_rule_needs_a_finance_admin(books):
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        bad = _rule(c, OWNER, "Big cards", {"kind": "card_create", "amount": {"gt": 5_000}},
                    {"outcome": "deny"}, [{"input": {"kind": "card_create", "amount": 6_000}, "expect": "allow"}])
        assert bad.code == "invalid" and "examples" in bad.reason
        ask = _rule(c, OWNER, "Cards ask", {"kind": "card_create"}, {"outcome": "needs_approval", "who": "manager"},
                    [{"input": {"kind": "card_create"}, "expect": "needs_approval"}])
        deny = _rule(c, OWNER, "No gus cards", {"kind": "card_create", "holder": "human:gus"},
                     {"outcome": "deny", "reason": "gus has no cards"},
                     [{"input": {"kind": "card_create", "holder": "human:gus"}, "expect": "deny"},
                      {"input": {"kind": "card_create", "holder": "human:eve"}, "expect": "allow"}])
        assert ask.status == deny.status == "committed"
        b = act(c, OWNER, "budget_create", {"name": "Cloud", "account_id": account(c, "6100"), "amount": 50_000})
        refused = act(c, OWNER, "card_create", {"holder": "human:gus", "budget_id": b.result["budget_id"],
                                                "amount": 1_000, "provider_user_id": "u"})
        assert refused.status == "denied" and refused.reason == "gus has no cards"   # deny beats needs_approval
        runs = c.execute("SELECT json_extract(outcome_json,'$.outcome') FROM fin_rule_runs WHERE rule_id=?",
                         (deny.result["rule_id"],)).fetchall()
        assert [r[0] for r in runs] == ["deny"]
        tested = act(c, OWNER, "rule_test", {"rule_id": deny.result["rule_id"]})
        assert tested.result["changed_count"] == 1
        assert FR.rules(c, OWNER, at=AT)["rules"]
        with pytest.raises(FinanceError):
            FR.rules(c, "bot:coder", at=AT)
        act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.rules"})
        act(c, OWNER, "right_grant", {"teammate": "human:cara", "right": "finance.rules"})
        by_bot = _rule(c, "bot:coder", "Coder rule", {"kind": "journal"}, {"outcome": "allow"},
                       [{"input": {"kind": "journal"}, "expect": "allow"}])
        assert by_bot.status == "needs_approval" and by_bot.approver == OWNER
        by_cara = _rule(c, "human:cara", "Cara rule", {"kind": "journal"}, {"outcome": "allow"},
                        [{"input": {"kind": "journal"}, "expect": "allow"}])
        assert by_cara.status == "committed"


def test_a_daily_limit_holds_under_concurrency(books):
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        made = _rule(c, OWNER, "Card money a day", {"kind": "card_create"},
                     {"outcome": "limit", "limit": 10_000, "per": "day"},
                     [{"input": {"kind": "card_create", "amount": 10_001}, "expect": "deny"},
                      {"input": {"kind": "card_create", "amount": 10_000}, "expect": "limit"}])
        assert made.status == "committed", made
        b = act(c, OWNER, "budget_create", {"name": "Cloud", "account_id": account(c, "6100"), "amount": 90_000})
    done = _race(books, 3, lambda c, i: act(c, OWNER, "card_create", {
        "holder": ("human:eve", "human:fay", "human:gus")[i], "budget_id": b.result["budget_id"], "amount": 4_000,
        "provider_user_id": "u"}))
    assert sorted(r.status for r in done) == ["committed", "committed", "denied"]
    with books.read() as c:
        assert c.execute("SELECT sum(amount) FROM fin_rule_counters WHERE rule_id=?", (made.result["rule_id"],)
                         ).fetchone()[0] == 8_000


def test_stale_bank_data_asks_instead_of_allowing(books):
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.pay"})
        made = _rule(c, OWNER, "Keep a cushion", {"kind": "payment_create", "balance": {"gte": 0}},
                     {"outcome": "limit", "limit": 1_000_000},
                     [{"input": {"kind": "payment_create", "balance": 5, "amount": 10}, "expect": "limit"}])
        assert made.status == "committed", made
        body = {"recipient_id": rid, "amount": 10_000, "account_id": account(c, "6100"), "tags": {"team": "eng"},
                "money_account_id": _checking(c)}
        fresh = act(c, "human:ben", "payment_create", body, dry_run=True, at="2026-10-15T12:10:00.000000Z")
        assert fresh.status == "committed", fresh
        stale = act(c, "human:ben", "payment_create", body, dry_run=True, at="2026-10-15T13:00:00.000000Z")
        assert stale.status == "needs_approval" and stale.approver == OWNER


def test_a_stale_input_never_dismisses_a_rule():
    """L: a deny on a low balance can't be skipped because a stale balance looked high; it asks instead."""
    from datetime import date
    from backend.finance.core import gates as G
    low = G.rule_of("r1", "Low balance", {"kind": "payment_create", "balance": {"lt": 100_000}}, {"outcome": "deny"})
    day = date(2026, 10, 15)
    stale = {"kind": "payment_create", "balance": 900_000, G.AGES: {"balance": 120}}
    assert G.evaluate([low], stale, day).outcome == "needs_approval"
    assert G.evaluate([low], {**stale, "balance": 50, G.AGES: {"balance": 120}}, day).outcome == "deny"
    assert G.evaluate([low], {**stale, G.AGES: {"balance": 1}}, day).outcome == "allow"     # fresh and high
    other = {"kind": "card_create", "balance": 900_000, G.AGES: {"balance": 120}}
    assert G.evaluate([low], other, day).outcome == "allow"                                 # the fresh part misses


def test_money_reads_cut_to_the_viewer_and_a_revoked_grant_is_gone(books):
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 5_000, "account_id": account(c, "6100"),
                                         "tags": {"team": "web"}, "money_account_id": _checking(c)})
        act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 7_000, "account_id": account(c, "6100"),
                                         "tags": {"team": "sales"}, "money_account_id": _checking(c)})

        def amounts(who):
            return sorted(p["amount"] for p in FR.payments(c, who, at=AT)["payments"])
        assert amounts(OWNER) == [5_000, 7_000]
        assert amounts("human:cara") == [5_000] and amounts("human:ben") == [5_000]       # managers: their teams
        assert amounts("human:dan") == [7_000]
        assert amounts("bot:coder") == []                                                  # a plain bot
        grant = act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "expenses", "whose": "team",
                                             "whose_team": "sales"})
        assert amounts("bot:coder") == [7_000]
        assert all(p["error"] is None for p in FR.payments(c, "bot:coder", at=AT)["payments"])
        act(c, OWNER, "view_revoke", {"grant_id": grant.result["grant_id"]})
        assert amounts("bot:coder") == []
        with pytest.raises(FinanceError):
            FR.recipients(c, "bot:coder", at=AT)
        assert FR.recipients(c, OWNER, at=AT)["recipients"][0]["last4"] == "8888"
        assert FR.reconciliations(c, "human:cara", at=AT)["reconciliations"] == []
        assert FR.may_connect(c, OWNER, at=AT) and not FR.may_connect(c, "human:cara", at=AT)


def test_every_send_is_judged_again_with_its_counters_once_and_a_new_deny_cancels_it(books):
    """C: right before the send, the action's rights, view, rules (counters less its own) and budget run again: a
    payment that used its own limit still goes; one a rule now denies, or whose payer lost the right, is cancelled."""
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.pay"})
        cap = _rule(c, OWNER, "Monthly cap", {"kind": "payment_create"},
                    {"outcome": "limit", "limit": 60_000, "per": "month"},
                    [{"input": {"kind": "payment_create", "amount": 10}, "expect": "limit"}])
        assert cap.status == "committed", cap
        body = {"recipient_id": rid, "account_id": account(c, "6100"), "tags": {"team": "eng"},
                "money_account_id": _checking(c)}
        fits = act(c, "human:ben", "payment_create", {**body, "amount": 50_000})
        assert fits.status == "committed", fits
    sync_once(books, fake, HOURS[1])
    assert [b["amount"] for b in fake.sent("request_send_money")] == [500]      # its own 500.00 not counted twice
    with books.transaction() as c:
        later = act(c, OWNER, "payment_create", {**body, "amount": 5_000})
        lost = act(c, "human:ben", "payment_create", {**body, "amount": 4_000, "tags": {"team": "web"}})
        assert later.status == lost.status == "committed"
        _rule(c, OWNER, "No more Acme", {"kind": "payment_create", "counterparty": "Acme Supplies",
                                         "amount": {"lt": 6_000}},
              {"outcome": "deny"}, [{"input": {"kind": "payment_create", "counterparty": "Acme Supplies",
                                               "amount": 1}, "expect": "deny"}])
        act(c, OWNER, "right_revoke", {"teammate": "human:ben", "right": "finance.pay"})
    sync_once(books, fake, HOURS[3])
    assert len(fake.sent("request_send_money")) == 1
    with books.read() as c:
        rows = dict(c.execute("SELECT amount, status || ':' || last_error FROM fin_payments WHERE amount<50000"))
        assert rows[5_000].startswith("cancelled:No more Acme")                 # a rule added after it was queued
        assert rows[4_000] == "cancelled:whoever asked for it no longer has the right to pay"
        assert sorted(_holds(c)) == [(4_000, "released"), (5_000, "released"), (50_000, "held")]   # the sent one keeps its


def test_a_wire_needs_its_purpose_and_a_refusal_is_never_silent(books):
    """Q: a wire carries purpose.simple; a refusal from Mercury fails it with an event and a Needs-you to its maker."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        made = act(c, OWNER, "recipient_add", {"name": "Acme Wire", "method": "wire",
                                               "account_number": "000011112222", "routing_number": "000000000"})
        assert made.status == "committed", made
    sync_once(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        body = {"recipient_id": made.result["recipient_id"], "amount": 30_000, "account_id": account(c, "6100"),
                "money_account_id": _checking(c)}
        bare = act(c, OWNER, "payment_create", body)
        assert bare.code == "invalid" and "purpose" in bare.reason
        # C19: Mercury's categories, with additional_info exactly where the category takes it.
        for purpose in ({"category": "nonsense"}, {"category": "vendor"}, {"category": "other"},
                        {"category": "employee", "additional_info": "Eve"}):
            assert act(c, OWNER, "payment_create", {**body, "purpose": purpose}).code == "invalid", purpose
        assert act(c, OWNER, "payment_create", {**body, "amount": 1_000, "purpose": {"category": "subsidiary"}}
                   ).status == "committed"
        paid = act(c, OWNER, "payment_create", {**body, "purpose": {"category": "vendor", "additional_info": "Q4"}})
        assert paid.status == "committed", paid
        again = act(c, OWNER, "payment_create", {**body, "amount": 30_001, "purpose": {"category": "travel"}})
        fixable = act(c, OWNER, "payment_create", {**body, "amount": 30_002, "purpose": {"category": "expenses"}})
    fake.queue["request_send_money"] = [Answer(200, MERCURY["send_request_created"]),
                                        Answer(200, MERCURY["send_request_created"]),
                                        Answer(400, {"message": "amount is over the daily wire limit"}),
                                        Answer(400, {"message": "purpose.simple.category is invalid"})]
    sync_once(books, fake, HOURS[1])
    assert fake.sent("request_send_money")[1]["purpose"] == {"simple": {"category": "vendor", "additionalInfo": "Q4"}}
    with books.read() as c:
        def status(made):
            return c.execute("SELECT status FROM fin_payments WHERE id=?", (made.result["payment_id"],)).fetchone()[0]
        assert status(again) == "failed"
        assert c.execute("SELECT count(*) FROM fin_events WHERE name='finance.payment.failed'").fetchone()[0] == 1
        assert _asks(c, "Payment of 300.01 failed") == 1
        assert status(fixable) == "queued" and _asks(c, "Payment of 300.02 is waiting") == 1   # a fixable refusal
        # The notices name the amount, so they are private to the one who must act.
        assert c.execute("SELECT private FROM tasks WHERE title LIKE 'Payment of 300.01 failed%'").fetchone()[0] == 1
        waiting = c.execute("SELECT private FROM tasks WHERE title LIKE 'Payment of 300.02 is waiting%'").fetchone()
        assert waiting[0] == 1
        assert (30_002, "held") in _holds(c)


# ------------------------------------------------------------------------------------------------ sealed bank details
ACCOUNT, ROUTING = "000077776666", "987654329"           # fictional (the routing number fails the ABA checksum)
PAYEE = {"name": "Acme Parts", "method": "ach", "account_number": ACCOUNT, "routing_number": ROUTING}


def _plaintext_anywhere(c) -> list[str]:
    """Every table and column holding the full account or routing number as text (encrypted blobs excluded)."""
    found = []
    for (table,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        try:
            rows = c.execute(f'SELECT * FROM "{table}"').fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            for col, value in zip(row.keys(), tuple(row)):
                if isinstance(value, str) and (ACCOUNT in value or ROUTING in value):
                    found.append(f"{table}.{col}")
    return found


def _sealed_rows(c) -> int:
    return c.execute("SELECT count(*) FROM credentials WHERE source='finance' AND ciphertext IS NOT NULL").fetchone()[0]


def test_bank_numbers_live_only_in_the_vault_through_create_approve_send(api):
    store = api.app.state.store
    FA.use_vault(api.app.state.vault)                    # this app's vault: the api fixture starts no bank worker
    FA.banking_stop()                                    # this test drives the worker itself, with a fake Mercury
    FA.use_vault(api.app.state.vault)                    # the app registers it only when its timers start
    fake = FakeMercury()
    with store.transaction() as c:
        FA.setup(c, OWNER, AT)
    connect(store, fake)
    with store.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.pay"})
        assert _rule(c, OWNER, "Payees ask", {"kind": "recipient_add"}, {"outcome": "needs_approval", "who": "owner"},
                     [{"input": {"kind": "recipient_add"}, "expect": "needs_approval"}]).status == "committed"
    answers = []
    dry = post(api, "finance/actions", {"kind": "recipient_add", "payload": PAYEE, "dry_run": True}, token="ben-test")
    asked = post(api, "finance/actions", {"kind": "recipient_add", "payload": PAYEE}, token="ben-test", key="payee-1")
    again = post(api, "finance/actions", {"kind": "recipient_add", "payload": PAYEE}, token="ben-test", key="payee-1")
    answers += [dry, asked, again]
    assert dry["status"] == asked["status"] == "needs_approval" and again["action_id"] == asked["action_id"]
    with store.transaction() as c:
        assert _sealed_rows(c) == 1
        H.approval_decide(c, OWNER, asked["approval_id"], "approved")
        assert FA.approval_decided(c, asked["approval_id"], AT).status == "committed"
    sync_once(store, fake)
    sent = fake.sent("create_recipient")[0]["electronicRoutingInfo"]
    assert (sent["accountNumber"], sent["routingNumber"]) == (ACCOUNT, ROUTING)   # read from the vault at send time
    answers += [get(api, "finance/recipients"), get(api, f"finance/actions/{asked['action_id']}", token="ben-test"),
                get(api, "finance/payments"), get(api, "finance/rules"), get(api, f"approvals/{asked['approval_id']}")]
    with store.read() as c:
        assert _plaintext_anywhere(c) == [] and _sealed_rows(c) == 1
        recipient = c.execute("SELECT last4, details_ref FROM fin_recipients").fetchone()
        assert recipient["last4"] == "6666" and recipient["details_ref"]
    assert ACCOUNT not in json.dumps(answers) and ROUTING not in json.dumps(answers)
    assert get(api, "finance/recipients")["recipients"][0]["last4"] == "6666"


def test_a_denied_or_declined_payee_leaves_no_bank_numbers_and_no_vault_row(api):
    store = api.app.state.store
    FA.use_vault(api.app.state.vault)
    FA.banking_stop()
    FA.use_vault(api.app.state.vault)
    fake = FakeMercury()
    with store.transaction() as c:
        FA.setup(c, OWNER, AT)
    connect(store, fake)
    denied = post(api, "finance/actions", {"kind": "recipient_add", "payload": PAYEE}, token="cara-test")
    assert denied["status"] == "denied"
    forged = post(api, "finance/actions", {"kind": "recipient_add", "payload": {**PAYEE, "details_ref": "cred-mercury"}})
    assert forged["status"] == "failed"                  # a caller never names a vault row
    with store.transaction() as c:
        assert _plaintext_anywhere(c) == [] and _sealed_rows(c) == 0
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.pay"})
        _rule(c, OWNER, "Payees ask", {"kind": "recipient_add"}, {"outcome": "needs_approval", "who": "owner"},
              [{"input": {"kind": "recipient_add"}, "expect": "needs_approval"}])
    asked = post(api, "finance/actions", {"kind": "recipient_add", "payload": PAYEE}, token="ben-test")
    with store.transaction() as c:
        assert _sealed_rows(c) == 1
        H.approval_decide(c, OWNER, asked["approval_id"], "declined")
        assert FA.approval_decided(c, asked["approval_id"], AT).status == "denied"
        assert _sealed_rows(c) == 0 and _plaintext_anywhere(c) == []
    assert fake.sent("create_recipient") == []


def test_a_limit_added_after_payments_were_queued_is_reserved_at_send_once(books):
    """N6: queued payments a new monthly limit now covers reserve its counter with their send claim, owned by each
    payment: the first fits and counts once (a retried send doesn't count it again); the next is over and cancelled."""
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        body = {"recipient_id": rid, "account_id": account(c, "6100"), "money_account_id": _checking(c)}
        first = act(c, OWNER, "payment_create", {**body, "amount": 10_000})
        second = act(c, OWNER, "payment_create", {**body, "amount": 10_000, "memo": "second"})
        assert first.status == second.status == "committed"
        cap = _rule(c, OWNER, "Monthly cap", {"kind": "payment_create"},
                    {"outcome": "limit", "limit": 15_000, "per": "month"},
                    [{"input": {"kind": "payment_create", "amount": 10}, "expect": "limit"}])
        assert cap.status == "committed", cap
        rule_id = str(cap.result["rule_id"])
    fake.queue["request_send_money"] = [Answer(429, {})]                  # the first try is turned back: retried
    sync_once(books, fake, HOURS[1])
    sync_once(books, fake, HOURS[3])
    sync_once(books, fake, HOURS[5])
    assert len(fake.sent("request_send_money")) == 2                       # the 429, then the send
    with books.read() as c:
        assert tuple(c.execute("SELECT sum(amount), sum(count) FROM fin_rule_counters WHERE rule_id=?",
                               (rule_id,)).fetchone()) == (10_000, 1)
        status = dict(c.execute("SELECT id, status || ':' || coalesce(last_error, '') FROM fin_payments"))
        assert sorted(status.values())[0].startswith("awaiting_bank_approval")
        assert sorted(status.values())[1].startswith("cancelled:Monthly cap"), status


def test_a_limit_reserved_at_send_is_given_back_once_when_the_payment_fails_or_is_cancelled(books):
    """Round 3 #4: what a payment reserved at its send claim comes off the counter when Mercury refuses it or a person
    cancels it after a retry, once (its record empties)."""
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        body = {"recipient_id": rid, "account_id": account(c, "6100"), "money_account_id": _checking(c)}
        assert act(c, OWNER, "payment_create", {**body, "amount": 10_000, "memo": "retried"}).status == "committed"
        assert act(c, OWNER, "payment_create", {**body, "amount": 20_000, "memo": "refused"}).status == "committed"
        rule_id = str(_rule(c, OWNER, "Monthly cap", {"kind": "payment_create"},
                            {"outcome": "limit", "limit": 50_000, "per": "month"},
                            [{"input": {"kind": "payment_create", "amount": 10}, "expect": "limit"}]
                            ).result["rule_id"])

    def counted(c):
        return tuple(c.execute("SELECT coalesce(sum(amount), 0), coalesce(sum(count), 0) FROM fin_rule_counters "
                               "WHERE rule_id=?", (rule_id,)).fetchone())
    fake.queue["request_send_money"] = [Answer(429, {}), Answer(400, {"errors": {"message": "account closed"}})]
    sync_once(books, fake, HOURS[1])
    with books.transaction() as c:
        state = {m: (st, cj) for m, st, cj in c.execute("SELECT memo, status, counted_json FROM fin_payments")}
        assert state["refused"] == ("failed", "[]")                         # refused: its reservation came back
        assert state["retried"][0] == "queued" and state["retried"][1] != "[]"
        assert counted(c) == (10_000, 1)                                    # only the retried one's
        pid = str(c.execute("SELECT id FROM fin_payments WHERE memo='retried'").fetchone()[0])
        assert act(c, OWNER, "payment_cancel", {"payment_id": pid}).status == "committed"
        assert counted(c) == (0, 0)
        assert c.execute("SELECT counted_json FROM fin_payments WHERE id=?", (pid,)).fetchone()[0] == "[]"


def _lines(c, source="bank"):
    return sorted(tuple(r) for r in c.execute(
        "SELECT a.code, l.amount FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id JOIN fin_entries e "
        "ON e.id=l.entry_id WHERE e.source=?", (source,)))


def _lost_send(books, fake, at=HOURS[1]):
    """A 500.00 payment on 6100 whose send Mercury accepted but whose answer never came back: unknown."""
    rid = _payee(books, fake)
    with books.transaction() as c:
        made = act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 50_000, "account_id": account(c, "6100"),
                                                "tags": {"team": "eng"}, "money_account_id": _checking(c)})
        pid = str(made.result["payment_id"])
    fake.queue["request_send_money"] = [Answer(None, {})]
    sync_once(books, fake, at)
    with books.read() as c:
        key = c.execute("SELECT idempotency_key FROM fin_payments WHERE id=?", (pid,)).fetchone()[0]
    return pid, key


def _debit(fake, txn_id, request_id, event_id, **extra):
    fake.txns[txn_id] = {**MERCURY["txn_bank_fee"], "id": txn_id, "amount": -500.0, "kind": "outgoingPayment",
                         "counterpartyName": "Acme Supplies", "requestId": request_id,
                         "postedAt": "2026-10-16T10:00:00Z", **extra}
    fake.events_list.append({"id": event_id, "resourceType": "transaction", "resourceId": txn_id,
                             "operationType": "create", "resourceVersion": 1})


def test_a_lost_send_whose_debit_posts_before_it_is_found_settles_on_its_line_once(books):
    """PR review P1-a: an accepted send whose answer was lost, its request approved and its debit posted before the
    recovery found it. The debit first posts as an unknown spend (6900); once found, it posts again as the payment
    (6100, Acme Supplies), the payment settles and its hold is taken in, once."""
    fake = FakeMercury()
    connect(books, fake)
    pid, key = _lost_send(books, fake)
    fake.land({"recipientId": "rcp-1", "amount": 500.0, "externalMemo": f"tico:{key} Acme"}, "req-9", "approved")
    _debit(fake, "txn-lost", "req-9", "evt-lost")                                # no note: only its request id
    for at in HOURS[2:5]:
        sync_once(books, fake, at)
    with books.read() as c:
        assert tuple(c.execute("SELECT status, provider_request_id FROM fin_payments WHERE id=?", (pid,)).fetchone()) \
            == ("settled", "req-9")
        assert _holds(c) == [(50_000, "settled")]
        # The 6900 posting is reversed; net, the debit sits on the payment's line.
        net: dict[str, int] = {}
        for code, amount in _lines(c):
            net[code] = net.get(code, 0) + amount
        assert {k: v for k, v in net.items() if v} == {"1000": -50_000, "6100": 50_000}
        tagged = c.execute("SELECT t.value FROM fin_line_tags t JOIN fin_lines l ON l.id=t.line_id JOIN fin_accounts a "
                           "ON a.id=l.account_id WHERE a.code='6100' AND t.kind='team'").fetchall()
        assert [r[0] for r in tagged] == ["eng"]


def _update(fake, txn_id, event_id, version, **changes):
    fake.txns[txn_id] = {**fake.txns[txn_id], **changes}
    fake.events_list.append({"id": event_id, "resourceType": "transaction", "resourceId": txn_id,
                             "operationType": "update", "resourceVersion": version})


def _net(c):
    net: dict[str, int] = {}
    for code, amount in _lines(c):
        net[code] = net.get(code, 0) + amount
    return {k: v for k, v in net.items() if v}


def test_a_debit_re_dated_in_the_sweep_that_finds_its_payment_settles_on_a_live_entry(books):
    """Codex round 6 #1: the unknown payment's debit posted as an unknown spend; in the sweep that finds the payment,
    Mercury re-dates the debit (its correction reposts the row) and the recovery reposts it again. Each repost has its
    own source id, so the payment never settles on a reversed entry: the debit nets onto the payment's line."""
    fake = FakeMercury()
    connect(books, fake)
    pid, key = _lost_send(books, fake)
    _debit(fake, "txn-lost", "req-9", "evt-lost")
    sync_once(books, fake, HOURS[2])                                          # not found yet: posts to 6900
    _update(fake, "txn-lost", "evt-lost-2", 2, postedAt="2026-10-17T10:00:00Z")
    fake.land({"recipientId": "rcp-1", "amount": 500.0, "externalMemo": f"tico:{key} Acme"}, "req-9", "approved")
    sync_once(books, fake, HOURS[3])                                          # re-dated, then found, one sweep
    with books.read() as c:
        assert str(c.execute("SELECT status FROM fin_payments WHERE id=?", (pid,)).fetchone()[0]) == "settled"
        assert _holds(c) == [(50_000, "settled")]
        entry = c.execute("SELECT e.status, e.reverses_id FROM fin_feed_txns f JOIN fin_entries e ON e.id=f.entry_id "
                          "WHERE f.provider_id='txn-lost'").fetchone()
        assert tuple(entry) == ("posted", None)
        assert _net(c) == {"1000": -50_000, "6100": 50_000}


def test_a_debit_returned_before_its_lost_payment_is_found_ends_the_payment(books):
    """Codex round 6 #4: the debit came back (returned) before the recovery found the payment: once found, the
    payment is returned and its hold released, never left submitted holding budget."""
    fake = FakeMercury()
    connect(books, fake)
    pid, key = _lost_send(books, fake)
    _debit(fake, "txn-lost", "req-9", "evt-lost")
    sync_once(books, fake, HOURS[2])
    _update(fake, "txn-lost", "evt-lost-2", 2, status="reversed")
    sync_once(books, fake, HOURS[3])
    fake.land({"recipientId": "rcp-1", "amount": 500.0, "externalMemo": f"tico:{key} Acme"}, "req-9", "approved")
    sync_once(books, fake, HOURS[4])
    with books.read() as c:
        assert tuple(c.execute("SELECT status, provider_request_id FROM fin_payments WHERE id=?", (pid,)).fetchone()) \
            == ("returned", "req-9")
        assert _holds(c) == [(50_000, "released")]
        assert _net(c) == {}


def _reconcile(c, provider_id):
    """Reconcile checking through Oct 16 with this feed row cleared; its id."""
    fid = str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id=?", (provider_id,)).fetchone()[0])
    args = {"money_account_id": _checking(c), "statement_end": "2026-10-16", "cleared": [fid]}
    off = act(c, OWNER, "reconcile", {**args, "statement_balance": 0})
    if off.result["status"] != "done":
        c.execute("DELETE FROM fin_reconciliations")
        done = act(c, OWNER, "reconcile", {**args, "statement_balance": -int(off.result["difference"])})
        assert done.result["status"] == "done", done
    return fid


def test_a_reconciled_debit_on_the_payments_line_under_another_vendor_still_moves_to_its_vendor(books):
    """Codex round 8 #1: the debit was categorized to the payment's line and tags but under the bank's counterparty,
    not the recipient's vendor, then reconciled. Found as the payment, its spend moves to the vendor (the hold on the
    vendor's budget is taken in by that move), so the vendor budget reads (500, 0, 500), never (0, 0, 1,000)."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        line = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 200_000})
        made = act(c, OWNER, "budget_create", {"name": "Acme", "account_id": account(c, "6100"),
                                               "vendor": "Acme Supplies", "amount": 100_000,
                                               "parent_id": line.result["budget_id"]})
        assert made.status == "committed", made
        budget_id = str(made.result["budget_id"])
    pid, key = _lost_send(books, fake)
    with books.read() as c:
        rec = c.execute("SELECT r.contact_id, r.name FROM fin_payments p JOIN fin_recipients r ON r.id=p.recipient_id "
                        "WHERE p.id=?", (pid,)).fetchone()
        assert str(rec[0] or rec[1]) == "Acme Supplies"
        assert c.execute("SELECT budget_id FROM fin_holds WHERE source='payment'").fetchone()[0] == budget_id

    def acme(c):
        s = FA.budget_state(c, budget_id, "2026-10-20T12:00:00.000000Z")
        return s.actual, s.held, s.remaining
    _debit(fake, "txn-lost", "req-9", "evt-lost", counterpartyName="ACME SUPPLY CO")
    sync_once(books, fake, HOURS[2])
    with books.transaction() as c:
        fid = str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-lost'").fetchone()[0])
        done = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100"), "tags": {"team": "eng"}})
        assert done.status == "committed", done
        _reconcile(c, "txn-lost")
        assert acme(c) == (0, 50_000, 50_000)
    fake.land({"recipientId": "rcp-1", "amount": 500.0, "externalMemo": f"tico:{key} Acme"}, "req-9", "approved")
    sync_once(books, fake, HOURS[3])
    with books.read() as c:
        assert str(c.execute("SELECT status FROM fin_payments WHERE id=?", (pid,)).fetchone()[0]) == "settled"
        assert acme(c) == (50_000, 0, 50_000)
        assert c.execute("SELECT count(*) FROM fin_events WHERE name LIKE 'finance.budget.%' AND subject=?",
                         (budget_id,)).fetchone()[0] == 0


def test_a_reconciled_debit_found_as_a_lost_payment_moves_its_spend_to_the_payments_line(books):
    """Codex round 6 #4 and round 7: a 500.00 Software payment holds 500.00 on a 1,000.00 Software budget; its debit
    posted as an unknown spend (Other expenses) and was reconciled before the recovery found the payment. The bank
    line stays as reconciled; the spend moves to the payment's line in an entry that takes in the hold, so Software
    reads (500 spent, 0 held, 500 left), never (0, 0, 1,000). The categorize event stays. Grok round 8: found in
    November with October still open, the move is dated on the debit's own October day; and while the row stays
    reconciled the move can't be reversed by hand (that would free the budget)."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 100_000})
        budget_id = str(made.result["budget_id"])

    def software(c, at="2026-10-20T12:00:00.000000Z"):
        s = FA.budget_state(c, budget_id, at)
        return s.actual, s.held, s.remaining
    pid, key = _lost_send(books, fake)
    _debit(fake, "txn-lost", "req-9", "evt-lost")
    sync_once(books, fake, HOURS[2])
    with books.transaction() as c:
        fid = _reconcile(c, "txn-lost")
        bank = [x for x in _lines(c) if x[0] == "1000"]
        assert software(c) == (0, 50_000, 50_000)
    fake.land({"recipientId": "rcp-1", "amount": 500.0, "externalMemo": f"tico:{key} Acme"}, "req-9", "approved")
    sync_once(books, fake, "2026-11-02T13:30:00.000000Z")
    sync_once(books, fake, "2026-11-02T14:30:00.000000Z")
    with books.transaction() as c:
        assert tuple(c.execute("SELECT status, provider_txn_id FROM fin_payments WHERE id=?", (pid,)).fetchone()) \
            == ("settled", fid)
        assert _holds(c) == [(50_000, "settled")]
        assert software(c) == (50_000, 0, 50_000)
        assert software(c, "2026-11-20T12:00:00.000000Z") == (0, 0, 100_000)
        moved = c.execute("SELECT id, entry_date FROM fin_entries WHERE source='bank' AND source_id=?",
                          (f"{fid}:reclass",)).fetchone()
        assert moved["entry_date"] == "2026-10-16"
        refused = act(c, OWNER, "journal", {"reverses": moved["id"]})
        assert refused.code == "conflict" and "reconciled" in refused.reason
        assert software(c) == (50_000, 0, 50_000)
        # Round 8 #2: the move and the hold never count together, so no warn or cutoff fired on the way.
        assert c.execute("SELECT count(*) FROM fin_events WHERE name LIKE 'finance.budget.%' AND subject=?",
                         (budget_id,)).fetchone()[0] == 0
        assert [x for x in _lines(c) if x[0] == "1000"] == bank                  # the reconciled bank line as it was
        assert _net(c) == {"1000": -50_000, "6100": 50_000}
        tagged = c.execute("SELECT t.value FROM fin_line_tags t JOIN fin_lines l ON l.id=t.line_id JOIN fin_accounts a "
                           "ON a.id=l.account_id WHERE a.code='6100' AND t.kind='team'").fetchall()
        assert [r[0] for r in tagged] == ["eng"]
        assert c.execute("SELECT count(*) FROM fin_events WHERE name='finance.feed.needs_category' AND "
                         "payload_json LIKE '%found after it was reconciled%'").fetchone()[0] == 1
    with books.transaction() as c:                    # unreconciled and re-categorized: the move goes with the entry
        rec = str(c.execute("SELECT reconciliation_id FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()[0])
        assert act(c, OWNER, "reconcile_undo", {"reconciliation_id": rec}).status == "committed"
        again = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100"), "tags": {"team": "eng"}})
        assert again.status == "committed", again
        assert _net(c) == {"1000": -50_000, "6100": 50_000}


def test_a_debit_carrying_a_lost_sends_key_is_the_payment_when_it_arrives(books):
    """PR review P1-a (the marker): a debit whose note carries the payment's tico:<key> is that payment as it
    arrives, before any request is found: it posts once on the payment's line."""
    fake = FakeMercury()
    connect(books, fake)
    pid, key = _lost_send(books, fake)
    _debit(fake, "txn-marked", "req-10", "evt-marked", note=f"tico:{key}")
    sync_once(books, fake, HOURS[2])
    with books.read() as c:
        assert tuple(c.execute("SELECT status, provider_request_id FROM fin_payments WHERE id=?", (pid,)).fetchone()) \
            == ("settled", "req-10")
        assert _holds(c) == [(50_000, "settled")] and _lines(c) == [("1000", -50_000), ("6100", 50_000)]


@pytest.fixture
def stale_vault():
    """What an earlier app (or a test) left registered: a vault that is not set up for this one."""
    from types import SimpleNamespace
    FA.use_vault(SimpleNamespace(configured=False))


def test_payee_and_payment_actions_work_on_an_app_with_timers_off(stale_vault, api):  # noqa: F811
    """The app registers its own vault at startup, so an app with the scheduler off (a demo, this fixture) never seals
    bank details with whatever an earlier registration left behind."""
    store = api.app.state.store
    assert not api.app.state.store.settings.scheduler_enabled
    fake = FakeMercury()
    with store.transaction() as c:
        FA.setup(c, OWNER, AT)
    connect(store, fake)
    added = post(api, "finance/actions", {"kind": "recipient_add", "payload": PAYEE})
    assert added["status"] == "committed", added
    with store.transaction() as c:
        assert _sealed_rows(c) == 1 and _plaintext_anywhere(c) == []
    sync_once(store, fake)
    with store.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        pay = {"recipient_id": added["result"]["recipient_id"], "money_account_id": _checking(c),
               "account_id": account(c, "6100"), "amount": 2_500}
    paid = post(api, "finance/actions", {"kind": "payment_create", "payload": pay})
    assert paid["status"] == "committed", paid


def test_direct_send_is_for_a_paid_recipient_once_the_owner_turns_it_on_and_skips_the_approval_queue(books):
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "payment_settings", {"direct_send": True}).status == "committed"
        made = act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 50_000, "account_id": account(c, "6100"),
                                                "tags": {"team": "eng"}, "money_account_id": _checking(c)})
        assert made.status == "committed"
    sync_once(books, fake, HOURS[1])
    fake.txns["txn-pay"] = {**MERCURY["txn_bank_fee"], "id": "txn-pay", "amount": -500.0, "kind": "outgoingPayment",
                            "counterpartyName": "Acme Supplies", "requestId": "req-1", "postedAt": "2026-10-16T10:00:00Z"}
    fake.events_list.append({"id": "evt-pay", "resourceType": "transaction", "resourceId": "txn-pay",
                             "operationType": "create", "resourceVersion": 1})
    sync_once(books, fake, HOURS[3])
    sync_once(books, fake, HOURS[6])
    with books.transaction() as c:
        direct = act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 10_000, "account_id": account(c, "6100"),
                                                  "mode": "direct", "money_account_id": _checking(c)}, at=HOURS[7])
        assert direct.status == "committed"
        dpid = str(direct.result["payment_id"])
    sync_once(books, fake, HOURS[8])
    assert len(fake.sent("create_transaction")) == 1 and len(fake.sent("request_send_money")) == 1
    with books.read() as c:
        assert tuple(c.execute("SELECT mode, status, provider_request_id FROM fin_payments WHERE id=?",
                               (dpid,)).fetchone()) == ("direct", "submitted", "txn-direct-1")
    fake.txns["txn-direct-1"] = {**MERCURY["txn_bank_fee"], "id": "txn-direct-1", "amount": -100.0,
                                 "kind": "outgoingPayment", "counterpartyName": "Acme Supplies",
                                 "postedAt": "2026-10-16T12:00:00Z"}
    fake.events_list.append({"id": "evt-direct", "resourceType": "transaction", "resourceId": "txn-direct-1",
                             "operationType": "create", "resourceVersion": 1})
    sync_once(books, fake, HOURS[10])
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_payments WHERE id=?", (dpid,)).fetchone()[0] == "settled"


def _paid_acme(books, fake):
    """Direct send on, and one payment to Acme Supplies paid through Mercury: the payee is known and paid."""
    rid = _payee(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "payment_settings", {"direct_send": True}).status == "committed"
        made = act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 50_000, "account_id": account(c, "6100"),
                                                "tags": {"team": "eng"}, "money_account_id": _checking(c)})
        assert made.status == "committed", made
    sync_once(books, fake, HOURS[1])
    fake.txns["txn-pay"] = {**MERCURY["txn_bank_fee"], "id": "txn-pay", "amount": -500.0, "kind": "outgoingPayment",
                            "counterpartyName": "Acme Supplies", "requestId": "req-1", "postedAt": "2026-10-16T10:00:00Z"}
    fake.events_list.append({"id": "evt-pay", "resourceType": "transaction", "resourceId": "txn-pay",
                             "operationType": "create", "resourceVersion": 1})
    sync_once(books, fake, HOURS[3])
    sync_once(books, fake, HOURS[6])
    return rid


def _direct(books, rid, amount=10_000):
    with books.transaction() as c:
        made = act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": amount, "account_id": account(c, "6100"),
                                                "mode": "direct", "money_account_id": _checking(c)}, at=HOURS[7])
        assert made.status == "committed", made
        return str(made.result["payment_id"])


def test_turning_direct_send_off_before_a_queued_direct_payment_goes_sends_it_by_the_approval_queue(books):
    """Item 4: the company switch is read again at send. A direct payment queued while it was on goes to Mercury's
    approval queue once it is off, and is never sent as a direct transaction."""
    fake = FakeMercury()
    connect(books, fake)
    rid = _paid_acme(books, fake)
    dpid = _direct(books, rid)
    with books.transaction() as c:
        assert act(c, OWNER, "payment_settings", {"direct_send": False}).status == "committed"
    sync_once(books, fake, HOURS[8])
    assert fake.sent("create_transaction") == []
    assert len(fake.sent("request_send_money")) == 2
    with books.read() as c:
        mode, status = c.execute("SELECT mode, status FROM fin_payments WHERE id=?", (dpid,)).fetchone()
    assert mode == "approval_queue" and status not in ("failed", "cancelled"), (mode, status)


def test_a_rule_on_direct_payments_added_after_queueing_cancels_the_send(books):
    """Codex P1: the send-time recheck builds the same facts as the create did, mode included, so a deny on direct
    payments added after the payment was queued cancels it, and no direct transaction is made."""
    fake = FakeMercury()
    connect(books, fake)
    rid = _paid_acme(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "payment_settings", {"direct_send": True}).status == "committed"
    dpid = _direct(books, rid)
    with books.transaction() as c:
        _rule(c, OWNER, "No direct pays", {"kind": "payment_create", "mode": "direct"}, {"outcome": "deny"},
              [{"input": {"kind": "payment_create", "mode": "direct"}, "expect": "deny"}])
    sync_once(books, fake, HOURS[8])
    assert fake.sent("create_transaction") == []
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_payments WHERE id=?", (dpid,)).fetchone()[0] == "cancelled"


def test_a_direct_payment_the_bank_cancels_with_no_request_id_fails_and_its_hold_is_released(books):
    """Item 8: a direct send has no request id, so its own Mercury transaction id ends it when the bank voids the row."""
    fake = FakeMercury()
    connect(books, fake)
    rid = _paid_acme(books, fake)
    dpid = _direct(books, rid)
    sync_once(books, fake, HOURS[8])
    assert len(fake.sent("create_transaction")) == 1
    fake.txns["txn-direct-1"] = {**MERCURY["txn_bank_fee"], "id": "txn-direct-1", "amount": -100.0,
                                 "kind": "outgoingPayment", "counterpartyName": "Acme Supplies",
                                 "status": "cancelled", "postedAt": "2026-10-16T12:00:00Z"}
    fake.events_list.append({"id": "evt-direct-cancel", "resourceType": "transaction", "resourceId": "txn-direct-1",
                             "operationType": "update", "resourceVersion": 2})
    sync_once(books, fake, HOURS[10])
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_payments WHERE id=?", (dpid,)).fetchone()[0] == "failed"
        assert (10_000, "released") in _holds(c)


def test_a_send_time_rule_on_who_asks_cancels_a_payment_someone_else_queued(books):
    """Ben queues a payment; the owner then adds a deny for payments by anyone but the owner. The send-time facts carry
    actor.owner as the request did, so the payment is cancelled before Mercury sees it."""
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.pay"})
        body = {"recipient_id": rid, "account_id": account(c, "6100"), "tags": {"team": "eng"},
                "money_account_id": _checking(c)}
        queued = act(c, "human:ben", "payment_create", {**body, "amount": 5_000})
        assert queued.status == "committed", queued
        _rule(c, OWNER, "Only the owner pays", {"kind": "payment_create", "actor.owner": False},
              {"outcome": "deny"}, [{"input": {"kind": "payment_create", "actor.owner": False}, "expect": "deny"}])
    sync_once(books, fake, HOURS[1])
    assert fake.sent("request_send_money") == []
    with books.read() as c:
        status = c.execute("SELECT status || ':' || coalesce(last_error, '') FROM fin_payments").fetchone()[0]
        assert status.startswith("cancelled:Only the owner pays"), status
