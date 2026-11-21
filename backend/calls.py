"""Calls in threads (docs/meet.md): a Google Meet, or a Tico Meet audio and screen-share call, that belongs to a thread.

A call is a live meeting (backend/live_meetings.py) that its thread governs, whichever provider runs it. A Google Meet
call (`provider` google_meet) is a Meet space Tico makes as the caller (backend/google_meet.py, the Workspace key's Meet
scope); Google runs the media and the sweep polls Google for who is in it. Everything below about LiveKit, tokens and
the server's recorder is Tico Meet's (`provider` tico) only.

For a Tico Meet call: The thread's audience decides who may join
and hear it, the bots a person with Write attaches reply in the call and in the thread, and the transcript goes into
the thread when the call ends. The media runs on LiveKit (backend/call_media.py): LiveKit keeps only the room, while
Tico keeps the call's state and its transcript. A person's token grants audio and screen share, is good for five
minutes, and is refreshed only while they are still in the call and may still read the thread.

Speech reaches the transcript through the Gateway's speech-to-text, and no raw audio is kept. Each person's speech is
cut where they pause (call_voice.Segmenter), and each utterance takes its place in the transcript in the order it was
heard, whichever transcription finishes first. In a voice call (`speak_replies`, the default for Tico Meet) the bots
answer aloud as well as in the thread (backend/call_voice.py). A call survives a Tico restart, because its state is in
the database and the sweep picks it up; if LiveKit restarts and loses the room, the call ends with its transcript kept.
Who is in the room is read from LiveKit by the sweep (Tico voice left out): a room empty for EMPTY_SECONDS ends the
call.

One owner: the process that holds a call's recorder holds its media, its speech buffers and its voice. A process that
does not hold a call's recorder never plays into it (call_voice.Voice.reply).

The thread checks and posts are core's (backend/threads.py, docs/dev/threads-contract.md). A bot's rights on a teammate
are teammate_access's, as they are everywhere else.
"""

import bisect
import json
import logging
import re
import threading
import time
import urllib.parse
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from threading import Timer
from typing import Literal

from fastapi import Query, Request
from pydantic import Field

from . import (
    audit_log,
    calendar_api,
    call_items,
    call_prep,
    call_media,
    call_ring,
    call_sessions,
    call_voice,
    gateway_connection,
    google_meet,
    google_workspace,
    live_meetings,
    teammate_access,
    threads,
)
from .models import Contract
from .store import H, Problem
from .views import human_only
from .api_contract import api_v2

log = logging.getLogger("tico.calls")

ROOM_PREFIX = "tico-call-"
TRANSCRIPT_BYTES = 12_000
LIST_LIMIT = 50
VOICE_EFFORT = "medium"  # every spoken call turn runs at this effort, whatever the bot's own (live_meetings)
PARTICIPANT_LIMIT = 200
PCM_BYTES_PER_SECOND = call_media.SAMPLE_RATE * 2
WORKERS = 4  # clips transcribed at once: the Gateway waits on them, the recorder's audio must not
BACKLOG = 4  # a call's clips waiting for their text at most: past it the oldest is dropped, and a repeat asked for
CLIP_ORDER_WAIT_SECONDS = 6  # ready text can move past a transcription still out this long
DRAIN_SECONDS = call_media.TRANSCRIBE_TIMEOUT + 15  # the longest a call's end waits for its last clips
END_WAIT_SECONDS = 2  # End answers with the call ended when its last clips are in by then, else with it ending
BOT_LIMIT = live_meetings.CALL_BOT_LIMIT  # bots in one call
REPEAT_NOTE = call_voice.REPEAT_NOTE
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
RINGING_LIMIT = 20  # the calls `GET /api/v2/calls/ringing` answers with at most
LIVE_LIMIT = 20  # the calls `GET /api/v2/calls/live` answers with at most
CHANGE_FIELDS = ("live_count", "status_source", "meet_seen_at")  # with in_call: call.updated
# Scheduled calls (docs/meet.md, Scheduled calls). A start a little behind now is still taken: the dialog's "now" was
# a moment ago. The invitation names at most INVITE_LIMIT people.
SCHEDULE_LONGEST = timedelta(hours=8)
SCHEDULE_PAST = timedelta(minutes=5)
INVITE_LIMIT = 50
UPCOMING_LIMIT = 50
UPCOMING_FACES = 8
# A scheduled call's id comes from its booking's idempotency key, so a retry names the same call and the same event.
SCHEDULE_IDS = uuid.UUID("5d1f6b0e-2c47-4f0a-9a36-7c2b8e1d4f53")

# The auth module, set by install_calls: an actor becomes the identity that the thread rules read.
AUTH = None
CALLS = None  # the app's Calls, set by install_calls: the reply hook hands spoken replies to it


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


def room_owner(c, thread_id):
    """The person whose own special personal room (their Assistant chat) this thread is, or a sub-thread of: its owner,
    whoever else it may let read. None for any other thread."""
    root = threads.root_of(c, thread_id)
    if not root or threads.audience_of(root) != "personal" or not root.get("room_key"):
        return None
    return threads.personal_owner(root)


def assistant_room(c, thread_id):
    """Whether the thread is a person's own Assistant chat or a sub-thread of it: a call there is theirs alone."""
    from . import rooms

    return bool(rooms.assistant_room_owner(threads.root_of(c, thread_id)))


def alone_with_assistant(c, thread_id, slugs):
    """A call in a person's own Assistant chat takes no agent but the Assistant, by either attach route and for its
    whole life, before or after the Assistant leaves it: its voice session reads as that person (rooms.call_person)."""
    assistant = H.actor_id(H.assistant_actor(c))
    if any(slug != assistant for slug in slugs) and assistant_room(c, thread_id):
        raise Problem("assistant_alone", "A call in an Assistant chat takes no agent but the Assistant", 409)


def cancel_bot_turns(c, meeting_id, slug, reason):
    """A bot that leaves a call answers none of its open turns in it."""
    for turn in c.execute(
        "SELECT * FROM live_meeting_turns WHERE meeting_id=? AND bot=? AND status IN ('pending','claimed')",
        (meeting_id, slug),
    ).fetchall():
        live_meetings._finish_pass(c, meeting_id, turn, reason)


def voice_call(c, rid):
    """The call whose meeting is `rid`, when it is a voice call (a Tico Meet call with speak_replies), else None."""
    row = c.execute("SELECT * FROM calls WHERE live_meeting_id=?", (rid,)).fetchone()
    return row if row and row["provider"] == "tico" and row["speak_replies"] else None


def spoken_prompt(c, rid):
    """A bot's turn in a voice call is told that its answer is read aloud (live_meetings.PROMPT_HOOKS)."""
    call = voice_call(c, rid)
    return call_voice.SPOKEN_PROMPT.format(call_id=call["id"]) if call else ""


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
    machine = CALLS
    if machine is not None and voice_call(c, rid):
        # Never spoken inside this transaction: once the reply has committed, it is handed to the call's voice, which
        # makes the speech and plays it outside any transaction.
        live_meetings._event(c, rid, "meeting.voice", {"reply_id": reply["id"], "bot": bot, "state": "requested"})
        call_id, reply_id, text, turn_id = row["id"], reply["id"], reply["text"], reply["turn_id"]
        c.after_commit.append(lambda: machine.voice.reply(call_id, bot, reply_id, text, turn_id))


def route_question(c, rid, question):
    """A voice call's transcript lines and chat messages ask its bots through its voice, one open question at a time
    (live_meetings.QUESTION_ROUTERS). Returns whether the meeting is a voice call's."""
    row = voice_call(c, rid)
    machine = CALLS
    if row is None or machine is None:
        return False
    heard = question["heard"] if question.get("heard") is not None else machine.voice.clock()
    machine.voice.route(c, row, {**question, "heard": heard})
    return True


def bot_left(c, rid, bot):
    """A bot taken out of a call's meeting by any route stops speaking in it once that commits."""
    row = c.execute("SELECT id FROM calls WHERE live_meeting_id=?", (rid,)).fetchone()
    machine = CALLS
    if row is not None and machine is not None:
        call_id = row["id"]
        c.after_commit.append(lambda: machine.voice.cancel(call_id, bot))


class Empty(Contract):
    pass


class StartCall(Contract):
    title: str = Field(default="Call", min_length=1, max_length=200)
    # Bots answer aloud as well as in the thread. Unset: on for Tico Meet, off for Google Meet. On (or any bots) makes
    # the call a Tico Meet one: only there can a bot speak.
    speak_replies: bool | None = None
    provider: Literal["auto", "google_meet", "tico"] = "auto"
    # Retained in the request hash for callers retrying a start across the change to per-bot voice settings.
    model: str | None = Field(default=None, max_length=120, description="Deprecated. A bot keeps its own model.")
    effort: str | None = Field(default=None, max_length=20, description="Deprecated. Voice replies use low effort.")
    bots: list[str] = Field(default_factory=list, max_length=BOT_LIMIT)  # bots in the call from its start
    # Both given: the call is booked for then (docs/meet.md, Scheduled calls), not started. ISO 8601 with an offset.
    starts_at: str | None = Field(default=None, max_length=40)
    ends_at: str | None = Field(default=None, max_length=40)

    def model_dump(self, **kwargs):
        # The idempotency hash reads this: a call started now hashes as it did before calls could be booked, so a
        # retry sent across that upgrade is still answered from its first answer.
        dumped = super().model_dump(**kwargs)
        if dumped.get("starts_at") is None and dumped.get("ends_at") is None:
            dumped.pop("starts_at", None)
            dumped.pop("ends_at", None)
        for key in ("model", "effort"):
            if dumped.get(key) is None:
                dumped.pop(key, None)
        return dumped


class ChangeCall(Contract):
    # A scheduled call's new times (docs/meet.md, Scheduled calls). ISO 8601 with an offset.
    starts_at: str = Field(min_length=1, max_length=40)
    ends_at: str = Field(min_length=1, max_length=40)


def _start_hashed_before_voice(body):
    """A start as servers before voice calls stored its idempotency hash (speak_replies false by default, no bots), so
    a retry of a start sent to one of them is answered from its first answer rather than refused as different. None for
    a start those servers could not have been sent."""
    if body.bots or body.starts_at or body.ends_at or body.model is not None or body.effort is not None:
        return None
    return {"title": body.title, "speak_replies": bool(body.speak_replies), "provider": body.provider}


class CallBots(Contract):
    bots: list[str] = Field(min_length=1, max_length=BOT_LIMIT)


class CallStage(Contract):
    """What to show on a Tico Meet call's stage: your screen, or a Tico file you may read (docs/meet.md, Sharing)."""

    kind: Literal["screen", "file"]
    file_id: str | None = Field(default=None, max_length=200)


