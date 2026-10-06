"""Regressions for the pre-pilot security review (reports/2026-09-29-security-review.md)."""
import asyncio

from backend.store import encode
from backend.tests.test_api import api, post, setup_attempt  # noqa: F401


def raw_get(app, path):
    """One GET with the path exactly as sent: an HTTP client would resolve the dot segments."""
    scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [], "http_version": "1.1", "scheme": "http", "server": ("test", 80),
             "client": ("203.0.113.9", 1), "root_path": ""}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)
    asyncio.run(app(scope, receive, send))
    return sent[0]["status"]


def test_dot_segments_do_not_carry_a_sign_in_exemption_to_the_page(api):
    assert raw_get(api.app, "/index.html") == 401
    assert raw_get(api.app, "/download/../index.html") == 404
    assert raw_get(api.app, "/download/./../changelog.js") == 404


def test_pages_and_api_refuse_foreign_framing(api):
    for path in ("/api/v2/me", "/"):
        headers = api.get(path, headers={"Authorization": "Bearer ana-test"}).headers
        assert headers["x-frame-options"] == "SAMEORIGIN"
        assert "frame-ancestors 'self'" in headers["content-security-policy"]
    page = api.get("/", headers={"Authorization": "Bearer ana-test"}).headers["content-security-policy"]
    # The page has no inline script (its code is ui/app/*.js), so only our own files may run.
    assert page.startswith("script-src 'self';") and "unsafe-inline" not in page and "sha256-" not in page


class EmptyEmailProxy:
    name = "cloudflare"

    def email(self, headers):
        return ""                       # a service token's assertion: signed, but no person


def test_a_verified_assertion_without_an_email_is_not_a_person(api):
    auth = api.app.state.auth
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('noemail','No Email','')")
    auth.proxy, auth.owner_email, auth._owner_id = EmptyEmailProxy(), "", ""
    assert api.get("/api/v2/me").status_code == 401


def test_botops_borrows_only_the_rights_of_the_request_it_is_working_on(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,state) VALUES('botops','BotOps','active')")
        c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES('botops',?, 'ana')",
                  (encode({"name": "botops", "runtime": "fake", "status": "active"}),))
    # The run is handling the owner's room; the owner's request in another thread is not its to cite.
    _, _, botops = setup_attempt(api, "botops")
    from backend.store import H
    with api.app.state.store.transaction() as c:
        elsewhere = H.open_conversation(c, "human:ana", ["human:ana", "bot:botops"], kind="chat", subject="x")
        other = H.say(c, "human:ana", "bot:botops", "Rename Ops", conversation_id=elsewhere["id"])
    body = {"display_name": "Operations", "expected_revision": 1}
    post(api, "bots/ops/definition", {**body, "on_behalf_of": other["id"]}, botops["token"], expected=403)
    here = post(api, "chat/botops", {"text": "Rename Ops"})
    # The request in the current conversation is its to cite with the Owner’s management rights.
    post(api, "bots/ops/definition", {**body, "on_behalf_of": here["id"]}, botops["token"])


def test_a_server_runner_credential_does_not_read_as_its_operator(api):
    from backend.tests.test_api import headers, runner
    machine = runner(api)                               # enrolled as "test", i.e. not a Mac
    reply = api.post("/api/v2/sql", json={"sql": "SELECT count(*) FROM conversations"},
                     headers=headers(machine["token"]))
    assert reply.status_code == 403
    post(api, "runners/heartbeat", {"version": "test", "platform": "darwin", "readiness": {}},
         token=machine["token"])
    reply = api.post("/api/v2/sql", json={"sql": "SELECT count(*) FROM conversations"},
                     headers=headers(machine["token"]))
    assert reply.status_code == 403                     # a heartbeat cannot claim to be a Mac


def test_a_person_reads_and_books_only_calendars_they_may_see(api):
    get_ = lambda cal, token: api.get("/api/v2/calendar/appointments", params={"calendar": cal},
                                      headers={"Authorization": "Bearer " + token})
    assert get_("ana@acme.example", "ben-test").status_code == 403
    assert get_("ben@acme.example", "ben-test").status_code == 200
    assert get_("ben@acme.example", "ana-test").status_code == 200
    booking = {"calendar": "ana@acme.example", "title": "x", "start": "2099-01-01T09:00:00+00:00",
               "end": "2099-01-01T09:30:00+00:00", "attendees": ["someone@evil.example"]}
    post(api, "calendar/appointments", booking, token="ben-test", expected=403)


def test_leaving_revokes_a_persons_api_tokens(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO human_tokens(id,human,label,token_hash,created,created_by) "
                  "VALUES('t1','cara','x','h','2026-01-01T00:00:00Z','human:cara')")
    post(api, "people/cara", {"left": True})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT revoked_at FROM human_tokens WHERE id='t1'").fetchone()[0]


def test_a_quarantine_is_not_lifted_through_the_bot_definition(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='quarantined' WHERE slug='ops'")
        revision = c.execute("SELECT revision FROM bot_config WHERE bot='ops'").fetchone()[0]
    reply = post(api, "bots/ops/definition", {"status": "active", "expected_revision": revision}, expected=409)
    assert reply["error"]["code"] == "quarantined"


def test_an_importer_machine_cannot_send_work_in_a_persons_name(api):
    from backend.tests.test_api import headers, runner
    machine = runner(api)
    body = {"title": "Filed", "transcript": "Dana: hello there.", "source": "granola", "external_id": "g-9",
            "owner_email": "ben@acme.example", "send_to": "ops"}
    reply = api.post("/api/v2/meetings/import", json=body, headers=headers(machine["token"]))
    assert reply.status_code == 403
