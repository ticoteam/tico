# pyright: strict
"""Evidence attached to a stage requirement on a ticket: a company file, an uploaded blob or a link, recorded with the
digest and work version it was attached to. Track B owns this module.

An attachment is never edited. A push or a new file version makes it out of date rather than changing it: a file
attached at an older version than the file's current one is refused, and an attachment whose work version is behind the
ticket's makes its decision stale (the engine reads that, not this module). Check runs and receipts are written by the
engine (Track A), not attached by a person or a bot, so the API takes only files, blobs and links.

The privacy rule: the requirement's reviewer must be able to read the ticket, or the attach is refused. With no reviewer
(`nobody`), the person or bot attaching must be able to read it. The file, blob or page attached must be readable by the
caller and by the reviewer too, so evidence from a private ticket never reaches a reviewer who cannot open that ticket.
Attaching is a change to the ticket: it takes the rights a change takes (`_may_edit_ticket`), not just read.
"""

import hashlib
import json
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from fastapi import Request
from pydantic import Field

from . import hubdb
from . import pipeline_requirements as R
from . import pipeline_rights
from . import task_privacy as privacy
from .models import Contract
from .store import Problem

# hubdb is untyped: its functions are Any here (as in stranded_resume.py), not partially unknown.
H: Any = hubdb
PRIVACY: Any = privacy

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store

SOURCES = ("file", "blob", "link")
URL_MAX = 2000
ATTACH_LIMIT = 20  # the newest attachments a ticket shows against one requirement
MIME_FORMATS = {"image/": "image", "video/": "video"}


class AttachBody(Contract):
    """One piece of evidence for a requirement on a ticket. `file_version` pins a company file's version; left out, it is
    the file's current one."""

    source: Literal["file", "blob", "link"]
    file_id: str | None = Field(default=None, max_length=128)
    file_version: int | None = Field(default=None, ge=1, le=100000)
    blob_id: str | None = Field(default=None, max_length=128)
    url: str | None = Field(default=None, max_length=URL_MAX)


def attachments_for(
    conn: "sqlite3.Connection", task_id: str, stage_requirement_id: str, limit: int | None = None
) -> list[dict[str, Any]]:
    """The attachments a ticket has against one requirement, newest first. Removed ones are left out; `limit` caps them."""
    rows = conn.execute(
        "SELECT * FROM requirement_attachments WHERE task_id=? AND stage_requirement_id=? AND removed_at IS NULL "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (task_id, stage_requirement_id, -1 if limit is None else limit),
    ).fetchall()
    return [dict(r) for r in rows]


def current(conn: "sqlite3.Connection", task_id: str, stage_requirement_id: str) -> dict[str, Any] | None:
    """The newest attachment a ticket has against one requirement, or None."""
    found = attachments_for(conn, task_id, stage_requirement_id)
    return found[0] if found else None


def _format_of(mime: str, name: str, native_page: bool = False) -> str:
    """The format a requirement's `formats` names for a piece of evidence: a Tico page (a `pages` row), a PDF, an image,
    a video, or a plain file."""
    if native_page:
        return "page"
    lowered = (mime or "").lower()
    for prefix, kind in MIME_FORMATS.items():
        if lowered.startswith(prefix):
            return kind
    if lowered == "application/pdf" or name.lower().endswith(".pdf"):
        return "page"
    return "file"


def _native_pages(conn: "sqlite3.Connection", file_ids: list[str]) -> set[str]:
    """The company files among `file_ids` that are live Tico pages (backend/pages.py), so a page satisfies a page format."""
    if not file_ids or not H._has_table(conn, "pages"):
        return set()
    rows = conn.execute(
        "SELECT file_id FROM pages WHERE file_id IN (SELECT value FROM json_each(?)) AND deleted_at IS NULL "
        "AND archived=0",
        (json.dumps(file_ids),),
    ).fetchall()
    return {str(r[0]) for r in rows}


