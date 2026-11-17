"""General numbers its tasks (docs/tasks.md, "Move to another pipeline" and "Delete"): its preset has the prefix G, a
pipeline that gets a prefix late numbers the tasks it holds oldest first, a restored task with no number gets one, and
the migration gives an existing General its letter and every old task a number.

Synthetic rows only (backend/tests/helpers/engine.py).
"""

import importlib
import sqlite3

import pytest

from backend import pipeline_settings as PS
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
MIGRATION = "backend.migrations.20261117213811_general_prefix_and_numbers"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        X.pipeline(conn)
        yield conn


def _unnumbered(c, *titles):
    """General tasks made while General had no prefix: no number and empty letters, as production holds them."""
    c.execute("UPDATE pipelines SET prefix=NULL WHERE id='general'")
    rows = [X.new_task(c, ANA, title) for title in titles]
    for i, row in enumerate(rows):
        c.execute(
            "UPDATE tasks SET number=NULL, prefix='', created_at=? WHERE id=?",
            (f"2026-01-0{i + 1}T00:00:00.000000Z", row["id"]),
        )
    return [row["id"] for row in rows]


def _numbers(c, ids):
    return [c.execute("SELECT number FROM tasks WHERE id=?", (i,)).fetchone()[0] for i in ids]


def test_a_new_general_task_has_the_next_number_and_a_g_label(c):
    assert PS.preset(c, "general")["prefix"] == "G"
    first = X.new_task(c, ANA, "Order paper")
    second = X.new_task(c, ANA, "Book the room")
    assert second["number"] == first["number"] + 1
    assert H.ticket_refs(c, [second["id"]])[second["id"]]["label"] == f"G{second['number']}"
    assert [t["id"] for t in H.tasks(c, q=f"#{second['number']}")] == [second["id"]]


def test_saving_a_prefix_numbers_old_tasks_oldest_first_after_the_bin_and_keeps_given_numbers(c):
    ids = _unnumbered(c, "Oldest", "Middle", "Newest")
    kept = X.new_task(c, ANA, "Numbered already")
    c.execute("UPDATE tasks SET number=40 WHERE id=?", (kept["id"],))
    c.execute(
        "INSERT INTO task_trash (task_id, number, deleted_at, deleted_by, rows) VALUES ('gone', 70, ?, ?, '{}')",
        (H.now(), ANA),
    )
    X.settled(c)
    PS.save(c, ANA, dict(PS.read(c, "general"), prefix="G"))
    assert _numbers(c, ids) == [71, 72, 73]
    assert _numbers(c, [kept["id"]]) == [40], "a number already given never changes"
    assert H.ticket_refs(c, [ids[0]])[ids[0]]["label"] == "G71"
    notes = c.execute("SELECT note FROM history_events WHERE subject_id=? AND kind='number'", (ids[0],)).fetchall()
    assert [n[0] for n in notes] == ["Numbered when General got the prefix G"]
    assert all(X.wakes(c, i) == [] for i in ids), "numbering wakes nobody"
    PS.save(c, ANA, dict(PS.read(c, "general"), name="General"))
    assert _numbers(c, ids) == [71, 72, 73], "saving again numbers nothing twice"


def test_a_restored_task_with_no_number_gets_the_next_one(c):
    from backend import task_delete

    row = X.new_task(c, ANA, "Bring back")
    c.execute("UPDATE tasks SET number=NULL, prefix='' WHERE id=?", (row["id"],))
    task_delete.delete_tasks(c, [row["id"]], apply=True, actor=ANA)
    top = c.execute("SELECT MAX(number) FROM tasks").fetchone()[0] or 0
    assert task_delete.restore_tasks(c, [row["id"]], ANA)["restored"]
    assert _numbers(c, [row["id"]]) == [top + 1]
    assert E.task(c, row["id"])["prefix"] == "G"


def _old_install():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE pipelines (id TEXT PRIMARY KEY, name TEXT, prefix TEXT, preset TEXT, retired_at TEXT);"
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, pipeline_id TEXT, number INTEGER UNIQUE, prefix TEXT, created_at TEXT);"
        "CREATE TABLE task_trash (task_id TEXT PRIMARY KEY, number INTEGER);"
        "CREATE TABLE history_events (id TEXT PRIMARY KEY, subject_type TEXT, subject_id TEXT, actor_id TEXT, kind TEXT,"
        " old TEXT, new TEXT, note TEXT, action_id TEXT, via TEXT, data TEXT, created_at TEXT);"
        "INSERT INTO pipelines VALUES ('general', 'General', NULL, 'general', NULL), ('product', 'Product', 'P', NULL, NULL);"
        "INSERT INTO tasks VALUES ('b', 'general', NULL, '', '2026-02-01'), ('a', 'general', NULL, '', '2026-01-01'),"
        " ('p', 'product', 5, 'P', '2026-01-15'), ('moved', 'general', 3, 'P', '2026-01-10');"
        "INSERT INTO task_trash VALUES ('x', 9);"
    )
    return conn


def test_the_migration_gives_general_g_and_numbers_its_old_tasks_once():
    migration = importlib.import_module(MIGRATION)
    conn = _old_install()
    migration.up(conn)
    assert conn.execute("SELECT prefix FROM pipelines WHERE id='general'").fetchone()[0] == "G"
    rows = dict(conn.execute("SELECT id, number || COALESCE(prefix, '') FROM tasks").fetchall())
    assert rows == {"a": "10G", "b": "11G", "p": "5P", "moved": "3P"}
    events = conn.execute("SELECT subject_id, actor_id, new, note FROM history_events ORDER BY new").fetchall()
    assert events == [
        ("a", "keeper", "10", "Numbered when General got the prefix G"),
        ("b", "keeper", "11", "Numbered when General got the prefix G"),
    ]
    migration.up(conn)
    assert conn.execute("SELECT COUNT(*) FROM history_events").fetchone()[0] == 2, "running it again changes nothing"


def test_the_migration_takes_the_first_free_letter_when_g_is_used():
    migration = importlib.import_module(MIGRATION)
    conn = _old_install()
    conn.execute("INSERT INTO pipelines VALUES ('growth', 'Growth', 'G', NULL, NULL), ('ads', 'Ads', 'A', NULL, NULL)")
    migration.up(conn)
    assert conn.execute("SELECT prefix FROM pipelines WHERE id='general'").fetchone()[0] == "B"


def test_the_migration_leaves_a_general_that_has_a_prefix():
    migration = importlib.import_module(MIGRATION)
    conn = _old_install()
    conn.execute("UPDATE pipelines SET prefix='T' WHERE id='general'")
    migration.up(conn)
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE number IS NULL").fetchone()[0] == 2
