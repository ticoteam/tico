"""The statements the busiest reads and the computers' polls take on a company the size of the hosted TIDY one
(backend/tests/helpers/tidy_scale.py: 18.5k tasks, 19k rooms, 40k messages, 30k followers, 170 bots on 10 computers).

On Nov 19 TIDY's pages ran 200 to 700 statements each, one or a few per row, fine on a test's few rows and 1 to 50 s
there. Each route here has a budget: the statements it took when this test was written plus about a fifth. A change that
adds a read per row goes over it on this company, whatever the timing on the machine running the test. A statement count
does not depend on how fast the machine is, so no time is asserted: the median of a few calls per route is reported at the end of the run
(conftest.py prints each test's `summary` property) with the routes over the 1 s target marked."""

import collections
import contextlib
import statistics
import time
import uuid

import pytest
import time_machine

# Statements per request: the count on main when the budget was set (the comment, Nov 19) plus 20%, rounded up.
BUDGETS = {
    "GET /api/v2/threads/rail": 129,  # 107
    "GET /api/v2/needs-you": 137,  # 114
    "GET /api/v2/health": 472,  # 392; moves by a few with the hash seed (holds._usage_limit judges the first two bots alone)
    "GET /api/v2/company/floor": 125,  # 104
    "GET /api/v2/bots": 95,  # 79
    "GET /api/v2/places/person:me": 357,  # 297
    "GET /api/v2/tasks?status=open&limit=500": 114,  # 95
    "GET /api/v2/tasks?status=open&limit=500&counts=stage&brief=true": 122,  # 101: the board
    "GET /api/v2/tasks?owner=bot:bot001&status=open&brief=true&limit=200": 95,  # 79: a bot's open tasks
    "GET /api/status": 54,  # 45
    "GET /api/v2/usage?group=bot": 21,  # 17 (255 before one snapshot and one spend read for every bot)
    "GET /api/v2/usage?group=day": 16,  # 13
    "GET /api/v2/events (until its first read)": 173,  # 144
    "POST /api/v2/runners/heartbeat": 82,  # 68
    "POST /api/v2/jobs/claim": 33,  # 27
    "POST /api/v2/attempts/{aid}/events": 71,  # 59
    "POST /api/v2/runner-claude-logins": 14,  # 11
    "GET /api/v2/runner-credential-grants": 35,  # 29
}
# Bytes of the answer, for the lists whose weight was the cost: the size when the budget was set (the comment) plus 10%.
BYTE_BUDGETS = {
    "GET /api/v2/tasks?status=open&limit=500&counts=stage&brief=true": 1_285_000,  # 1,167,828 (1,459,644 with whole tags)
}
NOW = "2026-11-18T15:00:00Z"  # a Wednesday afternoon
TARGET_MS = 1000  # every route's p95 target
CALLS = 3  # cold calls per route: their counts must agree, and their median is the reported time
PERSON, RUNNER = "ana-test", "runner-r0"
# The benches whose seeds make the company (backend/tests/helpers/tidy_scale.py BENCHES).
SEEDS = (
    "bench/perf-hub-overload.py",
    "bench/perf-status-health.py",
    "bench/perf-holds-pages.py",
    "bench/perf-runner-polls.py",
)  # the owner, who sees everything; the busiest computer


@pytest.fixture(scope="module")
def tidy(tmp_path_factory, api_database):
    from backend.auth import Identity
    from backend.tests.helpers import tidy_scale
    from backend.tests.helpers.api_app import api_client

    # A fixed day: what a page reads per row can depend on the day of the week and the hours since a row was written.
    # The clock ticks on from it, so the build's own order and timeouts are as they would be.
    travel = time_machine.travel(NOW, tick=True)
    travel.start()
    try:
        install = api_client(tmp_path_factory.mktemp("tidy-scale"), api_database)
        try:
            api = next(install)
            started = time.perf_counter()
            made = tidy_scale.build(api)
            made["build_s"] = time.perf_counter() - started
            with api.app.state.store.read() as c:
                made["counts"] = tidy_scale.counts(c)
            api.app.state.store.settings.test_identities[RUNNER] = Identity("runner:r0", "runner", runner_id="r0")
            made["polls"] = tidy_scale.bench("perf-runner-polls")
            yield api, made
        finally:
            install.close()
    finally:
        travel.stop()


