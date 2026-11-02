"""Deterministic, transactionally deduplicated scheduling independent of local runners."""

import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from croniter import croniter

from . import goals as G
from . import placement, run_triggers
from . import system_routines as SR
from .batch_work import isolated
from .store import (H, encode, reclaim_free_pages, sweep_credential_uses, sweep_deltas, sweep_events, sweep_idempotency,
                    sweep_mail, sweep_tokens)


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
def run_still_wanted(store, auth, at, failures=None):
    # A task idle for a week gets one "Still wanted?" to its person (backend/still_wanted.py).
    with SR.recording(store, "still_wanted", failures) as run:
        from . import still_wanted
        with store.transaction() as c:
            asked = still_wanted.ask_idle(c, stamp(at))
        run.did(SR.words_of(asked, "task asked about", "No idle tasks"))


def run_kpi_asks(store, auth, at, failures=None):
    # A KPI that went stale as time passed becomes its owner's task here (backend/goal_asks.py). Its own transaction: a
    # failure here never undoes the goal colours.
    with SR.recording(store, "kpi_asks", failures, "goal_asks") as run:
        from . import goal_asks
        with store.transaction() as c:
            found = goal_asks.sync(c, auth.owner_id(c), at, goal_asks.readers(c, auth))
        run.did(SR.words_of({"asked": (found or {}).get("created") or [],
                             "closed": (found or {}).get("closed") or []}, none="Nothing to ask"))


def run_health_scan(store, auth, at, failures=None):
    # Only while the owner has the Health Monitor on (backend/health_monitor.py). Plain code; it wakes the bot only for a
    # finding the last scan did not have.
    with SR.recording(store, "health_scan", failures, "health_monitor") as run:
        from . import health_monitor
        woke = health_monitor.check(store, auth, store.settings)
        run.did(SR.words_of(woke, "new finding", "No new findings"))


def run_ask_deadlines(store, auth, at, failures=None):
    # A bot's ask past its deadline goes ahead on its default, or expires (backend/ask_deadlines.py).
    with SR.recording(store, "ask_deadlines", failures) as run:
        from . import ask_deadlines
        with store.transaction() as c:
            done = ask_deadlines.sweep(c, stamp(at))
        run.did(SR.words_of(done, "ask settled", "No asks past their deadline"))


def run_task_manager_report(store, auth, at, failures=None):
    # One report per department, built in plain code (backend/task_manager.py). Nothing to report is a quiet run: no task
    # and no model call. A department with something gets one "Task check" task for the Task Manager.
    with SR.recording(store, "task_manager_report", failures, "task_manager") as run:
        from . import task_manager
        with store.transaction() as c:
            made = task_manager.report(c, auth, store.settings, stamp(at))
        run.did(made["words"], quiet=not made["items"])


def run_engineering_exceptions(store, auth, at, failures=None):
    # Plain code (backend/pipeline_exceptions.py): a manager on the Manager profile is woken with its board's exceptions
    # only when an entry is new since the last list; no entry, or no new one, is a quiet run.
    with SR.recording(store, "engineering_exceptions", failures) as run:
        from . import pipeline_exceptions
        with store.transaction() as c:
            woke = pipeline_exceptions.deliver(c, stamp(at))
        run.did(SR.words_of(woke, "manager woken", "No new exceptions"), quiet=not woke)


def run_invariants(store, auth, at, failures=None):
    # Plain SQL over the hub (backend/invariants.py): no model. Reads take no write lock; a run is recorded only when a
    # check changed, inside the one write transaction that changes it. A quiet sweep writes nothing.
    from . import invariants
    run = SR.Run("invariant_checks")

    def record(c, counts):
        firing = [key for key, n in counts.items() if n]
        SR.keep(c, run.did(f"{len(firing)} failing: {', '.join(firing)}" if firing else "All checks pass"))

    try:
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


QUICK = {"still_wanted": run_still_wanted, "kpi_asks": run_kpi_asks, "health_scan": run_health_scan,
         "ask_deadlines": run_ask_deadlines, "task_manager_report": run_task_manager_report,
         "engineering_exceptions": run_engineering_exceptions, "bookkeeper_check": run_bookkeeper_check}

