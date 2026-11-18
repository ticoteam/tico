# pyright: strict
"""The Needs-you lists the route built in the last few seconds, so a list's next pages come from the build that gave its
first (backend/views.py needs_answer).

A page's `next` cursor names the version its page came from. Before this, every page built the whole list again and
cut its slice from the new build: live (Nov 18) a 384-item list read in pages of 50 built it eight times. Now each build
that gives a list keeps it here for TTL seconds under (viewer, version), and a cursor of that version is cut from it
with no build. A cursor for a version not held here (expired, evicted, or never built in this process) builds as
before, and starts again from the top when the list moved (`restart`).

- Viewers never share an entry: the key holds the caller's whole identity (actor, role, token, scope, view, ...), so
  any difference in what a caller may see is a different key.
- A viewer keeps only their newest build: storing one drops the same viewer's others, so after a live `needs` event
  mends a page (`ids=`, which builds) a cursor from before it starts again from the top. Newest is by when the build's
  read snapshot began (`begin`, taken before it opens), not when it finished: two builds can run at once, and a slow
  one that began before a change never replaces one that began after it. A build that began before the viewer's
  newest is not kept.
- Memory is bounded: at most MAX_ENTRIES lists, oldest used first out, each kept until TTL seconds after its snapshot
  began.
- Before a page is cut from here, the route checks that the caller may still read each task on it
  (backend/views.py `needs_still_readable`); when one is gone it builds as before.

A page cut from here can be up to TTL seconds older than the database. It is the same list the first page came from,
which is what its cursor asks for; the live `needs` event brings the changes in between, as it does for a held list."""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

TTL = 30.0  # seconds a built list serves its next pages
MAX_ENTRIES = 32  # lists held at once, for every viewer together

clock: Callable[[], float] = time.monotonic


@dataclass(frozen=True)
class Built:
    items: list[dict[str, Any]]
    version: str
    snoozed_until: Any
    at: float  # when its read snapshot began (`begin`)
    counts: dict[str, Any]  # what every page of it carries over the whole list, worked out by its first page


class NeedsCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: OrderedDict[tuple[str, str], Built] = OrderedDict()
        # Each viewer's newest snapshot kept in the last TTL seconds, whether or not its list is still held (evicted
        # for room): a build that began earlier never takes its place.
        self._newest: dict[str, float] = {}

    def keep(
        self,
        viewer: str,
        version: str,
        items: list[dict[str, Any]],
        snoozed_until: Any,
        began: float,
        counts: dict[str, Any] | None = None,
    ) -> None:
        """Hold a list the viewer was just given, from a read snapshot that began at `began` (`begin`), in place of
        the viewer's others; not when the viewer has one from a snapshot that began later, or it is TTL old already.
        `counts` is the list's page counts, shared with the caller, who may fill it in after."""
        now = clock()
        with self._lock:
            for seen in [v for v, at in self._newest.items() if now - at > TTL]:
                del self._newest[seen]
            if now - began > TTL or began < self._newest.get(viewer, began):
                return
            self._newest[viewer] = began
            for key in [k for k in self._held if k[0] == viewer]:
                del self._held[key]
            self._held[(viewer, version)] = Built(
                list(items), version, snoozed_until, began, {} if counts is None else counts
            )
            while len(self._held) > MAX_ENTRIES:
                self._held.popitem(last=False)

    def get(self, viewer: str, version: str) -> Built | None:
        """The viewer's list at `version` while it is fresh, else None."""
        now = clock()
        with self._lock:
            for key in [k for k, b in self._held.items() if now - b.at > TTL]:
                del self._held[key]
            built = self._held.get((viewer, version))
            if built is not None:
                self._held.move_to_end((viewer, version))
            return built

    def drop(self, viewer: str) -> None:
        """Hold none of the viewer's lists: their next cursor builds."""
        with self._lock:
            for key in [k for k in self._held if k[0] == viewer]:
                del self._held[key]

    def __len__(self) -> int:
        with self._lock:
            return len(self._held)


def of(app: Any) -> NeedsCache:
    """The app's cache, made on first use."""
    cache = getattr(app.state, "needs_cache", None)
    if cache is None:
        with _making:
            cache = getattr(app.state, "needs_cache", None)
            if cache is None:
                cache = NeedsCache()
                app.state.needs_cache = cache
    return cache


def begin() -> float:
    """The time a build's read snapshot begins, taken before it opens: the snapshot holds every write that ended before
    it, so of two builds of a viewer the one that began later holds all the earlier one may lack."""
    return clock()


def viewer(who: Any) -> str:
    """The cache key of a caller: their whole identity, so two callers who may see different things never share."""
    return repr(who)


_making = threading.Lock()
