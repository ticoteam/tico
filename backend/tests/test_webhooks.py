"""Outgoing webhooks (backend/webhooks.py, backend/webhook_delivery.py, backend/egress.py): who hears of what, the exact
bytes and signature that leave, how a failure is retried, and what the network guard refuses."""
import base64
import hashlib
import hmac
import ipaddress
import json
import time

import httpx
import pytest

from backend import egress
from backend import hubdb as H
from backend import webhook_delivery as D
from backend import webhooks as W
from backend.tests.test_tasks_board import api, bot_token, get, headers  # noqa: F401
from backend.tests.test_tasks_board import post as _post

PUBLIC = [ipaddress.ip_address("93.184.216.34")]
URL = "https://hooks.acme.example/tico"


def verify(secret, headers_, body, now=None, tolerance=300):
    """A Standard Webhooks verifier written from the spec text, not from our signer: the signed content is
    `id.timestamp.body`, the key is the base64 payload after `whsec_`, the header holds space-separated `v1,<sig>`."""
    sent = int(headers_["webhook-timestamp"])
    assert abs((now or time.time()) - sent) <= tolerance
    key = base64.b64decode(secret.split("_", 1)[1])
    want = base64.b64encode(hmac.new(key, f"{headers_['webhook-id']}.{headers_['webhook-timestamp']}.".encode() + body,
                                     hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(want, sig.split(",", 1)[1]) for sig in headers_["webhook-signature"].split(" ")
               if sig.startswith("v1,"))


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
    return post(api, "webhooks", {"name": "Acme hook", "url": url, "events": list(events), **more}, token=token)["webhook"]


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


def test_the_standard_webhooks_vector_and_our_signature_verify_with_an_independent_verifier():
    published = {"webhook-id": "msg_p5jXN8AQM9LWM0D4loKWxJek", "webhook-timestamp": "1614265330",
                 "webhook-signature": "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="}
    secret, body = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw", b'{"test": 2432232314}'
    assert verify(secret, published, body, now=1614265330)
    assert D.signature(secret, published["webhook-id"], 1614265330, body) == published["webhook-signature"]
    assert not verify(secret, published, body + b" ", now=1614265330)


def test_a_delivery_carries_a_fresh_timestamp_a_stable_id_and_a_signature_the_receiver_verifies(api, net):
    made = make(api, events=["custom.*"])
    assert made["secret"].startswith("whsec_") and made["replayed"] is False
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.order", "data": {"order": 42}})
    net.reply = lambda request: httpx.Response(500)
    assert run(api, net) == 1
    first = net.seen[0]
    due(api)
    time.sleep(1.1)                                         # a second tick of the clock the timestamp reads
    assert run(api, net) == 1
    second = net.seen[1]
    assert first.headers["webhook-id"] == second.headers["webhook-id"] == sends(api)[0]["id"]
    assert first.headers["webhook-timestamp"] != second.headers["webhook-timestamp"]
    assert first.content == second.content
    for request in (first, second):
        assert verify(made["secret"], dict(request.headers), request.content)
    envelope = json.loads(first.content)
    assert set(envelope) == {"type", "version", "timestamp", "data", "links"}
    assert envelope["type"] == "custom.order" and envelope["data"] == {"order": 42} and envelope["version"] == 1
    attempts = get(api, f"webhooks/{made['id']}")["recent_attempts"]
    assert [a["status"] for a in attempts] == [500, 500]
    # The request went to the checked address, with the name kept for Host.
    assert first.url.host == "93.184.216.34" and first.headers["host"] == "hooks.acme.example"


def test_the_secret_is_shown_once_and_a_rotation_signs_with_both_for_a_day(api, net):
    made = make(api)
    again = post(api, "webhooks", {"name": "Acme hook", "url": URL, "events": ["*"]}, key="same")["webhook"]
    replay = post(api, "webhooks", {"name": "Acme hook", "url": URL, "events": ["*"]}, key="same")["webhook"]
    assert again["secret"] and replay["replayed"] is True and "secret" not in replay
    assert "secret" not in get(api, f"webhooks/{made['id']}")["webhook"]
    new = post(api, f"webhooks/{made['id']}/rotate", {})["webhook"]["secret"]
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.x"})
    run(api, net)
    request = net.seen[-1]
    assert request.headers["webhook-signature"].count("v1,") == 2
    assert verify(new, dict(request.headers), request.content) and verify(made["secret"], dict(request.headers), request.content)
    with api.app.state.store.transaction() as c:                      # past the day, only the new one signs
        c.execute("UPDATE webhooks SET secret_prev_until=? WHERE id=?", (H.shift(H.now(), hours=-1), made["id"]))
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.y"})
    run(api, net)
    late = net.seen[-1]
    assert late.headers["webhook-signature"].count("v1,") == 1 and not verify(made["secret"], dict(late.headers), late.content)


def test_retries_follow_the_schedule_then_give_up_once_and_say_so_once_a_day(api, net):
    for failed in range(1, 8):
        low, high = D.RETRY_AFTER[failed - 1] * 0.9, D.RETRY_AFTER[failed - 1] * 1.1
        assert all(low <= D.retry_delay(failed) <= high for _ in range(20))
    assert D.retry_delay(8) is None and D.RETRY_AFTER == (5, 300, 1800, 7200, 18000, 36000, 36000)
    made = make(api, events=["custom.*"])     # not "*": the give-up notice is itself a task event
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.a"})
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.b"})
    net.reply = lambda request: httpx.Response(302, headers={"location": "https://elsewhere.example/"})
    for _ in range(8):
        run(api, net)
        due(api)
    rows = sends(api)
    assert [r["state"] for r in rows] == ["failed", "failed"] and all(r["attempts"] == 8 for r in rows)
    assert all(r["last_status"] == 302 for r in rows)                 # a redirect is a failed try and is never followed
    assert {r.url.host for r in net.seen} == {"93.184.216.34"}
    with api.app.state.store.read() as c:
        events = c.execute("SELECT detail FROM events WHERE action='webhook.exhausted'").fetchall()
        notices = c.execute("SELECT id FROM tasks WHERE title LIKE 'Webhook % is not receiving events'").fetchall()
    assert len(events) == 2 and "order" not in events[0][0] and len(notices) == 1
    health = get(api, "health")["checks"]
    assert any(c["id"] == "webhooks" and "Acme hook" in c["summary"] for c in health)
    # A person retries one by hand: same id, tried again from the start.
    shown = get(api, f"webhooks/{made['id']}/sends")["items"]
    net.reply = lambda request: httpx.Response(204)
    post(api, f"webhooks/{made['id']}/sends/{shown[0]['id']}/retry", {})
    run(api, net)
    assert get(api, f"webhooks/{made['id']}/sends/{shown[0]['id']}")["send"]["state"] == "delivered"


def test_an_abort_answer_cancels_and_a_server_error_does_not(api, net):
    made = make(api)
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.a"})
    net.reply = lambda request: httpx.Response(410, headers={"webhook-delivery": "abort-message"})
    run(api, net)
    assert sends(api)[0]["state"] == "cancelled"
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.b"})
    net.reply = lambda request: httpx.Response(503)
    run(api, net)
    assert sends(api)[1]["state"] == "ready" and sends(api)[1]["attempts"] == 1


@pytest.mark.parametrize("url", ["http://hooks.acme.example/x", "https://user:pw@hooks.acme.example/x", "ftp://hooks.acme.example/x"])
def test_an_address_the_guard_refuses_cannot_be_saved(api, url):
    post(api, "webhooks", {"name": "Bad", "url": url, "events": ["*"]}, expected=422)


@pytest.mark.parametrize("url", ["https://10.0.0.1/", "https://169.254.169.254/latest", "https://192.168.1.10/x", "https://localhost/x",
                                 "https://[::1]/x", "https://[::ffff:10.0.0.5]/x", "https://2130706433/x", "https://127.1/x",
                                 "https://api.localhost/x", "https://printer.local/x", "https://db.internal/x", "https://localhost./x"])
def test_an_https_address_that_is_not_public_by_its_spelling_cannot_be_saved_or_set(api, url):
    """No name lookup at save: an IP literal outside public space, or a local name, is refused like its http twin."""
    post(api, "webhooks", {"name": "Bad", "url": url, "events": ["*"]}, expected=422)
    post(api, "webhooks", {"name": "Bad", "url": url, "events": ["*"], "allow_http": True}, expected=422)
    made = make(api)
    refused = post(api, f"webhooks/{made['id']}/update", {"url": url}, expected=422)
    assert "public" in refused["error"]["detail"] or "local" in refused["error"]["detail"]
    assert get(api, f"webhooks/{made['id']}")["webhook"]["url"] == made["url"]


def test_a_public_ip_literal_and_a_name_that_only_looks_local_can_be_saved(api):
    make(api, "https://8.8.8.8/hook")
    make(api, "https://dead.beef/hook")


def test_plain_http_is_for_localhost_and_the_owner_switch_alone(api, net):
    post(api, "webhooks", {"name": "Dev", "url": "http://localhost:9000/x", "events": ["*"]}, expected=422)
    post(api, "webhooks", {"name": "Dev", "url": "http://localhost:9000/x", "events": ["*"], "allow_http": True},
         token="ben-test", expected=403)
    post(api, "webhooks", {"name": "Dev", "url": "http://hooks.acme.example/x", "events": ["*"], "allow_http": True}, expected=422)
    made = make(api, "http://localhost:9000/x", allow_http=True)
    assert made["allow_http"] is True
    post(api, f"webhooks/{made['id']}/update", {"allow_http": False}, token="ben-test", expected=403)


def test_the_guard_refuses_private_addresses_proxies_and_follows_no_redirect(monkeypatch):
    seen = {}
    real = httpx.Client

    def spy(*args, **kw):
        seen.update(kw)
        return real(*args, **kw)
    monkeypatch.setattr(httpx, "Client", spy)
    transport = httpx.MockTransport(lambda request: httpx.Response(307, headers={"location": "http://127.0.0.1/"}))
    for address in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "::1", "::ffff:10.0.0.5", "fd00::1"):
        monkeypatch.setattr(egress, "resolve", lambda host, a=address: [ipaddress.ip_address(a)])
        with pytest.raises(egress.Blocked):
            egress.post("https://hooks.acme.example/x", b"{}", {}, transport=transport)
    # A name with one private address among public ones is refused too.
    monkeypatch.setattr(egress, "resolve", lambda host: PUBLIC + [ipaddress.ip_address("10.1.1.1")])
    with pytest.raises(egress.Blocked):
        egress.post("https://hooks.acme.example/x", b"{}", {}, transport=transport)
    monkeypatch.setattr(egress, "resolve", lambda host: PUBLIC)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.acme.example:3128")
    answer = egress.post("https://hooks.acme.example/x", b"{}", {}, transport=transport)
    assert answer.status == 307
    assert seen["trust_env"] is False and seen["follow_redirects"] is False and seen["timeout"] == 30.0
    for url in ("https://user@hooks.acme.example/", "http://hooks.acme.example/", "https://hooks.acme.example:bad/"):
        with pytest.raises(egress.Blocked):
            egress.check(url)
    # Loopback http needs the endpoint's flag and a loopback address.
    monkeypatch.setattr(egress, "resolve", lambda host: [ipaddress.ip_address("127.0.0.1")])
    assert egress.post("http://localhost:9000/x", b"{}", {}, allow_http=True, transport=transport).status == 307
    with pytest.raises(egress.Blocked):
        egress.post("http://localhost:9000/x", b"{}", {}, allow_http=False, transport=transport)
    monkeypatch.setattr(egress, "resolve", lambda host: PUBLIC)
    with pytest.raises(egress.Blocked):
        egress.post("http://hooks.acme.example/x", b"{}", {}, allow_http=True, transport=transport)


