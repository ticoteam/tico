"""Requests a real idle runner sends its hub per hour, by route, and how long a change takes to reach it: the harness
relay's `GET config` and the side jobs' `GET runners/importers`, which a runner on relay_hints_v1 reads on the hub's
word and every 5 minutes instead of every minute (runner-rs/src/events.rs relays_pushed).

For each binary given (BEFORE, AFTER: paths to `tico-runner` builds), a fresh fictional hub on a local port and one
enrolled computer running `tico-runner run` with TICO_SIDE_JOBS=1 and no bots. After WARM seconds it counts every
request for WINDOW seconds and prints them per hour. Then it moves a meeting importer onto the computer and back, and
connects and clears the Gateway, SAMPLES times each, and prints how long from the commit until the computer read again
(the runner without hints waits for its minute). The importers job the supervisor starts polls the same route every 30 s,
so one sample in a few hundred may be its read rather than the supervisor's.

    BEFORE=/tmp/before/tico-runner AFTER=runner-rs/target/release/tico-runner nice -n 10 .venv/bin/python \
        bench/perf-runner-push.py

Env: WARM (default 60), WINDOW (330), SAMPLES (5). Only fictional data, in a temporary folder removed afterwards; the
runner reaches nothing but the local hub.
"""

import collections
import json
import os
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)
import uvicorn

from backend import gateway_connection
from backend.tests.helpers.api import post, runner
from backend.tests.helpers.api_app import api_client

WARM = float(os.environ.get("WARM", 60))
WINDOW = float(os.environ.get("WINDOW", 330))
SAMPLES = int(os.environ.get("SAMPLES", 5))
CONFIG, IMPORTERS = "GET /api/v2/config", "GET /api/v2/runners/importers"


class Counting:
    """The hub's app, noting each request's method, path and time as it arrives."""

    def __init__(self, app):
        self.app, self.seen, self.lock = app, [], threading.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            with self.lock:
                self.seen.append((f"{scope['method']} {scope['path']}", time.monotonic()))
        await self.app(scope, receive, send)

    def since(self, route, at):
        with self.lock:
            return [t for r, t in self.seen if r == route and t > at]


def next_read(hub, route, after, limit):
    """Seconds from `after` until the computer asked `route` again, or None within `limit`."""
    while time.monotonic() < after + limit:
        found = hub.since(route, after)
        if found:
            return found[0] - after
        time.sleep(0.01)
    return None


def measure(label, binary):
    folder = Path(tempfile.mkdtemp(prefix=".tmp-runner-push-", dir=ROOT))
    install = api_client(folder, None)
    api = next(install)
    process = None
    try:
        store = api.app.state.store
        hub = Counting(api.app)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(hub, log_level="error", lifespan="off", timeout_graceful_shutdown=2))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        while not server.started:
            time.sleep(0.01)
        computer = runner(api, label="Bench")
        home, projects = folder / "home", folder / "projects"
        home.mkdir(), projects.mkdir()
        config = folder / "config" / "runner.json"
        config.parent.mkdir()
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        config.write_text(
            json.dumps(
                {
                    "url": url,
                    "token": computer["token"],
                    "runner_id": computer["runner_id"],
                    "projects_dir": str(projects),
                }
            )
        )
        env = {
            "HOME": str(home),
            "PATH": os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
            "TICO_SIDE_JOBS": "1",
            "TICO_UPDATE_CHECK": "off",
        }
        with (folder / "runner.log").open("a") as out:
            process = subprocess.Popen(
                [str(binary), "--config", str(config), "run"],
                cwd=ROOT,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        time.sleep(WARM)
        began = time.monotonic()
        time.sleep(WINDOW)
        counts = collections.Counter(r for r, t in list(hub.seen) if t > began)
        print(f"\n== {label}: requests per hour from one idle computer (window {WINDOW:.0f} s after {WARM:.0f} s)")
        for route, n in counts.most_common():
            print(f"  {route:55s} {n * 3600 / WINDOW:8.1f}")
        print(f"  {'all':55s} {sum(counts.values()) * 3600 / WINDOW:8.1f}")

        # A change on the hub to the computer's next read of it.
        late = []
        moves = []
        for n in range(SAMPLES):
            post(api, "meeting-importers/granola", {"enabled": n % 2 == 0, "runner_id": computer["runner_id"]})
            at = time.monotonic()  # committed: a read from here on sees the change
            took = next_read(hub, IMPORTERS, at, 70)
            moves.append(took) if took is not None else late.append("importers")
            time.sleep(2)
        flips = []
        for n in range(SAMPLES):
            with store.transaction() as c:
                record = gateway_connection._row(c)
                if n % 2 == 0:
                    record = {**record, "key": "vault:bench-placeholder"}  # connected: no value, the hint only
                else:
                    record.pop("key", None)
                gateway_connection._write(c, record)
            at = time.monotonic()
            took = next_read(hub, CONFIG, at, 70)
            flips.append(took) if took is not None else late.append("config")
            time.sleep(2)
        for name, values in (("importer moved -> GET runners/importers", moves), ("Gateway flip -> GET config", flips)):
            if values:
                print(
                    f"  {name:45s} median {statistics.median(values) * 1000:8.0f} ms"
                    f"  max {max(values) * 1000:8.0f} ms  (n={len(values)}: "
                    + ", ".join(f"{v * 1000:.0f}" for v in values)
                    + ")"
                )
        if late:
            print("  no read within 70 s:", collections.Counter(late))
        server.should_exit = True
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        if os.environ.get("KEEP_LOG"):
            print((folder / "runner.log").read_text()[-4000:])
        install.close()
        shutil.rmtree(folder, ignore_errors=True)


def main():
    for label in ("BEFORE", "AFTER"):
        binary = os.environ.get(label)
        if binary:
            measure(label.lower(), Path(binary).resolve())


if __name__ == "__main__":
    main()
