"""The Pipelines v3 cut-over migration (backend/migrations/20261110054000_pipelines_v3_cutover.py), on a database of
the shape it meets: the old `tasks` table with its type and step, and the v3 tables the earlier migration made."""

import gzip
import importlib
import shutil
import sqlite3

import pytest

from backend import boot as boot_step, migrations, replication
from backend.tests.helpers.migrations import FIXTURES, PIPELINES_V3, boot, snapshot
from backend.tests.helpers.replication import prepare

CUTOVER = importlib.import_module("backend.migrations.20261110054000_pipelines_v3_cutover")
V3 = importlib.import_module("backend.migrations.20261110010000_pipelines_v3")

OLD = """
CREATE TABLE task_types(id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE task_steps(id TEXT PRIMARY KEY, type_id TEXT REFERENCES task_types(id), name TEXT);
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, title TEXT, body TEXT, requester_id TEXT, owner_id TEXT, status TEXT,
  due_at TEXT, conversation_id TEXT, created_at TEXT, updated_at TEXT, done_at TEXT, closed_at TEXT,
  closed_by_id TEXT, note TEXT, goal_id TEXT, labels TEXT NOT NULL DEFAULT '[]',
  type_id TEXT REFERENCES task_types(id), step_id TEXT REFERENCES task_steps(id), number INTEGER, step_rank REAL,
  private INTEGER NOT NULL DEFAULT 1, check_back_at TEXT, questions TEXT, answer TEXT, next_run INTEGER NOT NULL
  DEFAULT 0, carried_by TEXT, verdict TEXT, work_revision INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL
  DEFAULT 1);
CREATE INDEX tasks_type_step ON tasks(type_id, step_id);
CREATE INDEX tasks_carried_by ON tasks(carried_by) WHERE carried_by IS NOT NULL;
CREATE TABLE task_tags(task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, tag TEXT);
CREATE TABLE approvals(id TEXT PRIMARY KEY, task_id TEXT);
CREATE TABLE task_holds(task_id TEXT PRIMARY KEY);
CREATE TABLE stage_requirements(id TEXT PRIMARY KEY);
CREATE TABLE task_trash(task_id TEXT PRIMARY KEY, rows TEXT);
CREATE TABLE messages(id TEXT PRIMARY KEY);
INSERT INTO task_types VALUES('general', 'General');
INSERT INTO task_steps VALUES('general-open', 'general', 'Open');
INSERT INTO task_trash VALUES('gone', '{}');
"""


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "hub.sqlite", isolation_level=None)
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(OLD)
    V3.up(c)
    yield c
    c.close()


def _run(c):
    """As the runner runs it: foreign keys off around one transaction, then checked."""
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("BEGIN IMMEDIATE")
    try:
        CUTOVER.up(c)
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise
    finally:
        c.execute("PRAGMA foreign_keys=ON")


def _columns(c, table="tasks"):
    return {row[1] for row in c.execute(f"PRAGMA table_info({table})")}


