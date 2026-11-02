"""The Slack bridge for native threads (`backend/slack_threads.py`, `_threads-plan.md` §5 and §9).

Only Slack is faked (FakeSlack), and the screening door. Core is the real `backend/threads.py`, and the org rule check
is the real one with the test judge from test_org_rule_check.
"""

import copy
import json
import time
from types import SimpleNamespace

import pytest

from backend import federation_threads as FT
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
        self.page_size = 200                        # the metadata lookups' page (a test makes it small to page)
        self.on_post = None                         # a hook run as a post lands (a test revises the message mid-send)
        self.down = False                           # True: Slack cannot be reached for the channel's check

    def auth_test(self):
        return {"ok": True, "team_id": TEAM, "user_id": BOT_USER, "bot_id": BOT_ID, "url": "https://acme.slack.com/"}

    def bots_info(self, bot_id):
        return {"id": bot_id, "app_id": APP, "user_id": BOT_USER, "name": "Tico"}

    def users_info(self, user_id):
        if user_id not in USERS:
            raise G.SlackError("users.info", "user_not_found")
        return USERS[user_id]

    def conversations_info(self, channel):
        if self.down:
            raise G.SlackUnreachable("conversations.info: no answer")
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

    def _page(self, rows, cursor):
        start = int(cursor or 0)
        end = start + self.page_size
        return rows[start:end], (str(end) if end < len(rows) else "")

    def history_metadata_page(self, channel, oldest, cursor=""):
        """Top-level messages only, as conversations.history gives them, with their metadata."""
        rows = [m for m in self.posts if m["channel"] == channel and not m["thread_ts"] and float(m["ts"]) > float(oldest or 0)]
        return self._page(rows, cursor)

    def replies_metadata_page(self, channel, thread_ts, oldest, cursor=""):
        """The replies in one thread, as conversations.replies gives them (the root is not among them)."""
        rows = [m for m in self.posts if m["channel"] == channel and m["thread_ts"] == thread_ts
                and float(m["ts"]) > float(oldest or 0)]
        return self._page(rows, cursor)

    def add_post(self, channel, thread_ts, text, delivery_id):
        """A message as Slack keeps it: the ts is the time it was made, so the reconcile window sees it."""
        ts = f"{time.time() + len(self.posts) * 0.001:.6f}"
        self.posts.append({"channel": channel, "thread_ts": thread_ts, "text": text, "ts": ts,
                           "metadata": {"event_type": "tico_thread_message", "event_payload": {"delivery_id": delivery_id}}})
        return ts

    def post_thread_message(self, channel, thread_ts, text, delivery_id):
        mode = self.lose.pop(0) if self.lose else None
        if mode == "before":
            raise G.SlackUnreachable("lost before Slack got it")
        ts = self.add_post(channel, thread_ts, text, delivery_id)
        if self.on_post:
            self.on_post(channel, text)
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
        # A members thread is read by its members: Ana is one, so the admin can see it and link it.
        c.execute("INSERT INTO thread_members (id,thread_id,actor,role,added_by,created_at,updated_at) "
                  "VALUES ('m-members-ana',?,'human:ana','member','human:ben',?,?)", (MEMBERS, now, now))
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
        c.execute("INSERT INTO thread_members (id,thread_id,actor,role,added_by,created_at,updated_at) "
                  "VALUES ('m-own-ana','T-own','human:ana','member','human:ana','x','x')")
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


def rule_world(world, with_scope=None):
    """A rule in force that every person's message breaks when it says the favorite color: the test judge answers.
    `with_scope` narrows it to the people it names (a With rule)."""
    from backend.tests.test_org_rule_check import COLOR, Judge
    from backend import org_rules as R
    with world.hub.transaction() as c:
        R.add_rule(c, Auth(world.hub), OWNER, {"kind": "internal", "text": COLOR, "who": {"everyone": True},
                                             "with": with_scope or {}, "if_broken": "block"})
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


