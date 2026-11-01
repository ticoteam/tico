"""The Slack bridge for native threads (`backend/slack_threads.py`, `_threads-plan.md` §5 and §9).

Only Slack is faked (FakeSlack), and the screening door. Core is the real `backend/threads.py`, and the org rule check
is the real one with the test judge from test_org_rule_check.
"""

import copy
import json
import time
from types import SimpleNamespace

import pytest

from backend import hubdb as H
from backend import screening
from backend import slack_gateway as G
from backend import slack_threads as ST
from backend import threads as TH
from backend.auth import Auth, Identity
from backend.config import Settings
from backend.store import Problem, Store, encode

TEAM, APP, BOT_USER, BOT_ID = "T02F3S2SD", "A0BU75YSGT1", "U0BUS6WA4SY", "B0BUNG973DY"
MARKETING, PRIVATE, LOUNGE, PARTNERS = "C0000000001", "G0000000001", "C0000000002", "C0000000003"
COMPANY, MEMBERS = "T-company", "T-members"
WHO = Identity("human:ana", "human", email="ana@acme.example")
OWNER = Identity("human:ben", "owner", email="ben@acme.example")   # a rule author other than the sender
PEOPLE = [
    {"id": "ana", "name": "Ana Rivera", "email": "ana@acme.example", "team": "leadership", "primary_for": ["*"]},
    {"id": "ben", "name": "Ben", "email": "ben@acme.example", "team": "product", "primary_for": ["product"]},
    # On the roster, but not admitted by hub-access (no acme.example address).
    {"id": "contractor", "name": "A Contractor", "email": "contractor@example.com", "team": ""},
]
BOTS = {
    "coo": {"display_name": "Tico", "description": "The assistant.", "reports_to": None, "status": "active"},
    "legal": {"display_name": "Legal", "description": "Contracts, terms and legal risk.", "reports_to": None, "status": "active"},
}
USERS = {
    "U1": {"id": "U1", "team_id": TEAM, "name": "ana", "profile": {"email": "ana@acme.example"}},
    "U2": {"id": "U2", "team_id": TEAM, "name": "ben", "profile": {"email": "ben@acme.example"}},
    "U3": {"id": "U3", "team_id": TEAM, "name": "contractor", "profile": {"email": "contractor@example.com"}},
    # A workspace member with an acme.example address who is not on the roster.
    "U4": {"id": "U4", "team_id": TEAM, "name": "newhire", "profile": {"email": "newhire@acme.example"}},
}
CHANNELS = {
    MARKETING: {"id": MARKETING, "name": "marketing", "is_member": True, "is_private": False},
    PRIVATE: {"id": PRIVATE, "name": "secret", "is_member": True, "is_private": True},
    LOUNGE: {"id": LOUNGE, "name": "lounge", "is_member": True, "is_private": False},
    PARTNERS: {"id": PARTNERS, "name": "partners", "is_member": True, "is_private": False, "is_ext_shared": True},
}
CHANNEL_MEMBERS = {MARKETING: ["U1", "U2", BOT_USER], PRIVATE: ["U1"], LOUNGE: ["U1", "U3"], PARTNERS: ["U1"]}
class FakeSlack:
    """The Web API as the bridge sees it: canned users, channels and members, and every post, update and note."""

    def __init__(self):
        self.channels, self.members = copy.deepcopy(CHANNELS), copy.deepcopy(CHANNEL_MEMBERS)
        self.pages = {}                             # channel -> top-level history
        self.posts, self.updates, self.deletes, self.ephemeral = [], [], [], []
        self.lose = []                              # per send: None, "before" (Slack never got it), "after" (it did)

    def auth_test(self):
        return {"ok": True, "team_id": TEAM, "user_id": BOT_USER, "bot_id": BOT_ID, "url": "https://acme.slack.com/"}

    def bots_info(self, bot_id):
        return {"id": bot_id, "app_id": APP, "user_id": BOT_USER, "name": "Tico"}

    def users_info(self, user_id):
        if user_id not in USERS:
            raise G.SlackError("users.info", "user_not_found")
        return USERS[user_id]

    def conversations_info(self, channel):
        if channel not in self.channels:
            raise G.SlackError("conversations.info", "channel_not_found")
        return self.channels[channel]

    def conversations_list(self):
        return list(self.channels.values())

    def conversations_members(self, channel):
        return list(self.members.get(channel, []))

    def history_page(self, channel, oldest="", cursor="", limit=200):
        return [m for m in self.pages.get(channel, []) if float(m["ts"]) > float(oldest or 0)][:limit], ""

    def replies(self, channel, thread_ts, oldest="", limit=200):
        return []

    def history_metadata(self, channel, oldest):
        return [m for m in self.posts if m["channel"] == channel]

    def post_thread_message(self, channel, thread_ts, text, delivery_id):
        mode = self.lose.pop(0) if self.lose else None
        if mode == "before":
            raise G.SlackUnreachable("lost before Slack got it")
        ts = f"{1700100000 + len(self.posts)}.000200"
        self.posts.append({"channel": channel, "thread_ts": thread_ts, "text": text, "ts": ts,
                           "metadata": {"event_type": "tico_thread_message", "event_payload": {"delivery_id": delivery_id}}})
        if mode == "after":
            raise G.SlackUnreachable("the reply was lost")
        return {"ok": True, "ts": ts}

    def update_message(self, channel, ts, text):
        self.updates.append((channel, ts, text))
        return {"ok": True}

    def delete_message(self, channel, ts):
        self.deletes.append((channel, ts))
        return {"ok": True}

    def post_ephemeral(self, channel, user, text):
        self.ephemeral.append((channel, user, text))
        return {"ok": True}


