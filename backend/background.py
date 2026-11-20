# pyright: strict
"""The server's long-lived background threads, beside backend/drains.py and backend/scheduler.py: a worker, watcher,
poller or listener that runs for the server's (or its owner's) life starts here, by name, as a daemon thread, and is
kept in one registry while it runs.

`start` starts one such thread; its owner keeps its own stop signal. `Loop` is a worker that loops until its stop event
and is woken by a `Bell` (the webhook deliveries, Finance's workers): started, or restarted when it died, and stopped
the same way each time. `stop_all` stops every Loop at shutdown: each stop event set and bell rung first, then one short
deadline for all their joins, so a worker in a slow call is left as a restart leaves it.

A one-off job, a per-request worker or a pool starts its own thread or executor, not here."""

import threading
import time
from collections.abc import Callable, Iterable
from typing import Any, Protocol

STOP_WAIT = 5.0  # seconds a shutdown waits for the Loops' threads, all of them together

_lock = threading.Lock()
_threads: set[threading.Thread] = set()  # every thread started here and not yet seen dead
_loops: list["Loop"] = []


def _monotonic() -> float:
    """The joins' clock: a test advances it instead of patching time."""
    return time.monotonic()


class Joinable(Protocol):
    def join(self, timeout: float | None = None) -> None: ...


def start(name: str, target: Callable[..., object], *args: Any) -> threading.Thread:
    """`target(*args)` on a named daemon thread, started and registered."""
    thread = threading.Thread(target=target, args=args, name=name, daemon=True)
    thread.start()  # before it is registered: a thread not started yet is not alive, and the prune would drop it
    with _lock:
        _threads.difference_update([t for t in _threads if not t.is_alive()])
        _threads.add(thread)
    return thread


def running() -> list[str]:
    """The names of the registered threads still running."""
    with _lock:
        return sorted(t.name for t in _threads if t.is_alive())


def join(threads: Iterable[Joinable], timeout: float | None) -> None:
    """Join each thread, all within one `timeout` (None waits for each), not `timeout` per thread."""
    end = None if timeout is None else _monotonic() + timeout
    for thread in threads:
        thread.join(None if end is None else max(0.0, end - _monotonic()))


class Bell:
    """A doorbell for a worker: `ring` wakes every waiter, and one that was busy when it rang does not sleep through it."""

    def __init__(self) -> None:
        self.cond, self.rung = threading.Condition(), 0

    def ring(self) -> None:
        with self.cond:
            self.rung += 1
            self.cond.notify_all()

    def wait(self, seen: int, timeout: float) -> int:
        with self.cond:
            if self.rung == seen:
                self.cond.wait(timeout)
            return self.rung


class Loop:
    """A worker that runs `target(*args, halt)` until its `halt` event is set, on `count` threads named after it. `wake`
    (its bell's ring) gets it out of its wait when it stops."""

    def __init__(self, name: str, wake: Callable[[], None]) -> None:
        self.name, self.wake = name, wake
        self.lock = threading.Lock()
        self.halt = threading.Event()
        self.threads: list[threading.Thread] = []
        with _lock:
            _loops.append(self)

    def start(
        self, target: Callable[..., object], *args: Any, count: int = 1, before: Callable[[], None] | None = None
    ) -> None:
        """Start the threads that are not running (the ones that died, or all of them after `stop`, with a new event).
        A stop still waiting on a thread starts none. `before` runs once, under the lock, just before any start."""
        with self.lock:
            self.threads = [t for t in self.threads if t.is_alive()]
            if self.halt.is_set():
                if self.threads:
                    return
                self.halt = threading.Event()
            if len(self.threads) < count and before is not None:
                before()
            while len(self.threads) < count:
                name = self.name if count == 1 else f"{self.name}-{len(self.threads)}"
                self.threads.append(start(name, target, *args, self.halt))

    def stopped(self) -> bool:
        return self.halt.is_set()

    def signal(self) -> list[threading.Thread]:
        """Set the stop event and ring the bell; the threads to join."""
        self.halt.set()
        self.wake()
        return list(self.threads)

    def forget(self, joined: list[threading.Thread]) -> None:
        """Drop the threads a stop joined (or gave up on); one a start made since stays."""
        with self.lock:
            self.threads = [t for t in self.threads if t not in joined]

    def stop(self, timeout: float = STOP_WAIT) -> None:
        """Set the stop event, ring the bell and join the threads within `timeout` seconds all told."""
        joined = self.signal()
        join(joined, timeout)
        self.forget(joined)


def stop_all(timeout: float = STOP_WAIT) -> None:
    """Stop every Loop: all their stop events set and bells rung first, then one `timeout` for all the joins."""
    with _lock:
        loops = list(_loops)
    joined = [(loop, loop.signal()) for loop in loops]
    join([thread for _, threads in joined for thread in threads], timeout)
    for loop, threads in joined:
        loop.forget(threads)
