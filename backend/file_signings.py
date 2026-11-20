# pyright: strict
"""Signing a PDF in Tico: a person's saved signature, and the record of each PDF they signed.

The web app draws the person's marks (their signature, a line of text, a date) into the PDF in the browser and sends
the signed bytes here as the next version of the same task file. One write adds that version and its `file_signings`
row: who signed, when, which version they signed and which version it made, the SHA-256 of each, and the address and
browser the request came from. Nothing here reads or changes the PDF itself.

    GET/PUT/DELETE /api/v2/me/signature     the caller's own signature (a person only)
    POST /api/v2/files/{fid}/signings       sign the file's current version (a person only, in their own session)
    GET  /api/v2/files/{fid}/signings       who signed which version of a file, for anyone who may see the file
"""

import asyncio
import base64
import binascii
import json
from collections.abc import Callable, Iterable
from typing import Any, cast

from fastapi import FastAPI, Request
from pydantic import Field

from . import models as M
from .api_contract import api_v2, error
from .store import H as _H
from .store import Problem

H: Any = _H

SIGNATURE_MAX = 300_000  # bytes of PNG: a drawn signature is a few kilobytes
PNG = b"\x89PNG\r\n\x1a\n"
MARK_KINDS = ("signature", "text", "date")
LIST_MAX = 200


class SignatureBody(M.Contract):
    image: str = Field(min_length=30, max_length=420_000, description="The signature as a PNG data URL")


def signature_bytes(image: str) -> bytes:
    """The PNG a data URL holds, refused unless it is one and small enough."""
    head, _, data = image.partition(",")
    if head != "data:image/png;base64":
        raise Problem("validation", "Send the signature as a PNG data URL", 422)
    try:
        png = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        raise Problem("validation", "The signature is not valid base64", 422) from None
    if not png.startswith(PNG) or len(png) > SIGNATURE_MAX:
        raise Problem("validation", "The signature is a PNG of at most 300 KB", 422)
    return png


def marks_of(raw: object) -> list[dict[str, Any]]:
    """What the person added, as the browser says it: [{kind, page}], at most 100. A record of what was drawn, never
    used to draw."""
    try:
        marks: Any = json.loads(str(raw or "[]"))
    except ValueError:
        raise Problem("validation", "marks is a JSON list", 422) from None
    items = cast(list[Any], marks) if isinstance(marks, list) else []
    if not items or len(items) > 100:
        raise Problem("validation", "Add at least one mark, at most 100", 422)
    out: list[dict[str, Any]] = []
    for m in items:
        mark = cast(dict[str, Any], m) if isinstance(m, dict) else {}
        if mark.get("kind") not in MARK_KINDS:
            raise Problem("validation", "Each mark is a signature, text or date", 422)
        page = mark.get("page")
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 100_000:
            raise Problem("validation", "Each mark names its page, from 1", 422)
        out.append({"kind": mark["kind"], "page": page})
    return out


def signed_versions(c: Any, file_ids: Iterable[str]) -> dict[tuple[str, int], dict[str, Any]]:
    """{(file id, version): {by, by_name, at}} for the versions a signing made, for a list of files' versions."""
    ids = sorted(set(file_ids))
    if not ids:
        return {}
    rows: list[Any] = c.execute(
        "SELECT file_id,after_version,signer_id,created_at FROM file_signings "
        "WHERE file_id IN (SELECT value FROM json_each(?))",
        (json.dumps(ids),),
    ).fetchall()
    return {
        (r["file_id"], r["after_version"]): {
            "by": r["signer_id"],
            "by_name": H.actor_name(c, r["signer_id"]),
            "at": r["created_at"],
        }
        for r in rows
    }


def view(c: Any, row: Any, *, person: bool) -> dict[str, Any]:
    """One signing as the record reads it. The address and browser are for people only."""
    out: dict[str, Any] = {
        "id": row["id"],
        "file_id": row["file_id"],
        "task_id": row["task_id"],
        "signer": row["signer_id"],
        "signer_name": H.actor_name(c, row["signer_id"]),
        "before_version": row["before_version"],
        "after_version": row["after_version"],
        "before_sha256": row["before_sha256"],
        "after_sha256": row["after_sha256"],
        "marks": json.loads(row["marks"] or "[]"),
        "created_at": row["created_at"],
    }
    if person:
        out |= {"ip_address": row["ip_address"], "user_agent": row["user_agent"]}
    return out


def refusal_of(result: Any) -> dict[str, Any] | None:
    """The refusal a write's stored answer holds (Store.mutate keeps one as `_refusal`), or None."""
    return cast(dict[str, Any], result).get("_refusal") if isinstance(result, dict) else None


