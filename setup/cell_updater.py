"""Each cell company's updater: docker/updater.py's contract, served to that one company (docs/cell.md, "Updates").

POST /update {"version": "1.2.3" | "v1.2.3" | "latest", "from": "1.2.2"}   start an update: 202 and the status; 409
                                       while one runs, while another cell command holds the company, or for a release
                                       older than it runs; 422 for anything but a stable release or latest
GET  /status                           {"state", "from", "to", "message", "snapshot", "restored"}; state is idle,
                                       pulling, restarting, healthy, rolled_back or failed
GET  /diagnostics                      {"mode": "cell", "version", "docker", "compose", "containers", "errors"}
All need `Authorization: Bearer <token>`: the company's own <company>/control/updater-token (setup/cell.py
`updater_token`), which its server reads as /control/updater-token. The server cannot tell this from the sidecar, so
Update now, an update service key (the Release Manager's rollout), Tico support's idle-time target
(backend/hosted_updates.py), the update events and Health's Version line work unchanged.

The work is setup/cell.py's own `update` for that company: the image pulled first, the database snapshotted with the
server stopped, the new release health-checked through the router, and the old release and database put back when it
does not come up. A release is refused (state failed, nothing changed) unless it is published (its SHA256SUMS
downloads, as the sidecar's bundle check needs) and no newer than this host's core: a company's compose.yaml is the
core's own file (`update` rewrites it from there), and a later release may need settings or mounts that file does
not pass. Tico moves the core with `cell init` from a newer core folder, never this service. Older than the company
runs is refused as the sidecar refuses it.

Isolation. One process (systemd unit tico-cell-updater, `cell init`), one listener per company. It runs as root, as
staff's cell commands do, because the work is `update`: Docker and the company's root-owned folder. A unit, not a
container, so no container ever holds the Docker socket.
- Company i's listener is bound to its default network's gateway address (the host's side of that bridge, 10.208.i.1)
  and UPDATER_PORT only, never 0.0.0.0, so nothing off the host reaches it and the router never sends anything there.
- The kernel takes a packet for any host address from any bridge, so another company's container can open a
  connection to that address. The listener answers only a peer inside that company's own default subnet, which only
  its containers are on; a server drops every capability, so it cannot send from an address it does not have.
- Then the token: read on each request from that company's own control folder, which only that company's server
  mounts, refused unless a plain file, compared in constant time, never logged.
- The company a listener acts on comes from the cell's record (slug and index, checked again on each request), never
  from the request: the body is read for a version and the release the server runs, nothing else, at most MAX_BODY.
- So a company can only update itself, to a release it could have reached with the sidecar. It reaches no other
  company and nothing else on the host.

A stop (`cell init` installing another core restarts the unit) lets an update under way finish (KillMode=mixed,
TimeoutStopSec in the unit). Each company's last status is kept in <company>/updater-status.json, so a restarted
service still answers it; one that was still running when the service died says so.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import hmac
import ipaddress
import json
import os
import re
import signal
import socket
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import cell as C

STABLE = re.compile(r"v?([0-9]{1,6})\.([0-9]{1,6})\.([0-9]{1,6})")
UNDER_WAY = ("pulling", "restarting")
MAX_BODY = 1024
TOKEN_MIN, TOKEN_MAX = 32, 4096
STATUS_FILE = "updater-status.json"
RESCAN = 5.0                     # how soon a new company's listener is up, and a removed one's gone
FREEBIND = getattr(socket, "IP_FREEBIND", 15)   # Linux: bind before Docker has made the company's bridge
INSPECT = ('{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Status}}|'
           '{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.RestartCount}}')    # docker/updater.py's own


def triple(version) -> tuple[int, int, int] | None:
    m = STABLE.fullmatch(version) if isinstance(version, str) else None
    return tuple(int(p) for p in m.groups()) if m else None


def tag(version: str) -> str:
    """A release as its image tag spells it (v1.2.3), as the sidecar does."""
    return "v" + version if version[:1].isdigit() else version


def log(text: str) -> None:
    print("tico-cell-updater: " + text, file=sys.stderr, flush=True)


class Service:
    """Every company's updater state, the listeners, and the update threads. `make_cell` gives a fresh setup/cell.py
    Cell per use (tests pass one on a fake System); `core` is this host's core release ("" when unknown); `bind` and
    `subnet` are company i's address and the peers it answers (tests move both to loopback)."""

    def __init__(self, root: Path, system: C.System | None = None, *, make_cell=None, core=None, bind=None,
                 subnet=None):
        self.root, self.system = Path(root), system or C.System()
        self.make_cell = make_cell or (lambda: C.Cell(self.root, self.system))
        self.core = core or C._installer_version
        self.bind = bind or (lambda index: (C.networks(index)["default_gateway"], C.UPDATER_PORT))
        self.subnet = subnet or (lambda index: C.networks(index)["default_subnet"])
        self.guard = threading.Lock()
        self.status: dict[str, dict] = {}
        self.errors: dict[str, collections.deque] = {}
        self.workers: set[threading.Thread] = set()
        self.listeners: dict[str, Listener] = {}
        self.published: set[str] = set()
        self.stopping = threading.Event()

    # --- state ------------------------------------------------------------------------------------------------

    def state(self, slug: str) -> dict:
        """The company's status (under `guard`): the saved one at first, else idle. One saved while an update ran
        means the service stopped under it."""
        if slug not in self.status:
            found = {"state": "idle", "from": "", "to": "", "message": ""}
            with contextlib.suppress(OSError, ValueError):
                saved = json.loads((self.root / "companies" / slug / STATUS_FILE).read_text())
                if isinstance(saved, dict) and saved.get("state") in ("healthy", "rolled_back", "failed", *UNDER_WAY):
                    found.update({k: v for k, v in saved.items() if isinstance(v, (str, bool))})
            if found["state"] in UNDER_WAY:
                found.update(state="failed", message="Not settled: the cell updater stopped while this update ran. "
                                                     "The company's next start finishes it or rolls it back.")
            self.status[slug] = found
            self.errors[slug] = collections.deque(maxlen=50)
        return self.status[slug]

    def set(self, slug: str, **fields) -> None:
        with self.guard:
            s = self.state(slug)
            s.update(fields)
            if fields.get("state") in ("failed", "rolled_back"):
                self.errors[slug].append("%s %s: %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), s["state"],
                                                        str(s.get("message") or "")[:250]))
            text = json.dumps(s, sort_keys=True) + "\n"
        d = self.root / "companies" / slug
        if d.is_dir():
            with contextlib.suppress(OSError):
                C._write_atomic(d / STATUS_FILE, text, 0o600)

    # --- requests ---------------------------------------------------------------------------------------------

    def company(self, slug: str, index: int) -> dict | None:
        """The record a listener serves, only while it is still that company at that index."""
        c = self.make_cell().load(slug)
        return c if c and c.get("state") != "removed" and c.get("index") == index else None

    def gate(self, slug: str, index: int, peer: str, auth: str) -> tuple[int, dict] | None:
        """None when the request may go on; else the answer. The peer first, so a stranger learns nothing more."""
        c = self.company(slug, index)
        try:
            inside = c is not None and ipaddress.ip_address(peer) in ipaddress.ip_network(self.subnet(index))
        except ValueError:
            inside = False
        if not inside:
            return 403, {"error": "forbidden"}
        expected = self.token(slug)
        if not expected or not hmac.compare_digest(auth.encode(), ("Bearer " + expected).encode()):
            return 401, {"error": "unauthorized"}
        return None

    def token(self, slug: str) -> str:
        """The company's own token, or "" (missing, not a plain file, or the wrong size). The server owns the folder,
        so the file is opened without following a link."""
        path = self.root / "companies" / slug / "control" / C.TOKEN_NAME
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return ""
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > TOKEN_MAX:
                return ""
            text = os.read(fd, TOKEN_MAX).decode("utf-8", "replace").strip()
        finally:
            os.close(fd)
        return text if len(text) >= TOKEN_MIN and not any(ch.isspace() for ch in text) else ""

    def get(self, slug: str, path: str) -> tuple[int, dict]:
        if path == "/status":
            with self.guard:
                return 200, dict(self.state(slug))
        if path == "/diagnostics":
            return 200, self.diagnostics(slug)
        return 404, {"error": "not found"}

    def post(self, slug: str, index: int, path: str, body: bytes) -> tuple[int, dict]:
        if path != "/update":
            return 404, {"error": "not found"}
        try:
            asked = json.loads(body or b"{}")
            version, running = asked.get("version", ""), asked.get("from", "")
        except (ValueError, AttributeError):
            version, running = "", ""
        if not isinstance(version, str) or not (version == "latest" or triple(version)):
            return 422, {"error": "version is latest or X.Y.Z"}
        version = tag(version)
        c = self.company(slug, index)
        if c is None:
            return 404, {"error": "not found"}
        if triple(version) and triple(c["version"]) and triple(version) < triple(c["version"]):
            return 409, {"error": "older than the running version"}
        running = tag(running) if triple(running) else c["version"]
        with self.guard:
            s = self.state(slug)
            if s["state"] in UNDER_WAY:
                return 409, {"error": "an update is already running", **s}
            before = dict(s)
            s.update(state="pulling", to=version, message="", snapshot="", restored=False, **{"from": running})
        try:                            # staff's `cell update` (or add, stop...) holds the company: not now
            with self.make_cell().lock("company-" + slug, timeout=0):
                pass
        except C.CellError:
            with self.guard:
                self.status[slug] = before
                return 409, {"error": "an update is already running", **before,
                             "message": "Another cell command is changing this company; try again when it is done."}
        self.set(slug)
        worker = threading.Thread(target=self.work, args=(slug, version), name="update-" + slug)
        with self.guard:                # the answer is the status as accepted, as the sidecar's: the worker moves on
            accepted = dict(self.state(slug))
            self.workers.add(worker)
        worker.start()
        return 202, accepted

    # --- the update -------------------------------------------------------------------------------------------

    def work(self, slug: str, version: str) -> None:
        cell = self.make_cell()
        cell.lock_wait = 0              # a command that took the company since the request: refused, not queued
        try:
            target, refused = self.check(cell, slug, version)
            if refused:
                self.set(slug, state="failed", message="Not updated: " + refused)
                return
            try:
                cell.pull(target)
            except C.CellError as e:
                self.set(slug, state="failed", message="Not updated: " + cell.scrub(e.detail))
                return
            self.set(slug, state="restarting")
            cell.update(argparse.Namespace(company=slug, version=target, timeout=C.HEALTH_TIMEOUT))
            self.set(slug, state="healthy", message="", snapshot=cell.taken)
        except C.CellError as e:
            detail = cell.scrub(e.detail)
            if e.code == "update_failed":
                self.set(slug, state="rolled_back", message=detail, snapshot=cell.taken, restored=bool(cell.taken))
            elif e.code in ("rollback_failed", "unhealthy"):
                self.set(slug, state="failed", message=detail, snapshot=cell.taken)
            else:                       # refused before anything changed, or put back as it was (cell.py `restarted`)
                self.set(slug, state="failed", message="Not updated: " + detail)
        except Exception as e:  # noqa: BLE001  the status says it, rather than a dead thread saying nothing
            self.set(slug, state="failed", message=cell.scrub(f"the cell updater failed: {type(e).__name__}: {e}")[:600])
        finally:
            log(f"{slug}: update to {version}: {self.status.get(slug, {}).get('state', '')}")
            with self.guard:
                self.workers.discard(threading.current_thread())

    def check(self, cell: C.Cell, slug: str, version: str) -> tuple[str, str]:
        """(the release to run, why not or ""): latest looked up, then published, not older than the company runs, and
        no newer than this host's core."""
        target = version
        if version == "latest":
            target = self.system.latest_release() or ""
            if not triple(target):
                return "", "could not look up the latest release"
            target = tag(target)
        elif target not in self.published:
            known = self.system.release_published(target)
            if known is None:
                return "", f"could not check that {target} is a published release; try again later"
            if not known:
                return "", f"{target} is not a published release"
            self.published.add(target)
        c = cell.load(slug)
        if not c or c.get("state") == "removed":
            return "", "the company is not on this host"
        if triple(c["version"]) and triple(target) < triple(c["version"]):
            return "", f"{target} is older than {c['version']}, which it runs"
        core = self.core()
        if not triple(core):
            return "", "this host's core has no release recorded (.tico-installer.json), so no release can be checked " \
                       "against it; Tico staff update it with `cell update`"
        if triple(target) > triple(core):
            return "", (f"{target} needs this host's core at {target} or later, and it is at {core}. Tico moves the "
                        "core with `cell init` from that release's core folder, then this update can run")
        return target, ""

    def diagnostics(self, slug: str) -> dict:
        """docker/updater.py's diagnostics, for this company's containers only."""
        cell = self.make_cell()

        def out(argv: list[str]) -> str:
            r = self.system.run(argv, timeout=15)
            return r.stdout.strip() if r.returncode == 0 else ""
        containers = []
        for ident in out(cell.compose(slug, "ps", "-a", "-q")).split()[:50]:
            row = out(["docker", "inspect", "--format", INSPECT, ident]).split("|")
            if len(row) == 4:
                containers.append({"name": row[0], "state": row[1], "health": row[2],
                                   "restarts": int(row[3]) if row[3].isdigit() else 0})
        with self.guard:
            self.state(slug)
            recent = list(self.errors[slug])
        return {"mode": "cell", "version": self.core(), "docker": out(["docker", "version", "--format",
                                                                       "{{.Server.Version}}"]),
                "compose": out(["docker", "compose", "version", "--short"]), "containers": containers, "errors": recent}

    # --- listeners --------------------------------------------------------------------------------------------

    def scan(self) -> None:
        """One listener per company not removed, at its own address; gone for one removed or moved to another slot."""
        want = {c["slug"]: c["index"] for c in self.make_cell().live_companies()}
        for slug, listener in list(self.listeners.items()):
            if want.get(slug) != listener.index:
                listener.shutdown()
                listener.server_close()
                del self.listeners[slug]
        for slug, index in want.items():
            if slug not in self.listeners:
                try:
                    self.listeners[slug] = Listener(self, slug, index, self.bind(index))
                except OSError as e:
                    log(f"{slug}: cannot listen on {self.bind(index)}: {e.strerror or e}")

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stopping.set())
        log(f"serving {self.root} (core {self.core() or 'unknown'})")
        while True:
            self.scan()
            if self.stopping.wait(RESCAN):
                break
        for listener in list(self.listeners.values()):
            listener.shutdown()
            listener.server_close()
        with self.guard:
            running = list(self.workers)
        for worker in running:          # an update under way finishes: a kill half-way would leave it to a restart
            worker.join()


