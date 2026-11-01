"""The cleanup's startup renames (backend/migrations/renames.py) upgrade a database from before the cleanup in place.

The old database is backend/tests/fixtures/schema_pre_cleanup.sql, a dump of a fresh hub at the last release
before the renames (schema 71), with a few rows written under the old column names. One boot renames every
column, keeps the rows, keeps every index, rewrites every trigger and view so it still works, and a second boot
changes nothing."""

import json
import re
import sqlite3
from pathlib import Path

import pytest

from backend.migrations import renames as schema_renames
from backend.config import Settings
from backend.migrations.renames_data import COLUMNS
from backend.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "schema_pre_cleanup.sql"
NEW = {(table, old): new for table, old, new, _wave in COLUMNS}
NOW = "2026-10-01T00:00:00Z"


def new(table, old):
    return NEW.get((table, old), old)


def columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def schema(conn):
    return sorted(tuple(r) for r in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master "
                                                 "WHERE sql IS NOT NULL"))


def restore(path):
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text())
    conn.close()
    conn = sqlite3.connect(path)        # the dump writes the FTS tables' schema rows; a new connection reads them
    conn.executescript(f"""
        INSERT INTO bots(id, slug, display_name, state, created) VALUES('ops', 'ops', 'Ops', 'active', '{NOW}');
        INSERT INTO humans(id, name, email, teams_json) VALUES('ana', 'Ana', 'ana@acme.example', '["ops"]');
        INSERT INTO runners(id, label, operator, token_hash, created, readiness_json)
        VALUES('runner-1', 'Acme Mac', 'ana', 'hash', '{NOW}', '{{"ok":true}}');
        INSERT INTO conversations(id, kind, subject, participants_json, created, scope)
        VALUES('conv-1', 'task', 'Acme launch', '["human:ana","bot:ops"]', '{NOW}', 'direct');
        INSERT INTO tasks(id, title, body, requester, owner, status, due, conversation_id, created, updated,
                          labels_json, acceptance_json)
        VALUES('task-1', 'Ship the Acme launch', 'body', 'human:ana', 'bot:ops', 'open', '2026-10-09',
               'conv-1', '{NOW}', '{NOW}', '["launch"]', '["it ships"]');
        INSERT INTO messages(id, conversation_id, from_actor, to_actor, kind, body, refs_json, created)
        VALUES('msg-1', 'conv-1', 'human:ana', 'human:ben', 'chat', 'hello', '{{"task":"task-1"}}', '{NOW}');
        INSERT INTO jobs(id, message_id, bot, state, created) VALUES('job-1', 'msg-1', 'ops', 'completed', '{NOW}');
        INSERT INTO attempts(id, job_id, bot, runner_id, generation, token_hash, state, lease_until, created,
                             started, finished, result_json)
        VALUES('att-1', 'job-1', 'ops', 'runner-1', 1, 'hash', 'completed', '{NOW}', '{NOW}', '{NOW}', '{NOW}',
               '{{"ok":true}}');
    """)
    before = {"indexes": {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")},
              "triggers": {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")},
              "views": {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")},
              "columns": {t: columns(conn, t) for (t,) in conn.execute(
                  "SELECT name FROM sqlite_master WHERE type='table'")}}
    conn.close()
    return before


@pytest.fixture(scope="module")
def upgraded(tmp_path_factory):
    path = tmp_path_factory.mktemp("renames") / "hub.sqlite"
    before = restore(path)
    store = Store(Settings(db_path=path))
    store.initialize()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    yield store, conn, before
    conn.close()


def test_the_map_names_only_columns_the_old_schema_has():
    tables = {}
    for fixture in (FIXTURE, FIXTURE.with_name("schema_old_main.sql")):
        conn = sqlite3.connect(":memory:")
        conn.executescript(fixture.read_text())
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            tables.setdefault(t, columns(conn, t))
    # A later wave may rename what an earlier one named (automation_runs: state_json -> state -> saved_state).
    named = {(t, n) for t, _old, n, _wave in COLUMNS}
    missing = [(t, old) for t, old, _new, _wave in COLUMNS if old not in tables.get(t, ()) and (t, old) not in named]
    clashes = [(t, old, n) for t, old, n, _wave in COLUMNS if n in tables.get(t, ())]
    assert not missing and not clashes, (missing[:5], clashes[:5])


def test_every_column_has_its_new_name_and_the_rows_kept_their_values(upgraded):
    _store, conn, before = upgraded
    for table, old, renamed, _wave in COLUMNS:
        if old not in before["columns"].get(table, ()):
            continue
        have = columns(conn, table)
        renamed = NEW.get((table, renamed), renamed)
        assert renamed in have and old not in have, (table, old, renamed)
    task = conn.execute("SELECT * FROM tasks WHERE id='task-1'").fetchone()
    assert json.loads(task[new("tasks", "labels_json")]) == ["launch"]
    assert json.loads(task[new("tasks", "acceptance_json")]) == ["it ships"]
    assert task[new("tasks", "due")] == "2026-10-09" and task[new("tasks", "created")] == NOW
    message = conn.execute("SELECT * FROM messages WHERE id='msg-1'").fetchone()
    assert json.loads(message[new("messages", "refs_json")]) == {"task": "task-1"}
    attempt = conn.execute("SELECT * FROM attempts WHERE id='att-1'").fetchone()
    assert json.loads(attempt[new("attempts", "result_json")]) == {"ok": True}
    assert attempt[new("attempts", "finished")] == NOW
    room = conn.execute("SELECT * FROM conversations WHERE id='conv-1'").fetchone()
    assert json.loads(room[new("conversations", "participants_json")]) == ["human:ana", "bot:ops"]


def test_indexes_triggers_and_views_survive_and_name_no_old_column(upgraded):
    _store, conn, before = upgraded
    for kind in ("index", "trigger", "view"):
        now = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))}
        # Growth segments put growth_cohorts_month_segment in place of the month-only unique index.
        assert before[kind + "es" if kind == "index" else kind + "s"] - {"growth_cohorts_month"} <= now, kind
    # An old name may live on as a column elsewhere (or as another rename's new name), so look only for names
    # that no table has any more, outside string literals.
    current = set().union(*(columns(conn, t) for (t,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")))
    gone = {old for _t, old, _n, _w in COLUMNS} - current
    left = []
    for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"):
        words = set(re.findall(r"\w+", re.sub(r"'(?:[^']|'')*'", "''", re.sub(r"--[^\n]*", "", sql))))
        left += [(name, old) for old in gone & words]
    assert not left, left[:10]
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert [tuple(r) for r in conn.execute("PRAGMA foreign_key_check")] == [], "foreign keys"


def test_a_message_to_a_bot_still_queues_a_job(upgraded):
    _store, conn, _before = upgraded
    refs = new("messages", "refs_json")
    created = new("messages", "created")
    conn.execute(f"INSERT INTO messages(id, conversation_id, sender_id, recipient_id, kind, body, {refs}, {created}) "
                 "VALUES('msg-2', 'conv-1', 'human:ana', 'bot:ops', 'chat', 'please ship', '{}', ?)", (NOW,))
    job = conn.execute("SELECT * FROM jobs WHERE message_id='msg-2'").fetchone()
    assert job and job["bot_id"] == "ops" and job["state"] == "queued" and job[new("jobs", "created")] == NOW
    # A quiet message (read through the renamed refs column) queues nothing.
    conn.execute(f"INSERT INTO messages(id, conversation_id, sender_id, recipient_id, kind, body, {refs}, {created}) "
                 "VALUES('msg-3', 'conv-1', 'human:ana', 'bot:ops', 'chat', 'fyi', '{\"quiet\":1}', ?)", (NOW,))
    assert not conn.execute("SELECT 1 FROM jobs WHERE message_id='msg-3'").fetchone()
    conn.rollback()


def test_the_external_sections_search_index_still_matches_its_table(upgraded):
    _store, conn, _before = upgraded
    conn.execute("INSERT INTO external_sections(id, version_id, position, title, path, heading, body, start_line, "
                 "end_line) VALUES(901, 'v1', 0, 'Runbook', 'docs/runbook.md', 'Deploys', 'zebracorn rollout', 1, 9)")
    hits = conn.execute("SELECT rowid FROM external_sections_fts WHERE external_sections_fts MATCH 'zebracorn'")
    assert [r[0] for r in hits] == [901]
    conn.execute("INSERT INTO external_sections_fts(external_sections_fts) VALUES('integrity-check')")
    conn.rollback()


def test_an_upgraded_database_has_the_shape_of_a_new_one(upgraded, tmp_path):
    """The migration files run on the old shape and the startup code makes the new one: both roads end at the same
    tables, columns, indexes, triggers and views (SQLite's rewrite may quote a renamed name; that is all)."""
    _store, conn, _before = upgraded
    fresh = tmp_path / "fresh.sqlite"
    Store(Settings(db_path=fresh)).initialize()

    def shape(c):
        tables = {t: columns(c, t) for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        others = {(kind, name): re.sub(r"\s+", " ", sql).replace('"', "") for kind, name, sql in c.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE type!='table' AND sql IS NOT NULL")}
        return tables, others

    new_conn = sqlite3.connect(fresh)
    try:
        assert shape(conn) == shape(new_conn)
    finally:
        new_conn.close()


def test_a_second_boot_changes_nothing(upgraded):
    store, conn, _before = upgraded
    assert schema_renames.pending(conn) == []
    first = schema(conn)
    store.initialize()
    assert schema(conn) == first
    assert schema_renames.apply(conn) == 0


def test_a_database_with_both_names_is_refused(tmp_path):
    table, old, renamed, _wave = COLUMNS[0]
    conn = sqlite3.connect(tmp_path / "both.sqlite")
    conn.execute(f'CREATE TABLE "{table}"("{old}" TEXT, "{renamed}" TEXT)')
    with pytest.raises(schema_renames.RenameConflict):
        schema_renames.apply(conn)
    assert columns(conn, table) == {old, renamed}


def test_apply_runs_in_the_callers_transaction_and_skips_tables_it_lacks(tmp_path):
    table, old, renamed, _wave = next(c for c in COLUMNS if c[0] == "tasks")
    conn = sqlite3.connect(tmp_path / "part.sqlite", isolation_level=None)
    conn.execute(f'CREATE TABLE tasks("id" TEXT, "{old}" TEXT)')     # one table of the old schema, nothing else
    conn.execute("BEGIN")
    assert schema_renames.apply(conn) == 1 and conn.in_transaction
    assert renamed in columns(conn, "tasks")
    conn.execute("ROLLBACK")                                          # the caller decides: nothing was committed
    assert columns(conn, "tasks") == {"id", old}
    assert schema_renames.apply_now(conn) == 1 and not conn.in_transaction
    assert columns(conn, "tasks") == {"id", renamed} and schema_renames.apply_now(conn) == 0
