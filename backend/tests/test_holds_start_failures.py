"""Migration 20261117104652_holds_start_failures, on a database an older server left: a run a claim held moves into an
open claim_error hold on the job, a run held for its login's quota reset is let go, each task a task.cant_start event
holds while Agent Ops's task is open gets an open cant_start hold, and the jobs columns go. Running it again changes
nothing, and its triggers end a run's holds with the run and a task's with Agent Ops's task."""

import importlib
import json

from backend.store import H
from backend.tests.helpers.api import post

MIGRATION = importlib.import_module("backend.migrations.20261117104652_holds_start_failures")
HELD = "2026-11-16T12:00:00.000000Z"
LATER = "2999-01-01T00:00:00.000000Z"
COLUMNS = (
    "id,kind,holdable_type,holdable_id,bot_id,reason,details,streak,until_at,upkeep_task_id,created_at,released_at,"
    "released_how"
)


def _old_columns(c):
    """jobs as an older server left it, and no triggers yet."""
    c.execute("DROP TRIGGER IF EXISTS holds_job_ends")
    c.execute("DROP TRIGGER IF EXISTS holds_cant_start_closes")
    c.execute("ALTER TABLE jobs ADD COLUMN hold_reason TEXT")
    c.execute("ALTER TABLE jobs ADD COLUMN hold_task_id TEXT")
    c.execute("ALTER TABLE jobs ADD COLUMN hold_streak INTEGER NOT NULL DEFAULT 0")


def _jobs(api, bot, n):
    for i in range(n):
        post(api, f"chat/{bot}", {"text": f"Run {i}, please."})
    with api.app.state.store.read() as c:
        return [r[0] for r in c.execute("SELECT id FROM jobs WHERE bot_id=? ORDER BY rowid", (bot,))]


def _event(c, id, action, target, detail, at=HELD):
    c.execute(
        "INSERT INTO events(id,created_at,actor,action,target,detail) VALUES(?,?,'keeper',?,?,?)",
        (id, at, action, target, json.dumps(detail)),
    )


def _task(c, title):
    return H.task_create(c, H.TICO_ACTOR, title, "Why.", "human:ana", lint=False)["id"]


def _rows(c):
    return {
        r["id"]: dict(r) for r in c.execute(f"SELECT {COLUMNS} FROM holds WHERE kind IN ('claim_error','cant_start')")
    }


def test_start_failures_move_into_holds_and_running_it_again_changes_nothing(api):
    claimed, quota, preset, ended = _jobs(api, "ops", 4)
    with api.app.state.store.transaction() as c:
        _old_columns(c)
        item, review, closed_review = _task(c, "Couldn't start"), _task(c, "Find why"), _task(c, "Old review")
        held_task, other_task = _task(c, "Review the PR"), _task(c, "Write the docs")
        H.task_close(c, H.TICO_ACTOR, closed_review, "Fixed.")
        c.execute(
            "UPDATE jobs SET held=1,hold_until=?,hold_reason=?,hold_task_id=?,hold_streak=3 WHERE id=?",
            (LATER, MIGRATION.CLAIM_REASON, item, claimed),
        )
        c.execute("UPDATE jobs SET held=1,hold_until=?,wake_class='quota-reset' WHERE id=?", (LATER, quota))
        c.execute("UPDATE jobs SET held=1,hold_until=?,wake_class='batch' WHERE id=?", (LATER, preset))
        c.execute(
            "UPDATE jobs SET state='completed',hold_reason=?,hold_streak=1 WHERE id=?", (MIGRATION.CLAIM_REASON, ended)
        )
        _event(c, "e1", "job.claim_failed", claimed, {"error": "KeyError"}, at="2026-11-16T11:00:00.000000Z")
        _event(c, "e2", "job.claim_failed", claimed, {"error": "RuntimeError"})
        # Two for one task and bot: the later one (same instant, later row) names the job.
        _event(c, "e3", "task.cant_start", held_task, {"bot": "ops", "review": review, "reason": "a", "job": "j1"})
        _event(c, "e4", "task.cant_start", held_task, {"bot": "ops", "review": review, "reason": "b", "job": "j2"})
        # Agent Ops's task about it closed already: nothing holds the task.
        _event(c, "e5", "task.cant_start", other_task, {"bot": "ops", "review": closed_review, "reason": "c"})

        MIGRATION.up(c)
        moved = _rows(c)
        MIGRATION.up(c)
        assert _rows(c) == moved

        assert set(moved) == {f"claim_error:job:{claimed}", f"cant_start:task:{held_task}:ops"}
        assert moved[f"claim_error:job:{claimed}"] == {
            "id": f"claim_error:job:{claimed}",
            "kind": "claim_error",
            "holdable_type": "job",
            "holdable_id": claimed,
            "bot_id": "ops",
            "reason": MIGRATION.CLAIM_REASON,
            "details": json.dumps({"error": "RuntimeError"}, separators=(",", ":")),
            "streak": 3,
            "until_at": LATER,
            "upkeep_task_id": item,
            "created_at": HELD,
            "released_at": None,
            "released_how": None,
        }
        task_hold = moved[f"cant_start:task:{held_task}:ops"]
        assert (task_hold["holdable_id"], task_hold["upkeep_task_id"], task_hold["reason"]) == (held_task, review, "b")
        assert json.loads(task_hold["details"]) == {"job": "j2"}

        columns = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
        assert not columns & {"hold_reason", "hold_task_id", "hold_streak"} and {"held", "hold_until"} <= columns
        held = dict(c.execute("SELECT id,held FROM jobs WHERE id IN (?,?,?)", (claimed, quota, preset)).fetchall())
        assert held == {claimed: 0, quota: 0, preset: 1}  # a wake preset's hold stays

        # The run ends: its hold ends with it. Agent Ops's task closes: the task's hold ends, as Agent Ops's doing.
        c.execute("UPDATE jobs SET state='canceled' WHERE id=?", (claimed,))
        H.task_close(c, H.TICO_ACTOR, review, "Fixed.")
        after = _rows(c)
    assert after[f"claim_error:job:{claimed}"]["released_how"] == "moot"
    assert after[f"cant_start:task:{held_task}:ops"]["released_how"] == "botops"


def test_a_held_task_that_closes_ends_its_cant_start_hold(api):
    with api.app.state.store.transaction() as c:
        review, held_task = _task(c, "Find why"), _task(c, "Review the PR")
        c.execute(
            "INSERT INTO holds(id,kind,holdable_type,holdable_id,bot_id,upkeep_task_id,created_by_id,created_at,"
            "updated_at) VALUES('x','cant_start','task',?,'ops',?,'keeper',?,?)",
            (held_task, review, HELD, HELD),
        )
        H.task_close(c, H.TICO_ACTOR, held_task, "Not needed.")
        row = c.execute("SELECT released_how FROM holds WHERE id='x'").fetchone()
        assert row[0] == "moot"
        assert c.execute("SELECT status FROM tasks WHERE id=?", (review,)).fetchone()[0] == "open"
