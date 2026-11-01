"""Due dates' tasks (plan §7.2, H10): once a day, in company time, the occurrences that fall due within their lead time open
ONE task per teammate listing them all, so a teammate with forty of them is woken once, not forty times.

- The pass runs at the first scheduler tick of each company day, as backend/release_flow.py's daily hour does. A run the
  same day opens nothing new. Completing an occurrence is its own action; closing its task completes nothing, so an
  occurrence still open when its task closes is opened again by the next day's pass (H10).
- An occurrence whose task is still open is not opened again. An archived due date, or one whose record is archived or
  gone, opens nothing further (H10c).
- An assignee that is missing or cannot take work (archived, paused) gets one Needs-you item for the install owner each
  day until it is fixed. The occurrences stay open, so nothing is dropped.
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from . import automation_due
from . import hubdb as H
from .finance import context as FC
from .finance.store import tico as TI
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


def candidates(c: Any, today: Any) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """The open occurrences that need a task, by the actor who works them, and one entry per due date whose assignee
    cannot take one. Reads only."""
    groups: dict[str, list[dict[str, Any]]] = {}
    unassigned: list[dict[str, Any]] = []
    for row in RD.live(c):
        due_date_id = str(row["id"])
        found = RD.subject(c, str(row["subject_type"]), str(row["subject_id"]))
        if found is None or not found[1]:
            continue                                   # an archived or missing record opens nothing (H10c)
        through = today + timedelta(days=int(row["lead_days"]))
        done = RD.completed_days(c, due_date_id)
        for day in RD.open_days(row, through, done):
            occ = RD.occurrence(c, due_date_id, day.isoformat())
            if occ is not None and occ["task_id"] and _task_open(c, str(occ["task_id"])):
                continue                               # its task is still open: the teammate has it
            item = {"due_date_id": due_date_id, "name": str(row["name"]), "subject": found[0], "due_on": day}
            actor, why = assignee(c, str(row["assignee_id"]))
            if why:
                if not any(u["due_date_id"] == due_date_id for u in unassigned):
                    unassigned.append({**item, "assignee": str(row["assignee_id"]), "why": why})
            else:
                groups.setdefault(actor, []).append(item)
    return groups, unassigned


def due(c: Any, at: datetime) -> bool:
    """Whether the day's pass has not run yet for this company day. Reads only."""
    return _last(c) != RD.local_day(at, zone()).isoformat()


def _open_for(c: Any, actor: str, items: list[dict[str, Any]], at: datetime) -> dict[str, Any]:
    """One task for one teammate, listing its occurrences, with each occurrence linked to it."""
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
                         deduplicate=False, trigger="schedule", trigger_source="due_dates")
    for i in items:
        RD.open_occurrence(c, i["due_date_id"], i["due_on"].isoformat(), str(task["id"]), _stamp(at))
    return {"actor": actor, "task": str(task["id"]), "occurrences": n}


def _notify(c: Any, item: dict[str, Any]) -> str | None:
    """One Needs-you item for the install owner about a due date whose assignee cannot take work."""
    owner = FC.owner(c)
    if not owner:
        return None
    title = f"Due date has no teammate: {item['name']} ({item['due_date_id'][:8]})"[:200]
    body = (f"{item['name']} at {item['subject']} is assigned to {item['assignee']}, which {item['why']}. Its "
            "occurrences stay open. Give it an active teammate (due_date_update) and the next pass opens its task.")
    return TI.needs_you(c, owner, title, body)


def tick(c: Any, at: datetime) -> dict[str, Any] | None:
    """Run the company day's pass once: open the tasks, tell the owner of any unassignable due date, and mark the day.
    None when the day's pass already ran. Caller owns the transaction."""
    today = RD.local_day(at, zone())
    if _last(c) == today.isoformat():
        return None
    groups, unassigned = candidates(c, today)
    tasks: list[dict[str, Any]] = []
    notices = [n for n in (_notify(c, item) for item in unassigned) if n]
    for actor, items in sorted(groups.items()):
        try:
            tasks.append(_open_for(c, actor, items, at))
        except H.Refused as exc:               # a rule declined the task: the owner hears why, and the day's pass ends
            owner = FC.owner(c)
            detail = str(getattr(exc, "detail", "") or exc)
            if owner:
                made = TI.needs_you(c, owner, f"Due dates for {actor} could not open", detail)
                notices.extend([made] if made else [])
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (KEY, json.dumps(today.isoformat())))
    return {"day": today.isoformat(), "tasks": tasks, "notices": notices}
