"""The Slack bridge for native threads (`backend/slack_threads.py`, `_threads-plan.md` §5 and §9).

Only Slack is faked (FakeSlack), and the screening door. Core is the real `backend/threads.py`, and the org rule check
is the real one with the test judge from test_org_rule_check.
"""

import json
import logging
import sqlite3
import time
from types import SimpleNamespace

import pytest

from backend import hubdb as H
from backend import task_engine as E
from backend import screening
from backend import slack_channels as SC
from backend import slack_gateway as G
from backend import slack_threads as ST
from backend import threads as TH
from backend.auth import Auth, Identity
from backend.config import Settings
from backend.store import Problem, Store, encode
from backend import bot_settings as BS
from backend.tests.helpers.slack_threads import APP, BOT_USER, Clock, FakeJudge, FakeSlack, MARKETING, TEAM

COMPANY, MEMBERS = "T-company", "T-members"
WHO = Identity("human:ana", "human", email="ana@acme.example")
OWNER = Identity("human:ben", "owner", email="ben@acme.example")  # a rule author other than the sender
PEOPLE = [
    {"id": "ana", "name": "Ana Rivera", "email": "ana@acme.example", "team": "leadership", "primary_for": ["*"]},
    {"id": "ben", "name": "Ben", "email": "ben@acme.example", "team": "product", "primary_for": ["product"]},
    # On the roster, but not admitted by hub-access (no acme.example address).
    {"id": "contractor", "name": "A Contractor", "email": "contractor@example.com", "team": ""},
]
BOTS = {
    "assistant": {"display_name": "Tico", "description": "The assistant.", "reports_to": None, "status": "active"},
    "legal": {
        "display_name": "Legal",
        "description": "Contracts, terms and legal risk.",
        "reports_to": None,
        "status": "active",
    },
}


@pytest.fixture
def hub(tmp_path):
    registry = tmp_path / "hub-registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text("owner: ana@acme.example\nallowed_domains: [acme.example]\n")
    settings = Settings(
        db_path=tmp_path / "hub.db", slack_team_id=TEAM, slack_app_id=APP, registry_dir=registry, assistant_name="Tico"
    )
    store = Store(settings)
    store.initialize()
    with store.transaction() as c:
        H.sync_registry(
            c, {slug: {"name": slug, "runtime": "fake", **entry} for slug, entry in BOTS.items()}, {"people": PEOPLE}
        )
        c.execute(
            "INSERT INTO registry_metadata VALUES('people',?)",
            (encode({"default_user": "ana", "teams": {}, "people": PEOPLE}),),
        )
        for slug, entry in BOTS.items():
            c.execute(
                'INSERT INTO bot_config(bot,team,owner_id,description,reports_to) VALUES(?,?,?,?,?)',
                (slug, None, 'ana', entry['description'], entry['reports_to']),
            )
            BS.write(c, slug, {'name': slug, 'runtime': 'fake'})
        now = H.now()
        for thread_id, audience, subject in ((COMPANY, "company", "Marketing"), (MEMBERS, "members", "Plans")):
            c.execute(
                "INSERT INTO conversations (id,kind,subject,created_at,audience,root_id,path) VALUES (?,?,?,?,?,?,'')",
                (thread_id, "thread", subject, now, audience, thread_id),
            )
        # A members thread is read by its members: Ana is one, so the admin can see it and link it.
        c.execute(
            "INSERT INTO thread_members (id,thread_id,actor,role,added_by,created_at,updated_at) "
            "VALUES ('m-members-ana',?,'human:ana','member','human:ben',?,?)",
            (MEMBERS, now, now),
        )
    return store


@pytest.fixture
def world(hub, monkeypatch):
    slack, clock = FakeSlack(), Clock()
    screened = []

    def fake_screen(store, source, text, bot=None, **kw):
        """The screening door with the judge taken out: text that says BLOCKME is held (a Block), anything else passes."""
        screened.append(source)
        return {"action": "hold" if "BLOCKME" in (text or "") else "warn", "source": source}

    monkeypatch.setattr(screening, "screen", fake_screen)
    gw = G.Gateway(hub, slack, judge_engine=FakeJudge(), clock=clock)
    gw.verify_app()
    return SimpleNamespace(hub=hub, slack=slack, clock=clock, gw=gw, bridge=gw.bridge, screened=screened)


# ----------------------------------------------------------------------------- helpers
def all_rows(world, sql, *args):
    with world.hub.read() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(world, sql, *args):
    found = all_rows(world, sql, *args)
    assert len(found) == 1, found
    return found[0]


def request(world, ref, thread=COMPANY, history=False):
    return ST.request_link(world.hub, WHO, thread, ref, history=history)["id"]


def link_row(world, link_id):
    return one(world, "SELECT * FROM slack_thread_links WHERE id=?", link_id)


def gate(world, link_id):
    with world.hub.read() as c:
        row = dict(c.execute("SELECT * FROM slack_thread_links WHERE id=?", (link_id,)).fetchone())
    return world.bridge.gate(row)


def activate_marketing(world, members=("U1", "U2")):
    world.slack.members[MARKETING] = list(members) + [BOT_USER]
    link_id = request(world, "#marketing")
    assert gate(world, link_id) == "active"
    return link_id


def ts(n):
    return f"{1700000000 + n}.000100"


def chan_event(stamp, text, user="U1", thread_ts=None):
    return {
        "op": "store",
        "event_id": f"Ev{stamp}",
        "team_id": TEAM,
        "api_app_id": APP,
        "channel": MARKETING,
        "channel_kind": "channel",
        "ts": stamp,
        "thread_ts": thread_ts or stamp,
        "reply_ts": thread_ts or stamp,
        "user_id": user,
        "author": "human",
        "author_name": None,
        "event_type": "message.channels",
        "text": text,
    }


def chan_edit(stamp, text, user="U1"):
    return {
        "op": "edit",
        "event_id": f"Ed{stamp}",
        "team_id": TEAM,
        "api_app_id": APP,
        "channel": MARKETING,
        "ts": stamp,
        "thread_ts": stamp,
        "reply_ts": stamp,
        "user_id": user,
        "author": "human",
        "event_type": "message.channels",
        "text": text,
    }


def chan_delete(stamp, user="U1"):
    return {
        "op": "delete",
        "event_id": f"Ed{stamp}",
        "team_id": TEAM,
        "api_app_id": APP,
        "channel": MARKETING,
        "ts": stamp,
        "thread_ts": stamp,
        "reply_ts": stamp,
        "user_id": user,
        "author": "human",
        "event_type": "message.channels",
    }


def send_as_ana(world, text, parent=None, thread=COMPANY):
    with world.hub.transaction() as c:
        return TH.post(c, WHO, thread, text, kind="say", refs={}, parent_message_id=parent, via="assistant")["id"]


def outside_members(world, thread_id=COMPANY):
    return [
        r["actor"]
        for r in all_rows(
            world, "SELECT actor FROM thread_members WHERE thread_id=? AND actor LIKE 'slack:%'", thread_id
        )
    ]


def origin_message(world, stamp):
    return one(
        world,
        "SELECT m.id, m.body, m.revision, m.deleted_at, m.origin_id, m.conversation_id FROM messages m "
        "JOIN slack_message_links l ON l.message_id = m.id WHERE l.channel_id=? AND l.ts=? AND l.direction='in'",
        MARKETING,
        stamp,
    )


# ----------------------------------------------------------------------------- linking
def test_a_link_is_requested_for_one_root_thread_at_a_time(world):
    with world.hub.transaction() as c:
        c.execute(
            "INSERT INTO conversations (id,kind,subject,created_at,audience,root_id,parent_id,parent_message_id,path) "
            "VALUES ('T-reply','thread','Reply','x','company',?,?,'m1','')",
            (COMPANY, COMPANY),
        )
        c.execute(
            "INSERT INTO conversations (id,kind,subject,created_at,audience,root_id,path) "
            "VALUES ('T-own','thread','Own','x','personal','T-own','')"
        )
        c.execute(
            "INSERT INTO thread_members (id,thread_id,actor,role,added_by,created_at,updated_at) "
            "VALUES ('m-own-ana','T-own','human:ana','member','human:ana','x','x')"
        )
    with pytest.raises(Problem) as sub:
        request(world, "#marketing", thread="T-reply")
    assert sub.value.code == "not_root"
    with pytest.raises(Problem) as own:
        request(world, "#marketing", thread="T-own")
    assert own.value.code == "audience"
    link_id = request(world, "#marketing")
    with pytest.raises(Problem) as twice:
        request(world, "#marketing")
    assert twice.value.code == "conflict"
    assert link_row(world, link_id)["state"] == "requested"


