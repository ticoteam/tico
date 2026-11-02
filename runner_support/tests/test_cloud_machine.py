"""The base image step and the enroll-at-boot step of a cloud machine, a bot's own or the shared one
(runner_support/cloud_machine.py)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from runner_support import cloud_machine as cm

CODE = "Zq3-abcdefghijklmnopqrstuvwxyz0123456789_AB"
HUB = "https://acme.example"


class Fake:
    """Stands in for every command: `enroll` writes runner.json the way the Rust runner does, after `fail` failures."""

    def __init__(self, m_home, fail=0, install_ok=True):
        self.calls, self.home, self.fail, self.install_ok = [], m_home, fail, install_ok
        self.code_files = []

    def __call__(self, command, **kw):
        self.calls.append(command)
        out, code = "", 0
        if "path" in command and "runner_support.engine" in command:
            out = "/home/user/tico/runtime/bin/tico-runner\n"
        elif "enroll" in command:
            code_file = Path(command[command.index("--code-file") + 1])
            self.code_files.append(code_file)
            assert json.loads(code_file.read_text()) == {"code": CODE}
            assert oct(code_file.stat().st_mode & 0o777) == "0o600"
            if self.fail:
                self.fail -= 1
                out, code = f"enroll failed for {CODE}: Tico is not answering", 1
            else:
                config = Path(command[command.index("--config") + 1])
                config.parent.mkdir(parents=True, exist_ok=True)
                config.write_text(json.dumps({"runner_id": "r1", "token": "t"}))
        elif command[-1] == "install" and "scripts/tico" in command[-2]:
            if self.install_ok:
                units = self.home / ".config" / "systemd" / "user"
                units.mkdir(parents=True, exist_ok=True)
                for job in cm.JOBS:
                    (units / f"tico-{job}.service").write_text("[Unit]\n")
            else:
                code = 1
        return subprocess.CompletedProcess(command, code, out, "")

    def named(self, word):
        return [c for c in self.calls if word in c]


@pytest.fixture
def machine(tmp_path):
    home, etc, root = tmp_path / "home", tmp_path / "etc", tmp_path / "tico"
    for d in (home, etc, root):
        d.mkdir()
    said = []

    def make(**kw):
        fake = Fake(home, **kw)
        m = cm.Machine(user="user", home=home, uid=os.getuid(), gid=os.getgid(), root=root, run=fake,
                       say=said.append, sleep=lambda s: None, etc=etc)
        return m, fake

    make.home, make.etc, make.said = home, etc, said
    return make


def boat_bashrc(home):
    (home / ".bashrc").write_text(
        "alias ll='ls -l'\n# >>> boat managed env >>>\n"
        f"export TICO_ENROLL_TOKEN='{CODE}'\nexport TICO_HUB_URL={HUB}\nexport BOAT_ID=sb_1\n# <<< boat managed env <<<\n")
    (home / ".bashrc").chmod(0o640)


def test_a_new_machine_enrolls_once_starts_the_runner_and_leaves_the_code_nowhere(machine):
    m, fake = machine()
    boat_bashrc(machine.home)
    assert cm.boot(m, {}) == 0
    enrolls = fake.named("enroll")
    assert len(enrolls) == 1
    command = enrolls[0]
    assert command[:3] == ["runuser", "-u", "user"] and "env" in command and "-i" in command
    assert HUB in command and CODE not in " ".join(command)
    assert not any(CODE in " ".join(c) for c in fake.calls)
    assert fake.code_files and not fake.code_files[0].exists()
    assert m.config.exists() and fake.named("install")
    bashrc = (machine.home / ".bashrc").read_text()
    assert CODE not in bashrc and "TICO_HUB_URL" in bashrc and "alias ll" in bashrc and "BOAT_ID" in bashrc
    assert oct((machine.home / ".bashrc").stat().st_mode & 0o777) == "0o640"
    assert not any(CODE in line for line in machine.said)


def test_a_later_boot_only_starts_the_runner_and_takes_out_a_code_boat_wrote_again(machine):
    m, fake = machine()
    boat_bashrc(machine.home)
    assert cm.boot(m, {}) == 0
    (machine.etc / "environment").write_text(f'PATH="/usr/bin"\nTICO_ENROLL_TOKEN="{CODE}"\n')
    m2, fake2 = machine()
    assert cm.boot(m2, {}) == 0
    assert not fake2.named("enroll") and not fake2.named("install")
    starts = fake2.named("start")
    assert starts and starts[0][-4:] == [f"tico-{job}.service" for job in cm.JOBS]
    assert (machine.etc / "environment").read_text() == 'PATH="/usr/bin"\n'


def test_a_lost_answer_is_retried_with_the_same_code_and_a_dead_server_leaves_it_for_the_next_try(machine):
    m, fake = machine(fail=1)
    boat_bashrc(machine.home)
    assert cm.boot(m, {}) == 0
    assert len(fake.named("enroll")) == 2 and m.config.exists()

    m.config.unlink()
    boat_bashrc(machine.home)
    m2, fake2 = machine(fail=99)
    assert cm.boot(m2, {}) == 1
    assert len(fake2.named("enroll")) == cm.ATTEMPTS
    assert all(not f.exists() for f in fake2.code_files)
    assert CODE in (machine.home / ".bashrc").read_text()   # kept: systemd runs the next try with it
    assert not any(CODE in line for line in machine.said)


def test_the_service_environment_is_read_and_unset_and_never_passed_on(machine):
    m, fake = machine()
    assert cm.boot(m, {cm.TOKEN: CODE, cm.HUB: HUB}) == 0
    assert fake.named("unset-environment") == [["systemctl", "unset-environment", cm.TOKEN]]
    assert len(fake.named("enroll")) == 1


def test_nothing_happens_without_a_code_or_on_another_computer(machine, tmp_path):
    m, fake = machine()
    assert cm.boot(m, {}) == 0
    assert fake.calls == []
    assert cm.boot(m, {cm.TOKEN: "not a code; rm -rf /", cm.HUB: HUB}) == 0
    assert fake.calls == []
    assert cm.load(etc=tmp_path / "nowhere") is None
    assert cm.boot(None, {cm.TOKEN: CODE, cm.HUB: HUB}) == 0


def test_a_named_file_comes_before_the_system_files_and_install_failures_retry(machine, tmp_path):
    named = tmp_path / "boat.env"
    named.write_text(f"TICO_ENROLL_TOKEN={CODE}\nTICO_HUB_URL={HUB}\n")
    (machine.etc / "environment").write_text("TICO_HUB_URL=https://other.example\n")
    m, fake = machine(install_ok=False)
    assert cm.boot(m, {cm.FILE: str(named)}) == 1
    assert HUB in fake.named("enroll")[0]
    assert CODE not in named.read_text()
    m2, fake2 = machine()
    assert cm.boot(m2, {}) == 0 and not fake2.named("enroll") and fake2.named("install")


def test_parse_reads_shell_assignments_without_running_them():
    text = "export A='x y'\nB=\"q\"  # note\n# C=no\nD=$(touch /tmp/pwn)\nE=\nnot a line\n"
    assert cm.parse(text) == {"A": "x y", "B": "q", "D": "", "E": ""}


def test_tools_get_google_chrome_on_x86_64_and_chromium_elsewhere(tmp_path):
    x86 = [(what, argv) for what, argv in cm.tool_commands("x86_64", tmp_path) if isinstance(argv, list)]
    arm = [(what, argv) for what, argv in cm.tool_commands("aarch64", tmp_path) if isinstance(argv, list)]
    install_x86 = next(argv for _, argv in x86 if "xvfb" in argv)
    install_arm = next(argv for _, argv in arm if "xvfb" in argv)
    assert "google-chrome-stable" in install_x86 and "chromium" not in install_x86
    assert "chromium" in install_arm and "google-chrome-stable" not in install_arm
    shared = {*cm.DISPLAY_PACKAGES, *cm.FONT_PACKAGES, "nodejs"}
    assert shared <= set(install_x86) and shared <= set(install_arm)
    assert "ffmpeg" not in install_x86 and "ffmpeg" not in install_arm
    assert any("google-chrome.asc" in " ".join(argv) for _, argv in x86)
    assert not any("google-chrome.asc" in " ".join(argv) for _, argv in arm)


def test_tool_sources_add_google_only_on_x86_64(tmp_path):
    chrome = tmp_path / "sources.list.d" / "google-chrome.sources"
    node = tmp_path / "sources.list.d" / "nodesource.sources"
    x86, arm = cm.tool_files("x86_64", tmp_path), cm.tool_files("aarch64", tmp_path)
    assert "Architectures: amd64" in x86[chrome] and "https://dl.google.com/linux/chrome/deb/" in x86[chrome]
    assert chrome not in arm
    assert "node_22.x" in x86[node] and "node_22.x" in arm[node]


@pytest.mark.parametrize("version, ok", [("24.21.0", True), ("22.0.0", True), ("21.7.3", False)])
def test_the_node_check_accepts_22_or_newer(tmp_path, version, ok):
    """boat's image ships Node 24 ahead of apt's 22 on PATH: a newer Node passes the check, an older one fails it."""
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    argv = next(argv for what, argv in cm.tool_commands("x86_64", tmp_path) if what.startswith("Node.js"))
    script = argv[-1].replace("process.versions.node", repr(version))
    assert subprocess.run([shutil.which("node"), "-e", script]).returncode == (0 if ok else 1)


def test_the_box_token_line_leaves_the_user_bashrc_and_the_rest_stays(tmp_path):
    bashrc = tmp_path / ".bashrc"
    bashrc.write_text('export PATH="$HOME/bin:$PATH"\nexport ASCII_TOKEN="secret-box-token"\nalias ll="ls -l"\n')
    bashrc.chmod(0o640)
    assert cm.drop_ascii_token(bashrc) == 1
    assert bashrc.read_text() == 'export PATH="$HOME/bin:$PATH"\nalias ll="ls -l"\n'
    assert bashrc.stat().st_mode & 0o777 == 0o640
    assert cm.drop_ascii_token(bashrc) == 0 and cm.drop_ascii_token(tmp_path / "missing") == 0
    assert not (tmp_path / "missing").exists()


def test_the_sources_are_written_only_once_their_keys_are_on_disk(machine, tmp_path):
    """A fresh image: apt refuses a repository whose Signed-By key is missing, so the first update runs with the
    distro's lists only, the keys come next, then the sources that name them, then the update that reads them."""
    m, fake = machine()
    apt = tmp_path / "apt"
    sources = apt / "sources.list.d"
    seen = []

    def run(command, **kw):
        if command[:2] == ["install", "-d"]:
            Path(command[-1]).mkdir(parents=True, exist_ok=True)
        elif "update" in command:
            seen.append(("update", sorted(p.name for p in sources.glob("*.sources")) if sources.exists() else []))
        elif command[0] == "curl":
            seen.append(("key", sorted(p.name for p in sources.glob("*.sources")) if sources.exists() else []))
            Path(command[command.index("-o") + 1]).write_text("key")
        elif "install" in command and "nodejs" in command:
            listed = [line.split("Signed-By: ")[1] for f in sources.glob("*.sources")
                      for line in f.read_text().splitlines() if line.startswith("Signed-By: ")]
            assert listed and all(Path(key).exists() for key in listed)
        return fake(command, **kw)

    m.run = run
    cm.tools(m, arch="x86_64", apt=apt)
    both = ["google-chrome.sources", "nodesource.sources"]
    assert seen == [("update", []), ("key", []), ("key", []), ("update", both)]
    steps = [what for what, _ in cm.tool_commands("x86_64", apt)]
    assert steps.index("curl and ca-certificates") < steps.index("the apt keyring directory") \
        < steps.index("NodeSource's signing key") < steps.index("the apt sources of NodeSource and Google") \
        < steps.index("the package lists with the new repositories")


