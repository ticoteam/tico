"""scripts/install.sh in real distro containers (ubuntu:24.04, debian:12) with docker and friends stubbed.

The scenarios are in install_scenarios.sh. The container first installs curl and python3 from the distro's own mirror,
the one step that needs the network, bounded by PACKAGES_SECONDS: a slow or stalled mirror fails as `not ok packages`,
not as a silent hang inside the installer's own apt-get. After it the scenarios read only local files. A container that still outlives SCENARIO_SECONDS fails with its last passed scenario and the
processes still running in it. The tests skip when Docker is not available."""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path

import pytest
from setup.tests.helpers.install_sh import SCRIPTS, build

sys_path = str(SCRIPTS)


FAKE_WIZARD = '''import os, pathlib, sys
d = pathlib.Path(os.environ["TICO_INSTALL_DIR"])
with (d / "wizard-args.txt").open("a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
if os.environ.get("FAKE_WIZARD_FAIL"):
    sys.exit(1)
tag = sys.argv[sys.argv.index("--tico-version") + 1]
fd = os.open(d / ".env", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, ("TICO_DOMAIN=tico.example.com\\nTICO_OIDC_CLIENT_SECRET=" + os.environ.get("TICO_OIDC_CLIENT_SECRET", "") + "\\nTICO_TAG=" + tag + "\\n").encode())
'''


# ubuntu:24.04 points at archive.ubuntu.com and security.ubuntu.com, which can send tens of KB/s to a runner, so apt-get
# update alone outlasts the test. The test is about install.sh, not Ubuntu's archive: the container uses Azure's Ubuntu
# mirror, the one GitHub's runner images use (update and install in seconds). debian:12 keeps deb.debian.org, a CDN.
# apt gives up on a mirror that sends nothing for 30 s and tries a request twice more; `timeout` bounds the whole step,
# so a mirror that trickles cannot hold it either.
UBUNTU_MIRROR = "http://azure.archive.ubuntu.com/ubuntu/"
PACKAGES_SECONDS = 240
SCENARIO_SECONDS = PACKAGES_SECONDS + 120
CONTAINER = (
    "if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then"
    f" sed -i 's#^URIs: .*#URIs: {UBUNTU_MIRROR}#' /etc/apt/sources.list.d/ubuntu.sources; fi; "
    "printf 'Acquire::http::Timeout \"30\";\\nAcquire::https::Timeout \"30\";\\nAcquire::Retries \"2\";\\n'"
    " > /etc/apt/apt.conf.d/99tico-install-test; "
    f"if timeout {PACKAGES_SECONDS} sh -c 'apt-get update -qq"
    " && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends curl python3' >/dev/null; then"
    " echo 'ok packages'; else"
    f" echo 'not ok packages: apt-get could not install curl and python3 from the distro mirror in {PACKAGES_SECONDS} s'"
    "; exit 1; fi; sh /scen.sh"
)
# Every process in the container with its command line, from /proc (the images have no ps).
PROCESSES = 'for p in /proc/[0-9]*; do printf "%s %s\\n" "${p#/proc/}" "$(tr "\\0" " " < "$p/cmdline")"; done'


