# pyright: strict
"""The server's own background workers: the loops and threads the app's lifespan starts (backend/app.py `_lifespan`),
and the way they end.

- A worker that dies of an uncaught exception is never silent. It is logged as an error with its traceback, sent to
  telemetry (`worker:<name>`, the exception's type only), and recorded in registry_metadata (`worker-died:<name>`: the
  type and the time, never the message), which the production check `worker_died` (backend/invariants.py) reads: Health
  shows it, and Check health files it for Agent Ops on its first run. A worker is not restarted; the next start of the
  server starts them all again, so it clears the records (`forget`).
- The way out (`shutdown`) signals every stop first, then waits for every worker together, under one deadline
  (SHUTDOWN_SECONDS). A worker still running at the deadline is cancelled and named in the log. Cancelling the await of a
  worker on a thread (`thread`) does not stop the thread, so each such thread still running is given THREAD_GRACE_SECONDS
  more, and named in the log if it runs on. Then the cleanups run, each on its own and in the order registered, whatever a
  worker or another cleanup did; the lifespan closes telemetry and the store after them, even when the way out is
  itself cancelled.
- A start that fails part way runs the same way out over what it had started (backend/app.py wraps the start in it).
"""

import asyncio
import inspect
import json
import logging
import threading
import time
from collections.abc import Callable, Coroutine
from typing import Any, Protocol

from .store import H

log = logging.getLogger("tico.lifespan_workers")

# The scheduler's own way out waits up to 90 s for its drains and its outside worker (backend/app.py `schedule_loop`).
SHUTDOWN_SECONDS = 120.0
CANCEL_SECONDS = 5.0  # a cancelled coroutine worker this long without ending is left behind
THREAD_GRACE_SECONDS = 5.0  # a cancelled worker's thread this long without ending is left running, and named
PREFIX = "worker-died:"  # registry_metadata key per dead worker: {"error": type name, "at": ISO time}
_PREFIX_END = PREFIX[:-1] + chr(
    ord(PREFIX[-1]) + 1
)  # the key range's end: every key that starts with PREFIX is below it


class Store(Protocol):
    def transaction(self) -> Any: ...


class Telemetry(Protocol):
    def capture(self, source: str, exc: BaseException | None = None) -> Any: ...


