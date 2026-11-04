import hashlib
import json
import os
import sqlite3
import subprocess
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from backend import replication
from backend.config import ROOT
from backend.hubdb import leave_wal_for_litestream

ENTRYPOINT = ROOT / "docker/entrypoint.sh"


class FakeS3:
    """Just the calls S3Mirror makes, over a dict. `modified` dates an object; the default is an old date."""
    def __init__(self):
        self.objects, self.modified = {}, {}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        outer = self

        class Pages:
            def paginate(self, Bucket, Prefix):
                old = datetime(2026, 5, 1, tzinfo=timezone.utc)
                yield {"Contents": [{"Key": k, "LastModified": outer.modified.get(k, old)}
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


@pytest.mark.parametrize("kind", ["s3"])
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


@pytest.mark.parametrize("kind", ["local"])
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
    before = path.stat()
    path.write_bytes(bytes(reversed(KEY)))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
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
    extra = env or {}
    env = {**os.environ, "PATH": f"{bin_dir}:{Path(__import__('sys').executable).parent}:{os.environ['PATH']}",
           "TICO_DATA_DIR": str(data), "TICO_BACKUP_DIR": str(backups), "PYTHONPATH": str(ROOT),
           "STUB_LOG": str(tmp_path / "stub.log"), "STUB_DB": str(source),
           "TICO_LITESTREAM_CONFIG": str(tmp_path / "litestream.yml"), **extra}   # not the shared /tmp default: xdist workers race on it
    if "TICO_BACKUP_URL" not in extra:    # an inherited bucket setting must not reach the tests; one a test passes is its own
        env.pop("TICO_BACKUP_URL", None)
    # An inherited BASH_ENV can restore the turn's PATH and run real litestream and uvicorn instead of the stubs.
    env.pop("BASH_ENV", None)
    return subprocess.run(["bash", str(ENTRYPOINT), command, *([] if command == "prepare" else args)], env=env, capture_output=True, text=True, timeout=60)


def test_restore_into_an_empty_volume_and_force_over_a_full_one(tmp_path):
    result = entrypoint(tmp_path)
    assert result.returncode == 0, result.stderr
    assert sqlite3.connect(tmp_path / "data" / "hub.sqlite").execute("SELECT x FROM t").fetchone() == (1,)
    forced = entrypoint(tmp_path)
    assert forced.returncode != 0 and "--force" in forced.stderr     # a non-empty volume needs --force
    forced = entrypoint(tmp_path, "--force")
    assert forced.returncode == 0, forced.stderr
    assert list((tmp_path / "data").glob("hub.sqlite.before-restore.*"))


@pytest.mark.parametrize("rehearsal", ["1"])
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


def litestream_config(tmp_path, monkeypatch, sync_interval=None):
    """`prepare` writes the Litestream config; returns its parsed replica block, or the refused prepare's result."""
    monkeypatch.delenv("TICO_BACKUP_SYNC_INTERVAL", raising=False)
    config = tmp_path / "litestream.yml"
    extra = {} if sync_interval is None else {"TICO_BACKUP_SYNC_INTERVAL": sync_interval}
    result, _ = prepare(tmp_path, TICO_LITESTREAM_CONFIG=str(config), **extra)
    if result.returncode != 0:
        return result
    return yaml.safe_load(config.read_text())


def test_litestream_syncs_every_minute_unless_the_install_sets_an_interval(tmp_path, monkeypatch):
    config = litestream_config(tmp_path, monkeypatch)
    assert config["dbs"][0]["replica"]["sync-interval"] == "1m"
    assert config["dbs"][0]["monitor-interval"] == "1s" and config["dbs"][0]["checkpoint-interval"] == "1m"
    assert config["snapshot"] == {"interval": "24h", "retention": "720h"}


@pytest.mark.parametrize("value", ["1s", "30s", "60m", "1h"])
def test_a_valid_sync_interval_is_written_and_nothing_else_changes(tmp_path, monkeypatch, value):
    config = litestream_config(tmp_path, monkeypatch, value)
    assert config["dbs"][0]["replica"]["sync-interval"] == value
    assert config["dbs"][0]["monitor-interval"] == "1s" and config["dbs"][0]["checkpoint-interval"] == "1m"


@pytest.mark.parametrize("value", ["0s", "61m", "2h", "10ms", "1.5m", "1h30m", "m", "3600"])
def test_an_invalid_sync_interval_is_refused_before_anything_starts(tmp_path, monkeypatch, value):
    result = litestream_config(tmp_path, monkeypatch, value)
    assert result.returncode != 0 and "TICO_BACKUP_SYNC_INTERVAL must be" in result.stderr
    assert not (tmp_path / "litestream.yml").exists() and not (tmp_path / "data" / "hub.sqlite").exists()


@pytest.mark.slow
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


@pytest.mark.slow
def test_a_fresh_install_starts_and_leaves_markers_a_later_empty_volume_respects(tmp_path):
    fresh, data = prepare(tmp_path)
    assert fresh.returncode == 0, fresh.stderr
    marker = json.loads((tmp_path / "backups" / replication.MARKER).read_text())["environment_id"]
    assert json.loads((data / replication.LOCAL_MARKER).read_text())["environment_id"] == marker
    for item in data.iterdir():                     # the volume is lost; the backup location keeps the marker
        subprocess.run(["rm", "-rf", str(item)])
    again, _ = prepare(tmp_path, restore_fails=True)
    assert again.returncode != 0 and not (data / "hub.sqlite").exists()


# -- every key file a restored database needs, not only credential.key ------------------------------------------------

PEM = "-----BEGIN RSA PRIVATE KEY-----\nsynthetic\n-----END RSA PRIVATE KEY-----\n"
SLACK_BOT, SLACK_APP = "xoxb-synthetic-0000000000", "xapp-synthetic-0000000000"


def seal_secrets(data):
    """A local-key install (no TICO_CREDENTIAL_KMS_KEY) with a GitHub App, directory sync credentials and Slack tokens."""
    from backend import slack_app
    from backend.config import Settings
    from backend.directory import Directory
    from backend.github_app import GitHubApp
    from backend.store import Store
    settings = Settings(db_path=data / "hub.sqlite")
    store = Store(settings)
    store.initialize()
    app = GitHubApp(settings, store)
    directory = Directory(settings, store, app)
    with store.transaction() as c:
        app.save(c, "human:ana", "Acme", True, {"id": 7, "slug": "acme-tico", "client_id": "Iv1.synthetic", "pem": PEM,
                                                 "webhook_secret": "whsec-synthetic", "client_secret": "cs-synthetic"})
        directory.put_credentials(c, "human:ana", "entra", {"tenant": "t", "client_id": "c", "client_secret": "s"}, "app c in t")
    slack_app.save(store, settings, "human:ana", SLACK_BOT, SLACK_APP)


def open_secrets(data):
    """What a server on `data` can decrypt: {github, directory, slack}, each the plaintext or the error."""
    from backend import slack_app
    from backend.config import Settings
    from backend.directory import Directory
    from backend.github_app import GitHubApp
    from backend.store import Problem, Store
    settings = Settings(db_path=data / "hub.sqlite")
    store = Store(settings)
    app = GitHubApp(settings, store)
    directory = Directory(settings, store, app)
    found = {}
    with store.transaction() as c:
        try:
            found["github"] = app.private_key(c, app.row(c))
        except Problem as exc:
            found["github"] = exc.detail
        try:
            found["directory"] = directory.credentials(c, "entra")["client_secret"]
        except Problem as exc:
            found["directory"] = exc.detail
    stored = slack_app.load(store, settings)
    found["slack"] = stored["bot_token"] if stored else None
    found["webhook"] = app.webhook_secret()
    return found


def test_a_restore_into_a_fresh_volume_can_decrypt_the_github_directory_and_slack_secrets(tmp_path):
    """The real path: the backup loop copies what Litestream does not, a database copy stands in for Litestream's, and
    `restore` (docker/entrypoint.sh) rebuilds an empty volume from both."""
    live = tmp_path / "data"
    live.mkdir()
    seal_secrets(live)
    assert open_secrets(live) == {"github": PEM, "directory": "s", "slack": SLACK_BOT, "webhook": "whsec-synthetic"}
    replication.loop(key_env(tmp_path), sleep=lambda s: None, rounds=1)
    from backend import manage
    manage.main(["snapshot", str(live / "hub.sqlite"), str(tmp_path / "litestream-copy.sqlite")])
    live.rename(tmp_path / "lost-server")
    result = entrypoint(tmp_path, env={"STUB_DB": str(tmp_path / "litestream-copy.sqlite")})
    assert result.returncode == 0, result.stderr
    assert open_secrets(tmp_path / "data") == {"github": PEM, "directory": "s", "slack": SLACK_BOT, "webhook": "whsec-synthetic"}
    for name in replication.KEY_FILES:
        assert (tmp_path / "backups" / "credential-key" / name).stat().st_mode & 0o077 == 0
        assert (tmp_path / "data" / name).stat().st_mode & 0o777 == 0o600
    assert "restored github-app.key" in result.stdout and "restored slack.key" in result.stdout
    status = replication.status(key_env(tmp_path))
    assert set(status["key_files"]) == set(replication.KEY_FILES)


def test_an_older_backup_without_the_other_key_files_still_restores_and_reads_create_no_new_key(tmp_path):
    live = tmp_path / "data"
    live.mkdir()
    seal_secrets(live)
    replication.loop(key_env(tmp_path), sleep=lambda s: None, rounds=1)
    for name in replication.KEY_FILES:          # a backup made before these were copied
        (tmp_path / "backups" / "credential-key" / name).unlink()
    from backend import manage
    manage.main(["snapshot", str(live / "hub.sqlite"), str(tmp_path / "litestream-copy.sqlite")])
    live.rename(tmp_path / "lost-server")
    result = entrypoint(tmp_path, env={"STUB_DB": str(tmp_path / "litestream-copy.sqlite")})
    assert result.returncode == 0, result.stderr
    assert "holds no github-app.key" in result.stdout and "holds no slack.key" in result.stdout
    found = open_secrets(tmp_path / "data")
    assert "github-app.key" in found["github"] and "missing" in found["github"]
    assert "github-app.key" in found["directory"] and found["slack"] is None and found["webhook"] == ""
    # Reading did not make a new key that would hide the missing one (and that a later restore would keep).
    assert not any((tmp_path / "data" / name).exists() for name in replication.KEY_FILES)


def test_the_other_key_files_go_to_the_bucket_beside_the_credential_key(tmp_path):
    fake = FakeS3()
    mirror = replication.S3Mirror(fake, "bucket", "tico/")
    for name in replication.KEY_FILES:
        (tmp_path / name).write_bytes(KEY)
        assert replication.sync_credential_key(tmp_path / name, mirror, {}, replication.key_copy(name))
        assert fake.objects["tico/credential-key/" + name] == KEY
    env = {"TICO_DB": str(tmp_path / "fresh" / "hub.sqlite")}
    assert replication.restore_key_files(env, mirror=mirror) == 0
    assert all((tmp_path / "fresh" / name).read_bytes() == KEY for name in replication.KEY_FILES)
    fake.objects["tico/credential-key/slack.key"] = b"short"
    (tmp_path / "fresh" / "slack.key").unlink()
    assert replication.restore_key_files(env, mirror=mirror) == 1         # said, and the other files still restored
    assert not (tmp_path / "fresh" / "slack.key").exists()


def test_a_new_key_never_overwrites_the_backups_only_good_copy(tmp_path):
    """github-app.key did not come back with a restore and a save made a new one: the copy in the backup that opens the
    stored GitHub App is kept aside, by a fresh backup process too. Saves that would orphan sealed rows are refused."""
    from backend.store import Problem
    live = tmp_path / "data"
    live.mkdir()
    seal_secrets(live)
    mirror = replication.LocalMirror(tmp_path / "backups")
    for name in replication.KEY_FILES:
        replication.sync_credential_key(live / name, mirror, {}, replication.key_copy(name))
    good = {name: (live / name).read_bytes() for name in replication.KEY_FILES}
    (live / "github-app.key").unlink()
    # Saving directory credentials now would seal them with a new key while the GitHub App row needs the old one.
    from backend.config import Settings
    from backend.directory import Directory
    from backend.github_app import GitHubApp
    from backend.store import Store
    settings = Settings(db_path=live / "hub.sqlite")
    store = Store(settings)
    app = GitHubApp(settings, store)
    directory = Directory(settings, store, app)
    with pytest.raises(Problem, match="github-app.key"), store.transaction() as c:
        directory.put_credentials(c, "human:ana", "google", {"admin_email": "a@acme.example"}, "hint")
    assert not (live / "github-app.key").exists()
    # The new key is made anyway (by hand, or once the person reconnects): the backup keeps the good copy aside.
    for name in replication.KEY_FILES:
        (live / name).write_bytes(b"\x07" * 32)
        replication.sync_credential_key(live / name, mirror, {}, replication.key_copy(name))   # a fresh process
        (aside,) = (tmp_path / "backups" / "credential-key").glob(name + ".replaced-*")
        assert aside.read_bytes() == good[name]
        assert (tmp_path / "backups" / "credential-key" / name).read_bytes() == b"\x07" * 32
    # The same key again (another fresh process) keeps nothing more aside.
    replication.sync_credential_key(live / "slack.key", mirror, {}, replication.key_copy("slack.key"))
    assert len(list((tmp_path / "backups" / "credential-key").glob("slack.key.replaced-*"))) == 1


def test_the_backup_status_lists_only_the_newest_level(tmp_path):
    s3, listed = FakeS3(), []
    paginator = s3.get_paginator

    def counting(name):
        pages = paginator(name)
        original = pages.paginate
        pages.paginate = lambda Bucket, Prefix: (listed.append(Prefix), original(Bucket, Prefix))[1]
        return pages
    s3.get_paginator = counting
    mirror = replication.S3Mirror(s3, "bucket", "tico")
    s3.objects.update({"tico/0001/a.ltx": b"", "tico/0009/b.ltx": b""})
    assert mirror.newest(replication.DB_LEVELS) is not None and listed == ["tico/0000/", "tico/0001/"]
    listed.clear()
    s3.objects["tico/0000/c.ltx"] = b""
    assert mirror.newest(replication.DB_LEVELS) is not None and listed == ["tico/0000/"]
    assert replication.S3Mirror(FakeS3(), "bucket", "tico").newest(replication.DB_LEVELS) is None

    local = replication.LocalMirror(tmp_path)
    (tmp_path / "0003").mkdir()
    (tmp_path / "0003/old.ltx").write_text("x")
    os.utime(tmp_path / "0003/old.ltx", (100, 100))
    assert local.newest(replication.DB_LEVELS) == 100
    (tmp_path / "0000").mkdir()
    (tmp_path / "0000/new.ltx").write_text("x")
    os.utime(tmp_path / "0000/new.ltx", (50, 50))
    assert local.newest(replication.DB_LEVELS) == 50


def add_copy(kind, store, level, txid, seconds_ago=0, replica=""):
    """A replica file at a Litestream level (0000/ on S3, ltx/0 in a file replica), named for the TXID it ends at and
    dated that long ago. `store` is the FakeS3 or the backup folder; `replica` is the folder of the replica under it."""
    when = time.time() - seconds_ago
    name = f"{txid:016x}-{txid:016x}.ltx"
    if kind == "s3":
        key = "/".join(part for part in ("tico", replica, f"{level:04d}", name) if part)
        store.objects[key] = b""
        store.modified[key] = datetime.fromtimestamp(when, timezone.utc)
    else:
        path = store / replica / f"ltx/{level}" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        os.utime(path, (when, when))


def mirror_pair(kind, tmp_path):
    fake = FakeS3()
    store = fake if kind == "s3" else tmp_path / "backups"
    mirror = replication.S3Mirror(fake, "bucket", "tico") if kind == "s3" else replication.LocalMirror(store)
    return store, mirror


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_the_copy_time_and_txid_come_from_the_first_replica_level_that_has_a_file(tmp_path, kind):
    store, mirror = mirror_pair(kind, tmp_path)
    now = time.time()
    add_copy(kind, store, 9, 5, seconds_ago=86400)   # only a daily snapshot: still a time, not nothing
    assert mirror.latest(replication.DB_LEVELS) == (pytest.approx(now - 86400, abs=5), 5)
    add_copy(kind, store, 2, 7, seconds_ago=900)     # level 2 is first with files once 0 and 1 are empty
    assert mirror.latest(replication.DB_LEVELS) == (pytest.approx(now - 900, abs=5), 7)
    add_copy(kind, store, 1, 8, seconds_ago=60)
    assert mirror.latest(replication.DB_LEVELS) == (pytest.approx(now - 60, abs=5), 8)
    add_copy(kind, store, 0, 9, seconds_ago=30)
    assert mirror.latest(replication.DB_LEVELS) == (pytest.approx(now - 30, abs=5), 9)


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_the_replica_position_is_read_only_from_the_replica_folder_it_is_given(tmp_path, kind):
    store, mirror = mirror_pair(kind, tmp_path)
    now, generation = time.time(), "gen-20261101T000000Z"
    add_copy(kind, store, 0, 3, seconds_ago=30)
    assert mirror.latest(replication.DB_LEVELS, replica=generation) == (None, None)
    add_copy(kind, store, 0, 4, seconds_ago=10, replica=generation)
    assert mirror.latest(replication.DB_LEVELS, replica=generation) == (pytest.approx(now - 10, abs=5), 4)
    assert mirror.latest(replication.DB_LEVELS)[1] == 3


def test_the_ltx_names_give_their_txid_range_and_other_names_give_none():
    assert replication.ltx_range("000000000000000a-00000000000000ff.ltx") == (10, 255)
    assert replication.ltx_range("0000000000000001-0000000000000005.ltx.part") is None
    assert replication.ltx_range("1-5.ltx") is None
    assert replication.max_txid(["0000000000000001-0000000000000005.ltx", "0000000000000006-000000000000000a.ltx",
                                 "x.ltx"]) == 10
    assert replication.max_txid(["x.ltx"]) is None and replication.max_txid([]) is None


def test_the_local_level_zero_is_counted_and_its_top_txid_read_from_the_names(tmp_path):
    db = tmp_path / "data" / "hub.sqlite"
    level = replication.litestream_state_dir(db) / "ltx" / "0"
    level.mkdir(parents=True)
    for txid in (1, 2, 3):
        (level / f"{txid:016x}-{txid:016x}.ltx").write_bytes(b"x")
    assert replication.local_level_zero(db) == (3, 3)
    assert replication.local_level_zero(tmp_path / "nowhere" / "hub.sqlite") == (0, None)


def test_the_status_has_a_time_for_an_idle_replica_and_says_why_only_when_no_database_file_is_there(tmp_path):
    env = key_env(tmp_path)
    replication.loop(env, sleep=lambda s: None, rounds=1)
    empty = replication.status(env)
    assert empty["last_replicated_at"] is None and empty["reason"] == replication.NO_RECENT_COPY
    add_copy("local", tmp_path / "backups", 9, 5, seconds_ago=3 * 86400)   # idle: its snapshot is days old
    replication.loop(env, sleep=lambda s: None, rounds=1)
    idle = replication.status(env)
    assert idle["last_replicated_at"] and idle["reason"] is None and idle["behind_since"] is None


def test_lag_is_timed_from_when_the_backup_fell_behind_and_clears_once_it_catches_up(tmp_path):
    env = key_env(tmp_path)
    level = replication.litestream_state_dir(Path(env["TICO_DB"])) / "ltx" / "0"
    level.mkdir(parents=True)
    for txid in range(1, 8):
        (level / f"{txid:016x}-{txid:016x}.ltx").write_bytes(b"x")
    add_copy("local", tmp_path / "backups", 9, 3, seconds_ago=86400)   # the replica holds TXIDs up to 3, as a snapshot
    replication.loop(env, sleep=lambda s: None, rounds=1)
    stuck = replication.status(env)
    saved = json.loads(Path(env["TICO_BACKUP_STATUS_FILE"]).read_text())
    assert stuck["behind_since"] and stuck["last_replicated_at"]
    assert saved["local_txid"] == f"{7:016x}" and saved["replica_txid"] == f"{3:016x}"
    # Still behind a round later: the time it fell behind is kept, not moved on.
    Path(env["TICO_BACKUP_STATUS_FILE"]).write_text(json.dumps({**saved, "behind_since": "2026-10-31T00:00:00Z"}))
    replication.loop(env, sleep=lambda s: None, rounds=1)
    assert replication.status(env)["behind_since"] == "2026-10-31T00:00:00Z"
    # Caught up: the replica holds the local TXID, even in a copy three days old. Nothing is behind, so it is cleared.
    add_copy("local", tmp_path / "backups", 0, 7, seconds_ago=3 * 86400)
    replication.loop(env, sleep=lambda s: None, rounds=1)
    assert replication.status(env)["behind_since"] is None


def test_the_status_without_a_mirror_keeps_what_the_last_round_saw(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"last_replicated_at": "2026-10-30T11:59:51Z", "behind_since": "2026-10-30T11:00:00Z"}))
    replication.write_status(None, path, unsent=4)
    saved = json.loads(path.read_text())
    assert saved["last_replicated_at"] == "2026-10-30T11:59:51Z" and saved["behind_since"] == "2026-10-30T11:00:00Z"
    assert saved["unsent_files"] == 4


