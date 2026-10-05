"""Deterministic, transactionally deduplicated scheduling independent of local runners."""

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from croniter import croniter

from . import goals as G
from . import placement
from .batch_work import isolated
from .statuses import PARKED_SQL
from .store import H, encode, sweep_idempotency, sweep_mail


def stamp(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def next_due(expr, at, zone):
    # Preserve the existing keeper's AND semantics for day-of-month and day-of-week.
    return croniter(expr, at.astimezone(ZoneInfo(zone)), day_or=False).get_next(datetime)


class Scheduler:
    def __init__(self, store, execution):
        self.store, self.execution = store, execution
        self.swept = None
        self.stranded = None
        self.stall_checked = None
        self.goals_checked = None

    def tick(self, at=None):
        at = at or datetime.now(timezone.utc)
        fired, failures = [], []
        started = time.monotonic()
        with self.store.transaction() as c:
            self.execution.expire(c)
            # Interrupted work that needs no eyes is settled here, so what a person is shown
            # is only the runs that used tools before they stopped.
            self.execution.auto_reconcile(c)
            self.execution.release_deploy_drains(c)
            self.execution.hold_unrunnable(c)
            placement.sweep(c, self.execution)            # an active bot with no computer gets one when one can take it
            H.lift_cooled_quarantines(c)
            rows = c.execute("SELECT s.*,coalesce(sc.timezone,'America/Los_Angeles') AS timezone "
                             "FROM schedules s JOIN bots b ON b.slug=s.bot LEFT JOIN schedule_config sc ON sc.schedule_id=s.id "
                             "WHERE b.state='active' AND coalesce(sc.enabled,1)=1 AND s.deleted_at IS NULL "
                             "AND s.event_name IS NULL "
                             "AND NOT EXISTS(SELECT 1 FROM bot_config bc WHERE bc.bot=s.bot "
                             "AND coalesce(json_extract(bc.config_json,'$.shared_from'),'')<>'') "
                             # a starter bot runs nothing on its own until it says its setup is done
                             "AND NOT EXISTS(SELECT 1 FROM bot_config pc WHERE pc.bot=s.bot "
                             "AND pc.onboarding_state IN " + PARKED_SQL + ")").fetchall()   # `on:` routines fire from routines.emit
            for row in rows:
                created = None
                with isolated(c, "schedule", row["id"], failures):
                    due = H.parse_ts(row["next_due"])
                    if due and due.tzinfo is None:
                        due = due.replace(tzinfo=ZoneInfo(row["timezone"]))
                    if not due:
                        # Initialize after the last recorded fire; a new schedule starts in the future.
                        base = H.parse_ts(row["last_fired"]) or at
                        if base.tzinfo is None:
                            base = base.replace(tzinfo=ZoneInfo(row["timezone"]))
                        due = next_due(row["cron"], base, row["timezone"])
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
                            existing = routines.latest_task(c, row["id"])
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
                                          refs={"task": tid, "wake": "occurrence"})
                            else:
                                task = routines.open_task(c, self.execution.auth, row, row["title"],
                                                          row["playbook"] or routines.DEFAULT_TEXT, stamp(at))
                                tid, outcome = task["id"], "created"
                                created = tid
                            c.execute("INSERT INTO schedule_occurrences VALUES(?,?,?,?)", (row["id"], when, tid, outcome))
                            H.event(c, H.KEEPER, "schedule.occurrence", row["id"], {"occurrence": when, "task": tid, "outcome": outcome})
                        following = next_due(row["cron"], at, row["timezone"])
                        c.execute("UPDATE schedules SET last_fired=?,next_due=? WHERE id=?", (when, stamp(following), row["id"]))
                    if created:
                        fired.append(created)
            # a merged pull request whose push record arrived after the deploy still ships
            from .github import ship_deployed
            ship_deployed(c, self.store.settings)
            for row in H.tasks_due_for_bots(c, stamp(at + timedelta(days=1))[:10]):
                with isolated(c, "reminder", row["id"], failures):
                    due = H.parse_ts(row.get("due"))
                    if due and due.tzinfo is None:
                        # Legacy rows allowed dates/local timestamps. New API writes require an
                        # offset; preserve the old machine's Pacific interpretation on migration.
                        due = due.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
                    if not due or due > at or not row["owner"].startswith("bot:"):
                        continue
                    if c.execute("SELECT 1 FROM task_reminders WHERE task_id=? AND due=?", (row["id"], row["due"])).fetchone():
                        continue
                    if H.bot(c, H.actor_id(row["owner"]))["state"] != "active":
                        continue
                    try:
                        H.say(c, H.KEEPER, row["owner"], "Due: " + row["title"], kind="notice",
                              conversation_id=row["conversation_id"], refs={"task": row["id"], "wake": "due"})
                    except H.Refused as exc:
                        H.event(c, H.KEEPER, "task.reminder-refused", row["id"], {"reason": str(exc)[:300]})
                    c.execute("INSERT INTO task_reminders VALUES(?,?,?)", (row["id"], row["due"], stamp(at)))
            c.execute("INSERT INTO service_health VALUES('scheduler',?,?,?) ON CONFLICT(service) DO UPDATE SET "
                      "last_success=excluded.last_success,last_error=excluded.last_error,detail_json=excluded.detail_json",
                      (stamp(at), "schedule errors" if failures else None,
                       # How long the tick held the write lock, so a slow one shows in service_health.
                       encode({"failures": failures, "tick_ms": round((time.monotonic() - started) * 1000)})))
        # Updates (backend/updates.py): today's queue, one bot asked at a time. Its own short
        # transaction, so a failure here never holds back the routines above.
        try:
            from . import updates
            with self.store.transaction() as c:
                updates.dispatch(c, at)
        except Exception as exc:
            failures.append({"updates": type(exc).__name__})
        if self.swept is None or at - self.swept >= timedelta(hours=1):
            # Outside the scheduling transaction: the sweep takes its own short write
            # locks. Marked first so a failing sweep retries hourly, not every tick.
            self.swept = at
            with self.store.transaction() as c:
                c.execute("DELETE FROM service_health WHERE service LIKE 'background:%' AND julianday(json_extract(detail_json,'$.failed_at')) < julianday(?) - 30", (stamp(at),))
            sweep_idempotency(self.store, stamp(at))
            sweep_mail(self.store, stamp(at))
            from . import events
            events.sweep(self.store, stamp(at))            # live events keep a day
        if self.goals_checked is None or at - self.goals_checked >= timedelta(hours=1):
            # Data goes stale as time passes, so every goal's automatic colour is worked out again once an
            # hour, whether or not a reading arrived (a person's colour is only ever suggested over).
            self.goals_checked = at
            try:
                with self.store.transaction() as c:
                    G.refresh(c)
            except Exception as exc:
                failures.append({"goals": type(exc).__name__})
        if self.stall_checked is None or at - self.stall_checked >= timedelta(minutes=1):
            # Every minute: bot tasks nothing is going to move (H.wake_stalled).
            self.stall_checked = at
            with self.store.transaction() as c:
                H.wake_stalled(c, stamp(at))
        if self.stranded is None or at - self.stranded >= timedelta(days=1):
            # Once a day: bot tasks parked as waiting with nothing left to wait on.
            self.stranded = at
            with self.store.transaction() as c:
                H.sweep_stranded(c, stamp(at))
        return {"fired": fired, "failures": failures}
