# pyright: strict
"""A bot's wake about a task on the engine (docs/pipelines-v3.md, "Effects travel with the move").

A wake is a row in `messages` with a `wake_key`: `<task id>:<visit id>:<level>:<try>`. The key is unique, so writing the
same wake twice is one wake. It is written in the same transaction as the move that causes it. The body says what is
missing and the five ways a run may end.
"""

from typing import TYPE_CHECKING, Any, cast

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
    refs: Row | None = None,
    trigger: str | None = None,
    source: str | None = None,
) -> str | None:
    """Wake `target` about `task`, once per key. Returns the message id, or None when this wake was already written or
    there is nobody to tell. `refs` are a caller's own facts for the run it starts (an automation's run and step, the
    other work to reconcile with); they never replace the engine's. `trigger` and `source` say what the run is for,
    where the caller knows better than "work a task"."""
    H = _h()
    if not target:
        return None
    wake_key = wake_key or key(task["id"], visit["id"], level, try_n)
    if conn.execute("SELECT 1 FROM messages WHERE wake_key=?", (wake_key,)).fetchone():
        return None
    if not H._actor_exists(conn, target) or not H.task_private_readable(conn, target, task):
        return None
    own: Row = {"task": task["id"], "wake": "task_turn", "visit": visit["id"], "level": level, "try": try_n}
    own["version"] = task.get("version")
    if quiet:
        own["quiet"] = True
    stamped: Row = RT.stamp({**(refs or {}), **own}, trigger or "task_turn", source)
    message = H._write_message(
        conn,
        H.KEEPER,
        target,
        text,
        H.conversation(conn, task["conversation_id"]),
        "notice",
        stamped,
        None,
        None,
    )
    if not message:
        return None
    conn.execute("UPDATE messages SET wake_key=? WHERE id=?", (wake_key, message["id"]))
    return message["id"]


# Why a bot cannot run, as the rest of a sentence about it: "Builder has no computer". The never-stuck check reads this
# before it wakes anyone, and `waiting_on` shows the task as Paused with the cause. Every cause is a read of saved state:
# nothing here asks a computer. The three in COMPANY_WIDE stop every bot they touch and are whole sentences.
STATE_CAUSES = {"paused": "is paused", "quarantined": "is quarantined", "planned": "has not started yet"}
REMOVED = "was removed"
NO_COMPUTER = "has no computer"
RUNS_FAILING = "keeps failing its runs"
COMPANY_PAUSED = "The company is paused"
CLOUD_LOCKED = "Tico Cloud is locked"
OUT_OF_CREDIT = "Tico Cloud is out of credit"
COMPANY_WIDE = (COMPANY_PAUSED, CLOUD_LOCKED, OUT_OF_CREDIT)


def cause_line(conn: "sqlite3.Connection", actor: str | None, cause: str) -> str:
    """A cause from `cannot_run` as one sentence: "Builder has no computer", "Tico Cloud is locked"."""
    if cause in COMPANY_WIDE:
        return cause
    return f"{_h().actor_name(conn, actor)} {cause}"


def cannot_run(conn: "sqlite3.Connection", actor: str | None, seen: dict[str, str | None] | None = None) -> str | None:
    """Why this bot cannot run now, in a few words, or None when it can. A paused bot's tasks say Paused and carry on
    by themselves when it can run again; nobody is woken meanwhile. `seen` keeps each bot's answer for one pass."""
    H = _h()
    if not H.is_bot(actor):
        return None
    bot: str = H.actor_id(actor)
    if seen is not None and bot in seen:
        return seen[bot]
    cause = _cause(conn, bot)
    if seen is not None:
        seen[bot] = cause
    return cause


