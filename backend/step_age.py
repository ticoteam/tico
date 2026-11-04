"""How long a ticket has been at its working step, and what Tico does when that runs long (docs/software-factory.md).

A ticket at a working step (status `doing`: In Progress or a Fixes step) of an engineering type (roadmap_flow.ENGINEERING),
or at any step whose flow sets `late_hours` (backend/step_flow.py), carries `step_since`: when it last entered that step or
that status, from its task events. Waiting, or any other held status, is not `doing`, so time spent there is not counted:
the clock starts again when the ticket comes back to work. The board shows that age on the card.

The stall watcher (backend/scheduler.py) runs one check on these tickets, at most once per stay at the step (`since`):

- LATE_HOURS at the step (the step's `late_hours`; an engineering type's step without one uses LATE_HOURS): the type's
  manager (Head of Engineering), or else the owner's manager, gets one notice to unblock or reroute it. It is a `blocker`
  wake, which a Lead's profile lets through at once. A manager who wrote in the ticket's room or changed it in the last
  SEEN_HOURS already knows how it stands: the notice waits until then. A type whose manager reads a stuck ticket on the
  exceptions list gets no notice; that list is the manager's (backend/pipeline_exceptions.py).

Owners are not woken for a quiet or idle ticket here. The task roundup lists it for its owner with the other tasks that need a look
(backend/task_roundup.py), and a turn that ended with its work standing reaches the manager's exceptions list.

Each ticket runs in its own savepoint (batch_work.isolated). One that raises is rolled back alone: its notice and its sent
marker go with it, the failure is logged, and it is looked at again on the next pass, while the rest of the sweep still runs."""

from . import hubdb as H
from .batch_work import isolated
from .pipeline_exceptions import reads_list
from .roadmap_flow import ENGINEERING

WORKING = "doing"
LATE_HOURS = 4
LATE_EVENT = "task.step_late"
SEEN_HOURS = 1


def _timed_steps(c):
    """{step id: flow} for the steps whose flow sets status_hours or late_hours."""
    from . import step_flow
    if not step_flow._ready(c):
        return {}
    return {s["id"]: step_flow.of(s) for s in H._rows(c.execute(
        "SELECT id, flow FROM task_steps WHERE flow LIKE '%status_hours%' OR flow LIKE '%late_hours%'"))}


def late_limit(c, row, timed=None):
    """The hours at its step after which a ticket is late, or None when that check is off there."""
    flow = (_timed_steps(c) if timed is None else timed).get(row.get("step_id")) or {}
    engineering = row.get("status") == WORKING and bool(c.execute(
        "SELECT 1 FROM task_types WHERE id=? AND role=?", (row.get("type_id") or "", ENGINEERING)).fetchone())
    return flow.get("late_hours", LATE_HOURS if engineering else None)


def since_map(c, rows):
    """{task id: when it entered its step} for the tickets among `rows` whose step is timed (module note). One query
    for the page. A ticket created straight into the step has no event: its creation time."""
    types = {r[0] for r in c.execute("SELECT id FROM task_types WHERE role=?", (ENGINEERING,))}
    timed = _timed_steps(c)
    rows = [r for r in rows if r.get("step_id") in timed
            or (r.get("status") == WORKING and r.get("type_id") in types)]
    if not rows:
        return {}
    ids = [r["id"] for r in rows]
    found = dict(c.execute(
        f"SELECT subject_id, max(created_at) FROM history_events WHERE subject_type='task' "
        f"AND subject_id IN ({','.join('?' * len(ids))}) AND kind IN ('step','status') GROUP BY subject_id", ids).fetchall())
    return {r["id"]: found.get(r["id"]) or r.get("created_at") for r in rows}


def _working(c):
    timed = list(_timed_steps(c))
    return H._rows(c.execute(
        f"SELECT t.* FROM {H.TASKS} t JOIN task_types y ON y.id=t.type_id WHERE (y.role=? AND t.status=?) "
        f"OR (t.step_id IN ({','.join('?' * len(timed))}) AND t.status NOT IN ('done','closed','declined')) "
        "ORDER BY t.updated_at", (ENGINEERING, WORKING, *timed)))


