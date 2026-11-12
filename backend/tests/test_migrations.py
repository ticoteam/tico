"""The migration authority (backend/migrations): a fresh database converges, booting again changes nothing, a failed
migration rolls back its DDL and its row and runs again once fixed, an applied file never changes, and a database this
code must not open is refused before anything is written to it."""

import gzip
import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from backend import bot_settings as BS
from backend import hubdb as H
from backend import invites
from backend import migrations
from backend.tests.helpers.migrations import FIXTURES, boot, fresh, restore_config, snapshot

STORE_SOURCE = Path(__file__).resolve().parents[1] / "store.py"
GOOD = {"sql": "CREATE TABLE half(x);\n", "py": "def up(conn):\n    conn.execute('CREATE TABLE half(x)')\n"}
BROKEN = {
    "sql": "CREATE TABLE half(x);\nSELECT * FROM missing;\n",
    "py": "def up(conn):\n    conn.execute('CREATE TABLE half(x)')\n    raise RuntimeError('injected')\n",
}


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _versions(conn):
    return [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


def test_fresh_database_converges_and_has_no_broken_references(tmp_path):
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == len(H.MIGRATIONS)
        # A new file has no cloud history when it first gets schema_migrations: the cloud steps run after the files.
        assert {row[0] for row in c.execute("SELECT version FROM schema_migrations")} == {
            f"legacy_user_version_{len(H.MIGRATIONS)}",
            *(m.version for m in migrations.files()),
        }
        assert "audit_log" in _tables(c)
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_booting_again_changes_nothing(tmp_path):
    path = fresh(tmp_path)
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first


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


def test_an_existing_cloud_machine_gains_the_pending_start_marker_without_losing_its_wake(tmp_path):
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("ALTER TABLE cloud_machines DROP COLUMN start_asked_at")
        c.execute("DELETE FROM schema_migrations WHERE version='20261105004523_cloud_machine_start_asked'")
        c.execute("INSERT INTO cloud_machines(bot,state,wake_key,woke_at) VALUES('ops','stopped','job:a',1000)")
    boot(path)
    with sqlite3.connect(path) as c:
        assert c.execute(
            "SELECT state,wake_key,woke_at,start_asked_at FROM cloud_machines WHERE bot='ops'"
        ).fetchone() == ("stopped", "job:a", 1000, None)
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first


def test_pending_machine_deletions_upgrade_without_losing_their_claim_fence(tmp_path):
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("DROP TABLE cloud_machine_deletions")
        c.execute("DELETE FROM schema_migrations WHERE version='20261105120026_cloud_machine_deletions'")
        c.execute("DELETE FROM schema_migrations WHERE version='20261105134042_cloud_machine_deletion_age'")
        c.execute(
            "INSERT INTO cloud_machines(bot,machine_id,runner_id,problem,mode,placement) "
            "VALUES('ops','m1','r1','delete_unknown','computer','shared')"
        )
    boot(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT machine_id,bot,runner_id,failures FROM cloud_machine_deletions").fetchall() == [
            ('m1', 'ops', 'r1', 0)
        ]
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first


def test_cloud_machine_codes_upgrade_without_losing_existing_enrollment(tmp_path):
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("ALTER TABLE cloud_machine_codes DROP COLUMN replacement_for")
        c.execute("DELETE FROM schema_migrations WHERE version='20261105122651_cloud_machine_replacement_codes'")
        c.execute("INSERT INTO cloud_machine_codes VALUES('existing-code','ops','now')")
    boot(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT code_hash,bot,replacement_for FROM cloud_machine_codes").fetchall() == [
            ('existing-code', 'ops', None)
        ]
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first


def test_failed_machine_ids_upgrade_without_losing_or_double_counting_attempts(tmp_path):
    version = "20261105131220_cloud_machine_enrollment_failures"
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("DROP TABLE cloud_machine_enrollment_failures")
        c.execute("ALTER TABLE cloud_machines DROP COLUMN create_asked_at")
        c.execute("DELETE FROM schema_migrations WHERE version=?", (version,))
        c.executemany(
            "INSERT INTO cloud_machines(bot,machine_id,state,enroll_fails) VALUES(?,?,?,?)",
            [
                ("counted", "m1", "deleted", 2),
                ("failed", "m2", "failed", 1),
                ("pending", "m3", "starting", 0),
                ("adopted", "m5", "starting", 0),
                ("kept", "m7", "starting", 2),
            ],
        )
        c.executemany(
            "INSERT INTO cloud_machine_deletions(machine_id,bot,reason) VALUES(?,?,?)",
            [
                ("m1", "counted", "never_enrolled"),
                ("m3", "pending", "never_enrolled"),
                ("m4", "adopted", "never_enrolled"),
                ("manual", "adopted", "manual"),
                ("m6", "kept", "never_enrolled"),
            ],
        )
        for bot, mid in (("counted", "m1"), ("kept", "m6")):
            H.event(c, H.KEEPER, "cloud_machine.deleted", bot, {"machine": mid, "reason": "never_enrolled", "tries": 2})
    boot(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT bot,enroll_fails,create_asked_at FROM cloud_machines ORDER BY bot").fetchall() == [
            ("adopted", 1, None),
            ("counted", 2, None),
            ("failed", 1, None),
            ("kept", 2, None),
            ("pending", 1, None),
        ]
        assert c.execute("SELECT machine_id FROM cloud_machine_enrollment_failures ORDER BY machine_id").fetchall() == [
            ("m1",),
            ("m2",),
            ("m3",),
            ("m4",),
            ("m6",),
        ]
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT enroll_fails FROM cloud_machines WHERE bot='adopted'").fetchone()[0] == 1


def test_enrollment_failure_migration_stands_alone_without_other_machine_migrations():
    up = _migration("20261105131220_cloud_machine_enrollment_failures").up
    with sqlite3.connect(":memory:") as c:
        up(c)
        assert "cloud_machine_enrollment_failures" in _tables(c)
        c.execute("CREATE TABLE cloud_machines(bot TEXT PRIMARY KEY,machine_id TEXT,state TEXT,enroll_fails INTEGER)")
        c.execute("INSERT INTO cloud_machines VALUES('ops','m1','failed',1)")
        up(c)
        assert c.execute("SELECT * FROM cloud_machine_enrollment_failures").fetchall() == [("m1", "ops")]
        assert c.execute("SELECT enroll_fails,create_asked_at FROM cloud_machines").fetchone() == (1, None)


def test_deletion_fence_age_upgrade_keeps_old_ids_and_retry_clocks(tmp_path):
    path = fresh(tmp_path)
    receipt = H.shift(H.now(), hours=-25)
    with sqlite3.connect(path) as c:
        c.execute("ALTER TABLE cloud_machine_deletions DROP COLUMN created_at")
        c.execute("DELETE FROM schema_migrations WHERE version='20261105134042_cloud_machine_deletion_age'")
        c.executemany(
            "INSERT INTO cloud_machine_deletions(machine_id,bot,runner_id,next_try_at,failures) VALUES(?,?,?,?,?)",
            [("old", "ops", "r1", 1000, 3), ("unknown", "_shared", "r2", 2000, 2)],
        )
        H.event(c, H.KEEPER, "cloud_machine.deleted", "ops", {"machine": "old"})
        c.execute("UPDATE events SET created_at=? WHERE action='cloud_machine.deleted'", (receipt,))
    boot(path)
    with sqlite3.connect(path) as c:
        rows = c.execute(
            "SELECT machine_id,bot,runner_id,next_try_at,failures,created_at "
            "FROM cloud_machine_deletions ORDER BY machine_id"
        ).fetchall()
        assert [r[:5] for r in rows] == [("old", "ops", "r1", 1000, 3), ("unknown", "_shared", "r2", 2000, 2)]
        assert rows[0][5] == c.execute("SELECT CAST(strftime('%s',?) AS REAL)", (receipt,)).fetchone()[0]
        assert rows[1][5] > rows[0][5] + 24 * 60 * 60
    boot(path)
    with sqlite3.connect(path) as c:
        assert (
            c.execute(
                "SELECT machine_id,bot,runner_id,next_try_at,failures,created_at "
                "FROM cloud_machine_deletions ORDER BY machine_id"
            ).fetchall()
            == rows
        )


def test_deletion_fence_age_migration_stands_alone_and_does_not_reset_an_age():
    up = _migration("20261105134042_cloud_machine_deletion_age").up
    with sqlite3.connect(":memory:") as c:
        up(c)
        c.execute("INSERT INTO cloud_machine_deletions(machine_id,bot) VALUES('old','ops')")
        up(c)
        age = c.execute("SELECT created_at FROM cloud_machine_deletions").fetchone()[0]
        assert age > 0
        up(c)
        assert c.execute("SELECT created_at FROM cloud_machine_deletions").fetchone()[0] == age


def test_personal_alert_migration_preserves_consent_caps_and_dispatches_when_adopted(tmp_path):
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('alert-person','Alert recipient','alerts@acme.example')")
        c.execute(
            "INSERT INTO alert_preferences(person_id,phone,phone_version,verified_at,call_consent,monthly_cap,updated_at) "
            "VALUES('alert-person','+14155550123','consent-1','now',1,3,'now')"
        )
        c.execute("UPDATE alert_company_settings SET monthly_cap=7 WHERE id=1")
        c.execute(
            "INSERT INTO personal_alerts(id,person_id,incident_key,kind,subject_id,phone_version,state,reserved,created_at) "
            "VALUES('paid','alert-person','incident-1','test','','consent-1','accepted',1,'now')"
        )
        before = {
            table: c.execute('SELECT * FROM ' + table).fetchall()
            for table in ('alert_preferences', 'alert_company_settings', 'personal_alerts')
        }
        c.execute("DELETE FROM schema_migrations WHERE version='20261105040931_personal_alerts'")
    boot(path)
    with sqlite3.connect(path) as c:
        assert {table: c.execute('SELECT * FROM ' + table).fetchall() for table in before} == before


def test_the_migrations_run_before_the_module_schema_code(tmp_path, monkeypatch):
    """A new file: a migration sees none of the tables the module code creates, so it must tolerate their absence."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    for migration in migrations.files():
        shutil.copy(migration.path, directory / migration.path.name)
    (directory / "0999_probe.py").write_text(
        "def up(conn):\n"
        "    have = conn.execute(\"SELECT count(*) FROM sqlite_master WHERE name IN ('google_connects','cloud_migrations')\")\n"
        "    conn.execute('CREATE TABLE probe(module_tables INTEGER)')\n"
        "    conn.execute('INSERT INTO probe VALUES(?)', (have.fetchone()[0],))\n"
    )
    monkeypatch.setattr(migrations, "DIRECTORY", directory)
    path = tmp_path / "hub.sqlite"
    boot(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT module_tables FROM probe").fetchone()[0] == 0
        assert "google_connects" in _tables(c)


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
    (directory / "0001_family.sql").write_text(FAMILY)
    (directory / "0002_rebuild.py").write_text(second)
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
        assert _versions(conn) == ["0001_family", "0002_rebuild"]
    finally:
        conn.close()


def test_a_foreign_keys_off_migration_that_breaks_a_reference_rolls_back(tmp_path):
    conn, directory = _family(tmp_path, REBUILD.replace("SELECT * FROM parent_old", "SELECT * FROM parent_old WHERE 0"))
    try:
        with pytest.raises(migrations.MigrationError, match="0002_rebuild left rows referring to missing rows"):
            migrations.apply(conn, directory)
        assert conn.execute("SELECT id FROM parent").fetchall() == [("p1",)]
        assert "parent_old" not in _tables(conn)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert _versions(conn) == ["0001_family"]
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
        assert _versions(conn) == ["0001_family", "0002_rebuild"]
    finally:
        conn.close()


@pytest.mark.parametrize("kind", ["sql", "py"])
def test_failed_migration_rolls_back_its_ddl_and_row_then_runs_once_fixed(tmp_path, kind):
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_base.sql").write_text("CREATE TABLE base(x);\n")
    later = directory / f"20261104235959_half.{kind}"
    later.write_text(BROKEN[kind])
    conn = sqlite3.connect(tmp_path / "hub.sqlite", isolation_level=None)
    try:
        with pytest.raises((sqlite3.Error, RuntimeError)):
            migrations.apply(conn, directory)
        assert _tables(conn) == {"schema_migrations", "base"}
        assert _versions(conn) == ["0001_base"]
        later.write_text(GOOD[kind])
        migrations.apply(conn, directory)
        assert "half" in _tables(conn)
        assert _versions(conn) == ["0001_base", "20261104235959_half"]
    finally:
        conn.close()


@pytest.mark.parametrize("kind", ["sql", "py"])
def test_a_retired_migration_is_recorded_and_never_imported_or_run(tmp_path, monkeypatch, kind):
    """A file in SUPERSEDED may import a module that is gone: it is recorded with its checksum and not loaded."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_base.sql").write_text("CREATE TABLE base(x);\n")
    body = {
        "sql": "CREATE TABLE never(x);\n",
        "py": "from backend import a_module_that_is_gone\n\ndef up(conn):\n    pass\n",
    }
    retired = directory / f"20261104235959_retired.{kind}"
    retired.write_text(body[kind])
    monkeypatch.setattr(migrations, "SUPERSEDED", {"20261104235959_retired"})
    conn = sqlite3.connect(tmp_path / "hub.sqlite", isolation_level=None)
    try:
        migrations.apply(conn, directory)
        assert _tables(conn) == {"schema_migrations", "base"}
        assert _versions(conn) == ["0001_base", "20261104235959_retired"]
        recorded = conn.execute("SELECT checksum FROM schema_migrations WHERE version='20261104235959_retired'")
        assert recorded.fetchone()[0] == hashlib.sha256(retired.read_bytes()).hexdigest()
    finally:
        conn.close()


