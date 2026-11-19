"""Measures opening a computer's event stream (`GET /api/v2/runners/me/events`) while another writer holds the database's
write lock for HOLD seconds, as on a busy hub: how long until the response starts and until `ready`, for a computer
whose contact stamp is due (CONTACT_EVERY, ten seconds, has passed since its last), and until that stamp is written,
with the stream still open. Keepalives, which stamp too, are later than all of that here.

    nice -n 10 .venv/bin/python bench/perf-runner-stream-open.py

Env: HOLD (default 1.5 seconds), RUNS (5). Only fictional data, in a temporary folder removed afterwards.
"""

import http.client
import os
import shutil
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend import events as E
from backend.tests.helpers.api import runner
from backend.tests.helpers.api_app import api_client

HOLD = float(os.environ.get("HOLD", 1.5))
RUNS = int(os.environ.get("RUNS", 5))
E.KEEPALIVE_SECONDS = 10  # after the opening stamp: the stamp timed is the opening's own


def opened(port, token):
    """(ms until the response started, ms until the whole `ready` frame, the open connection)."""
    began = time.perf_counter()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request("GET", "/api/v2/runners/me/events", headers={"Authorization": "Bearer " + token})
    response = conn.getresponse()
    started = time.perf_counter() - began
    assert response.status == 200, (response.status, response.read()[:200])
    lines = []
    while not (lines[-2:] and lines[-1] == b"\n" and any(x.startswith(b"event: ready") for x in lines)):
        line = response.readline()
        assert line, "the stream ended before its ready frame"
        lines.append(line)
    return started * 1000, (time.perf_counter() - began) * 1000, conn


def main():
    with tempfile.TemporaryDirectory(prefix=".tmp-runner-open-", dir=Path(__file__).resolve().parents[1]) as folder:
        install = api_client(Path(folder), None)
        api = next(install)
        try:
            store = api.app.state.store
            computer = runner(api, label="A")
            import socket

            import uvicorn

            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(api.app, log_level="error", lifespan="off"))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            thread.start()
            while not server.started:
                time.sleep(0.01)

            def written():
                with store.read() as c:
                    row = c.execute(
                        "SELECT last_seen_at FROM runner_states WHERE runner_id=?", (computer["runner_id"],)
                    )
                    return (row.fetchone() or [None])[0]

            for busy in (False, True):
                starts, readies, stamps = [], [], []
                for _ in range(RUNS):
                    with store.transaction() as c:  # its contact is due, as when it last wrote five minutes ago
                        c.execute(
                            "UPDATE runner_states SET last_seen_at=NULL WHERE runner_id=?", (computer["runner_id"],)
                        )
                    holder = None
                    if busy:
                        holder = sqlite3.connect(
                            str(store.settings.db_path), isolation_level=None, check_same_thread=False
                        )
                        holder.execute("BEGIN IMMEDIATE")
                        threading.Timer(HOLD, holder.rollback).start()
                    began = time.perf_counter()
                    start, ready, conn = opened(port, computer["token"])
                    starts.append(start)
                    readies.append(ready)
                    while not written() and time.perf_counter() - began < 30:  # the stream still open
                        time.sleep(0.02)
                    assert written(), "the open stream never wrote its contact"
                    stamps.append((time.perf_counter() - began) * 1000)
                    conn.close()
                    time.sleep(HOLD + 0.2)
                    if holder is not None:
                        holder.close()
                label = f"write lock held {HOLD:.1f}s" if busy else "database idle"
                print(
                    f"{label:22s} response start median {statistics.median(starts):8.1f} ms max {max(starts):8.1f} ms"
                    f" | ready median {statistics.median(readies):8.1f} ms max {max(readies):8.1f} ms"
                    f" | contact written after median {statistics.median(stamps):8.1f} ms"
                )
            server.should_exit = True
            thread.join(timeout=20)
            sock.close()
        finally:
            install.close()
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
