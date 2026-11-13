"""Helpers that other tests share, moved out of test_hook_relay.py so they import no test file."""

import base64
import json
import httpx
import pytest
from backend import hook_relay
from backend.tests.helpers.api import post


KEY = "tgw_acme_test_key"


BASE = "https://api.tico.dev"


class HQ:
    """Tico HQ's relay API (the contract), in memory. `queue` holds the deliveries it would serve."""

    def __init__(self):
        self.relays, self.queue, self.calls, self.acks = {}, [], [], []
        self.status = {}  # path suffix -> a forced HTTP status
        self.on_ack = None
        self.cursor_floor = None  # when set, pull and ack answer cursor_ahead with this cursor
        self.down = False
        self.key = KEY
        self.wake = None  # the address HQ would knock on
        self.short = ""  # HQ_HOOKS_HOST as HQ hands it out ("https://hooks.example"): short addresses

    def deliver(self, relay_id, delivery_id, body=b'{"order": 42}', headers=None):
        seq = len(self.queue) + 1
        self.queue.append(
            {
                "hq_seq": seq,
                "relay_id": relay_id,
                "trigger_id": self.relays[relay_id]["trigger_id"],
                "delivery_id": delivery_id,
                "received_at": "2026-10-25T10:00:00Z",
                "headers": headers or {"content-type": "application/json", "webhook-id": delivery_id},
                "body_b64": base64.b64encode(body).decode(),
            }
        )

    def __call__(self, request):
        if self.down:
            raise httpx.ConnectError("down")
        path = request.url.path.removeprefix("/v1/gateway/hooks/")
        body = json.loads(request.content or b"{}")
        self.calls.append((request.method, path, body, dict(request.headers)))
        assert request.headers["authorization"] == "Bearer " + self.key and request.headers["user-agent"].startswith(
            "Tico-Client/"
        )
        if path in self.status:
            return httpx.Response(self.status[path], json={"error": "forced"})
        if path == "wake":
            if body["url"] and "bad" in body["url"]:
                return httpx.Response(422, json={"error": "invalid_url"})
            self.wake = body["url"]
            return httpx.Response(200, json={"url": body["url"], "secret": "whsec_test" if body["url"] else None})
        if path == "relays" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "relay_id": r["id"],
                            "trigger_id": r["trigger_id"],
                            "name": "",
                            "address_prefix": "",
                            "wake": r.get("wake", False),
                            "system": bool(r.get("system")),
                            "created_at": "2026-10-25T09:00:00Z",
                        }
                        for r in self.relays.values()
                    ]
                },
            )
        if path == "relays":
            if "idempotency-key" not in request.headers:
                return httpx.Response(400, json={"error": "idempotency_key"})
            seen = [r for r in self.relays.values() if r["key"] == request.headers["idempotency-key"]]
            if seen:
                return httpx.Response(
                    200,
                    json={
                        "relay_id": seen[0]["id"],
                        "address": f"{BASE}/v1/hooks/{seen[0]['id']}",
                        "secret": None,
                        "replayed": True,
                    },
                )
            relay_id = "hr_" + format(len(self.relays) + 1, "016x")
            self.relays[relay_id] = {
                "id": relay_id,
                "trigger_id": body["trigger_id"],
                "key": request.headers["idempotency-key"],
            }
            where = f"{self.short}/{relay_id}" if self.short else f"{BASE}/v1/hooks/{relay_id}"
            return httpx.Response(
                200,
                json={
                    "relay_id": relay_id,
                    "address": f"{where}/s3cret{relay_id[-2:]}",
                    "secret": "s3cret" + relay_id[-2:],
                },
            )
        if path.startswith("relays/hr_") and path.count("/") == 1:
            self.relays[path.split("/")[1]]["wake"] = body["wake"]
            return httpx.Response(200, json={"relay_id": path.split("/")[1], "wake": body["wake"]})
        if path.endswith("/rotate"):
            relay_id = path.split("/")[1]
            return httpx.Response(
                200, json={"relay_id": relay_id, "address": f"{BASE}/v1/hooks/{relay_id}/rotated", "secret": "rotated"}
            )
        if path.endswith("/delete"):
            self.relays.pop(path.split("/")[1], None)
            return httpx.Response(200, json={"ok": True})
        if path == "pull":
            if self.cursor_floor is not None and body["cursor"] > self.cursor_floor:
                return httpx.Response(409, json={"error": "cursor_ahead", "cursor": self.cursor_floor})
            items = [i for i in self.queue if i["hq_seq"] > body["cursor"]][: body["limit"]]
            cursor = items[-1]["hq_seq"] if items else body["cursor"]
            return httpx.Response(200, json={"items": items, "cursor": cursor, "more": False})
        if path == "ack":
            if body["through_hq_seq"] > len(self.queue):
                return httpx.Response(409, json={"error": "cursor_ahead", "cursor": len(self.queue)})
            if self.on_ack:
                self.on_ack(body["through_hq_seq"])
            self.acks.append(body["through_hq_seq"])
            return httpx.Response(200, json={"ok": True, "acked": 1})
        if path == "pending":
            waiting = [i for i in self.queue if i["hq_seq"] > (self.acks[-1] if self.acks else 0)]
            return httpx.Response(200, json={"pending": len(waiting), "oldest_received_at": "2026-10-25T10:00:00Z"})
        return httpx.Response(404, json={"error": "not_found"})

    def paths(self):
        return [c[1] for c in self.calls]


@pytest.fixture
def hq(api, monkeypatch):
    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
    fake = HQ()
    monkeypatch.setattr(hook_relay, "TRANSPORT", httpx.MockTransport(fake))
    # The app's own loop must not pull in the middle of a test; the tests tick by hand.
    svc = api.app.state.hook_relay
    monkeypatch.setattr(svc, "tick", lambda: None)
    # The loop's first tick may already be handed to its thread. One that has not begun its pass finds this
    # empty pass; one that is inside its pass holds the lock, so taking the lock waits for it to finish.
    monkeypatch.setattr(svc, "_tick", lambda: None)
    with svc.lock:
        pass
    fake.calls.clear()  # ... and, with a key and no relay, it asked for notices: not part of the test
    api.app.state.hook_relay.listed = 0.0
    return fake


class Pulls:
    """The app's own 15-second loop may be mid-tick at the start of a test; this waits for its turn instead of skipping."""

    def __init__(self, svc):
        self.svc = svc

    def __getattr__(self, name):
        return getattr(self.svc, name)

    def __setattr__(self, name, value):
        if name == "svc":
            object.__setattr__(self, name, value)
        else:
            setattr(self.svc, name, value)

    def tick(self):
        with self.svc.lock:
            if not self.svc.off:
                type(self.svc)._tick(self.svc)  # the real pass: the fixture emptied the instance's own


def service(api):
    return Pulls(api.app.state.hook_relay)


def relay_for(api, hq, name="Shop"):
    trigger = post(api, "triggers", {"name": name})["trigger"]
    made = post(api, f"triggers/{trigger['id']}/relay", {})["relay"]
    return trigger["id"], made
