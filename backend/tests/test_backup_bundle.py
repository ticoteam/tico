"""Restore complete API-owned sources from independent binary backup copies."""

import base64
import hashlib
import io
import stat
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.backup import blob_inventory, restore_bundle, upload_bundle
from backend.blobs import Blobs
from backend.config import Settings
from backend.operations import backup
from backend.store import Problem
from backend.tests.test_api import api, headers, post, setup_attempt  # noqa: F401


class ObjectStore:
    """Offline S3 semantics, including conditional writes, independent GETs and HEADs."""
    def __init__(self):
        self.objects = {}
        self.gets = []
        self.heads = []
        self.after_put = None

    def put_object(self, **kw):
        slot = (kw["Bucket"], kw["Key"])
        assert kw["IfNoneMatch"] == "*" and kw["ServerSideEncryption"] == "AES256"
        if slot in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        data = kw["Body"].read() if hasattr(kw["Body"], "read") else kw["Body"]
        assert base64.b64encode(hashlib.sha256(data).digest()).decode() == kw["ChecksumSHA256"]
        self.objects[slot] = data
        if self.after_put:
            self.after_put(slot)
        return {"VersionId": "test-version"}

    def get_object(self, **kw):
        slot = (kw["Bucket"], kw["Key"])
        self.gets.append(slot)
        if slot not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[slot])}

    def head_object(self, **kw):
        # S3 reports the checksum it verified on receipt, only when asked for it.
        slot = (kw["Bucket"], kw["Key"])
        self.heads.append(slot)
        if slot not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        data = self.objects[slot]
        head = {"ContentLength": len(data)}
        if kw.get("ChecksumMode") == "ENABLED":
            head["ChecksumSHA256"] = base64.b64encode(hashlib.sha256(data).digest()).decode()
        return head

    def download_file(self, bucket, key, destination):
        with self.get_object(Bucket=bucket, Key=key)["Body"] as stream:
            Path(destination).write_bytes(stream.read())


def sources(api):
    note = api.post("/api/notes", data={"text": "Private attachment to recover"},
                    files={"files": ("draft.txt", b"Exact private draft")}, headers=headers("ben-test")).json()
    payload = b"\x03\x00" * 16000
    rid = api.post("/api/v2/meetings/import", data={"title": "Recover source media", "notes": "Keep typed notes",
                                                    "transcript": "Ana: Keep this call."},
                   files={"files": ("call.m4a", payload)}, headers=headers()).json()["id"]
    return note, rid, payload


def target(tmp_path):
    return Settings(db_path=tmp_path / "restore" / "hub.sqlite", blob_dir=tmp_path / "restore" / "files")


def bundle(api, s3, key="backups/test.sqlite"):
    return upload_bundle(api.app.state.store.settings.db_path, "backup-bucket", key, s3, api.app.state.blobs)


def test_restore_from_independent_objects_preserves_private_sources_and_fences_bots(api, tmp_path):
    note, rid, audio = sources(api)
    machine, _, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "before-loss"}, machine["token"])
    s3 = ObjectStore()
    report = bundle(api, s3)
    assert report["objects"] == 2 and report["object_bytes"] == len(audio) + len(b"Exact private draft")
    assert len([slot for slot in s3.gets if slot[1].startswith("recovery/")]) == 2
    # The database is verified from what S3 recorded on receipt, never downloaded again.
    assert ("backup-bucket", report["key"]) in s3.heads and ("backup-bucket", report["key"]) not in s3.gets
    # Make the live store unavailable without deleting any source files.
    api.app.state.blobs.directory.rename(tmp_path / "unavailable-live-files")
    settings = target(tmp_path)
    recovered = restore_bundle("backup-bucket", report["key"], settings, s3)
    assert recovered["verified"] and recovered["fenced_attempts"] == 1 and recovered["objects"] == 2
    assert stat.S_IMODE(settings.db_path.stat().st_mode) == 0o600
    settings.test_identities = api.app.state.store.settings.test_identities
    with TestClient(create_app(settings)) as restored:
        file_url = note["attachments"][0]["url"]
        assert restored.get(file_url, headers=headers("ben-test")).content == b"Exact private draft"
        assert restored.get(file_url, headers=headers("cara-test")).status_code == 403
        record = restored.get("/api/meetings/" + rid, headers=headers()).json()
        assert record["notes"] == "Keep typed notes" and record["attachments"][0]["name"] == "call.m4a"
        assert restored.get("/api/v2/runners", headers=headers(machine["token"])).status_code == 401
        inventory = blob_inventory(settings.db_path)
        assert audio in [restored.app.state.blobs.get(item["sha256"]) for item in inventory]


