"""The migration authority (backend/migrations): a fresh database converges, booting again changes nothing, a failed
migration rolls back its DDL and its row and runs again once fixed, an applied file never changes, and a database this
code must not open is refused before anything is written to it."""
import gzip
import hashlib
import importlib
import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from backend import hubdb as H
from backend import migrations
from backend.config import Settings
from backend.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
STORE_SOURCE = Path(__file__).resolve().parents[1] / "store.py"
GOOD = {"sql": "CREATE TABLE half(x);\n", "py": "def up(conn):\n    conn.execute('CREATE TABLE half(x)')\n"}
BROKEN = {"sql": "CREATE TABLE half(x);\nSELECT * FROM missing;\n",
          "py": "def up(conn):\n    conn.execute('CREATE TABLE half(x)')\n    raise RuntimeError('injected')\n"}


def boot(path, **kw):
    Store(Settings(db_path=path, registry_dir=FIXTURES / "registry")).initialize(seed_market=False, **kw)


def fresh(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    return path


def snapshot(path):
    with sqlite3.connect(path) as c:
        return (c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall(),
                c.execute("SELECT version,checksum,applied_at FROM schema_migrations ORDER BY version").fetchall())


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
            f"legacy_user_version_{len(H.MIGRATIONS)}", *(m.version for m in migrations.files())}
        assert "audit_log" in _tables(c)
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_booting_again_changes_nothing(tmp_path):
    path = fresh(tmp_path)
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first


