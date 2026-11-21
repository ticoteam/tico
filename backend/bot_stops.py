"""The ways Tico stops a bot that is failing or running away, or one of its queued runs, and what every page says.

- `run_failures`: a bot whose runs keep failing within a minute waits between runs, then is held after five in a row;
  Tico tries one run of a held bot on its own at growing intervals and releases it when one completes.
- `turn_budgets`: a bot that starts more runs in an hour or a day than its budget is held.
- a claim error (`hold_job`): one queued run whose claim raised while preparing it waits by itself (a `claim_error`
  hold on the job), from a minute doubling to 50 minutes; the bot's other runs go on. A bot-wide claim check that
  raises passes the bot over for a back-off instead (`check_failed`, the hold on the bot), and holds none of its runs.
- can't start (`cant_start`): a computer that hands back, or drops in silence, run_diagnostics.SAME of a job's runs in a
  row: the job fails, Agent Ops gets one task per bot, and the task waits on it (a `cant_start` hold on the task).

A hold keeps the bot's queued work and starts none of it until a person releases the bot (one action, all three
holds) or, for a failed-runs hold, until Tico's own try completes. A claim hold keeps only its run. What every page
says about a hold (the status row, a sender's warning, the "Saved - waiting" label, a task's Paused cause) is read in
one place, backend/holds.py, and every hold is a `holds` row. This module files and closes the Needs-you item each hold
raises for the bot's operator (`raise_item`, `close_item`), and releases the bot (`release`).
"""

import json
import logging

from . import holds, run_diagnostics, run_failures, turn_budgets
from .store import H

log = logging.getLogger(__name__)

CLAIM_REASON = "Couldn't start: an error while preparing this run"
CANT_START_TITLE = "Find why {bot}'s runs can't start"
# One bot's open claim_error holds on its jobs (holds_bot), the Needs-you item they name first.
CLAIM_ITEM_SQL = (
    "SELECT upkeep_task_id FROM holds INDEXED BY holds_bot WHERE bot_id=? AND released_at IS NULL "
    "AND kind='claim_error' AND holdable_type='job' AND upkeep_task_id IS NOT NULL"
)


def name_of(c, bot):
    return (H.bot(c, bot) or {}).get("display_name") or bot


def raise_item(c, settings, bot, title, body):
    """One Needs-you task for the bot's operator (else the owner), and its id; None when nobody can be asked or the
    task was refused. The hold stands either way, and Health names it."""
    from . import usage_limits

    person = usage_limits.recipients(c, bot, getattr(settings, "owner_email", "") or "")
    if not person:
        return None
    try:
        from . import person_asks

        return person_asks.task_for(
            c,
            H.TICO_ACTOR,
            title,
            body,
            H.human_actor(person["id"]),
            question=f"Can you get {H.actor_name(c, H.bot_actor(bot))} running again?",
            why=person_asks.first_sentence(body),
            asker=person_asks.agent_ops(),
        )["id"]
    except H.Refused:
        log.warning("Could not file the Needs-you task for the stopped bot %s", bot)
        return None


def close_item(c, task_id, note):
    """Close the Needs-you task a hold filed, once the bot is released. Quiet when it is already closed."""
    row = H.task(c, task_id) if task_id else None
    if not row or row["status"] != "open":
        return
    if H.attempt(c, H.task_close, H.TICO_ACTOR, task_id, note) is None:
        log.warning("Could not close the Needs-you task %s", task_id)


def release(c, actor, bot):
    """Release the bot from every stop it has: the failed-runs hold and its back-off, the budget hold, and the jobs its
    claim could not start (`hold_job`). Queued work starts again. Answers what was cleared:
    {"run_hold": bool, "budget": bool, "claim": the number of jobs released}."""
    return {
        "run_hold": run_failures.release(c, actor, bot),
        "budget": turn_budgets.release(c, actor, bot),
        "claim": release_claims(c, actor, bot),
    }