class Workers:
    """One run of the lifespan's workers, the stops that end them and the cleanups after them."""

    def __init__(self, store: Store, telemetry: Telemetry):
        self.store = store
        self.telemetry = telemetry
        self.tasks: list[tuple[str, asyncio.Task[Any]]] = []
        self.threads: list[tuple[str, threading.Event, threading.Event]] = []  # (name, started, finished)
        self.stops: list[tuple[str, Callable[[], object]]] = []
        self.cleanups: list[tuple[str, Callable[[], object]]] = []
        self.records: list[asyncio.Future[None]] = []
        self.stopping = False

    def task(self, name: str, work: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Starts `work` as a worker named `name`."""
        task = asyncio.create_task(work, name=f"tico-{name}")
        task.add_done_callback(lambda done: self._ended(name, done))
        self.tasks.append((name, task))
        return task

    def thread(self, name: str, work: Callable[..., object], *args: object) -> asyncio.Task[Any]:
        """Starts `work(*args)` on a thread as a worker named `name`; the way out knows whether its thread still runs."""
        started, finished = threading.Event(), threading.Event()

        def run() -> object:
            started.set()
            try:
                return work(*args)
            finally:
                finished.set()

        self.threads.append((name, started, finished))
        return self.task(name, asyncio.to_thread(run))

    def on_stop(self, name: str, stop: Callable[[], object]) -> None:
        """`stop()` runs first on the way out, before any worker is waited for."""
        self.stops.append((name, stop))

    def on_exit(self, name: str, cleanup: Callable[[], object]) -> None:
        """`cleanup()` (or the awaitable it returns) runs after the workers end, in the order registered."""
        self.cleanups.append((name, cleanup))

    def _ended(self, name: str, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        log.error("Background worker %s died: %s", name, type(exc).__name__, exc_info=exc)
        try:
            self.telemetry.capture(f"worker:{name}", exc)
        except Exception:  # noqa: BLE001 - telemetry must never hide the death it reports
            log.warning("Telemetry could not take worker %s's death", name, exc_info=True)
        if self.stopping:
            return  # on the way out: the next start runs it again, and would clear the record at once
        try:
            loop = asyncio.get_running_loop()
            self.records.append(loop.run_in_executor(None, record, self.store, name, type(exc).__name__))
        except RuntimeError:
            log.error("Worker %s's death could not be recorded for Health", name, exc_info=True)

    async def shutdown(self) -> None:
        """Every stop, then every worker under one deadline, then every cleanup (see the module docstring)."""
        self.stopping = True
        try:
            for name, stop in self.stops:
                try:
                    stop()
                except Exception:
                    log.error("Stopping %s failed", name, exc_info=True)
            await self._wait()
            await self._wait_threads()
            if self.records:
                await asyncio.gather(*self.records, return_exceptions=True)
        finally:
            await self._clean_up()

    async def _wait(self) -> None:
        if not self.tasks:
            return
        together = asyncio.gather(*(task for _, task in self.tasks), return_exceptions=True)
        try:
            await asyncio.wait_for(asyncio.shield(together), SHUTDOWN_SECONDS)
            return
        except TimeoutError:
            pass
        late = [name for name, task in self.tasks if not task.done()]
        log.error("Background workers still running %g s after their stop, cancelled: %s", SHUTDOWN_SECONDS, late)
        for _, task in self.tasks:
            task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(together), CANCEL_SECONDS)
        except TimeoutError:
            left = [name for name, task in self.tasks if not task.done()]
            log.error("Background workers did not end on their cancel, left behind: %s", left)

    async def _wait_threads(self) -> None:
        """A cancelled await of a thread leaves the thread running: give each still running a short grace, and name the
        ones that run on. The cleanups run either way (store.close closes a connection still in use once it is given
        back)."""
        running = [
            (name, finished) for name, started, finished in self.threads if started.is_set() and not finished.is_set()
        ]
        if not running:
            return

        def grace() -> None:
            end = time.monotonic() + THREAD_GRACE_SECONDS
            for _, finished in running:
                finished.wait(max(0.0, end - time.monotonic()))

        await asyncio.to_thread(grace)
        left = [name for name, finished in running if not finished.is_set()]
        if left:
            log.error("Background worker threads still running after the way out, cleaning up regardless: %s", left)

    async def _clean_up(self) -> None:
        for name, cleanup in self.cleanups:
            try:
                result = cleanup()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.error("Cleanup %s failed on the way out", name, exc_info=True)


def record(store: Store, name: str, error: str) -> None:
    """Worker `name` died of an `error` (its type's name): the production check `worker_died` reads it."""
    value = json.dumps({"error": error, "at": H.now()})
    with store.transaction() as c:
        c.execute(
            "INSERT INTO registry_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (PREFIX + name, value),
        )


def forget(store: Store) -> None:
    """A start runs every worker again: the deaths recorded before it no longer describe this server."""
    with store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key>=? AND key<?", (PREFIX, _PREFIX_END))


def dead(c: Any, limit: int = 500) -> list[str]:
    """`<name>: <error> at <time> UTC` for each worker recorded dead since this server started, by name."""
    rows = c.execute(
        "SELECT key, value FROM registry_metadata WHERE key>=? AND key<? ORDER BY key LIMIT ?",
        (PREFIX, _PREFIX_END, limit),
    ).fetchall()
    out: list[str] = []
    for row in rows:
        key, detail = str(row[0]), json.loads(row[1])
        at = str(detail.get("at") or "")[:16].replace("T", " ")
        out.append(f"{key[len(PREFIX) :]}: {detail.get('error') or 'an error'} at {at} UTC")
    return out
