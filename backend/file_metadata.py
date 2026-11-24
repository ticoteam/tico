"""One bounded worker derives media outside upload requests; tools are optional."""

import hashlib
import json
import os
import shutil
import signal
import struct
import sys
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import IO

from .blobs import register
from .store import H, Problem

FIELDS = ("width", "height", "duration_ms", "poster_blob_id", "thumb_blob_id", "preview_html_blob_id", "media_state")
# Starts from the few pending rows (partial indexes in hubdb.migrate), never a scan of every blob: it runs every few seconds.
# INDEXED BY pins the partial pending indexes: with small or freshly analysed tables the planner may otherwise
# scan the full (blob_id,media_state) index, which visits every version.
PENDING = (
    "SELECT b.*,m.poster_blob_id FROM (SELECT blob_id FROM blob_media INDEXED BY blob_media_pending "
    "WHERE media_state='pending' UNION SELECT blob_id FROM bot_file_versions INDEXED BY bot_file_versions_pending "
    "WHERE media_state='pending') p JOIN blobs b ON b.id=p.blob_id "
    "LEFT JOIN blob_media m ON m.blob_id=b.id LEFT JOIN blob_media_retries r ON r.blob_id=b.id "
    "WHERE COALESCE(r.retry_at,0)<=? LIMIT 10"
)


