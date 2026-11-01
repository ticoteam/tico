"""Due dates as Finance writes and reads them (plan §7.2): four action kinds, the reads behind `/api/v2/due-dates`, and the
`due_date` search. A planner checks the payload and the asker's right, then writes when the action commits, in the same
commit as the action. The daily pass that opens tasks is backend/due_date_flow.py."""
from datetime import date, datetime
from typing import Any

from .. import due_date_flow as FLOW
from ..records import core as RC
from ..records import due_dates as RD
from . import tab
from . import tab_ap as TA
from .context import Ctx, load
from .core import ap as AP
from .core import kinds as K
from .core import validate as V
from .core import view as VW
from .core.errors import FinanceError, NOT_FOUND, denied, invalid
from .handlers import Plan, Planner, Result

Row = dict[str, object]
CREATE_FIELDS = ("name", "subject_type", "subject_id", "repeats", "every_count", "starts_on", "lead_days", "assignee_id")
UPDATE_FIELDS = ("due_date_id", "name", "lead_days", "assignee_id", "repeats", "every_count", "starts_on",
                 "next_due_on", "active")


def _shape(build: Any) -> Any:
    try:
        return build()
    except RC.RecordError as exc:
        raise invalid(str(exc)) from exc


def _row(ctx: Ctx, p: V.Payload) -> Any:
    row = RD.get(ctx.c, V.text(p, "due_date_id", 80))
    if row is None:
        raise FinanceError(NOT_FOUND, "no such due date")
    return row


def _subject(ctx: Ctx, subject_type: str, subject_id: str) -> str:
    """The name of the record a due date sits on. A missing one is not found; an archived one is refused."""
    found = RD.subject(ctx.c, subject_type, subject_id)
    if found is None:
        raise FinanceError(NOT_FOUND, f"no such {subject_type}")
    if not found[1]:
        raise invalid(f"that {subject_type} is archived")
    return found[0]


def _assignee(ctx: Ctx, value: str) -> str:
    actor, why = FLOW.assignee(ctx.c, value)
    if why:
        raise invalid(f"the assignee {why}")
    return actor


def _every(p: V.Payload, repeats: str, current: object) -> int | None:
    """The count a completion-anchored repeat needs (every_days, every_months); None for a calendar one."""
    if repeats not in RD.COMPLETION:
        if p.get("every_count") is not None:
            raise invalid("every_count is for every_days and every_months")
        return None
    if "every_count" in p:
        return V.integer(p, "every_count", 1, RD.MAX_EVERY)
    if current is None:
        raise invalid("every_count is needed for every_days and every_months")
    return int(str(current))


def _moment(ctx: Ctx) -> date:
    return RD.local_day(datetime.fromisoformat(ctx.at), FLOW.zone())


def due_date_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, CREATE_FIELDS)
    name = _shape(lambda: RC.name(V.text(p, "name", RC.MAX_NAME)))
    subject_type = V.choice(p, "subject_type", RD.SUBJECT_TYPES)
    subject_id = V.text(p, "subject_id", 80)
    _subject(ctx, subject_type, subject_id)
    repeats = V.choice(p, "repeats", RD.REPEATS)
    every = _every(p, repeats, None)
    starts_on = V.day(p, "starts_on")
    lead = V.integer(p, "lead_days", 0, RD.MAX_LEAD, 0)
    assignee = _assignee(ctx, V.text(p, "assignee_id", 80))
    due_date_id = RD.new_id()
    row = {"id": due_date_id, "name": name, "subject_type": subject_type, "subject_id": subject_id,
           "repeats": repeats, "every_count": every, "starts_on": starts_on,
           "next_due_on": starts_on if repeats in RD.COMPLETION else None, "lead_days": lead,
           "assignee_id": assignee, "created_at": ctx.at, "created_by_id": who}

    def apply(aid: str) -> Result:
        RD.insert(ctx.c, row)
        return {"due_date_id": due_date_id}
    return Plan(K.default_gate(ctx.org, who, "due_date_create"), f"Add due date {name}"[:200], apply)


