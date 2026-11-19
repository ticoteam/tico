"""Measures the two polls every computer sends (Nov 19 TIDY flight recorder: POST /api/v2/runners/heartbeat 669 an hour,
avg 558 ms, slow samples 48 queries; POST /api/v2/runners/performance 459 an hour, ~27 queries) on a fictional company
at TIDY's size: RUNNERS computers sharing 165 bots, a few hundred open tasks with a worktree each, no turn
running (the provenance walk for live turns runs on the read snapshot, before the write lock, and is not measured here).

    nice -n 10 .venv/bin/python bench/perf-runner-polls.py

Env: RUNS (default 40 beats per route), RUNNERS (10), WORKTREES (300), SHAPES=1 (the statement shapes one call runs
inside its write transaction, with their count and time).

Per route it prints the median and p90 wall time of a request, its SQL time and statement count, and the write
transaction's lock hold (BEGIN IMMEDIATE to COMMIT) with the statements run inside it. Only fictional data, in a temporary
folder removed afterwards. Never point it at a real hub.
"""

import collections
import contextlib
import dataclasses
import json
import os
import shutil
import statistics
import sys
import threading
import time
import uuid
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend import flight
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import post

BOTS = [f"bot{n:03d}" for n in range(165)]
RUNS = int(os.environ.get("RUNS", 40))
RUNNERS = int(os.environ.get("RUNNERS", 10))
WORKTREES = int(os.environ.get("WORKTREES", 300))
CAPABILITIES = ["screen_v1", "turns_at_once_v1", "browser_exit_v1", "model_subscriptions_v1", "plan_usage_v1"]
SHA = "a" * 40


def seed(api):
    """RUNNERS enrolled computers, the bots spread over them, one open task with a present worktree per WORKTREES."""
    store = api.app.state.store
    computers = []
    for n in range(RUNNERS):
        code = post(api, "enrollments", {"operator": "ana"})["code"]
        computers.append(post(api, "runners/enroll", {"code": code, "label": f"box{n}", "platform": "linux"}))
    with store.transaction() as c:
        for n, slug in enumerate(BOTS):
            X.bot(c, slug, computer=False)
            rid = computers[n % RUNNERS]["runner_id"]
            c.execute("INSERT OR REPLACE INTO assignments VALUES(?,?,1,?,'human:ana')", (slug, rid, H.now()))
            if n % 20 == 0:  # a few bots told their plan was unavailable on another computer (kept, never cleared here)
                c.execute(
                    "INSERT INTO registry_metadata VALUES(?,?)",
                    (f"subscription-unavailable:{slug}", json.dumps({"runner_id": "elsewhere", "profile": "default"})),
                )
    links = collections.defaultdict(list)
    with store.transaction() as c:
        for k in range(WORKTREES):
            slug = BOTS[k % len(BOTS)]
            rid = computers[BOTS.index(slug) % RUNNERS]["runner_id"]
            task = X.new_task(c, X.ANA, f"Worktree task {k}", assignee="bot:" + slug)
            lid, now = H.new_id(), H.now()
            detail = {
                "owner": "bot:" + slug,
                "checkout_state": "ready",
                "expected_head": SHA,
                "checkout_target": "origin/main",
                "expected_base": "b" * 64,
                "last_commit": SHA,
                "dirty_files": 0,
                "last_activity": 1_700_000_000.0,
                "activity_at": now,
            }
            branch = f"tico/{task['id'][:8]}-work"
            c.execute(
                "INSERT INTO task_links(id,task_id,kind,url,state,added_by,created_at,repo,branch,computer_id,path,"
                "detail,updated_at) VALUES(?,?,'worktree','',?,?,?,?,?,?,?,?,?)",
                (
                    lid,
                    task["id"],
                    "present",
                    "bot:" + slug,
                    now,
                    "acme/product",
                    branch,
                    rid,
                    f"wt/{k}",
                    json.dumps(detail),
                    now,
                ),
            )
            links[rid].append((lid, branch))
    return computers, links


def heartbeat_body(api, computer, links):
    with api.app.state.store.read() as c:
        hosted = [r[0] for r in c.execute("SELECT bot FROM assignments WHERE runner_id=?", (computer["runner_id"],))]
    readiness = {
        "schema_version": 1,
        "worktrees": True,
        "worktree_waits": True,
        "runtimes": {"claude": {"installed": True, "authenticated": "ready", "version": "2.1.0"}},
        "bots": {
            bot: {"ready": True, "runtime": "claude", "model": "opus", "configuration_valid": True} for bot in hosted
        },
    }
    worktrees = [
        {
            "link_id": lid,
            "repo": "acme/product",
            "state": "present",
            "branch": branch,
            "checkout_state": "ready",
            "expected_head": SHA,
            "checkout_target": "origin/main",
            "expected_base": "b" * 64,
            "last_commit": SHA,
            "dirty_files": 0,
            "last_activity": 1_700_000_000.0,
        }
        for lid, branch in links
    ]
    return {
        "version": "0.6.115",
        "platform": "linux",
        "capacity": 8,
        "capabilities": CAPABILITIES,
        "readiness": readiness,
        "worktrees": worktrees,
        "repositories": [{"full_name": "acme/product", "state": "cloned", "size_mb": 120}],
        "profiles": [{"name": "default", "runtimes": {"claude": {"signed_in": True}}}],
        "release": "0.6.115",
    }


def performance_body(at):
    return {
        "samples": [
            {
                "at": at - 30 * k,
                "cpu": 35.0,
                "cores": 8,
                "load": [2.0, 2.0, 2.0],
                "mem_total": 16_000,
                "mem_used": 8_000,
                "disk": [{"volume": "state", "total_bytes": 100, "free_bytes": 50}],
                "active": 3,
                "procs": [{"name": f"proc{p}", "cpu": 5.0, "mem": 100, "count": 1} for p in range(6)],
            }
            for k in (1, 0)
        ]
    }


