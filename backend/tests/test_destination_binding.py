"""Saved credentials stay with the service they were saved for (backend/destinations.py): the Gateway connection and
outgoing webhooks with secret headers hold a save that would send them elsewhere until the editor keeps or clears them,
BotOps acting for a person turns that into the person's own Confirm card, and every such change leaves one audit event
that shows scheme, host and port only. Ana owns the team, Ben is an admin, Cara is a member (test_api's fixture)."""
import json

import pytest

from backend import destinations
from backend import gateway_connection
from backend import webhook_delivery as D
from backend import webhooks as W
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, put, ready, runner  # noqa: F401  (fixture)
from backend.tests.test_botops_confirm_cards import confirm
from backend.tests.test_botops_parity import act
from backend.tests.test_gateway_connection import KEY, OTHER, fake_gateway, save, setup
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixtures)
from backend.tests.test_webhooks import net, run  # noqa: F401  (fixture)

SECRET = "hdr-synthetic-secret-value-7781"
HOOK = "https://hooks.acme.example/tico/team-a?route=alpha"


# ------------------------------------------------------------------------------------------------- the one rule
@pytest.mark.parametrize("old,new,path_counts,same", [
    ("https://gw.acme.example/v1/gateway", "https://GW.Acme.Example:443/v2/other", False, True),   # case, default port, path
    ("https://gw.acme.example/v1", "https://gw.acme.example:8443/v1", False, False),             # port
    ("http://localhost:8080/v1", "https://localhost:8080/v1", False, False),                    # http to https
    ("https://gw.acme.example/v1", "https://gw.acme.example.evil.example/v1", False, False),      # host
    ("https://bücher.example/x", "https://xn--bcher-kva.example/x", False, True),                 # punycode is one host
    ("http://example.com/x", "http://example.com:80/x", False, True),
    ("https://hooks.acme.example/a", "https://hooks.acme.example/b", True, False),                # webhook: path counts
    ("https://hooks.acme.example/a?x=1", "https://hooks.acme.example/a?x=2", True, False),        # and query
    ("https://hooks.acme.example/a?x=1", "https://hooks.acme.example:443/a?x=1", True, True),
    ("https://[::1]:9000/a", "https://[::1]:9000/b", False, True),
    ("not a url", "https://gw.acme.example", False, False),
])
def test_same_service_is_scheme_host_and_effective_port_and_for_secret_headers_the_path_too(old, new, path_counts, same):
    assert destinations.same_service(old, new, path_counts=path_counts) is same


def test_the_safe_identity_shows_scheme_host_and_port_and_nothing_else():
    url = "https://user:pw@Hooks.Acme.Example/tenant-zeta/secret-path?token=abc#frag"
    shown = destinations.safe(url)
    assert shown == {"address": "https://hooks.acme.example:443", "scheme": "https", "host": "hooks.acme.example", "port": 443}
    moved = destinations.change(url, "https://hooks.acme.example/tenant-omega", path_counts=True, key=b"k" * 32)
    text = json.dumps(moved)
    for hidden in ("user", "pw", "tenant", "secret-path", "token", "abc", "frag", "omega"):
        assert hidden not in text, hidden
    assert moved["path"] == destinations.PATH_CHANGED
    assert moved["old"]["fingerprint"] != moved["new"]["fingerprint"] and len(moved["old"]["fingerprint"]) == 10
    assert destinations.address("https://[::1]/x") == "https://[::1]:443"


def events(api, action=destinations.EVENT):
    with api.app.state.store.read() as c:
        return [dict(r, detail=json.loads(r["detail"])) for r in
                c.execute("SELECT * FROM events WHERE action=? ORDER BY ts, id", (action,))]


def everything(api, receipts=True):
    """Every place a value could leak to, as one string: events, cards, chat and (`receipts`) the stored answers to the
    editor's own requests, which show the editor the full address they saved, as any read of it does."""
    with api.app.state.store.read() as c:
        return " ".join(str([tuple(r) for r in c.execute(f"SELECT * FROM {t}")])
                        for t in ("events", "assistant_actions", "messages") + (("idempotency",) if receipts else ()))


# ----------------------------------------------------------------------------------------------------- the Gateway
def gateway(api):
    with api.app.state.store.read() as c:
        return gateway_connection._row(c)


def held(reply):
    assert reply.status_code == 409, reply.text
    error = reply.json()["error"]
    assert error["code"] == "destination_change" and error["choices"] == ["keep", "clear"]
    return error


