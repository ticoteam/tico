# pyright: strict
"""Scheduled calls before they start (docs/meet.md, Scheduled calls): the bots' prep, and the calendar kept in charge.

The calendar is where a scheduled call's time lives. Before this sweep acts on a call it reads the call's one event
again, live (calendar_api.read_call_event): an event that moved moves the call, and one cancelled or deleted cancels
it. An event that cannot be read now is read again on the next pass; after READ_TRIES passes in a row the call is
prepped from the times it has. The count is kept in memory, so a restart only reads the event a few more times.

Prep is a skill plus normal tasks. PREP_LEAD before the start (at once for a call booked closer than that), each bot on
the call gets one task, "Prepare for <title>", from the person who booked it, that runs the built-in meeting-prep skill
(skills/meeting-prep/SKILL.md) for the call's thread; the thread gets the task's quiet "Started task" line. The tasks
and `prepped_at` are one write that only a call still unprepped makes, so a pass that runs twice (two processes, a
restart part-way through) gives them once.

A scheduled call nobody joined ends `missed` MISSED_AFTER its end.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from . import calendar_api
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
SKILL = "skills/meeting-prep/SKILL.md"


def _stored(text: object) -> str:
    """A calendar time in the format calls store times in (hubdb.now)."""
    at = datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc)
    return at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def instructions(row: Row) -> str:
    return (
        f"Prepare for the call {row['title']!r} (call {row['id']}) in thread {row['thread_id']}: read Tico's {SKILL} "
        "(under HUB_DIR) and follow it."
    )


class Prep:
    def __init__(self, app: Any, store: Any, calls: Any) -> None:
        self.app, self.store, self.calls = app, store, calls
        self.lock = threading.Lock()
        self.unread: dict[str, int] = {}  # call_id -> passes in a row its event could not be read

    def sweep(self) -> dict[str, int]:
        """One pass (the scheduler's, every minute): the scheduled calls due their prep, or past their end."""
        now: str = H.now()
        with self.store.read() as c:
            ids: list[str] = [
                r["id"]
                for r in c.execute(
                    "SELECT id FROM calls WHERE state='scheduled' AND ((prepped_at IS NULL AND scheduled_start<=?) "
                    "OR scheduled_end<=?) ORDER BY scheduled_start, id LIMIT ?",
                    (
                        H.shift(now, seconds=PREP_LEAD.total_seconds()),
                        H.shift(now, seconds=-MISSED_AFTER.total_seconds()),
                        BATCH,
                    ),
                )
            ]
        done: dict[str, int] = {}
        for call_id in ids:
            try:
                outcome = self.one(call_id)
            except Exception as exc:  # noqa: BLE001 - one call's failure must not stop the others
                from . import batch_work

                cast(Any, batch_work).report("call_prep", call_id, exc, logger="tico.call_prep")
                outcome = "failed"
            if outcome:
                done[outcome] = done.get(outcome, 0) + 1
        return done

    def _read(self, call_id: str) -> tuple[bool, Row | None]:
        """(read, event): whether the calendar answered, and the event it has (None: cancelled or gone)."""
        try:
            with self.store.read() as c:
                event = calendar_api.read_call_event(c, call_id)
        except Problem as exc:
            log.info("call_prep: the event of call %s could not be read: %s", call_id, cast(str, exc.code))
            with self.lock:
                self.unread[call_id] = self.unread.get(call_id, 0) + 1
            return False, None
        with self.lock:
            self.unread.pop(call_id, None)
        return True, event

    def one(self, call_id: str) -> str | None:
        """One call's pass. Returns what it did: prepped, moved, cancelled, missed, waiting (its event unread), or
        None when it had nothing to do."""
        read, event = self._read(call_id)
        if not read:
            with self.lock:
                if self.unread.get(call_id, 0) < READ_TRIES:
                    return "waiting"
                self.unread.pop(call_id, None)
        now: str = H.now()
        moved = False
        with self.store.transaction() as c:
            found = c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            if found is None or found["state"] != "scheduled":
                return None
            row: Row = dict(found)
            if read and event is None:
                self.calls.cancel(c, call_id, "calendar_cancelled")
                return "cancelled"
            if event is not None:
                start, end = _stored(event["start"]), _stored(event["end"])
                if (start, end) != (row["scheduled_start"], row["scheduled_end"]):
                    c.execute(
                        "UPDATE calls SET scheduled_start=?,scheduled_end=?,updated_at=? WHERE id=?",
                        (start, end, now, call_id),
                    )
                    self.calls._record(
                        c,
                        row["connector_actor"],
                        call_id,
                        row["thread_id"],
                        "call.moved",
                        {"starts_at": start, "ends_at": end},
                    )
                    row.update(scheduled_start=start, scheduled_end=end)
                    moved = True
            if row["scheduled_end"] <= H.shift(now, seconds=-MISSED_AFTER.total_seconds()):
                self.calls.cancel(c, call_id, "missed")
                return "missed"
            if row["prepped_at"] or row["scheduled_start"] > H.shift(now, seconds=PREP_LEAD.total_seconds()):
                return "moved" if moved else None
            self._prep(c, row, now)
            return "prepped"

    def _prep(self, c: Any, row: Row, now: str) -> None:
        """Each bot on the call gets its prep task, then the call is marked prepped, in the caller's write."""
        bots: list[str] = [
            r["bot"]
            for r in c.execute(
                "SELECT bot FROM live_meeting_bots WHERE meeting_id=? ORDER BY bot", (row["live_meeting_id"],)
            )
        ]
        tasks: list[str] = []
        for bot in bots:
            # A bot that cannot take the task (paused, gone, refused by a rule) costs only its own task.
            c.execute("SAVEPOINT call_prep_task")
            try:
                made: Row = H.task_create(
                    c,
                    row["connector_actor"],
                    "Prepare for " + row["title"],
                    "",
                    "bot:" + bot,
                    conversation_id=row["thread_id"],
                    lint=False,
                    deduplicate=False,
                    private=False,
                    instructions=instructions(row),
                )
                tasks.append(made["id"])
            except (H.Refused, Problem) as exc:
                c.execute("ROLLBACK TO call_prep_task")
                log.info("call_prep: %s got no prep task for call %s: %s", bot, row["id"], str(exc))
            c.execute("RELEASE call_prep_task")
        c.execute("UPDATE calls SET prepped_at=?,updated_at=? WHERE id=? AND prepped_at IS NULL", (now, now, row["id"]))
        self.calls._record(c, row["connector_actor"], row["id"], row["thread_id"], "call.prepped", {"tasks": tasks})