@pytest.mark.parametrize(("value", "seconds"), [(None, 60), ("30s", 30), ("10m", 600), ("2h", 60)])
def test_the_sync_interval_is_read_the_way_the_entrypoint_reads_it(value, seconds):
    env = {} if value is None else {"TICO_BACKUP_SYNC_INTERVAL": value}
    assert replication.sync_interval_seconds(env) == seconds


class Listing:
    """Stands in for `litestream ltx`: what it prints, or how it fails."""
    def __init__(self, stdout="[]", returncode=0, error=None):
        self.stdout, self.returncode, self.error, self.calls = stdout, returncode, error, []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, "")


def local_backup_config(tmp_path, db, monkeypatch):
    """The Litestream config docker/entrypoint.sh writes for a local backup location, and the environment the reset reads it
    with: an empty backup location at tmp_path/backups, and no bucket or rehearsal setting from the developer's shell."""
    backup = tmp_path / "backups"
    backup.mkdir(exist_ok=True)
    config = tmp_path / "litestream.yml"
    config.write_text("dbs:\n  - path: %s\n    replica:\n      url: %s\n      sync-interval: 1m\n" % (db, backup.as_uri()))
    for name in ("TICO_BACKUP_URL", "TICO_REHEARSAL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TICO_BACKUP_MODE", "local-only")
    monkeypatch.setenv("TICO_BACKUP_DIR", str(backup))
    return config


