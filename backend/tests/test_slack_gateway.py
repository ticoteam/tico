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
            c.execute("INSERT INTO bot_config(bot,config_json,team,operator,description,reports_to) VALUES(?,?,?,?,?,?)",
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
    return rows(hub, "SELECT bot,state FROM jobs ORDER BY created")


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
                        "missing_scopes": ["app_mentions:read", "chat:write.customize"]}
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
    post = rows(hub, "SELECT state,attempts,next_attempt FROM slack_posts")[0]
    assert post["state"] == "rate_limited" and post["attempts"] == 2 and post["next_attempt"] > clock()
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
    assert [p["state"] for p in rows(hub, "SELECT state FROM slack_posts ORDER BY created")] == ["sent", "failed"]
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
            c.execute("INSERT INTO bot_config(bot,config_json,team,operator,description,reports_to) VALUES(?,?,?,?,?,?)",
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
    return rows(hub, "SELECT d.reader,d.count,d.skipped,d.channels_json,m.body,m.refs_json,m.from_actor,m.to_actor "
                     "FROM slack_digests d JOIN messages m ON m.id=d.message_id ORDER BY d.created, d.reader")


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
    assert cmo["from_actor"] == "keeper" and cmo["to_actor"] == "bot:cmo"
    assert "## #marketing — Marketing work log." in cmo["body"] and "Ana Rivera: Launch copy is ready for review" in cmo["body"]
    assert "Deploy 4.2" not in cmo["body"] and "Nobody reads this" not in cmo["body"]
    assert "Ben: Deploy 4.2 went out" in sent[1]["body"]
    refs = json.loads(cmo["refs_json"])["slack"]
    assert refs["channel_name"] == "#marketing" and refs["permalink"] == f"https://acme.slack.com/archives/{MARKETING}"
    assert refs["digest"]["count"] == 1 and len(refs["digest"]["event_ids"]) == 1
    assert [j["bot"] for j in jobs(hub)] == ["cmo", "cto", "seo"], "each reader gets one turn"
    assert rows(hub, "SELECT subject,scope,participants_json FROM conversations ORDER BY created")[0] == {
        "subject": "Slack channels", "scope": "direct", "participants_json": '["keeper", "bot:cmo"]'}
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


@pytest.mark.parametrize('skip', ['outside', 'wrong_person'])
def test_task_result_skips(gateway, hub, skip):
    link_task_requester(hub, slack_id={'outside': 'U8', 'wrong_person': 'U2'}[skip])
    task_notice(hub)
    assert gateway.task_completions() == 0
    gateway.originate()
    gateway.mirror()
    assert rows(hub, 'SELECT * FROM slack_posts') == []
    assert gateway.slack.posts == []
