# pyright: strict
"""A task that depends on another one ("Blocked by #304"): it cannot go on until that task ends.

The dependency is a relation of kind `depends_on` on the waiting task (task_relations: `from_task` depends on
`to_task`), written with `tico task relate <id> depends_on <task>`, the MCP tool or the task page, all through
`task_relations.relate`; the other task reads it as "Blocks #307". Any pipeline, any stage, several at once, and no end
date. It is a named wait, not a state:

- `blockers`: the tasks it depends on that are still open. While there is one, `waiting_on.of` says only "Blocked by
  #304 Growth synced sources part 1, in Build" (and any OK or answer still asked of a person): nobody's turn, so no bot is
  woken and the never-stuck check passes it by. It holds no slot where a stage has a limit, is never Late (the time
  it is blocked does not count, never_stuck._spent), does not move on by itself (task_engine.advance), and leaves its
  owner's Needs you. A reader who may not open the blocker reads "Blocked by a task you can't open".
- `release`: when a task ends, every dependency on it comes off (a dependency holds until its task first ends, so a
  reopened task blocks nothing again), and each task that depended on it and has no other open dependency is read
  again: it moves on if everything its stage asks is met (task_engine.advance), else its owner is woken once, "#304 is
  done" or "#304 was closed without doing it" so they re-plan. A deleted task does the same, "#304 was deleted"
  (`deleted`). No poll and no date. Every dependency written or taken off leaves a history row on the waiting task,
  from which `spans` gives the time it was blocked.
- `check`: the writer must be able to change the waiting task and read the other one (task_relations.relate); a loop,
  or a dependency of or on a task that has ended, is refused.

Snooze stays for "look again on a date". A PR, main or a release is reached through the task that carries it.
"""

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from . import read_cache

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
LOG = logging.getLogger(__name__)

RELATION = "depends_on"
KIND = "depends_on"  # the prefix of the wait's requirement id; never a stored requirement
LINE = "Blocked by {ref} {title}, in {stage}"
UNNAMED = "Blocked by a task you can't open"
DONE = "{ref} is done"
CLOSED = "{ref} was closed without doing it"
DELETED = "{ref} was deleted"
WAKE = "unblocked"

# A task, aliased by `%s`, with a dependency still open: for filters beside the open snooze's (hubdb.SNOOZED).
BLOCKED = (
    "EXISTS (SELECT 1 FROM task_relations dep JOIN tasks blocker ON blocker.id = dep.to_task "
    "WHERE dep.from_task = %s.id AND dep.kind = 'depends_on' AND blocker.status = 'open')"
)

_OPEN = (
    "SELECT d.from_task, d.to_task, d.created_at FROM task_relations d JOIN tasks b ON b.id = d.to_task "
    "WHERE d.from_task IN (SELECT value FROM json_each(?)) AND d.kind = 'depends_on' AND b.status = 'open' "
    "ORDER BY d.created_at, d.rowid"
)


def _engine() -> Any:
    from . import task_engine

    return task_engine


def ref(row: Row | None) -> str:
    """How a line names a task: `#304`, or its id's first eight characters when it has no number."""
    if not row:
        return "a task"
    number = row.get("number")
    return f"#{number}" if number is not None else str(row["id"])[:8]


def _open_of(conn: "sqlite3.Connection", task_ids: list[str]) -> dict[str, list[tuple[str, str]]]:
    import json

    out: dict[str, list[tuple[str, str]]] = {task_id: [] for task_id in task_ids}
    if task_ids:  # one statement however many
        for found in conn.execute(_OPEN, (json.dumps(task_ids),)).fetchall():
            out[str(found[0])].append((str(found[1]), str(found[2])))
    return out


def blockers(conn: "sqlite3.Connection", task_id: str) -> list[tuple[str, str]]:
    """(task id, since) of each task this one depends on that is still open, oldest dependency first. Kept for the
    snapshot (`prime` reads a list's together)."""
    return read_cache.value(conn, (KIND, task_id), lambda: _open_of(conn, [task_id])[task_id], shared=True)


def prime(conn: "sqlite3.Connection", task_ids: Iterable[str]) -> None:
    """Read the open dependencies of many tasks in one statement, for the `blockers` reads that follow."""
    values = read_cache.cache(conn)
    if values is None:
        return
    unread = sorted({str(t) for t in task_ids if t and (KIND, str(t)) not in values})
    for task_id, found in _open_of(conn, unread).items():
        values[(KIND, task_id)] = found


def blocked(conn: "sqlite3.Connection", task_id: str) -> bool:
    return bool(blockers(conn, task_id))


def wait_id(task_id: str, blocker_id: str) -> str:
    return f"{KIND}:{task_id}:{blocker_id}"