def test_a_channel_links_only_when_its_audience_and_every_member_allow_it(world):
    # The five refusals come first, so the active link's outside members are not there to mask a refused one.
    for ref, thread, why in [
        ("#marketing", MEMBERS, "company thread"),  # a public channel needs a company thread
        ("#secret", COMPANY, "members thread"),  # a private channel needs a members thread
        ("#partners", COMPANY, "Slack Connect"),  # a shared channel is refused
        ("#lounge", COMPANY, "don't map"),  # a member who is not admitted does not map to a roster person
        ("#ghost", COMPANY, "No channel named"),
    ]:
        link_id = request(world, ref, thread=thread)
        assert gate(world, link_id) == "refused", ref
        assert why in link_row(world, link_id)["reason"], ref
        assert outside_members(world) == [], ref
    link_id = request(world, "#marketing", thread=COMPANY)
    assert gate(world, link_id) == "active"
    assert link_row(world, link_id)["channel_id"] == MARKETING
    assert outside_members(world) == [f"slack:{MARKETING}"]
    assert [
        m["person_id"]
        for m in all_rows(world, "SELECT person_id FROM slack_link_members WHERE link_id=? ORDER BY person_id", link_id)
    ] == ["ana", "ben"]


# ----------------------------------------------------------------------------- suspension
def test_a_link_that_stops_fitting_is_suspended_with_one_needs_you_item_and_a_second_break_reuses_it(world):
    link_id = activate_marketing(world, members=("U1",))
    breaks = {
        "slack connect": (
            lambda: world.slack.channels[MARKETING].__setitem__("is_ext_shared", True),
            lambda: world.slack.channels[MARKETING].pop("is_ext_shared"),
        ),
        "unmapped member": (
            lambda: world.slack.members[MARKETING].append("U3"),
            lambda: world.slack.members[MARKETING].remove("U3"),
        ),
        "thread narrows": (lambda: set_audience(world, "members"), lambda: set_audience(world, "company")),
    }
    first = None
    for change, (apply, undo) in breaks.items():
        apply()
        world.clock.advance(ST.CHECK_SECONDS + 1)
        world.bridge.check_links()
        row = link_row(world, link_id)
        assert row["state"] == "suspended", change
        task = one(world, "SELECT id, title FROM tasks WHERE id=?", row["needs_task_id"])
        assert "stopped" in task["title"], change
        first = first or row["needs_task_id"]
        assert row["needs_task_id"] == first, f"{change}: a second break reuses the open needs-you item"
        undo()
        ST.resume_link(world.hub, WHO, link_id)
        assert gate(world, link_id) == "active", change
    assert len(all_rows(world, "SELECT id FROM tasks WHERE title LIKE 'Slack channel #marketing%'")) == 1


def set_audience(world, audience):
    with world.hub.transaction() as c:
        c.execute("UPDATE conversations SET audience=? WHERE id=?", (audience, COMPANY))


# ----------------------------------------------------------------------------- inbound
def test_a_channel_message_goes_through_the_gateway_as_the_mapped_person_and_is_not_stored(world):
    activate_marketing(world)
    envelope = {
        "type": "event_callback",
        "team_id": TEAM,
        "api_app_id": APP,
        "event_id": "Ev-kickoff",
        "event": {
            "type": "message",
            "channel": MARKETING,
            "channel_type": "channel",
            "user": "U1",
            "ts": ts(1),
            "text": "Kickoff is at 10",
        },
    }
    assert world.gw.receive(envelope) == "posted"
    assert all_rows(world, "SELECT event_id FROM slack_events") == []
    msg = one(world, "SELECT sender_id, body, refs, origin_id FROM messages")
    assert msg["sender_id"] == "human:ana" and msg["body"] == "Kickoff is at 10"
    refs = json.loads(msg["refs"])
    assert refs["slack"] == {"channel": MARKETING, "ts": ts(1), "team": TEAM}
    assert msg["origin_id"].endswith(f"@slack:{TEAM}")
    assert "slack" in world.screened


def test_a_slack_thread_reply_is_a_sub_thread_of_the_message_it_answers(world):
    activate_marketing(world)
    world.bridge.receive(chan_event(ts(1), "Kickoff"))
    root = origin_message(world, ts(1))
    assert world.bridge.receive(chan_event(ts(2), "Re: kickoff", user="U2", thread_ts=ts(1))) == "posted"
    reply = one(
        world,
        "SELECT m.conversation_id, c.parent_message_id FROM messages m JOIN conversations c "
        "ON c.id = m.conversation_id WHERE m.body = 'Re: kickoff'",
    )
    assert reply["parent_message_id"] == root["id"]


# ----------------------------------------------------------------------------- Block
def test_a_blocked_message_is_held_and_its_author_is_told_privately(world):
    activate_marketing(world)
    assert world.bridge.receive(chan_event(ts(1), "BLOCKME: the plan")) == "held"
    assert all_rows(world, "SELECT id FROM messages") == []
    assert world.slack.ephemeral[-1][1] == "U1"
    # An edit of a held message is held too, and nothing reaches the core.
    assert world.bridge.receive(chan_edit(ts(1), "Fixed text")) == "held"
    assert all_rows(world, "SELECT id FROM message_revisions") == []
    assert len(world.slack.ephemeral) == 2


def test_a_held_edit_is_neither_applied_nor_mirrored_out(world):
    activate_marketing(world)
    world.bridge.receive(chan_event(ts(1), "Kickoff"))
    assert world.bridge.receive(chan_edit(ts(1), "BLOCKME now")) == "held"
    assert origin_message(world, ts(1))["body"] == "Kickoff"
    world.bridge.outbound()
    assert world.slack.updates == [] and world.slack.deletes == [] and world.slack.posts == []


def test_a_slack_message_is_never_sent_back_to_its_own_channel(world):
    activate_marketing(world)
    world.bridge.receive(chan_event(ts(1), "From the channel"))
    world.bridge.outbound()
    assert world.slack.posts == []
    assert one(world, "SELECT state FROM message_deliveries")["state"] == "delivered"


# ----------------------------------------------------------------------------- outbound
def test_a_tico_message_goes_out_as_the_app_with_the_person_named(world):
    activate_marketing(world)
    message_id = send_as_ana(world, "Ship it")
    world.bridge.outbound()
    [sent] = world.slack.posts
    assert sent["channel"] == MARKETING and sent["thread_ts"] == ""
    assert sent["text"] == "*Ana Rivera* · via Tico\nShip it"
    link = one(world, "SELECT state, ts FROM slack_message_links WHERE direction='out'")
    assert link["state"] == "sent" and link["ts"] == sent["ts"]
    assert one(world, "SELECT state FROM message_deliveries WHERE message_id=?", message_id)["state"] == "delivered"


def test_a_revision_goes_out_as_an_update_and_a_delete_as_a_delete(world):
    activate_marketing(world)
    message_id = send_as_ana(world, "Ship it")
    world.bridge.outbound()
    sent_ts = world.slack.posts[0]["ts"]
    with world.hub.transaction() as c:
        TH.edit_message(c, WHO, message_id, "Ship it today")
    world.bridge.outbound()
    assert world.slack.updates == [(MARKETING, sent_ts, "*Ana Rivera* · via Tico\nShip it today")]
    with world.hub.transaction() as c:
        TH.delete_message(c, WHO, message_id)
    world.bridge.outbound()
    assert world.slack.deletes == [(MARKETING, sent_ts)]


def test_sub_threads_are_flattened_with_the_title_two_levels_down(world):
    activate_marketing(world)
    root = send_as_ana(world, "Kickoff")
    reply = send_as_ana(world, "Budget: first pass", parent=root)
    reply_thread = one(world, "SELECT conversation_id FROM messages WHERE id=?", reply)["conversation_id"]
    send_as_ana(world, "Budget: final", parent=reply, thread=reply_thread)
    with world.hub.transaction() as c:  # the reply's own sub-thread has a title
        c.execute("UPDATE conversations SET subject=? WHERE parent_message_id=?", ("Budget", reply))
    world.bridge.outbound()
    root_ts = world.slack.posts[0]["ts"]
    assert [(p["thread_ts"], p["text"]) for p in world.slack.posts] == [
        ("", "*Ana Rivera* · via Tico\nKickoff"),
        (root_ts, "*Ana Rivera* · via Tico\nBudget: first pass"),
        (root_ts, "*Ana Rivera* · via Tico\n↳ Budget\nBudget: final"),
    ]


def test_unlinking_settles_what_was_not_yet_sent_so_nothing_goes_out(world):
    link_id = activate_marketing(world)
    send_as_ana(world, "Not yet")
    ST.unlink_link(world.hub, WHO, link_id)
    assert world.bridge.outbound() == []
    assert world.slack.posts == []
    assert one(world, "SELECT state FROM message_deliveries")["state"] == "moot"
    assert not world.bridge.linked(MARKETING)