def test_every_migration_adopts_a_database_that_already_has_its_change(tmp_path):
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO audit_log VALUES('a1','2026-01-01T00:00:00.000000Z','human:ana','task_cap:1',NULL,NULL,NULL)")
        c.execute("DELETE FROM schema_migrations WHERE version NOT LIKE 'legacy_%'")
    boot(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT id FROM audit_log").fetchall() == [("a1",)]
        versions = _versions(c)
        assert all(versions.count(m.version) == 1 for m in migrations.files())


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
        "    conn.execute('INSERT INTO probe VALUES(?)', (have.fetchone()[0],))\n")
    monkeypatch.setattr(migrations, "DIRECTORY", directory)
    path = tmp_path / "hub.sqlite"
    boot(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT module_tables FROM probe").fetchone()[0] == 0
        assert "google_connects" in _tables(c)


def test_a_rename_lands_before_startup_code_that_uses_the_new_name(tmp_path):
    """google_connect.ensure_schema builds an index on oauth_state at every start; on a file that still has `state`,
    migration 0003 must rename it first or the start fails."""
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("DROP INDEX google_connects_oauth_state")
        c.execute("ALTER TABLE google_connects RENAME COLUMN oauth_state TO state")
        c.execute("CREATE UNIQUE INDEX google_connects_state ON google_connects(state) WHERE state IS NOT NULL")
        c.execute("INSERT INTO google_connects(person_id,mailbox,status,state,created_at,updated_at) "
                  "VALUES('p1','ana@acme.example','signing_in','nonce-1','t','t')")
        c.execute("DELETE FROM schema_migrations WHERE version>='0003' AND version NOT LIKE 'legacy_%'")
    boot(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT oauth_state FROM google_connects").fetchall() == [("nonce-1",)]
        indexes = {row[1] for row in c.execute("PRAGMA index_list(google_connects)")}
        assert "google_connects_oauth_state" in indexes and "google_connects_state" not in indexes


def test_an_existing_database_loses_the_redundant_task_indexes_once(tmp_path):
    """Migration 0011 drops the three task indexes that no read uses; the covering ones it keeps stay, and booting again
    leaves the database as it is."""
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("CREATE INDEX tasks_owner_status ON tasks(owner_id, status)")
        c.execute("CREATE INDEX tasks_requester_status ON tasks(requester_id, status)")
        c.execute("CREATE INDEX tasks_private ON tasks(id) WHERE private IS NOT 0")
        # 0011 and every file after it run again (each is safe to repeat), as on a file that never had them
        c.execute("DELETE FROM schema_migrations WHERE version>='0011' AND version NOT LIKE 'legacy_%'")
    boot(path)
    with sqlite3.connect(path) as c:
        indexes = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert not {"tasks_owner_status", "tasks_requester_status", "tasks_private"} & indexes
        assert {"tasks_owner_status_cover", "tasks_requester_status_cover", "tasks_private_parties"} <= indexes
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first


FAMILY = ("CREATE TABLE parent(id TEXT PRIMARY KEY, name TEXT);\n"
          "CREATE TABLE child(id TEXT PRIMARY KEY, parent_id TEXT REFERENCES parent(id) ON DELETE CASCADE);\n"
          "INSERT INTO parent VALUES('p1','one');\nINSERT INTO child VALUES('c1','p1');\n")
REBUILD = ("FOREIGN_KEYS_OFF = True\n\n\ndef up(conn):\n"
           "    conn.execute('ALTER TABLE parent RENAME TO parent_old')\n"
           "    conn.execute(\"CREATE TABLE parent(id TEXT PRIMARY KEY, name TEXT CHECK (name <> ''))\")\n"
           "    conn.execute('INSERT INTO parent SELECT * FROM parent_old')\n"
           "    conn.execute('DROP TABLE parent_old')\n")


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
    later = directory / f"0002_half.{kind}"
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
        assert _versions(conn) == ["0001_base", "0002_half"]
    finally:
        conn.close()


def test_a_changed_applied_file_refuses_to_boot(tmp_path, monkeypatch):
    directory = tmp_path / "migrations"
    directory.mkdir()
    for migration in migrations.files():
        shutil.copy(migration.path, directory / migration.path.name)
    shipped = directory / "0001_audit_log.sql"
    monkeypatch.setattr(migrations, "DIRECTORY", directory)
    path = tmp_path / "hub.sqlite"
    boot(path)
    shipped.write_text(shipped.read_text() + "\n-- edited after it ran\n")
    with pytest.raises(migrations.MigrationError, match="0001_audit_log was applied"):
        boot(path)


@pytest.mark.parametrize("version", ["0099_from_the_future", f"legacy_user_version_{migrations.NEWEST_USER_VERSION + 1}"])
def test_a_database_with_a_migration_this_code_does_not_know_is_refused_untouched(tmp_path, version):
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (version, migrations.LEGACY, "2026-11-02T00:00:00.000000Z"))
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


def test_cloud_version_is_the_newest_cloud_step_store_writes():
    newest = max(int(n) for n in re.findall(r"INSERT INTO cloud_migrations VALUES\((\d+),", STORE_SOURCE.read_text()))
    assert migrations.CLOUD_VERSION == newest


def test_a_session_link_keeps_its_thread_once_in_its_url(tmp_path):
    """Migration 0090 strips the thread_id copy from session rows whose url names it, and leaves every other row alone."""
    up = importlib.import_module("backend.migrations.0090_task_session_thread_once").up
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE task_links(id TEXT PRIMARY KEY, kind TEXT, url TEXT, detail TEXT)")
    rows = {
        "bot": ("session", "session:ops/t-1", '{"primary": true, "harness": "codex", "bot": "ops", "thread_id": "t-1"}'),
        "resume": ("session", "session:cd /tmp/ops && codex resume abc123",
                   '{"primary": false, "harness": "codex", "thread_id": "abc123"}'),
        "reference": ("session", "session:orca ref", '{"primary": false, "thread_id": "elsewhere"}'),
        "pr": ("pr", "https://github.com/acme/app/pull/1", '{"thread_id": "t-1"}'),
        "broken": ("session", "session:ops/t-2", "not json"),
    }
    conn.executemany("INSERT INTO task_links VALUES(?,?,?,?)", [(key, *row) for key, row in rows.items()])
    up(conn)
    stored = dict(conn.execute("SELECT id, detail FROM task_links").fetchall())
    assert {key: json.loads(stored[key]) for key in ("bot", "resume")} == {
        "bot": {"primary": True, "harness": "codex", "bot": "ops"}, "resume": {"primary": False, "harness": "codex"}}
    assert stored["reference"] == rows["reference"][2] and stored["pr"] == rows["pr"][2] and stored["broken"] == "not json"
    up(conn)
    assert dict(conn.execute("SELECT id, detail FROM task_links").fetchall()) == stored
    up(sqlite3.connect(":memory:"))                 # a database with no task_links has nothing to strip


# The tables 0091_idempotency_one_table.py moves into `idempotency`, as migration 0090 and earlier left them.
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
TS = "2026-10-01T00:00:00.000000Z"


def test_a_populated_legacy_database_moves_each_row_and_keeps_its_behaviour(tmp_path):
    path = fresh(tmp_path)
    invite = "/api/v2/access/people/{}/invite"
    with sqlite3.connect(path) as c:
        c.execute("DROP TABLE idempotency")                    # the one table, with its triggers
        for ddl in LEGACY_TABLES:
            c.execute(ddl)
        c.executemany("INSERT INTO idempotency(actor,operation,key,request_hash,response,created_at) "
                      "VALUES('human:ana',?,?,?,?,?)", [
            ("/api/v2/tasks/t1/files", "ans-1", "h1", '{"ok": true}', TS),
            (PRODUCT, "receipt", "h2", '{"repository": "Acme/tico-receipt", "html_url": "x"}', TS),
            (invite.format("cara"), "collide", "h3", '{"invited": "cara"}', TS),   # shares its key with a send
        ])
        c.executemany("INSERT INTO invite_sends VALUES('human:ana',?,?,?,?,?,?,?,?,?)", [
            (invite.format("cara"), "collide", "h3", "cara", "sent", None, None, TS, TS),
            (invite.format("dan"), "sending", "h4", "dan", "sending", None, None, TS, TS),
            (invite.format("eve"), "refused", "h5", "eve", "refused", "too_many", "Too many invites", TS, TS),
        ])
        c.executemany("INSERT INTO github_product_repo_operations VALUES('human:ana',?,?,?,?,?,?,?,?,?,?,?,?)", [
            (PRODUCT, "prod-pending", "h6", "Acme", "tico-new", "pending", None, None, None, None, TS, TS),
            (PRODUCT, "prod-done", "h7", "Acme", "tico-done", "completed", '{"repository": "Acme/tico-done"}',
             None, None, None, TS, TS),
            (PRODUCT, "prod-rejected", "h8", "Acme", "tico-bad", "rejected", None, "github_repo", "Taken", 409, TS, TS),
        ])
        c.executemany("INSERT INTO github_review_operations VALUES('human:ana',?,?,?)", [
            ("rev-pending", "r1", None), ("rev-done", "r2", '{"id": 1}'),
        ])
        before = dict(c.execute("SELECT key, rowid FROM idempotency").fetchall())
        c.execute("DELETE FROM schema_migrations WHERE version>='0091' AND version NOT LIKE 'legacy_%'")
    boot(path)

    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        rows = {(r["operation"], r["key"]): dict(r) for r in c.execute("SELECT rowid, * FROM idempotency")}
        assert len(rows) == 10, sorted(rows)
        answer = rows[("/api/v2/tasks/t1/files", "ans-1")]
        assert (answer["binding"], answer["state"], answer["response"]) == (None, "completed", '{"ok": true}')
        assert answer["rowid"] == before["ans-1"], "a stored answer keeps its rowid"
        collide = rows[(invite.format("cara"), "collide")]
        assert (collide["binding"], collide["subject"], collide["state"], collide["response"]) == \
            ("invites", "cara", "completed", None), "the binding wins over the answer that shared its key"
        assert rows[(invite.format("dan"), "sending")]["state"] == "pending"
        eve = rows[(invite.format("eve"), "refused")]
        assert (eve["state"], eve["error_code"], eve["error_detail"]) == ("refused", "too_many", "Too many invites")
        receipt = rows[(PRODUCT, "receipt")]
        assert (receipt["binding"], receipt["subject"], receipt["state"]) == \
            ("github_product_repo", "Acme/tico-receipt", "completed")
        assert receipt["response"] == '{"repository": "Acme/tico-receipt", "html_url": "x"}'
        assert rows[(PRODUCT, "prod-pending")]["state"] == "pending"
        assert rows[(PRODUCT, "prod-pending")]["subject"] == "Acme/tico-new"
        assert rows[(PRODUCT, "prod-done")]["response"] == '{"repository": "Acme/tico-done"}'
        rejected = rows[(PRODUCT, "prod-rejected")]
        assert (rejected["state"], rejected["error_code"], rejected["error_status"]) == ("refused", "github_repo", 409)
        review = rows[(REVIEWS, "rev-pending")]
        assert (review["binding"], review["subject"], review["state"], review["response"]) == \
            ("github_review", "", "pending", None)
        assert rows[(REVIEWS, "rev-done")]["state"] == "completed"
        assert not {"invite_sends", "github_product_repo_operations", "github_review_operations"} & _tables(c)
        assert [r[1] for r in c.execute("PRAGMA table_info(idempotency)")] == [
            "actor", "operation", "key", "request_hash", "response", "created_at", "updated_at", "binding", "subject",
            "state", "error_code", "error_detail", "error_status"]
        assert "idempotency_answers_by_created" in {r[1] for r in c.execute("PRAGMA index_list(idempotency)")}
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first


def test_the_idempotency_table_the_migration_builds_is_the_one_store_declares():
    import re
    declared = re.search(r"CREATE TABLE IF NOT EXISTS (idempotency\(.*?'refused'\)\)\))", STORE_SOURCE.read_text(), re.S)
    built = importlib.import_module("backend.migrations.0091_idempotency_one_table").TABLE
    squash = lambda sql: re.sub(r"\s+", "", sql)
    assert declared and squash("CREATE TABLE " + declared.group(1)) == squash(built)
