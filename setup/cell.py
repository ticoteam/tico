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
    <root>/meet/                            Tico Meet (`init --meet-domain`, 0700): the cell's LiveKit key pair
                                            (keys/keys.yaml, 0600), the certificate Caddy's config and its certificates
    <root>/companies/<slug>/.env            0600, the server's settings
    <root>/companies/<slug>/compose.yaml    the stock compose.yaml, unchanged
    <root>/companies/<slug>/compose.cell.yaml   memory limit, data folder, networks: what makes it one of many
                                            (and the same for its Slack gateway, when `set-slack` turned it on)
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

Each company also has an updater, so its Update now, an update service key (the Release Owner's rollout) and Tico
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

A company's Slack gateway (compose.yaml's `slack` service, docs/slack.md) is off until staff turn it on with `set-slack
--company <slug> on`, which the record keeps (`slack`), so every later `add` and `update` writes it again: the .env's
COMPOSE_PROFILES is then `slack` (else empty, as for every other company), and compose.cell.yaml gives the gateway
what it gives the server: a memory limit, the company's own data folder in place of the stock volume, its label, and
the company's own default network (the stock service is on no other). It never mounts control/, so it cannot read the
updater token. It writes the database itself, so every stop that protects the database (`update`, `sleep`, `stop`, a
rollback) stops it with the server, and Docker must say both are stopped.

A company's stored secrets are on the cell's one KMS key, with its slug as the encryption context (docs/cell.md,
Stored secrets and KMS): `add` and `update` write the key and the company's own KMS access key into its .env
(`--kms-key`, `--kms-credentials-file`), and a new company is refused without them. `kms-policy` prints the IAM
policy for that access key. `rewrap` moves an existing company off its key files: it checks its backup bucket blocks
public access, runs the backend step (vault-rotate) on the stopped server, and deletes the key file copies in the
bucket with the host's role.

All Docker and system calls go through one `System`, so the tests run every command with a fake.
"""

from __future__ import annotations

import _ssl
import argparse
import base64
import contextlib
import datetime
import errno
import fcntl
import hashlib
import io
import hmac
import http.client
import ipaddress
import json
import os
import re
import urllib.parse
import secrets
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from . import contract, envfile, hosted, remote, settings as st, signin
from .cloudinit import VERSION_RE
from .ui import MissingInput

ROOT = "/srv/tico-cell"
SERVER_IMAGE = "ghcr.io/ticoteam/tico"
# The same pinned images compose.yaml uses for the single-company front doors (a test keeps them equal).
ROUTER_IMAGE = "caddy:2.11.9-alpine@sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0"
TUNNEL_IMAGE = (
    "cloudflare/cloudflared:2026.11.1@sha256:e613aadbc2d9511785d2e67a86b0c8ac18dd63a3f9af0d06faf04cbfdcb35f2d"
)
# Tico Meet's media server, the same pinned image as compose.yaml's `livekit` (a test keeps them equal).
MEET_IMAGE = "livekit/livekit-server:v1.13.11@sha256:25cd902de7cacbd5f1f779f6a6ac1eae20ba8f56b0935934ce6b0ab80d1e3d20"
ROUTER_ADDRESS, ROUTER_PORT = "127.0.0.1", 8080
ROUTER_CONTAINER, EDGE_PROJECT = "tico-cell-router", "tico-cell"
ROUTER_HEALTH_HOST, ROUTER_HEALTH_PATH = "cell-health.invalid", "/cell-healthz"
PROJECT_PREFIX = "tico-"
LABEL = "tico.cell.company"
SERVER_SERVICE, SLACK_SERVICE = "server", "slack"  # compose.yaml's service names: the server, its Slack gateway
SLACK_MEMORY = "1g"  # the Slack gateway's memory limit: one Python process with an outbound websocket
SERVER_UID = 10001  # the server image's user (Dockerfile)
TUNNEL_UID = 65532  # cloudflared's image runs as nonroot (uid and gid 65532)
# py-spy on every cell host, for `profile` (setup/cell_stats.py `t_profile`): the static binary from its PyPI wheel, for
# each machine type the wheel's url and sha256 and the binary's sha256 (checked without running it). 0.4.3 reads
# Python 3.15, the server image's Python.
PY_SPY = "usr/local/bin/py-spy"
PY_SPY_VERSION = "0.4.3"
PY_SPY_WHEELS = {
    "aarch64": (
        "https://files.pythonhosted.org/packages/60/c3/c9384da6992d64e0dffab551cda3d1e990de1a2685fcfcc16fc8e5a98b18/"
        "py_spy-0.4.3-py2.py3-none-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
        "bbcec46930d464b3bfc142d6d02ade4dd8ad5627823263b3eff0280e80c7c132",
        "ad6e22882e8dcaced4f0a76fef10d372645cc7b94c48a289ebe1bedb754774ed",
    ),
    "x86_64": (
        "https://files.pythonhosted.org/packages/78/1f/651389a861f6148754443834a50dd576993257af5f645d4316ab94505267/"
        "py_spy-0.4.3-py2.py3-none-manylinux1_x86_64.manylinux_2_5_x86_64.whl",
        "8a4277225f470d394a85cca0cdd944bef87f0097b6f20150a6dd3ac2e92806b8",
        "a41205d4925beafd108e1ace85e66c0a1a7adbfcbebcd4441f07848b608886c0",
    ),
}
PY_SPY_MEMBER = f"py_spy-{PY_SPY_VERSION}.data/scripts/py-spy"

# Never given to a company: our own names under the domain.
RESERVED = frozenset(
    {
        "hub",
        "admin",
        "accounts",
        "updates",
        "preview",
        "runner",
        "www",
        "api",
        "app",
        "mail",
        "status",
        "docs",
        "help",
        "support",
        "hq",
        "gateway",
        "hooks",
        "cloud",
        "tico",
        "team",
    }
)
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
MEMORY_RE = re.compile(r"^([1-9][0-9]{0,5})([mg])$")
MEMORY_DEFAULT, MEMORY_MIN_MB, MEMORY_MAX_MB = "2g", 512, 16 * 1024
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
SECRET_MAX = 64 * 1024
HOSTED_DEFAULT, SIGNIN_LABEL = "invited", "Tico account"
MAX_ALIASES = 5
# A company's page host (docs/company-page.md): its public files come from a folder of the cell's own, mounted into the
# company's server and read by the router. Only these three paths are served from the folder; the rest is the server's.
PAGES = "pages"  # under the cell root: one folder per company with a page host
PAGE_MOUNT = "/company-page-public"  # where the company's server writes its release folders (backend/company_page)
PAGE_MOUNT_ROUTER = "/pages"  # where the router reads every company's folder, read-only
PAGE_CACHE = "public, max-age=60"
PAGE_PATHS = ("/", "/data.json", "/feed.xml", "/assets/*")  # the public files a page host serves from its folder
# Requests with a reader's cookie or any query string go to the server: they are signed-in or paged reads.
READER_COOKIE_GLOB = "*tico_reader=*"
# The app's own cookies, removed from what a page host sends to the server (the reader's cookie stays): the bare or
# __Host- names of the session and sign-in cookies, and this install's local session.
APP_COOKIES_RE = "(?:^|;)[ ]*(?:__Host-)?tico_(?:session|login|local_session)[^=;]*=[^;]*"

# What `cell_env` writes for Tico Meet while the cell has it on, and removes while it is off.
MEET_ENV_KEYS = (
    "TICO_MEET_URL",
    "TICO_MEET_INTERNAL_URL",
    "TICO_MEET_DOMAIN",
    "TICO_MEET_API_KEY",
    "TICO_MEET_API_SECRET",
)

# --env-file: what a moved install brings. Never carried: keys the cell sets itself (with a value, in this render: see
# `add`), the ones below (the cell's own or read back from .env by the next `add`, so a carried one would feed back),
# and what belongs to a single-company install only.
ENV_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
# The company's credential vault on the cell's one KMS key (docs/cell.md, Credentials and KMS): the key, the company's
# slug as its encryption context, and the company's own AWS access key for KMS (containers cannot use the host's role).
# The cell writes them from `add`/`update --kms-key --kms-credentials-file`; an --env-file can never set them.
KMS_KEY, KMS_CONTEXT, KMS_KEY_ID, KMS_SECRET, KMS_REGION = KMS_ENV = (
    "TICO_CREDENTIAL_KMS_KEY",
    "TICO_CREDENTIAL_KMS_CONTEXT",
    "TICO_CREDENTIAL_KMS_ACCESS_KEY_ID",
    "TICO_CREDENTIAL_KMS_SECRET_ACCESS_KEY",
    "TICO_CREDENTIAL_KMS_REGION",
)
# A key ARN, not an alias or a bare id: the IAM policy (`kms-policy`) names it as its resource, and its region is the
# client's.
KMS_ARN_RE = re.compile(r"arn:aws(?:-[a-z]+)*:kms:([a-z]{2}(?:-[a-z]+)+-[0-9]):([0-9]{12}):key/[A-Za-z0-9-]{1,128}")
KMS_APPLICATION = "tico-credentials"  # backend/credentials.py's encryption context, beside the company
# What `cell` reads back from a company's .env and must keep out of every answer.
SECRET_ENV_KEYS = contract.SECRET_KEYS | {KMS_SECRET}
# `rewrap`'s backend step (backend/manage.py vault-rotate), in a one-off container of the company's server service: its
# image, .env and /data, while the server is stopped. Exit 0 done or already done, 3 stopped with nothing deleted.
REWRAP_STEP = ("-m", "backend.manage", "vault-rotate", "--json")
REWRAP_DONE = ("done", "already_done")
KEY_DIGESTS = ".key-digests.json"  # rewrap: the key files' SHA-256s, found in bundle backups after the files are gone
CELL_ENV_KEYS = frozenset(
    {
        "TICO_TEAM_NAME",
        "TICO_COMPANY_NAME",
        "TICO_OWNER_EMAIL",
        "TICO_DOMAIN",
        "TICO_GATEWAY_INVITE",
        "TICO_OWNER_LINK_HASH",
        "TICO_SETUP_HANDOFF_HASH",
        "TICO_BACKUP_URL",
        "TICO_BACKUP_REGION",
        "TICO_BACKUP_ENDPOINT",
        "LITESTREAM_ACCESS_KEY_ID",
        "LITESTREAM_SECRET_ACCESS_KEY",
        "TICO_COMPANY_FILE",
        "TICO_COMPANY_FILE_SHA256",
        "TICO_COMPANY_FILE_B64",
        "TICO_COMPANY_FILE_S3",
        "TICO_COMPANY_FILE_S3_REGION",
        "TICO_COMPANY_FILE_DIR",
        "TICO_UPDATES",
        "TICO_CELL",
        "TICO_HOSTED",
        "TICO_AUTH_PROXY",
        "TICO_OIDC_ISSUER",
        "TICO_OIDC_CLIENT_ID",
        "TICO_OIDC_CLIENT_SECRET",
        "TICO_OIDC_LABEL",
        "TICO_ACCESS_ISSUER",
        "TICO_ACCESS_AUDIENCE",
        "TICO_TRUSTED_PROXIES",
        "TICO_CLIENT_IP_HEADER",
        "TICO_EDGE_SUBNET",
        "TICO_EDGE_DYNAMIC_RANGE",
        "TICO_CLOUDFLARED_ADDRESS",
        "TICO_INSTALL_PRESET",
        "TICO_MODELS_PRESET",
        "TICO_SUPPORT",
        "CLOUDFLARE_TUNNEL_TOKEN",
        "COMPOSE_PROFILES",
        "COMPOSE_PROJECT_NAME",
        "TICO_UPDATER_URL",
        "TICO_TAG",
        "TICO_PORT",
        "TICO_EXPECTED_AWS_ACCOUNT",
        "TICO_EXPECTED_AWS_ROLE",
        "TICO_READER_ORIGIN",
        "TICO_COMPANY_PAGE_DIR",
        *MEET_ENV_KEYS,
        *KMS_ENV,
    }
)
CELL_ENV_PREFIXES = ("TICO_CADDY", "TICO_TUNNEL")
# Tico Meet on the cell (`init --meet-domain`, docs/cell.md "Tico Meet"): one LiveKit for every company, one key pair.
# LiveKit and the Caddy that gets its TURN certificate run in the edge project on the host's network. Browsers reach
# LiveKit's signal at wss://<company host>/rtc through the tunnel and the router; servers reach its API on their own
# default network's gateway (as they reach their updater). The public ports below are what the cell's security group
# opens (infra/cell-meet/cell-meet.yaml); MEET_PORT (the API and signal) and MEET_CERTS_HTTPS stay closed.
MEET_DIR = "meet"  # <root>/meet/ (0700): keys/, caddy/Caddyfile (the certificate Caddy's config), certs/ (its storage)
# The cell's LiveKit key pair, made once and kept: `<key>: "<secret>"`, LiveKit's own key_file (a YAML map), 0600. LiveKit
# reads it from a read-only mount, so the secret is in no container's environment or config (`docker inspect`).
MEET_KEYS = "meet/keys/keys.yaml"
MEET_KEYS_MOUNT = "/keys/keys.yaml"
MEET_CONTAINER, CERTS_CONTAINER = "tico-cell-livekit", "tico-cell-certs"
MEET_PORT = 7880
MEET_RTC_TCP, MEET_UDP_FIRST, MEET_UDP_LAST = 7881, 50000, 50100
MEET_TURN_UDP, MEET_TURN_TLS = 3478, 443  # nothing on a cell binds 443: the tunnel is outbound only
MEET_CERTS_HTTP, MEET_CERTS_HTTPS = 80, 8443  # the certificate Caddy: HTTP-01 on 80; its own https port is unused
MEET_PUBLIC_PORTS = (
    ("tcp", MEET_CERTS_HTTP, MEET_CERTS_HTTP),
    ("tcp", MEET_TURN_TLS, MEET_TURN_TLS),
    ("udp", MEET_TURN_UDP, MEET_TURN_UDP),
    ("tcp", MEET_RTC_TCP, MEET_RTC_TCP),
    ("udp", MEET_UDP_FIRST, MEET_UDP_LAST),
)
# Caddy's storage for a Let's Encrypt certificate (`cert_issuer acme`), under certs/ on the host and /certs in LiveKit.
MEET_CERT_PATH = "caddy/certificates/acme-v02.api.letsencrypt.org-directory/{d}/{d}"
MEET_STARTED_CERT = (
    "meet/livekit-cert.json"  # the expiry of the certificate LiveKit last started with (Cell.meet_renew)
)
MEET_UP_WAIT = 60  # seconds `init` waits for LiveKit's API and the TURN certificate before Tico Meet counts as on
MEET_EXPIRY_WARN_DAYS = 14  # the cell updater logs a warning while LiveKit's certificate runs out sooner than this
IMPORT_STAGING = ".data-import"  # beside data/: an import is unpacked here, then renamed into place

# Index i: port 20000+i, default network the i-th /24 of 10.208.0.0/12, edge network the i-th /28 of 10.224.0.0/16.
PORT_BASE, MAX_COMPANIES = 20000, 4000
DEFAULT_NETS = ipaddress.ip_network("10.208.0.0/12")
EDGE_NETS = ipaddress.ip_network("10.224.0.0/16")

# The rule that keeps containers from the instance's role. IMDSv2 with hop limit 1 is the second lock (docs/cell.md).
IMDS_RULE = ["DOCKER-USER", "-d", "169.254.169.254/32", "-j", "REJECT", "--reject-with", "icmp-port-unreachable"]
IMDS_LINE = "-A " + " ".join(IMDS_RULE)  # how `iptables -S DOCKER-USER` prints it
# With Tico Meet on, LiveKit's API and signal port (MEET_PORT, on the host's network) answers only loopback (the router)
# and the companies' default networks (their servers, on their gateway address): INPUT's first three rules, tagged.
MEET_TAG = "tico-cell-meet"
MEET_INPUT = [
    f"-A INPUT{src} -p tcp -m tcp --dport {MEET_PORT} -m comment --comment {MEET_TAG} -j {verdict}"
    for src, verdict in ((" -s 127.0.0.0/8", "ACCEPT"), (f" -s {DEFAULT_NETS}", "ACCEPT"), ("", "DROP"))
]
FIREWALL_SCRIPT = "usr/local/sbin/tico-cell-firewall"
FIREWALL_UNIT = "etc/systemd/system/tico-cell-firewall.service"
DOCKER_DROPIN = "etc/systemd/system/docker.service.d/tico-cell.conf"
DAEMON_JSON = "etc/docker/daemon.json"
DAEMON_SETTINGS = {"log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "3"}, "live-restore": True}

HEALTH_TIMEOUT = 300
MEET_RENEW_EVERY = 600.0  # seconds between the cell updater's checks for a renewed TURN certificate (Cell.meet_renew)
SNAPSHOTS_KEPT = 2
SNAPSHOT_SPARE = 1 << 30  # free bytes a snapshot must leave beyond its own copy
# Release folders, as HQ and the cell updater install them (setup/cell_updater.py CORES), under the host root.
CORES = "opt/tico-cell"
CORE_DIR_RE = re.compile(r"core-(v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]+)?)")
UNIT_DIR = "etc/systemd/system"
FRESH_S = 3600  # a core folder this new may be a running update's; a leftover .prune-* this old is not
PRUNE_S = 120  # seconds of image removals per prune; the next one goes on
DB_FILES = ("hub.sqlite", "hub.sqlite-wal", "hub.sqlite-shm")
STOPPED = ("", "exited", "created", "dead")  # container states in which nothing has the database open
# Each company's updater (setup/cell_updater.py): this port on its default network's gateway address, nowhere else.
UPDATER_PORT = 8090
UPDATER_UNIT = "etc/systemd/system/tico-cell-updater.service"
UPDATER_SERVICE = "tico-cell-updater.service"
TOKEN_NAME = "updater-token"  # in <company>/control, the server's /control (docker/entrypoint.sh reads it)
# The wake service (setup/cell_wake.py): the router sends a sleeping company's hosts here, naming the company in a
# header the route itself sets (a visitor's own copy of it is replaced).
WAKE_ADDRESS, WAKE_PORT = "127.0.0.1", 8082
WAKE_HEADER = "X-Tico-Cell-Wake"
WAKE_UNIT = "etc/systemd/system/tico-cell-wake.service"
WAKE_SERVICE = "tico-cell-wake.service"
# The hold: a dial to a server that is down (restarting, updating) is tried again, not answered with a 502. A request
# with a body is retried with its body from Caddy 2.11 on. Caddy 2.10 closed the body after the
# first failed dial, so such a request got the 503. No request_buffers: in 2.10.2 it mixed up concurrent bodies.
HOLD_FOR, HOLD_EVERY = "30s", "250ms"
# A config reload (every add, remove, sleep and wake) leaves open WebSockets this long before closing them.
STREAM_CLOSE_DELAY = "5m"
HOLD_BODY = "Tico is starting. Try again in a few seconds."
# Routes (`cell route`): a service on this host that is not a company (Tico's public demo), on its own hosts and
# loopback ports, kept in <root>/routes/<name>.json. Its ports stay clear of the companies' and the cell's own.
ROUTES = "routes"
ROUTE_PORTS = range(1024, PORT_BASE)
# Caddy admin, router, tunnel metrics, wake, updater, and Tico Meet's ports (on or not: a route never takes one).
CELL_PORTS = frozenset(
    {
        2019,
        ROUTER_PORT,
        ROUTER_PORT + 1,
        WAKE_PORT,
        UPDATER_PORT,
        MEET_PORT,
        MEET_RTC_TCP,
        MEET_TURN_UDP,
        MEET_CERTS_HTTPS,
    }
)
# The Meet ports a route's loopback port could name (the rest are below 1024 or above ROUTE_PORTS).
MEET_ROUTE_PORTS = frozenset({MEET_PORT, MEET_RTC_TCP, MEET_TURN_UDP, MEET_CERTS_HTTPS})
MAX_ROUTE_HOSTS, MAX_ROUTE_PORTS = 5, 2
NOINDEX = "noindex, nofollow"
ROBOTS_TXT = "User-agent: *\nDisallow: /\n"  # a literal newline inside a quoted Caddyfile token is kept
HEALTH_POLL = 0.15  # seconds between /healthz probes while a server starts
# A timed wake (`sleep --wake-at`): the record's wake_at, always UTC in this format, at most WAKE_AT_DAYS out when set.
WAKE_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
WAKE_AT_DAYS = 31
WAKE_AT_SHAPE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]{1,6})?)?"
    r"(Z|[+-][0-9]{2}:[0-9]{2})"
)
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

    def meet_rooms(self, token: str) -> int | None:
        """How many rooms the cell's LiveKit has open (ListRooms on its API, on loopback); None when it does not answer."""
        try:
            c = http.client.HTTPConnection(ROUTER_ADDRESS, MEET_PORT, timeout=5)
            c.request(
                "POST",
                "/twirp/livekit.RoomService/ListRooms",
                body=b"{}",
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            )
            r = c.getresponse()
            body = r.read(1 << 20)
            c.close()
            if r.status != 200:
                return None
            return len(json.loads(body or b"{}").get("rooms") or [])
        except (OSError, http.client.HTTPException, ValueError, AttributeError):
            return None

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
            raise CellError(
                "secret_file", "--import-data: a regular file readable by its owner only (chmod 600), or owned by root"
            )
        return os.fdopen(fd, "rb")

    def linux(self) -> bool:
        return sys.platform.startswith("linux")

    def free_bytes(self, path: str) -> int:
        v = os.statvfs(path)
        return v.f_bavail * v.f_frsize

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
            with e:
                return False if e.code == 404 else None
        except OSError:
            return None

    def fetch(self, url: str, limit: int = 64 << 20) -> bytes:
        """A download's bytes (at most `limit`); OSError when it fails."""
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read(limit)

    def machine(self) -> str:
        return os.uname().machine

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def monotonic(self) -> float:
        return time.monotonic()

    def _s3(self, region: str):
        """An S3 client with the host's own AWS role (the default chain: the instance profile), never a company's key.
        boto3 is imported only here: `rewrap` is the one command that needs it."""
        try:
            import boto3
        except ImportError:
            raise CellError("unsupported_host", "rewrap needs boto3 on the host (pip install boto3)") from None
        return boto3.client("s3", region_name=region or None)

    def public_access_blocked(self, bucket: str, region: str) -> tuple[bool, str]:
        """(True, "") when the bucket's public access block has all four flags on, else (False, why); read-only
        (GetPublicAccessBlock). An error reading it is raised: unknown is not safe."""
        from backend import replication

        return replication.bucket_blocks_public_access(self._s3(region), bucket)

    def delete_key_copies(self, bucket: str, prefix: str, region: str) -> int:
        """Every version and delete marker under <prefix>/credential-key/ deleted with the host's role; how many."""
        from backend import replication

        return replication.delete_key_copies(self._s3(region), bucket, prefix)

    def delete_bundle_key_copies(self, bucket: str, digests: list[str], region: str) -> tuple[int, int]:
        """Every version of a bundle key copy under backups/ whose SHA-256 is one of `digests` deleted with the host's
        role: (deleted, kept), kept being other companies' or unattributable copies left alone."""
        from backend import replication

        return replication.delete_bundle_key_copies(self._s3(region), bucket, set(digests))


