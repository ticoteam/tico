"""One person's identical reads, computed once.

A person with the app open on ten computers and tabs re-reads the same lists on the same live event: on the hosted
TIDY company (Nov 19) one person sent ten identical GET /api/v2/threads/rail, /company/floor, /health and /needs-you
within two seconds, each of them seconds of SQL in Python, so the server did the same work ten times and every page
waited behind it. Here a read of one of ROUTES, by one caller, with the same path, query and validators, is computed
once for everyone who asks while it is being computed, and its answer serves the same request again for FRESH_SECONDS
after its read began while no change has been logged since.

What makes it safe:
- Never across callers: the key holds the caller's whole identity (actor, role, token, scope, view, ...), as
  backend/needs_cache.py does, so two callers who may see different things never share an answer.
- Never older than a logged write: each request reads the company's newest change number when it arrives (`Changes`,
  events.latest). It may take an answer only from a computation whose read began at or after that number. A request
  that arrives after a newer change while a computation runs waits for the next one, which begins when that one ends
  and so reads everything committed before any of its waiters asked (backend/events.py Rounds does the same for
  streams). At most one computation per key runs at a time, and a queued one that every waiter has left never runs.
- A write that logs no change (an access setting, a computer's heartbeat) reaches a shared answer late by at most the
  running computation's time plus FRESH_SECONDS. The change number, not every commit, is the key on purpose: the hosted
  companies commit several times a second, and a key that moved on every commit would share almost nothing.
- When the number cannot be read, or the shared state is full, the request computes on its own, as before.
- A kept answer serves for FRESH_SECONDS from when its read began, and /api/status (which says the server's time) is
  only shared while it is being computed.
- Each request gets its own copy of the answer, then its own validators and wire encoding (backend/json_response.py).
  An answer that streams, sets a cookie or runs work after it is sent is never shared.

A caller who is not a person (a bot, a computer, BotOps, the Assistant acting for someone), a support or view
session, or any other route reads as before."""

import asyncio
import sqlite3
import threading
import time
import weakref
from collections.abc import Callable, Coroutine
from typing import Any

from starlette.responses import FileResponse, Response, StreamingResponse

from .json_response import TAGGED

ROUTES = frozenset(
    {
        "/api/v2/places/{place}",
        "/api/v2/threads/rail",
        "/api/v2/tasks",
        "/api/v2/health",
        "/api/v2/bots",
        "/api/v2/needs-you",
        "/api/v2/company/floor",
        "/api/status",
        "/api/employees",
    }
)
# Opening the Growth page records that it was opened (backend/growth_setup.py attach): computed for each request.
UNSHARED_PLACES = frozenset({"growth"})
# Shared only while being computed, never kept: its answer carries the server's time.
NOT_KEPT = frozenset({"/api/status"})
FRESH_SECONDS = 3.0  # how long after its read began an answer serves the same request again, with no change since
KEPT_BYTES = 64 * 1024 * 1024  # answers kept at once, for every caller together
KEPT_ONE = 8 * 1024 * 1024  # a bigger answer is shared only while it is being computed
MAX_SLOTS = 512  # keys tracked at once; past it a request computes on its own
MAX_QUERY = 2048  # bytes of query string a shared request may have

clock: Callable[[], float] = time.monotonic

Compute = Callable[[], Coroutine[Any, Any, Response]]