def _seen_by(c, row, who, at):
    """Whether `who` wrote in the ticket's room or changed it in the last SEEN_HOURS: they know how it stands."""
    since = H.shift(at, seconds=-SEEN_HOURS * 3600)
    return bool(c.execute("SELECT 1 FROM messages WHERE conversation_id=? AND sender_id=? AND created_at>? "
                          "AND deleted_at IS NULL UNION ALL SELECT 1 FROM history_events WHERE subject_type='task' "
                          "AND subject_id=? AND actor_id=? "
                          "AND created_at>? LIMIT 1", (row.get("conversation_id") or "", who, since, row["id"], who,
                                               since)).fetchone())


def _done(c, action, row, since):
    return bool(c.execute(f"SELECT 1 FROM events WHERE action=? AND target=? AND json_extract(detail,'$.since')=? "
                          "LIMIT 1", (action, row["id"], since)).fetchone())


def _hours(at, since):
    now, then = H.parse_ts(at), H.parse_ts(since)
    if now is None or then is None:
        raise ValueError(f"unreadable step time: {at!r}, {since!r}")
    return (now - then).total_seconds() / 3600


def _name(c, row):
    step = c.execute("SELECT name FROM task_steps WHERE id=?", (row.get("step_id") or "",)).fetchone()
    return ((f"#{row['number']} " if row.get("number") is not None else "") + str(row.get("title") or "")[:150],
            step[0] if step else "its step")


def _manager(c, row):
    """Who hears that a ticket runs late: the bot that manages its type, else the owner's manager. Never the owner."""
    from .lifecycle import _manager_actor
    owner = row.get("owner_id") or ""
    typed = H.type_manager(c, row)
    for who in ((H.bot_actor(typed) if typed else ""), _manager_actor(c, H.actor_id(owner)) if H.is_bot(owner) else ""):
        if who and who != owner:
            return who
    return None


def _row(c, row, start, at, timed=None):
    """What one working ticket is owed at `at`, written under the caller's savepoint. Returns the keys it lands in:
    "late" for a notice sent to its manager, "untold" when no manager could read it."""
    landed = []
    name, step = _name(c, row)
    owner = row.get("owner_id") or ""
    late_hours = late_limit(c, row, timed)
    if late_hours and _hours(at, start) >= late_hours and not _done(c, LATE_EVENT, row, start):
        typed = H.type_manager(c, row)
        if typed and reads_list(c, typed):
            return landed       # its manager reads a stuck ticket on the exceptions list, not as a notice
        to = _manager(c, row)
        if to and _seen_by(c, row, to, at):
            return landed   # the manager has just been at it: a later sweep tells them if it stays late
        hours = int(_hours(at, start))
        sent = (H._wake(c, row, to, f"Late: {name} has been at {step} for {hours} h (owner {H.actor_name(c, owner)}). "
                      "Unblock it, reroute it to another builder, or tell the owner what it waits on.",
                      {"wake": "blocker", "step_late": True}, trigger="stall_check") if to else None)
        if sent is None:
            # Nobody was told: no manager, or one that cannot read this ticket. Record nothing, so a
            # later sweep for the same stay can still send one notice once a manager is readable.
            landed.append("untold")
        else:
            H.event(c, H.KEEPER, LATE_EVENT, row["id"], {"owner": owner, "to": to, "since": start, "hours": hours})
            landed.append("late")
    return landed


def sweep(c, at=None):
    """Tell the manager about late tickets (module note). Returns the tickets told, nobody could be told, and that failed
    (logged; retried next pass)."""
    at = at or H.now()
    rows = _working(c)
    since = since_map(c, rows)
    timed = _timed_steps(c)
    done = {"late": [], "untold": [], "failed": []}
    for row in rows:
        start = since.get(row["id"])
        if not start:
            continue
        landed, errors = [], []
        with isolated(c, "step_age", row["id"], errors):
            landed = _row(c, row, start, at, timed)
        if errors:
            done["failed"].append(row["id"])
            continue
        for key in landed:
            done[key].append(row["id"])
    return done
