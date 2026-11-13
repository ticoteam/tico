"""Calls in threads (docs/meet.md): a Google Meet, or a Tico Meet audio and screen-share call, that belongs to a thread.

A call is a live meeting (backend/live_meetings.py) that its thread governs, whichever provider runs it. A Google Meet
call (`provider` google_meet) is a Meet space Tico makes as the caller (backend/google_meet.py, the Workspace key's Meet
scope); Google runs the media, the sweep polls Google for who is in it, and a person's Tico Recorder, when one is the
call's recording source, writes its transcript into the call's live meeting. Everything below about LiveKit, tokens and
the server's recorder is Tico Meet's (`provider` tico) only.

For a Tico Meet call: The thread's audience decides who may join
and hear it, the bots a person with Write attaches reply in the call and in the thread, and the transcript goes into
the thread when the call ends. The media runs on LiveKit (backend/call_media.py): LiveKit keeps only the room, while
Tico keeps the call's state and its transcript. A person's token grants audio and screen share, is good for five
minutes, and is refreshed only while they are still in the call and may still read the thread.

Speech reaches the transcript through the Gateway's speech-to-text, and no raw audio is kept. A call survives a Tico
restart, because its state is in the database and the sweep picks it up; if LiveKit restarts and loses the room, the
call ends with its transcript kept.

The thread checks and posts are core's (backend/threads.py, docs/dev/threads-contract.md). A bot's rights on a teammate
are teammate_access's, as they are everywhere else.
"""

import json
import logging
import re
import threading
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import Request
from pydantic import Field

from . import (
    audit_log,
    call_items,
    call_media,
    call_ring,
    gateway_connection,
    google_meet,
    google_workspace,
    live_meetings,
    recorder_installs,
    recorder_relay,
    teammate_access,
    threads,
)
from .models import Contract
from .store import H, Problem
from .views import human_only

log = logging.getLogger("tico.calls")

ROOM_PREFIX = "tico-call-"
FLUSH_SECONDS = 8  # speech from one person that goes to the Gateway at once
IDLE_SECONDS = 3  # a person quiet this long has what they said sent
TRANSCRIPT_BYTES = 12_000
LIST_LIMIT = 50
PARTICIPANT_LIMIT = 200
PCM_BYTES_PER_SECOND = call_media.SAMPLE_RATE * 2
WORKERS = 4  # clips transcribed at once: the Gateway waits on them, the recorder's audio must not
DRAIN_SECONDS = 150  # the longest a call's end waits for its last clips (the Gateway itself stops at 120 s)
CALL_LIST_BYTES = 262_144  # as the audit list: a page stays under 256 KB (backend/audit_log.py)
START_GRACE_SECONDS = 120  # a call whose room never appeared this long after it started has failed to start

# A Gateway that does not answer at a call's end leaves its action items owed. The sweep tries again: the first retry
# two minutes on, each wait twice the last, never more than an hour. A day after the first failure the call says so in
# its thread, once, and gives up on them.
ACTION_ITEMS_RETRY_SECONDS = 60
ACTION_ITEMS_RETRY_MAX_SECONDS = 3600
ACTION_ITEMS_GIVE_UP_SECONDS = 24 * 3600
HOLDS_LIMIT = 20  # a call's holds, as its connector sees them: a page, not an archive

# Google Meet calls. `auto` tries the providers in this order: Google Meet when the caller's Workspace mailbox has it,
# else Tico Meet when LiveKit is set up.
PROVIDERS = ("google_meet", "tico")
NO_ANSWER_SECONDS = 120  # a Meet nobody was seen in this long after it started ends no_answer
# Google opens a conference as soon as anyone loads the Meet's pre-join page, before they join, so a conference alone is
# nobody in the call: only a listed participant is. A conference ends as soon as its last person leaves, and a rejoin
# makes a new one on the same space, so a Meet someone was seen in ends only after nobody has been seen in it this long.
EMPTY_SECONDS = 60
STALE_SECONDS = 8 * 3600  # a Meet with no status from Google this long after it started ends stale
# A Meet whose reads have failed (no answer, Google's own error, 429) keeps the status its last good read gave. One Google
# has not answered for this long counts as having no status from Google, for the stale rule.
QUIET_SECONDS = 600
# The sweep reads live Meets from Google on its own small pool, so a slow or hanging Google never holds up the heartbeat:
# it waits POLL_BUDGET_SECONDS for the pass, and a read still out then is made again on a later pass. A Meet whose read
# failed skips 1, 2, 4... passes before its next one, never more than POLL_SKIP_MAX.
POLL_WORKERS = 4
POLL_BUDGET_SECONDS = 5
POLL_SKIP_MAX = 16
PRESENCE_MAX = 100  # the names one presence report may carry
RINGING_LIMIT = 20  # the calls `GET /api/v2/calls/ringing` answers with at most
CHANGE_FIELDS = ("live_count", "status_source", "recorder_actor", "meet_seen_at")  # with in_call: call.updated

# The auth module, set by install_calls: an actor becomes the identity that the thread rules read.
AUTH = None


def readable(c, actor, thread_id):
    """Whether `actor` may read the thread, and so hear the call in it: the thread's own rule decides."""
    try:
        who = AUTH.identity_for_actor(c, actor)
    except Problem:
        return False
    return threads.readable(c, threads.viewer(AUTH, who), thread_id)


def attachment_standing(c, joined_by, thread_id, slug):
    """Whether a bot attached by `joined_by` still answers to that person's access: they may read the call's thread and
    may write to the bot. Only then does the bot hear the call or reply in it (live_privacy and the sweep check this)."""
    if not readable(c, joined_by, thread_id):
        return False
    try:
        who = AUTH.identity_for_actor(c, joined_by)
        return who.role in ("human", "owner") and teammate_access.can(c, who, "write", f"bot:{slug}", auth=AUTH)
    except Problem:
        return False


def cancel_bot_turns(c, meeting_id, slug, reason):
    """A bot that leaves a call answers none of its open turns in it."""
    for turn in c.execute(
        "SELECT * FROM live_meeting_turns WHERE meeting_id=? AND bot=? AND status IN ('pending','claimed')",
        (meeting_id, slug),
    ).fetchall():
        live_meetings._finish_pass(c, meeting_id, turn, reason)


def speak(c, call_id, text):
    """A bot's reply read aloud in the call. Not built: `start` refuses speak_replies until the Gateway offers voice
    (docs/meet.md), so no call has the flag and the reply goes to the thread as text only."""
    return None


def project_reply(c, rid, reply, who):
    """A bot's reply in a call's meeting also goes into the call's thread, as that bot's message. Registered as a
    live_meetings reply hook, so it commits with the reply: the thread gets the reply or nothing. The bot may write there
    only while its attacher still reads the thread and may write to the bot, and only through the run that answered: that
    run's grant is the one thread it is given, so the reply goes through the thread's own rules."""
    row = c.execute(
        "SELECT id,thread_id,connector_actor,speak_replies FROM calls WHERE live_meeting_id=?", (rid,)
    ).fetchone()
    if not row:
        return
    bot = H.actor_id(who.actor)
    attached = c.execute("SELECT joined_by FROM live_meeting_bots WHERE meeting_id=? AND bot=?", (rid, bot)).fetchone()
    if not attached or not attachment_standing(c, attached[0], row["thread_id"], bot):
        raise Problem("forbidden", "The person who attached this bot can no longer read the call's thread", 403)
    if (
        not who.attempt_id
        or not c.execute("SELECT 1 FROM attempts WHERE id=? AND bot_id=?", (who.attempt_id, bot)).fetchone()
    ):
        raise Problem("forbidden", "This bot's reply has no run of its own to post it in the call's thread", 403)
    c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (who.attempt_id, row["thread_id"]))
    threads.post(
        c,
        threads.viewer(AUTH, who),
        row["thread_id"],
        reply["text"],
        kind="say",
        to_actor=row["connector_actor"],
        refs={
            "call": {"id": row["id"], "meeting_id": rid},
            "live_meeting": {"meeting_id": rid, "chat_id": reply["id"]},
        },
    )
    if row["speak_replies"]:
        speak(c, row["id"], reply["text"])


class Empty(Contract):
    pass


class StartCall(Contract):
    title: str = Field(default="Call", min_length=1, max_length=200)
    speak_replies: bool = False  # a bot's reply read aloud: refused until the Gateway has text-to-speech
    provider: Literal["auto", "google_meet", "tico"] = "auto"


class Presence(Contract):
    names: list[Annotated[str, Field(max_length=200)]] = Field(max_length=PRESENCE_MAX)


class CallBots(Contract):
    bots: list[str] = Field(min_length=1, max_length=10)


def _call(c, call_id):
    row = c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
    if not row:
        raise Problem("not_found", "Call not found", 404)
    return dict(row)


def _participant(c, call_id, actor):
    row = c.execute("SELECT * FROM call_participants WHERE call_id=? AND actor=?", (call_id, actor)).fetchone()
    return dict(row) if row else None


def _stamp(moment):
    """A moment in the format every stored time uses (hubdb.now)."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _seconds_since(stamp):
    started = datetime.fromisoformat(stamp.rstrip("Z")).replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - started).total_seconds()


def _started_long_ago(started_at):
    started = datetime.fromisoformat(started_at.rstrip("Z")).replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - started).total_seconds() > START_GRACE_SECONDS


def _clock(ms):
    seconds = int(ms or 0) // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _summary(row):
    return {
        "id": row["id"],
        "thread_id": row["thread_id"],
        "title": row["title"],
        "state": row["state"],
        "connector": row["connector_actor"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "end_reason": row["end_reason"],
    }


def _between(start, end):
    parse = lambda stamp: datetime.fromisoformat(stamp.rstrip("Z")).replace(tzinfo=timezone.utc)
    return (parse(end) - parse(start)).total_seconds()


def _duration(row):
    """An ended call's length in seconds. A Google Meet's runs from the first time anyone was seen in it to the last
    (`meet_active_at`), and is None when nobody ever was: a missed call. A Tico Meet's runs from its start to its end."""
    if row["state"] != "ended" or not row["ended_at"]:
        return None
    if _google(row):
        if not row["meet_seen_at"]:
            return None
        return max(0, int(_between(row["meet_seen_at"], row["meet_active_at"] or row["meet_seen_at"])))
    return max(0, int(_between(row["started_at"], row["ended_at"])))


