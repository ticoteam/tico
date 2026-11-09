"""Helpers that other tests share, moved out of test_pipeline_engine.py so they import no test file."""

from backend import hubdb as H
from backend import pipeline_engine as E
from backend.tests.helpers.pipeline_foundation import insert, step_id


BEN = "human:ben"


T0 = "2026-11-07T09:00:00.000000Z"


def requirement(c, board, stage, name, kind="approval", needs=None, reviewed_by="person", **extra):
    ident = H.new_id()
    values = {
        "id": ident,
        "pipeline_id": board["id"],
        "pipeline_stage_id": step_id(c, board["id"], stage),
        "name": name,
        "kind": kind,
        "needs": __import__("json").dumps(needs or {}),
        "reviewed_by": reviewed_by,
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(extra)
    insert(c, "stage_requirements", **values)
    return ident


def decide(c, task, req, verdict="met", judge="person", version=None, **extra):
    row = c.execute("SELECT work_revision FROM tasks WHERE id=?", (task,)).fetchone()
    stage = c.execute("SELECT pipeline_stage_id, version FROM stage_requirements WHERE id=?", (req,)).fetchone()
    insert(
        c,
        "stage_decisions",
        id=H.new_id(),
        task_id=task,
        pipeline_stage_id=stage[0],
        stage_requirement_id=req,
        requirement_version=version or stage[1],
        work_version=row[0],
        judge=judge,
        verdict=verdict,
        created_at=H.now(),
        **extra,
    )


def states(c, task):
    row = dict(c.execute("SELECT id, type_id, step_id, status FROM tasks WHERE id=?", (task,)).fetchone())
    return {d["name"]: d["state"] for d in E.dots_many(c, [row]).get(task, [])}