def cold(store):
    """Every read cache a request may find warm, emptied: the store's and Health's token use (health._TOKEN_CACHE)."""
    from backend import health

    store.roster_rows = {}
    store.row_cache = type(store.row_cache)()
    store.provenance_cache = type(store.provenance_cache)()
    health._TOKEN_CACHE.clear()


@contextlib.contextmanager
def counted(store):
    """The statements the block's request runs on any connection the store lends it, writes and their commit included:
    every write and snapshot borrows through Store.read, which is traced for the whole borrow. The live stream's reads on
    events.py's own pool count too, while one stream is measured alone."""
    from backend.tests.helpers.statement_scope import AUTH_THREADS, measuring, recorder

    seen = []
    original = store.read

    @contextlib.contextmanager
    def borrowed():
        with original() as c:
            c.set_trace_callback(recorder(seen, (AUTH_THREADS, "events-read")))
            try:
                yield c
            finally:
                c.set_trace_callback(None)

    store.read = borrowed
    try:
        with measuring(seen):
            yield seen
    finally:
        del store.read  # the class's own method again


def headers(token):
    return {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())}


def stream_setup(api, after):
    """Open the live stream as the web app does, one change back so its first read sends a frame, and close it once that
    frame arrives: the sign-in, the Needs-you baseline and the first catch-up read."""
    path = f"/api/v2/events?needs=changes&output=compact&after={after}"
    events = []
    with api.stream("GET", path, headers=headers(PERSON)) as r:
        assert r.status_code == 200, r.read()[:300]
        for line in r.iter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
                if len(events) == 2:
                    break
    assert events[:1] == ["ready"] and len(events) == 2 and events[1] != "expired", events


