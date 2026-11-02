"""The bank worker and card desired state, against a fake Mercury (review findings A, D, E, F, M, N, R): writes are
claimed before their call and a cut-off create is never sent blindly again; a card's provider writes follow its latest
desired state; a lower limit or a cancel keeps the reservation until Mercury confirms; ingestion is atomic per
transaction and retried; inbox events belong to their connection; older snapshots never win."""
import time

from backend.credentials import Vault
from backend.finance import banking, sync
from backend.finance.mercury import Answer
from backend.finance.store import banking as SK
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, account, act, books, connect, finance_template, store, sync_once)

HOURS = [f"2026-10-15T{h:02d}:30:00.000000Z" for h in range(13, 24)]


def _card(store, amount=20_000, budget=30_000):
    with store.transaction() as c:
        b = act(c, OWNER, "budget_create", {"name": "Cloud", "account_id": account(c, "6100"), "amount": budget,
                                            "at_limit": "decline"})
        made = act(c, OWNER, "card_create", {"holder": "human:eve", "budget_id": b.result["budget_id"],
                                             "amount": amount, "provider_user_id": "user-eve"})
        assert made.status == "committed", made
    return str(b.result["budget_id"]), str(made.result["card_id"])


def _state(c, card_id):
    row = c.execute("SELECT status, provider_status, limit_amount, provider_limit, budget_id FROM fin_cards WHERE id=?",
                    (card_id,)).fetchone()
    alloc = c.execute("SELECT amount FROM fin_allocations WHERE target_kind='card' AND target_id=?",
                      (card_id,)).fetchone()
    return (row[0], row[1], row[2], row[3], int(alloc[0]) if alloc else None)


def _asks(c, title):
    return c.execute("SELECT count(*) FROM tasks WHERE title LIKE ?", (f"{title}%",)).fetchone()[0]


def test_a_webhook_reads_the_balances_now_and_the_poll_stays_every_fifteen_minutes(books):
    """§9: balances come from webhooks. Mercury's balance event (a checkingAccount update, the recorded shape) waits on
    the connection; its pass reads the balance then, not at the next poll, and the poll itself is unchanged."""
    fake = FakeMercury()
    connect(books, fake)
    sync_once(books, fake)
    event = MERCURY["event_balance"]
    with books.transaction() as c:
        cid = str(c.execute("SELECT id FROM fin_connections").fetchone()[0])
        SK.inbox_add(c, connection_id=cid, provider="mercury", event_id=str(event["id"]), payload=event, at=AT)
    with books.read() as c:
        before = c.execute("SELECT m.balance, k.next_sync_at FROM fin_money_accounts m JOIN fin_connections k "
                           "ON k.id=m.connection_id WHERE m.provider_account_id='acct-chk'").fetchone()
    new = event["mergePatch"]["currentBalance"]
    fake.balances["acct-chk"] = new
    sync_once(books, fake, "2026-10-15T12:01:00.000000Z")       # a minute on: the poll is not due
    with books.read() as c:
        after = c.execute("SELECT m.balance, k.next_sync_at FROM fin_money_accounts m JOIN fin_connections k "
                          "ON k.id=m.connection_id WHERE m.provider_account_id='acct-chk'").fetchone()
    assert before[0] != round(new * 100)
    assert after[0] == round(new * 100)
    assert after[1] == before[1] and after[1] is not None


def test_a_cut_off_card_create_is_never_sent_again_and_a_claim_is_won_once(books):
    """A: the outbox row is claimed before the call; one claimed and never recorded goes to a finance admin."""
    fake = FakeMercury()
    connect(books, fake)
    _, card_id = _card(books)
    with books.transaction() as c:
        oid = str(c.execute("SELECT id FROM fin_outbox WHERE op='card_create'").fetchone()[0])
        assert SK.outbox_claim(c, oid, AT) and not SK.outbox_claim(c, oid, AT)       # only one worker wins
    sync_once(books, fake, HOURS[0])                                                   # an hour later
    assert fake.sent("create_card") == []
    with books.read() as c:
        status, why = c.execute("SELECT status, last_error FROM fin_outbox WHERE id=?", (oid,)).fetchone()
        assert status == "failed" and "check Mercury" in why
        assert _asks(c, "Check Mercury for a card") == 1
        assert c.execute("SELECT sync_status FROM fin_cards WHERE id=?", (card_id,)).fetchone()[0] == "failed"


