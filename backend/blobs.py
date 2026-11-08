"""Immutable binary storage. Only opaque, authorized IDs are exposed over HTTP."""

import base64
import collections
import contextlib
import io
import errno
import json
import logging
import time
import hashlib
import os
import re
import tempfile
import threading
from pathlib import Path

from .store import H, Problem
from . import blob_s3


log = logging.getLogger("tico.storage")

# How long an unreachable earlier place is skipped (Blobs._open_earlier).
EARLIER_RETRY = 300
# The digests a bucket does not have yet, for `copy_local`: each digest probes blob_locations' (digest, bucket) key, so
# the copies already made are never listed or read. The index blobs_by_digest serves the scan of blobs.
UNCOPIED_SQL = ("SELECT b.digest, MAX(b.content_type) AS content_type FROM blobs b "
                "WHERE NOT EXISTS (SELECT 1 FROM blob_locations l WHERE l.digest=b.digest AND l.bucket=?) GROUP BY b.digest")
# A refused upload starts a fresh write check when the last one began longer ago than this (Blobs._heal).
HEAL_AFTER = 60
# The place new files go to, fixed at one moment (`Blobs.target`): a copy that must land there whatever happens next.
Target = collections.namedtuple("Target", "generation settings sources s3")


# Bytes written here this recently are never purged (backend/file_library.py `drain`): every writer stores the bytes
# first and adds their `blobs` row in a later write, and a copy or a move lands them again after a purge looked.
FRESH_SECONDS = 120


