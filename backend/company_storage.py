"""The company's own file storage: an S3 or S3-compatible bucket an owner or admin chooses in Settings.

While a bucket is chosen, new file bytes go there instead of the built-in store (backend/blobs.py), under a
content-addressed key, with server-side encryption and no ACL, so the bucket stays private. People still open files
only through Tico's authorized routes; the bucket's name and keys are never in a file answer.

Each bucket the company has used is one `storage_targets` row and is never deleted, so bytes written there stay
readable after the company switches back to built-in storage or to another bucket. `blob_targets` records which
target holds each digest; a digest with no record is read from the built-in store as before. Nothing moves existing
bytes: switching changes where new bytes go.

Keys are sealed with the credential vault's data key (backend/credentials.py `CredentialCipher.seal`) and leave the
server only inside the S3 client. No answer, event or log carries the secret, and error text is built from S3's
error code, never from SDK messages, which can quote request material.

A bucket that cannot be written fails the write with a plain 503: falling back to the built-in store would scatter
one company's files across two places nobody chose. Health names the problem (service `company-storage`).
"""

import ipaddress
import json
import re
import sqlite3
import threading
import uuid
from urllib.parse import urlparse

from fastapi import Request

from . import models as M
from .store import H, Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS storage_targets(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('s3')), bucket TEXT NOT NULL,
 region TEXT NOT NULL DEFAULT '', prefix TEXT NOT NULL DEFAULT '', endpoint TEXT NOT NULL DEFAULT '',
 credentials TEXT NOT NULL CHECK(credentials IN ('keys','role')), access_key_id TEXT NOT NULL DEFAULT '',
 secret_sealed TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 0,
 created TEXT NOT NULL, created_by TEXT NOT NULL, updated TEXT NOT NULL, updated_by TEXT NOT NULL, retired TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS storage_targets_one_active ON storage_targets(active) WHERE active=1;
CREATE TABLE IF NOT EXISTS blob_targets(
 digest TEXT NOT NULL, target_id TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(digest, target_id));
"""
SERVICE = "company-storage"
BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
REGION = re.compile(r"[a-z0-9-]{0,32}")
PREFIX = re.compile(r"[A-Za-z0-9!_.*'()/-]{0,200}")
KEY_ID = re.compile(r"[\x21-\x7e]{3,128}")
PROBE_BODY = b"Tico storage connection test"
# Digests uploaded to a company target and not yet recorded: `blobs.register` records them in the transaction that
# registers the file, so the record commits with the row that needs it. Bounded; a digest that misses its record
# is still found by `CompanyStorage.open`, which tries the company's buckets before giving up.
_WRITTEN = {}
_WRITTEN_LIMIT = 10_000
_WRITTEN_LOCK = threading.Lock()


def remember(digest, target_id):
    with _WRITTEN_LOCK:
        _WRITTEN.pop(digest, None)
        _WRITTEN[digest] = target_id
        while len(_WRITTEN) > _WRITTEN_LIMIT:
            _WRITTEN.pop(next(iter(_WRITTEN)))


def record(c, digest):
    """Called where a blob row is inserted: the target the bytes went to, when it is one of this database's."""
    with _WRITTEN_LOCK:
        target_id = _WRITTEN.get(digest)
    if target_id:
        c.execute("INSERT OR IGNORE INTO blob_targets(digest,target_id,created) "
                  "SELECT ?,id,? FROM storage_targets WHERE id=?", (digest, H.now(), target_id))


def make_client(target, secret):
    """An S3 client for one target. Tests replace this with a fake; nothing here reaches the network until called."""
    import boto3
    from botocore.config import Config
    options = {"region_name": target["region"] or ("us-east-1" if target["endpoint"] else None),
               "endpoint_url": target["endpoint"] or None,
               "config": Config(signature_version="s3v4", connect_timeout=5, read_timeout=30,
                                retries={"max_attempts": 2})}
    if target["credentials"] == "keys":
        options.update(aws_access_key_id=target["access_key_id"], aws_secret_access_key=secret)
    return boto3.client("s3", **options)


def _code(exc):
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code") or "")


