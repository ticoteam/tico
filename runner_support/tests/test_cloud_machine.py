"""The base image step and the enroll-at-boot step of a cloud machine, a bot's own or the shared one
(runner_support/cloud_machine.py)."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from runner_support import cloud_machine as cm

CODE = "Zq3-abcdefghijklmnopqrstuvwxyz0123456789_AB"
HUB = "https://acme.example"


class Fake:
    """Stands in for every command: `enroll` writes runner.json the way the Rust runner does, after `fail` failures."""

    def __init__(self, m_home, fail=0, install_ok=True, updates=()):
        self.calls, self.home, self.fail, self.install_ok = [], m_home, fail, install_ok
        self.code_files, self.updates = [], list(updates)

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
        elif "runner_support.release_update" in command:
            result = self.updates.pop(0) if self.updates else {"state": "healthy"}
            out, code = json.dumps(result) + "\n", 0 if result["state"] == "healthy" else 1
        elif command[-1] == "install" and "scripts/tico-computer" in command[-2]:
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


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The first boot reads the hub's release; a test that does not give one sees an unreachable hub."""

    def unreachable(url, timeout):
        raise OSError("no network in tests")

    monkeypatch.setattr("urllib.request.urlopen", unreachable)


@pytest.fixture
def machine(tmp_path):
    home, etc, root = tmp_path / "home", tmp_path / "etc", tmp_path / "tico"
    for d in (home, etc, root):
        d.mkdir()
    said = []

    def make(**kw):
        fake = Fake(home, **kw)
        m = cm.Machine(
            user="user",
            home=home,
            uid=os.getuid(),
            gid=os.getgid(),
            root=root,
            run=fake,
            say=said.append,
            sleep=lambda s: None,
            etc=etc,
        )
        return m, fake

    make.home, make.etc, make.said = home, etc, said
    return make


def boat_bashrc(home):
    (home / ".bashrc").write_text(
        "alias ll='ls -l'\n# >>> boat managed env >>>\n"
        f"export TICO_ENROLL_TOKEN='{CODE}'\nexport TICO_HUB_URL={HUB}\nexport BOAT_ID=sb_1\n# <<< boat managed env <<<\n"
    )
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
    assert starts and starts[0][-len(cm.JOBS) :] == [f"tico-{job}.service" for job in cm.JOBS]
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
    assert CODE in (machine.home / ".bashrc").read_text()  # kept: systemd runs the next try with it
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
            listed = [
                line.split("Signed-By: ")[1]
                for f in sources.glob("*.sources")
                for line in f.read_text().splitlines()
                if line.startswith("Signed-By: ")
            ]
            assert listed and all(Path(key).exists() for key in listed)
        return fake(command, **kw)

    m.run = run
    cm.tools(m, arch="x86_64", apt=apt)
    both = ["google-chrome.sources", "nodesource.sources"]
    assert seen == [("update", []), ("key", []), ("key", []), ("update", both)]
    steps = [what for what, _ in cm.tool_commands("x86_64", apt)]
    assert (
        steps.index("curl and ca-certificates")
        < steps.index("the apt keyring directory")
        < steps.index("NodeSource's signing key")
        < steps.index("the apt sources of NodeSource and Google")
        < steps.index("the package lists with the new repositories")
    )


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
        cm.base(
            m,
            release="0.5.7",
            sudoers=tmp_path / "sudoers",
            unit_dir=tmp_path / "units",
            apt=tmp_path / "apt",
            arch="x86_64",
        )
    assert not cm.marker_path(machine.etc).exists() and not fake.named("loginctl")


