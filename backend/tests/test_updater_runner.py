"""docker/updater.py in runner mode: the runner box's sidecar pulls the matching tico-runner tag, recreates the
runner, and puts the old image back when the new one does not turn healthy."""
import importlib.util
import os
import stat
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


def test_a_switch_that_runs_over_is_rolled_back_before_the_update_says_how_it_ended(monkeypatch, tmp_path):
    """The runner holds new work while the update says pulling or restarting: the outcome must come after the last
    call that could restart the runner, the rollback's included."""
    updater = load(monkeypatch, "runner", tmp_path)
    docker = Daemon(updater, monkeypatch, lambda argv, tag: "up" in argv and tag == "v0.2.0")
    updater.update("v0.2.0")
    assert updater.status["state"] == "rolled_back", updater.status
    assert "did not finish within" in updater.status["message"] and "Went back to v0.1.0" in updater.status["message"]
    ups = [i for i, (argv, _, _) in enumerate(docker.calls) if "up" in argv]
    assert [docker.calls[i][1] for i in ups] == ["v0.2.0", "v0.1.0"]
    assert ups[-1] < max(i for i, (argv, _, _) in enumerate(docker.calls) if "{{.State.Health.Status}}" in argv)
    assert {state for _, _, state in docker.calls} <= {"idle", "pulling", "restarting"}   # nothing said until then


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
