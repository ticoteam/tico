"""Due dates on records (plan §1a, §7.2): a day a location, a piece of equipment or a vendor or customer must be worked, the
teammate who works it, and one occurrence per day it falls due, each completed on its own.

- Calendar repeats (once, weekly, monthly, quarterly, yearly) are counted from `starts_on` by backend/rule_due.py, so a
  month-end date clamps the way a rule's does. Nothing stores them; an occurrence with no row is open. Only the window
  from `window` counts: the latest occurrence on or before `counted_from_on` (the day the date was made, or its dates
  last changed), and every occurrence after it. An old date opens one occurrence, and nothing lapses after that.
- Completion-anchored repeats (every_days, every_months) keep one `next_due_on` that moves when the occurrence is
  completed, to the completion day plus the count. A late completion shifts the chain: a 90-day service done 20 days
  late is next due 90 days after the day it was done.
- The pure rules come first; the SQL for the two tables comes after. Nothing here reads the clock: callers pass the day.
"""
import sqlite3
import uuid
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .. import rule_due as _rule_due
from . import store as RS

D: Any = _rule_due

CALENDAR = ("once", "weekly", "monthly", "quarterly", "yearly")
COMPLETION = ("every_days", "every_months")
REPEATS = (*CALENDAR, *COMPLETION)
SUBJECT_TYPES = ("location", "equipment", "contact")
MAX_LEAD = 366          # days a due date opens its task before it falls due
MAX_EVERY = 3660        # the count of a completion-anchored repeat: ten years of days, or of months
HISTORY = 50            # the occurrences a due date's detail lists, newest first


def local_day(moment: datetime, zone: str) -> date:
    """The calendar day `moment` falls on in `zone`: the day a completion or a pass is counted on."""
    return moment.astimezone(ZoneInfo(zone)).date()


def step_after(day: date, repeats: str, count: int) -> date:
    """The day a completion-anchored chain falls due next: `count` days or months after the day it was completed. A
    monthly step clamps to the month's end, as a rule's does."""
    if repeats == "every_days":
        return day + timedelta(days=count)
    return D.occurrence(day, "monthly", count)


def chain_next(completed: date, due_on: date, repeats: str, count: int) -> date:
    """The day a completion-anchored chain falls due next, after its occurrence `due_on` was completed on `completed`: the
    count of days or months after the completion. An early completion can land that on or before the occurrence itself,
    so then the count is taken from the occurrence, and the chain always moves on past the day it was made for."""
    nxt = step_after(completed, repeats, count)
    return nxt if nxt > due_on else step_after(due_on, repeats, count)


def _index_near(first: date, repeats: str, day: date) -> int:
    """The index (from 0) of the calendar occurrence at or just before `day`, found by arithmetic: a weekly date steps seven
    days, a monthly, quarterly or yearly one steps months. A month's end clamps, which can move the true index down by one,
    so the walk in `_from` starts a couple of steps earlier."""
    if repeats == "once" or day <= first:
        return 0
    if repeats == "weekly":
        return (day - first).days // 7
    months = (day.year - first.year) * 12 + (day.month - first.month)
    return months // D.STEP_MONTHS[repeats]


def _from(first: date, repeats: str, day: date) -> Iterator[date]:
    """The calendar occurrences from a couple of steps before `day` on, oldest first, with no end: the caller stops. The walk
    starts at an index found by arithmetic, so a date from a century ago reaches this year as fast as one from last week."""
    n = max(0, _index_near(first, repeats, day) - 2)
    while True:
        occ: date | None = D.occurrence(first, repeats, n)
        if occ is None:
            return
        yield occ
        n += 1


def _on_grid(first: date, repeats: str, day: date) -> bool:
    """Whether `day` is one of the calendar occurrences from `first`, the clamped month-end ones included."""
    if day < first:
        return False
    near = _index_near(first, repeats, day)
    return any(D.occurrence(first, repeats, n) == day for n in range(max(0, near - 2), near + 3))


