# pyright: strict
"""The server's own background workers: the loops and threads the app's lifespan starts (backend/app.py `_lifespan`),
and the way they end.

- A worker that dies of an uncaught exception is never silent. It is logged as an error with its traceback, sent to
  telemetry (`worker_<name>`, the exception's type only), and recorded in registry_metadata (`worker-died:<name>`: the
  type and the time, never the message; a write that fails is tried RECORD_TRIES times). The production check
  `worker_died` (backend/invariants.py) reads that record: Health reads it on each view, and `on_death` (the app's: Check
  health's `file_check_now`) files it for Agent Ops at once. Neither waits for the scheduler, which is itself one of the
  workers. A worker is not restarted; the next start of the server starts them all again, so it clears the records
  (`forget`). A start-up job (`job`) that fails is logged and sent to telemetry only: it was never meant to keep running.
- The way out (`shutdown`) signals every stop first, then waits for every worker together, under one deadline
  (SHUTDOWN_SECONDS). A worker still running at the deadline is cancelled and named in the log. Cancelling the await of a
  thread (a `thread` worker's, or a call a worker made through `offload`) does not stop the thread, so each such thread
  still running is given THREAD_GRACE_SECONDS more, and named in the log if it runs on. Then the cleanups run, each on its
  own and in the order registered, whatever a worker or another cleanup did; the lifespan closes telemetry and the store
  after them. Each cleanup is given CLEANUP_SECONDS, then named in the log and left; one that runs a blocking call does it
  on a thread of its own (`on_exit_thread`). Every wait on a thread is made from a thread of its own, never the default pool, which the threads waited
  for may be filling. The way out runs as a task of its own: a cancel of the caller waits for it (it is bounded) and is
  passed on after, so the store never closes under a worker still being waited for.
- A start that fails part way runs the same way out over what it had started (backend/app.py wraps the start in it).
"""

