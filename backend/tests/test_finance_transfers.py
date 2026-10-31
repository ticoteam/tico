"""Internal transfers (spec §5, §9): a payment_create with method "internal" moves money between two of the company's own
bank accounts through Mercury's transfer endpoint (backend/finance/transfers.py). Covers the payment gates (over $1,000
asks the owner; the new-recipient rule never applies), approval, posting that nets to zero on P&L and settles the
transfer, a leg that comes back cancelled, an unknown or refused result never sent again, replay, and validation. The
fake Mercury stands in for the bank; synthetic fixtures."""
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


def _leg(raw_id, account, amount, day="2026-10-14"):
    """One side of a move between two of the company's accounts, as Mercury's feed shows it."""
    return {**MERCURY["txn_bank_fee"], "id": raw_id, "accountId": account, "amount": amount,
            "kind": "internalTransfer", "createdAt": f"{day}T10:00:00Z", "postedAt": f"{day}T10:00:00Z",
            "counterpartyName": "Acme"}


def _arrive(fake, out_account, in_account, amount_dollars):
    """Both legs reach the feed after the bank worker's first backfill, as transaction events it reads on its next pass."""
    for raw_id, account, value in (("leg-out", out_account, -amount_dollars), ("leg-in", in_account, amount_dollars)):
        fake.txns[raw_id] = _leg(raw_id, account, value)
        fake.events_list.append({"id": f"evt-{raw_id}", "resourceType": "transaction", "resourceId": raw_id,
                                 "operationType": "create", "resourceVersion": 1})


def _payload(src, dst, amount=25_000, memo="to savings"):
    return {"method": "internal", "from_account": src[0], "to_account": dst[0], "amount": amount, "memo": memo}


def test_an_internal_transfer_over_1000_asks_the_owner_then_goes_to_mercury_once(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.pay"})
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
