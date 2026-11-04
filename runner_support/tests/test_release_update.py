"""A checkout follows its server's release: the update process (python -m runner_support.release_update) against a temp repository."""
import fcntl
import os
from pathlib import Path
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
def repos(tmp_path, monkeypatch):
    """A public repo with releases 0.1.0 and 0.2.0 (0.2.0 changes the dependencies) plus a checkout on main at 0.1.0."""
    from runner_support import git_locks
    monkeypatch.setattr(git_locks, "OWNED_ROOTS", (tmp_path,))
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


def instruction_links(checkout):
    commit(checkout, "CLAUDE.md", "Repository rules\n")
    commit(checkout, "templates/employee-repo/AGENT.md", "Bot rules\n")
    for name, target in [("AGENTS.md", "CLAUDE.md"), ("templates/employee-repo/AGENTS.md", "AGENT.md"),
                         ("templates/employee-repo/CLAUDE.md", "AGENT.md")]:
        (checkout / name).symlink_to(target)
    commit(checkout, "notes.txt", "keep the local branch\n")


@pytest.mark.slow
def test_flattened_instruction_links_do_not_block_a_release_update(repos):
    _, checkout, state = repos
    instruction_links(checkout)
    # A repair interrupted by shutdown leaves only ignored Git metadata, never another dirty file.
    (checkout / ".git/tico-instruction-link-0123456789abcdef0123456789abcdef").symlink_to("CLAUDE.md")
    before = head(checkout)
    for name, text in [("AGENTS.md", "Repository rules\n"), ("templates/employee-repo/AGENTS.md", "Bot rules\n"),
                       ("templates/employee-repo/CLAUDE.md", "Bot rules\n")]:
        (checkout / name).unlink()
        (checkout / name).write_text(text)
    ru.repair_instruction_links(checkout, lambda *args: ru.git(checkout, *args))
    assert sh(checkout, "git", "status", "--porcelain") == ""
    assert (checkout / "AGENTS.md").is_symlink()
    result, calls = run_apply(checkout, state)
    assert result["state"] == "healthy" and calls.restarts == 1
    assert sh(checkout, "git", "rev-parse", "main") == before  # unpublished work retains its branch


@pytest.mark.slow
def test_instruction_edits_and_deliberately_staged_copies_stay_and_are_named(repos):
    _, checkout, state = repos
    instruction_links(checkout)
    edited = checkout / "AGENTS.md"
    edited.unlink()
    edited.write_text("A bot's new rules\n")
    staged = checkout / "templates/employee-repo/CLAUDE.md"
    staged.unlink()
    staged.write_text("Bot rules\n")
    sh(checkout, "git", "add", "templates/employee-repo/CLAUDE.md")
    index = (checkout / ".git/index").read_bytes()
    before = head(checkout)
    result, calls = run_apply(checkout, state)
    assert result["state"] == "blocked" and calls.restarts == 0
    assert "AGENTS.md" in result["error"] and "templates/employee-repo/CLAUDE.md" in result["error"]
    assert edited.read_text() == "A bot's new rules\n" and not edited.is_symlink()
    assert staged.read_text() == "Bot rules\n" and not staged.is_symlink()
    assert (checkout / ".git/index").read_bytes() == index and head(checkout) == before


@pytest.mark.slow
def test_instruction_link_repair_preserves_edits_to_its_target_and_external_targets(repos):
    _, checkout, _ = repos
    instruction_links(checkout)
    external = checkout.parent / "private.md"
    external.write_text("Outside the checkout\n")
    link = checkout / "nested/AGENTS.md"
    link.parent.mkdir()
    link.symlink_to(external)
    commit(checkout, "notes.txt", "another commit\n")
    edited = checkout / "AGENTS.md"
    edited.unlink()
    edited.write_text("Repository rules\n")
    link.unlink()
    link.write_text(external.read_text())
    # Commit only the fixture links; keep the real target edit uncommitted.
    (checkout / "CLAUDE.md").write_text("Bot target edit\n")
    ru.repair_instruction_links(checkout, lambda *args: ru.git(checkout, *args))
    assert not edited.is_symlink() and edited.read_text() == "Repository rules\n"
    assert not link.is_symlink() and link.read_text() == external.read_text()
    assert (checkout / "CLAUDE.md").read_text() == "Bot target edit\n"


