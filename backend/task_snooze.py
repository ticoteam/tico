"""Snoozing a task until a date.

A snoozed task says "nobody plans to get to this until then". It keeps its status, owner, goal and
place; only `snoozed_until` (and `snoozed_by`) are set. Until that moment:

- it is left out of the board, task lists (`GET /api/v2/tasks` unless `snoozed=all|only`), Needs you
  and Decide;
- nothing automatic moves it: no stall wake, spot check, stranded or stuck sweep (`H.not_yet_due`),
  no "Still wanted?" ask, no "Due:" reminder, and the keeper's notices about it reach its owner bot
  quietly (`H._wake`), as does another bot's comment (`H.comment_wakes`). A person's comment and Run
  now still reach the bot, and notices to anyone else (a requester bot handed it for review) are as usual.

When the moment passes, the scheduler clears the snooze (`wake_ended`): the task is back everywhere and
its owner bot gets one "Snooze over" notice, which also stands in for a "Due:" reminder that fell
inside the snooze. A note the snoozer left (`snoozed_note`) goes in that notice as "Your note:". Finishing,
declining or closing a task, or handing it on for review, clears its snooze (`H._set_status`). The requester is told when someone else snoozes their task.

This module imports nothing from hubdb at load, so hubdb can import it.
"""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from .batch_work import isolated

MAX_DAYS = 730
# The note the snoozer leaves for the bot's wake when the snooze ends: what to check, in a sentence or two.
NOTE_MAX = 500
# A date without a time is the start of that day where the team keeps its calendar (backend/updates.py).
ZONE = ZoneInfo("America/Los_Angeles")


def _H():
    from . import hubdb
    return hubdb


def snoozed(row, at=None):
    """Whether this task row is snoozed at `at` (a stored timestamp; now by default)."""
    until = (row or {}).get("snoozed_until")
    return bool(until) and until > (at or _H().now())


