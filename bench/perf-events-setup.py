"""Measures opening `GET /api/v2/events` (a person's live-update stream) on perf-holds-pages' fictional company at the
hosted TIDY company's size: how long until the response starts (what the flight recorder times) and until `ready`
(which carries a `needs=changes` stream's Needs-you version), and the SQL statements it takes, for one stream and for
ten streams of one person opening at once, as after a release when every client reconnects.

    nice -n 10 .venv/bin/python bench/perf-events-setup.py

Env as bench/perf-hub-overload.py (OPEN, CLOSED, THREADS, DB), plus FRAMES=<file>: each probe's stream text, from the
response's first byte through its catch-up to the change number `ready` names, as JSON, to compare two checkouts' on
one DB (DB=..., seeded first with SEED_ONLY=1). Only fictional data. Never point it at a real hub.
"""

import http.client
import importlib.util
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_spec = importlib.util.spec_from_file_location("holds_pages", Path(__file__).with_name("perf-holds-pages.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)  # imports conftest: the fictional registry and isolated benchmark settings
B = P.B

from backend import events as E

E.KEEPALIVE_SECONDS = 0.5  # a closed probe is noticed within a second
READERS = ("ana-test", "ben-test", "cara-test")
QUIET = E.__dict__.get("BASELINE_REUSE_SECONDS", 0) + 1  # between measures, so none reuses another's baseline


class Probe:
    """One stream: when its response started and when `ready` came, and its text through the catch-up."""

    def __init__(self, base, token, query):
        self.base, self.token, self.query = base, token, query
        self.headers_at = self.ready_at = None
        self.text, self.error = "", None

    def run(self, began):
        url = urlparse(self.base)
        conn = http.client.HTTPConnection(url.hostname, url.port, timeout=120)
        try:
            conn.request("GET", "/api/v2/events" + self.query, headers={"Authorization": "Bearer " + self.token})
            response = conn.getresponse()
            self.headers_at = time.perf_counter() - began
            if response.status != 200:
                self.error = f"{response.status} {response.read()[:200]!r}"
                return
            target, lines = None, []
            while True:
                line = response.readline().decode()
                if not line:
                    return
                lines.append(line)
                self.text = "".join(lines)
                if line.startswith("data: ") and lines[-2:-1] == ["event: ready\n"]:
                    self.ready_at = time.perf_counter() - began
                    target = json.loads(line[6:])["seq"]
                    if "after=" not in self.query:
                        return
                elif line.startswith("id: ") and target is not None and int(line[4:]) >= target:
                    while lines[-1] != "\n":  # the rest of that frame
                        lines.append(response.readline().decode())
                    self.text = "".join(lines)
                    return
        finally:
            conn.close()


def burst(base, store, token, query, n):
    """`n` streams of `token` opened at once: (probes, statements until each had its `ready` or catch-up)."""
    seen = []
    probes = [Probe(base, token, query) for _ in range(n)]
    gate = threading.Barrier(n)
    began = [0.0]

    def go(probe):
        gate.wait()
        if not began[0]:
            began[0] = time.perf_counter()
        probe.run(began[0])

    with B.counting(store, seen):
        threads = [threading.Thread(target=go, args=(p,)) for p in probes]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    for p in probes:
        assert p.error is None, p.error
    return probes, len(seen)


def summary(probes, statements):
    heads = [p.headers_at * 1000 for p in probes]
    ready = [p.ready_at * 1000 for p in probes]
    return (
        f"response start median {statistics.median(heads):8.1f} ms max {max(heads):8.1f} ms | "
        f"ready median {statistics.median(ready):8.1f} ms max {max(ready):8.1f} ms | {statements} statements"
    )


def report(api):
    if os.environ.get("SEED_ONLY"):  # DB=... seeded and saved: each checkout then measures its own copy of it
        return
    store = api.app.state.store
    with store.read() as c:
        newest = E.latest(c)
        counts = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("bots", "tasks", "changes")}
    print(counts, "newest change", newest)
    frames = {}
    for token in READERS:
        listed = api.get("/api/v2/needs-you?limit=1", headers=B.headers(token)).json()
        print(f"{token}: {listed.get('count')} Needs-you items")
    with _serving(api.app) as base:
        # Equality probes first, while the database is as seeded.
        for token in READERS:
            for query in (
                f"?needs=changes&after={newest - 300}",
                f"?after={newest - 300}",
                f"?topics=needs,tasks,threads&needs=changes&after={newest - 1200}",
                "?needs=changes",
            ):
                probes, _ = burst(base, store, token, query, 1)
                frames[f"{token} {query}"] = probes[0].text
                time.sleep(QUIET)
        for token in ("ana-test", "ben-test"):
            for query in ("?needs=changes", f"?needs=changes&after={newest - 50}", "?needs=list"):
                for n in (1, 10):
                    time.sleep(QUIET)
                    probes, statements = burst(base, store, token, query, n)
                    print(f"{token:9s} {query:24s} x{n:<2d} {summary(probes, statements)}")
            # Ten more right after ten: a second wave inside the reuse window (a release's reconnects in waves).
            probes, statements = burst(base, store, token, "?needs=changes", 10)
            print(f"{token:9s} {'?needs=changes (again)':24s} x10 {summary(probes, statements)}")
    if os.environ.get("FRAMES"):
        Path(os.environ["FRAMES"]).write_text(json.dumps(frames, indent=1, sort_keys=True))


def _serving(app):
    """The app on a free local port: yields its base URL."""
    import contextlib
    import socket

    import uvicorn

    @contextlib.contextmanager
    def serve():
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 30
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=20)
            sock.close()

    return serve()


B.report = report

if __name__ == "__main__":
    B.main()