@pytest.mark.slow
def test_updater_retries_an_abandoned_index_lock_but_keeps_fresh_and_open_locks(repos):
    from runner_support import git_locks
    _, checkout, _ = repos
    for name, command in [("index.lock", ("add", "app.py")), ("config.lock", ("config", "--local", "repair.test", "yes"))]:
        lock = checkout / ".git" / name
        lock.write_text("recent")
        assert ru.git(checkout, *command).returncode != 0 and lock.exists()
        old = time.time() - git_locks.OLD_S * 2
        os.utime(lock, (old, old))
        with lock.open():
            assert ru.git(checkout, *command).returncode != 0 and lock.exists()
        assert ru.git(checkout, *command).returncode == 0 and not lock.exists()


def test_inaccessible_bot_descriptors_block_lock_recovery_only_for_git_or_unknown_processes(tmp_path, monkeypatch):
    from runner_support import git_locks
    process = tmp_path / "123"
    process.mkdir()
    lock = tmp_path / "config.lock"
    lock.write_text("old")
    original = Path.iterdir

    def processes(path):
        if path == Path("/proc"):
            return iter([process])
        if path == process / "fd":
            raise PermissionError("another OS user")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", processes)
    (process / "comm").write_text("model-host\n")
    assert git_locks.unused(lock, lock.stat())
    assert not git_locks.unused(lock, lock.stat(), strict=True), "an unreadable model process may be an instruction writer"
    (process / "comm").write_text("git\n")
    assert not git_locks.unused(lock, lock.stat())
    (process / "comm").unlink()
    # A vanished process no longer holds a file.
    assert git_locks.unused(lock, lock.stat())


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


def runner_release(public, checkout):
    """Release 0.3.0 changes the runner's sources, so its binary is a new build."""
    commit(public, "runner-rs/src/main.rs", "fn main() {}")
    sh(public, "git", "tag", "v0.3.0")


def test_a_release_whose_new_runner_binary_is_not_published_yet_waits_without_moving(repos):
    public, checkout, state = repos
    runner_release(public, checkout)
    before, asked = head(checkout), []
    result, calls = run_apply(checkout, state, version="0.3.0", which=lambda name: None,
                              listed=lambda version: asked.append(version) or False)
    assert result["state"] == "waiting" and "does not have this computer's runner binary yet" in result["error"]
    assert asked == ["0.3.0"] and head(checkout) == before and calls.restarts == 0 and calls.prepared == []
    assert ru.read_status(state)["state"] == "waiting"
    # Once release.yml adds the binary, the next try moves.
    result, calls = run_apply(checkout, state, version="0.3.0", which=lambda name: None, listed=lambda version: True)
    assert result["state"] == "healthy" and ru.checkout_release(checkout) == "0.3.0"


def test_a_runner_binary_that_never_comes_is_a_failed_update_after_an_hour(repos):
    public, checkout, state = repos
    runner_release(public, checkout)
    now = [1000.0]
    wait = dict(version="0.3.0", which=lambda name: None, listed=lambda version: False, clock=lambda: now[0])
    assert run_apply(checkout, state, **wait)[0]["state"] == "waiting"
    now[0] += ru.BINARY_WAIT_S - 1
    assert run_apply(checkout, state, **wait)[0]["state"] == "waiting"     # the wait counts from the first try
    now[0] += 1
    result, calls = run_apply(checkout, state, **wait)
    assert result["state"] == "failed" and "after 60 minutes" in result["error"] and calls.restarts == 0


def test_only_a_changed_runner_without_cargo_asks_whether_its_binary_is_published(repos):
    public, checkout, state = repos
    asked = []
    listed = lambda version: asked.append(version) or False
    result, _ = run_apply(checkout, state, which=lambda name: None, listed=listed)      # 0.2.0: same runner sources
    assert result["state"] == "healthy" and asked == []
    runner_release(public, checkout)
    result, _ = run_apply(checkout, state, version="0.3.0", which=lambda name: "/usr/bin/cargo", listed=listed)
    assert result["state"] == "healthy" and asked == []                                  # cargo builds one


