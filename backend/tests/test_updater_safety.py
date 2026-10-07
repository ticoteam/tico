"""docker/updater.py: the database snapshot around an update, and the updater replacing itself."""
import json

import pytest

from backend.tests.test_updater_runner import load


class Fake:
    """`docker`: records argv, and answers inspect/ps/config from tables. `fail` names a compose verb that exits 1."""

    def __init__(self, module, monkeypatch, healthy=(True,), fail=(), container=None, config=None):
        self.calls, self.answers, self.fail, self.container, self.config = [], list(healthy), set(fail), container, config
        monkeypatch.setattr(module.subprocess, "run", self.run)
        monkeypatch.setattr(module, "healthy", lambda seconds: self.answers.pop(0))
        monkeypatch.setattr(module, "running_image", lambda: ("sha256:old", "v0.1.0"))
        monkeypatch.setattr(module, "pinned_services", lambda version: [])
        monkeypatch.setattr(module, "check_switched", lambda version, release, seconds=None: None)

    def verbs(self):
        return [next(x for x in a[a.index("--project-directory") + 2:] if not x.startswith("-")) for a, _ in self.calls
                if a[:2] == ["docker", "compose"]]

    def run(self, argv, env=None, **kw):
        self.calls.append((argv, env))
        out, code = "", 0
        if argv[:2] == ["docker", "compose"]:
            verb = next(x for x in argv[argv.index("--project-directory") + 2:] if not x.startswith("-"))
            code = 1 if verb in self.fail else 0
            out = "updater-1\n" if "ps" in argv else json.dumps(self.config) if verb == "config" else ""
        elif argv[:2] == ["docker", "inspect"]:
            out = json.dumps([self.container(argv[2])])
        return type("R", (), {"returncode": code, "stdout": out, "stderr": "boom" if code else ""})()


def test_a_snapshot_is_taken_before_the_switch_and_left_alone_when_the_update_works(monkeypatch, tmp_path):
    updater = load(monkeypatch, "", tmp_path)
    docker = Fake(updater, monkeypatch, [True])
    updater.update("v0.2.0")
    assert updater.status["state"] == "healthy" and updater.status["snapshot"].startswith("pre-update-v0.1.0-")
    verbs = docker.verbs()
    assert verbs.index("exec") < verbs.index("up") and "run" not in verbs and updater.status["restored"] is False
    exec_call = next(a for a, _ in docker.calls if "exec" in a)
    assert "/data/snapshots" in exec_call and "hub.sqlite" in updater.SNAPSHOT_SCRIPT and "backup(" in updater.SNAPSHOT_SCRIPT


def test_a_failed_health_check_restores_the_snapshot_because_migrations_may_have_run(monkeypatch, tmp_path):
    updater = load(monkeypatch, "", tmp_path)
    docker = Fake(updater, monkeypatch, [False, True])
    updater.update("v0.2.0")
    status = updater.status
    assert status["state"] == "rolled_back" and status["restored"] is True and status["snapshot"] in status["message"]
    assert "Went back to v0.1.0" in status["message"] and "Restored the database" in status["message"]
    verbs = docker.verbs()
    assert verbs[-3:] == ["stop", "run", "up"]                       # server stopped, snapshot back, old image up
    run_call, env = next((a, e) for a, e in docker.calls if "run" in a and a[:2] == ["docker", "compose"])
    assert env["TICO_TAG"] == "v0.1.0" and "--entrypoint" in run_call     # the OLD image puts it back
    assert json.loads((tmp_path / ".updater-status.json").read_text())["restored"] is True


