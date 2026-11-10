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

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, NoReturn

from . import hubdb_core, stage_effects, task_effects
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
FIRST_TRY_S = 120  # seconds after a wake before the never-stuck check looks at a bot that stopped early
GUARDED = ("status", "stage_id", "owner_id", "pipeline_id")


def _h() -> Any:
    from . import hubdb

    return hubdb


def _refuse(
    conn: "sqlite3.Connection",
    by: str,
    rule: str,
    detail: str,
    task_id: str | None = None,
    *,
    row: Row | None = None,
    missing: Sequence[str] = (),
    then: Sequence[str] = (),
    version: object = None,
    kept: tuple[str, Sequence[str]] | None = None,
    to: str | None = None,
) -> NoReturn:
    """Refuse, with what the caller needs to act on it: what is `missing`, the commands allowed next (`then`, by name,
    for `row`: `_then`) and the task's `version`. They go out with the refusal (store.refused), so a bot is never left
    to guess. `kept` is the detail and what is missing as kept on record (`_said` for ON_RECORD), when they name less
    than the caller is told. `to` is the stage a held forward move was going to.

    The refusal carries `again`: what `refusal_extra` needs to say `missing` and `next` again from the task as it is
    then, for a replay whose receipt could not keep them (store.replayed_refusal). Ids and names only."""
    H = _h()
    extra: Row = {}
    if missing:
        extra["missing"] = list(missing)
    if then and row is not None:
        extra["next"] = _then(row, *then)
    if version is not None:
        extra["version"] = int(str(version) or 0)
    stored: Row | None = None
    if kept is not None and (kept[0] != detail or list(kept[1]) != list(missing)):
        stored = {"detail": kept[0], "extra": {**extra, **({"missing": list(kept[1])} if kept[1] else {})}}
    again: Row | None = None
    if row is not None and (then or missing):
        again = {"task": row["id"], "then": list(then), "missing": bool(missing), "to": to}
    try:
        H.refuse(conn, by, rule, detail, task=task_id, extra=extra, stored=stored)
    except H.Refused as refused:
        refused.again = again
        raise
    raise AssertionError("refuse() always raises")  # pragma: no cover


# The commands a refusal may offer, by name (`_then`).
THEN = ("push", "back", "ask", "file", "snooze", "close", "show")


def _then(row: Row, *which: str) -> list[str]:
    """The commands a refusal offers, for this task at its version: `push` (go on anyway), `back`, `ask`, `file`,
    `snooze`, `close`, `show` (THEN)."""
    ident, version = row["id"], row.get("version")
    said = {
        "push": f'hub task move {ident} next -m "why" --version {version}',
        "back": f'hub task move {ident} <earlier stage> -m "why" --version {version}',
        "ask": f'hub task ask {ident} --to <person> "one sentence" --version {version}',
        "file": f'hub task file --parent {ident} "title"',
        "snooze": f'hub task snooze {ident} -m "why" --version {version}',
        "close": f'hub task close {ident} -m "why" --version {version}',
        "show": f"hub task show {ident}",
    }
    return [said[name] for name in which]


def refusal_extra(conn: "sqlite3.Connection", viewer: str, again: Row) -> Row | None:
    """What a refusal's caller needs to act on it, said again from the task as it is now: its `version`, the commands
    allowed `next` and what is `missing`, by the code that first said them (`_refuse`, `_said`). For a replay of the
    write it refused, whose receipt kept no private words (store.replayed_refusal). `again` is what the refusal
    carried. None when `viewer` may not read the task now: the replay gives what its receipt has."""
    H = _h()
    row = task(conn, str(again.get("task") or ""))
    if not row or not H.task_private_readable(conn, viewer, row):
        return None
    out: Row = {"version": int(row.get("version") or 0)}
    names = [str(name) for name in again.get("then") or () if name in THEN]
    if names:
        out["next"] = _then(row, *names)
    if again.get("missing") and row["status"] == "open" and row.get("visit") and row["stage"]["kind"] == "work":
        target = stage(conn, str(again["to"])) if again.get("to") else None
        missing = R.unmet(conn, row)
        if target and target["pipeline_id"] == row["pipeline_id"]:
            missing += _skipped(conn, row, target)
        out["missing"] = _lines(missing, _said(conn, viewer, row, missing))
    return out


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
        "SELECT COUNT(*) FROM tasks t WHERE t.pipeline_id IS NOT NULL AND t.stage_id=? AND t.status='open' "
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
    statement on this one task, and closed again before anything else runs, inside the caller's transaction."""
    names = list(fields)
    if set(names) - WRITES:
        raise ValueError(f"not a column the engine writes: {sorted(set(names) - WRITES)}")
    sets = ", ".join(f"{name}=?" for name in names)
    conn.execute("INSERT OR REPLACE INTO task_move_guards (id, open, task_id) VALUES (1, 1, ?)", (task_id,))
    try:
        conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", (*[fields[n] for n in names], task_id))
    finally:
        conn.execute("UPDATE task_move_guards SET open=0, task_id=NULL WHERE id=1")