class FakeJudge:
    """The judge is not asked in these tests; the gateway only needs an engine to hold."""

    def __call__(self, state, questions, label=None):
        raise AssertionError("the judge was asked")


class Clock:
    def __init__(self):
        self.now = H.now()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now = H.shift(self.now, seconds=seconds)


@pytest.fixture
def hub(tmp_path):
    registry = tmp_path / "hub-registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text("owner: ana@acme.example\nallowed_domains: [acme.example]\n")
    settings = Settings(db_path=tmp_path / "hub.db", slack_team_id=TEAM, slack_app_id=APP, registry_dir=registry,
                        assistant_name="Tico")
    store = Store(settings)
    store.initialize()
    with store.transaction() as c:
        H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", **entry} for slug, entry in BOTS.items()},
                        {"people": PEOPLE})
        c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({
            "default_user": "ana", "teams": {}, "people": PEOPLE}),))
        for slug, entry in BOTS.items():
            c.execute("INSERT INTO bot_config(bot,config_json,team,operator,description,reports_to) VALUES(?,?,?,?,?,?)",
                      (slug, encode({"name": slug, "runtime": "fake"}), None, "ana", entry["description"], entry["reports_to"]))
        now = H.now()
        for thread_id, audience, subject in ((COMPANY, "company", "Marketing"), (MEMBERS, "members", "Plans")):
            c.execute("INSERT INTO conversations (id,kind,subject,created,audience,root_id,path) VALUES (?,?,?,?,?,?,'')",
                      (thread_id, "thread", subject, now, audience, thread_id))
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
    return {"op": "store", "event_id": f"Ev{stamp}", "team_id": TEAM, "api_app_id": APP, "channel": MARKETING,
            "channel_kind": "channel", "ts": stamp, "thread_ts": thread_ts or stamp, "reply_ts": thread_ts or stamp,
            "user_id": user, "author": "human", "author_name": None, "event_type": "message.channels", "text": text}


def chan_edit(stamp, text, user="U1"):
    return {"op": "edit", "event_id": f"Ed{stamp}", "team_id": TEAM, "api_app_id": APP, "channel": MARKETING, "ts": stamp,
            "thread_ts": stamp, "reply_ts": stamp, "user_id": user, "author": "human", "event_type": "message.channels",
            "text": text}