# ----------------------------------------------------------------------------- uncertain sends
def test_an_uncertain_post_that_landed_is_found_in_history_and_not_sent_again(world):
    activate_marketing(world)
    send_as_ana(world, "Ship it")
    world.slack.lose = ["after"]
    world.bridge.outbound()
    assert one(world, "SELECT state FROM slack_message_links WHERE direction='out'")["state"] == "uncertain"
    world.clock.advance(130)
    world.bridge.reconcile()
    world.bridge.outbound()
    assert len(world.slack.posts) == 1
    assert one(world, "SELECT state FROM message_deliveries")["state"] == "delivered"


def test_an_uncertain_post_that_never_landed_is_sent_once_more(world):
    activate_marketing(world)
    send_as_ana(world, "Ship it")
    world.slack.lose = ["before"]
    world.bridge.outbound()
    world.clock.advance(130)
    world.bridge.reconcile()
    world.bridge.outbound()
    assert len(world.slack.posts) == 1
    assert one(world, "SELECT state FROM message_deliveries")["state"] == "delivered"


# ----------------------------------------------------------------------------- history
# ----------------------------------------------------------------------------- inbound: duplicates, queue, cache, rules
def test_messages_that_arrive_while_suspended_are_posted_on_resume_in_order(world):
    link_id = activate_marketing(world, members=("U1",))
    world.slack.members[MARKETING].append("U3")  # an unmapped member: the link suspends
    world.clock.advance(ST.CHECK_SECONDS + 1)
    world.bridge.check_links()
    assert link_row(world, link_id)["state"] == "suspended"
    assert world.bridge.receive(chan_event(ts(1), "first while suspended")) == "queued"
    assert world.bridge.receive(chan_event(ts(2), "second while suspended")) == "queued"
    assert all_rows(world, "SELECT id FROM messages WHERE body LIKE '%while suspended%'") == []
    world.slack.members[MARKETING].remove("U3")
    ST.resume_link(world.hub, WHO, link_id)
    assert gate(world, link_id) == "active"
    world.bridge.tick()
    assert [
        m["body"] for m in all_rows(world, "SELECT body FROM messages WHERE body LIKE '%while suspended%' ORDER BY seq")
    ] == ["first while suspended", "second while suspended"]
    assert all_rows(world, "SELECT seq FROM slack_inbound_events") == []


def test_users_info_is_asked_once_an_hour_per_user(world, monkeypatch):
    activate_marketing(world)
    asked = []
    real = world.slack.users_info
    monkeypatch.setattr(world.slack, "users_info", lambda uid: asked.append(uid) or real(uid))
    world.bridge.receive(chan_event(ts(1), "one"))
    world.bridge.receive(chan_event(ts(2), "two"))
    assert asked.count("U1") == 0  # linking asked already, within the hour
    world.clock.advance(ST.USER_CACHE_SECONDS + 1)
    world.bridge.receive(chan_event(ts(3), "three"))
    assert asked.count("U1") == 1


def rule_world(world, with_scope=None):
    """A rule in force that every person's message breaks when it says the favorite color: the test judge answers.
    `with_scope` narrows it to the people it names (a With rule)."""
    from backend.tests.helpers.org_rule_check import COLOR, Judge
    from backend import org_rules as R

    with world.hub.transaction() as c:
        R.add_rule(
            c,
            Auth(world.hub),
            OWNER,
            {
                "kind": "internal",
                "text": COLOR,
                "who": {"everyone": True},
                "with": with_scope or {},
                "if_broken": "block",
            },
        )
    world.gw._judge = Judge(world.hub)
    return COLOR


def test_a_rule_break_holds_the_slack_message_as_its_person_with_its_provenance(world):
    activate_marketing(world)
    rule_world(world)
    assert world.bridge.receive(chan_event(ts(1), "The favorite color is teal")) == "held"
    assert all_rows(world, "SELECT id FROM messages") == []
    hold = one(world, "SELECT * FROM org_rule_holds")
    assert hold["author"] == "human:ana"
    provenance = json.loads(hold["payload"])["provenance"]
    assert provenance["origin"] == {"source": "slack", "origin": f"{MARKETING}:{ts(1)}"}
    assert provenance["authenticated"].startswith("slack:")
    assert world.slack.ephemeral[-1][1] == "U1"


def test_send_anyway_carries_a_held_slack_message_in_as_its_person(world):
    from backend import org_rule_check as OR

    activate_marketing(world)
    rule_world(world)
    world.bridge.receive(chan_event(ts(1), "The favorite color is teal"))
    hold = one(world, "SELECT id, nonce FROM org_rule_holds")
    with world.hub.transaction() as c:
        OR.send(c, OWNER, hold["id"], hold["nonce"], auth=Auth(world.hub))
    carried = one(
        world,
        "SELECT m.sender_id, m.body FROM messages m JOIN slack_message_links l ON l.message_id = m.id WHERE l.ts = ?",
        ts(1),
    )
    assert carried["sender_id"] == "human:ana" and carried["body"] == "The favorite color is teal"


# ----------------------------------------------------------------------------- P1-1: revoked access stops sends
def test_a_narrowed_thread_stops_the_next_send_before_the_slack_check_runs_again(world):
    link_id = activate_marketing(world)
    send_as_ana(world, "Sent while allowed")
    world.bridge.outbound()  # the channel's Slack side is trusted for SEND_CHECK_SECONDS from here
    assert len(world.slack.posts) == 1
    send_as_ana(world, "Written after the change")
    with world.hub.transaction() as c:
        c.execute("UPDATE conversations SET audience='members' WHERE id=?", (COMPANY,))
    out = world.bridge.outbound()
    assert [r["state"] for r in out] == ["suspended"]
    assert len(world.slack.posts) == 1
    assert link_row(world, link_id)["state"] == "suspended"
    assert [
        d["state"]
        for d in all_rows(
            world, "SELECT state FROM message_deliveries WHERE state='pending' AND recipient=?", f"slack:{MARKETING}"
        )
    ] == ["pending"], "the message written after the change waits"


def test_a_member_who_can_no_longer_read_the_thread_stops_the_next_send(world, monkeypatch):
    link_id = activate_marketing(world)
    send_as_ana(world, "first")
    world.bridge.outbound()
    send_as_ana(world, "second")
    real = world.bridge.can_read
    monkeypatch.setattr(world.bridge, "can_read", lambda c, thread, pid: pid != "ben" and real(c, thread, pid))
    assert [r["state"] for r in world.bridge.outbound()] == ["suspended"]
    assert len(world.slack.posts) == 1
    assert link_row(world, link_id)["state"] == "suspended"


def test_a_channel_that_cannot_be_verified_on_slack_is_paused_and_nothing_is_sent_to_it(world):
    activate_marketing(world)
    world.slack.down = True
    send_as_ana(world, "Unverifiable")
    assert [r["state"] for r in world.bridge.outbound()] == ["paused"]
    assert world.slack.posts == []
    assert one(world, "SELECT state FROM message_deliveries")["state"] == "pending"
    world.slack.down = False
    world.bridge.outbound()
    assert len(world.slack.posts) == 1


# ----------------------------------------------------------------------------- P1-2: per-message privacy
def test_a_restricted_message_that_a_channel_member_cannot_read_never_reaches_the_channel(world):
    activate_marketing(world)
    message_id = send_as_ana(world, "Private to the thread")
    with world.hub.transaction() as c:
        c.execute("UPDATE messages SET restricted=1, legacy_audience='shared:legal' WHERE id=?", (message_id,))
    assert [r["state"] for r in world.bridge.outbound()] == ["moot"]
    assert world.slack.posts == []


def test_a_posted_message_comes_down_when_the_channel_can_no_longer_read_it(world):
    activate_marketing(world)
    message_id = send_as_ana(world, "Shared then withdrawn")
    world.bridge.outbound()
    sent_ts = world.slack.posts[0]["ts"]
    with world.hub.transaction() as c:
        TH.edit_message(c, WHO, message_id, "Shared then withdrawn, edited")  # the edit makes Tico look at it again
        c.execute("UPDATE messages SET restricted=1, legacy_audience='shared:legal' WHERE id=?", (message_id,))
    world.bridge.outbound()
    assert world.slack.deletes == [(MARKETING, sent_ts)]
    assert world.slack.updates == []


