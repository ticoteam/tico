"""Migration 0072: a run is one attempts row (backend/migrations/0072_turns_into_attempts.py)."""
import importlib
import sqlite3

import pytest

MERGE = importlib.import_module("backend.migrations.0072_turns_into_attempts")

# The run tables as the release before 0072 left them (column names after the cleanup's renames).
LEGACY = """
CREATE TABLE runners(id TEXT PRIMARY KEY);
CREATE TABLE jobs(id TEXT PRIMARY KEY);
CREATE TABLE attempts(
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), bot_id TEXT NOT NULL,
 runner_id TEXT NOT NULL REFERENCES runners(id), generation INTEGER NOT NULL,
 token_hash TEXT NOT NULL UNIQUE, state TEXT NOT NULL, lease_until TEXT NOT NULL,
 created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, thread_id TEXT,
 result TEXT, final_text TEXT, last_seq INTEGER NOT NULL DEFAULT 0, stop_requested_at TEXT, gateway_key_id TEXT,
 handed_runtime TEXT, handed_model TEXT, lane TEXT NOT NULL DEFAULT 'main', wake TEXT, model TEXT);
CREATE INDEX attempts_job ON attempts(job_id);
CREATE TABLE attempt_events(id INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(id), seq INTEGER);
CREATE TABLE turns(
 id TEXT PRIMARY KEY, bot TEXT, thread_id TEXT, started_at TEXT, finished_at TEXT, trigger TEXT, message_id TEXT,
 task_id TEXT, exit TEXT, summary TEXT, input_tokens INTEGER, cached_tokens INTEGER, output_tokens INTEGER,
 model TEXT, provider TEXT, est_cost_usd REAL, billing TEXT, trigger_kind TEXT, purpose TEXT, outcome TEXT);
CREATE TABLE deltas(id TEXT PRIMARY KEY, turn_id TEXT, seq INTEGER, kind TEXT, text TEXT, created_at TEXT);
"""


def up(conn):
    """As backend/migrations runs it: keys off, one transaction, and no row left referring to a missing one."""
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN IMMEDIATE")
    MERGE.up(conn)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.execute("COMMIT")
    conn.execute("PRAGMA foreign_keys=ON")


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "hub.db", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(LEGACY)
    c.execute("INSERT INTO runners VALUES('r1')")
    c.executemany("INSERT INTO jobs VALUES(?)", [("j1",), ("j2",)])
    c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,"
              "finished_at,thread_id,model) VALUES('a1','j1','ops','r1',1,'h1','completed','2026-01-01T00:10:00Z',"
              "'2026-01-01T00:00:00Z','2026-01-01T00:05:00Z',NULL,'{\"id\":\"m\"}')")
    c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
              "VALUES('a2','j2','ops','r1',1,'h2','failed','2026-01-01T00:10:00Z','2026-01-01T00:01:00Z')")
    c.execute("INSERT INTO attempt_events(attempt_id,seq) VALUES('a1',1)")
    c.execute("INSERT INTO turns VALUES('a1','ops','thread-1','2026-01-01T00:00:00Z','2026-01-01T00:06:00Z',"
              "'task','m1','t1','completed','Checked.',100,50,10,'gpt-6-sol','openai',0.25,'api','status_or_relation',"
              "'work on task','acted')")
    c.execute("INSERT INTO turns(id,bot,started_at,finished_at,trigger,exit,input_tokens) "
              "VALUES('old','cmo','2025-06-01T00:00:00Z','2025-06-01T00:01:00Z','message','completed',7)")
    c.execute("INSERT INTO deltas VALUES('d1','a1',1,'text','Hel','2026-01-01T00:00:01Z')")
    yield c
    c.close()


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_each_turn_moves_onto_its_attempt_and_the_old_tables_go(conn):
    up(conn)
    assert "turns" not in tables(conn)
    a1 = dict(conn.execute("SELECT * FROM attempts WHERE id='a1'").fetchone())
    # The lease and the claim's own columns stay as they were.
    assert (a1["job_id"], a1["runner_id"], a1["state"], a1["finished_at"], a1["model"]) == \
        ("j1", "r1", "completed", "2026-01-01T00:05:00Z", '{"id":"m"}')
    # The record: finished_at -> settled_at, model -> usage_model, thread_id filled from the turn.
    assert {k: a1[k] for k in ("settled_at", "usage_model", "thread_id", "trigger", "message_id", "task_id", "exit",
                               "summary", "input_tokens", "cached_tokens", "output_tokens", "provider",
                               "est_cost_usd", "billing", "trigger_kind", "purpose", "outcome")} == {
        "settled_at": "2026-01-01T00:06:00Z", "usage_model": "gpt-6-sol", "thread_id": "thread-1", "trigger": "task",
        "message_id": "m1", "task_id": "t1", "exit": "completed", "summary": "Checked.", "input_tokens": 100,
        "cached_tokens": 50, "output_tokens": 10, "provider": "openai", "est_cost_usd": 0.25, "billing": "api",
        "trigger_kind": "status_or_relation", "purpose": "work on task", "outcome": "acted"}
    assert conn.execute("SELECT count(*) FROM attempt_events WHERE attempt_id='a1'").fetchone()[0] == 1
    assert "attempts_job" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}