def stale_litestream_state(tmp_path, monkeypatch):
    """A database with the WAL and Litestream's local state beside it, as an earlier run leaves them."""
    data = tmp_path / "data"
    data.mkdir()
    db = data / "hub.sqlite"
    db.write_bytes(b"database")
    (data / "hub.sqlite-wal").write_bytes(b"wal")
    state = data / ".hub.sqlite-litestream"
    (state / "ltx" / "0").mkdir(parents=True)
    (state / "ltx" / "0" / "0000000000000003-0000000000000003.ltx").write_bytes(b"ltx")
    return db, state, local_backup_config(tmp_path, db, monkeypatch)


def assert_database_untouched(db):
    assert db.read_bytes() == b"database" and (db.parent / "hub.sqlite-wal").read_bytes() == b"wal"


def test_an_empty_replica_clears_the_stale_state_and_nothing_else(tmp_path, monkeypatch, capsys):
    db, state, config = stale_litestream_state(tmp_path, monkeypatch)
    listing = Listing("[]")
    monkeypatch.setattr(replication.subprocess, "run", listing)
    assert replication.main(["reset-stale-litestream", str(config), str(db)]) == 0
    assert not state.exists()
    assert_database_untouched(db)
    assert capsys.readouterr().out.strip() == "tico: the backup location is empty; removed Litestream's stale local state (.hub.sqlite-litestream)"
    (args, kwargs), = listing.calls
    assert args[:2] == ["litestream", "ltx"] and args[args.index("-config") + 1] == str(config) and args[-1] == str(db)
    assert 0 < kwargs["timeout"] <= 120


