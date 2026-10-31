"""Stripe in the books (spec §9): the balance feed, its posting (sales, fees, refunds and disputes in proportion), a payout
in transit until a Stripe deposit clears it, an invoice paid through Stripe, backfill and catch-up that take each balance
transaction once across passes, the opening balance from one cut, a reconnect that reuses its money account, daily
summaries, and webhook signatures. Runs on an in-memory fake of Stripe's API and the Mercury fixtures the bank tests use."""
import json
import time
from datetime import datetime, timezone
from typing import Any

import pytest

from backend import trigger_presets as P
from backend.finance import actions as FA
from backend.finance import banking, stripe_sync, sync
from backend.finance.context import load
from backend.finance.core import feeds as F
from backend.finance.mercury import Answer
from backend.tests.test_api import api, get, headers  # noqa: F401
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, FakeMercury, account, act, books, connect, finance_template, store)

DAY = int(datetime(2026, 10, 10, 12, tzinfo=timezone.utc).timestamp())
WEBHOOK_SECRET = "whsec_test_secret_0123456789abcdef"
ACCOUNT = "acct_test000001"
STALE_AT = "2026-10-20T12:00:00.000000Z"            # seven days past the first charges' day, plus the margin


def _source(kind: str, tid: str, link: str, name: str) -> dict[str, Any]:
    """The object a balance transaction came from, as the expanded list shows it."""
    if kind in ("charge", "payment"):
        return {"id": f"ch_{tid}", "object": "charge", "billing_details": {"name": name}}
    if kind in ("refund", "payment_refund", "refund_failure"):
        return {"id": f"re_{tid}", "object": "refund", "charge": link}
    if kind == "adjustment":
        return {"id": f"dp_{tid}", "object": "dispute", "charge": link}
    if kind == "payout":
        return {"id": f"po_{tid}", "object": "payout"}
    if kind in ("payout_failure", "payout_cancel"):
        return {"id": link, "object": "payout"}
    return {"id": f"src_{tid}", "object": "other"}


class FakeStripe:
    """Stripe's account and balance API in memory: balance transactions are added oldest first and served newest first,
    `page` at a time, the way the real list pages. `start` is a balance the book never saw (an earlier history)."""

    def __init__(self, page: int = 100, start: int = 0) -> None:
        self.txns: list[dict[str, Any]] = []
        self.page = page
        self.start = start

    def add(self, kind: str, amount: int, fee: int = 0, name: str = "", link: str = "",
            created: int | None = None) -> str:
        tid = f"txn_{len(self.txns) + 1:04d}"
        self.txns.append({"id": tid, "object": "balance_transaction", "type": kind, "amount": amount, "fee": fee,
                          "net": amount - fee, "currency": "usd", "status": "available",
                          "created": created if created is not None else DAY + 60 * (len(self.txns) + 1),
                          "description": kind, "source": _source(kind, tid, link, name)})
        return tid

    def account(self) -> Answer:
        return Answer(200, {"object": "account", "id": ACCOUNT})

    def balance(self) -> Answer:
        net = self.start + sum(int(t["net"]) for t in self.txns)
        return Answer(200, {"available": [{"amount": net, "currency": "usd"}], "pending": []})

    def transactions(self, starting_after: str | None = None, limit: int = 100) -> Answer:
        newest = list(reversed(self.txns))
        start = [t["id"] for t in newest].index(starting_after) + 1 if starting_after else 0
        page = newest[start:start + min(limit, self.page)]
        return Answer(200, {"object": "list", "data": page, "has_more": start + len(page) < len(newest)})

    def total(self) -> int:
        return self.start + sum(int(t["net"]) for t in self.txns)


def connect_stripe(store: Any, fake: FakeStripe, credential: str = "cred-stripe") -> str:
    found, why = stripe_sync.probe(fake)  # type: ignore[arg-type]
    assert not why, why
    with store.transaction() as c:
        done = act(c, OWNER, "connection_create", {"provider": "stripe", "credential_id": credential,
                                                   "probe": found})
    assert done.status == "committed", done
    return str(done.result["connection_id"])


def stripe_pass(store: Any, fake: FakeStripe, at: str = AT) -> str:
    """One pass of the bank worker over the Stripe connections, with `fake` as Stripe."""
    with store.read() as c:
        ids = [str(r[0]) for r in c.execute("SELECT id FROM fin_connections WHERE provider='stripe'")]
    out = ""
    for cid in ids:
        out = sync.connection(store, None, cid, make=lambda _t: fake, at=at)  # type: ignore[arg-type]
    return out


