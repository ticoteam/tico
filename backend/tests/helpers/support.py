"""Helpers that other tests share, moved out of test_support.py so they import no test file."""

import json
import re
import secrets
from pathlib import Path
import httpx
import pytest
from backend import support
from backend.tests.helpers.onboarding import signed_in


class FakeHQ:
    """Just enough of HQ's ticket routes (docs/telemetry.md, "Support tickets"): keeps tickets, answers by secret, and
    can be told to fail."""

    def __init__(self):
        self.tickets, self.seen, self.down, self.status_override = {}, [], False, None
        self.read_status = None  # answers every ticket read with this status (429: HQ asking us to slow down)
        self.fields = None  # an older HQ: refuses a ticket carrying a field beyond these
        self.refuse_field = None  # refuses every ticket, naming this field as the wrong one

    def __call__(self, request):
        self.seen.append(request)
        if self.down:
            raise httpx.ConnectTimeout("down")
        path, secret = request.url.path, request.headers.get("x-ticket-secret", "")
        if request.method == "POST" and path == "/v1/support":
            if self.status_override:
                return httpx.Response(self.status_override, json={"error": "x"})
            body = json.loads(request.content)
            if self.refuse_field:
                return httpx.Response(422, json={"error": "invalid", "field": self.refuse_field})
            if self.fields is not None and set(body) - self.fields:
                return httpx.Response(422, json={"error": "invalid", "field": "fields"})
            tid = self.new_ticket_id()
            self.tickets[tid] = {"secret": self.new_secret(), "status": "open", "input": body, "messages": []}
            return httpx.Response(201, json={"ticket_id": tid, "secret": self.tickets[tid]["secret"], "status": "open"})
        parts = path.strip("/").split("/")
        ticket = self.tickets.get(parts[2]) if len(parts) > 2 else None
        if not ticket or ticket["secret"] != secret:
            return httpx.Response(404, json={"error": "not_found"})
        if request.method == "GET" and self.read_status:
            return httpx.Response(self.read_status, json={"error": "rate_limited"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "ticket_id": parts[2],
                    "status": ticket["status"],
                    "created": "x",
                    "updated": "y",
                    "messages": ticket["messages"],
                },
            )
        if request.method == "DELETE":
            del self.tickets[parts[2]]
            return httpx.Response(200, json={"deleted": True})
        if parts[-1] == "messages":
            ticket["messages"].append(
                {
                    "id": len(ticket["messages"]) + 1,
                    "created": "z",
                    "from": "person",
                    "body": json.loads(request.content)["message"],
                }
            )
            ticket["status"] = "open"
            return httpx.Response(
                201, json={"ticket_id": parts[2], "status": "open", "message_id": len(ticket["messages"])}
            )
        return httpx.Response(404, json={})

    def new_ticket_id(self):
        return "TK-%08d" % (len(self.tickets) + 1)

    def new_secret(self):
        return "s" * 20 + str(len(self.tickets))

    def staff_replies(self, tid, body):
        messages = self.tickets[tid]["messages"]
        messages.append({"id": len(messages) + 1, "created": "later", "from": "staff", "body": body})
        self.tickets[tid]["status"] = "answered"


@pytest.fixture(autouse=True)
def hq(monkeypatch):
    fake = FakeHQ()
    monkeypatch.setattr(support, "TRANSPORT", httpx.MockTransport(fake))
    for name in ("TICO_SUPPORT", "TICO_TELEMETRY", "DO_NOT_TRACK", "TICO_HQ_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TICO_VERSION", "0.2.17")
    return fake


def file(api, headers=None, **body):
    return api.post(
        "/api/v2/support/tickets", json={"message": "The board will not load", **body}, headers=headers or signed_in()
    )


ROOT = Path(__file__).resolve().parents[3]


TICKET_ID = re.compile(r"TK-[A-Z2-7]{8}")  # docs/telemetry.md: "Ticket ids look like TK-AB12CD34"


def documented(doc, *routes):
    """Each route is a row of HQ's documented API (docs/<doc>), the contract an install is held to. HQ's code is not in
    this repository (it is in ticoteam/tico-services); the docs are what both sides agree on."""
    text = (ROOT / "docs" / doc).read_text()
    for route in routes:
        assert f"`{route}`" in text, f"{route} is not documented in docs/{doc}"


class ContractHQ(FakeHQ):
    """FakeHQ with ticket ids and secrets made as HQ makes them, and each request it was sent as
    (method, path, query, X-Ticket-Secret, JSON body), so a test can compare it with the documented contract."""

    def new_ticket_id(self):
        return "TK-" + "".join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567") for _ in range(8))

    def new_secret(self):
        return secrets.token_urlsafe(24)

    def calls(self, start=0):
        return [
            (
                r.method,
                r.url.path,
                r.url.query.decode(),
                r.headers.get("x-ticket-secret"),
                json.loads(r.content) if r.content else None,
            )
            for r in self.seen[start:]
        ]
