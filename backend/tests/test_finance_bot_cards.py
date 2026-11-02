"""Bot cards (spec §8, phase 3) against a fake Mercury: a bot's card is standing and its limit is its budget's; a bot
sees and acts on its own cards only; a decline budget's cutoff freezes them and a purchase over the budget is not held;
a card is saved on vendor accounts by a person, replay-safe, and a bot reads where its card works; a single-use card
waits for a provider that has them; and no card number reaches a payload, a receipt, a read or a tool."""
import json
import random
import re
import threading
import uuid

import pytest

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import budgeting
from backend.finance import reads as FR
from backend.finance.context import load
from backend.finance.core import validate as V
from backend.finance.core.org import SYSTEM
from backend.finance.store import budgets as SB
from backend.finance.store import schema as FS
from backend.tests.test_api import api, headers  # noqa: F401
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, account, act, books, connect, finance_template, set_manager, spend, store,
    sync_once)
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


def test_the_hourly_look_lifts_a_cutoff_freeze_as_a_logged_system_action(books):
    fake = FakeMercury()
    _, coder, seller = _world(books, fake)
    with books.transaction() as c:
        spend(c, "6100", 50_000)
        FA.drain_events(c, AT)
        assert _frozen_by(c, coder) == "budget" and _frozen_by(c, seller) == "budget"
    sync_once(books, fake, LATER)
    with books.transaction() as c:
        assert FA.watch_documents(c, NEXT) >= 1
        assert _status(c, coder) == "active" and _status(c, seller) == "active"
        lifts = c.execute("SELECT actor, status, payload_json FROM fin_actions WHERE kind='card_unfreeze'").fetchall()
        assert sorted((actor, status) for actor, status, _ in lifts) == [(SYSTEM, "committed")] * 2
        assert {json.loads(payload)["card_id"] for *_, payload in lifts} == {coder, seller}
    sync_once(books, fake, NEXT)
    sent = fake.sent("card_state")
    assert ("card-coder", "unfreeze") in sent and ("card-seller", "unfreeze") in sent


def test_system_work_cannot_lift_a_person_freeze_through_the_action_path(books):
    fake = FakeMercury()
    _, coder, seller = _world(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "card_freeze", {"card_id": seller}).status == "committed"
        spend(c, "6100", 50_000)
        FA.drain_events(c, AT)
        refused = act(c, SYSTEM, "card_unfreeze", {"card_id": seller}, key="system-lift-person")
        assert (refused.status, refused.code) == ("failed", "conflict")
        assert _status(c, seller) == "frozen" and _frozen_by(c, seller) == "person"
        assert _frozen_by(c, coder) == "budget"


def test_the_card_help_says_the_mercury_user_may_be_left_out_because_tico_finds_one(books):
    """Mercury issues a card to one of its own users. A card names that user, or Tico finds it (the holder's email,
    then the connection's card user): provider_user_id is optional, and the next tests show each way it is found."""
    fields = {f["name"]: f["required"] for f in FA.help("card_create")["fields"]}
    assert fields["provider_user_id"] is False


def test_a_card_for_a_teammate_is_issued_to_the_mercury_user_with_the_same_email(books):
    fake = FakeMercury()
    connect(books, fake)
    fake.queue["create_card"] = [{**MERCURY["card_created"], "cardId": "card-ben", "lastFourDigits": "3333"}]
    with books.transaction() as c:
        made = act(c, OWNER, "card_create", {"holder": "human:ben", "budget_id": _budget(c), "amount": 20_000})
        assert made.status == "committed", (made.code, made.reason)
    sync_once(books, fake)
    assert fake.sent("create_card") == [("user-ben", 20_000)]


def test_a_card_with_no_named_or_found_mercury_user_takes_the_connections_card_user_or_is_refused(books):
    fake = FakeMercury()
    connect(books, fake)
    fake.queue["create_card"] = [{**MERCURY["card_created"], "cardId": "card-coder", "lastFourDigits": "1111"}]
    with books.transaction() as c:
        budget = _budget(c)
        refused = act(c, OWNER, "card_create", {"holder": "bot:coder", "budget_id": budget, "amount": 20_000})
        assert (refused.status, refused.code) == ("failed", "invalid")
        assert "Mercury user is needed" in refused.reason
        assert c.execute("SELECT count(*) FROM fin_cards").fetchone()[0] == 0
        connection = str(c.execute("SELECT id FROM fin_connections").fetchone()[0])
        assert act(c, OWNER, "connection_settings", {"connection_id": connection,
                                                     "card_user_id": "user-zed"}).code == "invalid"
        assert act(c, OWNER, "connection_settings", {"connection_id": connection,
                                                     "card_user_id": "user-eve"}).status == "committed"
        made = act(c, OWNER, "card_create", {"holder": "bot:coder", "budget_id": budget, "amount": 20_000})
        assert made.status == "committed", (made.code, made.reason)
    sync_once(books, fake)
    assert fake.sent("create_card") == [("user-eve", 20_000)]


