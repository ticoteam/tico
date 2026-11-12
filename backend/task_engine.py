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
import logging
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any, NoReturn, cast

from . import hubdb_core, idea_checks, stage_effects, stage_pick, task_effects
from . import task_requirements as R
from . import task_wakes, validate_gate
from .hubdb_core import KEEPER as SYSTEM

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

# hubdb_core is untyped: its actor helpers get their types here.
_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human

NEXT, DONE, CLOSED, REOPEN = "next", "done", "closed", "reopen"
LOG = logging.getLogger("tico.task_engine")
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
    said: Row | None = None,
) -> NoReturn:
    """Refuse, with what the caller needs to act on it: what is `missing`, the commands allowed next (`then`, by name,
    for `row`: `_then`) and the task's `version`. They go out with the refusal (store.refused), so a bot is never left
    to guess. `kept` is the detail and what is missing as kept on record (`_said` for ON_RECORD), when they name less
    than the caller is told. `to` is the stage a held forward move was going to. `said` is a `shut_out`: its `cause`
    and `fix` go out with the refusal.

    The refusal carries `again`: what `refusal_extra` needs to say `missing` and `next` again from the task as it is
    then, for a replay whose receipt could not keep them (store.replayed_refusal). Ids and names only."""
    H = _h()
    extra: Row = {}
    if missing:
        extra["missing"] = list(missing)
    if then and row is not None and _runs_commands(by):
        extra["next"] = _then(row, *then)
    if version is not None:
        extra["version"] = int(str(version) or 0)
    if said:
        extra["cause"], extra["fix"] = said["cause"], said["fix"]
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


def _runs_commands(by: str) -> bool:
    """Whether the caller acts through `hub` commands: a bot, or a bot acting for a person (the Assistant, BotOps).
    A person in the app is told what is missing, never a `tico task ...` line."""
    H = _h()
    return bool(is_bot(by) or H.VIA.get())


# The commands a refusal may offer, by name (`_then`).
THEN = ("push", "back", "ask", "file", "snooze", "close", "show")


def _then(row: Row, *which: str) -> list[str]:
    """The commands a refusal offers, for this task at its version: `push` (go on anyway), `back`, `ask`, `file`,
    `snooze`, `close`, `show` (THEN)."""
    ident, version = row["id"], row.get("version")
    said = {
        "push": f'tico task move {ident} next -m "why" --version {version}',
        "back": f'tico task move {ident} <earlier stage> -m "why" --version {version}',
        "ask": f'tico task ask {ident} --to <person> "one sentence" --version {version}',
        "file": f'tico task file --parent {ident} "title"',
        "snooze": f'tico task snooze {ident} -m "why" --version {version}',
        "close": f'tico task close {ident} -m "why" --version {version}',
        "show": f"tico task show {ident}",
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
    if names and _runs_commands(viewer):
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


_STAGES_SQL = (
    "SELECT * FROM stages WHERE pipeline_id IN (%s) AND retired_at IS NULL "
    "ORDER BY CASE kind WHEN 'work' THEN 0 WHEN 'done' THEN 1 ELSE 2 END, position, id"
)


def _stages_of(conn: "sqlite3.Connection", pipeline_ids: list[str]) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {pipeline_id: [] for pipeline_id in pipeline_ids}
    if pipeline_ids:  # one statement however many (the ids go in as one JSON list)
        listed = (json.dumps(pipeline_ids),)
        for found in _h()._rows(conn.execute(_STAGES_SQL % "SELECT value FROM json_each(?)", listed)):
            out[str(found["pipeline_id"])].append(found)
    return out


def stages(conn: "sqlite3.Connection", pipeline_id: str) -> list[Row]:
    """A pipeline's stages in order: its work stages, then Done, then Closed. Read once in a snapshot (or for many
    pipelines together by `prime_stages`); a write reads them again (read_cache)."""
    from . import read_cache

    # Stored rows hold only plain values, so a shallow copy of each is a full one; the snapshot keeps its own.
    found = read_cache.value(conn, ("stages", pipeline_id), lambda: _stages_of(conn, [pipeline_id])[pipeline_id], True)
    return [dict(row) for row in found]


def prime_stages(conn: "sqlite3.Connection", pipeline_ids: Iterable[str]) -> None:
    """Read the stages of many pipelines together, for the `stages` reads that follow in this snapshot."""
    from . import read_cache

    values = read_cache.cache(conn)
    if values is None:
        return
    unread = sorted({str(p) for p in pipeline_ids if p and ("stages", str(p)) not in values})
    for pipeline_id, found in _stages_of(conn, unread).items():
        values.setdefault(("stages", pipeline_id), found)


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


# The task that filed this one, on the visit it filed it in, is still on that visit and in that stage.
_FILER_IN = (
    "SELECT 1 FROM links l JOIN tasks p ON p.id = l.from_task_id AND p.status='open' AND p.stage_id=? "
    "JOIN visits v ON v.id = l.visit_id AND v.task_id = p.id AND v.left_at IS NULL WHERE l.to_task_id=?"
)


def rides_with_filer(conn: "sqlite3.Connection", task_id: str, stage_id: str) -> bool:
    """A sub-task filed by a task that is still in `stage_id`, on the visit that filed it: it goes into that stage on
    its filer's slot. The filer waits on it, so making it wait for room there could hold both forever."""
    return conn.execute(_FILER_IN, (stage_id, task_id)).fetchone() is not None


def has_room(conn: "sqlite3.Connection", stage_row: Row, extra: int = 0, task_row: Row | None = None) -> bool:
    """A stage has room when it has no limit, or fewer of its tasks hold a slot than its limit. A task holds a slot
    while it is open and not snoozed, unless it waits only on a person's OK or answer (`waiting_on.slots`): bots keep
    working while people review. For `task_row`, it also has room when the task that filed it is there
    (`rides_with_filer`): the stage may then hold one over its limit, and takes no other task until it is under
    again."""
    limit = stage_row.get("task_limit")
    if stage_row["kind"] != "work" or not limit:
        return True
    if task_row and rides_with_filer(conn, str(task_row["id"]), str(stage_row["id"])):
        return True
    everyone = conn.execute(
        "SELECT COUNT(*) FROM tasks t WHERE t.pipeline_id IS NOT NULL AND t.stage_id=? AND t.status='open' "
        "AND NOT EXISTS (SELECT 1 FROM snoozes s WHERE s.task_id = t.id AND s.ended_at IS NULL)",
        (stage_row["id"],),
    ).fetchone()[0]
    if everyone + extra < limit:
        return True  # room even if every task held a slot: no task's waits are read
    from . import waiting_on

    return waiting_on.slots(conn, str(stage_row["id"])).held + extra < limit


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
    if stage_row.get("owner") == stage_pick.PICK:
        # One of several: a pick that becomes the task's owner keeps the assignee; otherwise the stage has nobody
        # until the pick (`_pick`) gives it one.
        pick = stage_pick.config(stage_row)
        return assignee if pick and pick.owns_task else None
    return stage_row.get("owner") or None


def owner_ruled(
    conn: "sqlite3.Connection", row: Row, stage_row: Row, owner: str | None, event: str = "enters"
) -> tuple[str | None, str, Row | None]:
    """`owner` (owner_for) for the task in `stage_row`, or None with the history line and the rule when a stage rule
    blocks that owner (backend/stage_rules.py, e.g. Owner on the team): the stage then has nobody, as with no
    assignee, and the pipeline's manager is woken to pick one (C25). The task still enters."""
    from . import stage_rules as _stage_rules

    stage_rules: Any = _stage_rules  # untyped module

    found = stage_rules.owner_check(conn, row, stage_row, owner, event)
    if not found:
        return owner, "", None
    return None, found[1], found[0]


def _owner_rule(conn: "sqlite3.Connection", row: Row, owner: str | None, by: str, set_anyway: bool) -> Row | None:
    """A stage rule on `owner_changes` that blocks `owner` (Owner on the team): a bot is refused, naming the rule; a
    person is refused unless they Set anyway, which returns the rule they overturned. Tico's own changes (a rule's
    action included) are not judged."""
    from . import stage_rules as _stage_rules

    stage_rules: Any = _stage_rules  # untyped module

    if not owner or by == SYSTEM:
        return None
    found = stage_rules.owner_check(conn, row, row["stage"], owner, "owner_changes")
    if not found:
        return None
    rule, line = found
    if is_human(by) and set_anyway:
        return rule
    told = f"{line} (rule {rule['id']})"
    if is_human(by):
        told += ". Set anyway to give it all the same."
    _refuse(conn, by, "task_stage_rule", told, row["id"], version=row.get("version"))


def not_shared(conn: "sqlite3.Connection", row: Row, who: str) -> Row | None:
    """Why `who` may not be asked about private task `row`, as a plain `{"who", "cause", "fix"}`, or None when they
    may read it already. An ask never makes anyone a reader: a person who can see the task shares it first."""
    H = _h()
    if not H.task_private(conn, row) or H.task_private_readable(conn, who, row):
        return None
    name = H.actor_name(conn, who)
    return {
        "who": who,
        "cause": f"{name} cannot read this private task",
        "fix": f"Someone who can see it adds {name} under Who can see, then asks again",
    }


def shut_out(
    conn: "sqlite3.Connection", row: Row, target: Row, owner: str | None = None, *, new: bool = False
) -> Row | None:
    """Why private task `row` may not go into work stage `target`, as a plain `{"who", "cause", "fix"}`, or None when
    it may. The engine never adds a reader, an owner or a party to a private task (docs/pipelines-v3.md, "Private
    tasks. Same engine. Who can see them doesn't change."), so the task stops before a stage whose owner, or someone the
    stage asks, cannot read it, and waits until a person who can see it shares it. `owner` is who would own it there
    (`owner_for`). A task the new owner keeps private by default is judged as it will be: private. `new`: the task is
    being made (`enter`), so whoever makes it is told to add the reader as they do."""
    H = _h()
    if target["kind"] != "work":
        return None
    if owner is None:
        owner = owner_for(target, row.get("assignee_id"), row.get("owner_id"))
    private = H.task_private(conn, row)
    new_owner = owner if owner and owner != row.get("owner_id") else None
    goes_private = not private and bool(new_owner) and bool(H.private_tasks_default(conn, new_owner))
    if not private and not goes_private:
        return None
    full: Row = H.task(conn, row["id"]) or row
    after: Row = {**full, "owner_id": owner, "private": 1}
    blocked: list[tuple[str, str]] = []
    if private and new_owner and not H.task_private_readable(conn, new_owner, full):
        blocked.append((new_owner, "owns"))
    for requirement in R.on_stage(conn, target["id"]):
        who = R.person_of(conn, requirement, "") if requirement["kind"] in ("ok", "answer") else None
        if who and who != owner and not H.task_private_readable(conn, who, after):
            blocked.append((who, "is asked in"))
    if not blocked:
        return None
    who, how = blocked[0]
    name = H.actor_name(conn, who)
    if goes_private:
        cause = (
            f"{name} {how} {target['name']} and could not read this task there: "
            f"{H.actor_name(conn, new_owner)} keeps its tasks private"
        )
        fix = f"Make it private and add {name} under Who can see"
    else:
        cause = f"{name} {how} {target['name']} and cannot read this private task"
        fix = f"Add {name} under Who can see" if new else f"Someone who can see it adds {name} under Who can see"
    return {"who": who, "cause": cause, "fix": fix}


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
    """Leave a visit. The requirements added to it end with it: a visit that ends met keeps them as they were (they did
    their work, and a bound OK among them may still be used, task_asks), any other end withdraws them."""
    conn.execute(
        "UPDATE visits SET left_at=?, how=?, by_id=?, why=? WHERE id=? AND left_at IS NULL",
        (ts, how, str(by), why, visit["id"]),
    )
    if how != "met":
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
    visit = _part(row, "visit")
    if not row.get("owner_id") and visit.get("pick_state") == stage_pick.FULL:
        return None, ""  # it waits for a free bot of the stage's list: nobody is woken
    waits = W.of(conn, row)
    target = W.turn(conn, row, waits, paused=True)
    if target and not row.get("owner_id") and target == _part(row, "pipeline").get("manager_id"):
        if visit.get("pick_state") == stage_pick.NOBODY:
            why = visit.get("pick_why") or "nobody it picks from could take it"
            return (
                target,
                f"Nobody could be picked for this task in {row['stage']['name']}: {why}. Pick one and reassign it.",
            )
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
        task_wakes.body(conn, row, turn_lead or lead, to=target),
        refs=refs,
        trigger=trigger,
        source=source,
    )
    for wait in W.of(conn, row):
        if wait.state in (W.BOT, W.PAUSED) and wait.ask and wait.requirement_id and wait.who != target:
            task_wakes.ask(conn, row, R.get(conn, wait.requirement_id), wait.who)
    return out


