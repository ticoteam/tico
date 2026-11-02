"""Due dates' tasks (plan §7.2, H10): once a day, in company time, the occurrences that fall due within their lead time open
ONE task per teammate listing them all, so a teammate with forty of them is woken once, not forty times.

- The pass runs at the first scheduler tick of each company day, as backend/release_flow.py's daily hour does. A run the
  same day opens nothing new. Completing an occurrence is its own action; closing its task completes nothing, so an
  occurrence still open when its task closes is opened again by the next day's pass (H10).
- An occurrence whose task is still open is not opened again. An archived due date, or one whose record is archived or
  gone, opens nothing further (H10c).
- An assignee that is missing or cannot take work (archived, paused) gets one Needs-you item for the install owner, naming
  all its due dates, until it is fixed. The occurrences stay open, so nothing is dropped.
- Reassigning a due date hands its open task to the new assignee (reassign); a task that cannot move closes with a note,
  and the next pass opens its occurrences again. No open task stays under the old assignee.
- A due date on a vendor or customer opens its tasks private to the assignee, apart from the tasks for other records,
  which stay company-visible. A Needs-you notice that names one is private too.
- A due date whose days move or that is cleared withdraws its open occurrence (withdraw): its task closes, or stays
  open with a note when another due date's occurrence is on it.
"""
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from . import automation_due
from . import hubdb as H
from .finance import context as FC
from .records import due_dates as RD

KEY = "due-dates-day"        # registry_metadata: the company day the pass last ran, as JSON text
LISTED = 50                  # occurrences a task body names; its title carries the whole number


def zone() -> str:
    from .updates import ZONE
    return str(ZONE.key)


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _last(c: Any) -> str | None:
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        value = json.loads(row[0]) if row else None
    except ValueError:
        return None
    return value if isinstance(value, str) else None


def assignee(c: Any, value: str) -> tuple[str, str]:
    """The actor a due date's assignee names, or ("", why not): an active bot or a person. The keeper takes no work."""
    actor = H.resolve_actor(c, value)
    if actor is None:
        return "", "is not a teammate"
    if actor == H.KEEPER:
        return "", "is the keeper, which takes no work"
    if H.is_bot(actor):
        state = str((H.bot(c, actor) or {}).get("state") or "missing")
        if state != "active":
            return "", f"is {state}"
    return actor, ""


def _task_open(c: Any, task_id: str) -> bool:
    row = c.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    return row is not None and str(row[0]) in H.ACTIVE_STATUSES


