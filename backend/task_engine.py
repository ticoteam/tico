# pyright: strict
"""The one writer of a task's stage, owner and status (docs/pipelines-v3.md, "The five rules").

Every change to `tasks.status`, `stage_id`, `owner_id` and `pipeline_id` is made here, by `_write`, which opens the
guard the trigger `tasks_move_only` reads, writes, and closes it again inside the caller's transaction. Reassign, Done,
Close and Reopen are all moves. This module is also the only writer of visits, snoozes, links, and requirements added to
one task.

Every function takes the caller's open write transaction and never commits. Each returns the task as `task()` gives it
after the write, and refuses through `hubdb.refuse` with a rule that starts with `task_`. No such refusal counts toward
quarantining a bot.

Names and signatures are in docs/dev/pipelines-v3-build.md.
"""

import json
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, NoReturn

from . import hubdb_core, stage_effects
from . import task_requirements as R
from . import task_wakes
from .hubdb_core import KEEPER as SYSTEM

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

# hubdb_core is untyped: its actor helpers get their types here.
_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human

NEXT, DONE, CLOSED, REOPEN = "next", "done", "closed", "reopen"
MAX_DEPTH = 5  # sub-tasks nest at most this deep (C20)
BOT_SNOOZES = 3  # a bot's snoozes in one stage before the manager bot gets the task
GUARDED = ("status", "stage_id", "owner_id", "pipeline_id")


def _h() -> Any:
    from . import hubdb

    return hubdb


def _refuse(conn: "sqlite3.Connection", by: str, rule: str, detail: str, task_id: str | None = None) -> NoReturn:
    _h().refuse(conn, by, rule, detail, task=task_id)
    raise AssertionError("refuse() always raises")  # pragma: no cover


def _part(row: Row, key: str) -> Row:
    """The dict a task carries under `key` (its open visit, its pipeline), or an empty one."""
    return row.get(key) or {}


# ----------------------------------------------------------------------------- reads
def pipeline(conn: "sqlite3.Connection", pipeline_id: str) -> Row | None:
    return _h()._one(conn, "SELECT * FROM pipelines WHERE id=?", (pipeline_id,))


def stages(conn: "sqlite3.Connection", pipeline_id: str) -> list[Row]:
    """A pipeline's stages in order: its work stages, then Done, then Closed."""
    return _h()._rows(
        conn.execute(
            "SELECT * FROM stages WHERE pipeline_id=? AND retired_at IS NULL "
            "ORDER BY CASE kind WHEN 'work' THEN 0 WHEN 'done' THEN 1 ELSE 2 END, position, id",
            (pipeline_id,),
        )
    )


def stage(conn: "sqlite3.Connection", stage_id: str) -> Row | None:
    return _h()._one(conn, "SELECT * FROM stages WHERE id=?", (stage_id,))


def task(conn: "sqlite3.Connection", task_id: str) -> Row | None:
    """A task as the engine sees it: its row, with `visit` (the open one), `stage`, `pipeline` and `snooze` (the open
    one, or None). None for a task that is not on the engine."""
    H = _h()
    row = H._one(conn, "SELECT * FROM tasks WHERE id=?", (task_id,))
    if not row or not row.get("pipeline_id"):
        return None
    row["visit"] = H._one(conn, "SELECT * FROM visits WHERE task_id=? AND left_at IS NULL", (task_id,))
    row["stage"] = stage(conn, row["stage_id"])
    row["pipeline"] = pipeline(conn, row["pipeline_id"])
    row["snooze"] = H._one(conn, "SELECT * FROM snoozes WHERE task_id=? AND ended_at IS NULL", (task_id,))
    return row


def _task(conn: "sqlite3.Connection", task_id: str) -> Row:
    """`task()` for a task the engine just wrote, which is on the engine."""
    row = task(conn, task_id)
    if row is None:
        raise LookupError(f"task {task_id} is not on the engine")
    return row


def depth(conn: "sqlite3.Connection", task_id: str) -> int:
    """How many tasks sit above this one: 0 for a task nobody filed."""
    seen, current, levels = {task_id}, task_id, 0
    while True:
        row = conn.execute("SELECT from_task_id FROM links WHERE to_task_id=?", (current,)).fetchone()
        if not row or row[0] in seen:
            return levels
        current = row[0]
        seen.add(current)
        levels += 1