def test_the_restore_script_swaps_the_database_and_clears_litestreams_tracking(monkeypatch, tmp_path):
    import sqlite3, subprocess, sys
    updater = load(monkeypatch, "", tmp_path)
    for name, value in (("snap.sqlite", "before"), ("hub.sqlite", "migrated")):
        db = sqlite3.connect(tmp_path / name)
        db.execute("CREATE TABLE t(v)"); db.execute("INSERT INTO t VALUES(?)", (value,)); db.commit(); db.close()
    (tmp_path / ".hub.sqlite-litestream").mkdir()
    (tmp_path / ".hub.sqlite-litestream" / "ltx").write_text("x")
    (tmp_path / "hub.sqlite-wal").write_text("stale")
    subprocess.run([sys.executable, "-c", updater.RESTORE_SCRIPT, str(tmp_path / "snap.sqlite"), str(tmp_path / "hub.sqlite")], check=True)
    value = lambda name: sqlite3.connect(tmp_path / name).execute("SELECT v FROM t").fetchone()[0]
    recovered, = tmp_path.glob("hub.sqlite.failed-update-*/recovery.sqlite")
    assert value("hub.sqlite") == "before" and value(recovered) == "migrated"
    assert not (tmp_path / ".hub.sqlite-litestream").exists() and not (tmp_path / "hub.sqlite-wal").exists()
    assert not (tmp_path / "hub.sqlite.restoring").exists()


def test_a_pull_failure_or_snapshot_failure_does_not_touch_the_database(monkeypatch, tmp_path):
    updater = load(monkeypatch, "", tmp_path)
    docker = Fake(updater, monkeypatch, [True], fail=["exec"])
    updater.update("v0.2.0")
    assert updater.status["state"] == "rolled_back" and "could not snapshot" in updater.status["message"]
    assert "stop" not in docker.verbs() and updater.status["restored"] is False


def rendered(updater, server, slack=None):
    """What `docker compose config --format json` prints for the server (and Slack) images."""
    services = {"server": {"image": server}, "updater": {"image": "ghcr.io/ticoteam/tico-updater:v0.1.0"}}
    return {"services": {**services, **({"slack": {"image": slack}} if slack else {})}}


@pytest.mark.parametrize("server, slack, pinned", [("ghcr.io/ticoteam/tico:v0.2.0", "ghcr.io/acme/tico:v0.1.0", "slack")])
def test_an_override_that_pins_the_image_is_refused_before_anything_changes(monkeypatch, tmp_path, server, slack, pinned):
    updater = load(monkeypatch, "", tmp_path)
    check = updater.pinned_services
    docker = Fake(updater, monkeypatch, config=rendered(updater, server, slack))
    monkeypatch.setattr(updater, "pinned_services", check)
    updater.update("v0.2.0")
    assert updater.status["state"] == "failed"
    assert "compose.override.yaml pins the image for %s; remove `image:`" % pinned in updater.status["message"]
    assert docker.verbs() == ["config"] and docker.calls[0][1]["TICO_TAG"] == "v0.2.0"   # rendered with the target tag


def switch(monkeypatch, tmp_path, release, after="sha256:new", tag="v0.1.0"):
    """An update to v0.2.0 with the real preflight and post-switch checks; the new server reports `release`."""
    import io
    updater = load(monkeypatch, "", tmp_path)
    (tmp_path / ".env").write_text("TICO_TEAM_NAME=Acme\n")
    checks = updater.pinned_services, updater.check_switched
    docker = Fake(updater, monkeypatch, [True, True], container=lambda ref: {"Id": "sha256:new"},
                  config=rendered(updater, "ghcr.io/ticoteam/tico:v0.2.0", "ghcr.io/ticoteam/tico:v0.2.0"))
    monkeypatch.setattr(updater, "pinned_services", checks[0])
    monkeypatch.setattr(updater, "check_switched", checks[1])
    images = iter([("sha256:old", tag), (after, "v0.2.0")])
    monkeypatch.setattr(updater, "running_image", lambda: next(images))
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda url, timeout=0: io.BytesIO(json.dumps({"ok": True, "release": release}).encode()))
    return updater, docker


def test_a_correct_render_and_the_new_release_pass(monkeypatch, tmp_path):
    updater, docker = switch(monkeypatch, tmp_path, "v0.2.0")
    updater.update("v0.2.0")
    assert updater.status["state"] == "healthy", updater.status["message"]
    assert docker.verbs()[0] == "config" and "TICO_TAG=v0.2.0" in (tmp_path / ".env").read_text()


@pytest.mark.parametrize("release, after, reason", [("v0.1.0", "sha256:new", "the server reports v0.1.0, not v0.2.0"),
                                                    ("v0.2.0", "sha256:old", "not running the v0.2.0 image")])
