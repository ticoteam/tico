"""Contact support (PRIVACY.md, "Support tickets"): the form's request is forwarded to HQ, only on submit and never in demo
mode, and a person sees their own tickets and HQ's replies, and no one else's."""

import json

import httpx
import pytest

from backend import support
from backend.auth import Identity
from backend.tests.test_onboarding import as_person, environment, signed_in  # noqa: F401


class FakeHQ:
    """Just enough of hq/support.py: keeps tickets, answers by secret, and can be told to fail."""

    def __init__(self):
        self.tickets, self.seen, self.down, self.status_override = {}, [], False, None

    def __call__(self, request):
        self.seen.append(request)
        if self.down:
            raise httpx.ConnectTimeout("down")
        path, secret = request.url.path, request.headers.get("x-ticket-secret", "")
        if request.method == "POST" and path == "/v1/support":
            if self.status_override:
                return httpx.Response(self.status_override, json={"error": "x"})
            body = json.loads(request.content)
            tid = "TK-%08d" % (len(self.tickets) + 1)
            self.tickets[tid] = {"secret": "s" * 20 + str(len(self.tickets)), "status": "open", "input": body, "messages": []}
            return httpx.Response(201, json={"ticket_id": tid, "secret": self.tickets[tid]["secret"], "status": "open"})
        parts = path.strip("/").split("/")
        ticket = self.tickets.get(parts[2]) if len(parts) > 2 else None
        if not ticket or ticket["secret"] != secret:
            return httpx.Response(404, json={"error": "not_found"})
        if request.method == "GET":
            return httpx.Response(200, json={"ticket_id": parts[2], "status": ticket["status"], "created": "x",
                                             "updated": "y", "messages": ticket["messages"]})
        if request.method == "DELETE":
            del self.tickets[parts[2]]
            return httpx.Response(200, json={"deleted": True})
        if parts[-1] == "messages":
            ticket["messages"].append({"id": len(ticket["messages"]) + 1, "created": "z", "from": "person",
                                       "body": json.loads(request.content)["message"]})
            ticket["status"] = "open"
            return httpx.Response(201, json={"status": "open"})
        return httpx.Response(404, json={})

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
    return api.post("/api/v2/support/tickets", json={"message": "The board will not load", **body},
                    headers=headers or signed_in())


def test_submit_forwards_exactly_what_the_form_showed_and_keeps_the_secret_on_the_server(environment, hq):
    api = environment()
    form = api.get("/api/v2/support/compose", headers=signed_in()).json()
    assert form["version"] == "0.2.17" and len(form["install_id"]) == 36 and form["to"] == "updates.tico.team"
    assert hq.seen == []                                          # opening the form sends nothing

    r = file(api, email="ana@acme.example")
    assert r.status_code == 200
    ticket = r.json()
    assert ticket["status"] == "open" and ticket["sent"] == {"version": "0.2.17", "install_id": form["install_id"]}
    assert "secret" not in r.text and "TK-" not in r.text and "hq_secret" not in r.text
    (request,) = hq.seen
    assert request.method == "POST" and str(request.url) == "https://updates.tico.team/v1/support"
    assert json.loads(request.content) == {"message": "The board will not load", "email": "ana@acme.example",
                                           "version": "0.2.17", "install_id": form["install_id"]}

    # Without the checkbox: only the message (and the email if one is given).
    file(api, include_ids=False)
    assert json.loads(hq.seen[-1].content) == {"message": "The board will not load"}
    assert file(api, include_ids=False).json()["sent"] == {}


@pytest.mark.parametrize("bad", [{"message": "x" * 4001}])
def test_bad_input_never_reaches_hq(environment, hq, bad):
    api = environment()
    assert file(api, **bad).status_code == 422
    assert hq.seen == []


def test_demo_mode_and_the_off_switch_refuse_everything(environment, hq, monkeypatch):
    api = environment(demo=True)
    api.headers["Host"] = "localhost:8765"                        # the demo answers loopback names only
    for call in (lambda: file(api), lambda: api.get("/api/v2/support/compose", headers=signed_in()),
                 lambda: api.post("/api/v2/support/tickets/refresh", headers=signed_in())):
        assert call().status_code == 409
    assert api.get("/api/v2/support/tickets", headers=signed_in()).json() == {"enabled": False, "tickets": [], "unread": 0}
    assert hq.seen == []
    monkeypatch.setenv("TICO_SUPPORT", "off")
    api = environment()
    assert file(api).status_code == 409 and hq.seen == []


def test_only_a_signed_in_person_may_file(environment, hq):
    api = environment()
    for identity in (Identity("human:riley", "human", email="riley@acme.example", via_token=True),
                     Identity("human:riley", "human", email="riley@acme.example", via="assistant")):
        api.app.state.store.settings.test_identities["viaa"] = identity
        assert file(api, headers=signed_in("viaa")).status_code == 403
    assert api.get("/api/v2/support/tickets").status_code in (401, 403)
    assert hq.seen == []


def test_a_person_sees_their_own_tickets_and_no_one_elses(environment, hq):
    api = environment()
    riley = as_person(api, "riley")
    mine, theirs = file(api).json(), file(api, headers=riley, message="Riley's problem").json()
    owner_list = api.get("/api/v2/support/tickets", headers=signed_in()).json()["tickets"]
    riley_list = api.get("/api/v2/support/tickets", headers=riley).json()["tickets"]
    assert [t["id"] for t in owner_list] == [mine["id"]] and [t["id"] for t in riley_list] == [theirs["id"]]
    other = "/api/v2/support/tickets/" + theirs["id"]
    assert api.post(other + "/messages", json={"message": "hi"}, headers=signed_in()).status_code == 404
    assert api.post(other + "/read", headers=signed_in()).status_code == 404
    assert api.delete(other, headers=signed_in()).status_code == 404
    assert len(hq.tickets) == 2                                     # the other person's ticket is untouched
    # Refresh asks HQ about the caller's tickets only.
    hq.seen.clear()
    api.post("/api/v2/support/tickets/refresh", headers=signed_in())
    assert [r.url.path for r in hq.seen] == ["/v1/support/TK-00000001"]
