"""The integrated Rust listener keeps HTTP, streaming and screen sockets on the same origin."""

import httpx
import pytest

from backend.tests.helpers.screen_relay_contract import service  # noqa: F401
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
            f"/api/v2/computers/{r['runner_id']}/screen/sessions", json={"control": False}, headers=headers()
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
