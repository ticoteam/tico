"""Follow-up times on tasks: a person or bot sets one, and the change is recorded in the task's history.

No time wakes a bot. A due follow-up is listed once on its owner's task roundup (backend/task_roundup.py), and consume()
clears it there. A queued check-back wake from before that is obsolete when its bot claims it (stale), unless its turn
is already under way (continuing).
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

NOTE_MAX = 500
ACTIVE = ("open", "doing", "waiting", "review", "ready")
RESULT = ("done", "declined")
STATUSES = ACTIVE + RESULT


def _H():
    from . import hubdb
    return hubdb


def _event(c, task_id, actor, field, old, new, note=""):
    # Appointment bookkeeping has its own deterministic identity. Adding it must not renumber the demo's
    # conversation/message ids, whose reader-set fixtures prove that existing access does not change.
    ordinal = c.execute("SELECT coalesce(max(rowid),0)+1 FROM history_events").fetchone()[0]
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(["task-check-back", task_id, field, ordinal])))
    _H()._task_event(c, task_id, actor, field, old, new, note, event_id=event_id)


def involved(row):
    H = _H()
    return bool(row and any(H.is_bot(row.get(k)) for k in ("owner_id", "requester_id")))


def actor_for(row):
    H = _H()
    if not involved(row) or row["status"] not in STATUSES:
        return None
    if row["status"] in RESULT:
        return row["requester_id"] if H.is_bot(row["requester_id"]) and row["requester_id"] != row["owner_id"] else None
    return row["owner_id"] if H.is_bot(row["owner_id"]) else row["requester_id"]


def human_snooze(row, at):
    H = _H()
    until = row.get("snoozed_until")
    return until if until and until > at and H.is_human(row.get("snoozed_by_id")) else None


def timestamp(value, at):
    """An offset timestamp or a duration; never guess a bot's timezone."""
    H = _H()
    duration = re.fullmatch(r"([1-9][0-9]{0,5})([mhd])", str(value or ""))
    if duration:
        seconds = int(duration[1]) * {"m": 60, "h": 3600, "d": 86400}[duration[2]]
        return H.shift(at, seconds=seconds)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Follow up by an ISO date-time with timezone, or a duration such as 45m or 2h") from None


def set_time(c, row, value, note=None, *, actor=None, at=None, system=False):
    H = _H()
    from . import task_holds
    row = task_holds.with_holds(c, row)     # its snooze, when the row came from plain `tasks`
    if system and ("version" not in row or "check_back_at" not in row):
        return row  # legacy upgrade helpers create tasks before the file migrations finish
    at = at or H.now()
    actor = actor or H.KEEPER
    if not system and H.is_bot(actor) and row["status"] in RESULT:
        H.refuse(c, actor, "check_back", "This result needs a disposition: close it to accept, or reopen it with what remains. Do not park a returned result again.")
    if not involved(row) or row["status"] not in STATUSES:
        if not system:
            H.refuse(c, actor, "check_back", "Only unfinished work involving a bot needs a follow-up")
        clear(c, row)
        return H.task(c, row["id"])
    try:
        stamp = timestamp(value, at)
    except ValueError as exc:
        H.refuse(c, actor, "date", str(exc))
    if stamp <= at or not system and stamp < H.shift(at, minutes=5):
        H.refuse(c, actor, "date", "Choose a follow-up at least five minutes in the future")
    if not system and H.is_bot(actor) and stamp > H.shift(at, days=14):
        H.refuse(c, actor, "date", "A bot sets a follow-up within 14 days; use a person's dated snooze for longer")
    snooze = human_snooze(row, at)
    if snooze and stamp < snooze:
        if system:
            stamp = snooze
        else:
            H.refuse(c, actor, "snoozed", "A person snoozed this task; follow up at or after " + snooze)
    text = str(note if note is not None else row.get("check_back_note") or "Inspect the next result and continue this task.").strip()
    if len(text) > NOTE_MAX:
        H.refuse(c, actor, "check_back", "Say what to inspect in at most 500 characters")
    if (stamp, text) == (row.get("check_back_at"), row.get("check_back_note")):
        return row
    bump = int(not system and getattr(c, "building", None) != row["id"])
    c.execute("UPDATE tasks SET check_back_at=?,check_back_note=?,version=version+? WHERE id=?", (stamp, text, bump, row["id"]))
    _event(c, row["id"], actor, "check_back_at", row.get("check_back_at"), stamp, text)
    if not system and stamp != row.get("check_back_at"):
        _event(c, row["id"], actor, "check_back_explicit", None, stamp, text)
        if external_wait(c, row):
            _event(c, row["id"], actor, "check_back_external", None, stand(c, row), text)
    return H.task(c, row["id"])


