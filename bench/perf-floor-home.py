"""Measures the Company page's two reads at app open, GET /api/v2/company/floor (the Floor) and GET
/api/v2/places/person:me (Home's cards), on a fictional software company: the preset pipelines and their role bots,
thirty more bots, OPEN open tasks spread over every work stage (a third with an OK asked of Ana or Ben, a fifth with a
PR), CLOSED finished tasks, threads with @s, and a month of daily updates.

    nice -n 19 .venv/bin/python bench/perf-floor-home.py

For each read, as Ana (the company's person, on every task's ladder) and as Ben, it prints the statements one call
runs, how many of them Needs you's build (views.needs_items) runs inside it, and the median wall time of RUNS calls;
for Home, the same per card. Then the statement shapes that repeat most in each read, with the wall time from each
statement to the next one summed (the statement and the Python after it), and the query plans of the Floor's own
statements. PROFILE=1 adds a cProfile of each read.

Only fictional data, in a temporary folder that is removed afterwards. Never point it at a real hub.
"""

import collections
import contextlib
import json
import os
import random
import re
import statistics
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend import cards as CARDS
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import threads
from backend import views as V
from backend.store import H, Problem
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers
from backend.tests.helpers.api_app import api_client
from backend.tests.helpers.product_engineering_presets import _answers, _team
from backend.tests.helpers.stage_inputs import _link_pr
from backend.tests.helpers.statement_scope import measuring, recorder

OPEN = int(os.environ.get("OPEN", 400))
CLOSED = int(os.environ.get("CLOSED", 3000))
THREADS = int(os.environ.get("THREADS", 150))
RUNS = int(os.environ.get("RUNS", 21))
WORKERS = [f"worker{n:02d}" for n in range(30)]
PEOPLE = ["human:ana", "human:ben", "human:cara"]
READS = ("/api/v2/company/floor", "/api/v2/places/person:me")
SKIP = (Problem, H.Refused)  # a move or an ask the engine refuses for this task: the bench goes on without it


