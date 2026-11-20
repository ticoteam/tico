"""A local server on a made-up company (Acme), seeded for the web app's journeys (web/e2e/journeys.ts).

    python3 web/e2e/hub/seeded.py [--web-dir build/web]

The company is the redesign test world's (evals/redesign: Dana the owner, Omar and Priya, four agents, a fake clock,
a fake runner), plus what the journeys start from (web/e2e/hub/journey_seed.py). Nothing here is a copy of a real
company, and nothing leaves the machine: mail goes to a fake Gmail, the decision model is a steady stand-in.

The server serves the web app's build (`npm run build` in web/) at /, as production does, and signs a browser in with
the local owner link (/api/v2/local-signin?token=...), as a loopback install does. It also answers GET /_e2e/sql?q=...
(one read-only SELECT, as JSON rows) so a journey can check what a click wrote; only this test server has that route.

The last line printed once it answers is `HUB {json}`: url, token (the local sign-in secret), tokens (each teammate's
bearer, for API calls from the test), and facts (the ids the seed made). Stops on SIGTERM and removes its folder.
"""

import argparse
import json
import os
import shutil
import signal
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from evals.redesign.harness import Org
from evals.redesign.seeds import SEEDS

import journey_seed

LOCAL_TOKEN = "e2e-local-owner-secret-0123456789abcdef"


def bound_socket():
    """A loopback socket already bound to a free port, handed to uvicorn as is (no race for the port)."""
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock


def teammate_tokens(world):
    """Each test bearer the world accepts, keyed by who it signs in as (human:dana -> dana)."""
    return {
        identity.actor.split(":", 1)[1]: token
        for token, identity in world.app.state.auth.settings.test_identities.items()
    }


def add_sql_route(world):
    """GET /_e2e/sql?q=SELECT...: the rows, read only. Registered first, so the app's page fallback never answers it."""
    db = world.app.state.auth.settings.db_path

    async def sql(request: Request):
        query = request.query_params.get("q", "")
        if not query.lstrip().lower().startswith("select"):
            raise HTTPException(400, "SELECT only")
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute(query).fetchall()]
        except sqlite3.Error as e:
            raise HTTPException(400, f"{type(e).__name__}: {e}") from None
        finally:
            con.close()
        return JSONResponse(rows)

    world.app.router.add_api_route("/_e2e/sql", sql, methods=["GET"])
    world.app.router.routes.insert(0, world.app.router.routes.pop())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--web-dir", default=str(ROOT / "build" / "web"), help="the web app's build (default build/web)"
    )
    args = parser.parse_args(argv)
    web_dir = Path(args.web_dir).resolve()
    if not (web_dir / "index.html").is_file():
        sys.exit(f"seeded: no build at {web_dir}; run `npm run build` in web/ first")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    world = Org().__enter__()  # starts the clock and the app; the lifespan runs under the world's test client
    server = thread = None
    try:
        settings = world.app.state.auth.settings
        sock = bound_socket()
        port = sock.getsockname()[1]
        settings.public_url = f"http://127.0.0.1:{port}"
        settings.web_dir = web_dir
        token_file = world.tmp / "local-owner.token"
        token_file.write_text(LOCAL_TOKEN)
        os.chmod(token_file, 0o600)
        settings.local_owner_token_file = token_file
        for name in ("toured", "judge", "assistant"):
            SEEDS[name](world)
        facts = journey_seed.seed(world)
        add_sql_route(world)
        # The lifespan already ran under the test client, so uvicorn must not run it again. A page left open keeps its
        # live stream: on SIGTERM wait a second for it.
        server = uvicorn.Server(
            uvicorn.Config(
                world.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off", timeout_graceful_shutdown=1
            )
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        for _ in range(600):  # a count, not a clock: the world's clock is frozen
            if server.started or not thread.is_alive():
                break
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("the hub did not start")
        hub = {"url": settings.public_url, "token": LOCAL_TOKEN, "tokens": teammate_tokens(world), "facts": facts}
        print("HUB " + json.dumps(hub), flush=True)
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
