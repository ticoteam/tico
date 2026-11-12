"""The gate's steps: serve the company, send the levels, read the server's flight recorder for each steady window, and
write the report. `__main__` is the command line around them; the smoke test (backend/tests/test_load_gate.py) calls
them with an in-process server."""

import contextlib
import json
import math
import os
import platform
import socket
import subprocess
import time
from pathlib import Path

from . import report as R, sender as S

ROOT = Path(__file__).resolve().parents[2]
LEVELS = (30, 60, 120, 300)


def commit(root=ROOT):
    """This checkout's commit and whether its tracked files differ from it."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True)
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "-uno"], cwd=root, capture_output=True, text=True, check=False
        )
        return head.stdout.strip(), bool(dirty.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return "", True


def host():
    """What the report says about a machine: name, CPUs, memory."""
    try:
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError):
        memory = None
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    return {"name": socket.gethostname(), "cpus": cpus, "memory_bytes": memory, "platform": platform.platform()}


def window(ramp, hold, lead=3.0, align=True):
    """(start, steady since, steady until): the steady window starts on a minute boundary when `align`, so the flight
    recorder's minutes cover it and nothing of the ramp."""
    since = time.time() + lead + ramp
    if align:
        since = math.ceil(since / 60) * 60
        hold = max(60, round(hold / 60) * 60)
    return since - ramp, since, since + hold


def run_levels(target, manifest, levels, ramp, hold, workers, seed, folder, align=True, starve_ms=0, grace=5, rest=0):
    """Send each level in turn; returns [(level, logs, since, until)]."""
    runs = []
    for level in levels:
        start, since, until = window(ramp, hold, align=align)
        logs = S.send(target, manifest, level, workers, start, ramp, until - since, seed, str(folder), starve_ms, grace)
        runs.append((level, logs, since, until))
        if rest:
            time.sleep(rest)  # the server settles between levels: what one level left queued is not the next one's load
    return runs


def http_flight(target, admin_token, wait=True):
    """A reader of the server's flight recorder over HTTP (GET /api/v2/system/metrics with a window). The recorder
    writes once a minute: with `wait`, the reader first waits for the minute that holds `until` to be written."""
    import httpx

    def read(since, until):
        flush = math.ceil(until / 60) * 60 + 5
        if wait and time.time() < flush:
            time.sleep(flush - time.time())
        r = httpx.get(
            target.rstrip("/") + "/api/v2/system/metrics",
            params={
                "section": "requests,process,events",
                "since": int(since // 60 * 60),
                "until": int(math.ceil(until / 60) * 60),
                "routes": 500,
            },
            headers={"Authorization": "Bearer " + admin_token},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    return read


def db_flight(db_path):
    """A reader of a stopped server's database: the recorder wrote its last minute when the server stopped."""
    import sqlite3
    from contextlib import closing

    from backend import flight

    def read(since, until):
        with closing(sqlite3.connect(Path(db_path).as_uri() + "?mode=ro", uri=True)) as c:
            c.row_factory = sqlite3.Row
            return flight.view(
                c,
                sections=("requests", "process", "events"),
                since=int(since // 60 * 60),
                until=int(math.ceil(until / 60) * 60),
                routes=500,
            )

    return read


def assemble(runs, read, manifest, meta, budget=None):
    """The whole report: the run's head and one section per level."""
    levels, server_commit = [], None
    for level, logs, since, until in runs:
        try:
            flight = read(since, until)
        except Exception as exc:  # noqa: BLE001  the report still says what the sender saw, and that the server side is missing
            flight = None
            meta.setdefault("problems", []).append(f"level {level}: flight recorder not read ({type(exc).__name__})")
        start = ((flight or {}).get("events") or {}).get("start") or {}
        server_commit = server_commit or start.get("commit")
        section = R.level_report(level, logs, flight, since, until, budget)
        section["window"]["aligned"] = since % 60 == 0 and until % 60 == 0
        levels.append(section)
    head = {
        **meta,
        "seed": manifest["seed"],
        "sizes": manifest["sizes"],
        "rows": manifest.get("rows", {}),
        "build_seconds": manifest.get("build_seconds"),
        "company_built_at_commit": manifest.get("built_at_commit"),
        "server_commit": server_commit or meta.get("server_commit"),
        "plan": S.plan_json(manifest),
        "limits": {
            "lateness_p95_ms": R.LATENESS_P95_MS,
            "lateness_max_ms": R.LATENESS_MAX_MS,
            "sender_cpu_share": R.SENDER_CPU_SHARE,
            "loop_lag_p95_ms": R.LOOP_LAG_P95_MS,
        },
    }
    head["pinned"] = bool(head["commit"]) and not head.get("dirty") and head["commit"] == head["server_commit"]
    return {"run": head, "levels": levels}


def loopback_only():
    """The server resolves no name but loopback, so nothing it does can reach a provider, Tico HQ or any shared system
    (AC-8); inbound connections from the sender need no lookup. As backend/tests/live.py block_outside_network."""
    real = socket.getaddrinfo

    def local_only(name, *args, **kwargs):
        if name in ("localhost", "127.0.0.1", "::1", "0.0.0.0", None):
            return real(name, *args, **kwargs)
        raise socket.gaierror(-2, "the load gate's server reaches no outside host")

    socket.getaddrinfo = local_only
    return real


@contextlib.contextmanager
def serve(folder, db, manifest, listen, scheduler, announce):
    """Serve the company from a copy of its database on `listen` (host:port) until stdin closes or the process is
    told to stop; `announce(info)` is called once the server answers, with the manifest and the URL."""
    import threading

    import uvicorn
    from fastapi.testclient import TestClient

    from backend.app import create_app

    from . import company

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    company.copy(Path(db), folder)
    loopback_only()
    head, dirty = commit()
    settings = company.settings(folder, scheduler=scheduler, release_commit=head)
    app = create_app(settings)
    company.identities(manifest, settings)
    hostname, _, port = listen.rpartition(":")
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((hostname or "127.0.0.1", int(port)))
    with TestClient(app):  # the app's own start: scheduler, flight recorder
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 60
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not server.started:
            raise SystemExit("the gate's server did not start within 60 seconds")
        url = f"http://{hostname or '127.0.0.1'}:{sock.getsockname()[1]}"
        announce(
            {
                "url": url,
                "manifest": manifest,
                "commit": head,
                "dirty": dirty,
                "host": host(),
                "db": str(folder / "hub.db"),
            }
        )
        try:
            yield url
        finally:
            server.should_exit = True
            thread.join(timeout=30)
            sock.close()


def load_budget(path):
    return json.loads(Path(path).read_text()) if path else None
