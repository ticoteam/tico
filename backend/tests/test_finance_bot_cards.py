"""Bot cards (spec §8, phase 3) against a fake Mercury: a bot's card is standing and its limit is its budget's; a bot
sees and acts on its own cards only; a decline budget's cutoff freezes them and a purchase over the budget is not held;
a card is saved on vendor accounts by a person, replay-safe, and a bot reads where its card works; a single-use card
waits for a provider that has them; and no card number reaches a payload, a receipt, a read or a tool."""
import random
import re
import threading
import uuid

import pytest

from backend.finance import actions as FA
from backend.finance import budgeting
from backend.finance import reads as FR
from backend.finance.context import load
from backend.finance.core import validate as V
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, account, act, books, connect, finance_template, spend, store, sync_once)
from clients import hubtools
from clients.tico import APIError

LATER = "2026-10-15T13:30:00.000000Z"
NEXT = "2026-11-03T09:00:00.000000Z"
CARD_NUMBER = re.compile(r"(?<!\d)\d{13,19}(?!\d)")


def _budget(c, name="Tools", amount=50_000, at_limit="decline", **scope):
    payload = {"name": name, "account_id": account(c, "6100"), "amount": amount, "at_limit": at_limit, **scope}
    made = act(c, OWNER, "budget_create", payload)
    assert made.status == "committed", (made.code, made.reason)
    return str(made.result["budget_id"])


def _bot_card(c, budget, bot, amount=20_000, user="user-cara"):
    return act(c, OWNER, "card_create", {"holder": bot, "budget_id": budget, "amount": amount,
                                         "provider_user_id": user})


def _world(books, fake):
    """Acme's Mercury connected; a 50.00 budget with a 20.00 standing card for each bot, both synced to Mercury (the
    fake answers card-coder and card-seller); both bots granted finance.cards."""
    connect(books, fake)
    fake.queue["create_card"] = [{**MERCURY["card_created"], "cardId": "card-coder", "lastFourDigits": "1111"},
                                 {**MERCURY["card_created"], "cardId": "card-seller", "lastFourDigits": "2222"}]
    with books.transaction() as c:
        budget = _budget(c)
        coder = str(_bot_card(c, budget, "bot:coder").result["card_id"])
    sync_once(books, fake)
    with books.transaction() as c:
        seller = str(_bot_card(c, budget, "bot:seller", user="user-dan").result["card_id"])
    sync_once(books, fake)
    with books.transaction() as c:
        for bot in ("bot:coder", "bot:seller"):
            assert act(c, OWNER, "right_grant", {"teammate": bot, "right": "finance.cards"}).status == "committed"
    return budget, coder, seller


def _status(c, card_id):
    return c.execute("SELECT status FROM fin_cards WHERE id=?", (card_id,)).fetchone()[0]


def _ids(found):
    return [str(r["id"]) for r in found["cards"]]


def _walk(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _walk(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk(v)
    else:
        yield value


def test_a_bot_card_is_standing_and_its_limit_follows_its_budget(books):
    fake = FakeMercury()
    budget, coder, _ = _world(books, fake)
    assert fake.sent("create_card") == [("user-cara", 20_000), ("user-dan", 20_000)]
    with books.transaction() as c:
        row = c.execute("SELECT holder, kind, provider_card_id, provider_limit FROM fin_cards WHERE id=?",
                        (coder,)).fetchone()
        assert tuple(row) == ("bot:coder", "standing", "card-coder", 20_000)
        # The budget's 50.00 holds both bots' cards (20.00 each): 10.00 of room until the budget grows.
        assert act(c, OWNER, "card_update_limit", {"card_id": coder, "amount": 25_000}).status == "committed"
        assert act(c, OWNER, "card_update_limit", {"card_id": coder, "amount": 35_000}).code == "invalid"
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 70_000}).status == "committed"
        assert act(c, OWNER, "card_update_limit", {"card_id": coder, "amount": 50_000}).status == "committed"
    sync_once(books, fake, LATER)
    assert fake.sent("card_limit") == [("card-coder", 50_000)]     # the two changes before the send, sent as one
    with books.read() as c:
        assert c.execute("SELECT provider_limit FROM fin_cards WHERE id=?", (coder,)).fetchone()[0] == 50_000


