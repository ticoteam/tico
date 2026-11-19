"""The statements the busiest reads and the computers' polls take on a company the size of the hosted TIDY one
(backend/tests/helpers/tidy_scale.py: 18.5k tasks, 19k rooms, 40k messages, 30k followers, 170 bots on 10 computers).

On Nov 19 TIDY's pages ran 200 to 700 statements each, one or a few per row, fine on a test's few rows and 1 to 50 s
there. Each route here has a budget: the statements it took when this test was written plus about a fifth. A change that
adds a read per row goes over it on this company, whatever the timing on the machine running the test. A statement count
is the same on every run, so no time is asserted: the median of a few calls per route is reported at the end of the run
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
    "GET /api/v2/threads/rail": 128,  # 106
    "GET /api/v2/needs-you": 136,  # 113
    "GET /api/v2/health": 472,  # 393
    "GET /api/v2/company/floor": 124,  # 103
    "GET /api/v2/bots": 95,  # 79
    "GET /api/v2/places/person:me": 356,  # 296
    "GET /api/v2/tasks?status=open&limit=500": 113,  # 94
    "GET /api/status": 53,  # 44
    "GET /api/v2/events (until its first read)": 168,  # 140
    "POST /api/v2/runners/heartbeat": 82,  # 68
    "POST /api/v2/jobs/claim": 33,  # 27
    "POST /api/v2/attempts/{aid}/events": 69,  # 57
    "POST /api/v2/runner-claude-logins": 9,  # 7
    "GET /api/v2/runner-credential-grants": 34,  # 28
}
NOW = "2026-11-18T15:00:00Z"  # a Wednesday afternoon
TARGET_MS = 1000  # every route's p95 target
CALLS = 3  # cold calls per route: their counts must agree, and their median is the reported time
PERSON, RUNNER = "ana-test", "runner-r0"  # the owner, who sees everything; the busiest computer


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


@contextlib.contextmanager
def counted(store):
    """The statements the block's request runs on any connection the store lends it, from cold read caches."""
    from backend.tests.helpers.statement_scope import measuring, recorder

    store.roster_rows = {}
    store.row_cache = type(store.row_cache)()
    store.provenance_cache = type(store.provenance_cache)()
    seen = []

    def traced(opened):
        @contextlib.contextmanager
        def borrowed(*args, **kwargs):
            with opened(*args, **kwargs) as c:
                c.set_trace_callback(recorder(seen))
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)

        return borrowed

    names = ("read", "read_transaction", "transaction")
    for name in names:
        setattr(store, name, traced(getattr(store, name)))
    try:
        with measuring(seen):
            yield seen
    finally:
        for name in names:
            delattr(store, name)  # the class's own method again


def headers(token):
    return {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())}


def stream_setup(api):
    """Open the live stream as the web app does, one change back so its first read sends a frame, and close it once that
    frame arrives: the sign-in, the Needs-you baseline and the first catch-up read."""
    with api.app.state.store.read() as c:
        latest = c.execute("SELECT COALESCE(max(seq), 0) FROM changes").fetchone()[0]
    path = f"/api/v2/events?needs=changes&output=compact&after={latest - 1}"
    frames = 0
    with api.stream("GET", path, headers=headers(PERSON)) as r:
        assert r.status_code == 200, r.read()[:300]
        for line in r.iter_lines():
            if line.startswith("event:"):
                frames += 1
                if frames == 2:  # `ready`, then the first read's frame
                    break


def requests(api, made):
    """Each budgeted route as a callable that makes one request and checks it answered."""
    polls, links = made["polls"], made["links"]
    beat = polls.heartbeat_body(api, {"runner_id": "r0"}, links.get("r0", []))
    seq = iter(range(1, 10_000))

    def get(path, token=PERSON):
        return lambda: api.get(path, headers=headers(token))

    def post(path, body, token=RUNNER):
        return lambda: api.post(path, json=body() if callable(body) else body, headers=headers(token))

    return {
        "GET /api/v2/threads/rail": get("/api/v2/threads/rail"),
        "GET /api/v2/needs-you": get("/api/v2/needs-you"),
        "GET /api/v2/health": get("/api/v2/health"),
        "GET /api/v2/company/floor": get("/api/v2/company/floor"),
        "GET /api/v2/bots": get("/api/v2/bots"),
        "GET /api/v2/places/person:me": get("/api/v2/places/person:me"),
        "GET /api/v2/tasks?status=open&limit=500": get("/api/v2/tasks?status=open&limit=500"),
        "GET /api/status": get("/api/status"),
        "GET /api/v2/events (until its first read)": lambda: stream_setup(api),
        "POST /api/v2/runners/heartbeat": post("/api/v2/runners/heartbeat", beat),
        "POST /api/v2/jobs/claim": post("/api/v2/jobs/claim", {"start_places": True, "next_run": True}),
        "POST /api/v2/attempts/{aid}/events": post(
            f"/api/v2/attempts/{made['attempt']}/events",
            lambda: {"events": [{"seq": next(seq), "kind": "delta", "payload": {"text": "Working on it"}}]},
        ),
        "POST /api/v2/runner-claude-logins": post("/api/v2/runner-claude-logins", {"live": []}),
        "GET /api/v2/runner-credential-grants": get("/api/v2/runner-credential-grants", RUNNER),
    }


def shapes(statements, top=8):
    from backend import flight

    common = collections.Counter(flight.fingerprint(sql) for sql in statements).most_common(top)
    return "\n".join(f"      {n:5d}x {shape[:180]}" for shape, n in common)


def test_the_busiest_routes_stay_inside_their_statement_budgets_at_tidy_scale(tidy, record_property):
    api, made = tidy
    store = api.app.state.store
    calls = requests(api, made)
    assert set(calls) == set(BUDGETS)
    for call in calls.values():  # a computer's first beat stores its report and a first read warms the process
        response = call()
        assert response is None or response.status_code == 200, response.text[:300]
    over, report, unsteady = [], [], []
    for route, call in calls.items():
        counts, times, last = [], [], []
        for _ in range(CALLS):
            with counted(store) as seen:
                started = time.perf_counter()
                response = call()
                times.append((time.perf_counter() - started) * 1000)
            assert response is None or response.status_code == 200, (route, response.text[:300])
            counts.append(len(seen))
            last = seen
        if len(set(counts)) != 1:
            unsteady.append(f"{route}: {counts}")
        ms = statistics.median(times)
        report.append(
            f"  {route:44s} {counts[-1]:5d} statements (budget {BUDGETS[route]:5d}) {ms:8.0f} ms"
            + ("  OVER 1 s" if ms > TARGET_MS else "")
        )
        if counts[-1] > BUDGETS[route]:
            over.append(f"  {route}: {counts[-1]} statements, budget {BUDGETS[route]}\n{shapes(last)}")
    record_property(
        "summary",
        f"Query budgets at TIDY scale ({made['counts']}; built in {made['build_s']:.1f} s), median of {CALLS} cold "
        f"calls:\n" + "\n".join(report),
    )
    assert not unsteady, "a route's statement count changed between identical calls:\n" + "\n".join(unsteady)
    assert not over, "over budget (most repeated statement shapes under each):\n" + "\n".join(over)