def test_a_release_whose_checksums_cannot_be_read_updates_as_before(repos):
    public, checkout, state = repos
    runner_release(public, checkout)
    result, _ = run_apply(checkout, state, version="0.3.0", which=lambda name: None, listed=lambda version: None)
    assert result["state"] == "healthy"


def test_the_prepare_step_is_a_new_process_of_the_new_tree(tmp_path, monkeypatch):
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw["cwd"]))
        return subprocess.CompletedProcess(cmd, 1, "", "Tico runner: no Rust runner for this checkout: cargo is not on PATH\n")
    assert ru.prepare_binary(tmp_path, run=run) == "" and not seen      # a tree from before the shim gets its own
    monkeypatch.setattr(ru.sys, "executable", "/opt/env-python/bin/python3")
    # Going back to a release from before runner_support/: its engine is still runner/engine.py.
    (tmp_path / "runner").mkdir()
    (tmp_path / "runner" / "engine.py").write_text("")
    assert "cargo is not on PATH" in ru.prepare_binary(tmp_path, run=run)
    assert seen.pop() == (["/opt/env-python/bin/python3", "-m", "runner.engine", "prepare"], str(tmp_path))
    (tmp_path / "runner_support").mkdir()
    (tmp_path / "runner_support" / "engine.py").write_text("")
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


@pytest.mark.slow
def test_instruction_backup_keeps_different_link_text_and_names_it_in_health(repos, capsys):
    _, checkout, state = repos
    instruction_links(checkout)
    path = checkout / "AGENTS.md"
    path.unlink()
    path.write_text("CLAUDE.md")
    result, calls = run_apply(checkout, state)
    backups = list((checkout / ".git/tico-instruction-backups").rglob("AGENTS.md.tico-backup-*"))
    assert len(backups) == 1 and backups[0].read_text() == "CLAUDE.md"
    assert path.is_symlink() and path.read_text() == "Repository rules\n"
    assert result["state"] == "blocked" and calls.restarts == 0
    assert str(backups[0].relative_to(checkout)) in ru.read_status(state)["error"]
    assert "bot instruction edit to review" in capsys.readouterr().err


@pytest.mark.slow
def test_open_instruction_writer_lands_in_backup_and_is_reported_on_next_update(repos):
    _, checkout, state = repos
    instruction_links(checkout)
    path = checkout / "AGENTS.md"
    path.unlink()
    path.write_text("Repository rules\n")
    with path.open("r+") as writer:
        assert not ru.repair_instruction_links(checkout, lambda *args: ru.git(checkout, *args))
        backups = list((checkout / ".git/tico-instruction-backups").rglob("AGENTS.md.tico-backup-*"))
        assert len(backups) == 1
        writer.seek(0)
        writer.write("Late bot edit\n")
        writer.truncate()
        writer.flush()
    assert backups[0].read_text() == "Late bot edit\n"
    assert path.read_text() == "Repository rules\n"
    result, _ = run_apply(checkout, state)
    assert result["state"] == "blocked" and backups[0].name in result["error"]


def test_lock_outside_runner_root_is_never_touched(tmp_path, monkeypatch):
    from runner_support import git_locks
    checkout = tmp_path / "outside"
    metadata = checkout / ".git"
    metadata.mkdir(parents=True)
    lock = metadata / "index.lock"
    lock.write_text("unpublished")
    old = time.time() - git_locks.OLD_S * 2
    os.utime(lock, (old, old))
    monkeypatch.setattr(git_locks, "OWNED_ROOTS", (tmp_path / "runner",))
    assert not git_locks.recover(checkout, f"Unable to create '{lock}': File exists")
    assert lock.read_text() == "unpublished"
    assert not (metadata / "tico-lock-recovery").exists()