def test_base_gives_sudo_and_the_boot_service_only_on_the_machine_it_prepares(machine, tmp_path):
    m, fake = machine()
    with pytest.raises(cm.Refused):
        cm.base(m, release="", sudoers=tmp_path / "sudoers", unit_dir=tmp_path / "units")
    cm.base(
        m,
        release="0.5.7",
        sudoers=tmp_path / "sudoers",
        unit_dir=tmp_path / "units",
        apt=tmp_path / "apt",
        arch="x86_64",
    )
    sudoers = tmp_path / "sudoers"
    assert "user ALL=(ALL) NOPASSWD:ALL" in sudoers.read_text() and oct(sudoers.stat().st_mode & 0o777) == "0o440"
    assert ["loginctl", "enable-linger", "user"] in fake.calls
    assert fake.named("visudo") and ["systemctl", "enable", cm.UNIT] in fake.calls
    assert [c for c in fake.calls if "runner_support.engine" in c and "prepare" in c]
    unit = (tmp_path / "units" / cm.UNIT).read_text()
    # runner_support's own name: every release a machine may boot (MIN_RUNNER_RELEASE 0.6.0) has it (since 0.5.94).
    assert f"ExecStart={m.python} -m runner_support.cloud_machine boot" in unit.splitlines()
    assert "Restart=on-failure" in unit and "WantedBy=multi-user.target" in unit
    marker = json.loads(cm.marker_path(machine.etc).read_text())
    assert marker == {"user": "user", "root": str(m.root), "release": "0.5.7"}

    m.config.parent.mkdir(parents=True, exist_ok=True)
    m.config.write_text("{}")
    with pytest.raises(cm.Refused):
        cm.base(m, release="0.5.7", sudoers=tmp_path / "sudoers", unit_dir=tmp_path / "units", apt=tmp_path / "apt")


def test_the_boot_units_command_runs_through_the_runner_forwarder():
    # What an image saved before this release starts, `python -m runner.cloud_machine boot`, reaches runner_support's
    # boot (--help: no root).
    root = Path(__file__).resolve().parents[2]
    old, new = (
        subprocess.run(
            [sys.executable, "-m", module, "boot", "--help"], cwd=root, capture_output=True, text=True, timeout=60
        )
        for module in ("runner.cloud_machine", "runner_support.cloud_machine")
    )
    assert old.returncode == new.returncode == 0, old.stderr
    assert old.stdout == new.stdout and "boot" in old.stdout


def old_boot_unit(m, units):
    """The boot unit as an image saved before runner_support/ was named has it."""
    units.mkdir(exist_ok=True)
    text = cm.unit_text(m).replace("-m runner_support.cloud_machine", "-m runner.cloud_machine")
    (units / cm.UNIT).write_text(text)
    (units / cm.UNIT).chmod(0o644)
    return text


def test_a_boot_points_a_saved_images_boot_unit_at_runner_support_once(machine, tmp_path):
    m, fake = machine()
    units = tmp_path / "units"
    old_boot_unit(m, units)
    assert cm.boot(m, {}, unit_dir=units) == 0
    assert (units / cm.UNIT).read_text() == cm.unit_text(m)
    assert oct((units / cm.UNIT).stat().st_mode & 0o777) == "0o644"
    assert fake.calls.count(["systemctl", "daemon-reload"]) == 1
    assert cm.boot(m, {}, unit_dir=units) == 0
    assert fake.calls.count(["systemctl", "daemon-reload"]) == 1  # already moved: nothing to reload


@pytest.mark.parametrize("how", ["exits non-zero", "times out"])
def test_a_failed_reload_puts_the_boot_unit_back_and_the_next_boot_tries_again(machine, tmp_path, how):
    m, fake = machine()
    units = tmp_path / "units"
    text = old_boot_unit(m, units)
    reloads = []

    def run(command, **kw):
        if command == ["systemctl", "daemon-reload"]:
            reloads.append(command)
            if len(reloads) == 1:
                if how == "times out":
                    raise subprocess.TimeoutExpired(command, 120)
                return subprocess.CompletedProcess(command, 1, "", "")
        return fake(command, **kw)

    m.run = run
    assert cm.boot(m, {}, unit_dir=units) == 0  # the boot goes on
    assert (units / cm.UNIT).read_text() == text and len(reloads) == 2
    assert cm.move_boot_unit(m, units) and (units / cm.UNIT).read_text() == cm.unit_text(m)


class Healthz:
    """urlopen's stand-in for the hub's /healthz."""

    def __init__(self, body):
        self.body, self.urls = body, []

    def __call__(self, url, timeout):
        self.urls.append(url)
        if isinstance(self.body, Exception):
            raise self.body
        body = self.body

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, n):
                return json.dumps(body).encode()

        return Reply()


