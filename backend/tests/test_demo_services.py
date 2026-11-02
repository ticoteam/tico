"""The demo's services as Ana sees them: company inboxes and a phone line with their messages, a mailing address with
letters routed to their owners, Gateway models, a company subscription on a computer, and the connected connectors."""

import hashlib
import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from clients import gateway as G

from backend import demo, gateway_tiers, github_app
from backend.app import create_app

NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return demo.build(tmp_path_factory.mktemp("demo-services"), now=NOW)


def as_ana(settings):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    client.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    return client


def test_company_inboxes_and_phone_line_hold_their_messages(built):
    with as_ana(built) as api:
        inboxes = {row["inbox_id"]: row for row in api.get("/api/v2/inbound/inboxes").json()["inboxes"]}
        assert {"inbox-care", "inbox-sales", "inbox-billing", "inbox-phone"} <= set(inboxes)
        phone = inboxes["inbox-phone"]
        assert (phone["kind"], phone["state"]) == ("sms", "active")
        assert phone["period_end"] > NOW.timestamp()
        assert "support" in phone["used_for"]
        sms = api.get("/api/v2/inbound/messages", params={"kind": "sms"}).json()["messages"]
        assert len(sms) == 4 and {row["inbox_id"] for row in sms} == {"inbox-phone"}
        email = api.get("/api/v2/inbound/messages", params={"kind": "email"}).json()["messages"]
        assert len(email) == 7


def test_email_and_phone_page_reads_configured_in_the_demo_and_still_refuses_writes(built):
    with as_ana(built) as api:
        listed = api.get("/api/v2/inbound/inboxes").json()
        assert listed["configured"] is True and len(listed["inboxes"]) == 4
        assert listed["last_synced_at"] is not None
        assert api.get("/api/v2/inbound/messages").json()["messages"]
        refused = api.post("/api/v2/inbound/inboxes", headers={"Idempotency-Key": "demo-services-create"},
                           json={"kind": "email", "name": "Sales two"})
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "inbound_unconfigured"


def test_phone_requests_read_is_empty_in_the_demo_not_an_error(built):
    with as_ana(built) as api:
        listed = api.get("/api/v2/inbound/phone-requests")
        assert listed.status_code == 200
        assert listed.json() == {"phone_requests": [], "next": None}


def test_letters_carry_the_mailing_address_and_reach_their_owners(built):
    with as_ana(built) as api:
        items = {row["external_id"]: row for row in api.get("/api/v2/postal/items").json()["items"]}
        assert set(items) == {"letter-fcc-lab", "letter-state-tax", "letter-invoice"}
        for item in items.values():
            assert item["status"] == "filed"
            assert item["address"].startswith("1200 Example Way")
        # Each deadline is counted from the day its letter arrived (5, 3.1 and 1.4 days before NOW, due in 14, 14, 30).
        assert {name: row["deadline"] for name, row in items.items()} == {
            "letter-fcc-lab": "2026-10-04", "letter-state-tax": "2026-10-06", "letter-invoice": "2026-10-24"}
        assert "net 30" in items["letter-invoice"]["summary"]
        owners = {}
        for name, item in items.items():
            owners[name] = api.get(f"/api/v2/tasks/{item['task_id']}").json()["task"]["owner_id"]
        assert owners == {"letter-fcc-lab": "bot:compliance", "letter-state-tax": "bot:finance-lead",
                          "letter-invoice": "bot:accounts-payable"}


def test_gateway_connection_and_models_in_use_per_department(built):
    with as_ana(built) as api:
        body = api.get("/api/v2/gateway").json()
        assert body["connection"]["connected"] is True
        assert body["connection"]["state"] == "connected"
        secret = hashlib.sha256(b"gateway").hexdigest()[:32]      # the key's derived part, as the seed makes it
        assert secret not in str(body)                          # only a preview of the key leaves the server
        assert len(body["picks"]) >= 8
        assert all(pick["bots"] >= 1 for pick in body["picks"])


def test_company_subscription_is_given_to_the_macbook(built):
    with as_ana(built) as api:
        subs = api.get("/api/v2/model-subscriptions").json()["subscriptions"]
        assert [row["name"] for row in subs] == ["Acme Claude team plan"]
        assigned = api.get("/api/v2/computer-subscriptions").json()["assignments"]
        assert [row["subscription_id"] for row in assigned] == [subs[0]["id"]]
        assert assigned[0]["runtime"] == "claude"


def test_github_and_slack_show_as_connected(built):
    with as_ana(built) as api:
        rows = {row["kind"]: row for row in api.get("/api/v2/connections").json()["connections"]}
        assert rows["github"]["status"] == "on"
        assert rows["slack"]["status"] == "on"


def test_bots_declare_their_mcp_tools_as_ready(built):
    with as_ana(built) as api:
        for slug, service in (("issue-triage", "linear"), ("sales-ops", "crm"), ("support", "helpdesk")):
            tools = {tool["service"]: tool for tool in api.get(f"/api/v2/bots/{slug}/tools").json()["tools"]}
            assert tools[service]["status"] == "ready"


def test_the_demo_never_asks_the_gateway_its_catalog_or_github(built, monkeypatch):
    """The Gateway key, its catalog and the models list, and GitHub's status are read from the sample: a check, a
    catalog fetch or a GitHub JWT here would be a call out of the demo."""
    asked = []

    def refused(*args, **kwargs):
        asked.append(args[:1])
        raise AssertionError("the demo reached out")

    for name in ("check", "enroll", "rotate", "_send"):
        monkeypatch.setattr(G, name, refused)
    monkeypatch.setattr(gateway_tiers, "fetch", refused)
    monkeypatch.setattr(github_app, "_client", refused)
    monkeypatch.setattr(github_app.jwt, "encode", refused)
    gateway_tiers.reset()
    with as_ana(built) as api:
        for path in ("/api/v2/gateway", "/api/v2/gateway/catalog", "/api/v2/models", "/api/v2/providers/gateway",
                     "/api/v2/github/app"):
            assert api.get(path).status_code == 200, path
        assert api.post("/api/v2/providers/gateway/check").status_code == 200
    for thread in threading.enumerate():          # a check or catalog fetch would run on one of these threads
        if thread.name.startswith("gateway-"):
            thread.join(5)
    assert asked == []


def test_github_app_status_reads_as_connected_in_the_demo(built):
    with as_ana(built) as api:
        status = api.get("/api/v2/github/app").json()
        assert status["connected"] is True and status["org"] == "acme-lights"
        assert status["installed"] is True and status["administration"] is True


def test_a_stored_key_that_will_not_sign_reads_as_the_stored_choice(built, monkeypatch):
    """Outside the demo, a stored key that will not sign is not a 500: no live answer, and the stored choice stands."""
    service = create_app(built).state.github_app
    monkeypatch.setattr(built, "demo", False)
    service.live.clear()
    assert service.live_permissions(refresh=True) is None
    assert service.can_create_repos(refresh=True) is True