def parse_until(value):
    """`value` (an ISO-8601 date or date-time) as a stored UTC timestamp, or None when it is not one.
    A bare date, or a time with no offset, reads in the team's zone."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            at = datetime.combine(datetime.fromisoformat(text).date(), time(0), ZONE)
        else:
            at = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=ZONE)
    except ValueError:
        return None
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def clean_note(conn, actor, note):
    """The note stripped, or None when there is none; refused when it is longer than NOTE_MAX."""
    text = str(note or "").strip()
    if len(text) > NOTE_MAX:
        _H().refuse(conn, actor, "note", f"the note is at most {NOTE_MAX} characters: say only what to check")
    return text or None


def may_snooze(conn, actor, row, mover=False):
    """The parties and their managers, a role holder, a mover or the keeper."""
    H = _H()
    if H.task_mover(conn, actor, row, mover) or H.task_participant(conn, actor, row):
        return True
    from .task_roles import roles_of
    return any(actor in actors for actors in roles_of(conn, row["id"]).values())


def _check(conn, actor, task_id, mover):
    H = _H()
    H._writer(conn, actor)
    row = H.task(conn, task_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no task {task_id}")
    H._task_private_writer(conn, actor, row)
    if not may_snooze(conn, actor, row, mover):
        H.refuse(conn, actor, "identity", "Only the requester, owner, their managers, a role holder or a mover snoozes it")
    return row


def _label(until):
    return until[:10]


def snooze(conn, actor, task_id, until, mover=False, note=None):
    """Snooze a live task until `until` (see parse_until); snoozing it again moves the date. `note` is what to
    check when the snooze ends; snoozing again replaces it (none clears it)."""
    H = _H()
    row = _check(conn, actor, task_id, mover)
    if row["status"] not in H.ACTIVE_STATUSES:
        H.refuse(conn, actor, "snooze", f"the task is {row['status']}; only open work is snoozed")
    stamp = parse_until(until)
    if not stamp:
        H.refuse(conn, actor, "date", "snooze until an ISO-8601 date (2026-11-02) or date-time")
    at = H.now()
    if stamp <= at:
        H.refuse(conn, actor, "date", "snooze until a moment in the future")
    if stamp > H.shift(at, days=MAX_DAYS):
        H.refuse(conn, actor, "date", f"snooze for at most {MAX_DAYS} days; close it if nobody will")
    note = clean_note(conn, actor, note)
    if (stamp, note) == (row.get("snoozed_until"), row.get("snoozed_note")):
        return row          # the same date and note again: nothing changes, nobody is told again
    conn.execute("UPDATE tasks SET snoozed_until=?, snoozed_by=?, snoozed_note=?, updated_at=?, version=version+1 "
                 "WHERE id=?", (stamp, actor, note, at, task_id))
    H._task_event(conn, task_id, actor, "snoozed_until", row.get("snoozed_until"), stamp, note or "")
    after = H.task(conn, task_id)
    # The owner learns nobody expects it before then; a bot reads it on its next real run, not now.
    if after["owner"] != actor:
        H._wake(conn, after, after["owner"], f"Snoozed until {_label(stamp)}: {after['title']}",
                {"snooze": stamp}, quiet_bots=True, quiet=True)
    # Whoever asked for it is told too, so nobody parks a request without the requester knowing.
    if after["requester"] not in (actor, after["owner"]):
        who = ((H.bot(conn, H.actor_id(actor)) if H.is_bot(actor) else None) or {}).get("name") or H.actor_id(actor)
        H._wake(conn, after, after["requester"], f"Snoozed until {_label(stamp)} by {who}: {after['title']}",
                {"snooze": stamp}, quiet_bots=True, quiet=True)
    H.recount(conn, [after["owner"], after["requester"]])
    return after


def unsnooze(conn, actor, task_id, mover=False):
    """End a snooze early: the task is back now, and its owner bot is woken once."""
    H = _H()
    row = _check(conn, actor, task_id, mover)
    if not row.get("snoozed_until"):
        return row
    left = row.get("snoozed_note")
    after = _clear(conn, actor, row, "unsnoozed")
    if after["owner"] != actor and after["status"] in H.ACTIVE_STATUSES:
        H._wake(conn, after, after["owner"], f"Unsnoozed: {after['title']}" + _note_line(left),
                _wake_refs({"wake": "snooze"}, left))
    return after


def _note_line(note):
    return f"\nYour note: {note}" if note else ""


def _wake_refs(refs, note):
    return {**refs, "note": note} if note else refs


def _clear(conn, actor, row, note):
    H = _H()
    conn.execute("UPDATE tasks SET snoozed_until=NULL, snoozed_by=NULL, snoozed_note=NULL, updated_at=?, "
                 "version=version+1 WHERE id=?", (H.now(), row["id"]))
    H._task_event(conn, row["id"], actor, "snoozed_until", row["snoozed_until"], None, note)
    H.recount(conn, [row["owner"], row["requester"]])
    return H.task(conn, row["id"])


def clear_finished(conn, actor, row):
    """A task that is done, declined or closed has nothing to wait for."""
    if row.get("snoozed_until"):
        _clear(conn, actor, row, "finished")


def ended(conn, at):
    """Snoozed tasks whose date has come by `at`."""
    H = _H()
    return H._rows(conn.execute("SELECT * FROM tasks WHERE snoozed_until IS NOT NULL AND snoozed_until<=? "
                                "ORDER BY snoozed_until,id", (at,)))


def wake_ended(conn, at, failures=None):
    """The scheduler's pass: each task whose snooze ended is back, and its owner bot hears it once.
    Returns the task ids woken."""
    H = _H()
    woken = []
    for row in ended(conn, at):
        with isolated(conn, "snooze", row["id"], failures):
            left = row.get("snoozed_note")
            after = _clear(conn, H.KEEPER, row, "snooze over")
            if after["status"] not in H.ACTIVE_STATUSES:
                continue
            if not H.is_bot(after["owner"]) or (H.bot(conn, H.actor_id(after["owner"])) or {}).get("state") != "active":
                # No wake: the note stays visible on the task, where a person or a paused bot reads it next.
                if left:
                    H.task_comment(conn, H.KEEPER, after["id"], f"Snooze over. Note: {left}", wake=False)
                continue
            if after.get("due") and H._has_table(conn, "task_reminders") and not H.not_yet_due(after, at):
                # This notice is the due date's reminder too; the Due pass would only repeat it.
                conn.execute("INSERT OR IGNORE INTO task_reminders VALUES(?,?,?)", (after["id"], after["due"], at))
            H._wake(conn, after, after["owner"], f"Snooze over: {after['title']}" + _note_line(left),
                    _wake_refs({"wake": "snooze"}, left), trigger="schedule")
            woken.append(after["id"])
    return woken