def deposit(store: Any, amount: int, pid: str, day: str = "2026-10-12", counterparty: str = "STRIPE") -> str:
    """A Mercury deposit taken in the way the bank worker takes one in (banking.ingest, one transaction)."""
    with store.transaction() as c:
        ctx = load(c, AT)
        cid, acct = c.execute("SELECT connection_id, provider_account_id FROM fin_money_accounts "
                              "WHERE provider='mercury' AND kind='bank' ORDER BY created_at LIMIT 1").fetchone()
        fid = banking.ingest(ctx, str(cid), F.FeedTxn(provider_id=pid, account_id=str(acct), amount=amount,
                                                      txn_date=day, status=F.POSTED, kind="externalTransfer",
                                                      counterparty=counterparty))
    return str(fid)


def _sum(c: Any, code: str) -> int:
    """What the posted entries put on one account, by code."""
    return int(c.execute("SELECT COALESCE(SUM(l.amount),0) FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
                         "JOIN fin_accounts a ON a.id=l.account_id WHERE a.code=? AND e.status='posted'",
                         (code,)).fetchone()[0])


def _named_sum(c: Any, name: str) -> int:
    """What the posted entries put on one account, by its name (the transit account is found by name)."""
    return int(c.execute("SELECT COALESCE(SUM(l.amount),0) FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
                         "JOIN fin_accounts a ON a.id=l.account_id WHERE a.name=? AND e.status='posted'",
                         (name,)).fetchone()[0])


def _every_entry_balances(c: Any) -> bool:
    return int(c.execute("SELECT COALESCE(SUM(l.amount),0) FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
                         "WHERE e.status='posted'").fetchone()[0]) == 0


def _bank_code(c: Any) -> str:
    return str(c.execute("SELECT a.code FROM fin_money_accounts m JOIN fin_accounts a ON a.id=m.account_id "
                         "WHERE m.provider='mercury' AND m.kind='bank' ORDER BY m.created_at LIMIT 1").fetchone()[0])


def _feed(c: Any, provider_id: str) -> Any:
    return c.execute("SELECT * FROM fin_feed_txns WHERE provider_id=?", (provider_id,)).fetchone()


def _asked(c: Any, provider_id: str, period: str = "") -> int:
    """How many times a person was asked about this Stripe row (for its key, `period`)."""
    row = _feed(c, provider_id)
    return int(c.execute("SELECT COUNT(*) FROM fin_events WHERE name='finance.feed.needs_category' AND subject=? AND "
                         "period_key=?", (str(row["id"]), period)).fetchone()[0])


def _entries(c: Any, prefix: str) -> int:
    return int(c.execute("SELECT COUNT(*) FROM fin_entries WHERE status='posted' AND source_id LIKE ?",
                         (prefix + "%",)).fetchone()[0])


def _customer(store: Any, name: str) -> str:
    with store.transaction() as c:
        return str(act(c, OWNER, "contact_create", {"name": name, "kind": "customer"}).result["contact_id"])


def _invoice(store: Any, customer: str, amount: int) -> None:
    with store.transaction() as c:
        made = act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": "2026-10-01",
                                                "lines": [{"account_id": account(c, "4000"), "amount": amount}]})
        assert act(c, OWNER, "invoice_send", {"invoice_id": made.result["invoice_id"]}).status == "committed"