def test_the_wrong_version_after_the_switch_is_rolled_back(monkeypatch, tmp_path, release, after, reason):
    # An untagged image: the release to go back to is the one the server reported.
    updater, docker = switch(monkeypatch, tmp_path, release, after=after, tag="latest")
    updater.update("v0.2.0", running="0.1.0")
    status = updater.status
    assert status["state"] == "rolled_back" and reason in status["message"] and "Went back to v0.1.0" in status["message"]
    assert ["docker", "tag", "sha256:old", updater.IMAGE + ":v0.1.0"] in [a for a, _ in docker.calls]
    assert docker.verbs()[-3:] == ["stop", "run", "up"] and docker.calls[-1][1]["TICO_TAG"] == "v0.1.0"
    assert "TICO_TAG" not in (tmp_path / ".env").read_text()


def container(updater, mode_dir, tag="v0.1.0"):
    def inspect(ref):
        return {"Image": "sha256:oldupdater", "Name": "/tico-updater-1", "Config": {"Image": "ghcr.io/ticoteam/tico-updater:" + tag},
                "Mounts": [{"Destination": updater.PROJECT, "Source": str(mode_dir)}], "State": {"Running": True}}
    return inspect


@pytest.mark.parametrize("mode", ["server", "runner"])
def test_after_an_update_a_helper_from_the_new_image_replaces_the_updater(monkeypatch, tmp_path, mode):
    updater = load(monkeypatch, mode, tmp_path)
    monkeypatch.setenv("TICO_UPDATER_SELF", "always")
    monkeypatch.setenv("TICO_UPDATER_PULL", "always")
    monkeypatch.setenv("HOSTNAME", "abc")
    monkeypatch.setattr(updater, "SELF_UPDATE", "always")
    docker = Fake(updater, monkeypatch, container=container(updater, "/srv/tico"))
    note = updater.replace_updater("v0.2.0")
    assert "replacing itself with v0.2.0" in note
    pulled = [a for a, _ in docker.calls if a[:2] == ["docker", "pull"]]
    assert pulled == [["docker", "pull", "ghcr.io/ticoteam/tico-updater:v0.2.0"]]
    run = next(a for a, _ in docker.calls if a[:3] == ["docker", "run", "-d"])
    joined = " ".join(run)
    assert "ghcr.io/ticoteam/tico-updater:v0.2.0 python /usr/local/bin/tico-updater replace-self" in joined
    assert "/srv/tico:/srv/tico" in joined and "TICO_PROJECT_DIR=/srv/tico" in joined and "TICO_SWAP_OLD_ID=sha256:oldupdater" in joined
    assert "TICO_UPDATER_MODE=" + mode in joined
    assert ("TICO_COMPOSE_FILE=runner.compose.yaml" in joined) == (mode == "runner")


def helper(monkeypatch, tmp_path, image_after):
    updater = load(monkeypatch, "server", tmp_path)
    monkeypatch.setenv("TICO_SWAP_TAG", "v0.2.0")
    monkeypatch.setenv("TICO_SWAP_OLD_ID", "sha256:oldupdater")
    monkeypatch.setenv("TICO_SWAP_OLD_REF", "ghcr.io/ticoteam/tico-updater:v0.1.0")
    (tmp_path / ".env").write_text("TICO_TAG=v0.2.0\nTICO_UPDATER_TAG=v0.1.0\n")
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    clock = iter(range(0, 10000, 5))
    monkeypatch.setattr(updater.time, "time", lambda: next(clock))
    docker = Fake(updater, monkeypatch, container=lambda ref: {"Image": image_after, "State": {"Running": True}})
    return updater, docker


def test_the_helper_puts_the_old_updater_back_when_the_new_one_does_not_stay_up(monkeypatch, tmp_path):
    updater, docker = helper(monkeypatch, tmp_path, "sha256:oldupdater")    # still the old image: it never got replaced
    assert updater.replace_self() == 1
    assert ["docker", "tag", "sha256:oldupdater", "ghcr.io/ticoteam/tico-updater:v0.1.0"] in [a for a, _ in docker.calls]
    assert [e["TICO_UPDATER_TAG"] for a, e in docker.calls if "up" in a] == ["v0.2.0", "v0.1.0"]
    assert "TICO_UPDATER_TAG=v0.1.0" in (tmp_path / ".env").read_text()      # the pin never moved


