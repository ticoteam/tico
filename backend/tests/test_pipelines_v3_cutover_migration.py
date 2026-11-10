"""The Pipelines v3 cut-over migration (backend/migrations/20261110054000_pipelines_v3_cutover.py), on a database of
the shape it meets: the old `tasks` table with its type and step, and the v3 tables the earlier migration made."""

import importlib
import sqlite3

import pytest

from backend import migrations
from backend.tests.helpers.migrations import boot, snapshot

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


def test_it_is_a_file_the_runner_knows():
    """Later files may follow it: pending migrations apply in any order, and each stands alone."""
    versions = [m.version for m in migrations.files()]
    assert "20261110054000_pipelines_v3_cutover" in versions
    assert CUTOVER.FOREIGN_KEYS_OFF is True


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
