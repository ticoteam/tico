"""Setup installs the release it is part of, not GitHub's latest stable (task #214). While 0.6 ships as release
candidates GitHub's "latest" is a 0.5 release, so an unpinned install from a v0.6.0-rc.N checkout got 0.5.

Three defaults: the server's release (--tico-version), the runner's (--runner-tag), and the compose.yaml an AWS install
writes. No git, no network: the checkout's tag and the server's /healthz are fakes."""

import base64
import gzip
import re
import json
import subprocess
from pathlib import Path

import pytest

from setup import cli, contract, runner_setup, state, wizard
from setup.cloud import Cli
from setup.tests import fakes
from setup.tests.fakes import CaptureIO
from setup.tests.helpers.preview_flags import CREATE, DOMAIN, env, world  # noqa: F401 (env: its secrets, autouse)

RC = "v0.6.0-rc.18"
STABLE = "v0.5.216"
CHECKOUT_COMPOSE = b"name: tico\n# the checkout's own compose.yaml\n"


def go(argv, d):
    io = CaptureIO()
    return cli.main(argv, deps=d, io=io), io.text


# --- which release a checkout is ----------------------------------------------------------------------------------


def git(stdout="", code=0, calls=None):
    def run(argv, **kw):
        if calls is not None:
            calls.append(argv)
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="")

    return run


def test_a_checkout_at_a_release_tag_is_that_release(tmp_path):
    calls = []
    assert wizard.checkout_version(tmp_path, git(RC + "\n", calls=calls)) == RC
    assert calls == [["git", "-C", str(tmp_path), "tag", "--points-at", "HEAD"]]


@pytest.mark.parametrize(
    "tags, release",
    [
        (f"nightly\n{RC}\n", RC),  # another tag on the same commit does not hide the release
        ("v0.6.0-rc.18\nv0.6.0\n", "v0.6.0"),  # an rc promoted on the same commit: the final release
    ],
)
def test_every_tag_on_head_is_read(tmp_path, tags, release):
    assert wizard.checkout_version(tmp_path, git(tags)) == release


def test_the_install_bundles_version_file_names_the_release_without_asking_git(tmp_path):
    (tmp_path / "VERSION").write_text("0.6.0-rc.18\n")  # scripts/build_install_bundle.py writes it without the v
    assert wizard.checkout_version(tmp_path, lambda *a, **k: pytest.fail("git is not asked")) == RC


@pytest.mark.parametrize(
    "run",
    [
        git(""),  # main, a branch, a commit between releases: no tag at HEAD
        git("", code=128),  # not a git checkout
        git("nightly\n"),  # a tag that is not a release
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("git")),  # no git installed
        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("git", 10)),
    ],
)
def test_anything_else_is_no_release(tmp_path, run):
    assert wizard.checkout_version(tmp_path, run) == ""


# --- AWS: the server's release and its compose.yaml ---------------------------------------------------------------


def aws(*extra, checkout=RC):
    d, ec2, iam, ssm, r53 = world()
    d.checkout_version = lambda: checkout
    d.compose_local = lambda: CHECKOUT_COMPOSE
    code, out = go([*CREATE, *extra], d)
    assert code == 0, out
    (run,) = ec2.called("run_instances")
    env_text = "\n".join(kw["Value"] for name, kw in ssm.calls if name == "put_parameter")
    return run["UserData"], env_text


def embedded_compose(user_data):
    m = re.search(r"echo (\S+) \| base64 -d \| gunzip > \S+/compose\.yaml", user_data)
    return gzip.decompress(base64.b64decode(m.group(1))) if m else None


def test_an_unpinned_aws_install_from_a_release_checkout_installs_that_release_with_its_own_compose():
    user_data, env_text = aws()
    assert f"TICO_TAG={RC}" in env_text
    assert embedded_compose(user_data) == CHECKOUT_COMPOSE  # the checkout is that release: its compose.yaml is
    assert "curl -fsSL https://raw.githubusercontent.com" not in user_data


def test_an_unpinned_aws_install_from_main_keeps_the_latest_image_and_the_checkouts_compose():
    user_data, env_text = aws(checkout="")
    assert "TICO_TAG=" not in env_text.replace("TICO_TAG=\n", "")  # unchanged: compose's ${TICO_TAG:-latest}
    assert embedded_compose(user_data) == CHECKOUT_COMPOSE


@pytest.mark.parametrize("checkout", [RC, ""])
def test_another_release_named_by_tico_version_brings_its_own_compose_not_the_checkouts(checkout):
    user_data, env_text = aws("--tico-version", STABLE, checkout=checkout)
    assert f"TICO_TAG={STABLE}" in env_text
    assert embedded_compose(user_data) is None
    assert f"curl -fsSL {contract.compose_url(STABLE)} -o " in user_data


def test_a_compose_ref_given_without_tico_version_still_names_where_compose_comes_from():
    sha = "0123456789abcdef0123456789abcdef01234567"
    user_data, env_text = aws("--compose-ref", sha)
    assert f"TICO_TAG={RC}" in env_text
    assert state.load(DOMAIN)[0]["compose_ref"] == sha


def test_naming_the_checkouts_own_release_keeps_its_compose():
    user_data, env_text = aws("--tico-version", RC)
    assert f"TICO_TAG={RC}" in env_text and embedded_compose(user_data) == CHECKOUT_COMPOSE


