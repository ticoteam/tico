"""The Slack gateway: a DM to Tico or an `@Tico` in a channel wakes the bot it is for.

One process, `python -m backend.slack_gateway`, supervised by `tico-slack-gateway.service` on the
same host as the API. It holds a Socket Mode connection (an outbound websocket; nothing listens),
so there is no public receiver and no signing secret. What it does, in order, for every event:

1. **Verify.** At start-up `auth.test` and `bots.info` must name the configured workspace and
   app; every envelope must carry the same team and app id. A message from a bot or an app
   (Tico's own posts included), an edit or any other subtype, a Slack Connect channel, a guest,
   and a sender whose verified email (fresh `users.info`) is not on the roster and admitted by
   `registry/hub-access.yaml` is dropped without a reply and recorded with the reason.
2. **Persist before acknowledging.** The event goes into `slack_events` (unique on Slack's event
   id and on `channel, ts`) before the envelope is acked, so a crash never loses a message and a
   redelivery never routes one twice.
3. **Route with the decision model.** One decision per message, asked in as many calls as the
   question cap needs (`clients/judge.py` is the client, the fixed questions are
   `questions/slack-route.json`): the message, the sender, the channel and its registry purpose, the last twelve
   exchanges in the thread with the bot each went to, the thread's previous routing, and the
   active roster as state; one `noul` "should this go to <bot>?" per active bot plus "is this a
   reply to the bot that last asked here?", "does it ask for anything?" and "does it name a
   bot?". The rules (`docs/slack-gateway.md`): asks below `slack_ask_threshold` wakes nobody;
   every bot at or above `slack_route_threshold` is a recipient, best `slack_max_recipients` by
   confidence; none means the assistant (`coo`) with the candidates named; a recipient the write
   layer refuses (paused, quarantined) is dropped with the reason, and the assistant again if
   that empties the set. Every score and answer is stored on the event and on the message. A call
   that cannot be answered is retried for `ROUTE_GIVE_UP_SECONDS`; one that is refused, or still
   failing after that, ends the message as `failed` with the reason, and the human is told in the thread.
4. **Write into the hub** through `hubdb.say`, as the verified person, never as Tico: each
   recipient gets its own `direct` conversation mapped to the Slack thread in `slack_threads`
   (or continues the one it already has there). The guardrails in `docs/how-it-works.md` apply
   unchanged; the message's refs carry the channel, the permalink, the exchanges and the routing.
5. **Mirror the reply.** When a routed bot's reply lands in that conversation it is posted once in
   the same thread or DM with `chat:write.customize` (the bot's display name, its icon when the
   registry has one) and a last-line footer `(sent from <bot>)`. A confirmed rate limit is
   retried after Slack's `Retry-After`; any other Slack refusal is `failed`; a crash or a
   network fault between send and record is `uncertain` and waits for an operator
   (`docs/slack-gateway.md`, Recovery).

6. **Read the channels like an employee reads them.** Every other message in a channel Tico is
   in (`message.channels`, `message.groups`) is stored as it arrives, bots' posts included,
   with edits and deletions applied to the stored row. Each reader a channel names in
   `registry/slack-channels.yaml` (`readers:`) has a cursor per channel in `slack_reads`; every
   `slack_digest_minutes` the gateway fills any gap the socket left (`conversations.history`
   since the newest stored message, `conversations.replies` for the threads it knows), then
   hands each reader what is unread, grouped by thread with the thread so far as context, as
   one keeper-written message in that reader's channels conversation, and moves the cursors in
   the same transaction. Nothing unread, nothing sent, no turn. A reply in a thread a bot
   already has a conversation in does not wait for the hour: it goes through step 3 at once.

7. **A bot's own app.** A bot may have a Slack app of its own: its tokens are the vault
   credentials whose bot variable names are `<SLUG>_SLACK_BOT_TOKEN` and `<SLUG>_SLACK_APP_TOKEN`,
   read every `VAULT_SECONDS`, so adding or rotating them needs no restart. The gateway holds one
   more Socket Mode connection per such app. A mention of it or a DM to it goes to that bot with
   no decision model (and no assistant to fall back to), and the bot's replies in that thread or
   DM are posted by the app itself, with no footer. A thread keeps the app it started with,
   unless the bot's own app is later addressed in it; Tico's copy of a channel message that
   names a bot's app is stored and left for that app's own event.

It never runs a bot turn and never loads a bot's secrets; the tokens it holds are the Tico app's,
read by `backend.config.slack_credentials` or pasted in Settings, and each bot app's, read from the
vault. Kill switch: `TICO_SLACK_GATEWAY_ENABLED`; `TICO_SLACK_DIGEST_MINUTES=0` pauses the readers'
pass while storage goes on.
"""

import argparse
import datetime as dt
import json
import logging
import os
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

from . import hubdb as H
from . import people as P
from . import providers
from . import slack_app
from .config import Settings, slack_credentials
from clients import judge as J
from .store import Store
from .views import entries, roster

LOG = logging.getLogger("tico.slack")

API = "https://slack.com/api/"
# The three fixed questions and the label are the shared set; the per-bot nouls are built from
# the roster at run time (`questions`). The call is the one primitive every bot has.
JUDGE_SET = J.load_set("slack-route")
JUDGE_MODEL = J.MODEL
JUDGE_LABEL = JUDGE_SET["label"]
EVENT_TYPES = ("app_mention", "message")
CHANNEL_TYPES = ("channel", "group")       # stored channel messages; "im" is the fast lane
STORED_SUBTYPES = ("", "bot_message", "thread_broadcast", "file_share", "me_message")
EXCHANGES = 12                  # thread context, both for the decision model and for the routed bot
CONTEXT_REPLIES = 3             # already-read replies shown before the new ones in a digest thread
DIGEST_LINE_CHARS = 600         # one Slack message inside a digest
DIGEST_CHARS = 60_000           # the whole digest body
HISTORY_PAGE = 200              # one conversations.history / replies call
TEXT_CHARS = 4_000              # what one Slack message may carry, in and out
POST_CHARS = 3_800              # a mirrored reply, before the footer
TICK_SECONDS = 2                # how often the loop looks for replies to mirror
RETRY_SECONDS = 60              # an event the decision model could not answer waits this long before the next try
ROUTE_GIVE_UP_SECONDS = 600     # unanswered this long, a message is failed and the human told, never retried again
RATE_LIMIT_DEFAULT = 30         # seconds, when Slack's Retry-After is unreadable
MAX_POST_ATTEMPTS = 10          # rate-limit retries before a post is given up as failed
MAX_BOTS = 60
# Scopes and events the manifest carries for the gateway. Missing ones are reported at start-up,
# never guessed around: the reinstall is a person's step (connectors/README.md).
NEEDED_SCOPES = ("app_mentions:read", "im:history", "channels:history", "groups:history", "chat:write",
                 "chat:write.customize", "users:read", "users:read.email")
CUSTOMIZE_SCOPE = "chat:write.customize"
VAULT_SECONDS = 300             # how often the vault is read for tokens stored there
# A bot's own app: the vault credentials that make one, and what the app needs to take a mention
# or a DM, check who sent it, answer it and open a DM. `channels:read`/`groups:read` are optional:
# without them Tico's client looks the channel up.
BOT_APP_ENV = re.compile(r"^([A-Z][A-Z0-9_]*)_SLACK_(BOT|APP)_TOKEN$")
BOT_APP_SCOPES = ("app_mentions:read", "im:history", "chat:write", "users:read", "users:read.email", "im:write")
MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
USER_RE = MENTION_RE
CHAN_RE = re.compile(r"<#([A-Z0-9]+)(?:\|([^>]*))?>")
LINK_RE = re.compile(r"<((?:https?|mailto):[^|>]+)(?:\|([^>]*))?>")
BANG_RE = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")


class SlackError(Exception):
    """Slack answered, and said no. `code` is its error; `retry_after` is set for a rate limit."""

    def __init__(self, method, code, retry_after=None):
        super().__init__(f"Slack {method}: {code}")
        self.method, self.code, self.retry_after = method, code, retry_after


class SlackUnreachable(Exception):
    """No answer, or an unreadable one: after a post this is `uncertain`, never a retry."""


# ----------------------------------------------------------------------------- the Slack side
class SlackAPI:
    """The Web API, one attempt per call, and the Socket Mode connection. Tests replace it."""

    def __init__(self, bot_token, app_token, timeout=30):
        self.bot_token, self.app_token, self.timeout = bot_token, app_token, timeout
        self.scopes = ()
        self._socket = None

    def call(self, method, params=None, post=False):
        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        headers = {"Authorization": "Bearer " + self.bot_token, "User-Agent": "tico-slack-gateway/1"}
        url, body = API + method, None
        if post:
            body = json.dumps(params).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        elif params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, data=body, headers=headers, method="POST" if post else "GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status, hdrs, raw = response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            status, hdrs, raw = exc.code, dict(exc.headers or {}), exc.read()
        except Exception as exc:                    # URLError, socket timeout, TLS
            raise SlackUnreachable(f"Slack {method}: {type(exc).__name__}") from exc
        lowered = {k.lower(): v for k, v in hdrs.items()}
        if status == 429:
            try:
                wait = int(float(lowered.get("retry-after", RATE_LIMIT_DEFAULT)))
            except (TypeError, ValueError):
                wait = RATE_LIMIT_DEFAULT
            raise SlackError(method, "ratelimited", retry_after=max(1, wait))
        if status >= 500:
            raise SlackUnreachable(f"Slack {method}: HTTP {status}")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SlackUnreachable(f"Slack {method}: unreadable response (HTTP {status})") from exc
        if method == "auth.test" and lowered.get("x-oauth-scopes"):
            self.scopes = tuple(s.strip() for s in lowered["x-oauth-scopes"].split(",") if s.strip())
        if not data.get("ok"):
            raise SlackError(method, str(data.get("error") or "unknown_error"))
        return data

    def auth_test(self):
        return self.call("auth.test", post=True)

    def bots_info(self, bot_id):
        return self.call("bots.info", {"bot": bot_id}).get("bot") or {}

    def users_info(self, user_id):
        return self.call("users.info", {"user": user_id}).get("user") or {}

    def conversations_info(self, channel):
        return self.call("conversations.info", {"channel": channel}).get("channel") or {}

    def history(self, channel, oldest="", limit=HISTORY_PAGE):
        """Top-level messages newer than `oldest` (exclusive), oldest first, one page."""
        data = self.call("conversations.history", {"channel": channel, "oldest": oldest or None, "limit": limit})
        return sorted(data.get("messages") or [], key=lambda m: float(m.get("ts") or 0))

    def replies(self, channel, thread_ts, oldest="", limit=HISTORY_PAGE):
        """Replies in one thread newer than `oldest` (exclusive), oldest first, the root excluded."""
        data = self.call("conversations.replies", {"channel": channel, "ts": thread_ts, "oldest": oldest or None,
                                                   "limit": limit})
        return sorted((m for m in data.get("messages") or [] if str(m.get("ts")) != str(thread_ts)),
                      key=lambda m: float(m.get("ts") or 0))

    def post_message(self, channel, thread_ts, text, username=None, icon_emoji=None, icon_url=None):
        return self.call("chat.postMessage", {
            "channel": channel, "thread_ts": thread_ts, "text": text, "username": username,
            "icon_emoji": icon_emoji, "icon_url": icon_url, "unfurl_links": False, "unfurl_media": False},
            post=True)

    def conversations_open(self, user_id):
        """The IM with this Slack user, created if needed. Needs `im:write`."""
        return self.call("conversations.open", {"users": user_id}, post=True).get("channel") or {}

    def connect(self, on_envelope):
        """Open Socket Mode. `on_envelope(payload)` is called for every events_api envelope after
        it has been persisted; the ack is sent here, right after that call returns."""
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.request import SocketModeRequest
        from slack_sdk.socket_mode.response import SocketModeResponse
        from slack_sdk.web import WebClient

        client = SocketModeClient(app_token=self.app_token,
                                  web_client=WebClient(token=self.bot_token, retry_handlers=[]))

        def listener(_client, request: SocketModeRequest):
            if request.type == "events_api":
                try:
                    on_envelope(request.payload or {})
                except Exception as exc:            # a bad event never takes the socket down
                    LOG.error("Event %s not persisted: %s", request.envelope_id, type(exc).__name__)
                    return                          # no ack: Slack redelivers, dedupe absorbs it
            _client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))

        client.socket_mode_request_listeners.append(listener)
        client.connect()
        self._socket = client
        return client

    def connected(self):
        return self._socket is not None and bool(self._socket.is_connected())

    def close(self):
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None