def test_a_card_request_may_name_its_merchant_which_is_kept_on_the_card_and_not_sent_to_mercury(books):
    """Spec §8.1: Mercury has no merchant locks, so a card's merchant is the request's intent. It is kept on the card
    and the help lists it; it is not sent to Mercury, and the card's limit is what Mercury enforces."""
    assert {f["name"] for f in FA.help("card_create")["fields"]} >= {"merchant"}
    fake = FakeMercury()
    connect(books, fake)
    fake.queue["create_card"] = [{**MERCURY["card_created"], "cardId": "card-coder", "lastFourDigits": "1111"}]
    with books.transaction() as c:
        made = act(c, OWNER, "card_create", {"holder": "bot:coder", "budget_id": _budget(c), "amount": 20_000,
                                             "provider_user_id": "user-cara", "merchant": "Acme Cloud"})
        assert made.status == "committed", (made.code, made.reason)
        card_id = str(made.result["card_id"])
    sync_once(books, fake)
    assert fake.sent("create_card") == [("user-cara", 20_000)]
    with books.read() as c:
        assert c.execute("SELECT merchant FROM fin_cards WHERE id=?", (card_id,)).fetchone()[0] == "Acme Cloud"


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
        # Fields join on newlines: a space would glue one field's digits onto the next once spaces are removed below,
        # and a random id's digits could then read as a card number.
        stored = "\n".join(str(v) for table in ("fin_actions", "fin_cards", "fin_card_saves")
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


def _stored_digits(c):
    """Every digit the action log, the card rows and the saved-card records hold, as one string."""
    text = " ".join(str(v) for table in ("fin_actions", "fin_cards", "fin_card_saves")
                    for row in c.execute(f"SELECT * FROM {table}").fetchall() for v in tuple(row))
    return re.sub(r"\D", "", text)


def test_a_card_number_is_refused_however_its_digits_are_spaced_or_followed(books):
    fake = FakeMercury()
    _, coder, _ = _world(books, fake)
    for typed in ("4242  4242  4242  4242", "4242\t4242 4242-4242", "4242\u00a04242\u00a04242\u00a04242",
                  "4242 4242 4242 4242 123", "4242-4242-4242-4242"):
        assert V.has_card_number({"vendor": typed}), typed
        with books.transaction() as c:
            refused = act(c, OWNER, "card_save", {"card_id": coder, "vendor": typed})
            assert (refused.status, refused.code, refused.reason) == \
                ("failed", "invalid", FA.CARD_NUMBER_REFUSED), typed
    with books.read() as c:
        assert "4242424242424242" not in _stored_digits(c)
    # Ordinary text with digits in it, and a number that fails Luhn, stay as they are.
    assert not V.has_card_number("4242 4242 4242 4241")
    assert not V.has_card_number("order 2026-10-31 ref 0001")
    assert not V.has_card_number({"budget_id": "ab4222222222222cd", "card_id": "12345678-1234-1234-1234-123456789012"})


def test_a_cvc_typed_as_free_text_is_refused_and_never_stored(books):
    """§8.3, §13: a CVC is never stored, logged or sent to a model, whatever sentence it is typed into."""
    for typed in ("CVC 123", "cvc: 4567", "CVV2=123", "CVC123", "security code 987", "card CSC 321 on file",
                  "cvc is 123"):
        assert V.has_card_number({"memo": typed}), typed
    for text in ("invoice 123 for cvc work", "CVC check passed on 3 runs", "security code 123456",
                  "order 2026-10-31 ref 0001"):
        assert not V.has_card_number({"memo": text}), text
    with books.transaction() as c:
        budgets = c.execute("SELECT count(*) FROM fin_budgets").fetchone()[0]
        refused = act(c, OWNER, "budget_create", {"name": "CVC 123", "account_id": account(c, "6100"),
                                                  "amount": 10_000, "at_limit": "decline"})
        assert (refused.status, refused.code, refused.reason) == ("failed", "invalid", FA.CARD_NUMBER_REFUSED)
        assert c.execute("SELECT count(*) FROM fin_budgets").fetchone()[0] == budgets
        assert c.execute("SELECT count(*) FROM fin_actions WHERE payload_json LIKE '%CVC%'").fetchone()[0] == 0


DASHED_CARD_SHAPES =("4242-4242-4242-4242-123", "4242 4242 4242 4242-123", "4242-4242-4242-4242-7",
                      "4242-4242-4242-4242-", "-4242424242424242", "4242424242424242-", "-4242-4242-4242-4242",
                      "123-4242-4242-4242-4242")


def test_a_dashed_card_number_is_refused_with_a_code_or_a_dash_on_either_side(books):
    """A dash next to a card number is a separator, not an id, unless letters or digits run on past it. A code after
    or before the number (one to four digits, dashed off) is a group of its own."""
    for typed in DASHED_CARD_SHAPES:
        assert V.has_card_number(typed), typed
    # A UUID-shaped id is no card, even when its first sixteen digits pass Luhn: a group that goes on after them is
    # no code.
    assert not V.has_card_number("42424242-4242-4242-4242-123456789012")
    assert not V.has_card_number("ord-4242424242424242")
    with books.transaction() as c:
        before = c.execute("SELECT count(*) FROM fin_budgets").fetchone()[0]
        for typed in DASHED_CARD_SHAPES:
            refused = act(c, OWNER, "budget_create", {"name": typed, "account_id": account(c, "6100"),
                                                      "amount": 10_000, "at_limit": "decline"})
            assert refused.status == "failed" and refused.code == "invalid", typed
            assert refused.reason == FA.CARD_NUMBER_REFUSED, typed
            refused = act(c, OWNER, "contact_create", {"name": "Acme Shop", "kind": "vendor", "tax_id": typed})
            assert (refused.status, refused.code, refused.reason) == ("failed", "invalid", V.CARD_NUMBER_REFUSED), typed
        assert c.execute("SELECT count(*) FROM fin_budgets").fetchone()[0] == before
        assert c.execute("SELECT count(*) FROM fin_contacts").fetchone()[0] == 0
        stored = " ".join(str(v) for table in ("fin_budgets", "fin_actions", "fin_contacts")
                          for row in c.execute(f"SELECT * FROM {table}").fetchall() for v in tuple(row))
        assert "4242424242424242" not in re.sub(r"\D", "", stored)


def test_a_dashed_card_number_in_a_plain_field_is_refused_and_never_stored(books):
    fake = FakeMercury()
    _, coder, _ = _world(books, fake)
    with books.transaction() as c:
        for typed in DASHED_CARD_SHAPES:
            refused = act(c, OWNER, "card_save", {"card_id": coder, "vendor": typed})
            assert (refused.status, refused.code, refused.reason) == \
                ("failed", "invalid", FA.CARD_NUMBER_REFUSED), typed
        assert c.execute("SELECT count(*) FROM fin_card_saves").fetchone()[0] == 0
        assert "4242424242424242" not in _stored_digits(c)


def test_a_card_number_sent_as_a_json_number_is_refused_before_anything_is_stored(books):
    fake = FakeMercury()
    _, coder, _ = _world(books, fake)
    assert V.has_card_number({"vendor": 4242424242424242})
    assert V.has_card_number({"vendor": 4242424242424242.0})
    assert V.has_card_number({"vendor": [4242424242424242]})
    assert not V.has_card_number({"vendor": 4242424242424241})
    with books.transaction() as c:
        refused = act(c, OWNER, "card_save", {"card_id": coder, "vendor": 4242424242424242})
        assert (refused.status, refused.code, refused.reason) == ("failed", "invalid", FA.CARD_NUMBER_REFUSED)
        assert c.execute("SELECT count(*) FROM fin_actions WHERE kind='card_save'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM fin_card_saves").fetchone()[0] == 0
        assert "4242424242424242" not in _stored_digits(c)


def test_a_card_number_in_a_route_payload_never_reaches_the_idempotency_receipt(api, monkeypatch):
    """store.mutate hashes the body it is given as the receipt: a card number must not be in that body."""
    from backend.store import Store
    given, original = [], Store.mutate

    def spy(self, *args, **kwargs):
        given.append(repr(args) + repr(kwargs))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Store, "mutate", spy)
    body = {"kind": "card_save", "payload": {"card_id": "card-none", "vendor": 4242424242424242}}
    refused = api.post("/api/v2/finance/actions", json=body, headers=headers("ana-test")).json()
    assert refused["status"] == "failed" and "4242424242424242" not in repr(refused)
    assert given and not any("4242424242424242" in text for text in given)