@pytest.mark.parametrize("name", ["0001_audit_log.sql", "20261104235959_probe.sql"])
def test_a_changed_applied_file_refuses_to_boot(tmp_path, monkeypatch, name):
    directory = tmp_path / "migrations"
    directory.mkdir()
    for migration in migrations.files():
        shutil.copy(migration.path, directory / migration.path.name)
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


def test_a_retired_file_emptied_after_it_ran_still_boots(tmp_path, monkeypatch):
    """A file in SUPERSEDED never runs again, so stubbing it out (a deleted import) does not refuse the database."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    for migration in migrations.files():
        shutil.copy(migration.path, directory / migration.path.name)
    retired = directory / "20261107140000_blocked_reason_recompute.py"
    monkeypatch.setattr(migrations, "DIRECTORY", directory)
    path = tmp_path / "hub.sqlite"
    boot(path)
    retired.write_text(retired.read_text() + "\n# emptied after it ran\n")
    boot(path)


def test_pending_files_run_in_mixed_version_order_even_below_an_applied_version(tmp_path):
    conn = sqlite3.connect(tmp_path / "hub.sqlite", isolation_level=None)
    conn.execute("CREATE TABLE ran(version TEXT)")
    (tmp_path / "0002_base.sql").write_text("INSERT INTO ran VALUES('0002_base');\n")
    (tmp_path / "20261105000000_newer.py").write_text(
        "def up(conn):\n    conn.execute(\"INSERT INTO ran VALUES('20261105000000_newer')\")\n"
    )
    try:
        migrations.apply(conn, tmp_path)
        recorded = conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
        # Another PR lands after the newer migration has run, with both legacy and timestamp pending files.
        (tmp_path / "0001_earlier.sql").write_text("INSERT INTO ran VALUES('0001_earlier');\n")
        (tmp_path / "20261104235959_pending.py").write_text(
            "def up(conn):\n    conn.execute(\"INSERT INTO ran VALUES('20261104235959_pending')\")\n"
        )
        (tmp_path / "20261106000000_last.sql").write_text("INSERT INTO ran VALUES('20261106000000_last');\n")
        expected = [
            "0001_earlier",
            "0002_base",
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
                "0002_base",
                "20261105000000_newer",
                "0001_earlier",
                "20261104235959_pending",
                "20261106000000_last",
            )
        ]
        assert (
            conn.execute(
                "SELECT * FROM schema_migrations WHERE version IN (?,?) ORDER BY version",
                ("0002_base", "20261105000000_newer"),
            ).fetchall()
            == recorded
        )
        before = conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
        migrations.apply(conn, tmp_path)
        assert conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall() == before
        assert conn.execute("SELECT count(*) FROM ran").fetchone()[0] == 5
    finally:
        conn.close()


@pytest.mark.parametrize("version", ["0001", "20261104235959"])
def test_duplicate_versions_across_sql_and_python_are_refused_before_either_runs(tmp_path, version):
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


def test_every_shipped_migration_file_keeps_its_checksum():
    """The fixture lists each migration that shipped (v0.5.109; 0070 on as of v0.5.178) with the SHA-256 of its file then. A database that applied
    one records that checksum, and a start refuses a file whose checksum changed (`_verify`): a shipped file is never
    edited, a fix is a new migration. A shipped file that is gone fails too."""
    shipped = [line.split() for line in (FIXTURES / "shipped_migration_checksums.txt").read_text().splitlines()]
    assert shipped and all(len(row) == 2 for row in shipped)
    changed = []
    for name, digest in shipped:
        path = migrations.DIRECTORY / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            changed.append(name)
    assert not changed, "Shipped migrations edited or removed (write a new migration instead): " + ", ".join(changed)


@pytest.mark.parametrize(
    "version",
    [
        "0099_from_the_future",
        "20261104235959_from_the_future",
        f"legacy_user_version_{migrations.NEWEST_USER_VERSION + 1}",
        f"legacy_cloud_{migrations.CLOUD_VERSION + 1}",
    ],
)
def test_a_database_with_a_migration_this_code_does_not_know_is_refused_untouched(tmp_path, version):
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute(
            "INSERT INTO schema_migrations VALUES(?,?,?)", (version, migrations.LEGACY, "2026-11-02T00:00:00.000000Z")
        )
    before = snapshot(path)
    with pytest.raises(migrations.MigrationError, match=re.escape(version)):
        boot(path)
    assert snapshot(path) == before


def test_a_database_below_the_bridge_is_refused_before_anything_is_written(tmp_path):
    path = tmp_path / "hub.sqlite"
    with gzip.open(FIXTURES / "hub-v0.2.1.sqlite.gz") as source, open(path, "wb") as target:
        shutil.copyfileobj(source, target)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(migrations.MigrationError, match="then upgrade to this release"):
        boot(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def _migration(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / "migrations" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_private_tasks_guest_becomes_its_first_reader_and_the_guest_column_goes():
    """20261105213649_task_readers: each guest is copied into `readers` (tasks and the trash), the indexes and
    triggers that named `guest` go, the covering indexes come back with `readers`, and running again changes nothing."""
    up = _migration("20261105213649_task_readers").up
    with sqlite3.connect(":memory:") as c:
        c.execute(
            "CREATE TABLE tasks(id TEXT PRIMARY KEY, requester_id TEXT, owner_id TEXT, private INTEGER, "
            "status TEXT, waiting_on TEXT, rank REAL, created_at TEXT, guest TEXT)"
        )
        c.execute(
            "CREATE INDEX tasks_private_status ON tasks(id, requester_id, owner_id, guest, private, status, "
            "waiting_on) WHERE private IS NOT 0"
        )
        c.execute("CREATE TRIGGER tasks_guest_seen AFTER UPDATE OF guest ON tasks BEGIN SELECT 1; END")
        c.execute("CREATE TABLE task_trash(task_id TEXT PRIMARY KEY, rows TEXT, guest TEXT)")
        c.executemany(
            "INSERT INTO tasks(id,requester_id,owner_id,private,status,guest) VALUES(?,?,?,1,'open',?)",
            [
                ("t1", "bot:cpo", "bot:ops", "human:ben"),
                ("t2", "bot:cpo", "bot:ops", None),
                ("t3", "human:ana", "bot:ops", ""),
            ],
        )
        c.execute("INSERT INTO task_trash VALUES('t9','[]','human:cara')")
        up(c)
        assert "guest" not in {row[1] for row in c.execute("PRAGMA table_info(tasks)")}
        assert "guest" not in {row[1] for row in c.execute("PRAGMA table_info(task_trash)")}
        moved = dict(c.execute("SELECT id, readers FROM tasks").fetchall())
        assert moved == {"t1": '["human:ben"]', "t2": None, "t3": None}
        assert c.execute("SELECT readers FROM task_trash").fetchone()[0] == '["human:cara"]'
        schema = {row[0]: row[1] for row in c.execute("SELECT name, sql FROM sqlite_master WHERE tbl_name='tasks'")}
        assert "tasks_guest_seen" not in schema and "readers" in schema["tasks_private_status"]
        assert {"tasks_queue_cover", "tasks_owner_status_cover", "tasks_requester_status_cover"} <= set(schema)
        before = c.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
        up(c)
        assert c.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall() == before
        assert dict(c.execute("SELECT id, readers FROM tasks").fetchall()) == moved


def test_a_session_link_keeps_its_thread_once_in_its_url():
    """Migration 0049 strips the thread_id copy from session rows whose url gives that thread back, and leaves every
    other row alone: a url that only contains the id, or names another thread, keeps its copy."""
    up = _migration("0049_task_session_thread_once").up
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE task_links(id TEXT PRIMARY KEY, kind TEXT, url TEXT, detail TEXT)")
    rows = {
        "bot": (
            "session",
            "session:ops/t-1",
            '{"primary": true, "harness": "codex", "bot": "ops", "thread_id": "t-1"}',
        ),
        "resume": (
            "session",
            "session:cd /tmp/ops && codex resume abc123",
            '{"primary": false, "harness": "codex", "thread_id": "abc123"}',
        ),
        "reference": ("session", "session:orca ref", '{"primary": false, "thread_id": "elsewhere"}'),
        "contains": ("session", "session:orca ref t-9", '{"primary": false, "harness": "orca", "thread_id": "t-9"}'),
        "moved": ("session", "session:ops/t-new", '{"primary": true, "bot": "ops", "thread_id": "t-old"}'),
        "pr": ("pr", "https://github.com/acme/app/pull/1", '{"thread_id": "t-1"}'),
        "broken": ("session", "session:ops/t-2", "not json"),
        "list": ("session", "session:ops/t-3", "[1]"),
    }
    conn.executemany("INSERT INTO task_links VALUES(?,?,?,?)", [(key, *row) for key, row in rows.items()])
    up(conn)
    stored = dict(conn.execute("SELECT id, detail FROM task_links").fetchall())
    assert {key: json.loads(stored[key]) for key in ("bot", "resume")} == {
        "bot": {"primary": True, "harness": "codex", "bot": "ops"},
        "resume": {"primary": False, "harness": "codex"},
    }
    assert {key: stored[key] for key in rows if key not in ("bot", "resume")} == {
        key: row[2] for key, row in rows.items() if key not in ("bot", "resume")
    }
    # What the API shows for the stripped rows is what it showed before.
    for key in ("bot", "resume"):
        shown = H._with_thread({"kind": "session", "url": rows[key][1], "detail": stored[key]})
        assert json.loads(shown["detail"]) == json.loads(rows[key][2])
    up(conn)
    assert dict(conn.execute("SELECT id, detail FROM task_links").fetchall()) == stored
    up(sqlite3.connect(":memory:"))  # a database with no task_links has nothing to strip


# The tables 0050_idempotency_one_table.py moves into `idempotency`, as migration 0049 and earlier left them.
LEGACY_TABLES = (
    "CREATE TABLE idempotency(actor TEXT NOT NULL, operation TEXT NOT NULL, key TEXT NOT NULL, "
    "request_hash TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(actor,operation,key))",
    "CREATE INDEX idempotency_by_created ON idempotency(created_at)",
    "CREATE TABLE invite_sends(actor TEXT NOT NULL, operation TEXT NOT NULL, key TEXT NOT NULL, "
    "request_hash TEXT NOT NULL, person TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK(state IN ('sending','sent','refused')), error_code TEXT, error_detail TEXT, "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(actor,operation,key))",
    "CREATE TABLE github_product_repo_operations(actor TEXT NOT NULL, operation TEXT NOT NULL, key TEXT NOT NULL, "
    "request_hash TEXT NOT NULL, target_org TEXT NOT NULL, target_name TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK(state IN ('pending','completed','rejected')), response TEXT, error_code TEXT, "
    "error_detail TEXT, error_status INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
    "PRIMARY KEY(actor,operation,key), CHECK(state!='completed' OR response IS NOT NULL), "
    "CHECK(state!='rejected' OR (error_code IS NOT NULL AND error_detail IS NOT NULL AND error_status IS NOT NULL)))",
    "CREATE TABLE github_review_operations(actor TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL, "
    "response TEXT, PRIMARY KEY(actor,key))",
)
PRODUCT, REVIEWS = "/api/v2/github/product-repos", "/api/v2/github/reviews"
ONBOARDING = "/api/v2/onboarding/company/people/{}/invite"
TS = "2026-10-01T00:00:00.000000Z"


def test_a_settled_message_whose_task_list_was_read_as_none_is_requeued_with_what_read_it(tmp_path):
    """Migration 0076 queues each message naming a list of tasks, and every row that read it, transitively, for another
    walk. A scalar message, a message with refs that are not JSON, and a row that read only scalars stay settled. The
    migration runs again without changing the queue, and a database without the read tables has nothing to requeue."""
    up = _migration("0076_requeue_list_task_refs").up
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE messages(id TEXT PRIMARY KEY, refs TEXT);
        CREATE TABLE read_index(key TEXT PRIMARY KEY) WITHOUT ROWID;
        CREATE TABLE read_deps(dep TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY(dep, key)) WITHOUT ROWID;
        CREATE TABLE read_dirty(key TEXT PRIMARY KEY) WITHOUT ROWID;
    """)
    refs = {
        "list": '{"task": ["t-known", "t-missing"]}',
        "task-id-list": '{"task_id": []}',
        "scalar": '{"task": "t-known"}',
        "broken": '{"task": [',
        "plain": None,
        "reply": '{"task": "t-known"}',
        "deeper": '{"task": "t-known"}',
        "other": '{"task": "t-known"}',
        # Python reads the last of a repeated key, and an escaped key name is the same key: SQLite reads neither.
        "repeated": '{"task": "t-known", "task": []}',
        "escaped": '{"tas\\u006b": []}',
        "repeated-scalar": '{"task": [], "task": "t-known"}',
    }
    conn.executemany("INSERT INTO messages VALUES(?,?)", list(refs.items()))
    settled = ["m:" + mid for mid in refs]
    conn.executemany("INSERT INTO read_index VALUES(?)", [(key,) for key in settled])
    # 'reply' read 'list' as a node, 'deeper' read 'reply', and 'other' read only the scalar message.
    conn.executemany(
        "INSERT INTO read_deps VALUES(?,?)", [("m:list", "m:reply"), ("m:reply", "m:deeper"), ("m:scalar", "m:other")]
    )
    up(conn)
    index = {row[0] for row in conn.execute("SELECT key FROM read_index")}
    dirty = {row[0] for row in conn.execute("SELECT key FROM read_dirty")}
    assert dirty == {"m:list", "m:task-id-list", "m:reply", "m:deeper", "m:repeated", "m:escaped"}
    assert index == {"m:scalar", "m:broken", "m:plain", "m:other", "m:repeated-scalar"}
    up(conn)
    assert {row[0] for row in conn.execute("SELECT key FROM read_dirty")} == dirty
    assert {row[0] for row in conn.execute("SELECT key FROM read_index")} == index
    bare = sqlite3.connect(":memory:")
    bare.execute("CREATE TABLE messages(id TEXT PRIMARY KEY, refs TEXT)")
    bare.execute("INSERT INTO messages VALUES('list', '{\"task\": []}')")
    up(bare)
    assert _tables(bare) == {"messages"}
