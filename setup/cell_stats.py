"""Cell stats: what a shared host ("cell") and every company on it is doing, pushed to HQ each minute (docs/cell-stats.md).

    python3 -m setup.cell_stats enroll --hq https://updates.tico.team --host-id cell-1    # the key on stdin, as root
    python3 -m setup.cell_stats enroll --uninstall
    python3 -m setup.cell_stats push                     # what tico-cell-stats.timer runs every 60 s
    python3 -m setup.cell_stats show [--company X] [--json]
    python3 -m setup.cell_stats debug-run TOOL [--args JSON]

The host only calls out: one HTTPS POST of the snapshot a minute, whose answer carries the debug requests staff queued
(each handed out once), then one POST per result. Nothing listens. The snapshot holds counters, timings, SQL shapes
(literals folded by the server), route templates and scrubbed log lines; never a row of company data, never a
container's environment, never a key.

Light by design: one `docker ps -a`, one `docker inspect` (of the cell's containers, with a template that never asks for
Config.Env) and one `docker stats --no-stream` per push, all in parallel with the /proc/stat interval; the error lines
come from each container's json-file log on the host, not one `docker logs` per company; database sizes and free
pages come from the file's header. `docker system df` and the top SQL shapes (read with SQLite inside each company's
server container, never on the host) run at most every 15 minutes (cached). The flight numbers are the core's own
`backend/flight_file.summary`, loaded by path (stdlib only), so they read the same while a server is stuck.

Stdlib only: the host's python3 runs this from the core release folder (/opt/tico-cell/stats-core, a link `push` keeps
on the newest /opt/tico-cell/core-v*). Every outside call is an argument list, never a shell string.
"""
from __future__ import annotations

import argparse
import calendar
import concurrent.futures as cf
import errno
import getpass
import gzip
import http.client
import importlib.util
import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import cell, contract
from .cell import Result

FORMAT = 1
CONFIG = "etc/tico-cell/stats.json"                 # under the host root: {"hq", "host_id", "key"}, 0600
STATE_DIR = "var/lib/tico-cell"
CACHE = STATE_DIR + "/stats-cache.json"             # the slow numbers between their refreshes
QUEUE = STATE_DIR + "/stats-debug.json"             # debug requests handed out but not run yet (the tick ran out)
CORE_ROOT = "opt/tico-cell"
CORE_LINK = CORE_ROOT + "/stats-core"               # the units' WorkingDirectory
UNIT_DIR = "etc/systemd/system"
SERVICE, TIMER = "tico-cell-stats.service", "tico-cell-stats.timer"

KEY_RE = re.compile(r"tch_[A-Za-z0-9_-]{43}")
HOST_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
DEBUG_ID_RE = re.compile(r"dbg_[0-9a-f]{16}")
VERSION_DIR_RE = re.compile(r"core-(v\d+\.\d+\.\d+)")

MAX_BODY = 512 * 1024           # the snapshot, uncompressed
MAX_OUTPUT = 256 * 1024         # one debug result
WINDOW_S = 300                  # the flight window
ERROR_SINCE_S = 70              # log lines this recent (a push a minute, with some overlap)
ERRORS_MAX, ERROR_CHARS = 20, 300
SLOW_EVERY = 15 * 60            # docker system df, top SQL shapes, attachment sizes
SLOW_ROUTE_P95_MS, SLOW_ROUTE_MAX_MS = 500, 1000
SQL_TOP = 10
HEALTH_TIMEOUT = 3
PUSH_TIMEOUT = 15
TICK_BUDGET = 45                # seconds of debug tools per tick (TimeoutStartSec is 50)
TOOL_MIN = 5                    # a tool is not started with less than this left; it waits for the next tick
TUNNEL_CONTAINER, TUNNEL_METRICS = "tico-cell-tunnel", 8081
ERROR_RE = re.compile(r"ERROR|CRITICAL|Traceback|Exception")
STATUS_FILE = "tmp/tico-backup-status.json"         # inside the server container (backend/replication.py)
DOCKER_TIMEOUT = 20

# Only these fields of `docker inspect`: never Config.Env, never the mounts (the `containers` tool asks for those).
INSPECT_FORMAT = ('{"id":{{json .Id}},"name":{{json .Name}},"state":{{json .State}},"restarts":{{json .RestartCount}},'
                  '"memory":{{json .HostConfig.Memory}},"log":{{json .LogPath}},"labels":{{json .Config.Labels}}}')
DEBUG_INSPECT_FORMAT = ('{"name":{{json .Name}},"image":{{json .Config.Image}},"created":{{json .Created}},'
                        '"state":{{json .State}},"restarts":{{json .RestartCount}},'
                        '"host_config":{"memory":{{json .HostConfig.Memory}},"nano_cpus":{{json .HostConfig.NanoCpus}},'
                        '"pids_limit":{{json .HostConfig.PidsLimit}},"restart":{{json .HostConfig.RestartPolicy}},'
                        '"read_only":{{json .HostConfig.ReadonlyRootfs}},"cap_drop":{{json .HostConfig.CapDrop}},'
                        '"cap_add":{{json .HostConfig.CapAdd}}},"labels":{{json .Config.Labels}},'
                        '"mounts":{{json .Mounts}}}')
LABELS_KEPT = ("com.docker.compose.project", "com.docker.compose.service", cell.LABEL)
MOUNT_SOURCES = ("/srv/", "/var/lib/docker/")


# --- the scrubber ---------------------------------------------------------------------------------------------------

def _long_run(m: re.Match) -> str:
    s = m.group(0)
    if re.search(r"\d", s) and re.search(r"[a-z]", s) and re.search(r"[A-Z]", s):
        return "[redacted]"
    return s


_SCRUB = [
    # A secret's name and its separator: the rest of that line goes, whatever it holds. No try at finding where the
    # value ends: it may be quoted, escaped once (\") or more (\\\") inside JSON a log line carries, and hold escaped
    # quotes itself. Losing the rest of a line that names a secret is the price.
    (re.compile(r"(?i)\b((?:set-)?cookie|authorization|[\w-]*(?:key|token|secret|password|passwd|session))"
                r"((?:\\*[\"'])?\s*[=:]\s*)[^\r\n]+"), r"\1\2[redacted]"),
    (re.compile(r"(?i)\bbearer\s+\S+"), "Bearer [redacted]"),
    (re.compile(r"\b(?:tch|tcs|tgi)_[A-Za-z0-9_-]{8,}"), "[redacted]"),
    (re.compile(r"\btico_[A-Za-z0-9_-]{16,}"), "[redacted]"),
    (re.compile(r"\bsk[-_][A-Za-z0-9_-]{12,}"), "[redacted]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), "[redacted]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}"), "[redacted]"),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{8,}"), "[redacted]"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}"), "[redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{6,}(?:\.[A-Za-z0-9_-]*){0,2}"), "[redacted]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"), "[redacted]"),
    (re.compile(r"(https?://[^\s?#\"'<>]*)\?[^\s\"'<>]+"), r"\1?[redacted]"),
    (re.compile(r"(?<=[\w/.%~-])\?[^\s\"'<>?]*=[^\s\"'<>]*"), "?[redacted]"),
    (re.compile(r"(?<![A-Za-z0-9+/_-])[0-9a-fA-F]{32,}(?![A-Za-z0-9+/_-])"), "[redacted]"),
    (re.compile(r"[A-Za-z0-9+/_-]{32,}={0,2}"), _long_run),
]


def scrub(text) -> str:
    """Every free-text string that leaves the host goes through here: header values, bearer tokens, key=value secrets,
    known key prefixes, JWTs, email addresses, URL query strings and long base64/hex runs become [redacted]."""
    text = str(text)
    for pattern, repl in _SCRUB:
        text = pattern.sub(repl, text)
    return text


# --- reading what a company controls ----------------------------------------------------------------------------
# This runs as root over folders a company's server writes (its data volume, its container's root through /proc). A
# symlink planted there, as the file itself or as any folder on the way, could point at /etc/shadow, at this host's
# stats key or at another company's data. So those reads start from a folder the host made (the company's folder under
# the cell root, or /proc/<pid>/root as the kernel resolves it), walk down one name at a time without following any
# symlink, and take only a regular file of a bounded size. A path string is never handed to open() below that root.

FILE_CAP = 64 * 1024 * 1024     # the largest company-written file read (a flight file rotates at 10 MB)
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)   # NONBLOCK: a FIFO can't hang us


class Unsafe(OSError):
    """Not a regular file reached without a symlink, or too big."""


def _parts(rel: str | Path) -> list[str]:
    rel = str(rel)
    parts = [x for x in rel.split("/") if x not in ("", ".")]
    if rel.startswith("/") or not parts or ".." in parts:
        raise Unsafe(f"not a plain relative path: {rel!r}")
    return parts


