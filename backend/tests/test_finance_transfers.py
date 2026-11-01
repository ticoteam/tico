"""Internal transfers (spec §5, §9): a payment_create with method "internal" moves money between two of the company's own
bank accounts through Mercury's transfer endpoint (backend/finance/transfers.py). Covers the payment gates (over $1,000
asks the owner; the new-recipient rule never applies), approval, posting that nets to zero on P&L and settles the
transfer, a leg that comes back cancelled, an unknown or refused result never sent again, replay, and validation. The
fake Mercury stands in for the bank; synthetic fixtures."""
import pytest

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance.mercury import Answer
from backend.finance.store import schema
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, act, books, connect, finance_template, store, sync_once)

LATER = "2026-10-15T14:00:00.000000Z"
LATEST = "2026-10-15T20:00:00.000000Z"
POLL = "2026-10-15T20:30:00.000000Z"                             # past the 15-minute poll after LATEST


def _accounts(store):
    """(money account id, Mercury account id, name) for each synced bank account, by name."""
    with store.read() as c:
        return [tuple(r) for r in c.execute("SELECT id, provider_account_id, name FROM fin_money_accounts "
                                            "WHERE kind='bank' AND provider_account_id IS NOT NULL ORDER BY name")]


def _two(store, fake):
    connect(store, fake)
    sync_once(store, fake)
    found = _accounts(store)
    assert len(found) >= 2, found
    return found[0], found[1]


def _by_provider(store, provider_id):
    """(money account id, Mercury account id, name) for the synced account with this Mercury id."""
    with store.read() as c:
        return tuple(c.execute("SELECT id, provider_account_id, name FROM fin_money_accounts "
                               "WHERE provider_account_id=?", (provider_id,)).fetchone())


def _rows(store, sql, args=()):
    with store.read() as c:
        return c.execute(sql, args).fetchall()


def _leg(raw_id, account, amount, day="2026-10-15"):
    """One side of a move between two of the company's accounts, as Mercury's feed shows it."""
    return {**MERCURY["txn_bank_fee"], "id": raw_id, "accountId": account, "amount": amount,
            "kind": "internalTransfer", "createdAt": f"{day}T10:00:00Z", "postedAt": f"{day}T10:00:00Z",
            "counterpartyName": "Acme"}


def _arrive(fake, out_account, in_account, amount_dollars, day="2026-10-15"):
    """Both legs reach the feed after the bank worker's first backfill, as transaction events it reads on its next pass."""
    for raw_id, account, value in (("leg-out", out_account, -amount_dollars), ("leg-in", in_account, amount_dollars)):
        fake.txns[raw_id] = _leg(raw_id, account, value, day)
        fake.events_list.append({"id": f"evt-{raw_id}", "resourceType": "transaction", "resourceId": raw_id,
                                 "operationType": "create", "resourceVersion": 1})


def _payload(src, dst, amount=25_000, memo="to savings"):
    return {"method": "internal", "from_account": src[0], "to_account": dst[0], "amount": amount, "memo": memo}


def _view(c, who):
    """Balance-sheet view of the company's bank accounts for `who`: the view a payment's money account line gets."""
    act(c, OWNER, "view_grant", {"subject_kind": "teammate", "subject": who, "what": "balance_sheet",
                                 "whose": "company"})


def _ledger(store, money_id):
    """(ledger account id, code) that a bank account is mapped to."""
    with store.read() as c:
        return tuple(c.execute("SELECT a.id, a.code FROM fin_money_accounts m JOIN fin_accounts a ON a.id=m.account_id "
                               "WHERE m.id=?", (money_id,)).fetchone())


def _queued_by_ben(store, src, dst, amount=10_000):
    """Ben (with the pay right and the view) queues a move; returns its transfer id."""
    with store.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.pay"})
        _view(c, "human:ben")
        made = act(c, "human:ben", "payment_create", _payload(src, dst, amount))
        assert made.status == "committed", made
        return str(made.result["transfer_id"])


def _assert_cancelled_unsent(store, fake):
    assert fake.sent("create_transfer") == []
    assert _rows(store, "SELECT status FROM fin_transfers")[0][0] == "cancelled"
    assert _rows(store, "SELECT status FROM fin_outbox WHERE op='transfer_create'")[0][0] == "failed"
    assert _rows(store, "SELECT count(*) FROM tasks WHERE title LIKE 'Transfer of%was not sent'")[0][0] == 1


