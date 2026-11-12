# pyright: strict
"""The server's slow drains, beside the 10-second loop (backend/app.py `schedule_loop`), not inside it.

The automation queue (its runs, then their Workspace Gmail actions), the server's own email sends and the Workspace key
check call models, connectors and Google, so one pass of them can take as long as those answer. Each has one thread of
its own here. The loop's pass, the automation doorbell and a manual run wake it; it runs at most one pass at a time, and
a wake while a pass runs asks for one more pass after it, never a second one beside it.

A pass has a short elapsed-time `Budget` (BUDGET seconds): once it is spent the pass claims no new run, action or email,
and the worker starts its next pass at once, so the queue keeps draining as fast as before. A call already started
finishes on the worker within its own timeout. The rows' own claims and leases (automation_runs.claimed_at,
automation_mail_actions.claimed_at and tries, email_sends.lease_id and permits) stay the guard against doing a row twice,
across a restart too, and their upkeep (runs cut off, expired Gmail action leases, a run that waited a day) stays on the
loop, in `automations.tick`, whatever the workers are doing.

Without workers (tests, a demo, a server whose scheduler is off) every caller drains inline, as it always did."""

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from .store import H, encode

LOG = logging.getLogger("tico.drains")

BUDGET = 5.0  # seconds a pass may claim new work for
MANUAL_WAIT = 60.0  # how long a manual run's answer waits for the worker's pass that takes it
QUIET_HEALTH = 60.0  # an idle pass rewrites its health row at most this often
# Each drain's failures go to telemetry under the name they had on the loop.
TELEMETRY: dict[str, str] = {
    "automations": "automations",
    "email": "email_drain",
    "workspace_check": "google_workspace",
}


class Stop(Protocol):
    def is_set(self) -> bool: ...


Work = Callable[["Budget"], Any]
OnError = Callable[[str, BaseException], Any]


class Budget:
    """A pass's elapsed-time budget: `left()` is False once `seconds` have gone by or the server is stopping. None
    seconds is no limit (an inline drain, as before). `spent` says whether the budget, not the queue, ended the pass."""

    def __init__(self, seconds: float | None = None, stop: Stop | None = None) -> None:
        self.seconds, self.stop = seconds, stop
        self.until = None if seconds is None else time.monotonic() + seconds
        self.spent = False

    def left(self) -> bool:
        if (self.stop is not None and self.stop.is_set()) or (
            self.until is not None and time.monotonic() >= self.until
        ):
            self.spent = True
            return False
        return True

    def again(self) -> "Budget":
        """A fresh budget of the same length, for the next stage of the same pass."""
        return Budget(self.seconds, self.stop)


def left(budget: Budget | None) -> bool:
    """True when there is time to claim one more row; no budget is no limit."""
    return budget is None or budget.left()