def latest_change(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT COALESCE(max(seq), 0) FROM changes").fetchone()[0]


def requests(api, made):
    """Each budgeted route as (prepare, call): `prepare` runs before the count (a computer's beat before its claim, as
    its own loop sends them), `call` makes the one request counted and checks it answered."""
    polls, links = made["polls"], made["links"]
    beat = polls.heartbeat_body(api, {"runner_id": "r0"}, links.get("r0", []))
    beat["capabilities"] = [*beat["capabilities"], "claude_login_v1"]  # it runs full logins: the login poll's work
    seq = iter(range(1, 10_000))
    after = {}

    def get(path, token=PERSON):
        return lambda: api.get(path, headers=headers(token))

    def post(path, body, token=RUNNER):
        return lambda: api.post(path, json=body() if callable(body) else body, headers=headers(token))

    def nothing():
        pass

    beating = post("/api/v2/runners/heartbeat", beat)
    return {
        "GET /api/v2/threads/rail": (nothing, get("/api/v2/threads/rail")),
        "GET /api/v2/needs-you": (nothing, get("/api/v2/needs-you")),
        "GET /api/v2/health": (nothing, get("/api/v2/health")),
        "GET /api/v2/company/floor": (nothing, get("/api/v2/company/floor")),
        "GET /api/v2/bots": (nothing, get("/api/v2/bots")),
        "GET /api/v2/places/person:me": (nothing, get("/api/v2/places/person:me")),
        "GET /api/v2/tasks?status=open&limit=500": (nothing, get("/api/v2/tasks?status=open&limit=500")),
        "GET /api/v2/tasks?status=open&limit=500&counts=stage&brief=true": (
            nothing,
            get("/api/v2/tasks?status=open&limit=500&counts=stage&brief=true"),
        ),
        "GET /api/v2/tasks?owner=bot:bot001&status=open&brief=true&limit=200": (
            nothing,
            get("/api/v2/tasks?owner=bot:bot001&status=open&brief=true&limit=200"),
        ),
        "GET /api/status": (nothing, get("/api/status")),
        "GET /api/v2/usage?group=bot": (nothing, get("/api/v2/usage?group=bot")),
        "GET /api/v2/usage?group=day": (nothing, get("/api/v2/usage?group=day")),
        "GET /api/v2/events (until its first read)": (
            lambda: after.update(seq=latest_change(api) - 1),
            lambda: stream_setup(api, after["seq"]),
        ),
        "POST /api/v2/runners/heartbeat": (nothing, beating),
        "POST /api/v2/jobs/claim": (beating, post("/api/v2/jobs/claim", {"start_places": True, "next_run": True})),
        "POST /api/v2/attempts/{aid}/events": (
            beating,
            post(
                f"/api/v2/attempts/{made['attempt']}/events",
                lambda: {"events": [{"seq": next(seq), "kind": "delta", "payload": {"text": "Working on it"}}]},
            ),
        ),
        "POST /api/v2/runner-claude-logins": (beating, post("/api/v2/runner-claude-logins", {"live": []})),
        "GET /api/v2/runner-credential-grants": (nothing, get("/api/v2/runner-credential-grants", RUNNER)),
    }


def shapes(statements, top=8):
    from backend import flight

    common = collections.Counter(flight.fingerprint(sql) for sql in statements).most_common(top)
    return "\n".join(f"      {n:5d}x {shape[:180]}" for shape, n in common)


def answered(route, response):
    assert response is None or response.status_code == 200, (route, response.text[:300])


def test_the_busiest_routes_stay_inside_their_statement_budgets_at_tidy_scale(tidy, request):
    api, made = tidy
    store = api.app.state.store
    calls = requests(api, made)
    assert set(calls) == set(BUDGETS)
    for route, (prepare, call) in calls.items():  # a computer's first beat stores its report; a first read warms up
        prepare()
        answered(route, call())
    # The live stream's shared look for changes another process wrote (events.Doorbell, every 1.5 s) is no part of a
    # stream's setup; while one is measured it waits.
    store.bell._every = 3600
    over, report = [], []
    for route, (prepare, call) in calls.items():
        counts, worst, times = [], [], []
        for _ in range(CALLS):
            prepare()
            cold(store)
            with counted(store) as seen:
                response = call()
            answered(route, response)
            counts.append(len(seen))
            size = len(response.content) if response is not None else 0
            worst = seen if len(seen) >= len(worst) else worst
        for _ in range(CALLS):  # timed apart: the trace callback costs, and a traced connection is not reused
            prepare()
            cold(store)
            started = time.perf_counter()
            answered(route, call())
            times.append((time.perf_counter() - started) * 1000)
        ms = statistics.median(times)
        report.append(
            f"  {route:44s} {max(counts):5d} statements (budget {BUDGETS[route]:5d}) {ms:8.0f} ms"
            + ("  OVER 1 s" if ms > TARGET_MS else "")
            + (f"  (counts {counts})" if len(set(counts)) > 1 else "")
        )
        if max(counts) > BUDGETS[route]:
            over.append(f"  {route}: {max(counts)} statements, budget {BUDGETS[route]}\n{shapes(worst)}")
        if size > BYTE_BUDGETS.get(route, size):
            over.append(f"  {route}: {size} bytes, budget {BYTE_BUDGETS[route]}")
        if route in BYTE_BUDGETS:
            report[-1] += f"  {size} bytes"
    request.node.user_properties.append(
        (
            "summary",
            f"Query budgets at TIDY scale ({made['counts']}; built in {made['build_s']:.1f} s from {', '.join(SEEDS)}), "
            f"median of {CALLS} cold calls:\n" + "\n".join(report),
        )
    )
    assert not over, "over budget (most repeated statement shapes under each):\n" + "\n".join(over)
