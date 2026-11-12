"""Webhooks through Tico Gateway, the install's side (backend/hook_relay.py), against a fake HQ."""

import json
from types import SimpleNamespace


from backend import hook_relay, triggers
from backend import hubdb as H
from backend.tests.helpers.api import get, post, put  # noqa: F401
from backend.tests.helpers.hook_relay import BASE, HQ, KEY, hq, relay_for, service


def receiver(monkeypatch, outcomes=None, boom=()):
    """A stand-in for triggers.receive (W2): records each call, writes an event so a commit is visible, and answers."""
    seen = []

    def receive(c, trigger_id, body, headers, *, secret, via="direct", delivery_id=None, received_at=None, **more):
        if delivery_id in boom:
            raise RuntimeError("receive broke")
        seen.append(
            {
                "trigger": trigger_id,
                "body": body,
                "headers": headers,
                "secret": secret,
                "via": via,
                "delivery": delivery_id,
                "received_at": received_at,
            }
        )
        H.event(c, "keeper", "test.received", delivery_id, {})
        outcome = (outcomes or {}).get(delivery_id, "accepted")
        return SimpleNamespace(outcome=outcome, status=202, delivery=delivery_id, reason=None, tasks=0, rules=[])

    monkeypatch.setattr(triggers, "receive", receive, raising=False)
    return seen


def test_a_short_address_from_hq_is_kept_and_an_answer_of_neither_shape_is_refused(api, hq):
    post(api, "triggers", {"name": "Shop"})
    hq.short = "https://hooks.example"  # HQ with HQ_HOOKS_HOST: https://<host>/<relay>/<secret>
    made = post(api, "triggers/shop/relay", {}, key="k1")["relay"]
    assert made["address_prefix"] == f"https://hooks.example/{made['relay_id']}" and made["address"].endswith(
        "/s3cret01"
    )
    assert hook_relay._prefix("http://hooks.example/hr_0123456789abcdef/x", "hr_0123456789abcdef") == ""
    assert hook_relay._prefix("https://hooks.example/hr_0123456789abcdef/x/y", "hr_0123456789abcdef") == ""
    assert hook_relay._prefix("https://hooks.example/other/x?hr_0123456789abcdef", "hr_0123456789abcdef") == ""


def test_a_create_retried_after_a_lost_answer_is_the_same_relay_without_a_secret(api, hq):
    post(api, "triggers", {"name": "Shop"})
    first = post(api, "triggers/shop/relay", {}, key="k1")["relay"]
    with api.app.state.store.transaction() as c:  # this server lost the answer before it saved it
        c.execute("DELETE FROM trigger_relays")
    again = post(api, "triggers/shop/relay", {}, key="k1")["relay"]
    assert (
        again["relay_id"] == first["relay_id"]
        and again["replayed"] is True
        and again["address"] == again["address_prefix"]
    )
    assert len(hq.relays) == 1


def test_a_concurrent_create_that_gets_the_same_hq_relay_keeps_it_instead_of_deleting_it(api, hq):
    """Two creates with one idempotency key both pass the gate and both get HQ's one relay; the second must not treat
    that relay as an extra and delete it."""
    post(api, "triggers", {"name": "Shop"})
    svc = api.app.state.hook_relay
    real = svc.ask

    def ask_then_other_request_saves_first(link, method, path, body, idempotency, quiet=False):
        answer = real(link, method, path, body, idempotency, quiet)
        if path == "relays" and method == "POST":
            with api.app.state.store.transaction() as c:
                c.execute(
                    "INSERT INTO trigger_relays(relay_id,trigger_id,address_prefix,created_by,created_at) VALUES(?,?,?,?,?)",
                    (answer["relay_id"], "shop", f"{BASE}/v1/hooks/{answer['relay_id']}", "human:ana", H.now()),
                )
        return answer

    svc.ask = ask_then_other_request_saves_first
    try:
        made = post(api, "triggers/shop/relay", {}, key="k1")["relay"]
    finally:
        del svc.ask
    assert made["relay_id"] in hq.relays and not any(p.endswith("/delete") for p in hq.paths())
    assert get(api, "triggers")["triggers"][0]["relay"]["address_prefix"].endswith(made["relay_id"])


def test_a_refused_key_stops_the_loop_until_the_key_changes(api, hq, monkeypatch):
    _, made = relay_for(api, hq)
    seen = receiver(monkeypatch)
    hq.deliver(made["relay_id"], "d1")
    hq.status["pull"] = 401
    svc = service(api)
    svc.tick()
    assert get(api, "relay")["relay"]["key"] == "revoked" and get(api, "relay")["relay"]["last_error"] == "key_revoked"
    asked = len(hq.calls)
    svc.tick()
    svc.tick()
    assert len(hq.calls) == asked  # silent while the key is the same
    hq.status.clear()
    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY + "2")
    hq.key = KEY + "2"
    svc.tick()
    assert [s["delivery"] for s in seen] == ["d1"] and get(api, "relay")["relay"]["key"] == "connected"


def test_an_address_hq_refuses_is_a_422_and_leaves_the_old_one(api, hq):
    put(api, "hooks/wake-url", {"url": "https://wake.acme.example/start"})
    refused = put(api, "hooks/wake-url", {"url": "https://bad.acme.example/start"}, expected=422)
    assert "public https" in refused["error"]["detail"]
    assert get(api, "hooks/wake-url")["url"] == "https://wake.acme.example/start"