# --- inputs ----------------------------------------------------------------------------------------------------


def check_slug(value: str, *, new: bool = False) -> str:
    slug = value or ""
    if not SLUG_RE.fullmatch(slug) or "--" in slug:
        raise CellError(
            "bad_slug",
            "--company is 1 to 40 lowercase letters, digits and single hyphens, starting and "
            "ending with a letter or digit",
        )
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
        raise CellError(
            "bad_args", f"{flag} is a whole number of minutes from 0 to {SLEEP_MINUTES_MAX} (0 = never sleeps)"
        )
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


def refuse_server_host(host: str, env: dict[str, str], flag: str) -> None:
    """bad_domain when a page host is the app's, visuals' or runners' host in the company's settings: its server refuses
    to start with that reader origin (backend/company_page/reader_app.py)."""
    for key in ("TICO_PUBLIC_URL", "TICO_VISUAL_ORIGIN", "TICO_RUNNER_URL"):
        value = (env.get(key) or "").strip()
        try:
            other = urllib.parse.urlsplit(value).hostname if value else None  # as backend/visuals.py hostname()
        except ValueError:
            other = None
        if host and other and other.lower() == host:
            raise CellError("bad_domain", f"{flag} {host} is the company's {key}")


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
        raise CellError(
            "bad_archive", "--import-data: no hub.sqlite at its top; archive the contents of /data (tar -C /data .)"
        )
    return total


def backup_keys(text: str, flag: str = "--backup-credentials-file") -> tuple[str, str]:
    """--backup-credentials-file (and --kms-credentials-file, `flag`): KEY=VALUE lines (LITESTREAM_ or AWS_ACCESS_KEY_ID
    and _SECRET_ACCESS_KEY), or the JSON `aws iam create-access-key` prints ({"AccessKey": {"AccessKeyId",
    "SecretAccessKey"}}, or that object alone)."""
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
        raise CellError("secret_file", f"{flag}: no access key id and secret access key in it")
    return key, secret


def check_kms_key(value: str, flag: str = "--kms-key") -> str:
    """A KMS key's ARN (arn:aws:kms:<region>:<account>:key/<id>), refused (bad_args) otherwise."""
    arn = (value or "").strip()
    if not KMS_ARN_RE.fullmatch(arn):
        raise CellError("bad_args", f"{flag} is a KMS key ARN such as arn:aws:kms:us-west-2:111122223333:key/<id>")
    return arn


def kms_env(slug: str, arn: str, keys: tuple[str, str]) -> dict[str, str]:
    """The .env lines that put a company's credential vault on the cell's KMS key: the key, the company's slug as the
    encryption context (so another company's access key cannot unwrap its data key, see `iam_policy`), its own access
    key and the key's region."""
    return {
        KMS_KEY: arn,
        KMS_CONTEXT: slug,
        KMS_KEY_ID: keys[0],
        KMS_SECRET: keys[1],
        KMS_REGION: KMS_ARN_RE.fullmatch(arn).group(1),
    }


def kms_set(env: dict[str, str]) -> bool:
    """The company's .env has a KMS key, its context and an access key pair: its server can wrap its data key."""
    return all(env.get(k) for k in (KMS_KEY, KMS_CONTEXT, KMS_KEY_ID, KMS_SECRET))


def iam_policy(slug: str, arn: str) -> dict:
    """The IAM policy for one company's KMS access key: wrap, unwrap and make data keys with the cell's key, and only
    with the company's own encryption context, so its key cannot open another company's data key."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "TicoCredentialsKms",
                "Effect": "Allow",
                "Action": ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"],
                "Resource": arn,
                "Condition": {
                    "StringEquals": {
                        "kms:EncryptionContext:company": slug,
                        "kms:EncryptionContext:application": KMS_APPLICATION,
                    }
                },
            }
        ],
    }


class Parser(argparse.ArgumentParser):
    """Argument errors are answered in JSON like every other error."""

    def error(self, message):
        raise CellError("bad_args", message)


def parser() -> Parser:
    p = Parser(
        prog="python3 -m setup cell",
        description="Many companies' Tico servers on one shared host (docs/cell.md). "
        "Prints one JSON object. Secrets come only from 0600 files.",
    )
    sub = p.add_subparsers(dest="action", required=True, parser_class=Parser)

    def cmd(name, help):
        c = sub.add_parser(name, help=help)
        c.add_argument("--json", action="store_true", help="accepted for clarity: the answer is always JSON")
        return c

    i = cmd("init", "once per host: Docker, the router, the tunnel and the metadata firewall")
    i.add_argument("--tunnel-token-file", metavar="F", help="the Cloudflare tunnel's token (0600); kept for later runs")
    i.add_argument("--domain", help="the domain every company is a name under (default tico.team, or the saved one)")
    i.add_argument(
        "--page-domain",
        metavar="DOMAIN",
        help="the domain of the companies' page hosts (docs/company-page.md), "
        "one wildcard route for all of them; kept when left out, \"\" clears it",
    )
    i.add_argument(
        "--meet-domain",
        metavar="HOST",
        help="Tico Meet's TURN hostname, such as meet-cell-1.tico.team (a DNS-only A record to this host): runs "
        "LiveKit for every company (docs/cell.md \"Tico Meet\"); kept when left out, \"\" turns it off",
    )
    i.add_argument(
        "--kms-key",
        metavar="ARN",
        help="the cell's one KMS key, used by `add` when it gives none; kept when left out, \"\" clears it",
    )
    a = cmd("add", "create or converge one company's server; answers once /healthz answers through the router")
    a.add_argument("--company", required=True, metavar="SLUG")
    a.add_argument("--domain", required=True, help="<slug>.<the cell's domain>, or with --own-domain any hostname")
    a.add_argument("--own-domain", action="store_true", help="staff, moving an install: --domain is its own hostname")
    a.add_argument(
        "--alias",
        action="append",
        metavar="HOST",
        help=f"another hostname for the same server (repeat, at most {MAX_ALIASES}); replaces the saved list",
    )
    a.add_argument(
        "--page-host",
        metavar="HOST",
        help="the company's public page's hostname, such as acme.tico.page: its "
        "files are served from the cell; kept when left out, \"\" clears it",
    )
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

    def kms_flags(c):
        c.add_argument(
            "--kms-key", metavar="ARN", help="the cell's KMS key for the company's stored secrets (default: init's)"
        )
        c.add_argument(
            "--kms-credentials-file",
            metavar="F",
            help="the company's own AWS access key for KMS (0600), limited by `kms-policy`'s policy",
        )

    kms_flags(a)
    a.add_argument("--company-name", help="the team's name when there is no --company-file (default from the slug)")
    a.add_argument("--memory", help=f"memory limit (default {MEMORY_DEFAULT})")
    a.add_argument("--version", help="the release to run, vX.Y.Z (default the installer's own release)")
    a.add_argument("--hosted", choices=list(st.HOSTED), help=f"TICO_HOSTED (default {HOSTED_DEFAULT})")
    a.add_argument("--signin-label", help=f"the sign-in button's name (default \"{SIGNIN_LABEL}\")")
    a.add_argument(
        "--env-file",
        metavar="F",
        help="settings carried from a moved install, KEY=VALUE (0600); kept in extra.env, replaced when given again",
    )
    a.add_argument(
        "--import-data",
        metavar="FILE",
        help="a moved install's /data as .tar or .tar.gz, unpacked into an empty data folder before the first start",
    )
    a.add_argument(
        "--sleep-minutes",
        metavar="N",
        help=f"the sleep policy: idle minutes before it sleeps, 0 to "
        f"{SLEEP_MINUTES_MAX} (0 = never); kept from the record when left out",
    )
    a.add_argument("--timeout", type=int, default=HEALTH_TIMEOUT, help="seconds to wait for /healthz")
    cmd("list", "every company on this host")
    for name, help in (
        ("status", "one company"),
        ("stop", "stop one company's server; its data stays"),
        ("start", "start it again; answers once /healthz answers"),
        ("sleep", "stop its server; its first request starts it again (the wake service)"),
        ("wake", "start a sleeping company's server; answers once /healthz answers"),
    ):
        c = cmd(name, help)
        c.add_argument("--company", required=True, metavar="SLUG")
        if name in ("start", "wake"):
            c.add_argument("--timeout", type=int, default=HEALTH_TIMEOUT)
        if name == "sleep":
            c.add_argument(
                "--wake-at",
                metavar="TIME",
                help="a timed wake: an ISO-8601 UTC time such as "
                "2026-10-30T18:00:00Z, at most 31 days out; the wake service starts the company then",
            )
    z = cmd("set-sleep", "a company's sleep policy, which its server reads (GET /sleep); no policy until set")
    z.add_argument("--company", required=True, metavar="SLUG")
    z.add_argument(
        "--minutes",
        required=True,
        metavar="N|default",
        help=f"idle minutes before it sleeps, 0 to {SLEEP_MINUTES_MAX} (0 = never); default removes the policy",
    )
    h = cmd("set-page-host", "a running company's page host (docs/company-page.md), without add's sign-in settings")
    h.add_argument("--company", required=True, metavar="SLUG")
    h.add_argument("--host", required=True, help="such as acme.tico.page; \"\" clears it (the folder is kept)")
    h.add_argument("--timeout", type=int, default=HEALTH_TIMEOUT, help="seconds to wait for /healthz")
    k = cmd("set-slack", "a company's Slack gateway (docs/slack.md) on or off; off until turned on, kept in its record")
    k.add_argument("--company", required=True, metavar="SLUG")
    k.add_argument("state", choices=["on", "off"])
    k.add_argument("--timeout", type=int, default=HEALTH_TIMEOUT, help="seconds to wait for /healthz")
    cmd("prune", "remove the release images and core folders no company here needs any more (also after each update)")
    u = cmd("update", "run another release; the database is snapshotted first and put back if it does not come up")
    u.add_argument("--company", required=True, metavar="SLUG")
    u.add_argument("--version", required=True)
    u.add_argument("--timeout", type=int, default=HEALTH_TIMEOUT)
    kms_flags(u)
    q = cmd("kms-policy", "the IAM policy for one company's KMS access key: the cell's key, its own context only")
    q.add_argument("--company", required=True, metavar="SLUG")
    q.add_argument("--kms-key", metavar="ARN", help="the cell's KMS key (default: init's)")
    w = cmd(
        "rewrap",
        "move a company's stored secrets to a fresh KMS data key, then delete its key files here and in its backups",
    )
    w.add_argument("--company", required=True, metavar="SLUG")
    w.add_argument("--timeout", type=int, default=HEALTH_TIMEOUT, help="seconds to wait for /healthz after")
    w.add_argument("--step-timeout", type=int, default=1800, help="seconds the rewrap step may take")
    r = cmd("remove", "remove one company's containers, route and (unless --keep-data) its folder")
    r.add_argument("--company", required=True, metavar="SLUG")
    r.add_argument("--keep-data", action="store_true", help="keep its folder, data and port for a later add")
    f = cmd("profile", "py-spy on a company's server for a few seconds: its hottest stacks (docs/cell-stats.md)")
    f.add_argument("--company", required=True)
    f.add_argument("--seconds", type=int, default=30, help="how long to sample, 5 to 60 (default 30)")
    o = cmd("route", "a service on this host that is not a company (the public demo): its hosts to its loopback ports")
    o.add_argument("op", choices=["set", "remove", "list"])
    o.add_argument("--name", metavar="NAME", help="the route's name (a slug)")
    o.add_argument("--host", action="append", metavar="HOST", help=f"a hostname (repeat, at most {MAX_ROUTE_HOSTS})")
    o.add_argument(
        "--port",
        action="append",
        type=int,
        metavar="PORT",
        help=f"a loopback port (repeat, at most {MAX_ROUTE_PORTS}): the first healthy one is used",
    )
    o.add_argument(
        "--noindex",
        action="store_true",
        help="X-Robots-Tag: noindex, nofollow and a robots.txt that disallows everything",
    )
    o.add_argument(
        "--if-router-current",
        action="store_true",
        help="refuse (router_changed) unless the router's "
        "file is what this checkout writes, checked under the cell lock",
    )
    o.add_argument(
        "--router-only",
        action="store_true",
        help="leave the tunnel's routes for a later change (no "
        "tunnel restart); the answer's tunnel_pending says whether one is due",
    )
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
    return {
        "default_subnet": str(default),
        "default_gateway": str(default.network_address + 1),
        "edge_subnet": str(edge),
        "edge_range": str(ipaddress.ip_network((base + 8, 29))),
        "edge_gateway": str(edge.network_address + 1),
        "caddy_address": str(edge.network_address + 2),
        "cloudflared_address": str(edge.network_address + 3),
    }


def router_config(companies: list[dict], routes: list[dict] = (), meet: bool = False) -> str:
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
    itself (header_up replaces any copy the visitor sent). A stopped one is answered 503 at once.

    A page host (page_site) serves its three public paths from disk first, and only those; see page_site. Routes
    (`cell route`, route_site) come after every company, before the catch-all. With Tico Meet on (`meet`), a running
    company's /rtc goes to the cell's LiveKit (company_site)."""
    lines = [
        "# Written by `python3 -m setup cell`; regenerated whenever a company is added, removed, stopped, started, "
        "put to sleep or woken. Do not edit.",
        "{",
        "\tadmin localhost:2019",
        "\tauto_https off",
        "\tpersist_config off",
        f"\thttp_port {ROUTER_PORT}",
        f"\tdefault_bind {ROUTER_ADDRESS}",
        "}",
        "",
    ]
    for c in sorted(companies, key=lambda c: c["domain"]):
        sites = ", ".join(f"http://{h}:{ROUTER_PORT}" for h in [c["domain"], *c.get("aliases", [])])
        lines += company_site(c, sites, meet)
        if c.get("page_host"):
            lines += page_site(c)
    for r in sorted(routes, key=lambda r: r["name"]):
        lines += route_site(r)
    lines += [
        f"http://:{ROUTER_PORT} {{",
        f"\trespond {ROUTER_HEALTH_PATH} \"ok\" 200",
        "\trespond \"No Tico company at this address.\" 404",
        "}",
    ]
    return "\n".join(lines) + "\n"


