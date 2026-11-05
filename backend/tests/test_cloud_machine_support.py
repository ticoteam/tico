"""HQ owns the cloud-machine helper switch; core checks the caller and never keeps a local choice."""

import json
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import Event, local

import httpx
import pytest

from backend import assistant, cloud_access, cloud_machines as CM, team_rules
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, headers, setup_attempt
from backend.tests.test_openapi_v2 import conforms
from backend.tests.test_assistant import assistant_turn
from backend.tests.test_mcp import call as mcp
from backend.tests.test_member_bots import botops, finish, turn
from backend.tests.test_settings_assistant import mcp as assistant_mcp

PATH = "/api/v2/cloud-machines/support"
KEY = "tgw-synthetic-fixture-key-0042"
URL = "https://hq.acme.example/v1/gateway"
UNAVAILABLE = {"available": False, "on": None, "machines": []}


class SupportHQ:
    def __init__(self):
        self.on = True
        self.calls = []
        self.keys = []
        self.revision = 0
        self.receipts = {}
        self.delayed = []
        self.error = None
        self.machines = [
            {"id": "cm-shared", "key": "_shared", "helper": "on", "seen": "2026-11-05T12:00:00Z"},
            {"id": "cm-builder", "key": "builder", "helper": "offline", "seen": None},
            {"id": "cm-ops", "key": "ops", "helper": "none", "seen": None},
        ]

    def __call__(self, request):
        assert str(request.url) == URL + "/machines/support"
        assert request.headers["authorization"] == "Bearer " + KEY
        assert request.headers["user-agent"].startswith("Tico-Client/")
        body = json.loads(request.content) if request.method == "PUT" else None
        self.calls.append((request.method, body))
        if body is not None:
            assert type(body["on"]) is bool and set(body) == {"on", "revision"}
            assert type(body["revision"]) is int and 0 < body["revision"] < 2**63
            self.keys.append(request.headers["idempotency-key"])
        if isinstance(self.error, Exception):
            self.delayed.append(request)
            raise self.error
        if self.error is not None:
            return self.error
        if body is not None:
            key = request.headers["idempotency-key"]
            # HQ replays a completed request before checking the latest company revision.
            if key in self.receipts:
                return httpx.Response(200, json=self.receipts[key])
            if body["revision"] <= self.revision:
                return httpx.Response(409, json={"error": "stale_revision", "on": self.on})
            self.revision = body["revision"]
            self.on = body["on"]
            self.machines[0]["helper"] = "on" if self.on else "off"
        # Unknown HQ fields must never cross the core API, even if a future HQ includes a credential.
        answer = {"on": self.on, "machines": [
            {**machine, "token": "synthetic-private-helper-token"} for machine in self.machines],
            "token": "synthetic-private-helper-token"}
        if body is not None:
            self.receipts[key] = answer
        return httpx.Response(200, json=answer)


@pytest.fixture
def hq(api, monkeypatch):
    cloud_access.apply(api.app.state.store, cloud_access.parse({"access": "open", "reason": None}))
    fake = SupportHQ()
    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
    monkeypatch.setenv("TICO_GATEWAY_URL", URL)
    monkeypatch.setattr(CM, "TRANSPORT", httpx.MockTransport(fake))
    revisions = iter(range(1, 100))
    monkeypatch.setattr(CM, "now_ns", lambda: next(revisions))
    return fake


def audit(api):
    with api.app.state.store.read() as c:
        return [(r["actor"], json.loads(r["detail"])) for r in c.execute(
            "SELECT actor,detail FROM events WHERE action='access.rules_updated' ORDER BY rowid")]


def test_reads_current_hq_state_and_machine_helpers_for_people(api, hq):
    response = api.get(PATH, headers=headers())
    assert response.status_code == 200, response.text
    assert response.json() == {"available": True, "on": True, "machines": hq.machines}
    hq.on = False
    response = api.get(PATH, headers=headers("cara-test"))
    assert response.json() == {"available": True, "on": False, "machines": hq.machines}
    assert hq.calls == [("GET", None), ("GET", None)]
    assert audit(api) == []
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    schema = document["paths"][PATH]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert conforms(response.json(), schema, document) is None


