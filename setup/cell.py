"""Many companies' Tico servers on one shared host: `python3 -m setup cell ...` (docs/cell.md).

Tico HQ drives this over AWS SSM, so it is a machine interface: every command prints exactly one JSON object on stdout,
`{"ok": true, ...}` or `{"ok": false, "error": "<code>", "detail": "..."}` (exit 0 or 1), takes secrets only from files
that no one else can read (mode 0600 or stricter), never from argv, and never prints them. Every command is idempotent:
`add` again with the same settings changes nothing and answers the same; an optional input left out on a re-run keeps
what was saved.

Each company is the server a hosted install makes (the stock image and entrypoint, `Settings.to_env()` for its .env,
the owner link as break-glass, the recommended preset), minus its own Caddy, cloudflared, updater sidecar and EC2
instance:

    <root>/cell.json                        the host: the domain every company is a name under, the router's port
    <root>/edge.yaml, router/Caddyfile      the router (Caddy, plain HTTP on 127.0.0.1:8080) and the one tunnel
    <root>/tunnel/, tunnel.env (0600)       cloudflared's routes and its token
    <root>/companies/<slug>/.env            0600, the server's settings
    <root>/companies/<slug>/compose.yaml    the stock compose.yaml, unchanged
    <root>/companies/<slug>/compose.cell.yaml   memory limit, data folder, networks: what makes it one of many
    <root>/companies/<slug>/extra.env       0600, settings carried from an install moved here (`add --env-file`)
    <root>/companies/<slug>/company.json    what the cell knows about it (no secrets): port, index, version, state,
                                            aliases
    <root>/companies/<slug>/data/           the server's /data (owned by the server's user, 10001)
    <root>/companies/<slug>/control/        the server's /control: updater-token (0600, 10001), its updater's secret
    <root>/companies/<slug>/updater-status.json   the last outcome its updater reports (setup/cell_updater.py)
    <root>/locks/                           one lock for the host's shared files, one per company

A company's index (the smallest free one, kept until `remove` without --keep-data) gives it its loopback port
(20000 + index) and its two private networks, so no two companies ever share one, across restarts too. The router maps
the Host header to that port: the company's domain and its aliases (`--alias`), no host on two companies. cloudflared
sends `*.<domain>` to the router, and before that wildcard, each company host it does not match (it matches exactly one
label: hub.tico.team yes; a.b.tico.team, tidy.rocks no). `add` and `remove` rewrite that list when it changes and
restart cloudflared then (it reads its config only when it starts; every company drops for those seconds). DNS for such
a host (a proxied CNAME to the tunnel, or a record of its own that points elsewhere today) is staff's job. Containers
cannot reach the EC2 metadata service (a DOCKER-USER rule, first in its chain, that `init` installs and `add` checks), so
no company can use the host's AWS role. `update` snapshots the database before a new release first starts, and puts it
back if that release does not come up.

The router holds a request while its company's server is down for a moment (a restart, an update): a dial that fails
is tried again every 250 ms for up to 30 s, and only then answered, with a plain 503 and Retry-After, never a 502.
`sleep` stops a company's server and sends its hosts to the wake service instead (setup/cell_wake.py, systemd unit
tico-cell-wake, which `init` installs): its first request starts the server with `wake` and is answered by it, and
`wake` sends the hosts straight to the server again. A stopped company (`stop`) is answered 503 at once.
`sleep --wake-at` (and the updater's POST /sleep) also records a timed wake in the company's record: the wake service
starts the company when that time is due, rather than waiting for a request. Starting, waking or stopping a company
clears it.

Each company also has an updater, so its Update now, an update service key (the Release Manager's rollout) and Tico
support's idle-time target work as on any install: `init` runs one host service (setup/cell_updater.py, systemd unit
tico-cell-updater) that speaks docker/updater.py's contract to each company on that company's own default-network
gateway address only (`updater_url`), checks the token in that company's own control/updater-token, and does the work
with this file's `update` for that company alone. The .env names it (TICO_UPDATER_URL) and marks the server a cell
company (TICO_CELL=1, for its cloud-access cadence). `update`, even to the release a company already runs, rewrites the
files `add` derives from the record (compose.yaml, compose.cell.yaml, the updater keys in .env, the token) from this
core, and restarts the server only when one changed: that is how a company made by an older core picks this up.

Moving an existing install here (staff only; HQ never does): `--own-domain` lets --domain be any hostname instead of
<slug>.<domain>; `--alias` adds hostnames (its runner and visual addresses); `--env-file` carries its other settings
(kept in extra.env and merged into every .env `add` and `update` write; a key the cell writes with a value, or owns, is
refused and named in "ignored"); `--import-data` unpacks its /data archive, checked whole first (no absolute names, no
`..`, no symlinks, hard links only to its own files, no special files), into an empty data folder before the first
start, through a staging folder renamed into place.

All Docker and system calls go through one `System`, so the tests run every command with a fake.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import errno
import fcntl
import http.client
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zlib
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
MAX_ALIASES = 5

# --env-file: what a moved install brings. Never carried: keys the cell sets itself (with a value, in this render: see
# `add`), the ones below (the cell's own or read back from .env by the next `add`, so a carried one would feed back),
# and what belongs to a single-company install only.
ENV_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
CELL_ENV_KEYS = frozenset({
    "TICO_TEAM_NAME", "TICO_COMPANY_NAME", "TICO_OWNER_EMAIL", "TICO_DOMAIN", "TICO_GATEWAY_INVITE",
    "TICO_OWNER_LINK_HASH", "TICO_BACKUP_URL", "TICO_BACKUP_REGION", "TICO_BACKUP_ENDPOINT", "LITESTREAM_ACCESS_KEY_ID",
    "LITESTREAM_SECRET_ACCESS_KEY", "TICO_COMPANY_FILE", "TICO_COMPANY_FILE_SHA256", "TICO_COMPANY_FILE_B64",
    "TICO_COMPANY_FILE_S3", "TICO_COMPANY_FILE_S3_REGION", "TICO_COMPANY_FILE_DIR", "TICO_SAMPLE_COMPANY",
    "TICO_UPDATES", "TICO_CELL", "TICO_HOSTED", "TICO_AUTH_PROXY", "TICO_OIDC_ISSUER", "TICO_OIDC_CLIENT_ID",
    "TICO_OIDC_CLIENT_SECRET", "TICO_OIDC_LABEL", "TICO_ACCESS_ISSUER", "TICO_ACCESS_AUDIENCE", "TICO_TRUSTED_PROXIES",
    "TICO_CLIENT_IP_HEADER", "TICO_EDGE_SUBNET", "TICO_EDGE_DYNAMIC_RANGE", "TICO_CLOUDFLARED_ADDRESS",
    "TICO_INSTALL_PRESET", "TICO_MODELS_PRESET", "TICO_SUPPORT",
    "CLOUDFLARE_TUNNEL_TOKEN", "COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME", "TICO_UPDATER_URL", "TICO_TAG", "TICO_PORT",
    "TICO_EXPECTED_AWS_ACCOUNT", "TICO_EXPECTED_AWS_ROLE"})
CELL_ENV_PREFIXES = ("TICO_CADDY", "TICO_TUNNEL")
IMPORT_STAGING = ".data-import"         # beside data/: an import is unpacked here, then renamed into place

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
# Each company's updater (setup/cell_updater.py): this port on its default network's gateway address, nowhere else.
UPDATER_PORT = 8090
UPDATER_UNIT = "etc/systemd/system/tico-cell-updater.service"
UPDATER_SERVICE = "tico-cell-updater.service"
TOKEN_NAME = "updater-token"            # in <company>/control, the server's /control (docker/entrypoint.sh reads it)
# The wake service (setup/cell_wake.py): the router sends a sleeping company's hosts here, naming the company in a
# header the route itself sets (a visitor's own copy of it is replaced).
WAKE_ADDRESS, WAKE_PORT = "127.0.0.1", 8082
WAKE_HEADER = "X-Tico-Cell-Wake"
WAKE_UNIT = "etc/systemd/system/tico-cell-wake.service"
WAKE_SERVICE = "tico-cell-wake.service"
# The hold: a dial to a server that is down (restarting, updating) is tried again, not answered with a 502. A request
# with a body is retried with its body from Caddy 2.11 on (ROUTER_IMAGE is 2.11.4); 2.10 closed the body after the
# first failed dial, so such a request got the 503. No request_buffers: in 2.10.2 it mixed up concurrent bodies.
HOLD_FOR, HOLD_EVERY = "30s", "250ms"
# A config reload (every add, remove, sleep and wake) leaves open WebSockets this long before closing them.
STREAM_CLOSE_DELAY = "5m"
HOLD_BODY = "Tico is starting. Try again in a few seconds."
HEALTH_POLL = 0.15                      # seconds between /healthz probes while a server starts
# A timed wake (`sleep --wake-at`): the record's wake_at, always UTC in this format, at most WAKE_AT_DAYS out when set.
WAKE_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
WAKE_AT_DAYS = 31
WAKE_AT_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]{1,6})?)?"
                           r"(Z|[+-][0-9]{2}:[0-9]{2})")
# A company's sleep policy (`set-sleep`, `add --sleep-minutes`): whole minutes idle before it sleeps; 0 = never sleeps.
SLEEP_MINUTES_MAX = 1440


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

    def http_status(self, host: str, path: str, timeout: float = 3, port: int = ROUTER_PORT) -> int:
        """The router's answer for `host` (0 when it does not answer); with `port`, a company server's own answer on its
        loopback port."""
        try:
            c = http.client.HTTPConnection(ROUTER_ADDRESS, port, timeout=timeout)
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

    def open_archive(self, path: str):
        """--import-data's archive, open for reading: a regular file (not through a symlink) that only its owner can
        read, or root's (an install's /data holds its keys)."""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            raise CellError("bad_archive", "--import-data: cannot open the file (missing, or a symlink)") from None
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (info.st_mode & 0o077 and info.st_uid != 0):
            os.close(fd)
            raise CellError("secret_file", "--import-data: a regular file readable by its owner only (chmod 600), "
                                           "or owned by root")
        return os.fdopen(fd, "rb")

    def linux(self) -> bool:
        return sys.platform.startswith("linux")

    def latest_release(self) -> str:
        from .wizard import _latest_release
        return _latest_release()

    def release_published(self, tag: str) -> bool | None:
        """Whether `tag` (vX.Y.Z) is a published release: its SHA256SUMS downloads, the file the sidecar updater fetches
        first. None when GitHub does not say (offline, rate-limited, a server error)."""
        url = f"https://github.com/ticoteam/tico/releases/download/{tag}/SHA256SUMS"
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                r.read(1 << 20)
                return r.status == 200
        except urllib.error.HTTPError as e:
            return False if e.code == 404 else None
        except OSError:
            return None

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


def check_sleep_minutes(value: str, flag: str) -> int:
    text = (value or "").strip()
    if not re.fullmatch(r"[0-9]{1,4}", text) or int(text) > SLEEP_MINUTES_MAX:
        raise CellError("bad_args", f"{flag} is a whole number of minutes from 0 to {SLEEP_MINUTES_MAX} "
                                    "(0 = never sleeps)")
    return int(text)


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


def check_host(value: str, flag: str, cell_domain: str) -> str:
    """--own-domain's --domain or an --alias: a plain lowercase hostname (DNS labels, a dot, no IP), not the cell's own."""
    host = (value or "").strip().lower()
    if not st.DOMAIN_RE.fullmatch(host) or host in (cell_domain, ROUTER_HEALTH_HOST):
        raise CellError("bad_domain", f"{flag} is a plain hostname such as hub.example.com (not {cell_domain} itself)")
    return host


def carried_env(text: str) -> dict[str, str]:
    """--env-file: KEY=VALUE lines (comments and blank lines allowed), read as the cell reads .env. The errors name the
    line, never a value."""
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        key, eq, _ = line.partition("=")
        if line and not line.startswith("#") and not (eq and ENV_KEY_RE.fullmatch(key.strip())):
            raise CellError("secret_file", f"--env-file: line {n} is not KEY=VALUE with KEY like TICO_RUNNER_URL")
    values = envfile.parse(text)
    for k, v in values.items():
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
            raise CellError("secret_file", f"--env-file: {k}'s value is not one line of text")
    return values


def cell_owned(key: str) -> bool:
    return key in CELL_ENV_KEYS or key.startswith(CELL_ENV_PREFIXES)


def check_members(members: list) -> int:
    """An install's /data as a tar: regular files and directories, and hard links to a file earlier in it. Refused
    whole: an absolute name, a `..`, any symlink (the server makes none), a device, fifo or other special file, and an
    archive without hub.sqlite at its top (a tar of the folder itself, not its contents, would start an empty company).
    The size of its files in bytes."""
    def inside(name: str) -> str:
        parts = [p for p in name.split("/") if p not in ("", ".")]
        if name.startswith("/") or ".." in parts:
            raise CellError("bad_archive", "--import-data: a name in it is absolute or has ..")
        return "/".join(parts)

    files, total = set(), 0
    for m in members:
        name = inside(m.name)
        if m.issym():
            raise CellError("bad_archive", "--import-data: it has a symlink")
        if m.islnk():
            if inside(m.linkname) not in files:
                raise CellError("bad_archive", "--import-data: a hard link in it points outside its own files")
            files.add(name)
        elif m.isreg():
            files.add(name)
            total += m.size
        elif not m.isdir():
            raise CellError("bad_archive", "--import-data: it has a device, fifo or other special file")
        if not name and not m.isdir():
            raise CellError("bad_archive", "--import-data: a member has no name")
    if "hub.sqlite" not in files:
        raise CellError("bad_archive", "--import-data: no hub.sqlite at its top; archive the contents of /data "
                                       "(tar -C /data .)")
    return total


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
    a.add_argument("--domain", required=True, help="<slug>.<the cell's domain>, or with --own-domain any hostname")
    a.add_argument("--own-domain", action="store_true", help="staff, moving an install: --domain is its own hostname")
    a.add_argument("--alias", action="append", metavar="HOST",
                   help=f"another hostname for the same server (repeat, at most {MAX_ALIASES}); replaces the saved list")
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
    a.add_argument("--env-file", metavar="F", help="settings carried from a moved install, KEY=VALUE (0600); kept "
                   "in extra.env, replaced when given again")
    a.add_argument("--import-data", metavar="FILE", help="a moved install's /data as .tar or .tar.gz, unpacked into an "
                   "empty data folder before the first start")
    a.add_argument("--sleep-minutes", metavar="N", help=f"the sleep policy: idle minutes before it sleeps, 0 to "
                   f"{SLEEP_MINUTES_MAX} (0 = never); kept from the record when left out")
    a.add_argument("--timeout", type=int, default=HEALTH_TIMEOUT, help="seconds to wait for /healthz")
    cmd("list", "every company on this host")
    for name, help in (("status", "one company"), ("stop", "stop one company's server; its data stays"),
                       ("start", "start it again; answers once /healthz answers"),
                       ("sleep", "stop its server; its first request starts it again (the wake service)"),
                       ("wake", "start a sleeping company's server; answers once /healthz answers")):
        c = cmd(name, help)
        c.add_argument("--company", required=True, metavar="SLUG")
        if name in ("start", "wake"):
            c.add_argument("--timeout", type=int, default=HEALTH_TIMEOUT)
        if name == "sleep":
            c.add_argument("--wake-at", metavar="TIME", help="a timed wake: an ISO-8601 UTC time such as "
                           "2026-10-30T18:00:00Z, at most 31 days out; the wake service starts the company then")
    z = cmd("set-sleep", "a company's sleep policy, which its server reads (GET /sleep); no policy until set")
    z.add_argument("--company", required=True, metavar="SLUG")
    z.add_argument("--minutes", required=True, metavar="N|default",
                   help=f"idle minutes before it sleeps, 0 to {SLEEP_MINUTES_MAX} (0 = never); default removes the policy")
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
    """The router's Caddyfile: each company's domain and aliases to its loopback port, anything else a 404. Plain HTTP on
    loopback only: the tunnel in front holds TLS. The forwarded scheme is https because that is what the visitor used.

    The hold: a dial that fails (the server is restarting or updating; nothing listens on its port) is tried again
    every HOLD_EVERY for up to HOLD_FOR, so the request waits and gets its normal answer. Only a dial is retried for
    every method (nothing reached the server); an error after the connection opened is retried for GET only, Caddy's
    default, so a write is never sent twice. What still fails is answered with a plain 503 and Retry-After, never a 502.
    The hold is only about reaching the server: a stream (SSE, a long answer) or a WebSocket, once open, has no time
    limit here, as before; SSE and unknown-length answers are flushed as they come (Caddy's own rule), and a reload
    leaves WebSockets open for STREAM_CLOSE_DELAY.

    A sleeping company's hosts go to the wake service instead, with the company named in WAKE_HEADER by the route
    itself (header_up replaces any copy the visitor sent). A stopped one is answered 503 at once."""
    lines = ["# Written by `python3 -m setup cell`; regenerated whenever a company is added, removed, stopped, started, "
             "put to sleep or woken. Do not edit.",
             "{", "\tadmin localhost:2019", "\tauto_https off", "\tpersist_config off", f"\thttp_port {ROUTER_PORT}",
             f"\tdefault_bind {ROUTER_ADDRESS}", "}", ""]
    held = [f"\t\tlb_try_duration {HOLD_FOR}", f"\t\tlb_try_interval {HOLD_EVERY}"]
    errors = ["\thandle_errors {", "\t\theader Retry-After 5", f"\t\trespond \"{HOLD_BODY}\" 503", "\t}"]
    for c in sorted(companies, key=lambda c: c["domain"]):
        sites = ", ".join(f"http://{h}:{ROUTER_PORT}" for h in hosts_of(c))
        state = c.get("state")
        if state == "stopped":
            lines += [f"{sites} {{", "\trespond \"This Tico company is stopped.\" 503", "}", ""]
        elif state == "sleeping":
            lines += [f"{sites} {{", f"\treverse_proxy {WAKE_ADDRESS}:{WAKE_PORT} {{",
                      f"\t\theader_up {WAKE_HEADER} {c['slug']}", "\t\theader_up X-Forwarded-Proto https", *held,
                      "\t\tflush_interval -1", "\t}", *errors, "}", ""]
        else:
            lines += [f"{sites} {{", f"\treverse_proxy 127.0.0.1:{int(c['port'])} {{",
                      "\t\theader_up X-Forwarded-Proto https", *held, f"\t\tstream_close_delay {STREAM_CLOSE_DELAY}",
                      "\t}", *errors, "}", ""]
    lines += [f"http://:{ROUTER_PORT} {{", f"\trespond {ROUTER_HEALTH_PATH} \"ok\" 200",
              "\trespond \"No Tico company at this address.\" 404", "}"]
    return "\n".join(lines) + "\n"


def hosts_of(c: dict) -> list[str]:
    return [c["domain"], *c.get("aliases", [])]


def tunnel_hosts(domain: str, companies: list[dict]) -> list[str]:
    """The company hosts the tunnel's `*.<domain>` does not match: Cloudflare's wildcard covers exactly one label."""
    return sorted({h for c in companies for h in hosts_of(c)
                   if not (h.endswith("." + domain) and "." not in h[:-len(domain) - 1])})


def tunnel_config(domain: str, hosts: list[str] = ()) -> str:
    """cloudflared's routes: `hosts` (see tunnel_hosts), then the wildcard, all to the router; anything else a 404.
    Without `hosts` it is the file every cell had before them, byte for byte (so no restart on upgrade)."""
    service = f"    service: http://{ROUTER_ADDRESS}:{ROUTER_PORT}"
    lines = ["# Written by `python3 -m setup cell init`: every company's name to the router; anything else a 404.",
             "ingress:"]
    for h in hosts:
        lines += [f"  - hostname: \"{h}\"", service]
    return "\n".join(lines + [f"  - hostname: \"*.{domain}\"", service, "  - service: http_status:404"]) + "\n"


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
    """The control folder replaces the stock tico-control volume (Compose merges volumes by their target), so the cell
    updater reads the token where the server reads it, and no other company's server mounts it."""
    n = networks(c["index"])
    return "\n".join([
        "# Written by `python3 -m setup cell`: what makes the stock compose.yaml beside it one company of many on a",
        "# shared host. Its own data and control folders, a memory limit, its own two networks (so none collide), the",
        "# cell marker, a label for `list`.",
        "services:", "  server:", f"    mem_limit: {c['memory']}", f"    memswap_limit: {c['memory']}",
        "    environment:", "      TICO_CELL:", "    labels:", f"      {LABEL}: {c['slug']}",
        "    volumes:", f"      - {data_dir}:/data", f"      - {data_dir.parent / 'control'}:/control",
        "networks:", "  default:", "    ipam:", "      config:", f"        - subnet: {n['default_subnet']}",
        f"          gateway: {n['default_gateway']}"]) + "\n"


def updater_url(index: int) -> str:
    """Where a company's server finds its updater: its own default network's gateway, the host side of that bridge.
    Only that company's containers are on the network; setup/cell_updater.py also refuses any other source address."""
    return f"http://{networks(index)['default_gateway']}:{UPDATER_PORT}"


def cell_env(values: dict[str, str], c: dict) -> dict[str, str]:
    """`values` (a company's .env) with the cell's updater keys: its updater's address and the cell marker, and no
    TICO_UPDATES=tico, which an older core wrote (it turns Update now and the update keys off)."""
    out = {k: v for k, v in values.items() if k != "TICO_UPDATES"}
    out.update(TICO_UPDATER_URL=updater_url(c["index"]), TICO_CELL="1")
    return out


def updater_unit(core: Path, python: str, root: Path) -> str:
    """The cell updater as a systemd service: it runs `update`, so it needs what `cell` needs (root, the Docker CLI,
    the cell's files and locks, the host's bridge addresses) and nothing a container would add. Its code is the core
    folder `init` ran from; only another `init` moves it. A stop waits for an update under way (TimeoutStopSec): the
    main process gets SIGTERM, finishes what it runs, then exits."""
    return "\n".join([
        "# Written by `python3 -m setup cell init`: each company's updater (setup/cell_updater.py, docs/cell.md).",
        "[Unit]", "Description=Tico cell: each company's updater", "After=docker.service network-online.target",
        "Wants=docker.service", "", "[Service]", "Type=simple", f"WorkingDirectory={core}",
        f"ExecStart={python} -m setup.cell_updater --root {root}", "Restart=always", "RestartSec=5",
        "KillMode=mixed", "TimeoutStopSec=1800", "NoNewPrivileges=yes", "PrivateTmp=yes", "",
        "[Install]", "WantedBy=multi-user.target"]) + "\n"


def wake_unit(core: Path, python: str, root: Path) -> str:
    """The wake service as a systemd service: it runs `wake` (Docker, the company's files and locks, the router's
    reload), so it is root like the updater, and on loopback only. A stop waits a little for a start under way; one cut
    short is finished by the next request's `wake` (Compose's up is idempotent)."""
    return "\n".join([
        "# Written by `python3 -m setup cell init`: starts a sleeping company on its first request (setup/cell_wake.py).",
        "[Unit]", "Description=Tico cell: wake sleeping companies", "After=docker.service network-online.target",
        "Wants=docker.service", "", "[Service]", "Type=simple", f"WorkingDirectory={core}",
        f"ExecStart={python} -m setup.cell_wake --root {root}", "Restart=always", "RestartSec=1",
        "KillMode=mixed", "TimeoutStopSec=60", "NoNewPrivileges=yes", "PrivateTmp=yes", "",
        "[Install]", "WantedBy=multi-user.target"]) + "\n"


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
        self.lock_wait = 900.0              # the cell updater waits for none: a held lock is another command at work
        self.taken = ""                     # the snapshot the last `update` took or kept, for the updater's /status
        self.direct = False                 # health on the server's own port, not through the router (`wake`)

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
    def lock(self, name: str, timeout: float | None = None):
        d = self.root / "locks"
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(d / f"{name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        deadline = self.system.monotonic() + (self.lock_wait if timeout is None else timeout)
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
        if c.get("state") == "sleeping" and (not row or row["state"] != "running"):
            return "sleeping"
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
        """Milliseconds from `started_at` (the command's start) until /healthz answers 200 through the router (with
        `direct`, on the server's own loopback port: a sleeping company's route leads to the wake service); the wait
        itself is up to `timeout` seconds from now, so a slow image pull does not use it up."""
        deadline = self.system.monotonic() + max(timeout, 1)
        while True:
            if self.direct:
                status = self.system.http_status(c["domain"], contract.HEALTH_PATH, timeout=1, port=int(c["port"]))
            else:
                status = self.system.http_status(c["domain"], contract.HEALTH_PATH)
            if status == 200:
                return int((self.system.monotonic() - started) * 1000)
            if self.system.monotonic() > deadline:
                logs = self.system.run(self.compose(c["slug"], "logs", "--no-color", "--tail", "15", "server"))
                tail = " | ".join(line.strip() for line in (logs.stdout or "").splitlines()[-15:] if line.strip())
                where = f"on its port {c['port']}" if self.direct else "through the router"
                raise CellError("unhealthy", self.scrub(f"{c['domain']} did not answer {contract.HEALTH_PATH} {where} "
                                                        f"within {timeout}s" + (f"; last log lines: {tail}" if tail else "")))
            self.system.sleep(HEALTH_POLL)

    def write_router(self) -> bool:
        """Regenerate the Caddyfile from every company not removed and reload the router. A config the router refuses
        is replaced by the one it had, so a bad change never takes every company down."""
        path = self.root / "router" / "Caddyfile"
        text = router_config(self.live_companies())
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

    def edge(self) -> list[str]:
        return ["docker", "compose", "--project-name", EDGE_PROJECT, "-f", str(self.root / "edge.yaml")]

    def live_companies(self) -> list[dict]:
        return [c for c in self.companies() if c.get("state") != "removed"]

    def tunnel_text(self, domain: str) -> str:
        return tunnel_config(domain, tunnel_hosts(domain, self.live_companies()))

    def write_tunnel(self) -> bool:
        """Under the cell lock: regenerate cloudflared's routes and, when the file changed and the tunnel runs, restart
        it (cloudflared reads them only when it starts, as `init` does). A restart that fails puts the old file back,
        so the next `add` or `remove` tries again."""
        path = self.root / "tunnel" / "cloudflared.yml"
        text = self.tunnel_text(self.load_cell()["domain"])
        try:
            old = path.read_text()
        except OSError:
            old = None
        if old == text:
            return False

        def put(body: str) -> None:
            _write_atomic(path, body)
            self.system.chown(path, TUNNEL_UID, TUNNEL_UID)     # a rewritten file comes back root-owned
        put(text)
        if (self.root / "tunnel.env").exists():
            r = self.system.run([*self.edge(), "restart", "tunnel"])
            if r.returncode:
                if old is not None:
                    put(old)
                raise CellError("docker_failed", self.scrub("restarting the tunnel with its new routes: "
                                                            + (r.stderr or r.stdout).strip()[-400:]))
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
        routes_changed = _write_atomic(self.root / "tunnel" / "cloudflared.yml", self.tunnel_text(domain))
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
        edge = self.edge()
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
        if self.updater_service():
            changed.append("updater")
        if self.wake_service():
            changed.append("wake")
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

    def updater_service(self) -> bool:
        """Install and run the cell updater from this core. A changed unit (the first `init`, or one from another core
        folder) restarts it without waiting (`--no-block`): its stop lets an update under way finish first. An unchanged
        one is only started if it is not running."""
        unit = updater_unit(Path(__file__).resolve().parent.parent, sys.executable, self.root)
        return self.host_service(UPDATER_UNIT, UPDATER_SERVICE, unit, "the cell updater")

    def wake_service(self) -> bool:
        """The wake service (setup/cell_wake.py), installed and run as the updater is."""
        unit = wake_unit(Path(__file__).resolve().parent.parent, sys.executable, self.root)
        return self.host_service(WAKE_UNIT, WAKE_SERVICE, unit, "the wake service")

    def host_service(self, path: str, service: str, unit: str, what: str) -> bool:
        if _write_atomic(self.host / path, unit):
            self.run(["systemctl", "daemon-reload"], "updater_failed", "systemctl daemon-reload")
            self.run(["systemctl", "enable", service], "updater_failed", f"enabling {what}")
            self.run(["systemctl", "restart", "--no-block", service], "updater_failed", f"starting {what}")
            return True
        if self.system.run(["systemctl", "is-active", "--quiet", service]).returncode:
            self.run(["systemctl", "start", "--no-block", service], "updater_failed", f"starting {what}")
            return True
        return False

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
        if args.own_domain:
            domain = check_host(domain, "--domain", cell["domain"])
        elif domain != f"{slug}.{cell['domain']}":
            raise CellError("bad_domain", f"--domain must be {slug}.{cell['domain']} (or, moving an install of its own, "
                                          "pass --own-domain)")
        aliases = None
        if args.alias is not None:          # given: replaces the saved list (--alias "" alone clears it)
            aliases = [check_host(a, "--alias", cell["domain"]) for a in args.alias if a.strip()]
            if len(aliases) > MAX_ALIASES:
                raise CellError("bad_args", f"--alias is given at most {MAX_ALIASES} times")
            if len(set(aliases)) != len(aliases):
                raise CellError("bad_domain", "--alias names the same host twice")
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
        sleep_minutes = check_sleep_minutes(args.sleep_minutes, "--sleep-minutes") \
            if args.sleep_minutes is not None else None
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
        carried = None
        if args.env_file:
            carried = carried_env(read_secret(args.env_file, "--env-file"))
            self.secrets += [v for v in carried.values() if v]
        plan = None
        if args.company_file:
            from .wizard import gather_company_file
            probe = st.Settings()
            try:
                gather_company_file(argparse.Namespace(company_file=args.company_file, company=None), probe)
            except MissingInput as e:
                raise CellError("company_file", str(e)) from None
            plan = (probe.company_file_data, probe.company)

        with self.lock("company-" + slug), contextlib.ExitStack() as cleanup:
            # Unpacked under this company's lock only (it can take minutes), then renamed into place under the cell's.
            staged = self.stage_import(args.import_data, self.dir(slug)) if args.import_data else None
            if staged:
                cleanup.callback(self.drop_import, staged)     # whatever is left of it, once this is done
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
                aliases = (old or {}).get("aliases", []) if aliases is None else aliases
                if domain in aliases:
                    raise CellError("bad_domain", f"--alias {domain} is the company's --domain")
                mine = {domain, *aliases}
                for other in self.live_companies():
                    clash = sorted(mine & set(hosts_of(other))) if other["slug"] != slug else []
                    if clash:
                        raise CellError("domain_taken", f"{clash[0]} belongs to {other['slug']}")
                index = old["index"] if old else self.allocate()
                d = self.dir(slug)
                old_env = envfile.parse(_read(d / ".env"))
                c = {"slug": slug, "domain": domain, "index": index, "port": PORT_BASE + index,
                     "memory": memory or (old or {}).get("memory") or MEMORY_DEFAULT,
                     "version": args.version or (old or {}).get("version") or self.default_version(),
                     "hosted": args.hosted or (old or {}).get("hosted") or HOSTED_DEFAULT,
                     "created_at": (old or {}).get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "state": "running"}
                if aliases:
                    c["aliases"] = aliases
                sleep_minutes = sleep_minutes if sleep_minutes is not None else (old or {}).get("sleep_minutes")
                if sleep_minutes is not None:
                    c["sleep_minutes"] = sleep_minutes
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
                           TICO_COMPANY_FILE_DIR=str(d / contract.COMPANY_FILE_DIR))
                env = cell_env(env, c)
                # Carried settings fill only what the cell leaves unset: a key it writes with a value is its own.
                written = {k for k, v in env.items() if v}
                extra = self.extra_env(slug) if carried is None else carried
                ignored = sorted(k for k in extra if k in written or cell_owned(k))
                extra = {k: v for k, v in extra.items() if v and k not in ignored}
                env.update(extra)
                changed = False
                if carried is not None:
                    changed = _write_atomic(d / "extra.env", "# Carried from the install this company moved from "
                                            "(`python3 -m setup cell add --env-file`). Keep it private (0600).\n"
                                            + envfile.render(extra).split("\n", 1)[1], 0o600)
                if staged:
                    self.place_import(staged, d)
                    changed = True
                changed = self.write_company(c, envfile.render(env), s.company_file_data) or changed
                self.save(c)
            before = self.containers(slug).get(slug, {})
            routed = []

            def route():
                with self.lock("cell"):
                    routed.append(self.write_router())
                    routed.append(self.write_tunnel())
            ready = self.launch(c, started, args.timeout, route)
            changed = changed or any(routed) or before.get("state") != "running" or before.get("health") != "healthy"
            answer = self.answer(c, ready, changed)
            if carried is not None:
                answer["ignored"] = ignored
                answer["unused"] = sorted(set(extra) - self.compose_keys())
            if staged:
                answer.update(imported=True, imported_bytes=staged[1])
            return answer

    def extra_env(self, slug: str) -> dict[str, str]:
        """extra.env (settings carried from a moved install), minus anything the cell owns; its values are secrets."""
        values = {k: v for k, v in envfile.parse(_read(self.dir(slug) / "extra.env")).items()
                  if v and not cell_owned(k)}
        self.secrets += [v for v in values.values() if v not in self.secrets]
        return values

    def compose_keys(self) -> set[str]:
        """The settings the stock compose.yaml passes on: a carried key outside them reaches no container."""
        text = _read(self.compose_source)
        return set(re.findall(r"^\s+([A-Z][A-Z0-9_]*):", text, re.M)) | set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", text))

    def stage_import(self, path: str, d: Path) -> tuple[Path, int, bool]:
        """--import-data, under the company's lock: refused (data_exists) unless data/ is empty or missing; the whole
        archive checked (check_members) before anything is written; then unpacked into d/IMPORT_STAGING, everything
        owned by the server's user. (staging, bytes, whether d was made for it); `place_import` renames it into place,
        `drop_import` removes what is left of it."""
        data = d / "data"
        if data.is_symlink() or (data.exists() and (not data.is_dir() or any(data.iterdir()))):
            raise CellError("data_exists", f"{d.name}'s data folder is not empty; an import goes only into a new one")
        if not hasattr(tarfile, "data_filter"):
            raise CellError("unsupported_host", "--import-data needs Python 3.12 (tarfile's data filter)")
        with self.system.open_archive(path) as f, contextlib.ExitStack() as stack:
            try:
                tar = stack.enter_context(tarfile.open(fileobj=f, mode="r:*"))
                members = tar.getmembers()
            except (tarfile.TarError, OSError, EOFError, zlib.error):
                raise CellError("bad_archive", "--import-data: not a readable .tar or .tar.gz") from None
            size = check_members(members)
            created = not d.exists()
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o700)
            staging = d / IMPORT_STAGING
            shutil.rmtree(staging, ignore_errors=True)     # left by a run that died half-way
            staging.mkdir(mode=0o700)
            staged = (staging, size, created)
            try:
                tar.extractall(staging, members=members, filter="data")
                os.chmod(staging, 0o700)
                for top, dirs, files in os.walk(staging):
                    for name in [*dirs, *files]:
                        self.system.chown(Path(top) / name, SERVER_UID, SERVER_UID)
                self.system.chown(staging, SERVER_UID, SERVER_UID)
            except (tarfile.TarError, OSError, EOFError, zlib.error) as e:
                self.drop_import(staged)
                if not isinstance(e, OSError):
                    raise CellError("bad_archive", "--import-data: refused while unpacking it") from None
                raise CellError("import_failed", f"--import-data: unpacking it failed ({e.strerror or e})") from None
            return staged

    def place_import(self, staged: tuple[Path, int, bool], d: Path) -> None:
        """The unpacked archive becomes data/ in one rename (data/ is missing or empty: an empty one is replaced)."""
        data = d / "data"
        with contextlib.suppress(FileNotFoundError):
            data.rmdir()            # empty (stage_import checked, under the same lock); refuses if it is not
        os.rename(staged[0], data)

    def drop_import(self, staged: tuple[Path, int, bool]) -> None:
        staging, _, created = staged
        shutil.rmtree(staging, ignore_errors=True)
        if created:                 # the folder was made only for the import: nothing else of the company is left
            with contextlib.suppress(OSError):
                staging.parent.rmdir()

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
        changed = self.write_compose(c)
        changed = _write_atomic(d / ".env", env_text, 0o600) or changed
        files = d / contract.COMPANY_FILE_DIR
        if plan:
            f = files / contract.COMPANY_FILE_NAME
            if _write_atomic(f, plan + "\n", 0o600):
                changed = True
            self.system.chown(f, SERVER_UID, SERVER_UID)
        return changed

    def write_compose(self, c: dict) -> bool:
        """The files `add` and `update` derive from the record and this core alone: the server's folders, the stock
        compose.yaml, the override and the updater token. True when any changed (the server must be recreated)."""
        d = self.dir(c["slug"])
        try:
            compose = self.compose_source.read_text()
        except OSError:
            raise CellError("internal", f"no compose.yaml at {self.compose_source}") from None
        data = d / "data"
        for p in (data, d / contract.COMPANY_FILE_DIR, d / "control"):
            p.mkdir(exist_ok=True)
            os.chmod(p, 0o700)
            self.system.chown(p, SERVER_UID, SERVER_UID)
        changed = _write_atomic(d / "compose.yaml", compose)
        changed = _write_atomic(d / "compose.cell.yaml", company_override(c, data)) or changed
        return self.updater_token(d) or changed

    def updater_token(self, d: Path) -> bool:
        """The company's own updater token, made once: 0600 and the server's (it reads it, docker/entrypoint.sh, and
        keeps one that is there). Its updater reads it back from here, so nothing else holds a copy. True when made."""
        path = d / "control" / TOKEN_NAME
        with contextlib.suppress(OSError):
            if not path.is_symlink() and path.is_file() and path.stat().st_size >= 32:
                return False
            path.unlink()                   # empty, short, or not a plain file: replaced
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32) + "\n")
        self.system.chown(path, SERVER_UID, SERVER_UID)
        return True

    def company(self, slug: str) -> dict:
        c = self.load(check_slug(slug))
        if not c:
            raise CellError("not_found", f"no company {slug} on this host")
        return c

    def list(self, args) -> dict:
        ps = self.containers() if self.companies() else {}
        return {"ok": True, "companies": [
            {"company": c["slug"], "url": f"https://{c['domain']}", "aliases": c.get("aliases", []), "port": c["port"],
             "version": c["version"], "memory": c["memory"], "state": self.state_of(c, ps)} for c in self.companies()]}

    def status(self, args) -> dict:
        c = self.company(args.company)
        ps = self.containers(c["slug"])
        state = self.state_of(c, ps)
        # A sleeping company is not probed: through the router, that would wake it.
        healthy = state not in ("removed", "sleeping") and \
            self.system.http_status(c["domain"], contract.HEALTH_PATH) == 200
        return {"ok": True, "company": c["slug"], "url": f"https://{c['domain']}", "aliases": c.get("aliases", []),
                "port": c["port"], "version": c["version"], "memory": c["memory"], "state": state, "healthy": healthy,
                "container": ps.get(c["slug"], {"state": "", "health": ""})}

    def live(self, slug: str) -> dict:
        """The company's record, read under its lock (so another command cannot have changed it since); not_found when
        there is none or it was removed."""
        c = self.company(slug)
        if c.get("state") == "removed":
            raise CellError("not_found", f"{c['slug']} was removed; add it again first")
        return c

    def routed(self) -> bool:
        """The router's config again, after a record's state changed (under the company's lock; this takes the cell's)."""
        with self.lock("cell"):
            return self.write_router()

    def stop(self, args) -> dict:
        """The server stopped, and its hosts answered 503 at once (no hold: nothing will come up)."""
        slug = check_slug(args.company)
        with self.lock("company-" + slug):
            c = self.live(slug)
            was = self.containers(slug).get(slug, {}).get("state")
            self.ensure_stopped(slug)
            route = c.get("state") != "stopped"
            c["state"] = "stopped"
            _set_wake_at(c, None)           # no longer asleep, so no timed wake
            self.save(c)
            if route:
                self.routed()
        return {"ok": True, "company": slug, "state": "stopped", "changed": was == "running"}

    def start(self, args) -> dict:
        started = self.system.monotonic()
        slug = check_slug(args.company)
        with self.lock("company-" + slug):
            c = self.live(slug)
            before = self.containers(slug).get(slug, {})
            route = c.get("state") != "running"     # stopped (503) or sleeping (the wake service): to the server now
            c["state"] = "running"
            _set_wake_at(c, None)
            self.save(c)
            if route:
                self.routed()               # the router holds requests until the server answers
            ready = self.launch(c, started, args.timeout)
        return self.answer(c, ready, before.get("state") != "running")

    def sleep(self, args) -> dict:
        """Stop the server and send its hosts to the wake service, whose first request starts it again (`wake`). The
        route moves first: a request that comes while the server stops waits for this lock in the wake service, then
        starts it again, which is what a request should do.

        `wake_at` (`--wake-at`, or the updater's POST /sleep) is the time the wake service starts the company by itself
        (`cell_wake.py`, `timed_wakes`). Every sleep sets it: one without a time clears the one an earlier sleep set. A
        company already asleep gets a new time without a stop."""
        slug = check_slug(args.company)
        wake_at = wake_at_text(getattr(args, "wake_at", None))
        with self.lock("company-" + slug):
            c = self.live(slug)
            was, before = c.get("state"), c.get("wake_at")
            if was == "sleeping" and self.containers(slug).get(slug, {}).get("state", "") in STOPPED:
                if before != wake_at:
                    _set_wake_at(c, wake_at)
                    self.save(c)
                return {"ok": True, "company": slug, "state": "sleeping", "changed": False}
            c["state"] = "sleeping"
            _set_wake_at(c, wake_at)
            self.save(c)
            try:
                self.routed()
                self.ensure_stopped(slug)
            except CellError:
                c["state"] = was
                _set_wake_at(c, before)
                self.save(c)
                with contextlib.suppress(CellError):
                    self.routed()
                raise
        return {"ok": True, "company": slug, "state": "sleeping", "changed": True}

    def wake(self, args) -> dict:
        started = self.system.monotonic()
        c, ready, changed = self.wake_company(check_slug(args.company), args.timeout, started)
        return self.answer(c, ready, changed)

    def wake_company(self, slug: str, timeout: int, started: float | None = None,
                     due=None) -> tuple[dict, int, bool]:
        """A sleeping company's server started under its lock, as `start` starts it (a pending update is finished or
        rolled back there too), healthy on its own port, then marked running and routed straight to it. A running one
        is only checked. A start that does not come up is stopped again, so the company sleeps and the next request
        tries again; one rolled back to the release before an update is awake on that release (update_failed is raised
        after the route, as `start` raises it). (record, ready_ms, changed).

        `due` is a timed wake's own test, `due(record) -> bool`, made under the lock on the record as it is now: a
        sweep read the record without the lock, and staff may have moved or cleared the time since (`sleep
        --wake-at`). False: CellError not_due and nothing is started. A request's wake passes none: it always wakes."""
        started = self.system.monotonic() if started is None else started
        with self.lock("company-" + slug):
            c = self.live(slug)
            state = c.get("state")
            if state == "running":
                return c, self.wait_healthy(c, started, timeout), False
            if state != "sleeping":
                raise CellError("not_sleeping", f"{slug} is {state}; `start` starts a stopped company")
            if due is not None and not due(c):
                raise CellError("not_due", f"{slug}'s timed wake was moved or cleared; it sleeps on")
            self.direct = True
            failed = None
            try:
                ready = self.launch(c, started, timeout)
            except CellError as e:
                if e.code != "update_failed":
                    self.system.run(self.compose(slug, "stop", "server"))
                    raise
                failed, ready = e, int((self.system.monotonic() - started) * 1000)
            finally:
                self.direct = False
            c["state"] = "running"
            _set_wake_at(c, None)
            self.save(c)
            self.routed()
            if failed:
                raise failed
        return c, ready, True

    def set_sleep(self, args) -> dict:
        """The company's sleep policy on its record (its server reads it through GET /sleep): N idle minutes, 0 for
        never, or `default` to remove it. Under the company's lock, as the other per-company commands; a no-op changes
        nothing."""
        slug = check_slug(args.company)
        minutes = None if args.minutes == "default" else check_sleep_minutes(args.minutes, "--minutes")
        with self.lock("company-" + slug):
            c = self.live(slug)
            changed = c.get("sleep_minutes") != minutes
            if minutes is None:
                c.pop("sleep_minutes", None)
            else:
                c["sleep_minutes"] = minutes
            if changed:
                self.save(c)
        return {"ok": True, "company": slug, "sleep_minutes": minutes, "changed": changed}

    def update(self, args) -> dict:
        """Another release, with the database safe first. The image is pulled before anything changes (a release that
        cannot be pulled leaves the company as it was). Then the server is stopped (Docker must say so), its database
        copied to snapshots/, and only then do .env and company.json name the new release, with `pending` = the release
        before and the snapshot. A running company starts the new release at once; a stopped one stays stopped and
        `start` (or `add`) starts it, and a sleeping one sleeps on and its next request (`wake`) starts it. Either way the first start is guarded by `launch`: no /healthz, and the old
        release and database come back. `pending` is saved, so a command that dies half-way is still rolled back by the
        next start. A stop or snapshot that fails changes nothing and the old release is started again.

        With the new release come this core's compose files, token and updater keys in .env (`write_compose`,
        `cell_env`). On the release the company already runs they are rewritten alone, and a running server is
        recreated only when one changed (`changed`): a company made by an older core picks up its updater that way."""
        started = self.system.monotonic()
        if not VERSION_RE.fullmatch(args.version or ""):
            raise CellError("bad_args", "--version is a release such as v1.2.3")
        slug = check_slug(args.company)
        self.taken = ""
        with self.lock("company-" + slug):
            c = self.live(slug)
            d = self.dir(slug)
            env_text = _read(d / ".env")
            self.secrets += [v for k, v in envfile.parse(env_text).items() if k in contract.SECRET_KEYS and v]
            extra = self.extra_env(slug)
            running = c.get("state") not in ("stopped", "sleeping")     # one that is not stays as it is
            pending = c.get("pending")
            self.taken = (pending or {}).get("snapshot", "")
            if c["version"] == args.version:
                changed = self.write_compose(c)
                changed = _write_atomic(d / ".env", _with_tag(env_text, c["version"], extra, c), 0o600) or changed
                if running and (pending or changed):
                    ready = self.launch(c, started, args.timeout)
                else:
                    ready = self.wait_healthy(c, started, args.timeout) if running else 0
                return self.answer(c, ready, changed)
            self.pull(args.version)
            try:
                self.ensure_stopped(slug)
                # An update still pending keeps its release and snapshot: the database has not moved on since.
                snap = pending or {"from": c["version"], "snapshot": self.snapshot(slug, c["version"])}
            except CellError as e:
                raise self.restarted(c, e, running) from None
            self.taken = snap.get("snapshot", "")
            if pending and pending["from"] == args.version:
                # Back to the release its database still belongs to: the pending update's own rollback.
                self.rollback(c, args.timeout, restart=running)
                return self.answer(c, self.wait_healthy(c, started, args.timeout) if running else 0, True)
            try:
                self.write_compose(c)
                _write_atomic(d / ".env", _with_tag(env_text, args.version, extra, c), 0o600)
            except CellError as e:
                raise self.restarted(c, e, running) from None
            except OSError as e:
                raise self.restarted(c, CellError("internal", f"writing the company's files: {e}"), running) from None
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
        there is no database yet. The last SNAPSHOTS_KEPT are kept. data/ belongs to the server, and its updater lets
        the company itself start this, so only plain files are copied: a symlink there would have root copy a host file
        into snapshots/, and a rollback would hand it to the server."""
        data, snaps = self.dir(slug) / "data", self.dir(slug) / "snapshots"
        found = [n for n in DB_FILES if os.path.lexists(data / n)]
        if "hub.sqlite" not in found:
            return ""
        if any(not stat.S_ISREG(os.lstat(data / n).st_mode) for n in found):
            raise CellError("snapshot_failed", "the database in data/ is not plain files (a symlink or other special "
                                               "file); nothing was copied")
        target = snaps / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{version}"
        try:
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            for name in found:
                shutil.copy2(data / name, target / name, follow_symlinks=False)
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
                self.write_tunnel()
                if not args.keep_data:
                    shutil.rmtree(d)
        return {"ok": True, "company": slug, "removed": True, "kept_data": bool(args.keep_data)}


def wake_at_text(value) -> str | None:
    """A timed wake's time as the record keeps it (WAKE_AT_FORMAT), or None when `value` is None (no timed wake).
    The time must say what zone it is in (a Z or an offset; a naive time is refused) and be at most WAKE_AT_DAYS from
    now. A time already past is kept: the wake service starts the company at its next sweep. CellError bad_wake_at
    for anything else, so the updater's POST /sleep and `sleep --wake-at` refuse the same values."""
    if value is None:
        return None
    bad = CellError("bad_wake_at", "wake_at is an ISO-8601 UTC time such as 2026-10-30T18:00:00Z, at most "
                    f"{WAKE_AT_DAYS} days out")
    if not isinstance(value, str) or not WAKE_AT_SHAPE.fullmatch(value):   # ISO-8601 shape, not a loose parse
        raise bad
    text = value
    if text.endswith("Z"):              # fromisoformat takes an offset, not the Z (before Python 3.11)
        text = text[:-1] + "+00:00"
    try:
        when = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise bad from None
    if when.tzinfo is None:
        raise bad
    when = when.astimezone(datetime.timezone.utc)
    if when.timestamp() - time.time() > WAKE_AT_DAYS * 86400:
        raise bad
    return when.strftime(WAKE_AT_FORMAT)