def has_room(conn: "sqlite3.Connection", stage_row: Row, extra: int = 0) -> bool:
    """A stage has room when it has no limit, or holds fewer open tasks that are not snoozed than its limit."""
    limit = stage_row.get("task_limit")
    if stage_row["kind"] != "work" or not limit:
        return True
    held = conn.execute(
        "SELECT COUNT(*) FROM tasks t WHERE t.stage_id=? AND t.status='open' "
        "AND NOT EXISTS (SELECT 1 FROM snoozes s WHERE s.task_id = t.id AND s.ended_at IS NULL)",
        (stage_row["id"],),
    ).fetchone()[0]
    return held + extra < limit


def next_stage(conn: "sqlite3.Connection", task_row: Row) -> Row | None:
    """The stage after the task's own: the next work stage, or Done after the last one. None for an ended task."""
    if task_row["stage"]["kind"] != "work":
        return None
    order = stages(conn, task_row["pipeline_id"])
    work = [s for s in order if s["kind"] == "work"]
    at = next((i for i, s in enumerate(work) if s["id"] == task_row["stage_id"]), None)
    if at is not None and at + 1 < len(work):
        return work[at + 1]
    return next((s for s in order if s["kind"] == "done"), None)


def owner_for(stage_row: Row, assignee: str | None, current: str | None = None) -> str | None:
    """Who owns a task in a stage: the stage's owner, or the task's assignee where the stage says so. A task that
    ends keeps the owner it had."""
    if stage_row["kind"] != "work":
        return current
    if stage_row.get("owner") == "assignee":
        return assignee
    return stage_row.get("owner") or None


# ----------------------------------------------------------------------------- the writer
WRITES = frozenset(
    {
        "status",
        "stage_id",
        "owner_id",
        "pipeline_id",
        "assignee_id",
        "version",
        "updated_at",
        "done_at",
        "closed_at",
        "closed_by_id",
    }
)


def _write(conn: "sqlite3.Connection", task_id: str, **fields: Any) -> None:
    """The only statement that changes a task's status, stage, owner or pipeline. The guard is open for this one
    statement and closed again before anything else runs, inside the caller's transaction."""
    names = list(fields)
    if set(names) - WRITES:
        raise ValueError(f"not a column the engine writes: {sorted(set(names) - WRITES)}")
    sets = ", ".join(f"{name}=?" for name in names)
    conn.execute("INSERT OR REPLACE INTO task_move_guards (id, open) VALUES (1, 1)")
    try:
        conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", (*[fields[n] for n in names], task_id))
    finally:
        conn.execute("UPDATE task_move_guards SET open=0 WHERE id=1")


def _open_visit(conn: "sqlite3.Connection", task_id: str, stage_id: str, how: str, ts: str) -> str:
    H = _h()
    visit_id = H.new_id()
    conn.execute(
        "INSERT INTO visits (id, task_id, stage_id, entered_at, entered_how, last_change_at) VALUES (?,?,?,?,?,?)",
        (visit_id, task_id, stage_id, ts, how, ts),
    )
    return visit_id


def _close_visit(conn: "sqlite3.Connection", visit: Row, how: str, by: str, why: str | None, ts: str) -> None:
    """Leave a visit. The requirements added to it end with it."""
    conn.execute(
        "UPDATE visits SET left_at=?, how=?, by_id=?, why=? WHERE id=? AND left_at IS NULL",
        (ts, how, str(by), why, visit["id"]),
    )
    conn.execute(
        "UPDATE requirements SET withdrawn_at=? WHERE visit_id=? AND withdrawn_at IS NULL",
        (ts, visit["id"]),
    )


def _check_version(
    conn: "sqlite3.Connection", by: str, row: Row, seen_version: int | str | None, cascade: bool = False
) -> None:
    if cascade:
        return
    if seen_version is None:
        if is_bot(by):
            _refuse(conn, by, "task_changed", "Say which version of the task you saw", row["id"])
        return
    if int(seen_version) != int(row.get("version") or 0):
        _refuse(conn, by, "task_changed", "The task changed since you looked. Read it again.", row["id"])


