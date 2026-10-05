"""docker/updater.py in runner mode: the runner box's sidecar pulls the matching tico-runner tag, recreates the
runner, and puts the old image back when the new one does not turn healthy."""
import importlib.util
import os
import stat
from pathlib import Path

import pytest

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
        monkeypatch.setattr(module.subprocess, "run", self.run)
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


def test_the_installers_override_rides_along_with_every_compose_call(monkeypatch, tmp_path):
    # `install.sh --runner --server-network` writes it; an update must keep the runner on the server's network.
    updater = load(monkeypatch, "runner", tmp_path)
    seen = []
    monkeypatch.setattr(updater.subprocess, "run", lambda argv, **kw: seen.append(argv) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    updater.compose("ps")
    assert "runner.override.yaml" not in " ".join(seen[-1])
    (tmp_path / "runner.override.yaml").write_text("services: {}\n")
    updater.compose("ps")
    assert seen[-1][seen[-1].index(str(tmp_path / "runner.override.yaml")) - 1] == "-f"


def test_a_downgrade_is_refused_and_an_upgrade_accepted(monkeypatch, tmp_path):
    updater = load(monkeypatch, "runner", tmp_path)
    Docker(updater, monkeypatch, [True])                               # running v0.1.0
    assert updater.older_than_running("v0.0.9") and updater.older_than_running("v0.0.99")
    assert not any(updater.older_than_running(v) for v in ("v0.1.0", "v0.1.1", "v1.0.0", "latest"))


def test_the_runner_compose_keeps_the_socket_in_the_sidecar():
    import yaml
    compose = yaml.safe_load((SOURCE.parent / "runner.compose.yaml").read_text())
    sockets = [name for name, svc in compose["services"].items()
               if any("docker.sock" in str(v) for v in svc.get("volumes", []))]
    assert sockets == ["updater"]
    assert compose["services"]["runner"]["environment"]["TICO_UPDATER_URL"] == "http://updater:8080"
    assert "runner-control:/control:ro" in compose["services"]["runner"]["volumes"]


def test_from_is_the_release_the_server_reported_not_the_last_updates(monkeypatch, tmp_path):
    updater = load(monkeypatch, "runner", tmp_path)
    docker = Docker(updater, monkeypatch, [True])
    monkeypatch.setattr(updater, "running_image", lambda: ("sha256:old", "latest"))   # a tag that names no release
    updater.status.update(state="healthy", **{"from": "v0.2.19", "to": "v0.2.20"})
    updater.update("v0.2.21", running="0.2.20")
    assert (updater.status["from"], updater.status["to"], updater.status["state"]) == ("v0.2.20", "v0.2.21", "healthy")
    # No report (an older server): the image tag, as before. A report that is no version is not believed.
    docker.answers[:] = [True]
    updater.update("v0.2.22", running="dev")
    assert updater.status["from"] == "latest"
    assert updater.release_name("0.2.20") == "v0.2.20" and updater.release_name("latest") == "" and updater.release_name(None) == ""
    assert updater.older_than_running("v0.2.19", "0.2.20") and not updater.older_than_running("v0.2.21", "0.2.20")


def test_a_space_failure_reports_the_cause_and_available_space(monkeypatch, tmp_path):
    from types import SimpleNamespace
    updater = load(monkeypatch, "runner", tmp_path)
    monkeypatch.setattr(updater.shutil, "disk_usage", lambda path: SimpleNamespace(free=42))
    updater.set_status(state="failed", message="failed to register layer: no space left on device")
    assert "Not enough disk space" in updater.status["message"]
    assert "docker image prune -a" in updater.status["message"]
    assert updater.status["disk_free"] == 42
    updater.save_status()
    updater.status.clear()
    updater.load_status()
    assert updater.status["disk_free"] == 42
