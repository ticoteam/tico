"""Demo mode: `python -m backend.demo` (or `docker run ... ghcr.io/ticoteam/tico demo`).

A whole fictional team, Acme, in a throwaway database, so someone can click through Tico in a
minute with no domain, DNS, sign-in or model account. It cannot be mistaken for an install:

- the page carries a "Demo - sample data" banner that cannot be closed;
- nothing leaves the computer: the update check is off, no telemetry is configured, and this process
  refuses every connection to a non-loopback address (`block_network`);
- bots never run: there is no scheduler, "run now" explains itself, and a message to a bot gets a
  one-line answer that says so;
- it listens on loopback only. A container has to bind 0.0.0.0 inside, so there it answers only
  requests whose Host is a loopback name; `--public-demo` is the one way to serve anyone else, and
  then the demo is read-only.

`--company heating-air`, `kitchen` or `works` builds one of three other fictional companies instead, with its records
imported from CSV files (backend/demo_companies.py).
"""

import argparse
import asyncio
import contextlib
import errno
import ipaddress
import logging
import os
import re
import shutil
import signal
import socket
import sys
import tempfile
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .auth import LOCAL_SIGNIN_PATH
from .config import LOOPBACK, Settings
from .demo_companies import COMPANIES
from .store import H, Problem

log = logging.getLogger("tico.demo")

DEFAULT_PORT = 8765
OWNER_EMAIL = "ana@acme.example"
BANNER = "Demo — sample data"
EXPLAIN = ("This is a demo with sample data: bots do not run here and nothing leaves this computer. "
           "On a real install, this starts the bot on a connected computer.")
BOT_REPLY = ("This is a demo, so I do not run here. On a real install this message would reach me on a "
             "connected computer and I would answer it there.")

# What would start a bot or call a model. Refused with EXPLAIN rather than queued for nobody.
RUNS = re.compile(r"^/api(?:/v2)?/(?:tasks/[^/]+/run-now|routines/[^/]+/run|jobs/[^/]+/(?:retry|reconcile)"
                  r"|bots/[^/]+/control|page-chat|docs/ask|judge|decisions|market/curator/sweep"
                  r"|(?:runners|computers)/[^/]+/(?:logins|harness-actions|restart)|system/update)$")
# Writes a public (read-only) demo still takes, so the page can remember what a visitor has read.
PUBLIC_WRITES = re.compile(r"^/api/v2/(?:updates/read|preferences/[^/]+|(?:setup/)?getting-started/state)$")

AVATAR = re.compile(r"^/api/(?:people|humans)/([a-z0-9-]+)/photo$")

ATTEMPTS = []   # every outbound connection refused in this process, for tests and for the curious


# ----------------------------------------------------------------------------- what may listen
def loopback_address(host):
    host = str(host).strip().strip("[]")
    if host in LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_bind(host, public_demo=False, in_container=False):
    """Refuse to serve sample data (with a built-in owner session) to a network by accident."""
    if public_demo or in_container or loopback_address(host):
        return
    raise SystemExit(
        f"tico demo: refusing to listen on {host or 'all interfaces'}. The demo signs everyone in as its "
        "owner, so it stays on this computer (127.0.0.1). To put it on a network on purpose, add "
        "--public-demo; it is then read-only. In Docker, publish the port on loopback: "
        "-p 127.0.0.1:8765:8765")


# ----------------------------------------------------------------------------- no outbound calls
def _remote(address):
    if isinstance(address, (str, bytes)):          # a unix socket path
        return False
    host = str(address[0]) if address else ""
    return bool(host) and not loopback_address(host) and host != "0.0.0.0"


