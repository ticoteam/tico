"""A runner follows its server's release: the checkout update against a temp repository, and the runner's decisions."""
import subprocess

import pytest

from runner import release_update as ru


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
    def __init__(self, healthy=(True,), install_error=""):
        self.restarts, self.installs, self.checks = 0, [], []
        self.answers, self.install_error = list(healthy), install_error

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
    result = ru.apply(checkout, version, state, install=calls.install, restart=calls.restart, healthy=calls.healthy, **kw)
    return result, calls


def head(repo):
    return sh(repo, "git", "rev-parse", "HEAD")


def test_update_switches_to_the_servers_tag_not_main(repos):
    public, checkout, state = repos
    result, calls = run_apply(checkout, state)
    assert result["state"] == "healthy" and ru.checkout_release(checkout) == "0.2.0"
    assert head(checkout) == sh(public, "git", "rev-parse", "v0.2.0") != head(public)   # main moved on; the runner did not
    assert calls.restarts == 1 and calls.installs == [(sh(checkout, "git", "rev-parse", "v0.1.0"), head(checkout))]
    assert calls.checks == [head(checkout)]
    assert ru.read_status(state)["state"] == "healthy" and ru.read_status(state)["target"] == "0.2.0"


def test_a_checkout_with_changes_is_refused_and_left_alone(repos):
    public, checkout, state = repos
    (checkout / "app.py").write_text("my local edit")
    before = head(checkout)
    result, calls = run_apply(checkout, state)
    assert result["state"] == "blocked" and "1 changed file" in result["error"]
    assert (checkout / "app.py").read_text() == "my local edit" and head(checkout) == before
    assert calls.restarts == 0 and ru.read_status(state)["state"] == "blocked"
    assert sh(checkout, "git", "branch", "--show-current") == "main"


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


# -- the runner's side -----------------------------------------------------------------------------

class Server:
    def __init__(self, version="0.2.0"):
        self.version, self.error = version, None

    def get(self, path):
        assert path == "runners/desired"
        if self.error:
            raise self.error
        return {"version": self.version, "min_runner": "0.1.0"}


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def follower(checkout, state, server=None, config=None, env=None, launched=None, supervised=True, **kw):
    launched = [] if launched is None else launched
    f = ru.Follower(config or {}, state, server or Server(), root=checkout, env={"TICO_RUNNER_KIND": "linux", **(env or {})},
                    supervised=lambda: supervised, launch=lambda *a: launched.append(a), clock=Clock(), wall=Clock(), **kw)
    f.poll()
    return f, launched


def test_a_behind_runner_waits_for_quiet_then_starts_one_update(repos):
    public, checkout, state = repos
    f, launched = follower(checkout, state)
    assert (f.pending, f.state, f.release) == ("0.2.0", "waiting", "0.1.0")
    assert f.blocks_claims(active=True) is False and launched == []      # a turn is running: keep working
    assert f.blocks_claims(active=False) is True and len(launched) == 1  # quiet: go, and claim nothing meanwhile
    assert launched[0][1] == "0.2.0" and f.state == "updating"
    assert f.blocks_claims(active=False) is True and len(launched) == 1
    assert f.fields() == {"release": "0.1.0", "kind": f.kind, "update": {"state": "updating", "target": "0.2.0", "error": ""}}


@pytest.mark.slow
def test_a_refused_update_is_reported_and_not_retried_until_the_release_changes(repos):
    public, checkout, state = repos
    ru.write_status(state, state="blocked", target="0.2.0", error="the checkout has 2 changed files", at=0.0)
    server = Server()
    f, launched = follower(checkout, state, server)
    assert (f.state, f.error, f.pending) == ("blocked", "the checkout has 2 changed files", None)
    assert not f.blocks_claims(False) and launched == []
    server.version = "0.3.0"
    f.poll()
    assert f.pending == "0.3.0"                                          # a new release is a new attempt
    f.wall.t = ru.RETRY_AFTER_S + 1
    server.version = "0.2.0"
    f.poll()
    assert f.pending == "0.2.0"                                          # and so is enough time


class Sidecar:
    def __init__(self, status=None, url="http://updater:8080"):
        self.url, self._status, self.started = url, status, []

    def configured(self):
        return bool(self.url)

    def status(self):
        return self._status

    def start(self, version):
        self.started.append(version)


DOCKER = {"TICO_RUNNER_KIND": "docker", "TICO_VERSION": "v0.1.0"}


@pytest.mark.slow
def test_a_docker_runner_asks_its_sidecar_when_quiet(repos):
    public, checkout, state = repos
    sidecar = Sidecar()
    f, _ = follower(checkout, state, env=DOCKER, sidecar=sidecar, config={"self_update": False})
    assert f.kind == "docker" and f.release == "0.1.0" and f.pending == "0.2.0"
    assert f.blocks_claims(active=False) and sidecar.started == ["0.2.0"]


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


def test_under_systemd_the_update_starts_as_a_unit_of_its_own(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    ru.spawn("/srv/tico", "0.2.0", tmp_path, 4242, env=SYSTEMD, run=run)
    cmd = calls[0]
    assert cmd[:3] == ["systemd-run", "--user", "--quiet"] and "--unit=tico-update-4242" in cmd   # not in the runner's cgroup
    assert "--setenv=TICO_SYSTEMD_UNIT=tico-bot.service" in cmd and "--working-directory=/srv/tico" in cmd
    assert cmd[cmd.index("-m") + 1] == "runner.release_update" and cmd[cmd.index("--pid") + 1] == "4242"
    with pytest.raises(OSError):
        ru.spawn("/srv/tico", "0.2.0", tmp_path, 1, env=SYSTEMD, run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", ""))


@pytest.mark.slow
@pytest.mark.parametrize("docker", [True])
def test_a_disk_failure_retries_as_soon_as_space_frees(repos, monkeypatch, docker):
    from types import SimpleNamespace
    _, checkout, state = repos
    free = [100]
    monkeypatch.setattr(ru.shutil, "disk_usage", lambda path: SimpleNamespace(free=free[0]))
    error = "no space left on device"
    sidecar = Sidecar({"state": "failed", "to": "v0.2.0", "message": error, "disk_free": 100})
    if not docker:
        ru.write_status(state, state="failed", target="0.2.0", error=error, at=0, disk_free=100)
    f, _ = follower(checkout, state, env=DOCKER if docker else None, sidecar=sidecar)
    assert f.state == "failed" and f.pending is None
    assert "Not enough disk space" in f.error
    f.poll()
    assert f.pending is None
    free[0] = 200
    f.poll()
    assert f.pending == "0.2.0"
