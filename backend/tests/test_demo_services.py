"""The demo's services as Ana sees them: company inboxes and a phone line with their messages, a mailing address with
letters routed to their owners, Gateway models, a company subscription on a computer, and the connected connectors."""

import hashlib
import sqlite3
import threading
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import time_machine
from fastapi.testclient import TestClient

from clients import gateway as G

from backend import demo, demo_billing, gateway_tiers, github_app
from backend.app import create_app
from backend.demo_sections.finance import CONTENT as FINANCE

NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module", autouse=True)
def demo_clock():
    """Builds and API reads see the same day, including clocks outside hubdb.now."""
    with time_machine.travel(NOW, tick=False):
        yield


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


def test_the_demo_sends_nothing_with_its_gateway_key(built, monkeypatch):
    """The sample key is never a credential for a call out: Billing, hosted servers, a bot's pause bit and the Gateway
    routes stay in the demo. The CLI's socket guard is not in this process, so every urllib open is recorded here."""
    sent = []

    def refused(self, request, *args, **kwargs):
        sent.append(getattr(request, "full_url", request))
        raise OSError("the demo sent a request")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refused)
    with as_ana(built) as api:
        assert api.get("/api/v2/billing").json()["state"] == "active"
        assert api.get("/api/v2/billing/usage", params={"day": "2026-09-24"}).json()["state"] == "ok"
        assert api.get("/api/v2/billing/statements/2026-09.csv").status_code == 200
        assert api.get("/api/v2/hosted-servers").json()["state"] == "none"
        assert api.post("/api/v2/hosted-servers/pv-0123456789abcdef/start").status_code == 409
        assert api.get("/api/v2/bots/coo/pause-state").json()["company_paused"] is False
        assert api.post("/api/v2/gateway/search", json={"query": "projector"}).status_code == 409
    assert sent == []


def test_email_and_phone_writes_refuse_in_the_demo_before_any_call_out(built, monkeypatch):
    """The Email & Phone buttons (a new inbox, a phone request, the phone quote, the company connection) are refused by
    the product in the demo before any request leaves: no HTTP client sends. Every send is recorded here."""
    sent = []
    local_send, local_async_send = httpx.Client.send, httpx.AsyncClient.send

    def sync_send(self, request, *args, **kwargs):
        if request.url.host in ("127.0.0.1", "localhost"):      # the test's own client, which talks to the app
            return local_send(self, request, *args, **kwargs)
        sent.append(str(request.url))
        raise OSError("the demo sent a request")

    async def async_send(self, request, *args, **kwargs):
        if request.url.host in ("127.0.0.1", "localhost"):
            return await local_async_send(self, request, *args, **kwargs)
        sent.append(str(request.url))
        raise OSError("the demo sent a request")

    monkeypatch.setattr(httpx.Client, "send", sync_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_send)
    with as_ana(built) as api:
        create = api.post("/api/v2/inbound/inboxes", headers={"Idempotency-Key": "demo-services-create"},
                          json={"kind": "email", "name": "Sales two"})
        assert create.status_code == 409 and create.json()["error"]["code"] == "inbound_unconfigured"
        phone = api.post("/api/v2/inbound/phone-requests", headers={"Idempotency-Key": "demo-services-phone"},
                         json={"name": "Mobile number"})
        assert phone.status_code == 409
        assert api.get("/api/v2/inbound/phone-quote").status_code == 409
        connect = api.post("/api/v2/inbound/company-connection", json={"action": "preview", "code": "x" * 43})
        assert connect.status_code == 503
    assert sent == []


def test_billing_is_a_sample_that_agrees_with_the_usage_page(built):
    """Billing comes from the demo's own usage: each day is the usage page's day to the micro, its hours and models add
    up to it, the statement's debits are the usage, and the CSV gives the statement's lines."""
    with as_ana(built) as api:
        billing = api.get("/api/v2/billing").json()
        page = api.get("/api/v2/usage", params={"group": "day", "from": "2026-09-01", "to": "2026-09-25"}).json()
        priced = {row["day"]: row["est_cost_usd"] for row in page["rows"] if row["est_cost_usd"]}
        assert billing["usage"] and {row["period"] for row in billing["usage"]} == set(priced)
        for day in billing["usage"]:
            assert abs(day["total_micros"] - round(priced[day["period"]] * 1_000_000)) <= 1
            assert sum(line["amount_micros"] for line in day["lines"]) == day["total_micros"]
            hours = api.get("/api/v2/billing/usage", params={"day": day["period"]}).json()["hours"]
            assert sum(hour["total_micros"] for hour in hours) == day["total_micros"]
        statement = billing["statement"]
        debits = sum(day["total_micros"] for day in billing["usage"])
        assert statement["debits_micros"] == debits == statement["debits_by_product"]["gateway"]
        # The launch credit, then the Gateway top-up Finance's books record (paid on the 2nd of the month before).
        launch, topup = billing["credits"]
        assert (launch["source"], launch["amount_micros"]) == ("grant", demo_billing.GRANT)
        assert (topup["source"], topup["created"]) == ("payment", "2026-08-02")
        assert topup["amount_micros"] == demo_billing.TOPUP == FINANCE["gateway_topup"]["amount"] * 10_000
        assert billing["balance_micros"] == launch["left_micros"] + topup["left_micros"] == statement["closing_micros"]
        assert launch["used_up"] or topup["left_micros"] == topup["amount_micros"]          # the launch credit goes first
        assert statement["closing_micros"] == statement["opening_micros"] + statement["credits_micros"] - debits
        csv_lines = api.get("/api/v2/billing/statements/2026-09.csv").text.splitlines()
        assert csv_lines == ["month,line,amount_micros", f"2026-09,opening,{statement['opening_micros']}",
                             *[f"2026-09,credits:{k},{v}" for k, v in statement["credits_by_source"].items()],
                             f"2026-09,debits:gateway,{-debits}", f"2026-09,closing,{statement['closing_micros']}"]


def test_a_build_long_after_its_dates_keeps_every_message_body(tmp_path):
    """Retention runs on the demo's own clock while it is built: a demo built months before today keeps its bodies."""
    settings = demo.build(tmp_path, now=NOW - timedelta(days=200))
    with closing(sqlite3.connect(settings.db_path)) as db:
        total = db.execute("SELECT COUNT(*) FROM inbound_messages").fetchone()[0]
        removed = db.execute("SELECT COUNT(*) FROM inbound_messages WHERE body IS NULL OR retention_removed=1").fetchone()[0]
    assert total == 11 and removed == 0


def settled_files(settings):
    """Every scan's state and the rows that hold them: a build that settles its files the same way every time."""
    with closing(sqlite3.connect(settings.db_path)) as db:
        states = db.execute("SELECT media_state, COUNT(*) FROM blob_media GROUP BY media_state ORDER BY 1").fetchall()
        versions = db.execute("SELECT media_state, COUNT(*) FROM bot_file_versions GROUP BY media_state ORDER BY 1").fetchall()
        blobs = db.execute("SELECT COUNT(*) FROM blobs").fetchone()[0]
    return states, versions, blobs


def test_every_scan_is_settled_before_the_build_is_saved(built, tmp_path):
    states, versions, blobs = settled_files(built)
    assert all(state != "pending" for state, _ in states + versions)
    assert settled_files(demo.build(tmp_path, now=NOW)) == (states, versions, blobs)

