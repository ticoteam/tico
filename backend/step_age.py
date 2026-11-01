"""How long a ticket has been at its working step, and what Tico does when that runs long (docs/software-factory.md).

A ticket at a working step (status `doing`: In Progress or a Fixes step) of an engineering type (roadmap_flow.ENGINEERING),
or at any step whose flow sets `status_hours` or `late_hours` (backend/step_flow.py), carries `step_since`: when it last entered that step or that status, from its task events. Waiting, or any other
held status, is not `doing`, so time spent there is not counted: the clock starts again when the ticket comes back to
work. The board shows that age on the card.

Two checks run with the stall watcher (backend/scheduler.py), each at most once per stay at the step (`since`):

The hours are the step's `status_hours` and `late_hours`; an engineering type's step without them uses STATUS_HOURS and
LATE_HOURS.

- STATUS_HOURS with no activity (no update, checkpoint or message from anyone but Tico): its bot owner is woken once
  for a status. The plain stall and status checks are held while the ticket stands as at its owner's last checkpoint
  (wake_rules.same_state), which is how a ticket whose gate move was refused sat in Building for hours; this wake is
  not one of those, so it goes through. The same skips apply (H.stall_skip): a live session, an open question, a snooze,
  a due date, a blocker, a subtask, or an owner whose profile has no stall checks.
- LATE_HOURS at the step: the type's manager (Head of Engineering), or else the owner's manager, gets one notice to
  unblock or reroute it. It is a `blocker` wake, which a Lead's profile lets through at once. A manager who wrote in
  the ticket's room or changed it in the last SEEN_HOURS already knows how it stands: the notice waits until then.

A third check is for the turn that ended and left its work standing: a working ticket, or an open check filed below an
engineering ticket, still `doing`, whose owner's last run on it finished TURN_IDLE_MINUTES ago with no run live, queued
or held on it after, and nothing recorded to wait on. Its owner is woken once per stay at the step (TURN_EVENT), with
the reason, so a turn that ended on a runtime blocker is picked up before the manager's hourly exceptions list has to
name it (backend/pipeline_exceptions.py). The wake is decided here, not by the owner's profile: a reviewer's profile
holds status checks as notes, and this is the one nudge its check gets before it times out.

Each ticket runs in its own savepoint (batch_work.isolated). One that raises is rolled back alone: its wakes and its
sent markers go with it, the failure is logged, and it is looked at again on the next pass, while the rest of the
sweep still runs."""

from . import hubdb as H
from .batch_work import isolated
from .pipeline_exceptions import reads_list
from .roadmap_flow import ENGINEERING

WORKING = "doing"
STATUS_HOURS = 2
LATE_HOURS = 4
STATUS_EVENT = "task.step_status"
TURN_EVENT = "task.turn_ended"
TURN_IDLE_MINUTES = 10
LATE_EVENT = "task.step_late"
SEEN_HOURS = 1


def _timed_steps(c):
    """{step id: flow} for the steps whose flow sets status_hours or late_hours."""
    from . import step_flow
    if not step_flow._ready(c):
        return {}
    return {s["id"]: step_flow.of(s) for s in H._rows(c.execute(
        "SELECT id, flow FROM task_steps WHERE flow LIKE '%status_hours%' OR flow LIKE '%late_hours%'"))}


def limits(c, row, timed=None):
    """(status hours, late hours) for a ticket at its step, either None when that check is off there."""
    flow = (_timed_steps(c) if timed is None else timed).get(row.get("step_id")) or {}
    engineering = row.get("status") == WORKING and bool(c.execute(
        "SELECT 1 FROM task_types WHERE id=? AND role=?", (row.get("type_id") or "", ENGINEERING)).fetchone())
    return (flow.get("status_hours", STATUS_HOURS if engineering else None),
            flow.get("late_hours", LATE_HOURS if engineering else None))


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
        f"SELECT task_id, max(ts) FROM task_events WHERE task_id IN ({','.join('?' * len(ids))}) "
        "AND field IN ('step','status') GROUP BY task_id", ids).fetchall())
    return {r["id"]: found.get(r["id"]) or r.get("created_at") for r in rows}