def open_dir(root: str | Path, rel: str | Path = "") -> int:
    """A file descriptor for the folder root/rel, every name below root opened with O_NOFOLLOW; the caller closes it."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    try:
        for name in _parts(rel) if str(rel).strip("/.") else []:
            nxt = os.open(name, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def safe_open(root: str | Path, rel: str | Path, cap: int | None = FILE_CAP):
    """The regular file root/rel opened for binary reading, with no symlink followed below root (Unsafe otherwise, or
    when it is bigger than `cap`; FileNotFoundError when it is not there). The one way this module opens a file a
    company could have planted."""
    parts = _parts(rel)
    fd = open_dir(root, "/".join(parts[:-1]))
    try:
        leaf = os.open(parts[-1], _FILE_FLAGS, dir_fd=fd)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise Unsafe(f"{rel} is a symlink") from e
        raise
    finally:
        os.close(fd)
    try:
        st = os.fstat(leaf)
        if not stat.S_ISREG(st.st_mode):
            raise Unsafe(f"{rel} is not a regular file")
        if cap is not None and st.st_size > cap:
            raise Unsafe(f"{rel} is bigger than {cap} bytes")
        os.set_blocking(leaf, True)
        return os.fdopen(leaf, "rb")
    except BaseException:
        os.close(leaf)
        raise


def safe_read(root: str | Path, rel: str | Path, limit: int, offset: int = 0, cap: int | None = FILE_CAP) -> bytes:
    with safe_open(root, rel, cap) as f:
        f.seek(offset)
        return f.read(limit)


def safe_stat(root: str | Path, rel: str | Path) -> os.stat_result:
    """os.stat of a regular file reached as safe_open reaches it (any size)."""
    with safe_open(root, rel, None) as f:
        return os.fstat(f.fileno())


def safe_listdir(root: str | Path, rel: str | Path) -> list[str]:
    fd = open_dir(root, rel)
    try:
        return os.listdir(fd)
    finally:
        os.close(fd)


def flight_opener(root: str | Path, rel: str | Path):
    """For flight_file.records/summary: each file of the flight folder root/rel opened through safe_open."""
    base = "/".join(_parts(rel))
    return lambda name: safe_open(root, f"{base}/{name}")


# --- the box: every call outside this process (the tests replace it) ---------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):      # never carry the key to another address
        return None


class Box:
    def __init__(self, host_root: str | Path = "/", cell_root: str | Path | None = None):
        self.host = Path(host_root)
        self.cell_root = Path(cell_root or os.environ.get("TICO_CELL_ROOT") or cell.ROOT)

    def path(self, p: str | Path) -> Path:
        return self.host / str(p).lstrip("/")

    def read(self, p: str | Path, limit: int = 1 << 20) -> str:
        with open(self.path(p), "rb") as f:
            return f.read(limit).decode("utf-8", "replace")

    def run(self, argv: list[str], timeout: float = DOCKER_TIMEOUT, cwd: str | None = None) -> Result:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "TICO_"))}
        try:
            p = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout, env=env,
                               check=False, stdin=subprocess.DEVNULL, cwd=cwd)
        except FileNotFoundError:
            return Result(127, "", f"{argv[0]}: not found")
        except subprocess.TimeoutExpired:
            return Result(124, "", f"{argv[0]}: timed out after {timeout:.0f}s")
        return Result(p.returncode, p.stdout, p.stderr)

    def http_get(self, host: str, path: str, port: int = cell.ROUTER_PORT,
                 timeout: float = HEALTH_TIMEOUT) -> tuple[int | None, int | None, bytes]:
        """(status, ms, the first 4 KB of the body) from 127.0.0.1:port with this Host header; (None, None, b"") when
        nothing answers in time."""
        t = time.monotonic()
        try:
            c = http.client.HTTPConnection(cell.ROUTER_ADDRESS, port, timeout=timeout)
            c.request("GET", path, headers={"Host": host, "User-Agent": "tico-cell-stats"})
            r = c.getresponse()
            body = r.read(4096)
            c.close()
            return r.status, int((time.monotonic() - t) * 1000), body
        except (OSError, http.client.HTTPException):
            return None, None, b""

    def post(self, url: str, body: bytes, headers: dict, timeout: float = PUSH_TIMEOUT) -> tuple[int, dict, bytes]:
        """(status, headers, body); status 0 when HQ could not be reached."""
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read(1 << 20)
        except urllib.error.HTTPError as e:
            try:
                data = e.read(1 << 16)
            except OSError:
                data = b""
            return e.code, dict(e.headers or {}), data
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            return 0, {}, str(getattr(e, "reason", e)).encode()

    def statvfs(self, p: str):
        return os.statvfs(self.path(p))

    def kill(self, pid: int, sig: int) -> None:
        os.kill(pid, sig)

    def euid(self) -> int:
        return os.geteuid()

    def time(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, s: float) -> None:
        time.sleep(s)


# --- small helpers --------------------------------------------------------------------------------------------------

def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def parse_iso(text) -> float | None:
    """'2026-10-30T12:00:00.123456789Z' (Docker, the backup status) to Unix seconds; None for zero times and junk."""
    t = str(text)[:19]
    if t.startswith("0001"):
        return None
    try:
        return float(calendar.timegm(time.strptime(t, "%Y-%m-%dT%H:%M:%S")))
    except (ValueError, TypeError):
        return None


_UNITS = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3,
          "tib": 1024 ** 4}


def size_bytes(text) -> float | None:
    """Docker's human sizes ('612.3MiB', '1.5kB', '0B', '41.2GB') to bytes."""
    m = re.fullmatch(r"\s*([0-9.]+)\s*([A-Za-z]*)\s*", str(text))
    if not m:
        return None
    unit = (m.group(2) or "b").lower()
    if unit not in _UNITS:
        return None
    try:
        return float(m.group(1)) * _UNITS[unit]
    except ValueError:
        return None


def _pair(text) -> tuple[float | None, float | None]:
    a, _, b = str(text).partition("/")
    return size_bytes(a), size_bytes(b)


def _r(v, digits=1):
    return None if v is None else round(v, digits)


def memory_mb(text) -> int | None:
    m = cell.MEMORY_RE.fullmatch(str(text or ""))
    if not m:
        return None
    return int(m.group(1)) * (1024 if m.group(2) == "g" else 1)


def labels_of(text) -> dict:
    return dict(p.split("=", 1) for p in str(text or "").split(",") if "=" in p)


def _version_key(v: str) -> tuple:
    return tuple(int(x) for x in v.lstrip("v").split("."))


def _cut(text: str, limit: int) -> str:
    data = text.encode()
    if len(data) <= limit:
        return text
    return data[:limit - 80].decode("utf-8", "ignore") + f"\n… [cut: {len(data) - limit + 80} more bytes]"


