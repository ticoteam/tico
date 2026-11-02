"""Imports of outside meeting transcripts, through two doors and one shape.

`POST /api/v2/meetings/import` files a meeting from any source, in any of the formats
`clients/transcript_formats.py` reads (docs/meetings.md). A person files their own; a machine that
runs an importer (the local worker that reads Close is one) files one for the roster person named
in `owner_email`. That machine is the capability `backend/connectors.py` uses: a registered runner
whose operator is named in `store.settings.processing_operators`. A bot cannot import, and a
machine cannot import a meeting for somebody who is not on the roster.
`POST /api/v2/imports/transcripts` is the Close worker's own door, kept for its revisions and
call-to-meeting linking.

Either way the result is a finished meeting: segments as `turns`, the raw and readable transcript,
the notes a source supplied, and one reference per (source, external id) so that importing the
same thing again updates it instead of adding another.
"""

import asyncio
import hashlib
import json
import re
from datetime import timedelta
from typing import Literal

from fastapi import Request
from pydantic import Field, field_validator, model_validator

from clients.transcript import raw_transcript
from clients import transcript_formats as TF

from . import models as M
from . import people as P
from .auth import Identity
from .connectors import EMAIL, instant
from .media import Note, Send, attachments, authorized, deliver, form, new_note, save, upload_contract, write_upload
from .meetings import get as meeting, initial_review, announce
from .store import H, Problem, encode
from .views import roster

SOURCES = ("close",)
SOURCE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")
CONTEXT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
SECURE_URL = re.compile(r"^https://[^\s<>]{1,480}$")
MAX_TURNS = 10_000
MAX_CHARACTERS = 1_000_000


def bounded_context(value):
    """A small flat map of strings: no provider payloads, no nesting, links over HTTPS."""
    if len(value) > 20:
        raise ValueError("A source context carries at most 20 fields")
    out = {}
    for key, item in value.items():
        if not CONTEXT_KEY.fullmatch(str(key)):
            raise ValueError("Source context keys are lower-case identifiers")
        if isinstance(item, bool) or not isinstance(item, (str, int, float, type(None))):
            raise ValueError("Source context values are text")
        text = "" if item is None else str(item).strip()
        if len(text) > 500:
            raise ValueError("Source context values are at most 500 characters")
        if key.endswith("_url") and text and not SECURE_URL.fullmatch(text):
            raise ValueError("Source context links must use HTTPS")
        out[str(key)] = text
    return out


class ImportCreate(M.Contract):
    source: str = Field(min_length=1, max_length=40)
    external_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:-]+$")
    owner_email: str = Field(default="", max_length=320)
    title: str = Field(default="", max_length=300)
    started: str = Field(max_length=50)
    ended: str = Field(max_length=50)
    duration_ms: int = Field(ge=0, le=24 * 60 * 60 * 1000)
    context: dict = Field(default_factory=dict)

    @field_validator("source")
    @classmethod
    def known(cls, value):
        if value not in SOURCES:
            raise ValueError("Imports come from " + ", ".join(SOURCES))
        return value

    @field_validator("owner_email")
    @classmethod
    def address(cls, value):
        value = value.lower()
        if value and not EMAIL.fullmatch(value):
            raise ValueError("The owner of an imported meeting is an email address")
        return value

    @field_validator("started", "ended")
    @classmethod
    def moment(cls, value):
        instant(value)
        return value

    @field_validator("context")
    @classmethod
    def bounded(cls, value):
        return bounded_context(value)

    @field_validator("title")
    @classmethod
    def one_line(cls, value):
        return re.sub(r"\s+", " ", value).strip()


class ImportedTurn(M.Contract):
    text: str = Field(min_length=1, max_length=20_000)
    start_ms: int = Field(ge=0, le=24 * 60 * 60 * 1000)
    end_ms: int = Field(ge=0, le=24 * 60 * 60 * 1000)
    speaker: str = Field(default="", max_length=100)
    side: str = Field(default="", max_length=40)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_ms < self.start_ms:
            raise ValueError("A transcript turn ends before it starts")
        return self