def plain_error(exc, bucket, step):
    """What went wrong, in words a person can act on. Only the S3 error code or the exception's type is used."""
    code, kind = _code(exc), type(exc).__name__
    action = {"writing": "s3:PutObject", "reading": "s3:GetObject", "deleting": "s3:DeleteObject"}.get(step, "")
    if code in ("NoSuchBucket",):
        return f"The bucket {bucket} does not exist"
    if code in ("AccessDenied", "403", "AllAccessDisabled"):
        return f"Access denied while {step} in {bucket}" + (f": allow {action}" if action else "")
    if code == "InvalidAccessKeyId":
        return "The access key ID is not recognised"
    if code == "SignatureDoesNotMatch":
        return "The secret access key does not match the access key ID"
    if code in ("ExpiredToken", "InvalidToken", "TokenRefreshRequired"):
        return "The credentials have expired"
    if code in ("AuthorizationHeaderMalformed", "PermanentRedirect", "IllegalLocationConstraintException", "301"):
        found = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPHeaders", {}).get("x-amz-bucket-region")
        return f"The bucket {bucket} is in another region" + (f": set the region to {found}" if found else "")
    if code == "RequestTimeTooSkewed":
        return "The server's clock is too far off for S3 to accept its requests"
    if kind in ("NoCredentialsError", "PartialCredentialsError"):
        return "The server has no AWS role credentials"
    if kind in ("EndpointConnectionError", "ConnectTimeoutError", "ConnectionClosedError", "ReadTimeoutError",
                "TimeoutError", "ConnectionError"):
        return f"Could not reach the storage service while {step} in {bucket}"
    if kind == "Mismatch":
        return f"The bucket {bucket} returned different bytes than were written"
    return f"Storage failed while {step} in {bucket} ({code or kind})"


class Mismatch(Exception):
    pass


def _endpoint(value):
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    url = urlparse(value)
    host = url.hostname or ""
    if url.username or url.password or url.query or url.fragment or url.path not in ("", "/") or not host:
        raise Problem("endpoint", "The endpoint is a bare address such as https://<account>.r2.cloudflarestorage.com", 422)
    loopback = host == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    # A local MinIO speaks plain HTTP; anything that leaves the computer must be https.
    if url.scheme != "https" and not (url.scheme == "http" and loopback):
        raise Problem("endpoint", "The endpoint must use https (plain http only for localhost)", 422)
    return url.scheme + "://" + url.netloc


def _server_buckets(settings, env=None):
    """The buckets the server keeps its own data in. The server's role must never be pointed at them for a company."""
    import os
    env = os.environ if env is None else env
    found = {settings.blob_bucket}
    backup = urlparse(env.get("TICO_BACKUP_URL", ""))
    if backup.scheme == "s3":
        found.add(backup.netloc)
    return {b for b in found if b}


def clean(body, settings):
    """The validated fields of a storage choice, or a 422 naming the first bad one."""
    bucket = (body.bucket or "").strip()
    if not BUCKET.fullmatch(bucket) or ".." in bucket:
        raise Problem("bucket", "Enter the bucket's name: 3 to 63 lowercase letters, digits, dots or hyphens", 422)
    region = (body.region or "").strip().lower()
    if not REGION.fullmatch(region):
        raise Problem("region", "Enter a region such as us-east-1 (or leave it empty)", 422)
    prefix = (body.prefix or "").strip().strip("/")
    if not PREFIX.fullmatch(prefix) or any(part in ("", ".", "..") for part in prefix.split("/") if prefix):
        raise Problem("prefix", "The folder may use letters, digits and ! _ . * ' ( ) - separated by /", 422)
    credentials = body.credentials or "keys"
    if credentials == "role":
        if not settings.company_storage_role:
            raise Problem("credentials", "This server does not offer its own AWS role for company storage; enter an "
                                         "access key instead", 422)
        if bucket in _server_buckets(settings):
            raise Problem("bucket", "That bucket holds this server's own data; choose a bucket of your own", 422)
    key_id = (body.access_key_id or "").strip()
    if credentials == "keys" and not KEY_ID.fullmatch(key_id):
        raise Problem("access_key_id", "Enter the access key ID", 422)
    return {"kind": "s3", "bucket": bucket, "region": region, "prefix": prefix, "endpoint": _endpoint(body.endpoint),
            "credentials": credentials, "access_key_id": key_id if credentials == "keys" else ""}


def mask(key_id):
    return ("…" + key_id[-4:]) if key_id else ""


