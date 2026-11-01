"""Tico Meet calls: an audio and screen-share call that belongs to a thread (docs/meet.md).

A call is a live meeting (backend/live_meetings.py) that its thread governs. The thread's audience decides who may join
and hear it, the bots a person with Write attaches reply in the call and in the thread, and the transcript goes into
the thread when the call ends. The media runs on LiveKit (backend/call_media.py): LiveKit keeps only the room, while
Tico keeps the call's state and its transcript. A person's token grants audio and screen share, is good for five
minutes, and is refreshed only while they are still in the call and may still read the thread.

Speech reaches the transcript through the Gateway's speech-to-text, and no raw audio is kept. A call survives a Tico
restart, because its state is in the database and the sweep picks it up; if LiveKit restarts and loses the room, the
call ends with its transcript kept.

Until core's threads contract lands (docs/dev/threads-contract.md), the thread checks use auth.conversation (`READER`)
and the thread post uses hubdb.say. Both are shims: replace them in one place each when core's functions exist.
"""
import logging
import threading
import time
import uuid

from fastapi import Request
from pydantic import Field

from . import audit_log, call_media, gateway_connection, live_meetings
from .models import Contract
from .store import H, Problem
from .views import human_only

log = logging.getLogger("tico.calls")

ROOM_PREFIX = "tico-call-"
FLUSH_SECONDS = 8        # speech from one person that goes to the Gateway at once
IDLE_SECONDS = 3         # a person quiet this long has what they said sent
TRANSCRIPT_BYTES = 12_000
LIST_LIMIT = 50
PARTICIPANT_LIMIT = 200
PCM_BYTES_PER_SECOND = call_media.SAMPLE_RATE * 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls(
 id TEXT PRIMARY KEY,
 thread_id TEXT NOT NULL,
 live_meeting_id TEXT NOT NULL UNIQUE REFERENCES live_meetings(id) ON DELETE CASCADE,
 connector_actor TEXT NOT NULL,
 title TEXT NOT NULL,
 state TEXT NOT NULL CHECK (state IN ('live','ended')),
 room_name TEXT NOT NULL UNIQUE,
 end_reason TEXT,
 started_at TEXT NOT NULL,
 ended_at TEXT,
 media_seen_at TEXT,
 transcript_posted_at TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS calls_thread ON calls(thread_id, started_at);
CREATE INDEX IF NOT EXISTS calls_state ON calls(state);
CREATE TABLE IF NOT EXISTS call_participants(
 call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
 actor TEXT NOT NULL,
 joined_at TEXT NOT NULL,
 left_at TEXT,
 removed_at TEXT,
 PRIMARY KEY(call_id, actor));
"""

# (c, actor, thread_id) -> bool. The thread check until core's threads.readable lands; set by install_calls.
READER = None


def ensure(c):
    for statement in SCHEMA.split(";"):
        if statement.strip():
            c.execute(statement)
    # The thread that governs a call's live meeting. The privacy checks read it from the meeting row they already have:
    # they run inside the SQL endpoint, which may not read this table.
    if not any(row[1] == "thread_id" for row in c.execute("PRAGMA table_info(live_meetings)")):
        c.execute("ALTER TABLE live_meetings ADD COLUMN thread_id TEXT")


def readable(c, actor, thread_id):
    """Whether `actor` may read the thread, and so hear the call in it."""
    return bool(READER and READER(c, actor, thread_id))


def _auth_reader(auth):
    def reader(c, actor, thread_id):
        try:
            auth.conversation(c, auth.identity_for_actor(c, actor), thread_id)
        except Problem:
            return False
        return True
    return reader


def project_reply(c, rid, reply, who):
    """A bot's reply in a call's meeting also goes into the call's thread, as that bot's message. Registered as a
    live_meetings reply hook, so it commits with the reply: the thread gets the reply or nothing."""
    row = c.execute("SELECT id,thread_id,connector_actor FROM calls WHERE live_meeting_id=?", (rid,)).fetchone()
    if not row:
        return
    H.say(c, who.actor, row["connector_actor"], reply["text"], conversation_id=row["thread_id"], kind="say",
          refs={"call": {"id": row["id"], "meeting_id": rid},
                "live_meeting": {"meeting_id": rid, "chat_id": reply["id"]}})


class Empty(Contract):
    pass


class StartCall(Contract):
    title: str = Field(default="Call", min_length=1, max_length=200)


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


def _summary(row):
    return {"id": row["id"], "thread_id": row["thread_id"], "title": row["title"], "state": row["state"],
            "connector": row["connector_actor"], "started_at": row["started_at"], "ended_at": row["ended_at"],
            "end_reason": row["end_reason"]}


class Calls:
    """The call operations. Writes run inside the caller's transaction (`c`) and record their audit row and threads change
    together with the change; the calls that reach LiveKit or the Gateway run outside any transaction."""

    def __init__(self, app, store, auth):
        self.app, self.store, self.auth = app, store, auth
        self.buffers = {}            # (call_id, identity) -> {"pcm": bytearray, "start_ms": int, "at": float}
        self.lock = threading.Lock()

    @property
    def media(self):
        return self.app.state.call_media

    # --- reads

    def view(self, c, call_id):
        row = _call(c, call_id)
        people = [dict(r) for r in c.execute(
            "SELECT actor,joined_at,left_at,removed_at FROM call_participants WHERE call_id=? "
            "ORDER BY joined_at,actor LIMIT ?", (call_id, PARTICIPANT_LIMIT))]
        bots = [r["bot"] for r in c.execute(
            "SELECT bot FROM live_meeting_bots WHERE meeting_id=? ORDER BY bot LIMIT ?",
            (row["live_meeting_id"], PARTICIPANT_LIMIT))]
        return {"id": row["id"], "thread_id": row["thread_id"], "title": row["title"], "state": row["state"],
                "connector": row["connector_actor"], "meeting_id": row["live_meeting_id"],
                "started_at": row["started_at"], "ended_at": row["ended_at"], "end_reason": row["end_reason"],
                "participants": people, "bots": bots}

    def readable_view(self, c, who, call_id):
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        return self.view(c, call_id)

    def token_payload(self, row, actor):
        room = row["room_name"]
        return {"url": self.media.url, "expires_in": call_media.TOKEN_TTL_SECONDS,
                "token": self.media.person_token(room, actor, H.actor_id(actor))}

    # --- writes

    def _record(self, c, actor, call_id, thread_id, kind, detail=None):
        """The audit row and the threads change for one call write."""
        audit_log.write(c, actor, f"call:{call_id}", None, {"kind": kind, **(detail or {})})
        c.execute("INSERT INTO changes(topic,kind,subject_id,conversation_id,at) VALUES('threads',?,?,?,?)",
                  (kind, call_id, thread_id, H.now()))

    def _connector(self, c, who, call_id):
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        if who.actor != row["connector_actor"]:
            raise Problem("forbidden", "Only the person who started this call can end it", 403)
        return row

    def start(self, c, who, thread_id, title):
        human_only(who)
        self.auth.domain(who)
        self.auth.conversation(c, who, thread_id, write=True)
        if self.media is None:
            raise Problem("meet_unavailable", "Tico Meet is not set up on this server", 503)
        live = c.execute("SELECT id FROM calls WHERE thread_id=? AND state='live'", (thread_id,)).fetchone()
        if live:
            raise Problem("call_in_progress", "This thread already has a call going", 409,
                          extra={"call_id": live["id"]})
        meeting = live_meetings.Connect(title=title, visibility="private")
        meeting_id, call_id, stamp = str(uuid.uuid4()), str(uuid.uuid4()), H.now()
        c.execute("INSERT INTO live_meetings(id,owner_actor,title,state,client_id,window_ms,cooldown_ms,reply_cap,"
                  "threshold,created,updated,started_at,visibility,client,meeting_model,thread_id) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (meeting_id, who.actor, meeting.title, "live", None, live_meetings._window_ms(self.app, meeting),
                   meeting.cooldown_seconds * 1000, meeting.reply_cap, meeting.threshold, stamp, stamp, stamp,
                   "private", None, None, thread_id))
        c.execute("INSERT INTO live_meeting_humans VALUES(?,?,?)", (meeting_id, who.actor, stamp))
        live_meetings._event(c, meeting_id, "meeting.state", {"state": "live", "connected_by": who.actor,
                                                              "visibility": "private"})
        c.execute("INSERT INTO calls(id,thread_id,live_meeting_id,connector_actor,title,state,room_name,started_at,"
                  "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (call_id, thread_id, meeting_id, who.actor, meeting.title, "live", ROOM_PREFIX + call_id,
                   stamp, stamp, stamp))
        c.execute("INSERT INTO call_participants(call_id,actor,joined_at) VALUES(?,?,?)", (call_id, who.actor, stamp))
        self._record(c, who.actor, call_id, thread_id, "call.started", {"meeting_id": meeting_id})
        return self.view(c, call_id)

    def join(self, c, who, call_id):
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        if row["state"] != "live":
            raise Problem("call_ended", "This call has ended", 409)
        stamp = H.now()
        c.execute("INSERT INTO call_participants(call_id,actor,joined_at) VALUES(?,?,?) "
                  "ON CONFLICT(call_id,actor) DO UPDATE SET joined_at=excluded.joined_at,left_at=NULL,removed_at=NULL",
                  (call_id, who.actor, stamp))
        c.execute("INSERT OR IGNORE INTO live_meeting_humans VALUES(?,?,?)", (row["live_meeting_id"], who.actor, stamp))
        self._record(c, who.actor, call_id, row["thread_id"], "call.joined", {"actor": who.actor})
        return self.view(c, call_id)

    def leave(self, c, who, call_id):
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        participant = _participant(c, call_id, who.actor)
        if not participant:
            raise Problem("not_joined", "You are not in this call", 409)
        if participant["left_at"] is None:
            stamp = H.now()
            c.execute("UPDATE call_participants SET left_at=? WHERE call_id=? AND actor=?", (stamp, call_id, who.actor))
            self._record(c, who.actor, call_id, row["thread_id"], "call.left", {"actor": who.actor})
        return self.view(c, call_id)

    def end(self, c, who, call_id):
        row = self._connector(c, who, call_id)
        if row["state"] == "ended":
            return self.view(c, call_id)
        self.finish(c, call_id, "ended_by_connector", by=who.actor)
        return self.view(c, call_id)

    def finish(self, c, call_id, reason, by=None):
        """End a live call: its state, its people and its meeting. Returns False when it had already ended."""
        row = _call(c, call_id)
        if row["state"] == "ended":
            return False
        stamp = H.now()
        actor = by or row["connector_actor"]
        c.execute("UPDATE calls SET state='ended',end_reason=?,ended_at=?,updated_at=? WHERE id=?",
                  (reason, stamp, stamp, call_id))
        c.execute("UPDATE call_participants SET left_at=? WHERE call_id=? AND left_at IS NULL AND removed_at IS NULL",
                  (stamp, call_id))
        c.execute("UPDATE live_meetings SET state='ended',ended_at=?,updated=? WHERE id=?",
                  (stamp, stamp, row["live_meeting_id"]))
        live_meetings._event(c, row["live_meeting_id"], "meeting.state", {"state": "ended", "by": actor,
                                                                          "action": reason})
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
        if not c.execute("SELECT 1 FROM live_meeting_humans WHERE meeting_id=? AND actor=?",
                         (meeting["id"], who.actor)).fetchone():
            raise Problem("forbidden", "Join this call before attaching bots", 403)
        for slug in slugs:
            live_meetings._active_bot(c, slug)
            access = self.auth.bot_access(c, who, slug)
            if not access["see"]:
                raise Problem("forbidden", f"You cannot see org-chart bot {slug}", 403)
            if not access["write"]:
                raise Problem("forbidden", f"You can see {slug} but do not have Write access to contact it", 403)
            cursor = c.execute("INSERT OR IGNORE INTO live_meeting_bots(meeting_id,bot,joined_by,joined_at) "
                               "VALUES(?,?,?,?)", (meeting["id"], slug, who.actor, H.now()))
            if cursor.rowcount:
                live_meetings._event(c, meeting["id"], "meeting.bot_joined", {
                    "bots": [slug], "joined_by": who.actor, "rights": "meeting_read_and_reply"})
                self._record(c, who.actor, call_id, row["thread_id"], "call.bot_attached",
                             {"bot": slug, "joined_by": who.actor})
        return self.view(c, call_id)

    def detach_bot(self, c, who, call_id, slug):
        human_only(who)
        self.auth.domain(who)
        row = _call(c, call_id)
        if not readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        if who.actor != row["connector_actor"] and not c.execute(
                "SELECT 1 FROM live_meeting_bots WHERE meeting_id=? AND bot=? AND joined_by=?",
                (row["live_meeting_id"], slug, who.actor)).fetchone():
            raise Problem("forbidden", "Only the person who attached this bot can remove it", 403)
        cursor = c.execute("DELETE FROM live_meeting_bots WHERE meeting_id=? AND bot=?", (row["live_meeting_id"], slug))
        if cursor.rowcount:
            live_meetings._event(c, row["live_meeting_id"], "meeting.bot_left", {"bot": slug, "by": who.actor})
            self._record(c, who.actor, call_id, row["thread_id"], "call.bot_detached", {"bot": slug})
        return self.view(c, call_id)

    # --- media, transcript and the thread, around the writes above

    def begin_media(self, call_id):
        """Start the server's recorder in the call's room after the call is stored. Without the media server the call
        ends at once, with a refusal, so no one joins a room that will not be heard."""
        with self.store.read() as c:
            row = _call(c, call_id)
        try:
            self.media.start_recorder(call_id, row["room_name"], self._audio_for(call_id))
        except call_media.MediaUnavailable:
            with self.store.transaction() as c:
                self.finish(c, call_id, "meet_unavailable")
            raise Problem("meet_unavailable", "Tico Meet's media server could not start this call", 503)

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
        self._transcribe(call_id, identity, taken)

    def flush(self, call_id=None, *, idle=False):
        """Send the speech that is waiting: all of it for the call (or every call), or only what has been quiet for
        IDLE_SECONDS."""
        now = time.monotonic()
        with self.lock:
            keys = [key for key, buffer in self.buffers.items()
                    if (call_id is None or key[0] == call_id) and (not idle or now - buffer["at"] >= IDLE_SECONDS)]
            taken = [(key, self.buffers.pop(key)) for key in keys]
        for (cid, identity), buffer in taken:
            self._transcribe(cid, identity, buffer)

    def _transcribe(self, call_id, identity, buffer):
        pcm = bytes(buffer["pcm"])
        if not pcm:
            return
        with self.store.read() as c:
            key, base, _fingerprint = gateway_connection.target(c, self.app.state.vault, self.store.settings)
        if not key:
            log.warning("A call's speech was not transcribed: no Gateway connection is set")
            return
        try:
            text = call_media.transcribe(base, key, call_media.wav_bytes(pcm))
        except call_media.MediaUnavailable:
            return      # this clip is lost and the call goes on; the Gateway's refusal is in its own log
        if not text:
            return
        duration_ms = len(pcm) * 1000 // PCM_BYTES_PER_SECOND
        with self.store.transaction() as c:
            row = _call(c, call_id)
            meeting = live_meetings._meeting(c, row["live_meeting_id"])
            if row["state"] != "live" or meeting["state"] != "live":
                return
            previous = c.execute("SELECT COALESCE(MAX(start_ms),0) FROM live_meeting_chunks WHERE meeting_id=?",
                                 (meeting["id"],)).fetchone()[0]
            start = max(buffer["start_ms"], previous)
            live_meetings.append_chunks(c, meeting["id"], meeting, identity, [{
                "seq": meeting["seq"] + 1, "speaker": H.actor_id(identity), "start_ms": start,
                "end_ms": start + duration_ms, "text": text}])
        self.app.state.live_meeting_routing.later(meeting["id"])

    def after_end(self, call_id):
        """What an ended call still owes outside the database: its room, its last windows, its transcript in the thread."""
        with self.store.read() as c:
            row = _call(c, call_id)
        try:
            self.media.stop_recorder(call_id)
            self.media.end_room(row["room_name"])
        except call_media.MediaUnavailable:
            log.warning("A call ended, but the media server did not confirm the room closed")
        try:
            live_meetings._run_windows(self.app, row["live_meeting_id"], final=True)
        except Exception as exc:
            from .batch_work import report
            report("calls_final_windows", call_id, exc, logger="tico.calls")
        self.post_transcript(call_id)

    def post_transcript(self, call_id):
        """The call's transcript, bounded, as one message in its thread, with the meeting it came from. Once only."""
        with self.store.transaction() as c:
            row = _call(c, call_id)
            if row["transcript_posted_at"]:
                return
            lines, size, cut = [], 0, False
            for chunk in c.execute("SELECT speaker,text FROM live_meeting_chunks WHERE meeting_id=? ORDER BY seq",
                                   (row["live_meeting_id"],)):
                line = f"{chunk['speaker']}: {chunk['text']}"
                if size + len(line) > TRANSCRIPT_BYTES:
                    cut = True
                    break
                lines.append(line)
                size += len(line) + 1
            body = f"Call transcript: {row['title']}\n" + ("\n".join(lines) or "(no speech was transcribed)")
            if cut:
                body += f"\n(cut short; the full transcript is on meeting {row['live_meeting_id']})"
            H.say(c, row["connector_actor"], row["connector_actor"], body, conversation_id=row["thread_id"],
                  kind="say", refs={"call": {"id": call_id, "meeting_id": row["live_meeting_id"]},
                                    "provenance": "restricted"})
            c.execute("UPDATE calls SET transcript_posted_at=? WHERE id=?", (H.now(), call_id))

    # --- the sweep: access that ended, bots whose attacher lost Write, and a LiveKit that restarted

    def _attacher_may_write(self, c, joined_by, slug):
        try:
            who = self.auth.identity_for_actor(c, joined_by)
        except Problem:
            return False
        if who.role not in ("human", "owner"):
            return False
        try:
            return bool(self.auth.bot_access(c, who, slug)["write"])
        except Problem:
            return False

    def sweep(self):
        """Keep live calls true to their threads (the scheduler's tick). Nothing here is a request, so a call that cannot
        reach LiveKit waits for the next tick."""
        self.flush(idle=True)
        with self.store.read() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM calls WHERE state='live'")]
        for call_id in ids:
            try:
                self._sweep_call(call_id)
            except call_media.MediaUnavailable:
                continue
            except Exception as exc:
                from .batch_work import report
                report("calls_sweep", call_id, exc, logger="tico.calls")

    def _sweep_call(self, call_id):
        with self.store.read() as c:
            row = _call(c, call_id)
            people = [r["actor"] for r in c.execute(
                "SELECT actor FROM call_participants WHERE call_id=? AND left_at IS NULL AND removed_at IS NULL",
                (call_id,))]
            revoked = [actor for actor in people if not readable(c, actor, row["thread_id"])]
            stale_bots = [r["bot"] for r in c.execute(
                "SELECT bot,joined_by FROM live_meeting_bots WHERE meeting_id=?", (row["live_meeting_id"],))
                if not self._attacher_may_write(c, r["joined_by"], r["bot"])]
        room = row["room_name"]
        exists = self.media.room_exists(room)
        if not exists and row["media_seen_at"]:
            with self.store.transaction() as c:
                self.finish(c, call_id, "media_lost")
            self.after_end(call_id)
            return
        for actor in revoked:
            self.media.remove(room, actor)
        with self.store.transaction() as c:
            if exists and row["media_seen_at"] is None:
                c.execute("UPDATE calls SET media_seen_at=? WHERE id=?", (H.now(), call_id))
            for actor in revoked:
                stamp = H.now()
                cursor = c.execute("UPDATE call_participants SET removed_at=? WHERE call_id=? AND actor=? "
                                   "AND left_at IS NULL AND removed_at IS NULL", (stamp, call_id, actor))
                if cursor.rowcount:
                    self._record(c, actor, call_id, row["thread_id"], "call.participant_removed", {"actor": actor})
            for slug in stale_bots:
                c.execute("DELETE FROM live_meeting_bots WHERE meeting_id=? AND bot=?", (row["live_meeting_id"], slug))
                live_meetings._event(c, row["live_meeting_id"], "meeting.bot_left", {"bot": slug,
                                                                                    "by": "access_ended"})
                self._record(c, row["connector_actor"], call_id, row["thread_id"], "call.bot_detached",
                             {"bot": slug, "reason": "attacher_lost_write"})
        if exists and not self.media.running(call_id):
            self.media.start_recorder(call_id, room, self._audio_for(call_id))


def install_calls(app, store, auth, write):
    """The call routes and the media port. `write` is the app's idempotent write (as live meetings use). Tests set
    app.state.call_media to a fake before the first call; without the Tico Meet settings it is None and calls are off."""
    global READER
    READER = _auth_reader(auth)
    if project_reply not in live_meetings.REPLY_HOOKS:
        live_meetings.REPLY_HOOKS.append(project_reply)
    if not hasattr(app.state, "call_media"):
        config = call_media.config_from_env()
        app.state.call_media = call_media.LiveKitMedia(config) if config else None
    calls = app.state.calls = Calls(app, store, auth)

    @app.get("/api/v2/threads/{thread_id}/calls")
    def list_calls(request: Request, thread_id: str, limit: int = 25, before: str = ""):
        who = request.state.identity
        human_only(who)
        auth.domain(who)
        size = min(max(limit, 1), LIST_LIMIT)
        with store.read() as c:
            auth.conversation(c, who, thread_id)
            rows = c.execute("SELECT * FROM calls WHERE thread_id=? AND (?='' OR started_at<?) "
                             "ORDER BY started_at DESC, id LIMIT ?", (thread_id, before, before, size)).fetchall()
        calls_page = [_summary(r) for r in rows]
        return {"calls": calls_page, "next_before": calls_page[-1]["started_at"] if len(calls_page) == size else None}

    @app.post("/api/v2/threads/{thread_id}/calls")
    def start_call(request: Request, thread_id: str, body: StartCall):
        who = request.state.identity
        result = write(request, body, lambda c: calls.start(c, who, thread_id, body.title))
        calls.begin_media(result["id"])
        return result

    @app.get("/api/v2/calls/{call_id}")
    def call_detail(request: Request, call_id: str):
        who = request.state.identity
        with store.read() as c:
            return calls.readable_view(c, who, call_id)

    @app.post("/api/v2/calls/{call_id}/join")
    def join_call(request: Request, call_id: str, body: Empty):
        who = request.state.identity
        result = write(request, body, lambda c: calls.join(c, who, call_id))
        with store.read() as c:
            row = _call(c, call_id)
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
            if not participant or participant["left_at"]:
                raise Problem("not_joined", "Join the call to get a token", 409)
            if row["state"] != "live":
                raise Problem("call_ended", "This call has ended", 409)
            return calls.token_payload(row, who.actor)

    @app.post("/api/v2/calls/{call_id}/leave")
    def leave_call(request: Request, call_id: str, body: Empty):
        who = request.state.identity
        return write(request, body, lambda c: calls.leave(c, who, call_id))

    @app.post("/api/v2/calls/{call_id}/end")
    def end_call(request: Request, call_id: str, body: Empty):
        who = request.state.identity
        with store.read() as c:
            calls._connector(c, who, call_id)      # refuses before any speech is sent on the call's behalf
        calls.flush(call_id)
        result = write(request, body, lambda c: calls.end(c, who, call_id))
        calls.after_end(call_id)
        return result

    @app.post("/api/v2/calls/{call_id}/bots")
    def attach_call_bots(request: Request, call_id: str, body: CallBots):
        who = request.state.identity
        return write(request, body, lambda c: calls.attach_bots(c, who, call_id, body.bots))

    @app.delete("/api/v2/calls/{call_id}/bots/{bot}")
    def detach_call_bot(request: Request, call_id: str, bot: str, body: Empty):
        who = request.state.identity
        return write(request, body, lambda c: calls.detach_bot(c, who, call_id, bot))