def seed(api):
    store = api.app.state.store
    rnd = random.Random(7)
    with store.transaction() as c:
        _team(c)
        _answers(c, software="yes")
        PS.ensure_presets(c)
        X.github_installed(c)
        for slug in WORKERS:
            X.bot(c, slug)
    with store.read() as c:
        stages = [
            (str(r[0]), str(r[1]))
            for r in c.execute(
                "SELECT s.id, s.pipeline_id FROM stages s JOIN pipelines p ON p.id=s.pipeline_id "
                "WHERE s.retired_at IS NULL AND p.retired_at IS NULL AND s.kind='work' AND coalesce(s.side,0)=0 "
                "ORDER BY s.pipeline_id, s.position"
            )
        ]
        done = {
            str(r[1]): str(r[0])
            for r in c.execute("SELECT id, pipeline_id FROM stages WHERE retired_at IS NULL AND kind='done'")
        }
    owners = [f"bot:{w}" for w in WORKERS] + PEOPLE
    made = 0
    for start in range(0, OPEN, 50):
        with store.transaction() as c:
            for k in range(start, min(OPEN, start + 50)):
                stage, pipeline = stages[k % len(stages)]
                row = H.task_create(
                    c,
                    "human:ana",
                    f"Open task {k}",
                    "What to do.",
                    owners[k % len(owners)],
                    pipeline=pipeline,
                    lint=False,
                )
                with contextlib.suppress(*SKIP):
                    if E.task(c, row["id"])["stage_id"] != stage:
                        E.move(c, row["id"], stage, "human:ana", "Set up for the bench")
                if k % 3 == 0:
                    version = H.task(c, row["id"])["version"]
                    with contextlib.suppress(*SKIP):
                        E.add_requirement(
                            c,
                            row["id"],
                            "human:cara",
                            "ok",
                            PEOPLE[k % 2],
                            f"Ready to ship {k}?",
                            seen_version=version,
                        )
                if k % 5 == 0:
                    _link_pr(c, row["id"], 1000 + k, f"{k:040x}")
                made += 1
    pipelines = sorted({p for _, p in stages if p in done})
    for start in range(0, CLOSED, 100):
        with store.transaction() as c:
            for k in range(start, min(CLOSED, start + 100)):
                pipeline = pipelines[k % len(pipelines)]
                row = H.task_create(
                    c,
                    "human:ana",
                    f"Old task {k}",
                    "Done long ago.",
                    owners[k % len(owners)],
                    pipeline=pipeline,
                    lint=False,
                )
                try:
                    E.move(c, row["id"], done[pipeline], "human:ana", "Finished for the bench")
                except SKIP:
                    E.move(c, row["id"], E.CLOSED, "human:ana", "Closed for the bench")
    with store.transaction() as c:
        for n in range(THREADS):
            starter = rnd.choice(PEOPLE + [f"bot:{w}" for w in WORKERS[:10]])
            text = f"Thread {n} kickoff" + (" @ana please look" if n % 3 == 0 else "")
            out = threads.post(c, starter, None, text, start={"title": f"Topic {n}", "audience": "company"})
            tid = out.get("thread_id") or out.get("conversation_id")
            for k in range(4):
                who = rnd.choice(PEOPLE + [f"bot:{w}" for w in WORKERS])
                with contextlib.suppress(*SKIP):
                    threads.post(c, who, tid, f"Note {k}" + (" @ben" if rnd.random() < 0.2 else ""))
        now, rows = H.now(), []
        for d in range(30):
            day = H.shift(now, days=-d)[:10]
            for w in WORKERS:
                rows.append(
                    (
                        H.new_id(),
                        w,
                        "daily",
                        day,
                        f"{w} on {day}",
                        "- shipped a thing",
                        H.shift(now, days=-d),
                        H.shift(now, days=-d),
                        None,
                        None,
                        None,
                        json.dumps([]),
                        None,
                        0,
                    )
                )
        c.executemany(
            "INSERT INTO updates(id,bot,kind,day,headline,body,created_at,updated_at,slides,drafted_by,edited_by,"
            "inputs,source,meetings) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    with store.read() as c:
        return {
            "open pipeline tasks": c.execute(
                "SELECT count(*) FROM tasks WHERE status='open' AND pipeline_id IS NOT NULL"
            ).fetchone()[0],
            "tasks": c.execute("SELECT count(*) FROM tasks").fetchone()[0],
            "visits": c.execute("SELECT count(*) FROM visits").fetchone()[0],
            "tasks by status": dict(c.execute("SELECT status, count(*) FROM tasks GROUP BY status").fetchall()),
            "open tasks by stage": len(
                c.execute(
                    "SELECT DISTINCT stage_id FROM tasks WHERE status='open' AND pipeline_id IS NOT NULL"
                ).fetchall()
            ),
            "open OKs": c.execute(
                "SELECT count(*) FROM requirements WHERE kind='ok' AND withdrawn_at IS NULL"
            ).fetchone()[0],
            "messages": c.execute("SELECT count(*) FROM messages").fetchone()[0],
        }


@contextlib.contextmanager
def tracing(store, seen):
    """Each statement the request runs on a borrowed connection, as (perf_counter, sql)."""
    originals = {name: getattr(store, name) for name in ("read", "read_transaction")}
    record = recorder(seen)

    def traced(opened):
        @contextlib.contextmanager
        def borrowed():
            with opened() as c:
                c.set_trace_callback(lambda sql: record((time.perf_counter(), sql)))
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)

        return borrowed

    try:
        for name, opened in originals.items():
            setattr(store, name, traced(opened))
        with measuring(seen):
            yield
    finally:
        for name, opened in originals.items():
            setattr(store, name, opened)


@contextlib.contextmanager
def attributed(seen, spans):
    """Which statements of `seen` ran inside needs_items and inside each Home card ({name: [count]})."""
    needs_items, card_data = V.needs_items, CARDS.card_data

    def counted(name, call):
        def run(*args, **kwargs):
            before = len(seen)
            try:
                return call(*args, **kwargs)
            finally:
                spans[name] = spans.get(name, 0) + len(seen) - before

        return run

    V.needs_items = counted("needs_items", needs_items)
    CARDS.card_data = lambda ctx, template, settings: counted("card " + template, card_data)(ctx, template, settings)
    try:
        yield
    finally:
        V.needs_items, CARDS.card_data = needs_items, card_data


