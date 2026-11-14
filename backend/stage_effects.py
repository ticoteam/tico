# pyright: strict
"""What entering a stage does, and how leaving it backwards undoes that (docs/pipelines-v3.md, "Entering a stage").

A stage's on-enter action is nothing, "file a linked task into pipeline X", or a source's action ("queue the PRs to
merge"). It happens once
per visit, in the move's transaction. Moving back or closing undoes it: tasks filed in that visit close with a note, and
PRs leave the merge queue.

Filing (`{"file_into": "<pipeline id>"}`, Product's In development into Engineering) files ONE task per visit, linked
(`links.kind = handoff`). It has the filing task's title and body, and a line `Spec: page:<file id>@<version>` naming
the page that counted on the nearest earlier stage with a page requirement (the Spec page as it was OK'd, D10), then
what each person said with their OK of it, marked as their notes on the spec (`sign_off_notes`). It is filed by the
filing task's owner (the Product Owner), who is its requester. Where the target pipeline has a manager, the filed task
has no assignee: a first stage owned by the task's assignee then has nobody, and that manager is woken to pick one
(C25); a Product Owner is not a builder. General, and a target with no manager, get the filer as its assignee. When the
filer is refused (the Product Owner is paused or still onboarding) the engine files it itself; one refused even then (the
target was retired) files nothing and never fails the move: the stage then waits on its Linked work done ("No task filed
yet"), the owner's turn, and the owner may file one by hand. The owner's one wake for the stage opens with why (a
private task: the first stage's owner there cannot read it; the engine never adds a reader). History: `handoff_filed` on
the filing task.

A source's action (`{"act": "github.queue"}`, Engineering's Merge queues the task's PRs): owed on enter, owed back on
undo (backend/source_actions.py).
"""

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
FORWARD = ("met", "pushed_past")  # how a visit left that took the task on to the next stage (task_engine.move)


def _h() -> Any:
    from . import hubdb

    return hubdb


def _on_enter(stage: Row | None) -> Row:
    value = (stage or {}).get("on_enter")
    if isinstance(value, str):
        value = _h()._json(value, {})
    return cast("Row", value) if isinstance(value, dict) else {}


def _spec_page(conn: "sqlite3.Connection", task: Row) -> Row | None:
    """The page that counted on the task's newest visit to the nearest earlier stage with a page requirement
    (`requirement_pages` row: `file_id`, `version`, `visit_id`), or None when there is none."""
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
            return page
    return None


def _person_oks(conn: "sqlite3.Connection", visit_id: str) -> list[Row]:
    """Each person's OK (of an OK or a page) on a visit, the newest answer per requirement, oldest first, as {by,
    requirement, notes}. A send back, a question's answer and a bot's OK are not sign-offs."""
    from . import task_requirements as R

    H = _h()
    out: list[Row] = []
    for answer in R.newest_answers(conn, visit_id).values():
        if answer.get("answer") != "ok" or not H.is_human(answer.get("by_id")):
            continue
        requirement = R.get(conn, str(answer["requirement_id"]))
        if requirement and requirement.get("kind") in ("ok", "page"):
            notes = str(answer.get("notes") or "").strip()
            out.append({"by": str(answer["by_id"]), "requirement": R.name_of(requirement), "notes": notes})
    return out


def sign_off_notes(conn: "sqlite3.Connection", visit_id: str) -> list[Row]:
    """What people said when they OK'd something on a visit: their OKs that came with notes (`_person_oks`)."""
    return [said for said in _person_oks(conn, visit_id) if said["notes"]]


def notes_on_the_way_in(conn: "sqlite3.Connection", task: Row) -> list[str]:
    """What the person said with the newest sign-off the open task passed on its way into this stage, one line each,
    marked as theirs: walking back over the visits it left forward, the first one a person OK'd. Nothing when that
    OK came with no notes, or the task came back or was reopened since. Every wake in the stage carries it
    (task_wakes.body), so whoever acts after a sign-off (the Builder after a Review OK, the Release Owner after the
    pre-release OK) reads what the person said there."""
    from . import task_engine

    H = _h()
    if not task.get("visit"):
        return []
    left = conn.execute(
        "SELECT id, stage_id, how FROM visits WHERE task_id=? AND left_at IS NOT NULL "
        "ORDER BY left_at DESC, entered_at DESC LIMIT 20",
        (task["id"],),
    ).fetchall()
    for visit_id, stage_id, how in left:
        if how not in FORWARD:
            break  # left backwards (or closed and reopened): what was OK'd there is not on the way in
        oks = _person_oks(conn, str(visit_id))
        if oks:
            stage_name = (task_engine.stage(conn, str(stage_id)) or {}).get("name") or "the stage before"
            return [
                f"{H.actor_name(conn, said['by'])}'s notes with their OK of {said['requirement']} in {stage_name}: "
                f"{said['notes']}"
                for said in oks
                if said["notes"]
            ]
    return []


def _sent_back(conn: "sqlite3.Connection", task: Row) -> str:
    """Why the task just came back into this stage, as the one who moved it back said it ("Not validated: the row is
    still clipped", validate_gate), naming the stage it came from; "" when it did not come back."""
    from . import task_engine

    found = conn.execute(
        "SELECT stage_id, how, why FROM visits WHERE task_id=? AND left_at IS NOT NULL "
        "ORDER BY left_at DESC, entered_at DESC LIMIT 1",
        (task["id"],),
    ).fetchone()
    if not found or found[1] != "back" or not str(found[2] or "").strip():
        return ""
    name = (task_engine.stage(conn, str(found[0])) or {}).get("name") or "a later stage"
    return f"Sent back from {name}: {' '.join(str(found[2]).split())}"


def _handoff_body(conn: "sqlite3.Connection", task: Row) -> str:
    """The filed task's body: the filing task's, why it came back when it did (`_sent_back`), the `Spec:` line, and what each person said with their OK of the
    spec, marked as theirs, so whoever builds it does not ask again what was already answered there."""
    H = _h()
    parts = [str(task.get("body") or "").rstrip()]
    back = _sent_back(conn, task)
    if back:
        parts.append(back)
    page = _spec_page(conn, task)
    if page:
        parts.append(f"Spec: page:{page['file_id']}@{page['version']}")
        for said in sign_off_notes(conn, str(page["visit_id"])):
            parts.append(f"{H.actor_name(conn, said['by'])}'s notes on the spec, given with their OK:\n{said['notes']}")
    return "\n\n".join(part for part in parts if part)


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
    body = _handoff_body(conn, task)
    last: Exception | None = None
    filer = _filer(conn, task, by)
    from . import task_engine

    # A task given to someone in General is theirs, as on any General task; elsewhere nobody picks an owner where the
    # target has no manager. In both, the filer stays the assignee.
    unassigned = target != "general" and bool((task_engine.pipeline(conn, target) or {}).get("manager_id"))
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
                unassigned=unassigned,
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
