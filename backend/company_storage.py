"""A company's own S3 bucket for its files, set by the owner in Settings > General.

With one set, Tico's file store (backend/blobs.py) writes every new file there instead of the server's own storage
(TICO_BLOB_BUCKET or the data volume), and reads from it first. Nothing already stored moves out from under a reader:
a file is read from the bucket in use, then from each bucket used before (newest first), then from the server's own
storage, and files on this computer's disk are copied into the new bucket in the background.

The secret access key is sealed with the credential vault (backend/credentials.py) and is never returned, logged or
kept in an idempotency record. Each save is checked first: Tico writes a small object to the bucket and reads it back
with the keys given, and saves nothing when that fails. A bucket replaced or turned off stays as an earlier place to
read from, with its keys, so its files keep opening.
"""

import hashlib
import re
from typing import Annotated

from fastapi import Request
from pydantic import Field

from . import blob_s3
from . import models as M
from .blobs import Blobs, Location
from .store import H, Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS company_storage(
 id TEXT PRIMARY KEY, bucket TEXT NOT NULL, prefix TEXT NOT NULL DEFAULT '', region TEXT NOT NULL,
 access_key_id TEXT NOT NULL, secret TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, created_by TEXT NOT NULL, retired_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS company_storage_one_active ON company_storage(active) WHERE active=1;
"""
BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d{1,2}$")
PREFIX = re.compile(r"^[A-Za-z0-9!_.*'()/-]{0,200}$")
ACCESS_KEY = re.compile(r"^[A-Z0-9]{16,128}$")
CHECK = b"Tico storage check: this object proves Tico can write and read this bucket.\n"


class StorageSet(M.Contract):
    """The owner's company bucket. `secret_access_key` may be left empty to keep the saved one for the same key ID."""
    bucket: str = Field(min_length=3, max_length=63)
    region: str = Field(min_length=4, max_length=32)
    prefix: str = Field(default="", max_length=200)
    access_key_id: str = Field(min_length=16, max_length=128)
    secret_access_key: Annotated[str, Field(max_length=128, repr=False)] = ""


def _context(row_id):
    return "company_storage:" + row_id


def _location(bucket, prefix):
    return bucket + ("/" + prefix if prefix else "")


def _public(row):
    key = row["access_key_id"]
    return {"bucket": row["bucket"], "prefix": row["prefix"], "region": row["region"],
            "location": _location(row["bucket"], row["prefix"]),
            "access_key_id": "…" + key[-4:], "created_at": row["created_at"], "created_by": row["created_by"],
            **({"retired": row["retired"]} if row["retired"] else {})}


def places(c, vault):
    """(the active place or None, earlier places newest first), each {bucket, prefix, region, keys} for Blobs.configure."""
    active, earlier = None, []
    for row in c.execute("SELECT * FROM company_storage ORDER BY active DESC, COALESCE(retired_at, created_at) DESC"):
        place = {"bucket": row["bucket"], "prefix": row["prefix"], "region": row["region"],
                 "keys": (row["access_key_id"], vault.cipher.unseal(c, _context(row["id"]), row["secret"]))}
        if row["active"]:
            active = place
        else:
            earlier.append(place)
    return active, earlier


def active_location(c):
    """{bucket, prefix, region} in use, or None: what Health reports instead of the server's own storage."""
    row = c.execute("SELECT bucket,prefix,region FROM company_storage WHERE active=1").fetchone()
    return dict(row) if row else None


def view(c, settings, blobs):
    from . import tico_storage
    active = c.execute("SELECT * FROM company_storage WHERE active=1").fetchone()
    earlier = c.execute("SELECT * FROM company_storage WHERE active=0 ORDER BY retired_at DESC").fetchall()
    return {"active": _public(active) if active else None,
            "earlier": [_public(row) for row in earlier],
            "server": {"mode": "s3" if settings.blob_bucket else "local",
                       "location": _location(settings.blob_bucket, settings.blob_prefix) if settings.blob_bucket else ""},
            "copy": {key: blobs.copy_status.get(key, 0) for key in ("done", "total", "failed", "running")},
            "tico": tico_storage.view(c, settings),
            "doc": "https://github.com/ticoteam/tico/blob/main/docs/files.md#a-companys-own-bucket"}


def require_owner(who):
    """The owner, as themselves: never a bot, BotOps or the Assistant acting for them."""
    if who.role != "owner" or who.via or who.task_actor:
        raise Problem("forbidden", "Only the owner sets where the company's files are stored, signed in to Tico", 403)


def validate(body):
    bucket = body.bucket.removeprefix("s3://").strip().strip("/")
    region, prefix = body.region.strip().lower(), body.prefix.strip().strip("/")
    if not BUCKET.match(bucket) or ".." in bucket:
        raise Problem("bucket", "Not an S3 bucket name: lowercase letters, digits, dots and hyphens", 422)
    if not REGION.match(region):
        raise Problem("region", "Not an AWS region, such as us-east-1", 422)
    if not PREFIX.match(prefix) or (prefix and any(part in ("", ".", "..") for part in prefix.split("/"))):
        raise Problem("prefix", "A folder in the bucket: letters, digits and - _ . / only, with no empty or .. parts", 422)
    if not ACCESS_KEY.match(body.access_key_id):
        raise Problem("access_key_id", "Not an AWS access key ID", 422)
    return bucket, region, prefix


def check(settings, bucket, prefix, region, keys):
    """Write a small object with these keys and read it back. Raises Problem naming the S3 error code only:
    SDK messages can carry credential material."""
    from botocore.config import Config
    place = Location(settings, bucket, prefix, region, keys)
    digest = hashlib.sha256(CHECK).hexdigest()
    key = (prefix + "/" if prefix else "") + Blobs.key(digest)
    try:
        s3 = blob_s3.client(place, kind="company", config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}))
    except Exception as exc:
        raise Problem("storage_check", "Tico can't use these keys: " + type(exc).__name__, 422) from None
    try:
        # The same create and abort as the server's write probe (Blobs._check_write): keys that cannot abort
        # would pass a plain write here, then fail that probe and every upload after the save.
        upload_id = None
        try:
            upload_id = s3.create_multipart_upload(Bucket=bucket, Key=key, ServerSideEncryption="AES256")["UploadId"]
            s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        except Exception as exc:
            raise Problem("storage_check", blob_s3.write_error(exc, _location(bucket, prefix), cleanup=upload_id is not None),
                          422) from None
        try:
            s3.put_object(Bucket=bucket, Key=key, Body=CHECK, ContentType="text/plain", ServerSideEncryption="AES256",
                          IfNoneMatch="*")
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") not in ("PreconditionFailed", "412"):
                raise Problem("storage_check", blob_s3.write_error(exc, _location(bucket, prefix)), 422) from None
        try:
            body = s3.get_object(Bucket=bucket, Key=key)["Body"]
            try:
                data = body.read(len(CHECK) + 1)
            finally:
                body.close()
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            reason = code if code in ("AccessDenied", "NoSuchKey", "NoSuchBucket") else type(exc).__name__
            raise Problem("storage_check", f"S3 storage can't read: {reason} on {_location(bucket, prefix)}"
                          + (" (s3:GetObject)" if code == "AccessDenied" else ""), 422) from None
        if data != CHECK:
            raise Problem("storage_check", "S3 storage returned different bytes than Tico wrote: check the bucket", 422)
    finally:
        try:
            s3.close()
        except Exception:
            pass