def test_changes_before_the_card_exists_apply_once_mercury_issues_it(books):
    """F: a lower limit and a freeze made before Mercury has the card: the create reads the latest limit, and the
    freeze follows once the card exists."""
    fake = FakeMercury()
    connect(books, fake)
    _, card_id = _card(books)
    with books.transaction() as c:
        assert act(c, OWNER, "card_update_limit", {"card_id": card_id, "amount": 15_000}).status == "committed"
        assert act(c, OWNER, "card_freeze", {"card_id": card_id}).status == "committed"
        assert _state(c, card_id)[4] == 15_000             # not issued, not being issued: the create reads 150.00
    sync_once(books, fake, HOURS[0])
    sync_once(books, fake, HOURS[1])
    assert fake.sent("create_card") == [("user-eve", 15_000)]
    assert fake.sent("card_state") == [("card-eve", "freeze")]
    with books.read() as c:
        assert _state(c, card_id) == ("frozen", "frozen", 15_000, 15_000, 15_000)


def test_a_card_whose_allocation_goes_to_zero_before_it_is_issued_is_not_issued_until_it_has_one(books):
    """PR review P1-b: allocation_set(0) before Mercury has the card froze it and freed its slice at once, yet the
    create still issued a 200.00 card. Now nothing is issued while it has no allocation; a new allocation issues it
    at that amount (and the freeze follows, as any change made before it existed)."""
    fake = FakeMercury()
    connect(books, fake)
    budget_id, card_id = _card(books)
    with books.transaction() as c:
        zero = act(c, OWNER, "allocation_set", {"budget_id": budget_id, "target_kind": "card", "target_id": card_id,
                                                "amount": 0})
        assert zero.status == "committed", zero
        assert _state(c, card_id)[0] == "frozen" and _state(c, card_id)[4] in (0, None)
    sync_once(books, fake, HOURS[0])
    sync_once(books, fake, HOURS[1])
    assert fake.sent("create_card") == []                                       # no card with nothing reserved
    with books.transaction() as c:
        assert c.execute("SELECT sync_status FROM fin_cards WHERE id=?", (card_id,)).fetchone()[0] == "waiting"
        again = act(c, OWNER, "allocation_set", {"budget_id": budget_id, "target_kind": "card", "target_id": card_id,
                                                 "amount": 12_000})
        assert again.status == "committed", again
    sync_once(books, fake, HOURS[2])
    sync_once(books, fake, HOURS[3])
    assert fake.sent("create_card") == [("user-eve", 12_000)]
    assert fake.sent("card_state") == [("card-eve", "freeze")]
    with books.read() as c:
        assert _state(c, card_id) == ("frozen", "frozen", 12_000, 12_000, 12_000)


def test_a_card_cancelled_before_it_is_issued_is_never_created(books):
    fake = FakeMercury()
    connect(books, fake)
    _, card_id = _card(books)
    with books.transaction() as c:
        oid = str(c.execute("SELECT id FROM fin_outbox WHERE op='card_create'").fetchone()[0])
        SK.outbox_claim(c, oid, HOURS[0])          # being issued right now: the cancel must wait for Mercury
        assert act(c, OWNER, "card_cancel", {"card_id": card_id}).status == "committed"
        assert _state(c, card_id)[4] == 20_000
        c.execute("UPDATE fin_outbox SET claimed_at=NULL WHERE id=?", (oid,))
    sync_once(books, fake, HOURS[0])                # the create sees the cancel and issues nothing
    assert fake.sent("create_card") == []
    with books.read() as c:
        assert _state(c, card_id)[4] == 0


