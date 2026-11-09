"""A test that leaves a pooled database connection open fails, at its own teardown (conftest.py).

A connection a test checks out from a Store and never returns is closed later by the garbage collector, in whichever
test happens to run then, and warnings-as-errors fails that other test. This module makes the failure land on the test
that caused it. Every pooled connection opened while a test runs is recorded with a short stack. At the test's teardown
the stores it made are closed, the threads it started get a short join, and a connection still open fails the test,
naming where it was opened and the threads still alive. The failing connections are closed only when no thread the
test started is still alive: a thread using a connection that is closed under it crashes the process.

A thread an earlier test started (a scheduler tick's thread-rank or stale-review pass) can open a connection while this
test runs and still hold it at this test's teardown. That connection is neither this test's leak nor safe to close: the
thread is in the middle of a statement on it, and closing it frees the statement cache under that statement, which
segfaulted xdist workers (merge queue run 42516409696). The guard records which thread holds each checked-out
connection and never closes, or blames this test for, one a live thread from before this test holds; that thread
returns it, and a closed store closes it then. Later teardowns check it again: if its thread ends without giving it
back, it is closed and reported, naming the test it was opened in.

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
# What the check costs in this process: its own work around each test and each connection opened, the joins, and the
# store's own connects (timed apart: they are the store's work, not the check's).
COST = {"seconds": 0.0, "joined": 0.0, "connect": 0.0}
# Every test's share, summed in the process that reports (the xdist controller, or the only process): for the summary.
SUMMED = {"tests": 0, "seconds": 0.0, "joined": 0.0, "connect": 0.0}

_store_init = _store.Store.__init__
_store_connect = _store.Store.connect
_store_give = _store.Store._give

# Who holds each pooled connection (`c._held_by`): the thread that has it checked out, POOLED while it waits idle in a
# store's pool, or None once given back and closed. Opening, taking out of the pool and putting back set it at the
# moment the connection changes hands, so a teardown never closes, nor even reads through SQLite, a connection another
# live thread may be using: a read there can deadlock against that thread's statement, a close crashes it. HOLD orders
# the holder changes the guard makes with the teardown's check-then-close. Reentrant: a read context the garbage
# collector frees while the teardown holds it gives its connection back on the same thread.
HOLD = threading.RLock()
POOLED = "pooled"
# Connections a test opened that an earlier test's thread still held at its end: {connection: that test's id}. Each
# teardown checks them again, and closes and reports one whose thread has ended without giving it back.
LENT = weakref.WeakKeyDictionary()


class _Pool(list):
    """A store's idle connections (Store.pool). One taken out is the taking thread's at once, under the store's pool
    lock, before any other thread can see it unheld; one put back is POOLED."""

    def pop(self, *args):
        c = super().pop(*args)
        c._held_by = threading.current_thread()
        return c

    def append(self, c):
        c._held_by = POOLED
        super().append(c)


def _recording_init(self, *args, **kwargs):
    _store_init(self, *args, **kwargs)
    # The finalizer closes the pool list it was given (store._close_idle), so it moves to the new list with it.
    self._pool_finalizer.detach()
    self.pool = _Pool()
    # HOLD itself, reentrant: _Pool runs Python under the pool lock, and a collection there can free an abandoned read
    # context, whose finalizer gives its connection back (Store._give takes the pool lock, _recording_give HOLD). One
    # lock for both leaves no order for two threads to take them in opposite ways.
    self.pool_lock = HOLD
    self._pool_finalizer = weakref.finalize(self, _store._close_idle, self.pool)
    STORES.add(self)


def _recording_connect(self, factory=None, check_same_thread=True):
    started = time.perf_counter()
    c = _store_connect(self, factory=factory, check_same_thread=check_same_thread)
    connected = time.perf_counter()
    COST["connect"] += connected - started
    # The pool passes its own factory. A caller's connection without one is outside the pool's lifetime rules.
    if factory is not None:
        # Without source lines: linecache checks each file's mtime on every lookup, which cost seconds per shard. The
        # lines are read only when a leak is reported.
        stack = traceback.StackSummary.extract(traceback.walk_stack(None), limit=STACK_FRAMES, lookup_lines=False)
        c._opened_at = (threading.current_thread().name, stack)
        c._held_by = threading.current_thread()  # held before the teardown can see it (Store._take opens it for us)
        OPENED.add(c)
        COST["seconds"] += time.perf_counter() - connected
    return c


def _recording_give(self, c):
    # The giving thread holds it while the store rolls it back and pools or closes it: the garbage collector can give
    # back, on its own thread, a read context another thread opened.
    with HOLD:
        c._held_by = threading.current_thread()
    try:
        _store_give(self, c)
    finally:
        with HOLD:
            # Still this thread's: the store closed it (a pooled one is POOLED, or already another thread's).
            if getattr(c, "_held_by", None) is threading.current_thread():
                c._held_by = None


_store.Store.__init__ = _recording_init
_store.Store.connect = _recording_connect
_store.Store._give = _recording_give


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


def _user(c):
    """Under HOLD: who else may be using `c`: the live thread other than this one that holds it, or POOLED while it waits
    in a pool for any thread to take; None when no other thread can be using it, so it is safe to read and close."""
    holder = getattr(c, "_held_by", None)
    if holder is POOLED:
        return POOLED
    if holder is None or holder is threading.current_thread() or not holder.is_alive():
        return None
    return holder


def _close(c):
    try:
        c.close()
    except sqlite3.Error:
        pass


def _opened_lines(c):
    thread, stack = c._opened_at
    return f"Opened on thread {thread} at:\n" + "".join(traceback.format_list(stack)).rstrip()


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
    SUMMED["connect"] += properties.get("pooled-connection-connect", 0.0)


@pytest.fixture(autouse=True)
def pooled_connections_closed(request):
    seconds, joined, connect = COST["seconds"], COST["joined"], COST["connect"]
    started = time.perf_counter()
    before = set(threading.enumerate())
    OPENED.clear()
    COST["seconds"] += time.perf_counter() - started
    yield
    started = time.perf_counter()
    close_stores()
    nodeid = request.node.nodeid
    with HOLD:
        # An earlier test's thread that has ended without giving back a connection it opened during that test.
        orphans = []
        for c, origin in list(LENT.items()):
            user = _user(c)
            if user is None:
                del LENT[c]
                if _is_open(c):
                    _close(c)
                    orphans.append((c, origin))
            elif user is POOLED:
                del LENT[c]  # given back
        # A connection a thread from before this test holds is that thread's: not this test's leak, and never touched
        # here (a statement in flight on a closed connection crashes the process). It is checked again later. One in a
        # pool is idle, and no test's leak.
        leaked = []
        for c in list(OPENED):
            user = _user(c)
            if user is POOLED:
                continue
            if user is not None:
                if user in before:
                    LENT[c] = nodeid
                else:
                    leaked.append(c)
            elif _is_open(c):
                leaked.append(c)
    started_here = []
    if leaked:
        # A connection a thread is still using is returned once the thread finishes, so wait for the threads first.
        started_here = [t for t in threading.enumerate() if t not in before and t is not threading.current_thread()]
        joining = time.perf_counter()
        _join(started_here, JOIN_SECONDS)
        COST["joined"] += time.perf_counter() - joining
    still, alive = [], []
    with HOLD:
        for c in leaked:
            user = _user(c)
            if user is POOLED:
                continue
            if user is not None:
                if user in before:
                    LENT[c] = nodeid
                else:
                    still.append(c)
            elif _is_open(c):
                still.append(c)
        alive = [t.name for t in started_here if t.is_alive()]
        if still and not alive:
            for c in still:
                if _user(c) is None:
                    _close(c)
    # Formatted outside HOLD: it allocates, and a collection it triggers can run finalizers that give connections back.
    lines = []
    if still:
        lines.append(f"{nodeid} left {len(still)} pooled database connection(s) open.")
        lines += [_opened_lines(c) for c in still]
        if alive:
            lines.append(
                "Threads this test started, still alive: "
                + ", ".join(alive)
                + ". Their connections stay open, and each is returned when its thread ends."
            )
    for c, origin in orphans:
        lines.append(
            f"{origin} opened a pooled database connection on a thread from an earlier test, and that thread "
            f"ended without giving it back; closed during the teardown of {nodeid}.\n" + _opened_lines(c)
        )
    report = "\n".join(lines) if lines else None
    COST["seconds"] += time.perf_counter() - started
    # The teardown report carries this test's share to the process that prints the summary (conftest.py).
    request.node.user_properties.append(("pooled-connection-seconds", round(COST["seconds"] - seconds, 4)))
    request.node.user_properties.append(("pooled-connection-joined", round(COST["joined"] - joined, 4)))
    request.node.user_properties.append(("pooled-connection-connect", round(COST["connect"] - connect, 4)))
    if report is not None:
        pytest.fail(report, pytrace=False)
