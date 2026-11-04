"""A question a bot put to a person stays on the person's Needs you until the person answers it.

Nothing sends a question back to its bot any more. Two business days after a question to a person (Mon-Fri, where the team
keeps its calendar) it is still an ask like any other: it stays on the person's Needs you, and no bot is woken about it.
`crossed_at` gives the day an ask counts as waiting; `sweep` stays as the scheduler's hourly entry point and does nothing.
"""

from datetime import datetime, time, timedelta, timezone

from . import hubdb as H
from .updates import ZONE

BUSINESS_DAYS = 2           # the business days an ask waits before it counts as waiting (the Task Manager's report uses 1)


def _local(ts):
    at = H.parse_ts(ts)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(ZONE)


def crossed_at(asked_ts, days=BUSINESS_DAYS):
    """When an ask written at `asked_ts` has waited `days` business days. One asked on a weekend starts counting on Monday
    morning. Friday 10:00 -> Tuesday 10:00."""
    at = _local(asked_ts)
    if at.weekday() >= 5:
        at = datetime.combine(at.date() + timedelta(days=7 - at.weekday()), time(0), ZONE)
    left = days
    while left:
        at += timedelta(days=1)
        if at.weekday() < 5:
            left -= 1
    return at


def sweep(conn, at=None):
    """Nothing: no question is taken back and no bot is woken. Returns {} (the same shape the old batch returned)."""
    return {}
