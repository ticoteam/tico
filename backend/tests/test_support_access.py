"""Letting Tico support in (docs/support.md): only an owner or admin opens a session from their own ticket, it ends
on Stop or at its time, every off switch refuses it, the app tier reaches reads and setup only, and the link to HQ
runs what HQ carries as the session and logs it."""

import base64
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend import support, support_access
from backend.auth import Identity
from backend.store import Problem
from backend.tests.test_onboarding import as_person, environment, machine, signed_in  # noqa: F401
from backend.tests.test_support import TICKET_ID, ContractHQ, FakeHQ, documented


class FakeAccessHQ(FakeHQ):
    """FakeHQ plus the access routes: opens sessions, hands out what `queued` holds, keeps the replies."""

    def __init__(self):
        super().__init__()
        self.queued, self.replies, self.ended, self.say_ended, self.reply_calls, self.opened = [], [], [], False, 0, 0

    def __call__(self, request):
        parts = request.url.path.strip("/").split("/")
        if len(parts) >= 4 and parts[3] == "access":
            self.seen.append(request)
            ticket = self.tickets.get(parts[2])
            if not ticket or ticket["secret"] != request.headers.get("x-ticket-secret"):
                return httpx.Response(404, json={"error": "not_found"})
            if len(parts) == 4:
                return httpx.Response(201, json={"session": self.new_session_id()})
            if parts[5] == "pull":
                taken, self.queued = self.queued, []
                return httpx.Response(200, json={"ended": self.say_ended, "requests": taken})
            if parts[5] == "reply":
                self.reply_calls += 1
                self.replies.extend(json.loads(request.content)["replies"])
                return httpx.Response(200, json={"ok": True})
            self.ended.append(parts[4])
            return httpx.Response(200, json={"ended": True})
        return super().__call__(request)

    def new_session_id(self):
        self.opened += 1
        return f"sa_{self.opened:024x}"