class Blobs:
    def __init__(self, settings, s3=None):
        self.bucket = settings.blob_bucket
        self.rehearsal = getattr(settings, "rehearsal", False)
        self.directory = settings.blob_dir or settings.db_path.parent / "blobs"
        self._s3 = s3
        self._probe_s3 = s3
        self.settings = settings
        self._sources = blob_s3.Sources(settings)
        # A supplied client is an explicit selection (also used by offline storage tests).
        if s3 is not None:
            self._sources.selected = self._sources.kind
        self._write_failed = False
        self._write_error = ""
        # digest -> when this server last wrote or found those bytes (FRESH_SECONDS); `purging` holds the same lock.
        self._fresh, self._fresh_lock = {}, threading.Lock()
        self._write_lock = threading.Lock()
        self._health_lock = threading.RLock()
        self._probe_lock = threading.Lock()
        self._probe_task = None
        # When the last write check began (monotonic), and whether a refused upload's re-check is running (`_heal`).
        self._checked, self._healing = None, False
        self._s3_failed = set()
        self.copy_status = {"done": 0, "total": 0, "running": False, "error": "", "failed": 0}
        # The server's own storage; `configure` may put a company bucket in its place.
        self._base, self._server_sources = settings, self._sources
        # Read-only places files may still be in: [(Location or settings, Sources)], newest first.
        self._earlier = []
        # {location: monotonic time} of an earlier place that failed other than "no such file": skipped for a while,
        # so one unreachable bucket does not add its timeouts to every read.
        self._earlier_down = {}
        # Bumped by `configure`, so a copy or probe begun for one target never records against another.
        self._generation = 0
        self._loop = self._store = None
        # Whether this disk's files are copied to a new place on their own, and why an upload is refused when the
        # place can't take it (`configure`).
        self._copy_disk, self._paused = True, ""
        # Tico file hosting's edge links (backend/tico_storage.py `edge_link`), while its place holds files.
        self.edge = None
        self._edge_seen = {}
        # Files this server uploaded to Tico file hosting's bucket since it started, so Move existing files does not
        # count them as still to move (after a restart, the move finds them there: tico_storage.copy_one).
        self._landed = set()

    def configure(self, company=None, earlier=(), store=None, checked=False, copy_disk=True, paused=""):
        """Write to `company` ({bucket, prefix, region, keys}), or to the server's own storage when None, from now
        on, without a restart. Files keep their bytes where they are: reads also try each of `earlier` and the
        server's own storage. With `store`, files on this computer's disk are then copied to the new place, unless
        `copy_disk` is False (Tico file hosting: the owner chooses Move existing files, backend/tico_storage.py).
        `checked`: the company's keys were just shown to write there, so writes need not wait for the probe.
        `paused`: the plain reason an upload is refused with when this place can't take it (`{reason}` is why)."""
        target = Location(self._base, **company) if company else self._base
        if company:
            # A client handed in for the server's own bucket (a backup's) cannot sign for the company's.
            self._s3 = self._probe_s3 = None
        places = [Location(self._base, **place) for place in earlier]
        if company and self._base.blob_bucket:
            places.append(self._base)
        here = location_of(target)
        seen, readable = {here}, []
        for place in places:
            if place.blob_bucket and location_of(place) not in seen:
                seen.add(location_of(place))
                readable.append((place, blob_s3.Sources(place)))
        with self._write_lock:
            self.settings, self.bucket = target, target.blob_bucket
            self._sources = blob_s3.Sources(target) if company else self._server_sources
            if checked and company:
                self._sources.selected = "company"
            self._write_failed, self._write_error = False, ""
            self._s3_failed = set()
            self._earlier = readable
            self._copy_disk, self._paused = copy_disk, paused
            self._generation += 1
        if store is not None and self._loop is not None and target.blob_bucket and copy_disk:
            threading.Thread(target=self.storage_loop, args=(store, self._loop), name="tico-storage-copy", daemon=True).start()

    def rekey(self, location, keys):
        """New keys for the place at `location` (Tico file hosting's last an hour), swapped in where it is used: no
        new write check, and a copy or move under way carries on; requests from now on sign with them. Whether a
        place is there."""
        found = False
        with self._write_lock:
            for place, sources in [(self.settings, self._sources), *self._earlier]:
                if isinstance(place, Location) and location_of(place) == location:
                    place.blob_keys = keys
                    sources.forget()
                    found = True
        return found

    def target(self):
        """The place new files go to now. `_upload` and `verify_s3` given it act on that place or not at all: once
        `configure` points new files elsewhere, they refuse rather than act on the new place."""
        with self._write_lock:
            return Target(self._generation, self.settings, self._sources, self._s3)

    def place_at(self, location):
        """(settings, Sources) of the place new files go to or an earlier one at `location`, or None."""
        with self._write_lock:
            for place, sources in [(self.settings, self._sources), *self._earlier]:
                if place.blob_bucket and location_of(place) == location:
                    return place, sources
        return None

    @property
    def s3(self):
        return self._s3 if self._s3 is not None else self._sources.s3

    @property
    def server_storage(self):
        """The server's own credentials, whatever bucket company files use: for the desktop app's builds."""
        return ServerStorage(self)

    def read_s3(self, operation, **options):
        if self._s3 is not None:
            return getattr(self._s3, operation)(**options), self._s3
        return self._sources.read_s3(operation, **options)

    def presign_get(self, s3, bucket, key, expires):
        if self._s3 is not None:
            return s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires)
        return self._sources.presign_get(s3, bucket, key, expires)

    @staticmethod
    def key(digest):
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid content digest")
        return "blobs/" + digest[:2] + "/" + digest

    def s3_key(self, digest, settings=None):
        prefix = (settings or self.settings).blob_prefix
        return (prefix + "/" if prefix else "") + self.key(digest)

    @property
    def location(self):
        return self.bucket + ("/" + self.settings.blob_prefix if self.settings.blob_prefix else "")

    def touch(self, digest):
        """Mark `digest` as just written: a purge waits for it (FRESH_SECONDS)."""
        now = time.monotonic()
        with self._fresh_lock:
            if len(self._fresh) > 50_000:
                self._fresh = {d: t for d, t in self._fresh.items() if now - t < FRESH_SECONDS}
            self._fresh[digest] = now

    @contextlib.contextmanager
    def purging(self, digest):
        """While held, no write marks bytes as fresh: yields whether `digest` was written too recently to purge."""
        with self._fresh_lock:
            seen = self._fresh.get(digest)
            yield seen is not None and time.monotonic() - seen < FRESH_SECONDS

    def put_staged(self, stream, digest, size, content_type="application/octet-stream"):
        """The multipart parser already hashed and spooled this stream."""
        with self._refused():
            return self._put_staged(stream, digest, size, content_type)

    def _refused(self):
        """Turns a storage failure into the plain reason `configure` was given, while one is (credits out)."""
        paused = self._paused

        @contextlib.contextmanager
        def plain():
            try:
                yield
            except Problem as exc:
                if not paused or exc.code != "blob_storage":
                    raise
                raise Problem("storage_paused", paused.format(reason=exc.detail.split(";")[0]), 503, True) from exc
        return plain()

    def _put_staged(self, stream, digest, size, content_type):
        if self.bucket and self.rehearsal:
            raise Problem("rehearsal", "Uploads are off in a rehearsal: attachments live in the company's bucket", 409)
        stream.seek(0)
        if self.bucket:
            self._upload(stream, digest, size, content_type)
        else:
            try:
                self._local(stream, digest)
            except OSError as exc:
                raise Problem("blob_storage", "File storage is unavailable; free disk space and check permissions", 503, True) from exc
        return digest

    def put(self, data, content_type="application/octet-stream"):
        with self._refused():
            return self._put(data, content_type)

    def _put(self, data, content_type):
        if self.bucket and not self.rehearsal:
            digest = hashlib.sha256(data).hexdigest()
            self._upload(io.BytesIO(data), digest, len(data), content_type, small_body=data)
            return digest
        return self._put_stream(io.BytesIO(data), content_type, None, 0, None)

    def put_stream(self, fileobj, content_type="application/octet-stream", max_bytes=None, rate_limit=0, stop=None):
        """Stage and hash in bounded chunks; staging files always leave with the request."""
        with self._refused():
            return self._put_stream(fileobj, content_type, max_bytes, rate_limit, stop)

    def _put_stream(self, fileobj, content_type, max_bytes, rate_limit, stop):
        if self.bucket and self.rehearsal:
            raise Problem("rehearsal", "Uploads are off in a rehearsal: attachments live in the company's bucket", 409)
        try:
            self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            with tempfile.TemporaryFile(dir=self.directory) as stream:
                sha, size = hashlib.sha256(), 0
                while chunk := fileobj.read(1024 * 1024):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise Problem("too_large", f"File exceeds the upload limit of {max_bytes} bytes", 413)
                    sha.update(chunk)
                    stream.write(chunk)
                digest = sha.hexdigest()
                stream.seek(0)
                if self.bucket:
                    self._upload(stream, digest, size, content_type, rate_limit=rate_limit, stop=stop)
                else:
                    self._local(stream, digest)
                return digest
        except OSError as exc:
            detail = "File storage disk is full; free space or configure an S3 bucket and retry" if exc.errno == errno.ENOSPC else "File staging storage is unavailable; check disk space and permissions"
            raise Problem("blob_storage", detail, 503, True) from exc

    def _upload(self, stream, digest, size, content_type, small_body=None, rate_limit=0, stop=None, at=None, whole=False):
        # `whole`: one PutObject with the file's SHA-256 whatever its size (up to S3's 5 GB), so S3 checks every byte
        # on receipt and keeps that checksum (`holds_whole`).
        # Reads may use an unselected source; writes must wait for the probe.
        # Keep one identity for every part and cleanup even if a later check switches it.
        self.touch(digest)
        try:
            self._upload_once(stream, digest, size, content_type, small_body, rate_limit, stop, at, whole)
        finally:
            self.touch(digest)

    def _upload_once(self, stream, digest, size, content_type, small_body, rate_limit, stop, at, whole):
        with self._write_lock:
            if at is not None and at.generation != self._generation:
                raise Problem("storage_changed", "Where new files go changed; the file was not copied", 409)
            if self._write_failed or self._sources.selected is None:
                detail = self._write_error or "S3 storage write check is still pending"
                self._heal()
                raise Problem("blob_storage", detail + "; retry without discarding your file", 503, True)
            s3, kind, sources = self._s3, self._sources.kind, self._sources
            settings, bucket = self.settings, self.bucket
        key = self.s3_key(digest, settings)
        options = dict(Bucket=bucket, Key=key, ContentType=content_type,
                       CacheControl="private, max-age=31536000, immutable",
                       ContentDisposition=disposition(content_type),
                       ServerSideEncryption="AES256", Metadata={"sha256": digest})
        upload_id = None
        try:
            s3 = s3 if s3 is not None else sources.client_for(kind)
            if self._matches_s3(digest, size, settings, bucket, sources):
                self._landing(digest, settings, at)
                return
            if size <= 8 * 1024 ** 2 or whole:
                s3.put_object(**options, Body=small_body if small_body is not None else PacedReader(stream, rate_limit, stop), IfNoneMatch="*",
                                   ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode())
            else:
                upload_id = s3.create_multipart_upload(**options)["UploadId"]
                parts = []
                paced = PacedReader(stream, rate_limit, stop)
                while chunk := paced.read(8 * 1024 ** 2):
                    number = len(parts) + 1
                    result = s3.upload_part(Bucket=bucket, Key=key, UploadId=upload_id,
                                                PartNumber=number, Body=chunk)
                    parts.append({"PartNumber": number, "ETag": result["ETag"]})
                s3.complete_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id,
                                                 MultipartUpload={"Parts": parts}, IfNoneMatch="*")
            self._landing(digest, settings, at)
            if at is None:
                self._visual_copy(digest, size, content_type, settings, small_body, stream)
        except Exception as exc:
            if upload_id:
                try:
                    s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
                except Exception:
                    pass
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("PreconditionFailed", "412"):
                if not self._matches_s3(digest, size, settings, bucket, sources):
                    raise Problem("blob_integrity", "Stored file failed its integrity check", 503) from exc
            else:
                raise Problem("blob_storage", blob_s3.write_error(exc, bucket) + "; retry without discarding your file", 503, True) from exc

    def _matches_s3(self, digest, size, settings=None, bucket=None, sources=None):
        options = dict(Bucket=bucket or self.bucket, Key=self.s3_key(digest, settings), ChecksumMode="ENABLED")
        try:
            if sources is not None and self._s3 is None:
                head = sources.read_s3("head_object", **options)[0]
            else:
                head, _ = self.read_s3("head_object", **options)
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        checksum = base64.b64encode(bytes.fromhex(digest)).decode()
        return head.get("ContentLength") == size and (
            head.get("ChecksumSHA256") == checksum or head.get("Metadata", {}).get("sha256") == digest)

    def _landing(self, digest, settings, at):
        # A new file, not a move's copy (that one counts once it is checked: tico_storage._move).
        found = self.edge
        if at is None and found and location_of(settings) == found["location"]:
            if len(self._landed) > 200_000:
                self._landed.clear()
            self._landed.add((found["location"], digest))

    def _visual_copy(self, digest, size, content_type, settings, small_body, stream):
        # A page or SVG uploaded to Tico file hosting's bucket gets its cleaned copy beside it from the bytes in hand
        # (backend/tico_storage.py `landed`); one that fails is made on its first view. Never fails the upload.
        from . import tico_storage
        found, mime = self.edge, (content_type or "").split(";", 1)[0].strip().lower()
        if not found or location_of(settings) != found["location"] or not tico_storage.visual_kind(mime, size):
            return
        try:
            if small_body is None:
                stream.seek(0)
            data = small_body if small_body is not None else stream.read()
        except Exception:
            return
        tico_storage.landed(self, digest, size, mime, data)

    def holds_whole(self, digest, size, at):
        """Whether the place `at` holds the file whole, by S3's own SHA-256 of what it received (a `whole` upload):
        no byte is read back."""
        head = at.sources.read_s3("head_object", Bucket=at.settings.blob_bucket, Key=self.s3_key(digest, at.settings),
                                  ChecksumMode="ENABLED")[0] if at.s3 is None else at.s3.head_object(
            Bucket=at.settings.blob_bucket, Key=self.s3_key(digest, at.settings), ChecksumMode="ENABLED")
        return (head.get("ContentLength") == size
                and head.get("ChecksumSHA256") == base64.b64encode(bytes.fromhex(digest)).decode())

    def _local(self, source, digest):
        self.touch(digest)
        try:
            return self._local_once(source, digest)
        finally:
            self.touch(digest)

    def _local_once(self, source, digest):
        path = self.directory / self.key(digest)
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := source.read(1024 * 1024):
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                with path.open("rb") as existing:
                    if hashlib.file_digest(existing, "sha256").hexdigest() != digest:
                        raise Problem("blob_integrity", "Stored file failed its integrity check", 503)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.unlink(temporary)

    def verify_s3(self, digest, rate_limit=0, stop=None, at=None):
        """Read the file back whole from the place new files go to (`at`, when the copy was made for that one)."""
        at = at or self.target()
        stream = self._read(at.settings, at.sources, at.s3, digest, {})
        try:
            sha = hashlib.sha256()
            paced = PacedReader(stream, rate_limit, stop)
            while chunk := paced.read(1024 * 1024):
                sha.update(chunk)
            if sha.hexdigest() != digest:
                raise Problem("blob_integrity", "Stored file failed its integrity check", 503)
        finally:
            stream.close()

    def open_range(self, digest, start=0, end=None):
        """Open now (so failures precede headers); iterator closes even on disconnect."""
        key = self.key(digest)
        span = {"Range": f"bytes={start}-{end if end is not None else ''}"} if start or end is not None else {}
        at = self.target()
        if at.settings.blob_bucket and digest not in self._s3_failed:
            try:
                stream = self._read(at.settings, at.sources, at.s3, digest, span)
            except Exception:
                stream = self._open_elsewhere(key, digest, start, span)
        else:
            stream = self._open_elsewhere(key, digest, start, span)
        def chunks():
            remaining = None if end is None else end - start + 1
            try:
                while remaining is None or remaining > 0:
                    chunk = stream.read(min(1024 * 1024, remaining) if remaining is not None else 1024 * 1024)
                    if not chunk:
                        break
                    if remaining is not None:
                        remaining -= len(chunk)
                    yield chunk
            finally:
                stream.close()
        return chunks()

    def _open_elsewhere(self, key, digest, start, span):
        """This computer's copy when there is one, else the first earlier bucket that has the file."""
        if self._earlier and not (self.directory / key).is_file():
            stream = self._open_earlier(digest, span)
            if stream is not None:
                return stream
        return self._open_local(key, start)

    def _open_earlier(self, digest, span):
        """The body from the first earlier place that has the file, or None."""
        for place, sources in self._earlier:
            where = location_of(place)
            if time.monotonic() - self._earlier_down.get(where, -EARLIER_RETRY) < EARLIER_RETRY:
                continue
            try:
                return self._read(place, sources, None, digest, span)
            except Exception as exc:
                # Any answer from S3 means the bucket is reachable: without s3:ListBucket a missing file is
                # AccessDenied, not NoSuchKey. Only a failure to get an answer (timeout, endpoint) marks it down.
                if getattr(exc, "response", None) is None:
                    self._earlier_down[where] = time.monotonic()
        return None

    def _read(self, place, sources, s3, digest, span):
        """The file's body from `place`. Bytes in Tico file hosting's bucket come through the company's address,
        where Tico's edge counts their delivery (backend/tico_storage.py `edge_read`); a read straight from that
        bucket would be neither counted nor cheaper. Any other place is read from its bucket."""
        found = self.edge
        if found and location_of(place) == found["location"]:
            from .tico_storage import edge_read
            return edge_read(found, "/" + self.key(digest), span)
        options = dict(Bucket=place.blob_bucket, Key=self.s3_key(digest, place), **span)
        if s3 is not None:
            return s3.get_object(**options)["Body"]
        return sources.read_s3("get_object", **options)[0]["Body"]

    def _open_local(self, key, start):
        try:
            stream = (self.directory / key).open("rb")
            stream.seek(start)
            return stream
        except OSError as exc:
            raise Problem("blob_storage", "Stored file is unavailable; check S3 access and retained local copies", 503, True) from exc

    def storage_loop(self, store, stop, retry_interval=30 * 60, first_retry=30):
        # `configure` starts a fresh loop for a new target; this one ends when the target changes. While writes fail,
        # or a pass ends on an error, it checks again after `first_retry` seconds, twice as long each time after,
        # up to `retry_interval`. An error never ends it: uploads stay refused until a check passes.
        self._loop, self._store = stop, store
        generation, wait = self._generation, first_retry
        while True:
            failed = False
            try:
                self.copy_local(store, stop)
            except Exception:
                failed = True
                log.exception("File storage check or copy failed; trying again in %s s", wait)
            if not (failed or self._write_failed) or stop.wait(wait) or generation != self._generation:
                return
            wait = min(wait * 2, retry_interval)

    def _heal(self):
        """Start one write check in the background for an upload refused because the last one failed, unless one is
        running or began within HEAL_AFTER seconds: a server heals on its next upload, not the storage loop's next try."""
        store, stop = self._store, self._loop
        if store is None or stop is None or stop.is_set():
            return
        with self._probe_lock:
            if self._healing or (self._checked is not None and time.monotonic() - self._checked < HEAL_AFTER):
                return
            self._healing, self._checked = True, time.monotonic()
        def heal():
            try:
                self.check_write(store, stop)
            except Exception:
                log.exception("File storage write check failed")
            finally:
                self._healing = False
        threading.Thread(target=heal, name="tico-storage-heal", daemon=True).start()

    def copy_local(self, store, stop, interval=0.25):
        if not self.bucket or self.rehearsal or stop.is_set() or not self._copy_disk:
            return
        generation, location = self._generation, self.location
        if not self.check_write(store, stop) or generation != self._generation:
            return
        with store.read() as c:
            rows = c.execute(UNCOPIED_SQL, (location,)).fetchall()
        rows = [r for r in rows if (self.directory / self.key(r["digest"])).is_file()]
        self.copy_status.update(done=0, total=len(rows), running=bool(rows), error="", failed=0)
        self._copy_health(store)
        for row in rows:
            if stop.is_set() or generation != self._generation:
                break
            try:
                path = self.directory / self.key(row["digest"])
                if not path.is_file():            # a deleted file's bytes, removed since the list was read
                    continue
                with path.open("rb") as source:
                    if hashlib.file_digest(source, "sha256").hexdigest() != row["digest"]:
                        raise Problem("blob_integrity", "Local file failed its integrity check", 503)
                    source.seek(0)
                    digest = row["digest"]
                    self._upload(source, digest, path.stat().st_size, row["content_type"],
                                 rate_limit=8 * 1024 ** 2 if interval else 0, stop=stop)
                self.verify_s3(digest, rate_limit=8 * 1024 ** 2 if interval else 0, stop=stop)
                if generation != self._generation:
                    break
                with store.transaction() as c:
                    c.execute("INSERT OR REPLACE INTO blob_locations VALUES(?,?,?)", (digest, location, H.now()))
                self._s3_failed.discard(row["digest"])
                self.copy_status["done"] += 1
            except Exception as exc:
                if stop.is_set() or generation != self._generation:
                    break
                self.copy_status["failed"] += 1
                self._s3_failed.add(row["digest"])
                self.copy_status["error"] = exc.detail if isinstance(exc, Problem) else "S3 copy failed; check bucket access and retry by restarting the server"
            self._copy_health(store)
            if stop.wait(interval):
                break
        self.copy_status["running"] = False
        self._copy_health(store)

    def check_write(self, store, stop=None, timeout=5 * 60):
        # Socket timeouts bound the probe, but cannot interrupt an in-flight SDK call.
        # A daemon lets shutdown leave that call without waiting for the executor.
        if stop is not None and stop.is_set():
            return False
        with self._probe_lock:
            if self._probe_task is None or self._probe_task[0].is_set() or self._probe_task[3] != self._generation:
                if self._probe_task is not None:
                    self._probe_task[2].set()
                done, errors, cancel = threading.Event(), [], threading.Event()
                self._probe_task = done, errors, cancel, self._generation
                self._checked = time.monotonic()
                class ProbeStop:
                    def is_set(self):
                        return cancel.is_set() or (stop is not None and stop.is_set())
                def probe():
                    try:
                        self._check_write(store, ProbeStop())
                    except Exception as exc:
                        errors.append(exc)
                    finally:
                        done.set()
                threading.Thread(target=probe, name="tico-storage-probe", daemon=True).start()
            done, errors, cancel, _ = self._probe_task
        deadline = time.monotonic() + timeout
        while not done.wait(0.05):
            if stop is not None and stop.is_set():
                return False
            if time.monotonic() >= deadline:
                cancel.set()
                with self._write_lock:
                    self._write_failed = True
                    self._write_error = "S3 storage write check timed out; check credential access"
                # Read and update together so a newly returned cleanup ID cannot be overwritten.
                with self._health_lock:
                    with store.read() as c:
                        row = c.execute("SELECT detail FROM service_health WHERE service='blob-s3'").fetchone()
                    previous = json.loads(row["detail"] or "{}") if row else {}
                    pending = {key: previous[key] for key in ("bucket", "key", "upload_id") if key in previous}
                    self._write_health(store, pending, self._write_error)
                return False
        if errors:
            raise errors[0]
        return self._sources.selected is not None and not self._write_failed and not (stop is not None and stop.is_set())

    def _check_write(self, store, stop=None):
        """Check PutObject permission without publishing a file or requiring DeleteObject."""
        from botocore.config import Config

        if stop is not None and stop.is_set():
            return
        # One target throughout, even if `configure` replaces it meanwhile.
        with self._write_lock:
            generation, settings, sources, bucket = self._generation, self.settings, self._sources, self.bucket
            location = self.location
        with store.read() as c:
            row = c.execute("SELECT detail FROM service_health WHERE service='blob-s3'").fetchone()
        previous = json.loads(row["detail"] or "{}") if row else {}
        # Retain at most one unfinished probe; retry cleanup before creating another. One left in a bucket
        # this server no longer writes to is not this target's to clean up (its keys may not reach it).
        pending = previous if previous.get("upload_id") and previous.get("location", location) == location else {}
        error = ""
        candidates = [sources.kind] if self._probe_s3 is not None else blob_s3.kinds(settings)
        for kind in candidates:
            if stop is not None and stop.is_set():
                return
            client = None
            try:
                client = self._probe_s3 or blob_s3.client(settings, kind=kind, config=Config(
                    connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}))
                if stop is not None and stop.is_set():
                    return
                if pending:
                    try:
                        client.abort_multipart_upload(Bucket=pending["bucket"], Key=pending["key"], UploadId=pending["upload_id"])
                    except Exception as exc:
                        if getattr(exc, "response", {}).get("Error", {}).get("Code") != "NoSuchUpload":
                            raise
                    pending = {}
                if stop is not None and stop.is_set():
                    self._write_health(store, pending, self._write_error, location, sources)
                    return
                key = self.s3_key(hashlib.sha256(b"Tico storage write check").hexdigest(), settings)
                result = client.create_multipart_upload(Bucket=bucket, Key=key, ServerSideEncryption="AES256")
                pending = {"bucket": bucket, "key": key, "upload_id": result["UploadId"]}
                # Recorded so a later check can abort it if this one stops here; unrecorded, it is aborted now.
                recorded = self._write_health(store, pending, self._write_error or "S3 storage write check is still pending", location, sources)
                if recorded and stop is not None and stop.is_set():
                    return
                client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=pending["upload_id"])
                pending = {}
                if stop is not None and stop.is_set():
                    self._write_health(store, pending, self._write_error, location, sources)
                    return
                with self._write_lock:
                    sources.selected = kind
                    if generation == self._generation:
                        self._write_failed = False
                        self._write_error = ""
                error = ""
                break
            except Exception as exc:
                error = blob_s3.write_error(exc, pending.get("bucket", bucket), cleanup=bool(pending))
            finally:
                if client is not None and self._probe_s3 is None:
                    client.close()
        with self._write_lock:
            if generation != self._generation:
                return
            self._write_failed = bool(error)
            self._write_error = error
        self._write_health(store, pending, error, location, sources)

    def _write_health(self, store, pending, error, location=None, sources=None):
        """Record the write check's state; whether it was recorded. Best effort: a busy database must neither count as
        an S3 failure nor stop the check, whose result lives in memory (`_write_failed`) either way."""
        detail = {**pending, "location": location or self.location, "error": error, "credentials": (sources or self._sources).kind}
        try:
            with self._health_lock, store.transaction() as c:
                c.execute("INSERT INTO service_health(service,last_succeeded_at,last_error,detail) VALUES(?,?,?,?) "
                          "ON CONFLICT(service) DO UPDATE SET last_succeeded_at=excluded.last_succeeded_at,last_error=excluded.last_error,detail=excluded.detail",
                          ("blob-s3", H.now() if not error else None, H.now() if error else None, json.dumps(detail)))
        except Exception as exc:
            log.warning("File storage check result not recorded: %s", type(exc).__name__)
            return False
        return True

    def _copy_health(self, store):
        with store.transaction() as c:
            c.execute("INSERT INTO service_health(service,last_succeeded_at,last_error,detail) VALUES(?,?,?,?) "
                      "ON CONFLICT(service) DO UPDATE SET last_succeeded_at=excluded.last_succeeded_at,last_error=excluded.last_error,detail=excluded.detail",
                      ("blob-copy", H.now() if not self.copy_status["error"] else None, H.now() if self.copy_status["error"] else None, json.dumps(self.copy_status)))

    def get(self, digest):
        data = b"".join(self.open_range(digest))
        if hashlib.sha256(data).hexdigest() != digest:
            local = self.directory / self.key(digest)
            if self.bucket and local.is_file():
                data = local.read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise Problem("blob_integrity", "Stored file failed its integrity check", 503)
        return data


    def purge(self, digest, kind=None):
        """Remove `digest`'s bytes from every place this server reads them from: this computer's disk, the place new
        files go to and each earlier place, with Tico file hosting's cleaned copy of a page or SVG (`kind`). Only for
        bytes no row needs any more (backend/file_library.py `drain`). Raises, naming each place that refused, so the
        caller tries again; a place that does not hold them is fine."""
        key = self.key(digest)
        (self.directory / key).unlink(missing_ok=True)
        with self._write_lock:
            places = [(self.settings, self._sources, self._s3), *((p, s, None) for p, s in self._earlier)]
        failed = []
        for place, sources, client in places:
            if not place.blob_bucket:
                continue
            keys = [self.s3_key(digest, place)]
            found = self.edge
            if kind and found and location_of(place) == found["location"] \
                    and getattr(self, "visual_ancestors", None) is not None:
                from .tico_storage import _visual, _visual_key
                path = _visual(digest, kind, self.visual_ancestors)[0]
                keys.append(_visual_key(place, path))
                self._edge_seen.pop((found["location"], path), None)
            try:
                s3 = client if client is not None else sources.client_for(sources.kind)
                for each in keys:
                    s3.delete_object(Bucket=place.blob_bucket, Key=each)
            except Exception as exc:
                failed.append(f"{location_of(place)}: {type(exc).__name__}")
        if failed:
            raise RuntimeError("could not remove the bytes from " + ", ".join(failed))


