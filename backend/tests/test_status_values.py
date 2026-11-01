"""The status and state lists (backend/status_values.py): refused outside the list, one spelling per meaning, and an
upgrade that names what it cannot map rather than guessing."""
import sqlite3

import pytest

from backend import google_connect, hubdb, migrations, status_values as SV
from backend.config import Settings
from backend.store import Store


def _fresh_store(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text("owner: ana@acme.example\n")
    settings = Settings(db_path=tmp_path / "hub.db", registry_dir=registry, test_identities={})
    Store(settings).initialize(seed_market=False)
    return settings.db_path


# Columns whose CHECK wants a real value, not a placeholder.
OVERRIDES = {("fin_payments", "method"): "ach", ("bot_config", "config"): "{}"}


def _required(conn, table, skip):
    """A value for every column the insert must name: NOT NULL with no default, except the one under test."""
    out = {}
    for _cid, name, kind, notnull, default, _pk in conn.execute(f"PRAGMA table_info({table})").fetchall():
        if name == skip or not (notnull and default is None):
            continue
        out[name] = OVERRIDES.get((table, name), 1 if kind.upper() in ("INTEGER", "BIGINT", "REAL") else f"{name}-1")
    return out


# Every column the lists cover, with the table the insert goes to. Foreign keys are off for the probe, so parents are
# not needed: the trigger or CHECK refuses the value before the row exists.
PROBED = [(spec.table, spec.column, spec.enforce) for spec in SV.COLUMNS]


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    return _fresh_store(tmp_path_factory.mktemp("status-values"))


@pytest.mark.parametrize("table,column,enforce", PROBED)
def test_a_value_outside_the_list_is_refused(db, table, column, enforce):
    with sqlite3.connect(db, isolation_level=None) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        values = _required(conn, table, column)
        values[column] = "not-a-listed-value"
        names = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with pytest.raises(sqlite3.IntegrityError) as refused:
            conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(values.values()))
        message = str(refused.value)
        if enforce == "trigger":
            assert f"{table}.{column}: invalid value" in message
        else:
            assert f"status_values_{column}" in message


@pytest.mark.parametrize("table,column,value", [
    ("jobs", "state", "canceled"), ("webhook_sends", "state", "canceled"),
    ("fin_payments", "status", "canceled"), ("fin_transfers", "status", "canceled"),
    ("tasks", "status", "doing"), ("attempts", "state", "input"), ("automation_runs", "status", "held"),
    ("bots", "state", "archived"), ("goals", "status", "gray"), ("approvals", "decision", "withdrawn"),
    ("bot_status", "state", "waiting_bot"), ("bot_config", "onboarding_state", "needs_setup"),
])
def test_a_listed_value_is_accepted(db, table, column, value):
    with sqlite3.connect(db, isolation_level=None) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        values = _required(conn, table, column)
        values[column] = value
        conn.execute(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                     tuple(values.values()))


def test_the_cancelled_spelling_is_refused_on_a_fresh_file(db):
    with sqlite3.connect(db, isolation_level=None) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        with pytest.raises(sqlite3.IntegrityError, match="jobs.state: invalid value"):
            conn.execute("INSERT INTO jobs(id,message_id,bot_id,state,created_at) VALUES('j1','m1','b1','cancelled','t')")


def test_a_fresh_file_is_at_the_current_version_with_the_lists_installed(db):
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(hubdb.MIGRATIONS) == 71
        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert "status_values_jobs_state_ins" in triggers and "status_values_webhook_sends_state_upd" in triggers
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='fin_payments'").fetchone()[0]
        assert "'canceled'" in sql and "'cancelled'" not in sql


# The upgrade, on small hand-made tables (what an existing file held before migration 0002).