def _read_json(path: Path) -> dict:
    try:
        v = json.loads(path.read_text())
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_private(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    cell._write_atomic(path, json.dumps(data, separators=(",", ":"), sort_keys=True) + "\n", 0o600)


_FLIGHT = None


def flight_file():
    """The core's backend/flight_file.py, loaded by path: stdlib only, and none of backend/__init__'s imports."""
    global _FLIGHT
    if _FLIGHT is None:
        path = Path(__file__).resolve().parent.parent / "backend" / "flight_file.py"
        spec = importlib.util.spec_from_file_location("tico_cell_flight_file", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _FLIGHT = mod
    return _FLIGHT


def own_version() -> str | None:
    return cell._installer_version() or None


def core_dirs(box: Box) -> list[tuple[str, Path]]:
    """(version, folder) of every /opt/tico-cell/core-vX.Y.Z, newest first."""
    out = []
    try:
        for p in box.path(CORE_ROOT).iterdir():
            m = VERSION_DIR_RE.fullmatch(p.name)
            if m and p.is_dir():
                out.append((m.group(1), p))
    except OSError:
        pass
    return sorted(out, key=lambda vp: _version_key(vp[0]), reverse=True)


def core_version(box: Box) -> str | None:
    dirs = core_dirs(box)
    if dirs:
        return dirs[0][0]
    v = _read_json(box.path(CORE_ROOT) / "core" / ".tico-installer.json").get("version")
    return v if isinstance(v, str) else None


def point_core_link(box: Box, fallback: Path | None = None) -> Path | None:
    """Keep /opt/tico-cell/stats-core on the newest core-v* folder that has this collector (HQ installs a release in
    its own folder and never touches older ones), else on `fallback`. A new release is picked up on the next tick."""
    target = next((p for _, p in core_dirs(box) if (p / "setup" / "cell_stats.py").is_file()), None) or fallback
    if target is None:
        return None
    link = box.path(CORE_LINK)
    try:
        if link.is_symlink() and os.readlink(link) == str(target):
            return target
        link.parent.mkdir(parents=True, exist_ok=True)
        tmp = link.with_name(f".{link.name}.{os.getpid()}")
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(str(target), tmp)
        os.replace(tmp, link)
    except OSError:
        return None
    return target


# --- docker, once for all containers ------------------------------------------------------------------------------

def docker_ps(box: Box) -> list[dict]:
    r = box.run(["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"])
    rows = []
    for line in r.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            row["_labels"] = labels_of(row.get("Labels"))
            rows.append(row)
    return rows


def docker_inspect(box: Box, ids: list[str], fmt: str = INSPECT_FORMAT) -> list[dict]:
    if not ids:
        return []
    r = box.run(["docker", "inspect", "--format", fmt, *ids])     # a container gone since ps: the rest still print
    out = []
    for line in r.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            row["name"] = str(row.get("name") or "").lstrip("/")
            out.append(row)
    return out


def docker_stats(box: Box, names: list[str] | None = None) -> dict[str, dict]:
    r = box.run(["docker", "stats", "--no-stream", "--no-trunc", "--format", "{{json .}}", *(names or [])], timeout=30)
    out = {}
    for line in r.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        mem, limit = _pair(row.get("MemUsage"))
        rx, tx = _pair(row.get("NetIO"))
        rd, wr = _pair(row.get("BlockIO"))
        try:
            cpu = float(str(row.get("CPUPerc", "")).rstrip("%"))
        except ValueError:
            cpu = None
        try:
            pids = int(row.get("PIDs"))
        except (TypeError, ValueError):
            pids = None
        out[str(row.get("Name", "")).lstrip("/")] = {
            "cpu_pct": _r(cpu), "mem_mb": None if mem is None else round(mem / 2 ** 20),
            "mem_limit_mb": None if limit is None else round(limit / 2 ** 20), "pids": pids,
            "net_rx_mb": _r(rx and rx / 1e6), "net_tx_mb": _r(tx and tx / 1e6),
            "blk_read_mb": _r(rd and rd / 1e6), "blk_write_mb": _r(wr and wr / 1e6)}
    return out


def docker_disk(box: Box) -> tuple[float | None, str | None]:
    """(GB Docker uses: images, containers, volumes, build cache; its root folder)."""
    r = box.run(["docker", "system", "df", "--format", "{{json .}}"], timeout=40)
    total, seen = 0.0, False
    for line in r.stdout.splitlines():
        try:
            n = size_bytes(json.loads(line).get("Size"))
        except (ValueError, AttributeError):
            continue
        if n is not None:
            total, seen = total + n, True
    root = box.run(["docker", "info", "--format", "{{.DockerRootDir}}"]).stdout.strip() or None
    return (round(total / 1e9, 1) if seen else None), root


# --- the host -------------------------------------------------------------------------------------------------------

def cpu_times(box: Box) -> list[int] | None:
    try:
        first = box.read("proc/stat", 4096).splitlines()[0].split()
        return [int(x) for x in first[1:9]] if first[0] == "cpu" else None
    except (OSError, IndexError, ValueError):
        return None


def cpu_share(a: list[int] | None, b: list[int] | None) -> dict:
    """user nice system idle iowait irq softirq steal, between two /proc/stat reads."""
    if not a or not b:
        return {"cpu_pct": None, "iowait_pct": None, "steal_pct": None}
    d = [y - x for x, y in zip(a, b, strict=False)]
    total = sum(d)
    if total <= 0:
        return {"cpu_pct": None, "iowait_pct": None, "steal_pct": None}
    return {"cpu_pct": round(100 * (total - d[3] - d[4]) / total, 1), "iowait_pct": round(100 * d[4] / total, 1),
            "steal_pct": round(100 * d[7] / total, 1)}


def _meminfo(box: Box) -> dict:
    out = {}
    try:
        for line in box.read("proc/meminfo", 16384).splitlines():
            k, _, v = line.partition(":")
            parts = v.split()
            if parts:
                out[k.strip()] = int(parts[0])      # kB
    except (OSError, ValueError):
        pass
    return out


def host_block(box: Box, cache: dict, df: tuple, stat_a, stat_b) -> dict:
    mem = _meminfo(box)
    try:
        load = [float(x) for x in box.read("proc/loadavg", 256).split()[:3]]
    except (OSError, ValueError):
        load = None
    try:
        uptime = int(float(box.read("proc/uptime", 256).split()[0]))
    except (OSError, ValueError, IndexError):
        uptime = None
    try:
        kernel = box.read("proc/sys/kernel/osrelease", 256).strip() or None
    except OSError:
        kernel = None
    disk = {"disk_total_gb": None, "disk_free_gb": None, "inodes_free_pct": None}
    try:
        v = box.statvfs("/")
        disk = {"disk_total_gb": round(v.f_blocks * v.f_frsize / 1e9, 1),
                "disk_free_gb": round(v.f_bavail * v.f_frsize / 1e9, 1),
                "inodes_free_pct": round(100 * v.f_favail / v.f_files, 1) if v.f_files else None}
    except OSError:
        pass
    out = {"cpus": _cpus(box), "load": load, **cpu_share(stat_a, stat_b),
           "mem_total_mb": mem.get("MemTotal") and round(mem["MemTotal"] / 1024),
           "mem_avail_mb": mem.get("MemAvailable") and round(mem["MemAvailable"] / 1024),
           "swap_used_mb": round((mem["SwapTotal"] - mem.get("SwapFree", 0)) / 1024) if "SwapTotal" in mem else None,
           **disk, "docker_disk_gb": df[0], "uptime_s": uptime, "kernel": kernel, "oom_kills_1h": oom_kills(box, cache)}
    root = df[1]
    if root and root != "/":
        try:
            if os.stat(box.path(root)).st_dev != os.stat(box.path("/")).st_dev:
                v = box.statvfs(root)
                out["docker_root_free_gb"] = round(v.f_bavail * v.f_frsize / 1e9, 1)
        except OSError:
            pass
    return out


def _cpus(box: Box) -> int | None:
    try:
        return sum(1 for line in box.read("proc/stat", 1 << 16).splitlines() if re.match(r"cpu\d+ ", line)) or None
    except OSError:
        return None


def oom_kills(box: Box, cache: dict) -> int | None:
    """OOM kills in the last hour, from the kernel's counter (/proc/vmstat oom_kill) and the samples kept in the cache."""
    try:
        count = next(int(line.split()[1]) for line in box.read("proc/vmstat", 1 << 16).splitlines()
                     if line.startswith("oom_kill "))
    except (OSError, StopIteration, ValueError, IndexError):
        return None
    now = box.time()
    samples = [s for s in cache.get("oom", []) if isinstance(s, list) and len(s) == 2 and now - s[0] <= 3900]
    samples.append([now, count])
    cache["oom"] = samples[-70:]
    base = next((c for t, c in samples if now - t >= 3540), samples[0][1])
    return max(0, count - base) if count >= base else count       # a reboot started the counter again


# --- one company -----------------------------------------------------------------------------------------------------

def _stale(entry, now: float) -> bool:
    return not isinstance(entry, dict) or now - (entry.get("at") or 0) >= SLOW_EVERY


DB = "data/hub.sqlite"      # under the company's folder


def parse_header(head: bytes, size: int) -> dict | None:
    """Page size, page count and free pages from a SQLite file's 100-byte header (sqlite.org/fileformat.html); None
    when it is not one. The header's page count is used only while its version-valid-for number matches the change
    counter, else the file size says it. Pages still in the WAL are not counted."""
    if len(head) < 100 or not head.startswith(b"SQLite format 3\x00"):
        return None
    page = int.from_bytes(head[16:18], "big")
    page = 65536 if page == 1 else page
    if page < 512 or page & (page - 1):
        return None
    pages = int.from_bytes(head[28:32], "big")
    if not pages or head[24:28] != head[92:96]:
        pages = size // page
    return {"page_size": page, "page_count": pages, "freelist_count": int.from_bytes(head[36:40], "big")}


def db_header(root: Path) -> dict | None:
    """The database's header numbers and its and the WAL's sizes, read through safe_open: no SQLite on the host."""
    try:
        with safe_open(root, DB, None) as f:
            size = os.fstat(f.fileno()).st_size
            head = parse_header(f.read(100), size)
    except OSError:
        return None
    try:
        wal = safe_stat(root, DB + "-wal").st_size
    except FileNotFoundError:
        wal = 0
    except OSError:
        wal = None
    return {**(head or {}), "size": size, "wal": wal}


# What reads the database with SQLite runs in the company's own server container, as the container's user (docker exec
# with no -u), with the server's python and a read-only URI: a symlink in there reaches only the container's own
# files. The script is fixed; its arguments are a mode, the database's path in the container and numbers this module
# made or validated. Its answer is one JSON line, checked like anything else from a company.
EXEC_PYTHON = "/opt/tico/.venv/bin/python"
CONTAINER_DB = "/data/hub.sqlite"
DB_SCRIPT = r"""
import json, sqlite3, sys, time
mode, db, budget = sys.argv[1], sys.argv[2], float(sys.argv[3])
start = time.monotonic()
c = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=1)
c.execute("PRAGMA query_only=1")
def cap(seconds):
    stop = min(start + budget, time.monotonic() + seconds)
    c.set_progress_handler(lambda: 1 if time.monotonic() > stop else 0, 20000)
out = {}
if mode == "top":
    cap(budget)
    try:
        out["sql_top"] = c.execute("SELECT fingerprint, SUM(n), SUM(total_ms), MAX(max_ms) FROM flight_sql WHERE created_at>=? "
                                   "GROUP BY fingerprint ORDER BY 3 DESC LIMIT ?",
                                   (int(sys.argv[4]), int(sys.argv[5]))).fetchall()
    except sqlite3.Error as e:
        out["error"] = str(e)
elif mode == "db":
    cap(budget)
    for p in ("page_size", "page_count", "freelist_count", "journal_mode", "wal_autocheckpoint"):
        try:
            out[p] = c.execute("PRAGMA " + p).fetchone()[0]
        except sqlite3.Error as e:
            out[p] = "stopped: %s" % e
    cap(20)
    try:
        out["largest"] = [{"name": n, "mb": round(b / 1e6, 2)} for n, b in c.execute(
            "SELECT name, SUM(pgsize) FROM dbstat GROUP BY name ORDER BY 2 DESC LIMIT 15").fetchall()]
    except sqlite3.Error as e:
        out["largest"] = "not available (%s)" % e
    cap(budget)
    try:
        out["quick_check"] = [r[0] for r in c.execute("PRAGMA quick_check").fetchmany(50)]
    except sqlite3.Error as e:
        out["quick_check"] = "stopped: %s" % e
c.close()
print(json.dumps(out))
"""
SQL_TOP_BUDGET = 5


def exec_db(box: Box, name: str, mode: str, budget: float, *numbers: int) -> tuple[dict | None, str]:
    """DB_SCRIPT in the server container `name`: (its answer, "") or (None, why)."""
    if mode not in ("top", "db") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise Refused("not a database read")
    argv = ["docker", "exec", name, EXEC_PYTHON, "-I", "-c", DB_SCRIPT, mode, CONTAINER_DB, str(int(budget))]
    r = box.run(argv + [str(int(n)) for n in numbers], timeout=budget + 5)
    if r.returncode:
        return None, scrub(f"the read in the server container failed ({r.returncode}): {r.stderr.strip()[-300:]}")
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1][:MAX_OUTPUT])
    except (ValueError, IndexError):
        return None, "the read in the server container gave no answer"
    return (out, "") if isinstance(out, dict) else (None, "the read in the server container gave no answer")