def run_scenarios(command, name, image):
    """(returncode, stdout, stderr) of the scenario container. One that outlives SCENARIO_SECONDS fails the test with
    the scenario it last passed and the processes still running in it: the step it hung on."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=SCENARIO_SECONDS)
        return process.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        running = subprocess.run(
            ["docker", "exec", name, "sh", "-c", PROCESSES], capture_output=True, text=True, timeout=30
        )
        process.kill()
        stdout, stderr = process.communicate()  # everything read so far, the part before the timeout too
    passed = [line[3:] for line in stdout.splitlines() if line.startswith("ok ")]
    pytest.fail(
        "the %s container ran past %d s; last passed: %s\nstill running:\n%s\nstdout tail:\n%s\nstderr tail:\n%s"
        % (
            image,
            SCENARIO_SECONDS,
            passed[-1] if passed else "(none)",
            running.stdout.strip() or running.stderr.strip(),
            stdout[-1500:],
            stderr[-1500:],
        )
    )


def release(base: Path, source: Path, version: str, tamper: bool = False) -> None:
    (source / "compose.yaml").write_text(f"# marker {version}\nname: tico\n")
    out = base / "download" / version
    assert build.main(["--version", version, "--output", str(out), "--source", str(source)]) == 0
    if tamper:  # a bundle that no longer matches its SHA256SUMS
        bundle = out / f"tico-bundle-{version}.tar.gz"
        bundle.write_bytes(bundle.read_bytes() + b"x")


@pytest.fixture(scope="module")
def rel(tmp_path_factory):
    base = tmp_path_factory.mktemp("rel")
    src = tmp_path_factory.mktemp("src")
    (src / "scripts").mkdir()
    shutil.copy(SCRIPTS / "install.sh", src / "scripts" / "install.sh")
    (src / "infra" / "windows").mkdir(parents=True)
    shutil.copy(SCRIPTS.parent / "infra" / "windows" / "install-wsl.ps1", src / "infra" / "windows" / "install-wsl.ps1")
    (src / "docker").mkdir()
    (src / "docker" / "runner.compose.yaml").write_text("name: tico-runner\n")
    (src / "setup").mkdir()
    (src / "setup" / "__init__.py").write_text("")
    (src / "setup" / "__main__.py").write_text(FAKE_WIZARD)
    (src / "setup" / "support.py").write_text("")
    (src / "backend").mkdir()
    (src / "backend" / "redaction.py").write_text("")
    for v in ("v0.2.0", "v0.3.0", "v0.9.0"):
        release(base, src, v, tamper=v == "v0.9.0")
    (base / "unpinned").mkdir()
    shutil.copy(SCRIPTS / "install.sh", base / "unpinned" / "install.sh")
    (base / "latest.json").write_text('{"url": "x", "tag_name": "v0.3.0", "name": "v0.3.0"}\n')
    return base


def docker_is_ready() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=8).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.fixture
def docker_ready():
    if not docker_is_ready():
        pytest.skip("Docker is unavailable or `docker info` failed/timed out after 8 seconds")


@pytest.mark.slow
@pytest.mark.parametrize("image", ["ubuntu:24.04", "debian:12"])
def test_installer_scenarios(image, rel, docker_ready):
    name = "tico-install-test-" + uuid.uuid4().hex[:8]
    # Docker Desktop may not share an external checkout or pytest's configured temp volume.
    with tempfile.TemporaryDirectory(
        prefix="tico-install-test-", dir="/private/tmp" if sys.platform == "darwin" else None
    ) as stage:
        staged = Path(stage)
        shutil.copytree(rel, staged / "rel")
        shutil.copy(Path(__file__).parent / "install_scenarios.sh", staged / "scen.sh")
        try:
            returncode, stdout, stderr = run_scenarios(
                [
                    "docker",
                    "run",
                    "--name",
                    name,
                    "--rm",
                    "--mount",
                    f"type=bind,src={staged / 'rel'},dst=/rel,readonly",
                    "--mount",
                    f"type=bind,src={staged / 'scen.sh'},dst=/scen.sh,readonly",
                    image,
                    "sh",
                    "-c",
                    CONTAINER,
                ],
                name,
                image,
            )
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    if returncode != 0 and any(
        message in stderr
        for message in ("Mounts denied", "is not shared from the host", "error while creating mount source path")
    ):
        pytest.skip(
            "Docker cannot bind-mount the staged installer fixtures; share the system temp directory in Docker Desktop"
        )
    lines = stdout.splitlines()
    failed = [l for l in lines if l.startswith("not ok")]
    assert not failed, "\n".join(failed) + "\n" + stderr[-2000:]
    assert lines and lines[-1] == "done", stdout[-2000:] + stderr[-2000:]
    passed = {l[3:] for l in lines if l.startswith("ok ")}
    for must in (
        "packages",
        "preflight-memory",
        "preflight-disk",
        "preflight-port-80",
        "fresh-secret-not-echoed",
        "rerun-env-untouched",
        "rerun-skips-wizard",
        "upgrade-keeps-settings",
        "checksum-mismatch-refused",
        "checksum-installs-nothing",
        "unpinned-uses-latest-release",
        "version-flag-overrides-baked",
        "older-installer-holds",
        "mac-team-install-refused",
        "mac-local-exit",
        "mac-runner-exit",
        "mac-docker-not-running",
        "wizard-fail-exit",
        "wizard-fail-no-offer-without-tty",
    ):
        assert must in passed, f"scenario {must} did not run"


def test_wizard_keeps_private_inbox_settings_across_sudo_by_name_only(tmp_path):
    # Run the real privilege boundary with a sudo stand-in that drops settings not explicitly preserved.
    stubs = tmp_path / "bin"
    stubs.mkdir()
    scripts = {
        "id": '#!/bin/sh\necho 1000\n',
        "sudo": '''#!/bin/sh
printf '%s\\n' "$*" > "$CAPTURE/argv"
keep=$2
for name in TICO_INBOUND_KEY TICO_INBOUND_URL; do
  case ",$keep," in *",$name,"*) ;; *) unset "$name" ;; esac
done
shift 2
exec "$@"
''',
        "python3": '''#!/bin/sh
umask 077
printf '%s\\n' "${TICO_INBOUND_KEY:-}" > "$CAPTURE/key"
printf '%s\\n' "${TICO_INBOUND_URL:-}" > "$CAPTURE/url"
''',
    }
    for name, text in scripts.items():
        (stubs / name).write_text(text)
        (stubs / name).chmod(0o755)
    source = (SCRIPTS / "install.sh").read_text()
    function = "run_wizard() {" + source.split("run_wizard() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    script = (
        'set -eu\nstep() { :; }; ensure_python() { :; }; warn() { :; }; as_root() { "$@"; }\n'
        'TUNNEL= YES=1 VERSION=v1.2.3 DIR="$CAPTURE"\n' + function + "run_wizard --non-interactive\n"
    )
    key, url = "tin_private-0123456789abcdefghijklmnopqrstuv", "https://inbox.private.example"
    result = subprocess.run(
        ["sh", "-c", script],
        env={
            "PATH": f"{stubs}:/usr/bin:/bin",
            "CAPTURE": str(tmp_path),
            "TICO_INBOUND_KEY": key,
            "TICO_INBOUND_URL": url,
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "key").read_text().strip() == key and (tmp_path / "url").read_text().strip() == url
    shown = result.stdout + result.stderr + (tmp_path / "argv").read_text()
    assert key not in shown and url not in shown


def test_bundle_from_the_real_repo_has_what_a_server_needs_and_is_reproducible(tmp_path):
    assert build.main(["--version", "v1.2.3", "--output", str(tmp_path / "a")]) == 0
    assert build.main(["--version", "v1.2.3", "--output", str(tmp_path / "b")]) == 0
    a, b = tmp_path / "a", tmp_path / "b"
    assert (a / "tico-bundle-v1.2.3.tar.gz").read_bytes() == (b / "tico-bundle-v1.2.3.tar.gz").read_bytes()
    with tarfile.open(a / "tico-bundle-v1.2.3.tar.gz") as t:
        names = set(t.getnames())
        assert t.extractfile("VERSION").read() == b"1.2.3\n"
        assert b"TICO_TAG=v1.2.3\n" in t.extractfile(".env.example").read()
    assert {
        "compose.yaml",
        ".env.example",
        "setup/__main__.py",
        "setup/cli.py",
        "scripts/tico-setup",
        "docker/runner.compose.yaml",
        "setup/support.py",
        "backend/redaction.py",
        "backend/company_file/starter.py",
        "backend/company_file/roles.json",
    } <= names
    assert not [n for n in names if "/tests/" in n or n.endswith((".pyc", ".env")) or n.startswith("/") or ".." in n]
    sums = dict(reversed(l.split("  ")) for l in (a / "SHA256SUMS").read_text().splitlines())
    for f, digest in sums.items():
        assert hashlib.sha256((a / f.strip()).read_bytes()).hexdigest() == digest
    script = (a / "install.sh").read_text()
    assert "TICO_VERSION_BAKED='v1.2.3'" in script and "@TICO_VERSION@" not in script
    assert "$Baked = 'v1.2.3'" in (a / "install-wsl.ps1").read_text() and "install-wsl.ps1" in sums


def test_build_refuses_a_non_release_version_and_a_script_without_the_placeholder(tmp_path):
    assert build.main(["--version", "latest", "--output", str(tmp_path)]) == 2
    with pytest.raises(SystemExit):
        build.bake("nothing to replace", "v1.2.3")


def shellcheck_skip_reason(environ=os.environ, which=shutil.which):
    """The Release workflow runs the same shellcheck after the tag is pushed; v0.2.30 failed there. On CI (GitHub sets
    CI=true; its Ubuntu VMs ship shellcheck) a missing shellcheck is a failure, not a skip, so the release gate cannot
    pass without this check. None means the test runs."""
    return None if which("shellcheck") or environ.get("CI") else "shellcheck not installed"


@pytest.mark.skipif(shellcheck_skip_reason() is not None, reason="shellcheck not installed")
def test_install_sh_is_shellcheck_clean_posix_sh():
    if not shutil.which("shellcheck"):
        pytest.fail("shellcheck is required on CI (CI is set) and is not installed")
    r = subprocess.run(["shellcheck", "-s", "sh", str(SCRIPTS / "install.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
