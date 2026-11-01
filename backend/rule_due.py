"""Due dates on rules (docs/rules-plan.md §1, §4): a date, a repeat, an owner and a status for each rule that has one.

- A due date is its own row (`rule_due_dates`), so one rule can have several (quarterly returns and an annual report).
  Its occurrences are worked out from the first date and the repeat; nothing stores them. `rule_due_status` holds each
  occurrence someone marked Satisfied, keyed by its date: by hand, or by its routine's task once a routine links one.
- Status is derived on read. Satisfied when the current occurrence has a status row (a Satisfied "once" stays so).
  Overdue after the end of that day in the due date's zone. Due soon within the linked routine's lead time (its
  trigger's `open_days`), or WINDOW_DAYS with no routine. Otherwise Upcoming.
- Repeats count from the first date, not from the occurrence before, and clamp to the month's end: monthly from the 31st
  falls on the 30th in April, and yearly from 29 February falls on the 28th in other years.
- An edit to the first date or the repeat ends the row (`ended_on`) and starts a new one, which points back at it
  (`replaces`), so the history stays true and reads as one chain. Removing a due date sets `retired_at`. Rows are never
  deleted.

Unversioned and idempotent (`ensure`), like backend/strategy.py.
"""
import calendar
import json
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import audit_log
from . import hubdb as H
from .store import Problem