def sql_top(box: Box, name: str | None, now: float) -> list | None:
    """The top SQL shapes of this hour and the last (flight_sql), read in the server container; None without one."""
    if not name:
        return None
    out, _ = exec_db(box, name, "top", SQL_TOP_BUDGET, int(now) // 3600 * 3600 - 3600, SQL_TOP)
    rows = (out or {}).get("sql_top")
    if not isinstance(rows, list):
        return None
    top = []
    for r in rows[:SQL_TOP]:
        if (isinstance(r, list) and len(r) == 4 and isinstance(r[0], str)
                and all(isinstance(x, (int, float)) or x is None for x in r[1:])):
            top.append({"shape": scrub(r[0])[:200], "count": r[1], "total_ms": round(r[2] or 0),
                        "max_ms": round(r[3] or 0)})
    return top


def files_mb(root: Path, rel: str, limit: int = 200_000) -> float | None:
    """The bytes of regular files under root/rel, in MB; every folder is opened with open_dir, so no symlink is
    followed, and entries are stat'ed without following."""
    try:
        os.close(open_dir(root, rel))
    except OSError:
        return None
    total, seen, stack = 0, 0, [rel]
    while stack and seen < limit:
        here = stack.pop()
        try:
            fd = open_dir(root, here)
        except OSError:
            continue
        try:
            with os.scandir(fd) as it:
                for e in it:
                    seen += 1
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(f"{here}/{e.name}")
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            pass
        finally:
            os.close(fd)
    return round(total / 1e6, 1)


def db_block(box: Box, root: Path, slug: str, cache: dict, now: float, server: str | None = None) -> tuple:
    """Sizes and free pages from the file each push; the top SQL shapes every 15 minutes, from the running server
    container `server` (left out while it is not running, and asked again on the next push)."""
    h = db_header(root)
    slow = cache.setdefault("db", {}).get(slug)
    if _stale(slow, now) and server and h:
        slow = cache["db"][slug] = {"at": now, "sql_top": sql_top(box, server, now)}
    blobs = cache.setdefault("files", {}).get(slug)
    if _stale(blobs, now):
        blobs = cache["files"][slug] = {"at": now, "mb": files_mb(root, "data/blobs")}
    mb = (lambda b: round(b / 1e6, 1) if isinstance(b, int) else None)
    pages, free = (h or {}).get("page_count"), (h or {}).get("freelist_count")
    return ({"size_mb": mb((h or {}).get("size")), "wal_mb": mb((h or {}).get("wal")),
             "freelist_pct": round(100 * free / pages, 1) if pages and free is not None else None,
             "files_mb": blobs.get("mb")}, (slow or {}).get("sql_top") if server else None)


def backup_block(box: Box, pid: int | None, now: float) -> dict | None:
    """The backup as the server's own backup loop last saw it (/tmp/tico-backup-status.json in the server container, read
    through /proc/<pid>/root: no docker exec). `age_s` is the newest copy's age, for display. `behind_s` is how long the
    backup location has been behind the database, null when caught up, and `stale` says it is past the warning time the
    server wrote (`warn_after_s`, ten sync intervals at least ten minutes). `checked_s` is how long ago the loop last read
    the backup location; past the warning time it is stale too. `unsent_files` is the local pile-up. A quiet
    database is never stale, whatever its copy's age: it has nothing new to send."""
    if not pid:
        return None
    try:
        saved = json.loads(safe_read(box.path(f"proc/{int(pid)}/root"), STATUS_FILE, 65536, cap=65536))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict):
        return None
    unsent = saved.get("unsent_files") if isinstance(saved.get("unsent_files"), int) else None
    at = parse_iso(saved.get("last_replicated_at"))
    since = parse_iso(saved.get("behind_since"))
    warn_after = saved["warn_after_s"] if isinstance(saved.get("warn_after_s"), int) else 600
    behind = None if since is None else max(0, int(now - since))
    checked = parse_iso(saved.get("checked_at"))
    checked_s = None if checked is None else max(0, int(now - checked))
    stale = (behind is not None and behind > warn_after) or (checked_s is not None and checked_s > warn_after)
    return {"age_s": None if at is None else max(0, int(now - at)), "behind_s": behind, "checked_s": checked_s,
            "stale": stale, "unsent_files": unsent, "detail": "litestream"}


FLIGHT = "data/flight"      # under the company's folder


def flight_summary(root: Path, since_s: float, now: float, **kw) -> dict | None:
    """flight_file.summary of the company's flight folder, every file opened through safe_open; None without one."""
    try:
        os.close(open_dir(root, FLIGHT))
    except OSError:
        return None
    return flight_file().summary(root / FLIGHT, since_s, now=now, opener=flight_opener(root, FLIGHT), **kw)


def flight_block(root: Path, now: float, top) -> dict | None:
    s = flight_summary(root, WINDOW_S, now, stall_limit=3, stack_frames=1)
    if s is None:
        return None
    minutes = s["minutes"]
    last = minutes[-1] if minutes else {}
    req = s.get("requests") or {}
    slow = [{"route": scrub(r["route"]), "count": r["n"], "p95_ms": round(r["p95"]), "max_ms": round(r["max_ms"]),
             "total_ms": round(r["total_ms"])}
            for r in s["routes"] if r["p95"] >= SLOW_ROUTE_P95_MS or r["max_ms"] >= SLOW_ROUTE_MAX_MS][:10]
    stall = s["stalls"][-1] if s["stalls"] else None
    return {
        "window_s": WINDOW_S,
        "requests": {"count": req.get("count"), "p50_ms": req.get("p50"), "p95_ms": req.get("p95"),
                     "p99_ms": req.get("p99"), "errors_5xx": req.get("errors")},
        "slow_routes": slow,
        "sql": {"total_ms": round(sum(m.get("sql_ms") or 0 for m in minutes)) if minutes else None,
                "lock_wait_ms": s["lock_waits"]["wait_ms"], "lock_hold_ms_max": s["peaks"]["hold_max"],
                "locked_errors": s["lock_waits"]["locked"], "top": top},
        "process": {"cpu_pct": last.get("cpu"), "rss_mb": round(last["rss"] / 2 ** 20) if last.get("rss") else None,
                    "loop_lag_ms_max": s["peaks"]["lag_max"], "threads": last.get("threads")},
        "stalls": {"count": s["stalls_total"],
                   "last": {"at": iso(stall["ts"]), "seconds": stall["seconds"], "class": stall["class"],
                            "ongoing": stall["ongoing"]} if stall else None},
    }


