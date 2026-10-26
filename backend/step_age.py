"""How long a Dev ticket has been at its working step, and what Tico does when that runs long (docs/software-factory.md).

A ticket of an engineering type (roadmap_flow.ENGINEERING) at a working step (status `doing`: In Progress or a Fixes
step) carries `step_since`: when it last entered that step or that status, from its task events. Waiting, or any other
held status, is not `doing`, so time spent there is not counted: the clock starts again when the ticket comes back to
work. The board shows that age on the card.

Two checks run with the stall watcher (backend/scheduler.py), each at most once per stay at the step (`since`):

- STATUS_HOURS with no activity (no update, checkpoint or message from anyone but Tico): its bot owner is woken once
  for a status. The plain stall and status checks are held while the ticket stands as at its owner's last checkpoint
  (wake_rules.same_state), which is how a ticket whose gate move was refused sat in Building for hours; this wake is
  not one of those, so it goes through. The same skips apply (H.stall_skip): a live session, an open question, a snooze,
  a due date, a blocker, a subtask, or an owner whose profile has no stall checks.
- LATE_HOURS at the step: the type's manager (Head of Engineering), or else the owner's manager, gets one notice to
  unblock or reroute it. It is a `blocker` wake, which a Lead's profile lets through at once."""

from . import hubdb as H
from .roadmap_flow import ENGINEERING

WORKING = "doing"
STATUS_HOURS = 2
LATE_HOURS = 4
STATUS_EVENT = "task.step_status"
LATE_EVENT = "task.step_late"


def since_map(c, rows):
    """{task id: when it entered its step} for the engineering tickets among `rows` at a working step. One query for
    the page. A ticket created straight into the step has no event: its creation time."""
    types = {r[0] for r in c.execute("SELECT id FROM task_types WHERE role=?", (ENGINEERING,))}
    rows = [r for r in rows if r.get("status") == WORKING and r.get("type_id") in types]
    if not rows:
        return {}
    ids = [r["id"] for r in rows]
    found = dict(c.execute(
        f"SELECT task_id, max(ts) FROM task_events WHERE task_id IN ({','.join('?' * len(ids))}) "
        "AND field IN ('step','status') GROUP BY task_id", ids).fetchall())
    return {r["id"]: found.get(r["id"]) or r.get("created") for r in rows}


def _working(c):
    return H._rows(c.execute(
        "SELECT t.* FROM tasks t JOIN task_types y ON y.id=t.type_id WHERE y.role=? AND t.status=? ORDER BY t.updated",
        (ENGINEERING, WORKING)))


def _activity(c, row):
    """The last time anyone but Tico touched the ticket: its update, its checkpoint, a message in its room."""
    said = c.execute("SELECT max(created) FROM messages WHERE conversation_id=? AND from_actor<>? AND deleted_at IS NULL",
                     (row.get("conversation_id") or "", H.KEEPER)).fetchone()[0]
    return max(x for x in (row.get("updated"), row.get("checkpoint_at"), said) if x)


def _done(c, action, row, since):
    return bool(c.execute(f"SELECT 1 FROM events WHERE action=? AND target=? AND json_extract(detail_json,'$.since')=? "
                          "LIMIT 1", (action, row["id"], since)).fetchone())


def _hours(at, since):
    return (H.parse_ts(at) - H.parse_ts(since)).total_seconds() / 3600


def _name(c, row):
    step = c.execute("SELECT name FROM task_steps WHERE id=?", (row.get("step_id") or "",)).fetchone()
    return ((f"#{row['number']} " if row.get("number") is not None else "") + str(row.get("title") or "")[:150],
            step[0] if step else "its step")


def _manager(c, row):
    """Who hears that a ticket runs late: the bot that manages its type, else the owner's manager. Never the owner."""
    from .lifecycle import _manager_actor
    owner = row.get("owner") or ""
    typed = H.type_manager(c, row)
    for who in ((H.bot_actor(typed) if typed else ""), _manager_actor(c, H.actor_id(owner)) if H.is_bot(owner) else ""):
        if who and who != owner:
            return who
    return None


def sweep(c, at=None):
    """Ask for a status on quiet tickets and tell the manager about late ones (module note). Returns what it did."""
    at = at or H.now()
    rows = _working(c)
    since = since_map(c, rows)
    parked = H.parked_bots(c)
    asked, late, untold = [], [], []
    for row in rows:
        start = since.get(row["id"])
        if not start:
            continue
        name, step = _name(c, row)
        owner = row.get("owner") or ""
        if (H.is_bot(owner) and H.actor_id(owner) not in parked and _hours(at, _activity(c, row)) >= STATUS_HOURS
                and not _done(c, STATUS_EVENT, row, start) and not H.stall_skip(c, row, at)
                and (H.bot(c, H.actor_id(owner)) or {}).get("state") == "active"):
            quiet = int(_hours(at, _activity(c, row)))
            H._wake(c, row, owner, f"Status check: {name} has had no activity for {quiet} h at {step}. Checkpoint where "
                    "it stands: carry it on now, or, if something holds it (a refused move, a review, a person), say "
                    "what and set it waiting on that.", {"wake": "step_status"}, trigger="stall_check")
            H.event(c, H.KEEPER, STATUS_EVENT, row["id"], {"bot": H.actor_id(owner), "since": start,
                                                             "quiet_hours": quiet})
            asked.append(row["id"])
        if _hours(at, start) >= LATE_HOURS and not _done(c, LATE_EVENT, row, start):
            to = _manager(c, row)
            hours = int(_hours(at, start))
            sent = (H._wake(c, row, to, f"Late: {name} has been at {step} for {hours} h (owner {H.actor_id(owner)}). "
                          "Unblock it, reroute it to another builder, or tell the owner what it waits on.",
                          {"wake": "blocker", "step_late": True}, trigger="stall_check") if to else None)
            if sent is None:
                # Nobody was told: no manager, or one that cannot read this ticket. Record nothing, so a
                # later sweep for the same stay can still send one notice once a manager is readable.
                untold.append(row["id"])
                continue
            H.event(c, H.KEEPER, LATE_EVENT, row["id"], {"owner": owner, "to": to, "since": start, "hours": hours})
            late.append(row["id"])
    return {"asked": asked, "late": late, "untold": untold}
