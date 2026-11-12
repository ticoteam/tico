"""Webhooks through Tico Gateway, the install's side (backend/hook_relay.py), against a fake HQ."""

import dataclasses
import json
import time
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


def test_an_address_needs_the_gateway_key_and_shows_its_secret_once(api, hq, monkeypatch):
    monkeypatch.delenv("TICO_GATEWAY_KEY")
    post(api, "triggers", {"name": "Shop"})
    refused = post(api, "triggers/shop/relay", {}, expected=409)
    assert "Settings > Models" in refused["error"]["detail"] and hq.calls == []

    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
    made = post(api, "triggers/shop/relay", {}, key="k1")["relay"]
    assert made["replayed"] is False and made["address"] == made["address_prefix"] + "/s3cret01"
    assert made["address_prefix"] == f"{BASE}/v1/hooks/{made['relay_id']}"
    listed = get(api, "triggers")["triggers"][0]
    assert listed["relay"]["address_prefix"] == made["address_prefix"] and "s3cret" not in json.dumps(listed)
    with api.app.state.store.read() as c:  # the secret is nowhere in the database
        assert "s3cret01" not in json.dumps([tuple(r) for r in c.execute("SELECT * FROM trigger_relays")])
    post(api, "triggers/shop/relay", {}, expected=409)  # one address per trigger

    rotated = post(api, "triggers/shop/relay/rotate", {})["relay"]
    assert rotated["address"].endswith("/rotated") and rotated["address_prefix"] == made["address_prefix"]
    gone = post(api, "triggers/shop/relay/delete", {})["relay"]
    assert gone["deleted"] and made["relay_id"] not in hq.relays
    assert get(api, "triggers")["triggers"][0]["relay"] is None
    post(api, "triggers/shop/relay/delete", {}, expected=404)


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


def test_only_the_owner_or_the_maker_manages_an_address(api, hq):
    post(api, "triggers", {"name": "Bens"}, token="ben-test")
    post(api, "triggers/bens/relay", {}, token="cara-test", expected=403)
    assert hq.calls == []
    post(api, "triggers/bens/relay", {}, token="ben-test")
    post(api, "triggers/bens/relay/rotate", {}, token="cara-test", expected=403)
    post(api, "triggers/bens/relay/delete", {}, token="cara-test", expected=403)
    assert post(api, "triggers/bens/relay/rotate", {})["relay"]["address"].endswith("/rotated")  # the owner may
    assert get(api, "relay", token="cara-test")["relay"]["relays"] == 1  # anyone may read


def test_the_wake_switch_is_read_from_the_gateways_list_and_set_through_it(api, hq):
    trigger_id, made = relay_for(api, hq)
    shown = lambda: get(api, "triggers")["triggers"][0]["relay"]["wake"]  # noqa: E731
    assert shown() is None  # HQ has not said yet
    hq.relays[made["relay_id"]]["wake"] = True
    service(api).tick()
    assert shown() is True
    post(api, f"triggers/{trigger_id}/relay/wake", {"wake": False}, token="cara-test", expected=403)
    assert post(api, f"triggers/{trigger_id}/relay/wake", {"wake": False})["relay"]["wake"] is False
    assert hq.calls[-1][1] == "relays/" + made["relay_id"] and hq.calls[-1][2] == {"wake": False} and shown() is False


def test_a_pull_feeds_each_delivery_as_gateway_and_acks_only_after_the_commit(api, hq, monkeypatch):
    trigger_id, made = relay_for(api, hq)
    seen = receiver(monkeypatch, outcomes={"d2": "duplicate", "d3": "refused", "d4": "rate_limited", "d5": "not_found"})
    for n in range(1, 6):
        hq.deliver(made["relay_id"], f"d{n}", body=f'{{"n": {n}}}'.encode())
    committed = []

    def check(through):
        with api.app.state.store.read() as c:  # what the ack covers is already committed
            committed.extend(r["target"] for r in c.execute("SELECT target FROM events WHERE action='test.received'"))
        assert through == 5 and hq.acks == []

    hq.on_ack = check
    service(api).tick()

    assert [s["delivery"] for s in seen] == ["d1", "d2", "d3", "d4", "d5"] and sorted(committed) == [
        "d1",
        "d2",
        "d3",
        "d4",
        "d5",
    ]
    first = seen[0]
    assert first == {
        "trigger": trigger_id,
        "body": b'{"n": 1}',
        "secret": None,
        "via": "gateway",
        "delivery": "d1",
        "headers": {"content-type": "application/json", "webhook-id": "d1"},
        "received_at": "2026-10-25T10:00:00.000000Z",
    }
    assert hq.acks == [5]  # every outcome is terminal
    status = get(api, "relay")["relay"]
    assert status["cursor"] == 5 and status["last_error"] == "" and status["last_pull"] and status["active"]
    service(api).tick()  # nothing new: no second pull-and-ack storm
    assert hq.acks == [5]


