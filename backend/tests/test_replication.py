import hashlib
import json
import os
import sqlite3
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend import replication
from backend.config import ROOT
from backend.tests.test_onboarding import environment, signed_in  # noqa: F401

ENTRYPOINT = ROOT / "docker/entrypoint.sh"


def test_status_modes(tmp_path):
    stamp = tmp_path / "status.json"
    stamp.write_text(json.dumps({"last_replicated_at": "2026-01-02T03:04:05Z"}))
    local = replication.status({"TICO_BACKUP_MODE": "local-only"}, stamp)
    assert local["mode"] == "local-only" and local["target_kind"] == "local"
    assert local["last_replicated_at"] == "2026-01-02T03:04:05Z" and "tico-backups" in local["warning"]
    r2 = replication.status({"TICO_BACKUP_MODE": "remote", "TICO_BACKUP_URL": "s3://b/p",
                             "TICO_BACKUP_ENDPOINT": "https://x.r2.cloudflarestorage.com"}, stamp)
    assert (r2["mode"], r2["target_kind"], r2["warning"]) == ("remote", "r2", "")
    assert replication.status({"TICO_BACKUP_MODE": "remote", "TICO_BACKUP_URL": "s3://b"}, stamp)["target_kind"] == "s3"
    minio = replication.status({"TICO_BACKUP_MODE": "remote", "TICO_BACKUP_URL": "s3://b", "TICO_BACKUP_ENDPOINT": "http://minio:9000"}, stamp)
    assert minio["target_kind"] == "s3-compatible"
    off = replication.status({"TICO_BACKUP_MODE": "off"}, stamp)
    assert off["mode"] == "off" and off["target_kind"] == "none" and off["warning"]
    # No status file yet: the replica has not been written, which is not the same as a time.
    assert replication.status({"TICO_BACKUP_MODE": "remote", "TICO_BACKUP_URL": "s3://b"}, tmp_path / "none")["last_replicated_at"] is None


class FakeS3:
    """Just the calls S3Mirror makes, over a dict."""
    def __init__(self):
        self.objects = {}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        outer = self

        class Pages:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k, "LastModified": datetime(2026, 5, 1, tzinfo=timezone.utc)}
                                    for k in sorted(outer.objects) if k.startswith(Prefix)]}
        return Pages()

    def upload_file(self, path, bucket, key):
        self.objects[key] = Path(path).read_bytes()

    def download_file(self, bucket, key, path):
        Path(path).write_bytes(self.objects[key])

    def head_object(self, Bucket, Key):
        from botocore.exceptions import ClientError
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}


def put_blob(root, data):
    digest = hashlib.sha256(data).hexdigest()
    path = root / "blobs" / digest[:2] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return digest


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_blobs_sync_incrementally_and_restore_verified(tmp_path, kind):
    mirror = replication.LocalMirror(tmp_path / "backups") if kind == "local" else replication.S3Mirror(FakeS3(), "bucket", "tico/")
    live = tmp_path / "live"
    first = put_blob(live, b"one")
    (live / "blobs" / "ab").mkdir(parents=True, exist_ok=True)
    (live / "blobs" / "ab" / ".upload-partial").write_bytes(b"half")
    assert replication.sync_blobs(live, mirror) == 1
    second = put_blob(live, b"two")
    assert replication.sync_blobs(live, mirror) == 1 and replication.sync_blobs(live, mirror) == 0
    fresh = tmp_path / "fresh"
    assert replication.restore_blobs(mirror, fresh) == 2
    assert (fresh / "blobs" / first[:2] / first).read_bytes() == b"one"
    assert (fresh / "blobs" / second[:2] / second).read_bytes() == b"two"
    assert not list(fresh.rglob(".upload-*")) and not list(fresh.rglob(".restore-*"))


KEY = bytes(range(32))


