# pyright: strict
"""One build of a person's Needs you for every caller that asks for it at the same change (views.needs_items).

Opening the app asks for the same person's list from up to eight requests at once: needs-you twice, each live stream's
baseline (events.needs_baseline), the company floor's count and the place cards. Live on 18 Nov each built the list on
its own, about 1,000 statements apiece, and one open held the server for 8 s. Now a build is kept here, and a caller
whose read snapshot stands at the same change mark takes a copy of it instead of building again; callers that ask
while it is being built wait for that build.

- The key is the caller's whole identity (needs_cache.viewer: actor, role, token, scope, view, ...), so two callers
  who may read different things never share, what the list is asked for (`snoozed`), and the database the connection
  reads.
- The change mark is read in the caller's own snapshot (`mark`): the newest `needs` change (events._needs: every write
  that can move anyone's Needs you, an access change to a task's readers included, writes one) and the roster clock
  (a bot paused or resumed). A list is shared only with a snapshot at the same mark, so a write that moves the list
  starts a new build. A write that moves no mark leaves a shared list at most TTL seconds old, and an ask whose snooze
  ends by the clock alone is listed at most TTL seconds late.
- A list is kept TTL seconds from when its build began, at most MAX_ENTRIES at once (oldest out first).
- Not shared: a caller whose connection has written in its open transaction (its own writes are not committed, so
  their mark is not one anyone else reads), a connection the store did not hand out, and a build inside a build.
- Every caller gets its own copy: a caller may change its items (needs_lines, sorting, paging).

This keeps one person's list across requests for up to TTL seconds, the one exception to backend/read_cache.py's
"no permission result outlives a request": keyed by what the caller may read, and dropped by any write that moves the
mark (spec "Build Needs you once per change", OK'd 2026-11-20)."""

import copy
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

TTL = 5.0  # seconds a build serves the callers at its mark
MAX_ENTRIES = 256  # lists held at once, for every person together (about 100 active people)
WAIT = 30.0  # seconds a caller waits on another's build before it builds its own

clock: Callable[[], float] = time.monotonic

NEEDS_MARK_SQL = "SELECT MAX(seq) FROM changes WHERE topic='needs'"
ROSTER_MARK_SQL = "SELECT value FROM roster_clock WHERE id=1"

Items = list[dict[str, Any]]
Key = tuple[Any, ...]

_lock = threading.Lock()
_held: "OrderedDict[Key, tuple[float, Items]]" = OrderedDict()
_building: dict[Key, "Future[Items]"] = {}
_local = threading.local()

# How many lists were built through here (tests and the bench count builds with it).
builds = 0


def mark(c: Any) -> tuple[Any, ...] | None:
    """The change mark of the caller's snapshot, or None when its list must not be shared (see the module's notes)."""
    if getattr(c, "pool_file", None) is None or getattr(c, "read_cache", None) is None:
        return None
    if getattr(c, "write_transaction", False) or c.total_changes != getattr(c, "provenance_changes", None):
        return None
    try:
        needs = c.execute(NEEDS_MARK_SQL).fetchone()
        roster = c.execute(ROSTER_MARK_SQL).fetchone()
    except sqlite3.OperationalError:  # a file made before these tables: nothing shared
        return None
    return (needs[0] if needs else None, roster[0] if roster else None)


def items(c: Any, viewer: str, snoozed: str, build: Callable[[], Items], alone: Callable[[], Items]) -> Items:
    """`build()`'s list for this viewer at the caller's change mark: kept, being built by another caller, or built here
    and kept; a copy of the caller's own each time. `alone()` when the list is not shared: the caller's own build."""
    if getattr(_local, "building", False):
        return alone()
    at = mark(c)
    if at is None:
        return _counted(alone)
    key: Key = (getattr(c, "pool_file", None), viewer, snoozed, at)
    now = clock()
    with _lock:
        for old in [k for k, (began, _) in _held.items() if now - began >= TTL]:
            del _held[old]
        kept = _held.get(key)
        if kept is not None:
            _held.move_to_end(key)
            return copy.deepcopy(kept[1])
        pending = _building.get(key)
        mine = pending is None
        if pending is None:
            pending = _building[key] = Future()
    if not mine:
        try:
            return copy.deepcopy(pending.result(timeout=WAIT))
        except TimeoutError:
            # Its build took too long: this caller builds on its own, as before sharing. A build that failed fails
            # each caller that waited on it, as their own build would have.
            return _counted(alone)
    began = clock()
    try:
        built = _counted(build)
    except BaseException as failed:
        with _lock:
            _building.pop(key, None)
        pending.set_exception(failed)
        raise
    with _lock:
        _building.pop(key, None)
        _held[key] = (began, built)
        while len(_held) > MAX_ENTRIES:
            _held.popitem(last=False)
    pending.set_result(built)
    return copy.deepcopy(built)


def _counted(build: Callable[[], Items]) -> Items:
    global builds
    _local.building = True
    try:
        with _lock:
            builds += 1
        return build()
    finally:
        _local.building = False


def clear() -> None:
    """Hold nothing (tests)."""
    with _lock:
        _held.clear()