@contextlib.contextmanager
def network_blocked():
    """Refuse every connection and name lookup that is not this computer. Process-wide while open."""
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    getaddrinfo, gethostbyname = socket.getaddrinfo, socket.gethostbyname

    def refuse(target):
        ATTEMPTS.append(str(target))
        raise OSError(errno.ENETUNREACH, "the Tico demo makes no outbound connections: " + str(target))

    def guarded_connect(self, address):
        if _remote(address):
            refuse(address)
        return connect(self, address)

    def guarded_connect_ex(self, address):
        if _remote(address):
            refuse(address)
        return connect_ex(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in (None, "") and not loopback_address(host) and host != "0.0.0.0":
            refuse(host)
        return getaddrinfo(host, *args, **kwargs)

    def guarded_gethostbyname(host):
        if not loopback_address(host):
            refuse(host)
        return gethostbyname(host)

    socket.socket.connect, socket.socket.connect_ex = guarded_connect, guarded_connect_ex
    socket.getaddrinfo, socket.gethostbyname = guarded_getaddrinfo, guarded_gethostbyname
    try:
        yield ATTEMPTS
    finally:
        socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
        socket.getaddrinfo, socket.gethostbyname = getaddrinfo, gethostbyname


# ----------------------------------------------------------------------------- the running demo
async def keep_alive(store, stop, vault=None):
    """What a real install's scheduler and a runner's heartbeat would keep true: the computers look
    online, the scheduler looks alive, and a message to a bot is answered (once) that this is a demo."""
    # The demo's scheduler is off, so Finance's bank worker would never sync. With TICO_MERCURY_BASE set (a local
    # fake Mercury: scripts/finance_e2e.sh), the worker runs and reads only that base. Without it, nothing starts.
    # The same override runs Finance's short jobs every tick (waiting events, bills due, reminders), so payments and
    # approvals can be tested without the scheduler.
    finance_jobs = False
    if vault is not None:
        from .finance import actions as finance
        from .finance import mercury
        if mercury.base_url() != mercury.BASE:
            finance.banking_start(store, vault)
            finance_jobs = True
    while not stop.is_set():
        try:
            await asyncio.to_thread(tick, store)
            if finance_jobs:
                await asyncio.to_thread(finance_tick, store)
        except Exception as exc:
            log.error("Demo tick failed: %s", type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=3)
        except TimeoutError:
            pass


def tick(store):
    with store.transaction() as c:
        now = H.now()
        c.execute("UPDATE runners SET last_seen=? WHERE revoked_at IS NULL", (now,))
        c.execute("INSERT INTO service_health VALUES('scheduler',?,NULL,'{}') ON CONFLICT(service) DO UPDATE SET "
                  "last_success=excluded.last_success,last_error=NULL", (now,))
        for job in c.execute("SELECT j.id,j.bot,m.from_actor,m.conversation_id,m.id AS message FROM jobs j "
                             "JOIN messages m ON m.id=j.message_id WHERE j.state='queued'").fetchall():
            c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (job["id"],))
            if H.is_human(job["from_actor"]):
                try:
                    H.say(c, H.bot_actor(job["bot"]), job["from_actor"], BOT_REPLY,
                          conversation_id=job["conversation_id"], in_reply_to=job["message"])
                except H.Refused:
                    pass


def finance_tick(store):
    from .finance import actions as finance, reads as finance_reads
    with store.read() as c:
        if not finance_reads.is_set_up(c):
            return
    for step in (finance.drain_events, finance.watch_documents):
        with store.transaction() as c:
            step(c)


def avatar(pid):
    """A drawn portrait for a sample person, so the page never asks for a photo that does not exist."""
    from . import demo_content
    name = next((p["name"] for p in demo_content.PEOPLE if p["id"] == pid), pid)
    initials = "".join(part[0] for part in name.split()[:2]).upper()
    hue = sum(map(ord, name)) * 37 % 360
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" '
           f'fill="hsl({hue},45%,42%)"/><text x="32" y="41" font-family="system-ui,sans-serif" font-size="26" '
           f'font-weight="600" text-anchor="middle" fill="#fff">{initials}</text></svg>')
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=3600"})


def install(app, auth, settings):
    """The demo's front gate, outermost so nothing else answers first."""

    # "Ask the librarian" answers from the graph alone: no model is called.
    from . import market
    market.complete = lambda question, excerpts: ""

    # The seeded Granola account holds no token and nothing leaves this computer: a sync answers "recent".
    async def granola_recent(who, interval=0):
        return {"state": "recent", "last_sync": app.state.granola.status(who)["last_sync"]}
    app.state.granola.trigger = granola_recent

    def problem(status, code, detail):
        return JSONResponse({"error": {"code": code, "detail": detail, "retryable": False}}, status_code=status)

    @app.middleware("http")
    async def demo_gate(request: Request, call_next):
        host = request.headers.get("host", "")
        name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        if not settings.demo_public and not loopback_address(name):
            return problem(421, "demo_host", "The demo answers only on localhost. Open http://localhost:"
                           + str(DEFAULT_PORT) + " (or use --public-demo).")
        path = request.url.path
        photo = AVATAR.match(path)
        if photo and request.method == "GET":
            return avatar(photo.group(1))
        if request.method == "GET" and path in ("/", "/index.html"):
            # Nobody types a secret to try a demo: the first page visit is the sign-in.
            if not auth.local_session(request.cookies):
                token = auth.local_token()
                return RedirectResponse(LOCAL_SIGNIN_PATH + "?token=" + token + "&next=/", status_code=302)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if RUNS.match(path):
                return problem(409, "demo", EXPLAIN)
            if settings.demo_public and not PUBLIC_WRITES.match(path):
                return problem(403, "demo", "This public demo is read-only. Run your own with "
                               "`docker run --rm -p 127.0.0.1:8765:8765 ghcr.io/ticoteam/tico demo`.")
        return await call_next(request)


