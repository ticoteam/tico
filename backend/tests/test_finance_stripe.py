"""Stripe in the books (spec §9): the balance feed, its posting (sales, fees, refunds and disputes in proportion), a payout
in transit until a Stripe deposit clears it, an invoice paid through Stripe, backfill and catch-up that take each balance
transaction once across passes, the opening balance from a read the Stripe balance did not move through, a reconnect that reuses its money account, daily
summaries, and webhook signatures. Runs on an in-memory fake of Stripe's API and the Mercury fixtures the bank tests use."""

import json
import time
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any

import pytest

from backend import trigger_presets as P
from backend.finance import actions as FA
from backend.finance import banking, documents, stripe_post, stripe_sync, sync
from backend.finance.context import load
from backend.finance.store import banking as store_banking
from backend.finance.core import feeds as F
from backend.finance.core.errors import FinanceError
from backend.finance.mercury import Answer
from backend.tests.helpers.api import get, headers  # noqa: F401
from backend.tests.helpers.finance_fixtures import (
    AT,
    OWNER,
    FakeMercury,
    account,
    act,
    books,
    close_month,
    connect,
    finance_template,
    reopen_rows,
    store,
    store_keys,
)

DAY = int(datetime(2026, 10, 10, 12, tzinfo=timezone.utc).timestamp())
WEBHOOK_SECRET = "whsec_test_secret_0123456789abcdef"
ACCOUNT = "acct_test000001"
STALE_AT = "2026-10-20T12:00:00.000000Z"  # seven days past the first charges' day, plus the margin


def _source(kind: str, tid: str, link: str, name: str) -> dict[str, Any]:
    """The object a balance transaction came from, as the expanded list shows it."""
    if kind in ("charge", "payment"):
        return {"id": f"ch_{tid}", "object": "charge", "billing_details": {"name": name}}
    if kind in ("refund", "payment_refund", "payment_failure_refund", "refund_failure"):
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
        self.reads = 0  # balance reads so far
        self.race: Callable[[], None] | None = None  # runs once, on read number `race_on`, before or after the sum
        self.race_on = 0
        self.race_when = "before"

    def add(
        self,
        kind: str,
        amount: int,
        fee: int = 0,
        name: str = "",
        link: str = "",
        created: int | None = None,
        source: str = "",
    ) -> str:
        """`source` is the refund a refund failure undoes. Left empty, a refund failure names the latest refund of its
        charge, as Stripe does."""
        tid = f"txn_{len(self.txns) + 1:04d}"
        if kind == "refund_failure" and not source:
            source = next(
                (
                    str(t["source"]["id"])
                    for t in reversed(self.txns)
                    if t["type"] == "refund" and t["source"]["charge"] == link
                ),
                "",
            )
        src = {"id": source, "object": "refund", "charge": link} if source else _source(kind, tid, link, name)
        self.txns.append(
            {
                "id": tid,
                "object": "balance_transaction",
                "type": kind,
                "amount": amount,
                "fee": fee,
                "net": amount - fee,
                "currency": "usd",
                "status": "available",
                "created": created if created is not None else DAY + 60 * (len(self.txns) + 1),
                "description": kind,
                "source": src,
            }
        )
        return tid

    def account(self) -> Answer:
        return Answer(200, {"object": "account", "id": ACCOUNT})

    def balance(self) -> Answer:
        self.reads += 1
        race = self.race if self.reads == self.race_on else None
        if race is not None and self.race_when == "before":
            race()
        net = self.start + sum(int(t["net"]) for t in self.txns)
        if race is not None and self.race_when == "after":
            race()
        return Answer(200, {"available": [{"amount": net, "currency": "usd"}], "pending": []})

    def transactions(
        self, starting_after: str | None = None, limit: int = 100, created_gte: int | None = None
    ) -> Answer:
        newest = [t for t in reversed(self.txns) if created_gte is None or int(t["created"]) >= created_gte]
        start = [t["id"] for t in newest].index(starting_after) + 1 if starting_after else 0
        page = newest[start : start + min(limit, self.page)]
        return Answer(200, {"object": "list", "data": page, "has_more": start + len(page) < len(newest)})

    def total(self) -> int:
        return self.start + sum(int(t["net"]) for t in self.txns)


def connect_stripe(store: Any, fake: FakeStripe, credential: str = "cred-stripe") -> str:
    found, why = stripe_sync.probe(fake)  # type: ignore[arg-type]
    assert not why, why
    with store.transaction() as c:
        done = act(c, OWNER, "connection_create", {"provider": "stripe", "credential_id": credential, "probe": found})
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
        cid, acct = c.execute(
            "SELECT connection_id, provider_account_id FROM fin_money_accounts "
            "WHERE provider='mercury' AND kind='bank' ORDER BY created_at LIMIT 1"
        ).fetchone()
        fid = banking.ingest(
            ctx,
            str(cid),
            F.FeedTxn(
                provider_id=pid,
                account_id=str(acct),
                amount=amount,
                txn_date=day,
                status=F.POSTED,
                kind="externalTransfer",
                counterparty=counterparty,
            ),
        )
    return str(fid)


def _sum(c: Any, code: str) -> int:
    """What the entries put on one account, by code. A reversed entry and its reversal both count, so each pair nets
    to nothing; the status filter would keep only the reversal."""
    return int(
        c.execute(
            "SELECT COALESCE(SUM(l.amount),0) FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id WHERE a.code=?",
            (code,),
        ).fetchone()[0]
    )


def _named_sum(c: Any, name: str) -> int:
    """What the entries put on one account, by its name (the transit account is found by name)."""
    return int(
        c.execute(
            "SELECT COALESCE(SUM(l.amount),0) FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id WHERE a.name=?",
            (name,),
        ).fetchone()[0]
    )


def _every_entry_balances(c: Any) -> bool:
    return int(c.execute("SELECT COALESCE(SUM(amount),0) FROM fin_lines").fetchone()[0]) == 0


def _bank_code(c: Any) -> str:
    return str(
        c.execute(
            "SELECT a.code FROM fin_money_accounts m JOIN fin_accounts a ON a.id=m.account_id "
            "WHERE m.provider='mercury' AND m.kind='bank' ORDER BY m.created_at LIMIT 1"
        ).fetchone()[0]
    )


def _feed(c: Any, provider_id: str) -> Any:
    return c.execute("SELECT * FROM fin_feed_txns WHERE provider_id=?", (provider_id,)).fetchone()


def _asked(c: Any, provider_id: str, period: str = "") -> int:
    """How many times a person was asked about this Stripe row (for its key, `period`)."""
    row = _feed(c, provider_id)
    return int(
        c.execute(
            "SELECT COUNT(*) FROM fin_events WHERE name='finance.feed.needs_category' AND subject=? AND period_key=?",
            (str(row["id"]), period),
        ).fetchone()[0]
    )


def _full_history(books: Any, cid: str) -> None:
    """Makes a connection one from before history_from: it reads its whole history."""
    with books.transaction() as c:
        settings = json.loads(c.execute("SELECT settings FROM fin_connections WHERE id=?", (cid,)).fetchone()[0])
        settings.pop("history_from")
        c.execute("UPDATE fin_connections SET settings=? WHERE id=?", (json.dumps(settings), cid))


def _kind_asks(c: Any, kind: str) -> int:
    """How many asks a Stripe kind Finance does not map raised on the Stripe money account."""
    return int(
        c.execute(
            "SELECT COUNT(*) FROM fin_events WHERE name='finance.feed.needs_category' AND period_key=? AND subject IN "
            "(SELECT id FROM fin_money_accounts WHERE kind='stripe')",
            (f"kind:{kind}",),
        ).fetchone()[0]
    )


def _entries(c: Any, prefix: str) -> int:
    return int(
        c.execute(
            "SELECT COUNT(*) FROM fin_entries WHERE status='posted' AND source_id LIKE ?", (prefix + "%",)
        ).fetchone()[0]
    )


def _customer(store: Any, name: str) -> str:
    with store.transaction() as c:
        return str(act(c, OWNER, "contact_create", {"name": name, "kind": "customer"}).result["contact_id"])


def _invoice(store: Any, customer: str, amount: int) -> str:
    with store.transaction() as c:
        made = act(
            c,
            OWNER,
            "invoice_create",
            {
                "customer_id": customer,
                "invoice_date": "2026-10-01",
                "lines": [{"account_id": account(c, "4000"), "amount": amount}],
            },
        )
        assert act(c, OWNER, "invoice_send", {"invoice_id": made.result["invoice_id"]}).status == "committed"
    return str(made.result["invoice_id"])


def _stripe_money(store: Any) -> str:
    with store.read() as c:
        return str(c.execute("SELECT id FROM fin_money_accounts WHERE provider='stripe'").fetchone()[0])


def _invoice_row(store: Any, iid: str) -> tuple[int, str]:
    with store.read() as c:
        row = c.execute("SELECT amount_paid, status FROM fin_invoices WHERE id=?", (iid,)).fetchone()
    return int(row[0]), str(row[1])


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