# ----------------------------------------------------------------------------- P1-3: one ingress for linked channels
def test_an_app_mention_in_a_linked_channel_is_carried_once_and_its_message_form_is_a_duplicate(world):
    activate_marketing(world)
    mention = {
        "type": "event_callback",
        "team_id": TEAM,
        "api_app_id": APP,
        "event_id": "Ev-mention-1",
        "event": {
            "type": "app_mention",
            "channel": MARKETING,
            "user": "U1",
            "ts": ts(1),
            "text": f"<@{BOT_USER}> kickoff is at 10?",
        },
    }
    assert world.gw.receive(mention) == "posted"
    assert all_rows(world, "SELECT event_id FROM slack_events") == [], (
        "a two-way linked channel's mention is not stored for the gateway"
    )
    # The mention also wakes Tico: core's notice ('human:ana in this thread: ...') is written beside the message.
    [carried] = all_rows(world, "SELECT body FROM messages WHERE kind='say'")
    assert "kickoff is at 10?" in carried["body"] and "<@" not in carried["body"]
    assert (
        len(all_rows(world, "SELECT id FROM messages WHERE body LIKE '%kickoff is at 10?%'")) == 2
    )  # message + notice
    message = {
        "type": "event_callback",
        "team_id": TEAM,
        "api_app_id": APP,
        "event_id": "Ev-message-1",
        "event": {
            "type": "message",
            "channel": MARKETING,
            "channel_type": "channel",
            "user": "U1",
            "ts": ts(1),
            "text": f"<@{BOT_USER}> kickoff is at 10?",
        },
    }
    assert world.gw.receive(message) == "duplicate"
    assert len(all_rows(world, "SELECT id FROM messages WHERE kind='say'")) == 1


# ----------------------------------------------------------------------------- P1-5: sub-threads and the link generation
def test_a_sub_thread_made_before_the_link_gets_the_channel_as_an_outside_member(world):
    root = send_as_ana(world, "Kickoff")
    reply = send_as_ana(world, "Budget", parent=root)
    reply_thread = one(world, "SELECT conversation_id FROM messages WHERE id=?", reply)["conversation_id"]
    assert outside_members(world, reply_thread) == []
    activate_marketing(world)
    assert outside_members(world, reply_thread) == [f"slack:{MARKETING}"]
    assert outside_members(world) == [f"slack:{MARKETING}"]


def test_unlinking_takes_the_channel_off_every_sub_thread_in_the_same_pass(world):
    link_id = activate_marketing(world)
    root = send_as_ana(world, "Kickoff")
    reply = send_as_ana(world, "Budget", parent=root)
    reply_thread = one(world, "SELECT conversation_id FROM messages WHERE id=?", reply)["conversation_id"]
    assert outside_members(world, reply_thread) == [f"slack:{MARKETING}"]
    ST.unlink_link(world.hub, WHO, link_id)
    assert outside_members(world, reply_thread) == []
    assert outside_members(world) == []


def test_a_relink_exports_nothing_written_while_unlinked_and_the_new_link_sends_normally(world):
    link_id = activate_marketing(world)
    send_as_ana(world, "Before the unlink")
    world.bridge.outbound()
    ST.unlink_link(world.hub, WHO, link_id)
    send_as_ana(world, "While unlinked")
    relinked = activate_marketing(world)
    assert relinked != link_id
    send_as_ana(world, "After the relink")
    world.bridge.outbound()
    texts = [p["text"] for p in world.slack.posts]
    assert not any("While unlinked" in t for t in texts)
    assert any("After the relink" in t for t in texts)


# ----------------------------------------------------------------------------- P1-6: the audience reaches the rule check
def test_a_with_rule_is_checked_against_the_channel_members_who_read_the_thread(world):
    activate_marketing(world)  # Ana's message's audience is Ben, who maps and reads the thread
    rule_world(world, with_scope={"people": ["ben"]})
    assert world.bridge.receive(chan_event(ts(1), "The favorite color is teal")) == "held"


def test_a_with_rule_naming_nobody_in_the_audience_does_not_hold(world):
    activate_marketing(world)
    rule_world(world, with_scope={"people": ["contractor"]})
    assert world.bridge.receive(chan_event(ts(1), "The favorite color is teal")) == "posted"


def test_send_anyway_is_refused_when_the_audience_changed_since_the_hold(world):
    from backend import org_rule_check as OR

    activate_marketing(world)
    rule_world(world, with_scope={"people": ["ben"]})
    assert world.bridge.receive(chan_event(ts(1), "The favorite color is teal")) == "held"
    hold = one(world, "SELECT id, nonce FROM org_rule_holds")
    with world.hub.transaction() as c:
        c.execute("DELETE FROM slack_link_members WHERE person_id='ben'")
    with pytest.raises(Problem) as exc:
        with world.hub.transaction() as c:
            OR.send(c, OWNER, hold["id"], hold["nonce"], auth=Auth(world.hub))
    assert exc.value.code == "changed" and exc.value.status == 409
    assert all_rows(world, "SELECT id FROM messages") == []
    assert one(world, "SELECT state FROM org_rule_holds")["state"] == "held", "the hold stays held"


def test_a_message_whose_audience_moves_before_its_write_is_retried_and_then_checked_again(world, monkeypatch):
    from backend import org_rule_check as OR

    link_id = activate_marketing(world)
    rule_world(world, with_scope={"people": ["ben"]})
    real = OR.inbound

    def moves_after_the_check(*args, **kwargs):
        verdict = real(*args, **kwargs)
        with world.hub.transaction() as c:
            c.execute("DELETE FROM slack_link_members WHERE person_id='ben'")
        return verdict

    monkeypatch.setattr(OR, "inbound", moves_after_the_check)
    assert world.bridge.receive(chan_event(ts(1), "The favorite color is teal")) == "retry"
    assert all_rows(world, "SELECT id FROM messages") == []
    assert len(all_rows(world, "SELECT seq FROM slack_inbound_events")) == 1
    monkeypatch.setattr(OR, "inbound", real)
    assert world.bridge.drain(link_id) == "posted", "the next try checks the audience as it is now"
    assert len(all_rows(world, "SELECT id FROM messages")) == 1


# ----------------------------------------------------------------------------- P1-7: fair batching per channel
def test_a_busy_channel_does_not_hold_back_another_channel_in_one_pass(world, monkeypatch):
    sales = "C0000000004"
    monkeypatch.setattr(ST, "SEND_BATCH", 3)
    activate_marketing(world)
    world.slack.channels[sales] = {"id": sales, "name": "sales", "is_member": True, "is_private": False}
    world.slack.members[sales] = ["U1", BOT_USER]
    with world.hub.transaction() as c:
        c.execute(
            "INSERT INTO conversations (id,kind,subject,created_at,audience,root_id,path) "
            "VALUES ('T-sales','thread','Sales','x','company','T-sales','')"
        )
    assert gate(world, request(world, "#sales", thread="T-sales")) == "active"
    for n in range(10):
        send_as_ana(world, f"busy {n}")
    send_as_ana(world, "quiet", thread="T-sales")
    world.bridge.outbound()
    texts = [p["text"] for p in world.slack.posts]
    assert sum("busy" in t for t in texts) == 3
    assert any(t.endswith("quiet") for t in texts), "the quiet channel's message is sent in the same pass"


# ----------------------------------------------------------------------------- P2-8: visibility for admin routes
OUTSIDER = Identity("human:contractor", "human", email="contractor@example.com")


# ----------------------------------------------------------------------------- P2-9: edits without a Slack user; notes
def test_a_held_edit_without_a_slack_user_is_noted_to_its_author_and_does_not_crash(world):
    activate_marketing(world)
    assert world.bridge.receive(chan_event(ts(1), "BLOCKME: the plan")) == "held"
    edit = chan_edit(ts(1), "Fixed text")
    del edit["user_id"]  # message_changed carries no user: the author comes from the mapping
    assert world.bridge.receive(edit) == "held"
    assert world.slack.ephemeral[-1][1] == "U1"
    assert "edit" in world.slack.ephemeral[-1][2]


def test_a_failed_block_note_does_not_stall_ingestion(world, monkeypatch):
    activate_marketing(world)

    def lost(channel, user, text):
        raise G.SlackUnreachable("the note was lost")

    monkeypatch.setattr(world.slack, "post_ephemeral", lost)
    assert world.bridge.receive(chan_event(ts(1), "BLOCKME: the plan")) == "held"
    assert world.bridge.receive(chan_event(ts(2), "Next message")) == "posted"


# ----------------------------------------------------------------------------- P2-10: a revision during the send
def test_a_revision_made_during_the_send_keeps_the_delivery_pending_and_is_sent_next(world):
    activate_marketing(world)
    message_id = send_as_ana(world, "Ship it")

    def revise_during_the_send(channel, text):
        with world.hub.transaction() as c:
            TH.edit_message(c, WHO, message_id, "Ship it today")

    world.slack.on_post = revise_during_the_send
    world.bridge.outbound()
    assert world.slack.updates == []
    pending = {
        r["state"]
        for r in all_rows(
            world,
            "SELECT state FROM message_deliveries WHERE message_id=? AND recipient=?",
            message_id,
            f"slack:{MARKETING}",
        )
    }
    assert pending == {"pending"}, "the revision was not sent, so the delivery is not settled"
    world.slack.on_post = None
    world.bridge.outbound()
    assert world.slack.updates == [(MARKETING, world.slack.posts[0]["ts"], "*Ana Rivera* · via Tico\nShip it today")]