# ----------------------------------------------------------------------------- start
def prepare(directory, url="", public=False, company=""):
    """Settings for a demo whose files live in `directory`. Sign-in is the loopback owner session."""
    directory = Path(directory)
    token_file = directory / "local-owner.token"
    if not token_file.exists():
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(os.urandom(32).hex())
    settings = Settings(
        db_path=directory / "hub.sqlite", registry_dir=directory / "registry", environment_id="demo",
        company_name=COMPANIES.get(company, "Acme"), app_name="Tico", assistant_name="Assistant",
        owner_email=OWNER_EMAIL,
        github_owner="acme", local_owner_token_file=token_file, public_url=(url.rstrip("/") if url and not public else f"http://127.0.0.1:{DEFAULT_PORT}"),
        scheduler_enabled=False, blob_dir=directory / "blobs", enabled_providers=("anthropic",),
        default_runtime="claude", default_model="claude-opus-5-5", release_id="demo", demo=True,
        demo_public=public)
    if public and url:
        # Loopback is what makes a built-in owner session legal (Settings refuses it elsewhere);
        # a public demo is that session on purpose, so the address is set after the check.
        settings.public_url = url.rstrip("/")
    return settings


def build(directory, now=None, url="", public=False, company=""):
    """A ready demo in `directory`: its registry, database and files. Returns its Settings. `company` (a key of
    COMPANIES) builds that company instead of the Acme software team."""
    from . import demo_companies, demo_seed
    os.environ["TICO_UPDATE_CHECK"] = "off"
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / ".tico-demo"
    expected = f"tico-demo-{company}-v1\n" if company else "tico-demo-v1\n"
    data = [directory / name for name in ("hub.sqlite", "registry", "local-owner.token", "blobs")]
    # Only a completed seed may reopen as a demo owner. Never overwrite another install or a partial seed.
    if marker.is_file() and marker.read_text() == expected and all(p.exists() for p in data[:3]):
        return prepare(directory, url, public, company)
    if marker.exists() or any(p.exists() for p in data):
        raise Problem("demo_data", "This directory is not a completed Tico demo; use an empty directory", 409)
    settings = prepare(directory, url, public, company)
    if company:
        demo_companies.populate(settings, company)
    else:
        demo_seed.populate(settings, now)
    marker.write_text(expected)
    return settings


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tico demo", description=__doc__.split("\n")[0])
    parser.add_argument("--host", default=None, help="address to listen on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--public-demo", action="store_true",
                        help="serve non-loopback addresses on purpose; the demo is then read-only")
    parser.add_argument("--url", default="", help="the browser address, e.g. http://localhost:8877 or https://demo.example.com")
    parser.add_argument("--in-container", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--data-dir", default="", help="keep the demo's files here (default: a temporary folder)")
    parser.add_argument("--company", default="", choices=sorted(COMPANIES),
                        help="build another fictional company instead of the Acme software team")
    args = parser.parse_args(argv)
    host = args.host if args.host is not None else ("0.0.0.0" if args.in_container else "127.0.0.1")
    check_bind(host, args.public_demo, args.in_container)
    if args.public_demo and not args.url:
        raise SystemExit("tico demo: --public-demo needs --url, the address people will open")
    for name in ("TICO_UPDATER_URL", "TICO_POSTHOG_KEY", "TICO_SENTRY_DSN", "TICO_SENTRY_SERVER_DSN"):
        os.environ.pop(name, None)
    from .server import run
    from .screen_binary import prepare as prepare_relay
    prepare_relay()
    directory = Path(args.data_dir or tempfile.mkdtemp(prefix="tico-demo-"))
    directory.mkdir(parents=True, exist_ok=True)
    # SIGTERM (docker stop, a supervisor) ends the demo the way Ctrl+C does, so the folder below still goes.
    previous = signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    try:
        print(f"Building the {COMPANIES.get(args.company, 'Acme')} demo ...", flush=True)
        with network_blocked():
            settings = build(directory, url=args.url, public=args.public_demo, company=args.company)
            if args.port != DEFAULT_PORT and not args.public_demo and not args.url:
                settings.public_url = settings.runner_url = f"http://127.0.0.1:{args.port}"
            browser_url = args.url.rstrip("/") if args.url else f"http://localhost:{args.port}"
            print(f"\nTico demo is ready: open {browser_url}\n"
                  "It is fictional sample data (acme.example); bots do not run and nothing leaves this computer.\n"
                  "Press Ctrl+C to stop; everything is discarded.\n", flush=True)
            run(settings=settings, host=host, port=args.port, log_level="warning")
    finally:
        signal.signal(signal.SIGTERM, previous)
        if not args.data_dir:          # a folder the person named with --data-dir is theirs to keep
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
