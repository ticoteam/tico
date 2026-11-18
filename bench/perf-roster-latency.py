"""Where a person with no admin rights loses time on the people pages, and what a finished bot turn costs the next
requests, on a fictional Tico Cloud company: BOTS bots, half of them owned by Cara (no admin rights), each with a task.

    nice -n 10 .venv/bin/python bench/perf-roster-latency.py

Env:
  RUNS (20), PATHS (comma-separated routes), BOTS (150)
  JSON=file      write every route's JSON per person, to compare before and after (COMPARE=a,b prints the routes that differ)
  PROFILE=1      cProfile RUNS calls of each route as Cara and print the top functions
  TURNS=1        per route: the median with no turns, and with one bot turn finishing (hubdb.turn_start + turn_finish)
                 before each call; prints the roster clock bumps per turn and the whole-roster reads per call

Only fictional data, in a temporary folder removed afterwards. Never point it at a real hub.
"""

import collections
import contextlib
import json
import os
import shutil
import statistics
import sys
import time
import traceback
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend import bot_settings as BS
from backend import cloud_access
from backend import cloud_machines as CM
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers
from backend.tests.helpers.cloud_access import signals

BOTS = [f"bot{n:03d}" for n in range(int(os.environ.get("BOTS", 150)))]
RUNS = int(os.environ.get("RUNS", 20))
PATHS = tuple(
    os.environ.get(
        "PATHS",
        "/api/employees,/api/v2/threads/rail,/api/v2/places/person:me,/api/v2/bots,/api/v2/health,/api/v2/company/floor",
    ).split(",")
)
# Ana owns the company; Cara is a person with no admin rights (Ben is a bot admin in the test registry).
TOKENS = ("ana-test", "cara-test")
SHAPES = {
    "owners": "SELECT owner_id,bot_owners FROM bot_config WHERE bot=",
    "cloud": "SELECT * FROM cloud_machines WHERE bot=",
    "deleting": "SELECT 1 FROM cloud_machine_deletions WHERE machine_id=",
    "config1": BS.ONE_SQL.replace("?", ""),
    "roster": BS.ROW_SQL,
    "roster_in": BS.ROW_SQL + " WHERE bc.bot IN (",
    "bots_all": "SELECT * FROM bots ORDER BY slug",
}


def shape(statement):
    for name, prefix in SHAPES.items():
        if name in ("roster", "bots_all"):
            if statement == prefix:
                return name
        elif statement.startswith(prefix):
            return name
    return None


