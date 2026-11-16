"""The status and state lists (backend/status_values.py): refused outside the list, one spelling per meaning, and a
start that names a stored value it cannot enforce rather than guessing."""

import sqlite3

import pytest

from backend import status_values as SV
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
OVERRIDES = {("fin_payments", "method"): "ach", ("fin_cards", "kind"): "standing"}


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


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("jobs", "state", "canceled"),
        ("webhook_sends", "state", "canceled"),
        ("fin_payments", "status", "canceled"),
        ("fin_transfers", "status", "canceled"),
        ("tasks", "status", "open"),
        ("attempts", "state", "input"),
        ("automation_runs", "status", "held"),
        ("bots", "state", "archived"),
        ("goals", "status", "gray"),
        ("bot_status", "state", "waiting_bot"),
        ("bot_config", "onboarding_state", "needs_setup"),
    ],
)
def test_a_listed_value_is_accepted(db, table, column, value):
    with sqlite3.connect(db, isolation_level=None) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        values = _required(conn, table, column)
        values[column] = value
        conn.execute(
            f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
            tuple(values.values()),
        )


def test_the_cancelled_spelling_is_refused_on_a_fresh_file(db):
    with sqlite3.connect(db, isolation_level=None) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        with pytest.raises(sqlite3.IntegrityError, match="jobs.state: invalid value"):
            conn.execute(
                "INSERT INTO jobs(id,message_id,bot_id,state,created_at) VALUES('j1','m1','b1','cancelled','t')"
            )


# A start on small hand-made tables, holding values a list does not have.


def _old_file(path):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE jobs(id TEXT PRIMARY KEY, message_id TEXT NOT NULL, bot_id TEXT NOT NULL,
                          state TEXT NOT NULL DEFAULT 'queued', created_at TEXT NOT NULL);
        CREATE INDEX jobs_state ON jobs(state);
    """)
    return conn


def test_a_check_column_with_an_unknown_value_keeps_its_rows_and_gets_no_check(tmp_path):
    conn = _old_file(tmp_path / "old.db")
    conn.executescript("""
        CREATE TABLE bots(slug TEXT PRIMARY KEY, state TEXT);
        INSERT INTO bots VALUES('a','active'),('b','hibernating');
    """)
    conn.execute("PRAGMA foreign_keys=OFF")
    SV.enforce(conn)
    assert conn.execute("SELECT slug,state FROM bots ORDER BY slug").fetchall() == [
        ("a", "active"),
        ("b", "hibernating"),
    ]
    assert "status_values_state" not in conn.execute("SELECT sql FROM sqlite_master WHERE name='bots'").fetchone()[0]
    # Once the row is fixed, the next enforce (every start) adds the CHECK.
    conn.execute("UPDATE bots SET state='paused' WHERE slug='b'")
    conn.execute("PRAGMA foreign_keys=OFF")
    SV.enforce(conn)
    assert "status_values_state" in conn.execute("SELECT sql FROM sqlite_master WHERE name='bots'").fetchone()[0]


def test_a_later_start_logs_a_check_column_it_cannot_enforce(tmp_path, caplog, monkeypatch):
    """A list that drops a value still stored: the start keeps the rows and the old CHECK (which still lets the dropped
    value in), and says so in the log every time, so a list that is not enforced is never silent."""
    conn = _old_file(tmp_path / "old.db")
    conn.executescript("""
        CREATE TABLE bots(slug TEXT PRIMARY KEY, state TEXT);
        INSERT INTO bots VALUES('a','active'),('b','archived');
    """)
    conn.execute("PRAGMA foreign_keys=OFF")
    SV.enforce(conn)
    installed = conn.execute("SELECT sql FROM sqlite_master WHERE name='bots'").fetchone()[0]
    assert "'archived'" in installed
    narrower = SV.Column(
        "bots", "state", ("active", "paused", "planned", "quarantined"), nullable=True, enforce="check"
    )
    monkeypatch.setattr(SV, "COLUMNS", tuple(narrower if s.table == "bots" else s for s in SV.COLUMNS))
    with caplog.at_level("WARNING", logger="backend.status_values"):
        SV.enforce(conn)
    assert "bots.state = 'archived' (1 rows)" in caplog.text
    assert conn.execute("SELECT sql FROM sqlite_master WHERE name='bots'").fetchone()[0] == installed
    conn.execute("INSERT INTO bots VALUES('c','archived')")  # the old list still holds


def test_the_value_table_in_the_docs_follows_the_lists():
    """docs/status-and-state.md's value table and each column's bullets match COLUMNS (scripts/build_status_docs.py)."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    done = subprocess.run(
        [sys.executable, str(root / "scripts/build_status_docs.py"), "--check"], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr
