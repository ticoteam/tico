"""The integrated Rust listener keeps HTTP, streaming and screen sockets on the same origin."""
import contextlib
import os
import subprocess
import sys

import httpx
import pytest

from backend.tests.test_screen_relay_contract import Service, service  # noqa: F401
from backend.tests.test_api import headers

pytestmark = pytest.mark.slow


def test_same_origin_gateway_preserves_http_streams_socket_paths_and_hides_control(service):
    with httpx.Client(base_url=service.public_origin, timeout=5) as client:
        for path in ("/control/v1/hello", "/_tico/screen/v1/authorize"):
            assert client.post(path, json={}).status_code == 403
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v2/tasks").status_code == 401
        assert client.get("/api/v2/tasks", headers=headers()).status_code == 200
        with client.stream("GET", "/api/v2/events", headers=headers()) as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            assert next(stream.iter_lines()) == "retry: 1000"
        r = service.computer()
        response = client.post(f"/api/v2/runners/{r['runner_id']}/screen/sessions",
                               json={"control": False}, headers=headers())
        assert response.status_code == 200, response.text
        opened = response.json()
        direct = service.relay_url
        service.relay_url = service.public_origin
        try:
            with service.websocket(opened["ws_path"], origin=service.origin) as browser:
                with service.websocket(service.runner_path(opened), token=r["token"]) as runner:
                    browser.send(b"same public path"); assert runner.recv(timeout=5) == b"same public path"
                    runner.send(b"same bearer"); assert browser.recv(timeout=5) == b"same bearer"
        finally:
            service.relay_url = direct


def test_standard_startup_owns_rust_and_shutdown_releases_the_public_port(tmp_path):
    reserved = Service.bind()
    port = reserved.getsockname()[1]
    reserved.close()
    (tmp_path / "registry").mkdir()
    origin = f"http://127.0.0.1:{port}"
    env = {**os.environ, "TICO_DB": str(tmp_path / "hub.sqlite"), "TICO_REGISTRY_DIR": str(tmp_path / "registry"),
           "TICO_BLOB_DIR": str(tmp_path / "blobs"), "TICO_SCHEDULER": "0", "TICO_UPDATE_CHECK": "off",
           "TICO_TELEMETRY": "off", "TICO_SUPPORT": "off", "TICO_PUBLIC_URL": origin,
           "TICO_AUTH_PROXY": "", "TICO_OWNER_EMAIL": "ana@acme.example",
           "TICO_LOCAL_OWNER_TOKEN": "fictional-owner-token", "TICO_LOCAL_OWNER_EMAIL": "ana@acme.example"}
    with (tmp_path / "server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, "-m", "backend.server", "--port", str(port)],
                                   env=env, stdout=log, stderr=log)
        try:
            def healthy():
                with contextlib.suppress(httpx.HTTPError):
                    return httpx.get(origin + "/healthz", timeout=.5).status_code == 200
                assert process.poll() is None, (tmp_path / "server.log").read_text()
                return False
            Service.eventually(healthy, seconds=20)
            assert httpx.get(origin + "/control/v1/hello").status_code == 403
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(5)
        # A clean stop terminates its own Rust child; no listener survives on the former public port.
        listener = Service.bind(port)
        listener.close()