def _working(c):
    timed = list(_timed_steps(c))
    return H._rows(c.execute(
        "SELECT t.* FROM tasks t JOIN task_types y ON y.id=t.type_id WHERE (y.role=? AND t.status=?) "
        f"OR (t.step_id IN ({','.join('?' * len(timed))}) AND t.status NOT IN ('done','closed','declined')) "
        "ORDER BY t.updated_at", (ENGINEERING, WORKING, *timed)))


def _activity(c, row):
    """The last time anyone but Tico touched the ticket: its update, its checkpoint, a message in its room."""
    said = c.execute("SELECT max(created_at) FROM messages WHERE conversation_id=? AND from_actor<>? AND deleted_at IS NULL",
                     (row.get("conversation_id") or "", H.KEEPER)).fetchone()[0]
    return max(x for x in (row.get("updated_at"), row.get("checkpoint_at"), said) if x)


def _seen_by(c, row, who, at):
    """Whether `who` wrote in the ticket's room or changed it in the last SEEN_HOURS: they know how it stands."""
    since = H.shift(at, seconds=-SEEN_HOURS * 3600)
    return bool(c.execute("SELECT 1 FROM messages WHERE conversation_id=? AND from_actor=? AND created_at>? "
                          "AND deleted_at IS NULL UNION ALL SELECT 1 FROM task_events WHERE task_id=? AND actor=? "
                          "AND ts>? LIMIT 1", (row.get("conversation_id") or "", who, since, row["id"], who,
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
    owner = row.get("owner") or ""
    typed = H.type_manager(c, row)
    for who in ((H.bot_actor(typed) if typed else ""), _manager_actor(c, H.actor_id(owner)) if H.is_bot(owner) else ""):
        if who and who != owner:
            return who
    return None


def _row(c, row, start, at, parked, timed=None):
    """What one working ticket is owed at `at`, written under the caller's savepoint. Returns the keys it lands in:
    "asked" for a status wake, "late" for a notice sent to its manager, "untold" when no manager could read it."""
    landed = []
    name, step = _name(c, row)
    owner = row.get("owner") or ""
    status_hours, late_hours = limits(c, row, timed)
    if (status_hours and H.is_bot(owner) and H.actor_id(owner) not in parked
            and _hours(at, _activity(c, row)) >= status_hours
            and not _done(c, STATUS_EVENT, row, start) and not H.stall_skip(c, row, at)
            and (H.bot(c, H.actor_id(owner)) or {}).get("state") == "active"):
        quiet = int(_hours(at, _activity(c, row)))
        H._wake(c, row, owner, f"Status check: {name} has had no activity for {quiet} h at {step}. Checkpoint where "
                "it stands: carry it on now, or, if something holds it (a refused move, a review, a person), say "
                "what and set it waiting on that.", {"wake": "step_status"}, trigger="stall_check")
        H.event(c, H.KEEPER, STATUS_EVENT, row["id"], {"bot": H.actor_id(owner), "since": start,
                                                         "quiet_hours": quiet})
        landed.append("asked")
    if late_hours and _hours(at, start) >= late_hours and not _done(c, LATE_EVENT, row, start):
        typed = H.type_manager(c, row)
        if typed and reads_list(c, typed):
            return landed       # its manager reads a stuck ticket on the exceptions list, not as a notice
        to = _manager(c, row)
        if to and _seen_by(c, row, to, at):
            return landed   # the manager has just been at it: a later sweep tells them if it stays late
        hours = int(_hours(at, start))
        sent = (H._wake(c, row, to, f"Late: {name} has been at {step} for {hours} h (owner {H.actor_id(owner)}). "
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
    """Ask for a status on quiet tickets and tell the manager about late ones (module note). Returns what it did, and
    the tickets that failed (logged; retried next pass)."""
    at = at or H.now()
    rows = _working(c)
    since = since_map(c, rows)
    timed = _timed_steps(c)
    parked = H.parked_bots(c)
    done = {"asked": [], "late": [], "untold": [], "failed": []}
    for row in rows:
        start = since.get(row["id"])
        if not start:
            continue
        landed, errors = [], []
        with isolated(c, "step_age", row["id"], errors):
            landed = _row(c, row, start, at, parked, timed)
        if errors:
            done["failed"].append(row["id"])
            continue
        for key in landed:
            done[key].append(row["id"])
    return done


def _turn_items(c):
    """Working engineering tickets and the open checks below engineering tickets, still `doing`, owned by a bot."""
    from .check_pools import is_check_title
    rows = H._rows(c.execute(
        "SELECT t.* FROM tasks t JOIN task_types y ON y.id=t.type_id WHERE y.role=? AND t.status=? "
        "AND t.owner LIKE 'bot:%' ORDER BY t.updated_at", (ENGINEERING, WORKING)))
    rows += [r for r in H._rows(c.execute(
        "SELECT t.*, p.type_id AS parent_type FROM tasks t JOIN task_relations r ON r.from_task=t.id AND r.kind='parent' "
        "JOIN tasks p ON p.id=r.to_task JOIN task_types y ON y.id=p.type_id WHERE y.role=? AND t.status=? "
        "AND t.owner LIKE 'bot:%' ORDER BY t.updated_at", (ENGINEERING, WORKING)))
        if is_check_title(c, {"type_id": r.pop("parent_type")}, r.get("title"))]
    return rows


def turn_ended(c, at=None):
    """Wake the owner once when its turn ended and left a working ticket or check with nothing queued (module note).
    Returns the task ids woken."""
    from .pipeline_exceptions import _runs, _waits
    from .rules import WAKE, Decision
    at = at or H.now()
    rows = _turn_items(c)
    if not rows:
        return []
    starts = {}
    ids = [r["id"] for r in rows]
    for part in H.chunks(ids):
        starts.update(dict(c.execute(
            f"SELECT task_id, max(ts) FROM task_events WHERE task_id IN ({','.join('?' * len(part))}) "
            "AND field IN ('step','status') GROUP BY task_id", part).fetchall()))
    parked = H.parked_bots(c)
    cutoff = H.shift(at, minutes=-TURN_IDLE_MINUTES)
    woke = []
    for row in rows:
        start = starts.get(row["id"]) or row.get("created_at")
        owner = row["owner"]
        if H.actor_id(owner) in parked or (H.bot(c, H.actor_id(owner)) or {}).get("state") != "active":
            continue
        live, finished, jobs = _runs(c, row)
        if live or jobs or not finished:
            continue
        ended = finished[0]["finished_at"] or finished[0]["created_at"]
        if ended < start or ended > cutoff or _done(c, TURN_EVENT, row, start) or _waits(c, row, at):
            continue
        with isolated(c, "turn_ended", row["id"]):
            name, step = _name(c, row)
            H._write_message(
                c, H.KEEPER, owner, f"Your last turn on {name} ended with it still in progress at {step} and nothing "
                "queued to carry it on. Carry it on now, set it waiting on what holds it, or mark it done or blocked; "
                "if it stays like this it goes on the engineering exceptions list.",
                H.conversation(c, row["conversation_id"]), "notice",
                H.RT.stamp({"task": row["id"], "wake": "step_status", "turn_ended": True}, "stall_check", None),
                None, None, decided=Decision(WAKE, "turn_ended"))
            H.event(c, H.KEEPER, TURN_EVENT, row["id"], {"bot": H.actor_id(owner), "since": start, "ended": ended})
            woke.append(row["id"])
    return woke
