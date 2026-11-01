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
    env = {**os.environ, "PATH": f"{bin_dir}:{Path(__import__('sys').executable).parent}:{os.environ['PATH']}",
           "TICO_DATA_DIR": str(data), "TICO_BACKUP_DIR": str(backups), "PYTHONPATH": str(ROOT),
           "STUB_LOG": str(tmp_path / "stub.log"), "STUB_DB": str(source), **(env or {})}
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


def add_copy(kind, store, level, seconds_ago=0, replica=""):
    """A replica file at a Litestream level (0000/ on S3, ltx/0 in a file replica), dated that long ago. `store` is the
    FakeS3 or the backup folder; `replica` is the folder of the replica under the backup location."""
    when = time.time() - seconds_ago
    if kind == "s3":
        key = "/".join(part for part in ("tico", replica, f"{level:04d}", f"{seconds_ago}.ltx") if part)
        store.objects[key] = b""
        store.modified[key] = datetime.fromtimestamp(when, timezone.utc)
    else:
        path = store / replica / f"ltx/{level}" / f"{seconds_ago}.ltx"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        os.utime(path, (when, when))


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_a_recent_copy_is_level_zero_then_level_one_and_snapshots_never_count(tmp_path, kind):
    fake = FakeS3()
    store = fake if kind == "s3" else tmp_path / "backups"
    mirror = replication.S3Mirror(fake, "bucket", "tico") if kind == "s3" else replication.LocalMirror(store)
    now = time.time()
    add_copy(kind, store, 9, seconds_ago=60)        # a daily snapshot, however new, is not a copy
    assert mirror.newest(replication.FRESH_LEVELS) is None
    add_copy(kind, store, 1, seconds_ago=120)       # level 1 counts while level 0 is empty
    assert mirror.newest(replication.FRESH_LEVELS) == pytest.approx(now - 120, abs=5)
    add_copy(kind, store, 0, seconds_ago=30)        # and level 0 wins whenever it has a file
    assert mirror.newest(replication.FRESH_LEVELS) == pytest.approx(now - 30, abs=5)


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_the_freshness_listing_reads_only_the_replica_folder_it_is_given(tmp_path, kind):
    fake = FakeS3()
    store = fake if kind == "s3" else tmp_path / "backups"
    mirror = replication.S3Mirror(fake, "bucket", "tico") if kind == "s3" else replication.LocalMirror(store)
    now, generation = time.time(), "gen-20261101T000000Z"
    add_copy(kind, store, 0, seconds_ago=30)
    assert mirror.newest(replication.FRESH_LEVELS, replica=generation) is None
    add_copy(kind, store, 0, seconds_ago=10, replica=generation)
    assert mirror.newest(replication.FRESH_LEVELS, replica=generation) == pytest.approx(now - 10, abs=5)
    assert mirror.newest(replication.FRESH_LEVELS) == pytest.approx(now - 30, abs=5)


def test_the_status_says_null_and_why_when_only_snapshots_reached_the_backup_location(tmp_path):
    env = key_env(tmp_path)
    add_copy("local", tmp_path / "backups", 9, seconds_ago=60)
    replication.loop(env, sleep=lambda s: None, rounds=1)
    stuck = replication.status(env)
    assert stuck["last_replicated_at"] is None and stuck["reason"] == replication.NO_RECENT_COPY
    add_copy("local", tmp_path / "backups", 0, seconds_ago=5)
    replication.loop(env, sleep=lambda s: None, rounds=1)
    fresh = replication.status(env)
    assert fresh["last_replicated_at"] and fresh["reason"] is None


def test_the_status_counts_the_local_level_zero_files_by_name(tmp_path):
    env = key_env(tmp_path)
    level = replication.litestream_state_dir(tmp_path / "data" / "hub.sqlite") / "ltx" / "0"
    level.mkdir(parents=True)
    for txid in (1, 2, 3):
        (level / f"{txid:016x}-{txid:016x}.ltx").write_bytes(b"x")
    assert replication.unsent_files(tmp_path / "data" / "hub.sqlite") == 3
    assert replication.unsent_files(tmp_path / "nowhere" / "hub.sqlite") == 0
    replication.loop(env, sleep=lambda s: None, rounds=1)
    assert replication.status(env)["unsent_files"] == 3


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


def stale_litestream_state(tmp_path):
    """A database with the WAL and Litestream's local state beside it, as an earlier run leaves them."""
    data = tmp_path / "data"
    data.mkdir()
    db = data / "hub.sqlite"
    db.write_bytes(b"database")
    (data / "hub.sqlite-wal").write_bytes(b"wal")
    state = data / ".hub.sqlite-litestream"
    (state / "ltx" / "0").mkdir(parents=True)
    (state / "ltx" / "0" / "0000000000000003-0000000000000003.ltx").write_bytes(b"ltx")
    return db, state


def assert_database_untouched(db):
    assert db.read_bytes() == b"database" and (db.parent / "hub.sqlite-wal").read_bytes() == b"wal"


def test_an_empty_replica_clears_the_stale_state_and_nothing_else(tmp_path, monkeypatch, capsys):
    db, state = stale_litestream_state(tmp_path)
    listing = Listing("[]")
    monkeypatch.setattr(replication.subprocess, "run", listing)
    assert replication.main(["reset-stale-litestream", "litestream.yml", str(db)]) == 0
    assert not state.exists()
    assert_database_untouched(db)
    assert capsys.readouterr().out.strip() == "tico: the backup location is empty; removed Litestream's stale local state (.hub.sqlite-litestream)"
    (args, kwargs), = listing.calls
    assert args[:2] == ["litestream", "ltx"] and args[args.index("-config") + 1] == "litestream.yml" and args[-1] == str(db)
    assert 0 < kwargs["timeout"] <= 120


def test_a_replica_with_any_file_at_any_level_keeps_the_state(tmp_path, monkeypatch, capsys):
    db, state = stale_litestream_state(tmp_path)
    monkeypatch.setattr(replication.subprocess, "run", Listing('[{"level": 9, "min_txid": "0000000000000001", "max_txid": "0000000000000004"}]'))
    assert replication.main(["reset-stale-litestream", "litestream.yml", str(db)]) == 0
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
    db, state = stale_litestream_state(tmp_path)
    monkeypatch.setattr(replication.subprocess, "run", listing)
    assert replication.main(["reset-stale-litestream", "litestream.yml", str(db)]) == 0
    assert state.is_dir()
    assert_database_untouched(db)
    assert capsys.readouterr().out.strip() == "tico: could not read the backup location; skipped the check for stale Litestream state"


def test_without_local_state_the_replica_is_not_even_asked(tmp_path, monkeypatch, capsys):
    db = tmp_path / "hub.sqlite"
    db.write_bytes(b"database")
    listing = Listing()
    monkeypatch.setattr(replication.subprocess, "run", listing)
    assert replication.main(["reset-stale-litestream", "litestream.yml", str(db)]) == 0
    assert listing.calls == [] and capsys.readouterr().out == "" and db.read_bytes() == b"database"
