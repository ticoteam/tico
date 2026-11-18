"""Measure 3 of Needs you per person (I39, AC-3): many readers opening Needs you at once against 1,500 open tasks.

    .venv/bin/python bench/needs-you-load.py [--seconds 60] [--readers 50] [--rate 10] [--path /api/v2/needs-you]
        [--shape tidy] [--walk --path '/api/v2/needs-you?limit=50']

It builds the bench's synthetic company (bench/needs-you-per-person.py: Ben has 6 items, Ana is the company's person
with about 150; `--shape tidy` the hosted company's shape, Ana with about 400) in a new temporary database, serves it with one uvicorn process, as production serves, and sends from
this process, so the sender's own work is not the server's. Each reader asks again `1/rate` s after its last ask was
sent, or as soon as its answer came if that took longer (a page waits for its answer before it asks again); 45 of 50
read as Ben and the rest as Ana, as the Performance Owner's run on rc.12 did. A request that has no answer in 20 s
(the app's client timeout) counts as timed out. It prints what was served, the rate, timeouts and errors, and the
p50 / p95 / p99 of the answer time; `--path /api/v2/me` gives the control route on the same server and load.

Only fictional data, in a temporary folder that is removed afterwards. Never point it at a real hub.
"""

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_S = 20.0


def serve(folder, port, open_tasks, shape="bench"):
    """The child process: build the company, then serve it until stdin closes."""
    import importlib.util
    import socket
    import threading

    import uvicorn
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.tests.helpers.api import api_settings
    from backend.tests.helpers.company import seed

    spec = importlib.util.spec_from_file_location("needs_bench", ROOT / "bench" / "needs-you-per-person.py")
    assert spec and spec.loader
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    settings = api_settings(Path(folder))
    app = create_app(settings)
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    with TestClient(app):
        with app.state.store.transaction() as c:
            seed(c)
        if shape == "tidy":
            from backend.blobs import Blobs

            app.state.store.blobs = getattr(app.state.store, "blobs", None) or Blobs(app.state.store.settings)
            bench.seed_tidy(app.state.store, open_tasks)
        else:
            bench.seed(app.state.store, open_tasks)
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        while not server.started:
            time.sleep(0.05)
        print(json.dumps({"url": f"http://127.0.0.1:{sock.getsockname()[1]}"}), flush=True)
        sys.stdin.read()
        server.should_exit = True
        thread.join(timeout=30)


async def reader(client, path, token, rate, until, out, walk=False):
    """Ask `path` again and again. `walk`: each ask is a page (`path` has `limit`) and the reader follows its `next`
    to the end of the list, as the app pages through Needs you; each page is timed on its own."""
    gap = 1.0 / rate
    while (sent := time.monotonic()) < until:
        url = path
        while url:
            asked = time.monotonic()
            try:
                response = await client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT_S)
                out["ok" if response.status_code == 200 else "errors"] += 1
                out["times"].append(time.monotonic() - asked)
                after = response.json().get("next") if walk and response.status_code == 200 else None
            except Exception as error:  # noqa: BLE001 - a timeout or a dropped connection is what we count
                out["timeouts" if "Timeout" in type(error).__name__ else "errors"] += 1
                after = None
            url = f"{path}&cursor={after}" if after else None
        await asyncio.sleep(max(0.0, sent + gap - time.monotonic()))


def summary(path, readers, rate, elapsed, out):
    times = sorted(out["times"])

    def pct(p):
        return times[min(len(times) - 1, int(p * len(times)))] * 1000 if times else float("nan")

    return {
        "path": path,
        "readers": readers,
        "rate_each": rate,
        "seconds": round(elapsed, 1),
        "served": out["ok"],
        "per_second": round(out["ok"] / elapsed, 1),
        "timeouts": out["timeouts"],
        "errors": out["errors"],
        "p50_ms": round(statistics.median(times) * 1000, 1) if times else None,
        "p95_ms": round(pct(0.95), 1),
        "p99_ms": round(pct(0.99), 1),
    }


async def send(url, path, readers, rate, seconds, ana_readers, badge_readers=0, walk=False):
    import httpx

    out = {"ok": 0, "errors": 0, "timeouts": 0, "times": []}
    badge = {"ok": 0, "errors": 0, "timeouts": 0, "times": []}
    limits = httpx.Limits(max_connections=readers + badge_readers, max_keepalive_connections=readers + badge_readers)
    async with httpx.AsyncClient(base_url=url, limits=limits) as client:
        # Warm the route once per person before the clock starts.
        for token in ("ben-test", "ana-test"):
            await client.get(path, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        start = time.monotonic()
        until = start + seconds
        tokens = ["ana-test" if n < ana_readers else "ben-test" for n in range(readers)]
        # The Tasks badge (`count=1`) beside them, as Ben, timed on its own.
        badges = [
            reader(client, "/api/v2/needs-you?count=1", "ben-test", rate, until, badge) for _ in range(badge_readers)
        ]
        await asyncio.gather(*(reader(client, path, t, rate, until, out, walk) for t in tokens), *badges)
        elapsed = time.monotonic() - start
    found = [summary(path, readers, rate, elapsed, out)]
    if badge_readers:
        found.append(summary("/api/v2/needs-you?count=1", badge_readers, rate, elapsed, badge))
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--readers", type=int, default=50)
    parser.add_argument("--ana-readers", type=int, default=5)
    parser.add_argument("--rate", type=float, default=10)
    parser.add_argument("--badge-readers", type=int, default=0, help="more readers, each reading the Tasks badge")
    parser.add_argument("--open-tasks", type=int, default=1500)
    parser.add_argument("--path", action="append", help="route(s) to send, in turn (default /api/v2/needs-you)")
    parser.add_argument(
        "--shape", choices=("bench", "tidy"), default="bench", help="tidy: the hosted company's shape (seed_tidy)"
    )
    parser.add_argument("--walk", action="store_true", help="follow each page's `next` (a path with limit=<n>)")
    parser.add_argument("--serve", nargs=2, metavar=("FOLDER", "PORT"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve[0], int(args.serve[1]), args.open_tasks, args.shape)
        return
    with tempfile.TemporaryDirectory(prefix="needs-you-load-") as folder:
        child = subprocess.Popen(
            [
                sys.executable,
                __file__,
                "--serve",
                folder,
                "0",
                "--open-tasks",
                str(args.open_tasks),
                "--shape",
                args.shape,
            ],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        try:
            assert child.stdout
            url = json.loads(child.stdout.readline())["url"]
            for path in args.path or ["/api/v2/needs-you"]:
                found = send(
                    url, path, args.readers, args.rate, args.seconds, args.ana_readers, args.badge_readers, args.walk
                )
                for result in asyncio.run(found):
                    print(json.dumps(result), flush=True)
        finally:
            assert child.stdin
            child.stdin.close()
            child.wait(timeout=60)


if __name__ == "__main__":
    main()
