"""Moving a task to another pipeline (backend/task_engine.py `refile`, docs/pipelines-v3.md "Move to another
pipeline"): the same task, with its number and label, re-filed into a stage of another pipeline as a normal entry.

Synthetic rows only (backend/tests/helpers/engine.py): a Dev pipeline (prefix D) whose Ideas stage is left by hand, and
a Backlog pipeline with a by-hand On ice, a bot-owned Next up with a limit of one, and a person's OK.
"""

import importlib
import sqlite3

import pytest

from backend import task_effects
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
DEV = (
    {"name": "Ideas", "by_hand": True},
    {"name": "Build", "owner": "bot:builder", "requirements": [{"kind": "owner_done"}]},
)
BACKLOG = (
    {"name": "On ice", "by_hand": True},
    {"name": "Next up", "owner": "bot:builder", "task_limit": 1, "requirements": [{"kind": "owner_done"}]},
)


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


@pytest.fixture
def boards(c):
    X.bot(c, "builder")
    X.bot(c, "helper")
    X.pipeline(c)
    dev = X.pipeline(c, "dev", "Dev", DEV)
    backlog = X.pipeline(c, "backlog", "Backlog", BACKLOG)
    c.execute("UPDATE pipelines SET prefix='D' WHERE id='dev'")
    c.execute("UPDATE pipelines SET prefix='B' WHERE id='backlog'")
    return {"dev": dev, "backlog": backlog}


def _published(monkeypatch):
    seen = []
    real = task_effects._publish

    def spy(conn, name, row, actor, change, revision):
        seen.append((name, change))
        return real(conn, name, row, actor, change, revision)

    monkeypatch.setattr(task_effects, "_publish", spy)
    return seen


def test_the_number_a_numbered_pipeline_gives_is_ticos_not_the_filers(c, boards):
    """The task's chat read "You numbered it #561": the history line had the filer as its actor. Tico gives it."""
    row = X.new_task(c, ANA, "Old Trello idea", "dev", assignee=ANA)
    event = H._one(c, "SELECT actor_id, new FROM history_events WHERE subject_id=? AND kind='number'", (row["id"],))
    assert (event["actor_id"], int(event["new"])) == (H.TICO_ACTOR, row["number"])


def test_a_moved_task_keeps_its_number_label_and_history_and_enters_the_new_stage(c, boards, monkeypatch):
    row = X.new_task(c, ANA, "Old Trello idea", "dev", assignee=ANA)
    number, label = row["number"], H.ticket_refs(c, [row["id"]])[row["id"]]["label"]
    assert label == f"D{number}"
    E.snooze(c, row["id"], ANA, "later", None)
    seen = _published(monkeypatch)
    moved = E.refile(c, row["id"], "Backlog", "On ice", ANA, "parked")
    assert (moved["pipeline_id"], moved["stage_id"], moved["status"]) == ("backlog", "backlog-on-ice", "open")
    assert moved["number"] == number and moved["version"] == row["version"] + 1
    assert H.ticket_refs(c, [row["id"]])[row["id"]]["label"] == f"D{number}", "the label is the task's, not the board's"
    assert moved["snooze"] is None, "a snooze sets a task aside in one stage"
    left = H._one(c, "SELECT * FROM visits WHERE id=?", (row["visit"]["id"],))
    assert (left["how"], left["why"], left["by_id"]) == ("moved", "parked", ANA)
    assert moved["visit"]["entered_how"] == "moved" and moved["visit"]["stage_id"] == "backlog-on-ice"
    event = H._one(
        c, "SELECT * FROM history_events WHERE subject_id=? AND kind='move' ORDER BY rowid DESC LIMIT 1", (row["id"],)
    )
    data = H._json(event["data"], {})
    assert (event["old"], event["new"], event["note"]) == ("dev-ideas", "backlog-on-ice", "parked")
    assert (data["move"], data["from_pipeline"], data["to_pipeline"]) == ("moved", "dev", "backlog")
    names = [name for name, _ in seen]
    assert names == ["task.step_changed", "task.moved"]
    change = dict(seen)["task.moved"]
    assert (change["from_stage"], change["to_stage"], change["by"]) == ("dev-ideas", "backlog-on-ice", ANA)
    assert X.wakes(c, row["id"]) == [], "a by-hand stage wakes nobody"
    assert X.guard_closed(c)