class CompanyStorage:
    """Where new bytes go and where recorded bytes are read from, for one store."""

    def __init__(self, store, cipher=None):
        self.store = store
        self._cipher = cipher
        self._clients = {}
        self._lock = threading.Lock()
        self._health_threads = []
        self._last_health = None

    @property
    def cipher(self):
        if self._cipher is None:
            from .credentials import Vault
            self._cipher = Vault(self.store).cipher
        return self._cipher

    @property
    def rehearsal(self):
        return getattr(self.store.settings, "rehearsal", False)

    # ------------------------------------------------------------------ targets
    def _rows(self, sql, args=()):
        # A database from before company storage (a hand-run backup of an old copy) has no targets.
        try:
            with self.store.read() as c:
                return [dict(row) for row in c.execute(sql, args)]
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise

    def active(self):
        rows = self._rows("SELECT * FROM storage_targets WHERE active=1")
        return rows[0] if rows else None

    def secret(self, target):
        if target["credentials"] != "keys":
            return ""
        with self.store.read() as c:
            return self.cipher.unseal(c, "storage_targets:" + target["id"], target["secret_sealed"])

    def client(self, target):
        # Rebuilt when the target changes (a rotated key), never shared between targets.
        stamp = (target["id"], target["updated"])
        with self._lock:
            cached = self._clients.get(target["id"])
            if cached and cached[0] == stamp:
                return cached[1]
        made = make_client(target, self.secret(target))
        with self._lock:
            self._clients[target["id"]] = (stamp, made)
        return made

    @staticmethod
    def key(target, digest):
        from .blobs import Blobs
        return (target["prefix"] + "/" if target["prefix"] else "") + Blobs.key(digest)

    # ------------------------------------------------------------------ writes
    def upload(self, target, stream, digest, size, content_type):
        """Put one blob in the company's bucket, or raise a 503 that names the problem. Bytes already there (the same
        digest and size) are not sent again; content addressing means a key never holds other bytes."""
        from .blobs import disposition
        if self.rehearsal:
            raise Problem("rehearsal", "Uploads are off in a rehearsal: new files go to the company's own bucket", 409)
        bucket, key = target["bucket"], self.key(target, digest)
        try:
            s3 = self.client(target)
            if not self._present(s3, bucket, key, size):
                extra = {"ContentType": content_type, "CacheControl": "private, max-age=31536000, immutable",
                         "ContentDisposition": disposition(content_type), "Metadata": {"sha256": digest}}
                # S3-compatible stores (R2, MinIO) encrypt at rest by their own settings and may refuse the header.
                if not target["endpoint"]:
                    extra["ServerSideEncryption"] = "AES256"
                stream.seek(0)
                s3.upload_fileobj(stream, bucket, key, ExtraArgs=extra)
                if not self._present(s3, bucket, key, size):
                    raise Mismatch()
        except Problem:
            raise
        except Exception as exc:
            error = plain_error(exc, bucket, "writing")
            self._health(target, error)
            raise Problem("company_storage", "Your company's file storage can't save this file: " + error
                          + ". Nothing was saved; fix the storage in Settings and retry", 503, True) from exc
        self._health(target, "")
        remember(digest, target["id"])

    @staticmethod
    def _present(s3, bucket, key, size):
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            # Without s3:ListBucket a missing key answers 403, not 404: treat both as "not there yet".
            if _code(exc) in ("404", "NoSuchKey", "NotFound", "403", "AccessDenied"):
                return False
            raise
        return head.get("ContentLength") == size

    # ------------------------------------------------------------------ reads
    def located(self, digest):
        return self._rows("SELECT t.* FROM blob_targets b JOIN storage_targets t ON t.id=b.target_id "
                          "WHERE b.digest=? ORDER BY t.active DESC, b.created DESC", (digest,))

    def candidates(self):
        """Every target, the active one first: where an unrecorded digest may still be."""
        return self._rows("SELECT * FROM storage_targets ORDER BY active DESC, updated DESC")

    def open(self, target, digest, start=0, end=None):
        args = {"Bucket": target["bucket"], "Key": self.key(target, digest)}
        if start or end is not None:
            args["Range"] = f"bytes={start}-{end if end is not None else ''}"
        return self.client(target).get_object(**args)["Body"]

    # ------------------------------------------------------------------ connection test
    def test(self, target, secret=None):
        """Write, read back and delete one small object under the prefix. Returns "" or the plain problem."""
        if self.rehearsal:
            raise Problem("rehearsal", "A rehearsal does not reach the company's bucket", 409)
        key = (target["prefix"] + "/" if target["prefix"] else "") + "tico-connection-test/" + uuid.uuid4().hex
        bucket, step = target["bucket"], "connecting"
        try:
            s3 = make_client(target, secret if secret is not None else self.secret(target))
            step = "writing"
            extra = {} if target["endpoint"] else {"ServerSideEncryption": "AES256"}
            s3.put_object(Bucket=bucket, Key=key, Body=PROBE_BODY, ContentType="text/plain", **extra)
            step = "reading"
            body = s3.get_object(Bucket=bucket, Key=key)["Body"]
            try:
                if body.read() != PROBE_BODY:
                    raise Mismatch()
            finally:
                body.close()
            step = "deleting"
            s3.delete_object(Bucket=bucket, Key=key)
        except Problem:
            raise
        except Exception as exc:
            return plain_error(exc, bucket, step)
        return ""

    def check_active(self):
        """At start: whether the active bucket still accepts writes, so Health says so before anyone uploads."""
        target = self.active()
        if target is None or self.rehearsal:
            return
        try:
            error = self.test(target)
        except Problem as exc:
            error = exc.detail
        self._health(target, error, wait=True)

    # ------------------------------------------------------------------ health
    def _health(self, target, error, wait=False):
        """Record the bucket's state for Health when it changes. A write may run inside another transaction, which
        holds the database's write lock, so the record is written from its own thread once that one commits."""
        state = (target["id"], error)
        if state == self._last_health and not wait:
            return
        self._last_health = state
        detail = {"target_id": target["id"], "bucket": target["bucket"], "error": error}
        def write():
            try:
                with self.store.transaction() as c:
                    c.execute("INSERT INTO service_health(service,last_success,last_error,detail_json) VALUES(?,?,?,?) "
                              "ON CONFLICT(service) DO UPDATE SET last_success=excluded.last_success,"
                              "last_error=excluded.last_error,detail_json=excluded.detail_json",
                              (SERVICE, None if error else H.now(), H.now() if error else None, json.dumps(detail)))
            except Exception:
                self._last_health = None     # try again with the next write
        if wait:
            write()
            return
        thread = threading.Thread(target=write, name="tico-company-storage-health", daemon=True)
        self._health_threads = [t for t in self._health_threads if t.is_alive()] + [thread]
        thread.start()

    def flush(self, timeout=30):
        """Wait for the Health records already started (tests and shutdown)."""
        for thread in list(self._health_threads):
            thread.join(timeout)