def test_a_replica_with_any_file_at_any_level_keeps_the_state(tmp_path, monkeypatch, capsys):
    db, state, config = stale_litestream_state(tmp_path, monkeypatch)
    monkeypatch.setattr(replication.subprocess, "run", Listing('[{"level": 9, "min_txid": "0000000000000001", "max_txid": "0000000000000004"}]'))
    assert replication.main(["reset-stale-litestream", str(config), str(db)]) == 0
    assert state.is_dir() and (state / "ltx" / "0").is_dir()
    assert_database_untouched(db)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("listing", [
    Listing(error=subprocess.TimeoutExpired("litestream", 60)),
    Listing(returncode=1, stdout=""),
    Listing(error=FileNotFoundError("litestream")),
    Listing(stdout="not json"),
], ids=["timeout", "error", "missing-binary", "garbled"])
def test_an_unreadable_replica_changes_nothing_and_says_the_check_was_skipped(tmp_path, monkeypatch, capsys, listing):
    db, state, config = stale_litestream_state(tmp_path, monkeypatch)
    monkeypatch.setattr(replication.subprocess, "run", listing)
    assert replication.main(["reset-stale-litestream", str(config), str(db)]) == 0
    assert state.is_dir()
    assert_database_untouched(db)
    assert capsys.readouterr().out.strip() == "tico: could not read the backup location; skipped the check for stale Litestream state"


def test_without_local_state_the_replica_is_not_even_asked(tmp_path, monkeypatch, capsys):
    db = tmp_path / "hub.sqlite"
    db.write_bytes(b"database")
    config = local_backup_config(tmp_path, db, monkeypatch)
    listing = Listing()
    monkeypatch.setattr(replication.subprocess, "run", listing)
    assert replication.main(["reset-stale-litestream", str(config), str(db)]) == 0
    assert listing.calls == [] and capsys.readouterr().out == "" and db.read_bytes() == b"database"


# -- backup generations: a stuck copy is healed by starting a new folder ----------------------------------------------

NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc).timestamp()
REPLICA_STAMP = "2026-11-01T11:00:00Z"    # the replica's newest copy: an hour before NOW
GENERATION = "gen-20261101T120000Z"       # what a heal at NOW starts


def listing_at(stamp):
    return json.dumps([{"level": 0, "min_txid": "0000000000000001", "max_txid": "0000000000000001", "size": 1,
                        "timestamp": stamp}])


def replica_url(config):
    return yaml.safe_load(config.read_text())["dbs"][0]["replica"]["url"]


def backup_setup(tmp_path, copies=1, environment=None):
    """A database with Litestream's local state beside it (`copies` L0 files, written at NOW), a file replica root with
    the environment.json given, and the Litestream config that points at that root, as docker/entrypoint.sh writes it."""
    data, backup = tmp_path / "data", tmp_path / "backups"
    data.mkdir()
    backup.mkdir()
    db = data / "hub.sqlite"
    db.write_bytes(b"database")
    (data / "hub.sqlite-wal").write_bytes(b"wal")
    level = data / ".hub.sqlite-litestream" / "ltx" / "0"
    level.mkdir(parents=True)
    for n in range(copies):
        path = level / ("%016x-%016x.ltx" % (n + 1, n + 1))
        path.write_bytes(b"ltx")
        os.utime(path, (NOW, NOW))
    if environment is not None:
        (backup / replication.MARKER).write_text(environment)
    config = tmp_path / "litestream.yml"
    config.write_text("dbs:\n  - path: %s\n    replica:\n      url: %s\n      sync-interval: 1m\n" % (db, backup.as_uri()))
    env = {"TICO_BACKUP_DIR": str(backup), "TICO_BACKUP_MODE": "local-only", "TICO_DB": str(db)}
    return config, db, data / ".hub.sqlite-litestream", backup, env


def test_an_empty_replica_with_a_stuck_state_is_reset_as_before_and_gets_no_generation(tmp_path, monkeypatch):
    config, db, state, backup, env = backup_setup(tmp_path, copies=replication.LOCAL_STUCK_FILES + 1)
    before = config.read_text()
    monkeypatch.setattr(replication.subprocess, "run", Listing("[]"))
    message = replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW)
    assert message == "tico: the backup location is empty; removed Litestream's stale local state (.hub.sqlite-litestream)"
    assert not state.exists() and config.read_text() == before
    assert list(backup.iterdir()) == []                  # no environment.json and no generation folder
    assert_database_untouched(db)


def test_a_populated_replica_with_a_stuck_state_moves_to_a_new_generation_and_keeps_its_marker(tmp_path, monkeypatch):
    config, db, state, backup, env = backup_setup(tmp_path, copies=replication.LOCAL_STUCK_FILES + 1,
                                                  environment='{"environment_id": "acme1"}')
    monkeypatch.setattr(replication.subprocess, "run", Listing(listing_at(REPLICA_STAMP)))
    message = replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW)
    root = "file://" + str(backup)
    assert message == ("tico: the local Litestream state was stuck; started backup generation " + GENERATION + " at "
                       + root + "/" + GENERATION + " (the copy at " + root + " is kept)")
    assert not state.exists()
    assert replica_url(config) == root + "/" + GENERATION
    assert json.loads((backup / replication.MARKER).read_text()) == {"environment_id": "acme1", "generation": GENERATION}
    assert (backup / GENERATION / ".tico-generation").is_file()
    assert (db.parent / replication.GENERATION_CACHE).read_text() == GENERATION + "\n"
    assert_database_untouched(db)


def test_an_unreadable_replica_changes_nothing_even_when_a_heal_is_asked_for(tmp_path, monkeypatch):
    config, db, state, backup, env = backup_setup(tmp_path, copies=replication.LOCAL_STUCK_FILES + 1)
    flag = replication.heal_flag_path(db)
    replication.write_heal_flag(flag, True, None)
    before = config.read_text()
    monkeypatch.setattr(replication.subprocess, "run", Listing(error=subprocess.TimeoutExpired("litestream", 60)))
    message = replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW)
    assert message == "tico: could not read the backup location; skipped the check for stale Litestream state"
    assert state.is_dir() and config.read_text() == before and list(backup.iterdir()) == []
    assert replication.read_heal_flag(flag)["requested"] is True       # the request waits for a readable replica
    assert_database_untouched(db)


def test_a_marker_that_cannot_be_read_refuses_the_new_generation_and_changes_nothing(tmp_path, monkeypatch):
    config, db, state, backup, env = backup_setup(tmp_path, copies=replication.LOCAL_STUCK_FILES + 1, environment="not json")
    before = config.read_text()
    monkeypatch.setattr(replication.subprocess, "run", Listing(listing_at(REPLICA_STAMP)))
    message = replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW)
    assert message.startswith("tico: could not start a new backup generation") and "keeps the copy it has" in message
    assert state.is_dir() and config.read_text() == before
    assert [p.name for p in backup.iterdir()] == [replication.MARKER]
    assert (backup / replication.MARKER).read_text() == "not json"
    assert_database_untouched(db)


def test_a_heal_request_rotates_once_and_the_next_request_waits_six_hours(tmp_path, monkeypatch):
    config, db, state, backup, env = backup_setup(tmp_path)
    flag = replication.heal_flag_path(db)
    monkeypatch.setattr(replication.subprocess, "run", Listing(listing_at(REPLICA_STAMP)))

    def request_and_reset(at):
        replication.write_heal_flag(flag, True, replication.read_heal_flag(flag).get("last_heal_at"))
        return replication.reset_stale_litestream(str(config), str(db), env=env, now=at)

    assert "started backup generation " + GENERATION in request_and_reset(NOW)
    assert replication.read_heal_flag(flag) == {"requested": False, "last_heal_at": "2026-11-01T12:00:00Z"}
    assert replica_url(config) == "file://" + str(backup) + "/" + GENERATION
    state.joinpath("ltx", "0").mkdir(parents=True)                      # the local state is back, still short
    (state / "ltx" / "0" / "0000000000000001-0000000000000001.ltx").write_bytes(b"ltx")
    assert "not starting another" in request_and_reset(NOW + 3600)     # an hour later: refused, and the request is cleared
    assert replica_url(config) == "file://" + str(backup) + "/" + GENERATION
    assert replication.read_heal_flag(flag) == {"requested": False, "last_heal_at": "2026-11-01T12:00:00Z"}
    later = NOW + replication.HEAL_INTERVAL + 1
    assert "gen-20261101T180001Z" in request_and_reset(later)           # six hours on, the next one may start
    assert replica_url(config) == "file://" + str(backup) + "/gen-20261101T180001Z"
    assert (backup / GENERATION / ".tico-generation").is_file()        # the older generation is kept


