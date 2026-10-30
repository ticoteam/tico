"""Due dates on rules (docs/rules-plan.md §1, §4): a date, a repeat, an owner and a status for each rule that has one.

- A due date is its own row (`rule_due_dates`), so one rule can have several (quarterly returns and an annual report).
  Its occurrences are worked out from the first date and the repeat; nothing stores them. `rule_due_status` holds each
  occurrence someone marked Satisfied, keyed by its date: by hand, or by its routine's task once a routine links one.
- Status is derived on read. Satisfied when the current occurrence has a status row (a Satisfied "once" stays so).
  Overdue after the end of that day in the due date's zone. Due soon within WINDOW_DAYS (there is no routine window
  yet). Otherwise Upcoming.
- Repeats count from the first date, not from the occurrence before, and clamp to the month's end: monthly from the 31st
  falls on the 30th in April, and yearly from 29 February falls on the 28th in other years.
- An edit to the first date or the repeat ends the row (`ended_on`) and starts a new one, so the history stays true.
  Removing a due date sets `retired_at`. Rows are never deleted.

Unversioned and idempotent (`ensure`), like backend/strategy.py.
"""
import calendar
import json
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import hubdb as H
from .store import Problem

REPEATS = ("once", "weekly", "monthly", "quarterly", "yearly")
STEP_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}
STATUSES = ("overdue", "due_soon", "upcoming", "satisfied")
WINDOW_DAYS = 30            # "due soon" without a routine window
TASK_DAYS = 14              # the routine's lead time when it opens a task before the date (§4)
TASK_DAYS_MAX = 365
MAX_OCCURRENCES = 5000      # a backstop: a weekly date from decades ago still walks in a few hundred steps
HISTORY = 5                 # the last status rows a due date shows

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS rule_due_dates(id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, first_due TEXT NOT NULL, "
    "repeats TEXT NOT NULL CHECK (repeats IN ('once','weekly','monthly','quarterly','yearly')), "
    "timezone TEXT NOT NULL, owner TEXT NOT NULL, routine_id TEXT, task_days INTEGER NOT NULL DEFAULT 14, "
    "ended_on TEXT, retired_at TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS rule_due_dates_rule ON rule_due_dates(rule_id)",
    "CREATE INDEX IF NOT EXISTS rule_due_dates_routine ON rule_due_dates(routine_id) WHERE routine_id IS NOT NULL",
    "CREATE TABLE IF NOT EXISTS rule_due_status(due_date_id TEXT NOT NULL, due_on TEXT NOT NULL, "
    "satisfied_by TEXT NOT NULL, via TEXT NOT NULL CHECK (via IN ('routine','person')), task_id TEXT, at TEXT NOT NULL, "
    "PRIMARY KEY(due_date_id, due_on))",
)


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)


def default_zone():
    from .updates import ZONE
    return ZONE.key


# ----------------------------------------------------------------------------- dates
def parse_date(value, what="the date"):
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        raise Problem("validation", f"{what} is a day as YYYY-MM-DD", 422) from None


def parse_zone(value):
    text = str(value or "").strip() or default_zone()
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError):
        raise Problem("validation", f"{value} is not a time zone (an IANA name such as America/Chicago)", 422) from None
    return text


def parse_repeats(value):
    if value not in REPEATS:
        raise Problem("validation", "Repeats is " + ", ".join(REPEATS), 422)
    return value


def parse_task_days(value):
    if value is None:
        return TASK_DAYS
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= TASK_DAYS_MAX:
        raise Problem("validation", f"The routine opens its task this many days before: 0 to {TASK_DAYS_MAX}", 422)
    return value


def local_today(zone):
    return datetime.now(ZoneInfo(zone)).date()


def occurrence(first, repeats, n):
    """The nth occurrence (from 0) counted from the first date, or None past the last one."""
    if repeats == "once":
        return first if n == 0 else None
    if repeats == "weekly":
        return first + timedelta(weeks=n)
    months = first.month - 1 + STEP_MONTHS[repeats] * n
    year, month = first.year + months // 12, months % 12 + 1
    return date(year, month, min(first.day, calendar.monthrange(year, month)[1]))