def test_a_re_run_from_a_newer_checkout_keeps_the_release_pinned_before():
    d, ec2, iam, ssm, r53 = world()
    d.compose_local = lambda: CHECKOUT_COMPOSE
    d.checkout_version = lambda: RC
    assert go([*CREATE, "--tico-version", STABLE], d)[0] == 0
    code, out = go(CREATE, d)
    assert code == 0, out
    env_text = [kw["Value"] for name, kw in ssm.calls if name == "put_parameter"][-1]
    assert f"TICO_TAG={STABLE}" in env_text and RC not in env_text


def test_same_release_only_swaps_the_compose_for_another_release():
    assert wizard.same_release("main", RC) and wizard.same_release("main", "")
    assert wizard.same_release("0123456789abcdef0123456789abcdef01234567", "")  # a pinned commit: as before
    assert wizard.same_release(RC, RC)
    assert not wizard.same_release(STABLE, RC) and not wizard.same_release(STABLE, "")


# --- Hetzner / DigitalOcean: the release cloud-init installs ------------------------------------------------------


class Hetzner:
    """hcloud, answered by argv prefix; keeps the user-data file the server was created with."""

    REPLIES = {
        ("hcloud", "server", "describe"): Cli(1, "", "not found"),
        ("hcloud", "primary-ip", "describe"): Cli(1, "", "not found"),
        ("hcloud", "firewall", "describe"): Cli(1, "", "not found"),
        ("hcloud", "primary-ip", "create"): Cli(0, json.dumps({"primary_ip": {"id": 7, "ip": "203.0.113.9"}})),
        ("hcloud", "server", "create"): Cli(
            0, json.dumps({"server": {"id": 42, "public_net": {"ipv4": {"ip": "203.0.113.9"}}}})
        ),
        ("hcloud", "ssh-key", "list"): Cli(0, "laptop\n"),
    }

    def __init__(self):
        self.user_data = ""

    def __call__(self, argv):
        if "--user-data-from-file" in argv:
            self.user_data = Path(argv[argv.index("--user-data-from-file") + 1]).read_text()
        best = max((k for k in self.REPLIES if argv[: len(k)] == list(k)), key=len, default=None)
        return self.REPLIES[best] if best else Cli(0, "")


def test_a_cloud_install_from_a_release_checkout_installs_that_release_without_asking_github(monkeypatch):
    monkeypatch.setenv("TICO_OIDC_CLIENT_SECRET", "GOCSPX-supersecret")
    f = Hetzner()
    d = fakes.deps(cloud_cli=f, which=lambda c: f"/usr/bin/{c}", checkout_version=lambda: RC)
    argv = ["--cloud", "hetzner", "--non-interactive", "--yes", "--domain", "tico.example.com", "--auth", "google"]
    argv += ["--client-id", "cid.apps.googleusercontent.com", "--company", "Acme", "--owner-email", "me@example.com"]
    code, out = go([*argv, "--skip-dns-wait"], d)  # fakes.deps fails the test if GitHub's latest is asked
    assert code == 0, out
    assert re.search(r"TICO_CLOUD_VERSION\W+" + re.escape(RC), f.user_data), f.user_data[:2000]


# --- the runner: the server's release ---------------------------------------------------------------------------

RUNNER = ["runner", "--dry-run", "--server-url", "https://tico.example.com", "--target", "ssh", "--ssh", "me@box"]
RUNNER += ["--label", "box", "--non-interactive"]


def test_a_runner_installs_the_release_the_server_reports_at_healthz():
    asked = []
    d = fakes.deps(server_release=lambda url: asked.append(url) or "v0.6.0-rc.18")
    code, out = go(RUNNER, d)
    assert code == 0, out
    assert asked == ["https://tico.example.com"]
    assert f"run the {RC} release's install.sh --runner" in out


def test_a_server_reporting_its_release_without_the_v_still_pins_it():
    code, out = go(RUNNER, fakes.deps(server_release=lambda url: "0.6.0-rc.18"))
    assert code == 0 and f"the {RC} release's" in out


@pytest.mark.parametrize("reported", ["", "dev", "demo"])
def test_a_server_that_names_no_release_stops_the_runner_rather_than_guess_latest(reported):
    code, out = go(RUNNER, fakes.deps(server_release=lambda url: reported))
    assert code != 0
    assert "--runner-tag vX.Y.Z" in out and "latest release's" not in out


@pytest.mark.parametrize("tag", ["latest", STABLE])
def test_runner_tag_still_wins_and_the_server_is_not_asked(tag):
    d = fakes.deps(server_release=lambda url: pytest.fail("--runner-tag given: /healthz is not read"))
    code, out = go([*RUNNER, "--runner-tag", tag], d)
    assert code == 0, out
    assert f"run the {tag} release's install.sh --runner" in out


def test_the_runner_join_script_fetches_the_servers_release_installer():
    assert runner_setup.installer_url(RC) == f"https://github.com/ticoteam/tico/releases/download/{RC}/install.sh"


def test_server_release_reads_healthz(monkeypatch):
    seen = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return b'{"ok": true, "service": "tico", "release": "v0.6.0-rc.18"}'

    monkeypatch.setattr(wizard.urllib.request, "urlopen", lambda req, timeout: seen.append(req.full_url) or Resp())
    assert wizard._server_release("https://tico.example.com/") == RC
    assert seen == ["https://tico.example.com/healthz"]
    monkeypatch.setattr(wizard.urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(OSError("down")))
    assert wizard._server_release("https://tico.example.com") == ""