# The Bookkeeper's Watch (daily) and rules digest (weekly): their own cadence, not controlled jobs (CONTROLS has the
# six a person switches). The Bookkeeper's pause is their off switch.
FIXED = {"bookkeeper_watch": (run_bookkeeper_watch, timedelta(days=1)),
         "bookkeeper_rules_digest": (run_bookkeeper_rules_digest, timedelta(days=7))}


def run_now(store, auth, key, actor):
    """Run now (Settings > Routines): one controlled job, once. A plain-code job runs here and its run record is on its
    row when this returns (a failure is recorded, not raised); the stale ticket review and the learning run start on
    their own thread, as when scheduled, and return True. Raises a 409 when that one is already running."""
    from .store import Problem
    at = datetime.now(timezone.utc)
    if key in QUICK:
        QUICK[key](store, auth, at, [])
        running = False
    elif key == "stale_review":
        from . import stale_review
        if store.settings.rehearsal:
            raise Problem("conflict", "A rehearsal server runs no stale ticket review", 409)
        if stale_review.tick(store, stamp(at), force=True) is None:
            raise Problem("conflict", "A stale ticket review is already running, or it was just switched off", 409)
        running = True
    elif key == "memory_tidy":
        from . import memory_tidy_controls
        running = memory_tidy_controls.run_now(store, auth, actor)
    elif key == "learnings":
        from . import learnings
        with store.transaction() as c:
            run_id = learnings.claim_tonight(c)
        learnings.start(store, run_id)
        running = True
    else:
        raise Problem("invalid", "Nothing to run", 422)
    with store.transaction() as c:
        H.event(c, actor, "system_routines.run_now", key, {"running": running})
    return running