def health_check(c):
    """The Health problem for the active bucket, or None."""
    row = c.execute("SELECT detail_json FROM service_health WHERE service=?", (SERVICE,)).fetchone()
    detail = json.loads(row["detail_json"] or "{}") if row else {}
    if not detail.get("error"):
        return None
    active = c.execute("SELECT id FROM storage_targets WHERE active=1").fetchone()
    if not active or active["id"] != detail.get("target_id"):
        return None
    return "Your company's file storage can't be used: " + detail["error"] + ". New files can't be saved until it is fixed"


# ---------------------------------------------------------------------- settings API
def require_admin(auth, who):
    """The owner or an admin, as themselves: never a bot, BotOps or the Assistant acting for them."""
    auth.domain(who)
    if who.role not in ("owner", "human") or who.via or who.task_actor or not auth.bot_admin(who):
        raise Problem("forbidden", "Only the owner or an admin changes where the company's files are stored", 403)


def view(c, settings):
    row = c.execute("SELECT * FROM storage_targets WHERE active=1").fetchone()
    status = c.execute("SELECT * FROM service_health WHERE service=?", (SERVICE,)).fetchone()
    detail = json.loads(status["detail_json"] or "{}") if status else {}
    current = bool(row and detail.get("target_id") == row["id"])
    out = {"mode": "s3" if row else "builtin", "role_available": bool(settings.company_storage_role),
           "bucket": "", "region": "", "prefix": "", "endpoint": "", "credentials": "keys",
           "access_key_id": "", "secret_set": False, "updated": None, "updated_by": None,
           "status": {"state": ("error" if detail.get("error") else "ok") if current else "unknown",
                      "error": detail.get("error", "") if current else "",
                      "checked": (status["last_error"] or status["last_success"]) if current and status else None},
           "files": c.execute("SELECT COUNT(DISTINCT digest) FROM blob_targets").fetchone()[0]}
    if row:
        out.update(bucket=row["bucket"], region=row["region"], prefix=row["prefix"], endpoint=row["endpoint"],
                   credentials=row["credentials"], access_key_id=mask(row["access_key_id"]),
                   secret_set=bool(row["secret_sealed"]), updated=row["updated"], updated_by=row["updated_by"])
    return out


def _candidate(c, fields):
    """The stored target the choice updates: the same bucket, folder and endpoint."""
    row = c.execute("SELECT * FROM storage_targets WHERE bucket=? AND prefix=? AND endpoint=? ORDER BY active DESC "
                    "LIMIT 1", (fields["bucket"], fields["prefix"], fields["endpoint"])).fetchone()
    return dict(row) if row else None