def test_a_blocked_address_at_send_time_is_a_failed_attempt_with_the_reason(api, net, monkeypatch):
    made = make(api)
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.a"})
    monkeypatch.setattr(egress, "resolve", lambda host: [ipaddress.ip_address("10.0.0.9")])
    run(api, net)
    row = sends(api)[0]
    assert row["state"] == "ready" and "not public" in row["last_error"] and not net.seen


def test_a_private_task_is_never_published_to_an_endpoint_whose_owner_cannot_read_it(api, net):
    ben, priya = make(api, events=["task.*"], token="ben-test"), make(api, events=["task.*"], token="priya-test")
    ana = make(api, events=["task.*"])
    with api.app.state.store.transaction() as c:
        private = H.task_create(c, "human:ben", "Review the sensitive packet", "Details.", "bot:cpo", private=True, lint=False)
        public = H.task_create(c, "human:ben", "Plan the launch", "Details.", "bot:cpo", lint=False)
    by_endpoint = {hook["id"]: {s["subject"] for s in sends(api, hook["id"]) if s["type"] == "task.created"}
                   for hook in (ben, priya, ana)}
    assert by_endpoint[ben["id"]] == {private["id"], public["id"]}
    assert by_endpoint[priya["id"]] == {public["id"]}
    assert by_endpoint[ana["id"]] == {private["id"], public["id"]}  # The bot party's manager is a reader.
    for row in sends(api, priya["id"]):
        assert "sensitive" not in row["body"]
    envelope = json.loads(next(s for s in sends(api, ben["id"]) if s["subject"] == public["id"] and s["type"] == "task.created")["body"])
    assert envelope["type"] == "task.created" and envelope["data"]["title"] == "Plan the launch"
    assert envelope["links"]["app"].endswith("#/task/" + public["id"])


