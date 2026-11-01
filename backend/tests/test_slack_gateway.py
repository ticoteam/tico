"""The Slack gateway (`backend/slack_gateway.py`, `docs/slack-gateway.md`).

Slack and the judge are fakes; nothing here touches the network. The events, threads, posts,
conversations, messages and jobs go through the real store and write layer, because the
guardrails are the point.
"""

import json

import pytest

from backend import hubdb as H
from backend import slack_gateway as G
from backend.config import Settings
from backend.store import Store, encode
from clients import judge as J

TEAM, APP, BOT_USER, BOT_ID = "T02F3S2SD", "A0BU75YSGT1", "U0BUS6WA4SY", "B0BUNG973DY"
MARKETING, DM = "C0000000001", "D0AAAAAAA"
SCOPES = ("channels:history", "groups:history", "chat:write", "chat:write.customize", "app_mentions:read",
          "im:history", "users:read", "users:read.email")
PEOPLE = [
    {"id": "ana", "name": "Ana Rivera", "email": "ana@acme.example", "team": "leadership", "primary_for": ["*"]},
    {"id": "ben", "name": "Ben", "email": "ben@acme.example", "team": "product", "primary_for": ["product"]},
    {"id": "cara", "name": "Cara Melo", "email": "cara@acme.example", "team": "engineering"},
    # On the roster, but not admitted by registry/hub-access.yaml (no acme.example address).
    {"id": "contractor", "name": "A Contractor", "email": "contractor@example.com", "team": ""},
]
BOTS = {
    "coo": {"display_name": "Tico", "description": "The assistant.", "reports_to": None, "status": "active"},
    "legal": {"display_name": "Legal", "description": "Contracts, terms and legal risk.", "reports_to": None, "status": "active"},
    "cmo": {"display_name": "CMO", "description": "Marketing strategy.", "reports_to": None, "status": "active"},
    "seo": {"display_name": "SEO", "description": "Organic search.", "reports_to": "cmo", "status": "active"},
    "content-social": {"display_name": "Content & Social", "description": "Posts and social.", "reports_to": "cmo", "status": "active"},
    "cto": {"display_name": "CTO", "description": "Engineering alerts.", "reports_to": None, "status": "active"},
    "finance": {"display_name": "Finance", "description": "Money.", "reports_to": None, "status": "paused"},
}
USERS = {
    "U1": {"id": "U1", "team_id": TEAM, "name": "ana", "profile": {"email": "ana@acme.example"}},
    "U2": {"id": "U2", "team_id": TEAM, "name": "ben", "profile": {"email": "ben@acme.example"}},
    "U3": {"id": "U3", "team_id": TEAM, "name": "contractor", "profile": {"email": "contractor@example.com"}},
    "U4": {"id": "U4", "team_id": TEAM, "name": "newhire", "profile": {"email": "newhire@acme.example"}},
    "U5": {"id": "U5", "team_id": TEAM, "name": "guest", "is_restricted": True, "profile": {"email": "guest@acme.example"}},
    "U6": {"id": "U6", "team_id": TEAM, "name": "gone", "deleted": True, "profile": {"email": "cara@acme.example"}},
    "U7": {"id": "U7", "team_id": TEAM, "name": "somebot", "is_bot": True, "profile": {"email": ""}},
    # A Slack Connect member of another workspace, with a acme.example address on the roster.
    "U8": {"id": "U8", "team_id": "T0OTHER", "name": "ben-elsewhere", "profile": {"email": "ben@acme.example"}},
}
CHANNELS = {
    MARKETING: {"id": MARKETING, "name": "marketing"},
    "C0EXT": {"id": "C0EXT", "name": "partners", "is_ext_shared": True},
}


class FakeSlack:
    """The Web API as the gateway sees it: canned users and channels, and every post it made."""

    def __init__(self, team=TEAM, app=APP, scopes=SCOPES):
        self.team, self.app, self.scopes = team, app, scopes
        self.posts, self.failures, self.calls = [], [], []
        self.pages, self.threads, self.unreadable = {}, {}, set()      # what history and replies return

    def auth_test(self):
        return {"ok": True, "team_id": self.team, "user_id": BOT_USER, "bot_id": BOT_ID, "url": "https://acme.slack.com/"}

    def bots_info(self, bot_id):
        assert bot_id == BOT_ID
        return {"id": bot_id, "app_id": self.app, "user_id": BOT_USER, "name": "Tico"}

    def users_info(self, user_id):
        self.calls.append(("users.info", user_id))
        if user_id not in USERS:
            raise G.SlackError("users.info", "user_not_found")
        return USERS[user_id]

    def conversations_info(self, channel):
        self.calls.append(("conversations.info", channel))
        if channel not in CHANNELS:
            raise G.SlackError("conversations.info", "channel_not_found")
        return CHANNELS[channel]

    def history(self, channel, oldest="", limit=200):
        self.calls.append(("conversations.history", channel, oldest))
        if channel in self.unreadable:
            raise G.SlackError("conversations.history", "not_in_channel")
        return sorted((m for m in self.pages.get(channel, []) if float(m["ts"]) > float(oldest or 0)),
                      key=lambda m: float(m["ts"]))[:limit]

    def replies(self, channel, thread_ts, oldest="", limit=200):
        self.calls.append(("conversations.replies", channel, thread_ts, oldest))
        return sorted((m for m in self.threads.get((channel, thread_ts), []) if float(m["ts"]) > float(oldest or 0)),
                      key=lambda m: float(m["ts"]))[:limit]

    def conversations_open(self, user_id):
        self.calls.append(("conversations.open", user_id))
        return {"id": "D0" + user_id}

    def post_message(self, channel, thread_ts, text, username=None, icon_emoji=None, icon_url=None):
        self.calls.append(("chat.postMessage", channel))
        if self.failures:
            raise self.failures.pop(0)
        self.posts.append({"channel": channel, "thread_ts": thread_ts, "text": text, "username": username,
                           "icon_emoji": icon_emoji, "icon_url": icon_url})
        return {"ok": True, "ts": f"{1700000000 + len(self.posts)}.000100"}


