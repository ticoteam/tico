"""Counts the statements one measured request runs, and no other thread's.

A count that traces every connection a store lends counts whatever any thread runs on that store while the request is
in flight. The app starts threads of its own (a worker pass, a refresh, a background read) that land in that window at
random. A count made here takes a statement when the thread running it carries the measurement's context: the request
inherits that context, and so do the threads it hands work to through anyio or asyncio (a TestClient request, a
threadpool call, asyncio.to_thread). A thread started with threading.Thread begins with an empty context, so a
background thread's statements are left out.

One pool does not copy context: the app's authentication pool (backend/app.py, thread name "tico-auth") runs each
request's `authenticate` through loop.run_in_executor. Its statements belong to the request, so while a measurement is
active they count into it. Nothing else does.
"""

import contextlib
import contextvars
import threading

AUTH_THREADS = "tico-auth"  # backend/app.py: the thread_name_prefix of the pool that authenticates each request

_MEASURING = contextvars.ContextVar("measured_statements", default=None)
_ACTIVE = []  # the measurements running now, innermost last


@contextlib.contextmanager
def measuring(seen):
    """Counts into `seen` the statements the request runs while the block runs."""
    token = _MEASURING.set(seen)
    _ACTIVE.append(seen)
    try:
        yield seen
    finally:
        _ACTIVE.remove(seen)
        _MEASURING.reset(token)


def belongs(seen, threads=(AUTH_THREADS,)):
    """Whether the statement the current thread runs is part of the measurement `seen` (see the module docstring).
    `threads` are the name prefixes of pools that run the request's work without its context (the authentication pool;
    a measurement of one live stream adds events.py's "events-read")."""
    if _MEASURING.get() is seen:
        return True
    return bool(_ACTIVE) and _ACTIVE[-1] is seen and threading.current_thread().name.startswith(tuple(threads))


def recorder(seen, threads=(AUTH_THREADS,)):
    """A trace callback for one connection: it appends a statement to `seen` only when the statement belongs to the
    measurement `seen`, whichever thread runs it (see the module docstring)."""

    def record(sql):
        if belongs(seen, threads):
            seen.append(sql)

    return record
