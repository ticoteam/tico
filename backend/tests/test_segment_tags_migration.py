"""The segment_tags migration (backend/migrations): goals and tasks gain one nullable segment each, and the rows an
install already has keep none. Running it again changes nothing. Examples use Acme."""

import importlib.util
import sqlite3
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "20261107005935_segment_tags.py"
_spec = importlib.util.spec_from_file_location("segment_tags", MIGRATION)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


def install():
    """Goals and tasks as an install from before segments has them, with a row of each."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE goals (id TEXT PRIMARY KEY, title TEXT NOT NULL, bet_id TEXT)")
    c.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, goal_id TEXT)")
    c.execute("INSERT INTO goals (id, title, bet_id) VALUES ('g1', 'Grow the screening book', 'bet-1')")
    c.execute("INSERT INTO tasks (id, title, goal_id) VALUES ('t1', 'Call Acme', 'g1')")
    return c


def columns(c, table):
    return {row[1]: row[2] for row in c.execute(f"PRAGMA table_info({table})")}


def indexes(c):
    return {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}


def test_existing_goals_and_tasks_gain_a_nullable_segment_and_keep_their_rows():
    c = install()
    migration.up(c)
    assert columns(c, "goals")["segment_id"] == "TEXT"
    assert columns(c, "tasks")["segment_id"] == "TEXT"
    assert c.execute("SELECT title, bet_id, segment_id FROM goals").fetchall() == [
        ("Grow the screening book", "bet-1", None)
    ]
    assert c.execute("SELECT title, goal_id, segment_id FROM tasks").fetchall() == [("Call Acme", "g1", None)]
    assert {"goals_segment", "tasks_segment"} <= indexes(c)


def test_running_it_again_changes_nothing_and_keeps_a_segment_already_set():
    c = install()
    migration.up(c)
    c.execute("UPDATE tasks SET segment_id='segment/screening' WHERE id='t1'")
    migration.up(c)
    assert c.execute("SELECT segment_id FROM tasks WHERE id='t1'").fetchone()[0] == "segment/screening"
    assert list(columns(c, "tasks")).count("segment_id") == 1
