"""The flight recorder: this server's own request, process and database history, kept in its database.

An incident (a server pinned at 100% CPU for hours) should be one query, and a release should be comparable with the one
before it, without installing profilers on the server. So the server keeps, per minute: requests per route template and
caller kind (count, 5xx, p50/p95/max, bytes), process CPU, memory, threads, open files and event-loop lag, and the write
transactions' lock waits and hold times; per hour: SQL time by query shape and database totals, with table sizes daily; and as
events: each start (release, commit, dependency and settings hashes, whether the image is the published release) and
each stall of the event loop with every thread's stack at that moment, the stall's class (cpu, memory, disk, lock or
unknown) and the resource counters it was read from.

Stalls, slow requests and the per-minute rows go first to a local NDJSON file beside the database
(backend/flight_file.py), which `python -m backend.flight summary --since 30m` reads with the server stopped or stuck;
the tables here are written after, with a short lock wait, for the Health page.

The request path pays for two clock reads and a deque append (`Recorder.request`, `Recorder.query`); everything else
(bucketing, fingerprinting SQL, writing) happens in one background thread (`Flight`), which writes once a minute in one
short transaction. Nothing leaves the server: no query text with values (SQL is reduced to its shape), no request paths
with ids (route templates only), no bodies, no setting values. Owners, admins and the team's bots read it at
`GET /api/v2/system/metrics` (bots without the slow requests' actors), and owners and admins on the Health page.
People's browsers add their own first-load and interaction times (LCP, INP) per screen through
`POST /api/v2/web-timings`, kept as histogram buckets only (`flight_web`, made by its migration).
Retention: minutes for two days, then hours; requests, browser timings, slow requests and process rows 14 days, SQL,
database and events 90 days (`sweep`, run by the scheduler's hourly sweep).
"""

import asyncio
import bisect
import collections
import contextvars
import hashlib
import itertools
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
import traceback

from . import background, hubdb


def _now():
    """The wall clock (epoch seconds): a test replaces this and not time.time for the whole process."""
    return time.time()


LOG = logging.getLogger("tico.flight")

SLOW_MS = 1000  # a request this slow is logged on its own
SLOW_PER_MINUTE = 50
SLOW_KEEP = 5000
TOP_STATEMENTS = 5  # a slow request names its costliest statement shapes, this many
STATEMENT_KEYS = 400  # distinct statement texts kept per request; later new ones are only counted
STALL_S = 2.0  # the event loop late by this much is a stall
STACK_GAP = 60  # every thread's stack with a stall, at most this often
TICK = 0.25
DB_WAIT = 2.0  # seconds the recorder's own database writes wait for the write lock; the file has it all
PENDING_MAX = 120  # table writes kept for the next try while the lock is busy (about two hours of minutes)
LONG_HOLD_MS = 5000  # a write transaction holding the lock this long is logged with its route and code site
LONG_HOLD_PER_MINUTE = 20
EVENTS_PER_MINUTE = 20  # events a request raises (`note`) kept a minute; the rest are counted as `dropped` in theirs
LOG_HOLD_MS = 250  # a write transaction holding the lock this long goes to the server log with its code site
LOG_HOLD_PER_MINUTE = 30
MINUTE_DAYS = 2  # request minutes older than this become hours
SHORT_DAYS = 14  # requests, slow requests, process minutes
LONG_DAYS = 90  # SQL hours, database sizes, events
SQL_TOP = 100  # query shapes kept per hour; the rest are one "(other)" row
SIZES_EVERY = 86400  # minimum interval between table/index size estimates
SAMPLE_WINDOWS = 3  # runs of rows sampled per table for its record size, spread over its rowid range
SAMPLE_ROWS = 100  # rows per sampled run
BUFFER = 200_000  # samples waiting for the background thread, at most
# The tables that grow, counted each hour; with the column that dates their oldest row, read by rowid or an index.
BIG = {
    "events": "created_at",
    "messages": "created_at",
    "attempt_events": "created_at",
    "attempts": "created_at",
    "jobs": "created_at",
    "idempotency": "created_at",
    "changes": "created_at",
    "tasks": "created_at",
    "flight_requests": None,
}
# Histogram bucket edges in milliseconds: 25% apart from 0.05 ms to about a minute. A percentile is its bucket's upper
# edge, so it reads at most a quarter high and never low.
EDGES = [0.05 * 1.25**i for i in range(64)]
# What people's browsers measure (ui/observability.ts, `vitals`): first-load LCP and the slowest interaction (INP) per
# screen visit, per screen template. A reading is only its metric, screen and histogram bucket in the minute it came;
# never who, which device, the URL or the element. `dropped` counts readings over a person's per-minute allowance.
WEB_METRICS = ("lcp", "inp")
# The app's screens (ui/observability.ts SCREENS, from ui/app/router.ts): `/<screen>`, or `/<screen>/<placeholder>` for
# its pages, never with an id.
WEB_SCREENS = (
    "assistant automations bot calendar changelog chat company-page computers credentials decide docs file finance getting-started "
    "goals growth health help home inbox integrations learnings library listening mail market meetings messaging notes "
    "overview page person recordings replay repositories roadmap rules runs seat settings skills sql strategy style-guide "
    "subscriptions tag tags task tasks team thread threads tools triggers updates usage webhooks welcome"
).split()
_PLACEHOLDERS = {"bot": ":bot", "person": ":person", "task": ":task", "thread": ":thread"}
WEB_ROUTES = frozenset(
    route for screen in WEB_SCREENS for route in ("/" + screen, f"/{screen}/{_PLACEHOLDERS.get(screen, ':sub')}")
)
WEB_TIMINGS_ROUTE = "POST /api/v2/web-timings"  # the browsers' own sends: listed apart from people's routes
WEB_MAX_MS = 60_000
WEB_PER_MINUTE = 120  # readings one person sends in a minute, at most; the rest are dropped and counted
WEB_BATCH = 20  # readings in one request, at most

_LITERAL = re.compile(r"'(?:[^']|'')*'")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_LIST = re.compile(r"\(\s*\?(?:\s*,\s*\?)+\s*\)")
_SPACE = re.compile(r"\s+")


def fingerprint(sql):
    """A statement's shape: literals, numbers and lists of placeholders folded, so one query is one row."""
    text = _SPACE.sub(" ", _NUMBER.sub("N", _LITERAL.sub("'?'", str(sql)))).strip()
    return _LIST.sub("(?,…)", text)[:300]


def top_statements(statements, fingerprints=None):
    """A request's TOP_STATEMENTS costliest statement shapes, [{sql, n, ms}], literals folded as in flight_sql."""
    if not statements:
        return []
    shapes = {}
    for sql, (n, ms) in list(statements.items()):
        fp = fingerprints.get(sql) if fingerprints is not None else None
        fp = fp or fingerprint(sql)
        row = shapes.setdefault(fp, [0, 0.0])
        row[0] += n
        row[1] += ms
    ranked = sorted(shapes.items(), key=lambda kv: -kv[1][1])[:TOP_STATEMENTS]
    return [{"sql": fp, "n": n, "ms": round(ms, 1)} for fp, (n, ms) in ranked]


def quantile(hist, q):
    total = sum(hist.values())
    if not total:
        return 0
    seen, target = 0, q * total
    for i in sorted(hist):
        seen += hist[i]
        if seen >= target:
            return round(EDGES[min(i, len(EDGES) - 1)], 2)
    return 0


def _add(hist, ms):
    i = bisect.bisect_left(EDGES, ms)
    hist[i] = hist.get(i, 0) + 1


def _merge(into, hist):
    for k, v in hist.items():
        into[int(k)] = into.get(int(k), 0) + v


# Per request: SQL milliseconds, write-lock wait milliseconds, statements and {statement: [n, ms]}, added to by the
# request's own threads. The middleware sets a fresh list; threads the request runs on copy the context, so they add to
# the same list. The statements let a slow request name where its SQL time went (TOP_STATEMENTS).
_COST = contextvars.ContextVar("tico_flight_cost", default=None)
# The request's method and ASGI scope, so a write transaction that holds the lock too long can name its route.
_ROUTE = contextvars.ContextVar("tico_flight_route", default=None)
_TOKENS = itertools.count()
ACTIVE = None  # the server's recorder, for write transactions on connections it did not open


def request_started(method=None, scope=None):
    cost = [0.0, 0.0, 0, {}]
    return cost, (_COST.set(cost), _ROUTE.set((method, scope) if scope is not None else None))


def request_finished(token):
    cost, route = token
    _COST.reset(cost)
    _ROUTE.reset(route)


def _route_now():
    """`POST /api/v2/runners/performance` when called on a request's threads, else None (a background job)."""
    held = _ROUTE.get()
    if not held:
        return None
    method, scope = held
    found = scope.get("route")
    return f"{method} {getattr(found, 'path', None) or 'unmatched'}"


_PLUMBING = {"flight.py", "store.py", "contextlib.py", "threading.py"}


def _site(frames):
    """The innermost frame of this code base outside the transaction plumbing: `backend/performance.py:477 report`."""
    for f in reversed(frames):
        if (
            f.filename.startswith(ROOT)
            and "site-packages" not in f.filename
            and os.path.basename(f.filename) not in _PLUMBING
        ):
            return f"{_where(f.filename)}:{f.lineno} {f.name}"
    return None


