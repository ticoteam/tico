"""Measures the GitHub webhook's push to a default branch (github.tell_pushed): how long it holds the single SQLite
writer and how many statements it runs inside it, on a fictional fleet at the hosted TIDY company's size (Nov 19: 13
computers, about 170 bots, 6,447 webhooks in three hours).

    nice -n 10 .venv/bin/python bench/perf-push-hints.py

Env: RUNNERS (10), BOTS (17 per computer), RUNS (20).

Every computer takes push hints, and every bot works on acme/product: each push names every bot. Only fictional data,
in a temporary folder removed afterwards. Never point it at a real hub.
"""

import dataclasses
import os
import shutil
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend import flight

RUNNERS = int(os.environ.get("RUNNERS", 10))
BOTS = int(os.environ.get("BOTS", 17))
RUNS = int(os.environ.get("RUNS", 20))


class Meter:
    """Write-lock holds: (ms, statements inside) while `on`."""

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


def seed(api):
    from backend import bot_settings as BS
    from backend.github import PUSH_HINTS
    from backend.store import H
    from backend.tests.helpers.api import assign, ready, runner
    from backend.tests.helpers.claim_statements import CAPABILITIES

    store = api.app.state.store
    for n in range(RUNNERS):
        slugs = [f"c{n}-{k:02d}" for k in range(BOTS)]
        with store.transaction() as c:
            H.sync_registry(
                c,
                {s: {"name": s, "runtime": "fake", "status": "active"} for s in slugs},
                {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben")]},
            )
            for s in slugs:
                c.execute("INSERT INTO bot_config(bot,owner_id,repo) VALUES(?,'ana','acme/product')", (s,))
                BS.write(c, s, {"name": s, "runtime": "fake"})
        machine = runner(api, label=f"Box {n}")
        for s in slugs:
            assign(api, machine, s)
        ready(api, machine, slugs, [*CAPABILITIES, PUSH_HINTS])


def main():
    from backend import github
    from backend.tests.helpers import api_app

    plain = api_app.api_settings
    api_app.api_settings = lambda folder: dataclasses.replace(plain(folder), flight_recorder=True)
    with tempfile.TemporaryDirectory(prefix=".tmp-push-hints-", dir=Path(__file__).resolve().parents[1]) as d:
        folder = Path(d)
        install = api_app.api_client(folder, None)
        api = next(install)
        try:
            seed(api)
            store = api.app.state.store
            meter = Meter(store.recorder)
            payload = {"ref": "refs/heads/main", "repository": {"full_name": "acme/product", "default_branch": "main"}}
            told = []
            for _ in range(RUNS):
                meter.on = True
                with store.transaction() as c:
                    told.append(len(github.tell_pushed(c, store.settings, payload)))
                meter.on = False
            ms, st = [h[0] for h in meter.holds], [h[1] for h in meter.holds]
            print(
                f"push to main, {RUNNERS * BOTS} bots: told p50 {statistics.median(told):.0f} | write-lock hold p50 "
                f"{statistics.median(ms):6.1f} ms max {max(ms):6.1f} ms | statements in lock p50 {statistics.median(st):5.0f}"
            )
        finally:
            install.close()
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