def test_a_sale_is_income_less_a_fee_and_the_balance_gets_the_net(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    assert stripe_pass(books, stripe) == "ok"
    with books.read() as c:
        assert (_sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (-10_000, 320, 9_680)
        assert _every_entry_balances(c)


def test_a_refund_reverses_its_sale_and_the_fee_stays_spent(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe.add("refund", -10_000, link="ch_txn_0001")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (0, 320, -320)
        assert _every_entry_balances(c)


def test_a_partial_refund_reverses_only_its_share_of_the_sale(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe.add("refund", -4_000, link="ch_txn_0001")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (-6_000, 320, 5_680)
        assert _every_entry_balances(c)


def test_a_partial_refund_of_an_invoice_paid_sale_reopens_its_receivable_in_proportion(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    customer = _customer(books, "Acme Retail")
    _invoice(books, customer, 10_000)
    stripe.add("charge", 10_000, 320, "Acme Retail")
    stripe.add("refund", -4_000, link="ch_txn_0001")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "1200"), _sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (4_000, -10_000, 320, 5_680)
        assert _every_entry_balances(c)


def test_a_refund_that_arrives_before_its_sale_waits_for_it(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("refund", -10_000, link="ch_txn_0002")
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (0, 320, -320)
        assert _feed(c, "txn_0001")["status"] == "posted"
        assert _every_entry_balances(c)


def test_a_dispute_reverses_its_sale_and_one_naming_no_sale_is_asked(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe.add("adjustment", -10_000, link="ch_txn_0001")
    stripe.add("adjustment", -500)
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (0, 320, -320)
        assert _feed(c, "txn_0003")["status"] == "new"
        assert _asked(c, "txn_0003") == 1
        assert _every_entry_balances(c)


def test_a_refund_failure_restores_the_refunded_sale(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe.add("refund", -10_000, link="ch_txn_0001")
    stripe.add("refund_failure", 10_000, link="ch_txn_0001")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (-10_000, 320, 9_680)
        assert _every_entry_balances(c)


def test_a_balance_type_nothing_posts_is_asked_and_posts_nothing(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("transfer", 500)
    stripe_pass(books, stripe)
    with books.read() as c:
        assert _sum(c, "1050") == 0
        assert _asked(c, "txn_0001") == 1


@pytest.mark.parametrize("deposit_first", [False, True])
def test_a_payout_clears_its_bank_deposit_once(books, deposit_first):
    """Whichever side arrives second posts the one clearing: the payout is in transit on its day, the deposit moves
    the bank and empties the transit, and income is not counted again on either side."""
    connect(books, FakeMercury())
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe.add("payout", -9_680)
    if deposit_first:
        deposit(books, 9_680, "pay-1")
        stripe_pass(books, stripe)
    else:
        stripe_pass(books, stripe)
        deposit(books, 9_680, "pay-1")
    with books.read() as c:
        assert _named_sum(c, "Stripe payouts in transit") == 0
        assert (_sum(c, "1050"), _sum(c, _bank_code(c)), _sum(c, "4000"), _sum(c, "6200")) == (0, 9_680, -10_000, 320)
        assert _entries(c, "stripe:clear:") == 1
        assert _every_entry_balances(c)


def test_an_unrelated_receipt_of_the_same_amount_never_clears_a_payout(books):
    connect(books, FakeMercury())
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe.add("payout", -9_680)
    stripe_pass(books, stripe)
    deposit(books, 9_680, "pay-1", counterparty="Acme Buyer")
    with books.read() as c:
        assert _named_sum(c, "Stripe payouts in transit") == 9_680
        assert _entries(c, "stripe:clear:") == 0
        assert _every_entry_balances(c)


def test_a_payout_outside_the_match_window_stays_in_transit_and_is_asked_after_seven_days(books):
    connect(books, FakeMercury())
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe.add("payout", -9_680)
    stripe_pass(books, stripe)
    deposit(books, 9_680, "pay-late", day="2026-10-30")
    stripe_pass(books, stripe, STALE_AT)
    with books.read() as c:
        assert _named_sum(c, "Stripe payouts in transit") == 9_680
        assert _entries(c, "stripe:clear:") == 0
        assert _asked(c, "txn_0002", "stale") == 1


def test_a_refund_whose_sale_never_arrives_is_asked_after_seven_days(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("refund", -10_000, link="ch_missing")
    stripe_pass(books, stripe)
    stripe_pass(books, stripe, STALE_AT)
    with books.read() as c:
        assert _sum(c, "1050") == 0
        assert _asked(c, "txn_0001", "stale") == 1


def test_an_invoice_paid_already_takes_a_customer_credit_and_a_person_is_asked(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    _invoice(books, _customer(books, "Acme Retail"), 10_000)
    stripe.add("charge", 10_000, 320, "Acme Retail")
    stripe_pass(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Retail")
    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    with books.read() as c:
        assert (_sum(c, "1200"), _sum(c, "4000"), _sum(c, "1050")) == (-10_000, -10_000, 19_360)
        assert _asked(c, "txn_0002", "paid") == 1
        assert _every_entry_balances(c)


def test_several_open_invoices_for_the_amount_ask_and_post_nothing(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    customer = _customer(books, "Acme Retail")
    _invoice(books, customer, 10_000)
    _invoice(books, customer, 10_000)
    stripe.add("charge", 10_000, 320, "Acme Retail")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "1050"), _sum(c, "1200"), _sum(c, "4000")) == (0, 20_000, -20_000)
        assert _feed(c, "txn_0001")["status"] == "new"
        assert _asked(c, "txn_0001", "ambiguous") == 1


def test_a_payout_that_fails_returns_its_transit_to_the_balance(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe.add("payout", -9_680)
    stripe.add("payout_failure", 9_680, link="po_txn_0002")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "1050"), _named_sum(c, "Stripe payouts in transit")) == (9_680, 0)
        assert _every_entry_balances(c)


def test_a_day_of_ordinary_sales_posts_one_summary_and_a_later_row_adjusts_the_day(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe.add("charge", 5_000, 160, "Acme Buyer")
    stripe.add("charge", 2_000, 60, "Acme Buyer")
    stripe.add("stripe_fee", -500)
    stripe_pass(books, stripe)
    with books.read() as c:
        assert _entries(c, "stripe:summary:") == 1
        assert (_sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (-17_000, 1_040, 15_960)
        assert _every_entry_balances(c)
    stripe.add("charge", 1_000, 30, "Acme Buyer", created=DAY + 3_000)
    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    stripe_pass(books, stripe, "2026-10-15T14:00:00.000000Z")
    with books.read() as c:
        assert _entries(c, "stripe:adjust:") == 1
        assert (_sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (-18_000, 1_070, 16_930)
        assert _every_entry_balances(c)


def test_the_opening_balance_counts_each_transaction_once_when_a_charge_lands_during_the_cut(books, monkeypatch):
    """A charge that lands after the balance is read and before the cut is taken: it is in the books as a sale, and the
    opening balance (the balance at the cut, less what was created by it) does not count it again."""
    stripe = FakeStripe(start=7_000)
    connect_stripe(books, stripe)
    for i in range(3):
        stripe.add("charge", 1_000, 0, "Acme Buyer", created=DAY + 60 * (i + 1))

    def cut_clock() -> float:
        stripe.add("charge", 500, 0, "Acme Late", created=DAY + 2_000)
        return DAY + 1_000

    monkeypatch.setattr(stripe_sync, "_clock", cut_clock)
    stripe_pass(books, stripe)
    with books.read() as c:
        opening = c.execute("SELECT code FROM fin_accounts WHERE subtype='opening'").fetchone()[0]
        assert _sum(c, str(opening)) == -7_000
        assert _sum(c, "1050") == stripe.total() == 10_500
        assert _every_entry_balances(c)


def test_backfill_reads_every_page_once_and_a_repeat_changes_nothing(books):
    stripe = FakeStripe(page=2)
    connect_stripe(books, stripe)
    for i in range(5):
        stripe.add("charge", 1_000 * (i + 1), 30, f"Buyer {i}")
    stripe_pass(books, stripe, AT)
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_feed_txns").fetchone()[0] == 5
        settings = json.loads(c.execute("SELECT settings_json FROM fin_connections WHERE provider='stripe'"
                                        ).fetchone()[0])
        assert settings["backfilled"] is True
        before = (_sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050"))
        entries = c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0]
    for at in ("2026-10-15T13:00:00.000000Z", "2026-10-15T14:00:00.000000Z"):
        stripe_pass(books, stripe, at)
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_feed_txns").fetchone()[0] == 5
        assert c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0] == entries
        assert (_sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == before
        assert _every_entry_balances(c)


def test_catch_up_resumes_across_passes_and_takes_every_new_row_once(books, monkeypatch):
    stripe = FakeStripe(page=2)
    connect_stripe(books, stripe)
    for i in range(9):
        stripe.add("charge", 1_000, 0, f"Buyer {i}")
    stripe_pass(books, stripe, AT)
    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    monkeypatch.setattr(stripe_sync, "CATCH_UP_PAGES", 2)
    for i in range(9):
        stripe.add("charge", 1_000, 0, f"New {i}")
    stripe_pass(books, stripe, "2026-10-15T14:00:00.000000Z")
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_feed_txns").fetchone()[0] == 13
    stripe_pass(books, stripe, "2026-10-15T15:00:00.000000Z")
    stripe_pass(books, stripe, "2026-10-15T16:00:00.000000Z")
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_feed_txns").fetchone()[0] == 18
        assert _sum(c, "1050") == stripe.total() == 18_000
        assert _every_entry_balances(c)
    stripe_pass(books, stripe, "2026-10-15T17:00:00.000000Z")
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_feed_txns").fetchone()[0] == 18
        assert _sum(c, "1050") == 18_000


def test_reconnecting_the_same_stripe_account_reuses_its_money_account(books):
    stripe = FakeStripe()
    cid = connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe_pass(books, stripe)
    with books.transaction() as c:
        assert act(c, OWNER, "connection_disconnect", {"connection_id": cid}).status == "committed"
    connect_stripe(books, stripe)
    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_money_accounts WHERE provider='stripe'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM fin_feed_txns").fetchone()[0] == 1
        assert (_sum(c, "4000"), _sum(c, "1050")) == (-10_000, 9_680)
        assert _every_entry_balances(c)


def test_a_second_live_connection_to_the_same_stripe_account_is_refused(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    found, why = stripe_sync.probe(stripe)  # type: ignore[arg-type]
    assert not why, why
    with books.transaction() as c:
        done = act(c, OWNER, "connection_create", {"provider": "stripe", "credential_id": "cred-stripe-2",
                                                   "probe": found})
    assert done.status != "committed"
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_money_accounts WHERE provider='stripe'").fetchone()[0] == 1


def test_the_connections_route_takes_a_stripe_key_through_the_stripe_probe(api, monkeypatch):  # noqa: F811
    store = api.app.state.store
    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)
    probed: list[str] = []

    def stripe_probe(_store: Any, _vault: Any, credential_id: str) -> tuple[dict[str, object], str]:
        probed.append(credential_id)
        return {"account": ACCOUNT, "balances": [{"currency": "USD", "available": 0, "pending": 0}]}, ""

    def no_mercury(*_args: Any) -> tuple[dict[str, object], str]:
        pytest.fail("a Stripe key was tested with Mercury's probe")

    monkeypatch.setattr(FA, "stripe_probe", stripe_probe)
    monkeypatch.setattr(FA, "mercury_probe", no_mercury)
    res = api.post("/api/v2/finance/connections", json={"provider": "stripe", "credential_id": "cred-stripe"},
                   headers=headers())
    assert res.status_code == 200, res.text
    assert probed == ["cred-stripe"]
    assert res.json()["status"] == "committed", res.json()
    assert res.json()["result"]["money_accounts"][0]["kind"] == "stripe"


def test_a_webhook_with_a_bad_or_stale_signature_is_refused():
    body = json.dumps({"id": "evt_1", "type": "payout.paid", "data": {}}).encode()
    now = int(time.time())
    good = {"stripe-signature": F.sign(body, WEBHOOK_SECRET, now)}
    assert P.verify("stripe_finance", good, body, WEBHOOK_SECRET, now) == (True, "")
    assert P.verify("stripe_finance", {"stripe-signature": F.sign(body, "whsec_other_0123456789abcdef", now)},
                    body, WEBHOOK_SECRET, now)[0] is False
    assert P.verify("stripe_finance", {"stripe-signature": F.sign(body, WEBHOOK_SECRET, now - 3600)},
                    body, WEBHOOK_SECRET, now)[0] is False
    assert P.verify("stripe_finance", {}, body, WEBHOOK_SECRET, now)[0] is False
    assert "stripe_finance" in P.FINANCE


def test_a_webhook_is_kept_once_for_the_one_stripe_connection_and_then_reads_the_balance(books):
    event = {"id": "evt_stripe_1", "type": "charge.succeeded", "data": {}}
    with books.transaction() as c:
        assert sync.stripe_delivery(c, event, AT) is False          # no Stripe connection yet: not kept
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 4_000, 120, "Acme Buyer")
    with books.transaction() as c:
        assert sync.stripe_delivery(c, event, AT) is True
    with books.transaction() as c:
        assert sync.stripe_delivery(c, event, AT) is False          # a replay is not kept again
    stripe_pass(books, stripe)
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_inbox WHERE provider_event_id='evt_stripe_1'").fetchone()[0] == "done"
        assert _sum(c, "1050") == 3_880