def begin(conn, recorder=None, *, gate=None, checkout=None):
    """`BEGIN IMMEDIATE`, timed for the flight recorder: how long the write lock took to get (counted while it is still
    being waited for, so a stall can see it) and, when the returned `done` is called after COMMIT or ROLLBACK, how
    long it was held. `checkout` borrows a connection after queue admission, so waiters do not drain the read pool."""
    rec = recorder or getattr(conn, "recorder", None) or ACTIVE
    if rec is None:
        if gate is not None:
            gate.acquire()
        try:
            if checkout is not None:
                conn = checkout()
            conn.execute("BEGIN IMMEDIATE")
        except BaseException:
            if gate is not None:
                gate.release()
            raise
        return gate.release if gate is not None else _nothing
    token = next(_TOKENS)
    started = time.monotonic()
    rec.waiting[token] = started
    acquired = False
    try:
        if gate is not None:
            gate.acquire()
            acquired = True
        if checkout is not None:
            conn = checkout()
        conn.execute("BEGIN IMMEDIATE")
    except BaseException as exc:
        if acquired:
            gate.release()
        rec.transaction(
            (time.monotonic() - started) * 1000, 0.0, isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc)
        )
        raise
    finally:
        rec.waiting.pop(token, None)
    held = time.monotonic()
    route = _route_now()
    rec.holding[token] = (held, route, threading.get_ident())

    def done():
        rec.holding.pop(token, None)
        hold_ms = (time.monotonic() - held) * 1000
        if gate is not None:
            gate.release()
        rec.transaction((held - started) * 1000, hold_ms)
        site = _log_hold(route, hold_ms, (held - started) * 1000) if hold_ms >= LOG_HOLD_MS else None
        if hold_ms >= LONG_HOLD_MS:  # who held it: the call site is read only for a long hold
            rec.long_hold(route, site or _site(traceback.extract_stack()), hold_ms, (held - started) * 1000)

    return done


def _nothing():
    pass


_logged = [0, 0]  # [minute, holds logged in it]


def _log_hold(route, hold_ms, wait_ms):
    minute = int(time.time() // 60)
    if _logged[0] != minute:
        _logged[:] = [minute, 0]
    if _logged[1] >= LOG_HOLD_PER_MINUTE:
        return None
    _logged[1] += 1
    site = _site(traceback.extract_stack())
    LOG.warning("write lock held %.0f ms (waited %.0f ms) at %s %s", hold_ms, wait_ms, site, route or "")
    return site


def caller_kind(who):
    role = getattr(who, "role", "") if who is not None else ""
    return {"owner": "human", "human": "human", "bot": "bot", "runner": "runner", "service": "service"}.get(
        role, "anonymous"
    )


def client_of(who):
    """How a signed-in caller reached the server, for a slow request's record: "token:<label>" for a personal API
    token (the assistant or script it names), "assistant" for the Assistant acting for a person, "browser" for any
    other person's sign-in. Empty for bots, runners and service keys, whose actor already says what they are. A
    person's tag lists came from six accounts at hundreds an hour, and nothing said which of these sent them."""
    if who is None or getattr(who, "role", "") not in ("owner", "human"):
        return ""
    if getattr(who, "via_token", False):
        return "token:" + (getattr(who, "token_label", "") or "unnamed")[:40]
    return "assistant" if getattr(who, "via", "") == "assistant" else "browser"


def _pct(values, q):
    if not values:
        return 0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1)


class Timing:
    """Where a request's time goes (is it the backend or the frontend?), in memory since the start: per route, the last
    300 requests' sign-in time (it waits for a worker thread) and whole answer time on the server; how late the event
    loop wakes (a blocked loop delays every request); and how many requests are in flight. Numbers only: no paths with
    ids, no bodies, no people. `GET /api/v2/operations/timing` reads it, every answer carries `Server-Timing`, and with
    the recorder on each request and loop wake is also handed to it, which keeps the history."""

    def __init__(self, keep=300):
        self.lock = threading.Lock()
        self.routes = collections.defaultdict(lambda: collections.deque(maxlen=keep))
        self.lag = collections.deque(maxlen=keep)
        self.inflight = self.peak = 0
        self.since = time.time()
        self.recorder = None

    def begin(self):
        with self.lock:
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)

    def end(self, route, auth_ms, total_ms, caller="anonymous", status=200, nbytes=0, actor="", cost=None, client=""):
        with self.lock:
            self.inflight -= 1
            self.routes[route].append((auth_ms, total_ms))
        if self.recorder is not None:
            self.recorder.request(route, caller, status, total_ms, nbytes, actor, cost, client)

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


def install_middleware(app, recorder):
    @app.middleware("http")
    async def request_timing(request, call_next):
        started = time.perf_counter()
        request.state.auth_ms = 0.0
        app.state.timing.begin()
        # This request's SQL time and write-lock wait, added to by the threads it runs on.
        cost, cost_token = request_started(request.method, request.scope) if recorder is not None else (None, None)
        route = request.url.path
        response = None
        try:
            response = await call_next(request)
            found = request.scope.get("route")
            route = getattr(found, "path", None) or ("static" if not route.startswith("/api") else "unmatched")
            total = (time.perf_counter() - started) * 1000
            extra = getattr(request.state, "server_timing", "")
            response.headers["Server-Timing"] = (
                f"auth;dur={request.state.auth_ms:.1f}, app;dur={total:.1f}"
                + (f", sql;dur={cost[0]:.1f}, wait;dur={cost[1]:.1f}" if cost is not None else "")
                + (", " + extra if extra else "")
            )
            return response
        finally:
            found = request.scope.get("route")
            key = (
                request.method
                + " "
                + (
                    getattr(found, "path", None)
                    or ("static" if not request.url.path.startswith("/api") else "unmatched")
                )
            )
            # The recorder's caller kind, status and size; a raised error counts as a 500.
            who = getattr(request.state, "identity", None)
            size = response.headers.get("content-length") if response is not None else None
            # Screen long-polls wait for new pixels; retain SQL/lock/processing time, exclude only deliberate sleeps.
            active_ms = max(0, (time.perf_counter() - started) * 1000 - getattr(request.state, "idle_wait_ms", 0))
            app.state.timing.end(
                key,
                request.state.auth_ms,
                active_ms,
                caller_kind(who),
                response.status_code if response is not None else 500,
                int(size) if size and size.isdigit() else 0,
                getattr(who, "actor", "") or "",
                cost,
                client_of(who),
            )
            if cost_token is not None:
                request_finished(cost_token)


def register_timing(app):
    from fastapi import Request

    from .store import Problem

    @app.get("/api/v2/operations/timing")
    def request_timings(request: Request):
        """Where requests spend their time (`Timing`): the owner, or Agent Ops."""
        who = request.state.identity
        if who.role != "owner" and who.actor != "bot:botops":
            raise Problem("forbidden", "Request timings are for the owner and Agent Ops", 403)
        return app.state.timing.summary()


