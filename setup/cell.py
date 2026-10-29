"""Many companies' Tico servers on one shared host: `python3 -m setup cell ...` (docs/cell.md).

Tico HQ drives this over AWS SSM, so it is a machine interface: every command prints exactly one JSON object on stdout,
`{"ok": true, ...}` or `{"ok": false, "error": "<code>", "detail": "..."}` (exit 0 or 1), takes secrets only from files
that no one else can read (mode 0600 or stricter), never from argv, and never prints them. Every command is idempotent:
`add` again with the same settings changes nothing and answers the same; an optional input left out on a re-run keeps
what was saved.

Each company is the server a hosted install makes (the stock image and entrypoint, `Settings.to_env()` for its .env,
the owner link as break-glass, the recommended preset), minus its own Caddy, cloudflared, updater and EC2 instance:

    <root>/cell.json                        the host: the domain every company is a name under, the router's port
    <root>/edge.yaml, router/Caddyfile      the router (Caddy, plain HTTP on 127.0.0.1:8080) and the one tunnel
    <root>/tunnel/, tunnel.env (0600)       cloudflared's routes and its token
    <root>/companies/<slug>/.env            0600, the server's settings
    <root>/companies/<slug>/compose.yaml    the stock compose.yaml, unchanged
    <root>/companies/<slug>/compose.cell.yaml   memory limit, data folder, networks: what makes it one of many
    <root>/companies/<slug>/company.json    what the cell knows about it (no secrets): port, index, version, state
    <root>/companies/<slug>/data/           the server's /data (owned by the server's user, 10001)
    <root>/locks/                           one lock for the host's shared files, one per company

A company's index (the smallest free one, kept until `remove` without --keep-data) gives it its loopback port
(20000 + index) and its two private networks, so no two companies ever share one, across restarts too. The router maps
the Host header to that port; cloudflared sends `*.<domain>` to the router. Containers cannot reach the EC2 metadata
service (a DOCKER-USER rule, first in its chain, that `init` installs and `add` checks), so no company can use the
host's AWS role. `update` snapshots the database before a new release first starts, and puts it back if that release
does not come up. The app is told Tico runs its updates (TICO_UPDATES=tico), so it has no Update now of its own.

All Docker and system calls go through one `System`, so the tests run every command with a fake.
"""
from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import http.client
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import contract, envfile, hosted, remote, settings as st, signin
from .cloudinit import VERSION_RE
from .ui import MissingInput

ROOT = "/srv/tico-cell"
SERVER_IMAGE = "ghcr.io/ticoteam/tico"
# The same pinned images compose.yaml uses for the single-company front doors (a test keeps them equal).
ROUTER_IMAGE = "caddy:2-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b"
TUNNEL_IMAGE = "cloudflare/cloudflared:2026.7.3@sha256:e39ee8da81ad5e05d77f38d2f51c60ca51bf2a8450ac3abab50c17fdb91d91bf"
ROUTER_ADDRESS, ROUTER_PORT = "127.0.0.1", 8080
ROUTER_CONTAINER, EDGE_PROJECT = "tico-cell-router", "tico-cell"
ROUTER_HEALTH_HOST, ROUTER_HEALTH_PATH = "cell-health.invalid", "/cell-healthz"
PROJECT_PREFIX = "tico-"
LABEL = "tico.cell.company"
SERVER_UID = 10001                      # the server image's user (Dockerfile)
TUNNEL_UID = 65532                      # cloudflared's image runs as nonroot (uid and gid 65532)

# Never given to a company: our own names under the domain.
RESERVED = frozenset({"hub", "admin", "accounts", "updates", "preview", "runner", "www", "api", "app", "mail", "status",
                      "docs", "help", "support", "hq", "gateway", "hooks", "cloud", "tico", "team"})
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
MEMORY_RE = re.compile(r"^([1-9][0-9]{0,5})([mg])$")
MEMORY_DEFAULT, MEMORY_MIN_MB, MEMORY_MAX_MB = "2g", 512, 16 * 1024
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
SECRET_MAX = 64 * 1024
HOSTED_DEFAULT, SIGNIN_LABEL = "invited", "Tico account"

# Index i: port 20000+i, default network the i-th /24 of 10.208.0.0/12, edge network the i-th /28 of 10.224.0.0/16.
PORT_BASE, MAX_COMPANIES = 20000, 4000
DEFAULT_NETS = ipaddress.ip_network("10.208.0.0/12")
EDGE_NETS = ipaddress.ip_network("10.224.0.0/16")