class FakeJudge:
    """Answers from a queue, or from a function of the state; remembers every call."""

    def __init__(self, answers=None):
        self.answers, self.calls = list(answers or []), []

    def __call__(self, state, questions, label=None):
        self.calls.append({"state": state, "questions": questions, "label": label})
        answer = self.answers.pop(0) if self.answers else {}
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer(state)
        # The engine shape (`clients/judge.py`): the answers inside a result with the model and usage.
        return {"model": "judge-1.13.0", "answers": answer, "usage": {"input_tokens": 500, "output_tokens": 0}, "ms": 210}


def answers(asks=0.9, reply_to_last=0.1, names_bot=0.1, **bots):
    out = {"asks": {"noul": asks}, "reply_to_last": {"noul": reply_to_last}, "names_bot": {"noul": names_bot}}
    for slug, p in bots.items():
        out["bot:" + slug.replace("_", "-")] = {"noul": p}
    return out


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
    settings = Settings(db_path=tmp_path / "hub.db", slack_team_id=TEAM, slack_app_id=APP, registry_dir=registry, assistant_name="Tico")
    store = Store(settings)
    store.initialize()
    with store.transaction() as c:
        H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", **entry} for slug, entry in BOTS.items()},
                        {"people": PEOPLE})
        c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({
            "default_user": "ana", "teams": {"marketing": {"root": "cmo"}, "engineering": {"root": "cto"}},
            "people": PEOPLE}),))
        for slug, entry in BOTS.items():
            c.execute("INSERT INTO bot_config(bot,config,team,owner_id,description,reports_to) VALUES(?,?,?,?,?,?)",
                      (slug, encode({"name": slug, "runtime": "fake"}), None, "ana", entry["description"], entry["reports_to"]))
    return store


@pytest.fixture
def gateway(hub):
    slack, engine, clock = FakeSlack(), FakeJudge(), Clock()
    gw = G.Gateway(hub, slack, judge_engine=engine, clock=clock)
    gw.verify_app()
    gw.engine = engine
    return gw


COUNTER = {"n": 0}


def envelope(text, channel=MARKETING, user="U1", thread_ts=None, kind="app_mention", event_id=None, ts=None,
             team=TEAM, app=APP, **event_fields):
    COUNTER["n"] += 1
    ts = ts or f"{1690000000 + COUNTER['n']}.000100"
    event = {"type": kind, "user": user, "channel": channel, "ts": ts, "text": text, **event_fields}
    if kind == "message":
        event["channel_type"] = "im"
    if thread_ts:
        event["thread_ts"] = thread_ts
    return {"type": "event_callback", "team_id": team, "api_app_id": app,
            "event_id": event_id or f"Ev{COUNTER['n']:08d}", "event": event}


def rows(hub, sql, *args):
    with hub.read() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def events(hub):
    return rows(hub, "SELECT * FROM slack_events ORDER BY ts")


def jobs(hub):
    return rows(hub, "SELECT bot_id,state FROM jobs ORDER BY created_at")


def send(gateway, text, judge_answer=None, **kw):
    """One message through the whole gateway: persist, then a tick that routes it."""
    if judge_answer is not None:
        gateway.engine.answers.append(judge_answer)
    assert gateway.receive(envelope(text, **kw)) == "queued"
    return gateway.tick()["events"][-1]


# ----------------------------------------------------------------------------- start-up
def test_startup_refuses_another_workspace_or_app_and_reports_missing_scopes(hub):
    with pytest.raises(RuntimeError, match="workspace"):
        G.Gateway(hub, FakeSlack(team="T0OTHER")).verify_app()
    with pytest.raises(RuntimeError, match="app"):
        G.Gateway(hub, FakeSlack(app="A0OTHER")).verify_app()
    with pytest.raises(RuntimeError, match="SLACK_TEAM_ID"):
        G.Gateway(Store(Settings(db_path=hub.settings.db_path)), FakeSlack()).verify_app()
    limited = G.Gateway(hub, FakeSlack(scopes=("chat:write", "im:history", "channels:history", "groups:history",
                                               "users:read", "users:read.email")))
    verified = limited.verify_app()
    assert verified == {"team_id": TEAM, "app_id": APP, "bot_user_id": BOT_USER,
                        "missing_scopes": ["app_mentions:read", "chat:write.customize", "files:read"]}
    assert limited.customize is False


# ----------------------------------------------------------------------------- verify and deny
def test_only_verified_admitted_roster_humans_in_internal_channels_get_through(gateway, hub):
    slack = gateway.slack
    # Cheap filters before anything is persisted: not our workspace, a bot, an edit, a group DM.
    assert gateway.receive(envelope("hi", team="T0OTHER")) == "foreign"
    assert gateway.receive(envelope("hi", bot_id="B0X")) == "bot_or_subtype"
    assert gateway.receive(envelope("hi", subtype="message_changed")) == "bot_or_subtype"
    assert gateway.receive(envelope("hi", user=BOT_USER)) == "bot_or_subtype"
    assert gateway.receive({**envelope("hi", kind="message"), "event": {**envelope("hi", kind="message")["event"], "channel_type": "mpim"}}) == "not_dm"
    # A plain channel message is stored for the channel's readers, never routed (see the channel tests).
    assert gateway.receive({**envelope("hi", kind="message"), "event": {**envelope("hi", kind="message")["event"], "channel_type": "channel"}}) == "stored"
    assert [e["state"] for e in events(hub)] == ["stored"] and slack.calls == []
    with hub.transaction() as c:
        c.execute("DELETE FROM slack_events")
    # Persisted, then denied with the reason, and nobody is replied to.
    denied = {
        "guest": send(gateway, "@Tico hello", user="U5"),
        "deactivated": send(gateway, "@Tico hello", user="U6"),
        "bot user": send(gateway, "@Tico hello", user="U7"),
        "not admitted": send(gateway, "@Tico hello", user="U3"),
        "not on roster": send(gateway, "@Tico hello", user="U4"),
        "unknown user": send(gateway, "@Tico hello", user="U9"),
        "slack connect": send(gateway, "@Tico hello", channel="C0EXT"),
        "other workspace": send(gateway, "@Tico hello", user="U8"),
        "other workspace, by DM": send(gateway, "hello", user="U8", channel=DM, kind="message"),
    }
    reasons = {name: result["reason"] for name, result in denied.items()}
    assert reasons == {"guest": "sender is a guest", "deactivated": "sender is deactivated",
                       "bot user": "sender is a bot", "not admitted": "sender is not admitted by hub-access",
                       "not on roster": "sender is not on the roster", "unknown user": "users.info: user_not_found",
                       "slack connect": "channel is shared outside the workspace",
                       "other workspace": "sender is outside the workspace",
                       "other workspace, by DM": "sender is outside the workspace"}
    assert not any(call == ("conversations.info", DM) for call in slack.calls), "a DM is never looked up as a channel"
    assert {e["state"] for e in events(hub)} == {"denied"}
    assert slack.posts == [] and jobs(hub) == []
    assert rows(hub, "SELECT count(*) AS n FROM conversations")[0]["n"] == 0
    # A verified person goes through: a fresh users.info each time, never a cache.
    before = len([call for call in slack.calls if call[0] == "users.info"])
    result = send(gateway, "<@U0BUS6WA4SY> can Legal check this clause?", answers(legal=0.9))
    assert result["state"] == "routed"
    assert len([call for call in slack.calls if call[0] == "users.info"]) == before + 1
    event = events(hub)[-1]
    assert event["actor"] == "human:ana" and event["text"] == "@Tico can Legal check this clause?"


