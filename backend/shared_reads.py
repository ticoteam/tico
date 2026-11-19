# pyright: strict
"""A rate limit on one person's identical reads: at most one computation a second, and never an old answer.

Nov 19, the hosted TIDY company: one person with the app open on ten computers and tabs sent ten identical GETs on
every live event, each seconds of SQL, and the server fell behind. Here a GET under /api/ by one caller, with the same
path, query and validators, is computed at most once per READ_RATE_SECONDS (TICO_READ_RATE_SECONDS, default 1; below
0 turns the limit off, as in tests):

- The first request computes at once.
- A request that arrives while that computation runs, or within the second after it began, waits for the next one,
  which begins when the running one ends and no sooner than a second after it began. Every request waiting then gets
  its answer. A request never takes an answer whose read began before the request arrived, so nothing is kept and
  nothing is stale.
- Never across callers: the key holds the caller's whole identity (actor, role, token, scope, view, ...).
- Each request gets its own copy of the answer, then its own validators and wire encoding (backend/json_response.py).
  An answer that streams, sets a cookie or runs work after it is sent is never shared; each such request computes.
- A caller who is not a person (a bot, a computer, BotOps, the Assistant acting for someone), a support or view
  session, and UNSHARED routes read as before."""

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
DEFAULT_SECONDS = 1.0

clock: Callable[[], float] = time.monotonic

Compute = Callable[[], Coroutine[Any, Any, Response]]


def setting() -> float:
    """TICO_READ_RATE_SECONDS as set (default 1): below 0 turns the limit off."""
    try:
        return float(os.environ.get("TICO_READ_RATE_SECONDS", DEFAULT_SECONDS))
    except ValueError:
        return DEFAULT_SECONDS


def key_of(request: Any, route: str) -> tuple[Any, ...] | None:
    """What two requests must have in common to share a computation, or None when this one never shares."""
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
    """One key's computation under way (`running`, begun at `began`) and the one queued behind it (`next`)."""

    __slots__ = ("running", "began", "next")

    def __init__(self) -> None:
        self.running: asyncio.Task[Response] | None = None
        self.began = float("-inf")
        self.next: asyncio.Task[Response] | None = None


class SharedReads:
    def __init__(self) -> None:
        self._slots: dict[tuple[Any, ...], _Slot] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        # How each request was answered (tests, the bench): it computed, or it waited for a shared computation.
        self.computed = self.joined = 0

    async def answer(self, key: tuple[Any, ...], compute: Compute, seconds: float = DEFAULT_SECONDS) -> Response:
        loop = asyncio.get_running_loop()
        if loop is not self._loop:  # a slot's computations belong to one event loop
            self._slots.clear()
            self._loop = loop
        slot = self._slots.get(key)
        if slot is None:
            slot = self._slots[key] = _Slot()
        if slot.running is None and slot.next is None and clock() - slot.began >= seconds:
            self.computed += 1
            return await self._share(self._begin(key, slot, compute()), compute, owner=True)
        creator = slot.next is None
        if creator:
            slot.next = loop.create_task(self._later(key, slot, compute, seconds))
        next_read = slot.next
        assert next_read is not None
        self.joined += 1
        return await self._share(next_read, compute, owner=creator)

    def _begin(self, key: tuple[Any, ...], slot: _Slot, read: Coroutine[Any, Any, Response]) -> asyncio.Task[Response]:
        task = asyncio.get_running_loop().create_task(read)
        slot.running, slot.began = task, clock()
        task.add_done_callback(lambda done: self._ended(key, slot, done))
        return task

    async def _later(self, key: tuple[Any, ...], slot: _Slot, compute: Compute, seconds: float) -> Response:
        """The queued computation: after the running one ends, and no sooner than `seconds` after it began. Requests
        that arrive until it begins join it, so its read begins after every one of them arrived."""
        if slot.running is not None:
            await asyncio.wait({slot.running})
        wait = slot.began + seconds - clock()
        if wait > 0:
            await asyncio.sleep(wait)
        slot.next = None
        return await self._begin(key, slot, compute())

    def _ended(self, key: tuple[Any, ...], slot: _Slot, task: asyncio.Task[Response]) -> None:
        if slot.running is task:
            slot.running = None
        if slot.running is None and slot.next is None and self._slots.get(key) is slot:
            # Idle: the slot may go once its second has passed; a later request starts a new one.
            loop = asyncio.get_running_loop()
            loop.call_later(DEFAULT_SECONDS * 2, self._drop, key, slot)

    def _drop(self, key: tuple[Any, ...], slot: _Slot) -> None:
        if slot.running is None and slot.next is None and self._slots.get(key) is slot:
            del self._slots[key]

    async def _share(self, task: asyncio.Task[Response], compute: Compute, owner: bool) -> Response:
        """The answer `task` gives, copied for this request; this request's own when that one cannot be shared."""
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


def of(store: Any) -> SharedReads:
    """The store's shared reads (one per Store, so per database)."""
    existing = getattr(store, "shared_reads", None)
    if existing is None:
        existing = store.shared_reads = SharedReads()
    return existing


async def answer(store: Any, request: Any, route: str, compute: Compute) -> Response:
    """`compute()`'s answer for this request, rate-limited with the same caller's identical requests (see above)."""
    key = key_of(request, route)
    seconds = setting()
    if key is None or seconds < 0:
        return await compute()
    return await of(store).answer(key, compute, seconds)
