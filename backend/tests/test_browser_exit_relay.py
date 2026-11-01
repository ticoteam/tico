"""A browser exit through the real Rust relay: the exit computer's link, a tunnel granted by Python, both ends paired
by their one-use tickets, bytes both ways, the day's log written from the relay's report, and the link dropped when
the owner stops sharing. Fictional identities, owned loopback services; build relay-rs first (see
backend/tests/test_screen_relay_contract.py) and run with -m slow -n 0."""

import json

import pytest
from websockets.exceptions import ConnectionClosed

from backend.browser_exit import today
from backend.tests.test_api import assign, get, post, runner
from backend.tests.test_screen_relay_contract import running_service

pytestmark = pytest.mark.slow


def test_a_tunnel_through_the_relay_carries_bytes_is_logged_and_stops_with_sharing(tmp_path):
    with running_service(tmp_path) as service:
        api = service.api
        laptop = runner(api, operator="cara", label="Cara's MacBook")
        server = runner(api, operator="ana", label="Office server")
        assign(api, server, "ops")
        service.eventually(lambda: api.app.state.store.screen_sessions.healthy)
        rid = laptop["runner_id"]
        with pytest.raises(Exception):          # not sharing: the relay's policy callback refuses the link
            with service.websocket("/api/v2/runners/me/browser-exit/ws", token=laptop["token"]):
                pass
        post(api, f"computers/{rid}/browser-exit", {"sharing": True, "bots": ["ops"]}, token="cara-test")
        post(api, "bots/ops/browser-exit", {"runner_id": rid})

        def ask():
            return api.post("/api/v2/runners/me/browser-exit/tunnels", json={"bot": "ops", "host": "www.acme.example",
                            "port": 443}, headers={"Authorization": "Bearer " + server["token"]})
        assert ask().json()["error"]["detail"] == "Cara's MacBook is offline"           # no link yet
        with service.websocket("/api/v2/runners/me/browser-exit/ws", token=laptop["token"]) as link:
            hello = json.loads(link.recv(timeout=5))        # the address the relay saw: the exit refuses to dial it
            assert hello["op"] == "hello"                     # "seen" is empty on the private listener
            granted = []
            service.eventually(lambda: granted.append(ask()) or granted[-1].status_code == 200)
            grant = granted[-1].json()
            opened = json.loads(link.recv(timeout=5))
            assert (opened["op"], opened["id"], opened["host"], opened["port"]) == (
                "open", grant["tunnel_id"], "www.acme.example", 443)
            base = f"/api/v2/runners/me/browser-exit/tunnels/{grant['tunnel_id']}"
            with service.websocket(f"{base}/exit?ticket={opened['ticket']}") as exit_end, \
                    service.websocket(f"{base}/ws?ticket={grant['ticket']}") as bot_end:
                exit_end.send('{"ok":true}')
                assert json.loads(bot_end.recv(timeout=5)) == {"ok": True}
                bot_end.send(b"hello")
                assert exit_end.recv(timeout=5) == b"hello"
                exit_end.send(b"world!")
                assert bot_end.recv(timeout=5) == b"world!"
                with pytest.raises(Exception):      # a ticket opens its end once
                    with service.websocket(f"{base}/ws?ticket={grant['ticket']}"):
                        pass
            service.eventually(lambda: get(api, f"computers/{rid}/browser-exit/usage", token="cara-test")["rows"])
            assert get(api, f"computers/{rid}/browser-exit/usage", token="cara-test")["rows"][0] == {
                "day": today(),
                "bot": "ops", "host": "www.acme.example", "bytes": 11, "tunnels": 1}
            post(api, f"computers/{rid}/browser-exit", {"sharing": False}, token="cara-test")
            with pytest.raises(ConnectionClosed):
                while True:
                    link.recv(timeout=5)
        assert ask().json()["error"]["code"] == "browser_exit_not_shared"
