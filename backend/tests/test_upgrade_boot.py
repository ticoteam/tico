"""An upgraded database boots: code that runs before the server's lifespan (create_app's installs, docker/entrypoint.sh's
environment_identity) must not read a renamed column before the cleanup migrations have run.

The old database is a fresh one with every rename reversed and the rename migrations marked not applied."""
import os
import re
import sqlite3
import shutil
import subprocess
import sys
from contextlib import closing

import pytest

from backend.app import create_app
from backend.config import ROOT, Settings
from backend.migrations import renames as schema_renames
from backend.migrations.renames_data import COLUMNS, TABLES
from backend.store import Store


def pre_rename_db(path):
    Store(Settings(db_path=path)).initialize()
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        assert not schema_renames.pending(conn)
        conn.execute("PRAGMA legacy_alter_table=OFF")
        have = {t: {r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')}
                for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table, old, new, _wave in reversed(COLUMNS):
            if new in have.get(table, ()):
                conn.execute(f'ALTER TABLE "{table}" RENAME COLUMN "{new}" TO "{old}"')
        for old, new in reversed(TABLES):
            if new in have:
                conn.execute(f'ALTER TABLE "{new}" RENAME TO "{old}"')
        # The rename waves (0013, 0014, 0031, 0034) are pending again, and so is every file after the first of them:
        # a migration never runs before one this database has already applied.
        conn.execute("DELETE FROM schema_migrations WHERE version >= '0013' AND version NOT LIKE 'legacy_%'")
        assert schema_renames.pending(conn)
        assert "refs_json" in {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        assert "value_json" in {r[1] for r in conn.execute("PRAGMA table_info(registry_metadata)")}


def columns(path, table):
    with closing(sqlite3.connect(path)) as conn:
        return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def copy_db(source, target):
    with closing(sqlite3.connect(source)) as s, closing(sqlite3.connect(target)) as t:
        s.backup(t)
    return target


# Reversing every rename is ~1000 ALTERs (SQLite rewrites the whole schema for each), so it is done once per run.
@pytest.fixture(scope="module")
def old_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("old") / "hub.sqlite"
    pre_rename_db(path)
    return path


def test_create_app_on_a_pre_rename_database(tmp_path, old_db):
    db = copy_db(old_db, tmp_path / "hub.sqlite")
    create_app(Settings(db_path=db))        # the installs open store transactions before the lifespan's initialize
    assert "refs" in columns(db, "messages")
    assert "value" in columns(db, "registry_metadata")


def environment_identity(tmp_path, db, **extra):
    script = (ROOT / "docker/entrypoint.sh").read_text()
    function = re.search(r"^environment_identity\(\) \{\n.*?^\}\n", script, re.S | re.M).group(0)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')      # a symlink would leave the venv
    (bin_dir / "python").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "TICO_DB": str(db), "PYTHONPATH": str(ROOT), **extra}
    for name in ("TICO_ENVIRONMENT_ID", "TICO_TEAM_NAME", "TICO_COMPANY_NAME"):
        if name not in extra:
            env.pop(name, None)
    result = subprocess.run([shutil.which("bash"), "-c", 'set -e\n' + function + 'environment_identity\necho "$TICO_ENVIRONMENT_ID"'],
                            env=env, capture_output=True, text=True, cwd=ROOT, timeout=120)
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
    assert environment_identity(tmp_path / "again", db) == first        # the id is kept
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("hub")) == ["hub.sqlite"]
