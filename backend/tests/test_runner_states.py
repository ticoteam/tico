"""A computer's identity (`runners`) and its runtime state (`runner_states`): a new runner gets its state row, and a
heartbeat writes only the state row."""

import sqlite3

import pytest

from backend import runner_states
from backend.tests.helpers.api import post, runner  # noqa: F401
from backend.tests.helpers.migrations import fresh


def test_a_new_runner_gets_its_state_row_and_leaves_with_it(tmp_path):
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("INSERT INTO humans(id,name) VALUES('h1','Ana')")
        c.execute(
            "INSERT INTO runners(id,label,owner_id,token_hash,created_at) "
            "VALUES('r1','Acme Mac','h1','hash-1','2026-01-01T00:00:00Z')"
        )
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
        c.execute(
            "CREATE TRIGGER runner_identity_write AFTER UPDATE ON runners "
            "BEGIN INSERT INTO runner_identity_writes VALUES(1); END"
        )
    post(
        api,
        "runners/heartbeat",
        {
            "version": "9.9.9",
            "platform": "test",
            "capacity": 3,
            "readiness": {"bot-1": True},
            "capabilities": ["private_lanes_v1"],
        },
        token=machine["token"],
    )
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM runner_identity_writes").fetchone()[0] == 0
        version, seen, capabilities = c.execute(
            "SELECT version,last_seen_at,capabilities FROM runner_states WHERE runner_id=?", (machine["runner_id"],)
        ).fetchone()
        assert version == "9.9.9" and seen and "private_lanes_v1" in capabilities
