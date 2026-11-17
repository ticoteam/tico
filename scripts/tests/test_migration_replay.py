"""Replay verdicts and private orchestration, with Docker replaced by a local fake."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from scripts import migration_replay as replay


@pytest.fixture
def live_database(tmp_path):
    data = tmp_path / "live data #?"
    data.mkdir()
    database = data / "hub.sqlite"
    conn = sqlite3.connect(database)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO tasks VALUES (?)", [(1,), (2,)])
        conn.commit()
        # Keep the writer open so committed rows stay in the WAL during backup.
        yield database
    finally:
        conn.close()


def mount_info(database, mount_type="volume"):
    return json.dumps(
        [
            {"Destination": "/other", "Source": "/unused", "Type": "bind"},
            {"Destination": "/data", "Source": str(database.parent), "Type": mount_type},
        ]
    ).encode()


def checks(counts=None, integrity_errors=0, foreign_keys=0):
    return {"counts": counts or {}, "integrity_errors": integrity_errors, "foreign_keys": foreign_keys}


def test_diff_reports_drops_first_and_only_surviving_losses_fail():
    before = checks({"removed": 500, "shrunk": 10, "grown": 2, "same": 4})
    after = checks({"shrunk": 9, "grown": 3, "same": 4, "added": 1})
    result = replay.verdict(before, after, True)
    assert result["dropped"] == ["removed"]
    assert result["new"] == ["added"]
    assert result["changed"] == {"grown": {"before": 2, "after": 3}, "shrunk": {"before": 10, "after": 9}}
    assert result["lost"] == ["shrunk"]
    assert not result["ok"]
    allowed = replay.verdict(before, after, True, ["shrunk"])
    assert allowed["ok"] and allowed["changed"] == result["changed"]


@pytest.mark.parametrize(
    ("before", "after", "migration_ok", "passed"),
    [
        (checks(foreign_keys=2), checks(foreign_keys=2), True, True),
        (checks(foreign_keys=2), checks(foreign_keys=1), True, True),
        (checks(foreign_keys=2), checks(foreign_keys=3), True, False),
        (checks(integrity_errors=1), checks(), True, False),
        (checks(), checks(integrity_errors=1), True, False),
        (checks(), checks(), False, False),
    ],
)
def test_check_and_migration_failures_cannot_be_waived(before, after, migration_ok, passed):
    result = replay.verdict(before, after, migration_ok, ["tasks"])
    assert result["ok"] is passed
    assert bool(result["failures"]) is not passed


def test_disk_check_includes_two_copies_and_the_reserve():
    assert replay.disk_check(100, 249, 50) == {
        "database_bytes": 100,
        "free_bytes": 249,
        "required_bytes": 250,
        "ok": False,
    }
    assert replay.disk_check(100, 250, 50)["ok"]


def test_arguments_keep_loss_exceptions_explicit_and_reserve_in_bytes():
    args = replay.arguments(["--container", "server", "--image", "candidate"])
    assert args.keep_free == 10 * replay.GIB and args.allow_loss == []
    args = replay.arguments(
        [
            "--container",
            "server",
            "--image",
            "candidate",
            "--keep-free",
            "0",
            "--allow-loss",
            "tasks",
            "--allow-loss",
            "messages",
        ]
    )
    assert args.keep_free == 0 and args.allow_loss == ["tasks", "messages"]


@pytest.mark.parametrize("extra", [["--keep-free", "-1"], ["--allow-loss", ""], ["--image=-v"]])
def test_arguments_refuse_invalid_reserves_exceptions_and_docker_options(extra):
    with pytest.raises(SystemExit) as error:
        replay.arguments(["--container", "server", "--image", "candidate", *extra])
    assert error.value.code == 2


def test_report_lists_dropped_tables_first_and_json_has_the_same_facts():
    before, after = checks({"old": 10, "tasks": 5}), checks({"new": 2, "tasks": 4})
    result = {
        "before": before,
        "after": after,
        "migration_seconds": 1.25,
        "disk": replay.disk_check(10, 100, 20),
        **replay.verdict(before, after, True),
    }
    text = replay.report(result)
    assert text.splitlines()[0] == "Dropped tables: old"
    assert "New tables: new" in text and "Rows tasks: 5 -> 4" in text
    assert "Migration: 1.250s" in text and "Result: FAIL" in text
    assert json.loads(text.splitlines()[-1].removeprefix("REPLAY ")) == result


def test_main_returns_failure_and_reports_only_safe_facts(monkeypatch, capsys):
    result = {"ok": False, "failures": ["snapshot failed"], "migration_seconds": None}
    monkeypatch.setattr(replay, "replay", lambda args: result)
    assert replay.main(["--container", "server", "--image", "candidate"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Dropped tables: not checked"
    assert json.loads(lines[-1][7:]) == result


@pytest.mark.parametrize("failure", [None, "migration", "interrupt", "snapshot"])
def test_docker_boundary_is_private_and_scratch_is_always_removed(tmp_path, live_database, monkeypatch, failure):
    calls, mounts = [], []
    before, after = checks({"tasks": 2}), checks({"tasks": 3})
    inspections = iter([before, after])
    monkeypatch.setattr(replay.tempfile, "gettempdir", lambda: str(tmp_path))
    connect = sqlite3.connect
    source_connections = []

    def connect_checked(database, *args, **kwargs):
        if isinstance(database, str) and database.startswith("file:"):
            assert database == live_database.resolve().as_uri() + "?mode=ro"
            assert kwargs == {"uri": True}
            source_connections.append(database)
        else:
            assert Path(database).parent.parent == tmp_path
            assert Path(database).parent != live_database.parent
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(replay.sqlite3, "connect", connect_checked)
    if failure == "snapshot":

        def failed_snapshot(database, destination):
            destination.touch()
            raise sqlite3.DatabaseError("customer value must not appear")

        monkeypatch.setattr(replay, "snapshot", failed_snapshot)

    def docker(*args, stdout=None):
        calls.append(args)
        assert args[0] != "exec"
        if args[0] == "inspect":
            assert args == ("inspect", "--format", "{{json .Mounts}}", "server")
            return mount_info(live_database)
        if args[0] == "run":
            mount = args[args.index("-v") + 1]
            mounts.append(Path(mount.removesuffix(":/replay:rw")))
            copy = mounts[-1] / "hub.sqlite"
            assert copy.stat().st_mode & 0o777 == 0o600
            with closing(connect(copy)) as conn:
                assert conn.execute("SELECT id FROM tasks ORDER BY id").fetchall() == [(1,), (2,)]
            assert args[args.index("--network") + 1] == "none"
            assert args[args.index("--log-driver") + 1] == "none"
            assert args[args.index("--pull") + 1] == "never"
            if args[-1] == replay.INSPECT:
                return json.dumps(next(inspections)).encode()
            assert stdout == subprocess.DEVNULL
            if failure == "migration":
                raise replay.ReplayError("customer value must not appear")
            if failure == "interrupt":
                raise KeyboardInterrupt
        return b""

    monkeypatch.setattr(replay, "docker", docker)
    result = replay.replay(replay.arguments(["--container", "server", "--image", "candidate", "--keep-free", "0"]))
    assert result["ok"] is (failure is None)
    assert calls[-1][:2] == ("rm", "--force")
    assert list(tmp_path.iterdir()) == [live_database.parent]
    assert all(not mount.exists() for mount in mounts)
    assert len(source_connections) == (0 if failure == "snapshot" else 1)
    with closing(connect(live_database)) as conn:
        assert conn.execute("SELECT id FROM tasks ORDER BY id").fetchall() == [(1,), (2,)]
    assert "customer value" not in replay.report(result)
    if failure == "migration":
        assert result["after"] == after and "migration failed" in result["failures"]


def test_insufficient_disk_refuses_before_snapshot_or_candidate(live_database, monkeypatch):
    calls = []
    monkeypatch.setattr(replay, "docker", lambda *args: calls.append(args) or mount_info(live_database, "bind"))
    size = live_database.stat().st_size + Path(str(live_database) + "-wal").stat().st_size
    monkeypatch.setattr(replay.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 2 * size - 1})())
    result = replay.replay(replay.arguments(["--container", "server", "--image", "candidate", "--keep-free", "0"]))
    assert result["failures"] == ["insufficient disk space"]
    assert result["disk"]["database_bytes"] == size
    assert len(calls) == 1 and calls[0][0] == "inspect"


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_unreadable_live_files_refuse_before_backup(live_database, monkeypatch, suffix, missing):
    calls = []
    monkeypatch.setattr(replay, "docker", lambda *args: calls.append(args) or mount_info(live_database))
    path_open = Path.open

    def unreadable(path, *args, **kwargs):
        if path == Path(str(live_database) + suffix):
            raise FileNotFoundError if missing else PermissionError
        return path_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", unreadable)
    result = replay.replay(replay.arguments(["--container", "server", "--image", "candidate"]))
    assert result["failures"] == [f"hub.sqlite{suffix} is missing or not readable"]
    assert len(calls) == 1 and calls[0][0] == "inspect"
    assert "disk" not in result


def test_missing_data_mount_refuses_before_backup(monkeypatch):
    monkeypatch.setattr(replay, "docker", lambda *args: b'[{"Destination":"/other","Source":"/unused"}]')
    result = replay.replay(replay.arguments(["--container", "server", "--image", "candidate"]))
    assert result["failures"] == ["container /data mount has no absolute host Source"]


def test_scratch_inside_live_mount_is_refused(live_database, monkeypatch):
    monkeypatch.setattr(replay, "docker", lambda *args: mount_info(live_database))
    monkeypatch.setattr(replay.tempfile, "gettempdir", lambda: str(live_database.parent))
    result = replay.replay(replay.arguments(["--container", "server", "--image", "candidate"]))
    assert result["failures"] == ["host scratch directory must be outside the /data mount"]
    assert not list(live_database.parent.glob("tico-replay-*"))
