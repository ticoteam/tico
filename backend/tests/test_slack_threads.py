"""The Slack bridge for native threads (`backend/slack_threads.py`, `_threads-plan.md` §5 and §9).

Slack, the judge and the screening door are fakes. Core is not on any branch yet, so `FakeCore` implements the contract's
entry points (`docs/dev/threads-contract.md` §3-§4, §11) by writing the rows the contract names (messages,
conversations, message_deliveries) into the real test database; the bridge's SQL reads them back.
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
from backend.auth import Identity
from backend.config import Settings
from backend.store import Problem, Store, encode

TEAM, APP, BOT_USER, BOT_ID = "T02F3S2SD", "A0BU75YSGT1", "U0BUS6WA4SY", "B0BUNG973DY"
MARKETING, PRIVATE, LOUNGE, PARTNERS = "C0000000001", "G0000000001", "C0000000002", "C0000000003"
COMPANY, MEMBERS = "T-company", "T-members"
WHO = Identity("human:ana", "human", email="ana@acme.example")
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
CONTRACT = {
    "conversations": {"audience": "TEXT NOT NULL DEFAULT ''", "parent_id": "TEXT", "parent_message_id": "TEXT",
                      "root_id": "TEXT", "path": "TEXT NOT NULL DEFAULT ''"},
    "messages": {"origin_id": "TEXT", "revision": "INTEGER NOT NULL DEFAULT 0", "deleted_at": "TEXT"},
}
DELIVERIES = """CREATE TABLE IF NOT EXISTS message_deliveries (
  id TEXT PRIMARY KEY, message_id TEXT NOT NULL, recipient TEXT NOT NULL, thread_id TEXT,
  revision INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'pending', mode TEXT NOT NULL DEFAULT 'digest',
  reason TEXT NOT NULL DEFAULT 'outside', error TEXT, created TEXT NOT NULL,
  UNIQUE (message_id, recipient, revision))"""


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


class FakeCore:
    """`backend/threads.py` stand-in with the contract's entry points, over the contract's rows."""

    def __init__(self):
        self.outside, self.calls = {}, []           # thread id -> registered outside recipients; every call made

    def viewer(self, auth, who):
        return who

    def readable(self, c, viewer, thread):
        return True

    def add_outside_delivery(self, c, thread_id, recipient):
        self.outside.setdefault(thread_id, set()).add(recipient)

    def follow(self, c, viewer, thread_id, *, notify="live"):
        self.calls.append(("follow", viewer.actor, thread_id, notify))

    def root(self, c, conv):
        return c.execute("SELECT root_id FROM conversations WHERE id=?", (conv,)).fetchone()[0] or conv

    def deliver(self, c, message_id, root, revision):
        for recipient in sorted(self.outside.get(root, ())):
            c.execute("INSERT INTO message_deliveries (id,message_id,recipient,thread_id,revision,state,created) "
                      "VALUES (?,?,?,?,?,'pending',?)", (H.new_id(), message_id, recipient, root, revision, H.now()))

    def post(self, c, principal, thread_id, body, *, kind="say", refs=None, parent_message_id=None, to_actor=None,
             via=None, origin=None, checked=None, attachments=None):
        self.calls.append(("post", thread_id, body, parent_message_id, (origin or {}).get("origin_id")))
        if origin:
            seen = c.execute("SELECT id FROM messages WHERE origin_id=?", (origin["origin_id"],)).fetchone()
            if seen:
                return {"id": seen[0]}
        now, root, conv = H.now(), self.root(c, thread_id), thread_id
        if parent_message_id:
            found = c.execute("SELECT id FROM conversations WHERE parent_message_id=?", (parent_message_id,)).fetchone()
            conv = found[0] if found else H.new_id()
            if not found:
                audience = c.execute("SELECT audience FROM conversations WHERE id=?", (root,)).fetchone()[0]
                c.execute("INSERT INTO conversations (id,kind,subject,created,audience,parent_id,parent_message_id,root_id,path) "
                          "VALUES (?,?,?,?,?,?,?,?,'')", (conv, "thread", body.split(":")[0].strip()[:40], now, audience,
                                                         thread_id, parent_message_id, root))
        mid = H.new_id()
        c.execute("INSERT INTO messages (id,conversation_id,from_actor,kind,body,refs_json,created,origin_id,revision) "
                  "VALUES (?,?,?,?,?,?,?,?,0)", (mid, conv, principal.actor, kind, body,
                                                 json.dumps({**(refs or {}), **({"via": via} if via else {})}), now,
                                                 (origin or {}).get("origin_id")))
        self.deliver(c, mid, root, 0)
        return {"id": mid, "body": body}

    def write_revision(self, c, message_id, revision, body, deleted):
        conv = c.execute("SELECT conversation_id FROM messages WHERE id=?", (message_id,)).fetchone()[0]
        if deleted:
            c.execute("UPDATE messages SET deleted_at=?, revision=? WHERE id=?", (H.now(), revision, message_id))
        else:
            c.execute("UPDATE messages SET body=?, revision=? WHERE id=?", (body, revision, message_id))
        self.deliver(c, message_id, self.root(c, conv), revision)

    def apply_revision(self, c, origin, *, body=None, deleted=False):
        self.calls.append(("revision", origin["origin_id"], origin["revision"], body, deleted))
        found = c.execute("SELECT id FROM messages WHERE origin_id=?", (origin["origin_id"],)).fetchone()
        if not found:
            raise Problem("not_found", "no such message", 404)
        self.write_revision(c, found[0], origin["revision"], body, deleted)
        return {"id": found[0]}

    def revise_local(self, c, message_id, body=None, deleted=False):
        revision = c.execute("SELECT revision FROM messages WHERE id=?", (message_id,)).fetchone()[0] + 1
        self.write_revision(c, message_id, revision, body, deleted)

    def settle_delivery(self, c, delivery_id, state, error=None):
        c.execute("UPDATE message_deliveries SET state=?, error=? WHERE id=?", (state, error, delivery_id))

    def outside_due(self, c, prefix, limit):
        rows = c.execute("SELECT id, message_id, revision, recipient, thread_id FROM message_deliveries "
                         "WHERE state='pending' AND recipient LIKE ? ORDER BY created, id LIMIT ?", (prefix + "%", limit))
        return [dict(r) for r in rows.fetchall()]


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


def contract_tables(store):
    """The contract's columns and deliveries table, until core creates them (docs/dev/threads-contract.md §1, §4)."""
    with store.transaction() as c:
        for table, columns in CONTRACT.items():
            have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, declaration in columns.items():
                if name not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        c.execute(DELIVERIES)


