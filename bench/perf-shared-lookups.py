"""Counts the three one-bot lookups the hot pages repeat per bot (Nov 18 TIDY flight recorder): a bot's owners
(`SELECT owner_id,bot_owners FROM bot_config WHERE bot=?`), its cloud machine (`SELECT * FROM cloud_machines WHERE
bot=?`) and its declaration (bot_settings.ONE_SQL), plus the whole-roster read (bot_settings.ROW_SQL), on a fictional
Tico Cloud company: BOTS bots on cloud machines (most shared, some their own, two refused for credit), half of them
owned by the non-admin person Ben, each with an open task.

    nice -n 10 .venv/bin/python bench/perf-shared-lookups.py

Env: BOTS (default 150), RUNS (20), CALLERS=1 (who issues the counted lookups), SAME=1 (each answer read again with
the snapshot read cache off, which must give the same JSON).

Per route and person it prints the statements of one call, each counted shape, and the median of RUNS calls. Only
fictional data, in a temporary folder removed afterwards. Never point it at a real hub.
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
from backend import cloud_access, read_cache
from backend import cloud_machines as CM
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers
from backend.tests.helpers.cloud_access import signals

BOTS = [f"bot{n:03d}" for n in range(int(os.environ.get("BOTS", 150)))]
RUNS = int(os.environ.get("RUNS", 20))
PATHS = ("/api/v2/bots", "/api/v2/health", "/api/v2/company/floor", "/api/employees")
TOKENS = ("ana-test", "ben-test")
SHAPES = {
    "owners": "SELECT owner_id,bot_owners FROM bot_config WHERE bot=",
    "cloud": "SELECT * FROM cloud_machines WHERE bot=",
    "deleting": "SELECT 1 FROM cloud_machine_deletions WHERE machine_id=",
    "config1": BS.ONE_SQL.replace("?", ""),
    "roster": BS.ROW_SQL,
    "roster_in": BS.ROW_SQL + " WHERE bc.bot IN (",
}


def shape(statement):
    for name, prefix in SHAPES.items():
        if name == "roster":
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
                ("ben" if n % 2 else "ana", BOTS[n // 10] if n >= 10 else None, slug),
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


def measure(api, path, token, callers):
    store, times, seen = api.app.state.store, [], []
    r = api.get(path, headers=headers(token))
    assert r.status_code == 200, (path, r.text[:300])
    with counting(store, seen, callers, f"{path} {token}"):
        api.get(path, headers=headers(token))
    for _ in range(RUNS):
        t = time.perf_counter()
        api.get(path, headers=headers(token))
        times.append((time.perf_counter() - t) * 1000)
    counts = collections.Counter(shape(s) for s in seen)
    return len(seen), counts, statistics.median(times)


def same(api, path, token):
    """'same' when the answer with the snapshot read cache off is the one with it on; two cached answers that differ
    (a clock in the answer) make the check unusable, said as 'unstable'."""
    first = canonical(api.get(path, headers=headers(token)))
    real = read_cache.cache
    read_cache.cache = lambda c: None
    try:
        off = canonical(api.get(path, headers=headers(token)))
    finally:
        read_cache.cache = real
    again = canonical(api.get(path, headers=headers(token)))
    if first != again:
        return "unstable"
    return "same" if off == first else "DIFFERENT"


def report(api):
    callers = collections.defaultdict(collections.Counter) if os.environ.get("CALLERS") else None
    print(f"{len(BOTS)} bots, {RUNS} runs")
    print(
        f"{'route':24s} {'person':9s} {'total':>6s} {'owners':>6s} {'cloud':>6s} {'delet':>6s} "
        f"{'cfg1':>6s} {'roster':>6s} {'rost_in':>7s} {'median ms':>10s} json"
    )
    for path in PATHS:
        for token in TOKENS:
            total, counts, ms = measure(api, path, token, callers)
            verdict = same(api, path, token) if os.environ.get("SAME") else "-"
            print(
                f"{path:24s} {token:9s} {total:6d} {counts['owners']:6d} {counts['cloud']:6d} "
                f"{counts['deleting']:6d} {counts['config1']:6d} {counts['roster']:6d} {counts['roster_in']:7d} "
                f"{ms:10.1f} {verdict}"
            )
    if callers is not None:
        for name, found in callers.items():
            print(f"-- {name}")
            for where, k in found.most_common(8):
                print(f"{k:6d}  {where}")


def main():
    import tempfile

    from backend.tests.helpers.api_app import api_client

    with tempfile.TemporaryDirectory(prefix=".tmp-lookups-", dir=Path(__file__).resolve().parents[1]) as directory:
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
