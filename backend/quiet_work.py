"""Quiet work stays visible and resumes (Dev 257).

A next-run task (`hub task create --next-run`) waits for its owner's next run, which carries it in its prompt and
marks it with `carried_by` (execution.claim). Until this, the carrier finishing took it for good, whether or not the
run did anything with it: a run that said "after Dev 241" in its own words left nothing to bring it back (Dev 242,
Oct 19: seven hours, until a person pressed Run now). Now:

- A quiet task is in one of three states (`view`, on the task and in `hub task show`): `queued`, a run of its own
  is waiting or going; `eligible`, the bot's next run carries it; `delivered`, a run carried it (which run, when),
  with `resume` when that run stored when to pick it up again.
- A run that carries a task it will not do now stores a resume condition (`defer`, `hub task defer`): after another
  task is finished (done or closed), at a time, or with the next run. A stored condition holds the task until it
  comes true; then the task gets one run of its own, as Run now gives it (`resume`).
- When a carrier finishes, each task it carried is settled (`settle`): one the run worked (a comment, a step,
  status or note change, a pull request, or `hub task defer --handled`) stays delivered; one it deferred keeps its
  condition; any other goes back to eligible, with a line in its history, and the next run carries it again.
- Health Monitor: a quiet task delivered more than two hours ago and not worked since, with no condition, is a
  finding naming the bot and the task (`stale`).

No second scheduler: the minute pass that ends snoozes ends time conditions (scheduler.py), and a finished task frees
the tasks deferred until it where it frees the ones it blocked (H._unblock).

This module imports nothing from hubdb at load, so hubdb can import it.
"""

import json

from .batch_work import isolated

CARRIED = "task.next-run.carried"
SETTLED = "task.next-run.settled"
STALE_HOURS = 2
MAX_DAYS = 730
# The task_events field each outcome is written under, so a task's own history says what became of each carriage.
FIELD = "quiet"
WORKED, RETURNED, DEFERRED, HANDLED = "worked", "returned", "deferred", "handled"
# What a carried task tells the run, beside its id and title (execution.claim): how to say "not now".
HINT = ("Not doing it in this run? Store when to pick it up: hub task defer {id} --after <task id> (when that task is "
        "done), --at <ISO time>, or --next-run (MCP: hub_task_defer). With none, and without working it here, your "
        "next run is handed it again.")


def _H():
    from . import hubdb
    return hubdb


def condition(row):
    """The task's stored resume condition (a dict), or None."""
    try:
        value = json.loads((row or {}).get("resume_json") or "null")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def describe(conn, cond):
    """A resume condition in a few words: "after <title> is done", "at 2026-10-19 18:00 UTC". A private task is named
    by its number or short id, never its title."""
    H = _H()
    if not cond:
        return ""
    if cond.get("kind") == "task":
        other = H.task(conn, cond.get("task") or "")
        name = (_task_name(other) if H.task_private(conn, other) else other["title"]) if other \
            else str(cond.get("task") or "")[:8]
        return f"after {name} is done"
    return f"at {str(cond.get('until') or '')[:16].replace('T', ' ')} UTC"


def _task_name(row):
    """How a task is named where its title may not be shown: its number in its type, else its short id."""
    if row.get("number") and row.get("type_id"):
        return f"#{row['number']} ({row['id'][:8]})"
    return row["id"][:8]


# ------------------------------------------------------------------------------------------------ what it is now
def _own_job(conn, row):
    """The run of its own a task has waiting or going: (job id, attempt id or None, created), or None."""
    H = _H()
    try:
        found = conn.execute(
            "SELECT j.id, j.attempt_id, j.created, j.state FROM jobs j JOIN messages m ON m.id=j.message_id "
            "JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE j.bot=? AND j.state IN ('queued','leased','running') AND {H.MESSAGE_TASK_SQL}=? "
            "ORDER BY j.created LIMIT 1", (H.actor_id(row["owner"]), row["id"])).fetchone()
    except Exception:  # noqa: BLE001 - a local hub has no jobs table
        return None
    return found


def carried_at(conn, aid, task_id):
    """When run `aid` was handed task `task_id` (its claim's carried event), or None."""
    try:
        for r in conn.execute("SELECT ts, detail_json FROM events WHERE action=? AND target=? ORDER BY ts",
                              (CARRIED, aid)):
            if task_id in (json.loads(r["detail_json"] or "{}").get("tasks") or []):
                return r["ts"]
    except Exception:  # noqa: BLE001 - fail quiet: the view only loses the time
        return None
    return None