def hold_job(c, settings, job_id, error):
    """A queued job its claim could not start waits by itself (its `claim_error` hold): from a minute, doubling with
    each failed try in a row up to 50 minutes (holds.KINDS), with an event, its bot's one Needs-you item and, on its
    first try, a note on its task. The hold stays open past its wait, keeping the count, until the run starts, is
    dropped or is released. Quiet when the job is no longer queued."""
    job = c.execute(
        "SELECT j.bot_id,j.message_id,m.conversation_id FROM jobs j JOIN messages m ON m.id=j.message_id "
        "WHERE j.id=? AND j.state='queued'",
        (job_id,),
    ).fetchone()
    if not job:
        return
    bot = job["bot_id"]
    was = c.execute(holds.OPEN_SQL, (holds.CLAIM_ERROR, "job", job_id, bot)).fetchone()
    tries = (was["streak"] if was else 0) + 1
    retry = H.shift(H.now(), seconds=holds.wait(holds.CLAIM_ERROR, tries))
    item = _claim_item(c, settings, bot)
    holds.put(
        c,
        holds.CLAIM_ERROR,
        "job",
        job_id,
        actor=H.TICO_ACTOR,
        bot_id=bot,
        reason=CLAIM_REASON,
        details={"error": error},
        streak=tries,
        until_at=retry,
        upkeep_task_id=item,
    )
    H.event(
        c, H.TICO_ACTOR, "job.claim_failed", job_id, {"bot": bot, "error": error, "tries": tries, "retry_at": retry}
    )
    if tries == 1:
        task_id = H.message_task_id(H.message(c, job["message_id"]), H.conversation(c, job["conversation_id"]))
        if task_id:
            try:
                H.task_comment(
                    c,
                    H.TICO_ACTOR,
                    task_id,
                    "Tico could not start this run: an error happened while its claim prepared it, so the run is "
                    "held. Tico retries it on its own after a wait that doubles with each failed try, up to an "
                    f"hour. To retry it now, release {name_of(c, bot)} (tico bot release {bot}). The server log has "
                    f"the traceback for job {job_id}.",
                    wake=False,
                )
            except H.Refused:
                log.warning("Could not note the held job %s on task %s", job_id, task_id)


def _claim_item(c, settings, bot):
    """The bot's one open Needs-you item for its held runs: the open one is reused, else a new one is filed. None when
    nobody can be asked; the runs wait either way."""
    for row in c.execute(CLAIM_ITEM_SQL, (bot,)).fetchall():
        task = H.task(c, row["upkeep_task_id"])
        if task and task["status"] in H.ACTIVE_STATUSES:
            return task["id"]
    name = name_of(c, bot)
    return raise_item(
        c,
        settings,
        bot,
        f"Couldn't start a queued run for {name}",
        f"Tico could not start a queued run for {name}: an error happened while its claim prepared it. The run is "
        "held, and Tico retries it on its own: the wait starts at a minute and doubles with each failed try, up to "
        "50 minutes, so it starts once the cause is fixed. The server log has the traceback, and Health names the "
        f"held runs. To retry them now, fix the cause and release {name} (tico bot release {bot}); a run that fails "
        "again is held again.",
    )


def clear_job(c, hold, note, how="moot"):
    """A claim's held run stops being held (`hold`, its claim_error row): it started (`how` probe_passed), or it is no
    longer needed. An open hold is released; one its run's end released already (holds_job_ends) is not. Either way its
    Needs-you item closes once no other held run of its bot names it."""
    if hold["released_at"] is None:
        holds.release(
            c, holds.CLAIM_ERROR, "job", hold["holdable_id"], actor=H.TICO_ACTOR, how=how, bot_id=hold["bot_id"]
        )
    item = hold["upkeep_task_id"]
    if item and item not in {r[0] for r in c.execute(CLAIM_ITEM_SQL, (hold["bot_id"],)).fetchall()}:
        close_item(c, item, note)