class Worker:
    """One drain on one thread. `work(budget)` is one pass; it returns how many rows it did (or None)."""

    def __init__(
        self,
        name: str,
        work: Work,
        *,
        store: Any = None,
        budget: float | None = BUDGET,
        on_error: OnError | None = None,
        stopping: Callable[[], bool] | None = None,
    ) -> None:
        self.name, self.work, self.store, self.budget, self.on_error = name, work, store, budget, on_error
        self.stopping = stopping  # the server's own stop, read as well as `stop` (an asyncio.Event's is_set)
        self._wake = threading.Event()
        self._cond = threading.Condition()
        self._asked = 0  # wakes asked so far
        self._covered = 0  # the wakes the last finished pass answered
        self._failures: list[dict[str, str]] = []  # failures not yet on the scheduler's health row
        self._written: float | None = None  # when this drain's health row was last written (monotonic)
        self._failed = False  # whether that row shows a failure (the next pass clears it)
        self.stop = threading.Event()
        self.passes = 0
        self.last: dict[str, Any] = {}  # the last pass: started, ms, done, spent, failure

    def wake(self) -> int:
        """Ask for a pass; returns a ticket for `wait`. While a pass runs, this is the one more pass after it."""
        with self._cond:
            self._asked += 1
            ticket = self._asked
        self._wake.set()
        return ticket

    def wait(self, ticket: int, timeout: float | None) -> bool:
        """Wait until a pass that started after the wake with `ticket` has finished; False when `timeout` ran out."""
        with self._cond:
            return self._cond.wait_for(lambda: self._covered >= ticket, timeout)

    def take_failures(self) -> list[dict[str, str]]:
        with self._cond:
            out, self._failures = self._failures, []
        return out

    def pass_once(self) -> dict[str, Any]:
        """One pass, on the calling thread (the worker's own, or a test's)."""
        self._wake.clear()  # before reading the count: a wake after this point is the next pass's
        with self._cond:
            covers = self._asked
        budget = Budget(self.budget, self)
        started, at = time.monotonic(), H.now()
        done = failure = None
        try:
            done = self.work(budget)
        except Exception as exc:  # noqa: BLE001 - a failing pass waits for the next one, as on the loop
            failure = type(exc).__name__
            LOG.warning("%s drain failed: %s", self.name, failure)
            if self.on_error:
                self.on_error(self.name, exc)
        self.last = {
            "started": at,
            "ms": round((time.monotonic() - started) * 1000),
            "done": done,
            "spent": budget.spent and not self.is_set(),
            "failure": failure,
        }
        self.passes += 1
        with self._cond:
            if failure:
                self._failures.append({self.name: failure})
            self._covered = max(self._covered, covers)
            self._cond.notify_all()
        self._health(self.last)
        return self.last

    def _health(self, last: dict[str, Any]) -> None:
        """This drain's last pass on its own service_health row (`drain:<name>`): when, how long, how much, and its
        failure. A quiet pass (nothing done, nothing failed) rewrites it at most once a minute."""
        if self.store is None:
            return
        now = time.monotonic()
        quiet = not last["done"] and not last["failure"] and not self._failed
        if quiet and self._written and now - self._written < QUIET_HEALTH:
            return
        self._written, self._failed = now, bool(last["failure"])
        try:
            with self.store.transaction() as c:
                c.execute(
                    "INSERT INTO service_health(service,last_succeeded_at,last_error,detail) VALUES(?,?,?,?) "
                    "ON CONFLICT(service) DO UPDATE SET last_succeeded_at=coalesce(excluded.last_succeeded_at,"
                    "service_health.last_succeeded_at),last_error=excluded.last_error,detail=excluded.detail",
                    (
                        "drain:" + self.name,
                        None if last["failure"] else last["started"],
                        last["failure"],
                        encode(
                            {
                                "at": last["started"],
                                "ms": last["ms"],
                                "done": last["done"] or 0,
                                "budget_spent": last["spent"],
                                "budget_s": self.budget,
                            }
                        ),
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - the row waits for the next pass
            LOG.warning("%s drain health not written: %s", self.name, type(exc).__name__)

    def halt(self) -> None:
        """Stop the worker: no new claims, and its thread ends after the pass in progress."""
        self.stop.set()
        self._wake.set()

    def is_set(self) -> bool:
        """Whether the worker is stopping: its own stop, or the server's."""
        return self.stop.is_set() or bool(self.stopping and self.stopping())

    def run(self) -> None:
        """The worker's thread until `stop`: a pass per wake, and straight away another while the budget, not the
        queue, ended the last one."""
        while not self.is_set():
            self._wake.wait(timeout=1.0)
            if self.is_set():
                break
            if not self._wake.is_set():
                continue
            last = self.pass_once()
            if last["spent"]:
                self._wake.set()  # work is left: the next pass carries on from where this one stopped


class Drains:
    """The server's drain workers: `automations` (the queue's runs, then their Gmail actions), `email` (Workspace
    inboxes' queued sends) and `workspace_check` (a pending or failing Workspace key, checked again)."""

    def __init__(
        self,
        store: Any,
        auth: Any,
        *,
        budget: float | None = BUDGET,
        on_error: OnError | None = None,
        stopping: Callable[[], bool] | None = None,
    ) -> None:
        from . import email_sending, system_routines
        from . import google_workspace as workspace_checks

        hub = store.automations
        self.automations = Worker(
            "automations",
            lambda budget: hub.drain(budget=budget),
            store=store,
            budget=budget,
            on_error=on_error,
            stopping=stopping,
        )
        self.email = Worker(
            "email",
            lambda budget: email_sending.drain(store, auth, budget=budget),  # pyright: ignore[reportUnknownMemberType]
            store=store,
            budget=budget,
            on_error=on_error,
            stopping=stopping,
        )
        self.workspace_check = Worker(
            "workspace_check",
            lambda budget: system_routines.pass_of(  # pyright: ignore[reportUnknownMemberType]
                store,
                "workspace_check",
                workspace_checks.tick,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                store,
            ),
            budget=None,
            on_error=on_error,
            stopping=stopping,
        )
        self.all = (self.automations, self.email, self.workspace_check)
        self.hub = hub

    def start(self) -> "Drains":
        """Each worker on a daemon thread of its own; the automation hub's doorbell and manual run wake its worker."""
        self.threads: list[threading.Thread] = []
        for worker in self.all:
            thread = threading.Thread(target=worker.run, name="tico-drain-" + worker.name, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.hub.worker = self.automations
        return self

    def wake(self) -> None:
        for worker in self.all:
            worker.wake()

    def take_failures(self) -> list[dict[str, str]]:
        return [f for worker in self.all for f in worker.take_failures()]

    def stop(self, timeout: float | None = None) -> None:
        """No new claims from here; a call already started finishes within its own timeout (or, past `timeout`, is
        left as a restart leaves it, for the leases' upkeep)."""
        if getattr(self.hub, "worker", None) is self.automations:
            self.hub.worker = None
        for worker in self.all:
            worker.halt()
        end = None if timeout is None else time.monotonic() + timeout
        for thread in getattr(self, "threads", ()):
            # One deadline for all three: a shutdown waits at most `timeout`, not `timeout` per worker.
            thread.join(None if end is None else max(0.0, end - time.monotonic()))