def chan_delete(stamp, user="U1"):
    return {"op": "delete", "event_id": f"Ed{stamp}", "team_id": TEAM, "api_app_id": APP, "channel": MARKETING, "ts": stamp,
            "thread_ts": stamp, "reply_ts": stamp, "user_id": user, "author": "human", "event_type": "message.channels"}


def send_as_ana(world, text, parent=None, thread=COMPANY):
    with world.hub.transaction() as c:
        return TH.post(c, WHO, thread, text, kind="say", refs={}, parent_message_id=parent, via="assistant")["id"]


def outside_members(world, thread_id=COMPANY):
    return [r["actor"] for r in all_rows(world, "SELECT actor FROM thread_members WHERE thread_id=? "
                                               "AND actor LIKE 'slack:%'", thread_id)]


def origin_message(world, stamp):
    return one(world, "SELECT m.id, m.body, m.revision, m.deleted_at, m.origin_id, m.conversation_id FROM messages m "
                      "JOIN slack_message_links l ON l.message_id = m.id WHERE l.channel_id=? AND l.ts=? AND l.direction='in'",
               MARKETING, stamp)


# ----------------------------------------------------------------------------- linking
def test_a_link_is_requested_for_one_root_thread_at_a_time(world):
    with world.hub.transaction() as c:
        c.execute("INSERT INTO conversations (id,kind,subject,created,audience,root_id,parent_id,parent_message_id,path) "
                  "VALUES ('T-reply','thread','Reply','x','company',?,?,'m1','')", (COMPANY, COMPANY))
        c.execute("INSERT INTO conversations (id,kind,subject,created,audience,root_id,path) "
                  "VALUES ('T-own','thread','Own','x','personal','T-own','')")
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


@pytest.mark.parametrize("ref, thread, why", [
    ("#marketing", COMPANY, None),
    ("#marketing", MEMBERS, "company thread"),       # a public channel needs a company thread
    ("#secret", COMPANY, "members thread"),          # a private channel needs a members thread
    ("#partners", COMPANY, "Slack Connect"),         # a shared channel is refused
    ("#lounge", COMPANY, "don't map"),               # a member who is not admitted does not map to a roster person
    ("#ghost", COMPANY, "No channel named"),
])
def test_a_channel_links_only_when_its_audience_and_every_member_allow_it(world, ref, thread, why):
    link_id = request(world, ref, thread=thread)
    state = gate(world, link_id)
    if why is None:
        assert state == "active"
        assert link_row(world, link_id)["channel_id"] == MARKETING
        assert outside_members(world) == [f"slack:{MARKETING}"]
        assert [m["person_id"] for m in all_rows(world, "SELECT person_id FROM slack_link_members WHERE link_id=? "
                                                        "ORDER BY person_id", link_id)] == ["ana", "ben"]
    else:
        assert state == "refused"
        assert why in link_row(world, link_id)["reason"]
        assert outside_members(world) == []


# ----------------------------------------------------------------------------- suspension
@pytest.mark.parametrize("change", ["slack connect", "unmapped member", "thread narrows"])
def test_a_link_that_stops_fitting_is_suspended_with_one_needs_you_item(world, change):
    link_id = activate_marketing(world, members=("U1",))
    if change == "slack connect":
        world.slack.channels[MARKETING]["is_ext_shared"] = True
    elif change == "unmapped member":
        world.slack.members[MARKETING].append("U3")
    else:
        with world.hub.transaction() as c:
            c.execute("UPDATE conversations SET audience='members' WHERE id=?", (COMPANY,))
    world.clock.advance(ST.CHECK_SECONDS + 1)
    world.bridge.check_links()
    row = link_row(world, link_id)
    assert row["state"] == "suspended"
    task = one(world, "SELECT id, title FROM tasks WHERE id=?", row["needs_task_id"])
    assert "stopped" in task["title"]