def _attempt(conn, aid):
    try:
        return conn.execute("SELECT id, bot, state, started, created, finished FROM attempts WHERE id=?",
                            (aid,)).fetchone()
    except Exception:  # noqa: BLE001 - a local hub has no attempts table
        return None


def _last(conn, task_id, outcomes):
    marks = ",".join("?" * len(outcomes))
    return conn.execute(f"SELECT ts, old, new FROM task_events WHERE task_id=? AND field=? AND new IN ({marks}) "
                        "ORDER BY ts DESC LIMIT 1", (task_id, FIELD, *outcomes)).fetchone()


def was_quiet(conn, task_id):
    """Whether this task ever was a quiet one: a Run now or a resume moved it to a run of its own."""
    return conn.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='next_run' AND old='1' LIMIT 1",
                        (task_id,)).fetchone() is not None


def view(conn, row, history=True):
    """Where a quiet task stands, for its view: None for an ordinary task. `history` also reads a task that was quiet
    until a Run now or a resume gave it a run of its own (one more indexed read; a list leaves it out)."""
    H = _H()
    if not row or not H.is_bot(row.get("owner")) or row.get("status") not in H.ACTIVE_STATUSES:
        return None
    quiet = bool(row.get("next_run"))
    if not quiet and not (history and was_quiet(conn, row["id"])):
        return None
    job = _own_job(conn, row)
    if job:
        return {"state": "queued", "job": job["id"], "run": job["attempt_id"], "since": job["created"],
                "words": "A run of its own is " + ("going" if job["state"] != "queued" else "waiting") + "."}
    if not quiet:
        return None
    cond = condition(row)
    aid = row.get("carried_by")
    if cond:
        run = cond.get("run")
        return {"state": "delivered", "run": run, "at": carried_at(conn, run, row["id"]) if run else None,
                "resume": cond, "words": "Picked up again " + describe(conn, cond) + "."}
    if H.next_run_waiting(conn, row):
        back = _last(conn, row["id"], (RETURNED,))
        out = {"state": "eligible", "words": "The bot's next run carries it."}
        if back and aid is None:
            out["returned_from"] = back["old"]
        return out
    at = carried_at(conn, aid, row["id"])
    attempt = _attempt(conn, aid)
    live = bool(attempt and attempt["state"] in ("leased", "running"))
    out = {"state": "delivered", "run": aid, "at": at, "running": live}
    if live:
        out["words"] = "Carried into a run that is still going."
    elif _last(conn, row["id"], (WORKED, HANDLED)):
        out["worked"] = True
        out["words"] = "Carried into a run, which worked it."
    else:
        out["worked"] = False
        out["words"] = "Carried into a run that left it, with no time set to pick it up."
    return out


# ------------------------------------------------------------------------------------------------ the carrier ends
def worked(conn, row, bot, since, until=None):
    """Whether bot `bot` (a slug) worked task `row` between `since` and `until`: a comment or message in its room, a
    change to its fields (step, status, note, ...), a link it added, or a pull request on the task opened or pushed
    to. A link refreshed from GitHub (checks, mergeable, review) is not work."""
    actor = "bot:" + bot
    until = until or "9999"
    if conn.execute("SELECT 1 FROM task_events WHERE task_id=? AND actor=? AND ts>=? AND ts<=? AND field<>? LIMIT 1",
                    (row["id"], actor, since, until, FIELD)).fetchone():
        return True
    if row.get("conversation_id") and conn.execute(
            "SELECT 1 FROM messages WHERE conversation_id=? AND from_actor=? AND created>=? AND created<=? LIMIT 1",
            (row["conversation_id"], actor, since, until)).fetchone():
        return True
    if conn.execute("SELECT 1 FROM events WHERE action='github.pull_request' AND target=? AND ts>=? AND ts<=? "
                    "AND json_extract(detail_json,'$.action') IN ('opened','synchronize') LIMIT 1",
                    (row["id"], since, until)).fetchone():
        return True
    try:
        return conn.execute("SELECT 1 FROM task_links WHERE task_id=? AND added_by=? AND created>=? AND created<=? "
                            "LIMIT 1", (row["id"], actor, since, until)).fetchone() is not None
    except Exception:  # noqa: BLE001 - a local hub has no task_links table
        return False


