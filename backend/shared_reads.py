"""One person's identical reads, computed once.

Nov 19 relief for the hosted TIDY company: one person with the app open on ten computers and tabs sent ten identical
GETs on every live event, each seconds of SQL, and the server fell behind. Here every GET under /api/ by one caller,
with the same path, query and validators, is computed once for everyone who asks while it is being computed, and the
answer serves the same request again for FRESH_SECONDS (TICO_READ_CACHE_SECONDS, default 1; 0 shares only while
computing). An answer can be up to that long behind a change: a deliberate, short-lived trade for load.

- Never across callers: the key holds the caller's whole identity (actor, role, token, scope, view, ...).
- Each request gets its own copy of the answer, then its own validators and wire encoding (backend/json_response.py).
  An answer that streams, sets a cookie or runs work after it is sent is never shared, nor are UNSHARED routes.
- A caller who is not a person (a bot, a computer, BotOps, the Assistant acting for someone), a support or view
  session reads as before."""

import asyncio
import os
import time
from collections.abc import Callable, Coroutine
from typing import Any

from starlette.responses import FileResponse, Response, StreamingResponse

from .json_response import TAGGED

# Reads that change something when they are read (a "seen" mark, a shown-email record, a stream) are never shared.
UNSHARED = frozenset({"/api/v2/events", "/api/v2/email/sends/{sid}"})
UNSHARED_PLACES = frozenset({"growth"})
FRESH_SECONDS = float(os.environ.get("TICO_READ_CACHE_SECONDS", "1"))  # how long one person's answer serves the same request again; 0 = only while it is being computed
KEPT_BYTES = 64 * 1024 * 1024  # answers kept at once, for every caller together
KEPT_ONE = 8 * 1024 * 1024  # a bigger answer is shared only while it is being computed

clock: Callable[[], float] = time.monotonic

Compute = Callable[[], Coroutine[Any, Any, Response]]


def key_of(request: Any, route: str) -> tuple[Any, ...] | None:
    """What two requests must have in common to share an answer, or None when this one never shares."""
    if request.method != "GET" or not route.startswith("/api/") or route in UNSHARED:
        return None
    who = getattr(request.state, "identity", None)
    if who is None or who.role not in ("human", "owner") or who.via or who.support or who.view:
        return None
    if getattr(request.state, "delegation", None):
        return None
    if route == "/api/v2/places/{place}" and request.path_params.get("place") in UNSHARED_PLACES:
        return None
    headers = request.headers
    # A route that answers If-None-Match itself (GET /api/v2/tasks) answers each validator differently; on the tagged
    # paths backend/json_response.py answers it after the share, for each request.
    validator = "" if request.url.path in TAGGED else headers.get("if-none-match", "")
    return (
        route,
        request.url.path,
        request.scope.get("query_string", b""),
        repr(who),
        headers.get("accept", ""),
        validator,
    )


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
    """One key's computation under way (`running`, from a read at or after change `running_seq`), the one queued
    behind it (`waiting`), and the newest answer kept (`kept`, read at or after `kept_seq`, ended at `kept_at`)."""

    __slots__ = ("running", "running_seq", "waiting", "waiting_seq", "kept", "kept_seq", "kept_at")

    def __init__(self) -> None:
        self.running: asyncio.Task[Response] | None = None
        self.running_seq = 0
        self.waiting: asyncio.Task[Response] | None = None
        self.waiting_seq = 0
        self.kept: Response | None = None
        self.kept_seq = 0
        self.kept_at = 0.0


