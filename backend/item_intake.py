# pyright: strict
"""Create-or-find intake, and an item closed outside (docs/dev/pipelines-v3.md "Intake").

    intake(conn, actor, item, *, title, updated_at, create, see, stage=None) -> {task_id, created, resumed, stale}
    primary_task(conn, source, item_type, key, connection_id) -> the task row or None
    closed_outside(conn, ...) / reopened_outside(conn, ...)

An outside item (connection, source, item type, key: backend/item_sources) is ready for work. `intake` finds the task
whose primary link is that item (`task_links.is_primary`; the partial unique index task_links_primary_item lets one
task hold it) or files one with that link, in the caller's one write transaction, so two intakes of one item never
file two tasks. A task found closed or done is reopened by Tico (the item is ready again outside: "ready" after a
withdrawal resumes the same task), whoever closed it. The caller must be allowed to use the connection (its tool,
api_tools.key_for), since the task's item is read through it from then on.

Stale events change nothing: an intake carries when the item last changed outside as it saw it (`updated_at`); one
older than what the last read of the item saw (`fields.updated`), or no newer than the last intake acted on
(`fields.intake_updated`, kept beside the metadata, item_metadata.note), answers `stale` and leaves the task as it is.

When a primary link's item turns closed outside (a read saw it, backend/item_sources/jira.py `save_issue`), the task's
owner is asked once to settle it or keep it: a person by a ❓ on the task's chat from the task's bot (else the bot
that filed it, else Agent Ops; with no bot at all, Tico's message on the chat), a bot by Tico's instructions on the
task, which wake it. The item turning open again takes the ❓ back. A secondary link closing asks nobody.
"""

from collections.abc import Callable
from types import ModuleType
from typing import TYPE_CHECKING, Any, TypedDict

from . import item_sources as IS
from . import item_metadata as IM
from . import hubdb_core

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human
CLOSED_REF = "outside_closed"  # the ❓ message's refs key: the item it asks about, item_sources.parse_ref's spelling


class Intake(TypedDict):
    task_id: str | None
    created: bool
    resumed: bool
    stale: bool


def _h() -> Any:
    from . import hubdb

    return hubdb


def _refuse(code: str, detail: str, status: int = 422) -> Exception:
    from .store import Problem

    problem: Any = Problem
    return problem(code, detail, status)


def item_ref(source: str, item_type: str, key: str, connection_id: str) -> str:
    return f"{source}:{item_type}:{key}@{connection_id}" if connection_id else f"{source}:{item_type}:{key}"


def primary_task(conn: "sqlite3.Connection", source: str, item_type: str, key: str, connection_id: str) -> Row | None:
    """The task holding the item's primary link, whatever its status, or None."""
    found = conn.execute(
        "SELECT task_id FROM task_links WHERE is_primary=1 AND source=? AND item_type=? AND item_key=? "
        "AND connection_id=?",
        (source, item_type, key, connection_id),
    ).fetchone()
    row: Row | None = _h().task(conn, str(found[0])) if found else None
    return row


def _stale(metadata: Row | None, updated_ms: int | None) -> bool:
    if metadata is None or updated_ms is None:
        return False
    from .item_sources.jira import stamp_ms

    fields: Row = metadata.get("fields") or {}
    read, taken = stamp_ms(fields.get("updated")), stamp_ms(fields.get("intake_updated"))
    return bool(read and updated_ms < read or taken and updated_ms <= taken)


def _module(source: str) -> ModuleType:
    module = IS.source(source)
    if module is None or not hasattr(module, "item_url"):
        raise _refuse("source", f"{source or '(none)'} has no intake; sources with one: jira")
    return module


def intake(
    conn: "sqlite3.Connection",
    actor: str,
    item: tuple[str, str, str, str],
    *,
    title: str | None,
    updated_at: str | None,
    create: Callable[[], str],
    see: Callable[[Row], None],
    stage: str | None = None,
) -> Intake:
    """Find or file the task for `item` (source, item type, key, connection), in the caller's write transaction.
    `create()` files the task as the caller's ordinary create does (its checks and defaults) and returns its id;
    `see(task)` refuses a found task the caller may not read. `stage`: where a filed task goes after its pipeline's first
    stage. Refuses (a Problem) an unknown source, item type or key, a connection the caller may not use, and a filed
    task with no title."""
    from . import api_tools as _api_tools
    from .item_sources.jira import stamp_ms

    AT: Any = _api_tools
    H = _h()
    source, item_type, text, connection_id = item
    module = _module(source)
    key = IS.match_key(source, item_type, text)
    if not key:
        raise _refuse("key", f"{text or '(none)'} is not a {source} {item_type} key")
    if not connection_id:
        raise _refuse("connection", f"A {source} item is one connection's: send connection_id, the tool's id")
    item_url: Callable[["sqlite3.Connection", str, str, str], str] = module.item_url
    try:
        url = item_url(conn, item_type, key, connection_id)
    except ValueError as error:
        raise _refuse("connection", str(error)) from None
    AT.key_for(conn, connection_id, actor)  # 403 tool_access, naming the setting, for a caller who may not use it
    updated_ms = None
    if updated_at is not None:
        updated_ms = stamp_ms(updated_at)
        if not updated_ms:
            raise _refuse("date", "updated_at must be an ISO-8601 date/time with a timezone")
    metadata = IM.get(conn, source, item_type, key, connection_id)
    stale = _stale(metadata, updated_ms)
    row = primary_task(conn, source, item_type, key, connection_id)
    created = resumed = False
    if row:
        see(row)
        if not stale and row["status"] != "open":
            H.task_move(conn, H.TICO_ACTOR, row["id"], H.TASK_REOPEN, f"Ready again in {module.NAME}.")
            resumed = True
    elif not stale:
        if not title:
            raise _refuse("validation", "A new task needs a title")
        task_id = create()
        H.task_link(conn, actor, task_id, url, title, item=(source, item_type, key, connection_id), mover=True)
        conn.execute(
            "UPDATE task_links SET is_primary=1 WHERE task_id=? AND source=? AND item_type=? AND item_key=? "
            "AND connection_id=?",
            (task_id, source, item_type, key, connection_id),
        )
        if stage:
            H.task_move(conn, actor, task_id, stage, f"Filed from {module.NAME} {key}.", mover=True)
        row = H.task(conn, task_id)
        created = True
    if not stale:
        IM.note(conn, source, item_type, key, {"intake_updated": updated_at} if updated_at else {}, connection_id)
    H.event(
        conn,
        actor,
        "item.intake",
        row["id"] if row else "",
        {
            "item": item_ref(source, item_type, key, connection_id),
            "created": created,
            "resumed": resumed,
            "stale": stale,
        },
    )
    return {"task_id": str(row["id"]) if row else None, "created": created, "resumed": resumed, "stale": stale}