def settle(conn, aid, bot, started):
    """Run `aid` of `bot` finished: settle each open quiet task it still holds. Worked: stays delivered. Deferred:
    keeps its condition. Anything else: back to eligible, said in its history. Returns {task id: outcome}."""
    H = _H()
    out = {}
    for row in H._rows(conn.execute("SELECT * FROM tasks WHERE carried_by=? AND next_run=1 AND status='open'", (aid,))):
        if condition(row):
            outcome = DEFERRED
        elif _last_since(conn, row["id"], HANDLED, started) or worked(conn, row, bot, started):
            outcome = WORKED
        else:
            outcome = RETURNED
            conn.execute("UPDATE tasks SET carried_by=NULL WHERE id=?", (row["id"],))
        H._task_event(conn, row["id"], H.KEEPER, FIELD, aid, outcome,
                      "back to the next run: the run that carried it left it, with no time set to pick it up"
                      if outcome == RETURNED else "")
        out[row["id"]] = outcome
    if out:
        H.event(conn, H.KEEPER, SETTLED, aid, {"bot": bot, "tasks": out})
    return out


def _last_since(conn, task_id, outcome, since):
    return conn.execute("SELECT 1 FROM task_events WHERE task_id=? AND field=? AND new=? AND ts>=? LIMIT 1",
                        (task_id, FIELD, outcome, since)).fetchone() is not None


