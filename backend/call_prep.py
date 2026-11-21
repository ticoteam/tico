# pyright: strict
"""Scheduled calls before they start (docs/meet.md, Scheduled calls): the bots' prep, and the calendar kept in charge.

The calendar is where a scheduled call's time lives. Before this sweep acts on a call it reads the call's one event
again, live (calendar_api.read_call_event): an event that moved moves the call, and one cancelled or deleted cancels
it. An event made all-day is not followed: a call needs a time, so it keeps the one it has. An event that cannot be
read now is read again on the next pass; after READ_TRIES passes in a row the call is prepped from the times it has,
but never ended `missed` from them: only an end the calendar confirmed ends it. The count is kept in memory, so a
restart only reads the event a few more times.

The sweep is the fallback. A change to a call's event that Tico hears (the Google watcher, a write through Tico's
calendar) has that call read again at once (`follow`, calendar_events.CALL_FOLLOWERS), and the first join reads it
live too (`at_join`). A move that puts the start more than PREP_LEAD away clears `prepped_at`: the bots prep again
for the new time.

Prep is a skill plus normal tasks. PREP_LEAD before the start (at once for a call booked closer than that), each bot on
the call gets one task, "Prepare for <title>", from the person who booked it, that runs the built-in meeting-prep skill
(skills/meeting-prep/SKILL.md) for the call's thread; the thread gets the task's quiet "Started task" line. The task is
private when the thread is (anything but a company thread), it goes to exactly the bot on the call, and none is given
when the booker can no longer read the thread. The tasks and `prepped_at` are one write that only a call still
unprepped makes, so a pass that runs twice (two processes, a restart part-way through) gives them once. A bot that
joins a live call without prep gets the same kind of task from the person who added it.

A scheduled call nobody joined ends `missed` MISSED_AFTER its end. One pass spends at most SWEEP_BUDGET; the calls it
did not reach wait for the next minute, so the heartbeat is never held up.
"""

import logging
import threading
import weakref
import time
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from . import calendar_api, calendar_events
from .store import H as _H
from .store import Problem

log = logging.getLogger("tico.call_prep")
Row = dict[str, Any]
# hubdb's helpers are untyped; strict mode reads them through Any.
H: Any = _H

PREP_LEAD = timedelta(minutes=30)
MISSED_AFTER = timedelta(hours=1)
READ_TRIES = 3
BATCH = 50  # calls one pass looks at, soonest first; the rest wait a minute
SWEEP_BUDGET = 20.0  # seconds one pass may spend; the heartbeat's minute steps run after it
SKILL = "skills/meeting-prep/SKILL.md"


def _stored(text: object) -> str:
    """A calendar time in the format calls store times in (hubdb.now)."""
    at = datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc)
    return at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def instructions(row: Row, live: bool) -> str:
    phase = "already live" if live else "scheduled"
    return (
        f"Prepare for the call {row['title']!r} (call {row['id']}) in thread {row['thread_id']}. "
        f"The call is {phase}. Read Tico's {SKILL} (under HUB_DIR) and follow it."
    )