def _ring_list(row):
    """The actors a call rang at its start (its `ring` column, a JSON list)."""
    try:
        found = json.loads(row["ring"] or "[]")
    except (TypeError, ValueError):
        return []
    return [actor for actor in found if isinstance(actor, str)] if isinstance(found, list) else []


def _google(row):
    return row["provider"] == "google_meet"


def _join_url(row, address):
    """The Meet link as one viewer opens it: `authuser` makes Meet open on their own Google account."""
    if not _google(row) or not row["meet_uri"]:
        return None
    return row["meet_uri"] + ("?" + urllib.parse.urlencode({"authuser": address}) if address else "")


def _spoken(name):
    """A display name as matching compares it: case and runs of spaces do not count."""
    return re.sub(r"\s+", " ", str(name or "")).strip().casefold()


def _recorder_recent(c, who):
    """Whether the viewer has a Tico Recorder seen in the last 30 days (recorder_installs.mine's `recent`), without
    starting the release feed check `mine` would."""
    person = H.actor_id(who.actor)
    _version, at = recorder_installs._newest(c, [person]).get(person, ("", ""))
    return bool(at) and recorder_installs._recent(at)


class Calls:
    """The call operations. Writes run inside the caller's transaction (`c`) and record their audit row and threads change
    together with the change; the calls that reach LiveKit or the Gateway run outside any transaction."""

    def __init__(self, app, store, auth):
        self.app, self.store, self.auth = app, store, auth
        self.buffers = {}  # (call_id, identity) -> {"pcm": bytearray, "start_ms": int, "at": float}
        self.lock = threading.Lock()
        self.pending = {}  # call_id -> the clips it is still transcribing
        self.workers = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="tico-meet-stt")
        self.starting = {}  # (actor, idempotency key) -> the lock a Google Meet start holds while it makes its space
        self.pollers = ThreadPoolExecutor(max_workers=POLL_WORKERS, thread_name_prefix="tico-meet-poll")
        self.polling = {}  # call_id -> a status read a past pass left still out
        self.backoff = {}  # call_id -> [failures in a row, passes still to skip]
        self.poll_pass = threading.Lock()  # one polling pass at a time

    @property
    def media(self):
        return self.app.state.call_media

    # --- reads

    def view(self, c, call_id, who):
        """The call as `who` may see it: their own place in it always, and everyone else only where `who` may see them
        (the teammate See level, as everywhere else)."""
        row = _call(c, call_id)
        people = [
            {**dict(r), "in_call": self._in_call(row, r)}
            for r in c.execute(
                "SELECT actor,joined_at,left_at,removed_at,in_call FROM call_participants WHERE call_id=? "
                "ORDER BY joined_at,actor LIMIT ?",
                (call_id, PARTICIPANT_LIMIT),
            )
            if r["actor"] == who.actor or self._sees(c, who, r["actor"])
        ]
        bots = [
            r["bot"]
            for r in c.execute(
                "SELECT bot FROM live_meeting_bots WHERE meeting_id=? ORDER BY bot LIMIT ?",
                (row["live_meeting_id"], PARTICIPANT_LIMIT),
            )
            if self._sees(c, who, f"bot:{r['bot']}")
        ]
        holds = (
            [
                dict(r)
                for r in c.execute(
                    "SELECT kind,reason,detail,created_at FROM call_holds WHERE call_id=? ORDER BY created_at LIMIT ?",
                    (call_id, HOLDS_LIMIT),
                )
            ]
            if who.actor == row["connector_actor"]
            else []
        )
        return {
            "id": row["id"],
            "thread_id": row["thread_id"],
            "title": row["title"],
            "state": row["state"],
            "connector": row["connector_actor"],
            "meeting_id": row["live_meeting_id"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "end_reason": row["end_reason"],
            "participants": people,
            "bots": bots,
            "speak_replies": bool(row["speak_replies"]),
            "holds": holds,
            **self.provider_fields(c, row, who),
        }

    @staticmethod
    def _in_call(row, participant):
        """Whether the status source has the person in the call now. Tico Meet knows from its own room: joined and not
        left."""
        if _google(row):
            return bool(participant["in_call"]) and row["state"] == "live"
        return row["state"] == "live" and participant["left_at"] is None and participant["removed_at"] is None

    def provider_fields(self, c, row, who, *, address=None, installed=None):
        """What a call view adds for its provider and for this viewer (docs/dev/meet-calls-contract.md): the Meet link
        as they open it, the status source's count, whether the call rings them, its messages and its recording source.
        A list passes the viewer's `address` and `installed` once for all its rows."""
        google = _google(row)
        if google and address is None:
            address = google_workspace.google_address(c, who.actor) or ""
        if installed is None:
            installed = _recorder_recent(c, who)
        if google:
            live_count = int(row["live_count"] or 0) if row["state"] == "live" else 0
        else:
            live_count = c.execute(
                "SELECT COUNT(*) FROM call_participants WHERE call_id=? AND left_at IS NULL AND removed_at IS NULL",
                (row["id"],),
            ).fetchone()[0]
        joined = c.execute(
            "SELECT 1 FROM call_participants WHERE call_id=? AND actor=?", (row["id"], who.actor)
        ).fetchone()
        source = row["recorder_actor"]
        if source and source != who.actor and not self._sees(c, who, source):
            source = None
        return {
            "provider": row["provider"],
            "meet_uri": row["meet_uri"],
            "join_url": _join_url(row, address),
            "live_count": live_count,
            "status_source": row["status_source"],
            "seen_at": row["meet_seen_at"],
            "duration_s": _duration(row),
            "ring": row["state"] == "live" and who.actor in _ring_list(row) and not joined,
            "card_message_id": row["card_message_id"],
            "transcript_message_id": row["transcript_message_id"],
            "recorder": {"source": source, "installed": bool(installed)},
        }

    def _sees(self, c, who, target):
        try:
            return teammate_access.levels_for(c, who, target, self.auth)["see"]
        except Problem:
            return False

    def readable_view(self, c, who, call_id):
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        return self.view(c, call_id, who)

    def token_payload(self, row, actor):
        room = row["room_name"]
        return {
            "url": self.media.url,
            "expires_in": call_media.TOKEN_TTL_SECONDS,
            "token": self.media.person_token(room, actor, H.actor_id(actor)),
        }

    # --- writes

    def _record(self, c, actor, call_id, thread_id, kind, detail=None):
        """The audit row and the threads change for one call write."""
        audit_log.write(c, actor, f"call:{call_id}", None, {"kind": kind, **(detail or {})})
        c.execute(
            "INSERT INTO changes(topic,kind,subject_id,conversation_id,actor,created_at) VALUES('threads',?,?,?,?,?)",
            (kind, call_id, thread_id, actor, H.now()),
        )

    def _changed(self, c, row, before, in_call_before):
        """A `call.updated` change when what readers see of the call's status moved: its count, who is in it, its status
        source, its recording source or when it was first seen. No audit row: these are what Google or a Recorder
        reported, not anyone's act."""
        after = _call(c, row["id"])
        in_call_after = self._in_call_set(c, row["id"])
        if any(before[name] != after[name] for name in CHANGE_FIELDS) or in_call_before != in_call_after:
            c.execute(
                "INSERT INTO changes(topic,kind,subject_id,conversation_id,actor,created_at) "
                "VALUES('threads','call.updated',?,?,?,?)",
                (row["id"], row["thread_id"], row["connector_actor"], H.now()),
            )
            return True
        return False

    @staticmethod
    def _in_call_set(c, call_id):
        return {
            r[0] for r in c.execute("SELECT actor FROM call_participants WHERE call_id=? AND in_call=1", (call_id,))
        }

    def _connector(self, c, who, call_id):
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        if who.actor != row["connector_actor"]:
            raise Problem("forbidden", "Only the person who started this call can do that", 403)
        return row

    def _may_start(self, c, who, thread_id, speak_replies):
        """The checks a start makes before anything is made, whatever the provider, and again inside its write."""
        human_only(who)
        self.auth.domain(who)
        if speak_replies:
            # The Gateway offers no text-to-speech yet (docs/ai-gateway.md lists voice as coming soon), so the flag is
            # refused rather than kept and ignored.
            raise Problem(
                "unavailable",
                "Spoken replies are not available yet: the Gateway has no text-to-speech. "
                "Start the call without speak_replies.",
                422,
            )
        threads.require(c, threads.viewer(self.auth, who), thread_id, write=True)
        live = c.execute("SELECT id FROM calls WHERE thread_id=? AND state='live'", (thread_id,)).fetchone()
        if live:
            raise Problem(
                "call_in_progress", "This thread already has a call going", 409, extra={"call_id": live["id"]}
            )

    def providers(self, c, who):
        """The call providers `who` could start now, in the order `auto` tries them."""
        offered = []
        if google_workspace.meet_ready(c) and google_workspace.workspace_mailbox(c, who.actor):
            offered.append("google_meet")
        if self.media is not None:
            offered.append("tico")
        return offered

    def choose(self, c, who, thread_id, wanted, speak_replies=False):
        """The provider a start uses: `auto` is the first the caller has (`providers`); a named one must be there."""
        self._may_start(c, who, thread_id, speak_replies)
        offered = self.providers(c, who)
        if wanted == "auto" and offered:
            return offered[0]
        if wanted in offered:
            return wanted
        if wanted == "google_meet":
            raise Problem(
                "meet_unavailable",
                "Google Meet is not available to you: the Workspace key must cover your mailbox and be authorized "
                "for Meet",
                503,
            )
        if wanted == "tico":
            raise Problem("meet_unavailable", "Tico Meet is not set up on this server", 503)
        raise Problem("meet_unavailable", "Calls are not set up: connect Google Workspace with Meet, or Tico Meet", 503)

    def make_space(self, who):
        """A Google Meet space made as the caller's Workspace mailbox. Outside any transaction: it asks Google."""
        with self.store.read() as c:
            box = google_workspace.workspace_mailbox(c, who.actor)
            token = None
            if box:
                try:
                    token = google_workspace.mint(c, box, "meet")["access_token"]
                except Problem:
                    token = None
        if not token:
            raise Problem("meet_unavailable", "Google did not give Tico a Meet token for your mailbox", 503)
        try:
            return google_meet.create_space(token)
        except google_meet.MeetUnavailable as exc:
            raise Problem(
                "meet_unavailable",
                "Google Meet did not make the meeting. Try again in a minute.",
                503,
                extra={"kind": exc.kind},
                retryable=exc.kind == "unreachable",
            ) from None

    def _ring_for(self, c, who, thread_id):
        """Who a call rings (backend/call_ring.py): the thread's people other than the caller who can read it, when it
        has at most RING_MAX people; nobody in a bigger thread. Bots are never rung."""
        root = threads.root_of(c, threads.thread_row(c, thread_id))
        people = [actor for actor in threads._dm_members(c, root) if str(actor).startswith("human:")]
        if len(people) > call_ring.RING_MAX:
            return []
        return [actor for actor in people if actor != who.actor and readable(c, actor, thread_id)]

    def start(self, c, who, thread_id, title, speak_replies=False, provider="tico", space=None):
        """Store a call, its live meeting, its card in the thread and who it rings. A Google Meet start brings the space
        `make_space` made before this write; a Tico Meet one needs LiveKit set up."""
        self._may_start(c, who, thread_id, speak_replies)
        if provider == "tico" and self.media is None:
            raise Problem("meet_unavailable", "Tico Meet is not set up on this server", 503)
        if provider == "google_meet" and not space:
            raise Problem("meet_unavailable", "Google Meet did not make the meeting. Try again in a minute.", 503)
        meeting = live_meetings.Connect(title=title, visibility="private")
        meeting_id, call_id, stamp = str(uuid.uuid4()), str(uuid.uuid4()), H.now()
        c.execute(
            "INSERT INTO live_meetings(id,owner_actor,title,state,client_id,window_ms,cooldown_ms,reply_cap,"
            "threshold,created_at,updated_at,started_at,visibility,client,meeting_model,thread_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                meeting_id,
                who.actor,
                meeting.title,
                "live",
                None,
                live_meetings._window_ms(self.app, meeting),
                meeting.cooldown_seconds * 1000,
                meeting.reply_cap,
                meeting.threshold,
                stamp,
                stamp,
                stamp,
                "private",
                None,
                None,
                thread_id,
            ),
        )
        c.execute("INSERT INTO live_meeting_humans VALUES(?,?,?)", (meeting_id, who.actor, stamp))
        live_meetings._event(
            c, meeting_id, "meeting.state", {"state": "live", "connected_by": who.actor, "visibility": "private"}
        )
        space = space or {}
        c.execute(
            "INSERT INTO calls(id,thread_id,live_meeting_id,connector_actor,title,state,room_name,started_at,"
            "created_at,updated_at,speak_replies,provider,meet_space,meet_uri,meet_code,ring) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                call_id,
                thread_id,
                meeting_id,
                who.actor,
                meeting.title,
                "live",
                ROOM_PREFIX + call_id,
                stamp,
                stamp,
                stamp,
                int(bool(speak_replies)),
                provider,
                space.get("name"),
                space.get("meeting_uri"),
                space.get("meeting_code"),
                json.dumps(self._ring_for(c, who, thread_id)),
            ),
        )
        c.execute("INSERT INTO call_participants(call_id,actor,joined_at) VALUES(?,?,?)", (call_id, who.actor, stamp))
        self._record(c, who.actor, call_id, thread_id, "call.started", {"meeting_id": meeting_id, "provider": provider})
        # The call's card in the thread. A thread that refuses it still has the call: the card is its place in the
        # timeline, not the call, so the refusal is undone on its own and the start goes on without one.
        c.execute("SAVEPOINT call_card")
        try:
            card = threads.post(
                c,
                threads.viewer(self.auth, who),
                thread_id,
                "Call",
                kind="say",
                refs={"call": {"id": call_id, "meeting_id": meeting_id, "card": True}},
            )
        except Problem as exc:
            c.execute("ROLLBACK TO call_card")
            log.info("A call's card was not posted: %s", exc.code)
            card = None
        c.execute("RELEASE call_card")
        if isinstance(card, dict) and card.get("id") and not card.get("held"):
            c.execute("UPDATE calls SET card_message_id=? WHERE id=?", (card["id"], call_id))
        return self.view(c, call_id, who)

    def ring_started(self, call_id):
        """Ring the people a call that just started rings, through call_ring's hooks. After the start's write, outside
        any transaction, once per call: a retried start answered from its first answer does not ring again."""
        with self.store.read() as c:
            row = _call(c, call_id)
            actors = _ring_list(row)
            if row["state"] != "live" or not actors:
                return
            caller = H.actor_name(c, row["connector_actor"])
            try:
                who = self.auth.identity_for_actor(c, row["connector_actor"])
                shown = threads.view(c, threads.viewer(self.auth, who), threads.thread_row(c, row["thread_id"]))
                name = shown.get("name") or shown.get("title") or row["title"]
            except Problem:
                name = row["title"]
        payload = {
            "title": caller,
            "body": "# " + str(name),
            "url": f"/#/thread/{urllib.parse.quote(row['thread_id'])}?call={urllib.parse.quote(call_id)}",
            "tag": "call-" + call_id,
        }
        call_ring.ring(self.app, call_id, actors, payload)

    def replayed(self, request, who):
        """Whether this request's idempotency key already has its answer stored, so the write will answer from it."""
        with self.store.read() as c:
            return self.store.saved(c, who, request.url.path, request.headers.get("idempotency-key"))

    def start_lock(self, actor, key):
        """One lock per (caller, idempotency key), so two copies of one start cannot both make a Meet space."""
        with self.lock:
            entry = self.starting.setdefault((actor, key), [threading.Lock(), 0])
            entry[1] += 1
        return entry

    def start_unlock(self, actor, key, entry):
        with self.lock:
            entry[1] -= 1
            if entry[1] <= 0:
                self.starting.pop((actor, key), None)

    def check_join(self, c, who, call_id):
        """Whether `who` may join this call now: the thread still lets them read it, and the call is live. It runs before a
        cached retry is answered, and again before a token is minted."""
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        if row["state"] != "live":
            raise Problem("call_ended", "This call has ended", 409)
        return row

    def join(self, c, who, call_id):
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        if row["state"] != "live":
            raise Problem("call_ended", "This call has ended", 409)
        stamp = H.now()
        if _google(row):
            # Opening the Meet from Tico. The row's joined_at stays the first open (or the first time Google saw them).
            c.execute(
                "INSERT INTO call_participants(call_id,actor,joined_at) VALUES(?,?,?) "
                "ON CONFLICT(call_id,actor) DO UPDATE SET left_at=NULL,removed_at=NULL",
                (call_id, who.actor, stamp),
            )
        else:
            c.execute(
                "INSERT INTO call_participants(call_id,actor,joined_at) VALUES(?,?,?) "
                "ON CONFLICT(call_id,actor) DO UPDATE SET joined_at=excluded.joined_at,left_at=NULL,removed_at=NULL,"
                "media_removed_at=NULL",
                (call_id, who.actor, stamp),
            )
        c.execute("INSERT OR IGNORE INTO live_meeting_humans VALUES(?,?,?)", (row["live_meeting_id"], who.actor, stamp))
        self._record(c, who.actor, call_id, row["thread_id"], "call.joined", {"actor": who.actor})
        return self.view(c, call_id, who)

    def leave(self, c, who, call_id):
        """Leave the call. A person whose access has ended may still leave, to drop their LiveKit connection, and gets only
        an acknowledgement. A call they cannot read and never joined answers as a missing one does."""
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        participant = _participant(c, call_id, who.actor)
        current = readable(c, who.actor, row["thread_id"])
        if not current and not (participant and participant["left_at"] is None):
            raise Problem("not_found", "Call not found", 404)
        if not participant:
            raise Problem("not_joined", "You are not in this call", 409)
        if participant["left_at"] is None:
            c.execute(
                "UPDATE call_participants SET left_at=?,media_removed_at=NULL WHERE call_id=? AND actor=?",
                (H.now(), call_id, who.actor),
            )
            self._record(c, who.actor, call_id, row["thread_id"], "call.left", {"actor": who.actor})
        if not current:
            return {"id": call_id, "left": True}
        return self.view(c, call_id, who)

    def end(self, c, who, call_id):
        row = self._connector(c, who, call_id)
        if row["state"] == "ended":
            return self.view(c, call_id, who)
        self.finish(c, call_id, "ended_by_connector", by=who.actor)
        return self.view(c, call_id, who)

    def retry_action_items(self, c, who, call_id):
        """The connector asks again for action items the Gateway could not make. Nothing is drafted here: the call is
        open to the sweep once more, so the Gateway is never called inside the request."""
        row = self._connector(c, who, call_id)
        if row["state"] != "ended":
            raise Problem("call_live", "The call is still live: its action items are made once it ends", 409)
        if row["action_items_posted_at"]:
            return self.view(c, call_id, who)
        c.execute(
            "UPDATE calls SET action_items_failed_at=NULL,action_items_attempts=0,action_items_retry_at=NULL,"
            "action_items_given_up_at=NULL,finalized_at=NULL,updated_at=? WHERE id=?",
            (H.now(), call_id),
        )
        self._record(c, who.actor, call_id, row["thread_id"], "call.action_items_retry", {})
        return self.view(c, call_id, who)

    def finish(self, c, call_id, reason, by=None):
        """End a live call: its state, its people and its meeting. Returns False when it had already ended."""
        row = _call(c, call_id)
        if row["state"] == "ended":
            return False
        stamp = H.now()
        actor = by or row["connector_actor"]
        c.execute(
            "UPDATE calls SET state='ended',end_reason=?,ended_at=?,updated_at=? WHERE id=?",
            (reason, stamp, stamp, call_id),
        )
        c.execute(
            "UPDATE call_participants SET left_at=? WHERE call_id=? AND left_at IS NULL AND removed_at IS NULL",
            (stamp, call_id),
        )
        c.execute("UPDATE call_participants SET in_call=0 WHERE call_id=? AND in_call=1", (call_id,))
        c.execute("UPDATE calls SET live_count=0 WHERE id=?", (call_id,))
        c.execute(
            "UPDATE live_meetings SET state='ended',ended_at=?,updated_at=? WHERE id=?",
            (stamp, stamp, row["live_meeting_id"]),
        )
        live_meetings._event(
            c, row["live_meeting_id"], "meeting.state", {"state": "ended", "by": actor, "action": reason}
        )
        live_meetings._end_open_turns(c, row["live_meeting_id"])
        self._record(c, actor, call_id, row["thread_id"], "call.ended", {"reason": reason})
        return True

    def attach_bots(self, c, who, call_id, slugs):
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        meeting = live_meetings._meeting(c, row["live_meeting_id"])
        if row["state"] != "live" or meeting["state"] not in ("live", "paused"):
            raise Problem("meeting_state", "Bots can only join a live call", 409)
        if not c.execute(
            "SELECT 1 FROM live_meeting_humans WHERE meeting_id=? AND actor=?", (meeting["id"], who.actor)
        ).fetchone():
            raise Problem("forbidden", "Join this call before attaching bots", 403)
        for slug in slugs:
            live_meetings._active_bot(c, slug)
            access = teammate_access.levels_for(c, who, f"bot:{slug}", self.auth)
            if not access["see"]:
                raise Problem("forbidden", f"You cannot see org-chart bot {slug}", 403)
            if not access["write"]:
                raise Problem("forbidden", f"You can see {slug} but do not have Write access to contact it", 403)
            cursor = c.execute(
                "INSERT OR IGNORE INTO live_meeting_bots(meeting_id,bot,joined_by,joined_at) VALUES(?,?,?,?)",
                (meeting["id"], slug, who.actor, H.now()),
            )
            if cursor.rowcount:
                live_meetings._event(
                    c,
                    meeting["id"],
                    "meeting.bot_joined",
                    {"bots": [slug], "joined_by": who.actor, "rights": "meeting_read_and_reply"},
                )
                self._record(
                    c, who.actor, call_id, row["thread_id"], "call.bot_attached", {"bot": slug, "joined_by": who.actor}
                )
        return self.view(c, call_id, who)

    def detach_bot(self, c, who, call_id, slug):
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        if (
            who.actor != row["connector_actor"]
            and not c.execute(
                "SELECT 1 FROM live_meeting_bots WHERE meeting_id=? AND bot=? AND joined_by=?",
                (row["live_meeting_id"], slug, who.actor),
            ).fetchone()
        ):
            raise Problem("forbidden", "Only the person who attached this bot can remove it", 403)
        cursor = c.execute("DELETE FROM live_meeting_bots WHERE meeting_id=? AND bot=?", (row["live_meeting_id"], slug))
        if cursor.rowcount:
            cancel_bot_turns(c, row["live_meeting_id"], slug, "bot_detached")
            live_meetings._event(c, row["live_meeting_id"], "meeting.bot_left", {"bot": slug, "by": who.actor})
            self._record(c, who.actor, call_id, row["thread_id"], "call.bot_detached", {"bot": slug})
        return self.view(c, call_id, who)

    # --- media, transcript and the thread, around the writes above

    def begin_media(self, call_id):
        """Start the server's recorder in the call's room after the call is stored. Without the media server the call
        ends at once, with a refusal, so no one joins a room that will not be heard."""
        with self.store.read() as c:
            row = _call(c, call_id)
        if _google(row):
            return  # Google runs a Meet's media; nothing of LiveKit's starts
        try:
            self.media.start_recorder(call_id, row["room_name"], self._audio_for(call_id))
        except call_media.MediaUnavailable:
            with self.store.transaction() as c:
                self.finish(c, call_id, "meet_unavailable")
                c.execute("UPDATE calls SET finalized_at=? WHERE id=?", (H.now(), call_id))  # nothing was said
            raise Problem("meet_unavailable", "Tico Meet's media server could not start this call", 503) from None
        with self.store.transaction() as c:
            c.execute("UPDATE calls SET media_seen_at=? WHERE id=? AND media_seen_at IS NULL", (H.now(), call_id))

    def _audio_for(self, call_id):
        def on_audio(identity, pcm, start_ms):
            self.ingest(call_id, identity, pcm, start_ms)

        return on_audio

    def ingest(self, call_id, identity, pcm, start_ms):
        """One piece of one person's speech from the recorder. It waits in memory until FLUSH_SECONDS have come in."""
        key = (call_id, identity)
        with self.lock:
            buffer = self.buffers.setdefault(key, {"pcm": bytearray(), "start_ms": start_ms, "at": 0.0})
            buffer["pcm"] += pcm
            buffer["at"] = time.monotonic()
            if len(buffer["pcm"]) < FLUSH_SECONDS * PCM_BYTES_PER_SECOND:
                return
            taken = self.buffers.pop(key)
        self._submit(call_id, identity, taken)

    def flush(self, call_id=None, *, idle=False):
        """Send the speech that is waiting: all of it for the call (or every call), or only what has been quiet for
        IDLE_SECONDS."""
        now = time.monotonic()
        with self.lock:
            keys = [
                key
                for key, buffer in self.buffers.items()
                if (call_id is None or key[0] == call_id) and (not idle or now - buffer["at"] >= IDLE_SECONDS)
            ]
            taken = [(key, self.buffers.pop(key)) for key in keys]
        for (cid, identity), buffer in taken:
            self._submit(cid, identity, buffer)

    def _submit(self, call_id, identity, buffer):
        """Transcribe a clip on a worker, so the recorder's audio never waits on the Gateway."""
        future = self.workers.submit(self._transcribe_clip, call_id, identity, buffer)
        with self.lock:
            self.pending.setdefault(call_id, []).append(future)

    def drain(self, call_id):
        """Wait (up to DRAIN_SECONDS) for the clips a call is still transcribing, so its transcript has them all."""
        with self.lock:
            waiting = self.pending.pop(call_id, [])
        wait(waiting, timeout=DRAIN_SECONDS)

    def finish_speech(self, call_id):
        """Send the speech still waiting for a call, and wait for it: the call's transcript must have every clip."""
        self.flush(call_id)
        self.drain(call_id)

    def _transcribe_clip(self, call_id, identity, buffer):
        try:
            self._transcribe(call_id, identity, buffer)
        except Exception as exc:  # noqa: BLE001 - a failed transcription is reported, the call goes on
            from .batch_work import report

            report("calls_transcribe", call_id, exc, logger="tico.calls")

    def _transcribe(self, call_id, identity, buffer):
        pcm = bytes(buffer["pcm"])
        if not pcm:
            return
        with self.store.read() as c:
            state = c.execute("SELECT state FROM calls WHERE id=?", (call_id,)).fetchone()
            if not state or state["state"] != "live":
                return  # the call has ended: its speech was sent at the end, and nothing after it is kept
            key, base, _fingerprint = gateway_connection.target(c, self.app.state.vault, self.store.settings)
        if not key:
            log.warning("A call's speech was not transcribed: no Gateway connection is set")
            return
        try:
            text = call_media.transcribe(base, key, call_media.wav_bytes(pcm))
        except call_media.MediaUnavailable:
            return  # this clip is lost and the call goes on; the Gateway's refusal is in its own log
        if not text:
            return
        duration_ms = len(pcm) * 1000 // PCM_BYTES_PER_SECOND
        with self.store.transaction() as c:
            row = _call(c, call_id)
            meeting = live_meetings._meeting(c, row["live_meeting_id"])
            if row["state"] != "live" or meeting["state"] != "live":
                return
            previous = c.execute(
                "SELECT COALESCE(MAX(start_ms),0) FROM live_meeting_chunks WHERE meeting_id=?", (meeting["id"],)
            ).fetchone()[0]
            start = max(buffer["start_ms"], previous)
            live_meetings.append_chunks(
                c,
                meeting["id"],
                meeting,
                identity,
                [
                    {
                        "seq": meeting["seq"] + 1,
                        "speaker": H.actor_id(identity),
                        "start_ms": start,
                        "end_ms": start + duration_ms,
                        "text": text,
                    }
                ],
            )
        self.app.state.live_meeting_routing.later(meeting["id"])

    def after_end(self, call_id):
        """What an ended call still owes: its room closed, its last windows, its transcript and its action items in the
        thread. Returns False while any of that is pending, so the sweep comes back to it; once it is all settled the call
        is marked finalized and nothing here runs again."""
        with self.store.read() as c:
            row = _call(c, call_id)
        if row["finalized_at"]:
            return True
        if row["wrapped_up_at"] is None:
            if (
                _google(row)
                and row["recorder_actor"]
                and row["ended_at"]
                and _seconds_since(row["ended_at"]) < live_meetings.RECORDER_TAIL_SECONDS
            ):
                return False  # the Recorder's last chunks may still be on their way (live_meetings.recorder_tail)
            if self.media is not None and not _google(row):
                try:
                    self.media.stop_recorder(call_id)
                    self.media.end_room(row["room_name"])
                except call_media.MediaUnavailable:
                    log.warning("A call ended, but the media server did not confirm the room closed")
                    return False
            self.drain(call_id)
            try:
                live_meetings._run_windows(self.app, row["live_meeting_id"], final=True)
            except Exception as exc:  # noqa: BLE001 - a failed final window is reported; the transcript is still posted
                from .batch_work import report

                report("calls_final_windows", call_id, exc, logger="tico.calls")
            self.post_transcript(call_id)
            with self.store.transaction() as c:
                c.execute("UPDATE calls SET wrapped_up_at=? WHERE id=? AND wrapped_up_at IS NULL", (H.now(), call_id))
        if not self.post_action_items(call_id):
            return False
        with self.store.transaction() as c:
            c.execute("UPDATE calls SET finalized_at=? WHERE id=? AND finalized_at IS NULL", (H.now(), call_id))
        return True

    def _post_in_thread(self, c, row, body):
        """One message in the call's thread, from its connector, naming the call and its meeting as the source. The
        live_meeting reference makes the meeting's own rules decide who reads the message. Returns the message."""
        who = self.auth.identity_for_actor(c, row["connector_actor"])
        return threads.post(
            c,
            who,
            row["thread_id"],
            body,
            kind="say",
            refs={
                "call": {"id": row["id"], "meeting_id": row["live_meeting_id"]},
                "live_meeting": {"meeting_id": row["live_meeting_id"]},
            },
        )

    def post_transcript(self, call_id):
        """The call's transcript, bounded, as one message in its thread, with the meeting it came from. Once only. A
        thread that refuses the post leaves it unposted: that refusal is a hold the connector sees, and it is final. A
        Google Meet call has a transcript only when a Recorder wrote one: with none, nothing is posted."""
        try:
            with self.store.transaction() as c:
                row = _call(c, call_id)
                if row["transcript_posted_at"]:
                    return
                lines, size, cut = [], 0, False
                for chunk in c.execute(
                    "SELECT speaker,text FROM live_meeting_chunks WHERE meeting_id=? ORDER BY seq",
                    (row["live_meeting_id"],),
                ):
                    line = f"{chunk['speaker']}: {chunk['text']}"
                    if size + len(line) > TRANSCRIPT_BYTES:
                        cut = True
                        break
                    lines.append(line)
                    size += len(line) + 1
                if not lines and _google(row):
                    return
                body = f"Call transcript: {row['title']}\n" + ("\n".join(lines) or "(no speech was transcribed)")
                if cut:
                    body += f"\n(cut short; the full transcript is on meeting {row['live_meeting_id']})"
                posted = self._post_in_thread(c, row, body)
                c.execute(
                    "UPDATE calls SET transcript_posted_at=?,transcript_message_id=? WHERE id=?",
                    (H.now(), posted.get("id") if isinstance(posted, dict) else None, call_id),
                )
        except Problem as exc:
            from .batch_work import report

            report("calls_transcript", call_id, exc, logger="tico.calls")
            self._hold(call_id, "transcript", exc)

    def post_action_items(self, call_id):
        """Action items drafted from the transcript, as one message in the thread, once. Returns False while they are still
        owed: the Gateway did not answer, and the sweep tries again later (_action_items_failed). A call with no speech, or
        with no Gateway set up, owes none."""
        with self.store.read() as c:
            row = _call(c, call_id)
            if row["action_items_posted_at"] or row["action_items_given_up_at"]:
                return True
            if row["action_items_retry_at"] and row["action_items_retry_at"] > H.now():
                return False
            rows = [
                {"t": _clock(r["start_ms"]), "speaker": r["speaker"], "text": r["text"]}
                for r in c.execute(
                    "SELECT start_ms,speaker,text FROM live_meeting_chunks WHERE meeting_id=? ORDER BY seq",
                    (row["live_meeting_id"],),
                )
            ]
            key, base, _fingerprint = gateway_connection.target(c, self.app.state.vault, self.store.settings)
            model = recorder_relay.chat_model(c, self.store.settings)
        if not rows or not key:
            return True
        try:
            items = call_items.draft(base, key, model, rows)
        except call_items.ItemsUnavailable as exc:
            log.warning("A call's action items were not drafted: %s", exc)
            return self._action_items_failed(call_id)
        listed = "\n".join(f"- {item['title']}" for item in items) or "No action items were found."
        body = f"Action items: {row['title']}\n{listed}"
        try:
            with self.store.transaction() as c:
                row = _call(c, call_id)
                if row["action_items_posted_at"]:
                    return True
                self._post_in_thread(c, row, body)
                c.execute(
                    "UPDATE calls SET action_items_posted_at=?,action_items_retry_at=NULL,updated_at=? WHERE id=?",
                    (H.now(), H.now(), call_id),
                )
        except Problem as exc:
            from .batch_work import report

            report("calls_action_items", call_id, exc, logger="tico.calls")
            self._hold(call_id, "action_items", exc)
        return True

    def _action_items_failed(self, call_id):
        """The Gateway did not answer: the items stay owed, the next try waits longer than the last, and a day after the first
        failure the call gives up on them (_give_up). Returns False while they are still owed."""
        now = H.now()
        with self.store.transaction() as c:
            row = _call(c, call_id)
            failed_at = row["action_items_failed_at"] or now
            if _seconds_since(failed_at) < ACTION_ITEMS_GIVE_UP_SECONDS:
                attempts = row["action_items_attempts"] + 1
                wait = min(ACTION_ITEMS_RETRY_MAX_SECONDS, ACTION_ITEMS_RETRY_SECONDS * 2**attempts)
                retry_at = _stamp(datetime.now(timezone.utc) + timedelta(seconds=wait))
                c.execute(
                    "UPDATE calls SET action_items_failed_at=?,action_items_attempts=?,action_items_retry_at=?,"
                    "updated_at=? WHERE id=?",
                    (failed_at, attempts, retry_at, now, call_id),
                )
                return False
        self._give_up(call_id)
        return True

    def _give_up(self, call_id):
        """Say once in the thread that the action items could not be made, and how to ask again. A thread that refuses even
        that line leaves a hold, as any refused post does."""
        with self.store.read() as c:
            title = _call(c, call_id)["title"]
        body = (
            f"Action items for {title} could not be made: the Gateway did not answer for a day. The connector "
            f"can retry them with `tico call retry {call_id}` or POST /api/v2/calls/{call_id}/action-items/retry."
        )
        try:
            with self.store.transaction() as c:
                row = _call(c, call_id)
                self._post_in_thread(c, row, body)
                self._record(c, row["connector_actor"], call_id, row["thread_id"], "call.action_items_given_up", {})
        except Problem as exc:
            from .batch_work import report

            report("calls_action_items", call_id, exc, logger="tico.calls")
            self._hold(call_id, "action_items", exc)
        with self.store.transaction() as c:
            c.execute(
                "UPDATE calls SET action_items_given_up_at=?,updated_at=? WHERE id=?", (H.now(), H.now(), call_id)
            )

    def _hold(self, call_id, kind, exc):
        """A post the thread refused: a hold the call's connector sees on the call, and an audit row. It is final, since the
        thread's own rule said no, so nothing retries it."""
        with self.store.transaction() as c:
            row = _call(c, call_id)
            c.execute(
                "INSERT INTO call_holds(id,call_id,kind,reason,detail,created_at) VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), call_id, kind, exc.code, exc.detail, H.now()),
            )
            self._record(
                c,
                row["connector_actor"],
                call_id,
                row["thread_id"],
                "call.post_refused",
                {"post": kind, "reason": exc.code},
            )

    # --- the sweep: access that ended, bots whose attacher lost the thread, LiveKit drops, and a LiveKit that restarted

    def sweep(self):
        """Keep calls true to their threads (the scheduler's tick). Nothing here is a request, so a call that cannot reach
        LiveKit waits for the next tick. An ended call comes back until its room is closed, its transcript is posted and
        LiveKit has dropped everyone who left. Live Google Meets are read from Google last, all together (poll_live)."""
        self.flush(idle=True)
        with self.store.read() as c:
            ids = [
                r["id"]
                for r in c.execute(
                    "SELECT id FROM calls WHERE state='live' OR finalized_at IS NULL OR (provider='tico' AND id IN "
                    "(SELECT call_id FROM call_participants WHERE media_removed_at IS NULL AND "
                    "(left_at IS NOT NULL OR removed_at IS NOT NULL)))"
                )
            ]
        polls = []
        for call_id in ids:
            try:
                if self._sweep_call(call_id):
                    polls.append(call_id)
            except call_media.MediaUnavailable:
                continue
            except Exception as exc:  # noqa: BLE001 - one call's sweep failing must not stop the others
                from .batch_work import report

                report("calls_sweep", call_id, exc, logger="tico.calls")
        self.poll_live(polls)

    def _sweep_call(self, call_id):
        """One call's pass. Returns True for a live Google Meet, whose status the sweep then reads from Google."""
        with self.store.read() as c:
            row = _call(c, call_id)
        if row["state"] == "ended":
            if not _google(row):
                self.drop_pending(call_id)
            if row["finalized_at"] is None:
                self.after_end(call_id)
            return
        with self.store.read() as c:
            people = [
                r["actor"]
                for r in c.execute(
                    "SELECT actor FROM call_participants WHERE call_id=? AND left_at IS NULL AND removed_at IS NULL",
                    (call_id,),
                )
            ]
            revoked = [actor for actor in people if not readable(c, actor, row["thread_id"])]
            stale_bots = [
                r["bot"]
                for r in c.execute(
                    "SELECT bot,joined_by FROM live_meeting_bots WHERE meeting_id=?", (row["live_meeting_id"],)
                )
                if not attachment_standing(c, r["joined_by"], row["thread_id"], r["bot"])
            ]
        if _google(row):
            with self.store.transaction() as c:
                self._keep_to_thread(c, row, revoked, stale_bots)
            return True
        room = row["room_name"]
        exists = self.media.room_exists(room)
        if not exists and (row["media_seen_at"] or _started_long_ago(row["started_at"])):
            # Speech already said is transcribed while the call is still live: _transcribe keeps a clip only then.
            self.finish_speech(call_id)
            with self.store.transaction() as c:
                self.finish(c, call_id, "media_lost")
            self.after_end(call_id)
            return
        with self.store.transaction() as c:
            if exists and row["media_seen_at"] is None:
                c.execute("UPDATE calls SET media_seen_at=? WHERE id=?", (H.now(), call_id))
            self._keep_to_thread(c, row, revoked, stale_bots)
        self.drop_pending(call_id)
        if exists and not self.media.running(call_id):
            self.media.start_recorder(call_id, room, self._audio_for(call_id))

    def _keep_to_thread(self, c, row, revoked, stale_bots):
        """Take out of the call the people who lost the thread, and the bots whose attacher did."""
        call_id = row["id"]
        for actor in revoked:
            cursor = c.execute(
                "UPDATE call_participants SET removed_at=? WHERE call_id=? AND actor=? "
                "AND left_at IS NULL AND removed_at IS NULL",
                (H.now(), call_id, actor),
            )
            if cursor.rowcount:
                self._record(c, actor, call_id, row["thread_id"], "call.participant_removed", {"actor": actor})
        for slug in stale_bots:
            cancel_bot_turns(c, row["live_meeting_id"], slug, "attacher_lost_thread")
            c.execute("DELETE FROM live_meeting_bots WHERE meeting_id=? AND bot=?", (row["live_meeting_id"], slug))
            live_meetings._event(c, row["live_meeting_id"], "meeting.bot_left", {"bot": slug, "by": "access_ended"})
            self._record(
                c,
                row["connector_actor"],
                call_id,
                row["thread_id"],
                "call.bot_detached",
                {"bot": slug, "reason": "attacher_lost_thread"},
            )

    # --- Google Meet: status from Google (or the Recorder), the recording source, and ending the conference

    def _mint_meet(self, c, actor):
        """A Meet access token for `actor`'s Workspace mailbox. Raises google_meet.MeetUnavailable: `refused` when Meet is
        not authorized, the mailbox is not covered or Google names its refusal; `unreachable` when Google's token answer
        names nothing (no answer, or its own error), which passes. Reads only."""
        if not google_workspace.meet_ready(c):
            raise google_meet.MeetUnavailable("refused")
        box = google_workspace.workspace_mailbox(c, actor)
        if not box:
            raise google_meet.MeetUnavailable("refused")
        try:
            return google_workspace.mint(c, box, "meet")["access_token"]
        except Problem as exc:
            passes = exc.extra.get("kind") == "other"  # Google's token answer named no refusal
            raise google_meet.MeetUnavailable("unreachable" if passes else "refused") from None

    def _meet_token(self, c, actor):
        """A Meet access token for `actor`'s Workspace mailbox, or None when there is none to have. Reads only."""
        try:
            return self._mint_meet(c, actor)
        except google_meet.MeetUnavailable:
            return None

    def read_status(self, call_id):
        """Google's answer for one live Google Meet, read as its connector with no transaction open: the space, then who
        is still in its active conference, each read once. {"ok": True, "conference", "people"}; {"ok": False, "refused":
        True} when Google will not give a status (no token for the mailbox, the scope or the API refused, no such space);
        {"ok": False, "refused": False} when Google did not answer (a timeout, its own error, 429), which passes and
        changes nothing. None when the call is no longer a live Meet."""
        try:
            with self.store.read() as c:
                row = _call(c, call_id)
                if not _google(row) or row["state"] != "live":
                    return None
                token = self._mint_meet(c, row["connector_actor"])
            space = google_meet.get_space(token, row["meet_space"])
            people = google_meet.active_participants(token, space["conference"]) if space["conference"] else []
        except google_meet.MeetUnavailable as exc:
            log.info("A Meet call's status read failed: %s", exc.kind)
            return {"ok": False, "refused": exc.kind != "unreachable"}
        return {"ok": True, "conference": space["conference"], "people": people}

    def poll(self, call_id):
        """One status read of a live Google Meet call, written, and the call wrapped up when the read ended it."""
        seen = self.read_status(call_id)
        if seen is None:
            return
        if self.apply_status(call_id, seen):
            self.lock_later(call_id)
            self.after_end(call_id)

    def poll_live(self, ids):
        """Read every live Google Meet in `ids` from Google at once, on the poll pool, and write what each read said.
        The pass waits POLL_BUDGET_SECONDS at most: a read still out then is left to finish and the call is read again
        on a later pass, never twice at once. A call whose read failed waits (`backoff`) before its next. Calls a read
        ended are wrapped up by the next pass of the sweep, so this pass stays inside its budget."""
        if not self.poll_pass.acquire(blocking=False):
            return
        try:
            live = set(ids)
            for gone in [call_id for call_id in self.backoff if call_id not in live]:
                self.backoff.pop(gone, None)
            due = []
            for call_id in ids:
                out = self.polling.get(call_id)
                if out is not None and not out.done():
                    continue
                self.polling.pop(call_id, None)
                waiting = self.backoff.get(call_id)
                if waiting and waiting[1] > 0:
                    waiting[1] -= 1
                    continue
                due.append(call_id)
            if not due:
                return
            futures = {self.pollers.submit(self.read_status, call_id): call_id for call_id in due}
            done, late = wait(futures, timeout=POLL_BUDGET_SECONDS)
            for future in late:
                if not future.cancel():
                    self.polling[futures[future]] = future
            index = None
            for future, call_id in futures.items():
                if future not in done:
                    continue
                try:
                    seen = future.result()
                    if seen is None:
                        continue
                    if seen["ok"]:
                        self.backoff.pop(call_id, None)
                    else:
                        failures = (self.backoff.get(call_id) or [0, 0])[0] + 1
                        self.backoff[call_id] = [failures, min(2 ** (failures - 1), POLL_SKIP_MAX)]
                    if index is None and seen["ok"] and seen["people"]:
                        with self.store.read() as c:
                            index = self._people_index(c)  # the roster, read once for the whole pass
                    if self.apply_status(call_id, seen, index):
                        self.lock_later(call_id)
                except Exception as exc:  # noqa: BLE001 - one call's status failing must not stop the others
                    from .batch_work import report

                    report("calls_poll", call_id, exc, logger="tico.calls")
        finally:
            self.poll_pass.release()

    def apply_status(self, call_id, seen, index=None):
        """Write what one read said (read_status), in one transaction with no request to Google in it, and end the call
        when that is what it says. Only a good read changes the status: a read Google did not answer keeps the count,
        who is in the call and the source as the last good read left them, and never ends the call but as stale.
        A conference with nobody listed in it is nobody in the call (Google opens one for the pre-join page), and a call
        may span several conferences (each rejoin after everyone left makes a new one). A call someone was seen in ends
        `meet_ended` once good reads have shown nobody for EMPTY_SECONDS (`meet_empty_at`, the first of them); one
        nobody was seen in within NO_ANSWER_SECONDS of its start ends `no_answer`. Google refusing leaves the status to
        the Recorder, or to nothing, and a call with no status from Google ends `stale` STALE_SECONDS after it
        started. Returns the end reason, or None."""
        ended = None
        with self.store.transaction() as c:
            row = _call(c, call_id)
            if not _google(row) or row["state"] != "live":
                return None
            self._drop_lost_source(c, row)
            row = _call(c, call_id)
            if seen["ok"]:
                people = seen["people"]
                self._set_status(c, row, "google", people, seen["conference"], index)
                row = _call(c, call_id)
                if people:
                    ended = None  # someone is in it: the call goes on, whichever conference they are in
                elif row["meet_seen_at"]:
                    if row["meet_empty_at"] and _seconds_since(row["meet_empty_at"]) >= EMPTY_SECONDS:
                        ended = "meet_ended"
                elif _seconds_since(row["started_at"]) > NO_ANSWER_SECONDS:
                    ended = "no_answer"
            else:
                if seen["refused"] and row["status_source"] == "google":
                    self._set_status(c, row, None, [], None)
                    row = _call(c, call_id)
                quiet = (
                    row["status_source"] != "google"
                    or not row["meet_polled_at"]
                    or _seconds_since(row["meet_polled_at"]) > QUIET_SECONDS
                )
                if quiet and _seconds_since(row["started_at"]) > STALE_SECONDS:
                    ended = "stale"
            if ended:
                self.finish(c, call_id, ended)
        return ended

    def _drop_lost_source(self, c, row):
        """A recording source who can no longer read the thread is no longer the source."""
        if row["recorder_actor"] and not readable(c, row["recorder_actor"], row["thread_id"]):
            before, in_call = _call(c, row["id"]), self._in_call_set(c, row["id"])
            c.execute(
                "UPDATE calls SET recorder_actor=NULL,recorder_claimed_at=NULL,status_source=CASE WHEN "
                "status_source='recorder' THEN NULL ELSE status_source END,live_count=CASE WHEN "
                "status_source='recorder' THEN 0 ELSE live_count END,updated_at=? WHERE id=?",
                (H.now(), row["id"]),
            )
            self._changed(c, row, before, in_call)

    @staticmethod
    def _people_index(c):
        """The company's people by spoken display name (a set: names can repeat) and by email, for matching."""
        from .views import roster

        names, emails = {}, {}
        for person in roster(c).get("people") or []:
            if person.get("hidden") or person.get("directory_left"):
                continue
            actor = "human:" + str(person["id"])
            if _spoken(person.get("name")):
                names.setdefault(_spoken(person.get("name")), set()).add(actor)
            if person.get("email"):
                emails[str(person["email"]).strip().lower()] = actor
        return names, emails

    def _match(self, c, row, people, index=None):
        """The company people among a status source's `people`, by display name (one person with that name), then by
        email when the source gives one. Only people who can read the call's thread are matched: anyone else is
        counted in the call and never named in it. `index` is a pass's _people_index, read once for all its calls."""
        if not people:
            return set()
        names, emails = index or self._people_index(c)
        found = set()
        for one in people:
            named = names.get(_spoken(one.get("name"))) or set() if _spoken(one.get("name")) else set()
            actor = next(iter(named)) if len(named) == 1 else emails.get(str(one.get("email") or "").lower())
            if actor and readable(c, actor, row["thread_id"]):
                found.add(actor)
        return found

    def _set_status(self, c, row, source, people, conference, index=None):
        """Record what a status source says is in the call: who (in_call, and a participant row for anyone first seen),
        how many, when someone was first and last seen, and when a read first showed nobody after that. `source` None
        clears the status. Writes `call.updated` only when something changed."""
        before, in_call_before = dict(row), self._in_call_set(c, row["id"])
        stamp = H.now()
        actors = self._match(c, row, people, index) if source else set()
        for actor in actors:
            c.execute(
                "INSERT INTO call_participants(call_id,actor,joined_at,in_call) VALUES(?,?,?,1) "
                "ON CONFLICT(call_id,actor) DO UPDATE SET in_call=1,left_at=NULL",
                (row["id"], actor, stamp),
            )
            c.execute("INSERT OR IGNORE INTO live_meeting_humans VALUES(?,?,?)", (row["live_meeting_id"], actor, stamp))
        c.execute(
            "UPDATE call_participants SET in_call=0 WHERE call_id=? AND in_call=1 AND actor NOT IN (%s)"
            % ",".join("?" * len(actors)),
            (row["id"], *sorted(actors)),
        )
        count = len(people) if source else 0
        # SET reads the row as it was, so meet_seen_at below is the one from before this read.
        c.execute(
            "UPDATE calls SET live_count=?,status_source=?,meet_seen_at=COALESCE(meet_seen_at,?),"
            "meet_active_at=COALESCE(?,meet_active_at),meet_polled_at=COALESCE(?,meet_polled_at),"
            "meet_empty_at=CASE WHEN ? OR ? IS NULL OR meet_seen_at IS NULL THEN NULL "
            "ELSE COALESCE(meet_empty_at,?) END,"
            "meet_conference=COALESCE(?,meet_conference),updated_at=? WHERE id=?",
            (
                count,
                source,
                stamp if count else None,
                stamp if count else None,
                stamp if source else None,
                count,
                source,
                stamp,
                conference,
                stamp,
                row["id"],
            ),
        )
        self._changed(c, row, before, in_call_before)

    def presence(self, c, who, call_id, names):
        """The recording source's Recorder says who it sees in the Meet window. Kept only while Google gives no status:
        Google's own answer wins."""
        row = self._readable_live(c, who, call_id)
        if row["recorder_actor"] != who.actor:
            raise Problem("forbidden", "Only the Recorder that records this call reports who is in it", 403)
        if row["status_source"] != "google":
            self._set_status(c, row, "recorder", [{"name": name} for name in names], None)
        return self.view(c, call_id, who)

    def _readable_live(self, c, who, call_id):
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        if not _google(row):
            raise Problem("not_google_meet", "This call is a Tico Meet call: its server records it", 409)
        if row["state"] != "live":
            raise Problem("call_ended", "This call has ended", 409)
        return row

    def _source_answer(self, c, who, call_id):
        row = _call(c, call_id)
        holder = row["recorder_actor"]
        shown = holder if holder and (holder == who.actor or self._sees(c, who, holder)) else None
        seq = c.execute("SELECT seq FROM live_meetings WHERE id=?", (row["live_meeting_id"],)).fetchone()
        return {
            "source": holder == who.actor,
            "meeting_id": row["live_meeting_id"],
            "title": row["title"],
            "source_actor": shown,
            # A Recorder numbers its chunks from 1, so it records only into a meeting nobody has written to yet.
            "has_transcript": bool(seq and seq[0]),
        }

    def claim_recorder(self, c, who, call_id):
        """A person who can read the thread asks for their Recorder to be the call's recording source. The first claim
        wins and its holder keeps it; anyone else is told who has it. Only someone in the call may (_in_call_or_thread).
        """
        row = self._readable_live(c, who, call_id)
        if not self._in_call_or_thread(c, row, who.actor):
            raise Problem("not_in_call", "Only someone in this call can record it", 403)
        if row["recorder_actor"] is None:
            before, in_call = dict(row), self._in_call_set(c, call_id)
            stamp = H.now()
            c.execute(
                "UPDATE calls SET recorder_actor=?,recorder_claimed_at=?,updated_at=? WHERE id=? "
                "AND recorder_actor IS NULL",
                (who.actor, stamp, stamp, call_id),
            )
            c.execute(
                "INSERT OR IGNORE INTO live_meeting_humans VALUES(?,?,?)", (row["live_meeting_id"], who.actor, stamp)
            )
            audit_log.write(c, who.actor, f"call:{call_id}", None, {"kind": "call.recorder_claimed"})
            self._changed(c, row, before, in_call)
        return self._source_answer(c, who, call_id)

    @staticmethod
    def _in_call_or_thread(c, row, actor):
        """Whether `actor` is in the call, for claiming its recording source: its connector, someone with a place in it
        that was not taken away (they opened it from Tico, or a status source saw them in it), or one of the thread's
        own people (its members; a DM's or a group chat's people). Reading the thread alone, as anyone can a company
        thread, is not being in the call."""
        if actor == row["connector_actor"]:
            return True
        if c.execute(
            "SELECT 1 FROM call_participants WHERE call_id=? AND actor=? AND removed_at IS NULL", (row["id"], actor)
        ).fetchone():
            return True
        root = threads.root_of(c, threads.thread_row(c, row["thread_id"]))
        return actor in threads._dm_members(c, root)

    def release_recorder(self, c, who, call_id):
        """The holder stops being the recording source; the Recorder's status goes with it."""
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        if row["recorder_actor"] and row["recorder_actor"] != who.actor:
            raise Problem(
                "forbidden", "Only the person whose Recorder records this call can stop it being the source", 403
            )
        if row["recorder_actor"] == who.actor:
            before, in_call = dict(row), self._in_call_set(c, call_id)
            c.execute(
                "UPDATE calls SET recorder_actor=NULL,recorder_claimed_at=NULL,updated_at=? WHERE id=?",
                (H.now(), call_id),
            )
            if row["status_source"] == "recorder" and row["state"] == "live":
                # An ended call keeps its source: it says something watched the call (a missed call, not an unknown one).
                c.execute("UPDATE calls SET status_source=NULL,live_count=0 WHERE id=?", (call_id,))
                c.execute("UPDATE call_participants SET in_call=0 WHERE call_id=?", (call_id,))
            audit_log.write(c, who.actor, f"call:{call_id}", None, {"kind": "call.recorder_released"})
            self._changed(c, row, before, in_call)
        return self._source_answer(c, who, call_id)

    def ringing(self, c, who):
        """The live calls ringing `who` now, in threads they can still read, newest first."""
        human_only(who)
        self.auth.domain(who)
        pattern = "%" + json.dumps(who.actor).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        found = []
        for r in c.execute(
            "SELECT id,thread_id FROM calls WHERE state='live' AND ring LIKE ? ESCAPE '\\' "
            "ORDER BY started_at DESC LIMIT ?",
            (pattern, RINGING_LIMIT * 5),
        ):
            if len(found) >= RINGING_LIMIT:
                break
            if not readable(c, who.actor, r["thread_id"]):
                continue
            shown = self.view(c, r["id"], who)
            if shown["ring"]:
                found.append(shown)
        return {"calls": found}

    def end_conference(self, call_id):
        """Ask Google to end a Meet call's active conference, as its connector, then lock its space so the link is spent.
        Best effort: the call ends either way."""
        with self.store.read() as c:
            row = _call(c, call_id)
            token = self._meet_token(c, row["connector_actor"]) if _google(row) else None
        if token and row["meet_space"]:
            google_meet.end_active_conference(token, row["meet_space"])
            self._lock(row, token)

    def lock_space(self, call_id):
        """Lock an ended Meet call's space (google_meet.lock_space), as its connector. Best effort: a failure is logged."""
        with self.store.read() as c:
            row = _call(c, call_id)
            token = self._meet_token(c, row["connector_actor"]) if _google(row) else None
        if token and row["meet_space"]:
            self._lock(row, token)
        elif _google(row):
            log.warning("A Meet call's space was not locked: no token for its connector")

    @staticmethod
    def _lock(row, token):
        try:
            locked = google_meet.lock_space(token, row["meet_space"])
        except Exception:  # noqa: BLE001 - locking is best effort: the call has ended either way
            locked = False
        if not locked:
            log.warning("A Meet call's space was not locked; its link still opens it (call %s)", row["id"])

    def lock_later(self, call_id):
        """Lock the space of a Meet the sweep ended, on the poll pool, so the heartbeat never waits on Google for it."""

        def lock():
            try:
                self.lock_space(call_id)
            except Exception as exc:  # noqa: BLE001 - reported; the call has ended either way
                from .batch_work import report

                report("calls_lock", call_id, exc, logger="tico.calls")

        self.pollers.submit(lock)

    def drop_pending(self, call_id):
        """Remove from the call's LiveKit room everyone who left or lost access. Each removal is kept until LiveKit confirms
        it, so a person who is still connected is not left in the room because one request failed."""
        with self.store.read() as c:
            row = _call(c, call_id)
            if _google(row):
                return  # nobody is in a LiveKit room for a Meet
            room = row["room_name"]
            pending = [
                r["actor"]
                for r in c.execute(
                    "SELECT actor FROM call_participants WHERE call_id=? AND media_removed_at IS NULL "
                    "AND (left_at IS NOT NULL OR removed_at IS NOT NULL)",
                    (call_id,),
                )
            ]
        for actor in pending:
            if not self._remove_from_room(call_id, room, actor):
                return

    def drop_from_media(self, call_id, actor):
        """Drop a person who just left from the room now. A failure leaves the removal pending for the sweep."""
        with self.store.read() as c:
            row = _call(c, call_id)
        if _google(row):
            return True  # leaving a Meet is Google's; Tico only records it
        try:
            return self._remove_from_room(call_id, row["room_name"], actor)
        except Exception as exc:  # noqa: BLE001 - a failed removal is reported and left pending for the sweep
            from .batch_work import report

            report("calls_leave", call_id, exc, logger="tico.calls")
            return False

    def _remove_from_room(self, call_id, room, actor):
        if self.media is None:
            return False
        try:
            self.media.remove(room, actor)
        except call_media.MediaUnavailable:
            return False
        with self.store.transaction() as c:
            c.execute(
                "UPDATE call_participants SET media_removed_at=? WHERE call_id=? AND actor=?", (H.now(), call_id, actor)
            )
        return True


