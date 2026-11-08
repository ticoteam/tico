# pyright: strict
"""The pipeline manager: a plain-code pass for each pipeline that has a manager, every `manager_every_minutes`.

A pass reads the pipeline's open tickets in SQL and lists the exceptions:

- a stage requirement the engine does not count as met in the ticket's current visit (its current standing, stale ones
  included): the assignee is asked when work is missing or out of date, the reviewer when work waits for a decision,
  once per visit (`ask`);
- a person reviewer's requirement with the card past the stage's Limit: the backup is asked, once per visit (`ask_backup`);
- a requirement that failed twice in the same visit: the manager is woken once for a judgment, once per visit and
  requirement (plain event dedupe; the budget);
- a card past the Limit ("Warn after"), read with pipeline_manager.late_tickets, so time on hold does not count;
- failed decisions in the visit (listed only).

Allowed actions only (D44): ask, ask_backup, refile, hold, release_hold, open_botops, forward, propose_rule. This pass emits
ask, ask_backup and open_botops. refile is wired with the check pools (M2), so it is not emitted here. No model is called
in the pass: the manager is woken only for judgment (a wake, once per requirement and visit), so an empty exception list
makes no model call.

The watchdog: a manager whose passes stopped (MISSED_RUNS missed runs) opens one BotOps task per stall. Only a pass that
finished ok counts as healthy; run_due runs the watchdog before the pass, so a pass recorded after an outage cannot hide it.

`run_due` is the one guarded dispatcher the scheduler calls: each step's failure is recorded and the others still run.
Each pipeline's pass is its own transaction, and a pass that raised is recorded as its own failed run.
"""

import logging
import secrets
from weakref import WeakKeyDictionary
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from . import hubdb
from . import pipeline_engine as E
from . import pipeline_rights
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
REPEATED_FAILURES = 2  # a requirement that fails this often in one visit wakes the manager for a judgment
RAIL_CAP = 100  # the most over-limit tickets the rail returns; the total is still counted
JUDGMENT_EVENT = "pipeline_manager.judgment"
BOTOPS = "bot:botops"
WAKE = "pipeline_manager"
DEFAULT_EVERY = 10
REVIEWED = ("person", "bot", "bot_then_person")
BACKUP_REVIEWED = ("person", "bot_then_person")
SELECT_PIPELINE = "SELECT id, name, manager, manager_every_minutes, goal, created_at FROM task_types"


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
    """The pipeline's newest pass (any status), or None. Scheduling reads it: a failed pass is retried on time."""
    rows = H._rows(
        c.execute(
            "SELECT * FROM pipeline_manager_runs WHERE pipeline_id=? ORDER BY started_at DESC, id DESC LIMIT 1",
            (pipeline_id,),
        )
    )
    return rows[0] if rows else None