def test_an_event_is_persisted_once_however_often_slack_delivers_it(gateway, hub):
    payload = envelope("<@U0BUS6WA4SY> ping", event_id="Ev1")
    assert gateway.receive(payload) == "queued"
    assert gateway.receive(payload) == "duplicate"
    # The same message under another event id (a DM that also mentions Tico arrives twice).
    twin = {**payload, "event_id": "Ev2"}
    assert gateway.receive(twin) == "duplicate"
    gateway.engine.answers.append(answers(legal=0.9))
    gateway.tick()
    assert [e["event_id"] for e in events(hub)] == ["Ev1"]
    assert len(jobs(hub)) == 1
    # A retry after routing changes nothing either.
    assert gateway.receive(payload) == "duplicate"
    assert gateway.tick()["events"] == []


# ----------------------------------------------------------------------------- routing
# ----------------------------------------------------------------------------- egress
def routed_reply(gateway, hub, text="Done."):
    send(gateway, "<@U0BUS6WA4SY> Legal?", answers(legal=0.9))
    thread = rows(hub, "SELECT * FROM slack_threads")[0]
    with hub.transaction() as c:
        message = rows(hub, "SELECT id FROM messages WHERE conversation_id=?", thread["conversation_id"])[0]
        reply = H.say(c, "bot:legal", "human:ana", text, conversation_id=thread["conversation_id"], in_reply_to=message["id"])
    return reply["id"]


@pytest.mark.parametrize("held", ["paused", "quarantined"])
def test_a_persons_new_thread_to_a_bot_held_since_the_roster_was_read_is_kept_and_said_so(gateway, hub, held):
    """Legal is paused or quarantined while the decision model reads the roster: the new thread's message is kept for
    Legal, the person is told in Slack that it waits, and it is one queued job that runs when Legal is back."""
    def hold(state):
        with hub.transaction() as c:
            if held == "quarantined":
                H.quarantine(c, "legal", "10 repeated refusals today")
            else:
                c.execute("UPDATE bots SET state='paused' WHERE slug='legal'")
        return answers(legal=0.9)

    result = send(gateway, "please review the vendor terms", judge_answer=hold, channel=DM, kind="message")
    assert result["state"] == "routed", result
    (kept,) = result["decision"]["delivered"]
    assert kept["bot_id"] == "legal" and kept["opened"] and result["decision"]["dropped"] == []
    assert "Your message is kept for when it is back" in kept["held"]
    assert ("until about" if held == "quarantined" else "until a person resumes it") in kept["held"]
    assert [p["text"] for p in gateway.slack.posts] == [kept["held"]] and gateway.slack.posts[0]["channel"] == DM
    message = rows(hub, "SELECT * FROM messages WHERE id=?", kept["message_id"])[0]
    assert (message["sender_id"], message["recipient_id"], message["body"]) == ("human:ana", "bot:legal",
                                                                             "please review the vendor terms")
    assert rows(hub, "SELECT bot_id,state FROM jobs WHERE message_id=?", kept["message_id"]) == [{"bot_id": "legal", "state": "queued"}]
    # Nothing else woke up for it, and it is not sent again: the assistant got nothing, one job waits for Legal.
    assert jobs(hub) == [{"bot_id": "legal", "state": "queued"}]
    with hub.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug='legal'")
    assert gateway.tick()["events"] == [] and jobs(hub) == [{"bot_id": "legal", "state": "queued"}]


def test_a_new_thread_to_a_bot_that_is_gone_is_still_dropped_and_the_assistant_asks(gateway, hub):
    def archive(state):
        with hub.transaction() as c:
            c.execute("UPDATE bots SET state='archived' WHERE slug='legal'")
        return answers(legal=0.9)

    result = send(gateway, "please review the vendor terms", judge_answer=archive, channel=DM, kind="message")
    assert [d["bot"] for d in result["decision"]["dropped"]] == ["legal"]
    assert [d["bot"] for d in result["decision"]["delivered"]] == ["coo"] and gateway.slack.posts == []