def test_task_changes_are_published_with_their_own_names_and_a_retried_write_sends_nothing_twice(api, net):
    made = make(api, events=["task.done", "task.state_changed", "task.assigned"])
    task = _post(api, "tasks", {"owner": "ops", "title": "Ship it", "body": "Ship it."})
    doing = _post(api, f"tasks/{task['id']}", {"version": task["version"], "status": "doing"})
    _post(api, f"tasks/{task['id']}", {"version": doing["version"], "status": "done", "note": "Shipped."})
    kinds = [s["type"] for s in sends(api, made["id"])]
    assert kinds.count("task.state_changed") == 1 and kinds.count("task.done") == 1
    first = publish(api, "task.done", task["id"], {"id": task["id"], "title": "Ship it"}, "bot:ops", revision="r1")
    second = publish(api, "task.done", task["id"], {"id": task["id"], "title": "Ship it"}, "bot:ops", revision="r1")
    assert (first, second) == (1, 0)


def test_catalog_examples_have_the_top_level_keys_their_producers_send(api, net):
    from backend import event_catalog as catalog
    made = make(api, events=["task.*", "approval.*"])
    task = _post(api, "tasks", {"owner": "ops", "title": "Ship it", "body": "Ship it."})
    _post(api, f"tasks/{task['id']}/comments", {"text": "Soon."})
    doing = _post(api, f"tasks/{task['id']}", {"version": task["version"], "status": "doing"})
    _post(api, f"tasks/{task['id']}", {"version": doing["version"], "status": "done", "note": "Shipped."})
    with api.app.state.store.transaction() as c:
        approval = H.approval_request(c, "bot:ops", "send", {"to": "x@acme.example", "cc": "", "subject": "Hi",
                                                            "body_sha256": "a" * 64, "mailbox": "ops"})
        H.approval_decide(c, "human:ana", approval["id"], "approved")
    sent = {s["type"]: json.loads(s["body"])["data"] for s in sends(api, made["id"])}
    assert {"task.created", "task.commented", "task.state_changed", "task.done", "approval.requested",
            "approval.decided"} <= set(sent)
    for name, data in sent.items():
        assert set(data) == set(catalog.EVENTS[name].example), name