def test_a_flag_file_that_is_only_present_asks_for_a_heal(tmp_path, monkeypatch):
    config, db, state, backup, env = backup_setup(tmp_path)
    flag = replication.heal_flag_path(db)
    flag.write_text("")                                                 # made by hand: an empty file is still a request
    monkeypatch.setattr(replication.subprocess, "run", Listing(listing_at(REPLICA_STAMP)))
    message = replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW)
    assert "started backup generation " + GENERATION in message
    assert replica_url(config) == "file://" + str(backup) + "/" + GENERATION
    assert replication.read_heal_flag(flag) == {"requested": False, "last_heal_at": "2026-11-01T12:00:00Z"}


def stalled_copy(tmp_path, monkeypatch, copies=replication.LOCAL_STUCK_FILES + 1, **extra_env):
    """A stuck copy: more local copies than a healthy install keeps, and a replica that stopped an hour ago while the
    database changed at NOW. Returns (check, stops, flag): check(minutes) runs one round of the runtime check. Keyword
    arguments are added to the environment (a sync interval, say)."""
    config, db, state, backup, env = backup_setup(tmp_path, copies=copies)
    env.update(extra_env)
    replica_copy = backup / "ltx" / "0" / "0000000000000001-0000000000000001.ltx"
    replica_copy.parent.mkdir(parents=True)
    replica_copy.write_bytes(b"ltx")
    os.utime(replica_copy, (NOW - 3600, NOW - 3600))
    os.utime(db, (NOW, NOW))
    stops, watch = [], replication.StallWatch()
    monkeypatch.setattr(replication, "stop_litestream", lambda env: stops.append(env))

    def check(minutes):
        return replication.stuck_replica_check(env, replication.LocalMirror(backup), watch, NOW + minutes * 60)
    return check, stops, replication.heal_flag_path(db)


def test_the_runtime_check_asks_for_a_heal_once_the_copy_has_stalled_while_the_database_changed(tmp_path, monkeypatch):
    check, stops, flag = stalled_copy(tmp_path, monkeypatch)
    assert check(0) is False and check(19) is False and stops == []    # not stalled for 20 minutes yet
    assert check(21) is True and len(stops) == 1                       # Litestream is stopped once, so the container restarts
    assert replication.read_heal_flag(flag)["requested"] is True


def test_the_runtime_check_waits_six_hours_after_a_heal_before_asking_again(tmp_path, monkeypatch):
    check, stops, flag = stalled_copy(tmp_path, monkeypatch)
    replication.write_heal_flag(flag, False, "2026-11-01T12:00:00Z")   # a heal began at NOW
    assert check(21) is False and stops == []
    assert check(6 * 60 + 21) is True and len(stops) == 1


@pytest.mark.parametrize("cached", [False, True], ids=["no-cache", "cache"])
def test_a_restore_and_a_seed_refuse_to_read_a_backup_whose_generation_cannot_be_read(tmp_path, cached):
    """A cache naming the generation is no stand-in here: a restore or a seed never reads a guessed generation."""
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / replication.MARKER).write_text("not json")
    if cached:
        (tmp_path / "data").mkdir(exist_ok=True)
        replication.write_generation_cache(tmp_path / "data", GENERATION)
    restore = entrypoint(tmp_path, command="restore")
    assert restore.returncode != 0 and "backup generation" in restore.stderr
    seeded, data = prepare(tmp_path, STUB_DB=str(tmp_path / "source.sqlite"))
    assert seeded.returncode != 0 and not (data / "hub.sqlite").exists()
    log = (tmp_path / "stub.log").read_text() if (tmp_path / "stub.log").exists() else ""
    assert "restore" not in log                                        # litestream restore never ran


@pytest.mark.parametrize("generation, suffix", [(None, ""), (GENERATION, "/" + GENERATION)], ids=["root", "generation"])
def test_a_restore_points_the_config_at_the_generation_environment_json_names(tmp_path, generation, suffix):
    marker = {"environment_id": "acme1", **({"generation": generation} if generation else {})}
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / replication.MARKER).write_text(json.dumps(marker))
    with sqlite3.connect(tmp_path / "source.sqlite") as restored:    # the database a restore brings back, with its id table
        restored.execute("CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT)")
    config = tmp_path / "litestream.yml"
    result, _ = prepare(tmp_path, STUB_DB=str(tmp_path / "source.sqlite"), TICO_LITESTREAM_CONFIG=str(config))
    assert result.returncode == 0, result.stderr
    assert replica_url(config) == (tmp_path / "backups").as_uri() + suffix
    assert json.loads((tmp_path / "backups" / replication.MARKER).read_text()).get("generation") == generation  # kept by mark-environment


# -- the generation cache: the start reads it, the reset job corrects it from environment.json ----------------------------

def test_a_start_with_a_cached_generation_uses_it_even_when_the_bucket_is_unreachable(tmp_path):
    """The start reads only the cache beside the database, so an unreachable bucket neither holds it up nor moves it to the
    root."""
    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(data / "hub.sqlite") as db:
        db.execute("CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT)")
    replication.write_generation_cache(data, GENERATION)
    config = tmp_path / "litestream.yml"
    result, _ = prepare(tmp_path, TICO_LITESTREAM_CONFIG=str(config), TICO_BACKUP_URL="s3://bucket/tico",
                        TICO_BACKUP_ENDPOINT="http://127.0.0.1:9")
    assert result.returncode == 0, result.stderr
    assert replica_url(config) == "s3://bucket/tico/" + GENERATION


def test_without_a_cache_an_unreachable_bucket_on_an_existing_database_uses_the_root_as_before(tmp_path, monkeypatch):
    """A database from before the cache has no generation to lose: the cache and the database share the data volume."""
    config, db, state, backup, env = backup_setup(tmp_path, copies=replication.LOCAL_STUCK_FILES + 1)

    def unreachable(mirror):
        raise OSError("the bucket is unreachable")
    monkeypatch.setattr(replication, "replica_generation", unreachable)
    monkeypatch.setattr(replication.subprocess, "run", Listing(error=subprocess.TimeoutExpired("litestream", 60)))
    assert replication.sync_generation(str(config), str(db), env) == "root"
    assert replica_url(config) == "file://" + str(backup)
    assert not replication.cache_path(db.parent).exists()
    assert replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW) == (
        "tico: could not read the backup location; skipped the check for stale Litestream state")
    assert state.is_dir()
    assert_database_untouched(db)


def test_a_cache_naming_a_generation_holds_when_environment_json_cannot_be_read(tmp_path):
    config, db, state, backup, env = backup_setup(tmp_path, environment="not json")
    replication.write_generation_cache(db.parent, GENERATION)
    assert replication.sync_generation(str(config), str(db), env) == "kept"
    assert replica_url(config) == "file://" + str(backup) + "/" + GENERATION
    assert replication.read_generation_cache(db.parent) == GENERATION


def test_a_config_naming_another_location_is_left_alone_and_the_reset_refuses(tmp_path):
    config, db, state, backup, env = backup_setup(tmp_path, copies=replication.LOCAL_STUCK_FILES + 1)
    before = replica_url(config)
    with pytest.raises(replication.GenerationUnknown):
        replication.reset_stale_litestream(str(config), str(db), env={**env, "TICO_BACKUP_DIR": str(tmp_path / "elsewhere")}, now=NOW)
    assert replica_url(config) == before and state.is_dir()
    assert_database_untouched(db)


def test_a_missing_database_with_no_cache_and_an_unreadable_environment_json_says_the_generation_is_unknown(tmp_path):
    config, db, state, backup, env = backup_setup(tmp_path, environment="not json")
    db.unlink()
    before = replica_url(config)
    with pytest.raises(replication.GenerationUnknown):
        replication.sync_generation(str(config), str(db), env)
    assert replica_url(config) == before