def test_a_dispute_reverses_its_sale_and_adjustments_naming_no_sale_go_to_suspense_asked_once(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe.add("adjustment", -10_000, link="ch_txn_0001")
    stripe.add("adjustment", -500)
    stripe.add("adjustment", -200)
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (0, 320, -1_020)
        assert _named_sum(c, "Stripe suspense") == 700  # the balance still ties: the unclear rows wait on suspense
        assert _feed(c, "txn_0003")["entry_id"] is not None and _feed(c, "txn_0004")["entry_id"] is not None
        assert _kind_asks(c, "adjustment") == 1
        assert _asked(c, "txn_0003") == 0 and _asked(c, "txn_0004") == 0
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


def test_a_refund_that_fails_restores_what_it_gave_back_and_not_a_share_of_the_others(books):
    """A $100 sale: refund A of $50 before anything settled it, then $50 applied by hand to a $50 invoice, then refund B
    of $50, which gives the invoice its $50 back. Refund A fails. It gave nothing back, so the invoice still owes its
    $50; the restore takes A's own record, not a share of what the charge's refunds gave back in all."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    iid = _invoice(books, _customer(books, "Acme Retail"), 5_000)
    stripe.add("charge", 10_000, 0, "Acme Shop")
    stripe_pass(books, stripe)
    refund_a = stripe.add("refund", -5_000, link="ch_txn_0001")
    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    with books.transaction() as c:
        sale = str(_feed(c, "txn_0001")["id"])
        made = act(c, OWNER, "invoice_record_payment", {"invoice_id": iid, "feed_id": sale, "amount": 5_000})
        assert made.status == "committed", made
    stripe.add("refund", -5_000, link="ch_txn_0001")
    stripe_pass(books, stripe, "2026-10-15T14:00:00.000000Z")
    assert _invoice_row(books, iid) == (0, "sent")
    stripe.add("refund_failure", 5_000, link="ch_txn_0001", source=f"re_{refund_a}")
    stripe_pass(books, stripe, "2026-10-15T15:00:00.000000Z")
    assert _invoice_row(books, iid) == (0, "sent")
    with books.read() as c:
        assert (_sum(c, "1200"), _sum(c, "4000"), _sum(c, "1050")) == (5_000, -10_000, 5_000)
        assert _every_entry_balances(c)


def test_a_second_failure_of_the_same_refund_changes_nothing(books):
    """The refund-failure case above, and then refund A fails a second time. It was undone once already, so the repeat
    posts nothing: the books and the invoice stay where the first failure left them."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    iid = _invoice(books, _customer(books, "Acme Retail"), 5_000)
    stripe.add("charge", 10_000, 0, "Acme Shop")
    stripe_pass(books, stripe)
    refund_a = stripe.add("refund", -5_000, link="ch_txn_0001")
    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    with books.transaction() as c:
        sale = str(_feed(c, "txn_0001")["id"])
        made = act(c, OWNER, "invoice_record_payment", {"invoice_id": iid, "feed_id": sale, "amount": 5_000})
        assert made.status == "committed", made
    stripe.add("refund", -5_000, link="ch_txn_0001")
    stripe_pass(books, stripe, "2026-10-15T14:00:00.000000Z")
    stripe.add("refund_failure", 5_000, link="ch_txn_0001", source=f"re_{refund_a}")
    stripe_pass(books, stripe, "2026-10-15T15:00:00.000000Z")
    with books.read() as c:
        before = (_sum(c, "1200"), _sum(c, "4000"), _sum(c, "1050"))
    repeat = stripe.add("refund_failure", 5_000, link="ch_txn_0001", source=f"re_{refund_a}")
    stripe_pass(books, stripe, "2026-10-15T16:00:00.000000Z")
    assert _invoice_row(books, iid) == (0, "sent")
    with books.read() as c:
        assert (_sum(c, "1200"), _sum(c, "4000"), _sum(c, "1050")) == before
        assert _feed(c, repeat)["status"] == "ignored"
        assert _every_entry_balances(c)


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


@pytest.mark.parametrize("deposit_first", [False, True])
def test_an_instant_payout_puts_what_the_bank_gets_in_transit_and_its_fee_to_fees(books, deposit_first):
    """An instant payout's balance transaction moves its gross plus Stripe's fee out of the balance; the bank gets the
    gross. The gross is in transit and clears the smaller deposit; the fee is a payment processing fee."""
    connect(books, FakeMercury())
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 110_000, 0, "Acme Buyer")
    stripe.add("payout", -100_000, 1_500)  # net -101,500: what left the balance
    if deposit_first:
        deposit(books, 100_000, "pay-instant")
        stripe_pass(books, stripe)
    else:
        stripe_pass(books, stripe)
        deposit(books, 100_000, "pay-instant")
    with books.read() as c:
        assert _named_sum(c, "Stripe payouts in transit") == 0
        assert (_sum(c, "1050"), _sum(c, _bank_code(c)), _sum(c, "6200")) == (8_500, 100_000, 1_500)
        assert _entries(c, "stripe:clear:") == 1
        assert _every_entry_balances(c)


def test_two_stripe_deposits_of_an_instant_payouts_gross_ask_which_one(books):
    connect(books, FakeMercury())
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 110_000, 0, "Acme Buyer")
    stripe.add("payout", -100_000, 1_500)
    deposit(books, 100_000, "pay-x")
    deposit(books, 100_000, "pay-y", day="2026-10-11")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert _asked(c, "txn_0002", "ambiguous") == 1 and _entries(c, "stripe:clear:") == 0


