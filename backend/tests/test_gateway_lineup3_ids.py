"""Gateway lineup 3's ids: every old id maps to this lineup's once (backend/gateway_tiers.py `new_id`), and the
migration rewrites the ids a database keeps as configuration, once (backend/migrations/*_gateway_lineup3_ids.py)."""

import importlib
import json
import sqlite3

import pytest

from backend import gateway_tiers as T

MIGRATION = importlib.import_module("backend.migrations.20261106173719_gateway_lineup3_ids")

OLD_TO_NEW = (
    ("tico-sales-medium-fast-1.0", "tico-medium-fast-1.0"),
    ("tico-general-medium-1.0", "tico-medium-1.0"),
    ("tico-general-low", "tico-low"),
    ("tico-engineering-high-1.0", "tico-high-1.0"),
    ("tico-data-low-fast", "tico-low-fast"),
    ("tico-operations-high-fast", "tico-high-fast"),
    ("tico-legal-medium-1.1", "tico-medium-1.1"),
    ("TICO-FINANCE-LOW-1.0", "tico-low-1.0"),
    ("tico-cheap-1.1", "tico-low-1.0"),
    ("tico-cheap-1.0", "tico-low-1.0"),
    ("tico-mid-1.0", "tico-medium-1.0"),
    ("tico-power-1.0", "tico-high-1.0"),
    ("tico-decide-1.0", "tico-decide-low-1.0"),
    ("tico-classify-1.0", "tico-decide-low"),
    ("tico-classify-low", "tico-decide-low"),
    ("tico-classify-low-1.0", "tico-decide-low"),
)
UNCHANGED = (
    "tico-meeting",
    "tico-meeting-1.0",
    "tico-decide-low",
    "tico-decide-low-1.1",
    "tico-xhigh-fast-1.0",
    "claude-opus-5-5",
    "tico-sales-huge-1.0",
    "tico-site",
    "",
)


@pytest.fixture(autouse=True)
def fresh_catalog():
    T.reset()
    yield
    T.reset()


@pytest.mark.parametrize(("old", "new"), OLD_TO_NEW)
def test_every_old_id_shape_maps_to_this_lineups_id_and_the_new_id_to_itself(old, new):
    assert T.new_id(old) == new and T.new_id(new) == new
    # The migration's frozen copy maps it the same way.
    assert MIGRATION.new_id(old) == new and MIGRATION.new_id(new) == new


def test_every_id_of_this_lineup_and_any_other_value_maps_to_itself():
    ids = [row["id"] for kind in ("models", "decisions") for row in T.SNAPSHOT[kind]]
    ids += [v["id"] for kind in ("models", "decisions") for row in T.SNAPSHOT[kind] for v in row["versions"]]
    for value in [*ids, *UNCHANGED]:
        assert T.new_id(value) == value and MIGRATION.new_id(value) == value, value
    assert T.new_id(None) is None
    # The migration carries a copy (a migration never calls app code); the two tables stay the same.
    assert MIGRATION.ALIASES == T.ALIASES and MIGRATION.DEPARTMENTS == T.OLD_DEPARTMENTS


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE bot_settings(bot_id TEXT PRIMARY KEY, model TEXT, fallback TEXT, extra TEXT);
        CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE task_run_models(task_id TEXT, model TEXT);
        CREATE TABLE automations(id TEXT PRIMARY KEY, flow TEXT);
        """
    )
    conn.executemany(
        "INSERT INTO bot_settings VALUES (?,?,?,?)",
        [
            (
                "acme-seller",
                "tico-sales-medium-1.0",
                json.dumps({"runtime": "pi", "model": "tico-mid-1.0"}),
                json.dumps({"task_model": {"model": "tico-marketing-low-fast"}, "check_models": ["pi:tico-power-1.0"]}),
            ),
            ("acme-writer", "claude-opus-5-5", None, json.dumps({"note": "tico-site stays"})),
            ("acme-new", "tico-high-1.0", json.dumps({"model": "tico-meeting-1.0"}), "{}"),
        ],
    )
    conn.executemany(
        "INSERT INTO registry_metadata VALUES (?,?)",
        [
            (
                "providers",
                json.dumps({"default": {"model": "tico-general-medium-1.0"}, "meeting_model": "tico-meeting"}),
            ),
            ("plain", "tico-cheap-1.1"),
            ("people", json.dumps({"people": [{"id": "ana", "team": "sales"}]})),
        ],
    )
    conn.execute("INSERT INTO task_run_models VALUES ('t1', 'tico-classify-1.0')")
    conn.execute(
        "INSERT INTO automations VALUES ('a1', ?)",
        (json.dumps({"steps": [{"ask": {"model": "tico-decide-1.0"}}, {"run": ["tico-engineering-high", 3]}]}),),
    )
    return conn


def _dump(conn):
    return {
        table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        for table in ("bot_settings", "registry_metadata", "task_run_models", "automations")
    }


def test_the_migration_rewrites_every_stored_id_once_and_running_it_again_changes_nothing():
    conn = _db()
    untouched = _dump(conn)
    MIGRATION.up(conn)
    first = _dump(conn)
    bots = {row[0]: row for row in first["bot_settings"]}
    assert bots["acme-seller"][1] == "tico-medium-1.0"
    assert json.loads(bots["acme-seller"][2]) == {"runtime": "pi", "model": "tico-medium-1.0"}
    assert json.loads(bots["acme-seller"][3]) == {
        "task_model": {"model": "tico-low-fast"},
        "check_models": ["pi:tico-high-1.0"],
    }
    # Rows with no old id are left byte for byte, the meeting tier included.
    before = {row[0]: row for row in untouched["bot_settings"]}
    assert bots["acme-writer"] == before["acme-writer"] and bots["acme-new"] == before["acme-new"]
    meta = dict(first["registry_metadata"])
    assert json.loads(meta["providers"]) == {"default": {"model": "tico-medium-1.0"}, "meeting_model": "tico-meeting"}
    assert meta["plain"] == "tico-low-1.0" and meta["people"] == dict(untouched["registry_metadata"])["people"]
    assert first["task_run_models"] == [("t1", "tico-decide-low")]
    assert json.loads(first["automations"][0][1]) == {
        "steps": [{"ask": {"model": "tico-decide-low-1.0"}}, {"run": ["tico-high", 3]}]
    }
    MIGRATION.up(conn)
    assert _dump(conn) == first


def test_the_migration_skips_a_table_or_column_a_database_lacks():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE bot_settings(bot_id TEXT PRIMARY KEY, model TEXT)")
    conn.execute("INSERT INTO bot_settings VALUES ('acme-ops', 'tico-operations-low-1.0')")
    MIGRATION.up(conn)
    assert conn.execute("SELECT model FROM bot_settings").fetchone()[0] == "tico-low-1.0"
