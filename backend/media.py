"""Cloud-owned meetings and notes: the list, the detail, edits, comments and Send. Never calls a model."""

import asyncio
import base64
import binascii
import json
import hashlib
import mimetypes
import secrets
import re
from email.parser import BytesParser
from email.policy import HTTP

from fastapi import Request

from pydantic import Field, ValidationError

from . import meetings as MS
from . import models as M
from . import note_outcomes
from .blobs import Blobs, brief, register
from .files import is_file_id
from clients import bot_files as BF
from . import rooms
from .store import H, P, Problem, encode, digest as request_digest
from .views import default_bot, human_only, roster


class Note(M.Contract):
    text: M.Text
    title: str = Field(default="", max_length=300)
    calendar: dict | None = None
    slug: str = Field(default="auto", max_length=200)


class Edit(M.Contract):
    title: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=200_000)
    private: bool | None = None
    version: int = Field(ge=1)


class Send(M.Contract):
    slug: str = Field(default="auto", max_length=200)
    instructions: str = Field(default="", max_length=200_000)


class TaskFile(M.Contract):
    """A deliverable a bot (or person) attaches to a task: text as-is, or bytes base64-encoded."""
    model_config = {**M.Contract.model_config, "str_strip_whitespace": False}
    name: str = Field(min_length=1, max_length=200)
    text: str | None = Field(default=None, min_length=1, max_length=2_000_000)
    content_base64: str | None = Field(default=None, min_length=1, max_length=14_000_000)
    note: str | None = Field(default=None, max_length=500)
    ask: M.ReviewAsk | None = None


def task_file_contract():
    schema = TaskFile.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(definitions[node["$ref"].rsplit("/", 1)[1]])
            return {key: inline(value) for key, value in node.items()}
        if isinstance(node, list):
            return [inline(value) for value in node]
        return node

    schema = inline(schema)
    multipart = {**schema, "required": ["file"], "properties": {
        key: value for key, value in schema["properties"].items() if key not in ("text", "content_base64", "ask")}}
    multipart["properties"].update({
        "file": {"type": "string", "format": "binary", "description": "At most TICO_UPLOAD_MAX_BYTES (2 GiB by default)."},
        "poster": {"type": "string", "format": "binary", "description": "Optional PNG or JPEG poster."},
        "ask": {"type": "string", "description": "JSON-encoded structured ask, with questions and optional who."}})
    return {"requestBody": {"required": True, "content": {
        "application/json": {"schema": schema}, "multipart/form-data": {"schema": multipart}}}}