def test_a_failed_instant_payout_returns_its_gross_to_transit_and_its_fee_off_fees(books):
    """Stripe returns a failed instant payout's gross and fee in one balance transaction: transit gives back the gross
    it holds and the fee is no longer an expense."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 110_000, 0, "Acme Buyer")
    stripe.add("payout", -100_000, 1_500)
    stripe.add("payout_failure", 101_500, link="po_txn_0002")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "1050"), _named_sum(c, "Stripe payouts in transit"), _sum(c, "6200")) == (110_000, 0, 0)
        assert _every_entry_balances(c)


def test_a_payout_never_clears_a_brex_deposit_whose_sign_is_still_unchecked(books):
    """A Brex cash row waits unposted until its connection's sign is checked: its amount is not trusted, so a payout
    never takes it as its deposit."""
    from backend.tests.test_finance_brex import CASH, FakeBrex, brex_pass, cash_txn, connect_brex

    brex = FakeBrex()
    connect_brex(books, brex)
    assert brex_pass(books, brex) == "ok"
    with books.transaction() as c:
        c.execute(
            "UPDATE fin_connections SET settings=json_set(settings, '$.sign', 'unverified') WHERE provider='brex'"
        )
    row = cash_txn("cash_stripe", "PAYMENT", 9_680, day="2026-10-12")
    row["description"] = "STRIPE TRANSFER"
    brex.cash_txns[CASH].append(row)
    assert brex_pass(books, brex, "2026-10-15T13:00:00.000000Z") == "ok"
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe.add("payout", -9_680)
    stripe_pass(books, stripe, "2026-10-15T14:00:00.000000Z")
    with books.read() as c:
        assert _feed(c, "cash_stripe")["entry_id"] is None
        assert _named_sum(c, "Stripe payouts in transit") == 9_680 and _entries(c, "stripe:clear:") == 0


def test_a_bank_debit_payment_that_fails_after_posting_reverses_its_sale(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("payment", 10_000, 80, "Acme Buyer")
    stripe.add("payment_failure_refund", -10_000, link="ch_txn_0001")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (0, 80, -80)
        assert _named_sum(c, "Stripe suspense") == 0 and _kind_asks(c, "payment_failure_refund") == 0
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
    """On a connection that reads its whole history (one made before history_from), a sale that never came is asked
    about."""
    stripe = FakeStripe()
    _full_history(books, connect_stripe(books, stripe))
    stripe.add("refund", -10_000, link="ch_missing")
    stripe_pass(books, stripe)
    stripe_pass(books, stripe, STALE_AT)
    with books.read() as c:
        assert _sum(c, "1050") == 0
        assert _asked(c, "txn_0001", "stale") == 1


def test_a_refund_of_a_sale_before_history_from_posts_against_revenue_once_the_backfill_ended(books):
    """A history cut at history_from: a refund of a sale before that day never finds its sale. It waits through the
    backfill (its sale may be on an older page), then posts against Revenue with no ask: the sale is in the opening."""
    stripe = FakeStripe(start=50_000)
    connect_stripe(books, stripe)
    stripe.add("refund", -10_000, link="ch_before_history")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert _feed(c, "txn_0001")["entry_id"] is None
    stripe_pass(books, stripe, STALE_AT)
    stripe_pass(books, stripe, "2026-10-20T13:00:00.000000Z")
    with books.read() as c:
        assert _feed(c, "txn_0001")["entry_id"] is not None
        assert (_sum(c, "4000"), _sum(c, "1050")) == (10_000, stripe.total())
        assert _asked(c, "txn_0001", "stale") == 0
        assert _every_entry_balances(c)


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


@pytest.mark.parametrize("when", ["before", "after"])
def test_the_opening_balance_counts_each_transaction_once_when_a_charge_lands_during_the_cut(books, when):
    """A charge created while the cut's balance is read, before or after the sum: the balance holds it or does not, and
    the opening balance (the balance less every row taken in) counts it once either way."""
    stripe = FakeStripe(start=7_000)
    connect_stripe(books, stripe)
    for i in range(3):
        stripe.add("charge", 1_000, 0, "Acme Buyer", created=DAY + 60 * (i + 1))
    stripe.reads = 0
    stripe.race_on, stripe.race_when = 2, when  # read 1 is the pass's balance, read 2 is the cut's
    stripe.race = lambda: stripe.add("charge", 500, 0, "Acme Late", created=DAY + 2_000)
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
        settings = json.loads(c.execute("SELECT settings FROM fin_connections WHERE provider='stripe'").fetchone()[0])
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


@pytest.mark.parametrize("failing", ["txn_0006", "txn_0007", "txn_0008", "txn_0009"])
@pytest.mark.parametrize("catch_up_pages", [1, 2, 10])
def test_a_catch_up_pass_that_fails_mid_walk_takes_every_row_on_the_retry(books, monkeypatch, failing, catch_up_pages):
    """One row of the new batch fails once. The rows older than it, and the rows newer, all come in on the retry."""
    stripe = FakeStripe(page=2)
    connect_stripe(books, stripe)
    for i in range(5):
        stripe.add("charge", 1_000, 0, f"Buyer {i}")
    stripe_pass(books, stripe, AT)
    for i in range(4):
        stripe.add("charge", 1_000, 0, f"New {i}")
    monkeypatch.setattr(stripe_sync, "CATCH_UP_PAGES", catch_up_pages)
    real_ingest = banking.ingest
    failed: list[str] = []

    def flaky(ctx: Any, cid: str, t: F.FeedTxn) -> Any:
        if t.provider_id == failing and not failed:
            failed.append(t.provider_id)
            raise RuntimeError("the write failed once")
        return real_ingest(ctx, cid, t)

    monkeypatch.setattr(banking, "ingest", flaky)
    for at in ("2026-10-15T13:00:00.000000Z", "2026-10-15T14:00:00.000000Z", "2026-10-15T15:00:00.000000Z"):
        stripe_pass(books, stripe, at)
    assert failed == [failing]
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_feed_txns").fetchone()[0] == 9
        assert _sum(c, "1050") == stripe.total() == 9_000
        assert _every_entry_balances(c)


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
        done = act(
            c, OWNER, "connection_create", {"provider": "stripe", "credential_id": "cred-stripe-2", "probe": found}
        )
    assert done.status != "committed"
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_money_accounts WHERE provider='stripe'").fetchone()[0] == 1


def test_the_connections_route_takes_a_stripe_key_through_the_stripe_probe(api, monkeypatch):  # noqa: F811
    store = api.app.state.store
    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)
        store_keys(c, "cred-stripe")
    probed: list[str] = []

    def stripe_probe(_store: Any, _vault: Any, credential_id: str) -> tuple[dict[str, object], str]:
        probed.append(credential_id)
        return {"account": ACCOUNT, "balances": [{"currency": "USD", "available": 0, "pending": 0}]}, ""

    def no_mercury(*_args: Any) -> tuple[dict[str, object], str]:
        pytest.fail("a Stripe key was tested with Mercury's probe")

    monkeypatch.setattr(FA, "stripe_probe", stripe_probe)
    monkeypatch.setattr(FA, "mercury_probe", no_mercury)
    with store.read() as c:
        lines = {
            key: str(c.execute("SELECT id FROM fin_accounts WHERE type=? AND active=1 LIMIT 1", (kind,)).fetchone()[0])
            for key, kind in (("transfer_account_id", "expense"), ("capital_account_id", "liability"))
        }
    body = {"provider": "stripe", "credential_id": "cred-stripe", **lines}
    res = api.post("/api/v2/finance/connections", json=body, headers=headers())
    assert res.status_code == 200, res.text
    assert probed == ["cred-stripe"]
    assert res.json()["status"] == "committed", res.json()
    assert res.json()["result"]["money_accounts"][0]["kind"] == "stripe"
    with store.read() as c:
        settings = _settings(c, str(res.json()["result"]["connection_id"]))
    assert {k: settings[k] for k in lines} == lines


def test_the_connections_route_refuses_stripe_lines_for_another_provider_before_any_probe(api, monkeypatch):  # noqa: F811
    store = api.app.state.store
    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)
        store_keys(c, "cred-stripe")

    probed: list[str] = []

    def no_probe(_store: Any, _vault: Any, credential_id: str) -> tuple[dict[str, object], str]:
        probed.append(credential_id)
        return {}, "probed"

    for name in ("mercury_probe", "stripe_probe", "brex_probe"):
        monkeypatch.setattr(FA, name, no_probe)
    for provider in ({}, {"provider": "mercury"}, {"provider": "brex"}):
        body = {**provider, "credential_id": "cred-stripe", "transfer_account_id": "acct-made-up"}
        res = api.post("/api/v2/finance/connections", json=body, headers=headers())
        assert res.status_code == 422 and "transfer_account_id" in res.text, (provider, res.text)
    assert probed == []  # the key never reached a probe


def test_a_marked_payment_paid_through_stripe_still_posts_the_fee_so_the_balance_nets(books):
    """An invoice marked paid from the Stripe balance waits for its sale. The sale's fee posts too, so the balance holds
    the net, and the invoice is paid in full."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    iid = _invoice(books, _customer(books, "Acme Retail"), 10_000)
    with books.transaction() as c:
        made = act(
            c,
            OWNER,
            "invoice_record_payment",
            {"invoice_id": iid, "money_account_id": _stripe_money(books), "date": "2026-10-10"},
        )
        assert made.status == "committed", made
    stripe.add("charge", 10_000, 320, "Acme Retail")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "1050"), _sum(c, "6200"), _sum(c, "4000"), _sum(c, "1200")) == (9_680, 320, -10_000, 0)
        assert _every_entry_balances(c)
    assert _invoice_row(books, iid) == (10_000, "paid")


def test_a_refund_of_an_invoice_paid_sale_takes_its_share_off_what_the_invoice_has_received(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    iid = _invoice(books, _customer(books, "Acme Retail"), 10_000)
    stripe.add("charge", 10_000, 320, "Acme Retail")
    stripe.add("refund", -4_000, link="ch_txn_0001")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "1200"), _sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (4_000, -10_000, 320, 5_680)
    assert _invoice_row(books, iid) == (6_000, "partial")


def test_a_stripe_sale_never_settles_an_invoice_at_its_net_because_bank_matching_does_not_see_it(books):
    """A $100 sale with a $3 fee and an open invoice of its net, $97, from the customer the charge names. A Stripe sale
    matches its gross only, so no invoice of the net is paid and the invoice stays owed."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    iid = _invoice(books, _customer(books, "Acme Retail"), 9_700)
    stripe.add("charge", 10_000, 300, "Acme Retail")
    stripe_pass(books, stripe)
    assert _invoice_row(books, iid) == (0, "sent")
    with books.read() as c:
        assert _every_entry_balances(c)


def test_feed_paid_refuses_a_stripe_row_rather_than_settling_it_at_its_net(books):
    """feed_paid is the bank-row path. A Stripe row handed to it is refused before anything settles."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 300, "Acme Retail")
    stripe_pass(books, stripe)
    with books.transaction() as c:
        row = _feed(c, "txn_0001")
        with pytest.raises(FinanceError) as refused:
            documents.feed_paid(load(c, AT), row, "invoice", {"id": "inv_none", "customer_id": ""}, "entry_none")
        assert refused.value.code == "invalid"
    with books.read() as c:
        assert _sum(c, "1200") == 0