def test_the_strategy_bot_and_a_bot_without_the_send_switch_are_refused(api):
    made = make(api)
    assert publish(api, "custom.x", "s", {}, "bot:goal-manager") == 0       # the broad path never raises, and sends nothing
    with api.app.state.store.transaction() as c:
        with pytest.raises(W.Refused, match="Strategy Bot"):
            W.publish(c, "custom.x", "s", {}, "bot:goal-manager", only=made["id"])
        with pytest.raises(W.Refused, match="switch is off"):
            W.publish(c, "custom.x", "s", {}, "bot:ops", only=made["id"])
        with pytest.raises(W.Refused, match="does not exist"):
            W.publish(c, "custom.x", "s", {}, "human:ana", only="ep_missing")
        c.execute("INSERT INTO bot_mail_settings(bot,outbound_send,forward_to,updated_at,updated_by) VALUES('ops',1,'[]',?,?)",
                  (H.now(), "human:ana"))
        assert W.publish(c, "custom.x", "s", {}, "bot:ops", only=made["id"]) == 1
    post(api, f"webhooks/{made['id']}/pause", {})
    with api.app.state.store.transaction() as c:
        with pytest.raises(W.Refused, match="paused"):
            W.publish(c, "custom.x", "s", {}, "human:ana", only=made["id"])


