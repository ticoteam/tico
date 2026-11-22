# pyright: strict
"""One ask of the person who owns a piece of work, for something only they can settle, taken back by Tico once the
cause goes away. Used by the release close-out (backend/release_closeout.py: a Jira issue withdrawn or closed outside
Tico) and by the outbound gate (backend/outbound_actions.py: an action whose outcome stays unknown).

The ask is an ordinary OK on a task (task_engine.add_requirement), asked by Tico: it is in the person's Needs you and,
like any OK, holds the task in its stage until it is answered, pushed past or withdrawn. With no task, or one that is no
longer open (a close-out after Done, an unknown outbound outcome), a small private task is filed for the person first,
and closed again when the ask is withdrawn.

Who is asked (`people_for`), the first who can read the task (nobody when none can): the task's owner when that is a person who is here, then
the people the outbound gate asks for an OK (the bot's manager, then the company's default person). The OK is
people-only, so no bot answers or pushes past it.
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


def people_for(conn: "sqlite3.Connection", task: Row | None, actor: str = "") -> list[str]:
    """Whom to ask, in order, each a person who is here and reads the task as GET /api/v2/tasks/{id} does (privacy and
    the bot-activity rule, auth.actor_reads_task): its owner, then the outbound gate's people for its owner bot (or
    `actor` when there is no task): the bot's manager, then the company's default person. Empty when nobody can:
    nobody is asked then."""
    from . import outbound_actions as OA
    from . import task_requirements as R
    from . import auth as _auth

    H = _h()
    owner = str((task or {}).get("owner_id") or "")
    first = [owner] if H.is_human(owner) and R.active_person(conn, owner) else []
    people = list(dict.fromkeys(first + OA._people_for(conn, owner or actor)))  # pyright: ignore[reportPrivateUsage]
    auth: Any = _auth
    return [p for p in people if task is None or auth.actor_reads_task(conn, p, task)]


def _ask_on(conn: "sqlite3.Connection", task_id: str, person: str, line: str) -> str | None:
    """A people-only OK (no bot answers or pushes past it) asked by Tico on an open task, or None when refused (the
    person cannot read the task or has left): nothing is written then."""
    H, E = _h(), _engine()
    conn.execute("SAVEPOINT owner_ask")
    try:
        asked: Row = E.add_requirement(conn, task_id, H.TICO_ACTOR, "ok", person, line)
        conn.execute("UPDATE requirements SET who_oks='people_only' WHERE id=?", (asked["requirement_id"],))
    except Exception as exc:  # noqa: BLE001 - a refused ask
        conn.execute("ROLLBACK TO owner_ask")
        conn.execute("RELEASE owner_ask")
        LOG.info("Asking %s on %s failed: %s", person, task_id, exc)
        return None
    conn.execute("RELEASE owner_ask")
    return str(asked["requirement_id"])


def ask(
    conn: "sqlite3.Connection", task_id: str | None, people: list[str], line: str, *, title: str, body: str
) -> tuple[str, str] | None:
    """Ask the first of `people` who can be asked `line` on the open task; with no task, or one that is not open, on a
    private task filed for the first person who can be asked (titled `title`, `body` its first message): (requirement
    id, task id), or None with nobody to ask."""
    H = _h()
    row: Row | None = H.task(conn, task_id) if task_id else None
    if row and row.get("status") == "open":
        for person in people:
            found = _ask_on(conn, str(row["id"]), person, line)
            if found:
                return found, str(row["id"])
        return None
    for person in people:
        conn.execute("SAVEPOINT owner_file")
        try:
            made: Row = H.task_create(
                conn, H.TICO_ACTOR, title[:300], body, person, deduplicate=False, lint=False, private=True
            )
            found = _ask_on(conn, str(made["id"]), person, line)
            if not found:
                raise LookupError(person)
        except Exception:  # noqa: BLE001 - this person cannot be asked: the next one
            conn.execute("ROLLBACK TO owner_file")
            conn.execute("RELEASE owner_file")
            continue
        conn.execute("RELEASE owner_file")
        return found, str(made["id"])
    return None


def withdraw(
    conn: "sqlite3.Connection", requirement_id: str, why: str, own_task: str | None, *, advance: bool = True
) -> None:
    """Take back an ask (one already answered or past is left as it is); `advance` False when another ask is about to
    take its place. When it is on a task other than `own_task`, the task `ask` filed for it alone, that task is closed
    too."""
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
        E.withdraw_requirement(conn, requirement_id, H.TICO_ACTOR, why, advance_task=advance)
        row: Row | None = H.task(conn, task_id)
        if task_id != own_task and row and row.get("status") == "open":
            E.move(conn, task_id, E.CLOSED, H.TICO_ACTOR, why)
    except Exception as exc:  # noqa: BLE001 - the ask stays; whoever it names can still answer it
        conn.execute("ROLLBACK TO owner_withdraw")
        LOG.warning("Taking back ask %s failed: %s", requirement_id, exc)
    conn.execute("RELEASE owner_withdraw")
