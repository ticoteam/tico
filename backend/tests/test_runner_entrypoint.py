"""docker/runner-entrypoint.sh follows a server that moved (TICO_URL changed), without Docker.

Runs the real script as a plain user with HOME in a temp dir, a loopback HTTP server standing in for a Tico
server, and a fake runner binary (TICO_RUNNER_BIN) that exits at once in place of `tico-runner run`.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "docker" / "runner-entrypoint.sh"
TOKEN = "runner-token-kept"

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0 or not shutil.which("jq"),
    reason="the script takes its root path when run as root; needs jq",
)


def serve(accepts):
    """A loopback server: 200 for the runner's token when `accepts`, 401 otherwise."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            ok = accepts and self.headers.get("Authorization") == f"Bearer {TOKEN}"
            self.send_response(200 if ok else 401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"[]" if ok else b'{"error":"unauthorized"}')

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def closed_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"   # nothing listens here any more


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def fake_runner(tmp_path):
    fake = tmp_path / "tico-runner"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    return fake


def enrolled(home, url):
    config = home / "runner.json"
    config.write_text(json.dumps({"url": url, "token": TOKEN, "self_update": False, "name": "box"}))
    config.chmod(0o600)
    return config


def run_join(home, fake_runner, url):
    env = {
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "PYTHONPATH": str(ROOT),
        "TICO_RUNNER_BIN": str(fake_runner),
    }
    return subprocess.run(["bash", str(SCRIPT), "join", "--url", url], env=env, capture_output=True,
                          text=True, timeout=60)


def test_a_server_that_moved_is_followed_and_the_rest_of_the_file_is_kept(home, fake_runner):
    old, _ = serve(True)
    new, new_url = serve(True)
    config = enrolled(home, f"http://127.0.0.1:{old.server_address[1]}")
    result = run_join(home, fake_runner, new_url + "/")   # a trailing slash on the given url is not a different url
    assert result.returncode == 0, result.stderr
    saved = json.loads(config.read_text())
    assert saved == {"url": new_url, "token": TOKEN, "self_update": False, "name": "box"}
    assert config.stat().st_mode & 0o777 == 0o600
    assert not list(home.glob("runner.json.new.*"))
    assert f"the server moved: now {new_url} (was http://127.0.0.1:{old.server_address[1]})" in result.stdout


@pytest.mark.parametrize("answers", ["rejects", "does not answer"])
def test_a_new_url_that_refuses_or_is_silent_leaves_the_file_alone(home, fake_runner, answers):
    old, old_url = serve(True)
    other = serve(False)[1] if answers == "rejects" else closed_url()
    config = enrolled(home, old_url)
    before = config.read_bytes()
    result = run_join(home, fake_runner, other)
    assert result.returncode == 0, result.stderr   # the runner goes on with the saved url
    assert config.read_bytes() == before
    assert f"the given --url {other} was not used" in result.stdout
    assert f"still using {old_url}" in result.stdout
    assert "the server moved" not in result.stdout
