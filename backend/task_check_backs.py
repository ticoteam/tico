"""Durable appointments to inspect bot work, on the existing message/job/attempt queue.

The ledger proves a wake was written, not that its result was handled. A dropped wake or a completed turn that
leaves no next appointment is repaired with backoff. Human-only work keeps its existing reminders.
"""
import json
import hashlib
import os
import re
import uuid
from datetime import datetime, timezone

from .batch_work import isolated

KEY = "task_check_backs"
LIMIT = 50
NOTE_MAX = 500
FIRST_WAKES_PER_HOUR = max(1, int(os.environ.get("TICO_TASK_CHECK_BACKS_PER_BOT_HOUR", "12")))
FLEET_FIRST_WAKES_PER_HOUR = max(1, int(os.environ.get("TICO_TASK_CHECK_BACKS_FLEET_HOUR", "50")))
WAKES_PER_BOT_HOUR = max(1, int(os.environ.get("TICO_TASK_CHECK_BACKS_WAKES_PER_BOT_HOUR", "24")))  # first and repeat wakes
WAKES_PER_TASK_DAY = 12
REACHABLE_SECONDS = 90  # a computer heard from this recently can take a start: execution.company_waiter's bar
INFLIGHT_JOBS = ("queued", "held", "leased", "running", "uncertain")
# Live receipts only, for the dedupe: a notice's job not yet handled, or a run still going that took its input.
LIVE_JOBS = INFLIGHT_JOBS + ("input",)
LIVE_RUNS = ("leased", "running", "input", "uncertain")
ACTIVE =("open", "doing", "waiting", "review", "ready")
RESULT = ("done", "declined")
STATUSES = ACTIVE + RESULT
PREDICATE = ("(status IN ('open','doing','waiting','review','ready') OR "
             "status IN ('done','declined') AND requester_id LIKE 'bot:%' AND requester_id<>owner_id AND check_back_at IS NOT NULL)")


def _H():
    from . import hubdb
    return hubdb


def _event(c, task_id, actor, field, old, new, note=""):
    # Appointment bookkeeping has its own deterministic identity. Adding it must not renumber the demo's
    # conversation/message ids, whose reader-set fixtures prove that existing access does not change.
    ordinal = c.execute("SELECT coalesce(max(rowid),0)+1 FROM history_events").fetchone()[0]
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(["task-check-back", task_id, field, ordinal])))
    _H()._task_event(c, task_id, actor, field, old, new, note, event_id=event_id)


def enabled(c):
    from . import read_cache, system_routines
    return read_cache.value(c, KEY + ":enabled", lambda: system_routines.config_of(c, KEY)["on"], shared=True)


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


def default_time(c, row, at, stagger=False):
    H = _H()
    from . import quiet_work
    resume = quiet_work.condition(row) or {}  # resume and due already served as legacy check-back dates
    values = [row.get("snoozed_until"), resume.get("until"), row.get("due_at")]
    if H._has_table(c, "deferrals"):
        deferred = c.execute("SELECT min(due_at) FROM deferrals WHERE task_id=? AND settled_at IS NULL AND due_at>?",
                             (row["id"], at)).fetchone()[0]
        values.append(deferred)
    future = []
    for value in values:
        try:
            candidate = timestamp(value, at)
        except ValueError:
            continue  # old date-only or malformed deadlines cannot supply an unambiguous appointment
        if candidate > at:
            future.append(candidate)
    if future:
        return min(future)
    # Check children share the step's timeout rather than adding a second 30-minute timer.
    parent = H.task(c, H.TR.parent_of(c, row["id"]) or "")
    if parent:
        step = c.execute("SELECT * FROM task_steps WHERE id=?", (parent.get("step_id"),)).fetchone()
        hours = (H._json(dict(step).get("flow"), {}) or {}).get("timeout_hours") if step else None
        if isinstance(hours, (int, float)) and hours > 0:
            return H.shift(at, seconds=int(hours * 3600))
    seconds = int(hashlib.sha256(row["id"].encode()).hexdigest()[:8], 16) % 3600 if stagger else 0
    return H.shift(at, minutes=30, seconds=seconds)


