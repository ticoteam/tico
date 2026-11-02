"""A queued run the claim could not start is held for a back-off, with its reason, and no claim retries it before then.

The claim evaluates each candidate job and prepares the one it hands out (execution.Execution.claim). An error in
either belongs to that job alone: the claim rolls back that job's writes, logs the traceback once, and holds the job
here. A held run is retried on its own: its wait starts at a minute and doubles with each failed try, up to an hour,
and the first claim after the wait releases it to the queue (release_due). A run that starts is no longer held
(started); one that fails again is held again with a longer wait. The bot's operator gets one Needs-you item for all
its held runs, the job's task a note, and Health a finding. Releasing the bot (bot_stops.release, POST
/api/v2/bots/{bot}/release) frees its held runs at once.

An error in a bot-wide check (its budget, back-off or login hold) is not one run's: the claim passes the bot over,
and Health names the check (check_failed). None of the bot's runs is held for it. The claim does not run that check
again until a back-off has passed (check_delay), so a persistent fault costs one traceback per back-off, not one per
poll.
"""

import json
import logging

from .store import H

log = logging.getLogger(__name__)

REASON = "Couldn't start: an error while preparing this run"
RETRY_FIRST = 60            # seconds a held run waits before its first retry; doubles for each failed try in a row
RETRY_MAX = 3600            # and never longer than this
CHECK = "claim_check:"      # the service_health row of a bot whose claim check is failing
CHECK_RETRY_FIRST = 60      # seconds a bot whose check raised is passed over before the check runs again
CHECK_RETRY_MAX = 300       # doubled for each failed check in a row, and never longer than this


def delay(tries):
    """The wait after the `tries`-th failed try in a row, in seconds."""
    return min(RETRY_MAX, RETRY_FIRST * 2 ** max(0, tries - 1))


def check_delay(tries):
    """The back-off after a bot's `tries`-th failed claim check in a row, in seconds."""
    return min(CHECK_RETRY_MAX, CHECK_RETRY_FIRST * 2 ** max(0, tries - 1))


def hold(c, settings, job_id, error):
    """Hold a queued job its claim could not start: its wait, the reason, an event, its bot's Needs-you item and, on its
    first try, a note on its task. Quiet when the job is no longer queued and free, as another claim or a person may
    have moved it."""
    job = c.execute("SELECT j.bot_id,j.message_id,j.hold_streak,m.conversation_id FROM jobs j "
                    "JOIN messages m ON m.id=j.message_id WHERE j.id=? AND j.state='queued' AND j.held=0",
                    (job_id,)).fetchone()
    if not job:
        return
    bot, tries = job["bot_id"], job["hold_streak"] + 1
    retry = H.shift(H.now(), seconds=delay(tries))
    c.execute("UPDATE jobs SET held=1,hold_until=?,hold_reason=?,hold_streak=? WHERE id=?",
              (retry, REASON, tries, job_id))
    H.event(c, H.KEEPER, "job.claim_failed", job_id, {"bot": bot, "error": error, "tries": tries, "retry_at": retry})
    item = _needs_you(c, settings, bot)
    c.execute("UPDATE jobs SET hold_task_id=? WHERE id=?", (item, job_id))
    if tries == 1:
        task_id = H.message_task_id(H.message(c, job["message_id"]), H.conversation(c, job["conversation_id"]))
        if task_id:
            _note(c, task_id, bot, job_id)


def _needs_you(c, settings, bot):
    """The bot's one open Needs-you item for its held runs: the open one is reused, else a new one is filed. None when
    nobody can be asked; the runs stay held either way."""
    from . import bot_stops
    open_item = c.execute("SELECT hold_task_id FROM jobs WHERE bot_id=? AND hold_reason=? AND state='queued' "
                          "AND hold_task_id IS NOT NULL LIMIT 1", (bot, REASON)).fetchone()
    if open_item:
        task = H.task(c, open_item["hold_task_id"])
        if task and task["status"] in H.ACTIVE_STATUSES:
            return task["id"]
    name = bot_stops.name_of(c, bot)
    return bot_stops.raise_item(
        c, settings, bot, f"Couldn't start a queued run for {name}",
        f"Tico could not start a queued run for {name}: an error happened while its claim prepared it. The run is "
        "held, and Tico retries it on its own: the wait starts at a minute and doubles with each failed try, up to "
        "an hour, so it starts once the cause is fixed. The server log has the traceback, and Health names the "
        f"held runs. To retry them now, fix the cause and release {name} (hub bot release {bot}); a run that fails "
        "again is held again.")


def _note(c, task_id, bot, job_id):
    from . import bot_stops
    name = bot_stops.name_of(c, bot)
    try:
        H.task_comment(c, H.KEEPER, task_id,
                       "Tico could not start this run: an error happened while its claim prepared it, so the run is "
                       "held. Tico retries it on its own after a wait that doubles with each failed try, up to an "
                       f"hour. To retry it now, release {name} (hub bot release {bot}). The server log has the "
                       f"traceback for job {job_id}.", wake=False)
    except H.Refused:
        log.warning("Could not note the held job %s on task %s", job_id, task_id)