def test_a_bot_card_waits_for_a_finance_admin_and_a_teammate_cannot_issue_one_alone(books):
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        line = _budget(c)
        budget = _budget(c, name="Eng tools", amount=20_000, team="eng", parent_id=line)
        assert act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.cards"}).status == "committed"
        asked = act(c, "human:ben", "card_create", {"holder": "bot:coder", "budget_id": budget, "amount": 5_000,
                                                    "provider_user_id": "user-cara"})
        assert (asked.status, asked.rule) == ("needs_approval", "rule:bot-card-standing")
        assert asked.approver == OWNER
        assert c.execute("SELECT count(*) FROM fin_cards").fetchone()[0] == 0


def test_a_bot_sees_and_acts_only_on_its_own_cards(books):
    fake = FakeMercury()
    _, coder, seller = _world(books, fake)
    with books.transaction() as c:
        assert _ids(FR.cards(c, "bot:coder")) == [coder]
        assert _ids(FR.cards(c, "bot:seller")) == [seller]
        assert [str(r["id"]) for r in FR.search(c, "bot:coder", kind="card")["results"]] == [coder]
        for kind, payload in (("card_freeze", {"card_id": seller}), ("card_unfreeze", {"card_id": seller}),
                              ("card_update_limit", {"card_id": seller, "amount": 10_000}),
                              ("card_save", {"card_id": seller, "vendor": "Acme Cloud"})):
            refused = act(c, "bot:coder", kind, payload)
            assert refused.code == "not_found", kind
        assert act(c, "bot:coder", "card_freeze", {"card_id": coder}).status == "committed"
        assert _status(c, coder) == "frozen"


def _frozen_by(c, card_id):
    return c.execute("SELECT freeze_by FROM fin_cards WHERE id=?", (card_id,)).fetchone()[0]


def test_a_cutoff_freeze_lifts_when_a_raise_leaves_room_and_a_raise_short_of_it_does_not(books):
    fake = FakeMercury()
    budget, coder, seller = _world(books, fake)
    with books.transaction() as c:
        spend(c, "6100", 50_000)
        FA.drain_events(c, AT)
        assert _status(c, coder) == "frozen" and _frozen_by(c, coder) == "budget"
        assert act(c, OWNER, "card_unfreeze", {"card_id": coder}).code == "denied"
    sync_once(books, fake, LATER)
    assert sorted(fake.sent("card_state")) == [("card-coder", "freeze"), ("card-seller", "freeze")]
    with books.transaction() as c:
        # 60.00 still leaves the 50.00 spent and the 40.00 reserved over it: the freezes stay.
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 60_000}).status == "committed"
        assert _status(c, coder) == "frozen" and _status(c, seller) == "frozen"
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 100_000}).status == "committed"
        assert _status(c, coder) == "active" and _status(c, seller) == "active"
        assert _frozen_by(c, coder) is None
        assert act(c, OWNER, "card_unfreeze", {"card_id": coder}).code == "conflict"
    sync_once(books, fake, LATER)
    sent = fake.sent("card_state")
    assert ("card-coder", "unfreeze") in sent and ("card-seller", "unfreeze") in sent


def test_a_cutoff_freeze_lifts_at_a_new_period_and_a_person_freeze_stays(books):
    fake = FakeMercury()
    _, coder, seller = _world(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "card_freeze", {"card_id": seller}).status == "committed"
        spend(c, "6100", 50_000)
        FA.drain_events(c, AT)
        assert _frozen_by(c, coder) == "budget" and _frozen_by(c, seller) == "person"
    sync_once(books, fake, LATER)
    with books.transaction() as c:
        assert FA.watch_documents(c, NEXT) >= 1        # November: the October spend is no longer in the period
        assert _status(c, coder) == "active" and _status(c, seller) == "frozen"
        assert _frozen_by(c, seller) == "person"
        row = next(r for r in FR.cards(c, OWNER)["cards"] if str(r["id"]) == seller)
        assert row["frozen_by"] == "person"
    sync_once(books, fake, NEXT)
    sent = fake.sent("card_state")
    assert ("card-coder", "unfreeze") in sent and ("card-seller", "unfreeze") not in sent


def test_a_person_who_freezes_a_budget_frozen_card_keeps_it_frozen_when_the_budget_has_room(books):
    fake = FakeMercury()
    budget, coder, _ = _world(books, fake)
    with books.transaction() as c:
        spend(c, "6100", 50_000)
        FA.drain_events(c, AT)
        assert _frozen_by(c, coder) == "budget"
        assert act(c, OWNER, "card_freeze", {"card_id": coder}).status == "committed"     # a person claims it
        assert _frozen_by(c, coder) == "person"
    sync_once(books, fake, LATER)
    with books.transaction() as c:
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 100_000}).status == "committed"
        assert _status(c, coder) == "frozen" and _frozen_by(c, coder) == "person"
        assert act(c, OWNER, "card_unfreeze", {"card_id": coder}).status == "committed"
    sync_once(books, fake, NEXT)
    assert fake.sent("card_state").count(("card-coder", "unfreeze")) == 1


