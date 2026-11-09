"""An upgraded database boots: code that runs before the server's lifespan (create_app's installs, backend.boot's
environment_identity) must not read a renamed column before the cleanup migrations have run.

The old database is the last release's own schema (schema_pre_cleanup.sql, user_version 71)."""

import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from backend.app import create_app
from backend.config import ROOT, Settings
from backend.migrations import renames as schema_renames

FIXTURE = Path(__file__).parent / "fixtures" / "schema_pre_cleanup.sql"


def pre_rename_db(path):
    """The last release before the renames, as its own schema dump: the boot under test is the real upgrade."""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(FIXTURE.read_text())
        assert schema_renames.pending(conn)


def columns(path, table):
    with closing(sqlite3.connect(path)) as conn:
        return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def copy_db(source, target):
    with closing(sqlite3.connect(source)) as s, closing(sqlite3.connect(target)) as t:
        s.backup(t)
    return target


# Booting the old schema runs every rename and migration, so the database is built once per run.
@pytest.fixture(scope="module")
def old_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("old") / "hub.sqlite"
    pre_rename_db(path)
    return path


def test_create_app_on_a_pre_rename_database(tmp_path, old_db):
    db = copy_db(old_db, tmp_path / "hub.sqlite")
    create_app(Settings(db_path=db))  # the installs open store transactions before the lifespan's initialize
    assert "refs" in columns(db, "messages")
    assert "value" in columns(db, "registry_metadata")


def environment_identity(tmp_path, db, **extra):
    env = {**os.environ, "TICO_DB": str(db), "PYTHONPATH": str(ROOT), **extra}
    for name in ("TICO_ENVIRONMENT_ID", "TICO_TEAM_NAME", "TICO_COMPANY_NAME"):
        if name not in extra:
            env.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-c", "from backend.boot import environment_identity; print(environment_identity())"],
        env=env,
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r"[a-z0-9-]+", result.stdout.strip()), result.stdout
    return result.stdout.strip()


@pytest.mark.parametrize("extra", [{}, {"TICO_TEAM_NAME": "Acme"}], ids=["no team", "named team, no environment id"])
def test_entrypoint_environment_identity_on_a_pre_rename_database(tmp_path, old_db, extra):
    db = copy_db(old_db, tmp_path / "hub.sqlite")
    environment_identity(tmp_path, db, **extra)
    assert "value" in columns(db, "registry_metadata")


def test_entrypoint_environment_identity_opens_a_renamed_database_as_before(tmp_path):
    """Only a pre-rename database is migrated: any other is opened as it was, which hubdb.connect would not (it
    switches the file to WAL and leaves a -wal behind)."""
    db = tmp_path / "hub.sqlite"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.commit()
    first = environment_identity(tmp_path, db)
    assert environment_identity(tmp_path / "again", db) == first  # the id is kept
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("hub")) == ["hub.sqlite"]


def test_environment_identity_preserves_the_wal_and_skips_an_unchanged_id(tmp_path):
    from backend.hubdb import leave_wal_for_litestream

    db = tmp_path / "hub.sqlite"
    with closing(leave_wal_for_litestream(sqlite3.connect(db))) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.commit()
    first = environment_identity(tmp_path, db)
    wal = tmp_path / "hub.sqlite-wal"
    content, modified = wal.read_bytes(), wal.stat().st_mtime_ns
    assert environment_identity(tmp_path, db) == first
    assert wal.read_bytes() == content and wal.stat().st_mtime_ns == modified
    assert environment_identity(tmp_path, db, TICO_ENVIRONMENT_ID="acme-restored") == "acme-restored"
    assert environment_identity(tmp_path, db) == "acme-restored"