def started(c, job_id):
    """The claim handed the run out: its bot's check passes again, so Health stops naming it, and the run is no longer
    held."""
    row = c.execute("SELECT bot_id FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row:
        check_passed(c, row["bot_id"])
    clear(c, job_id, "Its held run started.")


def check_passed(c, bot):
    """The bot's claim check read cleanly again: Health stops naming it."""
    c.execute("DELETE FROM service_health WHERE service=?", (CHECK + bot,))


def clear(c, job_id, note="Its held run is no longer held."):
    """A held run stops being held: it started, or the claim dropped it as no longer needed. Its reason, wait and count
    go, and its Needs-you item closes once no other held run of its bot names that item."""
    from . import bot_stops
    row = c.execute("SELECT bot_id,hold_task_id FROM jobs WHERE id=? AND hold_reason=?", (job_id, REASON)).fetchone()
    if not row:
        return
    c.execute("UPDATE jobs SET held=0,hold_until=NULL,hold_reason=NULL,hold_task_id=NULL,hold_streak=0 WHERE id=?",
              (job_id,))
    item = row["hold_task_id"]
    if item and not c.execute("SELECT 1 FROM jobs WHERE bot_id=? AND hold_reason=? AND state='queued' AND hold_task_id=? "
                              "LIMIT 1", (row["bot_id"], REASON, item)).fetchone():
        bot_stops.close_item(c, item, note)


def release(c, actor, bot):
    """Free the bot's held runs now, whatever their wait, and close their Needs-you item. A held run another path
    cancelled meanwhile is freed too, so its item closes with it. Answers how many runs were freed."""
    from . import bot_stops
    rows = c.execute("SELECT id,hold_task_id FROM jobs WHERE bot_id=? AND hold_reason=?", (bot, REASON)).fetchall()
    if not rows:
        return 0
    c.execute("UPDATE jobs SET held=0,hold_until=NULL,hold_reason=NULL,hold_task_id=NULL,hold_streak=0 "
              "WHERE bot_id=? AND hold_reason=?", (bot, REASON))
    for task_id in sorted({row["hold_task_id"] for row in rows if row["hold_task_id"]}):
        bot_stops.close_item(c, task_id, "Released.")
    H.event(c, actor, "job.claim_released", bot, {"jobs": [row["id"] for row in rows]})
    return len(rows)


def release_due(c, at=None):
    """Let go the held runs whose wait has ended, so the claim may pick them. Each keeps its reason and count until it
    starts, is dropped or is released: a failed try then renews its wait instead of starting a new one."""
    c.execute("UPDATE jobs SET held=0 WHERE held=1 AND state='queued' AND hold_reason=? AND hold_until<=?",
              (REASON, at or H.now()))


def held(c):
    """The bots with runs held for this reason, one row each: how many, and since when the oldest waits. A run whose
    wait has ended stays listed until it starts, is dropped or is released."""
    return [dict(row) for row in c.execute(
        "SELECT bot_id,count(*) AS jobs,min(created_at) AS since FROM jobs WHERE hold_reason=? AND state='queued' "
        "GROUP BY bot_id ORDER BY bot_id", (REASON,))]


def check_failed(c, bot, exc):
    """A bot-wide check of its claim raised. The caller passes the bot over and calls this once per claim: the
    traceback is logged here, and Health names the bot and the error class until a run of the bot starts."""
    error = type(exc).__name__
    log.error("claim check failed for bot %s: %s", bot, error, exc_info=exc)
    c.execute("INSERT INTO service_health(service,last_succeeded_at,last_error,detail) VALUES(?,NULL,?,?) "
              "ON CONFLICT(service) DO UPDATE SET last_error=excluded.last_error,detail=excluded.detail",
              (CHECK + bot, error, json.dumps({"bot": bot, "error": error, "failed_at": H.now()})))


def check_failures(c):
    """The bots whose claim check is failing and that still have work it holds back, queued runs or open tasks of their
    own (the claim's backfill asks the same check), as {bot, error, failed_at}."""
    out = []
    for row in c.execute("SELECT h.detail FROM service_health h WHERE h.service GLOB 'claim_check:*' "
                         "AND (EXISTS (SELECT 1 FROM jobs j WHERE j.state='queued' AND ?||j.bot_id=h.service) "
                         "OR EXISTS (SELECT 1 FROM tasks t WHERE t.status='open' "
                         "AND t.owner_id='bot:'||substr(h.service,?))) "
                         "ORDER BY h.service", (CHECK, len(CHECK) + 1)):
        out.append(json.loads(row[0] or "{}"))
    return out