def test_an_unreadable_generation_file_stops_the_start_before_the_config_is_written(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(data / "hub.sqlite") as db:
        db.execute("CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT)")
    (data / replication.GENERATION_CACHE).write_text("not a generation\n")
    config = tmp_path / "litestream.yml"
    result, _ = prepare(tmp_path, TICO_LITESTREAM_CONFIG=str(config))
    assert result.returncode != 0 and "backup generation file" in result.stderr, result.stderr
    assert not config.exists()


def test_a_restore_over_a_database_whose_generation_cannot_be_read_stops_before_moving_it(tmp_path):
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / replication.MARKER).write_text("not json")
    data = tmp_path / "data"
    data.mkdir()
    (data / "hub.sqlite").write_bytes(b"database")
    result = entrypoint(tmp_path, "--force")
    assert result.returncode != 0 and "backup generation" in result.stderr, result.stderr
    assert (data / "hub.sqlite").read_bytes() == b"database"
    assert not list(data.glob("hub.sqlite.before-restore.*"))
    log = tmp_path / "stub.log"
    assert not log.exists() or "restore" not in log.read_text()          # litestream restore never ran


def test_the_bucket_client_gives_up_within_seconds_so_a_start_or_a_seed_cannot_hang():
    mirror = replication.mirror_from_env({"TICO_BACKUP_URL": "s3://bucket/tico", "TICO_BACKUP_ENDPOINT": "http://127.0.0.1:9"})
    config = mirror.s3.meta.config
    assert (config.connect_timeout, config.read_timeout, config.retries["total_max_attempts"]) == (3, 10, 2)


def test_the_loop_reads_the_replica_position_from_the_generation_the_volume_names(tmp_path):
    env = key_env(tmp_path)
    db = Path(env["TICO_DB"])
    level = replication.litestream_state_dir(db) / "ltx" / "0"
    level.mkdir(parents=True)
    (level / f"{2:016x}-{2:016x}.ltx").write_bytes(b"x")
    add_copy("local", tmp_path / "backups", 0, 9, seconds_ago=60)    # the root: an older generation's history
    add_copy("local", tmp_path / "backups", 0, 1, seconds_ago=30, replica="gen-20261101T000000Z")
    replication.write_generation_cache(db.parent, "gen-20261101T000000Z")
    replication.loop(env, sleep=lambda s: None, rounds=1)
    saved = json.loads(Path(env["TICO_BACKUP_STATUS_FILE"]).read_text())
    assert saved["replica_txid"] == f"{1:016x}" and saved["behind_since"]


@pytest.mark.parametrize("strict", [False, True], ids=["reset", "strict"])
def test_a_generation_this_version_does_not_know_raises_in_every_mode(tmp_path, strict):
    config, db, state, backup, env = backup_setup(tmp_path, environment='{"generation": "gen-someday"}')
    replication.write_generation_cache(db.parent, GENERATION)
    before = replica_url(config)
    with pytest.raises(replication.GenerationUnknown):
        replication.sync_generation(str(config), str(db), env, strict=strict)
    assert replica_url(config) == before and replication.read_generation_cache(db.parent) == GENERATION


def test_a_strict_sync_refuses_an_unreadable_environment_json_even_with_a_cache(tmp_path):
    config, db, state, backup, env = backup_setup(tmp_path, environment="not json")
    replication.write_generation_cache(db.parent, GENERATION)
    before = replica_url(config)
    with pytest.raises(replication.GenerationUnknown):
        replication.sync_generation(str(config), str(db), env, strict=True)
    assert replica_url(config) == before and replication.read_generation_cache(db.parent) == GENERATION


def test_the_strict_sync_command_exits_4_where_the_plain_one_keeps_the_cache(tmp_path, monkeypatch):
    config, db, state, backup, env = backup_setup(tmp_path, environment="not json")
    replication.write_generation_cache(db.parent, GENERATION)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("TICO_BACKUP_URL", raising=False)
    assert replication.main(["sync-generation", "--strict", str(config), str(db)]) == 4
    assert replication.main(["sync-generation", str(config), str(db)]) == 0
    assert replica_url(config) == "file://" + str(backup) + "/" + GENERATION


def database_with_a_wal_only_commit(path):
    """A WAL-mode database whose last commit is only in its -wal, as a running server leaves it (no checkpoint on close).
    Returns the -wal's bytes."""
    conn = leave_wal_for_litestream(sqlite3.connect(path, isolation_level=None))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE rows(id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO rows VALUES(1)")
    conn.close()
    return path.with_name(path.name + "-wal").read_bytes()


def test_a_forced_restore_keeps_the_current_database_with_the_wal_its_last_commit_is_in(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    wal = database_with_a_wal_only_commit(data / "hub.sqlite")
    assert wal
    result = entrypoint(tmp_path, "--force")
    assert result.returncode == 0, result.stderr
    kept = [p for p in data.glob("hub.sqlite.before-restore.*") if not p.name.endswith(("-wal", "-shm"))]
    assert len(kept) == 1, result.stdout
    aside = kept[0]
    assert aside.with_name(aside.name + "-wal").read_bytes() == wal     # the WAL moved with the database
    assert not (data / "hub.sqlite-wal").exists()
    kept_copy = sqlite3.connect(aside)
    try:
        assert kept_copy.execute("SELECT id FROM rows").fetchall() == [(1,)]   # its last commit is still there
    finally:
        kept_copy.close()


def test_a_wal_left_without_its_database_moves_aside_before_a_restore_writes_there(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "hub.sqlite-wal").write_bytes(b"orphan commits")
    result = entrypoint(tmp_path, "--force")
    assert result.returncode == 0, result.stderr
    assert not (data / "hub.sqlite-wal").exists()
    (orphan,) = data.glob("hub.sqlite.orphan-*-wal")
    assert orphan.read_bytes() == b"orphan commits"
    assert sqlite3.connect(data / "hub.sqlite").execute("SELECT x FROM t").fetchone() == (1,)


def test_a_seed_moves_a_wal_left_without_its_database_aside_before_it_restores(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "hub.sqlite-wal").write_bytes(b"orphan commits")
    with sqlite3.connect(tmp_path / "source.sqlite") as source:
        source.execute("CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT)")
    result, data = prepare(tmp_path, STUB_DB=str(tmp_path / "source.sqlite"))
    assert result.returncode == 0, result.stderr
    assert not (data / "hub.sqlite-wal").exists()
    (orphan,) = data.glob("hub.sqlite.orphan-*-wal")
    assert orphan.read_bytes() == b"orphan commits"


class ReplicaStub:
    """Just the listing write_status makes: the newest copy time and the highest TXID of the replica's first level."""
    def __init__(self, txid):
        self.txid, self.fail = txid, None

    def latest(self, levels, replica=""):
        if self.fail:
            raise self.fail
        return (1_793_000_000.0 if self.txid is not None else None), self.txid


def test_a_busy_install_restarts_its_lag_each_round_while_the_replica_keeps_up(tmp_path):
    path, replica, start, local, seen = tmp_path / "status.json", ReplicaStub(90), 1_793_000_000.0, 100, []
    for round_no in range(4):
        now = start + 30 * round_no
        replication.write_status(replica, path, local_txid=local, now=now)
        seen.append((json.loads(path.read_text())["behind_since"], now))
        replica.txid, local = local, local + 60    # the replica reaches what was local; local writes on
    assert [since for since, _ in seen] == [replication._stamp(now) for _, now in seen]    # never older than one round


def test_a_replica_that_stops_advancing_keeps_its_first_time_until_it_catches_up(tmp_path):
    path, replica, start = tmp_path / "status.json", ReplicaStub(90), 1_793_000_000.0
    seen = []
    for round_no, local in enumerate((100, 160, 220)):
        replication.write_status(replica, path, local_txid=local, now=start + 30 * round_no)
        seen.append(json.loads(path.read_text())["behind_since"])
    assert seen == [replication._stamp(start)] * 3
    assert json.loads(path.read_text())["pending_txid"] == f"{100:016x}"
    replica.txid = 220
    replication.write_status(replica, path, local_txid=220, now=start + 90)
    assert json.loads(path.read_text())["behind_since"] is None


def test_an_unreachable_listing_keeps_the_last_copy_counts_the_pile_up_and_leaves_checked_at_old(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"last_replicated_at": "2026-10-30T11:59:51Z", "behind_since": "2026-10-30T11:00:00Z",
                                "pending_txid": f"{5:016x}", "replica_txid": f"{3:016x}",
                                "checked_at": "2026-10-30T11:59:00Z"}))
    replica = ReplicaStub(None)
    replica.fail = ConnectionError("unreachable")
    replication.write_status(replica, path, unsent=27914, local_txid=9, now=1_793_361_600.0)
    saved = json.loads(path.read_text())
    assert saved["listing_error"] == "ConnectionError"
    assert saved["checked_at"] == "2026-10-30T11:59:00Z"          # not moved, so the age shows
    assert saved["unsent_files"] == 27914 and saved["local_txid"] == f"{9:016x}"
    assert saved["last_replicated_at"] == "2026-10-30T11:59:51Z" and saved["behind_since"] == "2026-10-30T11:00:00Z"
    replica.fail, replica.txid = None, 9
    replication.write_status(replica, path, unsent=0, local_txid=9, now=1_793_361_660.0)
    saved = json.loads(path.read_text())
    assert "listing_error" not in saved and saved["checked_at"] != "2026-10-30T11:59:00Z" and saved["behind_since"] is None