class BotApp:
    """One bot's own Slack app: its client, who it is in Slack, and the credential revisions it was
    connected with, so a rotated token reconnects."""

    def __init__(self, bot, slack, revision):
        self.bot, self.slack, self.revision = bot, slack, revision
        self.app_id = self.bot_user_id = ""
        self.name = bot


# ----------------------------------------------------------------------------- pure helpers
def humanize(text, names=None):
    """Slack markup as a person reads it. `names` maps user ids to what to call them."""
    names = names or {}
    s = str(text or "")
    s = USER_RE.sub(lambda m: "@" + names.get(m.group(1), m.group(1)), s)
    s = CHAN_RE.sub(lambda m: "#" + (m.group(2) or m.group(1)), s)
    s = LINK_RE.sub(lambda m: (f"{m.group(2)} ({m.group(1)})" if m.group(2) else m.group(1)), s)
    s = BANG_RE.sub(lambda m: "@" + m.group(1), s)
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()


def slack_escape(text):
    """Text as Slack's parser must see it: `&`, `<` and `>` escaped, so a bot's `<!channel>` is
    shown as text rather than paging the channel and "x < y" survives."""
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def permalink(team_url, channel, ts, thread_ts=None):
    base = (team_url or "https://slack.com/").rstrip("/")
    link = f"{base}/archives/{channel}/p{str(ts).replace('.', '')}"
    if thread_ts and thread_ts != ts:
        link += f"?thread_ts={thread_ts}&cid={channel}"
    return link


def admitted(acl, email):
    """`registry/hub-access.yaml`: the owner, the allowed list, or an allowed domain."""
    email = str(email or "").strip().lower()
    if not email or "@" not in email:
        return False
    acl = acl if isinstance(acl, dict) else {}
    if email == str(acl.get("owner") or "").strip().lower():
        return True
    if email in {str(e or "").strip().lower() for e in acl.get("allowed") or []}:
        return True
    domain = email.rsplit("@", 1)[1]
    return domain in {str(d or "").strip().lower().lstrip("@") for d in acl.get("allowed_domains") or []}


def is_guest(user):
    return bool(user.get("is_restricted") or user.get("is_ultra_restricted") or user.get("is_stranger"))


def is_external(channel):
    return any(channel.get(k) for k in ("is_ext_shared", "is_pending_ext_shared", "is_shared", "is_org_shared"))


def event_from(payload, bot_user_id):
    """The one event this gateway handles out of an events_api payload, or (None, why).

    A DM that also mentions Tico arrives twice, as `message` and as `app_mention`; the second
    is a duplicate on `channel, ts` and is dropped at persist time.

    `thread_ts` is the context key: the thread root in a channel, and "" in a DM, because a DM is
    one conversation per bot however the person types (at the bottom, or inside a reply thread).
    `reply_ts` is where a reply to this message is posted: the thread root in a channel; in a DM
    the thread the message was in, or "" for the bottom of the DM.
    """
    event = (payload or {}).get("event") or {}
    kind = str(event.get("type") or "")
    if kind not in EVENT_TYPES:
        return None, "type"
    if kind == "message" and event.get("channel_type") in CHANNEL_TYPES:
        return channel_event_from(payload, bot_user_id)
    if kind == "message" and event.get("channel_type") != "im":
        return None, "not_dm"
    if event.get("bot_id") or event.get("subtype") or event.get("user") == bot_user_id:
        return None, "bot_or_subtype"
    if not event.get("user") or not event.get("channel") or not event.get("ts"):
        return None, "shape"
    text = str(event.get("text") or "").strip()
    if not text and not event.get("files"):
        return None, "empty"
    im = kind == "message" or str(event["channel"]).startswith("D")
    in_thread = str(event.get("thread_ts") or "")
    return {"op": "route", "event_id": str(payload.get("event_id") or ""), "team_id": str(payload.get("team_id") or ""),
            "api_app_id": str(payload.get("api_app_id") or ""),
            "event_type": "message.im" if kind == "message" else kind,
            "channel": str(event["channel"]), "user_id": str(event["user"]), "ts": str(event["ts"]),
            "thread_ts": "" if im else (in_thread or str(event["ts"])),
            "reply_ts": in_thread if im else (in_thread or str(event["ts"])),
            "channel_kind": "im" if im else "channel", "author": "human", "author_name": None,
            "text": (text or "(a file with no text)")[:TEXT_CHARS]}, None


def message_text(message):
    """What a message says, for a person reading it later: its text, else what its attachments
    or blocks carry (alert feeds post with an empty `text` and everything in an attachment)."""
    text = str(message.get("text") or "").strip()
    if text:
        return text
    parts = []
    for att in message.get("attachments") or []:
        for key in ("pretext", "title", "text", "fallback"):
            value = str((att or {}).get(key) or "").strip()
            if value and value not in parts:
                parts.append(value)
                if key == "fallback":
                    break
    if not parts and message.get("files"):
        parts.append("(a file with no text)")
    return "\n".join(parts)


def channel_message(channel, channel_type, message, bot_user_id):
    """A stored row out of one channel message (an event or a history page), or (None, why)."""
    subtype = str(message.get("subtype") or "")
    if subtype not in STORED_SUBTYPES:
        return None, "subtype"
    ts = str(message.get("ts") or "")
    if not channel or not ts:
        return None, "shape"
    text = message_text(message)
    if not text:
        return None, "empty"
    is_bot = bool(message.get("bot_id")) or subtype == "bot_message" or str(message.get("user") or "") == bot_user_id
    user_id = str(message.get("user") or message.get("bot_id") or "")
    if not user_id:
        return None, "shape"
    in_thread = str(message.get("thread_ts") or "")
    return {"op": "store", "channel": str(channel), "ts": ts, "thread_ts": in_thread or ts, "reply_ts": in_thread or ts,
            "channel_kind": "channel", "event_type": "message." + ("group" if channel_type == "group" else "channel") + "s",
            "user_id": user_id, "author": "bot" if is_bot else "human",
            "author_name": str(message.get("username") or (message.get("bot_profile") or {}).get("name") or "") or None,
            "text": text[:TEXT_CHARS]}, None


def channel_event_from(payload, bot_user_id):
    """A `message.channels` / `message.groups` event: something to store, edit or delete."""
    event = (payload or {}).get("event") or {}
    head = {"event_id": str(payload.get("event_id") or ""), "team_id": str(payload.get("team_id") or ""),
            "api_app_id": str(payload.get("api_app_id") or "")}
    channel = str(event.get("channel") or "")
    subtype = str(event.get("subtype") or "")
    if subtype == "message_changed":
        inner = event.get("message") or {}
        text = message_text(inner)
        if not channel or not inner.get("ts") or not text:
            return None, "shape"
        return {**head, "op": "edit", "channel": channel, "ts": str(inner["ts"]), "text": text[:TEXT_CHARS],
                "edited_ts": str(event.get("ts") or "")}, None
    if subtype == "message_deleted":
        ts = str(event.get("deleted_ts") or (event.get("previous_message") or {}).get("ts") or "")
        if not channel or not ts:
            return None, "shape"
        return {**head, "op": "delete", "channel": channel, "ts": ts}, None
    row, why = channel_message(channel, str(event.get("channel_type") or ""), event, bot_user_id)
    if not row:
        return None, why
    return {**head, **row}, None


