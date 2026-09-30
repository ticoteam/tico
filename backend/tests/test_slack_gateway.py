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

    def __init__(self, team=TEAM, app=APP, scopes=SCOPES, bot_user=BOT_USER, bot_id=BOT_ID):
        self.team, self.app, self.scopes, self.bot_user, self.bot_id = team, app, scopes, bot_user, bot_id
        self.posts, self.failures, self.calls = [], [], []
        self.pages, self.threads, self.unreadable = {}, {}, set()      # what history and replies return

    def auth_test(self):
        return {"ok": True, "team_id": self.team, "user_id": self.bot_user, "bot_id": self.bot_id,
                "url": "https://acme.slack.com/"}

    def bots_info(self, bot_id):
        assert bot_id == self.bot_id
        return {"id": bot_id, "app_id": self.app, "user_id": self.bot_user, "name": "Tico"}

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
def test_low_confidence_falls_back_to_the_assistant_with_the_candidates(gateway, hub):
    result = send(gateway, "<@U0BUS6WA4SY> thoughts on the thing?", answers(legal=0.4, seo=0.35, cto=0.2))
    decision = result["decision"]
    assert decision["fallback"] is True and [r["bot"] for r in decision["recipients"]] == ["coo"]
    assert [c["bot"] for c in decision["candidates"]] == ["legal", "seo", "cto"]
    assert jobs(hub) == [{"bot": "coo", "state": "queued"}]
    conversation = H.conversation(hub.connect(), rows(hub, "SELECT conversation_id FROM slack_threads")[0]["conversation_id"])
    assert conversation["scope"] == "direct", "a Slack DM is a front door, never the personal room"


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


def test_a_dm_is_a_front_door_and_the_reply_goes_back_to_the_dm(gateway, hub):
    result = send(gateway, "can you check the NDA?", answers(legal=0.8), channel=DM, kind="message")
    assert result["state"] == "routed"
    assert not any(call[0] == "conversations.info" for call in gateway.slack.calls), "a DM needs no channel lookup"
    refs = json.loads(rows(hub, "SELECT refs_json FROM messages")[0]["refs_json"])
    assert refs["slack"]["kind"] == "im" and refs["slack"]["channel_name"] == "DM"
    thread = rows(hub, "SELECT * FROM slack_threads")[0]
    with hub.transaction() as c:
        H.say(c, "bot:legal", "human:ana", "Reading it now.", conversation_id=thread["conversation_id"])
    gateway.tick()
    assert gateway.slack.posts[0]["channel"] == DM and gateway.slack.posts[0]["thread_ts"] is None, "a DM reply is not a thread"
    assert thread["thread_ts"] == "", "a DM is one context however the person types"


# ----------------------------------------------------------------------------- pure pieces
# ----------------------------------------------------------------------------- the review's cases
# ----------------------------------------------------------------------------- a bot's own app
LEGAL_APP, LEGAL_USER, LEGAL_BOT_ID, LEGAL_DM = "A0LEGAL0001", "U0LEGALBOT", "B0LEGAL0001", "D0LEGAL00"


