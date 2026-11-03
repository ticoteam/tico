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

from backend import hubdb as H
from backend import invites
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
        assert {"tasks_owner_status_cover", "tasks_requester_status_cover", "tasks_private_status"} <= indexes
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first


def test_an_existing_database_loses_the_two_duplicate_indexes_once(tmp_path):
    """Migration 0042 drops invariants_lease (a prefix of attempts_live_lease) and invariants_open_approvals (a copy of
    approvals_open_created); the indexes that serve the same reads stay, and booting again changes nothing."""
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("CREATE INDEX invariants_lease ON attempts(lease_until) WHERE state IN ('leased','running')")
        c.execute("CREATE INDEX invariants_open_approvals ON approvals(created_at) WHERE decision IS NULL")
        c.execute("DELETE FROM schema_migrations WHERE version>='0042' AND version NOT LIKE 'legacy_%'")
    boot(path)
    with sqlite3.connect(path) as c:
        indexes = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert not {"invariants_lease", "invariants_open_approvals"} & indexes
        assert {"attempts_live_lease", "approvals_open_created"} <= indexes
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first


def test_an_existing_database_loses_the_private_parties_index_and_the_parties_read_stays_covered(tmp_path):
    """Migration 0042 drops tasks_private_parties, a prefix of tasks_private_status under the same WHERE. The private
    tasks' parties read (row_clock.PARTIES) names tasks_private_status and reads the index alone; booting again changes
    nothing."""
    from backend import row_clock
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("CREATE INDEX tasks_private_parties ON tasks(id, requester_id, owner_id, guest, private) "
                  "WHERE private IS NOT 0")
        c.execute("DELETE FROM schema_migrations WHERE version>='0042' AND version NOT LIKE 'legacy_%'")
    boot(path)
    with sqlite3.connect(path) as c:
        indexes = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        plan = " ".join(row[3] for row in c.execute("EXPLAIN QUERY PLAN " + row_clock.PARTIES))
    assert "tasks_private_parties" not in indexes and "tasks_private_status" in indexes
    assert "COVERING INDEX tasks_private_status" in plan, plan
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


def _back_to_before(path, version):
    """The file as it was before `version` ran: the migration rows from it on are forgotten, so the next boot runs them."""
    with sqlite3.connect(path) as c:
        c.execute("DELETE FROM schema_migrations WHERE version>=? AND version NOT LIKE 'legacy_%'", (version,))


def _shape(path):
    """Each table's columns (an added column sits last, so not the CREATE text) and every other object's SQL."""
    with sqlite3.connect(path) as c:
        tables = {name: sorted(tuple(row)[1:] for row in c.execute(f'PRAGMA table_info("{name}")'))
                  for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        return tables, sorted(c.execute("SELECT type,name,sql FROM sqlite_master WHERE type<>'table'").fetchall())


def test_an_older_threads_shape_gets_the_missing_columns_tables_triggers_and_search_index_once(tmp_path):
    """Migration 0047: the threads schema the modules used to make on every start. A file missing some of it (columns
    added later, a helper table, an older trigger, the search index) ends with the shape a new file has, the new search
    index holds the messages already there, and a second boot changes nothing."""
    path = fresh(tmp_path)
    new = _shape(path)
    with sqlite3.connect(path) as c:
        for table, column in (("calls", "finalized_at"), ("federation_states", "company"), ("inbound_inboxes", "audience"),
                              ("org_rules", "where_terms"), ("slack_posts", "recipient_actor"),
                              ("live_meetings", "thread_id")):
            c.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        c.execute("DROP TABLE thread_digests")
        c.execute("DROP TABLE teammate_accesses")
        c.execute("DROP TRIGGER threads_revisions_deleted")
        c.execute("CREATE TRIGGER threads_revisions_deleted AFTER UPDATE OF deleted_at ON messages BEGIN SELECT 1; END")
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'message_search_%'"
                                 ).fetchall():
            c.execute(f"DROP TRIGGER {name}")
        c.execute("DROP TABLE message_search")
        c.execute("DROP TABLE message_search_keys")
        c.execute("INSERT INTO conversations(id,kind,created_at) VALUES('c-acme','direct','2026-01-01T00:00:00Z')")
        c.execute("INSERT INTO messages(id,conversation_id,sender_id,kind,body,created_at) "
                  "VALUES('m-acme','c-acme','human:ana','say','Acme quarterly memo','2026-01-01T00:00:00Z')")
    _back_to_before(path, "0047")
    boot(path)
    once = snapshot(path)
    assert _shape(path) == new
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT k.message_id FROM message_search JOIN message_search_keys k ON k.id=message_search.rowid "
                         "WHERE message_search MATCH 'quarterly'").fetchall() == [("m-acme",)]
    boot(path)
    assert snapshot(path) == once


