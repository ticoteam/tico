"""Helpers that other tests share, moved out of test_cell.py so they import no test file."""

import io
import json
import os
import time
from pathlib import Path
import pytest
from setup import cell
from setup.cell import Result


ROOT = Path(__file__).resolve().parents[3]


SECRET = "oidc-secret-" + "s" * 40


def utc_in(seconds):
    """A wake time `seconds` from now, as a request or `sleep --wake-at` writes it (2026-10-30T18:00:00Z)."""
    return time.strftime(cell.WAKE_AT_FORMAT, time.gmtime(time.time() + seconds))


class FakeSystem:
    """Records every call; `docker compose up` makes a company's container running and healthy, as Docker would."""

    def __init__(self):
        self.calls, self.chowns, self.t = [], [], 0.0
        self.ps = {}  # slug -> (State, Status)
        self.images = set()
        self.health = {}  # host -> status; default 200 while its container runs
        self.busy_ports, self.fail = set(), {}
        self.imds = True  # the metadata block is DOCKER-USER's first rule
        self.docker_user = None  # or the chain's rules as `iptables -S` prints them
        self.release = "v1.2.3"
        self.hosts = {}  # host -> slug, for a host that is not <slug>.<anything>
        self.unpublished = set()  # tags GitHub has no release for; None: GitHub does not answer
        self.probes = []  # (host, port) of each health probe

    def run(self, argv, *, input=None, timeout=900):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        for key, res in self.fail.items():
            if key in joined:
                return res
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
                            "Labels": f"com.docker.compose.service=server,{cell.LABEL}={s}",
                            "State": state,
                            "Status": status,
                        }
                    )
                    + "\n"
                    for s, (state, status) in self.ps.items()
                    if not want or s == want
                ),
            )
        if argv[:3] == ["docker", "image", "inspect"]:
            return Result(0 if argv[-1] in self.images else 1)
        if "--project-name" in argv:
            proj = argv[argv.index("--project-name") + 1]
            slug = proj[len(cell.PROJECT_PREFIX) :]
            if proj == cell.EDGE_PROJECT:
                return Result(0)
            if "up" in argv:
                self.ps[slug] = ("running", "Up 2 seconds (healthy)")
            elif "stop" in argv:
                self.ps[slug] = ("exited", "Exited (0) 1 second ago")
            elif "down" in argv:
                self.ps.pop(slug, None)
        return Result(0)

    def http_status(self, host, path, timeout=3, port=cell.ROUTER_PORT):
        if host == cell.ROUTER_HEALTH_HOST:
            return 200
        self.probes.append((host, port))
        slug = self.hosts.get(host) or host.split(".")[0]
        if self.ps.get(slug, ("",))[0] != "running":
            return 502
        return self.health.get(host, 200)

    def port_free(self, port):
        return port not in self.busy_ports

    def chown(self, path, uid, gid):
        self.chowns.append((str(path), uid, gid))

    def open_archive(self, path):
        return cell.System.open_archive(self, path)

    def linux(self):
        return True

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


def add_args(host, slug="boatyard", *extra):
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
        *extra,
        "--json",
    ]


@pytest.fixture
def ready(host):
    assert host("init", "--json")["ok"]
    return host


def record(host, slug="boatyard"):
    return json.loads((host.root / "companies" / slug / "company.json").read_text())
