"""Measures opening a computer's event stream (`GET /api/v2/runners/me/events`) while another writer holds the database's
write lock for HOLD seconds, as on a busy hub: how long until the response starts and until `ready`, for a computer
whose contact stamp is due (each of its streams reconnects every five minutes, the stamp's own interval).

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
E.KEEPALIVE_SECONDS = 0.5


def opened(port, token):
    """(ms until the response started, ms until `ready`)."""
    began = time.perf_counter()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        conn.request("GET", "/api/v2/runners/me/events", headers={"Authorization": "Bearer " + token})
        response = conn.getresponse()
        started = time.perf_counter() - began
        while True:
            line = response.readline()
            if not line or line.startswith(b"event: ready"):
                return started * 1000, (time.perf_counter() - began) * 1000
    finally:
        conn.close()


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
            for busy in (False, True):
                starts, readies = [], []
                for _ in range(RUNS):
                    with store.transaction() as c:  # its contact is due, as when it last wrote five minutes ago
                        c.execute("UPDATE runners SET last_seen_at=NULL WHERE id=?", (computer["runner_id"],))
                    holder = None
                    if busy:
                        holder = sqlite3.connect(str(store.settings.db_path), isolation_level=None)
                        holder.execute("BEGIN IMMEDIATE")
                        threading.Timer(HOLD, holder.rollback).start()
                    start, ready = opened(port, computer["token"])
                    starts.append(start)
                    readies.append(ready)
                    time.sleep(HOLD + 0.2)
                    if holder is not None:
                        holder.close()
                label = f"write lock held {HOLD:.1f}s" if busy else "database idle"
                print(
                    f"{label:22s} response start median {statistics.median(starts):8.1f} ms max {max(starts):8.1f} ms"
                    f" | ready median {statistics.median(readies):8.1f} ms max {max(readies):8.1f} ms"
                )
            with store.read() as c:
                seen = c.execute("SELECT last_seen_at FROM runners WHERE id=?", (computer["runner_id"],)).fetchone()[0]
            print("contact written:", bool(seen))
            server.should_exit = True
            thread.join(timeout=20)
            sock.close()
        finally:
            install.close()
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
