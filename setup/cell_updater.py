"""Each cell company's updater: docker/updater.py's contract, served to that one company (docs/cell.md, "Updates").

POST /update {"version": "1.2.3" | "v1.2.3" | "latest", "from": "1.2.2"}   start an update: 202 and the status; 409
                                       while one runs, while another cell command holds the company, or for a release
                                       older than it runs; 422 for anything but a stable release or latest
GET  /status                           {"state", "from", "to", "message", "snapshot", "restored"}; state is idle,
                                       pulling, restarting, healthy, rolled_back or failed
GET  /diagnostics                      {"mode": "cell", "version", "docker", "compose", "containers", "errors"}
GET  /sleep                            {"minutes": N | null}: the company's sleep policy (`cell set-sleep`), idle
                                       minutes before it sleeps; 0 = never sleeps, null = no policy
POST /sleep {"wake_at": "2026-10-30T18:00:00Z" | null, "reason": "idle"}   put the company to sleep (`cell sleep`), with
                                       a timed wake at wake_at when given: 202 {"state": "sleeping", "wake_at"} and the
                                       work a second later, so the answer gets back before the server stops; 409
                                       {"error": "busy"} while an update runs, a sleep is under way, or another cell
                                       command holds the company; an update accepted before the work starts cancels it;
                                       422 {"error": "bad_wake_at"} for a time that is not ISO-8601 UTC or is more than
                                       31 days out (and {"error": "bad_body"} for a body that is not a JSON object).
                                       The body is at most MAX_BODY bytes; a key other than wake_at is ignored.
All need `Authorization: Bearer <token>`: the company's own <company>/control/updater-token (setup/cell.py
`updater_token`), which its server reads as /control/updater-token. The server cannot tell this from the sidecar, so
Update now, an update service key (the Release Manager's rollout), Tico support's idle-time target
(backend/hosted_updates.py), the update events and Health's Version line work unchanged.

The work is setup/cell.py's own `update` for that company: the image pulled first, the database snapshotted with the
server stopped, the new release health-checked through the router, and the old release and database put back when it
does not come up. A release is refused (state failed, nothing changed) unless it is published (its SHA256SUMS
downloads, as the sidecar's bundle check needs); older than the company runs is refused as the sidecar refuses it.
POST /sleep's work is setup/cell.py's own `sleep` for that company, with its wake_at recorded (a timed wake, which
the wake service runs: setup/cell_wake.py).

Sleep and update, one at a time. An update does its publication check, core install and pull outside the company's
lock, so the lock alone cannot keep a sleep from stopping the server under it. A sleep is therefore reserved for its
company under the same `guard` that admits updates (`sleeps`): pending through its lead, committed once its work
begins. An update admitted while the sleep is pending cancels it (the server asks to sleep again later, on the new
release); one that comes once it is committed is a 409, as a sleep that comes while an update runs is. The work
commits only when its own reservation is still there, so an update admitted during the lead always wins.

A company's compose.yaml is the core's own file (`update` rewrites it from there), and a later release may need
settings or mounts an older core's file does not pass. So a release newer than this host's core runs with that
release's own core: its folder installed beside the others as HQ installs one (/opt/tico-cell/core-<vX.Y.Z>, from
GitHub's tag archive, unpacked in a staging folder and renamed into place; an existing folder is used as it is),
then that folder's own `python3 -m setup cell update` run as a child, which takes the company's lock itself. This
service never runs `cell init`: that restarts it, and the router and the tunnel stay with staff (HQ's core install).
The status and diagnostics say the host's core lags until then.

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
import posixpath
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

from . import cell as C

STABLE = re.compile(r"v?([0-9]{1,6})\.([0-9]{1,6})\.([0-9]{1,6})")
UNDER_WAY = ("pulling", "restarting")
MAX_BODY = 1024
SLEEP_LEAD = 1.0                 # how long POST /sleep's work waits after its answer, so the answer gets back first
TOKEN_MIN, TOKEN_MAX = 32, 4096
STATUS_FILE = "updater-status.json"
RESCAN = 5.0                     # how soon a new company's listener is up, and a removed one's gone
FREEBIND = getattr(socket, "IP_FREEBIND", 15)   # Linux: bind before Docker has made the company's bridge
CORES = Path("/opt/tico-cell")  # core-<vX.Y.Z> per release, where HQ installs them (tico-services hosting/cloud.py)
CORE_URL = "https://github.com/ticoteam/tico/archive/refs/tags/%s.tar.gz"
CORE_MAX = 256 << 20             # the archive as downloaded (a release's is a few MB)
CORE_TREE_MAX = 1 << 30          # and unpacked: a bad archive cannot fill the disk the companies share
CORE_MEMBERS = 100_000
CORE_FETCH_S = 300               # the whole download
CORE_RUN_S = 1800                # the core's own `cell update`: pull, stop, snapshot, the health wait (TimeoutStopSec)
CORE_NAME = re.compile(r"core-(v[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6})")
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


def host_core(folder: Path | None = None) -> str:
    """This core's release: .tico-installer.json (the bundle's), else its folder's name, core-vX.Y.Z, as HQ installs a
    core from GitHub's archive, which has no .tico-installer.json. "" when neither says."""
    if folder is None:
        found = C._installer_version()
        if found:
            return found
        folder = Path(C.__file__).resolve().parent.parent
    m = CORE_NAME.fullmatch(Path(folder).name)
    return m.group(1) if m else ""