def test_a_new_machine_moves_to_the_hubs_release_before_it_enrolls(machine, monkeypatch):
    """#648: an image built on 0.5.19 enrolled with a server that refuses runners below 0.6; the first boot moves it
    before the runner enrolls, so nothing below the hub's release ever enrolls."""
    hub = Healthz({"ok": True, "release": "0.6.64"})
    monkeypatch.setattr("urllib.request.urlopen", hub)
    m, fake = machine()
    boat_bashrc(machine.home)
    assert cm.boot(m, {}) == 0
    assert hub.urls == [HUB + "/healthz"]
    names = [" ".join(c) for c in fake.calls]
    update = next(i for i, c in enumerate(names) if "runner_support.release_update" in c)
    enroll = next(i for i, c in enumerate(names) if " enroll " in f" {c} ")
    assert update < enroll, "the checkout moves before the runner enrolls"
    command = fake.calls[update]
    assert command[:3] == ["runuser", "-u", "user"]
    assert command[command.index("--version") + 1] == "0.6.64"
    assert command[command.index("--state-dir") + 1] == str(machine.home / ".config" / "tico" / "first-boot-update")
    assert "--pid" not in command  # no runner yet: forward only, nothing restarted
    assert not any(CODE in c for c in names)
    assert any("on release 0.6.64" in line for line in machine.said)
    # A later boot never moves the checkout: the running runner follows the hub itself.
    m2, fake2 = machine()
    assert cm.boot(m2, {}) == 0 and not fake2.named("--state-dir") and len(hub.urls) == 1


@pytest.mark.parametrize(
    "refusal",
    [
        {"state": "blocked", "error": "the checkout has 3 changed files; commit or stash them: a"},
        {"state": "blocked", "error": "the checkout changed during the update; retry"},
    ],
)
def test_a_momentary_refusal_is_tried_again_and_other_failures_leave_it_to_the_runner(machine, monkeypatch, refusal):
    monkeypatch.setattr("urllib.request.urlopen", Healthz({"release": "0.6.64"}))
    waits = []
    m, fake = machine(updates=[refusal, refusal, {"state": "healthy"}])
    m.sleep = waits.append
    boat_bashrc(machine.home)
    assert cm.boot(m, {}) == 0
    assert len(fake.named("--state-dir")) == 3 and waits == [cm.CATCH_UP_WAIT_S] * 2
    assert fake.named("enroll") and fake.named("install")

    m.config.unlink()
    shutil.rmtree(machine.home / ".config" / "systemd")
    boat_bashrc(machine.home)
    m2, fake2 = machine(updates=[{"state": "failed", "error": "release v0.6.64 was not found"}])
    assert cm.boot(m2, {}) == 0
    assert len(fake2.named("--state-dir")) == 1 and fake2.named("enroll") and fake2.named("install")
    assert any("the runner will keep trying" in line for line in machine.said)


def test_a_refusal_that_lasts_is_tried_a_bounded_number_of_times_inside_the_codes_life(machine, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", Healthz({"release": "0.6.64"}))
    m, fake = machine(updates=[{"state": "blocked", "error": "the checkout has 1 changed file: a"}] * 99)
    boat_bashrc(machine.home)
    assert cm.boot(m, {}) == 0
    assert len(fake.named("--state-dir")) == cm.CATCH_UP_TRIES and fake.named("enroll")
    assert cm.CATCH_UP_TRIES * cm.CATCH_UP_WAIT_S <= 8 * 60  # the enroll code lasts 15 minutes


@pytest.mark.parametrize(
    "body",
    [OSError("unreachable"), {"release": ""}, {"release": "main; rm -rf /"}, ["0.6.64"], {"ok": True}],
)
def test_an_unreadable_hub_release_skips_the_catch_up(machine, monkeypatch, body):
    monkeypatch.setattr("urllib.request.urlopen", Healthz(body))
    m, fake = machine()
    boat_bashrc(machine.home)
    assert cm.boot(m, {}) == 0
    assert not fake.named("--state-dir") and fake.named("enroll") and fake.named("install")


def test_hub_release_reads_healthz_and_strips_a_leading_v():
    assert cm.hub_release(HUB + "/", Healthz({"release": "v0.6.64"})) == "0.6.64"
    assert cm.hub_release(HUB, Healthz({"release": "0.7.0-rc.1"})) == "0.7.0-rc.1"


def test_each_boot_records_whether_it_came_through_the_old_runner_package(machine, monkeypatch):
    # `python -m runner.cloud_machine boot` (an image saved before v0.6.315) sets TICO_RUNNER_SHIM; the runner reads
    # the file and reports it beside its release (runner-rs/src/follow.rs entry).
    m, _ = machine()
    assert cm.boot(m, {"TICO_RUNNER_SHIM": "1"}) == 0
    assert (machine.etc / "tico" / "boot-entry").read_text() == "runner\n"
    m, _ = machine()
    assert cm.boot(m, {}) == 0
    assert (machine.etc / "tico" / "boot-entry").read_text() == "runner_support\n"

    # A boot that cannot write its record leaves none, never the last boot's: the runner then reports not reported.
    def full(path, text, mode):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cm, "write_root_file", full)
    m, _ = machine()
    assert cm.boot(m, {"TICO_RUNNER_SHIM": "1"}) == 0
    assert not (machine.etc / "tico" / "boot-entry").exists()
    assert any("could not record how this boot started" in line for line in machine.said)
    monkeypatch.undo()
    # The boot service's own start hands the word to boot() and takes it out of what boot starts.
    monkeypatch.setenv("TICO_RUNNER_SHIM", "1")
    for key in (cm.TOKEN, cm.HUB, cm.FILE):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cm.os, "geteuid", lambda: 0)
    m, _ = machine()
    monkeypatch.setattr(cm, "load", lambda: m)
    assert cm.main(["boot"]) == 0
    assert (machine.etc / "tico" / "boot-entry").read_text() == "runner\n" and "TICO_RUNNER_SHIM" not in os.environ