def key_env(tmp_path, **extra):
    (tmp_path / "data").mkdir(exist_ok=True)
    return {"TICO_DB": str(tmp_path / "data" / "hub.sqlite"), "TICO_BACKUP_MODE": "local-only",
            "TICO_BACKUP_DIR": str(tmp_path / "backups"), "TICO_BACKUP_STATUS_FILE": str(tmp_path / "status.json"),
            "TICO_BLOB_DIR": str(tmp_path / "data" / "blobs"), **extra}


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_the_credential_key_is_copied_when_it_appears_and_again_when_it_changes(tmp_path, kind, capsys):
    fake = FakeS3()
    mirror = replication.LocalMirror(tmp_path / "backups") if kind == "local" else replication.S3Mirror(fake, "bucket", "tico/")
    path, state = tmp_path / "credential.key", {}
    assert replication.sync_credential_key(path, mirror, state) is None and not mirror.exists(replication.CREDENTIAL_KEY_COPY)
    path.write_bytes(KEY)
    first = replication.sync_credential_key(path, mirror, state)
    assert first["copied_at"] and first["stamp"] == replication.credential_key_stamp(path)
    assert mirror.exists(replication.CREDENTIAL_KEY_COPY)
    if kind == "s3":
        assert fake.objects["tico/credential-key/credential.key"] == KEY
    else:
        copy = tmp_path / "backups" / replication.CREDENTIAL_KEY_COPY
        assert copy.read_bytes() == KEY and copy.stat().st_mode & 0o077 == 0
    # Unchanged: nothing is sent again. Changed: the new key is copied.
    mirror.put = lambda *a: pytest.fail("an unchanged key was sent again")
    assert replication.sync_credential_key(path, mirror, state)["copied_at"] == first["copied_at"]
    del mirror.put
    path.write_bytes(bytes(reversed(KEY)))
    assert replication.sync_credential_key(path, mirror, state)["stamp"] != ""
    restored = tmp_path / "restored.key"
    assert replication.restore_credential_key(mirror, restored) == "restored"
    assert restored.read_bytes() == bytes(reversed(KEY)) and restored.stat().st_mode & 0o777 == 0o600
    # A half-written file is not a key, and nothing printed so far carried one.
    path.write_bytes(b"short")
    assert replication.sync_credential_key(path, mirror, state) is None
    assert KEY.hex() not in capsys.readouterr().out


def test_the_loop_reports_the_key_copy_keeps_it_out_of_logs_and_the_status_file(tmp_path, capsys):
    env = key_env(tmp_path)
    key = tmp_path / "data" / "credential.key"
    before = replication.status(env)["credential_key"]
    assert before == {"present": False, "copied_at": None, "current": False}
    key.write_bytes(KEY)
    assert replication.status(env)["credential_key"] == {"present": True, "copied_at": None, "current": False}
    replication.loop(env, sleep=lambda s: None, rounds=1)
    shown = replication.status(env)["credential_key"]
    assert shown["present"] and shown["current"] and shown["copied_at"]
    assert (tmp_path / "backups" / "credential-key" / "credential.key").read_bytes() == KEY
    saved = (tmp_path / "status.json").read_text()
    assert KEY.hex() not in saved and "\\x" not in saved and hashlib.sha256(KEY).hexdigest() not in saved
    # Changed after the copy: not current until the next round copies it.
    before = key.stat()
    key.write_bytes(bytes(reversed(KEY)))
    # tmpfs can give immediate same-size writes the same timestamp. Advance the
    # metadata used by the status contract explicitly instead of depending on elapsed time.
    os.utime(key, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    assert replication.status(env)["credential_key"]["current"] is False
    replication.loop(env, sleep=lambda s: None, rounds=1)
    assert replication.status(env)["credential_key"]["current"] is True
    out = capsys.readouterr()
    assert KEY.hex() not in out.out + out.err
    # A failing backup location logs the kind of failure only.
    class Broken(replication.LocalMirror):
        def put(self, key, source):
            raise RuntimeError(Path(source).read_bytes().hex())
    key.write_bytes(KEY + b"")
    os.utime(key, (1, 1))
    from unittest import mock
    with mock.patch.object(replication, "mirror_from_env", lambda env: Broken(tmp_path / "x")):
        replication.loop(env, sleep=lambda s: None, rounds=1)
    out = capsys.readouterr()
    assert "RuntimeError" in out.out and KEY.hex() not in out.out + out.err


def test_a_kms_key_or_no_backup_means_no_key_copy(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "credential.key").write_bytes(KEY)
    env = key_env(tmp_path, TICO_CREDENTIAL_KMS_KEY="alias/tico")
    replication.loop(env, sleep=lambda s: None, rounds=1)
    assert not (tmp_path / "backups" / "credential-key").exists()
    assert replication.status(env)["credential_key"]["present"] is False
    off = key_env(tmp_path, TICO_BACKUP_MODE="off")
    replication.loop(off, sleep=lambda s: None, rounds=1)
    assert not (tmp_path / "backups" / "credential-key").exists()
    assert replication.status(off)["credential_key"] == {"present": True, "copied_at": None, "current": False}


def test_restoring_the_credential_key_never_overwrites_a_different_one(tmp_path):
    mirror = replication.LocalMirror(tmp_path / "backups")
    assert replication.restore_credential_key(mirror, tmp_path / "credential.key") == "missing"
    source = tmp_path / "source.key"
    source.write_bytes(KEY)
    replication.sync_credential_key(source, mirror, {})
    target = tmp_path / "credential.key"
    target.write_bytes(b"\x01" * 32)
    assert replication.restore_credential_key(mirror, target) == "kept" and target.read_bytes() == b"\x01" * 32
    assert replication.restore_credential_key(mirror, target, replace=True) == "restored"
    assert target.read_bytes() == KEY and target.stat().st_mode & 0o777 == 0o600
    (aside,) = tmp_path.glob("credential.key.before-restore.*")
    assert aside.read_bytes() == b"\x01" * 32
    assert replication.restore_credential_key(mirror, target, replace=True) == "kept"
    # A backup copy that is not a key is refused, and leaves nothing behind.
    (tmp_path / "backups" / "credential-key" / "credential.key").write_bytes(b"bad")
    with pytest.raises(SystemExit):
        replication.restore_credential_key(mirror, tmp_path / "other.key")
    assert not (tmp_path / "other.key").exists() and not list(tmp_path.glob(".restore-key-*"))


def test_restore_rejects_a_blob_that_fails_its_checksum(tmp_path):
    mirror = replication.LocalMirror(tmp_path / "backups")
    bad = tmp_path / "backups" / "files" / "blobs" / "aa" / ("a" * 64)
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"tampered")
    with pytest.raises(SystemExit, match="checksum"):
        replication.restore_blobs(mirror, tmp_path / "fresh")