def test_lower_limits_and_cancels_keep_the_reservation_until_mercury_confirms(books):
    """E and F: a lower limit holds its old reservation until Mercury confirms it, superseded limits are never sent,
    a limit can't go under what the card used, and a cancel frees the slice only once Mercury cancels."""
    fake = FakeMercury()
    connect(books, fake)
    budget_id, card_id = _card(books)
    sync_once(books, fake, HOURS[0])
    with books.transaction() as c:
        assert act(c, OWNER, "card_update_limit", {"card_id": card_id, "amount": 10_000}).status == "committed"
        assert _state(c, card_id)[4] == 20_000                                  # still reserved at 200.00
        assert act(c, OWNER, "card_create", {"holder": "human:fay", "budget_id": budget_id, "amount": 15_000,
                                             "provider_user_id": "u"}).code == "invalid"   # the room isn't free yet
    fake.queue["card_limit"] = [Answer(500, {})]
    sync_once(books, fake, HOURS[1])
    with books.read() as c:
        assert _state(c, card_id)[3:] == (20_000, 20_000)                       # Mercury still allows 200.00
    with books.transaction() as c:                                             # two more edits before the retry
        assert act(c, OWNER, "card_update_limit", {"card_id": card_id, "amount": 12_000}).status == "committed"
        assert act(c, OWNER, "card_update_limit", {"card_id": card_id, "amount": 11_000}).status == "committed"
    sync_once(books, fake, HOURS[3])
    assert [a for _, a in fake.sent("card_limit")] == [10_000, 11_000]         # 120.00 was superseded, never sent
    with books.read() as c:
        assert _state(c, card_id) == ("active", "active", 11_000, 11_000, 11_000)
    fake.txns["txn-card-1"] = dict(MERCURY["txn_card_pending"])               # a 42.50 authorization
    fake.events_list.append({"id": "evt-a", "resourceType": "transaction", "resourceId": "txn-card-1",
                             "operationType": "create", "resourceVersion": 1})
    sync_once(books, fake, HOURS[4])
    with books.transaction() as c:
        low = act(c, OWNER, "card_update_limit", {"card_id": card_id, "amount": 4_000})
        assert low.code == "invalid" and "used" in low.reason
        assert act(c, OWNER, "allocation_set", {"budget_id": budget_id, "target_kind": "card", "target_id": card_id,
                                                "amount": 4_000}).code == "invalid"
        assert act(c, OWNER, "card_cancel", {"card_id": card_id}).status == "committed"
        assert _state(c, card_id)[4] == 11_000                                  # until Mercury cancels it
    sync_once(books, fake, HOURS[6])
    assert fake.sent("card_state") == [("card-eve", "cancel")]
    with books.read() as c:
        assert _state(c, card_id) == ("cancelled", "cancelled", 11_000, 11_000, 0)


def test_an_allocation_on_a_card_must_be_on_its_own_budget(books):
    """D: allocation_set can't move a card's limit through another budget, nor reach a card the caller can't see."""
    fake = FakeMercury()
    connect(books, fake)
    _, card_id = _card(books)
    with books.transaction() as c:
        other = act(c, OWNER, "budget_create", {"name": "Other", "account_id": account(c, "6900"), "amount": 90_000})
        assert other.status == "committed", other
        wrong = act(c, OWNER, "allocation_set", {"budget_id": other.result["budget_id"], "target_kind": "card",
                                                 "target_id": card_id, "amount": 50_000})
        assert wrong.code == "invalid" and "own budget" in wrong.reason
        assert act(c, OWNER, "allocation_set", {"budget_id": other.result["budget_id"], "target_kind": "card",
                                                "target_id": "card-nope", "amount": 1}).code == "not_found"
        assert _state(c, card_id)[2] == 20_000


