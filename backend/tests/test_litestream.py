"""An actual Litestream process, WAL writes, and independent restores from its replica."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest
import yaml

from backend.config import ROOT


def test_continuous_replication_restores_wal_commits(tmp_path):
    binary = Path(os.environ.get("LITESTREAM_BINARY", ROOT / "runtime/cloud-tools/litestream"))
    if not binary.is_file():
        pytest.skip("Install the checksum-pinned Litestream binary to run the restore integration test")
    source = tmp_path / "live.sqlite"
    replica = tmp_path / "replica"
    config = tmp_path / "litestream.yml"
    config.write_text(yaml.safe_dump({"dbs": [{"path": str(source), "replica": {"url": replica.as_uri(), "sync-interval": "100ms"}}]}))
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, body TEXT)")
    connection.commit()
    with (tmp_path / "replicate.log").open("w") as log:
        process = subprocess.Popen([str(binary), "replicate", "-config", str(config)], stdout=log, stderr=log)
        try:
            for n in range(30):
                connection.execute("INSERT INTO messages VALUES(?,?)", (n, "A durable conversation " + str(n)))
                connection.commit()
                time.sleep(0.02)
            deadline, index, restored = time.monotonic() + 20, 0, False
            while time.monotonic() < deadline:
                assert process.poll() is None, "Replication process exited"
                dest = tmp_path / f"restored-{index}.sqlite"
                result = subprocess.run([str(binary), "restore", "-o", str(dest), replica.as_uri()], capture_output=True, timeout=10)
                if result.returncode == 0:
                    with sqlite3.connect(dest) as c:
                        if c.execute("SELECT count(*) FROM messages").fetchone()[0] == 30:
                            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                            assert c.execute("SELECT body FROM messages WHERE id=29").fetchone()[0] == "A durable conversation 29"
                            restored = True
                            break
                index += 1
                time.sleep(0.2)
            assert restored, "Replica never restored all committed messages"
        finally:
            process.terminate()
            process.wait(timeout=15)
            connection.close()


# Fast local retention and compaction: Litestream then drops the local copy of its first changes while the replica
# still has them, the state an earlier run leaves behind on a host (the bug backend/replication.py resets).
STALE_STATE_CONFIG = {"l0-retention": "1s", "l0-retention-check-interval": "1s", "levels": [{"level": 1, "interval": "1s"}]}


def stale_state_setup(tmp_path, binary):
    """A database replicated to a file replica until its first local changes are gone; the replica is then moved away,
    so it is empty and the local state is not. Returns (config, db, replica, state dir)."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "litestream").symlink_to(binary.resolve())
    source, replica = tmp_path / "data" / "hub.sqlite", tmp_path / "replica"
    source.parent.mkdir()
    config = tmp_path / "litestream.yml"
    config.write_text(yaml.safe_dump({**STALE_STATE_CONFIG, "dbs": [{"path": str(source), "replica": {"url": replica.as_uri(), "sync-interval": "100ms"}}]}))
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE rows(id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    state = source.parent / ".hub.sqlite-litestream"
    with replicating(binary, config):
        write(source, range(5))
        wait_until(lambda: not any(p.name.startswith("0000000000000001-") for p in (state / "ltx" / "0").glob("*.ltx")), 20)
    return config, source, replica, state


class replicating:
    """A `litestream replicate` process, stopped on exit: politely, or at once when `graceful` is False (a stuck shutdown)."""
    def __init__(self, binary, config, graceful=True):
        self.binary, self.config, self.graceful = binary, config, graceful

    def __enter__(self):
        self.process = subprocess.Popen([str(self.binary), "replicate", "-config", str(self.config)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        return self

    def __exit__(self, *exc):
        if self.process.poll() is None and not self.graceful:
            self.process.kill()
            self.process.wait()
        elif self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def write(source, ids, pause=0.3):
    connection = sqlite3.connect(source)
    for n in ids:
        connection.execute("INSERT INTO rows VALUES(?)", (n,))
        connection.commit()
        time.sleep(pause)
    connection.close()


def wait_until(condition, seconds):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.2)


def restored_ids(binary, replica, tmp_path):
    """The ids the replica restores to, or None when it has nothing to restore."""
    dest = tmp_path / "restored.sqlite"
    dest.unlink(missing_ok=True)
    result = subprocess.run([str(binary), "restore", "-o", str(dest), replica.as_uri()], capture_output=True, timeout=30)
    if result.returncode != 0:
        return None
    with sqlite3.connect(dest) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return [row[0] for row in c.execute("SELECT id FROM rows ORDER BY id")]


def reset_stale_state(tmp_path, config, source, **extra_env):
    """The command as the entrypoint runs it: `python -m backend.replication`, with `litestream` on PATH."""
    env = {**os.environ, "PATH": str(tmp_path / "bin") + os.pathsep + os.environ["PATH"], **extra_env}
    return subprocess.run([sys.executable, "-m", "backend.replication", "reset-stale-litestream", str(config), str(source)],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def litestream_binary():
    binary = Path(os.environ.get("LITESTREAM_BINARY", ROOT / "runtime/cloud-tools/litestream"))
    if not binary.is_file():
        pytest.skip("Install the checksum-pinned Litestream binary to run the stale-state integration tests")
    return binary


@pytest.mark.slow
def test_a_moved_replica_never_takes_new_writes_without_the_reset(tmp_path):
    binary = litestream_binary()
    config, source, replica, _ = stale_state_setup(tmp_path, binary)
    replica.rename(tmp_path / "replica-moved")                 # the backup location is new: nothing in it
    with replicating(binary, config, graceful=False):
        write(source, range(100, 110))
        time.sleep(2)                                          # Litestream stays stuck on the missing first change
    ids = restored_ids(binary, replica, tmp_path)
    assert ids is None or 109 not in ids, "the new writes reached the replica without the reset"


@pytest.mark.slow
def test_a_moved_replica_takes_new_writes_after_the_reset(tmp_path):
    binary = litestream_binary()
    config, source, replica, state = stale_state_setup(tmp_path, binary)
    replica.rename(tmp_path / "replica-moved")
    result = reset_stale_state(tmp_path, config, source)
    assert result.returncode == 0, result.stderr
    assert "removed Litestream's stale local state" in result.stdout and not state.exists()
    with replicating(binary, config):
        write(source, range(100, 110))
    assert restored_ids(binary, replica, tmp_path) == list(range(5)) + list(range(100, 110))


@pytest.mark.slow
def test_a_populated_replica_is_left_alone_by_the_reset(tmp_path):
    binary = litestream_binary()
    config, source, replica, state = stale_state_setup(tmp_path, binary)
    result = reset_stale_state(tmp_path, config, source)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and state.is_dir()
    with replicating(binary, config):
        write(source, range(100, 110))
    assert restored_ids(binary, replica, tmp_path) == list(range(5)) + list(range(100, 110))


# -- backup generations, with the real binary --------------------------------------------------------------------------

def stuck_copy(tmp_path, binary):
    """A populated replica whose marker names an environment, and the local state a stuck Litestream leaves: far more L0
    copies than a healthy install keeps, the newest written two hours after the replica's copies."""
    config, source, replica, state = stale_state_setup(tmp_path, binary)
    (replica / "environment.json").write_text(json.dumps({"environment_id": "acme1"}))
    ahead = time.time() + 2 * 3600
    for n in range(5001):
        path = state / "ltx" / "0" / ("%016x-%016x.ltx" % (n + 1000, n + 1000))
        path.write_bytes(b"ltx")
        os.utime(path, (ahead, ahead))
    return config, source, replica, state


def generation_of(config):
    return yaml.safe_load(config.read_text())["dbs"][0]["replica"]["url"].rsplit("/", 1)[1]


def entrypoint_restore(tmp_path, backup):
    """`docker/entrypoint.sh restore` into an empty data volume, with the real litestream on PATH. Returns (result, db)."""
    data = tmp_path / "restored-volume"
    data.mkdir()
    env = {**os.environ, "PATH": str(tmp_path / "bin") + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
           "TICO_DATA_DIR": str(data), "TICO_BACKUP_DIR": str(backup), "TICO_LITESTREAM_CONFIG": str(tmp_path / "restore.yml"),
           "PYTHONPATH": str(ROOT)}
    env.pop("TICO_BACKUP_URL", None)
    env.pop("BASH_ENV", None)
    result = subprocess.run(["bash", str(ROOT / "docker/entrypoint.sh"), "restore"], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    return result, data / "hub.sqlite"


def rows_of(db):
    connection = sqlite3.connect(db)
    try:
        return [row[0] for row in connection.execute("SELECT id FROM rows ORDER BY id")]
    finally:
        connection.close()


@pytest.mark.slow
def test_a_stuck_copy_moves_to_a_new_generation_and_new_writes_land_there(tmp_path):
    binary = litestream_binary()
    config, source, replica, state = stuck_copy(tmp_path, binary)
    result = reset_stale_state(tmp_path, config, source, TICO_BACKUP_DIR=str(replica))
    assert result.returncode == 0, result.stderr
    generation = generation_of(config)
    assert generation.startswith("gen-") and "started backup generation " + generation in result.stdout
    assert not state.exists()
    assert json.loads((replica / "environment.json").read_text()) == {"environment_id": "acme1", "generation": generation}
    with replicating(binary, config):
        write(source, range(100, 110))
    assert restored_ids(binary, replica / generation, tmp_path) == list(range(5)) + list(range(100, 110))
    assert restored_ids(binary, replica, tmp_path) == list(range(5))     # the copy it had is kept as it was


@pytest.mark.slow
def test_a_restore_after_a_rotation_returns_the_newest_data(tmp_path):
    binary = litestream_binary()
    config, source, replica, _ = stuck_copy(tmp_path, binary)
    assert reset_stale_state(tmp_path, config, source, TICO_BACKUP_DIR=str(replica)).returncode == 0
    with replicating(binary, config):
        write(source, range(100, 110))
    result, restored = entrypoint_restore(tmp_path, replica)
    assert result.returncode == 0, result.stderr
    assert rows_of(restored) == list(range(5)) + list(range(100, 110))


@pytest.mark.slow
def test_a_root_layout_replica_without_a_generation_restores_through_the_entrypoint_as_before(tmp_path):
    binary = litestream_binary()
    source, replica = tmp_path / "live" / "hub.sqlite", tmp_path / "replica"
    source.parent.mkdir()
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "litestream").symlink_to(binary.resolve())
    config = tmp_path / "litestream.yml"
    config.write_text(yaml.safe_dump({"dbs": [{"path": str(source), "replica": {"url": replica.as_uri(), "sync-interval": "100ms"}}]}))
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE rows(id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    with replicating(binary, config):
        write(source, range(5))
        wait_until(lambda: any(replica.rglob("*.ltx")), 20)
    result, restored = entrypoint_restore(tmp_path, replica)
    assert result.returncode == 0, result.stderr
    assert rows_of(restored) == list(range(5))