def due_date_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, UPDATE_FIELDS)
    row = _row(ctx, p)
    due_date_id = str(row["id"])
    changes: dict[str, object] = {}
    if "name" in p:
        changes["name"] = _shape(lambda: RC.name(V.text(p, "name", RC.MAX_NAME)))
    if "lead_days" in p:
        changes["lead_days"] = V.integer(p, "lead_days", 0, RD.MAX_LEAD)
    if "assignee_id" in p:
        changes["assignee_id"] = _assignee(ctx, V.text(p, "assignee_id", 80))
    repeats = V.choice(p, "repeats", RD.REPEATS) if "repeats" in p else str(row["repeats"])
    starts_on = V.day(p, "starts_on") if "starts_on" in p else str(row["starts_on"])
    if "starts_on" in p:
        changes["starts_on"] = starts_on
    changes["repeats"] = repeats
    changes["every_count"] = _every(p, repeats, row["every_count"])
    if repeats in RD.COMPLETION:
        if "next_due_on" in p:
            changes["next_due_on"] = V.day(p, "next_due_on")
        elif str(row["repeats"]) in RD.COMPLETION:
            changes["next_due_on"] = str(row["next_due_on"])
        else:
            changes["next_due_on"] = starts_on
    elif "next_due_on" in p:
        raise invalid("next_due_on is for every_days and every_months")
    else:
        changes["next_due_on"] = None
    if "active" in p:
        changes["archived_at"] = None if V.flag(p, "active", True) else ctx.at

    def apply(aid: str) -> Result:
        RD.update(ctx.c, due_date_id, {**changes, "updated_at": ctx.at, "updated_by_id": who})
        return {"due_date_id": due_date_id}
    return Plan(K.default_gate(ctx.org, who, "due_date_update"), f"Change due date {row['name']}"[:200], apply)


def _complete(ctx: Ctx, who: str, due_date_id: str, day: date) -> Result:
    """Complete one occurrence. Idempotent: a second completion records nothing. A completion-anchored chain moves to
    the completion day plus its count, so a late completion shifts it."""
    row = RD.get(ctx.c, due_date_id)
    iso = day.isoformat()
    done = RD.complete_occurrence(ctx.c, due_date_id, iso, who, ctx.at)
    nxt = str(row["next_due_on"]) if row["next_due_on"] else None
    if done and str(row["repeats"]) in RD.COMPLETION:
        nxt_day = RD.step_after(_moment(ctx), str(row["repeats"]), int(row["every_count"]))
        nxt = nxt_day.isoformat()
        RD.update(ctx.c, due_date_id, {"next_due_on": nxt, "updated_at": ctx.at, "updated_by_id": who})
    return {"due_date_id": due_date_id, "due_on": iso, "completed": done, "next_due_on": nxt}


def due_date_occurrence_complete(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("due_date_id", "due_on"))
    row = _row(ctx, p)
    due_date_id = str(row["id"])
    if "due_on" in p:
        day = date.fromisoformat(V.day(p, "due_on"))
    else:
        found = RD.next_open(row, RD.completed_days(ctx.c, due_date_id))
        if found is None:
            raise invalid("nothing is open on this due date")
        day = found
    existing = RD.occurrence(ctx.c, due_date_id, day.isoformat())
    completed = existing is not None and existing["completed_at"] is not None
    if not completed and not RD.is_occurrence(row, day):
        raise invalid(f"{day.isoformat()} is not one of this due date's days")

    def apply(aid: str) -> Result:
        return _complete(ctx, who, due_date_id, day)
    return Plan(K.default_gate(ctx.org, who, "due_date_occurrence_complete"),
                f"Complete {row['name']} for {day.isoformat()}"[:200], apply)