# ----------------------------------------------------------------------------- P2-11: reconciliation reads the whole window
def test_an_uncertain_reply_is_found_in_its_slack_thread_not_in_the_channel_history(world):
    activate_marketing(world)
    root = send_as_ana(world, "Kickoff")
    world.bridge.outbound()
    reply = send_as_ana(world, "Budget", parent=root)
    world.slack.lose = ["after"]
    world.bridge.outbound()
    assert (
        one(world, "SELECT state FROM slack_message_links WHERE direction='out' AND message_id=?", reply)["state"]
        == "uncertain"
    )
    world.clock.advance(130)
    world.bridge.reconcile()
    world.bridge.outbound()
    assert len(world.slack.posts) == 2, "found in its thread, so not sent again"
    assert one(world, "SELECT state FROM message_deliveries WHERE message_id=?", reply)["state"] == "delivered"


def test_an_uncertain_post_is_found_on_a_later_page_and_not_sent_again(world):
    activate_marketing(world)
    world.slack.page_size = 2
    send_as_ana(world, "Ship it")
    for n in range(3):
        world.slack.add_post(MARKETING, "", f"other {n}", f"other-{n}")
    world.slack.lose = ["after"]
    world.bridge.outbound()
    world.clock.advance(130)
    world.bridge.reconcile()
    world.bridge.outbound()
    assert len(world.slack.posts) == 4, "the target was found on page two, so it is not posted again"
    assert [
        d["state"]
        for d in all_rows(world, "SELECT state FROM message_deliveries WHERE recipient=?", f"slack:{MARKETING}")
    ] == ["delivered"]


def test_an_uncertain_post_is_absent_only_after_every_page_of_the_window_is_read(world, monkeypatch):
    activate_marketing(world)
    world.slack.page_size = 1
    send_as_ana(world, "Ship it")
    for n in range(3):
        world.slack.add_post(MARKETING, "", f"other {n}", f"other-{n}")
    world.slack.lose = ["before"]
    world.bridge.outbound()
    monkeypatch.setattr(ST, "RECONCILE_PAGES", 2)
    world.clock.advance(130)
    world.bridge.reconcile()
    assert one(world, "SELECT state FROM slack_message_links WHERE direction='out'")["state"] == "uncertain"
    assert len(world.slack.posts) == 3, "not declared missing before the whole window was read"
    monkeypatch.setattr(ST, "RECONCILE_PAGES", 50)
    world.bridge.reconcile()
    assert one(world, "SELECT state FROM slack_message_links WHERE direction='out'")["state"] == "ready"
    world.bridge.outbound()
    assert len(world.slack.posts) == 4
    assert [p["text"] for p in world.slack.posts].count("*Ana Rivera* · via Tico\nShip it") == 1


# ----------------------------------------------------------------------------- P2-12: edit order
# ----------------------------------------------------------------------------- audit 38: restricted messages use fresh members
def test_a_member_who_joins_later_is_in_the_restricted_message_check_from_the_next_send(world, monkeypatch):
    real = TH.message_readable
    monkeypatch.setattr(
        TH,
        "message_readable",
        lambda c, v, m, **kw: v.actor == "human:ana" if m.get("restricted") else real(c, v, m, **kw),
    )
    activate_marketing(world, members=("U1",))
    message_id = send_as_ana(world, "For Ana to read, not the channel")
    with world.hub.transaction() as c:
        c.execute("UPDATE messages SET restricted=1, legacy_audience='shared:legal' WHERE id=?", (message_id,))
    world.slack.members[MARKETING] = ["U1", "U2", BOT_USER]  # Ben joins: a mapped member who cannot read it
    assert [r["state"] for r in world.bridge.outbound()] == ["moot"]
    assert world.slack.posts == []
    assert [r["person_id"] for r in all_rows(world, "SELECT person_id FROM slack_link_members ORDER BY person_id")] == [
        "ana",
        "ben",
    ]


def test_a_restricted_message_waits_for_a_fresh_member_check_and_is_not_sent_on_a_stale_one(world):
    link_id = activate_marketing(world)
    message_id = send_as_ana(world, "Restricted, with the snapshot out of date")
    with world.hub.transaction() as c:
        c.execute("UPDATE messages SET restricted=1 WHERE id=?", (message_id,))
    world.bridge._verified.clear()  # the snapshot is older than the check window
    with world.hub.read() as c:
        due = ST._due(c, MARKETING, 10)
    assert due and world.bridge.send_one(link_row(world, link_id), due[0])["state"] == "waiting"
    assert world.slack.posts == []
    assert [r["state"] for r in world.bridge.outbound()] == ["delivered"]  # the next pass checks again, then sends
    assert len(world.slack.posts) == 1


# ----------------------------------------------------------------------------- read-only links: the channels bots read
def read_marketing(world, readers=("legal",)):
    """#marketing on the list with readers, as an owner adds it: a read-only link the gateway then activates."""
    with world.hub.transaction() as c:
        entry = SC.add(c, world.hub.settings, OWNER, "#marketing", readers=list(readers))
    link_id = entry["link"]["id"]
    assert gate(world, link_id) == "active"
    return link_id


def test_a_read_only_link_carries_the_channel_in_and_never_writes_back_to_slack(world):
    """The channel's members are not checked: one who is not on the roster suspends nothing and their message is
    skipped. A held message tells nobody in Slack, and what is said in the thread does not go out."""
    world.slack.members[MARKETING].append("U3")  # not admitted: a two-way link would be refused
    link_id = read_marketing(world)
    row = link_row(world, link_id)
    assert row["read_only"] == 1 and row["audience"] == "members" and ST.view(row)["read_only"] is True
    thread = row["thread_id"]
    assert outside_members(world, thread) == [] and all_rows(world, "SELECT * FROM slack_link_members") == []
    members = {r["actor"] for r in all_rows(world, "SELECT actor FROM thread_members WHERE thread_id=?", thread)}
    assert members == {"human:ben", "bot:legal"}, "the readers and the admin who listed it"
    follows = {
        r["actor"]: (r["following"], r["notify"])
        for r in all_rows(world, "SELECT actor,following,notify FROM thread_followers WHERE thread_id=?", thread)
    }
    assert follows == {"human:ben": (0, "live"), "bot:legal": (1, "digest")}

    assert world.bridge.receive(chan_event(ts(1), "Kickoff is at 10")) == "posted"
    assert one(world, "SELECT sender_id, conversation_id FROM messages WHERE body='Kickoff is at 10'") == {
        "sender_id": "human:ana",
        "conversation_id": thread,
    }
    assert world.bridge.receive(chan_event(ts(2), "From outside", user="U3")) == "skipped"
    assert all_rows(world, "SELECT id FROM messages WHERE body='From outside'") == []
    assert world.bridge.receive(chan_event(ts(3), "BLOCKME: the plan")) == "held"
    assert world.slack.ephemeral == [], "a read-only link says nothing in Slack"

    with world.hub.transaction() as c:
        TH.post(c, OWNER, thread, "Noted in Tico", kind="say", refs={}, via="assistant")
    assert world.bridge.outbound() == [] and world.slack.posts == []
    world.clock.advance(ST.CHECK_SECONDS + 1)
    world.bridge.check_links()
    assert link_row(world, link_id)["state"] == "active", "an unmapped member suspends nothing"


def test_a_read_only_links_readers_wake_once_on_the_hourly_bot_digest(world):
    from backend import thread_bot_digest

    read_marketing(world)
    for n in range(3):
        world.bridge.receive(chan_event(ts(n + 1), f"Update {n}"))
    assert thread_bot_digest.sweep(world.hub) == 1
    notice = one(world, "SELECT recipient_id, body FROM messages WHERE kind='notice' AND sender_id='keeper'")
    assert notice["recipient_id"] == "bot:legal" and ": 3" in notice["body"]


def test_a_two_way_link_for_a_channel_bots_read_is_refused_while_the_read_only_link_is_on(world):
    read_marketing(world)
    link_id = request(world, "#marketing")
    assert gate(world, link_id) == "refused"
    assert "already linked" in link_row(world, link_id)["reason"]


def test_send_anyway_carries_a_held_message_of_a_read_only_link_in_as_its_person(world):
    from backend import org_rule_check as OR

    link_id = read_marketing(world)
    rule_world(world)
    assert world.bridge.receive(chan_event(ts(1), "The favorite color is teal")) == "held"
    hold = one(world, "SELECT id, nonce FROM org_rule_holds")
    with world.hub.transaction() as c:
        OR.send(c, OWNER, hold["id"], hold["nonce"], auth=Auth(world.hub))
    carried = one(world, "SELECT sender_id, conversation_id FROM messages WHERE body='The favorite color is teal'")
    assert carried == {"sender_id": "human:ana", "conversation_id": link_row(world, link_id)["thread_id"]}
    assert world.slack.ephemeral == []