# ----------------------------------------------------------------------------- closed outside


def _open_primary(conn: "sqlite3.Connection", source: str, item_type: str, key: str, connection_id: str) -> list[Row]:
    from . import task_engine as _task_engine

    E: Any = _task_engine
    rows: list[Row] = []
    for (task_id,) in conn.execute(
        "SELECT l.task_id FROM task_links l JOIN tasks t ON t.id = l.task_id WHERE l.is_primary=1 AND l.source=? "
        "AND l.item_type=? AND l.item_key=? AND l.connection_id=? AND t.status='open'",
        (source, item_type, key, connection_id),
    ).fetchall():
        found: Row | None = E.task(conn, str(task_id))
        if found:
            rows.append(found)
    return rows


def _asks(conn: "sqlite3.Connection", task_id: str, ref: str) -> list[Row]:
    """The open ❓ on the task that ask about this item being closed."""
    return [
        dict(zip(("id", "bot_id", "person_id"), found, strict=True))
        for found in conn.execute(
            "SELECT q.id, q.bot_id, q.person_id FROM question_marks q JOIN messages m ON m.id = q.message_id "
            "WHERE q.task_id=? AND q.cleared_at IS NULL AND json_extract(m.refs, ?) = ?",
            (task_id, f"$.{CLOSED_REF}", ref),
        ).fetchall()
    ]


def closed_outside(
    conn: "sqlite3.Connection", source: str, item_type: str, key: str, connection_id: str, words: str
) -> None:
    """The item turned closed outside: the owner of each open task holding its primary link is asked once whether to
    settle the task or keep it (`words`: what the source says, e.g. "Jira says Done")."""
    from . import person_asks, question_marks, task_wakes

    H = _h()
    ref = item_ref(source, item_type, key, connection_id)
    line = f"{key} is closed. Settle this task or keep it?"
    why = f"{words}. Reply settle to close it here, or keep and why."
    for row in _open_primary(conn, source, item_type, key, connection_id):
        owner = str(row.get("owner_id") or "")
        if is_bot(owner):
            task_wakes.tell(
                conn, row, f"{line} {words}. Close the task if the work is settled, or say why it stays.", quiet=False
            )
            continue
        if not is_human(owner) or _asks(conn, str(row["id"]), ref):
            continue
        # The task's bot, else the bot that filed it (an intake agent), else Agent Ops.
        bots = [str(row.get(name) or "") for name in ("assignee_id", "requester_id")]
        asker = person_asks.author(conn, *(bot for bot in bots if is_bot(bot)))
        if asker:
            conn.execute("SAVEPOINT outside_closed")
            try:
                question_marks.ask(
                    conn, asker, str(row["id"]), owner, f"{line}\n\n{why}", checked=False, refs={CLOSED_REF: ref}
                )
                conn.execute("RELEASE outside_closed")
                continue
            except H.Refused:  # the owner cannot be asked here: Tico's own message on the chat says it instead
                conn.execute("ROLLBACK TO outside_closed")
                conn.execute("RELEASE outside_closed")
        task_wakes.tell(conn, row, f"{line}\n\n{why}")


def reopened_outside(
    conn: "sqlite3.Connection", source: str, item_type: str, key: str, connection_id: str, words: str
) -> None:
    """The item turned open again outside: each open ❓ that asked about it being closed is taken back, and a bot that
    owns the task is told quietly."""
    from . import task_wakes

    H = _h()
    ref = item_ref(source, item_type, key, connection_id)
    for row in _open_primary(conn, source, item_type, key, connection_id):
        marks = _asks(conn, str(row["id"]), ref)
        if marks:
            conn.execute(
                "UPDATE question_marks SET cleared_at=?, cleared_reason='withdrawn', cleared_by_id=? "
                "WHERE id IN (SELECT value FROM json_each(?)) AND cleared_at IS NULL",
                (H.now(), H.TICO_ACTOR, H._dump([m["id"] for m in marks])),
            )
            for bot in sorted({str(m["bot_id"]) for m in marks}):
                H._recount(conn, bot)
            for mark in marks:
                H.event(
                    conn, H.TICO_ACTOR, "question.cleared", mark["id"], {"reason": "withdrawn", "to": mark["person_id"]}
                )
        if is_bot(row.get("owner_id")):
            task_wakes.tell(conn, row, f"{key} is open again ({words}): keep going.")