def test_owner_turns_it_off_and_back_on_at_hq_and_records_who(api, hq):
    for on in (False, True):
        response = api.put(PATH, json={"on": on}, headers=headers())
        assert response.status_code == 200, response.text
        assert response.json() == {"available": True, "on": on, "machines": hq.machines}
        assert hq.on is on
    assert hq.calls == [("PUT", {"on": False, "revision": 1}), ("PUT", {"on": True, "revision": 2})]
    assert audit(api) == [("human:ana", {"after": {"cloud_machine_support": False}}),
                          ("human:ana", {"after": {"cloud_machine_support": True}})]
    with api.app.state.store.read() as c:
        assert "cloud_machine_support" not in team_rules.load(c)
        stored = c.execute("SELECT value FROM registry_metadata WHERE key='rules'").fetchone()
        assert not stored or "cloud_machine_support" not in json.loads(stored[0])
    history = api.get("/api/v2/history?kind=permission.changed", headers=headers()).json()["items"]
    assert any(row["result"] == "team rules" for row in history)


def test_replaying_enable_after_disable_does_not_enable_again(api, hq):
    enable = headers(key="enable-support")
    first = api.put(PATH, json={"on": True}, headers=enable)
    assert first.status_code == 200, first.text
    assert api.put(PATH, json={"on": False}, headers=headers()).status_code == 200
    replay = api.put(PATH, json={"on": True}, headers=enable)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert api.get(PATH, headers=headers()).json()["on"] is False
    conflict = api.put(PATH, json={"on": False}, headers=enable)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"
    assert hq.calls == [("PUT", {"on": True, "revision": 1}), ("PUT", {"on": False, "revision": 2}), ("GET", None)]
    assert len(audit(api)) == 2


def test_key_is_required_before_contacting_hq(api, hq):
    for key in (None, "x" * 201):
        sent = {"Authorization": "Bearer ana-test"}
        if key:
            sent["Idempotency-Key"] = key
        response = api.put(PATH, json={"on": False}, headers=sent)
        assert response.status_code == 422 and response.json()["error"]["code"] == "idempotency_key"
    assert hq.calls == [] and audit(api) == []


def test_a_delayed_enable_cannot_overtake_a_confirmed_disable(api, hq):
    enable = headers(key="lost-enable")
    hq.error = httpx.ReadTimeout("synthetic-private-HQ-details")
    assert api.put(PATH, json={"on": True}, headers=enable).status_code == 502
    hq.error = None
    assert api.put(PATH, json={"on": False}, headers=headers()).status_code == 200
    late = hq(hq.delayed[0])
    assert late.status_code == 409 and late.json() == {"error": "stale_revision", "on": False}
    replay = api.put(PATH, json={"on": True}, headers=enable)
    assert replay.status_code == 409 and replay.json()["error"]["code"] == "stale_revision"
    assert replay.json()["error"]["on"] is False and replay.json()["error"]["retryable"] is False
    calls = len(hq.calls)
    assert api.put(PATH, json={"on": True}, headers=enable).json() == replay.json()
    assert len(hq.calls) == calls, "a stale refusal is final, not a pending write"
    assert hq.on is False
    assert hq.calls == [("PUT", {"on": True, "revision": 1}), ("PUT", {"on": False, "revision": 2}),
                        ("PUT", {"on": True, "revision": 1}), ("PUT", {"on": True, "revision": 1})]
    assert len(audit(api)) == 1


def test_an_uncertain_write_reuses_its_saved_revision(api, hq):
    enable = headers(key="retry-enable")
    hq.error = httpx.ReadTimeout("synthetic-private-HQ-details")
    assert api.put(PATH, json={"on": True}, headers=enable).status_code == 502
    with api.app.state.store.read() as c:
        receipt = c.execute("SELECT response FROM idempotency WHERE actor=? AND operation=? AND key=?",
                            ("human:ana", PATH, "retry-enable")).fetchone()
        assert json.loads(receipt[0]) == {"pending": True, "revision": 1}
    hq.error = None
    retried = api.put(PATH, json={"on": True}, headers=enable)
    assert retried.status_code == 200 and retried.json()["on"] is True
    assert hq.calls == [("PUT", {"on": True, "revision": 1}), ("PUT", {"on": True, "revision": 1})]
    assert hq.keys == ["retry-enable", "retry-enable"]
    assert len(audit(api)) == 1