def test_a_held_sale_applied_in_full_by_hand_cannot_be_categorized_again(books):
    """A $100 sale with a $3 fee applied in full to a $100 invoice: the books hold it in AR, so categorizing it again
    would post its money a second time. The row is refused as applied in full and nothing posts."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    iid = _invoice(books, _customer(books, "Acme Retail"), 10_000)
    with books.transaction() as c:
        rule = {
            "kind": "posting",
            "name": "Hold",
            "when": {"counterparty": "Acme Hold"},
            "outcome": {"outcome": "ask"},
            "examples": [{"input": {"counterparty": "Acme Hold"}, "expect": "ask"}],
        }
        assert act(c, OWNER, "rule_create", rule).status == "committed"
    stripe.add("charge", 10_000, 300, "Acme Hold")
    stripe_pass(books, stripe)
    with books.transaction() as c:
        held = str(_feed(c, "txn_0001")["id"])
        made = act(c, OWNER, "invoice_record_payment", {"invoice_id": iid, "feed_id": held, "amount": 10_000})
        assert made.status == "committed", made
    with books.transaction() as c:
        again = act(c, OWNER, "categorize", {"feed_id": held, "account_id": account(c, "4000")})
        assert again.status != "committed" and again.code == "conflict"
    with books.read() as c:
        assert (_sum(c, "1200"), _sum(c, "4000"), _sum(c, "6200")) == (0, -10_000, 300)
        assert _every_entry_balances(c)


def test_a_refund_gives_back_no_more_than_each_settlement_received_and_the_rest_is_a_plain_refund(books):
    """A $100 sale a person applied $50 of to an invoice: the full refund gives the invoice its $50 back, and the rest
    of the refund is paid out of the customer's credit. The invoice never owes a negative amount."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    iid = _invoice(books, _customer(books, "Acme Retail"), 5_000)
    with books.transaction() as c:
        rule = {
            "kind": "posting",
            "name": "Hold",
            "when": {"counterparty": "Acme Hold"},
            "outcome": {"outcome": "ask"},
            "examples": [{"input": {"counterparty": "Acme Hold"}, "expect": "ask"}],
        }
        assert act(c, OWNER, "rule_create", rule).status == "committed"
    stripe.add("charge", 10_000, 0, "Acme Hold")
    stripe_pass(books, stripe)
    with books.transaction() as c:
        held = str(_feed(c, "txn_0001")["id"])
        made = act(c, OWNER, "invoice_record_payment", {"invoice_id": iid, "feed_id": held, "amount": 5_000})
        assert made.status == "committed", made
    stripe.add("refund", -10_000, link="ch_txn_0001")
    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    with books.read() as c:
        assert (_sum(c, "1200"), _sum(c, "1050")) == (5_000, 0)
        assert _every_entry_balances(c)
    assert _invoice_row(books, iid) == (0, "sent")


def test_separate_refunds_of_a_sale_that_settled_two_invoices_leave_each_invoice_at_exactly_zero(books):
    """A $101 sale settling invoices of $50 and $51, refunded in two parts: the rounding of the first refund is
    carried into the second, so both invoices end at 0, not at a cent either way."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    customer = _customer(books, "Acme Retail")
    first, second = _invoice(books, customer, 5_000), _invoice(books, customer, 5_100)
    money = _stripe_money(books)
    with books.transaction() as c:
        for iid in (first, second):
            paid = act(
                c, OWNER, "invoice_record_payment", {"invoice_id": iid, "money_account_id": money, "date": "2026-10-10"}
            )
            assert paid.status == "committed", paid
    stripe.add("charge", 10_100, 0, "Acme Retail")
    stripe_pass(books, stripe)
    stripe.add("refund", -5_000, link="ch_txn_0001")
    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    stripe.add("refund", -5_100, link="ch_txn_0001")
    stripe_pass(books, stripe, "2026-10-16T13:00:00.000000Z")
    assert _invoice_row(books, first) == (0, "sent")
    assert _invoice_row(books, second) == (0, "sent")
    with books.read() as c:
        assert _every_entry_balances(c)
        assert _sum(c, "1050") == 0


def test_a_day_of_many_sales_under_a_split_rule_posts_as_journals_under_the_line_cap(books):
    """100 buyers on one day under a five-account split: their counter lines alone reach the journal cap with the fees
    and the balance, so the day posts as several balanced journals, and every row takes its entry."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    with books.transaction() as c:
        accounts = [account(c, "4000")] + [
            str(
                act(c, OWNER, "account_create", {"code": f"41{i}0", "name": f"Line {i}", "type": "income"}).result[
                    "account_id"
                ]
            )
            for i in range(1, 5)
        ]
        split = [{"account_id": a, "share": 2_000} for a in accounts]
        rule = {
            "kind": "posting",
            "name": "Split",
            "when": {"direction": "in"},
            "outcome": {"outcome": "post", "split": split},
            "examples": [{"input": {"direction": "in"}, "expect": "post"}],
        }
        assert act(c, OWNER, "rule_create", rule).status == "committed"
    for n in range(100):
        stripe.add("charge", 1_000, 30, f"Buyer {n:03d}")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM fin_feed_txns WHERE kind='charge' AND status='posted'").fetchone()[0] == 100
        )
        sizes = [
            int(r[0])
            for r in c.execute(
                "SELECT COUNT(*) FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
                "WHERE e.status='posted' GROUP BY e.id"
            )
        ]
        assert len(sizes) == 2 and max(sizes) <= 500, sizes
        assert _every_entry_balances(c)
        assert (_sum(c, "1050"), _sum(c, "6200")) == (97_000, 3_000)


def test_summarized_sales_reconcile_to_the_statement_one_row_at_a_time(books):
    """Three sales in one day's summary: each row clears for its own net, so the statement of their nets reconciles."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 10_000, 320, "Acme Buyer")
    stripe.add("charge", 5_000, 160, "Acme Buyer")
    stripe.add("charge", 2_000, 60, "Acme Buyer")
    stripe_pass(books, stripe)
    with books.transaction() as c:
        mid = _stripe_money(books)
        ids = [
            str(r[0])
            for r in c.execute("SELECT id FROM fin_feed_txns WHERE money_account_id=? ORDER BY provider_id", (mid,))
        ]
        done = act(
            c,
            OWNER,
            "reconcile",
            {"money_account_id": mid, "statement_end": "2026-10-14", "statement_balance": 16_460, "cleared": ids},
        )
        assert done.status == "committed", done
        assert done.result["status"] == "done" and done.result["difference"] == 0, done.result


def test_a_posting_rule_sets_the_sale_account_in_the_summary_and_a_refund_reverses_that_account(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    with books.transaction() as c:
        services = str(
            act(c, OWNER, "account_create", {"code": "4100", "name": "Services", "type": "income"}).result["account_id"]
        )
        rule = {
            "kind": "posting",
            "name": "Cloud",
            "when": {"counterparty": "Acme Cloud"},
            "outcome": {"outcome": "post", "account_id": services},
            "examples": [{"input": {"counterparty": "Acme Cloud"}, "expect": "post"}],
        }
        assert act(c, OWNER, "rule_create", rule).status == "committed"
    stripe.add("charge", 10_000, 320, "Acme Cloud")
    stripe.add("charge", 5_000, 160, "Acme Buyer")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "4100"), _sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (-10_000, -5_000, 480, 14_520)
        assert _every_entry_balances(c)
    stripe.add("refund", -10_000, link="ch_txn_0001")
    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    with books.read() as c:
        assert (_sum(c, "4100"), _sum(c, "4000"), _sum(c, "6200"), _sum(c, "1050")) == (0, -5_000, 480, 4_520)
        assert _every_entry_balances(c)


def test_a_sale_a_posting_rule_asks_about_is_held_and_a_person_is_asked(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    with books.transaction() as c:
        rule = {
            "kind": "posting",
            "name": "Hold",
            "when": {"counterparty": "Acme Hold"},
            "outcome": {"outcome": "ask"},
            "examples": [{"input": {"counterparty": "Acme Hold"}, "expect": "ask"}],
        }
        assert act(c, OWNER, "rule_create", rule).status == "committed"
    stripe.add("charge", 3_000, 90, "Acme Hold")
    stripe_pass(books, stripe)
    with books.read() as c:
        assert _feed(c, "txn_0001")["status"] == "new"
        assert _asked(c, "txn_0001") == 1
        assert (_sum(c, "1050"), _sum(c, "4000")) == (0, 0)


def test_a_held_sale_a_person_categorizes_posts_its_gross_income_and_its_fee_like_an_unheld_sale(books):
    """The held row is the net. Categorizing it posts the gross to the chosen account and the fee to fees, the same
    entry an unheld sale of that amount would post, so the balance nets."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    with books.transaction() as c:
        rule = {
            "kind": "posting",
            "name": "Hold",
            "when": {"counterparty": "Acme Hold"},
            "outcome": {"outcome": "ask"},
            "examples": [{"input": {"counterparty": "Acme Hold"}, "expect": "ask"}],
        }
        assert act(c, OWNER, "rule_create", rule).status == "committed"
    stripe.add("charge", 3_000, 90, "Acme Hold")
    stripe_pass(books, stripe)
    with books.transaction() as c:
        held = str(_feed(c, "txn_0001")["id"])
        done = act(c, OWNER, "categorize", {"feed_id": held, "account_id": account(c, "4000")})
        assert done.status == "committed", done
    with books.read() as c:
        assert (_sum(c, "1050"), _sum(c, "6200"), _sum(c, "4000")) == (2_910, 90, -3_000)
        assert _every_entry_balances(c)