def _tables(c):
    return {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_a_database_that_still_has_tasks_is_refused_and_nothing_changes(conn):
    conn.execute(
        "INSERT INTO tasks (id, title, status, type_id, step_id) VALUES ('t1','Old','doing','general','general-open')"
    )
    before = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
    with pytest.raises(RuntimeError, match="1 tasks from Tico 0.5"):
        _run(conn)
    assert conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall() == before
    assert conn.execute("SELECT id, status, step_id FROM tasks").fetchall() == [("t1", "doing", "general-open")]
    assert conn.execute("SELECT task_id FROM task_trash").fetchall() == [("gone",)]


def test_the_old_columns_tables_indexes_and_the_trash_go(conn):
    _run(conn)
    assert not _columns(conn) & set(CUTOVER.DEAD_COLUMNS)
    assert {"id", "status", "pipeline_id", "stage_id", "assignee_id", "version", "work_revision"} <= _columns(conn)
    assert not _tables(conn) & set(CUTOVER.OLD_TABLES)
    assert {r[3] for r in conn.execute("PRAGMA foreign_key_list(tasks)")} == {"pipeline_id", "stage_id"}
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE tbl_name='tasks' AND type='index'")}
    assert {"tasks_conversation", "tasks_number", "tasks_stage"} <= names
    assert not names & {"tasks_type_step", "tasks_carried_by"}
    assert conn.execute("SELECT COUNT(*) FROM task_trash").fetchone()[0] == 0
    # A table that names tasks keeps working against the new one.
    conn.execute("INSERT INTO tasks (id, status) VALUES ('t1', 'open')")
    conn.execute("INSERT INTO task_tags VALUES ('t1', 'x')")


def test_a_new_task_starts_open_in_no_stage(conn):
    _run(conn)
    conn.execute("INSERT INTO tasks (id, title, status) VALUES ('ok', 'Fine', 'open')")
    for column, value in (("status", "done"), ("status", None), ("pipeline_id", "general"), ("stage_id", "s1")):
        row = {"id": "bad", "status": "open", column: value}
        with pytest.raises(sqlite3.IntegrityError, match="a new task starts open, in no stage"):
            conn.execute(
                f"INSERT INTO tasks ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values())
            )
    assert conn.execute("SELECT id FROM tasks").fetchall() == [("ok",)]


@pytest.mark.parametrize("column", ["status", "stage_id", "owner_id", "pipeline_id"])
def test_status_stage_owner_and_pipeline_change_only_while_the_guard_is_open_for_that_task(conn, column):
    _run(conn)
    conn.execute("INSERT INTO pipelines (id, name, created_at, updated_at) VALUES ('general', 'General', 'now', 'now')")
    conn.execute(
        "INSERT INTO stages (id, pipeline_id, position, kind, name, created_at, updated_at) "
        "VALUES ('s1', 'general', 0, 'work', 'To do', 'now', 'now')"
    )
    conn.execute("INSERT INTO tasks (id, status) VALUES ('t1', 'open'), ('t2', 'open')")
    value = {"status": "done", "stage_id": "s1", "owner_id": "bot:a", "pipeline_id": "general"}[column]
    with pytest.raises(sqlite3.IntegrityError, match="change only through move"):
        conn.execute(f"UPDATE tasks SET {column}=? WHERE id='t1'", (value,))
    conn.execute("UPDATE task_move_guards SET open=1, task_id='t2' WHERE id=1")
    with pytest.raises(sqlite3.IntegrityError, match="change only through move"):
        conn.execute(f"UPDATE tasks SET {column}=? WHERE id='t1'", (value,))  # the guard is open for another task
    conn.execute(f"UPDATE tasks SET {column}=? WHERE id='t2'", (value,))
    conn.execute("UPDATE task_move_guards SET open=0, task_id=NULL WHERE id=1")
    conn.execute("UPDATE tasks SET title='Renamed' WHERE id='t1'")  # any other column is free
    assert conn.execute(f"SELECT {column} FROM tasks WHERE id='t2'").fetchone()[0] == value


def test_a_body_change_still_raises_the_work_revision(conn):
    _run(conn)
    conn.execute("INSERT INTO tasks (id, status, body) VALUES ('t1', 'open', 'a')")
    conn.execute("UPDATE tasks SET body='b' WHERE id='t1'")
    assert conn.execute("SELECT work_revision FROM tasks WHERE id='t1'").fetchone()[0] == 1


def test_a_new_database_starts_on_the_new_shape_and_a_second_start_changes_nothing(tmp_path):
    """The whole start (Store.initialize), not the migration alone: nothing that runs after the migrations brings an
    old table or column back, the constraints are the new ones, and General is there."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first
    with sqlite3.connect(path) as c:
        assert not _tables(c) & set(CUTOVER.OLD_TABLES)
        assert not _columns(c) & set(CUTOVER.DEAD_COLUMNS)
        assert {r[2] for r in c.execute("PRAGMA foreign_key_list(tasks)")} == {"pipelines", "stages"}
        on_tasks = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE tbl_name='tasks' AND type='trigger'")}
        assert {
            "tasks_move_only",
            "tasks_enter_only",
            "tasks_body_work_revision",
            "status_values_tasks_status_ins",
            "status_values_tasks_status_upd",
            "changes_tasks_insert",
            "changes_tasks_update",
            "changes_tasks_delete",
            "read_deps_task_update",
            "row_clock_parties_update",
        } <= on_tasks
        with pytest.raises(sqlite3.IntegrityError, match="tasks.status: invalid value|a new task starts open"):
            c.execute("INSERT INTO tasks (id, status) VALUES ('t1', 'doing')")
        assert c.execute("SELECT id FROM pipelines").fetchall() == [("general",)]
        stages = c.execute("SELECT id, kind FROM stages ORDER BY position, id").fetchall()
        assert set(stages) == {("general-todo", "work"), ("general-done", "done"), ("general-closed", "closed")}
        assert c.execute("SELECT stage_id, kind FROM requirements").fetchall() == [("general-todo", "owner_done")]
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_a_tico_05_database_with_tasks_is_refused_before_anything_is_written(tmp_path):
    """The start itself (preflight), not only the migration: an older database with pending migrations before the
    cut-over would otherwise have those applied before the cut-over refused it."""
    path = tmp_path / "hub.sqlite"
    with sqlite3.connect(path) as c:
        c.executescript(OLD)
        c.execute("PRAGMA user_version=71")
        c.execute(migrations.TABLE)
        c.execute("INSERT INTO tasks (id, title, status) VALUES ('t1', 'Old', 'doing')")
    before = path.read_bytes()
    with pytest.raises(migrations.MigrationError, match="tasks from Tico 0.5"):
        migrations.preflight(path)
    with pytest.raises(migrations.MigrationError, match="tasks from Tico 0.5"):
        boot(path)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as c:
        c.execute("DELETE FROM tasks")
    migrations.preflight(path)  # with no task there is nothing to lose: it may start


V3_FEEDBACK = "20261110020000_feedback_ask_subject"  # a migration before the cut-over that a 0.5 database has not had


def test_a_tico_05_database_is_refused_by_every_connection_before_any_migration_runs(tmp_path):
    """Not only the store's start: a bare connection (hubdb.migrate, which every hubdb.connect runs) refuses it too,
    before it runs a migration the database has not had, so nothing is written."""
    from backend.store import H

    path = tmp_path / "hub.sqlite"
    boot(path)
    old = sqlite3.connect(":memory:", isolation_level=None)
    with sqlite3.connect(path) as made:
        made.backup(old)
    old.row_factory = sqlite3.Row
    old.execute("DELETE FROM schema_migrations WHERE version IN (?, ?)", (migrations.CUTOVER, V3_FEEDBACK))
    old.execute("INSERT INTO tasks (id, title, status) VALUES ('t1', 'Old', 'open')")
    before = old.execute("SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall()
    with pytest.raises(migrations.MigrationError, match="tasks from Tico 0.5"):
        H.migrate(old)
    assert old.execute("SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall() == before
    old.close()


def test_a_database_on_this_release_with_tasks_starts(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO tasks (id, title, status) VALUES ('t1', 'New', 'open')")
    migrations.preflight(path)
    boot(path)


# A Tico v0.5.216 database with no tasks: `backend.manage initialize`-style Store.initialize, then one start of that
# release's app (its lifespan), then two people, two bots, a thread with a member and a message, an automation and the
# company profile written into its tables (raw SQL in the 0.5 shape). VACUUM INTO, gzip -9 -n. Its vision column only
# follows the strategy versions (strategy.sync_vision, in 0.5 too), so its description is what is checked.
V05 = FIXTURES / "hub-v0.5.216-no-tasks.sqlite.gz"
V05_DATA = {
    "SELECT id, email FROM humans ORDER BY id": [("morgan", "morgan@acme.example"), ("riley", "riley@acme.example")],
    "SELECT id, state FROM bots ORDER BY id": [("botops", "active"), ("librarian", "active")],
    "SELECT body FROM messages WHERE id='m1'": [("Hello team",)],
    "SELECT actor, role FROM thread_members WHERE thread_id='c1'": [("human:morgan", "owner")],
    "SELECT name FROM automations WHERE id='a1'": [("Weekly note",)],
    "SELECT description FROM company_profile": [("Acme makes anvils",)],
}


def _v05(tmp_path):
    path = tmp_path / "hub.sqlite"
    with gzip.open(V05) as source, open(path, "wb") as target:
        shutil.copyfileobj(source, target)
    return path


def _dangling(c):
    """Every trigger, view and index that names a table or column that is not there. The schema re-check SQLite runs on
    an ALTER TABLE (migrations.schema_errors), and each one walked on its own: a view is prepared, an index's and a
    trigger's table must be there, and an index's columns too."""
    found = [f"{kind} {name}: {error}" for kind, name, error in migrations.schema_errors(c)]
    tables = _tables(c)
    for kind, name, table in c.execute(
        "SELECT type, name, tbl_name FROM sqlite_master WHERE type IN ('view', 'index', 'trigger') AND sql IS NOT NULL"
    ).fetchall():
        if kind == "view":
            try:
                c.execute(f'SELECT * FROM "{name}" LIMIT 0').fetchall()
            except sqlite3.OperationalError as exc:
                found.append(f"view {name}: {exc}")
            continue
        if table not in tables | (_views(c) if kind == "trigger" else set()):
            found.append(f"{kind} {name}: no table {table}")
        elif kind == "index":
            columns = _columns(c, table)
            named = [row[2] for row in c.execute(f'PRAGMA index_info("{name}")') if row[2]]
            found += [f"index {name}: no column {column}" for column in named if column not in columns]
    return found


def _views(c):
    return {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='view'")}


def test_the_dangling_check_finds_a_trigger_on_another_table_that_reads_a_gone_column(tmp_path):
    c = sqlite3.connect(tmp_path / "x.sqlite", isolation_level=None)
    c.executescript(
        "CREATE TABLE a(x, y); CREATE TABLE b(z);"
        "CREATE TRIGGER reads_y AFTER INSERT ON b BEGIN SELECT y FROM a; END;"
        "CREATE TRIGGER reads_gone AFTER INSERT ON b BEGIN SELECT * FROM gone; END;"
        "CREATE VIEW sees_y AS SELECT y FROM a; DROP TABLE a; CREATE TABLE a(x);"
    )
    assert sorted(migrations.schema_errors(c)) == [
        ("trigger", "reads_gone", "no such table: main.gone"),
        ("trigger", "reads_y", "no such column: y"),
        ("view", "sees_y", "no such column: y"),
    ]
    assert len(c.execute("SELECT * FROM sqlite_master").fetchall()) == 5  # nothing was changed


def test_a_tico_05_database_with_no_tasks_starts_and_keeps_everything_but_the_old_engine(tmp_path, monkeypatch):
    """The 0.5 change feed left changes_attempts_carried on attempts, reading tasks.carried_by: the cut-over failed on
    it ("error in trigger changes_attempts_carried: no such column: carried_by") and the server could not start. Now
    every migration from Pipelines v3 on leaves nothing in the schema that names something gone, and the start leaves
    nothing either."""
    path = _v05(tmp_path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert "carried_by" in _columns(c)
        assert _dangling(c) == []
    run, after = migrations._run, {}

    def checked(conn, migration):
        run(conn, migration)
        if migration.version >= PIPELINES_V3:
            after[migration.version] = _dangling(conn)

    monkeypatch.setattr(migrations, "_run", checked)
    boot(path)
    assert migrations.CUTOVER in after
    assert {version: found for version, found in after.items() if found} == {}
    with sqlite3.connect(path, isolation_level=None) as c:
        assert _dangling(c) == []
        assert not _columns(c) & set(CUTOVER.DEAD_COLUMNS)
        assert not _tables(c) & set(CUTOVER.OLD_TABLES)
        for query, rows in V05_DATA.items():
            assert c.execute(query).fetchall() == rows, query
        assert c.execute("SELECT id FROM pipelines").fetchall() == [("general",)]
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    boot(path)  # and it starts again


def _files(directory):
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


def _old_with_its_task_in_the_log(made, path, left, environment_id=None):
    """A 0.5 database whose only task is in the write-ahead log, not yet in the file, copied to `path` with what a
    server that stopped (cleanly, or without closing) left beside it. Returns the files there, by name."""
    made.mkdir()
    path.parent.mkdir(exist_ok=True)
    writer = sqlite3.connect(made / "hub.sqlite", isolation_level=None)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.executescript(OLD)
    writer.execute("PRAGMA user_version=71")
    writer.execute(migrations.TABLE)
    writer.execute("CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    if environment_id:
        writer.execute("INSERT INTO registry_metadata VALUES('docker-environment-id', ?)", (f'"{environment_id}"',))
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("INSERT INTO tasks (id, title, status) VALUES ('t1', 'Old', 'doing')")
    if left == "no log":
        writer.close()  # the last connection merges the log and removes it
    for name in ("hub.sqlite", "hub.sqlite-wal", "hub.sqlite-shm"):  # what a server that stopped without closing left
        if (made / name).exists() and not (left == "log only" and name.endswith("-shm")):
            shutil.copyfile(made / name, path.parent / name)
    writer.close()
    before = _database_files(path.parent)
    assert (
        set(before)
        == {
            "no log": {"hub.sqlite"},
            "log and index": {"hub.sqlite", "hub.sqlite-wal", "hub.sqlite-shm"},
            "log only": {"hub.sqlite", "hub.sqlite-wal"},
        }[left]
    )
    return before


def _database_files(directory):
    return {path.name: path.read_bytes() for path in sorted(directory.glob("hub.sqlite*"))}


@pytest.mark.parametrize("left", ["no log", "log and index", "log only"])
def test_a_refused_tico_05_database_is_left_byte_for_byte_as_it_was(tmp_path, left):
    """The refusal must read the log to see the task, and leave the file, its -wal and its -shm (where there are any)
    unchanged, and make no new file. A read-write check merged the log into the file on close, and a plain read-only
    one writes the -shm or makes it."""
    path = tmp_path / "old" / "hub.sqlite"
    before = _old_with_its_task_in_the_log(tmp_path / "made", path, left)
    assert _files(path.parent) == before
    with pytest.raises(migrations.MigrationError, match="tasks from Tico 0.5"):
        migrations.preflight(path)
    with pytest.raises(migrations.MigrationError, match="tasks from Tico 0.5"):
        boot(path)
    assert _files(path.parent) == before


@pytest.mark.parametrize(
    ("left", "stored", "configured"),
    [("no log", None, None), ("log and index", "acme", "acme-other"), ("log only", "acme", None)],
)
def test_the_docker_start_refuses_a_tico_05_database_before_writing_its_id(tmp_path, left, stored, configured):
    """`docker/entrypoint.sh prepare` (backend/boot.py) writes the install's id into the database before the server
    starts. On a 0.5 database with tasks it refuses first: no id row added or changed, no -wal or -shm made, and the
    tunnel, Caddy files and environment markers after it are not written either."""
    data = tmp_path / "data"
    before = _old_with_its_task_in_the_log(tmp_path / "made", data / "hub.sqlite", left, stored)
    result, _ = prepare(tmp_path, **({"TICO_ENVIRONMENT_ID": configured} if configured else {}))
    assert result.returncode != 0
    assert "tasks from Tico 0.5" in result.stderr and "Traceback" not in result.stderr
    assert _database_files(data) == before
    assert not (data / replication.LOCAL_MARKER).exists()
    # The Slack gateway reads the id beside it, and leaves the database as it was too.
    assert boot_step.stored_environment_id({"TICO_DB": str(data / "hub.sqlite")}, tries=1) == stored
    assert _database_files(data) == before


@pytest.mark.parametrize("which", ["fresh", "0.5 with no tasks"])
def test_the_docker_start_still_prepares_a_new_volume_and_a_tico_05_database_with_no_tasks(tmp_path, which):
    data = tmp_path / "data"
    if which != "fresh":
        data.mkdir()
        shutil.copyfile(_v05(tmp_path), data / "hub.sqlite")
    result, _ = prepare(tmp_path, TICO_ENVIRONMENT_ID="acme")
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(data / "hub.sqlite") as c:
        assert c.execute("SELECT value FROM registry_metadata WHERE key='docker-environment-id'").fetchone() == (
            '"acme"',
        )
    boot(data / "hub.sqlite")  # and the server's own start takes it