def test_an_unfreeze_waits_while_an_ancestor_decline_budget_is_at_its_cutoff(books):
    fake = FakeMercury()
    budget, _, _ = _world(books, fake)               # Tools: 50.00, decline; two 20.00 cards on it
    with books.transaction() as c:
        eng = _budget(c, name="Eng tools", amount=10_000, team="eng", parent_id=budget)
        kid = str(_bot_card(c, eng, "bot:coder", amount=5_000).result["card_id"])
        ctx = load(c, AT)
        # Tools has 5.00 left once Eng tools' 5.00 card reserves its share: a 5.00 hold takes Tools to its cutoff, while
        # Eng tools (10.00, 5.00 reserved) still has room.
        held = budgeting.place(ctx, budget_scope=budget, amount=5_000, currency=ctx.book.base_currency, source="manual",
                               source_id="cutoff-tools", expires_at=None)
        assert held.allowed
        FA.drain_events(c, AT)
        assert _status(c, kid) == "frozen" and _frozen_by(c, kid) == "budget"
        refused = act(c, OWNER, "card_unfreeze", {"card_id": kid})
        assert (refused.status, refused.code) == ("denied", "denied") and "Tools budget is used up" in refused.reason
        FA.watch_documents(c, LATER)                  # the hourly sweep: Tools is still at its cutoff
        assert _status(c, kid) == "frozen"
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 100_000}).status == "committed"
        assert _status(c, kid) == "active" and _frozen_by(c, kid) is None


def test_an_unfreeze_refusal_names_only_budgets_the_asker_can_see(books):
    fake = FakeMercury()
    budget, _, _ = _world(books, fake)               # Tools: 50.00, decline; coder and seller hold 40.00 of it
    with books.transaction() as c:
        eng = _budget(c, name="Eng tools", amount=10_000, team="eng", parent_id=budget)
        kid = str(_bot_card(c, eng, "bot:coder", amount=5_000).result["card_id"])
        ctx = load(c, AT)
        held = budgeting.place(ctx, budget_scope=budget, amount=5_000, currency=ctx.book.base_currency, source="manual",
                               source_id="cutoff-hidden", expires_at=None)
        assert held.allowed
        FA.drain_events(c, AT)
        assert _status(c, kid) == "frozen" and _frozen_by(c, kid) == "budget"
        # The bot holds its card but has no view of Tools, the budget above it: the refusal does not name it.
        refused = act(c, "bot:coder", "card_unfreeze", {"card_id": kid})
        assert (refused.status, refused.code) == ("denied", "denied")
        assert "Tools" not in refused.reason
        assert refused.reason == "a budget above this one is used up; raise it before unfreezing"
        assert "Tools budget is used up" in act(c, OWNER, "card_unfreeze", {"card_id": kid}).reason


def test_a_refusal_that_named_a_budget_replays_without_the_name_once_the_asker_cannot_see_it(books):
    fake = FakeMercury()
    budget, _, _ = _world(books, fake)               # Tools: 50.00, decline; coder and seller hold 40.00 of it
    with books.transaction() as c:
        eng = _budget(c, name="Eng tools", amount=10_000, team="eng", parent_id=budget)
        kid = str(_bot_card(c, eng, "bot:coder", amount=5_000).result["card_id"])
        ctx = load(c, AT)
        held = budgeting.place(ctx, budget_scope=budget, amount=5_000, currency=ctx.book.base_currency, source="manual",
                               source_id="cutoff-replay", expires_at=None)
        assert held.allowed
        FA.drain_events(c, AT)
        assert _status(c, kid) == "frozen" and _frozen_by(c, kid) == "budget"
        grant = act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "everything"})
        assert grant.status == "committed"
        # The bot sees Tools now, so the refusal names it; the record keeps only the fallback, never the name.
        refused = act(c, "bot:coder", "card_unfreeze", {"card_id": kid}, key="unfreeze-replay")
        assert (refused.status, refused.reason) == ("denied", "the Tools budget is used up; raise it before unfreezing")
        stored = c.execute("SELECT reason FROM fin_actions WHERE id=?", (refused.action_id,)).fetchone()[0]
        assert stored == "a budget above this one is used up; raise it before unfreezing"
        # Its view is taken back: the same key replays without the name.
        assert act(c, OWNER, "view_revoke", {"grant_id": grant.result["grant_id"]}).status == "committed"
        replay = act(c, "bot:coder", "card_unfreeze", {"card_id": kid}, key="unfreeze-replay")
        assert replay.replayed and replay.reason == "a budget above this one is used up; raise it before unfreezing"
        # Given back, the same key names it again.
        assert act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "everything"}).status == "committed"
        again = act(c, "bot:coder", "card_unfreeze", {"card_id": kid}, key="unfreeze-replay")
        assert again.replayed and again.reason == "the Tools budget is used up; raise it before unfreezing"