def test_a_post_is_attempted_once_and_a_crash_between_send_and_record_is_uncertain(gateway, hub):
    slack, clock = gateway.slack, gateway.clock
    mid = routed_reply(gateway, hub)
    slack.failures.append(G.SlackUnreachable("Slack chat.postMessage: TimeoutError"))
    gateway.tick()
    post = rows(hub, "SELECT state,attempts,error FROM slack_posts")[0]
    assert post["state"] == "uncertain" and post["attempts"] == 1 and "TimeoutError" in post["error"]
    gateway.tick()
    assert rows(hub, "SELECT attempts FROM slack_posts")[0]["attempts"] == 1 and slack.posts == []
    # An operator confirmed the reply is not in Slack and set it back to ready: one more attempt.
    with hub.transaction() as c:
        c.execute("UPDATE slack_posts SET state='ready' WHERE message_id=?", (mid,))
    slack.failures.append(G.SlackError("chat.postMessage", "ratelimited", retry_after=5))
    gateway.tick()
    post = rows(hub, "SELECT state,attempts,next_attempt_at FROM slack_posts")[0]
    assert post["state"] == "rate_limited" and post["attempts"] == 2 and post["next_attempt_at"] > clock()
    gateway.tick()
    assert rows(hub, "SELECT attempts FROM slack_posts")[0]["attempts"] == 2, "not before Retry-After"
    clock.advance(6)
    gateway.tick()
    assert rows(hub, "SELECT state,attempts,slack_ts FROM slack_posts")[0] == {"state": "sent", "attempts": 3, "slack_ts": "1700000001.000100"}
    # A definite refusal is failed, not retried.
    with hub.transaction() as c:
        H.say(c, "bot:legal", "human:ana", "One more thing.", conversation_id=rows(hub, "SELECT conversation_id FROM slack_threads")[0]["conversation_id"])
    slack.failures.append(G.SlackError("chat.postMessage", "channel_not_found"))
    gateway.tick()
    assert [p["state"] for p in rows(hub, "SELECT state FROM slack_posts ORDER BY created_at")] == ["sent", "failed"]
    gateway.tick()
    assert len(slack.posts) == 1


# ----------------------------------------------------------------------------- the review's cases
def test_a_bot_reply_is_escaped_so_it_cannot_page_the_channel_or_lose_prose(gateway, hub):
    routed_reply(gateway, hub, "<!channel> R&D says x < y and y > z")
    gateway.tick()
    assert gateway.slack.posts[0]["text"] == "&lt;!channel&gt; R&amp;D says x &lt; y and y &gt; z\n\n(sent from Legal)"
    with hub.transaction() as c:
        H.say(c, "bot:legal", "human:ana", "<!everyone> <!here>", conversation_id=rows(hub, "SELECT conversation_id FROM slack_threads")[0]["conversation_id"])
    gateway.tick()
    assert gateway.slack.posts[1]["text"] == "&lt;!everyone&gt; &lt;!here&gt;\n\n(sent from Legal)"


# ----------------------------------------------------------------------------- channels read like an employee reads them
RELEASE, QUIET = "C0000000002", "C0QUIET"
CHANNELS.update({RELEASE: {"id": RELEASE, "name": "release_notes"}, QUIET: {"id": QUIET, "name": "quiet"}})
USERS["U1"]["profile"]["real_name"] = "Ana Rivera"
USERS["U2"]["profile"]["real_name"] = "Ben"


@pytest.fixture
def readers(tmp_path):
    """A registry whose channels name their readers: #marketing is read by the CMO and SEO,
    #release_notes by the CTO, #quiet by nobody; a paused bot and an unknown slug are listed too."""
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text("owner: ana@acme.example\nallowed_domains:\n  - acme.example\n")
    (registry / "slack-channels.yaml").write_text(f"""
channels:
  - id: {MARKETING}
    name: marketing
    purpose: "Marketing work log."
    post: true
    readers: [cmo, seo, finance, nobody-here]
  - id: {RELEASE}
    name: release_notes
    purpose: "Product release notes."
    post: false
    readers: cto
  - id: {QUIET}
    name: quiet
    purpose: "Nobody reads this one."
    post: false
""")
    settings = Settings(db_path=tmp_path / "hub.db", slack_team_id=TEAM, slack_app_id=APP, registry_dir=registry,
                        slack_digest_cap=5)
    store = Store(settings)
    store.initialize()
    with store.transaction() as c:
        H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", **entry} for slug, entry in BOTS.items()},
                        {"people": PEOPLE})
        c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({
            "default_user": "ana", "teams": {"marketing": {"root": "cmo"}, "engineering": {"root": "cto"}},
            "people": PEOPLE}),))
        for slug, entry in BOTS.items():
            c.execute("INSERT INTO bot_config(bot,config,team,owner_id,description,reports_to) VALUES(?,?,?,?,?,?)",
                      (slug, encode({"name": slug, "runtime": "fake"}), None, "ana", entry["description"], entry["reports_to"]))
    slack, engine, clock = FakeSlack(), FakeJudge(), Clock()
    gw = G.Gateway(store, slack, judge_engine=engine, clock=clock)
    gw.verify_app()
    gw.engine = engine
    gw.tick()                                   # the first pass: cursors start at now, nothing is delivered
    assert digests(store) == []
    return gw


def channel_message(text, channel=MARKETING, user="U1", thread_ts=None, subtype=None, ts=None, **fields):
    """A `message.channels` envelope: what Slack sends for a plain channel message."""
    payload = envelope(text, channel=channel, user=user, thread_ts=thread_ts, kind="message", ts=ts, **fields)
    payload["event"]["channel_type"] = "channel"
    if subtype:
        payload["event"]["subtype"] = subtype
    return payload


def digests(hub):
    return rows(hub, "SELECT d.reader,d.count,d.skipped,d.channels,m.body,m.refs,m.sender_id,m.recipient_id "
                     "FROM slack_digests d JOIN messages m ON m.id=d.message_id ORDER BY d.created_at, d.reader")


def cursors(hub):
    return {(r["channel"], r["reader"]): r for r in rows(hub, "SELECT * FROM slack_reads")}


