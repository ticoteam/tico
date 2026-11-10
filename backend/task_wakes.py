# pyright: strict
"""A bot's wake about a task on the engine (docs/pipelines-v3.md, "Effects travel with the move").

A wake is a row in `messages` with a `wake_key`: `<task id>:<visit id>:<level>:<try>`. The key is unique, so writing the
same wake twice is one wake. It is written in the same transaction as the move that causes it. The body says what is
missing and the five ways a run may end.
"""

from typing import TYPE_CHECKING, Any

from . import run_triggers
from . import task_requirements as R

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
# run_triggers is untyped: its functions are Any here.
RT: Any = run_triggers

ENDINGS = (
    "Leave the task in one of five states before you stop:\n"
    "1. Done with the stage: what is missing is met, or you moved the task.\n"
    "2. Asking a person, on the task.\n"
    "3. Waiting on a task you filed.\n"
    "4. Snoozed, with a reason, to a date where there is one.\n"
    "5. Closed, with a reason.\n"
    "Anything else counts as stopped early and you are woken again."
)


def _h() -> Any:
    from . import hubdb

    return hubdb


def key(task_id: str, visit_id: str, level: str = "owner", try_n: int = 0) -> str:
    return f"{task_id}:{visit_id}:{level}:{try_n}"


def body(conn: "sqlite3.Connection", task: Row, lead: str = "") -> str:
    """What a bot reads when it is woken for a task: the stage, what is missing, and how its run may end."""
    stage: Row = task.get("stage") or {}
    pipeline: Row = task.get("pipeline") or {}
    lines = [lead.strip()] if lead.strip() else []
    lines.append(f"{task.get('title') or 'A task'} is in {stage.get('name', '?')} ({pipeline.get('name', '?')}).")
    missing = [s for s in R.standings(conn, task) if not s.met]
    if missing:
        lines.append("Missing to leave this stage:")
        for s in missing:
            waits = f" Waiting on {s.on}." if s.state == R.WAITING and s.on else ""
            lines.append(f"- {s.name}: {s.line}.{waits}" if s.line and s.line != s.name else f"- {s.name}.{waits}")
    lines.append(f"Version {task.get('version')}. Send it with every command on this task.")
    lines.append(ENDINGS)
    return "\n".join(lines)


def add(
    conn: "sqlite3.Connection",
    task: Row,
    target: str | None,
    visit: Row,
    text: str,
    *,
    level: str = "owner",
    try_n: int = 0,
    wake_key: str | None = None,
    quiet: bool = False,
) -> str | None:
    """Wake `target` about `task`, once per key. Returns the message id, or None when this wake was already written or
    there is nobody to tell."""
    H = _h()
    if not target:
        return None
    wake_key = wake_key or key(task["id"], visit["id"], level, try_n)
    if conn.execute("SELECT 1 FROM messages WHERE wake_key=?", (wake_key,)).fetchone():
        return None
    if not H._actor_exists(conn, target) or not H.task_private_readable(conn, target, task):
        return None
    refs: Row = {"task": task["id"], "wake": "task_turn", "visit": visit["id"], "level": level, "try": try_n}
    refs["version"] = task.get("version")
    if quiet:
        refs["quiet"] = True
    message = H._write_message(
        conn,
        H.KEEPER,
        target,
        text,
        H.conversation(conn, task["conversation_id"]),
        "notice",
        RT.stamp(refs, "task_turn"),
        None,
        None,
    )
    if not message:
        return None
    conn.execute("UPDATE messages SET wake_key=? WHERE id=?", (wake_key, message["id"]))
    return message["id"]


# Why a bot that exists cannot run, by its state. The never-stuck check reads this before it wakes anyone, and
# `waiting_on` shows the task as Paused with the cause.
STATE_CAUSES = {"paused": "paused", "quarantined": "quarantined", "planned": "not started yet"}


def cannot_run(conn: "sqlite3.Connection", actor: str | None) -> str | None:
    """Why this bot cannot run now, in a few words, or None when it can. A paused bot's tasks say Paused and carry on
    by themselves when it can run again; nobody is woken meanwhile."""
    H = _h()
    if not H.is_bot(actor):
        return None
    row = conn.execute("SELECT state FROM bots WHERE id=?", (H.actor_id(actor),)).fetchone()
    if not row:
        return "removed"
    state = row[0] or "active"
    if state != "active":
        return STATE_CAUSES.get(state, state)
    return None


def queued(conn: "sqlite3.Connection", task_id: str, actor: str | None) -> bool:
    """A wake for this task is waiting for this bot: queued or just claimed, held ones included."""
    H = _h()
    if not H.is_bot(actor):
        return False
    return bool(
        conn.execute(
            "SELECT 1 FROM jobs j JOIN messages m ON m.id = j.message_id "
            "WHERE j.bot_id=? AND j.state IN ('queued', 'leased') AND m.wake_key LIKE ? ESCAPE '\\' LIMIT 1",
            (H.actor_id(actor), _like(task_id)),
        ).fetchone()
    )


def drop(conn: "sqlite3.Connection", task_id: str, actor: str | None) -> int:
    """Cancel the wakes for this task still queued for `actor`: it no longer has the turn. Returns how many."""
    H = _h()
    if not H.is_bot(actor):
        return 0
    cursor = conn.execute(
        "UPDATE jobs SET state='canceled' WHERE state='queued' AND bot_id=? AND message_id IN "
        "(SELECT id FROM messages WHERE wake_key LIKE ? ESCAPE '\\')",
        (H.actor_id(actor), _like(task_id)),
    )
    return cursor.rowcount


def _like(task_id: str) -> str:
    escaped = str(task_id).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + ":%"
