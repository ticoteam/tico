"""Consistent SQLite snapshots with verified object-store upload and restore checks."""

import hashlib
import base64
import json
import os
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from . import hubdb
from .blobs import Blobs
from .config import Settings


class RecoveryBlobs(Blobs):
    """Independent immutable copies, outside the live and expiring DB prefixes."""

    @staticmethod
    def key(digest):
        return "recovery/" + Blobs.key(digest)


def iter_blob_inventory(database, unverified_in=None):
    """All registered bytes, including recoverable deleted sources, from one snapshot.

    With a bucket name, only the blobs no earlier bundle has verified in that
    bucket's recovery prefix.
    """
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as c:
        rows = c.execute("SELECT digest,min(size),max(size),count(*) FROM blobs "
                         + ("WHERE digest NOT IN (SELECT sha256 FROM backup_verified_blobs WHERE bucket=?) "
                            if unverified_in else "") + "GROUP BY digest ORDER BY digest",
                         (unverified_in,) if unverified_in else ())
        for digest, low, high, references in rows:
            key = RecoveryBlobs.key(digest)
            if not isinstance(low, int) or low < 0 or high != low:
                raise ValueError("Conflicting or invalid sizes in the source blob inventory")
            yield {"sha256": digest, "size": low, "references": references, "key": key}


def blob_inventory(database):
    return list(iter_blob_inventory(database))