def test_an_internal_transfer_over_1000_asks_the_owner_then_goes_to_mercury_once(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.pay"})
        _view(c, "human:ben")
        asked = act(c, "human:ben", "payment_create", _payload(src, dst, amount=250_000))
        assert asked.status == "needs_approval" and asked.approver == OWNER and asked.approval_id, asked
        assert c.execute("SELECT count(*) FROM fin_transfers").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM fin_outbox WHERE op='transfer_create'").fetchone()[0] == 0
        H.approval_decide(c, OWNER, asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done is not None and done.status == "committed", done
    sync_once(books, fake)
    (body,) = fake.sent("create_transfer")
    assert body["sourceAccountId"] == src[1] and body["destinationAccountId"] == dst[1]
    assert body["amount"] == 2500 and str(body["idempotencyKey"]).startswith("transfer-")
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "submitted"


def test_an_internal_transfer_is_never_a_new_recipient_so_no_recipient_rule_asks_about_it(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        # A company rule that asks about every payee not paid before. The own account is known, so it does not match.
        strangers = act(c, OWNER, "rule_create", {
            "name": "Strangers ask", "when": {"kind": "payment_create", "recipient.known": {"not": True}},
            "outcome": {"outcome": "needs_approval", "who": "owner"},
            "examples": [{"input": {"kind": "payment_create", "recipient.known": False}, "expect": "needs_approval"}]})
        assert strangers.status == "committed", strangers
        made = act(c, OWNER, "payment_create", _payload(src, dst, amount=25_000))
        assert made.status == "committed" and made.approval_id is None, made
    sync_once(books, fake)
    assert len(fake.sent("create_transfer")) == 1


def test_both_legs_posting_settle_the_transfer_as_one_entry_that_moves_no_pnl(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, amount=80_000)).status == "committed"
    sync_once(books, fake)                                  # sent; the legs are not in Mercury yet
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "submitted"
    _arrive(fake, src[1], dst[1], 800.0)
    sync_once(books, fake, LATER)
    legs = _rows(books, "SELECT status FROM fin_feed_txns WHERE provider_id IN ('leg-out', 'leg-in')")
    assert [r[0] for r in legs] == ["posted", "posted"]
    status, out_leg, in_leg, settled = _rows(books, "SELECT status, from_feed_id, to_feed_id, settled_at "
                                                    "FROM fin_transfers")[0]
    assert status == "posted" and settled is not None
    assert _rows(books, "SELECT provider_id FROM fin_feed_txns WHERE id=?", (out_leg,))[0][0] == "leg-out"
    assert _rows(books, "SELECT provider_id FROM fin_feed_txns WHERE id=?", (in_leg,))[0][0] == "leg-in"
    with books.read() as c:
        (entry_id,) = [r[0] for r in c.execute("SELECT id FROM fin_entries WHERE memo LIKE 'Transfer:%'")]
        lines = c.execute("SELECT a.type, l.amount FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id "
                          "WHERE l.entry_id=?", (entry_id,)).fetchall()
        assert sorted(t for t, _ in lines) == ["asset", "asset"] and sum(a for _, a in lines) == 0
        # Nothing of the transfer lands on income or expense, so P&L does not move.
        pnl_moves = c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_accounts a "
                              "ON a.id=l.account_id JOIN fin_entries e ON e.id=l.entry_id "
                              "WHERE e.memo LIKE 'Transfer:%' AND a.type IN ('income', 'expense')").fetchone()[0]
        assert pnl_moves == 0


def test_legs_already_posted_when_the_transfer_is_recorded_settle_it_at_once(books):
    fake = FakeMercury()
    connect(books, fake)
    # The backfill reads /transactions once, at the first sync, so these legs are in Mercury before Finance reads.
    fake.txns["leg-out"] = _leg("leg-out", "acct-chk", -800.0)
    fake.txns["leg-in"] = _leg("leg-in", "acct-sav", 800.0)
    sync_once(books, fake)
    src, dst = _by_provider(books, "acct-chk"), _by_provider(books, "acct-sav")
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, amount=80_000)).status == "committed"
    sync_once(books, fake, LATER)
    assert len(fake.sent("create_transfer")) == 1
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "posted"
    assert _rows(books, "SELECT count(*) FROM fin_transfers WHERE from_feed_id IS NOT NULL "
                        "AND to_feed_id IS NOT NULL")[0][0] == 1


def test_a_leg_that_comes_back_cancelled_sends_its_transfer_back_to_waiting(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, amount=80_000)).status == "committed"
    sync_once(books, fake)
    _arrive(fake, src[1], dst[1], 800.0)
    sync_once(books, fake, LATER)
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "posted"
    fake.txns["leg-out"] = {**fake.txns["leg-out"], "status": "cancelled"}
    fake.events_list.append({"id": "evt-leg-out-2", "resourceType": "transaction", "resourceId": "leg-out",
                             "operationType": "update", "resourceVersion": 2})
    sync_once(books, fake, LATEST)
    assert tuple(_rows(books, "SELECT status, from_feed_id, to_feed_id FROM fin_transfers")[0]) == (
        "submitted", None, None)
    assert len(fake.sent("create_transfer")) == 1