def test_incomplete_source_never_publishes_manifest_or_advances_backup_health(api):
    sources(api)
    with api.app.state.store.read() as c:
        digest = c.execute("SELECT digest FROM blobs ORDER BY size LIMIT 1").fetchone()[0]
    (api.app.state.blobs.directory / Blobs.key(digest)).write_bytes(b"Corrupt test content")
    s3, metrics = ObjectStore(), Mock()
    with pytest.raises((Problem, RuntimeError)):
        backup(api.app.state.store, s3, metrics, "backup-bucket")
    assert not any(key.endswith(".manifest.json") for _, key in s3.objects)
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM service_health WHERE service='backup'").fetchone()
        assert row["last_succeeded_at"] is None and row["last_error"]
    assert metrics.put_metric_data.call_args.kwargs["MetricData"][0]["Value"] == 0


def test_corrupt_backups_fail_before_creating_restored_database(api, tmp_path):
    sources(api)
    s3 = ObjectStore()
    bundle(api, s3)
    slot = next(slot for slot in s3.objects if slot[1].startswith("recovery/"))
    s3.objects[slot] = b"Corrupted independent backup"
    settings = target(tmp_path)
    with pytest.raises(Problem):
        restore_bundle("backup-bucket", "backups/test.sqlite", settings, s3)
    assert not settings.db_path.exists()


def test_backup_detects_corruption_during_independent_download(api):
    sources(api)
    s3 = ObjectStore()
    def corrupt(slot):
        if slot[1].startswith("recovery/"):
            s3.objects[slot] = b"Storage returned wrong bytes"
    s3.after_put = corrupt
    with pytest.raises(Problem, match="integrity"):
        bundle(api, s3)
    assert not any(key.endswith(".manifest.json") for _, key in s3.objects)


KEY_NAMES = ("credential.key", "github-app.key", "slack.key")


def hand_run_backup(api, s3, monkeypatch, key="backups/hand.sqlite"):
    """`python -m backend.manage backup`, with the fake object store in place of S3."""
    import json

    import boto3

    from backend import manage
    settings = api.app.state.store.settings
    monkeypatch.setattr(boto3, "client", lambda name, *a, **k: s3 if name == "s3" else pytest.fail(name))
    manage.main(["backup", str(settings.db_path), "--bucket", "backup-bucket", "--key", key,
                 "--blob-dir", str(api.app.state.blobs.directory)])


def test_a_hand_run_backup_carries_the_key_files_and_restore_brings_them_back(api, tmp_path, monkeypatch, capsys):
    sources(api)
    data = api.app.state.store.settings.db_path.parent
    keys = {name: bytes([i + 1]) * 32 for i, name in enumerate(KEY_NAMES)}
    for name, value in keys.items():
        (data / name).write_bytes(value)
    s3 = ObjectStore()
    hand_run_backup(api, s3, monkeypatch)
    report = capsys.readouterr().out
    assert all(name in report for name in KEY_NAMES) and not any(value.hex() in report for value in keys.values())
    settings = target(tmp_path)
    recovered = restore_bundle("backup-bucket", "backups/hand.sqlite", settings, s3)
    assert recovered["key_files"] == {name: "restored" for name in KEY_NAMES}
    for name, value in keys.items():
        path = settings.db_path.parent / name
        assert path.read_bytes() == value and stat.S_IMODE(path.stat().st_mode) == 0o600
    # A bundle restored again beside the same keys keeps them; a different key there is never overwritten.
    again = target(tmp_path / "again")
    again.db_path.parent.mkdir(parents=True)
    (again.db_path.parent / "slack.key").write_bytes(b"\x09" * 32)
    with pytest.raises(ValueError, match="slack.key"):
        restore_bundle("backup-bucket", "backups/hand.sqlite", again, s3)
    assert not again.db_path.exists() and (again.db_path.parent / "slack.key").read_bytes() == b"\x09" * 32
    assert not (again.db_path.parent / "credential.key").exists()


def test_a_hand_run_backup_carries_only_the_key_files_present_and_refuses_a_broken_one(api, tmp_path, monkeypatch):
    sources(api)
    data = api.app.state.store.settings.db_path.parent
    (data / "github-app.key").write_bytes(b"\x05" * 32)
    s3 = ObjectStore()
    hand_run_backup(api, s3, monkeypatch)
    settings = target(tmp_path)
    assert restore_bundle("backup-bucket", "backups/hand.sqlite", settings, s3)["key_files"] == {"github-app.key": "restored"}
    assert not (settings.db_path.parent / "credential.key").exists()
    # A key file that is not 32 bytes is no key: the bundle is not published rather than carry it.
    (data / "slack.key").write_bytes(b"short")
    with pytest.raises(RuntimeError, match="slack.key"):
        hand_run_backup(api, s3, monkeypatch, key="backups/broken.sqlite")
    assert ("backup-bucket", "backups/broken.sqlite.manifest.json") not in s3.objects
    # A tampered key copy in the bucket fails the restore before the database is made.
    s3.objects[("backup-bucket", "backups/hand.sqlite.keys/github-app.key")] = b"\x06" * 32
    other = target(tmp_path / "tampered")
    with pytest.raises(RuntimeError, match="github-app.key"):
        restore_bundle("backup-bucket", "backups/hand.sqlite", other, s3)
    assert not other.db_path.exists() and not (other.db_path.parent / "github-app.key").exists()