def test_closed_git_lock_is_owned_by_cwd_arguments_or_environment(tmp_path, monkeypatch):
    from runner_support import git_locks
    checkout = tmp_path / "repo"
    metadata = checkout / ".git"
    metadata.mkdir(parents=True)
    lock = metadata / "index.lock"
    lock.write_text("closed by git, still owned")
    process = tmp_path / "123"
    process.mkdir()
    (process / "comm").write_text("git\n")
    (process / "fd").mkdir()
    original = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda p: iter([process]) if p == Path("/proc") else original(p))
    cases = [(checkout, ["git", "commit"], []),
             (tmp_path, ["git", "-C", str(checkout), "commit"], []),
             (tmp_path, ["git", "--git-dir", str(metadata), "commit"], []),
             (tmp_path, ["git", "--git-dir=" + str(metadata), "commit"], []),
             (tmp_path, ["git", "commit"], ["GIT_DIR=" + str(metadata)])]
    for cwd, args, environ in cases:
        (process / "cwd").unlink(missing_ok=True)
        (process / "cwd").symlink_to(cwd)
        (process / "cmdline").write_bytes("\0".join(args).encode() + b"\0")
        (process / "environ").write_bytes("\0".join(environ).encode())
        assert not git_locks.unused(lock, lock.stat(), (checkout, metadata))
    (process / "cmdline").write_bytes(b"git\0status\0")
    (process / "environ").write_bytes(b"")
    assert git_locks.unused(lock, lock.stat(), (checkout, metadata))
    (process / "environ").unlink()
    assert not git_locks.unused(lock, lock.stat(), (checkout, metadata))
    (process / "stat").write_text("123 (git) Z 1 0 0\n")
    assert git_locks.unused(lock, lock.stat(), (checkout, metadata))
    (process / "stat").write_text("123 (git) S 1 0 0\n")
    assert not git_locks.unused(lock, lock.stat(), (checkout, metadata))


