"""What entering a stage does, and how leaving it backwards undoes that (docs/pipelines-v3.md, "Entering a stage").

A stage's on-enter action is nothing, "file a linked task into pipeline X", or "queue the PRs to merge". It happens once
per visit, in the move's transaction. Moving back or closing undoes it: tasks filed in that visit close with a note, and
PRs leave the merge queue.

Phase 1 has the undo for filed tasks. Filing into a pipeline arrives with Product, the PR queue with Engineering.
"""

import json


def _on_enter(stage):
    value = stage.get("on_enter") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    return value or {}


def enter(conn, task, by):
    """Run the on-enter action of the stage `task` just entered. `task` is `task_engine.task()`'s shape."""
    action = _on_enter(task.get("stage") or {})
    if not action:
        return
    raise NotImplementedError(f"on-enter action {sorted(action)} arrives with a later phase")


def filed_open(conn, visit_id):
    """Ids of the tasks filed during a visit that are still open, oldest first."""
    return [
        row[0]
        for row in conn.execute(
            "SELECT t.id FROM links l JOIN tasks t ON t.id = l.to_task_id "
            "WHERE l.visit_id=? AND t.status='open' ORDER BY l.created_at, l.id",
            (visit_id,),
        ).fetchall()
    ]


def undo(conn, task, by, note):
    """Undo what the task's open visit did on entering: close the tasks filed in it that are still open."""
    from . import task_engine

    visit = task.get("visit")
    if not visit:
        return
    for filed in filed_open(conn, visit["id"]):
        task_engine.move(conn, filed, task_engine.CLOSED, by, note, cascade=True)