def ensure(c, row, at=None):
    H = _H()
    if "version" not in row or "check_back_at" not in row:
        return
    at = at or H.now()
    if not involved(row) or row["status"] not in STATUSES:
        clear(c, row)
        return
    if row["status"] in RESULT:
        if not enabled(c):
            clear(c, row)
            return  # results returned during shadow mode belong to the historical audit, not an enable-time queue
        consumer = actor_for(row)
        parent = H.task(c, H.TR.parent_of(c, row["id"]) or "")
        if not consumer or parent and parent["status"] in ACTIVE and actor_for(parent) == consumer:
            clear(c, row)
            return
    if not row.get("check_back_at"):
        set_time(c, row, default_time(c, row, at), system=True, at=at)


def moved(c, row, at=None):
    """Every status path, including keeper moves, reconciles the same appointment."""
    H = _H()
    at = at or H.now()
    ensure(c, row, at)
    if row["status"] not in RESULT or not enabled(c):
        return
    consumer = actor_for(row)
    if not consumer:
        clear(c, H.task(c, row["id"]))  # human acceptance uses its existing question; self-work is finished
        return
    parent = H.task(c, H.TR.parent_of(c, row["id"]) or "")
    target = parent if parent and parent["status"] in ACTIVE and actor_for(parent) == consumer else row
    now = H.shift(at, seconds=5)
    until = target.get("check_back_at")
    set_time(c, target, min(until, now) if until and until > at else now,
             "Inspect the returned result and record its disposition.", system=True, at=at)
    if target["id"] != row["id"]:
        clear(c, H.task(c, row["id"]))


def pending(row, at=None):
    return bool(row and row.get("check_back_at") and row["check_back_at"] > (at or _H().now()))


def view(row, c=None, at=None):
    stamp = row.get("check_back_at")
    if not stamp:
        return None
    due = stamp <= (at or _H().now())
    out = {"at": stamp, "note": row.get("check_back_note") or "", "by": actor_for(row), "due": due}
    if c is not None:
        out["enabled"] = enabled(c)
        if not out["enabled"]:
            return out
        blocked = c.execute("SELECT old,new FROM history_events WHERE subject_type='task' AND subject_id=? "
                            "AND kind='check_back_blocked' ORDER BY created_at DESC,rowid DESC LIMIT 1", (row["id"],)).fetchone()
        if blocked and blocked["old"] == stand(c, row):
            out["delivery"] = blocked["new"]
    if c is not None and due:
        bot = _H().bot(c, _H().actor_id(actor_for(row) or ""))
        record = c.execute("SELECT * FROM task_check_back_wakes WHERE task_id=? AND check_back_at=?",
                            (row["id"], stamp)).fetchone()
        if record or "delivery" not in out:
            out["delivery"] = _delivery(c, record) if record else "pending"
        if not enabled(c):
            out["delivery"] = "disabled"
        elif not bot or bot.get("state") != "active":
            out["delivery"] = "bot_unavailable"
    return out


def coverage(c, visible_sql="1", at=None):
    """Shadow counts do not send anything or imply that an old result was consumed."""
    at = at or _H().now()
    row = c.execute("SELECT "
                    "sum(CASE WHEN status IN ('open','doing','waiting','review','ready') AND check_back_at IS NULL THEN 1 ELSE 0 END) missing, "
                    "sum(CASE WHEN check_back_at<? THEN 1 ELSE 0 END) overdue, "
                    "sum(CASE WHEN status IN ('done','declined') AND requester_id<>owner_id THEN 1 ELSE 0 END) results "
                    "FROM tasks WHERE status IN ('open','doing','waiting','review','ready','done','declined') "
                    "AND (owner_id LIKE 'bot:%' OR requester_id LIKE 'bot:%') AND (" + visible_sql + ")", (at,)).fetchone()
    return {key: row[key] or 0 for key in ("missing", "overdue", "results")}


