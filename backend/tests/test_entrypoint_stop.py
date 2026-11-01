"""docker/entrypoint.sh `server` on a stop: the server gets TICO_STOP_GRACE and is then killed, and only after it has
exited is Litestream stopped, so its last sync covers every write. A server or Litestream that exits on its own ends
the container, the other stopped in the same order."""
import contextlib
import json
import os
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.config import ROOT

ENTRYPOINT = ROOT / "docker/entrypoint.sh"


def new_bash() -> bool:
    """`wait -n -p` is bash 5.1 (the image has 5.2)."""
    bash = shutil.which("bash")
    r = subprocess.run([bash, "-c", "(( BASH_VERSINFO[0] > 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] >= 1) ))"])
    return r.returncode == 0


pytestmark = [pytest.mark.slow, pytest.mark.skipif(not new_bash(), reason="needs bash 5.1 or later")]

# The server: logs that it is up, and on SIGTERM logs it and keeps running (as the real one does, for many seconds),
# or exits with STUB_SERVER_EXIT once Litestream is up (so the stop comes after its trap is set). The attachment loop:
# sleeps. Anything else: the real interpreter.
PYTHON = """#!/bin/sh
if [ "$1 $2" = "-m backend.server" ]; then
  echo "server up" >> "$STUB_LOG"
  [ -z "$STUB_SERVER_EXIT" ] || { until grep -qx "litestream up" "$STUB_LOG" 2>/dev/null; do sleep 0.05; done; exit "$STUB_SERVER_EXIT"; }
  trap 'echo "server TERM" >> "$STUB_LOG"' TERM
  while :; do sleep 0.05; done
fi
if [ "$1 $2 $3" = "-m backend.replication loop" ]; then exec sleep 600; fi
if [ "$1 $2 $3" = "-m backend.replication reset-stale-litestream" ] && [ -n "$STUB_RESET_EXIT" ]; then exit "$STUB_RESET_EXIT"; fi
if [ "$1 $2 $3" = "-m backend.replication reset-stale-litestream" ] && [ -n "$STUB_RESET_WAIT" ]; then echo "reset waiting" >> "$STUB_LOG"; exec sleep 600; fi
exec %s "$@"
"""

# Litestream: restore finds no replica; replicate logs the replica url its config names, then runs until SIGTERM, then takes
# a moment for its last sync.
LITESTREAM = """#!/bin/sh
[ "$1" = replicate ] || exit 0
echo "replica $(sed -n 's/^ *url: //p' "$3")" >> "$STUB_LOG"
trap 'echo "litestream TERM" >> "$STUB_LOG"; sleep 0.3; echo "litestream synced" >> "$STUB_LOG"; exit 0' TERM
echo "litestream up" >> "$STUB_LOG"
[ -z "$STUB_LITESTREAM_EXIT" ] || { sleep 1; echo "litestream died" >> "$STUB_LOG"; exit 2; }
while :; do sleep 0.05; done
"""