def shape(sql):
    sql = re.sub(r"'(?:[^']|'')*'", "?", sql)
    sql = re.sub(r"\b\d+\b", "?", sql)
    sql = re.sub(r"\?(?:\s*,\s*\?)+", "?…", sql)
    return re.sub(r"\s+", " ", sql).strip()


def one(api, path, token):
    store = api.app.state.store
    response = api.get(path, headers=headers(token))  # warm: the caches a connection keeps
    assert response.status_code == 200, response.text
    seen, spans = [], {}
    with attributed(seen, spans), tracing(store, seen):
        started = time.perf_counter()
        api.get(path, headers=headers(token))
        ended = time.perf_counter()
    times = []
    for _ in range(RUNS):
        began = time.perf_counter()
        api.get(path, headers=headers(token))
        times.append((time.perf_counter() - began) * 1000)
    costs = collections.Counter()
    counts = collections.Counter()
    for (at, sql), nxt in zip(seen, [s[0] for s in seen[1:]] + [ended], strict=True):
        counts[shape(sql)] += 1
        costs[shape(sql)] += (nxt - at) * 1000
    return {
        "statements": len(seen),
        "median_ms": statistics.median(times),
        "spans": spans,
        "counts": counts,
        "costs": costs,
        "traced_ms": (ended - started) * 1000,
    }


def report(api):
    for token in ("ana-test", "ben-test"):
        for path in READS:
            out = one(api, path, token)
            spans = out["spans"]
            print(
                f"\n{path} as {token[:3]}: {out['statements']} statements, {out['median_ms']:.1f} ms median of {RUNS}"
                f" (needs_items: {spans.get('needs_items', 0)} statements)"
            )
            for name, n in sorted(spans.items()):
                if name.startswith("card "):
                    print(f"    {name:28s} {n:5d} statements")
            print("    top statement shapes (calls, ms to the next statement):")
            for sql, n in out["counts"].most_common(12):
                print(f"    {n:5d} {out['costs'][sql]:8.1f} ms  {sql[:190]}")
            slowest = sorted(out["costs"].items(), key=lambda kv: -kv[1])[:6]
            print("    slowest shapes:")
            for sql, ms in slowest:
                print(f"    {out['counts'][sql]:5d} {ms:8.1f} ms  {sql[:190]}")
    if os.environ.get("PROFILE"):
        import cProfile
        import pstats

        for path in READS:
            prof = cProfile.Profile()
            prof.enable()
            for _ in range(3):
                api.get(path, headers=headers("ana-test"))
            prof.disable()
            print(f"\nprofile {path}")
            pstats.Stats(prof).sort_stats("cumulative").print_stats(r"backend", 30)


def plans(api):
    """The Floor's own statements' query plans, as Ana reads them."""
    from backend import company_floor as F

    from backend.auth import Identity

    store, auth = api.app.state.store, api.app.state.auth
    who = Identity("human:ana", "owner", "ana@acme.example")
    seen = []
    with store.read_transaction() as c:
        c.set_trace_callback(seen.append)
        F._rates(c, ["product"], auth.task_sql(c, who))
        F._moves(c, auth.task_sql(c, who), {"product"})
        F._holders(c, auth.task_sql(c, who), [])
        c.set_trace_callback(None)
        for sql in seen:
            if not sql.lstrip().upper().startswith("SELECT"):
                continue
            plan = [str(step[3]) for step in c.execute("EXPLAIN QUERY PLAN " + sql).fetchall()]
            print("\nplan:", shape(sql)[:160])
            for step in plan:
                print("    ", step)


def main(api):
    began = time.perf_counter()
    sizes = seed(api)
    print(f"seeded in {time.perf_counter() - began:.1f}s: {sizes}")
    report(api)
    plans(api)


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory(prefix=".tmp-floor-home-", dir=Path(__file__).resolve().parents[1]) as folder:
        install = api_client(Path(folder), None)
        try:
            main(next(install))
        finally:
            install.close()