def test_a_bot_owned_work_stage_wakes_its_bot_once_and_a_full_one_refuses(c, boards):
    first = X.new_task(c, ANA, "Fix the export", "dev", assignee=ANA)
    second = X.new_task(c, ANA, "Fix the import", "dev", assignee=ANA)
    moved = E.refile(c, first["id"], "backlog", "Next up", ANA)
    assert moved["owner_id"] == "bot:builder", "the new stage's owner rule sets the owner"
    assert [who for _, who in X.wakes(c, first["id"])] == ["bot:builder"]
    with pytest.raises(H.Refused, match="task_stage_full"):
        E.refile(c, second["id"], "backlog", "Next up", ANA)
    assert E.task(c, second["id"])["pipeline_id"] == "dev"


def test_a_bot_says_why_and_never_lands_a_task_past_a_persons_ok(c, boards):
    X.pipeline(
        c,
        "legal",
        "Legal",
        (
            {"name": "Sign", "requirements": [{"kind": "ok", "person": ANA, "who_oks": "people_only"}]},
            {"name": "File", "owner": "bot:builder", "requirements": [{"kind": "owner_done"}]},
        ),
    )
    row = X.new_task(c, ANA, "File the lease", "dev", assignee=ANA)
    with pytest.raises(H.Refused, match="task_why"):
        E.refile(c, row["id"], "backlog", None, "bot:helper", seen_version=row["version"])
    with pytest.raises(H.Refused, match="task_needs_person"):
        E.refile(c, row["id"], "legal", "File", "bot:helper", "It is a lease", seen_version=row["version"])
    moved = E.refile(c, row["id"], "backlog", None, "bot:helper", "Not this quarter", seen_version=row["version"])
    assert moved["stage_id"] == "backlog-on-ice", "no stage named: the pipeline's first"


def test_a_running_turn_holds_the_move_but_not_the_bots_own(c, boards):
    row = X.new_task(c, ANA, "Fix the export", "dev")
    built = E.move(c, row["id"], "Build", ANA)
    X.ran(c, row["id"], "bot:builder", state="running", at="2999-01-01T00:00:00.000000Z")
    with pytest.raises(H.Refused, match="task_turn_running"):
        E.refile(c, row["id"], "backlog", None, ANA)
    moved = E.refile(c, row["id"], "backlog", None, "bot:builder", "Parked", seen_version=built["version"])
    assert moved["pipeline_id"] == "backlog"


def test_done_and_closed_tasks_land_in_the_new_pipelines_done_and_closed(c, boards):
    done = X.new_task(c, ANA, "Shipped the export", "dev", assignee=ANA)
    E.move(c, done["id"], E.DONE, ANA, "shipped")
    closed = X.new_task(c, ANA, "Never mind", "dev", assignee=ANA)
    E.move(c, closed["id"], E.CLOSED, ANA, "dropped")
    a = E.refile(c, done["id"], "backlog", "Next up", ANA)
    b = E.refile(c, closed["id"], "backlog", None, ANA)
    assert (a["status"], a["stage_id"]) == ("done", "backlog-done"), "a move never reopens a task"
    assert (b["status"], b["stage_id"]) == ("closed", "backlog-closed")
    assert X.wakes(c, done["id"], "bot:builder") == []


def test_open_asks_end_with_the_old_visit_and_sub_tasks_stay_open(c, boards):
    row = X.new_task(c, ANA, "Plan the launch", "dev", assignee=ANA)
    sub = X.new_task(c, ANA, "Book the venue", "general", assignee=ANA, parent=row["id"])
    asked = E.add_requirement(c, row["id"], ANA, "ok", "human:ben", "OK the plan")
    E.refile(c, row["id"], "backlog", None, ANA)
    gone = H._one(c, "SELECT withdrawn_at FROM requirements WHERE id=?", (asked["requirement_id"],))
    assert gone["withdrawn_at"], "the old stage's asks are withdrawn"
    assert E.task(c, sub["id"])["status"] == "open", "sub-tasks stay in their own pipelines"
    assert c.execute("SELECT 1 FROM links WHERE to_task_id=?", (sub["id"],)).fetchone(), "the link stays"