def log_lines(box: Box, log_path: str | None, since: float, name: str, running: bool = True) -> list[dict]:
    """ERROR/CRITICAL/Traceback/Exception lines since `since` from the container's json-file log (`cell init` sets that
    driver), read from its tail; `docker logs` only for a running container Docker gives no log file for (another
    driver). A log file that is not there yet has no lines."""
    rows: list[tuple[float | None, str]] = []
    text = None
    if log_path:
        try:
            p = Path(log_path)                      # <docker root>/containers/<id>/<id>-json.log
            with safe_open(box.path(p.parent.parent), f"{p.parent.name}/{p.name}", None) as f:
                f.seek(max(0, os.fstat(f.fileno()).st_size - 256 * 1024))
                text = f.read(256 * 1024).decode("utf-8", "replace")
        except OSError:
            text = ""
    elif not running:
        text = ""
    if text is not None:
        floor = iso(since)
        for line in text.splitlines():
            if not ERROR_RE.search(line):           # cheap first: most lines are not errors
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            stamp = str(r.get("time", ""))[:19] + "Z"
            if stamp >= floor and ERROR_RE.search(str(r.get("log", ""))):
                rows.append((parse_iso(stamp), str(r["log"]).rstrip("\n")))
    else:
        r = box.run(["docker", "logs", "--since", f"{ERROR_SINCE_S}s", "--tail", "500", "--timestamps", name])
        for line in (r.stdout + r.stderr).splitlines():
            stamp, _, rest = line.partition(" ")
            if ERROR_RE.search(rest):
                rows.append((parse_iso(stamp), rest))
    return [{"at": iso(at), "line": scrub(line)[:ERROR_CHARS]} for at, line in rows[-ERRORS_MAX:]]


def container_row(i: dict, stats: dict) -> dict:
    st = i.get("state") if isinstance(i.get("state"), dict) else {}
    labels = i.get("labels") if isinstance(i.get("labels"), dict) else {}
    health = st.get("Health") if isinstance(st.get("Health"), dict) else {}
    s = stats.get(i["name"], {})
    mem = i.get("memory")
    return {"role": labels.get("com.docker.compose.service") or None, "name": i["name"], "state": st.get("Status"),
            "health": health.get("Status") or None, "restarts": i.get("restarts"), "oom_killed": bool(st.get("OOMKilled")),
            "started_at": iso(parse_iso(st.get("StartedAt"))),
            "cpu_pct": s.get("cpu_pct"), "mem_mb": s.get("mem_mb"),
            "mem_limit_mb": round(mem / 2 ** 20) if isinstance(mem, int) and mem > 0 else s.get("mem_limit_mb"),
            "pids": s.get("pids"), "net_rx_mb": s.get("net_rx_mb"), "net_tx_mb": s.get("net_tx_mb"),
            "blk_read_mb": s.get("blk_read_mb"), "blk_write_mb": s.get("blk_write_mb")}


def ps_states(rows: list[dict]) -> dict[str, dict]:
    """{slug: {"state", "health"}} as `cell` reads them (Cell.containers), from the one `docker ps`."""
    out = {}
    for row in rows:
        slug = row["_labels"].get(cell.LABEL, "")
        status = str(row.get("Status", ""))
        health = ("unhealthy" if "(unhealthy)" in status else "healthy" if "(healthy)" in status
                  else "starting" if "health: starting" in status else "")
        if slug:
            out[slug] = {"state": str(row.get("State", "")), "health": health}
    return out


# --- the snapshot ------------------------------------------------------------------------------------------------------

class View:
    """The cell's companies and their containers, from one `docker ps` and one `docker inspect`."""

    def __init__(self, box: Box, ps: list[dict] | None = None):
        self.box = box
        self.cell = cell.Cell(box.cell_root, None, box.host)     # only its record readers; it runs nothing here
        self.records = [c for c in self.cell.companies() if c.get("state") != "removed"]
        self.ps = docker_ps(box) if ps is None else ps

    def slugs(self) -> list[str]:
        return [c["slug"] for c in self.records]

    def containers_of(self, slug: str) -> list[dict]:
        proj = cell.project(slug)
        return [r for r in self.ps if r["_labels"].get("com.docker.compose.project") == proj
                or r["_labels"].get(cell.LABEL) == slug]

    def root(self, slug: str) -> Path:
        """The company's folder: made by the host, so the walk into what the company writes (data/) starts here."""
        return self.cell.dir(slug)

    def container(self, slug: str, role: str = "server") -> dict | None:
        return next((r for r in self.containers_of(slug)
                     if r["_labels"].get("com.docker.compose.service") == role), None)


def collect(box: Box, cache: dict, *, company: str | None = None, host_id: str | None = None) -> dict:
    started = box.monotonic()
    now = box.time()
    stat_a = cpu_times(box)
    df_entry = cache.get("df")
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        f_ps = pool.submit(docker_ps, box)
        f_stats = pool.submit(docker_stats, box)
        f_df = pool.submit(docker_disk, box) if _stale(df_entry, now) else None
        f_router = pool.submit(box.http_get, cell.ROUTER_HEALTH_HOST, cell.ROUTER_HEALTH_PATH)
        f_ready = pool.submit(box.http_get, "127.0.0.1", "/ready", TUNNEL_METRICS)
        view = View(box, f_ps.result())
        records = [c for c in view.records if not company or c["slug"] == company]
        health = {c["slug"]: pool.submit(box.http_get, c["domain"], contract.HEALTH_PATH) for c in records}
        ids = [r["ID"] for c in records for r in view.containers_of(c["slug"]) if r.get("ID")]
        tunnel_row = next((r for r in view.ps if str(r.get("Names", "")).lstrip("/") == TUNNEL_CONTAINER), None)
        inspected = {i["name"]: i for i in docker_inspect(box, ids)}
        states = ps_states(view.ps)

        def one(c: dict) -> dict:
            slug = c["slug"]
            names = sorted(str(r.get("Names", "")).lstrip("/") for r in view.containers_of(slug))
            rows = [inspected[n] for n in names if n in inspected]
            root = view.root(slug)
            server = next((i for i in rows if (i.get("labels") or {}).get("com.docker.compose.service") == "server"), None)
            pid = ((server or {}).get("state") or {}).get("Pid") if server else None
            running = server and ((server.get("state") or {}).get("Status")) == "running"
            db, top = db_block(box, root, slug, cache, now, server["name"].lstrip("/") if running else None)
            errors = []
            for i in rows:
                if (i.get("labels") or {}).get("com.docker.compose.service") in ("server", "updater"):
                    errors += log_lines(box, i.get("log"), now - ERROR_SINCE_S, i["name"],
                                        ((i.get("state") or {}).get("Status")) == "running")
            errors.sort(key=lambda e: e["at"] or "")
            try:
                flight = flight_block(root, now, top)
            except Exception as e:  # noqa: BLE001  a bad flight file must not cost the rest of the snapshot
                flight = {"window_s": WINDOW_S, "error": scrub(f"{type(e).__name__}: {e}")[:200]}
            return {"slug": slug, "version": c.get("version"), "state": view.cell.state_of(c, states),
                    "memory_limit_mb": memory_mb(c.get("memory")), "healthz": None, "containers": rows,
                    "db": db, "backup": backup_block(box, pid, now), "flight": flight, "errors": errors[-ERRORS_MAX:]}

        def safe(c: dict) -> dict:
            try:
                return one(c)
            except Exception as e:  # noqa: BLE001  one company's odd state must not cost the others their numbers
                return {"slug": c["slug"], "version": c.get("version"), "state": None, "healthz": None,
                        "containers": [], "db": None, "backup": None, "flight": None, "errors": [],
                        "error": scrub(f"{type(e).__name__}: {e}")[:200]}

        blocks = list(pool.map(safe, records))
        stats = f_stats.result()
        for b in blocks:
            b["containers"] = [container_row(i, stats) for i in b["containers"]]
            code, ms, _ = health[b["slug"]].result()
            b["healthz"] = {"code": code, "ms": ms}
        if f_df is not None:
            df = f_df.result()
            cache["df"] = df_entry = {"at": now, "gb": df[0], "root": df[1]}
        rcode, rms, _ = f_router.result()
        ready = f_ready.result()
    if box.monotonic() - started < 0.5:
        box.sleep(0.5 - (box.monotonic() - started))
    stat_b = cpu_times(box)
    host = host_block(box, cache, ((df_entry or {}).get("gb"), (df_entry or {}).get("root")), stat_a, stat_b)
    snap = {"format": FORMAT, "host_id": host_id, "at": iso(now), "collect_ms": None, "trimmed": False,
            "core_version": core_version(box), "collector_version": own_version(),
            "host": host, "router": {"up": rcode == 200, "ms": rms}, "tunnel": tunnel(tunnel_row, ready),
            "companies": blocks}
    snap["collect_ms"] = int((box.monotonic() - started) * 1000)
    return snap


def tunnel(row: dict | None, ready: tuple) -> dict:
    if row is None:
        return {"up": None, "detail": "no tunnel on this host"}
    if row.get("State") != "running":
        return {"up": False, "detail": f"cloudflared {row.get('State') or 'missing'}"}
    code, _, body = ready
    try:
        n = int(json.loads(body or b"{}").get("readyConnections"))
    except (ValueError, TypeError, AttributeError):
        n = None
    if code == 200:
        return {"up": True, "detail": "cloudflared ready" + (f", {n} connections" if n is not None else "")}
    return {"up": False, "detail": f"cloudflared running, not ready ({code or 'no answer'})"}


