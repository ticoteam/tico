"""One log line per outage, not one per failed poll, and how long to wait before the next try."""

import collections
import re
import time

from clients.tico import APIError

# The last few lines that say something went wrong, for a support bundle (backend/diagnostics.py); it is
# the same text the runner prints, kept in memory only and sent only in a heartbeat the server reads.
TROUBLE = re.compile(r"error|fail|unavailable|refused|rejected|interrupted|cannot|could not|not updating|exception|limit", re.I)
RECENT = collections.deque(maxlen=50)


def log(line, *, diagnostic=False):
    from .redact import scrub_log            # a running turn's secrets never reach the log
    line = scrub_log(line)
    print(time.strftime("%Y-%m-%d %H:%M:%S") + " " + line, flush=True)
    if diagnostic or TROUBLE.search(line):
        # The server keeps at most 300 characters per line; the timestamp and space are 21 of them.
        RECENT.append((time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + line.replace("\n", " "))[:300])


def span(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02}m"


def describe(exc):
    if isinstance(exc, APIError):
        detail = exc.detail if exc.detail.startswith("HTTP ") or not exc.status else f"HTTP {exc.status}: {exc.detail}"
        return f"{exc.code}, {detail}"
    return type(exc).__name__


class Outage:
    """A recurring failure that should read as one event in the log.

    `failed(exc)` prints on the first failure, then at most one progress line per `progress`
    seconds while the same thing keeps failing, and returns how long to wait before the next try
    (1 s, 2 s, 4 s ... capped at `cap`). `recovered()` prints once when the next call succeeds
    and resets the backoff. The three phrases finish "<prefix>: ..." lines:

        Tico runner: cloud unavailable (http_error, HTTP 530: Error 1033: ...); retrying
        Tico runner: still unavailable after 47 failures / 3m12s
        Tico runner: cloud reachable again after 3m40s (47 failures)
    """

    def __init__(self, prefix, down="cloud unavailable", still="still unavailable", up="cloud reachable again",
                 *, progress=60, cap=15, clock=time.monotonic, out=None):
        self.prefix, self.down, self.still, self.up = prefix, down, still, up
        self.progress, self.cap, self.clock = progress, cap, clock
        self.out = out or log
        self.failures, self.since, self.last_line, self.shown = 0, None, None, None

    @property
    def failing(self):
        return self.failures > 0

    def failed(self, exc):
        now = self.clock()
        self.failures += 1
        what = describe(exc)
        if self.since is None:
            self.since = self.last_line = now
            self.shown = what
            self.out(f"{self.prefix}: {self.down} ({what}); retrying")
        elif now - self.last_line >= self.progress:
            self.last_line = now
            changed = "" if what == self.shown else f" ({what})"
            self.shown = what
            self.out(f"{self.prefix}: {self.still} after {self.failures} failures / {span(now - self.since)}{changed}")
        return min(self.cap, 2 ** min(self.failures - 1, 16))

    def recovered(self):
        if self.since is None:
            return
        line = f"{self.prefix}: {self.up} after {span(self.clock() - self.since)} ({self.failures} failures)"
        if self.out is log:
            log(line, diagnostic=True)
        else:
            self.out(line)
        self.failures, self.since, self.last_line, self.shown = 0, None, None, None
