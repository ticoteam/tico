"""Helpers that other tests share, moved out of test_slack_gateway.py so they import no test file."""

import pytest
from backend import hubdb as H
from backend import slack_gateway as G
from backend.config import Settings
from backend.store import Store, encode
from clients import judge as J
from backend import bot_settings as BS


TEAM, APP, BOT_USER, BOT_ID = "T02F3S2SD", "A0BU75YSGT1", "U0BUS6WA4SY", "B0BUNG973DY"


MARKETING, DM = "C0000000001", "D0AAAAAAA"


SCOPES = (
    "channels:history",
    "groups:history",
    "chat:write",
    "chat:write.customize",
    "app_mentions:read",
    "im:history",
    "users:read",
    "users:read.email",
)


PEOPLE = [
    {"id": "ana", "name": "Ana Rivera", "email": "ana@acme.example", "team": "leadership", "primary_for": ["*"]},
    {"id": "ben", "name": "Ben", "email": "ben@acme.example", "team": "product", "primary_for": ["product"]},
    {"id": "cara", "name": "Cara Melo", "email": "cara@acme.example", "team": "engineering"},
    # On the roster, but not admitted by registry/hub-access.yaml (no acme.example address).
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
    "cmo": {"display_name": "CMO", "description": "Marketing strategy.", "reports_to": None, "status": "active"},
    "seo": {"display_name": "SEO", "description": "Organic search.", "reports_to": "cmo", "status": "active"},
    "content-social": {
        "display_name": "Content & Social",
        "description": "Posts and social.",
        "reports_to": "cmo",
        "status": "active",
    },
    "cto": {"display_name": "CTO", "description": "Engineering alerts.", "reports_to": None, "status": "active"},
    "finance": {"display_name": "Finance", "description": "Money.", "reports_to": None, "status": "paused"},
}


USERS = {
    "U1": {"id": "U1", "team_id": TEAM, "name": "ana", "profile": {"email": "ana@acme.example"}},
    "U2": {"id": "U2", "team_id": TEAM, "name": "ben", "profile": {"email": "ben@acme.example"}},
    "U3": {"id": "U3", "team_id": TEAM, "name": "contractor", "profile": {"email": "contractor@example.com"}},
    "U4": {"id": "U4", "team_id": TEAM, "name": "newhire", "profile": {"email": "newhire@acme.example"}},
    "U5": {
        "id": "U5",
        "team_id": TEAM,
        "name": "guest",
        "is_restricted": True,
        "profile": {"email": "guest@acme.example"},
    },
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
        self.posts, self.failures, self.calls, self.unfurls = [], [], [], []
        self.pages, self.threads, self.unreadable = {}, {}, set()  # what history and replies return

    def auth_test(self):
        return {
            "ok": True,
            "team_id": self.team,
            "user_id": BOT_USER,
            "bot_id": BOT_ID,
            "url": "https://acme.slack.com/",
        }

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
        return sorted(
            (m for m in self.pages.get(channel, []) if float(m["ts"]) > float(oldest or 0)),
            key=lambda m: float(m["ts"]),
        )[:limit]

    def replies(self, channel, thread_ts, oldest="", limit=200):
        self.calls.append(("conversations.replies", channel, thread_ts, oldest))
        return sorted(
            (m for m in self.threads.get((channel, thread_ts), []) if float(m["ts"]) > float(oldest or 0)),
            key=lambda m: float(m["ts"]),
        )[:limit]

    def conversations_open(self, user_id):
        self.calls.append(("conversations.open", user_id))
        return {"id": "D0" + user_id}

    def unfurl(self, event, unfurls):
        self.calls.append(("chat.unfurl", event.get("channel")))
        if self.failures:
            raise self.failures.pop(0)
        self.unfurls.append(unfurls)
        return {"ok": True}

    def post_message(self, channel, thread_ts, text, username=None, icon_emoji=None, icon_url=None):
        self.calls.append(("chat.postMessage", channel))
        if self.failures:
            raise self.failures.pop(0)
        self.posts.append(
            {
                "channel": channel,
                "thread_ts": thread_ts,
                "text": text,
                "username": username,
                "icon_emoji": icon_emoji,
                "icon_url": icon_url,
            }
        )
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
        return {
            "model": "judge-1.13.0",
            "answers": answer,
            "usage": {"input_tokens": 500, "output_tokens": 0},
            "ms": 210,
        }


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
            (
                encode(
                    {
                        "default_user": "ana",
                        "teams": {"marketing": {"root": "cmo"}, "engineering": {"root": "cto"}},
                        "people": PEOPLE,
                    }
                ),
            ),
        )
        for slug, entry in BOTS.items():
            c.execute(
                'INSERT INTO bot_config(bot,team,owner_id,description,reports_to) VALUES(?,?,?,?,?)',
                (slug, None, 'ana', entry['description'], entry['reports_to']),
            )
            BS.write(c, slug, {'name': slug, 'runtime': 'fake'})
    return store