def test_a_held_sale_applied_by_hand_to_an_invoice_posts_its_gross_and_fee_so_a_refund_nets_the_receivable(books):
    """A $100 sale with a $3 fee, held, applied by hand to a $100 invoice: the receivable clears on the gross and the fee
    posts as an expense, the same as an automatic match. A full refund reopens the invoice; the fee stays spent."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    iid = _invoice(books, _customer(books, "Acme Retail"), 10_000)
    with books.transaction() as c:
        rule = {
            "kind": "posting",
            "name": "Hold",
            "when": {"counterparty": "Acme Hold"},
            "outcome": {"outcome": "ask"},
            "examples": [{"input": {"counterparty": "Acme Hold"}, "expect": "ask"}],
        }
        assert act(c, OWNER, "rule_create", rule).status == "committed"
    stripe.add("charge", 10_000, 300, "Acme Hold")
    stripe_pass(books, stripe)
    with books.transaction() as c:
        held = str(_feed(c, "txn_0001")["id"])
        made = act(c, OWNER, "invoice_record_payment", {"invoice_id": iid, "feed_id": held, "amount": 10_000})
        assert made.status == "committed", made
    with books.read() as c:
        assert (_sum(c, "1200"), _sum(c, "6200"), _sum(c, "1050")) == (0, 300, 9_700)
        assert _every_entry_balances(c)
    assert _invoice_row(books, iid) == (10_000, "paid")
    stripe.add("refund", -10_000, link="ch_txn_0001")
    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    with books.read() as c:
        assert (_sum(c, "1200"), _sum(c, "6200"), _sum(c, "1050")) == (10_000, 300, -300)
        assert _every_entry_balances(c)
    assert _invoice_row(books, iid) == (0, "sent")


def test_a_stripe_charge_with_a_fee_is_linked_to_marked_payments_that_add_up_to_its_gross(books):
    """Four invoices of one customer, $60, $40, $60 and $40, all marked paid through Stripe, and a $100 charge with a $3.20
    fee that names none of them: the match asks. A person links the $60 and $40 marks to it. They add up to the gross, so
    the fee posts, the balance gets the net, and a full refund gives both invoices back."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    money = _stripe_money(books)
    customer = _customer(books, "Acme Retail")
    iids = [_invoice(books, customer, amount) for amount in (6_000, 4_000, 6_000, 4_000)]
    with books.transaction() as c:
        for iid in iids:
            paid = act(
                c, OWNER, "invoice_record_payment", {"invoice_id": iid, "money_account_id": money, "date": "2026-10-10"}
            )
            assert paid.status == "committed", paid
    stripe.add("charge", 10_000, 320, "Unknown Shopper")
    stripe_pass(books, stripe)
    with books.transaction() as c:
        row = str(_feed(c, "txn_0001")["id"])
        assert _feed(c, "txn_0001")["status"] == "new"
        pair = [
            str(r[0])
            for r in c.execute(
                "SELECT id FROM fin_settlements WHERE to_id IN (?, ?) AND from_kind='expected' AND voided_at IS NULL "
                "ORDER BY amount DESC",
                (iids[0], iids[1]),
            ).fetchall()
        ]
        linked = act(c, OWNER, "feed_match", {"feed_id": row, "settlement_ids": pair})
        assert linked.status == "committed", linked
    with books.read() as c:
        assert (_sum(c, "1050"), _sum(c, "6200"), _sum(c, "1200")) == (19_680, 320, 0)
        assert _every_entry_balances(c)
    assert _invoice_row(books, iids[0]) == (6_000, "paid")
    assert _invoice_row(books, iids[1]) == (4_000, "paid")
    stripe.add("refund", -10_000, link="ch_txn_0001")
    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    with books.read() as c:
        assert (_sum(c, "1050"), _sum(c, "6200"), _sum(c, "1200")) == (9_680, 320, 10_000)
        assert _every_entry_balances(c)
    assert _invoice_row(books, iids[0]) == (0, "sent")
    assert _invoice_row(books, iids[1]) == (0, "sent")


def test_a_summarized_sale_recategorized_after_its_month_closed_still_reconciles_that_month(books):
    """A $100 sale with a $3.20 fee in October's summary, recategorized in November after October closed: October still
    holds the sale's $96.80 net for reconciliation, and the row no longer carries its summary mark."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    october = int(datetime(2026, 10, 14, 12, tzinfo=timezone.utc).timestamp())
    stripe.add("charge", 10_000, 320, "Acme Buyer", created=october)
    stripe_pass(books, stripe)
    money = _stripe_money(books)
    with books.transaction() as c:
        assert close_month(c, "2026-10", at="2026-11-05T12:00:00.000000Z").status == "committed"
        reopen_rows(c)
        row = str(_feed(c, "txn_0001")["id"])
        moved = act(
            c, OWNER, "categorize", {"feed_id": row, "account_id": account(c, "4000")}, at="2026-11-05T12:00:00.000000Z"
        )
        assert moved.status == "committed", moved
    with books.read() as c:
        gl = str(c.execute("SELECT account_id FROM fin_money_accounts WHERE id=?", (money,)).fetchone()[0])
        book = str(c.execute("SELECT id FROM fin_books").fetchone()[0])
        effects = [
            (int(r["through_amount"]), int(r["live_amount"]))
            for r in store_banking.row_effects(c, book, money, gl, "2026-10-31")
            if str(r["id"]) == row
        ]
        assert effects == [(9_680, 9_680)]
        assert "summary" not in json.loads(_feed(c, "txn_0001")["raw"])


def test_a_refund_of_a_posted_sale_applied_by_hand_reopens_the_receivable_it_gives_back(books):
    """A $100 sale posted in its day's summary and refunded $50 before anything paid an invoice. Then $50 of it is applied
    by hand to a $50 invoice, and the last $50 is refunded. The invoice owes its $50 again, so the books carry it in
    receivables; the sale's income is reversed once, and the balance is empty."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    iid = _invoice(books, _customer(books, "Acme Retail"), 5_000)
    stripe.add("charge", 10_000, 0, "Acme Shop")
    stripe_pass(books, stripe)
    stripe.add("refund", -5_000, link="ch_txn_0001")
    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    with books.transaction() as c:
        sale = str(_feed(c, "txn_0001")["id"])
        made = act(c, OWNER, "invoice_record_payment", {"invoice_id": iid, "feed_id": sale, "amount": 5_000})
        assert made.status == "committed", made
    stripe.add("refund", -5_000, link="ch_txn_0001")
    stripe_pass(books, stripe, "2026-10-15T14:00:00.000000Z")
    with books.read() as c:
        assert (_sum(c, "1200"), _sum(c, "4000"), _sum(c, "1050")) == (5_000, -5_000, 0)
        assert _every_entry_balances(c)
    assert _invoice_row(books, iid) == (0, "sent")


def test_a_webhook_with_a_bad_or_stale_signature_is_refused():
    body = json.dumps({"id": "evt_1", "type": "payout.paid", "data": {}}).encode()
    now = int(time.time())
    good = {"stripe-signature": F.sign(body, WEBHOOK_SECRET, now)}
    assert P.verify("stripe_finance", good, body, WEBHOOK_SECRET, now) == (True, "")
    assert (
        P.verify(
            "stripe_finance",
            {"stripe-signature": F.sign(body, "whsec_other_0123456789abcdef", now)},
            body,
            WEBHOOK_SECRET,
            now,
        )[0]
        is False
    )
    assert (
        P.verify(
            "stripe_finance", {"stripe-signature": F.sign(body, WEBHOOK_SECRET, now - 3600)}, body, WEBHOOK_SECRET, now
        )[0]
        is False
    )
    assert P.verify("stripe_finance", {}, body, WEBHOOK_SECRET, now)[0] is False
    assert "stripe_finance" in P.FINANCE


def test_a_webhook_is_kept_once_for_the_one_stripe_connection_and_then_reads_the_balance(books):
    event = {"id": "evt_stripe_1", "type": "charge.succeeded", "data": {}}
    with books.transaction() as c:
        assert sync.stripe_delivery(c, event, AT) == "no_connection"  # no Stripe connection yet: refused, not seen
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("charge", 4_000, 120, "Acme Buyer")
    with books.transaction() as c:
        assert sync.stripe_delivery(c, event, AT) == "kept"
    with books.transaction() as c:
        assert sync.stripe_delivery(c, event, AT) == "seen"  # a replay is not kept again
    stripe_pass(books, stripe)
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_inbox WHERE provider_event_id='evt_stripe_1'").fetchone()[0] == "done"
        assert _sum(c, "1050") == 3_880


def _stripe_opening(c: Any) -> int:
    """The live opening balance entry's amount on the Stripe line (1050)."""
    return int(
        c.execute(
            "SELECT coalesce(sum(l.amount), 0) FROM fin_entries e JOIN fin_lines l ON l.entry_id=e.id "
            "JOIN fin_accounts a ON a.id=l.account_id WHERE e.source='sync' AND e.source_id LIKE 'opening:%' "
            "AND e.status='posted' AND e.reverses_id IS NULL AND a.code='1050'"
        ).fetchone()[0]
    )


