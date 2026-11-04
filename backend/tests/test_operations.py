from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from backend.operations import api_healthy, backup, health, replication_healthy, restore_check, tunnel_healthy
from backend.store import H
from backend.tests.test_api import api


def test_operational_health_never_advances_backup_time_after_failed_upload(api):
    store = api.app.state.store
    s3, cw = Mock(), Mock()
    with patch("backend.operations.upload_bundle", return_value={"verified": True, "sha256": "a" * 64,
                                                                "bytes": 1, "objects": 0, "object_bytes": 0,
                                                                "manifest_sha256": "b" * 64}):
        backup(store, s3, cw, "test-bucket")
    with store.read() as c:
        before = c.execute("SELECT last_succeeded_at FROM service_health WHERE service='backup'").fetchone()[0]
    with patch("backend.operations.upload_bundle", side_effect=RuntimeError("cannot upload")), pytest.raises(RuntimeError):
        backup(store, s3, cw, "test-bucket")
    with store.read() as c:
        row = c.execute("SELECT * FROM service_health WHERE service='backup'").fetchone()
        assert row["last_succeeded_at"] == before and row["last_error"] == "RuntimeError"
    with patch("backend.operations.api_healthy", return_value=True), patch("backend.operations.replication_healthy", return_value=True), patch("backend.operations.tunnel_healthy", return_value=True):
        metrics = health(store, cw)
    assert metrics["disk_free_bytes"] > 0
    assert metrics["api_healthy"] is True and metrics["replication_healthy"] is True and metrics["tunnel_healthy"] is True
    assert cw.put_metric_data.call_count == 9
    assert all({"Name": "Scope", "Value": "default"} in call.kwargs["MetricData"][0]["Dimensions"]
               for call in cw.put_metric_data.call_args_list)


def test_failed_restore_check_does_not_advance_prior_success(api, tmp_path):
    store = api.app.state.store
    from backend.tests.test_backup_bundle import ObjectStore
    s3, cw = ObjectStore(), Mock()
    backup(store, s3, cw, "bucket")
    with (patch("backend.operations.RESTORE_ROOT", tmp_path / "checks"),
          patch("backend.operations.shutil.disk_usage", return_value=Mock(free=10 * 1024 ** 3)) as capacity):
        restore_check(store, s3, cw, "bucket")
        with store.read() as c:
            before = c.execute("SELECT last_succeeded_at FROM service_health WHERE service='restore-check'").fetchone()[0]
        with patch("backend.operations.restore_bundle", side_effect=RuntimeError("corrupt")), pytest.raises(RuntimeError):
            restore_check(store, s3, cw, "bucket")
        capacity.return_value.free = 0
        with patch("backend.operations.restore_bundle") as restore, pytest.raises(RuntimeError, match="Not enough retained-volume capacity"):
            restore_check(store, s3, cw, "bucket")
        restore.assert_not_called()
    with store.read() as c:
        row = c.execute("SELECT * FROM service_health WHERE service='restore-check'").fetchone()
    assert row["last_succeeded_at"] == before and row["last_error"] == "RuntimeError"
    assert cw.put_metric_data.call_args.kwargs["MetricData"][0]["Value"] == 0



def test_backup_notifications_follow_active_implementation_without_rewriting_history(api, monkeypatch):
    from backend import replication
    from backend.tests.test_api import headers

    old = H.shift(H.now(), hours=-50)
    with api.app.state.store.transaction() as c:
        for service in ("backup", "restore-check"):
            c.execute("INSERT INTO service_health(service,last_succeeded_at,last_error) VALUES(?,?,?)",
                      (service, old, "Old timer failed"))
    issues = lambda: [i for i in api.get("/api/status", headers=headers()).json()["health_issues"]
                      if i["kind"] == "service"]
    monkeypatch.delenv("TICO_BACKUP_MODE", raising=False)
    assert {i["title"] for i in issues()} == {"backup needs attention", "restore-check needs attention"}
    monkeypatch.setenv("TICO_BACKUP_MODE", "remote")
    current = {"mode": "remote", "target_kind": "s3", "last_replicated_at": H.now()}
    monkeypatch.setattr(replication, "status", lambda: current)
    assert issues() == []
    current["last_replicated_at"] = old     # an old copy with nothing behind it is a quiet database, not a problem
    assert issues() == []
    current["behind_since"] = old           # the backup has lacked a transaction the database has for 50 hours
    assert len(issues()) == 1 and issues()[0]["title"] == "Backups need attention"
    del current["behind_since"]
    current.update(mode="off", last_replicated_at=None)
    assert issues()[0]["severity"] == "error"
    current.update(mode="remote", last_replicated_at=H.now(),
                   credential_key={"present": True, "current": False})
    assert "credential key" in issues()[0]["detail"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM service_health WHERE last_succeeded_at=? AND last_error=?",
                         (old, "Old timer failed")).fetchone()[0] == 2


def test_the_scheduled_backup_carries_the_key_files_and_the_restore_check_reads_them(api, tmp_path):
    import json

    from backend.tests.test_backup_bundle import ObjectStore
    store = api.app.state.store
    data = store.settings.db_path.parent
    (data / "credential.key").write_bytes(b"\x01" * 32)
    (data / "github-app.key").write_bytes(b"\x02" * 32)
    s3, cw = ObjectStore(), Mock()
    report = backup(store, s3, cw, "bucket")
    assert report["key_files"] == ["credential.key", "github-app.key"]
    manifest = json.loads(s3.objects[("bucket", report["key"] + ".manifest.json")])
    assert sorted(manifest["key_files"]) == ["credential.key", "github-app.key"]
    assert s3.objects[("bucket", report["key"] + ".keys/github-app.key")] == b"\x02" * 32
    with store.read() as c:
        detail = json.loads(c.execute("SELECT detail FROM service_health WHERE service='backup'").fetchone()[0])
    assert detail["key_files"] == ["credential.key", "github-app.key"] and "\x02" not in json.dumps(detail)
    with (patch("backend.operations.RESTORE_ROOT", tmp_path / "checks"),
          patch("backend.operations.shutil.disk_usage", return_value=Mock(free=10 * 1024 ** 3))):
        assert restore_check(store, s3, cw, "bucket")["verified"]
    assert not list((tmp_path / "checks").iterdir())            # the drill's copy, keys included, is removed
