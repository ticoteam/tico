"""Contact support (PRIVACY.md, "Support tickets"): the form's request is forwarded to HQ, only on submit and never in demo
mode, and a person sees their own tickets and HQ's replies, and no one else's."""

import json
import re
import secrets
from pathlib import Path

import httpx
import pytest

from backend import support
from backend.auth import Identity
from backend.tests.test_onboarding import as_person, environment, signed_in  # noqa: F401


class FakeHQ:
    """Just enough of HQ's ticket routes (docs/telemetry.md, "Support tickets"): keeps tickets, answers by secret, and
    can be told to fail."""

    def __init__(self):
        self.tickets, self.seen, self.down, self.status_override = {}, [], False, None
        self.read_status = None      # answers every ticket read with this status (429: HQ asking us to slow down)
        self.fields = None           # an older HQ: refuses a ticket carrying a field beyond these
        self.refuse_field = None     # refuses every ticket, naming this field as the wrong one

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
            return httpx.Response(200, json={"ticket_id": parts[2], "status": ticket["status"], "created": "x",
                                             "updated": "y", "messages": ticket["messages"]})
        if request.method == "DELETE":
            del self.tickets[parts[2]]
            return httpx.Response(200, json={"deleted": True})
        if parts[-1] == "messages":
            ticket["messages"].append({"id": len(ticket["messages"]) + 1, "created": "z", "from": "person",
                                       "body": json.loads(request.content)["message"]})
            ticket["status"] = "open"
            return httpx.Response(201, json={"ticket_id": parts[2], "status": "open",
                                             "message_id": len(ticket["messages"])})
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
                                           "version": "0.2.17", "install_id": form["install_id"], "source": "person"}

    # Without the checkbox: only the message (and the email if one is given).
    file(api, include_ids=False, app_version="0.3.47")
    assert json.loads(hq.seen[-1].content) == {"message": "The board will not load", "source": "person"}
    assert file(api, include_ids=False).json()["sent"] == {}


def test_the_desktop_apps_version_goes_labelled_apart_and_an_older_hq_still_gets_the_ticket(environment, hq):
    api = environment()
    assert file(api, app_version="0.3.47").json()["sent"]["app_version"] == "0.3.47"
    sent = json.loads(hq.seen[-1].content)
    assert sent["version"] == "0.2.17" and sent["app_version"] == "0.3.47"
    assert "app_version" not in file(api, app_version="0.3.47; rm -rf").json()["sent"]
    assert "app_version" not in json.loads(hq.seen[-1].content)

    hq.fields = {"message", "email", "install_id", "version"}
    r = file(api, app_version="0.3.47")
    assert r.status_code == 200 and "app_version" not in r.json()["sent"]
    assert [json.loads(x.content).get("app_version") for x in hq.seen[-2:]] == ["0.3.47", None]
    assert len(hq.tickets) == 3

    # Only that refusal is retried: HQ objecting to the value itself is answered at once; objecting to another field is
    # answered after the one retry without source (an older HQ's refusal does not say which field it was).
    for fields, refuse, body, asks in ((None, "app_version", {"app_version": "0.3.47"}, 1),
                                       ({"message"}, None, {"email": "ana@acme.example"}, 2)):
        hq.fields, hq.refuse_field, before = fields, refuse, len(hq.seen)
        r = file(api, **body)
        assert r.status_code < 500 and r.status_code >= 400, r.text
        assert len(hq.seen) == before + asks
    assert "source" not in json.loads(hq.seen[-1].content)
    assert len(hq.tickets) == 3


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
    # A ticket filed while support was on: once it is off, nothing about it, or anything else, reaches HQ.
    api = environment()
    ticket = "/api/v2/support/tickets/" + file(api).json()["id"]
    hq.seen.clear()
    monkeypatch.setenv("TICO_SUPPORT", "off")
    for call in (lambda: file(api), lambda: api.get("/api/v2/support/compose", headers=signed_in()),
                 lambda: api.post("/api/v2/support/tickets/refresh", headers=signed_in()),
                 lambda: api.post(ticket + "/messages", json={"message": "hi"}, headers=signed_in()),
                 lambda: api.delete(ticket, headers=signed_in())):
        assert call().status_code == 409
    assert hq.seen == []


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