def test_same_service_gateway_edits_save_with_no_question(api, monkeypatch):
    """Tests 1, 3 and 13: a new key at the same address, a path on the same origin and connect-automatically save at
    once; the address saved before this rule is the approved one, so nothing is migrated or cleared."""
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        fake.active(OTHER, 0.1)
        save(api, fake)
        assert put(api, "providers/gateway", {"key": OTHER, "url": fake.url, "expected_revision": gateway(api)["revision"]})
        same_origin = fake.url.replace("/v1/gateway", "/v2/gateway")
        moved = put(api, "providers/gateway", {"url": same_origin, "expected_revision": gateway(api)["revision"]})
        assert moved["url"] == same_origin and moved["connected"]
        with api.app.state.store.transaction() as c:
            gateway_connection._write(c, {**gateway_connection._row(c), "auto": {"off": True}})
        post(api, "providers/gateway/automatic", {})
        assert gateway(api).get("key") and events(api) == []


@pytest.mark.parametrize("change", ["host", "port", "scheme", "default"])
def test_a_new_gateway_service_is_held_and_names_both_destinations_never_the_key(api, monkeypatch, change):
    """Test 4 (API reason) and 14: another host, port, http to https or back to Tico's own address is held with the old
    and new scheme, host and port and the key's label; a cancel (not answering) changes nothing and writes no event."""
    setup(api, monkeypatch)
    with fake_gateway() as fake, fake_gateway() as elsewhere:
        fake.active(KEY, 0.1)
        save(api, fake)
        port = fake.server.server_port
        url = {"host": fake.url.replace("127.0.0.1", "localhost"), "port": elsewhere.url,
               "scheme": fake.url.replace("http://", "https://"), "default": ""}[change]
        before = gateway(api)
        reply = api.put("/api/v2/providers/gateway", json={"url": url, "expected_revision": before["revision"]},
                        headers=headers("ana-test"))
        error = held(reply)
        assert error["old"] == {"address": f"http://127.0.0.1:{port}", "scheme": "http", "host": "127.0.0.1", "port": port}
        assert error["new"]["address"] == {"host": f"http://localhost:{port}",
                                           "port": f"http://127.0.0.1:{elsewhere.server.server_port}",
                                           "scheme": f"https://127.0.0.1:{port}",
                                           "default": "https://updates.tico.team:443"}[change]
        assert error["credentials"] == ["Gateway key"] and error["revision"] == before["revision"]
        assert KEY not in reply.text and before["preview"] not in reply.text and "/v1/gateway" not in reply.text
        assert gateway(api) == before and events(api) == [] and elsewhere.calls == []


def test_keep_sends_the_key_only_to_the_new_gateway_and_leaves_one_event(api, monkeypatch):
    """Test 5 and 10."""
    setup(api, monkeypatch)
    with fake_gateway() as fake, fake_gateway() as elsewhere:
        fake.active(KEY, 0.1)
        elsewhere.active(KEY, 0.2)
        save(api, fake)
        fake.calls.clear()
        preview = gateway(api)["preview"]
        shown = put(api, "providers/gateway", {"url": elsewhere.url, "expected_revision": gateway(api)["revision"],
                                               "credentials": "keep"})
        assert shown["url"] == elsewhere.url and shown["connected"] and shown["state"] == "connected"
        assert elsewhere.calls == [("/v1/gateway/key", KEY)] and fake.calls == []
        [event] = events(api)
        assert event["actor"] == "human:ana" and event["target"] == "tico" and event["ts"]
        assert event["detail"]["integration"] == "gateway" and event["detail"]["option"] == "keep"
        assert event["detail"]["kept"] == ["Gateway key"] and event["detail"]["cleared"] == []
        assert event["detail"]["old"]["address"] == f"http://127.0.0.1:{fake.server.server_port}"
        assert event["detail"]["new"]["address"] == f"http://127.0.0.1:{elsewhere.server.server_port}"
        # The Gateway's own saved event names the destination by scheme, host and port too, never its path.
        saved = events(api, "gateway.connection.saved")
        assert all("url" not in e["detail"] and e["detail"]["destination"]["address"] for e in saved)
        text = everything(api)
        assert KEY not in text and preview not in text and "/v1/gateway" not in text