REPEATS = ("once", "weekly", "monthly", "quarterly", "yearly")
STEP_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}
STATUSES = ("overdue", "due_soon", "upcoming", "satisfied")
WINDOW_DAYS = 30            # "due soon" without a routine window
ROUTINE_DAYS = 14           # a routine's lead time when its trigger names none (automation_due.OPEN_DAYS)
MAX_OCCURRENCES = 5000      # a backstop: a weekly date from decades ago still walks in a few hundred steps
HISTORY = 5                 # the last status rows a list shows for a due date
OPEN_DAYS = 20              # the open occurrences a detail names (the count is always exact)
DETAIL_MARKS = 100          # the satisfied occurrences a detail shows, per row of its chain
CHAIN_MAX = 50              # the rows of one due date's chain (its edits) a detail walks

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS rule_due_dates(id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, first_due TEXT NOT NULL, "
    "repeats TEXT NOT NULL CHECK (repeats IN ('once','weekly','monthly','quarterly','yearly')), "
    "timezone TEXT NOT NULL, owner TEXT NOT NULL, routine_id TEXT, replaces TEXT, "
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


def state(row, satisfied, today, window=WINDOW_DAYS):
    """Where one due date stands on `today` (a day in its zone). `satisfied` is the set of due days already marked, and
    `window` the days before a due day it is Due soon. Returns {due_on, status, open}: `open` counts the unsatisfied
    occurrences inside the window (overdue or due soon)."""
    horizon = today + timedelta(days=window)
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
    elif latest is not None and row["repeats"] != "once" and beyond is not None:
        # A repeating date whose occurrences in the window are all marked has moved on: its next one is what is due.
        current, status = beyond, "upcoming"
    elif latest is not None:
        current, status = latest, "satisfied"
    else:
        current, status = beyond or parse_date(row["first_due"]), "upcoming"
    return {"due_on": current.isoformat(), "status": status, "open": len(opened),
            "open_days": [day.isoformat() for day in opened[:OPEN_DAYS]]}


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


def task_days(c, rows):
    """{routine id: its lead time in days} for the rows' linked routines still in place (not deleted)."""
    ids = sorted({r["routine_id"] for r in rows if r.get("routine_id")})
    out = {}
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        for r in c.execute("SELECT id, flow FROM automations WHERE deleted_at IS NULL AND id IN (%s)"
                           % ",".join("?" * len(part)), part):
            trigger = json.loads(r["flow"]).get("trigger") or {}
            if trigger.get("type") == "due":
                out[r["id"]] = int(trigger.get("open_days", ROUTINE_DAYS))
    return out


def window(row, days):
    """The days before a due day that it is Due soon: its routine's lead time, else WINDOW_DAYS."""
    return days.get(row.get("routine_id") or "", WINDOW_DAYS)


def views(c, rows, today=None):
    """Each due date's view, with its status today: {id, rule_id, first_due, repeats, timezone, owner, routine_id,
    task_days, ended_on, retired_at, due_on, status, open, history}. `rows` are rule_due_dates rows. `task_days` is the
    linked routine's lead time (None with no routine). A list reads this; a detail reads `detail`."""
    statuses = _status_rows(c, [r["id"] for r in rows])
    days = task_days(c, rows)
    out = []
    for row in rows:
        marks = statuses.get(row["id"], [])
        satisfied = {m["due_on"] for m in marks}
        now = today or local_today(row["timezone"])
        current = state(row, satisfied, now, window(row, days))
        out.append({
            "id": row["id"], "rule_id": row["rule_id"], "first_due": row["first_due"], "repeats": row["repeats"],
            "timezone": row["timezone"], "owner": row["owner"], "routine_id": row["routine_id"],
            "task_days": days.get(row["routine_id"] or ""),
            "ended_on": row["ended_on"], "retired_at": row["retired_at"],
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


def detail(c, row, today=None):
    """One due date in full, for the side panel: its view with the open occurrences named, and its chain, oldest first.
    The chain runs back through the rows it replaced (ended by an edit) and on to the one that replaced it, ended or
    removed rows included, so the marks an edit left behind stay in reach by id."""
    chain = [dict(row)]
    seen = {row["id"]}
    while chain[0]["replaces"] and chain[0]["replaces"] not in seen and len(chain) < CHAIN_MAX:
        earlier = one(c, chain[0]["replaces"])
        if not earlier:
            break
        seen.add(earlier["id"])
        chain.insert(0, dict(earlier))
    while len(chain) < CHAIN_MAX:
        later = c.execute("SELECT * FROM rule_due_dates WHERE replaces=?", (chain[-1]["id"],)).fetchone()
        if not later or later["id"] in seen:
            break
        seen.add(later["id"])
        chain.append(dict(later))
    current = views(c, [row], today)[0]
    current["open_days"] = state(row, satisfied_days(c, row["id"]), today or local_today(row["timezone"]),
                                 window(row, task_days(c, [row])))["open_days"]
    rows = []
    for link in chain:
        marks = [dict(m) for m in c.execute("SELECT * FROM rule_due_status WHERE due_date_id=? ORDER BY due_on DESC "
                                            "LIMIT ?", (link["id"], DETAIL_MARKS))]
        rows.append({"id": link["id"], "first_due": link["first_due"], "repeats": link["repeats"],
                     "timezone": link["timezone"], "owner": link["owner"], "routine_id": link["routine_id"],
                     "replaces": link["replaces"], "replaced_by": _replaced_by(c, link["id"]),
                     "ended_on": link["ended_on"], "retired_at": link["retired_at"],
                     "satisfied": [{"due_on": m["due_on"], "satisfied_by": m["satisfied_by"], "via": m["via"],
                                    "at": m["at"], "task_id": m["task_id"]} for m in marks]})
    return {**current, "replaces": row["replaces"], "replaced_by": _replaced_by(c, row["id"]), "chain": rows}


def _replaced_by(c, due_id):
    found = c.execute("SELECT id FROM rule_due_dates WHERE replaces=?", (due_id,)).fetchone()
    return found["id"] if found else None


def satisfied_days(c, due_id):
    return {r["due_on"] for r in c.execute("SELECT due_on FROM rule_due_status WHERE due_date_id=?", (due_id,))}


# ----------------------------------------------------------------------------- writes
def add(c, actor, rule_id, first_due, repeats, zone, owner, routine_id=None, replaces=None):
    row_id = H.new_id()
    c.execute("INSERT INTO rule_due_dates (id, rule_id, first_due, repeats, timezone, owner, routine_id, replaces, "
              "ended_on, retired_at, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?)",
              (row_id, rule_id, first_due, repeats, zone, owner, routine_id, replaces, actor, H.now()))
    return row_id


def restart(c, actor, row, changes):
    """An edit to the first date or the repeat: the row ends today in its zone and a new one takes over, so the marks it
    already has stay with it. Returns the new row's id."""
    ended = local_today(row["timezone"]).isoformat()
    c.execute("UPDATE rule_due_dates SET ended_on=? WHERE id=?", (ended, row["id"]))
    fresh = {**row, **changes}
    return add(c, actor, row["rule_id"], fresh["first_due"], fresh["repeats"], fresh["timezone"], fresh["owner"],
               fresh["routine_id"], replaces=row["id"])


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
    current = state(row, satisfied, now, window(row, task_days(c, [row])))
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
    try:
        c.execute("INSERT INTO rule_due_status (due_date_id, due_on, satisfied_by, via, task_id, at) VALUES (?,?,?,?,?,?)",
                  (row["id"], target.isoformat(), actor, via, task_id, H.now()))
    except sqlite3.IntegrityError:
        # Two marks of the same day raced: the second is the same answer as marking a satisfied day.
        raise Problem("conflict", f"{target.isoformat()} is already satisfied", 409) from None
    return target.isoformat()


def marked(c, due_id, due_on):
    """Whether that occurrence of the due date is already marked Satisfied (its routine then skips the day)."""
    return bool(c.execute("SELECT 1 FROM rule_due_status WHERE due_date_id=? AND due_on=?", (due_id, due_on)).fetchone())


def on_task_status(c, actor, task, old, new):
    """Every task status write (hubdb `_set_status`, in its transaction) of a routine's occurrence task: a move into done
    or closed that completes it (`_completes`) marks its due date's occurrence Satisfied, whatever the status before
    (the owner's done after the keeper's close counts); a move back into play takes the mark away. A decline of a
    finished task leaves the mark, until the task comes back into play."""
    if not task.get("occurrence") or not H._has_table(c, "rule_due_dates") or new == old:
        return
    if new in ("done", "closed"):
        on_due_task_completed(c, {**task, "status": new}, actor)
    elif new in H.ACTIVE_STATUSES and old not in H.ACTIVE_STATUSES:     # back in play (from done, closed or declined)
        on_due_task_reopened(c, task, actor)


def _completes(task, actor):
    """Completed (docs/rules-plan.md §1): marked done by its owner, or closed by a person. A mover's or manager's done,
    the keeper's close (ask expiry, cleanup) and a decline (a cancel) never are."""
    if not actor or actor == H.KEEPER:
        return False
    if task.get("status") == "done":
        return actor == task.get("owner")
    return task.get("status") == "closed" and H.is_human(actor)


def links(c, due_id, routine_id):
    """Whether that automation is the routine the due date row links now (`routine_id`, set only by
    backend/rule_due_routine.py): the one automation that may carry the row's id in its trigger, and stamp it on a task."""
    row = one(c, due_id) if due_id and routine_id else None
    return bool(row) and row["routine_id"] == routine_id


def linked(c, row, routine_id):
    """Whether that automation is, or was, the due date row's routine: its `routine_id` now, or one the rule's audit trail
    recorded for this row (every add and edit of a due date writes its view, routine included). A routine turned off or
    deleted after it opened a task still stands for that task; an automation that only names the row never does."""
    if not routine_id:
        return False
    if row["routine_id"] == routine_id:
        return True
    for found in c.execute("SELECT before, after FROM audit_log WHERE object=?", ("org_rule:" + row["rule_id"],)):
        for raw in found:
            due = (json.loads(raw) or {}).get("due") if raw else None
            if isinstance(due, dict) and due.get("id") == row["id"] and due.get("routine_id") == routine_id:
                return True
    return False


def _occurrence_row(c, task):
    """The due date and the day a routine's task stands for: the routine and the day from its occurrence marker
    (`<automation>:<due_on>`, the server's), the row from `tasks.rule_due_id`, which the run that opened the task stamped
    only while that automation was the row's routine (automation_actions `_occurrence`), scheduled or Run now. The
    routine must be (or have been) the row's (`linked`). Later edits to the routine or the due date never change it, so
    a task opened before an edit marks the ended row and a task outliving its routine still marks its day. (None, None)
    when it is not a rule's routine or that due date was removed."""
    routine, _, due_on = str(task.get("occurrence") or "").rpartition(":")
    try:
        day = date.fromisoformat(due_on)
    except ValueError:
        return None, None
    row = one(c, task.get("rule_due_id")) if task.get("rule_due_id") else None
    if not row or row["retired_at"] or not is_occurrence(parse_date(row["first_due"]), row["repeats"], day):
        return None, None
    if not linked(c, row, routine):
        return None, None
    return row, due_on


def on_due_task_completed(c, task, closed_by=None):
    """A routine's task completed (`_completes`): its occurrence of the linked due date is marked Satisfied by whoever
    completed it, via routine, with the task's id and an audit row on the rule. Returns the due date ids marked."""
    if not _completes(task, closed_by):
        return []
    row, due_on = _occurrence_row(c, task)
    if not row or due_on in satisfied_days(c, row["id"]):
        return []
    try:
        c.execute("INSERT INTO rule_due_status (due_date_id, due_on, satisfied_by, via, task_id, at) "
                  "VALUES (?,?,?,?,?,?)", (row["id"], due_on, closed_by, "routine", task.get("id"), H.now()))
    except sqlite3.IntegrityError:
        return []
    audit_log.write(c, closed_by, "org_rule:" + row["rule_id"], None,
                    {"due": row["id"], "satisfied": due_on, "task": task.get("id")}, "routine")
    return [row["id"]]


def on_due_task_reopened(c, task, reopened_by=None):
    """A routine's task reopened: the occurrence it marked is no longer satisfied. Removes the routine's mark for this
    task, and writes an audit row on each rule. Returns the due date ids unmarked."""
    actor = reopened_by or task.get("owner") or H.KEEPER
    unmarked = []
    for mark in _rows(c, "SELECT * FROM rule_due_status WHERE task_id=? AND via='routine'", (task.get("id"),)):
        due = one(c, mark["due_date_id"])
        c.execute("DELETE FROM rule_due_status WHERE due_date_id=? AND due_on=?", (mark["due_date_id"], mark["due_on"]))
        if due:
            audit_log.write(c, actor, "org_rule:" + due["rule_id"],
                            {"due": due["id"], "satisfied": mark["due_on"], "by": mark["satisfied_by"]}, None, "routine")
        unmarked.append(mark["due_date_id"])
    return unmarked