def test_a_generation_change_between_rounds_switches_levels_and_drops_the_old_lag(tmp_path):
    env = key_env(tmp_path)
    db = Path(env["TICO_DB"])
    level = replication.litestream_state_dir(db) / "ltx" / "0"
    level.mkdir(parents=True)
    for txid in range(1, 8):
        (level / f"{txid:016x}-{txid:016x}.ltx").write_bytes(b"x")
    add_copy("local", tmp_path / "backups", 0, 3, seconds_ago=10)    # the root replica holds TXID 3
    status_file, gen, rounds = Path(env["TICO_BACKUP_STATUS_FILE"]), "gen-20261101T000000Z", []

    def between_rounds(seconds):
        # The reset job lands between two rounds: the cache names a new generation, whose folder is still empty.
        rounds.append(seconds)
        if len(rounds) == 1:
            status_file.write_text(json.dumps({**json.loads(status_file.read_text()), "behind_since": "2026-10-31T00:00:00Z"}))
            replication.write_generation_cache(db.parent, gen)

    replication.loop(env, sleep=between_rounds, rounds=2)
    after = json.loads(status_file.read_text())
    assert after["generation"] == gen
    assert after["last_replicated_at"] is None and after["reason"] == replication.NO_RECENT_COPY    # not the root's copy
    assert after["behind_since"] not in (None, "2026-10-31T00:00:00Z")                              # old lag dropped


# -- rehearsal, strict reads, and the order of a generation switch -------------------------------------------------------

def test_a_rehearsal_reads_the_generation_environment_json_names_and_writes_nothing_to_the_backup_location(tmp_path):
    config, db, state, backup, env = backup_setup(tmp_path, environment='{"generation": "%s"}' % GENERATION)
    env["TICO_REHEARSAL"] = "1"
    replication.write_generation_cache(db.parent, "")    # the root, as a copy taken before the switch holds it
    marker = (backup / replication.MARKER).read_text()
    assert replication.sync_generation(str(config), str(db), env, strict=True) == "read"
    assert replica_url(config) == "file://" + str(backup) + "/" + GENERATION
    assert replication.read_generation_cache(db.parent) == GENERATION
    assert [p.name for p in backup.iterdir()] == [replication.MARKER] and (backup / replication.MARKER).read_text() == marker


def test_a_rehearsal_strict_read_refuses_an_unreadable_environment_json_even_with_a_cache(tmp_path):
    config, db, state, backup, env = backup_setup(tmp_path, environment="not json")
    env["TICO_REHEARSAL"] = "1"
    replication.write_generation_cache(db.parent, GENERATION)
    before = replica_url(config)
    with pytest.raises(replication.GenerationUnknown):
        replication.sync_generation(str(config), str(db), env, strict=True)
    assert replica_url(config) == before and replication.read_generation_cache(db.parent) == GENERATION


@pytest.mark.parametrize("strict", [False, True], ids=["plain", "strict"])
def test_a_root_marker_takes_the_cached_generation_only_when_the_read_is_not_strict(tmp_path, strict):
    config, db, state, backup, env = backup_setup(tmp_path, environment="{}")
    replication.write_generation_cache(db.parent, GENERATION)
    before = replica_url(config)
    if strict:
        with pytest.raises(replication.GenerationUnknown):
            replication.sync_generation(str(config), str(db), env, strict=True)
        assert replica_url(config) == before
    else:
        assert replication.sync_generation(str(config), str(db), env) == "kept"
        assert replica_url(config) == "file://" + str(backup) + "/" + GENERATION
    assert replication.read_generation_cache(db.parent) == GENERATION


def test_a_restore_refuses_a_root_marker_that_disagrees_with_the_cache_before_moving_the_database(tmp_path):
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / replication.MARKER).write_text("{}")
    data = tmp_path / "data"
    data.mkdir()
    (data / "hub.sqlite").write_bytes(b"database")
    (data / replication.GENERATION_CACHE).write_text(GENERATION + "\n")
    result = entrypoint(tmp_path, "--force")
    assert result.returncode != 0 and "backup generation" in result.stderr, result.stderr
    assert (data / "hub.sqlite").read_bytes() == b"database" and not list(data.glob("hub.sqlite.before-restore.*"))
    log = tmp_path / "stub.log"
    assert not log.exists() or "restore" not in log.read_text()          # litestream restore never ran


def test_a_rehearsal_never_starts_a_generation_even_with_a_stuck_copy_and_a_heal_request(tmp_path, monkeypatch):
    config, db, state, backup, env = backup_setup(tmp_path, copies=replication.LOCAL_STUCK_FILES + 1,
                                                  environment='{"environment_id": "acme1"}')
    env["TICO_REHEARSAL"] = "1"
    replication.write_heal_flag(replication.heal_flag_path(db), True, None)
    before = config.read_text()
    monkeypatch.setattr(replication.subprocess, "run", Listing(listing_at(REPLICA_STAMP)))
    assert replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW) is None
    assert config.read_text() == before and state.is_dir()
    assert [p.name for p in backup.iterdir()] == [replication.MARKER]
    assert_database_untouched(db)


def test_a_generation_switch_removes_the_old_local_state_after_its_commit_and_before_the_cache_names_the_new_folder(
        tmp_path, monkeypatch):
    old = "gen-20261031T000000Z"
    config, db, state, backup, env = backup_setup(tmp_path, copies=replication.LOCAL_STUCK_FILES + 1,
                                                  environment='{"generation": "%s"}' % old)
    replication.write_generation_cache(db.parent, old)
    monkeypatch.setattr(replication.subprocess, "run", Listing(listing_at(REPLICA_STAMP)))
    seen, remove = [], replication.shutil.rmtree

    def recording(path, *args, **kwargs):
        marker = replication.read_marker(replication.LocalMirror(str(backup)))
        seen.append((replication.read_generation_cache(db.parent), marker.get("generation")))
        return remove(path, *args, **kwargs)
    monkeypatch.setattr(replication.shutil, "rmtree", recording)
    message = replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW)
    assert message.startswith("tico: the local Litestream state was stuck; started backup generation " + GENERATION)
    assert seen == [(old, GENERATION)]           # the commit has landed; the cache still names the folder the copies belong to
    assert replication.read_generation_cache(db.parent) == GENERATION and not state.exists()


def test_a_switch_a_crash_left_unfinished_is_finished_with_the_cache_written_last(tmp_path, monkeypatch):
    old = "gen-20261031T000000Z"
    config, db, state, backup, env = backup_setup(tmp_path, copies=replication.LOCAL_STUCK_FILES + 1,
                                                  environment='{"generation": "%s"}' % GENERATION)
    replication.write_generation_cache(db.parent, old)     # the commit landed, the cache and the local state did not
    monkeypatch.setattr(replication.subprocess, "run", Listing("[]"))
    seen, remove = [], replication.shutil.rmtree

    def recording(path, *args, **kwargs):
        seen.append(replication.read_generation_cache(db.parent))
        return remove(path, *args, **kwargs)
    monkeypatch.setattr(replication.shutil, "rmtree", recording)
    assert replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW) == (
        "tico: the backup location is empty; removed Litestream's stale local state (.hub.sqlite-litestream)")
    assert seen == [old] and replica_url(config) == "file://" + str(backup) + "/" + GENERATION
    assert replication.read_generation_cache(db.parent) == GENERATION and not state.exists()