def dimensions(stream):
    """Small header reads, including when Pillow is not installed."""
    stream.seek(0)
    head = stream.read(32)
    if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
        return struct.unpack(">II", head[16:24])
    if head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
        return struct.unpack("<HH", head[6:10])
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        if head[12:16] == b"VP8X" and len(head) >= 30:
            return int.from_bytes(head[24:27], "little") + 1, int.from_bytes(head[27:30], "little") + 1
        if head[12:16] == b"VP8 " and head[23:26] == b"\x9d\x01\x2a":
            return tuple(n & 0x3FFF for n in struct.unpack("<HH", head[26:30]))
        if head[12:16] == b"VP8L" and head[20] == 0x2F:
            bits = int.from_bytes(head[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if head[:2] == b"\xff\xd8":
        stream.seek(2)
        for _ in range(256):
            if stream.read(1) != b"\xff":
                break
            marker = stream.read(1)
            while marker == b"\xff":
                marker = stream.read(1)
            if marker in (b"\xd9", b"\xda", b""):
                break
            if marker == b"\x01" or b"\xd0" <= marker <= b"\xd7":
                continue
            length = stream.read(2)
            if len(length) != 2:
                break
            size = int.from_bytes(length, "big")
            if size < 2:
                break
            if marker[0] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                frame = stream.read(5)
                if len(frame) == 5:
                    h, w = struct.unpack(">HH", frame[1:])
                    return w, h
                break
            stream.seek(size - 2, 1)
    return None, None


def validate_poster(stream):
    stream.seek(0)
    head = stream.read(8)
    stream.seek(0)
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    raise Problem("validation", "Supply a PNG or JPEG poster", 422)


class DecodeError(Exception):
    """An unsupported or invalid media input cannot be retried."""


IMAGES = ("image/png", "image/jpeg", "image/gif", "image/webp")
VIDEOS = ("video/mp4", "video/webm", "video/quicktime")
# Word and Excel files get an HTML preview from backend/office_preview.py, which needs no tool beyond Python.
OFFICE = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
}
# A type that says nothing of what the file is: a Word or Excel file sent as one is known by its name, as the chat's card
# knows it (web/src/lib/fileKind.ts); office_preview.py still checks it is one.
GENERIC = {"", "application/octet-stream", "binary/octet-stream"}
BY_NAME = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
TRIES = 5  # failed passes before a source is given up on (`Metadata.retry`)
MAX_BYTES = 25 * 1024**2  # a larger source gets no preview: it is never copied in to be read (the file still opens)
WALL_SECONDS = 30
RSS_KIB = 1024 * 1024  # what a child may hold on macOS, where address-space limits are refused


PREVIEW_PENDING = "preview_pending"


def preview_missing(derivative, media):
    """The refusal for a preview that is not there: `preview_pending` while the worker has a Word or Excel file's
    preview still to make (the route answers 202 so the chat asks again), else not found."""
    if derivative == "preview_html" and media is not None and media["media_state"] == "pending":
        return Problem(PREVIEW_PENDING, "The preview is still being made", 404)
    return Problem("not_found", "File preview not found", 404)


def media_type(mime, name=None):
    """A file's type as the worker reads it: its own, parameters dropped, or for a generic type a Word or Excel type
    its name gives."""
    mime = (mime or "").split(";", 1)[0].strip().lower()
    if mime in GENERIC:
        return BY_NAME.get(Path(name or "").suffix.lower(), mime)
    return mime


def derives(mime, name=None):
    """Whether the worker makes anything from a file of this type on this server: a picture always (its size, and a
    thumbnail with Pillow), a Word or Excel file always (its HTML preview); a video or a PDF only with its tool
    installed. The chat queues only these."""
    mime = media_type(mime, name)
    return (
        mime in IMAGES
        or mime in OFFICE
        or mime in VIDEOS
        and bool(shutil.which("ffprobe") and shutil.which("ffmpeg"))
        or mime == "application/pdf"
        and bool(shutil.which("pdftoppm"))
    )


def supervised(args, stdout: int | IO[bytes] = subprocess.PIPE):
    """Run one child to its end under the wall timeout, in a process group of its own that is killed whole when it runs
    over (or, on macOS, holds more than RSS_KIB). Returns what it wrote to a piped stdout."""
    with subprocess.Popen(
        args, stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.DEVNULL, start_new_session=True
    ) as child:
        deadline = time.monotonic() + WALL_SECONDS
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise DecodeError("Media decoding timed out")
                try:
                    output, _ = child.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    if sys.platform == "darwin":
                        usage = subprocess.run(
                            ["/bin/ps", "-o", "rss=", "-p", str(child.pid)],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL,
                            timeout=1,
                        )
                        if usage.stdout.strip() and int(usage.stdout) > RSS_KIB:
                            raise DecodeError("Media decoding exceeded its memory budget")
            if child.returncode:
                raise DecodeError("Media decoding failed")
            return output or b""
        finally:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()


def image(source, target):
    with source.open("rb") as stream:
        width, height = dimensions(stream)
    try:
        import PIL  # noqa: F401
    except ImportError:
        return width, height, None
    try:
        output = supervised([sys.executable, str(Path(__file__).with_name("file_image.py")), str(source), str(target)])
        width, height = json.loads(output)
    except (subprocess.SubprocessError, ValueError) as exc:
        raise DecodeError("Image decoding failed") from exc
    return width, height, target


def run(args, scratch):
    """One tool through backend.limited (its limits, a bare environment, `scratch` as its working directory). Its stdout
    goes to a file in `scratch`, so the tool's file-size limit bounds what it prints too. Returns those bytes."""
    out = Path(scratch) / "stdout"
    with out.open("wb") as sink:
        supervised([sys.executable, "-I", str(Path(__file__).with_name("limited.py")), str(scratch), *args], sink)
    data = out.read_bytes()
    out.unlink()
    return data


class Metadata:
    def __init__(self, store, blobs):
        self.store, self.blobs = store, blobs
        self.stop, self.wake = threading.Event(), threading.Event()

    def process(self, blob):
        result = dict(
            width=None,
            height=None,
            duration_ms=None,
            poster_blob_id=blob.get("poster_blob_id"),
            thumb_blob_id=None,
            preview_html_blob_id=None,
            media_state="none",
        )
        mime = media_type(blob["content_type"], blob.get("name"))
        if not (mime.startswith("image/") or mime.startswith("video/") or mime == "application/pdf" or mime in OFFICE):
            return self.save(blob, result, [])
        if not derives(mime) or (blob.get("size") or 0) > MAX_BYTES:
            # Nothing is derived from it here (an SVG, no tool for it, or too big), so it is never read: a file in Tico's
            # bucket is read through the company's address, and every byte read there is charged as delivery.
            return self.save(blob, result | {"media_state": "ready" if result["poster_blob_id"] else "none"}, [])
        derived = []
        try:
            self.blobs.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="media-", dir=self.blobs.directory) as directory:
                root = Path(directory)
                source, poster, thumb = root / "source", root / "poster.jpg", root / "thumb.jpg"
                sha = hashlib.sha256()
                with source.open("wb") as out:
                    for chunk in self.blobs.open_range(blob["digest"]):
                        out.write(chunk)
                        sha.update(chunk)
                if sha.hexdigest() != blob["digest"]:
                    raise Problem("blob_integrity", "Media source failed its integrity check", 503, True)
                preview = None
                if mime in IMAGES:
                    result["width"], result["height"], preview = image(source, thumb)
                elif mime in VIDEOS:
                    container = "matroska" if mime == "video/webm" else "mov"
                    probe = json.loads(
                        run(
                            [
                                shutil.which("ffprobe"),
                                "-v",
                                "error",
                                "-show_streams",
                                "-show_format",
                                "-of",
                                "json",
                                "-protocol_whitelist",
                                "file",
                                "-f",
                                container,
                                str(source),
                            ],
                            root,
                        )
                    )
                    stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
                    result["width"], result["height"] = stream.get("width"), stream.get("height")
                    seconds = float(probe.get("format", {}).get("duration") or stream.get("duration") or 0)
                    result["duration_ms"] = round(seconds * 1000)
                    run(
                        [
                            shutil.which("ffmpeg"),
                            "-nostdin",
                            "-v",
                            "error",
                            "-ss",
                            str(min(1, seconds * 0.1)),
                            "-protocol_whitelist",
                            "file",
                            "-threads",
                            "1",
                            "-f",
                            container,
                            "-i",
                            str(source),
                            "-threads",
                            "1",
                            "-frames:v",
                            "1",
                            "-vf",
                            "scale=640:640:force_original_aspect_ratio=decrease",
                            str(poster),
                        ],
                        root,
                    )
                    if not poster.is_file():
                        raise DecodeError("The video gave no frame")
                    if not result["poster_blob_id"]:
                        derived.append(("poster_blob_id", poster))
                    _, _, preview = image(poster, thumb)
                elif mime == "application/pdf":
                    run(
                        [
                            shutil.which("pdftoppm"),
                            "-f",
                            "1",
                            "-singlefile",
                            "-scale-to",
                            "640",
                            "-jpeg",
                            str(source),
                            str(root / "poster"),
                        ],
                        root,
                    )
                    if not poster.is_file():
                        raise DecodeError("The PDF gave no first page")
                    if not result["poster_blob_id"]:
                        derived.append(("poster_blob_id", poster))
                    result["width"], result["height"], preview = image(poster, thumb)
                elif mime in OFFICE:
                    page = root / "preview.html"
                    script = str(Path(__file__).with_name("office_preview.py"))
                    run([sys.executable, "-I", script, OFFICE[mime], str(source), str(page)], root)
                    if not page.is_file():
                        raise DecodeError("The document gave no preview")
                    derived.append(("preview_html_blob_id", page))
                if preview:
                    derived.append(("thumb_blob_id", preview))
                prepared = []
                try:
                    for field, path in derived:
                        kind = "text/html" if field == "preview_html_blob_id" else "image/jpeg"
                        with path.open("rb") as data:
                            digest = self.blobs.put_stream(data, kind)
                        prepared.append(
                            (
                                field,
                                {
                                    "digest": digest,
                                    "size": path.stat().st_size,
                                    "name": path.name,
                                    "content_type": kind,
                                },
                            )
                        )
                except BaseException:
                    # Previews stored before the failure are queued for removal; a later try that stores them again
                    # keeps them (drain checks again and waits out fresh writes).
                    self.discard(prepared)
                    raise
                result["media_state"] = "ready" if result["width"] or result["poster_blob_id"] or prepared else "none"
                self.save(blob, result, prepared)
        except (DecodeError, subprocess.SubprocessError, ValueError, KeyError, StopIteration):
            # Permanent decode failures never fail the upload.
            self.save(blob, result | {"media_state": "ready" if result["poster_blob_id"] else "none"}, [])

    def save(self, blob, result, prepared):
        from types import SimpleNamespace

        with self.store.transaction() as c:
            if not c.execute("SELECT 1 FROM blobs WHERE id=?", (blob["id"],)).fetchone():
                # Deleted while it was read: nothing to describe, and no preview to keep for it.
                c.execute("DELETE FROM blob_media_retries WHERE blob_id=?", (blob["id"],))
                self.discard(prepared, c)
                return
            for field, upload in prepared:
                result[field] = register(c, SimpleNamespace(actor=blob["owner"]), **upload)["id"]
            c.execute("DELETE FROM blob_media_retries WHERE blob_id=?", (blob["id"],))
            values = tuple(result[k] for k in FIELDS)
            c.execute("INSERT OR IGNORE INTO blob_media(blob_id) VALUES(?)", (blob["id"],))
            assignment = ",".join(k + "=?" for k in FIELDS)
            c.execute("UPDATE blob_media SET " + assignment + " WHERE blob_id=?", (*values, blob["id"]))
            c.execute("UPDATE bot_file_versions SET " + assignment + " WHERE blob_id=?", (*values, blob["id"]))

    def discard(self, prepared, c=None):
        """Queue stored previews no row took for removal, as a delete queues its own bytes (file_library.drain)."""
        from .file_library import queue_purge

        if c is None:
            with self.store.transaction() as c:
                return self.discard(prepared, c)
        for _, upload in prepared:
            queue_purge(c, upload["digest"], "", H.now())

    def retry(self, blob):
        """Another try later, with backoff, after a pass that failed for a reason that may pass (storage, the database).
        The TRIES-th failure gives the source up: its pending rows become `none` and its retry row goes, in one write,
        so a source that always fails is never read again. A source deleted meanwhile leaves only its retry row to go."""
        bid = blob["id"]
        with self.store.transaction() as c:
            if not c.execute("SELECT 1 FROM blobs WHERE id=?", (bid,)).fetchone():
                c.execute("DELETE FROM blob_media_retries WHERE blob_id=?", (bid,))
                return
            row = c.execute("SELECT attempts FROM blob_media_retries WHERE blob_id=?", (bid,)).fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            if attempts >= TRIES:
                c.execute("UPDATE blob_media SET media_state='none' WHERE blob_id=? AND media_state='pending'", (bid,))
                c.execute(
                    "UPDATE bot_file_versions SET media_state='none' WHERE blob_id=? AND media_state='pending'", (bid,)
                )
                c.execute("DELETE FROM blob_media_retries WHERE blob_id=?", (bid,))
                return
            c.execute("INSERT OR IGNORE INTO blob_media(blob_id) VALUES(?)", (bid,))
            c.execute(
                "INSERT INTO blob_media_retries(blob_id,attempts,retry_at) VALUES(?,?,?) "
                "ON CONFLICT(blob_id) DO UPDATE SET attempts=excluded.attempts,retry_at=excluded.retry_at",
                (bid, attempts, time.time() + min(3600, 5 * 2 ** min(attempts - 1, 10))),
            )

    def batch(self):
        with self.store.read() as c:
            rows = list(c.execute(PENDING, (time.time(),)))
        for row in rows:
            if self.stop.is_set():
                break
            try:
                self.process(dict(row))
            except Exception:
                try:
                    self.retry(dict(row))
                except Exception:
                    self.stop.wait(5)  # database unavailable; the loop will try again
        return bool(rows)

    def cleanup(self):
        # Only our staging directories, older than a day; never retained blob copies.
        for path in self.blobs.directory.glob("media-*"):
            if not path.is_symlink() and path.is_dir() and path.stat().st_mtime < time.time() - 86400:
                shutil.rmtree(path)

    def loop(self):
        try:
            self.cleanup()
        except Exception:
            pass
        while not self.stop.is_set():
            try:
                if not self.batch():
                    self.wake.wait(5)
                    self.wake.clear()
            except Exception:
                self.stop.wait(5)
            self.stop.wait(0.1)
