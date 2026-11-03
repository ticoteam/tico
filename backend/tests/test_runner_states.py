"""A computer's identity (`runners`) and its runtime state (`runner_states`, migration 0062): an existing file keeps every
state value when the columns move, the move runs once, and a heartbeat writes only the state row."""
import sqlite3

import pytest

from backend import runner_states
from backend.tests.test_api import api, post, runner  # noqa: F401
from backend.tests.test_migrations import boot, fresh, snapshot

OLD_STATE = (("last_seen_at", "TEXT"), ("awake_since", "TEXT"), ("version", "TEXT"),
             ("capacity", "INTEGER NOT NULL DEFAULT 4"), ("readiness", "TEXT NOT NULL DEFAULT '{}'"),
             ("readiness_at", "TEXT"), ("capabilities", "TEXT NOT NULL DEFAULT '[]'"), ("checkout", "TEXT"),
             ("restart_since", "TEXT"), ("memory_hold", "INTEGER NOT NULL DEFAULT 0"), ("last_sampled_at", "INTEGER"))
SEEN = ("2026-11-01T10:00:00.000000Z", "2026-11-01T08:00:00.000000Z", "1.2.3", 6, '{"bot-1": true}',
        "2026-11-01T09:59:00.000000Z", '["private_lanes_v1"]', "main@abc", "2026-11-01T09:00:00.000000Z", 1, 1761990000)


def _before_the_move(path, extra=()):
    """The file as it was before 0062: state columns on runners, no state table, view or trigger. r1 reported its state;
    r2 never did. `extra` statements run after the columns exist, to add indexes or triggers the file had."""
    with sqlite3.connect(path) as c:
        c.execute("DROP VIEW runners_with_state")
        c.execute("DROP TRIGGER runner_states_new_runner")
        c.execute("DROP TABLE runner_states")
        for name, declaration in OLD_STATE:
            c.execute(f"ALTER TABLE runners ADD COLUMN {name} {declaration}")
        c.execute("INSERT INTO humans(id,name) VALUES('h1','Ana')")
        names = ",".join(name for name, _ in OLD_STATE)
        c.execute(f"INSERT INTO runners(id,label,owner_id,token_hash,created_at,platform,{names}) "
                  f"VALUES('r1','Acme Mac','h1','hash-1','2026-01-01T00:00:00Z','darwin',{','.join('?' * len(SEEN))})",
                  SEEN)
        c.execute("INSERT INTO runners(id,label,owner_id,token_hash,created_at) "
                  "VALUES('r2','Acme box','h1','hash-2','2026-01-01T00:00:00Z')")
        for statement in extra:
            c.execute(statement)
        c.execute("DELETE FROM schema_migrations WHERE version>='0062' AND version NOT LIKE 'legacy_%'")


def test_an_existing_file_moves_every_state_value_off_runners_once(tmp_path):
    path = fresh(tmp_path)
    _before_the_move(path)
    boot(path)
    with sqlite3.connect(path) as c:
        columns = {row[1] for row in c.execute("PRAGMA table_info(runners)")}
        assert not columns & {name for name, _ in OLD_STATE}
        assert c.execute("SELECT label,platform FROM runners WHERE id='r1'").fetchone() == ("Acme Mac", "darwin")
        names = ",".join(name for name, _ in OLD_STATE)
        assert c.execute(f"SELECT {names} FROM runner_states WHERE runner_id='r1'").fetchone() == SEEN
        assert c.execute(f"SELECT {names} FROM runners_with_state WHERE id='r1'").fetchone() == SEEN
        # A runner that never reported reads the defaults, as it did on the old columns.
        assert c.execute("SELECT last_seen_at,capacity,readiness,capabilities,memory_hold FROM runners_with_state "
                         "WHERE id='r2'").fetchone() == (None, 4, "{}", "[]", 0)
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first


def test_an_index_on_a_moved_column_is_dropped_and_the_values_still_move(tmp_path):
    path = fresh(tmp_path)
    _before_the_move(path, extra=["CREATE INDEX runners_seen ON runners(last_seen_at)"])
    boot(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='runners_seen'").fetchall() == []
        assert c.execute("SELECT last_seen_at FROM runners_with_state WHERE id='r1'").fetchone() == (SEEN[0],)


def test_a_trigger_on_a_moved_column_stops_the_move_and_names_both(tmp_path):
    path = fresh(tmp_path)
    _before_the_move(path, extra=["CREATE TRIGGER runner_seen_watch AFTER UPDATE ON runners "
                                  "WHEN NEW.last_seen_at IS NULL BEGIN SELECT 1; END"])
    with pytest.raises(RuntimeError, match="runner_seen_watch.*last_seen_at"):
        boot(path)
    with sqlite3.connect(path) as c:
        # Nothing changed: the columns are still on runners, the trigger is still there, no state table.
        assert {"last_seen_at", "version"} <= {row[1] for row in c.execute("PRAGMA table_info(runners)")}
        assert c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name='runner_seen_watch'").fetchall()
        assert c.execute("SELECT name FROM sqlite_master WHERE name='runner_states'").fetchall() == []


def test_a_new_runner_gets_its_state_row_and_leaves_with_it(tmp_path):
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("INSERT INTO humans(id,name) VALUES('h1','Ana')")
        c.execute("INSERT INTO runners(id,label,owner_id,token_hash,created_at) "
                  "VALUES('r1','Acme Mac','h1','hash-1','2026-01-01T00:00:00Z')")
        assert c.execute("SELECT runner_id FROM runner_states").fetchall() == [("r1",)]
        c.execute("DELETE FROM runners WHERE id='r1'")
        assert c.execute("SELECT count(*) FROM runner_states").fetchone()[0] == 0


def test_saving_a_column_that_is_not_runner_state_is_refused():
    with pytest.raises(ValueError):
        runner_states.save(None, "r1", label="renamed")


def test_a_heartbeat_writes_the_state_row_and_never_the_identity_row(api):
    machine = runner(api)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("CREATE TABLE runner_identity_writes(n INTEGER)")
        c.execute("CREATE TRIGGER runner_identity_write AFTER UPDATE ON runners "
                  "BEGIN INSERT INTO runner_identity_writes VALUES(1); END")
    post(api, "runners/heartbeat", {"version": "9.9.9", "platform": "test", "capacity": 3,
                                    "readiness": {"bot-1": True}, "capabilities": ["private_lanes_v1"]},
         token=machine["token"])
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM runner_identity_writes").fetchone()[0] == 0
        version, seen, capabilities = c.execute(
            "SELECT version,last_seen_at,capabilities FROM runner_states WHERE runner_id=?",
            (machine["runner_id"],)).fetchone()
        assert version == "9.9.9" and seen and "private_lanes_v1" in capabilities
