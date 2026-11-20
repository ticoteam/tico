"""Measures the Heartbeat's never-stuck step (backend/never_stuck.py, scheduler.step_never_stuck): how long it holds
the single SQLite writer and how many statements it runs inside it, on perf-hub-overload's fictional company with the
hosted TIDY company's open work (Nov 19: 1,150 open tasks on the engine, 27 of 3,037 passes doing anything, each
pass holding the write lock about 0.4 s on a quiet host).

    nice -n 10 .venv/bin/python bench/perf-never-stuck.py

Env: OPEN (default 1150 open tasks), CLOSED (4000), THREADS (100), RUNS (10).

It runs passes at one fixed time until one does nothing, then measures RUNS more: a pass with nothing to do (nearly
every one), and a pass with one due snooze to end (one write). Per pass: the step's wall time, the write-lock hold
(BEGIN IMMEDIATE to COMMIT) and the statements run inside it. Only fictional data, in a temporary folder removed
afterwards. Never point it at a real hub.
"""

import importlib.util
import os
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("OPEN", "1150")
os.environ.setdefault("CLOSED", "4000")
os.environ.setdefault("THREADS", "100")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_spec = importlib.util.spec_from_file_location("overload", Path(__file__).with_name("perf-hub-overload.py"))
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)  # imports conftest: the fictional registry and isolated benchmark settings

from backend import flight
from backend.store import H

RUNS = int(os.environ.get("RUNS", 10))


class Meter:
    """Write-lock holds (ms) and the statements run inside each, while `on`."""

    def __init__(self, recorder):
        self.on, self.local, self.holds = False, threading.local(), []
        query, begin = recorder.query, flight.begin

        def counted(sql, ms, locked=False):
            if self.on and getattr(self.local, "held", None) is not None:
                self.local.held[1] += 1
            return query(sql, ms, locked)

        def timed(conn, rec=None, **kwargs):
            done = begin(conn, rec, **kwargs)
            self.local.held = [time.perf_counter(), 0]

            def finished():
                started, statements = self.local.held
                self.local.held = None
                if self.on:
                    self.holds.append(((time.perf_counter() - started) * 1000, statements))
                return done()

            return finished

        recorder.query, flight.begin = counted, timed


def passes(api, meter, at, label, before=None):
    from backend import heartbeat as HB
    from backend import scheduler as S
    from backend.scheduler import Scheduler

    scheduler = Scheduler(api.app.state.store, api.app.state.execution)
    walls, holds, statements, words = [], [], [], set()
    for _ in range(RUNS):
        if before:
            before()
        ctx = HB.Context(scheduler, at)
        meter.holds, meter.on = [], True
        t = time.perf_counter()
        words.add(S.step_never_stuck(ctx)[0])
        walls.append((time.perf_counter() - t) * 1000)
        meter.on = False
        holds.append(sum(h[0] for h in meter.holds))
        statements.append(sum(h[1] for h in meter.holds))
    print(
        f"  {label:34s} wall p50 {statistics.median(walls):7.1f} ms | write-lock hold p50 "
        f"{statistics.median(holds):7.1f} ms max {max(holds):7.1f} ms | statements in lock p50 "
        f"{statistics.median(statements):6.0f} | {sorted(words)}"
    )


def main():
    import shutil
    import tempfile

    import dataclasses

    from backend.tests.helpers import api_app

    plain = api_app.api_settings
    api_app.api_settings = lambda folder: dataclasses.replace(plain(folder), flight_recorder=True)
    with tempfile.TemporaryDirectory(prefix=".tmp-never-stuck-", dir=Path(__file__).resolve().parents[1]) as d:
        folder = Path(d)
        install = api_app.api_client(folder, None)
        api = next(install)
        try:
            t = time.perf_counter()
            B.seed(api)
            store = api.app.state.store
            with store.read() as c:
                open_tasks = c.execute(
                    "SELECT count(*) FROM tasks WHERE status='open' AND pipeline_id IS NOT NULL"
                ).fetchone()[0]
            print(f"seeded in {time.perf_counter() - t:.1f}s: {open_tasks} open tasks on the engine")
            meter = Meter(store.recorder)
            at = datetime.now(timezone.utc)
            from backend import heartbeat as HB
            from backend import scheduler as S
            from backend.scheduler import Scheduler

            scheduler = Scheduler(store, api.app.state.execution)
            for _ in range(20):  # the first passes wake whoever has a turn: run until one does nothing
                if S.step_never_stuck(HB.Context(scheduler, at))[1]:
                    break
            passes(api, meter, at, "a pass with nothing to do")
            with store.read() as c:
                victim = c.execute(
                    "SELECT t.id, v.id FROM tasks t JOIN visits v ON v.task_id=t.id AND v.left_at IS NULL "
                    "WHERE t.status='open' AND t.pipeline_id IS NOT NULL ORDER BY v.entered_at DESC LIMIT 1"
                ).fetchone()

            def due_snooze():
                with store.transaction() as c:
                    c.execute(
                        "INSERT INTO snoozes(id,task_id,visit_id,by_id,until_at,created_at) VALUES(?,?,?,?,?,?)",
                        (H.new_id(), victim[0], victim[1], "human:ana", "2000-01-01T00:00:00.000000Z", H.now()),
                    )

            passes(api, meter, at, "a pass ending one due snooze", before=due_snooze)
        finally:
            install.close()
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
