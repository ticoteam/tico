"""The real Rust screen WebSocket and lifecycle contract.

These start only owned loopback services on allocated ports and use fictional identities/SQLite.
Run this file with -m slow -n 0. The binary is TICO_SCREEN_RELAY_BIN, else the build matching the relay-rs sources:
backend/screen_binary.prepare finds it, or builds it with cargo on first use.
"""

import contextlib
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus

from backend.store import Problem
from backend.tests.helpers.api import get, headers, post, runner
from backend.tests.helpers.screen_relay_contract import KEY, service

pytestmark = pytest.mark.slow


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
            browser.send(b"\x01\x02")
            assert computer.recv(timeout=5) == b"\x01\x02"
            computer.send("frame")
            assert browser.recv(timeout=5) == "frame"
            denied(service, service.runner_path(opened), token=r["token"])
            service.opened(r)
            service.opened(r)
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
            return c.execute(
                "SELECT count(*) FROM events WHERE action='runner.screen_close' AND target=? "
                "AND json_extract(detail,'$.session_id')=?",
                (rid, opened["session_id"]),
            ).fetchone()[0]

    service.eventually(lambda: closes() == 1)
    # Admission and revocation serialize through durable policy and the grant generation.
    with ThreadPoolExecutor(2) as pool:
        opening = pool.submit(
            lambda: service.api.post(
                f"/api/v2/computers/{rid}/screen/sessions", headers=headers(), json={"control": False}
            )
        )
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


def test_private_protocol_fences_replays_stale_grants_and_lease_loss(service):
    r = service.computer()
    remote = service.api.app.state.store.screen_sessions
    with remote.lock:
        body = {
            "lease": remote.lease,
            "sequence": remote.sequence + 1,
            "op": "grant",
            "id": "prepared",
            "runner_id": r["runner_id"],
            "actor": "human:ana",
            "control": False,
            "generation": remote.generations[r["runner_id"]],
            "mode": "control",
        }
        answer = remote._request("/control/v1/command", body)
        assert remote._request("/control/v1/command", body) == answer  # exact retry is harmless
        remote.sequence += 1
        assert remote._command("pending", runner_id=r["runner_id"])["sessions"] == []
        denied(service, f"/api/v2/runners/{r['runner_id']}/screen/ws?ticket=" + answer["ticket"])
        remote._command(
            "mode",
            runner_id=r["runner_id"],
            generation=remote.generations[r["runner_id"]] + 1,
            mode="off",
            reason="revoked",
        )
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
    report = {
        "id": opened["session_id"],
        "runner_id": r["runner_id"],
        "actor": "human:ana",
        "control": False,
        "lease": remote.lease,
        "seconds": 0,
        "code": 1012,
    }
    with service.api.app.state.store.transaction() as c:
        remote._record(c, report)
        remote._record(c, report)
        assert (
            c.execute(
                "SELECT count(*) FROM events WHERE action='runner.screen_close' AND target=? "
                "AND json_extract(detail,'$.session_id')=?",
                (r["runner_id"], opened["session_id"]),
            ).fetchone()[0]
            == 1
        )
    with httpx.Client(base_url=service.relay_url, timeout=3) as client:
        assert client.get("/control/v1/hello").status_code == 403
        assert (
            client.post(
                "/control/v1/command",
                headers={"Authorization": "Bearer " + KEY},
                json={"lease": remote.lease, "sequence": remote.sequence + 1, "op": "heartbeat", "ack": []},
            ).status_code
            == 409
        )