def test_refresh_paces_tickets_by_activity_and_asks_about_the_one_in_view(environment, hq):
    """HQ counts every ask from an install together, so a ticket written on in the last hour is asked about every
    30 s, a quiet open one every 5 minutes, a closed one every 6 hours, and the one a person has open every 30 s
    whatever it is; the most recently written on first, and a 429 ends the round like HQ being down."""
    api = environment()
    ids = {name: file(api, message=name).json()["id"] for name in ("busy", "quiet", "stale", "closed")}
    hq_of = dict(zip(ids, hq.tickets))
    now = support.H.now()
    ago = lambda seconds: support.H.shift(now, seconds=-seconds)
    hq_stamp = lambda seconds: ago(seconds)[:19] + "Z"             # HQ's own format
    old = ago(2 * 86400)
    rows = {  # name: (status, created, checked, newest message)
        "busy": ("answered", old, ago(60), hq_stamp(600)),        # the person wrote 10 minutes ago
        "quiet": ("answered", old, ago(60), hq_stamp(86400)),     # nothing for a day, asked a minute ago
        "stale": ("open", old, ago(400), hq_stamp(86400)),        # nothing for a day, asked 6+ minutes ago
        "closed": ("closed", old, ago(3600), hq_stamp(86400)),
    }
    with api.app.state.store.transaction() as c:
        for name, (status, created, checked, said) in rows.items():
            hq.tickets[hq_of[name]]["status"] = status
            hq.tickets[hq_of[name]]["messages"] = [{"id": 1, "created": said, "from": "person", "body": "hi"}]
            c.execute("UPDATE support_tickets SET status=?, created_at=?, checked=?, messages=? WHERE id=?",
                      (status, created, checked, json.dumps([{"id": 1, "created_at": said, "from": "person", "body": "hi",
                                                              "has_diagnostics": False}]), ids[name]))

    def asked(body=None):
        hq.seen.clear()
        r = api.post("/api/v2/support/tickets/refresh", headers=signed_in(), **({"json": body} if body else {}))
        assert r.status_code == 200, r.text
        return [request.url.path.rsplit("/", 1)[1] for request in hq.seen], r.json()["refresh_failed"]

    # Busy is due at 30 s and asked first; quiet waits 5 minutes; stale is due; closed waits 6 hours.
    assert asked() == ([hq_of["busy"], hq_of["stale"]], False)
    # The one in view is asked about first, even closed, and every 30 s; another person's ticket is not.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE support_tickets SET checked=? WHERE id IN (?,?)", (ago(60), ids["busy"], ids["closed"]))
    assert asked({"ticket": ids["closed"]}) == ([hq_of["closed"], hq_of["busy"]], False)
    riley = file(api, headers=as_person(api, "riley"), message="Riley's").json()["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE support_tickets SET checked=?", (now,))
    assert asked({"ticket": riley}) == ([], False)
    # HQ answering 429 ends the round after the first ask, the most recent first, and keeps what was known.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE support_tickets SET checked=?", (ago(400),))
    hq.read_status = 429
    assert asked() == ([hq_of["busy"]], True)


ROOT = Path(__file__).resolve().parents[2]
TICKET_ID = re.compile(r"TK-[A-Z2-7]{8}")             # docs/telemetry.md: "Ticket ids look like TK-AB12CD34"


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
        return [(r.method, r.url.path, r.url.query.decode(), r.headers.get("x-ticket-secret"),
                 json.loads(r.content) if r.content else None) for r in self.seen[start:]]