def test_the_list_adds_and_takes_off_readers_and_the_last_one_ends_the_read_only_link(world):
    link_id = read_marketing(world, readers=("legal", "assistant"))
    thread = link_row(world, link_id)["thread_id"]
    with world.hub.transaction() as c:
        left = SC.remove(c, world.hub.settings, OWNER, "#marketing", reader="assistant")
    assert left["readers"] == ["legal"] and link_row(world, link_id)["state"] == "active"
    assert all_rows(world, "SELECT 1 FROM thread_members WHERE thread_id=? AND actor='bot:assistant'", thread) == []
    with world.hub.transaction() as c:
        last = SC.remove(c, world.hub.settings, OWNER, "#marketing", reader="legal")
    assert last["readers"] == [] and last["link"] is None
    assert link_row(world, link_id)["state"] == "unlinked"
    # Listed again with a reader, the channel gets a new read-only link; a refused one is asked again.
    with world.hub.transaction() as c:
        again = SC.add(c, world.hub.settings, OWNER, "#marketing", readers=["legal"])
    assert again["link"]["id"] != link_id and again["link"]["state"] == "requested"
    world.slack.channels[MARKETING]["is_ext_shared"] = True
    assert gate(world, again["link"]["id"]) == "refused"
    with world.hub.transaction() as c:
        asked = SC.add(c, world.hub.settings, OWNER, "#marketing", readers=["assistant"])
    assert asked["link"]["id"] == again["link"]["id"] and asked["link"]["state"] == "requested"
    assert asked["readers"] == ["legal", "assistant"]
    # Taken off the list, the read-only link ends with it.
    with world.hub.transaction() as c:
        SC.remove(c, world.hub.settings, OWNER, "#marketing")
    assert link_row(world, again["link"]["id"])["state"] == "unlinked"


def test_a_reader_of_a_two_way_linked_channel_follows_its_thread_and_the_channel_keeps_its_link(world):
    link_id = activate_marketing(world)
    with world.hub.transaction() as c:
        entry = SC.add(c, world.hub.settings, OWNER, "#marketing", readers=["legal"], name="marketing")
    assert entry["link"]["id"] == link_id and entry["readers"] == ["legal"]
    follow = one(
        world, "SELECT following, notify FROM thread_followers WHERE thread_id=? AND actor='bot:legal'", COMPANY
    )
    assert (follow["following"], follow["notify"]) == (1, "digest")
    with world.hub.transaction() as c:
        assert SC.remove(c, world.hub.settings, OWNER, "#marketing", reader="legal")["readers"] == []
        SC.remove(c, world.hub.settings, OWNER, "#marketing")
    assert link_row(world, link_id)["state"] == "active", "a two-way link is the thread's, not the list's"


def later(world, n):
    """A Slack ts n seconds after the test clock's now: a read-only link catches up from its activation."""
    return f"{ST._epoch(world.clock()) + n:.6f}"


def pending_digests(world, bot):
    return [
        r["thread_id"]
        for r in all_rows(
            world,
            "SELECT thread_id FROM message_deliveries WHERE recipient=? AND mode='digest' AND state='pending' "
            "ORDER BY created_at",
            bot,
        )
    ]


FROM_SLACK = "json_extract(refs,'$.slack.ts') IS NOT NULL"


def test_a_reply_in_a_read_channel_reaches_each_readers_digest(world):
    """A Slack thread reply becomes a sub-thread of the message it answers; the readers follow it at digest before
    the reply is written, so the reply is in each reader's next digest."""
    from backend import thread_bot_digest

    link_id = read_marketing(world, readers=("legal", "assistant"))
    thread = link_row(world, link_id)["thread_id"]
    assert world.bridge.receive(chan_event(ts(1), "Launch plan")) == "posted"
    assert world.bridge.receive(chan_event(ts(2), "Looks good", user="U2", thread_ts=ts(1))) == "posted"
    assert world.bridge.receive(chan_event(ts(3), "Agreed", thread_ts=ts(1))) == "posted"
    sub = one(world, "SELECT conversation_id FROM messages WHERE body='Looks good'")["conversation_id"]
    assert sub != thread and one(world, "SELECT parent_id FROM conversations WHERE id=?", sub)["parent_id"] == thread
    for bot in ("bot:legal", "bot:assistant"):
        assert pending_digests(world, bot) == [thread, sub, sub]
    assert thread_bot_digest.sweep(world.hub) == 2
    notice = one(world, "SELECT body FROM messages WHERE kind='notice' AND recipient_id='bot:legal'")["body"]
    assert f"- {thread}: 1" in notice and f"- {sub}: 2" in notice


def test_a_read_only_link_carries_other_apps_posts_but_never_ticos_own(world):
    """An alert or deploy feed is carried in by Tico under the app's name; Tico's own app is never echoed back. A
    two-way link carries no app's post."""
    link_id = read_marketing(world)
    alert = {**chan_event(ts(1), "CPU high on web-1", user="B0ALERTS"), "author": "bot", "author_name": "alerts"}
    assert world.bridge.receive(alert) == "posted"
    carried = one(world, "SELECT sender_id, refs FROM messages WHERE body='*alerts*: CPU high on web-1'")
    assert carried["sender_id"] == H.KEEPER and json.loads(carried["refs"])["slack"]["name"] == "alerts"
    assert pending_digests(world, "bot:legal") == [link_row(world, link_id)["thread_id"]]
    own = {**chan_event(ts(2), "Tico said this", user=BOT_USER), "author": "bot"}
    assert world.bridge.receive(own) == "bot"
    with world.hub.transaction() as c:
        c.execute("UPDATE slack_thread_links SET read_only=0 WHERE id=?", (link_id,))
    assert world.bridge.receive({**alert, "ts": ts(3), "event_id": "Ev3"}) == "bot"


def test_a_sender_slack_cannot_look_up_yet_waits_and_is_tried_again(world, monkeypatch, caplog):
    """A rate limit or no answer from users.info is not a person who does not map: the message stays queued (and the
    ones after it wait) until Slack answers, for a few tries; a user Slack says is gone is skipped at once."""
    import logging

    link_id = read_marketing(world)
    answers = {"U2": [G.SlackError("users.info", "ratelimited", retry_after=30), G.SlackUnreachable("no answer")]}
    real = world.slack.users_info

    def users_info(user_id):
        if answers.get(user_id):
            raise answers[user_id].pop(0)
        return real(user_id)

    monkeypatch.setattr(world.slack, "users_info", users_info)
    caplog.set_level(logging.WARNING, logger="tico.slack.threads")
    assert world.bridge.receive(chan_event(ts(1), "From Ben", user="U2")) == "retry"
    assert world.bridge.receive(chan_event(ts(2), "From Ana")) == "retry", "the queue keeps its order"
    assert all_rows(world, "SELECT id FROM messages WHERE body IN ('From Ben','From Ana')") == []
    assert len(all_rows(world, "SELECT seq FROM slack_inbound_events WHERE link_id=?", link_id)) == 2
    assert "slack.inbound.sender" in caplog.text and "ratelimited" in caplog.text
    world.clock.advance(ST.PERSON_RETRY + 1)
    assert world.bridge.drain(link_id) == "retry", "no answer the second time either"
    world.clock.advance(2 * ST.PERSON_RETRY + 1)
    assert world.bridge.drain(link_id) == "posted"
    bodies = [r["body"] for r in all_rows(world, "SELECT body FROM messages WHERE body LIKE 'From %' ORDER BY rowid")]
    assert bodies == ["From Ben", "From Ana"]

    answers["U2"] = [G.SlackUnreachable("no answer") for _ in range(ST.PERSON_TRIES)]
    world.bridge._users.clear()
    assert world.bridge.receive(chan_event(ts(3), "Never looked up", user="U2")) == "retry"
    for _ in range(ST.PERSON_TRIES - 1):
        world.clock.advance(ST.PERSON_RETRY * ST.PERSON_TRIES)
        world.bridge.drain(link_id)
    assert all_rows(world, "SELECT seq FROM slack_inbound_events WHERE link_id=?", link_id) == [], "tries are bounded"
    assert world.bridge.receive(chan_event(ts(4), "Gone", user="U9")) == "skipped", "user_not_found is definite"