def test_an_unknown_result_is_never_sent_again_and_a_person_is_asked_to_check(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    fake.queue["create_transfer"] = [Answer(None, {})]          # sent, but no answer came back
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, 10_000)).status == "committed"
    sync_once(books, fake)
    sync_once(books, fake, LATER)
    sync_once(books, fake, LATEST)
    assert len(fake.sent("create_transfer")) == 1
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "unknown"
    assert _rows(books, "SELECT status FROM fin_outbox WHERE op='transfer_create'")[0][0] == "failed"
    assert _rows(books, "SELECT count(*) FROM tasks WHERE title LIKE 'Check Mercury for transfer%'")[0][0] == 1
    # Its legs turn up after all: that settles it, and still nothing is sent again.
    _arrive(fake, src[1], dst[1], 100.0)
    sync_once(books, fake, POLL)
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "posted"
    assert len(fake.sent("create_transfer")) == 1


def test_a_refused_transfer_is_failed_not_sent_again_and_reported(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    fake.queue["create_transfer"] = [Answer(422, {"message": "insufficient funds"})]
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, 10_000)).status == "committed"
    sync_once(books, fake)
    sync_once(books, fake, LATER)
    assert len(fake.sent("create_transfer")) == 1
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "failed"
    assert _rows(books, "SELECT count(*) FROM tasks WHERE title LIKE 'Mercury refused transfer%'")[0][0] == 1


def test_a_replayed_transfer_is_the_same_transfer_and_goes_to_mercury_once(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        first = act(c, OWNER, "payment_create", _payload(src, dst, 10_000), key="move-1")
        again = act(c, OWNER, "payment_create", _payload(src, dst, 10_000), key="move-1")
    assert first.status == "committed" and again.action_id == first.action_id and again.replayed
    assert len(_rows(books, "SELECT id FROM fin_transfers")) == 1
    assert len(_rows(books, "SELECT id FROM fin_outbox WHERE op='transfer_create'")) == 1
    sync_once(books, fake)
    sync_once(books, fake, LATER)
    assert len(fake.sent("create_transfer")) == 1


def test_an_internal_transfer_needs_two_of_the_companys_bank_accounts_and_money_above_zero(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        same = act(c, OWNER, "payment_create", _payload(src, src))
        zero = act(c, OWNER, "payment_create", _payload(src, dst, 0))
        nothing = act(c, OWNER, "payment_create", {**_payload(src, dst), "to_account": "mny_missing"})
        payee = act(c, OWNER, "payment_create", {**_payload(src, dst), "recipient_id": "rec_x"})
        no_to = act(c, OWNER, "payment_create", {k: v for k, v in _payload(src, dst).items() if k != "to_account"})
    assert same.code == "invalid" and zero.code == "invalid" and nothing.code == "not_found", (same, zero, nothing)
    assert payee.code == "invalid" and no_to.code == "invalid", (payee, no_to)
    assert _rows(books, "SELECT count(*) FROM fin_transfers")[0][0] == 0


def test_migration_73_adds_fin_transfers_to_an_install_made_before_it_and_running_it_again_changes_nothing(books):
    with books.transaction() as c:
        c.execute("DROP TABLE fin_transfers")
        schema.gaps_books(c)
        schema.gaps_books(c)
        found = c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='fin_transfers'").fetchone()
        assert found[0] == 1


# fin_transfers as migration 73 first wrote it: before the columns added since, and the posted and cancelled statuses
FIRST_FIN_TRANSFERS = """CREATE TABLE IF NOT EXISTS fin_transfers (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), action_id TEXT NOT NULL,
  from_money_account_id TEXT NOT NULL REFERENCES fin_money_accounts(id),
  to_money_account_id TEXT NOT NULL REFERENCES fin_money_accounts(id),
  amount BIGINT NOT NULL CHECK (amount > 0), currency TEXT NOT NULL, memo TEXT NOT NULL DEFAULT '',
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','submitted','unknown','failed')),
  provider_txn_id TEXT, last_error TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT,
  CHECK (from_money_account_id <> to_money_account_id));"""


def test_migration_73_adds_every_fin_transfers_column_added_since_it_was_first_written(books):
    """An install that ran migration 73 as first written gets the columns added since, idempotently."""
    with books.transaction() as c:
        c.execute("DROP TABLE fin_transfers")
        c.execute(FIRST_FIN_TRANSFERS)
        schema.gaps_books(c)
        schema.gaps_books(c)
        have = {str(r[1]) for r in c.execute("PRAGMA table_info(fin_transfers)").fetchall()}
    assert {"from_feed_id", "to_feed_id", "settled_at", "submitted_at", "counted", "provider_credit_txn_id",
            "find_after"} <= have, have


def _limit_per_month(books, rule_id_out: list[str] | None = None) -> str:
    with books.transaction() as c:
        rule_id = str(act(c, OWNER, "rule_create", {
            "name": "Moves per month", "when": {"kind": "payment_create", "method": "internal"},
            "outcome": {"outcome": "limit", "limit": 50_000, "per": "month"},
            "examples": [{"input": {"kind": "payment_create", "method": "internal", "amount": 10}, "expect": "limit"}]}
        ).result["rule_id"])
    return rule_id


def _reserved(books, rule_id: str) -> tuple[int, int]:
    with books.read() as c:
        return tuple(c.execute("SELECT coalesce(sum(amount),0), coalesce(sum(count),0) FROM fin_rule_counters "
                               "WHERE rule_id=?", (rule_id,)).fetchone())


def test_a_transfer_cancelled_after_a_429_gives_back_the_limit_its_claim_reserved(books):
    """Mercury answered 429 after the send claim reserved a monthly limit, so the transfer stays queued to go again.
    Cancelling it takes the limit back: the counter holds only what went."""
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, 10_000, "held")).status == "committed"
    rule_id = _limit_per_month(books)
    fake.queue["create_transfer"] = [Answer(429, {"message": "slow down"})]
    sync_once(books, fake)
    assert _reserved(books, rule_id) == (10_000, 1)
    with books.transaction() as c:
        tid = str(c.execute("SELECT id FROM fin_transfers").fetchone()[0])
        assert act(c, OWNER, "payment_cancel", {"transfer_id": tid}).status == "committed"
    assert _reserved(books, rule_id) == (0, 0)
    assert tuple(_rows(books, "SELECT status, counted FROM fin_transfers")[0]) == ("cancelled", "[]")