def wake_turn(conn: "sqlite3.Connection", row: Row) -> str | None:
    """`_wake_turn` for the never-stuck check: the wake a turn starts with, written once per visit (its key), so a
    sweep may call it on every pass. Returns the message id, or None when it was written already or nobody has a turn."""
    return _wake_turn(conn, row)


def _drop_stale(conn: "sqlite3.Connection", task_id: str) -> int:
    """The turn may have gone to someone else: cancel the wakes still queued for a bot that has no turn on the task
    now, and keep those of a bot that still has one, a bot named on an OK or an answer included (R2). A snoozed or
    ended task, or one at level `person`, keeps none."""
    from . import waiting_on as W

    row = task(conn, task_id)
    if not row or row["status"] != "open" or row.get("snooze"):
        return task_wakes.drop(conn, task_id)
    return task_wakes.drop(conn, task_id, keep=W.turns(conn, row))


# ----------------------------------------------------------------------------- one of several
def _pick(
    conn: "sqlite3.Connection", row: Row, target: Row, owner: str | None, unable: str
) -> "tuple[stage_pick.Choice, stage_pick.Pick] | None":
    """The pick for a task entering `target` when its owner is "One of several" and the task has nobody there yet (a
    task with an owner where the pick becomes the owner keeps it: naming one always wins). None otherwise. A stage
    rule that refused the owner leaves it to the manager, as before."""
    pick = stage_pick.config(target) if target["kind"] == "work" else None
    if not pick or owner or unable:
        return None
    return stage_pick.choose(conn, row, target, pick), pick


def _picked(
    conn: "sqlite3.Connection", task_id: str, visit_id: str, stage_row: Row, choice: "stage_pick.Choice"
) -> None:
    """Write a pick on the visit it was made for and on the task's history."""
    conn.execute(
        "UPDATE visits SET holder_id=?, pick_state=?, pick_why=? WHERE id=?",
        (choice.bot, choice.state, None if choice.bot else choice.why, visit_id),
    )
    _event(
        conn,
        task_id,
        SYSTEM,
        "pick",
        None,
        choice.bot,
        stage_pick.line(conn, stage_row, choice),
        {"stage": stage_row["id"], "bot": choice.bot, "state": choice.state, "why": choice.why, "back": choice.back},
    )


