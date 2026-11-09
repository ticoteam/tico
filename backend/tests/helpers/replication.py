"""Helpers that other tests share, moved out of test_replication.py so they import no test file."""

import os
import sqlite3
import subprocess
import textwrap
from pathlib import Path
from backend.config import ROOT


ENTRYPOINT = ROOT / "docker/entrypoint.sh"


def entrypoint(tmp_path, *args, command="restore", env=None):
    data, backups, bin_dir = tmp_path / "data", tmp_path / "backups", tmp_path / "bin"
    data.mkdir(exist_ok=True)
    backups.mkdir(exist_ok=True)
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "litestream"
    # restore -if-replica-exists -config CFG DB: write the database it would have restored
    stub.write_text(
        textwrap.dedent("""\
        #!/bin/sh
        echo "$@" >> "$STUB_LOG"
        if [ "$1" = restore ]; then
          [ -z "$STUB_FAIL" ] || { echo "AccessDenied" >&2; exit 1; }
          [ -f "$STUB_DB" ] && cp "$STUB_DB" "$(eval echo \\${$#})"
        fi
        exit 0
    """)
    )
    stub.chmod(0o755)
    source = tmp_path / "source.sqlite"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE IF NOT EXISTS t(x)")
        db.execute("INSERT INTO t VALUES(1)")
    extra = env or {}
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{Path(__import__('sys').executable).parent}:{os.environ['PATH']}",
        "TICO_DATA_DIR": str(data),
        "TICO_BACKUP_DIR": str(backups),
        "PYTHONPATH": str(ROOT),
        "STUB_LOG": str(tmp_path / "stub.log"),
        "STUB_DB": str(source),
        "TICO_LITESTREAM_CONFIG": str(tmp_path / "litestream.yml"),
        **extra,
    }  # not the shared /tmp default: xdist workers race on it
    if (
        "TICO_BACKUP_URL" not in extra
    ):  # an inherited bucket setting must not reach the tests; one a test passes is its own
        env.pop("TICO_BACKUP_URL", None)
    # An inherited BASH_ENV can restore the turn's PATH and run real litestream and uvicorn instead of the stubs.
    env.pop("BASH_ENV", None)
    return subprocess.run(
        ["bash", str(ENTRYPOINT), command, *([] if command == "prepare" else args)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def prepare(tmp_path, restore_fails=False, **extra):
    """`docker/entrypoint.sh prepare` on a file replica (the tico-backups directory), with a fake litestream."""
    env = {
        "TICO_COMPANY_NAME": "Acme",
        "TICO_OWNER_EMAIL": "owner@example.com",
        "TICO_AUTH_PROXY": "none",
        "TICO_LOCAL_OWNER_TOKEN_FILE": str(tmp_path / "token"),
        "STUB_FAIL": "1" if restore_fails else "",
        "STUB_DB": str(tmp_path / "no-replica"),
        **extra,
    }
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    result = entrypoint(tmp_path, "prepare", command="prepare", env=env)
    return result, data
