"""Webhooks end to end, over real HTTP: a Zapier-shaped call posted to the address Tico gives a trigger starts the
automation the recipe in docs/webhooks-zapier-make-n8n.md names, and a signed outgoing delivery reaches a receiver on
this machine that checks it with a verifier written from the spec text, not from our signer."""

import http.server
import json
import threading
from urllib.parse import urlsplit

import httpx
import pytest

from backend import webhook_delivery as D
from backend import webhooks as W
from backend.tests.live import block_outside_network, live  # noqa: F401
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_webhooks import publish, verify

# The "Start something" recipe of docs/webhooks-zapier-make-n8n.md, with the trigger's type taken from the body's `type`.
RECIPE = {
    "name": "Order paid", "owner": "bot:ops", "enabled": True,
    "trigger": {"type": "webhook", "id": "zapier-orders",
                "filters": [{"field": "webhook.type", "op": "is", "value": "order.paid"}]},
    "steps": [{"id": "wake", "type": "action", "enabled": True, "do": "wake_bot",
               "args": {"bot": "ops", "text": "Order {{webhook.data.id}} paid: {{webhook.data.total}} from "
                                              "{{webhook.data.customer}}"}}],
}
PAID = {"type": "order.paid", "id": "ord_1001", "total": 42, "customer": "ada@acme.example"}


class Receiver(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("content-length", 0))
        self.server.received.append(({k.lower(): v for k, v in self.headers.items()}, self.rfile.read(size)))
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture(autouse=True)
def no_outside_network(monkeypatch):
    """The receiver and the API are on this computer; nothing else is reached."""
    block_outside_network(monkeypatch, "the webhook e2e test has no network")


@pytest.fixture
def receiver():
    """A real HTTP server on a free loopback port; `received` keeps each call's headers and exact bytes."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    server.received = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def messages_to(api, actor):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT body, refs FROM messages WHERE recipient_id=? ORDER BY created_at", (actor,))]


@pytest.mark.slow
def test_a_zapier_shaped_post_over_http_starts_the_automation_its_recipe_names(api, live):
    trigger = post(api, "triggers", {"name": "Zapier orders"})["trigger"]
    assert trigger["id"] == "zapier-orders"
    post(api, "triggers/zapier-orders", {"type_from": "/type"})
    aid = post(api, "automations", RECIPE)["id"]
    address = live + urlsplit(trigger["url"]).path
    with httpx.Client(trust_env=False, timeout=10) as client:
        paid = client.post(address, json=PAID, headers={"Idempotency-Key": "ord_1001"})
        refunded = client.post(address, json={"type": "order.refunded", "id": "ord_1002", "total": 5,
                                              "customer": "ada@acme.example"})
    assert paid.status_code == 202, paid.text
    assert refunded.status_code == 202, refunded.text
    woke = messages_to(api, "bot:ops")
    assert len(woke) == 1
    # The call's values reach the bot as quoted data blocks, never as text of the prompt itself.
    for field, value in (("webhook.data.id", "ord_1001"), ("webhook.data.total", "42"),
                         ("webhook.data.customer", "ada@acme.example")):
        assert f'field="{field}">\n{value}\n' in woke[0]["body"], woke[0]["body"][:300]
    assert json.loads(woke[0]["refs"])["automation"] == aid
    assert len(get(api, f"automations/{aid}/runs")["runs"]) == 1, "the refund's type does not match the filter"


@pytest.mark.slow
def test_a_task_done_reaches_a_local_receiver_signed_for_an_independent_verifier(api, receiver):
    url = f"http://127.0.0.1:{receiver.server_address[1]}/tico"
    made = post(api, "webhooks", {"name": "Acme receiver", "url": url, "events": ["task.done"], "allow_http": True})
    secret = made["webhook"]["secret"]
    task = post(api, "tasks", {"owner": "ops", "title": "Acme outbound", "body": "Seeded for the webhook e2e test."})
    publish(api, "task.done", task["id"], {"id": task["id"], "title": "Acme outbound"}, "bot:ops", revision="r1")

    store, vault = api.app.state.store, api.app.state.vault
    while D.step(store, vault):            # the delivery worker's loop, on the real transport
        pass

    assert len(receiver.received) == 1
    headers_, body = receiver.received[0]
    assert verify(secret, headers_, body), "the receiver's independent check refused our signature"
    assert D.signature(secret, headers_["webhook-id"], int(headers_["webhook-timestamp"]), body) == \
        headers_["webhook-signature"].split(" ")[0]
    sent = json.loads(body)
    sent = json.loads(body)
    assert sent["type"] == "task.done" and sent["data"]["id"] == task["id"]
    with api.app.state.store.read() as c:
        state = c.execute("SELECT state FROM webhook_sends WHERE webhook_id=?", (made["webhook"]["id"],)).fetchone()
    assert state["state"] == "delivered"
