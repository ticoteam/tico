"""Helpers that other tests share, moved out of test_cell.py so they import no test file."""

import io
import json
import os
import re
import time
from pathlib import Path
import pytest
from setup import cell, envfile
from setup.cell import Result


ROOT = Path(__file__).resolve().parents[3]


SECRET = "oidc-secret-" + "s" * 40


def utc_in(seconds):
    """A wake time `seconds` from now, as a request or `sleep --wake-at` writes it (2026-10-30T18:00:00Z)."""
    return time.strftime(cell.WAKE_AT_FORMAT, time.gmtime(time.time() + seconds))


class FakeSystem:
    """Records every call; `docker compose up` makes a company's container running and healthy, as Docker would, and
    its Slack gateway's too when its .env's COMPOSE_PROFILES names slack."""

    def __init__(self):
        self.calls, self.chowns, self.t = [], [], 0.0
        self.ps = {}  # slug -> (State, Status)
        self.slack = {}  # slug -> (State, Status) of its Slack gateway's container
        self.images = set()
        self.health = {}  # host -> status; default 200 while its container runs
        self.busy_ports, self.fail = set(), {}
        self.imds = True  # the metadata block is DOCKER-USER's first rule
        self.docker_user = None  # or the chain's rules as `iptables -S` prints them
        self.release = "v1.2.3"
        self.hosts = {}  # host -> slug, for a host that is not <slug>.<anything>
        self.unpublished = set()  # tags GitHub has no release for; None: GitHub does not answer
        self.probes = []  # (host, port) of each health probe
        self.livekit_started = None  # the cell LiveKit's {{.State.StartedAt}}; None: no such container
        self.rooms = 0  # what ListRooms answers (a count), or None when LiveKit does not answer
        self.tokens = []  # each token ListRooms was asked with
        self.input = ["-A INPUT -i lo -j ACCEPT"]  # INPUT as `iptables -S` prints it; the firewall script edits it
        self.firewall_works = True  # False: running the firewall script leaves INPUT as it is
        self.meet_cert = True  # `up` of an edge project with Tico Meet: the certificate Caddy gets its certificate
        # py-spy: what `py-spy --version` says when no file is at its path (the pinned one; None: not installed), what a
        # download of each url answers (missing: it fails), and the machine type
        self.py_spy, self.downloads, self.arch = cell.PY_SPY_VERSION, {}, "aarch64"

    def run(self, argv, *, input=None, timeout=900):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        for key, res in self.fail.items():
            if key in joined:
                return res
        if argv[0].endswith("/py-spy") and argv[1:] == ["--version"]:
            if Path(argv[0]).exists():  # an installed fake binary holds what it prints
                return Result(0, Path(argv[0]).read_text())
            return Result(0, f"py-spy {self.py_spy}\n") if self.py_spy else Result(127, "", "not found")
        if argv[:3] == ["iptables", "-S", "INPUT"]:
            return Result(0, "-P INPUT ACCEPT\n" + "".join(r + "\n" for r in self.input))
        if argv and argv[0].endswith(cell.FIREWALL_SCRIPT) and self.firewall_works:
            mine = [r for r in self.input if f" --comment {cell.MEET_TAG} " not in r]
            self.input = (cell.MEET_INPUT if "want='" in Path(argv[0]).read_text() else []) + mine
            return Result(0)
        if argv[:2] == ["iptables", "-S"]:
            rules = (
                self.docker_user
                if self.docker_user is not None
                else ([cell.IMDS_LINE] if self.imds else []) + ["-A DOCKER-USER -j RETURN"]
            )
            return Result(0, "-N DOCKER-USER\n" + "".join(r + "\n" for r in rules))
        if argv[:2] == ["docker", "pull"]:
            self.images.add(argv[-1])
            return Result(0)
        if argv[:2] == ["docker", "ps"]:
            flt = argv[argv.index("--filter") + 1]
            want = flt.split("=", 2)[2] if flt.count("=") == 2 else ""
            return Result(
                0,
                "".join(
                    json.dumps(
                        {
                            "Labels": f"com.docker.compose.service={service},{cell.LABEL}={s}",
                            "State": state,
                            "Status": status,
                        }
                    )
                    + "\n"
                    # The gateway's row after the server's: a reader keyed by slug alone would take the gateway's.
                    for service, rows in (("server", self.ps), ("slack", self.slack))
                    for s, (state, status) in rows.items()
                    if not want or s == want
                ),
            )
        if argv[:2] == ["docker", "inspect"] and argv[-1] == cell.MEET_CONTAINER:
            if self.livekit_started is None:
                return Result(1, "", f"Error: No such object: {cell.MEET_CONTAINER}")
            return Result(0, self.livekit_started + "\n")
        if argv[:3] == ["docker", "image", "inspect"]:
            return Result(0 if argv[-1] in self.images else 1)
        if "--project-name" in argv:
            proj = argv[argv.index("--project-name") + 1]
            slug = proj[len(cell.PROJECT_PREFIX) :]
            if proj == cell.EDGE_PROJECT:
                edge = Path(argv[argv.index("-f") + 1])
                domain = re.search(r"TICO_MEET_DOMAIN: (\S+)", edge.read_text()) if edge.exists() else None
                if "up" in argv and domain and self.meet_cert:
                    crt = edge.parent / cell.MEET_DIR / "certs" / (cell.MEET_CERT_PATH.format(d=domain[1]) + ".crt")
                    crt.parent.mkdir(parents=True, exist_ok=True)
                    crt.write_text("cert")
                return Result(0)
            env = Path(argv[argv.index("--env-file") + 1]) if "--env-file" in argv else None
            profiles = envfile.parse(env.read_text()).get("COMPOSE_PROFILES", "") if env and env.exists() else ""
            if "up" in argv:
                self.ps[slug] = ("running", "Up 2 seconds (healthy)")
                if "slack" in profiles.split(","):
                    self.slack[slug] = ("running", "Up 2 seconds")  # its health check is off
            elif "rm" in argv:
                if "slack" in argv:
                    self.slack.pop(slug, None)
            elif "stop" in argv:
                self.ps[slug] = ("exited", "Exited (0) 1 second ago")
                if "slack" in argv and slug in self.slack:
                    self.slack[slug] = ("exited", "Exited (0) 1 second ago")
            elif "down" in argv:
                self.ps.pop(slug, None)
                self.slack.pop(slug, None)
        return Result(0)

    def http_status(self, host, path, timeout=3, port=cell.ROUTER_PORT):
        if host == cell.ROUTER_HEALTH_HOST:
            return 200
        self.probes.append((host, port))
        slug = self.hosts.get(host) or host.split(".")[0]
        if self.ps.get(slug, ("",))[0] != "running":
            return 502
        return self.health.get(host, 200)

    def meet_rooms(self, token):
        self.tokens.append(token)
        return self.rooms

    def port_free(self, port):
        return port not in self.busy_ports

    def chown(self, path, uid, gid):
        self.chowns.append((str(path), uid, gid))

    def open_archive(self, path):
        return cell.System.open_archive(self, path)

    def linux(self):
        return True

    def fetch(self, url, limit=64 << 20):
        if url not in self.downloads:
            raise OSError(f"{url}: unreachable")
        return self.downloads[url]

    def machine(self):
        return self.arch

    def latest_release(self):
        return self.release

    def release_published(self, tag):
        return None if self.unpublished is None else tag not in self.unpublished

    def sleep(self, seconds):
        self.t += seconds

    def monotonic(self):
        return self.t

    def argv_text(self):
        return "\n".join(" ".join(c) for c in self.calls)