@pytest.fixture
def gateway(hub):
    slack, engine, clock = FakeSlack(), FakeJudge(), Clock()
    gw = G.Gateway(hub, slack, judge_engine=engine, clock=clock)
    gw.verify_app()
    gw.engine = engine
    return gw


COUNTER = {"n": 0}


def envelope(
    text,
    channel=MARKETING,
    user="U1",
    thread_ts=None,
    kind="app_mention",
    event_id=None,
    ts=None,
    team=TEAM,
    app=APP,
    **event_fields,
):
    COUNTER["n"] += 1
    ts = ts or f"{1690000000 + COUNTER['n']}.000100"
    event = {"type": kind, "user": user, "channel": channel, "ts": ts, "text": text, **event_fields}
    if kind == "message":
        event["channel_type"] = "im"
    if thread_ts:
        event["thread_ts"] = thread_ts
    return {
        "type": "event_callback",
        "team_id": team,
        "api_app_id": app,
        "event_id": event_id or f"Ev{COUNTER['n']:08d}",
        "event": event,
    }


def rows(hub, sql, *args):
    with hub.read() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def send(gateway, text, judge_answer=None, **kw):
    """One message through the whole gateway: persist, then a tick that routes it."""
    if judge_answer is not None:
        gateway.engine.answers.append(judge_answer)
    assert gateway.receive(envelope(text, **kw)) == "queued"
    return gateway.tick()["events"][-1]


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
    settings = Settings(
        db_path=tmp_path / "hub.db", slack_team_id=TEAM, slack_app_id=APP, registry_dir=registry, slack_digest_cap=5
    )
    store = Store(settings)
    store.initialize()
    with store.transaction() as c:
        H.sync_registry(
            c, {slug: {"name": slug, "runtime": "fake", **entry} for slug, entry in BOTS.items()}, {"people": PEOPLE}
        )
        c.execute(
            "INSERT INTO registry_metadata VALUES('people',?)",
            (
                encode(
                    {
                        "default_user": "ana",
                        "teams": {"marketing": {"root": "cmo"}, "engineering": {"root": "cto"}},
                        "people": PEOPLE,
                    }
                ),
            ),
        )
        for slug, entry in BOTS.items():
            c.execute(
                'INSERT INTO bot_config(bot,team,owner_id,description,reports_to) VALUES(?,?,?,?,?)',
                (slug, None, 'ana', entry['description'], entry['reports_to']),
            )
            BS.write(c, slug, {'name': slug, 'runtime': 'fake'})
    slack, engine, clock = FakeSlack(), FakeJudge(), Clock()
    gw = G.Gateway(store, slack, judge_engine=engine, clock=clock)
    gw.verify_app()
    gw.engine = engine
    gw.tick()  # the first pass: cursors start at now, nothing is delivered
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
    return rows(
        hub,
        "SELECT d.reader,d.count,d.skipped,d.channels,m.body,m.refs,m.sender_id,m.recipient_id "
        "FROM slack_digests d JOIN messages m ON m.id=d.message_id ORDER BY d.created_at, d.reader",
    )


# ----------------------------------------------------------------------------- the call's size
class CappedJudge:
    """The real contract's size and shape check on every call; answers by question id."""

    def __init__(self, scores, fail=None):
        self.scores, self.fail, self.calls = scores, fail, []

    def __call__(self, state, questions, label=None):
        self.calls.append(list(questions))
        J.validate(state, questions, label)  # a call past J.MAX_CALL_CHARS is refused, as the real call does
        if self.fail:
            raise self.fail
        return {
            "model": "judge-1.13.0",
            "usage": {},
            "ms": 1,
            "answers": {qid: {"noul": self.scores.get(qid, 0.05)} for qid in questions},
        }


def add_bots(hub, count):
    with hub.transaction() as c:
        H.sync_registry(
            c,
            {
                f"bot-{n:02d}": {
                    "name": f"bot-{n:02d}",
                    "runtime": "fake",
                    "status": "active",
                    "display_name": f"Bot {n:02d}",
                }
                for n in range(count)
            },
            {},
        )
