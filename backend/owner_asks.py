# pyright: strict
"""One ask of the person who owns a piece of work, for something only they can settle, taken back by Tico once the
cause goes away. Used by the release close-out (backend/release_closeout.py: a Jira issue withdrawn or closed outside
Tico) and by the outbound gate (backend/outbound_actions.py: an action whose outcome stays unknown).

The ask is an ordinary OK on a task (task_engine.add_requirement), asked by Tico: it is in the person's Needs you and,
like any OK, holds the task in its stage until it is answered, pushed past or withdrawn. On a task that is no longer
open (a close-out after Done), a small private task is filed for the person first, and closed again when the ask is
withdrawn.

Who is asked (`person_for`): the task's owner when that is a person who is here; else, for a bot's task or none, the
people the outbound gate asks for an OK (the bot's manager, then the company's default person).
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
LOG = logging.getLogger(__name__)


def _h() -> Any:
    from . import hubdb

    return hubdb


def _engine() -> Any:
    from . import task_engine

    return task_engine


def person_for(conn: "sqlite3.Connection", task: Row | None, actor: str = "") -> str | None:
    """The person to ask: the task's owner when a person who is here, else the first of the outbound gate's people for
    the task's owner bot (or `actor` when there is no task): its manager, then the company's default person."""
    from . import outbound_actions as OA
    from . import task_requirements as R

    H = _h()
    owner = str((task or {}).get("owner_id") or "")
    if H.is_human(owner) and R.active_person(conn, owner):
        return owner
    people = OA._people_for(conn, owner or actor)  # pyright: ignore[reportPrivateUsage]
    return people[0] if people else None


def ask(
    conn: "sqlite3.Connection", task_id: str | None, person: str, line: str, *, title: str, body: str
) -> tuple[str, str] | None:
    """Ask `person` `line` on the task (filed first when there is none or it is not open): (requirement id, task id),
    or None when the ask was refused (nothing is written then)."""
    H, E = _h(), _engine()
    conn.execute("SAVEPOINT owner_ask")
    try:
        row: Row | None = H.task(conn, task_id) if task_id else None
        if not row or row.get("status") != "open":
            made: Row = H.task_create(
                conn, H.TICO_ACTOR, title[:300], body, person, deduplicate=False, lint=False, private=True
            )
            row = H.task(conn, str(made["id"]))
            assert row is not None
        asked: Row = E.add_requirement(conn, str(row["id"]), H.TICO_ACTOR, "ok", person, line)
    except Exception as exc:  # noqa: BLE001 - a refused ask (the person can't read the task, left): nothing written
        conn.execute("ROLLBACK TO owner_ask")
        conn.execute("RELEASE owner_ask")
        LOG.info("Asking %s failed: %s", person, exc)
        return None
    conn.execute("RELEASE owner_ask")
    return str(asked["requirement_id"]), str(row["id"])


def withdraw(conn: "sqlite3.Connection", requirement_id: str, why: str, own_task: str | None) -> None:
    """Take back an ask (one already answered or past is left as it is). When it is on a task other than `own_task`,
    the task `ask` filed for it alone, that task is closed too."""
    if not requirement_id:
        return
    H, E = _h(), _engine()
    found = conn.execute(
        "SELECT v.task_id FROM requirements r JOIN visits v ON v.id = r.visit_id WHERE r.id=?", (requirement_id,)
    ).fetchone()
    if not found:
        return
    task_id = str(found[0])
    conn.execute("SAVEPOINT owner_withdraw")
    try:
        E.withdraw_requirement(conn, requirement_id, H.TICO_ACTOR, why)
        row: Row | None = H.task(conn, task_id)
        if task_id != own_task and row and row.get("status") == "open":
            E.move(conn, task_id, E.CLOSED, H.TICO_ACTOR, why)
    except Exception as exc:  # noqa: BLE001 - the ask stays; whoever it names can still answer it
        conn.execute("ROLLBACK TO owner_withdraw")
        LOG.warning("Taking back ask %s failed: %s", requirement_id, exc)
    conn.execute("RELEASE owner_withdraw")


def open_ask(conn: "sqlite3.Connection", requirement_id: str) -> bool:
    """Whether an ask still waits: not withdrawn, not answered, on a visit its task has not left."""
    if not requirement_id:
        return False
    return (
        conn.execute(
            "SELECT 1 FROM requirements r JOIN visits v ON v.id = r.visit_id WHERE r.id=? AND r.withdrawn_at IS NULL "
            "AND v.left_at IS NULL AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.requirement_id = r.id "
            "AND a.undone_at IS NULL)",
            (requirement_id,),
        ).fetchone()
        is not None
    )