class Recorder:
    """In-memory collection. The request and SQL paths only append; `drain` (the background thread) does the rest."""

    def __init__(self):
        self.samples = collections.deque(maxlen=BUFFER)
        self.lock = threading.Lock()
        self.fingerprints = {}
        self._reset()
        self.sql = {}  # hour -> fingerprint -> [n, total, max, hist]
        self.beat = None  # monotonic time the event loop last woke
        self.loop_thread = None
        self.cpu = (time.monotonic(), time.process_time())
        self.dropped = 0
        self.waiting = {}  # write-lock waits still going: token -> monotonic start
        self.holding = {}  # write locks held now: token -> (monotonic start, route, thread id)
        self.wait_total = 0.0  # write-lock wait finished since the start, ms
        self.slow_file = []  # slow requests not yet in the file
        self._drainer = None  # the thread that drains `samples` (start_drain)
        self._drain_halt = None

    def _reset(self):
        self.requests = {}  # (route, caller) -> [n, errors, bytes, total, max, hist, bytes_max, sql, wait]
        self.slow = []
        self.wait_sum = 0.0
        self.lag = {}
        self.lag_max = 0.0
        self.queries = 0
        self.sql_ms = 0.0
        self.txns = 0
        self.wait_max = self.hold_max = 0.0
        self.hold = {}
        self.locked = 0
        self.long_holds = []
        self.events = []  # (ts, kind, detail) raised by requests (`note`), written to flight_events
        self.events_dropped = 0
        self.web = {}  # (metric, route) -> hist
        self.web_dropped = 0

    # The hot paths: one append each, safe from any thread. A full buffer (the background thread is not keeping up)
    # discards the oldest sample; `dropped` counts them, approximately, so the gap shows in the minute's row.
    def request(self, route, caller, status, ms, nbytes=0, actor="", cost=None, client=""):
        if len(self.samples) >= BUFFER:
            self.dropped += 1
        sql_ms, wait_ms, queries = cost[:3] if cost else (0.0, 0.0, 0)
        statements = cost[3] if cost is not None and len(cost) > 3 else None
        self.samples.append(
            ("r", route, caller, status, ms, nbytes, actor, sql_ms, wait_ms, queries, statements, client)
        )

    def query(self, sql, ms, locked=False):
        if len(self.samples) >= BUFFER:
            self.dropped += 1
        self.samples.append(("q", sql, ms, locked))
        cost = _COST.get()
        if cost is not None:
            cost[0] += ms
            cost[2] += 1
            if len(cost) > 3:
                seen = cost[3].get(sql)
                if seen is not None:
                    seen[0] += 1
                    seen[1] += ms
                elif len(cost[3]) < STATEMENT_KEYS:
                    cost[3][sql] = [1, ms]

    def transaction(self, wait_ms, hold_ms, locked=False):
        if len(self.samples) >= BUFFER:
            self.dropped += 1
        self.samples.append(("t", wait_ms, hold_ms, locked))
        self.wait_total += wait_ms
        cost = _COST.get()
        if cost is not None:
            cost[1] += wait_ms

    def web_timing(self, metric, route, ms):
        """One reading from a person's browser, already checked (`install`, POST /api/v2/web-timings)."""
        if len(self.samples) >= BUFFER:
            self.dropped += 1
        self.samples.append(("w", metric, route, ms))

    def web_drop(self, n=1):
        self.samples.append(("wd", n))

    def long_hold(self, route, site, hold_ms, wait_ms):
        """A write transaction that held the lock for LONG_HOLD_MS or more, from the thread that held it."""
        self.samples.append(("h", int(time.time()), route, site, hold_ms, wait_ms))

    def event(self, kind, detail):
        """Something a request saw that a reading of the recorder needs later (`note`): one append."""
        self.samples.append(("e", int(time.time()), kind, detail))

    def tick(self, lag_ms):
        """The event loop woke `lag_ms` late (`watch_loop`)."""
        self.beat = time.monotonic()
        if self.loop_thread is None:
            self.loop_thread = threading.get_ident()
        if len(self.samples) >= BUFFER:
            self.dropped += 1
        self.samples.append(("l", lag_ms))

    def drain(self, now=None):
        hour = int((now or time.time()) // 3600 * 3600)
        edges, fingerprints, find = EDGES, self.fingerprints, bisect.bisect_left
        with self.lock:
            shapes = None  # this hour's shapes, once a query sample lands in it (an empty hour is not kept)
            while True:
                try:
                    s = self.samples.popleft()
                except IndexError:
                    return
                kind = s[0]
                if kind == "q":
                    _, sql, ms, locked = s
                    if shapes is None:
                        shapes = self.sql.setdefault(hour, {})
                    fp = fingerprints.get(sql)
                    if fp is None:
                        if len(fingerprints) > 5000:
                            fingerprints.clear()
                        fp = fingerprints[sql] = fingerprint(sql)
                    row = shapes.get(fp)
                    if row is None:
                        row = shapes[fp] = [0, 0.0, 0.0, {}]
                    row[0] += 1
                    row[1] += ms
                    if ms > row[2]:
                        row[2] = ms
                    hist = row[3]
                    i = find(edges, ms)
                    hist[i] = hist.get(i, 0) + 1
                    self.queries += 1
                    self.sql_ms += ms
                    if locked:
                        self.locked += 1
                elif kind == "r":
                    _, route, caller, status, ms, nbytes, actor, sql_ms, wait_ms, queries, statements, client = s
                    row = self.requests.get((route, caller))
                    if row is None:
                        row = self.requests[(route, caller)] = [0, 0, 0, 0.0, 0.0, {}, 0, 0.0, 0.0]
                    row[0] += 1
                    row[1] += status >= 500
                    row[2] += nbytes or 0
                    row[3] += ms
                    row[4] = max(row[4], ms)
                    _add(row[5], ms)
                    row[6] = max(row[6], nbytes or 0)
                    row[7] += sql_ms
                    row[8] += wait_ms
                    if ms >= SLOW_MS and len(self.slow) < SLOW_PER_MINUTE:
                        top = top_statements(statements, fingerprints)
                        slow = (
                            int(time.time()),
                            route,
                            caller,
                            actor,
                            round(ms, 1),
                            nbytes or 0,
                            status,
                            round(sql_ms, 1),
                            round(wait_ms, 1),
                            queries,
                            json.dumps(top, separators=(",", ":")) if top else None,
                        )
                        self.slow.append(slow)
                        self.slow_file.append(
                            {
                                "kind": "slow",
                                "route": route,
                                "caller": caller,
                                "actor": actor,
                                **({"client": client} if client else {}),
                                "ms": slow[4],
                                "bytes": slow[5],
                                "status": status,
                                "sql_ms": round(sql_ms, 1),
                                "wait_ms": round(wait_ms, 1),
                                "queries": queries,
                                **({"top": top} if top else {}),
                            }
                        )
                elif kind == "t":
                    _, wait, hold, locked = s
                    self.txns += 1
                    self.wait_sum += wait
                    self.wait_max = max(self.wait_max, wait)
                    self.hold_max = max(self.hold_max, hold)
                    _add(self.hold, hold)
                    self.locked += bool(locked)
                elif kind == "h":
                    _, ts, route, site, hold, wait = s
                    if len(self.long_holds) < LONG_HOLD_PER_MINUTE:
                        self.long_holds.append(
                            {
                                "ts": ts,
                                "route": route,
                                "site": site,
                                "hold_s": round(hold / 1000, 1),
                                "wait_s": round(wait / 1000, 1),
                            }
                        )
                elif kind == "e":
                    if len(self.events) < EVENTS_PER_MINUTE:
                        self.events.append(s[1:])
                    else:
                        self.events_dropped += 1
                elif kind == "l":
                    _add(self.lag, s[1])
                    self.lag_max = max(self.lag_max, s[1])
                elif kind == "w":
                    _, metric, route, ms = s
                    _add(self.web.setdefault((metric, route), {}), ms)
                elif kind == "wd":
                    self.web_dropped += s[1]

    def start_drain(self, every=TICK):
        """Drains `samples` on a thread of its own, every `every` seconds, so nothing that waits on the database (a
        flush, a stall's record, the hourly size pass) can leave the buffer to fill and drop samples. Idempotent."""
        if self._drainer is not None:
            return
        halt = self._drain_halt = threading.Event()

        def loop():
            while not halt.wait(every):
                try:
                    self.drain()
                except Exception as exc:
                    LOG.warning("Flight recorder drain failed: %s", type(exc).__name__)

        self._drainer = background.start("tico-flight-drain", loop)

    def stop_drain(self):
        if self._drainer is None:
            return
        self._drain_halt.set()
        self._drainer.join(timeout=5)
        self._drainer = None

    def pop_slow(self):
        with self.lock:
            out, self.slow_file = self.slow_file, []
        return out

    def open_holds(self, now=None):
        """Write locks held LONG_HOLD_MS or more and still held, each with its route and where its thread is now."""
        now = time.monotonic() if now is None else now
        frames = None
        out = []
        for since, route, ident in list(self.holding.values()):
            if (now - since) * 1000 < LONG_HOLD_MS:
                continue
            frames = sys._current_frames() if frames is None else frames
            frame = frames.get(ident)
            out.append(
                {
                    "ts": int(time.time()),
                    "route": route,
                    "open": True,
                    "hold_s": round(now - since, 1),
                    "site": _site(traceback.extract_stack(frame)) if frame is not None else None,
                }
            )
        return out[:LONG_HOLD_PER_MINUTE]

    def take(self, minute):
        """The minute's rows, and the SQL hours, ready to write; the minute's counters start again."""
        self.drain()  # everything recorded so far is in this minute, drained or not
        with self.lock:
            requests, slow, lag, lag_max = self.requests, self.slow, self.lag, self.lag_max
            web, web_dropped = self.web, self.web_dropped
            long_holds = self.long_holds + self.open_holds()
            events = self.events + (
                [(minute, "events_dropped", {"n": self.events_dropped})] if self.events_dropped else []
            )
            totals = (self.queries, self.sql_ms, self.txns, self.wait_max, self.hold_max, self.hold, self.locked)
            wait_sum = self.wait_sum
            self._reset()
            dropped, self.dropped = self.dropped, 0
            sql = {
                hour: {fp: [r[0], r[1], r[2], dict(r[3])] for fp, r in shapes.items()}
                for hour, shapes in self.sql.items()
                if shapes
            }
            current = max(self.sql) if self.sql else None
            self.sql = {current: self.sql[current]} if current is not None else {}
        mono, cpu = time.monotonic(), time.process_time()
        then = self.cpu
        self.cpu = (mono, cpu)
        process = process_stats()
        queries, sql_ms, txns, wait_max, hold_max, hold, locked = totals
        rows = [
            (
                minute,
                60,
                route,
                caller,
                r[0],
                r[1],
                r[2],
                round(r[3], 1),
                quantile(r[5], 0.5),
                quantile(r[5], 0.95),
                round(r[4], 1),
                json.dumps(r[5], separators=(",", ":")),
            )
            for (route, caller), r in requests.items()
        ]
        proc = (
            minute,
            round(100 * (cpu - then[1]) / max(mono - then[0], 1e-6), 1),
            process["rss"],
            process["threads"],
            process["fds"],
            process["sockets"],
            quantile(lag, 0.95),
            round(lag_max, 1),
            sum(r[0] for r in requests.values()),
            queries,
            round(sql_ms, 1),
            txns,
            round(wait_max, 1),
            round(hold_max, 1),
            quantile(hold, 0.95),
            locked,
            dropped,
        )
        # The file's copy (backend/flight_file.py): the same numbers, with each route's largest answer, SQL time and
        # write-lock wait, and the minute's total lock wait.
        names = (
            "ts",
            "cpu",
            "rss",
            "threads",
            "fds",
            "sockets",
            "lag_p95",
            "lag_max",
            "requests",
            "queries",
            "sql_ms",
            "txns",
            "wait_max",
            "hold_max",
            "hold_p95",
            "locked",
            "dropped",
        )
        minute_record = {"kind": "minute", **dict(zip(names, proc)), "wait_ms": round(wait_sum, 1)}
        routes_record = {
            "kind": "routes",
            "ts": minute,
            "span": 60,
            "routes": [
                {
                    "route": route,
                    "caller": caller,
                    "n": r[0],
                    "errors": r[1],
                    "bytes": r[2],
                    "bytes_max": r[6],
                    "total_ms": round(r[3], 1),
                    "p50": quantile(r[5], 0.5),
                    "p95": quantile(r[5], 0.95),
                    "max_ms": round(r[4], 1),
                    "sql_ms": round(r[7], 1),
                    "wait_ms": round(r[8], 1),
                }
                for (route, caller), r in requests.items()
            ],
        }
        sql_rows = []
        for hour, shapes in sql.items():
            ranked = sorted(shapes.items(), key=lambda kv: -kv[1][1])
            other = [0, 0.0, 0.0, {}]
            for fp, (n, total, peak, hist) in ranked[SQL_TOP:]:
                other[0] += n
                other[1] += total
                other[2] = max(other[2], peak)
                _merge(other[3], hist)
            kept = ranked[:SQL_TOP] + ([("(other)", other)] if other[0] else [])
            sql_rows += [
                (
                    hour,
                    fp,
                    n,
                    round(total, 1),
                    quantile(hist, 0.95),
                    round(peak, 2),
                    json.dumps(hist, separators=(",", ":")),
                )
                for fp, (n, total, peak, hist) in kept
            ]
        holds = [{"kind": "long_hold", **h} for h in long_holds]
        web_rows = [
            (minute, 60, metric, route, sum(hist.values()), json.dumps(hist, separators=(",", ":")))
            for (metric, route), hist in web.items()
        ]
        if web_dropped:
            web_rows.append((minute, 60, "dropped", "", web_dropped, "{}"))
        return {
            "requests": rows,
            "web": web_rows,
            "slow": slow,
            "process": proc,
            "sql": sql_rows,
            "long_holds": long_holds,
            "events": events,
            "file": ([minute_record, routes_record] if requests else [minute_record]) + holds,
        }


def write(c, taken):
    c.executemany("INSERT OR REPLACE INTO flight_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", taken["requests"])
    c.executemany(
        "INSERT INTO flight_slow(created_at,route,caller,actor,ms,bytes,status,sql_ms,wait_ms,queries,top) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        taken["slow"],
    )
    c.execute("INSERT OR REPLACE INTO flight_process VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", taken["process"])
    c.executemany("INSERT OR REPLACE INTO flight_sql VALUES(?,?,?,?,?,?,?)", taken["sql"])
    c.executemany("INSERT OR REPLACE INTO flight_web VALUES(?,?,?,?,?,?)", taken.get("web") or [])
    for hold in taken.get("long_holds") or []:
        _event(c, "long_hold", {k: v for k, v in hold.items() if k != "ts"}, hold["ts"])
    for ts, kind, detail in taken.get("events") or []:
        _event(c, kind, detail, ts)


def process_stats():
    from .flight_file import rss_now

    out = {"rss": rss_now(), "threads": threading.active_count(), "fds": None, "sockets": None}
    try:
        fds = os.listdir("/proc/self/fd")
        out["fds"] = len(fds)
        sockets = 0
        for fd in fds:
            try:
                sockets += os.readlink("/proc/self/fd/" + fd).startswith("socket:")
            except OSError:
                pass
        out["sockets"] = sockets
    except OSError:
        try:
            out["fds"] = len(os.listdir("/dev/fd"))  # macOS: open files only, no socket count
        except OSError:
            pass
    return out


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDLE = {
    ("threading.py", "wait"),
    ("queue.py", "get"),
    ("selectors.py", "select"),
    ("thread.py", "_worker"),
    ("threading.py", "_wait_for_tstate_lock"),
}


def _where(filename):
    if filename.startswith(ROOT):
        return os.path.relpath(filename, ROOT)
    parts = filename.replace("\\", "/").split("/")
    if "site-packages" in parts:
        return "/".join(parts[parts.index("site-packages") + 1 :])
    return "/".join(parts[-2:])


def stacks(loop_thread=None, depth=14, limit=24):
    """Every thread's innermost frames as `file:line function`: where each one is, never its values. Idle pool threads
    are counted, not listed."""
    names = {t.ident: t.name for t in threading.enumerate()}
    me = threading.get_ident()
    out, idle = [], 0
    for ident, frame in sys._current_frames().items():
        if ident == me:
            continue
        frames = traceback.extract_stack(frame)[-depth:]
        inner = frames[-1] if frames else None
        loop = ident == loop_thread
        if not loop and inner and (os.path.basename(inner.filename), inner.name) in IDLE:
            idle += 1
            continue
        out.append(
            {
                "thread": names.get(ident, str(ident)),
                "loop": loop,
                "stack": [f"{_where(f.filename)}:{f.lineno} {f.name}" for f in frames],
            }
        )
    out.sort(key=lambda t: not t["loop"])
    return {"busy": out[:limit], "idle": idle}


_clock = time.perf_counter


class TimedCursor(sqlite3.Cursor):
    """A statement's cursor that adds the time spent fetching its rows to the statement's one sample, recorded when the
    rows run out, at `fetchall`, or when the cursor is dropped. Iteration is timed row by row, never the caller's work
    between rows."""

    recorder = None
    _sql = None
    _ms = 0.0

    def _record(self):
        sql = self._sql
        if sql is not None:
            self._sql = None
            self.recorder.query(sql, self._ms)

    def fetchone(self):
        started = _clock()
        row = super().fetchone()
        self._ms += (_clock() - started) * 1000
        if row is None:
            self._record()
        return row

    def fetchall(self):
        started = _clock()
        rows = super().fetchall()
        self._ms += (_clock() - started) * 1000
        self._record()
        return rows

    def fetchmany(self, size=None):
        started = _clock()
        rows = super().fetchmany() if size is None else super().fetchmany(size)
        self._ms += (_clock() - started) * 1000
        if len(rows) < (self.arraysize if size is None else size):
            self._record()
        return rows

    def __iter__(self):
        return self._rows()

    def _rows(self):
        # Rows are stepped in small batches so the clock is read per batch, not per row.
        fetch = sqlite3.Cursor.fetchmany
        while True:
            started = _clock()
            rows = fetch(self, 32)
            self._ms += (_clock() - started) * 1000
            if not rows:
                self._record()
                return
            yield from rows

    def __del__(self):
        if self._sql is not None:
            self._record()


class TimedConnection(sqlite3.Connection):
    """A Store connection whose statements are timed, rows fetched included, for the flight recorder."""

    recorder = None
    cursor_class = TimedCursor

    def execute(self, sql, parameters=()):
        cursor = self.cursor(self.cursor_class)
        started = _clock()
        try:
            cursor.execute(sql, parameters)
        except sqlite3.OperationalError as exc:
            self.recorder.query(sql, (_clock() - started) * 1000, "locked" in str(exc))
            raise
        except Exception:
            self.recorder.query(sql, (_clock() - started) * 1000)
            raise
        cursor._ms = (_clock() - started) * 1000
        cursor._sql = sql
        return cursor

    def executemany(self, sql, parameters):
        started = _clock()
        try:
            return super().executemany(sql, parameters)
        finally:
            self.recorder.query(sql, (_clock() - started) * 1000)


def connection_class(recorder):
    cursor = type("RecordedCursor", (TimedCursor,), {"recorder": recorder})
    return type("RecordedConnection", (TimedConnection,), {"recorder": recorder, "cursor_class": cursor})


# ---------------------------------------------------------------------------- what this server is

SECRET = re.compile(r"secret|token|password|key|dsn|credential|arn|identities|email|url|issuer|audience|owner", re.I)


def config_fingerprint(settings):
    """Per non-secret setting a short hash (so two starts show which changed), and one hash of them all. Fields kept out
    of repr, and any whose name could hold a secret or an address, are left out entirely: no value is ever recorded."""
    import dataclasses

    fields = {}
    for f in dataclasses.fields(settings):
        if not f.repr or SECRET.search(f.name):
            continue
        fields[f.name] = hashlib.sha256(repr(getattr(settings, f.name, None)).encode()).hexdigest()[:10]
    whole = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]
    return whole, fields