def is_pdf(v: Any) -> bool:
    mime, name = str(v["mime"] or ""), str(v["name"] or "")
    return mime.lower().split(";")[0].strip() == "application/pdf" or name.lower().endswith(".pdf")


def signer_only(who: Any) -> None:
    """A person in their own session: never a bot, a service, Tico support, a bot's view of the app, or the Assistant
    acting for them (backend/assistant.py NEVER keeps its turn off these routes too)."""
    from .views import human_only  # pyright: ignore[reportUnknownVariableType] - untyped module

    human_only(who)
    if who.support or who.view or who.via == "assistant":
        raise Problem("person_only", "Only the person signs, in their own session", 403)


def install(app: FastAPI, store: Any, auth: Any, blobs: Any, files: Any, mutate: Callable[..., Any]) -> None:
    @app.get(
        "/api/v2/me/signature",
        **api_v2("Files", "getMySignature", "The caller's saved signature, or null", "MySignature"),
    )
    def get_signature(request: Request):
        who = request.state.identity
        signer_only(who)
        with store.read() as c:
            row = c.execute(
                "SELECT image,updated_at FROM signatures WHERE person_id=?", (H.actor_id(who.actor),)
            ).fetchone()
        return {"signature": dict(row) if row else None}

    @app.put(
        "/api/v2/me/signature",
        **api_v2("Files", "saveMySignature", "Save or replace the caller's signature (a PNG data URL)", "MySignature"),
    )
    def save_signature(request: Request, body: SignatureBody):
        who = request.state.identity
        signer_only(who)
        signature_bytes(body.image)

        def work(c: Any) -> dict[str, Any]:
            at = H.now()
            c.execute(
                "INSERT INTO signatures(person_id,image,created_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(person_id) DO UPDATE SET image=excluded.image,updated_at=excluded.updated_at",
                (H.actor_id(who.actor), body.image, at, at),
            )
            return {"signature": {"image": body.image, "updated_at": at}}

        return mutate(request, body, work)

    @app.delete(
        "/api/v2/me/signature",
        **api_v2("Files", "deleteMySignature", "Remove the caller's saved signature", "MySignature"),
    )
    def delete_signature(request: Request):
        who = request.state.identity
        signer_only(who)

        def work(c: Any) -> dict[str, Any]:
            c.execute("DELETE FROM signatures WHERE person_id=?", (H.actor_id(who.actor),))
            return {"signature": None}

        return mutate(request, M.Empty(), work)

    @app.get(
        "/api/v2/files/{fid}/signings",
        **api_v2(
            "Files",
            "listFileSignings",
            "Who signed which version of a file in Tico, newest first: the version signed and the one it made, each "
            "one's SHA-256, what was added on which page, and when (a person also reads the address and browser)",
            "FileSignings",
            responses={"404": error("not_found: no such file, or one the caller may not see")},
        ),
    )
    def list_signings(request: Request, fid: str):
        who = request.state.identity
        with store.read() as c:
            files.visible_file(c, who, fid)
            rows: list[Any] = c.execute(
                "SELECT * FROM file_signings WHERE file_id=? ORDER BY after_version DESC LIMIT ?", (fid, LIST_MAX)
            ).fetchall()
            person = who.role in ("owner", "human")
            return {"file": fid, "signings": [view(c, r, person=person) for r in rows]}

    sign_body = {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file", "version", "marks"],
                    "properties": {
                        "file": {"type": "string", "format": "binary", "description": "The signed PDF"},
                        "version": {"type": "integer", "description": "The version the marks were drawn on"},
                        "marks": {
                            "type": "string",
                            "description": 'JSON: [{"kind": "signature|text|date", "page": 1}]',
                        },
                    },
                }
            }
        },
    }

    @app.post(
        "/api/v2/files/{fid}/signings",
        **api_v2(
            "Files",
            "signFile",
            "Sign a task's PDF: the signed bytes become the file's next version, with a record of the signing",
            "FileSigningResult",
            request_body=sign_body,
            responses={
                "403": error("person_only: a bot, a service, support or the Assistant; or no write on the task"),
                "404": error("not_found: no such file, or one the caller may not see"),
                "409": error("stale_version: the file has a newer version than the one signed; open it again"),
            },
        ),
    )
    async def sign(request: Request, fid: str) -> Any:
        # The upload and file modules this borrows from are not typed.
        from . import blobs as blobs_module
        from . import client_ip as client_ip_module
        from . import file_upload as file_upload_module
        from . import files as files_module
        from . import media as media_module
        from . import task_reads as task_reads_module

        untyped: tuple[Any, ...] = (
            blobs_module,
            client_ip_module,
            file_upload_module,
            files_module,
            media_module,
            task_reads_module,
        )
        b, ip_mod, up, fm, md, tr = untyped
        lib: dict[str, Any] = {
            "register": b.register,
            "client_ip": ip_mod.client_ip,
            "parse": up.parse,
            "file_added": fm.file_added,
            "stored_version": fm.stored_version,
            "replay_upload": md.replay_upload,
            "upload_key": md.upload_key,
            "comment_rights": tr.comment_rights,
        }
        who: Any = request.state.identity
        signer_only(who)
        lib["upload_key"](request)
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            raise Problem("validation", "Send the signed PDF as multipart/form-data", 422)

        def signable(c: Any, number: int) -> tuple[Any, Any]:
            """The file's row and the version signed, refused unless `who` may read it and write on its task, and it
            is the current version of a PDF."""
            row = files.visible_file(c, who, fid)
            if not row["task_id"] or row["locator"] != "tico_blob":
                raise Problem("validation", "Only a task's file can be signed", 422)
            lib["comment_rights"](c, auth, who, row["task_id"])
            before = files.readable_version(c, who, fid, number)
            if not is_pdf(before):
                raise Problem("validation", "Only a PDF can be signed", 422)
            if row["current_version"] != number:
                raise Problem("stale_version", "This file has a newer version: open it again to sign that one", 409)
            return row, before

        parsed: Any = await lib["parse"](
            request, store.settings.upload_max_bytes, blobs.directory, allowed=("version", "marks")
        )
        fields, uploads, stack = parsed
        try:
            upload: Any = uploads.get("file")
            if not upload:
                raise Problem("validation", "Send the signed PDF as `file`", 422)
            try:
                number = int(str(fields.get("version", "")))
            except ValueError:
                raise Problem("validation", "Say the version signed", 422) from None
            marks = marks_of(fields.get("marks"))
            if not upload["stream"].read(5).startswith(b"%PDF-"):
                raise Problem("validation", "The signed file is not a PDF", 422)
            upload["stream"].seek(0)
            digest, size = upload["digest"], upload["size"]
            payload = {"version": number, "marks": marks, "digest": digest, "size": size}
            replay = await asyncio.to_thread(lib["replay_upload"], store, request, payload)
            if replay is not None:
                return replay

            def check() -> None:
                with store.read() as c:
                    signable(c, number)

            await asyncio.to_thread(check)
            await asyncio.to_thread(blobs.put_staged, upload["stream"], digest, size, "application/pdf")
        finally:
            stack.close()
        ip = lib["client_ip"](request, store.settings)
        agent = request.headers.get("user-agent", "")[:500] or None

        def work(c: Any) -> dict[str, Any]:
            row, before = signable(c, number)
            task_id = row["task_id"]
            H._writer(c, who.actor, task_id=task_id)
            item = lib["register"](c, who, digest, size, before["name"], "application/pdf")
            _, after = files.attach_task(c, who, task_id, item, digest, row["bot_id"], file=row, series=False)
            c.execute("INSERT INTO task_assets VALUES(?,?)", (task_id, item["id"]))
            c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.now(), task_id))
            lib["file_added"](c, who, task_id, item, after)
            signing: dict[str, Any] = {
                "id": H.new_id(),
                "file_id": fid,
                "task_id": task_id,
                "signer_id": who.actor,
                "before_version": number,
                "after_version": after,
                "before_sha256": lib["stored_version"](c, fid, number)["blob_digest"],
                "after_sha256": digest,
                "marks": json.dumps(marks),
                "ip_address": None if ip == "unknown" else ip,
                "user_agent": agent,
                "created_at": H.now(),
            }
            c.execute(
                "INSERT INTO file_signings(" + ",".join(signing) + ") VALUES(" + ",".join("?" * len(signing)) + ")",
                tuple(signing.values()),
            )
            H.event(
                c,
                who.actor,
                "file.signed",
                task_id,
                {"file": fid, "before_version": number, "after_version": after, "after_sha256": digest},
            )
            return {"file_id": fid, "version": after, "signing": view(c, signing, person=True)}

        result: Any = await asyncio.to_thread(
            store.mutate, who, request.url.path, request.headers.get("idempotency-key"), payload, work
        )
        refusal = refusal_of(result)
        if refusal:
            raise Problem(refusal["code"], refusal["detail"], refusal["status"])
        app.state.file_metadata.wake.set()
        return result