def test_an_unfinished_switch_whose_old_state_cannot_be_removed_stops_the_start_and_keeps_the_cache(
        tmp_path, monkeypatch):
    old = "gen-20261031T000000Z"
    config, db, state, backup, env = backup_setup(tmp_path, copies=replication.LOCAL_STUCK_FILES + 1,
                                                  environment='{"generation": "%s"}' % GENERATION)
    replication.write_generation_cache(db.parent, old)     # the commit landed, the cache and the local state did not
    monkeypatch.setattr(replication.subprocess, "run", Listing("[]"))

    def failing(path, *args, **kwargs):
        raise PermissionError("read-only volume")
    monkeypatch.setattr(replication.shutil, "rmtree", failing)
    with pytest.raises(replication.GenerationSwitchFailed):
        replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW)
    assert replication.read_generation_cache(db.parent) == old and state.is_dir()   # the next start tries again


class Recovering:
    """Stands in for `litestream ltx` on a backup location that cannot be read for its first `failures` calls, then prints
    `stdout`."""
    def __init__(self, failures, stdout="[]"):
        self.failures, self.stdout, self.calls = failures, stdout, 0

    def __call__(self, args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise subprocess.TimeoutExpired("litestream", 60)
        return subprocess.CompletedProcess(args, 0, self.stdout, "")


def no_wait(seconds):
    raise AssertionError("nothing should wait here, but it slept " + str(seconds) + " s")


def test_an_unfinished_switch_waits_for_the_backup_location_and_changes_nothing_until_it_reads(
        tmp_path, monkeypatch, capsys):
    old = "gen-20261031T000000Z"
    config, db, state, backup, env = backup_setup(tmp_path, copies=replication.LOCAL_STUCK_FILES + 1,
                                                  environment='{"generation": "%s"}' % GENERATION)
    replication.write_generation_cache(db.parent, old)     # the commit landed; the cache and the local state did not
    listing = Recovering(failures=2)
    monkeypatch.setattr(replication.subprocess, "run", listing)
    paused = []

    def sleep(seconds):    # what the cache and the local state are at each pause
        paused.append((seconds, replication.read_generation_cache(db.parent), state.is_dir()))
    message = replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW, sleep=sleep)
    assert paused == [(30, old, True), (60, old, True)]
    assert listing.calls == 3
    assert message == "tico: the backup location is empty; removed Litestream's stale local state (.hub.sqlite-litestream)"
    assert not state.exists() and replication.read_generation_cache(db.parent) == GENERATION
    assert replica_url(config) == "file://" + str(backup) + "/" + GENERATION
    assert len(capsys.readouterr().out.splitlines()) == 3      # one line per attempt: two failures, then the read
    assert_database_untouched(db)


def test_an_unfinished_switch_whose_local_state_is_gone_finishes_without_asking_the_backup_location(tmp_path, monkeypatch):
    old = "gen-20261031T000000Z"
    config, db, state, backup, env = backup_setup(tmp_path, copies=0, environment='{"generation": "%s"}' % GENERATION)
    replication.shutil.rmtree(state)                       # the switch got as far as removing the local state
    replication.write_generation_cache(db.parent, old)
    listing = Recovering(failures=10)
    monkeypatch.setattr(replication.subprocess, "run", listing)
    assert replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW, sleep=no_wait) is None
    assert listing.calls == 0 and replication.read_generation_cache(db.parent) == GENERATION
    assert_database_untouched(db)


def test_a_failed_listing_with_no_unfinished_switch_is_not_waited_for(tmp_path, monkeypatch):
    config, db, state, backup, env = backup_setup(tmp_path, copies=replication.LOCAL_STUCK_FILES + 1,
                                                  environment='{"generation": "%s"}' % GENERATION)
    replication.write_generation_cache(db.parent, GENERATION)
    monkeypatch.setattr(replication.subprocess, "run", Listing(error=subprocess.TimeoutExpired("litestream", 60)))
    message = replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW, sleep=no_wait)
    assert message == "tico: could not read the backup location; skipped the check for stale Litestream state"
    assert state.is_dir() and replication.read_generation_cache(db.parent) == GENERATION
    assert_database_untouched(db)


@pytest.mark.parametrize("cache", ["", None], ids=["kept", "root"])
def test_a_rotation_after_an_unreadable_marker_moves_the_cache_to_the_new_folder(tmp_path, monkeypatch, cache):
    # The first read of environment.json fails, so the start keeps the cache's folder (or the root). The bucket then answers,
    # the stuck copy rotates to a new folder, and the cache must follow the config rather than keep the old folder.
    config, db, state, backup, env = backup_setup(tmp_path, copies=replication.LOCAL_STUCK_FILES + 1,
                                                  environment='{"environment_id": "acme1"}')
    if cache is not None:
        replication.write_generation_cache(db.parent, cache)
    real, reads = replication.read_marker, []

    def unreachable_once(mirror):
        reads.append(mirror)
        if len(reads) == 1:
            raise OSError("the backup location is unreachable")
        return real(mirror)
    monkeypatch.setattr(replication, "read_marker", unreachable_once)
    monkeypatch.setattr(replication.subprocess, "run", Listing(listing_at(REPLICA_STAMP)))
    message = replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW)
    assert message.startswith("tico: the local Litestream state was stuck; started backup generation " + GENERATION)
    assert replication.read_generation_cache(db.parent) == GENERATION
    assert replica_url(config) == "file://" + str(backup) + "/" + GENERATION
    assert not state.exists() and len(reads) == 2


# -- the heal limits grow with the sync interval ------------------------------------------------------------------------

@pytest.mark.parametrize("copies", [4000, 6000])
def test_an_hourly_sync_does_not_heal_a_40_minute_stall_with_a_few_thousand_copies(tmp_path, monkeypatch, copies):
    check, stops, flag = stalled_copy(tmp_path, monkeypatch, copies=copies, TICO_BACKUP_SYNC_INTERVAL="1h")
    assert check(0) is False and check(40) is False and check(150) is False    # under three hours' worth of copies
    assert stops == [] and not flag.exists()


def test_an_hourly_sync_heals_after_two_intervals_once_the_copies_pass_three_intervals(tmp_path, monkeypatch):
    check, stops, flag = stalled_copy(tmp_path, monkeypatch, copies=3 * 3600 + 1, TICO_BACKUP_SYNC_INTERVAL="1h")
    assert check(0) is False and check(119) is False and stops == []
    assert check(121) is True and len(stops) == 1


def test_the_start_check_leaves_an_hourly_install_alone_that_the_one_minute_limits_would_call_stuck(tmp_path, monkeypatch):
    config, db, state, backup, env = backup_setup(tmp_path, copies=6000, environment='{"environment_id": "acme1"}')
    env["TICO_BACKUP_SYNC_INTERVAL"] = "1h"
    listing = json.loads(listing_at(REPLICA_STAMP))          # the replica is an hour behind
    assert replication.stuck_by_local_state(state, listing, {"TICO_BACKUP_SYNC_INTERVAL": "1m"}) is True
    assert replication.stuck_by_local_state(state, listing, env) is False
    monkeypatch.setattr(replication.subprocess, "run", Listing(listing_at(REPLICA_STAMP)))
    assert replication.reset_stale_litestream(str(config), str(db), env=env, now=NOW) is None
    assert state.is_dir() and replica_url(config) == "file://" + str(backup)


@pytest.mark.parametrize("locals_seen", [(100_000, 30), (50, 100, 60)])
def test_local_txids_that_go_back_start_the_lag_over(tmp_path, locals_seen):
    # A reset can write the new generation's cache while old local files are still there, so their high TXID becomes the
    # pending one. Then the reset clears them and local falls back: below the pending TXID, or below the last local TXID.
    # The replica stays at 29, so an interval kept from the old TXIDs would never end.
    path, replica, start, gen = tmp_path / "status.json", ReplicaStub(29), 1_793_000_000.0, "gen-20261101T000000Z"
    for round_no, local in enumerate(locals_seen):
        replication.write_status(replica, path, local_txid=local, generation=gen, now=start + 60 * round_no)
    saved = json.loads(path.read_text())
    assert saved["behind_since"] == replication._stamp(start + 60 * (len(locals_seen) - 1))
    assert saved["pending_txid"] == f"{locals_seen[-1]:016x}"