def clear(c, row):
    if row.get("check_back_at") or row.get("check_back_note"):
        c.execute("UPDATE tasks SET check_back_at=NULL,check_back_note=NULL WHERE id=?", (row["id"],))


def consume(c, task_id, actor):
    """A follow-up whose time has come was listed on its owner's task roundup (backend/task_roundup.py): it is consumed,
    so its time is cleared, and the clearing is recorded."""
    row = _H().task(c, task_id)
    if row and row.get("check_back_at"):
        _event(c, task_id, actor, "check_back_at", row["check_back_at"], None, "Listed on the task roundup")
        clear(c, row)


def ensure(c, row):
    """Clears a follow-up that no longer applies: no bot is involved in the task, or it is finished. Run on every status
    change and when a task is made."""
    if "version" not in row or "check_back_at" not in row:
        return
    if not involved(row) or row["status"] not in ACTIVE:
        clear(c, row)


def view(row, at=None):
    """The follow-up a task view carries: its time, its note, who set it, and whether it has come due."""
    stamp = row.get("check_back_at")
    if not stamp:
        return None
    return {"at": stamp, "note": row.get("check_back_note") or "", "by": actor_for(row),
            "due": stamp <= (at or _H().now())}


def external_wait(c, row):
    return bool(row.get("waiting_on") or _H().recorded_wait(c, row) in ("ask", "approval", "pull_request", "milestone", "subtask"))


def stand(c, row):
    H = _H()
    base = H._stall_stand(c, row, exclude_actors=tuple(x for x in (actor_for(row),) if x))
    links = [list(r) for r in c.execute("SELECT id,state,pr_sha,pr_merged_at FROM task_links WHERE task_id=? AND kind='pr' ORDER BY id", (row["id"],))]
    human = c.execute("SELECT id FROM history_events WHERE subject_type='task' AND subject_id=? AND actor_id LIKE 'human:%' "
                      "AND kind='check_back_explicit' ORDER BY created_at DESC,rowid DESC LIMIT 1", (row["id"],)).fetchone()
    return hashlib.sha256(json.dumps([base, links, human[0] if human else None]).encode()).hexdigest()[:16]


def continuing(c, job):
    """Whether a queued job is the rest of a turn already under way: its attempt started (a drain's time limit and a shared
    login's hold cap yield one), or a person resumed it (job_recovery 'resume'). execution.obsolete_notices_sql keeps these."""
    started = c.execute("SELECT 1 FROM attempts WHERE id=? AND started_at IS NOT NULL", (job["attempt_id"],)).fetchone()
    resumed = c.execute("SELECT 1 FROM job_recovery WHERE job_id=? AND attempt_id=? AND decision='resume'",
                        (job["id"], job["attempt_id"])).fetchone()
    return started is not None or resumed is not None


def stale(c, message, job=None):
    """Whether a queued check-back wake is obsolete when its bot claims it. Tico sends none now, so every one is, except
    the rest of a turn already under way (continuing), which runs to its end."""
    H = _H()
    refs = message.get("refs") or {}
    if message.get("sender_id") != H.KEEPER or refs.get("wake") != "check_back":
        return False
    return not (job is not None and continuing(c, job))
