"""Where a request's time goes (is it the backend or the frontend?).

Per route, the last 300 requests: how long signing in took (it waits for a worker thread) and how
long the whole answer took on the server. Also how late the event loop wakes up (a blocked loop
delays every request) and how many requests are in flight. Numbers only: no paths with ids, no
bodies, no people. `GET /api/v2/ops/timing` reads it; every answer carries `Server-Timing`. When the
flight recorder is on (backend/flight.py), every request and loop wake is also handed to it, which keeps
history in the database.
"""

import asyncio
import threading
import time
from collections import defaultdict, deque


def _pct(values, q):
    if not values:
        return 0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1)


class Timing:
    def __init__(self, keep=300):
        self.lock = threading.Lock()
        self.routes = defaultdict(lambda: deque(maxlen=keep))
        self.lag = deque(maxlen=keep)
        self.inflight = self.peak = 0
        self.since = time.time()
        self.recorder = None

    def begin(self):
        with self.lock:
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)

    def end(self, route, auth_ms, total_ms, caller="anonymous", status=200, nbytes=0, actor="", cost=None):
        with self.lock:
            self.inflight -= 1
            self.routes[route].append((auth_ms, total_ms))
        if self.recorder is not None:
            self.recorder.request(route, caller, status, total_ms, nbytes, actor, cost)

    def summary(self):
        with self.lock:
            rows = []
            for route, samples in self.routes.items():
                auth = [a for a, _ in samples]
                total = [t for _, t in samples]
                rows.append(
                    {
                        "route": route,
                        "n": len(samples),
                        "auth_p50": _pct(auth, 0.5),
                        "auth_p95": _pct(auth, 0.95),
                        "p50": _pct(total, 0.5),
                        "p95": _pct(total, 0.95),
                        "max": round(max(total), 1),
                    }
                )
            lag = list(self.lag)
            return {
                "since": self.since,
                "inflight": self.inflight,
                "peak_inflight": self.peak,
                "loop_lag_p50": _pct(lag, 0.5),
                "loop_lag_p95": _pct(lag, 0.95),
                "loop_lag_max": round(max(lag), 1) if lag else 0,
                "routes": sorted(rows, key=lambda r: -r["p95"] * r["n"]),
            }


async def watch_loop(timing, stop, every=0.5):
    """How late the event loop wakes from a short sleep: anything blocking it shows here."""
    try:
        while not stop.is_set():
            started = time.perf_counter()
            await asyncio.sleep(every)
            lag = (time.perf_counter() - started - every) * 1000
            timing.lag.append(lag)
            if timing.recorder is not None:
                timing.recorder.tick(lag)
    finally:
        if timing.recorder is not None:
            timing.recorder.beat = None  # a probe that stopped is not a stalled loop