def test_a_stripe_opening_moves_by_a_raw_post_and_back_by_its_reversal(books):
    """Phase 2 #2 (§4.1 opening): a Stripe row a raw-totals month covers moves the opening the way a bank row does: the
    opening takes its amount in, with no balance read, and its reversal takes it back out. The Stripe line is the
    provider's balance once the row is covered; before and after, the unposted row is the only difference."""
    sep = int(datetime(2026, 9, 10, 12, tzinfo=timezone.utc).timestamp())
    stripe = FakeStripe(start=7_000)
    _full_history(books, connect_stripe(books, stripe))  # a full history: a row with no sale waits for a person
    stripe.add("refund_failure", 500, link="ch_missing", created=sep)  # waits for its sale: the balance holds it
    stripe.add("charge", 2_000, 0, "Acme Buyer")  # October: posted as a sale
    stripe_pass(books, stripe)
    with books.transaction() as c:
        assert _stripe_opening(c) == 7_000
        assert _sum(c, "1050") == 9_000  # the waiting restore is the one row the books do not carry
        assert _feed(c, "txn_0001")["status"] == "new"
    csv = "month,line,amount,team\n2026-09,Sales revenue,250000,sales\n"
    with books.transaction() as c:
        r = act(
            c,
            OWNER,
            "import_raw_totals",
            {"csv": csv, "basis": "cash", "mapping": {"Sales revenue": "4000"}, "source_name": "Stripe history"},
        )
        assert r.status == "committed", r.reason
        entry = r.result["months"][0]["entry_id"]
        assert _stripe_opening(c) == 7_500
        assert _sum(c, "1050") == stripe.total() == 9_500
        assert _every_entry_balances(c)
    with books.transaction() as c:
        assert act(c, OWNER, "journal", {"reverses": entry}).status == "committed"
        assert _stripe_opening(c) == 7_000
        assert _sum(c, "1050") == stripe.total() - 500
        assert _feed(c, "txn_0001")["status"] == "new"  # uncovered again: it waits for a person


# ------------------------------------------------------------------------------------------------ history_from
def _settings(c: Any, cid: str) -> dict[str, Any]:
    return dict(json.loads(c.execute("SELECT settings FROM fin_connections WHERE id=?", (cid,)).fetchone()[0]))


def _opening_entry(c: Any) -> Any:
    return c.execute(
        "SELECT e.entry_date, l.amount FROM fin_entries e JOIN fin_lines l ON l.entry_id=e.id "
        "JOIN fin_accounts a ON a.id=l.account_id WHERE e.source_id LIKE 'opening:%' AND a.code='1050'"
    ).fetchone()


def test_a_new_stripe_connection_reads_from_jan_1_by_default_and_a_history_from_must_be_a_day(books):
    stripe = FakeStripe()
    found, _ = stripe_sync.probe(stripe)  # type: ignore[arg-type]
    with books.transaction() as c:
        conn = {"provider": "stripe", "credential_id": "cred-stripe", "probe": found}
        bad = act(c, OWNER, "connection_create", {**conn, "history_from": "June"})
        assert bad.status != "committed" and "history_from" in str(bad.reason), bad
        made = act(c, OWNER, "connection_create", conn)
        assert made.status == "committed", made
        assert _settings(c, str(made.result["connection_id"]))["history_from"] == "2026-01-01"


def test_a_cut_stripe_history_reads_from_its_day_and_opens_at_the_balance_less_the_rows_taken_in(books):
    """Stripe answers only rows created from history_from (created[gte]); the balance holds the older ones too. The
    opening is the balance less the rows taken in, dated the day before the first of them."""
    older = int(datetime(2025, 12, 15, 12, tzinfo=timezone.utc).timestamp())
    stripe = FakeStripe(start=7_000)
    stripe.add("charge", 3_000, 0, "Acme Old", created=older)  # before history_from: never read
    stripe.add("charge", 1_000, 0, "Acme Buyer")
    stripe.add("charge", 2_000, 0, "Acme Buyer")
    asked: list[int | None] = []
    plain = stripe.transactions

    def spy(starting_after: str | None = None, limit: int = 100, created_gte: int | None = None) -> Answer:
        asked.append(created_gte)
        return plain(starting_after, limit, created_gte)

    stripe.transactions = spy  # type: ignore[method-assign]
    connect_stripe(books, stripe)
    stripe_pass(books, stripe)
    assert asked and set(asked) == {int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())}
    with books.read() as c:
        assert _feed(c, "txn_0001") is None
        day, amount = _opening_entry(c)
        assert (day, amount) == ("2026-10-09", 7_000 + 3_000)  # B - S = 13_000 - 3_000
        assert _sum(c, "1050") == stripe.total() == 13_000
        assert _every_entry_balances(c)


# ------------------------------------------------------------------------------------------------ marketplace kinds
def _line_of(c: Any, name: str) -> Any:
    return c.execute(
        "SELECT a.code, a.type, p.code AS parent FROM fin_accounts a LEFT JOIN fin_accounts p ON p.id=a.parent_id "
        "WHERE a.name=?",
        (name,),
    ).fetchone()


def test_connect_transfers_and_stripe_capital_post_in_the_day_summary_with_no_ask(books):
    """A marketplace's balance: Connect transfers out (and one coming back) to Payouts to connected accounts under Cost
    of revenue, at the first free code from 5300 (a company line already holds 5300); the Capital paydowns withheld from
    sales to the Stripe Capital loan liability. One summary entry for the day, no person asked."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    with books.transaction() as c:
        assert (
            act(c, OWNER, "account_create", {"code": "5300", "name": "Pro payouts", "type": "expense"}).status
            == "committed"
        )
    stripe.add("charge", 10_000, 0, "Acme Buyer")
    stripe.add("transfer", -4_000)
    stripe.add("transfer", -3_000)
    stripe.add("transfer_refund", 1_000)
    stripe.add("financing_paydown", -2_000)
    stripe.add("financing_paydown", -1_500)
    stripe_pass(books, stripe)
    with books.read() as c:
        assert tuple(_line_of(c, "Payouts to connected accounts")) == ("5301", "expense", "5000")
        assert _line_of(c, "Stripe Capital loan")["type"] == "liability"
        assert _named_sum(c, "Payouts to connected accounts") == 6_000
        assert _named_sum(c, "Stripe Capital loan") == 3_500
        assert _sum(c, "5300") == 0
        assert _sum(c, "1050") == stripe.total()
        assert _entries(c, "stripe:summary:") == 1
        assert (
            int(c.execute("SELECT COUNT(*) FROM fin_events WHERE name='finance.feed.needs_category'").fetchone()[0])
            == 0
        )
        assert _every_entry_balances(c)


def test_a_capital_loan_paid_out_and_the_last_one_paid_down_the_same_day_net_on_the_loan_line(books):
    """A new Stripe Capital loan arrives (financing_payout) the day the last one is paid down: both post to the loan
    line, which is left owing the difference, and nothing goes to suspense or asks a person."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("financing_payout", 50_000)
    stripe.add("financing_paydown", -30_000)
    stripe_pass(books, stripe)
    with books.read() as c:
        assert _named_sum(c, "Stripe Capital loan") == -20_000  # a credit: the loan still owed
        assert _line_of(c, "Stripe suspense") is None
        assert _sum(c, "1050") == stripe.total()
        assert (
            int(c.execute("SELECT COUNT(*) FROM fin_events WHERE name='finance.feed.needs_category'").fetchone()[0])
            == 0
        )
        assert _every_entry_balances(c)


def test_a_line_the_company_made_is_reused_by_name_in_any_case(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    with books.transaction() as c:
        made = act(c, OWNER, "account_create", {"code": "2320", "name": "stripe capital LOAN", "type": "liability"})
        loan = str(made.result["account_id"])
    stripe.add("financing_paydown", -2_000)
    stripe_pass(books, stripe)
    with books.read() as c:
        assert _sum(c, "2320") == 2_000
        assert c.execute("SELECT COUNT(*) FROM fin_accounts WHERE lower(name)='stripe capital loan'").fetchone()[0] == 1
        assert account(c, "2320") == loan


def test_lines_a_connection_names_take_its_transfers_and_capital_and_a_wrong_type_is_refused(books):
    stripe = FakeStripe()
    cid = connect_stripe(books, stripe)
    with books.transaction() as c:
        sellers = str(
            act(c, OWNER, "account_create", {"code": "5450", "name": "Seller share", "type": "expense"}).result[
                "account_id"
            ]
        )
        loan = str(
            act(c, OWNER, "account_create", {"code": "2330", "name": "Capital", "type": "liability"}).result[
                "account_id"
            ]
        )
        wrong = act(c, OWNER, "connection_settings", {"connection_id": cid, "transfer_account_id": loan})
        assert wrong.status != "committed"
        done = act(
            c,
            OWNER,
            "connection_settings",
            {"connection_id": cid, "transfer_account_id": sellers, "capital_account_id": loan},
        )
        assert done.status == "committed", done.reason
    stripe.add("transfer", -4_000)
    stripe.add("financing_paydown", -900)
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_sum(c, "5450"), _sum(c, "2330")) == (4_000, 900)
        assert _line_of(c, "Payouts to connected accounts") is None
        assert _every_entry_balances(c)