def window(row: Mapping[str, object]) -> date:
    """The first calendar occurrence that counts: the latest one on or before `counted_from_on`. A date that starts after
    that day counts from its start. Only calendar repeats have one."""
    first = date.fromisoformat(str(row["starts_on"]))
    counted = date.fromisoformat(str(row["counted_from_on"]))
    if counted <= first:
        return first
    latest = first
    for day in _from(first, str(row["repeats"]), counted):
        if day > counted:
            break
        latest = day
    return latest


def open_days(row: Mapping[str, object], through: date, completed: set[str]) -> list[date]:
    """The occurrences of one due date that are open and fall due by `through`, oldest first. A completion-anchored
    chain has at most one open occurrence, on `next_due_on`; a calendar one has every counted occurrence not yet
    completed."""
    repeats = str(row["repeats"])
    if repeats in COMPLETION:
        due = date.fromisoformat(str(row["next_due_on"]))
        return [due] if due <= through and due.isoformat() not in completed else []
    first = date.fromisoformat(str(row["starts_on"]))
    start = window(row)
    out: list[date] = []
    for day in _from(first, repeats, start):
        if day > through:
            break
        if day >= start and day.isoformat() not in completed:
            out.append(day)
    return out


def next_open(row: Mapping[str, object], completed: set[str]) -> date | None:
    """The earliest open occurrence however far off, or None when a once date is complete."""
    repeats = str(row["repeats"])
    if repeats in COMPLETION:
        due = date.fromisoformat(str(row["next_due_on"]))
        return None if due.isoformat() in completed else due
    first = date.fromisoformat(str(row["starts_on"]))
    start = window(row)
    for day in _from(first, repeats, start):
        if start <= day and day.isoformat() not in completed:
            return day
    return None


def is_occurrence(row: Mapping[str, object], day: date) -> bool:
    """Whether `day` is one of the due date's counted occurrences: the chain's `next_due_on`, or a calendar day from the
    window on."""
    if str(row["repeats"]) in COMPLETION:
        return day.isoformat() == str(row["next_due_on"])
    first = date.fromisoformat(str(row["starts_on"]))
    return window(row) <= day and _on_grid(first, str(row["repeats"]), day)


def status(row: Mapping[str, object], next_on: date | None, today: date) -> str:
    """What a list shows: done (a once date that is complete), overdue, due (within its lead time), or upcoming."""
    if row["archived_at"]:
        return "archived"
    if next_on is None:
        return "done"
    if next_on < today:
        return "overdue"
    lead = row["lead_days"]
    if not isinstance(lead, int):
        raise TypeError("a due date's lead_days is a whole number of days")
    return "due" if next_on <= today + timedelta(days=lead) else "upcoming"


# ---------------------------------------------------------------------------------------------------------- the rows
def new_id() -> str:
    return str(uuid.uuid4())


def get(c: sqlite3.Connection, due_date_id: str) -> Any:
    return c.execute("SELECT * FROM due_dates WHERE id=?", (due_date_id,)).fetchone()


def occurrence(c: sqlite3.Connection, due_date_id: str, due_on: str) -> Any:
    return c.execute("SELECT * FROM due_date_occurrences WHERE due_date_id=? AND due_on=?",
                     (due_date_id, due_on)).fetchone()


def completed_days(c: sqlite3.Connection, due_date_id: str) -> set[str]:
    return {str(r[0]) for r in c.execute(
        "SELECT due_on FROM due_date_occurrences WHERE due_date_id=? AND completed_at IS NOT NULL", (due_date_id,))}


def latest_completed(c: sqlite3.Connection, due_date_id: str) -> Any:
    return c.execute("SELECT * FROM due_date_occurrences WHERE due_date_id=? AND completed_at IS NOT NULL "
                     "ORDER BY due_on DESC LIMIT 1", (due_date_id,)).fetchone()


def history(c: sqlite3.Connection, due_date_id: str) -> list[Any]:
    return list(c.execute("SELECT * FROM due_date_occurrences WHERE due_date_id=? ORDER BY due_on DESC LIMIT ?",
                          (due_date_id, HISTORY)))


