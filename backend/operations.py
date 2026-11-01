"""Scheduled backup and health reporting; never runs an inference client."""

import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from .backup import restore_bundle, upload_bundle
from .blobs import Blobs
from .config import Settings
from .store import H, Store, encode

RESTORE_ROOT = Path("/var/lib/tico/restore-checks")
TUNNEL_CONFIG = Path("/etc/tico/tunnel.yml")


def report_health(store, service, *, success=None, error=None, detail=None):
    with store.transaction() as c:
        c.execute("INSERT INTO service_health VALUES(?,?,?,?) ON CONFLICT(service) DO UPDATE SET "
                  "last_succeeded_at=coalesce(excluded.last_succeeded_at,service_health.last_succeeded_at),"
                  "last_error=excluded.last_error,detail=excluded.detail",
                  (service, success, error, encode(detail or {})))


def metric(client, name, value, unit="Count"):
    scope = os.environ.get("TICO_METRIC_SCOPE", "default")
    client.put_metric_data(Namespace="Tico", MetricData=[{"MetricName": name, "Value": value, "Unit": unit,
                           "Dimensions": [{"Name": "Application", "Value": "tico"},
                                          {"Name": "Scope", "Value": scope}]}])


def api_healthy(settings):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/healthz", timeout=2) as response:
            raw = response.read(4097)
        if len(raw) > 4096:
            return False
        data = json.loads(raw)
        return (data.get("ok") is True and data.get("service") == "tico"
                and (not settings.release_id or data.get("release") == settings.release_id))
    except (OSError, ValueError, urllib.error.HTTPError):
        return False


def replication_healthy():
    try:
        with urllib.request.urlopen("http://127.0.0.1:9091/metrics", timeout=2) as response:
            raw = response.read(65537)
        return 0 < len(raw) <= 65536
    except (OSError, urllib.error.HTTPError):
        return False


def tunnel_healthy():
    """Treat an intentionally unconfigured tunnel as healthy; require it once configured."""
    if not TUNNEL_CONFIG.is_file() or TUNNEL_CONFIG.is_symlink():
        return True
    try:
        result = subprocess.run(["systemctl", "is-active", "--quiet", "tico-tunnel.service"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=3)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def retention_tier(store, bucket, now):
    """The first point of a month or a day gets its long-lived prefix; a same-day extra
    (a deploy's post-activation backup, a manual run) lands under backups/hourly/ and
    the bucket lifecycle expires it after two days."""
    with store.read() as c:
        rows = {r["service"]: dict(r) for r in c.execute("SELECT * FROM service_health WHERE service IN ('backup-daily','backup-monthly')")}
    for tier, length in (("monthly", 7), ("daily", 10)):
        row = rows.get("backup-" + tier, {})
        detail = json.loads(row.get("detail") or "{}")
        if detail.get("bucket") != bucket or (row.get("last_succeeded_at") or "")[:length] != now[:length]:
            return tier
    return "hourly"


def backup(store, s3, cloudwatch, bucket):
    now = H.now()
    tier = retention_tier(store, bucket, now)
    key = "backups/" + tier + "/" + now.replace(":", "-") + "-" + uuid.uuid4().hex + ".sqlite"
    try:
        # The key files beside the database go with it, as with `manage.py backup`: a database restored without them
        # cannot open the credentials, GitHub App, directory sync or Slack secrets (backend/backup.py).
        # Files may be in the company's own bucket (backend/company_storage.py): the bundle reads them there.
        from .company_storage import for_backup
        report = upload_bundle(store.settings.db_path, bucket, key, s3, for_backup(store, Blobs(store.settings, s3)),
                               key_dir=store.settings.db_path.parent)
    except Exception as error:
        report_health(store, "backup", error=type(error).__name__)
        try:
            metric(cloudwatch, "BackupSucceeded", 0)
        except Exception:
            pass
        raise
    detail = {"bucket": bucket, "key": key, "sha256": report["sha256"], "tier": tier,
              "database_bytes": report["bytes"],
              "objects": report["objects"], "object_bytes": report["object_bytes"],
              "objects_verified": report.get("objects_verified", 0), "manifest_sha256": report["manifest_sha256"],
              "key_files": report.get("key_files", [])}
    report_health(store, "backup", success=H.now(), detail=detail)
    # The first successful point of a month also satisfies that day's point;
    # longer retention covers both without duplicating all source verification.
    for category in (("monthly", "daily") if tier == "monthly" else ("daily",) if tier == "daily" else ()):
        report_health(store, "backup-" + category, success=now, detail=detail)
    metric(cloudwatch, "BackupSucceeded", 1)
    return {**report, "tier": tier}


RESTORE_CHECK_HOUR = 3  # UTC; the daily verified backup runs at 03:00


def restore_check_due(store, now=None):
    """The timer fires hourly; the check runs once per day after 03:00 UTC. A run that a deploy
    stopped, or one that failed, is simply due again at the next hourly tick (a burst
    of deploys over the 03:30 slot left the check a day and a half stale with no error)."""
    now = H.parse_ts(now or H.now())
    boundary = now.replace(hour=RESTORE_CHECK_HOUR, minute=0, second=0, microsecond=0)
    if boundary > now:
        boundary -= timedelta(days=1)
    with store.read() as c:
        row = c.execute("SELECT last_succeeded_at FROM service_health WHERE service='restore-check'").fetchone()
    last = H.parse_ts(row["last_succeeded_at"]) if row and row["last_succeeded_at"] else None
    return last is None or last < boundary


def restore_check(store, s3, cloudwatch, bucket):
    with store.read() as c:
        row = c.execute("SELECT last_succeeded_at,last_error,detail FROM service_health WHERE service='backup'").fetchone()
    try:
        if not row or not row["last_succeeded_at"] or row["last_error"]:
            raise RuntimeError("No successful current backup is available for an isolated restore check")
        detail = json.loads(row["detail"] or "{}")
        if detail.get("bucket") != bucket:
            raise RuntimeError("Latest backup belongs to a different storage bucket")
        database_bytes = detail.get("database_bytes", store.settings.db_path.stat().st_size)
        object_bytes = detail.get("object_bytes")
        if (not isinstance(database_bytes, int) or database_bytes < 1
                or not isinstance(object_bytes, int) or object_bytes < 0):
            raise RuntimeError("Latest backup has invalid capacity metadata")
        RESTORE_ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
        free = shutil.disk_usage(RESTORE_ROOT).free
        required = database_bytes * 2 + object_bytes + 5 * 1024 ** 3
        if required > free:
            raise RuntimeError("Not enough retained-volume capacity for an isolated restore check")
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="restore-", dir=RESTORE_ROOT) as temporary:
            root = Path(temporary)
            settings = Settings(db_path=root / "hub.sqlite", blob_dir=root / "blobs")
            report = restore_bundle(bucket, detail["key"], settings, s3)
            if not report.get("verified") or not report.get("reenrollment_required"):
                raise RuntimeError("Isolated restore did not pass its recovery fencing checks")
        result = {"bucket": bucket, "key": detail["key"], "source_sha256": report["source"]["sha256"],
                  "objects": report["objects"], "object_bytes": report["object_bytes"],
                  "elapsed_ms": round((time.monotonic() - started) * 1000), "ephemeral_copy_removed": True}
    except Exception as error:
        report_health(store, "restore-check", error=type(error).__name__)
        try:
            metric(cloudwatch, "RestoreCheckSucceeded", 0)
        except Exception:
            pass
        raise
    report_health(store, "restore-check", success=H.now(), detail=result)
    metric(cloudwatch, "RestoreCheckSucceeded", 1)
    return {**result, "verified": True}