def test_a_channel_message_is_stored_and_reaches_the_readers_once_on_the_hourly_pass(readers):
    gw, hub = readers, readers.store
    assert set(cursors(hub)) == {(MARKETING, "cmo"), (MARKETING, "seo"), (RELEASE, "cto")}, \
        "a paused bot, an unknown slug and a channel with no readers get no cursor"
    assert gw.receive(channel_message("Launch copy is ready for review")) == "stored"
    assert gw.receive(channel_message("Deploy 4.2 went out", channel=RELEASE, user="U2")) == "stored"
    assert gw.receive(channel_message("Nobody reads this", channel=QUIET)) == "stored"
    assert [e["state"] for e in events(hub)] == ["stored"] * 3 and jobs(hub) == [], "stored is not routed"
    assert gw.tick()["digests"] is None, "not due yet"
    gw.clock.advance(3600)
    out = gw.tick()["digests"]
    assert [(d["reader"], d["state"], d["count"]) for d in out] == [("cmo", "sent", 1), ("cto", "sent", 1), ("seo", "sent", 1)]
    sent = digests(hub)
    assert [d["reader"] for d in sent] == ["cmo", "cto", "seo"]
    cmo = sent[0]
    assert cmo["sender_id"] == "keeper" and cmo["recipient_id"] == "bot:cmo"
    assert "## #marketing — Marketing work log." in cmo["body"] and "Ana Rivera: Launch copy is ready for review" in cmo["body"]
    assert "Deploy 4.2" not in cmo["body"] and "Nobody reads this" not in cmo["body"]
    assert "Ben: Deploy 4.2 went out" in sent[1]["body"]
    refs = json.loads(cmo["refs"])["slack"]
    assert refs["channel_name"] == "#marketing" and refs["permalink"] == f"https://acme.slack.com/archives/{MARKETING}"
    assert refs["digest"]["count"] == 1 and len(refs["digest"]["event_ids"]) == 1
    assert [j["bot"] for j in jobs(hub)] == ["cmo", "cto", "seo"], "each reader gets one turn"
    assert rows(hub, "SELECT subject,scope,participants FROM conversations ORDER BY created_at")[0] == {
        "subject": "Slack channels", "scope": "direct", "participants": '["keeper", "bot:cmo"]'}
    # The cursors moved with the delivery; nothing new means nothing sent and no turn.
    gw.clock.advance(3600)
    assert [d["state"] for d in gw.tick()["digests"]] == ["nothing"] * 3
    assert len(digests(hub)) == 3 and len(jobs(hub)) == 3
    # The next message lands in the same conversation for that reader.
    gw.receive(channel_message("Second post"))
    gw.clock.advance(3600)
    gw.tick()
    assert len({r["conversation_id"] for r in rows(hub, "SELECT conversation_id FROM slack_digests WHERE reader='cmo'")}) == 1
    assert "Second post" in digests(hub)[-1]["body"] and "Launch copy" not in digests(hub)[-1]["body"]



# ----------------------------------------------------------------------------- the question cap
class CappedJudge:
    """The real contract's cap and shape check on every call; answers by question id."""

    def __init__(self, scores, fail=None):
        self.scores, self.fail, self.calls = scores, fail, []

    def __call__(self, state, questions, label=None):
        self.calls.append(list(questions))
        J.validate(state, questions, label)           # more than J.MAX_QUESTIONS is refused, as the real call does
        if self.fail:
            raise self.fail
        return {"model": "judge-1.13.0", "usage": {}, "ms": 1,
                "answers": {qid: {"noul": self.scores.get(qid, 0.05)} for qid in questions}}


def add_bots(hub, count):
    with hub.transaction() as c:
        H.sync_registry(c, {f"bot-{n:02d}": {"name": f"bot-{n:02d}", "runtime": "fake", "status": "active",
                                              "display_name": f"Bot {n:02d}"} for n in range(count)}, {})


def test_a_shared_dm_with_56_active_bots_is_asked_in_calls_within_the_cap_and_routed(gateway, hub):
    add_bots(hub, 56 - 6)                     # the fixture already has six active bots
    with hub.read() as c:
        assert len(gateway.fleet(c)) == 56
    gateway._judge = engine = CappedJudge({"asks": 0.9, "bot:bot-49": 0.95, "bot:legal": 0.7})
    result = send(gateway, "please draft the vendor terms", channel=DM, kind="message")
    assert result["state"] == "routed", result
    assert [r["bot"] for r in result["decision"]["recipients"]] == ["bot-49", "legal"]
    assert [len(call) for call in engine.calls] == [40, 19], "56 bots and the three fixed questions, split at the cap"
    assert sorted(q for call in engine.calls for q in call) == sorted(set(q for call in engine.calls for q in call)), \
        "no question is asked twice"
    assert len(result["decision"]["scores"]) == 56
    assert events(hub)[0]["state"] == "routed"


def test_a_persons_message_the_model_reads_as_asking_for_nothing_still_reaches_the_assistant(gateway, hub):
    """Decide picks who gets a person's message, never whether anyone does (the Decide on Jev spec)."""
    result = send(gateway, "thanks, that's all", judge_answer=answers(asks=0.1, legal=0.9), channel=DM, kind="message")
    assert result["state"] == "routed" and [r["bot"] for r in result["decision"]["recipients"]] == ["coo"]
    assert "asks for nothing" in result["decision"]["reason"] and [j["bot"] for j in jobs(hub)] == ["coo"]


def test_a_decision_outage_retries_for_ten_minutes_and_then_fails_instead_of_looping(gateway, hub):
    gateway._judge = CappedJudge({}, fail=J.JudgeError("unavailable", "the decision model answered 503", 503, retryable=True))
    assert send(gateway, "hello there", channel=DM, kind="message")["state"] == "received"
    assert gateway.slack.posts == [], "a short outage tells nobody"
    gateway.clock.advance(G.RETRY_SECONDS)
    assert [e["state"] for e in gateway.tick()["events"]] == ["received"], "retried after a minute"
    gateway.clock.advance(G.ROUTE_GIVE_UP_SECONDS)
    final = gateway.tick()["events"][-1]
    assert final["state"] == "failed" and "still failing after 10 minutes" in final["reason"]
    assert events(hub)[0]["state"] == "failed" and len(gateway.slack.posts) == 1
    calls = len(gateway._judge.calls)
    gateway.clock.advance(3600)
    assert gateway.tick()["events"] == [] and len(gateway._judge.calls) == calls


def task_notice(hub, owner='bot:legal', requester='human:ana', actor=None, status='done', quiet=False):
    with hub.transaction() as c:
        made = H.task_create(c, requester, 'Write the summary', 'Write the summary for the team.', owner, lint=False)
        H.task_update(c, actor or owner, made['id'], status=status, note='Ready <now>\nMore details.', quiet=quiet)
    return made


def link_task_requester(hub, slack_id='U1', enabled=True):
    hub.settings.slack_gateway_enabled = enabled
    hub.settings.public_url = 'https://tico.example.com'
    with hub.transaction() as c:
        c.execute('UPDATE humans SET slack_id=? WHERE id=?', (slack_id, 'ana'))


