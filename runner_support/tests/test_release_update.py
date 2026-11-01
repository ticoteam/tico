"""A checkout follows its server's release: the update process (python -m runner_support.release_update) against a temp repository."""
import fcntl
import subprocess
import threading
import time

import pytest

from runner_support import release_update as ru


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, name, text="x", message=None):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", message or name)
    return sh(repo, "git", "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    """A public repo with releases 0.1.0 and 0.2.0 (0.2.0 changes the dependencies) plus a checkout on main at 0.1.0."""
    public = tmp_path / "public"
    public.mkdir()
    sh(public, "git", "init", "-q", "-b", "main")
    commit(public, "app.py", "one")
    commit(public, "backend/requirements.txt", "httpx==1\n")
    sh(public, "git", "tag", "v0.1.0")
    commit(public, "app.py", "two")
    commit(public, "backend/requirements.txt", "httpx==2\n")
    sh(public, "git", "tag", "v0.2.0")
    commit(public, "app.py", "unreleased main")
    checkout = tmp_path / "checkout"
    sh(tmp_path, "git", "clone", "-q", str(public), "checkout")
    sh(checkout, "git", "reset", "-q", "--hard", "v0.1.0")
    sh(checkout, "git", "tag", "-d", "v0.2.0")       # the checkout has not seen that release yet
    state = tmp_path / "state"
    state.mkdir()
    return public, checkout, state


class Calls:
    def __init__(self, healthy=(True,), install_error="", prepare_error=""):
        self.restarts, self.installs, self.checks, self.prepared = 0, [], [], []
        self.answers, self.install_error, self.prepare_error = list(healthy), install_error, prepare_error

    def prepare(self, root):
        self.prepared.append(sh(root, "git", "rev-parse", "HEAD"))
        return self.prepare_error

    def restart(self):
        self.restarts += 1

    def install(self, root, old, new):
        self.installs.append((old, new))
        return self.install_error if len(self.installs) == 1 else ""

    def healthy(self, commit, since):
        self.checks.append(commit)
        return self.answers.pop(0) if self.answers else True


def run_apply(checkout, state, version="0.2.0", calls=None, **kw):
    calls = calls or Calls()
    result = ru.apply(checkout, version, state, install=calls.install, prepare=calls.prepare, restart=calls.restart,
                      healthy=calls.healthy, **kw)
    return result, calls


def head(repo):
    return sh(repo, "git", "rev-parse", "HEAD")


def test_update_switches_to_the_servers_tag_not_main(repos):
    public, checkout, state = repos
    result, calls = run_apply(checkout, state)
    assert result["state"] == "healthy" and ru.checkout_release(checkout) == "0.2.0"
    assert head(checkout) == sh(public, "git", "rev-parse", "v0.2.0") != head(public)   # main moved on; the runner did not
    assert calls.restarts == 1 and calls.installs == [(sh(checkout, "git", "rev-parse", "v0.1.0"), head(checkout))]
    assert calls.checks == [head(checkout)] and calls.prepared == [head(checkout)]   # the new tree's binary, before the restart
    assert ru.read_status(state)["state"] == "healthy" and ru.read_status(state)["target"] == "0.2.0"


def test_a_checkout_with_changes_is_refused_and_left_alone(repos):
    public, checkout, state = repos
    (checkout / "app.py").write_text("my local edit")
    before = head(checkout)
    result, calls = run_apply(checkout, state)
    assert result["state"] == "blocked" and "1 changed file" in result["error"] and result["error"].endswith("updates itself: app.py")
    assert (checkout / "app.py").read_text() == "my local edit" and head(checkout) == before
    assert calls.restarts == 0 and ru.read_status(state)["state"] == "blocked"
    assert sh(checkout, "git", "branch", "--show-current") == "main"


def test_a_blocked_update_names_the_first_three_changed_files(repos):
    public, checkout, state = repos
    names = [f"notes/{letter}.txt" for letter in "abcde"]
    for name in names:
        commit(checkout, name, "one")                   # tracked, so a change to it shows in status
    for name in names:
        (checkout / name).write_text("two")
    result, calls = run_apply(checkout, state)
    error = result["error"]
    assert result["state"] == "blocked" and "5 changed files" in error
    assert error.endswith("updates itself: notes/a.txt, notes/b.txt, notes/c.txt, and 2 more")
    assert "notes/d.txt" not in error and "notes/e.txt" not in error and calls.restarts == 0


def test_a_changed_file_named_like_a_disk_error_is_named_not_read_as_one(repos):
    public, checkout, state = repos
    commit(checkout, "tests/test_enospc.py", "one")
    (checkout / "tests/test_enospc.py").write_text("two")
    result, _ = run_apply(checkout, state)
    assert result["state"] == "blocked" and result["error"].endswith("updates itself: tests/test_enospc.py")


def test_a_renamed_file_is_named_with_both_paths(repos):
    public, checkout, state = repos
    # a staged rename is one status line: "R  app.py -> renamed.py"
    sh(checkout, "git", "mv", "app.py", "renamed.py")
    result, _ = run_apply(checkout, state)
    assert result["state"] == "blocked" and "app.py -> renamed.py" in result["error"]


@pytest.mark.slow
def test_a_tag_that_moved_upstream_is_not_trusted(repos):
    public, checkout, state = repos
    run_apply(checkout, state)                                  # the checkout now has v0.2.0
    sh(checkout, "git", "checkout", "-q", "main")
    sh(public, "git", "tag", "-f", "v0.2.0", "main")            # someone re-points the release
    result, _ = run_apply(checkout, state)
    assert result["state"] == "failed" and "differs" in result["error"]


def test_rollback_when_the_new_release_does_not_come_back(repos):
    public, checkout, state = repos
    before = head(checkout)
    result, calls = run_apply(checkout, state, calls=Calls(healthy=(False, True)))
    assert result["state"] == "rolled_back" and "did not report in" in result["error"]
    assert head(checkout) == before and sh(checkout, "git", "branch", "--show-current") == "main"
    assert calls.restarts == 2 and len(calls.installs) == 2       # forward, then the old dependencies back
    assert ru.read_status(state)["state"] == "rolled_back"


def test_a_release_whose_rust_runner_cannot_be_had_goes_back_before_any_restart(repos):
    public, checkout, state = repos
    before = head(checkout)
    result, calls = run_apply(checkout, state, calls=Calls(prepare_error="could not download the Rust runner for 0.2.0"))
    assert result["state"] == "rolled_back" and result["error"].startswith("could not download the Rust runner")
    assert head(checkout) == before and calls.restarts == 1 and calls.checks == [before]   # the restart is the way back


def test_the_prepare_step_is_a_new_process_of_the_new_tree(tmp_path, monkeypatch):
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw["cwd"]))
        return subprocess.CompletedProcess(cmd, 1, "", "Tico runner: no Rust runner for this checkout: cargo is not on PATH\n")
    assert ru.prepare_binary(tmp_path, run=run) == "" and not seen      # a tree from before the shim gets its own
    (tmp_path / "runner").mkdir()
    (tmp_path / "runner" / "engine.py").write_text("")
    monkeypatch.setattr(ru.sys, "executable", "/opt/env-python/bin/python3")
    assert "cargo is not on PATH" in ru.prepare_binary(tmp_path, run=run)
    assert seen == [(["/opt/env-python/bin/python3", "-m", "runner_support.engine", "prepare"], str(tmp_path))]


# -- runners that share one checkout -------------------------------------------------------------

def test_a_checkout_already_on_the_release_restarts_a_runner_that_started_on_other_code(repos):
    public, checkout, state = repos
    started = head(checkout)
    run_apply(checkout, state)
    on_release = head(checkout)
    restarted = []
    result, calls = run_apply(checkout, state, started=started, helpers=lambda: restarted.append("all"))
    assert result["state"] == "healthy" and ru.read_status(state)["state"] == "healthy"
    assert calls.restarts == 1 and calls.checks == [on_release] and restarted == ["all"]
    assert calls.installs == [(started, on_release)]          # this runner's interpreter may not have the new dependencies
    assert head(checkout) == on_release
    result, calls = run_apply(checkout, state, calls=Calls(healthy=(False,)), started=started)
    assert result["state"] == "failed" and "did not report in" in result["error"] and calls.restarts == 1
    assert head(checkout) == on_release                                             # no going back from a restart


def test_a_failed_install_on_a_checkout_already_moved_leaves_the_runner_running(repos):
    public, checkout, state = repos
    started = head(checkout)
    run_apply(checkout, state)
    result, calls = run_apply(checkout, state, calls=Calls(install_error="pip install failed: no network"), started=started)
    assert result["state"] == "failed" and result["error"] == "pip install failed: no network"
    assert calls.restarts == 0 and ru.read_status(state)["state"] == "failed"


def test_the_install_step_uses_the_update_process_interpreter(repos, monkeypatch):
    public, checkout, state = repos
    started = head(checkout)
    run_apply(checkout, state)
    pips = []

    def run(cmd, **kw):
        if cmd[0] == "git":
            return subprocess.run(cmd, **kw)
        pips.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(ru.sys, "executable", "/opt/env-python/bin/python3")
    assert ru.pip_install(checkout, started, head(checkout), run=run) == ""
    assert pips[0][:4] == ["/opt/env-python/bin/python3", "-m", "pip", "install"]
    assert pips[0][-1] == str(checkout / "backend" / "requirements.txt")


def test_a_runner_already_on_the_release_is_not_restarted(repos):
    public, checkout, state = repos
    run_apply(checkout, state)
    for started in (head(checkout), ""):                                            # "": a runner from before --started
        result, calls = run_apply(checkout, state, started=started)
        assert result["state"] == "healthy" and calls.restarts == 0 and calls.checks == []


def hold(checkout):
    handle = open(ru.lock_path(checkout), "a")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def test_an_update_of_a_shared_checkout_waits_for_another_under_way(repos):
    public, checkout, state = repos
    before = head(checkout)
    held = hold(checkout)                                         # another environment's update holds the checkout
    result, calls = run_apply(checkout, state, lock_wait=0)
    assert result["state"] == "waiting" and ru.read_status(state)["state"] == "waiting"
    assert head(checkout) == before and calls.restarts == 0 and calls.installs == []
    seen = []

    def release():
        time.sleep(1.5)
        seen.append(ru.read_status(state)["state"])
        held.close()
    threading.Thread(target=release).start()
    result, calls = run_apply(checkout, state, lock_wait=30)
    assert seen == ["updating"]                                    # its runner starts no second update meanwhile
    assert result["state"] == "healthy" and ru.checkout_release(checkout) == "0.2.0" and calls.restarts == 1


# -- helper jobs follow the release --------------------------------------------------------------

@pytest.mark.slow
def test_a_healthy_update_restarts_the_helpers_and_a_rollback_does_not(repos):
    public, checkout, state = repos
    restarted = []
    result, _ = run_apply(checkout, state, helpers=lambda: restarted.append("all"))
    assert result["state"] == "healthy" and restarted == ["all"]
    sh(checkout, "git", "reset", "-q", "--hard", "v0.1.0")
    restarted.clear()
    result, _ = run_apply(checkout, state, calls=Calls(healthy=(False, True)), helpers=lambda: restarted.append("all"))
    assert result["state"] == "rolled_back" and restarted == []


class Launchctl:
    def __init__(self, fail_kickstart=()):
        self.calls, self.fail_kickstart = [], set(fail_kickstart)

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        bad = cmd[1] == "kickstart" and cmd[-1].rsplit("/", 1)[-1] in self.fail_kickstart
        return subprocess.CompletedProcess(cmd, 1 if bad else 0, "", "")


def install_plists(agents, *labels):
    agents.mkdir(parents=True, exist_ok=True)
    for label in labels:
        (agents / f"{label}.plist").write_text("<plist/>")


def test_only_installed_helpers_are_restarted(tmp_path):
    agents = tmp_path / "agents"
    install_plists(agents, "team.tico.tico-bot", "team.tico.tico-connectors", "team.tico.tico-importers")
    launchctl, said = Launchctl(), []
    done = ru.restart_helpers({}, launchctl, agents, uid=501, say=said.append)
    assert done == ["connectors", "importers"]                  # close-calls has no plist; the bot job is not touched here
    assert launchctl.calls == [["launchctl", "kickstart", "-k", "gui/501/team.tico.tico-connectors"],
                               ["launchctl", "kickstart", "-k", "gui/501/team.tico.tico-importers"]]
    assert len(said) == 2 and "team.tico.tico-connectors" in said[0]


def test_an_installed_helper_that_is_not_loaded_is_bootstrapped(tmp_path):
    agents = tmp_path / "agents"
    install_plists(agents, "team.tico.tico-connectors")
    launchctl = Launchctl(fail_kickstart={"team.tico.tico-connectors"})
    done = ru.restart_helpers({}, launchctl, agents, uid=501, say=lambda line: None)
    assert done == ["connectors"] and launchctl.calls[-1] == ["launchctl", "bootstrap", "gui/501", str(agents / "team.tico.tico-connectors.plist")]


# -- a Linux checkout supervised by systemd user units -----------------------------------------------

SYSTEMD = {"TICO_SYSTEMD_UNIT": "tico-bot.service", "PATH": "/usr/bin", "HOME": "/home/ana"}


class Systemctl:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1 if cmd[-1] in self.fail else 0, "", "")


def test_the_update_restarts_the_runner_through_systemctl_when_a_unit_runs_it(monkeypatch):
    killed = []
    monkeypatch.setattr(ru.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    systemctl = Systemctl()
    ru.restart_runner(4242, env=SYSTEMD, run=systemctl)
    assert systemctl.calls == [["systemctl", "--user", "restart", "tico-bot.service"]] and killed == []


def test_a_failed_systemctl_falls_back_to_stopping_the_process(monkeypatch):
    killed = []

    def kill(pid, sig):
        killed.append(sig)
        if sig == 0:
            raise ProcessLookupError
    monkeypatch.setattr(ru.os, "kill", kill)
    ru.restart_runner(4242, env=SYSTEMD, run=Systemctl(fail={"tico-bot.service"}))
    assert killed[0] == ru.signal.SIGTERM                 # Restart=always starts it again


def test_only_installed_helper_units_are_restarted_through_systemctl(tmp_path):
    units = tmp_path / "units"
    units.mkdir()
    for name in ("tico-bot.service", "tico-connectors.service", "tico-importers.service"):
        (units / name).write_text("[Service]\n")
    systemctl, said = Systemctl(), []
    done = ru.restart_helpers_systemd(SYSTEMD, systemctl, unit_dir=units, say=said.append)
    assert done == ["connectors", "importers"]              # close-calls has no unit; the runner's own is not touched here
    assert systemctl.calls == [["systemctl", "--user", "restart", "tico-connectors.service"],
                               ["systemctl", "--user", "restart", "tico-importers.service"]]
    (units / "tico-acme-close-calls.service").write_text("[Service]\n")
    assert ru.restart_helpers_systemd({"TICO_SYSTEMD_UNIT": "tico-acme-bot.service"}, Systemctl(), unit_dir=units,
                                      say=lambda line: None) == ["close-calls"]