def test_base_installs_the_tools_as_root_before_the_marker(machine, tmp_path):
    m, fake = machine()
    apt = tmp_path / "apt"
    cm.base(m, release="0.5.7", sudoers=tmp_path / "sudoers", unit_dir=tmp_path / "units", apt=apt, arch="aarch64")
    installs = [i for i, c in enumerate(fake.calls) if "chromium" in c and "install" in c]
    assert installs and max(installs) < fake.calls.index(["loginctl", "enable-linger", "user"])
    assert not fake.named("playwright") and not fake.named("ffmpeg") and not fake.named("google-chrome-stable")
    assert (apt / "sources.list.d" / "nodesource.sources").exists()
    assert not (apt / "sources.list.d" / "google-chrome.sources").exists()


def test_a_failed_tool_step_refuses_the_base_and_writes_no_marker(machine, tmp_path):
    m, fake = machine()

    def run(command, **kw):
        if "xvfb" in command:
            return subprocess.CompletedProcess(command, 100, "", "E: Unable to locate package xvfb")
        return fake(command, **kw)

    m.run = run
    with pytest.raises(cm.Refused, match="installing the package lists|xvfb|installing"):
        cm.base(m, release="0.5.7", sudoers=tmp_path / "sudoers", unit_dir=tmp_path / "units",
                apt=tmp_path / "apt", arch="x86_64")
    assert not cm.marker_path(machine.etc).exists() and not fake.named("loginctl")


