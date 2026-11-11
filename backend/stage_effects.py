# pyright: strict
"""What entering a stage does, and how leaving it backwards undoes that (docs/pipelines-v3.md, "Entering a stage").

A stage's on-enter action is nothing, "file a linked task into pipeline X", or a source's action ("queue the PRs to
merge"). It happens once
per visit, in the move's transaction. Moving back or closing undoes it: tasks filed in that visit close with a note, and
PRs leave the merge queue.

Filing (`{"file_into": "<pipeline id>"}`, Product's In development into Engineering) files ONE task per visit,
linked (`links.kind = handoff`). It has the filing task's title and body, and a line `Spec: page:<file id>@<version>` naming
the page that counted on the nearest earlier stage with a page requirement (the Spec page as it was OK'd, D10). It is
filed by the filing task's owner (the Product Owner), who is its requester. When that is refused (the Product
Owner is paused or still onboarding) the engine files it itself; one refused even then (the target was retired)
files nothing and never fails the move: the stage then waits on its Linked work done ("No task filed yet"), the
owner's turn, and the owner may file one by hand. The owner's one wake for the stage opens with why (a private task:
the first stage's owner there cannot read it; the engine never adds a reader). History: `handoff_filed` on the
filing task.

A source's action (`{"act": "github.queue"}`, Engineering's Merge queues the task's PRs): owed on enter, owed back on
undo (backend/source_actions.py).
"""

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]


def _h() -> Any:
    from . import hubdb

    return hubdb


def _on_enter(stage: Row | None) -> Row:
    value = (stage or {}).get("on_enter")
    if isinstance(value, str):
        value = _h()._json(value, {})
    return cast("Row", value) if isinstance(value, dict) else {}


def _spec_line(conn: "sqlite3.Connection", task: Row) -> str:
    """`page:<file>@<version>`: the page that counted on the task's newest visit to the nearest earlier stage with a
    page requirement, or "" when there is none."""
    from . import task_engine, task_pages

    work = [s for s in task_engine.stages(conn, task["pipeline_id"]) if s["kind"] == "work"]
    at = next((i for i, s in enumerate(work) if s["id"] == task["stage_id"]), 0)
    for stage in reversed(work[:at]):
        if not conn.execute(
            "SELECT 1 FROM requirements WHERE stage_id=? AND kind='page' AND withdrawn_at IS NULL", (stage["id"],)
        ).fetchone():
            continue
        page = task_pages.newest_for_task(conn, task["id"], stage["id"])
        if page:
            return f"page:{page['file_id']}@{page['version']}"
    return ""


def _filer(conn: "sqlite3.Connection", task: Row, by: str) -> str:
    """Who files the handoff: the filing task's owner (the Product Owner), else its pipeline's manager, else whoever
    moved it."""
    H = _h()
    pipeline: Row = task.get("pipeline") or {}
    candidates: list[str | None] = [task.get("owner_id"), pipeline.get("manager_id"), by]
    for actor in candidates:
        if actor and (H.is_bot(actor) or H.is_human(actor)) and H._actor_exists(conn, actor):
            return str(actor)
    return str(H.KEEPER)


def _file(conn: "sqlite3.Connection", task: Row, target: str, by: str) -> tuple[str | None, str]:
    """File the handoff into pipeline `target`. Returns the filed task's id and "", or None and why it was refused."""
    H = _h()
    body = str(task.get("body") or "").rstrip()
    spec = _spec_line(conn, task)
    if spec:
        body = f"{body}\n\nSpec: {spec}" if body else f"Spec: {spec}"
    last: Exception | None = None
    filer = _filer(conn, task, by)
    for actor in dict.fromkeys((filer, str(H.KEEPER))):
        conn.execute("SAVEPOINT stage_effects_file")
        try:
            made = H.task_create(
                conn,
                actor,
                task.get("title") or "",
                body,
                actor,
                parent_id=task["id"],
                pipeline=target,
                link="handoff",
                deduplicate=False,
                lint=False,
            )
        except H.Refused as refused:
            conn.execute("ROLLBACK TO stage_effects_file")
            conn.execute("RELEASE stage_effects_file")
            last = refused
            continue
        conn.execute("RELEASE stage_effects_file")
        H.history_event(conn, "task", task["id"], by, "handoff_filed", None, made["id"], None, {"pipeline": target})
        return str(made["id"]), ""
    H.event(conn, by, "task.handoff_refused", task["id"], {"pipeline": target, "why": str(last or "")})
    return None, _refused_line(conn, target, last)


def _refused_line(conn: "sqlite3.Connection", target: str, refused: Exception | None) -> str:
    """Why no task was filed, in the refusal's own words (a private task: who owns the first stage there and cannot
    read it; the engine never adds a reader)."""
    from . import task_engine

    extra: Row = getattr(refused, "extra", None) or {}
    why = str(extra.get("cause") or getattr(refused, "detail", "") or "").strip().rstrip(".")
    name = (task_engine.pipeline(conn, target) or {}).get("name") or target
    return f"No {name} task was filed: {why}." if why else f"No {name} task was filed."


def enter(conn: "sqlite3.Connection", task: Row, by: str) -> str:
    """Run the on-enter action of the stage `task` just entered. `task` is `task_engine.task()`'s shape. Never raises
    into the move for a refused filing (see the module's docstring). Returns what the owner's wake for the stage opens
    with: why no task was filed, else ""."""
    action = _on_enter(task.get("stage"))
    target = action.get("file_into")
    lead = ""
    if isinstance(target, str) and target and task.get("visit"):
        _, lead = _file(conn, task, target, by)
    from . import source_actions

    source_actions.enter(conn, task, by)
    return lead


def filed_open(conn: "sqlite3.Connection", visit_id: str) -> list[str]:
    """Ids of the tasks filed during a visit that are still open, oldest first."""
    return [
        row[0]
        for row in conn.execute(
            "SELECT t.id FROM links l JOIN tasks t ON t.id = l.to_task_id "
            "WHERE l.visit_id=? AND t.status='open' ORDER BY l.created_at, l.id",
            (visit_id,),
        ).fetchall()
    ]


def merged_work(conn: "sqlite3.Connection", task_id: str) -> bool:
    """Whether a filed task has a linked PR that merged: its work is in the code, so undoing the filing leaves it open
    (docs/pipelines-v3.md scenario 24) to reach a release or be closed by its owner."""
    from .item_sources import github as GS

    return any(link["metadata"] and link["metadata"]["state"] == "merged" for link in GS.prs_of(conn, task_id))


def undo(conn: "sqlite3.Connection", task: Row, by: str, note: str) -> None:
    """Undo what the task's open visit did on entering: close the tasks filed in it that are still open, except one
    whose PR already merged."""
    from . import task_engine

    visit = task.get("visit")
    if not visit:
        return
    from . import source_actions

    source_actions.undo_stage(conn, task)
    for filed in filed_open(conn, visit["id"]):
        if merged_work(conn, filed):
            continue
        task_engine.move(conn, filed, task_engine.CLOSED, by, note, cascade=True)