def test_a_status_read_names_a_budget_only_while_the_asker_can_see_it(books):
    fake = FakeMercury()
    budget, _, _ = _world(books, fake)               # Tools: 50.00, decline; coder and seller hold 40.00 of it
    with books.transaction() as c:
        eng = _budget(c, name="Eng tools", amount=10_000, team="eng", parent_id=budget)
        kid = str(_bot_card(c, eng, "bot:coder", amount=5_000).result["card_id"])
        ctx = load(c, AT)
        held = budgeting.place(ctx, budget_scope=budget, amount=5_000, currency=ctx.book.base_currency, source="manual",
                               source_id="cutoff-read", expires_at=None)
        assert held.allowed
        FA.drain_events(c, AT)
        grant = act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "everything"})
        assert grant.status == "committed"
        refused = act(c, "bot:coder", "card_unfreeze", {"card_id": kid}, key="unfreeze-read")
        assert refused.reason == "the Tools budget is used up; raise it before unfreezing"
        # A status read gives the same rule as a replay: the name while the asker can see Tools, the fallback after.
        seen = FR.action_for(c, "bot:coder", refused.action_id)
        assert seen["reason"] == "the Tools budget is used up; raise it before unfreezing"
        assert act(c, OWNER, "view_revoke", {"grant_id": grant.result["grant_id"]}).status == "committed"
        unseen = FR.action_for(c, "bot:coder", refused.action_id)
        assert unseen["reason"] == "a budget above this one is used up; raise it before unfreezing"


def test_a_warn_budget_paused_at_zero_keeps_its_cards_frozen_through_the_sweep(books):
    fake = FakeMercury()
    budget, coder, seller = _world(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "at_limit": "warn"}).status == "committed"
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 0}).status == "committed"
        assert _frozen_by(c, coder) == "budget" and _frozen_by(c, seller) == "budget"
        FA.watch_documents(c, LATER)                 # the hourly sweep: a zero amount pauses a warn budget too
        assert _status(c, coder) == "frozen" and _status(c, seller) == "frozen"
    with books.transaction() as c:
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 100_000}).status == "committed"
        assert _status(c, coder) == "active" and _status(c, seller) == "active"


def test_a_budget_back_from_inactive_at_zero_today_must_fit_its_later_versions(books):
    """A paused child that comes back active brings its November version: the parent's cards (40.00) and that version
    (20.00) fit its 50.00 in October but not in November."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        line = _budget(c)                                              # Tools: 50.00 a month, decline
        eng = _budget(c, name="Eng tools", amount=20_000, team="eng", parent_id=line, effective_from="2026-11-01")
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 0}).status == "committed"
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "active": False}).status == "committed"
        assert _bot_card(c, line, "bot:coder").status == "committed"
        assert _bot_card(c, line, "bot:seller", user="user-dan").status == "committed"
        refused = act(c, OWNER, "budget_update", {"budget_id": eng, "active": True})
        assert refused.code == "invalid" and "reserve 600.00" in refused.reason
        assert c.execute("SELECT active FROM fin_budgets WHERE id=?", (eng,)).fetchone()[0] == 0
        assert act(c, OWNER, "budget_update", {"budget_id": line, "amount": 60_000}).status == "committed"
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "active": True}).status == "committed"


def test_a_child_with_no_amount_today_that_comes_back_active_fits_its_later_version(books):
    """A child with no amount in force today (its first version is November's) brings that version back with it, so on
    November 1 its 500.00 must fit the parent alone and then with the parent's 400.00 of cards."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        line = _budget(c, amount=100_000)                                # Tools: 1000.00, decline
        assert _bot_card(c, line, "bot:coder", amount=20_000).status == "committed"
        assert _bot_card(c, line, "bot:seller", amount=20_000, user="user-dan").status == "committed"
        eng = _budget(c, name="Eng tools", amount=50_000, team="eng", parent_id=line, effective_from="2026-11-01")
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "active": False}).status == "committed"
        # 500.00 of children alone is more than 450.00: refused by the children check.
        assert act(c, OWNER, "budget_update", {"budget_id": line, "amount": 45_000}).status == "committed"
        refused = act(c, OWNER, "budget_update", {"budget_id": eng, "active": True})
        assert refused.code == "invalid" and "children would total" in refused.reason
        assert c.execute("SELECT active FROM fin_budgets WHERE id=?", (eng,)).fetchone()[0] == 0
        # 500.00 fits alone at 800.00, but 400.00 of cards on top makes 900.00: refused by the reserve check.
        assert act(c, OWNER, "budget_update", {"budget_id": line, "amount": 80_000}).status == "committed"
        refused = act(c, OWNER, "budget_update", {"budget_id": eng, "active": True})
        assert refused.code == "invalid" and "reserve 900.00" in refused.reason
        assert c.execute("SELECT active FROM fin_budgets WHERE id=?", (eng,)).fetchone()[0] == 0
        assert act(c, OWNER, "budget_update", {"budget_id": line, "amount": 90_000}).status == "committed"
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "active": True}).status == "committed"


def test_a_card_on_an_ended_child_budget_is_refused_and_nothing_is_queued(books):
    """Eng tools (100.00) ends under Tools (100.00). A 200.00 card on it reserves nothing against Tools, since an ended
    budget reserves nothing, so it used to pass and queue Mercury at 200.00 while Tools stayed at 100.00 free. Now it is
    refused and nothing is queued or reserved."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        line = _budget(c, amount=10_000)                                 # Tools: 100.00 a month, decline
        eng = _budget(c, name="Eng tools", amount=10_000, team="eng", parent_id=line)
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "active": False}).status == "committed"
        refused = _bot_card(c, eng, "bot:coder", amount=20_000)
        assert refused.code == "invalid" and "has ended" in refused.reason, refused
        assert c.execute("SELECT COUNT(*) FROM fin_cards WHERE holder='bot:coder'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM fin_outbox WHERE op='card_create'").fetchone()[0] == 0
        assert FA.budget_state(c, line, AT).remaining == 10_000


def test_ending_a_child_keeps_its_card_reserve_against_the_parent(books):
    """Eng tools (100.00) has a 50.00 card under Tools (100.00), so Tools has 50.00 left. Ending Eng tools frees nothing:
    the card keeps its Mercury limit until it is lowered or cancelled. Tools' remaining must not rise."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        line = _budget(c, amount=10_000)
        eng = _budget(c, name="Eng tools", amount=10_000, team="eng", parent_id=line)
        assert _bot_card(c, eng, "bot:coder", amount=5_000).status == "committed"
        before = FA.budget_state(c, line, AT).remaining
        assert before == 5_000
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "active": False}).status == "committed"
        assert FA.budget_state(c, line, AT).remaining == before


