"""The real Rust screen WebSocket and lifecycle contract.

These start only owned loopback services on allocated ports and use fictional identities/SQLite.
Build relay-rs first (`cd relay-rs && cargo build --release`), then run this file with -m slow -n 0. The binary is
TICO_SCREEN_RELAY_BIN, else the checkout's own relay-rs/target/release build when it matches the sources.
"""
import contextlib
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

from backend.app import create_app
from backend.store import Problem
from backend.tests import test_api as api_helpers
from backend.tests.test_api import get, headers, post, runner
from backend.tests.test_screen import offer

pytestmark = pytest.mark.slow
KEY = "fictional-relay-key-for-isolated-tests-only"


def relay_binary():
    """The relay under test: TICO_SCREEN_RELAY_BIN, else this checkout's release build if it is the current sources'."""
    from backend import screen_binary
    binary = os.environ.get("TICO_SCREEN_RELAY_BIN", "")
    if not binary:
        built = Path(os.environ.get("CARGO_TARGET_DIR") or screen_binary.ROOT / "relay-rs" / "target") / "release" / "tico-screen-relay"
        if screen_binary.current(built, screen_binary.source_id()):
            return str(built)
    if not binary or not Path(binary).is_file():
        raise RuntimeError("Build relay-rs (cd relay-rs && cargo build --release) or set TICO_SCREEN_RELAY_BIN to its executable")
    return binary