def test_hq_replays_an_applied_write_with_a_lost_answer_before_checking_its_revision(api, hq):
    enable = headers(key="applied-enable")
    hq.error = httpx.ReadTimeout("synthetic-private-HQ-details")
    assert api.put(PATH, json={"on": True}, headers=enable).status_code == 502
    hq.error = None
    applied = hq(hq.delayed[0])
    assert applied.status_code == 200 and applied.json()["on"] is True
    assert api.put(PATH, json={"on": False}, headers=headers()).status_code == 200
    replay = api.put(PATH, json={"on": True}, headers=enable)
    assert replay.status_code == 200 and replay.json()["on"] is True
    assert hq.on is False and hq.revision == 2
    assert hq.calls[-1] == ("PUT", {"on": True, "revision": 1})


def test_new_choices_keep_growing_revisions_when_the_clock_moves_back(api, hq, monkeypatch):
    revisions = iter([10, 9, 10])
    monkeypatch.setattr(CM, "now_ns", lambda: next(revisions))
    for on in (False, True, False):
        response = api.put(PATH, json={"on": on}, headers=headers())
        assert response.status_code == 200, response.text
    assert [body["revision"] for _, body in hq.calls] == [10, 11, 12]


@pytest.mark.parametrize("on", [False, True])
def test_stale_revision_returns_only_hqs_current_boolean_and_is_final(api, hq, on):
    hq.error = httpx.Response(409, json={"error": "stale_revision", "on": on,
                                        "token": "synthetic-private-helper-token"})
    sent = headers(key="stale-choice")
    response = api.put(PATH, json={"on": not on}, headers=sent)
    assert response.status_code == 409
    assert response.json()["error"] == {"code": "stale_revision", "on": on, "retryable": False,
                                         "detail": "A newer change already reached Tico HQ."}
    hq.error = None
    assert api.put(PATH, json={"on": not on}, headers=sent).json() == response.json()
    conflict = api.put(PATH, json={"on": on}, headers=sent)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"
    assert len(hq.calls) == 1 and audit(api) == []


def test_a_legacy_pending_receipt_cannot_be_retried_without_a_revision(api, hq):
    sent = headers(key="legacy-choice")
    api.app.state.store.mutate(Identity("human:ana", "owner"), PATH, "legacy-choice", {"on": True},
                               lambda c: {"pending": True})
    response = api.put(PATH, json={"on": True}, headers=sent)
    assert response.status_code == 409 and response.json()["error"]["code"] == "hq_unconfirmed"
    assert hq.calls == [] and audit(api) == []


def test_a_pending_enable_cannot_bypass_a_new_cloud_lock(api, hq):
    sent = headers(key="pending-enable")
    hq.error = httpx.ReadTimeout("synthetic-private-HQ-details")
    assert api.put(PATH, json={"on": True}, headers=sent).status_code == 502
    cloud_access.apply(api.app.state.store, cloud_access.parse({"access": "locked", "reason": "unpaid"}))
    hq.error = None
    response = api.put(PATH, json={"on": True}, headers=sent)
    assert response.status_code == 402 and response.json()["error"]["code"] == "locked"
    assert len(hq.calls) == 1 and audit(api) == []


def test_hq_is_called_without_an_open_write_transaction(api, hq, monkeypatch):
    store = api.app.state.store
    transaction = store.transaction
    active = local()

    @contextmanager
    def tracked(*args, **kwargs):
        with transaction(*args, **kwargs) as c:
            active.open = True
            try:
                yield c
            finally:
                active.open = False

    def receive(request):
        assert not getattr(active, "open", False), "HQ must not hold the database writer"
        return hq(request)

    monkeypatch.setattr(store, "transaction", tracked)
    monkeypatch.setattr(CM, "TRANSPORT", httpx.MockTransport(receive))
    assert api.put(PATH, json={"on": True}, headers=headers()).status_code == 200
    assert hq.calls == [("PUT", {"on": True, "revision": 1})]