def test_clear_moves_the_gateway_and_drops_its_key_in_one_write_with_no_check(api, monkeypatch):
    """Test 6: the new address and no key; Tico models off; connecting automatically off; nothing sent."""
    setup(api, monkeypatch)
    with fake_gateway() as fake, fake_gateway() as elsewhere:
        fake.active(KEY, 0.1)
        save(api, fake)
        assert "tico" in get(api, "providers")["enabled"]
        before = gateway(api)
        shown = put(api, "providers/gateway", {"url": elsewhere.url, "expected_revision": before["revision"],
                                               "credentials": "clear"})
        after = gateway(api)
        assert after["url"] == elsewhere.url and after["revision"] == before["revision"] + 1
        assert not shown["connected"] and "key" not in after and "preview" not in after
        assert after["auto"]["off"] is True and "tico" not in get(api, "providers")["enabled"]
        assert elsewhere.calls == []
        [event] = events(api)
        assert event["detail"]["option"] == "clear" and event["detail"]["cleared"] == ["Gateway key"]


def test_a_new_key_typed_with_the_new_address_replaces_the_saved_one_and_is_recorded(api, monkeypatch):
    setup(api, monkeypatch)
    with fake_gateway() as fake, fake_gateway() as elsewhere:
        fake.active(KEY, 0.1)
        elsewhere.active(OTHER, 0.1)
        save(api, fake)
        put(api, "providers/gateway", {"key": OTHER, "url": elsewhere.url, "expected_revision": gateway(api)["revision"]})
        assert elsewhere.calls == [("/v1/gateway/key", OTHER)]
        [event] = events(api)
        assert event["detail"]["option"] == destinations.REPLACED


def test_only_the_owner_moves_the_gateway(api, monkeypatch):
    """Test 7: refused as today, before any question."""
    setup(api, monkeypatch)
    with fake_gateway() as fake, fake_gateway() as elsewhere:
        fake.active(KEY, 0.1)
        save(api, fake)
        for token in ("ben-test", "cara-test"):
            for choice in ("", "keep", "clear"):
                put(api, "providers/gateway", {"url": elsewhere.url, "expected_revision": gateway(api)["revision"],
                                               "credentials": choice}, token=token, expected=403)
        assert gateway(api)["url"] == fake.url and events(api) == []


# --------------------------------------------------------------------------------------------------------- webhooks
def hook(api, token="ana-test", url=HOOK, **more):
    made = post(api, "webhooks", {"name": "Acme hook", "url": url, "events": ["custom.*"], "headers": [
        {"name": "X-Api-Key", "value": SECRET, "secret": True}, {"name": "X-Env", "value": "prod"}], **more}, token=token)
    return made["webhook"]


def shown(api, hook_id, token="ana-test"):
    return get(api, f"webhooks/{hook_id}", token=token)["webhook"]


def update(api, hook_id, body, token="ana-test"):
    return api.post(f"/api/v2/webhooks/{hook_id}/update", json=body, headers=headers(token))


def test_same_destination_webhook_edits_save_with_no_question(api, net):
    """Test 2: name, events, filter, rate, pause, plain headers, a newly typed secret and a new signing secret."""
    made = hook(api)
    for body in ({"name": "Renamed"}, {"events": ["custom.*", "task.*"]}, {"filter": {"bots": []}}, {"rate_per_hour": 50},
                 {"headers": [{"name": "x-api-key", "secret": True}, {"name": "X-Env", "value": "staging"}]},
                 {"headers": [{"name": "x-api-key", "secret": True, "value": "a-new-typed-value"}]}):
        r = update(api, made["id"], {**body, "expected_revision": shown(api, made["id"])["revision"]})
        assert r.status_code == 200, (body, r.text)
    post(api, f"webhooks/{made['id']}/pause", {})
    post(api, f"webhooks/{made['id']}/resume", {})
    post(api, f"webhooks/{made['id']}/rotate", {})
    assert events(api) == []


@pytest.mark.parametrize("url", ["https://hooks.acme.example/tico/team-b?route=alpha",      # path
                                 "https://hooks.acme.example/tico/team-a?route=beta",       # query
                                 "https://other.acme.example/tico/team-a?route=alpha",      # host
                                 "https://hooks.acme.example:8443/tico/team-a?route=alpha"])  # port
def test_a_webhook_with_a_secret_header_holds_any_new_destination_and_cancel_changes_nothing(api, net, url):
    """Tests 3, 4 and 14."""
    made = hook(api)
    before = shown(api, made["id"])
    reply = update(api, made["id"], {"url": url, "expected_revision": before["revision"]})
    error = held(reply)
    assert error["credentials"] == ["x-api-key"] and error["name"] == "Acme hook"
    assert error["old"]["address"] == "https://hooks.acme.example:443"
    assert error["path"] in (destinations.SAME_PATH, destinations.PATH_CHANGED)
    for hidden in (SECRET, "team-a", "team-b", "route", "alpha", "beta"):
        assert hidden not in reply.text, hidden
    assert shown(api, made["id"]) == before and events(api) == []