def test_a_bot_asks_a_person_approves_and_the_person_owns_the_endpoint(api, net):
    bot = bot_token(api, "ops")
    body = {"name": "Ops hook", "url": URL, "events": ["custom.*"]}
    asked = post(api, "webhooks", body, token=bot)
    assert asked["approval"]["status"] == "pending" and asked["webhook"] is None
    again = post(api, "webhooks", body, token=bot, key="another")
    assert again["approval"]["id"] == asked["approval"]["id"]
    assert get(api, "webhooks")["items"] == []
    post(api, f"approvals/{asked['approval']['id']}", {"decision": "approved"})
    made = post(api, "webhooks", body, token=bot)["webhook"]
    assert made["created_by"] == "human:ana" and made["requested_by"] == "bot:ops" and made["url"].endswith("/…")
    assert [h["id"] for h in get(api, "webhooks")["items"]] == [made["id"]]
    again = post(api, "webhooks", body, token=bot, key="third")             # the approval was spent: a new ask
    assert again["approval"]["id"] != asked["approval"]["id"]
    post(api, f"webhooks/{made['id']}/rotate", {}, token=bot, expected=403)     # a bot manages none
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.x"}, token=bot, expected=403)   # switch off


def test_people_manage_their_own_endpoints_and_the_owner_all(api, net):
    mine = make(api, token="ben-test")
    assert get(api, "webhooks", token="priya-test")["items"] == []
    get(api, f"webhooks/{mine['id']}", token="priya-test", expected=404)
    post(api, f"webhooks/{mine['id']}/pause", {}, token="priya-test", expected=403)
    listed = get(api, "webhooks")["items"]                                      # the owner sees it, and may change it
    assert [h["id"] for h in listed] == [mine["id"]] and listed[0]["can_change"] is True
    updated = post(api, f"webhooks/{mine['id']}/update", {"name": "Renamed", "events": ["task.done"]})["webhook"]
    assert updated["name"] == "Renamed" and updated["events"] == ["task.done"] and updated["revision"] == 2
    post(api, f"webhooks/{mine['id']}/update", {"name": "x", "expected_revision": 1}, expected=409)
    paused = post(api, f"webhooks/{mine['id']}/pause", {"reason": "Maintenance"})["webhook"]
    assert paused["enabled"] is False and paused["paused_reason"] == "Maintenance"
    post(api, f"webhooks/{mine['id']}/send", {"type": "custom.x"}, token="ben-test", expected=409)
    post(api, f"webhooks/{mine['id']}/resume", {})
    post(api, f"webhooks/{mine['id']}/send", {"type": "custom.x"}, token="ben-test")
    post(api, f"webhooks/{mine['id']}/send", {"type": "task.done"}, token="ben-test", expected=422)   # custom names only
    post(api, f"webhooks/{mine['id']}/example", {"type": "task.done"}, token="ben-test")
    assert {s["type"] for s in sends(api, mine["id"])} == {"custom.x", "task.done"}
    gone = post(api, f"webhooks/{mine['id']}/delete", {}, token="ben-test")
    assert gone["cancelled"] == 2 and {s["state"] for s in sends(api, mine["id"])} == {"cancelled"}
    get(api, f"webhooks/{mine['id']}", token="ben-test", expected=404)