def test_a_second_break_reuses_the_open_needs_you_item(world):
    link_id = activate_marketing(world, members=("U1",))
    world.slack.channels[MARKETING]["is_ext_shared"] = True
    world.clock.advance(ST.CHECK_SECONDS + 1)
    world.bridge.check_links()
    first = link_row(world, link_id)["needs_task_id"]
    del world.slack.channels[MARKETING]["is_ext_shared"]
    ST.resume_link(world.hub, WHO, link_id)
    assert gate(world, link_id) == "active"
    world.slack.channels[MARKETING]["is_ext_shared"] = True
    world.clock.advance(ST.CHECK_SECONDS + 1)
    world.bridge.check_links()
    assert link_row(world, link_id)["needs_task_id"] == first
    assert len(all_rows(world, "SELECT id FROM tasks WHERE title LIKE 'Slack channel #marketing%'")) == 1


# ----------------------------------------------------------------------------- inbound
def test_a_channel_message_goes_through_the_gateway_as_the_mapped_person_and_never_the_readers_pass(world):
    activate_marketing(world)
    envelope = {"type": "event_callback", "team_id": TEAM, "api_app_id": APP, "event_id": "Ev-kickoff",
                "event": {"type": "message", "channel": MARKETING, "channel_type": "channel", "user": "U1",
                          "ts": ts(1), "text": "Kickoff is at 10"}}
    assert world.gw.receive(envelope) == "posted"
    assert all_rows(world, "SELECT event_id FROM slack_events") == []
    msg = one(world, "SELECT from_actor, body, refs_json, origin_id FROM messages")
    assert msg["from_actor"] == "human:ana" and msg["body"] == "Kickoff is at 10"
    refs = json.loads(msg["refs_json"])
    assert refs["slack"] == {"channel": MARKETING, "ts": ts(1), "team": TEAM}
    assert msg["origin_id"].endswith(f"@slack:{TEAM}")
    assert "slack" in world.screened


def test_a_slack_thread_reply_is_a_sub_thread_of_the_message_it_answers(world):
    activate_marketing(world)
    world.bridge.receive(chan_event(ts(1), "Kickoff"))
    root = origin_message(world, ts(1))
    assert world.bridge.receive(chan_event(ts(2), "Re: kickoff", user="U2", thread_ts=ts(1))) == "posted"
    reply = one(world, "SELECT m.conversation_id, c.parent_message_id FROM messages m JOIN conversations c "
                       "ON c.id = m.conversation_id WHERE m.body = 'Re: kickoff'")
    assert reply["parent_message_id"] == root["id"]


def test_an_edit_and_a_delete_become_a_revision_and_a_tombstone(world):
    activate_marketing(world)
    world.bridge.receive(chan_event(ts(1), "Kickoff"))
    assert world.bridge.receive(chan_edit(ts(1), "Kickoff at 11")) == "edited"
    assert origin_message(world, ts(1))["body"] == "Kickoff at 11"
    assert origin_message(world, ts(1))["revision"] == 1
    assert world.bridge.receive(chan_delete(ts(1))) == "deleted"
    assert origin_message(world, ts(1))["deleted_at"] is not None


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
    with world.hub.transaction() as c:                           # the reply's own sub-thread has a title
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
def test_history_is_opt_in_and_carries_only_mapped_members_from_the_last_thirty_days(world):
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
    carried = all_rows(world, "SELECT from_actor, body, refs_json FROM messages ORDER BY created")
    assert [m["body"] for m in carried] == ["Kickoff today", "Sounds good"]
    assert [m["from_actor"] for m in carried] == ["human:ana", "human:ben"]
    assert all(json.loads(m["refs_json"])["import"] is True for m in carried)


def test_history_is_off_unless_asked_for(world):
    link_id = activate_marketing(world)
    world.slack.pages[MARKETING] = [{"ts": f"{time.time() - 100:.6f}", "user": "U1", "text": "old news"}]
    world.clock.advance(61)
    world.bridge.tick()
    assert link_row(world, link_id)["history_state"] == "off"
    assert all_rows(world, "SELECT id FROM messages") == []


