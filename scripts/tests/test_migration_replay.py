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
    for name, key in (("credential.key", b"c" * 32), ("slack.key", b"s" * 32)):
        (data / name).write_bytes(key)
        (data / name).chmod(0o640)
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


def mount_info(database, mount_type="volume", env=None, source=None):
    return json.dumps(
        {
            "Mounts": [
                {"Destination": "/other", "Source": "/unused", "Type": "bind"},
                {"Destination": "/data", "Source": str(source or database.parent), "Type": mount_type},
            ],
            "Config": {"Env": [f"{key}={value}" for key, value in (env or {}).items()]},
        }
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
    assert result["failures"] == ["row loss: shrunk"]
    allowed = replay.verdict(before, after, True, ["shrunk"])
    assert not allowed["failures"] and allowed["changed"] == result["changed"]


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
    assert args.keep_free == 10 * replay.GIB and args.allow_loss == [] and not args.allow_warnings
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


def test_report_lists_dropped_tables_first_and_json_has_changes_and_totals_not_every_count():
    before, after = checks({"old": 10, "tasks": 5}), checks({"new": 2, "tasks": 4})
    result = {
        "before": before,
        "after": after,
        "migration_seconds": 1.25,
        "disk": replay.disk_check(10, 100, 20),
        "ok": False,
        **replay.verdict(before, after, True),
    }
    text = replay.report(result)
    assert text.splitlines()[0] == "Dropped tables: old"
    assert "New tables: new" in text and "Rows tasks: 5 -> 4" in text
    assert "Migration: 1.250s" in text and "Result: FAIL" in text
    summary = json.loads(text.splitlines()[-1].removeprefix("REPLAY "))
    assert summary["changed"] == result["changed"] and summary["dropped"] == ["old"] and summary["new"] == ["new"]
    assert summary["before"] == {"integrity_errors": 0, "foreign_keys": 0, "tables": 2, "rows": 15}
    assert summary["after"] == {"integrity_errors": 0, "foreign_keys": 0, "tables": 2, "rows": 6}
    assert "Before: integrity ok" in text and "2 tables, 15 rows" in text


def test_main_returns_failure_and_reports_only_safe_facts(monkeypatch, capsys):
    result = {"ok": False, "failures": ["snapshot failed"], "migration_seconds": None}
    monkeypatch.setattr(replay, "replay", lambda args: result)
    assert replay.main(["--container", "server", "--image", "candidate"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Dropped tables: not checked"
    assert json.loads(lines[-1][7:]) == result


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "migration",
        "interrupt",
        "snapshot",
        "kms",
        "warnings",
        "warnings_allowed",
        "cleanup_present",
        "cleanup_interrupt",
        "cleanup_verification",
        "cleanup_verify_interrupt",
        "cleanup_rm_failure",
    ],
)
def test_docker_boundary_is_private_and_scratch_is_always_removed(
    tmp_path, live_database, monkeypatch, capsys, scenario
):
    calls, mounts = [], []
    before, after = checks({"tasks": 2}), checks({"tasks": 3})
    inspections = iter([before, after])
    monkeypatch.setattr(replay.tempfile, "gettempdir", lambda: str(tmp_path))
    connect = sqlite3.connect
    source_connections = []
    env = {"TICO_DB": "/data/hub.sqlite", "TICO_PRIVATE_SETTING": "private=environment value # $ literal"}
    image_env = {"PATH": "/old-image/bin", "PYTHONPATH": "/old-image"}  # the live image's own, never copied
    if scenario == "kms":
        env.update(TICO_CREDENTIAL_KMS_KEY="private-kms-key", AWS_DEFAULT_REGION="private-region")
    key_reads = []
    path_open = Path.open

    def open_checked(path, mode="r", *args, **kwargs):
        if path.parent == live_database.parent and path.name.endswith(".key"):
            assert mode == "rb"
            key_reads.append(path.name)
        return path_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_checked)

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
    if scenario == "snapshot":

        def failed_snapshot(database, destination):
            destination.touch()
            raise sqlite3.DatabaseError("customer value must not appear")

        monkeypatch.setattr(replay, "snapshot", failed_snapshot)

    def docker(*args, stdout=None, stderr=subprocess.DEVNULL):
        calls.append(args)
        assert args[0] != "exec"
        if args[0] == "inspect":
            assert args == ("inspect", "--format", "{{json .}}", "server")
            return mount_info(live_database, env={**env, **image_env})
        if args[0] == "run":
            mount = args[args.index("-v") + 1]
            mounts.append(Path(mount.removesuffix(":/replay:rw")))
            copy = mounts[-1] / "hub.sqlite"
            env_file = Path(args[args.index("--env-file") + 1])
            assert env_file.parent == copy.parent
            assert env_file.stat().st_mode & 0o777 == 0o600
            copied_env = dict(line.split("=", 1) for line in env_file.read_text().splitlines())
            assert copied_env == {**env, "TICO_DB": "/replay/hub.sqlite"}
            for name, key in (("credential.key", b"c" * 32), ("slack.key", b"s" * 32)):
                key_file = copy.parent / name
                assert key_file.read_bytes() == key
                assert key_file.stat().st_mode & 0o777 == 0o600
                assert f"{key_file}:/replay/{name}:ro" in args
            assert copy.stat().st_mode & 0o777 == 0o600
            with closing(connect(copy)) as conn:
                assert conn.execute("SELECT id FROM tasks ORDER BY id").fetchall() == [(1,), (2,)]
            assert args[args.index("--network") + 1] == ("bridge" if scenario == "kms" else "none")
            assert args[args.index("--log-driver") + 1] == "none"
            assert args[args.index("--pull") + 1] == "never"
            if args[-1] == replay.INSPECT:
                return json.dumps(next(inspections)).encode()
            assert stdout == subprocess.DEVNULL
            assert replay.os.fstat(stderr.fileno()).st_mode & 0o777 == 0o600
            if scenario in ("warnings", "warnings_allowed"):
                stderr.write(
                    b"WARNING:tico.migrations:private warning value\n"
                    b"/app/migration.py:12: UserWarning: private warning value\n"
                    b"  warnings.warn('private warning value')\n"
                    b"ERROR:tico.migrations:private warning value\n"
                    b"CRITICAL:tico.migrations:private warning value\n"
                    b"INFO:tico.migrations:private info value\n"
                )
            if scenario == "migration":
                raise replay.ReplayError("customer value must not appear")
            if scenario == "interrupt":
                raise KeyboardInterrupt
        if args[0] == "rm":
            if scenario in ("cleanup_present", "cleanup_rm_failure"):
                raise replay.ReplayError("private docker error")
            if scenario == "cleanup_interrupt":
                raise KeyboardInterrupt
        if args[0] == "ps":
            name = calls[-2][-1]
            assert args == ("ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.ID}}")
            if scenario == "cleanup_present":
                return b"private-container-id\n"
            if scenario == "cleanup_verification":
                raise replay.ReplayError("private docker error")
            if scenario == "cleanup_verify_interrupt":
                raise KeyboardInterrupt
        return b""

    monkeypatch.setattr(replay, "docker", docker)
    extra = ["--allow-warnings"] if scenario == "warnings_allowed" else []
    result = replay.replay(
        replay.arguments(["--container", "server", "--image", "candidate", "--keep-free", "0", *extra])
    )
    assert result["ok"] is (scenario in ("success", "kms", "warnings_allowed", "cleanup_rm_failure"))
    assert calls[-2][:2] == ("rm", "--force") and calls[-1][0] == "ps"
    assert list(tmp_path.iterdir()) == [live_database.parent]
    assert all(not mount.exists() for mount in mounts)
    assert len(source_connections) == (0 if scenario == "snapshot" else 1)
    assert key_reads == ["credential.key", "slack.key"]
    for name, key in (("credential.key", b"c" * 32), ("slack.key", b"s" * 32)):
        assert (live_database.parent / name).read_bytes() == key
        assert (live_database.parent / name).stat().st_mode & 0o777 == 0o640
    with closing(connect(live_database)) as conn:
        assert conn.execute("SELECT id FROM tasks ORDER BY id").fetchall() == [(1,), (2,)]
    text = replay.report(result)
    for value in (
        *env.values(),
        "c" * 32,
        "s" * 32,
        "private warning value",
        "private info value",
        "private docker error",
        "private-container-id",
    ):
        assert value not in text and value not in str(calls)
    assert "customer value" not in text
    assert "network: on (KMS)" in text if scenario == "kms" else "network: none" in text
    if scenario in ("warnings", "warnings_allowed"):
        assert result["warnings"] == 4 and "Migration warnings: 4" in text
        assert ("migration warnings" in result["failures"]) is (scenario == "warnings")
    if scenario == "cleanup_present":
        assert result["failures"] == [f"replay container still present: {calls[-2][-1]}"]
    if scenario in ("cleanup_interrupt", "cleanup_verify_interrupt"):
        assert result["failures"] == ["interrupted"]
    if scenario == "cleanup_verification":
        assert result["failures"] == ["replay container cleanup verification failed"]
    if scenario == "migration":
        assert result["after"] == after and "migration failed" in result["failures"]
    monkeypatch.setattr(replay, "replay", lambda args: result)
    assert replay.main(["--container", "server", "--image", "candidate"]) == int(not result["ok"])
    assert capsys.readouterr().out == text + "\n"


