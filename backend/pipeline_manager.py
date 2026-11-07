# pyright: strict
"""The pipeline manager: a plain-code pass for each pipeline that has a manager, every `manager_every_minutes`.

A pass reads the pipeline's open tickets in SQL and lists the exceptions:

- a stage requirement not met in the ticket's current visit: its reviewer is asked, once per visit (`ask`);
- a person reviewer's requirement with the card past the stage's Limit: the backup is asked, once per visit (`ask_backup`);
- a card past the Limit ("Warn after"), read with the step_age clock, so time on hold does not count;
- failed decisions in the visit (listed only).

Allowed actions only (D44): ask, ask_backup, refile, hold, release_hold, open_botops, forward, propose_rule. This pass emits
ask, ask_backup and open_botops. refile is wired with the check pools (M2), so it is not emitted here. No model is called:
the manager is woken only for judgment (Message manager, Track C), so an empty exception list makes no model call.

The watchdog: a manager whose passes stopped (MISSED_RUNS missed runs) opens one BotOps task per stall.

`run_due` is the one guarded dispatcher the scheduler calls: each step's failure is recorded and the others still run.
Each pipeline's pass is its own transaction, and a pass that raised is recorded as its own failed run.
"""

import logging
import secrets
from weakref import WeakKeyDictionary
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from . import hubdb
from . import step_age

# hubdb is untyped: its functions are Any here (as in stranded_resume.py), not partially unknown.
H: Any = hubdb
SA: Any = step_age  # step_age is read through Any for the same reason

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth

LOG = logging.getLogger("tico.pipeline_manager")
ALLOWED = ("ask", "ask_backup", "refile", "hold", "release_hold", "open_botops", "forward", "propose_rule")
EMITTED = ("ask", "ask_backup", "open_botops")
MISSED_RUNS = 3  # a manager that misses this many passes in a row gets one BotOps task
BOTOPS = "bot:botops"
WAKE = "pipeline_manager"
DEFAULT_EVERY = 10
REVIEWED = ("person", "bot", "bot_then_person")
BACKUP_REVIEWED = ("person", "bot_then_person")
SELECT_PIPELINE = "SELECT id, name, manager, manager_every_minutes, created_at FROM task_types"


def when_of(at: Any) -> datetime:
    """`at` as an aware UTC datetime (now when it is None; a string is an ISO time)."""
    if at is None:
        return datetime.now(timezone.utc)
    if isinstance(at, str):
        parsed = H.parse_ts(at)
        if parsed is None:
            raise ValueError(f"unreadable time: {at!r}")
        at = parsed
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc)


def stamp_of(at: Any) -> str:
    return when_of(at).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


def _managed(c: "sqlite3.Connection") -> list[dict[str, Any]]:
    """The pipelines that have a manager bot."""
    return H._rows(c.execute(SELECT_PIPELINE + " WHERE manager IS NOT NULL AND manager<>''"))


def _every(pipeline: dict[str, Any]) -> int:
    return max(1, int(pipeline.get("manager_every_minutes") or DEFAULT_EVERY))


def last_run(c: "sqlite3.Connection", pipeline_id: str) -> dict[str, Any] | None:
    """The pipeline's newest pass (any status), or None."""
    rows = H._rows(
        c.execute(
            "SELECT * FROM pipeline_manager_runs WHERE pipeline_id=? ORDER BY started_at DESC, id DESC LIMIT 1",
            (pipeline_id,),
        )
    )
    return rows[0] if rows else None


def _due(c: "sqlite3.Connection", pipeline: dict[str, Any], when: datetime) -> bool:
    last = last_run(c, pipeline["id"])
    if last is None:
        return True
    started = H.parse_ts(last["started_at"])
    return started is None or when - started >= timedelta(minutes=_every(pipeline))