def inventory_summary(database):
    # The complete inventory already lives in the checksummed SQLite snapshot.
    # Bind a compact digest to it, instead of loading millions of audio-chunk
    # entries into a JSON manifest or the small EC2 instance's memory.
    digest, count, size, references = hashlib.sha256(), 0, 0, 0
    for item in iter_blob_inventory(database):
        digest.update(json.dumps(item, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        count += 1
        size += item["size"]
        references += item["references"]
    return {"sha256": digest.hexdigest(), "count": count, "bytes": size, "references": references}


def immutable_put(s3, bucket, key, data, digest):
    return s3.put_object(Bucket=bucket, Key=key, Body=data, IfNoneMatch="*",
                         ServerSideEncryption="AES256",
                         ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode())


def verify_stored(s3, bucket, key, size, digest):
    """Read back what S3 holds: the object's length and the SHA-256 it verified on receipt.

    S3 rejects a PUT whose bytes do not match the declared checksum, so its stored
    checksum is an independent record of the object. Downloading the whole database
    to hash it again proved the same thing while starving the API's only worker.
    """
    head = s3.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
    expected = base64.b64encode(bytes.fromhex(digest)).decode()
    if head.get("ContentLength") != size or head.get("ChecksumSHA256") != expected:
        raise RuntimeError("Stored object does not match the verified local copy: " + key)


def verify_objects(items, verify):
    """Overlap object-store latency with bounded workers and payload memory.

    Each worker retains source and downloaded bytes. Small batches cap the sum
    of advertised object sizes at 32 MiB; oversized objects run alone. Inventory
    stays streamed, and every result must succeed before the caller can publish.
    """
    with ThreadPoolExecutor(max_workers=4) as pool:
        batch, size = [], 0
        def finish():
            for future in batch:
                future.result()
            batch.clear()
        for item in items:
            if batch and (len(batch) >= 4 or size + item['size'] > 32 * 1024 * 1024):
                finish()
                size = 0
            if item['size'] > 32 * 1024 * 1024:
                verify(item)
            else:
                batch.append(pool.submit(verify, item))
                size += item['size']
        finish()


def record_verified_blobs(source, bucket, items):
    """The bundle's one live-database write: which recovery copies never need re-reading."""
    if not items:
        return
    from .store import H, Store
    with Store(Settings(db_path=Path(source))).transaction() as c:
        c.executemany("INSERT OR IGNORE INTO backup_verified_blobs VALUES(?,?,?,?)",
                      [(bucket, item["sha256"], item["key"], H.now()) for item in items])


# The local key files beside the database (backend/credentials.py, github_app.py, slack_app.py): a database restored
# without them cannot open the credentials, the GitHub App and directory sync secrets, or the Slack tokens.
KEY_FILES = ("credential.key", "github-app.key", "slack.key")
KEY_BYTES = 32


def key_file_copy(key, name):
    return key + ".keys/" + name


def read_key_files(key_dir):
    """{name: bytes} for the key files present in `key_dir`. One that is not 32 bytes is not a key: refused by name,
    never carried, so a bundle never holds a copy that opens nothing."""
    found = {}
    for name in KEY_FILES:
        path = Path(key_dir) / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        if len(data) != KEY_BYTES:
            raise RuntimeError(name + " beside the database is not a valid key (32 bytes); the backup was not published")
        found[name] = data
    return found


def upload_bundle(source, bucket, key, s3, blobs, key_dir=None):
    """Publish a manifest only after verifying the DB and every blob not verified before.

    No source deletions; the only live-database write records verified recovery
    copies. A failed upload leaves unreferenced backup objects, but cannot
    advertise a complete recovery point.

    With `key_dir` (the hand-run `manage.py backup`: the database's directory) the key files present there are copied
    beside the database as <key>.keys/<name>, read back, and listed in the manifest by name and SHA-256, so
    restore_bundle can put them back. The key bytes are never in the manifest or the report.
    """
    if not key.startswith("backups/") or not key.endswith(".sqlite"):
        raise ValueError("Use a unique backups/*.sqlite object key")
    keys = read_key_files(key_dir) if key_dir is not None else {}
    recovery = RecoveryBlobs(Settings(db_path=Path(source), blob_bucket=bucket), s3)
    # Keep the snapshot workspace beside the source database on the retained data
    # volume instead of relying on the instance's small PrivateTmp filesystem.
    with tempfile.TemporaryDirectory(
            prefix=".tico-bundle-", dir=Path(source).resolve().parent) as temporary:
        local = Path(temporary) / "snapshot.sqlite"
        expected = snapshot(source, local)
        inventory = inventory_summary(local)
        with local.open("rb") as stream:
            immutable_put(s3, bucket, key, stream, expected["sha256"])
        verify_stored(s3, bucket, key, expected["bytes"], expected["sha256"])
        # Initialize lazy S3 clients on the caller thread before sharing them.
        if blobs.bucket:
            blobs.s3
        verified = []
        def verify(item):
            data = blobs.get(item["sha256"])
            if len(data) != item["size"]:
                raise RuntimeError("Source blob size differs from its database record")
            try:
                immutable_put(s3, bucket, item["key"], data, item["sha256"])
            except Exception as error:
                if getattr(error, "response", {}).get("Error", {}).get("Code") not in ("PreconditionFailed", "412"):
                    raise
            # Always read the recovery namespace, never merely trust upload metadata.
            if recovery.get(item["sha256"]) != data:
                raise RuntimeError("Recovery blob failed independent download verification")
            verified.append(item)
        # A recovery copy read back once is immutable and versioned, so later bundles
        # skip it; the daily isolated restore still reads every one. Blobs verified
        # before a failure stay recorded.
        try:
            verify_objects(iter_blob_inventory(local, unverified_in=bucket), verify)
        finally:
            record_verified_blobs(source, bucket, verified)
        key_files = {}
        for name, data in keys.items():
            digest = hashlib.sha256(data).hexdigest()
            copy = key_file_copy(key, name)
            immutable_put(s3, bucket, copy, data, digest)
            with s3.get_object(Bucket=bucket, Key=copy)["Body"] as stream:
                if stream.read() != data:
                    raise RuntimeError(name + " failed independent download verification")
            key_files[name] = {"key": copy, "sha256": digest}
        manifest = {"format": "tico-backup-v1", "database": {"key": key, **expected}, "objects": inventory}
        if key_files:
            # Older restore tools ignore this field; they restore the database and blobs as before.
            manifest["key_files"] = key_files
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        immutable_put(s3, bucket, key + ".manifest.json", encoded, hashlib.sha256(encoded).hexdigest())
        with s3.get_object(Bucket=bucket, Key=key + ".manifest.json")["Body"] as stream:
            if stream.read() != encoded:
                raise RuntimeError("Backup manifest failed independent download verification")
        report = {"bucket": bucket, "key": key, "verified": True, **expected,
                  "objects": inventory["count"], "object_bytes": inventory["bytes"],
                  "objects_verified": len(verified), "manifest_sha256": hashlib.sha256(encoded).hexdigest()}
        if key_dir is not None:
            report["key_files"] = sorted(key_files)
        return report


def restore_bundle(bucket, key, destination, s3):
    """Restore a complete bundle to a new DB and explicit immutable blob destination.

    The caller must fence the old backend first. The restored database is not
    created until the snapshot, manifest inventory, and all binary bytes verify.
    """
    target = Path(destination.db_path).resolve()
    if target.exists():
        raise ValueError("Restore requires a new database path; existing data is never overwritten")
    if not destination.blob_bucket and destination.blob_dir is None:
        raise ValueError("Specify the restore blob directory or bucket explicitly")
    if not key.startswith("backups/") or not key.endswith(".sqlite"):
        raise ValueError("Use the exact backups/*.sqlite key for a complete recovery point")
    with s3.get_object(Bucket=bucket, Key=key + ".manifest.json")["Body"] as stream:
        encoded = stream.read(64 * 1024 * 1024 + 1)
    if len(encoded) > 64 * 1024 * 1024:
        raise ValueError("Backup manifest is too large for this restore tool")
    manifest = json.loads(encoded)
    if manifest.get("format") != "tico-backup-v1" or manifest.get("database", {}).get("key") != key:
        raise ValueError("Not a complete Tico backup manifest for the requested database")
    keys = fetch_key_files(manifest, bucket, key, s3, target.parent)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".tico-restore-", dir=target.parent) as temporary:
        downloaded = Path(temporary) / "snapshot.sqlite"
        s3.download_file(bucket, key, str(downloaded))
        report = inspect(downloaded)
        if report["integrity"] != ["ok"] or report["foreign_key_violations"]:
            raise RuntimeError("Downloaded database failed integrity validation")
        if {"key": key, **report} != manifest["database"]:
            raise RuntimeError("Database does not match the backup manifest")
        inventory = inventory_summary(downloaded)
        if inventory != manifest.get("objects"):
            raise RuntimeError("Backup object inventory does not match the database")
        recovery = RecoveryBlobs(Settings(db_path=target, blob_bucket=bucket), s3)
        output = Blobs(destination, s3)
        def verify(item):
            data = recovery.get(item["sha256"])
            if len(data) != item["size"]:
                raise RuntimeError("Recovery blob size does not match the database")
            output.put(data)
            if output.get(item["sha256"]) != data:
                raise RuntimeError("Restored blob failed independent verification")
        verify_objects(iter_blob_inventory(downloaded), verify)
        result = restore_snapshot(downloaded, target)
        return {**result, "objects": inventory["count"], "object_bytes": inventory["bytes"],
                "manifest_sha256": hashlib.sha256(encoded).hexdigest(), "verified": True,
                "key_files": place_key_files(keys, target.parent)}


def fetch_key_files(manifest, bucket, key, s3, directory):
    """The bundle's key files ({name: bytes}), each checked against the manifest and 32 bytes long, before anything is
    restored. A different key already in `directory` is refused by name: it may open another database, and it is never
    overwritten."""
    listed = manifest.get("key_files") or {}
    if not isinstance(listed, dict) or not set(listed) <= set(KEY_FILES):
        raise ValueError("The backup manifest lists unexpected key files")
    found = {}
    for name, entry in sorted(listed.items()):
        if not isinstance(entry, dict) or entry.get("key") != key_file_copy(key, name):
            raise ValueError("The backup manifest lists " + name + " in an unexpected place")
        with s3.get_object(Bucket=bucket, Key=entry["key"])["Body"] as stream:
            data = stream.read(KEY_BYTES + 1)
        if len(data) != KEY_BYTES or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            raise RuntimeError(name + " in the backup does not match its manifest")
        here = Path(directory) / name
        if here.exists() and here.read_bytes() != data:
            raise ValueError(here.name + " already exists beside the restore target and is a different key; restore "
                             "to an empty directory (nothing was restored)")
        found[name] = data
    return found


def place_key_files(keys, directory):
    """Writes each key file beside the restored database, mode 0600, without overwriting: {name: restored|kept}."""
    placed = {}
    for name, data in keys.items():
        path = Path(directory) / name
        if path.exists():
            placed[name] = "kept"          # the same key (fetch_key_files refused a different one)
            continue
        fd, temporary = tempfile.mkstemp(prefix=".restore-key-", dir=directory)    # mode 0600
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(temporary, 0o600)
            os.link(temporary, path)       # fails rather than replace a key that appeared meanwhile
        finally:
            os.unlink(temporary)
        placed[name] = "restored"
    return placed


def checksum(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as c:
        integrity = [row[0] for row in c.execute("PRAGMA integrity_check")]
        foreign_keys = c.execute("PRAGMA foreign_key_check").fetchall()
        names = [row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = {name: c.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in names}
        return {"integrity": integrity, "foreign_key_violations": len(foreign_keys), "tables": counts,
                "bytes": Path(path).stat().st_size, "sha256": checksum(path)}


def copy_pages(source, destination):
    """Copy one consistent page image of a live database into a new private file."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or any(Path(str(destination) + suffix).exists() for suffix in ("", "-wal", "-shm", "-journal")):
        raise ValueError("Snapshot destination must be a new, distinct file")
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
        # Exclusive creation also prevents a concurrent restore from replacing
        # this target. Database credentials/history must not be world-readable.
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(destination)) as dst:
            # Keep one stable source snapshot for the copy. Yielding every 256 pages lets a
            # frequently written WAL database repeatedly restart the online backup; on the
            # production database that rewrote gigabytes without completing a 644 MB copy.
            # WAL readers do not block writers, so copying all remaining pages in one step is
            # both the faster and the more predictable online-backup boundary.
            src.backup(dst, pages=-1)
    return destination


def snapshot(source, destination):
    destination = copy_pages(source, destination)
    report = inspect(destination)
    if report["integrity"] != ["ok"] or report["foreign_key_violations"]:
        raise RuntimeError("Snapshot validation failed; preserve it for investigation")
    return report


def checkpoint(source, destination):
    """Exact deployment rollback aid: the page copy without the full integrity scan.

    integrity_check reads every page again and takes minutes on the production
    database while the API is stopped. The daily verified backup already runs it
    on the same data; the checkpoint only needs to be a complete, openable copy.
    """
    destination = copy_pages(source, destination)
    with closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as c:
        pages, page_size = (c.execute("PRAGMA " + name).fetchone()[0] for name in ("page_count", "page_size"))
        tables = c.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    if pages * page_size != destination.stat().st_size or not tables:
        raise RuntimeError("Checkpoint copy is incomplete; preserve it for investigation")
    return {"bytes": pages * page_size, "tables": tables}


VACUUM_FREE_BYTES = 256 * 1024 * 1024


def compact(database):
    """Drop the idempotency rows past the retry window, then reclaim the file space if it matters.

    Runs at deploy activation on the fenced database, after the rollback checkpoint
    is taken. A VACUUM rewrites the whole file, so it happens only when a quarter of
    the file or 256 MB is free pages. Litestream is stopped at that point and takes
    a fresh snapshot of the rewritten file when it comes back.
    """
    from .store import Store, sweep_idempotency
    database = Path(database).resolve()
    before = database.stat().st_size
    store = Store(Settings(db_path=database))
    store.initialize()  # builds the created index here, not on the API's 30-second startup path
    deleted = sweep_idempotency(store)
    # VACUUM writes its temporary image where SQLite keeps temp files: the data volume, not root.
    os.environ.setdefault("SQLITE_TMPDIR", str(database.parent))
    with closing(hubdb.leave_wal_for_litestream(sqlite3.connect(database, timeout=30))) as c:
        page_size, free = (c.execute("PRAGMA " + name).fetchone()[0] for name in ("page_size", "freelist_count"))
        free_bytes = free * page_size
        vacuumed = free_bytes > VACUUM_FREE_BYTES or free_bytes * 4 > before
        if vacuumed:
            c.execute("VACUUM")
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {"deleted": deleted, "free_bytes": free_bytes, "vacuumed": vacuumed,
            "bytes_before": before, "bytes_after": database.stat().st_size}


def upload_snapshot(source, bucket, key, s3):
    """Database-only diagnostic helper, not a complete production recovery point.

    Production operations use upload_bundle; restore_bundle rejects this older
    manifest format. No success until an independent DB download verifies.
    """
    with tempfile.TemporaryDirectory(
            prefix=".tico-backup-", dir=Path(source).resolve().parent) as temporary:
        local = Path(temporary) / "snapshot.sqlite"
        expected = snapshot(source, local)
        s3.upload_file(str(local), bucket, key, ExtraArgs={"Metadata": {"sha256": expected["sha256"]}})
        restored = Path(temporary) / "verified.sqlite"
        s3.download_file(bucket, key, str(restored))
        actual = inspect(restored)
        if actual != expected:
            raise RuntimeError("Uploaded backup failed independent download/restore verification")
        s3.put_object(Bucket=bucket, Key=key + ".manifest.json", Body=json.dumps(expected).encode(),
                      ContentType="application/json")
        return {"bucket": bucket, "key": key, "verified": True, **expected}


def restore_snapshot(source, destination):
    """Restore to a new file and fence all pre-disaster machine/execution credentials.

    The old server must be stopped/fenced first by the operator. Revoking every restored
    machine prevents credentials revoked *after* the backup from silently becoming valid.
    """
    report = snapshot(source, destination)
    from .config import Settings
    from .store import H, Store
    store = Store(Settings(db_path=Path(destination)))
    store.initialize(seed_market=False)
    with store.transaction() as c:
        now = H.now()
        active = c.execute("SELECT count(*) FROM attempts WHERE state IN ('leased','running')").fetchone()[0]
        c.execute("UPDATE jobs SET state='uncertain' WHERE state IN ('running','input')")
        c.execute("UPDATE jobs SET state='queued' WHERE state='leased'")
        c.execute("UPDATE attempts SET state='expired',finished_at=?,lease_until=? WHERE state IN ('leased','running')", (now, now))
        from .run_triggers import SETTLE_SQL
        # Its runner will never report these runs' tokens: 0 with the reason, as hubdb.turn_finish records it.
        c.execute(f"UPDATE turns SET finished_at=?,exit='interrupted',{SETTLE_SQL},input_tokens=0,cached_tokens=0,"
                  "output_tokens=0,est_cost_usd=0,usage_reason='no_result' WHERE finished_at IS NULL", (now,))
        c.execute("UPDATE assignments SET generation=generation+1,updated_at=?,updated_by='keeper'", (now,))
        c.execute("UPDATE runners SET revoked_at=coalesce(revoked_at,?)", (now,))
        c.execute("UPDATE enrollments SET expires_at=?", (now,))
        c.execute("UPDATE bots SET token_hash=NULL")
        # An old backup must not resurrect a credential grant revoked after it was taken.
        c.execute("UPDATE credential_grants SET revoked_at=coalesce(revoked_at,?),revoked_by='recovery'", (now,))
        c.execute("UPDATE service_jobs SET state='queued',runner_id=NULL,token_hash=NULL,lease_until=NULL,updated_at=? WHERE state='running'", (now,))
        H.event(c, H.KEEPER, "recovery.fenced", "database", {"source_sha256": report["sha256"], "active_attempts": active})
    return {"source": report, "restored": inspect(destination), "reenrollment_required": True, "fenced_attempts": active}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Restore a verified Tico database and all registered binary objects")
    parser.add_argument("--bucket", required=True, help="Private backup bucket")
    parser.add_argument("--key", required=True, help="Exact backups/*.sqlite recovery point (manifest required)")
    parser.add_argument("--database", required=True, type=Path, help="New destination database; never overwritten")
    storage = parser.add_mutually_exclusive_group(required=True)
    storage.add_argument("--blob-dir", type=Path, help="Explicit local directory for restored binary objects")
    storage.add_argument("--blob-bucket", help="Destination private S3 bucket for restored live blobs")
    parser.add_argument("--profile", help="AWS profile; omit on EC2 to use the instance role")
    parser.add_argument("--region", default="us-west-2")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--old-backend-fenced", action="store_true",
                      help="Confirm the old backend is stopped and cannot accept writes or schedule work")
    mode.add_argument("--isolated-drill", action="store_true",
                      help="Confirm the restore target is isolated and will not serve production traffic or runners")
    args = parser.parse_args()
    import boto3
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    settings = Settings(db_path=args.database, blob_dir=args.blob_dir, blob_bucket=args.blob_bucket or "")
    result = restore_bundle(args.bucket, args.key, settings, session.client("s3"))
    print(json.dumps({**result, "mode": "isolated-drill" if args.isolated_drill else "recovery"}, indent=2))


if __name__ == "__main__":
    main()