class SharedReads:
    def __init__(self) -> None:
        self._slots: dict[tuple[Any, ...], _Slot] = {}
        self._owners: dict[asyncio.Task[Response], Compute] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._kept_bytes = 0
        self._pruned = 0.0
        # How each request was answered (tests, the bench): computed, joined a computation, or from a kept answer.
        self.computed = self.joined = self.reused = 0

    async def answer(self, key: tuple[Any, ...], seq: int, compute: Compute) -> Response:
        loop = asyncio.get_running_loop()
        if loop is not self._loop:  # a slot's computations belong to one event loop
            self._slots.clear()
            self._owners.clear()
            self._kept_bytes, self._loop = 0, loop
        now = clock()
        self._prune(now)
        slot = self._slots.get(key)
        if slot is None:
            slot = self._slots[key] = _Slot()
        if slot.kept is not None and slot.kept_seq >= seq and now - slot.kept_at <= FRESH_SECONDS:
            self.reused += 1
            return copy(slot.kept)
        if slot.running is not None and slot.running_seq >= seq:
            return await self._join(slot.running, compute)
        if slot.running is None:
            return await self._join(self._start(key, slot, compute, seq), compute)
        # A computation from before this request's newest change is under way: the next one begins when it ends.
        if slot.waiting is None:
            slot.waiting = self._own(loop.create_task(self._after(slot.running, compute)), compute)
            slot.waiting_seq = seq
        else:
            slot.waiting_seq = max(slot.waiting_seq, seq)
        return await self._join(slot.waiting, compute)

    def _own(self, task: asyncio.Task[Response], compute: Compute) -> asyncio.Task[Response]:
        """`task` computes for the request that made `compute` (the context it runs in is that request's)."""
        self._owners[task] = compute
        task.add_done_callback(lambda done: self._owners.pop(done, None))
        return task

    def _start(self, key: tuple[Any, ...], slot: _Slot, compute: Compute, seq: int) -> asyncio.Task[Response]:
        task = self._own(asyncio.get_running_loop().create_task(compute()), compute)
        slot.running, slot.running_seq = task, seq
        task.add_done_callback(lambda done: self._ended(key, slot, done, seq))
        return task

    async def _after(self, previous: asyncio.Task[Response], compute: Compute) -> Response:
        await asyncio.wait({previous})
        return await compute()

    def _ended(self, key: tuple[Any, ...], slot: _Slot, task: asyncio.Task[Response], seq: int) -> None:
        """`task`, whose read began at or after change `seq`, ended: keep its answer, and begin the one queued."""
        if slot.running is task:
            slot.running = None
        failed = task.cancelled() or task.exception() is not None
        if not failed:
            response = task.result()
            size = len(response.body) if shareable(response) else KEPT_ONE + 1
            held = len(slot.kept.body) if slot.kept is not None else 0
            if size <= KEPT_ONE and response.status_code == 200 and self._kept_bytes - held + size <= KEPT_BYTES:
                self._kept_bytes += size - held
                slot.kept, slot.kept_seq, slot.kept_at = response, seq, clock()
        if slot.waiting is not None and slot.running is None and self._slots.get(key) is slot:
            # The queued computation's read begins now, after every request that waits on it arrived.
            waiting, slot.waiting, label = slot.waiting, None, slot.waiting_seq
            slot.running, slot.running_seq = waiting, label
            waiting.add_done_callback(lambda done: self._ended(key, slot, done, label))

    async def _join(self, task: asyncio.Task[Response], compute: Compute) -> Response:
        """The answer `task` gives, copied for this request; this request's own when that one cannot be shared."""
        owner = self._owners.get(task) is compute
        if owner:
            self.computed += 1
        else:
            self.joined += 1
        try:
            response = await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled() and not owner:
                return await compute()
            raise
        except Exception:
            if owner:
                raise
            return await compute()  # the shared computation failed: this request tries on its own, as before
        if shareable(response):
            return copy(response)
        return response if owner else await compute()

    def _prune(self, now: float) -> None:
        if now - self._pruned < 1.0:
            return
        self._pruned = now
        for key, slot in list(self._slots.items()):
            if slot.kept is not None and now - slot.kept_at > FRESH_SECONDS:
                self._kept_bytes -= len(slot.kept.body)
                slot.kept = None
            if slot.kept is None and slot.running is None and slot.waiting is None:
                del self._slots[key]


def of(store: Any) -> SharedReads:
    """The store's shared reads (one per Store, so per database)."""
    existing = getattr(store, "shared_reads", None)
    if existing is None:
        existing = store.shared_reads = SharedReads()
    return existing


async def answer(store: Any, request: Any, route: str, compute: Compute) -> Response:
    """`compute()`'s answer for this request, shared with the same caller's identical requests (see above)."""
    key = key_of(request, route)
    if key is None:
        return await compute()
    # A short time-based cache (Nov 19 relief for TIDY): a person's identical reads within FRESH_SECONDS, or while one
    # is being computed, get one answer, whatever changed in between.
    return await of(store).answer(key, 0, compute)
