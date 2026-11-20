"""Measures how long the hourly retention sweeps hold the single SQLite writer per write transaction
(store.sweep_idempotency, and sweep_deltas and sweep_tokens, which share its batch size), at the old batch (5000 rows)
and the new one (store.SWEEP_BATCH), on fictional cached request answers past their retry window.

    nice -n 10 .venv/bin/python bench/perf-retention-sweeps.py

Env: ROWS (20000 expired answers, each naming a message so its reverse-index trigger runs on delete).

Per batch size: the write transactions, the hold p50 and max (BEGIN IMMEDIATE to COMMIT), and the total deleted.
Only fictional data, in a temporary folder removed afterwards. Never point it at a real hub.
"""

import dataclasses
import json
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

ROWS = int(os.environ.get("ROWS", 20000))
OLD = "2026-01-01T00:00:00.000000Z"
NOW = "2026-03-01T00:00:00.000000Z"


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


def seed(store):
    response = json.dumps({"id": "m", "sender_id": "human:ana", "body": "Synthetic answer " + "x" * 1500})
    with store.transaction() as c:
        c.executemany(
            "INSERT INTO idempotency(actor,operation,key,request_hash,response,created_at) VALUES(?,?,?,?,?,?)",
            [
                ("human:ana", "/api/v2/messages", f"k{n}", "h", response.replace('"m"', f'"m{n}"'), OLD)
                for n in range(ROWS)
            ],
        )


def main():
    from fastapi.testclient import TestClient

    from backend import store as S
    from backend.app import create_app
    from backend.tests.helpers.api import api_settings

    for label, batch in (("old batch 5000", 5000), (f"new batch {S.SWEEP_BATCH}", S.SWEEP_BATCH)):
        with tempfile.TemporaryDirectory(prefix=".tmp-retention-", dir=Path(__file__).resolve().parents[1]) as d:
            folder = Path(d)
            app = create_app(dataclasses.replace(api_settings(folder), flight_recorder=True))
            try:
                with TestClient(app):
                    store = app.state.store
                    seed(store)
                    meter = Meter(store.recorder)
                    meter.on = True
                    deleted = S.sweep_idempotency(store, NOW, batch=batch)
                    meter.on = False
                    ms = [h[0] for h in meter.holds]
                    st = [h[1] for h in meter.holds]
                    print(
                        f"{label:16s} deleted {deleted:6d} in {len(ms):3d} writes | hold p50 "
                        f"{statistics.median(ms):7.1f} ms max {max(ms):7.1f} ms | statements in lock p50 "
                        f"{statistics.median(st):4.0f} | total held {sum(ms):7.0f} ms"
                    )
                    flight.begin = ORIGINAL_BEGIN  # the next run's meter wraps the real one
            finally:
                shutil.rmtree(folder, ignore_errors=True)


ORIGINAL_BEGIN = flight.begin

if __name__ == "__main__":
    main()