def entrypoint(tmp_path, *args, command="restore", env=None):
    data, backups, bin_dir = tmp_path / "data", tmp_path / "backups", tmp_path / "bin"
    data.mkdir(exist_ok=True)
    backups.mkdir(exist_ok=True)
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "litestream"
    # restore -if-replica-exists -config CFG DB: write the database it would have restored
    stub.write_text(textwrap.dedent("""\
        #!/bin/sh
        echo "$@" >> "$STUB_LOG"
        if [ "$1" = restore ]; then
          [ -z "$STUB_FAIL" ] || { echo "AccessDenied" >&2; exit 1; }
          [ -f "$STUB_DB" ] && cp "$STUB_DB" "$(eval echo \\${$#})"
        fi
        exit 0
    """))
    stub.chmod(0o755)
    source = tmp_path / "source.sqlite"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE IF NOT EXISTS t(x)")
        db.execute("INSERT INTO t VALUES(1)")
    env = {**os.environ, "PATH": f"{bin_dir}:{Path(__import__('sys').executable).parent}:{os.environ['PATH']}",
           "TICO_DATA_DIR": str(data), "TICO_BACKUP_DIR": str(backups), "PYTHONPATH": str(ROOT),
           "STUB_LOG": str(tmp_path / "stub.log"), "STUB_DB": str(source), **(env or {})}
    env.pop("TICO_BACKUP_URL", None)
    return subprocess.run(["bash", str(ENTRYPOINT), command, *([] if command == "prepare" else args)], env=env, capture_output=True, text=True, timeout=60)


def test_restore_refuses_a_non_empty_volume_without_force(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "hub.sqlite").write_bytes(b"live")
    result = entrypoint(tmp_path)
    assert result.returncode != 0 and "--force" in result.stderr
    assert (tmp_path / "data" / "hub.sqlite").read_bytes() == b"live"
    assert not (tmp_path / "stub.log").exists()


def test_restore_into_an_empty_volume_and_force_over_a_full_one(tmp_path):
    result = entrypoint(tmp_path)
    assert result.returncode == 0, result.stderr
    assert sqlite3.connect(tmp_path / "data" / "hub.sqlite").execute("SELECT x FROM t").fetchone() == (1,)
    forced = entrypoint(tmp_path)
    assert forced.returncode != 0
    forced = entrypoint(tmp_path, "--force")
    assert forced.returncode == 0, forced.stderr
    assert list((tmp_path / "data").glob("hub.sqlite.before-restore.*"))