def _need_why(
    conn: "sqlite3.Connection", by: str, why: str | None, what: str, task_id: str, everyone: bool = False
) -> None:
    if (everyone or is_bot(by)) and not (why or "").strip():
        _refuse(conn, by, "task_why", f"Say why you {what}", task_id)


def _event(
    conn: "sqlite3.Connection",
    task_id: str,
    by: str,
    kind: str,
    old: str | None = None,
    new: str | None = None,
    note: str | None = None,
    data: Row | None = None,
) -> None:
    _h().history_event(conn, "task", task_id, by, kind, old, new, note, data)


def _wake_turn(conn: "sqlite3.Connection", row: Row, lead: str = "") -> str | None:
    """Tell whoever has the turn in a stage the task just entered, once per visit. A person is not told: their list is a
    read. A stage with nobody set wakes the pipeline's manager bot to pick someone (C25)."""
    if row["stage"]["kind"] != "work" or row.get("snooze") or row["stage"].get("by_hand"):
        return None
    owner = row.get("owner_id")
    if is_bot(owner):
        return task_wakes.add(conn, row, owner, row["visit"], task_wakes.body(conn, row, lead))
    if not owner:
        manager = _part(row, "pipeline").get("manager_id")
        if is_bot(manager):
            lead = lead or "This task has no owner. Pick one and reassign it."
            return task_wakes.add(conn, row, manager, row["visit"], task_wakes.body(conn, row, lead))
    return None


# ----------------------------------------------------------------------------- new
def enter(
    conn: "sqlite3.Connection",
    task_id: str,
    pipeline_id: str,
    by: str,
    *,
    assignee: str | None = None,
    parent: str | None = None,
    link: str = "sub_task",
) -> Row:
    """New. Put a task that is not on the engine yet into its pipeline's first stage, open its visit and wake the
    owner. `parent` links it to the task that filed it, on that task's open visit."""
    H = _h()
    row = H._one(conn, "SELECT * FROM tasks WHERE id=?", (task_id,))
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    if row.get("pipeline_id"):
        _refuse(conn, by, "task_changed", "The task is already in a pipeline", task_id)
    pipe = pipeline(conn, pipeline_id)
    first = next((s for s in stages(conn, pipeline_id) if s["kind"] == "work"), None) if pipe else None
    if not pipe or pipe.get("retired_at") or not first:
        _refuse(conn, by, "task_no_such_stage", "No such pipeline", task_id)
    above: Row | None = None
    if parent:
        above = task(conn, parent)
        if not above:
            _refuse(conn, by, "not-found", "No such task to file under", task_id)
        if depth(conn, parent) >= MAX_DEPTH:
            _refuse(conn, by, "task_too_deep", f"Sub-tasks nest at most {MAX_DEPTH} deep", task_id)
    ts = H.now()
    owner = owner_for(first, assignee)
    _write(
        conn,
        task_id,
        pipeline_id=pipeline_id,
        stage_id=first["id"],
        owner_id=owner,
        assignee_id=assignee,
        status="open",
        updated_at=ts,
    )
    _open_visit(conn, task_id, first["id"], "new", ts)
    if above:
        conn.execute(
            "INSERT INTO links (id, from_task_id, to_task_id, visit_id, kind, created_at) VALUES (?,?,?,?,?,?)",
            (H.new_id(), parent, task_id, _part(above, "visit").get("id"), link, ts),
        )
    _event(conn, task_id, by, "move", None, first["id"], None, {"move": "new", "owner": owner, "parent": parent})
    entered = _task(conn, task_id)
    stage_effects.enter(conn, entered, by)
    _wake_turn(conn, entered)
    return entered


