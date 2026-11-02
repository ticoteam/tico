"""The demo's services as Ana sees them: company inboxes and a phone line with their messages, a mailing address with
letters routed to their owners, Gateway models, a company subscription on a computer, and the connected connectors."""

import hashlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import demo
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
        sms = api.get("/api/v2/inbound/messages", params={"kind": "sms"}).json()["messages"]
        assert len(sms) == 4 and {row["inbox_id"] for row in sms} == {"inbox-phone"}
        email = api.get("/api/v2/inbound/messages", params={"kind": "email"}).json()["messages"]
        assert len(email) == 7


def test_letters_carry_the_mailing_address_and_reach_their_owners(built):
    with as_ana(built) as api:
        items = {row["external_id"]: row for row in api.get("/api/v2/postal/items").json()["items"]}
        assert set(items) == {"letter-fcc-lab", "letter-state-tax", "letter-invoice"}
        for item in items.values():
            assert item["status"] == "filed"
            assert item["address"].startswith("1200 Example Way")
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