def test_task_results_are_one_tico_dm_per_task_status(gateway, hub):
    owner, status = 'bot:legal', 'done'
    link_task_requester(hub)
    made = task_notice(hub, owner=owner, status=status)
    assert gateway.task_completions() == 1
    assert gateway.task_completions() == 0
    gateway.originate()
    gateway.mirror()
    assert len(rows(hub, 'SELECT * FROM slack_posts')) == 1
    # Recreate the gateway before delivering: queued work survives a restart.
    restarted = G.Gateway(hub, gateway.slack, clock=gateway.clock)
    restarted.verify_app()
    assert restarted.task_completions() == 0
    assert restarted.deliver()[0]['state'] == 'sent'
    assert restarted.deliver() == []
    post = gateway.slack.posts[0]
    label = 'Finished' if status == 'done' else 'Declined'
    assert post['text'] == (f'{label}: Write the summary\nby {"Legal" if owner.startswith("bot:") else "Ben"}'
                            f'\nReady &lt;now&gt;\n<https://tico.example.com/#/task/{made["id"]}|Open task>')
    assert post['channel'] == 'D0U1' and post['thread_ts'] is None
    assert post['username'] is None
    with hub.transaction() as c:
        H.task_update(c, 'human:ana', made['id'], status='open')
        H.task_update(c, owner, made['id'], status=status)
        H.task_update(c, owner, made['id'], status=status)
    assert gateway.task_completions() == 0
    gateway.originate()
    gateway.mirror()
    assert len(rows(hub, 'SELECT * FROM slack_posts')) == 1


def test_a_muted_bot_stays_in_tico_unless_the_person_is_talking_to_it_in_slack(gateway, hub):
    link_task_requester(hub)
    with hub.transaction() as c:
        raw = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        raw["people"] = [{**p, "slack_muted_bots": ["legal"]} if p["id"] == "ana" else p for p in raw["people"]]
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(raw),))
    # Ana asked Legal from Slack, so everything Legal says in that conversation still goes back there.
    answer = routed_reply(gateway, hub)
    thread = rows(hub, "SELECT * FROM slack_threads")[0]
    with hub.transaction() as c:
        later = H.say(c, "bot:legal", "human:ana", "Run report.", conversation_id=thread["conversation_id"])["id"]
        other = H.say(c, "bot:cmo", "human:ana", "Done with the launch plan.")["id"]
    gateway.originate()
    gateway.mirror()
    with hub.transaction() as c:
        # Days later, Legal's unprompted digest lands in the same chat; Ana is no longer talking there.
        c.execute("UPDATE messages SET created_at=? WHERE conversation_id=? AND sender_id='human:ana'",
                  (H.shift(H.now(), days=-2), thread["conversation_id"]))
        muted = H.say(c, "bot:legal", "human:ana", "Weekly contract digest.", conversation_id=thread["conversation_id"])["id"]
    gateway.originate()
    gateway.mirror()
    posts = {r["message_id"]: r["state"] for r in rows(hub, "SELECT * FROM slack_posts")}
    assert posts == {answer: "ready", later: "ready", muted: "cancelled", other: "ready"}


@pytest.mark.parametrize('skip', ['wrong_person'])
def test_task_result_skips(gateway, hub, skip):
    link_task_requester(hub, slack_id={'outside': 'U8', 'wrong_person': 'U2'}[skip])
    task_notice(hub)
    assert gateway.task_completions() == 0
    gateway.originate()
    gateway.mirror()
    assert rows(hub, 'SELECT * FROM slack_posts') == []
    assert gateway.slack.posts == []


def test_routing_counts_against_the_senders_daily_cap_and_over_it_the_assistant_gets_the_message(gateway, hub):
    hub.settings.decisions_daily_calls_human = 1
    first = send(gateway, "please review the vendor terms", judge_answer=answers(legal=0.9), channel=DM,
                 kind="message", user="U2")
    assert first["state"] == "routed"
    audit = rows(hub, "SELECT actor,target,detail FROM events WHERE action='judge.call'")
    assert [(r["actor"], r["target"]) for r in audit] == [("human:ben", G.JUDGE_LABEL)]
    assert json.loads(audit[0]["detail"])["via"] == "slack" and "vendor" not in audit[0]["detail"]
    kept = json.loads(audit[0]["detail"])["answers"]
    assert kept["bot:legal"] == {"type": "noul", "value": 0.9, "confidence": 0.8}
    assert kept["asks"] == {"type": "noul", "value": 0.9, "confidence": 0.8}
    from backend.decide_readout import readout
    with hub.read() as c:
        out = readout(c, hub.settings)
    (row,) = [r for r in out["sets"] if r["set"] == "slack-route"]
    assert row["answers"]["asks"] == {"0.7 and over": 1} and row["answers"]["(other questions)"]["0.7 and over"] >= 1
    assert "not kept" not in out["text"]
    calls = len(gateway.engine.calls)
    second = send(gateway, "and the NDA too", channel=DM, kind="message", user="U2")
    assert len(gateway.engine.calls) == calls, "over the cap the decision model is not asked"
    assert second["state"] == "routed" and second["decision"]["routed_by"] == "budget"
    assert [r["bot"] for r in second["decision"]["recipients"]] == ["coo"]


def test_a_routing_retry_is_one_short_http_attempt_and_one_audited_call(hub, monkeypatch):
    from backend import judge as B
    attempts = []

    def opener(request, timeout=None):
        attempts.append(timeout)
        raise TimeoutError()

    monkeypatch.setattr("clients.judge.urlopen", opener)
    hub.settings.typesafe_api_key = "synthetic"
    gw = G.Gateway(hub, FakeSlack(), clock=Clock())
    gw.verify_app()
    assert gw.receive(envelope("hello there", channel=DM, kind="message")) == "queued"
    assert gw.tick()["events"][-1]["state"] == "received"
    assert attempts == [B.JEV_TIMEOUT], "the gateway's own minute-by-minute retry is the only retry"
    gw.clock.advance(G.RETRY_SECONDS)
    gw.tick()
    gw.clock.advance(G.ROUTE_GIVE_UP_SECONDS)
    assert gw.tick()["events"][-1]["state"] == "failed"
    assert len(attempts) == 3
    assert rows(hub, "SELECT count(*) AS n FROM events WHERE action='judge.call'")[0]["n"] == 1


