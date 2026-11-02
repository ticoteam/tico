"""docker/updater.py in runner mode: the runner box's sidecar pulls the matching tico-runner tag, recreates the
runner, and puts the old image back when the new one does not turn healthy."""
import importlib.util
import os
import stat
import subprocess
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[2] / "docker/updater.py"


def load(monkeypatch, mode, tmp_path):
    for key, value in {"TICO_UPDATER_MODE": mode, "TICO_PROJECT_DIR": str(tmp_path),
                       "TICO_COMPOSE_FILE": "runner.compose.yaml" if mode == "runner" else "",
                       "TICO_UPDATER_TOKEN_FILE": str(tmp_path / "token"), "TICO_UPDATER_SELF": "never", "TICO_UPDATER_BUNDLE": "never"}.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("tico_updater_" + (mode or "server"), SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Docker:
    """Stands in for `docker`: records every command, and answers the two questions the updater asks."""

    def __init__(self, module, monkeypatch, healthy):
        self.calls, self.answers = [], list(healthy)
        monkeypatch.setattr(module, "docker_run", self.run)
        monkeypatch.setattr(module, "healthy", lambda seconds: self.answers.pop(0))
        monkeypatch.setattr(module, "running_image", lambda: ("sha256:old", "v0.1.0"))
        monkeypatch.setattr(module, "pinned_services", lambda version: [])
        monkeypatch.setattr(module, "check_switched", lambda version, release: None)

    def run(self, argv, env=None, **kw):
        self.calls.append((argv, (env or {}).get("TICO_TAG")))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def compose(self):
        return [(a[a.index("--project-directory") + 2:], tag) for a, tag in self.calls if a[:2] == ["docker", "compose"]]


def test_runner_mode_manages_the_runner_service_and_image(monkeypatch, tmp_path):
    updater = load(monkeypatch, "runner", tmp_path)
    assert (updater.SERVICE, updater.IMAGE) == ("runner", "ghcr.io/ticoteam/tico-runner")
    (tmp_path / ".env").write_text("TICO_URL=https://tico.example.com\n")
    docker = Docker(updater, monkeypatch, [True])
    updater.update("v0.2.0")
    assert updater.status["state"] == "healthy" and updater.status["to"] == "v0.2.0" and updater.status["from"] == "v0.1.0"
    assert docker.compose() == [(["pull", "runner"], "v0.2.0"),
                                (["up", "-d", "--no-deps", "--pull", "never", "runner"], "v0.2.0")]
    assert all(a[3] == str(tmp_path / "runner.compose.yaml") for a, _ in docker.calls if a[:2] == ["docker", "compose"])
    assert "TICO_TAG=v0.2.0" in (tmp_path / ".env").read_text()      # a later `docker compose up` stays on it


def test_the_runner_compose_gets_the_host_memory_default_unless_env_sets_one(monkeypatch, tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       33554432 kB\n")                     # 32 GiB: the same default the installer gives
    monkeypatch.setenv("TICO_RUNNER_MEMORY_RULE", str(SOURCE.parent / "runner-memory.sh"))
    monkeypatch.setenv("TICO_UPDATER_MEMINFO", str(meminfo))
    updater = load(monkeypatch, "runner", tmp_path)
    seen = []

    def run(argv, env=None, **kw):
        seen.append(env.get("TICO_RUNNER_MEMORY"))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(updater, "docker_run", run)
    (tmp_path / ".env").write_text("TICO_URL=https://tico.example.com\n")
    updater.compose("ps", "-q", "runner")
    assert seen[-1] == "28g"
    (tmp_path / ".env").write_text("TICO_URL=https://tico.example.com\nTICO_RUNNER_MEMORY=0\n")
    updater.compose("ps", "-q", "runner")
    assert seen[-1] is None                                                 # .env says 0: compose gets no override
    (tmp_path / ".env").write_text("TICO_URL=https://tico.example.com\nTICO_RUNNER_MEMORY=2g\n")
    updater.compose("ps", "-q", "runner")
    assert seen[-1] is None


def test_a_spaced_or_commented_setting_in_env_is_read_as_the_setting_it_is(monkeypatch, tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       33554432 kB\n")
    monkeypatch.setenv("TICO_RUNNER_MEMORY_RULE", str(SOURCE.parent / "runner-memory.sh"))
    monkeypatch.setenv("TICO_UPDATER_MEMINFO", str(meminfo))
    updater = load(monkeypatch, "runner", tmp_path)
    seen = []
    monkeypatch.setattr(updater, "docker_run", lambda argv, env=None, **kw: seen.append(env.get("TICO_RUNNER_MEMORY"))
                        or subprocess.CompletedProcess(argv, 0, "", ""))
    (tmp_path / ".env").write_text("TICO_URL=https://tico.example.com\nTICO_RUNNER_MEMORY = 2g\n")
    updater.compose("ps", "-q", "runner")
    assert seen[-1] is None                                                 # `NAME = value` is a setting
    for line in ("TICO_RUNNER_MEMORY: 0", "export TICO_RUNNER_MEMORY=0"):     # compose's other assignment forms
        (tmp_path / ".env").write_text("TICO_URL=https://tico.example.com\n" + line + "\n")
        updater.compose("ps", "-q", "runner")
        assert seen[-1] is None, line
    (tmp_path / ".env").write_text("TICO_URL=https://tico.example.com\n# TICO_RUNNER_MEMORY=2g\n")
    updater.compose("ps", "-q", "runner")
    assert seen[-1] == "28g"                                                # a commented line is no setting


def fake_docker(monkeypatch, updater, limit="0"):
    """Answers `compose ps` with one container and `docker inspect` with its memory limit; records every call."""
    calls = []

    def run(argv, env=None, **kw):
        calls.append(argv)
        out = "abc123\n" if "ps" in argv else limit + "\n" if "inspect" in argv else ""
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(updater, "docker_run", run)
    return calls


def test_a_runner_left_uncapped_by_an_older_updater_gets_the_default_live(monkeypatch, tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       33554432 kB\n")                     # 32 GiB: the default is 28g
    monkeypatch.setenv("TICO_RUNNER_MEMORY_RULE", str(SOURCE.parent / "runner-memory.sh"))
    monkeypatch.setenv("TICO_UPDATER_MEMINFO", str(meminfo))
    updater = load(monkeypatch, "runner", tmp_path)
    (tmp_path / ".env").write_text("TICO_URL=https://tico.example.com\n")
    calls = fake_docker(monkeypatch, updater, limit="0")
    updater.cap_running_runner()
    assert ["docker", "update", "--memory", "28g", "--memory-swap", "28g", "abc123"] in calls

    calls = fake_docker(monkeypatch, updater, limit="8589934592")          # someone set 8 GiB: that stays
    updater.cap_running_runner()
    assert not [a for a in calls if a[:2] == ["docker", "update"]]

    (tmp_path / ".env").write_text("TICO_URL=https://tico.example.com\nTICO_RUNNER_MEMORY=0\n")
    calls = fake_docker(monkeypatch, updater, limit="0")                   # .env says no limit: nothing to do
    updater.cap_running_runner()
    assert calls == []


def test_a_failed_live_cap_is_logged_and_the_updater_goes_on(monkeypatch, tmp_path, capsys):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       33554432 kB\n")
    monkeypatch.setenv("TICO_RUNNER_MEMORY_RULE", str(SOURCE.parent / "runner-memory.sh"))
    monkeypatch.setenv("TICO_UPDATER_MEMINFO", str(meminfo))
    updater = load(monkeypatch, "runner", tmp_path)
    (tmp_path / ".env").write_text("TICO_URL=https://tico.example.com\n")

    def broken(argv, env=None, **kw):
        raise subprocess.TimeoutExpired(argv, 5)

    monkeypatch.setattr(updater, "docker_run", broken)
    updater.cap_running_runner()                                            # must not raise
    assert "could not cap the runner's memory" in capsys.readouterr().out


def test_a_runner_that_does_not_turn_healthy_is_rolled_back(monkeypatch, tmp_path):
    updater = load(monkeypatch, "runner", tmp_path)
    (tmp_path / ".env").write_text("TICO_URL=https://tico.example.com\n")
    docker = Docker(updater, monkeypatch, [False, True])
    updater.update("v0.2.0")
    assert updater.status["state"] == "rolled_back" and "Went back to v0.1.0" in updater.status["message"]
    assert "the runner did not come up healthy" in updater.status["message"]
    assert (["docker", "tag", "sha256:old", "ghcr.io/ticoteam/tico-runner:v0.1.0"], None) in docker.calls
    assert docker.compose()[-1] == (["up", "-d", "--no-deps", "--pull", "never", "runner"], "v0.1.0")
    assert "TICO_TAG" not in (tmp_path / ".env").read_text()           # the failed version is not remembered


def test_the_token_is_the_supervisors_alone_on_every_start(monkeypatch, tmp_path):
    updater = load(monkeypatch, "runner", tmp_path)
    owners = []
    monkeypatch.setattr(updater.os, "chown", lambda path, uid, gid: owners.append((uid, gid)))
    (tmp_path / "token").write_text("old\n")
    (tmp_path / "token").chmod(0o644)                                  # what earlier versions left behind
    updater.ensure_token()
    assert stat.S_IMODE((tmp_path / "token").stat().st_mode) == 0o600 and owners == [(10002, 10002)]
    assert (tmp_path / "token").read_text() == "old\n"                 # kept, not rewritten
    (tmp_path / "token").unlink()
    updater.ensure_token()
    assert len((tmp_path / "token").read_text().strip()) == 64 and stat.S_IMODE((tmp_path / "token").stat().st_mode) == 0o600


def test_a_second_start_leaves_a_locked_token_alone(monkeypatch, tmp_path):
    # The updater is root without CAP_FOWNER: once the token is ticorun's, chmod on it fails. v0.2.10 and
    # v0.2.11 crashed on every start after the first because of that.
    updater = load(monkeypatch, "runner", tmp_path)
    (tmp_path / "token").write_text("t\n")
    (tmp_path / "token").chmod(0o600)
    real = updater.os.lstat
    monkeypatch.setattr(updater.os, "lstat", lambda path: os.stat_result((real(path).st_mode, *real(path)[1:4], 10002, *real(path)[5:])))
    def refuse(*args):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(updater.os, "chmod", refuse)
    monkeypatch.setattr(updater.os, "chown", refuse)
    updater.ensure_token()                                             # already locked down: nothing to do
    monkeypatch.setattr(updater.os, "lstat", real)
    updater.ensure_token()                                             # not locked and refused: logged, no crash


def test_a_downgrade_is_refused_and_an_upgrade_accepted(monkeypatch, tmp_path):
    updater = load(monkeypatch, "runner", tmp_path)
    Docker(updater, monkeypatch, [True])                               # running v0.1.0
    assert updater.older_than_running("v0.0.9") and updater.older_than_running("v0.0.99")
    assert not any(updater.older_than_running(v) for v in ("v0.1.0", "v0.1.1", "v1.0.0", "latest"))



class Daemon:
    """`docker` for the whole update, the runner's own checks included: every call must carry a time limit, and
    `hang` names the call that runs over it. Records the status each call was made under."""

    def __init__(self, module, monkeypatch, hang):
        import subprocess
        self.module, self.hang, self.calls, self.expired = module, hang, [], subprocess.TimeoutExpired
        monkeypatch.setattr(module, "docker_run", self.run)
        monkeypatch.setattr(module, "pinned_services", lambda version: [])
        monkeypatch.setattr(module, "POLL_SECONDS", 0)

    def run(self, argv, env=None, timeout=None, **kw):
        assert timeout, argv                                        # nothing the worker runs may hang it
        tag = (env or {}).get("TICO_TAG")
        self.calls.append((argv, tag, self.module.status["state"]))
        if self.hang(argv, tag):
            raise self.expired(argv, timeout)
        out = ""
        if "ps" in argv:
            out = "c1\n"
        elif argv[:2] == ["docker", "inspect"] and "{{.State.Health.Status}}" in argv:
            out = "healthy\n"
        elif argv[:2] == ["docker", "inspect"]:
            out = "sha256:old ghcr.io/ticoteam/tico-runner:v0.1.0\n"
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()


class NeverSettled(BaseException):
    """settle() catches every Exception and tries again, so a fake daemon stops a settle that would hold forever."""


class Moby:
    """A Docker daemon with containers, for what a killed client leaves behind. `up` resolves the runner's container
    and asks the daemon to stop it; once accepted, that stop runs to its end even if the client is killed (Moby
    stops with a context that ignores cancellation). `hang(argv, tag, n)` names the calls whose client is killed
    after the daemon accepted; their stop lands `lag` calls later. `n` counts the calls so far."""

    def __init__(self, module, monkeypatch, hang, lag=3, healthy_tags=("v0.1.0",)):
        import subprocess
        self.module, self.hang, self.lag, self.healthy_tags = module, hang, lag, healthy_tags
        self.expired, self.calls, self.n = subprocess.TimeoutExpired, [], 0
        self.containers = {"c1": {"tag": "v0.1.0", "running": True}}   # what the update starts from
        self.pending = []      # [container id, calls until it lands]: accepted stops nobody waited for
        self.idle_recreates = 0   # answered recreates that change nothing (a daemon that has not caught up)
        self.stopped = []      # containers an accepted stop stopped after the client was gone, with the status then
        self.ended = []        # (state, accepted stops still aimed at a container that exists) at each outcome
        self.restores = {}     # one-off id -> calls until its restore replaces the database (then it ends, --rm)
        self.restored = []     # (live service containers, status) when a restore replaced the database
        monkeypatch.setattr(module, "docker_run", self.run)
        monkeypatch.setattr(module, "pinned_services", lambda version: [])
        monkeypatch.setattr(module, "check_switched", lambda version, release: None)
        monkeypatch.setattr(module, "POLL_SECONDS", 0)
        monkeypatch.setattr(module, "SETTLE_RETRY_SECONDS", 0)
        monkeypatch.setattr(module, "HEALTH_SECONDS", 1)
        set_status = module.set_status

        def watched(**fields):
            set_status(**fields)
            if fields.get("state") in ("healthy", "rolled_back", "failed"):
                self.ended.append((fields["state"], [c for c, _ in self.pending if c in self.containers]))
        monkeypatch.setattr(module, "set_status", watched)
        save_status, self.saves = module.save_status, 0

        def counted():
            self.saves += 1
            if self.saves > 500:
                raise NeverSettled(module.status["message"])
            save_status()
        monkeypatch.setattr(module, "save_status", counted)

    def live(self):
        return [c for c, v in self.containers.items() if v["running"] and not v.get("oneoff")]

    def land(self):
        for cid in list(self.restores):
            self.restores[cid] -= 1
            if self.restores[cid] <= 0 and self.containers.get(cid, {}).get("running"):
                del self.restores[cid], self.containers[cid]
                self.restored.append((self.live(), self.module.status["state"]))
        for item in list(self.pending):
            item[1] -= 1
            if item[1] <= 0:
                self.pending.remove(item)
                if item[0] in self.containers and self.containers[item[0]]["running"]:
                    self.containers[item[0]]["running"] = False
                    self.stopped.append((item[0], self.module.status["state"]))

    def run(self, argv, env=None, timeout=None, **kw):
        assert timeout, argv                                        # nothing the worker runs may hang it
        self.n += 1
        self.land()
        tag = (env or {}).get("TICO_TAG")
        self.calls.append((argv, tag, self.module.status["state"]))
        args = argv[argv.index("--project-directory") + 2:] if "--project-directory" in argv else argv[1:]
        out = ""
        if args[:1] == ["run"]:                                     # a --rm one-off: the snapshot restore
            cid = "r%d" % self.n
            self.containers[cid] = {"tag": tag, "running": True, "oneoff": True}
            self.restores[cid] = self.lag
            if self.hang(argv, tag, self.n):
                raise self.expired(argv, timeout)                   # the client is killed; the container is not
            del self.restores[cid], self.containers[cid]
            self.restored.append((self.live(), self.module.status["state"]))
        elif args[:1] == ["stop"]:
            for c in self.live():
                self.containers[c]["running"] = False
        elif argv[:2] == ["docker", "rm"]:
            if self.containers.get(argv[2], {}).get("running"):
                return type("R", (), {"returncode": 1, "stdout": "", "stderr": "cannot remove a running container"})()
            self.containers.pop(argv[2], None)
        elif args[:1] == ["up"]:
            current = [c for c in self.containers if not self.containers[c].get("oneoff")]
            if "--force-recreate" not in args and any(self.containers[c]["tag"] == tag and self.containers[c]["running"]
                                                      for c in current):
                pass                                                # up to date: nothing to recreate
            else:
                if "--force-recreate" in args and self.idle_recreates:
                    self.idle_recreates -= 1
                    return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
                accepted = [[c, self.lag] for c in current]
                if self.hang(argv, tag, self.n):
                    self.pending += accepted                        # the client is killed; the daemon is not
                    raise self.expired(argv, timeout)
                for c in current:                                   # an answered recreate: stopped and removed
                    del self.containers[c]
                self.pending = [p for p in self.pending if p[0] in self.containers]
                self.containers["c%d" % (self.n + 100)] = {"tag": tag, "running": True}
        elif args[:1] == ["ps"]:
            out = "".join(c + "\n" for c, v in self.containers.items()
                          if "-a" in args or (v["running"] and not v.get("oneoff")))
        elif argv[:2] == ["docker", "inspect"] and "{{.State.Status}}" in argv[3]:
            out = "".join("%s %s %s\n" % (c, v.get("state") or ("running" if v["running"] else "exited"),
                                          "True" if v.get("oneoff") else "")
                          for c, v in self.containers.items() if c in argv[4:])
        elif argv[:2] == ["docker", "inspect"] and "{{.State.Health.Status}}" in argv:
            box = self.containers.get(argv[-1])
            out = "healthy\n" if box and box["running"] and box["tag"] in self.healthy_tags else "unhealthy\n"
        elif argv[:2] == ["docker", "inspect"]:
            tag = self.containers[argv[-1]]["tag"]
            out = "sha256:%s ghcr.io/ticoteam/tico-runner:%s\n" % (tag, tag)
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()


def test_a_switch_that_runs_over_holds_work_until_docker_confirms_the_old_version(monkeypatch, tmp_path):
    """The runner holds new work while the update says pulling, restarting or uncertain. The new version's `up`
    is killed after Docker accepted the stop of the running runner: the update may not say how it ended (and let
    the runner claim again) while that stop could still land on a runner that is working."""
    updater = load(monkeypatch, "runner", tmp_path)
    docker = Moby(updater, monkeypatch, lambda argv, tag, n: "up" in argv and tag == "v0.2.0")
    updater.update("v0.2.0")
    assert updater.status["state"] == "rolled_back", updater.status
    assert "did not finish within" in updater.status["message"] and "Docker then confirmed the runner on v0.1.0" in updater.status["message"]
    assert docker.ended == [("rolled_back", [])]                     # no accepted stop left that could land
    assert [c["tag"] for c in docker.containers.values() if c["running"]] == ["v0.1.0"]
    assert {state for _, state in docker.stopped} <= {"restarting", "uncertain"}
    assert any(state == "uncertain" for _, _, state in docker.calls)
    assert any("--force-recreate" in argv and tag == "v0.1.0" for argv, tag, _ in docker.calls)


def test_a_rollback_that_runs_over_holds_work_until_docker_confirms_the_old_version(monkeypatch, tmp_path):
    """The case a killed client cannot settle: the new version is unhealthy, and the rollback's `up` is killed after
    Docker accepted the stop of the new container. Saying `failed` then would let the runner claim work that the
    accepted stop then kills. It stays uncertain, holding, until a recreate Docker answered is the only container."""
    updater = load(monkeypatch, "runner", tmp_path)
    docker = Moby(updater, monkeypatch, lambda argv, tag, n: "up" in argv and tag == "v0.1.0"
                  and "--force-recreate" not in argv, lag=40)
    updater.update("v0.2.0")
    assert updater.status["state"] == "rolled_back", updater.status
    assert updater.status["message"].startswith("the runner did not come up healthy")
    assert docker.ended == [("rolled_back", [])]
    assert [c["tag"] for c in docker.containers.values() if c["running"]] == ["v0.1.0"]
    assert not docker.stopped                                        # the force-recreate removed what it aimed at
    first_uncertain = next(i for i, (_, _, state) in enumerate(docker.calls) if state == "uncertain")
    assert all(state in ("idle", "pulling", "restarting") for _, _, state in docker.calls[:first_uncertain])


def test_a_settling_recreate_that_runs_over_is_tried_again_and_the_hold_stays(monkeypatch, tmp_path):
    updater = load(monkeypatch, "runner", tmp_path)
    tries = []

    def hang(argv, tag, n):
        if "up" in argv and tag == "v0.1.0" and "--force-recreate" not in argv:
            return True
        if "--force-recreate" in argv:
            tries.append(n)
            return len(tries) == 1                                   # the first settling recreate is killed too
        return False
    docker = Moby(updater, monkeypatch, hang, lag=2)
    updater.update("v0.2.0")
    assert len(tries) == 2 and updater.status["state"] == "rolled_back", updater.status
    assert docker.ended == [("rolled_back", [])]
    assert {state for _, state in docker.stopped} <= {"restarting", "uncertain"}   # landed while holding
    assert (tmp_path / updater.STATUS_FILE).exists()


def test_an_answered_recreate_that_leaves_the_old_container_does_not_settle(monkeypatch, tmp_path):
    """Settled needs the recreate's own container to be the only one: an answer alone is not enough."""
    updater = load(monkeypatch, "runner", tmp_path)
    docker = Moby(updater, monkeypatch, lambda argv, tag, n: "up" in argv and tag == "v0.1.0"
                  and "--force-recreate" not in argv, lag=60)
    docker.idle_recreates = 2
    updater.update("v0.2.0")
    recreates = [state for argv, _, state in docker.calls if "--force-recreate" in argv]
    assert recreates == ["uncertain"] * 3 and updater.status["state"] == "rolled_back"
    assert docker.ended == [("rolled_back", [])] and not docker.stopped


def test_an_uncertain_outcome_survives_a_restart_and_is_settled_by_the_next_updater(monkeypatch, tmp_path):
    import json
    import time
    updater = load(monkeypatch, "runner", tmp_path)
    (tmp_path / updater.STATUS_FILE).write_text(json.dumps({
        "state": "uncertain", "from": "v0.1.0", "to": "v0.2.0", "settle_tag": "v0.1.0",
        "settle_outcome": "docker compose up did not finish within 600 seconds", "message": "Not settled"}))
    docker = Moby(updater, monkeypatch, lambda argv, tag, n: False)
    docker.containers = {"c1": {"tag": "v0.2.0", "running": True}}
    docker.pending = [["c1", 50]]
    updater.load_status()
    deadline = time.time() + 10
    while updater.status["state"] == "uncertain" and time.time() < deadline:
        time.sleep(0.01)
    assert updater.status["state"] == "rolled_back" and docker.ended == [("rolled_back", [])]
    assert [c["tag"] for c in docker.containers.values()] == ["v0.1.0"]


def test_an_updater_stopped_during_the_switch_settles_it_when_it_starts_again(monkeypatch, tmp_path):
    """The updater container itself stops mid-switch (its compose client with it); Docker's accepted stop has not
    landed. What it saved before the switch makes the next updater hold and settle, not answer an old outcome."""
    import json
    import time
    updater = load(monkeypatch, "runner", tmp_path)
    saved = []

    class Stopped(BaseException):
        pass

    def hang(argv, tag, n):
        if "up" in argv and tag == "v0.2.0":
            saved.append(json.loads((tmp_path / updater.STATUS_FILE).read_text()))
            raise Stopped()                                          # the process is gone: nothing after this runs
        return False
    docker = Moby(updater, monkeypatch, hang)
    try:
        updater.update("v0.2.0")
    except Stopped:
        pass
    assert saved[0]["state"] == "restarting" and saved[0]["settle_tag"] == "v0.1.0"
    docker.pending = [["c1", 50]]                                    # the switch's stop, accepted, still to land
    restarted = load(monkeypatch, "runner", tmp_path)
    again = Moby(restarted, monkeypatch, lambda argv, tag, n: False)
    again.containers, again.pending = docker.containers, docker.pending
    restarted.load_status()
    deadline = time.time() + 10
    while restarted.status["state"] in ("restarting", "uncertain") and time.time() < deadline:
        time.sleep(0.01)
    assert restarted.status["state"] == "rolled_back" and again.ended == [("rolled_back", [])]
    assert "the updater stopped while it was switching to v0.2.0" in restarted.status["message"]
    assert [c["tag"] for c in again.containers.values()] == ["v0.1.0"]


def test_an_update_is_refused_while_the_last_one_is_unsettled(monkeypatch, tmp_path):
    import io
    import json
    from types import SimpleNamespace
    updater = load(monkeypatch, "runner", tmp_path)
    (tmp_path / "token").write_text("secret\n")
    updater.status.update(state="uncertain", message="Not settled")
    before = dict(updater.status)
    # This guards the refusal, not socket scheduling under a parallel test run.
    handler = object.__new__(updater.Handler)
    handler.path = "/update"
    body = json.dumps({"version": "v0.2.0"}).encode()
    handler.headers = {"Authorization": "Bearer secret", "Content-Length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    replies = []
    handler.reply = lambda code, body: replies.append((code, body))

    def must_not_start(*args, **kwargs):
        raise AssertionError("an unsettled update must not start another update")

    monkeypatch.setattr(updater, "threading", SimpleNamespace(Thread=must_not_start))
    handler.do_POST()
    assert replies == [(409, {"error": "an update is already running", **before})]
    assert updater.status == before


def test_a_runner_that_cannot_be_read_is_not_updated_and_says_so(monkeypatch, tmp_path):
    updater = load(monkeypatch, "runner", tmp_path)
    docker = Daemon(updater, monkeypatch, lambda argv, tag: argv[:2] == ["docker", "inspect"])
    updater.update("v0.2.0")
    assert updater.status["state"] == "failed" and updater.status["to"] == "v0.2.0"
    assert updater.status["message"].startswith("Not updated: could not read the running runner")
    assert not [argv for argv, _, _ in docker.calls if "pull" in argv or "up" in argv]


def test_a_compose_call_that_runs_over_is_killed_and_cannot_finish_later(monkeypatch, tmp_path):
    import time
    import pytest
    updater = load(monkeypatch, "runner", tmp_path)
    marker = tmp_path / "switched"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # docker compose is a plugin the docker CLI runs as its child: the switch happens in a grandchild
    (bin_dir / "docker").write_text(f"#!/bin/sh\n(sleep 0.5; touch {marker}) &\nwait\n")
    (bin_dir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    with pytest.raises(RuntimeError, match="did not finish within"):
        updater.compose("up", "-d", "runner", timeout=0.1)
    time.sleep(0.8)
    assert not marker.exists()