HELD = [f"\t\tlb_try_duration {HOLD_FOR}", f"\t\tlb_try_interval {HOLD_EVERY}"]
HELD_ERRORS = ["\thandle_errors {", "\t\theader Retry-After 5", f"\t\trespond \"{HOLD_BODY}\" 503", "\t}"]


def company_site(c: dict, sites: str, meet: bool = False) -> list[str]:
    """One company's domain (and aliases) block: its server, the wake service while it sleeps, or a 503 when stopped.
    With Tico Meet on, a running company's /rtc (LiveKit's signal WebSocket, which checks its own token) goes to the
    cell's LiveKit first, held as the server is and kept open across a reload; everything else goes to the server.
    Without it the block is what it was before Tico Meet, byte for byte, so an upgrade does not reload the router."""
    state = c.get("state")
    if state == "stopped":
        return [f"{sites} {{", "\trespond \"This Tico company is stopped.\" 503", "}", ""]
    if state == "sleeping":
        return [
            f"{sites} {{",
            f"\treverse_proxy {WAKE_ADDRESS}:{WAKE_PORT} {{",
            f"\t\theader_up {WAKE_HEADER} {c['slug']}",
            "\t\theader_up X-Forwarded-Proto https",
            *HELD,
            "\t\tflush_interval -1",
            "\t}",
            *HELD_ERRORS,
            "}",
            "",
        ]
    server = [
        f"\treverse_proxy 127.0.0.1:{int(c['port'])} {{",
        "\t\theader_up X-Forwarded-Proto https",
        *HELD,
        f"\t\tstream_close_delay {STREAM_CLOSE_DELAY}",
        "\t}",
    ]
    if meet:
        server = [
            "\t@rtc path /rtc /rtc/*",
            "\thandle @rtc {",
            f"\t\treverse_proxy {ROUTER_ADDRESS}:{MEET_PORT} {{",
            *("\t" + line for line in HELD),
            f"\t\t\tstream_close_delay {STREAM_CLOSE_DELAY}",
            "\t\t}",
            "\t}",
            "\thandle {",
            *("\t" + line for line in server),
            "\t}",
        ]
    return [f"{sites} {{", *server, *HELD_ERRORS, "}", ""]


def page_site(c: dict) -> list[str]:
    """A page host: GET or HEAD of exactly `/`, `/data.json` or `/feed.xml`, or of a file under `/assets/`, with no reader
    cookie and no query string, is a file from the company's `current` release (`index.html` for `/`). A missing file
    falls through to the company's
    server, so an old or empty folder costs nothing. Everything else on the host goes to the server, or to the wake
    service while it sleeps: those are the same proxies as the company's own host, with the same hold. The router
    removes the Authorization header and the app's cookies from every request it sends on, so a visitor's bearer token
    or app session never reaches the company's server through its page host. A stopped company answers 503 as its
    other hosts do; its files stay in place for when it starts again.

    The folder is the company's own (`<root>/pages/<slug>`, mounted read-only here at /pages): a route names only this
    company's folder, and the slug was validated when the record was made."""
    root = f"{PAGE_MOUNT_ROUTER}/{c['slug']}/current"
    site = f"http://{c['page_host']}:{ROUTER_PORT}"
    if c.get("state") == "stopped":
        return [f"{site} {{", "\trespond \"This Tico company is stopped.\" 503", "}", ""]
    guard = [
        "\t\tmethod GET HEAD",
        f"\t\tnot header Cookie {READER_COOKIE_GLOB}",
        "\t\texpression \"{http.request.uri.query} == ''\"",
    ]
    lines = [f"{site} {{", "\trequest_header -Authorization", f"\trequest_header Cookie \"{APP_COOKIES_RE}\" \"\""]
    for name, paths, file in (
        ("page_index", "/", "index.html"),
        ("page_files", "/data.json /feed.xml", "{path}"),
        ("page_assets", "/assets/*", "{path}"),
    ):
        lines += [
            f"\t@{name} {{",
            *guard,
            f"\t\tpath {paths}",
            "\t\tfile {",
            f"\t\t\troot {root}",
            f"\t\t\ttry_files {file}",
            "\t\t}",
            "\t}",
            f"\thandle @{name} {{",
            f"\t\troot * {root}",
            f"\t\theader Cache-Control \"{PAGE_CACHE}\"",
            "\t\theader X-Content-Type-Options nosniff",
            "\t\tfile_server",
            "\t}",
        ]
    lines += ["\thandle {", *("\t" + line for line in proxy_lines(c)), "\t}", *HELD_ERRORS, "}", ""]
    return lines


def proxy_lines(c: dict) -> list[str]:
    """The reverse proxy for an awake or sleeping company (the same as its domain's, so a page host holds alike)."""
    if c.get("state") == "sleeping":
        return [
            f"\treverse_proxy {WAKE_ADDRESS}:{WAKE_PORT} {{",
            f"\t\theader_up {WAKE_HEADER} {c['slug']}",
            "\t\theader_up X-Forwarded-Proto https",
            *HELD,
            "\t\tflush_interval -1",
            "\t}",
        ]
    return [
        f"\treverse_proxy 127.0.0.1:{int(c['port'])} {{",
        "\t\theader_up X-Forwarded-Proto https",
        *HELD,
        f"\t\tstream_close_delay {STREAM_CLOSE_DELAY}",
        "\t}",
    ]


def route_site(r: dict) -> list[str]:
    """A route (`cell route set`): something on this host that is not a company, such as Tico's public demo, on hosts
    of its own, sent to its loopback ports. With more than one port, the first port whose /healthz answers gets each
    request (Caddy's active health check), so a second copy can start beside the first and take over when the first
    stops, with no router reload. A route holds requests the way a company does. `noindex` adds
    `X-Robots-Tag: noindex, nofollow` to every answer and serves a robots.txt that disallows everything."""
    sites = ", ".join(f"http://{h}:{ROUTER_PORT}" for h in r["hosts"])
    ports = [int(p) for p in r["ports"]]
    tag = [f"\theader X-Robots-Tag \"{NOINDEX}\""] if r.get("noindex") else []
    robots = (
        [
            "\thandle /robots.txt {",
            "\t\theader Content-Type \"text/plain; charset=utf-8\"",
            f"\t\trespond \"{ROBOTS_TXT}\" 200",
            "\t}",
        ]
        if r.get("noindex")
        else []
    )
    health = (
        [
            "\t\t\tlb_policy first",
            f"\t\t\thealth_uri {contract.HEALTH_PATH}",
            "\t\t\thealth_interval 2s",
            "\t\t\thealth_timeout 2s",
            "\t\t\tfail_duration 5s",
        ]
        if len(ports) > 1
        else []
    )
    return [
        f"{sites} {{",
        *tag,
        *robots,
        "\thandle {",
        "\t\treverse_proxy " + " ".join(f"127.0.0.1:{p}" for p in ports) + " {",
        "\t\t\theader_up X-Forwarded-Proto https",
        *("\t" + line for line in HELD),
        *health,
        f"\t\t\tstream_close_delay {STREAM_CLOSE_DELAY}",
        "\t\t}",
        "\t}",
        "\thandle_errors {",
        *("\t" + line for line in tag),
        "\t\theader Retry-After 5",
        f"\t\trespond \"{HOLD_BODY}\" 503",
        "\t}",
        "}",
        "",
    ]


def hosts_of(c: dict) -> list[str]:
    """Every host a company answers on: its domain, its aliases and its page host (the wake service checks these too)."""
    return [c["domain"], *c.get("aliases", []), *([c["page_host"]] if c.get("page_host") else [])]


def tunnel_hosts(
    domain: str, companies: list[dict], page_domain: str | None = None, routes: list[dict] = ()
) -> list[str]:
    """The company and route hosts the tunnel's wildcards do not match: `*.<domain>` and, when set, `*.<page domain>`,
    each covering exactly one label."""
    covered = [domain] + ([page_domain] if page_domain else [])

    def wildcard(h: str) -> bool:
        return any(h.endswith("." + d) and "." not in h[: -len(d) - 1] for d in covered)

    every = {h for c in companies for h in hosts_of(c)} | {h for r in routes for h in r["hosts"]}
    return sorted(h for h in every if not wildcard(h))


def tunnel_config(domain: str, hosts: list[str] = (), page_domain: str | None = None) -> str:
    """cloudflared's routes: `hosts` (see tunnel_hosts), then the wildcards, all to the router; anything else a 404.
    Without `hosts` and a page domain it is the file every cell had before them, byte for byte (so no restart on upgrade)."""
    service = f"    service: http://{ROUTER_ADDRESS}:{ROUTER_PORT}"
    lines = [
        "# Written by `python3 -m setup cell init`: every company's name to the router; anything else a 404.",
        "ingress:",
    ]
    for h in hosts:
        lines += [f"  - hostname: \"{h}\"", service]
    lines += [f"  - hostname: \"*.{domain}\"", service]
    if page_domain:
        lines += [f"  - hostname: \"*.{page_domain}\"", service]
    return "\n".join(lines + ["  - service: http_status:404"]) + "\n"


def edge_compose(root: Path, tunnel: bool, meet_domain: str | None = None) -> str:
    """The router and the tunnel: on the host's network, so they reach each company's loopback port; nothing else. The
    router also reads the page folders, read-only. With `meet_domain`, Tico Meet too (meet_services)."""
    lines = [
        "# Written by `python3 -m setup cell init`: the shared host's front door. Companies are separate projects.",
        f"name: {EDGE_PROJECT}",
        "services:",
        "  router:",
        f"    image: {ROUTER_IMAGE}",
        f"    container_name: {ROUTER_CONTAINER}",
        "    network_mode: host",
        "    restart: unless-stopped",
        "    init: true",
        "    security_opt: [\"no-new-privileges:true\"]",
        "    cap_drop: [ALL]",
        "    cap_add: [NET_BIND_SERVICE]",  # caddy's binary carries this as a file capability; exec fails without it
        "    read_only: true",
        "    tmpfs: [/tmp]",
        "    command: [caddy, run, --config, /etc/caddy/Caddyfile, --adapter, caddyfile]",
        "    volumes:",
        f"      - {root}/router:/etc/caddy:ro",
        "      - router-data:/data",
        "      - router-config:/config",
        f"      - {root}/{PAGES}:{PAGE_MOUNT_ROUTER}:ro",
    ]
    if tunnel:
        lines += [
            "  tunnel:",
            f"    image: {TUNNEL_IMAGE}",
            "    container_name: tico-cell-tunnel",
            "    network_mode: host",
            "    restart: unless-stopped",
            "    init: true",
            "    security_opt: [\"no-new-privileges:true\"]",
            "    cap_drop: [ALL]",
            "    read_only: true",
            "    command: tunnel --no-autoupdate --metrics 127.0.0.1:8081 --config /etc/cloudflared/cloudflared.yml run",
            f"    env_file: [{root}/tunnel.env]",
            "    volumes:",
            f"      - {root}/tunnel:/etc/cloudflared:ro",
            "    depends_on: [router]",
        ]
    if meet_domain:
        lines += meet_services(root, meet_domain)
    lines += ["volumes:", "  router-data:", "  router-config:"]
    return "\n".join(lines) + "\n"


def meet_services(root: Path, domain: str) -> list[str]:
    """Tico Meet's two edge services, on the host's network like the router (no docker-proxy for 101 UDP ports):

    - `certs`: Caddy (the router's pinned image) holding the TURN certificate for `domain`, by HTTP-01 only on port 80,
      so no DNS credential is ever on the shared host. It serves nothing else; its admin API is off (the router's is on
      localhost:2019) and its own https port is MEET_CERTS_HTTPS, which the security group keeps closed. The domain is
      in its environment, so a new one recreates it.
    - `livekit`: LiveKit with the cell's key pair from its key file (a read-only mount, so the secret is in neither
      `docker inspect` nor `docker compose config`), TURN over TLS on 443 with that certificate (read-only), TURN over
      UDP and the media ports. It reads the certificate only when it starts: the cell updater restarts it
      after a renewal, once no room is open (Cell.meet_renew)."""
    return [
        "  certs:",
        f"    image: {ROUTER_IMAGE}",
        f"    container_name: {CERTS_CONTAINER}",
        "    network_mode: host",
        "    restart: unless-stopped",
        "    init: true",
        "    security_opt: [\"no-new-privileges:true\"]",
        "    cap_drop: [ALL]",
        "    cap_add: [NET_BIND_SERVICE]",
        "    read_only: true",
        "    tmpfs: [/tmp, /config]",
        "    environment:",
        f"      TICO_MEET_DOMAIN: {domain}",
        "    command: [caddy, run, --config, /etc/caddy/Caddyfile, --adapter, caddyfile]",
        "    volumes:",
        f"      - {root}/{MEET_DIR}/caddy:/etc/caddy:ro",
        f"      - {root}/{MEET_DIR}/certs:/data",
        "  livekit:",
        f"    image: {MEET_IMAGE}",
        f"    container_name: {MEET_CONTAINER}",
        "    network_mode: host",
        "    restart: unless-stopped",
        "    init: true",
        "    security_opt: [\"no-new-privileges:true\"]",
        "    cap_drop: [ALL]",
        "    cap_add: [NET_BIND_SERVICE]",  # TURN over TLS on 443
        "    read_only: true",
        "    tmpfs: [/tmp]",
        "    environment:",
        "      LIVEKIT_CONFIG: |",
        *("        " + line for line in livekit_config(domain).splitlines()),
        "    volumes:",
        f"      - {(root / MEET_KEYS).parent}:{Path(MEET_KEYS_MOUNT).parent}:ro",
        f"      - {root}/{MEET_DIR}/certs:/certs:ro",
        "    depends_on: [certs]",
    ]


