"""Helpers that other tests share, moved out of test_webhooks.py so they import no test file."""

import base64
import hashlib
import hmac
import ipaddress
import time
import httpx
import pytest
from backend import egress
from backend import webhook_delivery as D
from backend import webhooks as W
from backend.tests.helpers.tasks_board import headers


PUBLIC = [ipaddress.ip_address("93.184.216.34")]


URL = "https://hooks.acme.example/tico"


def verify(secret, headers_, body, now=None, tolerance=300):
    """A Standard Webhooks verifier written from the spec text, not from our signer: the signed content is
    `id.timestamp.body`, the key is the base64 payload after `whsec_`, the header holds space-separated `v1,<sig>`."""
    sent = int(headers_["webhook-timestamp"])
    assert abs((now or time.time()) - sent) <= tolerance
    key = base64.b64decode(secret.split("_", 1)[1])
    want = base64.b64encode(
        hmac.new(
            key, f"{headers_['webhook-id']}.{headers_['webhook-timestamp']}.".encode() + body, hashlib.sha256
        ).digest()
    ).decode()
    return any(
        hmac.compare_digest(want, sig.split(",", 1)[1])
        for sig in headers_["webhook-signature"].split(" ")
        if sig.startswith("v1,")
    )


@pytest.fixture
def net(monkeypatch):
    """The network: every name resolves to a public address, and each request goes to `net.answer` and is kept."""

    class Net:
        seen = []
        reply = staticmethod(lambda request: httpx.Response(200))

    state = Net()
    state.seen = []

    def handler(request):
        state.seen.append(request)
        return state.reply(request)

    state.transport = httpx.MockTransport(handler)
    monkeypatch.setattr(egress, "resolve", lambda host: PUBLIC)
    return state


def post(api, path, body, token="ana-test", expected=200, key=None):
    """The API call; `key` pins the Idempotency-Key so a repeat is a replay."""
    sent = headers(token)
    if key:
        sent["Idempotency-Key"] = key
    reply = api.post("/api/v2/" + path, json=body, headers=sent)
    assert reply.status_code == expected, reply.text
    return reply.json()


def make(api, url=URL, events=("*",), token="ana-test", **more):
    return post(api, "webhooks", {"name": "Acme hook", "url": url, "events": list(events), **more}, token=token)[
        "webhook"
    ]


def run(api, net, rehearsal=False):
    """Deliver everything that is due, as the workers would."""
    app = api.app
    count = 0
    while D.step(app.state.store, app.state.vault, transport=net.transport, rehearsal=rehearsal):
        count += 1
    return count


def due(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE webhook_sends SET next_attempt_at='1970-01-01T00:00:00.000000Z' WHERE state='ready'")


def sends(api, hook_id=None):
    with api.app.state.store.read() as c:
        sql = "SELECT * FROM webhook_sends" + (" WHERE webhook_id=?" if hook_id else "") + " ORDER BY created_at, id"
        return [dict(r) for r in c.execute(sql, (hook_id,) if hook_id else ())]


def publish(api, *args, **kw):
    with api.app.state.store.transaction() as c:
        return W.publish(c, *args, **kw)
