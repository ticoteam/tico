"""Measures the task page (GET /api/v2/tasks/{tid}) and the task lists people's browsers poll (GET /api/v2/tasks with
brief=1) on a fictional company: a pipeline of four stages with requirements, a few hundred tasks a bot and a person
own, open, done and closed, and one task with a long history (bot replies from runs, people's notes, child tasks).

    nice -n 19 python3 bench/perf-task-reads.py [out.json]

Per read it prints the statements one call takes (cold row caches and warm), the median wall time of RUNS calls, the
answer's size, and the statements it repeats most. With a path, it also writes them as JSON.
"""

import collections
import contextlib
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as EH
from backend.tests.helpers.api import headers
from backend.tests.helpers.api_app import api_client
from backend.tests.helpers.statement_scope import measuring, recorder
from backend.tests.test_task_detail_queries import build

RUNS = 20
OPEN, DONE, CLOSED, PEOPLE_TASKS = 160, 220, 120, 150
STAGES = tuple(
    {
        "name": name,
        "owner": "assignee",
        "requirements": [{"kind": "owner_done"}, {"kind": "owner_done", "line": f"{name} checked"}],
    }
    for name in ("Spec", "Build", "Review", "Ship")
)


def seed(api):
    """The task with a long history (build), then the lists' tasks in a four-stage pipeline."""
    tid = build(api, comments=8, people_comments=6, events=20, children=6, bots=20)
    store = api.app.state.store
    with store.transaction() as c:
        flow = EH.pipeline(c, "eng", "Engineering", STAGES)
        stage_ids = list(flow["stages"].values())[:4]
        parent = None
        for n in range(OPEN + DONE + CLOSED):
            owner = "bot:ops" if n % 3 else "bot:cpo"
            row = H.task_create(
                c,
                "human:ana" if n % 2 else "human:ben",
                f"Acme item {n}",
                "Make the thing better. " * 8,
                owner,
                pipeline="eng",
                stage=stage_ids[n % 4],
                labels=["acme", f"area-{n % 5}"] if n % 4 == 0 else None,
                parent_id=parent if n % 7 == 0 else None,
                lint=False,
                deduplicate=False,
            )
            if n % 25 == 0:
                parent = row["id"]
            if n % 5 == 0:
                H.task_comment(c, "human:ana", row["id"], f"Note on {n}", wake=False)
            if OPEN <= n < OPEN + DONE:
                E._write(c, row["id"], status="done")
                c.execute("UPDATE tasks SET done_at=? WHERE id=?", (H.now(), row["id"]))
            elif n >= OPEN + DONE:
                E._write(c, row["id"], status="closed")
                c.execute("UPDATE tasks SET closed_at=? WHERE id=?", (H.now(), row["id"]))
        for n in range(PEOPLE_TASKS):
            H.task_create(
                c,
                "bot:ops",
                f"Acme ask {n}",
                "Please look. " * 6,
                "human:ana",
                pipeline="eng",
                stage=stage_ids[n % 4],
                lint=False,
                deduplicate=False,
            )
        EH.settled(c)
    return tid


@contextlib.contextmanager
def counting(store, seen):
    """Every statement the measured request runs on the store's connections, by statement_scope's rule."""

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

    originals = {name: getattr(store, name) for name in ("read", "read_transaction", "transaction")}
    try:
        for name, opened in originals.items():
            setattr(store, name, traced(opened))
        with measuring(seen):
            yield
    finally:
        for name, opened in originals.items():
            setattr(store, name, opened)


def sites(store, path, token, api):
    """Each statement of one warm call with where it ran from: the innermost backend frames outside the store."""
    import traceback

    found = []

    def traced(opened):
        @contextlib.contextmanager
        def borrowed(*args, **kwargs):
            with opened(*args, **kwargs) as c:
                record = recorder(found)

                def hook(sql):
                    record(sql)
                    if found and found[-1] is sql:
                        frames = [
                            f"{Path(f.filename).name}:{f.lineno}:{f.name}"
                            for f in traceback.extract_stack()[:-1]
                            if "/backend/" in f.filename and "/tests/" not in f.filename
                        ]
                        found[-1] = (sql, " < ".join(reversed(frames[-4:])))

                c.set_trace_callback(hook)
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)

        return borrowed

    originals = {name: getattr(store, name) for name in ("read", "read_transaction", "transaction")}
    try:
        for name, opened in originals.items():
            setattr(store, name, traced(opened))
        with measuring(found):
            api.get(path, headers=headers(token))
    finally:
        for name, opened in originals.items():
            setattr(store, name, opened)
    return [f if isinstance(f, tuple) else (f, "?") for f in found]


def shape(sql):
    sql = re.sub(r"'[^']*'", "?", sql)
    sql = re.sub(r"\b\d+\b", "?", sql)
    sql = re.sub(r"\?(\s*,\s*\?)+", "?…", sql)
    return re.sub(r"\s+", " ", sql).strip()[:170]