def insert(c: sqlite3.Connection, row: Mapping[str, object]) -> None:
    cols = sorted(row)
    c.execute(f"INSERT INTO due_dates({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [row[k] for k in cols])


def update(c: sqlite3.Connection, due_date_id: str, fields: Mapping[str, object]) -> None:
    if not fields:
        return
    cols = sorted(fields)
    c.execute(f"UPDATE due_dates SET {','.join(k + '=?' for k in cols)} WHERE id=?",
              [*(fields[k] for k in cols), due_date_id])


def complete_occurrence(c: sqlite3.Connection, due_date_id: str, due_on: str, who: str, at: str) -> bool:
    """Mark one occurrence complete. True when this call did it; False when it already was. The row is created if the
    pass never opened it, and the UPDATE only lands on an open row, so two completions record one."""
    c.execute("INSERT OR IGNORE INTO due_date_occurrences(id, due_date_id, due_on, created_at) VALUES(?,?,?,?)",
              (new_id(), due_date_id, due_on, at))
    done = c.execute("UPDATE due_date_occurrences SET completed_at=?, completed_by_id=? "
                     "WHERE due_date_id=? AND due_on=? AND completed_at IS NULL", (at, who, due_date_id, due_on))
    return done.rowcount == 1


def reopen_occurrence(c: sqlite3.Connection, due_date_id: str, due_on: str) -> bool:
    """Take a completion back. True when the occurrence was complete and is open again."""
    done = c.execute("UPDATE due_date_occurrences SET completed_at=NULL, completed_by_id=NULL "
                     "WHERE due_date_id=? AND due_on=? AND completed_at IS NOT NULL", (due_date_id, due_on))
    return done.rowcount == 1


def open_occurrence(c: sqlite3.Connection, due_date_id: str, due_on: str, task_id: str, at: str) -> None:
    """Record that a task carries an open occurrence, so the next pass does not open a second one for it."""
    c.execute("INSERT OR IGNORE INTO due_date_occurrences(id, due_date_id, due_on, created_at) VALUES(?,?,?,?)",
              (new_id(), due_date_id, due_on, at))
    c.execute("UPDATE due_date_occurrences SET task_id=? WHERE due_date_id=? AND due_on=?",
              (task_id, due_date_id, due_on))


def live(c: sqlite3.Connection) -> list[Any]:
    """Due dates that are not archived, oldest first: the ones the daily pass walks."""
    return list(c.execute("SELECT * FROM due_dates WHERE archived_at IS NULL ORDER BY created_at, id"))


def contact_kind(c: sqlite3.Connection, contact_id: str) -> str | None:
    """A contact's kind (vendor, customer or both), or None when there is no such contact."""
    row = c.execute("SELECT kind FROM fin_contacts WHERE id=?", (contact_id,)).fetchone()
    return str(row[0]) if row else None


def page(c: sqlite3.Connection, *, q: str, archived: str, after: tuple[str, str] | None, most: int, viewer: str,
         teammate: bool, contact_kinds: tuple[str, ...], subject_type: str | None,
         subject_ids: tuple[str, ...] | None) -> list[Any]:
    """Due dates by name then id, past `after`, that this viewer may read. A guest (not a teammate) reads only the ones it
    works. A contact subject shows when its kind is in `contact_kinds` or the viewer works it. `archived` is hide, only or
    all; `subject_type` and `subject_ids` narrow to one record. Every filter is in the SQL, so paging counts right."""
    where: list[str] = []
    args: list[object] = []
    if archived == "hide":
        where.append("archived_at IS NULL")
    elif archived == "only":
        where.append("archived_at IS NOT NULL")
    if q:
        where.append("instr(lower(name), ?) > 0")
        args.append(q.lower())
    if not teammate:
        where.append("assignee_id = ?")
        args.append(viewer)
    contact = "subject_type <> 'contact' OR assignee_id = ?"
    args.append(viewer)
    if contact_kinds:
        contact += (" OR EXISTS (SELECT 1 FROM fin_contacts k WHERE k.id = due_dates.subject_id AND k.kind IN "
                    f"({','.join('?' * len(contact_kinds))}))")
        args.extend(contact_kinds)
    where.append(f"({contact})")
    if subject_type is not None:
        where.append("subject_type = ?")
        args.append(subject_type)
    if subject_ids is not None:
        where.append(f"subject_id IN ({','.join('?' * len(subject_ids))})")
        args.extend(subject_ids)
    if after is not None:
        where.append("(name, id) > (?, ?)")
        args.extend(after)
    sql = "SELECT * FROM due_dates WHERE " + " AND ".join(where) + " ORDER BY name, id LIMIT ?"
    return list(c.execute(sql, [*args, most]))


def subject(c: sqlite3.Connection, subject_type: str, subject_id: str) -> tuple[str, bool] | None:
    """The name of the record a due date sits on, and whether that record is active; None when there is no such record.
    Equipment is missing until its table exists (backend/records/equipment)."""
    if subject_type == "location":
        row = c.execute("SELECT name, archived_at FROM locations WHERE id=?",
                        (RS.resolve(c, "locations", subject_id),)).fetchone()
        return (str(row[0]), row[1] is None) if row else None
    if subject_type == "equipment":
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='equipment'").fetchone() is None:
            return None
        row = c.execute("SELECT name, archived_at FROM equipment WHERE id=?", (subject_id,)).fetchone()
        return (str(row[0]), row[1] is None) if row else None
    row = c.execute("SELECT name, active FROM fin_contacts WHERE id=?", (subject_id,)).fetchone()
    return (str(row[0]), bool(row[1])) if row else None


def subject_ids(c: sqlite3.Connection, subject_type: str, subject_id: str) -> tuple[str, ...]:
    """Every id that names the record `subject_id` names: the id itself, the record's own id, and the ids merged into it
    (a location's aliases). A due date stored under any of them sits on the same record."""
    if subject_type != "location":
        return (subject_id,)
    canon = RS.resolve(c, "locations", subject_id)
    ids = {subject_id, canon}
    ids.update(str(r[0]) for r in c.execute(
        "SELECT alias_id FROM record_aliases WHERE table_name='locations' AND record_id=?", (canon,)))
    return tuple(sorted(ids))


def next_due_and_status(c: sqlite3.Connection, subject_type: str, subject_ids_asked: Iterable[str], today: date
                        ) -> dict[str, tuple[date, str]]:
    """Each record's earliest open day and its most pressing status (see status): overdue, due or upcoming, by the rule the
    due filter and each due date's own pill use. Keyed by the ids asked for: a merged location's old id answers with its
    record's."""
    canon_of: dict[str, str] = {}
    for given in subject_ids_asked:
        canon_of[given] = RS.resolve(c, "locations", given) if subject_type == "location" else given
    lookup: dict[str, str] = {}
    for canon in set(canon_of.values()):
        for stored in subject_ids(c, subject_type, canon):
            lookup[stored] = canon
    best: dict[str, date] = {}
    state: dict[str, str] = {}
    pressing = ("overdue", "due", "upcoming")
    if lookup:
        marks = ",".join("?" * len(lookup))
        rows = c.execute(f"SELECT * FROM due_dates WHERE archived_at IS NULL AND subject_type=? AND subject_id IN ({marks})",
                         [subject_type, *lookup]).fetchall()
        for row in rows:
            canon = lookup[str(row["subject_id"])]
            nxt = next_open(row, completed_days(c, str(row["id"])))
            if nxt is not None and (canon not in best or nxt < best[canon]):
                best[canon] = nxt
            if nxt is not None:
                now = status(row, nxt, today)
                if canon not in state or pressing.index(now) < pressing.index(state[canon]):
                    state[canon] = now
    return {given: (best[canon_of[given]], state[canon_of[given]]) for given in canon_of if canon_of[given] in best}