def test_base_gives_sudo_and_the_boot_service_only_on_the_machine_it_prepares(machine, tmp_path):
    m, fake = machine()
    with pytest.raises(cm.Refused):
        cm.base(m, release="", sudoers=tmp_path / "sudoers", unit_dir=tmp_path / "units")
    cm.base(m, release="0.5.7", sudoers=tmp_path / "sudoers", unit_dir=tmp_path / "units", apt=tmp_path / "apt",
            arch="x86_64")
    sudoers = tmp_path / "sudoers"
    assert "user ALL=(ALL) NOPASSWD:ALL" in sudoers.read_text() and oct(sudoers.stat().st_mode & 0o777) == "0o440"
    assert ["loginctl", "enable-linger", "user"] in fake.calls
    assert fake.named("visudo") and ["systemctl", "enable", cm.UNIT] in fake.calls
    assert [c for c in fake.calls if "runner_support.engine" in c and "prepare" in c]
    unit = (tmp_path / "units" / cm.UNIT).read_text()
    assert "-m runner_support.cloud_machine boot" in unit and "Restart=on-failure" in unit and "WantedBy=multi-user.target" in unit
    marker = json.loads(cm.marker_path(machine.etc).read_text())
    assert marker == {"user": "user", "root": str(m.root), "release": "0.5.7"}

    m.config.parent.mkdir(parents=True, exist_ok=True)
    m.config.write_text("{}")
    with pytest.raises(cm.Refused):
        cm.base(m, release="0.5.7", sudoers=tmp_path / "sudoers", unit_dir=tmp_path / "units", apt=tmp_path / "apt")