def seed(api):
    store = api.app.state.store
    cloud_access.apply(store, cloud_access.parse(signals("open")))
    with store.transaction() as c:
        c.execute(
            "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (CM.SIZES_KEY, json.dumps({"sizes": [{"name": "default", "cpu": 2, "memory_gb": 4}], "caps": {}})),
        )
        c.execute(
            "INSERT INTO runners(id,label,owner_id,token_hash,created_at) VALUES('r-shared','shared','ana','hs',?)",
            (H.now(),),
        )
        c.execute(
            "INSERT INTO cloud_machines(bot,mode,placement,machine_id,runner_id,state,changed_by,updated_at) "
            "VALUES(?,'cloud','shared','m-shared','r-shared','running','tico',?)",
            (CM.SHARED, H.now()),
        )
        for n, slug in enumerate(BOTS):
            X.bot(c, slug, computer=False)
            c.execute(
                "UPDATE bot_config SET owner_id=?, reports_to=? WHERE bot=?",
                ("cara" if n % 2 else "ana", BOTS[n // 10] if n >= 10 else None, slug),
            )
            own = n % 7 == 0
            problem = "out_of_credit" if n in (3, 4) else ""
            c.execute(
                "INSERT INTO cloud_machines(bot,mode,placement,machine_id,state,problem,problem_at,changed_by,"
                "updated_at) VALUES(?,'cloud',?,?,?,?,?,'human:ana',?)",
                (
                    slug,
                    "own" if own else "shared",
                    f"m-{slug}" if own else None,
                    "stopped" if own else "none",
                    problem,
                    H.now() if problem else None,
                    H.now(),
                ),
            )
            if n == 14:
                c.execute(
                    "INSERT INTO cloud_machine_deletions(machine_id,bot,reason,created_at) VALUES(?,?,'manual',?)",
                    (f"m-{slug}", slug, H.now()),
                )
        for n, slug in enumerate(BOTS):
            H.task_create(c, "human:ana", f"Work for {slug}", "Do it.", "bot:" + slug, private=n % 5 == 0)


def counting(store, seen, callers=None, label=""):
    originals = {name: getattr(store, name) for name in ("read", "read_transaction")}

    def record(statement):
        seen.append(statement)
        name = shape(statement)
        if callers is not None and name:
            frames = [
                f"{Path(f.filename).stem}.{f.name}:{f.lineno}"
                for f in traceback.extract_stack()[:-1]
                if "/backend/" in f.filename and not f.filename.endswith(("read_cache.py", "store.py"))
            ]
            callers[f"{label} {name}"][" < ".join(reversed(frames[-3:]))] += 1

    def traced(opened):
        @contextlib.contextmanager
        def borrowed():
            with opened() as c:
                c.set_trace_callback(record)
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)

        return borrowed

    @contextlib.contextmanager
    def scope():
        try:
            for name, opened in originals.items():
                setattr(store, name, traced(opened))
            yield
        finally:
            for name, opened in originals.items():
                setattr(store, name, opened)

    return scope()


def canonical(response):
    return json.dumps(response.json(), sort_keys=True)


TURN = {"n": 0}


def turn(api):
    """One bot turn starting and finishing, as a runner reports it."""
    from backend import hubdb

    bot = BOTS[TURN["n"] % len(BOTS)]
    TURN["n"] += 1
    with api.app.state.store.transaction() as c:
        row = hubdb.turn_start(c, "bot:" + bot, bot, trigger="message", trigger_kind="message")
        hubdb.turn_finish(c, "bot:" + bot, row["id"], "ok", "done")


def clock(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT value FROM roster_clock WHERE id=1").fetchone()[0]


def measure(api, path, token, turns=False):
    store, times, seen = api.app.state.store, [], []
    r = api.get(path, headers=headers(token))
    assert r.status_code == 200, (path, r.text[:300])
    if turns:
        turn(api)
    with counting(store, seen):
        api.get(path, headers=headers(token))
    for _ in range(RUNS):
        if turns:
            turn(api)
        t = time.perf_counter()
        api.get(path, headers=headers(token))
        times.append((time.perf_counter() - t) * 1000)
    counts = collections.Counter(shape(s) for s in seen)
    return len(seen), counts, statistics.median(times)


def answers(api):
    """Every route's JSON per person, with times and generated ids replaced (e2e/normalize.py), so two runs compare."""
    from e2e.normalize import normalize

    return {
        f"{path} {token}": normalize(api.get(path, headers=headers(token)).json(), {})
        for path in PATHS
        for token in TOKENS
    }


def profile(api, path, token):
    import cProfile
    import io
    import pstats

    api.get(path, headers=headers(token))
    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(RUNS):
        api.get(path, headers=headers(token))
    profiler.disable()
    for order in ("cumulative", "tottime"):
        out = io.StringIO()
        stats = pstats.Stats(profiler, stream=out).sort_stats(order)
        stats.print_stats(r"backend|copy|json", 22)
        print(f"-- {path} {token} by {order}, {RUNS} calls")
        print(
            "\n".join(line for line in out.getvalue().splitlines() if line.strip() and "Ordered by" not in line)[:6000]
        )


def report(api):
    print(f"{len(BOTS)} bots, {RUNS} runs")
    if os.environ.get("JSON"):
        Path(os.environ["JSON"]).write_text(json.dumps(answers(api), sort_keys=True))
        print(f"answers written to {os.environ['JSON']}")
    if os.environ.get("PROFILE"):
        for path in PATHS:
            profile(api, path, "cara-test")
        return
    turns = (False, True) if os.environ.get("TURNS") else (False,)
    if os.environ.get("TURNS"):
        before = clock(api)
        turn(api)
        print(f"roster clock bumps per finished turn: {clock(api) - before}")
    print(f"{'route':26s} {'person':9s} {'turns':5s} {'total':>6s} {'roster':>6s} {'bots':>5s} {'median ms':>10s}")
    for path in PATHS:
        for token in TOKENS:
            for busy in turns:
                total, counts, ms = measure(api, path, token, busy)
                print(
                    f"{path:26s} {token:9s} {'yes' if busy else 'no':5s} {total:6d} {counts['roster']:6d} "
                    f"{counts['bots_all']:5d} {ms:10.1f}"
                )


def compare(a, b):
    before, after = (json.loads(Path(p).read_text()) for p in (a, b))
    differ = [k for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]
    print(f"{len(before)} answers compared: " + ("all the same" if not differ else "DIFFERENT: " + ", ".join(differ)))


def main():
    import tempfile

    from backend.tests.helpers.api_app import api_client

    if os.environ.get("COMPARE"):
        compare(*os.environ["COMPARE"].split(","))
        return
    with tempfile.TemporaryDirectory(prefix=".tmp-roster-", dir=Path(__file__).resolve().parents[1]) as directory:
        folder = Path(directory)
        install = api_client(folder, None)
        api = next(install)
        t = time.perf_counter()
        seed(api)
        print(f"seeded in {time.perf_counter() - t:.1f}s")
        try:
            report(api)
        finally:
            install.close()
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