def _open_visit(conn: "sqlite3.Connection", task_id: str, stage_id: str, how: str, ts: str) -> str:
    H = _h()
    visit_id = H.new_id()
    conn.execute(
        "INSERT INTO visits (id, task_id, stage_id, entered_at, entered_how, last_change_at, next_try_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (visit_id, task_id, stage_id, ts, how, ts, H.shift(ts, seconds=FIRST_TRY_S)),
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


def check_version(
    conn: "sqlite3.Connection", by: str, row: Row, seen_version: int | str | None, cascade: bool = False
) -> None:
    """Refuse a write made against an old version of the task. A bot always says which version it saw; a person may
    leave it out. `cascade` is the engine closing a task with the one that filed it."""
    if cascade:
        return
    if seen_version is None:
        if is_bot(by):
            _refuse(
                conn,
                by,
                "task_changed",
                "Say which version of the task you saw",
                row["id"],
                row=row,
                then=("show",),
                version=row.get("version"),
            )
        return
    if int(seen_version) != int(row.get("version") or 0):
        _refuse(
            conn,
            by,
            "task_changed",
            "The task changed since you looked. Read it again.",
            row["id"],
            row=row,
            then=("show",),
            version=row.get("version"),
        )


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
) -> str:
    """One row of the task's history; returns its id. Through hubdb's writer, so a change made as part of a Strategy
    Bot decision or by an Assistant for a person is marked as such."""
    return str(_h()._task_event(conn, task_id, by, kind, old, new, note or "", data=data))


def _turn_of(conn: "sqlite3.Connection", row: Row) -> tuple[str | None, str]:
    """The one bot to wake about an open task now, as the never-stuck ladder picks it (`waiting_on.turn`), and the
    lead its wake starts with when it is not the owner: a stage with nobody set wakes the manager bot to pick someone
    (C25); a bot named on an OK is told what it is asked. None when it is nobody's turn, or a person's."""
    from . import waiting_on as W

    if row["status"] != "open" or row.get("snooze") or row["stage"].get("by_hand"):
        return None, ""
    waits = W.of(conn, row)
    target = W.turn(conn, row, waits, paused=True)
    if target and not row.get("owner_id") and target == _part(row, "pipeline").get("manager_id"):
        return target, "This task has no owner. Pick one and reassign it."
    asked = next((w for w in waits if w.who == target and w.ask), None) if target != row.get("owner_id") else None
    return target, f"You are asked: {asked.line}" if asked else ""


def _wake_turn(
    conn: "sqlite3.Connection",
    row: Row,
    lead: str = "",
    *,
    refs: Row | None = None,
    trigger: str | None = None,
    source: str | None = None,
) -> str | None:
    """Tell whoever has the turn in a stage the task just entered, once per visit: the bot the ladder would wake, and
    each other bot an OK or an answer names while it has a turn too (its ask's one wake: task_wakes.ask). A person is
    not told: their list is a read. After that the ladder tries only its own bot. `lead` opens the wake when the turn's
    own lead says nothing; `refs`, `trigger` and `source` are a new task's own (`enter`)."""
    from . import waiting_on as W

    if row["stage"]["kind"] != "work":
        return None
    target, turn_lead = _turn_of(conn, row)
    if not target:
        return None
    out = task_wakes.add(
        conn,
        row,
        target,
        row["visit"],
        task_wakes.body(conn, row, turn_lead or lead),
        refs=refs,
        trigger=trigger,
        source=source,
    )
    for wait in W.of(conn, row):
        if wait.state in (W.BOT, W.PAUSED) and wait.ask and wait.requirement_id and wait.who != target:
            task_wakes.ask(conn, row, R.get(conn, wait.requirement_id), wait.who)
    return out


