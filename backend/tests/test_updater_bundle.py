"""docker/updater.py moves the compose bundle with the image: verified against SHA256SUMS, replaced atomically,
kept for rollback, and never touching .env."""
import hashlib
import importlib.util
import io
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "docker/updater.py"


def make_bundle(files):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return raw.getvalue()


@pytest.fixture
def release_server():
    """A fake GitHub releases host: /download/<tag>/<asset> from a dict, plus the latest-release API."""
    assets = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = assets.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_port

    def publish(tag, files, tamper=False):
        bundle = make_bundle(files)
        name = "tico-bundle-%s.tar.gz" % tag
        digest = hashlib.sha256(bundle + (b"x" if tamper else b"")).hexdigest()
        assets["/download/%s/%s" % (tag, name)] = bundle
        assets["/download/%s/SHA256SUMS" % tag] = ("%s  %s\n" % (digest, name)).encode()

    yield base, assets, publish
    server.shutdown()


def load(monkeypatch, tmp_path, base, mode="server"):
    for key, value in {"TICO_UPDATER_MODE": mode, "TICO_PROJECT_DIR": str(tmp_path / "project"),
                       "TICO_COMPOSE_FILE": "runner.compose.yaml" if mode == "runner" else "",
                       "TICO_UPDATER_TOKEN_FILE": str(tmp_path / "token"), "TICO_UPDATER_SELF": "never", "TICO_RELEASES_URL": base,
                       "TICO_LATEST_URL": base + "/latest.json"}.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "project").mkdir(exist_ok=True)
    spec = importlib.util.spec_from_file_location("tico_updater_bundle_" + mode, SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Docker:
    def __init__(self, module, monkeypatch, healthy):
        self.calls, self.answers, self.compose_at_up = [], list(healthy), []
        monkeypatch.setattr(module.subprocess, "run", self.run)
        monkeypatch.setattr(module, "healthy", lambda seconds: self.answers.pop(0))
        monkeypatch.setattr(module, "running_image", lambda: ("sha256:old", "v0.1.0"))
        monkeypatch.setattr(module, "pinned_services", lambda version: [])
        monkeypatch.setattr(module, "check_switched", lambda version, release: None)
        self.module = module

    def run(self, argv, env=None, **kw):
        self.calls.append(argv)
        if "up" in argv:  # what compose.yaml said at the moment of each `up`
            self.compose_at_up.append((Path(self.module.PROJECT) / (self.module.COMPOSE_FILE or "compose.yaml")).read_text())
        out = "server\nslack\nupdater\n" if argv[-2:] == ["config", "--services"] else ""
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()


def install(project, mode="server"):
    (project / ".env").write_text("TICO_TAG=v0.1.0\nTICO_COMPANY_NAME=Acme\n")
    if mode == "server":
        (project / "compose.yaml").write_text("old compose\n")
        (project / "docker").mkdir(exist_ok=True)
        (project / "docker/runner.compose.yaml").write_text("old runner\n")
    else:
        (project / "runner.compose.yaml").write_text("old runner\n")


NEW = {"compose.yaml": "new compose\n", ".env.example": "new example\n", "docker/runner.compose.yaml": "new runner\n",
       ".env": "TICO_TAG=evil\n", "VERSION": "0.2.0\n"}


def test_update_replaces_the_bundle_and_never_env(monkeypatch, tmp_path, release_server):
    base, _, publish = release_server
    publish("v0.2.0", NEW)
    updater = load(monkeypatch, tmp_path, base)
    project = tmp_path / "project"
    install(project)
    docker = Docker(updater, monkeypatch, [True])
    updater.update("v0.2.0")
    assert updater.status["state"] == "healthy", updater.status
    assert (project / "compose.yaml").read_text() == "new compose\n"
    assert (project / ".env.example").read_text() == "new example\n"
    assert (project / "docker/runner.compose.yaml").read_text() == "new runner\n"
    assert (project / ".bundle-version").read_text() == "v0.2.0\n"
    assert (project / ".env").read_text() == "TICO_COMPANY_NAME=Acme\nTICO_TAG=v0.2.0\n"   # only TICO_TAG moved
    assert (project / ".bundle-previous/files/compose.yaml").read_text() == "old compose\n"
    assert set(docker.compose_at_up) == {"new compose\n"}          # containers came up from the new file
    assert not list(project.rglob("*.tico-new"))
    ups = [c for c in docker.calls if "up" in c]
    assert any(c[-1] == "slack" for c in ups) and not any(c[-1] == "updater" for c in ups)


def test_a_checksum_mismatch_is_refused_and_changes_nothing(monkeypatch, tmp_path, release_server):
    base, _, publish = release_server
    publish("v0.2.0", NEW, tamper=True)
    updater = load(monkeypatch, tmp_path, base)
    project = tmp_path / "project"
    install(project)
    docker = Docker(updater, monkeypatch, [True])
    updater.update("v0.2.0")
    assert updater.status["state"] == "failed" and "checksum mismatch" in updater.status["message"]
    assert (project / "compose.yaml").read_text() == "old compose\n"
    assert (project / ".env").read_text() == "TICO_TAG=v0.1.0\nTICO_COMPANY_NAME=Acme\n"
    assert docker.calls == []                                     # no pull, no restart


def test_an_unsafe_bundle_is_refused(monkeypatch, tmp_path, release_server):
    base, _, publish = release_server
    publish("v0.2.0", {"../evil": "x", "compose.yaml": "new\n"})
    updater = load(monkeypatch, tmp_path, base)
    install(tmp_path / "project")
    Docker(updater, monkeypatch, [True])
    updater.update("v0.2.0")
    assert updater.status["state"] == "failed" and "unsafe" in updater.status["message"]
    assert (tmp_path / "project/compose.yaml").read_text() == "old compose\n"


def test_rollback_restores_image_and_bundle(monkeypatch, tmp_path, release_server):
    base, _, publish = release_server
    publish("v0.2.0", {**NEW, "brand-new.txt": "x\n"})
    updater = load(monkeypatch, tmp_path, base)
    project = tmp_path / "project"
    install(project)
    docker = Docker(updater, monkeypatch, [False, True])
    updater.update("v0.2.0")
    assert updater.status["state"] == "rolled_back" and "Went back to v0.1.0" in updater.status["message"]
    assert (project / "compose.yaml").read_text() == "old compose\n"
    assert (project / "docker/runner.compose.yaml").read_text() == "old runner\n"
    assert not (project / "brand-new.txt").exists() and not (project / ".bundle-version").exists()
    assert (project / ".env").read_text() == "TICO_TAG=v0.1.0\nTICO_COMPANY_NAME=Acme\n"
    assert ["docker", "tag", "sha256:old", "ghcr.io/ticoteam/tico:v0.1.0"] in docker.calls
    assert docker.compose_at_up[-1] == "old compose\n"            # the old image came back on the old file
