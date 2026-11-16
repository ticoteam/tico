"""The migration authority (backend/migrations): a new database loads the baseline and converges, booting again changes
nothing, a failed migration rolls back its DDL and its row and runs again once fixed, an applied file never changes,
and a database this code must not open is refused before anything is written to it. test_schema_baseline.py compares a
new database with the oldest one this code upgrades."""

import json
import re
import shutil
import sqlite3

import pytest

from backend import hubdb as H
from backend import migrations
from backend.tests.helpers.migrations import boot, fresh, snapshot

GOOD = {"sql": "CREATE TABLE half(x);\n", "py": "def up(conn):\n    conn.execute('CREATE TABLE half(x)')\n"}
BROKEN = {
    "sql": "CREATE TABLE half(x);\nSELECT * FROM missing;\n",
    "py": "def up(conn):\n    conn.execute('CREATE TABLE half(x)')\n    raise RuntimeError('injected')\n",
}


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _versions(conn):
    return [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


def _copy_directory(directory):
    """This code's migrations, baseline and floor in a directory of their own, for a test that edits a file."""
    for migration in migrations.files():
        shutil.copy(migration.path, directory / migration.path.name)
    for name in (migrations.BASELINE, migrations.RETIRED_LIST):
        shutil.copy(migrations.DIRECTORY / name, directory / name)


def test_a_new_database_records_every_file_the_baseline_holds_and_has_no_broken_references(tmp_path):
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        recorded = set(_versions(c))
        assert {m.version for m in migrations.files()} <= recorded
        assert migrations.retired() <= recorded  # a new file is at the floor, like one that ran the retired files
        assert "audit_log" in _tables(c) and "cloud_migrations" not in _tables(c)
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_booting_again_changes_nothing(tmp_path):
    path = fresh(tmp_path)
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first


def test_a_migration_written_after_the_baseline_runs_on_a_new_file(tmp_path, monkeypatch):
    """The baseline records the files it holds; one it does not (a later PR's) runs on top of it."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    _copy_directory(directory)
    (directory / "20991231000000_later.sql").write_text("CREATE TABLE later(x);\n")
    monkeypatch.setattr(migrations, "DIRECTORY", directory)
    path = tmp_path / "hub.sqlite"
    boot(path)
    with sqlite3.connect(path) as c:
        assert "later" in _tables(c) and "20991231000000_later" in _versions(c)


def test_native_hub_participant_access_observes_meeting_deletion_without_cloud_boot(tmp_path):
    from backend import meeting_audience

    c = H.connect(tmp_path / 'native.sqlite')
    try:
        H.sync_registry(
            c, {}, {'people': [{'id': 'ana', 'email': 'ana@acme.example'}, {'id': 'ben', 'email': 'ben@acme.example'}]}
        )
        task = H.task_create(c, 'human:ana', 'Prepare the plan', '', 'human:ana', private=True, lint=False)
        c.execute(
            "INSERT INTO meetings(id,title,owner,metadata,created_at,updated_at) "
            "VALUES('source','Planning','ana@acme.example',?, 'now','now')",
            (json.dumps({'kind': 'meeting', 'private': True, 'participants': [{'email': 'ben@acme.example'}]}),),
        )
        c.execute(
            "INSERT INTO meeting_items(id,meeting_id,section,text,status,created_by,result_ref,created_at,updated_at) "
            "VALUES('sent','source','task','Prepare the plan','pushed','human:ana',?,'now','now')",
            (task['id'],),
        )
        meeting_audience.grant(c, task['id'], ['human:ben'], 'human:ana')
        assert H.task_private_readable(c, 'human:ben', task)
        c.execute("INSERT INTO media_control(meeting_id,deleted_at) VALUES('source','now')")
        assert not H.task_private_readable(c, 'human:ben', task)
        assert H.task_private_readable(c, 'human:ana', task)
    finally:
        c.close()


FAMILY = (
    "CREATE TABLE parent(id TEXT PRIMARY KEY, name TEXT);\n"
    "CREATE TABLE child(id TEXT PRIMARY KEY, parent_id TEXT REFERENCES parent(id) ON DELETE CASCADE);\n"
    "INSERT INTO parent VALUES('p1','one');\nINSERT INTO child VALUES('c1','p1');\n"
)
REBUILD = (
    "FOREIGN_KEYS_OFF = True\n\n\ndef up(conn):\n"
    "    conn.execute('ALTER TABLE parent RENAME TO parent_old')\n"
    "    conn.execute(\"CREATE TABLE parent(id TEXT PRIMARY KEY, name TEXT CHECK (name <> ''))\")\n"
    "    conn.execute('INSERT INTO parent SELECT * FROM parent_old')\n"
    "    conn.execute('DROP TABLE parent_old')\n"
)


def _family(tmp_path, second):
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "20261101000001_family.sql").write_text(FAMILY)
    (directory / "20261101000002_rebuild.py").write_text(second)
    conn = sqlite3.connect(tmp_path / "hub.sqlite", isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA legacy_alter_table=ON")
    return conn, directory


def test_a_foreign_keys_off_migration_rebuilds_a_parent_without_cascading_and_restores_the_setting(tmp_path):
    conn, directory = _family(tmp_path, REBUILD)
    try:
        migrations.apply(conn, directory)
        assert conn.execute("SELECT id FROM child").fetchall() == [("c1",)]
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert _versions(conn) == ["20261101000001_family", "20261101000002_rebuild"]
    finally:
        conn.close()


def test_a_foreign_keys_off_migration_that_breaks_a_reference_rolls_back(tmp_path):
    conn, directory = _family(tmp_path, REBUILD.replace("SELECT * FROM parent_old", "SELECT * FROM parent_old WHERE 0"))
    try:
        with pytest.raises(
            migrations.MigrationError, match="20261101000002_rebuild left rows referring to missing rows"
        ):
            migrations.apply(conn, directory)
        assert conn.execute("SELECT id FROM parent").fetchall() == [("p1",)]
        assert "parent_old" not in _tables(conn)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert _versions(conn) == ["20261101000001_family"]
    finally:
        conn.close()


def test_a_broken_reference_the_file_already_had_does_not_block_a_foreign_keys_off_migration(tmp_path):
    conn, directory = _family(tmp_path, REBUILD)
    conn.execute("CREATE TABLE stray(id TEXT PRIMARY KEY, parent_id TEXT REFERENCES parent(id))")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO stray VALUES('s1','gone')")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        migrations.apply(conn, directory)
        assert _versions(conn) == ["20261101000001_family", "20261101000002_rebuild"]
    finally:
        conn.close()


@pytest.mark.parametrize("kind", ["sql", "py"])
def test_failed_migration_rolls_back_its_ddl_and_row_then_runs_once_fixed(tmp_path, kind):
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "20261101000001_base.sql").write_text("CREATE TABLE base(x);\n")
    later = directory / f"20261104235959_half.{kind}"
    later.write_text(BROKEN[kind])
    conn = sqlite3.connect(tmp_path / "hub.sqlite", isolation_level=None)
    try:
        with pytest.raises((sqlite3.Error, RuntimeError)):
            migrations.apply(conn, directory)
        assert _tables(conn) == {"schema_migrations", "base"}
        assert _versions(conn) == ["20261101000001_base"]
        later.write_text(GOOD[kind])
        migrations.apply(conn, directory)
        assert "half" in _tables(conn)
        assert _versions(conn) == ["20261101000001_base", "20261104235959_half"]
    finally:
        conn.close()


@pytest.mark.parametrize("name", ["20261116182727_drop_retired_tables.sql", "20261199235959_probe.sql"])
def test_a_changed_applied_file_refuses_to_boot(tmp_path, monkeypatch, name):
    """A file the baseline recorded and one a database ran after it: neither may change once applied."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    _copy_directory(directory)
    shipped = directory / name
    if not shipped.exists():
        shipped.write_text("CREATE TABLE probe(x);\n")
    monkeypatch.setattr(migrations, "DIRECTORY", directory)
    path = tmp_path / "hub.sqlite"
    boot(path)
    shipped.write_text(shipped.read_text() + "\n-- edited after it ran\n")
    before = snapshot(path)
    with pytest.raises(migrations.MigrationError, match=f"{shipped.stem} was applied"):
        boot(path)
    assert snapshot(path) == before


def test_pending_files_run_in_mixed_version_order_even_below_an_applied_version(tmp_path):
    conn = sqlite3.connect(tmp_path / "hub.sqlite", isolation_level=None)
    conn.execute("CREATE TABLE ran(version TEXT)")
    (tmp_path / "20261101000002_base.sql").write_text("INSERT INTO ran VALUES('20261101000002_base');\n")
    (tmp_path / "20261105000000_newer.py").write_text(
        "def up(conn):\n    conn.execute(\"INSERT INTO ran VALUES('20261105000000_newer')\")\n"
    )
    try:
        migrations.apply(conn, tmp_path)
        recorded = conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
        # Another PR lands after the newer migration has run, with pending files on both sides of it.
        (tmp_path / "20261101000001_earlier.sql").write_text("INSERT INTO ran VALUES('20261101000001_earlier');\n")
        (tmp_path / "20261104235959_pending.py").write_text(
            "def up(conn):\n    conn.execute(\"INSERT INTO ran VALUES('20261104235959_pending')\")\n"
        )
        (tmp_path / "20261106000000_last.sql").write_text("INSERT INTO ran VALUES('20261106000000_last');\n")
        expected = [
            "20261101000001_earlier",
            "20261101000002_base",
            "20261104235959_pending",
            "20261105000000_newer",
            "20261106000000_last",
        ]
        assert [m.version for m in migrations.files(tmp_path)] == expected
        migrations.apply(conn, tmp_path)
        assert _versions(conn) == expected
        assert conn.execute("SELECT version FROM ran ORDER BY rowid").fetchall() == [
            (v,)
            for v in (
                "20261101000002_base",
                "20261105000000_newer",
                "20261101000001_earlier",
                "20261104235959_pending",
                "20261106000000_last",
            )
        ]
        assert (
            conn.execute(
                "SELECT * FROM schema_migrations WHERE version IN (?,?) ORDER BY version",
                ("20261101000002_base", "20261105000000_newer"),
            ).fetchall()
            == recorded
        )
        before = conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
        migrations.apply(conn, tmp_path)
        assert conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall() == before
        assert conn.execute("SELECT count(*) FROM ran").fetchone()[0] == 5
    finally:
        conn.close()


def test_duplicate_versions_across_sql_and_python_are_refused_before_either_runs(tmp_path):
    version = "20261104235959"
    (tmp_path / f"{version}_a.sql").write_text("CREATE TABLE should_not_exist(x);\n")
    (tmp_path / f"{version}_b.py").write_text("def up(conn): pass\n")
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        with pytest.raises(migrations.MigrationError, match="Two migration files share a number"):
            migrations.apply(conn, tmp_path)
        assert "should_not_exist" not in _tables(conn)
        assert _versions(conn) == []
    finally:
        conn.close()


@pytest.mark.parametrize(
    "version",
    ["20261104235959_from_the_future", "20991231000000_from_a_newer_tico"],
)
def test_a_database_with_a_migration_this_code_does_not_know_is_refused_untouched(tmp_path, version):
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (version, "0" * 64, "2026-11-02T00:00:00.000000Z"))
    before = snapshot(path)
    with pytest.raises(migrations.MigrationError, match=re.escape(version)):
        boot(path)
    assert snapshot(path) == before