def test_a_read_only_link_catches_up_on_what_the_socket_missed(world, monkeypatch):
    """From its activation, then from where it last read: once an hour and right after a reconnect. A message the
    thread already has is not carried twice; Tico's own posts and joins are left out; an app's post is carried."""
    link_id = read_marketing(world)
    thread = link_row(world, link_id)["thread_id"]
    assert world.bridge.receive(chan_event(later(world, 1), "Live one")) == "posted"
    world.slack.pages[MARKETING] = [
        {"ts": later(world, 1), "user": "U1", "text": "Live one"},
        {"ts": later(world, 2), "user": "U2", "text": "Missed one"},
        {"ts": later(world, 3), "bot_id": "B0ALERTS", "username": "alerts", "text": "Disk full"},
        {"ts": later(world, 4), "user": BOT_USER, "bot_id": "B0BUNG973DY", "text": "Tico's own"},
        {"ts": later(world, 5), "user": "U2", "subtype": "channel_join", "text": "joined"},
        {"ts": later(world, 6), "user": "U1", "text": "Root", "reply_count": 1},
    ]
    reply = {"ts": later(world, 7), "user": "U2", "text": "A reply", "thread_ts": later(world, 6)}
    monkeypatch.setattr(world.slack, "replies", lambda channel, thread_ts, oldest="", limit=200: [reply])
    caught = world.bridge.tick()["caught_up"]
    assert caught == [{"link": link_id, "state": "done", "events": 5, "carried": "posted"}]
    bodies = {
        r["body"]: r["sender_id"] for r in all_rows(world, "SELECT body, sender_id FROM messages WHERE " + FROM_SLACK)
    }
    assert bodies == {
        "Live one": "human:ana",
        "Missed one": "human:ben",
        "*alerts*: Disk full": H.KEEPER,
        "Root": "human:ana",
        "A reply": "human:ben",
    }
    assert link_row(world, link_id)["read_ts"] == later(world, 7)
    assert one(world, "SELECT conversation_id FROM messages WHERE body='A reply'")["conversation_id"] != thread

    world.slack.pages[MARKETING].append({"ts": later(world, 8), "user": "U1", "text": "After the pass"})
    world.clock.advance(ST.CATCH_UP_CHECK + 1)
    assert world.bridge.tick()["caught_up"] == [], "once an hour"
    world.gw.bridge.catch_up_soon()  # the socket came back
    assert world.bridge.tick()["caught_up"][0]["events"] == 1
    assert len(all_rows(world, "SELECT id FROM messages WHERE body='After the pass'")) == 1
    read_to = link_row(world, link_id)["read_ts"]

    def down(*args):
        raise G.SlackUnreachable("conversations.history: no answer")

    monkeypatch.setattr(world.slack, "history_page", down)
    world.clock.advance(ST.CATCH_UP_SECONDS + 1)
    assert world.bridge.tick()["caught_up"] == [{"link": link_id, "state": "failed"}]
    assert link_row(world, link_id)["read_ts"] == read_to, "a failed pass reads from the same place next time"


def test_unread_slack_messages_before_the_migration_reach_the_readers_digest_after_it(world):
    """The readers' pass had read #marketing up to a cursor; the new read-only link starts there, so what came after
    is carried into the thread and counted in the reader's digest, and what was read before is not."""
    import importlib

    from backend import thread_bot_digest

    base = ST._epoch(world.clock())
    with world.hub.transaction() as c:
        c.execute("DROP INDEX IF EXISTS slack_thread_links_ref")
        c.execute("DROP INDEX IF EXISTS slack_thread_links_read_only")
        c.execute("ALTER TABLE slack_thread_links DROP COLUMN read_only")
        c.execute("ALTER TABLE slack_thread_links DROP COLUMN read_ts")
        c.execute("ALTER TABLE slack_channels ADD COLUMN readers TEXT NOT NULL DEFAULT '[]'")
        c.execute("ALTER TABLE slack_channels ADD COLUMN digest_hours REAL NOT NULL DEFAULT 0")
        c.execute("CREATE TABLE slack_reads (channel TEXT, reader TEXT, last_ts TEXT, PRIMARY KEY (channel, reader))")
        now = H.now()
        c.execute(
            "INSERT INTO slack_channels(key,channel_id,name,readers,post,note,digest_hours,created_at,updated_at,"
            "updated_by) VALUES(?,?,'marketing','[\"legal\"]',1,'',0,?,?,'human:ben')",
            (MARKETING, MARKETING, now, now),
        )
        c.execute("INSERT INTO slack_reads VALUES(?,?,?)", (MARKETING, "legal", f"{base - 50:.6f}"))
        importlib.import_module("backend.migrations.20261117104315_slack_readers_to_links").up(c)
    link = one(world, "SELECT id, read_ts FROM slack_thread_links")
    assert link["read_ts"] == f"{base - 50:.6f}"
    world.slack.pages[MARKETING] = [
        {"ts": f"{base - 60:.6f}", "user": "U1", "text": "Read before"},
        {"ts": f"{base - 40:.6f}", "user": "U1", "text": "Unread while it moved"},
        {"ts": f"{base - 30:.6f}", "user": "U2", "text": "Also unread"},
    ]
    assert gate(world, link["id"]) == "active"
    world.bridge.tick()
    carried = [r["body"] for r in all_rows(world, "SELECT body FROM messages WHERE " + FROM_SLACK + " ORDER BY rowid")]
    assert carried == ["Unread while it moved", "Also unread"]
    assert thread_bot_digest.sweep(world.hub) == 1
    assert ": 2" in one(world, "SELECT body FROM messages WHERE kind='notice' AND recipient_id='bot:legal'")["body"]


# ----------------------------------------------------------------------------- audit 23 (Slack): a terminal refusal surfaces once
def test_a_terminal_slack_refusal_files_one_needs_you_item_until_it_is_resolved(world, monkeypatch):
    link_id = activate_marketing(world)

    def refuse(channel, thread_ts, text, delivery_id):
        raise G.SlackError("chat.postMessage", "msg_too_long")

    monkeypatch.setattr(world.slack, "post_thread_message", refuse)
    send_as_ana(world, "first")
    send_as_ana(world, "second")
    world.bridge.outbound()
    assert [
        r["state"]
        for r in all_rows(
            world, "SELECT state FROM message_deliveries WHERE recipient LIKE 'slack:%' ORDER BY created_at"
        )
    ] == ["failed", "failed"]
    items = all_rows(world, "SELECT id FROM tasks WHERE title LIKE 'A message to Slack%'")
    assert len(items) == 1, "two refusals in one run file one item"
    assert link_row(world, link_id)["state"] == "active", "a message refusal does not suspend the channel"
    assert link_row(world, link_id)["failure_task_id"] == items[0]["id"]
    with world.hub.transaction() as c:
        item = H.task(c, items[0]["id"])
        E.move(c, item["id"], E.DONE, item["owner_id"], "Shortened the message")
    send_as_ana(world, "third")
    world.bridge.outbound()
    assert len(all_rows(world, "SELECT id FROM tasks WHERE title LIKE 'A message to Slack%'")) == 2


def _a_thread_the_admin_cannot_read_gives_the_same_404_as_a_missing_one(world):
    with pytest.raises(Problem) as hidden:
        ST.request_link(world.hub, OUTSIDER, MEMBERS, "#secret", auth=Auth(world.hub))
    with pytest.raises(Problem) as missing:
        ST.request_link(world.hub, OUTSIDER, "T-missing", "#marketing", auth=Auth(world.hub))
    assert hidden.value.status == missing.value.status == 404
    assert hidden.value.code == missing.value.code and hidden.value.detail == missing.value.detail


def _a_listing_leaves_out_the_links_to_threads_the_admin_cannot_read(world):
    link_id = request(world, "#secret", thread=MEMBERS)  # a members thread: Ana is a member, the outsider is not
    assert gate(world, link_id) == "active"
    assert [r["id"] for r in ST.list_links(world.hub, WHO, auth=Auth(world.hub))["links"]] == [link_id]
    assert ST.list_links(world.hub, OUTSIDER, auth=Auth(world.hub))["links"] == []
    with pytest.raises(Problem) as exc:
        ST.list_links(world.hub, OUTSIDER, MEMBERS, auth=Auth(world.hub))
    assert exc.value.status == 404


def _an_edit_and_a_delete_become_a_revision_and_a_tombstone(world, base):
    activate_marketing(world)
    world.bridge.receive(chan_event(ts(base + 1), "Kickoff"))
    assert world.bridge.receive(chan_edit(ts(base + 1), "Kickoff at 11")) == "edited"
    assert origin_message(world, ts(base + 1))["body"] == "Kickoff at 11"
    assert origin_message(world, ts(base + 1))["revision"] == 1
    assert world.bridge.receive(chan_delete(ts(base + 1))) == "deleted"
    assert origin_message(world, ts(base + 1))["deleted_at"] is not None


def _an_edit_to_the_same_text_is_not_a_revision(world, base):
    activate_marketing(world)
    world.bridge.receive(chan_event(ts(base + 1), "Kickoff"))
    unfurl = dict(chan_edit(ts(base + 1), "Kickoff"), edited_ts=ts(base + 3))
    assert world.bridge.receive(unfurl) == "unchanged"
    assert origin_message(world, ts(base + 1))["revision"] == 0


