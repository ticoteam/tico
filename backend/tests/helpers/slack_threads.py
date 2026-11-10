"""Helpers that other tests share, moved out of test_slack_threads.py so they import no test file."""

import copy
import time
from backend import hubdb as H
from backend import slack_gateway as G


TEAM, APP, BOT_USER, BOT_ID = "T02F3S2SD", "A0BU75YSGT1", "U0BUS6WA4SY", "B0BUNG973DY"


MARKETING, PRIVATE, LOUNGE, PARTNERS = "C0000000001", "G0000000001", "C0000000002", "C0000000003"


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
        self.pages = {}  # channel -> top-level history
        self.posts, self.updates, self.deletes, self.ephemeral = [], [], [], []
        self.lose = []  # per send: None, "before" (Slack never got it), "after" (it did)
        self.page_size = 200  # the metadata lookups' page (a test makes it small to page)
        self.on_post = None  # a hook run as a post lands (a test revises the message mid-send)
        self.down = False  # True: Slack cannot be reached for the channel's check

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
        rows = [
            m
            for m in self.posts
            if m["channel"] == channel and not m["thread_ts"] and float(m["ts"]) > float(oldest or 0)
        ]
        return self._page(rows, cursor)

    def replies_metadata_page(self, channel, thread_ts, oldest, cursor=""):
        """The replies in one thread, as conversations.replies gives them (the root is not among them)."""
        rows = [
            m
            for m in self.posts
            if m["channel"] == channel and m["thread_ts"] == thread_ts and float(m["ts"]) > float(oldest or 0)
        ]
        return self._page(rows, cursor)

    def add_post(self, channel, thread_ts, text, delivery_id):
        """A message as Slack keeps it: the ts is the time it was made, so the reconcile window sees it."""
        ts = f"{time.time() + len(self.posts) * 0.001:.6f}"
        self.posts.append(
            {
                "channel": channel,
                "thread_ts": thread_ts,
                "text": text,
                "ts": ts,
                "metadata": {"event_type": "tico_thread_message", "event_payload": {"delivery_id": delivery_id}},
            }
        )
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