def install(app, store, auth):
    storage = app.state.company_storage

    def resolve(body):
        """The choice as a target plus its secret: a secret not sent is the saved one for the same key ID."""
        fields = clean(body, store.settings)
        with store.read() as c:
            existing = _candidate(c, fields)
        secret = body.secret_access_key or ""
        if fields["credentials"] == "keys" and not secret:
            if existing and existing["credentials"] == "keys" and existing["access_key_id"] == fields["access_key_id"] \
                    and existing["secret_sealed"]:
                secret = storage.secret(existing)
            else:
                raise Problem("secret_access_key", "Enter the secret access key", 422)
        if len(secret) > 256:
            raise Problem("secret_access_key", "The secret access key is too long", 422)
        return fields, existing, secret

    @app.get("/api/v2/team/storage")
    def read_storage(request: Request):
        """Where new company files are stored. The secret is never returned; the key ID only by its last characters."""
        require_admin(auth, request.state.identity)
        with store.read() as c:
            return view(c, store.settings)

    @app.put("/api/v2/team/storage")
    def write_storage(request: Request, body: M.CompanyStorageChoice):
        """Choose built-in storage or a bucket. A bucket is saved only after a connection test passes."""
        who = request.state.identity
        require_admin(auth, who)
        if store.settings.rehearsal:
            raise Problem("rehearsal", "Storage settings can't change in a rehearsal", 409)
        if body.mode == "builtin":
            with store.transaction() as c:
                c.execute("UPDATE storage_targets SET active=0,retired=?,updated=?,updated_by=? WHERE active=1",
                          (H.now(), H.now(), who.actor))
                H.event(c, who.actor, "storage.settings", "company", {"mode": "builtin"})
                return view(c, store.settings)
        fields, existing, secret = resolve(body)
        error = storage.test({**fields, "id": "", "secret_sealed": ""}, secret)
        if error:
            raise Problem("storage_test", "Not saved. " + error, 422)
        with store.transaction() as c:
            now = H.now()
            c.execute("UPDATE storage_targets SET active=0,retired=? WHERE active=1", (now,))
            target_id = existing["id"] if existing else H.new_id()
            sealed = storage.cipher.seal(c, "storage_targets:" + target_id, secret) if fields["credentials"] == "keys" else ""
            if existing:
                c.execute("UPDATE storage_targets SET region=?,credentials=?,access_key_id=?,secret_sealed=?,active=1,"
                          "retired=NULL,updated=?,updated_by=? WHERE id=?",
                          (fields["region"], fields["credentials"], fields["access_key_id"], sealed, now, who.actor,
                           target_id))
            else:
                c.execute("INSERT INTO storage_targets(id,kind,bucket,region,prefix,endpoint,credentials,access_key_id,"
                          "secret_sealed,active,created,created_by,updated,updated_by) VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?,?)",
                          (target_id, "s3", fields["bucket"], fields["region"], fields["prefix"], fields["endpoint"],
                           fields["credentials"], fields["access_key_id"], sealed, now, who.actor, now, who.actor))
            H.event(c, who.actor, "storage.settings", "company",
                    {"mode": "s3", "bucket": fields["bucket"], "prefix": fields["prefix"],
                     "endpoint": fields["endpoint"], "credentials": fields["credentials"]})
            # The test just passed: Health starts from a clean state for this bucket.
            c.execute("INSERT INTO service_health(service,last_success,last_error,detail_json) VALUES(?,?,NULL,?) "
                      "ON CONFLICT(service) DO UPDATE SET last_success=excluded.last_success,last_error=NULL,"
                      "detail_json=excluded.detail_json",
                      (SERVICE, now, json.dumps({"target_id": target_id, "bucket": fields["bucket"], "error": ""})))
            storage._last_health = (target_id, "")
            return view(c, store.settings)

    @app.post("/api/v2/team/storage/test")
    def test_storage(request: Request, body: M.CompanyStorageTest):
        """Write, read and delete one small object with these settings (or the saved ones when none are sent)."""
        who = request.state.identity
        require_admin(auth, who)
        key = request.headers.get("idempotency-key") or ""
        if not 1 <= len(key) <= 200:
            raise Problem("idempotency_key", "Provide an Idempotency-Key of 1–200 characters", 422)
        if body.bucket:
            fields, _, secret = resolve(body)
            target = {**fields, "id": "", "secret_sealed": ""}
        else:
            target = storage.active()
            if target is None:
                raise Problem("not_found", "No company bucket is set: files use the built-in storage", 404)
            secret = None
        error = storage.test(target, secret)
        if not body.bucket:
            storage._health(target, error, wait=True)
        return {"ok": not error, "error": error}


def install_lifespan(app):
    """At start, check the active bucket once in the background."""
    storage = getattr(app.state, "company_storage", None)
    if storage is not None and not storage.rehearsal:
        threading.Thread(target=storage.check_active, name="tico-company-storage-check", daemon=True).start()