def test_recovery_sidecar_serializes_and_keeps_the_stale_inode(tmp_path, monkeypatch):
    from runner_support import git_locks
    metadata = tmp_path / ".git"
    metadata.mkdir()
    lock = metadata / "index.lock"
    lock.write_text("preserved")
    monkeypatch.setattr(git_locks, "OWNED_ROOTS", (tmp_path,))
    error = f"Unable to create '{lock}': File exists"
    old = time.time() - 10 * 60
    os.utime(lock, (old, old))
    assert not git_locks.recover(tmp_path, error)
    old = time.time() - git_locks.OLD_S * 2
    os.utime(lock, (old, old))
    inode = lock.stat().st_ino
    with (metadata / "tico-lock-recovery").open("w") as sidecar:
        fcntl.flock(sidecar, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not git_locks.recover(tmp_path, error)
    assert git_locks.recover(tmp_path, error)
    stale = list(metadata.glob("index.lock.stale-*"))
    assert len(stale) == 1 and stale[0].stat().st_ino == inode and stale[0].read_text() == "preserved"


def test_recovery_race_restores_the_new_lock_without_discarding_it(tmp_path, monkeypatch):
    from runner_support import git_locks
    lock = tmp_path / "index.lock"
    lock.write_text("abandoned")
    before = lock.stat()
    rename = Path.rename

    def replace_before_rename(path, destination):
        rename(path, tmp_path / "original")
        path.write_text("new writer's lock")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", replace_before_rename)
    with pytest.raises(git_locks.RecoveryConflict, match="Git lock changed during recovery"):
        git_locks.quarantine(lock, before)
    assert lock.read_text() == "new writer's lock"
    assert (tmp_path / "original").read_text() == "abandoned"


def test_two_recoveries_cannot_move_each_others_lock(tmp_path, monkeypatch):
    from runner_support import git_locks
    metadata = tmp_path / ".git"
    metadata.mkdir()
    lock = metadata / "index.lock"
    lock.write_text("old lock")
    old = time.time() - git_locks.OLD_S * 2
    os.utime(lock, (old, old))
    monkeypatch.setattr(git_locks, "OWNED_ROOTS", (tmp_path,))
    inspecting, release = threading.Event(), threading.Event()

    def unused(*args):
        inspecting.set()
        assert release.wait(10)
        return True

    monkeypatch.setattr(git_locks, "unused", unused)
    error = f"Unable to create '{lock}': File exists"
    results = []
    first = threading.Thread(target=lambda: results.append(git_locks.recover(tmp_path, error)))
    first.start()
    try:
        assert inspecting.wait(10)
        assert not git_locks.recover(tmp_path, error)
        assert lock.read_text() == "old lock"
    finally:
        release.set()
        first.join(10)
    assert results == [True]
    lock.write_text("new writer's lock")
    assert not git_locks.recover(tmp_path, error)
    assert lock.read_text() == "new writer's lock"
    assert len(list(metadata.glob("index.lock.stale-*"))) == 1


def test_a_raced_replacement_keeps_both_names_when_the_original_path_is_taken(tmp_path, monkeypatch):
    from runner_support import git_locks
    lock = tmp_path / "index.lock"
    lock.write_text("abandoned")
    before = lock.stat()
    lock.rename(tmp_path / "original")
    lock.write_text("raced replacement")
    link = os.link

    def occupied(source, destination, **kwargs):
        Path(destination).write_text("newly acquired")
        return link(source, destination, **kwargs)

    monkeypatch.setattr(os, "link", occupied)
    with pytest.raises(git_locks.RecoveryConflict, match="inspect"):
        git_locks.quarantine(lock, before)
    assert lock.read_text() == "newly acquired"
    backups = list(tmp_path.glob("index.lock.stale-*"))
    assert len(backups) == 1 and backups[0].read_text() == "raced replacement"


def test_instruction_backup_cleanup_never_follows_a_metadata_symlink(tmp_path):
    checkout, outside = tmp_path / "repo", tmp_path / "outside"
    metadata = checkout / ".git"
    metadata.mkdir(parents=True)
    outside.mkdir()
    (checkout / "AGENTS.md").write_text("same bytes")
    backup = outside / "AGENTS.md.tico-backup-1"
    backup.write_text("same bytes")
    (metadata / "tico-instruction-backups").symlink_to(outside)
    assert "cannot inspect instruction backups" in ru.instruction_backup_note(checkout, metadata)
    assert backup.read_text() == "same bytes"


def test_instruction_writer_closing_during_fd_scan_cannot_lose_its_edit(tmp_path, monkeypatch):
    from runner_support import git_locks
    metadata = tmp_path / ".git"
    saved = metadata / "tico-instruction-backups"
    saved.mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("same bytes")
    backup = saved / "AGENTS.md.tico-backup-1"
    backup.write_text("same bytes")

    def write_and_close(path, before, **kwargs):
        path.write_text("late edit, writer now closed")
        return True

    monkeypatch.setattr(git_locks, "unused", write_and_close)
    assert backup.name in ru.instruction_backup_note(tmp_path, metadata)
    assert backup.read_text() == "late edit, writer now closed"


def test_macos_closed_git_owner_and_unreadable_ps_output_block_recovery(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from runner_support import git_locks
    checkout, elsewhere = tmp_path / "repo", tmp_path / "elsewhere"
    checkout.mkdir()
    elsewhere.mkdir()
    lock = checkout / "index.lock"
    lock.write_text("closed but owned")
    current = {"cwd": checkout, "command": "git commit"}

    def run(command, **kwargs):
        if command[:2] == ["ps", "-axo"]:
            return SimpleNamespace(returncode=0, stdout="123 /usr/bin/git\n", stderr="")
        if command[0] == "ps":
            return SimpleNamespace(returncode=0, stdout=current["command"], stderr="")
        if "-d" in command:
            return SimpleNamespace(returncode=0, stdout=f"p123\nn{current['cwd']}\n", stderr="")
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")

    monkeypatch.setattr(git_locks.sys, "platform", "darwin")
    monkeypatch.setattr(git_locks.subprocess, "run", run)
    assert not git_locks.unused(lock, lock.stat(), (checkout,))
    current["cwd"] = elsewhere
    assert git_locks.unused(lock, lock.stat(), (checkout,))
    assert not git_locks.unused(lock, lock.stat(), strict=True)
    current["command"] = f"git --git-dir={checkout} commit"
    assert not git_locks.unused(lock, lock.stat(), (checkout,))
    current["command"] = ""
    assert not git_locks.unused(lock, lock.stat(), (checkout,))