def test_a_start_puts_back_the_federation_state_row_a_devcopy_emptied(tmp_path):
    """federation_states holds one row that Relay.status reads unguarded; migration 0047 makes it once, and every start
    puts it back when a devcopy emptied the table."""
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("DELETE FROM federation_states")
    boot(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT singleton FROM federation_states").fetchall() == [(1,)]

def test_an_existing_bots_runtime_model_and_host_move_into_its_config_then_the_columns_go(tmp_path):
    """Migration 0043: a runtime, model or host the config lacks is copied into it (the config keeps what it has), and
    the six legacy columns are dropped. cwd, thread_id and token_hash have nothing to move."""
    path = fresh(tmp_path)
    slug = "acme-bot"
    with sqlite3.connect(path) as c:
        for column in ("runtime", "model", "host", "cwd", "thread_id", "token_hash"):
            c.execute(f"ALTER TABLE bots ADD COLUMN {column} TEXT")
        c.execute("INSERT INTO bots(id,slug,display_name,effort,state,created_at) "
                  "VALUES(?,?,'Acme Bot','low','active','2026-01-01T00:00:00Z')", (slug, slug))
        c.execute("INSERT INTO bot_config(bot,config,owner_id) VALUES(?,?,'human:ana')",
                  (slug, '{"name":"x","reasoning_effort":"low","model":"kept-in-config"}'))
        c.execute("UPDATE bots SET runtime='codex', model='column-model', host='keeper', cwd='/work/x', "
                  "thread_id='t1', token_hash='h' WHERE id=?", (slug,))
    _back_to_before(path, "0043")
    boot(path)
    with sqlite3.connect(path) as c:
        columns = {row[1] for row in c.execute("PRAGMA table_info(bots)")}
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (slug,)).fetchone()[0])
    assert not {"runtime", "model", "host", "cwd", "thread_id", "token_hash"} & columns
    assert {"id", "slug", "effort", "state"} <= columns
    # boot adds its own defaults to every config (profile, wake_preset, ...); only the three keys this moves are checked
    assert {k: config.get(k) for k in ("name", "reasoning_effort", "model", "runtime", "host")} == {
        "name": "x", "reasoning_effort": "low", "model": "kept-in-config", "runtime": "codex", "host": "keeper"}


