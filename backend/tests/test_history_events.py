"""One history table (backend/migrations/0064_history_events.py): an existing file's five change logs move into
`history_events` with every value kept, the old tables go, a booted file stays as it is, rows other than a task's are
append-only, a market subject kind the table does not know refuses the move, and the readers answer what the old
tables answered (test_the_readers_answer_what_the_old_tables_did)."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from backend import company, goals, market, task_delete
from backend import hubdb as H
from backend.config import Settings
from backend.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0064_history_events.py"
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
    spec = importlib.util.spec_from_file_location("history_events_0064", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fill(c):
    c.executemany("INSERT INTO task_events VALUES(?,?,?,?,?,?,?,?,?,?)", [
        ("t1", "task-1", "2026-01-01T00:00:01Z", "human:ana", "status", "open", "doing", "", "act-1", "assistant"),
        ("t2", "task-1", "2026-01-01T00:00:02Z", "bot:ops", "note", None, None, "started", None, None),
        # The old table allowed a NULL field and a NULL time: both stay NULL, so task_history() still answers null.
        ("t3", "task-1", None, None, None, None, None, None, None, None),
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
        ("t3", "task-1", None, None, None, None, None, None, None, None, None),
        ("shared", "task-2", None, None, None, None, None, None, None, None, None)]
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
        # 0065_home_merges is applied after 0064 too; the loader will not run 0064 behind it, so both come off.
        c.execute("DELETE FROM schema_migrations WHERE version IN ('0064_history_events', '0065_home_merges')")
    boot(path)
    with sqlite3.connect(path) as c:
        assert not set(SOURCES) & {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = dict(c.execute("SELECT subject_type, count(*) FROM history_events GROUP BY subject_type"))
        assert counts == {"task": 4, "goal": 3, "market_entity": 1, "market_edge": 1, "market_evidence": 1,
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


def test_the_readers_answer_what_the_old_tables_did(tmp_path):
    # Each golden answer is the old reader's own SQL (main's goals.py, company.py, market.py, hubdb.py and strategy.py),
    # run on the five old tables before the move; the new readers run on history_events after it.
    c = sqlite3.connect(tmp_path / "old.sqlite")
    c.row_factory = sqlite3.Row
    for statement in OLD:
        c.execute(statement)
    fill(c)
    c.execute("INSERT INTO company_events VALUES('c2','2026-01-04T00:00:01Z','human:ana','vision','Old','New')")
    c.execute("INSERT INTO strategy_events VALUES('s2','bet_tripped','bot:goal-manager','2026-01-06T00:00:00Z',"
              "'{\"bet_id\":\"b1\",\"n\":3}')")
    golden = {
        "task": [dict(r) for r in c.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY created_at", ("task-1",))],
        "goal": [dict(r) for r in c.execute("SELECT * FROM goal_events WHERE goal_id=? ORDER BY created_at", ("goal-1",))],
        "company": [dict(r) for r in c.execute(
            "SELECT id, created_at, actor, field, old, new FROM company_events ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (50,))],
        "market": [dict(r) for r in c.execute("SELECT * FROM market_events WHERE created_at>=? ORDER BY created_at, id",
                                              ("2026-01-01",))],
        "tripped": {r[0] for r in c.execute("SELECT json_extract(payload,'$.bet_id') FROM strategy_events WHERE kind=? AND "
                                            "json_extract(payload,'$.n')=?", ("bet_tripped", 3))},
    }
    assert len(golden["task"]) == 3 and len(golden["goal"]) == 2 and len(golden["market"]) == 4
    # The NULL field and time are in the golden answer as null, and the new reader must answer null too.
    assert [r for r in golden["task"] if r["id"] == "t3"][0]["field"] is None
    assert [r for r in golden["task"] if r["id"] == "t3"][0]["created_at"] is None
    assert golden["tripped"] == {"b1"}
    migration().up(c)
    assert [dict(r) for r in H.task_history(c, "task-1")] == golden["task"]
    assert [dict(r) for r in goals.history(c, "goal-1")] == golden["goal"]
    assert [dict(r) for r in company.history(c, 50)] == golden["company"]
    events = H._rows(c.execute(f"SELECT {market.EVENT_COLUMNS} FROM {market.EVENTS} AND created_at>=? "
                               "ORDER BY created_at, id", ("2026-01-01",)))
    assert events == golden["market"]
    # The strategy's bet check (backend/strategy.py) reads its tripped bets from the payload, now the row's data.
    assert {r[0] for r in c.execute("SELECT json_extract(data,'$.bet_id') FROM history_events WHERE subject_type='strategy' "
                                    "AND kind=? AND json_extract(data,'$.n')=?", ("bet_tripped", 3))} == golden["tripped"]


def test_task_rows_keep_their_rowids_so_a_persisted_verdict_key_still_matches(tmp_path):
    # The reopen was the 100th row ever written to task_events: the 99 rows before it were deleted, leaving gaps. A
    # verdict delivered before the move carries its cycle as that rowid ("100:verdict approve 06d18530"), and the
    # delivery dedups on that exact key, so the migrated history must still name the reopen as 100.
    c = sqlite3.connect(tmp_path / "old.sqlite")
    c.row_factory = sqlite3.Row
    c.execute(OLD[0])
    c.execute(OLD[1])
    c.executemany("INSERT INTO task_events(id, task_id, created_at, actor, field, old, new, note) VALUES(?,?,?,?,?,?,?,?)",
                  [(f"filler-{i}", "task-9", "2026-01-01T00:00:00Z", "bot:ops", "note", None, None, "")
                   for i in range(99)])
    c.execute("INSERT INTO task_events(id, task_id, created_at, actor, field, old, new, note) "
              "VALUES('reopen','task-1','2026-01-02T00:00:00Z','human:ana','status','done','doing','')")
    c.execute("DELETE FROM task_events WHERE task_id='task-9'")
    c.execute("INSERT INTO task_events(id, task_id, created_at, actor, field, old, new, note) "
              "VALUES('verdict','task-1','2026-01-02T00:00:01Z','bot:finance','verdict',NULL,'approve','')")
    c.execute("INSERT INTO goal_events(id, goal_id, created_at, actor, field, old, new, note) "
              "VALUES('g1','goal-1','2026-01-02T00:00:02Z','human:ana','status','on_track','at_risk','')")
    assert c.execute("SELECT rowid FROM task_events WHERE id='reopen'").fetchone()[0] == 100
    migration().up(c)
    assert c.execute("SELECT rowid FROM history_events WHERE id='reopen'").fetchone()[0] == 100
    assert c.execute("SELECT rowid FROM history_events WHERE id='verdict'").fetchone()[0] == 101
    assert c.execute("SELECT rowid FROM history_events WHERE id='g1'").fetchone()[0] == 102  # other sources come after
    row = {"id": "task-1"}
    assert H._subtask_result_cycle(c, row) == "100:"
    assert H._subtask_result_key(c, row, "Checked it. VERDICT: approve PR #1549 head 06d18530") == "100:verdict approve 06d18530"
    assert H._check_cycle_start(c, {"id": "task-1", "verdict": "approve"}) == {"at": "2026-01-02T00:00:00Z", "current": True}