def test_a_shared_screenshot_reaches_the_bot_as_a_file_line_it_can_open():
    shot = {"id": "F0SHOT1", "name": "checkout-error.png", "mimetype": "image/png"}
    payload = {"event_id": "Ev1", "team_id": "T1", "api_app_id": "A1",
               "event": {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1", "ts": "1.1",
                         "text": "checkout breaks, see screenshot", "files": [shot]}}
    event, why = G.event_from(payload, "UBOT")
    assert why is None
    assert event["text"] == "checkout breaks, see screenshot\n[file F0SHOT1] checkout-error.png"
    payload["event"]["text"] = ""
    assert G.event_from(payload, "UBOT")[0]["text"] == "[file F0SHOT1] checkout-error.png"
    assert G.message_text({"text": "", "files": [shot]}) == "[file F0SHOT1] checkout-error.png"
    assert G.message_text({"text": "", "files": [{"name": "no-id.png"}]}) == "(a file with no text)"
    assert "files:read" in G.NEEDED_SCOPES
    assert G.message_text({"text": "", "files": [{"id": "F1", "name": "a.png\n[file F2] b.pdf"}]}) == \
        "[file F1] a.png [file F2] b.pdf"


# ----------------------------------------------------------------------------- visuals: one line, never the visual (Dev 248)
def visual(kind="image", n=1):
    return {"file_id": f"f{n}", "version": 1, "title": f"Chart {n}", "kind": kind, "name": f"chart{n}.png"}


@pytest.mark.parametrize("attachments, line", [
    ([visual()], "1 visual - view in Tico"),
    ([visual(n=1), visual("page", 2)], "2 visuals - view in Tico"),
    ([visual(k, i) for i, k in enumerate(["image", "svg", "page", "video", "audio"])], "5 visuals - view in Tico"),
    # A plain file (a PDF, an oversize picture kept as a chip) is not a visual; nor is an older attachment with no kind.
    ([visual(), {"file_id": "f9", "version": 1, "kind": "file", "name": "terms.pdf"}, {"id": "legacy"}],
     "1 visual - view in Tico"),
])
def test_a_reply_with_visuals_reaches_slack_as_its_text_plus_one_line_to_the_message(gateway, hub, attachments, line):
    hub.settings.public_url = "https://tico.example.com"
    first = routed_reply(gateway, hub)
    thread = rows(hub, "SELECT * FROM slack_threads")[0]
    with hub.transaction() as c:
        reply = H.say(c, "bot:legal", "human:ana", "Revenue doubled <fast>.", conversation_id=thread["conversation_id"],
                      in_reply_to=first, refs={"attachments": attachments})["id"]
    gateway.tick()
    text = gateway.slack.posts[-1]["text"]
    # The whole text, then the line linking to the message in that bot's chat room (no task here), then the footer. Slack
    # wants `&` escaped inside a link too, and opens it as `&`.
    assert text == ("Revenue doubled &lt;fast&gt;.\n\n"
                    f"<https://tico.example.com/?bot=legal&amp;conversation={thread['conversation_id']}&amp;message={reply}|{line}>"
                    "\n\n(sent from Legal)")
    assert "chart" not in text.lower()


def test_a_reply_with_no_visual_or_only_files_has_no_line(gateway, hub):
    first = routed_reply(gateway, hub)
    thread = rows(hub, "SELECT * FROM slack_threads")[0]
    with hub.transaction() as c:
        H.say(c, "bot:legal", "human:ana", "Terms attached.", conversation_id=thread["conversation_id"], in_reply_to=first,
              refs={"attachments": [{"file_id": "f9", "version": 1, "kind": "file", "name": "terms.pdf"}]})
    gateway.tick()
    assert [p["text"] for p in gateway.slack.posts] == ["Done.\n\n(sent from Legal)", "Terms attached.\n\n(sent from Legal)"]


def test_a_long_reply_with_a_visual_keeps_the_line_after_the_cut(gateway, hub):
    first = routed_reply(gateway, hub)
    thread = rows(hub, "SELECT * FROM slack_threads")[0]
    with hub.transaction() as c:
        H.say(c, "bot:legal", "human:ana", "x" * (G.POST_CHARS + 50), conversation_id=thread["conversation_id"],
              in_reply_to=first, refs={"attachments": [visual()]})
    gateway.tick()
    text = gateway.slack.posts[-1]["text"]
    assert text.endswith("|1 visual - view in Tico>\n\n(sent from Legal)") and "x …\n\n<" in text


def task_reply(hub, private, attachments):
    """Legal, owner of a task Ana asked for, answers her in the task's conversation."""
    with hub.transaction() as c:
        made = H.task_create(c, "human:ana", "Q3 numbers", "Chart the quarter.", "bot:legal", lint=False, private=private)
        cid = H.task(c, made["id"])["conversation_id"]
        reply = H.say(c, "bot:legal", "human:ana", "Here is the quarter.", conversation_id=cid,
                      refs={"attachments": attachments})["id"]
    return made["id"], reply


def test_a_task_reply_with_a_visual_links_to_that_message_in_the_task(gateway, hub):
    link_task_requester(hub)
    task, reply = task_reply(hub, False, [visual(), visual("svg", 2)])
    gateway.originate()
    gateway.mirror()
    gateway.deliver()
    [post] = gateway.slack.posts
    assert post["text"] == ("Here is the quarter.\n\n"
                            f"<https://tico.example.com/?task={task}&amp;message={reply}|2 visuals - view in Tico>"
                            f"\n\n(sent from Legal on <https://tico.example.com/#/task/{task}|Q3 numbers>)")


def test_a_task_reply_names_and_links_its_task(gateway, hub):
    """A DM about a task makes sense on its own: the footer names the task (number first when it has one) and links to it."""
    link_task_requester(hub)
    task, _ = task_reply(hub, False, [])
    with hub.transaction() as c:
        c.execute("UPDATE tasks SET number=19332, title=? WHERE id=?", ("Voice <tokens> & keys", task))
    gateway.originate()
    gateway.mirror()
    gateway.deliver()
    [post] = gateway.slack.posts
    assert post["text"] == ("Here is the quarter.\n\n"
                            f"(sent from Legal on <https://tico.example.com/#/task/{task}|#19332 Voice &lt;tokens&gt; &amp; keys>)")