@pytest.fixture
def hub(tmp_path):
    registry = tmp_path / "hub-registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text("owner: ana@acme.example\nallowed_domains: [acme.example]\n")
    settings = Settings(db_path=tmp_path / "hub.db", slack_team_id=TEAM, slack_app_id=APP, registry_dir=registry,
                        assistant_name="Tico")
    store = Store(settings)
    store.initialize()
    contract_tables(store)
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
    slack, core, clock = FakeSlack(), FakeCore(), Clock()
    screened = []

    def fake_screen(store, source, text, bot=None, **kw):
        """The screening door with the judge taken out: text that says BLOCKME is held (a Block), anything else passes."""
        screened.append(source)
        return {"action": "hold" if "BLOCKME" in (text or "") else "warn", "source": source}

    monkeypatch.setattr(screening, "screen", fake_screen)
    gw = G.Gateway(hub, slack, judge_engine=FakeJudge(), clock=clock)
    gw.verify_app()
    gw.bridge = ST.Bridge(gw, core=core)
    return SimpleNamespace(hub=hub, slack=slack, core=core, clock=clock, gw=gw, bridge=gw.bridge, screened=screened)


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
        return world.core.post(c, WHO, thread, text, kind="say", refs={}, parent_message_id=parent, via="assistant")["id"]


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
        assert world.core.outside == {COMPANY: {f"slack:{MARKETING}"}}
        assert [m["person_id"] for m in all_rows(world, "SELECT person_id FROM slack_link_members WHERE link_id=? "
                                                        "ORDER BY person_id", link_id)] == ["ana", "ben"]
    else:
        assert state == "refused"
        assert why in link_row(world, link_id)["reason"]
        assert world.core.outside == {}


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
    assert refs["origin"] == f"slack:{MARKETING}" and refs["slack"]["ts"] == ts(1)
    assert msg["origin_id"].endswith(f"@slack:{TEAM}")
    assert "slack" in world.screened


def test_a_slack_thread_reply_is_a_sub_thread_of_the_message_it_answers(world):
    activate_marketing(world)
    world.bridge.receive(chan_event(ts(1), "Kickoff"))
    root = origin_message(world, ts(1))
    assert world.bridge.receive(chan_event(ts(2), "Re: kickoff", user="U2", thread_ts=ts(1))) == "posted"
    call = world.core.calls[-1]
    assert call[1] == root["conversation_id"] and call[3] == root["id"]


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
    assert not any(call[0] == "revision" for call in world.core.calls)
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
        world.core.revise_local(c, message_id, body="Ship it today")
    world.bridge.outbound()
    assert world.slack.updates == [(MARKETING, sent_ts, "*Ana Rivera* · via Tico\nShip it today")]
    with world.hub.transaction() as c:
        world.core.revise_local(c, message_id, deleted=True)
    world.bridge.outbound()
    assert world.slack.deletes == [(MARKETING, sent_ts)]


def test_sub_threads_are_flattened_with_the_title_two_levels_down(world):
    activate_marketing(world)
    root = send_as_ana(world, "Kickoff")
    reply = send_as_ana(world, "Budget: first pass", parent=root)
    reply_thread = one(world, "SELECT conversation_id FROM messages WHERE id=?", reply)["conversation_id"]
    send_as_ana(world, "Budget: final", parent=reply, thread=reply_thread)
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
    ST.unlink_link(world.hub, WHO, link_id, core=world.core)
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
