"""One history table (backend/migrations/0103_history_events.py): an existing file's five change logs move into
`history_events` with every value kept, the old tables go, a booted file stays as it is, rows other than a task's are
append-only, and a market subject kind the table does not know refuses the move."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from backend import task_delete
from backend.config import Settings
from backend.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0103_history_events.py"
OLD = (
    "CREATE TABLE task_events (id TEXT PRIMARY KEY, task_id TEXT, created_at TEXT, actor TEXT, field TEXT, old TEXT, "
    "new TEXT, note TEXT, action_id TEXT, via TEXT)",
    "CREATE TABLE goal_events (id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, created_at TEXT NOT NULL, actor TEXT NOT NULL, "
    "field TEXT NOT NULL, old TEXT, new TEXT, note TEXT NOT NULL DEFAULT '', status_by TEXT, status_source TEXT)",
    "CREATE TABLE market_events (id TEXT PRIMARY KEY, subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL, "
    "created_at TEXT NOT NULL, actor TEXT NOT NULL, field TEXT NOT NULL, old TEXT, new TEXT, insight_id TEXT, "
    "note TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE company_events (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, actor TEXT NOT NULL, field TEXT NOT NULL, "
    "old TEXT NOT NULL DEFAULT '', new TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE strategy_events (id TEXT PRIMARY KEY, kind TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL, "
    "payload TEXT NOT NULL DEFAULT '{}')",
)
SOURCES = ("task_events", "goal_events", "market_events", "company_events", "strategy_events")


def boot(path):
    Store(Settings(db_path=path, registry_dir=FIXTURES / "registry")).initialize(seed_market=False)


def migration():
    spec = importlib.util.spec_from_file_location("history_events_0103", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fill(c):
    c.executemany("INSERT INTO task_events VALUES(?,?,?,?,?,?,?,?,?,?)", [
        ("t1", "task-1", "2026-01-01T00:00:01Z", "human:ana", "status", "open", "doing", "", "act-1", "assistant"),
        ("t2", "task-1", "2026-01-01T00:00:02Z", "bot:ops", "note", None, None, "started", None, None),
        ("shared", "task-2", None, None, None, None, None, None, None, None)])
    c.executemany("INSERT INTO goal_events VALUES(?,?,?,?,?,?,?,?,?,?)", [
        ("g1", "goal-1", "2026-01-02T00:00:00Z", "human:ana", "status", "on_track", "at_risk", "slipped", "bot:coo",
         "check"),
        ("g2", "goal-1", "2026-01-02T00:00:01Z", "human:ana", "title", "Old", "New", "", None, None),
        ("shared", "goal-2", "2026-01-02T00:00:02Z", "human:ben", "note", None, None, "clash", None, None)])
    c.executemany("INSERT INTO market_events VALUES(?,?,?,?,?,?,?,?,?,?)", [
        ("m1", "entity", "acme", "2026-01-03T00:00:00Z", "bot:research", "name", None, "Acme", "ins-1", ""),
        ("m2", "edge", "e1", "2026-01-03T00:00:01Z", "bot:research", "kind", "rival", "partner", None, "why"),
        ("m3", "evidence", "v1", "2026-01-03T00:00:02Z", "bot:research", "page.read", None, None, None, ""),
        ("m4", "document", "d1", "2026-01-03T00:00:03Z", "bot:research", "page.changed", "a", "b", None, "")])
    c.execute("INSERT INTO company_events VALUES('c1','2026-01-04T00:00:00Z','human:ana','mission','','Ship')")
    c.execute("INSERT INTO strategy_events VALUES('s1','thesis.set','bot:goal-manager','2026-01-05T00:00:00Z',"
              "'{\"thesis\":\"Acme wins\"}')")


def rows(c, subject_type):
    return c.execute("SELECT id, subject_id, actor_id, kind, old, new, note, action_id, via, data, created_at "
                     "FROM history_events WHERE subject_type=? ORDER BY rowid", (subject_type,)).fetchall()


def test_the_five_logs_move_into_one_table_with_every_value_kept(tmp_path):
    c = sqlite3.connect(tmp_path / "old.sqlite")
    for statement in OLD:
        c.execute(statement)
    fill(c)
    migration().up(c)
    tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not set(SOURCES) & tables
    assert rows(c, "task") == [
        ("t1", "task-1", "human:ana", "status", "open", "doing", "", "act-1", "assistant", None, "2026-01-01T00:00:01Z"),
        ("t2", "task-1", "bot:ops", "note", None, None, "started", None, None, None, "2026-01-01T00:00:02Z"),
        ("shared", "task-2", None, "", None, None, None, None, None, None, "")]
    goal = rows(c, "goal")
    assert [row[0] for row in goal] == ["g1", "g2", "goal:shared"]  # the clashing id keeps its row, with its type
    assert json.loads(goal[0][9]) == {"status_by": "bot:coo", "status_source": "check"} and goal[1][9] is None
    assert goal[0][1:7] == ("goal-1", "human:ana", "status", "on_track", "at_risk", "slipped")
    assert [(row[0], row[3]) for row in rows(c, "market_entity")] == [("m1", "name")]
    assert json.loads(rows(c, "market_entity")[0][9]) == {"insight_id": "ins-1"}
    assert [row[0] for row in rows(c, "market_edge") + rows(c, "market_evidence") + rows(c, "market_document")] == [
        "m2", "m3", "m4"]
    assert rows(c, "company") == [("c1", "company", "human:ana", "mission", "", "Ship", None, None, None, None,
                                   "2026-01-04T00:00:00Z")]
    strategy = rows(c, "strategy")
    assert strategy[0][:4] == ("s1", "strategy", "bot:goal-manager", "thesis.set")
    assert json.loads(strategy[0][9]) == {"thesis": "Acme wins"}


def test_a_task_log_from_before_action_id_and_via_still_moves(tmp_path):
    c = sqlite3.connect(tmp_path / "old.sqlite")
    c.execute("CREATE TABLE task_events (id TEXT PRIMARY KEY, task_id TEXT, created_at TEXT, actor TEXT, field TEXT, "
              "old TEXT, new TEXT, note TEXT)")
    c.execute("INSERT INTO task_events VALUES('t1','task-1','2026-01-01T00:00:00Z','human:ana','owner','a','b','')")
    migration().up(c)
    assert rows(c, "task") == [("t1", "task-1", "human:ana", "owner", "a", "b", "", None, None, None,
                                "2026-01-01T00:00:00Z")]


def test_an_unknown_market_subject_kind_refuses_the_move(tmp_path):
    c = sqlite3.connect(tmp_path / "old.sqlite")
    c.execute(OLD[2])
    c.execute("INSERT INTO market_events VALUES('m1','planet','p1','t','bot:x','name',NULL,NULL,NULL,'')")
    with pytest.raises(RuntimeError, match="planet"):
        migration().up(c)


def test_an_existing_file_moves_on_boot_and_booting_again_changes_nothing(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    with sqlite3.connect(path) as c:
        for statement in OLD:
            c.execute(statement)
        fill(c)
        c.execute("DELETE FROM schema_migrations WHERE version>='0103' AND version NOT LIKE 'legacy_%'")
    boot(path)
    with sqlite3.connect(path) as c:
        assert not set(SOURCES) & {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = dict(c.execute("SELECT subject_type, count(*) FROM history_events GROUP BY subject_type"))
        assert counts == {"task": 3, "goal": 3, "market_entity": 1, "market_edge": 1, "market_evidence": 1,
                          "market_document": 1, "company": 1, "strategy": 1}
        before = c.execute("SELECT * FROM history_events ORDER BY rowid").fetchall()
    boot(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT * FROM history_events ORDER BY rowid").fetchall() == before


def test_only_a_tasks_rows_can_be_changed_or_removed(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    with sqlite3.connect(path) as c:
        c.executemany("INSERT INTO history_events(id, subject_type, subject_id, actor_id, kind, created_at) "
                      "VALUES(?,?,?,?,?,?)", [("h1", "task", "task-1", "bot:ops", "note", "t"),
                                              ("h2", "goal", "goal-1", "human:ana", "status", "t"),
                                              ("h3", "strategy", "strategy", "bot:x", "thesis.set", "t")])
        c.execute("UPDATE history_events SET note='closed' WHERE id='h1'")
        c.execute("DELETE FROM history_events WHERE id='h1'")
        for statement in ("UPDATE history_events SET note='x' WHERE id='h2'", "DELETE FROM history_events WHERE id='h3'"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                c.execute(statement)
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO history_events(id, subject_type, subject_id, kind, created_at) "
                      "VALUES('h4','planet','p','x','t')")


def test_a_trash_snapshot_saved_before_the_move_restores_its_task_history():
    old = {"task_events": [{"_rowid": 7, "id": "t1", "task_id": "task-1", "created_at": "2026-01-01T00:00:00Z",
                            "actor": "human:ana", "field": "status", "old": "open", "new": "done", "note": ""}],
           "task_links": [{"id": "l1"}]}
    moved = task_delete._history_rows(old)
    assert "task_events" not in moved and moved["task_links"] == [{"id": "l1"}]
    assert moved["history_events"] == [{"_rowid": 7, "id": "t1", "subject_type": "task", "subject_id": "task-1",
                                        "actor_id": "human:ana", "kind": "status", "old": "open", "new": "done",
                                        "note": "", "action_id": None, "via": None,
                                        "created_at": "2026-01-01T00:00:00Z"}]
