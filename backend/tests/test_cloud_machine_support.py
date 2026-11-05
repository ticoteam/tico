"""HQ owns the cloud-machine helper switch; core checks the caller and never keeps a local choice."""

import json

import httpx
import pytest

from backend import cloud_access, cloud_machines as CM, team_rules
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, headers, setup_attempt
from backend.tests.test_openapi_v2 import conforms

PATH = "/api/v2/cloud-machines/support"
KEY = "tgw-synthetic-fixture-key-0042"
URL = "https://hq.acme.example/v1/gateway"
UNAVAILABLE = {"available": False, "on": None, "machines": []}


class SupportHQ:
    def __init__(self):
        self.on = True
        self.calls = []
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
        if isinstance(self.error, Exception):
            raise self.error
        if self.error is not None:
            return self.error
        if body is not None:
            assert type(body["on"]) is bool and set(body) == {"on"}
            self.on = body["on"]
            self.machines[0]["helper"] = "on" if self.on else "off"
        # Unknown HQ fields must never cross the core API, even if a future HQ includes a credential.
        return httpx.Response(200, json={"on": self.on, "machines": [
            {**machine, "token": "synthetic-private-helper-token"} for machine in self.machines],
            "token": "synthetic-private-helper-token"})


@pytest.fixture
def hq(api, monkeypatch):
    cloud_access.apply(api.app.state.store, cloud_access.parse({"access": "open", "reason": None}))
    fake = SupportHQ()
    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
    monkeypatch.setenv("TICO_GATEWAY_URL", URL)
    monkeypatch.setattr(CM, "TRANSPORT", httpx.MockTransport(fake))
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
    assert hq.calls == [("PUT", {"on": False}), ("PUT", {"on": True})]
    assert audit(api) == [("human:ana", {"after": {"cloud_machine_support": False}}),
                          ("human:ana", {"after": {"cloud_machine_support": True}})]
    with api.app.state.store.read() as c:
        assert "cloud_machine_support" not in team_rules.load(c)
        stored = c.execute("SELECT value FROM registry_metadata WHERE key='rules'").fetchone()
        assert not stored or "cloud_machine_support" not in json.loads(stored[0])
    history = api.get("/api/v2/history?kind=permission.changed", headers=headers()).json()["items"]
    assert any(row["result"] == "team rules" for row in history)


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


def test_only_a_boolean_is_accepted_and_no_extra_fields_are_sent(api, hq):
    for body in ({}, {"on": "false"}, {"on": 0}, {"on": None}, {"on": False, "token": "synthetic-private"}):
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


@pytest.mark.parametrize("failure", ["timeout", "server_error", "refused", "missing_route", "bad_answer", "bad_machine", "oversized"])
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