@pytest.mark.parametrize("second_on", [False, True])
def test_overlapping_writes_are_ordered_and_retries_are_not_resent(api, hq, monkeypatch, second_on):
    service = api.app.state.cloud_machines
    lock = service.support_lock
    entered, release, waiting = Event(), Event(), Event()
    support = CM.HQ.support
    hq.on = False

    class ObservedLock:
        def __enter__(self):
            if lock.locked():
                waiting.set()
            return lock.__enter__()

        def __exit__(self, *exc):
            return lock.__exit__(*exc)

    def delayed(self, on=None, revision=None, idempotency_key=None):
        if on is True:
            entered.set()          # The older enable has not reached HQ yet.
            assert release.wait(10)
        return support(self, on, revision=revision, idempotency_key=idempotency_key)

    monkeypatch.setattr(service, "support_lock", ObservedLock())
    monkeypatch.setattr(CM.HQ, "support", delayed)
    first_key = headers(key="overlapping-enable")
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(api.put, PATH, json={"on": True}, headers=first_key)
        try:
            assert entered.wait(5)
            second = pool.submit(api.put, PATH, json={"on": second_on},
                                 headers=first_key if second_on else headers(key="newer-disable"))
            assert waiting.wait(5), "the newer write must wait for the older write"
            assert not second.done()
            read = pool.submit(api.get, PATH, headers=headers()).result(timeout=5)
            assert read.status_code == 200 and read.json()["on"] is False
            # Unrelated writes also keep working while HQ is delayed.
            rule = pool.submit(api.put, "/api/v2/access/rules", json={"admin_sql": False},
                               headers=headers()).result(timeout=5)
            assert rule.status_code == 200, rule.text
        finally:
            release.set()
        enabled, changed = first.result(timeout=5), second.result(timeout=5)
    assert enabled.status_code == changed.status_code == 200
    if second_on:
        assert changed.json() == enabled.json()
        assert hq.calls == [("GET", None), ("PUT", {"on": True, "revision": 1})]
    else:
        assert changed.json()["on"] is False and hq.on is False
        assert hq.calls == [("GET", None), ("PUT", {"on": True, "revision": 1}), ("PUT", {"on": False, "revision": 2})]


def test_unsigned_admin_member_bot_and_runner_cannot_change_access(api, hq):
    assert api.get(PATH).status_code == 401
    assert api.put(PATH, json={"on": False}).status_code == 401
    for token in ("ben-test", "cara-test"):
        assert api.put(PATH, json={"on": False}, headers=headers(token)).status_code == 403
    computer, _, attempt = setup_attempt(api)
    for token in (attempt["token"], computer["token"]):
        assert api.get(PATH, headers=headers(token)).status_code == 403
        assert api.put(PATH, json={"on": False}, headers=headers(token)).status_code == 403
    assert hq.calls == [] and audit(api) == []


def test_full_app_support_cannot_enable_its_own_machine_access(api, hq, monkeypatch):
    monkeypatch.setenv("TICO_SUPPORT_ACCESS", "on")
    sid = "sa_" + "1" * 24
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO support_access(id,ticket_id,tier,started_by,started_at,expires_at) "
                  "VALUES(?,?,?,?,?,?)", (sid, "ticket-test", "full", "human:ana", H.now(), H.shift(H.now(), hours=1)))
    api.app.state.store.settings.test_identities["staff-test"] = Identity(
        "human:tico-support", "owner", support="full", token_id=sid)
    assert api.put(PATH, json={"on": True}, headers=headers("staff-test")).status_code == 403
    assert hq.calls == [] and audit(api) == []