def _drop_stale(conn: "sqlite3.Connection", task_id: str) -> int:
    """The turn may have gone to someone else: cancel the wakes still queued for a bot that has no turn on the task
    now, and keep those of a bot that still has one, a bot named on an OK or an answer included (R2). A snoozed or
    ended task, or one at level `person`, keeps none."""
    from . import waiting_on as W

    row = task(conn, task_id)
    if not row or row["status"] != "open" or row.get("snooze"):
        return task_wakes.drop(conn, task_id)
    return task_wakes.drop(conn, task_id, keep=W.turns(conn, row))


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
    wake: Row | None = None,
    trigger: str | None = None,
    source: str | None = None,
) -> Row:
    """New. Put a task that is not on the engine yet into its pipeline's first stage, open its visit and wake the
    owner. `parent` links it to the task that filed it, on that task's open visit. `wake` (what the owner bot's run
    should know: the other work to reconcile with, an automation's run and step), `trigger` and `source` go on the
    owner's wake."""
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
    _owned(conn, row, _task(conn, task_id), by)
    entered = _task(conn, task_id)
    stage_effects.enter(conn, entered, by)
    _wake_turn(conn, entered, refs=wake, trigger=trigger, source=source)
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


def _said(conn: "sqlite3.Connection", viewer: str | None, row: Row, missing: Sequence[R.Standing]) -> list[str]:
    """How each missing requirement stands, said to `viewer`: a sub-task they may not read is not named, as task_view
    does not name it (task_calls.standing_line). None is the engine (nothing hidden); task_calls.ON_RECORD is what is
    kept on record."""
    from . import task_calls

    hidden = task_calls.hidden_filed(conn, row, viewer)
    return [task_calls.standing_line(each, hidden) for each in missing]


def _lines(missing: Sequence[R.Standing], said: Sequence[str]) -> list[str]:
    """What is missing, one line each: the requirement's name and how it stands."""
    return [
        f"{each.name}: {line}" if line and line != each.name else each.name
        for each, line in zip(missing, said, strict=True)
    ]


def _may_push_past(
    conn: "sqlite3.Connection", by: str, row: Row, why: str | None, missing: list[R.Standing], to: str | None = None
) -> None:
    """Hold a forward move to what is missing. With a reason, anyone pushes past, except a requirement whose switch is
    off, or a people-only OK when anyone but a person tries. Refuses, or returns. Writes nothing. The words are said
    to `by`, and kept on record naming no private sub-task."""
    from . import task_calls

    said = _said(conn, None if by == SYSTEM else by, row, missing)
    on_record = _said(conn, task_calls.ON_RECORD, row, missing)
    lines, kept_lines = _lines(missing, said), _lines(missing, on_record)
    version = row.get("version")
    for at, standing in enumerate(missing):
        requirement = standing.requirement
        if not (why or "").strip():
            _refuse(
                conn,
                by,
                "task_not_met",
                f"Not met: {standing.name}. {said[at]}".strip(),
                row["id"],
                missing=lines,
                row=row,
                then=("push", "ask", "file", "snooze", "close"),
                version=version,
                kept=(f"Not met: {standing.name}. {on_record[at]}".strip(), kept_lines),
                to=to,
            )
        if not R.can_push_past(requirement):
            _refuse(
                conn,
                by,
                "task_no_push_past",
                f"{standing.name} can't be pushed past. Move back or close.",
                row["id"],
                missing=lines,
                row=row,
                then=("back", "ask", "snooze", "close"),
                version=version,
                kept=(f"{standing.name} can't be pushed past. Move back or close.", kept_lines),
                to=to,
            )
        if R.people_only(requirement) and not is_human(by):
            _refuse(
                conn,
                by,
                "task_needs_person",
                f"{standing.name} needs a person",
                row["id"],
                missing=lines,
                row=row,
                then=("ask", "snooze", "close"),
                version=version,
                kept=(f"{standing.name} needs a person", kept_lines),
                to=to,
            )


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