def test_a_warn_budget_at_zero_does_not_hold_a_card_back_from_the_sweep(books):
    fake = FakeMercury()
    budget, _, _ = _world(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "at_limit": "warn"}).status == "committed"
        eng = _budget(c, name="Eng tools", amount=10_000, team="eng", parent_id=budget)
        kid = str(_bot_card(c, eng, "bot:coder", amount=5_000).result["card_id"])
        ctx = load(c, AT)
        # One 5.00 hold on Eng tools takes it to its cutoff, and Tools (a warn budget) to zero with it.
        held = budgeting.place(ctx, budget_scope=eng, amount=5_000, currency=ctx.book.base_currency, source="manual",
                               source_id="cutoff-eng", expires_at=None)
        assert held.allowed
        FA.drain_events(c, AT)
        assert _status(c, kid) == "frozen" and _frozen_by(c, kid) == "budget"
        # Releasing the hold gives Eng tools room (a raise would push Tools' cards past its amount). The warn budget
        # above it blocks nothing.
        assert budgeting.release_for(load(c, LATER), "manual", "cutoff-eng") == 1
        FA.watch_documents(c, LATER)
        assert _status(c, kid) == "active" and _frozen_by(c, kid) is None


def test_a_budget_cut_checks_its_cards_against_a_child_version_from_a_later_day(books):
    fake = FakeMercury()
    budget, _, _ = _world(books, fake)               # Tools: 50.00; coder and seller hold 40.00 of it
    with books.transaction() as c:
        eng = str(act(c, OWNER, "budget_create", {"name": "Eng tools", "account_id": account(c, "6100"), "team": "eng",
                                                  "parent_id": budget}).result["budget_id"])
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 6_000,
                                               "effective_from": "2026-11-01"}).status == "committed"
        # October alone would pass (400.00 of cards under 450.00), but Eng tools rises to 60.00 in November:
        # 460.00 is needed.
        refused = act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 45_000})
        assert refused.code == "invalid" and "reserve 460.00" in refused.reason
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 46_000}).status == "committed"


def test_unsave_finds_a_save_made_before_the_vendor_became_a_contact(books):
    fake = FakeMercury()
    _, coder, _ = _world(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "card_save", {"card_id": coder, "vendor": "Acme Cloud"}).status == "committed"
        contact = str(act(c, OWNER, "contact_create", {"name": "Acme Cloud", "kind": "vendor"}).result["contact_id"])
        # The save is keyed by the name; the contact's id is another key, and its alias is the name's.
        assert act(c, OWNER, "card_save", {"card_id": coder, "vendor": contact}).code == "conflict"
        off = act(c, OWNER, "card_unsave", {"card_id": coder, "vendor": "Acme Cloud"})
        assert off.status == "committed", (off.code, off.reason)
        assert FR.search(c, "bot:coder", kind="card", filters={"vendor": contact})["results"] == []
        assert act(c, OWNER, "card_unsave", {"card_id": coder, "vendor": contact}).code == "conflict"
        assert act(c, OWNER, "card_save", {"card_id": coder, "vendor": contact}).status == "committed"


def test_a_child_budget_cannot_push_its_parents_cards_past_the_parent(books):
    fake = FakeMercury()
    budget, _, _ = _world(books, fake)               # Tools 500.00, decline; coder and seller cards 200.00 each
    with books.transaction() as c:
        made = act(c, OWNER, "budget_create", {"name": "Ops tools", "account_id": account(c, "6100"),
                                               "vendor": "Acme Cloud", "parent_id": budget, "amount": 20_000})
        assert (made.status, made.code) == ("failed", "invalid") and "reserve 600.00" in made.reason   # 400 + 200
        eng = str(act(c, OWNER, "budget_create", {"name": "Eng tools", "account_id": account(c, "6100"), "team": "eng",
                                                  "parent_id": budget, "amount": 500}).result["budget_id"])
        raised = act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 20_000})      # 400 + 200
        assert (raised.status, raised.code) == ("failed", "invalid") and "reserve 600.00" in raised.reason
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 9_000}).status == "committed"     # 400 + 90
        # A later version of the child is fit on its own day too: 400.00 of cards + 110.00 is over Tools' 500.00.
        later = act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 11_000, "effective_from": "2026-11-01"})
        assert (later.status, later.code) == ("failed", "invalid") and "reserve 510.00" in later.reason
        ok = act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 10_000, "effective_from": "2026-11-01"})
        assert ok.status == "committed"                                                 # 400 + 100


def test_a_child_that_comes_back_active_must_fit_its_parents_cards(books):
    fake = FakeMercury()
    budget, _, _ = _world(books, fake)               # Tools 500.00, decline; coder and seller cards 200.00 each
    with books.transaction() as c:
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 100_000}).status == "committed"
        eng = str(act(c, OWNER, "budget_create", {"name": "Eng tools", "account_id": account(c, "6100"), "team": "eng",
                                                  "parent_id": budget, "amount": 20_000}).result["budget_id"])
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "active": False}).status == "committed"
        # 400.00 of cards on Tools; nothing active under it
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 50_000}).status == "committed"
        back = act(c, OWNER, "budget_update", {"budget_id": eng, "active": True})     # 400 + 200
        assert (back.status, back.code) == ("failed", "invalid") and "reserve 600.00" in back.reason
        # Its amount from a later day cannot come with the change: the days before it would go unchecked.
        later = act(c, OWNER, "budget_update", {"budget_id": eng, "active": True, "amount": 10_000,
                                                "effective_from": "2026-11-01"})
        assert (later.status, later.code) == ("failed", "invalid") and "takes its amount from today" in later.reason
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "amount": 100_000}).status == "committed"
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "active": True}).status == "committed"


