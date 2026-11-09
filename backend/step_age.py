"""How long a ticket has been at its working step, and when it runs late (docs/software-factory.md).

A ticket at a working step (status `doing`: In Progress or a Fixes step) of an engineering type (roadmap_flow.ENGINEERING),
or at any step whose flow sets `late_hours` (backend/step_flow.py), carries `step_since`: when it last entered that step or
that status, from its task events. Waiting, or any other held status, is not `doing`, so time spent there is not counted:
the clock starts again when the ticket comes back to work. The board shows that age on the card.

A ticket is late by the one Late rule (pipeline_manager.late_tickets): it has spent its step's Warn after
(task_steps.limit_minutes) in its open stage visit, and a hold pauses it. A step with no Warn after is never late ("Don't
warn" is authoritative; no default hours apply). `late_items` finds the late working ones and sends nothing: the task roundup lists each one on its manager's list as
"Late in <step>" (backend/task_roundup.py). The manager is the bot that manages the type, else the owner's manager, and a
person is never listed. A type whose manager reads a stuck ticket on the exceptions list gets none of these; that list is
the manager's (backend/pipeline_exceptions.py). A manager who wrote in the ticket's room or changed it in the last
SEEN_HOURS already knows how it stands, so it is not listed for them yet.

Owners are not woken for a late or idle ticket. The task roundup lists it for its owner with the other tasks that need a
look, and a turn that ended with its work standing reaches the manager's exceptions list."""

from . import hubdb as H
from .pipeline_exceptions import reads_list
from .roadmap_flow import ENGINEERING

WORKING = "doing"
LATE_HOURS = 4
LATE_EVENT = "task.step_late"
SEEN_HOURS = 1


def _timed_steps(c):
    """{step id: flow} for the steps whose flow sets status_hours or late_hours, or whose Limit (task_steps.limit_minutes,
    "Warn after") is set. A set Limit is the step's late_hours: it replaces the flow's value."""
    from . import step_flow

    if not step_flow._ready(c):
        return {}
    out: dict[str, dict] = {}
    for s in H._rows(
        c.execute(
            "SELECT id, flow, limit_minutes FROM task_steps WHERE flow LIKE '%status_hours%' OR flow LIKE '%late_hours%' "
            "OR limit_minutes IS NOT NULL"
        )
    ):
        flow = step_flow.of(s)
        if s.get("limit_minutes") is not None:
            flow = {**flow, "late_hours": s["limit_minutes"] / 60}
        if "late_hours" in flow or "status_hours" in flow:
            out[s["id"]] = flow
    return out


def since_map(c, rows):
    """{task id: when it entered its step} for the tickets among `rows` whose step is timed (module note). One query
    for the page. A ticket created straight into the step has no event: its creation time."""
    types = {r[0] for r in c.execute("SELECT id FROM task_types WHERE role=?", (ENGINEERING,))}
    timed = _timed_steps(c)
    rows = [r for r in rows if r.get("step_id") in timed or (r.get("status") == WORKING and r.get("type_id") in types)]
    if not rows:
        return {}
    ids = [r["id"] for r in rows]
    found = dict(
        c.execute(
            f"SELECT subject_id, max(created_at) FROM history_events WHERE subject_type='task' "
            f"AND subject_id IN ({','.join('?' * len(ids))}) AND kind IN ('step','status') GROUP BY subject_id",
            ids,
        ).fetchall()
    )
    return {r["id"]: found.get(r["id"]) or r.get("created_at") for r in rows}


def _working(c):
    timed = list(_timed_steps(c))
    return H._rows(
        c.execute(
            f"SELECT t.* FROM {H.TASKS} t JOIN task_types y ON y.id=t.type_id WHERE (y.role=? AND t.status=?) "
            f"OR (t.step_id IN ({','.join('?' * len(timed))}) AND t.status NOT IN ('done','closed','declined')) "
            "ORDER BY t.updated_at",
            (ENGINEERING, WORKING, *timed),
        )
    )


def _seen_by(c, row, who, at):
    """Whether `who` wrote in the ticket's room or changed it in the last SEEN_HOURS: they know how it stands."""
    since = H.shift(at, seconds=-SEEN_HOURS * 3600)
    return bool(
        c.execute(
            "SELECT 1 FROM messages WHERE conversation_id=? AND sender_id=? AND created_at>? "
            "AND deleted_at IS NULL UNION ALL SELECT 1 FROM history_events WHERE subject_type='task' "
            "AND subject_id=? AND actor_id=? "
            "AND created_at>? LIMIT 1",
            (row.get("conversation_id") or "", who, since, row["id"], who, since),
        ).fetchone()
    )


def _step(c, row):
    step = c.execute("SELECT name FROM task_steps WHERE id=?", (row.get("step_id") or "",)).fetchone()
    return step[0] if step else "its step"


def _manager(c, row):
    """Who hears that a ticket runs late: the bot that manages its type, else the owner's manager. Never the owner."""
    from .lifecycle import _manager_actor

    owner = row.get("owner_id") or ""
    typed = H.type_manager(c, row)
    for who in ((H.bot_actor(typed) if typed else ""), _manager_actor(c, H.actor_id(owner)) if H.is_bot(owner) else ""):
        if who and who != owner:
            return who
    return None


def late_items(c, at=None):
    """The working tickets late at their step at `at`, oldest first: dicts with row, to (the bot actor whose list gets the
    line), since (when it entered its stage visit), hours (how long it has been there) and step (its name). Lateness is
    pipeline_manager.late_tickets, the one Late rule the pipeline rail and overview read. A ticket is left out when its
    manager reads it on the exceptions list, when no bot manages it, or when that manager has just been at it."""
    from . import pipeline_manager as PM  # read here: pipeline_manager imports this module

    at = at or H.now()
    rows = _working(c)
    late = PM.late_tickets(c, rows, at)
    out = []
    for row in rows:
        found = late.get(row["id"])
        if not found:
            continue
        typed = H.type_manager(c, row)
        if typed and reads_list(c, typed):
            continue
        to = _manager(c, row)
        if not to or not H.is_bot(to) or _seen_by(c, row, to, at):
            continue
        out.append({"row": row, "to": to, "since": found["since"], "hours": found["hours"], "step": _step(c, row)})
    out.sort(key=lambda item: (item["since"], item["row"]["id"]))
    return out
