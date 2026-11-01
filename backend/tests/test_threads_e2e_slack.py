"""The Slack bridge for native threads, end to end over the API (spec section 9, "Slack bridge with fakes").

The API is the real app (the `api` fixture): links are requested and removed through `/api/v2/slack/links`, and the
thread is read and written through `/api/v2/threads`. Only Slack is faked (FakeSlack from test_slack_threads), and the
screening judge (the same fake as that module's). The gateway's tick and the channel's inbound events stand in for the
gateway loop, so each step is what the running server does, in order.
"""

from types import SimpleNamespace

import pytest

from backend import screening
from backend import slack_gateway as G
from backend.tests.test_api import api, headers  # noqa: F401  (the api fixture)
from backend.tests.test_slack_threads import BOT_USER, MARKETING, TEAM, APP, FakeJudge, FakeSlack, Clock
from backend.tests.threads_e2e import call, create_thread, ok, require_threads_api, say, timeline


def chan_event(stamp, text, user="U1", thread_ts=None):
    """A message in the linked channel, as the Slack events API delivers it."""
    return {"op": "store", "event_id": f"Ev{stamp}", "team_id": TEAM, "api_app_id": APP, "channel": MARKETING,
            "channel_kind": "channel", "ts": stamp, "thread_ts": thread_ts or stamp, "reply_ts": thread_ts or stamp,
            "user_id": user, "author": "human", "author_name": None, "event_type": "message.channels", "text": text}


def ts(n):
    return f"{1700000000 + n}.000100"


@pytest.fixture
def slack_world(api, monkeypatch):
    """The real app with a Slack gateway over FakeSlack. The screening door is the test's fake: BLOCKME holds."""
    settings = api.app.state.store.settings
    settings.slack_team_id, settings.slack_app_id = TEAM, APP
    # Sign-in and admission are the owner's to set: allow the company domain, as an install does, so the channel's
    # members (acme.example addresses) are admitted people.
    view = ok(call(api, "GET", "access", "ana-test"))
    saved = call(api, "PUT", "access/allow", "ana-test", {"allowed": view["allowed"], "allowed_domains": ["acme.example"],
                                                           "expected_revision": view["revision"]})
    assert saved.status_code == 200, saved.text

    def fake_screen(store, source, text, bot=None, **kw):
        return {"action": "hold" if "BLOCKME" in (text or "") else "warn", "source": source}

    monkeypatch.setattr(screening, "screen", fake_screen)
    slack, clock = FakeSlack(), Clock()
    gw = G.Gateway(api.app.state.store, slack, judge_engine=FakeJudge(), clock=clock)
    gw.verify_app()
    slack.members[MARKETING] = ["U1", "U2", BOT_USER]   # Ana and Ben, and the app: everyone in the channel can read a company thread
    return SimpleNamespace(api=api, slack=slack, gw=gw, bridge=gw.bridge, clock=clock)


def link(world, thread_id, channel="#marketing", token="ana-test"):
    return ok(call(world.api, "POST", "slack/links", token, {"thread_id": thread_id, "channel": channel}))


def tick(world):
    return world.gw.tick()


def links(world, token="ana-test"):
    return ok(call(world.api, "GET", "slack/links", token))


def link_state(world, link_id):
    return next(row["state"] for row in links(world)["links"] if row["id"] == link_id)


def test_an_admin_links_a_channel_to_a_company_thread_and_the_link_goes_active(slack_world):
    require_threads_api(slack_world.api)
    t = create_thread(slack_world.api, title="Marketing", audience="company")
    asked = link(slack_world, t["id"])
    assert asked["state"] == "requested", "the request is written first; the gateway checks it"
    tick(slack_world)
    assert link_state(slack_world, asked["id"]) == "active", "the check ran and both members can read the thread"
    assert asked["channel"]["name"] in ("#marketing", "marketing"), asked


def test_only_an_admin_can_link_a_channel(slack_world):
    require_threads_api(slack_world.api)
    t = create_thread(slack_world.api, title="Marketing", audience="company")
    refused = call(slack_world.api, "POST", "slack/links", "cara-test", {"thread_id": t["id"], "channel": "#marketing"})
    assert refused.status_code == 403, refused.text
    assert links(slack_world, "ana-test")["links"] == [], "a refused request writes no link"


def test_a_channel_message_is_read_in_the_thread_and_a_reply_goes_back_to_the_channel(slack_world):
    require_threads_api(slack_world.api)
    t = create_thread(slack_world.api, title="Marketing", audience="company")
    asked = link(slack_world, t["id"])
    tick(slack_world)
    assert link_state(slack_world, asked["id"]) == "active"

    slack_world.bridge.receive(chan_event(ts(1), "Pricing draft is ready"))
    assert any(m.get("body") == "Pricing draft is ready" for m in timeline(slack_world.api, "ana-test", t["id"])), \
        "the Slack message is carried into the thread as its sender"

    say(slack_world.api, "ana-test", t["id"], "Reply from Tico for the channel")
    tick(slack_world)
    posted = [p["text"] for p in slack_world.slack.posts if p["channel"] == MARKETING]
    assert any("Reply from Tico for the channel" in text for text in posted), posted


def test_unlinking_stops_replies_from_reaching_the_channel(slack_world):
    require_threads_api(slack_world.api)
    t = create_thread(slack_world.api, title="Marketing", audience="company")
    asked = link(slack_world, t["id"])
    tick(slack_world)
    assert link_state(slack_world, asked["id"]) == "active"

    gone = call(slack_world.api, "DELETE", f"slack/links/{asked['id']}", "ana-test")
    assert gone.status_code in (200, 204), gone.text
    say(slack_world.api, "ana-test", t["id"], "Sent after the unlink")
    tick(slack_world)
    posted = [p["text"] for p in slack_world.slack.posts if p["channel"] == MARKETING]
    assert not any("Sent after the unlink" in text for text in posted), posted


def test_a_blocked_channel_message_is_held_and_never_reaches_the_thread(slack_world):
    require_threads_api(slack_world.api)
    t = create_thread(slack_world.api, title="Marketing", audience="company")
    asked = link(slack_world, t["id"])
    tick(slack_world)
    assert link_state(slack_world, asked["id"]) == "active"

    slack_world.bridge.receive(chan_event(ts(2), "Clean update from the channel"))
    slack_world.bridge.receive(chan_event(ts(3), "BLOCKME pricing terms"))
    bodies = [m.get("body") for m in timeline(slack_world.api, "ana-test", t["id"])]
    assert "Clean update from the channel" in bodies, "the control message from the same channel arrives"
    assert "BLOCKME pricing terms" not in bodies, bodies


def test_a_private_thread_cannot_be_linked_to_a_channel_its_members_are_not_in(slack_world):
    require_threads_api(slack_world.api)
    private = create_thread(slack_world.api, title="Pricing war room")       # members only: Ana alone
    asked = link(slack_world, private["id"])
    tick(slack_world)
    row = next(r for r in links(slack_world)["links"] if r["id"] == asked["id"])
    assert row["state"] == "refused", row
    say(slack_world.api, "ana-test", private["id"], "Only for the war room")
    tick(slack_world)
    assert not any("Only for the war room" in p["text"] for p in slack_world.slack.posts), "nothing leaves the thread"
