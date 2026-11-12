"""The integrated Rust listener keeps HTTP, streaming and screen sockets on the same origin."""

import contextlib
import os
import subprocess
import sys
import threading
import time

import httpx
import pytest

from backend.tests.helpers.screen_relay_contract import Service, gateway_origin, relay_binary, service  # noqa: F401
from backend.tests.helpers.api import headers

pytestmark = pytest.mark.slow


def test_same_origin_gateway_preserves_http_streams_socket_paths_and_hides_control(service):
    with httpx.Client(base_url=service.public_origin, timeout=5) as client:
        for path in ("/control/v1/hello", "/_tico/screen/v1/authorize"):
            refused = client.post(path, json={})
            # The refusal never reads the body, so the gateway must not leave the connection open for a next request.
            assert (refused.status_code, refused.headers.get("connection")) == (403, "close")
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v2/tasks").status_code == 401
        assert client.get("/api/v2/tasks", headers=headers()).status_code == 200
        with client.stream("GET", "/api/v2/events", headers=headers()) as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            assert next(stream.iter_lines()) == "retry: 1000"
        r = service.computer()
        response = client.post(
            f"/api/v2/runners/{r['runner_id']}/screen/sessions", json={"control": False}, headers=headers()
        )
        assert response.status_code == 200, response.text
        opened = response.json()
        direct = service.relay_url
        service.relay_url = service.public_origin
        try:
            with service.websocket(opened["ws_path"], origin=service.origin) as browser:
                with service.websocket(service.runner_path(opened), token=r["token"]) as runner:
                    browser.send(b"same public path")
                    assert runner.recv(timeout=5) == b"same public path"
                    runner.send(b"same bearer")
                    assert browser.recv(timeout=5) == b"same bearer"
        finally:
            service.relay_url = direct


class PortTaken(Exception):
    """backend.server's relay could not bind the public port it was given."""


def standard_startup(root, port):
    root.mkdir()
    (root / "registry").mkdir()
    origin = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "TICO_DB": str(root / "hub.sqlite"),
        "TICO_REGISTRY_DIR": str(root / "registry"),
        "TICO_BLOB_DIR": str(root / "blobs"),
        "TICO_SCHEDULER": "0",
        "TICO_UPDATE_CHECK": "off",
        "TICO_TELEMETRY": "off",
        "TICO_SUPPORT": "off",
        "TICO_PUBLIC_URL": origin,
        "TICO_AUTH_PROXY": "",
        "TICO_OWNER_EMAIL": "ana@acme.example",
        "TICO_LOCAL_OWNER_TOKEN": "fictional-owner-token",
        "TICO_LOCAL_OWNER_EMAIL": "ana@acme.example",
    }
    with (root / "server.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "backend.server", "--port", str(port)], env=env, stdout=log, stderr=log
        )
        try:

            def running():
                # Checked before any answer on the port is trusted: if another test took the port, that answer is
                # theirs. The log is read once the process has exited, so it holds the relay's last words.
                if process.poll() is None:
                    return
                text = (root / "server.log").read_text()
                if "could not start" in text:
                    raise PortTaken(text)
                raise AssertionError(text)

            def healthy():
                running()
                with contextlib.suppress(httpx.HTTPError):
                    return httpx.get(origin + "/healthz", timeout=0.5).status_code == 200
                return False

            Service.eventually(healthy, seconds=20)
            hello = httpx.get(origin + "/control/v1/hello").status_code
            running()
            assert hello == 403
            # On a stop, the public port answers 200 until it refuses connects, never the relay's 502 for a server
            # that no longer listens: Caddy holds a refused connect, but passes a 502 through to the visitor.
            process.terminate()
            gone = time.monotonic() + 1.5
            while True:
                try:
                    answer = httpx.get(origin + "/healthz", timeout=0.5).status_code
                except httpx.ConnectError:
                    break
                assert answer == 200, f"the public port answered {answer} while the server stopped"
                assert time.monotonic() < gone, "the public port still accepts connects"
                time.sleep(0.02)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(5)
        # A clean stop terminates its own Rust child; no listener survives on the former public port.
        listener = Service.bind(port)
        listener.close()