def candidates(c: Any, today: Any) -> tuple[dict[tuple[str, bool], list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """The open occurrences that need a task, by the actor who works them and whether they name a contact (a contact's
    tasks are private, so they never share a task with the rest), and one entry per assignee that cannot take work,
    naming the due dates it holds. The assignee is resolved once per due date. Reads only."""
    groups: dict[tuple[str, bool], list[dict[str, Any]]] = {}
    unassigned: dict[str, dict[str, Any]] = {}
    for row in RD.live(c):
        due_date_id = str(row["id"])
        found = RD.subject(c, str(row["subject_type"]), str(row["subject_id"]))
        if found is None or not found[1]:
            continue                                   # an archived or missing record opens nothing (H10c)
        contact = str(row["subject_type"]) == "contact"
        through = today + timedelta(days=int(row["lead_days"]))
        done = RD.completed_days(c, due_date_id)
        items: list[dict[str, Any]] = []
        for day in RD.open_days(row, through, done):
            occ = RD.occurrence(c, due_date_id, day.isoformat())
            if occ is not None and occ["task_id"] and _task_open(c, str(occ["task_id"])):
                continue                               # its task is still open: the teammate has it
            items.append({"due_date_id": due_date_id, "name": str(row["name"]), "subject": found[0], "due_on": day})
        if not items:
            continue
        actor, why = assignee(c, str(row["assignee_id"]))
        if why:
            held = unassigned.setdefault(str(row["assignee_id"]), {"assignee": str(row["assignee_id"]), "why": why,
                                                                   "names": [], "contact": False})
            held["names"].append(str(row["name"]))
            held["contact"] = bool(held["contact"]) or contact
        else:
            groups.setdefault((actor, contact), []).extend(items)
    return groups, unassigned


def due(c: Any, at: datetime) -> bool:
    """Whether the day's pass has not run yet for this company day. Reads only."""
    return _last(c) != RD.local_day(at, zone()).isoformat()


def _open_for(c: Any, actor: str, items: list[dict[str, Any]], at: datetime, private: bool) -> dict[str, Any]:
    """One task for one teammate, listing its occurrences, each linked to it. `private` keeps a task that names a
    contact to the assignee and its requester."""
    items.sort(key=lambda i: (i["due_on"], i["name"]))
    n = len(items)
    lines = [f"- {i['name']} at {i['subject']}, due {i['due_on'].isoformat()} (due date {i['due_date_id']})"
             for i in items[:LISTED]]
    if n > LISTED:
        lines.append(f"...and {n - LISTED} more.")
    body = ("These are due now or within their lead time.\n\n" + "\n".join(lines) +
            "\n\nMark each one done when its work is done: the Finance action due_date_occurrence_complete with its "
            "due_date_id and due_on. Closing this task completes nothing; anything still open comes back on the next "
            "day's pass.")
    title = f"Due dates: {n} due"
    task = H.task_create(c, H.KEEPER, title, body, actor, due=automation_due.due_end(items[0]["due_on"], zone()),
                         deduplicate=False, trigger="schedule", trigger_source="due_dates",
                         private=True if private else None)
    for i in items:
        RD.open_occurrence(c, i["due_date_id"], i["due_on"].isoformat(), str(task["id"]), _stamp(at))
    return {"actor": actor, "task": str(task["id"]), "occurrences": n}


def _needs_you(c: Any, person: str, title: str, body: str, private: bool = False) -> str | None:
    """One Needs-you item the person owns; an open one with the same title is reused. None when they can't be given one.
    `private` keeps it to the person and its requester, for a notice that names a contact."""
    try:
        task: Any = H.task_create(c, H.KEEPER, title[:200], body, person, deduplicate=True,
                                  private=True if private else None)
    except H.Refused:
        return None
    return str(task["id"]) if isinstance(task, dict) and task.get("id") else None


def _notify(c: Any, held: dict[str, Any]) -> str | None:
    """One Needs-you item for the install owner about an assignee that cannot take work, naming its due dates. The title
    has no count, so the open item is reused as more due dates fall to the same assignee."""
    owner = FC.owner(c)
    if not owner:
        return None
    names: list[str] = held["names"]
    n = len(names)
    title = f"Due dates need a teammate: {held['assignee']}"[:200]
    listed = "\n".join(f"- {name}" for name in names[:LISTED])
    more = f"\n...and {n - LISTED} more." if n > LISTED else ""
    body = (f"{n} due date(s) are assigned to {held['assignee']}, which {held['why']}. Their occurrences stay open. "
            "Give them an active teammate (due_date_update) and the next pass opens their tasks.\n\n" + listed + more)
    return _needs_you(c, owner, title, body, bool(held["contact"]))


def reassign(c: Any, due_date_id: str, new_actor: str, name: str) -> dict[str, list[str]]:
    """Hand a due date's open tasks to its new assignee; due_date_update calls this when the assignee changes. An open task
    whose open occurrences all belong to this due date moves to the new assignee through the task path, so its history shows
    the hand-off. A task that also carries another due date's open work, or that the task path refuses to move, closes with
    a note instead: its occurrences then have no open task, and the next pass opens each for whoever it is assigned to. No
    open task is left under the old assignee. Writes only through the hub's task calls."""
    note = f"{name} is now assigned to {new_actor}."
    moved: list[str] = []
    closed: list[str] = []
    rows = c.execute("SELECT DISTINCT task_id FROM due_date_occurrences WHERE due_date_id=? AND task_id IS NOT NULL "
                     "AND completed_at IS NULL", (due_date_id,)).fetchall()
    for (task_id,) in rows:
        task = str(task_id)
        if not _task_open(c, task):
            continue
        others = c.execute("SELECT COUNT(*) FROM due_date_occurrences WHERE task_id=? AND due_date_id<>? "
                           "AND completed_at IS NULL", (task, due_date_id)).fetchone()[0]
        if not others:
            try:
                H.task_update(c, H.KEEPER, task, owner=new_actor, note=note)
                moved.append(task)
                continue
            except H.Refused:
                pass
        H.task_close(c, H.KEEPER, task, note=note)
        closed.append(task)
    return {"moved": moved, "closed": closed}


def withdraw(c: Any, due_date_id: str, name: str, keep: Callable[[str], bool] | None = None) -> dict[str, list[str]]:
    """Take a due date's incomplete occurrences off it, because its days moved or it was cleared (a warranty's end does
    both). `keep` spares the days it answers true for, each an ISO day: a schedule change that leaves an open day on
    the schedule leaves its work alone. The other rows go, so the next pass opens what the due date asks for now. Each
    open task that carried one closes with a note, unless it also holds another open occurrence: that task stays open
    for them and gets the note. Writes only through the hub's task calls."""
    note = f"{name} changed: its open occurrence is withdrawn. The next pass opens its current one, if it has one."
    found = c.execute("SELECT due_on, task_id FROM due_date_occurrences WHERE due_date_id=? AND completed_at IS NULL",
                      (due_date_id,)).fetchall()
    gone = [(str(day), task) for day, task in found if keep is None or not keep(str(day))]
    c.executemany("DELETE FROM due_date_occurrences WHERE due_date_id=? AND due_on=? AND completed_at IS NULL",
                  [(due_date_id, day) for day, _ in gone])
    rows = sorted({(str(task),) for _, task in gone if task is not None})
    kept: list[str] = []
    closed: list[str] = []
    for (task_id,) in rows:
        task = str(task_id)
        if not _task_open(c, task):
            continue
        others = c.execute("SELECT COUNT(*) FROM due_date_occurrences WHERE task_id=? AND completed_at IS NULL",
                           (task,)).fetchone()[0]
        if others:
            H.task_update(c, H.KEEPER, task, note=note)
            kept.append(task)
        else:
            H.task_close(c, H.KEEPER, task, note=note)
            closed.append(task)
    return {"kept": kept, "closed": closed}


def tick(c: Any, at: datetime) -> dict[str, Any] | None:
    """Run the company day's pass once: open the tasks, tell the owner of any unassignable due date, and mark the day.
    None when the day's pass already ran. Caller owns the transaction."""
    today = RD.local_day(at, zone())
    if _last(c) == today.isoformat():
        return None
    groups, unassigned = candidates(c, today)
    tasks: list[dict[str, Any]] = []
    notices = [n for n in (_notify(c, held) for _, held in sorted(unassigned.items())) if n]
    for (actor, private), items in sorted(groups.items()):
        try:
            tasks.append(_open_for(c, actor, items, at, private))
        except H.Refused as exc:               # a rule declined the task: the owner hears why, and the pass goes on
            owner = FC.owner(c)
            detail = str(getattr(exc, "detail", "") or exc)
            if owner:
                made = _needs_you(c, owner, f"Due dates for {actor} could not open", detail, private)
                notices.extend([made] if made else [])
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (KEY, json.dumps(today.isoformat())))
    return {"day": today.isoformat(), "tasks": tasks, "notices": notices}