def fit(snap: dict, cap: int = MAX_BODY) -> bytes:
    """The snapshot as compact JSON within `cap` bytes: error lines go first, then flight detail (slow routes, SQL
    shapes), then the whole flight block, then companies from the end; `trimmed` says it happened."""
    def dump() -> bytes:
        return json.dumps(snap, separators=(",", ":")).encode()
    body = dump()
    if len(body) <= cap:
        return body
    snap["trimmed"] = True
    companies = snap.get("companies") or []
    steps = [lambda c: c.update(errors=c.get("errors", [])[-3:]), lambda c: c.update(errors=[]),
             lambda c: c["flight"] and c["flight"].update(slow_routes=[]),
             lambda c: c["flight"] and isinstance(c["flight"].get("sql"), dict) and c["flight"]["sql"].update(top=[]),
             lambda c: c.update(flight=None)]
    for step in steps:
        for c in companies:
            step(c)
        body = dump()
        if len(body) <= cap:
            return body
    dropped = 0
    while companies and len(body) > cap:
        companies.pop()
        dropped += 1
        snap["companies_dropped"] = dropped
        body = dump()
    return body


# --- debug tools -------------------------------------------------------------------------------------------------------

class Refused(Exception):
    pass


UNIT_RE = re.compile(r"(?:docker|containerd|cloudflared|caddy|tico-cell-[a-z0-9][a-z0-9-]{0,39})(?:\.service|\.timer)?")
# tool: {arg: (kind, required)}; kind is "company", a tuple of allowed strings, or an (lo, hi) int range.
TOOLS: dict[str, dict] = {
    "host_top": {},
    "cell_status": {},
    "containers": {"company": ("company", False)},
    "logs": {"company": ("company", True), "role": (("server", "updater"), True), "minutes": ((1, 60), True),
             "lines": ((1, 1000), True)},
    "flight": {"company": ("company", True), "minutes": ((1, 1440), True)},
    "db": {"company": ("company", True)},
    "stacks": {"company": ("company", True)},
    "tracemalloc": {"company": ("company", True), "action": (("start", "dump"), True)},
    "journal": {"unit": ("unit", True), "minutes": ((1, 60), True)},
}
TIMEOUTS = {"host_top": 15, "cell_status": 30, "containers": 40, "logs": 20, "flight": 30, "db": 60, "stacks": 8,
            "tracemalloc": 20, "journal": 20}