@pytest.mark.parametrize("reason", ["trial_over", "unpaid"])
def test_locked_owner_can_read_and_revoke_but_cannot_enable(api, hq, reason):
    cloud_access.apply(api.app.state.store, cloud_access.parse({"access": "locked", "reason": reason}))
    assert api.get(PATH).status_code == 401
    assert api.put(PATH, json={"on": False}).status_code == 401
    for token in ("ben-test", "cara-test"):
        assert api.get(PATH, headers=headers(token)).status_code == 402
        assert api.put(PATH, json={"on": False}, headers=headers(token)).status_code == 402
    assert api.get(PATH, headers=headers()).json()["on"] is True
    off = api.put(PATH, json={"on": False}, headers=headers())
    assert off.status_code == 200 and off.json()["on"] is False
    assert api.get(PATH, headers=headers()).json()["on"] is False
    refused = api.put(PATH, json={"on": True}, headers=headers())
    assert refused.status_code == 402 and refused.json()["error"]["code"] == "locked"
    assert api.get("/api/v2/cloud-machines", headers=headers()).status_code == 402
    assert hq.on is False
    assert hq.calls == [("GET", None), ("PUT", {"on": False, "revision": 1}), ("GET", None)]
    assert audit(api) == [("human:ana", {"after": {"cloud_machine_support": False}})]


def test_owner_assistant_needs_the_same_confirmation_as_the_support_access_rule(api, hq):
    _, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    # This is a real leased Assistant credential, which authenticates as the owner without support scope.
    who = api.app.state.auth.authenticate({name.lower(): value for name, value in headers(token).items()}, PATH, "PUT")
    assert who.role == "owner" and who.via == "assistant" and not who.support
    for on in (False, True):
        for path, body in (("/api/v2/access/rules", {"support_access": on}), (PATH, {"on": on})):
            assert not assistant.admits_on_turn("PUT", path, body)
            response = api.put(path, json=body, headers=headers(token))
            assert response.status_code == 403, response.text
            assert response.json()["error"]["code"] == "confirm_required"
    assert hq.calls == [] and audit(api) == []


def test_cloud_support_catalogue_is_owner_only_and_reads_hq_through_mcp(api, hq):
    key = "team.rules.cloud_machine_support"
    err, found = mcp(api, "hub_settings_find", {"words": "cloud support"})
    assert not err, found
    entry = next(e for e in found["result"] if e["key"] == key)
    assert entry["who"] == "owner" and entry["class"] == "confirm"
    assert entry["method"] == "PUT" and entry["route"] == PATH and entry["body"] == {"on": "<bool>"}
    shown = api.get("/api/v2/settings/catalog/" + key, headers=headers()).json()
    assert shown["read"] == {"path": "cloud-machines/support", "pointer": "on"}
    err, shown = mcp(api, "hub_settings_show", {"key": key})
    assert not err and shown["value"] is True and shown["you_may"] is True, shown
    for token in ("ben-test", "cara-test"):
        err, shown = mcp(api, "hub_settings_show", {"key": key}, token)
        assert not err and shown["you_may"] is False, shown
        err, refused = mcp(api, "hub_settings_set", {"key": key, "value": "off"}, token)
        assert err and refused["error"] == "forbidden", refused
    assert hq.on is True and audit(api) == []
    err, changed = mcp(api, "hub_settings_set", {"key": key, "value": "off"})
    assert not err and changed["value"] is False, changed
    assert hq.on is False
    assert audit(api) == [("human:ana", {"after": {"cloud_machine_support": False}})]


@pytest.mark.parametrize("via", ["botops", "assistant"])
def test_cloud_support_catalogue_waits_for_the_owners_confirmation(api, hq, botops, via):
    args = {"key": "team.rules.cloud_machine_support", "value": "off"}
    if via == "botops":
        attempt = turn(api, botops, person="ana-test", text="Turn cloud machine support off")
        err, card = mcp(api, "hub_settings_set", args, attempt["token"])
    else:
        _, attempt = assistant_turn(api, "ana-test", "Turn cloud machine support off")
        err, card = assistant_mcp(api, "hub_settings_set", {**args, "on_behalf_of": "turn"}, attempt["token"])
    assert not err and card["needs_confirm"] is True, card
    action = card["action"]
    assert action["method"] == "PUT" and action["path"] == PATH and action["body"] == {"on": False}
    assert hq.calls == [] and audit(api) == []
    confirm = f"/api/v2/assistant/actions/{action['id']}/confirm"
    assert api.post(confirm, json={}, headers=headers(attempt["token"])).status_code == 403
    if via == "botops":
        finish(api, botops, attempt)
    response = api.post(confirm, json={}, headers=headers())
    assert response.status_code == 200 and response.json()["action"]["status"] == "done", response.text
    assert hq.on is False and hq.calls == [("PUT", {"on": False, "revision": 1})]
    assert audit(api) == [("human:ana", {"after": {"cloud_machine_support": False}, "via": via})]