@pytest.mark.parametrize("rehearsal", ["0", "1"])
def test_restore_brings_the_credential_key_back_and_force_keeps_a_different_one_aside(tmp_path, rehearsal):
    copy = tmp_path / "backups" / replication.CREDENTIAL_KEY_COPY
    copy.parent.mkdir(parents=True)
    copy.write_bytes(KEY)
    result = entrypoint(tmp_path, env={"TICO_REHEARSAL": rehearsal})
    assert result.returncode == 0, result.stderr
    key = tmp_path / "data" / "credential.key"
    assert key.read_bytes() == KEY and key.stat().st_mode & 0o777 == 0o600
    assert KEY.hex() not in result.stdout + result.stderr
    # Over an install that has another key: without --force the volume is refused; with it the old key is kept aside.
    key.write_bytes(b"\x02" * 32)
    assert entrypoint(tmp_path, env={"TICO_REHEARSAL": rehearsal}).returncode != 0 and key.read_bytes() == b"\x02" * 32
    assert entrypoint(tmp_path, "--force", env={"TICO_REHEARSAL": rehearsal}).returncode == 0
    assert key.read_bytes() == KEY
    assert [p.read_bytes() for p in (tmp_path / "data").glob("credential.key.before-restore.*")] == [b"\x02" * 32]


def prepare(tmp_path, restore_fails=False, **extra):
    """`docker/entrypoint.sh prepare` on a file replica (the tico-backups directory), with a fake litestream."""
    env = {"TICO_COMPANY_NAME": "Acme", "TICO_OWNER_EMAIL": "owner@example.com", "TICO_AUTH_PROXY": "none",
           "TICO_LOCAL_OWNER_TOKEN_FILE": str(tmp_path / "token"), "STUB_FAIL": "1" if restore_fails else "",
           "STUB_DB": str(tmp_path / "no-replica"), **extra}
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    result = entrypoint(tmp_path, "prepare", command="prepare", env=env)
    return result, data


def test_a_failed_restore_of_an_existing_company_refuses_to_start(tmp_path):
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / replication.MARKER).write_text('{"environment_id": "acme1"}')
    result, data = prepare(tmp_path, restore_fails=True)
    assert result.returncode != 0 and "existing company" in result.stderr and "TICO_INITIALIZE_EMPTY" in result.stderr
    assert not (data / "hub.sqlite").exists() and not (data / replication.LOCAL_MARKER).exists()


def test_an_unreadable_backup_refuses_even_with_no_marker(tmp_path):
    result, data = prepare(tmp_path, restore_fails=True)
    assert result.returncode != 0 and "unknown whether" in result.stderr and not (data / "hub.sqlite").exists()


def test_a_new_company_over_an_existing_backup_needs_initialize_empty(tmp_path):
    level = tmp_path / "backups" / "ltx" / "0"
    level.mkdir(parents=True)
    (level / "0000000000000001-0000000000000001.ltx").write_bytes(b"x")      # a replica holds a database, no marker yet
    refused, data = prepare(tmp_path)
    assert refused.returncode != 0 and "existing company" in refused.stderr and not (data / "hub.sqlite").exists()
    allowed, _ = prepare(tmp_path, TICO_INITIALIZE_EMPTY="1")
    assert allowed.returncode == 0, allowed.stderr
    assert (data / "hub.sqlite").exists() and (data / replication.LOCAL_MARKER).exists()
    assert (tmp_path / "backups" / replication.MARKER).exists()


def test_a_fresh_install_starts_and_leaves_markers_a_later_empty_volume_respects(tmp_path):
    fresh, data = prepare(tmp_path)
    assert fresh.returncode == 0, fresh.stderr
    marker = json.loads((tmp_path / "backups" / replication.MARKER).read_text())["environment_id"]
    assert json.loads((data / replication.LOCAL_MARKER).read_text())["environment_id"] == marker
    for item in data.iterdir():                     # the volume is lost; the backup location keeps the marker
        subprocess.run(["rm", "-rf", str(item)])
    again, _ = prepare(tmp_path, restore_fails=True)
    assert again.returncode != 0 and not (data / "hub.sqlite").exists()
