"""Stripe in the books (spec §9): the balance feed, its posting (sales, fees, refunds), an invoice paid through Stripe, a
payout matched once to its bank deposit, backfill that reads every page once, and webhook signatures. Runs on an
in-memory fake of Stripe's balance API and the Mercury fixtures the bank tests use."""
import json
import time
from datetime import datetime, timezone
from typing import Any

import pytest

from backend import trigger_presets as P
from backend.finance import banking, stripe_sync, sync
from backend.finance.context import load
from backend.finance.core import feeds as F
from backend.finance.mercury import Answer
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, FakeMercury, account, act, books, connect, finance_template, store)

DAY = int(datetime(2026, 10, 10, 12, tzinfo=timezone.utc).timestamp())
WEBHOOK_SECRET = "whsec_test_secret_0123456789abcdef"


class FakeStripe:
    """Stripe's balance API in memory: balance transactions are added oldest first and served newest first, `page` at
    a time, the way the real list pages."""

    def __init__(self, page: int = 100) -> None:
        self.txns: list[dict[str, Any]] = []
        self.page = page

    def add(self, kind: str, amount: int, fee: int = 0, name: str = "") -> str:
        tid = f"txn_{len(self.txns) + 1:04d}"
        self.txns.append({"id": tid, "object": "balance_transaction", "type": kind, "amount": amount, "fee": fee,
                          "net": amount - fee, "currency": "usd", "status": "available",
                          "created": DAY + 60 * len(self.txns), "description": kind,
                          "source": {"id": f"ch_{tid}", "object": "charge", "billing_details": {"name": name}}})
        return tid

    def balance(self) -> Answer:
        net = sum(int(t["net"]) for t in self.txns)
        return Answer(200, {"available": [{"amount": net, "currency": "usd"}], "pending": []})

    def transactions(self, starting_after: str | None = None, limit: int = 100) -> Answer:
        newest = list(reversed(self.txns))
        start = [t["id"] for t in newest].index(starting_after) + 1 if starting_after else 0
        page = newest[start:start + min(limit, self.page)]
        return Answer(200, {"object": "list", "data": page, "has_more": start + len(page) < len(newest)})


def connect_stripe(store: Any, fake: FakeStripe) -> str:
    found, why = stripe_sync.probe(fake)  # type: ignore[arg-type]
    assert not why, why
    with store.transaction() as c:
        done = act(c, OWNER, "connection_create", {"provider": "stripe", "credential_id": "cred-stripe",
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


def deposit(store: Any, amount: int, pid: str, day: str = "2026-10-12") -> str:
    """A Mercury deposit taken in the way the bank worker takes one in (banking.ingest, one transaction)."""
    with store.transaction() as c:
        ctx = load(c, AT)
        cid, acct = c.execute("SELECT connection_id, provider_account_id FROM fin_money_accounts "
                              "WHERE provider='mercury' AND kind='bank' ORDER BY created_at LIMIT 1").fetchone()
        fid = banking.ingest(ctx, str(cid), F.FeedTxn(provider_id=pid, account_id=str(acct), amount=amount,
                                                      txn_date=day, status=F.POSTED, kind="externalTransfer",
                                                      counterparty="STRIPE"))
    return str(fid)


def _sum(c: Any, code: str) -> int:
    """What the posted entries put on one account, by code."""
    return int(c.execute("SELECT COALESCE(SUM(l.amount),0) FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
                         "JOIN fin_accounts a ON a.id=l.account_id WHERE a.code=? AND e.status='posted'",
                         (code,)).fetchone()[0])


def _every_entry_balances(c: Any) -> bool:
    return int(c.execute("SELECT COALESCE(SUM(l.amount),0) FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
                         "WHERE e.status='posted'").fetchone()[0]) == 0


def _bank_code(c: Any) -> str:
    return str(c.execute("SELECT a.code FROM fin_money_accounts m JOIN fin_accounts a ON a.id=m.account_id "
                         "WHERE m.provider='mercury' AND m.kind='bank' ORDER BY m.created_at LIMIT 1").fetchone()[0])


def test_a_sale_is_income_less_a_fee_and_the_balance_gets_the_net(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    assert stripe_pass(books, stripe) == "ok"
    with books.read() as c:
        assert (_sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (-10_000, 320, 9_680)
        assert _every_entry_balances(c)


def test_a_refund_reverses_the_income_and_the_fee_stays_spent(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe.add("refund", -10_000)
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (0, 320, -320)
        assert _every_entry_balances(c)


@pytest.mark.parametrize("deposit_first", [False, True])
def test_a_payout_matches_its_bank_deposit_once(books, deposit_first):
    """Whichever side arrives second posts the one transfer: the balance and the bank each move once, and income is
    not counted again on the payout or the deposit."""
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
        assert c.execute("SELECT COUNT(*) FROM fin_entries WHERE memo LIKE 'Transfer:%' AND status='posted'"
                         ).fetchone()[0] == 1
        assert (_sum(c, "1050"), _sum(c, _bank_code(c)), _sum(c, "4000"), _sum(c, "6200")) == (0, 9_680, -10_000, 320)
        assert c.execute("SELECT COUNT(*) FROM fin_feed_txns WHERE status='posted' AND provider_id IN "
                         "('txn_0002','pay-1')").fetchone()[0] == 2
        assert _every_entry_balances(c)


def test_an_invoice_paid_through_stripe_settles_its_receivable_once(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    with books.transaction() as c:
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}
                           ).result["contact_id"])
        made = act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": "2026-10-01",
                                                "lines": [{"account_id": account(c, "4000"), "amount": 10_000}]})
        assert act(c, OWNER, "invoice_send", {"invoice_id": made.result["invoice_id"]}).status == "committed"
    stripe.add("charge", 10_000, 320, "Acme Retail")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert _sum(c, "1200") == 0                       # invoiced and paid: the receivable is gone
        assert _sum(c, "4000") == -10_000                 # the income is the invoice's, counted once
        assert (_sum(c, "6200"), _sum(c, "1050")) == (320, 9_680)
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
