# pyright: strict
"""Per-pipeline estimated spend caps, layered on the shared spend accounting and claim path."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from typing import Any

from . import hubdb, pipeline_rights, read_cache, usage_limits, usage_records
from .store import Problem

H: Any = hubdb  # hubdb is untyped; the pipeline settings module uses the same boundary.
UL: Any = usage_limits

NANO = 10**9
COPY = "Enter an amount above zero, or leave it blank."
PERIODS = (("daily", "day", "daily"), ("monthly", "month", "monthly"))


def amount_nano(value: object) -> int | None:
    """A valid positive cap in nanodollars, or None when blank."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Problem("validation", COPY, 422)
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number > UL.MAX_USD:
        raise Problem("validation", COPY, 422)
    stored = UL.nano(round(number, 6))
    if stored <= 0:
        raise Problem("validation", COPY, 422)
    return stored


def _bounds(at: datetime | None = None) -> dict[str, tuple[str, str, str]]:
    windows = UL.bounds(at)
    return {period: windows[period][0:3] for period, _, _ in PERIODS}


def spend_nano(c: sqlite3.Connection, pipeline_id: str, period: str, at: datetime | None = None) -> int:
    """Finished model estimates plus priced CI in nanodollars for this pipeline's UTC window."""
    key, start, end = _bounds(at)[period]
    del key
    model = c.execute(
        f"SELECT coalesce(sum(r.est_cost_usd),0) FROM {usage_records.RUNS} r "
        "JOIN tasks t ON t.id=r.task_id WHERE t.type_id=? AND r.finished_at>=? AND r.finished_at<?",
        (pipeline_id, start, end),
    ).fetchone()[0]
    ci = c.execute(
        "SELECT coalesce(sum(r.cost_cents),0) FROM ci_runs r JOIN tasks t ON t.id=r.task_id "
        "WHERE t.type_id=? AND r.seen_at<>'' AND r.seen_at>=? AND r.seen_at<?",
        (pipeline_id, start, end),
    ).fetchone()[0]
    return UL.nano(float(model or 0)) + int(ci or 0) * (NANO // 100)


def spend(c: sqlite3.Connection, pipeline_id: str, period: str, at: datetime | None = None) -> float:
    """Finished model estimates plus priced CI for tasks in this pipeline's UTC window."""
    return round(spend_nano(c, pipeline_id, period, at) / NANO, 6)


def read(c: sqlite3.Connection, actor: str, pipeline_id: str, at: datetime | None = None) -> dict[str, Any]:
    if not pipeline_rights.may_see(c, actor, pipeline_id):
        raise Problem("not_found", "No such pipeline", 404)
    row = c.execute("SELECT daily_cap_nano,monthly_cap_nano FROM task_types WHERE id=?", (pipeline_id,)).fetchone()
    if row is None:
        raise Problem("not_found", "No such pipeline", 404)
    return {
        "daily_cap_usd": row["daily_cap_nano"] / NANO if row["daily_cap_nano"] is not None else None,
        "monthly_cap_usd": row["monthly_cap_nano"] / NANO if row["monthly_cap_nano"] is not None else None,
        "today_usd": spend(c, pipeline_id, "daily", at),
        "month_usd": spend(c, pipeline_id, "monthly", at),
    }


def _target(c: sqlite3.Connection, pipeline_id: str) -> dict[str, Any] | None:
    row = read_cache.one(
        c,
        "SELECT id,name,manager,pipeline_visibility,daily_cap_nano,monthly_cap_nano FROM task_types WHERE id=?",
        (pipeline_id,),
    )
    return dict(row) if row else None


def _period_key(period: str, at: datetime | None) -> tuple[str, str, str]:
    return _bounds(at)[period]


def _recipient(c: sqlite3.Connection, row: dict[str, Any]) -> str | None:
    manager = str(row.get("manager") or "").strip()
    if manager:
        found = H.resolve_bot(c, manager)
        if found and found.get("state") == "active":
            return H.bot_actor(found["id"])
    return pipeline_rights.company_owner(c) or None


def _notice(c: sqlite3.Connection, row: dict[str, Any], recipient: str, word: str) -> None:
    H.say(c, H.KEEPER, recipient, f"{row['name']} is at 80% of its {word} cap.", kind="notice")


def _decision(
    c: sqlite3.Connection, row: dict[str, Any], recipient: str, period: str, cap_nano: int, at: datetime | None
) -> str:
    word = "daily" if period == "daily" else "monthly"
    current = spend(c, row["id"], period, at)
    body = (
        f"{row['name']} is over its {word} cap (${current:,.2f} spent against ${cap_nano / NANO:,.2f}). "
        "The hold remains until this period turns over or the cap is raised. Choose one:\n\n"
        "- Ask Chris to raise: sends the request to Chris; the manager cannot raise a cap.\n"
        "- Pause: keep the hold until the period turns over.\n"
        "- Reprioritize: change the manager's own task queue; the cap still holds.\n\n"
        "To record the choice, finish this task with the choice name as its note."
    )
    task = H.task_create(
        c,
        H.KEEPER,
        f"Pipeline cap decision: {row['name']} ({word})",
        body,
        recipient,
        deduplicate=False,
        lint=False,
        private=row.get("pipeline_visibility") == "members",
    )
    return str(task["id"])


def _level(c: sqlite3.Connection, row: dict[str, Any], period: str, at: datetime | None = None) -> None:
    cap_nano = row.get(period + "_cap_nano")
    if cap_nano is None or int(cap_nano) <= 0:
        return
    amount_nano = spend_nano(c, row["id"], period, at)
    level = 100 if amount_nano >= int(cap_nano) else 80 if amount_nano * 100 >= int(cap_nano) * 80 else 0
    if not level:
        return
    key, _, _ = _period_key(period, at)
    now = H.now()
    recipient = _recipient(c, row)
    if level == 100:
        # A run that jumps past both thresholds gets the decision task only, like Usage's existing limit path.
        c.execute(
            "INSERT OR IGNORE INTO pipeline_budget_alerts "
            "(pipeline_id,period,period_key,cap_nano,level,created_at) VALUES(?,?,?,?,80,?)",
            (row["id"], period, key, int(cap_nano), now),
        )
        c.execute(
            "INSERT OR IGNORE INTO pipeline_budget_alerts "
            "(pipeline_id,period,period_key,cap_nano,level,created_at) VALUES(?,?,?,?,100,?)",
            (row["id"], period, key, int(cap_nano), now),
        )
        if c.execute("SELECT changes()").fetchone()[0] and recipient:
            task_id = _decision(c, row, recipient, period, int(cap_nano), at)
            c.execute(
                "UPDATE pipeline_budget_alerts SET decision_task_id=? WHERE pipeline_id=? AND period=? "
                "AND period_key=? AND cap_nano=? AND level=100",
                (task_id, row["id"], period, key, int(cap_nano)),
            )
    else:
        inserted = c.execute(
            "INSERT OR IGNORE INTO pipeline_budget_alerts "
            "(pipeline_id,period,period_key,cap_nano,level,created_at) VALUES(?,?,?,?,80,?)",
            (row["id"], period, key, int(cap_nano), now),
        ).rowcount
        if inserted and recipient:
            _notice(c, row, recipient, "daily" if period == "daily" else "monthly")


def after_run(c: sqlite3.Connection, task_id: str | None, at: datetime | None = None) -> None:
    if not task_id:
        return
    task = H.task(c, task_id)
    row = _target(c, str(task.get("type_id") or "")) if task else None
    if row:
        for period, _, _ in PERIODS:
            _level(c, row, period, at)


def hold_reason(
    c: sqlite3.Connection, task_id: str | None, at: datetime | None = None, *, task: dict[str, Any] | None = None
) -> str | None:
    if not task_id:
        return None
    task = task if task is not None else H.task(c, task_id)
    row = _target(c, str(task.get("type_id") or "")) if task else None
    if not row:
        return None
    met: list[tuple[str, str, str]] = []
    for period, _, word in PERIODS:
        cap = row.get(period + "_cap_nano")
        if cap is not None and int(cap) > 0 and spend_nano(c, row["id"], period, at) >= int(cap):
            key = _period_key(period, at)[0]
            alert = c.execute(
                "SELECT created_at FROM pipeline_budget_alerts WHERE pipeline_id=? AND period=? AND period_key=? "
                "AND cap_nano=? AND level=100",
                (row["id"], period, key, int(cap)),
            ).fetchone()
            # Claim and health reads may call this on read-only connections. Threshold notices and decision tasks are
            # written by after_run() and settings_changed(), never as a side effect of explaining a queued job.
            met.append((str(alert[0]) if alert else "", period, word))
    if not met:
        return None
    _, period, _ = min(met)
    return f"pipeline_cap_{period}"


def settings_changed(c: sqlite3.Connection, pipeline_id: str, old: dict[str, Any]) -> None:
    row = _target(c, pipeline_id)
    if not row:
        return
    for period, _, _ in PERIODS:
        column = period + "_cap_nano"
        before, after = old.get(column), row.get(column)
        if before == after:
            continue
        bounds = _period_key(period, None)
        key = bounds[0]
        previous = (
            c.execute(
                "SELECT decision_task_id,request_task_id FROM pipeline_budget_alerts WHERE pipeline_id=? AND period=? "
                "AND period_key=? AND cap_nano=? AND level=100",
                (pipeline_id, period, key, before),
            ).fetchone()
            if before is not None
            else None
        )
        if previous:
            if previous["decision_task_id"]:
                decision = H.task(c, previous["decision_task_id"])
                if decision and decision["status"] in H.ACTIVE_STATUSES:
                    H.task_update(c, H.KEEPER, decision["id"], status="done", note="The cap changed.", strict=False)
            if previous["request_task_id"] and (after is None or int(after) > int(before or 0)):
                request = H.task(c, previous["request_task_id"])
                if request and request["status"] in H.ACTIVE_STATUSES:
                    H.task_update(c, H.KEEPER, request["id"], status="done", note="The cap was raised.", strict=False)
        _level(c, row, period)


def decision_closed(c: sqlite3.Connection, actor: str, task_id: str, status: str, note: str | None) -> None:
    found = c.execute(
        "SELECT a.pipeline_id,a.period,a.period_key,a.cap_nano,a.request_task_id,t.manager,t.name,d.owner_id "
        "FROM pipeline_budget_alerts a JOIN task_types t ON t.id=a.pipeline_id "
        "JOIN tasks d ON d.id=a.decision_task_id "
        "WHERE a.decision_task_id=? AND a.level=100",
        (task_id,),
    ).fetchone()
    if not found:
        return
    if actor != found["owner_id"]:
        return
    choice = str(note or "").strip().splitlines()[0].rstrip(".! ").casefold()
    if status != "done" or choice != "ask chris to raise" or found["request_task_id"]:
        return
    from . import question_tasks

    question_api: Any = question_tasks

    person = pipeline_rights.company_owner(c)
    if not person:
        return
    word = "daily" if found["period"] == "daily" else "monthly"
    cap = int(found["cap_nano"]) / NANO
    text = (
        f"May I raise the {word} cap for {found['name']} from ${cap:,.2f}? "
        "The pipeline remains on hold until its cap is raised."
    )
    request = question_api.ask_plain(c, actor, person, text, why="Pipeline spend cap decision")
    if request is None:
        return
    c.execute(
        "UPDATE pipeline_budget_alerts SET request_task_id=? WHERE pipeline_id=? AND period=? AND period_key=? "
        "AND cap_nano=? AND level=100",
        (request["id"], found["pipeline_id"], found["period"], found["period_key"], found["cap_nano"]),
    )