def pick_again(conn: "sqlite3.Connection", task_id: str) -> bool:
    """Pick again for a task that waits for one ("Waiting for a free Reviewer", or nobody could take it): the
    never-stuck pass calls it on every open task, oldest first, so the first bot with room gets the oldest. Returns
    True when a bot was given the task, which is then woken."""
    row = task(conn, task_id)
    if not row or row["status"] != "open" or row.get("snooze") or row.get("owner_id"):
        return False
    visit = _part(row, "visit")
    pick = stage_pick.config(row["stage"])
    if not pick or not visit.get("pick_state"):
        return False
    choice = stage_pick.choose(conn, row, row["stage"], pick)
    if not choice.bot:
        if choice.state != visit.get("pick_state"):
            conn.execute(
                "UPDATE visits SET pick_state=?, pick_why=? WHERE id=?", (choice.state, choice.why, visit["id"])
            )
            if choice.state == stage_pick.NOBODY:
                _wake_turn(conn, _task(conn, task_id))  # the manager is told why, once for the visit
        return False
    ts = _h().now()
    fields: Row = {"owner_id": choice.bot, "version": int(row.get("version") or 0) + 1, "updated_at": ts}
    if pick.owns_task:
        fields["assignee_id"] = choice.bot
    _write(conn, task_id, **fields)
    _picked(conn, task_id, str(visit["id"]), row["stage"], choice)
    _reset_ladder(conn, visit, ts, SYSTEM)
    _owned(conn, row, _task(conn, task_id), SYSTEM)
    moved = _task(conn, task_id)
    if _turn_of(conn, moved)[0] == choice.bot:
        task_wakes.add(
            conn,
            moved,
            choice.bot,
            moved["visit"],
            task_wakes.body(
                conn, moved, f"This task is yours now: {stage_pick.line(conn, row['stage'], choice)}.", to=choice.bot
            ),
            wake_key=f"{task_id}:{visit['id']}:pick:v{moved['version']}",
        )
    return True


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
    start: str | None = None,
) -> Row:
    """New. Put a task that is not on the engine yet into its pipeline's first stage, open its visit and wake the
    owner. `parent` links it to the task that filed it, on that task's open visit. `wake` (what the owner bot's run
    should know: the other work to reconcile with, an automation's run and step), `trigger` and `source` go on the
    owner's wake. `start`, a work stage of the pipeline, enters it there instead: the Finance Owner's own checks, which
    it files already taken in and done, wait only on Finance's last stage (bookkeeper.filing)."""
    H = _h()
    row = H._one(conn, "SELECT * FROM tasks WHERE id=?", (task_id,))
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    if row.get("pipeline_id"):
        _refuse(conn, by, "task_changed", "The task is already in a pipeline", task_id)
    pipe = pipeline(conn, pipeline_id)
    work = [s for s in stages(conn, pipeline_id) if s["kind"] == "work"] if pipe else []
    first = next((s for s in work if s["id"] == start), None) if start else (work[0] if work else None)
    if not pipe or pipe.get("retired_at") or not first:
        _refuse(conn, by, "task_no_such_stage", "No such pipeline", task_id)
    if not is_human(by):
        # Entering further in skips the stages before it, so a bot never starts a task past a person's OK.
        for skipped in work[: work.index(first)]:
            if any(R.people_only(r) for r in R.on_stage(conn, skipped["id"])):
                _refuse(conn, by, "task_needs_person", f"{skipped['name']} needs a person's OK first", task_id)
    above: Row | None = None
    if parent:
        above = task(conn, parent)
        if not above:
            _refuse(conn, by, "not-found", "No such task to file under", task_id)
        if depth(conn, parent) >= MAX_DEPTH:
            _refuse(conn, by, "task_too_deep", f"Sub-tasks nest at most {MAX_DEPTH} deep", task_id)
    owner, unable, ruled = owner_ruled(conn, {**row, "pipeline_id": pipeline_id}, first, owner_for(first, assignee))
    if unable:
        assignee = None
    picked = _pick(conn, {**row, "pipeline_id": pipeline_id, "assignee_id": assignee}, first, owner, unable)
    if picked and picked[0].bot:
        owner = picked[0].bot
        if picked[1].owns_task:
            assignee = owner
    # The rule a move into a stage keeps keeps a new task out of its first stage too: a private task never goes where
    # its owner, or someone the stage asks, cannot read it, for nobody could take it on from there.
    held = shut_out(conn, row, first, owner, new=True)
    if held:
        _refuse(conn, by, "task_not_shared", f"{held['cause']}. {held['fix']} as you create it.", task_id, said=held)
    ts = H.now()
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
    entered_visit = _open_visit(conn, task_id, first["id"], "new", ts)
    if picked:
        _picked(conn, task_id, entered_visit, first, picked[0])
    if above:
        conn.execute(
            "INSERT INTO links (id, from_task_id, to_task_id, visit_id, kind, created_at) VALUES (?,?,?,?,?,?)",
            (H.new_id(), parent, task_id, _part(above, "visit").get("id"), link, ts),
        )
    _event(
        conn,
        task_id,
        by,
        "move",
        None,
        first["id"],
        unable or None,
        {"move": "new", "owner": owner, "parent": parent, **({"stage_rule": ruled["id"]} if ruled else {})},
    )
    if ruled:
        from . import stage_rules as _stage_rules

        stage_rules: Any = _stage_rules  # untyped module

        stage_rules.record(conn, task_id, ruled, unable, SYSTEM, data={"owner": owner_for(first, assignee) or ""})
    if above:
        _drop_stale(conn, str(parent))  # the parent waits on it now: its owner's queued wake for the parent goes
    if idea_checks.is_ideas(first):
        # Product's Ideas: the duplicate check is owed, and a bot's own idea is snoozed in this same write.
        idea_checks.filed(conn, _task(conn, task_id), by, ts)
    _owned(conn, row, _task(conn, task_id), by)
    entered = _task(conn, task_id)
    lead = stage_effects.enter(conn, entered, by)
    _wake_turn(conn, entered, lead, refs=wake, trigger=trigger, source=source)
    return entered


# ----------------------------------------------------------------------------- move
def short_of_queue(work: Sequence[Row], stage: Row | None) -> Row | None:
    """Where a reopened or restored task lands instead of `stage`: the stage itself, or, when its on-enter action
    queues the PRs to merge, the work stage before it, so what that stage needs (the person's OK) is asked again on a
    new visit. Nothing that skips the stages before it lands in a stage that queues PRs."""
    from .item_sources import github as GS

    if not stage or not GS.queues_prs(stage):
        return stage
    at = next((i for i, s in enumerate(work) if s["id"] == stage["id"]), 0)
    return work[at - 1] if at > 0 else stage


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
        target = short_of_queue(work, target)
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


def _decide(conn: "sqlite3.Connection", by: str, row: Row, why: str | None, decided: list[R.Standing]) -> None:
    """Record a move by the bot asked to decide a page (task_requirements.asked_to_decide) as its OK of the version
    attached now, with the move's reason, as `answers.answer` keeps one."""
    if not decided:
        return
    from . import answers
    from . import task_pages as P

    for standing in decided:
        page = P.current(conn, row["visit"]["id"], standing.requirement["id"])
        version = int(page["version"]) if page else None
        answers._write(conn, row, standing.requirement["id"], by, "ok", why, version)  # pyright: ignore[reportPrivateUsage]