def test_ingestion_is_atomic_per_transaction_and_a_failure_stays_to_retry(books, monkeypatch):
    """M: a transaction that fails to post rolls back whole and its event stays in the inbox, retried later; a late
    transaction dated in a closed month posts today."""
    fake = FakeMercury()
    connect(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
    late = {**MERCURY["txn_bank_fee"], "id": "txn-late", "createdAt": "2026-09-20T10:00:00Z",
            "postedAt": "2026-09-20T10:00:00Z"}
    fake.txns["txn-late"] = late
    fake.events_list.append({"id": "evt-late", "resourceType": "transaction", "resourceId": "txn-late",
                             "operationType": "create", "resourceVersion": 1})
    real = banking._settle

    def broken(*a, **k):
        raise RuntimeError("posting broke")
    monkeypatch.setattr(banking, "_settle", broken)
    sync_once(books, fake, HOURS[0])
    with books.read() as c:
        assert c.execute("SELECT count(*) FROM fin_feed_txns WHERE provider_id='txn-late'").fetchone()[0] == 0
        status, attempts = c.execute("SELECT status, attempts FROM fin_inbox WHERE provider_event_id='evt-late'"
                                     ).fetchone()
        assert (status, attempts) == ("new", 1)
    monkeypatch.setattr(banking, "_settle", real)
    sync_once(books, fake, HOURS[2])
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_inbox WHERE provider_event_id='evt-late'").fetchone()[0] == "done"
        day = c.execute("SELECT e.entry_date FROM fin_entries e JOIN fin_feed_txns f ON f.entry_id=e.id "
                        "WHERE f.provider_id='txn-late'").fetchone()[0]
        assert day == AT[:10]                                                    # not in closed September


def test_inbox_events_belong_to_their_connection_and_a_404_backs_off(books):
    """N: an event for a connection is taken in only by it; a 404 backs off instead of being dropped; an event with
    no connection is done only when the connection that owns the account took it in."""
    fake = FakeMercury()
    connect(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        cid = str(c.execute("SELECT id FROM fin_connections").fetchone()[0])
        SK.inbox_add(c, connection_id=cid, provider="mercury", event_id="evt-early",
                     payload={"resourceType": "transaction", "resourceId": "txn-dep", "resourceVersion": 1}, at=AT)
    sync_once(books, fake, HOURS[0])                         # Mercury doesn't show it yet
    with books.read() as c:
        assert tuple(c.execute("SELECT status, attempts FROM fin_inbox WHERE provider_event_id='evt-early'"
                               ).fetchone()) == ("new", 1)
    fake.txns["txn-dep"] = dict(MERCURY["txn_deposit"])
    sync_once(books, fake, HOURS[2])
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_inbox WHERE provider_event_id='evt-early'").fetchone()[0] == "done"
        assert c.execute("SELECT count(*) FROM fin_feed_txns WHERE provider_id='txn-dep'").fetchone()[0] == 1
        # Scoped reads: another connection's queue is not this one's.
        assert SK.outbox_due(c, HOURS[9], 10, "conn-other") == []
        assert SK.payments_due(c, HOURS[9], 10, "conn-other") == []
        assert SK.inbox_waiting(c, 10, "conn-other") == []


def test_an_older_or_versionless_snapshot_never_overwrites_a_newer_row(books):
    """R: a backfill page (no version) or an older event changes nothing once a versioned event was taken in, and a
    posted row never takes a pending snapshot or a new hold."""
    fake = FakeMercury()
    connect(books, fake)
    sync_once(books, fake)
    posted = {**MERCURY["txn_bank_fee"], "amount": -20.0}
    fake.txns["txn-fee"] = posted
    fake.events_list.append({"id": "evt-v3", "resourceType": "transaction", "resourceId": "txn-fee",
                             "operationType": "update", "resourceVersion": 3})
    sync_once(books, fake, HOURS[0])
    from backend.finance.core import feeds as F
    with books.transaction() as c:
        from backend.finance.context import load
        ctx = load(c, HOURS[1])
        cid = str(c.execute("SELECT id FROM fin_connections").fetchone()[0])
        banking.ingest(ctx, cid, F.mercury_txn({**MERCURY["txn_bank_fee"], "amount": -15.0}, 0))   # backfill page
        banking.ingest(ctx, cid, F.mercury_txn({**MERCURY["txn_bank_fee"], "amount": -16.0}, 2))   # older event
        banking.ingest(ctx, cid, F.mercury_txn({**MERCURY["txn_bank_fee"], "amount": -21.0, "status": "pending",
                                                "postedAt": None}, 4))                            # pending again
        amount, version, pending = c.execute("SELECT amount, version, pending FROM fin_feed_txns "
                                             "WHERE provider_id='txn-fee'").fetchone()
        assert (amount, version, pending) == (-2_000, 3, 0)
        assert c.execute("SELECT count(*) FROM fin_holds").fetchone()[0] == 0


def test_a_cards_writes_go_one_at_a_time_and_an_out_of_step_answer_requeues(books):
    """N1: while one write for a card is at Mercury, a later write for the same card can't be claimed; an answer that
    leaves the card out of step with its desired state keeps a card_sync queued and the card pending."""
    from backend.finance import sync
    fake = FakeMercury()
    connect(books, fake)
    _, card_id = _card(books)
    sync_once(books, fake, HOURS[0])
    with books.transaction() as c:
        assert act(c, OWNER, "card_update_limit", {"card_id": card_id, "amount": 18_000}).status == "committed"
        first = c.execute("SELECT * FROM fin_outbox WHERE op='card_sync' AND status='pending'").fetchone()
        assert SK.outbox_claim(c, str(first["id"]), HOURS[1])             # worker A sends 180.00
        assert act(c, OWNER, "card_update_limit", {"card_id": card_id, "amount": 10_000}).status == "committed"
        second = c.execute("SELECT id FROM fin_outbox WHERE op='card_sync' AND status='pending' AND claimed_at IS NULL"
                           ).fetchone()[0]
        assert not SK.outbox_claim(c, str(second), HOURS[1])              # worker B waits for A
        c.execute("DELETE FROM fin_outbox WHERE id=?", (second,))         # even if B's row were lost...
        sync._record(c, first, {"card_id": card_id}, Answer(200, {"confirmed": {"limit": 18_000}}), HOURS[1])
        assert c.execute("SELECT sync_status FROM fin_cards WHERE id=?", (card_id,)).fetchone()[0] == "pending"
        assert c.execute("SELECT count(*) FROM fin_outbox WHERE op='card_sync' AND status='pending'").fetchone()[0] == 1
        assert _state(c, card_id)[3:] == (18_000, 20_000)                 # Mercury allows 180.00: still reserved
    sync_once(books, fake, HOURS[2])
    with books.read() as c:
        assert _state(c, card_id) == ("active", "active", 10_000, 10_000, 10_000)
        assert c.execute("SELECT sync_status FROM fin_cards WHERE id=?", (card_id,)).fetchone()[0] == "ok"


def test_a_cut_off_create_keeps_its_reservation_through_a_cancel_until_a_person_confirms(books):
    """N2, round 3 #1: a creation cut off mid-call is uncertain; a cancel keeps the reservation. Mercury's whole card
    list (every page) only gives candidates: no match, a same-nickname card or an empty list decides nothing. A
    person confirms the Mercury card (read from Mercury, adopted, cancelled there) or that there is none (freed)."""
    fake = FakeMercury()
    connect(books, fake)
    budget_id, card_id = _card(books)
    with books.transaction() as c:
        other_id = str(act(c, OWNER, "card_create", {"holder": "human:fay", "budget_id": budget_id, "amount": 5_000,
                                                     "provider_user_id": "user-fay"}).result["card_id"])
        for oid, in c.execute("SELECT id FROM fin_outbox WHERE op='card_create'").fetchall():
            assert SK.outbox_claim(c, str(oid), AT)                      # both at Mercury when the worker died
    fake.queue["cards"] = [Answer(500, {}), Answer(500, {})]             # Mercury's list is down at first
    sync_once(books, fake, HOURS[0])                                       # the cut-off is found
    with books.transaction() as c:
        assert c.execute("SELECT provider_status FROM fin_cards WHERE id=?", (card_id,)).fetchone()[0] == "uncertain"
        assert act(c, OWNER, "card_cancel", {"card_id": card_id}).status == "committed"
        assert act(c, OWNER, "card_cancel", {"card_id": other_id}).status == "committed"
        assert _state(c, card_id)[4] == 20_000 and _state(c, other_id)[4] == 5_000      # nothing freed yet
        name = str(SK.card_create_payload(c, card_id)["nickname"])
    sync_once(books, fake, HOURS[1])                                       # the list fails: nothing changes
    with books.read() as c:
        assert _state(c, card_id)[1:] == ("uncertain", 20_000, None, 20_000)
    # Eve's list runs over two pages: an older card first, then one with the nickname made that day.
    fake.card_page = 1
    fake.cards_list += [{"id": "card-old", "userId": "user-eve", "nickname": "old", "lastFour": "1111",
                         "status": "active", "createdAt": "2026-01-01T00:00:00Z"},
                        {"id": "card-found", "userId": "user-eve", "nickname": name, "lastFour": "4242",
                         "status": "active", "createdAt": AT, "spendLimit": {"amountCents": 20_000}}]
    sync_once(books, fake, HOURS[2])                                       # fay's list is empty
    assert [arg for arg in fake.sent("cards") if arg[0] == "user-eve"][-2:] == [("user-eve", None),
                                                                                ("user-eve", "card-old")]
    with books.transaction() as c:
        for k in (card_id, other_id):                                      # neither decided by a list
            assert _state(c, k)[1] == "uncertain" and _state(c, k)[4] in (20_000, 5_000)
        error = c.execute("SELECT sync_error FROM fin_cards WHERE id=?", (card_id,)).fetchone()[0]
        assert "card-found" in error and "card-old" not in error
        assert c.execute("SELECT provider_card_id FROM fin_cards WHERE id=?", (card_id,)).fetchone()[0] is None
        asks = [r[0] for r in c.execute("SELECT title FROM tasks WHERE title LIKE 'Confirm a Mercury card%'")]
        assert len(asks) == 2, asks
        assert act(c, OWNER, "card_resolve", {"card_id": card_id}).code == "invalid"
        assert act(c, OWNER, "card_resolve", {"card_id": card_id, "provider_card_id": "card-found"}
                   ).status == "committed"
        assert act(c, OWNER, "card_resolve", {"card_id": other_id, "none": True}).status == "committed"
        assert _state(c, other_id)[1] == "cancelled" and _state(c, other_id)[4] == 0     # never issued: freed
        assert act(c, OWNER, "card_resolve", {"card_id": other_id, "none": True}).code == "conflict"
    sync_once(books, fake, HOURS[3])                                       # read from Mercury, adopted, cancelled
    sync_once(books, fake, HOURS[4])
    assert fake.sent("card") == ["card-found"]
    assert fake.sent("card_state") == [("card-found", "cancel")]
    assert fake.sent("create_card") == []
    with books.read() as c:
        assert _state(c, card_id) == ("cancelled", "cancelled", 20_000, 20_000, 0)
        assert c.execute("SELECT provider_card_id, last4 FROM fin_cards WHERE id=?", (card_id,)).fetchone()[:] == \
            ("card-found", "4242")


def test_a_person_can_not_adopt_another_users_card_and_none_on_an_active_card_issues_it_again(books):
    fake = FakeMercury()
    connect(books, fake)
    budget_id, card_id = _card(books)
    with books.transaction() as c:
        for oid, in c.execute("SELECT id FROM fin_outbox WHERE op='card_create'").fetchall():
            assert SK.outbox_claim(c, str(oid), AT)
    sync_once(books, fake, HOURS[0])
    fake.cards_list.append({"id": "card-fay", "userId": "user-fay", "nickname": "x", "lastFour": "9999",
                            "status": "active", "createdAt": AT})
    with books.transaction() as c:
        assert act(c, OWNER, "card_resolve", {"card_id": card_id, "provider_card_id": "card-fay"}).status == \
            "committed"
    sync_once(books, fake, HOURS[1])
    with books.transaction() as c:
        row = c.execute("SELECT provider_status, provider_card_id, sync_error FROM fin_cards WHERE id=?",
                        (card_id,)).fetchone()
        assert (row[0], row[1]) == ("uncertain", None) and "another user" in row[2]
        # Round 4 #2: a bot with the cards right can not settle it alone; a finance admin decides.
        act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.cards"})
        act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "everything"})
        queued = c.execute("SELECT count(*) FROM fin_outbox").fetchone()[0]
        for ask in ({"none": True}, {"provider_card_id": "card-fay"}):
            bot = act(c, "bot:coder", "card_resolve", {"card_id": card_id, **ask})
            assert (bot.status, bot.rule) == ("needs_approval", "default:bot-card-resolve"), bot
        assert c.execute("SELECT count(*) FROM fin_outbox").fetchone()[0] == queued          # nothing sent
        assert act(c, OWNER, "card_resolve", {"card_id": card_id, "none": True}).status == "committed"
        assert _state(c, card_id)[4] == 20_000                                  # still reserved: issued again
    sync_once(books, fake, HOURS[2])
    assert len(fake.sent("create_card")) == 1
    with books.read() as c:
        assert _state(c, card_id)[:2] == ("active", "active")