def _a_duplicate_edit_event_is_one_revision(world, base):
    activate_marketing(world)
    world.bridge.receive(chan_event(ts(base + 1), "Kickoff"))
    first = chan_edit(ts(base + 1), "Kickoff at 11")
    first["edited_ts"] = ts(base + 5)
    assert world.bridge.receive(first) == "edited"
    assert world.bridge.receive(dict(first, event_id="Ed-retry")) == "duplicate"
    assert origin_message(world, ts(base + 1))["revision"] == 1
    assert (
        len(
            all_rows(
                world,
                "SELECT revision FROM message_revisions WHERE origin_id=?",
                origin_message(world, ts(base + 1))["origin_id"],
            )
        )
        == 2
    )


def _an_older_edit_arriving_after_a_newer_one_is_stale_and_changes_nothing(world, base):
    activate_marketing(world)
    world.bridge.receive(chan_event(ts(base + 1), "Kickoff"))
    newer = dict(chan_edit(ts(base + 1), "Kickoff at 12"), edited_ts=ts(base + 5))
    older = dict(chan_edit(ts(base + 1), "Kickoff at 11"), edited_ts=ts(base + 3), event_id="Ed-late")
    assert world.bridge.receive(newer) == "edited"
    assert world.bridge.receive(older) == "stale"
    assert origin_message(world, ts(base + 1))["body"] == "Kickoff at 12"
    assert origin_message(world, ts(base + 1))["revision"] == 1


def _history_is_off_unless_asked_for(world):
    link_id = activate_marketing(world)
    world.slack.pages[MARKETING] = [{"ts": f"{time.time() - 100:.6f}", "user": "U1", "text": "old news"}]
    world.clock.advance(61)
    world.bridge.tick()
    assert link_row(world, link_id)["history_state"] == "off"
    assert all_rows(world, "SELECT id FROM messages") == []


def _history_is_opt_in_and_carries_only_mapped_members_from_the_last_thirty_days(world):
    now = time.time()
    world.slack.members[MARKETING] = ["U1", "U2", BOT_USER]
    world.slack.pages[MARKETING] = [
        {"ts": f"{now - 3600:.6f}", "user": "U1", "text": "Kickoff today"},
        {"ts": f"{now - 3000:.6f}", "user": "U4", "text": "from someone not on the roster"},
        {"ts": f"{now - 2500:.6f}", "user": "U2", "text": "BLOCKME: held"},
        {"ts": f"{now - 2000:.6f}", "user": "U2", "bot_id": "B1", "text": "a bot"},
        {"ts": f"{now - 1000:.6f}", "user": "U2", "text": "Sounds good"},
        {"ts": f"{now - 40 * 86400:.6f}", "user": "U1", "text": "too old"},
    ]
    link_id = request(world, "#marketing", history=True)
    assert gate(world, link_id) == "active"
    world.clock.advance(61)
    world.bridge.tick()
    link = link_row(world, link_id)
    assert link["history_state"] == "done" and link["history_count"] == 2
    carried = all_rows(world, "SELECT sender_id, body, refs FROM messages ORDER BY created_at")
    assert [m["body"] for m in carried] == ["Kickoff today", "Sounds good"]
    assert [m["sender_id"] for m in carried] == ["human:ana", "human:ben"]
    assert all(json.loads(m["refs"])["import"] is True for m in carried)


def _history_stops_at_the_cap(world, monkeypatch):
    monkeypatch.setattr(ST, "HISTORY_CAP", 1)
    now = time.time()
    world.slack.members[MARKETING] = ["U1", BOT_USER]
    world.slack.pages[MARKETING] = [
        {"ts": f"{now - 500:.6f}", "user": "U1", "text": "first"},
        {"ts": f"{now - 400:.6f}", "user": "U1", "text": "second"},
    ]
    link_id = request(world, "#marketing", history=True)
    gate(world, link_id)
    world.clock.advance(61)
    world.bridge.tick()
    assert link_row(world, link_id)["history_count"] == 1
    assert [m["body"] for m in all_rows(world, "SELECT body FROM messages WHERE body IN ('first', 'second')")] == [
        "first"
    ]


# ----------------------------------------------------------------------------- stories: one app for several checks
def _unlink_every_link(world):
    """Stop what the last check linked, so the next check links its own channel from a clean start."""
    for row in all_rows(world, "SELECT id FROM slack_thread_links WHERE state != 'unlinked'"):
        ST.unlink_link(world.hub, WHO, row["id"])


def test_the_admin_cannot_read_a_thread_story(world, subtests):
    """A thread the admin cannot read is refused and listed like a missing one; the listing shows what the admin can."""
    for check in (
        _a_thread_the_admin_cannot_read_gives_the_same_404_as_a_missing_one,
        _a_listing_leaves_out_the_links_to_threads_the_admin_cannot_read,
    ):
        with subtests.test(check.__name__):
            check(world)


def test_an_edit_story_one_revision_a_tombstone_and_stale_edits(world, subtests):
    """Edits to one channel message in one app: a delete is a tombstone, an edit to the same text is no revision, a
    duplicate edit event is one revision, and an older edit after a newer one changes nothing. Each check has its own
    stamps and link."""
    checks = (
        (_an_edit_and_a_delete_become_a_revision_and_a_tombstone, 0),
        (_an_edit_to_the_same_text_is_not_a_revision, 100),
        (_a_duplicate_edit_event_is_one_revision, 200),
        (_an_older_edit_arriving_after_a_newer_one_is_stale_and_changes_nothing, 300),
    )
    for number, (check, base) in enumerate(checks):
        if number:
            _unlink_every_link(world)
        with subtests.test(check.__name__):
            check(world, base)


def test_a_history_story_is_off_opt_in_and_capped(world, monkeypatch, subtests):
    """History import in one app: off unless asked for, opt-in carries only mapped members of the last thirty days, and
    it stops at the cap. Each check links the channel from a clean start."""
    checks = (
        _history_is_off_unless_asked_for,
        _history_is_opt_in_and_carries_only_mapped_members_from_the_last_thirty_days,
        _history_stops_at_the_cap,
    )
    for number, check in enumerate(checks):
        if number:
            _unlink_every_link(world)
        with subtests.test(check.__name__):
            if check is _history_stops_at_the_cap:
                check(world, monkeypatch)
            else:
                check(world)


# ----------------------------------------------------------------------------- loop failures: the class and a scrubbed message
def locked(*_args, **_kwargs):
    raise sqlite3.OperationalError("database is locked (key sk-testkey-000000000000)")


LOCKED = "OperationalError: database is locked (key [redacted])"


def test_a_message_whose_carry_in_fails_stays_queued_and_the_failure_is_logged_scrubbed(world, monkeypatch, caplog):
    activate_marketing(world)
    monkeypatch.setattr(world.bridge, "_process", locked)
    with caplog.at_level(logging.ERROR, logger="tico.slack.threads"):
        assert world.bridge.receive(chan_event(ts(1), "Kickoff")) == "queued"
    assert f"Slack inbound carry failed: {LOCKED}" in caplog.text
    assert len(all_rows(world, "SELECT seq FROM slack_inbound_events")) == 1


def test_a_queue_pass_that_fails_for_a_link_is_logged_scrubbed_and_reports_an_error(world, monkeypatch, caplog):
    activate_marketing(world)
    monkeypatch.setattr(world.bridge, "_process", locked)
    world.bridge.receive(chan_event(ts(1), "Kickoff"))  # queued: its carry-in fails too, logged on arrival
    with caplog.at_level(logging.ERROR, logger="tico.slack.threads"):
        assert world.bridge.drain_all() == ["error"]
    assert f"Slack queue for a link failed: {LOCKED}" in caplog.text
    assert len(all_rows(world, "SELECT seq FROM slack_inbound_events")) == 1


def test_a_delivery_that_fails_is_logged_scrubbed_and_the_next_one_still_goes_out(world, monkeypatch, caplog):
    activate_marketing(world)
    send_as_ana(world, "first")
    send_as_ana(world, "second")
    real_send_one, tried = world.bridge.send_one, []

    def fail_the_first(link, delivery):
        tried.append(delivery["id"])
        if len(tried) == 1:
            locked()
        return real_send_one(link, delivery)

    monkeypatch.setattr(world.bridge, "send_one", fail_the_first)
    with caplog.at_level(logging.ERROR, logger="tico.slack.threads"):
        out = world.bridge.outbound()
    assert f"Slack delivery {tried[0]} failed: {LOCKED}" in caplog.text
    assert {"delivery_id": tried[0], "state": "error"} in out
    assert [p["text"] for p in world.slack.posts] == ["*Ana Rivera* · via Tico\nsecond"]


def test_a_history_import_that_fails_is_logged_scrubbed_and_left_to_be_tried_again(world, monkeypatch, caplog):
    link_id = request(world, "#marketing", history=True)
    assert gate(world, link_id) == "active"
    monkeypatch.setattr(world.bridge, "import_history", locked)
    world.clock.advance(61)
    with caplog.at_level(logging.ERROR, logger="tico.slack.threads"):
        world.bridge.tick()
    assert f"Slack history import failed: {LOCKED}" in caplog.text
    assert link_row(world, link_id)["history_state"] == "pending"
