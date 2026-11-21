# pyright: strict
"""How long a task waited on what: `task_wait_spans`, one row per stretch of the same headline wait.

    sync(conn, task_id, row=None, waits=None) -> bool     bring the task's open span in line with its headline now
    recheck(conn, task_id, have, found=None)               the never-stuck pass's backstop for one task it read
    reconcile(conn, task_ids, have) -> int                 the same for the open tasks the ladder does not check
    close_ended(conn) -> int                               close the open spans of tasks that are no longer open
    open_spans(conn) -> dict[str, Key]                     every open span, by task, in one read

`waiting_on` works out what a task waits on and forgets it; this keeps when each headline (its visit, state and who)
began and ended. The engine calls `sync` with the waits it already worked out after a write (a move, a change, an
answer, a snooze), and the card slot's reconcile calls it the same way. The never-stuck pass compares what it read of
each task it checks with the open spans (`recheck`), then every other open task on the engine (by hand, blocked,
snoozed: `reconcile`), and closes the spans of tasks that ended (`close_ended`): that is what catches a flip no write
made, such as checks finishing, a bot running out of credit or a stage getting room. Nothing reads or writes spans on
a read of a task. A write is one statement or two, only when the headline changed.
"""

from typing import TYPE_CHECKING, Any

from . import task_depends
from . import task_engine as E
from . import waiting_on as W

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
Key = tuple[str | None, str, str | None]  # (visit_id, kind, who)

_OPEN_SQL = "SELECT id, visit_id, kind, who FROM task_wait_spans WHERE task_id=? AND closed_at IS NULL"
_ENDED_SQL = (
    "SELECT s.id FROM task_wait_spans s LEFT JOIN tasks t ON t.id = s.task_id WHERE s.closed_at IS NULL "
    "AND (t.id IS NULL OR t.status <> 'open' OR t.pipeline_id IS NULL)"
)


def _h() -> Any:
    from . import hubdb

    return hubdb


def headline(row: Row | None, waits: list[W.Wait]) -> Key | None:
    """The span a task should have open now: its visit and its first wait. None when it waits on nothing (ended)."""
    if not row or row.get("status") != "open" or not waits:
        return None
    visit: Row = row.get("visit") or {}
    head = waits[0]
    return (str(visit["id"]) if visit.get("id") else None, head.state, head.who)


def _settled(conn: "sqlite3.Connection", row: Row) -> Key | None:
    """The headline when it is known without working the waits out: a snoozed task that no open task blocks waits on
    its snooze first (waiting_on.of)."""
    snooze: Row | None = row.get("snooze")
    if not snooze or row.get("status") != "open" or row["stage"].get("kind") != "work":
        return None
    if task_depends.blockers(conn, str(row["id"])):
        return None
    return (str(row["visit"]["id"]), W.SNOOZED, snooze["by_id"])


def _want(conn: "sqlite3.Connection", row: Row | None, waits: list[W.Wait] | None) -> Key | None:
    if waits is None and row and row.get("status") == "open":
        settled = _settled(conn, row)
        if settled:
            return settled
        waits = W.of(conn, row)
    return headline(row, waits or [])


def _open(conn: "sqlite3.Connection", task_id: str) -> tuple[str | None, Key | None]:
    found = conn.execute(_OPEN_SQL, (task_id,)).fetchone()
    return (str(found[0]), (found[1], str(found[2]), found[3])) if found else (None, None)


def _put(conn: "sqlite3.Connection", task_id: str, open_id: str | None, want: Key | None) -> None:
    """Close the open span and open one for `want`: one statement or two."""
    H = _h()
    now = H.now()
    if open_id:
        conn.execute("UPDATE task_wait_spans SET closed_at=? WHERE id=?", (now, open_id))
    if want:
        conn.execute(
            "INSERT INTO task_wait_spans (id, task_id, visit_id, kind, who, opened_at) VALUES (?,?,?,?,?,?)",
            (H.new_id(), task_id, *want, now),
        )


def sync(conn: "sqlite3.Connection", task_id: str, row: Row | None = None, waits: list[W.Wait] | None = None) -> bool:
    """Close the task's open span when its headline wait is no longer the same, and open one for the headline it has
    now. `row` (task_engine.task) and `waits` (waiting_on.of) are what the caller read just now, so nothing is worked
    out again. Returns whether it wrote."""
    want = _want(conn, row if row is not None else E.task(conn, task_id), waits)
    open_id, have = _open(conn, task_id)
    if have == want:
        return False
    _put(conn, task_id, open_id, want)
    return True


def open_spans(conn: "sqlite3.Connection") -> dict[str, Key]:
    """Every open span by task: what the never-stuck pass compares each task it reads with."""
    return {
        str(found[0]): (found[1], str(found[2]), found[3])
        for found in conn.execute("SELECT task_id, visit_id, kind, who FROM task_wait_spans WHERE closed_at IS NULL")
    }


def recheck(
    conn: "sqlite3.Connection",
    task_id: str,
    have: Key | None,
    found: "tuple[Row, Any, list[W.Wait]] | None" = None,
) -> bool:
    """The backstop for one task: `have` is its open span when the pass began, and `found` what the pass read of it
    (row, standings, waits), still true for its headline. Writes only when the headline differs, and never over a span a
    write in this pass already moved: that write read the task fresh."""
    row, waits = (found[0], found[2]) if found else (E.task(conn, task_id), None)
    want = _want(conn, row, waits)
    if want == have:
        return False
    open_id, now = _open(conn, task_id)
    if now != have:
        return False
    _put(conn, task_id, open_id, want)
    return True


# Every open task on the engine.
OPEN_TASKS = "SELECT id FROM tasks WHERE pipeline_id IS NOT NULL AND status = 'open' ORDER BY id"


def reconcile(conn: "sqlite3.Connection", task_ids: list[str], have: dict[str, Key]) -> int:
    """`recheck` for each of these tasks, read together (waiting_on.prime: nothing more when the pass read them
    already and wrote nothing since). Returns how many spans it changed."""
    W.prime(conn, task_ids)
    return sum(recheck(conn, task_id, have.get(task_id)) for task_id in task_ids)


def close_ended(conn: "sqlite3.Connection") -> int:
    """Close the open spans of tasks that are done, closed, deleted or off the engine by a path that did not sync.
    Reads first, so a pass with nothing to close writes nothing. Returns how many it closed."""
    ids = [str(found[0]) for found in conn.execute(_ENDED_SQL).fetchall()]
    if ids:
        marks = ",".join("?" * len(ids))
        conn.execute(f"UPDATE task_wait_spans SET closed_at=? WHERE id IN ({marks})", (_h().now(), *ids))
    return len(ids)