def test_a_child_that_comes_back_active_must_fit_among_its_siblings(books):
    with books.transaction() as c:
        budget = _budget(c, amount=50_000)           # Tools 500.00, decline, no cards
        eng = str(act(c, OWNER, "budget_create", {"name": "Acme tools", "account_id": account(c, "6100"),
                                                  "vendor": "Acme Cloud", "parent_id": budget,
                                                  "amount": 20_000}).result["budget_id"])
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "active": False}).status == "committed"
        ops = act(c, OWNER, "budget_create", {"name": "Globex tools", "account_id": account(c, "6100"),
                                              "vendor": "Globex", "parent_id": budget, "amount": 35_000})
        assert ops.status == "committed"             # Acme tools is inactive, so Globex tools fits beside it: 350.00
        back = act(c, OWNER, "budget_update", {"budget_id": eng, "active": True})                    # 350 + 200
        assert (back.status, back.code) == ("failed", "invalid") and "overlaps" not in back.reason
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 10_000}).status == "committed"  # 350 + 100
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "active": True}).status == "committed"


def test_a_card_number_entered_as_a_tax_id_is_refused_before_it_is_sealed(books):
    with books.transaction() as c:
        before = c.execute("SELECT count(*) FROM fin_contacts").fetchone()[0]
        for typed in (4242424242424242, "4242 4242 4242 4242"):
            refused = act(c, OWNER, "contact_create", {"name": "Acme Shop", "kind": "vendor", "tax_id": typed})
            assert (refused.status, refused.code, refused.reason) == ("failed", "invalid", V.CARD_NUMBER_REFUSED)
        assert c.execute("SELECT count(*) FROM fin_contacts").fetchone()[0] == before
        assert "4242424242424242" not in _stored_digits(c)
        assert act(c, OWNER, "contact_create", {"name": "Acme Shop", "kind": "vendor",
                                                "tax_id": "12-3456789"}).status == "committed"


def test_migration_67_marks_a_cutoff_freeze_made_before_freeze_by_existed(books):
    fake = FakeMercury()
    budget, coder, seller = _world(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "card_freeze", {"card_id": seller}).status == "committed"     # a person's freeze first
        spend(c, "6100", 50_000)
        FA.drain_events(c, AT)                                                          # then the cutoff: coder only
        assert _frozen_by(c, coder) == "budget" and _frozen_by(c, seller) == "person"
        # An install from before freeze_by: the columns are empty, the action log is what there is.
        c.execute("UPDATE fin_cards SET freeze_by=NULL, freeze_budget_id=NULL")
        FS.phase3(c)
        row = c.execute("SELECT freeze_by, freeze_budget_id FROM fin_cards WHERE id=?", (coder,)).fetchone()
        assert tuple(row) == ("budget", budget)
        assert _frozen_by(c, seller) is None
        # A cutoff freeze whose card has since been given no allocation was a person's zero: it stays NULL.
        c.execute("UPDATE fin_cards SET freeze_by=NULL, freeze_budget_id=NULL")
        c.execute("UPDATE fin_allocations SET amount=0 WHERE target_kind='card' AND target_id=?", (coder,))
        FS.phase3(c)
        assert _frozen_by(c, coder) is None


def test_migration_67_adds_the_saved_card_record_to_an_install_from_66(store):
    with store.transaction() as c:
        c.execute("DELETE FROM cloud_migrations WHERE version=67")
        c.execute("DROP TABLE fin_card_saves")
    store.initialize(seed_market=False)
    with store.read() as c:
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='fin_card_saves'").fetchone()
        assert c.execute("SELECT count(*) FROM cloud_migrations WHERE version=67").fetchone()[0] == 1