def _may_go_back_to(conn: "sqlite3.Connection", by: str, row: Row, target: Row, kind: str) -> None:
    """Only a person moves a task back into a stage that queues its PRs (from a later stage, where a new PR may have
    been linked). Anyone else moves it back to the stage before that one, where what it needs is asked again."""
    if kind != "back" or is_human(by):
        return
    work = [s for s in stages(conn, row["pipeline_id"]) if s["kind"] == "work"]
    short = short_of_queue(work, target)
    if short and short["id"] != target["id"]:
        _refuse(
            conn,
            by,
            "task_stage",
            f"Only a person moves a task back into {target['name']}. Move it back to {short['name']} instead.",
            row["id"],
            row=row,
            then=("back", "ask"),
            version=row.get("version"),
        )


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
    held = shut_out(conn, row, target)
    if held:
        _refuse(
            conn,
            by,
            "task_not_shared",
            f"{held['cause']}. {held['fix']}, then move it again.",
            task_id,
            row=row,
            then=("snooze", "close"),
            version=row.get("version"),
            said=held,
        )
    ts = H.now()
    visit = row["visit"]
    pushed: list[Row] = []
    decided: list[R.Standing] = []
    if kind in ("forward", "done"):
        missing = R.unmet(conn, row) + _skipped(conn, row, target)
        if is_bot(by) and (why or "").strip():
            # The bot asked to decide a page Jev was unsure of moved it on with a reason: that is its decision, an OK
            # with its why on the requirement, not a check skipped.
            decided = [s for s in missing if R.asked_to_decide(s, by)]
            missing = [s for s in missing if not R.asked_to_decide(s, by)]
        _may_push_past(conn, by, row, why, missing, target["id"])
        if target["kind"] == "work" and not has_room(conn, target, task_row=row):
            # From a stage tasks leave by hand the task stays put until someone moves it again, unless it is a
            # person's request that moves on by itself (`not_held_by_hand`).
            held_here = bool(row["stage"].get("by_hand")) and not not_held_by_hand(conn, row)
            then_what = (
                f"It holds {target['task_limit']} a bot is working on: move it again when one leaves."
                if held_here
                else "The task moves in when there is room."
            )
            _refuse(
                conn,
                by,
                "task_stage_full",
                f"No room in {target['name']}. {then_what}",
                task_id,
                row=row,
                then=("snooze", "close"),
                version=row.get("version"),
            )
        _decide(conn, by, row, why, decided)
        pushed = _push_past(conn, by, row, why, missing, ts)
    elif kind == "reopen":
        _need_why(conn, by, why, "reopen it", task_id, everyone=True)
    else:
        _need_why(conn, by, why, "close it" if kind == "closed" else "move it back", task_id)
        _may_go_back_to(conn, by, row, target, kind)
    if kind == "closed" and not cascade:
        _may_close(conn, by, row)
    # Why its sub-tasks close, kept on each: their readers may not read a private task that filed them, so it is not named.
    named = not H.task_private(conn, row) and row.get("title")
    closed_with = f"Closed with {named or 'the task that filed it'}"
    if kind in ("back", "closed"):
        stage_effects.undo(conn, row, by, closed_with)
    if kind == "closed":
        _close_under(conn, task_id, by, closed_with)
    how = {"forward": "met", "done": "met", "back": "back", "closed": "closed", "reopen": "reopened"}[kind]
    if pushed:
        how = "pushed_past"
    _close_visit(conn, visit, how, by, why, ts)
    if row.get("snooze"):
        _end_snooze(conn, row["snooze"], by, ts)  # a snooze sets a task aside in one stage: it ends with the visit
    wanted = owner_for(target, row.get("assignee_id"), row.get("owner_id"))
    owner, unable, ruled = owner_ruled(conn, row, target, wanted)
    task_wakes.drop(conn, task_id)  # what was queued was about the visit that just closed
    status = {"work": "open", "done": "done", "closed": "closed"}[target["kind"]]
    extra: Row = {}
    if "done_at" in row:
        extra["done_at"] = ts if status == "done" else None
    if "closed_at" in row:
        extra["closed_at"] = ts if status == "closed" else None
    if "closed_by_id" in row:
        extra["closed_by_id"] = str(by) if status == "closed" else None
    if unable:
        extra["assignee_id"] = None  # the manager picks who has it (reassign), as for a task with no assignee
    picked = _pick(conn, row, target, owner, unable)
    if picked and picked[0].bot:
        owner = picked[0].bot
        if picked[1].owns_task:
            extra["assignee_id"] = owner  # the pick becomes the task's owner, for life
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
    entered_visit = _open_visit(conn, task_id, target["id"], how, ts)
    if picked:
        _picked(conn, task_id, entered_visit, target, picked[0])
    revision = _event(
        conn,
        task_id,
        by,
        "move",
        row["stage_id"],
        target["id"],
        ". ".join(part for part in (why, unable) if part) or None,
        {
            "move": "pushed_past" if pushed else kind,
            "owner": owner,
            "was_owner": row.get("owner_id"),
            **({"pushed_past": pushed} if pushed else {}),
            **({"stage_rule": ruled["id"]} if ruled else {}),
        },
    )
    if ruled:
        from . import stage_rules as _stage_rules

        stage_rules: Any = _stage_rules  # untyped module

        stage_rules.record(conn, task_id, ruled, unable, SYSTEM, data={"owner": wanted or ""})
    _owned(conn, row, _task(conn, task_id), by)
    moved = _task(conn, task_id)
    if target["kind"] == "work":
        _wake_turn(conn, moved, stage_effects.enter(conn, moved, by))
    elif not cascade:
        _ended(conn, moved, by)
    if pushed:
        _tell_manager(conn, moved, f"Pushed past {', '.join(p['name'] for p in pushed)}: {why}", "pushed_past")
    if moved["status"] != "open" or kind == "forward":
        # A task that goes after this one may be held on it in Merge: queued now, or its owner told (merge_order).
        from . import merge_order

        merge_order.release(conn, task_id, SYSTEM)
    # What a move causes beyond the task's own row: the events, a routine's day, stopping work on a closed task
    # (backend/task_effects.py). Last, and for every move, so nothing that moves a task can leave them out.
    task_effects.moved(conn, row, moved, kind, by, why, revision)
    return _task(conn, task_id)


def _owned(conn: "sqlite3.Connection", before: Row, after: Row, by: str) -> None:
    """Who has the task now comes first, before anyone is woken or anything hears of the move: what follows a new
    owner, its private default among it (task_effects.owner_moved). Nobody is made a reader: a move that would need
    one is held first (`shut_out`). The caller reads the task again after."""
    task_effects.owner_moved(conn, before, after, by)


def _close_under(conn: "sqlite3.Connection", task_id: str, by: str, note: str) -> None:
    """Close every open task under `task_id`, however deep: a closed task leaves no open work behind it. An ended task
    in between stays as it ended, and what is open under it closes too. One whose PR already merged stays open, with
    what is under it (stage_effects.merged_work): its work is in the code."""
    children = conn.execute(
        "SELECT t.id, t.status FROM links l JOIN tasks t ON t.id = l.to_task_id "
        "WHERE l.from_task_id=? ORDER BY l.created_at, l.id",
        (task_id,),
    ).fetchall()
    for child, status in children:
        if status == "open" and stage_effects.merged_work(conn, child):
            continue
        if status == "open":
            move(conn, child, CLOSED, by, note, cascade=True)
        else:
            _close_under(conn, child, by, note)


def _ended(conn: "sqlite3.Connection", row: Row, by: str) -> None:
    """A task ended. If a task filed it and that was the last one open from that visit, the filing task's owner is woken
    once with how each ended, and the filing task moves on if that was all it needed. When the turn is a person's (the
    stage's OK, held while the review ran: waiting_on.of), its owner bot is still woken: it has the review's findings
    to fold in before the person is asked (task_wakes.FILED). A filed task the woken bot may not be told of (task_wakes.told) is
    "A sub-task": that it ended, never its title. A parent nobody is woken on is logged with why."""
    link = _h()._one(conn, "SELECT * FROM links WHERE to_task_id=?", (row["id"],))
    if not link:
        return
    if not link.get("visit_id"):
        return _no_parent_wake("filed_in_no_visit", link["from_task_id"], row["id"])
    above = task(conn, link["from_task_id"])
    if not above or above["status"] != "open":
        return _no_parent_wake("parent_ended", link["from_task_id"], row["id"])
    if _part(above, "visit").get("id") != link["visit_id"]:
        return _no_parent_wake("parent_left_stage", above["id"], row["id"])
    if stage_effects.filed_open(conn, link["visit_id"]):
        return _no_parent_wake("filed_still_open", above["id"], row["id"])
    # A bot that snoozed while it waited on what it filed is woken now, not on the snooze's date (Initech, Nov 11).
    above = task_effects.filed_ended(conn, above, by)
    if advance(conn, above["id"]):
        return
    filed = conn.execute(
        "SELECT t.id, t.title, t.status FROM links l JOIN tasks t ON t.id = l.to_task_id WHERE l.visit_id=? "
        "ORDER BY l.created_at, l.id",
        (link["visit_id"],),
    ).fetchall()
    _reset_ladder(conn, above["visit"], _h().now(), by)
    above = _task(conn, above["id"])
    target, _ = _turn_of(conn, above)
    if not target and _may_wake_owner(above):
        target = str(above["owner_id"])
    if not target:
        level = _part(above, "visit").get("level")
        why = "snoozed" if above.get("snooze") else "person_level" if level == "person" else "no_bot_owner"
        _no_parent_wake(why, above["id"], row["id"])
    if target:
        from . import waiting_on as W

        shown = task_wakes.told(conn, above, target)
        lead = "The tasks this one filed have ended: " + "; ".join(
            f"{(title or 'A task') if shown(filed_id) else W.SUB_TASK}: {'Done' if status == 'done' else 'Closed'}"
            for filed_id, title, status in filed
        )
        task_wakes.add(
            conn,
            above,
            target,
            above["visit"],
            task_wakes.body(conn, above, lead, to=target),
            wake_key=f"{above['id']}:{above['visit']['id']}:{task_wakes.FILED}:{row['id']}",
        )


