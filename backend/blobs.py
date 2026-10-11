"""Immutable binary storage. Only opaque, authorized IDs are exposed over HTTP."""

import base64
import io
import errno
import json
import time
import hashlib
import os
import re
import tempfile
import threading
from pathlib import Path

from .store import H, Problem
from . import blob_s3


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
        self._write_lock = threading.Lock()
        self._health_lock = threading.RLock()
        self._probe_lock = threading.Lock()
        self._probe_task = None
        self._s3_failed = set()
        self.copy_status = {"done": 0, "total": 0, "running": False, "error": "", "failed": 0}

    @property
    def s3(self):
        return self._s3 if self._s3 is not None else self._sources.s3

    def read_s3(self, operation, **options):
        if self._s3 is not None:
            return getattr(self._s3, operation)(**options), self._s3
        return self._sources.read_s3(operation, **options)

    @staticmethod
    def key(digest):
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid content digest")
        return "blobs/" + digest[:2] + "/" + digest

    def s3_key(self, digest):
        prefix = self.settings.blob_prefix
        return (prefix + "/" if prefix else "") + self.key(digest)

    @property
    def location(self):
        return self.bucket + ("/" + self.settings.blob_prefix if self.settings.blob_prefix else "")

    def put_staged(self, stream, digest, size, content_type="application/octet-stream"):
        """The multipart parser already hashed and spooled this stream."""
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
        if self.bucket and not self.rehearsal:
            digest = hashlib.sha256(data).hexdigest()
            self._upload(io.BytesIO(data), digest, len(data), content_type, small_body=data)
            return digest
        return self.put_stream(io.BytesIO(data), content_type)

    def put_stream(self, fileobj, content_type="application/octet-stream", max_bytes=None, rate_limit=0, stop=None):
        """Stage and hash in bounded chunks; staging files always leave with the request."""
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

    def _upload(self, stream, digest, size, content_type, small_body=None, rate_limit=0, stop=None):
        # Reads may use an unselected source; writes must wait for the probe.
        # Keep one identity for every part and cleanup even if a later check switches it.
        with self._write_lock:
            if self._write_failed or self._sources.selected is None:
                detail = self._write_error or "S3 storage write check is still pending"
                raise Problem("blob_storage", detail + "; retry without discarding your file", 503, True)
            s3, kind = self._s3, self._sources.kind
        key = self.s3_key(digest)
        options = dict(Bucket=self.bucket, Key=key, ContentType=content_type,
                       CacheControl="private, max-age=31536000, immutable",
                       ContentDisposition=disposition(content_type),
                       ServerSideEncryption="AES256", Metadata={"sha256": digest})
        upload_id = None
        try:
            s3 = s3 if s3 is not None else self._sources.client_for(kind)
            if self._matches_s3(digest, size):
                return
            if size <= 8 * 1024 ** 2:
                s3.put_object(**options, Body=small_body if small_body is not None else PacedReader(stream, rate_limit, stop), IfNoneMatch="*",
                                   ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode())
            else:
                upload_id = s3.create_multipart_upload(**options)["UploadId"]
                parts = []
                paced = PacedReader(stream, rate_limit, stop)
                while chunk := paced.read(8 * 1024 ** 2):
                    number = len(parts) + 1
                    result = s3.upload_part(Bucket=self.bucket, Key=key, UploadId=upload_id,
                                                PartNumber=number, Body=chunk)
                    parts.append({"PartNumber": number, "ETag": result["ETag"]})
                s3.complete_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id,
                                                 MultipartUpload={"Parts": parts}, IfNoneMatch="*")
        except Exception as exc:
            if upload_id:
                try:
                    s3.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
                except Exception:
                    pass
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("PreconditionFailed", "412"):
                if not self._matches_s3(digest, size):
                    raise Problem("blob_integrity", "Stored file failed its integrity check", 503) from exc
            else:
                raise Problem("blob_storage", blob_s3.write_error(exc, self.bucket) + "; retry without discarding your file", 503, True) from exc

    def _matches_s3(self, digest, size):
        try:
            head, _ = self.read_s3("head_object", Bucket=self.bucket, Key=self.s3_key(digest), ChecksumMode="ENABLED")
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        checksum = base64.b64encode(bytes.fromhex(digest)).decode()
        return head.get("ContentLength") == size and (
            head.get("ChecksumSHA256") == checksum or head.get("Metadata", {}).get("sha256") == digest)

    def _local(self, source, digest):
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

    def verify_s3(self, digest, rate_limit=0, stop=None):
        response, _ = self.read_s3("get_object", Bucket=self.bucket, Key=self.s3_key(digest))
        stream = response["Body"]
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
        if self.bucket and digest not in self._s3_failed:
            try:
                args = dict(Bucket=self.bucket, Key=self.s3_key(digest))
                if start or end is not None:
                    args["Range"] = f"bytes={start}-{end if end is not None else ''}"
                response, _ = self.read_s3("get_object", **args)
                stream = response["Body"]
            except Exception:
                stream = self._open_local(key, start)
        else:
            stream = self._open_local(key, start)
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

    def _open_local(self, key, start):
        try:
            stream = (self.directory / key).open("rb")
            stream.seek(start)
            return stream
        except OSError as exc:
            raise Problem("blob_storage", "Stored file is unavailable; check S3 access and retained local copies", 503, True) from exc

    def storage_loop(self, store, stop, retry_interval=30 * 60):
        self.copy_local(store, stop)
        while self._write_failed and not stop.wait(retry_interval):
            self.copy_local(store, stop)

    def copy_local(self, store, stop, interval=0.25):
        if not self.bucket or self.rehearsal or stop.is_set():
            return
        if not self.check_write(store, stop):
            return
        with store.read() as c:
            rows = list(c.execute("SELECT digest,MAX(content_type) AS content_type FROM blobs GROUP BY digest"))
            known = {r[0] for r in c.execute("SELECT digest FROM blob_locations WHERE bucket=?", (self.location,))}
        rows = [r for r in rows if r["digest"] not in known and (self.directory / self.key(r["digest"])).is_file()]
        self.copy_status.update(done=0, total=len(rows), running=bool(rows), error="", failed=0)
        self._copy_health(store)
        for row in rows:
            if stop.is_set():
                break
            try:
                path = self.directory / self.key(row["digest"])
                with path.open("rb") as source:
                    if hashlib.file_digest(source, "sha256").hexdigest() != row["digest"]:
                        raise Problem("blob_integrity", "Local file failed its integrity check", 503)
                    source.seek(0)
                    digest = row["digest"]
                    self._upload(source, digest, path.stat().st_size, row["content_type"],
                                 rate_limit=8 * 1024 ** 2 if interval else 0, stop=stop)
                self.verify_s3(digest, rate_limit=8 * 1024 ** 2 if interval else 0, stop=stop)
                with store.transaction() as c:
                    c.execute("INSERT OR REPLACE INTO blob_locations VALUES(?,?,?)", (digest, self.location, H.now()))
                self._s3_failed.discard(row["digest"])
                self.copy_status["done"] += 1
            except Exception as exc:
                if stop.is_set():
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
            if self._probe_task is None or self._probe_task[0].is_set():
                done, errors, cancel = threading.Event(), [], threading.Event()
                self._probe_task = done, errors, cancel
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
            done, errors, cancel = self._probe_task
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
                        row = c.execute("SELECT detail_json FROM service_health WHERE service='blob-s3'").fetchone()
                    previous = json.loads(row["detail_json"] or "{}") if row else {}
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
        with store.read() as c:
            row = c.execute("SELECT detail_json FROM service_health WHERE service='blob-s3'").fetchone()
        previous = json.loads(row["detail_json"] or "{}") if row else {}
        # Retain at most one unfinished probe; retry cleanup before creating another.
        pending = previous if previous.get("upload_id") else {}
        error = ""
        candidates = [self._sources.kind] if self._probe_s3 is not None else blob_s3.kinds(self.settings)
        for kind in candidates:
            if stop is not None and stop.is_set():
                return
            client = None
            try:
                client = self._probe_s3 or blob_s3.client(self.settings, kind=kind, config=Config(
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
                    self._write_health(store, pending, self._write_error)
                    return
                key = self.s3_key(hashlib.sha256(b"Tico storage write check").hexdigest())
                result = client.create_multipart_upload(Bucket=self.bucket, Key=key, ServerSideEncryption="AES256")
                pending = {"bucket": self.bucket, "key": key, "upload_id": result["UploadId"]}
                self._write_health(store, pending, self._write_error or "S3 storage write check is still pending")
                if stop is not None and stop.is_set():
                    return
                client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=pending["upload_id"])
                pending = {}
                if stop is not None and stop.is_set():
                    self._write_health(store, pending, self._write_error)
                    return
                with self._write_lock:
                    self._sources.selected = kind
                    self._write_failed = False
                    self._write_error = ""
                error = ""
                break
            except Exception as exc:
                error = blob_s3.write_error(exc, pending.get("bucket", self.bucket), cleanup=bool(pending))
            finally:
                if client is not None and self._probe_s3 is None:
                    client.close()
        with self._write_lock:
            self._write_failed = bool(error)
            self._write_error = error
        self._write_health(store, pending, error)

    def _write_health(self, store, pending, error):
        detail = {**pending, "location": self.location, "error": error, "credentials": self._sources.kind}
        with self._health_lock, store.transaction() as c:
            c.execute("INSERT INTO service_health(service,last_success,last_error,detail_json) VALUES(?,?,?,?) "
                      "ON CONFLICT(service) DO UPDATE SET last_success=excluded.last_success,last_error=excluded.last_error,detail_json=excluded.detail_json",
                      ("blob-s3", H.now() if not error else None, H.now() if error else None, json.dumps(detail)))

    def _copy_health(self, store):
        with store.transaction() as c:
            c.execute("INSERT INTO service_health(service,last_success,last_error,detail_json) VALUES(?,?,?,?) "
                      "ON CONFLICT(service) DO UPDATE SET last_success=excluded.last_success,last_error=excluded.last_error,detail_json=excluded.detail_json",
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


def register(c, who, digest, size, name, content_type="application/octet-stream"):
    # A display name is never used to construct a storage path.
    name = Path(str(name).replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)[:200] or "attachment"
    row = {"id": H.new_id(), "owner": who.actor, "digest": digest, "size": size,
           "name": name, "content_type": content_type, "created": H.now()}
    c.execute("INSERT INTO blobs(id,owner,digest,size,name,content_type,created) VALUES(:id,:owner,:digest,:size,:name,:content_type,:created)", row)
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
