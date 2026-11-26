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
- The change mark is read in the caller's own snapshot (`mark`): the newest change of each topic that can move a
  Needs you (MARK_TOPICS: a `needs` row, events._needs, for every write that moves anyone's list, an access change to a
  task's readers included; a task's, message's or thread's change, which can end a snooze or change an item's words)
  and the roster clock (a bot paused or resumed). A list is shared only with a snapshot at the same mark, so a write that moves the list
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

# The change topics whose writes can move a Needs you: a `needs` row names the people whose list moved, and a task's,
# message's, thread's, bot's, person's or setup change can end a snooze or change what an item says. Run output,
# computers, presence and the like (most of the log) never move it.
MARK_TOPICS = ("needs", "tasks", "messages", "threads", "bots", "people", "setup")
# The whole mark in one statement: each topic's newest change, the roster clock (a bot paused or resumed), every
# pipeline's settings (a stage's OK whose words change asks something else, which ends its snoozes) and the team's
# access settings.
MARK_SQL = (
    "SELECT "
    + ", ".join(f"(SELECT MAX(seq) FROM changes WHERE topic='{topic}')" for topic in MARK_TOPICS)
    + ", (SELECT value FROM roster_clock WHERE id=1), (SELECT COUNT(*) FROM pipelines)"
    + ", (SELECT TOTAL(settings_version) FROM pipelines)"
    # Who may read what beyond a task's own readers: the owner, the Admins, bot access and the people roster
    # (backend/access.py). A person made a plain member reads no more as an Admin from a list kept before.
    + ", (SELECT group_concat(key || '=' || value, char(10)) FROM registry_metadata"
    + " WHERE key IN ('owner', 'access', 'bot_access', 'people'))"
)

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
    if getattr(c, "store_token", None) is None or getattr(c, "read_cache", None) is None:
        return None
    if getattr(c, "write_transaction", False) or c.total_changes != getattr(c, "provenance_changes", None):
        return None
    try:
        found = c.execute(MARK_SQL).fetchone()
    except sqlite3.OperationalError:  # a file made before these tables: nothing shared
        return None
    return tuple(found) if found else None


def items(
    c: Any, viewer: str, snoozed: str, build: Callable[[], Items], alone: Callable[[], Items], wakes: Any = None
) -> Items:
    """`build()`'s list for this viewer at the caller's change mark: kept, being built by another caller, or built here
    and kept; a copy of the caller's own each time. `alone()` when the list is not shared: the caller's own build.
    `wakes()` is when the caller's next snoozed ask comes back (ask_snoozes.next_end): part of the key, so the list
    the app reads again at that moment holds it, with no write to move the mark."""
    if getattr(_local, "building", False):
        return alone()
    at = mark(c)
    if at is None:
        return _counted(alone)
    key: Key = (
        c.store_token,
        getattr(c, "pool_file", None),
        viewer,
        snoozed,
        at,
        wakes() if wakes is not None else None,
    )
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