# ----------------------------------------------------------------------------- P1-1: revoked access stops sends
def test_a_narrowed_thread_stops_the_next_send_before_the_slack_check_runs_again(world):
    link_id = activate_marketing(world)
    send_as_ana(world, "Sent while allowed")
    world.bridge.outbound()                      # the channel's Slack side is trusted for SEND_CHECK_SECONDS from here
    assert len(world.slack.posts) == 1
    send_as_ana(world, "Written after the change")
    with world.hub.transaction() as c:
        c.execute("UPDATE conversations SET audience='members' WHERE id=?", (COMPANY,))
    out = world.bridge.outbound()
    assert [r["state"] for r in out] == ["suspended"]
    assert len(world.slack.posts) == 1
    assert link_row(world, link_id)["state"] == "suspended"
    assert [d["state"] for d in all_rows(world, "SELECT state FROM message_deliveries WHERE state='pending' AND recipient=?",
                                         f"slack:{MARKETING}")] == ["pending"], "the message written after the change waits"


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
        TH.edit_message(c, WHO, message_id, "Shared then withdrawn, edited")   # the edit makes Tico look at it again
        c.execute("UPDATE messages SET restricted=1, legacy_audience='shared:legal' WHERE id=?", (message_id,))
    world.bridge.outbound()
    assert world.slack.deletes == [(MARKETING, sent_ts)]
    assert world.slack.updates == []


# ----------------------------------------------------------------------------- P1-3: one ingress for linked channels
def test_an_app_mention_in_a_linked_channel_is_carried_once_and_its_message_form_is_a_duplicate(world):
    activate_marketing(world)
    mention = {"type": "event_callback", "team_id": TEAM, "api_app_id": APP, "event_id": "Ev-mention-1",
               "event": {"type": "app_mention", "channel": MARKETING, "user": "U1", "ts": ts(1),
                         "text": f"<@{BOT_USER}> kickoff is at 10?"}}
    assert world.gw.receive(mention) == "posted"
    assert all_rows(world, "SELECT event_id FROM slack_events") == [], "a linked channel's mention never reaches the readers' pass"
    # The mention also wakes Tico: core's notice ('human:ana in this thread: ...') is written beside the message.
    [carried] = all_rows(world, "SELECT body FROM messages WHERE kind='say'")
    assert "kickoff is at 10?" in carried["body"] and "<@" not in carried["body"]
    assert len(all_rows(world, "SELECT id FROM messages WHERE body LIKE '%kickoff is at 10?%'")) == 2   # message + notice
    message = {"type": "event_callback", "team_id": TEAM, "api_app_id": APP, "event_id": "Ev-message-1",
               "event": {"type": "message", "channel": MARKETING, "channel_type": "channel", "user": "U1", "ts": ts(1),
                         "text": f"<@{BOT_USER}> kickoff is at 10?"}}
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
    activate_marketing(world)                    # Ana's message's audience is Ben, who maps and reads the thread
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
    assert len(all_rows(world, "SELECT seq FROM slack_inbound_queue")) == 1
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
        c.execute("INSERT INTO conversations (id,kind,subject,created,audience,root_id,path) "
                  "VALUES ('T-sales','thread','Sales','x','company','T-sales','')")
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


def test_a_thread_the_admin_cannot_read_gives_the_same_404_as_a_missing_one(world):
    with pytest.raises(Problem) as hidden:
        ST.request_link(world.hub, OUTSIDER, MEMBERS, "#secret", auth=Auth(world.hub))
    with pytest.raises(Problem) as missing:
        ST.request_link(world.hub, OUTSIDER, "T-missing", "#marketing", auth=Auth(world.hub))
    assert hidden.value.status == missing.value.status == 404
    assert hidden.value.code == missing.value.code and hidden.value.detail == missing.value.detail


def test_a_listing_leaves_out_the_links_to_threads_the_admin_cannot_read(world):
    link_id = request(world, "#secret", thread=MEMBERS)      # a members thread: Ana is a member, the outsider is not
    assert gate(world, link_id) == "active"
    assert [r["id"] for r in ST.list_links(world.hub, WHO, auth=Auth(world.hub))["links"]] == [link_id]
    assert ST.list_links(world.hub, OUTSIDER, auth=Auth(world.hub))["links"] == []
    with pytest.raises(Problem) as exc:
        ST.list_links(world.hub, OUTSIDER, MEMBERS, auth=Auth(world.hub))
    assert exc.value.status == 404