def test_a_webhook_without_secret_headers_moves_with_no_question(api, net):
    made = post(api, "webhooks", {"name": "Plain", "url": HOOK, "events": ["custom.*"]})["webhook"]
    r = update(api, made["id"], {"url": "https://other.acme.example/x", "expected_revision": made["revision"]})
    assert r.status_code == 200, r.text


def test_a_webhook_address_change_needs_the_revision_it_was_read_at(api, net):
    """Test 11."""
    made = hook(api)
    r = update(api, made["id"], {"url": "https://other.acme.example/x", "credentials": "keep"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "expected_revision", r.text
    assert update(api, made["id"], {"url": "https://other.acme.example/x", "credentials": "keep",
                                    "expected_revision": made["revision"] + 5}).status_code == 409
    assert shown(api, made["id"])["url"] == HOOK


def test_keep_sends_the_saved_header_only_to_the_new_webhook_address(api, net):
    """Tests 5 and 10."""
    made = hook(api)
    new = "https://other.acme.example/receiver/zz-tenant?sig=qq"
    r = update(api, made["id"], {"url": new, "expected_revision": made["revision"], "credentials": "keep"})
    assert r.status_code == 200, r.text
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.ping"})
    assert run(api, net) == 1
    [sent] = net.seen
    assert sent.headers["host"] == "other.acme.example" and sent.url.raw_path.decode() == "/receiver/zz-tenant?sig=qq"
    assert sent.headers["x-api-key"] == SECRET
    [event] = events(api)
    assert event["target"] == made["id"] and event["detail"]["option"] == "keep"
    assert event["detail"]["kept"] == ["x-api-key"] and event["detail"]["path"] == destinations.PATH_CHANGED
    assert SECRET not in everything(api)
    text = everything(api, receipts=False)
    for hidden in ("zz-tenant", "receiver", "sig=qq", "team-a", "route=alpha"):
        assert hidden not in text, hidden


def test_clear_removes_secret_values_keeps_their_names_and_pauses_until_they_are_typed_again(api, net):
    """Test 6: nothing is sent until the value is typed again; then the endpoint sends with it."""
    made = hook(api)
    new = "https://other.acme.example/tico"
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.ping"})        # queued before the change
    r = update(api, made["id"], {"url": new, "expected_revision": made["revision"], "credentials": "clear"})
    assert r.status_code == 200, r.text
    after = shown(api, made["id"])
    assert after["url"] == new and after["enabled"] is False and after["paused_reason"] == W.NEEDS_VALUE
    assert {"name": "x-api-key", "secret": True, "set": False, "needs_value": True} in after["headers"]
    assert {"name": "x-env", "secret": False, "value": "prod"} in after["headers"]
    post(api, f"webhooks/{made['id']}/resume", {}, expected=409)
    assert run(api, net) == 0 and net.seen == []
    # Kept as needing a value, it stays paused; typed again, the endpoint is back on and sends it.
    assert update(api, made["id"], {"headers": [{"name": "x-api-key", "secret": True}, {"name": "x-env", "value": "prod"}],
                                    "expected_revision": after["revision"]}).status_code == 200
    assert shown(api, made["id"])["enabled"] is False
    assert update(api, made["id"], {"headers": [{"name": "x-api-key", "secret": True, "value": "typed-for-new"},
                                                {"name": "x-env", "value": "prod"}],
                                    "expected_revision": shown(api, made["id"])["revision"]}).status_code == 200
    assert shown(api, made["id"])["enabled"] is True
    assert run(api, net) == 1 and net.seen[0].headers["x-api-key"] == "typed-for-new"
    assert net.seen[0].headers["host"] == "other.acme.example"
    [event] = events(api)
    assert event["detail"]["option"] == "clear" and event["detail"]["cleared"] == ["x-api-key"]
    assert SECRET not in everything(api)


def test_a_clear_on_an_endpoint_paused_on_purpose_leaves_it_paused_once_the_values_are_typed_again(api, net):
    made = hook(api)
    paused = post(api, f"webhooks/{made['id']}/pause", {"reason": "Maintenance"})["webhook"]
    assert update(api, made["id"], {"url": "https://other.acme.example/tico", "expected_revision": paused["revision"],
                                    "credentials": "clear"}).status_code == 200
    after = shown(api, made["id"])
    assert after["enabled"] is False and after["paused_reason"] == "Maintenance"
    assert update(api, made["id"], {"headers": [{"name": "x-api-key", "secret": True, "value": "typed-for-new"},
                                                {"name": "x-env", "value": "prod"}],
                                    "expected_revision": after["revision"]}).status_code == 200
    again = shown(api, made["id"])
    assert again["enabled"] is False and again["paused_reason"] == "Maintenance"


def test_a_send_claimed_before_a_clear_is_never_sent_without_its_header(api, net):
    """Test 12, delivery worker: the send reads the address and headers from the stored row when it posts."""
    made = hook(api)
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.ping"})
    store, vault = api.app.state.store, api.app.state.vault
    claimed = D.claim(store, H.now())
    assert claimed
    assert update(api, made["id"], {"url": "https://other.acme.example/x", "expected_revision": made["revision"],
                                    "credentials": "clear"}).status_code == 200
    D.deliver(store, vault, claimed, transport=net.transport)
    assert net.seen == []
    with store.read() as c:
        assert c.execute("SELECT state FROM webhook_sends WHERE id=?", (claimed,)).fetchone()["state"] == "ready"


def test_only_the_creator_an_admin_or_the_owner_moves_a_webhook(api, net):
    """Test 7: Cara (a member) can't move Ben's endpoint, with or without an answer; Ben and Ana can."""
    made = hook(api, token="ben-test")
    for choice in (None, "keep", "clear"):
        r = update(api, made["id"], {"url": "https://other.acme.example/x", "expected_revision": made["revision"],
                                     **({"credentials": choice} if choice else {})}, token="cara-test")
        assert r.status_code in (403, 404), r.text
    held(update(api, made["id"], {"url": "https://other.acme.example/x", "expected_revision": made["revision"]},
                token="ana-test"))
    assert events(api) == []


# --------------------------------------------------------------------------------------- BotOps acting for a person
def test_botops_moving_a_webhook_for_a_person_waits_for_their_tap(api, net, botops):
    """Test 8: unanswered it is refused with what the person must decide; answered it is a Confirm card; nothing
    changes until the person taps; the tap runs as them and leaves the one event."""
    made = hook(api)
    ana = turn(api, botops, person="ana-test", text="Point my webhook at the new receiver")
    new = "https://other.acme.example/tico"
    path = f"webhooks/{made['id']}/update"
    unsaid = act(api, ana, "POST", path, {"url": new, "expected_revision": made["revision"]})
    held(unsaid)
    norev = act(api, ana, "POST", path, {"url": new, "credentials": "keep"})
    assert norev.status_code == 422 and norev.json()["error"]["code"] == "expected_revision", norev.text
    card = act(api, ana, "POST", path, {"url": new, "expected_revision": made["revision"], "credentials": "keep"})
    assert card.status_code == 200 and card.json()["needs_confirm"], card.text
    card = card.json()
    diff = card["action"]["diff"]
    assert {"field": "destination", "old": "https://hooks.acme.example:443", "new": "https://other.acme.example:443"} in diff
    assert {"field": "credentials", "new": "keep: x-api-key"} in diff
    assert "team-a" not in json.dumps(diff) + card["action"]["description"]
    assert shown(api, made["id"])["url"] == HOOK and events(api) == []
    # BotOps can't tap it for her.
    assert act(api, ana, "POST", f"assistant/actions/{card['action']['id']}/confirm", {}).status_code == 403
    assert confirm(api, card, "ana-test")["action"]["status"] == "done"
    assert shown(api, made["id"])["url"] == new
    [event] = events(api)
    assert event["actor"] == "human:ana" and event["detail"]["option"] == "keep"
    assert event["detail"]["confirmed_by"] == "human:ana" and event["detail"]["via"] == "botops"
    # A same-destination edit for her is not carded (cards stay as they are).
    same = act(api, ana, "POST", path, {"name": "Renamed", "expected_revision": shown(api, made["id"])["revision"]})
    assert same.status_code == 200 and "needs_confirm" not in same.json(), same.text
    finish(api, botops, ana)


def test_botops_never_resends_a_secret_header_with_a_move(api, net, botops):
    """A secret listed without a value would keep its saved one; a request for a person can't name one at all."""
    made = hook(api)
    ana = turn(api, botops, person="ana-test", text="Point my webhook at the new receiver and keep its headers")
    body = {"url": "https://other.acme.example/tico", "expected_revision": made["revision"], "credentials": "keep",
            "headers": [{"name": "x-api-key", "secret": True}, {"name": "x-env", "value": "prod"}]}
    refused = act(api, ana, "POST", f"webhooks/{made['id']}/update", body)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "secret_in_request", refused.text
    assert shown(api, made["id"])["url"] == HOOK and events(api) == []
    finish(api, botops, ana)


def test_kept_secret_headers_read_the_same_before_the_save_as_after_it():
    """The card check (`kept_by`, before saving) and the save (`carried`, after) agree on what a new address gets."""
    before = [{"name": "x-api-key", "secret": True, "value": "sealed-1"}, {"name": "x-env", "secret": False, "value": "p"},
              {"name": "x-old", "secret": True, "value": "", "needs": True}]
    assert W.kept_by(before, None) == ["x-api-key"]
    assert W.kept_by(before, [{"name": "X-Api-Key", "secret": True}, {"name": "x-env", "value": "p"}]) == ["x-api-key"]
    assert W.kept_by(before, [{"name": "x-api-key", "secret": True, "value": "typed"}]) == []
    assert W.kept_by(before, [{"name": "x-old", "secret": True}, {"name": "x-env", "value": "p"}]) == []


def as_ana(via, confirmed=False):
    from dataclasses import replace
    from backend.auth import Identity
    return replace(Identity("human:ana", "owner", email="ana@acme.example"), via=via, confirmed=confirmed)


def test_a_webhook_move_for_a_person_without_their_tap_is_refused_by_the_save_itself(api, net):
    """However the request reached the save, a delegated move runs only from the person's tap."""
    made = hook(api)
    store, vault = api.app.state.store, api.app.state.vault
    def move(who, option):
        with store.transaction() as c:
            W.update(c, who, made["id"], W.WebhookUpdate(url="https://other.acme.example/tico", credentials=option,
                                                         expected_revision=made["revision"]), vault)
    for via in ("botops", "assistant"):
        for option in ("keep", "clear"):
            with pytest.raises(Exception) as refused:
                move(as_ana(via), option)
            assert getattr(refused.value, "code", "") == "confirm_required", refused.value
    assert shown(api, made["id"])["url"] == HOOK and events(api) == []
    move(as_ana("botops", confirmed=True), "keep")
    assert shown(api, made["id"])["url"] == "https://other.acme.example/tico" and len(events(api)) == 1


def test_a_gateway_move_for_a_person_without_their_tap_is_refused_by_the_save_itself(api, monkeypatch):
    setup(api, monkeypatch)
    monkeypatch.setattr(gateway_connection, "check_later", lambda *args, **kwargs: None)
    store, vault = api.app.state.store, api.app.state.vault
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)
        before = gateway(api)
        for option in ("keep", "clear"):
            with pytest.raises(Exception) as refused, store.transaction() as c:
                gateway_connection.save(c, vault, as_ana("botops"), {"url": "", "credentials": option,
                                                                    "expected_revision": before["revision"]}, H.now())
            assert getattr(refused.value, "code", "") == "confirm_required", refused.value
        assert gateway(api)["url"] == before["url"] and gateway(api).get("key") and events(api) == []


def test_botops_clearing_for_a_person_also_waits_for_their_tap(api, net, botops):
    made = hook(api)
    ana = turn(api, botops, person="ana-test", text="Move my webhook and drop its key")
    card = act(api, ana, "POST", f"webhooks/{made['id']}/update",
               {"url": "https://other.acme.example/tico", "expected_revision": made["revision"], "credentials": "clear",
                "name": "Moved"})
    assert card.status_code == 200 and card.json()["needs_confirm"], card.text
    # What else the request changes runs at the tap, so the card names it too.
    assert {"field": "name", "new": "Moved"} in card.json()["action"]["diff"]
    assert "name" in card.json()["action"]["description"]
    assert shown(api, made["id"])["enabled"] is True and shown(api, made["id"])["url"] == HOOK
    assert confirm(api, card.json(), "ana-test")["action"]["status"] == "done"
    assert shown(api, made["id"])["enabled"] is False and shown(api, made["id"])["name"] == "Moved"
    finish(api, botops, ana)


def test_botops_resetting_the_gateway_to_its_default_address_waits_for_the_owners_tap(api, monkeypatch, botops):
    """Test 8, Gateway: an empty URL (Tico's own address) is a destination change too, so it is carded."""
    setup(api, monkeypatch)
    monkeypatch.setattr(gateway_connection, "check_later", lambda *args, **kwargs: None)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)
        put(api, "access/rules", {"botops_direct": True})
        ana = turn(api, botops, person="ana-test", text="Put the Gateway back to Tico's address")
        revision = gateway(api)["revision"]
        held(act(api, ana, "PUT", "providers/gateway", {"url": "", "expected_revision": revision}))
        card = act(api, ana, "PUT", "providers/gateway", {"url": "", "expected_revision": revision, "credentials": "clear"})
        assert card.status_code == 200 and card.json()["needs_confirm"], card.text
        assert {"field": "destination", "old": f"http://127.0.0.1:{fake.server.server_port}",
                "new": "https://updates.tico.team:443"} in card.json()["action"]["diff"]
        assert gateway(api)["url"] == fake.url and gateway(api).get("key")
        assert confirm(api, card.json(), "ana-test")["action"]["status"] == "done"
        after = gateway(api)
        assert after["url"] == "" and "key" not in after and after["auto"]["off"] is True
        finish(api, botops, ana)