# A job's claim_error hold: its open one, else its last released one (a run that ends releases its hold before its
# caller clears it: holds_job_ends). One statement, a seek of each partial index; none when the job never had one.
JOB_CLAIM_SQL = (
    "SELECT * FROM (SELECT * FROM holds INDEXED BY holds_open WHERE kind='claim_error' AND holdable_type='job' "
    "AND holdable_id=?1 AND released_at IS NULL) UNION ALL SELECT * FROM (SELECT * FROM holds INDEXED BY holds_released "
    "WHERE holdable_type='job' AND holdable_id=?1 AND kind='claim_error' AND released_at IS NOT NULL "
    "ORDER BY released_at DESC LIMIT 1) LIMIT 1"
)


def clear_claim(c, job_id, note):
    """`clear_job` for a job dropped by id, before or after it ended, when it held a claim_error: one read when not."""
    row = c.execute(JOB_CLAIM_SQL, (job_id,)).fetchone()
    if row:
        clear_job(c, row, note)


def release_claims(c, actor, bot):
    """Free the bot's held runs now, whatever their wait, its claim check's back-off with them, and close their
    Needs-you item, and the item of a held run that ended meanwhile (its hold ended with it: holds_job_ends). Answers
    how many runs were freed."""
    rows = c.execute(
        "UPDATE holds INDEXED BY holds_bot SET released_at=?2,released_by_id=?3,released_how='person',updated_at=?2 "
        "WHERE bot_id=?1 AND released_at IS NULL AND kind='claim_error' RETURNING holdable_type,holdable_id",
        (bot, H.now(), actor),
    ).fetchall()
    items = c.execute(
        "SELECT DISTINCT h.upkeep_task_id FROM holds h JOIN tasks t ON t.id=h.upkeep_task_id AND t.status='open' "
        "WHERE h.bot_id=? AND h.kind='claim_error' ORDER BY 1",
        (bot,),
    ).fetchall()
    for item in items:
        close_item(c, item[0], "Released.")
    jobs = sorted(r["holdable_id"] for r in rows if r["holdable_type"] == "job")
    if jobs:
        H.event(c, actor, "job.claim_released", bot, {"jobs": jobs})
    return len(jobs)


def check_failed(c, bot, exc, was=None):
    """A bot-wide check of its claim raised: the claim passes the bot over until its back-off ends (a `claim_error`
    hold on the bot, from a minute doubling to five: holds.CLAIM_CHECK; `was` its open one). The traceback is logged
    here, once per back-off, and Health names the bot and the error class until a run of the bot starts."""
    error = type(exc).__name__
    log.error("claim check failed for bot %s: %s", bot, error, exc_info=exc)
    tries = (was["streak"] if was else 0) + 1
    holds.put(
        c,
        holds.CLAIM_ERROR,
        "bot",
        bot,
        actor=H.TICO_ACTOR,
        reason=f"Its claim check raised {error}",
        details={"error": error, "failed_at": H.now()},
        streak=tries,
        until_at=H.shift(H.now(), seconds=holds.wait(holds.CLAIM_CHECK, tries)),
    )


def claim_holds(c):
    """For Health: the bots with runs a claim error holds, one row each (how many, and since when the oldest waits),
    and the bots whose claim check is failing and that still have work it holds back, queued runs or open tasks of
    their own (the claim's backfill asks the same check), as {bot, error, failed_at}. A run whose wait has ended stays
    listed until it starts, is dropped or is released."""
    held = [
        dict(row)
        for row in c.execute(
            "SELECT h.bot_id,count(*) AS jobs,min(h.created_at) AS since FROM holds h INDEXED BY holds_open "
            "WHERE h.kind='claim_error' AND h.holdable_type='job' AND h.released_at IS NULL GROUP BY h.bot_id "
            "ORDER BY h.bot_id"
        )
    ]
    checks = [
        {"bot": row["holdable_id"], **json.loads(row["details"] or "{}")}
        for row in c.execute(
            "SELECT h.holdable_id,h.details FROM holds h INDEXED BY holds_open WHERE h.kind='claim_error' "
            "AND h.holdable_type='bot' AND h.released_at IS NULL "
            "AND (EXISTS (SELECT 1 FROM jobs j WHERE j.bot_id=h.holdable_id AND j.state='queued') "
            "OR EXISTS (SELECT 1 FROM tasks t WHERE t.owner_id='bot:'||h.holdable_id AND t.status='open')) "
            "ORDER BY h.holdable_id"
        )
    ]
    return held, checks


