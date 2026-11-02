"""One public team logo, normalized to PNG for desktop builds."""
import asyncio
import hashlib
import io
import json
import warnings

from fastapi import Request
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError

from . import blobs
from .store import H, Problem, encode

LIMIT = 1024 * 1024
PIXELS = 16 * 1024 * 1024
KEY = "team_icon"


def png(data):
    if len(data) > LIMIT:
        raise Problem("too_large", "Team icon must be at most 1 MB", 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in ("PNG", "JPEG", "WEBP"):
                    raise ValueError()
                if image.width * image.height > PIXELS:
                    raise ValueError()
                image.load()
                image.thumbnail((1024, 1024))
                image = image.convert("RGBA")
                image.info.clear()
                # Desktop icon generators require a square; retain the entire logo.
                side = max(image.width, image.height)
                square = Image.new("RGBA", (side, side))
                square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
                image = square
                while True:
                    out = io.BytesIO()
                    image.save(out, format="PNG")
                    if out.tell() <= LIMIT:
                        return out.getvalue()
                    image.thumbnail((max(1, image.width // 2), max(1, image.height // 2)))
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise Problem("icon_type", "Use a valid PNG, JPEG or WebP image", 422) from exc


def install_team_icon(app, store):
    storage = app.state.blobs

    @app.get("/api/v2/team/icon")
    def get_icon(request: Request):
        with store.read() as c:
            row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
        if not row:
            raise Problem("not_found", "No team icon is set", 404)
        icon = json.loads(row[0])
        etag = '"' + icon["digest"] + '"'
        headers = {"ETag": etag, "Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"}
        matches = [value.strip().removeprefix("W/") for value in request.headers.get("if-none-match", "").split(",")]
        if etag in matches or "*" in matches:
            return Response(status_code=304, headers=headers)
        return Response(storage.get(icon["digest"]), media_type="image/png", headers=headers)

    @app.post("/api/v2/team/icon")
    async def set_icon(request: Request):
        who = request.state.identity
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner changes the team icon", 403)
        data = await asyncio.to_thread(png, await request.body())
        digest = hashlib.sha256(data).hexdigest()
        def cleanup(c, icon):
            old = icon.get("retired_digest")
            if not old or c.execute("SELECT 1 FROM blobs WHERE digest=?", (old,)).fetchone():
                return
            try:
                if storage.bucket:
                    storage.s3.delete_object(Bucket=storage.bucket, Key=storage.s3_key(old))
                (storage.directory / storage.key(old)).unlink(missing_ok=True)
            except Exception as exc:
                raise Problem("blob_storage", "Previous team icon cleanup failed; check storage permissions and disk space, then retry", 503, True) from exc
            c.execute("DELETE FROM blob_locations WHERE digest=?", (old,))

        def save(c):
            previous = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
            icon = json.loads(previous[0]) if previous else None
            if icon:
                # Refuse another replacement if the previous cleanup is still failing. At most
                # one retired logo is kept, and deleting it cannot invalidate the current one.
                cleanup(c, icon)
            storage.put(data, "image/png")
            if icon:
                c.execute("UPDATE blobs SET digest=?,size=?,created_at=? WHERE id=?",
                          (digest, len(data), H.now(), icon["id"]))
            else:
                icon = blobs.register(c, who, digest, len(data), "team-icon.png", "image/png")
            value = {"id": icon["id"], "digest": digest}
            if previous and icon["digest"] != digest:
                value["retired_digest"] = icon["digest"]
            c.execute("INSERT INTO registry_metadata(key,value) VALUES(?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (KEY, encode(value)))
            return {"url": "/api/v2/team/icon", "content_type": "image/png"}

        def write():
            result = store.mutate(who, request.url.path, request.headers.get("idempotency-key"), {"digest": digest}, save)
            # Cleanup follows the committed replacement. Retries also finish a pending cleanup
            # when the idempotency response is replayed, without storing another logo.
            with store.transaction() as c:
                row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
                icon = json.loads(row[0])
                cleanup(c, icon)
                if icon.pop("retired_digest", None):
                    c.execute("UPDATE registry_metadata SET value=? WHERE key=?", (encode(icon), KEY))
            return result

        return await asyncio.to_thread(write)