def context(c, row, recipient):
    """Bounded, recipient-readable evidence; absence never proves an unreadable child does not exist."""
    H = _H()
    if row["status"] in RESULT:
        return ("A delegated result needs your disposition. Read the result and verify its evidence.\n"
                "Accept completed work with `hub task close " + row["id"] + "`, or reopen it with a concrete note about what remains.\n"
                "For a declined result, decide whether to close it or reopen/reassign it. Reading or commenting alone does not consume it.\n"
                "Do not set another follow-up on the returned result; put remaining work on an actionable task with its own date.")
    lines = ["Follow up on this task now.", "Expected: " + (row.get("check_back_note") or "inspect the next result"),
             "Inspect the actual result or blocker, repair routine handoffs within your authority, and continue.",
             "Before waiting again, verify the assignment/reservation exists and set a future follow-up with",
             "`hub task check-back " + row["id"] + " --in 45m --for <what to inspect>`. A status note is not a handoff."]
    lines.append("Checkpoint: " + str(row.get("note") or "none recorded")[:400])
    if row.get("due_at"):
        lines.append("Delivery deadline: " + row["due_at"])
    from . import quiet_work
    resume = quiet_work.condition(row)
    if resume:
        lines.append("Deferred production work: " + quiet_work.describe(c, resume)[:250]
                     + ". Inspect now; retain this production wait unless its condition is met or explicitly changed.")
    links = c.execute("SELECT url,state FROM task_links WHERE task_id=? AND kind='pr' LIMIT 8", (row["id"],)).fetchall()
    lines.append("Linked pull requests: " + (", ".join((x["state"] or "unknown") + " " + x["url"][:180] for x in links) if links else "none"))
    children = c.execute("SELECT t.* FROM tasks t JOIN task_relations r ON r.from_task=t.id "
                         "WHERE r.to_task=? AND r.kind='parent' ORDER BY t.created_at LIMIT 9", (row["id"],)).fetchall()
    shown = [dict(x) for x in children[:8] if H.task_private_readable(c, recipient, dict(x))]
    lines.append("Readable subtasks: " + (str(len(shown)) if shown else "none; unreadable work may exist. Verify routing before filing a duplicate."))
    for child in shown:
        name = ("#" + str(child["number"])) if child.get("number") else child["id"][:8]
        job = c.execute("SELECT j.state FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "WHERE json_extract(m.refs,'$.task')=? AND m.recipient_id=? ORDER BY j.created_at DESC LIMIT 1",
                         (child["id"], child["owner_id"])).fetchone()
        lines.append(f"  {name}: {child['status']} ({child['owner_id']}), latest delivery: {job[0] if job else 'none recorded'}")
    if H._has_table(c, "check_waits"):
        wait = c.execute("SELECT reason FROM check_waits WHERE ticket_id=? AND state='waiting' LIMIT 1", (row["id"],)).fetchone()
        lines.append("Check reservation: " + (str(wait[0])[:300] if wait else "none"))
    if row.get("waiting_on"):
        lines.append("Recorded wait: " + str(row["waiting_on"])[:200] + ". Follow the existing ask rules; do not ask again just because this timer fired.")
    return "\n".join(lines)[:3500]


def _delivery(c, record):
    if not record or not record["message_id"]:
        return "missing"
    job = c.execute("SELECT state FROM jobs WHERE message_id=?", (record["message_id"],)).fetchone()
    if job and job[0] in INFLIGHT_JOBS:
        return job[0]  # a retry can be queued after an earlier attempt failed
    # Inputs are the receipt, including a check-back joined into a previously queued run.
    received = c.execute("SELECT a.state,a.created_at FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE j.message_id=? "
                         "UNION ALL SELECT a.state,a.created_at FROM attempt_inputs i JOIN attempts a ON a.id=i.attempt_id "
                         "WHERE i.message_id=? ORDER BY created_at DESC LIMIT 1", (record["message_id"], record["message_id"])).fetchone()
    if received:
        return received[0]
    return job[0] if job else "missing"


# One set-based probe of the live receipts: a job not yet handled, or a live run that took the notice as input. Keyed
# by the ledger's (task_id, check_back_at) primary key, the message's primary key, jobs' unique message_id and
# attempt_inputs' message_id key, so completed history is never read.
IN_FLIGHT_SQL = ("SELECT 1 FROM task_check_back_wakes w JOIN messages m ON m.id=w.message_id "
                 "WHERE w.task_id=? AND m.recipient_id=? AND (EXISTS (SELECT 1 FROM jobs j WHERE j.message_id=w.message_id "
                 f"AND j.state IN ({','.join('?' * len(LIVE_JOBS))})) OR EXISTS (SELECT 1 FROM attempt_inputs i "
                 "JOIN attempts a ON a.id=i.attempt_id WHERE i.message_id=w.message_id "
                 f"AND a.state IN ({','.join('?' * len(LIVE_RUNS))}))) LIMIT 1")


