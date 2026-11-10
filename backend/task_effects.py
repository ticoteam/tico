# pyright: strict
"""What a move and a reassignment do beyond the task's own row (docs/dev/pipelines-v3-cutover.md, "What a move still
does").

The engine calls `moved` at the end of every move and `reassigned` at the end of every reassignment, inside the
caller's transaction. Nothing outside the engine calls them, so no caller can skip one: a routine's day is marked, the
webhooks and automations hear, work on a closed task stops, whoever moves the task.

The engine writes the history row, cancels and writes the wakes, closes sub-tasks and tells the task that filed this
one. Those are not here.

Nothing here tells a person: no chat, email or push about a task. A person reads Needs you.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from . import hubdb_core

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
_core: Any = hubdb_core
SYSTEM: str = _core.KEEPER

# Whose act made the engine move a task: the owner who said the work is done, the person who gave the last OK. The
# engine moves as SYSTEM; what the move causes is theirs (a routine's day counts as done by its owner, not by nobody).
_CAUSE: ContextVar[str | None] = ContextVar("task_effects_cause", default=None)


def _h() -> Any:
    from . import hubdb

    return hubdb


@contextmanager
def because(actor: str) -> Generator[None]:
    """While this is open, a move the engine makes by itself is `actor`'s for what it causes."""
    token = _CAUSE.set(str(actor))
    try:
        yield
    finally:
        _CAUSE.reset(token)


def cause(by: str) -> str:
    """Who a move is attributed to: whoever made it, or for the engine's own move whoever caused it."""
    return (_CAUSE.get() or by) if by == SYSTEM else by


def _event_name(old: str, new: str) -> str | None:
    if new in ("done", "closed"):
        return f"task.{new}"
    if old in ("done", "closed") and new == "open":
        return "task.reopened"
    return None


def _publish(conn: "sqlite3.Connection", name: str, row: Row, actor: str, change: Row, revision: str | None) -> None:
    """One event for the task trigger rules, the outgoing webhooks and the automations."""
    H = _h()
    from . import task_trigger_rules

    triggers: Any = task_trigger_rules
    triggers.enqueue(conn, name, row["id"], actor, revision, field=change["field"], new=change["to"])
    facts: Row = {
        "id": row["id"],
        "title": row.get("title"),
        "status": row.get("status"),
        "pipeline": row.get("pipeline_id"),
        "stage": row.get("stage_id"),
        "owner_id": row.get("owner_id"),
        "requester_id": row.get("requester_id"),
        "actor": actor,
        "change": change,
    }
    H.hook(conn, name, row["id"], lambda: facts, actor, revision=revision)


def _tell_requester(conn: "sqlite3.Connection", after: Row, actor: str, why: str | None) -> None:
    """A bot that asked for a task hears once how it ended. Not when it ended it itself or owns it. Not when the engine
    already told it, on the task that filed this one, that what it filed there has ended (task_engine._ended, which
    runs before this): only a wake it wrote counts, so a parent that is closing, is snoozed, or has left the stage the
    task was filed from takes nothing away."""
    H = _h()
    requester = after.get("requester_id")
    if not H.is_bot(requester) or requester in (actor, after.get("owner_id")):
        return
    link = conn.execute("SELECT from_task_id, visit_id FROM links WHERE to_task_id=?", (after["id"],)).fetchone()
    if link and link[1]:
        # The key task_engine._ended gives its wake on the filing task.
        told = f"{link[0]}:{link[1]}:filed:{after['id']}"
        if conn.execute("SELECT 1 FROM messages WHERE wake_key=? AND recipient_id=?", (told, requester)).fetchone():
            return
    visit: Row = after.get("visit") or {}
    wake_key = f"{after['id']}:{visit.get('id')}:result"
    if conn.execute("SELECT 1 FROM messages WHERE wake_key=?", (wake_key,)).fetchone():
        return
    said = f"{'Finished' if after['status'] == 'done' else 'Closed'}: {after.get('title') or 'A task'}"
    if (why or "").strip():
        said += f"\n{why}"
    message = H._wake(conn, after, requester, said, {"news": "task_done"}, quiet_bots=True)
    if message:
        conn.execute("UPDATE messages SET wake_key=? WHERE id=?", (wake_key, message["id"]))