def validate(tool, args, slugs) -> dict:
    """The allow-list, again on the host: HQ's checks are not trusted. Exactly the schema's keys, exact types."""
    if not isinstance(tool, str) or tool not in TOOLS:
        raise Refused(f"unknown tool {str(tool)[:40]!r}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise Refused("args must be an object")
    schema = TOOLS[tool]
    extra = sorted(set(args) - set(schema))
    if extra:
        raise Refused(f"{tool} takes no {', '.join(str(k)[:30] for k in extra)}")
    out = {}
    for name, (kind, required) in schema.items():
        if name not in args or args[name] is None:
            if required:
                raise Refused(f"{tool} needs {name}")
            continue
        v = args[name]
        if kind == "company":
            if not isinstance(v, str) or not cell.SLUG_RE.fullmatch(v) or v not in slugs:
                raise Refused(f"{name}: no such company on this host")
        elif kind == "unit":
            if not isinstance(v, str) or not UNIT_RE.fullmatch(v):
                raise Refused(f"{name}: not an allowed unit")
        elif isinstance(kind, tuple) and all(isinstance(k, str) for k in kind):
            if not isinstance(v, str) or v not in kind:
                raise Refused(f"{name}: one of {', '.join(kind)}")
        else:
            lo, hi = kind
            if type(v) is not int or not lo <= v <= hi:
                raise Refused(f"{name}: a whole number from {lo} to {hi}")
        out[name] = v
    return out


def _out(r: Result) -> str:
    return (r.stdout or "") + (("\n" + r.stderr) if r.stderr and r.stderr.strip() else "")


def t_host_top(box: Box, view: View, a: dict, timeout: float) -> tuple[bool, str]:
    parts, ok = [], True
    for title, argv, head in (("top", ["top", "-b", "-n1"], 40), ("free -m", ["free", "-m"], 0),
                              ("df -h", ["df", "-h"], 0), ("uptime", ["uptime"], 0),
                              ("docker system df", ["docker", "system", "df"], 0)):
        r = box.run(argv, timeout=max(1.0, min(timeout / 3, 15)))
        ok = ok and r.returncode == 0
        text = _out(r)
        if head:
            text = "\n".join(text.splitlines()[:head])
        parts.append(f"== {title}\n{text.rstrip()}")
    return ok, "\n\n".join(parts)


def t_cell_status(box: Box, view: View, a: dict, timeout: float) -> tuple[bool, str]:
    r = box.run([sys.executable, "-m", "setup", "cell", "list", "--json"], timeout=timeout,
                cwd=str(Path(__file__).resolve().parent.parent))
    return r.returncode == 0, _out(r)


def t_containers(box: Box, view: View, a: dict, timeout: float) -> tuple[bool, str]:
    slugs = [a["company"]] if a.get("company") else view.slugs()
    rows = [r for s in slugs for r in view.containers_of(s)]
    ids = [r["ID"] for r in rows if r.get("ID")]
    names = [str(r.get("Names", "")).lstrip("/") for r in rows]
    found = []
    for i in docker_inspect(box, ids, DEBUG_INSPECT_FORMAT):
        st = i.get("state") if isinstance(i.get("state"), dict) else {}
        if isinstance(st.get("Health"), dict):
            st["Health"] = {"Status": st["Health"].get("Status"), "FailingStreak": st["Health"].get("FailingStreak")}
        labels = i.get("labels") if isinstance(i.get("labels"), dict) else {}
        i["labels"] = {k: v for k, v in labels.items() if k in LABELS_KEPT}
        mounts = []
        for m in i.get("mounts") or []:
            src = str(m.get("Source") or "")
            mounts.append({"type": m.get("Type"), "destination": m.get("Destination"), "rw": m.get("RW"),
                           "source": src if src.startswith(MOUNT_SOURCES) else "[outside /srv and /var/lib/docker]"})
        i["mounts"] = mounts
        found.append(i)
    stats = docker_stats(box, names) if names else {}
    return True, json.dumps({"containers": found, "stats": stats}, indent=1, sort_keys=True)


def t_logs(box: Box, view: View, a: dict, timeout: float) -> tuple[bool, str]:
    row = view.container(a["company"], a["role"])
    if not row:
        return False, f"{a['company']} has no {a['role']} container"
    name = str(row.get("Names", "")).lstrip("/")
    r = box.run(["docker", "logs", "--since", f"{a['minutes']}m", "--tail", str(a["lines"]), "--timestamps", name],
                timeout=timeout)
    return r.returncode == 0, _out(r)


def t_flight(box: Box, view: View, a: dict, timeout: float) -> tuple[bool, str]:
    s = flight_summary(view.root(a["company"]), a["minutes"] * 60, box.time())
    if s is None:
        return False, f"no flight folder for {a['company']} (is the flight recorder on?)"
    return True, json.dumps(s, indent=1, default=str)


def t_db(box: Box, view: View, a: dict, timeout: float) -> tuple[bool, str]:
    """Sizes and header numbers from the file on the host; pragmas, the largest tables and quick_check from inside the
    company's running server container."""
    h = db_header(view.root(a["company"]))
    if h is None:
        return False, f"no database for {a['company']} (or not a plain file)"
    out = {"size_mb": round(h["size"] / 1e6, 1), "wal_mb": round(h["wal"] / 1e6, 1) if h["wal"] is not None else None,
           "header": {k: h.get(k) for k in ("page_size", "page_count", "freelist_count")}}
    row = view.container(a["company"], "server")
    if not row or row.get("State") != "running":
        out["sqlite"] = "server not running"
        return True, json.dumps(out, indent=1)
    got, why = exec_db(box, str(row.get("Names", "")).lstrip("/"), "db", max(1, min(timeout, 60) - 5))
    out["sqlite"] = got if got is not None else why
    return True, json.dumps(out, indent=1, default=str)


def server_pid(box: Box, view: View, slug: str) -> tuple[int | None, str]:
    """The host pid of the company's Tico server process: the pid it wrote to flight/server.pid is in the container's
    pid namespace, so it is matched on NSpid among the processes in that namespace, and must run backend.server."""
    row = view.container(slug, "server")
    if not row:
        return None, f"{slug} has no server container"
    info = docker_inspect(box, [row["ID"]])
    init = ((info[0].get("state") or {}).get("Pid") if info else 0) or 0
    if not init:
        return None, f"{slug}'s server is not running"
    try:
        inner = int(safe_read(view.root(slug), f"{FLIGHT}/{flight_file().PID}", 32, cap=4096).decode().strip())
    except (OSError, ValueError):
        return None, "no flight/server.pid (is the flight recorder on?)"
    proc = box.path("proc")
    try:
        ns = os.readlink(proc / str(init) / "ns" / "pid")
    except OSError:
        return None, "cannot read the container's pid namespace"
    for e in os.scandir(proc):
        if not e.name.isdigit():
            continue
        try:
            if os.readlink(Path(e.path) / "ns" / "pid") != ns:
                continue
            line = next((x for x in (Path(e.path) / "status").read_text().splitlines() if x.startswith("NSpid:")), "")
            if line.split()[-1:] != [str(inner)]:
                continue
            cmd = (Path(e.path) / "cmdline").read_bytes()
        except (OSError, IndexError):
            continue
        if b"backend.server" not in cmd and b"uvicorn" not in cmd:
            return None, f"pid {inner} in {slug}'s server is not the Tico server"
        return int(e.name), ""
    return None, f"pid {inner} from server.pid is not running"


def t_stacks(box: Box, view: View, a: dict, timeout: float) -> tuple[bool, str]:
    pid, why = server_pid(box, view, a["company"])
    if not pid:
        return False, why
    root, rel = view.root(a["company"]), f"{FLIGHT}/{flight_file().STACKS}"
    try:
        before = safe_stat(root, rel).st_size
    except FileNotFoundError:
        before = 0
    except OSError as e:
        return False, f"stacks.txt is not readable: {e}"
    box.kill(pid, signal.SIGUSR1)
    deadline = box.monotonic() + min(timeout, 5)
    size = before
    while box.monotonic() < deadline:
        box.sleep(0.2)
        try:
            size = safe_stat(root, rel).st_size
        except OSError:
            continue
        if size > before:
            box.sleep(0.3)          # faulthandler writes thread by thread
            break
    if size <= before:
        return False, "the server wrote no stacks (is the flight recorder on?)"
    try:
        return True, safe_read(root, rel, MAX_OUTPUT, before if size >= before else 0).decode("utf-8", "replace")
    except OSError as e:
        return False, f"stacks.txt is not readable: {e}"


def tracing(root: Path, now: float) -> bool:
    """Whether tracemalloc is on: the newest memory record since the server's last start says `started_at`."""
    state = False
    for r in flight_file().records(root / FLIGHT, now - 86400, flight_opener(root, FLIGHT)):
        if r.get("kind") == "start":
            state = False
        elif r.get("kind") == "memory":
            state = r.get("state") == "started"
    return state


def t_tracemalloc(box: Box, view: View, a: dict, timeout: float) -> tuple[bool, str]:
    root = view.root(a["company"])
    on = tracing(root, box.time())
    if a["action"] == "start" and on:
        return False, "tracemalloc is already on; send dump"
    if a["action"] == "dump" and not on:
        return False, "tracemalloc is not on; send start first"
    pid, why = server_pid(box, view, a["company"])
    if not pid:
        return False, why
    def dumps() -> set[str]:
        try:
            return {n for n in safe_listdir(root, FLIGHT) if n.startswith("memory-") and n.endswith(".txt")}
        except OSError:
            return set()
    before = dumps()
    box.kill(pid, signal.SIGUSR2)
    if a["action"] == "start":
        return True, f"tracemalloc started in {a['company']}'s server; send dump to get the top 50 allocation sites"
    deadline = box.monotonic() + min(timeout, 20)
    while box.monotonic() < deadline:
        box.sleep(0.5)
        new = sorted(dumps() - before)
        if new:
            box.sleep(0.2)
            try:
                return True, safe_read(root, f"{FLIGHT}/{new[-1]}", MAX_OUTPUT).decode("utf-8", "replace")
            except OSError as e:
                return False, f"{new[-1]} is not readable: {e}"
    return False, "the server wrote no memory file in time"


def t_journal(box: Box, view: View, a: dict, timeout: float) -> tuple[bool, str]:
    r = box.run(["journalctl", "-u", a["unit"], "--since", f"{a['minutes']} min ago", "--no-pager", "-o",
                 "short-iso", "-n", "5000"], timeout=timeout)
    return r.returncode == 0, _out(r)


RUNNERS = {"host_top": t_host_top, "cell_status": t_cell_status, "containers": t_containers, "logs": t_logs,
           "flight": t_flight, "db": t_db, "stacks": t_stacks, "tracemalloc": t_tracemalloc, "journal": t_journal}


def run_tool(box: Box, tool, args, timeout: float | None = None, view: View | None = None) -> dict:
    """{"ok", "ms", "output"} for one request: validated here, run with its own timeout, output scrubbed and cut."""
    t = box.monotonic()
    try:
        view = view or View(box)
        a = validate(tool, args, set(view.slugs()))
        ok, text = RUNNERS[tool](box, view, a, min(timeout or TIMEOUTS[tool], TIMEOUTS[tool]))
    except Refused as e:
        ok, text = False, f"refused: {e}"
    except Exception as e:  # noqa: BLE001  a result HQ can show, even for a bug
        ok, text = False, f"failed: {type(e).__name__}: {e}"
    return {"ok": bool(ok), "ms": int((box.monotonic() - t) * 1000), "output": _cut(scrub(text), MAX_OUTPUT)}


# --- push ---------------------------------------------------------------------------------------------------------------

def load_config(box: Box) -> dict:
    c = _read_json(box.path(CONFIG))
    if not (isinstance(c.get("hq"), str) and check_hq(c["hq"]) and isinstance(c.get("host_id"), str)
            and HOST_ID_RE.fullmatch(c["host_id"]) and isinstance(c.get("key"), str) and KEY_RE.fullmatch(c["key"])):
        raise SystemExit(f"not enrolled: no valid /{CONFIG} (run `python3 -m setup.cell_stats enroll`)")
    return c


def check_hq(url: str) -> str | None:
    """HQ's base address: https (http only to this machine, for tests), no path, query or credentials."""
    u = urllib.parse.urlsplit(url.strip().rstrip("/"))
    local = u.hostname in ("127.0.0.1", "localhost", "::1")
    if (u.scheme == "https" or (u.scheme == "http" and local)) and u.hostname and not u.username and not u.password \
            and u.path in ("", "/") and not u.query and not u.fragment:
        return f"{u.scheme}://{u.netloc}"
    return None


def _headers(cfg: dict, gz: bool) -> dict:
    h = {"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json",
         "User-Agent": "tico-cell-stats"}     # the snapshot carries collector_version
    if gz:
        h["Content-Encoding"] = "gzip"
    return h


def post_result(box: Box, cfg: dict, item_id: str, result: dict) -> int:
    url = f"{cfg['hq']}/v1/cells/{cfg['host_id']}/debug/{item_id}"
    status, _, _ = box.post(url, json.dumps(result).encode(), _headers(cfg, False))
    return status


def push(box: Box, out=None) -> int:
    """One tick: collect, POST, then run the debug requests that came back (and any left from the last tick) within the
    tick's budget. No retry: a refused or failed push is said once on stdout (the journal), and the next tick tries."""
    out = out or sys.stdout
    started = box.monotonic()
    cfg = load_config(box)
    point_core_link(box)
    cache_path = box.path(CACHE)
    cache = _read_json(cache_path)
    snap = collect(box, cache, host_id=cfg["host_id"])
    body = fit(snap)
    _write_private(cache_path, cache)
    status, headers, answer = box.post(f"{cfg['hq']}/v1/cells/{cfg['host_id']}/stats", gzip.compress(body, 5),
                                       _headers(cfg, True))
    report = {"ok": status == 200, "status": status, "collect_ms": snap["collect_ms"], "bytes": len(body),
              "trimmed": snap["trimmed"]}
    items = []
    if status == 200:
        try:
            items = [d for d in (json.loads(answer or b"{}").get("debug") or []) if isinstance(d, dict)]
        except (ValueError, AttributeError):
            report["detail"] = "the answer was not JSON"
    elif status == 429:
        report["detail"] = f"too soon (Retry-After {headers.get('Retry-After') or headers.get('retry-after') or '?'})"
    elif status in (401, 403):
        report["detail"] = "HQ refused the key: rotate it and run enroll again" if status == 401 else \
            "HQ refused this host (revoked, or another host's key)"
    elif status == 0:
        report["detail"] = "HQ could not be reached: " + scrub(answer.decode("utf-8", "replace"))[:200]
    else:
        report["detail"] = f"HQ answered {status}"
    queue_path = box.path(QUEUE)
    queued = _read_json(queue_path).get("items") or []
    pending = [d for d in queued if isinstance(d, dict)] + items
    report["debug"] = run_debug(box, cfg, pending, started, queue_path)
    out.write(json.dumps(report) + "\n")
    out.flush()
    return 0 if status in (200, 429) else 1


def run_debug(box: Box, cfg: dict, pending: list[dict], started: float, queue_path: Path) -> dict:
    done, left, view = 0, [], None
    seen = set()
    for item in pending:
        item_id = item.get("id")
        if not isinstance(item_id, str) or not DEBUG_ID_RE.fullmatch(item_id) or item_id in seen:
            continue                                    # no address to answer at
        seen.add(item_id)
        remaining = started + TICK_BUDGET - box.monotonic()
        if remaining < TOOL_MIN:
            left.append({"id": item_id, "tool": item.get("tool"), "args": item.get("args")})
            continue
        view = view or View(box)
        result = run_tool(box, item.get("tool"), item.get("args"), timeout=remaining, view=view)
        post_result(box, cfg, item_id, result)
        done += 1
    if left or queue_path.exists():
        _write_private(queue_path, {"items": left})
    return {"ran": done, "left": len(left)}


# --- enroll -------------------------------------------------------------------------------------------------------------

def units(core_link: Path) -> dict[str, str]:
    return {
        SERVICE: "\n".join([
            "# Written by `python3 -m setup.cell_stats enroll` (docs/cell-stats.md). Do not edit.",
            "[Unit]", "Description=Tico cell stats: this host's numbers to HQ, and staff's debug requests",
            "After=docker.service network-online.target", "Wants=network-online.target", "",
            "[Service]", "Type=oneshot", f"WorkingDirectory={core_link}",
            "ExecStart=/usr/bin/python3 -m setup.cell_stats push", "TimeoutStartSec=50", "Nice=10",
            "IOSchedulingClass=idle", "Environment=PYTHONDONTWRITEBYTECODE=1", ""]),
        TIMER: "\n".join([
            "# Written by `python3 -m setup.cell_stats enroll` (docs/cell-stats.md). Do not edit.",
            "[Unit]", "Description=Tico cell stats every 60 seconds", "",
            "[Timer]", "OnActiveSec=10s", "OnUnitActiveSec=60s", "AccuracySec=5s", "Persistent=false",
            f"Unit={SERVICE}", "", "[Install]", "WantedBy=timers.target", ""]),
    }


def enroll(box: Box, args, stdin=None, out=None) -> int:
    out, stdin = out or sys.stdout, stdin or sys.stdin

    def answer(d: dict) -> int:
        out.write(json.dumps(d) + "\n")
        return 0 if d.get("ok") else 1
    if box.euid() != 0:
        return answer({"ok": False, "error": "not_root", "detail": "run enroll as root"})
    unit_dir = box.path(UNIT_DIR)
    if args.uninstall:
        box.run(["systemctl", "disable", "--now", TIMER])
        box.run(["systemctl", "stop", SERVICE])
        removed = []
        for p in (unit_dir / TIMER, unit_dir / SERVICE, box.path(CONFIG), box.path(CACHE), box.path(QUEUE),
                  box.path(CORE_LINK)):
            if p.is_symlink() or p.exists():
                p.unlink()
                removed.append("/" + str(p.relative_to(box.host)))
        box.run(["systemctl", "daemon-reload"])
        return answer({"ok": True, "removed": removed})
    hq = check_hq(args.hq or "")
    if not hq:
        return answer({"ok": False, "error": "bad_args", "detail": "--hq is HQ's https:// address, no path"})
    if not HOST_ID_RE.fullmatch(args.host_id or ""):
        return answer({"ok": False, "error": "bad_args", "detail": "--host-id: lowercase letters, digits, hyphens"})
    key = (getpass.getpass("Stats key (tch_…): ") if stdin.isatty() else stdin.read(4096)).strip()
    if not KEY_RE.fullmatch(key):
        return answer({"ok": False, "error": "bad_key", "detail": "stdin must hold the tch_ key HQ showed once"})
    _write_private(box.path(CONFIG), {"hq": hq, "host_id": args.host_id, "key": key})
    os.chmod(box.path(CONFIG), 0o600)
    target = point_core_link(box, Path(__file__).resolve().parent.parent)
    link = Path("/") / CORE_LINK
    for name, text in units(link).items():
        cell._write_atomic(unit_dir / name, text, 0o644)
    steps = [box.run(["systemctl", "daemon-reload"]), box.run(["systemctl", "enable", "--now", TIMER])]
    failed = next((r for r in steps if r.returncode), None)
    if failed:
        return answer({"ok": False, "error": "systemd", "detail": scrub((failed.stderr or failed.stdout)[-400:])})
    return answer({"ok": True, "host_id": args.host_id, "hq": hq, "config": "/" + CONFIG, "core": str(target),
                   "timer": TIMER})


# --- show --------------------------------------------------------------------------------------------------------------

def render(snap: dict) -> str:
    h = snap["host"]
    lines = [f"{snap.get('host_id') or '(not enrolled)'} at {snap['at']}, collected in {snap['collect_ms']} ms"
             f" (core {snap.get('core_version') or '?'}, collector {snap.get('collector_version') or '?'})",
             f"  cpu {h['cpu_pct']}% iowait {h['iowait_pct']}% steal {h['steal_pct']}% load {h['load']} on {h['cpus']} cpus",
             f"  memory {h['mem_avail_mb']} of {h['mem_total_mb']} MB free, swap used {h['swap_used_mb']} MB, "
             f"OOM kills in the last hour {h['oom_kills_1h']}",
             f"  disk {h['disk_free_gb']} of {h['disk_total_gb']} GB free, inodes {h['inodes_free_pct']}% free, "
             f"docker {h['docker_disk_gb']} GB",
             f"  router {'up' if snap['router']['up'] else 'DOWN'} ({snap['router']['ms']} ms), "
             f"tunnel {snap['tunnel']['detail']}", ""]
    lines.append(f"  {'company':<20} {'state':<10} {'healthz':>8} {'cpu%':>6} {'mem MB':>8} {'db MB':>8} "
                 f"{'wal MB':>7} {'backup s':>8} {'p95 ms':>7} {'lock ms':>8} {'stalls':>6} {'errors':>6}")
    for c in snap["companies"]:
        server = next((x for x in c["containers"] if x["role"] == "server"), {})
        f = c.get("flight") or {}
        req, sql, stalls = f.get("requests") or {}, f.get("sql") or {}, f.get("stalls") or {}
        lines.append(f"  {c['slug']:<20} {c['state']:<10} {str(c['healthz']['code']):>8} "
                     f"{str(server.get('cpu_pct')):>6} {str(server.get('mem_mb')):>8} {str(c['db']['size_mb']):>8} "
                     f"{str(c['db']['wal_mb']):>7} {str((c.get('backup') or {}).get('age_s')):>8} "
                     f"{str(req.get('p95_ms')):>7} {str(sql.get('lock_wait_ms')):>8} {str(stalls.get('count')):>6} "
                     f"{len(c['errors']):>6}")
    return "\n".join(lines)


def show(box: Box, args, out=None) -> int:
    out = out or sys.stdout
    try:
        host_id = _read_json(box.path(CONFIG)).get("host_id")
    except OSError:
        host_id = None
    cache = _read_json(box.path(CACHE))
    snap = collect(box, cache, company=args.company, host_id=host_id or socket.gethostname())
    if args.company and not snap["companies"]:
        out.write(json.dumps({"ok": False, "error": "not_found", "detail": f"no company {args.company} here"}) + "\n")
        return 1
    if args.json:
        out.write(json.dumps(snap["companies"][0] if args.company else snap, indent=1) + "\n")
    else:
        out.write(render(snap) + "\n")
        if args.company:
            out.write(json.dumps(snap["companies"][0], indent=1) + "\n")
    return 0


def debug_run(box: Box, args, out=None) -> int:
    out = out or sys.stdout
    try:
        parsed = json.loads(args.args) if args.args else {}
    except ValueError:
        out.write("--args is a JSON object\n")
        return 1
    r = run_tool(box, args.tool, parsed)
    out.write(r["output"].rstrip() + f"\n-- {'ok' if r['ok'] else 'failed'} in {r['ms']} ms\n")
    return 0 if r["ok"] else 1


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m setup.cell_stats",
                                description="This cell's numbers to HQ each minute, and staff's debug tools "
                                            "(docs/cell-stats.md).")
    sub = p.add_subparsers(dest="action", required=True)
    e = sub.add_parser("enroll", help="as root: save HQ's key (from stdin) and start the timer")
    e.add_argument("--hq", help="HQ's address, e.g. https://updates.tico.team")
    e.add_argument("--host-id", help="this host's id at HQ, e.g. cell-1")
    e.add_argument("--uninstall", action="store_true", help="stop the timer; remove the units, key and cache")
    sub.add_parser("push", help="one tick: send the snapshot, run the debug requests HQ hands back")
    s = sub.add_parser("show", help="print the snapshot here; nothing is sent")
    s.add_argument("--company", metavar="SLUG", help="only this company, in full")
    s.add_argument("--json", action="store_true")
    d = sub.add_parser("debug-run", help="run one allow-listed debug tool here and print its output; nothing is sent")
    d.add_argument("tool", choices=sorted(TOOLS))
    d.add_argument("--args", metavar="JSON", help='e.g. \'{"company": "boatyard", "minutes": 30}\'')
    return p


def main(argv: list[str] | None = None, *, box: Box | None = None) -> int:
    args = parser().parse_args(argv)
    box = box or Box()
    if args.action == "enroll":
        return enroll(box, args)
    if args.action == "push":
        return push(box)
    if args.action == "show":
        return show(box, args)
    return debug_run(box, args)


if __name__ == "__main__":
    sys.exit(main())