def views(conn: "sqlite3.Connection", rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each attachment row with what a reader needs to show it: `format` (image, video, page, file or link), `name` and
    `href`, the address of its content. A company file's href pins the version that was attached, so a later version
    never changes what was reviewed; a link's href is its URL. The media of all rows come from one query per kind."""
    files = sorted(
        {f"{r['file_id']}:{int(r['file_version'])}" for r in rows if r.get("file_id") and r.get("file_version")}
    )
    blobs = sorted({str(r["blob_id"]) for r in rows if r.get("blob_id")})
    media: dict[str, tuple[str, str]] = {}
    if files:
        for found in conn.execute(
            "SELECT file_id, version, mime, name FROM bot_file_versions "
            "WHERE file_id || ':' || version IN (SELECT value FROM json_each(?))",
            (json.dumps(files),),
        ).fetchall():
            media[f"{found[0]}:{found[1]}"] = (str(found[2]), str(found[3]))
    if blobs:
        for found in conn.execute(
            "SELECT id, content_type, name FROM blobs WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(blobs),),
        ).fetchall():
            media[str(found[0])] = (str(found[1]), str(found[2]))
    pages = _native_pages(conn, sorted({str(r["file_id"]) for r in rows if r.get("file_id")}))
    out: list[dict[str, Any]] = []
    for r in rows:
        source = str(r.get("source") or "")
        if source == "link":
            fmt, name, href = "link", str(r.get("url") or ""), r.get("url")
        elif source == "blob" and r.get("blob_id"):
            mime, name = media.get(str(r["blob_id"]), ("", "File"))
            fmt, href = _format_of(mime, name), f"/api/v2/files/{r['blob_id']}"
        elif r.get("file_id") and r.get("file_version"):
            version = int(r["file_version"])
            mime, name = media.get(f"{r['file_id']}:{version}", ("", str(r["file_id"])))
            fmt = _format_of(mime, name, str(r["file_id"]) in pages)
            href = f"/api/v2/files/{r['file_id']}/versions/{version}"
        else:
            fmt, name, href = "file", source.replace("_", " ").capitalize() or "File", None
        out.append({**r, "format": fmt, "name": name, "href": href})
    return out


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _may_edit_ticket(conn: "sqlite3.Connection", actor: str, task: dict[str, Any]) -> bool:
    """Who may change a ticket, as a change to it is made (hubdb.task_update): its owner, requester or their managers and
    delegates, a bot that works the ticket's type, or a person who may move any ticket. A bot that only reads the type
    attaches nothing: attached evidence can void an approval."""
    return bool(H._task_party(conn, actor, task) or H.type_bot_works(conn, actor, task) or H.can_move(conn, actor))


def _reader(conn: "sqlite3.Connection", auth: "Auth", who: Any, actor: str) -> Any:
    """The identity a reader of this actor's name reads a source as, or None when no identity can be made for it (a person
    no longer on the roster). The caller's own identity is `who`."""
    if actor == str(who.actor):
        return who
    guard: Any = auth
    try:
        return guard.identity_for_actor(conn, actor)
    except Problem:
        return None


def _file_readable(conn: "sqlite3.Connection", auth: "Auth", reader: Any, row: dict[str, Any]) -> bool:
    """Whether `reader` may read this company file, its page audience and its task's privacy included (files.file_visible)."""
    if reader is None:
        return False
    from . import files as files_module

    untyped: Any = files_module  # files.py is untyped: its functions are Any here, as hubdb's are
    return bool(untyped.file_visible(conn, auth, reader, row, {}))


def _source(conn: "sqlite3.Connection", auth: "Auth", who: Any, reviewer: str, body: dict[str, Any]) -> dict[str, Any]:
    """What the body names, as the row's columns, after checking the source exists, is ready, is current, and can be read
    by the caller and by the reviewer. A source the caller cannot read is not found to them (as everywhere else); one the
    reviewer cannot read is refused. A file whose version is no longer the newest is out of date."""
    source = body["source"]
    actor = str(who.actor)
    if source == "file":
        if not body.get("file_id"):
            raise Problem("validation", "A file attachment names a file", 422)
        found = conn.execute("SELECT * FROM bot_files WHERE id=?", (body["file_id"],)).fetchone()
        if found is None or found["archived"] or found["state"] != "ready":
            raise Problem("not_found", "No such file", 404)
        row = dict(found)
        if not _file_readable(conn, auth, who, row):
            raise Problem("not_found", "No such file", 404)
        if reviewer != actor and not _file_readable(conn, auth, _reader(conn, auth, who, reviewer), row):
            raise Problem("forbidden", "The reviewer cannot read this file, so cannot see the evidence", 403)
        newest = int(found["current_version"])
        version = int(body.get("file_version") or newest)
        if version < newest:
            raise Problem("out_of_date", f"Version {version} is out of date: the file is at version {newest}", 409)
        if version > newest:
            raise Problem("validation", f"The file has no version {version}", 422)
        row = conn.execute(
            "SELECT blob_id, digest, mime, name FROM bot_file_versions WHERE file_id=? AND version=?",
            (body["file_id"], version),
        ).fetchone()
        if row is None:
            raise Problem("not_found", "No such file version", 404)
        native = body["file_id"] in _native_pages(conn, [body["file_id"]])
        return {
            "blob_id": row["blob_id"],
            "file_id": body["file_id"],
            "file_version": version,
            "url": None,
            "digest": row["digest"],
            "format": _format_of(row["mime"] or found["mime"], row["name"] or found["title"], native),
            "bot_id": found["bot_id"],
        }
    if source == "blob":
        if not body.get("blob_id"):
            raise Problem("validation", "A blob attachment names a blob", 422)
        found = conn.execute(
            "SELECT id, digest, content_type, name FROM blobs WHERE id=?", (body["blob_id"],)
        ).fetchone()
        if found is None or not PRIVACY.blob_readable(conn, actor, found["id"]):
            raise Problem("not_found", "No such file", 404)
        if reviewer != actor and not PRIVACY.blob_readable(conn, reviewer, found["id"]):
            raise Problem("forbidden", "The reviewer cannot read this file, so cannot see the evidence", 403)
        return {
            "blob_id": found["id"],
            "file_id": None,
            "file_version": None,
            "url": None,
            "digest": found["digest"],
            "format": _format_of(found["content_type"], found["name"]),
            "bot_id": None,
        }
    url = (body.get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise Problem("validation", "A link attachment needs an http or https address", 422)
    return {
        "blob_id": None,
        "file_id": None,
        "file_version": None,
        "url": url,
        "digest": _digest_text(url),
        "format": "link",
        "bot_id": None,
    }


def _may_read(conn: "sqlite3.Connection", actor: str, task_row: dict[str, Any]) -> bool:
    return bool(H.task_private_readable(conn, actor, task_row))


def attach(
    conn: "sqlite3.Connection", auth: "Auth", who: Any, task_id: str, requirement_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Attach evidence to a requirement of the ticket's current stage. `who` is the caller's identity; the writer check
    (a bot must be able to edit the ticket) and the reviewer's privacy check both apply."""
    actor = str(who.actor)
    guard: Any = auth  # the Auth class is untyped
    guard.task(conn, who, task_id)  # 404 unless the caller can read the ticket
    H._writer(conn, actor, task_id=task_id)
    task: dict[str, Any] = H.task(conn, task_id) or {}
    if not _may_edit_ticket(conn, actor, task):
        raise Problem(
            "forbidden",
            "Only the ticket's owner, requester, their managers, a bot that works its type or a task mover can attach evidence",
            403,
        )
    req = R.requirement(conn, requirement_id)
    if req is None or req["retired_at"]:
        raise Problem("not_found", "No such requirement", 404)
    if req["kind"] != "attachment":
        raise Problem("validation", "This requirement takes no attachment", 422)
    from . import pipeline_engine

    if not pipeline_engine.counts_on_card(conn, task_id, req):
        raise Problem("conflict", "The ticket is not at this requirement's stage", 409)
    reviewer = req["reviewer_id"] or actor
    if not _may_read(conn, reviewer, task):
        raise Problem("forbidden", "The reviewer cannot read this ticket, so cannot see the attachment", 403)
    needs = req["needs"]
    what = _source(conn, auth, who, reviewer, body)
    formats: list[str] = needs.get("formats") or []
    if formats and what["format"] not in formats:
        raise Problem("validation", f"This requirement takes {', '.join(formats)}, not {what['format']}", 422)
    if needs.get("is_work"):
        conn.execute("UPDATE tasks SET work_revision = work_revision + 1 WHERE id=?", (task_id,))
    work = conn.execute("SELECT work_revision FROM tasks WHERE id=?", (task_id,)).fetchone()
    # The same evidence attached again keeps the earliest work version it was attached at on this ticket, so old evidence
    # cannot look fresh after a change (D33). Removed attachments count too.
    earliest = conn.execute(
        "SELECT MIN(work_version) FROM requirement_attachments WHERE task_id=? AND digest=?", (task_id, what["digest"])
    ).fetchone()
    attached_at = int(earliest[0]) if earliest and earliest[0] is not None else (int(work[0]) if work else 0)
    now = H.now()
    row_id = H.new_id()
    conn.execute(
        "INSERT INTO requirement_attachments (id, task_id, stage_requirement_id, source, blob_id, file_id, file_version, "
        "task_link_id, url, digest, work_version, commit_sha, attached_by, created_at) "
        "VALUES (?,?,?,?,?,?,?,NULL,?,?,?,NULL,?,?)",
        (
            row_id,
            task_id,
            requirement_id,
            body["source"],
            what["blob_id"],
            what["file_id"],
            what["file_version"],
            what["url"],
            what["digest"],
            attached_at,
            actor,
            now,
        ),
    )
    H.event(
        conn,
        actor,
        "requirement.attach",
        task_id,
        {"requirement": requirement_id, "attachment": row_id, "source": body["source"], "digest": what["digest"]},
    )
    from . import pipeline_github_checks

    pipeline_github_checks.publish_affected(conn, task_id)
    return {**(current(conn, task_id, requirement_id) or {}), "reviewed_by": req["reviewed_by"]}


def remove(conn: "sqlite3.Connection", auth: "Auth", who: Any, attachment_id: str) -> dict[str, Any]:
    """Take an attachment off its requirement. Its attacher, or the pipeline's owner or an admin, may."""
    actor = str(who.actor)
    found = conn.execute("SELECT * FROM requirement_attachments WHERE id=?", (attachment_id,)).fetchone()
    if found is None or found["removed_at"]:
        raise Problem("not_found", "No such attachment", 404)
    guard: Any = auth  # the Auth class is untyped
    guard.task(conn, who, found["task_id"])
    req = R.requirement(conn, found["stage_requirement_id"]) or {}
    if actor != found["attached_by"] and not pipeline_rights.may_edit_rules(conn, actor, req.get("pipeline_id")):
        raise Problem("forbidden", "Only who attached it, or the pipeline's owner and admins, may remove it", 403)
    now = H.now()
    conn.execute(
        "UPDATE requirement_attachments SET removed_at=?, removed_by=? WHERE id=?", (now, actor, attachment_id)
    )
    H.event(conn, actor, "requirement.detach", found["task_id"], {"attachment": attachment_id})
    from . import pipeline_github_checks

    pipeline_github_checks.publish_affected(conn, found["task_id"])
    return {**dict(found), "removed_at": now, "removed_by": actor}


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """The attachment routes: attach to a requirement on a ticket, list them, and remove one."""
    guard: Any = auth
    db: Any = store  # Store is untyped, so its connections are Any here, not partially unknown

    @app.post("/api/v2/tasks/{tid}/requirements/{requirement_id}/attachments")
    def requirement_attach(request: Request, tid: str, requirement_id: str, body: AttachBody) -> dict[str, Any]:
        """Attach a company file, an uploaded blob or a link as evidence for a requirement of the ticket's current stage.
        A person or a bot may attach; the reviewer must be able to read the ticket."""
        who = request.state.identity

        def write(c: "sqlite3.Connection") -> dict[str, Any]:
            return attach(c, auth, who, tid, requirement_id, body.model_dump())

        return mutate(request, body, write)

    @app.get("/api/v2/tasks/{tid}/requirements/{requirement_id}/attachments")
    def requirement_attachments(request: Request, tid: str, requirement_id: str) -> dict[str, Any]:
        """The evidence attached to one requirement on a ticket, newest first, with its digest, work version, format,
        name and href (see views). At most ATTACH_LIMIT rows."""
        who = request.state.identity
        with db.read() as c:
            guard.task(c, who, tid)
            return {"attachments": views(c, attachments_for(c, tid, requirement_id, ATTACH_LIMIT))}

    @app.delete("/api/v2/requirement-attachments/{attachment_id}")
    def requirement_detach(request: Request, attachment_id: str) -> dict[str, Any]:
        """Remove an attachment; it stops counting for the requirement."""
        who = request.state.identity

        def write(c: "sqlite3.Connection") -> dict[str, Any]:
            return remove(c, auth, who, attachment_id)

        return mutate(request, R.Nothing(), write)