def _old_file(path):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE jobs(id TEXT PRIMARY KEY, message_id TEXT NOT NULL, bot_id TEXT NOT NULL,
                          state TEXT NOT NULL DEFAULT 'queued', created_at TEXT NOT NULL);
        CREATE INDEX jobs_state ON jobs(state);
    """)
    return conn


def _upgrade_as_migrate_runs_it(conn):
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        SV.upgrade(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def test_the_upgrade_names_values_it_cannot_map_and_changes_nothing(tmp_path):
    conn = _old_file(tmp_path / "old.db")
    conn.executemany("INSERT INTO jobs(id,message_id,bot_id,state,created_at) VALUES(?,?,?,?,?)",
                     [("j1", "m", "b", "queued", "t"), ("j2", "m", "b", "stalled", "t"),
                      ("j3", "m", "b", "cancelled", "t")])
    with pytest.raises(SV.StatusValuesError) as failed:
        _upgrade_as_migrate_runs_it(conn)
    assert "jobs.state = 'stalled' (1 rows)" in str(failed.value)
    assert conn.execute("SELECT state FROM jobs ORDER BY id").fetchall() == [("queued",), ("stalled",), ("cancelled",)]


def test_the_upgrade_rewrites_cancelled_and_refuses_it_afterwards(tmp_path):
    conn = _old_file(tmp_path / "old.db")
    conn.executemany("INSERT INTO jobs(id,message_id,bot_id,state,created_at) VALUES(?,?,?,?,?)",
                     [("j1", "m", "b", "queued", "t"), ("j2", "m", "b", "cancelled", "t")])
    _upgrade_as_migrate_runs_it(conn)
    assert conn.execute("SELECT state FROM jobs ORDER BY id").fetchall() == [("queued",), ("canceled",)]
    with pytest.raises(sqlite3.IntegrityError, match="jobs.state: invalid value"):
        conn.execute("UPDATE jobs SET state='cancelled' WHERE id='j1'")


def test_google_connects_state_is_renamed_to_oauth_state(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db", isolation_level=None)
    conn.executescript("""
        CREATE TABLE google_connects(person_id TEXT PRIMARY KEY, mailbox TEXT NOT NULL, status TEXT NOT NULL,
          state TEXT, verifier TEXT, code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE UNIQUE INDEX google_connects_state ON google_connects(state) WHERE state IS NOT NULL;
        INSERT INTO google_connects(person_id,mailbox,status,state,created_at,updated_at)
          VALUES('p1','ana@acme.example','signing_in','nonce-1','t','t');
    """)
    rename = migrations.Migration(migrations.DIRECTORY / "0003_google_connects_oauth_state.py")
    migrations._module(rename).up(conn)
    google_connect.ensure_schema(conn)
    columns = [r[1] for r in conn.execute("PRAGMA table_info(google_connects)")]
    assert "oauth_state" in columns and "state" not in columns
    assert conn.execute("SELECT oauth_state FROM google_connects").fetchone()[0] == "nonce-1"
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(google_connects)")}
    assert "google_connects_oauth_state" in indexes and "google_connects_state" not in indexes
    migrations._module(rename).up(conn)
    google_connect.ensure_schema(conn)


def test_a_stale_needs_onboarding_row_maps_to_needs_setup_and_bot_status_keeps_its_states(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db", isolation_level=None)
    conn.executescript("""
        CREATE TABLE bot_config(bot TEXT PRIMARY KEY, config TEXT, onboarding_state TEXT);
        CREATE TABLE bot_status(bot TEXT PRIMARY KEY, state TEXT, focus TEXT);
        INSERT INTO bot_config VALUES('starter','{}','needs_onboarding'), ('ready','{}','onboarded');
        INSERT INTO bot_status VALUES('starter','idle',''), ('stuck','limited','');
    """)
    _upgrade_as_migrate_runs_it(conn)
    assert conn.execute("SELECT bot,onboarding_state FROM bot_config ORDER BY bot").fetchall() == [
        ("ready", "onboarded"), ("starter", "needs_setup")]
    assert conn.execute("SELECT bot,state FROM bot_status ORDER BY bot").fetchall() == [
        ("starter", "idle"), ("stuck", "limited")]
    with pytest.raises(sqlite3.IntegrityError, match="status_values_state"):
        conn.execute("UPDATE bot_status SET state='active' WHERE bot='stuck'")