def _notice_in_flight(c, task_id, target):
    """Whether a check-back notice for this task, to this bot, is still queued, held, leased, running or uncertain, or
    a run still going took it. Any due time that moves while one waits (a snooze end, a deferral, a repair) must not
    queue a second one."""
    return c.execute(IN_FLIGHT_SQL, (task_id, target, *LIVE_JOBS, *LIVE_RUNS)).fetchone() is not None


def _cancel_moved(c, row, target):
    """Cancel this task's queued notices to this bot that no longer match the task (`stale`): its due time moved while
    one waited. A claim cancels those, but only on a computer that wakes, and a stopped cloud machine is not started for
    an obsolete notice (cloud_machines.bot_want). Left queued, it would hold the dedupe and nothing would be sent. A
    notice a run already took is live, and stays, as does one a run started and yielded on."""
    from .execution import suppress_notice
    H = _H()
    queued = c.execute("SELECT j.id,j.bot_id,j.attempt_id,j.message_id FROM task_check_back_wakes w "
                       "JOIN messages m ON m.id=w.message_id JOIN jobs j ON j.message_id=m.id "
                       "WHERE w.task_id=? AND m.recipient_id=? AND j.state='queued' "
                       "AND NOT EXISTS (SELECT 1 FROM job_recovery r WHERE r.job_id=j.id AND r.attempt_id=j.attempt_id "
                       "AND r.decision='resume') "
                       # A run that started and yielded continues on this job (execution.obsolete_notices_sql): keep it.
                       "AND NOT EXISTS (SELECT 1 FROM attempts ya WHERE ya.id=j.attempt_id AND ya.started_at IS NOT NULL "
                       "AND ya.state='interrupted' AND json_extract(ya.result,'$.yielded'))", (row["id"], target)).fetchall()
    for job in queued:
        if stale(c, H.message(c, job["message_id"]), job["bot_id"]):
            suppress_notice(c, job, row["id"], "Check-back moved before its notice was delivered")


def _computer_out(c, bot, at):
    """Whether the bot's assigned computer should take no new notice now.

    A cloud machine sleeps when idle and is started by queued work for its bot (cloud_machines.bot_want), so its silence
    never gates: the notice queues and wakes it. Only its memory gates, and only while it is running and reporting.
    Any other computer is out while silent past REACHABLE_SECONDS (asleep, offline), or while it holds new starts for a
    stale sample (start_places.computer_hold). Memory is read on its own (start_places.memory_held): at 90%+ the computer
    is also overloaded, and computer_hold then reports only "computer busy". CPU overload alone is not held here, and a
    bot with no assigned computer is not gated."""
    from . import cloud_machines, start_places
    H = _H()
    place = c.execute("SELECT r.id,r.last_seen_at FROM assignments a JOIN runners_with_state r ON r.id=a.runner_id WHERE a.bot=?",
                      (bot,)).fetchone()
    if not place:
        return False
    reporting = bool(place["last_seen_at"]) and place["last_seen_at"] >= H.shift(at, seconds=-REACHABLE_SECONDS)
    machine = cloud_machines.home(c, bot)
    if machine is not None:
        if machine["state"] != "running" or not reporting:
            return False  # a stopped or starting machine takes the notice in its queue, and its want starts it
        return start_places.memory_held(c, place["id"])  # reads the real clock, as every other caller does
    if not reporting:
        return True
    hold = start_places.computer_hold(c, place["id"])
    return (bool(hold) and hold[0] == start_places.NOT_REPORTING) or start_places.memory_held(c, place["id"])


def stand(c, row):
    H = _H()
    base = H._stall_stand(c, row, exclude_actors=tuple(x for x in (actor_for(row),) if x))
    links = [list(r) for r in c.execute("SELECT id,state,pr_sha,pr_merged_at FROM task_links WHERE task_id=? AND kind='pr' ORDER BY id", (row["id"],))]
    human = c.execute("SELECT id FROM history_events WHERE subject_type='task' AND subject_id=? AND actor_id LIKE 'human:%' "
                      "AND kind='check_back_explicit' ORDER BY created_at DESC,rowid DESC LIMIT 1", (row["id"],)).fetchone()
    return hashlib.sha256(json.dumps([base, links, human[0] if human else None]).encode()).hexdigest()[:16]