def health(store, cloudwatch):
    with store.read() as c:
        rows = {r["service"]: dict(r) for r in c.execute("SELECT * FROM service_health")}
    age_metrics = {"scheduler": "SchedulerAgeSeconds", "backup": "BackupAgeSeconds",
                   "restore-check": "RestoreCheckAgeSeconds"}
    for service, name in age_metrics.items():
        row = rows.get(service, {})
        last = H.parse_ts(row.get("last_succeeded_at"))
        age = max(0, (H.parse_ts(H.now()) - last).total_seconds()) if last else 1_000_000
        metric(cloudwatch, name, age, "Seconds")
    free = shutil.disk_usage(store.settings.db_path.parent).free
    metric(cloudwatch, "DataDiskFreeBytes", free, "Bytes")
    api_ok, replication_ok, tunnel_ok = api_healthy(store.settings), replication_healthy(), tunnel_healthy()
    metric(cloudwatch, "ApiHealthy", int(api_ok))
    metric(cloudwatch, "ReplicationHealthy", int(replication_ok))
    metric(cloudwatch, "TunnelHealthy", int(tunnel_ok))
    return {"services": {k: {"last_succeeded_at": v["last_succeeded_at"], "last_error": v["last_error"]} for k, v in rows.items()},
            "disk_free_bytes": free, "api_healthy": api_ok, "replication_healthy": replication_ok,
            "tunnel_healthy": tunnel_ok}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore-check", "health"))
    parser.add_argument("--if-due", action="store_true",
                        help="restore-check only: skip when today's check already succeeded")
    args = parser.parse_args()
    import boto3
    store = Store(Settings.from_env())
    cw = boto3.client("cloudwatch")
    if args.operation == "backup":
        result = backup(store, boto3.client("s3"), cw, os.environ["TICO_STORAGE_BUCKET"])
    elif args.operation == "restore-check" and args.if_due and not restore_check_due(store):
        result = {"skipped": "today's restore check already succeeded"}
    elif args.operation == "restore-check":
        result = restore_check(store, boto3.client("s3"), cw, os.environ["TICO_STORAGE_BUCKET"])
    else:
        result = health(store, cw)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
