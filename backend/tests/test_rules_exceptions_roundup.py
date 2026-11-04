"""A saved rule on the retired "exceptions" event becomes a task_roundup rule (backend/migrations/0072_rules_exceptions_roundup.py),
so validation and loading never meet an event that no longer exists. Every other rule keeps its event."""
import importlib
import sqlite3

from backend import rules

MIGRATION = importlib.import_module("backend.migrations.0072_rules_exceptions_roundup")
NOW = "2026-11-01T00:00:00.000000Z"


def saved(conn, rule_id, event):
    conn.execute("INSERT INTO rules(id,scope_kind,event,then_outcome,created_by,created_at,updated_at) "
                 "VALUES(?,'team',?,'wake_bot','human:ana',?,?)", (rule_id, event, NOW, NOW))


def test_a_saved_exceptions_rule_moves_to_the_roundup_and_every_other_rule_keeps_its_event():
    conn = sqlite3.connect(":memory:")
    conn.executescript(rules.SCHEMA)
    saved(conn, "old", "exceptions")
    saved(conn, "kept", "assignment")
    saved(conn, "roundup", "task_roundup")
    MIGRATION.up(conn)
    events = dict(conn.execute("SELECT id, event FROM rules").fetchall())
    assert events == {"old": "task_roundup", "kept": "assignment", "roundup": "task_roundup"}
    assert all(event in rules.EVENTS for event in events.values()), "every saved rule names an event that exists"


def test_the_migration_does_nothing_on_a_database_without_rules():
    MIGRATION.up(sqlite3.connect(":memory:"))
