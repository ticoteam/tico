"""scripts/install.sh in real distro containers (ubuntu:24.04, debian:12) with docker and friends stubbed.

The scenarios are in install_scenarios.sh. They install curl and python3 from the distro's own packages, so the
containers need network access; the tests skip when Docker is not available."""
import hashlib
import importlib.util
import shutil
import subprocess
import tarfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys_path = str(SCRIPTS)


def _builder():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_install_bundle", SCRIPTS / "build_install_bundle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build = _builder()

FAKE_WIZARD = '''import os, pathlib, sys
d = pathlib.Path(os.environ["TICO_INSTALL_DIR"])
with (d / "wizard-args.txt").open("a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
tag = sys.argv[sys.argv.index("--tico-version") + 1]
fd = os.open(d / ".env", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, ("TICO_DOMAIN=tico.example.com\\nTICO_OIDC_CLIENT_SECRET=" + os.environ.get("TICO_OIDC_CLIENT_SECRET", "") + "\\nTICO_TAG=" + tag + "\\n").encode())
'''


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
    (src / "docker").mkdir()
    (src / "docker" / "runner.compose.yaml").write_text("name: tico-runner\n")
    (src / "setup").mkdir()
    (src / "setup" / "__init__.py").write_text("")
    (src / "setup" / "__main__.py").write_text(FAKE_WIZARD)
    for v in ("v0.2.0", "v0.3.0", "v0.9.0"):
        release(base, src, v, tamper=v == "v0.9.0")
    (base / "unpinned").mkdir()
    shutil.copy(SCRIPTS / "install.sh", base / "unpinned" / "install.sh")
    (base / "latest.json").write_text('{"url": "x", "tag_name": "v0.3.0", "name": "v0.3.0"}\n')
    return base


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=8).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.fixture
def docker_ready():
    """Probe lazily only when the opt-in container scenario is selected."""
    if not _docker_available():
        pytest.skip("Docker is unavailable or `docker info` timed out")


@pytest.mark.slow
@pytest.mark.parametrize("image", ["ubuntu:24.04", "debian:12"])
def test_installer_scenarios(image, rel, docker_ready):
    name = "tico-install-test-" + uuid.uuid4().hex[:8]
    try:
        r = subprocess.run(
            ["docker", "run", "--name", name, "--rm", "-v", f"{rel}:/rel:ro", "-v", f"{Path(__file__).parent / 'install_scenarios.sh'}:/scen.sh:ro",
             image, "sh", "-c", "apt-get update -qq >/dev/null 2>&1; sh /scen.sh"],
            capture_output=True, text=True, timeout=900)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    lines = r.stdout.splitlines()
    failed = [l for l in lines if l.startswith("not ok")]
    assert not failed, "\n".join(failed) + "\n" + r.stderr[-2000:]
    assert lines and lines[-1] == "done", r.stdout[-2000:] + r.stderr[-2000:]
    passed = {l[3:] for l in lines if l.startswith("ok ")}
    for must in ("preflight-memory", "preflight-disk", "preflight-port-80", "fresh-secret-not-echoed", "rerun-env-untouched",
                 "rerun-skips-wizard", "upgrade-keeps-settings", "checksum-mismatch-refused", "checksum-installs-nothing",
                 "unpinned-uses-latest-release", "version-flag-overrides-baked", "older-installer-holds",
                 "mac-team-install-refused", "mac-local-exit", "mac-runner-exit", "mac-docker-not-running"):
        assert must in passed, f"scenario {must} did not run"


def test_bundle_from_the_real_repo_has_what_a_server_needs_and_is_reproducible(tmp_path):
    assert build.main(["--version", "v1.2.3", "--output", str(tmp_path / "a")]) == 0
    assert build.main(["--version", "v1.2.3", "--output", str(tmp_path / "b")]) == 0
    a, b = tmp_path / "a", tmp_path / "b"
    assert (a / "tico-bundle-v1.2.3.tar.gz").read_bytes() == (b / "tico-bundle-v1.2.3.tar.gz").read_bytes()
    with tarfile.open(a / "tico-bundle-v1.2.3.tar.gz") as t:
        names = set(t.getnames())
        assert t.extractfile("VERSION").read() == b"1.2.3\n"
        assert b"TICO_TAG=v1.2.3\n" in t.extractfile(".env.example").read()
    assert {"compose.yaml", ".env.example", "setup/__main__.py", "setup/cli.py", "scripts/tico-setup", "docker/runner.compose.yaml"} <= names
    assert not [n for n in names if "/tests/" in n or n.endswith((".pyc", ".env")) or n.startswith("/") or ".." in n]
    sums = dict(reversed(l.split("  ")) for l in (a / "SHA256SUMS").read_text().splitlines())
    for f, digest in sums.items():
        assert hashlib.sha256((a / f.strip()).read_bytes()).hexdigest() == digest
    script = (a / "install.sh").read_text()
    assert "TICO_VERSION_BAKED='v1.2.3'" in script and "@TICO_VERSION@" not in script


def test_docker_probe_is_lazy_and_timeout_is_bounded(monkeypatch):
    calls = []

    def reject_container_probe(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == 'docker':
            raise AssertionError('Docker must not be probed while importing the test module')
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, 'run', reject_container_probe)
    spec = importlib.util.spec_from_file_location('install_tests_import_probe', __file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert all(command[0] != 'docker' for command, _ in calls)

    monkeypatch.setattr(module.shutil, 'which', lambda name: None)
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: pytest.fail('ran Docker without a binary'))
    assert module._docker_available() is False

    monkeypatch.setattr(module.shutil, 'which', lambda name: '/usr/bin/docker' if name == 'docker' else None)

    monkeypatch.setattr(module.subprocess, 'run',
                        lambda command, **kwargs: subprocess.CompletedProcess(command, 1))
    assert module._docker_available() is False

    def timeout(command, **kwargs):
        assert command == ['docker', 'info']
        assert kwargs['timeout'] == 8
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])

    monkeypatch.setattr(module.subprocess, 'run', timeout)
    assert module._docker_available() is False


def test_only_real_container_scenarios_are_marked_slow():
    slow = []
    for name, test in globals().items():
        if name.startswith('test_') and callable(test):
            marks = {mark.name for mark in getattr(test, 'pytestmark', ())}
            if 'slow' in marks:
                slow.append(name)
    assert slow == ['test_installer_scenarios']


def test_build_refuses_a_non_release_version_and_a_script_without_the_placeholder(tmp_path):
    assert build.main(["--version", "latest", "--output", str(tmp_path)]) == 2
    with pytest.raises(SystemExit):
        build.bake("nothing to replace", "v1.2.3")


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_install_sh_is_shellcheck_clean_posix_sh():
    r = subprocess.run(["shellcheck", "-s", "sh", str(SCRIPTS / "install.sh")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