def wake_at_time(c: dict) -> float | None:
    """The record's timed wake as a Unix time, or None when it has none. ValueError for one not in WAKE_AT_FORMAT."""
    if not c.get("wake_at"):
        return None
    if not isinstance(c["wake_at"], str):
        raise ValueError("wake_at is not a string")
    return datetime.datetime.strptime(c["wake_at"], WAKE_AT_FORMAT).replace(tzinfo=datetime.timezone.utc).timestamp()


def _set_wake_at(c: dict, wake_at: str | None) -> None:
    """The record's timed wake: set, or removed (None). Only a sleeping company has one."""
    if wake_at:
        c["wake_at"] = wake_at
    else:
        c.pop("wake_at", None)


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _with_tag(env_text: str, version: str, extra: dict[str, str] | None = None, c: dict | None = None) -> str:
    """The same .env with another release, written exactly as `add` would write it (carried settings fill only keys
    the .env leaves unset, as in `add`; with `c`, the cell's updater keys as `add` writes them)."""
    values = {**(extra or {}), **envfile.parse(env_text), "TICO_TAG": version}
    return envfile.render(cell_env(values, c) if c else values)


def main(argv: list[str], *, system: System | None = None, root: str | Path | None = None,
         host_root: str | Path = "/", compose_source: Path | None = None, out=None) -> int:
    out = out or sys.stdout
    cell = Cell(Path(root or os.environ.get("TICO_CELL_ROOT") or ROOT), system or System(), Path(host_root), compose_source)
    try:
        args = parser().parse_args(argv)
        result = getattr(cell, args.action.replace("-", "_"))(args)     # set-sleep is set_sleep
    except CellError as e:
        result = {"ok": False, "error": e.code, "detail": cell.scrub(e.detail), **e.extra}
    except Exception as e:  # noqa: BLE001  an answer HQ can read, even for a bug; the secrets scrubbed out of it
        result = {"ok": False, "error": "internal", "detail": cell.scrub(f"{type(e).__name__}: {e}")[-600:]}
    out.write(json.dumps(result) + "\n")
    out.flush()
    return 0 if result.get("ok") else 1