def blocked(c, row, cause):
    state = stand(c, row)
    previous = c.execute("SELECT old,new FROM history_events WHERE subject_type='task' AND subject_id=? "
                         "AND kind='check_back_blocked' ORDER BY created_at DESC,rowid DESC LIMIT 1", (row["id"],)).fetchone()
    if not previous or tuple(previous) != (state, cause):
        _event(c, row["id"], _H().KEEPER, "check_back_blocked", state, cause)


def external_wait(c, row):
    return bool(row.get("waiting_on") or _H().recorded_wait(c, row) in ("ask", "approval", "pull_request", "milestone", "subtask"))


def _repairs(c, row):
    H = _H()
    state = stand(c, row)
    recent = c.execute("SELECT kind AS field,new FROM history_events WHERE subject_type='task' AND subject_id=? "
                       "AND kind='check_back_repair' ORDER BY created_at DESC,rowid DESC LIMIT 12", (row["id"],)).fetchall()
    count = 0
    for r in recent:
        if (H._json(r["new"], {}) or {}).get("stand") != state:
            break
        count += 1
    return count, state


def _unchanged_wakes(c, row):
    state = stand(c, row)
    count = 0
    for r in c.execute("SELECT kind AS field,new FROM history_events WHERE subject_type='task' AND subject_id=? "
                       "AND kind IN ('check_back_wake','check_back_external') "
                       "ORDER BY created_at DESC,rowid DESC LIMIT 13", (row["id"],)):
        if r["field"] == "check_back_external" or r["new"] != state:
            break
        count += 1
    return count


def explicit(c, row):
    """Whether a person or bot set the current due time itself; the keeper's defaults, repairs and re-parks are not."""
    last = c.execute("SELECT actor_id AS actor FROM history_events WHERE subject_type='task' AND subject_id=? "
                     "AND kind='check_back_at' ORDER BY created_at DESC,rowid DESC LIMIT 1", (row["id"],)).fetchone()
    return bool(last) and last["actor"] != _H().KEEPER


def changed(c, row):
    """Whether anyone but the bot moved the task since its last delivered check-back wake. No recorded wake counts as
    changed, and so does a notice a moved due time canceled before delivery: the bot never received it. A message on the
    task counts too, even a comment: stand() skips the owner's messages, and a comment writes no task event."""
    H = _H()
    seen = c.execute("SELECT created_at,new FROM history_events WHERE subject_type='task' AND subject_id=? "
                     "AND kind='check_back_wake' ORDER BY created_at DESC,rowid DESC LIMIT 1", (row["id"],)).fetchone()
    if not seen or seen["new"] != stand(c, row):
        return True
    last = c.execute("SELECT j.state FROM task_check_back_wakes w JOIN jobs j ON j.message_id=w.message_id "
                     "WHERE w.task_id=? ORDER BY w.sent_at DESC LIMIT 1", (row["id"],)).fetchone()
    if last and last["state"] == "canceled":
        return True
    if c.execute("SELECT 1 FROM messages WHERE conversation_id=? AND created_at>=? AND deleted_at IS NULL "
                 "AND sender_id NOT IN (?,?) LIMIT 1", (row["conversation_id"], seen["created_at"], actor_for(row), H.KEEPER)).fetchone():
        return True
    return bool(c.execute("SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND created_at>=? "
                          "AND kind NOT LIKE 'check_back%' AND actor_id IS NOT ? LIMIT 1",
                          (row["id"], seen["created_at"], actor_for(row))).fetchone())


def quiet(c, row, at):
    """A keeper-set repeat with nothing new: check later on a doubling back-off from 30 minutes, capped at the 8h repair
    cap, keeping the expected note. It is not a repair, so it never feeds the stall count that escalates work nothing moves."""
    H = _H()
    state = stand(c, row)
    count = 0
    for r in c.execute("SELECT kind AS field,new FROM history_events WHERE subject_type='task' AND subject_id=? "
                       "AND kind IN ('check_back_quiet','check_back_wake') ORDER BY created_at DESC,rowid DESC LIMIT 12", (row["id"],)):
        if r["field"] == "check_back_wake" or r["new"] != state:
            break
        count += 1
    set_time(c, row, H.shift(at, minutes=min(30 * 2 ** min(count, 4), 480)), system=True, at=at)
    _event(c, row["id"], H.KEEPER, "check_back_quiet", None, state, "Nothing moved since the last check-back")