class TranscriptImport(ImportCreate):
    resource_type: str = Field(pattern="^(call|meeting)$")
    transcript_index: int = Field(default=0, ge=0, le=9)
    source_updated_at: str = Field(max_length=50)
    summary_text: str = Field(default="", max_length=200_000)
    turns: list[ImportedTurn] = Field(min_length=1, max_length=MAX_TURNS)
    calendar_event_id: str = Field(default="", max_length=500)
    attached_call_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("source_updated_at")
    @classmethod
    def updated_moment(cls, value):
        instant(value)
        return value

    @field_validator("attached_call_ids")
    @classmethod
    def call_ids(cls, values):
        if any(not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value) for value in values):
            raise ValueError("Attached call IDs must be Close activity IDs")
        return values

    @model_validator(mode="after")
    def bounded_transcript(self):
        if sum(len(t.text) for t in self.turns) > MAX_CHARACTERS:
            raise ValueError("A transcript is at most one million characters")
        return self


class Participant(M.Contract):
    name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=320)

    @model_validator(mode="after")
    def someone(self):
        self.email = self.email.lower()
        if self.email and not EMAIL.fullmatch(self.email):
            raise ValueError("A participant's email is an address")
        if not (self.name or self.email):
            raise ValueError("A participant needs a name or an email")
        return self


class MeetingImport(M.Contract):
    """One meeting from any source. Only a transcript or notes is required (docs/meetings.md)."""
    title: str = Field(default="", max_length=300)
    started_at: str | None = Field(default=None, max_length=50)
    duration_seconds: float | None = Field(default=None, ge=0, le=24 * 60 * 60)
    participants: list[str | Participant] = Field(default_factory=list, max_length=100)
    source: str = Field(default="api", max_length=40)
    external_id: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_.:@/-]*$")
    transcript: str | list[dict] = ""
    format: str = Field(default="auto", max_length=10)
    notes: str = Field(default="", max_length=200_000)
    media_url: str = Field(default="", max_length=500)
    context: dict = Field(default_factory=dict)
    private: bool | None = None
    review: Literal["pending", "live"] | None = None
    send_to: str = Field(default="", max_length=80)
    owner_email: str = Field(default="", max_length=320)      # a machine names the roster person it files for

    @field_validator("owner_email")
    @classmethod
    def owner_address(cls, value):
        value = value.strip().lower()
        if value and not EMAIL.fullmatch(value):
            raise ValueError("owner_email is an email address")
        return value

    @field_validator("title")
    @classmethod
    def one_line(cls, value):
        return re.sub(r"\s+", " ", value).strip()

    @field_validator("source")
    @classmethod
    def lower_source(cls, value):
        value = value.strip().lower()
        if value in SOURCES:
            raise ValueError(f"{value} is filed by its own importer; use another source name")
        if not SOURCE_NAME.fullmatch(value):
            raise ValueError("source is a short lower-case name such as zoom, granola or upload")
        return value

    @field_validator("format")
    @classmethod
    def known_format(cls, value):
        if value not in TF.FORMATS:
            raise ValueError("format is one of " + ", ".join(TF.FORMATS))
        return value

    @field_validator("started_at")
    @classmethod
    def moment(cls, value):
        if value:
            try:
                instant(value)
            except ValueError:
                raise ValueError("started_at is an ISO-8601 time with a timezone") from None
        return value or None

    @field_validator("media_url")
    @classmethod
    def secure(cls, value):
        if value and not SECURE_URL.fullmatch(value):
            raise ValueError("media_url must be an https link")
        return value

    @field_validator("context")
    @classmethod
    def bounded(cls, value):
        return bounded_context(value)

    @model_validator(mode="after")
    def has_content(self):
        if isinstance(self.transcript, list):
            if len(self.transcript) > MAX_TURNS:
                raise ValueError(f"A transcript is at most {MAX_TURNS} segments")
        elif len(self.transcript) > MAX_CHARACTERS:
            raise ValueError("A transcript is at most one million characters")
        if not (self.transcript if isinstance(self.transcript, list) else self.transcript.strip()) and not self.notes.strip():
            raise ValueError("Send a transcript, notes, or both")
        return self


class SourceHeartbeat(M.Contract):
    state: str = Field(pattern="^(ok|error)$")
    pending: int = Field(default=0, ge=0, le=1_000_000)
    imported: int = Field(default=0, ge=0, le=1_000_000)
    error_code: str = Field(default="", pattern=r"^[a-z_]{0,40}$")


def transcript_meta(turns):
    """What the list and the detail read from a transcript's turns."""
    return {"turns": turns, "preview": " ".join(t["text"] for t in turns)[:160],
            "speakers": sorted({t["speaker"] for t in turns if t["speaker"]})}


