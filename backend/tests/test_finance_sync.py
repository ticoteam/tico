"""The bank worker and card desired state, against a fake Mercury (review findings A, D, E, F, M, N, R): writes are
claimed before their call and a cut-off create is never sent blindly again; a card's provider writes follow its latest
desired state; a lower limit or a cancel keeps the reservation until Mercury confirms; ingestion is atomic per
transaction and retried; inbox events belong to their connection; older snapshots never win."""
from backend.finance import banking
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


def test_a_cut_off_create_keeps_its_reservation_through_a_cancel_until_mercury_says(books):
    """N2: a creation cut off mid-call is uncertain; a cancel then keeps the reservation; Mercury's card list decides:
    a matching card is adopted and cancelled there, none frees the reservation."""
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
    sync_once(books, fake, HOURS[2])                                       # the list fails: still uncertain
    with books.read() as c:
        assert _state(c, card_id)[4] == 20_000
    with books.read() as c:
        name = str(SK.card_create_payload(c, card_id)["nickname"])
    fake.cards_list.append({"id": "card-found", "userId": "user-eve", "nickname": name, "lastFour": "4242",
                            "status": "active", "createdAt": AT, "spendLimit": {"amountCents": 20_000}})
    sync_once(books, fake, HOURS[4])                                       # fay has no card at Mercury
    sync_once(books, fake, HOURS[5])
    assert fake.sent("card_state") == [("card-found", "cancel")]
    assert fake.sent("create_card") == []
    with books.read() as c:
        assert _state(c, card_id) == ("cancelled", "cancelled", 20_000, 20_000, 0)
        assert c.execute("SELECT provider_card_id FROM fin_cards WHERE id=?", (card_id,)).fetchone()[0] == "card-found"
        assert _state(c, other_id)[1] == "cancelled" and _state(c, other_id)[4] == 0


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