def test_the_new_updater_does_not_inherit_the_helpers_pull_and_bundle_settings(monkeypatch, tmp_path):
    updater, docker = helper(monkeypatch, tmp_path, "sha256:newupdater")
    monkeypatch.setenv("TICO_UPDATER_PULL", "never")     # how replace_updater starts the helper
    monkeypatch.setenv("TICO_UPDATER_BUNDLE", "never")
    assert updater.replace_self() == 0
    up = next(e for a, e in docker.calls if "up" in a)
    assert "TICO_UPDATER_PULL" not in up and "TICO_UPDATER_BUNDLE" not in up


def crashed_database(tmp_path):
    import sqlite3, subprocess, sys
    for name in ("snapshot.sqlite", "hub.sqlite"):
        with sqlite3.connect(tmp_path / name) as db:
            db.execute("CREATE TABLE t(v)")
            db.execute("INSERT INTO t VALUES('before')")
    # An abrupt exit leaves a committed row solely in the WAL, as a crashed server can.
    subprocess.run([sys.executable, "-c", """
import os, sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute('PRAGMA journal_mode=WAL')
c.execute('PRAGMA wal_autocheckpoint=0')
c.execute("INSERT INTO t VALUES('after snapshot')")
c.commit()
os._exit(0)
""", str(tmp_path / "hub.sqlite")], check=True)
    assert (tmp_path / "hub.sqlite-wal").stat().st_size > 0
    return tmp_path / "snapshot.sqlite", tmp_path / "hub.sqlite"


def read_values(path):
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return [r[0] for r in db.execute("SELECT v FROM t")]


def test_rollback_keeps_wal_commits_and_all_prior_recovery_evidence(monkeypatch, tmp_path):
    import subprocess, sys
    updater = load(monkeypatch, "", tmp_path)
    snapshot, db = crashed_database(tmp_path)
    old = tmp_path / "hub.sqlite.failed-update"
    old.write_bytes(b"prior evidence")
    assert read_values(db) == ["before", "after snapshot"]
    subprocess.run([sys.executable, "-c", updater.RESTORE_SCRIPT, str(snapshot), str(db)], check=True)
    recovered, = tmp_path.glob("hub.sqlite.failed-update-*/recovery.sqlite")
    assert read_values(recovered) == ["before", "after snapshot"]
    assert read_values(db) == ["before"]
    assert not (tmp_path / "hub.sqlite-wal").exists()
    subprocess.run([sys.executable, "-c", updater.RESTORE_SCRIPT, str(snapshot), str(db)], check=True)
    assert len(list(tmp_path.glob("hub.sqlite.failed-update-*/recovery.sqlite"))) == 2
    assert read_values(recovered) == ["before", "after snapshot"]
    assert old.read_bytes() == b"prior evidence"


def test_unreadable_failed_database_keeps_a_raw_main_and_sidecar_set(monkeypatch, tmp_path):
    import subprocess, sys
    updater = load(monkeypatch, "", tmp_path)
    snapshot, db = crashed_database(tmp_path)
    db.write_bytes(b"unreadable fictional database")
    evidence = {suffix: (tmp_path / ("hub.sqlite" + suffix)).read_bytes() for suffix in ("", "-wal")
                if (tmp_path / ("hub.sqlite" + suffix)).exists()}
    subprocess.run([sys.executable, "-c", updater.RESTORE_SCRIPT, str(snapshot), str(db)], check=True)
    raw, = tmp_path.glob("hub.sqlite.failed-update-*/raw")
    assert (raw / "hub.sqlite-shm").exists()  # SQLite may rebuild this index while opening read-only.
    for suffix, data in evidence.items():
        assert (raw / ("hub.sqlite" + suffix)).read_bytes() == data
    assert not list(tmp_path.glob("hub.sqlite.failed-update-*/recovery.sqlite"))
    assert read_values(db) == ["before"]