def test_a_delivery_for_a_trigger_without_this_relay_is_acked_and_never_received(api, hq, monkeypatch):
    _, made = relay_for(api, hq)
    seen = receiver(monkeypatch)
    hq.relays["hr_00000000000000ff"] = {"trigger_id": "someone-elses", "id": "hr_00000000000000ff", "key": "x"}
    hq.deliver("hr_00000000000000ff", "stranger")
    hq.deliver(made["relay_id"], "mine")
    service(api).tick()
    assert [s["delivery"] for s in seen] == ["mine"] and hq.acks == [2]


def test_a_receive_that_raises_is_not_acked_and_is_tried_again_then_skipped(api, hq, monkeypatch):
    _, made = relay_for(api, hq)
    seen = receiver(monkeypatch, boom=("bad",))
    hq.deliver(made["relay_id"], "good")
    hq.deliver(made["relay_id"], "bad")
    hq.deliver(made["relay_id"], "after")
    svc = service(api)
    svc.tick()
    assert [s["delivery"] for s in seen] == ["good"] and hq.acks == []  # nothing acked past the failure
    assert get(api, "relay")["relay"]["cursor"] == 1 and get(api, "relay")["relay"]["last_error"] == "receive_failed"
    for _ in range(hook_relay.POISON):
        svc.next_try = 0
        svc.tick()
    assert [s["delivery"] for s in seen] == ["good", "after"] and hq.acks[-1] == 3  # skipped for good, the rest came
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='trigger.relay_skipped'").fetchone()[0] == 1


def test_cursor_ahead_restarts_from_the_servers_cursor(api, hq, monkeypatch):
    _, made = relay_for(api, hq)
    receiver(monkeypatch)
    with api.app.state.store.transaction() as c:
        hook_relay.save(c, cursor=40, acked=40)
    hq.cursor_floor = 2
    hq.deliver(made["relay_id"], "a")
    hq.deliver(made["relay_id"], "b")
    hq.deliver(made["relay_id"], "c")
    service(api).tick()
    assert get(api, "relay")["relay"]["cursor"] == 3 and hq.acks == [3]  # reset to 2, then c


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


def test_a_failing_pull_keeps_the_cursor_backs_off_and_reaches_health(api, hq, monkeypatch):
    _, made = relay_for(api, hq)
    receiver(monkeypatch)
    hq.deliver(made["relay_id"], "d1")
    svc = service(api)
    svc.tick()
    assert hq.acks == [1]
    hq.down = True
    clock = [time.time()]
    monkeypatch.setattr(hook_relay, "now", lambda: clock[0])
    svc.next_try = 0
    svc.tick()
    first = svc.next_try - time.monotonic()
    svc.next_try = 0
    svc.tick()
    second = svc.next_try - time.monotonic()
    assert 10 < first <= 15 and 25 < second <= 30
    assert get(api, "relay")["relay"]["last_error"] == "unreachable" and get(api, "relay")["relay"]["cursor"] == 1
    with api.app.state.store.read() as c:
        assert hook_relay.health(c) is None  # not yet
    clock[0] += 16 * 60
    with api.app.state.store.read() as c:
        assert "cannot reach" in hook_relay.health(c)["summary"]
    hq.down = False
    svc.next_try = 0
    svc.tick()
    with api.app.state.store.read() as c:
        assert hook_relay.health(c) is None and get(api, "relay")["relay"]["last_error"] == ""


def test_a_rehearsal_never_pulls(api, hq, monkeypatch):
    _, made = relay_for(api, hq)
    seen = receiver(monkeypatch)
    hq.deliver(made["relay_id"], "d1")
    svc = service(api)
    before = len(hq.calls)
    svc.settings = dataclasses.replace(svc.settings, rehearsal=True)
    svc.tick()
    assert len(hq.calls) == before and seen == []


def test_deleting_a_trigger_deletes_its_address_at_the_gateway(api, hq, monkeypatch):
    _, made = relay_for(api, hq)
    receiver(monkeypatch)
    post(api, "triggers/shop/delete", {})
    assert made["relay_id"] in hq.relays  # no network inside the delete
    with api.app.state.store.read() as c:
        assert c.execute("SELECT deleted_at FROM trigger_relays").fetchone()[0]
    assert get(api, "relay")["relay"]["deleting"] == 1
    service(api).tick()
    assert made["relay_id"] not in hq.relays and get(api, "relay")["relay"]["deleting"] == 0
    # The same name made again starts with no address.
    post(api, "triggers", {"name": "Shop"})
    assert get(api, "triggers")["triggers"][0]["relay"] is None