def test_a_bots_own_app_reaches_that_bot_directly_and_answers_as_itself(hub):
    tico, legal, made = FakeSlack(), FakeSlack(app=LEGAL_APP, bot_user=LEGAL_USER, bot_id=LEGAL_BOT_ID), []
    gw = G.Gateway(hub, tico, judge_engine=FakeJudge(), clock=Clock(),
                   slack_factory=lambda bot_token, app_token: made.append((bot_token, app_token)) or legal,
                   bot_app_tokens=lambda: {"legal": {"bot_token": "xoxb-l", "app_token": "xapp-l", "revision": 1}})
    gw.verify_app()
    gw.tick()
    assert made == [("xoxb-l", "xapp-l")] and sorted(gw.bot_apps) == ["legal"]

    # A mention of Legal's app goes to Legal with no decision model; its reply is Legal's app's, bare.
    assert gw.receive(envelope(f"<@{LEGAL_USER}> is this NDA ok?", app=LEGAL_APP)) == "queued"
    result = gw.tick()["events"][-1]
    assert result["state"] == "routed" and result["decision"]["routed_by"] == "own app"
    assert [r["bot"] for r in result["decision"]["recipients"]] == ["legal"] and gw._judge.calls == []
    assert rows(hub, "SELECT body FROM messages")[-1]["body"] == "@Legal is this NDA ok?"
    thread = rows(hub, "SELECT * FROM slack_threads")[0]
    assert thread["app"] == "legal"
    with hub.transaction() as c:
        H.say(c, "bot:legal", "human:ana", "Clause 4 needs a cap.", conversation_id=thread["conversation_id"])
    gw.tick()
    assert tico.posts == [] and legal.posts == [{"channel": MARKETING, "thread_ts": thread["thread_ts"],
                                                 "text": "Clause 4 needs a cap.", "username": None,
                                                 "icon_emoji": None, "icon_url": None}]

    # A follow-up naming the app in its thread: Tico's channel copy comes first and waits for the
    # app's own event, so it is routed once, by the app, never by the decision model.
    ts = "1699999999.000100"
    copy = envelope(f"<@{LEGAL_USER}> and clause 7?", kind="message", ts=ts, thread_ts=thread["thread_ts"])
    copy["event"]["channel_type"] = "channel"
    assert gw.receive(copy) == "stored"
    assert gw.receive(envelope(f"<@{LEGAL_USER}> and clause 7?", app=LEGAL_APP, ts=ts,
                               thread_ts=thread["thread_ts"])) == "queued"
    gw.tick()
    assert [(e["state"], e["app"]) for e in events(hub) if e["ts"] == ts] == [("routed", "legal")]
    assert gw._judge.calls == [] and len(rows(hub, "SELECT 1 FROM slack_threads")) == 1

    # A DM to Legal's app, and a conversation Legal starts with Ben, stay on Legal's app; a bot
    # without an app of its own still opens Tico's DM.
    assert send(gw, "can you read the lease?", channel=LEGAL_DM, kind="message", app=LEGAL_APP)["state"] == "routed"
    with hub.transaction() as c:
        c.execute("UPDATE humans SET slack_id='U2' WHERE id='ben'")
        H.say(c, "bot:legal", "human:ben", "The lease renews in May.")
        H.say(c, "bot:cmo", "human:ben", "Campaign is live.")
    gw.tick()
    assert ("conversations.open", "U2") in legal.calls and ("conversations.open", "U2") in tico.calls
    assert [p["text"] for p in legal.posts][-1] == "The lease renews in May."
    assert tico.posts[-1]["text"] == "Campaign is live.\n\n(sent from CMO)"

    # Someone who may not write to Legal reaches nobody through its app: no assistant picks it up.
    from backend.tests.test_api import restrict
    with hub.transaction() as c:
        restrict(c, "legal", people=["ana"])
    refused = send(gw, "draft me a contract", channel="D0LEGAL02", kind="message", app=LEGAL_APP, user="U2")
    assert refused["state"] == "failed" and refused["decision"]["recipients"] == []
    assert refused["decision"]["dropped"][0]["reason"].startswith("access")
    assert rows(hub, "SELECT body FROM messages")[-1]["body"] != "draft me a contract"