def test_a_routed_raise_is_checked_at_the_raised_amount_with_the_parents_cards(books):
    """A child change that would pass its parent asks the parent's owner to raise it (spec §4.4). The raise covers the
    parent's cards as well as its children, and the cards are checked at the raised amount: a raise that fits is not
    refused up front with "lower these cards first"."""
    fake = FakeMercury()
    budget, _, _ = _world(books, fake)               # Tools 500.00; coder and seller each reserve 200.00 of it
    with books.transaction() as c:
        assert act(c, OWNER, "budget_update", {"budget_id": budget, "owner": "human:dan"}).status == "committed"
        eng = _budget(c, name="Eng tools", amount=10_000, team="eng", parent_id=budget)
        asked = act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 52_000}, key="routed-cards")
        assert (asked.status, asked.approver) == ("needs_approval", "human:dan"), (asked.code, asked.reason)
        H.approval_decide(c, "human:dan", asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done is not None and done.status == "committed", (done and done.reason)
        amounts = {bid: max(SB.versions(c, [bid])[bid], key=lambda v: (v.effective_from, v.version)).amount
                   for bid in (budget, eng)}
        assert amounts == {budget: 92_000, eng: 52_000}      # 520.00 children + 400.00 cards = 920.00


def test_a_routed_raise_must_fit_every_ancestor_with_its_cards_not_only_its_parent(books):
    """Codex P2: a routed raise raises the parent and checks the parent's cards, but not the first unchanged ancestor
    above it. Company 1,000.00 with 600.00 of cards; Software 300.00 under it; Engineering 300.00 under Software. Raising
    Engineering to 500.00 raises Software to 500.00, and Company would then reserve 1,100.00: refused, not routed. Once
    Company is 1,200.00 the same raise fits, and goes to Software's owner."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        company = _budget(c, name="Company", amount=100_000)
        assert _bot_card(c, company, "bot:coder", amount=30_000).status == "committed"
        assert _bot_card(c, company, "bot:seller", amount=30_000, user="user-dan").status == "committed"
        software = _budget(c, name="Software", amount=30_000, team="eng", parent_id=company)
        eng = _budget(c, name="Engineering", amount=30_000, team="eng", vendor="Acme Cloud", parent_id=software)
        assert act(c, OWNER, "budget_update", {"budget_id": software, "owner": "human:dan"}).status == "committed"
        refused = act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 50_000}, key="grand-refused")
        assert (refused.status, refused.code) == ("failed", "invalid"), (refused.status, refused.code, refused.reason)
        assert "Company" in refused.reason, refused.reason
        amounts = {bid: max(SB.versions(c, [bid])[bid], key=lambda v: (v.effective_from, v.version)).amount
                   for bid in (company, software, eng)}
        assert amounts == {company: 100_000, software: 30_000, eng: 30_000}
        assert act(c, OWNER, "budget_update", {"budget_id": company, "amount": 120_000}).status == "committed"
        asked = act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 50_000}, key="grand-fits")
        assert (asked.status, asked.approver) == ("needs_approval", "human:dan"), (asked.code, asked.reason)
        H.approval_decide(c, "human:dan", asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done is not None and done.status == "committed", (done and done.reason)
        amounts = {bid: max(SB.versions(c, [bid])[bid], key=lambda v: (v.effective_from, v.version)).amount
                   for bid in (company, software, eng)}
        assert amounts == {company: 120_000, software: 50_000, eng: 50_000}


def test_a_cards_refusal_names_the_cards_only_while_the_asker_can_see_them(books):
    fake = FakeMercury()
    budget, _, _ = _world(books, fake)               # Tools: 50.00; coder reserves 200.00 and seller 200.00 of it
    with books.transaction() as c:
        _budget(c, name="Eng tools", amount=10_000, team="eng", parent_id=budget)
        assert act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.budgets"}).status == "committed"
        grant = act(c, OWNER, "view_grant", {"subject": "human:ben", "what": "everything"})
        assert grant.status == "committed"
        refused = act(c, "human:ben", "budget_update", {"budget_id": budget, "amount": 45_000}, key="cards-shown")
        assert refused.code == "invalid" and "lower these cards first" in refused.reason
        assert "coder" in refused.reason and "reserves 200.00" in refused.reason
        stored = c.execute("SELECT reason FROM fin_actions WHERE id=?", (refused.action_id,)).fetchone()[0]
        assert "coder" not in stored and "seller" not in stored and "200.00" not in stored
        assert act(c, OWNER, "view_revoke", {"grant_id": grant.result["grant_id"]}).status == "committed"
        replay = act(c, "human:ben", "budget_update", {"budget_id": budget, "amount": 45_000}, key="cards-shown")
        assert replay.replayed and "coder" not in replay.reason and "200.00" not in replay.reason
        seen = FR.action_for(c, "human:ben", refused.action_id)
        assert "coder" not in seen["reason"] and "200.00" not in seen["reason"]
        assert act(c, OWNER, "view_grant", {"subject": "human:ben", "what": "everything"}).status == "committed"
        again = act(c, "human:ben", "budget_update", {"budget_id": budget, "amount": 45_000}, key="cards-shown")
        assert again.replayed and "coder" in again.reason and "reserves 200.00" in again.reason


def test_a_routed_raise_covers_the_parents_own_holds_with_its_children(books):
    """A hold placed on a parent budget itself (no target) reserves that money as a card's holds do, so a routed raise of
    its child raises the parent to cover it too. Company 1,300.00 with 600.00 of cards; Software 300.00 with a 150.00 run
    hold on it; Engineering 300.00 under it. Raising Engineering to 500.00 needs Software at 650.00 (500.00 of children
    and the hold), so the request is for that. Lowering Software below it is refused, as a card's reserve would be."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        company = _budget(c, name="Company", amount=130_000)
        assert _bot_card(c, company, "bot:coder", amount=30_000).status == "committed"
        assert _bot_card(c, company, "bot:seller", amount=30_000, user="user-dan").status == "committed"
        software = _budget(c, name="Software", amount=30_000, team="eng", parent_id=company)
        eng = _budget(c, name="Engineering", amount=30_000, team="eng", vendor="Acme Cloud", parent_id=software)
        assert act(c, OWNER, "budget_update", {"budget_id": software, "owner": "human:dan"}).status == "committed"
        held = FA.hold_place(c, budget_scope=software, amount=15_000, currency="USD", source="run", source_id="run-1",
                             expires_at=None, at=AT)
        assert held.allowed, held
        asked = act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 50_000}, key="held-raise")
        assert (asked.status, asked.approver) == ("needs_approval", "human:dan"), (asked.code, asked.reason)
        H.approval_decide(c, "human:dan", asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done is not None and done.status == "committed", (done and done.reason)
        amounts = {bid: max(SB.versions(c, [bid])[bid], key=lambda v: (v.effective_from, v.version)).amount
                   for bid in (software, eng)}
        assert amounts == {software: 65_000, eng: 50_000}
        lowered = act(c, OWNER, "budget_update", {"budget_id": software, "amount": 60_000}, key="held-lower")
        assert (lowered.status, lowered.code) == ("failed", "invalid"), (lowered.status, lowered.code, lowered.reason)


def test_a_routed_raise_covers_the_grandparents_teammate_and_gateway_allocations(books):
    """A teammate's allocation and a gateway key's allocation on a grandparent reserve their money as a card's allocation
    does, so a routed raise of the parent must fit them too. Company 900.00 holds a 200.00 allocation to eve and a 150.00
    allocation to gateway key gw-1; Software 300.00 sits under it, Engineering 300.00 under Software. Raising Engineering
    to 500.00 needs Software at 500.00, and Company at 850.00 with the allocations: that fits, and Software's owner
    approves it. Raising Engineering to 600.00 would need 950.00 at Company: refused, since one raise cannot pass the
    grandparent. Lowering Company to 840.00 is refused the same way."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        company = _budget(c, name="Company", amount=90_000)
        software = _budget(c, name="Software", amount=30_000, team="eng", parent_id=company)
        eng = _budget(c, name="Engineering", amount=30_000, team="eng", vendor="Acme Cloud", parent_id=software)
        assert act(c, OWNER, "budget_update", {"budget_id": software, "owner": "human:dan"}).status == "committed"
        for kind, target, amount in (("teammate", "human:eve", 20_000), ("gateway_key", "gw-1", 15_000)):
            made = act(c, OWNER, "allocation_set", {"budget_id": company, "target_kind": kind, "target_id": target,
                                                    "amount": amount})
            assert made.status == "committed", (kind, made.status, made.reason)
        asked = act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 50_000}, key="alloc-raise")
        assert (asked.status, asked.approver) == ("needs_approval", "human:dan"), (asked.status, asked.code, asked.reason)
        H.approval_decide(c, "human:dan", asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done is not None and done.status == "committed", (done and done.reason)
        amounts = {bid: max(SB.versions(c, [bid])[bid], key=lambda v: (v.effective_from, v.version)).amount
                   for bid in (software, eng)}
        assert amounts == {software: 50_000, eng: 50_000}
        over = act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 60_000}, key="alloc-over")
        assert (over.status, over.code) == ("failed", "invalid"), (over.status, over.code, over.reason)
        lowered = act(c, OWNER, "budget_update", {"budget_id": company, "amount": 84_000}, key="alloc-lower")
        assert (lowered.status, lowered.code) == ("failed", "invalid"), (lowered.status, lowered.code, lowered.reason)


def test_a_hold_inside_a_child_counts_once_against_the_parents_teammate_allocation(books):
    """A teammate's allocation on a parent reserves only what that teammate uses outside the children: a hold for them on a
    child sits inside the child's amount, which the parent already counts. Parent 1,000.00 with a 500.00 child and a 100.00
    teammate allocation for eve, and a 300.00 hold for eve on the child. The parent needs 600.00 (500.00 of child and
    100.00 of allocation): lowering it to 700.00 commits, lowering it to 599.00 is refused."""
    connect(books, FakeMercury())
    with books.transaction() as c:
        parent = _budget(c, name="Parent", amount=100_000)
        child = _budget(c, name="Child", amount=50_000, team="eng", parent_id=parent)
        made = act(c, OWNER, "allocation_set", {"budget_id": parent, "target_kind": "teammate",
                                                "target_id": "human:eve", "amount": 10_000})
        assert made.status == "committed", (made.status, made.reason)
        held = FA.hold_place(c, budget_scope=child, amount=30_000, currency="USD", source="run", source_id="run-eve",
                             expires_at=None, target="teammate:human:eve", at=AT)
        assert held.allowed, held
        lowered = act(c, OWNER, "budget_update", {"budget_id": parent, "amount": 70_000}, key="child-hold-700")
        assert lowered.status == "committed", (lowered.status, lowered.code, lowered.reason)
        refused = act(c, OWNER, "budget_update", {"budget_id": parent, "amount": 59_900}, key="child-hold-599")
        assert (refused.status, refused.code) == ("failed", "invalid"), (refused.status, refused.code, refused.reason)


def test_lowering_a_parent_below_an_ended_childs_card_reserve_is_refused(books):
    """Eng tools (100.00) carries an 80.00 card under Tools (100.00). Ending Eng tools keeps that card reserved against
    Tools (spec 4.4), so Tools cannot drop to 10.00; it can go down to the 80.00 it reserves and no lower."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        line = _budget(c, amount=10_000)
        eng = _budget(c, name="Eng tools", amount=10_000, team="eng", parent_id=line)
        assert _bot_card(c, eng, "bot:coder", amount=8_000).status == "committed"
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "active": False}).status == "committed"
        lowered = act(c, OWNER, "budget_update", {"budget_id": line, "amount": 1_000})
        assert lowered.code == "invalid" and "reserve 80.00" in lowered.reason, lowered
        assert FA.budget_state(c, line, AT).amount == 10_000
        assert act(c, OWNER, "budget_update", {"budget_id": line, "amount": 8_000}).status == "committed"