@pytest.fixture(autouse=True)
def hq(monkeypatch):
    fake = FakeAccessHQ()
    monkeypatch.setattr(support, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(support_access, "WORKER", False)
    for name in ("TICO_SUPPORT", "TICO_TELEMETRY", "DO_NOT_TRACK", "TICO_HQ_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TICO_SUPPORT_ACCESS", "on")
    monkeypatch.setenv("TICO_VERSION", "0.2.17")
    return fake


def ticket(api, headers=None):
    r = api.post("/api/v2/support/tickets", json={"message": "Bots stopped", "include_ids": False},
                 headers=headers or signed_in())
    assert r.status_code == 200, r.text
    return r.json()["id"]


def start(api, headers=None, **body):
    headers = headers or signed_in()
    body.setdefault("ticket_id", ticket(api, headers))
    return api.post("/api/v2/support/access", json=body, headers=headers)


def bearer(session):
    return {"Authorization": "Bearer " + support_access._tokens[session["id"]], "Idempotency-Key": "k-" + session["id"]}


def test_a_session_starts_shows_everyone_the_banner_and_stops_at_once(environment, hq):
    api = environment()
    riley = as_person(api, "riley")
    assert api.get("/api/v2/support/access", headers=signed_in()).json()["active"] is None
    r = start(api, tier="app", hours=4)
    assert r.status_code == 200, r.text
    session = r.json()
    assert session["tier"] == "app" and 4 * 3600 - 60 < session["seconds_left"] <= 4 * 3600
    opened = json.loads(next(x for x in hq.seen if x.url.path.endswith("/access")).content)
    assert opened["tier"] == "app" and opened["expires_at"].endswith("Z")
    seen = api.get("/api/v2/support/access", headers=riley).json()   # any person sees it
    assert seen["available"] and seen["active"]["id"] == session["id"]
    # One at a time.
    assert start(api).status_code == 409
    token = bearer(session)
    assert api.get("/api/v2/health", headers=token).status_code == 200
    stopped = api.post(f"/api/v2/support/access/{session['id']}/stop", headers=riley)   # an admin may stop it
    assert stopped.status_code == 200 and stopped.json()["end_reason"] == "stopped"
    assert len(hq.ended) == 1
    assert api.get("/api/v2/health", headers=token).status_code == 401
    assert api.get("/api/v2/support/access", headers=signed_in()).json()["active"] is None
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM support_access").fetchone()
        assert row["token_hash"] is None and row["ended_by"] == "human:riley"
        assert c.execute("SELECT count(*) FROM events WHERE action IN ('support_access.started','support_access.stopped')").fetchone()[0] == 2


def test_a_session_keeps_hqs_ids_and_each_request_lists_its_own(environment, hq):
    """One session has one id: HQ's, here as there, and its events name the ticket's HQ id, so ops can match this
    install's log to admin. Each person's requests list the sessions started from them, for the log under each."""
    api = environment()
    riley = as_person(api, "riley")
    first = start(api, hours=1).json()
    assert first["id"] == first["hq_session"] == "sa_" + "0" * 23 + "1"
    with api.app.state.store.read() as c:
        hq_id = c.execute("SELECT hq_id FROM support_tickets WHERE id=?", (first["ticket_id"],)).fetchone()[0]
    assert first["hq_ticket"] == hq_id and hq_id in hq.tickets
    assert api.post(f"/api/v2/support/access/{first['id']}/stop", headers=signed_in()).status_code == 200
    second = start(api, riley).json()
    assert second["id"] == "sa_" + "0" * 23 + "2" and hq.ended == [first["id"]]
    with api.app.state.store.read() as c:
        events = c.execute("SELECT action, target, detail_json FROM events WHERE action LIKE 'support_access.%' "
                           "ORDER BY rowid").fetchall()
    assert [(e["action"], e["target"], json.loads(e["detail_json"])["hq_ticket"]) for e in events][:2] == [
        ("support_access.started", first["id"], hq_id), ("support_access.stopped", first["id"], hq_id)]
    mine = api.get("/api/v2/support/access", headers=signed_in()).json()["sessions"]
    assert [(x["id"], x["ticket_id"], x["end_reason"]) for x in mine] == [(first["id"], first["ticket_id"], "stopped")]
    assert [x["id"] for x in api.get("/api/v2/support/access", headers=riley).json()["sessions"]] == [second["id"]]


def test_an_expired_session_lets_nothing_in_and_the_worker_ends_it(environment, hq):
    api = environment()
    session = start(api).json()
    token = bearer(session)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE support_access SET expires_at='2020-01-01T00:00:00.000000Z'")
    assert api.get("/api/v2/health", headers=token).status_code == 401
    assert support_access.pull_once(api.app) is False
    with api.app.state.store.read() as c:
        assert c.execute("SELECT end_reason FROM support_access").fetchone()[0] == "expired"
    assert start(api).status_code == 200                       # a new one may start


def test_only_an_owner_or_admin_starts_one_and_full_access_is_the_owners(environment, hq):
    api = environment()
    member, admin = as_person(api, "quinn"), as_person(api, "riley")
    assert start(api, member).status_code == 403
    assert start(api, admin, tier="full").status_code == 403
    api.app.state.store.settings.test_identities["tok"] = Identity("human:morgan", "owner", email="owner@acme.example",
                                                                   via_token=True)
    assert api.post("/api/v2/support/access", json={"ticket_id": "sup_x"}, headers=signed_in("tok")).status_code == 403
    # Another person's ticket is not theirs to open a session from.
    assert start(api, admin, ticket_id=ticket(api)).status_code == 404
    assert start(api, admin, tier="app").status_code == 200


@pytest.mark.parametrize("switch", ["flag", "TICO_SUPPORT", "rehearsal", "demo", "rule"])
def test_every_off_switch_refuses_and_ends_what_was_live(environment, hq, monkeypatch, switch):
    api = environment()
    session = start(api).json()
    token = bearer(session)
    if switch == "flag":
        monkeypatch.delenv("TICO_SUPPORT_ACCESS")
    elif switch == "TICO_SUPPORT":
        monkeypatch.setenv("TICO_SUPPORT", "off")
    elif switch == "rehearsal":
        monkeypatch.setenv("TICO_REHEARSAL", "1")
    elif switch == "rule":
        assert api.put("/api/v2/access/rules", json={"support_access": False}, headers=signed_in()).status_code == 200
    else:
        # The same install, its live session and all, now in demo mode.
        monkeypatch.setattr(api.app.state.store.settings, "demo", True)
        api.headers["Host"] = "localhost:8765"
    status = api.get("/api/v2/support/access", headers=signed_in()).json()
    assert status["available"] is False and status["active"] is None and status["off_reason"]
    r = api.post("/api/v2/support/access", json={"ticket_id": "sup_x"}, headers=signed_in())
    assert r.status_code == 409 and r.json()["error"]["code"] == "support_access_off"
    assert api.get("/api/v2/health", headers=token).status_code == 401
    sent = len(hq.seen)
    assert support_access.pull_once(api.app) is False
    # With support itself off nothing goes to HQ, not even the end notice. With only the flag off, HQ hears it ended;
    # the team rule ended it when it was turned off, which tells HQ nothing.
    ended = ["end"] if switch == "flag" else []
    assert [r.url.path.rsplit("/", 1)[-1] for r in hq.seen[sent:]] == ended
    with api.app.state.store.read() as c:
        assert c.execute("SELECT end_reason FROM support_access").fetchone()[0] == "turned_off"


def test_app_access_reads_and_changes_setup_and_nothing_else(environment, hq):
    api = environment()
    token = bearer(start(api, tier="app").json())
    for path in ("/api/v2/health", "/api/v2/access", "/api/v2/bots", "/api/v2/providers"):
        assert api.get(path, headers=token).status_code == 200, path
    refused = [("POST", "/api/v2/credentials/cred_1/reveal", {}), ("POST", "/api/v2/approvals/apr_1", {"decision": "approved"}),
               ("POST", "/api/v2/messages", {"to": "bot:coo", "body": "hi"}), ("POST", "/api/v2/tasks", {"title": "x"}),
               ("PUT", "/api/v2/access/rules", {"admin_sql": False}), ("GET", "/api/v2/me/tokens", None),
               ("GET", "/api/v2/support/access", None), ("POST", "/api/v2/support/access/x/stop", {})]
    for method, path, body in refused:
        r = api.request(method, path, json=body, headers=token)
        assert r.status_code == 403 and r.json()["error"]["code"] == "support_access", (path, r.text)
    computer = machine(api)
    # A model sign-in's code would let support choose the account the computer runs on.
    for path in (f"/api/v2/runners/{computer['runner_id']}/logins", f"/api/v2/runners/{computer['runner_id']}/harness-actions"):
        r = api.post(path, json={"harness": "codex"}, headers=token)
        assert r.status_code == 403 and r.json()["error"]["code"] == "support_access", (path, r.text)
    # Nor read the link and code of a sign-in the person starts during the session.
    for path in (f"/api/v2/runners/{computer['runner_id']}/logins/lgn_1", f"/api/v2/runners/{computer['runner_id']}/logins"):
        r = api.get(path, headers=token)
        assert r.status_code == 403 and r.json()["error"]["code"] == "support_access", (path, r.text)
    r = api.post(f"/api/v2/runners/{computer['runner_id']}/restart", json={}, headers=token)
    assert r.status_code < 300, r.text
    with api.app.state.store.read() as c:
        actors = {row[0] for row in c.execute("SELECT actor FROM events WHERE actor LIKE '%support%'")}
    assert actors == {"human:tico-support"}


def test_full_access_is_the_owners_reach_but_not_the_session_or_tokens(environment, hq):
    api = environment()
    token = bearer(start(api, tier="full").json())
    assert api.put("/api/v2/access/rules", json={"admin_sql": False}, headers=token).status_code == 200
    for method, path in (("GET", "/api/v2/support/access"), ("POST", "/api/v2/me/tokens"),
                         ("POST", "/api/v2/service-keys"), ("POST", "/api/v2/access/owner"),
                         ("POST", "/api/v2/access/owner/email")):
        assert api.request(method, path, json={}, headers=token).status_code == 403, path


def test_full_access_keeps_the_owners_lasting_changes_and_neither_tier_opens_a_personal_page(environment, hq):
    api = environment()
    app_token = bearer(start(api, tier="app").json())
    for method, path in (("POST", "/api/v2/triggers"), ("POST", "/api/v2/triggers/trg_1/delete"),
                         ("POST", "/api/v2/triggers/trg_1/rotate"), ("POST", "/api/v2/credentials"),
                         ("POST", "/api/v2/github/app/disconnect"), ("PUT", "/api/v2/slack/tokens"),
                         ("PUT", "/api/v2/directory"), ("GET", "/api/v2/assistant"),
                         ("GET", "/api/v2/tasks/t1/chat"), ("GET", "/api/v2/calendar/upcoming")):
        r = api.request(method, path, json={}, headers=app_token)
        assert r.status_code == 403 and r.json()["error"]["code"] == "support_access", (method, path, r.text)
    with api.app.state.store.read() as c:
        sid = c.execute("SELECT id FROM support_access WHERE ended_at IS NULL").fetchone()[0]
    assert api.post(f"/api/v2/support/access/{sid}/stop", json={}, headers=signed_in()).status_code == 200
    full_token = bearer(start(api, tier="full").json())
    for method, path in (("GET", "/api/v2/assistant"), ("GET", "/api/v2/tasks/t1/chat"), ("GET", "/api/v2/calendar/upcoming")):
        r = api.request(method, path, headers=full_token)
        assert r.status_code == 403 and r.json()["error"]["code"] == "support_access", (path, r.text)
    # The owner's reach, as decided for the full level: a webhook it makes, or a computer's model sign-in, is its owner's
    # call, not refused here.
    computer = machine(api)
    for path in ("/api/v2/triggers", f"/api/v2/runners/{computer['runner_id']}/logins"):
        r = api.post(path, json={}, headers=full_token)
        assert not (r.status_code == 403 and r.json()["error"]["code"] == "support_access"), (path, r.text)


@pytest.mark.parametrize("path", ["/api/v2/me%2Ftokens", "/api/v2/support%2Faccess", "/api/v2/me/tokens%3F",
                                  "/api/v2/support/access%3Fx", "/api/v2/%73upport/access", "/api/v2/health%3F"])
def test_an_encoded_address_is_refused_as_sent(environment, hq, path):
    api = environment()
    token = bearer(start(api, tier="full").json())
    r = api.get(path, headers=token)
    assert r.status_code == 403 and r.json()["error"]["code"] == "support_access", (path, r.status_code, r.text)


@pytest.mark.parametrize("path", ["/api/v2//support/access", "/api/v2/./me/tokens", "/api/v2/x/../me/tokens",
                                  "/api/v2/%73upport/access", "/api/v2/support/access/", "/api/v2/me;x/tokens",
                                  "/api/v2", "/api/v3/health", ""])
def test_the_gate_reads_only_the_plain_path_form(path):
    who = Identity(support_access.SUPPORT_ACTOR, "owner", support="full")
    with pytest.raises(Problem) as refused:
        support_access.gate(who, path, "GET")
    assert refused.value.status == 403 and refused.value.code == "support_access"
    support_access.gate(who, "/api/v2/health", "GET")
    support_access.gate(who, "/api/v2/health/", "GET")


def test_the_worker_runs_what_hq_carries_as_the_session_and_logs_it(environment, hq):
    api = environment()
    session = start(api, tier="app").json()
    hq.queued = [{"rid": "r1", "by": "support-agent", "method": "GET", "path": "/api/v2/health", "query": "",
                  "content_type": "", "body_b64": ""},
                 {"rid": "r2", "by": "staff", "method": "POST", "path": "/api/v2/messages", "query": "",
                  "content_type": "application/json", "body_b64": base64.b64encode(b'{"to":"bot:coo"}').decode()},
                 {"rid": "r3", "by": "staff", "method": "GET", "path": "/download/latest.json"}]
    assert support_access.pull_once(api.app) is True
    # Each answer went back on its own, as soon as its request finished.
    assert hq.reply_calls == 3
    replies = {r["rid"]: r for r in hq.replies}
    assert replies["r1"]["status"] == 200 and "json" in replies["r1"]["content_type"]
    assert json.loads(base64.b64decode(replies["r1"]["body_b64"]))
    assert replies["r2"]["status"] == 403 and replies["r3"]["status"] == 400
    log = api.get(f"/api/v2/support/access/{session['id']}/log", headers=signed_in()).json()["log"]
    assert [(x["by"], x["method"], x["path"], x["status"]) for x in reversed(log)] == [
        ("support-agent", "GET", "/api/v2/health", 200), ("staff", "POST", "/api/v2/messages", 403),
        ("staff", "GET", "/download/latest.json", 400)]
    assert api.get(f"/api/v2/support/access/{session['id']}/log", headers=as_person(api, "quinn")).status_code == 403
    # HQ ending it ends it here.
    hq.say_ended = True
    assert support_access.pull_once(api.app) is False
    with api.app.state.store.read() as c:
        assert c.execute("SELECT end_reason FROM support_access").fetchone()[0] == "ended_by_hq"


def test_a_restart_mints_a_fresh_token_for_a_live_session(environment, hq):
    api = environment()
    session = start(api).json()
    old = bearer(session)
    support_access._tokens.clear()                             # what a restart forgets
    support_access.started(api.app, support_access._runtime["loop"])
    assert api.get("/api/v2/health", headers=old).status_code == 401
    assert api.get("/api/v2/health", headers=bearer(session)).status_code == 200


class ContractAccessHQ(FakeAccessHQ):
    """FakeAccessHQ with ticket ids, secrets and session ids made as HQ makes them (ContractHQ's)."""

    new_ticket_id, new_secret, calls = ContractHQ.new_ticket_id, ContractHQ.new_secret, ContractHQ.calls

    def new_session_id(self):
        return "sa_" + secrets.token_hex(12)


def test_a_support_session_keeps_to_hq_s_documented_access_contract(environment, monkeypatch):
    """The install's side of a support session, with every request it makes held exactly to the access routes as
    docs/tico-hq.md ("Support access") documents them: it opens the session with its tier and end time, pulls what a
    staff call left at HQ, runs it as the session and replies with its status and body, keeps the log (by, path,
    status), and on Stop ends it at HQ, each with the ticket's secret in `X-Ticket-Secret`. HQ's own side (the staff
    routes, its in-memory queue, refusing a call once the session ended) is tested in ticoteam/tico-services."""
    documented("tico-hq.md", "POST /v1/support/{ticket}/access", "POST /v1/support/{ticket}/access/{session}/pull",
               "POST /v1/support/{ticket}/access/{session}/reply", "POST /v1/support/{ticket}/access/{session}/end")
    hq = ContractAccessHQ()
    monkeypatch.setattr(support, "TRANSPORT", httpx.MockTransport(hq))
    api = environment()

    # Opening, from a ticket just filed: `{tier, expires_at}` (to the second, UTC) answered `{session}`.
    before = datetime.now(timezone.utc).replace(microsecond=0)
    session = start(api, tier="app", hours=2).json()
    (tid, held), = hq.tickets.items()
    secret = held["secret"]
    assert TICKET_ID.fullmatch(tid)
    filed, (method, path, query, sent_secret, body) = hq.calls()
    assert filed == ("POST", "/v1/support", "", None, {"message": "Bots stopped", "source": "person"})
    assert (method, path, query, sent_secret) == ("POST", f"/v1/support/{tid}/access", "", secret)
    assert set(body) == {"tier", "expires_at"} and body["tier"] == "app"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", body["expires_at"])
    ends = datetime.strptime(body["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert before + timedelta(hours=2) <= ends <= datetime.now(timezone.utc) + timedelta(hours=2)
    with api.app.state.store.read() as c:
        hq_session = c.execute("SELECT hq_session FROM support_access WHERE id=?", (session["id"],)).fetchone()[0]
    assert re.fullmatch(r"sa_[0-9a-f]{24}", hq_session)
    base = f"/v1/support/{tid}/access/{hq_session}"

    # A staff call waits at HQ; the pull hands it over as documented, the install runs it and replies to it alone.
    hq.queued = [{"rid": "r1", "by": "support-agent", "method": "GET", "path": "/api/v2/health", "query": "",
                  "content_type": "", "body_b64": ""},
                 {"rid": "r2", "by": "staff", "method": "GET", "path": "/api/v2/health", "query": "verbose=1",
                  "content_type": "", "body_b64": ""}]
    mark = len(hq.seen)
    assert support_access.pull_once(api.app, wait=1) is True
    calls = hq.calls(mark)
    assert [(m, p, q, s) for m, p, q, s, _ in calls] == [("POST", base + "/pull", "", secret),
                                                         ("POST", base + "/reply", "", secret),
                                                         ("POST", base + "/reply", "", secret)]
    assert calls[0][4] == {}
    for (*_, sent), rid in zip(calls[1:], ("r1", "r2")):
        (reply,) = sent["replies"]
        assert set(reply) == {"rid", "status", "content_type", "body_b64"} and reply["rid"] == rid
        assert reply["status"] == 200 and reply["content_type"].startswith("application/json")
        assert json.loads(base64.b64decode(reply["body_b64"], validate=True))
    log = api.get(f"/api/v2/support/access/{session['id']}/log", headers=signed_in()).json()["log"]
    assert [(x["by"], x["method"], x["path"], x["status"]) for x in reversed(log)] == [
        ("support-agent", "GET", "/api/v2/health", 200), ("staff", "GET", "/api/v2/health", 200)]

    # Stop here ends it at HQ, and nothing more is pulled.
    mark = len(hq.seen)
    assert api.post(f"/api/v2/support/access/{session['id']}/stop", headers=signed_in()).json()["end_reason"] == "stopped"
    assert hq.calls(mark) == [("POST", base + "/end", "", secret, {})] and hq.ended == [hq_session]
    assert support_access.pull_once(api.app, wait=1) is False and len(hq.seen) == mark + 1

    for request in hq.seen:
        assert (request.url.scheme, request.url.host) == ("https", "updates.tico.team")
        assert "authorization" not in request.headers and request.headers["content-type"] == "application/json"