# ----------------------------------------------------------------------------- P2-9: edits without a Slack user; notes
def test_a_held_edit_without_a_slack_user_is_noted_to_its_author_and_does_not_crash(world):
    activate_marketing(world)
    assert world.bridge.receive(chan_event(ts(1), "BLOCKME: the plan")) == "held"
    edit = chan_edit(ts(1), "Fixed text")
    del edit["user_id"]                          # message_changed carries no user: the author comes from the mapping
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
    pending = {r["state"] for r in all_rows(world, "SELECT state FROM message_deliveries WHERE message_id=? AND recipient=?",
                                             message_id, f"slack:{MARKETING}")}
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
    assert one(world, "SELECT state FROM slack_message_links WHERE direction='out' AND message_id=?", reply)["state"] == "uncertain"
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
    assert [d["state"] for d in all_rows(world, "SELECT state FROM message_deliveries WHERE recipient=?",
                                         f"slack:{MARKETING}")] == ["delivered"]


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
def test_an_older_edit_arriving_after_a_newer_one_is_stale_and_changes_nothing(world):
    activate_marketing(world)
    world.bridge.receive(chan_event(ts(1), "Kickoff"))
    newer = dict(chan_edit(ts(1), "Kickoff at 12"), edited_ts=ts(5))
    older = dict(chan_edit(ts(1), "Kickoff at 11"), edited_ts=ts(3), event_id="Ed-late")
    assert world.bridge.receive(newer) == "edited"
    assert world.bridge.receive(older) == "stale"
    assert origin_message(world, ts(1))["body"] == "Kickoff at 12"
    assert origin_message(world, ts(1))["revision"] == 1


# ----------------------------------------------------------------------------- shared with another company
PEER = "gi_" + "a" * 24
GUEST = f"human:dana@{PEER}"


def carry_in(world, thread, text, origin="o-1"):
    """A message another company's install carried into this thread, through the door federation uses."""
    with world.hub.transaction() as c:
        FT.add_outside(c, thread, PEER)
        return FT.post_inbound(c, thread, text, kind="say",
                               origin={"origin_id": origin, "revision": 0, "install": PEER, "actor": GUEST})["id"]


def test_another_companys_words_never_reach_a_channel_even_on_a_link_made_before_the_thread_was_shared(world):
    link_id = request(world, "#secret", thread=MEMBERS)
    assert gate(world, link_id) == "active"
    carry_in(world, MEMBERS, "Another company's words")
    with world.hub.transaction() as c:
        TH.post(c, "human:ana", MEMBERS, "Our own words")
    world.bridge.outbound()
    assert [p["text"].split("\n")[-1] for p in world.slack.posts] == ["Our own words"]
    assert all_rows(world, "SELECT state FROM message_deliveries WHERE recipient LIKE 'slack:%' AND error='from another "
                    "company'") == [{"state": "moot"}]


def test_a_replica_or_a_thread_shared_out_cannot_be_linked(world):
    with world.hub.transaction() as c:
        now = H.now()
        c.execute("INSERT INTO federation_threads(id,thread_id,home_install,origin_id,created_at,updated_at) "
                  "VALUES('ft-1',?,?,'home-thread',?,?)", (MEMBERS, PEER, now, now))
    with pytest.raises(Problem) as refused:
        request(world, "#secret", thread=MEMBERS)
    assert refused.value.code == "shared_thread" and refused.value.status == 409
    with world.hub.transaction() as c:
        c.execute("UPDATE federation_threads SET origin_id=thread_id, home_install='gi_me' WHERE id='ft-1'")
    assert request(world, "#secret", thread=MEMBERS)          # the home's own thread, shared with nobody: it can
    ST.unlink_link(world.hub, WHO, all_rows(world, "SELECT id FROM slack_thread_links")[0]["id"], auth=Auth(world.hub))
    with world.hub.transaction() as c:
        c.execute("INSERT INTO federation_members(id,thread_id,install,status,joined_at_seq,created_at,updated_at) "
                  "VALUES('fm-1',?,?,'active',1,?,?)", (MEMBERS, PEER, now, now))
    with pytest.raises(Problem) as refused:
        request(world, "#secret", thread=MEMBERS)
    assert refused.value.code == "shared_thread"
    with world.hub.transaction() as c:
        c.execute("UPDATE federation_members SET status='removed' WHERE id='fm-1'")
    assert request(world, "#secret", thread=MEMBERS)          # the home took it back: its own again