def upload_contract(model):
    """Document the same strict fields the bounded multipart parser validates."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})
    def inline(value):
        if isinstance(value, dict):
            if value.get("$ref", "").startswith("#/$defs/"):
                return inline(definitions[value["$ref"].rsplit("/", 1)[1]])
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value
    schema = inline(schema)
    json_schema = json.loads(json.dumps(schema))
    for name in ("refs", "acceptance_criteria"):
        if name in schema["properties"]:
            schema["properties"][name] = {"type": "string", "description": "JSON-encoded " + name}
    schema["properties"]["files"] = {"type": "array", "maxItems": 10,
        "items": {"type": "string", "format": "binary"},
        "description": "At most 10 MB per file; entire request at most 20 MB. Files are untrusted, private source material."}
    return {"parameters": [{"name": "Idempotency-Key", "in": "header", "required": True,
                            "schema": {"type": "string", "minLength": 1, "maxLength": 200}}],
            "requestBody": {"required": True, "content": {
                "multipart/form-data": {"schema": schema},
                "application/json": {"schema": json_schema}}}}


def calendar(value):
    if value is None:
        return None
    allowed = {k: str(value[k])[:1000] for k in ("event_id", "title", "start", "end", "organizer") if k in value}
    attendees = value.get("attendees", [])
    if not isinstance(attendees, list) or any(not isinstance(a, str) for a in attendees):
        raise Problem("calendar", "Calendar attendees must be a list of strings", 422)
    allowed["attendees"] = [a[:300] for a in attendees[:100]]
    return allowed


def upload_key(request):
    key = request.headers.get("idempotency-key")
    if not key or len(key) > 200:
        raise Problem("idempotency_key", "Provide an Idempotency-Key of 1–200 characters", 422)
    return key


def replay_upload(store, request, payload):
    """Check the same authenticated receipt as mutate, before any durable blob write.

    Multipart retries still parse/hash their spools so a changed body gets a conflict.
    The final mutation checks again under its write lock to handle concurrent retries.
    """
    from .auth import validate_identity
    who = request.state.identity
    principal = who.actor + (":" + who.attempt_id if who.role == "bot" else "")
    key = upload_key(request)
    with store.read() as c:
        validate_identity(c, who)
        row = c.execute("SELECT request_hash,response_json FROM idempotency WHERE actor=? AND operation=? AND key=?",
                        (principal, request.url.path, key)).fetchone()
    if row is None:
        return None
    if row["request_hash"] != request_digest(encode(payload)):
        raise Problem("idempotency_conflict", "This key was used for different content", 409)
    result = json.loads(row["response_json"])
    if isinstance(result, dict) and "_refusal" in result:
        refusal = result["_refusal"]
        raise Problem(refusal["code"], refusal["detail"], refusal["status"])
    return result


async def form(request, model, blobs, store):
    upload_key(request)
    raw = await request.body()
    content_type = request.headers.get("content-type", "application/json")
    def decode():
        uploads = []
        try:
            if content_type.startswith("multipart/form-data"):
                message = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + raw)
                if not message.is_multipart() or message.defects:
                    raise ValueError("Malformed multipart body")
                fields = {}
                for index, part in enumerate(message.iter_parts()):
                    if index >= 30 or part.is_multipart() or part.defects:
                        raise ValueError("Too many fields or malformed multipart data")
                    name = part.get_param("name", header="content-disposition")
                    data = part.get_payload(decode=True) or b""
                    filename = part.get_filename()
                    if filename is not None:
                        if name not in ("files", "files[]") or len(data) > 10_000_000 or len(uploads) >= 10:
                            raise ValueError("Use at most ten files, each at most 10 MB")
                        uploads.append({"name": filename, "data": data, "content_type": part.get_content_type()})
                    elif not name or name in fields:
                        raise ValueError("Duplicate or unnamed form field")
                    else:
                        fields[name] = data.decode("utf-8")
                for field in ("calendar", "refs", "acceptance_criteria", "participants", "context",
                              "labels", "links"):
                    if field in fields:
                        fields[field] = json.loads(fields[field])
            else:
                fields = json.loads(raw or b"{}")
            body = model.model_validate(fields)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            raise Problem("validation", f"Invalid request fields or attachments ({where}: {first['msg']})", 422) from exc
        except (ValueError, UnicodeError) as exc:
            raise Problem("validation", "Invalid request fields or attachments", 422) from exc
        prepared = [{"name": upload["name"], "size": len(upload["data"]),
                     "content_type": upload["content_type"], "digest": hashlib.sha256(upload["data"]).hexdigest()}
                    for upload in uploads]
        return body, uploads, prepared
    body, uploads, prepared = await asyncio.to_thread(decode)
    if await asyncio.to_thread(replay_upload, store, request, {**body.model_dump(), "uploads": prepared}) is None:
        # Binary bytes become durable before the transaction publishes references to them.
        for upload in uploads:
            await asyncio.to_thread(blobs.put, upload["data"], upload["content_type"])
    return body, prepared


def control(c, rid):
    c.execute("INSERT OR IGNORE INTO media_control(meeting_id) VALUES(?)", (rid,))


def save(c, meta, *, transcript=None, notes=None, readable=None):
    old = MS.get(meta["id"], c)
    MS.snapshot(meta, transcript if transcript is not None else (old or {}).get("transcript_original", ""),
                notes if notes is not None else (old or {}).get("notes", ""), conn=c, readable=readable)
    control(c, meta["id"])
    # Typed notes need version history too, including intentionally empty edits.
    c.execute("INSERT OR IGNORE INTO meeting_versions(meeting_id,content_hash,metadata_json,"
              "transcript_original,transcript_readable,notes,created) "
              "SELECT id,content_hash,metadata_json,transcript_original,transcript_readable,notes,updated "
              "FROM meetings WHERE id=?", (meta["id"],))


def may_write(who, record):
    """Only the company owner and the person the source belongs to may change or send it."""
    if record.get("review_state", "live") != "live":
        return MS.filed_for(record, who)
    return who.role == "owner" or (who.role == "human" and bool(who.email)
                                   and record["owner"].lower() == who.email.lower())


def shared_meeting(record, who):
    """A company meeting is readable by everyone signed in; a private one only by its attendees.

    Typed notes are never shared: they are one person's own material. Attendees are the calendar
    snapshot's invitees and the participants an importer named; an importer can write any
    spelling, so compare without case.
    """
    meta = record["metadata"]
    if record.get("review_state", "live") != "live":
        return False
    if meta.get("kind") != "meeting":
        return False
    if not meta.get("private"):
        return True
    email = (who.email or "").lower()
    attendees = [*((meta.get("calendar") or {}).get("attendees") or []),
                 *(p.get("email", "") for p in meta.get("participants") or [] if isinstance(p, dict))]
    return bool(email) and any(str(a).strip().lower() == email for a in attendees)


def authorized(c, who, rid, *, include_deleted=False, write=False):
    record = MS.get(rid, c)
    if not record:
        raise Problem("not_found", "Note or meeting not found", 404)
    state = c.execute("SELECT * FROM media_control WHERE meeting_id=?", (rid,)).fetchone()
    if state and state["deleted_at"] and not include_deleted:
        raise Problem("not_found", "Note or meeting not found", 404)
    if record.get("review_state", "live") != "live" and not MS.filed_for(record, who):
        raise Problem("not_found", "Note or meeting not found", 404)
    if may_write(who, record):
        return record
    if not write and who.role in ("human", "owner") and shared_meeting(record, who):
        return record
    raise Problem("forbidden", "This source belongs to another person", 403)


def view(c, who, rid, *, include_transcripts=False):
    record = authorized(c, who, rid)
    meta = record["metadata"]
    state = c.execute("SELECT * FROM media_control WHERE meeting_id=?", (rid,)).fetchone()
    delivery = record["delivery"]
    if delivery and delivery["task_id"]:
        meta["sent"] = {"task": delivery["task_id"], "slug": delivery["destination"], "sent_by": delivery["requested_by"]}
    result = {**meta, "notes": record["notes"], "transcript_readable": record["transcript_readable"],
              "meeting_context": MS.context(record), "delivery": delivery,
              "version": state["version"] if state else 1, "turns": meta.get("turns", []),
              "can_edit": may_write(who, record)}
    result["outcome"] = note_outcomes.outcome(c, result, who.actor, who.role == "owner")
    result["outbox"] = outbox(c, rid)
    if include_transcripts:
        result["transcript_sources"] = [{"id": row["id"], "source": row["source"],
            "resource_type": row["resource_type"], "transcript_index": row["transcript_index"],
            "source_updated_at": row["source_updated_at"], "text": row["transcript_text"],
            "turns": json.loads(row["turns_json"]),
            "summary": row["summary_text"],
            "primary": row["content_hash"] == meta.get("transcript_import_hash")}
            for row in c.execute("SELECT t.* FROM recording_transcripts t WHERE t.meeting_id=? "
                                 "AND NOT EXISTS(SELECT 1 FROM recording_transcripts newer "
                                 "WHERE newer.source=t.source AND newer.resource_type=t.resource_type "
                                 "AND newer.external_id=t.external_id AND newer.transcript_index=t.transcript_index "
                                 "AND newer.id>t.id) ORDER BY t.id", (rid,))]
    return result


def outbox(c, rid):
    """Doc updates, tasks and feature requests still on this meeting — the structured outcomes."""
    groups = {"doc": [], "task": [], "feature": []}
    try:
        rows = c.execute("SELECT id,section,text,status,result_ref FROM meeting_items WHERE meeting_id=? "
                         "AND status!='dismissed' AND section IN ('doc','task','feature') "
                         "ORDER BY created", (rid,)).fetchall()
    except Exception:
        return groups
    for row in rows:
        groups[row["section"]].append({"id": row["id"], "text": row["text"], "status": row["status"],
                                       "result_ref": row["result_ref"]})
    return groups


def attachments(c, who, rid, uploads):
    out = []
    for upload in uploads:
        item = register(c, who, **upload)
        c.execute("INSERT INTO media_assets(meeting_id,blob_id,kind) VALUES(?,?,'attachment')", (rid, item["id"]))
        out.append(item)
    return out


def new_note(c, who, body, uploads):
    if not who.email:
        raise Problem("identity", "An authenticated email is required", 403)
    rid = H.parse_ts(H.now()).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    while c.execute("SELECT 1 FROM meetings WHERE id=?", (rid,)).fetchone():
        rid = H.parse_ts(H.now()).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    meta = {"id": rid, "kind": "note", "title": body.title or body.text.splitlines()[0][:200],
            "owner": who.email, "uploaded_by": who.email, "started": H.now(), "ended": H.now(),
            "duration_ms": 0, "note": body.text, "preview": body.text[:160], "status": "done",
            "sent": None, "speakers": [], "attachments": [], "calendar": calendar(body.calendar),
            "private": False, "warning": None, "error": None}
    save(c, meta)
    meta["attachments"] = attachments(c, who, rid, uploads)
    save(c, meta)
    H.event(c, who.actor, "note.created", rid)
    return meta


def require_live(record):
    if record.get("review_state", "live") != "live":
        raise Problem("meeting_review", "Share this meeting before sending it to a teammate", 409)


def deliver(c, auth, who, rid, body, uploads):
    record = authorized(c, who, rid, write=True)
    require_live(record)
    meta = record["metadata"]
    person = P.person(H.actor_id(who.actor), roster(c)) or {}
    slug = body.slug if body.slug != "auto" else person.get("bot") or default_bot(c, auth.settings)
    auth.target(c, who, slug, need="write")
    previous = record["delivery"]
    if previous:
        if previous["destination"] != slug or previous["instructions"] != body.instructions or uploads:
            raise Problem("delivery_conflict", "This source already has a delivery request; refresh to inspect it", 409)
        if previous["status"] == "error" and not previous["task_id"]:
            if previous["requested_by"].lower() != who.email.lower():
                raise Problem("delivery_sender", "The original sender must retry this delivery", 409)
            c.execute("UPDATE meeting_deliveries SET status='pending',error=NULL WHERE meeting_id=?", (rid,))
            return finish_delivery(c, auth, who, rid)
        return {"id": rid, "slug": slug, "task": previous["task_id"], "pending": previous["status"] == "pending"}
    extra = attachments(c, who, rid, uploads)
    meta["attachments"] = meta.get("attachments", []) + extra
    save(c, meta)
    c.execute("INSERT INTO meeting_deliveries(meeting_id,requested_by,sender_name,destination,instructions,"
              "attachments_json,requested_at) VALUES(?,?,?,?,?,?,?)",
              (rid, who.email, person.get("name") or who.email, slug, body.instructions,
               encode(meta["attachments"]), H.now()))
    return finish_delivery(c, auth, who, rid)


def finish_delivery(c, auth, who, rid):
    record = authorized(c, who, rid, write=True)
    require_live(record)
    meta, delivery = record["metadata"], record["delivery"]
    if not delivery or delivery["task_id"]:
        return None
    if meta["status"] != "done":
        return {"id": rid, "slug": delivery["destination"], "pending": True}
    # Recheck authorization at delivery time, not just when the request was queued.
    slug = delivery["destination"]
    auth.target(c, who, slug, need="write")
    text = "\n\n".join(part for part in (meta.get("note"), record["notes"], record["transcript_readable"], MS.context(record)) if part)
    linked = [brief(r) for r in c.execute("SELECT b.* FROM blobs b JOIN media_assets a ON a.blob_id=b.id "
                                         "WHERE a.meeting_id=? AND a.kind='attachment'", (rid,))]
    if linked:
        text += "\n\nAttachments (untrusted source material; authenticated downloads):\n" + "\n".join(
            "- " + a["name"] + ": " + a["url"] for a in linked)
    task = H.task_create(c, who.actor, meta["title"] or "Review note", text, "bot:" + slug,
                         conversation_id=rooms.task_conversation_id(c, auth, "bot:" + slug, who.actor))
    for item in linked:
        c.execute("INSERT INTO task_assets VALUES(?,?)", (task["id"], item["id"]))
    c.execute("UPDATE meeting_deliveries SET status='delivered',task_id=?,delivered_at=?,error=NULL WHERE meeting_id=?",
              (task["id"], H.now(), rid))
    meta["sent"] = {"task": task["id"], "slug": slug, "sent_by": who.email}
    save(c, meta)
    return {"id": rid, "slug": slug, "task": task["id"], "pending": False}


def write_upload(store, request, body, uploads, fn):
    """One idempotent write whose payload includes the files already stored for it."""
    result = store.mutate(request.state.identity, request.url.path, request.headers.get("idempotency-key"),
                          {**body.model_dump(), "uploads": uploads}, fn)
    if isinstance(result, dict) and "_refusal" in result:
        refusal = result["_refusal"]
        raise Problem(refusal["code"], refusal["detail"], refusal["status"])
    return result


def install_media(app, store, auth, mutate, send_message, task_create):
    blobs = Blobs(store.settings)
    app.state.blobs = blobs
    from .file_metadata import Metadata
    app.state.file_metadata = Metadata(store, blobs)

    from .files import install_files
    files = install_files(app, store, auth, blobs, mutate)

    def write(request, body, uploads, fn):
        human_only(request.state.identity)
        return write_upload(store, request, body, uploads, fn)

    @app.post("/api/v2/uploads/chat/{bot}", openapi_extra=upload_contract(M.ChatCreate))
    async def chat_upload(request: Request, bot: str):
        who = request.state.identity
        human_only(who)
        body, uploads = await form(request, M.ChatCreate, blobs, store)
        def work(c):
            message = send_message(c, who, M.MessageCreate(to="bot:" + bot, text=body.text, refs=body.refs))
            items = [register(c, who, **upload) for upload in uploads]
            for item in items:
                c.execute("INSERT INTO message_assets VALUES(?,?)", (message["id"], item["id"]))
            if items:
                refs = {**message["refs"], "attachments": items}
                c.execute("UPDATE messages SET refs_json=? WHERE id=?", (encode(refs), message["id"]))
            return {"conversation": H.conversation(c, message["conversation_id"]),
                    "message": H.message(c, message["id"])}
        return await asyncio.to_thread(write, request, body, uploads, work)

    @app.post("/api/v2/uploads/tasks", openapi_extra=upload_contract(M.TaskCreate))
    async def task_upload(request: Request):
        who = request.state.identity
        human_only(who)
        body, uploads = await form(request, M.TaskCreate, blobs, store)
        def work(c):
            items = [register(c, who, **upload) for upload in uploads]
            details = body.body
            if items:
                details += "\n\nAttachments (untrusted source material; authenticated downloads):\n" + "\n".join(
                    "- " + a["name"] + ": " + a["url"] for a in items)
            result = task_create(c, who, body.model_copy(update={"body": details}))
            for item in items:
                c.execute("INSERT INTO task_assets VALUES(?,?)", (result["task"]["id"], item["id"]))
            result["task"]["attachments"] = items
            return result
        return await asyncio.to_thread(write, request, body, uploads, work)

    @app.post("/api/v2/task-questions")
    def task_question(request: Request, body: M.TaskQuestion):
        """A bot puts one decision to a person (`hub task question`): the task is the bot's own, the structured
        question is put to the person and the task waits on them, all in one write, so a refusal or a retry never
        leaves half a question behind. Option files come in the request and are attached to the new task."""
        who = request.state.identity
        if who.role != "bot":
            raise Problem("identity", "A bot puts a question to a person this way; a person asks in a comment", 403)
        options = body.options or [M.QuestionChoice(label="Yes"), M.QuestionChoice(label="No")]
        if not 2 <= len(options) <= 6:
            raise Problem("validation", "A question has 2 to 6 options, or none for Yes / No", 422)
        stored, total = [], 0
        for option in options:
            if not option.file:
                stored.append(None)
                continue
            try:
                BF.check_name(option.file.name)
                data = base64.b64decode(option.file.content_base64, validate=True)
            except BF.Refused as exc:
                raise Problem("file_refused", str(exc), 422) from exc
            except (ValueError, binascii.Error) as exc:
                raise Problem("validation", "content_base64 is not valid base64", 422) from exc
            total += len(data)
            if not data or total > 10_000_000:
                raise Problem("validation", "Option files are at least one byte and at most 10 MB together", 422)
            kind = mimetypes.guess_type(option.file.name)[0] or "application/octet-stream"
            stored.append((blobs.put(data, kind), len(data), option.file.name, kind))
        question = body.question.strip()

        def work(c):
            person = H.resolve_actor(c, body.on)
            if not person or not H.is_human(person) or not H.human(c, H.actor_id(person)):
                raise Problem("validation", f"{body.on} is not a person on the team", 422)
            made = task_create(c, who, M.TaskCreate(
                title=body.title or question, body=body.body or question, owner=who.actor, private=body.private,
                relations=[M.TaskRelationRef(task=body.parent, kind="parent")] if body.parent else []))
            tid = made["task"]["id"]
            if not H.task_private_readable(c, person, H.task(c, tid)):
                raise Problem("privacy", f"This task would be private and {H.actor_id(person)} could not read it: "
                              "ask on the private task you are working on (hub task comment --ask), or without --private", 403)
            choices = []
            for option, item in zip(options, stored):
                choice = {"label": option.label, **({"description": option.description} if option.description else {})}
                if item:
                    blob = register(c, who, item[0], item[1], item[2], item[3])
                    fid, number = files.attach_task(c, who, tid, blob, item[0], H.actor_id(who.actor))
                    c.execute("INSERT INTO task_assets VALUES(?,?)", (tid, blob["id"]))
                    c.execute("INSERT INTO blob_media(blob_id,poster_blob_id) VALUES(?,NULL)", (blob["id"],))
                    H._task_event(c, tid, who.actor, "file", None, blob["name"], "")
                    choice["file"] = f"{fid}@{number}"
                choices.append(choice)
            from .task_review import check_ask
            ask = check_ask(c, tid, M.ReviewAsk(questions=[M.ReviewQuestion(
                id="decision", header="", question=question, options=choices)], who=person), auth, who)
            said = H.task_comment(c, who.actor, tid, question, ask=ask)
            H.task_update(c, who.actor, tid, status="waiting", waiting_on=person, note=question)
            return {"task": H.task(c, tid),
                    "question": next(m for m in H.task_comments(c, tid, actor=who.actor) if m["id"] == said["id"])}
        result = mutate(request, body, work)
        app.state.file_metadata.wake.set()
        return result

    @app.get("/api/v2/tasks/{tid}/files")
    def task_files(request: Request, tid: str):
        with store.read() as c:
            task_id = auth.resolve_task(c, request.state.identity, tid)
            return files.task_listing(c, request.state.identity, task_id)

    @app.post("/api/v2/tasks/{tid}/files", openapi_extra=task_file_contract())
    async def task_attach(request: Request, tid: str):
        who = request.state.identity
        from .task_review import comment_rights, edit_version
        with store.read() as c:
            tid = auth.resolve_task(c, who, tid)
            comment_rights(c, auth, who, tid)
        upload_key(request)
        poster = None
        if request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            from .file_upload import parse
            fields, uploads, stack = await parse(request, store.settings.upload_max_bytes, blobs.directory)
            try:
                upload = uploads["file"]
                fields.setdefault("name", upload["filename"])
                if "ask" in fields:
                    fields["ask"] = json.loads(fields["ask"])
                body = TaskFile.model_validate(fields)
                supplied_type = upload["mime"].lower().split(";", 1)[0].strip()
                content_type = mimetypes.guess_type(body.name)[0] or (supplied_type if re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", supplied_type) else "application/octet-stream")
                size = upload["size"]
                if who.role == "bot":
                    try:
                        BF.check_name(body.name)
                    except BF.Refused as exc:
                        raise Problem("file_refused", str(exc), 422) from exc
                digest = upload["digest"]
                if "poster" in uploads:
                    supplied = uploads["poster"]
                    from .file_metadata import validate_poster
                    poster_type = await asyncio.to_thread(validate_poster, supplied["stream"])
                    poster = {"digest": supplied["digest"], "size": supplied["size"], "name": "poster", "content_type": poster_type}
                receipt = {**body.model_dump(), "digest": digest, "size": size, "poster": poster}
                replay = await asyncio.to_thread(replay_upload, store, request, {**body.model_dump(), "uploads": [receipt]})
                if replay is not None:
                    return replay
                await asyncio.to_thread(blobs.put_staged, upload["stream"], digest, size, content_type)
                if poster:
                    await asyncio.to_thread(blobs.put_staged, supplied["stream"], poster["digest"], supplied["size"], poster_type)
            except (ValueError, ValidationError) as exc:
                raise Problem("validation", "Invalid upload name, note or ask JSON", 422) from exc
            finally:
                stack.close()
        else:
            try:
                body = TaskFile.model_validate(await request.json())
            except (ValueError, ValidationError) as exc:
                raise Problem("validation", "Invalid task file fields", 422) from exc
            if (body.text is None) == (body.content_base64 is None):
                raise Problem("validation", "Send exactly one of text or content_base64", 422)
            if body.text is not None:
                data = body.text.encode("utf-8")
            else:
                try:
                    data = base64.b64decode(body.content_base64, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise Problem("validation", "content_base64 is not valid base64", 422) from exc
            size = len(data)
            if not data or size > 10_000_000:
                raise Problem("validation", "A file is at least one byte and at most 10 MB", 422)
            content_type = mimetypes.guess_type(body.name)[0] or ("text/plain" if body.text is not None else "application/octet-stream")
            if who.role == "bot":
                try:
                    BF.check_name(body.name)
                except BF.Refused as exc:
                    raise Problem("file_refused", str(exc), 422) from exc
            payload = body.model_dump()
            if not body.note and body.ask is None:
                payload.pop("note")
                payload.pop("ask")
            replay = await asyncio.to_thread(replay_upload, store, request, payload)
            if replay is not None:
                return replay
            digest = await asyncio.to_thread(blobs.put, data, content_type)
        def work(c):
            task = comment_rights(c, auth, who, tid)
            item = register(c, who, digest, size, body.name, content_type)
            bot_actor = next((a for a in (who.actor, task["owner"], task["requester"]) if H.is_bot(a)), None)
            fid, number = files.attach_task(c, who, tid, item, digest,
                                           H.actor_id(bot_actor) if bot_actor else default_bot(c, store.settings))
            c.execute("INSERT INTO task_assets VALUES(?,?)", (tid, item["id"]))
            if body.note is not None or body.ask is not None:
                edit_version(c, auth, who, tid, fid, number,
                             M.FileVersionEdit(note=body.note, ask=body.ask))
            c.execute("UPDATE tasks SET updated=? WHERE id=?", (H.now(), tid))
            # The task's own activity says a file was added, as it says a link was.
            H._task_event(c, tid, who.actor, "file", None, item["name"], "" if number == 1 else f"version {number}")
            H.event(c, who.actor, "task.file", tid, {"file": item["id"], "name": item["name"], "size": item["size"]})
            preview = register(c, who, **poster)["id"] if poster else None
            c.execute("INSERT INTO blob_media(blob_id,poster_blob_id) VALUES(?,?)", (item["id"], preview))
            if preview:
                c.execute("UPDATE bot_file_versions SET poster_blob_id=? WHERE file_id=? AND version=?",
                          (preview, fid, number))
            return {"file": {**item, "file_id": fid, "version": number}, "file_id": fid, "version": number,
                    "link": store.settings.public_url + f"/api/v2/files/{fid}?v={number}"}
        # Include byte identity in the receipt without storing multipart bytes.
        receipt = {**body.model_dump(), "digest": digest, "size": size, "poster": poster}
        if request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            result = await asyncio.to_thread(write_upload, store, request, body, [receipt], work)
        else:
            # Preserve the pre-upgrade hash when an older runner replays its JSON receipt.
            result = await asyncio.to_thread(store.mutate, who, request.url.path,
                                             request.headers.get("idempotency-key"), payload, work)
            if isinstance(result, dict) and "_refusal" in result:
                refusal = result["_refusal"]
                raise Problem(refusal["code"], refusal["detail"], refusal["status"])
        app.state.file_metadata.wake.set()
        return result

    @app.get("/api/meetings/sources")
    def sources(request: Request):
        human_only(request.state.identity)
        with store.read() as c:
            row = c.execute("SELECT last_success,last_error,detail_json FROM service_health "
                            "WHERE service='recording:close'").fetchone()
            detail = json.loads(row["detail_json"] or "{}") if row else {}
            if not row:
                status = "needs_setup"
            elif row["last_error"] and (not row["last_success"] or row["last_error"] >= row["last_success"]):
                status = "error"
            elif row["last_success"] and row["last_success"] >= H.shift(H.now(), minutes=-15):
                status = "syncing"
            else:
                status = "delayed"
            extra = []
            from .meeting_importers import IMPORTERS, status_of
            for r in c.execute("SELECT source FROM meeting_importers WHERE enabled=1"):
                if r["source"] not in IMPORTERS:
                    continue
                health = c.execute("SELECT last_success,last_error,detail_json FROM service_health WHERE service=?",
                                   ("recording:" + r["source"],)).fetchone()
                detail = json.loads(health["detail_json"] or "{}") if health else {}
                state = status_of(True, health, IMPORTERS[r["source"]]["interval"], H.now())
                extra.append({"id": r["source"], "name": IMPORTERS[r["source"]]["name"],
                              "status": "needs_setup" if state == "waiting" else state,
                              "last_success": health["last_success"] if health else None,
                              "last_attempt": detail.get("last_attempt"), "last_import": detail.get("last_import"),
                              "pending": 0, "imported": detail.get("imported_total", 0),
                              "error_code": detail.get("error_code", "")})
            return {"sources": [
                {"id": "import", "name": "Import", "status": "available", "last_success": None},
                {"id": "close", "name": "Close", "status": status,
                 "last_success": row["last_success"] if row else None,
                 "last_attempt": detail.get("last_attempt"), "last_import": detail.get("last_import"),
                 "pending": detail.get("pending", 0),
                 "imported": detail.get("imported", 0), "error_code": detail.get("error_code", "")},
            ] + extra}

    @app.get("/api/meetings")
    def listing(request: Request, q: str = "", review: str = "live"):
        who = request.state.identity
        human_only(who)
        if review not in ("live", "pending", "dismissed"):
            raise Problem("review", "review is pending, live or dismissed", 422)
        with store.read() as c:
            rows = []
            for r in c.execute("SELECT id FROM meetings ORDER BY created DESC"):
                try:
                    item = view(c, who, r[0])
                except Problem:
                    continue
                if item["review_state"] != review:
                    continue
                if q and q.lower() not in encode(item).lower():
                    continue
                rows.append(item)
            return rows

    @app.get("/api/meetings/{rid}")
    def read(request: Request, rid: str):
        with store.read() as c:
            return view(c, request.state.identity, rid, include_transcripts=True)

    @app.get("/api/meetings/{rid}/versions")
    def versions(request: Request, rid: str):
        with store.read() as c:
            authorized(c, request.state.identity, rid)
            return {"versions": [{**dict(row), "metadata": json.loads(row["metadata_json"])}
                                 for row in c.execute("SELECT * FROM meeting_versions WHERE meeting_id=? ORDER BY id DESC", (rid,))]}

    @app.post("/api/notes")
    @app.post("/api/send")
    async def note(request: Request):
        human_only(request.state.identity)
        body, uploads = await form(request, Note, blobs, store)
        def work(c):
            meta = new_note(c, request.state.identity, body, uploads)
            if request.url.path == "/api/send":
                return deliver(c, auth, request.state.identity, meta["id"], Send(slug=body.slug), [])
            return view(c, request.state.identity, meta["id"])
        return await asyncio.to_thread(write, request, body, uploads, work)

    @app.post("/api/meetings/{rid}/attachments", openapi_extra=upload_contract(M.Empty))
    async def attach(request: Request, rid: str):
        human_only(request.state.identity)
        with store.read() as c:
            authorized(c, request.state.identity, rid, write=True)
        body, uploads = await form(request, M.Empty, blobs, store)
        if not uploads:
            raise Problem("attachments", "Attach at least one file", 422)
        def work(c):
            meta = authorized(c, request.state.identity, rid, write=True)["metadata"]
            meta["attachments"] = meta.get("attachments", []) + attachments(
                c, request.state.identity, rid, uploads)
            save(c, meta)
            return view(c, request.state.identity, rid)
        return await asyncio.to_thread(write, request, body, uploads, work)

    @app.post("/api/meetings/{rid}/edit")
    def edit(request: Request, rid: str, body: Edit):
        def work(c):
            meta = authorized(c, request.state.identity, rid, write=True)["metadata"]
            control(c, rid)
            version = c.execute("SELECT version FROM media_control WHERE meeting_id=?", (rid,)).fetchone()[0]
            if body.version != version:
                raise Problem("version_conflict", "This note changed; reload before editing", 409)
            if body.title is not None:
                meta["title"] = body.title
            if body.note is not None:
                meta["note"] = body.note
                meta["preview"] = body.note[:160]
            if body.private is not None:
                meta["private"] = bool(body.private)
            save(c, meta)
            c.execute("UPDATE media_control SET version=version+1 WHERE meeting_id=?", (rid,))
            return view(c, request.state.identity, rid)
        return mutate(request, body, work)

    @app.post("/api/meetings/{rid}/send")
    async def send(request: Request, rid: str):
        human_only(request.state.identity)
        with store.read() as c:
            authorized(c, request.state.identity, rid, write=True)
        body, uploads = await form(request, Send, blobs, store)
        return await asyncio.to_thread(write, request, body, uploads,
                                       lambda c: deliver(c, auth, request.state.identity, rid, body, uploads))

    @app.post("/api/v2/meetings/{rid}/delete")
    @app.post("/api/meetings/{rid}/delete")
    def delete(request: Request, rid: str, body: M.Empty):
        def work(c):
            authorized(c, request.state.identity, rid, include_deleted=True, write=True)
            control(c, rid)
            c.execute("UPDATE media_control SET deleted_at=coalesce(deleted_at,?),version=version+1 WHERE meeting_id=?", (H.now(), rid))
            H.event(c, request.state.identity.actor, "note.deleted", rid, {"recoverable": True})
            return {"ok": True, "recoverable": True}
        return mutate(request, body, work)

    def readable_blob(c, who, bid):
        """The blob row if `who` may read it (its owner, or anyone who may see a message or task it
        is attached to); refuses otherwise. Shared by the download and its metadata."""
        row = c.execute("SELECT * FROM blobs WHERE id=?", (bid,)).fetchone()
        if not row:
            raise Problem("not_found", "File not found", 404)
        from . import task_privacy as privacy
        if not privacy.blob_readable(c, privacy.actor(who), bid):
            raise Problem("not_found", "File not found", 404)
        # Ownership of the company account is not ordinary access to another person's
        # private Tico attachment. Shared-room and task access is derived from the linked
        # object below, using the same authorization as its conversation or task.
        allowed = who.role in ("owner", "human") and row["owner"] == who.actor
        if not allowed and who.role in ("owner", "human", "bot"):
            for linked in c.execute("SELECT m.* FROM message_assets a JOIN messages m ON m.id=a.message_id WHERE a.blob_id=?", (bid,)):
                try:
                    auth.conversation(c, who, linked["conversation_id"])
                    privacy.require_message(c, who, dict(linked))
                    allowed = True
                    break
                except Problem:
                    pass
            for linked in c.execute("SELECT task_id FROM task_assets WHERE blob_id=?", (bid,)):
                try:
                    auth.task(c, who, linked[0])
                    allowed = True
                    break
                except Problem:
                    pass
        if not allowed:
            raise Problem("forbidden", "You cannot access this file", 403)
        return row

    @app.get("/api/v2/files/{bid}/meta")
    def file_meta(request: Request, bid: str, v: int | None = None):
        """Name, size and type without the bytes (the viewer decides a
        thumbnail or a type mark from this, not by downloading the file)."""
        with store.read() as c:
            stored = c.execute("SELECT 1 FROM bot_files WHERE id=?", (bid,)).fetchone()
        if is_file_id(bid) or stored:
            try:
                return files.serve(request.state.identity, bid, v, meta=True)
            except Problem as exc:
                if is_file_id(bid) or v not in (None, 1) or exc.status not in (403, 404):
                    raise
        if v not in (None, 1):
            raise Problem("not_found", "File version not found", 404)
        with store.read() as c:
            row = readable_blob(c, request.state.identity, bid)
            media = c.execute("SELECT * FROM blob_media WHERE blob_id=?", (bid,)).fetchone()
            return {"id": row["id"], "name": row["name"], "size": row["size"], "content_type": row["content_type"],
                    **({k: media[k] for k in ("width", "height", "duration_ms", "poster_blob_id", "thumb_blob_id", "media_state")} if media else {})}

    def resolve_blob(c, who, bid, v=None, derivative=None):
        row = readable_blob(c, who, bid)
        version = c.execute("SELECT * FROM bot_file_versions WHERE blob_id=? AND (? IS NULL OR version=?)", (bid, v, v)).fetchone()
        if v is not None and not version and v != 1:
            raise Problem("not_found", "File version not found", 404)
        if derivative:
            media = version or c.execute("SELECT * FROM blob_media WHERE blob_id=?", (bid,)).fetchone()
            preview = media[derivative + "_blob_id"] if media else None
            row = c.execute("SELECT * FROM blobs WHERE id=?", (preview,)).fetchone() if preview else None
            if not row:
                raise Problem("not_found", "File preview not found", 404)
        return dict(row)

    @app.head("/api/v2/files/{bid}/poster", include_in_schema=False)
    @app.get("/api/v2/files/{bid}/poster")
    def poster(request: Request, bid: str, v: int | None = None):
        return download_blob(request, bid, v, "poster")

    @app.head("/api/v2/files/{bid}/thumb", include_in_schema=False)
    @app.get("/api/v2/files/{bid}/thumb")
    def thumb(request: Request, bid: str, v: int | None = None):
        return download_blob(request, bid, v, "thumb")

    @app.head("/api/v2/files/{bid}", include_in_schema=False)
    @app.get("/api/v2/files/{bid}")
    def download(request: Request, bid: str, v: int | None = None):
        return download_blob(request, bid, v)

    def download_blob(request, bid, v=None, derivative=None):
        who = request.state.identity
        with store.read() as c:
            stored = c.execute("SELECT 1 FROM bot_files WHERE id=?", (bid,)).fetchone()
        if is_file_id(bid) or stored:
            try:
                return files.serve(who, bid, v, request=request, derivative=derivative)
            except Problem as exc:
                if is_file_id(bid) or v not in (None, 1) or exc.status not in (403, 404):
                    raise
        if v not in (None, 1):
            raise Problem("not_found", "File version not found", 404)
        with store.read() as c:
            row = resolve_blob(c, who, bid, v, derivative)
        from .file_delivery import serve
        return serve(request, blobs, row, v is not None)