def dependency_hash():
    try:
        from importlib import metadata

        pins = sorted(f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions() if d.metadata["Name"])
    except Exception:
        return ""
    return hashlib.sha256("\n".join(pins).encode()).hexdigest()[:16]


def code_state(root=ROOT):
    """`modified` when the code differs from what the image was built with (files copied over a release image), `match`
    when it is the same, None when the image recorded no hash (a source checkout, or an image from before the hash)."""
    try:
        with open(os.path.join(root, "release-manifest.json")) as manifest:
            expected = json.load(manifest).get("code")
    except (OSError, ValueError, AttributeError):
        return None
    if not expected:
        return None
    from .code_hash import digest

    return "match" if digest(root) == expected else "modified"


def provenance(settings, version, root=ROOT):
    """Whether this image runs the code it was built with, from the commit its release tag names on GitHub. `modified`
    is code changed after the build (files copied over a release image); `mismatch` an image built elsewhere and
    labelled as a release; `unverified` a release label with no commit to check."""
    from . import releases

    if code_state(root) == "modified":
        return "modified"
    if not releases.parse(version) or settings.rehearsal or not releases.Checker.enabled():
        return "unknown"
    if not settings.release_commit or not settings.release_repo:
        return "unverified"
    try:
        with releases._client(timeout=5) as http:
            reply = http.get(
                f"https://api.github.com/repos/{settings.release_repo}/commits/v{version}",
                headers={"Accept": "application/vnd.github.sha"},
            )
        if reply.status_code != 200:
            return "unknown"
        published = reply.text.strip()
    except Exception:
        return "unknown"
    return "match" if published == settings.release_commit else "mismatch"


def start_event(settings, check=True):
    from . import releases

    version = releases.version()
    whole, fields = config_fingerprint(settings)
    return {
        "version": version,
        "commit": settings.release_commit,
        "repository": settings.release_repo,
        "python": sys.version.split()[0],
        "sqlite": sqlite3.sqlite_version,
        "dependencies": dependency_hash(),
        "config": whole,
        "config_fields": fields,
        "provenance": provenance(settings, version) if check else "unknown",
    }