def _may_wake_owner(row: Row) -> bool:
    """Whether a filing task's owner bot may hear that what it filed ended, though nobody has a turn (a person's OK
    waits): the task is open, not snoozed and not at level person (task_wakes.may_start holds those too)."""
    return (
        row["status"] == "open"
        and not row.get("snooze")
        and (_part(row, "visit").get("level") or "owner") != "person"
        and is_bot(row.get("owner_id"))
    )


def _no_parent_wake(why: str, parent_id: str, filed_id: str) -> None:
    """A filed task ended and nobody is woken on the task that filed it: the category and both ids, for the logs. A
    person-owned parent (`no_bot_owner`) is the normal case there, so it logs at DEBUG."""
    level = logging.DEBUG if why == "no_bot_owner" else logging.INFO
    LOG.log(level, "parent wake skipped: %s parent=%s filed=%s", why, parent_id, filed_id)


def tell_owner(
    conn: "sqlite3.Connection",
    task_id: str,
    text: str,
    what: str,
    *,
    wake: bool = True,
    refs: Row | None = None,
    trigger: str | None = None,
) -> str | None:
    """News from outside a task for its bot owner (a pull request's checks or reviews), once per visit and `what`: a
    wake when it is the owner's turn (`_turn_of`), the task waits on no person first, and `wake`; else a quiet note
    it reads on its next run. Its run is
    held to the turn and the run cap like every engine wake (task_wakes.may_start). A person owner is told nothing:
    the task is in their Needs you. Returns the message id, or None."""
    row = task(conn, task_id)
    if not row or row["status"] != "open" or not is_bot(row.get("owner_id")):
        return None
    from . import waiting_on as W

    owner = str(row["owner_id"])
    waits = W.of(conn, row)
    # A task that waits on a person first is theirs to move: the bot hears of it on its next run.
    run = wake and not (waits and waits[0].state == W.PERSON) and _turn_of(conn, row)[0] == owner
    return task_wakes.add(
        conn,
        row,
        owner,
        row["visit"],
        task_wakes.body(conn, row, text, to=owner) if run else text,
        wake_key=f"{row['id']}:{row['visit']['id']}:{what}",
        quiet=not run,
        refs=refs,
        trigger=trigger,
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
    """Forward, by the engine: move the task to its next stage if everything is met, there is room, and nobody there
    is shut out of a private task (`shut_out`: it waits until it is shared). Returns the moved task, or None when it
    stays."""
    row = task(conn, task_id)
    if not row or row["status"] != "open" or row.get("snooze") or row["stage"].get("by_hand"):
        return None
    target = next_stage(conn, row)
    if not target or R.unmet(conn, row) or not has_room(conn, target, task_row=row) or shut_out(conn, row, target):
        return None
    if idea_checks.holds(conn, row):
        return None  # Jev's duplicate check comes first (idea_checks), for WAIT_S at most
    H = _h()
    try:
        return move(conn, task_id, target["id"], SYSTEM, seen_version=row.get("version"))
    except H.Refused as refused:
        # Everything read as met and there was room, yet the move was refused: say why in the server log, or a task
        # that never moves on leaves no trace (Nov 12: tasks sat in Build for hours with "Moving to Review 1").
        log_refused_move(task_id, str(target["id"]), refused)
        raise


def log_refused_move(task_id: str, to: str, refused: Exception) -> None:
    """The server log line for an automatic move the engine refused: its category (the refusal's rule), the task and
    the stage it was going to, and the refusal's words with anything like a secret taken out, cut to 300 characters."""
    from . import run_diagnostics

    diagnostics: Any = run_diagnostics  # untyped module
    rule = str(getattr(refused, "rule", type(refused).__name__))
    detail = str(diagnostics.scrub(getattr(refused, "detail", "") or str(refused)))[:300]
    LOG.warning("automatic move refused: category=%s task=%s to=%s detail=%s", rule, task_id, to, detail)


def own_snooze(row: Row) -> bool:
    """The task's snooze is one its owner bot set on its own task: how it waits for something (its PR's checks). A bot's
    idea filed snoozed into Product's Ideas is not: only a person ends that one (idea_checks.filed)."""
    snooze: Row = row.get("snooze") or {}
    owner = row.get("owner_id")
    if idea_checks.filed_snooze(snooze or None):
        return False
    return bool(snooze) and snooze.get("by_id") == owner and is_bot(owner)


def ready_but_snoozed(conn: "sqlite3.Connection", row: Row) -> bool:
    """Whether the task, its snooze aside, has everything met and only waits for a slot in the next stage
    (`waiting_on.all_met`): "Moving to Review 1" or "No room in Review 1"."""
    from . import waiting_on as W

    if row.get("status") != "open" or row["stage"].get("by_hand"):
        return False
    return W.all_met(W.of(conn, {**row, "snooze": None}))


def move_on_past_own_snooze(conn: "sqlite3.Connection", task_id: str, why: str, hops: int = 20) -> bool:
    """A snooze the owner bot set on its own task, now that everything the stage asks is met and the next stage has
    room: the snooze was how the bot waited for that (its checks), so it ends, as SYSTEM, and the task moves on (and
    through stages that need nothing, up to `hops`). Nothing changes when the move is refused or there is no room: the
    snooze stays and the refusal is in the server log. True when it moved. *Nov 12: Tico Team's Build tasks sat for
    hours, snoozed "until checks pass", with their checks passed and Review 1 unlimited.*"""
    row = task(conn, task_id)
    if not row or not own_snooze(row) or not ready_but_snoozed(conn, row):
        return False
    target = next_stage(conn, row)
    if not target or not has_room(conn, target, task_row=row) or shut_out(conn, row, target):
        return False
    H = _h()
    conn.execute("SAVEPOINT move_on_past_own_snooze")
    try:
        unsnooze(conn, task_id, SYSTEM, why)
        moved = advance(conn, task_id) is not None
    except H.Refused:  # logged by `advance`
        moved = False
    if not moved:
        conn.execute("ROLLBACK TO move_on_past_own_snooze")  # the snooze stays as it was
    conn.execute("RELEASE move_on_past_own_snooze")
    if not moved:
        return False
    for _ in range(hops):  # stages that need nothing are passed in the same step, as the never-stuck check does
        conn.execute("SAVEPOINT move_on_hop")
        try:
            hopped = advance(conn, task_id) is not None
        except H.Refused:
            conn.execute("ROLLBACK TO move_on_hop")
            hopped = False
        conn.execute("RELEASE move_on_hop")
        if not hopped:
            break
    return True


def not_held_by_hand(conn: "sqlite3.Connection", row: Row) -> bool:
    """A person's request (one a person asked for that no task filed, as C8 reads it) on its first visit, in a by-hand
    stage whose "a person's request moves on" setting is on (`requests_move_on`: Product's Ideas): the person already
    picked it, so it is not held there. It moves on by itself when the next stage has room, oldest first
    (never_stuck). With the setting off (Growth's Ideas, Monitoring's Watching) every task there waits until someone
    moves it. One moved back into the stage, or snoozed there, is held like any other."""
    stage_row: Row = row.get("stage") or {}
    visit: Row = row.get("visit") or {}
    if not stage_row.get("by_hand") or not stage_row.get("requests_move_on"):
        return False
    if visit.get("entered_how") != "new" or row.get("snooze"):
        return False
    if not is_human(row.get("requester_id")):
        return False
    return not conn.execute("SELECT 1 FROM links WHERE to_task_id=?", (row["id"],)).fetchone()


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
    *,
    set_anyway: bool = False,
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
    overturned = _owner_rule(conn, row, owner, by, set_anyway)
    ts = H.now()
    fields: Row = {"owner_id": owner, "version": int(row.get("version") or 0) + 1, "updated_at": ts}
    pick = stage_pick.config(row["stage"])
    if row["stage"].get("owner") == "assignee" or (pick and pick.owns_task):
        fields["assignee_id"] = owner
    _write(conn, task_id, **fields)
    if pick:
        # Whoever has it now holds this stage: a task back here goes to them, and it no longer waits for a pick.
        conn.execute(
            "UPDATE visits SET holder_id=?, pick_state=NULL, pick_why=NULL WHERE id=?", (owner, row["visit"]["id"])
        )
    _reset_ladder(conn, row["visit"], ts, by)  # the old owner's wakes go with its turn
    said = ". ".join(p for p in (why, f"Overturned rule {overturned['name']}" if overturned else "") if p) or why
    data: Row = {"stage": row["stage_id"], **({"overturned_rule": overturned["id"]} if overturned else {})}
    revision = _event(conn, task_id, by, "reassign", row.get("owner_id"), owner, said, data)
    if overturned:
        H.event(conn, by, "stage_rule.overturned", task_id, {"rule": overturned["id"], "owner": owner})
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
            task_wakes.body(conn, moved, "This task is yours now." + (f" {why}" if why else ""), to=owner),
            wake_key=f"{task_id}:{moved['visit']['id']}:owner:v{moved['version']}",
        )
    return moved


