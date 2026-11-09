"""hcloud / doctl are replaced by a recorder: no test here can create anything."""

import json

import pytest
import yaml

from setup import cli, verify
from setup.cloud import Cli
from setup.tests import fakes
from setup.tests.fakes import CaptureIO

SECRET = "GOCSPX-supersecret"
BASE = [
    "--non-interactive",
    "--yes",
    "--domain",
    "tico.example.com",
    "--auth",
    "google",
    "--client-id",
    "cid.apps.googleusercontent.com",
    "--company",
    "Acme",
    "--owner-email",
    "me@example.com",
    "--tico-version",
    "v1.2.3",
    "--skip-dns-wait",
]


class FakeCli:
    """Answers by argv prefix; remembers argv and the user-data file the CLI was pointed at."""

    def __init__(self, replies=None):
        self.replies, self.calls, self.user_data, self.rules = replies or {}, [], None, None
        self.mode = None

    def __call__(self, argv):
        self.calls.append(argv)
        for flag in ("--user-data-from-file", "--user-data-file"):
            if flag in argv:
                import os

                path = argv[argv.index(flag) + 1]
                with open(path) as f:
                    self.user_data = f.read()
                self.mode = os.stat(path).st_mode & 0o777
        if "--rules-file" in argv:
            with open(argv[argv.index("--rules-file") + 1]) as f:
                self.rules = json.load(f)
        best = max((k for k in self.replies if argv[: len(k)] == list(k)), key=len, default=None)
        return self.replies[best] if best else Cli(0, "")

    def verbs(self):
        return [" ".join(a[:3]) for a in self.calls]


def hz(over=None):
    r = {
        ("hcloud", "server", "describe"): Cli(1, "", "not found"),
        ("hcloud", "primary-ip", "describe"): Cli(1, "", "not found"),
        ("hcloud", "firewall", "describe"): Cli(1, "", "not found"),
        ("hcloud", "primary-ip", "create"): Cli(0, json.dumps({"primary_ip": {"id": 7, "ip": "203.0.113.9"}})),
        ("hcloud", "server", "create"): Cli(
            0,
            json.dumps(
                {"server": {"id": 42, "public_net": {"ipv4": {"ip": "203.0.113.9"}}}, "root_password": "ROOTPW-leak"}
            ),
        ),
        ("hcloud", "ssh-key", "list"): Cli(0, "laptop\n"),
    }
    r.update(over or {})
    return FakeCli(r)


def do(over=None):
    r = {
        ("doctl", "compute", "droplet", "list"): Cli(0, ""),
        ("doctl", "compute", "firewall", "list"): Cli(0, ""),
        ("doctl", "compute", "reserved-ip", "create"): Cli(0, "198.51.100.5\n"),
        ("doctl", "compute", "droplet", "create"): Cli(0, "999 192.0.2.44\n"),
        ("doctl", "compute", "ssh-key", "list"): Cli(0, "123\n"),
    }
    r.update(over or {})
    return FakeCli(r)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TICO_OIDC_CLIENT_SECRET", SECRET)
    monkeypatch.setattr(verify, "check_health", lambda d: verify.Check("h", True, "ok"))
    monkeypatch.setattr(verify, "run_all", lambda **kw: [verify.Check("stub", True, "ok")])


def go(argv, fake, installed=True, **kw):
    d = fakes.deps(cloud_cli=fake, which=lambda c: f"/usr/bin/{c}" if installed else None, **kw)
    io = CaptureIO()
    code = cli.main(argv, deps=d, io=io)
    return code, io.text


def test_hetzner_creates_ip_firewall_then_server_with_the_right_argv_and_private_user_data():
    f = hz()
    code, out = go(["--cloud", "hetzner", *BASE], f)
    assert code == 0, out
    assert f.verbs() == [
        "hcloud location list",
        "hcloud ssh-key list",
        "hcloud server describe",
        "hcloud primary-ip describe",
        "hcloud primary-ip create",
        "hcloud firewall describe",
        "hcloud firewall create",
        "hcloud server create",
    ]
    n = "tico-tico-example-com"
    create = next(a for a in f.calls if a[:3] == ["hcloud", "server", "create"])
    for pair in (
        ["--name", n],
        ["--type", "cax11"],
        ["--image", "ubuntu-24.04"],
        ["--location", "nbg1"],
        ["--primary-ipv4", n],
        ["--firewall", n],
        ["--ssh-key", "laptop"],
        ["--label", "ManagedBy=tico-setup"],
        ["--label", f"tico-setup-name={n}"],
    ):
        i = create.index(pair[0])
        assert create[i : i + 2] == pair or pair in [create[j : j + 2] for j in range(len(create) - 1)], pair
    ip_create = next(a for a in f.calls if a[:3] == ["hcloud", "primary-ip", "create"])
    assert "--auto-delete=false" in ip_create and ip_create[ip_create.index("--type") + 1] == "ipv4"
    assert {(r["protocol"], r["port"]) for r in f.rules} == {
        ("tcp", "22"),
        ("tcp", "80"),
        ("tcp", "443"),
        ("udp", "443"),
    }
    assert f.mode == 0o600
    env = yaml.safe_load(f.user_data)["write_files"][0]["content"]
    assert (
        "TICO_CLOUD_SERVER_IP=203.0.113.9" in env
        and "TICO_CLOUD_VERSION=v1.2.3" in env
        and "TICO_CLOUD_ALLOW_SSH=1" in env
    )
    assert not any(SECRET in " ".join(a) for a in f.calls)  # secrets travel in the file, never in argv
    assert SECRET not in out and "ROOTPW-leak" not in out
    assert "A tico.example.com 203.0.113.9" in out or "203.0.113.9" in out


def test_hetzner_without_an_ssh_key_opens_no_port_22():
    f = hz({("hcloud", "ssh-key", "list"): Cli(0, "")})
    code, out = go(["--cloud", "hetzner", *BASE], f)
    assert code == 0 and "--ssh-key" not in next(a for a in f.calls if a[:3] == ["hcloud", "server", "create"])
    assert {r["port"] for r in f.rules} == {"80", "443"} and "TICO_CLOUD_ALLOW_SSH=0" in f.user_data
    assert "no SSH access" in out


def test_not_signed_in_prints_login_and_manual_steps_and_creates_nothing():
    f = hz({("hcloud", "location", "list"): Cli(1, "", "no active context")})
    code, out = go(["--cloud", "hetzner", *BASE], f)
    assert code == 2 and "not signed in" in out and "hcloud context create" in out and "Cloud config: paste" in out
    assert f.verbs() == ["hcloud location list"]


def test_state_is_saved_without_secrets(tmp_path):
    go(["--cloud", "hetzner", *BASE], hz())
    saved = (tmp_path / "home" / "tico.example.com" / "state.json").read_text()
    assert SECRET not in saved and '"target": "hetzner"' in saved and "203.0.113.9" in saved
    code, out = go(["destroy", "--domain", "tico.example.com"], FakeCli())
    assert code == 0 and "hcloud server delete tico-tico-example-com" in out