def _event(c, kind, detail, now=None):
    c.execute(
        "INSERT INTO flight_events(created_at,kind,detail) VALUES(?,?,?)",
        (int(now or time.time()), kind, json.dumps(detail, separators=(",", ":"))),
    )


def note(conn, kind, detail):
    """Record an event from inside a request, read or write, without writing there: the recorder keeps it and the
    minute's write puts it in flight_events. Nothing happens with the recorder off."""
    rec = getattr(conn, "recorder", None) or ACTIVE
    if rec is not None:
        rec.event(kind, detail)


def record_event(store, kind, detail, now=None):
    with store.transaction() as c:
        _event(c, kind, detail, now)


def last_start(c):
    row = c.execute(
        "SELECT created_at,detail FROM flight_events WHERE kind='start' ORDER BY created_at DESC,id DESC LIMIT 1"
    ).fetchone()
    return {"at": row[0], **json.loads(row[1])} if row else None


# ---------------------------------------------------------------------------- the database itself


def _rows(c, table):
    """About how many rows `table` holds, from its rowid span: exact for a log pruned from its oldest end, a little
    high for one deleted from the middle. count(*) read every page of a table of millions of rows each hour, for
    minutes, holding a read snapshot that kept the WAL from resetting. A table without rowids is counted."""
    try:
        # One aggregate per SELECT: SQLite answers min() or max() from the b-tree's end only when it is alone.
        low, high = c.execute(f"SELECT (SELECT min(rowid) FROM {table}), (SELECT max(rowid) FROM {table})").fetchone()
    except sqlite3.OperationalError:
        return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return 0 if high is None else high - low + 1


def db_stats(store, sizes=True):
    """The database's size and the detail `size_db` keeps. Every read is its own short snapshot, so none holds a read
    open for minutes, which stopped WAL checkpoints. Object sizes are estimated from sampled rows (`_object_sizes`); no
    page is walked, where dbstat walked every page of the file once a day."""
    path = str(store.settings.db_path)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    wal = os.path.getsize(path + "-wal") if os.path.exists(path + "-wal") else 0
    detail = {"objects": None, "rows": {}, "oldest": {}}
    with store.read() as c:
        page_size = c.execute("PRAGMA page_size").fetchone()[0]
        free = c.execute("PRAGMA freelist_count").fetchone()[0] * page_size
        objects = c.execute("SELECT name, type, tbl_name FROM sqlite_master WHERE type IN ('table','index')").fetchall()
    present = {name for name, kind, _ in objects if kind == "table"}
    if sizes:
        detail["objects"] = _object_sizes(store, page_size, objects)
    for table, column in BIG.items():
        if table not in present:
            continue
        with store.read() as c:
            detail["rows"][table] = _rows(c, table)
            if column:
                row = c.execute(f"SELECT {column} FROM {table} ORDER BY rowid LIMIT 1").fetchone()
                detail["oldest"][table] = row[0] if row else None
    return size, wal, free, detail


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _sample(c, table):
    """`(rows, {column: average bytes})` for `table`: `rows` as `_rows` counts them, and the averages from SAMPLE_WINDOWS
    runs of SAMPLE_ROWS rows spread over its rowid range. Only the values' lengths are read, never sent anywhere."""
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({_quote(table)})")]
    rows = _rows(c, table)
    q = _quote(table)
    exprs = ", ".join(f"coalesce(length(CAST({_quote(col)} AS BLOB)), 0)" for col in cols)
    sample = []
    if rows and cols:
        try:
            low, high = c.execute(f"SELECT (SELECT min(rowid) FROM {q}), (SELECT max(rowid) FROM {q})").fetchone()
            for i in range(SAMPLE_WINDOWS):
                start = low + (high - low) * i // SAMPLE_WINDOWS
                sample += c.execute(
                    f"SELECT {exprs} FROM {q} WHERE rowid >= ? ORDER BY rowid LIMIT ?", (start, SAMPLE_ROWS)
                ).fetchall()
        except sqlite3.OperationalError:  # a table without rowids: its first rows
            sample = c.execute(f"SELECT {exprs} FROM {q} LIMIT ?", (SAMPLE_ROWS * SAMPLE_WINDOWS,)).fetchall()
    averages = {col: sum(r[i] for r in sample) / len(sample) if sample else 0.0 for i, col in enumerate(cols)}
    return rows, averages


def _object_sizes(store, page_size, objects):
    """Estimated bytes of the 40 largest tables and indexes, `{name: bytes}`. A table is its rows times its record size
    (the column averages from `_sample`, plus a few header bytes per row). An index is its table's rows times its
    columns' averages and a rowid, 8 bytes for an expression. Partial indexes count every row, so they read high.
    Every object is at least one page, as a b-tree always is. Each table and its indexes are one short read."""
    sizes = {}
    tables = [("sqlite_schema", "table", "sqlite_schema")] + [obj for obj in objects if obj[1] == "table"]
    for name, _, _ in tables:
        with store.read() as c:
            rows, averages = _sample(c, name)
            sizes[name] = rows * (len(averages) + 3 + sum(averages.values()))
            for index, kind, table in objects:
                if kind == "index" and table == name:
                    columns = [r[2] for r in c.execute(f"PRAGMA index_info({_quote(index)})")]
                    sizes[index] = rows * (5 + sum(averages.get(col, 8) for col in columns))
    sizes = {name: max(page_size, round(size)) for name, size in sizes.items()}
    return dict(sorted(sizes.items(), key=lambda kv: -kv[1])[:40])


# ---------------------------------------------------------------------------- retention


def sweep(store, now=None):
    """Request minutes older than two days become hours; old rows go. Each step is its own short transaction."""
    now = int(now or time.time())
    cutoff = now - MINUTE_DAYS * 86400
    while True:
        with store.transaction() as c:
            row = c.execute(
                "SELECT min(created_at) FROM flight_requests WHERE span=60 AND created_at<?", (cutoff,)
            ).fetchone()
            if row[0] is None:
                break
            hour = row[0] // 3600 * 3600
            end = min(hour + 3600, cutoff)
            merged = {}
            for r in c.execute(
                "SELECT route,caller,n,errors,bytes,total_ms,max_ms,hist FROM flight_requests "
                "WHERE span=60 AND created_at>=? AND created_at<?",
                (hour, end),
            ):
                m = merged.setdefault((r[0], r[1]), [0, 0, 0, 0.0, 0.0, {}])
                m[0] += r[2]
                m[1] += r[3]
                m[2] += r[4]
                m[3] += r[5]
                m[4] = max(m[4], r[6] or 0)
                _merge(m[5], json.loads(r[7] or "{}"))
            for (route, caller), m in merged.items():
                old = c.execute(
                    "SELECT n,errors,bytes,total_ms,max_ms,hist FROM flight_requests WHERE created_at=? AND span=3600 "
                    "AND route=? AND caller=?",
                    (hour, route, caller),
                ).fetchone()
                if old:  # the hour's earlier part, rolled up by an earlier sweep
                    m[0] += old[0]
                    m[1] += old[1]
                    m[2] += old[2]
                    m[3] += old[3]
                    m[4] = max(m[4], old[4] or 0)
                    _merge(m[5], json.loads(old[5] or "{}"))
                c.execute(
                    "INSERT OR REPLACE INTO flight_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        hour,
                        3600,
                        route,
                        caller,
                        m[0],
                        m[1],
                        m[2],
                        round(m[3], 1),
                        quantile(m[5], 0.5),
                        quantile(m[5], 0.95),
                        m[4],
                        json.dumps(m[5], separators=(",", ":")),
                    ),
                )
            c.execute("DELETE FROM flight_requests WHERE span=60 AND created_at>=? AND created_at<?", (hour, end))
    _sweep_web(store, cutoff)
    short, long = now - SHORT_DAYS * 86400, now - LONG_DAYS * 86400
    with store.transaction() as c:
        c.execute("DELETE FROM flight_requests WHERE created_at<?", (short,))
        c.execute("DELETE FROM flight_web WHERE created_at<?", (short,))
        c.execute(
            "DELETE FROM flight_slow WHERE created_at<? OR id<=(SELECT max(id) FROM flight_slow)-?", (short, SLOW_KEEP)
        )
        c.execute("DELETE FROM flight_process WHERE created_at<?", (short,))
        c.execute("DELETE FROM flight_sql WHERE created_at<?", (long,))
        c.execute("DELETE FROM flight_db WHERE created_at<?", (long,))
        c.execute("DELETE FROM flight_events WHERE created_at<? AND kind<>'start'", (long,))
        c.execute(
            "DELETE FROM flight_events WHERE kind='start' AND id NOT IN "
            "(SELECT id FROM flight_events WHERE kind='start' ORDER BY id DESC LIMIT 500)"
        )


def _sweep_web(store, cutoff):
    """Browser timing minutes older than `cutoff` become hours, an hour per transaction, as request minutes do."""
    while True:
        with store.transaction() as c:
            row = c.execute(
                "SELECT min(created_at) FROM flight_web WHERE span=60 AND created_at<?", (cutoff,)
            ).fetchone()
            if row[0] is None:
                return
            hour = row[0] // 3600 * 3600
            end = min(hour + 3600, cutoff)
            merged = {}
            for r in c.execute(
                "SELECT metric,route,n,hist FROM flight_web WHERE (span=60 AND created_at>=? AND created_at<?) "
                "OR (span=3600 AND created_at=?)",
                (hour, end, hour),
            ):
                m = merged.setdefault((r[0], r[1]), [0, {}])
                m[0] += r[2]
                _merge(m[1], json.loads(r[3] or "{}"))
            c.executemany(
                "INSERT OR REPLACE INTO flight_web VALUES(?,?,?,?,?,?)",
                [
                    (hour, 3600, metric, route, n, json.dumps(hist, separators=(",", ":")))
                    for (metric, route), (n, hist) in merged.items()
                ],
            )
            c.execute("DELETE FROM flight_web WHERE span=60 AND created_at>=? AND created_at<?", (hour, end))