@pytest.mark.parametrize("stale", ["revision", "destination", "credentials", "rights", "expired"])
def test_a_confirm_card_is_refused_at_the_tap_once_anything_it_was_bound_to_changed(api, net, botops, stale):
    """Test 9: the card is bound to the endpoint's revision (so its destination and which secrets it holds), the
    person's rights and 24 hours. Ben is an admin who may manage Ana's endpoint until he is not."""
    made = hook(api)
    ben = turn(api, botops, person="ben-test", text="Point Ana's webhook at the new receiver")
    card = act(api, ben, "POST", f"webhooks/{made['id']}/update",
               {"url": "https://other.acme.example/tico", "expected_revision": made["revision"], "credentials": "keep"})
    assert card.status_code == 200 and card.json()["needs_confirm"], card.text
    card = card.json()
    rev = made["revision"]
    if stale == "revision":
        assert update(api, made["id"], {"name": "Renamed", "expected_revision": rev}).status_code == 200
    elif stale == "destination":
        assert update(api, made["id"], {"url": "https://third.acme.example/x", "expected_revision": rev,
                                        "credentials": "keep"}).status_code == 200
    elif stale == "credentials":
        assert update(api, made["id"], {"headers": [{"name": "x-api-key", "secret": True, "value": "another"}],
                                        "expected_revision": rev}).status_code == 200
    elif stale == "rights":
        api.app.state.auth.bot_admins.discard("ben@acme.example")
    else:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE assistant_actions SET created_at=? WHERE id=?", (H.shift(H.now(), hours=-25), card["action"]["id"]))
    url_before = shown(api, made["id"])["url"]
    r = api.post(f"/api/v2/assistant/actions/{card['action']['id']}/confirm", json={}, headers=headers("ben-test"))
    status = r.json().get("action", {}).get("status") if r.status_code == 200 else None
    assert r.status_code != 200 or status == "failed", r.text
    assert shown(api, made["id"])["url"] == url_before != "https://other.acme.example/tico"
    finish(api, botops, ben)