def test_a_budget_cannot_go_below_its_cards_and_child_budgets(books):
    fake = FakeMercury()
    budget, coder, seller = _world(books, fake)
    with books.transaction() as c:
        assert _budget(c, name="Eng tools", amount=10_000, team="eng", parent_id=budget)
        refused = act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 45_000})
        assert refused.code == "invalid"
        assert "lower these cards first" in refused.reason and "coder" in refused.reason
        assert "seller" in refused.reason and "reserves 200.00" in refused.reason
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 49_000}).code == "invalid"
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 50_000}).status == "committed"


def test_a_pause_freezes_the_cards_keeping_allocations_and_a_raise_that_leaves_room_lifts_them(books):
    fake = FakeMercury()
    budget, coder, seller = _world(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "card_freeze", {"card_id": coder}).status == "committed"     # a person froze coder
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 0}).status == "committed"
        assert _status(c, seller) == "frozen" and _frozen_by(c, seller) == "budget"
        assert _frozen_by(c, coder) == "person"
        allocated = c.execute("SELECT amount FROM fin_allocations WHERE budget_id=? AND target_kind='card' "
                              "ORDER BY target_id", (budget,)).fetchall()
        assert [int(r[0]) for r in allocated] == [20_000, 20_000]
    sync_once(books, fake, LATER)
    assert sorted(fake.sent("card_state")) == [("card-coder", "freeze"), ("card-seller", "freeze")]
    with books.transaction() as c:
        # Zero leaves no room: the reserves are over it, so raising to exactly them does not lift the seller's card.
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 40_000}).status == "committed"
        assert _status(c, seller) == "frozen"
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 100_000}).status == "committed"
        assert _status(c, seller) == "active" and _frozen_by(c, seller) is None
        assert _status(c, coder) == "frozen" and _frozen_by(c, coder) == "person"
    sync_once(books, fake, LATER)
    sent = fake.sent("card_state")
    assert ("card-seller", "unfreeze") in sent and ("card-coder", "unfreeze") not in sent


def test_a_pause_takes_effect_now_and_a_zero_raise_is_not_a_pause_of_later_days(books):
    fake = FakeMercury()
    budget, coder, _ = _world(books, fake)
    with books.transaction() as c:
        later = act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 0, "effective_from": "2026-11-01"})
        assert later.code == "invalid" and "takes effect now" in later.reason
        assert _status(c, coder) == "active"
        # Lowering to a non-zero amount below the reserves is still refused: only a pause may go to zero.
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 39_000}).code == "invalid"


def test_a_card_that_used_more_than_its_allocation_reserves_what_it_used(books):
    fake = FakeMercury()
    budget, coder, _ = _world(books, fake)
    with books.transaction() as c:
        ctx = load(c, AT)
        held = budgeting.place(ctx, budget_scope=budget, amount=25_000, currency=ctx.book.base_currency, source="card",
                               source_id="auth-big", expires_at=None, target=f"card:{coder}")
        assert held.allowed
        # Coder reserves its 25.00 used (not its 20.00 allocation) and seller 20.00: 45.00 in all.
        refused = act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 44_000})
        assert refused.code == "invalid" and "coder" in refused.reason and "reserves 250.00" in refused.reason
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 45_000}).status == "committed"


def test_a_bot_card_purchase_over_a_decline_budget_is_declined_and_one_within_it_is_held(books):
    fake = FakeMercury()
    _, coder, _ = _world(books, fake)
    with books.transaction() as c:
        spend(c, "6100", 5_000)
        ctx = load(c, AT)
        budget = next(b.id for b in ctx.budgets().values() if b.name == "Tools")
        over = budgeting.place(ctx, budget_scope=budget, amount=30_000, currency=ctx.book.base_currency, source="card",
                               source_id="auth-1", expires_at=None, target=f"card:{coder}")
        assert (over.allowed, over.action) == (False, "decline")
        assert c.execute("SELECT count(*) FROM fin_holds WHERE source='card'").fetchone()[0] == 0
        within = budgeting.place(ctx, budget_scope=budget, amount=10_000, currency=ctx.book.base_currency,
                                 source="card", source_id="auth-2", expires_at=None, target=f"card:{coder}")
        assert within.allowed and within.hold_id
        assert c.execute("SELECT count(*) FROM fin_holds WHERE source='card'").fetchone()[0] == 1