def test_an_attempt_with_no_turn_has_no_record_and_a_turn_with_no_attempt_has_no_lease(conn):
    up(conn)
    a2 = dict(conn.execute("SELECT * FROM attempts WHERE id='a2'").fetchone())
    assert (a2["job_id"], a2["state"], a2["trigger"], a2["settled_at"]) == ("j2", "failed", None, None)
    old = dict(conn.execute("SELECT * FROM attempts WHERE id='old'").fetchone())
    assert {k: old[k] for k in ("job_id", "runner_id", "generation", "token_hash", "state", "lease_until")} == \
        dict.fromkeys(("job_id", "runner_id", "generation", "token_hash", "state", "lease_until"))
    assert (old["bot_id"], old["created_at"], old["settled_at"], old["trigger"], old["input_tokens"]) == \
        ("cmo", "2025-06-01T00:00:00Z", "2025-06-01T00:01:00Z", "message", 7)
    assert conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 3


def test_deltas_follow_their_run(conn):
    up(conn)
    assert tuple(conn.execute("SELECT id, attempt_id, text FROM deltas").fetchone()) == ("d1", "a1", "Hel")


def test_a_job_still_needs_its_whole_lease_and_state_keeps_its_values(conn):
    up(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO attempts(id,job_id,bot_id,created_at) VALUES('bad','j1','ops','x')")
    conn.execute("INSERT INTO attempts(id,bot_id,created_at) VALUES('chat','ops','x')")    # no job: no lease
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE attempts SET state='sideways' WHERE id='a1'")


def test_running_it_again_changes_nothing(conn):
    up(conn)
    before = [tuple(r) for r in conn.execute("SELECT * FROM attempts ORDER BY id")]
    up(conn)
    assert [tuple(r) for r in conn.execute("SELECT * FROM attempts ORDER BY id")] == before


def test_a_file_with_turns_and_no_attempts_table_gets_one(tmp_path):
    c = sqlite3.connect(tmp_path / "old.db", isolation_level=None)
    try:
        c.execute("CREATE TABLE turns(id TEXT PRIMARY KEY, bot TEXT, thread_id TEXT, started_at TEXT, "
                  "finished_at TEXT, trigger TEXT, model TEXT)")
        c.execute("INSERT INTO turns VALUES('t1','ops','th','2025-01-01','2025-01-02','message','m')")
        c.execute("CREATE TABLE deltas(id TEXT PRIMARY KEY, turn_id TEXT, seq INTEGER)")
        up(c)
        assert tuple(c.execute("SELECT id, bot_id, thread_id, created_at, settled_at, usage_model, job_id "
                               "FROM attempts").fetchone()) == ("t1", "ops", "th", "2025-01-01", "2025-01-02", "m", None)
        assert "turns" not in tables(c)
    finally:
        c.close()