def livekit_config(domain: str) -> str:
    """LiveKit's config: compose.yaml's `livekit`, with TURN over TLS on 443 and the certificate where Caddy keeps it."""
    cert = "/certs/" + MEET_CERT_PATH.format(d=domain)
    return (
        "\n".join(
            [
                f"port: {MEET_PORT}",
                f"key_file: {MEET_KEYS_MOUNT}",
                "rtc:",
                f"  tcp_port: {MEET_RTC_TCP}",
                f"  port_range_start: {MEET_UDP_FIRST}",
                f"  port_range_end: {MEET_UDP_LAST}",
                "  use_external_ip: true",
                "turn:",
                "  enabled: true",
                f"  domain: {domain}",
                f"  tls_port: {MEET_TURN_TLS}",
                f"  udp_port: {MEET_TURN_UDP}",
                f"  cert_file: {cert}.crt",
                f"  key_file: {cert}.key",
                "  external_tls: false",
                "logging:",
                "  json: true",
            ]
        )
        + "\n"
    )


def certs_caddyfile() -> str:
    """The certificate Caddy's config. The same for every domain (it reads TICO_MEET_DOMAIN from its environment)."""
    return (
        "\n".join(
            [
                "# Written by `python3 -m setup cell init --meet-domain`: gets and renews the TURN certificate. Do not edit.",
                "{",
                "\tadmin off",
                "\tpersist_config off",
                f"\thttp_port {MEET_CERTS_HTTP}",
                f"\thttps_port {MEET_CERTS_HTTPS}",
                "\tcert_issuer acme {",
                "\t\tdisable_tlsalpn_challenge",
                "\t}",
                "}",
                "",
                "{$TICO_MEET_DOMAIN} {",
                "\trespond \"Tico Meet\" 200",
                "}",
            ]
        )
        + "\n"
    )


def meet_keys(text: str) -> tuple[str, str]:
    """The cell's LiveKit key pair from its key file (one `<key>: "<secret>"` line); ("", "") when it holds none."""
    pairs = [
        m.groups()
        for line in text.splitlines()
        if (m := re.fullmatch(r'(API[A-Za-z0-9]{8,64}): "([A-Za-z0-9_-]{32,128})"', line.strip()))
    ]
    return pairs[0] if len(pairs) == 1 else ("", "")


def new_meet_keys() -> str:
    """A new key file: one key pair, as LiveKit's key_file reads it (a YAML map; the secret quoted, so YAML always reads
    a string). LiveKit wants a secret of at least 32 characters."""
    key, secret = "API" + secrets.token_hex(8), secrets.token_urlsafe(32)
    return (
        "# Written by `python3 -m setup cell init --meet-domain`: the cell's LiveKit key pair. Holds a secret: keep it "
        "private (0600).\n"
        f'{key}: "{secret}"\n'
    )


def cert_not_after(path: Path) -> float | None:
    """When the first certificate in a PEM file (a chain's leaf) runs out, as a Unix time; None when it is not one."""
    try:
        return ssl.cert_time_to_seconds(str(_ssl._test_decode_cert(str(path))["notAfter"]))
    except (OSError, ValueError, KeyError, ssl.SSLError):
        return None


def meet_token(key: str, secret: str, now: float) -> str:
    """A one-minute HS256 token for LiveKit's server API that may only list rooms (backend/call_media.py signs alike)."""

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    claims = {"iss": key, "sub": "tico-cell", "nbf": int(now) - 10, "exp": int(now) + 60, "video": {"roomList": True}}
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(secret.encode(), f"{head}.{body}".encode("ascii"), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(mac)}"


