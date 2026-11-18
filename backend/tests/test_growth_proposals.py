"""A bot's Growth proposal (backend/growth_proposals.py): a channel, a source or both that the bot will own, asked as an
OK of the owner (or an admin it names). Nothing is added before the OK; on it Tico adds it as that person, owned by the
bot, and tells the bot. Acme: Ana owns the company, Ben is an admin, Cara a member; ops is the growth bot."""

import json

from backend import growth_proposals as GP
from backend.tests.helpers.api import get, give_ok, post, put, send_back
from backend.tests.helpers.company import bot

CHANNEL = {"slug": "linkedin", "name": "LinkedIn posts", "track": "brand", "stage": "testing", "why": "Founder posts"}
SOURCE = {"truth_for": "signups", "system": "HubSpot", "reach_kind": "mcp"}


def propose(api, token, body, expected=200):
    return post(api, "growth/proposals", body, token=token, expected=expected)


def channels(api):
    return {c["slug"]: c for c in get(api, "growth/channels")["channels"]}


def sources(api):
    return get(api, "growth/sources")["sources"]


def events(api, action):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM events WHERE action=?", (action,))]


def test_a_proposed_channel_and_source_wait_for_the_ok_then_are_the_bots(api):
    ops = bot(api, "ops")
    asked = propose(api, ops, {"channel": CHANNEL, "source": SOURCE})
    assert asked["status"] == "waiting" and asked["asked_of"] and asked["task_id"]
    assert channels(api) == {} and sources(api) == []  # nothing before the OK
    shown = get(api, f"oks/{asked['requirement_id']}")
    assert shown["content"]["channel"]["name"] == "LinkedIn posts" and shown["content"]["source"]["system"] == "HubSpot"
    assert shown["content"]["owner"] == "bot:ops"
    give_ok(api, asked["requirement_id"])
    added = channels(api)["linkedin"]
    [source] = sources(api)
    assert added["owner_ref"] == "bot:ops" and source["owner_ref"] == "bot:ops"
    assert added["source_id"] == source["id"]
    [made] = events(api, "growth.channel.create")
    assert made["actor"] == "human:ana"  # added by the person who OK'd it
    [proposed] = events(api, "growth.channel.proposed")
    assert json.loads(proposed["detail"])["proposed_by"] == "bot:ops" and proposed["target"] == added["id"]
    with api.app.state.store.read() as c:
        said = c.execute(
            "SELECT body FROM messages WHERE recipient_id='bot:ops' AND kind='notice' AND body LIKE 'Added channel%'"
        ).fetchall()
    assert len(said) == 1 and "linkedin" in said[0][0] and source["id"] in said[0][0]


def test_the_bots_setup_says_it_may_propose_and_who_oks(api):
    ops = bot(api, "ops")
    put(api, "growth/settings", {"revenue_month": 90, "margin_pct": 40})
    nxt = get(api, "growth/setup", token=ops)["next"]
    assert nxt["step"] == "channel" and nxt["you_may"] is False
    assert nxt["you_may_propose"]["tool"] == "tico_growth_channel_propose" and nxt["you_may_propose"]["oks"]
    assert "you_may_propose" not in get(api, "growth/setup", token="cara-test")["next"]


def test_an_admin_it_was_asked_of_may_ok_it(api):
    asked = propose(api, bot(api, "ops"), {"channel": CHANNEL, "ask": "human:ben"})
    give_ok(api, asked["requirement_id"], token="ben-test")
    assert channels(api)["linkedin"]["owner_ref"] == "bot:ops"


def test_only_the_owner_or_an_admin_may_ok(api):
    asked = propose(api, bot(api, "ops"), {"channel": CHANNEL})
    with api.app.state.store.transaction() as c:
        found = GP.task_asks.grant(c, asked["requirement_id"])
        assert GP.may_ok(c, "human:cara", found) == "Only the owner or an admin OKs a Growth channel"
        assert GP.may_ok(c, "bot:ops", found) and GP.may_ok(c, "human:ben", found) is None
    propose(api, bot(api, "ops"), {"channel": CHANNEL, "ask": "human:cara"}, expected=422)
    get(api, f"oks/{asked['requirement_id']}", token="cara-test", expected=404)  # not hers to see or OK
    assert channels(api) == {}


def test_a_sent_back_proposal_adds_nothing_and_the_bot_sees_why(api):
    asked = propose(api, bot(api, "ops"), {"channel": CHANNEL, "source": SOURCE})
    send_back(api, asked["requirement_id"], note="Not LinkedIn yet.")
    assert channels(api) == {} and sources(api) == []
    with api.app.state.store.read() as c:
        told = c.execute("SELECT 1 FROM messages WHERE recipient_id='bot:ops' AND body LIKE '%Not LinkedIn yet.%'")
        assert told.fetchone()


def test_a_bot_proposes_only_what_it_owns_itself(api):
    ops = bot(api, "ops")
    propose(api, ops, {"channel": {**CHANNEL, "owner_ref": "finance"}}, expected=403)
    propose(api, ops, {"source": {**SOURCE, "owner_ref": "human:cara"}}, expected=403)
    propose(api, ops, {"channel": {**CHANNEL, "owner_ref": "ops"}})  # itself, by slug
    propose(api, "ana-test", {"channel": CHANNEL}, expected=403)  # a person adds it directly


def test_a_taken_or_waiting_slug_is_refused_and_a_retry_is_the_same_ask(api):
    ops, finance = bot(api, "ops"), bot(api, "finance")
    first = propose(api, ops, {"channel": CHANNEL})
    again = propose(api, ops, {"channel": CHANNEL})
    assert again["requirement_id"] == first["requirement_id"] and again["task_id"] == first["task_id"]
    refused = post(api, "growth/proposals", {"channel": {**CHANNEL, "name": "Other"}}, token=finance, expected=409)
    assert "already waits for an OK on task #" in str(refused)
    post(api, "growth/channels", {"slug": "door", "name": "Door to door"})
    assert "There is already a channel door" in str(
        propose(api, ops, {"channel": {**CHANNEL, "slug": "door"}}, expected=409)
    )


def test_an_ok_after_the_slug_was_taken_by_hand_adds_nothing_twice(api):
    asked = propose(api, bot(api, "ops"), {"channel": CHANNEL, "source": SOURCE})
    post(api, "growth/channels", {"slug": "linkedin", "name": "LinkedIn by hand"})
    refused = give_ok(api, asked["requirement_id"], expected=409)
    assert "There is already a channel linkedin" in str(refused)
    assert channels(api)["linkedin"]["name"] == "LinkedIn by hand" and sources(api) == []


def test_a_source_for_the_bots_own_channel_is_named_on_it(api):
    ops = bot(api, "ops")
    post(api, "growth/channels", {"slug": "linkedin", "name": "LinkedIn", "owner_ref": "bot:ops"})
    post(api, "growth/channels", {"slug": "door", "name": "Door", "owner_ref": "bot:finance"})
    propose(api, ops, {"source": SOURCE, "for_channel": "door"}, expected=403)
    asked = propose(api, ops, {"source": SOURCE, "for_channel": "linkedin"})
    give_ok(api, asked["requirement_id"])
    [source] = sources(api)
    assert source["owner_ref"] == "bot:ops" and channels(api)["linkedin"]["source_id"] == source["id"]


def test_a_bot_never_proposes_finance_ad_spend(api):
    refused = propose(api, bot(api, "ops"), {"source": {**SOURCE, "sync_kind": "finance_ad_spend"}}, expected=403)
    assert "A person connects Finance ad spend" in str(refused)