# ------------------------------------------------------------------------------- what a send reads (test 12, race)
def interleave(monkeypatch, cipher, write):
    """Run `write` on its own connection, once, the moment a send has read the saved row and starts unsealing its key:
    a save that commits between the send's reads."""
    unseal, done = cipher.unseal, []

    def paused(c, context, sealed):
        if context == gateway_connection.CONTEXT and not done:
            done.append(True)
            write()
        return unseal(c, context, sealed)
    monkeypatch.setattr(cipher, "unseal", paused)
    return done


@pytest.mark.parametrize("edit", ["clear", "replace"])
def test_a_check_never_sends_the_key_to_an_address_saved_while_it_read(api, monkeypatch, edit):
    """The key read for address A never goes to B, which a save made while the check was reading (with Clear, or with
    a newly typed key): the check takes key and address from one read, and its answer about A's key is not kept."""
    setup(api, monkeypatch)
    monkeypatch.setattr(gateway_connection, "check_later", lambda *args, **kwargs: None)
    store, vault, settings = api.app.state.store, api.app.state.vault, api.app.state.store.settings
    with fake_gateway() as fake, fake_gateway() as elsewhere:
        fake.active(KEY, 0.1)
        elsewhere.active(KEY, 0.1)
        elsewhere.active(OTHER, 0.1)
        save(api, fake)
        fake.calls.clear()

        def move():
            with store.transaction() as w:
                gateway_connection.save(w, vault, as_ana(""), {
                    "url": elsewhere.url, "expected_revision": gateway_connection.revision(w),
                    **({"credentials": "clear"} if edit == "clear" else {"key": OTHER})}, H.now())
        done = interleave(monkeypatch, vault.cipher, move)
        gateway_connection.check_now(store, vault, settings)
        assert done and gateway(api)["url"] == elsewhere.url
        assert all(key != KEY for _, key in elsewhere.calls), elsewhere.calls
        assert fake.calls == [("/v1/gateway/key", KEY)]          # where it was kept, as read
        assert "check" not in gateway(api)                        # and its answer is not B's


