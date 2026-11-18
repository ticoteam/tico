# pyright: strict
"""Each API client's fair share of the server (docs/api.md#rate-limits): one script must not slow the whole company.

One person's API token reading a task 600 times a minute, each read about a second of SQL, left runners' heartbeats
waiting 6.5 s and people's Needs you 14 s behind it, and writes failing `busy`. A client here is one API token or
service key, one bot or one person's Assistant; each has a budget of requests running at once and requests in any
60 seconds. Past either one the request is refused at once with 429 `rate_limited`, a `Retry-After` header and the
same seconds in `retry_after`, before it reads anything, so the burst costs the company almost nothing. Clients
retry 429 with backoff (clients/tico.py, ui/app/api.ts).

Runners (heartbeats, claims, a run's events) and people in the app (a browser sign-in, a frontend's bearer session,
Tico support, a bot's read-only view of the app) have no budget: they are what the budgets protect. A wait on another
system that holds no database time (a browser command, a Gateway image or video) takes no concurrency slot; each has
its own cap, and a browser command is sent once, so a refusal would lose it. It still counts toward the minute. An MCP
call counts as the API requests it makes.

Counted in this process's memory, like a push address's minute (backend/listening.py): a restart starts every
window again. The numbers are settings (`TICO_FAIR_SHARE_*`, backend/config.py); 0 turns that limit off."""

import collections
import math
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import NoReturn, Self

from .auth import Identity
from .config import Settings
from .store import Problem

WINDOW = 60.0
# Requests that wait on another system, not on the database (clients/tico.py LONG_POSTS, VIDEO_CONTENT).
WAITS = re.compile(r"/api/v2/(?:browser/commands|gateway/images|gateway/videos/[^/]+/content)")
# The MCP server's own request (backend/mcp.py): each tool call comes back through the same middleware as ordinary
# requests with the caller's credential, and those are what count. Counting both would spend two slots per call.
RELAYS = ("/api/v2/mcp",)
SWEEP_EVERY = 1000  # admissions between sweeps of clients that went quiet


@dataclass(frozen=True)
class Budget:
    concurrent: int  # requests running at once; 0 is no limit
    per_minute: int  # requests started in any 60 seconds; 0 is no limit


def client(who: Identity) -> tuple[str, str] | None:
    """(budget kind, client key) for a caller with a budget, or None. The key is the credential where there is one,
    so two tokens of one person are two clients, and the Assistant acting for a person is not that person's token."""
    if who.role == "runner" or who.view:
        return None
    if who.via == "assistant":
        return "bot", "assistant:" + who.actor
    if who.via_token:
        return "token", "token:" + (who.token_id or who.actor + "/" + who.token_label)
    if who.role == "service":
        return "token", who.actor
    if who.role == "bot":
        return "bot", who.actor
    return None


def _named(who: Identity) -> str:
    if who.via == "assistant":
        return "The Assistant"
    if who.via_token:
        return f"The API token “{who.token_label}”" if who.token_label else "This API token"
    if who.role == "service":
        return "This service key"
    return "This bot"


class FairShare:
    def __init__(self, budgets: dict[str, Budget], clock: Callable[[], float] = time.monotonic) -> None:
        self.budgets = budgets  # {kind: Budget}
        self.clock = clock
        self.lock = threading.Lock()
        self.running: collections.Counter[str] = collections.Counter()  # key -> requests running now that hold a slot
        self.started: dict[str, collections.deque[float]] = {}  # key -> deque of monotonic start times within WINDOW
        self.admitted = 0

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            {
                "token": Budget(settings.fair_share_token_concurrent, settings.fair_share_token_per_minute),
                "bot": Budget(settings.fair_share_bot_concurrent, settings.fair_share_bot_per_minute),
            }
        )

    def admit(self, who: Identity | None, path: str) -> str | None:
        """A ticket for `done` when the request may run (None when it has no budget or takes no slot); else raises
        429 `rate_limited`, which counts toward nothing."""
        if who is None or path in RELAYS:
            return None
        found = client(who)
        if found is None:
            return None
        kind, key = found
        budget = self.budgets.get(kind)
        if budget is None or not budget.concurrent and not budget.per_minute:
            return None
        holds = bool(budget.concurrent) and not WAITS.fullmatch(path)
        now = self.clock()
        with self.lock:
            window = self.started.setdefault(key, collections.deque[float]())
            while window and window[0] <= now - WINDOW:
                window.popleft()
            if holds and self.running[key] >= budget.concurrent:
                self._refuse(
                    who,
                    1,
                    f"already has {budget.concurrent} requests running, its limit at once",
                    {"limit": budget.concurrent, "window": "concurrent"},
                )
            if budget.per_minute and len(window) >= budget.per_minute:
                wait = max(1, math.ceil(window[0] + WINDOW - now))
                self._refuse(
                    who,
                    wait,
                    f"made {budget.per_minute} requests in the last minute, its limit",
                    {"limit": budget.per_minute, "window": "minute"},
                )
            if budget.per_minute:
                window.append(now)
            if holds:
                self.running[key] += 1
            self.admitted += 1
            if self.admitted % SWEEP_EVERY == 0:
                self._sweep(now)
        return key if holds else None

    def done(self, ticket: str | None) -> None:
        if ticket is None:
            return
        with self.lock:
            self.running[ticket] -= 1
            if self.running[ticket] <= 0:
                del self.running[ticket]

    def _refuse(self, who: Identity, wait: int, what: str, extra: dict[str, int | str]) -> NoReturn:
        raise Problem(
            "rate_limited",
            f"{_named(who)} {what}, so other work in the company is not held up. Retry in {wait} s, and "
            "follow /api/v2/events instead of reading the same thing again.",
            429,
            retryable=True,
            extra={**extra, "retry_after": wait},
        )

    def _sweep(self, now: float) -> None:
        for key in [k for k, w in self.started.items() if not w or w[-1] <= now - WINDOW]:
            if not self.running.get(key):
                del self.started[key]
