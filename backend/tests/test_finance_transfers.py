"""Internal transfers (gaps brief item 4, backend/finance/transfers.py): money between two of the company's own bank
accounts, through the outbox to Mercury's transfer endpoint. Covers the approval path, the default allow for a transfer
that is not money out, posting as a transfer that nets to zero on P&L, an unknown or refused result never sent again,
replay, and validation. The fake Mercury stands in for the bank; synthetic fixtures."""
from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance.store import schema
from backend.finance.mercury import Answer
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, act, books, connect, finance_template, store, sync_once)

LATER = "2026-10-15T14:00:00.000000Z"
LATEST = "2026-10-15T20:00:00.000000Z"


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


def _leg(raw_id, account, amount, day="2026-10-14"):
    """One side of a move between two of the company's accounts, as Mercury's feed shows it."""
    return {**MERCURY["txn_bank_fee"], "id": raw_id, "accountId": account, "amount": amount,
            "kind": "internalTransfer", "createdAt": f"{day}T10:00:00Z", "postedAt": f"{day}T10:00:00Z",
            "counterpartyName": "Acme"}


def _payload(src, dst, amount=25_000, memo="to savings"):
    return {"from_account": src[0], "to_account": dst[0], "amount": amount, "memo": memo}


def _by_provider(store, provider_id):
    """(money account id, Mercury account id, name) for the synced account with this Mercury id."""
    with store.read() as c:
        return tuple(c.execute("SELECT id, provider_account_id, name FROM fin_money_accounts "
                               "WHERE provider_account_id=?", (provider_id,)).fetchone())


def _rows(store, sql, args=()):
    with store.read() as c:
        return c.execute(sql, args).fetchall()


def test_a_transfer_waits_for_an_approval_rule_then_goes_to_mercury_once(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.pay"})
        rule = act(c, OWNER, "rule_create", {
            "name": "Transfers ask", "when": {"kind": "transfer_create"},
            "outcome": {"outcome": "needs_approval", "who": "owner"},
            "examples": [{"input": {"kind": "transfer_create"}, "expect": "needs_approval"}]})
        assert rule.status == "committed", rule
        asked = act(c, "human:ben", "transfer_create", _payload(src, dst))
        assert asked.status == "needs_approval" and asked.approval_id, asked
        assert c.execute("SELECT count(*) FROM fin_transfers").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM fin_outbox WHERE op='transfer_create'").fetchone()[0] == 0
        H.approval_decide(c, OWNER, asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done is not None and done.status == "committed", done
    sync_once(books, fake)
    (body,) = fake.sent("create_transfer")
    assert body["sourceAccountId"] == src[1] and body["destinationAccountId"] == dst[1]
    assert body["amount"] == 250 and str(body["idempotencyKey"]).startswith("transfer-")
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "submitted"


def test_a_transfer_is_allowed_by_default_and_posts_as_a_transfer_that_nets_to_zero_on_pnl(books):
    fake = FakeMercury()
    connect(books, fake)
    # The backfill reads /transactions once, at the first sync, so both legs must already be in Mercury by then.
    fake.txns["leg-out"] = _leg("leg-out", "acct-chk", -2500.0)
    fake.txns["leg-in"] = _leg("leg-in", "acct-sav", 2500.0)
    sync_once(books, fake)
    src, dst = _by_provider(books, "acct-chk"), _by_provider(books, "acct-sav")
    with books.transaction() as c:
        # Over $1,000: a payment would need the owner's approval (§6.4 rule 2); a transfer is not money out.
        made = act(c, OWNER, "transfer_create", _payload(src, dst, amount=250_000))
        assert made.status == "committed", made
    sync_once(books, fake, LATER)
    assert len(fake.sent("create_transfer")) == 1
    legs = _rows(books, "SELECT status FROM fin_feed_txns WHERE provider_id IN ('leg-out', 'leg-in')")
    assert [r[0] for r in legs] == ["posted", "posted"]
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


def test_an_unknown_result_is_never_sent_again_and_a_person_is_asked_to_check(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    fake.queue["create_transfer"] = [Answer(None, {})]          # sent, but no answer came back
    with books.transaction() as c:
        assert act(c, OWNER, "transfer_create", _payload(src, dst, 10_000)).status == "committed"
    sync_once(books, fake)
    sync_once(books, fake, LATER)
    sync_once(books, fake, LATEST)
    assert len(fake.sent("create_transfer")) == 1
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "unknown"
    assert _rows(books, "SELECT status FROM fin_outbox WHERE op='transfer_create'")[0][0] == "failed"
    assert _rows(books, "SELECT count(*) FROM tasks WHERE title LIKE 'Check Mercury for transfer%'")[0][0] == 1


def test_a_refused_transfer_is_failed_not_sent_again_and_reported(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    fake.queue["create_transfer"] = [Answer(422, {"message": "insufficient funds"})]
    with books.transaction() as c:
        assert act(c, OWNER, "transfer_create", _payload(src, dst, 10_000)).status == "committed"
    sync_once(books, fake)
    sync_once(books, fake, LATER)
    assert len(fake.sent("create_transfer")) == 1
    assert _rows(books, "SELECT status FROM fin_transfers")[0][0] == "failed"
    assert _rows(books, "SELECT count(*) FROM tasks WHERE title LIKE 'Mercury refused transfer%'")[0][0] == 1


def test_a_replayed_transfer_is_the_same_transfer_and_goes_to_mercury_once(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        first = act(c, OWNER, "transfer_create", _payload(src, dst, 10_000), key="move-1")
        again = act(c, OWNER, "transfer_create", _payload(src, dst, 10_000), key="move-1")
    assert first.status == "committed" and again.action_id == first.action_id and again.replayed
    assert len(_rows(books, "SELECT id FROM fin_transfers")) == 1
    assert len(_rows(books, "SELECT id FROM fin_outbox WHERE op='transfer_create'")) == 1
    sync_once(books, fake)
    sync_once(books, fake, LATER)
    assert len(fake.sent("create_transfer")) == 1


def test_a_transfer_needs_two_of_the_companys_bank_accounts_and_money_above_zero(books):
    fake = FakeMercury()
    src, dst = _two(books, fake)
    with books.transaction() as c:
        same = act(c, OWNER, "transfer_create", _payload(src, src))
        zero = act(c, OWNER, "transfer_create", _payload(src, dst, 0))
        nothing = act(c, OWNER, "transfer_create", {**_payload(src, dst), "to_account": "mny_missing"})
    assert same.code == "invalid" and zero.code == "invalid" and nothing.code == "not_found", (same, zero, nothing)
    assert _rows(books, "SELECT count(*) FROM fin_transfers")[0][0] == 0


def test_migration_73_adds_fin_transfers_to_an_install_made_before_it_and_running_it_again_changes_nothing(books):
    with books.transaction() as c:
        c.execute("DROP TABLE fin_transfers")
        schema.gaps_books(c)
        schema.gaps_books(c)
        found = c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='fin_transfers'").fetchone()
        assert found[0] == 1