class Service:
    def __init__(self, root, binary=None):
        self.root, self.binary = root, binary
        self.process, self.thread, self.server, self.initial = None, None, None, None
        self.listener = self.bind()
        self.origin = f"http://127.0.0.1:{self.listener.getsockname()[1]}"
        self.relay_url = self.origin
        self.start_rust()
        self.settings = replace(api_helpers.api_settings(root), public_url=self.origin,
                                screen_relay_url=self.relay_url, screen_relay_key=KEY)
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
        port = 0 if self.relay_url == self.origin else int(self.relay_url.rsplit(":", 1)[1])
        if not hasattr(self, "public_origin"):
            reserved = self.bind()
            self.public_origin = f"http://127.0.0.1:{reserved.getsockname()[1]}"
            reserved.close()
        self.process = subprocess.Popen([str(binary)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={**os.environ, "TICO_SCREEN_RELAY_KEY": KEY, "TICO_SCREEN_PYTHON_URL": self.origin,
                 "TICO_SCREEN_RELAY_BIND": f"127.0.0.1:{port}",
                 "TICO_SCREEN_PUBLIC_BIND": self.public_origin.removeprefix("http://")})
        line = self.process.stdout.readline()
        if not line.startswith("screen relay listening on "):
            raise RuntimeError("Relay did not start: " + line)
        self.relay_url = "http://" + line.strip().split()[-1]

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
            self.initial.close(); self.initial = None
        else:
            self.api.__exit__(None, None, None)
        self.settings = replace(self.settings, test_identities=dict(self.settings.test_identities))
        self.api = TestClient(create_app(self.settings)).__enter__()
        self.listener = self.bind(port)
        self.start_http()

    def restart_rust(self):
        self.process.terminate(); self.process.wait(5)
        self.start_rust()
        self.eventually(lambda: self.api.app.state.store.screen_sessions.healthy
                        and self.api.app.state.store.screen_sessions.boot == self.control("hello", get=True)["boot"])

    def control(self, op, get=False, **body):
        with httpx.Client(base_url=self.relay_url, headers={"Authorization": "Bearer " + KEY}, timeout=4) as http:
            response = http.get("/control/v1/hello") if get else http.post("/control/v1/" + op, json=body)
        response.raise_for_status()
        return response.json()

    def websocket(self, path, token=None, origin=None, **options):
        return connect(self.relay_url.replace("http://", "ws://") + path,
                       additional_headers=headers(token) if token else None, origin=origin,
                       open_timeout=5, close_timeout=1, max_size=16 << 20, **options)

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
            time.sleep(.02)
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
                        self.process.kill(); self.process.wait(5)


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


def denied(service, path, **kwargs):
    with pytest.raises(InvalidStatus) as exc:
        with service.websocket(path, **kwargs):
            pass
    assert exc.value.response.status_code == 403


def test_ticket_replay_wrong_runner_pairing_modes_and_limits(service):
    r, wrong = service.computer(), service.computer()
    opened = service.opened(r, control=True)
    denied(service, opened["ws_path"], origin="https://untrusted.example")
    denied(service, service.runner_path(opened), token=wrong["token"])
    # A runner may arrive first and preserve its VNC greeting until the browser comes.
    with service.websocket(service.runner_path(opened), token=r["token"]) as computer:
        computer.send(b"RFB 003.008\n")
        def redeem(_):
            try:
                return service.websocket(opened["ws_path"], origin=service.origin, subprotocols=["binary"])
            except InvalidStatus as exc:
                assert exc.response.status_code == 403
                return None
        with ThreadPoolExecutor(2) as pool:
            attempts = list(pool.map(redeem, range(2)))
        winners = [s for s in attempts if s]
        assert len(winners) == 1
        with winners[0] as browser:
            assert browser.subprotocol == "binary" and browser.recv(timeout=5) == b"RFB 003.008\n"
            browser.send(b"\x01\x02"); assert computer.recv(timeout=5) == b"\x01\x02"
            computer.send("frame"); assert browser.recv(timeout=5) == "frame"
            denied(service, service.runner_path(opened), token=r["token"])
            service.opened(r); service.opened(r)
            post(service.api, f"computers/{r['runner_id']}/screen/sessions", {"control": False}, expected=429)
            post(service.api, f"computers/{r['runner_id']}/screen", {"mode": "view"})
            with pytest.raises(ConnectionClosed) as closed:
                browser.recv(timeout=5)
            assert closed.value.rcvd.code == 4403
    denied(service, opened["ws_path"])
    post(service.api, f"computers/{r['runner_id']}/screen/sessions", {"control": True}, expected=403)
    post(service.api, f"computers/{r['runner_id']}/screen", {"mode": "off"})
    assert get(service.api, "runners/me/screen/sessions", token=r["token"]) == {"sessions": []}


def test_revocation_races_disconnects_and_duplicate_safe_audit(service):
    r = service.computer()
    opened = service.opened(r)
    with service.websocket(opened["ws_path"]) as browser:
        with service.websocket(service.runner_path(opened), token=r["token"]) as computer:
            computer.close()
            with pytest.raises(ConnectionClosed):
                browser.recv(timeout=5)
    rid = r["runner_id"]
    def closes():
        with service.api.app.state.store.read() as c:
            return c.execute("SELECT count(*) FROM events WHERE action='runner.screen_close' AND target=? "
                             "AND json_extract(detail,'$.session_id')=?", (rid, opened["session_id"])).fetchone()[0]
    service.eventually(lambda: closes() == 1)
    # Admission and revocation serialize through durable policy and the grant generation.
    with ThreadPoolExecutor(2) as pool:
        opening = pool.submit(lambda: service.api.post(f"/api/v2/computers/{rid}/screen/sessions",
                                                     headers=headers(), json={"control": False}))
        off = pool.submit(lambda: post(service.api, f"computers/{rid}/screen", {"mode": "off"}))
        response, _ = opening.result(), off.result()
    assert response.status_code in (200, 403)
    if response.status_code == 200:
        denied(service, response.json()["ws_path"])
    assert get(service.api, "runners/me/screen/sessions", token=r["token"]) == {"sessions": []}
    post(service.api, f"computers/{rid}/screen", {"mode": "control"})
    opened = service.opened(r)
    with service.websocket(opened["ws_path"]) as browser:
        with service.websocket(service.runner_path(opened), token=r["token"]) as computer:
            post(service.api, f"runners/{rid}/revoke", {})
            with pytest.raises(ConnectionClosed) as closed:
                browser.recv(timeout=5)
            assert closed.value.rcvd.code == 4403
    denied(service, service.runner_path(opened), token=r["token"])
    service.eventually(lambda: closes() == 1)


def test_restart_invalidates_old_tickets_and_reconciles_audit(service):
    r = service.computer()
    opened = service.opened(r)
    service.restart_python()
    denied(service, opened["ws_path"])
    opened = service.opened(r)
    with service.websocket(opened["ws_path"]) as browser:
        with service.websocket(service.runner_path(opened), token=r["token"]) as computer:
            computer.send(b"new server"); assert browser.recv(timeout=5) == b"new server"
    opened = service.opened(r)
    service.restart_rust()
    denied(service, opened["ws_path"])
    with service.api.app.state.store.read() as c:
        count = c.execute("SELECT count(*) FROM events WHERE action='runner.screen_close' AND target=? "
                          "AND json_extract(detail,'$.session_id')=?", (r["runner_id"], opened["session_id"])).fetchone()[0]
    assert count == 1


def test_backpressure_does_not_delay_revocation(service):
    r = service.computer()
    opened = service.opened(r)
    with service.websocket(opened["ws_path"], max_queue=1) as browser:
        with service.websocket(service.runner_path(opened), token=r["token"]) as computer:
            stop = threading.Event()
            def flood():
                try:
                    for _ in range(1024):
                        if stop.is_set():
                            return
                        computer.send(b"x" * 65536)
                except (ConnectionClosed, OSError):
                    pass
            producer = threading.Thread(target=flood, daemon=True)
            producer.start()
            try:
                time.sleep(.2)  # The browser's bounded receive queue deliberately stops draining.
                start = time.monotonic()
                post(service.api, f"computers/{r['runner_id']}/screen", {"mode": "off"})
                with pytest.raises(ConnectionClosed) as closed:
                    while True:
                        browser.recv(timeout=max(.1, 6 - (time.monotonic() - start)))
                assert closed.value.rcvd.code == 4403
                assert time.monotonic() - start < 6
            finally:
                stop.set()
                with contextlib.suppress(OSError):
                    computer.socket.shutdown(socket.SHUT_RDWR)
                producer.join(5)
                assert not producer.is_alive()


def test_private_protocol_fences_replays_stale_grants_and_lease_loss(service):
    r = service.computer()
    remote = service.api.app.state.store.screen_sessions
    with remote.lock:
        body = {"lease": remote.lease, "sequence": remote.sequence + 1, "op": "grant", "id": "prepared",
                "runner_id": r["runner_id"], "actor": "human:ana", "control": False,
                "generation": remote.generations[r["runner_id"]], "mode": "control"}
        answer = remote._request("/control/v1/command", body)
        assert remote._request("/control/v1/command", body) == answer  # exact retry is harmless
        remote.sequence += 1
        assert remote._command("pending", runner_id=r["runner_id"])["sessions"] == []
        denied(service, f"/api/v2/runners/{r['runner_id']}/screen/ws?ticket=" + answer["ticket"])
        remote._command("mode", runner_id=r["runner_id"], generation=remote.generations[r["runner_id"]] + 1,
                        mode="off", reason="revoked")
        with pytest.raises(Problem) as stale:
            remote._request("/control/v1/command", {**body, "sequence": remote.sequence + 1})
        assert getattr(stale.value, "status", None) == 403
        with pytest.raises(Problem):
            remote._request("/control/v1/command", body)  # earlier sequence cannot resurrect a closed grant
    # Re-establish an epoch, then model a Python process unable to renew its permission lease.
    remote.start()
    post(service.api, f"computers/{r['runner_id']}/screen", {"mode": "control"})
    opened = service.opened(r)
    with service.websocket(opened["ws_path"]) as browser:
        with service.websocket(service.runner_path(opened), token=r["token"]) as computer:
            remote.superseded = True
            with pytest.raises(ConnectionClosed) as closed:
                browser.recv(timeout=9)
            assert closed.value.rcvd.code == 1012
    report = {"id": opened["session_id"], "runner_id": r["runner_id"], "actor": "human:ana",
              "control": False, "lease": remote.lease, "seconds": 0, "code": 1012}
    with service.api.app.state.store.transaction() as c:
        remote._record(c, report)
        remote._record(c, report)
        assert c.execute("SELECT count(*) FROM events WHERE action='runner.screen_close' AND target=? "
                         "AND json_extract(detail,'$.session_id')=?",
                         (r["runner_id"], opened["session_id"])).fetchone()[0] == 1
    with httpx.Client(base_url=service.relay_url, timeout=3) as client:
        assert client.get("/control/v1/hello").status_code == 403
        assert client.post("/control/v1/command", headers={"Authorization": "Bearer " + KEY},
                           json={"lease": remote.lease, "sequence": remote.sequence + 1,
                                 "op": "heartbeat", "ack": []}).status_code == 409