def test_single_use_waits_for_a_provider_that_has_them_and_each_kind_fits_its_holder(books):
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        budget = _budget(c)
        single = act(c, OWNER, "card_create", {"holder": "bot:coder", "budget_id": budget, "amount": 5_000,
                                               "kind": "single_use", "provider_user_id": "user-cara"})
        assert (single.status, single.code) == ("denied", "not_available")
        for holder, kind in (("bot:coder", "person"), ("human:eve", "standing"), ("bot:coder", "gift")):
            assert act(c, OWNER, "card_create", {"holder": holder, "budget_id": budget, "amount": 5_000, "kind": kind,
                                                 "provider_user_id": "u"}).code == "invalid", (holder, kind)
        assert act(c, OWNER, "card_create", {"holder": "bot:coder", "budget_id": budget, "amount": 5_000,
                                             "merchant": "acme.example", "provider_user_id": "u"}).code == "invalid"
        dry = act(c, OWNER, "card_create", {"holder": "bot:coder", "budget_id": budget, "amount": 5_000,
                                            "kind": "standing", "provider_user_id": "u"}, dry_run=True)
        assert dry.rule == "rule:bot-card-standing"
        assert act(c, OWNER, "card_create", {"holder": "human:eve", "budget_id": budget, "amount": 5_000,
                                             "provider_user_id": "u"}).status == "committed"


def _race(store, n, work):
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


def test_a_saved_card_record_is_replay_safe_and_a_bot_reads_where_its_card_works(books):
    fake = FakeMercury()
    _, coder, seller = _world(books, fake)
    with books.transaction() as c:
        saved = act(c, OWNER, "card_save", {"card_id": coder, "vendor": " Acme Cloud "}, key="save-coder")
        assert (saved.status, saved.result["vendor"], saved.result["vendor_label"]) == ("committed", "acme cloud",
                                                                                         "Acme Cloud")
        replay = act(c, OWNER, "card_save", {"card_id": coder, "vendor": " Acme Cloud "}, key="save-coder")
        assert (replay.status, replay.replayed, replay.action_id) == ("committed", True, saved.action_id)
        again = act(c, OWNER, "card_save", {"card_id": coder, "vendor": "acme cloud"}, key="save-again")
        assert again.code == "conflict"
        assert c.execute("SELECT count(*) FROM fin_card_saves").fetchone()[0] == 1
        found = FR.search(c, "bot:coder", kind="card", filters={"vendor": "ACME CLOUD"})["results"]
        assert [str(r["id"]) for r in found] == [coder]
        saved_on = found[0]["saved_on"]
        assert [(s["vendor"], s["label"], s["saved_by"]) for s in saved_on] == [("acme cloud", "Acme Cloud", OWNER)]
        assert FR.search(c, "bot:coder", kind="card", filters={"vendor": "Other Co"})["results"] == []
        assert FR.search(c, "bot:seller", kind="card", filters={"vendor": "Acme Cloud"})["results"] == []
        assert act(c, OWNER, "card_unsave", {"card_id": coder, "vendor": "Acme Cloud"}, key="unsave").status == \
            "committed"
        assert FR.search(c, "bot:coder", kind="card", filters={"vendor": "Acme Cloud"})["results"] == []
        again_off = act(c, OWNER, "card_unsave", {"card_id": coder, "vendor": "Acme Cloud"}, key="unsave-2")
        assert again_off.code == "conflict"
        assert act(c, OWNER, "card_save", {"card_id": coder, "vendor": "Acme Cloud"}, key="save-back").status == \
            "committed"
        assert tuple(c.execute("SELECT count(*), sum(active) FROM fin_card_saves").fetchone()) == (1, 1)
        asked = act(c, "bot:coder", "card_save", {"card_id": coder, "vendor": "Partner Shop"}, key="bot-save")
        assert (asked.status, asked.rule) == ("needs_approval", "default:bot-card-vendor")


def test_saving_a_card_at_a_vendor_twice_at_once_records_it_once(books):
    fake = FakeMercury()
    _, coder, _ = _world(books, fake)
    done = _race(books, 2, lambda c, i: act(c, OWNER, "card_save", {"card_id": coder, "vendor": "Acme Cloud"},
                                            key=f"race-{i}"))
    assert sorted(r.status for r in done) == ["committed", "failed"]
    with books.read() as c:
        assert c.execute("SELECT count(*) FROM fin_card_saves").fetchone()[0] == 1