def due_date_occurrence_reopen(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Take back a completion. A completion-anchored chain returns to that occurrence's day, so only its latest
    completion can be reopened: a later one was made against the chain it moved."""
    V.only(p, ("due_date_id", "due_on"))
    row = _row(ctx, p)
    due_date_id = str(row["id"])
    day = date.fromisoformat(V.day(p, "due_on"))
    existing = RD.occurrence(ctx.c, due_date_id, day.isoformat())
    if existing is None or existing["completed_at"] is None:
        raise invalid(f"{day.isoformat()} is not complete")
    if str(row["repeats"]) in RD.COMPLETION:
        latest = RD.latest_completed(ctx.c, due_date_id)
        if latest is not None and str(latest["due_on"]) != day.isoformat():
            raise invalid("reopen the latest completion first")

    def apply(aid: str) -> Result:
        reopened = RD.reopen_occurrence(ctx.c, due_date_id, day.isoformat())
        if reopened and str(row["repeats"]) in RD.COMPLETION:
            RD.update(ctx.c, due_date_id, {"next_due_on": day.isoformat(), "updated_at": ctx.at,
                                           "updated_by_id": who})
        return {"due_date_id": due_date_id, "due_on": day.isoformat(), "reopened": reopened}
    return Plan(K.default_gate(ctx.org, who, "due_date_occurrence_reopen"),
                f"Reopen {row['name']} for {day.isoformat()}"[:200], apply)


PLANNERS: dict[str, Planner] = {
    "due_date_create": due_date_create, "due_date_update": due_date_update,
    "due_date_occurrence_complete": due_date_occurrence_complete,
    "due_date_occurrence_reopen": due_date_occurrence_reopen,
}


# ----------------------------------------------------------------------------------------------------------- reads
def _in_view_or_refuse(view: VW.View) -> None:
    """Due dates are for viewers with a finance view at all, as records are."""
    if not view.sees_anything():
        raise denied("records are not in your view")


def _contacts_visible(ctx: Ctx, view: VW.View) -> bool:
    return any(TA.sees_contact_kind(ctx, view, k) for k in AP.CONTACT_KINDS)


def _out(c: Any, row: Any, today: date) -> Row:
    nxt = RD.next_open(row, RD.completed_days(c, str(row["id"])))
    found = RD.subject(c, str(row["subject_type"]), str(row["subject_id"]))
    return {"id": str(row["id"]), "name": str(row["name"]), "subject_type": str(row["subject_type"]),
            "subject_id": str(row["subject_id"]), "subject_name": found[0] if found else None,
            "repeats": str(row["repeats"]), "every_count": row["every_count"], "starts_on": str(row["starts_on"]),
            "next_due_on": nxt.isoformat() if nxt else None, "lead_days": int(row["lead_days"]),
            "assignee_id": str(row["assignee_id"]), "status": RD.status(row, nxt, today),
            "archived_at": row["archived_at"]}


def listing(c: Any, viewer: str, *, q: str = "", archived: str = "hide", cursor: str | None = None,
            limit: int = 50, at: str | None = None) -> Row:
    """Due dates by name then id. `archived` hides the archived ones (the default), shows only them, or both; a name
    search matches `q`. A viewer who sees no contacts is not shown due dates on contacts. `next_cursor` pages."""
    if archived not in ("hide", "only", "all"):
        raise invalid("archived is hide, only or all")
    ctx = load(c, at)
    view = ctx.view(viewer)
    _in_view_or_refuse(view)
    parts = tab.decode_cursor(cursor)
    after = (parts[0], parts[1]) if len(parts) == 2 else None
    most = tab.page_size(limit)
    rows = RD.page(c, q=(q or "").strip()[:100], archived=archived, after=after, most=most + 1,
                   contacts=_contacts_visible(ctx, view))
    shown = rows[:most]
    nxt = tab.encode_cursor([str(shown[-1]["name"]), str(shown[-1]["id"])]) if len(rows) > most else None
    today = RD.local_day(datetime.fromisoformat(ctx.at), FLOW.zone())
    return {"due_dates": [_out(c, r, today) for r in shown], "next_cursor": nxt}


def due_date(c: Any, viewer: str, *, due_date_id: str, at: str | None = None) -> Row:
    """One due date with its occurrences, newest first: each one's completion and the task that carried it."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    _in_view_or_refuse(view)
    row = RD.get(c, due_date_id)
    if row is None or (str(row["subject_type"]) == "contact" and not _contacts_visible(ctx, view)):
        raise FinanceError(NOT_FOUND, "no such due date")
    today = RD.local_day(datetime.fromisoformat(ctx.at), FLOW.zone())
    out = _out(c, row, today)
    out["history"] = [{"due_on": str(h["due_on"]), "completed_at": h["completed_at"],
                       "completed_by_id": h["completed_by_id"], "task_id": h["task_id"]}
                      for h in RD.history(c, str(row["id"]))]
    return {"due_date": out}