# ------------------------------------------------------------------------------------------------ "not now"
def defer(conn, actor, task_id, after=None, at=None, next_run=False, handled=False):
    """Store when a quiet task carried into a run (or waiting for one) is picked up again: `after` a task is done or
    closed, `at` a time, or with the `next_run`. `handled` says the run is done with it for now (it stays delivered).
    The owner bot or a person. Returns the task."""
    H = _H()
    from . import task_snooze
    H._writer(conn, actor)
    row = H.task(conn, task_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no task {task_id}")
    H._task_private_writer(conn, actor, row)
    if H.is_bot(actor) and actor != row["owner"]:
        H.refuse(conn, actor, "identity", "only the task's own bot, or a person, says when it is picked up again")
    if sum(bool(x) for x in (after, at, next_run, handled)) != 1:
        H.refuse(conn, actor, "defer", "give one of --after <task>, --at <time>, --next-run or --handled")
    if not row.get("next_run") or row["status"] != "open":
        H.refuse(conn, actor, "defer", "only an open quiet task (filed for the bot's next run) is deferred; "
                                       "snooze or set waiting on any other")
    aid = row.get("carried_by")
    if handled:
        if not aid:
            H.refuse(conn, actor, "defer", "no run has carried it yet, so there is nothing to have handled")
        if row.get("resume_json"):
            # Done with it for now: a condition stored earlier no longer brings it back.
            conn.execute("UPDATE tasks SET resume_json=NULL, updated=? WHERE id=?", (H.now(), task_id))
        H._task_event(conn, task_id, actor, FIELD, aid, HANDLED, "")
        return H.task(conn, task_id)
    was = condition(row)
    if next_run:
        conn.execute("UPDATE tasks SET resume_json=NULL, carried_by=NULL, updated=? WHERE id=?", (H.now(), task_id))
        H._task_event(conn, task_id, actor, FIELD, aid, RETURNED, "back to the next run")
        H.event(conn, actor, "task.next-run.deferred", task_id, {"next_run": True, "run": aid})
        return H.task(conn, task_id)
    cond = {"run": aid, "by": actor, "set": H.now()}
    if after:
        other = H.task(conn, after)
        if not other or not H.task_private_readable(conn, actor, other):
            H.refuse(conn, actor, "not-found", f"no task {after}")
        if other["id"] == task_id:
            H.refuse(conn, actor, "defer", "a task cannot wait for itself")
        if other["status"] not in H.ACTIVE_STATUSES:
            H.refuse(conn, actor, "defer", f"that task is already {other['status']}: work this one now")
        cond.update(kind="task", task=other["id"])
    else:
        stamp = task_snooze.parse_until(at)
        if not stamp:
            H.refuse(conn, actor, "date", "defer until an ISO-8601 date (2026-11-02) or date-time")
        if stamp <= H.now():
            H.refuse(conn, actor, "date", "defer until a moment in the future")
        if stamp > H.shift(H.now(), days=MAX_DAYS):
            H.refuse(conn, actor, "date", f"defer for at most {MAX_DAYS} days; close it if nobody will")
        cond.update(kind="time", until=stamp)
    conn.execute("UPDATE tasks SET resume_json=?, updated=? WHERE id=?", (json.dumps(cond), H.now(), task_id))
    H._task_event(conn, task_id, actor, FIELD, aid, DEFERRED, describe(conn, cond))
    H.event(conn, actor, "task.next-run.deferred", task_id,
            {k: cond.get(k) for k in ("kind", "task", "until", "run")} | ({"was": was} if was else {}))
    return H.task(conn, task_id)


# ------------------------------------------------------------------------------------------------ resuming
def resume(conn, row, why, trigger):
    """The condition came true: the task gets one run of its own, as Run now gives it, unless one already waits."""
    H = _H()
    conn.execute("UPDATE tasks SET resume_json=NULL, next_run=0, updated=? WHERE id=?", (H.now(), row["id"]))
    H._task_event(conn, row["id"], H.KEEPER, "next_run", "1", "0", "resume: " + why)
    after = H.task(conn, row["id"])
    if not _own_job(conn, after):
        H._wake(conn, after, after["owner"], f"Resume: {after['title']}\n{why}",
                {"wake": "resume", "resume": why}, trigger=trigger)
    return after


def finished(conn, done_task):
    """`done_task` is done or closed: each open quiet task deferred until it resumes. Returns their ids."""
    H = _H()
    out = []
    for row in H._rows(conn.execute("SELECT * FROM tasks WHERE resume_json IS NOT NULL AND next_run=1 "
                                    "AND status='open' AND json_extract(resume_json,'$.task')=?", (done_task["id"],))):
        name = _task_name(done_task) if H.task_private(conn, done_task) and not H.task_private(conn, row) \
            else done_task["title"]
        resume(conn, row, f"{name} is {done_task['status']}.", "status_or_relation")
        out.append(row["id"])
    return out


# Read through hubdb's tasks_resume_due (TASKS_RESUME_INDEX), whose predicate is this one's, word for word.
DUE_SQL = ("SELECT * FROM tasks WHERE resume_json IS NOT NULL AND next_run=1 "
           "AND status='open' AND json_extract(resume_json,'$.until')<=? "
           "ORDER BY json_extract(resume_json,'$.until'), id")


def due(conn, at, failures=None):
    """The scheduler's pass: each open quiet task whose resume time has come gets its run. Returns their ids."""
    H = _H()
    out = []
    for row in H._rows(conn.execute(DUE_SQL, (at,))):
        with isolated(conn, "resume", row["id"], failures):
            resume(conn, row, "The time it was deferred to came: " + describe(conn, condition(row)) + ".", "schedule")
            out.append(row["id"])
    return out


# ------------------------------------------------------------------------------------------------ Health
def stale(conn, at=None, hours=STALE_HOURS):
    """Quiet tasks handed to a run more than `hours` ago and not worked since, with no condition: [{task, bot, since}].
    Only active bots' open tasks; the oldest unworked carriage counts, so a task handed back again and again is one."""
    H = _H()
    at = at or H.now()
    cutoff = H.shift(at, seconds=-hours * 3600)
    out = []
    try:
        rows = H._rows(conn.execute(
            "SELECT t.* FROM tasks t JOIN bots b ON t.owner='bot:'||b.slug WHERE b.state='active' AND t.next_run=1 "
            "AND t.status='open' AND t.resume_json IS NULL AND t.snoozed_until IS NULL "
            "AND (t.carried_by IS NOT NULL OR EXISTS (SELECT 1 FROM task_events e WHERE e.task_id=t.id "
            "AND e.field=? AND e.new=?))", (FIELD, RETURNED)))
    except Exception:  # noqa: BLE001 - a local hub without these tables has no runs
        return []
    for row in rows:
        bot = H.actor_id(row["owner"])
        last_work = _last(conn, row["id"], (WORKED, HANDLED, DEFERRED))
        floor = last_work["ts"] if last_work else ""
        first = conn.execute("SELECT old FROM task_events WHERE task_id=? AND field=? AND new=? AND ts>? "
                             "ORDER BY ts LIMIT 1", (row["id"], FIELD, RETURNED, floor)).fetchone()
        aid = first["old"] if first else row.get("carried_by")
        since = carried_at(conn, aid, row["id"]) if aid else None
        if not since or since > cutoff:
            continue
        if not first:
            # Carried and not settled: a run still going, or one that ended before runs were settled. Either may
            # have worked it, or said it is done with it, already.
            attempt = _attempt(conn, aid)
            going = not attempt or attempt["state"] in ("leased", "running")
            if _last_since(conn, row["id"], HANDLED, since) or worked(
                    conn, row, bot, since, at if going else attempt["finished"] or at):
                continue
        out.append({"task": row, "bot": bot, "since": since, "run": aid})
    return out