@pytest.mark.parametrize("failure", ["sync"])
def test_preservation_failure_leaves_the_live_database_and_wal_untouched(monkeypatch, tmp_path, failure):
    import subprocess, sys
    updater = load(monkeypatch, "", tmp_path)
    snapshot, db = crashed_database(tmp_path)
    tracking = tmp_path / ".hub.sqlite-litestream"
    tracking.mkdir()
    if failure == "raw-copy":
        db.write_bytes(b"unreadable fictional database")
    evidence = {suffix: (tmp_path / ("hub.sqlite" + suffix)).read_bytes() for suffix in ("", "-wal")}
    # Fault injection runs the exact script; only recovery I/O fails, after staging succeeds.
    prefix = """
import builtins, os, tempfile
real_open, real_mkdtemp, real_fsync = builtins.open, tempfile.mkdtemp, os.fsync
def fail(*args, **kwargs):
    raise OSError('fictional preservation failure')
"""
    if failure == "directory":
        prefix += "tempfile.mkdtemp = fail\n"
    elif failure == "sync":
        prefix += "count = 0\ndef fsync(fd):\n    global count\n    count += 1\n    return fail() if count > 1 else real_fsync(fd)\nos.fsync = fsync\n"
    else:
        prefix += "def checked_open(path, mode='r', *a, **kw):\n    if '/raw/' in str(path) and 'w' in mode: fail()\n    return real_open(path, mode, *a, **kw)\nbuiltins.open = checked_open\n"
    result = subprocess.run([sys.executable, "-c", prefix + updater.RESTORE_SCRIPT, str(snapshot), str(db)], capture_output=True)
    assert result.returncode != 0 and b"fictional preservation failure" in result.stderr
    assert tracking.exists()
    assert (tmp_path / "hub.sqlite-shm").exists()
    for suffix, data in evidence.items():
        assert (tmp_path / ("hub.sqlite" + suffix)).read_bytes() == data
    if failure != "raw-copy":
        assert read_values(db) == ["before", "after snapshot"]


def test_a_slow_first_answer_after_the_switch_is_tried_again_not_rolled_back(monkeypatch, tmp_path):
    import io
    updater, docker = switch(monkeypatch, tmp_path, "v0.2.0")
    answers = iter([OSError("connection refused"), json.dumps({"ok": True, "release": "v0.2.0"})])
    def urlopen(url, timeout=0):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(answer.encode())
    monkeypatch.setattr(updater.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    images = iter([("sha256:old", "v0.1.0"), ("sha256:new", "v0.2.0"), ("sha256:new", "v0.2.0")])
    monkeypatch.setattr(updater, "running_image", lambda: next(images))
    updater.update("v0.2.0")
    assert updater.status["state"] == "healthy", updater.status["message"]


def test_prune_also_removes_dangling_images_of_its_own_repositories_only(monkeypatch, tmp_path):
    updater = load(monkeypatch, "", tmp_path)
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        out = ""
        if argv[:3] == ["docker", "image", "ls"] and "dangling=true" in argv:
            out = {"ghcr.io/ticoteam/tico": "sha256:aaa\nsha256:old\n", "ghcr.io/ticoteam/tico-updater": "sha256:bbb\n"}[argv[3]]
        elif argv[:3] == ["docker", "image", "ls"]:
            out = "v0.2.0\nv0.1.0\nv0.0.9\nlatest\n<none>\n" if argv[3] == "ghcr.io/ticoteam/tico" else ""
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()
    monkeypatch.setattr(updater.subprocess, "run", run)
    updater.prune_images(keep={"v0.2.0", "v0.1.0"}, keep_ids={"sha256:old"})
    # The old tagged release and both repositories' dangling images go; the replaced image (by id) stays.
    assert [a[2] for a in calls if a[:2] == ["docker", "rmi"]] == ["ghcr.io/ticoteam/tico:v0.0.9", "sha256:aaa", "sha256:bbb"]
    assert all(a[3].startswith("ghcr.io/ticoteam/tico") for a in calls if a[:3] == ["docker", "image", "ls"])