def is_wait(requirement_id: object) -> bool:
    """Whether a wait (waiting_on.Wait.requirement_id) is a dependency's."""
    return str(requirement_id or "").startswith(KIND + ":")


_NAMED = "SELECT t.id, t.number, t.title, s.name FROM tasks t LEFT JOIN stages s ON s.id = t.stage_id WHERE t.id=?"


def line(conn: "sqlite3.Connection", blocker_id: str) -> str:
    """ "Blocked by #304 Growth synced sources part 1, in Build"."""
    found = read_cache.one(conn, _NAMED, (blocker_id,))
    if not found:
        return UNNAMED
    said = LINE.format(ref=ref({"id": found[0], "number": found[1]}), title=str(found[2] or "").strip(), stage="")
    return said + str(found[3]) if found[3] else said.removesuffix(", in ")


_BLOCKING = (
    "SELECT d.to_task, d.from_task, w.number, d.created_at FROM task_relations d JOIN tasks w ON w.id = d.from_task "
    "WHERE d.to_task IN (SELECT value FROM json_each(?)) AND d.kind = 'depends_on' AND w.status = 'open' "
    "ORDER BY d.created_at, d.rowid"
)


def blocking(conn: "sqlite3.Connection", task_ids: Iterable[str]) -> dict[str, list[tuple[str, object, str]]]:
    """{task id: [(dependent id, its number, since)]}: the open tasks each of these blocks ("Blocks #307, #308"), oldest
    dependency first, in one statement however many."""
    import json

    ids = sorted({str(t) for t in task_ids if t})
    out: dict[str, list[tuple[str, object, str]]] = {task_id: [] for task_id in ids}
    if ids:
        for found in conn.execute(_BLOCKING, (json.dumps(ids),)):
            out[str(found[0])].append((str(found[1]), found[2], str(found[3])))
    return out


def blocks_line(named: list[str], hidden: int = 0) -> str:
    """ "Blocks #307, #308", "Blocks #307 and 1 more", or "Blocks 2 tasks" when none may be named."""
    if not named:
        return f"Blocks {hidden} task{'' if hidden == 1 else 's'}"
    return f"Blocks {', '.join(named)}" + (f" and {hidden} more" if hidden else "")


def dependents(conn: "sqlite3.Connection", task_id: str) -> list[str]:
    """The tasks that depend on this one."""
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT from_task FROM task_relations WHERE to_task=? AND kind=? ORDER BY created_at, rowid",
            (task_id, RELATION),
        )
    ]


def _depends_on(conn: "sqlite3.Connection", task_id: str) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT to_task FROM task_relations WHERE from_task=? AND kind=? ORDER BY created_at, rowid",
            (task_id, RELATION),
        )
    ]


def spans(conn: "sqlite3.Connection", task_id: str, since: str) -> list[tuple[str, str | None]]:
    """(from, to) of each time the task was blocked, from `since` on, for Late (never_stuck._spent): from when a
    dependency was written to when it came off (its task ended, it was taken off, or its task was deleted), None while
    it is on. Read from the task's history, where every write and removal of a dependency leaves a row, so a span
    outlives its relation row; a relation row with no history (a trash restore) counts from its own date."""
    opened: dict[str, str] = {}
    out: list[tuple[str, str | None]] = []
    for old, new, at in conn.execute(
        "SELECT old, new, created_at FROM history_events WHERE subject_type='task' AND subject_id=? AND kind=? "
        "ORDER BY created_at, rowid",
        (task_id, RELATION),
    ).fetchall():
        if new:
            opened.setdefault(str(new), str(at))
        elif old and str(old) in opened:
            out.append((opened.pop(str(old)), str(at)))
    for blocker, created in conn.execute(
        "SELECT to_task, created_at FROM task_relations WHERE from_task=? AND kind=?", (task_id, RELATION)
    ).fetchall():
        out.append((opened.pop(str(blocker), str(created)), None))
    out += [(at, None) for at in opened.values()]
    return [(max(start, since), end) for start, end in out if end is None or end > since]


# ----------------------------------------------------------------------------- writes
def check(conn: "sqlite3.Connection", actor: str, later: Row, blocker: Row) -> None:
    """Refuse a dependency that cannot hold: of a task that has ended, on one that has ended, or a loop (#304 already
    depends on #307, however far round). The writer's rights are checked by the caller (task_relations.relate)."""
    from . import hubdb

    H: Any = hubdb
    for row, what in ((later, "it"), (blocker, ref(blocker))):
        if row["status"] != "open":
            who = "The task" if what == "it" else what
            H.refuse(conn, actor, "task_ended", f"{who} has ended: there is nothing to wait for")
    path = _path(conn, str(blocker["id"]), str(later["id"]))
    if path:
        E = _engine()
        names = " → ".join(ref(E.task(conn, tid) or {"id": tid}) for tid in [str(later["id"]), *path])
        H.refuse(
            conn,
            actor,
            "kind",
            f"That would make a loop ({names}): {ref(blocker)} already depends on {ref(later)}.",
        )