def install_calls(app, store, auth, write):
    """The call routes and the media port. `write` is the app's idempotent write (as live meetings use). Tests set
    app.state.call_media to a fake before the first call; without the Tico Meet settings it is None and calls are off."""
    global AUTH
    AUTH = auth
    if project_reply not in live_meetings.REPLY_HOOKS:
        live_meetings.REPLY_HOOKS.append(project_reply)
    if not hasattr(app.state, "call_media"):
        config = call_media.config_from_env()
        app.state.call_media = call_media.LiveKitMedia(config) if config else None
    calls = app.state.calls = Calls(app, store, auth)

    @app.get("/api/v2/threads/{thread_id}/calls")
    def list_calls(request: Request, thread_id: str, limit: int = 25, before: str = ""):
        """A thread's calls, newest first, a page at a time. `next_before` is the cursor for the next page, or null.
        `providers` is what the viewer could start, in the order `auto` tries them; `available` is whether that is
        anything."""
        who = request.state.identity
        human_only(who)
        auth.domain(who)
        size = min(max(limit, 1), LIST_LIMIT)
        at, _, call_id = before.partition("|")
        with store.read() as c:
            threads.require(c, threads.viewer(auth, who), thread_id)
            offered = calls.providers(c, who)
            address = google_workspace.google_address(c, who.actor) or ""
            installed = _recorder_recent(c, who)
            found = [
                {**_summary(r), **calls.provider_fields(c, r, who, address=address, installed=installed)}
                for r in c.execute(
                    "SELECT * FROM calls WHERE thread_id=? AND (?='' OR started_at<? OR (started_at=? AND id<?)) "
                    "ORDER BY started_at DESC, id DESC LIMIT ?",
                    (thread_id, before, at, at, call_id, size + 1),
                )
            ]
        shown, used = [], 256
        for item in found[:size]:
            cost = len(json.dumps(item).encode()) + 1
            if shown and used + cost > CALL_LIST_BYTES:
                break  # the first call of a page always fits
            used += cost
            shown.append(item)
        more = len(found) > len(shown)
        return {
            "calls": shown,
            "next_before": f"{shown[-1]['started_at']}|{shown[-1]['id']}" if more else None,
            "available": bool(offered),
            "providers": offered,
        }

    @app.post("/api/v2/threads/{thread_id}/calls")
    def start_call(request: Request, thread_id: str, body: StartCall):
        """Start a call. A Google Meet start makes its space before the write, outside any transaction; a retry with the
        same idempotency key is answered from the first answer and makes no second space (the lock keeps two copies
        sent at once from both making one)."""
        who = request.state.identity
        key = request.headers.get("idempotency-key") or ""
        entry = calls.start_lock(who.actor, key)
        try:
            with entry[0]:
                replayed = calls.replayed(request, who)
                provider, space = "tico", None
                if not replayed:
                    with store.read() as c:
                        provider = calls.choose(c, who, thread_id, body.provider, body.speak_replies)
                    if provider == "google_meet":
                        try:
                            space = calls.make_space(who)
                        except Problem:
                            with store.read() as c:
                                fallback = body.provider == "auto" and "tico" in calls.providers(c, who)
                            if not fallback:
                                raise
                            provider = "tico"  # auto: Google could not make it, and Tico Meet can
                result = write(
                    request,
                    body,
                    lambda c: calls.start(
                        c, who, thread_id, body.title, body.speak_replies, provider=provider, space=space
                    ),
                )
        finally:
            calls.start_unlock(who.actor, key, entry)
        if result.get("provider", "tico") == "tico":
            calls.begin_media(result["id"])
        if not replayed:
            calls.ring_started(result["id"])
        return result

    @app.get("/api/v2/calls/ringing")
    def ringing_calls(request: Request):
        """The live calls ringing the caller now (page load and reconnect), in threads they can read."""
        who = request.state.identity
        with store.read() as c:
            return calls.ringing(c, who)

    @app.get("/api/v2/calls/{call_id}")
    def call_detail(request: Request, call_id: str):
        who = request.state.identity
        with store.read() as c:
            return calls.readable_view(c, who, call_id)

    @app.post("/api/v2/calls/{call_id}/join")
    def join_call(request: Request, call_id: str, body: Empty):
        who = request.state.identity
        result = write(
            request, body, lambda c: calls.join(c, who, call_id), check=lambda c: calls.check_join(c, who, call_id)
        )
        with store.read() as c:
            row = calls.check_join(c, who, call_id)  # a token is minted only for a person who may still join
        if _google(row):
            return {"call": result, "url": result.get("join_url")}  # Google runs the media: no token
        return {"call": result, **calls.token_payload(row, who.actor)}

    @app.post("/api/v2/calls/{call_id}/token")
    def refresh_token(request: Request, call_id: str):
        who = request.state.identity
        human_only(who)
        auth.domain(who)
        with store.read() as c:
            row = _call(c, call_id)
            participant = _participant(c, call_id, who.actor)
            if participant and participant["removed_at"]:
                raise Problem("call_access_removed", "You were removed from this call; join again to get a token", 403)
            if not readable(c, who.actor, row["thread_id"]):
                raise Problem("not_found", "Call not found", 404)
            if _google(row):
                raise Problem("not_tico_meet", "This call is a Google Meet: open its link, no token is needed", 409)
            if not participant or participant["left_at"]:
                raise Problem("not_joined", "Join the call to get a token", 409)
            if row["state"] != "live":
                raise Problem("call_ended", "This call has ended", 409)
            return calls.token_payload(row, who.actor)

    @app.post("/api/v2/calls/{call_id}/leave")
    def leave_call(request: Request, call_id: str, body: Empty):
        who = request.state.identity
        result = write(request, body, lambda c: calls.leave(c, who, call_id))
        calls.drop_from_media(call_id, who.actor)  # LiveKit drops the person now; a failure is retried by the sweep
        return result

    @app.post("/api/v2/calls/{call_id}/end")
    def end_call(request: Request, call_id: str, body: Empty):
        who = request.state.identity
        with store.read() as c:
            row = calls._connector(c, who, call_id)  # refuses before any speech is sent on the call's behalf
        if _google(row) and row["state"] == "live":
            calls.end_conference(call_id)  # best effort: Google removes everyone; the call ends either way
        calls.finish_speech(call_id)
        result = write(request, body, lambda c: calls.end(c, who, call_id))
        calls.after_end(call_id)
        return result

    @app.post("/api/v2/calls/{call_id}/action-items/retry")
    def retry_call_action_items(request: Request, call_id: str, body: Empty):
        who = request.state.identity
        return write(request, body, lambda c: calls.retry_action_items(c, who, call_id))

    @app.post("/api/v2/calls/{call_id}/bots")
    def attach_call_bots(request: Request, call_id: str, body: CallBots):
        who = request.state.identity
        return write(request, body, lambda c: calls.attach_bots(c, who, call_id, body.bots))

    @app.delete("/api/v2/calls/{call_id}/bots/{bot}")
    def detach_call_bot(request: Request, call_id: str, bot: str, body: Empty):
        who = request.state.identity
        return write(request, body, lambda c: calls.detach_bot(c, who, call_id, bot))

    @app.post("/api/v2/calls/{call_id}/presence")
    def call_presence(request: Request, call_id: str, body: Presence):
        """The recording source's Recorder reports the names in the Meet window; kept only while Google gives none."""
        who = request.state.identity
        return write(request, body, lambda c: calls.presence(c, who, call_id, body.names))

    @app.post("/api/v2/calls/{call_id}/recorder")
    def claim_call_recorder(request: Request, call_id: str, body: Empty):
        """Make the caller's Tico Recorder the call's recording source: the first claim wins and its holder keeps it."""
        who = request.state.identity
        return write(request, body, lambda c: calls.claim_recorder(c, who, call_id))

    @app.delete("/api/v2/calls/{call_id}/recorder")
    def release_call_recorder(request: Request, call_id: str, body: Empty):
        """The holder's Recorder stops being the call's recording source."""
        who = request.state.identity
        return write(request, body, lambda c: calls.release_recorder(c, who, call_id))
