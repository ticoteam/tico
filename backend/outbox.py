"""The mechanics the delivery outboxes share: webhook sends, the finance writes and the postal
letters' retries. Each outbox keeps its own table and its own state names; this module only knows the table it is
given (`Table`) and the columns and states that describe a row's life.

- **Lease.** A worker claims a due row by moving it from its due state to its flight state and stamping the lease
  column, in a short transaction, before it calls out (`claim`, `claim_next`). A row still in flight whose lease is older
  than the stale window was cut off mid-call: `reclaim_stale` puts it back to due, and the call is made again.
- **Schedule.** `Waits` gives the seconds to wait after each failed attempt; past the last one the row gives up, unless
  `repeat_last` keeps the last wait for good. `Doubling` is an open-ended exponential wait (no give-up).
- **Settling.** `settle_failure` records the attempt count and the error, then either returns the row to its due state
  at the next time or moves it to its give-up state. `finish` writes any other terminal state.
- **Idempotency.** The row's id is the key the receiver sees, and it never changes across attempts. A row that has
  left its due state (sent, given up, canceled) is never claimed again, so a repeated send of it is refused by the claim
  rather than repeated on the wire.
- **Log line.** `note` writes one line per outcome and counts it in-process (`counts`).

Times are whatever the caller stores (ISO text or epoch seconds): this module compares them and passes them on, and the
caller does the arithmetic through `later`.
"""
import logging
import random
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Any

LOG = logging.getLogger(__name__)

_lock = threading.Lock()
_counts = Counter()


@dataclass(frozen=True)
class Table:
    """Where one outbox keeps its rows. The names are the caller's constants, never user input."""
    name: str
    due: str                       # the state of a row waiting to be sent
    flight: str                    # the state while a worker holds it (its lease is set)
    due_at: str = "next_attempt_at"
    lease: str = "claimed_at"
    attempts: str = "attempts"
    error: str = "last_error"
    id: str = "id"


@dataclass(frozen=True)
class Waits:
    """Seconds to wait after the 1st, 2nd... failed attempt. With `repeat_last` the last wait repeats and nothing gives
    up; otherwise the attempt after the last wait is the one that gives up. `jitter` spreads each wait by up to that
    fraction either way."""
    seconds: tuple[float, ...]
    jitter: float = 0.0
    repeat_last: bool = False

    @property
    def max_attempts(self) -> int | None:
        return None if self.repeat_last else len(self.seconds) + 1

    def after(self, failed: int, rng: Any = random) -> float | None:
        """Seconds until the next attempt after `failed` failed attempts, or None when none are left."""
        if failed < 1:
            raise ValueError("failed must count at least one attempt")
        if self.repeat_last:
            base = self.seconds[min(failed - 1, len(self.seconds) - 1)]
        elif failed > len(self.seconds):
            return None
        else:
            base = self.seconds[failed - 1]
        return base * rng.uniform(1 - self.jitter, 1 + self.jitter) if self.jitter else base

    def wait_for(self, failed: int) -> float:
        """The wait after `failed` failed attempts, for a schedule that never gives up (`repeat_last`)."""
        wait = self.after(failed)
        if wait is None:
            raise ValueError("this schedule gives up: it has no wait past its last attempt")
        return wait


@dataclass(frozen=True)
class Doubling:
    """`first` seconds after the first failure, doubling for each one after it, never more than `cap`. The doubling
    stops after `steps` doublings. It never gives up."""
    first: float
    cap: float
    steps: int = 8

    def after(self, failed: int, rng: Any = random) -> float:
        return min(self.cap, self.first * 2 ** min(failed, self.steps))


# -------------------------------------------------------------------------------------------------- claim and lease
def claim(c, table, row_id, now):
    """Take one row: due to flight, lease stamped `now`. True when it was ours. A row that is not due (sent, given up,
    canceled, or held by another worker) is refused, which is what makes a repeat of it a no-op."""
    moved = c.execute(f"UPDATE {table.name} SET state=?, {table.lease}=? WHERE {table.id}=? AND state=?",
                      (table.flight, now, row_id, table.due)).rowcount == 1
    if moved:
        count(table.name, "claimed", 1)
    return moved


def claim_next(c, table, now, *, accept=None, limit=100):
    """The first due row (by due time) that `accept` allows, claimed; or None. `accept` sees each candidate row."""
    rows = c.execute(f"SELECT * FROM {table.name} WHERE state=? AND {table.due_at}<=? ORDER BY {table.due_at} LIMIT ?",
                     (table.due, now, limit)).fetchall()
    for row in rows:
        if accept is not None and not accept(row):
            continue
        if claim(c, table, row[table.id], now):
            return row
    return None


def release(c, table, row_id):
    """Give a claimed row back unsent, with no attempt counted: it is due again without waiting. True when it was held."""
    return c.execute(f"UPDATE {table.name} SET state=?, {table.lease}=NULL WHERE {table.id}=? AND state=?",
                     (table.due, row_id, table.flight)).rowcount == 1


def reclaim_stale(c, table, before, now):
    """Rows still in flight whose lease was stamped before `before` go back to due at `now`. Returns how many."""
    back = c.execute(f"UPDATE {table.name} SET state=?, {table.lease}=NULL, {table.due_at}=? "
                     f"WHERE state=? AND {table.lease}<?", (table.due, now, table.flight, before)).rowcount
    if back:
        count(table.name, "reclaimed", back)
    return back


# ---------------------------------------------------------------------------------------------- settle the attempt
def finish(c, table, row_id, state, *, attempts, error, due_at, extra=None):
    """Write a row's terminal (or any non-flight) state and release its lease. `extra` adds columns the caller keeps."""
    cols = {"state": state, table.attempts: attempts, table.error: error, table.due_at: due_at, table.lease: None,
            **(extra or {})}
    assignments = ", ".join(f"{column}=?" for column in cols)
    c.execute(f"UPDATE {table.name} SET {assignments} WHERE {table.id}=?", (*cols.values(), row_id))


def settle_failure(c, table, row_id, *, attempts, error, schedule, now, later, give_up_state, rng=random, extra=None):
    """A failed attempt: back to due at `later(now, wait)` when the schedule has a wait left, else `give_up_state` at
    `now`. Returns True when the row gave up."""
    wait = schedule.after(attempts, rng)
    if wait is None:
        finish(c, table, row_id, give_up_state, attempts=attempts, error=error, due_at=now, extra=extra)
        note(table.name, "gave_up", row_id, attempts=attempts)
        return True
    finish(c, table, row_id, table.due, attempts=attempts, error=error, due_at=later(now, wait), extra=extra)
    note(table.name, "retry", row_id, attempts=attempts, wait=round(wait, 1))
    return False


# ----------------------------------------------------------------------------------------------------- log and count
def note(name, event, row_id, **fields):
    """One log line for an outcome, and the count of it. Fields are for the log only: never an error's text, which a
    provider may fill with the payment or the person it was about."""
    count(name, event)
    detail = " ".join(f"{key}={value}" for key, value in fields.items() if value not in (None, ""))
    LOG.info("outbox %s %s id=%s%s", name, event, row_id, f" {detail}" if detail else "")


def count(name, event, n=1):
    """Count an outcome without a log line, for a retry that would otherwise log once per row per try."""
    with _lock:
        _counts[(name, event)] += n


def counts():
    """What this process has done so far: {(outbox table, event): count}."""
    with _lock:
        return dict(_counts)