class Changes:
    """The company's newest change number (events.latest, the `changes` log's sequence, which every logged write
    moves), read on a read-only connection of its own so it never waits for the pool. A replaced database file (a
    restore) is opened again, under a new epoch: (epoch, number) orders newest last."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.lock = threading.Lock()
        self.c: sqlite3.Connection | None = None
        self.file: Any = None
        self.epoch = 0

    def __call__(self) -> tuple[int, int] | None:
        """(epoch, newest change number), or None when it cannot be read."""
        path = self.store.settings.db_path
        try:
            file = self.store._file()
            with self.lock:
                if self.c is None or file != self.file:
                    if self.c is not None:
                        self.c.close()
                        self.c = None
                    self.c = sqlite3.connect(
                        path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1, check_same_thread=False
                    )
                    self.file, self.epoch = file, self.epoch + 1
                row = self.c.execute("SELECT seq FROM sqlite_sequence WHERE name='changes'").fetchone()
                return self.epoch, row[0] if row else 0
        except (sqlite3.Error, OSError):
            return None


def key_of(request: Any, route: str) -> tuple[Any, ...] | None:
    """What two requests must have in common to share an answer, or None when this one never shares."""
    if request.method != "GET" or route not in ROUTES:
        return None
    who = getattr(request.state, "identity", None)
    if who is None or who.role not in ("human", "owner") or who.via or who.support or who.view:
        return None
    if getattr(request.state, "delegation", None):
        return None
    if route == "/api/v2/places/{place}" and request.path_params.get("place") in UNSHARED_PLACES:
        return None
    query = request.scope.get("query_string", b"")
    if len(query) > MAX_QUERY:
        return None
    headers = request.headers
    # A route that answers If-None-Match itself (GET /api/v2/tasks) answers each validator differently; on the tagged
    # paths backend/json_response.py answers it after the share, for each request.
    validator = "" if request.url.path in TAGGED else headers.get("if-none-match", "")
    return (route, request.url.path, query, repr(who), headers.get("accept", ""), validator)


def shareable(response: Any) -> bool:
    return (
        isinstance(response, Response)
        and not isinstance(response, (StreamingResponse, FileResponse))
        and isinstance(getattr(response, "body", None), bytes)
        and response.background is None
        and b"set-cookie" not in (name for name, _ in response.raw_headers)
    )


def copy(response: Response) -> Response:
    """A response of the same kind with the same status, headers and body, whose headers and body the caller may
    change (backend/json_response.py compress does) without changing the shared one."""
    out = object.__new__(type(response))
    out.__dict__.update(response.__dict__)
    out.__dict__.pop("_headers", None)
    out.raw_headers = list(response.raw_headers)
    return out


class _Slot:
    """One key's computation under way (`running`, begun at change number `running_at`), the one queued behind it
    (`waiting`, with how many requests wait on it and the newest number they read, `waiting_at`), and the newest answer
    kept (`kept`, read at change number `kept_at`, begun at `kept_began`)."""

    __slots__ = ("route", "running", "running_at", "waiting", "waiting_at", "waiters", "kept", "kept_at", "kept_began")

    def __init__(self, route: str) -> None:
        self.route = route
        self.running: asyncio.Task[Response] | None = None
        self.running_at: Any = None
        self.waiting: asyncio.Task[Response] | None = None
        self.waiting_at: Any = None
        self.waiters = 0
        self.kept: Response | None = None
        self.kept_at: Any = None
        self.kept_began = 0.0


class _Loop:
    """One event loop's slots: a computation's task belongs to the loop that made it."""

    def __init__(self) -> None:
        self.slots: dict[tuple[Any, ...], _Slot] = {}
        self.kept_bytes = 0
        self.pruned = 0.0


class SharedReads:
    def __init__(self) -> None:
        self._loops: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Loop] = weakref.WeakKeyDictionary()
        # How each request was answered (tests, the bench): computed, joined a computation, or from a kept answer.
        self.computed = self.joined = self.reused = 0

    async def answer(self, key: tuple[Any, ...], count: Any, compute: Compute) -> Response:
        loop = asyncio.get_running_loop()
        state = self._loops.get(loop)
        if state is None:
            state = self._loops[loop] = _Loop()
        now = clock()
        self._prune(state, now)
        slot = state.slots.get(key)
        if slot is None:
            if len(state.slots) >= MAX_SLOTS:
                return await compute()
            slot = state.slots[key] = _Slot(key[0])
        if slot.kept is not None and slot.kept_at >= count and now - slot.kept_began <= FRESH_SECONDS:
            self.reused += 1
            return copy(slot.kept)
        if slot.running is not None and slot.running_at is not None and slot.running_at >= count:
            return await self._join(slot.running, compute, owner=False)
        if slot.running is None:
            task = loop.create_task(compute())  # in this request's context: its timing and marks
            slot.running, slot.running_at = task, count
            task.add_done_callback(lambda done: self._ended(state, key, slot, done, count, now))
            return await self._join(task, compute, owner=True)
        # A computation from before this request's newest change is under way: the next one begins when it ends.
        owner = slot.waiting is None
        if owner:
            slot.waiting = loop.create_task(self._after(slot, slot.running, compute))
            slot.waiting_at = count
        else:
            slot.waiting_at = max(slot.waiting_at, count)  # (epoch, number): a newer file's epoch is higher
        slot.waiters += 1
        waiting = slot.waiting
        assert waiting is not None
        try:
            return await self._join(waiting, compute, owner=owner)
        finally:
            slot.waiters -= 1
            if slot.waiters == 0 and slot.waiting is waiting and not waiting.done():
                waiting.cancel()  # every request that wanted it has gone: it never runs
                slot.waiting = None

    async def _after(self, slot: _Slot, previous: asyncio.Task[Response], compute: Compute) -> Response:
        await asyncio.wait({previous})
        return await compute()

    def _ended(
        self, state: _Loop, key: tuple[Any, ...], slot: _Slot, task: asyncio.Task[Response], count: Any, began: float
    ) -> None:
        """`task`, begun at change number `count` at `began`, ended: keep its answer, and begin the one queued."""
        if slot.running is task:
            slot.running = None
        if not task.cancelled() and task.exception() is None and slot.route not in NOT_KEPT:
            response = task.result()
            size = len(response.body) if shareable(response) else KEPT_ONE + 1
            held = len(slot.kept.body) if slot.kept is not None else 0
            if size <= KEPT_ONE and response.status_code == 200 and state.kept_bytes - held + size <= KEPT_BYTES:
                state.kept_bytes += size - held
                slot.kept, slot.kept_at, slot.kept_began = response, count, began
        if slot.running is not None or state.slots.get(key) is not slot:
            return
        waiting, slot.waiting = slot.waiting, None
        if waiting is not None and not waiting.done():
            # The queued computation's read begins now, after every request that waits on it arrived: the newest count
            # they read is its label, since a request that reads that number again has seen no change since.
            started, label = clock(), slot.waiting_at
            slot.running, slot.running_at = waiting, label
            waiting.add_done_callback(lambda done: self._ended(state, key, slot, done, label, started))
        elif slot.kept is None:
            del state.slots[key]

    async def _join(self, task: asyncio.Task[Response], compute: Compute, owner: bool) -> Response:
        """The answer `task` gives, copied for this request; this request's own when that one cannot be shared. The
        `owner` is the request whose `compute` the task runs."""
        if owner:
            self.computed += 1
        else:
            self.joined += 1
        try:
            response = await asyncio.shield(task)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise  # this request itself was cancelled (its client left)
            return await compute()  # the shared computation was dropped: this request computes on its own
        except Exception:
            if owner:
                raise
            return await compute()  # the shared computation failed: this request tries on its own, as before
        if shareable(response):
            return copy(response)
        return response if owner else await compute()

    def _prune(self, state: _Loop, now: float) -> None:
        if now - state.pruned < 1.0:
            return
        state.pruned = now
        for key, slot in list(state.slots.items()):
            if slot.kept is not None and now - slot.kept_began > FRESH_SECONDS:
                state.kept_bytes -= len(slot.kept.body)
                slot.kept = None
            if slot.kept is None and slot.running is None and slot.waiting is None:
                del state.slots[key]


def of(store: Any) -> SharedReads:
    """The store's shared reads (one per Store, so per database)."""
    existing = getattr(store, "shared_reads", None)
    if existing is None:
        existing = store.shared_reads = SharedReads()
    return existing


def changes(store: Any) -> Changes:
    existing = getattr(store, "shared_reads_changes", None)
    if existing is None:
        existing = store.shared_reads_changes = Changes(store)
    return existing


async def answer(store: Any, request: Any, route: str, compute: Compute) -> Response:
    """`compute()`'s answer for this request, shared with the same caller's identical requests (see above)."""
    key = key_of(request, route)
    if key is None:
        return await compute()
    count = await asyncio.get_running_loop().run_in_executor(None, changes(store))
    if count is None:
        return await compute()
    return await of(store).answer(key, count, compute)