class Meter:
    """Statements (shape, ms, inside the write lock) and write-lock holds of the calls made while `on`."""

    def __init__(self, recorder):
        self.recorder, self.on, self.local = recorder, False, threading.local()
        self.queries, self.holds = [], []
        query, begin = recorder.query, flight.begin

        def counted(sql, ms, locked=False):
            if self.on:
                # The text as given: fingerprinted after the request, outside the timed call and lock hold.
                self.queries.append((sql, ms, getattr(self.local, "held", False)))
            return query(sql, ms, locked)

        def timed(conn, rec=None, **kwargs):
            done = begin(conn, rec, **kwargs)
            held = time.perf_counter()
            self.local.held = True

            def finished():
                self.local.held = False
                if self.on:
                    self.holds.append((time.perf_counter() - held) * 1000)
                return done()

            return finished

        recorder.query, flight.begin = counted, timed

    @contextlib.contextmanager
    def call(self):
        self.queries, self.holds, self.on = [], [], True
        try:
            yield self
        finally:
            self.on = False


def measure(api, meter, name, send):
    walls, sql, counts, holds, locked_counts, shapes = [], [], [], [], [], collections.Counter()
    shape_ms = collections.Counter()
    for n in range(RUNS):
        with meter.call():
            t = time.perf_counter()
            r = send(n)
            walls.append((time.perf_counter() - t) * 1000)
        assert r.status_code == 200, r.text[:300]
        sql.append(sum(ms for _, ms, _ in meter.queries))
        counts.append(len(meter.queries))
        holds.append(sum(meter.holds))
        locked_counts.append(sum(1 for *_, inside in meter.queries if inside))
        for sql_text, ms, inside in meter.queries:
            shape = flight.fingerprint(sql_text)
            shapes[(inside, shape)] += 1
            shape_ms[(inside, shape)] += ms
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(len(xs) * p))]
    print(
        f"{name:12s} wall p50 {statistics.median(walls):7.1f} ms p90 {q(walls, 0.9):7.1f} | "
        f"sql p50 {statistics.median(sql):6.1f} ms | queries {statistics.median(counts):5.0f} "
        f"(in lock {statistics.median(locked_counts):4.0f}) | lock hold p50 {statistics.median(holds):6.1f} ms "
        f"p90 {q(holds, 0.9):6.1f} ms | transactions {len(meter.holds)}"
    )
    if os.environ.get("SHAPES"):
        for (inside, shape), k in sorted(shapes.items(), key=lambda kv: -shape_ms[kv[0]])[:40]:
            print(
                f"   {'LOCK' if inside else 'read'} {k / RUNS:5.1f}x {shape_ms[(inside, shape)] / RUNS:7.2f} ms  {shape[:170]}"
            )


def explain(store, computers):
    """The plans of the reads this change folds together: per hosted bot, and all of a computer's bots at once."""
    with store.read() as c:
        hosted = [
            r[0] for r in c.execute("SELECT bot FROM assignments WHERE runner_id=?", (computers[0]["runner_id"],))
        ]
        keys = ["subscription-unavailable:" + bot for bot in hosted]
        for label, sql, args in (
            ("old, once per bot", "SELECT value FROM registry_metadata WHERE key=?", keys[:1]),
            (
                "new, once a beat",
                f"SELECT key,value FROM registry_metadata WHERE key IN ({','.join('?' * len(keys))})",
                keys,
            ),
        ):
            plan = " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, args))
            t = time.perf_counter()
            for _ in range(200):
                if label.startswith("old"):
                    for key in keys:
                        c.execute(sql, (key,)).fetchall()
                else:
                    c.execute(sql, keys).fetchall()
            print(f"{label:18s} {plan} | {(time.perf_counter() - t) * 1000 / 200:.3f} ms for {len(keys)} bots")


def main():
    import tempfile

    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.tests.helpers.api import api_settings
    from backend.tests.helpers.company import seed as company

    with tempfile.TemporaryDirectory(prefix=".tmp-runner-polls-", dir=Path(__file__).resolve().parents[1]) as directory:
        folder = Path(directory)
        settings = dataclasses.replace(api_settings(folder), flight_recorder=True)
        app = create_app(settings)
        try:
            with TestClient(app) as api:
                with app.state.store.transaction() as c:
                    company(c)
                t = time.perf_counter()
                computers, links = seed(api)
                print(
                    f"seeded in {time.perf_counter() - t:.1f}s: {RUNNERS} computers, {len(BOTS)} bots, {WORKTREES} worktrees"
                )
                meter = Meter(app.state.store.recorder)
                bodies = [heartbeat_body(api, r, links[r["runner_id"]]) for r in computers]

                def beat(n):
                    r = computers[n % len(computers)]
                    return api.post(
                        "/api/v2/runners/heartbeat",
                        json=bodies[n % len(computers)],
                        headers={"Authorization": "Bearer " + r["token"], "Idempotency-Key": str(uuid.uuid4())},
                    )

                start = int(time.time()) - 3600

                def perf(n):
                    r = computers[n % len(computers)]
                    return api.post(
                        "/api/v2/runners/performance",
                        json=performance_body(start + 60 * (n // len(computers))),
                        headers={"Authorization": "Bearer " + r["token"], "Idempotency-Key": str(uuid.uuid4())},
                    )

                for n in range(len(computers)):  # each computer's first beat stores its report; later ones repeat it
                    beat(n), perf(n)
                explain(app.state.store, computers)
                measure(api, meter, "heartbeat", beat)
                measure(api, meter, "performance", lambda n: perf(n + len(computers)))
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
