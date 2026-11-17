# pyright: strict
"""Whether a task shows Late now: the one rule the list row, the task's own read and the Late filter share.

A Late mark (`visits.late_told_at`, `late_person_at`) is written by never_stuck while the task has one owner. A
reassignment keeps the mark on the same visit (task_engine.reassign), so every read checks it against the owner who
holds the task now, under the rule that wrote it:
  - a person's own task is Late only once its due date has passed (never_stuck._late_by_due, C6);
  - any other task is Late only while its stage has run past its expected time (never_stuck._late).
task_match.py's Late filter states the same two rules in SQL: keep them in step.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from . import hubdb_core


def _instant(value: Any) -> datetime | None:
    """A timestamp as an aware datetime (UTC when it has no offset), None when it cannot be read."""
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def due_instant(due_at: Any) -> datetime | None:
    """When a task is due. A due date with no time is Late once that day is over."""
    due = _instant(due_at)
    if due is not None and len(str(due_at)) == 10:
        due += timedelta(days=1)
    return due


def is_late(owner_id: Any, due_at: Any, entered_at: Any, expected_minutes: Any, now: datetime) -> bool:
    """Whether a task held by `owner_id`, in a visit entered at `entered_at` in a stage that expects `expected_minutes`,
    is overdue at `now`. The caller has already seen a Late mark on the visit."""
    if hubdb_core.is_human(owner_id):
        due = due_instant(due_at)
        return due is not None and now >= due
    minutes = int(expected_minutes or 0)
    entered = _instant(entered_at)
    return bool(minutes) and entered is not None and (now - entered).total_seconds() >= minutes * 60


def late_of(row: dict[str, Any], visit: dict[str, Any], expected_minutes: Any, now: datetime) -> dict[str, Any] | None:
    """The Late a task shows, `{since, level}`, or None: its open visit's mark, while `is_late` holds for its owner."""
    since = visit.get("late_told_at")
    if not since or not is_late(row.get("owner_id"), row.get("due_at"), visit.get("entered_at"), expected_minutes, now):
        return None
    return {"since": since, "level": "person" if visit.get("late_person_at") else "told"}