def test_a_private_task_reply_with_a_visual_never_reaches_slack(gateway, hub):
    """The link would only open for the task's readers, but nothing of a private task goes out in the first place:
    not the text, not the line, not its ids."""
    link_task_requester(hub)
    task, reply = task_reply(hub, True, [visual()])
    gateway.originate()
    gateway.mirror()
    gateway.deliver()
    assert gateway.slack.posts == []
    assert all(r["state"] == "cancelled" for r in rows(hub, "SELECT * FROM slack_posts WHERE message_id=?", reply))


def test_a_slack_thread_reply_links_to_its_own_room_not_the_bots_usual_chat(gateway, hub):
    """Each Slack thread is a chat room of its own beside the person's usual chat with the bot: the link names the
    reply's room, and only the people in that room find it among their chats with the bot."""
    with hub.transaction() as c:
        usual = H.say(c, "human:ana", "bot:legal", "Hi Legal.")["conversation_id"]
    first = routed_reply(gateway, hub)
    send(gateway, "<@U0BUS6WA4SY> Legal, another thing?", answers(legal=0.9))
    threads = [r["conversation_id"] for r in rows(hub, "SELECT * FROM slack_threads ORDER BY created_at")]
    assert len(set(threads + [usual])) == 3
    with hub.transaction() as c:
        reply = H.say(c, "bot:legal", "human:ana", "The chart.", conversation_id=threads[0], in_reply_to=first,
                      refs={"attachments": [visual()]})["id"]
    gateway.tick()
    text = [p["text"] for p in gateway.slack.posts if p["text"].startswith("The chart.")][0]
    assert f"conversation={threads[0]}&amp;message={reply}|1 visual - view in Tico>" in text
    with hub.read() as c:
        assert threads[0] in {r["id"] for r in H.chats_with(c, "human:ana", "bot:legal")}
        assert threads[0] not in {r["id"] for r in H.chats_with(c, "human:ben", "bot:legal")}


def test_a_task_done_post_counts_the_owners_visual_result_and_links_to_that_comment(gateway, hub):
    """The done notice itself has no visuals: the line is the owner's result comment's, posted the normal way, and it
    links to the newest such comment, which the task's conversation shows. A visual from before a reopen is not a later
    result."""
    link_task_requester(hub)
    with hub.transaction() as c:
        made = H.task_create(c, "human:ana", "Q3 numbers", "Chart the quarter.", "bot:legal", lint=False)
        old = H.task_comment(c, "bot:legal", made["id"], "First cut.", extra_refs={"attachments": [visual(n=7)]})
        chart = H.task_comment(c, "bot:legal", made["id"], "Here it is.",
                               extra_refs={"attachments": [visual(), visual("svg", 2)]})
        H.task_comment(c, "bot:legal", made["id"], "Numbers checked twice.")
        H.task_update(c, "bot:legal", made["id"], status="done", note="Charted")
    assert gateway.task_completions() == 1
    gateway.deliver()
    text = gateway.slack.posts[0]["text"]
    with hub.read() as c:
        shown = [m["id"] for m in H.task_comments(c, made["id"])]
    assert chart["id"] in shown
    assert text.endswith(f"\n<https://tico.example.com/?task={made['id']}&amp;message={chart['id']}|2 visuals - view in Tico>"
                         f"\n<https://tico.example.com/#/task/{made['id']}|Open task>")
    # Reopened and then declined with no new visual: the first result's visuals are not announced again.
    with hub.transaction() as c:
        H.task_update(c, "human:ana", made["id"], status="open")
        H.task_comment(c, "bot:legal", made["id"], "Not doing the rest.")
        H.task_update(c, "bot:legal", made["id"], status="declined", note="Out of scope")
    assert gateway.task_completions() == 1
    gateway.deliver()
    assert "visual" not in gateway.slack.posts[1]["text"] and old["id"] not in gateway.slack.posts[1]["text"]


def test_a_task_done_post_without_a_visual_result_has_no_line(gateway, hub):
    link_task_requester(hub)
    task_notice(hub)
    assert gateway.task_completions() == 1
    gateway.deliver()
    assert "visual" not in gateway.slack.posts[0]["text"]


def test_a_stored_channel_message_reaches_a_message_automation_once_and_only_after_it_was_made(readers, monkeypatch):
    """The readers' copy of a channel message is what a message automation on that channel hears: offered once, only
    while the redesign is on, and never the backlog from before the automation existed. The row stays `stored`, so
    the readers, the edit path and an @-mention that promotes it are unchanged."""
    from backend import automations
    gw, hub = readers, readers.store
    heard = []
    monkeypatch.setattr(automations, "on_slack", lambda c, event, person, channel, settings=None: (
        heard.append((event["text"], person["id"], channel["name"])) or ["run_1"]))
    with hub.transaction() as c:
        c.execute("INSERT INTO registry_metadata(key,value) VALUES('redesign',?)", ('{"enabled": true}',))
    assert gw.receive(channel_message("Before the automation")) == "stored"
    gw.clock.advance(60)
    with hub.transaction() as c:
        c.execute("INSERT INTO automations(id,name,owner_id,group_id,trigger_type,enabled,builtin,flow,version,created_at,"
                  "created_by_id,updated_at) VALUES('a1','Marketing','ana',NULL,'message',1,0,?,1,?,'human:ana',?)",
                  ('{"trigger": {"type": "message", "inbox": "slack:#marketing"}}', gw.clock(), gw.clock()))
    gw.clock.advance(60)
    assert gw.receive(channel_message("After the automation")) == "stored"
    out = gw.tick()["events"]
    after = rows(hub, "SELECT event_id FROM slack_events WHERE text=?", "After the automation")[0]["event_id"]
    assert out == [{"event_id": after, "state": "stored", "reason": "automation"}]
    assert heard == [("After the automation", "ana", "#marketing")]
    gw.clock.advance(60)
    assert gw.tick()["events"] == [] and len(heard) == 1, "offered once, never again"
    states = rows(hub, "SELECT text,state,processed_at FROM slack_events ORDER BY received_at")
    assert [s["state"] for s in states] == ["stored", "stored"], "the readers keep the row as it was"
    assert states[0]["processed_at"] is None, "the backlog is not offered"
    assert states[1]["processed_at"] is not None