def test_a_transfer_a_later_rule_refuses_after_a_429_gives_back_the_limit_its_claim_reserved(books):
    """A rule that now refuses a transfer that reserved a limit and was then answered 429: the refusal cancels it,
    and the limit it reserved comes back."""
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, 10_000, "held")).status == "committed"
    rule_id = _limit_per_month(books)
    fake.queue["create_transfer"] = [Answer(429, {"message": "slow down"})]
    sync_once(books, fake)
    assert _reserved(books, rule_id) == (10_000, 1)
    with books.transaction() as c:
        made = act(c, OWNER, "rule_create", {
            "name": "No bank moves", "when": {"kind": "payment_create", "method": "internal"},
            "outcome": {"outcome": "deny"},
            "examples": [{"input": {"kind": "payment_create", "method": "internal"}, "expect": "deny"}]})
        assert made.status == "committed", made
    sync_once(books, fake, LATEST)
    assert len(fake.sent("create_transfer")) == 1
    assert _reserved(books, rule_id) == (0, 0)
    assert tuple(_rows(books, "SELECT status, counted FROM fin_transfers")[0]) == ("cancelled", "[]")


def test_an_unknown_transfers_out_leg_past_five_thousand_transactions_is_found_by_a_later_poll(books):
    """The fallback reads REQUEST_PAGES pages of Mercury's transactions a poll, then keeps its place on the transfer,
    so a later poll reads on from there. The leg sits past the first 5,000 and has its key in externalMemo only, so the
    search misses it and the paged read finds it."""
    fake = FakeMercury()
    src, dst = _two(books, fake)
    fake.queue["create_transfer"] = [Answer(None, {})]
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, 80_000)).status == "committed"
    sync_once(books, fake, "2026-10-15T23:58:00.000000Z")        # sent, no answer: unknown; the feed backfills here
    (sent,) = fake.sent("create_transfer")
    for i in range(5_000):
        fake.txns[f"pad-{i}"] = {**_leg(f"pad-{i}", "acct-elsewhere", -1.0, "2026-10-16"), "note": None}
    fake.txns["leg-out"] = {**_leg("leg-out", src[1], -800.0, "2026-10-16"), "note": None,
                            "externalMemo": sent["note"], "createdAt": "2026-10-16T00:04:00Z"}
    sync_once(books, fake, "2026-10-16T09:00:00.000000Z")
    assert tuple(_rows(books, "SELECT status FROM fin_transfers")[0]) == ("unknown",)
    before = len(fake.sent("transactions"))
    sync_once(books, fake, "2026-10-16T09:30:00.000000Z")
    assert tuple(_rows(books, "SELECT status, provider_txn_id FROM fin_transfers")[0]) == ("submitted", "leg-out")
    # the next poll reads on from where the last one stopped
    assert fake.sent("transactions")[before:][0] == list(fake.txns)[4_999]