def test_configured_database_resolves_keys_beside_it(tmp_path, live_database, monkeypatch):
    source = live_database.parent.parent
    setting = "/data/" + str(live_database.relative_to(source))
    monkeypatch.setattr(
        replay, "docker", lambda *args: mount_info(live_database, env={"TICO_DB": setting}, source=source)
    )
    database, size, env, found_source = replay.live_database("server")
    assert database == live_database and size > 0
    assert found_source == source
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    env_file, mounts = replay.replay_settings(database, env, scratch)
    assert env_file.read_text() == "TICO_DB=/replay/hub.sqlite\n"
    assert mounts == [
        "-v",
        f"{scratch / 'credential.key'}:/replay/credential.key:ro",
        "-v",
        f"{scratch / 'slack.key'}:/replay/slack.key:ro",
    ]


@pytest.mark.parametrize(
    "entry",
    ["TICO_PRIVATE=first\nsecond", "AWS_PRIVATE=first\rsecond", "TICO_PRIVATE=first\0second", "TICO_A-B=private"],
)
def test_unrepresentable_environment_fails_without_disclosing_values(live_database, monkeypatch, entry):
    info = json.loads(mount_info(live_database))
    info["Config"]["Env"] = [entry]
    monkeypatch.setattr(replay, "docker", lambda *args: json.dumps(info).encode())
    result = replay.replay(replay.arguments(["--container", "server", "--image", "candidate"]))
    assert not result["ok"]
    assert result["failures"] == ["live environment cannot be represented in an env file"]
    assert entry not in replay.report(result)