def test_an_automatic_rotation_never_sends_the_key_to_an_address_saved_while_it_read(api, monkeypatch):
    """The same for automatic rotation: the key it rotates goes only to the address it was kept for."""
    import datetime
    from clients import gateway as G
    setup(api, monkeypatch)
    monkeypatch.setattr(gateway_connection, "check_later", lambda *args, **kwargs: None)
    store, vault, settings = api.app.state.store, api.app.state.vault, api.app.state.store.settings
    with fake_gateway() as fake, fake_gateway() as elsewhere:
        with store.transaction() as c:
            gateway_connection._write(c, {
                "url": fake.url, "revision": 1, "key": vault.cipher.seal(c, gateway_connection.CONTEXT, KEY),
                "preview": "tgw…042", "origin": gateway_connection.AUTOMATIC, "key_id": "gk_synthetic",
                "rotate_after": "2026-01-01T00:00:00Z", "install": "gi_" + "a" * 24,
                "install_secret": vault.cipher.seal(c, gateway_connection.INSTALL_CONTEXT, "gis_" + "b" * 43)})
        sent = []
        monkeypatch.setattr(G, "rotate", lambda base, key, opener=None: sent.append((base, key)) or (0, {}, "unreachable"))

        def move():
            with store.transaction() as w:
                gateway_connection.save(w, vault, as_ana(""), {"url": elsewhere.url, "credentials": "clear",
                                                              "expected_revision": 1}, H.now())
        done, linked = [], gateway_connection._linked

        def paused(c, settings):                          # read right after the record, before anything else
            if not done:
                done.append(True)
                move()
            return linked(c, settings)
        monkeypatch.setattr(gateway_connection, "_linked", paused)
        gateway_connection.auto_tick(store, vault, settings, now=datetime.datetime.now(datetime.timezone.utc))
        assert done and gateway(api)["url"] == elsewhere.url and "key" not in gateway(api)
        assert sent == [(fake.url, KEY)]