def save(store, vault, who, body):
    bucket, region, prefix = validate(body)
    with store.read() as c:
        current = c.execute("SELECT * FROM company_storage WHERE active=1").fetchone()
        secret = body.secret_access_key
        if not secret:
            if not current or current["access_key_id"] != body.access_key_id:
                raise Problem("secret_access_key", "Enter the secret access key for this key ID", 422)
            secret = vault.cipher.unseal(c, _context(current["id"]), current["secret"])
    check(store.settings, bucket, prefix, region, (body.access_key_id, secret))
    row_id = H.new_id()
    with store.transaction() as c:
        c.execute("UPDATE company_storage SET active=0, retired_at=? WHERE active=1", (H.now(),))
        # One row per place: the new keys read what the older ones wrote there.
        c.execute("DELETE FROM company_storage WHERE bucket=? AND prefix=?", (bucket, prefix))
        c.execute("INSERT INTO company_storage(id,bucket,prefix,region,access_key_id,secret,active,created_at,created_by) "
                  "VALUES(?,?,?,?,?,?,1,?,?)",
                  (row_id, bucket, prefix, region, body.access_key_id, vault.cipher.seal(c, _context(row_id), secret),
                   H.now(), who.actor))
        H.event(c, who.actor, "storage.set", _location(bucket, prefix),
                {"region": region, "access_key_id": "…" + body.access_key_id[-4:],
                 "replaced": _location(current["bucket"], current["prefix"]) if current else ""})