def segments_of(body):
    """The importer's transcript as validated segments, or a 422 that says what is wrong."""
    if isinstance(body.transcript, str) and not body.transcript.strip():
        return []
    try:
        parsed = TF.parse(body.transcript, body.format)
        turns = [ImportedTurn.model_validate(t).model_dump(exclude={"side"}) for t in parsed]
    except (TF.TranscriptFormatError, ValueError) as exc:
        raise Problem("transcript", str(exc).splitlines()[0][:300], 422) from exc
    if sum(len(t["text"]) for t in turns) > MAX_CHARACTERS:
        raise Problem("transcript", "A transcript is at most one million characters", 422)
    return turns


def end_of(started, duration_ms, fallback):
    try:
        return (instant(started) + timedelta(milliseconds=duration_ms)).isoformat()
    except ValueError:                          # an older meeting's start had no zone
        return fallback


def listed_participants(c, values):
    """Participants as the source named them, each linked to the roster person it matches."""
    people = roster(c)
    out = []
    for value in values:
        entry = value if isinstance(value, Participant) else None
        text = "" if entry else value.strip()
        email = entry.email if entry else (text.lower() if EMAIL.fullmatch(text.lower()) else "")
        name = entry.name if entry else ("" if email else text)
        found = P.person_by_email(email, people) if email else None
        if not found and name:
            found = next((p for p in people.get("people") or []
                          if str(p.get("name") or "").strip().casefold() == name.casefold()), None)
        item = {"name": (found or {}).get("name") or name, "email": email or (found or {}).get("email") or "",
                "person_id": (found or {}).get("id")}
        if item["name"] or item["email"]:
            out.append(item)
    return out