def docker_time(value: str) -> float | None:
    """`docker inspect`'s {{.State.StartedAt}} (RFC 3339 in UTC, nanoseconds) as a Unix time; None when it is not one."""
    m = re.fullmatch(r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(\.[0-9]+)?Z", value.strip())
    if not m:
        return None
    t = datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    return t.timestamp() + float(m.group(2) or 0)


def company_override(c: dict, data_dir: Path, pages: Path | None = None) -> str:
    """The control folder replaces the stock tico-control volume (Compose merges volumes by their target), so the cell
    updater reads the token where the server reads it, and no other company's server mounts it. A page host adds its
    folder of public files, read-write, and the two settings that name it (the company's server writes them there)."""
    n = networks(c["index"])
    page = (
        [f"      TICO_COMPANY_PAGE_DIR: {PAGE_MOUNT}", f"      TICO_READER_ORIGIN: https://{c['page_host']}"]
        if c.get("page_host")
        else []
    )
    mount = [f"      - {pages}:{PAGE_MOUNT}"] if c.get("page_host") and pages else []
    # The Slack gateway, when it is on: the server's limits with a memory limit of its own, and the data folder only
    # (no control/: the updater token is the server's alone). It stays on the company's default network.
    slack = (
        [
            f"  {SLACK_SERVICE}:",
            f"    mem_limit: {SLACK_MEMORY}",
            f"    memswap_limit: {SLACK_MEMORY}",
            "    environment:",
            "      TICO_CELL:",
            "    labels:",
            f"      {LABEL}: {c['slug']}",
            "    volumes:",
            f"      - {data_dir}:/data",
        ]
        if c.get("slack")
        else []
    )
    return (
        "\n".join(
            [
                "# Written by `python3 -m setup cell`: what makes the stock compose.yaml beside it one company of many on a",
                "# shared host. Its own data and control folders, a memory limit, its own two networks (so none collide), the",
                "# cell marker, a label for `list`, and its page folder when it has a page host; the same for its Slack",
                "# gateway when it is on.",
                "services:",
                "  server:",
                f"    mem_limit: {c['memory']}",
                f"    memswap_limit: {c['memory']}",
                "    environment:",
                "      TICO_CELL:",
                *page,
                "    labels:",
                f"      {LABEL}: {c['slug']}",
                "    volumes:",
                f"      - {data_dir}:/data",
                f"      - {data_dir.parent / 'control'}:/control",
                *mount,
                *slack,
                "networks:",
                "  default:",
                "    ipam:",
                "      config:",
                f"        - subnet: {n['default_subnet']}",
                f"          gateway: {n['default_gateway']}",
            ]
        )
        + "\n"
    )


def updater_url(index: int) -> str:
    """Where a company's server finds its updater: its own default network's gateway, the host side of that bridge.
    Only that company's containers are on the network; setup/cell_updater.py also refuses any other source address."""
    return f"http://{networks(index)['default_gateway']}:{UPDATER_PORT}"


def cell_env(values: dict[str, str], c: dict, meet: dict | None = None) -> dict[str, str]:
    """`values` (a company's .env) with the cell's updater keys: its updater's address and the cell marker, and no
    TICO_UPDATES=tico, which an older core wrote (it turns Update now and the update keys off). COMPOSE_PROFILES comes
    from the record: `slack` when its Slack gateway is on, else none.

    Tico Meet (`meet`, Cell.meet: the cell's TURN domain and key pair, or None while it is off): browsers connect to
    the company's own host (the router sends /rtc to LiveKit), and its server calls LiveKit's API on its default
    network's gateway, as it calls its updater. Off, the keys are removed, so calls answer meet_unavailable."""
    out = {k: v for k, v in values.items() if k != "TICO_UPDATES" and k not in MEET_ENV_KEYS}
    out.update(
        TICO_UPDATER_URL=updater_url(c["index"]),
        TICO_CELL="1",
        COMPOSE_PROFILES=SLACK_SERVICE if c.get("slack") else "",
    )
    if meet:
        out.update(
            TICO_MEET_URL=f"wss://{c['domain']}",
            TICO_MEET_INTERNAL_URL=f"http://{networks(c['index'])['default_gateway']}:{MEET_PORT}",
            TICO_MEET_DOMAIN=meet["domain"],
            TICO_MEET_API_KEY=meet["key"],
            TICO_MEET_API_SECRET=meet["secret"],
        )
    return out


def updater_unit(core: Path, python: str, root: Path) -> str:
    """The cell updater as a systemd service: it runs `update`, so it needs what `cell` needs (root, the Docker CLI,
    the cell's files and locks, the host's bridge addresses) and nothing a container would add. Its code is the core
    folder `init` ran from; only another `init` moves it. A stop waits for an update under way (TimeoutStopSec): the
    main process gets SIGTERM, finishes what it runs, then exits."""
    return (
        "\n".join(
            [
                "# Written by `python3 -m setup cell init`: each company's updater (setup/cell_updater.py, docs/cell.md).",
                "[Unit]",
                "Description=Tico cell: each company's updater",
                "After=docker.service network-online.target",
                "Wants=docker.service",
                "",
                "[Service]",
                "Type=simple",
                f"WorkingDirectory={core}",
                f"ExecStart={python} -m setup.cell_updater --root {root}",
                "Restart=always",
                "RestartSec=5",
                "KillMode=mixed",
                "TimeoutStopSec=1800",
                "NoNewPrivileges=yes",
                "PrivateTmp=yes",
                "",
                "[Install]",
                "WantedBy=multi-user.target",
            ]
        )
        + "\n"
    )


def wake_unit(core: Path, python: str, root: Path) -> str:
    """The wake service as a systemd service: it runs `wake` (Docker, the company's files and locks, the router's
    reload), so it is root like the updater, and on loopback only. A stop waits a little for a start under way; one cut
    short is finished by the next request's `wake` (Compose's up is idempotent)."""
    return (
        "\n".join(
            [
                "# Written by `python3 -m setup cell init`: starts a sleeping company on its first request (setup/cell_wake.py).",
                "[Unit]",
                "Description=Tico cell: wake sleeping companies",
                "After=docker.service network-online.target",
                "Wants=docker.service",
                "",
                "[Service]",
                "Type=simple",
                f"WorkingDirectory={core}",
                f"ExecStart={python} -m setup.cell_wake --root {root}",
                "Restart=always",
                "RestartSec=1",
                "KillMode=mixed",
                "TimeoutStopSec=60",
                "NoNewPrivileges=yes",
                "PrivateTmp=yes",
                "",
                "[Install]",
                "WantedBy=multi-user.target",
            ]
        )
        + "\n"
    )


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
        self.lock_wait = 900.0  # the cell updater waits for none: a held lock is another command at work
        self.taken = ""  # the snapshot the last `update` took or kept, for the updater's /status
        self.direct = False  # health on the server's own port, not through the router (`wake`)
        self.meet_days_left: float | None = None  # see meet_renew

    # paths and records
    def dir(self, slug: str) -> Path:
        return self.root / "companies" / slug

    def scrub(self, text: str) -> str:
        return envfile.scrub(text, self.secrets)

    def load_cell(self) -> dict:
        try:
            return json.loads((self.root / "cell.json").read_text())
        except (OSError, ValueError):
            raise CellError(
                "not_initialized", "this host has no cell: run `python3 -m setup cell init` first"
            ) from None

    def saved_cell(self) -> dict:
        """cell.json, or {} before the first `init`."""
        try:
            return json.loads((self.root / "cell.json").read_text())
        except (OSError, ValueError):
            return {}

    def meet(self) -> dict | None:
        """Tico Meet while the cell has it on (`init --meet-domain`): {"domain", "key", "secret"}, else None. The secret
        joins the ones every answer and error is scrubbed of."""
        domain = self.saved_cell().get("meet_domain")
        if not domain:
            return None
        key, secret = meet_keys(_read(self.root / MEET_KEYS))
        if not key:
            raise CellError(
                "meet_failed", f"Tico Meet is on but {self.root / MEET_KEYS} holds no key pair: re-run `cell init`"
            )
        if secret not in self.secrets:
            self.secrets.append(secret)
        return {"domain": domain, "key": key, "secret": secret}

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
        return [
            "docker",
            "compose",
            "--project-name",
            project(slug),
            "--project-directory",
            str(d),
            "--env-file",
            str(d / ".env"),
            "-f",
            str(d / "compose.yaml"),
            "-f",
            str(d / "compose.cell.yaml"),
            *args,
        ]

    def containers(self, slug: str = "", service: str = SERVER_SERVICE) -> dict[str, dict]:
        """{slug: {"state", "health"}} for the cell's containers of one Compose service (the servers by default; a Slack
        gateway carries the same label), from one `docker ps`."""
        return {s: row for (s, svc), row in self.labelled(slug).items() if svc == service}

    def labelled(self, slug: str = "") -> dict[tuple[str, str], dict]:
        """{(slug, service): {"state", "health"}} for every container with the cell's label, from one `docker ps`. A row
        without a Compose service label is a server (a container made by hand)."""
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
            health = (
                "unhealthy"
                if "(unhealthy)" in status
                else "healthy"
                if "(healthy)" in status
                else "starting"
                if "health: starting" in status
                else ""
            )
            if name:
                service = labels.get("com.docker.compose.service") or SERVER_SERVICE
                out[(name, service)] = {"state": str(row.get("State", "")), "health": health}
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

    def meet_firewall(self, on: bool) -> bool:
        """With `on`, INPUT's first three rules are MEET_INPUT and no other rule has its tag; without, no rule has it."""
        r = self.system.run(["iptables", "-S", "INPUT"])
        rules = [line.strip() for line in r.stdout.splitlines() if line.startswith("-A INPUT ")]
        tagged = [line for line in rules if f" --comment {MEET_TAG} " in line]
        return r.returncode == 0 and (rules[:3] == MEET_INPUT == tagged if on else not tagged)

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
                raise CellError(
                    "unhealthy",
                    self.scrub(
                        f"{c['domain']} did not answer {contract.HEALTH_PATH} {where} "
                        f"within {timeout}s" + (f"; last log lines: {tail}" if tail else "")
                    ),
                )
            self.system.sleep(HEALTH_POLL)

    def write_router(self, meet: bool | None = None) -> bool:
        """Regenerate the Caddyfile from every company not removed and reload the router. A config the router refuses
        is replaced by the one it had, so a bad change never takes every company down. `meet` is whether Tico Meet is
        on (default: what cell.json says)."""
        path = self.root / "router" / "Caddyfile"
        meet = bool(self.saved_cell().get("meet_domain")) if meet is None else meet
        text = router_config(self.live_companies(), self.routes(), meet)
        try:
            old = path.read_text()
        except OSError:
            old = None
        if old == text:
            return False
        _write_atomic(path, text)
        r = self.system.run(
            [
                "docker",
                "exec",
                ROUTER_CONTAINER,
                "caddy",
                "reload",
                "--config",
                "/etc/caddy/Caddyfile",
                "--adapter",
                "caddyfile",
            ]
        )
        if r.returncode:
            if old is not None:
                _write_atomic(path, old)
            raise CellError(
                "router_failed",
                self.scrub("the router refused the new config: " + (r.stderr or r.stdout).strip()[-400:]),
            )
        return True

    def edge(self) -> list[str]:
        return ["docker", "compose", "--project-name", EDGE_PROJECT, "-f", str(self.root / "edge.yaml")]

    def live_companies(self) -> list[dict]:
        return [c for c in self.companies() if c.get("state") != "removed"]

    def routes(self) -> list[dict]:
        """Every route (`cell route set`), by name. A file that is not a route record is skipped, not fatal: the router
        still gets every company."""
        out = []
        with contextlib.suppress(OSError):
            for p in sorted((self.root / ROUTES).glob("*.json")):
                with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
                    r = json.loads(p.read_text())
                    if r["name"] == p.stem and r["hosts"] and r["ports"]:
                        out.append(r)
        return out

    def pages_dir(self, slug: str) -> Path:
        """A company's folder of public files for its page host (mounted into its server, read by the router)."""
        return self.root / PAGES / slug

    def tunnel_text(self, domain: str, page_domain: str | None = None) -> str:
        return tunnel_config(
            domain, tunnel_hosts(domain, self.live_companies(), page_domain, self.routes()), page_domain
        )

    def write_tunnel(self) -> bool:
        """Under the cell lock: regenerate cloudflared's routes and, when the file changed and the tunnel runs, restart
        it (cloudflared reads them only when it starts, as `init` does). A restart that fails puts the old file back,
        so the next `add` or `remove` tries again."""
        path = self.root / "tunnel" / "cloudflared.yml"
        cell = self.load_cell()
        text = self.tunnel_text(cell["domain"], cell.get("page_domain"))
        try:
            old = path.read_text()
        except OSError:
            old = None
        if old == text:
            return False

        def put(body: str) -> None:
            _write_atomic(path, body)
            self.system.chown(path, TUNNEL_UID, TUNNEL_UID)  # a rewritten file comes back root-owned

        put(text)
        if (self.root / "tunnel.env").exists():
            r = self.system.run([*self.edge(), "restart", "tunnel"])
            if r.returncode:
                if old is not None:
                    put(old)
                raise CellError(
                    "docker_failed",
                    self.scrub("restarting the tunnel with its new routes: " + (r.stderr or r.stdout).strip()[-400:]),
                )
        return True

    def image_present(self, version: str) -> bool:
        return (
            self.system.run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", f"{SERVER_IMAGE}:{version}"]
            ).returncode
            == 0
        )

    def pull(self, version: str) -> None:
        """The release's image, without touching any .env (compose.yaml's image is SERVER_IMAGE:TICO_TAG)."""
        if not self.image_present(version):
            self.run(
                ["docker", "pull", "--quiet", f"{SERVER_IMAGE}:{version}"],
                "pull_failed",
                f"pulling {SERVER_IMAGE}:{version}",
            )

    def up(self, slug: str, version: str) -> None:
        self.pull(version)
        self.run(self.compose(slug, "up", "-d", "--remove-orphans"), "docker_failed", "docker compose up")

    def services(self, slug: str) -> list[str]:
        """What has the company's database open: its server, and its Slack gateway when that is on (gateway first)."""
        return [SLACK_SERVICE, SERVER_SERVICE] if (self.load(slug) or {}).get("slack") else [SERVER_SERVICE]

    def ensure_stopped(self, slug: str) -> None:
        """Stop the server (and its Slack gateway), then make sure Docker says none of the company's containers runs:
        nothing touches its database before that."""
        self.run(self.compose(slug, "stop", *self.services(slug)), "stop_failed", "docker compose stop")
        for (_, service), row in sorted(self.labelled(slug).items()):
            if row["state"] not in STOPPED:
                what = "the server" if service == SERVER_SERVICE else f"its {service} container"
                raise CellError("stop_failed", f"{what} is still {row['state']} after docker compose stop")

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
        failed = CellError(
            "update_failed",
            f"{new} did not come up ({err.detail}); back on {c['version']}"
            + (" with the database from before the update" if pending.get("snapshot") else ""),
        )
        failed.extra = {"version": c["version"]}
        raise failed from None

    def answer(self, c: dict, ready_ms: int, changed: bool) -> dict:
        return {
            "ok": True,
            "company": c["slug"],
            "url": f"https://{c['domain']}",
            "port": c["port"],
            "ready_ms": ready_ms,
            "version": c["version"],
            "changed": changed,
            "owner_link_file": str(self.dir(c["slug"]) / "owner-link.txt"),
        }

    # --- commands --------------------------------------------------------------------------------------------

    def init(self, args) -> dict:
        if not self.system.linux():
            raise CellError("unsupported_host", "cell init runs on the shared Linux host (Ubuntu 24.04)")
        token = read_secret(args.tunnel_token_file, "--tunnel-token-file") if args.tunnel_token_file else ""
        if token:
            self.secrets.append(token)
            if not re.fullmatch(r"[A-Za-z0-9+/=_-]{20,4096}", token):
                raise CellError("secret_file", "--tunnel-token-file: not a tunnel token")
        # cell.json is read under the lock: a setting left out is kept as it is now, not as it was before another
        # command (a rollback's `init --meet-domain ""`) changed it.
        with self.lock("cell"):
            saved = self.saved_cell()
            domain = (args.domain or saved.get("domain") or "tico.team").strip().lower().strip(".")
            if not st.DOMAIN_RE.fullmatch(domain):
                raise CellError("bad_domain", "--domain is a plain domain such as tico.team")
            if saved.get("domain") and saved["domain"] != domain and self.companies():
                raise CellError(
                    "bad_domain", f"this cell serves {saved['domain']} and has companies; it cannot move to {domain}"
                )
            # Kept when left out, as --domain is; "" clears it. Its wildcard routes every company's page host.
            page_domain = saved.get("page_domain")
            if args.page_domain is not None:
                page_domain = (args.page_domain or "").strip().lower().strip(".") or None
            if page_domain and (not st.DOMAIN_RE.fullmatch(page_domain) or page_domain == domain):
                raise CellError("bad_domain", "--page-domain is a plain domain of its own, such as tico.page")
            # Tico Meet's TURN host: kept when left out, "" turns Tico Meet off (its key pair stays, for turning it on
            # again).
            meet_domain = saved.get("meet_domain")
            if args.meet_domain is not None:
                meet_domain = (args.meet_domain or "").strip().lower().strip(".") or None
            if meet_domain and (not st.DOMAIN_RE.fullmatch(meet_domain) or meet_domain in (domain, page_domain)):
                raise CellError(
                    "bad_domain", "--meet-domain is a plain hostname of its own, such as meet-cell-1.tico.team"
                )
            if meet_domain:
                self.refuse_taken("", {meet_domain} - {saved.get("meet_domain")})
                for r in self.routes() if not saved.get("meet_domain") else []:
                    clash = sorted(set(r["ports"]) & MEET_ROUTE_PORTS)
                    if clash:
                        raise CellError(
                            "port_taken", f"the route {r['name']} uses port {clash[0]}, which Tico Meet needs"
                        )
            # The cell's one KMS key (Chris, 2026-11-18): kept when left out, "" clears it. `add` uses it when it gives
            # none.
            kms_key = saved.get("kms_key")
            if getattr(args, "kms_key", None) is not None:
                kms_key = check_kms_key(args.kms_key) if args.kms_key.strip() else None
            result = self._init(saved, domain, token, page_domain, meet_domain, kms_key)
        # Last and outside the cell's lock: py-spy is a tool for people, so a slow or failed download never holds up or
        # fails the rest of init (a core update runs init).
        spy = self.py_spy()
        if spy == "installed":
            result["changed"].append("py-spy")
        elif spy != "current":
            result["py_spy"] = spy
        return result

    def _init(
        self,
        saved: dict,
        domain: str,
        token: str,
        page_domain: str | None = None,
        meet_domain: str | None = None,
        kms_key: str | None = None,
    ) -> dict:
        changed = []
        if self.system.run(["docker", "compose", "version"]).returncode:
            self.run(
                ["sh", "-c", "set -eu\n" + "\n".join(remote.docker_install_lines("ubuntu", False))],
                "docker_failed",
                "installing Docker",
            )
            changed.append("docker")
        if self.daemon_settings():
            self.run(["systemctl", "restart", "docker"], "docker_failed", "restarting Docker with the cell's settings")
            changed.append("daemon.json")
        if self.firewall(meet=bool(meet_domain)):
            changed.append("firewall")
        self.run([str(self.host / FIREWALL_SCRIPT)], "firewall_failed", "running the cell's firewall rules")
        if not self.imds_blocked():
            raise CellError(
                "firewall_failed", "the DOCKER-USER rule that blocks 169.254.169.254 is not the chain's first rule"
            )
        if not self.meet_firewall(bool(meet_domain)):
            raise CellError(
                "firewall_failed",
                f"the INPUT rules that keep port {MEET_PORT} to this host and its companies are not "
                + ("the chain's first three" if meet_domain else "gone"),
            )

        for d in ("companies", "router", "tunnel", "locks", PAGES):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        os.chmod(self.root / "companies", 0o700)
        if token:
            changed += (
                ["tunnel token"] if _write_atomic(self.root / "tunnel.env", f"TUNNEL_TOKEN={token}\n", 0o600) else []
            )
        tunnel = (self.root / "tunnel.env").exists()
        routes_changed = _write_atomic(self.root / "tunnel" / "cloudflared.yml", self.tunnel_text(domain, page_domain))
        # cloudflared runs as TUNNEL_UID and cannot read a root-owned directory: every init sets the owner again
        # (a file this run rewrote comes back root-owned, so it is covered too)
        os.chmod(self.root / "tunnel", 0o700)
        for f in (self.root / "tunnel", self.root / "tunnel" / "cloudflared.yml"):
            self.system.chown(f, TUNNEL_UID, TUNNEL_UID)
        caddyfile = self.root / "router" / "Caddyfile"
        if not caddyfile.exists():  # the first run; later ones go through write_router, which reloads
            _write_atomic(caddyfile, router_config(self.live_companies()))
        if meet_domain:
            changed += self.meet_files()
        if _write_atomic(self.root / "edge.yaml", edge_compose(self.root, tunnel, meet_domain)):
            changed.append("edge")
        edge = self.edge()
        self.run([*edge, "up", "-d", "--remove-orphans"], "docker_failed", "starting the router and tunnel")
        if tunnel and routes_changed:  # cloudflared reads its routes only when it starts
            self.run([*edge, "restart", "tunnel"], "docker_failed", "restarting the tunnel with its routes")
            changed.append("tunnel routes")
        started = self.system.monotonic()
        while self.system.http_status(ROUTER_HEALTH_HOST, ROUTER_HEALTH_PATH) != 200:
            if self.system.monotonic() - started > 60:
                raise CellError("router_failed", f"the router does not answer on {ROUTER_ADDRESS}:{ROUTER_PORT}")
            self.system.sleep(0.5)
        # Tico Meet counts as on (the router's /rtc, the companies' TICO_MEET_* settings, cell.json) only once LiveKit
        # answers and has its certificate. One that does not come up and was off before is taken down again; one that
        # was on stays on, as a LiveKit that stopped by itself would.
        problem = self.meet_wait(meet_domain) if meet_domain else ""
        was_on = saved.get("meet_domain")
        if problem:
            meet_domain = was_on
        if self.write_router(meet=bool(meet_domain)):
            changed.append("router")
        if self.updater_service():
            changed.append("updater")
        if self.wake_service():
            changed.append("wake")
        cell = {**saved, "domain": domain, "router": f"http://{ROUTER_ADDRESS}:{ROUTER_PORT}"}
        if CORE_DIR_RE.fullmatch(own_core()):  # HQ runs every later command from the core it last ran init from
            cell["init_cores"] = sorted({*saved.get("init_cores", []), own_core()})
        for name, value in (("page_domain", page_domain), ("meet_domain", meet_domain), ("kms_key", kms_key)):
            if value:
                cell[name] = value
            else:
                cell.pop(name, None)
        if cell != saved:
            _write_atomic(self.root / "cell.json", json.dumps(cell, indent=2, sort_keys=True) + "\n", 0o600)
            changed.append("cell.json")
        if problem:
            if not was_on:
                problem += "; " + self.meet_take_down(tunnel)
            else:
                problem += f"; Tico Meet stays on with {was_on}"
            raise CellError("meet_failed", problem)
        return {
            "ok": True,
            "domain": domain,
            "root": str(self.root),
            "router": cell["router"],
            "tunnel": tunnel,
            "companies": len(self.companies()),
            "changed": changed,
            **({"page_domain": page_domain} if page_domain else {}),
            **({"meet_domain": meet_domain} if meet_domain else {}),
            **({"kms_key": kms_key} if kms_key else {}),
        }

    def py_spy(self) -> str:
        """PY_SPY_VERSION at /usr/local/bin/py-spy, known by its sha256 (never by running it): "current" when it is
        there, "installed" when this run put it there, else why not."""
        arch = self.system.machine()
        if arch not in PY_SPY_WHEELS:
            return f"no py-spy wheel for {arch}"
        url, wheel_digest, digest = PY_SPY_WHEELS[arch]
        target = self.host / PY_SPY
        with contextlib.suppress(OSError):
            if not target.is_symlink() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                return "current"
        try:
            wheel = self.system.fetch(url)
        except OSError as e:
            return f"py-spy download failed: {e}"
        if hashlib.sha256(wheel).hexdigest() != wheel_digest:
            return "py-spy download does not match its pinned sha256"
        try:
            with zipfile.ZipFile(io.BytesIO(wheel)) as z:
                binary = z.read(PY_SPY_MEMBER)
        except (zipfile.BadZipFile, KeyError) as e:
            return f"py-spy wheel unreadable: {e}"
        if hashlib.sha256(binary).hexdigest() != digest:
            return "py-spy in the wheel does not match its pinned sha256"
        tmp = target.with_name(f".{target.name}.{os.getpid()}.new")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(binary)
            os.chmod(tmp, 0o755)
            os.replace(tmp, target)
        except OSError as e:
            return f"py-spy not written: {e}"
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
        return "installed"

    def profile(self, args) -> dict:
        """`seconds` of py-spy on the company's server process (setup/cell_stats.py `t_profile`, the same tool HQ's
        debug requests run): its hottest stacks, and the file with all of them."""
        from . import cell_stats

        r = cell_stats.run_tool(
            cell_stats.Box(self.host, self.root),
            "profile",
            {"company": check_slug(args.company), "seconds": args.seconds},
        )
        if not r["ok"]:
            raise CellError("profile_failed", r["output"])
        return {"ok": True, "company": args.company, "ms": r["ms"], "output": r["output"]}

    def meet_files(self) -> list[str]:
        """Tico Meet's files: the key pair (made once, 0600, never printed; its secret is scrubbed from every answer),
        the certificate Caddy's config, and the folder that holds its certificates (root's only)."""
        changed = []
        path = self.root / MEET_KEYS
        for d in (self.root / MEET_DIR, path.parent, self.root / MEET_DIR / "certs"):
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o700)
        if not meet_keys(_read(path))[0]:
            _write_atomic(path, new_meet_keys(), 0o600)
            changed.append("meet keys")
        os.chmod(path, 0o600)  # LiveKit refuses a key file that others can read
        self.secrets.append(meet_keys(_read(path))[1])
        if _write_atomic(self.root / MEET_DIR / "caddy" / "Caddyfile", certs_caddyfile()):
            changed.append("meet certificates")
        return changed

    def meet_cert(self, domain: str) -> Path:
        """Where the certificate Caddy keeps the TURN certificate for `domain` (LiveKit's /certs)."""
        return self.root / MEET_DIR / "certs" / (MEET_CERT_PATH.format(d=domain) + ".crt")

    def meet_wait(self, domain: str) -> str:
        """Wait up to MEET_UP_WAIT seconds for LiveKit's API to answer on loopback and the TURN certificate to be
        there. "" once both are; else what is missing."""
        key, secret = meet_keys(_read(self.root / MEET_KEYS))
        started = self.system.monotonic()
        while True:
            cert = self.meet_cert(domain).exists()
            api = self.system.meet_rooms(meet_token(key, secret, time.time())) is not None
            if cert and api:
                return ""
            if self.system.monotonic() - started > MEET_UP_WAIT:
                missing = ([] if api else [f"LiveKit's API on {ROUTER_ADDRESS}:{MEET_PORT} does not answer"]) + (
                    []
                    if cert
                    else [f"there is no TURN certificate for {domain} yet (its DNS must point here, port 80 open)"]
                )
                return (
                    f"Tico Meet did not come up in {MEET_UP_WAIT}s: " + " and ".join(missing) + f" (see `docker logs "
                    f"{MEET_CONTAINER}` and `docker logs {CERTS_CONTAINER}`)"
                )
            self.system.sleep(1)

    def meet_take_down(self, tunnel: bool) -> str:
        """After a Tico Meet that did not come up: the edge project and the firewall without it again. Says what it did."""
        try:
            _write_atomic(self.root / "edge.yaml", edge_compose(self.root, tunnel))
            self.run([*self.edge(), "up", "-d", "--remove-orphans"], "docker_failed", "stopping Tico Meet")
            self.firewall(meet=False)
            self.run([str(self.host / FIREWALL_SCRIPT)], "firewall_failed", "running the cell's firewall rules")
        except CellError as e:
            return f"it is off, but taking it down failed ({e.detail}): re-run `cell init`"
        return "it stays off"

    def meet_renew(self, now: float | None = None) -> str:
        """After Caddy renews the TURN certificate, restart LiveKit (it reads it only when it starts), but only while no
        room is open, so no call is dropped. The cell updater asks every MEET_RENEW_EVERY; Caddy renews about 30 days
        before expiry, so an empty moment comes long before the old certificate runs out. Answers what it did: off,
        no_certificate, current, not_running, busy (rooms open, LiveKit did not answer, or another cell command holds
        the cell) or restarted.

        It also keeps `meet_days_left`: the days until the certificate LiveKit started with runs out (None when not
        known). A renewal that never finds an empty moment shows there long before calls fail (the updater warns)."""
        self.meet_days_left = None
        meet = self.meet()
        if not meet:
            return "off"
        now = time.time() if now is None else now
        cert = self.meet_cert(meet["domain"])
        try:
            issued = cert.stat().st_mtime
        except OSError:
            return "no_certificate"  # not issued yet: LiveKit restarts by itself until it is (restart: unless-stopped)
        r = self.system.run(["docker", "inspect", "--format", "{{.State.StartedAt}}", MEET_CONTAINER])
        started = docker_time(r.stdout) if r.returncode == 0 else None
        if started is None:
            return "not_running"
        # LiveKit runs on the file's certificate when it started after the file was written; after a renewal it still
        # runs on the one before, whose expiry was kept while it was the file's.
        kept = self.root / MEET_STARTED_CERT
        if issued <= started:
            not_after = cert_not_after(cert)
            if not_after is not None:
                _write_atomic(kept, json.dumps({"started": started, "not_after": not_after}) + "\n", 0o600)
        else:
            try:
                record = json.loads(kept.read_text())
                not_after = float(record["not_after"]) if record.get("started") == started else None
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                not_after = None
        if not_after is not None:
            self.meet_days_left = (not_after - now) / 86400
        if issued <= started:
            return "current"
        if self.system.meet_rooms(meet_token(meet["key"], meet["secret"], now)) != 0:
            return "busy"
        try:
            with self.lock("cell", timeout=0):
                self.run([*self.edge(), "restart", "livekit"], "docker_failed", "restarting LiveKit")
        except CellError as e:
            if e.code == "busy":
                return "busy"
            raise
        return "restarted"

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

    def firewall(self, meet: bool = False) -> bool:
        """The firewall script, its unit and Docker's drop-in. With `meet`, the script also keeps LiveKit's API port
        to loopback and the companies (MEET_INPUT, INPUT's first three rules); without, it removes those rules."""
        rule = " ".join(IMDS_RULE[1:])
        drop_meet = (
            f"iptables -S INPUT | grep -e ' --comment {MEET_TAG} ' | sed 's/^-A /-D /' | "
            "while read -r rule; do iptables $rule; done"
        )
        meet_lines = (
            [
                "# Tico Meet: LiveKit's API and signal port answers this host and its companies' networks only.",
                "want='" + "\n".join(MEET_INPUT) + "'",
                "have=$(iptables -S INPUT | grep -m3 '^-A INPUT ' || true)",
                'if [ "$have" != "$want" ]; then',
                "  " + drop_meet,
                *(f"  iptables -I INPUT {i} {line[len('-A INPUT ') :]}" for i, line in enumerate(MEET_INPUT, 1)),
                "fi",
            ]
            if meet
            else ["# Tico Meet is off: none of its rules.", drop_meet]
        )
        script = (
            "\n".join(
                [
                    "#!/bin/sh",
                    "# Written by `python3 -m setup cell init`: containers on this host never reach the EC2 metadata service,",
                    "# so no company can use the host's AWS role. Run before Docker starts and again after (docs/cell.md).",
                    "# The rule must be the chain's first: a rule above it that accepts would be reached first.",
                    "set -eu",
                    "iptables -N DOCKER-USER 2>/dev/null || true",
                    "first=$(iptables -S DOCKER-USER | grep -m1 '^-A DOCKER-USER ' || true)",
                    f"[ \"$first\" = \"{IMDS_LINE}\" ] || iptables -I DOCKER-USER 1 {rule}",
                    *meet_lines,
                ]
            )
            + "\n"
        )
        unit = (
            "\n".join(
                [
                    "[Unit]",
                    "Description=Tico cell: containers cannot reach the EC2 metadata service",
                    "Before=docker.service",
                    "After=network-pre.target",
                    "",
                    "[Service]",
                    "Type=oneshot",
                    "RemainAfterExit=yes",
                    f"ExecStart=/{FIREWALL_SCRIPT}",
                    "",
                    "[Install]",
                    "WantedBy=multi-user.target",
                ]
            )
            + "\n"
        )
        dropin = "\n".join(["[Service]", f"ExecStartPost=/{FIREWALL_SCRIPT}"]) + "\n"
        changed = _write_atomic(self.host / FIREWALL_SCRIPT, script, 0o755)
        changed = _write_atomic(self.host / FIREWALL_UNIT, unit) or changed
        changed = _write_atomic(self.host / DOCKER_DROPIN, dropin) or changed
        if changed:
            self.run(["systemctl", "daemon-reload"], "firewall_failed", "systemctl daemon-reload")
            self.run(
                ["systemctl", "enable", "tico-cell-firewall.service"], "firewall_failed", "enabling the firewall unit"
            )
        return changed

    def add(self, args) -> dict:
        started = self.system.monotonic()
        slug = check_slug(args.company, new=True)
        cell = self.load_cell()
        meet = self.meet()
        domain = (args.domain or "").strip().lower()
        if args.own_domain:
            domain = check_host(domain, "--domain", cell["domain"])
        elif domain != f"{slug}.{cell['domain']}":
            raise CellError(
                "bad_domain",
                f"--domain must be {slug}.{cell['domain']} (or, moving an install of its own, pass --own-domain)",
            )
        aliases = None
        if args.alias is not None:  # given: replaces the saved list (--alias "" alone clears it)
            aliases = [check_host(a, "--alias", cell["domain"]) for a in args.alias if a.strip()]
            if len(aliases) > MAX_ALIASES:
                raise CellError("bad_args", f"--alias is given at most {MAX_ALIASES} times")
            if len(set(aliases)) != len(aliases):
                raise CellError("bad_domain", "--alias names the same host twice")
        page_host = None  # not given: kept from the record; "" clears it
        if args.page_host is not None:
            page_host = check_host(args.page_host, "--page-host", cell["domain"]) if args.page_host.strip() else ""
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
        sleep_minutes = (
            check_sleep_minutes(args.sleep_minutes, "--sleep-minutes") if args.sleep_minutes is not None else None
        )
        backup_given = [args.backup_url, args.backup_region, args.backup_credentials_file]
        if any(backup_given) and not all(backup_given):
            raise CellError("bad_args", "--backup-url, --backup-region and --backup-credentials-file go together")
        if args.backup_url and not re.fullmatch(
            r"s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9](/[A-Za-z0-9._/-]*)?", args.backup_url
        ):
            raise CellError("bad_args", "--backup-url is s3://bucket/prefix")
        if args.backup_region:
            from .aws import REGION_RE

            if not REGION_RE.fullmatch(args.backup_region):
                raise CellError("bad_args", "--backup-region is an AWS region such as us-west-2")
        if args.company_name is not None and (
            not args.company_name.strip()
            or len(args.company_name) > 80
            or any(ord(ch) < 32 for ch in args.company_name)
        ):
            raise CellError("bad_args", "--company-name is 1 to 80 characters on one line")

        client_secret = read_secret(args.client_secret_file, "--client-secret-file")
        self.secrets.append(client_secret)
        invite = read_secret(args.gateway_invite_file, "--gateway-invite-file") if args.gateway_invite_file else None
        if invite is not None:
            self.secrets.append(invite)
            if not st.INVITE_RE.fullmatch(invite):
                raise CellError(
                    "secret_file", "--gateway-invite-file: not an invite code (8 to 200 letters, digits, - and _)"
                )
        keys = None
        if args.backup_credentials_file:
            keys = backup_keys(read_secret(args.backup_credentials_file, "--backup-credentials-file"))
            self.secrets.append(keys[1])
        kms_arn = check_kms_key(args.kms_key) if getattr(args, "kms_key", None) else None
        kms_keys = None
        if getattr(args, "kms_credentials_file", None):
            kms_keys = backup_keys(
                read_secret(args.kms_credentials_file, "--kms-credentials-file"), "--kms-credentials-file"
            )
            self.secrets.append(kms_keys[1])
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
            if self.load(slug) is None:
                # A new company's stored secrets are on KMS from its first one: never a key file a backup could carry.
                missing = [
                    flag
                    for flag, given in (
                        ("--kms-key", kms_arn or cell.get("kms_key")),
                        ("--kms-credentials-file", kms_keys),
                    )
                    if not given
                ]
                if missing:
                    raise CellError(
                        "kms_required",
                        f"a new company needs KMS for its stored secrets: give {' and '.join(missing)} "
                        "(docs/cell.md, Credentials and KMS)",
                    )
            # Unpacked under this company's lock only (it can take minutes), then renamed into place under the cell's.
            staged = self.stage_import(args.import_data, self.dir(slug)) if args.import_data else None
            if staged:
                cleanup.callback(self.drop_import, staged)  # whatever is left of it, once this is done
            with self.lock("cell"):
                if not self.imds_blocked():
                    raise CellError(
                        "not_initialized",
                        "containers on this host can reach the EC2 metadata service "
                        "(the DOCKER-USER block is missing or not first); run "
                        "`python3 -m setup cell init`",
                    )
                old = self.load(slug)
                if old and args.version and args.version != old["version"]:
                    # A new release migrates the database: that is `update`, with its snapshot and rollback.
                    raise CellError(
                        "use_update",
                        f"{slug} runs {old['version']}; change its release with `python3 -m "
                        f"setup cell update --company {slug} --version {args.version}`",
                    )
                aliases = (old or {}).get("aliases", []) if aliases is None else aliases
                if domain in aliases:
                    raise CellError("bad_domain", f"--alias {domain} is the company's --domain")
                host = (old or {}).get("page_host", "") if page_host is None else page_host
                if host in {domain, *aliases} and host:
                    raise CellError("bad_domain", f"--page-host {host} is one of the company's own hosts")
                self.refuse_taken(slug, {domain, *aliases, *([host] if host else [])})
                index = old["index"] if old else self.allocate()
                d = self.dir(slug)
                old_env = envfile.parse(_read(d / ".env"))
                c = {
                    "slug": slug,
                    "domain": domain,
                    "index": index,
                    "port": PORT_BASE + index,
                    "memory": memory or (old or {}).get("memory") or MEMORY_DEFAULT,
                    "version": args.version or (old or {}).get("version") or self.default_version(),
                    "hosted": args.hosted or (old or {}).get("hosted") or HOSTED_DEFAULT,
                    "created_at": (old or {}).get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "state": "running",
                }
                if aliases:
                    c["aliases"] = aliases
                if host:
                    c["page_host"] = host
                sleep_minutes = sleep_minutes if sleep_minutes is not None else (old or {}).get("sleep_minutes")
                if sleep_minutes is not None:
                    c["sleep_minutes"] = sleep_minutes
                if (old or {}).get("slack"):  # set only by `set-slack`; add keeps it
                    c["slack"] = True
                if (old or {}).get("pending"):  # an update not yet seen healthy: this start is still guarded by it
                    c["pending"] = old["pending"]
                if plan is None and (d / contract.COMPANY_FILE_DIR / contract.COMPANY_FILE_NAME).exists():
                    data = (d / contract.COMPANY_FILE_DIR / contract.COMPANY_FILE_NAME).read_text().strip()
                    plan = (data, old_env.get("TICO_TEAM_NAME", ""))
                name = (
                    (plan[1] if plan else "")
                    or args.company_name
                    or old_env.get("TICO_TEAM_NAME")
                    or slug.replace("-", " ").title()
                )
                s = st.Settings(
                    target="local",
                    domain=domain,
                    front_door="caddy",
                    updater=False,
                    company=name,
                    owner_email=email,
                    auth="oidc",
                    issuer=issuer,
                    client_id=args.client_id,
                    client_secret=client_secret,
                    signin_label=label,
                    hosted=c["hosted"],
                    hosted_zone=cell["domain"],
                    preset="recommended",
                    support="on",
                    gateway_invite=old_env.get("TICO_GATEWAY_INVITE", "") if invite is None else invite,
                    tag=c["version"],
                    company_file_data=plan[0] if plan else "",
                    owner_link_hash=old_env.get("TICO_OWNER_LINK_HASH", ""),
                )
                if keys:
                    s.backup_url, s.backup_region, (s.backup_key_id, s.backup_secret) = (
                        args.backup_url,
                        args.backup_region,
                        keys,
                    )
                elif old_env.get("TICO_BACKUP_URL"):
                    s.backup_url, s.backup_region = old_env["TICO_BACKUP_URL"], old_env.get("TICO_BACKUP_REGION", "")
                    s.backup_key_id = old_env.get("LITESTREAM_ACCESS_KEY_ID", "")
                    s.backup_secret = old_env.get("LITESTREAM_SECRET_ACCESS_KEY", "")
                self.secrets += [x for x in s.secrets() if x not in self.secrets]
                n = networks(index)
                s.trusted_proxies = f"127.0.0.1/32,::1/128,{n['default_gateway']}/32,{n['edge_gateway']}/32"
                s.client_ip_header = "cf-connecting-ip"  # Cloudflare sets it at the edge; the router passes it on
                s.edge_subnet, s.edge_dynamic_range = n["edge_subnet"], n["edge_range"]
                s.caddy_address, s.cloudflared_address = n["caddy_address"], n["cloudflared_address"]
                d.mkdir(parents=True, exist_ok=True)
                os.chmod(d, 0o700)
                if host:
                    # The server writes its release folders here (as its own user); the router reads them, so they are
                    # world-readable, and the folder is kept when the host is cleared.
                    pages = self.pages_dir(slug)
                    pages.mkdir(parents=True, exist_ok=True)
                    os.chmod(pages, 0o755)
                    self.system.chown(pages, SERVER_UID, SERVER_UID)
                if not s.owner_link_hash:
                    s.owner_link_hash = hosted.new_link(s, d / "owner-link.txt")
                try:
                    env = s.to_env()
                except MissingInput as e:
                    raise CellError("bad_args", self.scrub(str(e))) from None
                env.update(
                    TICO_PORT=str(c["port"]),
                    COMPOSE_PROFILES="",
                    COMPOSE_PROJECT_NAME=project(slug),
                    TICO_COMPANY_FILE_DIR=str(d / contract.COMPANY_FILE_DIR),
                )
                if host:
                    env.update(TICO_READER_ORIGIN=f"https://{host}", TICO_COMPANY_PAGE_DIR=PAGE_MOUNT)
                env.update(self.kms_values(slug, old_env, kms_arn, kms_keys, cell.get("kms_key")))
                env = cell_env(env, c, meet)
                # Carried settings fill only what the cell leaves unset: a key it writes with a value is its own.
                written = {k for k, v in env.items() if v}
                extra = self.extra_env(slug) if carried is None else carried
                ignored = sorted(k for k in extra if k in written or cell_owned(k))
                extra = {k: v for k, v in extra.items() if v and k not in ignored}
                env.update(extra)
                refuse_server_host(host, env, "--page-host")
                changed = False
                if carried is not None:
                    changed = _write_atomic(
                        d / "extra.env",
                        "# Carried from the install this company moved from "
                        "(`python3 -m setup cell add --env-file`). Keep it private (0600).\n"
                        + envfile.render(extra).split("\n", 1)[1],
                        0o600,
                    )
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
            return {**answer, **kms_answer(env)}

    def kms_values(
        self,
        slug: str,
        saved: dict[str, str],
        arn: str | None,
        keys: tuple[str, str] | None,
        default_arn: str | None = None,
    ) -> dict[str, str]:
        """The KMS lines for a company's .env: the flags', each one left out kept from `saved` (its .env now), the key
        last from `init --kms-key` (`default_arn`). Without both a key and an access key, what was saved, as it was: a
        company made before KMS keeps running as it did (its answer warns, and `rewrap` refuses). The saved secret
        joins the scrubbed ones."""
        old = {k: saved[k] for k in KMS_ENV if saved.get(k)}
        if old.get(KMS_SECRET) and old[KMS_SECRET] not in self.secrets:
            self.secrets.append(old[KMS_SECRET])
        arn = arn or old.get(KMS_KEY) or default_arn
        if keys is None and old.get(KMS_KEY_ID) and old.get(KMS_SECRET):
            keys = (old[KMS_KEY_ID], old[KMS_SECRET])
        if not (arn and keys):
            return old
        return kms_env(slug, check_kms_key(arn, KMS_KEY), keys)

    def extra_env(self, slug: str) -> dict[str, str]:
        """extra.env (settings carried from a moved install), minus anything the cell owns; its values are secrets."""
        values = {
            k: v for k, v in envfile.parse(_read(self.dir(slug) / "extra.env")).items() if v and not cell_owned(k)
        }
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
            shutil.rmtree(staging, ignore_errors=True)  # left by a run that died half-way
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
            data.rmdir()  # empty (stage_import checked, under the same lock); refuses if it is not
        os.rename(staged[0], data)

    def drop_import(self, staged: tuple[Path, int, bool]) -> None:
        staging, _, created = staged
        shutil.rmtree(staging, ignore_errors=True)
        if created:  # the folder was made only for the import: nothing else of the company is left
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
        changed = (
            _write_atomic(d / "compose.cell.yaml", company_override(c, data, self.pages_dir(c["slug"]))) or changed
        )
        return self.updater_token(d) or changed

    def updater_token(self, d: Path) -> bool:
        """The company's own updater token, made once: 0600 and the server's (it reads it, docker/entrypoint.sh, and
        keeps one that is there). Its updater reads it back from here, so nothing else holds a copy. True when made."""
        path = d / "control" / TOKEN_NAME
        with contextlib.suppress(OSError):
            if not path.is_symlink() and path.is_file() and path.stat().st_size >= 32:
                return False
            path.unlink()  # empty, short, or not a plain file: replaced
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
        return {
            "ok": True,
            "companies": [
                {
                    "company": c["slug"],
                    "url": f"https://{c['domain']}",
                    "aliases": c.get("aliases", []),
                    "port": c["port"],
                    "version": c["version"],
                    "memory": c["memory"],
                    "state": self.state_of(c, ps),
                    **({"page_host": c["page_host"]} if c.get("page_host") else {}),
                    **({"slack": True} if c.get("slack") else {}),
                }
                for c in self.companies()
            ],
        }

    def status(self, args) -> dict:
        c = self.company(args.company)
        rows = self.labelled(c["slug"])
        ps = {s: row for (s, svc), row in rows.items() if svc == SERVER_SERVICE}
        state = self.state_of(c, ps)
        # A sleeping company is not probed: through the router, that would wake it.
        healthy = (
            state not in ("removed", "sleeping") and self.system.http_status(c["domain"], contract.HEALTH_PATH) == 200
        )
        return {
            "ok": True,
            "company": c["slug"],
            "url": f"https://{c['domain']}",
            "aliases": c.get("aliases", []),
            "port": c["port"],
            "version": c["version"],
            "memory": c["memory"],
            "state": state,
            "healthy": healthy,
            "container": ps.get(c["slug"], {"state": "", "health": ""}),
            **({"page_host": c["page_host"], "page_url": f"https://{c['page_host']}"} if c.get("page_host") else {}),
            **(
                {"slack": True, "slack_container": rows.get((c["slug"], SLACK_SERVICE), {"state": "", "health": ""})}
                if c.get("slack")
                else {}
            ),
        }

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
            _set_wake_at(c, None)  # no longer asleep, so no timed wake
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
            route = c.get("state") != "running"  # stopped (503) or sleeping (the wake service): to the server now
            c["state"] = "running"
            _set_wake_at(c, None)
            self.save(c)
            if route:
                self.routed()  # the router holds requests until the server answers
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

    def wake_company(self, slug: str, timeout: int, started: float | None = None, due=None) -> tuple[dict, int, bool]:
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
                    self.system.run(self.compose(slug, "stop", *self.services(slug)))
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

    def refuse_taken(self, slug: str, mine: set[str]) -> None:
        """domain_taken when another company or a route already answers on one of `mine`, or it is Tico Meet's TURN host
        (a DNS-only record to this host, not the tunnel)."""
        meet = self.saved_cell().get("meet_domain")
        if meet and meet in mine:
            raise CellError("domain_taken", f"{meet} is this cell's Tico Meet host")
        for other in self.live_companies():
            clash = sorted(mine & set(hosts_of(other))) if other["slug"] != slug else []
            if clash:
                raise CellError("domain_taken", f"{clash[0]} belongs to {other['slug']}")
        for r in self.routes():
            clash = sorted(mine & set(r["hosts"]))
            if clash:
                raise CellError("domain_taken", f"{clash[0]} belongs to the route {r['name']}")

    def set_page_host(self, args) -> dict:
        """A running company's page host (docs/company-page.md), or "" to clear it: what `add --page-host` sets, without
        add's sign-in settings, so staff can turn on the page for a company that already runs. Its settings are
        rewritten, its server restarts and the host is routed; a cleared host keeps the folder. The record carries
        `page_pending` until the restarted server answers, so a run that failed is redone in full when given again; a
        settled one only re-writes its routes."""
        started = self.system.monotonic()
        slug = check_slug(args.company)
        cell = self.load_cell()
        host = check_host(args.host, "--host", cell["domain"]) if (args.host or "").strip() else ""
        with self.lock("company-" + slug):
            c = self.live(slug)
            if c.get("state") != "running":
                raise CellError("not_running", f"{slug} is {c.get('state')}; start it before giving it a page host")
            if host and host in {c["domain"], *c.get("aliases", [])}:
                raise CellError("bad_domain", f"--host {host} is one of the company's own hosts")
            if host == c.get("page_host", "") and not c.get("page_pending"):
                with self.lock("cell"):
                    routed = [self.write_router(), self.write_tunnel()]
                return {"ok": True, "company": slug, "page_host": host or None, "changed": any(routed)}
            d = self.dir(slug)
            env = envfile.parse(_read(d / ".env"))
            self.secrets += [v for k, v in env.items() if k in SECRET_ENV_KEYS and v and v not in self.secrets]
            refuse_server_host(host, env, "--host")
            with self.lock("cell"):  # the host's owner is checked and recorded under one lock, as add does
                self.refuse_taken(slug, {host} if host else set())
                if host:
                    pages = self.pages_dir(slug)
                    pages.mkdir(parents=True, exist_ok=True)
                    os.chmod(pages, 0o755)
                    self.system.chown(pages, SERVER_UID, SERVER_UID)
                    c["page_host"] = host
                    env.update(TICO_READER_ORIGIN=f"https://{host}", TICO_COMPANY_PAGE_DIR=PAGE_MOUNT)
                else:
                    c.pop("page_host", None)
                    env.pop("TICO_READER_ORIGIN", None)
                    env.pop("TICO_COMPANY_PAGE_DIR", None)
                c["page_pending"] = True
                self.write_company(c, envfile.render(env), "")
                self.save(c)

            def route():
                with self.lock("cell"):
                    self.write_router()
                    self.write_tunnel()

            ready = self.launch(c, started, args.timeout, route)
            c.pop("page_pending", None)
            self.save(c)
            return {**self.answer(c, ready, True), "page_host": host or None}

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

    def set_slack(self, args) -> dict:
        """The company's Slack gateway on or off (docs/slack.md), kept in its record so every later `add` and `update`
        writes it again. On: COMPOSE_PROFILES=slack and the gateway's block in compose.cell.yaml; a running company
        starts it (its server's own config does not change, so the server is not recreated), and a stopped or sleeping
        one starts it with its server. Off: the gateway's container is stopped and removed first (a failure changes
        nothing), then the files are written as for any other company. Asked again, it only redoes what differs."""
        started = self.system.monotonic()
        slug = check_slug(args.company)
        on = args.state == "on"
        with self.lock("company-" + slug):
            c = self.live(slug)
            d = self.dir(slug)
            env_text = _read(d / ".env")
            self.secrets += [v for k, v in envfile.parse(env_text).items() if k in SECRET_ENV_KEYS and v]
            if not on:
                # Named with its profile, so Compose finds it whatever .env says now.
                self.run(
                    self.compose(slug, "--profile", SLACK_SERVICE, "rm", "--stop", "--force", SLACK_SERVICE),
                    "docker_failed",
                    "removing the Slack gateway",
                )
            changed = bool(c.get("slack")) != on
            if on:
                c["slack"] = True
            else:
                c.pop("slack", None)
            files = self.write_compose(c)
            env = _with_tag(env_text, c["version"], self.extra_env(slug), c, self.meet())
            files = _write_atomic(d / ".env", env, 0o600) or files
            if changed:
                self.save(c)
            ready = 0
            if on and c.get("state") == "running":
                gateway = self.containers(slug, SLACK_SERVICE).get(slug, {}).get("state")
                if files or gateway != "running":
                    ready = self.launch(c, started, args.timeout)
                    changed = True
        return {"ok": True, "company": slug, "slack": on, "changed": changed or files, "ready_ms": ready}

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
        recreated only when one changed (`changed`): a company made by an older core picks up its updater that way.

        --kms-key and --kms-credentials-file put the company's stored secrets on KMS (`kms_values`); left out, the
        saved ones stay. The answer says whether KMS is set (`kms_answer`)."""
        answer = self._update(args)
        self.prune_quietly()  # every release leaves an image and a core folder behind; outside the company's lock
        return {**answer, **kms_answer(envfile.parse(_read(self.dir(answer["company"]) / ".env")))}

    def _update(self, args) -> dict:
        started = self.system.monotonic()
        if not VERSION_RE.fullmatch(args.version or ""):
            raise CellError("bad_args", "--version is a release such as v1.2.3")
        slug = check_slug(args.company)
        kms_arn = check_kms_key(args.kms_key) if getattr(args, "kms_key", None) else None
        kms_keys = None
        if getattr(args, "kms_credentials_file", None):
            kms_keys = backup_keys(
                read_secret(args.kms_credentials_file, "--kms-credentials-file"), "--kms-credentials-file"
            )
            self.secrets.append(kms_keys[1])
        default_arn = self.load_cell().get("kms_key") if kms_keys and not kms_arn else None
        self.taken = ""
        with self.lock("company-" + slug):
            c = self.live(slug)
            d = self.dir(slug)
            env_text = _read(d / ".env")
            self.secrets += [v for k, v in envfile.parse(env_text).items() if k in SECRET_ENV_KEYS and v]
            if kms_arn or kms_keys:
                values = envfile.parse(env_text)
                values.update(self.kms_values(slug, values, kms_arn, kms_keys, default_arn))
                if not kms_set(values):
                    raise CellError(
                        "kms_required",
                        "KMS needs both a key and the company's access key: give "
                        + ("--kms-credentials-file" if kms_arn else "--kms-key")
                        + " too (none is saved)",
                    )
                env_text = envfile.render(values)
            extra = self.extra_env(slug)
            meet = self.meet()  # before anything stops: a cell whose Tico Meet keys are missing changes nothing
            running = c.get("state") not in ("stopped", "sleeping")  # one that is not stays as it is
            pending = c.get("pending")
            self.taken = (pending or {}).get("snapshot", "")
            if c["version"] == args.version:
                changed = self.write_compose(c)
                changed = _write_atomic(d / ".env", _with_tag(env_text, c["version"], extra, c, meet), 0o600) or changed
                if running and (pending or changed):
                    ready = self.launch(c, started, args.timeout)
                else:
                    ready = self.wait_healthy(c, started, args.timeout) if running else 0
                return self.answer(c, ready, changed)
            if not pending and self.short_of_space(slug):
                self.prune_quietly({args.version})  # room for the snapshot, made while the company still serves
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
                _write_atomic(d / ".env", _with_tag(env_text, args.version, extra, c, meet), 0o600)
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
            err.detail += (
                f"; still on {c['version']}"
                if not r.returncode
                else f"; starting {c['version']} again failed too: " + (r.stderr or r.stdout).strip()[-300:]
            )
        err.extra = {"version": c["version"]}
        return err

    def short_of_space(self, slug: str) -> int:
        """Bytes a snapshot of the company's database would still need on this disk (0: it fits with SNAPSHOT_SPARE)."""
        data = self.dir(slug) / "data"
        size = sum(os.lstat(data / n).st_size for n in DB_FILES if os.path.isfile(data / n))
        with contextlib.suppress(OSError):
            return max(0, size + SNAPSHOT_SPARE - self.system.free_bytes(str(data)))
        return 0

    def snapshot(self, slug: str, version: str) -> str:
        """The stopped server's database, before a new release migrates it, in snapshots/<name>; the name, or "" when
        there is no database yet. The last SNAPSHOTS_KEPT are kept, the oldest removed before the copy so the disk
        never holds one more. A copy that would leave less than SNAPSHOT_SPARE free is refused as disk_full before a
        byte is copied (`update` has `prune` make room before it stops the server).
        data/ belongs to the server, and its updater lets the company itself start this, so only plain files are
        copied: a symlink there would have root copy a host file into snapshots/, and a rollback would hand it to the
        server."""
        data, snaps = self.dir(slug) / "data", self.dir(slug) / "snapshots"
        found = [n for n in DB_FILES if os.path.lexists(data / n)]
        if "hub.sqlite" not in found:
            return ""
        if any(not stat.S_ISREG(os.lstat(data / n).st_mode) for n in found):
            raise CellError(
                "snapshot_failed",
                "the database in data/ is not plain files (a symlink or other special file); nothing was copied",
            )
        older = sorted(p for p in snaps.iterdir() if p.is_dir()) if snaps.is_dir() else []
        for old in older[: max(0, len(older) - (SNAPSHOTS_KEPT - 1))]:
            shutil.rmtree(old, ignore_errors=True)
        short = self.short_of_space(slug)
        if short:
            raise CellError(
                "disk_full",
                f"copying the database needs {short / 1e9:.1f} GB more free space on this host; nothing was copied. "
                "Free space on the host or grow its disk",
            )
        dest = snaps / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{version}"
        try:
            dest.mkdir(parents=True, exist_ok=True, mode=0o700)
            for name in found:
                shutil.copy2(data / name, dest / name, follow_symlinks=False)
        except OSError as e:
            shutil.rmtree(dest, ignore_errors=True)
            raise CellError("snapshot_failed", f"copying the database to {dest}: {e.strerror or e}") from None
        return dest.name

    # disk
    def prune(self, args=None, keep: set[str] = frozenset()) -> dict:
        """The server images and core folders (/opt/tico-cell/core-vX.Y.Z) no company here needs, removed: every
        release leaves both behind, and nothing else ever removes them. Needed: each company's release and the one its
        pending update would roll back to, `keep`, and any release newer than all of those (an update on its way in).
        A core folder also stays while it is the one running this, one `init` ran from (cell.json `init_cores`: HQ runs
        its commands from the core it installed), a link's target (stats-core), one a tico-* systemd unit names, any
        process's working directory, or under FRESH_S old. An image a container uses stays (docker rmi refuses it); one
        removed between an update's pull and its start is pulled again by `up`. With no company here nothing is
        removed. One prune at a time."""
        self.load_cell()
        try:
            with self.lock("prune", timeout=0):
                in_use = {v for c in self.companies() for v in (c.get("version"), (c.get("pending") or {}).get("from"))}
                in_use |= self.updates_under_way()
                in_use = {v for v in in_use | set(keep) if isinstance(v, str) and VERSION_RE.fullmatch(v)}
                if not in_use:
                    return {"ok": True, "images_removed": 0, "cores_removed": 0}
                newest = max(_release_key(v) for v in in_use)

                def needed(v: str) -> bool:
                    return v in in_use or _release_key(v) > newest

                images, cores = self.prune_images(needed), self.prune_cores(needed)
        except CellError as e:
            if e.code != "busy":
                raise
            return {"ok": True, "busy": True}
        return {"ok": True, "images_removed": images, "cores_removed": cores}

    def updates_under_way(self) -> set[str]:
        """The releases each company's updater is moving it to right now (its updater-status.json), so an update
        between its pull or core install and its record keeps both."""
        out = set()
        for d in (self.root / "companies").glob("*/updater-status.json"):
            with contextlib.suppress(OSError, ValueError, AttributeError):
                s = json.loads(d.read_text())
                if s.get("state") in ("pulling", "restarting"):
                    out.add(s.get("to"))
        return out

    def prune_quietly(self, keep: set[str] = frozenset()) -> None:
        """`prune` where its failure must not fail the command that asked for it (update, snapshot)."""
        with contextlib.suppress(Exception):
            self.prune(keep=keep)

    def prune_images(self, needed) -> int:
        r = self.system.run(["docker", "images", SERVER_IMAGE, "--format", "{{.Tag}}"], timeout=60)
        removed, deadline = 0, self.system.monotonic() + PRUNE_S
        for tag in sorted(set(r.stdout.split()) if not r.returncode else ()):
            if VERSION_RE.fullmatch(tag) and not needed(tag) and self.system.monotonic() < deadline:
                removed += not self.system.run(["docker", "rmi", f"{SERVER_IMAGE}:{tag}"], timeout=60).returncode
        return removed

    def prune_cores(self, needed) -> int:
        base = self.host / CORES
        try:
            entries = sorted(base.iterdir())
        except OSError:
            return 0
        held = {own_core(), *(self.saved_cell().get("init_cores") or [])}
        for p in entries:
            with contextlib.suppress(OSError):
                if p.is_symlink():
                    held.add(Path(os.readlink(p)).name)
        with contextlib.suppress(OSError):
            for proc in (self.host / "proc").iterdir():
                with contextlib.suppress(OSError):
                    cwd = Path(os.readlink(proc / "cwd")).parts  # ("/", "opt", "tico-cell", "core-v…", …)
                    if cwd[1:3] == tuple(CORES.split("/")) and len(cwd) > 3:
                        held.add(cwd[3])
        with contextlib.suppress(OSError):
            for unit in [*(self.host / UNIT_DIR).glob("tico-*.service"), *(self.host / UNIT_DIR).glob("tico-*.d/*")]:
                held |= {f"core-{v}" for v in CORE_DIR_RE.findall(_read(unit))}
        now, removed = time.time(), 0
        for p in entries:
            try:
                st = p.lstat()
            except OSError:
                continue  # gone since the listing (a link swap, an installer's staging folder)
            if not stat.S_ISDIR(st.st_mode):
                continue
            age = now - st.st_mtime
            m = CORE_DIR_RE.fullmatch(p.name)
            if m and p.name not in held and age > FRESH_S and not needed(m[1]):
                gone = base / f".prune-{p.name}.{os.getpid()}"
                try:
                    os.rename(p, gone)  # gone from core-v* at once: a half-removed folder never looks installed
                except OSError:
                    continue
                shutil.rmtree(gone, ignore_errors=True)
                removed += 1
            elif p.name.startswith(".prune-") and age > FRESH_S:
                shutil.rmtree(p, ignore_errors=True)  # a removal that was interrupted
        return removed

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
            raise failed(
                f"could not stop {c['version']} to put {old} back, so its database was left as it is: {e.detail}"
            ) from None
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
                raise failed(
                    f"could not put the database from {old} back ({e}); the server is stopped on {c['version']}"
                ) from None
        env = self.dir(slug) / ".env"
        _write_atomic(env, _with_tag(_read(env), old), 0o600)
        c["version"] = old
        c.pop("pending", None)
        self.save(c)
        if not restart:
            return
        r = self.system.run(self.compose(slug, "up", "-d", "--remove-orphans"))
        if r.returncode:
            raise failed(
                f"{old} and its database are back, but starting it failed: " + (r.stderr or r.stdout).strip()[-300:]
            )
        try:
            self.wait_healthy(c, self.system.monotonic(), timeout)
        except CellError as e:
            raise failed(f"{old} and its database are back, but it does not answer: {e.detail}") from None

    def route(self, args) -> dict:
        """A route: hosts that are not a company's, sent to a loopback port on this host (docs/cell.md, Routes). `set`
        replaces the route's hosts and ports; `remove` drops it. Both rewrite the router (a reload, as `add` does) and,
        unless --router-only, the tunnel's routes, restarting the tunnel when a host the wildcard misses came or went.
        Every command that rewrites the router or tunnel keeps the routes."""
        cell = self.load_cell()
        if args.op == "list":
            return {"ok": True, "routes": self.routes()}
        if not args.name:
            raise CellError("bad_args", "--name is required")
        name = check_slug(args.name)
        path = self.root / ROUTES / f"{name}.json"
        with self.lock("cell"):
            if getattr(args, "if_router_current", False):
                # A checkout that may be older than the host's core (the demo's deploy): change nothing unless the
                # router's file is what this checkout writes, read under the same lock the rewrite takes.
                live, meet = self.live_companies(), bool(self.saved_cell().get("meet_domain"))
                if _read(self.root / "router" / "Caddyfile") not in (
                    router_config(live, meet=meet),
                    router_config(live, self.routes(), meet),
                ):
                    raise CellError(
                        "router_changed",
                        "the router's file is not what this checkout's cell writes for "
                        "these companies; not touching it",
                    )
            if args.op == "set":
                hosts = [check_host(h, "--host", cell["domain"]) for h in args.host or []]
                ports = list(args.port or [])
                if not hosts or len(hosts) > MAX_ROUTE_HOSTS or len(set(hosts)) != len(hosts):
                    raise CellError("bad_domain", f"--host: 1 to {MAX_ROUTE_HOSTS} different hostnames")
                if (
                    not ports
                    or len(ports) > MAX_ROUTE_PORTS
                    or len(set(ports)) != len(ports)
                    or any(p not in ROUTE_PORTS or p in CELL_PORTS for p in ports)
                ):
                    raise CellError(
                        "bad_args",
                        f"--port: 1 to {MAX_ROUTE_PORTS} different loopback ports from "
                        f"{ROUTE_PORTS.start} to {ROUTE_PORTS.stop - 1}, not the cell's own",
                    )
                for c in self.live_companies():
                    clash = sorted(set(hosts) & set(hosts_of(c)))
                    if clash:
                        raise CellError("domain_taken", f"{clash[0]} belongs to {c['slug']}")
                for r in self.routes():
                    clash = sorted(set(hosts) & set(r["hosts"])) if r["name"] != name else []
                    if clash:
                        raise CellError("domain_taken", f"{clash[0]} belongs to the route {r['name']}")
                record = {"name": name, "hosts": hosts, "ports": ports, "noindex": bool(args.noindex)}
                changed = _write_atomic(path, json.dumps(record, indent=2, sort_keys=True) + "\n", 0o600)
            else:
                changed = path.exists()
                path.unlink(missing_ok=True)
            routed = self.write_router()
            tunneled = False if args.router_only else self.write_tunnel()
            pending = (self.root / "tunnel.env").exists() and self.tunnel_text(
                cell["domain"], cell.get("page_domain")
            ) != _read(self.root / "tunnel" / "cloudflared.yml")
        return {
            "ok": True,
            "route": name,
            "changed": changed,
            "router": routed,
            "tunnel": tunneled,
            "tunnel_pending": pending,
        }

    def kms_policy(self, args) -> dict:
        """The IAM policy for the company's own KMS access key (`iam_policy`), for the person who makes its IAM user:
        the cell's key (--kms-key, or init's) with the company's own encryption context only. Reads nothing else."""
        slug = check_slug(args.company)
        arn = args.kms_key
        if not arn:
            arn = self.load_cell().get("kms_key")
            if not arn:
                raise CellError(
                    "bad_args", "--kms-key is required: this cell has no KMS key of its own (init --kms-key)"
                )
        arn = check_kms_key(arn)
        return {"ok": True, "company": slug, "kms_key": arn, "policy": iam_policy(slug, arn)}

    def rewrap(self, args) -> dict:
        """Take an existing company's stored secrets off its key files (docs/cell.md, Credentials and KMS), under its
        lock, in this order, each step stopping the run before the next:

        1. Its backup bucket (TICO_BACKUP_URL) must block public access, all four settings, read with the host's role:
           else public_access, naming the bucket, before anything else.
        2. Its .env must have its KMS settings (`update --kms-key --kms-credentials-file`): else kms_required.
        3. Its server (and Slack gateway) stopped, Docker saying so (asleep or stopped: already), then the backend step
           (REWRAP_STEP) in a one-off container: wrap with KMS, every secret moved to a fresh data key, then the key
           files deleted from data/. A company that was running is started again, also when the step failed. A step
           that does not answer done is rewrap_failed with its `failed` kinds and counts: nothing deleted.
        4. With the host's role, every version of the key file copies under <backup prefix>/credential-key/ deleted.
        5. With the host's role, every version of a bundle backup's key file copy (backups/**.sqlite.keys/, hand-run
           `manage.py backup`) whose SHA-256 is one of this company's key files' deleted; other copies are counted as
           kept and left alone. The SHA-256s are recorded beside .env before step 3, so a run after an interruption
           still finds them.

        Run again, each step finds its work done (the step answers already_done; nothing is left to delete)."""
        started = self.system.monotonic()
        slug = check_slug(args.company)
        with self.lock("company-" + slug):
            c = self.live(slug)
            env = envfile.parse(_read(self.dir(slug) / ".env"))
            self.secrets += [v for k, v in env.items() if k in SECRET_ENV_KEYS and v and v not in self.secrets]
            url, region = env.get("TICO_BACKUP_URL", ""), env.get("TICO_BACKUP_REGION", "")
            bucket = prefix = ""
            if url:
                m = re.fullmatch(r"s3://([^/]+)(?:/(.*))?", url)
                if not m:
                    raise CellError("bad_args", f"{slug}'s TICO_BACKUP_URL is not s3://bucket/prefix")
                bucket, prefix = m.group(1), (m.group(2) or "").strip("/")
                blocked, why = self.s3(
                    "reading the public access block of bucket " + bucket,
                    lambda: self.system.public_access_blocked(bucket, region),
                )
                if not blocked:
                    raise CellError(
                        "public_access",
                        f"{why or 'bucket ' + bucket + ' does not block public access'}; turn on all four Block Public "
                        "Access settings first. Nothing was changed",
                    )
            if not kms_set(env):
                raise CellError(
                    "kms_required",
                    f"{slug} has no KMS settings: run `update` with --kms-key and --kms-credentials-file first",
                )
            running = c.get("state") == "running"  # asleep or stopped: its server stays down after
            # The key files' SHA-256s, kept beside .env before the step deletes the files: they pick out this company's
            # copies in a bundle backup (step 5), also on a run after an interruption, when the files are gone.
            digests = self.key_digests(slug) if bucket else []
            self.pull(c["version"])
            out, failed = {}, None
            try:
                self.ensure_stopped(slug)
                step = self.compose(
                    slug,
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "-e",
                    "TICO_DB=/data/hub.sqlite",
                    "--entrypoint",
                    "python",
                    SERVER_SERVICE,
                    *REWRAP_STEP,
                )
                r = self.system.run(step, timeout=args.step_timeout)
                out = step_answer(r.stdout)
                if r.returncode or out.get("status") not in REWRAP_DONE:
                    failed = rewrap_failed(r, out)
            except CellError as e:
                failed = e
            ready = 0
            if running:
                try:
                    ready = self.launch(c, started, args.timeout)
                except CellError as e:
                    if failed is None:
                        raise
                    failed.detail += f"; starting {slug} again failed too: {e.detail}"
            if failed is not None:
                raise failed
            deleted, bundles, kept = 0, 0, 0
            if bucket:
                deleted = self.s3(
                    f"deleting the key file copies under s3://{bucket}/{prefix + '/' if prefix else ''}credential-key/",
                    lambda: self.system.delete_key_copies(bucket, prefix, region),
                )
                bundles, kept = self.s3(
                    f"deleting this company's bundle key copies under s3://{bucket}/backups/",
                    lambda: self.system.delete_bundle_key_copies(bucket, digests, region),
                )
        return {
            "ok": True,
            "company": slug,
            "public_access_blocked": True if bucket else None,
            "step": out.get("status"),
            "resealed": out.get("resealed") or {},
            "deleted": out.get("deleted") or [],
            "deleted_key_copies": deleted,
            "deleted_bundle_key_copies": bundles,
            "kept_bundle_key_copies": kept,
            "state": c.get("state"),
            "ready_ms": ready,
        }

    def key_digests(self, slug: str) -> list[str]:
        """The SHA-256s of the company's key files, now in data/ or recorded by an earlier run (KEY_DIGESTS, 0600,
        beside .env), merged and recorded again. Digests only: they open nothing."""
        from backend import replication

        path = self.dir(slug) / KEY_DIGESTS
        try:
            known = set(json.loads(_read(path) or "[]"))
        except ValueError:
            known = set()
        found = sorted(known | replication.key_file_digests(self.dir(slug) / "data"))
        if found:
            _write_atomic(path, json.dumps(found) + "\n", 0o600)
        return found

    def s3(self, what: str, call):
        """`call()` (an S3 call with the host's role); any error but our own is s3_failed, saying what it did."""
        try:
            return call()
        except CellError:
            raise
        except Exception as e:  # noqa: BLE001  botocore's errors, without importing botocore here
            raise CellError(
                "s3_failed", self.scrub(f"{what} with the host's role: {type(e).__name__}: {e}")[-600:]
            ) from None

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
                    shutil.rmtree(self.pages_dir(slug), ignore_errors=True)  # after the router stopped serving it
        return {"ok": True, "company": slug, "removed": True, "kept_data": bool(args.keep_data)}