# The rule that keeps containers from the instance's role. IMDSv2 with hop limit 1 is the second lock (docs/cell.md).
IMDS_RULE = ["DOCKER-USER", "-d", "169.254.169.254/32", "-j", "REJECT", "--reject-with", "icmp-port-unreachable"]
IMDS_LINE = "-A " + " ".join(IMDS_RULE)     # how `iptables -S DOCKER-USER` prints it
FIREWALL_SCRIPT = "usr/local/sbin/tico-cell-firewall"
FIREWALL_UNIT = "etc/systemd/system/tico-cell-firewall.service"
DOCKER_DROPIN = "etc/systemd/system/docker.service.d/tico-cell.conf"
DAEMON_JSON = "etc/docker/daemon.json"
DAEMON_SETTINGS = {"log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "3"}, "live-restore": True}

HEALTH_TIMEOUT = 300
SNAPSHOTS_KEPT = 2
DB_FILES = ("hub.sqlite", "hub.sqlite-wal", "hub.sqlite-shm")
STOPPED = ("", "exited", "created", "dead")    # container states in which nothing has the database open
# TICO_UPDATES: Tico runs this server's updates (`cell update`), so the app has no Update now (backend/releases.py).
UPDATES_BY = "tico"


class CellError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail
        self.extra: dict = {}


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class System:
    """Every call outside this process. The tests replace it (setup/tests/test_cell.py FakeSystem)."""

    def run(self, argv: list[str], *, input: str | None = None, timeout: float = 900) -> Result:
        # COMPOSE_* and TICO_* from the calling shell would override a company's .env in Compose.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "TICO_"))}
        try:
            p = subprocess.run(argv, input=input, capture_output=True, text=True, timeout=timeout, env=env, check=False)
        except FileNotFoundError:
            return Result(127, "", f"{argv[0]}: not found")
        except subprocess.TimeoutExpired:
            return Result(124, "", f"{argv[0]}: timed out after {int(timeout)}s")
        return Result(p.returncode, p.stdout, p.stderr)

    def http_status(self, host: str, path: str, timeout: float = 3) -> int:
        """The router's answer for `host` (0 when it does not answer)."""
        try:
            c = http.client.HTTPConnection(ROUTER_ADDRESS, ROUTER_PORT, timeout=timeout)
            c.request("GET", path, headers={"Host": host})
            status = c.getresponse().status
            c.close()
            return status
        except (OSError, http.client.HTTPException):
            return 0

    def port_free(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

    def chown(self, path: Path, uid: int, gid: int) -> None:
        os.chown(path, uid, gid)

    def linux(self) -> bool:
        return sys.platform.startswith("linux")

    def latest_release(self) -> str:
        from .wizard import _latest_release
        return _latest_release()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def monotonic(self) -> float:
        return time.monotonic()


# --- inputs ----------------------------------------------------------------------------------------------------

def check_slug(value: str, *, new: bool = False) -> str:
    slug = value or ""
    if not SLUG_RE.fullmatch(slug) or "--" in slug:
        raise CellError("bad_slug", "--company is 1 to 40 lowercase letters, digits and single hyphens, starting and "
                                    "ending with a letter or digit")
    if new and slug in RESERVED:
        raise CellError("reserved_name", f"{slug} is reserved for Tico's own use")
    return slug


def check_memory(value: str) -> str:
    m = MEMORY_RE.fullmatch((value or "").strip().lower())
    mb = int(m.group(1)) * (1024 if m.group(2) == "g" else 1) if m else 0
    if not MEMORY_MIN_MB <= mb <= MEMORY_MAX_MB:
        raise CellError("bad_args", f"--memory is a size such as 2g or 1536m, from {MEMORY_MIN_MB}m to 16g")
    return value.strip().lower()


def read_secret(path: str, flag: str) -> str:
    """A secret file's text, stripped. Refused when anyone but its owner may read it, so a secret never sits in a file
    others could copy; the error names the flag, never the content."""
    try:
        info = os.stat(path)
        if not os.path.isfile(path):
            raise CellError("secret_file", f"{flag}: not a regular file")
        if info.st_mode & 0o077:
            raise CellError("secret_file", f"{flag}: the file must be readable by its owner only (chmod 600)")
        if info.st_size > SECRET_MAX:
            raise CellError("secret_file", f"{flag}: the file is too large")
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        raise CellError("secret_file", f"{flag}: cannot read the file") from None
    except UnicodeError:
        raise CellError("secret_file", f"{flag}: the file is not UTF-8 text") from None
    if not text:
        raise CellError("secret_file", f"{flag}: the file is empty")
    return text


def backup_keys(text: str) -> tuple[str, str]:
    """--backup-credentials-file: KEY=VALUE lines (LITESTREAM_ or AWS_ACCESS_KEY_ID and _SECRET_ACCESS_KEY), or the
    JSON `aws iam create-access-key` prints ({"AccessKey": {"AccessKeyId", "SecretAccessKey"}}, or that object alone)."""
    key = secret = ""
    if text.startswith("{"):
        try:
            data = json.loads(text)
            data = data.get("AccessKey", data)
            key, secret = str(data.get("AccessKeyId", "")), str(data.get("SecretAccessKey", ""))
        except (ValueError, AttributeError):
            pass
    else:
        e = envfile.parse(text)
        key = e.get("LITESTREAM_ACCESS_KEY_ID") or e.get("AWS_ACCESS_KEY_ID", "")
        secret = e.get("LITESTREAM_SECRET_ACCESS_KEY") or e.get("AWS_SECRET_ACCESS_KEY", "")
    if not (re.fullmatch(r"[A-Z0-9]{16,128}", key) and re.fullmatch(r"[A-Za-z0-9/+=]{16,128}", secret)):
        raise CellError("secret_file", "--backup-credentials-file: no access key id and secret access key in it")
    return key, secret


class Parser(argparse.ArgumentParser):
    """Argument errors are answered in JSON like every other error."""

    def error(self, message):
        raise CellError("bad_args", message)


def parser() -> Parser:
    p = Parser(prog="python3 -m setup cell", description="Many companies' Tico servers on one shared host (docs/cell.md). "
               "Prints one JSON object. Secrets come only from 0600 files.")
    sub = p.add_subparsers(dest="action", required=True, parser_class=Parser)

    def cmd(name, help):
        c = sub.add_parser(name, help=help)
        c.add_argument("--json", action="store_true", help="accepted for clarity: the answer is always JSON")
        return c

    i = cmd("init", "once per host: Docker, the router, the tunnel and the metadata firewall")
    i.add_argument("--tunnel-token-file", metavar="F", help="the Cloudflare tunnel's token (0600); kept for later runs")
    i.add_argument("--domain", help="the domain every company is a name under (default tico.team, or the saved one)")
    a = cmd("add", "create or converge one company's server; answers once /healthz answers through the router")
    a.add_argument("--company", required=True, metavar="SLUG")
    a.add_argument("--domain", required=True, help="<slug>.<the cell's domain>")
    a.add_argument("--owner-email", required=True)
    a.add_argument("--auth", required=True, choices=["oidc"])
    a.add_argument("--issuer", required=True)
    a.add_argument("--client-id", required=True)
    a.add_argument("--client-secret-file", required=True, metavar="F")
    a.add_argument("--gateway-invite-file", metavar="F")
    a.add_argument("--backup-url", help="s3://bucket/prefix, a prefix of this company's own")
    a.add_argument("--backup-region")
    a.add_argument("--backup-credentials-file", metavar="F", help="this company's own scoped access key (0600)")
    a.add_argument("--company-file", metavar="F", help="the company YAML from the org-chart picker")
    a.add_argument("--company-name", help="the team's name when there is no --company-file (default from the slug)")
    a.add_argument("--memory", help=f"memory limit (default {MEMORY_DEFAULT})")
    a.add_argument("--version", help="the release to run, vX.Y.Z (default the installer's own release)")
    a.add_argument("--hosted", choices=list(st.HOSTED), help=f"TICO_HOSTED (default {HOSTED_DEFAULT})")
    a.add_argument("--signin-label", help=f"the sign-in button's name (default \"{SIGNIN_LABEL}\")")
    a.add_argument("--timeout", type=int, default=HEALTH_TIMEOUT, help="seconds to wait for /healthz")
    cmd("list", "every company on this host")
    for name, help in (("status", "one company"), ("stop", "stop one company's server; its data stays"),
                       ("start", "start it again; answers once /healthz answers")):
        c = cmd(name, help)
        c.add_argument("--company", required=True, metavar="SLUG")
        if name == "start":
            c.add_argument("--timeout", type=int, default=HEALTH_TIMEOUT)
    u = cmd("update", "run another release; the database is snapshotted first and put back if it does not come up")
    u.add_argument("--company", required=True, metavar="SLUG")
    u.add_argument("--version", required=True)
    u.add_argument("--timeout", type=int, default=HEALTH_TIMEOUT)
    r = cmd("remove", "remove one company's containers, route and (unless --keep-data) its folder")
    r.add_argument("--company", required=True, metavar="SLUG")
    r.add_argument("--keep-data", action="store_true", help="keep its folder, data and port for a later add")
    return p


# --- the cell --------------------------------------------------------------------------------------------------

def _write_atomic(path: Path, text: str, mode: int = 0o644) -> bool:
    """Replace `path` in one step (a reader sees the old file or the new one); False when it already holds `text`."""
    try:
        if path.read_text() == text:
            os.chmod(path, mode)
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
    return True


def project(slug: str) -> str:
    return PROJECT_PREFIX + slug


def networks(index: int) -> dict[str, str]:
    default = ipaddress.ip_network((int(DEFAULT_NETS.network_address) + index * 256, 24))
    edge = ipaddress.ip_network((int(EDGE_NETS.network_address) + index * 16, 28))
    base = int(edge.network_address)
    return {"default_subnet": str(default), "default_gateway": str(default.network_address + 1),
            "edge_subnet": str(edge), "edge_range": str(ipaddress.ip_network((base + 8, 29))),
            "edge_gateway": str(edge.network_address + 1), "caddy_address": str(edge.network_address + 2),
            "cloudflared_address": str(edge.network_address + 3)}


def router_config(companies: list[dict]) -> str:
    """The router's Caddyfile: each company's domain to its loopback port, anything else a 404. Plain HTTP on loopback
    only: the tunnel in front holds TLS. The forwarded scheme is https because that is what the visitor used."""
    lines = ["# Written by `python3 -m setup cell`; regenerated whenever a company is added or removed. Do not edit.",
             "{", "\tadmin localhost:2019", "\tauto_https off", "\tpersist_config off", f"\thttp_port {ROUTER_PORT}",
             f"\tdefault_bind {ROUTER_ADDRESS}", "}", ""]
    for c in sorted(companies, key=lambda c: c["domain"]):
        lines += [f"http://{c['domain']}:{ROUTER_PORT} {{", f"\treverse_proxy 127.0.0.1:{int(c['port'])} {{",
                  "\t\theader_up X-Forwarded-Proto https", "\t}", "}", ""]
    lines += [f"http://:{ROUTER_PORT} {{", f"\trespond {ROUTER_HEALTH_PATH} \"ok\" 200",
              "\trespond \"No Tico company at this address.\" 404", "}"]
    return "\n".join(lines) + "\n"


def tunnel_config(domain: str) -> str:
    return "\n".join([
        "# Written by `python3 -m setup cell init`: every company's name to the router; anything else a 404.",
        "ingress:", f"  - hostname: \"*.{domain}\"", f"    service: http://{ROUTER_ADDRESS}:{ROUTER_PORT}",
        "  - service: http_status:404"]) + "\n"


def edge_compose(root: Path, tunnel: bool) -> str:
    """The router and the tunnel: on the host's network, so they reach each company's loopback port; nothing else."""
    lines = ["# Written by `python3 -m setup cell init`: the shared host's front door. Companies are separate projects.",
             f"name: {EDGE_PROJECT}", "services:", "  router:", f"    image: {ROUTER_IMAGE}",
             f"    container_name: {ROUTER_CONTAINER}", "    network_mode: host", "    restart: unless-stopped",
             "    init: true", "    security_opt: [\"no-new-privileges:true\"]", "    cap_drop: [ALL]",
             "    cap_add: [NET_BIND_SERVICE]",   # caddy's binary carries this as a file capability; exec fails without it
             "    read_only: true", "    tmpfs: [/tmp]", "    command: [caddy, run, --config, /etc/caddy/Caddyfile, --adapter, caddyfile]",
             "    volumes:", f"      - {root}/router:/etc/caddy:ro", "      - router-data:/data", "      - router-config:/config"]
    if tunnel:
        lines += ["  tunnel:", f"    image: {TUNNEL_IMAGE}", "    container_name: tico-cell-tunnel", "    network_mode: host",
                  "    restart: unless-stopped", "    init: true", "    security_opt: [\"no-new-privileges:true\"]",
                  "    cap_drop: [ALL]", "    read_only: true",
                  "    command: tunnel --no-autoupdate --metrics 127.0.0.1:8081 --config /etc/cloudflared/cloudflared.yml run",
                  f"    env_file: [{root}/tunnel.env]", "    volumes:", f"      - {root}/tunnel:/etc/cloudflared:ro",
                  "    depends_on: [router]"]
    lines += ["volumes:", "  router-data:", "  router-config:"]
    return "\n".join(lines) + "\n"


def company_override(c: dict, data_dir: Path) -> str:
    n = networks(c["index"])
    return "\n".join([
        "# Written by `python3 -m setup cell`: what makes the stock compose.yaml beside it one company of many on a",
        "# shared host. Its own data folder, a memory limit, its own two networks (so none collide), a label for `list`.",
        "services:", "  server:", f"    mem_limit: {c['memory']}", f"    memswap_limit: {c['memory']}",
        "    environment:", "      TICO_UPDATES:", "    labels:", f"      {LABEL}: {c['slug']}",
        "    volumes:", f"      - {data_dir}:/data",
        "networks:", "  default:", "    ipam:", "      config:", f"        - subnet: {n['default_subnet']}",
        f"          gateway: {n['default_gateway']}"]) + "\n"


def _installer_version() -> str:
    """The release this installer came with (HQ's bundle writes .tico-installer.json beside setup/)."""
    try:
        v = json.loads((Path(__file__).resolve().parent.parent / ".tico-installer.json").read_text())["version"]
        return v if isinstance(v, str) and VERSION_RE.fullmatch(v) else ""
    except (OSError, ValueError, KeyError, TypeError):
        return ""


class Cell:
    def __init__(self, root: Path, system: System, host_root: Path = Path("/"), compose_source: Path | None = None):
        self.root, self.system, self.host = Path(root), system, Path(host_root)
        self.compose_source = compose_source or Path(__file__).resolve().parent.parent / "compose.yaml"
        self.secrets: list[str] = []

    # paths and records
    def dir(self, slug: str) -> Path:
        return self.root / "companies" / slug

    def scrub(self, text: str) -> str:
        return envfile.scrub(text, self.secrets)

    def load_cell(self) -> dict:
        try:
            return json.loads((self.root / "cell.json").read_text())
        except (OSError, ValueError):
            raise CellError("not_initialized", "this host has no cell: run `python3 -m setup cell init` first") from None

    def load(self, slug: str) -> dict | None:
        try:
            return json.loads((self.dir(slug) / "company.json").read_text())
        except (OSError, ValueError):
            return None

    def save(self, c: dict) -> None:
        _write_atomic(self.dir(c["slug"]) / "company.json", json.dumps(c, indent=2, sort_keys=True) + "\n", 0o600)

    def companies(self) -> list[dict]:
        out = []
        with contextlib.suppress(OSError):
            for p in sorted((self.root / "companies").iterdir()):
                c = self.load(p.name)
                if c:
                    out.append(c)
        return out

    @contextlib.contextmanager
    def lock(self, name: str, timeout: float = 900):
        d = self.root / "locks"
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(d / f"{name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        deadline = self.system.monotonic() + timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as e:
                    if e.errno not in (errno.EAGAIN, errno.EACCES):
                        raise
                    if self.system.monotonic() > deadline:
                        raise CellError("busy", f"another cell command holds the {name} lock") from None
                    self.system.sleep(0.25)
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    # docker
    def run(self, argv: list[str], code: str, what: str, **kw) -> Result:
        r = self.system.run(argv, **kw)
        if r.returncode:
            raise CellError(code, self.scrub(f"{what}: " + (r.stderr or r.stdout).strip()[-600:]))
        return r

    def compose(self, slug: str, *args: str) -> list[str]:
        d = self.dir(slug)
        return ["docker", "compose", "--project-name", project(slug), "--project-directory", str(d),
                "--env-file", str(d / ".env"), "-f", str(d / "compose.yaml"), "-f", str(d / "compose.cell.yaml"), *args]

    def containers(self, slug: str = "") -> dict[str, dict]:
        """{slug: {"state", "health"}} for the cell's server containers, from one `docker ps`."""
        flt = f"label={LABEL}" + (f"={slug}" if slug else "")
        r = self.run(["docker", "ps", "-a", "--filter", flt, "--format", "{{json .}}"], "docker_failed", "docker ps")
        out = {}
        for line in r.stdout.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            labels = dict(p.split("=", 1) for p in str(row.get("Labels", "")).split(",") if "=" in p)
            name = labels.get(LABEL, "")
            status = str(row.get("Status", ""))
            health = ("unhealthy" if "(unhealthy)" in status else "healthy" if "(healthy)" in status
                      else "starting" if "health: starting" in status else "")
            if name:
                out[name] = {"state": str(row.get("State", "")), "health": health}
        return out

    def state_of(self, c: dict, ps: dict) -> str:
        if c.get("state") == "removed":
            return "removed"
        row = ps.get(c["slug"])
        if not row:
            return "missing"
        if row["state"] != "running":
            return "stopped"
        return {"healthy": "running", "unhealthy": "unhealthy"}.get(row["health"], "starting")

    def imds_blocked(self) -> bool:
        """The metadata REJECT is the first rule of DOCKER-USER. Present further down is not enough: an earlier rule
        that accepts would let a container through before it is reached. `iptables -S` lists rules in the order they
        apply."""
        r = self.system.run(["iptables", "-S", "DOCKER-USER"])
        rules = [line.strip() for line in r.stdout.splitlines() if line.startswith("-A DOCKER-USER ")]
        return r.returncode == 0 and bool(rules) and rules[0] == IMDS_LINE

    def wait_healthy(self, c: dict, started: float, timeout: int) -> int:
        """Milliseconds from `started` (the command's start) until /healthz answers 200 through the router; the wait
        itself is up to `timeout` seconds from now, so a slow image pull does not use it up."""
        deadline = self.system.monotonic() + max(timeout, 1)
        while True:
            if self.system.http_status(c["domain"], contract.HEALTH_PATH) == 200:
                return int((self.system.monotonic() - started) * 1000)
            if self.system.monotonic() > deadline:
                logs = self.system.run(self.compose(c["slug"], "logs", "--no-color", "--tail", "15", "server"))
                tail = " | ".join(line.strip() for line in (logs.stdout or "").splitlines()[-15:] if line.strip())
                raise CellError("unhealthy", self.scrub(f"{c['domain']} did not answer {contract.HEALTH_PATH} through the "
                                                        f"router within {timeout}s" + (f"; last log lines: {tail}" if tail else "")))
            self.system.sleep(0.5)

    def write_router(self) -> bool:
        """Regenerate the Caddyfile from every company not removed and reload the router. A config the router refuses
        is replaced by the one it had, so a bad change never takes every company down."""
        path = self.root / "router" / "Caddyfile"
        text = router_config([c for c in self.companies() if c.get("state") != "removed"])
        try:
            old = path.read_text()
        except OSError:
            old = None
        if old == text:
            return False
        _write_atomic(path, text)
        r = self.system.run(["docker", "exec", ROUTER_CONTAINER, "caddy", "reload", "--config", "/etc/caddy/Caddyfile",
                             "--adapter", "caddyfile"])
        if r.returncode:
            if old is not None:
                _write_atomic(path, old)
            raise CellError("router_failed", self.scrub("the router refused the new config: " + (r.stderr or r.stdout).strip()[-400:]))
        return True

    def image_present(self, version: str) -> bool:
        return self.system.run(["docker", "image", "inspect", "--format", "{{.Id}}", f"{SERVER_IMAGE}:{version}"]).returncode == 0

    def pull(self, version: str) -> None:
        """The release's image, without touching any .env (compose.yaml's image is SERVER_IMAGE:TICO_TAG)."""
        if not self.image_present(version):
            self.run(["docker", "pull", "--quiet", f"{SERVER_IMAGE}:{version}"], "pull_failed",
                     f"pulling {SERVER_IMAGE}:{version}")

    def up(self, slug: str, version: str) -> None:
        self.pull(version)
        self.run(self.compose(slug, "up", "-d", "--remove-orphans"), "docker_failed", "docker compose up")

    def ensure_stopped(self, slug: str) -> None:
        """Stop the server, then make sure Docker says it is not running: nothing touches its database before that."""
        self.run(self.compose(slug, "stop", "server"), "stop_failed", "docker compose stop")
        state = self.containers(slug).get(slug, {}).get("state", "")
        if state not in STOPPED:
            raise CellError("stop_failed", f"the server is still {state} after docker compose stop")

    def launch(self, c: dict, started: float, timeout: int, between=None) -> int:
        """Run c's release (`between` runs once it is up, before the wait) and wait for /healthz. While an update is
        pending (`c["pending"]`: the release before it and its database snapshot, see `update`), a release that does
        not come up is rolled back; one that does ends the pending update."""
        try:
            self.up(c["slug"], c["version"])
        except CellError as e:
            self.undo_update(c, e, timeout)
            raise
        if between:
            between()
        try:
            ready = self.wait_healthy(c, started, timeout)
        except CellError as e:
            self.undo_update(c, e, timeout)
            raise
        if c.pop("pending", None) is not None:
            self.save(c)
        return ready

    def undo_update(self, c: dict, err: CellError, timeout: int) -> None:
        """After `err` from a pending update's release: the release and database from before it, then update_failed.
        Without a pending update it returns, and the caller raises `err` as it is."""
        pending = c.get("pending")
        if not pending:
            return
        new = c["version"]
        try:
            self.rollback(c, timeout)
        except CellError as r:
            r.detail = f"{new} did not come up ({err.detail}); {r.detail}"
            raise r from None
        failed = CellError("update_failed", f"{new} did not come up ({err.detail}); back on {c['version']}"
                           + (" with the database from before the update" if pending.get("snapshot") else ""))
        failed.extra = {"version": c["version"]}
        raise failed from None

    def answer(self, c: dict, ready_ms: int, changed: bool) -> dict:
        return {"ok": True, "company": c["slug"], "url": f"https://{c['domain']}", "port": c["port"], "ready_ms": ready_ms,
                "version": c["version"], "changed": changed, "owner_link_file": str(self.dir(c["slug"]) / "owner-link.txt")}

    # --- commands --------------------------------------------------------------------------------------------

    def init(self, args) -> dict:
        if not self.system.linux():
            raise CellError("unsupported_host", "cell init runs on the shared Linux host (Ubuntu 24.04)")
        try:
            saved = json.loads((self.root / "cell.json").read_text())
        except (OSError, ValueError):
            saved = {}
        domain = (args.domain or saved.get("domain") or "tico.team").strip().lower().strip(".")
        if not st.DOMAIN_RE.fullmatch(domain):
            raise CellError("bad_domain", "--domain is a plain domain such as tico.team")
        if saved.get("domain") and saved["domain"] != domain and self.companies():
            raise CellError("bad_domain", f"this cell serves {saved['domain']} and has companies; it cannot move to {domain}")
        token = read_secret(args.tunnel_token_file, "--tunnel-token-file") if args.tunnel_token_file else ""
        if token:
            self.secrets.append(token)
            if not re.fullmatch(r"[A-Za-z0-9+/=_-]{20,4096}", token):
                raise CellError("secret_file", "--tunnel-token-file: not a tunnel token")
        with self.lock("cell"):
            return self._init(saved, domain, token)

    def _init(self, saved: dict, domain: str, token: str) -> dict:
        changed = []
        if self.system.run(["docker", "compose", "version"]).returncode:
            self.run(["sh", "-c", "set -eu\n" + "\n".join(remote.docker_install_lines("ubuntu", False))],
                     "docker_failed", "installing Docker")
            changed.append("docker")
        if self.daemon_settings():
            self.run(["systemctl", "restart", "docker"], "docker_failed", "restarting Docker with the cell's settings")
            changed.append("daemon.json")
        if self.firewall():
            changed.append("firewall")
        self.run([str(self.host / FIREWALL_SCRIPT)], "firewall_failed", "blocking the metadata service from containers")
        if not self.imds_blocked():
            raise CellError("firewall_failed", "the DOCKER-USER rule that blocks 169.254.169.254 is not the chain's "
                                               "first rule")

        for d in ("companies", "router", "tunnel", "locks"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        os.chmod(self.root / "companies", 0o700)
        if token:
            changed += ["tunnel token"] if _write_atomic(self.root / "tunnel.env", f"TUNNEL_TOKEN={token}\n", 0o600) else []
        tunnel = (self.root / "tunnel.env").exists()
        routes_changed = _write_atomic(self.root / "tunnel" / "cloudflared.yml", tunnel_config(domain))
        # cloudflared runs as TUNNEL_UID and cannot read a root-owned directory: every init sets the owner again
        # (a file this run rewrote comes back root-owned, so it is covered too)
        os.chmod(self.root / "tunnel", 0o700)
        for f in (self.root / "tunnel", self.root / "tunnel" / "cloudflared.yml"):
            self.system.chown(f, TUNNEL_UID, TUNNEL_UID)
        caddyfile = self.root / "router" / "Caddyfile"
        if not caddyfile.exists():      # the first run; later ones go through write_router, which reloads
            _write_atomic(caddyfile, router_config([c for c in self.companies() if c.get("state") != "removed"]))
        if _write_atomic(self.root / "edge.yaml", edge_compose(self.root, tunnel)):
            changed.append("edge")
        edge = ["docker", "compose", "--project-name", EDGE_PROJECT, "-f", str(self.root / "edge.yaml")]
        self.run([*edge, "up", "-d", "--remove-orphans"], "docker_failed", "starting the router and tunnel")
        if tunnel and routes_changed:   # cloudflared reads its routes only when it starts
            self.run([*edge, "restart", "tunnel"], "docker_failed", "restarting the tunnel with its routes")
            changed.append("tunnel routes")
        started = self.system.monotonic()
        while self.system.http_status(ROUTER_HEALTH_HOST, ROUTER_HEALTH_PATH) != 200:
            if self.system.monotonic() - started > 60:
                raise CellError("router_failed", f"the router does not answer on {ROUTER_ADDRESS}:{ROUTER_PORT}")
            self.system.sleep(0.5)
        if self.write_router():
            changed.append("router")
        cell = {**saved, "domain": domain, "router": f"http://{ROUTER_ADDRESS}:{ROUTER_PORT}"}
        if cell != saved:
            _write_atomic(self.root / "cell.json", json.dumps(cell, indent=2, sort_keys=True) + "\n", 0o600)
            changed.append("cell.json")
        return {"ok": True, "domain": domain, "root": str(self.root), "router": cell["router"], "tunnel": tunnel,
                "companies": len(self.companies()), "changed": changed}

    def daemon_settings(self) -> bool:
        """Log rotation and live restore (a Docker restart leaves every company running). Keys already set are kept."""
        path = self.host / DAEMON_JSON
        try:
            current = json.loads(path.read_text())
        except FileNotFoundError:
            current = {}
        except (OSError, ValueError):
            raise CellError("docker_failed", f"{path} is not valid JSON; fix it and re-run") from None
        merged = {**DAEMON_SETTINGS, **current}
        if merged == current:
            return False
        _write_atomic(path, json.dumps(merged, indent=2, sort_keys=True) + "\n")
        return True

    def firewall(self) -> bool:
        rule = " ".join(IMDS_RULE[1:])
        script = "\n".join([
            "#!/bin/sh",
            "# Written by `python3 -m setup cell init`: containers on this host never reach the EC2 metadata service,",
            "# so no company can use the host's AWS role. Run before Docker starts and again after (docs/cell.md).",
            "# The rule must be the chain's first: a rule above it that accepts would be reached first.",
            "set -eu", "iptables -N DOCKER-USER 2>/dev/null || true",
            "first=$(iptables -S DOCKER-USER | grep -m1 '^-A DOCKER-USER ' || true)",
            f"[ \"$first\" = \"{IMDS_LINE}\" ] || iptables -I DOCKER-USER 1 {rule}"]) + "\n"
        unit = "\n".join([
            "[Unit]", "Description=Tico cell: containers cannot reach the EC2 metadata service", "Before=docker.service",
            "After=network-pre.target", "", "[Service]", "Type=oneshot", "RemainAfterExit=yes",
            f"ExecStart=/{FIREWALL_SCRIPT}", "", "[Install]", "WantedBy=multi-user.target"]) + "\n"
        dropin = "\n".join(["[Service]", f"ExecStartPost=/{FIREWALL_SCRIPT}"]) + "\n"
        changed = _write_atomic(self.host / FIREWALL_SCRIPT, script, 0o755)
        changed = _write_atomic(self.host / FIREWALL_UNIT, unit) or changed
        changed = _write_atomic(self.host / DOCKER_DROPIN, dropin) or changed
        if changed:
            self.run(["systemctl", "daemon-reload"], "firewall_failed", "systemctl daemon-reload")
            self.run(["systemctl", "enable", "tico-cell-firewall.service"], "firewall_failed", "enabling the firewall unit")
        return changed

    def add(self, args) -> dict:
        started = self.system.monotonic()
        slug = check_slug(args.company, new=True)
        cell = self.load_cell()
        domain = (args.domain or "").strip().lower()
        if domain != f"{slug}.{cell['domain']}":
            raise CellError("bad_domain", f"--domain must be {slug}.{cell['domain']}")
        email = (args.owner_email or "").strip()
        if not st.EMAIL_RE.fullmatch(email) or re.search(r"[^A-Za-z0-9.@_+-]", email):
            raise CellError("bad_args", "--owner-email is not a plain email address")
        issuer = (args.issuer or "").rstrip("/")
        if not signin.valid_issuer(issuer):
            raise CellError("bad_args", "--issuer is an https issuer URL with no query, fragment or credentials")
        if not CLIENT_ID_RE.fullmatch(args.client_id or ""):
            raise CellError("bad_args", "--client-id is 1 to 200 letters, digits and . _ : -")
        label = SIGNIN_LABEL if args.signin_label is None else args.signin_label
        if len(label) > 40 or any(ch in label for ch in "<>\n\r"):
            raise CellError("bad_args", "--signin-label is at most 40 characters, without < > or line breaks")
        if args.version is not None and not VERSION_RE.fullmatch(args.version):
            raise CellError("bad_args", "--version is a release such as v1.2.3")
        memory = check_memory(args.memory) if args.memory is not None else None
        backup_given = [args.backup_url, args.backup_region, args.backup_credentials_file]
        if any(backup_given) and not all(backup_given):
            raise CellError("bad_args", "--backup-url, --backup-region and --backup-credentials-file go together")
        if args.backup_url and not re.fullmatch(r"s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9](/[A-Za-z0-9._/-]*)?", args.backup_url):
            raise CellError("bad_args", "--backup-url is s3://bucket/prefix")
        if args.backup_region:
            from .aws import REGION_RE
            if not REGION_RE.fullmatch(args.backup_region):
                raise CellError("bad_args", "--backup-region is an AWS region such as us-west-2")
        if args.company_name is not None and (not args.company_name.strip() or len(args.company_name) > 80
                                              or any(ord(ch) < 32 for ch in args.company_name)):
            raise CellError("bad_args", "--company-name is 1 to 80 characters on one line")

        client_secret = read_secret(args.client_secret_file, "--client-secret-file")
        self.secrets.append(client_secret)
        invite = read_secret(args.gateway_invite_file, "--gateway-invite-file") if args.gateway_invite_file else None
        if invite is not None:
            self.secrets.append(invite)
            if not st.INVITE_RE.fullmatch(invite):
                raise CellError("secret_file", "--gateway-invite-file: not an invite code (8 to 200 letters, digits, - and _)")
        keys = None
        if args.backup_credentials_file:
            keys = backup_keys(read_secret(args.backup_credentials_file, "--backup-credentials-file"))
            self.secrets.append(keys[1])
        plan = None
        if args.company_file:
            from .wizard import gather_company_file
            probe = st.Settings()
            try:
                gather_company_file(argparse.Namespace(company_file=args.company_file, company=None), probe)
            except MissingInput as e:
                raise CellError("company_file", str(e)) from None
            plan = (probe.company_file_data, probe.company)

        with self.lock("company-" + slug):
            with self.lock("cell"):
                if not self.imds_blocked():
                    raise CellError("not_initialized", "containers on this host can reach the EC2 metadata service "
                                                       "(the DOCKER-USER block is missing or not first); run "
                                                       "`python3 -m setup cell init`")
                old = self.load(slug)
                if old and args.version and args.version != old["version"]:
                    # A new release migrates the database: that is `update`, with its snapshot and rollback.
                    raise CellError("use_update", f"{slug} runs {old['version']}; change its release with `python3 -m "
                                                  f"setup cell update --company {slug} --version {args.version}`")
                for other in self.companies():
                    if other["slug"] != slug and other["domain"] == domain and other.get("state") != "removed":
                        raise CellError("domain_taken", f"{domain} belongs to {other['slug']}")
                index = old["index"] if old else self.allocate()
                d = self.dir(slug)
                old_env = envfile.parse(_read(d / ".env"))
                c = {"slug": slug, "domain": domain, "index": index, "port": PORT_BASE + index,
                     "memory": memory or (old or {}).get("memory") or MEMORY_DEFAULT,
                     "version": args.version or (old or {}).get("version") or self.default_version(),
                     "hosted": args.hosted or (old or {}).get("hosted") or HOSTED_DEFAULT,
                     "created": (old or {}).get("created") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "state": "running"}
                if (old or {}).get("pending"):  # an update not yet seen healthy: this start is still guarded by it
                    c["pending"] = old["pending"]
                if plan is None and (d / contract.COMPANY_FILE_DIR / contract.COMPANY_FILE_NAME).exists():
                    data = (d / contract.COMPANY_FILE_DIR / contract.COMPANY_FILE_NAME).read_text().strip()
                    plan = (data, old_env.get("TICO_TEAM_NAME", ""))
                name = (plan[1] if plan else "") or args.company_name or old_env.get("TICO_TEAM_NAME") \
                    or slug.replace("-", " ").title()
                s = st.Settings(
                    target="local", domain=domain, front_door="caddy", updater=False, company=name, owner_email=email,
                    auth="oidc", issuer=issuer, client_id=args.client_id, client_secret=client_secret, signin_label=label,
                    hosted=c["hosted"], hosted_zone=cell["domain"], preset="recommended", support="on",
                    gateway_invite=old_env.get("TICO_GATEWAY_INVITE", "") if invite is None else invite,
                    tag=c["version"], company_file_data=plan[0] if plan else "",
                    owner_link_hash=old_env.get("TICO_OWNER_LINK_HASH", ""))
                if keys:
                    s.backup_url, s.backup_region, (s.backup_key_id, s.backup_secret) = args.backup_url, args.backup_region, keys
                elif old_env.get("TICO_BACKUP_URL"):
                    s.backup_url, s.backup_region = old_env["TICO_BACKUP_URL"], old_env.get("TICO_BACKUP_REGION", "")
                    s.backup_key_id = old_env.get("LITESTREAM_ACCESS_KEY_ID", "")
                    s.backup_secret = old_env.get("LITESTREAM_SECRET_ACCESS_KEY", "")
                self.secrets += [x for x in s.secrets() if x not in self.secrets]
                n = networks(index)
                s.trusted_proxies = f"127.0.0.1/32,::1/128,{n['default_gateway']}/32,{n['edge_gateway']}/32"
                s.client_ip_header = "cf-connecting-ip"     # Cloudflare sets it at the edge; the router passes it on
                s.edge_subnet, s.edge_dynamic_range = n["edge_subnet"], n["edge_range"]
                s.caddy_address, s.cloudflared_address = n["caddy_address"], n["cloudflared_address"]
                d.mkdir(parents=True, exist_ok=True)
                os.chmod(d, 0o700)
                if not s.owner_link_hash:
                    s.owner_link_hash = hosted.new_link(s, d / "owner-link.txt")
                try:
                    env = s.to_env()
                except MissingInput as e:
                    raise CellError("bad_args", self.scrub(str(e))) from None
                env.update(TICO_PORT=str(c["port"]), COMPOSE_PROFILES="", COMPOSE_PROJECT_NAME=project(slug),
                           TICO_UPDATER_URL="", TICO_UPDATES=UPDATES_BY,
                           TICO_COMPANY_FILE_DIR=str(d / contract.COMPANY_FILE_DIR))
                changed = self.write_company(c, envfile.render(env), s.company_file_data)
                self.save(c)
            before = self.containers(slug).get(slug, {})
            routed = []

            def route():
                with self.lock("cell"):
                    routed.append(self.write_router())
            ready = self.launch(c, started, args.timeout, route)
            changed = changed or any(routed) or before.get("state") != "running" or before.get("health") != "healthy"
            return self.answer(c, ready, changed)

    def allocate(self) -> int:
        used = {c["index"] for c in self.companies()}
        for i in range(MAX_COMPANIES):
            if i not in used and self.system.port_free(PORT_BASE + i):
                return i
        raise CellError("full", f"this host has no free company slot ({MAX_COMPANIES} at most)")

    def default_version(self) -> str:
        v = _installer_version() or self.system.latest_release()
        if not VERSION_RE.fullmatch(v or ""):
            raise CellError("bad_args", "no release to run: pass --version vX.Y.Z")
        return v

    def write_company(self, c: dict, env_text: str, plan: str) -> bool:
        d = self.dir(c["slug"])
        try:
            compose = self.compose_source.read_text()
        except OSError:
            raise CellError("internal", f"no compose.yaml at {self.compose_source}") from None
        data, files = d / "data", d / contract.COMPANY_FILE_DIR
        for p in (data, files):
            p.mkdir(exist_ok=True)
            os.chmod(p, 0o700)
            self.system.chown(p, SERVER_UID, SERVER_UID)
        changed = _write_atomic(d / "compose.yaml", compose)
        changed = _write_atomic(d / "compose.cell.yaml", company_override(c, data)) or changed
        changed = _write_atomic(d / ".env", env_text, 0o600) or changed
        if plan:
            f = files / contract.COMPANY_FILE_NAME
            if _write_atomic(f, plan + "\n", 0o600):
                changed = True
            self.system.chown(f, SERVER_UID, SERVER_UID)
        return changed

    def company(self, slug: str) -> dict:
        c = self.load(check_slug(slug))
        if not c:
            raise CellError("not_found", f"no company {slug} on this host")
        return c

    def list(self, args) -> dict:
        ps = self.containers() if self.companies() else {}
        return {"ok": True, "companies": [
            {"company": c["slug"], "url": f"https://{c['domain']}", "port": c["port"], "version": c["version"],
             "memory": c["memory"], "state": self.state_of(c, ps)} for c in self.companies()]}

    def status(self, args) -> dict:
        c = self.company(args.company)
        ps = self.containers(c["slug"])
        state = self.state_of(c, ps)
        healthy = state != "removed" and self.system.http_status(c["domain"], contract.HEALTH_PATH) == 200
        return {"ok": True, "company": c["slug"], "url": f"https://{c['domain']}", "port": c["port"], "version": c["version"],
                "memory": c["memory"], "state": state, "healthy": healthy,
                "container": ps.get(c["slug"], {"state": "", "health": ""})}

    def live(self, slug: str) -> dict:
        """The company's record, read under its lock (so another command cannot have changed it since); not_found when
        there is none or it was removed."""
        c = self.company(slug)
        if c.get("state") == "removed":
            raise CellError("not_found", f"{c['slug']} was removed; add it again first")
        return c

    def stop(self, args) -> dict:
        slug = check_slug(args.company)
        with self.lock("company-" + slug):
            c = self.live(slug)
            was = self.containers(slug).get(slug, {}).get("state")
            self.ensure_stopped(slug)
            c["state"] = "stopped"
            self.save(c)
        return {"ok": True, "company": slug, "state": "stopped", "changed": was == "running"}

    def start(self, args) -> dict:
        started = self.system.monotonic()
        slug = check_slug(args.company)
        with self.lock("company-" + slug):
            c = self.live(slug)
            before = self.containers(slug).get(slug, {})
            c["state"] = "running"
            self.save(c)
            ready = self.launch(c, started, args.timeout)
        return self.answer(c, ready, before.get("state") != "running")

    def update(self, args) -> dict:
        """Another release, with the database safe first. The image is pulled before anything changes (a release that
        cannot be pulled leaves the company as it was). Then the server is stopped (Docker must say so), its database
        copied to snapshots/, and only then do .env and company.json name the new release, with `pending` = the release
        before and the snapshot. A running company starts the new release at once; a stopped one stays stopped and
        `start` (or `add`) starts it. Either way the first start is guarded by `launch`: no /healthz, and the old
        release and database come back. `pending` is saved, so a command that dies half-way is still rolled back by the
        next start. A stop or snapshot that fails changes nothing and the old release is started again."""
        started = self.system.monotonic()
        if not VERSION_RE.fullmatch(args.version or ""):
            raise CellError("bad_args", "--version is a release such as v1.2.3")
        slug = check_slug(args.company)
        with self.lock("company-" + slug):
            c = self.live(slug)
            d = self.dir(slug)
            env_text = _read(d / ".env")
            self.secrets += [v for k, v in envfile.parse(env_text).items() if k in contract.SECRET_KEYS and v]
            running = c.get("state") != "stopped"
            pending = c.get("pending")
            if c["version"] == args.version:
                if running and pending:
                    ready = self.launch(c, started, args.timeout)
                else:
                    ready = self.wait_healthy(c, started, args.timeout) if running else 0
                return self.answer(c, ready, False)
            self.pull(args.version)
            try:
                self.ensure_stopped(slug)
                # An update still pending keeps its release and snapshot: the database has not moved on since.
                snap = pending or {"from": c["version"], "snapshot": self.snapshot(slug, c["version"])}
            except CellError as e:
                raise self.restarted(c, e, running) from None
            if pending and pending["from"] == args.version:
                # Back to the release its database still belongs to: the pending update's own rollback.
                self.rollback(c, args.timeout, restart=running)
                return self.answer(c, self.wait_healthy(c, started, args.timeout) if running else 0, True)
            try:
                _write_atomic(d / ".env", _with_tag(env_text, args.version), 0o600)
            except OSError as e:
                raise self.restarted(c, CellError("internal", f"writing .env: {e}"), running) from None
            c["version"], c["pending"] = args.version, snap
            self.save(c)
            if not running:
                return self.answer(c, 0, True)
            return self.answer(c, self.launch(c, started, args.timeout), True)

    def restarted(self, c: dict, err: CellError, running: bool) -> CellError:
        """`err` from before an update changed anything, after starting the old release again if it ran."""
        if running:
            r = self.system.run(self.compose(c["slug"], "up", "-d", "--remove-orphans"))
            err.detail += (f"; still on {c['version']}" if not r.returncode else
                           f"; starting {c['version']} again failed too: " + (r.stderr or r.stdout).strip()[-300:])
        err.extra = {"version": c["version"]}
        return err

    def snapshot(self, slug: str, version: str) -> str:
        """The stopped server's database, before a new release migrates it, in snapshots/<name>; the name, or "" when
        there is no database yet. The last SNAPSHOTS_KEPT are kept."""
        data, snaps = self.dir(slug) / "data", self.dir(slug) / "snapshots"
        if not (data / "hub.sqlite").exists():
            return ""
        target = snaps / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{version}"
        try:
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            for name in DB_FILES:
                if (data / name).exists():
                    shutil.copy2(data / name, target / name)
        except OSError as e:
            shutil.rmtree(target, ignore_errors=True)
            raise CellError("snapshot_failed", f"copying the database to {target}: {e.strerror or e}") from None
        for old in sorted(p for p in snaps.iterdir() if p.is_dir())[:-SNAPSHOTS_KEPT]:
            shutil.rmtree(old, ignore_errors=True)
        return target.name

    def rollback(self, c: dict, timeout: int, *, restart: bool = True) -> None:
        """Back to the release and database from before c's pending update. The database files are replaced only once
        Docker says the server is stopped; then the old release must answer /healthz. Raises rollback_failed, saying
        what state the company was left in."""
        slug, pending = c["slug"], c["pending"]
        old, data = pending["from"], self.dir(slug) / "data"

        def failed(detail: str) -> CellError:
            e = CellError("rollback_failed", self.scrub(detail))
            e.extra = {"version": c["version"]}
            return e

        try:
            self.ensure_stopped(slug)
        except CellError as e:
            raise failed(f"could not stop {c['version']} to put {old} back, so its database was left as it is: "
                         f"{e.detail}") from None
        if pending.get("snapshot"):
            snapshot = self.dir(slug) / "snapshots" / pending["snapshot"]
            try:
                if not (snapshot / "hub.sqlite").is_file():
                    raise FileNotFoundError(f"{snapshot} has no hub.sqlite")
                for name in DB_FILES:
                    with contextlib.suppress(FileNotFoundError):
                        (data / name).unlink()
                    if (snapshot / name).exists():
                        shutil.copy2(snapshot / name, data / name)
                        self.system.chown(data / name, SERVER_UID, SERVER_UID)
            except OSError as e:
                raise failed(f"could not put the database from {old} back ({e}); the server is stopped on "
                             f"{c['version']}") from None
        env = self.dir(slug) / ".env"
        _write_atomic(env, _with_tag(_read(env), old), 0o600)
        c["version"] = old
        c.pop("pending", None)
        self.save(c)
        if not restart:
            return
        r = self.system.run(self.compose(slug, "up", "-d", "--remove-orphans"))
        if r.returncode:
            raise failed(f"{old} and its database are back, but starting it failed: "
                         + (r.stderr or r.stdout).strip()[-300:])
        try:
            self.wait_healthy(c, self.system.monotonic(), timeout)
        except CellError as e:
            raise failed(f"{old} and its database are back, but it does not answer: {e.detail}") from None

    def remove(self, args) -> dict:
        """The company's slot (its index, so its port) stays taken until the router no longer sends its domain there:
        the record is marked removed and the router reloaded under the cell lock, and only then is the folder deleted.
        A router that refuses the new config leaves the record (and the slot) in place; running remove again finishes."""
        slug = check_slug(args.company)
        with self.lock("company-" + slug):
            c = self.load(slug)
            d = self.dir(slug)
            if not c and not d.exists():
                return {"ok": True, "company": slug, "removed": False, "kept_data": False}
            if (d / ".env").exists() and (d / "compose.cell.yaml").exists():
                down = ["down", "--remove-orphans"] + ([] if args.keep_data else ["--volumes"])
                self.run(self.compose(slug, *down), "docker_failed", "docker compose down")
            with self.lock("cell"):
                if c and c.get("state") != "removed":
                    c["state"] = "removed"
                    self.save(c)
                self.write_router()
                if not args.keep_data:
                    shutil.rmtree(d)
        return {"ok": True, "company": slug, "removed": True, "kept_data": bool(args.keep_data)}


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _with_tag(env_text: str, version: str) -> str:
    """The same .env with another release, written exactly as `add` would write it."""
    return envfile.render({**envfile.parse(env_text), "TICO_TAG": version})


def main(argv: list[str], *, system: System | None = None, root: str | Path | None = None,
         host_root: str | Path = "/", compose_source: Path | None = None, out=None) -> int:
    out = out or sys.stdout
    cell = Cell(Path(root or os.environ.get("TICO_CELL_ROOT") or ROOT), system or System(), Path(host_root), compose_source)
    try:
        args = parser().parse_args(argv)
        result = getattr(cell, args.action)(args)
    except CellError as e:
        result = {"ok": False, "error": e.code, "detail": cell.scrub(e.detail), **e.extra}
    except Exception as e:  # noqa: BLE001  an answer HQ can read, even for a bug; the secrets scrubbed out of it
        result = {"ok": False, "error": "internal", "detail": cell.scrub(f"{type(e).__name__}: {e}")[-600:]}
    out.write(json.dumps(result) + "\n")
    out.flush()
    return 0 if result.get("ok") else 1
