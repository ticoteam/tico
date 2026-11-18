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
- A viewer keeps only their newest version: storing a build drops the same viewer's older ones, so after a live
  `needs` event mends a page (`ids=`, which builds) a cursor from before it starts again from the top.
- Memory is bounded: at most MAX_ENTRIES lists, oldest used first out, each kept TTL seconds at most.

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
    at: float


class NeedsCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: OrderedDict[tuple[str, str], Built] = OrderedDict()

    def keep(self, viewer: str, version: str, items: list[dict[str, Any]], snoozed_until: Any) -> None:
        """Hold a list the viewer was just given, in place of any older version of theirs."""
        built = Built(list(items), version, snoozed_until, clock())
        with self._lock:
            for key in [k for k in self._held if k[0] == viewer and k[1] != version]:
                del self._held[key]
            self._held[(viewer, version)] = built
            self._held.move_to_end((viewer, version))
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


def viewer(who: Any) -> str:
    """The cache key of a caller: their whole identity, so two callers who may see different things never share."""
    return repr(who)


_making = threading.Lock()
