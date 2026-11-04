"""A real listening API on a free port, for tests that need a server a process can reach: `live` is its URL."""

import socket
import threading
import time

import pytest
import uvicorn


def block_outside_network(monkeypatch, reason):
    """External effects off: only loopback names resolve, so a command or tool that would reach a provider (Tico HQ,
    a model gateway) fails as unreachable instead of sending anything."""
    real = socket.getaddrinfo

    def local_only(host, *args, **kwargs):
        if host in ("localhost", "127.0.0.1", "::1"):
            return real(host, *args, **kwargs)
        raise socket.gaierror(-2, reason)
    monkeypatch.setattr(socket, "getaddrinfo", local_only)


@pytest.fixture
def live(api):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(api.app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "test API did not become ready within 30 seconds"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()
