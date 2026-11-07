"""A test that leaves a pooled database connection open fails, at its own teardown (conftest.py).

A connection a test checks out from a Store and never returns is closed later by the garbage collector, in whichever
test happens to run then, and warnings-as-errors fails that other test. This module makes the failure land on the test
that caused it. Every pooled connection opened while a test runs is recorded with a short stack. At the test's teardown
the stores it made are closed, the threads it started get a short join, and a connection still open fails the test,
naming where it was opened and the threads still alive. The failing connections are closed only when no thread the
test started is still alive: a thread using a connection that is closed under it crashes the process.

The guard is set up before the other autouse fixtures in conftest.py (they take it as a parameter), so it tears down
after them, once their threads have been joined.
"""

import sqlite3
import threading
import time
import traceback
import weakref

import pytest

from backend import store as _store

JOIN_SECONDS = 2.0  # in total, for the threads a test started, and only while a connection is still open
STACK_FRAMES = 12

STORES = weakref.WeakSet()  # every Store made in this process
OPENED = weakref.WeakSet()  # pooled connections opened while the current test runs
# What the check costs in this process: the work around each test and around each connection opened, and the joins.
COST = {"seconds": 0.0, "joined": 0.0}
# Every test's share, summed in the process that reports (the xdist controller, or the only process): for the summary.
SUMMED = {"tests": 0, "seconds": 0.0, "joined": 0.0}

_store_init = _store.Store.__init__
_store_connect = _store.Store.connect


def _recording_init(self, *args, **kwargs):
    _store_init(self, *args, **kwargs)
    STORES.add(self)


def _recording_connect(self, factory=None, check_same_thread=True):
    started = time.perf_counter()
    c = _store_connect(self, factory=factory, check_same_thread=check_same_thread)
    # The pool passes its own factory. A caller's connection without one is outside the pool's lifetime rules.
    if factory is not None:
        # Without source lines: linecache checks each file's mtime on every lookup, which cost seconds per shard. The
        # lines are read only when a leak is reported.
        stack = traceback.StackSummary.extract(traceback.walk_stack(None), limit=STACK_FRAMES, lookup_lines=False)
        c._opened_at = (threading.current_thread().name, stack)
        OPENED.add(c)
    COST["seconds"] += time.perf_counter() - started
    return c


_store.Store.__init__ = _recording_init
_store.Store.connect = _recording_connect


def close_stores():
    """Closes every store's idle connections. A connection still checked out is closed when it is returned."""
    for store in list(STORES):
        store.close()


def _is_open(c):
    try:
        c.total_changes
    except sqlite3.ProgrammingError:  # a closed connection refuses every operation
        return False
    return True


def _join(threads, budget):
    deadline = time.monotonic() + budget
    for thread in threads:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        thread.join(remaining)


def add_report(report):
    """Adds one teardown report's share of the check (its user properties, set below) to SUMMED."""
    properties = dict(report.user_properties)
    if "pooled-connection-seconds" not in properties:
        return
    SUMMED["tests"] += 1
    SUMMED["seconds"] += properties["pooled-connection-seconds"]
    SUMMED["joined"] += properties.get("pooled-connection-joined", 0.0)


@pytest.fixture(autouse=True)
def pooled_connections_closed(request):
    seconds, joined = COST["seconds"], COST["joined"]
    started = time.perf_counter()
    before = set(threading.enumerate())
    OPENED.clear()
    COST["seconds"] += time.perf_counter() - started
    yield
    started = time.perf_counter()
    close_stores()
    leaked = [c for c in list(OPENED) if _is_open(c)]
    started_here = []
    if leaked:
        # A connection a thread is still using is returned once the thread finishes, so wait for the threads first.
        started_here = [t for t in threading.enumerate() if t not in before and t is not threading.current_thread()]
        joining = time.perf_counter()
        _join(started_here, JOIN_SECONDS)
        COST["joined"] += time.perf_counter() - joining
        leaked = [c for c in leaked if _is_open(c)]
    report = None
    if leaked:
        lines = [f"{request.node.nodeid} left {len(leaked)} pooled database connection(s) open."]
        for c in leaked:
            thread, stack = c._opened_at
            lines.append(f"Opened on thread {thread} at:\n" + "".join(traceback.format_list(stack)).rstrip())
        alive = [t.name for t in started_here if t.is_alive()]
        if alive:
            lines.append(
                "Threads this test started, still alive: "
                + ", ".join(alive)
                + ". Their connections stay open, and each is returned when its thread ends."
            )
        report = "\n".join(lines)
        if not alive:
            for c in leaked:
                try:
                    c.close()
                except sqlite3.Error:
                    pass
    COST["seconds"] += time.perf_counter() - started
    # The teardown report carries this test's share to the process that prints the summary (conftest.py).
    request.node.user_properties.append(("pooled-connection-seconds", round(COST["seconds"] - seconds, 4)))
    request.node.user_properties.append(("pooled-connection-joined", round(COST["joined"] - joined, 4)))
    if report is not None:
        pytest.fail(report, pytrace=False)