def _may_close(conn: "sqlite3.Connection", by: str, row: Row) -> None:
    """A bot can't close a person's request by itself (C8): a task a person asked for that no task filed. It asks the
    person why it can't be done, and they close it or reassign it. A person closes anything; a bot closes what a bot
    asked for and what a task filed."""
    requester = row.get("requester_id")
    if not is_bot(by) or not is_human(requester):
        return
    if conn.execute("SELECT 1 FROM links WHERE to_task_id=?", (row["id"],)).fetchone():
        return
    name = _h().actor_name(conn, requester)
    _refuse(
        conn,
        by,
        "task_needs_person",
        f"{name} asked for this task, so only a person can close it. Ask {name}, in one sentence, why it can't be "
        "done: they will close it or reassign it.",
        row["id"],
        row=row,
        then=("ask", "snooze"),
        version=row.get("version"),
    )


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
    check_version(conn, by, row, seen_version, cascade)
    target, kind = _target(conn, by, row, to)
    if not target or kind is None:
        return row
    ts = H.now()
    visit = row["visit"]
    pushed: list[Row] = []
    if kind in ("forward", "done"):
        missing = R.unmet(conn, row) + _skipped(conn, row, target)
        _may_push_past(conn, by, row, why, missing, target["id"])
        if target["kind"] == "work" and not has_room(conn, target):
            _refuse(
                conn,
                by,
                "task_stage_full",
                f"No room in {target['name']}. The task moves in when there is room.",
                task_id,
                row=row,
                then=("snooze", "close"),
                version=row.get("version"),
            )
        pushed = _push_past(conn, by, row, why, missing, ts)
    elif kind == "reopen":
        _need_why(conn, by, why, "reopen it", task_id, everyone=True)
    else:
        _need_why(conn, by, why, "close it" if kind == "closed" else "move it back", task_id)
    if kind == "closed" and not cascade:
        _may_close(conn, by, row)
    if kind in ("back", "closed"):
        stage_effects.undo(conn, row, by, f"Closed with {row.get('title') or 'the task that filed it'}")
    if kind == "closed":
        _close_under(conn, task_id, by, f"Closed with {row.get('title') or 'the task that filed it'}")
    how = {"forward": "met", "done": "met", "back": "back", "closed": "closed", "reopen": "reopened"}[kind]
    if pushed:
        how = "pushed_past"
    _close_visit(conn, visit, how, by, why, ts)
    if row.get("snooze"):
        _end_snooze(conn, row["snooze"], by, ts)  # a snooze sets a task aside in one stage: it ends with the visit
    owner = owner_for(target, row.get("assignee_id"), row.get("owner_id"))
    task_wakes.drop(conn, task_id)  # what was queued was about the visit that just closed
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
    revision = _event(
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
    _owned(conn, row, _task(conn, task_id), by)
    moved = _task(conn, task_id)
    if target["kind"] == "work":
        stage_effects.enter(conn, moved, by)
        _wake_turn(conn, moved)
    elif not cascade:
        _ended(conn, moved, by)
    if pushed:
        _tell_manager(conn, moved, f"Pushed past {', '.join(p['name'] for p in pushed)}: {why}", "pushed_past")
    # What a move causes beyond the task's own row: the events, a routine's day, stopping work on a closed task
    # (backend/task_effects.py). Last, and for every move, so nothing that moves a task can leave them out.
    task_effects.moved(conn, row, moved, kind, by, why, revision)
    return _task(conn, task_id)


def _owned(conn: "sqlite3.Connection", before: Row, after: Row, by: str) -> None:
    """Who has the task now comes first, before anyone is woken or anything hears of the move: what follows a new
    owner, its private default among it (task_effects.owner_moved), then, on a private task in a work stage, everyone
    the stage asks is brought in to read it (task_calls.bring_in_asked). The caller reads the task again after."""
    from . import task_calls

    task_effects.owner_moved(conn, before, after, by)
    if after["stage"]["kind"] == "work":
        task_calls.bring_in_asked(conn, after["id"], by)


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
    _reset_ladder(conn, above["visit"], _h().now(), by)
    above = _task(conn, above["id"])
    target, _ = _turn_of(conn, above)
    if target:
        task_wakes.add(
            conn,
            above,
            target,
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
    """Move every task that is ready and has room, oldest first. Run when a pipeline's settings change a limit. The
    never-stuck check does the same for every task on each pass, so a freed slot is filled within 10 seconds. Returns
    the ids of the tasks it moved."""
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
def _may_name(conn: "sqlite3.Connection", by: str, actor: str | None, task_id: str) -> None:
    """Refuse naming someone who cannot act: a person who left, was removed or cannot sign in, a bot that does not
    exist, or anything that is not a person or a bot. Nobody (None) is not refused here."""
    if actor is None:
        return
    if is_human(actor):
        if not R.active_person(conn, actor):
            _refuse(conn, by, "task_requirement", "That person has left or is not here. Ask someone who is.", task_id)
        return
    if is_bot(actor):
        if not _h().bot(conn, actor):
            _refuse(conn, by, "task_requirement", "There is no such bot here", task_id)
        return
    _refuse(conn, by, "task_requirement", "Name a person or a bot", task_id)


def reassign(
    conn: "sqlite3.Connection",
    task_id: str,
    owner: str | None,
    by: str,
    why: str | None = None,
    seen_version: int | str | None = None,
) -> Row:
    """Change who owns the task for this visit. Same writer, so it is logged and raises the version. It resets the
    tries, drops the old owner's queued wakes for the task (a bot that still has a turn, named on an OK, keeps its
    own) and wakes the new owner when the turn is theirs. In a stage owned by "the task's assignee", the assignee
    changes with it."""
    H = _h()
    row = task(conn, task_id)
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    check_version(conn, by, row, seen_version)
    if row["status"] != "open":
        _refuse(conn, by, "task_ended", f"The task is {row['stage']['name']}. Reopen it first.", task_id)
    owner = owner or None
    if owner == row.get("owner_id"):
        return row
    _may_name(conn, by, owner, task_id)
    ts = H.now()
    fields: Row = {"owner_id": owner, "version": int(row.get("version") or 0) + 1, "updated_at": ts}
    if row["stage"].get("owner") == "assignee":
        fields["assignee_id"] = owner
    _write(conn, task_id, **fields)
    _reset_ladder(conn, row["visit"], ts, by)  # the old owner's wakes go with its turn
    revision = _event(conn, task_id, by, "reassign", row.get("owner_id"), owner, why, {"stage": row["stage_id"]})
    moved = _task(conn, task_id)
    # What follows the owner: its private default, sessions, worktree, followers, place in the new owner's queue
    # (backend/task_effects.py). Before the wake, so a task that just became private wakes only who may read it.
    task_effects.reassigned(conn, row, moved, by, why, revision)
    moved = _task(conn, task_id)
    if is_bot(owner) and _turn_of(conn, moved)[0] == owner:
        task_wakes.add(
            conn,
            moved,
            owner,
            moved["visit"],
            task_wakes.body(conn, moved, "This task is yours now." + (f" {why}" if why else "")),
            wake_key=f"{task_id}:{moved['visit']['id']}:owner:v{moved['version']}",
        )
    return moved


def progress_mark(conn: "sqlite3.Connection", visit_id: str, standings: Sequence[R.Standing] | None = None) -> str:
    """What the never-stuck check compares to tell a task that changed from one that did not: the answers, added
    requirements and filed tasks of this visit, and where each requirement stands (so a PR that opened or a check that
    finished is a change). A comment is not a change. Nor is a snooze or its end: setting a task aside earns no more
    runs. Nor is the task's version: it says the row changed since someone looked, and a field edit (a new title, a
    label) raises it. A move and a reassign start the ladder again themselves. `standings` saves reading them again
    when the caller has them."""
    found = conn.execute(
        "SELECT v.task_id, "
        "(SELECT COUNT(*) FROM answers a WHERE a.visit_id = v.id), "
        "(SELECT COUNT(*) FROM answers a WHERE a.visit_id = v.id AND a.undone_at IS NOT NULL), "
        "(SELECT COUNT(*) FROM requirements r WHERE r.visit_id = v.id), "
        "(SELECT COUNT(*) FROM requirements r WHERE r.visit_id = v.id AND r.withdrawn_at IS NOT NULL), "
        "(SELECT COUNT(*) FROM links l WHERE l.visit_id = v.id), "
        "(SELECT COUNT(*) FROM links l LEFT JOIN tasks c ON c.id = l.to_task_id "
        " WHERE l.visit_id = v.id AND COALESCE(c.status, 'closed') <> 'open') "
        "FROM visits v WHERE v.id=?",
        (visit_id,),
    ).fetchone()
    if not found:
        return ""
    if standings is None:
        row = task(conn, found[0])
        standings = R.standings(conn, row) if row and _part(row, "visit").get("id") == visit_id else ()
    stand = ",".join(f"{s.requirement['id']}={s.state}:{s.on or ''}" for s in standings)
    return ".".join(str(part or 0) for part in found[1:]) + "." + hashlib.sha256(stand.encode()).hexdigest()[:12]


def _reset_ladder(conn: "sqlite3.Connection", visit: Row, ts: str, by: str) -> None:
    """The task changed: start the never-stuck ladder again. The owner has it, no tries are counted, and the check
    looks again after the first gap. Call it after the change is written, so the mark it keeps includes the change. A
    task that Late brought to a person stays with that person until a person acts. Wakes still queued for a bot that
    has no turn now are canceled (`_drop_stale`)."""
    mark = progress_mark(conn, visit["id"])
    look = _h().shift(ts, seconds=FIRST_TRY_S)
    if visit.get("level") == "person" and visit.get("late_person_at") and not is_human(by):
        conn.execute(
            "UPDATE visits SET progress_mark=?, last_change_at=?, next_try_at=? WHERE id=?",
            (mark, ts, look, visit["id"]),
        )
    else:
        conn.execute(
            "UPDATE visits SET tries=0, level='owner', person_id=NULL, next_try_at=?, last_change_at=?, progress_mark=? "
            "WHERE id=?",
            (look, ts, mark, visit["id"]),
        )
    _drop_stale(conn, visit["task_id"])


def changed(conn: "sqlite3.Connection", task_id: str, by: str) -> None:
    """Something a task waits on changed outside a move: an answer came, or was taken back. The ladder starts again.
    Whoever calls this also wakes the bot it hands the turn to."""
    row = task(conn, task_id)
    if row and row["status"] == "open":
        _reset_ladder(conn, row["visit"], _h().now(), by)


def try_again(conn: "sqlite3.Connection", task_id: str, by: str, why: str | None = None) -> Row:
    """Try again: hand a task the ladder or Late brought to a person back to its owner, with the count at zero. The
    owner is woken once. Only a person tries again: a bot that could restart the ladder could keep a task going
    round it for ever."""
    H = _h()
    row = task(conn, task_id)
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    if not is_human(by):
        _refuse(
            conn,
            by,
            "task_needs_person",
            "Try again needs a person. You can reassign it, ask a person, snooze it or close it.",
            task_id,
        )
    if row["status"] != "open":
        _refuse(conn, by, "task_ended", f"The task is {row['stage']['name']}. Reopen it first.", task_id)
    ts = H.now()
    visit = row["visit"]
    _reset_ladder(conn, visit, ts, by)
    _event(conn, task_id, by, "try_again", visit.get("level"), "owner", why)
    out = _task(conn, task_id)
    target, _ = _turn_of(conn, out)
    if target:
        lead = "Try this task again." + (f" {why}" if why else "")
        task_wakes.add(
            conn,
            out,
            target,
            out["visit"],
            task_wakes.body(conn, out, lead),
            wake_key=f"{task_id}:{visit['id']}:again:{ts}",
        )
    return out


# ----------------------------------------------------------------------------- snooze
def _end_snooze(conn: "sqlite3.Connection", snoozed: Row, by: str, ts: str) -> None:
    conn.execute(
        "UPDATE snoozes SET ended_at=?, ended_by_id=? WHERE id=? AND ended_at IS NULL", (ts, str(by), snoozed["id"])
    )


def on_delete(conn: "sqlite3.Connection", task_ids: Sequence[str], by: str) -> int:
    """The delete path (`task_delete.delete_tasks`), before the rows go: a deleted task keeps no place in a pipeline.
    Its snooze, its visits with their answers, the asks added to them and the effects they owed are removed, and its
    queued wakes are canceled. The links to and from it stay: the task that filed it reads a deleted task as closed,
    and a restore (`restore`) starts it on a new visit. Returns how many visits went."""
    ids = [str(task_id) for task_id in task_ids if task_id]
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    visits = f"SELECT id FROM visits WHERE task_id IN ({marks})"
    for task_id in ids:
        task_wakes.drop(conn, task_id)
    conn.execute(f"DELETE FROM effects WHERE visit_id IN ({visits})", ids)
    conn.execute(f"DELETE FROM answers WHERE visit_id IN ({visits})", ids)
    conn.execute(f"DELETE FROM requirements WHERE visit_id IN ({visits})", ids)
    conn.execute(f"DELETE FROM snoozes WHERE task_id IN ({marks})", ids)
    return conn.execute(f"DELETE FROM visits WHERE task_id IN ({marks})", ids).rowcount


def restore(conn: "sqlite3.Connection", task_id: str, by: str, was: Row) -> Row:
    """Place a task that came back from the trash (`task_delete.restore_tasks`): its row is open and in no stage.
    `was` is what the trash kept of where it stood: `status`, `pipeline_id`, `stage_id`, `owner_id`, `assignee_id`,
    `done_at`, `closed_at`, `closed_by_id`.

    An open task starts a new visit in the stage it was in, or the first one if that stage is gone, owned as the
    stage says, and a bot that owns it is woken. A done or closed one goes back as it ended: same owner, same times,
    nobody woken. A pipeline that is gone or retired gives General. What it was asked before is not asked again."""
    H = _h()
    row = H._one(conn, "SELECT * FROM tasks WHERE id=?", (task_id,))
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    if row.get("pipeline_id"):
        _refuse(conn, by, "task_changed", "The task is already in a pipeline", task_id)
    pipe = pipeline(conn, str(was.get("pipeline_id") or "")) if was.get("pipeline_id") else None
    if not pipe or pipe.get("retired_at"):
        pipe = pipeline(conn, H.GENERAL_PIPELINE)
    order = stages(conn, pipe["id"]) if pipe else []
    work = [s for s in order if s["kind"] == "work"]
    status = str(was.get("status") or "open")
    if status == "open":
        target = next((s for s in work if s["id"] == was.get("stage_id")), None) or (work[0] if work else None)
    else:
        target = next((s for s in order if s["kind"] == status), None)
    if not pipe or not target:
        _refuse(conn, by, "task_no_such_stage", "No pipeline to restore the task into", task_id)
    ts = H.now()
    assignee = was.get("assignee_id") or was.get("owner_id")
    fields: Row = {
        "pipeline_id": pipe["id"],
        "stage_id": target["id"],
        "status": status,
        "assignee_id": assignee,
        "updated_at": ts,
    }
    if status == "open":
        fields["owner_id"] = owner_for(target, assignee)
    else:
        fields["owner_id"] = was.get("owner_id")
        fields["done_at"] = was.get("done_at") if status == "done" else None
        fields["closed_at"] = was.get("closed_at") if status == "closed" else None
        fields["closed_by_id"] = was.get("closed_by_id") if status == "closed" else None
    _write(conn, task_id, **fields)
    how = {"open": "new", "done": "met", "closed": "closed"}[status]
    _open_visit(conn, task_id, target["id"], how, ts)
    _event(conn, task_id, by, "move", None, target["id"], "Restored", {"move": "restored", "owner": fields["owner_id"]})
    placed = _task(conn, task_id)
    if status == "open":
        stage_effects.enter(conn, placed, by)
        _wake_turn(conn, placed, "This task was restored.")
    return placed


def snooze(
    conn: "sqlite3.Connection",
    task_id: str,
    by: str,
    why: str | None = None,
    until: str | None = None,
    seen_version: int | str | None = None,
) -> Row:
    """Set a task aside until a date, or with no end. A bot gives a reason. While snoozed nothing wakes for it, it is not
    Late, it holds no slot and it leaves Needs you. A snooze is not a change: the ladder's count and level are kept,
    so setting a task aside earns no runs. A bot's fourth snooze in one stage is not taken: the manager bot gets the
    task instead, and the result's `note` says so."""
    H = _h()
    row = task(conn, task_id)
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    check_version(conn, by, row, seen_version)
    if row["status"] != "open":
        _refuse(conn, by, "task_ended", f"The task is {row['stage']['name']}. Reopen it first.", task_id)
    _need_why(conn, by, why, "snooze it", task_id)
    ts = H.now()
    visit = row["visit"]
    if is_bot(by) and int(visit.get("bot_snoozes") or 0) >= BOT_SNOOZES:
        conn.execute(
            "UPDATE visits SET level='manager', tries=0, next_try_at=? WHERE id=? AND level='owner'", (ts, visit["id"])
        )
        # The manager bot has it now: what was still queued for a bot with no turn would start runs nobody asked for.
        # A bot named on an OK or an answer that still waits keeps its wake.
        _drop_stale(conn, task_id)
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
    task_wakes.drop(conn, task_id)  # nothing wakes for a snoozed task
    _event(conn, task_id, by, "snooze", None, until, why)
    out = _task(conn, task_id)
    if not until and is_bot(by):
        _tell_manager(conn, out, f"On ice, with no end date: {why}", f"on_ice:{out['snooze']['id']}")
    return out


def unsnooze(conn: "sqlite3.Connection", task_id: str, by: str, why: str | None = None) -> Row:
    """End a task's snooze. Its end is not a change either: no wake is written here, and the ladder's count and level
    are as they were. The task is due at once, and the never-stuck check wakes whoever has the turn on its next pass,
    as the next try (at level `person` nobody is woken: it is back in that person's Needs you). It can leave the stage
    one over its limit."""
    H = _h()
    row = task(conn, task_id)
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    if not row.get("snooze"):
        return row
    ts = H.now()
    _end_snooze(conn, row["snooze"], by, ts)
    conn.execute("UPDATE visits SET next_try_at=? WHERE id=?", (ts, row["visit"]["id"]))
    _event(conn, task_id, by, "unsnooze", row["snooze"].get("until_at"), None, why)
    return _task(conn, task_id)


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
    check_version(conn, by, row, seen_version)
    if row["status"] != "open":
        _refuse(conn, by, "task_ended", f"The task is {row['stage']['name']}. Reopen it first.", task_id)
    if kind not in ("ok", "answer"):
        _refuse(conn, by, "task_requirement", "Only an OK or an answer can be added to one task", task_id)
    if not (is_human(person) or is_bot(person)):
        _refuse(conn, by, "task_requirement", "Name who it is for", task_id)
    _may_name(conn, by, person, task_id)
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
    _reset_ladder(conn, visit, ts, by)
    _event(conn, task_id, by, "requirement", None, requirement_id, line.strip(), {"kind": kind, "person": person})
    out = _task(conn, task_id)
    out["requirement_id"] = requirement_id
    # A person finds it in Needs you. A bot that is named is woken, once, when it has a turn now: not while a person
    # has the task (Late or the ladder brought it there) or it is snoozed. Then the never-stuck check wakes it once it
    # has one.
    task_wakes.ask(conn, out, R.get(conn, requirement_id))
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
    _reset_ladder(conn, visit, ts, by)
    _event(conn, visit["task_id"], by, "requirement_withdrawn", requirement_id, None, why)
    return advance(conn, visit["task_id"]) or task(conn, visit["task_id"])
