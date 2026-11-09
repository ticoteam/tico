import os
import shutil
import sqlite3
from contextlib import closing

import pytest

from backend import backup
from backend.backup import checkpoint, compact, inspect, snapshot, upload_snapshot
from backend.config import Settings
from backend.tests.helpers.api import assign, claim, post, ready, runner  # noqa: F401
from backend.tests.test_api import api  # noqa: F401
from backend.store import IDEMPOTENCY_RETENTION_HOURS, H, Store, sweep_idempotency


def test_snapshot_preserves_committed_wal_data_and_can_restore(tmp_path):
    source, dest = tmp_path / "live.sqlite", tmp_path / "backup.sqlite"
    with closing(sqlite3.connect(source)) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA wal_autocheckpoint=0")
        c.execute("CREATE TABLE messages(body TEXT)")
        c.execute("INSERT INTO messages VALUES('Committed in WAL')")
        c.commit()
        assert source.with_name("live.sqlite-wal").stat().st_size > 0
        report = snapshot(source, dest)
        assert report["integrity"] == ["ok"]
        with closing(sqlite3.connect(dest)) as restored:
            assert restored.execute("SELECT body FROM messages").fetchone()[0] == "Committed in WAL"
        with pytest.raises(ValueError):
            snapshot(source, dest)


def test_deployment_checkpoint_is_an_exact_copy_that_skips_the_integrity_scan(tmp_path, monkeypatch):
    source, dest = tmp_path / "live.sqlite", tmp_path / "deploy-backups" / "1.sqlite"
    with closing(sqlite3.connect(source)) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA wal_autocheckpoint=0")
        c.execute("CREATE TABLE messages(body TEXT)")
        c.execute("INSERT INTO messages VALUES('Committed in WAL')")
        c.commit()
        scans = []

        class Traced(sqlite3.Connection):
            def execute(self, sql, *args):
                scans.append(sql)
                return super().execute(sql, *args)

        connect = sqlite3.connect
        monkeypatch.setattr(backup.sqlite3, "connect", lambda *a, **k: connect(*a, factory=Traced, **k))
        report = checkpoint(source, dest)
    assert not any("integrity_check" in sql or "quick_check" in sql for sql in scans)
    assert report == {"bytes": dest.stat().st_size, "tables": 1}
    with closing(sqlite3.connect(dest)) as restored:
        assert restored.execute("SELECT body FROM messages").fetchone()[0] == "Committed in WAL"
    assert inspect(dest)["integrity"] == ["ok"]
    with pytest.raises(ValueError):
        checkpoint(source, dest)
    empty = tmp_path / "empty.sqlite"
    sqlite3.connect(empty).close()
    with pytest.raises(RuntimeError, match="incomplete"):
        checkpoint(empty, tmp_path / "deploy-backups" / "2.sqlite")


def test_failed_upload_never_reports_success_or_deletes_source(tmp_path):
    source = tmp_path / "live.sqlite"
    with closing(sqlite3.connect(source)) as c:
        c.execute("CREATE TABLE data(x)")

    class FailingS3:
        def upload_file(self, *args, **kwargs):
            raise RuntimeError("upload unavailable")

    with pytest.raises(RuntimeError, match="upload unavailable"):
        upload_snapshot(source, "bucket", "backup", FailingS3())
    assert inspect(source)["integrity"] == ["ok"]


def test_backup_success_requires_verified_download(tmp_path):
    source = tmp_path / "live.sqlite"
    object_path = tmp_path / "s3-object"
    with closing(sqlite3.connect(source)) as c:
        c.execute("CREATE TABLE data(x)")

    class S3:
        manifest = None

        def upload_file(self, source, *args, **kwargs):
            shutil.copyfile(source, object_path)

        def download_file(self, bucket, key, dest):
            shutil.copyfile(object_path, dest)

        def put_object(self, **kwargs):
            self.manifest = kwargs

    s3 = S3()
    result = upload_snapshot(source, "bucket", "backup", s3)
    assert result["verified"] is True
    assert s3.manifest["Key"] == "backup.manifest.json"