def _path(conn: "sqlite3.Connection", start: str, goal: str) -> list[str]:
    """The tasks from `start` to `goal` along "depends on", both ends included, or [] when there is no such path."""
    back: dict[str, str | None] = {start: None}
    frontier = [start]
    while frontier:
        nxt: list[str] = []
        for tid in frontier:
            for other in _depends_on(conn, tid):
                if other in back:
                    continue
                back[other] = tid
                if other == goal:
                    out = [goal]
                    cursor = back[goal]
                    while cursor is not None:
                        out.append(cursor)
                        cursor = back[cursor]
                    return list(reversed(out))
                nxt.append(other)
        frontier = nxt
    return []


def release(conn: "sqlite3.Connection", ended_id: str, by: str) -> int:
    """`ended_id` ended: each dependency on it comes off, with a history row on both tasks, so a dependency holds only
    until its task first ends (reopening that task blocks nothing again). Then each task that depended on it and has no
    open dependency left moves on, or its owner is woken once (`release_one`). Returns how many were released."""
    from . import task_relations

    TR: Any = task_relations  # untyped module
    waiting = dependents(conn, ended_id)
    for task_id in waiting:
        conn.execute(
            "DELETE FROM task_relations WHERE from_task=? AND to_task=? AND kind=?", (task_id, ended_id, RELATION)
        )
        TR._history(conn, by, task_id, ended_id, RELATION, False, "it ended")
    found: Any = conn.execute("SELECT number, status FROM tasks WHERE id=?", (ended_id,)).fetchone()
    said = (DONE if found and found[1] == "done" else CLOSED).format(
        ref=ref({"id": ended_id, "number": found[0] if found else None})
    )
    return sum(release_one(conn, task_id, ended_id, said) for task_id in waiting)


def deleted(conn: "sqlite3.Connection", task_id: str, number: object, by: str) -> int:
    """`task_id` is being deleted: each dependency on it comes off (a history row on the task that waited), and each
    task left with no open dependency moves on, or its owner is woken once, "#304 was deleted". Call it before the
    relation rows go."""
    from . import hubdb

    H: Any = hubdb
    waiting = dependents(conn, task_id)
    for waiter in waiting:
        conn.execute(
            "DELETE FROM task_relations WHERE from_task=? AND to_task=? AND kind=?", (waiter, task_id, RELATION)
        )
        H._task_event(conn, waiter, by, RELATION, task_id, None, "it was deleted")
    said = DELETED.format(ref=ref({"id": task_id, "number": number}))
    return sum(release_one(conn, waiter, task_id, said) for waiter in waiting)


def restores(conn: "sqlite3.Connection", row: Row, restored_id: str) -> bool:
    """Whether a trashed dependency goes back with the task restored from the trash. A dependency on that task does not:
    the tasks that waited on it were released when it was deleted (`deleted`), and their owners may have moved on.
    One of that task on another goes back only while the other is still open: a dependency holds until its task first
    ends."""
    if str(row["to_task"]) == restored_id:
        return False
    found = conn.execute("SELECT status FROM tasks WHERE id=?", (row["to_task"],)).fetchone()
    return bool(found) and found[0] == "open"


def release_one(conn: "sqlite3.Connection", task_id: str, ended_id: str, said: str) -> int:
    """One task whose dependency `ended_id` came off. Still blocked by another: nothing, and nobody is woken. Else it
    moves on if everything is met and there is room; if not, its owner is woken once with `said`. A move the engine
    refuses is logged and the owner is told instead: a dependent never stops its blocker from ending. 1 when it was
    released."""
    E = _engine()
    row: Row | None = E.task(conn, task_id)
    if not row or row["status"] != "open" or not row.get("visit"):
        return 0
    if _open_of(conn, [task_id])[task_id]:
        return 0
    E.changed(conn, task_id, E.SYSTEM)
    from . import hubdb

    H: Any = hubdb
    try:
        conn.execute("SAVEPOINT depends_release")
        moved = E.advance(conn, task_id)
        conn.execute("RELEASE depends_release")
    except H.Refused as refused:  # advance logged it (task_engine.log_refused_move)
        conn.execute("ROLLBACK TO depends_release")
        conn.execute("RELEASE depends_release")
        LOG.info("dependency released %s but its move was refused: %s", task_id, refused)
        moved = None
    if moved:
        return 1
    E.tell_owner(conn, task_id, said, f"{WAKE}:{ended_id}")
    return 1