def register(c, who, digest, size, name, content_type="application/octet-stream"):
    # A display name is never used to construct a storage path.
    name = Path(str(name).replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)[:200] or "attachment"
    row = {"id": H.new_id(), "owner": who.actor, "digest": digest, "size": size,
           "name": name, "content_type": content_type, "created_at": H.now()}
    c.execute("INSERT INTO blobs(id,owner,digest,size,name,content_type,created_at) VALUES(:id,:owner,:digest,:size,:name,:content_type,:created_at)", row)
    return brief(row)


def brief(row):
    return {k: row[k] for k in ("id", "name", "size", "content_type")} | {
        "url": "/api/v2/files/" + row["id"]}


def disposition(mime):
    mime = mime.lower().split(";", 1)[0].strip()
    inline = mime in {"image/png", "image/jpeg", "image/gif", "image/webp", "video/mp4", "video/webm",
                      "video/quicktime", "application/pdf", "text/plain", "text/markdown", "text/csv"} or mime.startswith("audio/")
    return "inline" if inline else "attachment"


class PacedReader:
    """Copy traffic has a byte budget, including SDK reads of small objects."""
    def __init__(self, source, rate, stop):
        self.source, self.rate, self.stop = source, rate, stop

    def read(self, size=-1):
        # SDK reads are bounded even if a caller asks for the rest of the stream.
        chunk = self.source.read(1024 * 1024 if size < 0 else size)
        if self.rate and chunk:
            if self.stop and self.stop.wait(len(chunk) / self.rate):
                raise OSError("Copy stopped")
            if not self.stop:
                time.sleep(len(chunk) / self.rate)
        return chunk

    def seek(self, *args):
        return self.source.seek(*args)

    def tell(self):
        return self.source.tell()