class Scheduler:
    def __init__(self, store, execution):
        self.store, self.execution = store, execution
        self.swept = None
        self.stranded = None
        self.asks_checked = None
        self.stall_checked = None
        self.goals_checked = None
        self.go_lives_checked = None
        self.gateway_checked = None
        self.billing_checked = None
        self.cloud_checked = None
        self.stale_checked = None
        self.hosted_checked = None
        self.invariants_checked = None
        self.finance_checked = {}   # Finance job -> its last start in this process
        self.marks = {}             # a controlled job's last start in this process, if its record could not be written

    def finance_due(self, key, at, every):
        last = self.finance_checked.get(key)
        if last is not None and at - last < every:
            return False
        self.finance_checked[key] = at                 # marked first: a failing job retries on its interval
        return True

    def finance_tick(self, at, failures):
        """Finance's short jobs (backend/finance/jobs.py), each in its own short transaction and none on the network:
        waiting `finance.*` events every tick, run holds past expiry every ten minutes, Gateway usage postings hourly
        (the Gateway worker fetches the meter), bills due, reminders, the cash alert and ready closes hourly, setup
        when missing and next month's period daily."""
        from .finance import actions as finance, reads as finance_reads
        steps = []
        try:
            finance.gateway_ensure()                   # a Gateway worker that died comes back on the next tick
            finance.banking_ensure()                   # so does the bank worker
            with self.store.read() as c:
                ready = finance_reads.is_set_up(c)
                waiting = ready and finance_reads.events_waiting(c)
        except Exception as exc:
            failures.append({"finance": type(exc).__name__})
            return
        if not ready:
            if self.finance_due("setup", at, timedelta(hours=1)):
                steps.append(("finance_setup", lambda c: finance.ensure_setup(c, self.store.settings.owner_email)))
        else:
            if waiting:
                steps.append(("finance_events", lambda c: finance.drain_events(c, stamp(at))))
            if self.finance_due("holds", at, timedelta(minutes=10)):
                steps.append(("finance_holds", lambda c: finance.expire_holds(c, stamp(at))))
            if self.finance_due("usage", at, timedelta(hours=1)):
                steps.append(("finance_usage", lambda c: finance.post_usage(c, stamp(at))))
            if self.finance_due("documents", at, timedelta(hours=1)):
                steps.append(("finance_documents", lambda c: finance.watch_documents(c, stamp(at))))
            if self.finance_due("period", at, timedelta(days=1)):
                steps.append(("finance_period", lambda c: finance.open_next_period(c, stamp(at))))
        for name, step in steps:
            try:
                with self.store.transaction() as c:
                    step(c)
            except Exception as exc:
                failures.append({name: type(exc).__name__})

    def due(self, key, at, cfgs, last):
        """Whether a controlled job (backend/system_routines.py CONTROLS) runs now: on, and due on its saved preset since
        its last start. Marked first, so a failing job retries on its preset, not every tick."""
        cfg = cfgs[key]
        mark = max([t for t in (self.marks.get(key), last.get(key)) if t], default=None)
        if not cfg["on"] or not SR.due(cfg, at, mark):
            return False
        self.marks[key] = at
        return True

    def tick(self, at=None):
        at = at or datetime.now(timezone.utc)
        fired, failures, holds = [], [], []
        started = time.monotonic()

        @contextmanager
        def locked(step):
            # One short write transaction per step and per reminder batch. The pass used to be one transaction, and on
            # a hub with many tasks every request waited 20-30 s behind it (TK-RCZJXEWA). Each step is whole on its
            # own; reminders keep their dedupe rows (task_reminders), checked under the same lock that writes them.
            # The hold is timed from BEGIN to commit; the wait for the lock is kept apart, since it measures other
            # writers, not this pass.
            asked, begun = time.monotonic(), None
            try:
                with self.store.transaction() as c:
                    begun = time.monotonic()
                    yield c
            finally:
                if begun is not None:
                    holds.append((time.monotonic() - begun, begun - asked, step))

        from . import wake_presets

        def wake_presets_tick(c):
            wake_presets.tick(c, H.now())

        def placement_sweep(c):
            placement.sweep(c, self.execution)

        def notes_catch_up(c):
            # A completed run's notes are settled where its result is recorded; this settles any that path missed.
            H.notes_settle_catch_up(c, 500)

        def source_outages_tick(c):
            from . import source_outages
            source_outages.sync(c, self.store.settings)

        def company_turns_tick(c):
            from . import org_turns
            org_turns.observe(c, self.store.settings, detailed=False)
        # The turn fixer first: a start it proves dead is freed with a note on its task before plain expiry takes it.
        # Fleet classification for the Health finding runs on a read connection between two short writes, so claim
        # renewals and events are not parked behind every queued bot's refusal check (TK / #1654 residual).
        with SR.recording(self.store, "turn_fixer", failures) as run:
            from . import turn_fixer
            freed, incident = turn_fixer.run_split(self.execution, self.store.settings, locked, self.store.read)
            run.did(turn_fixer.words(freed, incident), quiet=not freed)
        with self.store.read() as c:
            from . import start_places
            expired = [row[0] for row in c.execute(
                "SELECT id FROM attempts WHERE state IN ('leased','running') AND lease_until<=? ORDER BY lease_until,id",
                (H.now(),))]
            expired = list(dict.fromkeys([*expired, *start_places.overdue(c)]))
        for aid in expired:
            with locked("expire:" + aid) as c:
                self.execution.expire(c, attempt_ids=[aid])
        for step in (
                     # Interrupted work that needs no eyes is settled here, so what a person is shown
                     # is only the runs that used tools before they stopped.
                     self.execution.auto_reconcile,
                     self.execution.release_deploy_drains,
                     self.execution.release_limits,      # a usage limit lifts at its reset, not at the next claim
                     source_outages_tick,
                     company_turns_tick,
                     wake_presets_tick,                         # wake presets let go of the notices and sweeps now due
                     self.execution.hold_unrunnable,
                     placement_sweep,                           # an active bot with no computer gets one
                     H.lift_cooled_quarantines,
                     notes_catch_up):
            with locked(getattr(step, "__name__", "step")) as c:
                step(c)
        # a merged pull request whose push record arrived after the deploy still ships
        from .github import ship_deployed
        with locked("ship_deployed") as c:
            ship_deployed(c, self.store.settings)
        from .task_loops import flag as flag_task_loops
        with locked("task_loops") as c:
            with isolated(c, "task_loops", "all", failures):
                flag_task_loops(c)
        # Snoozes whose date came: back on every list, and one notice to the owner bot (backend/task_snooze.py).
        from . import task_snooze
        with SR.recording(self.store, "snooze_ends") as run:
            with locked("task_snooze") as c:
                woken = task_snooze.wake_ended(c, stamp(at), failures)
            run.did(SR.words_of(woken, "snooze ended", "Nothing due"), quiet=not woken)
        # Quiet tasks a run deferred to a time that has come get their run (backend/quiet_work.py).
        from . import quiet_work
        with locked("quiet_work") as c:
            quiet_work.due(c, stamp(at), failures)
        # Deferred items whose deadline came: one run per session carries them (backend/delivery.py).
        from . import delivery
        with locked("deferred") as c:
            delivery.due(c, stamp(at), failures)
        from . import assigned_start
        if not self.store.settings.rehearsal:
            unstarted = []
            try:
                # One snapshot for the cursor read and the selection, so the runs' provenance primes together.
                with self.store.read_transaction() as c:
                    saved = c.execute('SELECT value FROM registry_metadata WHERE key=?',
                                      (assigned_start.CURSOR_KEY,)).fetchone()
                    cursor = [H._json(saved[0], "") if saved else ""]
                    unstarted = assigned_start.due(c, stamp(at), cursor=cursor, failures=failures)
                with locked("assigned_start_cursor") as c:
                    c.execute('INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                              (assigned_start.CURSOR_KEY, H._dump(cursor[0])))
            except Exception as exc:
                failures.append({'assigned_start': 'selection', 'error': type(exc).__name__})
            for task_id in unstarted:
                with locked("assigned_start:" + task_id) as c:
                    with isolated(c, "assigned_start", task_id, failures):
                        assigned_start.sweep(c, stamp(at), [task_id])
            from . import queue_refusals
            # One snapshot for the whole scan, so the bots' shared reads (runners, providers, holds) are read once.
            with self.store.read_transaction() as c:
                observations = queue_refusals.pending(c, self.store.settings, stamp(at), failures)
            for change in observations:
                with locked("queue_refusals:" + change[0]) as c:
                    with isolated(c, "queue_refusals", change[0], failures):
                        queue_refusals.apply(c, change)
        # A milestone wait whose pull request merged or release was published, while the server was down or
        # before its wake was written, gets its one wake (backend/milestone_waits.py).
        from . import milestone_waits
        with locked("milestone_waits") as c:
            milestone_waits.reconcile(c, failures)
        # The team's daily release hour: the Release Manager's one task for what is at Pending Release (backend/release_flow.py).
        if not self.store.settings.rehearsal:
            from . import release_flow
            with self.store.read() as c:
                release_due = release_flow.due(c, at)
            if release_due:
                with locked("release_schedule") as c:
                    with isolated(c, "release_schedule", release_due, failures):
                        release_flow.tick(c, at)
        # Due-date reminders: candidates are read without the lock (H.TASKS_DUE_SQL, an index), and only the ones
        # that would send are written, a batch per transaction, each checked again under the lock.
        with self.store.read() as c:
            ready = []
            for row in H.tasks_due_for_bots(c, stamp(at + timedelta(days=1))[:10]):
                try:
                    if remind(c, row, at):
                        ready.append(row)
                except Exception:
                    ready.append(row)                   # the write below records the failure
        reminders = 0
        for batch in range(0, len(ready), REMINDER_BATCH):
            with locked("reminders") as c:
                for listed in ready[batch:batch + REMINDER_BATCH]:
                    with isolated(c, "reminder", listed["id"], failures):
                        row = c.execute("SELECT id,title,owner_id,due_at,conversation_id FROM tasks WHERE id=? AND due_at=? "
                                        "AND owner_id=? AND snoozed_until IS NULL AND status IN (" +
                                        ",".join("?" * len(H.ACTIVE_STATUSES)) + ")",
                                        (listed["id"], listed["due_at"], listed["owner_id"], *H.ACTIVE_STATUSES)).fetchone()
                        if row is None or not remind(c, dict(row), at):
                            continue
                        try:
                            H.say(c, H.KEEPER, row["owner_id"], "Due: " + row["title"], kind="notice",
                                  conversation_id=row["conversation_id"],
                                  refs=run_triggers.stamp({"task": row["id"], "wake": "due"}, "schedule",
                                                           purpose="work task due soon"))
                        except H.Refused as exc:
                            H.event(c, H.KEEPER, "task.reminder-refused", row["id"], {"reason": str(exc)[:300]})
                        c.execute("INSERT INTO task_reminders VALUES(?,?,?)", (row["id"], row["due_at"], stamp(at)))
                        reminders += 1
        SR.write(self.store, SR.Run("due_reminders").did(SR.words_of(reminders, "reminder sent", "Nothing due"),
                                                         quiet=not reminders))
        longest = max(holds, default=(0, 0, None))
        with self.store.transaction() as c:
            c.execute("INSERT INTO service_health VALUES('scheduler',?,?,?) ON CONFLICT(service) DO UPDATE SET "
                      "last_succeeded_at=excluded.last_succeeded_at,last_error=excluded.last_error,detail=excluded.detail",
                      (stamp(at), "schedule errors" if failures else None,
                       # How long the 10-second pass took, the longest any one of its transactions held the write
                       # lock and which step that was, and the longest it waited for the lock behind other writers.
                       # The minute and hourly passes below take their own transactions and are not counted here.
                       encode({"failures": failures, "tick_ms": round((time.monotonic() - started) * 1000),
                               "hold_ms": round(longest[0] * 1000), "hold_step": longest[2],
                               "wait_ms": round(max((h[1] for h in holds), default=0) * 1000),
                               "transactions": len(holds)})))
        # Updates (backend/updates.py): today's queue, one bot asked at a time. Its own short
        # transaction, so a failure here never holds back the routines above.
        # A person's draft is written by the company's model in a background thread, so the call never
        # holds the write lock; its row stays out until the thread posts it (or gives up on it).
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
        with SR.recording(self.store, "updates", failures) as run:
            from . import updates
            settings = self.store.settings
            with self.store.transaction() as c:
                writer = updates.writer_for(c, settings, self.store)
                sent = updates.dispatch(c, at, settings.assistant_bot if writer else None)
            if sent and updates.is_person(sent):
                threading.Thread(target=updates.write_draft, args=(self.store, sent, writer, settings.assistant_bot),
                                 name="update-draft", daemon=True).start()
            run.did("1 asked" if sent else "Nobody due", quiet=not sent)
        # The nightly learning run (backend/learnings.py): one row per night claims it; the work runs
        # in a background thread, so the decision model's calls never hold the write lock.
        try:
            from . import learnings
            learnings.nightly(self.store, at)
        except Exception as exc:
            failures.append({"learnings": type(exc).__name__})
        try:
            from . import memory_tidy_controls
            from .store import Problem
            try:
                memory_tidy_controls.nightly(self.store, self.execution.auth, at)
            except Problem as exc:
                if exc.detail != memory_tidy_controls.UNAVAILABLE:
                    raise
        except Exception as exc:
            failures.append({"memory_tidy": type(exc).__name__})
        # Page monitors (backend/market_watch.py): once a day the watched competitor pages are read on their own thread.
        try:
            from . import market_watch
            market_watch.tick(self.store, at)
        except Exception as exc:
            failures.append({"market_watch": type(exc).__name__})
        try:
            from . import automations
            automations.tick(self.store, at)                   # schedules, batch times, answered turns, the queue
        except Exception as exc:
            failures.append({"automations": type(exc).__name__})
        try:
            from . import screening
            screening.tick(self.store)                         # outside text a restart left waiting for its check
        except Exception as exc:
            failures.append({"screening": type(exc).__name__})
        try:
            from . import webhook_delivery
            webhook_delivery.ensure()                          # a delivery worker that died comes back on the next tick
        except Exception as exc:
            failures.append({"webhook_delivery": type(exc).__name__})
        self.finance_tick(at, failures)
        if self.swept is None or at - self.swept >= timedelta(hours=1):
            # Outside the scheduling transaction: the sweep takes its own short write
            # locks. Marked first so a failing sweep retries hourly, not every tick.
            self.swept = at
            with SR.recording(self.store, "cleanup") as run:
                with self.store.transaction() as c:
                    c.execute("DELETE FROM service_health WHERE service LIKE 'background:%' AND julianday(json_extract(detail,'$.failed_at')) < julianday(?) - 30", (stamp(at),))
                    # A duplicate is not a failure (batch_work.isolated); rows an older server wrote for one go.
                    c.execute("DELETE FROM service_health WHERE service LIKE 'background:%' AND last_error LIKE '%: duplicate: %'")
                    SR.prune(c)                                # each system job keeps its last runs
                    from . import rules
                    rules.prune_events(c)                      # the webhook rules' repeat ledger keeps 30 days
                    from . import webhook_delivery
                    webhook_delivery.prune(c, stamp(at))       # outgoing webhooks: events and finished sends keep a month
                    from . import automations
                    automations.prune(c, stamp(at))            # automation runs: step detail keeps 30 days
                    from . import webhooks
                    webhooks.auto_disable(c, stamp(at))        # an endpoint that failed every attempt for five days is turned off
                sweep_idempotency(self.store, stamp(at))
                sweep_mail(self.store, stamp(at))
                sweep_deltas(self.store, stamp(at))            # finished runs' streamed text pieces keep a day (store.DELTA_RETENTION_DAYS)
                sweep_tokens(self.store, stamp(at))            # finished runs' token rows keep a week (store.TOKEN_RETENTION_DAYS)
                sweep_events(self.store, stamp(at))            # bookkeeping events keep 7 or 30 days (store.EVENT_NOISE_ACTIONS, EVENT_SWEEP_ACTIONS)
                sweep_credential_uses(self.store, stamp(at))   # one-time: old credential use rows to one per USE_WINDOW (store.TRIM_MAX a pass)
                reclaim_free_pages(self.store)                 # the pages those sweeps freed go back to the disk (store.VACUUM_PASS_MAX a pass)
                from . import events
                events.sweep(self.store, stamp(at))            # live events keep a day
                from . import triggers
                triggers.sweep(self.store, stamp(at))          # webhook bodies keep their trigger's days, delivery ids 30
                from . import flight
                flight.sweep(self.store, at.timestamp())       # the flight recorder's minutes become hours
                from . import performance
                performance.sweep(self.store, at.timestamp())  # computers' minutes become ten-minute points
                from . import support_triage
                support_triage.sweep(self.store, stamp(at))    # a support ticket waits here at most an hour
                from . import screening
                screening.sweep(self.store, stamp(at))         # a screened item waits at most an hour; verdicts keep 30 days
                run.did("Done")
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
            try:
                # The Strategy Bot's Monday note: started once above Passive, paused at Passive (backend/strategy_bot.py
                # `reconcile`).
                from . import strategy_bot
                with self.store.transaction() as c:
                    strategy_bot.reconcile(c)
            except Exception as exc:
                failures.append({"strategy_bot": type(exc).__name__})
        # The controlled jobs, each on its saved preset (Settings > Routines): KPI stale asks, the Health Monitor's scan
        # (hourly by default), Still wanted? (daily) and ask go-ahead.
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
        # Production invariants every 15 minutes (backend/invariants.py): plain SQL, no model.
        if self.invariants_checked is None or at - self.invariants_checked >= timedelta(minutes=15):
            self.invariants_checked = at
            run_invariants(self.store, self.execution.auth, at, failures)
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
                        c, self.store.settings)
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
                cloud_access.sync_later(self.store, Vault(self.store), self.store.settings)
        # Tico file hosting's keys last an hour: HQ is asked again every 5 minutes while the company has it, and
        # before they end (backend/tico_storage.py `_plan`). On its own thread; nothing at all without it.
        from . import tico_storage
        tico_storage.sync_later(self.store, due_only=True)
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
        if self.go_lives_checked is None or at - self.go_lives_checked >= timedelta(minutes=10):
            # A go-live waiting for a computer that never reports still ends, with its notice (backend/go_live.py).
            self.go_lives_checked = at
            with SR.recording(self.store, "go_live_expiry", failures, "go_live") as run:
                from . import go_live
                with self.store.transaction() as c:
                    ended = go_live.expire(c, at)
                run.did(SR.words_of(ended, "go-live ended", "Nothing to end"), quiet=not ended)
        if self.stall_checked is None or at - self.stall_checked >= timedelta(minutes=1):
            # Every minute: bot tasks nothing is going to move (H.wake_stalled).
            self.stall_checked = at
            with SR.recording(self.store, "stall_checks") as run:
                with self.store.transaction() as c:
                    from . import run_diagnostics
                    run_diagnostics.sweep(c, stamp(at))
                # Holds include person and approval waits that deliberately
                # never enter the stall candidate list. Reconcile them independently,
                # with one short transaction per task so runner writes can progress.
                from . import task_blocked
                with self.store.read_transaction() as c:
                    blocked_ids = task_blocked.candidates(c, stamp(at))
                for task_id in blocked_ids:
                    with locked('blocked_check:' + task_id) as c:
                        task_blocked.sweep(c, stamp(at), task_ids=[task_id])
                # Discover candidates in a WAL snapshot, then recheck one task under each
                # short lock. Unrelated runner writes can progress between tasks.
                with self.store.read_transaction() as c:
                    rows = [row for row in [*H.stalled_tasks(c, stamp(at)), *H.spot_check_tasks(c, stamp(at))]
                            if not H.stall_skip(c, row, stamp(at))]
                    task_ids = list(dict.fromkeys(row["id"] for row in rows))
                    try:
                        from . import stall_triage
                        triage = stall_triage.inputs(c, stamp(at), rows)
                    except Exception as exc:
                        triage = []
                        failures.append({"stall_triage": type(exc).__name__})
                # Decide reads the quiet tasks that stand as at their bot's checkpoint (backend/stall_triage.py) on its
                # own thread, off this lock; the wake loop below sees its verdict from the next minute on.
                if triage:
                    stall_triage.tick(self.store, triage, stamp(at))
                woke = {key: [] for key in ("woke", "escalated", "checked", "stuck", "held", "retried")}
                # Our earlier task wake must not make the same bot appear busy for
                # the rest of this pass. Other newly queued work still fences it.
                queued_here = []
                for task_id in task_ids:
                    with locked("stall_check:" + task_id) as c:
                        result = H.wake_stalled(c, stamp(at), task_ids=[task_id], queued_here=queued_here)
                        for key, values in result.items():
                            woke.setdefault(key, []).extend(values)
                # Dev tickets long at a working step: a status from a quiet owner, the late ones to their manager.
                aged = {}
                try:
                    from . import step_age
                    with self.store.transaction() as c:
                        aged = step_age.sweep(c, stamp(at))
                except Exception as exc:
                    failures.append({"step_age": type(exc).__name__})
                # A turn that ended and left its ticket or check in progress with nothing queued: its owner, once.
                nudged = []
                try:
                    from . import step_age
                    with self.store.transaction() as c:
                        nudged = step_age.turn_ended(c, stamp(at))
                except Exception as exc:
                    failures.append({"turn_ended": type(exc).__name__})
                # Work piling up at a step with a backlog setting: whoever it names hears of it (backend/step_backlog.py).
                backlogged = []
                try:
                    from . import step_backlog
                    with self.store.transaction() as c:
                        backlogged = [f"{n} at {s}" + (f" for {o}" if o else "") for s, o, n, _ in step_backlog.sweep(c, stamp(at))]
                except Exception as exc:
                    failures.append({"step_backlog": type(exc).__name__})
                # Tickets waiting at an assigning step go to the least busy bot of its pool (backend/task_allocator.py).
                allocated = []
                try:
                    from . import task_allocator
                    with self.store.transaction() as c:
                        allocated = [f"{t} to {b}" for t, b in task_allocator.allocate(c, stamp(at))]
                except Exception as exc:
                    failures.append({"task_allocator": type(exc).__name__})
                # A check open past its step's timeout is closed and its round skipped (backend/step_flow.py).
                timed_out = []
                try:
                    from . import verdict_moves
                    with self.store.transaction() as c:
                        timed_out = verdict_moves.timeouts(c, stamp(at))
                except Exception as exc:
                    failures.append({"check_timeouts": type(exc).__name__})
                acted = {"stalled tasks woken": woke.get("woke") or [], "sent to BotOps": woke.get("escalated") or [],
                         "retried": woke.get("retried") or [], "held for setup": woke.get("held") or [],
                         "asked for a status": aged.get("asked") or [], "late tickets told": aged.get("late") or [],
                         "checks timed out": timed_out, "tickets allocated": allocated, "backlogs reported": backlogged,
                         "late tickets nobody told": aged.get("untold") or [],
                         "owners woken after an idle turn": nudged,
                         "tickets failed": aged.get("failed") or []}
                run.did(SR.words_of(acted, none="Nothing stalled"), quiet=not any(acted.values()))
                # ...and queued jobs whose message their bot may no longer read, which no claim would ever take.
                try:
                    with self.store.read_transaction() as c:
                        # The expensive privacy/provenance scan does not need the writer lock.
                        from . import task_privacy
                        job_ids = [row["job_id"] for row in c.execute(
                            "SELECT j.id AS job_id,j.bot_id,m.* FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued'")
                            if not task_privacy.message_readable(c, "bot:" + row["bot_id"], row, current=True)]
                    for job_id in job_ids:
                        with locked("drop_unreadable:" + job_id) as c:
                            self.execution.drop_unreadable(c, job_ids=[job_id])
                except Exception as exc:
                    failures.append({"drop_unreadable": type(exc).__name__})
        if self.stranded is None or at - self.stranded >= timedelta(hours=1):
            # Every hour: bot tasks parked as waiting with nothing left to wait on.
            self.stranded = at
            try:
                # Once after this shipped: every waiting task's reason put right, one wake per bot (task_holds.py).
                from . import task_holds
                with self.store.transaction() as c:
                    task_holds.backfill(c, stamp(at))
            except Exception as exc:
                failures.append({"task_holds_backfill": type(exc).__name__})
            with SR.recording(self.store, "waiting_checks") as run:
                with self.store.transaction() as c:
                    moved = H.sweep_stranded(c, stamp(at))
                run.did(SR.words_of(moved, "task sent back to work", "Nothing stranded"))
            # ...bot tasks waiting on a person with a plain ask become the bot's task blocked on one small subtask
            # the person owns...
            try:
                from . import person_asks
                with self.store.transaction() as c:
                    person_asks.migrate(c)
            except Exception as exc:
                failures.append({"person_asks": type(exc).__name__})
            # ...and bot tasks waiting on a person with no plain ask: their bot is asked once to restate it.
            try:
                from . import restate_asks
                with self.store.transaction() as c:
                    restate_asks.ask_restate(c)
            except Exception as exc:
                failures.append({"restate_asks": type(exc).__name__})
        if self.asks_checked is None or at - self.asks_checked >= timedelta(hours=1):
            self.asks_checked = at
            # Every hour: approvals and asks nobody will act on leave Needs you (backend/approval_expiry.py).
            with SR.recording(self.store, "approval_expiry", failures) as run:
                from . import approval_expiry, memory_tidy_cards
                with self.store.transaction() as c:
                    ended = approval_expiry.sweep(c, stamp(at))
                    # Standalone cards use their own two-business-day clock, never task closure.
                    tidy_expired = memory_tidy_cards.sweep(c, stamp(at))
                ended = ended or {}
                run.did(SR.words_of({"approvals expired": ended.get("stale") or [],
                                     "approvals closed with their task": ended.get("ended") or [],
                                     "asks cleared": ended.get("asks") or [],
                                     "tidy cards withdrawn": tidy_expired}, none="Nothing expired"))
            # A Finance action whose approval was decided, withdrawn or expired ends now (its hold released).
            try:
                from .finance import actions as finance
                with self.store.transaction() as c:
                    finance.approvals_sweep(c, stamp(at))
            except Exception as exc:
                failures.append({"finance_approvals": type(exc).__name__})
            # ...and each weekday morning, questions unanswered two business days go back to their bots, one wake per
            # bot (backend/still_needed.py).
            with SR.recording(self.store, "still_needed", failures) as run:
                from . import still_needed
                with self.store.transaction() as c:
                    sent = still_needed.sweep(c, stamp(at))
                run.did(SR.words_of(len(sent or {}), "bot reminded", "Nothing to send back"))
        return {"fired": fired, "failures": failures}
