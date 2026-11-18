import socket
import sqlite3

from botocore.exceptions import ClientError
import pytest

from backend import operations
from backend.operations import backup, health, restore_check
from backend.store import H
from backend.tests.helpers.backup_bundle import ObjectStore
from backend.tests.helpers.cloudwatch import CloudWatch
from backend.tests.live import live  # noqa: F401 - pytest finds the live fixture by this import


class UnavailableObjectStore(ObjectStore):
    """Every upload is refused the way S3 refuses it while it is unavailable."""

    def put_object(self, **kw):
        raise ClientError({"Error": {"Code": "ServiceUnavailable"}}, "PutObject")


def closed_url(path):
    """A loopback URL that nothing listens on, so the probe gets connection refused."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}{path}"


def test_operational_health_never_advances_backup_time_after_failed_upload(api, live, tmp_path):
    store = api.app.state.store
    cw = CloudWatch()
    backup(store, ObjectStore(), cw, "test-bucket")
    with store.read() as c:
        before = c.execute("SELECT last_succeeded_at FROM service_health WHERE service='backup'").fetchone()[0]
    with pytest.raises(ClientError):
        backup(store, UnavailableObjectStore(), cw, "test-bucket")
    with store.read() as c:
        row = c.execute("SELECT * FROM service_health WHERE service='backup'").fetchone()
        assert row["last_succeeded_at"] == before and row["last_error"] == "ClientError"
    metrics = health(
        store,
        cw,
        api_url=live + "/healthz",
        replication_url=closed_url("/metrics"),
        tunnel_config=tmp_path / "no-tunnel.yml",
    )
    assert metrics["disk_free_bytes"] > 0
    assert metrics["api_healthy"] is True and metrics["tunnel_healthy"] is True
    assert metrics["replication_healthy"] is False
    assert cw.points("BackupSucceeded") == [1, 0]
    assert len(cw.calls) == 9
    assert all({"Name": "Scope", "Value": "default"} in m["Dimensions"] for c in cw.calls for m in c["MetricData"])


def test_failed_restore_check_does_not_advance_prior_success(api, tmp_path, monkeypatch):
    store = api.app.state.store
    s3, cw = ObjectStore(), CloudWatch()
    backup(store, s3, cw, "bucket")
    monkeypatch.setattr(operations, "RESTORE_ROOT", tmp_path / "checks")  # double: the check writes under this root
    monkeypatch.setattr(operations, "_free_bytes", lambda path: 10 * 1024**3)
    restore_check(store, s3, cw, "bucket")
    with store.read() as c:
        before = c.execute("SELECT last_succeeded_at FROM service_health WHERE service='restore-check'").fetchone()[0]
    key = next(k for _, k in s3.objects if k.startswith("backups/") and k.endswith(".sqlite"))
    s3.objects[("bucket", key)] = b"Damaged copy in the bucket"
    with pytest.raises(sqlite3.DatabaseError):
        restore_check(store, s3, cw, "bucket")
    monkeypatch.setattr(operations, "_free_bytes", lambda path: 0)
    with pytest.raises(RuntimeError, match="Not enough retained-volume capacity"):
        restore_check(store, s3, cw, "bucket")
    assert list((tmp_path / "checks").iterdir()) == []  # the refused check made no copy
    with store.read() as c:
        row = c.execute("SELECT * FROM service_health WHERE service='restore-check'").fetchone()
    assert row["last_succeeded_at"] == before and row["last_error"] == "RuntimeError"
    assert cw.points("RestoreCheckSucceeded") == [1, 0, 0]


def test_backup_notifications_follow_active_implementation_without_rewriting_history(api, monkeypatch):
    from backend import replication
    from backend.tests.helpers.api import headers

    old = H.shift(H.now(), hours=-50)
    with api.app.state.store.transaction() as c:
        for service in ("backup", "restore-check"):
            c.execute(
                "INSERT INTO service_health(service,last_succeeded_at,last_error) VALUES(?,?,?)",
                (service, old, "Old timer failed"),
            )
    issues = lambda: [
        i for i in api.get("/api/status", headers=headers()).json()["health_issues"] if i["kind"] == "service"
    ]
    monkeypatch.delenv("TICO_BACKUP_MODE", raising=False)
    assert {i["title"] for i in issues()} == {"backup needs attention", "restore-check needs attention"}
    monkeypatch.setenv("TICO_BACKUP_MODE", "remote")
    current = {"mode": "remote", "target_kind": "s3", "last_replicated_at": H.now()}
    monkeypatch.setattr(replication, "status", lambda: current)
    assert issues() == []
    current["last_replicated_at"] = old  # an old copy with nothing behind it is a quiet database, not a problem
    assert issues() == []
    current["behind_since"] = old  # the backup has lacked a transaction the database has for 50 hours
    assert len(issues()) == 1 and issues()[0]["title"] == "Backups need attention"
    del current["behind_since"]
    current.update(mode="off", last_replicated_at=None)
    assert issues()[0]["severity"] == "error"
    current.update(mode="remote", last_replicated_at=H.now(), credential_key={"present": True, "current": False})
    assert "credential key" in issues()[0]["detail"]
    with api.app.state.store.read() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM service_health WHERE last_succeeded_at=? AND last_error=?",
                (old, "Old timer failed"),
            ).fetchone()[0]
            == 2
        )


def test_the_scheduled_backup_carries_the_key_files_and_the_restore_check_reads_them(api, tmp_path, monkeypatch):
    import json

    store = api.app.state.store
    data = store.settings.db_path.parent
    (data / "credential.key").write_bytes(b"\x01" * 32)
    (data / "github-app.key").write_bytes(b"\x02" * 32)
    s3, cw = ObjectStore(), CloudWatch()
    report = backup(store, s3, cw, "bucket")
    assert report["key_files"] == ["credential.key", "github-app.key"]
    manifest = json.loads(s3.objects[("bucket", report["key"] + ".manifest.json")])
    assert sorted(manifest["key_files"]) == ["credential.key", "github-app.key"]
    assert s3.objects[("bucket", report["key"] + ".keys/github-app.key")] == b"\x02" * 32
    with store.read() as c:
        detail = json.loads(c.execute("SELECT detail FROM service_health WHERE service='backup'").fetchone()[0])
    assert detail["key_files"] == ["credential.key", "github-app.key"] and "\x02" not in json.dumps(detail)
    monkeypatch.setattr(operations, "RESTORE_ROOT", tmp_path / "checks")  # double: the check writes under this root
    monkeypatch.setattr(operations, "_free_bytes", lambda path: 10 * 1024**3)
    assert restore_check(store, s3, cw, "bucket")["verified"]
    assert not list((tmp_path / "checks").iterdir())  # the drill's copy, keys included, is removed
