"""An upgraded database boots: code that runs before the server's lifespan (create_app's installs, backend.boot's
environment_identity) must not read a column before the pending migrations have run.

The old database is the oldest this code opens: the compatibility floor's own schema (fixtures/schema_floor.sql)."""

import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from backend.app import create_app
from backend import migrations
from backend.config import ROOT, Settings

FIXTURE = Path(__file__).parent / "fixtures" / "schema_floor.sql"


def floor_db(path):
    """The floor release's database, as its own schema dump: the boot under test is the real upgrade."""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(FIXTURE.read_text())


def copy_db(source, target):
    with closing(sqlite3.connect(source)) as s, closing(sqlite3.connect(target)) as t:
        s.backup(t)
    return target


@pytest.fixture(scope="module")
def old_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("old") / "hub.sqlite"
    floor_db(path)
    return path


def test_create_app_on_a_floor_database(tmp_path, old_db):
    db = copy_db(old_db, tmp_path / "hub.sqlite")
    create_app(Settings(db_path=db))  # the installs open store transactions before the lifespan's initialize
    with closing(sqlite3.connect(db)) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='cloud_migrations'").fetchone()


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


def test_entrypoint_environment_identity_on_a_floor_database(tmp_path, old_db):
    """A named install with no environment id yet: the case that cannot construct Settings before this step. Without a
    team name the step takes the same path (backend.boot.environment_identity and migrations.preflight never read
    TICO_TEAM_NAME or TICO_COMPANY_NAME), so one boot covers both."""
    db = copy_db(old_db, tmp_path / "hub.sqlite")
    first = environment_identity(tmp_path, db, TICO_TEAM_NAME="Acme")
    assert environment_identity(tmp_path / "again", db) == first  # the id is kept


def test_environment_identity_preserves_the_wal_and_skips_an_unchanged_id(tmp_path, old_db):
    from backend.hubdb import leave_wal_for_litestream

    db = tmp_path / "hub.sqlite"
    with closing(sqlite3.connect(old_db)) as source, closing(leave_wal_for_litestream(sqlite3.connect(db))) as conn:
        source.backup(conn)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO registry_metadata VALUES('wal-probe', '1')")
        conn.commit()
    first = environment_identity(tmp_path, db)
    wal = tmp_path / "hub.sqlite-wal"
    content, modified = wal.read_bytes(), wal.stat().st_mtime_ns
    assert environment_identity(tmp_path, db) == first
    assert wal.read_bytes() == content and wal.stat().st_mtime_ns == modified
    assert environment_identity(tmp_path, db, TICO_ENVIRONMENT_ID="acme-restored") == "acme-restored"
    assert environment_identity(tmp_path, db) == "acme-restored"


def test_create_app_on_a_file_that_does_not_exist_yet(tmp_path):
    """The installs open store transactions (some make a table on first use) before the lifespan's initialize: the new
    file takes the baseline first, so it is never a file with a few tables and no schema record."""
    db = tmp_path / "data" / "hub.sqlite"
    create_app(Settings(db_path=db))
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (max(migrations.retired()),)).fetchone()
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='github_app'").fetchone()


def test_a_store_connection_to_a_new_file_gives_it_the_baseline_first(tmp_path):
    """A write on a file no start has opened (code that makes a table on first use): the file is a Tico database from
    then on, and its first start goes on as on any new one."""
    from backend.store import Store

    db = tmp_path / "hub.sqlite"
    store = Store(Settings(db_path=db))
    with store.transaction() as c:
        c.execute("CREATE TABLE IF NOT EXISTS made_on_first_use(x)")
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
    store.initialize()