def moved(
    conn: "sqlite3.Connection",
    before: Row,
    after: Row,
    kind: str,
    by: str,
    why: str | None = None,
    revision: str | None = None,
) -> None:
    """A task moved: `kind` is forward, back, done, closed or reopen. `before` and `after` are `task_engine.task()`'s
    shape; `revision` is the id of the move's history row."""
    H = _h()
    actor = cause(by)
    old, new = str(before.get("status")), str(after.get("status"))
    if before.get("stage_id") != after.get("stage_id"):
        change: Row = {"field": "stage", "from": before.get("stage_id"), "to": after.get("stage_id"), "move": kind}
        _publish(conn, "task.step_changed", after, actor, change, revision)
    if before.get("owner_id") != after.get("owner_id"):
        # The next stage has another owner: what follows the owner follows it here as on a reassignment.
        _owner_changed(conn, before, after, actor, revision)
    if old == new:
        return
    from . import delivery, reconnects, rule_due, run_triggers

    reconnects_: Any = reconnects
    reconnects_.moved(conn, after["id"], "status", old, new)
    name = _event_name(old, new)
    if name:
        _publish(conn, name, after, actor, {"field": "status", "from": old, "to": new, "move": kind}, revision)
    if before.get("occurrence"):
        # A routine's task for a due date: finishing it marks that day, reopening takes the mark back.
        rules: Any = rule_due
        rules.on_task_status(conn, actor, before, old, new)
    if old == "done" and new == "open":
        # Someone sent a bot's finished result back: that is what became of the run that made it.
        runs: Any = run_triggers
        runs.turn_result(conn, actor, before, "overridden", H.event, new)
    if new in ("done", "closed"):
        deliveries: Any = delivery
        deliveries.task_closed(conn, before)
        _tell_requester(conn, after, actor, why)
    H.event(conn, actor, "task.close" if new == "closed" else "task.update", after["id"], {"status": new, "note": why})
    H.recount(conn, [after.get("owner_id"), after.get("requester_id"), before.get("owner_id")])
    if new == "closed":
        # Last, so a wake this move queued is stopped too. The run that makes the close is never stopped.
        H.stop_ended_work(conn, actor, after["id"], "closed")


def _owner_changed(conn: "sqlite3.Connection", before: Row, after: Row, actor: str, revision: str | None) -> None:
    """What follows a task's owner, whichever way it changed (a reassignment, or a move into a stage someone else
    owns): the new owner's private default, the sessions and worktree, the per-task model, the followers, the old
    owner's goal on the task, its place in the new owner's queue, and the `task.assigned` event."""
    H = _h()
    task_id = after["id"]
    old, new = before.get("owner_id"), after.get("owner_id")
    from . import reconnects, task_followers, task_run_models

    made_private = bool(new and H.private_tasks_default(conn, new) and not H.task_private(conn, before))
    if made_private:
        # A bot whose tasks are private by default: this one is now, and every task under it with it.
        H._made_private(conn, actor, task_id)
        for below in H.descendants(conn, task_id):
            if not H.task_private(conn, below):
                H._made_private(conn, actor, below["id"])
    # Rights lent on the task were lent by the owner it had.
    conn.execute("DELETE FROM task_delegations WHERE task_id=?", (task_id,))
    if H.is_bot(old):
        H._demote_sessions(conn, task_id, owner=new)
        for link in H.task_links(conn, task_id):
            if link["kind"] == "worktree":
                detail: Row = H._json(link.get("detail"), {}) or {}
                detail.setdefault("owner", old)
                conn.execute("UPDATE task_links SET detail=? WHERE id=?", (H._dump(detail), link["id"]))
        room = H.conversation(conn, before.get("conversation_id")) if before.get("conversation_id") else None
        if room:
            # The goal Tico set for the old owner ends with its ownership. A new bot owner's first run sets its own.
            from . import chat_goals

            goals: Any = chat_goals
            goals.clear_task_goal(conn, room)
    models: Any = task_run_models
    models.moved(conn, actor, task_id)
    followers: Any = task_followers
    followers.owner_moved(conn, before, old, new)
    conn.execute("UPDATE tasks SET rank=? WHERE id=?", (H._queue_end(conn, new) if new else None, task_id))
    reconnects_: Any = reconnects
    reconnects_.moved(conn, task_id, "owner", old, new)
    change: Row = {"field": "owner", "from": old, "to": new}
    _publish(conn, "task.assigned", after, actor, change, revision)


def reassigned(
    conn: "sqlite3.Connection", before: Row, after: Row, by: str, why: str | None = None, revision: str | None = None
) -> None:
    """A task was given to another owner in its stage. Someone handing a bot's work to someone else is what became of
    the run that made it; then everything that follows the owner (`_owner_changed`)."""
    H = _h()
    actor = cause(by)
    old, new = before.get("owner_id"), after.get("owner_id")
    if old == new:
        return
    from . import run_triggers

    runs: Any = run_triggers
    runs.turn_result(conn, actor, before, "overridden", H.event)
    _owner_changed(conn, before, after, actor, revision)
    H.event(conn, actor, "task.update", after["id"], {"owner": new, "note": why})
    H.recount(conn, [new, old, after.get("requester_id")])
