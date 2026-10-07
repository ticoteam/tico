"""One bounded worker derives media outside upload requests; tools are optional."""
import hashlib
import json
import shutil
import struct
import sys
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .blobs import register
from .store import Problem

FIELDS = ("width", "height", "duration_ms", "poster_blob_id", "thumb_blob_id", "media_state")
# Starts from the few pending rows (partial indexes in hubdb.migrate), never a scan of every blob: it runs every few seconds.
PENDING = ("SELECT b.*,m.poster_blob_id FROM (SELECT blob_id FROM blob_media WHERE media_state='pending' "
           "UNION SELECT blob_id FROM bot_file_versions WHERE media_state='pending') p JOIN blobs b ON b.id=p.blob_id "
           "LEFT JOIN blob_media m ON m.blob_id=b.id LEFT JOIN blob_media_retries r ON r.blob_id=b.id "
           "WHERE COALESCE(r.retry_at,0)<=? LIMIT 10")


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
            return tuple(n & 0x3fff for n in struct.unpack("<HH", head[26:30]))
        if head[12:16] == b"VP8L" and head[20] == 0x2f:
            bits = int.from_bytes(head[21:25], "little")
            return (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
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
            if marker[0] in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
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


def image(source, target):
    with source.open("rb") as stream:
        width, height = dimensions(stream)
    try:
        import PIL  # noqa: F401
    except ImportError:
        return width, height, None
    try:
        args = [sys.executable, str(Path(__file__).with_name("file_image.py")), str(source), str(target)]
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as child:
            deadline = time.monotonic() + 30
            try:
                while True:
                    if time.monotonic() >= deadline:
                        raise DecodeError("Image decoding timed out")
                    try:
                        output, _ = child.communicate(timeout=.1)
                        break
                    except subprocess.TimeoutExpired:
                        if sys.platform == "darwin":
                            usage = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(child.pid)],
                                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=1)
                            if usage.stdout.strip() and int(usage.stdout) > 1024 * 1024:
                                raise DecodeError("Image decoding exceeded its memory budget")
                if child.returncode:
                    raise DecodeError("Image decoding failed")
                width, height = json.loads(output)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
    except (subprocess.SubprocessError, ValueError) as exc:
        raise DecodeError("Image decoding failed") from exc
    return width, height, target


def run(args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=30, cwd=str(Path(args[-1]).parent) if Path(args[-1]).is_absolute() else None)


class Metadata:
    def __init__(self, store, blobs):
        self.store, self.blobs = store, blobs
        self.stop, self.wake = threading.Event(), threading.Event()

    def process(self, blob):
        result = dict(width=None, height=None, duration_ms=None, poster_blob_id=blob.get("poster_blob_id"),
                      thumb_blob_id=None, media_state="none")
        mime = blob["content_type"]
        if not (mime.startswith("image/") or mime.startswith("video/") or mime == "application/pdf"):
            return self.save(blob, result, [])
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
                if mime in ("image/png", "image/jpeg", "image/gif", "image/webp"):
                    result["width"], result["height"], preview = image(source, thumb)
                elif mime in ("video/mp4", "video/webm", "video/quicktime") and shutil.which("ffprobe") and shutil.which("ffmpeg"):
                    container = "matroska" if mime == "video/webm" else "mov"
                    probe = json.loads(run([shutil.which("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", "-protocol_whitelist", "file,pipe", "-f", container, str(source)]).stdout)
                    stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
                    result["width"], result["height"] = stream.get("width"), stream.get("height")
                    seconds = float(probe.get("format", {}).get("duration") or stream.get("duration") or 0)
                    result["duration_ms"] = round(seconds * 1000)
                    run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-ss", str(min(1, seconds * .1)),
                         "-protocol_whitelist", "file,pipe", "-f", container, "-i", str(source), "-frames:v", "1", "-vf", "scale=640:640:force_original_aspect_ratio=decrease", str(poster)])
                    if not result["poster_blob_id"]:
                        derived.append(("poster_blob_id", poster))
                    _, _, preview = image(poster, thumb)
                elif mime == "application/pdf" and shutil.which("pdftoppm"):
                    run([shutil.which("pdftoppm"), "-f", "1", "-singlefile", "-scale-to", "640", "-jpeg", str(source), str(root / "poster")])
                    if not result["poster_blob_id"]:
                        derived.append(("poster_blob_id", poster))
                    result["width"], result["height"], preview = image(poster, thumb)
                if preview:
                    derived.append(("thumb_blob_id", preview))
                prepared = []
                for field, path in derived:
                    with path.open("rb") as data:
                        digest = self.blobs.put_stream(data, "image/jpeg")
                    prepared.append((field, {"digest": digest, "size": path.stat().st_size,
                                             "name": path.name, "content_type": "image/jpeg"}))
                result["media_state"] = "ready" if result["width"] or result["poster_blob_id"] or prepared else "none"
                self.save(blob, result, prepared)
        except (DecodeError, subprocess.SubprocessError, ValueError, KeyError, StopIteration):
            # Permanent decode failures never fail the upload.
            self.save(blob, result | {"media_state": "ready" if result["poster_blob_id"] else "none"}, [])

    def save(self, blob, result, prepared):
        from types import SimpleNamespace
        with self.store.transaction() as c:
            for field, upload in prepared:
                result[field] = register(c, SimpleNamespace(actor=blob["owner"]), **upload)["id"]
            c.execute("DELETE FROM blob_media_retries WHERE blob_id=?", (blob["id"],))
            values = tuple(result[k] for k in FIELDS)
            c.execute("INSERT OR IGNORE INTO blob_media(blob_id) VALUES(?)", (blob["id"],))
            assignment = ",".join(k + "=?" for k in FIELDS)
            c.execute("UPDATE blob_media SET " + assignment + " WHERE blob_id=?", (*values, blob["id"]))
            c.execute("UPDATE bot_file_versions SET " + assignment + " WHERE blob_id=?", (*values, blob["id"]))

    def retry(self, blob):
        with self.store.transaction() as c:
            c.execute("INSERT OR IGNORE INTO blob_media(blob_id) VALUES(?)", (blob["id"],))
            c.execute("INSERT OR IGNORE INTO blob_media_retries(blob_id) VALUES(?)", (blob["id"],))
            row = c.execute("SELECT attempts FROM blob_media_retries WHERE blob_id=?", (blob["id"],)).fetchone()
            attempts = row["attempts"] + 1
            c.execute("UPDATE blob_media_retries SET attempts=?,retry_at=? WHERE blob_id=?",
                      (attempts, time.time() + min(3600, 5 * 2 ** min(attempts - 1, 10)), blob["id"]))

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
            self.stop.wait(.1)