# ---------------------------------------------------------------------------- the background thread


class Flight(threading.Thread):
    """Drains the samples, watches for stalls, writes once a minute and sizes the database each hour. Everything goes to
    the flight file first (backend/flight_file.py, its own thread, never waiting); the tables after, each write waiting
    at most DB_WAIT for the write lock, so a held lock costs the Health page a minute, not the recorder its watch."""

    def __init__(
        self, recorder, store, settings, folder=None, stall_s=STALL_S, stack_gap=STACK_GAP, tick=TICK, db_wait=DB_WAIT
    ):
        super().__init__(name="tico-flight", daemon=True)
        from . import flight_file

        self.recorder, self.store, self.settings = recorder, store, settings
        self.halt = threading.Event()
        self.db_every = 3600
        self.db_next = time.time() + 120  # not on the busy first minutes after a start
        self.folder = folder if folder is not None else flight_file.directory(settings.db_path)
        self.file = flight_file.Writer(self.folder)
        self.usage = flight_file.Usage()
        self.stall_s, self.stack_gap, self.tick_s, self.db_wait = stall_s, stack_gap, tick, db_wait
        self.open_stall = None
        self.last_stacks = float("-inf")
        self.stalls = 0
        self.db_failed = 0
        self.pending = []  # table writes the lock kept out, oldest first: (minute or None, work)
        self.lost = []  # minutes whose rows went before a write got through, for the next `recorder_gap`

    def run(self):
        from . import releases

        self.file.start()
        self.recorder.start_drain(self.tick_s)
        self.file.put(
            {
                "kind": "start",
                "pid": os.getpid(),
                "version": releases.version(),
                "commit": self.settings.release_commit,
                "python": sys.version.split()[0],
                "sqlite": sqlite3.sqlite_version,
                "platform": sys.platform,
            }
        )
        self.record_start()
        minute = int(time.time() // 60 * 60)
        while not self.halt.wait(self.tick_s):
            try:
                self.step()
                now = time.time()
                if now >= minute + 60:
                    self.flush(minute)
                    minute = int(now // 60 * 60)
                if now >= self.db_next:
                    self.size_db(now)
            except Exception as exc:
                LOG.warning("Flight recorder pass failed: %s", type(exc).__name__)
        self.recorder.stop_drain()
        try:
            self.recorder.drain()
            self.flush(minute)
        except Exception:
            pass
        self.file.close()

    def record_start(self):
        try:
            event = start_event(self.settings)
            at = _now()  # a write the lock defers keeps the time it happened
            self.write_db(lambda c: _event(c, "start", event, at))
        except Exception as exc:
            LOG.warning("Flight recorder start event failed: %s", type(exc).__name__)

    def step(self):
        self.usage.sample(self.recorder.wait_total)
        for record in self.recorder.pop_slow():
            self.file.put(record)
        return self.watch()

    def watch(self, now=None):
        """A stall is the event loop not waking for `stall_s` past its half-second sleep. Each one is a `stall` record
        (with every thread's stack at most every `stack_gap` seconds) and, once the loop wakes, a `stall_end` with the
        whole window's class. Returns the `stall` record when one starts."""
        from .flight_file import classify

        rec = self.recorder
        beat = rec.beat
        now = time.monotonic() if now is None else now
        if self.open_stall is not None and beat != self.open_stall["beat"]:
            opened, self.open_stall = self.open_stall, None
            if beat is not None:
                usage = self.usage.window(opened["beat"], list(rec.waiting.values()), now=beat)
                if usage:
                    self.file.put(
                        {
                            "kind": "stall_end",
                            "id": opened["id"],
                            "seconds": round(beat - opened["beat"], 1),
                            "class": classify(usage),
                            "usage": usage,
                        }
                    )
        if beat is None or self.open_stall is not None or now - beat < self.stall_s + 0.5:
            return None
        self.stalls += 1
        usage = self.usage.window(beat, list(rec.waiting.values()), now) or {}
        record = {
            "kind": "stall",
            "id": f"{os.getpid()}-{self.stalls}",
            "seconds": round(now - beat, 1),
            "class": classify(usage) if usage else "unknown",
            "usage": usage,
        }
        if now - self.last_stacks >= self.stack_gap:
            self.last_stacks = now
            record["threads"] = stacks(rec.loop_thread)
        self.file.put(record)
        self.open_stall = {"beat": beat, "id": record["id"]}
        loop = next((t for t in (record.get("threads") or {}).get("busy", []) if t["loop"]), None)
        LOG.warning(
            "Event loop stalled %.1fs (%s); loop thread at: %s",
            record["seconds"],
            record["class"],
            " < ".join(reversed(loop["stack"][-6:])) if loop else "unknown",
        )
        if "threads" in record:
            detail = {
                "stalled_s": record["seconds"],
                "class": record["class"],
                "usage": usage,
                "threads": record["threads"],
            }
            at = _now()
            self.write_db(lambda c: _event(c, "stall", detail, at))
        return record

    def write_db(self, work, minute=None):
        """One short write transaction on a connection of the recorder's own, waiting at most `db_wait` for the lock,
        that also writes whatever an earlier busy lock kept out, oldest first. False (and counted in the next minute's
        `db_failed`) when it could not: the work waits for the next try, up to PENDING_MAX of them, and a minute that
        goes unwritten is named in a `recorder_gap` event, so the tables never lose a minute silently."""
        self.pending.append((minute, work))
        try:
            c = hubdb.leave_wal_for_litestream(
                sqlite3.connect(str(self.settings.db_path), timeout=self.db_wait, isolation_level=None)
            )
        except sqlite3.Error:
            self.db_failed += 1
            self._trim()
            return False
        failing = None
        try:
            c.execute("BEGIN IMMEDIATE")
            try:
                for failing, (_, queued) in enumerate(self.pending):
                    queued(c)
                failing = None
                if self.lost:
                    _event(c, "recorder_gap", {"minutes": self.lost[-PENDING_MAX:], "count": len(self.lost)})
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            self.pending, self.lost = [], []
            return True
        except Exception as exc:
            self.db_failed += 1
            busy = isinstance(exc, sqlite3.OperationalError) and any(w in str(exc) for w in ("locked", "busy"))
            if not busy and failing is not None:
                # Not the lock: that work would fail every time, so it goes now (named) rather than block the rest.
                gone, _ = self.pending.pop(failing)
                if gone is not None:
                    self.lost.append(gone)
            self._trim()
            LOG.warning("Flight recorder table write deferred (%d waiting): %s", len(self.pending), type(exc).__name__)
            return False
        finally:
            c.close()

    def _trim(self):
        while len(self.pending) > PENDING_MAX:
            gone, _ = self.pending.pop(0)
            if gone is not None:
                self.lost.append(gone)

    def flush(self, minute):
        taken = self.recorder.take(minute)
        for record in taken["file"]:
            if record["kind"] == "minute":
                record.update(file_dropped=self.file.dropped, db_failed=self.db_failed, db_pending=len(self.pending))
            self.file.put(record)
        for hold in taken["long_holds"]:
            LOG.warning(
                "Write lock held %.1fs%s by %s at %s",
                hold["hold_s"],
                " (still held)" if hold.get("open") else "",
                hold["route"] or "a background job",
                hold["site"] or "an unknown site",
            )
        self.write_db(lambda c: write(c, taken), minute)

    def size_db(self, now):
        started = time.monotonic()
        self.db_next = now + self.db_every
        with self.store.read() as c:
            latest = c.execute("SELECT created_at,detail FROM flight_db ORDER BY created_at DESC LIMIT 1").fetchone()
        previous = json.loads(latest["detail"] or "{}") if latest else {}
        # The original scan timestamp travels with each hourly sample, including across recorder restarts.
        objects_at = previous.get("objects_at", latest["created_at"] if latest else None)
        scan = objects_at is None or now - objects_at >= SIZES_EVERY
        size, wal, free, detail = db_stats(self.store, sizes=scan)
        detail["objects_at"] = int(now) if scan else objects_at
        if not scan:
            detail["objects"] = previous.get("objects")
        took = time.monotonic() - started
        detail["seconds"] = round(took, 2)
        row = (int(now // 3600 * 3600), size, wal, free, json.dumps(detail, separators=(",", ":")))
        self.write_db(lambda c: c.execute("INSERT OR REPLACE INTO flight_db VALUES(?,?,?,?,?)", row))

    def stop(self):
        self.halt.set()
        self.join(timeout=5)


# ---------------------------------------------------------------------------- reading it back

SECTIONS = ("requests", "slow", "process", "sql", "db", "events", "web")
CALLERS = ("human", "bot", "runner", "service", "anonymous")


def _requests(c, since, until=None, top=25, caller=None):
    """Per route: n, errors, bytes, total time and p50/p95/p99 over the window. With `caller` (a CALLERS kind), only
    that kind's calls count, and the routes are ranked by its requests rather than by total time, with the browsers' timing sends apart (`web_timings`)."""
    merged = {}
    for r in c.execute(
        "SELECT route,caller,n,errors,bytes,total_ms,max_ms,hist FROM flight_requests WHERE created_at>=? AND created_at<?"
        + (" AND caller=?" if caller else ""),
        (since, until if until is not None else 2**62, *((caller,) if caller else ())),
    ):
        m = merged.setdefault(
            r[0],
            {"route": r[0], "n": 0, "errors": 0, "bytes": 0, "total_ms": 0.0, "max_ms": 0.0, "callers": {}, "_h": {}},
        )
        m["n"] += r[2]
        m["errors"] += r[3]
        m["bytes"] += r[4]
        m["total_ms"] += r[5]
        m["max_ms"] = max(m["max_ms"], r[6] or 0)
        m["callers"][r[1]] = m["callers"].get(r[1], 0) + r[2]
        _merge(m["_h"], json.loads(r[7] or "{}"))
    every, rows = {}, []
    for m in merged.values():
        hist = m.pop("_h")
        _merge(every, hist)
        rows.append(
            {
                **m,
                "total_ms": round(m["total_ms"], 1),
                "p50": quantile(hist, 0.5),
                "p95": quantile(hist, 0.95),
                "p99": quantile(hist, 0.99),
            }
        )
    rows.sort(key=(lambda r: (-r["n"], -r["total_ms"])) if caller else (lambda r: -r["total_ms"]))
    # Ranked by one kind of caller, the browsers' own timing sends (one per screen visit) would take a place among the
    # routes people wait on; they are counted in the totals and listed apart.
    beacon = [r for r in rows if r["route"] == WEB_TIMINGS_ROUTE] if caller else []
    ranked = [r for r in rows if r["route"] != WEB_TIMINGS_ROUTE] if caller else rows
    return {
        **({"caller": caller} if caller else {}),
        "n": sum(r["n"] for r in rows),
        "errors": sum(r["errors"] for r in rows),
        "p50": quantile(every, 0.5),
        "p95": quantile(every, 0.95),
        "p99": quantile(every, 0.99),
        "routes": ranked[:top],
        **({"web_timings": beacon[0]} if caller and beacon else {}),
    }


def _web(c, since, until=None):
    """People's browser timings over the window: per metric and screen n, p50, p75, p95, p99 and max (each a bucket's
    upper edge, as for requests), busiest first; and the readings dropped over a person's per-minute allowance."""
    merged, dropped = {}, 0
    for metric, route, n, hist in c.execute(
        "SELECT metric,route,n,hist FROM flight_web WHERE created_at>=? AND created_at<?",
        (since, until if until is not None else 2**62),
    ):
        if metric == "dropped":
            dropped += n
            continue
        m = merged.setdefault((metric, route), [0, {}])
        m[0] += n
        _merge(m[1], json.loads(hist or "{}"))
    rows = [
        {
            "metric": metric,
            "route": route,
            "n": n,
            **{f"p{int(q * 100)}": quantile(hist, q) for q in (0.5, 0.75, 0.95, 0.99)},
            "max_ms": round(EDGES[min(max(hist), len(EDGES) - 1)], 2) if hist else 0,
        }
        for (metric, route), (n, hist) in merged.items()
    ]
    rows.sort(key=lambda r: (WEB_METRICS.index(r["metric"]) if r["metric"] in WEB_METRICS else 9, -r["n"], r["route"]))
    return {"timings": rows, "dropped": dropped}


def _sql(c, since):
    merged = {}
    for r in c.execute(
        "SELECT fingerprint,n,total_ms,max_ms,hist FROM flight_sql WHERE created_at>=?", (since // 3600 * 3600,)
    ):
        m = merged.setdefault(r[0], {"sql": r[0], "n": 0, "total_ms": 0.0, "max_ms": 0.0, "_h": {}})
        m["n"] += r[1]
        m["total_ms"] += r[2]
        m["max_ms"] = max(m["max_ms"], r[3] or 0)
        _merge(m["_h"], json.loads(r[4] or "{}"))
    rows = [
        {
            **{k: v for k, v in m.items() if k != "_h"},
            "total_ms": round(m["total_ms"], 1),
            "p95": quantile(m["_h"], 0.95),
        }
        for m in merged.values()
    ]
    rows.sort(key=lambda r: -r["total_ms"])
    return {"queries": rows[:25]}


def _process(c, since, until=None):
    rows = [
        dict(r)
        for r in c.execute(
            "SELECT * FROM flight_process WHERE created_at>=? AND created_at<? ORDER BY created_at",
            (since, until if until is not None else 2**62),
        )
    ]

    def peak(key):
        values = [r[key] for r in rows if r[key] is not None]
        return max(values) if values else None

    cpu = [r["cpu"] for r in rows if r["cpu"] is not None]
    return {
        "cpu_avg": round(sum(cpu) / len(cpu), 1) if cpu else None,
        "cpu_max": peak("cpu"),
        "rss": rows[-1]["rss"] if rows else None,
        "threads": rows[-1]["threads"] if rows else None,
        "sockets": rows[-1]["sockets"] if rows else None,
        "lag_max": peak("lag_max"),
        "lag_p95": peak("lag_p95"),
        "wait_max": peak("wait_max"),
        "hold_max": peak("hold_max"),
        "hold_p95": peak("hold_p95"),
        "locked": sum(r["locked"] or 0 for r in rows),
        "dropped": sum(r["dropped"] or 0 for r in rows),
        "minutes": rows[-180:],
    }


def _db(c, now):
    latest = c.execute("SELECT * FROM flight_db ORDER BY created_at DESC LIMIT 1").fetchone()
    if not latest:
        return {}
    day = c.execute(
        "SELECT * FROM flight_db WHERE created_at<=? ORDER BY created_at DESC LIMIT 1",
        (latest["created_at"] - 86400 + 1800,),
    ).fetchone()
    detail = json.loads(latest["detail"] or "{}")
    before = json.loads(day["detail"] or "{}") if day else {}
    return {
        "at": latest["created_at"],
        "bytes": latest["bytes"],
        "wal_bytes": latest["wal_bytes"],
        "free_bytes": latest["free_bytes"],
        "growth_24h": latest["bytes"] - day["bytes"] if day else None,
        "objects": detail.get("objects"),
        "objects_at": detail.get("objects_at", latest["created_at"]),
        "rows": detail.get("rows"),
        "oldest": detail.get("oldest"),
        "rows_24h": {k: v - (before.get("rows") or {}).get(k, v) for k, v in (detail.get("rows") or {}).items()}
        if day
        else None,
    }


# GET /api/v2/system/update/speed: each route's requests and p95 since this server took its release, its first
# SPEED_WARMUP seconds (cold caches, every open page reloading) left out, against the last hour it ran the `base` release
# (default the one before). Route templates and timings only, never who made a request. The release train compares
# the two (scripts/release_train.py `speed_check`).
SPEED_PATH = "/api/v2/system/update/speed"
SPEED_BEFORE = 3600
SPEED_WARMUP = 120
SPEED_ROUTES = 10_000  # every route: a cut per window would drop a route from one side of the comparison


def speed(c, since, end, now, warmup=SPEED_WARMUP):
    """[{route, before: {n, p95}, after: {n, p95}}]: the hour before `end` (only minutes wholly before it) against
    `since` + `warmup` to `now` (epoch seconds), busiest after first."""
    windows = {
        "before": _requests(c, end - SPEED_BEFORE, end - 59, SPEED_ROUTES)["routes"],
        "after": _requests(c, since + warmup, now + 60, SPEED_ROUTES)["routes"],
    }
    out = {}
    for name, rows in windows.items():
        for r in rows:
            out.setdefault(r["route"], {"route": r["route"], "before": {"n": 0, "p95": 0}, "after": {"n": 0, "p95": 0}})
            out[r["route"]][name] = {"n": r["n"], "p95": r["p95"]}
    return sorted(out.values(), key=lambda r: (-r["after"]["n"], r["route"]))


def speed_windows(history, base=None):
    """(since, end, base) from the release history ({version, since} each, oldest first): when the current release
    started, and when the server last stopped running `base` (default the release before), or None when it never ran
    it here."""
    stamps = [hubdb.parse_ts(h.get("since")) for h in history]
    if not history or stamps[-1] is None:
        return None
    since = int(stamps[-1].timestamp())
    for i in range(len(history) - 2, -1, -1):
        ended = stamps[i + 1]
        if (base is None or history[i].get("version") == base) and ended is not None:
            return since, int(ended.timestamp()), history[i].get("version")
    return None


def view(c, minutes=60, sections=SECTIONS, now=None, since=None, until=None, routes=25, caller=None):
    """The last `minutes`, or with `since` the minutes from `since` up to `until` (epoch seconds, `until` excluded): a
    load run reads exactly its steady window this way. Requests and process minutes keep to the window; slow requests,
    SQL hours and events start at `since`."""
    now = int(now or time.time())
    if since is not None:  # the window's own length, in whole minutes
        minutes = max(0, -(-(int(until if until is not None else now) - int(since)) // 60))
    since = int(since) if since is not None else now - minutes * 60
    out: dict = {"since": since, "now": now, "minutes": minutes}
    if until is not None:
        out["until"] = int(until)
    if "requests" in sections:
        out["requests"] = _requests(c, since, until, routes, caller)
    if "web" in sections:
        out["web"] = _web(c, since, until)
    if "slow" in sections:
        out["slow"] = [
            {**{k: r[k] for k in r.keys() if k != "top"}, **({"top": json.loads(r["top"])} if r["top"] else {})}
            for r in c.execute(
                "SELECT created_at,route,caller,actor,ms,bytes,status,sql_ms,wait_ms,queries,top FROM flight_slow "
                "WHERE created_at>=? ORDER BY id DESC LIMIT 50",
                (since,),
            )
        ]
    if "process" in sections:
        out["process"] = _process(c, since, until)
    if "sql" in sections:
        out["sql"] = _sql(c, since)
    if "db" in sections:
        out["db"] = _db(c, now)
    if "events" in sections:
        out["events"] = {
            "start": last_start(c),
            "recent": [
                {"id": r[0], "at": r[1], "kind": r[2], **json.loads(r[3])}
                for r in c.execute(
                    "SELECT id,created_at,kind,detail FROM flight_events WHERE created_at>=? ORDER BY id DESC LIMIT 20",
                    (since,),
                )
            ],
        }
    return out


def install(app, store, auth):
    from fastapi import Request

    from .store import Problem

    @app.get("/api/v2/system/metrics")
    def system_metrics(
        request: Request,
        minutes: int = 60,
        section: str = "all",
        since: int | None = None,
        until: int | None = None,
        routes: int = 25,
        caller: str | None = None,
    ):
        """The flight recorder (backend/flight.py): owners, admins and the team's bots. `since` and `until` (epoch
        seconds) read one window instead of the last `minutes`, and `routes` how many routes to list (1 to 500): the
        load gate (bench/load_gate) reads its steady window this way. `caller` (human, bot, runner, service or
        anonymous) keeps the requests to that kind of caller, ranked by its requests."""
        who = request.state.identity
        auth.domain(who)
        if not auth.bot_admin(who) and who.role != "bot":
            raise Problem("forbidden", "Only an owner, an admin or a bot reads the server's metrics", 403)
        wanted = SECTIONS if section == "all" else tuple(s for s in section.split(",") if s in SECTIONS)
        if not wanted:
            raise Problem("section", "section is all or a list of: " + ", ".join(SECTIONS), 422)
        if since is not None and until is not None and until <= since:
            raise Problem("window", "until must be after since", 422)
        if caller is not None and caller not in CALLERS:
            raise Problem("caller", "caller is one of: " + ", ".join(CALLERS), 422)
        with store.read() as c:
            out = view(
                c,
                max(1, min(int(minutes), SHORT_DAYS * 1440)),
                wanted,
                since=since,
                until=until,
                routes=max(1, min(int(routes), 500)),
                caller=caller or None,
            )
        if who.role == "bot":
            # A slow request's actor names the person or bot who made it; a bot doing performance work needs the
            # route and timing, not who was waiting.
            for row in out.get("slow") or []:
                row.pop("actor", None)
        return out

    @app.get(SPEED_PATH)
    def update_speed(request: Request, base: str | None = None):
        """How this server's routes answer since it took its release, against the last hour it ran `base` (`speed`):
        an `update` service key (the release train, docs/service-keys.md), the owner and the admins."""
        from . import releases

        who = request.state.identity
        if not (who.role == "service" and who.scope == "update"):
            auth.domain(who)
            if not auth.bot_admin(who) or who.role not in ("owner", "human"):
                raise Problem("forbidden", "Only an owner, an admin or an update key reads the release's speed", 403)
        now = int(time.time())
        with store.read() as c:
            found = speed_windows(releases.history(c), base.strip().removeprefix("v") if base else None)
            if found is None:
                return {"release": releases.version(), "since": None, "now": now, "base": None, "routes": []}
            since, end, ran = found
            return {
                "release": releases.version(),
                "since": since,
                "now": now,
                "base": ran,
                "routes": speed(c, since, end, now),
            }

    # On app.state so a test can hold the minute still (backend/tests/test_web_timings.py).
    allowance = app.state.web_allowance = WebAllowance()

    @app.post("/api/v2/web-timings", status_code=202)
    async def web_timings(request: Request):
        """People's browsers send their own speed here (ui/observability.ts, `vitals`): {"timings": [{metric, route,
        ms}]}, at most WEB_BATCH. Only people: a bot's or runner's token is refused. A metric other than WEB_METRICS, a
        screen outside WEB_ROUTES or a value outside 0 to WEB_MAX_MS refuses the whole request, and nothing is kept.
        Each reading goes to the recorder's memory (no write on this path) as its bucket; nothing else of the request
        is kept. Over WEB_PER_MINUTE readings a minute from one person, the rest are dropped and counted."""
        who = request.state.identity
        auth.domain(who)
        if caller_kind(who) != "human":
            raise Problem("forbidden", "Only a person's browser sends its speed", 403)
        try:
            body = await request.json()
        except ValueError:
            raise Problem("timings", "The body is JSON: {\"timings\": [{metric, route, ms}]}", 422) from None
        readings = body.get("timings") if isinstance(body, dict) else None
        if not isinstance(readings, list) or not readings or len(readings) > WEB_BATCH:
            raise Problem("timings", f"timings is a list of 1 to {WEB_BATCH} readings", 422)
        clean = []
        for item in readings:
            ms = item.get("ms") if isinstance(item, dict) else None
            if (
                not isinstance(item, dict)
                or item.get("metric") not in WEB_METRICS
                or item.get("route") not in WEB_ROUTES
                or isinstance(ms, bool)
                or not isinstance(ms, (int, float))
                or not 0 <= ms <= WEB_MAX_MS
            ):
                raise Problem(
                    "timing",
                    "Each reading is {metric: lcp or inp, route: a screen template, ms: 0 to 60000}",
                    422,
                )
            clean.append((item["metric"], item["route"], float(ms)))
        kept = allowance.take(getattr(who, "actor", "") or "", len(clean))
        recorder = getattr(store, "recorder", None)
        if recorder is not None:
            for metric, route, ms in clean[:kept]:
                recorder.web_timing(metric, route, ms)
            if kept < len(clean):
                recorder.web_drop(len(clean) - kept)
        return {"kept": kept if recorder is not None else 0, "dropped": len(clean) - kept}


class WebAllowance:
    """How many browser timings each person may still send this minute (WEB_PER_MINUTE), in memory only."""

    def __init__(self, per_minute=WEB_PER_MINUTE, clock=time.time):
        self.per_minute, self.clock = per_minute, clock
        self.lock = threading.Lock()
        self.minute, self.used = None, {}

    def take(self, who, n):
        """How many of `n` readings `who` may send now; those are counted against this minute."""
        with self.lock:
            minute = int(self.clock() // 60)
            if minute != self.minute:
                self.minute, self.used = minute, {}
            used = self.used.get(who, 0)
            kept = max(0, min(n, self.per_minute - used))
            self.used[who] = used + kept
            return kept


# ---------------------------------------------------------------------------- reading the file, server or no server


def _since(text):
    """`30m`, `2h`, `1d`, `90s`, or a number of minutes, in seconds."""
    text = str(text).strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text and text[-1] in units:
        return float(text[:-1]) * units[text[-1]]
    return float(text) * 60


def _folder(given):
    from pathlib import Path

    from .flight_file import directory

    if given:
        return Path(given)
    if os.environ.get("TICO_DB"):
        return directory(os.environ["TICO_DB"])
    # `docker compose exec` does not see the entrypoint's TICO_DB; the data volume is /data there.
    data = Path(os.environ.get("TICO_DATA_DIR") or "/data")
    return data / "flight"


def _signal(folder, signum):
    """Send `signum` to the server whose pid the recorder wrote, after checking the pid is a Tico server."""
    import subprocess

    try:
        pid = int((folder / "server.pid").read_text().strip())
    except (OSError, ValueError):
        raise SystemExit(f"no server.pid in {folder}: is the server running with the flight recorder on?")
    try:
        command = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        command = ""
    if "backend" not in command and "uvicorn" not in command:
        raise SystemExit(f"pid {pid} from {folder}/server.pid is not a running Tico server")
    os.kill(pid, signum)
    return pid


def main(argv=None):
    import argparse
    import signal

    from . import flight_file

    parser = argparse.ArgumentParser(
        prog="python -m backend.flight",
        description="Read the flight recorder's local file (works with the server stopped "
        "or stuck), or ask the server for its stacks or memory.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    summary = sub.add_parser("summary", help="slow routes, stalls with their class and stacks, per-minute numbers")
    summary.add_argument("--since", default="30m", help="how far back: 30m, 2h, 1d (default 30m)")
    summary.add_argument("--json", action="store_true", help="the summary as JSON")
    stacks_cmd = sub.add_parser("stacks", help="send SIGUSR1: the server writes every thread's stack, printed here")
    memory = sub.add_parser("memory", help="send SIGUSR2: start tracemalloc, or (when on) write the top 50 sites")
    for p in (summary, stacks_cmd, memory):
        p.add_argument("--dir", help="the flight folder (default: beside $TICO_DB, else /data/flight)")
    args = parser.parse_args(argv)
    folder = _folder(args.dir)
    if args.command == "summary":
        if not folder.exists():
            print(f"no flight folder at {folder} (pass --dir)", file=sys.stderr)
            return 1
        out = flight_file.summary(folder, _since(args.since))
        print(json.dumps(out, indent=1) if args.json else flight_file.render(out))
        return 0
    if args.command == "stacks":
        path = folder / flight_file.STACKS
        with open(path, "a") as f:
            f.write(time.strftime("--- %Y-%m-%dT%H:%M:%SZ, python -m backend.flight stacks\n", time.gmtime()))
        before = path.stat().st_size
        _signal(folder, signal.SIGUSR1)
        for _ in range(30):
            time.sleep(0.1)
            if path.exists() and path.stat().st_size > before:
                break
        time.sleep(0.2)
        with open(path, "rb") as f:
            f.seek(before)
            print(f.read().decode("utf-8", "replace"))
        return 0
    pid = _signal(folder, signal.SIGUSR2)
    print(
        f"sent SIGUSR2 to {pid}: the first starts tracemalloc, the next writes {folder}/memory-<time>.txt and stops it"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
