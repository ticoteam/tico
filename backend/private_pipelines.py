# pyright: strict
"""Private pipelines (docs/pipelines-v3.md "Private pipelines"): every task in one is a private task.

No new access system. A task that enters a private pipeline (`hubdb.task_create`, which stage effects file through, and
`task_engine.restore`) is made private whatever was asked, and its reader list gets the pipeline's manager and the
company's person (`waiting_on.company_person`: its owner while they can act). Its requester and owner read it as any
private task's do. Nobody else is added: not the manager's group, not an Admin.

The pipeline itself (name, goal, stages, manager, counts) stays visible to everyone, so anyone can file into it.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

STAYS_PRIVATE = "A task in a private pipeline stays private."


def _h() -> Any:
    from . import hubdb

    return hubdb


def is_private(row: Row | None) -> bool:
    """Whether a pipeline row (`task_engine.pipeline`) is private."""
    return bool(row and row.get("private"))


def of_task(conn: "sqlite3.Connection", task_id: str) -> bool:
    """Whether the task is in a private pipeline now."""
    found = conn.execute(
        "SELECT p.private FROM tasks t JOIN pipelines p ON p.id = t.pipeline_id WHERE t.id=?", (task_id,)
    ).fetchone()
    return bool(found and found[0])


def readers(conn: "sqlite3.Connection", pipeline_row: Row) -> list[str]:
    """Who a private pipeline adds to each task's reader list: its manager and the company's person."""
    from . import waiting_on

    out = [pipeline_row.get("manager_id"), waiting_on.company_person(conn)]
    return [str(who) for who in dict.fromkeys(out) if who]


def admit(conn: "sqlite3.Connection", task_id: str, pipeline_row: Row) -> None:
    """Make a task about to enter private pipeline `pipeline_row` private, with the pipeline's readers on its list.
    The system does it (KEEPER in the history), before the task enters its first stage, so the engine's rule that a
    private task only goes where its owner reads it (`task_engine.shut_out`) holds for the manager's stages."""
    H = _h()
    row = H._one(conn, "SELECT id, private, readers, requester_id FROM tasks WHERE id=?", (task_id,))
    if not row:
        return
    have = H.task_readers(row)
    # The owner is listed too: a stage or a reassignment can take the task from them, and they still read it.
    added = [who for who in readers(conn, pipeline_row) if who not in have and who != row.get("requester_id")]
    if added:
        conn.execute("UPDATE tasks SET readers=? WHERE id=?", (H._dump(have + added), task_id))
        for who in added:
            H._task_event(conn, task_id, H.KEEPER, "reader", None, who, "")
    if not H.task_private(conn, row):
        # A company task coming back into one (a restore): private now, its thread narrowed to who reads it.
        H._made_private(conn, H.KEEPER, task_id)