def repair(c, row, at):
    H = _H()
    count, state = _repairs(c, row)
    delay = min(30 * 2 ** min(count, 4), 480)
    recovery = None
    if max(count, _unchanged_wakes(c, row)) >= H.STALL_WAKES_PER_DAY and not external_wait(c, row):
        escalated = c.execute("SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? "
                              "AND kind='check_back_escalated' AND new=? LIMIT 1",
                              (row["id"], state)).fetchone()
        if not escalated and not H._no_diagnosis(c, row, count):
            recovery = H._escalate_stuck(c, row, max(count, _unchanged_wakes(c, row)))
            _event(c, row["id"], H.KEEPER, "check_back_escalated", None, state, "Recovery routed once for this unchanged work")
        else:
            # A private task or a diagnostic cannot safely create another diagnostic. Its debt remains visible.
            blocked(c, row, "recovery_blocked")
    until = H.shift(at, minutes=delay)
    if recovery:
        ensure(c, recovery, at)
        until = H.task(c, recovery["id"]).get("check_back_at") or until
    after = set_time(c, row, until, "Recovery follow-up: inspect the unresolved work and its handoff.", system=True, at=at)
    _event(c, row["id"], H.KEEPER, "check_back_repair", None,
                  json.dumps({"stand": state, "count": count + 1, "recovery_task": recovery["id"] if recovery else None}), "")
    return after


def _recovery(c, row):
    H = _H()
    event = c.execute("SELECT detail FROM events WHERE target=? AND action='task.stall_escalated' ORDER BY rowid DESC LIMIT 1",
                      (row["id"],)).fetchone()
    recovery = H.task(c, (H._json(event[0], {}) or {}).get("recovery_task") or "") if event else None
    return recovery if recovery and recovery["status"] in ACTIVE else None


def candidates(c, at):
    H = _H()
    cursor_row = c.execute("SELECT value FROM registry_metadata WHERE key='task_check_back_scan'").fetchone()
    cursor = H._json(cursor_row[0], ["", ""]) if cursor_row else ["", ""]
    query = (f"SELECT * FROM tasks WHERE {PREDICATE} AND (check_back_at<=? OR check_back_at IS NULL) "
             "AND (owner_id LIKE 'bot:%' OR requester_id LIKE 'bot:%') "
             "AND (coalesce(check_back_at,''),id)>(?,?) ORDER BY check_back_at,id LIMIT ?")
    rows = c.execute(query, (at, *cursor, LIMIT)).fetchall()
    if not rows and cursor != ["", ""]:
        rows = c.execute(query, (at, "", "", LIMIT)).fetchall()
    next_cursor = [rows[-1]["check_back_at"] or "", rows[-1]["id"]] if rows else ["", ""]
    return rows, next_cursor


def run_pass(store, at, failures=None):
    result = {"woke": [], "repaired": [], "blocked": []}
    with store.read_transaction() as c:
        if not enabled(c):
            return result
        rows, cursor = candidates(c, at)
    for original in rows:
        with store.transaction() as c:
            checked = sweep(c, at, failures, task_ids=[original["id"]])
        for key in result:
            result[key].extend(checked[key])
    with store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('task_check_back_scan',?)", (json.dumps(cursor),))
        c.execute("DELETE FROM task_check_back_wakes WHERE rowid IN (SELECT w.rowid FROM task_check_back_wakes w "
                  "JOIN tasks t ON t.id=w.task_id WHERE w.sent_at<? AND w.check_back_at IS NOT t.check_back_at "
                  "AND EXISTS (SELECT 1 FROM task_check_back_wakes first WHERE first.task_id=w.task_id AND first.sent_at<w.sent_at) LIMIT ?)",
                  (_H().shift(at, days=-30), LIMIT))
    return result