class Refused(Exception):
    """A core that is not installed: the reason, for the status."""


def fetch_archive(url: str, dest: Path) -> None:
    """`url` into `dest` (a new file), at most CORE_MAX bytes and CORE_FETCH_S seconds in all."""
    deadline, n = time.monotonic() + CORE_FETCH_S, 0
    request = urllib.request.Request(url, headers={"User-Agent": "tico-cell-updater"})
    with urllib.request.urlopen(request, timeout=60) as r, open(dest, "xb") as f:
        while chunk := r.read(1 << 20):
            n += len(chunk)
            if n > CORE_MAX:
                raise Refused(f"the archive is over {CORE_MAX >> 20} MB")
            if time.monotonic() > deadline:
                raise Refused(f"the download took over {CORE_FETCH_S} s")
            f.write(chunk)


def _inside(path: str) -> bool:
    """A relative path that stays inside the folder it is taken from."""
    norm = posixpath.normpath(path)
    return not path.startswith("/") and norm != ".." and not norm.startswith("../")


def unpack(archive: Path, into: Path) -> None:
    """The files under the archive's one top folder, into `into` (which exists, empty): `tar --strip-components=1`,
    except that the whole archive is checked first. Refused for an absolute name or a `..`, a link that leads out of
    the tree, a device or FIFO, a second top folder, or more than CORE_MEMBERS files or CORE_TREE_MAX bytes. Python's
    "data" filter is the second lock on the same rules."""
    with tarfile.open(archive, "r:gz") as t:
        keep, total, top = [], 0, None
        for m in t:
            parts = PurePosixPath(m.name).parts
            if not parts or m.name.startswith("/") or ".." in parts:
                raise Refused(f"the archive has a member outside its folder: {m.name[:120]!r}")
            if not (m.isfile() or m.isdir() or m.issym() or m.islnk()):
                raise Refused(f"the archive has a device or FIFO: {m.name[:120]!r}")
            top = top or parts[0]
            if parts[0] != top:
                raise Refused("the archive has more than one top folder")
            rest = PurePosixPath(*parts[1:]) if len(parts) > 1 else None
            if rest is None:
                continue
            if m.issym() and not _inside(str(rest.parent / m.linkname)):
                raise Refused(f"the archive has a link out of its folder: {m.name[:120]!r}")
            if m.islnk():
                target = PurePosixPath(m.linkname).parts
                if m.linkname.startswith("/") or ".." in target or target[:1] != (top,) or len(target) < 2:
                    raise Refused(f"the archive has a link out of its folder: {m.name[:120]!r}")
                m.linkname = str(PurePosixPath(*target[1:]))
            total += max(m.size, 0)
            if len(keep) >= CORE_MEMBERS or total > CORE_TREE_MAX:
                raise Refused("the archive is too big")
            m.name = str(rest)
            keep.append(m)
        t.extractall(into, members=keep, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))