def _stage_stored(row):
    """`calls.stage` as stored (JSON): what shows and `n`, the count of stage writes; {} for a call never shared on."""
    try:
        found = json.loads(row.get("stage") or "{}")
    except (TypeError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _stage_record(row):
    """What shows on the call's stage, or None for nothing."""
    found = _stage_stored(row)
    if found.get("kind") not in ("screen", "file") or not found.get("by"):
        return None
    return found


def _stage_n(row):
    try:
        return int(_stage_stored(row).get("n") or 0)
    except (TypeError, ValueError):
        return 0


def _present(participant):
    """Whether a participant row is someone in the call now: joined, not left, not removed."""
    return bool(participant) and participant["left_at"] is None and participant["removed_at"] is None


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
        "state": _state(row),
        "connector": row["connector_actor"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "end_reason": row["end_reason"],
        "scheduled_start": row["scheduled_start"],
        "scheduled_end": row["scheduled_end"],
    }


def _instant(value, field):
    try:
        at = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        raise Problem("validation", f"{field} is not an ISO 8601 time", 422) from None
    if at.tzinfo is None:
        raise Problem("validation", f"{field} needs its offset from UTC", 422)
    return at.astimezone(timezone.utc)


def booking(starts_at, ends_at):
    """A scheduled call's (start, end) as stored, on the whole minute, or None for a call that starts now."""
    if starts_at is None and ends_at is None:
        return None
    if not starts_at or not ends_at:
        raise Problem("validation", "A scheduled call needs both starts_at and ends_at", 422)
    start = _instant(starts_at, "starts_at").replace(second=0, microsecond=0)
    end = _instant(ends_at, "ends_at").replace(second=0, microsecond=0)
    if end <= start:
        raise Problem("validation", "A call ends after it starts", 422)
    if end - start > SCHEDULE_LONGEST:
        raise Problem("validation", "A scheduled call lasts at most 8 hours", 422)
    if start < datetime.now(timezone.utc) - SCHEDULE_PAST:
        raise Problem("validation", "A call is scheduled for a time still to come", 422)
    return _stamp(start), _stamp(end)


def _calendar_time(stamp):
    """A stored time as the calendar takes it: whole seconds, in UTC."""
    return _instant(stamp, "time").strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _require_open(row, scheduled=False):
    """Refuse a call that has ended, or whose End was pressed (nothing more is heard in it): nobody joins it then. A
    join takes a scheduled call too (`scheduled`): the first one starts it."""
    if scheduled and row["state"] == "scheduled":
        return
    if row["state"] != "live":
        raise Problem("call_ended", "This call has ended", 409)
    if row["ending_at"]:
        raise Problem("call_ended", "This call is ending", 409)


def _state(row):
    """A call's state as readers see it: `ending` from End until its last speech is in (Tico Meet), then `ended`."""
    return "ending" if row["state"] == "live" and row["ending_at"] else row["state"]


def _join_url(row, address):
    """The Meet link as one viewer opens it: `authuser` makes Meet open on their own Google account."""
    if not _google(row) or not row["meet_uri"]:
        return None
    return row["meet_uri"] + ("?" + urllib.parse.urlencode({"authuser": address}) if address else "")


def _spoken(name):
    """A display name as matching compares it: case and runs of spaces do not count."""
    return re.sub(r"\s+", " ", str(name or "")).strip().casefold()


def bot_ids(c, refs):
    """The bots a request names, each by its id or its slug (H.resolve_bot), as their ids: each once, in order. A call
    stores and checks bots by id; a name that is no bot is 404."""
    ids: list[str] = []
    for ref in refs or ():
        bot = H.resolve_bot(c, ref)
        if not bot:
            raise Problem("not_found", f"Org-chart bot {ref} was not found", 404)
        if bot["id"] not in ids:
            ids.append(bot["id"])
    return ids


class Calls:
    """The call operations. Writes run inside the caller's transaction (`c`) and record their audit row and threads change
    together with the change; the calls that reach LiveKit or the Gateway run outside any transaction."""

    def __init__(self, app, store, auth):
        self.app, self.store, self.auth = app, store, auth
        self.buffers = {}  # (call_id, identity) -> call_voice.Segmenter: one speaker's speech, cut at their pauses
        self.voiced = {}  # call_id -> the voice clock's time of anyone's last voiced frame in it (`last_voiced`)
        # (call_id, identity) -> {"snapshot": the segmenter's early (start_ms, pcm), "future": its transcription}: started
        # as the speaker's pause begins, and used by the clip the pause ends if that clip is the same audio.
        self.early = {}
        self.lock = threading.RLock()
        self.pending = {}  # call_id -> the work still out for it, dropped as each is done
        # call_id -> {"n": clips cut so far, "queue": the clips not yet stored, by where they start}: the transcript keeps
        # the order speech was said in, across people, whichever clip is transcribed first.
        self.order = {}
        self.closing = set()  # calls whose End stopped their listening; their speech already heard is still stored
        self.ending = {}  # call_id -> the thread finishing an ended call once its last clips are in
        self.workers = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="tico-meet-stt")
        self.clip_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tico-meet-clip")
        self.voice = call_voice.Voice(app, store, self)
        self.starting = {}  # (actor, idempotency key) -> the lock a Google Meet start holds while it makes its space
        self.pollers = ThreadPoolExecutor(max_workers=POLL_WORKERS, thread_name_prefix="tico-meet-poll")
        self.polling = {}  # call_id -> a status read a past pass left still out
        self.backoff = {}  # call_id -> [failures in a row, passes still to skip]
        self.poll_pass = threading.Lock()  # one polling pass at a time
        self.went_live = set()  # scheduled calls a committed join just started, until that join starts their media

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
        voiced = row["provider"] == "tico" and bool(row["speak_replies"]) and row["state"] == "live"
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
            "state": _state(row),
            "connector": row["connector_actor"],
            "meeting_id": row["live_meeting_id"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "end_reason": row["end_reason"],
            "participants": people,
            "bots": bots,
            "speak_replies": bool(row["speak_replies"]),
            "model": None,
            "effort": VOICE_EFFORT if voiced else None,
            "voice": self.voice.view(call_id, bots, voiced),
            "holds": holds,
            "stage": self._stage_view(c, row, who),
            "stage_n": _stage_n(row),
            "scheduled_start": row["scheduled_start"],
            "scheduled_end": row["scheduled_end"],
            **self.provider_fields(c, row, who),
        }

    def _stage_view(self, c, row, who):
        """The stage as `who` sees it, checked on every read: a file they may not read (now) is only `no_access`, with
        nothing that names it. Nothing shows once the call is no longer live or its presenter has left."""
        stage = _stage_record(row)
        if not stage or row["state"] != "live" or row["ending_at"]:
            return None
        if not _present(_participant(c, row["id"], stage["by"])):
            return None
        shown = {"kind": stage["kind"], "by": stage["by"], "at": stage.get("at")}
        if stage["kind"] == "screen":
            return shown
        file = self._stage_file(c, who, stage.get("file_id"))
        if file is None:
            return {**shown, "no_access": True}
        return {**shown, "file_id": file["id"], "version": int(stage.get("version") or 0), "title": file["title"]}

    def _stage_file(self, c, who, file_id):
        """The file's row when `who` may read it now, else None."""
        files = getattr(self.app.state, "files", None)
        if files is None or not file_id:
            return None
        try:
            row = files.visible_file(c, who, file_id)
        except Problem:
            return None
        return None if row["state"] == "deleted" else row

    @staticmethod
    def _in_call(row, participant):
        """Whether the status source has the person in the call now. Tico Meet knows from its own room: a join puts the
        person in, a leave takes them out, and the sweep sets both from who LiveKit has in the room."""
        return bool(participant["in_call"]) and row["state"] == "live"

    def provider_fields(self, c, row, who, *, address=None):
        """What a call view adds for its provider and for this viewer (docs/dev/meet-calls-contract.md): the Meet link
        as they open it, the status source's count, whether the call rings them and its messages. A list passes the
        viewer's `address` once for all its rows."""
        google = _google(row)
        if google and address is None:
            address = google_workspace.google_address(c, who.actor) or ""
        if google:
            live_count = int(row["live_count"] or 0) if row["state"] == "live" else 0
        else:
            live_count = c.execute(
                "SELECT COUNT(*) FROM call_participants WHERE call_id=? AND in_call=1", (row["id"],)
            ).fetchone()[0]
        joined = c.execute(
            "SELECT 1 FROM call_participants WHERE call_id=? AND actor=?", (row["id"], who.actor)
        ).fetchone()
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
        source or when it was first seen. No audit row: these are what Google reported, not anyone's act."""
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

    def _may_start(self, c, who, thread_id, scheduled=False):
        """The checks a start makes before anything is made, whatever the provider, and again inside its write. A call
        scheduled for later may be booked while another is going."""
        human_only(who)
        self.auth.domain(who)
        threads.require(c, threads.viewer(self.auth, who), thread_id, write=True)
        if not scheduled:
            self._none_live(c, thread_id)

    @staticmethod
    def _none_live(c, thread_id):
        live = c.execute("SELECT id FROM calls WHERE thread_id=? AND state='live'", (thread_id,)).fetchone()
        if live:
            raise Problem(
                "call_in_progress", "This thread already has a call going", 409, extra={"call_id": live["id"]}
            )

    def providers(self, c, who):
        """The call providers `who` could start now, in the order `auto` tries them: Tico Meet first, for every call."""
        offered = []
        if self.media is not None:
            offered.append("tico")
        if google_workspace.meet_ready(c) and google_workspace.workspace_mailbox(c, who.actor):
            offered.append("google_meet")
        return offered

    def choose(self, c, who, thread_id, wanted, speak_replies=None, bots=()):
        """The provider a start uses: `auto` is the first the caller has (`providers`); a named one must be there. A
        call with bots or spoken replies is Tico Meet's, whatever the default: only there can a bot speak."""
        self._may_start(c, who, thread_id)
        if speak_replies or bots:
            if wanted == "google_meet":
                raise Problem(
                    "voice_needs_tico_meet", "Bots join and speak in Tico Meet calls only: start it as Tico Meet", 422
                )
            wanted = "tico"
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

    @staticmethod
    def _people_of(c, thread_id):
        """The people of a call's thread (its root's), bots left out."""
        root = threads.root_of(c, threads.thread_row(c, thread_id))
        return list(dict.fromkeys(a for a in threads._dm_members(c, root) if str(a).startswith("human:")))

    def _ring_for(self, c, who, thread_id):
        """Who a call rings (backend/call_ring.py): the thread's people other than the caller who can read it, when it
        has at most RING_MAX people; nobody in a bigger thread. Bots are never rung."""
        if assistant_room(c, thread_id):
            return []  # an Assistant chat's call is its person's alone: it rings nobody
        people = self._people_of(c, thread_id)
        if len(people) > call_ring.RING_MAX:
            return []
        return [actor for actor in people if actor != who.actor and readable(c, actor, thread_id)]

    def start(
        self,
        c,
        who,
        thread_id,
        title,
        speak_replies=None,
        provider="tico",
        space=None,
        bots=(),
        *,
        scheduled=None,
    ):
        """Store a call, its live meeting, its card in the thread, who it rings and the bots it starts with. A Google
        Meet start brings the space `make_space` made before this write; a Tico Meet one needs LiveKit set up.

        `scheduled` books a Tico Meet call for later instead (`schedule_plan`): the call is `scheduled` and its meeting
        paused, nobody is in it and nobody is rung until the first join starts it, and its calendar event is recorded
        here, in the same write."""
        self._may_start(c, who, thread_id, scheduled=scheduled is not None)
        if speak_replies is None:
            speak_replies = provider == "tico"
        if (speak_replies or bots) and provider != "tico":
            raise Problem("voice_needs_tico_meet", "Bots join and speak in Tico Meet calls only", 422)
        if provider == "tico" and self.media is None:
            raise Problem("meet_unavailable", "Tico Meet is not set up on this server", 503)
        if provider == "google_meet" and not space:
            raise Problem("meet_unavailable", "Google Meet did not make the meeting. Try again in a minute.", 503)
        meeting = live_meetings.Connect(title=title, visibility="private")
        meeting_id, stamp = str(uuid.uuid4()), H.now()
        call_id = scheduled["call_id"] if scheduled else str(uuid.uuid4())
        state = "scheduled" if scheduled else "live"
        c.execute(
            "INSERT INTO live_meetings(id,owner_actor,title,state,client_id,window_ms,cooldown_ms,reply_cap,"
            "threshold,created_at,updated_at,started_at,visibility,thread_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                meeting_id,
                who.actor,
                meeting.title,
                "paused" if scheduled else "live",
                None,
                live_meetings._window_ms(self.app, meeting),
                meeting.cooldown_seconds * 1000,
                meeting.reply_cap,
                meeting.threshold,
                stamp,
                stamp,
                stamp,
                "private",
                thread_id,
            ),
        )
        c.execute("INSERT INTO live_meeting_humans VALUES(?,?,?)", (meeting_id, who.actor, stamp))
        live_meetings._event(
            c,
            meeting_id,
            "meeting.state",
            {"state": "paused" if scheduled else "live", "connected_by": who.actor, "visibility": "private"},
        )
        space = space or {}
        c.execute(
            "INSERT INTO calls(id,thread_id,live_meeting_id,connector_actor,title,state,room_name,started_at,"
            "created_at,updated_at,speak_replies,provider,meet_space,meet_uri,meet_code,ring,scheduled_start,"
            "scheduled_end) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                call_id,
                thread_id,
                meeting_id,
                who.actor,
                meeting.title,
                state,
                ROOM_PREFIX + call_id,
                stamp,
                stamp,
                stamp,
                int(bool(speak_replies)),
                provider,
                space.get("name"),
                space.get("meeting_uri"),
                space.get("meeting_code"),
                json.dumps([] if scheduled else self._ring_for(c, who, thread_id)),
                scheduled["start"] if scheduled else None,
                scheduled["end"] if scheduled else None,
            ),
        )
        if scheduled:
            calendar_api.record_call_event(c, who, scheduled["event"], call_id, scheduled.get("occurrence"))
            self._record(
                c,
                who.actor,
                call_id,
                thread_id,
                "call.scheduled",
                {"meeting_id": meeting_id, "starts_at": scheduled["start"], "ends_at": scheduled["end"]},
            )
        else:
            c.execute(
                "INSERT INTO call_participants(call_id,actor,joined_at,in_call) VALUES(?,?,?,?)",
                (call_id, who.actor, stamp, int(provider == "tico")),
            )
            self._record(
                c, who.actor, call_id, thread_id, "call.started", {"meeting_id": meeting_id, "provider": provider}
            )
        if bots:
            self._attach(c, who, _call(c, call_id), live_meetings._meeting(c, meeting_id), bots)
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
        if bots and not scheduled:
            self.app.state.call_prep.live(c, _call(c, call_id))
        return self.view(c, call_id, who)

    @staticmethod
    def schedule_id(who, thread_id, key):
        """A booking's call id: from its idempotency key when it has one (a retry books the same call), else new."""
        if not key:
            return str(uuid.uuid4())
        return str(uuid.uuid5(SCHEDULE_IDS, f"{who.actor}\n{thread_id}\n{key}"))

    def _invited(self, c, booker, thread_id):
        """Who a scheduled call's event invites: the thread's people, other than its booker, who can read it now; nobody
        for a call in a person's Assistant chat, which is theirs alone, booked or moved."""
        if assistant_room(c, thread_id):
            return []
        people = [a for a in self._people_of(c, thread_id) if a != booker and readable(c, a, thread_id)]
        return people[:INVITE_LIMIT]

    def schedule_plan(self, c, who, thread_id, title, when, bots, key):
        """What a scheduled start brings into its write, made before it (`start`'s `scheduled`): the call's id, its
        times, its bots as ids (named once here, so a slug renamed while Google is asked names no other bot), and where
        its one calendar event goes. Everything the write would refuse is checked first, so a calendar is never written
        for a call that cannot be stored. The event invites the thread's people who can read it; its location and notes
        hold the call's link."""
        self._may_start(c, who, thread_id, scheduled=True)
        if self.media is None:
            raise Problem("meet_unavailable", "Tico Meet is not set up on this server", 503)
        bots = bot_ids(c, bots)
        self._may_attach(c, who, bots)
        alone_with_assistant(c, thread_id, bots)  # before the calendar is written
        call_id = self.schedule_id(who, thread_id, key)
        if c.execute("SELECT 1 FROM calls WHERE id=?", (call_id,)).fetchone():
            raise Problem("receipt_expired", "This booking's receipt expired; book it again", 409)
        link = f"{self.store.settings.public_url}/#/call/{urllib.parse.quote(call_id)}"
        event = calendar_api.plan_call_event(
            c,
            who,
            title=title,
            start=_calendar_time(when[0]),
            end=_calendar_time(when[1]),
            invited=self._invited(c, who.actor, thread_id),
            link=link,
        )
        return {"call_id": call_id, "start": when[0], "end": when[1], "event": event, "bots": bots}

    def schedule_event(self, plan):
        """Write a scheduled call's Google event before its write (Google is asked outside any transaction). A Tico
        calendar's event is made in the write itself."""
        if plan["event"]["home"] != "google":
            return plan
        with self.store.read() as c:
            plan["occurrence"] = calendar_api.insert_call_event(c, plan["event"], plan["call_id"])
        return plan

    def unschedule_event(self, plan):
        """Take back the Google event of a scheduled start whose write failed, so no invitation names a call that
        does not exist."""
        if plan.get("occurrence") is None:
            return
        with self.store.read() as c:
            calendar_api.drop_call_event(c, plan["event"], plan["call_id"])

    def _may_change(self, c, who, call_id):
        """A scheduled call may be moved or cancelled by the person who booked it, or by anyone who manages its
        thread, until it starts."""
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        if who.actor != row["connector_actor"] and not threads.can_manage(
            c, threads.viewer(self.auth, who), row["thread_id"]
        ):
            raise Problem("forbidden", "Only the person who booked this call, or who manages its thread, can", 403)
        if row["state"] != "scheduled":
            raise Problem("call_not_scheduled", "This call has started or ended; its time no longer changes", 409)
        return row

    def may_end(self, c, who, call_id):
        """End's check: a scheduled call is cancelled by whoever may change it, a started one ended by its starter."""
        row = _call(c, call_id)
        if row["state"] == "scheduled":
            return self._may_change(c, who, call_id)
        return self._connector(c, who, call_id)

    def change_plan(self, c, who, call_id, when):
        """A scheduled call's new times, and where its event's change goes (`calendar_api.plan_call_change`), made
        before the write: the calendar is written first. The event's people become who can read the thread now."""
        row = self._may_change(c, who, call_id)
        event = calendar_api.plan_call_change(
            c,
            call_id,
            row["connector_actor"],
            start=_calendar_time(when[0]),
            end=_calendar_time(when[1]),
            invited=self._invited(c, row["connector_actor"], row["thread_id"]),
        )
        return {"start": when[0], "end": when[1], "event": event}

    def reschedule(self, c, who, call_id, plan, occ):
        """Store a scheduled call's new times, once its calendar took them (`occ`: Google's answer; a Tico event
        changes here, in this write)."""
        row = self._may_change(c, who, call_id)
        if plan is None:
            raise Problem("receipt_expired", "This change's receipt expired; send it again", 409)
        calendar_api.record_call_change(c, who, plan["event"], occ)
        if (plan["start"], plan["end"]) != (row["scheduled_start"], row["scheduled_end"]):
            self.move(c, row, plan["start"], plan["end"], by=who.actor)
        return self.view(c, call_id, who)

    def move(self, c, row, start, end, now=None, by=None):
        """A scheduled call's new times, from its calendar or a change made in Tico, in the caller's write (`row` is
        updated in place). A start more than call_prep.PREP_LEAD away clears `prepped_at`: its bots prep again for
        the new time."""
        now = now or H.now()
        far = start > H.shift(now, seconds=call_prep.PREP_LEAD.total_seconds())
        prepped = None if far else row["prepped_at"]
        c.execute(
            "UPDATE calls SET scheduled_start=?,scheduled_end=?,prepped_at=?,updated_at=? WHERE id=?",
            (start, end, prepped, now, row["id"]),
        )
        self._record(
            c,
            by or row["connector_actor"],
            row["id"],
            row["thread_id"],
            "call.moved",
            {"starts_at": start, "ends_at": end},
        )
        row.update(scheduled_start=start, scheduled_end=end, prepped_at=prepped)

    @staticmethod
    def readable(c, actor, thread_id):
        return readable(c, actor, thread_id)

    attachment_standing = staticmethod(attachment_standing)

    @staticmethod
    def thread_private(c, thread_id):
        """Whether a call's thread is private: anything but a company thread. Work filed from it is private too."""
        return threads.audience_of(threads.root_of(c, threads.thread_row(c, thread_id))) != "company"

    def upcoming(self, c, who):
        """The scheduled calls `who` is in, soonest first: in threads they can read, and booked by them or with them
        among the thread's people. The SQL keeps only calls whose thread names them before the limit; each is then
        checked exactly. A row is compact: the call, its times, its thread, up to UPCOMING_FACES of its people, and
        `can_edit` (`_may_change`'s rule in the same SQL: the booker, an admin, the thread's owner or an owner member;
        a teammate's manage grant on someone's home thread is not read here, so such a call shows no ⋯ though PATCH
        would take it)."""
        human_only(who)
        self.auth.domain(who)
        found = []
        rows = c.execute(
            "SELECT calls.id,calls.title,calls.thread_id,calls.connector_actor,calls.scheduled_start,"
            "calls.scheduled_end,(calls.connector_actor=:me OR :admin OR r.owner_id=:me OR EXISTS (SELECT 1 FROM "
            "thread_members o WHERE o.thread_id=r.id AND o.actor=:me AND o.role='owner')) AS can_edit "
            "FROM calls JOIN conversations t ON t.id=calls.thread_id "
            "JOIN conversations r ON r.id=COALESCE(t.root_id,t.id) WHERE calls.state='scheduled' AND "
            "(calls.connector_actor=:me OR r.owner_id=:me OR EXISTS (SELECT 1 FROM thread_members m "
            "WHERE m.thread_id=r.id AND m.actor=:me) OR EXISTS (SELECT 1 FROM json_each(CASE WHEN "
            "json_valid(r.participants) THEN r.participants END) p WHERE p.value=:me)) "
            "ORDER BY calls.scheduled_start, calls.id LIMIT :n",
            {"me": who.actor, "admin": bool(threads.viewer(self.auth, who).admin(c)), "n": UPCOMING_LIMIT * 2},
        ).fetchall()
        for row in rows:
            if len(found) >= UPCOMING_LIMIT:
                break
            if not readable(c, who.actor, row["thread_id"]):
                continue
            people = list(dict.fromkeys([row["connector_actor"], *self._people_of(c, row["thread_id"])]))
            if who.actor not in people:
                continue
            faces = [a for a in people if a == who.actor or self._sees(c, who, a)][:UPCOMING_FACES]
            found.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "thread_id": row["thread_id"],
                    "scheduled_start": row["scheduled_start"],
                    "scheduled_end": row["scheduled_end"],
                    "people": faces,
                    "can_edit": bool(row["can_edit"]),
                }
            )
        return {"calls": found}

    def next_scheduled(self, c, who, thread_id):
        """The thread's soonest scheduled call, for its header (one indexed read), or None. `can_edit`: whether the
        viewer may change its time or cancel it (`_may_change`)."""
        row = c.execute(
            "SELECT * FROM calls WHERE thread_id=? AND state='scheduled' ORDER BY scheduled_start, id LIMIT 1",
            (thread_id,),
        ).fetchone()
        if row is None:
            return None
        edit = who.actor == row["connector_actor"] or threads.can_manage(c, threads.viewer(self.auth, who), thread_id)
        return {**_summary(row), **self.provider_fields(c, row, who), "can_edit": bool(edit)}

    def _go_live(self, c, who, row):
        """The first join of a scheduled call starts it: from here it is a call like any other, with the bots it was
        booked with. It rings the thread's other people as a start does. True when this join started it."""
        self._none_live(c, row["thread_id"])
        if self.media is None:
            raise Problem("meet_unavailable", "Tico Meet is not set up on this server", 503)
        stamp = H.now()
        started = c.execute(
            "UPDATE calls SET state='live',started_at=?,updated_at=?,ring=? WHERE id=? AND state='scheduled'",
            (stamp, stamp, json.dumps(self._ring_for(c, who, row["thread_id"])), row["id"]),
        ).rowcount
        if not started:
            return False
        c.execute(
            "UPDATE live_meetings SET state='live',started_at=?,updated_at=? WHERE id=?",
            (stamp, stamp, row["live_meeting_id"]),
        )
        live_meetings._event(c, row["live_meeting_id"], "meeting.state", {"state": "live", "by": who.actor})
        self._record(
            c,
            who.actor,
            row["id"],
            row["thread_id"],
            "call.started",
            {"meeting_id": row["live_meeting_id"], "provider": row["provider"], "scheduled": True},
        )
        self.app.state.call_prep.live(c, row)
        call_id = row["id"]
        live = _call(c, call_id)
        for bot in live_meetings._attached(c, row["live_meeting_id"]):
            call_sessions.ensure(c, self.store.settings, live, bot["bot"])
        c.after_commit.append(lambda: self._went_live(call_id))
        return True

    def _went_live(self, call_id):
        with self.lock:
            self.went_live.add(call_id)

    def take_went_live(self, call_id):
        """Whether a join just started this scheduled call, once: the join that did starts its media and rings."""
        with self.lock:
            if call_id not in self.went_live:
                return False
            self.went_live.discard(call_id)
            return True

    def cancel(self, c, call_id, reason, by=None):
        """End a scheduled call that never started. Nothing was said in it, so it owes no transcript and no action
        items: it is finalized at once. Returns False when it was no longer scheduled."""
        row = _call(c, call_id)
        if row["state"] != "scheduled":
            return False
        self.finish(c, call_id, reason, by)
        stamp = H.now()
        c.execute("UPDATE calls SET wrapped_up_at=?,finalized_at=? WHERE id=?", (stamp, stamp, call_id))
        return True

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
        """Whether `who` may join this call now: the thread still lets them read it, and the call is live and not ending,
        or scheduled (the join starts it). It runs before a cached retry is answered, and again before a token is
        minted."""
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        _require_open(row, scheduled=True)
        return row

    def join(self, c, who, call_id):
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        _require_open(row, scheduled=True)
        if row["state"] == "scheduled":
            self._go_live(c, who, row)
            row = _call(c, call_id)
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
                "INSERT INTO call_participants(call_id,actor,joined_at,in_call) VALUES(?,?,?,1) "
                "ON CONFLICT(call_id,actor) DO UPDATE SET joined_at=excluded.joined_at,left_at=NULL,removed_at=NULL,"
                "media_removed_at=NULL,in_call=1",
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
                "UPDATE call_participants SET left_at=?,media_removed_at=NULL,in_call=CASE WHEN ? THEN in_call ELSE 0 END "
                "WHERE call_id=? AND actor=?",
                (H.now(), int(_google(row)), call_id, who.actor),
            )
            self._record(c, who.actor, call_id, row["thread_id"], "call.left", {"actor": who.actor})
            self._drop_stage(c, row, who.actor)
        if not current:
            return {"id": call_id, "left": True}
        return self.view(c, call_id, who)

    def end(self, c, who, call_id, gone=None):
        """End the call. A Google Meet one ends here. A Tico Meet one is marked ending: nothing more is heard or said in
        it, its bots' open turns end, and it is ended once the speech already heard is in its transcript (wind_down).
        A scheduled one is cancelled, with its event: `gone` is the Google delete made before this write."""
        row = self.may_end(c, who, call_id)
        if row["state"] == "ended":
            return self.view(c, call_id, who)
        if row["state"] == "scheduled":
            calendar_api.record_call_cancel(c, who, call_id, gone)
            self.cancel(c, call_id, "cancelled", by=who.actor)
            return self.view(c, call_id, who)
        if _google(row):
            self.finish(c, call_id, "ended_by_connector", by=who.actor)
            return self.view(c, call_id, who)
        if not row["ending_at"]:
            c.execute("UPDATE calls SET ending_at=?,updated_at=? WHERE id=?", (H.now(), H.now(), call_id))
            live_meetings._end_open_turns(c, row["live_meeting_id"])
            call_sessions.stop(c, row["live_meeting_id"])
            self._record(c, who.actor, call_id, row["thread_id"], "call.ending", {})
        return self.view(c, call_id, who)

    def set_stage(self, c, who, call_id, kind, file_id=None):
        """Show the caller's screen or a Tico file on the stage, for everyone in the call. Only someone in the call may,
        and a file only when they may read it; its current version is what shows. The newest write wins: it replaces
        whatever showed, whoever showed it."""
        row = self._stage_call(c, who, call_id)
        stage = {"kind": kind, "by": who.actor, "at": H.now(), "n": _stage_n(row) + 1}
        if kind == "file":
            file = self._stage_file(c, who, file_id)
            if file is None:
                raise Problem("not_found", "File not found", 404)
            stage.update(file_id=file["id"], version=int(file["current_version"]))
        elif file_id:
            raise Problem("validation_error", "A screen takes no file_id", 422)
        c.execute("UPDATE calls SET stage=?,updated_at=? WHERE id=?", (json.dumps(stage), H.now(), call_id))
        self._record(
            c, who.actor, call_id, row["thread_id"], "call.stage", {"kind": kind, "file_id": stage.get("file_id")}
        )
        return self.view(c, call_id, who)

    def clear_stage(self, c, who, call_id):
        """Stop showing: the presenter's own stop, or the connector's. Anyone else's leaves the stage as it is (a stop
        sent after someone else took the stage changes nothing)."""
        row = self._stage_call(c, who, call_id, ending_ok=True)
        stage = _stage_record(row)
        if stage and who.actor in (stage["by"], row["connector_actor"]):
            self._drop_stage(c, row, stage["by"])
        return self.view(c, call_id, who)

    def _stage_call(self, c, who, call_id, ending_ok=False):
        """The call for a stage write: a Tico Meet call the caller reads and is in now (or started)."""
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        if _google(row):
            raise Problem("not_tico_meet", "This call is a Google Meet: share in Meet", 409)
        if not ending_ok:
            _require_open(row)
        connector_stop = ending_ok and who.actor == row["connector_actor"]
        if not connector_stop and not _present(_participant(c, call_id, who.actor)):
            raise Problem("not_joined", "Join the call to show something in it", 403)
        return row

    def _drop_stage(self, c, row, actor):
        """Clear the stage when `actor` is the one showing on it. The count goes on, so a read can tell an older one."""
        current = _call(c, row["id"])
        stage = _stage_record(current)
        if not stage or stage["by"] != actor:
            return
        cleared = json.dumps({"n": _stage_n(current) + 1})
        c.execute("UPDATE calls SET stage=?,updated_at=? WHERE id=?", (cleared, H.now(), row["id"]))
        self._record(c, actor, row["id"], row["thread_id"], "call.stage", {"kind": None})

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
        call_sessions.stop(c, row["live_meeting_id"])
        self._record(c, actor, call_id, row["thread_id"], "call.ended", {"reason": reason})
        c.after_commit.append(lambda: self.voice.forget(call_id))
        return True

    def attach_bots(self, c, who, call_id, slugs):
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        meeting = live_meetings._meeting(c, row["live_meeting_id"])
        if row["state"] != "live" or row["ending_at"] or meeting["state"] not in ("live", "paused"):
            raise Problem("meeting_state", "Bots can only join a live call", 409)
        if not c.execute(
            "SELECT 1 FROM live_meeting_humans WHERE meeting_id=? AND actor=?", (meeting["id"], who.actor)
        ).fetchone():
            raise Problem("forbidden", "Join this call before attaching bots", 403)
        self._attach(c, who, row, meeting, slugs)
        self.app.state.call_prep.live(c, row)
        return self.view(c, call_id, who)

    def _may_attach(self, c, who, slugs):
        """Each bot is on the team and active, and `who` may write to it."""
        for slug in bot_ids(c, slugs):
            live_meetings._active_bot(c, slug)
            access = teammate_access.levels_for(c, who, f"bot:{slug}", self.auth)
            if not access["see"]:
                raise Problem("forbidden", f"You cannot see org-chart bot {slug}", 403)
            if not access["write"]:
                raise Problem("forbidden", f"You can see {slug} but do not have Write access to contact it", 403)

    def _attach(self, c, who, row, meeting, slugs):
        """Attach bots `who` may write to, BOT_LIMIT in the call at most: the limit counts the bots already in it."""
        call_id = row["id"]
        slugs = bot_ids(c, slugs)
        live_meetings.check_call_bot_limit(c, meeting["id"], slugs)
        alone_with_assistant(c, row["thread_id"], slugs)
        self._may_attach(c, who, slugs)
        for slug in slugs:
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
            call_sessions.ensure(c, self.store.settings, row, slug)

    def detach_bot(self, c, who, call_id, slug):
        human_only(who)
        self.auth.domain(who)
        slug = (H.resolve_bot(c, slug) or {}).get("id", slug)  # a bot since deleted still leaves by its id
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
        call_sessions.stop(c, row["live_meeting_id"], slug)
        cursor = c.execute("DELETE FROM live_meeting_bots WHERE meeting_id=? AND bot=?", (row["live_meeting_id"], slug))
        if cursor.rowcount:
            cancel_bot_turns(c, row["live_meeting_id"], slug, "bot_detached")
            live_meetings._event(c, row["live_meeting_id"], "meeting.bot_left", {"bot": slug, "by": who.actor})
            self._record(c, who.actor, call_id, row["thread_id"], "call.bot_detached", {"bot": slug})
            c.after_commit.append(lambda: self.voice.cancel(call_id, slug))
        return self.view(c, call_id, who)

    # --- media, transcript and the thread, around the writes above

    def begin_media(self, call_id):
        """Start the server's recorder in the call's room after the call is stored. Without the media server the call
        ends at once, with a refusal, so no one joins a room that will not be heard."""
        with self.store.read() as c:
            row = _call(c, call_id)
        if _google(row):
            return  # Google runs a Meet's media; nothing of LiveKit's starts
        if row["speak_replies"]:
            self.voice.warm()  # the Gateway's voices are read while the call rings, not when a bot first speaks
        try:
            self.media.start_recorder(call_id, row["room_name"], self._audio_for(call_id), self._quiet_for(call_id))
        except call_media.MediaUnavailable:
            with self.store.transaction() as c:
                self.finish(c, call_id, "meet_unavailable")
                c.execute("UPDATE calls SET finalized_at=? WHERE id=?", (H.now(), call_id))  # nothing was said
            raise Problem("meet_unavailable", "Tico Meet's media server could not start this call", 503) from None
        with self.store.transaction() as c:
            c.execute("UPDATE calls SET media_seen_at=? WHERE id=? AND media_seen_at IS NULL", (H.now(), call_id))

    def _audio_for(self, call_id):
        def on_audio(identity, pcm, start_ms):
            return self.ingest(call_id, identity, pcm, start_ms)

        return on_audio

    def _quiet_for(self, call_id):
        def on_quiet(identity):
            self.flush(call_id, identity)  # their audio stopped coming: what they said goes now

        return on_quiet

    def ingest(self, call_id, identity, pcm, start_ms):
        """One piece of one person's speech from the recorder. It waits in memory until the person pauses
        (call_voice.Segmenter); each utterance then goes to be transcribed. Nothing is heard once End was pressed."""
        if call_media.is_voice(identity):
            return
        key = (call_id, identity)
        with self.lock:
            if call_id in self.closing:
                return
            segmenter = self.buffers.get(key)
            now_ms = round(self.voice.clock() * 1000)
            if (
                segmenter is not None
                and segmenter.end is not None
                and start_ms < segmenter.end // call_voice.BYTES_PER_MS
            ):
                # The recorder came back on a new timeline: what was under way is sent as it stands, and the new
                # timeline gets its own place on the call's clock.
                if clip := segmenter.flush():
                    self._submit(call_id, identity, self._clip_buffer(segmenter, *clip))
                segmenter = None
            if segmenter is None:
                segmenter = self.buffers[key] = call_voice.Segmenter()
                # This timeline onto the call's clock in ms: the piece's end is now.
                segmenter.offset_ms = now_ms - (start_ms + len(pcm) // call_voice.BYTES_PER_MS)
            segmenter.at = self.voice.clock()
            was, barges = segmenter.speaking, segmenter.barges
            clips = segmenter.feed(pcm, start_ms)
            if (voiced := segmenter.voiced_at()) is not None:
                voiced = min(voiced, now_ms)  # never later than now, however the timeline runs
                self.voiced[call_id] = max(self.voiced.get(call_id, voiced), voiced)
            if segmenter.barges != barges:
                self.voice.human_speaking(call_id)  # they have said enough to take the floor from a bot's voice
            for start, clip in clips:
                self._submit(call_id, identity, self._clip_buffer(segmenter, start, clip))
            self._transcribe_early(key, segmenter)
            if was and not segmenter.speaking and not clips:
                self._kick(call_id)  # a cough, not speech: a clip it held back may be stored now
            return segmenter.idle_seconds()

    @staticmethod
    def _clip_buffer(segmenter, start, pcm):
        """A clip as `_submit` takes it: its audio, where it starts on its recorder's timeline and on the call's clock
        (ms), and its first and last voiced frame on the call's clock (Segmenter.spans)."""
        buffer = {"pcm": pcm, "start_ms": start, "at_ms": start + segmenter.offset_ms}
        if segmenter.spans:
            buffer["voiced_from"], buffer["voiced_to"] = segmenter.spans.pop(0)
        return buffer

    def turn_pending_locked(self, call_id):
        """Whether a person is speaking in the call, or a clip of theirs waits for its text or is being stored: their
        turn goes on until its clip is stored (self.lock held)."""
        order = self.order.get(call_id)
        return bool(order and (order["queue"] or order.get("storing"))) or any(
            cid == call_id and segmenter.speaking for (cid, _), segmenter in self.buffers.items()
        )

    def speech_pending(self, call_id):
        """A person is speaking or what they said has not yet reached the question router."""
        with self.lock:
            return any(not future.done() for future in self.pending.get(call_id, ())) or any(
                cid == call_id and segmenter.speaking for (cid, _), segmenter in self.buffers.items()
            )

    def flush(self, call_id=None, identity=None, *, idle=False):
        """Send waiting speech, or with idle=True only speech whose pause has reached its threshold."""
        now = self.voice.clock()
        with self.lock:
            for key, segmenter in list(self.buffers.items()):
                if call_id is not None and key[0] != call_id or identity is not None and key[1] != identity:
                    continue
                if idle and now - segmenter.at < segmenter.idle_seconds():
                    continue
                was = segmenter.speaking
                clip = segmenter.flush()
                if not segmenter.speaking:
                    self.buffers.pop(key, None)
                if clip:
                    self._submit(key[0], key[1], self._clip_buffer(segmenter, *clip))
                elif was:
                    self._kick(key[0])
                self._drop_early(key)

    def _submit(self, call_id, identity, buffer):
        """Transcribe a clip on a worker, so the recorder's audio never waits on the Gateway (self.lock held). The clip
        takes its place in the call's queue by where it starts, and is stamped with when it was heard (the voice's
        clock), so a question is aged from then. A call has BACKLOG clips waiting for their text at most: past that the
        oldest is dropped, and in a 1:1 bot call the person is asked to repeat it."""
        order = self.order.setdefault(call_id, {"n": 0, "queue": [], "settling": False, "timer": None})
        buffer["heard"] = self.voice.clock()
        buffer["duration_ms"] = len(buffer["pcm"]) * 1000 // PCM_BYTES_PER_SECOND
        # Ordered on the call's clock: a recorder that came back on a new timeline still sorts after what came before.
        clip = {
            "n": order["n"],
            "start": buffer.get("at_ms", buffer["start_ms"]),
            "identity": identity,
            "buffer": buffer,
        }
        clip.update(text=None, ready=False, discard=None, early="none")
        reuse = None
        if early := self.early.pop((call_id, identity), None):
            if early["snapshot"] == (buffer["start_ms"], buffer["pcm"]):
                clip["early"], reuse = "hit", early["future"]
            else:
                clip["early"] = "miss"
                early["future"].cancel()
        order["n"] += 1
        bisect.insort(order["queue"], clip, key=lambda one: (one["start"], one["n"]))
        out = sorted((one for one in order["queue"] if not one["ready"]), key=lambda x: x["n"])
        if len(out) > BACKLOG:
            for oldest in out[: len(out) - BACKLOG]:
                self._discard_clip(oldest, "dropped_backlog")
            self._kick(call_id)
        if reuse:
            self._reuse_early(call_id, clip, reuse)
        else:
            self._track(call_id, self.workers.submit(self._transcribe_clip, call_id, clip))

    def _reuse_early(self, call_id, clip, early):
        """Give a clip the text of its early transcription when that is done (self.lock held), without a worker of its
        own. Only now is the call's work tracked, so drain and settle wait for this clip, not for unused early requests."""
        done = Future()
        self._track(call_id, done)

        def finish(future):
            try:
                self._finish_clip(call_id, clip, lambda: None if future.cancelled() else future.result())
            finally:
                done.set_result(None)

        early.add_done_callback(finish)

    def _transcribe_early(self, key, segmenter):
        """Start transcribing an utterance as its pause begins (self.lock held), so its text can be ready when the pause
        ends. Speech resuming (or a new pause) drops it; its text is then unused. It is not the call's work until a clip
        uses it (`_reuse_early`), so an unused one holds up neither drain nor speech_pending."""
        early = self.early.get(key)
        if early is not None and early["snapshot"] is segmenter.early:
            return
        self._drop_early(key)
        if segmenter.early is not None:
            future = self.workers.submit(self._transcribe, key[0], {"pcm": segmenter.early[1]})
            self.early[key] = {"snapshot": segmenter.early, "future": future}

    def _drop_early(self, key):
        if early := self.early.pop(key, None):
            early["future"].cancel()

    @staticmethod
    def _discard_clip(clip, outcome):
        clip.update(ready=True, discard=outcome)
        clip["buffer"]["pcm"] = b""

    def _track(self, call_id, future):
        """Keep a call's work until it is done (drain and settle wait on it), and no longer."""
        self.pending.setdefault(call_id, set()).add(future)

        def done(finished):
            with self.lock:
                out = self.pending.get(call_id)
                if out is not None:
                    out.discard(finished)
                    if not out:
                        self.pending.pop(call_id, None)

        future.add_done_callback(done)

    def _kick(self, call_id):
        """Schedule at most one writer per call (self.lock held). Gateway work cannot occupy the writer."""
        order = self.order.get(call_id)
        if order and order["queue"] and not order["settling"]:
            order["settling"] = True
            self._track(call_id, self.clip_writer.submit(self._settle, call_id))

    def drain(self, call_id):
        """Wait (up to DRAIN_SECONDS) for the clips a call is still transcribing, so its transcript has them all."""
        deadline = time.monotonic() + DRAIN_SECONDS
        while True:
            with self.lock:
                waiting = [future for future in self.pending.get(call_id, ()) if not future.done()]
            if not waiting or wait(waiting, timeout=max(0, deadline - time.monotonic())).not_done:
                return

    def finish_speech(self, call_id):
        """Send the speech still waiting for a call, and wait for it: the call's transcript must have every clip."""
        self.flush(call_id)
        self.drain(call_id)

    def _transcribe_clip(self, call_id, clip):
        self._finish_clip(call_id, clip, lambda: self._transcribe(call_id, clip["buffer"]))

    def _finish_clip(self, call_id, clip, transcribe):
        """Give a clip not discarded its text (None when transcription failed or was cancelled), and wake the writer."""
        text = None
        try:
            if not clip["discard"]:
                text = transcribe()
        except Exception as exc:  # noqa: BLE001 - a failed transcription is reported, the call goes on
            from .batch_work import report

            report("calls_transcribe", call_id, exc, logger="tico.calls")
        finally:
            with self.lock:
                if not clip["discard"]:
                    clip.update(text=text, ready=True)
                    self._kick(call_id)

    def _transcribe(self, call_id, buffer):
        """The Gateway's text (possibly empty), or None when transcription was unavailable or the call ended."""
        pcm = bytes(buffer["pcm"])
        if not pcm:
            return None
        with self.store.read() as c:
            state = c.execute("SELECT state FROM calls WHERE id=?", (call_id,)).fetchone()
            if not state or state["state"] != "live":
                return None  # the call has ended: its speech was sent at the end, and nothing after it is kept
            key, base, _fingerprint = gateway_connection.target(c, self.app.state.vault, self.store.settings)
        if not key:
            log.warning("A call's speech was not transcribed: no Gateway connection is set")
            return None
        try:
            return call_media.transcribe(base, key, call_media.wav_bytes(pcm))
        except call_media.MediaUnavailable as exc:
            log.warning("call_clip_not_transcribed call=%s status=%s", call_id, getattr(exc, "status", 0))
            return None  # this clip is lost and the call goes on

    def _held_back(self, call_id, clip):
        """Whether someone else in the call is still saying something they began before this clip (self.lock held): the
        clip waits for it, so the transcript is in the order things were said."""
        return any(
            key[0] == call_id
            and key[1] != clip["identity"]
            and segmenter.speaking
            and segmenter.start + segmenter.offset_ms < clip["start"]
            for key, segmenter in self.buffers.items()
        )

    def _wake_clip_writer(self, call_id):
        with self.lock:
            self._kick(call_id)

    def _next_clip(self, call_id, order):
        """Take a clip to settle (self.lock held), or schedule a wakeup if a slow request is holding up ready text."""
        if timer := order["timer"]:
            timer.cancel()
            order["timer"] = None
        queue = order["queue"]
        for index, clip in enumerate(queue):
            if clip["discard"]:
                return queue.pop(index)
        if not queue or self._held_back(call_id, queue[0]):
            return None
        head = queue[0]
        if head["ready"]:
            return queue.pop(0)
        if not any(clip["ready"] for clip in queue[1:]):
            return None
        delay = head["buffer"]["heard"] + CLIP_ORDER_WAIT_SECONDS - self.voice.clock()
        if delay <= 0:
            self._discard_clip(head, "late")
            return queue.pop(0)
        timer = Timer(delay, self._wake_clip_writer, args=(call_id,))
        timer.daemon = True
        order["timer"] = timer
        timer.start()
        return None

    def _settle(self, call_id):
        """Write clips in the order speech began. A discarded clip reports its outcome without blocking this order."""
        while True:
            with self.lock:
                order = self.order.get(call_id)
                if not order:
                    return
                clip = self._next_clip(call_id, order)
                if clip is None:
                    order["settling"] = False
                    return
                order["storing"] = True  # until it has been routed (turn_pending)
            outcome = "store_failed"
            try:
                outcome = self._store_clip(call_id, clip)
            except Exception as exc:  # noqa: BLE001 - one clip that cannot be stored must not hold up the rest
                from .batch_work import report

                report("calls_store_clip", call_id, exc, logger="tico.calls")
            finally:
                with self.lock:
                    order["storing"] = False
                clip["buffer"]["pcm"] = b""
                self._log_clip(call_id, clip, outcome)
            if outcome == "stored":
                try:
                    self.voice.ask_ready(call_id)  # a 1:1 call's turn this clip ended (call_voice.Voice.ask_ready)
                except Exception as exc:  # noqa: BLE001 - the ticker asks it again
                    from .batch_work import report

                    report("calls_voice", call_id, exc, logger="tico.calls")

    def _log_clip(self, call_id, clip, outcome):
        buffer = clip["buffer"]
        log.info(
            "call_clip call=%s n=%s ms=%s outcome=%s early=%s wait_ms=%s",
            call_id,
            clip["n"],
            buffer["duration_ms"],
            outcome,
            clip["early"],
            max(0, round((self.voice.clock() - buffer["heard"]) * 1000)),
        )

    def _store_clip(self, call_id, clip):
        """Store and route one clip. The segmenter only submits clips with enough voiced audio to be speech, so
        even an empty Gateway result asks for a repeat in a 1:1 bot call."""
        identity, buffer, text = clip["identity"], clip["buffer"], clip["text"]
        with self.store.transaction(notify=True) as c:
            row = c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            if not row or row["state"] != "live":
                return "ended"
            meeting = live_meetings._meeting(c, row["live_meeting_id"])
            if meeting["state"] != "live":
                return "ended"
            voiced = bool(voice_call(c, meeting["id"]))
            if not text:
                if voiced and not row["ending_at"] and self.voice.answers_all(c, row):
                    self.voice._note(self.voice._call(call_id), REPEAT_NOTE)
                    self.voice.touch(call_id, c)
                return clip["discard"] or ("empty" if text == "" else "untranscribed")
            previous = c.execute(
                "SELECT COALESCE(MAX(start_ms),0) FROM live_meeting_chunks WHERE meeting_id=?", (meeting["id"],)
            ).fetchone()[0]
            start = max(buffer["start_ms"], previous)
            chunk = {
                "seq": meeting["seq"] + 1,
                "speaker": H.actor_id(identity),
                "start_ms": start,
                "end_ms": start + buffer["duration_ms"],
                "text": text,
                "heard": buffer.get("heard"),
                "voiced_from": buffer.get("voiced_from"),
                "voiced_to": buffer.get("voiced_to"),
                "spoken": True,
            }
            live_meetings.append_chunks(c, meeting["id"], meeting, identity, [chunk])
        if not voiced:
            self.app.state.live_meeting_routing.later(meeting["id"])
        return "stored"

    def stop_listening(self, call_id):
        """End was pressed (and stored): nothing more is heard, and the bots stop speaking now."""
        with self.lock:
            self.closing.add(call_id)
        self.voice.cancel(call_id)

    def wind_down(self, call_id, by, wait_seconds=None):
        """After End: the speech already heard goes to be transcribed, and the call is ended once it is in, by one thread
        of its own however many Ends (or sweeps) ask. Each waits for it END_WAIT_SECONDS (or `wait_seconds`), so two
        Ends pressed together answer alike. Returns True when the call is ended now."""
        with self.lock:
            worker = self.ending.get(call_id)
            if worker is None:
                worker = threading.Thread(target=self._close, args=(call_id, by), name="tico-meet-end", daemon=True)
                self.ending[call_id] = worker
                worker.start()
        worker.join(END_WAIT_SECONDS if wait_seconds is None else wait_seconds)
        return not worker.is_alive()

    def _close(self, call_id, by, reason="ended_by_connector"):
        try:
            self.finish_speech(call_id)
            with self.store.transaction() as c:
                self.finish(c, call_id, reason, by=by)
            self.after_end(call_id)
        except Exception as exc:  # noqa: BLE001 - the sweep finishes a call whose end failed here
            from .batch_work import report

            report("calls_end", call_id, exc, logger="tico.calls")
        finally:
            with self.lock:
                if self.ending.get(call_id) is threading.current_thread():
                    self.ending.pop(call_id, None)
                self.closing.discard(call_id)

    def settle(self, timeout: float = 60):
        """Wait until every clip is stored, every call End finished is ended and every reply is spoken (tests)."""
        deadline = time.monotonic() + timeout
        while True:
            with self.lock:
                futures = [f for waiting in self.pending.values() for f in waiting if not f.done()]
                enders = list(self.ending.values())
            if not futures and not enders:
                break
            wait(futures, timeout=max(0.0, deadline - time.monotonic()))
            for worker in enders:
                worker.join(max(0.0, deadline - time.monotonic()))
            if time.monotonic() > deadline:
                return False
        return self.voice.settle(max(0.0, deadline - time.monotonic()))

    def after_end(self, call_id):
        """What an ended call still owes: its room closed, its last windows, its transcript and its action items in the
        thread. Returns False while any of that is pending, so the sweep comes back to it; once it is all settled the call
        is marked finalized and nothing here runs again."""
        with self.store.read() as c:
            row = _call(c, call_id)
        if row["finalized_at"]:
            return True
        if row["wrapped_up_at"] is None:
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
        with self.lock:
            self.voiced.pop(call_id, None)
            for key in [key for key in self.buffers if key[0] == call_id]:
                self.buffers.pop(key, None)
            self.early = {key: early for key, early in self.early.items() if key[0] != call_id}
            order = self.order.pop(call_id, None)
            if order and order["timer"]:
                order["timer"].cancel()
        if order:
            for clip in order["queue"]:
                self._log_clip(call_id, clip, "ended")
        return True

    def _post_in_thread(self, c, row, body, notes=None):
        """One message in the call's thread, from its connector, naming the call and its meeting as the source. The
        live_meeting reference makes the meeting's own rules decide who reads the message. `notes`: the action items
        and summary, as data under refs.call. It is the call's record, not something the connector said to the
        thread's bots: like the call's card it wakes none of them (`record`, threads.quiet_notice), so a bot does not
        start on the action items by itself. Returns the message."""
        who = self.auth.identity_for_actor(c, row["connector_actor"])
        return threads.post(
            c,
            who,
            row["thread_id"],
            body,
            kind="say",
            refs={
                "call": {
                    "id": row["id"],
                    "meeting_id": row["live_meeting_id"],
                    "record": True,
                    **({"notes": notes} if notes else {}),
                },
                "live_meeting": {"meeting_id": row["live_meeting_id"]},
            },
        )

    def post_transcript(self, call_id):
        """The call's transcript, bounded, as one message in its thread, with the meeting it came from. Once only. A
        thread that refuses the post leaves it unposted: that refusal is a hold the connector sees, and it is final.
        A call without transcribed speech leaves only its call card."""
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
                if not lines and not cut:
                    return
                body = "\n".join([f"Call transcript: {row['title']}", *lines])
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
            model = call_items.model(c, self.store.settings)
        if not rows or not key:
            return True
        try:
            notes = call_items.draft(base, key, model, rows)
        except call_items.ItemsUnavailable as exc:
            log.warning("A call's action items were not drafted: %s", exc)
            return self._action_items_failed(call_id)
        items, summary = notes["items"], notes["summary"]
        # The meeting window's notes read them from here (backend/call_notes.py): the items with who took each on.
        shown = {"summary": summary, "items": [{"title": item["title"], "owner": item["owner"]} for item in items]}
        try:
            with self.store.transaction() as c:
                row = _call(c, call_id)
                if row["action_items_posted_at"]:
                    return True
                if items or summary:  # neither posts nothing; the call is still done with its action items
                    body = f"Action items: {row['title']}"  # the header is what backend/call_notes.py looks for
                    if items:
                        body += "\n" + "\n".join(f"- {item['title']}" for item in items)
                    if summary:
                        body += "\n\nSummary:\n" + "\n".join(f"- {line}" for line in summary)
                    self._post_in_thread(c, row, body, notes=shown)
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
        from . import call_follow_ups

        self.flush(idle=True)
        call_follow_ups.sweep(self)
        with self.store.read() as c:
            ids = [
                r["id"]
                for r in c.execute(
                    "SELECT id FROM calls WHERE state<>'scheduled' AND (state='live' OR finalized_at IS NULL OR "
                    "(provider='tico' AND id IN (SELECT call_id FROM call_participants WHERE media_removed_at IS NULL "
                    "AND (left_at IS NOT NULL OR removed_at IS NOT NULL))))"
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
        if row["ending_at"]:
            # One thread ends it: the End that is winding it down here, or (its End pressed in a process that is gone,
            # whose speech went with it) one started now. The sweep does not wait for it.
            with self.lock:
                ours = call_id in self.closing
            if not ours:
                self.stop_listening(call_id)
            self.wind_down(call_id, row["connector_actor"], wait_seconds=0)
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
        # Who lost the thread is taken out first, whatever LiveKit answers below: a failed read never keeps them in.
        with self.store.transaction() as c:
            self._keep_to_thread(c, row, revoked, stale_bots)
        if _google(row):
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
        try:
            present = self.media.participants(room) if exists else None
        except call_media.MediaUnavailable:
            present = None  # a read LiveKit did not answer says nothing about who is in the room
        ended = None
        with self.store.transaction() as c:
            if exists and row["media_seen_at"] is None:
                c.execute("UPDATE calls SET media_seen_at=? WHERE id=?", (H.now(), call_id))
            if present is not None:
                ended = self._presence(c, _call(c, call_id), present)
        if ended:
            self.finish_speech(call_id)
            with self.store.transaction() as c:
                self.finish(c, call_id, ended)
            self.after_end(call_id)
            return
        self.drop_pending(call_id)
        if exists and not self.media.running(call_id):
            self.media.start_recorder(call_id, room, self._audio_for(call_id), self._quiet_for(call_id))
        if row["speak_replies"] and self.media.running(call_id):
            from . import call_follow_ups

            call_follow_ups.queue_speech(self, call_id)

    def _presence(self, c, row, present):
        """Record who LiveKit has in a Tico Meet call's room (Tico voice left out), and say whether that ends the call:
        `empty` once nobody has been in it for EMPTY_SECONDS after someone was (time to rejoin), `no_answer` when nobody
        was in it within NO_ANSWER_SECONDS of its start. Returns the end reason, or None."""
        before, in_call_before = dict(row), self._in_call_set(c, row["id"])
        stamp = H.now()
        actors = sorted({identity for identity in present if identity.startswith("human:")})
        c.execute(
            "UPDATE call_participants SET in_call=CASE WHEN actor IN (%s) THEN 1 ELSE 0 END "
            "WHERE call_id=? AND (in_call=1 OR actor IN (%s))"
            % (",".join("?" * len(actors)), ",".join("?" * len(actors))),
            (*actors, row["id"], *actors),
        )
        count = len(present)
        c.execute(
            "UPDATE calls SET live_count=?,status_source='recorder',meet_seen_at=COALESCE(meet_seen_at,?),"
            "meet_active_at=COALESCE(?,meet_active_at),meet_polled_at=?,"
            "meet_empty_at=CASE WHEN ? OR meet_seen_at IS NULL AND ? IS NULL THEN NULL ELSE COALESCE(meet_empty_at,?) END,"
            "updated_at=? WHERE id=?",
            (
                count,
                stamp if count else None,
                stamp if count else None,
                stamp,
                count,
                stamp if count else None,
                stamp,
                stamp,
                row["id"],
            ),
        )
        self._changed(c, row, before, in_call_before)
        row = _call(c, row["id"])
        if count:
            return None
        if row["meet_seen_at"]:
            if row["meet_empty_at"] and _seconds_since(row["meet_empty_at"]) >= EMPTY_SECONDS:
                return "empty"
            return None
        if _seconds_since(row["started_at"]) > NO_ANSWER_SECONDS:
            return "no_answer"
        return None

    def _keep_to_thread(self, c, row, revoked, stale_bots):
        """Take out of the call the people who lost the thread, and the bots whose attacher did."""
        call_id = row["id"]
        for actor in revoked:
            cursor = c.execute(
                "UPDATE call_participants SET removed_at=?,in_call=0 WHERE call_id=? AND actor=? "
                "AND left_at IS NULL AND removed_at IS NULL",
                (H.now(), call_id, actor),
            )
            if cursor.rowcount:
                self._record(c, actor, call_id, row["thread_id"], "call.participant_removed", {"actor": actor})
                self._drop_stage(c, row, actor)
        for slug in stale_bots:
            c.after_commit.append(lambda slug=slug: self.voice.cancel(call_id, slug))
            cancel_bot_turns(c, row["live_meeting_id"], slug, "attacher_lost_thread")
            call_sessions.stop(c, row["live_meeting_id"], slug)
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

    # --- Google Meet: status from Google, and ending the conference

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
        nobody was seen in within NO_ANSWER_SECONDS of its start ends `no_answer`. Google refusing clears the status,
        and a call with no status from Google ends `stale` STALE_SECONDS after it started. Returns the end reason, or
        None."""
        ended = None
        with self.store.transaction() as c:
            row = _call(c, call_id)
            if not _google(row) or row["state"] != "live":
                return None
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

    def ringing(self, c, who):
        """The live calls ringing `who` now, in threads they can still read, newest first."""
        human_only(who)
        self.auth.domain(who)
        pattern = "%" + json.dumps(who.actor).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        found = []
        for r in c.execute(
            "SELECT id,thread_id FROM calls WHERE state='live' AND ending_at IS NULL AND ring LIKE ? ESCAPE '\\' "
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

    def live(self, c, who, thread_id=""):
        """The live Tico Meet calls in threads `who` can read (the thread's own read rule), newest first, as call views:
        the Meetings page's Live. An ending call is left out; it is over for anyone looking. With `thread_id`, every
        live call in that thread, a Google Meet or an ending call too, since each refuses a start there (`_none_live`),
        picked before the list's cap (the Assistant sheet's Call and Join)."""
        human_only(who)
        self.auth.domain(who)
        found = []
        sql, args = "SELECT id,thread_id FROM calls WHERE state='live' ", []
        if thread_id:
            sql, args = sql + "AND thread_id=? ", [thread_id]
        else:
            sql += "AND provider='tico' AND ending_at IS NULL "
        for r in c.execute(sql + "ORDER BY started_at DESC LIMIT ?", (*args, LIVE_LIMIT * 5)):
            if len(found) >= LIVE_LIMIT:
                break
            if readable(c, who.actor, r["thread_id"]):
                found.append(self.view(c, r["id"], who))
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
    global AUTH, CALLS
    AUTH = auth
    if project_reply not in live_meetings.REPLY_HOOKS:
        live_meetings.REPLY_HOOKS.append(project_reply)
    if spoken_prompt not in live_meetings.PROMPT_HOOKS:
        live_meetings.PROMPT_HOOKS.append(spoken_prompt)
    if voice_call not in live_meetings.WINDOW_GATES:
        live_meetings.WINDOW_GATES.append(voice_call)  # a voice call's bots answer when asked, never on their own
    if route_question not in live_meetings.QUESTION_ROUTERS:
        live_meetings.QUESTION_ROUTERS.append(route_question)
    if bot_left not in live_meetings.BOT_LEFT_HOOKS:
        live_meetings.BOT_LEFT_HOOKS.append(bot_left)
    if not hasattr(app.state, "call_media"):
        config = call_media.config_from_env()
        app.state.call_media = call_media.LiveKitMedia(config) if config else None
    calls = app.state.calls = CALLS = Calls(app, store, auth)
    prep = app.state.call_prep = call_prep.Prep(app, store, calls)
    from . import call_follow_ups

    call_follow_ups.install(app, auth, write)

    @app.get(
        "/api/v2/threads/{thread_id}/calls",
        **api_v2(
            "Calls",
            "listCalls",
            "The calls in a thread, newest first, a page at a time: before=<next_before> reads the next page",
            "CallList",
        ),
    )
    def list_calls(request: Request, thread_id: str, limit: int = 25, before: str = ""):
        """A thread's calls, newest first, a page at a time. `next_before` is the cursor for the next page, or null.
        `providers` is what the viewer could start, in the order `auto` tries them; `available` is whether that is
        anything. `next_scheduled` is the thread's soonest scheduled call, whatever the page."""
        who = request.state.identity
        human_only(who)
        auth.domain(who)
        size = min(max(limit, 1), LIST_LIMIT)
        at, _, call_id = before.partition("|")
        with store.read() as c:
            threads.require(c, threads.viewer(auth, who), thread_id)
            offered = calls.providers(c, who)
            address = google_workspace.google_address(c, who.actor) or ""
            found = [
                {**_summary(r), **calls.provider_fields(c, r, who, address=address)}
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
        with store.read() as c:
            upcoming = calls.next_scheduled(c, who, thread_id)
        return {
            "calls": shown,
            "next_before": f"{shown[-1]['started_at']}|{shown[-1]['id']}" if more else None,
            "available": bool(offered),
            "providers": offered,
            "next_scheduled": upcoming,
        }

    @app.post(
        "/api/v2/threads/{thread_id}/calls",
        **api_v2(
            "Calls",
            "startCall",
            "Start a call in a thread you can write to: provider auto (Google Meet when your Workspace mailbox has it, else Tico Meet), google_meet or tico. A Google Meet start makes the space, posts the call's card and rings. With starts_at and ends_at the call is scheduled instead: a Tico Meet call, one event on your calendar, started by its first join",
            "Call",
        ),
    )
    def start_call(request: Request, thread_id: str, body: StartCall):
        """Start a call. A Google Meet start makes its space before the write, outside any transaction; a retry with the
        same idempotency key is answered from the first answer and makes no second space (the lock keeps two copies
        sent at once from both making one). A scheduled start writes its calendar event the same way."""
        who = request.state.identity
        key = request.headers.get("idempotency-key") or ""
        if body.starts_at is not None or body.ends_at is not None:
            return schedule_call(request, who, key, thread_id, body)
        entry = calls.start_lock(who.actor, key)
        try:
            with entry[0]:
                replayed = calls.replayed(request, who)
                provider, space = "tico", None
                if not replayed:
                    with store.read() as c:
                        provider = calls.choose(c, who, thread_id, body.provider, body.speak_replies, body.bots)
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
                    retry_body=lambda _c, _result: _start_hashed_before_voice(body),
                    fn=lambda c: calls.start(
                        c,
                        who,
                        thread_id,
                        body.title,
                        body.speak_replies,
                        provider=provider,
                        space=space,
                        bots=body.bots,
                    ),
                )
        finally:
            calls.start_unlock(who.actor, key, entry)
        if result.get("provider", "tico") == "tico":
            calls.begin_media(result["id"])
        if not replayed:
            calls.ring_started(result["id"])
        return result

    def schedule_call(request, who, key, thread_id, body):
        """Book a call for later. Its calendar event is written first; a calendar that refuses it leaves no call, and a
        write that fails after a Google event was made takes the event back. A retry is answered from its stored
        answer before its times are checked again, so one sent after its start time passed still gets it."""
        entry = calls.start_lock(who.actor, key)
        try:
            with entry[0]:
                plan = None
                if not calls.replayed(request, who):
                    when = booking(body.starts_at, body.ends_at)
                    with store.read() as c:
                        plan = calls.schedule_plan(c, who, thread_id, body.title, when, body.bots, key)
                    calls.schedule_event(plan)
                try:
                    return write(
                        request,
                        body,
                        fn=lambda c: calls.start(
                            c,
                            who,
                            thread_id,
                            body.title,
                            body.speak_replies,
                            provider="tico",
                            bots=plan["bots"] if plan else body.bots,
                            scheduled=plan,
                        ),
                    )
                except BaseException:
                    if plan is not None:
                        calls.unschedule_event(plan)
                    raise
        finally:
            calls.start_unlock(who.actor, key, entry)

    @app.get(
        "/api/v2/calls/upcoming",
        **api_v2(
            "Calls",
            "listUpcomingCalls",
            "The scheduled calls you are in, soonest first, with who is in them",
            "CallUpcoming",
        ),
    )
    def upcoming_calls(request: Request):
        who = request.state.identity
        with store.read() as c:
            return calls.upcoming(c, who)

    @app.get(
        "/api/v2/calls/ringing",
        **api_v2("Calls", "listRingingCalls", "The live calls ringing you now, in threads you can read", "CallRinging"),
    )
    def ringing_calls(request: Request):
        """The live calls ringing the caller now (page load and reconnect), in threads they can read."""
        who = request.state.identity
        with store.read() as c:
            return calls.ringing(c, who)

    @app.get(
        "/api/v2/calls/live",
        **api_v2(
            "Calls",
            "listLiveCalls",
            "The live Tico Meet calls in threads you can read, newest first; with thread, that thread's alone",
            "CallLive",
        ),
    )
    def live_calls(request: Request, thread: str = Query("", max_length=200)):
        who = request.state.identity
        with store.read() as c:
            return calls.live(c, who, thread)

    @app.get(
        "/api/v2/calls/{call_id}",
        **api_v2("Calls", "getCall", "A call: its state, people, bots and how it ended", "Call"),
    )
    def call_detail(request: Request, call_id: str):
        who = request.state.identity
        with store.read() as c:
            return calls.readable_view(c, who, call_id)

    @app.post(
        "/api/v2/calls/{call_id}/join",
        **api_v2(
            "Calls",
            "joinCall",
            "Join a call. Tico Meet: its media URL and a token good for 5 minutes. Google Meet: the Meet link as you open it, no token",
            "CallJoin",
        ),
    )
    def join_call(request: Request, call_id: str, body: Empty):
        """Join a call. The first join of a scheduled call reads its event live first (call_prep.Prep.at_join): one
        cancelled on its calendar is refused, one moved starts with its new times."""
        who = request.state.identity
        if not calls.replayed(request, who):
            with store.read() as c:
                row = calls.check_join(c, who, call_id)
            if row["state"] == "scheduled":
                prep.at_join(call_id)
        result = write(
            request, body, lambda c: calls.join(c, who, call_id), check=lambda c: calls.check_join(c, who, call_id)
        )
        if calls.take_went_live(call_id):
            # This join started a scheduled call: its media and its ringing start here, as a start's do.
            calls.begin_media(call_id)
            calls.ring_started(call_id)
        with store.read() as c:
            row = calls.check_join(c, who, call_id)  # a token is minted only for a person who may still join
        if _google(row):
            return {"call": result, "url": result.get("join_url")}  # Google runs the media: no token
        return {"call": result, **calls.token_payload(row, who.actor)}

    @app.post(
        "/api/v2/calls/{call_id}/token",
        **api_v2(
            "Calls",
            "refreshCallToken",
            "A fresh token for a Tico Meet call you have joined; 409 not_tico_meet for a Google Meet",
            "CallToken",
            idempotency="Not needed: a refresh writes nothing; the header is ignored.",
        ),
    )
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
            _require_open(row)
            return calls.token_payload(row, who.actor)

    @app.post("/api/v2/calls/{call_id}/leave", **api_v2("Calls", "leaveCall", "Leave a call you joined", "Call"))
    def leave_call(request: Request, call_id: str, body: Empty):
        who = request.state.identity
        result = write(request, body, lambda c: calls.leave(c, who, call_id))
        calls.drop_from_media(call_id, who.actor)  # LiveKit drops the person now; a failure is retried by the sweep
        return result

    @app.post(
        "/api/v2/calls/{call_id}/end",
        **api_v2("Calls", "endCall", "End a call you started; its transcript is posted to the thread", "Call"),
    )
    def end_call(request: Request, call_id: str, body: Empty):
        """End a call. Tico Meet: the End is checked and stored first, so a refused one leaves the call as it was; then
        listening and the bots' voice stop at once, and the call answers `ended` when the speech already heard is
        transcribed within END_WAIT_SECONDS, else `ending`, and it ends on its own once that is in."""
        who = request.state.identity
        with store.read() as c:
            row = calls.may_end(c, who, call_id)  # refuses before any speech is sent on the call's behalf
        if row["state"] == "scheduled":
            return cancel_call(request, who, call_id, body)
        if _google(row):
            if row["state"] == "live":
                calls.end_conference(call_id)  # best effort: Google removes everyone; the call ends either way
            result = write(request, body, lambda c: calls.end(c, who, call_id))
            calls.after_end(call_id)
            return result
        result = write(request, body, lambda c: calls.end(c, who, call_id))
        if row["state"] == "live":
            calls.stop_listening(call_id)
            if calls.wind_down(call_id, who.actor):
                with store.read() as c:
                    return calls.view(c, call_id, who)
        return result

    @app.put(
        "/api/v2/calls/{call_id}/stage",
        **api_v2(
            "Calls",
            "setCallStage",
            "Show your screen or a Tico file you may read on a Tico Meet call's stage; the newest replaces what showed",
            "Call",
        ),
    )
    def set_call_stage(request: Request, call_id: str, body: CallStage):
        who = request.state.identity
        return write(request, body, lambda c: calls.set_stage(c, who, call_id, body.kind, body.file_id))

    @app.delete(
        "/api/v2/calls/{call_id}/stage",
        **api_v2(
            "Calls", "clearCallStage", "Stop showing on a call's stage: its presenter's, or the connector's", "Call"
        ),
    )
    def clear_call_stage(request: Request, call_id: str, body: Empty):
        who = request.state.identity
        return write(request, body, lambda c: calls.clear_stage(c, who, call_id))

    def cancel_call(request, who, call_id, body):
        """End a call still scheduled: its event is deleted on its calendar first (everyone invited is told), then the
        call is cancelled. Google not answering leaves both as they were."""
        gone = None
        if not calls.replayed(request, who):
            with store.read() as c:
                gone = calendar_api.delete_call_event(c, call_id)
        return write(request, body, lambda c: calls.end(c, who, call_id, gone=gone))

    @app.patch(
        "/api/v2/calls/{call_id}",
        **api_v2(
            "Calls",
            "changeCall",
            "Change a scheduled call's time: its calendar event is changed first (everyone invited is told), and the call only once the calendar took it. For whoever booked it or manages its thread",
            "Call",
        ),
    )
    def change_call(request: Request, call_id: str, body: ChangeCall):
        """Move a scheduled call. Google is written outside any transaction, before the call's write; a calendar that
        refuses leaves the call as it was. A Tico calendar's event changes in the call's write."""
        who = request.state.identity
        key = request.headers.get("idempotency-key") or ""
        entry = calls.start_lock(who.actor, "change\n" + call_id + "\n" + key)
        try:
            with entry[0]:
                plan = occ = None
                if not calls.replayed(request, who):
                    when = booking(body.starts_at, body.ends_at)
                    with store.read() as c:
                        plan = calls.change_plan(c, who, call_id, when)
                        occ = calendar_api.change_call_event(c, who, plan["event"])
                return write(request, body, lambda c: calls.reschedule(c, who, call_id, plan, occ))
        finally:
            calls.start_unlock(who.actor, "change\n" + call_id + "\n" + key, entry)

    @app.post(
        "/api/v2/calls/{call_id}/action-items/retry",
        **api_v2(
            "Calls",
            "retryCallActionItems",
            "Ask again for a call's action items the Gateway could not make; the sweep makes them",
            "Call",
        ),
    )
    def retry_call_action_items(request: Request, call_id: str, body: Empty):
        who = request.state.identity
        return write(request, body, lambda c: calls.retry_action_items(c, who, call_id))

    @app.post(
        "/api/v2/calls/{call_id}/bots",
        **api_v2("Calls", "attachCallBots", "Let bots listen to a call and reply in its thread", "Call"),
    )
    def attach_call_bots(request: Request, call_id: str, body: CallBots):
        who = request.state.identity
        return write(request, body, lambda c: calls.attach_bots(c, who, call_id, body.bots))

    @app.delete(
        "/api/v2/calls/{call_id}/bots/{bot}", **api_v2("Calls", "detachCallBot", "Take a bot out of a call", "Call")
    )
    def detach_call_bot(request: Request, call_id: str, bot: str, body: Empty):
        who = request.state.identity
        return write(request, body, lambda c: calls.detach_bot(c, who, call_id, bot))
