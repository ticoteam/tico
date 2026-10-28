"""Fakes for the redesign scenarios. Each one is a copy, cut down to what the scenarios call, of a fake the backend
suite already uses (the source line is in its docstring), except the Gmail fake, which is imported unchanged. Nothing
here reaches a network or a real service.

The judge is a replay. A call is answered from the next scripted answer when the caller passed one, else from
recordings/<key>.json (real model output, saved once by `python -m evals.redesign.record`), else MissingRecording.
REDESIGN_LIVE=1 turns on the live sample (5% of recorded calls, at most 10 a run). The live call is not built: only the
decision is, and go_live says so."""
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

import httpx

from connectors.mail.tests.fake import FakeGmailService  # noqa: F401 (the mail tests' own fake, unchanged)
RECORDINGS = Path(__file__).parent / "recordings"
LIVE_RATE, LIVE_MAX = 0.05, 10
# The company's Decisions level in the fixture (clients/judge.py normalize_level(None)). A call with no step level, such
# as the listening judge's, is asked at it, as the Gateway engine resolves it. Every recording was taken at low.
COMPANY_LEVEL = "low"
PUBLIC = [ipaddress.ip_address("93.184.216.34")]   # backend/tests/test_webhooks.py:20


class MissingRecording(LookupError):
    """No recording and no scripted answer for this judge call."""