PROGRESS_SQL = (
    "SELECT v.task_id, "
    "(SELECT COUNT(*) FROM answers a WHERE a.visit_id = v.id), "
    "(SELECT COUNT(*) FROM answers a WHERE a.visit_id = v.id AND a.undone_at IS NOT NULL), "
    "(SELECT COUNT(*) FROM requirements r WHERE r.visit_id = v.id), "
    "(SELECT COUNT(*) FROM requirements r WHERE r.visit_id = v.id AND r.withdrawn_at IS NOT NULL), "
    "(SELECT COUNT(*) FROM links l WHERE l.visit_id = v.id), "
    "(SELECT COUNT(*) FROM links l LEFT JOIN tasks c ON c.id = l.to_task_id "
    " WHERE l.visit_id = v.id AND COALESCE(c.status, 'closed') <> 'open') "
    "FROM visits v WHERE v.id=?"
)


def progress_mark(conn: "sqlite3.Connection", visit_id: str, standings: Sequence[R.Standing] | None = None) -> str:
    """What the never-stuck check compares to tell a task that changed from one that did not: the answers, added
    requirements and filed tasks of this visit, and where each requirement stands (so a PR that opened or a check that
    finished is a change). A comment is not a change (a person's that wakes the owner bot restarts the ladder itself:
    `person_spoke`). Nor is a snooze or its end: setting a task aside earns no more runs. Nor is the task's version: it
    says the row changed since someone looked, and a field edit (a new title, a label) raises it. A move and a reassign
    start the ladder again themselves. `standings` saves reading them again when the caller has them."""
    from . import read_cache

    found = cast("Any", read_cache).one(conn, PROGRESS_SQL, (visit_id,))  # a pass reads them for many (never_stuck)
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
    if visit.get("level") == "person" and visit.get("late_person_at") and not _h().by_own_hand(by):
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


def person_spoke(conn: "sqlite3.Connection", task_id: str, by: str) -> None:
    """A person wrote to the task's owner bot while the owner has it (a comment that wakes the bot): the ladder starts
    again, as when an answer comes. The run the comment starts is the first of the new count, so if it stops before
    the task is finished the bot is woken again after the first gap: never left for the hour a third try waits, and
    never handed straight to the manager for runs spent before the person spoke. A person's own words or their token
    count; the Assistant or BotOps acting for them do not (`hubdb.VIA`), since a bot that could restart the ladder could
    keep a task going round it for ever. A task a person or the manager bot has keeps its level: Try again is the way
    back from a person."""
    H = _h()
    if not is_human(by) or H.VIA.get():
        return
    row = task(conn, task_id)
    if not row or row["status"] != "open" or row.get("snooze") or not row.get("visit"):
        return
    if (row["visit"].get("level") or "owner") != "owner":
        return
    _reset_ladder(conn, row["visit"], H.now(), by)