# -- the move at the runner's start (machines that have not booted since the boot unit could move) -------------------


def entry_file(etc):
    return etc / "tico" / "boot-entry"


@pytest.mark.parametrize("last_boot", ["runner\n", None])
def test_a_runner_start_moves_the_boot_unit_and_records_that_the_next_boot_uses_runner_support(
    machine, tmp_path, last_boot
):
    # A machine booted through `python -m runner.cloud_machine` (or before boots wrote a record) and never since: its
    # runner's start moves the unit, reloads systemd and records runner_support, so it reports clear with no reboot.
    m, fake = machine()
    units = tmp_path / "units"
    old_boot_unit(m, units)
    if last_boot:
        entry_file(machine.etc).parent.mkdir(parents=True)
        entry_file(machine.etc).write_text(last_boot)
    assert cm.move_unit_now(m, units) == 0
    assert (units / cm.UNIT).read_text() == cm.unit_text(m)
    assert fake.calls.count(["systemctl", "daemon-reload"]) == 1
    assert entry_file(machine.etc).read_text() == "runner_support\n"
    assert oct(entry_file(machine.etc).stat().st_mode & 0o777) == "0o644"


@pytest.mark.parametrize("how", ["reload fails", "write fails"])
def test_a_failed_move_at_the_runner_start_keeps_the_old_unit_and_the_old_record(machine, tmp_path, monkeypatch, how):
    m, fake = machine()
    units = tmp_path / "units"
    text = old_boot_unit(m, units)
    entry_file(machine.etc).parent.mkdir(parents=True)
    entry_file(machine.etc).write_text("runner\n")
    if how == "reload fails":
        m.run = lambda command, **kw: subprocess.CompletedProcess(command, 1, "", "")
    else:
        from runner_support import release_update

        def full(path, content, mode):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(release_update, "replace_file", full)
    assert cm.move_unit_now(m, units) == 1
    assert (units / cm.UNIT).read_text() == text
    assert entry_file(machine.etc).read_text() == "runner\n"


def test_a_runner_start_records_runner_support_when_the_unit_already_moved(machine, tmp_path):
    # The boot that came through the old package moved the unit: the next boot uses runner_support, so say so now.
    m, fake = machine()
    units = tmp_path / "units"
    units.mkdir()
    (units / cm.UNIT).write_text(cm.unit_text(m))
    entry_file(machine.etc).parent.mkdir(parents=True)
    entry_file(machine.etc).write_text("runner\n")
    assert cm.move_unit_now(m, units) == 0
    assert entry_file(machine.etc).read_text() == "runner_support\n"
    assert ["systemctl", "daemon-reload"] not in fake.calls