def start(tmp_path, **extra):
    bin_dir, data, backups = tmp_path / "bin", tmp_path / "data", tmp_path / "backups"
    for d in (bin_dir, data, backups):
        d.mkdir(exist_ok=True)
    for name, text in (("python", PYTHON % shlex.quote(sys.executable)), ("litestream", LITESTREAM)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "stub.log"
    env = {**os.environ, "PATH": f"{bin_dir}:{Path(sys.executable).parent}:{os.environ['PATH']}",
           "TICO_DATA_DIR": str(data), "TICO_BACKUP_DIR": str(backups), "PYTHONPATH": str(ROOT),
           "TICO_LITESTREAM_CONFIG": str(tmp_path / "litestream.yml"), "TICO_COMPANY_NAME": "Acme",
           "TICO_OWNER_EMAIL": "owner@example.com", "TICO_AUTH_PROXY": "none", "STUB_LOG": str(log),
           "STUB_SERVER_EXIT": "", "STUB_LITESTREAM_EXIT": "", **extra}
    for key in ("TICO_BACKUP_URL", "BASH_ENV", "TICO_BACKUP", "TICO_REHEARSAL"):
        env.pop(key, None)
    env.update(extra)
    # Output to a file, not a pipe: a stub left behind (a failure here) would hold a pipe open. Its own session, so
    # `finish` can clean up everything it started, as the container's end does.
    out = (tmp_path / "out.txt").open("w")
    p = subprocess.Popen(["bash", str(ENTRYPOINT), "server"], env=env, stdout=out, stderr=subprocess.STDOUT,
                         start_new_session=True)
    out.close()
    return p, log


def finish(p, tmp_path, timeout=60.0) -> str:
    """Wait for the entry point to exit; then end whatever it left running. Its output."""
    try:
        p.wait(timeout)
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(p.pid, signal.SIGKILL)
    return (tmp_path / "out.txt").read_text()


def lines(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def until(check, timeout=60.0):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def test_a_stop_kills_a_slow_server_after_the_grace_then_lets_litestream_sync(tmp_path):
    p, log = start(tmp_path, TICO_STOP_GRACE="0.5")
    try:
        until(lambda: {"server up", "litestream up"} <= set(lines(log)))
        began = time.monotonic()
        p.send_signal(signal.SIGTERM)
        p.wait(20)
        took = time.monotonic() - began
    finally:
        out = finish(p, tmp_path)
    assert p.returncode == 0, out
    seen = lines(log)
    assert seen.index("server TERM") < seen.index("litestream TERM") < seen.index("litestream synced"), seen
    assert 0.5 <= took < 3, took                 # the grace, Litestream's sync, and not Docker's 10 s
    assert "shutdown-sync-timeout: 5s" in (tmp_path / "litestream.yml").read_text().splitlines()


def test_a_server_that_exits_ends_the_container_with_its_code_after_litestream_syncs(tmp_path):
    p, log = start(tmp_path, STUB_SERVER_EXIT="3")
    out = finish(p, tmp_path)
    assert p.returncode == 3, out
    seen = lines(log)
    assert seen.index("server up") < seen.index("litestream TERM") < seen.index("litestream synced"), seen


def test_a_litestream_that_dies_stops_the_server_and_fails(tmp_path):
    p, log = start(tmp_path, STUB_LITESTREAM_EXIT="1", TICO_STOP_GRACE="0.2")
    out = finish(p, tmp_path)
    assert p.returncode == 1, out
    seen = lines(log)
    assert seen.index("litestream died") < seen.index("server TERM"), seen
    assert "Litestream exited; stopping the server" in out


def test_a_generation_switch_that_broke_off_keeps_litestream_from_starting_on_its_config(tmp_path):
    # reset-stale-litestream exits 3 only after environment.json may name a new generation: Litestream must not start.
    p, log = start(tmp_path, STUB_RESET_EXIT="3", TICO_STOP_GRACE="0.2")
    out = finish(p, tmp_path)
    assert p.returncode != 0, out
    assert "litestream up" not in lines(log), lines(log)


def test_a_reset_job_waiting_for_the_backup_location_leaves_the_server_up_and_ends_on_a_stop(tmp_path):
    # An unfinished generation switch waits in the reset job while the backup location cannot be read. The server keeps
    # running meanwhile, and a stop ends the wait (the job is a shell subshell, which a TERM ends at once).
    p, log = start(tmp_path, STUB_RESET_WAIT="1", TICO_STOP_GRACE="0.2")
    try:
        until(lambda: {"server up", "reset waiting"} <= set(lines(log)))
        assert p.poll() is None, (tmp_path / "out.txt").read_text()
        began = time.monotonic()
        p.send_signal(signal.SIGTERM)
        p.wait(20)
        took = time.monotonic() - began
    finally:
        out = finish(p, tmp_path)
    assert p.returncode == 0, out
    assert "litestream up" not in lines(log) and "server TERM" in lines(log)
    assert took < 3, took


def test_backups_off_stops_the_server_the_same_way(tmp_path):
    p, log = start(tmp_path, TICO_BACKUP="off", TICO_STOP_GRACE="0.3")
    try:
        until(lambda: "server up" in lines(log))
        began = time.monotonic()
        p.send_signal(signal.SIGTERM)
        p.wait(20)
        took = time.monotonic() - began
    finally:
        out = finish(p, tmp_path)
    assert p.returncode == 0, out
    assert "litestream up" not in lines(log) and "server TERM" in lines(log)
    assert took < 2, took


def test_a_bad_grace_is_refused(tmp_path):
    p, _ = start(tmp_path, TICO_STOP_GRACE="soon")
    out = finish(p, tmp_path)
    assert p.returncode != 0 and "TICO_STOP_GRACE" in out