def _run_0043(c):
    spec = importlib.util.spec_from_file_location(
        "m0043", Path(__file__).resolve().parents[1] / "migrations" / "0043_drop_bot_legacy_columns.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.up(c)


def test_a_null_config_takes_the_columns_and_one_not_an_object_stops_0043(tmp_path):
    """Migration 0043: a config of JSON null counts as {} and gets the column values; a config that is not a JSON object
    stops the migration instead of dropping a runtime or model it cannot copy."""
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        for column in ("runtime", "model", "host", "cwd", "thread_id", "token_hash"):
            c.execute(f"ALTER TABLE bots ADD COLUMN {column} TEXT")
        for slug, config in (("null-bot", "null"), ("list-bot", "[]")):
            c.execute("INSERT INTO bots(id,slug,display_name,effort,state,created_at,runtime,model) "
                      "VALUES(?,?,'B','low','active','2026-01-01T00:00:00Z','codex','m1')", (slug, slug))
            c.execute("INSERT INTO bot_config(bot,config,owner_id) VALUES(?,?,'human:ana')", (slug, config))
    with sqlite3.connect(path) as c:
        with pytest.raises(RuntimeError, match="list-bot"):
            _run_0043(c)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE bot_config SET config='{}' WHERE bot='list-bot'")
    _back_to_before(path, "0043")
    boot(path)
    with sqlite3.connect(path) as c:
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='null-bot'").fetchone()[0])
    assert (config.get("runtime"), config.get("model")) == ("codex", "m1")


def _old_kpis(c):
    """kpis as a database before 0044 has it: goal_id (NOT NULL, '' for none) and the kpis_goal index."""
    c.execute("ALTER TABLE kpis ADD COLUMN goal_id TEXT NOT NULL DEFAULT ''")
    c.execute("CREATE INDEX kpis_goal ON kpis(goal_id)")


def _old_kpi(c, kid, goal, target):
    c.execute("INSERT INTO kpis(id,slug,name,unit,target,goal_id,owner_id,direction,cadence,created_at,created_by_id,"
              "definition_version,updated_at) VALUES(?,?,?,'',?,?,'company','up','weekly','2026-01-01T00:00:00Z',"
              "'human:ana',1,'2026-01-01T00:00:00Z')", (kid, kid, kid.title(), target, goal))


def test_kpi_goal_id_goes_and_a_kpi_taken_off_its_goal_stays_off(tmp_path):
    """Migration 0044 drops kpis.goal_id and its index. A link that exists keeps its own target; a KPI whose goal_id has
    no link (a person took it off the goal) is not linked again. kpis.target stays."""
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        for goal in ("g-old", "g-linked"):
            c.execute("INSERT INTO goals(id,title,owner_id,created_at,created_by_id,updated_at) VALUES(?,'Grow revenue',"
                      "'company','2026-01-01T00:00:00Z','human:ana','2026-01-01T00:00:00Z')", (goal,))
        _old_kpis(c)
        _old_kpi(c, "k-off", "g-old", 7.5)
        _old_kpi(c, "k-linked", "g-linked", 4)
        c.execute("INSERT INTO goal_kpis(id,goal_id,kpi_id,kind,target,deadline,created_at,created_by_id,updated_at,"
                  "updated_by_id) VALUES('k-linked','g-linked','k-linked','improve',9,'2026-12-31','2026-01-01T00:00:00Z',"
                  "'human:ana','2026-01-01T00:00:00Z','human:ana')")
    _back_to_before(path, "0044")
    boot(path)
    with sqlite3.connect(path) as c:
        columns = {row[1] for row in c.execute("PRAGMA table_info(kpis)")}
        links = {r[0]: r[1:] for r in c.execute("SELECT kpi_id, goal_id, kind, target FROM goal_kpis")}
        indexes = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        targets = dict(c.execute("SELECT id, target FROM kpis"))
    assert "goal_id" not in columns and "kpis_goal" not in indexes
    assert links == {"k-linked": ("g-linked", "improve", 9)}
    assert targets == {"k-off": 7.5, "k-linked": 4}
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first


def test_a_standalone_kpi_keeps_its_target_and_the_migration_runs(tmp_path):
    """Migration 0044 does not stop on a KPI that has a target and no goal, nor on one whose goal is gone: each stays
    a standalone KPI with its target on the KPI, and links to nothing."""
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        _old_kpis(c)
        _old_kpi(c, "k-loose", "", 3)
        _old_kpi(c, "k-orphan", "g-deleted", 5)
    _back_to_before(path, "0044")
    boot(path)
    with sqlite3.connect(path) as c:
        columns = {row[1] for row in c.execute("PRAGMA table_info(kpis)")}
        targets = dict(c.execute("SELECT id, target FROM kpis"))
        links = c.execute("SELECT count(*) FROM goal_kpis WHERE kpi_id IN ('k-loose','k-orphan')").fetchone()[0]
    assert "goal_id" not in columns and "target" in columns
    assert targets == {"k-loose": 3, "k-orphan": 5} and links == 0


def _migration(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "migrations" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_session_link_keeps_its_thread_once_in_its_url():
    """Migration 0048 strips the thread_id copy from session rows whose url gives that thread back, and leaves every
    other row alone: a url that only contains the id, or names another thread, keeps its copy."""
    up = _migration("0048_task_session_thread_once").up
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE task_links(id TEXT PRIMARY KEY, kind TEXT, url TEXT, detail TEXT)")
    rows = {
        "bot": ("session", "session:ops/t-1", '{"primary": true, "harness": "codex", "bot": "ops", "thread_id": "t-1"}'),
        "resume": ("session", "session:cd /tmp/ops && codex resume abc123",
                   '{"primary": false, "harness": "codex", "thread_id": "abc123"}'),
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
        "bot": {"primary": True, "harness": "codex", "bot": "ops"}, "resume": {"primary": False, "harness": "codex"}}
    assert {key: stored[key] for key in rows if key not in ("bot", "resume")} == {
        key: row[2] for key, row in rows.items() if key not in ("bot", "resume")}
    # What the API shows for the stripped rows is what it showed before.
    for key in ("bot", "resume"):
        shown = H._with_thread({"kind": "session", "url": rows[key][1], "detail": stored[key]})
        assert json.loads(shown["detail"]) == json.loads(rows[key][2])
    up(conn)
    assert dict(conn.execute("SELECT id, detail FROM task_links").fetchall()) == stored
    up(sqlite3.connect(":memory:"))                 # a database with no task_links has nothing to strip


# The tables 0049_idempotency_one_table.py moves into `idempotency`, as migration 0048 and earlier left them.
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
            (invite.format("cara"), "collide", "h3", '{"id": "m1", "sender_id": "human:ana"}', TS),   # and a send
            ("/api/v2/messages", "ans-2", "h9", '{"id": "m2", "sender_id": "human:ana"}', TS),
        ])
        # What the answer triggers had indexed (the test's DROP took the triggers): each answer that quotes a message.
        c.executemany("INSERT INTO idempotency_messages VALUES(?, 'human:ana', ?, ?)", [
            ("m1", invite.format("cara"), "collide"), ("m2", "/api/v2/messages", "ans-2")])
        c.executemany("INSERT INTO invite_sends VALUES('human:ana',?,?,?,?,?,?,?,?,?)", [
            (invite.format("cara"), "collide", "h3", "cara", "sent", None, None, TS, TS),
            (invite.format("dan"), "sending", "h4", "dan", "sending", None, None, TS, TS),
            (invite.format("eve"), "refused", "h5", "eve", "refused", "too_many", "Too many invites", TS, TS),
            # Fay was removed: people_remove scrubbed her binding (no hash, the refusal text that named her replaced).
            (invite.format("fay"), "scrubbed", "", "fay", "refused", "invite_failed", invites.REMOVED, TS, TS),
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
        c.execute("DELETE FROM schema_migrations WHERE version>='0049' AND version NOT LIKE 'legacy_%'")
    boot(path)

    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        rows = {(r["operation"], r["key"]): dict(r) for r in c.execute("SELECT rowid, * FROM idempotency")}
        assert len(rows) == 12, sorted(rows)
        answer = rows[("/api/v2/tasks/t1/files", "ans-1")]
        assert (answer["binding"], answer["state"], answer["response"]) == (None, "completed", '{"ok": true}')
        assert answer["rowid"] == before["ans-1"], "a stored answer keeps its rowid"
        assert rows[("/api/v2/messages", "ans-2")]["rowid"] == before["ans-2"]
        collide = rows[(invite.format("cara"), "collide")]
        assert (collide["binding"], collide["subject"], collide["state"], collide["response"]) == \
            ("invites", "cara", "completed", None), "the binding wins over the answer that shared its key"
        assert rows[(invite.format("dan"), "sending")]["state"] == "pending"
        eve = rows[(invite.format("eve"), "refused")]
        assert (eve["state"], eve["error_code"], eve["error_detail"]) == ("refused", "too_many", "Too many invites")
        fay = rows[(invite.format("fay"), "scrubbed")]
        assert (fay["subject"], fay["state"], fay["request_hash"], fay["error_detail"]) == \
            ("fay", "refused", "", invites.REMOVED), "a scrubbed binding stays scrubbed"
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
        indexes = {r[1] for r in c.execute("PRAGMA index_list(idempotency)")}
        assert {"idempotency_answers_by_created", "idempotency_bindings"} <= indexes
        assert "idempotency_by_created" not in indexes
        # The answer index keeps the answer, not the one that became a binding; the triggers are back.
        assert [tuple(r) for r in c.execute("SELECT message_id, key FROM idempotency_messages")] == [("m2", "ans-2")]
        triggers = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='idempotency'")}
        assert {"idempotency_messages_insert", "idempotency_messages_update", "idempotency_messages_delete"} <= triggers
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first


def test_the_idempotency_table_the_migration_builds_is_the_one_store_declares():
    source = STORE_SOURCE.read_text()
    declared = re.search(r"CREATE TABLE IF NOT EXISTS (idempotency\(.*?PRIMARY KEY\(actor,operation,key\)\));", source, re.S)
    migration = _migration("0049_idempotency_one_table")
    squash = lambda sql: re.sub(r"\s+", "", sql)
    assert declared and squash("CREATE TABLE " + declared.group(1)) == squash(migration.TABLE)
    assert migration.INDEXES[0] + ";" in source      # the answers' index; the bindings' one is the migration's own


def test_the_threads_delivery_trigger_follows_hubdbs_task_news_rule():
    """Migration 0047 makes threads_primary_delivery from literal SQL; the condition must stay hubdb's
    delivery_counts_sql, which every read side uses, or task news would wake people again on a new install."""
    module = importlib.import_module("backend.migrations.0047_native_threads_schema")
    assert H.delivery_counts_sql("NEW", "NEW.recipient_id") in module.THREAD_TRIGGERS["threads_primary_delivery"]