def stalled(c: "sqlite3.Connection", pipeline: dict[str, Any], at: Any) -> int:
    """How many passes this manager has missed: the whole intervals since its last pass started, less the one that is due."""
    when = when_of(at)
    last = last_run(c, pipeline["id"])
    base = H.parse_ts(last["started_at"]) if last else H.parse_ts(pipeline.get("created_at") or "")
    if base is None:
        return 0
    intervals = int((when - base).total_seconds() // (_every(pipeline) * 60))
    return max(0, intervals - 1)


def _visits(c: "sqlite3.Connection", pipeline_id: str) -> list[dict[str, Any]]:
    """The open stage visits of the pipeline's live tickets."""
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    return H._rows(
        c.execute(
            "SELECT v.id AS visit_id, v.task_id, v.pipeline_stage_id, v.entered_at FROM stage_visits v "
            f"JOIN tasks t ON t.id = v.task_id WHERE v.pipeline_id=? AND v.left_at IS NULL AND t.status IN ({marks}) "
            "ORDER BY v.entered_at, v.id",
            (pipeline_id, *H.ACTIVE_STATUSES),
        )
    )


def _holds(c: "sqlite3.Connection", task_id: str) -> tuple[bool, list[str]]:
    """(whether a hold is open now, the times the task's holds were released)."""
    rows = H._rows(c.execute("SELECT released_at FROM task_holds WHERE task_id=?", (task_id,)))
    open_now = any(not row["released_at"] for row in rows)
    return open_now, [row["released_at"] for row in rows if row["released_at"]]


def over_limit(c: "sqlite3.Connection", pipeline_id: str, at: Any) -> list[dict[str, Any]]:
    """The live tickets of a pipeline past their stage's Limit, oldest first. The clock is step_age's: a ticket on hold
    does not run its clock, and it starts again at the release."""
    rows = H._rows(
        c.execute(
            f"SELECT * FROM tasks WHERE type_id=? AND status IN ({','.join('?' * len(H.ACTIVE_STATUSES))})",
            (pipeline_id, *H.ACTIVE_STATUSES),
        )
    )
    since = SA.since_map(c, rows)
    timed = SA._timed_steps(c)
    stamp = stamp_of(at)
    out: list[dict[str, Any]] = []
    for row in rows:
        start = since.get(row["id"])
        limit = SA.late_limit(c, row, timed)
        if not start or not limit:
            continue
        open_now, released = _holds(c, row["id"])
        if open_now:
            continue
        if released:
            start = max(start, max(released))
        try:
            hours = SA._hours(stamp, start)
        except ValueError:
            continue
        if hours >= limit:
            out.append(
                {
                    "task_id": row["id"],
                    "title": row.get("title") or "",
                    "pipeline_stage_id": row.get("step_id") or "",
                    "stage": SA._step(c, row),
                    "hours": round(hours, 2),
                    "limit_hours": limit,
                    "since": start,
                }
            )
    out.sort(key=lambda item: (item["since"], item["task_id"]))
    return out


def _met(c: "sqlite3.Connection", visit_id: str, requirement_id: str) -> bool:
    return (
        c.execute(
            "SELECT 1 FROM stage_decisions WHERE stage_visit_id=? AND stage_requirement_id=? "
            "AND verdict IN ('met','override') AND undone_at IS NULL LIMIT 1",
            (visit_id, requirement_id),
        ).fetchone()
        is not None
    )


def _ask_once(
    c: "sqlite3.Connection",
    *,
    pipeline_id: str,
    run_id: str,
    task: dict[str, Any],
    visit_id: str,
    requirement_id: str,
    action: str,
    target: str,
    text: str,
    at: str,
) -> bool:
    """Ask `target` about one requirement of one visit, once. Returns whether it was asked now."""
    if action not in EMITTED:
        raise ValueError(f"the pipeline manager may not {action}")
    done = c.execute(
        "SELECT 1 FROM pipeline_manager_actions WHERE stage_visit_id=? AND stage_requirement_id=? AND action=?",
        (visit_id, requirement_id, action),
    ).fetchone()
    if done is not None:
        return False
    c.execute(
        "INSERT INTO pipeline_manager_actions (id, pipeline_id, pipeline_manager_run_id, task_id, stage_visit_id, "
        "stage_requirement_id, action, target_actor, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (new_id("pma"), pipeline_id, run_id, task["id"], visit_id, requirement_id, action, target, text, at),
    )
    H._wake(c, task, target, text, {"wake": WAKE}, trigger="schedule", source="pipeline_manager." + action)
    return True


def _pass(c: "sqlite3.Connection", pipeline: dict[str, Any], at: datetime) -> dict[str, int]:
    """One pass over a pipeline, inside the caller's transaction: its run row, its actions and wakes."""
    pid = pipeline["id"]
    stamp = stamp_of(at)
    run_id = new_id("pmr")
    c.execute(
        "INSERT INTO pipeline_manager_runs (id, pipeline_id, started_at, status) VALUES (?,?,?,'ok')",
        (run_id, pid, stamp),
    )
    over = over_limit(c, pid, at)
    over_ids = {item["task_id"] for item in over}
    exceptions: list[str] = [f"over:{item['task_id']}" for item in over]
    actions = 0
    for visit in _visits(c, pid):
        task = H.task(c, visit["task_id"])
        if task is None:
            continue
        requirements = H._rows(
            c.execute(
                "SELECT id, name, reviewed_by, reviewer_id, backup_reviewer_id FROM stage_requirements "
                "WHERE pipeline_stage_id=? AND retired_at IS NULL ORDER BY position, name",
                (visit["pipeline_stage_id"],),
            )
        )
        for req in requirements:
            if _met(c, visit["visit_id"], req["id"]):
                continue
            exceptions.append(f"missing:{visit['visit_id']}:{req['id']}")
            if req["reviewer_id"] and req["reviewed_by"] in REVIEWED:
                actions += int(
                    _ask_once(
                        c,
                        pipeline_id=pid,
                        run_id=run_id,
                        task=task,
                        visit_id=visit["visit_id"],
                        requirement_id=req["id"],
                        action="ask",
                        target=req["reviewer_id"],
                        text=f"{req['name']} is still missing on {task['title']}. Add it or say why it is not there.",
                        at=stamp,
                    )
                )
            if visit["task_id"] in over_ids and req["backup_reviewer_id"] and req["reviewed_by"] in BACKUP_REVIEWED:
                actions += int(
                    _ask_once(
                        c,
                        pipeline_id=pid,
                        run_id=run_id,
                        task=task,
                        visit_id=visit["visit_id"],
                        requirement_id=req["id"],
                        action="ask_backup",
                        target=req["backup_reviewer_id"],
                        text=f"{task['title']} is past its Limit on {req['name']}. You are the backup reviewer.",
                        at=stamp,
                    )
                )
    for failed in H._rows(
        c.execute(
            "SELECT d.stage_visit_id AS visit_id, d.stage_requirement_id AS requirement_id FROM stage_decisions d "
            "JOIN stage_visits v ON v.id = d.stage_visit_id WHERE v.pipeline_id=? AND v.left_at IS NULL "
            "AND d.verdict='failed' AND d.undone_at IS NULL",
            (pid,),
        )
    ):
        exceptions.append(f"failed:{failed['visit_id']}:{failed['requirement_id']}")
    c.execute(
        "UPDATE pipeline_manager_runs SET finished_at=?, exceptions_count=?, actions_count=? WHERE id=?",
        (stamp_of(datetime.now(timezone.utc)), len(exceptions), actions, run_id),
    )
    return {"exceptions": len(exceptions), "actions": actions}


def _failed(c: "sqlite3.Connection", pipeline: dict[str, Any], at: datetime, error: BaseException) -> None:
    """Record a pass that raised: its own run row, so the rail's log shows it first."""
    c.execute(
        "INSERT INTO pipeline_manager_runs (id, pipeline_id, started_at, finished_at, status, error) "
        "VALUES (?,?,?,?,'failed',?)",
        (new_id("pmr"), pipeline["id"], stamp_of(at), stamp_of(datetime.now(timezone.utc)), type(error).__name__),
    )


def tick(store: Any, at: Any) -> None:
    """One pass for each pipeline whose manager is due. Plain code; no model call."""
    when = when_of(at)
    with store.read() as c:
        due = [p for p in _managed(c) if _due(c, p, when)]
    for pipeline in due:
        try:
            with store.transaction() as c:
                _pass(c, pipeline, when)
        except Exception as exc:  # noqa: BLE001 - the failure is its own run; the other pipelines still run
            LOG.warning("pipeline manager pass failed for %s: %s", pipeline["id"], type(exc).__name__)
            with store.transaction() as c:
                _failed(c, pipeline, when, exc)


def watchdog(store: Any, at: Any) -> None:
    """Opens one BotOps task for each pipeline manager that has missed MISSED_RUNS passes, once per stall."""
    when = when_of(at)
    with store.read() as c:
        stuck = [p["id"] for p in _managed(c) if stalled(c, p, when) >= MISSED_RUNS]
    for pid in stuck:
        with store.transaction() as c:
            found = c.execute(SELECT_PIPELINE + " WHERE id=?", (pid,)).fetchone()
            if found is None:
                continue
            row = dict(found)
            missed = stalled(c, row, when)
            if missed < MISSED_RUNS:
                continue
            last = last_run(c, pid)
            base = last["started_at"] if last else (row.get("created_at") or "")
            opened = c.execute(
                "SELECT 1 FROM pipeline_manager_actions WHERE pipeline_id=? AND action='open_botops' AND created_at>? "
                "LIMIT 1",
                (pid, base),
            ).fetchone()
            if opened is not None:
                continue
            made = H.task_create(
                c,
                H.KEEPER,
                f"Pipeline manager stopped: {row['name']}",
                f"The manager of {row['name']} has missed {missed} passes; its last pass started {base or 'never'}. "
                "Check the manager bot and the pipeline's log.",
                BOTOPS,
                deduplicate=False,
                lint=False,
                private=False,
                trigger="schedule",
                trigger_source="pipeline_manager.watchdog",
            )
            c.execute(
                "INSERT INTO pipeline_manager_actions (id, pipeline_id, pipeline_manager_run_id, task_id, stage_visit_id, "
                "stage_requirement_id, action, target_actor, note, created_at) "
                "VALUES (?,?,?,?,NULL,NULL,'open_botops',?,?,?)",
                (
                    new_id("pma"),
                    pid,
                    last["id"] if last else "",
                    made["id"],
                    BOTOPS,
                    f"missed {missed} passes",
                    stamp_of(when),
                ),
            )


def _readable(c: "sqlite3.Connection", who: str, task_id: str) -> bool:
    row = H.task(c, task_id)
    return bool(row) and H.task_private_readable(c, who, row)


def rail(c: "sqlite3.Connection", pipeline_id: str, at: Any = None, who: str | None = None) -> dict[str, Any] | None:
    """What the pipeline's rail shows: the last pass, the log (errors first) and the over-limit list. With `who`, the
    tickets that reader may not see are left out. None when there is no such pipeline."""
    found = c.execute(SELECT_PIPELINE + " WHERE id=?", (pipeline_id,)).fetchone()
    if found is None:
        return None
    row = dict(found)
    runs = H._rows(
        c.execute(
            "SELECT id, started_at, finished_at, status, exceptions_count, actions_count, model_called, error "
            "FROM pipeline_manager_runs WHERE pipeline_id=? ORDER BY started_at DESC, id DESC LIMIT 50",
            (pipeline_id,),
        )
    )
    actions = H._rows(
        c.execute(
            "SELECT id, task_id, action, target_actor, note, created_at FROM pipeline_manager_actions "
            "WHERE pipeline_id=? ORDER BY created_at DESC, id DESC LIMIT 50",
            (pipeline_id,),
        )
    )
    at = when_of(at)
    over = over_limit(c, pipeline_id, at)
    if who is not None:
        over = [item for item in over if _readable(c, who, item["task_id"])]
        actions = [item for item in actions if not item["task_id"] or _readable(c, who, item["task_id"])]
    return {
        "pipeline_id": pipeline_id,
        "manager": row.get("manager"),
        "every_minutes": _every(row),
        "missed_runs": stalled(c, row, at),
        "last_run": runs[0] if runs else None,
        "errors": [run for run in runs if run["status"] == "failed"],
        "over_limit": over,
        "late": over,  # the coordinator's name for the over-limit list
        "log": [*[run for run in runs if run["status"] == "failed"], *actions],
        "actions": actions,
    }


THROTTLE_SECONDS = 60  # the scheduler calls run_due every tick; the passes themselves run at most this often per store
_LAST_RUN: "WeakKeyDictionary[Any, datetime]" = (
    WeakKeyDictionary()
)  # per store; weak, so a dead store's id is never reused


def run_due(store: Any, at: Any, failures: list[dict[str, str]] | None = None) -> None:
    """The scheduler's one call: the pipeline passes, the watchdog and the Factory Managers. A failure in one is logged
    and recorded in `failures` (the scheduler's pass record); the others still run. Throttled: at most one round per
    THROTTLE_SECONDS per store (tests set THROTTLE_SECONDS to 0)."""
    from . import factory_manager

    when = when_of(at)
    last = _LAST_RUN.get(store)
    if last is not None and (when - last).total_seconds() < THROTTLE_SECONDS:
        return
    _LAST_RUN[store] = when
    for name, step in (
        ("pipeline_manager", tick),
        ("pipeline_manager_watchdog", watchdog),
        ("factory_manager", factory_manager.tick),
    ):
        try:
            step(store, when)
        except Exception as exc:  # noqa: BLE001 - recorded below; the other passes still run
            LOG.warning("%s failed: %s", name, type(exc).__name__)
            if failures is not None:
                failures.append({name: type(exc).__name__})


def install(app: "FastAPI", store: Any, mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """GET /api/v2/pipelines/{pipeline_id}/manager: the rail's data (read only)."""
    from fastapi import Request

    from .store import Problem

    @app.get("/api/v2/pipelines/{pipeline_id}/manager")
    def pipeline_manager_rail(request: Request, pipeline_id: str) -> dict[str, Any]:
        """The pipeline manager's rail: its last pass, the log with errors first, and the tickets over their Limit."""
        who = request.state.identity
        if who.role not in ("owner", "human", "bot"):
            raise Problem("forbidden", "People and bots read the pipeline manager", 403)
        with store.read() as c:
            out = rail(c, pipeline_id, datetime.now(timezone.utc), who.actor)
        if out is None:
            raise Problem("not_found", "No such pipeline", 404)
        return out