def test_a_transfer_needs_both_accounts_in_the_requesters_view_and_names_neither_otherwise(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    src_ledger, _ = _ledger(books, src[0])
    with books.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.pay"})
        nothing = act(c, "human:ben", "payment_create", _payload(src, dst, 10_000))
        assert nothing.code == "denied" and nothing.status != "committed", nothing
        # The checking account alone is not enough: both accounts must be in the view, and the refusal names neither.
        act(c, OWNER, "view_grant", {"subject_kind": "teammate", "subject": "human:ben", "what": "bs_lines",
                                     "whose": "company", "account_ids": [src_ledger]})
        one = act(c, "human:ben", "payment_create", _payload(src, dst, 10_000))
        assert one.code == "denied" and one.status != "committed", one
        for name in (src[2], dst[2]):
            assert name not in nothing.reason and name not in one.reason
        assert c.execute("SELECT count(*) FROM fin_transfers").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM fin_outbox WHERE op='transfer_create'").fetchone()[0] == 0


def test_a_rule_on_accounts_matches_a_transfer_by_its_two_ledger_accounts(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    _, src_code = _ledger(books, src[0])
    with books.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.pay"})
        _view(c, "human:ben")
        made = act(c, OWNER, "rule_create", {
            "name": "Checking moves ask", "when": {"kind": "payment_create", "accounts": src_code},
            "outcome": {"outcome": "needs_approval", "who": "owner"},
            "examples": [{"input": {"kind": "payment_create", "accounts": [src_code]}, "expect": "needs_approval"}]})
        assert made.status == "committed", made
        asked = act(c, "human:ben", "payment_create", _payload(src, dst, 10_000))
        assert asked.status == "needs_approval" and asked.approver == OWNER, asked
        assert c.execute("SELECT count(*) FROM fin_transfers").fetchone()[0] == 0


def test_a_queued_transfer_whose_payer_lost_the_right_is_cancelled_and_nothing_is_sent(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    _queued_by_ben(books, src, dst)
    with books.transaction() as c:
        act(c, OWNER, "right_revoke", {"teammate": "human:ben", "right": "finance.pay"})
    sync_once(books, fake)
    _assert_cancelled_unsent(books, fake)


def test_a_deny_rule_added_after_a_transfer_is_queued_cancels_it_before_it_goes(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    _queued_by_ben(books, src, dst)
    with books.transaction() as c:
        made = act(c, OWNER, "rule_create", {
            "name": "No bank moves", "when": {"kind": "payment_create", "method": "internal"},
            "outcome": {"outcome": "deny"},
            "examples": [{"input": {"kind": "payment_create", "method": "internal"}, "expect": "deny"}]})
        assert made.status == "committed", made
    sync_once(books, fake)
    _assert_cancelled_unsent(books, fake)


def test_an_approval_rule_added_after_queueing_holds_the_transfer_and_asks_once(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    _, src_code = _ledger(books, src[0])
    _queued_by_ben(books, src, dst)
    with books.transaction() as c:
        made = act(c, OWNER, "rule_create", {
            "name": "Checking asks", "when": {"kind": "payment_create", "accounts": src_code},
            "outcome": {"outcome": "needs_approval", "who": "owner"},
            "examples": [{"input": {"kind": "payment_create", "accounts": [src_code]}, "expect": "needs_approval"}]})
        assert made.status == "committed", made
    sync_once(books, fake)
    sync_once(books, fake, LATER)
    assert fake.sent("create_transfer") == []
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "queued"
    status, claimed, due = _rows(books, "SELECT status, claimed_at, next_try_at FROM fin_outbox "
                                        "WHERE op='transfer_create'")[0]
    assert status == "pending" and claimed is None and due is not None
    assert _rows(books, "SELECT count(*) FROM tasks WHERE title LIKE 'Transfer of%is waiting'")[0][0] == 1


def test_payments_switched_off_for_the_bank_after_queueing_cancel_the_transfer(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    _queued_by_ben(books, src, dst)
    with books.transaction() as c:
        c.execute("UPDATE fin_connections SET settings=json_set(settings, '$.payments_enabled', "
                  "json('false'))")
    sync_once(books, fake)
    _assert_cancelled_unsent(books, fake)


def test_a_queued_transfer_is_cancelled_before_its_call_is_claimed_and_then_cannot_be_cancelled_again(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        tid = str(act(c, OWNER, "payment_create", _payload(src, dst, 10_000)).result["transfer_id"])
        cancel = act(c, OWNER, "payment_cancel", {"transfer_id": tid})
        assert cancel.status == "committed", cancel
        again = act(c, OWNER, "payment_cancel", {"transfer_id": tid})
        assert again.code == "conflict", again
    sync_once(books, fake)
    sync_once(books, fake, LATER)
    assert fake.sent("create_transfer") == []
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "cancelled"
    assert _rows(books, "SELECT status FROM fin_outbox WHERE op='transfer_create'")[0][0] == "failed"


def test_a_transfer_already_sent_or_whose_call_is_claimed_cannot_be_cancelled(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        tid = str(act(c, OWNER, "payment_create", _payload(src, dst, 10_000)).result["transfer_id"])
    sync_once(books, fake)
    with books.transaction() as c:
        sent = act(c, OWNER, "payment_cancel", {"transfer_id": tid})
        assert sent.code == "conflict" and "Mercury" in sent.reason, sent
    with books.transaction() as c:
        claimed_id = str(act(c, OWNER, "payment_create", _payload(src, dst, 20_000)).result["transfer_id"])
        c.execute("UPDATE fin_outbox SET claimed_at=? WHERE op='transfer_create' AND payload LIKE ?",
                  (AT, f"%{claimed_id}%"))
        held = act(c, OWNER, "payment_cancel", {"transfer_id": claimed_id})
        assert held.code == "conflict" and "Mercury" in held.reason, held
    assert len(fake.sent("create_transfer")) == 1


@pytest.mark.parametrize("day, settles", [("2026-10-01", False), ("2026-10-14", False), ("2026-10-15", True),
                                          ("2026-10-20", True), ("2026-10-21", False)])
def test_legs_settle_a_transfer_only_when_dated_from_its_submit_day_through_five_days_on(books, day, settles):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, 80_000)).status == "committed"
    sync_once(books, fake)                                  # sent on the submit day, 2026-10-15
    _arrive(fake, src[1], dst[1], 800.0, day=day)
    sync_once(books, fake, LATER)
    status = _rows(books, "SELECT status FROM fin_transfers")[0][0]
    assert status == ("posted" if settles else "submitted"), (day, status)


def test_when_mercury_returns_a_transfer_id_its_legs_settle_that_transfer_not_an_older_one(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        first = str(act(c, OWNER, "payment_create", _payload(src, dst, 80_000)).result["transfer_id"])
    sync_once(books, fake)                                  # sent as xfer-1
    with books.transaction() as c:
        second = str(act(c, OWNER, "payment_create", _payload(src, dst, 80_000), at=LATER).result["transfer_id"])
    sync_once(books, fake, LATER)                           # sent as xfer-2, the newer one
    # The out leg is Mercury's transaction for the newer transfer, so its id is that transfer's provider id. (A leg
    # carrying a request id is a payment's, and is never paired as a transfer leg.)
    for raw_id, account, value in (("xfer-2", src[1], -800.0), ("leg-in", dst[1], 800.0)):
        fake.txns[raw_id] = _leg(raw_id, account, value)
        fake.events_list.append({"id": f"evt-{raw_id}", "resourceType": "transaction", "resourceId": raw_id,
                                 "operationType": "create", "resourceVersion": 1})
    sync_once(books, fake, POLL)
    rows = dict(_rows(books, "SELECT id, status FROM fin_transfers"))
    assert rows[second] == "posted" and rows[first] == "submitted", rows


def _assert_private_to_owner(store, title):
    """The one Needs-you task with this title is private: the owner (its assignee) reads it, a teammate outside Finance
    does not."""
    with store.read() as c:
        (task_id,) = [r[0] for r in c.execute("SELECT id FROM tasks WHERE title LIKE ?", (title,)).fetchall()]
        row = H.task(c, task_id)
        assert row["private"] == 1, dict(row)
        assert H.task_private_readable(c, OWNER, row)
        assert not H.task_private_readable(c, "human:gus", row)


def test_a_limit_added_after_transfers_were_queued_is_reserved_at_send_once(books):
    """Two $100 moves are queued; a $100 a day limit is added before they go. The first fits and reserves the counter
    at its send claim; the second is over it and is cancelled, never sent."""
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, 10_000, "first")).status == "committed"
        assert act(c, OWNER, "payment_create", _payload(src, dst, 10_000, "second")).status == "committed"
        cap = act(c, OWNER, "rule_create", {
            "name": "Moves per day", "when": {"kind": "payment_create", "method": "internal"},
            "outcome": {"outcome": "limit", "limit": 10_000, "per": "day"},
            "examples": [{"input": {"kind": "payment_create", "method": "internal", "amount": 10}, "expect": "limit"}]})
        assert cap.status == "committed", cap
        rule_id = str(cap.result["rule_id"])
    sync_once(books, fake)
    assert len(fake.sent("create_transfer")) == 1
    with books.read() as c:
        assert tuple(c.execute("SELECT sum(amount), sum(count) FROM fin_rule_counters WHERE rule_id=?",
                               (rule_id,)).fetchone()) == (10_000, 1)
        assert sorted(r[0] for r in c.execute("SELECT status FROM fin_transfers")) == ["cancelled", "submitted"]


def test_a_refused_transfer_gives_back_the_limit_it_reserved(books):
    """A transfer that reserved a monthly limit at its send claim, then was refused by Mercury, takes it back: only the
    transfer that went is counted."""
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, 10_000, "refused")).status == "committed"
        assert act(c, OWNER, "payment_create", _payload(src, dst, 20_000, "sent")).status == "committed"
        rule_id = str(act(c, OWNER, "rule_create", {
            "name": "Moves per month", "when": {"kind": "payment_create", "method": "internal"},
            "outcome": {"outcome": "limit", "limit": 50_000, "per": "month"},
            "examples": [{"input": {"kind": "payment_create", "method": "internal", "amount": 10}, "expect": "limit"}]}
        ).result["rule_id"])
    fake.queue["create_transfer"] = [Answer(422, {"message": "insufficient funds"})]
    sync_once(books, fake)
    with books.read() as c:
        (sent_amount,) = c.execute("SELECT amount FROM fin_transfers WHERE status='submitted'").fetchone()
        assert c.execute("SELECT count(*) FROM fin_transfers WHERE status='failed'").fetchone()[0] == 1
        assert tuple(c.execute("SELECT sum(amount), sum(count) FROM fin_rule_counters WHERE rule_id=?",
                               (rule_id,)).fetchone()) == (sent_amount, 1)


def test_a_deny_on_the_owner_rule_added_after_someone_else_queued_a_transfer_cancels_it(books):
    """Ben queues a move; the owner then adds a deny for payments by anyone but the owner. The send-time facts carry
    actor.owner as the request did, so the rule applies and nothing is sent."""
    fake = FakeMercury()
    src, dst = _two(books, fake)
    _queued_by_ben(books, src, dst)
    with books.transaction() as c:
        made = act(c, OWNER, "rule_create", {
            "name": "Only the owner moves money", "when": {"kind": "payment_create", "actor.owner": False},
            "outcome": {"outcome": "deny"},
            "examples": [{"input": {"kind": "payment_create", "actor.owner": False}, "expect": "deny"}]})
        assert made.status == "committed", made
    sync_once(books, fake)
    _assert_cancelled_unsent(books, fake)


def test_a_cancelled_transfer_notice_is_private_to_the_one_who_must_act(books):
    """Ben's payment right is revoked after he queued a $100 move: the cancellation's Needs-you names the amount, so
    it is private to the owner, and a teammate outside Finance cannot read it."""
    fake = FakeMercury()
    src, dst = _two(books, fake)
    _queued_by_ben(books, src, dst)
    with books.transaction() as c:
        act(c, OWNER, "right_revoke", {"teammate": "human:ben", "right": "finance.pay"})
    sync_once(books, fake)
    _assert_cancelled_unsent(books, fake)
    _assert_private_to_owner(books, "Transfer of 100.00 was not sent%")


def test_a_waiting_transfer_notice_is_private_to_the_one_who_must_act(books):
    """A rule that asks the owner about the checking account, added after Ben queued a $100 move: the waiting Needs-you
    names the amount, so it is private to the owner."""
    fake = FakeMercury()
    src, dst = _two(books, fake)
    _, src_code = _ledger(books, src[0])
    _queued_by_ben(books, src, dst)
    with books.transaction() as c:
        made = act(c, OWNER, "rule_create", {
            "name": "Checking asks", "when": {"kind": "payment_create", "accounts": src_code},
            "outcome": {"outcome": "needs_approval", "who": "owner"},
            "examples": [{"input": {"kind": "payment_create", "accounts": [src_code]}, "expect": "needs_approval"}]})
        assert made.status == "committed", made
    sync_once(books, fake)
    assert fake.sent("create_transfer") == []
    _assert_private_to_owner(books, "Transfer of 100.00 is waiting%")


def test_a_transfer_cut_off_before_midnight_settles_on_the_legs_of_its_submit_day(books):
    """The worker claimed the call at 23:58 on the 15th; Mercury took the transfer and the worker stopped before it
    recorded the answer. The recovery runs the next morning. Its legs are dated the 15th, so they settle it: the window
    starts when the call was made, not when it was recovered."""
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, 80_000)).status == "committed"
        c.execute("UPDATE fin_outbox SET claimed_at=? WHERE op='transfer_create'", ("2026-10-15T23:58:00.000000Z",))
    sync_once(books, fake, "2026-10-16T09:00:00.000000Z")
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "unknown"
    _arrive(fake, src[1], dst[1], 800.0, day="2026-10-15")
    sync_once(books, fake, "2026-10-16T09:30:00.000000Z")
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "posted"
    assert fake.sent("create_transfer") == []


def test_a_transfer_cut_off_mid_call_is_found_by_its_note_key_and_takes_mercurys_created_time(books):
    """Mercury took the transfer and the answer was lost: it is unknown and never sent again. Its out-leg carries the
    transfer's key in its note, so it is found in Mercury's transactions, and the created time Mercury gave it is the
    submit day the legs post in (the claim time stands only until Mercury returns it)."""
    fake = FakeMercury()
    src, dst = _two(books, fake)
    fake.queue["create_transfer"] = [Answer(None, {})]          # sent, but no answer came back
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, 80_000)).status == "committed"
    sync_once(books, fake, "2026-10-15T23:58:00.000000Z")
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "unknown"
    (sent,) = fake.sent("create_transfer")
    assert str(sent["note"]).startswith("tico:")                 # the key travels in the note, as payments' do
    fake.txns["leg-out"] = {**_leg("leg-out", src[1], -800.0, "2026-10-16"), "note": sent["note"],
                            "createdAt": "2026-10-16T00:04:00Z"}   # Mercury's own submit time, after midnight
    sync_once(books, fake, "2026-10-16T09:00:00.000000Z")
    assert tuple(_rows(books, "SELECT status, provider_txn_id, submitted_at FROM fin_transfers")[0]) == \
        ("submitted", "leg-out", "2026-10-16T00:04:00.000000Z")
    _arrive(fake, src[1], dst[1], 800.0, day="2026-10-16")
    sync_once(books, fake, "2026-10-16T09:30:00.000000Z")
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "posted"
    assert len(fake.sent("create_transfer")) == 1