def location_of(settings):
    return settings.blob_bucket + ("/" + settings.blob_prefix if settings.blob_prefix else "")


class Location:
    """The server's settings with a company's own bucket, region and keys (backend/company_storage.py) in place of
    its storage settings; everything else reads through to the server's."""
    def __init__(self, base, bucket, prefix="", region="", keys=None):
        self._base = base
        self.blob_bucket, self.blob_prefix, self.blob_region = bucket, prefix, region
        self.blob_endpoint, self.blob_credentials, self.blob_keys = "", "company", keys

    def __getattr__(self, name):
        return getattr(self._base, name)

    def __repr__(self):
        return f"Location({location_of(self)!r})"


class ServerStorage:
    """A Blobs' read access with the server's own credentials (backend/downloads.py), shared with its write probe
    while no company bucket is set."""
    def __init__(self, blobs):
        self.blobs = blobs

    @property
    def s3(self):
        return self.blobs._s3 if self.blobs._s3 is not None else self.blobs._server_sources.s3

    def read_s3(self, operation, **options):
        if self.blobs._s3 is not None:
            return getattr(self.blobs._s3, operation)(**options), self.blobs._s3
        return self.blobs._server_sources.read_s3(operation, **options)

    def presign_get(self, s3, bucket, key, expires):
        if self.blobs._s3 is not None:
            return s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires)
        return self.blobs._server_sources.presign_get(s3, bucket, key, expires)