@pytest.fixture
def host(tmp_path):
    system = FakeSystem()
    root, etc = tmp_path / "cell", tmp_path / "host"

    def call(*argv):
        out = io.StringIO()
        rc = cell.main(
            list(argv), system=system, root=root, host_root=etc, compose_source=ROOT / "compose.yaml", out=out
        )
        lines = out.getvalue().splitlines()
        assert len(lines) == 1, "exactly one JSON object on stdout"
        answer = json.loads(lines[0])
        assert rc == (0 if answer["ok"] else 1)
        return answer

    call.system, call.root, call.etc, call.tmp = system, root, etc, tmp_path
    return call


def secret_file(tmp_path, name, text, mode=0o600):
    p = tmp_path / name
    p.write_text(text + "\n")
    os.chmod(p, mode)
    return str(p)


KMS_ARN = "arn:aws:kms:us-west-2:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab"
KMS_KEY_ID, KMS_SECRET = "AKIA" + "K" * 16, "km/" + "y" * 37  # synthetic, never a real key


def kms_args(host, arn=KMS_ARN):
    """--kms-key and --kms-credentials-file, which a new company needs (a synthetic access key in a 0600 file)."""
    creds = secret_file(
        host.tmp, "kms-credentials", f"AWS_ACCESS_KEY_ID={KMS_KEY_ID}\nAWS_SECRET_ACCESS_KEY={KMS_SECRET}"
    )
    return ["--kms-key", arn, "--kms-credentials-file", creds]


def add_args(host, slug="boatyard", *extra, kms=True):
    return [
        "add",
        "--company",
        slug,
        "--domain",
        f"{slug}.tico.team",
        "--owner-email",
        "owner@boatyard.example",
        "--auth",
        "oidc",
        "--issuer",
        "https://accounts.tico.team",
        "--client-id",
        "tc_" + "0" * 24,
        "--client-secret-file",
        secret_file(host.tmp, "client-secret", SECRET),
        "--version",
        "v1.2.3",
        *(kms_args(host) if kms else []),
        *extra,
        "--json",
    ]


@pytest.fixture
def ready(host):
    assert host("init", "--json")["ok"]
    return host


def record(host, slug="boatyard"):
    return json.loads((host.root / "companies" / slug / "company.json").read_text())