class Prep:
    def __init__(self, app: Any, store: Any, calls: Any) -> None:
        self.app, self.store, self.calls = app, store, calls
        self.lock = threading.Lock()
        self.unread: dict[str, int] = {}  # call_id -> passes in a row its event could not be read
        self.following: list[threading.Thread] = []
        with store.read() as c:
            calendar_events.CALL_FOLLOWERS[str(H._main_path(c))] = weakref.WeakMethod(self.follow)

    def sweep(self) -> dict[str, int]:
        """One pass (the scheduler's, every minute): the scheduled calls due their prep, or past their end. Calls still
        to prep come first, so ones already prepped and waiting on an unreadable calendar never crowd them out."""
        now: str = H.now()
        deadline = time.monotonic() + SWEEP_BUDGET
        with self.store.read() as c:
            ids: list[str] = [
                r["id"]
                for r in c.execute(
                    # A call past its end started before it, so both kinds are a range of calls_scheduled.
                    "SELECT id FROM calls WHERE state='scheduled' AND scheduled_start<=? AND (prepped_at IS NULL "
                    "OR scheduled_end<=?) ORDER BY prepped_at IS NOT NULL, scheduled_start, id LIMIT ?",
                    (
                        H.shift(now, seconds=PREP_LEAD.total_seconds()),
                        H.shift(now, seconds=-MISSED_AFTER.total_seconds()),
                        BATCH,
                    ),
                )
            ]
        done: dict[str, int] = {}
        for index, call_id in enumerate(ids):
            if index and time.monotonic() > deadline:
                done["deferred"] = len(ids) - index
                break
            try:
                outcome = self.one(call_id)
            except Exception as exc:  # noqa: BLE001 - one call's failure must not stop the others
                from . import batch_work

                cast(Any, batch_work).report("call_prep", call_id, exc, logger="tico.call_prep")
                outcome = "failed"
            if outcome:
                done[outcome] = done.get(outcome, 0) + 1
        return done

    def follow(self, call_id: str) -> None:
        """Read a call again now, its event having changed (an after-commit hook: the read runs on its own thread,
        since the write that heard the change still holds the writer)."""
        worker = threading.Thread(target=self._follow_now, args=(call_id,), name="call-follow", daemon=True)
        with self.lock:
            self.following = [t for t in self.following if t.is_alive()]
            self.following.append(worker)
        worker.start()

    def _follow_now(self, call_id: str) -> None:
        try:
            self.one(call_id)
        except Exception:  # noqa: BLE001 - logged; the minute sweep reads the call again
            log.warning("call_prep: call %s did not follow its event", call_id, exc_info=True)

    def settle(self, timeout: float = 10.0) -> None:
        """Wait for the re-reads `follow` started (tests, shutdown)."""
        with self.lock:
            workers = list(self.following)
        for worker in workers:
            worker.join(timeout)

    def _scheduled(self, call_id: str) -> bool:
        with self.store.read() as c:
            row = c.execute("SELECT state FROM calls WHERE id=?", (call_id,)).fetchone()
        return row is not None and row["state"] == "scheduled"

    def _read(self, call_id: str, count: bool = True) -> tuple[bool, Row | None]:
        """(read, event): whether the calendar answered, and the event it has (None: cancelled or gone)."""
        try:
            with self.store.read() as c:
                event = calendar_api.read_call_event(c, call_id)
        except Problem as exc:
            log.info("call_prep: the event of call %s could not be read: %s", call_id, cast(str, exc.code))
            if count:
                with self.lock:
                    self.unread[call_id] = self.unread.get(call_id, 0) + 1
            return False, None
        with self.lock:
            self.unread.pop(call_id, None)
        return True, event

    def one(self, call_id: str) -> str | None:
        """One call's pass. Returns what it did: prepped, moved, cancelled, missed, waiting (its event unread), or
        None when it had nothing to do."""
        if not self._scheduled(call_id):
            return None
        read, event = self._read(call_id)
        if not read:
            with self.lock:
                if self.unread.get(call_id, 0) < READ_TRIES:
                    return "waiting"
                self.unread.pop(call_id, None)
        now: str = H.now()
        with self.store.transaction() as c:
            found = c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            if found is None or found["state"] != "scheduled":
                return None
            row: Row = dict(found)
            outcome = self._apply(c, row, read, event, now)
            if outcome == "cancelled":
                return outcome
            if read and row["scheduled_end"] <= H.shift(now, seconds=-MISSED_AFTER.total_seconds()):
                self.calls.cancel(c, call_id, "missed")
                return "missed"
            if row["prepped_at"] or row["scheduled_start"] > H.shift(now, seconds=PREP_LEAD.total_seconds()):
                return outcome
            self._prep(c, row, now)
            return "prepped"

    def at_join(self, call_id: str) -> None:
        """The first join of a scheduled call reads its event live before the call starts: a cancelled one is refused
        (and the call cancelled), a moved one moves the call. A calendar that cannot be read now lets the join go on
        with the times the call has."""
        read, event = self._read(call_id, count=False)
        if not read:
            return
        with self.store.transaction() as c:
            found = c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            if found is None or found["state"] != "scheduled":
                return
            outcome = self._apply(c, dict(found), True, event, H.now())
        if outcome == "cancelled":
            raise Problem("call_cancelled", "This call was cancelled on its calendar", 409)

    def _apply(self, c: Any, row: Row, read: bool, event: Row | None, now: str) -> str | None:
        """Bring a scheduled call to its event as just read, in the caller's write: cancelled, moved (`row` updated in
        place), or None. An all-day event is logged and not followed."""
        if read and event is None:
            self.calls.cancel(c, row["id"], "calendar_cancelled")
            return "cancelled"
        if event is None:
            return None
        if event.get("all_day"):
            log.info("call_prep: the event of call %s is all-day now; the call keeps its times", row["id"])
            return None
        start, end = _stored(event["start"]), _stored(event["end"])
        if (start, end) == (row["scheduled_start"], row["scheduled_end"]):
            return None
        self.calls.move(c, row, start, end, now)
        return "moved"

    def _prep(self, c: Any, row: Row, now: str) -> None:
        """Each bot on the call gets its prep task, then the call is marked prepped, in the caller's write. A booker who
        can no longer read the thread gives none: the task would carry the thread to them."""
        tasks: list[str] = []
        if self.calls.readable(c, row["connector_actor"], row["thread_id"]):
            tasks = self._tasks(c, row, live=False)
        else:
            log.info("call_prep: call %s gets no prep: its booker cannot read its thread", row["id"])
        c.execute("UPDATE calls SET prepped_at=?,updated_at=? WHERE id=? AND prepped_at IS NULL", (now, now, row["id"]))
        self.calls._record(c, row["connector_actor"], row["id"], row["thread_id"], "call.prepped", {"tasks": tasks})

    def live(self, c: Any, row: Row) -> None:
        self._tasks(c, row, live=True)

    def attached_to_meeting(self, c: Any, meeting_id: str, added: list[str]) -> None:
        row = c.execute(
            "SELECT * FROM calls WHERE live_meeting_id=? AND ending_at IS NULL "
            "AND (state='live' OR (state='scheduled' AND prepped_at IS NOT NULL))",
            (meeting_id,),
        ).fetchone()
        if row is None:
            return
        if row["state"] == "live":
            self.live(c, dict(row))
        else:
            self._tasks(c, dict(row), live=False, added=added)

    def _tasks(self, c: Any, row: Row, *, live: bool, added: list[str] | None = None) -> list[str]:
        bots = c.execute(
            "SELECT b.bot,b.joined_by,n.prep_task_id,LENGTH(n.text) AS note_length "
            "FROM live_meeting_bots b LEFT JOIN call_bot_notes n ON n.call_id=? AND n.bot_id=b.bot "
            "WHERE b.meeting_id=? ORDER BY b.bot",
            (row["id"], row["live_meeting_id"]),
        ).fetchall()
        if not bots:
            return []
        from . import calls as _calls

        calls: Any = _calls
        if calls.room_owner(c, row["thread_id"]):
            return []  # a call in a person's own room (their Assistant chat) needs no prep, whoever started it
        private = bool(self.calls.thread_private(c, row["thread_id"]))
        from_attachment = live or added is not None

        def same_prep(conn: Any, task_id: str) -> bool:
            return (
                conn.execute(
                    "SELECT 1 FROM call_bot_notes WHERE call_id=? AND prep_task_id=?", (row["id"], task_id)
                ).fetchone()
                is not None
            )

        tasks: list[str] = []
        for bot in bots:
            bot_id: str = bot["bot"]
            if added is not None and bot_id not in added:
                continue
            requester: str = bot["joined_by"] if from_attachment else row["connector_actor"]
            if from_attachment and (bot["prep_task_id"] or bot["note_length"]):
                continue
            if from_attachment and not self.calls.attachment_standing(c, requester, row["thread_id"], bot_id):
                continue
            owner = "bot:" + bot_id
            # A bot that cannot take the task (paused, gone, refused by a rule, handed elsewhere) costs only its own.
            c.execute("SAVEPOINT call_prep_task")
            try:
                made: Row = H.task_create(
                    c,
                    requester,
                    "Prepare for " + row["title"],
                    "",
                    owner,
                    conversation_id=row["thread_id"],
                    lint=False,
                    same=same_prep,
                    private=private,
                    wake_refs={"call_prep": row["id"]},
                    instructions=instructions(row, live),
                )
                if made.get("owner_id") != owner:
                    raise Problem("rerouted", f"the task went to {made.get('owner_id')}, not {owner}", 409)
                c.execute(
                    "INSERT INTO call_bot_notes(call_id,bot_id,prep_task_id) VALUES(?,?,?) "
                    "ON CONFLICT(call_id,bot_id) DO UPDATE SET prep_task_id=excluded.prep_task_id",
                    (row["id"], bot_id, made["id"]),
                )
                tasks.append(made["id"])
            except (H.Refused, Problem) as exc:
                c.execute("ROLLBACK TO call_prep_task")
                log.info("call_prep: %s got no prep task for call %s: %s", bot_id, row["id"], str(exc))
            c.execute("RELEASE call_prep_task")
        return tasks