def registry_channels(registry_dir):
    try:
        data = yaml.safe_load((registry_dir / "slack-channels.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}
    rows = data.get("channels") if isinstance(data, dict) else data
    out = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("id"):
            readers = row.get("readers") or []
            if isinstance(readers, str):
                readers = [readers]
            try:
                hours = max(0.0, float(row.get("digest_hours") or 0))
            except (TypeError, ValueError):
                hours = 0.0
            out[str(row["id"])] = {"name": str(row.get("name") or ""), "purpose": str(row.get("purpose") or ""),
                                   "post": row.get("post") is not False, "digest_hours": hours,
                                   "readers": [str(r).strip() for r in readers if str(r or "").strip()]}
    return out


def channel_link(team_url, channel):
    return f"{(team_url or 'https://slack.com/').rstrip('/')}/archives/{channel}"


def slack_time(ts):
    try:
        return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OverflowError, OSError):
        return str(ts)


def ends_with_question(body):
    lines = [line.strip() for line in str(body or "").splitlines() if line.strip()]
    return bool(lines) and "?" in lines[-1]


# ----------------------------------------------------------------------------- the gateway
class Gateway:
    """Verify, persist, route, write, mirror. One instance per process; tests drive `tick()`."""

    def __init__(self, store, slack, judge_engine=None, clock=None, slack_factory=None, cipher=None,
                 bot_app_tokens=None):
        self.store, self.slack = store, slack
        self.settings = store.settings
        self._judge = judge_engine
        self.clock = clock or H.now
        self.team_id = self.settings.slack_team_id
        self.app_id = self.settings.slack_app_id
        self.bot_user_id = ""
        self.team_url = ""
        self.customize = True
        self.wake = threading.Event()
        self.stop = threading.Event()
        self._customize_warned = False
        self._names = {}                    # Slack user id -> what a digest calls them
        self._unreadable = set()            # channels history refused (not a member, no scope)
        self.next_digest = self.clock()     # the first pass sets the readers' cursors
        self.pin_workspace = False          # tokens the owner pasted into Tico define the workspace
        self.heartbeat = None               # called every loop turn; the process uses it for health
        self._auth = None                   # who may write to which bot (backend/bot_access.py)
        # Tokens stored in the vault. Tests hand in the Slack fakes and the tokens; the process makes
        # clients and reads the vault. `listening` is set once Tico's own socket is up.
        self.make_slack = slack_factory or SlackAPI
        self._cipher = cipher
        self.next_vault = self.clock()
        self.listening = False
        # Bots' own apps.
        self._bot_app_tokens = bot_app_tokens
        self.bot_apps = {}                  # bot slug -> BotApp, verified (and listening in the process)
        self._bot_app_logged = {}           # bot slug -> the revision whose failure was logged

    # ------------------------------------------------------------------ start-up
    @property
    def judge(self):
        if self._judge is None and self.settings.typesafe_api_key:
            self._judge = J.direct(self.settings.typesafe_api_key)
        if self._judge is not None:
            return self._judge
        # No TypeSafe key: the company's own provider answers, when the server holds its API key.
        # Looked up each time, because the owner can change the provider choice while this runs.
        from .judge import fallback_engine
        with self.store.read() as c:
            return fallback_engine(providers.load(c, self.settings))

    def verify_app(self):
        """Refuse to start unless the token is the configured workspace's and app's."""
        auth = self.slack.auth_test()
        team = str(auth.get("team_id") or "")
        if not self.team_id and self.pin_workspace:
            self.team_id = team
        if not self.team_id:
            raise RuntimeError("SLACK_TEAM_ID is not configured; the gateway does not guess a workspace")
        if team != self.team_id:
            raise RuntimeError(f"Slack token belongs to workspace {team}, not {self.team_id}")
        bot = self.slack.bots_info(str(auth.get("bot_id") or ""))
        app = str(bot.get("app_id") or "")
        if not app:
            raise RuntimeError("bots.info did not name the app this token belongs to")
        if self.app_id and app != self.app_id:
            raise RuntimeError(f"Slack token belongs to app {app}, not {self.app_id}")
        self.app_id, self.bot_user_id, self.team_url = app, str(auth.get("user_id") or ""), str(auth.get("url") or "")
        if self.team_url.startswith("https://") and self.team_url.rstrip("/").endswith(".slack.com"):
            with self.store.transaction() as c:
                c.execute("INSERT INTO registry_metadata(key,value_json) VALUES('slack_workspace_url',?) "
                          "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                          (json.dumps(self.team_url),))
        scopes = tuple(getattr(self.slack, "scopes", ()) or ())
        missing = [s for s in NEEDED_SCOPES if scopes and s not in scopes]
        self.customize = not scopes or CUSTOMIZE_SCOPE in scopes
        if missing:
            LOG.warning("Slack app %s lacks scopes %s; reinstall it from connectors/slack-app-manifest.yaml "
                        "(mentions need app_mentions:read; replies show as Tico without %s)",
                        app, ",".join(missing), CUSTOMIZE_SCOPE)
        if self.judge is None:
            LOG.warning("No decision model (TypeSafe key or a provider API key): every message routes to %s until one is configured",
                        self.settings.assistant_bot)
        return {"team_id": team, "app_id": app, "bot_user_id": self.bot_user_id, "missing_scopes": missing}

    def recover(self):
        """A post that was mid-flight when the process died is uncertain, not retried."""
        with self.store.transaction() as c:
            rows = c.execute("SELECT message_id FROM slack_posts WHERE state='sending'").fetchall()
            for row in rows:
                c.execute("UPDATE slack_posts SET state='uncertain',error=?,updated=? WHERE message_id=?",
                          ("gateway restarted while sending", self.clock(), row["message_id"]))
        if rows:
            LOG.warning("%d post(s) were mid-flight at the last stop and are now uncertain", len(rows))
        return len(rows)

    # ------------------------------------------------------------------ tokens in the vault
    def _vault(self, want):
        """Bot variable name -> (credential id, revision, secret) for the stored credentials `want` accepts,
        the newest per name. Only what is wanted is decrypted. Only a credential administrator can
        store one, so a name is as trusted as the vault."""
        if not self.settings.credential_kms_key:
            return {}
        if self._cipher is None:
            from .credentials import CredentialCipher
            self._cipher = CredentialCipher(self.settings.credential_kms_key)
        rows = {}
        with self.store.read() as c:
            for row in c.execute("SELECT id,env,nonce,ciphertext,revision FROM credentials "
                                 "WHERE env LIKE '%SLACK%TOKEN' AND ciphertext IS NOT NULL ORDER BY updated, id"):
                if want(row["env"]):
                    rows[row["env"]] = row
            return {env: (row["id"], row["revision"], self._cipher.decrypt(c, row).strip()) for env, row in rows.items()}

    # ------------------------------------------------------------------ bots' own apps
    def vault_tokens(self):
        """Bot slug -> its app's tokens, from the vault: the newest credential with each bot variable name
        `<SLUG>_SLACK_BOT_TOKEN` / `<SLUG>_SLACK_APP_TOKEN`, for a bot that exists, both present."""
        if self._bot_app_tokens is not None:
            return self._bot_app_tokens()
        found = {}
        with self.store.read() as c:
            for env, value in self._vault(BOT_APP_ENV.match).items():
                match = BOT_APP_ENV.match(env)
                slug = match.group(1).lower().replace("_", "-")
                if H.bot(c, slug):
                    found.setdefault(slug, {})["bot_token" if match.group(2) == "BOT" else "app_token"] = value
        out = {}
        for slug, pair in found.items():
            if set(pair) != {"bot_token", "app_token"}:
                continue
            out[slug] = {"ids": [pair[k][0] for k in ("bot_token", "app_token")],
                         "revision": tuple(pair[k][:2] for k in ("bot_token", "app_token")),
                         "bot_token": pair["bot_token"][2], "app_token": pair["app_token"][2]}
        return out

    def verify_bot_app(self, app):
        """Refuse a bot's app unless its token is this workspace's and a different app from Tico's."""
        auth = app.slack.auth_test()
        team = str(auth.get("team_id") or "")
        if team != self.team_id:
            raise RuntimeError(f"its token belongs to workspace {team}, not {self.team_id}")
        info = app.slack.bots_info(str(auth.get("bot_id") or ""))
        app_id = str(info.get("app_id") or "")
        if not app_id:
            raise RuntimeError("bots.info did not name the app its token belongs to")
        if app_id == self.app_id:
            raise RuntimeError("its token is the Tico app's, not an app of its own")
        taken = [other.bot for other in self.bot_apps.values() if other.app_id == app_id and other.bot != app.bot]
        if taken:
            raise RuntimeError(f"app {app_id} is already {taken[0]}'s")
        app.app_id, app.bot_user_id = app_id, str(auth.get("user_id") or "")
        with self.store.read() as c:
            app.name = (H.bot(c, app.bot) or {}).get("display_name") or app.bot
        scopes = tuple(getattr(app.slack, "scopes", ()) or ())
        missing = [s for s in BOT_APP_SCOPES if scopes and s not in scopes]
        if missing:
            LOG.warning("%s's Slack app %s lacks scopes %s (connectors/slack-bot-app-manifest.yaml)",
                        app.bot, app_id, ",".join(missing))
        return missing

    def connect_bot_apps(self):
        """Match the connected bot apps to the vault: connect a new one, reconnect a rotated one,
        drop one whose credentials are gone. A failure is logged once per revision and tried
        again next time. Returns the connected bots."""
        self.next_vault = H.shift(self.clock(), seconds=VAULT_SECONDS)
        try:
            wanted = self.vault_tokens()
        except Exception as exc:                    # the vault or KMS: keep what is connected
            LOG.error("Bots' Slack apps not read from the vault: %s", type(exc).__name__)
            return sorted(self.bot_apps)
        for slug in [s for s, app in self.bot_apps.items()
                     if s not in wanted or wanted[s]["revision"] != app.revision]:
            self.drop_bot_app(slug)
        for slug, tokens in sorted(wanted.items()):
            if slug in self.bot_apps:
                continue
            app = BotApp(slug, self.make_slack(tokens["bot_token"], tokens["app_token"]), tokens["revision"])
            try:
                self.verify_bot_app(app)
                if self.listening:
                    app.slack.connect(lambda payload: self.receive(payload))
            except Exception as exc:                # a wrong token, Slack down: next time again
                if self._bot_app_logged.get(slug) != tokens["revision"]:
                    LOG.error("%s's own Slack app not connected: %s", slug,
                              exc if isinstance(exc, (RuntimeError, SlackError)) else type(exc).__name__)
                    self._bot_app_logged[slug] = tokens["revision"]
                if hasattr(app.slack, "close"):
                    app.slack.close()
                continue
            self.bot_apps[slug] = app
            self._bot_app_logged.pop(slug, None)
            if tokens.get("ids"):
                with self.store.transaction() as c:
                    for cid in tokens["ids"]:
                        H.event(c, H.KEEPER, "credential.revealed", cid, {"for": "slack gateway", "bot": slug})
            LOG.info("Connected %s's own Slack app %s (bot user %s)", slug, app.app_id, app.bot_user_id)
        return sorted(self.bot_apps)

    def drop_bot_app(self, slug):
        app = self.bot_apps.pop(slug, None)
        if app is not None:
            if hasattr(app.slack, "close"):
                app.slack.close()
            LOG.info("Disconnected %s's own Slack app %s", slug, app.app_id)

    def app_of(self, api_app_id):
        """The bot whose own app this is, "" for Tico, None for an app this gateway does not hold."""
        if api_app_id and api_app_id == self.app_id:
            return ""
        for app in list(self.bot_apps.values()):
            if app.app_id and app.app_id == api_app_id:
                return app.bot
        return None

    def mention_names(self):
        """Slack user id -> what a mention of it reads as: Tico, and each bot app by its bot's name."""
        names = {app.bot_user_id: app.name for app in list(self.bot_apps.values()) if app.bot_user_id}
        names[self.bot_user_id] = self.settings.assistant_name
        return names

    # ------------------------------------------------------------------ ingress
    def receive(self, payload):
        """Persist an envelope's event before it is acknowledged. Returns what happened."""
        app = self.app_of(str(payload.get("api_app_id") or "")) \
            if str(payload.get("team_id") or "") == self.team_id else None
        own = self.bot_apps.get(app) if app else None
        if app is None or (app and own is None):
            LOG.warning("Envelope from another workspace or app (%s/%s) ignored",
                        payload.get("team_id"), payload.get("api_app_id"))
            return "foreign"
        event, why = event_from(payload, own.bot_user_id if own else self.bot_user_id)
        if not event:
            return why
        if not event["event_id"]:
            return "shape"
        if app and event["op"] != "route":
            return "not_for_bot_app"        # channels are stored and read through Tico's app
        event["app"] = app
        if event["op"] in ("edit", "delete"):
            return self.amend(event)
        with self.store.transaction() as c:
            outcome = self.persist(c, event)
        if outcome == "queued":
            self.wake.set()
        return outcome

    def persist(self, c, event):
        """One message into `slack_events`: `queued` for the fast lane, `stored` for the readers,
        `duplicate` when it is already there. Pure store; the caller holds the transaction."""
        raw = event["text"]
        event["text"] = humanize(raw, self.mention_names())[:TEXT_CHARS] or raw
        state = "received"
        if event["op"] == "store":
            # A person's reply in a thread a bot already talks in is for that bot, now; anything
            # else in the channel waits for the readers' pass. A message naming a bot's own app is
            # that app's: its own mention event takes the stored row below, never the decision model first.
            names_own_app = any("<@" + app.bot_user_id in raw for app in list(self.bot_apps.values()) if app.bot_user_id)
            attached = not names_own_app and event["author"] == "human" and event["thread_ts"] != event["ts"] and c.execute(
                "SELECT 1 FROM slack_threads WHERE channel=? AND thread_ts=?",
                (event["channel"], event["thread_ts"])).fetchone() is not None
            state = "received" if attached else "stored"
        now = self.clock()
        cursor = c.execute(
            "INSERT OR IGNORE INTO slack_events(event_id,team_id,channel,channel_kind,thread_ts,reply_ts,ts,"
            "user_id,event_type,text,received,state,author,author_name,updated,app) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event["event_id"], event["team_id"], event["channel"], event["channel_kind"], event["thread_ts"],
             event["reply_ts"], event["ts"], event["user_id"], event["event_type"], event["text"], now, state,
             event["author"], event["author_name"], now, event.get("app") or ""))
        if cursor.rowcount == 1:
            return "queued" if state == "received" else "stored"
        if event["op"] == "route":
            # The channel copy of an `@Tico` can arrive before the mention itself: the stored
            # row becomes the mention, so it is routed once and never twice.
            promoted = c.execute(
                "UPDATE slack_events SET state='received',event_type=?,reply_ts=?,app=?,processed=NULL,updated=? "
                "WHERE channel=? AND ts=? AND state='stored'",
                (event["event_type"], event["reply_ts"], event.get("app") or "", now, event["channel"],
                 event["ts"])).rowcount
            if promoted:
                return "queued"
        return "duplicate"

    def amend(self, event):
        """An edit or a deletion in a channel, applied to the stored row; unknown rows are ignored."""
        now = self.clock()
        with self.store.transaction() as c:
            if event["op"] == "edit":
                text = humanize(event["text"], self.mention_names())[:TEXT_CHARS] or event["text"]
                changed = c.execute("UPDATE slack_events SET text=?,edited=?,updated=? WHERE channel=? AND ts=? AND deleted IS NULL",
                                    (text, event.get("edited_ts") or now, now, event["channel"], event["ts"])).rowcount
            else:
                changed = c.execute("UPDATE slack_events SET deleted=?,updated=? WHERE channel=? AND ts=? AND deleted IS NULL",
                                    (now, now, event["channel"], event["ts"])).rowcount
        return ("edited" if event["op"] == "edit" else "deleted") if changed else "unknown"

    def pending(self):
        """Events not yet handled; one the decision model could not answer waits RETRY_SECONDS before another try."""
        with self.store.read() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM slack_events WHERE state='received' AND (processed IS NULL OR processed<=?) "
                "ORDER BY received, ts LIMIT 50", (H.shift(self.clock(), seconds=-RETRY_SECONDS),))]

    def verify_sender(self, c, event, client=None):
        """The verified person behind the event, or (None, reason). Network first, then the roster."""
        try:
            user = (client or self.slack).users_info(event["user_id"])
        except (SlackError, SlackUnreachable) as exc:
            return None, None, f"users.info: {getattr(exc, 'code', type(exc).__name__)}"
        if user.get("is_bot") or user.get("id") == "USLACKBOT":
            return None, user, "sender is a bot"
        if user.get("deleted"):
            return None, user, "sender is deactivated"
        if str(user.get("team_id") or "") != self.team_id:
            # A DM never goes through conversations.info, so the Slack Connect check above never
            # sees it; the sender's own workspace is the one check that covers every event.
            return None, user, "sender is outside the workspace"
        if is_guest(user):
            return None, user, "sender is a guest"
        email = str((user.get("profile") or {}).get("email") or "").strip().lower()
        if not email:
            return None, user, "sender has no verified email"
        from . import access
        if not admitted(access.acl(c, self.settings), email):
            return None, user, "sender is not admitted by hub-access"
        person = P.person_by_email(email, roster(c))
        if not person:
            return None, user, "sender is not on the roster"
        if not H.human(c, person["id"]):
            return None, user, "sender is not a hub person"
        return person, user, None

    def verify_channel(self, event, own=None):
        dm = {"kind": "im", "name": "DM", "purpose": "a direct message to " + (own.name if own else self.settings.assistant_name)}
        if event["channel_kind"] == "im":
            return dm, None
        channel, why = None, None
        # A bot's own app asks first; without channels:read it cannot, and Tico's client answers.
        for client in ([own.slack] if own else []) + [self.slack]:
            try:
                channel = client.conversations_info(event["channel"])
                break
            except (SlackError, SlackUnreachable) as exc:
                why = f"conversations.info: {getattr(exc, 'code', type(exc).__name__)}"
        if channel is None:
            return None, why
        if is_external(channel):
            return None, "channel is shared outside the workspace"
        if channel.get("is_im"):
            return dm, None
        registry = registry_channels(self.settings.registry_dir).get(event["channel"]) or {}
        name = str(channel.get("name") or registry.get("name") or event["channel"])
        return {"kind": "channel", "name": "#" + name.lstrip("#"), "purpose": registry.get("purpose") or ""}, None

    # ------------------------------------------------------------------ the state the decision model reads
    def blocked(self, c, person):
        """The bots this person may not send requests to. A Slack message reaches a bot as the
        person's own message, so it needs the same Write on the bot that their chat in Tico does."""
        from .auth import Auth, Problem
        if self._auth is None:
            self._auth = Auth(self.store)
        self._auth.sync_access(c)
        try:
            who = self._auth.identity_for_actor(c, "human:" + person["id"])
        except Problem:
            return {row[0] for row in c.execute("SELECT bot FROM bot_config")}
        return {slug for slug, level in self._auth.bot_accesses(c, who).items() if not level["write"]}

    def fleet(self, c, person=None):
        """Active bots only: slug, display name, team, description, reports_to. With `person`, only
        the ones they may write to: the decision model never hears of the others."""
        people = roster(c)
        configs = entries(c)
        blocked = self.blocked(c, person) if person is not None else set()
        out = []
        for row in c.execute("SELECT slug,display_name FROM bots WHERE state='active' ORDER BY slug LIMIT ?", (MAX_BOTS,)):
            if row["slug"] in blocked:
                continue
            config = configs.get(row["slug"]) or {}
            out.append({"slug": row["slug"], "name": row["display_name"] or row["slug"],
                        "team": P.team_of(row["slug"], configs, people),
                        "description": str(config.get("description") or "")[:400],
                        "reports_to": config.get("reports_to")})
        return out

    def exchanges(self, c, channel, thread_ts):
        """The last twelve lines of this Slack thread: who wrote each, and the bot it went to."""
        rows = []
        for r in c.execute("SELECT ts,actor,text,routing_json,state,author,author_name,user_id FROM slack_events "
                           "WHERE channel=? AND thread_ts=? AND state IN ('routed','recorded','stored') AND deleted IS NULL "
                           "ORDER BY ts DESC LIMIT ?", (channel, thread_ts, EXCHANGES)):
            routing = json.loads(r["routing_json"] or "{}")
            who = r["actor"] or ((r["author_name"] or r["user_id"]) if r["state"] == "stored" else "human")
            rows.append({"ts": r["ts"], "from": who, "text": r["text"][:1000],
                         "to": [x["bot"] for x in routing.get("recipients") or []]})
        for r in c.execute("SELECT slack_ts,bot,text FROM slack_posts WHERE channel=? AND (?='' OR thread_ts=?) "
                           "AND state='sent' ORDER BY created DESC LIMIT ?", (channel, thread_ts, thread_ts, EXCHANGES)):
            rows.append({"ts": r["slack_ts"] or "", "from": "bot:" + r["bot"], "text": r["text"][:1000], "to": []})
        rows.sort(key=lambda x: float(x["ts"] or 0))
        return rows[-EXCHANGES:]

    def previous_routing(self, c, channel, thread_ts):
        """How this thread was routed last time, and whether each bot is still waiting on a reply."""
        row = c.execute("SELECT routing_json FROM slack_events WHERE channel=? AND thread_ts=? AND state='routed' "
                        "ORDER BY ts DESC LIMIT 1", (channel, thread_ts)).fetchone()
        if not row:
            return None
        routing = json.loads(row["routing_json"] or "{}")
        threads = {r["bot"]: r["conversation_id"] for r in
                   c.execute("SELECT bot,conversation_id FROM slack_threads WHERE channel=? AND thread_ts=?", (channel, thread_ts))}
        recipients = []
        for one in routing.get("recipients") or []:
            # Waiting on a reply means the bot's question is the last thing in the conversation;
            # once the person has answered, `write` links the answer and the bot is not waiting.
            last = c.execute("SELECT from_actor,kind,body FROM messages WHERE conversation_id=? "
                             "ORDER BY created DESC LIMIT 1", (threads.get(one["bot"]),)).fetchone()
            recipients.append({**one, "open_ask": bool(last) and last["from_actor"] == "bot:" + one["bot"]
                               and (last["kind"] == "ask" or ends_with_question(last["body"]))})
        return {"recipients": recipients, "fallback": bool(routing.get("fallback")),
                "reason": routing.get("reason"), "routed_by": routing.get("routed_by")}

    def questions(self, fleet):
        """One noul per active bot, then the set's fixed three (`questions/slack-route.json`)."""
        out = {}
        for bot in fleet:
            manager = f" It reports to {bot['reports_to']}." if bot.get("reports_to") else ""
            team = f" ({bot['team']} team)" if bot.get("team") else ""
            out["bot:" + bot["slug"]] = {"type": "noul", "instructions":
                f"This message should be handled by {bot['name']}{team}: {bot['description'] or 'no description'}.{manager}"}
        out.update(JUDGE_SET["questions"])
        return out

    def ask(self, state, fleet):
        """The decision model's answers to every question, in calls of at most `J.MAX_QUESTIONS`.

        The questions are independent and their ids unique, so the calls' answers merge into one
        map. A call that fails fails the whole ask; nothing is routed from half the answers."""
        questions = self.questions(fleet)
        ids = list(questions)
        answers = {}
        for start in range(0, len(ids), J.MAX_QUESTIONS):
            batch = {qid: questions[qid] for qid in ids[start:start + J.MAX_QUESTIONS]}
            answers.update(self.judge(state, batch, JUDGE_LABEL)["answers"])
        return answers

    def state(self, c, event, person, channel):
        return {"message": {"text": event["text"], "from": {"id": person["id"], "name": person["name"],
                                                            "team": person.get("team") or None,
                                                            "primary_for": person.get("primary_for") or []},
                            "where": channel},
                "thread": self.exchanges(c, event["channel"], event["thread_ts"]),
                "previous_routing": self.previous_routing(c, event["channel"], event["thread_ts"]),
                "roster": self.fleet(c, person)}

    # ------------------------------------------------------------------ the rules
    def decide(self, answers, fleet):
        """The decision model's answers, through the rules, to a routing decision. Pure."""
        def noul(key):
            try:
                return round(max(0.0, min(1.0, float((answers.get(key) or {}).get("noul") or 0))), 3)
            except (TypeError, ValueError):
                return 0.0
        scores = {bot["slug"]: noul("bot:" + bot["slug"]) for bot in fleet}
        decision = {"routed_by": "judge", "model": JUDGE_MODEL, "label": JUDGE_LABEL, "scores": scores, "asks": noul("asks"),
                    "reply_to_last": noul("reply_to_last"), "names_bot": noul("names_bot"),
                    "thresholds": {"route": self.settings.slack_route_threshold, "ask": self.settings.slack_ask_threshold,
                                   "max": self.settings.slack_max_recipients},
                    "recipients": [], "candidates": [], "fallback": False, "dropped": [], "reason": ""}
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        decision["candidates"] = [{"bot": slug, "confidence": p} for slug, p in ranked[:3]]
        if decision["asks"] < self.settings.slack_ask_threshold:
            decision["reason"] = "asks for nothing; recorded, nobody woken"
            return decision
        chosen = [(slug, p) for slug, p in ranked if p >= self.settings.slack_route_threshold]
        if len(chosen) > self.settings.slack_max_recipients:
            decision["dropped"] += [{"bot": slug, "confidence": p, "reason": "over the recipient cap"}
                                    for slug, p in chosen[self.settings.slack_max_recipients:]]
            chosen = chosen[:self.settings.slack_max_recipients]
        if not chosen:
            decision["fallback"] = True
            asker = self.fallback_bot([bot["slug"] for bot in fleet])
            if asker:
                decision["reason"] = "no bot at threshold; " + ("the assistant" if asker == self.settings.assistant_bot
                                                               else asker) + " asks"
                chosen = [(asker, scores.get(asker, 0.0))]
            else:
                decision["reason"] = "no bot at threshold, and no assistant or BotOps to ask; recorded, nobody woken"
        decision["recipients"] = [{"bot": slug, "confidence": p} for slug, p in chosen]
        return decision

    def fallback_bot(self, active):
        """Who asks "which bot?" when nobody scored: the assistant, or BotOps in a company that
        chose not to have one (the assistant is optional). None when neither is running."""
        if self.settings.assistant_bot in active:
            return self.settings.assistant_bot
        return "botops" if "botops" in active else None

    def default_decision(self, fleet):
        """No decision model: everything goes to the fallback bot and says so."""
        asker = self.fallback_bot([bot["slug"] for bot in fleet])
        return {"routed_by": "default", "model": None, "scores": {}, "asks": None, "reply_to_last": None,
                "names_bot": None, "recipients": [{"bot": asker, "confidence": None}] if asker else [],
                "candidates": [], "fallback": True, "dropped": [],
                "reason": "no decisions key configured; routed to " + (asker or "nobody (no assistant or BotOps is running)")}

    def direct_decision(self, own):
        """A message to a bot's own app is for that bot: no decision model, and no assistant to fall back to."""
        return {"routed_by": "own app", "model": None, "app": own.bot, "scores": {}, "asks": None,
                "reply_to_last": None, "names_bot": None, "recipients": [{"bot": own.bot, "confidence": None}],
                "candidates": [], "fallback": False, "dropped": [], "reason": ""}

    # ------------------------------------------------------------------ writing into the hub
    def conversation_for(self, c, actor, bot, event):
        app = event.get("app") or ""
        row = c.execute("SELECT conversation_id,app FROM slack_threads WHERE channel=? AND thread_ts=? AND bot=?",
                        (event["channel"], event["thread_ts"], bot)).fetchone()
        if row and H.conversation(c, row["conversation_id"]) and not H.conversation(c, row["conversation_id"])["closed_at"]:
            if app and row["app"] != app:
                # Addressed through the bot's own app: its replies here come from that app now.
                c.execute("UPDATE slack_threads SET app=? WHERE channel=? AND thread_ts=? AND bot=?",
                          (app, event["channel"], event["thread_ts"], bot))
            return row["conversation_id"], False
        conv = H.open_conversation(c, actor, [actor, "bot:" + bot], kind="chat",
                                   subject=f"Slack {event['channel']} {event['thread_ts']}", scope="direct")
        c.execute("INSERT OR REPLACE INTO slack_threads(channel,thread_ts,bot,conversation_id,created,last_routed,app) "
                  "VALUES(?,?,?,?,?,NULL,?)", (event["channel"], event["thread_ts"], bot, conv["id"], self.clock(), app))
        return conv["id"], True

    def refs_for(self, event, channel, decision, thread, person):
        return {"slack": {"channel": event["channel"], "channel_name": channel["name"], "kind": channel["kind"],
                          "purpose": channel.get("purpose") or None, "ts": event["ts"],
                          "thread_ts": event["reply_ts"] or None,
                          "permalink": permalink(self.team_url, event["channel"], event["ts"], event["reply_ts"] or None),
                          "event_id": event["event_id"], "sender": person["email"],
                          "note": ("This message came from Slack (" + channel["name"] + "). Your reply in this "
                                   "conversation is posted to that Slack thread for you, under your name; do not "
                                   "post to Slack yourself for this conversation. Keep the reply readable there."),
                          "thread": thread},
                "routing": decision}

    def write(self, c, event, person, channel, decision, thread):
        """Each recipient gets the message in its own thread conversation. Returns the decision."""
        actor = "human:" + person["id"]
        delivered, refs = [], self.refs_for(event, channel, decision, thread, person)
        blocked = self.blocked(c, person)
        for one in list(decision["recipients"]):
            bot = one["bot"]
            if bot in blocked:
                decision["dropped"].append({"bot": bot, "confidence": one.get("confidence"),
                                            "reason": "access: the sender may not send this bot requests"})
                decision["recipients"] = [r for r in decision["recipients"] if r["bot"] != bot]
                continue
            try:
                cid, opened = self.conversation_for(c, actor, bot, event)
                # A follow-up answers the bot's last line: an unanswered `ask` is closed with an
                # `answer` (what `hub question ask --wait` polls for); any other bot line is linked.
                kind, reply_to = "say", None
                last = c.execute("SELECT id,from_actor,kind FROM messages WHERE conversation_id=? "
                                 "ORDER BY created DESC LIMIT 1", (cid,)).fetchone()
                if last and last["from_actor"] == "bot:" + bot:
                    reply_to = last["id"]
                    if last["kind"] == "ask" and not H.answers_to(c, [last["id"]]):
                        kind = "answer"
                c.execute("SAVEPOINT slack_write")
                try:
                    message = H.say(c, actor, "bot:" + bot, event["text"], conversation_id=cid, kind=kind,
                                    refs=refs, in_reply_to=reply_to)
                    c.execute("RELEASE slack_write")
                except H.Refused:
                    c.execute("ROLLBACK TO slack_write")
                    c.execute("RELEASE slack_write")
                    raise
            except H.Refused as exc:
                decision["dropped"].append({"bot": bot, "confidence": one.get("confidence"), "reason": f"{exc.rule}: {exc.detail}"[:300]})
                decision["recipients"] = [r for r in decision["recipients"] if r["bot"] != bot]
                continue
            c.execute("UPDATE slack_threads SET last_routed=? WHERE channel=? AND thread_ts=? AND bot=?",
                      (self.clock(), event["channel"], event["thread_ts"], bot))
            delivered.append({"bot": bot, "conversation_id": cid, "message_id": message["id"], "opened": opened})
        if not delivered and not decision["fallback"] and decision["dropped"] and decision["routed_by"] != "own app":
            # Everyone the decision model chose is unreachable: the assistant gets it, with the reasons on it.
            asker = self.fallback_bot([r["slug"] for r in c.execute("SELECT slug FROM bots WHERE state='active'")
                                       if r["slug"] not in blocked])
            if asker:
                decision["fallback"] = True
                decision["reason"] = "every recipient was dropped; " + ("the assistant" if asker == self.settings.assistant_bot
                                                                         else asker) + " asks"
                decision["recipients"] = [{"bot": asker, "confidence": decision["scores"].get(asker)}]
                return self.write(c, event, person, channel, decision, thread)
        decision["delivered"] = delivered
        return decision

    def record_only(self, c, event, person, decision):
        """A message that asks for nothing: kept on the thread's conversations, no job queued."""
        actor = "human:" + person["id"]
        kept = []
        for row in c.execute("SELECT bot,conversation_id FROM slack_threads WHERE channel=? AND thread_ts=? "
                             "ORDER BY last_routed DESC", (event["channel"], event["thread_ts"])):
            try:
                message = H.say(c, actor, actor, event["text"], conversation_id=row["conversation_id"],
                                refs={"slack": {"channel": event["channel"], "ts": event["ts"], "thread_ts": event["thread_ts"],
                                                "event_id": event["event_id"], "recorded_only": True}, "routing": decision})
                kept.append({"bot": row["bot"], "conversation_id": row["conversation_id"], "message_id": message["id"]})
            except H.Refused as exc:
                decision["dropped"].append({"bot": row["bot"], "reason": f"{exc.rule}: {exc.detail}"[:300]})
        decision["recorded_on"] = kept
        return decision

    def finish(self, c, event, state, actor=None, reason=None, decision=None):
        c.execute("UPDATE slack_events SET state=?,actor=?,reason=?,routing_json=?,processed=? WHERE event_id=?",
                  (state, actor, (reason or "")[:300] or None, json.dumps(decision, sort_keys=True) if decision else None,
                   self.clock(), event["event_id"]))
        H.event(c, actor or H.KEEPER, "slack.event", event["event_id"],
                {"state": state, "channel": event["channel"], "thread_ts": event["thread_ts"], "reason": reason,
                 "recipients": [r["bot"] for r in (decision or {}).get("recipients") or []]})

    def routing_error(self, event, person, exc):
        """The decision model could not answer. A refusal, or an outage that outlasted
        `ROUTE_GIVE_UP_SECONDS`, ends the message as `failed` and tells the human; a short outage
        leaves it `received` for the next try. Never an endless loop."""
        actor = "human:" + person["id"]
        expired = H.shift(event["received"], seconds=ROUTE_GIVE_UP_SECONDS) <= self.clock()
        if exc.retryable and not expired:
            with self.store.transaction() as c:
                self.finish(c, event, "received", actor=actor, reason=f"decisions: {exc}")
            LOG.error("Event %s waits: %s", event["event_id"], exc)
            return {"event_id": event["event_id"], "state": "received", "reason": str(exc)}
        reason = f"decisions: {exc}" + (f" (still failing after {ROUTE_GIVE_UP_SECONDS // 60} minutes)" if exc.retryable else "")
        with self.store.transaction() as c:
            self.finish(c, event, "failed", actor=actor, reason=reason)
        LOG.error("Event %s failed: %s", event["event_id"], reason)
        try:
            self.slack.post_message(event["channel"], event["reply_ts"] or None,
                                    "Sorry, I could not work out which bot should get your message, so nobody has it. "
                                    "Please send it again in a few minutes. If it keeps happening, tell the team that runs Tico.")
        except Exception as post_exc:               # the failure is recorded either way
            LOG.warning("Could not tell the human in %s that routing failed: %s", event["channel"], type(post_exc).__name__)
        return {"event_id": event["event_id"], "state": "failed", "reason": reason}

    def process(self, event):
        """One persisted event, start to finish. Network calls happen outside the write lock."""
        own = self.bot_apps.get(event.get("app")) if event.get("app") else None
        if event.get("app") and own is None:
            # Its app's credentials went from the vault after the event came in.
            with self.store.transaction() as c:
                self.finish(c, event, "failed", reason=f"{event['app']}'s own Slack app is not connected")
            return {"event_id": event["event_id"], "state": "failed"}
        with self.store.read() as c:
            person, user, denied = self.verify_sender(c, event, own.slack if own else None)
        channel, channel_denied = (self.verify_channel(event, own) if not denied else (None, None))
        denied = denied or channel_denied
        if denied:
            with self.store.transaction() as c:
                self.finish(c, event, "denied", reason=denied)
            LOG.info("Event %s denied: %s", event["event_id"], denied)
            return {"event_id": event["event_id"], "state": "denied", "reason": denied}
        with self.store.read() as c:
            state = self.state(c, event, person, channel)
        fleet, thread = state["roster"], state["thread"]
        if own:
            decision = self.direct_decision(own)
        elif self.judge is None:
            decision = self.default_decision(fleet)
        else:
            try:
                answers = self.ask(state, fleet)
            except J.JudgeError as exc:
                return self.routing_error(event, person, exc)
            decision = self.decide(answers, fleet)
        with self.store.transaction() as c:
            if not decision["recipients"]:
                decision = self.record_only(c, event, person, decision)
                self.finish(c, event, "recorded", actor="human:" + person["id"], reason=decision["reason"], decision=decision)
                state_out = "recorded"
            else:
                decision = self.write(c, event, person, channel, decision, thread)
                state_out = "routed" if decision.get("delivered") else "failed"
                self.finish(c, event, state_out, actor="human:" + person["id"],
                            reason=decision["reason"] or (decision["dropped"][0]["reason"] if decision["dropped"] and state_out == "failed" else None),
                            decision=decision)
        LOG.info("Event %s %s -> %s", event["event_id"], state_out,
                 ",".join(r["bot"] for r in decision["recipients"]) or "nobody")
        return {"event_id": event["event_id"], "state": state_out, "decision": decision}

    # ------------------------------------------------------------------ egress
    def im_channel(self, c, person_id, app=""):
        """The existing DM channel with this person through this app (Tico's by default), if anyone
        has already talked there."""
        actor = "human:" + person_id
        row = c.execute(
            "SELECT t.channel FROM slack_threads t JOIN conversations v ON v.id=t.conversation_id "
            "WHERE t.thread_ts='' AND t.app=? AND v.participants_json LIKE ? LIMIT 1",
            (app, '%"' + actor + '"%')).fetchone()
        return row["channel"] if row else None

    def originate(self):
        """A bot message to a person with no Slack mapping yet: open (or reuse) their Tico DM.

        Tico's app posts it, under the sending bot's name, or the bot's own app when it has one.
        Their reply comes back through the gateway into the same hub conversation, which the
        person's profile shows.
        """
        with self.store.read() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT m.id,m.body,m.conversation_id,m.from_actor,m.to_actor FROM messages m "
                "WHERE m.kind IN ('say','ask','answer','notice') AND m.from_actor LIKE 'bot:%' "
                "AND m.to_actor LIKE 'human:%' "
                "AND NOT EXISTS (SELECT 1 FROM slack_posts p WHERE p.message_id=m.id) "
                "AND NOT EXISTS (SELECT 1 FROM slack_threads t WHERE t.conversation_id=m.conversation_id) "
                "ORDER BY m.created LIMIT 20")]
        opened = 0
        for row in rows:
            bot, pid = row["from_actor"][4:], row["to_actor"][6:]
            own = self.bot_apps.get(bot)
            app = bot if own else ""
            slack_id = ""
            with self.store.transaction() as c:
                if c.execute("SELECT 1 FROM slack_threads WHERE conversation_id=?",
                             (row["conversation_id"],)).fetchone():
                    continue
                channel = self.im_channel(c, pid, app)
                if not channel:
                    slack_id = str((H.human(c, pid) or {}).get("slack_id") or "").strip()
                    if not slack_id:
                        raw = c.execute("SELECT value_json FROM registry_metadata WHERE key='people'").fetchone()
                        roster = P.load(json.loads(raw["value_json"]) if raw else {})
                        slack_id = str((P.person(pid, roster) or {}).get("slack_id") or "").strip()
                    if not slack_id:
                        continue
                else:
                    now = self.clock()
                    c.execute("INSERT OR REPLACE INTO slack_threads"
                              "(channel,thread_ts,bot,conversation_id,created,last_routed,app) "
                              "VALUES(?,?,?,?,?,?,?)", (channel, "", bot, row["conversation_id"], now, now, app))
                    c.execute("INSERT OR IGNORE INTO slack_posts"
                              "(message_id,channel,thread_ts,bot,text,state,created,updated,app) "
                              "VALUES(?,?,?,?,?,'ready',?,?,?)",
                              (row["id"], channel, "", bot, row["body"], now, now, app))
                    opened += 1
                    continue
            try:
                opened_ch = (own.slack if own else self.slack).conversations_open(slack_id)
                channel = str(opened_ch.get("id") or "")
            except Exception as exc:
                LOG.warning("Could not open a Slack DM with %s: %s", pid, type(exc).__name__)
                continue
            if not channel:
                continue
            with self.store.transaction() as c:
                if c.execute("SELECT 1 FROM slack_threads WHERE conversation_id=?",
                             (row["conversation_id"],)).fetchone():
                    continue
                now = self.clock()
                c.execute("INSERT OR REPLACE INTO slack_threads"
                          "(channel,thread_ts,bot,conversation_id,created,last_routed,app) "
                          "VALUES(?,?,?,?,?,?,?)", (channel, "", bot, row["conversation_id"], now, now, app))
                c.execute("INSERT OR IGNORE INTO slack_posts"
                          "(message_id,channel,thread_ts,bot,text,state,created,updated,app) "
                          "VALUES(?,?,?,?,?,'ready',?,?,?)",
                          (row["id"], channel, "", bot, row["body"], now, now, app))
                opened += 1
        return opened

    def mirror(self):
        """New bot replies in mapped conversations become posts to make, once each."""
        with self.store.transaction() as c:
            rows = c.execute(
                "SELECT m.id,m.body,m.in_reply_to,t.channel,t.thread_ts,t.bot,t.app FROM messages m "
                "JOIN slack_threads t ON t.conversation_id=m.conversation_id AND m.from_actor='bot:'||t.bot "
                "WHERE m.created>t.created AND m.kind IN ('say','ask','answer','notice') AND m.to_actor LIKE 'human:%' "
                "AND NOT EXISTS (SELECT 1 FROM slack_posts p WHERE p.message_id=m.id) ORDER BY m.created LIMIT 50").fetchall()
            for r in rows:
                # The reply goes where the message it answers was: the thread root in a channel;
                # in a DM, the bottom of the DM unless the person wrote inside a reply thread.
                where = c.execute(
                    "SELECT e.reply_ts FROM messages p JOIN slack_events e "
                    "ON e.event_id=json_extract(p.refs_json,'$.slack.event_id') WHERE p.id=?", (r["in_reply_to"],)).fetchone() \
                    if r["in_reply_to"] else None
                if where is None:
                    where = c.execute("SELECT reply_ts FROM slack_events WHERE channel=? AND thread_ts=? AND state='routed' "
                                      "ORDER BY ts DESC LIMIT 1", (r["channel"], r["thread_ts"])).fetchone()
                reply_ts = str(where["reply_ts"] if where else r["thread_ts"] or "")
                now = self.clock()
                c.execute("INSERT OR IGNORE INTO slack_posts(message_id,channel,thread_ts,bot,text,state,created,updated,app) "
                          "VALUES(?,?,?,?,?,'ready',?,?,?)",
                          (r["id"], r["channel"], reply_ts, r["bot"], r["body"], now, now, r["app"] or ""))
        return len(rows)

    def rendered(self, c, post):
        """What Slack shows: the reply, then the footer that names the bot even where customize is not honoured."""
        bot = H.bot(c, post["bot"]) or {}
        name = bot.get("display_name") or post["bot"]
        config = (entries(c).get(post["bot"]) or {})
        text = str(post["text"] or "").strip()
        if len(text) > POST_CHARS:
            text = text[:POST_CHARS].rstrip() + " …"
        if post.get("app"):
            # The bot's own app is the bot in Slack, with its own name and icon: no footer.
            return {"text": slack_escape(text), "username": None, "icon_emoji": None, "icon_url": None}
        return {"text": slack_escape(text) + f"\n\n(sent from {name})", "username": name,
                "icon_emoji": str(config.get("slack_icon") or "") or None,
                "icon_url": str(config.get("slack_icon_url") or "") or None}

    def deliver(self):
        """Post what is ready, one attempt each. A crash between send and record is uncertain. A post
        for a bot app that is not connected waits, and never holds up the others."""
        now = self.clock()
        apps = ["", *sorted(self.bot_apps)]
        with self.store.read() as c:
            due = [dict(r) for r in c.execute(
                "SELECT * FROM slack_posts WHERE (state='ready' OR (state='rate_limited' AND next_attempt<=?)) "
                "AND app IN (%s) ORDER BY created LIMIT 20" % ",".join("?" * len(apps)), (now, *apps))]
            shaped = {row["message_id"]: self.rendered(c, row) for row in due}
        results = []
        for post in due:
            with self.store.transaction() as c:
                claimed = c.execute("UPDATE slack_posts SET state='sending',attempts=attempts+1,updated=? "
                                    "WHERE message_id=? AND state IN ('ready','rate_limited')", (self.clock(), post["message_id"])).rowcount
            if not claimed:
                continue
            body = shaped[post["message_id"]]
            own = self.bot_apps.get(post["app"]) if post["app"] else None
            if not own and not self.customize:
                if not self._customize_warned:
                    LOG.warning("Posting without %s: replies show as %s with the footer only", CUSTOMIZE_SCOPE,
                                self.settings.assistant_name)
                    self._customize_warned = True
                body = {**body, "username": None, "icon_emoji": None, "icon_url": None}
            state, slack_ts, error, next_attempt = "sent", None, None, None
            try:
                answer = (own.slack if own else self.slack).post_message(
                    post["channel"], post["thread_ts"] or None, body["text"], username=body["username"],
                    icon_emoji=body["icon_emoji"], icon_url=body["icon_url"])
                slack_ts = str(answer.get("ts") or "")
            except SlackError as exc:
                if exc.code == "ratelimited" and post["attempts"] + 1 < MAX_POST_ATTEMPTS:
                    state, error = "rate_limited", exc.code
                    next_attempt = H.shift(self.clock(), seconds=exc.retry_after or RATE_LIMIT_DEFAULT)
                elif exc.code == "ratelimited":
                    state, error = "failed", f"ratelimited after {post['attempts'] + 1} attempts"
                else:
                    state, error = "failed", exc.code
            except Exception as exc:                # unreachable, unreadable, or anything unexpected
                state, error = "uncertain", f"{type(exc).__name__}: {exc}"[:300]
            with self.store.transaction() as c:
                c.execute("UPDATE slack_posts SET state=?,slack_ts=?,error=?,next_attempt=?,updated=? WHERE message_id=?",
                          (state, slack_ts, error, next_attempt, self.clock(), post["message_id"]))
                H.event(c, "bot:" + post["bot"], "slack.post", post["message_id"],
                        {"state": state, "channel": post["channel"], "thread_ts": post["thread_ts"], "error": error})
            if state != "sent":
                LOG.warning("Post %s for %s is %s: %s", post["message_id"], post["bot"], state, error)
            results.append({"message_id": post["message_id"], "state": state, "slack_ts": slack_ts})
        return results

    # ------------------------------------------------------------------ the readers' pass
    def readable(self):
        """Registry channels with readers, by channel id."""
        return {cid: entry for cid, entry in registry_channels(self.settings.registry_dir).items() if entry["readers"]}

    def newest_ts(self, c, channel, thread_ts=None):
        if thread_ts:
            row = c.execute("SELECT MAX(ts) AS ts FROM slack_events WHERE channel=? AND thread_ts=? AND ts!=?",
                            (channel, thread_ts, thread_ts)).fetchone()
        else:
            row = c.execute("SELECT MAX(ts) AS ts FROM slack_events WHERE channel=?", (channel,)).fetchone()
        return str(row["ts"] or "") if row else ""

    def backfill(self):
        """What the socket missed: history per readable channel since its newest stored message,
        and replies in every thread the gateway knows. Stores only; nothing found here is routed
        (the fast lane is the socket's), so an old mention never wakes a bot late."""
        stored = 0
        channels = self.readable()
        with self.store.read() as c:
            since = {cid: self.newest_ts(c, cid) for cid in channels}
            threads = [(r["channel"], r["thread_ts"]) for r in c.execute(
                "SELECT DISTINCT channel,thread_ts FROM slack_threads WHERE thread_ts!='' AND channel IN (%s)"
                % ",".join("?" * len(channels)), tuple(channels))] if channels else []
        found = []
        for cid, entry in channels.items():
            if cid in self._unreadable:
                continue
            try:
                page = self.slack.history(cid, oldest=since[cid])
            except SlackError as exc:
                if exc.code in ("not_in_channel", "channel_not_found", "missing_scope", "invalid_auth"):
                    LOG.warning("Channel #%s is not readable (%s); its readers get nothing until it is",
                                entry["name"] or cid, exc.code)
                    self._unreadable.add(cid)
                continue
            except SlackUnreachable as exc:
                LOG.warning("History for #%s skipped: %s", entry["name"] or cid, exc)
                continue
            channel_type = "group" if cid.startswith("G") else "channel"
            for message in page:
                found.append((cid, channel_type, message))
                if int(message.get("reply_count") or 0) and str(message.get("ts")) == str(message.get("thread_ts") or message.get("ts")):
                    threads.append((cid, str(message["ts"])))
        seen = set()
        for cid, thread_ts in threads:
            if (cid, thread_ts) in seen or cid in self._unreadable:
                continue
            seen.add((cid, thread_ts))
            with self.store.read() as c:
                oldest = self.newest_ts(c, cid, thread_ts)
            try:
                page = self.slack.replies(cid, thread_ts, oldest=oldest)
            except (SlackError, SlackUnreachable) as exc:
                LOG.warning("Replies for %s/%s skipped: %s", cid, thread_ts, getattr(exc, "code", exc))
                continue
            for message in page:
                found.append((cid, "group" if cid.startswith("G") else "channel", {**message, "thread_ts": thread_ts}))
        if not found:
            return 0
        with self.store.transaction() as c:
            for cid, channel_type, message in found:
                row, _why = channel_message(cid, channel_type, message, self.bot_user_id)
                if not row:
                    continue
                row = {**row, "op": "store", "event_id": f"history:{cid}:{row['ts']}", "team_id": self.team_id,
                       "api_app_id": self.app_id}
                # History fills context and the readers' unread; it never routes.
                if c.execute("SELECT 1 FROM slack_events WHERE channel=? AND ts=?", (cid, row["ts"])).fetchone():
                    continue
                stored += c.execute(
                    "INSERT OR IGNORE INTO slack_events(event_id,team_id,channel,channel_kind,thread_ts,reply_ts,ts,"
                    "user_id,event_type,text,received,state,author,author_name,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,'stored',?,?,?)",
                    (row["event_id"], row["team_id"], cid, "channel", row["thread_ts"], row["reply_ts"], row["ts"],
                     row["user_id"], row["event_type"],
                     humanize(row["text"], self.mention_names())[:TEXT_CHARS] or row["text"],
                     self.clock(), row["author"], row["author_name"], self.clock())).rowcount
        if stored:
            LOG.info("Backfilled %d channel message(s) the socket did not deliver", stored)
        return stored

    def display_name(self, row):
        """What a digest calls the author: the roster name for a person, the bot's own name for a bot."""
        if row["author"] == "bot":
            return row["author_name"] or ("the " + self.settings.assistant_name + " app" if row["user_id"] == self.bot_user_id else "a bot")
        uid = row["user_id"]
        if uid not in self._names:
            try:
                user = self.slack.users_info(uid)
                profile = user.get("profile") or {}
                self._names[uid] = str(profile.get("real_name") or profile.get("display_name") or user.get("real_name")
                                       or user.get("name") or uid)
            except (SlackError, SlackUnreachable):
                return uid
        return self._names[uid]

    def render_line(self, row, marker=""):
        text = str(row["text"] or "").strip()
        if row["deleted"]:
            text = "(message removed)"
        elif len(text) > DIGEST_LINE_CHARS:
            text = text[:DIGEST_LINE_CHARS].rstrip() + " …"
        text = text.replace("\n", "\n    ")
        tag = " (edited)" if row["edited"] and not row["deleted"] else ""
        return f"- {slack_time(row['ts'])} {self.display_name(row)}{tag}{marker}: {text}"

    def unread_for(self, c, channel, reader, cursor):
        """New rows past the reader's cursor (what the fast lane already gave this reader excluded),
        and rows it has seen that were edited or removed since its last pass."""
        new, changed, newest = [], [], cursor["last_ts"]
        for r in c.execute("SELECT * FROM slack_events WHERE channel=? AND ts>? AND deleted IS NULL ORDER BY ts",
                           (channel, cursor["last_ts"])):
            r = dict(r)
            newest = r["ts"]
            if r["state"] == "routed":
                routing = json.loads(r["routing_json"] or "{}")
                if reader in [d["bot"] for d in routing.get("delivered") or []]:
                    continue
            new.append(r)
        if cursor["last_run"]:
            changed = [dict(r) for r in c.execute(
                "SELECT * FROM slack_events WHERE channel=? AND ts<=? AND updated>? AND (edited IS NOT NULL OR deleted IS NOT NULL) "
                "ORDER BY ts", (channel, cursor["last_ts"], cursor["last_run"]))]
        return new, changed, newest

    def render_channel(self, c, channel, entry, new, changed, cursor):
        """One channel's section: threads in order, each with its earlier lines as context."""
        lines = [f"## #{entry['name'] or channel}" + (f" — {entry['purpose']}" if entry.get("purpose") else ""),
                 channel_link(self.team_url, channel)]
        by_thread = {}
        for r in new:
            by_thread.setdefault(r["thread_ts"], []).append(r)
        for thread_ts, rows in sorted(by_thread.items(), key=lambda kv: float(kv[0] or 0)):
            lines.append("")
            root_is_new = any(r["ts"] == thread_ts for r in rows)
            if not root_is_new:
                earlier = [dict(r) for r in c.execute(
                    "SELECT * FROM slack_events WHERE channel=? AND thread_ts=? AND ts<=? AND deleted IS NULL "
                    "ORDER BY ts DESC LIMIT ?", (channel, thread_ts, cursor["last_ts"], CONTEXT_REPLIES + 1))]
                root = [r for r in earlier if r["ts"] == thread_ts]
                if not root:
                    root = [dict(r) for r in c.execute("SELECT * FROM slack_events WHERE channel=? AND ts=?", (channel, thread_ts))]
                context = root + [r for r in reversed(earlier) if r["ts"] != thread_ts][-CONTEXT_REPLIES:]
                if context:
                    lines.append(f"thread ({permalink(self.team_url, channel, thread_ts)}), earlier:")
                    lines.extend(self.render_line(r, " (earlier)") for r in context)
                    lines.append("new:")
                else:
                    lines.append(f"thread ({permalink(self.team_url, channel, thread_ts)}), new:")
            elif len(rows) > 1:
                lines.append(f"thread ({permalink(self.team_url, channel, thread_ts)}):")
            lines.extend(self.render_line(r) for r in rows)
        if changed:
            lines.append("")
            lines.append("changed since your last look:")
            lines.extend(self.render_line(r) for r in changed)
        return "\n".join(lines)

    def reader_conversation(self, c, reader):
        row = c.execute("SELECT conversation_id FROM slack_digests WHERE reader=? ORDER BY created DESC LIMIT 1",
                        (reader,)).fetchone()
        if row:
            conv = H.conversation(c, row["conversation_id"])
            if conv and not conv["closed_at"]:
                return conv
        return H.open_conversation(c, H.KEEPER, [H.KEEPER, "bot:" + reader], kind="chat",
                                   subject="Slack channels", scope="direct")

    def digest(self):
        """Every reader gets what is unread in its channels, once, and its cursors move with it."""
        channels = self.readable()
        with self.store.read() as c:
            active = {r["slug"] for r in c.execute("SELECT slug FROM bots WHERE state='active'")}
        by_reader = {}
        for cid, entry in channels.items():
            for reader in entry["readers"]:
                if reader in active:
                    by_reader.setdefault(reader, []).append((cid, entry))
                else:
                    LOG.debug("Reader %s of #%s is not an active bot", reader, entry["name"] or cid)
        # Names are looked up before the write lock is taken, so Slack's latency is not the
        # API's; what a thread's earlier lines need is usually cached by then.
        with self.store.read() as c:
            for r in c.execute("SELECT DISTINCT e.user_id AS uid, e.author FROM slack_events e JOIN slack_reads r "
                               "ON r.channel=e.channel WHERE e.ts>r.last_ts OR (r.last_run IS NOT NULL AND e.updated>r.last_run) "
                               "LIMIT 500"):
                self.display_name({"author": r["author"], "user_id": r["uid"], "author_name": None})
        results = []
        for reader, chans in sorted(by_reader.items()):
            try:
                with self.store.transaction() as c:
                    results.append(self.digest_for(c, reader, chans))
            except H.Refused as exc:
                LOG.warning("Digest for %s refused (%s): %s", reader, exc.rule, exc.detail)
                results.append({"reader": reader, "state": "refused", "reason": f"{exc.rule}: {exc.detail}"[:300]})
        return results

    def digest_for(self, c, reader, chans):
        now = self.clock()
        sections, ids, count, skipped, names = [], [], 0, 0, []
        for cid, entry in chans:
            cursor = c.execute("SELECT * FROM slack_reads WHERE channel=? AND reader=?", (cid, reader)).fetchone()
            if cursor is None:
                # A new reader starts at now: the channel's past is context, not unread.
                c.execute("INSERT INTO slack_reads(channel,reader,last_ts,last_run,digests) VALUES(?,?,?,NULL,0)",
                          (cid, reader, self.newest_ts(c, cid)))
                continue
            hours = entry.get("digest_hours") or 0
            if hours and cursor["last_run"] and cursor["last_run"] > H.shift(now, seconds=-hours * 3600):
                continue                            # this channel is read less often: it waits, unread
            new, changed, newest = self.unread_for(c, cid, reader, cursor)
            if not new and not changed:
                if newest != cursor["last_ts"]:      # only what the fast lane already gave this reader
                    c.execute("UPDATE slack_reads SET last_ts=? WHERE channel=? AND reader=?", (newest, cid, reader))
                continue
            over = max(0, len(new) - self.settings.slack_digest_cap)
            if over:
                new, skipped = new[:self.settings.slack_digest_cap], skipped + over
            section = self.render_channel(c, cid, entry, new, changed, cursor)
            if over:
                section += f"\n\n({over} more in #{entry['name'] or cid} not shown; read the channel for those.)"
            sections.append(section)
            ids.extend(r["event_id"] for r in new + changed)
            count += len(new) + len(changed)
            names.append("#" + (entry["name"] or cid))
            c.execute("UPDATE slack_reads SET last_ts=?,last_run=?,digests=digests+1 WHERE channel=? AND reader=?",
                      (newest, now, cid, reader))
        if not sections:
            return {"reader": reader, "state": "nothing", "count": 0}
        head = (f"Slack, what is new in the channels you read: {count} message(s) in {', '.join(names)}"
                + (f", {skipped} more not shown" if skipped else "") + ". Channel posts follow your Slack access and "
                "registry/slack-channels.yaml (post: false blocks them); a reply in a thread here is not through this conversation.")
        body = head + "\n\n" + "\n\n".join(sections)
        if len(body) > DIGEST_CHARS:
            body = body[:DIGEST_CHARS].rstrip() + "\n\n(… the digest was cut at its size limit; read the channels for the rest.)"
        conv = self.reader_conversation(c, reader)
        first = chans[0][0]
        refs = {"slack": {"digest": {"channels": names, "count": count, "skipped": skipped, "event_ids": ids[:500]},
                          "channel": first, "channel_name": names[0] if len(names) == 1 else f"{len(names)} channels",
                          "kind": "channel", "permalink": channel_link(self.team_url, first),
                          "note": "A digest of Slack channels you read, written by the gateway. Nothing here was said "
                                  "to you; act on it as an employee who read the channel would."}}
        message = H.feed(c, "bot:" + reader, body, conv, refs=refs)
        c.execute("INSERT INTO slack_digests(id,reader,conversation_id,message_id,channels_json,event_ids_json,count,skipped,created) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", (H.new_id(), reader, conv["id"], message["id"], json.dumps(names),
                                                json.dumps(ids), count, skipped, now))
        H.event(c, H.KEEPER, "slack.digest", message["id"], {"reader": reader, "channels": names, "count": count, "skipped": skipped})
        LOG.info("Digest for %s: %d message(s) in %s", reader, count, ", ".join(names))
        return {"reader": reader, "state": "sent", "count": count, "skipped": skipped, "message_id": message["id"],
                "conversation_id": conv["id"]}

    def maybe_digest(self):
        """The readers' pass when it is due: fill the socket's gaps, then deliver."""
        minutes = self.settings.slack_digest_minutes
        if not minutes or self.clock() < self.next_digest:
            return None
        self.next_digest = H.shift(self.clock(), seconds=minutes * 60)
        try:
            self.backfill()
        except Exception as exc:                    # the pass still delivers what is stored
            LOG.error("Backfill failed: %s: %s", type(exc).__name__, exc)
        return self.digest()

    # ------------------------------------------------------------------ the loop
    def tick(self):
        """One pass: bots' own apps when due, pending events, then replies to mirror, then posts to
        send, then the readers' pass when due."""
        if self.clock() >= self.next_vault:
            self.connect_bot_apps()
        out = []
        for event in self.pending():
            try:
                out.append(self.process(event))
            except Exception as exc:                # one bad event never stops the rest
                LOG.error("Event %s failed: %s", event["event_id"], type(exc).__name__)
                with self.store.transaction() as c:
                    self.finish(c, event, "failed", reason=f"{type(exc).__name__}: {exc}")
                out.append({"event_id": event["event_id"], "state": "failed"})
        self.originate()
        self.mirror()
        posts = self.deliver()
        digests = self.maybe_digest()
        return {"events": out, "posts": posts, "digests": digests}

    def run(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                LOG.error("Gateway tick failed: %s", type(exc).__name__)
            if self.heartbeat:
                try:
                    self.heartbeat()
                except Exception as exc:
                    LOG.error("Gateway heartbeat failed: %s", type(exc).__name__)
            self.wake.wait(TICK_SECONDS)
            self.wake.clear()


# ----------------------------------------------------------------------------- the process
def _sleep(stopping, seconds):
    stopping.wait(seconds)
    return not stopping.is_set()


def main(argv=None, make_slack=None):
    """`make_slack(bot, app)` is the test seam for the Slack client. With TICO_SLACK_WAIT=1 (the Docker
    service) a missing token or a refused start is reported on the Health page and retried, and
    new tokens pasted into Tico take effect without a restart; otherwise those exit as before."""
    parser = argparse.ArgumentParser(description="Tico's Slack gateway (docs/slack-gateway.md)")
    parser.add_argument("--check", action="store_true", help="verify the app and the scopes, then exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stdout)
    settings = Settings.from_env()
    if not settings.slack_gateway_enabled and not args.check:
        LOG.info("TICO_SLACK_GATEWAY_ENABLED is not 1; the Slack gateway stays off")
        return 0
    wait = os.environ.get("TICO_SLACK_WAIT") == "1" and not args.check
    make_slack = make_slack or SlackAPI
    stopping = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopping.set())
    store = None
    while True:
        credentials, stored = slack_credentials(settings), None
        if not (credentials["bot_token"] and credentials["app_token"]):
            store = store or Store(settings)
            store.initialize()
            stored = slack_app.load(store, settings)
            if stored:
                credentials = stored
        if not credentials["bot_token"] or not credentials["app_token"]:
            if not wait:
                LOG.error("No Slack tokens: set SLACK_BOT_TOKEN and SLACK_APP_TOKEN or TICO_SLACK_SECRET_ARN")
                return 1
            LOG.info("No Slack tokens yet; paste them in Settings > Slack")
            if _sleep(stopping, 15):
                continue
            return 0
        if credentials["team_id"]:
            settings.slack_team_id = credentials["team_id"]
        if credentials["app_id"]:
            settings.slack_app_id = credentials["app_id"]
        store = store or Store(settings)
        store.initialize()
        gateway = Gateway(store, make_slack(credentials["bot_token"], credentials["app_token"]))
        gateway.pin_workspace = stored is not None
        try:
            verified = gateway.verify_app()
        except (SlackError, SlackUnreachable, RuntimeError) as exc:
            LOG.error("Refusing to start: %s", exc)
            if not wait:
                return 1
            slack_app.report(store, "disconnected", str(exc))
            if _sleep(stopping, 60):
                continue
            return 0
        LOG.info("Verified workspace %s app %s bot user %s", verified["team_id"], verified["app_id"], verified["bot_user_id"])
        if args.check:
            verified["bot_apps"] = gateway.connect_bot_apps()
            print(json.dumps(verified))
            return 0
        if stored is not None and not (stored["team_id"] and stored["app_id"]):
            slack_app.pin(store, verified["team_id"], verified["app_id"])
        gateway.recover()
        try:
            gateway.slack.connect(lambda payload: gateway.receive(payload))
        except Exception as exc:
            LOG.error("Socket Mode did not connect: %s", type(exc).__name__)
            if not wait:
                return 1
            slack_app.report(store, "disconnected", f"Socket Mode did not connect ({type(exc).__name__}); check the app-level token")
            gateway.slack.close()
            if _sleep(stopping, 60):
                continue
            return 0
        gateway.listening = True                # bots' own apps connect on the first tick
        LOG.info("Socket Mode connected; listening for app_mention, message.im, message.channels and message.groups; "
                 "readers' pass every %d minute(s)", settings.slack_digest_minutes)
        state = {"connected": None, "stamp": slack_app.stamp(store) if stored else None}

        def heartbeat():
            up = gateway.slack.connected() if hasattr(gateway.slack, "connected") else True
            if up != state["connected"]:
                state["connected"] = up
                if up:
                    slack_app.report(store, "connected", team=verified["team_id"])
                else:
                    slack_app.report(store, "disconnected", "The Slack websocket dropped; reconnecting")
            if state["stamp"] is not None and slack_app.stamp(store) != state["stamp"]:
                LOG.info("Slack tokens changed; reconnecting")
                gateway.stop.set()

        gateway.heartbeat = heartbeat
        stopper = threading.Thread(target=lambda: (stopping.wait(), gateway.stop.set(), gateway.wake.set()), daemon=True)
        stopper.start()
        try:
            gateway.run()
        finally:
            for slug in list(gateway.bot_apps):
                gateway.drop_bot_app(slug)
            gateway.slack.close()
            LOG.info("Slack gateway stopped")
        if stopping.is_set() or not wait or state["stamp"] is None:
            return 0
        settings.slack_team_id = settings.slack_app_id = ""


if __name__ == "__main__":
    sys.exit(main())