def _cause(conn: "sqlite3.Connection", bot: str) -> str | None:
    from . import bot_settings, cloud_access, company_pause, gateway_connection, harnesses, run_backoff

    row = conn.execute("SELECT state FROM bots WHERE id=?", (bot,)).fetchone()
    if not row:
        return REMOVED
    state = row[0] or "active"
    if state != "active":
        return STATE_CAUSES.get(state, f"is {state}")
    if company_pause.record(conn) is not None:
        return COMPANY_PAUSED
    # These modules are untyped: what they return is Any here.
    cloud: Any = cloud_access
    config: Row = cast("Any", bot_settings).read(conn, bot) or {}
    access = cloud.access(conn)
    if cloud.locked(conn, access):
        return CLOUD_LOCKED
    if cloud.frozen(conn, access) and cast("Any", gateway_connection).is_tico(config.get("model")):
        return OUT_OF_CREDIT  # a bot on its own subscription or keys still runs
    kinds: Any = harnesses
    if kinds.resolve_harness(config) not in kinds.EXTERNAL_HARNESSES:
        # A computer that is offline is not a cause: a cloud computer sleeps until work is queued for it.
        placed = conn.execute(
            "SELECT r.revoked_at FROM assignments a JOIN runners r ON r.id = a.runner_id WHERE a.bot=?", (bot,)
        ).fetchone()
        if not placed or placed[0]:
            return NO_COMPUTER
    held: Row = cast("Any", run_backoff).get(conn, bot) or {}
    if held.get("held_at"):
        return RUNS_FAILING  # held until a person releases it (backend/run_backoff.py)
    return None


def live_run(conn: "sqlite3.Connection", task: Row, actor: str | None, now: str | None = None) -> bool:
    """This bot has a run on this task whose computer still checks in. A run is live only while its lease is renewed
    (every 90 seconds at most): one that is held, cut off or lost is over."""
    H = _h()
    if not H.is_bot(actor):
        return False
    return bool(
        conn.execute(
            "SELECT 1 FROM jobs j JOIN attempts a ON a.id = j.attempt_id JOIN messages m ON m.id = j.message_id "
            "WHERE j.bot_id=? AND a.state IN ('leased', 'running') AND a.lease_until > ? "
            "AND (m.wake_key LIKE ? ESCAPE '\\' OR m.conversation_id = ?) LIMIT 1",
            (H.actor_id(actor), now or H.now(), _like(task["id"]), task.get("conversation_id") or ""),
        ).fetchone()
    )


def last_run(conn: "sqlite3.Connection", task: Row, actor: str | None) -> Row | None:
    """This bot's newest run on this task: its `state` and when it ended (`finished_at`). None when it never ran for
    it."""
    H = _h()
    if not H.is_bot(actor):
        return None
    found = conn.execute(
        "SELECT a.state, a.finished_at FROM attempts a JOIN jobs j ON j.id = a.job_id "
        "JOIN messages m ON m.id = j.message_id WHERE a.bot_id=? "
        "AND (m.wake_key LIKE ? ESCAPE '\\' OR m.conversation_id = ?) ORDER BY a.created_at DESC, a.id DESC LIMIT 1",
        (H.actor_id(actor), _like(task["id"]), task.get("conversation_id") or ""),
    ).fetchone()
    return {"state": found[0], "finished_at": found[1]} if found else None


def woken_since(conn: "sqlite3.Connection", task_id: str, actor: str | None, since: str) -> bool:
    """A wake about this task was written for `actor` at or after `since`."""
    return bool(
        conn.execute(
            "SELECT 1 FROM messages WHERE wake_key LIKE ? ESCAPE '\\' AND recipient_id=? AND created_at>=? LIMIT 1",
            (_like(task_id), actor, since),
        ).fetchone()
    )


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


def drop(conn: "sqlite3.Connection", task_id: str, keep: str | None = None) -> int:
    """Cancel every wake still queued for this task, whoever it is for: the turn they were about is over. With `keep`,
    that bot's wakes stay queued. A run that already took its wake is left alone. Returns how many."""
    H = _h()
    kept = H.actor_id(keep) if H.is_bot(keep) else None
    cursor = conn.execute(
        "UPDATE jobs SET state='canceled' WHERE state='queued' AND bot_id IS NOT ? AND message_id IN "
        "(SELECT id FROM messages WHERE wake_key LIKE ? ESCAPE '\\')",
        (kept, _like(task_id)),
    )
    return cursor.rowcount


def cancel(conn: "sqlite3.Connection", wake_key: str) -> int:
    """Cancel one wake by its key, while no run has taken it yet. Returns how many jobs it canceled."""
    cursor = conn.execute(
        "UPDATE jobs SET state='canceled' WHERE state='queued' AND message_id IN "
        "(SELECT id FROM messages WHERE wake_key=?)",
        (wake_key,),
    )
    return cursor.rowcount


def _like(task_id: str) -> str:
    escaped = str(task_id).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + ":%"
