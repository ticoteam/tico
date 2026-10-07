"""scripts/install.sh --runner, run for real against a local release with docker and the host checks stubbed.

Unlike test_install_sh.py this needs no container: everything the script touches is under tmp_path."""
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from setup.tests.test_install_sh import SCRIPTS, build

ROOT = SCRIPTS.parent
DOCKER = """#!/bin/sh
echo "docker $*" >> "$STUB_LOG"
case "$*" in
  "compose version --short") echo 2.29.7 ;;
  "version --format"*) echo 27.0.0 ;;
  "volume inspect tico-runner") [ -n "${STUB_VOLUME:-}" ] || exit 1 ;;
  "container inspect"*) [ -n "${STUB_BARE:-}" ] || exit 1 ;;
esac
exit 0
"""


@pytest.fixture
def box(tmp_path):
    """A fake Linux server: stub tools first on PATH, a local release to download, a directory to install into."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "docker").write_text(DOCKER)
    (stubs / "uname").write_text('#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo x86_64;; *) exec /usr/bin/uname "$@";; esac\n')
    (stubs / "id").write_text('#!/bin/sh\nif [ "$1" = -u ]; then echo 0; else exec /usr/bin/id "$@"; fi\n')
    (stubs / "df").write_text("#!/bin/sh\nprintf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\nfake 1 1 50000000 1%% /\\n'\n")
    (stubs / "ss").write_text("#!/bin/sh\nexit 0\n")
    (stubs / "hostname").write_text("#!/bin/sh\necho build-host\n")
    for tool in stubs.iterdir():
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "meminfo").write_text("MemTotal:        2000000 kB\n")
    rel = tmp_path / "rel"
    version = "v0.2.0"
    assert build.main(["--version", version, "--output", str(rel / "download" / version), "--source", str(ROOT)]) == 0
    return {"stubs": stubs, "rel": rel, "dir": tmp_path / "runner", "log": tmp_path / "docker.log", "tmp": tmp_path}


def install(box, *args, **env):
    variables = {"PATH": f"{box['stubs']}:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin",
                 "HOME": str(box["tmp"]), "STUB_LOG": str(box["log"]),
                 "TICO_INSTALL_RELEASES_URL": "file://" + str(box["rel"]),
                 "TICO_INSTALL_MEMINFO": str(box["tmp"] / "meminfo"), **env}
    script = box["rel"] / "download" / "v0.2.0" / "install.sh"
    return subprocess.run(["sh", str(script), "--dir", str(box["dir"]), *args], env=variables,
                          capture_output=True, text=True, timeout=120)


JOIN = ("--runner", "--url", "https://tico.example.com", "--code", "code_123-abc", "--label", "Ana's build box")


def test_runner_mode_writes_the_compose_file_and_a_pinned_env_and_starts_it(box):
    result = install(box, *JOIN)
    assert result.returncode == 0, result.stdout + result.stderr
    compose = (box["dir"] / "runner.compose.yaml").read_text()
    assert "tico-updater" in compose and "updater:" in compose        # the sidecar is what follows the release
    assert not (box["dir"] / "compose.yaml").exists()                 # the server's file stays out
    env = (box["dir"] / ".env").read_text().splitlines()
    assert env == ["TICO_URL=https://tico.example.com", "TICO_CODE=code_123-abc",
                   'TICO_RUNNER_LABEL="Ana\'s build box"', "TICO_TAG=v0.2.0", "TICO_UPDATER_TAG=v0.2.0"]
    assert stat.S_IMODE((box["dir"] / ".env").stat().st_mode) == 0o600
    calls = box["log"].read_text()
    assert "docker compose -f runner.compose.yaml up -d" in calls
    assert "docker run" not in calls


def test_running_it_again_keeps_the_env_and_a_bare_docker_run_switches_over_with_its_volume(box):
    assert install(box, *JOIN).returncode == 0
    (box["dir"] / ".env").write_text((box["dir"] / ".env").read_text() + "TICO_RUNNER_PINNED=1\n")
    before = (box["dir"] / ".env").read_text()
    again = install(box, "--runner")                                    # no join flags: the update path
    assert again.returncode == 0 and "Keeping the settings" in again.stdout
    assert (box["dir"] / ".env").read_text() == before
    shutil.rmtree(box["dir"])
    box["log"].write_text("")
    switched = install(box, *JOIN, STUB_VOLUME="1", STUB_BARE="1")
    assert switched.returncode == 0, switched.stderr
    assert "TICO_RUNNER_HOME_VOLUME=tico-runner" in (box["dir"] / ".env").read_text()
    calls = box["log"].read_text()
    assert "docker rm -f tico-runner" in calls
    assert calls.index("docker rm -f tico-runner") < calls.index("up -d")



def test_a_new_code_url_or_label_replaces_only_those_keys_of_an_existing_env(box):
    assert install(box, *JOIN).returncode == 0
    env = box["dir"] / ".env"
    env.write_text(env.read_text() + "TICO_RUNNER_PINNED=1\n")
    again = install(box, "--runner", "--code", "fresh-code_9", "--url", "https://new.example.com")
    assert again.returncode == 0, again.stdout + again.stderr
    assert "Updating TICO_URL TICO_CODE in" in again.stdout and "other settings are kept" in again.stdout
    assert env.read_text().splitlines() == [
        "TICO_URL=https://new.example.com", "TICO_CODE=fresh-code_9", 'TICO_RUNNER_LABEL="Ana\'s build box"',
        "TICO_TAG=v0.2.0", "TICO_UPDATER_TAG=v0.2.0", "TICO_RUNNER_PINNED=1"]
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert "up -d" in box["log"].read_text()
    # A key the file lacks is added, and a label alone leaves the join code as it was.
    env.write_text("TICO_TAG=v0.2.0\n")
    assert install(box, "--runner", "--label", "Box 2").returncode == 0
    assert env.read_text().splitlines() == ["TICO_TAG=v0.2.0", 'TICO_RUNNER_LABEL="Box 2"']


def test_a_named_runner_gets_its_own_project_container_volume_and_updater_next_to_the_default_one(box):
    assert install(box, *JOIN).returncode == 0                                  # the default runner is not touched below
    default_env = (box["dir"] / ".env").read_text()
    mail = box["tmp"] / "runner-mail"
    args = ["--runner", "--name", "Mail Bot", "--url", "http://server:8765", "--code", "code_456", "--server-network", "tico_default"]
    box["log"].write_text("")
    result = subprocess.run(["sh", str(box["rel"] / "download" / "v0.2.0" / "install.sh"), "--dir", str(mail), *args],
                            env={"PATH": f"{box['stubs']}:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(box["tmp"]),
                                 "STUB_LOG": str(box["log"]), "TICO_INSTALL_RELEASES_URL": "file://" + str(box["rel"]),
                                 "TICO_INSTALL_MEMINFO": str(box["tmp"] / "meminfo"), "STUB_VOLUME": "1", "STUB_BARE": "1"},
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (box["dir"] / ".env").read_text() == default_env
    assert (mail / ".env").read_text().splitlines() == [
        "TICO_URL=http://server:8765", "TICO_CODE=code_456", 'TICO_RUNNER_LABEL="Mail Bot"', "TICO_TAG=v0.2.0",
        "TICO_UPDATER_TAG=v0.2.0", "TICO_RUNNER_HOME_VOLUME=tico-runner-mail-bot_runner-home"]     # never the first runner's volume
    override = (mail / "runner.override.yaml").read_text()
    assert override.startswith("name: tico-runner-mail-bot\n") and "container_name: tico-runner-mail-bot\n" in override
    assert "aliases: [tico-runner-mail-bot-updater]" in override and "http://tico-runner-mail-bot-updater:8080" in override
    assert "networks: [default, server]" in override and "name: tico_default" in override
    calls = box["log"].read_text()
    assert "docker rm -f" not in calls                                          # the default runner's container is not replaced
    assert "docker compose -f runner.compose.yaml -f runner.override.yaml up -d" in calls
    # Running it again without --server-network keeps the network and the names, and the label is not reset.
    assert subprocess.run(["sh", str(box["rel"] / "download" / "v0.2.0" / "install.sh"), "--dir", str(mail), "--runner", "--name", "Mail Bot"],
                          env={"PATH": f"{box['stubs']}:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(box["tmp"]),
                               "STUB_LOG": str(box["log"]), "TICO_INSTALL_RELEASES_URL": "file://" + str(box["rel"]),
                               "TICO_INSTALL_MEMINFO": str(box["tmp"] / "meminfo")}, capture_output=True, text=True).returncode == 0
    assert (mail / "runner.override.yaml").read_text() == override
    assert 'TICO_RUNNER_LABEL="Mail Bot"' in (mail / ".env").read_text()


def test_name_needs_runner_and_a_usable_slug_and_the_default_is_unchanged(box):
    for args, wanted in ((("--name", "mail"), "--name goes with --runner"), (("--runner", "--name", "!!!", "--url", "http://s:1", "--code", "c"), "--name needs letters")):
        result = install(box, *args)
        assert result.returncode == 2 and wanted in result.stderr, result.stderr
    assert install(box, *JOIN).returncode == 0
    assert not (box["dir"] / "runner.override.yaml").exists()


def test_local_port_and_names_prefill_setup_and_survive_an_upgrade(box):
    curl = box["stubs"] / "curl"
    curl.write_text('#!/bin/sh\ncase "$*" in *healthz*) echo "health $*" >> "$STUB_LOG"; echo ok;; *) exec /usr/bin/curl "$@";; esac\n')
    curl.chmod(0o755)
    result = install(box, "--local", "--owner-email", "ana@example.com", "--owner-name", "Ana", "--team-name", "Acme", "--port", "8877")
    assert result.returncode == 0, result.stdout + result.stderr
    text = (box["dir"] / ".env").read_text()
    assert 'TICO_OWNER_NAME="Ana"' in text and 'TICO_TEAM_NAME="Acme"' in text and 'TICO_COMPANY_NAME="Acme"' in text and 'TICO_PORT=8877' in text
    assert 'reusable owner sign-in link private' in result.stdout or 'token from:' in result.stdout
    assert 'Recover the sign-in token:' in result.stdout and 'this computer' in result.stdout
    assert 'http://127.0.0.1:8877/healthz' in box["log"].read_text()
    result = install(box, "--local")
    assert result.returncode == 0 and (box["dir"] / ".env").read_text() == text
    result = install(box, "--local", "--port", "8878")
    assert result.returncode == 0 and 'TICO_PORT=8878' in (box["dir"] / ".env").read_text()
    assert 'http://127.0.0.1:8878/healthz' in box["log"].read_text()