def wake_at_text(value) -> str | None:
    """A timed wake's time as the record keeps it (WAKE_AT_FORMAT), or None when `value` is None (no timed wake).
    The time must say what zone it is in (a Z or an offset; a naive time is refused) and be at most WAKE_AT_DAYS from
    now. A time already past is kept: the wake service starts the company at its next sweep. CellError bad_wake_at
    for anything else, so the updater's POST /sleep and `sleep --wake-at` refuse the same values."""
    if value is None:
        return None
    bad = CellError(
        "bad_wake_at", f"wake_at is an ISO-8601 UTC time such as 2026-10-30T18:00:00Z, at most {WAKE_AT_DAYS} days out"
    )
    if not isinstance(value, str) or not WAKE_AT_SHAPE.fullmatch(value):  # ISO-8601 shape, not a loose parse
        raise bad
    text = value
    if text.endswith("Z"):  # fromisoformat takes an offset, not the Z (before Python 3.11)
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


def step_answer(stdout: str) -> dict:
    """The backend step's JSON object: the last line of its output that is one (log lines may come before it)."""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            with contextlib.suppress(ValueError):
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
    return {}


def rewrap_failed(r: Result, out: dict) -> CellError:
    """rewrap_failed for a step that did not answer done: its status, reason and `failed` counts (secret kinds and how
    many did not open under KMS), or the end of its output when it gave no answer. Nothing was deleted."""
    failed = out.get("failed") if isinstance(out.get("failed"), dict) else {}
    counts = ", ".join(f"{kind}: {n}" for kind, n in sorted(failed.items()))
    reason = str(out.get("reason") or "").strip() or (r.stderr or r.stdout or "").strip()[-400:]
    e = CellError(
        "rewrap_failed",
        f"the rewrap step {out.get('status') or 'failed'} (exit {r.returncode})"
        + (f": {reason}" if reason else "")
        + (f"; secrets that do not open under KMS: {counts}" if counts else "")
        + "; nothing was deleted",
    )
    e.extra = {"step": out.get("status"), "exit": r.returncode, "failed": failed}
    return e