def measure(api, path, token):
    store = api.app.state.store
    first = api.get(path, headers=headers(token))
    assert first.status_code == 200, (path, first.text[:300])
    cold, warm = [], []
    store.roster_rows = {}
    store.row_cache = type(store.row_cache)()
    with counting(store, cold):
        api.get(path, headers=headers(token))
    with counting(store, warm):
        api.get(path, headers=headers(token))
    times = []
    for _ in range(RUNS):
        t = time.perf_counter()
        api.get(path, headers=headers(token))
        times.append((time.perf_counter() - t) * 1000)
    top = collections.Counter(shape(q) for q in warm).most_common(12)
    return {
        "path": path,
        "token": token,
        "statements_cold": len(cold),
        "statements_warm": len(warm),
        "median_ms": round(statistics.median(times), 1),
        "kb": round(len(first.content) / 1024, 1),
        "top": top,
    }


def explain(api, ids):
    """EXPLAIN QUERY PLAN and median time (RUNS runs) of the statements this branch changed, old and new shapes, as
    the reader cara (a person with rules) asks them."""
    from backend.auth import Identity

    store, auth = api.app.state.store, api.app.state.auth
    with store.read_transaction() as c:
        visible = auth.task_sql(c, Identity("human:cara", "human", "cara@acme.example"))
        stage_ids = sorted({r[0] for r in c.execute("SELECT stage_id FROM tasks WHERE stage_id IS NOT NULL")})
        pipeline_ids = sorted({r[0] for r in c.execute("SELECT pipeline_id FROM tasks WHERE pipeline_id IS NOT NULL")})
        named = json.dumps(ids)
        cases = {
            "hidden refs, old (two statements)": [
                ("SELECT id FROM tasks WHERE id IN (SELECT value FROM json_each(?))", (named,)),
                ("SELECT id FROM tasks WHERE id IN (SELECT value FROM json_each(?)) AND (" + visible + ")", (named,)),
            ],
            "hidden refs, new (one statement)": [
                (
                    "SELECT id FROM tasks WHERE id IN (SELECT value FROM json_each(?)) AND NOT coalesce(("
                    + visible
                    + "),0)",
                    (named,),
                )
            ],
            "stages, old (one per stage)": [("SELECT * FROM stages WHERE id=?", (i,)) for i in stage_ids],
            "stages, new (one)": [
                ("SELECT * FROM stages WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(stage_ids),))
            ],
            "pipelines, old (one per pipeline)": [("SELECT * FROM pipelines WHERE id=?", (i,)) for i in pipeline_ids],
            "pipelines, new (one)": [
                ("SELECT * FROM pipelines WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(pipeline_ids),))
            ],
        }
        old = [{r[0] for r in c.execute(sql, args)} for sql, args in cases["hidden refs, old (two statements)"]]
        new = {r[0] for r in c.execute(*cases["hidden refs, new (one statement)"][0])}
        print(f"hidden refs: old {len(old[0] - old[1])}, new {len(new)}, same {old[0] - old[1] == new}")
        for name, statements in cases.items():
            times = []
            for _ in range(RUNS):
                t = time.perf_counter()
                for sql, args in statements:
                    c.execute(sql, args).fetchall()
                times.append((time.perf_counter() - t) * 1e6)
            print(f"EXPLAIN {name}: {len(statements)} statements, median {statistics.median(times):.0f} us")
            for sql, args in statements[:2]:
                for row in c.execute("EXPLAIN QUERY PLAN " + sql, args):
                    print("      ", row[3])


def main(out=None):
    import tempfile

    with tempfile.TemporaryDirectory(prefix=".tmp-task-reads-", dir=Path(__file__).resolve().parents[1]) as directory:
        install = api_client(Path(directory), None)
        api = next(install)
        try:
            t = time.perf_counter()
            tid = seed(api)
            print(f"seeded in {time.perf_counter() - t:.1f}s")
            old = H.shift(H.now(), days=-30)
            some = api.get("/api/v2/tasks?brief=1&owner=ops&status=open&limit=1", headers=headers("ana-test")).json()
            listed = some["tasks"][0]["id"]
            paths = [
                (f"/api/v2/tasks/{tid}", "ana-test"),
                (f"/api/v2/tasks/{tid}", "cara-test"),
                (f"/api/v2/tasks/{listed}", "ana-test"),
                # what the open page reads, in place of the whole task, while its run works (bench/task-page-reads.cts)
                (f"/api/v2/tasks/{tid}/execution", "ana-test"),
                ("/api/v2/tasks?brief=1&owner=human:ana&status=all", "ana-test"),
                (f"/api/v2/tasks?owner=ops&status=done,closed&snoozed=all&brief=true&updated_since={old}", "ana-test"),
                ("/api/v2/tasks?brief=1&status=open", "ben-test"),
            ]
            results = []
            for path, token in paths:
                r = measure(api, path, token)
                results.append(r)
                print(
                    f"{path[:90]:90s} {token:9s} {r['statements_cold']:5d} cold {r['statements_warm']:5d} warm "
                    f"{r['median_ms']:8.1f} ms {r['kb']:8.1f} KB"
                )
                for q, k in r["top"]:
                    print(f"      {k:4d}  {q}")
                if os.environ.get("SITES"):
                    for n, (sql, where) in enumerate(sites(api.app.state.store, path, token, api)):
                        print(f"   {n:3d} {shape(sql)[:110]}\n         {where}")
            some_ids = [
                t["id"]
                for t in api.get("/api/v2/tasks?brief=1&status=all&limit=40", headers=headers("ana-test")).json()[
                    "tasks"
                ]
            ]
            explain(api, some_ids)
            if out:
                Path(out).write_text(json.dumps(results, indent=1))
        finally:
            install.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
