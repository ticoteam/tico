"""A real Tico hub for the browser tests (ui/tests/support/real-hub.cjs): the app in-process on a fresh temporary database,
started from the scenario harness's world (evals/redesign/harness.py Org) and seeded by the named seeds
(evals/redesign/seeds.py).

    python3 ui/tests/support/real_hub_server.py [--seed org,toured]

The world is the scenario's: its people, bots and reports_to, the fake clock frozen at the scenario's start (so a runner
heartbeat seeded during setup does not age out during a test), and the fakes it sets up for external services. The app serves ui/ itself, as in
production (backend/app.py). A browser signs in by sending a teammate's token as the Bearer, the token the API takes.

Prints one JSON line when ready: {"url", "seeds", "skipped", "tokens": {teammate id: token}, "runner": the world's
runner token, for a test that claims a bot's run as its computer would}. Stops on SIGTERM and
removes its temporary folder.
"""

import argparse
import json
import shutil
import signal
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import uvicorn  # noqa: E402

from evals.redesign.harness import Org  # noqa: E402
from evals.redesign.seeds import SEEDS  # noqa: E402


def bound_socket():
    """A loopback socket already bound to a free port and handed to uvicorn as is, so no other process can take the
    port between choosing it and serving on it."""
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock


def teammate_tokens(world):
    """Each test token the world accepts, keyed by the teammate it signs in as (human:ana -> ana)."""
    return {identity.actor.split(":", 1)[1]: token
            for token, identity in world.app.state.auth.settings.test_identities.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", default="org", help="comma-separated seeds, run in order (default: org)")
    args = parser.parse_args(argv)
    names = [name for name in args.seed.split(",") if name]
    unknown = [name for name in names if name not in SEEDS]
    if unknown:
        parser.error("unknown seed: " + ", ".join(unknown) + "; known: " + ", ".join(sorted(SEEDS)))
    # SystemExit from the handler runs the finally below, so a stopped hub removes its temporary folder.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    world = Org().__enter__()     # starts the clock and the app; the lifespan runs under the world's test client
    server = thread = None
    try:
        settings = world.app.state.auth.settings
        sock = bound_socket()
        port = sock.getsockname()[1]
        settings.public_url = f"http://127.0.0.1:{port}"
        skipped = [name for name in names if SEEDS[name](world) is False]
        # The lifespan already ran under the test client, so uvicorn must not run it again.
        server = uvicorn.Server(uvicorn.Config(world.app, host="127.0.0.1", port=port, log_level="warning",
                                               lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        for _ in range(600):   # a count, not a clock: the world's clock is frozen
            if server.started or not thread.is_alive():
                break
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("the hub did not start")
        print(json.dumps({"url": settings.public_url, "seeds": names, "skipped": skipped,
                          "tokens": teammate_tokens(world), "runner": world.runner["token"]}), flush=True)
        while True:
            time.sleep(1)
    finally:
        if server is not None:
            server.should_exit = True
            thread.join(10)
        world.__exit__(None, None, None)
        shutil.rmtree(world.tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
