"""Migration 0075: a task's holds are task_holds rows (backend/migrations/0075_task_holds.py)."""
import importlib
import sqlite3

import pytest

from backend import hubdb as H, runner_wire, status_values, task_delete, task_holds

HOLDS = importlib.import_module("backend.migrations.0075_task_holds")

# The tasks table as the release before 0075 left it, cut to the columns this needs.
LEGACY = """
CREATE TABLE tasks(
 id TEXT PRIMARY KEY, title TEXT, owner_id TEXT, status TEXT, created_at TEXT, updated_at TEXT, blocker_key TEXT,
 snoozed_until TEXT, snoozed_by_id TEXT, snoozed_note TEXT, blocked_since TEXT, blocked_reason TEXT,
 blocked_kind TEXT, blocked_from_step TEXT, blocked_from_status TEXT, blocked_gate TEXT, holds_cleared_at TEXT);
CREATE INDEX tasks_snoozed ON tasks(snoozed_until) WHERE snoozed_until IS NOT NULL;
CREATE INDEX tasks_blocked_since ON tasks(blocked_since) WHERE blocked_since IS NOT NULL;
CREATE TRIGGER status_values_tasks_blocked_from_status_ins BEFORE INSERT ON tasks WHEN NEW.blocked_from_status
 IS NOT NULL AND NEW.blocked_from_status NOT IN ('open','review') BEGIN SELECT RAISE(ABORT, 'x'); END;
CREATE TABLE history_events(id TEXT PRIMARY KEY, subject_type TEXT, subject_id TEXT, actor_id TEXT, kind TEXT,
 old TEXT, new TEXT, note TEXT, created_at TEXT);
"""
GATE = '{"head": "abc123", "kind": "qa", "pr": 7}'
# The view a task gets from these rows: its holds list and the fields a claim hands a runner.
WIRED = ("blocked_gate_json", "snoozed_by", "blocked_since", "blocked_reason", "blocked_kind", "blocked_from_step",
         "blocked_from_status", "snoozed_until", "snoozed_note", "holds_cleared_at")



def up(conn):
    conn.execute("BEGIN IMMEDIATE")
    HOLDS.up(conn)
    conn.execute("COMMIT")


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "hub.db", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(LEGACY)
    c.execute("INSERT INTO tasks VALUES('both','Ship it','bot:ops','waiting','2026-10-01T00:00:00Z',"
              "'2026-10-05T00:00:00Z','key-1','2026-11-09T09:00:00Z','human:sam','after the freeze',"
              "'2026-10-04T00:00:00Z','Waiting on Sam','dependency','step-review','review',?,'2026-10-04T06:00:00Z')",
              (GATE,))
    c.execute("INSERT INTO tasks(id,title,status,created_at,updated_at,snoozed_until,snoozed_by_id) "
              "VALUES('snoozed','Later','open','2026-10-01T00:00:00Z','2026-10-02T00:00:00Z','2026-11-03T08:00:00Z',"
              "'human:ana')")
    c.execute("INSERT INTO tasks(id,title,status,created_at,updated_at) VALUES('free','Free','open','x','y')")
    c.execute("INSERT INTO history_events(id,subject_type,subject_id,actor_id,kind,old,new,note,created_at) "
              "VALUES('e1','task','both','human:sam','snoozed_until',NULL,'2026-11-09T09:00:00Z','',"
              "'2026-10-03T12:00:00Z')")
    yield c
    c.close()


def view(conn, sql, task_id):
    row = dict(conn.execute(f"SELECT * FROM {sql} tasks WHERE id=?", (task_id,)).fetchone())
    wired = runner_wire.task(row)
    return task_holds.public(row), {k: wired.get(k) for k in WIRED}