def test_only_a_boolean_is_accepted_and_no_extra_fields_are_sent(api, hq):
    for body in ({}, {"on": "false"}, {"on": 0}, {"on": None}, {"on": False, "revision": 1},
                 {"on": False, "token": "synthetic-private"}):
        assert api.put(PATH, json=body, headers=headers()).status_code == 422
    assert hq.calls == [] and audit(api) == []


def test_self_hosted_is_unavailable_without_contacting_hq(api, hq):
    cloud_access.apply(api.app.state.store, None)
    assert api.get(PATH, headers=headers()).json() == UNAVAILABLE
    response = api.put(PATH, json={"on": False}, headers=headers())
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_cloud"
    assert hq.calls == [] and audit(api) == []


@pytest.mark.parametrize("mode", ["demo", "rehearsal"])
def test_demo_and_rehearsal_never_contact_hq(api, hq, monkeypatch, mode):
    monkeypatch.setattr(api.app.state.store.settings, mode, True)
    if mode == "demo":
        api.headers["Host"] = "localhost:8765"
    assert api.get(PATH, headers=headers()).json() == UNAVAILABLE
    assert api.put(PATH, json={"on": False}, headers=headers()).status_code == 404
    assert hq.calls == [] and audit(api) == []


def test_hqs_not_cloud_hides_the_rule_even_with_old_cloud_metadata(api, hq):
    hq.error = httpx.Response(404, json={"error": "not_cloud"})
    assert api.get(PATH, headers=headers()).json() == UNAVAILABLE
    response = api.put(PATH, json={"on": False}, headers=headers())
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_cloud"
    assert audit(api) == []


@pytest.mark.parametrize("failure", ["timeout", "server_error", "refused", "missing_route", "bad_answer", "bad_machine", "bad_stale", "oversized"])
def test_hq_failure_is_clear_sends_no_private_text_and_never_saves_a_choice(api, hq, failure):
    if failure == "timeout":
        hq.error = httpx.ConnectTimeout("synthetic-private-HQ-details")
    elif failure == "server_error":
        hq.error = httpx.Response(503, text="synthetic-private-HQ-details")
    elif failure == "refused":
        hq.error = httpx.Response(403, json={"error": "synthetic-private-HQ-details"})
    elif failure == "missing_route":
        hq.error = httpx.Response(404, json={"error": "not_found"})
    elif failure == "bad_answer":
        hq.error = httpx.Response(200, json={"on": "false", "machines": []})
    elif failure == "bad_machine":
        hq.error = httpx.Response(200, json={"on": False, "machines": [{
            **hq.machines[0], "helper": "synthetic-private-HQ-details"}]})
    elif failure == "bad_stale":
        hq.error = httpx.Response(409, json={"error": "stale_revision", "on": "synthetic-private-HQ-details"})
    else:
        hq.error = httpx.Response(200, content=b"x" * (CM.ANSWER_CAP + 1))
    for method, kwargs in (("GET", {}), ("PUT", {"json": {"on": False}})):
        response = api.request(method, PATH, headers=headers(), **kwargs)
        assert response.status_code == 502, response.text
        assert response.json()["error"]["code"] == "hq_unreachable"
        assert "Didn't reach Tico HQ" in response.json()["error"]["detail"]
        assert "synthetic-private" not in response.text
    assert audit(api) == [] and hq.on is True
    hq.error = None
    hq.on = False                         # HQ's off wins; a failed page change is never replayed.
    assert api.get(PATH, headers=headers()).json()["on"] is False


def test_a_missing_gateway_key_is_a_failure_not_a_default_on(api, hq, monkeypatch):
    monkeypatch.delenv("TICO_GATEWAY_KEY")
    response = api.get(PATH, headers=headers())
    assert response.status_code == 502 and response.json()["error"]["code"] == "hq_unreachable"
    assert api.put(PATH, json={"on": False}, headers=headers()).status_code == 502
    assert hq.calls == [] and audit(api) == []