def recording_key(state, questions, level):
    """A recording's name: a hash of what the judge was asked (state, questions and the decide level)."""
    blob = json.dumps({"state": state, "questions": questions, "level": level}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


class ReplayJudge:
    """app.state.judge for the scenarios. Same call shape as backend/tests/test_judge.py:16 FakeJudge, which keeps
    every call. `script` is a list of answer dicts, used first and in order, so a routing check gets the exact
    confidence it scripted; once it is spent, a recording answers. A scripted answer stands in for a model answer, so
    checks read routing and storage, never the model."""

    def __init__(self, script=(), fail=None):
        self.script, self.fail, self.calls = list(script), fail, []

    def __call__(self, state, questions, label=None, level=None):
        level = level or COMPANY_LEVEL
        self.calls.append({"state": state, "questions": questions, "label": label, "level": level})
        if self.fail:
            raise self.fail
        if self.script:
            # A passed script wins over a recording: a named scenario needs the exact confidence it scripted.
            return {"model": "scripted", "usage": {"input_tokens": 0, "output_tokens": 0}, "ms": 0,
                    "answers": self.script.pop(0)}
        path = RECORDINGS / f"{recording_key(state, questions, level)}.json"
        if path.exists():
            saved = json.loads(path.read_text())
            return {name: saved[name] for name in ("model", "usage", "ms", "answers")}
        raise MissingRecording(
            f"no recording recordings/{path.name} and no scripted answer for this call; re-record with "
            "`python -m evals.redesign.record` (the recorder needs live credentials: HUB_API_URL and HUB_TOKEN, "
            "or TYPESAFE_API_KEY)")


def live_sample(rng, sent_so_far):
    """Whether one recorded call also goes live this run: off unless REDESIGN_LIVE=1, 5% a call, 10 a run at most."""
    if os.environ.get("REDESIGN_LIVE") != "1" or sent_so_far >= LIVE_MAX:
        return False
    return rng.random() < LIVE_RATE


def go_live(*_args, **_kwargs):
    raise NotImplementedError("live: not built")


def attach_judge(org, engine):
    """Puts the engine where the hub reads its judge: app.state.judge, as backend/tests/test_judge.py sets it."""
    org.app.state.judge = engine
    return engine


TEAM, APP, BOT_USER, BOT_ID = "T0WF1", "A0WF1", "U0BOT", "B0BOT"
# The fixture's Slack people (recorded inputs name them by first name) and channels (ids as the recordings name them).
SLACK_PEOPLE = {"dana": ("U0DANA", "dana@acme.example"), "omar": ("U0OMAR", "omar@acme.example"),
                "priya": ("U0PRIYA", "priya@acme.example")}
SLACK_CHANNELS = {"C-release": "release", "C-platform": "platform", "C-general": "general", "C-product": "product",
                  "C-random": "random"}


class FakeSlack:
    """The Slack Web API as the gateway (backend/slack_gateway.py Gateway) calls it: the workspace and app it verifies,
    the people and channels the recordings name, and every post it made."""

    def __init__(self):
        self.posts, self.calls = [], []

    def auth_test(self):
        return {"ok": True, "team_id": TEAM, "user_id": BOT_USER, "bot_id": BOT_ID, "url": "https://acme.slack.com/"}

    def bots_info(self, bot_id):
        return {"id": bot_id, "app_id": APP, "user_id": BOT_USER, "name": "Tico"}

    def users_info(self, user_id):
        from backend.slack_gateway import SlackError
        self.calls.append(("users.info", user_id))
        for name, (uid, email) in SLACK_PEOPLE.items():
            if uid == user_id:
                return {"id": uid, "team_id": TEAM, "name": name, "profile": {"email": email}}
        raise SlackError("users.info", "user_not_found")

    def conversations_info(self, channel):
        from backend.slack_gateway import SlackError
        self.calls.append(("conversations.info", channel))
        if channel not in SLACK_CHANNELS:
            raise SlackError("conversations.info", "channel_not_found")
        return {"id": channel, "name": SLACK_CHANNELS[channel]}

    def history(self, channel, oldest="", limit=200):
        self.calls.append(("conversations.history", channel, oldest))
        return []

    def replies(self, channel, thread_ts, oldest="", limit=200):
        self.calls.append(("conversations.replies", channel, thread_ts, oldest))
        return []

    def conversations_open(self, user_id):
        self.calls.append(("conversations.open", user_id))
        return {"id": "D0" + user_id}

    def post_message(self, channel, thread_ts, text, username=None, icon_emoji=None, icon_url=None):
        self.calls.append(("chat.postMessage", channel))
        self.posts.append({"channel": channel, "thread_ts": thread_ts, "text": text, "username": username,
                           "icon_emoji": icon_emoji, "icon_url": icon_url})
        return {"ok": True, "ts": f"{1700000000 + len(self.posts)}.000100"}


def slack_envelope(message):
    """One recorded message as Socket Mode delivers it (`events_api`): a message event in a channel, or a direct
    message (channel id D-...). `ts` keeps the recording's stem, which the run's trigger reference carries."""
    is_dm = str(message["channel"]).startswith("D")
    uid = SLACK_PEOPLE[message["user"]][0]
    return {"type": "event_callback", "team_id": TEAM, "api_app_id": APP, "event_id": "Ev-" + message["ts"],
            "event": {"type": "message", "channel": message["channel"], "channel_type": "im" if is_dm else "channel",
                      "user": uid, "text": message["text"], "ts": message["ts"]}}


def hook(client, url, body=b'{"order": 42}', headers=None):
    """backend/tests/test_triggers.py:8 hook: one delivery to an incoming address, as the outside world sends it."""
    return client.post(urlsplit(url).path, content=body,
                       headers={"content-type": "application/json", **(headers or {})})


def stripe_sig(secret, body, t):
    """backend/tests/test_triggers_incoming.py:16 stripe_sig: Stripe's scheme, t=<unix>,v1=<HMAC-SHA256 of "<t>.<body>">."""
    return f"t={t},v1=" + hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()


class Receiver:
    """The far end of an outgoing webhook: backend/tests/test_webhooks.py:37 `net` fixture. Every request is kept and
    `reply` decides the answer. Pair with deliver(), which resolves every name to PUBLIC as the fixture does."""

    def __init__(self, reply=None):
        self.seen = []
        self.reply = reply or (lambda request: httpx.Response(200))
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request):
        self.seen.append(request)
        return self.reply(request)


def deliver(app, receiver, rehearsal=False):
    """Runs the outgoing webhook queue until it is empty, through the receiver (test_webhooks.py:72). Returns the count."""
    from backend import egress, webhook_delivery as D
    sent = 0
    with mock.patch.object(egress, "resolve", lambda host: PUBLIC):
        while D.step(app.state.store, app.state.vault, rehearsal=rehearsal, transport=receiver.transport):
            sent += 1
    return sent