def test_the_list_counts_seven_days_and_the_send_log_shows_state_attempts_and_next_try(api, net):
    made = make(api)
    for name in ("a", "b", "c"):
        post(api, f"webhooks/{made['id']}/send", {"type": f"custom.{name}"})
    net.reply = lambda request: httpx.Response(200) if b"custom.a" in request.content else httpx.Response(500)
    run(api, net)
    listed = get(api, "webhooks")["items"][0]
    assert listed["last_7_days"] == {"delivered": 1, "failed": 0, "pending": 2}
    log = get(api, f"webhooks/{made['id']}/sends")
    states = {row["type"]: row for row in log["items"]}
    assert states["custom.a"]["state"] == "delivered" and states["custom.a"]["next_attempt_at"] is None
    assert states["custom.b"]["state"] == "ready" and states["custom.b"]["attempts"] == 1
    assert states["custom.b"]["last_status"] == 500 and states["custom.b"]["next_attempt_at"]
    assert log["more"] is False
    only = get(api, f"webhooks/{made['id']}/sends?state=delivered")["items"]
    assert [row["type"] for row in only] == ["custom.a"]


def test_at_most_two_sends_to_one_host_are_in_flight_and_a_crashed_worker_gives_its_send_back(api, net):
    first, second = make(api), make(api)
    for hook in (first, second):
        for n in range(2):
            post(api, f"webhooks/{hook['id']}/send", {"type": f"custom.n{n}"})
    store = api.app.state.store
    now = H.now()
    claimed = [D.claim(store, now) for _ in range(4)]
    assert len([c for c in claimed if c]) == 2                                  # the third waits for a free slot
    with store.transaction() as c:
        c.execute("UPDATE webhook_sends SET claimed_at=? WHERE state='sending'", (H.shift(now, seconds=-300),))
    assert D.recover(store, now) == 2
    assert {s["state"] for s in sends(api)} == {"ready"}
    # A second host is not held up by the first.
    other = make(api, "https://other.acme.example/tico")
    post(api, f"webhooks/{other['id']}/send", {"type": "custom.z"})
    with store.transaction() as c:
        c.execute("UPDATE webhook_sends SET state='sending', claimed_at=? WHERE webhook_id IN (?,?)", (now, first["id"], second["id"]))
    assert D.claim(store, H.now()) is not None and sends(api, other["id"])[0]["state"] == "sending"


def test_an_hourly_rate_makes_sends_wait_and_never_drops_them(api, net):
    made = make(api, rate_per_hour=2)
    for n in range(3):
        post(api, f"webhooks/{made['id']}/send", {"type": f"custom.n{n}"})
    assert run(api, net) == 2
    waiting = [s for s in sends(api) if s["state"] == "ready"]
    assert len(waiting) == 1 and waiting[0]["next_attempt_at"] > H.now()


def test_a_copy_for_rehearsal_sends_nothing_and_says_so(api, net):
    made = make(api)
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.a"})
    assert run(api, net, rehearsal=True) == 0
    row = sends(api)[0]
    assert row["state"] == "withheld" and not net.seen
    post(api, f"webhooks/{made['id']}/sends/{row['id']}/retry", {}, expected=409)


def test_an_event_reaches_at_most_twenty_endpoints_and_health_says_how_many_were_skipped(api, net):
    for n in range(22):
        make(api, events=["custom.*"])
    assert publish(api, "custom.big", "s1", {"n": 1}, "human:ana") == 20
    with api.app.state.store.read() as c:
        assert c.execute("SELECT skipped FROM webhook_events").fetchone()[0] == 2
    summary = [c for c in get(api, "health")["checks"] if c["id"] == "webhooks"]
    assert summary and "skipped" in summary[0]["summary"]