def test_receipt_time_reaches_receive_in_the_servers_own_format_or_is_replaced(api, hq, monkeypatch):
    _, made = relay_for(api, hq)
    seen = receiver(monkeypatch)
    for delivery, at in (
        ("plus", "2026-10-25T10:00:00+00:00"),
        ("junk", "yesterday"),
        ("future", "2999-01-01T00:00:00Z"),
        ("none", None),
    ):
        hq.deliver(made["relay_id"], delivery)
        hq.queue[-1]["received_at"] = at
    before = H.now()
    service(api).tick()
    by = {s["delivery"]: s["received_at"] for s in seen}
    assert by["plus"] == "2026-10-25T10:00:00.000000Z"
    now = H.now()
    for name in ("junk", "future", "none"):  # replaced by the time of the pull, whichever side of midnight it ran
        assert before <= by[name] <= now and by[name].endswith("Z") and len(by[name]) == len(now)


def test_the_owner_sets_and_clears_the_wake_address_and_the_secret_is_shown_once(api, hq):
    assert get(api, "hooks/wake-url") == {"url": None, "set_at": None, "set_by": None}
    url = "https://wake.acme.example/start?token=t0ken"
    made = put(api, "hooks/wake-url", {"url": url})
    assert made == {"url": url, "secret": "whsec_test"}
    method, path, body, headers = hq.calls[-1]
    assert (method, path, body) == ("POST", "wake", {"url": url, "secret": None}) and headers["idempotency-key"]
    shown = get(api, "hooks/wake-url")
    assert (
        shown["url"] == url and shown["set_by"] == "human:ana" and shown["set_at"] and "whsec" not in json.dumps(shown)
    )
    with api.app.state.store.read() as c:  # HQ signs: the secret is nowhere here, and the audit names only the host
        assert "whsec_test" not in json.dumps([tuple(r) for r in c.execute("SELECT * FROM hook_relay_state")])
        audit = [dict(r) for r in c.execute("SELECT action, detail FROM events WHERE action LIKE 'hooks.wake_url%'")]
    assert [a["action"] for a in audit] == ["hooks.wake_url_set"] and "t0ken" not in audit[0]["detail"]
    assert "wake.acme.example" in audit[0]["detail"]

    assert put(api, "hooks/wake-url", {"url": None}) == {"url": None, "secret": None}
    assert hq.calls[-1][2] == {"url": None, "secret": None} and hq.wake is None
    assert get(api, "hooks/wake-url") == {"url": None, "set_at": None, "set_by": None}


def test_only_the_owner_reads_or_changes_the_wake_address(api, hq):
    for token in ("ben-test", "cara-test"):
        get(api, "hooks/wake-url", token=token, expected=403)
        put(api, "hooks/wake-url", {"url": "https://wake.acme.example/start"}, token=token, expected=403)
    assert hq.calls == []


def test_the_wake_address_needs_the_gateway_key_and_never_leaves_a_rehearsal(api, hq, monkeypatch):
    url = {"url": "https://wake.acme.example/start"}
    monkeypatch.delenv("TICO_GATEWAY_KEY")
    refused = put(api, "hooks/wake-url", url, expected=409)
    assert "Settings > Models" in refused["error"]["detail"] and hq.calls == []
    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
    svc = service(api)
    monkeypatch.setattr(svc, "settings", dataclasses.replace(svc.settings, rehearsal=True))
    put(api, "hooks/wake-url", url, expected=409)
    assert hq.calls == [] and get(api, "hooks/wake-url")["url"] is None


def test_every_non_plain_https_wake_address_is_refused_before_hq_is_asked(api, hq):
    urls = [
        "http://wake.acme.example/start",
        "https://ana:pw@wake.acme.example/start",
        "ftp://wake.acme.example",
        "https:///start",
        "wake.acme.example",
        "",
        "https://wake.acme.example/a b",
    ]
    for url in urls:
        put(api, "hooks/wake-url", {"url": url}, expected=422)
        assert hq.calls == [], url


def test_an_address_hq_refuses_is_a_422_and_leaves_the_old_one(api, hq):
    put(api, "hooks/wake-url", {"url": "https://wake.acme.example/start"})
    refused = put(api, "hooks/wake-url", {"url": "https://bad.acme.example/start"}, expected=422)
    assert "public https" in refused["error"]["detail"]
    assert get(api, "hooks/wake-url")["url"] == "https://wake.acme.example/start"