def test_the_hand_off_the_old_stage_filed_closes_as_after_back_and_a_sub_task_stays(c, boards):
    X.pipeline(c, "sales", "Sales", ({"name": "Contract", "on_enter": {"file_into": "general"}},))
    deal = X.new_task(c, ANA, "Acme deal", "sales", assignee=ANA)
    handoff = c.execute(
        "SELECT to_task_id FROM links WHERE from_task_id=? AND kind='handoff'", (deal["id"],)
    ).fetchone()
    sub = X.new_task(c, ANA, "Call Acme", "general", assignee=ANA, parent=deal["id"])
    E.refile(c, deal["id"], "backlog", None, ANA, "Not this quarter")
    assert handoff and E.task(c, handoff[0])["status"] == "closed", "what the stage did on entering is undone"
    assert E.task(c, sub["id"])["status"] == "open"


def test_a_task_made_without_a_prefix_keeps_no_label_after_it_moves(c, boards):
    c.execute("UPDATE pipelines SET prefix=NULL WHERE id='general'")  # a pipeline with no prefix, as General had
    row = X.new_task(c, ANA, "Untitled", "general", assignee=ANA)
    c.execute("UPDATE tasks SET prefix=NULL WHERE id=?", (row["id"],))  # made before prefixes were kept
    E.refile(c, row["id"], "backlog", None, ANA)
    assert H.ticket_refs(c, [row["id"]])[row["id"]]["label"] is None, "General has no prefix: not B<number> now"


def test_the_migration_lets_visits_end_moved_and_keeps_every_row_and_label():
    migration = importlib.import_module("backend.migrations.20261117015544_task_prefix_and_moved_visits")
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE pipelines (id TEXT PRIMARY KEY, prefix TEXT);"
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, pipeline_id TEXT, number INTEGER);"
        "CREATE TABLE visits (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, stage_id TEXT NOT NULL, "
        "entered_how TEXT NOT NULL CHECK (entered_how IN ('new', 'met', 'pushed_past', 'back', 'closed', 'reopened')), "
        "left_at TEXT, how TEXT CHECK (how IS NULL OR how IN ('met', 'pushed_past', 'back', 'closed', 'reopened')));"
        "CREATE INDEX visits_task ON visits(task_id);"
        "CREATE TABLE log (what TEXT);"
        "CREATE TRIGGER tasks_logged AFTER UPDATE ON tasks BEGIN INSERT INTO log VALUES ('changed'); END;"
        "INSERT INTO pipelines VALUES ('dev', 'D'), ('general', NULL);"
        "INSERT INTO tasks VALUES ('a', 'dev', 7), ('b', 'general', NULL), ('c', NULL, NULL);"
        "INSERT INTO visits VALUES ('v1', 'a', 's', 'new', 'now', 'met');"
    )
    for _ in range(2):  # again: nothing changes
        migration.up(conn)
    conn.execute("INSERT INTO visits VALUES ('v2', 'a', 's', 'moved', 'later', 'moved')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO visits VALUES ('v3', 'a', 's', 'jumped', NULL, NULL)")
    assert conn.execute("SELECT id, how FROM visits ORDER BY id").fetchall() == [("v1", "met"), ("v2", "moved")]
    assert conn.execute("SELECT id, prefix FROM tasks ORDER BY id").fetchall() == [("a", "D"), ("b", None), ("c", None)]
    indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='visits' AND sql IS NOT NULL"
    )
    assert indexes.fetchall() == [("visits_task",)]
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall() == [("tasks_logged",)]
    assert conn.execute("SELECT COUNT(*) FROM log").fetchone()[0] == 0, "the backfill moved nothing a screen shows"