def test_history_stops_at_the_cap(world, monkeypatch):
    monkeypatch.setattr(ST, "HISTORY_CAP", 1)
    now = time.time()
    world.slack.members[MARKETING] = ["U1", BOT_USER]
    world.slack.pages[MARKETING] = [{"ts": f"{now - 500:.6f}", "user": "U1", "text": "first"},
                                    {"ts": f"{now - 400:.6f}", "user": "U1", "text": "second"}]
    link_id = request(world, "#marketing", history=True)
    gate(world, link_id)
    world.clock.advance(61)
    world.bridge.tick()
    assert link_row(world, link_id)["history_count"] == 1
    assert [m["body"] for m in all_rows(world, "SELECT body FROM messages")] == ["first"]


# ----------------------------------------------------------------------------- inbound: duplicates, queue, cache, rules
def test_a_duplicate_edit_event_is_one_revision(world):
    activate_marketing(world)
    world.bridge.receive(chan_event(ts(1), "Kickoff"))
    first = chan_edit(ts(1), "Kickoff at 11")
    first["edited_ts"] = ts(5)
    assert world.bridge.receive(first) == "edited"
    assert world.bridge.receive(dict(first, event_id="Ed-retry")) == "duplicate"
    assert origin_message(world, ts(1))["revision"] == 1
    assert len(all_rows(world, "SELECT revision FROM message_revisions WHERE origin_id=?",
                        origin_message(world, ts(1))["origin_id"])) == 2


def test_an_edit_to_the_same_text_is_not_a_revision(world):
    activate_marketing(world)
    world.bridge.receive(chan_event(ts(1), "Kickoff"))
    unfurl = dict(chan_edit(ts(1), "Kickoff"), edited_ts=ts(3))
    assert world.bridge.receive(unfurl) == "unchanged"
    assert origin_message(world, ts(1))["revision"] == 0


def test_messages_that_arrive_while_suspended_are_posted_on_resume_in_order(world):
    link_id = activate_marketing(world, members=("U1",))
    world.slack.members[MARKETING].append("U3")                  # an unmapped member: the link suspends
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
    assert [m["body"] for m in all_rows(world, "SELECT body FROM messages WHERE body LIKE '%while suspended%' "
                                               "ORDER BY seq")] == [
        "first while suspended", "second while suspended"]
    assert all_rows(world, "SELECT seq FROM slack_inbound_queue") == []


def test_users_info_is_asked_once_an_hour_per_user(world, monkeypatch):
    activate_marketing(world)
    asked = []
    real = world.slack.users_info
    monkeypatch.setattr(world.slack, "users_info", lambda uid: asked.append(uid) or real(uid))
    world.bridge.receive(chan_event(ts(1), "one"))
    world.bridge.receive(chan_event(ts(2), "two"))
    assert asked.count("U1") == 0                                 # linking asked already, within the hour
    world.clock.advance(ST.USER_CACHE_SECONDS + 1)
    world.bridge.receive(chan_event(ts(3), "three"))
    assert asked.count("U1") == 1


def rule_world(world):
    """A rule in force that every person's message breaks when it says the favorite color: the test judge answers."""
    from backend.tests.test_org_rule_check import COLOR, Judge
    from backend import org_rules as R
    with world.hub.transaction() as c:
        R.add_rule(c, Auth(world.hub), OWNER, {"kind": "internal", "text": COLOR, "who": {"everyone": True},
                                             "if_broken": "block"})
    world.gw._judge = Judge(world.hub)
    return COLOR


def test_a_rule_break_holds_the_slack_message_as_its_person_with_its_provenance(world):
    activate_marketing(world)
    rule_world(world)
    assert world.bridge.receive(chan_event(ts(1), "The favorite color is teal")) == "held"
    assert all_rows(world, "SELECT id FROM messages") == []
    hold = one(world, "SELECT * FROM org_rule_holds")
    assert hold["author"] == "human:ana"
    provenance = json.loads(hold["payload_json"])["provenance"]
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
    carried = one(world, "SELECT m.from_actor, m.body FROM messages m JOIN slack_message_links l ON l.message_id = m.id "
                         "WHERE l.ts = ?", ts(1))
    assert carried["from_actor"] == "human:ana" and carried["body"] == "The favorite color is teal"