def occurrences(first, repeats, until):
    """Ascending occurrences up to `until`, plus the first one after it (that one is the next to fall due)."""
    out = []
    for n in range(MAX_OCCURRENCES):
        day = occurrence(first, repeats, n)
        if day is None:
            break
        out.append(day)
        if day > until:
            break
    return out


def is_occurrence(first, repeats, day):
    return any(d == day for d in occurrences(first, repeats, day))


def state(row, satisfied, today):
    """Where one due date stands on `today` (a day in its zone). `satisfied` is the set of due days already marked.
    Returns {due_on, status, open}: `open` counts the unsatisfied occurrences inside the window (overdue or due soon)."""
    horizon = today + timedelta(days=WINDOW_DAYS)
    opened, latest, beyond = [], None, None
    for day in occurrences(parse_date(row["first_due"]), row["repeats"], horizon):
        if day > horizon:
            beyond = day
            break
        if day.isoformat() in satisfied:
            latest = day
        else:
            opened.append(day)
    if opened:
        current = opened[0]
        status = "overdue" if current < today else "due_soon"
    elif latest is not None:
        current, status = latest, "satisfied"
    else:
        current, status = beyond or parse_date(row["first_due"]), "upcoming"
    return {"due_on": current.isoformat(), "status": status, "open": len(opened)}


# ----------------------------------------------------------------------------- reads
def _rows(c, sql, args=()):
    return [dict(r) for r in c.execute(sql, args)]


def live_rows(c, rule_ids=None):
    """The due dates in force (not ended, not removed), for these rules or for all of them."""
    if rule_ids is None:
        return _rows(c, "SELECT * FROM rule_due_dates WHERE retired_at IS NULL AND ended_on IS NULL ORDER BY created_at")
    ids = list(rule_ids)
    out = []
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        out += _rows(c, "SELECT * FROM rule_due_dates WHERE retired_at IS NULL AND ended_on IS NULL AND rule_id IN (%s) "
                        "ORDER BY created_at" % ",".join("?" * len(part)), part)
    return out


def _status_rows(c, due_ids):
    found = {due_id: [] for due_id in due_ids}
    ids = list(due_ids)
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        for r in c.execute("SELECT * FROM rule_due_status WHERE due_date_id IN (%s) ORDER BY at DESC"
                           % ",".join("?" * len(part)), part):
            found[r["due_date_id"]].append(dict(r))
    return found


def views(c, rows, today=None):
    """Each due date's view, with its status today: {id, rule_id, first_due, repeats, timezone, owner, routine_id,
    task_days, ended_on, retired_at, due_on, status, open, history}. `rows` are rule_due_dates rows."""
    statuses = _status_rows(c, [r["id"] for r in rows])
    out = []
    for row in rows:
        marks = statuses.get(row["id"], [])
        satisfied = {m["due_on"] for m in marks}
        now = today or local_today(row["timezone"])
        current = state(row, satisfied, now)
        out.append({
            "id": row["id"], "rule_id": row["rule_id"], "first_due": row["first_due"], "repeats": row["repeats"],
            "timezone": row["timezone"], "owner": row["owner"], "routine_id": row["routine_id"],
            "task_days": row["task_days"], "ended_on": row["ended_on"], "retired_at": row["retired_at"],
            "due_on": current["due_on"], "status": current["status"], "open": current["open"],
            "history": [{"due_on": m["due_on"], "satisfied_by": m["satisfied_by"], "via": m["via"], "at": m["at"],
                         "task_id": m["task_id"]} for m in marks[:HISTORY]],
        })
    return out


def due_rows(c, rule_ids=None):
    """Views of the due dates in force, grouped by rule id."""
    grouped = {}
    for found in views(c, live_rows(c, rule_ids)):
        grouped.setdefault(found["rule_id"], []).append(found)
    return grouped


def one(c, due_id):
    row = c.execute("SELECT * FROM rule_due_dates WHERE id=?", (due_id,)).fetchone()
    return dict(row) if row else None


def satisfied_days(c, due_id):
    return {r["due_on"] for r in c.execute("SELECT due_on FROM rule_due_status WHERE due_date_id=?", (due_id,))}