import asyncio
import inspect
import json
import logging
import re
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
CLEANUP_SECONDS = 15.0  # a cleanup this long without ending is named in the log and left; the next one runs
RECORD_TRIES = (0.2, 1.0)  # the pauses between tries of a death's record: three tries in all (a busy database)
RECORD_SECONDS = 10.0  # the way out waits this long for deaths still being recorded and filed
# A worker's name: telemetry takes `worker_<name>` as its source, which must match observability.SOURCE.
NAME = re.compile(r"[a-z][a-z_]{0,24}")
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

    def __init__(self, store: Store, telemetry: Telemetry, on_death: Callable[[], object] | None = None):
        self.store = store
        self.telemetry = telemetry
        self.on_death = on_death  # after a death is recorded, on the recording thread
        self.tasks: list[tuple[str, asyncio.Task[Any]]] = []
        self.threads: list[tuple[str, threading.Event, threading.Event]] = []  # (name, started, finished)
        self.jobs: set[str] = set()  # start-up jobs: they end by themselves, and a failure is not a dead worker
        self.stops: list[tuple[str, Callable[[], object]]] = []
        self.cleanups: list[tuple[str, Callable[[], object]]] = []
        self.records: list[threading.Event] = []  # set when a death's record (and its filing) is done
        self.stopping = False
        self.teardown: asyncio.Task[None] | None = None

    def task(self, name: str, work: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Starts `work` as a worker named `name`."""
        if not NAME.fullmatch(name):
            work.close()
            raise ValueError(f"a worker's name is lowercase letters and underscores, at most 25: {name!r}")
        task = asyncio.create_task(work, name=f"tico-{name}")
        task.add_done_callback(lambda done: self._ended(name, done))
        self.tasks.append((name, task))
        return task

    def thread(self, name: str, work: Callable[..., object], *args: object) -> asyncio.Task[Any]:
        """Starts `work(*args)` on a thread as a worker named `name`; the way out knows whether its thread still runs."""
        return self.task(name, self.offload(name, work, *args))

    def job(self, name: str, work: Callable[..., object], *args: object) -> asyncio.Task[Any]:
        """A start-up job on a thread: waited for on the way out like a worker, but it ends by itself, and a failure is
        logged and sent to telemetry without counting as a dead worker."""
        self.jobs.add(name)
        return self.thread(name, work, *args)

    async def offload(self, name: str, work: Callable[..., Any], *args: object) -> Any:
        """`await asyncio.to_thread(work, *args)` for worker `name`, fenced on the way out: a thread still running when
        its await was cancelled gets the grace and the log line a `thread` worker's does."""
        started, finished = threading.Event(), threading.Event()
        entry = (name, started, finished)

        def run() -> Any:
            started.set()
            try:
                return work(*args)
            finally:
                finished.set()

        self.threads.append(entry)
        try:
            return await asyncio.to_thread(run)
        finally:
            if finished.is_set() or not started.is_set():
                self.threads.remove(entry)  # nothing of it runs on; a loop's calls must not pile up here

    def on_stop(self, name: str, stop: Callable[[], object]) -> None:
        """`stop()` runs first on the way out, before any worker is waited for."""
        self.stops.append((name, stop))

    def on_exit(self, name: str, cleanup: Callable[[], object]) -> None:
        """`cleanup()` (or the awaitable it returns) runs after the workers end, in the order registered."""
        self.cleanups.append((name, cleanup))

    def on_exit_thread(self, name: str, cleanup: Callable[..., object], *args: object) -> None:
        """`cleanup(*args)` after the workers end, on a thread of its own: never the default pool, which threads left
        running past the deadline may fill. Waited for up to CLEANUP_SECONDS."""

        async def run() -> None:
            failed: list[BaseException] = []
            done = threading.Event()

            def call() -> None:
                try:
                    cleanup(*args)
                except Exception as exc:  # noqa: BLE001 - raised again on the loop, below
                    failed.append(exc)
                finally:
                    done.set()

            threading.Thread(target=call, name=f"tico-exit-{name}", daemon=True).start()
            if not await wait_events([done], CLEANUP_SECONDS):
                raise TimeoutError
            if failed:
                raise failed[0]

        self.on_exit(name, run)

    def _ended(self, name: str, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        if name in self.jobs:
            log.error("Start-up job %s failed: %s", name, type(exc).__name__, exc_info=exc)
        else:
            log.error("Background worker %s died: %s", name, type(exc).__name__, exc_info=exc)
        try:
            self.telemetry.capture(f"worker_{name}", exc)
        except Exception:  # noqa: BLE001 - telemetry must never hide the death it reports
            log.warning("Telemetry could not take worker %s's death", name, exc_info=True)
        if self.stopping or name in self.jobs:
            return  # on the way out: the next start runs it again, and would clear the record at once
        self._record(name, type(exc).__name__)

    def _record(self, name: str, error: str) -> None:
        """Records the death and files it, on a thread of its own: the default pool may be what is full, and the
        database may be busy for a moment, so the write is tried again before it is given up, loudly."""
        done = threading.Event()

        def run() -> None:
            try:
                for pause in (*RECORD_TRIES, None):
                    try:
                        record(self.store, name, error)
                        break
                    except Exception:
                        if pause is None:
                            log.error("Worker %s's death could not be recorded for Health", name, exc_info=True)
                            return
                        time.sleep(pause)
                if self.on_death is not None:
                    try:
                        self.on_death()
                    except Exception:
                        log.error("Worker %s's death could not be filed for Agent Ops", name, exc_info=True)
            finally:
                done.set()

        self.records.append(done)
        threading.Thread(target=run, name=f"tico-death-{name}", daemon=True).start()

    async def shutdown(self) -> None:
        """Every stop, then every worker under one deadline, then every cleanup (see the module docstring). A cancel of
        the caller waits for all of it, then is passed on."""
        if self.teardown is None:
            self.teardown = asyncio.ensure_future(self._teardown())
        cancelled = False
        while True:
            try:
                await asyncio.shield(self.teardown)
                break
            except asyncio.CancelledError:
                if self.teardown.done():
                    raise
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def _teardown(self) -> None:
        self.stopping = True
        try:
            for name, stop in self.stops:
                try:
                    stop()
                except Exception:
                    log.error("Stopping %s failed", name, exc_info=True)
            await self._wait()
            await self._wait_threads()
            if not await wait_events(self.records, RECORD_SECONDS):
                log.error("Worker deaths still being recorded after %g s, closing regardless", RECORD_SECONDS)
        finally:
            await self._clean_up()

    async def _wait(self) -> None:
        if not self.tasks:
            return
        together = asyncio.gather(*(task for _, task in self.tasks), return_exceptions=True)
        together.add_done_callback(_retrieved)  # left unfinished at the deadline, it must not be reported as unread
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
        await wait_events([finished for _, finished in running], THREAD_GRACE_SECONDS)
        left = sorted({name for name, finished in running if not finished.is_set()})
        if left:
            log.error("Background worker threads still running after the way out, cleaning up regardless: %s", left)

    async def _clean_up(self) -> None:
        for name, cleanup in self.cleanups:
            try:
                result = cleanup()
                if inspect.isawaitable(result):
                    await asyncio.wait_for(asyncio.ensure_future(result), CLEANUP_SECONDS)
            except TimeoutError:
                log.error("Cleanup %s still running after %g s on the way out, going on", name, CLEANUP_SECONDS)
            except Exception:
                log.error("Cleanup %s failed on the way out", name, exc_info=True)


def _retrieved(future: asyncio.Future[Any]) -> None:
    if not future.cancelled():
        future.exception()


def _settle(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


async def wait_events(events: list[threading.Event], seconds: float) -> bool:
    """Whether every event was set within `seconds`. The wait runs on a thread of its own, never on the default pool:
    the threads it waits for may be the ones filling that pool, and a wait queued behind them would never start."""
    if all(event.is_set() for event in events):
        return True
    loop = asyncio.get_running_loop()
    done: asyncio.Future[None] = loop.create_future()

    def wait() -> None:
        end = time.monotonic() + seconds
        for event in events:
            event.wait(max(0.0, end - time.monotonic()))
        try:
            loop.call_soon_threadsafe(_settle, done)
        except RuntimeError:
            pass  # the loop closed: nobody waits any more

    threading.Thread(target=wait, name="tico-shutdown-wait", daemon=True).start()
    try:
        await asyncio.wait_for(asyncio.shield(done), seconds + 1)
    except TimeoutError:
        pass
    return all(event.is_set() for event in events)


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
