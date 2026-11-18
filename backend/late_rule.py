# pyright: strict
"""Whether a task shows Late now: the one rule the list row, the task's own read and the Late filter share.

A Late mark (`visits.late_told_at`, `late_person_at`) is written by never_stuck while the task has one owner. A
reassignment keeps the mark on the same visit (task_engine.reassign), so every read checks it against the owner who
holds the task now, under the rule that wrote it:
  - a person's own task is Late only once its due date has passed (never_stuck._late_by_due, C6);
  - any other task is Late only while its stage has run past its expected time (never_stuck._late), and never while it
    is a person's turn (all it waits on is people's answers: waiting_on.persons_turn) or after a person answered
    since the mark (the bot's clock started again: never_stuck.clock_from).
task_match.py's Late filter states the same two rules in SQL: keep them in step.
"""

import json
import sqlite3
from collections.abc import Sequence
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


def last_answers(conn: sqlite3.Connection, visits: Sequence[tuple[str, str, str]]) -> dict[str, str]:
    """{visit id: when a person last answered on it} for (visit id, task id, entered at) rows, the visits with an answer
    only: a person's answer to an OK or a question (answers; a bot's own "done" is not one), or a reply that cleared a ❓ (question_marks). Two reads for any
    number of visits."""
    if not visits:
        return {}
    out: dict[str, str] = {}
    rows = json.dumps([list(v) for v in visits])
    for visit_id, at in conn.execute(
        "SELECT a.visit_id, MAX(a.created_at) FROM answers a WHERE a.by_id LIKE 'human:%' AND a.visit_id IN "
        "(SELECT json_extract(value, '$[0]') FROM json_each(?)) GROUP BY a.visit_id",
        (rows,),
    ).fetchall():
        out[str(visit_id)] = str(at)
    for visit_id, at in conn.execute(
        "SELECT json_extract(j.value, '$[0]'), MAX(q.cleared_at) FROM json_each(?) j JOIN question_marks q "
        "ON q.task_id = json_extract(j.value, '$[1]') AND q.cleared_message_id IS NOT NULL "
        "AND q.cleared_at >= json_extract(j.value, '$[2]') GROUP BY json_extract(j.value, '$[0]')",
        (rows,),
    ).fetchall():
        if at and str(at) > out.get(str(visit_id), ""):
            out[str(visit_id)] = str(at)
    return out


def late_of(
    row: dict[str, Any],
    visit: dict[str, Any],
    expected_minutes: Any,
    now: datetime,
    persons_turn: bool = False,
    answered_at: Any = None,
) -> dict[str, Any] | None:
    """The Late a task shows, `{since, level}`, or None: its open visit's mark, while `is_late` holds for its owner. A
    bot's task shows none while it is a person's turn (`persons_turn`), or when a person answered after the mark
    (`answered_at`): never_stuck marks it again if the bot then sits a whole expected time."""
    since = visit.get("late_told_at")
    if not since or not is_late(row.get("owner_id"), row.get("due_at"), visit.get("entered_at"), expected_minutes, now):
        return None
    if not hubdb_core.is_human(row.get("owner_id")) and (
        persons_turn or (answered_at and str(answered_at) > str(since))
    ):
        return None
    return {"since": since, "level": "person" if visit.get("late_person_at") else "told"}