def run_child(argv: list[str], cwd: Path, env: dict, timeout: float) -> tuple[int, str]:
    """(exit code, stdout). stderr is not kept: the answer is the JSON line, scrubbed by the cell itself."""
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 127, ""
    return p.returncode, p.stdout


class Service:
    """Every company's updater state, the listeners, and the update threads. `make_cell` gives a fresh setup/cell.py
    Cell per use (tests pass one on a fake System); `core` is this host's core release ("" when unknown); `bind` and
    `subnet` are company i's address and the peers it answers (tests move both to loopback); `cores`, `fetch` and
    `run_core` are where release cores go, the download, and the child run of a core's `cell update`; `lead` is the
    wait before POST /sleep's work."""

    def __init__(self, root: Path, system: C.System | None = None, *, make_cell=None, core=None, bind=None,
                 subnet=None, cores=None, fetch=None, run_core=None, lead=SLEEP_LEAD):
        self.root, self.system = Path(root), system or C.System()
        self.make_cell = make_cell or (lambda: C.Cell(self.root, self.system))
        self.core = core or host_core
        self.cores, self.fetch, self.run_core = Path(cores or CORES), fetch or fetch_archive, run_core or run_child
        self.lead = lead                        # POST /sleep's work waits this long after its answer
        self.installing = threading.Lock()      # one core install at a time: two companies to one release share it
        self.bind = bind or (lambda index: (C.networks(index)["default_gateway"], C.UPDATER_PORT))
        self.subnet = subnet or (lambda index: C.networks(index)["default_subnet"])
        self.guard = threading.Lock()
        self.status: dict[str, dict] = {}
        self.errors: dict[str, collections.deque] = {}
        self.workers: set[threading.Thread] = set()
        self.sleeps: dict[str, dict] = {}       # slug -> the sleep reserved for it (under `guard`): {"wake_at", "committed"}
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
        if path == "/sleep":
            return 200, {"minutes": self.sleep_minutes(slug)}
        return 404, {"error": "not found"}

    def sleep_minutes(self, slug: str) -> int | None:
        """The company's sleep policy from its record, or None when there is none (or the record holds no valid one)."""
        c = self.make_cell().load(slug) or {}
        minutes = c.get("sleep_minutes")
        if isinstance(minutes, int) and not isinstance(minutes, bool) and 0 <= minutes <= C.SLEEP_MINUTES_MAX:
            return minutes
        return None

    def post(self, slug: str, index: int, path: str, body: bytes) -> tuple[int, dict]:
        if path == "/sleep":
            return self.sleep(slug, index, body)
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
            if (self.sleeps.get(slug) or {}).get("committed"):     # the server is stopping: update it when it wakes
                return 409, {"error": "an update is already running", **s,
                             "message": "This company is going to sleep; try again when it wakes."}
            if self.sleeps.pop(slug, None):     # a pending sleep gives way: its work finds no reservation and stops
                log(f"{slug}: sleep cancelled by an update to {version}")
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

    # --- sleep ------------------------------------------------------------------------------------------------

    def sleep(self, slug: str, index: int, body: bytes) -> tuple[int, dict]:
        """POST /sleep: the company's `cell sleep`, with its timed wake. The answer is 202 as accepted, and the work
        runs in a thread after `lead` seconds, so the server gets its answer before its own container stops. Refused
        (409 busy) while an update runs for the company, its sleep is committed, or another cell command holds its
        lock, as /update refuses. Accepted, it is the company's reserved sleep (`sleeps`): one still pending is
        replaced, so the latest wake_at wins and the earlier work finds itself replaced and stops."""
        try:
            asked = json.loads(body or b"{}")
        except ValueError:
            return 422, {"error": "bad_body"}
        if not isinstance(asked, dict):
            return 422, {"error": "bad_body"}
        try:
            wake_at = C.wake_at_text(asked.get("wake_at"))
        except C.CellError as e:
            return 422, {"error": e.code}
        if self.company(slug, index) is None:
            return 404, {"error": "not found"}
        with self.guard:
            if self.busy(slug):
                return 409, {"error": "busy"}
        try:                            # the same probe /update makes: staff's command holds the company, not now
            with self.make_cell().lock("company-" + slug, timeout=0):
                pass
        except C.CellError:
            return 409, {"error": "busy"}
        mine = {"wake_at": wake_at, "committed": False}
        worker = threading.Thread(target=self.sleep_work, args=(slug, mine), name="sleep-" + slug, daemon=True)
        with self.guard:                # again: an update may have been admitted during the probe
            if self.busy(slug):
                return 409, {"error": "busy"}
            self.sleeps[slug] = mine
            self.workers.add(worker)
        worker.start()
        return 202, {"state": "sleeping", "wake_at": wake_at}

    def busy(self, slug: str) -> bool:
        """An update under way for the company, or its sleep committed (under `guard`)."""
        return self.state(slug)["state"] in UNDER_WAY or bool((self.sleeps.get(slug) or {}).get("committed"))

    def sleep_work(self, slug: str, mine: dict) -> None:
        """`cell sleep` for the company, in this thread, after the lead. It commits under `guard` only while `mine` is
        still the company's reserved sleep: an update admitted meanwhile took it away (or a later sleep replaced it),
        and then nothing is stopped. Committed, an update is refused until the reservation is released here, however
        the sleep ends. A lock taken since the request is refused, not queued (as `work` does); a failure is logged,
        since the answer was already 202 and nothing else reports it."""
        cell = self.make_cell()
        cell.lock_wait = 0
        try:
            time.sleep(self.lead)
            with self.guard:
                if self.sleeps.get(slug) is not mine:
                    log(f"{slug}: sleep not run: an update or a later sleep came first")
                    return
                mine["committed"] = True
            cell.sleep(argparse.Namespace(company=slug, wake_at=mine["wake_at"]))
            log(f"{slug}: asleep" + (f" until {mine['wake_at']}" if mine["wake_at"] else ""))
        except C.CellError as e:
            log(f"{slug}: sleep refused: {e.code}: {cell.scrub(e.detail)[:300]}")
        except Exception as e:  # noqa: BLE001  logged, rather than a dead thread saying nothing
            log(f"{slug}: sleep failed: {cell.scrub(f'{type(e).__name__}: {e}')[:300]}")
        finally:
            with self.guard:
                if self.sleeps.get(slug) is mine:
                    del self.sleeps[slug]
                self.workers.discard(threading.current_thread())

    # --- the update -------------------------------------------------------------------------------------------

    def work(self, slug: str, version: str) -> None:
        cell = self.make_cell()
        cell.lock_wait = 0              # a command that took the company since the request: refused, not queued
        try:
            target, refused, newer = self.check(cell, slug, version)
            if refused:
                self.set(slug, state="failed", message="Not updated: " + refused)
                return
            folder = None
            if newer:
                try:
                    folder = self.install_core(target)
                except (Refused, OSError, tarfile.TarError) as e:
                    self.set(slug, state="failed", message=cell.scrub(f"Not updated: could not install the {target} "
                                                                       f"core: {e}")[:600])
                    return
            try:
                cell.pull(target)
            except C.CellError as e:
                self.set(slug, state="failed", message="Not updated: " + cell.scrub(e.detail))
                return
            self.set(slug, state="restarting")
            if folder is not None:
                return self.through_core(cell, slug, target, folder)
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

    def check(self, cell: C.Cell, slug: str, version: str) -> tuple[str, str, bool]:
        """(the release to run, why not or "", whether it needs its own core): latest looked up, then published, not
        older than the company runs. Newer than this host's core (or a core that does not say) runs with its own."""
        target = version
        if version == "latest":
            target = self.system.latest_release() or ""
            if not triple(target):
                return "", "could not look up the latest release", False
            target = tag(target)
        if target not in self.published:
            known = self.system.release_published(target)
            if known is None:
                return "", f"could not check that {target} is a published release; try again later", False
            if not known:
                return "", f"{target} is not a published release", False
            self.published.add(target)
        c = cell.load(slug)
        if not c or c.get("state") == "removed":
            return "", "the company is not on this host", False
        if triple(c["version"]) and triple(target) < triple(c["version"]):
            return "", f"{target} is older than {c['version']}, which it runs", False
        core = self.core()
        return target, "", not triple(core) or triple(target) > triple(core)

    def lag(self, version: str) -> str:
        return f"updated with core {version}; host init still at {self.core() or 'an unknown release'}"

    def install_core(self, target: str) -> Path:
        """Release `target`'s core folder, installed when missing as HQ installs one: GitHub's tag archive downloaded
        and unpacked into a staging folder beside it (.core-<tag>.<random>), then renamed into place, so a half-done
        install never looks installed. An existing folder is used as it is, never touched."""
        final = self.cores / f"core-{target}"
        with self.installing:
            if final.is_symlink() or (final.exists() and not final.is_dir()):
                raise Refused(f"{final} is not a folder")
            if not final.exists():
                self.cores.mkdir(mode=0o755, parents=True, exist_ok=True)
                stage = Path(tempfile.mkdtemp(prefix=f".core-{target}.", dir=self.cores))
                try:
                    tree = stage / "tree"
                    tree.mkdir(mode=0o755)
                    self.fetch(CORE_URL % target, stage / "core.tar.gz")
                    unpack(stage / "core.tar.gz", tree)
                    if not (tree / "setup" / "cell.py").is_file():
                        raise Refused("the archive has no setup/cell.py")
                    os.chmod(tree, 0o755)
                    os.rename(tree, final)
                finally:
                    shutil.rmtree(stage, ignore_errors=True)
            if not (final / "setup" / "cell.py").is_file():
                raise Refused(f"{final} has no setup/cell.py")
        return final

    def through_core(self, cell: C.Cell, slug: str, target: str, folder: Path) -> None:
        """That core's own `cell update` for this company, as a child (the company's compose.yaml then comes from the
        matching core), mapped to the status as the in-process update is. This worker holds no lock: the child takes
        the company's lock, as staff's command would."""
        snaps = self.root / "companies" / slug / "snapshots"
        before = set(os.listdir(snaps)) if snaps.is_dir() else set()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "TICO_"))}
        env["TICO_CELL_ROOT"] = str(self.root)
        code, out = self.run_core([sys.executable, "-m", "setup", "cell", "update", "--company", slug, "--version", target,
                                   "--json"], folder, env, CORE_RUN_S)
        answer = {}
        for line in reversed(out.splitlines()):
            with contextlib.suppress(ValueError):
                found = json.loads(line)
                if isinstance(found, dict):
                    answer = found
                    break
        new = sorted((set(os.listdir(snaps)) if snaps.is_dir() else set()) - before)
        taken = new[-1] if new else ""
        error, detail = str(answer.get("error") or ""), cell.scrub(str(answer.get("detail") or ""))[:600]
        if answer.get("ok") is True:
            self.set(slug, state="healthy", message=self.lag(target) + ".", snapshot=taken)
        elif error == "update_failed":
            self.set(slug, state="rolled_back", message=detail, snapshot=taken, restored=True)
        elif error in ("rollback_failed", "unhealthy"):
            self.set(slug, state="failed", message=detail, snapshot=taken)
        elif error:
            self.set(slug, state="failed", message="Not updated: " + detail)
        else:
            self.set(slug, state="failed", message=(
                f"The {target} core's `cell update` gave no answer (exit {code}"
                + (f", stopped after {CORE_RUN_S // 60} minutes" if code == 124 else "")
                + "). `cell status` says where the company is; its next start finishes a pending update or rolls it back."))

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
        core, c = self.core(), cell.load(slug) or {}
        runs = c.get("version") if isinstance(c.get("version"), str) else ""
        note = self.lag(runs) if triple(runs) and (not triple(core) or triple(runs) > triple(core)) else ""
        return {"mode": "cell", "version": core, "docker": out(["docker", "version", "--format", "{{.Server.Version}}"]),
                "compose": out(["docker", "compose", "version", "--short"]), "containers": containers, "errors": recent,
                "note": note}

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