def test_a_card_limit_request_the_requester_cannot_see_goes_to_their_manager(books):
    """Cara leads Web (she reports to Ben) and has finance.cards only. She sees Web but not the company Tools budget above
    it, so her request to raise Web's card limit goes to Ben under the budget-request rule, not to the install owner."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        line = _budget(c, amount=10_000)
        web = _budget(c, name="Web tools", amount=10_000, team="web", parent_id=line)
        card = _bot_card(c, web, "bot:coder", amount=5_000)
        assert card.status == "committed", card
        assert act(c, OWNER, "right_grant", {"teammate": "human:cara", "right": "finance.cards"}).status == "committed"
        asked = act(c, "human:cara", "card_update_limit", {"card_id": str(card.result["card_id"]), "amount": 6_000})
        assert (asked.status, asked.approver, asked.rule) == ("needs_approval", "human:ben", "default:budget-request"), \
            (asked.code, asked.reason, asked.approver, asked.rule)


def test_a_manager_routed_card_limit_is_not_committed_by_the_old_manager_after_a_change(books):
    """Cara's card-limit request is routed to Ben, her manager. Before Ben decides, her manager becomes Dan. Ben's approval
    must not commit: the request is asked again of Dan, and the card keeps its 5,000 limit. It used to commit, because
    while an approval is decided the figure route is dropped, so the manager was never checked again."""
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        line = _budget(c, amount=10_000)
        web = _budget(c, name="Web tools", amount=10_000, team="web", parent_id=line)
        card = _bot_card(c, web, "bot:coder", amount=5_000)
        assert card.status == "committed", card
        card_id = str(card.result["card_id"])
        assert act(c, OWNER, "right_grant", {"teammate": "human:cara", "right": "finance.cards"}).status == "committed"
        asked = act(c, "human:cara", "card_update_limit", {"card_id": card_id, "amount": 6_000})
        assert (asked.status, asked.approver) == ("needs_approval", "human:ben"), (asked.code, asked.reason)
        set_manager(c, "cara", "dan")
        H.approval_decide(c, "human:ben", asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert (done.status, done.approver) == ("needs_approval", "human:dan"), (done.status, done.code, done.reason)
        limits = {str(r["target_id"]): int(r["amount"]) for r in SB.allocations(c, [web])}
        assert limits[card_id] == 5_000
