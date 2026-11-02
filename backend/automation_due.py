"""Due dates for an automation's "Before a due date" trigger (backend/automations.py): the days something falls due,
and when the run for each one starts.

Pure: no database and no clock. Occurrences are anchored on `first_due`, so a monthly one from Jan 31 falls on Feb 28
(or 29), then Mar 31 again, not Mar 28. An occurrence's run starts at 00:00 in the trigger's zone, `open_days` before
its due day. There is no RRULE: a repeat is once, weekly, monthly, quarterly or yearly.
"""
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

REPEATS = ("once", "weekly", "monthly", "quarterly", "yearly")
MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}   # the months between two occurrences of each repeat
OPEN_DAYS = 14          # how many days before the due day the run starts, unless the trigger says otherwise
MAX_OPEN_DAYS = 366
MAX_CATCH_UP = 50       # runs one pass of the scheduler starts for one routine, after a long outage


def occurrence(first, repeats, n):
    """The n-th due day (0 is `first`), or None: a `once` routine has only the one."""
    if repeats == "once":
        return first if n == 0 else None
    if repeats == "weekly":
        return first + timedelta(weeks=n)
    year, month = divmod(first.month - 1 + MONTHS[repeats] * n, 12)
    year, month = first.year + year, month + 1
    return date(year, month, min(first.day, monthrange(year, month)[1]))


def first_on_or_after(first, repeats, day):
    """The earliest due day that is `day` or later, or None when a `once` routine's date has passed."""
    if repeats == "once":
        return first if first >= day else None
    if repeats == "weekly":
        n = max(0, -(-(day - first).days // 7))
    else:
        n = max(0, ((day.year - first.year) * 12 + day.month - first.month) // MONTHS[repeats] - 1)
    while (due := occurrence(first, repeats, n)) < day:
        n += 1
    return due


def next_after(first, repeats, due):
    """The due day after `due`, or None."""
    return first_on_or_after(first, repeats, due + timedelta(days=1))


def fire_at(due, open_days, zone):
    """When the run for the due day `due` starts: 00:00 in `zone`, `open_days` before, as an aware UTC time."""
    start = due - timedelta(days=open_days)
    return datetime.combine(start, time.min, tzinfo=ZoneInfo(zone)).astimezone(timezone.utc)


def due_end(due: date, zone: str) -> str:
    """The last second of the due day in `zone`, as an offset timestamp: the due a task of that day carries."""
    return datetime.combine(due, time(23, 59, 59), tzinfo=ZoneInfo(zone)).isoformat()


def zone_of(trigger):
    """The trigger's zone, or the company's (updates.ZONE) for one saved without it."""
    if trigger.get("tz"):
        return str(trigger["tz"])
    from .updates import ZONE
    return str(ZONE.key)


def first_due(trigger):
    return date.fromisoformat(str(trigger["first_due"]))


def open_days_of(trigger):
    return int(trigger.get("open_days", OPEN_DAYS))


def start(trigger, at):
    """The cursor a new or re-timed routine starts from: its first due day from today on, in its zone. A day already
    past is not backfilled; today's, whose run may have started already, still runs at once. None when none is left."""
    today = at.astimezone(ZoneInfo(zone_of(trigger))).date()
    return first_on_or_after(first_due(trigger), trigger["repeats"], today)