def sweep(c, at=None, failures=None, task_ids=None):
    H = _H()
    at = at or H.now()
    result = {"woke": [], "repaired": [], "blocked": []}
    if not enabled(c):
        return result
    if task_ids is None:
        rows, cursor = candidates(c, at)
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('task_check_back_scan',?)", (json.dumps(cursor),))
    else:
        rows = [{"id": tid} for tid in task_ids]
    for original in rows:
        with isolated(c, "check_back", original["id"], failures):
            row = H.task(c, original["id"])
            if not row or row["status"] not in STATUSES or row.get("check_back_at") and row["check_back_at"] > at:
                continue  # candidates were read outside this transaction
            target = actor_for(row)
            if not target:
                clear(c, row)
                continue
            snooze = human_snooze(row, at)
            if snooze:
                set_time(c, row, snooze, system=True, at=at)
                continue
            if not row.get("check_back_at"):
                # Preserve valid legacy waits; stagger overdue work instead of waking an old fleet at once.
                if row["status"] in RESULT:
                    continue  # historical results remain auditable; enabling never generates consumption work
                else:
                    planned = default_time(c, row, at, stagger=True)
                    row = set_time(c, row, planned,
                                   "Imported follow-up: inspect the current handoff.", system=True, at=at)
                _event(c, row["id"], H.KEEPER, "check_back_imported", None, row["check_back_at"], "Legacy appointment coverage")
                result["repaired"].append(row["id"])
                continue
            bot = H.bot(c, H.actor_id(target))
            if not bot or bot.get("state") != "active":
                # Pausing is deliberate, not a failed follow-up. Keep debt due without spending retries while paused.
                result["blocked"].append(row["id"])
                continue
            _cancel_moved(c, row, target)  # before the dedupe: a moved notice must not hold it and strand the follow-up
            if _notice_in_flight(c, row["id"], target):
                continue  # one notice per task and bot: the queued one carries this inspection until it is handled
            if _computer_out(c, bot["id"], at):
                # A silent or memory-held computer takes no notice; the due time waits for it instead of piling up.
                blocked(c, row, "computer_unavailable")
                result["blocked"].append(row["id"])
                continue
            record = c.execute("SELECT * FROM task_check_back_wakes WHERE task_id=? AND check_back_at=?",
                                (row["id"], row["check_back_at"])).fetchone()
            if record:
                repair(c, row, at)
                result["repaired"].append(row["id"])
                continue
            recovery = _recovery(c, row)
            if recovery:
                ensure(c, recovery, at)
                set_time(c, row, max(H.shift(at, minutes=30), H.task(c, recovery["id"]).get("check_back_at") or at), system=True, at=at)
                result["blocked"].append(row["id"])
                continue
            count, _ = _repairs(c, row)
            if max(count, _unchanged_wakes(c, row)) >= H.STALL_WAKES_PER_DAY and not external_wait(c, row):
                repair(c, row, at)
                result["blocked"].append(row["id"])
                continue
            daily = c.execute("SELECT count(*) FROM history_events WHERE subject_type='task' AND subject_id=? "
                              "AND kind='check_back_wake' AND created_at>=?",
                              (row["id"], H.shift(at, days=-1))).fetchone()[0]
            if daily >= WAKES_PER_TASK_DAY:
                blocked(c, row, "daily_inspection_cap")
                result["blocked"].append(row["id"])
                continue
            if not H._actor_exists(c, target) or not H.task_private_readable(c, target, row):
                repair(c, row, at)
                result["blocked"].append(row["id"])
                continue
            first = not c.execute("SELECT 1 FROM task_check_back_wakes WHERE task_id=? LIMIT 1", (row["id"],)).fetchone()
            if not first and not explicit(c, row) and not changed(c, row):
                quiet(c, row, at)  # keeper-set and nothing moved: back off, without counting as a repair
                continue
            if first:
                firsts = c.execute("SELECT count(*) FROM task_check_back_wakes w JOIN messages m ON m.id=w.message_id "
                                   "WHERE w.sent_at>=? AND m.recipient_id=? AND NOT EXISTS "
                                   "(SELECT 1 FROM task_check_back_wakes older WHERE older.task_id=w.task_id AND older.sent_at<w.sent_at)",
                                   (H.shift(at, hours=-1), target)).fetchone()[0]
                fleet = c.execute("SELECT count(DISTINCT task_id) FROM task_check_back_wakes WHERE sent_at>=? "
                                  "AND NOT EXISTS (SELECT 1 FROM task_check_back_wakes older WHERE older.task_id=task_check_back_wakes.task_id "
                                  "AND older.sent_at<task_check_back_wakes.sent_at)", (H.shift(at, hours=-1),)).fetchone()[0]
                if firsts >= FIRST_WAKES_PER_HOUR or fleet >= FLEET_FIRST_WAKES_PER_HOUR:
                    blocked(c, row, "hourly_first_wake_cap")
                    result["blocked"].append(row["id"])
                    continue  # retain visible due debt; a later pass owns delivery
            hourly = c.execute("SELECT count(*) FROM task_check_back_wakes w JOIN messages m ON m.id=w.message_id "
                               "WHERE w.sent_at>=? AND m.recipient_id=?", (H.shift(at, hours=-1), target)).fetchone()[0]
            if hourly >= WAKES_PER_BOT_HOUR:
                blocked(c, row, "hourly_wake_cap")
                result["blocked"].append(row["id"])
                continue  # retain visible due debt; a later pass owns delivery
            message = H._wake(c, row, target, context(c, row, target),
                              {"wake": "check_back", "check_back_at": row["check_back_at"], "delivery": "queue"},
                              trigger="check_back")
            if message:
                _event(c, row["id"], H.KEEPER, "check_back_wake", None, stand(c, row), "Scheduled inspection delivered")
                c.execute("INSERT INTO task_check_back_wakes VALUES(?,?,?,?)",
                          (row["id"], row["check_back_at"], at, message["id"]))
                if row.get("due_at") and row["due_at"] <= at:
                    c.execute("INSERT OR IGNORE INTO task_reminders VALUES(?,?,?)", (row["id"], row["due_at"], at))
                result["woke"].append(row["id"])
    return result


