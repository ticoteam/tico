"""The routine a rule's due date can link (docs/rules-plan.md §4, "Linked to a rule's due date"): one "Before a due date"
automation (backend/automations.py, trigger `due`) that opens the due date's task `task_days` before each occurrence.

- The automation is saved through the automations module (`_checked`, `_save`, `remove`), so it has versions, an audit
  row and its `next_due` like any routine, and shows on the Automations page.
- Its trigger carries the due date's days, repeat, zone and id (`rule_due_id`); its one step is a `create_task` titled
  with the rule's sentence for the due date's owner. The automation's owner is that owner too, so it acts as them and
  the task is their own work (`task_caps` never cap it).
- `rule_due_dates.routine_id` names it. An edit that starts a new row moves the same automation to it; turning the
  routine off (or removing the due date) deletes the automation, as a person deleting it on the Automations page does.
  A sync keeps a linked routine's paused or running state.
- Completing its task marks the day Satisfied, and a day already marked by hand is skipped: backend/rule_due.py.
"""
from . import automation_due
from . import automations as AU
from . import hubdb as H
from . import rule_due as D
from .store import Problem

STEP_ID = "open"
NAME_MAX = 120          # an automation's name (automation_flow.check)
TITLE_MAX = 300         # a create_task's title (automation_actions._create_task)
UNCHANGED = object()    # an edit that leaves the routine as it is


def task_days_of(body, key="task_days"):
    """The lead time a request sets: an int from 0 to automation_due.MAX_OPEN_DAYS, None (no routine), or UNCHANGED
    when the request does not name it."""
    if key not in body:
        return UNCHANGED
    value = body[key]
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= automation_due.MAX_OPEN_DAYS:
        raise Problem("validation", f"task_days is a whole number of days from 0 to {automation_due.MAX_OPEN_DAYS}", 422)
    return value


def _flow(row, rule_text, days, before=None):
    title = " ".join(str(rule_text or "").split())
    owner = "person:" + row["owner"][6:] if row["owner"].startswith("human:") else row["owner"]
    trigger = {"type": "due", "first_due": row["first_due_on"], "repeats": row["repeats"], "open_days": days,
               "tz": row["timezone"], "rule_due_id": row["id"]}
    step = {"id": STEP_ID, "type": "action", "do": "create_task",
            "args": {"title": title[:TITLE_MAX], "owner": row["owner"]}}
    name = title if len(title) <= NAME_MAX else title[:NAME_MAX - 1].rstrip() + "…"
    # A new routine starts on; one already linked keeps its state, so an edit never resumes a paused one.
    enabled = bool(before.get("enabled", True)) if before else True
    return {**(before or {}), "name": name, "owner": owner, "trigger": trigger, "steps": [step], "enabled": enabled,
            "builtin": False}


def current_days(c, row):
    """The lead time of the routine the row links, or None with none."""
    return D.task_days(c, [row]).get(row.get("routine_id") or "")


def sync(c, actor, row, rule_text, days, via=None):
    """Make the row's routine match it: `days` an int turns it on (made, or the linked one moved to this row's dates,
    owner and id), None turns it off. Returns the routine's id, or None."""
    linked = AU._row(c, row["routine_id"]) if row.get("routine_id") else None
    if days is None:
        if linked:
            AU.remove(c, actor, linked, via)
        if row.get("routine_id"):
            D.update_in_place(c, row["id"], {"routine_id": None})
        return None
    before = AU.flow_of(linked) if linked else None
    flow = AU._checked(c, _flow(row, rule_text, days, before), access=False)
    aid = linked["id"] if linked else "auto_" + H.new_id()
    # The row names its routine first: only the routine a due date links may carry its id (automations._save).
    if row.get("routine_id") != aid:
        D.update_in_place(c, row["id"], {"routine_id": aid})
    if flow != before:
        AU._save(c, actor, aid, flow, before, via=via)
    return aid


def off(c, actor, row, via=None):
    """The due date was removed: its routine goes too, but only while the routine is this row's. An edit moves the
    routine to the new row and the ended one keeps its routine_id, so its trigger's `rule_due_id` says whose it is."""
    linked = AU._row(c, row["routine_id"]) if row.get("routine_id") else None
    if linked and str((AU.flow_of(linked).get("trigger") or {}).get("rule_due_id") or "") == row["id"]:
        AU.remove(c, actor, linked, via)