def turn_off(store, who):
    with store.transaction() as c:
        current = c.execute("SELECT * FROM company_storage WHERE active=1").fetchone()
        if not current:
            raise Problem("not_set", "The company's files already use the server's own storage", 409)
        c.execute("UPDATE company_storage SET active=0, retired_at=? WHERE id=?", (H.now(), current["id"]))
        H.event(c, who.actor, "storage.off", _location(current["bucket"], current["prefix"]), {})


def apply(store, vault, blobs, copy=True, checked=False):
    """Point the file store at what is saved now: the company's own places, and Tico file hosting's when HQ handed
    one over (backend/tico_storage.py), which takes new files while it is on."""
    from . import tico_storage
    with store.read() as c:
        active, earlier = places(c, vault)
        hosting, edge = tico_storage.place(c, vault), tico_storage.edge(c, vault)
    active, earlier, copy_disk, paused = tico_storage.arrange(active, earlier, hosting)
    blobs.configure(active, earlier, store if copy else None, checked=checked, copy_disk=copy_disk,
                    paused=tico_storage.PAUSED if paused else "")
    blobs.edge = edge


def startup(store, vault, blobs):
    """Before the copy loop starts (backend/app.py lifespan), so it copies to the saved place."""
    from . import tico_storage
    tico_storage.startup(store, vault, blobs)
    with store.read() as c:
        saved = (c.execute("SELECT 1 FROM company_storage LIMIT 1").fetchone()
                 or c.execute("SELECT 1 FROM tico_storage WHERE bucket!=''").fetchone())
    if not saved:
        return
    try:
        apply(store, vault, blobs, copy=False)
    except Problem as exc:
        # The vault key is missing or wrong: new files stay on the server's storage until it is restored.
        import logging
        logging.getLogger("tico.storage").warning("Company storage not applied: %s", exc.detail)


def for_backup(store, blobs):
    """`blobs` (a backup's reader, backend/operations.py and manage.py) pointed at the company's bucket and the
    earlier ones too, so a bundle can read every file. Unchanged when none was ever set."""
    with store.read() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_storage'").fetchone():
            return blobs
        hosted = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tico_storage'").fetchone()
        if not c.execute("SELECT 1 FROM company_storage LIMIT 1").fetchone() and not (
                hosted and c.execute("SELECT 1 FROM tico_storage WHERE bucket!=''").fetchone()):
            return blobs
    from .credentials import Vault
    apply(store, Vault(store), blobs, copy=False)
    return blobs


def install(app, store, auth):
    vault, blobs = app.state.vault, app.state.blobs

    @app.get("/api/v2/system/storage")
    def storage_read(request: Request):
        """Where the company's files are stored: the owner's bucket, earlier ones still read, and the server's own."""
        who = request.state.identity
        auth.domain(who)
        require_owner(who)
        with store.read() as c:
            return view(c, store.settings, blobs)

    @app.put("/api/v2/system/storage")
    def storage_set(request: Request, body: StorageSet):
        """Store the company's files in its own S3 bucket from now on. Checked by writing and reading back first."""
        who = request.state.identity
        auth.domain(who)
        require_owner(who)
        if store.settings.rehearsal or store.settings.demo:
            raise Problem("rehearsal", "Storage can't be changed in a rehearsal or the demo", 409)
        save(store, vault, who, body)
        apply(store, vault, blobs, checked=True)
        with store.read() as c:
            return view(c, store.settings, blobs)

    @app.delete("/api/v2/system/storage")
    def storage_off(request: Request):
        """New files go back to the server's own storage; files already in the bucket are still read from it."""
        who = request.state.identity
        auth.domain(who)
        require_owner(who)
        if store.settings.rehearsal or store.settings.demo:
            raise Problem("rehearsal", "Storage can't be changed in a rehearsal or the demo", 409)
        turn_off(store, who)
        apply(store, vault, blobs)
        with store.read() as c:
            return view(c, store.settings, blobs)