def test_the_ticket_round_trip_keeps_to_hq_s_documented_contract(environment, monkeypatch):
    """The page's whole ticket round trip, with every request the install makes held exactly to HQ's ticket routes as
    docs/telemetry.md ("Support tickets") documents them: the method and path, the ticket's secret in `X-Ticket-Secret`
    (never in the address, never a staff key), and the JSON fields. HQ answers in the documented shapes, and the page
    shows what they say: a staff reply arrives as unread, writing back reaches the ticket, reading clears the count,
    deleting removes it. HQ's own side (its queue, the staff routes) is tested in ticoteam/tico-services."""
    documented("telemetry.md", "POST /v1/support", "GET /v1/support/{id}", "POST /v1/support/{id}/messages",
               "DELETE /v1/support/{id}", "POST /v1/staff/tickets/{id}/reply")
    hq = ContractHQ()
    monkeypatch.setattr(support, "TRANSPORT", httpx.MockTransport(hq))
    monkeypatch.setattr(support, "FRESH_S", 0)
    api = environment()
    install_id = api.get("/api/v2/support/compose", headers=signed_in()).json()["install_id"]

    # Filing: `POST /v1/support` with the documented fields only and no secret; `201 {ticket_id, secret, status}`.
    mine = file(api).json()
    (tid, held), = hq.tickets.items()
    assert TICKET_ID.fullmatch(tid) and mine["status"] == "open"
    assert hq.calls() == [("POST", "/v1/support", "", None,
                           {"message": "The board will not load", "source": "person", "version": "0.2.17",
                            "install_id": install_id})]
    secret = held["secret"]
    assert secret not in json.dumps(api.get("/api/v2/support/tickets", headers=signed_in()).json())

    # The team replies (`POST /v1/staff/tickets/{id}/reply`, HQ's side); the page's refresh asks
    # `GET /v1/support/{id}` with the secret and shows the documented answer, the reply unread.
    hq.staff_replies(tid, "Try a reload")
    mark = len(hq.seen)
    api.post("/api/v2/support/tickets/refresh", headers=signed_in())
    assert hq.calls(mark) == [("GET", f"/v1/support/{tid}", "", secret, None)]
    listed = api.get("/api/v2/support/tickets", headers=signed_in()).json()
    (ticket,) = listed["tickets"]
    assert ticket["id"] == mine["id"] and ticket["status"] == "answered" and listed["unread"] == 1
    assert [(m["id"], m["from"], m["body"]) for m in ticket["messages"]] == [(1, "staff", "Try a reload")]

    # Writing back: `POST /v1/support/{id}/messages` `{message}`, then the ticket is read again.
    mark = len(hq.seen)
    assert api.post(f"/api/v2/support/tickets/{mine['id']}/messages", json={"message": "That fixed it"},
                    headers=signed_in()).json()["status"] == "open"
    assert hq.calls(mark) == [("POST", f"/v1/support/{tid}/messages", "", secret, {"message": "That fixed it"}),
                              ("GET", f"/v1/support/{tid}", "", secret, None)]
    assert [m["body"] for m in hq.tickets[tid]["messages"]] == ["Try a reload", "That fixed it"]

    # Reading the thread clears the unread count and asks HQ nothing.
    mark = len(hq.seen)
    api.post(f"/api/v2/support/tickets/{mine['id']}/read", headers=signed_in())
    assert api.get("/api/v2/support/tickets", headers=signed_in()).json()["unread"] == 0
    assert hq.calls(mark) == []

    # Deleting: `DELETE /v1/support/{id}` with the secret.
    mark = len(hq.seen)
    assert api.delete(f"/api/v2/support/tickets/{mine['id']}", headers=signed_in()).json() == {"deleted": True}
    assert hq.calls(mark) == [("DELETE", f"/v1/support/{tid}", "", secret, None)] and not hq.tickets

    # Every request went to HQ's address over HTTPS, as JSON when it had a body, and never with a staff key.
    for request in hq.seen:
        assert (request.url.scheme, request.url.host) == ("https", "updates.tico.team")
        assert "authorization" not in request.headers
        assert request.headers.get("content-type") == ("application/json" if request.content else None)