def test_a_card_saved_on_many_vendors_shows_ten_and_the_filter_still_finds_the_rest(books):
    fake = FakeMercury()
    _, coder, _ = _world(books, fake)
    with books.transaction() as c:
        for i in range(11):
            assert act(c, OWNER, "card_save", {"card_id": coder, "vendor": f"Vendor {i:02d}"}, key=f"many-{i}").status == \
                "committed"
        found = FR.search(c, "bot:coder", kind="card", filters={"vendor": "vendor 10"})["results"]
        assert [str(r["id"]) for r in found] == [coder]
        assert len(found[0]["saved_on"]) == 10


def test_a_card_saved_on_a_vendor_contact_reads_under_its_id(books):
    fake = FakeMercury()
    _, coder, _ = _world(books, fake)
    with books.transaction() as c:
        contact = str(act(c, OWNER, "contact_create", {"name": "Acme Cloud", "kind": "vendor"}).result["contact_id"])
        saved = act(c, OWNER, "card_save", {"card_id": coder, "vendor": contact}, key="save-contact")
        assert saved.status == "committed"
        found = FR.search(c, "bot:coder", kind="card", filters={"vendor": contact})["results"]
        assert [str(r["id"]) for r in found] == [coder]
        assert found[0]["saved_on"][0]["label"] == "Acme Cloud" and found[0]["saved_on"][0]["vendor"] == contact


def test_no_card_number_is_taken_stored_or_read_back(books):
    fake = FakeMercury()
    budget, coder, _ = _world(books, fake)
    typed = "4242 4242 4242 4242"
    with books.transaction() as c:
        for kind, payload in (("card_create", {"holder": "bot:coder", "budget_id": budget, "amount": 5_000,
                                               "provider_user_id": "u", "card_number": typed}),
                              ("card_save", {"card_id": coder, "vendor": "Acme", "note": typed}),
                              ("card_update_limit", {"card_id": coder, "amount": 9_000, "cvc": "123"})):
            refused = act(c, OWNER, kind, payload, key=f"typed-{kind}")
            assert (refused.status, refused.code) == ("failed", "invalid"), kind
        assert FA.receipt_payload(c, "card_save", {"card_id": coder, "vendor": typed}) == \
            {"refused": FA.CARD_NUMBER_REFUSED}
        stored = " ".join(str(v) for table in ("fin_actions", "fin_cards", "fin_card_saves")
                          for row in c.execute(f"SELECT * FROM {table}").fetchall() for v in tuple(row))
        assert typed.replace(" ", "") not in stored.replace(" ", "")
        assert not CARD_NUMBER.search(stored.replace(" ", ""))
        outputs = [FR.cards(c, "bot:coder"), FR.cards(c, OWNER), FR.search(c, "bot:coder", kind=None),
                   FR.search(c, OWNER, kind="card"), FA.help()]
    for output in outputs:
        for item in _walk(output):
            assert str(item).replace(" ", "") != typed.replace(" ", ""), item
            assert not CARD_NUMBER.search(str(item).replace(" ", "")), item
        assert not {k for k in _walk(output) if isinstance(k, str) and k.lower() in {
            "number", "card_number", "pan", "cvc", "cvv", "security_code"}}
    for tool in ("hub_finance_card_fill", "hub_finance_search"):
        fields = set(hubtools.BY_NAME[tool]["inputSchema"]["properties"])
        assert not {"number", "card_number", "pan", "cvc", "cvv"} & fields
    with pytest.raises(APIError) as refused:
        hubtools.BY_NAME["hub_finance_card_fill"]["fn"](None, {"card_id": coder, "merchant": "shop.example",
                                                              "amount": 5_000})
    assert refused.value.code == "not_available"


def test_an_id_is_never_read_as_a_card_number():
    """Ids are random hex: a Luhn-valid run of digits inside one must not refuse the action that carries it."""
    assert not V.has_card_number({"budget_id": "ab4222222222222cd"})
    rnd = random.Random(7)
    ids = [uuid.UUID(int=rnd.getrandbits(128)) for _ in range(20_000)]
    assert not any(V.has_card_number({"card_id": str(i), "budget_id": i.hex}) for i in ids)


def test_migration_67_adds_the_saved_card_record_to_an_install_from_66(store):
    with store.transaction() as c:
        c.execute("DELETE FROM cloud_migrations WHERE version=67")
        c.execute("DROP TABLE fin_card_saves")
    store.initialize(seed_market=False)
    with store.read() as c:
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='fin_card_saves'").fetchone()
        assert c.execute("SELECT count(*) FROM cloud_migrations WHERE version=67").fetchone()[0] == 1
