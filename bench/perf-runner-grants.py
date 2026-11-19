"""Measures GET /api/v2/runner-credential-grants on a fictional company the size of the hosted TIDY one (Nov 19 flight
recorder: 230 calls an hour, avg 565 ms, slow samples median 3.3 s with 2.0 s of SQL in 123 statements, the company's
default-read tools and keys read once per bot): BOTS bots over RUNNERS computers (~20 each), TOOLS tools with a few
hundred keys, settings and mixed grants (backend/tests/helpers/runner_grants.py).

    nice -n 10 python bench/perf-runner-grants.py

Env: BOTS (default 165), TOOLS (60), RUNNERS (8), RUNS (21).

For each computer it prints, old (each bot read on its own, outside a snapshot, as the route did before) and new (the
route as it is): the median handler ms, the median SQL ms the flight recorder timed, and the statements one call
runs; then the HTTP median of the new route, the statements the old one repeated most, and the plans of the reads.
The two answers must be equal. Only fictional data, in a temporary folder removed afterwards. Never point it at a real
hub.
"""

import collections
import dataclasses
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from fastapi.testclient import TestClient

from backend import default_read, flight, tool_settings
from backend.app import create_app
from backend.auth import Identity
from backend.tests.helpers.api import api_settings, headers
from backend.tests.helpers.company import seed as seed_company
from backend.tests.helpers.runner_grants import reference, seed

BOTS = ["tools-owner"] + [f"bot{n:03d}" for n in range(int(os.environ.get("BOTS", 165)) - 1)]
TOOLS = int(os.environ.get("TOOLS", 60))
RUNNERS = int(os.environ.get("RUNNERS", 8))
RUNS = int(os.environ.get("RUNS", 21))
PATH = "/api/v2/runner-credential-grants"


def timed(call):
    """(answer, handler ms, SQL ms, {statement: count}) of one call, its SQL timed by the flight recorder."""
    cost, token = flight.request_started()
    started = time.perf_counter()
    try:
        out = call()
    finally:
        flight.request_finished(token)
    return out, (time.perf_counter() - started) * 1000, cost[0], {s: v[0] for s, v in cost[3].items()}


def median_of(call):
    timed(call)  # warm
    samples = [timed(call) for _ in range(RUNS)]
    return (
        samples[0][0],
        statistics.median(s[1] for s in samples),
        statistics.median(s[2] for s in samples),
        samples[0][3],
    )


def main():
    with tempfile.TemporaryDirectory(prefix=".tmp-grants-", dir=Path(__file__).resolve().parents[1]) as directory:
        folder = Path(directory)
        settings = dataclasses.replace(api_settings(folder), flight_recorder=True)
        app = create_app(settings)
        try:
            with TestClient(app) as api:
                run(app, api)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


def run(app, api):
    store = app.state.store
    with store.transaction() as c:
        seed_company(c)
    t = time.perf_counter()
    with store.transaction() as c:
        seed(c, BOTS, random.Random(19), tools=TOOLS, runners=RUNNERS)
    with store.read() as c:
        counts = {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("bots", "tools", "credentials", "credential_grants", "tool_settings")
        }
    print(f"seeded in {time.perf_counter() - t:.1f}s", counts)
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == PATH)
    totals = collections.Counter()
    worst = None
    for n in range(RUNNERS):
        rid = f"r{n}"
        token = "runner-" + rid
        who = Identity("runner:" + rid, "runner", runner_id=rid)
        settings = store.settings
        settings.test_identities[token] = who
        request = SimpleNamespace(state=SimpleNamespace(identity=who))
        old, old_ms, old_sql, old_statements = median_of(lambda rid=rid: reference(store, rid))
        new, new_ms, new_sql, new_statements = median_of(lambda request=request: endpoint(request))
        assert new == old, rid
        http = []
        for _ in range(RUNS):
            started = time.perf_counter()
            r = api.get(PATH, headers=headers(token))
            http.append((time.perf_counter() - started) * 1000)
            assert r.status_code == 200 and r.json() == old
        olds, news = sum(old_statements.values()), sum(new_statements.values())
        print(
            f"{rid}: {len(old['bots']):3d} bots | old {old_ms:7.1f} ms, SQL {old_sql:7.1f} ms, {olds:4d} statements"
            f" | new {new_ms:7.1f} ms, SQL {new_sql:7.1f} ms, {news:4d} statements | new HTTP {statistics.median(http):7.1f} ms"
        )
        totals.update(old_ms=old_ms, old_sql=old_sql, olds=olds, new_ms=new_ms, new_sql=new_sql, news=news)
        if worst is None or olds > sum(worst[0].values()):
            worst = (old_statements, new_statements)
    k = RUNNERS
    print(
        f"mean per computer: old {totals['old_ms'] / k:.1f} ms (SQL {totals['old_sql'] / k:.1f} ms, "
        f"{totals['olds'] / k:.0f} statements) -> new {totals['new_ms'] / k:.1f} ms (SQL {totals['new_sql'] / k:.1f} ms, "
        f"{totals['news'] / k:.0f} statements)"
    )
    for label, statements in zip(("old", "new"), worst, strict=True):
        print(f"{label}: statements by count (one call, the busiest computer)")
        for sql, count in sorted(statements.items(), key=lambda kv: -kv[1])[:8]:
            print(f"    {count:4d}  {' '.join(sql.split())[:150]}")
    explain(store)


def explain(store):
    """The plans of the reads: unchanged statements, now run once a request instead of once a bot."""
    subs = ["bot:" + b for b in (BOTS * 4)[1:4]]  # three subjects, however few bots
    per_subject = (
        "SELECT g.credential_id FROM credential_grants g LEFT JOIN credential_grants p ON p.id=g.parent_id "
        "LEFT JOIN bot_config bc ON substr(g.subject,1,4)='bot:' AND bc.bot=substr(g.subject,5) "
        "WHERE g.subject=? AND g.revoked_at IS NULL AND (g.parent_id IS NULL OR (p.revoked_at IS NULL AND p.id IS NOT "
        "NULL AND p.subject='human:'||bc.owner_id))"
    )
    batched = per_subject.replace("SELECT g.credential_id", "SELECT g.subject,g.credential_id").replace(
        "g.subject=?", "g.subject IN (?,?,?)"
    )
    plans = {
        "default_read.CANDIDATES_SQL (old 1/bot, new 1/request)": (default_read.CANDIDATES_SQL, ()),
        "tool_settings.KEY_TOOLS_SQL (old 1/bot, new 1/request)": (tool_settings.KEY_TOOLS_SQL, ()),
        "explicit grants, one subject (old 1/bot)": (per_subject, subs[:1]),
        "explicit grants, prime_granted (new 1/500 bots)": (batched, subs),
        "tool settings of tools (old 1/bot, new 1/request)": (
            "SELECT s.*, t.name AS tool_name FROM tool_settings s JOIN tools t ON t.id=s.tool_id"
            " WHERE s.tool_id IN (?,?,?) ORDER BY s.tool_id, s.name, s.id",
            ("tool01", "tool02", "tool03"),
        ),
    }
    with store.read() as c:
        for name, (sql, args) in plans.items():
            plan = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql, args)]
            print(f"{name}\n    " + " / ".join(plan))


if __name__ == "__main__":
    main()