class Listener(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, service: Service, slug: str, index: int, address: tuple[str, int]):
        self.service, self.slug, self.index = service, slug, index
        super().__init__(address, Handler, bind_and_activate=False)
        try:
            if sys.platform.startswith("linux"):
                self.socket.setsockopt(socket.IPPROTO_IP, FREEBIND, 1)
            self.server_bind()
            self.server_activate()
        except OSError:
            self.server_close()
            raise
        threading.Thread(target=self.serve_forever, daemon=True, name="listen-" + slug).start()


class Handler(BaseHTTPRequestHandler):
    server_version = "tico-cell-updater"
    timeout = 10                        # a client that sends nothing does not keep a thread

    def reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def gated(self) -> bool:
        s = self.server
        refused = s.service.gate(s.slug, s.index, self.client_address[0], self.headers.get("Authorization", ""))
        if refused:
            self.reply(*refused)
        return refused is None

    def do_GET(self):
        if self.gated():
            self.reply(*self.server.service.get(self.server.slug, self.path))

    def do_POST(self):
        if not self.gated():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            return self.reply(413, {"error": "the body is at most %d bytes" % MAX_BODY})
        self.reply(*self.server.service.post(self.server.slug, self.server.index, self.path, self.rfile.read(length)))

    def log_message(self, *args):
        pass                            # request lines are not kept: nothing here needs them


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m setup.cell_updater",
                                description="Each cell company's updater (docs/cell.md); `cell init` runs it.")
    p.add_argument("--root", default=os.environ.get("TICO_CELL_ROOT") or C.ROOT)
    args = p.parse_args(argv)
    Service(Path(args.root)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