def kms_answer(env: dict[str, str]) -> dict:
    """What an `add` or `update` answer says of the company's KMS settings (never the access key)."""
    if kms_set(env):
        return {"kms": True, "kms_key": env[KMS_KEY]}
    return {
        "kms": False,
        "warning": "KMS is not set: the company's stored secrets are opened by a key file that its backups carry; "
        "give it --kms-key and --kms-credentials-file with `update`, then run `rewrap`",
    }


def own_core() -> str:
    """The name of the core folder this runs from (core-vX.Y.Z on a cell host)."""
    return Path(__file__).resolve().parent.parent.name


def _release_key(v: str) -> tuple:
    """v1.2.3 above v1.2.3-rc.9 above v1.2.2 (VERSION_RE's form)."""
    core, _, pre = v[1:].partition("-")
    return (*(int(x) for x in core.split(".")), not pre, pre)


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _with_tag(
    env_text: str, version: str, extra: dict[str, str] | None = None, c: dict | None = None, meet: dict | None = None
) -> str:
    """The same .env with another release, written exactly as `add` would write it (carried settings fill only keys
    the .env leaves unset, as in `add`; with `c`, the cell's updater and Tico Meet keys as `add` writes them)."""
    values = {**(extra or {}), **envfile.parse(env_text), "TICO_TAG": version}
    return envfile.render(cell_env(values, c, meet) if c else values)


def main(
    argv: list[str],
    *,
    system: System | None = None,
    root: str | Path | None = None,
    host_root: str | Path = "/",
    compose_source: Path | None = None,
    out=None,
) -> int:
    out = out or sys.stdout
    cell = Cell(
        Path(root or os.environ.get("TICO_CELL_ROOT") or ROOT), system or System(), Path(host_root), compose_source
    )
    try:
        args = parser().parse_args(argv)
        result = getattr(cell, args.action.replace("-", "_"))(args)  # set-sleep is set_sleep
    except CellError as e:
        result = {"ok": False, "error": e.code, "detail": cell.scrub(e.detail), **e.extra}
    except Exception as e:  # noqa: BLE001  an answer HQ can read, even for a bug; the secrets scrubbed out of it
        result = {"ok": False, "error": "internal", "detail": cell.scrub(f"{type(e).__name__}: {e}")[-600:]}
    out.write(json.dumps(result) + "\n")
    out.flush()
    return 0 if result.get("ok") else 1