def test_docker_captures_diagnostics_without_relaying_them(tmp_path, monkeypatch, capsys):
    diagnostics = tmp_path / "stderr"

    def run(args, stdout, stderr, check):
        assert args == ["docker", "run", "candidate"] and stdout == subprocess.DEVNULL and not check
        stderr.write(b"WARNING:tico:private diagnostic\n")
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(replay.subprocess, "run", run)
    with diagnostics.open("wb") as stderr, pytest.raises(replay.ReplayError, match="^Docker step failed$"):
        replay.docker("run", "candidate", stdout=subprocess.DEVNULL, stderr=stderr)
    assert replay.warning_count(diagnostics) == 1
    captured = capsys.readouterr()
    assert not captured.out and not captured.err


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
    monkeypatch.setattr(replay, "docker", lambda *args: b'{"Mounts":[],"Config":{"Env":[]}}')
    result = replay.replay(replay.arguments(["--container", "server", "--image", "candidate"]))
    assert result["failures"] == ["container /data mount has no absolute host Source"]


def test_scratch_inside_live_mount_is_refused(live_database, monkeypatch):
    monkeypatch.setattr(replay, "docker", lambda *args: mount_info(live_database))
    monkeypatch.setattr(replay.tempfile, "gettempdir", lambda: str(live_database.parent))
    result = replay.replay(replay.arguments(["--container", "server", "--image", "candidate"]))
    assert result["failures"] == ["host scratch directory must be outside the /data mount"]
    assert not list(live_database.parent.glob("tico-replay-*"))


def test_code_puts_a_checkouts_backend_over_the_images_read_only(tmp_path, monkeypatch):
    (tmp_path / "backend" / "migrations").mkdir(parents=True)
    args = replay.arguments(["--container", "server", "--image", "candidate", "--code", str(tmp_path)])
    calls = []
    monkeypatch.setattr(replay, "docker", lambda *a: calls.append(a) or b"/opt/tico\n")
    assert replay.code_mount(args.image, args.code) == ["-v", f"{tmp_path.resolve()}/backend:/opt/tico/backend:ro"]
    assert calls == [("image", "inspect", "--format", "{{.Config.WorkingDir}}", "candidate")]
    assert replay.code_mount("candidate", None) == []


def test_code_must_be_a_checkout_with_migrations(tmp_path):
    with pytest.raises(SystemExit):
        replay.arguments(["--container", "server", "--image", "candidate", "--code", str(tmp_path)])


def test_unset_and_image_variables_are_left_out(live_database, monkeypatch):
    info = json.loads(mount_info(live_database))
    # Compose's unset names carry no "="; the image's own variables may hold anything.
    info["Config"]["Env"] = ["TICO_UNSET", "XAI_API_KEY", "PATH=/bin", "NOTE=first\nsecond", "TICO_KEEP=yes"]
    monkeypatch.setattr(replay, "docker", lambda *args: json.dumps(info).encode())
    _, _, env, _ = replay.live_database("server")
    assert env == {"TICO_KEEP": "yes"}