def test_a_card_for_a_same_service_gateway_path_shows_only_scheme_host_and_port(api, monkeypatch, botops):
    """Tests 4 and 10 on the forced card: a path on the same origin needs no keep-or-clear, but the card the saved key
    still forces shows the destination by scheme, host and port, never the path; the tap runs the real address."""
    setup(api, monkeypatch)
    monkeypatch.setattr(gateway_connection, "check_later", lambda *args, **kwargs: None)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)
        private = fake.url.replace("/v1/gateway", "/private-tenant-q7/v2")
        ana = turn(api, botops, person="ana-test", text="Point the Gateway at our tenant path")
        card = act(api, ana, "PUT", "providers/gateway", {"url": private, "expected_revision": gateway(api)["revision"]})
        assert card.status_code == 200 and card.json()["needs_confirm"], card.text
        action, address = card.json()["action"], f"http://127.0.0.1:{fake.server.server_port}"
        assert {"field": "destination", "old": address, "new": address} in action["diff"]
        assert {"field": "path", "new": destinations.PATH_CHANGED} in action["diff"]
        with api.app.state.store.read() as c:
            row = c.execute("SELECT description, diff FROM assistant_actions WHERE id=?", (action["id"],)).fetchone()
        for text in (json.dumps(action["diff"]), action["description"], row["description"], row["diff"],
                     everything(api, receipts=False).replace(json.dumps(private)[1:-1], "")):
            assert "private-tenant" not in text and "/v1/gateway" not in text, text
        assert gateway(api)["url"] == fake.url
        assert confirm(api, card.json(), "ana-test")["action"]["status"] == "done"
        assert gateway(api)["url"] == private and gateway(api).get("key")
        finish(api, botops, ana)