def test_an_unknown_kind_posts_to_suspense_asked_once_until_a_rule_on_its_kind_takes_it(books):
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    for _ in range(3):
        stripe.add("some_new_kind", -700)
    stripe_pass(books, stripe)
    with books.read() as c:
        assert _named_sum(c, "Stripe suspense") == 2_100  # what left the balance waits on suspense until decided
        assert _sum(c, "1050") == stripe.total()
        assert _kind_asks(c, "some_new_kind") == 1
        assert all(_asked(c, f"txn_000{i}") == 0 for i in (1, 2, 3))
    with books.transaction() as c:
        rule = {
            "kind": "posting",
            "name": "New kind",
            "when": {"feed.kind": "some_new_kind"},
            "outcome": {"outcome": "post", "account_id": account(c, "6900")},
            "examples": [{"input": {"feed.kind": "some_new_kind"}, "expect": "post"}],
        }
        assert act(c, OWNER, "rule_create", rule).status == "committed"
    stripe.add("some_new_kind", -300, created=DAY + 86_400)
    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    with books.read() as c:
        assert (_named_sum(c, "Stripe suspense"), _sum(c, "6900")) == (2_100, 300)
        assert _kind_asks(c, "some_new_kind") == 1
        assert _sum(c, "1050") == stripe.total()
        assert _every_entry_balances(c)


def test_advances_are_not_taken_for_capital_they_wait_on_suspense_asked_once_per_kind(books):
    """Stripe also names instant payouts `advance`, so an advance is not taken for a Capital loan: it waits on suspense,
    one ask per kind. A Capital paydown still goes to the loan."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("advance", 30_000)
    stripe.add("advance", 20_000)
    stripe.add("advance_funding", 5_000)
    stripe.add("financing_paydown", -1_000)
    stripe_pass(books, stripe)
    with books.read() as c:
        assert (_named_sum(c, "Stripe suspense"), _named_sum(c, "Stripe Capital loan")) == (-55_000, 1_000)
        assert (_kind_asks(c, "advance"), _kind_asks(c, "advance_funding")) == (1, 1)
        assert _sum(c, "1050") == stripe.total()
        assert _every_entry_balances(c)


def test_a_day_whose_rows_cancel_out_posts_nothing_and_the_sync_goes_on(books):
    """A transfer and its full reversal on one day sum to nothing: no summary entry (it could not balance), the rows are
    done, and the next day's rows still post."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("transfer", -500)
    stripe.add("transfer_refund", 500)
    assert stripe_pass(books, stripe) == "ok"
    with books.read() as c:
        assert _entries(c, "stripe:summary:") == 0
        assert {str(_feed(c, t)["status"]) for t in ("txn_0001", "txn_0002")} == {"ignored"}
    stripe.add("transfer", -700, created=DAY + 86_400)
    assert stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z") == "ok"
    with books.read() as c:
        assert (_entries(c, "stripe:summary:"), _named_sum(c, "Payouts to connected accounts")) == (1, 700)
        assert _sum(c, "1050") == stripe.total()
        assert _every_entry_balances(c)


def _created_gte_spy(stripe: FakeStripe) -> list[int | None]:
    """Records the created[gte] of every balance-transaction read."""
    asked: list[int | None] = []
    plain = stripe.transactions

    def spy(starting_after: str | None = None, limit: int = 100, created_gte: int | None = None) -> Answer:
        asked.append(created_gte)
        return plain(starting_after, limit, created_gte)

    stripe.transactions = spy  # type: ignore[method-assign]
    return asked


JAN_1 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())


def test_a_cut_history_with_no_rows_keeps_reading_from_its_day_and_opens_before_it(books):
    """A history cut at Jan 1 with no row from that day on: the backfill takes nothing in and keeps no boundary, and
    every later poll still reads from Jan 1 (an older row is never taken in). The opening is the balance, dated the day
    before Jan 1, so a row on the sync day first seen later is not netted into it."""
    older = int(datetime(2025, 12, 15, 12, tzinfo=timezone.utc).timestamp())
    stripe = FakeStripe(start=7_000)
    stripe.add("charge", 3_000, 0, "Acme Old", created=older)
    asked = _created_gte_spy(stripe)
    connect_stripe(books, stripe)
    stripe_pass(books, stripe)
    with books.read() as c:
        assert _opening_entry(c)[:] == ("2025-12-31", 10_000)
    late = int(datetime(2026, 10, 15, 12, 30, tzinfo=timezone.utc).timestamp())  # the sync day, after the balance read
    stripe.add("charge", 1_000, 0, "Acme Buyer", created=late)
    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    stripe_pass(books, stripe, "2026-10-15T14:00:00.000000Z")
    assert asked and set(asked) == {JAN_1}
    with books.read() as c:
        assert _feed(c, "txn_0001") is None
        assert _opening_entry(c)[:] == ("2025-12-31", 10_000)
        assert _sum(c, "1050") == stripe.total() == 11_000
        assert _every_entry_balances(c)