def cant_start(c, row, why, computer, lapses=0):
    """Give Agent Ops the bot's runs that cannot start: one open task per bot, a comment on it naming each further task,
    and a `cant_start` hold on this run's task, kept until Agent Ops's task closes. Answers Agent Ops's task id, or None when
    there is no Agent Ops to give it to (the bot is Agent Ops, or the company has none): then the job still fails, and the
    task is not held. `lapses` is how many runs in a row its computer took and went silent on (execution.expire), when
    that, not run_diagnostics.SAME hand-backs, is why."""
    maintainer = H.FLEET_MAINTAINER
    bot = row["bot_id"]
    if bot == maintainer or not H.bot(c, maintainer):
        return None
    info = H.bot(c, bot) or {}
    label = info.get("slug") or bot
    name = info.get("display_name") or label
    owner = H.bot_actor(maintainer)
    title = CANT_START_TITLE.format(bot=label)[:200]
    task = _run_task(c, row)
    if task and task["status"] != "open":
        task = None
    if not task:
        about = "its work"
    elif H.task_private_readable(c, owner, task):
        about = f'the task "{task["title"]}" ({task["id"]})'
    else:
        about = "a task you cannot read"
    said = (
        f"{name}'s computer ({computer}) took its last {lapses} runs on {about} and never started them: {why}"
        if lapses
        else f"{name}'s computer ({computer}) handed back its last {run_diagnostics.SAME} runs on {about} before "
        f"starting them, each time saying: {why}"
    )
    review = c.execute(
        f"SELECT id FROM tasks WHERE owner_id=? AND requester_id=? AND title=? AND status='open' AND {H.TASK_SAYS_SQL} "
        "ORDER BY created_at DESC LIMIT 1",
        (owner, H.TICO_ACTOR, title, H.BOT_REVIEW_ID.format(id=bot)),
    ).fetchone()
    try:
        if review:
            review_id = review["id"]
            held = task and c.execute(holds.OPEN_SQL, (holds.CANT_START, "task", task["id"], bot)).fetchone()
            if not held or held["upkeep_task_id"] != review_id:
                H.task_comment(c, H.TICO_ACTOR, review_id, said + ". Tico stopped retrying it too.")
        else:
            made = H.task_create(
                c,
                H.TICO_ACTOR,
                title,
                f"{said}.\n\nTico stopped retrying: a wait will not clear this. The task waits on {name}, paused, "
                f"until you close this one. Fix the cause on {name}'s computer or in its repository (its Runs show "
                f"each run's reason), then close this task: Tico wakes {name} on its work again, and files here again "
                "if it still cannot start.\n\n" + H.BOT_REVIEW_ID.format(id=bot),
                owner,
                lint=False,
                deduplicate=False,
            )
            review_id = made["id"]
    except H.Refused:
        return None
    if task:
        holds.put(
            c,
            holds.CANT_START,
            "task",
            task["id"],
            actor=H.TICO_ACTOR,
            bot_id=bot,
            reason=why,
            details={"job": row["job_id"], "computer": computer},
            upkeep_task_id=review_id,
        )
    return review_id


def _run_task(c, row):
    task_id = row["task_id"]
    if not task_id and row["job_id"]:
        from .task_wakes import message_task

        job = c.execute("SELECT message_id FROM jobs WHERE id=?", (row["job_id"],)).fetchone()
        task_id = job and message_task(c, job["message_id"])
    return H.task(c, task_id) if task_id else None