def finished_turn(c, aid, bot, task_id, at=None):
    H = _H()
    if not enabled(c):
        return
    at = at or H.now()
    tids = {task_id} if task_id else set()
    tids.update(r[0] for r in c.execute("SELECT id FROM tasks WHERE carried_by=? LIMIT 100", (aid,)))
    tids.update(r[0] for r in c.execute("SELECT json_extract(m.refs,'$.task') FROM attempt_inputs i "
                                       "JOIN messages m ON m.id=i.message_id JOIN jobs j ON j.message_id=m.id WHERE i.attempt_id=? "
                                       "AND j.state='completed' AND json_extract(m.refs,'$.wake')='check_back' LIMIT 100", (aid,)))
    deferred = {r[0] for r in c.execute("SELECT json_extract(m.refs,'$.task') FROM attempt_inputs i JOIN messages m ON m.id=i.message_id "
                                      "JOIN jobs j ON j.message_id=m.id WHERE i.attempt_id=? AND j.state='queued' "
                                      "AND json_extract(m.refs,'$.wake')='check_back' LIMIT 100", (aid,))}
    tids.difference_update(deferred - {task_id})
    for tid in tids - {None}:
        row = H.task(c, tid)
        if not row or actor_for(row) != "bot:" + bot:
            continue
        if not row.get("check_back_at") or row["check_back_at"] <= at:
            repair(c, row, at)


def stale(c, message, bot):
    H = _H()
    refs = message.get("refs") or {}
    if message.get("sender_id") != H.KEEPER or refs.get("wake") != "check_back":
        return False
    row = H.task(c, refs.get("task") or "")
    return (not row or not enabled(c) or row.get("check_back_at") != refs.get("check_back_at")
            or actor_for(row) != "bot:" + bot or bool(human_snooze(row, H.now())))


def fold_snooze(c, row, body, refs, at):
    """One snooze-end wake carries both promises, with an ordinary check-back delivery receipt."""
    if (enabled(c) and actor_for(row) == row["owner_id"]
            and row.get("check_back_at") and row["check_back_at"] <= at):
        return body + "\n\n" + context(c, row, row["owner_id"]), {
            **refs, "wake": "check_back", "check_back_at": row["check_back_at"], "delivery": "queue"}
    return body, refs


def record_fold(c, row, message, refs, at):
    if message and refs.get("wake") == "check_back":
        c.execute("INSERT OR IGNORE INTO task_check_back_wakes VALUES(?,?,?,?)",
                  (row["id"], refs["check_back_at"], at, message["id"]))
