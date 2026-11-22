"""A fake Jira Cloud and two record-integration Jira sites, shared by the Jira intake and routing tests."""

import base64
import ipaddress

import httpx
import pytest

from backend import egress
from backend.item_sources import jira as JS
from backend.tests.helpers.api import headers, post
from backend.tests.helpers.credentials import setup


PUBLIC = "93.184.215.14"
TOKENS = {
    "acme.atlassian.net": "jira-synthetic-acme-0011223344",
    "beta.atlassian.net": "jira-synthetic-beta-5566778899",
}


class FakeJira:
    """Jira Cloud at the network edge: each site's issues, answered to GET /rest/api/3/issue/<key> with that site's key.
    `during` runs while a read is out (a due mark landing mid-read, a read that hangs)."""

    def __init__(self):
        self.issues: dict[tuple[str, str], dict] = {}
        self.seen: list[httpx.Request] = []
        self.during = None
        self.tools: dict[str, str] = {}

    def set(self, host, key, status, category, updated, summary="Fix the export"):
        self.issues[(host, key)] = {"status": status, "category": category, "updated": updated, "summary": summary}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.during:
            self.during()
        host = request.headers["host"]
        wanted = "Basic " + base64.b64encode(f"ops@acme.example:{TOKENS[host]}".encode()).decode()
        if request.headers.get("authorization") != wanted:
            return httpx.Response(401, json={"errorMessages": ["synthetic"]})
        key = request.url.path.rsplit("/", 1)[1]
        issue = self.issues.get((host, key))
        if issue is None:
            return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})
        status = {"name": issue["status"], "statusCategory": {"key": issue["category"], "name": issue["status"]}}
        fields = {"summary": issue["summary"], "status": status, "updated": issue["updated"]}
        return httpx.Response(200, json={"key": key, "fields": fields})


@pytest.fixture
def jira(api, monkeypatch):
    """Two Jira sites, each a record integration (an administrator marked it as holding records) with a shared read
    key, and a fake Jira behind both. Nobody is given access to either: Tico reads with the connection's key."""
    setup(api)
    vendor = FakeJira()
    monkeypatch.setattr(egress, "resolve", lambda host: [ipaddress.ip_address(PUBLIC)])
    monkeypatch.setattr(JS, "TRANSPORT", httpx.MockTransport(vendor))
    for name, host in (("Jira Acme", "acme.atlassian.net"), ("Jira Beta", "beta.atlassian.net")):
        made = post(api, "api-tools", {"name": name, "base_url": f"https://{host}", "auth_scheme": "basic_password"})
        body = {"name": "Jira read", "value": TOKENS[host], "username": "ops@acme.example", "access_level": "read"}
        post(api, f"api-tools/{made['id']}/keys", body)
        _patch_tool(api, made["id"], {"record_owner": "outside"})
        vendor.tools[host] = made["id"]
    return vendor


def _patch_tool(api, tool_id, body, token="ana-test", expected=200):
    r = api.patch(f"/api/v2/api-tools/{tool_id}", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def _create(api, ref, title="Fix the export", token="ana-test", expected=200, **more):
    body = {"owner": "ben", "title": title, "message": "From Jira.", "primary_link": ref, **more}
    return post(api, "tasks", body, token, expected=expected)