def test_the_hourly_cleanup_keeps_a_month_of_sends_and_two_hundred_attempts(api, net):
    made = make(api)
    old = H.shift(H.now(), days=-31)
    with api.app.state.store.transaction() as c:
        for n in range(3):
            c.execute("INSERT INTO webhook_events(id,type,subject,created_at) VALUES(?,?,?,?)", (f"e{n}", "custom.x", "s", old if n < 2 else H.now()))
        c.execute("INSERT INTO webhook_sends(id,event_id,webhook_id,type,body,state,next_attempt_at,created_at) VALUES('old','e0',?,'custom.x','{}','delivered',?,?)",
                  (made["id"], old, old))
        c.execute("INSERT INTO webhook_sends(id,event_id,webhook_id,type,body,state,next_attempt_at,created_at) VALUES('new','e2',?,'custom.x','{}','delivered',?,?)",
                  (made["id"], H.now(), H.now()))
        c.execute("INSERT INTO webhook_sends(id,event_id,webhook_id,type,body,state,next_attempt_at,created_at) VALUES('wait','e1',?,'custom.x','{}','ready',?,?)",
                  (made["id"], old, old))
        for n in range(250):
            c.execute("INSERT INTO webhook_attempts(send_id,webhook_id,n,at,timestamp_sent) VALUES('new',?,?,?,0)",
                      (made["id"], n + 1, H.shift(H.now(), seconds=n)))
        D.prune(c)
        assert [r[0] for r in c.execute("SELECT id FROM webhook_sends ORDER BY id")] == ["new", "wait"]
        assert c.execute("SELECT count(*) FROM webhook_events").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM webhook_attempts").fetchone()[0] == 200
        assert c.execute("SELECT min(n) FROM webhook_attempts").fetchone()[0] == 51        # the newest 200 stay


def test_the_other_events_are_published_where_they_happen(api, net):
    made = make(api, events=["approval.*", "needs_you.*", "bot.*"])
    with api.app.state.store.transaction() as c:
        approval = H.approval_request(c, "bot:ops", "send", {"to": "x@acme.example", "cc": "", "subject": "Hi",
                                                            "body_sha256": "a" * 64, "mailbox": "ops"})
        H.approval_decide(c, "human:ana", approval["id"], "approved")
    kinds = {s["type"] for s in sends(api, made["id"])}
    assert {"approval.requested", "approval.decided"} <= kinds
    body = json.loads(next(s for s in sends(api, made["id"]) if s["type"] == "approval.decided")["body"])
    assert body["data"]["decision"] == "approved" and "payload" not in body["data"]


def test_an_endpoint_cannot_subscribe_to_events_only_rules_see(api):
    for name in ("webhook.received", "tico.notice"):
        assert "rule with forward" in json.dumps(post(api, "webhooks", {"name": "x", "url": URL, "events": [name]},
                                                      expected=422))
    make(api, events=["*"])                                            # a star still works: it means what is sent


def test_publish_to_one_endpoint_checks_the_actor_against_that_endpoint(api):
    bens = make(api, events=["custom.*"], token="ben-test")
    api.app.state.auth.owner_email = "ana@acme.example"        # the roster's owner, as a rule's maker is rebuilt from it
    with api.app.state.store.transaction() as c:
        with pytest.raises(W.Refused, match="may send to it"):
            W.publish(c, "custom.x", "s", {}, "human:priya", only=bens["id"])
        assert W.publish(c, "custom.x", "s", {}, "human:ben", only=bens["id"]) == 1
        assert W.publish(c, "custom.y", "s", {}, "human:ana", only=bens["id"]) == 1     # an admin may
        assert W.publish(c, "custom.z", "s", {}, H.KEEPER, only=bens["id"]) == 1
        c.execute("INSERT INTO bot_mail_settings(bot,outbound_send,forward_to,updated_at,updated_by) VALUES('ops',1,'[]',?,?)",
                  (H.now(), "human:ana"))
        narrow = W.publish(c, "custom.w", "s", {}, "bot:ops", only=bens["id"])
        assert narrow == 1
        c.execute("UPDATE webhooks SET events='[\"task.*\"]' WHERE id=?", (bens["id"],))
        with pytest.raises(W.Refused, match="does not listen"):
            W.publish(c, "custom.v", "s", {}, "bot:ops", only=bens["id"])