def test_an_unowned_event_no_connection_can_read_yet_is_retried_for_a_day(books):
    """G8: an event that came with no connection, which every connection reads as 404, is kept and retried (it may
    not be visible yet) until a day after it arrived; only then is it nobody's."""
    fake = FakeMercury()
    connect(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        first = c.execute("SELECT * FROM fin_connections").fetchone()
        SK.insert(c, "fin_connections", {"provider": "mercury", "purpose": "bank", "status": "active",
                                         "name": "Mercury 2", "credential_ref": first["credential_ref"],
                                         "created_at": AT, "created_by": OWNER})
        for event, txn, at in (("evt-soon", "txn-dep", AT), ("evt-never", "txn-none", "2026-10-14T10:00:00.000000Z")):
            SK.inbox_add(c, connection_id=None, provider="mercury", event_id=event,
                         payload={"resourceType": "transaction", "resourceId": txn, "resourceVersion": 1}, at=at)
    sync_once(books, fake, HOURS[0])                    # both connections: 404
    with books.read() as c:
        status = dict(c.execute("SELECT provider_event_id, status FROM fin_inbox WHERE provider_event_id LIKE 'evt-%'"))
        assert status == {"evt-soon": "new", "evt-never": "ignored"}        # a day old: given up
    fake.txns["txn-dep"] = dict(MERCURY["txn_deposit"])                    # now Mercury shows it
    sync_once(books, fake, HOURS[2])
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_inbox WHERE provider_event_id='evt-soon'").fetchone()[0] == "done"
        assert c.execute("SELECT count(*) FROM fin_feed_txns WHERE provider_id='txn-dep'").fetchone()[0] == 1


def test_the_bank_worker_calls_the_base_it_is_pointed_at(store, monkeypatch):
    """TICO_MERCURY_BASE sends the worker's calls to a sandbox or a local fake; unset, they go to Mercury's own API."""
    from backend.finance.mercury import BASE
    from backend.finance.store import tico
    monkeypatch.setattr(tico, "credential_secret", lambda c, vault, ref: "token-for-test")
    monkeypatch.setenv("TICO_MERCURY_BASE", "http://127.0.0.1:18790/api/v1/")
    assert sync._provider(store, object(), "cred-mercury", None)._base == "http://127.0.0.1:18790/api/v1"
    monkeypatch.delenv("TICO_MERCURY_BASE")
    assert sync._provider(store, object(), "cred-mercury", None)._base == BASE


def test_a_stray_base_naming_another_host_is_ignored_with_one_warning(monkeypatch, caplog):
    """TICO_MERCURY_BASE only counts for a local or sandbox host. Any other host is ignored: the real token must never
    be sent elsewhere. The warning is logged once per process."""
    import logging
    from backend.finance import mercury
    monkeypatch.setattr(mercury, "_warned", [False])
    caplog.set_level(logging.WARNING, logger="backend.finance.mercury")
    monkeypatch.setenv("TICO_MERCURY_BASE", "https://evil.example.com/api/v1")
    assert mercury.base_url() == mercury.BASE
    assert mercury.base_url() == mercury.BASE
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    monkeypatch.setenv("TICO_MERCURY_BASE", "https://api-sandbox.mercury.com/api/v1")
    assert mercury.base_url() == "https://api-sandbox.mercury.com/api/v1"
def test_a_card_change_reaches_mercury_on_commit_without_waiting_for_a_sweep_or_sync_now(books):
    """A card freeze commits and the bank worker is rung after the commit, so Mercury gets the call with no manual
    sync (the worker's sweep is minutes away). The card shows pending until Mercury confirms it."""
    fake = FakeMercury()
    connect(books, fake)
    _, card_id = _card(books)
    sync_once(books, fake, HOURS[0])                                  # the card is issued at Mercury
    assert fake.sent("create_card") == [("user-eve", 20_000)]
    sync.start(books, Vault(books), make=lambda _token: fake)         # the worker runs and waits on its doorbell
    try:
        time.sleep(0.5)
        rung = sync.BELL.rung
        with books.transaction() as c:
            assert act(c, OWNER, "card_freeze", {"card_id": card_id}).status == "committed"
            assert c.execute("SELECT sync_status FROM fin_cards WHERE id=?", (card_id,)).fetchone()[0] == "pending"
            assert sync.BELL.rung == rung                             # not before the commit
        assert sync.BELL.rung > rung                                  # rung by the commit, not left to the sweep
        deadline = time.monotonic() + 15
        confirmed = None
        while time.monotonic() < deadline and confirmed is None:
            time.sleep(0.05)
            with books.read() as c:
                row = c.execute("SELECT sync_status, provider_status FROM fin_cards WHERE id=?", (card_id,)).fetchone()
                confirmed = tuple(row) if row[0] == "ok" else None
        assert fake.sent("card_state") == [("card-eve", "freeze")]
        assert confirmed == ("ok", "frozen")
    finally:
        sync.stop()


def test_an_undone_card_change_does_not_ring_the_bank_worker(books, monkeypatch):
    """A card action that fails after queueing its ring is undone to its savepoint, ring included, even though the
    transaction still commits (the failure's receipt)."""
    fake = FakeMercury()
    connect(books, fake)
    _, card_id = _card(books)
    sync_once(books, fake, HOURS[0])
    import backend.finance.actions as A
    real = A._audit

    def fail_on_commit(c, p, action_id, kind, status, *a, **k):
        if status == "committed":
            raise A.tico.Refused("audit write failed")
        return real(c, p, action_id, kind, status, *a, **k)
    monkeypatch.setattr(A, "_audit", fail_on_commit)
    rung = sync.BELL.rung
    with books.transaction() as c:
        assert act(c, OWNER, "card_freeze", {"card_id": card_id}).status == "failed"
        assert c.execute("SELECT status FROM fin_cards WHERE id=?", (card_id,)).fetchone()[0] == "active"
    assert sync.BELL.rung == rung