def test_a_transfer_keeps_both_of_mercurys_legs_from_its_answer(books):
    """Mercury answers a transfer with both of its legs, each a transaction with its own id: both are kept."""
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, 10_000)).status == "committed"
    sync_once(books, fake)
    assert tuple(_rows(books, "SELECT status, provider_txn_id, provider_credit_txn_id FROM fin_transfers")[0]) == \
        ("submitted", "xfer-1", "xfer-1-credit")


def test_an_unknown_transfer_is_searched_by_its_note_in_its_account_before_all_transactions_are_read(books):
    """The lookup narrows first: its account's transactions matching its note, within the days around its claim. Its
    out-leg is found there and takes Mercury's created time."""
    fake = FakeMercury()
    src, dst = _two(books, fake)
    fake.queue["create_transfer"] = [Answer(None, {})]          # sent, but no answer came back
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, 80_000)).status == "committed"
    sync_once(books, fake, "2026-10-15T23:58:00.000000Z")
    (sent,) = fake.sent("create_transfer")
    fake.txns["leg-out"] = {**_leg("leg-out", src[1], -800.0, "2026-10-16"), "note": sent["note"],
                            "createdAt": "2026-10-16T00:04:00Z"}
    sync_once(books, fake, "2026-10-16T09:00:00.000000Z")
    account, searched, start, end = fake.sent("account_transactions")[-1]
    assert (account, searched, start, end) == (src[1], sent["note"], "2026-10-14", "2026-10-16")
    assert tuple(_rows(books, "SELECT status, provider_txn_id, submitted_at FROM fin_transfers")[0]) == \
        ("submitted", "leg-out", "2026-10-16T00:04:00.000000Z")