def test_a_bots_app_tokens_come_from_the_vault_by_bot_variable_name(hub):
    from backend.credentials import CredentialCipher
    from backend.tests.test_credentials import FakeKMS
    cipher = CredentialCipher("test-key", FakeKMS())

    def credential(cid, env, secret):
        with hub.transaction() as c:
            ciphertext, nonce = cipher.encrypt(c, cid, secret)
            c.execute("INSERT INTO credentials(id,name,kind,env,ciphertext,nonce,created,updated,updated_by) "
                      "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET ciphertext=excluded.ciphertext,"
                      "nonce=excluded.nonce,revision=credentials.revision+1,updated=excluded.updated",
                      (cid, env, "token", env, ciphertext, nonce, H.now(), H.now(), "human:ana"))

    credential("c1", "LEGAL_SLACK_BOT_TOKEN", "xoxb-legal")
    credential("c2", "LEGAL_SLACK_APP_TOKEN", "xapp-legal")
    credential("c3", "SLACK_BOT_TOKEN", "xoxb-tico")            # the Tico app's own, not a bot's
    credential("c4", "SLACK_APP_TOKEN", "xapp-tico")
    credential("c5", "NOBODY_SLACK_BOT_TOKEN", "xoxb-nobody")   # no such bot
    credential("c6", "NOBODY_SLACK_APP_TOKEN", "xapp-nobody")
    credential("c7", "CMO_SLACK_BOT_TOKEN", "xoxb-cmo")         # half a pair
    hub.settings.credential_kms_key = "test-key"
    made = []
    gw = G.Gateway(hub, FakeSlack(), clock=Clock(), cipher=cipher, slack_factory=lambda bot_token, app_token: made.append(
        (bot_token, app_token)) or FakeSlack(app=LEGAL_APP, bot_user=LEGAL_USER, bot_id=LEGAL_BOT_ID))
    gw.verify_app()
    assert gw.connect_bot_apps() == ["legal"] and made == [("xoxb-legal", "xapp-legal")]
    assert sorted(r["target"] for r in rows(hub, "SELECT target FROM events WHERE action='credential.revealed'")) == ["c1", "c2"]
    assert gw.connect_bot_apps() == ["legal"] and len(made) == 1, "unchanged credentials are not reconnected"
    credential("c1", "LEGAL_SLACK_BOT_TOKEN", "xoxb-legal-rotated")
    assert gw.connect_bot_apps() == ["legal"] and made[-1] == ("xoxb-legal-rotated", "xapp-legal")
    with hub.transaction() as c:
        c.execute("DELETE FROM credentials WHERE id='c2'")
    assert gw.connect_bot_apps() == [] and gw.app_of(LEGAL_APP) is None


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


def edit(channel, ts, text):
    COUNTER["n"] += 1
    return {"type": "event_callback", "team_id": TEAM, "api_app_id": APP, "event_id": f"Ev{COUNTER['n']:08d}",
            "event": {"type": "message", "subtype": "message_changed", "channel": channel, "channel_type": "channel",
                      "ts": f"{1690009000 + COUNTER['n']}.000000", "message": {"ts": ts, "text": text, "user": "U1"}}}


def delete(channel, ts):
    COUNTER["n"] += 1
    return {"type": "event_callback", "team_id": TEAM, "api_app_id": APP, "event_id": f"Ev{COUNTER['n']:08d}",
            "event": {"type": "message", "subtype": "message_deleted", "channel": channel, "channel_type": "channel",
                      "ts": f"{1690009000 + COUNTER['n']}.000000", "deleted_ts": ts}}


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


def test_a_decision_the_model_refuses_ends_failed_with_the_reason_and_tells_the_human(gateway, hub):
    gateway._judge = CappedJudge({}, fail=J.JudgeError("invalid", "at most 40 questions in one call"))
    result = send(gateway, "please draft the vendor terms", channel=DM, kind="message")
    assert result["state"] == "failed" and "at most 40 questions" in result["reason"]
    event = events(hub)[0]
    assert event["state"] == "failed" and "decisions: at most 40 questions" in event["reason"]
    assert [p["channel"] for p in gateway.slack.posts] == [DM]
    assert "nobody has it" in gateway.slack.posts[0]["text"]
    gateway.clock.advance(3600)
    assert gateway.tick()["events"] == [], "a failed message is never picked up again"
    assert len(gateway._judge.calls) == 1 and jobs(hub) == []


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