def test_rows_an_earlier_finance_asked_about_one_by_one_settle_once_their_kind_is_mapped(books, monkeypatch):
    """Transfers an earlier Finance left new and asked about row by row: the next pass posts them in the day's summary
    and withdraws their asks; a pass after that changes nothing."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    stripe.add("transfer", -4_000)
    stripe.add("transfer", -1_000)
    with monkeypatch.context() as old:
        old.setattr(stripe_post, "_other", lambda ctx, row, money, t, gross: stripe_post._ask(ctx, str(row["id"]), "?"))
        stripe_pass(books, stripe)
    with books.read() as c:
        assert [_asked(c, t) for t in ("txn_0001", "txn_0002")] == [1, 1]
        assert _entries(c, "stripe:summary:") == 0

    def state() -> tuple[int, int, int, int]:
        with books.read() as c:
            asks = _asked(c, "txn_0001") + _asked(c, "txn_0002")
            lines = int(c.execute("SELECT COUNT(*) FROM fin_lines").fetchone()[0])
            return asks, _entries(c, "stripe:summary:"), _named_sum(c, "Payouts to connected accounts"), lines

    stripe_pass(books, stripe, "2026-10-15T13:00:00.000000Z")
    after = state()
    assert after[:3] == (0, 1, 5_000)
    stripe_pass(books, stripe, "2026-10-15T14:00:00.000000Z")
    assert state() == after
    with books.read() as c:
        assert _sum(c, "1050") == stripe.total()
        assert _every_entry_balances(c)


def test_a_refund_of_a_sale_held_here_waits_for_it_in_a_cut_history(books):
    """In a cut history, a refund whose sale was taken in but is held (several invoices could be it) is not one of a
    sale before history_from: past seven days it still waits for its sale instead of posting against Revenue."""
    stripe = FakeStripe()
    connect_stripe(books, stripe)
    customer = _customer(books, "Acme Retail")
    _invoice(books, customer, 10_000)
    _invoice(books, customer, 10_000)
    stripe.add("charge", 10_000, 0, "Acme Retail")
    stripe.add("refund", -10_000, link="ch_txn_0001")
    stripe_pass(books, stripe)
    stripe_pass(books, stripe, STALE_AT)
    stripe_pass(books, stripe, "2026-10-20T13:00:00.000000Z")
    with books.read() as c:
        assert (_feed(c, "txn_0001")["entry_id"], _feed(c, "txn_0002")["entry_id"]) == (None, None)
        assert _sum(c, "4000") == -20_000  # the two invoices only: the refund took nothing back from Revenue
        assert _every_entry_balances(c)


def test_a_line_set_while_the_backfill_reads_is_kept(books):
    """connection_settings changing the connection while its backfill reads Stripe page by page: the sync's own writes
    keep the change."""
    stripe = FakeStripe(page=1)
    for amount in (1_000, 2_000, 3_000):
        stripe.add("charge", amount, 0, "Acme Buyer")
    cid = connect_stripe(books, stripe)
    with books.transaction() as c:
        sellers = str(
            act(c, OWNER, "account_create", {"code": "5450", "name": "Seller share", "type": "expense"}).result[
                "account_id"
            ]
        )
    plain = stripe.transactions
    calls = [0]

    def meanwhile(starting_after: str | None = None, limit: int = 100, created_gte: int | None = None) -> Answer:
        calls[0] += 1
        if calls[0] == 3:  # the second page: the first was taken in and its place saved
            with books.transaction() as c:
                done = act(c, OWNER, "connection_settings", {"connection_id": cid, "transfer_account_id": sellers})
                assert done.status == "committed", done.reason
        return plain(starting_after, limit, created_gte)

    stripe.transactions = meanwhile  # type: ignore[method-assign]
    stripe_pass(books, stripe)
    with books.read() as c:
        settings = _settings(c, cid)
        assert settings["backfilled"] is True and calls[0] >= 4
        assert settings["transfer_account_id"] == sellers


def test_connection_create_with_no_probe_points_to_the_connect_route(books):
    with books.transaction() as c:
        for provider in ("mercury", "stripe", "brex"):
            done = act(c, OWNER, "connection_create", {"provider": provider, "credential_id": "cred-stripe"})
            assert done.status != "committed" and "/api/v2/finance/connections" in str(done.reason), done


def test_a_new_stripe_connection_takes_its_transfer_line_and_refuses_a_wrong_type(books):
    stripe = FakeStripe()
    found, _ = stripe_sync.probe(stripe)  # type: ignore[arg-type]
    conn = {"provider": "stripe", "credential_id": "cred-stripe", "probe": found}
    with books.transaction() as c:
        sellers = str(
            act(c, OWNER, "account_create", {"code": "5300", "name": "Pro payouts", "type": "expense"}).result[
                "account_id"
            ]
        )
        wrong = act(c, OWNER, "connection_create", {**conn, "transfer_account_id": account(c, "4000")})
        assert wrong.status != "committed" and "transfer_account_id" in str(wrong.reason), wrong
        made = act(c, OWNER, "connection_create", {**conn, "transfer_account_id": sellers})
        assert made.status == "committed", made
        assert _settings(c, str(made.result["connection_id"]))["transfer_account_id"] == sellers
    stripe.add("transfer", -4_000)
    stripe_pass(books, stripe)
    with books.read() as c:
        assert _sum(c, "5300") == 4_000
        assert _line_of(c, "Payouts to connected accounts") is None


class _CountingBell:
    """The worker's doorbell, counting instead of sleeping: each wait records how long the loop would sleep, and the
    `stop`th wait halts the loop."""

    def __init__(self, halt: Any, stop: int) -> None:
        self.rung, self.waits, self.halt, self.stop = 0, [], halt, stop

    def wait(self, seen: int, timeout: float) -> int:
        self.waits.append(timeout)
        if len(self.waits) >= self.stop:
            self.halt.set()
        return self.rung


def _run_loop(books: Any, fake: Any, monkeypatch: Any, stop: int) -> list[float]:
    import threading

    halt = threading.Event()
    bell = _CountingBell(halt, stop)
    monkeypatch.setattr(sync, "BELL", bell)
    sync._loop(books, None, False, lambda _t: fake, halt)
    return bell.waits


def test_a_first_backfill_steps_again_after_a_short_pause_not_a_sweep(books, monkeypatch):
    """Each step reads BACKFILL_PAGES pages; while the backfill is unfinished the worker steps again after
    BACKFILL_PAUSE, and once it ends it waits the SWEEP."""
    stripe = FakeStripe(page=2)
    connect_stripe(books, stripe)
    for i in range(5):
        stripe.add("charge", 1_000, 0, f"Buyer {i}")
    monkeypatch.setattr(stripe_sync, "BACKFILL_PAGES", 1)
    assert _run_loop(books, stripe, monkeypatch, stop=3) == [sync.BACKFILL_PAUSE, sync.BACKFILL_PAUSE, sync.SWEEP]
    with books.read() as c:
        settings = json.loads(c.execute("SELECT settings FROM fin_connections WHERE provider='stripe'").fetchone()[0])
        assert settings["backfilled"] is True
        assert c.execute("SELECT COUNT(*) FROM fin_feed_txns").fetchone()[0] == 5


def test_a_failing_backfill_waits_the_sweep_and_is_not_read_again_at_once(books, monkeypatch):
    """A 429 on the backfill leaves the connection in error and due a poll later: the worker sleeps the SWEEP, and the
    next sweep does not read Stripe again."""
    stripe = FakeStripe(page=2)
    connect_stripe(books, stripe)
    stripe.add("charge", 1_000, 0, "Buyer")
    reads: list[str | None] = []

    def limited(starting_after: str | None = None, limit: int = 100, created_gte: int | None = None) -> Answer:
        reads.append(starting_after)
        return Answer(429, {"error": {"message": "Too many requests"}})

    monkeypatch.setattr(stripe, "transactions", limited)
    assert _run_loop(books, stripe, monkeypatch, stop=2) == [sync.SWEEP, sync.SWEEP]
    assert len(reads) == 1
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_connections WHERE provider='stripe'").fetchone()[0] == "error"


def test_the_short_pause_is_only_for_an_active_backfill_whose_next_step_is_due(books):
    connect_stripe(books, FakeStripe())
    later = "2026-10-15T12:45:00.000000Z"
    for status, due, wait in (
        ("active", AT, sync.BACKFILL_PAUSE),
        ("active", later, sync.SWEEP),  # backing off: due a poll later
        ("error", AT, sync.SWEEP),  # failed: the poll retries it, not the pause
    ):
        with books.transaction() as c:
            c.execute("UPDATE fin_connections SET status=?, next_sync_at=?", (status, due))
        assert sync.pause(books, at=AT) == wait, (status, due)
    with books.transaction() as c:
        c.execute(
            "UPDATE fin_connections SET status='active', next_sync_at=?, "
            "settings=json_set(settings, '$.backfilled', json('true'))",
            (AT,),
        )
    assert sync.pause(books, at=AT) == sync.SWEEP  # backfilled: a due poll is a sweep's business


def test_a_backfill_step_that_raises_goes_to_error_and_waits_the_sweep(books, monkeypatch):
    """A step that raises before it records its outcome puts the connection in error, due a poll later: the worker
    does not step it again on the short pause."""
    stripe = FakeStripe()
    cid = connect_stripe(books, stripe)
    calls: list[str] = []

    def broken(store: Any, provider: Any, cid: str, now: str) -> list[str]:
        calls.append(cid)
        raise RuntimeError("made-up failure")

    monkeypatch.setattr(stripe_sync, "catch_up", broken)
    assert _run_loop(books, stripe, monkeypatch, stop=3) == [sync.SWEEP, sync.SWEEP, sync.SWEEP]
    assert calls == [cid]
    with books.read() as c:
        status, due, why = c.execute("SELECT status, next_sync_at, last_error FROM fin_connections").fetchone()
    assert status == "error" and str(due) > sync._now() and "RuntimeError" in why


def test_a_short_wake_steps_only_a_due_backfill(books, monkeypatch):
    """Between backfill steps the worker steps only the backfills that are due: a connection in error with a waiting
    webhook, or a finished one with owed work, waits for a full sweep or a ring."""
    cid = connect_stripe(books, FakeStripe())
    with books.transaction() as c:
        store_banking.inbox_add(
            c,
            connection_id=cid,
            provider="stripe",
            event_id="evt_made_up",
            payload={"type": "balance.available"},
            at=AT,
        )
    stepped: list[str] = []
    monkeypatch.setattr(sync, "connection", lambda store, vault, cid, **_k: stepped.append(cid) or "ok")
    later = "2026-10-15T12:45:00.000000Z"
    for status, due, backfilled, short, want in (
        ("error", later, False, True, []),  # in error with a webhook waiting: not on a short wake
        ("error", later, False, False, [cid]),  # a full sweep still tries it (and finds it idle)
        ("active", AT, True, True, []),  # a finished backfill's poll is a sweep's business
        ("active", AT, False, True, [cid]),  # the backfill itself steps
    ):
        with books.transaction() as c:
            c.execute(
                "UPDATE fin_connections SET status=?, next_sync_at=?, "
                "settings=json_set(coalesce(settings, '{}'), '$.backfilled', json(?))",
                (status, due, "true" if backfilled else "false"),
            )
        stepped.clear()
        sync.step(books, None, at=AT, backfill_only=short)
        assert stepped == want, (status, due, backfilled, short)


def test_the_loop_steps_everything_after_a_ring_or_a_sweep(books, monkeypatch):
    """The first step and any step after a ring or a SWEEP's time are full; a wake on the short pause is not."""
    import threading

    flags: list[bool] = []
    monkeypatch.setattr(sync, "step", lambda *_a, backfill_only=False, **_k: flags.append(backfill_only))
    monkeypatch.setattr(sync, "pause", lambda _store: sync.BACKFILL_PAUSE)
    s = sync.SWEEP
    clock = iter([0.0, 0.0, 2.0, 4.0, 6.0, 6.0 + s, 6.0 + s, 8.0 + s, 8.0 + s])
    monkeypatch.setattr(sync, "_monotonic", lambda: next(clock))
    halt = threading.Event()

    class Ringing(_CountingBell):
        def wait(self, seen: int, timeout: float) -> int:
            super().wait(seen, timeout)
            if len(self.waits) == 3:
                self.rung += 1  # a webhook rang during the third wait
            return self.rung

    monkeypatch.setattr(sync, "BELL", Ringing(halt, 6))
    sync._loop(books, None, False, None, halt)
    assert flags == [False, True, True, False, False, True]  # full, short, short, rung, a SWEEP gone by, short