# ----------------------------------------------------------------------------- move
def _target(conn: "sqlite3.Connection", by: str, row: Row, to: str) -> tuple[Row, str] | tuple[None, None]:
    """The stage `to` names for this task, and the kind of move it is: forward, back, done, closed or reopen. None
    when the task is already there."""
    order = stages(conn, row["pipeline_id"])
    work = [s for s in order if s["kind"] == "work"]
    here = row["stage"]
    ended = here["kind"] != "work"
    if to == NEXT:
        if ended:
            _refuse(conn, by, "task_ended", f"The task is {here['name']}. Reopen it first.", row["id"])
        target = next_stage(conn, row)
    elif to in (DONE, CLOSED):
        target = next((s for s in order if s["kind"] == to), None)
    elif to == REOPEN:
        if not ended:
            return None, None
        before = conn.execute(
            "SELECT v.stage_id FROM visits v JOIN stages s ON s.id = v.stage_id "
            "WHERE v.task_id=? AND v.left_at IS NOT NULL AND s.kind='work' AND s.retired_at IS NULL "
            "ORDER BY v.left_at DESC, v.entered_at DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        target = next((s for s in work if before and s["id"] == before[0]), None) or (work[0] if work else None)
    else:
        target = next((s for s in order if s["id"] == to), None)
        target = target or next((s for s in order if s["name"].lower() == str(to).strip().lower()), None)
    if not target:
        _refuse(conn, by, "task_no_such_stage", "No such stage in this pipeline", row["id"])
    if target["id"] == here["id"]:
        return None, None
    if ended:
        if target["kind"] != "work":
            _refuse(conn, by, "task_ended", f"The task is {here['name']}. Reopen it first.", row["id"])
        return target, "reopen"
    if target["kind"] == "closed":
        return target, "closed"
    if target["kind"] == "done":
        return target, "done"
    positions = {s["id"]: i for i, s in enumerate(work)}
    return target, "forward" if positions[target["id"]] > positions[here["id"]] else "back"


def _skipped(conn: "sqlite3.Connection", row: Row, target: Row) -> list[R.Standing]:
    """The requirements of every work stage a forward move jumps over, each as not met."""
    work = [s for s in stages(conn, row["pipeline_id"]) if s["kind"] == "work"]
    at = next(i for i, s in enumerate(work) if s["id"] == row["stage_id"])
    end = next((i for i, s in enumerate(work) if s["id"] == target["id"]), len(work))
    out: list[R.Standing] = []
    for between in work[at + 1 : end]:
        for requirement in R.on_stage(conn, between["id"]):
            out.append(
                R.Standing(requirement, R.NOT_MET, None, f"{between['name']} was skipped: {R.name_of(requirement)}")
            )
    return out


def _may_push_past(conn: "sqlite3.Connection", by: str, row: Row, why: str | None, missing: list[R.Standing]) -> None:
    """Hold a forward move to what is missing. With a reason, anyone pushes past, except a requirement whose switch is
    off, or a people-only OK when anyone but a person tries. Refuses, or returns. Writes nothing."""
    for standing in missing:
        requirement = standing.requirement
        if not (why or "").strip():
            _refuse(conn, by, "task_not_met", f"Not met: {standing.name}. {standing.line}".strip(), row["id"])
        if not R.can_push_past(requirement):
            _refuse(
                conn, by, "task_no_push_past", f"{standing.name} can't be pushed past. Move back or close.", row["id"]
            )
        if R.people_only(requirement) and not is_human(by):
            _refuse(conn, by, "task_needs_person", f"{standing.name} needs a person", row["id"])


def _push_past(
    conn: "sqlite3.Connection", by: str, row: Row, why: str | None, missing: list[R.Standing], ts: str
) -> list[Row]:
    """Record what a forward move pushed past: one answer per requirement of this visit. Returns what was pushed past."""
    H = _h()
    pushed: list[Row] = []
    for standing in missing:
        requirement = standing.requirement
        if requirement.get("stage_id") == row["stage_id"] or requirement.get("visit_id"):
            conn.execute(
                "INSERT INTO answers (id, visit_id, requirement_id, by_id, answer, notes, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (H.new_id(), row["visit"]["id"], requirement["id"], str(by), "pushed_past", why, ts),
            )
        pushed.append({"id": requirement["id"], "name": standing.name})
    return pushed


def move(
    conn: "sqlite3.Connection",
    task_id: str,
    to: str,
    by: str,
    why: str | None = None,
    seen_version: int | str | None = None,
    *,
    cascade: bool = False,
) -> Row:
    """Move a task: forward, push past, back, done, close or reopen. `to` is a stage id or name, or NEXT, DONE, CLOSED
    or REOPEN. A forward move is held to what the stage needs and to room; with `why` it pushes past. Back, close and
    reopen are never held. `cascade` is the engine closing a task with the one that filed it."""
    H = _h()
    row = task(conn, task_id)
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    _check_version(conn, by, row, seen_version, cascade)
    target, kind = _target(conn, by, row, to)
    if not target or kind is None:
        return row
    ts = H.now()
    visit = row["visit"]
    pushed: list[Row] = []
    if kind in ("forward", "done"):
        missing = R.unmet(conn, row) + _skipped(conn, row, target)
        _may_push_past(conn, by, row, why, missing)
        if target["kind"] == "work" and not has_room(conn, target):
            _refuse(conn, by, "task_stage_full", f"No room in {target['name']}", task_id)
        pushed = _push_past(conn, by, row, why, missing, ts)
    elif kind == "reopen":
        _need_why(conn, by, why, "reopen it", task_id, everyone=True)
    else:
        _need_why(conn, by, why, "close it" if kind == "closed" else "move it back", task_id)
    if kind in ("back", "closed"):
        stage_effects.undo(conn, row, by, f"Closed with {row.get('title') or 'the task that filed it'}")
    if kind == "closed":
        _close_under(conn, task_id, by, f"Closed with {row.get('title') or 'the task that filed it'}")
    how = {"forward": "met", "done": "met", "back": "back", "closed": "closed", "reopen": "reopened"}[kind]
    if pushed:
        how = "pushed_past"
    _close_visit(conn, visit, how, by, why, ts)
    if target["kind"] != "work" and row.get("snooze"):
        _end_snooze(conn, row["snooze"], by, ts)
    owner = owner_for(target, row.get("assignee_id"), row.get("owner_id"))
    if owner != row.get("owner_id"):
        task_wakes.drop(conn, task_id, row.get("owner_id"))
    status = {"work": "open", "done": "done", "closed": "closed"}[target["kind"]]
    extra: Row = {}
    if "done_at" in row:
        extra["done_at"] = ts if status == "done" else None
    if "closed_at" in row:
        extra["closed_at"] = ts if status == "closed" else None
    if "closed_by_id" in row:
        extra["closed_by_id"] = str(by) if status == "closed" else None
    _write(
        conn,
        task_id,
        stage_id=target["id"],
        owner_id=owner,
        status=status,
        version=int(row.get("version") or 0) + 1,
        updated_at=ts,
        **extra,
    )
    _open_visit(conn, task_id, target["id"], how, ts)
    _event(
        conn,
        task_id,
        by,
        "move",
        row["stage_id"],
        target["id"],
        why,
        {
            "move": "pushed_past" if pushed else kind,
            "owner": owner,
            "was_owner": row.get("owner_id"),
            **({"pushed_past": pushed} if pushed else {}),
        },
    )
    moved = _task(conn, task_id)
    if target["kind"] == "work":
        stage_effects.enter(conn, moved, by)
        _wake_turn(conn, moved)
    elif not cascade:
        _ended(conn, moved, by)
    if pushed:
        _tell_manager(conn, moved, f"Pushed past {', '.join(p['name'] for p in pushed)}: {why}", "pushed_past")
    return moved


def _close_under(conn: "sqlite3.Connection", task_id: str, by: str, note: str) -> None:
    """Close every open task under `task_id`, however deep: a closed task leaves no open work behind it. An ended task
    in between stays as it ended, and what is open under it closes too."""
    children = conn.execute(
        "SELECT t.id, t.status FROM links l JOIN tasks t ON t.id = l.to_task_id "
        "WHERE l.from_task_id=? ORDER BY l.created_at, l.id",
        (task_id,),
    ).fetchall()
    for child, status in children:
        if status == "open":
            move(conn, child, CLOSED, by, note, cascade=True)
        else:
            _close_under(conn, child, by, note)


def _ended(conn: "sqlite3.Connection", row: Row, by: str) -> None:
    """A task ended. If a task filed it and that was the last one open from that visit, the filing task's owner is woken
    once with how each ended, and the filing task moves on if that was all it needed."""
    link = _h()._one(conn, "SELECT * FROM links WHERE to_task_id=?", (row["id"],))
    if not link or not link.get("visit_id"):
        return
    above = task(conn, link["from_task_id"])
    if not above or above["status"] != "open" or _part(above, "visit").get("id") != link["visit_id"]:
        return
    if stage_effects.filed_open(conn, link["visit_id"]):
        return
    if advance(conn, above["id"]):
        return
    filed = conn.execute(
        "SELECT t.title, t.status FROM links l JOIN tasks t ON t.id = l.to_task_id WHERE l.visit_id=? "
        "ORDER BY l.created_at, l.id",
        (link["visit_id"],),
    ).fetchall()
    lead = "The tasks this one filed have ended: " + "; ".join(
        f"{title or 'A task'}: {'Done' if status == 'done' else 'Closed'}" for title, status in filed
    )
    owner = above.get("owner_id")
    if is_bot(owner) and not above.get("snooze"):
        task_wakes.add(
            conn,
            above,
            owner,
            above["visit"],
            task_wakes.body(conn, above, lead),
            wake_key=f"{above['id']}:{above['visit']['id']}:filed:{row['id']}",
        )


def _tell_manager(conn: "sqlite3.Connection", row: Row, text: str, what: str) -> str | None:
    """One quiet note to the pipeline's manager bot, once per visit and kind of news. Nothing runs for it."""
    manager = _part(row, "pipeline").get("manager_id")
    if not is_bot(manager) or manager == row.get("owner_id"):
        return None
    return task_wakes.add(
        conn,
        row,
        manager,
        row["visit"],
        f"{row.get('title') or 'A task'}: {text}",
        wake_key=f"{row['id']}:{row['visit']['id']}:{what}",
        quiet=True,
    )


def advance(conn: "sqlite3.Connection", task_id: str) -> Row | None:
    """Forward, by the engine: move the task to its next stage if everything is met and there is room. Returns the moved
    task, or None when it stays."""
    row = task(conn, task_id)
    if not row or row["status"] != "open" or row.get("snooze") or row["stage"].get("by_hand"):
        return None
    target = next_stage(conn, row)
    if not target or R.unmet(conn, row) or not has_room(conn, target):
        return None
    return move(conn, task_id, target["id"], SYSTEM, seen_version=row.get("version"))


def fill_open_slots(conn: "sqlite3.Connection", stage_ids: Sequence[str] | None = None) -> list[str]:
    """Move every task that is ready and has room, oldest first. Run right after a move frees a slot, and by the
    never-stuck check. Returns the ids of the tasks it moved."""
    sql = (
        "SELECT t.id FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
        "JOIN stages s ON s.id = t.stage_id "
        "WHERE t.pipeline_id IS NOT NULL AND t.status='open' AND s.kind='work' AND s.by_hand=0 "
        "AND NOT EXISTS (SELECT 1 FROM snoozes z WHERE z.task_id = t.id AND z.ended_at IS NULL)"
    )
    args: tuple[str, ...] = ()
    if stage_ids:
        sql += " AND t.stage_id IN (" + ",".join("?" * len(stage_ids)) + ")"
        args = tuple(stage_ids)
    moved: list[str] = []
    for (task_id,) in conn.execute(sql + " ORDER BY v.entered_at, v.id", args).fetchall():
        if advance(conn, task_id):
            moved.append(task_id)
    return moved


# ----------------------------------------------------------------------------- reassign
def reassign(
    conn: "sqlite3.Connection",
    task_id: str,
    owner: str | None,
    by: str,
    why: str | None = None,
    seen_version: int | str | None = None,
) -> Row:
    """Change who owns the task for this visit. Same writer, so it is logged and raises the version. It resets the
    tries, drops the old owner's queued wakes for the task and wakes the new owner. In a stage owned by "the task's
    assignee", the assignee changes with it."""
    H = _h()
    row = task(conn, task_id)
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    _check_version(conn, by, row, seen_version)
    if row["status"] != "open":
        _refuse(conn, by, "task_ended", f"The task is {row['stage']['name']}. Reopen it first.", task_id)
    owner = owner or None
    if owner == row.get("owner_id"):
        return row
    ts = H.now()
    fields: Row = {"owner_id": owner, "version": int(row.get("version") or 0) + 1, "updated_at": ts}
    if row["stage"].get("owner") == "assignee":
        fields["assignee_id"] = owner
    task_wakes.drop(conn, task_id, row.get("owner_id"))
    _write(conn, task_id, **fields)
    conn.execute(
        "UPDATE visits SET tries=0, level='owner', person_id=NULL, next_try_at=NULL, last_change_at=? WHERE id=?",
        (ts, row["visit"]["id"]),
    )
    _event(conn, task_id, by, "reassign", row.get("owner_id"), owner, why, {"stage": row["stage_id"]})
    moved = _task(conn, task_id)
    if is_bot(owner) and not moved.get("snooze") and not moved["stage"].get("by_hand"):
        task_wakes.add(
            conn,
            moved,
            owner,
            moved["visit"],
            task_wakes.body(conn, moved, "This task is yours now." + (f" {why}" if why else "")),
            wake_key=f"{task_id}:{moved['visit']['id']}:owner:v{moved['version']}",
        )
    return moved


# ----------------------------------------------------------------------------- snooze
def _end_snooze(conn: "sqlite3.Connection", snoozed: Row, by: str, ts: str) -> None:
    conn.execute(
        "UPDATE snoozes SET ended_at=?, ended_by_id=? WHERE id=? AND ended_at IS NULL", (ts, str(by), snoozed["id"])
    )


def snooze(
    conn: "sqlite3.Connection",
    task_id: str,
    by: str,
    why: str | None = None,
    until: str | None = None,
    seen_version: int | str | None = None,
) -> Row:
    """Set a task aside until a date, or with no end. A bot gives a reason. While snoozed nothing wakes for it, it is not
    Late, it holds no slot and it leaves Needs you. A bot's fourth snooze in one stage is not taken: the manager bot gets
    the task instead, and the result's `note` says so."""
    H = _h()
    row = task(conn, task_id)
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    if seen_version is not None:
        _check_version(conn, by, row, seen_version)
    if row["status"] != "open":
        _refuse(conn, by, "task_ended", f"The task is {row['stage']['name']}. Reopen it first.", task_id)
    _need_why(conn, by, why, "snooze it", task_id)
    ts = H.now()
    visit = row["visit"]
    if is_bot(by) and int(visit.get("bot_snoozes") or 0) >= BOT_SNOOZES:
        conn.execute(
            "UPDATE visits SET level='manager', tries=0, next_try_at=?, last_change_at=? WHERE id=? AND level='owner'",
            (ts, ts, visit["id"]),
        )
        _event(conn, task_id, by, "snooze_refused", None, None, why, {"snoozes": visit.get("bot_snoozes")})
        out = _task(conn, task_id)
        out["note"] = f"Snoozed {BOT_SNOOZES} times in this stage. The manager has it now."
        return out
    if row.get("snooze"):
        _end_snooze(conn, row["snooze"], by, ts)
    conn.execute(
        "INSERT INTO snoozes (id, task_id, visit_id, by_id, why, until_at, created_at) VALUES (?,?,?,?,?,?,?)",
        (H.new_id(), task_id, visit["id"], str(by), why, until, ts),
    )
    if is_bot(by):
        conn.execute("UPDATE visits SET bot_snoozes = bot_snoozes + 1 WHERE id=?", (visit["id"],))
    task_wakes.drop(conn, task_id, row.get("owner_id"))
    _event(conn, task_id, by, "snooze", None, until, why)
    out = _task(conn, task_id)
    if not until and is_bot(by):
        _tell_manager(conn, out, f"On ice, with no end date: {why}", f"on_ice:{out['snooze']['id']}")
    return out


def unsnooze(conn: "sqlite3.Connection", task_id: str, by: str, why: str | None = None) -> Row:
    """End a task's snooze. The owner is woken once. It can leave the stage one over its limit."""
    H = _h()
    row = task(conn, task_id)
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    if not row.get("snooze"):
        return row
    ts = H.now()
    _end_snooze(conn, row["snooze"], by, ts)
    conn.execute("UPDATE visits SET last_change_at=? WHERE id=?", (ts, row["visit"]["id"]))
    _event(conn, task_id, by, "unsnooze", row["snooze"].get("until_at"), None, why)
    out = _task(conn, task_id)
    owner = out.get("owner_id")
    if is_bot(owner) and out["stage"]["kind"] == "work" and not out["stage"].get("by_hand"):
        task_wakes.add(
            conn,
            out,
            owner,
            out["visit"],
            task_wakes.body(conn, out, "The snooze on this task ended."),
            wake_key=f"{task_id}:{out['visit']['id']}:unsnooze:{row['snooze']['id']}",
        )
    return out


# ----------------------------------------------------------------------------- a requirement on one task
def add_requirement(
    conn: "sqlite3.Connection",
    task_id: str,
    by: str,
    kind: str,
    person: str | None,
    line: str,
    *,
    choices: list[str] | None = None,
    shows: object = None,
    seen_version: int | str | None = None,
) -> Row:
    """Add an OK or an answer from a named person to this task, for this visit (C30). The task does not leave the stage
    until it is answered, withdrawn or pushed past. A bot has one open per task: asking again replaces it (C15)."""
    H = _h()
    row = task(conn, task_id)
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    if seen_version is not None:
        _check_version(conn, by, row, seen_version)
    if row["status"] != "open":
        _refuse(conn, by, "task_ended", f"The task is {row['stage']['name']}. Reopen it first.", task_id)
    if kind not in ("ok", "answer"):
        _refuse(conn, by, "task_requirement", "Only an OK or an answer can be added to one task", task_id)
    if not (is_human(person) or is_bot(person)):
        _refuse(conn, by, "task_requirement", "Name who it is for", task_id)
    if not (line or "").strip():
        _refuse(conn, by, "task_requirement", "Say what you are asking, in one sentence", task_id)
    ts = H.now()
    visit = row["visit"]
    if is_bot(by):
        conn.execute(
            "UPDATE requirements SET withdrawn_at=? WHERE visit_id=? AND added_by_id=? AND by_bot=1 "
            "AND withdrawn_at IS NULL",
            (ts, visit["id"], str(by)),
        )
    requirement_id = H.new_id()
    conn.execute(
        "INSERT INTO requirements (id, visit_id, kind, who_oks, person_id, added_by_id, by_bot, line, shows, choices, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            requirement_id,
            visit["id"],
            kind,
            "people_with_exceptions",
            person,
            str(by),
            1 if is_bot(by) else 0,
            line.strip(),
            None if shows is None else json.dumps(shows),
            None if choices is None else json.dumps(choices),
            ts,
        ),
    )
    conn.execute("UPDATE visits SET last_change_at=? WHERE id=?", (ts, visit["id"]))
    _event(conn, task_id, by, "requirement", None, requirement_id, line.strip(), {"kind": kind, "person": person})
    out = _task(conn, task_id)
    out["requirement_id"] = requirement_id
    return out


def withdraw_requirement(
    conn: "sqlite3.Connection", requirement_id: str, by: str, why: str | None = None
) -> Row | None:
    """Take back a requirement that was added to one task. The task moves on if that was all it waited for."""
    H = _h()
    requirement = R.get(conn, requirement_id)
    if not requirement or not requirement.get("visit_id"):
        _refuse(conn, by, "not-found", "No such requirement on a task")
    visit = H._one(conn, "SELECT * FROM visits WHERE id=?", (requirement["visit_id"],))
    if requirement.get("withdrawn_at") or not visit or visit.get("left_at"):
        return task(conn, visit["task_id"]) if visit else None
    ts = H.now()
    conn.execute("UPDATE requirements SET withdrawn_at=? WHERE id=?", (ts, requirement_id))
    conn.execute("UPDATE visits SET last_change_at=? WHERE id=?", (ts, visit["id"]))
    _event(conn, visit["task_id"], by, "requirement_withdrawn", requirement_id, None, why)
    return advance(conn, visit["task_id"]) or task(conn, visit["task_id"])