def try_again(conn: "sqlite3.Connection", task_id: str, by: str, why: str | None = None) -> Row:
    """Try again: hand a task the ladder or Late brought to a person back to its owner, with the count at zero. The
    owner is woken once. Only a person tries again, in their own session (H.by_own_hand): a bot, or the Assistant or
    BotOps acting for a person, that could restart the ladder could keep a task going round it for ever."""
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
    if not H.by_own_hand(by):
        _refuse(
            conn,
            by,
            "task_needs_person",
            "Try again is the person's own, in Tico: the Assistant, BotOps and tokens can't. Ask them to press it.",
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
            task_wakes.body(conn, out, lead, to=target),
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
    conn.execute(f"DELETE FROM idea_checks WHERE task_id IN ({marks})", ids)  # an owed check is for this visit
    return conn.execute(f"DELETE FROM visits WHERE task_id IN ({marks})", ids).rowcount


def restore(conn: "sqlite3.Connection", task_id: str, by: str, was: Row) -> Row:
    """Place a task that came back from the trash (`task_delete.restore_tasks`): its row is open and in no stage.
    `was` is what the trash kept of where it stood: `status`, `pipeline_id`, `stage_id`, `owner_id`, `assignee_id`,
    `done_at`, `closed_at`, `closed_by_id`.

    An open task starts a new visit in the stage it was in while that stage is still in the pipeline, has room and
    may take it (`shut_out`: its owner, and whoever it asks, can read a private task), else in the first stage, as a
    new task would; owned as the stage says. What follows the owner comes first, then the stage's on-enter, then the
    owner's wake. When neither stage may take a private task the restore is refused (`task_not_shared`, its cause and
    fix): nobody is made a reader or an owner of it. A done or closed one goes back as it ended: same owner, same
    times, nobody woken. A pipeline that is gone or retired gives General. What it was asked before is not asked
    again."""
    H = _h()
    row = H._one(conn, "SELECT * FROM tasks WHERE id=?", (task_id,))
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    if row.get("pipeline_id"):
        _refuse(conn, by, "task_changed", "The task is already in a pipeline", task_id)
    pipe = pipeline(conn, str(was.get("pipeline_id") or "")) if was.get("pipeline_id") else None
    if not pipe or pipe.get("retired_at"):
        pipe = pipeline(conn, H.GENERAL_PIPELINE)
    if pipe and pipe.get("private"):
        # Back into a private pipeline: private, with its readers, before a stage is judged (backend/private_pipelines.py).
        from . import private_pipelines

        private_pipelines.admit(conn, task_id, pipe)
        row = H._one(conn, "SELECT * FROM tasks WHERE id=?", (task_id,))
    order = stages(conn, pipe["id"]) if pipe else []
    work = [s for s in order if s["kind"] == "work"]
    status = str(was.get("status") or "open")
    assignee = was.get("assignee_id") or was.get("owner_id")
    if status == "open":
        # Judged with the owner it had: the restored row holds none yet, and its own owner coming back is no new reader.
        placed = {**row, "owner_id": was.get("owner_id"), "assignee_id": assignee}
        target = _restore_stage(conn, by, placed, work, was.get("stage_id"))
    else:
        target = next((s for s in order if s["kind"] == status), None)
    if not pipe or not target:
        _refuse(conn, by, "task_no_such_stage", "No pipeline to restore the task into", task_id)
    ts = H.now()
    fields: Row = {
        "pipeline_id": pipe["id"],
        "stage_id": target["id"],
        "status": status,
        "assignee_id": assignee,
        "updated_at": ts,
    }
    picked = None
    if status == "open":
        fields["owner_id"] = owner_for(target, assignee)
        # A stage that picks from several picks for a restored task as for any task that enters it.
        picked = _pick(
            conn, {**row, "pipeline_id": pipe["id"], "assignee_id": assignee}, target, fields["owner_id"], ""
        )
        if picked and picked[0].bot:
            fields["owner_id"] = picked[0].bot
            if picked[1].owns_task:
                fields["assignee_id"] = picked[0].bot
    else:
        fields["owner_id"] = was.get("owner_id")
        fields["done_at"] = was.get("done_at") if status == "done" else None
        fields["closed_at"] = was.get("closed_at") if status == "closed" else None
        fields["closed_by_id"] = was.get("closed_by_id") if status == "closed" else None
    _write(conn, task_id, **fields)
    how = {"open": "new", "done": "met", "closed": "closed"}[status]
    restored_visit = _open_visit(conn, task_id, target["id"], how, ts)
    if picked:
        _picked(conn, task_id, restored_visit, target, picked[0])
    _event(conn, task_id, by, "move", None, target["id"], "Restored", {"move": "restored", "owner": fields["owner_id"]})
    if status == "open":
        _owned(conn, {**row, "owner_id": was.get("owner_id")}, _task(conn, task_id), by)
        stage_effects.enter(conn, _task(conn, task_id), by)
        _wake_turn(conn, _task(conn, task_id), "This task was restored.")
    return _task(conn, task_id)


def _restore_stage(conn: "sqlite3.Connection", by: str, row: Row, work: Sequence[Row], was: object) -> Row | None:
    """Where an open task comes back (`restore`): the work stage it was in, while it is still in the pipeline, has
    room and may take it, else the first one if that may. Refused when a private task may go into neither."""
    first = work[0] if work else None
    saved = short_of_queue(work, next((s for s in work if s["id"] == was), None))
    held: Row | None = None
    for stage in (saved, first):
        if not stage or (stage is saved and stage is not first and not has_room(conn, stage)):
            continue
        out = shut_out(conn, row, stage, owner_for(stage, row.get("assignee_id")))
        if not out:
            return stage
        held = held or out
    if held and first:
        said = {**held, "fix": f"Restore it once {first['name']} is owned by someone who can read it"}
        _refuse(conn, by, "task_not_shared", f"{said['cause']}. {said['fix']}.", row["id"], said=said)
    return first


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
    # A stage rule on a bot's snooze whose If is fixed conditions only (no model call inside a write) blocks it here;
    # one with a Jev question was asked before the write (task_moves_routes, stage_rules.judge_snooze).
    blocked = stage_rules_snooze(conn, row, by, why, until)
    if blocked:
        _refuse(
            conn, by, "task_stage_rule", blocked, task_id, row=row, then=("ask", "file"), version=row.get("version")
        )
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
        _person_ends_filed(conn, row, by)
        _end_snooze(conn, row["snooze"], by, ts)
    conn.execute(
        "INSERT INTO snoozes (id, task_id, visit_id, by_id, why, until_at, created_at) VALUES (?,?,?,?,?,?,?)",
        (H.new_id(), task_id, visit["id"], str(by), why, until, ts),
    )
    if is_bot(by):
        conn.execute("UPDATE visits SET bot_snoozes = bot_snoozes + 1 WHERE id=?", (visit["id"],))
    task_wakes.drop(conn, task_id)  # nothing wakes for a snoozed task
    _event(conn, task_id, by, "snooze", None, until, why)
    H.event(conn, by, "task.snoozed", task_id, {"until": until, "why": why or "", "stage": row["stage_id"]})
    out = _task(conn, task_id)
    if not until and is_bot(by):
        _tell_manager(conn, out, f"On ice, with no end date: {why}", f"on_ice:{out['snooze']['id']}")
    return out


def _person_ends_filed(conn: "sqlite3.Connection", row: Row, by: str) -> None:
    """A bot's idea filed snoozed into Product's Ideas (idea_checks.filed) waits for a person: no bot ends or replaces
    that snooze, its own filer included. Tico's own (a person's move out of the stage) still may."""
    if is_bot(by) and idea_checks.filed_snooze(row.get("snooze")):
        _refuse(
            conn,
            by,
            "idea_filed_snoozed",
            "A bot's idea stays snoozed until a person unsnoozes it. Ask the person on the task if it should go on.",
            row["id"],
        )


def stage_rules_snooze(conn: "sqlite3.Connection", row: Row, by: str, why: str | None, until: str | None) -> str:
    """The refusal a fixed-condition stage rule gives a bot's snooze of `row` ("" when none): "Rule <name>: <reason>
    (rule <id>)". Jev's rules are not asked here."""
    from . import stage_rules as _stage_rules

    stage_rules: Any = _stage_rules  # untyped module

    found = stage_rules.judge_snooze(conn, row, by, why or "", until, fixed_only=True)
    return refusal_line(found[0], found[1]) if found else ""


def refusal_line(rule: Row, line: str) -> str:
    """What a bot a stage rule blocked is told: the rule's line, its reason, and the rule's id."""
    reason = str(rule.get("reason") or "")
    said = line if reason and reason in line else f"{line}. {reason}".rstrip(". ")
    return f"{said} (rule {rule['id']})"


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
    _person_ends_filed(conn, row, by)
    ts = H.now()
    _end_snooze(conn, row["snooze"], by, ts)
    conn.execute("UPDATE visits SET next_try_at=? WHERE id=?", (ts, row["visit"]["id"]))
    _event(conn, task_id, by, "unsnooze", row["snooze"].get("until_at"), None, why)
    return _task(conn, task_id)


# The release production runs, as end_release_snoozes last saw it.
RELEASE_SEEN = "release-snoozes-seen"
# Open tasks a bot snoozed in a stage whose page names the release production runs (`with_release`: Product's
# Validate), whose filed work has a PR in the repository this server runs (?1, `owner/name`): its production is this
# server. Such a snooze waits for this server to run a release, so a new one ends it.
_RELEASE_WAITS = (
    "SELECT z.task_id FROM snoozes z JOIN tasks t ON t.id = z.task_id "
    "WHERE z.ended_at IS NULL AND z.by_id LIKE 'bot:%' AND t.status = 'open' AND t.pipeline_id IS NOT NULL "
    "AND EXISTS (SELECT 1 FROM requirements r WHERE r.stage_id = t.stage_id AND r.withdrawn_at IS NULL "
    "AND json_extract(r.settings, '$.with_release')) "
    "AND EXISTS (SELECT 1 FROM links l JOIN task_links tl ON tl.task_id = l.to_task_id AND tl.source = 'github' "
    "AND tl.item_type = 'pr' WHERE l.from_task_id = t.id "
    "AND lower(substr(tl.item_key, 1, length(?1) + 1)) = lower(?1) || '#') "
    "ORDER BY z.created_at, z.id"
)


def end_release_snoozes(conn: "sqlite3.Connection", running: str, repo: str) -> list[str]:
    """This server runs another release (`running`, of `repo`, read at start-up): a bot's snooze ends in a stage that
    checks the release production runs, on a task whose filed work is in `repo`, and whoever has the turn is woken once
    with the release, instead of sleeping to the clock time it guessed. A person's snooze stays. The first start that
    sees no earlier release only records it. Returns the tasks whose snooze ended."""
    found = conn.execute("SELECT value FROM registry_metadata WHERE key=?", (RELEASE_SEEN,)).fetchone()
    conn.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)", (RELEASE_SEEN, running))
    if not found or found[0] == running or not repo:
        return []
    ended: list[str] = []
    for (task_id,) in conn.execute(_RELEASE_WAITS, (repo,)).fetchall():
        snoozed: Row = (task(conn, task_id) or {}).get("snooze") or {}
        why = str(snoozed.get("why") or "")
        out = unsnooze(conn, task_id, SYSTEM, f"Production runs {running} now")
        target, _ = _turn_of(conn, out)
        if target:
            lead = f"Production runs {running} now (it ran {found[0]}). Your snooze ended: {why}".rstrip(": ")
            task_wakes.add(
                conn,
                out,
                target,
                out["visit"],
                task_wakes.body(conn, out, lead, to=target),
                wake_key=f"{task_id}:{out['visit']['id']}:release:{running}",
            )
        ended.append(str(task_id))
    return ended


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
    for_action: bool = False,
) -> Row:
    """Add an OK or an answer from a named person to this task, for this visit (C30). The task does not leave the stage
    until it is answered, withdrawn or pushed past. A bot has one open per task: asking again replaces it (C15,
    `_replace_open`). A bot does not ask a question of a person the stage's OK or page already waits on
    (`_stage_asks`), nor one in Product's Validate that is not an OK of its passed proof page (validate_gate;
    `for_action`: an OK bound to an action, task_asks.ask_ok, is that action's own)."""
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
    if person == by and not is_human(by):
        # A bot never satisfies its own request. A person may ask themself: their own click is what answers it (an OK
        # of their own ask is refused unless it is that click, answers._may_answer).
        _refuse(conn, by, "task_requirement", "Nobody answers their own ask: ask someone else", task_id)
    if kind == "ok" and not is_human(person):
        _refuse(conn, by, "task_requirement", "An OK is asked of a person", task_id)
    if is_bot(by):
        page = _people_page(conn, row, kind, shows, choices)
        if page:
            _refuse(conn, by, "task_requirement", f"{page} is a person's OK already. {R.PAGE_OK_HINT}", task_id)
    if not for_action:
        shows = validate_gate.check_ask(conn, row, by, person, kind, shows, choices)
    if is_bot(by) and kind == "answer" and is_human(person):
        signoff = _stage_asks(conn, row, str(person))
        if signoff:
            name = H.actor_name(conn, person)
            _refuse(
                conn, by, "task_requirement", f"The stage already asks {name} to OK {signoff}. {ASKED_HINT}", task_id
            )
    _may_name(conn, by, person, task_id)
    unshared = not_shared(conn, row, str(person))
    if unshared:
        _refuse(conn, by, "private", f"{unshared['cause']}. {unshared['fix']}.", task_id, said=unshared)
    if not (line or "").strip():
        _refuse(conn, by, "task_requirement", "Say what you are asking, in one sentence", task_id)
    ts = H.now()
    visit = row["visit"]
    if is_bot(by):
        _replace_open(conn, task_id, visit["id"], by, ts)
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


