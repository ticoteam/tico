"""Helpers that other tests share, moved out of test_screen_relay_contract.py so they import no test file."""

import contextlib
from dataclasses import replace
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from websockets.sync.client import connect
from backend.app import create_app
from backend.tests.helpers import api as api_helpers
from backend.tests.helpers.api import headers, post, runner
from backend.tests.helpers.screen import offer


KEY = "fictional-relay-key-for-isolated-tests-only"


def relay_binary():
    """The relay under test: TICO_SCREEN_RELAY_BIN, else this checkout's release build if it is the current sources'."""
    from backend import screen_binary

    binary = os.environ.get("TICO_SCREEN_RELAY_BIN", "")
    if not binary:
        built = (
            Path(os.environ.get("CARGO_TARGET_DIR") or screen_binary.ROOT / "relay-rs" / "target")
            / "release"
            / "tico-screen-relay"
        )
        if screen_binary.current(built, screen_binary.source_id()):
            return str(built)
    if not binary or not Path(binary).is_file():
        raise RuntimeError(
            "Build relay-rs (cd relay-rs && cargo build --release) or set TICO_SCREEN_RELAY_BIN to its executable"
        )
    return binary


def gateway_origin(process):
    """The public gateway's origin: the line the relay prints right after its own address, with the port it bound."""
    line = process.stdout.readline()
    if not line.startswith("screen gateway listening on "):
        raise RuntimeError("Relay did not open its gateway: " + line)
    return "http://" + line.strip().split()[-1]


class Service:
    def __init__(self, root, binary=None):
        self.root, self.binary = root, binary
        self.process, self.thread, self.server, self.initial = None, None, None, None
        self.listener = self.bind()
        self.origin = f"http://127.0.0.1:{self.listener.getsockname()[1]}"
        self.relay_url = self.origin
        self.start_rust()
        self.settings = replace(
            api_helpers.api_settings(root),
            public_url=self.origin,
            screen_relay_url=self.relay_url,
            screen_relay_key=KEY,
        )
        # Reuse the production-facing fictional API fixture without sharing its application or database.
        original = api_helpers.api_settings
        api_helpers.api_settings = lambda _: self.settings
        try:
            self.initial = api_helpers.api.__wrapped__(root, None)
            self.api = next(self.initial)
        finally:
            api_helpers.api_settings = original
        self.start_http()

    @staticmethod
    def bind(port=0):
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(128)
        return listener

    def start_rust(self):
        binary = self.binary or relay_binary()
        # Both listeners bind port 0 and the relay reports the ports it took, so no port is picked here and released for
        # another test to take. A restart keeps the relay's own port, the one the API was given: if another test took it
        # once the old relay exited, the start is tried again.
        port = 0 if self.relay_url == self.origin else int(self.relay_url.rsplit(":", 1)[1])
        for attempt in range(10):
            self.process = subprocess.Popen(
                [str(binary)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env={
                    **os.environ,
                    "TICO_SCREEN_RELAY_KEY": KEY,
                    "TICO_SCREEN_PYTHON_URL": self.origin,
                    "TICO_SCREEN_RELAY_BIND": f"127.0.0.1:{port}",
                    "TICO_SCREEN_PUBLIC_BIND": "127.0.0.1:0",
                },
            )
            line = self.process.stdout.readline()
            if line.startswith("screen relay listening on "):
                break
            self.process.wait(5)
            if "AddrInUse" not in line or attempt == 9:
                raise RuntimeError("Relay did not start: " + line)
            time.sleep(0.2)
        self.relay_url = "http://" + line.strip().split()[-1]
        self.public_origin = gateway_origin(self.process)

    def start_http(self):
        self.server = uvicorn.Server(uvicorn.Config(self.api.app, lifespan="off", log_level="error", access_log=False))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.listener]}, daemon=True)
        self.thread.start()
        self.eventually(lambda: self.server.started)

    def stop_http(self):
        if self.server:
            self.server.should_exit = True
            self.thread.join(10)
            assert not self.thread.is_alive(), "owned API server did not stop"
            self.listener.close()

    def restart_python(self):
        port = int(self.origin.rsplit(":", 1)[1])
        self.stop_http()
        if self.initial:
            self.initial.close()
            self.initial = None
        else:
            self.api.__exit__(None, None, None)
        self.settings = replace(self.settings, test_identities=dict(self.settings.test_identities))
        self.api = TestClient(create_app(self.settings)).__enter__()
        # stop_http released the port: another test may take it before this bind, so the bind is tried again.
        for attempt in range(50):
            try:
                self.listener = self.bind(port)
                break
            except OSError:
                if attempt == 49:
                    raise
                time.sleep(0.1)
        self.start_http()

    def restart_rust(self):
        self.process.terminate()
        self.process.wait(5)
        self.start_rust()
        self.eventually(
            lambda: (
                self.api.app.state.store.screen_sessions.healthy
                and self.api.app.state.store.screen_sessions.boot == self.control("hello", get=True)["boot"]
            )
        )

    def control(self, op, get=False, **body):
        with httpx.Client(base_url=self.relay_url, headers={"Authorization": "Bearer " + KEY}, timeout=4) as http:
            response = http.get("/control/v1/hello") if get else http.post("/control/v1/" + op, json=body)
        response.raise_for_status()
        return response.json()

    def websocket(self, path, token=None, origin=None, **options):
        return connect(
            self.relay_url.replace("http://", "ws://") + path,
            additional_headers=headers(token) if token else None,
            origin=origin,
            open_timeout=5,
            close_timeout=1,
            max_size=16 << 20,
            **options,
        )

    def computer(self, mode="control"):
        r = runner(self.api)
        offer(self.api, r)
        post(self.api, f"computers/{r['runner_id']}/screen", {"mode": mode})
        return r

    def opened(self, r, control=False):
        return post(self.api, f"computers/{r['runner_id']}/screen/sessions", {"control": control})

    @staticmethod
    def runner_path(opened):
        return f"/api/v2/runners/me/screen/{opened['session_id']}/ws"

    @staticmethod
    def eventually(predicate, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError("condition did not become true within its deadline")

    def close(self):
        try:
            self.stop_http()
        finally:
            try:
                if self.initial:
                    self.initial.close()
                elif hasattr(self, "api"):
                    self.api.__exit__(None, None, None)
            finally:
                if hasattr(self, "listener"):
                    self.listener.close()
                if self.process and self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(5)


@contextlib.contextmanager
def running_service(root, binary=None):
    service = Service.__new__(Service)
    service.process = service.server = service.initial = None
    try:
        service.__init__(root, binary)
        yield service
    finally:
        service.close()


@pytest.fixture
def service(tmp_path):
    with running_service(tmp_path) as service:
        yield service