def last_ok_run(c: "sqlite3.Connection", pipeline_id: str) -> dict[str, Any] | None:
    """The pipeline's newest pass that finished ok, or None. Health reads it: a failed pass is not a healthy one."""
    rows = H._rows(
        c.execute(
            "SELECT * FROM pipeline_manager_runs WHERE pipeline_id=? AND status='ok' "
            "ORDER BY started_at DESC, id DESC LIMIT 1",
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


def stall_clock(c: "sqlite3.Connection", pipeline_id: str) -> dict[str, Any] | None:
    """The pass that starts the clock on missed passes: the last ok one, else the first attempt (a failed pass), else None.
    A manager with no run yet is not behind: its first pass is due on the next tick. Its creation time is no clock, so a
    manager set on an old pipeline is not judged from when the pipeline was made (the watchdog's first tick after an
    update opened a "stopped" task for every one)."""
    last = last_ok_run(c, pipeline_id)
    if last is not None:
        return last
    rows = H._rows(
        c.execute(
            "SELECT * FROM pipeline_manager_runs WHERE pipeline_id=? ORDER BY started_at, id LIMIT 1", (pipeline_id,)
        )
    )
    return rows[0] if rows else None


def stalled(c: "sqlite3.Connection", pipeline: dict[str, Any], at: Any) -> int:
    """How many passes this manager has missed: the whole intervals since its clock started (stall_clock), less the one
    that is due. Missing passes count from one run interval plus MISSED_RUNS after that start."""
    when = when_of(at)
    clock = stall_clock(c, pipeline["id"])
    base = H.parse_ts(clock["started_at"]) if clock else None
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


BIND_CHUNK = 500  # ids per SQL statement: well under SQLite's bind-parameter limit, whatever the pipeline's size


def _by_ids(
    c: "sqlite3.Connection", ids: list[str], sql: str, tail: list[Any] | tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    """Run `sql` once per BIND_CHUNK of `ids`: `{marks}` in it becomes the placeholders, and `tail` follows them. A ticket
    is in one chunk only, so each ticket keeps its rows in the order the SQL sorts them."""
    out: list[dict[str, Any]] = []
    for start in range(0, len(ids), BIND_CHUNK):
        part = ids[start : start + BIND_CHUNK]
        statement = sql.replace("{marks}", ",".join("?" * len(part)))
        out.extend(H._rows(c.execute(statement, (*part, *tail))))
    return out


def late_tickets(c: "sqlite3.Connection", rows: list[dict[str, Any]], at: Any) -> dict[str, dict[str, Any]]:
    """The one Late rule: the manager's rail and the overview both read it. A live ticket is late once its active time in
    its stage's Warn after (task_steps.limit_minutes, D55) reaches the limit. Active time is the time since the start of its
    open stage visit, less the time on hold (any task_holds row, open or released) and the time it sat in status waiting
    (D40), so a pause never restarts the clock and time before a pause still counts. A new visit starts from zero. A ticket
    with no open visit counts from its latest move into its step, and one with neither from its creation.
    `rows` are task rows (id, type_id, step_id, status, created_at). Returns {task id: {since, hours, limit_hours,
    limit_minutes}} for the late ones only; `since` is when the visit's clock started."""
    now = when_of(at)
    limits: dict[str, int] = {
        str(r["id"]): r["limit_minutes"]
        for r in H._rows(c.execute("SELECT id, limit_minutes FROM task_steps WHERE limit_minutes IS NOT NULL"))
    }
    live = [
        r
        for r in rows
        if r.get("status") in H.ACTIVE_STATUSES and r.get("status") != "waiting" and str(r.get("step_id")) in limits
    ]
    if not live:
        return {}
    ids = [r["id"] for r in live]
    entered = {
        r["task_id"]: r["entered_at"]
        for r in _by_ids(
            c, ids, "SELECT task_id, entered_at FROM stage_visits WHERE left_at IS NULL AND task_id IN ({marks})"
        )
    }
    # A ticket with no open visit (a pipeline with no requirements has none) started its stage at its latest step move
    # into that step, not when it was made (Fix L2-4).
    into_step = {
        (r["task_id"], r["step_id"]): r["at"]
        for r in _by_ids(
            c,
            ids,
            "SELECT subject_id AS task_id, new AS step_id, max(created_at) AS at FROM history_events "
            "WHERE subject_type='task' AND kind='step' AND subject_id IN ({marks}) GROUP BY subject_id, new",
        )
    }
    began: dict[str, datetime] = {}
    for r in live:
        ticket = r["id"]
        start = H.parse_ts(entered.get(ticket) or into_step.get((ticket, str(r.get("step_id"))), "") or "")
        start = start or H.parse_ts(r.get("created_at") or "")
        if start is not None:
            began[ticket] = start
    if not began:
        return {}
    # The paused spans of each ticket: every hold (open ones run to now) and every stretch in status waiting, which pauses
    # the clock with or without a hold row (product roadmap moves make none).
    paused: dict[str, list[tuple[datetime, datetime]]] = {}
    for hold in _by_ids(c, ids, "SELECT task_id, created_at, released_at FROM task_holds WHERE task_id IN ({marks})"):
        start = H.parse_ts(hold["created_at"])
        end = H.parse_ts(hold["released_at"]) or now
        if start is not None:
            paused.setdefault(hold["task_id"], []).append((start, end))
    earliest = stamp_of(min(began.values()))
    waiting_since: dict[str, datetime] = {}
    for event in _by_ids(
        c,
        ids,
        "SELECT subject_id AS task_id, old, new, created_at FROM history_events WHERE subject_type='task' "
        "AND kind='status' AND subject_id IN ({marks}) AND created_at >= ? ORDER BY created_at, rowid",
        [earliest],
    ):
        ticket = str(event["task_id"])
        when = H.parse_ts(event["created_at"])
        if when is None:
            continue
        if event["new"] == "waiting":
            waiting_since[ticket] = when
        elif event["old"] == "waiting":
            # A stretch that began before this visit counts from the visit's start.
            start = waiting_since.pop(ticket, None) or began.get(ticket) or when
            paused.setdefault(ticket, []).append((start, when))
    for ticket, start in waiting_since.items():
        paused.setdefault(ticket, []).append((start, now))
    out: dict[str, dict[str, Any]] = {}
    for r in live:
        ticket = r["id"]
        if ticket not in began:
            continue
        start = began[ticket]
        minutes = (now - start).total_seconds() / 60 - _paused_minutes(paused.get(ticket, []), start, now)
        limit = limits[str(r["step_id"])]
        if minutes >= limit:
            out[ticket] = {
                "since": stamp_of(start),
                "hours": round(minutes / 60, 2),
                "limit_hours": limit / 60,
                "limit_minutes": limit,
            }
    return out


LATE_CAP = 500


def late_listing(
    c: "sqlite3.Connection",
    actor: str,
    visible_sql: str,
    pipeline_id: str,
    at: Any,
    wanted: set[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """The Late tickets of a pipeline that `actor` may see, as the endpoint returns them, and whether the list was cut at
    LATE_CAP. Visibility is one query over the late ids (pipeline_rights.list_sql), so the query count does not grow with
    the number of late tickets. `wanted` limits the answer to those ids."""
    readable = pipeline_rights.list_sql(c, actor, visible_sql)
    # Visibility and the asked ids narrow the tickets before any clock is computed: only those rows are evaluated.
    if wanted is not None:
        found = _late_found(c, _live_rows(c, pipeline_id, ids=sorted(wanted), readable=readable), at)
        more = len(found) > LATE_CAP
    else:
        found, more = _late_page(c, pipeline_id, at, readable)
    return {
        item["task_id"]: {"since": item["since"], "hours": item["hours"], "limit_hours": item["limit_hours"]}
        for item in found[:LATE_CAP]
    }, more


LATE_PAGE = 500  # live tickets evaluated per page of the pipeline-wide Late list


def _late_page(c: "sqlite3.Connection", pipeline_id: str, at: Any, readable: str) -> tuple[list[dict[str, Any]], bool]:
    """The pipeline-wide Late list, read a page of LATE_PAGE live tickets at a time (by id) until LATE_CAP + 1 late ones
    are found. The work is bounded by the pages read; `more` says whether a late ticket was left out of the answer."""
    found: list[dict[str, Any]] = []
    after = ""
    while len(found) <= LATE_CAP:
        page = _live_rows(c, pipeline_id, after=after, readable=readable, limit=LATE_PAGE)
        found.extend(_late_found(c, page, at))
        if len(page) < LATE_PAGE:
            break
        after = str(page[-1]["id"])
    found.sort(key=lambda item: (item["since"], item["task_id"]))
    return found, len(found) > LATE_CAP


def _paused_minutes(spans: list[tuple[datetime, datetime]], start: datetime, now: datetime) -> float:
    """The minutes of [start, now] that the spans cover, each counted once however they overlap."""
    clipped = sorted((max(a, start), min(b, now)) for a, b in spans if min(b, now) > max(a, start))
    total = 0.0
    edge: datetime | None = None
    for a, b in clipped:
        if edge is None or a > edge:
            total += (b - a).total_seconds() / 60
            edge = b
        elif b > edge:
            total += (b - edge).total_seconds() / 60
            edge = b
    return total


def _live_rows(
    c: "sqlite3.Connection",
    pipeline_id: str,
    *,
    ids: list[str] | None = None,
    readable: str | None = None,
    after: str = "",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """The narrow rows of a pipeline's live tickets that the Late rule and the rail need: all of them, or only `ids` (in
    chunks), or one page by id after `after`. `readable` (pipeline_rights.list_sql) narrows them to what a reader may open."""
    statuses = ",".join("?" * len(H.ACTIVE_STATUSES))
    where = f"type_id=? AND status IN ({statuses})"
    tail: list[Any] = [pipeline_id, *H.ACTIVE_STATUSES]
    if readable:
        where += f" AND ({readable})"
    columns = "SELECT id, type_id, step_id, status, title, created_at FROM tasks WHERE "
    if ids is not None:
        return _by_ids(c, ids, columns + f"id IN ({{marks}}) AND {where}", tail)
    sql = columns + f"id > ? AND {where} ORDER BY id"
    args: list[Any] = [after, *tail]
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
    return H._rows(c.execute(sql, args))


def over_limit(c: "sqlite3.Connection", pipeline_id: str, at: Any) -> list[dict[str, Any]]:
    """The live tickets of a pipeline past their stage's Warn after, oldest first (late_tickets). For the rail, which counts
    every one of them."""
    return _late_found(c, _live_rows(c, pipeline_id), at)


def _late_found(c: "sqlite3.Connection", rows: list[dict[str, Any]], at: Any) -> list[dict[str, Any]]:
    """The late ones among `rows` (task rows from _live_rows), oldest first, with their stage names in one query."""
    late = late_tickets(c, rows, at)
    if not late:
        return []
    titles = {str(row["id"]): row for row in rows}
    steps = [str(titles[ticket]["step_id"] or "") for ticket in late]
    names = {
        str(r["id"]): r["name"]
        for r in H._rows(
            c.execute(f"SELECT id, name FROM task_steps WHERE id IN ({','.join('?' * len(steps))})", steps)
        )
    }
    out = [
        {
            "task_id": ticket,
            "title": titles[ticket].get("title") or "",
            "pipeline_stage_id": titles[ticket].get("step_id") or "",
            "stage": names.get(str(titles[ticket].get("step_id") or ""), "its step"),
            **info,
        }
        for ticket, info in late.items()
    ]
    out.sort(key=lambda item: (item["since"], item["task_id"]))
    return out


def _task_rows(c: "sqlite3.Connection", task_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The narrow task rows for these tickets, from one query."""
    if not task_ids:
        return {}
    marks = ",".join("?" * len(task_ids))
    return {
        str(r["id"]): r
        for r in H._rows(
            c.execute(
                "SELECT id, type_id, step_id, status, title, owner_id, work_revision, created_at, conversation_id "
                f"FROM tasks WHERE id IN ({marks})",
                task_ids,
            )
        )
    }


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
    """Ask `target` about one requirement of one visit, once. Returns whether it was asked now. The once-per-visit
    key is written only when the ask reached its target: an undelivered ask (a target who cannot read the ticket) is
    logged and tried again on a later pass."""
    if action not in EMITTED:
        raise ValueError(f"the pipeline manager may not {action}")
    done = c.execute(
        "SELECT 1 FROM pipeline_manager_actions WHERE stage_visit_id=? AND stage_requirement_id=? AND action=?",
        (visit_id, requirement_id, action),
    ).fetchone()
    if done is not None:
        return False
    if H._wake(c, task, target, text, {"wake": WAKE}, trigger="schedule", source="pipeline_manager." + action) is None:
        LOG.warning("pipeline manager %s on %s was not delivered to %s", action, task["id"], target)
        return False
    c.execute(
        "INSERT INTO pipeline_manager_actions (id, pipeline_id, pipeline_manager_run_id, task_id, stage_visit_id, "
        "stage_requirement_id, action, target_actor, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (new_id("pma"), pipeline_id, run_id, task["id"], visit_id, requirement_id, action, target, text, at),
    )
    return True


def _judgment_once(
    c: "sqlite3.Connection",
    *,
    pipeline: dict[str, Any],
    task: dict[str, Any],
    visit_id: str,
    requirement: dict[str, Any],
    failures: int,
) -> bool:
    """Wake the pipeline's manager once for a requirement that keeps failing in one visit. The budget: one wake per visit
    and requirement, kept as an event so a later pass does not repeat it. The wake carries the pipeline's goal (D58)."""
    manager = pipeline.get("manager")
    key = f"{visit_id}:{requirement['id']}"
    if (
        not manager
        or c.execute("SELECT 1 FROM events WHERE action=? AND target=? LIMIT 1", (JUDGMENT_EVENT, key)).fetchone()
    ):
        return False
    goal = f" Goal: {pipeline['goal']}" if pipeline.get("goal") else ""
    text = (
        f"{requirement['name']} failed {failures} times on {task['title']}. Judge whether it is met, "
        f"or say what the card needs.{goal}"
    )
    if (
        H._wake(
            c, task, H.bot_actor(manager), text, {"wake": WAKE}, trigger="schedule", source="pipeline_manager.judgment"
        )
        is None
    ):
        return False
    H.event(c, H.KEEPER, JUDGMENT_EVENT, key, {"pipeline": pipeline["id"], "task": task["id"], "failures": failures})
    return True


def _pass(c: "sqlite3.Connection", pipeline: dict[str, Any], at: datetime) -> dict[str, int]:
    """One pass over a pipeline, inside the caller's transaction: its run row, its actions and wakes. What is unmet is the
    engine's current standing of each requirement in the visit's stage (pipeline_engine.standings), not a record of
    whether it was ever met."""
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
    visits = _visits(c, pid)
    tasks = _task_rows(c, list({str(v["task_id"]) for v in visits}))
    standing = E.standings(c, list(tasks.values())) if tasks else {}
    for visit in visits:
        task = tasks.get(str(visit["task_id"]))
        if task is None:
            continue
        for found in standing.get(str(task["id"]), []):
            req = found.requirement
            if found.stage_id != str(visit["pipeline_stage_id"]) or found.state == "met":
                continue
            exceptions.append(f"missing:{visit['visit_id']}:{found.id}")
            if found.state == "waiting":
                # Work waits for a decision: its reviewer is asked. Nothing else is owed by the assignee.
                target = req["reviewer_id"] if req["reviewed_by"] in REVIEWED else None
                text = f"{req['name']} on {task['title']} is waiting for your decision."
            elif found.state == "failed":
                target = task.get("owner_id")
                text = f"{req['name']} on {task['title']} was judged not met. Fix it or say why it should count."
            else:  # none (missing) or out_of_date: the assignee owes the work
                target = task.get("owner_id")
                text = f"{req['name']} is still missing on {task['title']}. Add it or say why it is not there."
                if found.state == "out_of_date":
                    text = f"{req['name']} on {task['title']} is out of date. Add it again or say why it still holds."
            if target:
                actions += int(
                    _ask_once(
                        c,
                        pipeline_id=pid,
                        run_id=run_id,
                        task=task,
                        visit_id=visit["visit_id"],
                        requirement_id=found.id,
                        action="ask",
                        target=str(target),
                        text=text,
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
                        requirement_id=found.id,
                        action="ask_backup",
                        target=req["backup_reviewer_id"],
                        text=f"{task['title']} is past its Limit on {req['name']}. You are the backup reviewer.",
                        at=stamp,
                    )
                )
    for failed in H._rows(
        c.execute(
            "SELECT d.stage_visit_id AS visit_id, d.stage_requirement_id AS requirement_id, v.task_id AS task_id, "
            "COUNT(*) AS failures FROM stage_decisions d JOIN stage_visits v ON v.id = d.stage_visit_id "
            "WHERE v.pipeline_id=? AND v.left_at IS NULL AND d.verdict='failed' AND d.undone_at IS NULL "
            "GROUP BY d.stage_visit_id, d.stage_requirement_id, v.task_id ORDER BY d.stage_visit_id, d.stage_requirement_id",
            (pid,),
        )
    ):
        exceptions.append(f"failed:{failed['visit_id']}:{failed['requirement_id']}")
        if int(failed["failures"]) < REPEATED_FAILURES:
            continue
        task_row = tasks.get(str(failed["task_id"])) or H.task(c, failed["task_id"])
        req_row = c.execute(
            "SELECT id, name FROM stage_requirements WHERE id=?", (failed["requirement_id"],)
        ).fetchone()
        if task_row is None or req_row is None:
            continue
        actions += int(
            _judgment_once(
                c,
                pipeline=pipeline,
                task=task_row,
                visit_id=failed["visit_id"],
                requirement={"id": req_row[0], "name": req_row[1]},
                failures=int(failed["failures"]),
            )
        )
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
            last = last_ok_run(c, pid)
            clock = stall_clock(c, pid)
            base = clock["started_at"] if clock else ""
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
    total = len(over)
    over = over[:RAIL_CAP]  # the oldest first; the rail says how many more there are
    return {
        "pipeline_id": pipeline_id,
        "manager": row.get("manager"),
        "goal": row.get("goal") or None,
        "every_minutes": _every(row),
        "missed_runs": stalled(c, row, at),
        "last_run": runs[0] if runs else None,
        "errors": [run for run in runs if run["status"] == "failed"],
        "over_limit": over,
        "over_limit_total": total,
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
    # The watchdog runs first: it judges the passes that were missed before this round records one (a pass recorded
    # first would hide an outage from it).
    for name, step in (
        ("pipeline_manager_watchdog", watchdog),
        ("pipeline_manager", tick),
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
            # A members-only pipeline the reader may not see has no rail: the same answer as one that is not there.
            seen = pipeline_rights.may_see(c, who.task_actor or who.actor, pipeline_id)
            out = rail(c, pipeline_id, datetime.now(timezone.utc), who.actor) if seen else None
        if out is None:
            raise Problem("not_found", "No such pipeline", 404)
        return out

    @app.get("/api/v2/pipelines/{pipeline_id}/late")
    def pipeline_late(request: Request, pipeline_id: str, ids: str | None = None) -> dict[str, Any]:
        """The Late list the pipeline's cards read for their "Nh in step" time: the live tickets of the pipeline past
        their Warn after (late_tickets), only the ones the reader may see. `ids` (a comma list, the cards on screen) asks
        for those; without it the list is capped at LATE_CAP and `more` says whether it was cut."""
        who = request.state.identity
        if who.role not in ("owner", "human", "bot"):
            raise Problem("forbidden", "People and bots read the pipeline's Late list", 403)
        wanted: set[str] | None = None
        if ids is not None:
            wanted = {part.strip() for part in ids.split(",") if part.strip()}
            if len(wanted) > LATE_CAP:
                raise Problem("too_many_ids", f"Ask for at most {LATE_CAP} tickets at a time", 400)
        with store.read() as c:
            seen = pipeline_rights.may_see(c, who.task_actor or who.actor, pipeline_id)
            if not seen:
                raise Problem("not_found", "No such pipeline", 404)
            late, more = late_listing(
                c,
                who.task_actor or who.actor,
                auth.task_sql(c, who),  # pyright: ignore[reportUnknownMemberType]
                pipeline_id,
                datetime.now(timezone.utc),
                wanted,
            )
        return {"late": late, "more": more}