def test_an_unknown_transfer_the_search_misses_is_still_found_in_the_full_list(books):
    """Its key only in another memo field, which the search does not read: every transaction is read, and it is found."""
    fake = FakeMercury()
    src, dst = _two(books, fake)
    fake.queue["create_transfer"] = [Answer(None, {})]
    with books.transaction() as c:
        assert act(c, OWNER, "payment_create", _payload(src, dst, 80_000)).status == "committed"
    sync_once(books, fake, "2026-10-15T23:58:00.000000Z")
    (sent,) = fake.sent("create_transfer")
    fake.txns["leg-out"] = {**_leg("leg-out", src[1], -800.0, "2026-10-16"), "note": None,
                            "externalMemo": sent["note"], "createdAt": "2026-10-16T00:04:00Z"}
    sync_once(books, fake, "2026-10-16T09:00:00.000000Z")
    assert fake.sent("account_transactions")[-1][1] == sent["note"]          # searched first, and it missed
    assert tuple(_rows(books, "SELECT status, provider_txn_id FROM fin_transfers")[0]) == ("submitted", "leg-out")


def test_the_transfers_read_lists_moves_between_accounts_the_viewer_sees_both_of_newest_first(books):
    from backend.finance import reads as FR
    fake = FakeMercury()
    src, dst = _two(books, fake)
    tid = _queued_by_ben(books, src, dst)
    src_ledger, _ = _ledger(books, src[0])
    with books.transaction() as c:
        mine = FR.transfers(c, "human:ben")
        (row,) = mine["transfers"]
        assert row["id"] == tid and row["status"] == "queued" and row["amount"] == 10_000 and mine["next_cursor"] is None
        assert (row["from"]["id"], row["to"]["id"]) == (src[0], dst[0]) and row["created_by"] == "human:ben"
        assert FR.transfers(c, OWNER)["transfers"][0]["id"] == tid
        # One of the two accounts is not enough to see the move.
        act(c, OWNER, "view_grant", {"subject_kind": "teammate", "subject": "human:cara", "what": "bs_lines",
                                     "whose": "company", "account_ids": [src_ledger]})
        assert FR.transfers(c, "human:cara")["transfers"] == []
        act(c, "human:ben", "payment_cancel", {"transfer_id": tid})
        assert FR.transfers(c, "human:ben")["transfers"][0]["status"] == "cancelled"