def _people_page(
    conn: "sqlite3.Connection", row: Row, kind: str, shows: object, choices: list[str] | None
) -> str | None:
    """The name of the stage's people-only page requirement a bot's ask would duplicate, or None: an OK that shows that
    page or shows nothing, or a yes/no question that shows that page, while the stage has one. A person OKs that page
    already, every version, so the bot's ask adds a second one and nothing passes on it. A yes/no that shows nothing is
    a plain question (a Spec stage's clarifying question) and is allowed."""
    yes_no = kind == "answer" and sorted(str(c).strip().lower() for c in (choices or [])) == ["no", "yes"]
    if kind != "ok" and not yes_no:
        return None
    from . import task_pages as P

    refs = [str(ref) for ref in cast("list[object]", shows)] if isinstance(shows, list) else []
    for requirement in R.for_visit(conn, row["visit"]):
        if requirement["kind"] != "page" or not requirement.get("stage_id") or not R.people_only(requirement):
            continue
        attached = P.current(conn, row["visit"]["id"], requirement["id"])
        named = bool(attached) and any(str(attached["file_id"]) in ref for ref in refs)
        if named or (kind == "ok" and not refs):
            return R.name_of(requirement)
    return None


# What a bot is told when the stage already asks the person it names (`_stage_asks`).
ASKED_HINT = "Do not ask them again: put any question on the page or in a comment."


def _stage_asks(conn: "sqlite3.Connection", row: Row, person: str) -> str | None:
    """The name of the stage's OK or page that waits on `person` now, or None. A bot's question to that same person
    would be a second item in their Needs you, and the stage would wait on both (Tico Team, Nov 12: the Product Owner
    asked "Please OK the attached spec" beside the Spec page's own OK, and Spec stayed after the page was OK'd). A page
    a person OKs anyway counts while Jev is still checking it: it goes to them next."""
    from . import waiting_on

    for standing in R.standings(conn, row):
        requirement = standing.requirement
        if standing.state != R.WAITING or not requirement.get("stage_id") or requirement["kind"] not in R.STEERED:
            continue
        on = standing.on
        if on == R.JEV:
            if not (requirement.get("person_id") or R.people_only(requirement)):
                continue  # Jev passing it is enough: no person is asked
            on = R.person_of(conn, requirement, row["visit"]["id"])
        elif on and not is_human(on):
            continue  # a bot named to approve, or a source
        if (on or waiting_on.person_for(conn, row)) == person:
            return R.name_of(requirement)
    return None


def _replace_open(conn: "sqlite3.Connection", task_id: str, visit_id: str, by: str, ts: str) -> None:
    """C15: a bot has one ask of its own on a visit (the index requirements_one_per_bot): asking again replaces it.
    What was answered stays on record (its answer, and the task's `decided`, task_calls), and an OK that was used stays
    used. An OK bound to exact content that was given and not used yet is never dropped by a new ask, nor made good for
    anything else: the bot uses it or withdraws it first."""
    from . import task_asks

    rows = conn.execute(
        "SELECT id FROM requirements WHERE visit_id=? AND added_by_id=? AND by_bot=1 AND withdrawn_at IS NULL",
        (visit_id, str(by)),
    ).fetchall()
    for (old,) in rows:
        if task_asks.standing(conn, str(old)) == task_asks.OK:
            _refuse(
                conn,
                by,
                "task_requirement",
                "Your earlier OK on this task was given and not used yet: use it, or withdraw it, then ask again",
                task_id,
            )
    for (old,) in rows:
        conn.execute("UPDATE requirements SET withdrawn_at=? WHERE id=?", (ts, old))
        _event(conn, task_id, by, "requirement_withdrawn", old, None, "Asked again")


def withdraw_requirement(
    conn: "sqlite3.Connection", requirement_id: str, by: str, why: str | None = None
) -> Row | None:
    """Take back a requirement that was added to one task. The task moves on if that was all it waited for. A bot takes
    back only what a bot asked: an ask a person added stays in the Needs you of whom it names until a person takes it
    back or it is answered."""
    H = _h()
    requirement = R.get(conn, requirement_id)
    if not requirement or not requirement.get("visit_id"):
        _refuse(conn, by, "not-found", "No such requirement on a task")
    visit = H._one(conn, "SELECT * FROM visits WHERE id=?", (requirement["visit_id"],))
    if requirement.get("withdrawn_at") or not visit or visit.get("left_at"):
        return task(conn, visit["task_id"]) if visit else None
    if is_bot(by) and not requirement.get("by_bot"):
        _refuse(conn, by, "task_needs_person", "A person asked this: only a person takes it back", visit["task_id"])
    ts = H.now()
    conn.execute("UPDATE requirements SET withdrawn_at=? WHERE id=?", (ts, requirement_id))
    _reset_ladder(conn, visit, ts, by)
    _event(conn, visit["task_id"], by, "requirement_withdrawn", requirement_id, None, why)
    return advance(conn, visit["task_id"]) or task(conn, visit["task_id"])