def test_each_open_hold_becomes_a_row_and_the_columns_go(conn):
    before = {tid: view(conn, "tasks", tid) for tid in ("both", "snoozed", "free")}
    up(conn)
    assert [tuple(r) for r in conn.execute(
        "SELECT task_id, kind, cause, reason, by_id, until_at, from_step_id, from_status, gate, cleared_at, created_at, "
        "released_at FROM task_holds ORDER BY task_id, kind")] == [
        ("both", "blocked", "dependency", "Waiting on Sam", None, None, "step-review", "review", GATE,
         "2026-10-04T06:00:00Z", "2026-10-04T00:00:00Z", None),
        ("both", "snoozed", None, "after the freeze", "human:sam", "2026-11-09T09:00:00Z", None, None, None, None,
         "2026-10-03T12:00:00Z", None),     # from when it was snoozed, the latest snoozed_until event
        ("snoozed", "snoozed", None, None, "human:ana", "2026-11-03T08:00:00Z", None, None, None, None,
         "2026-10-02T00:00:00Z", None)]     # no event: the task's updated_at
    columns = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert columns.isdisjoint(HOLDS.COLUMNS) and "blocker_key" in columns
    schema = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('index','trigger')")}
    assert schema.isdisjoint({"tasks_snoozed", "tasks_blocked_since", "status_values_tasks_blocked_from_status_ins"})
    # The holds list and the runner wire read the same from the rows as they did from the columns.
    assert {tid: view(conn, H.TASKS, tid) for tid in before} == before


def test_one_open_hold_of_a_kind_a_released_one_stays_as_history(conn):
    up(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO task_holds(task_id,kind,created_at) VALUES('both','blocked','now')")
    task_holds.release(conn, "both", "blocked", "2026-10-06T00:00:00Z")
    task_holds.put(conn, "both", "blocked", "2026-10-07T00:00:00Z", cause="manual", reason="Held")
    assert conn.execute("SELECT count(*) FROM task_holds WHERE task_id='both' AND kind='blocked'").fetchone()[0] == 2
    assert task_holds.open_hold(conn, "both", "blocked")["reason"] == "Held"
    # put on an open hold moves it on, as writing the old columns again did.
    task_holds.put(conn, "both", "blocked", "2026-10-08T00:00:00Z", reason="Held longer")
    assert task_holds.open_hold(conn, "both", "blocked")["created_at"] == "2026-10-08T00:00:00Z"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO task_holds(task_id,kind,created_at) VALUES('free','paused','now')")


def test_running_it_again_changes_nothing(conn):
    up(conn)
    before = [tuple(r) for r in conn.execute("SELECT * FROM task_holds ORDER BY id")]
    up(conn)
    assert [tuple(r) for r in conn.execute("SELECT * FROM task_holds ORDER BY id")] == before


def test_a_new_file_gets_only_the_table(tmp_path):
    c = sqlite3.connect(tmp_path / "new.db", isolation_level=None)
    try:
        c.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY, title TEXT)")
        up(c)
        assert c.execute("SELECT count(*) FROM task_holds").fetchone()[0] == 0
        assert [r[1] for r in c.execute("PRAGMA table_info(tasks)")] == ["id", "title"]
    finally:
        c.close()


def test_a_saved_status_outside_the_list_is_refused(conn):
    up(conn)
    spec = next(s for s in status_values.COLUMNS if (s.table, s.column) == ("task_holds", "from_status"))
    status_values._ensure_triggers(conn, spec)
    task_holds.change(conn, "both", "blocked", from_status="open")
    with pytest.raises(sqlite3.IntegrityError, match="task_holds.from_status"):
        task_holds.change(conn, "both", "blocked", from_status="sideways")
    task_holds.put(conn, "snoozed", "blocked", "now", from_status=None)      # NULL: nothing to go back to


def test_a_task_trashed_with_hold_columns_comes_back_with_its_holds(conn):
    up(conn)
    task = {"id": "free", "updated_at": "2026-10-02T00:00:00Z", "snoozed_until": None, "snoozed_by_id": None,
            "snoozed_note": None, "blocked_since": "2026-10-01T00:00:00Z", "blocked_reason": "Held for legal",
            "blocked_kind": "manual", "blocked_from_step": None, "blocked_from_status": "doing", "blocked_gate": None,
            "holds_cleared_at": None}
    task_delete._legacy_holds(conn, task)
    assert task_holds.open_hold(conn, "free", "blocked") == {
        "created_at": "2026-10-01T00:00:00Z", "cause": "manual", "reason": "Held for legal", "by_id": None,
        "until_at": None, "from_step_id": None, "from_status": "doing", "gate": None, "cleared_at": None}
    assert task_holds.open_hold(conn, "free", "snoozed") is None
    assert "snoozed_until" in task_delete.HANDLED["tasks"]     # not refused as a value with no column