def test_a_custom_event_about_a_private_task_is_refused_to_an_endpoint_whose_owner_cannot_read_it(api):
    priyas = make(api, events=["custom.*"], token="priya-test")
    bens = make(api, events=["custom.*"], token="ben-test")
    with api.app.state.store.transaction() as c:
        private = H.task_create(c, "human:ben", "Review the sensitive packet", "Details.", "bot:cpo", private=True, lint=False)
        with pytest.raises(W.Refused, match="may not read this"):
            W.publish(c, "custom.x", private["id"], {"text": "sensitive"}, H.KEEPER, only=priyas["id"])
        assert W.publish(c, "custom.x", private["id"], {"text": "sensitive"}, H.KEEPER, only=bens["id"]) == 1
        assert W.publish(c, "custom.x", "not-a-task", {"text": "fine"}, H.KEEPER, only=priyas["id"]) == 1


def test_a_server_that_drips_its_answer_is_cut_off_at_the_total_deadline(monkeypatch):
    monkeypatch.setattr(egress, "resolve", lambda host: PUBLIC)
    monkeypatch.setattr(egress, "DEADLINE", 0.2)

    def slow(request):
        time.sleep(0.5)
        return httpx.Response(200)
    with pytest.raises(egress.Blocked, match="took longer"):
        egress.post("https://hooks.acme.example/x", b"{}", {}, transport=httpx.MockTransport(slow))

    def hang(host):
        time.sleep(1)
        return PUBLIC
    monkeypatch.setattr(egress, "resolve", hang)
    with pytest.raises(egress.Blocked, match="name lookup"):
        egress.post("https://hooks.acme.example/x", b"{}", {}, transport=httpx.MockTransport(slow))


def test_an_endpoint_whose_owner_has_left_hears_nothing(api, net, monkeypatch):
    from backend import auth as A
    ben, ana = make(api, events=["task.*", "custom.*"], token="ben-test"), make(api, events=["task.*", "custom.*"])
    monkeypatch.setattr(A, "standing", lambda c, pid: "left" if pid == "ben" else "")
    with api.app.state.store.transaction() as c:
        public = H.task_create(c, "human:ben", "Plan the launch", "Details.", "bot:cpo", lint=False)     # Ben is still a party
    publish(api, "custom.note", "n1", {"text": "company news"}, H.KEEPER)
    assert sends(api, ben["id"]) == []
    assert {s["subject"] for s in sends(api, ana["id"])} >= {public["id"], "n1"}


def test_a_paused_endpoint_has_the_events_of_its_pause_kept_for_send_missed(api, net):
    only = make(api, events=["custom.*"])
    post(api, f"webhooks/{only['id']}/pause", {})
    assert publish(api, "custom.a", "during", {"n": 1}, "human:ana") == 0
    post(api, f"webhooks/{only['id']}/resume", {})
    assert sends(api, only["id"]) == []
    assert post(api, f"webhooks/{only['id']}/replay", {"since": "2020-01-01"})["queued"] == 1
    assert [s["subject"] for s in sends(api, only["id"])] == ["during"]
    with api.app.state.store.transaction() as c:                    # no endpoint at all: nothing is journaled
        c.execute("UPDATE webhooks SET deleted_at=?", (H.now(),))
        before = c.execute("SELECT count(*) FROM webhook_events").fetchone()[0]
        W.publish(c, "custom.a", "none", {"n": 2}, "human:ana")
        assert c.execute("SELECT count(*) FROM webhook_events").fetchone()[0] == before


def test_endpoints_that_cannot_read_an_event_do_not_use_up_its_fan_out_places(api, net, monkeypatch):
    monkeypatch.setattr(W, "MAX_PER_EVENT", 2)
    blind = [make(api, events=["task.*"], token="priya-test") for _ in range(2)]       # older, and cannot read a private task
    ben = make(api, events=["task.*"], token="ben-test")
    with api.app.state.store.transaction() as c:
        private = H.task_create(c, "human:ben", "Review the sensitive packet", "Details.", "bot:cpo", private=True, lint=False)
    assert [s["subject"] for s in sends(api, ben["id"]) if s["type"] == "task.created"] == [private["id"]]
    assert all(sends(api, b["id"]) == [] for b in blind)
