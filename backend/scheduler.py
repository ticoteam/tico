"""Deterministic, transactionally deduplicated scheduling independent of local runners."""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from croniter import croniter

from . import goals as G
from . import pipeline_manager
from . import placement, run_triggers
from . import system_routines as SR
from .batch_work import isolated
from .store import H, encode
from . import sources as SRC

if TYPE_CHECKING:
    from .heartbeat import Context

LOG = logging.getLogger("tico.scheduler")


def stamp(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def next_due(expr, at, zone):
    # Preserve the existing keeper's AND semantics for day-of-month and day-of-week.
    return croniter(expr, at.astimezone(ZoneInfo(zone)), day_or=False).get_next(datetime)


REMINDER_BATCH = 50


def remind(c, row, at):
    """Whether a task from H.tasks_due_for_bots gets its due-date reminder now."""
    due = H.parse_ts(row.get("due_at"))
    if due and due.tzinfo is None:
        # Legacy rows allowed dates/local timestamps. New API writes require an
        # offset; preserve the old machine's Pacific interpretation on migration.
        due = due.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
    if not due or due > at or not row["owner_id"].startswith("bot:"):
        return False
    if c.execute("SELECT 1 FROM task_reminders WHERE task_id=? AND due_at=?", (row["id"], row["due_at"])).fetchone():
        return False
    return H.bot(c, H.actor_id(row["owner_id"]))["state"] == "active"


# The controlled system routines' blocks (backend/system_routines.py CONTROLS), shared by the scheduler and Run now: a
# manual run goes through the same code, so the same limits.
def run_kpi_asks(store, auth, at, failures=None):
    # A KPI that went stale as time passed becomes its owner's task here (backend/goal_asks.py). Its own transaction: a
    # failure here never undoes the goal colours.
    with SR.recording(store, "kpi_asks", failures, "goal_asks") as run:
        from . import goal_asks

        with store.transaction() as c:
            found = goal_asks.sync(c, auth.owner_id(c), at, goal_asks.readers(c, auth))
        run.did(
            SR.words_of(
                {"asked": (found or {}).get("created") or [], "closed": (found or {}).get("closed") or []},
                none="Nothing to ask",
            )
        )


def run_ask_deadlines(store, auth, at, failures=None):
    # A bot's ask past its deadline goes ahead on its default, or expires (backend/ask_deadlines.py). A Heartbeat step
    # (backend/heartbeat.py): returns its (words, quiet); a raise is its failure, which the beat records.
    from . import ask_deadlines

    with store.transaction() as c:
        done = ask_deadlines.sweep(c, stamp(at))
    return SR.words_of(done, "ask settled", "No asks past their deadline"), SR.nothing(done)


def run_task_manager_report(store, auth, at, failures=None):
    # One report per department, built in plain code (backend/task_manager.py). Nothing to report is a quiet run: no task
    # and no model call. A department with something gets one "Task check" task for the Task Manager.
    with SR.recording(store, "task_manager_report", failures, "task_manager") as run:
        from . import task_manager

        with store.transaction() as c:
            made = task_manager.report(c, auth, store.settings, stamp(at))
        run.did(made["words"], quiet=not made["items"])


def run_invariants(store, auth, at, failures=None):
    # Plain SQL over the hub (backend/invariants.py): no model. Reads take no write lock; a run is recorded only when a
    # check changed, inside the one write transaction that changes it. A quiet sweep writes nothing.
    from . import invariants

    run = SR.Run("invariant_checks")

    def record(c, counts):
        firing = [key for key, n in counts.items() if n]
        SR.keep(c, run.did(f"{len(firing)} failing: {', '.join(firing)}" if firing else "All checks pass"))

    try:
        with SRC.writing_as("check-health"):
            invariants.sweep(store, auth, store.settings, at, record=record)
    except Exception as exc:
        run.error = SR.failure_words(exc)
        if failures is None:
            raise
        failures.append({"invariants": type(exc).__name__})
        SR.write(store, run)


def run_bookkeeper_check(store, auth, at, failures=None):
    # The Bookkeeper's Finance work queue, read in plain code (backend/bookkeeper.py). Nothing new is a quiet run: no task
    # and no model call. Something new opens or updates one "Books check" task for the Bookkeeper.
    with SR.recording(store, "bookkeeper_check", failures, "bookkeeper") as run:
        from . import bookkeeper

        with store.transaction() as c:
            made = bookkeeper.check(c, auth, stamp(at))
        run.did(made["words"], quiet=not made["items"])


def run_bookkeeper_watch(store, auth, at, failures=None):
    # The Bookkeeper's Watch (backend/bookkeeper.py watch): runway, unusual spend, duplicate charges and price increases,
    # read in plain code. Nothing new is a quiet run; something new opens or updates one "Watch" task for the Bookkeeper.
    with SR.recording(store, "bookkeeper_watch", failures, "bookkeeper") as run:
        from . import bookkeeper

        with store.transaction() as c:
            made = bookkeeper.watch(c, auth, stamp(at))
        run.did(made["words"], quiet=not made["items"])


def run_bookkeeper_rules_digest(store, auth, at, failures=None):
    # The weekly digest of posting rules made that week (backend/bookkeeper.py rules_digest). No new rule is a quiet run.
    with SR.recording(store, "bookkeeper_rules_digest", failures, "bookkeeper") as run:
        from . import bookkeeper

        with store.transaction() as c:
            made = bookkeeper.rules_digest(c, auth, stamp(at))
        run.did(made["words"], quiet=not made["items"])


def run_thread_digest(store, auth, at, failures=None):
    # One email an hour to each person with threads to list on digest (backend/thread_digest.py). Nothing unread is a
    # quiet run: no mail and no write. A Heartbeat step: returns its (words, quiet).
    from . import thread_digest

    sent = thread_digest.sweep(store, thread_digest.sender_for(auth))
    return SR.words_of(sent, "digest", "No digests to send"), SR.nothing(sent)


def run_thread_bot_digest(store, auth, at, failures=None):
    # A bot on digest gets one run an hour for what its threads gathered (backend/thread_bot_digest.py). Plain code: a
    # model runs only when a batch has something in it. A Heartbeat step: returns its (words, quiet).
    from . import thread_bot_digest

    woken = thread_bot_digest.sweep(store)
    return SR.words_of(woken, "bot", "No bot digests"), SR.nothing(woken)


def run_skill_usage_rollup(store, auth, at, failures=None):
    # Skill uses folded into daily counts and raw uses past 90 days deleted (backend/skill_usage.py). Plain code;
    # nothing new is a quiet run.
    from . import skill_usage

    with SR.recording(store, "skill_usage_rollup", failures, "skills") as run:
        said = skill_usage.words(skill_usage.run(store, at))
        run.did(said or "Nothing new", quiet=not said)


def run_thread_retention(store, auth, at, failures=None):
    # Threads that keep their messages for a set time lose what is past it (backend/thread_retention.py), in short
    # batches. Plain code; nothing past its time is a quiet run.
    from . import thread_retention

    with SR.recording(store, "thread_retention", failures, "helper") as run:
        purged = thread_retention.sweep(store)
        run.did(SR.words_of(purged, "message", "Nothing past its time"), quiet=not purged)


# Ask go-ahead (ask_deadlines) and the two thread digests are Heartbeat steps (backend/heartbeat.py STEPS), on the same
# switch, preset and hour; the beat times them from their last start.
QUICK = {
    "kpi_asks": run_kpi_asks,
    "task_manager_report": run_task_manager_report,
    "bookkeeper_check": run_bookkeeper_check,
}

# The Bookkeeper's Watch (daily) and rules digest (weekly): their own cadence, not controlled jobs (CONTROLS has the
# a person switches). The Bookkeeper's pause is their off switch.
FIXED = {
    "bookkeeper_watch": (run_bookkeeper_watch, timedelta(days=1)),
    "bookkeeper_rules_digest": (run_bookkeeper_rules_digest, timedelta(days=7)),
    "thread_retention": (run_thread_retention, timedelta(minutes=15)),
    "skill_usage_rollup": (run_skill_usage_rollup, timedelta(hours=1)),
}


def run_now(store, auth, key, actor):
    """Run now (Settings > Routines): one controlled job, once. A plain-code job runs here and its run record is on its
    row when this returns (a failure is recorded, not raised); stale ticket review starts on its own thread. Learn
    runs through Maintenance. Returns whether background work started. Raises a 409 when stale review is running."""
    from .store import Problem

    at = datetime.now(timezone.utc)
    from . import heartbeat

    if key in QUICK:
        QUICK[key](store, auth, at, [])
        running = False
    elif key in heartbeat.CONTROLLED:
        heartbeat.run_now(store, auth, key)  # a Heartbeat step's block once, its outcome on its step row
        running = False
    elif key == "stale_review":
        from . import stale_review

        if store.settings.rehearsal:
            raise Problem("conflict", "A rehearsal server runs no stale ticket review", 409)
        if stale_review.tick(store, stamp(at), force=True) is None:
            raise Problem("conflict", "A stale ticket review is already running, or it was just switched off", 409)
        running = True
    elif key == "learnings":
        from . import maintenance

        _, running = maintenance.run_now(store, auth, actor, step="learn")
    else:
        raise Problem("invalid", "Nothing to run", 422)
    with store.transaction() as c:
        H.event(c, actor, "system_routines.run_now", key, {"running": running})
    return running


# ----------------------------------------------------------------------------- the Heartbeat's steps from the tick
# Each was a recorded block of Scheduler.tick; backend/heartbeat.py STEPS runs them, in this order, before the rest of
# the tick. Moved as they were: each returns its (words, quiet), and a raise is its failure, which the beat records,
# adds to the pass's `failures` and sends to telemetry as the tick's raise was.
def step_stall_marks(ctx):
    # Stall marks and the server's run cap first, in their own short write, every minute: a slow step below must not
    # keep a silent run unmarked, and a run past its limit lapses here, before plain expiry looks at it.
    with ctx.locked("stall_marks") as c:
        from . import run_diagnostics

        run_diagnostics.sweep(c, stamp(ctx.at), listening_since=ctx.scheduler.listening_since)
    return "Checked", True


def step_turn_fixer(ctx):
    # The turn fixer first: a start it proves dead is freed with a note on its task before plain expiry takes it.
    # Fleet classification for the Health finding runs on a read connection between two short writes, so claim
    # renewals and events are not parked behind every queued bot's refusal check (TK / #1654 residual).
    from . import turn_fixer

    freed, incident = turn_fixer.run_split(ctx.execution, ctx.settings, ctx.locked, ctx.store.read)
    return turn_fixer.words(freed, incident), not freed


def step_run_housekeeping(ctx):
    # Lease expiry and the per-tick run steps are one job, Run housekeeping.
    store, execution, locked = ctx.store, ctx.execution, ctx.locked
    from . import wake_presets

    def wake_presets_tick(c):
        wake_presets.tick(c, H.now())

    def placement_sweep(c):
        placement.sweep(c, execution)

    def notes_catch_up(c):
        # A completed run's notes are settled where its result is recorded; this settles any that path missed.
        H.notes_settle_catch_up(c, 500)

    def source_outages_tick(c):
        from . import source_outages

        source_outages.sync(c, store.settings)

    def company_turns_tick(c):
        from . import org_turns

        org_turns.observe(c, store.settings, detailed=False)

    with store.read() as c:
        from . import start_places

        expired = [
            row[0]
            for row in c.execute(
                "SELECT id FROM attempts WHERE state IN ('leased','running') AND lease_until<=? ORDER BY lease_until,id",
                (H.now(),),
            )
        ]
        expired = list(dict.fromkeys([*expired, *start_places.overdue(c)]))
    for aid in expired:
        with locked("expire:" + aid) as c:
            execution.expire(c, attempt_ids=[aid])
    for step in (
        # Interrupted work that needs no eyes is settled here, so what a person is shown
        # is only the runs that used tools before they stopped.
        execution.auto_reconcile,
        execution.release_deploy_drains,
        execution.release_limits,  # a usage limit lifts at its reset, not at the next claim
        source_outages_tick,
        company_turns_tick,
        wake_presets_tick,  # wake presets let go of the notices and sweeps now due
        execution.hold_unrunnable,
        placement_sweep,  # an active bot with no computer gets one
        H.lift_cooled_quarantines,
        notes_catch_up,
    ):
        with locked(getattr(step, "__name__", "step")) as c:
            step(c)
    return (SR.count_words(len(expired), "lapsed run ended", "lapsed runs ended") if expired else "Done"), not expired


def step_ship_deployed(ctx):
    # a merged pull request whose push record arrived after the deploy still ships
    from .github import ship_deployed

    with ctx.locked("ship_deployed") as c:
        shipped = ship_deployed(c, ctx.settings)
    return SR.words_of(shipped, "pull request shipped", "Nothing to ship", "pull requests shipped"), SR.nothing(shipped)


def step_task_loops(ctx):
    from .task_loops import flag as flag_task_loops
    from .heartbeat import Failed

    told, before = [], len(ctx.failures)
    with ctx.locked("task_loops") as c:
        with isolated(c, "task_loops", "all", ctx.failures):
            told = flag_task_loops(c)
    if len(ctx.failures) > before:
        raise Failed("Failed: the loop check could not finish")
    return SR.words_of(told, "looping task told", "No looping tasks", "looping tasks told"), SR.nothing(told)


def step_never_stuck(ctx: "Context") -> tuple[str, bool]:
    # Pipelines v3: one pass over every open task on the engine (backend/never_stuck.py). A task it cannot work out is
    # left as it was and reported; the rest of the pass still happens.
    from . import never_stuck
    from .batch_work import report
    from .heartbeat import Failed

    failed: list[tuple[str, Exception]] = []
    with ctx.locked("never_stuck") as c:
        done = never_stuck.check(c, stamp(ctx.at), failed)
    for task_id, error in failed:
        ctx.failures.append({"never_stuck": task_id, "error": type(error).__name__})
        report("never_stuck", task_id, error)
    if failed:
        raise Failed(f"Failed: {SR.count_words(len(failed), 'task')} could not be checked")
    return SR.words_of(done, none="Nothing to do"), SR.nothing(done)


def step_snooze_ends(ctx):
    # Snoozes whose date came: back on every list, and one notice to the owner bot (backend/task_snooze.py).
    # With the deferred runs below, one job: Snooze ends.
    from . import delivery, quiet_work, task_snooze

    at, failures = ctx.at, ctx.failures
    with ctx.locked("task_snooze") as c:
        woken = task_snooze.wake_ended(c, stamp(at), failures)
    # Quiet tasks a run deferred to a time that has come get their run (backend/quiet_work.py).
    with ctx.locked("quiet_work") as c:
        resumed = quiet_work.due(c, stamp(at), failures)
    # Deferred items whose deadline came: one run per session carries them (backend/delivery.py).
    with ctx.locked("deferred") as c:
        carried = delivery.due(c, stamp(at), failures)
    done = [
        SR.words_of(found, one, "", many)
        for found, one, many in (
            (woken, "snooze ended", "snoozes ended"),
            (resumed, "quiet task resumed", "quiet tasks resumed"),
            (carried, "deferred run started", "deferred runs started"),
        )
    ]
    done = [words for words in done if words]
    return ", ".join(done) or "Nothing due", not done


def step_queue_check(ctx):
    # The fleet scan is a read: it runs outside the write lock, in one snapshot, so the bots' shared reads (runners,
    # providers, holds) are read once and other writers are not held while it runs.
    from . import queue_refusals

    with ctx.store.read_transaction() as c:
        observations = queue_refusals.pending(c, ctx.settings, stamp(ctx.at), ctx.failures)
    # Each observation is its own short write: the writer gate is not reentrant, so one inside "deferred" would wait out its timeout.
    # Only a new, changed or cleared hold counts; the minutely refresh of the same one is a quiet run.
    changed = 0
    for change in observations:
        with ctx.locked("queue_refusals:" + change[0]) as c:
            with isolated(c, "queue_refusals", change[0], ctx.failures):
                changed += bool(queue_refusals.apply(c, change))
    return SR.words_of(changed, "bot's queue noted", "Nothing new", "bots' queues noted"), SR.nothing(changed)


def step_milestone_waits(ctx):
    # A milestone wait whose pull request merged or release was published, while the server was down or
    # before its wake was written, gets its one wake (backend/milestone_waits.py).
    from . import milestone_waits

    with ctx.locked("milestone_waits") as c:
        woken = milestone_waits.reconcile(c, ctx.failures)
    return SR.words_of(woken, "waiting task woken", "Nothing waiting", "waiting tasks woken"), SR.nothing(woken)


def step_due_reminders(ctx):
    # Due-date reminders: candidates are read without the lock (H.TASKS_DUE_SQL, an index), and only the ones
    # that would send are written, a batch per transaction, each checked again under the lock.
    at, failures = ctx.at, ctx.failures
    with ctx.store.read() as c:
        ready = []
        for row in H.tasks_due_for_bots(c, stamp(at + timedelta(days=1))[:10]):
            try:
                if remind(c, row, at):
                    ready.append(row)
            except Exception:
                ready.append(row)  # the write below records the failure
    reminders = 0
    for batch in range(0, len(ready), REMINDER_BATCH):
        with ctx.locked("reminders") as c:
            for listed in ready[batch : batch + REMINDER_BATCH]:
                with isolated(c, "reminder", listed["id"], failures):
                    row = c.execute(
                        "SELECT id,title,owner_id,due_at,conversation_id FROM tasks WHERE id=? AND due_at=? "
                        "AND owner_id=? AND NOT "
                        + H.SNOOZED % "tasks"
                        + " AND status IN ("
                        + ",".join("?" * len(H.ACTIVE_STATUSES))
                        + ")",
                        (listed["id"], listed["due_at"], listed["owner_id"], *H.ACTIVE_STATUSES),
                    ).fetchone()
                    if row is None or not remind(c, dict(row), at):
                        continue
                    try:
                        H.say(
                            c,
                            H.KEEPER,
                            row["owner_id"],
                            "Due: " + row["title"],
                            kind="notice",
                            conversation_id=row["conversation_id"],
                            refs=run_triggers.stamp(
                                {"task": row["id"], "wake": "due"}, "schedule", purpose="work task due soon"
                            ),
                        )
                    except H.Refused as exc:
                        H.event(c, H.KEEPER, "task.reminder-refused", row["id"], {"reason": str(exc)[:300]})
                    c.execute("INSERT INTO task_reminders VALUES(?,?,?)", (row["id"], row["due_at"], stamp(at)))
                    reminders += 1
    return SR.words_of(reminders, "reminder sent", "Nothing due", "reminders sent"), SR.nothing(reminders)


def step_updates(ctx):
    # Updates (backend/updates.py): pump the queue built by Maintenance, one bot asked at a time. Its own short
    # transaction, so a failure here never holds back the routines above.
    # A person's draft is written by the company's model in a background thread, so the call never
    # holds the write lock; its row stays out until the thread posts it (or gives up on it).
    from . import updates

    store, settings = ctx.store, ctx.settings
    with store.transaction() as c:
        writer = updates.writer_for(c, settings, store)
        sent = updates.dispatch(c, ctx.at, settings.assistant_bot if writer else None, build=False)
    if sent and updates.is_person(sent):
        threading.Thread(
            target=updates.write_draft,
            args=(store, sent, writer, settings.assistant_bot),
            name="update-draft",
            daemon=True,
        ).start()
    return ("1 asked" if sent else "Nobody due"), not sent


def step_company_page(ctx):
    # The Company page's editions that came due (backend/company_page/schedule.py); the model call is off this loop.
    from .company_page import schedule as company_page_schedule

    begun = company_page_schedule.start(ctx.store, ctx.at)
    return ("Editions started" if begun else "Nothing due"), not begun


def step_ask_deadlines(ctx):
    return run_ask_deadlines(ctx.store, ctx.auth, ctx.at)


def step_thread_digest(ctx):
    return run_thread_digest(ctx.store, ctx.auth, ctx.at)


def step_thread_bot_digest(ctx):
    return run_thread_bot_digest(ctx.store, ctx.auth, ctx.at)


def step_thread_loop_guard(ctx):
    # Bot wakes the loop guard held: they go by themselves as their root's rolling hour reopens. Plain SQL, no model.
    from . import threads

    resumed = threads.reopen_due(ctx.store)
    return (
        SR.count_words(resumed, "paused reply", "paused replies") + " let go" if resumed else "Nothing paused"
    ), not resumed


def step_ci_sweep(ctx: "Context") -> tuple[str, bool]:
    # The repository's workflow runs since the last sweep: recorded, priced and posted to Finance (backend/ci_runs.py).
    from . import ci_runs
    from .store import H

    settings = ctx.settings
    return ci_runs.sweep(ctx.store, ctx.github_app, settings.release_repo, settings.ci_rate_per_job_minute, H.now())


def step_go_live_expiry(ctx):
    # A go-live waiting for a computer that never reports still ends, with its notice (backend/go_live.py).
    from . import go_live

    with ctx.store.transaction() as c:
        ended = go_live.expire(c, ctx.at)
    return SR.words_of(ended, "go-live ended", "Nothing to end", "go-lives ended"), SR.nothing(ended)


def step_stall_checks(ctx):
    # Every minute: the checks that stay per minute. A stalled task no longer wakes its bot here; the task
    # roundup lists it (backend/task_roundup.py).
    at, failures, store, locked = ctx.at, ctx.failures, ctx.store, ctx.locked
    with SRC.writing_as("stuck-check"):
        # Holds include person and approval waits that deliberately
        # never enter the stall candidate list. Reconcile them independently,
        # with one short transaction per task so runner writes can progress.
        from . import task_blocked

        with store.read_transaction() as c:
            blocked_ids = task_blocked.candidates(c, stamp(at))
        for task_id in blocked_ids:
            with locked('blocked_check:' + task_id) as c:
                task_blocked.sweep(c, stamp(at), task_ids=[task_id])
        # Tickets waiting at an assigning step go to the least busy bot of its pool (backend/task_allocator.py).
        allocated = []
        try:
            from . import task_allocator

            with store.transaction() as c:
                allocated = [f"{t} to {b}" for t, b in task_allocator.allocate(c, stamp(at))]
        except Exception as exc:
            failures.append({"task_allocator": type(exc).__name__})
        # A check open past its step's timeout is closed and its round skipped (backend/step_flow.py).
        timed_out = []
        try:
            from . import verdict_moves

            with store.transaction() as c:
                timed_out = verdict_moves.timeouts(c, stamp(at))
        except Exception as exc:
            failures.append({"check_timeouts": type(exc).__name__})
        retried = []
        try:
            from . import check_pools

            retried = check_pools.retry(store, service=ctx.github_app)
        except Exception as exc:
            failures.append({"check_pool_retry": type(exc).__name__})
        acted = {"checks timed out": timed_out, "tickets allocated": allocated, "waiting checks filed": retried}
        # ...and queued jobs whose message their bot may no longer read, which no claim would ever take.
        try:
            with store.read_transaction() as c:
                # The expensive privacy/provenance scan does not need the writer lock.
                from . import task_privacy

                job_ids = [
                    row["job_id"]
                    for row in c.execute(
                        "SELECT j.id AS job_id,j.bot_id,m.* FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued'"
                    )
                    if not task_privacy.message_readable(c, "bot:" + row["bot_id"], row, current=True)
                ]
            for job_id in job_ids:
                with locked("drop_unreadable:" + job_id) as c:
                    ctx.execution.drop_unreadable(c, job_ids=[job_id])
        except Exception as exc:
            failures.append({"drop_unreadable": type(exc).__name__})
    return SR.words_of(acted, none="Nothing stalled"), not any(acted.values())


def step_approval_expiry(ctx):
    # Every hour: approvals, asks and held messages nobody will act on leave Needs you (backend/approval_expiry.py).
    from . import approval_expiry

    with ctx.store.transaction() as c:
        ended = approval_expiry.sweep(c, stamp(ctx.at))
    ended = ended or {}
    acted = {
        "approvals expired": ended.get("stale") or [],
        "approvals closed with their task": ended.get("ended") or [],
        "asked cards expired": ended.get("cards") or [],
        "held messages kept unsent": ended.get("held") or [],
    }
    return SR.words_of(acted, none="Nothing expired"), not any(acted.values())


def step_stuck_cards(ctx: "Context") -> tuple[str, bool]:
    # Every 10 minutes: the stuck rule wakes a pipeline's manager about a card that stopped moving, then asks a person
    # (backend/stuck_cards.py). Plain SQL, no model.
    from . import stuck_cards

    store: Any = ctx.store  # Store.transaction is untyped
    with store.transaction() as c:
        done = stuck_cards.sweep(c, stamp(ctx.at))
    acted = {
        "managers woken": done["woken"],
        "people asked": done["asked"],
        "answers passed on": done["relayed"],
    }
    return SR.words_of(acted, none="No stuck cards"), not any(acted.values())


def step_stuck_alarm(ctx: "Context") -> tuple[str, bool]:
    # Every 10 minutes: one task to a factory's manager for each card stuck more than 4 hours, and the alarm clears itself
    # when the card moves (backend/stuck_alarm.py). Off unless TICO_STUCK_ALARM is on. Plain SQL, no model.
    from . import stuck_alarm

    if not stuck_alarm.enabled():
        return "Stuck alarm off", True
    store: Any = ctx.store  # Store.transaction is untyped
    with store.transaction() as c:
        done = stuck_alarm.sweep(c, stamp(ctx.at))
    acted = {"alarms raised": done["raised"], "alarms cleared": done["cleared"]}
    return SR.words_of(acted, none="No stuck alarms"), not any(acted.values())


class Scheduler:
    def __init__(self, store, execution, onboarding=None, auth=None, github_app=None):
        self.store, self.execution = store, execution
        self.github_app = github_app
        self.listening_since = H.now()  # when this server began listening; a computer is not silent before it
        self.onboarding, self.auth = onboarding, auth  # the Bookkeeper is built the tick Finance is set up
        self.swept = None
        self.stranded = None
        self.resumed = None
        self.asks_checked = None
        self.goals_checked = None
        self.gateway_checked = None
        self.tools_checked = None
        self.billing_checked = None
        self.cloud_checked = None
        self.stale_checked = None
        self.threads_ranked = None
        self.hosted_checked = None
        self.invariants_checked = None
        self.finance_checked = {}  # Finance job -> its last start in this process
        self.marks = {}  # a controlled job's last start in this process, if its record could not be written
        self.step_marks = {}  # a Heartbeat step's last start in this process (backend/heartbeat.py `_due`)

    def finance_due(self, key, at, every):
        last = self.finance_checked.get(key)
        if last is not None and at - last < every:
            return False
        self.finance_checked[key] = at  # marked first: a failing job retries on its interval
        return True

    def finance_tick(self, at, failures):
        """Finance's short jobs (backend/finance/jobs.py), each in its own short transaction and none on the network:
        waiting `finance.*` events every tick, run holds past expiry every ten minutes, Gateway usage postings hourly
        (the Gateway worker fetches the meter), bills due, reminders, the cash alert and ready closes hourly, setup
        when missing and next month's period daily."""
        from . import bookkeeper
        from .finance import actions as finance, reads as finance_reads

        steps = []
        try:
            finance.gateway_ensure()  # a Gateway worker that died comes back on the next tick
            finance.banking_ensure()  # so does the bank worker
            with self.store.read() as c:
                ready = finance_reads.is_set_up(c)
                waiting = ready and finance_reads.events_waiting(c)
        except Exception as exc:
            failures.append({"finance": type(exc).__name__})
            SR.write(self.store, SR.Run("finance_upkeep", error=SR.failure_words(exc)))
            return
        if not ready:
            if self.finance_due("setup", at, timedelta(hours=1)):
                steps.append(("finance_setup", lambda c: finance.ensure_setup(c, self.store.settings.owner_email)))
                if self.onboarding is not None:  # its own step: a Finance set up now builds the Bookkeeper now
                    steps.append(("bookkeeper", lambda c: bookkeeper.ensure(c, self.onboarding, self.auth)))
        else:
            if waiting:
                steps.append(("finance_events", lambda c: finance.drain_events(c, stamp(at))))
            if self.finance_due("holds", at, timedelta(minutes=10)):
                steps.append(("finance_holds", lambda c: finance.expire_holds(c, stamp(at))))
            if self.finance_due("usage", at, timedelta(hours=1)):
                steps.append(("finance_usage", lambda c: finance.post_usage(c, stamp(at))))
            if self.finance_due("prepaids", at, timedelta(hours=1)):
                steps.append(("finance_prepaids", lambda c: finance.amortize_prepaids(c, stamp(at))))
            if self.finance_due("documents", at, timedelta(hours=1)):
                steps.append(("finance_documents", lambda c: finance.watch_documents(c, stamp(at))))
            if self.finance_due("period", at, timedelta(days=1)):
                steps.append(("finance_period", lambda c: finance.open_next_period(c, stamp(at))))
            if self.finance_due("subscriptions", at, timedelta(days=1)):  # suggests and cancels (subscriptions.py)
                steps.append(("finance_subscriptions", lambda c: finance.detect_subscriptions(c, stamp(at))))
        failed = 0
        for name, step in steps:
            try:
                with self.store.transaction() as c:
                    step(c)
            except Exception as exc:
                failures.append({name: type(exc).__name__})
                failed += 1
        # One run of Finance upkeep per tick (Settings > Routines, Automations); a tick with nothing due folds.
        run = SR.Run("finance_upkeep")
        if failed:
            run.error = "Failed: " + SR.count_words(failed, "step") + " of " + str(len(steps)) + " did not finish"
        else:
            run.did(SR.count_words(len(steps), "step ran", "steps ran") if steps else "Nothing due", quiet=not steps)
        SR.write(self.store, run)

    def due(self, key, at, cfgs, last):
        """Whether a controlled job (backend/system_routines.py CONTROLS) runs now: on, and due on its saved preset since
        its last start. Marked first, so a failing job retries on its preset, not every tick. A job whose preset waits
        for its own times (SR.WAITS_FOR_ANCHOR) and has never run waits for its next one after this process first sees it
        on, so a deploy does not run it at once."""
        cfg = cfgs[key]
        mark = max([t for t in (self.marks.get(key), last.get(key)) if t], default=None)
        if mark is None and cfg["on"] and cfg.get("preset") in SR.WAITS_FOR_ANCHOR:
            mark = self.marks.setdefault(key, at)
        if not cfg["on"] or not SR.due(cfg, at, mark):
            return False
        self.marks[key] = at
        return True

    def tick(self, at=None, ctx=None):
        """The rest of the 10-second pass, after the Heartbeat's beat (backend/heartbeat.py), in the beat's `ctx`: its
        failures and write-lock holds go on this pass's service_health row. Called on its own, it beats first."""
        from . import heartbeat

        if ctx is None:
            ctx = heartbeat.Context(self, at)
            heartbeat.beat(ctx)
        at = ctx.at
        # tick_ms is this tick's own time; the beat records its own (heartbeat_beats.total_ms).
        started = time.monotonic()
        fired, failures, holds, locked = [], ctx.failures, ctx.holds, ctx.locked
        # A running turn's step the bot status throttle held back is sent now (backend/thread_bot_status.py).
        from . import thread_bot_status

        with self.store.read() as c:
            trailing = thread_bot_status.trailing_roots(c)
        if trailing:
            with locked("thread_bot_status") as c:
                with isolated(c, "thread_bot_status", "trailing", failures):
                    thread_bot_status.send_trailing(c, trailing)
        # The team's daily release hour: the Release Manager's one task for what is at Pending Release (backend/release_flow.py).
        if not self.store.settings.rehearsal:
            from . import release_flow

            with self.store.read() as c:
                release_due = release_flow.due(c, at)
            if release_due:
                with SR.recording(self.store, "release_hour") as run:
                    sent, before = None, len(failures)
                    with locked("release_schedule") as c:
                        with isolated(c, "release_schedule", release_due, failures):
                            sent = release_flow.tick(c, at)
                    if len(failures) > before:
                        run.error = "Failed: the release task could not be sent"
                    else:
                        run.did("Release task sent" if (sent or {}).get("sent") else "Nothing at Pending Release")
        longest = max(holds, default=(0, 0, None))
        with self.store.transaction() as c:
            c.execute(
                "INSERT INTO service_health VALUES('scheduler',?,?,?) ON CONFLICT(service) DO UPDATE SET "
                "last_succeeded_at=excluded.last_succeeded_at,last_error=excluded.last_error,detail=excluded.detail",
                (
                    stamp(at),
                    "schedule errors" if failures else None,
                    # How long the 10-second pass took, the longest any one of its transactions held the write
                    # lock and which step that was, and the longest it waited for the lock behind other writers.
                    # The minute and hourly passes below take their own transactions and are not counted here.
                    encode(
                        {
                            "failures": failures,
                            "tick_ms": round((time.monotonic() - started) * 1000),
                            "hold_ms": round(longest[0] * 1000),
                            "hold_step": longest[2],
                            "wait_ms": round(max((h[1] for h in holds), default=0) * 1000),
                            "transactions": len(holds),
                        }
                    ),
                ),
            )
        try:
            # The controlled jobs' saved settings; if they cannot be read, only those jobs wait for the next tick.
            with self.store.read() as c:
                cfgs = SR.config(c)
                last = SR.last_started(c, list(SR.QUICK_KEYS))
        except Exception as exc:
            cfgs = None
            failures.append({"system_routines": type(exc).__name__})
        from . import task_rules

        task_rules.tick(self.store, at, failures)
        # Reader notices a restart or the issuer's limit left waiting; the mail goes on a thread of its own. Its own
        # guard, so a failed edition start (the Heartbeat's Company page editions step) never holds the notices back.
        try:
            from .company_page import readers as company_page_readers

            company_page_readers.tick(self.store)
        except Exception as exc:
            failures.append({"company_page_readers": type(exc).__name__})
        # Maintenance (backend/maintenance.py): the run starts when its schedule says it is due; its steps run on a thread.
        from . import maintenance

        maintenance.tick(self.store, self.execution.auth, at, failures)
        # Page monitors (backend/market_watch.py): once a day the watched competitor pages are read on their own thread.
        try:
            from . import market_watch

            market_watch.tick(self.store, at)
        except Exception as exc:
            failures.append({"market_watch": type(exc).__name__})
        try:
            from . import automations

            automations.tick(self.store, at)  # schedules, batch times, answered turns, the queue
        except Exception as exc:
            failures.append({"automations": type(exc).__name__})
        with SR.recording(self.store, "screening", failures) as run:
            from . import screening

            screening.tick(self.store)  # outside text a restart left waiting for its check
            run.did("Done", quiet=True)
        try:
            from . import webhook_delivery

            webhook_delivery.ensure()  # a delivery worker that died comes back on the next tick
        except Exception as exc:
            failures.append({"webhook_delivery": type(exc).__name__})
        from . import pipeline_judges

        pipeline_judges.dispatch(self.store, at, failures)  # bot-reviewed pipeline requirements, one guarded call
        self.finance_tick(at, failures)
        if self.swept is None or at - self.swept >= timedelta(hours=1):
            # Deadline work that cannot wait for Clean up (backend/maintenance_steps/clean_up.py holds the pruning). Marked
            # first, so a failing sweep retries hourly, not every tick.
            self.swept = at
            with SR.recording(self.store, "hourly_deadlines", failures, "deadlines") as run:
                with self.store.transaction() as c:
                    from . import webhooks

                    off = webhooks.auto_disable(
                        c, stamp(at)
                    )  # an endpoint that failed every attempt for five days is off
                try:
                    from . import computers

                    computers.sweep(self.store, stamp(at))  # replaced self-hosted computers retire only when unused
                except Exception as exc:
                    LOG.exception("Computer retirement failed")
                    failures.append({"computer_retirement": type(exc).__name__})
                from . import support_triage

                support_triage.sweep(self.store, stamp(at))  # a support ticket waits here at most an hour
                from . import screening

                screening.sweep(self.store, stamp(at))  # a screened item waits at most an hour
                run.did(SR.words_of(off, "webhook turned off", "Done", "webhooks turned off"), quiet=not off)
        if self.goals_checked is None or at - self.goals_checked >= timedelta(hours=1):
            # Data goes stale as time passes, so every goal's automatic colour is worked out again once an
            # hour, whether or not a reading arrived (a person's colour is only ever suggested over).
            self.goals_checked = at
            with SR.recording(self.store, "goal_colours", failures, "goals") as run:
                with self.store.read() as c:
                    goal_ids = [row["id"] for row in G.goals(c, live_only=True)]
                for goal_id in goal_ids:
                    with locked("goal_colour:" + goal_id) as c:
                        G.refresh(c, [goal_id])
                run.did("Done")
            # The Strategy Bot's Monday note: started once above Passive, paused at Passive (backend/strategy_bot.py
            # `reconcile`).
            with SR.recording(self.store, "strategy_note", failures, "strategy_bot") as run:
                from . import strategy_bot

                with self.store.transaction() as c:
                    strategy_bot.reconcile(c)
                run.did("Checked", quiet=True)
        # The controlled jobs, each on its saved preset (Settings > Routines): KPI stale asks and reports (ask go-ahead is
        # a Heartbeat step).
        for key, run in QUICK.items():
            if cfgs and self.due(key, at, cfgs, last):
                run(self.store, self.execution.auth, at, failures)
        # The Bookkeeper's fixed-cadence jobs (FIXED): due one interval after their last start, as controlled jobs are.
        if cfgs is not None:
            for key, (run, every) in FIXED.items():
                mark = max([t for t in (self.marks.get(key), last.get(key)) if t], default=None)
                if mark is None or at - mark >= every:
                    self.marks[key] = at
                    run(self.store, self.execution.auth, at, failures)
        # Proposed skills and updates waiting for their screen (backend/skill_screening.py): on their own thread, since a
        # screen's model call can take 90 seconds. The pass records its own run.
        try:
            from . import skill_screening

            skill_screening.kick(self.store)
        except Exception as exc:
            failures.append({"skill_screening": type(exc).__name__})
        # Production invariants every 15 minutes (backend/invariants.py): plain SQL, no model.
        if self.invariants_checked is None or at - self.invariants_checked >= timedelta(minutes=15):
            self.invariants_checked = at
            run_invariants(self.store, self.execution.auth, at, failures)
        # Pipeline managers, their watchdog and the Factory Managers: plain code, one guarded call (backend/pipeline_manager.py).
        pipeline_manager.run_due(self.store, at, failures)
        if self.github_app is not None:
            # Tico's approval checks on pull requests (backend/pipeline_github_checks.py): recorded and posted on their own thread.
            from . import pipeline_github_checks

            pipeline_github_checks.dispatch(self.store, self.github_app, failures)
        if self.tools_checked is None or at - self.tools_checked >= timedelta(minutes=5):
            # API tools on the proxy with a check path, each every 30 minutes (backend/api_tools.py): a real call with
            # each key, on its own thread, so a slow vendor never holds a tick.
            self.tools_checked = at
            with SR.recording(self.store, "tool_checks", failures, "tools"):
                from . import api_tools

                api_tools.check_soon(self.store, self.store.settings)
        if self.gateway_checked is None or at - self.gateway_checked >= timedelta(hours=1):
            # Every hour, while a gateway key or an invite is set: the Tico AI Gateway's word on it (revoked, 80% of the month's cap,
            # the cap reached), for Settings and Health. On its own thread: a tick never waits on the Gateway.
            self.gateway_checked = at
            with SR.recording(self.store, "gateway_check", failures, "gateway") as run:
                from . import gateway_connection
                from .credentials import Vault

                with self.store.read() as c:
                    # An install command's invite, or the identity a first sign-up gave the install, also makes it
                    # due: the install enrolls for its own key, at start and hourly until it has one, and rotates it
                    # when the Gateway says.
                    due = gateway_connection.connected(c, self.store.settings) or gateway_connection.maintained(
                        c, self.store.settings
                    )
                if due:
                    gateway_connection.check_soon(self.store, Vault(self.store), self.store.settings)
                    # And whether this company uses Tico file hosting, with this server's total for HQ's estimate
                    # (backend/tico_storage.py).
                    from . import tico_storage

                    tico_storage.sync_later(self.store, report=True)
                run.did("Checked" if due else "No Gateway key set")
        from . import cloud_access

        if cloud_access.due(self.store, at, self.cloud_checked):
            # A Tico Cloud company's access (open, frozen or locked) from HQ: every minute while it is a cloud company,
            # hourly otherwise (backend/cloud_access.py). On its own thread; nothing at all without a gateway key.
            self.cloud_checked = at
            from . import gateway_connection
            from .credentials import Vault

            with self.store.read() as c:
                linked = gateway_connection.connected(c, self.store.settings)
            if linked:
                with SR.recording(self.store, "cloud_access", failures) as run:
                    cloud_access.sync_later(self.store, Vault(self.store), self.store.settings)
                    run.did("Asked HQ", quiet=True)
        # Tico file hosting's keys last an hour: HQ is asked again every 5 minutes while the company has it, and
        # before they end (backend/tico_storage.py `_plan`). On its own thread; nothing at all without it.
        from . import tico_storage

        # The outcome lands on its thread, after this run is written: the run says only that HQ was asked, in the access
        # check's words, so the two fold into one row.
        if tico_storage.sync_later(self.store, due_only=True):
            SR.write(self.store, SR.Run("cloud_access").did("Asked HQ", quiet=True))
        if self.billing_checked is None or at - self.billing_checked >= timedelta(hours=1):
            # Every hour, while this server holds HQ's read-only staff key (the Tico team's own install only): HQ's
            # nightly billing check for the owner's Health page (backend/billing_check.py). On its own thread.
            self.billing_checked = at
            with SR.recording(self.store, "billing_check", failures) as run:
                from . import billing_check

                held = billing_check.env_key(self.store.settings)
                if held:
                    billing_check.read_later(self.store, self.store.settings)
                run.did("Checked" if held else "Not used on this server")
            # And once a day, while this self-run server is linked to its Tico company (backend/company_server.py):
            # its version, for app.tico.dev and admin. On its own thread; no model turn.
            with SR.recording(self.store, "company_heartbeat", failures) as run:
                from . import company_server

                due = company_server.heartbeat_due(self.store)
                if due:
                    company_server.heartbeat_later(self.store)
                run.did("Sent" if due else "Nothing to send", quiet=not due)
        if self.hosted_checked is None or at - self.hosted_checked >= timedelta(minutes=1):
            # Every minute on a hosted server: the release Tico support scheduled for it, read from HQ every 15
            # minutes, started once no bot turn runs (backend/hosted_updates.py). On its own thread; nothing at all on
            # a self-run server, and nothing until support sets one.
            self.hosted_checked = at
            from . import hosted_updates

            if hosted_updates.on(self.store.settings):
                with SR.recording(self.store, "hosted_update", failures) as run:
                    due = hosted_updates.due(self.store)
                    if due:
                        hosted_updates.tick_later(self.store)
                    run.did("Checked" if due else "Nothing scheduled", quiet=not due)
        if self.stale_checked is None or at - self.stale_checked >= timedelta(minutes=5):
            # Stale ticket review (backend/stale_review.py), while the team has it on: its watermark and saved preset
            # space the passes (hourly by default);
            # the Decide calls run on the pass's own thread, never under this lock.
            self.stale_checked = at
            try:
                from . import stale_review

                stale_review.tick(self.store, stamp(at))
            except Exception as exc:
                failures.append({"stale_review": type(exc).__name__})
        if self.threads_ranked is None or at - self.threads_ranked >= timedelta(seconds=300):
            # Thread ranking (backend/thread_rank.py): the Important view's 1-10 levels, for roots with new messages;
            # the Decide calls run on the pass's own thread, never under this lock.
            self.threads_ranked = at
            try:
                from . import thread_rank

                thread_rank.tick(self.store, stamp(at))
            except Exception as exc:
                failures.append({"thread_rank": type(exc).__name__})
        from . import stranded_resume

        if self.resumed is None or at - self.resumed >= timedelta(minutes=stranded_resume.SWEEP_MINUTES):
            # A bot's task left in progress with no run and nothing to wait on gets one resume wake, a few per pass
            # (backend/stranded_resume.py).
            self.resumed = at
            with SR.recording(self.store, "stranded_resume", failures) as run:
                done = stranded_resume.sweep(self.store, stamp(at))
                run.did(
                    SR.words_of(
                        {"stranded tasks resumed": done["resumed"], "stopped after no progress": done["stopped"]},
                        none="Nothing stranded",
                    ),
                    quiet=not any(done.values()),
                )
        if self.stranded is None or at - self.stranded >= timedelta(hours=1):
            # Every hour: a bot's task waiting on nothing tracked goes back to open after a grace (task_holds.unstrand).
            self.stranded = at
            with SR.recording(self.store, "waiting_checks", failures, "stranded") as run:
                from . import task_holds

                with self.store.transaction() as c:
                    unstranded = task_holds.unstrand(c, stamp(at))
                run.did(
                    SR.words_of(
                        {
                            "tasks waiting on nothing reopened": unstranded["opened"],
                            "stuck tasks handed to a manager": unstranded["told"],
                        },
                        none="Nothing stranded",
                    )
                )

        if self.asks_checked is None or at - self.asks_checked >= timedelta(hours=1):
            self.asks_checked = at
            # A Finance action whose approval was decided, withdrawn or expired ends now (its hold released).
            with SR.recording(self.store, "finance_upkeep", failures, "finance_approvals") as run:
                from .finance import actions as finance

                with self.store.transaction() as c:
                    ended = finance.approvals_sweep(c, stamp(at))
                run.did(
                    SR.words_of(ended, "approved action finished", "Nothing due", "approved actions finished"),
                    quiet=not ended,
                )
            # ...and each weekday morning, questions unanswered two business days go back to their bots, as lines on
            # their task roundup (backend/still_needed.py).
            with SR.recording(self.store, "still_needed", failures) as run:
                from . import still_needed

                with self.store.transaction() as c:
                    sent = still_needed.sweep(c, stamp(at))
                run.did(
                    SR.words_of(
                        len(sent or {}), "bot with questions back", "Nothing to send back", "bots with questions back"
                    )
                )
        return {"fired": fired, "failures": failures}