ATTEMPTS = ("CREATE TABLE attempts(id TEXT PRIMARY KEY, job_id TEXT, bot_id TEXT, trigger TEXT, settled_at TEXT, "
            "exit TEXT, summary TEXT, outcome TEXT, input_tokens INTEGER)")
APPLIED = "2026-10-10T00:00:00.000000Z"    # 0072's applied_at, in the format hubdb.now() writes


def applied_0072(conn, at=APPLIED):
    conn.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)")
    if at:
        conn.execute("INSERT INTO schema_migrations VALUES('0072_turns_into_attempts','x',?)", (at,))


def test_a_run_record_0072_moved_gets_the_default_and_a_bare_lease_settled_after_it_stays_bare(conn):
    # 0072 moved the first turn with no trigger, and it finished before 0072 was applied: it gets turn_start's default.
    # The expired lease was closed after 0072 by turn_finish (zero usage, a record) and has no trigger of its own. The
    # bare lease never recorded anything. The usage row has no settle time. All three stay NULL.
    applied_0072(conn)
    conn.execute(ATTEMPTS)
    conn.executemany("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?)", [
        ("moved", None, "ops", None, "2026-10-02T00:00:00.000000Z", "ok", None, None, None),
        ("expired", "job-2", "ops", None, "2026-10-20T00:00:00.000000Z", "interrupted", None, None, 0),
        ("bare", "job-1", "ops", None, None, None, None, None, None),
        ("usage", None, "ops", None, None, None, None, None, 120),
        ("kept", None, "ops", "task", "2026-10-02T00:00:00.000000Z", "ok", None, None, None),
    ])
    up(conn)
    assert dict(conn.execute("SELECT id, trigger FROM attempts").fetchall()) == {
        "moved": "message", "expired": None, "bare": None, "usage": None, "kept": "task"}


def test_with_no_0072_row_no_run_record_is_repaired(conn):
    # A file that never ran 0072 as a numbered migration has no rows 0072 moved: nothing is repaired, with no
    # schema_migrations table at all and with one that lacks the row.
    conn.execute(ATTEMPTS)
    conn.execute("INSERT INTO attempts VALUES('moved',NULL,'ops',NULL,'2026-10-02T00:00:00.000000Z','ok',NULL,NULL,NULL)")
    up(conn)
    assert [row[0] for row in conn.execute("SELECT trigger FROM attempts")] == [None]
    applied_0072(conn, at=None)
    up(conn)
    assert [row[0] for row in conn.execute("SELECT trigger FROM attempts")] == [None]


def test_a_copy_that_moves_fewer_holds_than_the_columns_hold_stops_the_migration(conn, monkeypatch):
    monkeypatch.setattr(HOLDS, "_copy", lambda conn, have: None)
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(RuntimeError, match="0 open holds for 3 held tasks"):
        HOLDS.up(conn)
    conn.execute("ROLLBACK")
    assert "snoozed_until" in {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='task_holds'").fetchone()[0] == 0


def test_a_hold_hubdb_wrote_before_the_migration_is_not_copied_twice(conn):
    # hubdb.migrate makes task_holds before the migration files run, so a row can be there already: the copy skips a
    # task's hold that has an open row, and the count reads only the tasks whose hold columns are set.
    conn.execute(HOLDS.TABLE)
    for statement in HOLDS.INDEXES:
        conn.execute(statement)
    conn.execute("INSERT INTO task_holds(task_id,kind,cause,created_at) "
                 "VALUES('both','blocked','manual','2026-10-04T01:00:00Z')")
    conn.execute("INSERT INTO task_holds(task_id,kind,created_at) VALUES('free','snoozed','2026-10-04T02:00:00Z')")
    up(conn)
    assert [tuple(r) for r in conn.execute(
        "SELECT task_id, kind, cause FROM task_holds WHERE task_id='both' AND released_at IS NULL ORDER BY kind")] == [
        ("both", "blocked", "manual"), ("both", "snoozed", None)]