# ----------------------------------------------------------------------------- writes
def add(c, actor, rule_id, first_due, repeats, zone, owner, task_days=None, routine_id=None):
    row_id = H.new_id()
    c.execute("INSERT INTO rule_due_dates (id, rule_id, first_due, repeats, timezone, owner, routine_id, task_days, "
              "ended_on, retired_at, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?)",
              (row_id, rule_id, first_due, repeats, zone, owner, routine_id, parse_task_days(task_days), actor, H.now()))
    return row_id


def restart(c, actor, row, changes):
    """An edit to the first date or the repeat: the row ends today in its zone and a new one takes over, so the marks it
    already has stay with it. Returns the new row's id."""
    ended = local_today(row["timezone"]).isoformat()
    c.execute("UPDATE rule_due_dates SET ended_on=? WHERE id=?", (ended, row["id"]))
    fresh = {**row, **changes}
    return add(c, actor, row["rule_id"], fresh["first_due"], fresh["repeats"], fresh["timezone"], fresh["owner"],
               fresh["task_days"], fresh["routine_id"])


def update_in_place(c, row_id, changes):
    if not changes:
        return
    sets = ", ".join(f"{k}=?" for k in changes)
    c.execute(f"UPDATE rule_due_dates SET {sets} WHERE id=?", (*changes.values(), row_id))


def remove(c, row_id):
    c.execute("UPDATE rule_due_dates SET retired_at=? WHERE id=?", (H.now(), row_id))


def satisfy(c, actor, row, due_on=None, via="person", task_id=None):
    """Mark one occurrence Satisfied. With more than one open occurrence, `due_on` says which; otherwise the current one.
    Returns the day marked."""
    satisfied = satisfied_days(c, row["id"])
    now = local_today(row["timezone"])
    current = state(row, satisfied, now)
    if due_on is None:
        if current["open"] > 1:
            raise Problem("validation", f"{current['open']} occurrences are open: say which one with due_on", 422)
        if current["status"] == "satisfied":
            raise Problem("conflict", f"{current['due_on']} is already satisfied", 409)
        target = parse_date(current["due_on"])
    else:
        target = parse_date(due_on, "due_on")
        if not is_occurrence(parse_date(row["first_due"]), row["repeats"], target):
            raise Problem("validation", f"{target.isoformat()} is not an occurrence of this due date", 422)
        if target.isoformat() in satisfied:
            raise Problem("conflict", f"{target.isoformat()} is already satisfied", 409)
    c.execute("INSERT INTO rule_due_status (due_date_id, due_on, satisfied_by, via, task_id, at) VALUES (?,?,?,?,?,?)",
              (row["id"], target.isoformat(), actor, via, task_id, H.now()))
    return target.isoformat()


def on_due_task_completed(c, task, closed_by=None):
    """The seam P0's routine link calls when a routine's task closes (nothing calls it yet). A routine's task for a due
    date carries refs {routine_id, due_on}; completing it marks that occurrence Satisfied, via routine. A close by the
    keeper (ask expiry, cleanup), a cancel, or a task that is not done writes nothing; reopening the task does not undo
    the mark here (the caller removes it). Returns the due date ids marked."""
    if task.get("status") != "done" or closed_by == H.KEEPER:
        return []
    refs = task.get("refs") or {}
    if isinstance(refs, str):
        try:
            refs = json.loads(refs)
        except ValueError:
            return []
    routine_id, due_on = refs.get("routine_id"), refs.get("due_on")
    if not routine_id or not due_on:
        return []
    marked = []
    for row in live_rows(c):
        if row["routine_id"] != routine_id or due_on in satisfied_days(c, row["id"]):
            continue
        if not is_occurrence(parse_date(row["first_due"]), row["repeats"], parse_date(due_on)):
            continue
        try:
            c.execute("INSERT INTO rule_due_status (due_date_id, due_on, satisfied_by, via, task_id, at) "
                      "VALUES (?,?,?,?,?,?)", (row["id"], due_on, H.KEEPER, "routine", task.get("id"), H.now()))
        except sqlite3.IntegrityError:
            continue
        marked.append(row["id"])
    return marked