def test_a_machine_with_no_boot_unit_records_nothing(machine, tmp_path):
    m, _ = machine()
    assert cm.move_unit_now(m, tmp_path / "units") == 1
    assert not entry_file(machine.etc).exists()


def test_the_move_unit_command_runs_as_root_on_the_marked_machine(machine, monkeypatch):
    m, _ = machine()
    moved = []
    monkeypatch.setattr(cm, "load", lambda: m)
    monkeypatch.setattr(cm, "move_unit_now", lambda mm: moved.append(mm) or 0)
    monkeypatch.setattr(cm.os, "geteuid", lambda: 1000)
    assert cm.main(["move-unit"]) == 2 and moved == []
    monkeypatch.setattr(cm.os, "geteuid", lambda: 0)
    assert cm.main(["move-unit"]) == 0 and moved == [m]
    monkeypatch.setattr(cm, "load", lambda: None)
    assert cm.main(["move-unit"]) == 0  # not a cloud machine: nothing to do


class Sudo:
    def __init__(self, code=0, raises=None):
        self.calls, self.code, self.raises = [], code, raises

    def __call__(self, command, **kw):
        self.calls.append((command, kw))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(command, self.code, "", "systemctl daemon-reload failed\n")


UNIT_ENV = {"TICO_SYSTEMD_UNIT": "tico-bot.service"}  # scripts/tico-computer install's unit runs the runner


def cloud_etc(tmp_path, unit, entry):
    etc = tmp_path / "etc"
    (etc / "tico").mkdir(parents=True)
    (etc / "systemd" / "system").mkdir(parents=True)
    (etc / "tico" / "cloud-machine.json").write_text("{}")
    (etc / "systemd" / "system" / cm.UNIT).write_text(f"[Service]\nExecStart=/py -m {unit} boot\n")
    if entry:
        (etc / "tico" / "boot-entry").write_text(entry)
    return etc


@pytest.mark.parametrize(
    "unit, entry",
    [
        ("runner.cloud_machine", "runner\n"),
        ("runner.cloud_machine", None),
        ("runner_support.cloud_machine", "runner\n"),
        ("runner_support.cloud_machine", None),
    ],
)
def test_the_runners_start_asks_root_to_move_whatever_is_still_old(tmp_path, unit, entry):
    etc, run, said = cloud_etc(tmp_path, unit, entry), Sudo(), []
    cm.move_at_start(UNIT_ENV, etc=etc, run=run, python="/py", root=tmp_path, say=said.append)
    [(command, kw)] = run.calls
    assert command == ["sudo", "-n", "/py", "-m", "runner_support.cloud_machine", "move-unit"]
    assert kw["cwd"] == str(tmp_path) and kw["timeout"] > 0


def test_the_runners_start_asks_nothing_when_all_is_moved_unsupervised_or_off_a_cloud_machine(tmp_path):
    etc, run = cloud_etc(tmp_path, "runner_support.cloud_machine", "runner_support\n"), Sudo()
    cm.move_at_start(UNIT_ENV, etc=etc, run=run, python="/py", root=tmp_path, say=print)
    etc = cloud_etc(tmp_path / "by-hand", "runner.cloud_machine", None)
    cm.move_at_start({}, etc=etc, run=run, python="/py", root=tmp_path, say=print)  # started by hand or by a test
    (etc / "tico" / "cloud-machine.json").unlink()
    (etc / "systemd" / "system" / cm.UNIT).write_text("ExecStart=/py -m runner.cloud_machine boot\n")
    cm.move_at_start(UNIT_ENV, etc=etc, run=run, python="/py", root=tmp_path, say=print)
    assert run.calls == []


@pytest.mark.parametrize(
    "run", [Sudo(code=1), Sudo(raises=subprocess.TimeoutExpired("sudo", 60)), Sudo(raises=FileNotFoundError("sudo"))]
)
def test_a_failed_move_at_the_runners_start_is_said_and_the_runner_still_starts(tmp_path, run):
    etc, said = cloud_etc(tmp_path, "runner.cloud_machine", "runner\n"), []
    cm.move_at_start(UNIT_ENV, etc=etc, run=run, python="/py", root=tmp_path, say=said.append)  # never raises
    assert any("boot unit" in line for line in said)
    assert (etc / "tico" / "boot-entry").read_text() == "runner\n"
