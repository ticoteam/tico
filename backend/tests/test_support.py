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
        c.execute("INSERT OR IGNORE INTO agents(bot,harness,token_hash,created,created_by) VALUES(?,?,?,?,?)",
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


def test_a_bot_drafts_a_person_approves_and_the_bot_sends_one_ticket_owned_by_the_person(environment, hq):
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
    assert set(json.loads(request.content)) == {"message", "version", "install_id"}    # no email, no diagnostics
    # The approver's ticket: on their Support page, counted for them, not the bot's.
    with api.app.state.store.read() as c:
        assert [tuple(x) for x in c.execute("SELECT actor FROM support_tickets")] == [(approver,)]
    mine = api.get("/api/v2/support/tickets", headers=signed_in()).json()["tickets"]
    assert [t["id"] for t in mine] == [sent["ticket"]["id"]]
    # Again: the same ticket back, nothing more reaches HQ.
    again = send(api, bot, approval["id"])
    assert again.status_code == 200 and again.json()["ticket"]["id"] == sent["ticket"]["id"] and again.json()["again"]
    assert len(hq.seen) == 1 and len(hq.tickets) == 1


def test_without_ids_only_the_message_goes(environment, hq):
    api = environment()
    bot = as_bot(api)
    approval = draft(api, bot, include_ids=False).json()
    decide(api, approval["id"])
    assert send(api, bot, approval["id"]).json()["ticket"]["sent"] == {}
    assert json.loads(hq.seen[-1].content) == {"message": REPORT}


def test_a_report_is_refused_when_declined_sent_by_another_or_not_what_was_approved(environment, hq):
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
        row = c.execute("SELECT payload_json FROM approvals WHERE id=?", (approval["id"],)).fetchone()[0]
        c.execute("UPDATE approvals SET payload_json=? WHERE id=?",
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
def test_a_draft_that_is_not_exactly_a_report_is_refused(environment, hq, payload, why):
    api = environment()
    r = api.post("/api/v2/approvals", json={"kind": "support", "payload": payload}, headers=as_bot(api))
    assert r.status_code >= 400 and why in r.text, r.text


def test_support_off_refuses_drafting_and_sending(environment, hq, monkeypatch):
    api = environment()
    bot = as_bot(api)
    approval = draft(api, bot).json()
    decide(api, approval["id"])
    monkeypatch.setenv("TICO_SUPPORT", "off")
    assert draft(api, bot, message="Another").status_code == 409
    assert send(api, bot, approval["id"]).status_code == 409
    assert hq.seen == [] and not consumed(api, approval["id"])


def test_hq_down_or_refusing_spends_nothing_and_a_retry_sends_it(environment, hq):
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


def test_the_person_only_routes_still_refuse_a_bot(environment, hq):
    api = environment()
    bot = as_bot(api)
    assert file(api, headers=bot).status_code == 403
    for path in ("/api/v2/support/tickets", "/api/v2/support/compose"):
        assert api.get(path, headers=bot).status_code == 403
    assert hq.seen == []
