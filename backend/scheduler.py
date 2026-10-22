"""Deterministic, transactionally deduplicated scheduling independent of local runners."""

import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from croniter import croniter

from . import goals as G
from . import placement, run_triggers
from .batch_work import isolated
from .harnesses import SYNCED_SQL
from .statuses import PARKED_SQL
from .store import H, encode, sweep_idempotency, sweep_mail


def stamp(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def next_due(expr, at, zone):
    # Preserve the existing keeper's AND semantics for day-of-month and day-of-week.
    return croniter(expr, at.astimezone(ZoneInfo(zone)), day_or=False).get_next(datetime)

# The routines a tick looks at; `on:` routines fire from routines.emit.
SCHEDULES_SQL = (
    "SELECT s.*,coalesce(sc.timezone,'America/Los_Angeles') AS timezone "
    "FROM schedules s JOIN bots b ON b.slug=s.bot LEFT JOIN schedule_config sc ON sc.schedule_id=s.id "
    "WHERE b.state='active' AND coalesce(sc.enabled,1)=1 AND s.deleted_at IS NULL "
    "AND s.event_name IS NULL "
    "AND NOT EXISTS(SELECT 1 FROM bot_config bc WHERE bc.bot=s.bot "
    "AND coalesce(json_extract(bc.config_json,'$.shared_from'),'')<>'') "
    # a bot synced in from another platform never sees a task (hubdb._takes_tasks)
    "AND NOT EXISTS(SELECT 1 FROM bot_config xc WHERE xc.bot=s.bot "
    "AND json_extract(xc.config_json,'$.harness') IN (" + SYNCED_SQL + ")) "
    # a starter bot runs nothing on its own until it says its setup is done
    "AND NOT EXISTS(SELECT 1 FROM bot_config pc WHERE pc.bot=s.bot "
    "AND pc.onboarding_state IN " + PARKED_SQL + ")")
REMINDER_BATCH = 50


def schedule_due(row, at):
    due = H.parse_ts(row["next_due"])
    if due and due.tzinfo is None:
        due = due.replace(tzinfo=ZoneInfo(row["timezone"]))
    if not due:
        # Initialize after the last recorded fire; a new schedule starts in the future.
        base = H.parse_ts(row["last_fired"]) or at
        if base.tzinfo is None:
            base = base.replace(tzinfo=ZoneInfo(row["timezone"]))
        due = next_due(row["cron"], base, row["timezone"])
    return due


def waiting(row, at):
    """A routine not yet due whose next_due is already written: the tick has nothing to write for it."""
    due = schedule_due(row, at)
    return due > at and stamp(due) == row["next_due"]


def remind(c, row, at):
    """Whether a task from H.tasks_due_for_bots gets its due-date reminder now."""
    due = H.parse_ts(row.get("due"))
    if due and due.tzinfo is None:
        # Legacy rows allowed dates/local timestamps. New API writes require an
        # offset; preserve the old machine's Pacific interpretation on migration.
        due = due.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
    if not due or due > at or not row["owner"].startswith("bot:"):
        return False
    if c.execute("SELECT 1 FROM task_reminders WHERE task_id=? AND due=?", (row["id"], row["due"])).fetchone():
        return False
    return H.bot(c, H.actor_id(row["owner"]))["state"] == "active"


class Scheduler:
    def __init__(self, store, execution):
        self.store, self.execution = store, execution
        self.swept = None
        self.stranded = None
        self.still_wanted = None
        self.asks_checked = None
        self.stall_checked = None
        self.goals_checked = None
        self.go_lives_checked = None
        self.health_scanned = None
        self.gateway_checked = None
        self.billing_checked = None
        self.stale_checked = None

    def tick(self, at=None):
        at = at or datetime.now(timezone.utc)
        fired, failures, holds = [], [], []
        started = time.monotonic()

        @contextmanager
        def write():
            # One short write transaction per step, per routine and per reminder batch. The pass used to be one
            # transaction, and on a hub with many tasks and routines every request waited 20-30 s behind it
            # (TK-RCZJXEWA). Each step is whole on its own; routines and reminders keep their dedupe rows
            # (schedule_occurrences, task_reminders), checked under the same lock that writes them.
            begun = time.monotonic()
            try:
                with self.store.transaction() as c:
                    yield c
            finally:
                holds.append(time.monotonic() - begun)

        from . import wake_presets
        for step in (self.execution.expire,
                     # Interrupted work that needs no eyes is settled here, so what a person is shown
                     # is only the runs that used tools before they stopped.
                     self.execution.auto_reconcile,
                     self.execution.release_deploy_drains,
                     self.execution.release_limits,      # a usage limit lifts at its reset, not at the next claim
                     lambda c: wake_presets.tick(c, H.now()),   # wake presets let go of the notices and sweeps now due
                     self.execution.hold_unrunnable,
                     lambda c: placement.sweep(c, self.execution),   # an active bot with no computer gets one
                     H.lift_cooled_quarantines):
            with write() as c:
                step(c)
        # Routines are read without the lock; one not yet due whose next_due is already written needs no write at all.
        with self.store.read() as c:
            rows = c.execute(SCHEDULES_SQL).fetchall()
        for listed in rows:
            try:
                if waiting(listed, at):
                    continue
            except Exception:
                pass                                    # a bad cron or date is recorded by the write below
            created = None
            with write() as c:
                row = c.execute(SCHEDULES_SQL + " AND s.id=?", (listed["id"],)).fetchone()
                if row is None:                         # deleted, disabled or its bot paused since the read
                    continue
                with isolated(c, "schedule", row["id"], failures):
                    due = schedule_due(row, at)
                    if due > at:
                        # Written only when it moves: every routine rewrote its unchanged next_due
                        # every 10 s, a steady stream of dirty pages for the WAL and Litestream.
                        if stamp(due) != row["next_due"]:
                            c.execute("UPDATE schedules SET next_due=? WHERE id=?", (stamp(due), row["id"]))
                    else:
                        # Coalesce missed occurrences into the latest due occurrence.
                        local = (at + timedelta(microseconds=1)).astimezone(ZoneInfo(row["timezone"]))
                        occurrence = croniter(row["cron"], local, day_or=False).get_prev(datetime)
                        when = stamp(occurrence)
                        exists = c.execute("SELECT 1 FROM schedule_occurrences WHERE schedule_id=? AND occurrence=?",
                                           (row["id"], when)).fetchone()
                        if not exists:
                            from . import routines
                            existing, skip = routines.latest_task(c, row["id"]), None
                            if existing:
                                tid, outcome = existing["id"], "coalesced_into_existing_task"
                                # An absorbed occurrence still wakes the owner, or an open task
                                # would swallow every later run in silence; a waiting task is
                                # on a person, not the bot. Once per occurrence, so the cron bounds it.
                                if existing["status"] in ("open", "doing", "review", "ready"):
                                    H.say(c, H.KEEPER, "bot:" + row["bot"],
                                          f"Scheduled occurrence {when[:16].replace('T', ' ')}Z of {row['title']} "
                                          "absorbed into this open task; continue it or mark it done.",
                                          kind="notice", conversation_id=H.task(c, tid)["conversation_id"],
                                          refs=run_triggers.stamp({"task": tid, "wake": "occurrence", "sweep": row["id"]},
                                                                  "schedule", row["id"]))
                            elif skip := routines.no_input(c, self.store.settings, row, occurrence):
                                tid, outcome = None, "skipped"
                            else:
                                task = routines.open_task(c, self.execution.auth, row, row["title"],
                                                          row["playbook"] or routines.DEFAULT_TEXT, stamp(at),
                                                          sweep=True)
                                tid, outcome = task["id"], "created"
                                created = tid
                            c.execute("INSERT INTO schedule_occurrences VALUES(?,?,?,?)", (row["id"], when, tid, outcome))
                            H.event(c, H.KEEPER, "schedule.occurrence", row["id"], {
                                "occurrence": when, "task": tid, "outcome": outcome, **({"reason": skip} if skip else {})})
                        following = next_due(row["cron"], at, row["timezone"])
                        c.execute("UPDATE schedules SET last_fired=?,next_due=? WHERE id=?", (when, stamp(following), row["id"]))
                    if created:
                        fired.append(created)
        # a merged pull request whose push record arrived after the deploy still ships
        from .github import ship_deployed
        with write() as c:
            ship_deployed(c, self.store.settings)
        from .task_loops import flag as flag_task_loops
        with write() as c:
            with isolated(c, "task_loops", "all", failures):
                flag_task_loops(c)
        # Snoozes whose date came: back on every list, and one notice to the owner bot (backend/task_snooze.py).
        from . import task_snooze
        with write() as c:
            task_snooze.wake_ended(c, stamp(at), failures)
        # Quiet tasks a run deferred to a time that has come get their run (backend/quiet_work.py).
        from . import quiet_work
        with write() as c:
            quiet_work.due(c, stamp(at), failures)
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
        for batch in range(0, len(ready), REMINDER_BATCH):
            with write() as c:
                for listed in ready[batch:batch + REMINDER_BATCH]:
                    with isolated(c, "reminder", listed["id"], failures):
                        row = c.execute("SELECT id,title,owner,due,conversation_id FROM tasks WHERE id=? AND due=? "
                                        "AND owner=? AND snoozed_until IS NULL AND status IN (" +
                                        ",".join("?" * len(H.ACTIVE_STATUSES)) + ")",
                                        (listed["id"], listed["due"], listed["owner"], *H.ACTIVE_STATUSES)).fetchone()
                        if row is None or not remind(c, dict(row), at):
                            continue
                        try:
                            H.say(c, H.KEEPER, row["owner"], "Due: " + row["title"], kind="notice",
                                  conversation_id=row["conversation_id"],
                                  refs=run_triggers.stamp({"task": row["id"], "wake": "due"}, "schedule"))
                        except H.Refused as exc:
                            H.event(c, H.KEEPER, "task.reminder-refused", row["id"], {"reason": str(exc)[:300]})
                        c.execute("INSERT INTO task_reminders VALUES(?,?,?)", (row["id"], row["due"], stamp(at)))
        with write() as c:
            c.execute("INSERT INTO service_health VALUES('scheduler',?,?,?) ON CONFLICT(service) DO UPDATE SET "
                      "last_success=excluded.last_success,last_error=excluded.last_error,detail_json=excluded.detail_json",
                      (stamp(at), "schedule errors" if failures else None,
                       # How long the pass took, and the longest any one of its transactions held the write lock,
                       # so a slow one shows in service_health.
                       encode({"failures": failures, "tick_ms": round((time.monotonic() - started) * 1000),
                               "hold_ms": round(max(holds, default=0) * 1000), "transactions": len(holds)})))
        # Updates (backend/updates.py): today's queue, one bot asked at a time. Its own short
        # transaction, so a failure here never holds back the routines above.
        # A person's draft is written by the company's model in a background thread, so the call never
        # holds the write lock; its row stays out until the thread posts it (or gives up on it).
        try:
            from . import updates
            settings = self.store.settings
            with self.store.transaction() as c:
                write = updates.writer_for(c, settings, self.store)
                sent = updates.dispatch(c, at, settings.assistant_bot if write else None)
            if sent and updates.is_person(sent):
                threading.Thread(target=updates.write_draft, args=(self.store, sent, write, settings.assistant_bot),
                                 name="update-draft", daemon=True).start()
        except Exception as exc:
            failures.append({"updates": type(exc).__name__})
        # The nightly learning run (backend/learnings.py): one row per night claims it; the work runs
        # in a background thread, so the decision model's calls never hold the write lock.
        try:
            from . import learnings
            learnings.nightly(self.store, at)
        except Exception as exc:
            failures.append({"learnings": type(exc).__name__})
        if self.swept is None or at - self.swept >= timedelta(hours=1):
            # Outside the scheduling transaction: the sweep takes its own short write
            # locks. Marked first so a failing sweep retries hourly, not every tick.
            self.swept = at
            with self.store.transaction() as c:
                c.execute("DELETE FROM service_health WHERE service LIKE 'background:%' AND julianday(json_extract(detail_json,'$.failed_at')) < julianday(?) - 30", (stamp(at),))
                # A duplicate is not a failure (batch_work.isolated); rows an older server wrote for one go.
                c.execute("DELETE FROM service_health WHERE service LIKE 'background:%' AND last_error LIKE '%: duplicate: %'")
            sweep_idempotency(self.store, stamp(at))
            sweep_mail(self.store, stamp(at))
            from . import events
            events.sweep(self.store, stamp(at))            # live events keep a day
            from . import flight
            flight.sweep(self.store, at.timestamp())       # the flight recorder's minutes become hours
            from . import performance
            performance.sweep(self.store, at.timestamp())  # computers' minutes become ten-minute points
            from . import support_triage
            support_triage.sweep(self.store, stamp(at))    # a support ticket waits here at most an hour
        if self.goals_checked is None or at - self.goals_checked >= timedelta(hours=1):
            # Data goes stale as time passes, so every goal's automatic colour is worked out again once an
            # hour, whether or not a reading arrived (a person's colour is only ever suggested over).
            self.goals_checked = at
            try:
                with self.store.transaction() as c:
                    G.refresh(c)
            except Exception as exc:
                failures.append({"goals": type(exc).__name__})
            try:
                # A KPI that went stale as time passed becomes its owner's task here (backend/goal_asks.py). Its own
                # transaction: a failure here never undoes the colours above.
                from . import goal_asks
                with self.store.transaction() as c:
                    goal_asks.sync(c, self.execution.auth.owner_id(c), at)
            except Exception as exc:
                failures.append({"goal_asks": type(exc).__name__})
            try:
                # The CEO Bot's Monday note, started once on an install whose switch was already on (backend/ceo_bot.py).
                from . import ceo_bot
                with self.store.transaction() as c:
                    ceo_bot.reconcile(c)
            except Exception as exc:
                failures.append({"ceo_bot": type(exc).__name__})
        if self.health_scanned is None or at - self.health_scanned >= timedelta(hours=1):
            # Every hour, only while the owner has it on: the Health Monitor's scan (backend/health_monitor.py). Plain
            # code; it wakes the bot only for a finding the last scan did not have.
            self.health_scanned = at
            try:
                from . import health_monitor
                with self.store.transaction() as c:
                    health_monitor.scan(c, self.execution.auth, self.store.settings)
            except Exception as exc:
                failures.append({"health_monitor": type(exc).__name__})
        if self.gateway_checked is None or at - self.gateway_checked >= timedelta(hours=1):
            # Every hour, while a gateway key or an invite is set: the Tico AI Gateway's word on it (revoked, 80% of the month's cap,
            # the cap reached), for Settings and Health. On its own thread: a tick never waits on the Gateway.
            self.gateway_checked = at
            try:
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
            except Exception as exc:
                failures.append({"gateway": type(exc).__name__})
        if self.billing_checked is None or at - self.billing_checked >= timedelta(hours=1):
            # Every hour, while this server holds HQ's read-only staff key (the Tico team's own install only): HQ's
            # nightly billing check for the owner's Health page (backend/billing_check.py). On its own thread.
            self.billing_checked = at
            try:
                from . import billing_check
                if billing_check.env_key(self.store.settings):
                    billing_check.read_later(self.store, self.store.settings)
            except Exception as exc:
                failures.append({"billing_check": type(exc).__name__})
        if self.stale_checked is None or at - self.stale_checked >= timedelta(minutes=5):
            # Stale ticket review (backend/stale_review.py), while the team has it on: the watermark makes it hourly;
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
            try:
                from . import go_live
                with self.store.transaction() as c:
                    go_live.expire(c, at)
            except Exception as exc:
                failures.append({"go_live": type(exc).__name__})
        if self.stall_checked is None or at - self.stall_checked >= timedelta(minutes=1):
            # Every minute: bot tasks nothing is going to move (H.wake_stalled).
            self.stall_checked = at
            with self.store.transaction() as c:
                H.wake_stalled(c, stamp(at))
            # ...and queued jobs whose message their bot may no longer read, which no claim would ever take.
            try:
                with self.store.transaction() as c:
                    self.execution.drop_unreadable(c)
            except Exception as exc:
                failures.append({"drop_unreadable": type(exc).__name__})
        if self.stranded is None or at - self.stranded >= timedelta(hours=1):
            # Every hour: bot tasks parked as waiting with nothing left to wait on.
            self.stranded = at
            with self.store.transaction() as c:
                H.sweep_stranded(c, stamp(at))
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
        if self.still_wanted is None or at - self.still_wanted >= timedelta(days=1):
            self.still_wanted = at
            # Once a day: a task idle for a week gets one "Still wanted?" to its person (backend/still_wanted.py).
            try:
                from . import still_wanted
                with self.store.transaction() as c:
                    still_wanted.ask_idle(c, stamp(at))
            except Exception as exc:
                failures.append({"still_wanted": type(exc).__name__})
        if self.asks_checked is None or at - self.asks_checked >= timedelta(hours=1):
            self.asks_checked = at
            # Every hour: a bot's ask past its deadline goes ahead on its default, or expires (backend/ask_deadlines.py).
            try:
                from . import ask_deadlines
                with self.store.transaction() as c:
                    ask_deadlines.sweep(c, stamp(at))
            except Exception as exc:
                failures.append({"ask_deadlines": type(exc).__name__})
            # ...and approvals and asks nobody will act on leave Needs you (backend/approval_expiry.py).
            try:
                from . import approval_expiry
                with self.store.transaction() as c:
                    approval_expiry.sweep(c, stamp(at))
            except Exception as exc:
                failures.append({"approval_expiry": type(exc).__name__})
        return {"fired": fired, "failures": failures}