def stored_secret(api, ticket_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT hq_secret FROM support_tickets WHERE id=?", (ticket_id,)).fetchone()[0]


def test_the_ticket_secret_is_sealed_with_the_credential_vault_and_every_use_still_works(environment, hq):
    api = environment()
    key = api.app.state.store.settings.db_path.parent / "credential.key"
    assert not key.exists()                       # a fresh install: the vault has no key yet
    ticket = file(api).json()
    (tid, held), = hq.tickets.items()
    stored = stored_secret(api, ticket["id"])
    assert held["secret"] not in stored and stored.startswith("sealed:v1:")
    assert key.exists() and oct(key.stat().st_mode & 0o777) == "0o600"   # the first ticket made the vault's key
    # Writing, refreshing and deleting send HQ the real secret (FakeHQ answers 404 to any other).
    hq.staff_replies(tid, "Try a reload")
    assert api.post("/api/v2/support/tickets/refresh", headers=signed_in()).json()["tickets"][0]["status"] == "answered"
    assert api.post(f"/api/v2/support/tickets/{ticket['id']}/messages", json={"message": "Thanks"},
                    headers=signed_in()).status_code == 200
    assert api.delete(f"/api/v2/support/tickets/{ticket['id']}", headers=signed_in()).json() == {"deleted": True}
    assert not hq.tickets


def test_plaintext_ticket_secrets_from_before_are_sealed_once_on_start(environment, hq):
    api = environment()
    ticket = file(api).json()
    (tid, held), = hq.tickets.items()
    with api.app.state.store.transaction() as c:       # as the release before this one kept it
        c.execute("UPDATE support_tickets SET hq_secret=? WHERE id=?", (held["secret"], ticket["id"]))
    vault = api.app.state.vault
    assert support.seal_plaintext_secrets(api.app.state.store, vault) == 1
    sealed = stored_secret(api, ticket["id"])
    assert sealed.startswith("sealed:v1:") and held["secret"] not in sealed
    assert support.seal_plaintext_secrets(api.app.state.store, vault) == 0 and stored_secret(api, ticket["id"]) == sealed
    hq.staff_replies(tid, "Fixed")
    assert api.post("/api/v2/support/tickets/refresh", headers=signed_in()).json()["tickets"][0]["status"] == "answered"


def test_a_vault_that_cannot_be_opened_refuses_a_ticket_before_anything_reaches_hq(environment, hq):
    api = environment()
    file(api)
    hq.seen.clear()
    (api.app.state.store.settings.db_path.parent / "credential.key").unlink()
    api.app.state.vault.cipher.cached = None
    r = file(api, message="Another problem")
    assert r.status_code == 503 and r.json()["error"]["code"] == "vault_unavailable"
    assert hq.seen == [] and len(hq.tickets) == 1


# ----------------------------------------------------------------------------- a bot's report, approved by a person
REPORT = "hub task update failed since 0.2.17 with: refused (version). Tried a fresh read; same error."


def as_bot(api, slug="coo"):
    """The bot, connected as an external agent (a Hermes profile): its own standing credential, no runner lease."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug=?", (slug,))
        c.execute("INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
                  (slug, "hermes", "hash-" + slug, "2026-01-01T00:00:00Z", "human:test"))
    api.app.state.store.settings.test_identities["bot-" + slug] = Identity("bot:" + slug, "bot", agent="hermes")
    return signed_in("bot-" + slug)


def draft(api, headers, message=REPORT, **extra):
    import hashlib
    payload = {"message": message, "content_sha256": hashlib.sha256(message.encode()).hexdigest(), **extra}
    import uuid
    return api.post("/api/v2/approvals", json={"kind": "support", "payload": payload},
                    headers={**headers, "Idempotency-Key": str(uuid.uuid4())})


def decide(api, approval_id, decision="approved"):
    with api.app.state.store.read() as c:
        to = c.execute("SELECT m.to_actor FROM approvals a JOIN messages m ON m.id=a.message_id WHERE a.id=?",
                       (approval_id,)).fetchone()[0]
    return to, api.post(f"/api/v2/approvals/{approval_id}", json={"decision": decision}, headers=signed_in())


def send(api, headers, approval_id):
    return api.post("/api/v2/support/tickets/from-approval", json={"approval": approval_id}, headers=headers)


def consumed(api, approval_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT consumed_at FROM approvals WHERE id=?", (approval_id,)).fetchone()[0]


def test_a_bot_drafts_a_person_approves_and_the_bot_sends_one_ticket_owned_by_the_person(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    bot = as_bot(api)
    r = draft(api, bot)
    assert r.status_code == 200, r.text
    approval = r.json()
    assert approval["kind"] == "support" and hq.seen == []          # drafting sends nothing
    assert send(api, bot, approval["id"]).json()["error"]["code"] == "approval"     # not approved yet
    assert hq.seen == [] and not consumed(api, approval["id"])
    needs = api.get("/api/v2/needs-you", headers=signed_in()).json()["items"]
    card = next(i for i in needs if i.get("id") == approval["id"])
    assert card["title"] == "Send this to Tico support?" and card["support"] == {"to": "updates.tico.team"}
    approver, decided = decide(api, approval["id"])
    assert decided.status_code == 200, decided.text
    r = send(api, bot, approval["id"])
    assert r.status_code == 200, r.text
    sent = r.json()
    assert sent["filed_as"] == approver and sent["ticket"]["message"] == REPORT and consumed(api, approval["id"])
    assert sent["ticket"]["sent"]["version"] == "0.2.17" and "secret" not in r.text
    (request,) = hq.seen
    assert set(json.loads(request.content)) == {"message", "version", "install_id", "source"}    # no email, no diagnostics
    assert json.loads(request.content)["source"] == "bot_report"        # a bot's report a person approved
    # The approver's ticket: on their Support page, counted for them, not the bot's.
    with api.app.state.store.read() as c:
        assert [tuple(x) for x in c.execute("SELECT actor FROM support_tickets")] == [(approver,)]
    mine = api.get("/api/v2/support/tickets", headers=signed_in()).json()["tickets"]
    assert [t["id"] for t in mine] == [sent["ticket"]["id"]]
    # Again: the same ticket back, nothing more reaches HQ.
    again = send(api, bot, approval["id"])
    assert again.status_code == 200 and again.json()["ticket"]["id"] == sent["ticket"]["id"] and again.json()["again"]
    assert len(hq.seen) == 1 and len(hq.tickets) == 1


def test_without_ids_only_the_message_goes(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    bot = as_bot(api)
    approval = draft(api, bot, include_ids=False).json()
    decide(api, approval["id"])
    assert send(api, bot, approval["id"]).json()["ticket"]["sent"] == {}
    assert json.loads(hq.seen[-1].content) == {"message": REPORT, "source": "bot_report"}


def test_a_report_is_refused_when_declined_sent_by_another_or_not_what_was_approved(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    bot = as_bot(api)
    declined = draft(api, bot, message="Declined report").json()
    decide(api, declined["id"], "declined")
    r = send(api, bot, declined["id"])
    assert r.status_code == 409 and "declined" in r.json()["error"]["detail"]
    approval = draft(api, bot).json()
    decide(api, approval["id"])
    for other in (as_bot(api, "botops"), signed_in()):           # another bot, or the approver themselves
        assert send(api, other, approval["id"]).status_code == 403
    assert send(api, bot, "nope").status_code == 404
    # The stored message changed after the person read it: its hash no longer matches.
    with api.app.state.store.transaction() as c:
        row = c.execute("SELECT payload FROM approvals WHERE id=?", (approval["id"],)).fetchone()[0]
        c.execute("UPDATE approvals SET payload=? WHERE id=?",
                  (row.replace("hub task update", "hub task delete"), approval["id"]))
    r = send(api, bot, approval["id"])
    assert r.status_code == 409 and "hash" in r.json()["error"]["detail"]
    assert hq.seen == [] and not consumed(api, approval["id"])


@pytest.mark.parametrize("payload, why", [
    ({"message": REPORT, "content_sha256": "0" * 64}, "doesn't match"),
    ({"message": REPORT, "content_sha256": "ABC"}, "64 lower-case hex"),
    ({"message": REPORT}, "support needs content_sha256"),
    ({"message": " padded ", "content_sha256": __import__("hashlib").sha256(b" padded ").hexdigest()}, "blank space"),
    ({"message": "x" * 4001, "content_sha256": __import__("hashlib").sha256(b"x" * 4001).hexdigest()}, "4000"),
    ({"message": REPORT, "content_sha256": __import__("hashlib").sha256(REPORT.encode()).hexdigest(),
      "diagnostics": "abc"}, "not diagnostics"),
    ({"message": REPORT, "content_sha256": __import__("hashlib").sha256(REPORT.encode()).hexdigest(),
      "include_ids": "yes"}, "true or false"),
])
def test_a_draft_that_is_not_exactly_a_report_is_refused(environment, hq, payload, why):  # noqa: F811  (the fixture)
    api = environment()
    r = api.post("/api/v2/approvals", json={"kind": "support", "payload": payload}, headers=as_bot(api))
    assert r.status_code >= 400 and why in r.text, r.text


def test_support_off_refuses_drafting_and_sending(environment, hq, monkeypatch):  # noqa: F811  (the fixture)
    api = environment()
    bot = as_bot(api)
    approval = draft(api, bot).json()
    decide(api, approval["id"])
    monkeypatch.setenv("TICO_SUPPORT", "off")
    assert draft(api, bot, message="Another").status_code == 409
    assert send(api, bot, approval["id"]).status_code == 409
    assert hq.seen == [] and not consumed(api, approval["id"])


def test_hq_down_or_refusing_spends_nothing_and_a_retry_sends_it(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    bot = as_bot(api)
    approval = draft(api, bot).json()
    decide(api, approval["id"])
    hq.down = True
    r = send(api, bot, approval["id"])
    assert r.status_code == 502 and r.json()["error"]["retryable"] is True and not consumed(api, approval["id"])
    hq.down, hq.status_override = False, 429
    assert send(api, bot, approval["id"]).status_code == 429 and not consumed(api, approval["id"])
    hq.status_override = None
    r = send(api, bot, approval["id"])
    assert r.status_code == 200 and consumed(api, approval["id"]) and len(hq.tickets) == 1
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 1


def test_the_person_only_routes_still_refuse_a_bot(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    bot = as_bot(api)
    assert file(api, headers=bot).status_code == 403
    for path in ("/api/v2/support/tickets", "/api/v2/support/compose"):
        assert api.get(path, headers=bot).status_code == 403
    assert hq.seen == []


def test_a_bot_reports_and_sends_over_mcp(environment, hq):  # noqa: F811  (the fixture)
    from backend.tests.test_mcp import call, rpc
    api = environment()
    as_bot(api)
    tools = {t["name"] for t in rpc(api, "tools/list", token="bot-coo")["result"]["tools"]}
    assert {"hub_support_report", "hub_support_send"} <= tools
    err, drafted = call(api, "hub_support_report", {"message": "  " + REPORT.replace(". ", ".\r\n") + "\n"}, token="bot-coo")
    assert not err, drafted
    assert "approve" in drafted["next"] and hq.seen == []
    err, early = call(api, "hub_support_send", {"approval": drafted["approval"]}, token="bot-coo")
    assert err and hq.seen == []
    decide(api, drafted["approval"])
    err, sent = call(api, "hub_support_send", {"approval": drafted["approval"]}, token="bot-coo")
    assert not err, sent
    assert json.loads(hq.seen[-1].content)["message"] == REPORT.replace(". ", ".\n")    # what the person read
    err, again = call(api, "hub_support_send", {"approval": drafted["approval"]}, token="bot-coo")
    assert not err and again["ticket"]["id"] == sent["ticket"]["id"] and len(hq.tickets) == 1


def test_a_persons_own_draft_is_theirs_to_approve_and_is_filed_as_them(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    as_person(api, "riley")
    tokens = {"riley-token": Identity("human:riley", "human", email="riley@acme.example", via_token=True),
              "riley-assistant": Identity("human:riley", "human", email="riley@acme.example", via="assistant")}
    api.app.state.store.settings.test_identities.update(tokens)
    # The Assistant drafts nothing on its own: like any write it may not make, it proposes it on a Confirm card.
    assert draft(api, signed_in("riley-assistant")).json()["error"]["code"] == "confirm_required"
    for token, message in (("riley-token", REPORT), ("riley-token", "A second report from Riley's token")):
        approval = draft(api, signed_in(token), message=message).json()
        with api.app.state.store.read() as c:
            to = c.execute("SELECT m.to_actor FROM approvals a JOIN messages m ON m.id=a.message_id WHERE a.id=?",
                           (approval["id"],)).fetchone()[0]
        assert to == "human:riley"                                   # not the default person
        # Neither their token nor the Assistant says yes for them: only Riley at their browser.
        for proxy in (token, "riley-assistant"):
            assert api.post(f"/api/v2/approvals/{approval['id']}", json={"decision": "approved"},
                            headers=signed_in(proxy)).status_code == 403
        assert api.post(f"/api/v2/approvals/{approval['id']}", json={"decision": "approved"},
                        headers=signed_in()).status_code == 403      # nor the owner: it isn't theirs
        assert api.post(f"/api/v2/approvals/{approval['id']}", json={"decision": "approved"},
                        headers=signed_in("riley")).status_code == 200
        sent = send(api, signed_in(token), approval["id"])
        assert sent.status_code == 200, sent.text
        assert sent.json()["filed_as"] == "human:riley"
    riley = api.get("/api/v2/support/tickets", headers=signed_in("riley")).json()["tickets"]
    assert len(riley) == 2 and api.get("/api/v2/support/tickets", headers=signed_in()).json()["tickets"] == []


def test_a_bots_draft_keeps_the_usual_approver(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    approval = draft(api, as_bot(api)).json()
    approver, _ = decide(api, approval["id"])
    assert approver != "bot:coo" and approver.startswith("human:")


def test_a_persons_token_cannot_say_yes_to_a_report_through_the_needs_you_list(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    approval = draft(api, as_bot(api)).json()
    with api.app.state.store.read() as c:
        approver = c.execute("SELECT m.to_actor FROM approvals a JOIN messages m ON m.id=a.message_id WHERE a.id=?",
                             (approval["id"],)).fetchone()[0]
    me = api.get("/api/me", headers=signed_in()).json()
    api.app.state.store.settings.test_identities["approver-token"] = Identity(
        approver, me.get("role") or "owner", email=me.get("email") or "", via_token=True)
    token = signed_in("approver-token")
    b = api.post("/api/v2/batch", json={}, headers=token).json()
    assert b.get("item", {}).get("key") == "approval:" + approval["id"], b
    r = api.post(f"/api/v2/batch/{b['id']}/respond", json={"kind": "decide", "decision": "approve"}, headers=token)
    assert r.status_code == 200, r.text
    done = api.post(f"/api/v2/batch/{b['id']}/commit", json={}, headers=token).json()
    assert done["committed"] and len(done["errors"]) == 1 and "in Tico itself" in done["errors"][0], done
    with api.app.state.store.read() as c:
        assert c.execute("SELECT decision FROM approvals WHERE id=?", (approval["id"],)).fetchone()[0] is None
    assert send(api, as_bot(api), approval["id"]).status_code != 200
    assert hq.seen == [] and not consumed(api, approval["id"])


def test_a_setup_request_starts_from_a_snapshot_and_keeps_its_kind_even_at_an_older_hq(environment, hq):  # noqa: F811
    from backend.store import H
    api = environment()
    with api.app.state.store.transaction() as c:                   # a parked bot whose name must not go
        H.sync_registry(c, {"payroll": {"name": "Payroll for Acme Corp", "status": "active"}}, None)
        c.execute("INSERT OR REPLACE INTO bot_config(bot,config,operator,onboarding_state) "
                  "VALUES('payroll','{}','human:test','needs_setup')")
        c.execute("UPDATE bots SET display_name='Payroll for Acme Corp' WHERE slug='payroll'")
    owner_items = api.get("/api/v2/getting-started", headers=signed_in()).json()["items"]
    assert any("Payroll for Acme Corp" in item["label"] for item in owner_items)
    snapshot = api.get("/api/v2/support/setup-snapshot?asked_from=bot", headers=signed_in()).json()
    assert "Payroll" not in snapshot["message"] and "- Bots in Needs setup: 1" in snapshot["message"]
    plain = api.get("/api/v2/support/setup-snapshot?asked_from=health", headers=as_person(api, "quinn")).json()
    assert "Needs setup" not in plain["message"] and "Payroll" not in plain["message"]
    assert hq.seen == [] and snapshot["kind"] == "setup"           # building the snapshot sends nothing
    lines = snapshot["message"].splitlines()
    assert "Setup snapshot (asked from a bot in Needs setup):" in lines and "- Computer online: no" in lines
    assert any(line.startswith("- Steps done: ") for line in lines) and "- First bot ready: no" in lines
    assert file(api, message=snapshot["message"], kind="setup").json()["sent"]["kind"] == "setup"
    assert json.loads(hq.seen[-1].content)["kind"] == "setup"
    hq.fields = {"message", "email", "install_id", "version"}
    assert "kind" not in file(api, message="Stuck on the computer step", kind="setup").json()["sent"]
    assert json.loads(hq.seen[-1].content)["message"] == "[Setup] Stuck on the computer step"
    assert file(api, kind="billing").status_code == 422


def test_the_feedback_question_waits_for_a_first_result_and_an_answer_arrives_as_a_feedback_request(environment, hq, monkeypatch):  # noqa: F811
    from backend.store import H
    from backend.tests.test_usage_count import complete_a_bot_turn
    api = environment()
    store = api.app.state.store
    question = lambda h=None: api.get("/api/v2/config", headers=h or signed_in()).json()["feedback_question"]
    with store.transaction() as c:                                 # everyone so far acted before any result
        c.execute("UPDATE events SET ts=?", (H.shift(H.now(), days=-3),))
    complete_a_bot_turn(api, H.shift(H.now(), days=-1))
    assert question() is None                                      # only the Assistant has answered
    complete_a_bot_turn(api, H.shift(H.now(), hours=2), bot="payroll")
    assert question() is None                                      # a result stamped ahead of this clock waits
    with store.transaction() as c:
        c.execute("UPDATE attempts SET finished_at=? WHERE bot='payroll'", (H.shift(H.now(), days=-1),))
    assert question() == {"moment": "first_result", "question": "How is Tico working for you?"}
    assert api.get("/api/v2/config", headers=as_bot(api)).json()["feedback_question"] is None
    r = api.post("/api/v2/support/feedback", json={"moment": "first_result", "answer": "Good so far"}, headers=signed_in())
    assert r.status_code == 200 and r.json()["answered"]
    sent = json.loads(hq.seen[-1].content)                         # only their words: no IDs, no diagnostics
    assert sent == {"message": sent["message"], "kind": "feedback", "source": "person"} and sent["message"].endswith("\n\nGood so far")
    assert question() is None
    # A week on, the person is asked once more, but not within 3 days of their last answer.
    with store.transaction() as c:
        c.execute("UPDATE attempts SET finished_at=? WHERE bot='payroll'", (H.shift(H.now(), days=-8),))
        c.execute("UPDATE events SET ts=?", (H.shift(H.now(), days=-9),))
        c.execute("UPDATE preferences SET value=json_set(value,'$.first_result.at',?) WHERE key=?",
                  (H.shift(H.now(), days=-1), "support.feedback"))
    assert question() is None
    with store.transaction() as c:
        c.execute("UPDATE preferences SET value=json_set(value,'$.first_result.at',?) WHERE key=?",
                  (H.shift(H.now(), days=-7), "support.feedback"))
    assert question()["moment"] == "day_7"
    before = len(hq.seen)
    assert not api.post("/api/v2/support/feedback", json={"moment": "day_7"}, headers=signed_in()).json()["answered"]
    assert len(hq.seen) == before and question() is None
    # Someone who joined after the first result is asked from their own first action, never about that first result.
    riley = as_person(api, "riley")
    assert question(riley) is None                                 # has not done anything yet
    with store.transaction() as c:
        H.event(c, "human:riley", "test.acted", "x", {})
    assert question(riley) is None
    with store.transaction() as c:
        c.execute("UPDATE events SET ts=? WHERE actor='human:riley'", (H.shift(H.now(), days=-7),))
    from backend import support_asks
    support_asks.JOINED.clear()             # the server keeps a first action once read; only this test rewrites one
    assert question(riley)["moment"] == "day_7"
    monkeypatch.setenv("TICO_SUPPORT", "off")
    assert question(riley) is None and api.get("/api/v2/config", headers=riley).json()["support_on"] is False


def test_each_filing_says_its_source_and_an_older_hq_still_gets_the_ticket(environment, hq):
    """Spec ticket-source.md criteria 1, 3 and 8: the person's own app sends `person` (it never names one), a person's
    claim of `botops` is filed as theirs (BotOps' own request is tested in test_botops_parity.py), nothing else may be
    claimed, and an HQ that refuses `source` as an unknown field gets the ticket again without it, its message
    unchanged (BotOps' name stays at its start, which such an HQ reads)."""
    api = environment()
    file(api)
    assert json.loads(hq.seen[-1].content)["source"] == "person"
    assert "source" not in file(api).json()["sent"]                 # HQ's sorting, not something the person sent
    file(api, message="[BotOps] Sync fails", source="botops")
    assert json.loads(hq.seen[-1].content)["source"] == "person"
    for claimed in ("bot_report", "health_monitor", "hq_alert", "website", "unknown"):
        assert file(api, source=claimed).status_code == 422
    filed = len(hq.tickets)

    hq.fields = {"message", "email", "install_id", "version", "app_version", "kind"}
    r = file(api, message="[BotOps] Sync fails")
    assert r.status_code == 200, r.text
    first, again = (json.loads(x.content) for x in hq.seen[-2:])
    assert first["source"] == "person" and "source" not in again and again["message"] == "[BotOps] Sync fails"
    assert len(hq.tickets) == filed + 1
    # Today's HQ knows kind but not source: only source is dropped, so a setup request keeps its kind.
    r = file(api, message="Stuck on the computer step", kind="setup")
    assert r.status_code == 200 and r.json()["sent"]["kind"] == "setup"
    last = json.loads(hq.seen[-1].content)
    assert "source" not in last and last["kind"] == "setup" and last["message"] == "Stuck on the computer step"
    # An HQ from before kind as well: both go, as before, and the kind is named in the message.
    hq.fields = {"message", "email", "install_id", "version"}
    r = file(api, message="Stuck again", kind="setup")
    assert r.status_code == 200 and "kind" not in r.json()["sent"]
    last = json.loads(hq.seen[-1].content)
    assert set(last) <= hq.fields and last["message"] == "[Setup] Stuck again"
    # Only those refusals are retried: HQ objecting to the value itself is answered as it is, after one request.
    hq.fields, hq.refuse_field, before = None, "source", len(hq.seen)
    assert 400 <= file(api).status_code < 500 and len(hq.seen) == before + 1


def test_the_botops_tool_says_botops_and_keeps_its_name_in_the_message(monkeypatch):
    """Criterion 3: `hub support file` sends `source: botops` beside the `[BotOps] ` prefix older HQs read."""
    from clients import hubtools
    calls = []

    class Api:
        def post(self, path, body, key=None):
            calls.append((path, body))
            return {}
    monkeypatch.setattr(hubtools, "_as_person", lambda api, ref=None: api)
    hubtools.support_file(Api(), {"message": "Sync fails"})
    assert calls == [("support/tickets", {"message": "[BotOps] Sync fails", "include_ids": True, "source": "botops"})]