def install_imports(app, store, auth, execution, mutate):
    def importer(c, who):
        runner = execution.runner(c, who)
        # The same operators as connectors: the configured list, else the owner alone.
        if not runner or runner["operator"] not in (store.settings.processing_operators
                                                    or (execution.auth.owner_id(c),)):
            raise Problem("forbidden", "This machine is not assigned the private app-import capability", 403)
        return runner

    def deleted(c, rid):
        row = c.execute("SELECT deleted_at FROM media_control WHERE meeting_id=?", (rid,)).fetchone()
        return bool(row and row[0])

    @app.post("/api/v2/meetings/import", openapi_extra=upload_contract(MeetingImport))
    async def meeting_import(request: Request):
        """File a meeting of the caller's (or of `owner_email`, from a machine), or update the one
        this source and id already made."""
        machine = request.state.identity
        body, uploads = await form(request, MeetingImport, app.state.blobs, store)

        def work(c):
            return file_meeting(c, machine, body, uploads)

        return await asyncio.to_thread(write_upload, store, request, body, uploads, work)

    def file_meeting(c, machine, body, uploads, *, fill_empty=False, live_client=None):
        person_call = machine.role in ("owner", "human")
        turns = segments_of(body)
        runner = None
        if person_call:
            who = machine
            if body.owner_email and body.owner_email != (who.email or "").lower():
                raise Problem("forbidden", "Only an importer machine files a meeting for someone else", 403)
        else:
            runner = importer(c, machine)
            if body.review == "live":
                raise Problem("forbidden", "Only a person may share an imported meeting", 403)
            # Filing for someone is the importer's job; handing work to a bot in their name is not.
            if body.send_to:
                raise Problem("forbidden", "An importer machine files meetings; a person sends them", 403)
            person = c.execute("SELECT id,email FROM humans WHERE email IS NOT NULL AND lower(email)=?",
                               (body.owner_email,)).fetchone() if body.owner_email else None
            if not person:
                raise Problem("not_found", "owner_email must name a person on the Tico roster", 404)
            who = Identity("human:" + person["id"], "human", person["email"])
        key = (body.source, "meeting", f"{who.actor}:{body.external_id}") if body.external_id else None
        ref = c.execute("SELECT meeting_id FROM recording_source_refs WHERE source=? AND resource_type=? "
                        "AND external_id=?", key).fetchone() if key else None
        if fill_empty and not ref and body.media_url:
            # Scope the URL fallback to this person's Granola source references, including deleted meetings.
            ref = c.execute("SELECT m.id FROM meetings m JOIN recording_source_refs r ON r.meeting_id=m.id "
                            "WHERE r.source=? AND r.resource_type='meeting' "
                            "AND substr(r.external_id,1,?)=? AND json_extract(m.metadata,'$.media_url')=? LIMIT 1",
                            (body.source, len(who.actor) + 1, who.actor + ":", body.media_url)).fetchone()
        existing = bool(ref)
        if fill_empty and ref and key:
            c.execute("INSERT INTO recording_source_refs(source,resource_type,external_id,meeting_id,created_at) "
                      "VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING", (*key, ref[0], H.now()))
        if ref and deleted(c, ref[0]):
            # Somebody threw this meeting away. Say so, so an importer stops offering it
            # rather than bringing back what a person deleted.
            return {"id": ref[0], "status": "deleted", "existing": True, "changed": False}
        if ref:
            meta = dict(authorized(c, who, ref[0], write=True)["metadata"])
        else:
            meta = new_note(c, who, Note(text=body.title or "Imported meeting"), [])
            meta.update(note="", preview="", private=body.source == "granola",
                        review_state="live" if person_call and (body.review == "live" or body.source == "manual")
                        else "pending" if body.review == "pending" else initial_review(c, who))
        rid = meta["id"]
        def notes_hash(notes):
            return hashlib.sha256(notes.encode()).hexdigest()
        if fill_empty and existing:
            stored = meeting(rid, c)
            source_notes = (body.source == "granola" and meta.get("granola_mcp_notes_hash")
                            == notes_hash(stored.get("notes") or ""))
            body = body.model_copy(update={
                "title": "" if meta.get("title") else body.title,
                "started_at": meta.get("started") or body.started_at,
                "duration_seconds": meta["duration_ms"] / 1000 if meta.get("duration_ms") else body.duration_seconds,
                "participants": [] if meta.get("participants") else body.participants,
                "notes": "" if stored.get("notes") and not source_notes else body.notes,
                "transcript": "" if meta.get("turns") else body.transcript,
                "media_url": "" if meta.get("media_url") else body.media_url,
                "private": None if "private" in meta else body.private,
                "context": meta.get("source_context") or body.context})
            turns = segments_of(body)
        people = listed_participants(c, body.participants)
        fingerprint = hashlib.sha256(encode({
            "turns": turns, "notes": body.notes, "title": body.title, "started_at": body.started_at,
            "duration": body.duration_seconds, "participants": people, "media_url": body.media_url,
            "context": body.context, "private": body.private}).encode()).hexdigest()
        known = {(a.get("name"), a.get("size")) for a in meta.get("attachments") or []}
        fresh = [u for u in uploads if (u["name"], u["size"]) not in known]
        if existing and meta.get("import_hash") == fingerprint and not fresh:
            return {"id": rid, "status": "done", "existing": True, "changed": False,
                    "review_state": meta.get("review_state", "live"),
                    "link": "#/meetings?meeting=" + rid}
        started = body.started_at or meta.get("started") or H.now()
        duration_ms = (int(body.duration_seconds * 1000) if body.duration_seconds is not None
                       else TF.duration_ms(turns) if turns else meta.get("duration_ms") or 0)
        meta.update(kind="meeting", status="done", started_at=started, ended=end_of(started, duration_ms, meta.get("ended")),
                    duration_ms=duration_ms, source=body.source, source_type="meeting",
                    source_context=body.context, transcript_provider=body.source, import_hash=fingerprint,
                    recorded_by=who.email, uploaded_by=runner["id"] if runner else who.email,
                    warning=None, error=None)
        if body.title or not existing:
            meta["title"] = body.title or meta.get("title") or "Imported meeting"
        if body.private is not None and (not existing or not meta.get("reviewed_at") or meta.get("review_state") != "live"):
            meta["private"] = body.private
        if people or not existing:
            meta.update(participants=people, confirmed_attendees=[p["email"] for p in people if p["email"]])
        if body.media_url or not existing:
            meta["media_url"] = body.media_url
        if turns:
            meta.update(transcript_meta(turns))
        elif not meta.get("preview"):
            meta["preview"] = body.notes.strip()[:160]
        if fresh:
            meta["attachments"] = (meta.get("attachments") or []) + attachments(c, who, rid, fresh)
        if body.notes:
            if fill_empty and body.source == "granola":
                # Only summaries written by this MCP path can be regenerated; hashes protect later edits.
                meta["granola_mcp_notes_hash"] = notes_hash(body.notes)
            else:
                meta.pop("granola_mcp_notes_hash", None)
        if body.send_to:
            send_to = (H.resolve_bot(c, body.send_to) or {}).get("id", body.send_to)
            auth.target(c, who, send_to, need="write")
            if meta.get("review_state") == "pending":
                meta["review_send_to"] = send_to
        transcript = raw_transcript(turns)
        if live_client is not None:
            meta["client"] = live_client
        save(c, meta, transcript=transcript or None, readable=transcript.replace("\n", "\n\n") if transcript else
             stored["transcript_readable"] if fill_empty and existing else None,
             notes=body.notes or None)
        if key and not existing:
            c.execute("INSERT INTO recording_source_refs(source,resource_type,external_id,meeting_id,created_at) "
                      "VALUES(?,?,?,?,?)", (*key, rid, H.now()))
        H.event(c, machine.actor, "meeting.imported", rid,
                {"source": body.source, "owner": who.email, "turns": len(turns), "notes": bool(body.notes),
                 "existing": existing})
        if not existing:
            announce(c, auth, rid, meta)
        result = {"id": rid, "title": meta["title"], "kind": "meeting", "status": "done", "turns": len(turns),
                  "review_state": meta.get("review_state", "live"),
                  "existing": existing, "changed": True, "link": "#/meetings?meeting=" + rid}
        if body.send_to and meta.get("review_state") == "live":
            # The same Send a person presses: the bot's task carries the transcript and notes.
            sent = deliver(c, auth, who, rid, Send(bot_id=body.send_to), [])
            result["sent"] = {"bot": sent["bot"], "task": sent.get("task")}
        return result

    app.state.import_meeting = file_meeting

    @app.post("/api/v2/imports/transcripts")
    def transcript_import(request: Request, body: TranscriptImport):
        """The Close worker's door: upsert one provider transcript revision for a roster owner."""
        def work(c):
            who = request.state.identity
            runner = importer(c, who)
            key = (body.source, body.resource_type, body.external_id)
            ref = c.execute("SELECT meeting_id FROM recording_source_refs WHERE source=? AND resource_type=? "
                            "AND external_id=?", key).fetchone()
            rid = ref[0] if ref else None
            if not rid and body.resource_type == "meeting":
                for call_id in body.attached_call_ids:
                    linked = c.execute("SELECT meeting_id FROM recording_source_refs WHERE source='close' "
                                       "AND resource_type='call' AND external_id=?", (call_id,)).fetchone()
                    if linked:
                        rid = linked[0]
                        break
            if not rid and body.calendar_event_id:
                # Only an exact event and owner, close in time, is safe to associate automatically.
                from datetime import datetime
                start = datetime.fromisoformat(body.started.replace("Z", "+00:00"))
                for candidate in c.execute("SELECT m.id,m.metadata FROM meetings m "
                                           "LEFT JOIN media_control mc ON mc.meeting_id=m.id "
                                           "WHERE lower(m.owner)=? AND mc.deleted_at IS NULL",
                                           (body.owner_email,)):
                    meta = json.loads(candidate["metadata"])
                    cal = meta.get("calendar") or {}
                    if cal.get("event_id") != body.calendar_event_id or meta.get("status") == "recording":
                        continue
                    try:
                        delta = abs((start - datetime.fromisoformat(str(meta.get("started") or "").replace("Z", "+00:00"))).total_seconds())
                    except ValueError:
                        continue
                    if delta <= 900:
                        rid = candidate["id"]
                        break
            created_new = not rid
            existing = not created_new
            if rid:
                record = meeting(rid, c)
                if deleted(c, rid):
                    return {"id": rid, "status": "deleted", "existing": True, "changed": False}
                if not record:
                    raise Problem("not_found", "The source meeting no longer exists", 404)
                meta = dict(record["metadata"])
            else:
                person = c.execute("SELECT id,email FROM humans WHERE email IS NOT NULL AND lower(email)=?",
                                   (body.owner_email or auth.settings.owner_email.lower(),)).fetchone()
                if not person:
                    raise Problem("not_found", "The owner of an imported meeting must be on the Tico roster", 404)
                owner = Identity("human:" + person["id"], "human", person["email"])
                meta = new_note(c, owner, Note(text=body.title or "Close call"), [])
                rid = meta["id"]
                meta.update(kind="meeting", title=body.title or "Close call", note="", preview="",
                            status="done", started_at=body.started, ended=body.ended,
                            duration_ms=body.duration_ms,
                            recorded_by=person["email"], uploaded_by=runner["id"], private=False,
                            review_state=initial_review(c, owner) if body.owner_email else "live",
                            source=body.source, source_type=body.resource_type,
                            source_context=body.context, warning=None, error=None)
            if not ref:
                c.execute("INSERT INTO recording_source_refs(source,resource_type,external_id,meeting_id,created_at) "
                          "VALUES(?,?,?,?,?)", (*key, rid, H.now()))
            if body.resource_type == "meeting":
                # Close explicitly associates these call activities with this meeting. Reserve
                # their references now, so a call transcript arriving later joins this record.
                for call_id in body.attached_call_ids:
                    c.execute("INSERT OR IGNORE INTO recording_source_refs"
                              "(source,resource_type,external_id,meeting_id,created_at) "
                              "VALUES('close','call',?,?,?)", (call_id, rid, H.now()))
            turns = [t.model_dump() for t in body.turns]
            transcript = raw_transcript(turns)
            content_hash = hashlib.sha256(encode({"turns": turns, "summary": body.summary_text}).encode()).hexdigest()
            latest = c.execute("SELECT id,content_hash,source_updated_at FROM recording_transcripts "
                               "WHERE source=? AND resource_type=? AND external_id=? "
                               "AND transcript_index=? ORDER BY id DESC LIMIT 1",
                               (*key, body.transcript_index)).fetchone()
            if latest and (latest["content_hash"] == content_hash or
                           instant(body.source_updated_at) < instant(latest["source_updated_at"])):
                return {"id": rid, "status": meta["status"], "existing": existing,
                        "review_state": meta.get("review_state", "live"), "changed": False, "transcript_id": latest["id"]}
            c.execute("INSERT INTO recording_transcripts(meeting_id,source,resource_type,external_id,"
                      "transcript_index,content_hash,source_updated_at,turns,transcript_text,"
                      "summary_text,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (rid, *key, body.transcript_index, content_hash, body.source_updated_at,
                       encode(turns), transcript, body.summary_text, H.now()))
            transcript_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            # Imported transcript-only meetings may follow provider corrections. A meeting that
            # was typed or recorded before Close, or already sent, keeps its established primary transcript.
            primary = created_new
            revisable = (meta.get("transcript_import_hash") and not record["delivery"]
                         and body.transcript_index == meta.get("transcript_primary_index", 0)
                         and instant(body.source_updated_at) >= instant(meta["transcript_source_updated_at"])
                         and not c.execute("SELECT 1 FROM meeting_items WHERE meeting_id=? LIMIT 1", (rid,)).fetchone()) if existing else False
            if primary or revisable:
                meta.update(status="done", **transcript_meta(turns),
                            transcript_import_hash=content_hash, transcript_provider="close",
                            transcript_primary_index=body.transcript_index,
                            transcript_source_updated_at=body.source_updated_at,
                            warning=None, error=None)
                if primary:
                    meta["source_summary"] = body.summary_text
                save(c, meta, transcript=transcript, readable=transcript.replace("\n", "\n\n"),
                     notes=body.summary_text if primary else None)
            H.event(c, who.actor, "meeting.transcript_imported", rid,
                    {"source": body.source, "resource_type": body.resource_type,
                     "external_id": body.external_id, "transcript_id": transcript_id})
            return {"id": rid, "status": meta["status"], "existing": existing,
                    "review_state": meta.get("review_state", "live"), "changed": True, "transcript_id": transcript_id}
        return mutate(request, body, work)

    @app.post("/api/v2/imports/sources/close/status")
    def close_status(request: Request, body: SourceHeartbeat):
        def work(c):
            importer(c, request.state.identity)
            old = c.execute("SELECT detail FROM service_health WHERE service='recording:close'").fetchone()
            detail = json.loads(old[0]) if old else {}
            detail.update(last_attempt=H.now(), pending=body.pending, imported=body.imported)
            if body.imported:
                detail["last_import"] = H.now()
            if body.state == "ok":
                detail["error_code"] = ""
                c.execute("INSERT INTO service_health(service,last_succeeded_at,last_error,detail) "
                          "VALUES('recording:close',?,NULL,?) ON CONFLICT(service) DO UPDATE SET "
                          "last_succeeded_at=excluded.last_succeeded_at,last_error=NULL,detail=excluded.detail",
                          (H.now(), encode(detail)))
            else:
                detail["error_code"] = body.error_code or "sync_error"
                c.execute("INSERT INTO service_health(service,last_succeeded_at,last_error,detail) "
                          "VALUES('recording:close',NULL,?,?) ON CONFLICT(service) DO UPDATE SET "
                          "last_error=excluded.last_error,detail=excluded.detail",
                          (H.now(), encode(detail)))
            return {"ok": True}
        return mutate(request, body, work)

    from .meeting_importers import install_meeting_importers
    install_meeting_importers(app, store, auth, execution, mutate, importer)
