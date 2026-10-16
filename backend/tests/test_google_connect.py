"""Settings > Inbox > Connect Google: a person connects their own mailbox, read only, through Tico's own Google app.

The browser comes back to the server; the computer that syncs mail finishes the sign-in and keeps the grant.
No Workspace admin, no service account, no domain-wide delegation, and nobody connects anyone else's mailbox.
"""

import json
import subprocess
import time
import urllib.parse

import pytest

from backend import google_connect
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, get, headers, post, runner, setup_attempt  # noqa: F401
from connectors.mail import oauth
from runner.connectors import ConnectorPublisher, failure_reason

CLIENT = "1234-tico.apps.googleusercontent.com"


@pytest.fixture
def app(api):
    settings = api.app.state.store.settings
    settings.google_oauth_client_id, settings.google_oauth_client_secret = CLIENT, "tico-secret"
    with api.app.state.store.transaction() as c:      # a signed-in agent, as a Hermes bot is
        c.execute("INSERT INTO agents(bot,harness,token_hash,created,created_by) VALUES('inbox','hermes','x',?,"
                  "'human:ana')", (H.now(),))
    settings.test_identities["inbox-test"] = Identity("bot:inbox", "bot", agent="hermes")
    return api


def start(api, token="cara-test"):
    answer = post(api, "mail/google/connect", {}, token)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(answer["auth_url"]).query)
    return answer, {k: v[0] for k, v in query.items()}


def back(api, token="cara-test", **query):
    r = api.get("/api/v2/mail/google/callback?" + urllib.parse.urlencode(query), headers=headers(token),
                follow_redirects=False)
    assert r.status_code == 302, r.text
    return r.headers["location"]


def row(api, person="cara"):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM google_connects WHERE person_id=?", (person,)).fetchone())


def test_the_scopes_are_the_connectors_read_only_scopes():
    assert google_connect.READ_SCOPES == oauth.IDENTITY_SCOPES + oauth.READ_SCOPES
    assert not set(google_connect.READ_SCOPES) & set().union(*oauth.CAPABILITIES.values())


def test_without_tico_s_google_app_connect_google_says_so(api):
    assert get(api, "mail/google", "cara-test")["configured"] is False
    r = api.post("/api/v2/mail/google/connect", json={}, headers=headers("cara-test"))
    assert r.status_code == 409 and "TICO_GOOGLE_OAUTH_CLIENT_ID" in r.text


def test_a_person_gets_google_s_read_only_sign_in_for_their_own_mailbox(app):
    answer, query = start(app)
    assert answer["mailbox"] == "cara@acme.example"
    assert query["client_id"] == CLIENT and query["login_hint"] == "cara@acme.example"
    assert query["redirect_uri"] == app.app.state.store.settings.public_url + "/api/v2/mail/google/callback"
    assert query["scope"].split() == google_connect.READ_SCOPES
    assert "gmail.modify" not in query["scope"] and "auth/calendar " not in query["scope"] + " "
    assert query["access_type"] == "offline" and query["code_challenge_method"] == "S256"
    assert "tico-secret" not in answer["auth_url"]
    assert get(app, "mail/google", "cara-test")["status"] == "signing_in"


def test_only_the_person_themself_connects(app):
    r = app.post("/api/v2/mail/google/connect", json={}, headers=headers("inbox-test"))
    assert r.status_code == 403
    r = app.post("/api/v2/mail/google/disconnect", json={}, headers=headers("inbox-test"))
    assert r.status_code == 403
    machine = runner(app)
    assert app.post("/api/v2/mail/google/connect", json={}, headers=headers(machine["token"])).status_code == 403


def test_the_callback_takes_only_the_same_person_s_open_state_once(app):
    _, query = start(app)
    # Someone else's browser coming back with Cara's state changes nothing.
    assert back(app, "ben-test", state=query["state"], code="c-1") == google_connect.INBOX
    assert row(app)["status"] == "signing_in" and row(app)["code"] is None
    assert back(app, state="forged", code="c-1") == google_connect.INBOX
    assert back(app, state=query["state"], code="c-1") == google_connect.INBOX
    assert row(app)["status"] == "ready" and row(app)["state"] is None
    assert back(app, state=query["state"], code="c-2") == google_connect.INBOX     # spent
    assert row(app)["code"] == "c-1"


def test_a_workspace_policy_denial_is_named_as_the_admin_s(app):
    _, query = start(app)
    assert back(app, state=query["state"], error="admin_policy_enforced") == google_connect.INBOX
    seen = get(app, "mail/google", "cara-test")
    assert seen["status"] == "failed" and seen["kind"] == "admin_policy"
    assert "Google Workspace for acme.example has a policy that blocks Tico" in seen["message"]
    assert "did not ask for more access" in seen["message"] and "Admin console" in seen["hint"]
    assert row(app)["verifier"] is None
    machine = runner(app)
    assert post(app, "connectors/mail/google/claim", {}, machine["token"])["connect"] is None


def test_declining_on_google_s_page_is_not_called_a_policy(app):
    _, query = start(app)
    back(app, state=query["state"], error="access_denied")
    seen = get(app, "mail/google", "cara-test")
    assert seen["kind"] == "declined" and "policy" not in seen["message"]


def test_the_mail_computer_finishes_it_and_the_code_is_cleared(app):
    _, query = start(app)
    back(app, state=query["state"], code="c-1")
    r = app.post("/api/v2/connectors/mail/google/claim", json={}, headers=headers("cara-test"))
    assert r.status_code == 403                                      # a person never sees the code again
    machine = runner(app)
    with app.app.state.store.transaction() as c:
        assert google_connect.pending(c, machine["runner_id"])
    assert get(app, "runners/connectors", machine["token"])["connectors"] == ["mail"]
    claimed = post(app, "connectors/mail/google/claim", {}, machine["token"])["connect"]
    assert claimed["action"] == "connect" and claimed["mailbox"] == "cara@acme.example"
    assert claimed["code"] == "c-1" and claimed["client_id"] == CLIENT and claimed["client_secret"] == "tico-secret"
    assert claimed["verifier"] and claimed["redirect_uri"].endswith("/api/v2/mail/google/callback")
    assert post(app, "connectors/mail/google/claim", {}, machine["token"])["connect"] is None
    other = runner(app, label="Other Mac")
    r = app.post(f"/api/v2/connectors/mail/google/{claimed['id']}/result", json={"status": "connected"},
                 headers=headers(other["token"]))
    assert r.status_code == 409
    post(app, f"connectors/mail/google/{claimed['id']}/result", {"status": "connected", "can": ["read"]},
         machine["token"])
    seen = get(app, "mail/google", "cara-test")
    assert seen["status"] == "connected" and seen["can"] == ["read"]
    stored = row(app)
    assert stored["code"] is None and stored["verifier"] is None and stored["state"] is None
    assert get(app, "runners/connectors", machine["token"])["connectors"] == []


def test_another_account_signing_in_is_shown_as_refused(app):
    _, query = start(app)
    back(app, state=query["state"], code="c-1")
    machine = runner(app)
    claimed = post(app, "connectors/mail/google/claim", {}, machine["token"])["connect"]
    post(app, f"connectors/mail/google/{claimed['id']}/result",
         {"status": "failed", "kind": "identity_mismatch", "message": "the account that signed in is ben@acme.example, "
          "not cara@acme.example; nothing was stored", "hint": "Sign in to Google as cara@acme.example"},
         machine["token"])
    seen = get(app, "mail/google", "cara-test")
    assert seen["status"] == "failed" and seen["kind"] == "identity_mismatch"
    assert "nothing was stored" in seen["message"] and "cara@acme.example" in seen["hint"]


def test_disconnect_is_queued_for_the_computer_without_the_client(app):
    seen = post(app, "mail/google/disconnect", {}, "cara-test")
    assert seen["status"] == "disconnecting"
    machine = runner(app)
    claimed = post(app, "connectors/mail/google/claim", {}, machine["token"])["connect"]
    assert claimed == {"id": "cara", "action": "disconnect", "mailbox": "cara@acme.example"}
    r = app.post(f"/api/v2/connectors/mail/google/{claimed['id']}/result", json={"status": "connected"},
                 headers=headers(machine["token"]))
    assert r.status_code == 409                                      # a revoke cannot come back as a connect
    post(app, f"connectors/mail/google/{claimed['id']}/result", {"status": "disconnected"}, machine["token"])
    assert get(app, "mail/google", "cara-test")["status"] == "disconnected"


def connected(app, machine, token="cara-test"):
    """A finished connect, taken and answered by `machine`."""
    _, query = start(app, token)
    back(app, token, state=query["state"], code="c-1")
    claimed = post(app, "connectors/mail/google/claim", {"holds_mail": True}, machine["token"])["connect"]
    assert claimed and claimed["action"] == "connect"
    post(app, f"connectors/mail/google/{claimed['id']}/result", {"status": "connected", "can": ["read"]},
         machine["token"])


def test_disconnect_and_reconnect_go_only_to_the_computer_holding_the_grant(app):
    mac, server = runner(app, label="Mac"), runner(app, label="Linux server")
    connected(app, mac)
    post(app, "mail/google/disconnect", {}, "cara-test")
    # The other computer is not told to run the job for it, and can't take it even saying it holds mail.
    assert get(app, "runners/connectors", server["token"])["connectors"] == []
    assert post(app, "connectors/mail/google/claim", {"holds_mail": True}, server["token"])["connect"] is None
    claimed = post(app, "connectors/mail/google/claim", {"holds_mail": True}, mac["token"])["connect"]
    assert claimed["action"] == "disconnect"
    post(app, "connectors/mail/google/cara/result", {"status": "disconnected"}, mac["token"])
    assert row(app)["holder"] is None
    # A reconnect also goes to the holder, and a failed one leaves the earlier grant's holder in place.
    connected(app, mac)
    _, query = start(app)
    back(app, state=query["state"], code="c-2")
    assert post(app, "connectors/mail/google/claim", {"holds_mail": True}, server["token"])["connect"] is None
    claimed = post(app, "connectors/mail/google/claim", {"holds_mail": True}, mac["token"])["connect"]
    post(app, "connectors/mail/google/cara/result", {"status": "failed", "kind": "identity_mismatch"}, mac["token"])
    assert row(app)["holder"] == mac["runner_id"]


def test_a_first_connect_goes_to_the_computer_that_already_holds_mail(app):
    keyed, bare = runner(app, label="Mac with the key"), runner(app, label="Laptop")
    assert post(app, "connectors/mail/google/claim", {"holds_mail": True}, keyed["token"])["connect"] is None
    _, query = start(app)
    back(app, state=query["state"], code="c-1")
    assert get(app, "runners/connectors", bare["token"])["connectors"] == []
    assert post(app, "connectors/mail/google/claim", {}, bare["token"])["connect"] is None
    assert post(app, "connectors/mail/google/claim", {"holds_mail": True}, keyed["token"])["connect"]


def test_a_holder_that_was_removed_no_longer_keeps_the_row(app):
    mac, server = runner(app, label="Mac"), runner(app, label="Linux server")
    connected(app, mac)
    with app.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), mac["runner_id"]))
    post(app, "mail/google/disconnect", {}, "cara-test")
    assert post(app, "connectors/mail/google/claim", {}, server["token"])["connect"]["action"] == "disconnect"


def test_a_disconnect_its_offline_computer_never_takes_says_so_and_stays_queued(app):
    mac = runner(app, label="Mac")
    connected(app, mac)
    post(app, "mail/google/disconnect", {}, "cara-test")
    assert "stalled" not in get(app, "mail/google", "cara-test")
    with app.app.state.store.transaction() as c:
        c.execute("UPDATE google_connects SET started=? WHERE person_id='cara'",
                  (time.time() - google_connect.STALLED_SECONDS - 1,))
    seen = get(app, "mail/google", "cara-test")
    assert seen["status"] == "disconnecting" and seen["stalled"] is True
    assert "hasn't come online" in seen["message"] and "myaccount.google.com/permissions" in seen["hint"]
    # When the computer is back it still revokes.
    assert post(app, "connectors/mail/google/claim", {"holds_mail": True}, mac["token"])["connect"]["action"] == \
        "disconnect"


def test_only_recent_live_computers_keep_a_holder_record(app):
    old, gone, now = runner(app, label="Old"), runner(app, label="Gone"), runner(app, label="Now")
    for machine in (old, gone):
        post(app, "connectors/mail/google/claim", {"holds_mail": True}, machine["token"])
    with app.app.state.store.transaction() as c:
        c.execute("UPDATE google_mail_holders SET seen=? WHERE runner_id=?",
                  (time.time() - google_connect.HOLDER_SECONDS - 1, old["runner_id"]))
        c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), gone["runner_id"]))
    post(app, "connectors/mail/google/claim", {}, now["token"])
    with app.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT runner_id FROM google_mail_holders")] == [now["runner_id"]]


def test_nobody_acting_for_the_person_connects_or_disconnects(app):
    settings = app.app.state.store.settings
    settings.test_identities["cara-assistant"] = Identity("human:cara", "human", "cara@acme.example", via="assistant")
    machine, _, attempt = setup_attempt(app, "ops")
    settings.test_identities["cara-botops"] = Identity("human:cara", "human", "cara@acme.example",
                                                       task_actor="bot:ops", runner_id=machine["runner_id"],
                                                       attempt_id=attempt["id"])
    for token in ("cara-assistant", "cara-botops"):
        for path in ("connect", "disconnect"):
            r = app.post("/api/v2/mail/google/" + path, json={}, headers=headers(token))
            assert r.status_code == 403, (token, path, r.text)
    with app.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM google_connects").fetchone()[0] == 0


def test_a_sign_in_nobody_finishes_expires(app):
    _, query = start(app)
    with app.app.state.store.transaction() as c:
        c.execute("UPDATE google_connects SET started=? WHERE person_id='cara'", (time.time() - 3600,))
    assert back(app, state=query["state"], code="c-1") == google_connect.INBOX
    seen = get(app, "mail/google", "cara-test")
    assert seen["kind"] == "expired" and row(app)["verifier"] is None


def test_admin_policy_at_refresh_reaches_health_as_the_admin_s():
    assert failure_reason(RuntimeError("Local mail lookup failed: error: the Google Workspace for acme.example "
                                       "has a policy that blocks Tico")) == "admin_policy"
    assert failure_reason(RuntimeError("error: invalid_grant")) == "signin"


def test_health_names_a_policy_block_as_the_workspace_admin_s(app):
    machine = runner(app)
    post(app, "connectors/health", {"service": "mail", "failing": [
        {"account": "cara@acme.example", "reason": "admin_policy"}]}, machine["token"])
    with app.app.state.store.read() as c:
        found = c.execute("SELECT last_error FROM service_health WHERE service='connector:mail'").fetchone()
    assert "Google Workspace for acme.example has a policy that blocks Tico" in found["last_error"]


# The computer's side: runner/connectors.py

class Hub:
    def __init__(self, queue):
        self.queue, self.results = list(queue), []

    def post(self, path, body):
        if path == "connectors/mail/google/claim":
            return {"connect": self.queue.pop(0) if self.queue else None}
        self.results.append((path, body))
        return {"ok": True}


def mail_script(tmp_path, body):
    script = tmp_path / "hub/scripts/mail.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    return script


def test_the_computer_finishes_the_sign_in_with_the_code_on_stdin_only(tmp_path):
    seen = tmp_path / "seen"
    mail_script(tmp_path, f'echo "$@" > {seen}.args\ncat > {seen}.stdin\n'
                          'echo \'{"status": "connected", "mailbox": "cara@acme.example", "can": ["read"]}\'\n')
    hub = Hub([{"id": "cara", "action": "connect", "mailbox": "cara@acme.example", "code": "c-1",
                "verifier": "v-1", "redirect_uri": "https://tico.acme.example/api/v2/mail/google/callback",
                "client_id": CLIENT, "client_secret": "tico-secret"}])
    publisher = ConnectorPublisher({"url": "http://localhost:8765", "token": "test", "projects_dir": str(tmp_path)},
                                   client=hub)
    publisher.script = tmp_path / "hub/scripts/mail.sh"
    assert publisher.google_connect_tick() == 1
    assert hub.results == [("connectors/mail/google/cara/result", {"status": "connected", "can": ["read"]})]
    args = (tmp_path / "seen.args").read_text()
    assert args.split() == ["google", "finish"] and "c-1" not in args and "tico-secret" not in args
    assert json.loads((tmp_path / "seen.stdin").read_text())["code"] == "c-1"


def test_a_failed_finish_carries_only_its_kind_and_words(tmp_path):
    mail_script(tmp_path, 'cat > /dev/null\necho \'{"status": "failed", "kind": "admin_policy", "message": "m", '
                          '"hint": "h", "mailbox": "cara@acme.example"}\'\nexit 1\n')
    hub = Hub([{"id": "cara", "action": "connect", "mailbox": "cara@acme.example", "code": "c"}])
    publisher = ConnectorPublisher({"url": "http://localhost:8765", "token": "test"}, client=hub)
    publisher.script = tmp_path / "hub/scripts/mail.sh"
    publisher.google_connect_tick()
    assert hub.results[0][1] == {"status": "failed", "kind": "admin_policy", "message": "m", "hint": "h"}


def test_disconnect_runs_the_revoke(tmp_path):
    seen = tmp_path / "seen"
    mail_script(tmp_path, f'echo "$@" > {seen}\necho \'{{"status": "revoked", "mailbox": "cara@acme.example"}}\'\n')
    hub = Hub([{"id": "cara", "action": "disconnect", "mailbox": "cara@acme.example"}])
    publisher = ConnectorPublisher({"url": "http://localhost:8765", "token": "test"}, client=hub)
    publisher.script = tmp_path / "hub/scripts/mail.sh"
    publisher.google_connect_tick()
    assert seen.read_text().split() == ["google", "revoke", "--mailbox", "cara@acme.example", "--json"]
    assert hub.results[0][1] == {"status": "disconnected"}


def test_a_finish_that_hangs_is_reported_failed(tmp_path):
    def hang(request):
        raise subprocess.TimeoutExpired("mail.sh", 60)
    hub = Hub([{"id": "cara", "action": "connect", "mailbox": "cara@acme.example"}])
    publisher = ConnectorPublisher({"url": "http://localhost:8765", "token": "test"}, client=hub, google=hang)
    publisher.google_connect_tick()
    assert hub.results[0][1]["status"] == "failed" and hub.results[0][1]["kind"] == "network"


def test_a_revoke_with_nothing_here_is_not_called_disconnected(tmp_path):
    mail_script(tmp_path, 'echo \'{"status": "revoked", "mailbox": "cara@acme.example", "absent": true}\'\n')
    hub = Hub([{"id": "cara", "action": "disconnect", "mailbox": "cara@acme.example"}])
    publisher = ConnectorPublisher({"url": "http://localhost:8765", "token": "test"}, client=hub)
    publisher.script = tmp_path / "hub/scripts/mail.sh"
    publisher.google_connect_tick()
    assert hub.results[0][1]["status"] == "failed" and hub.results[0][1]["kind"] == "not_here"


def test_a_calendar_write_refused_before_google_is_posted_failed_not_unknown(tmp_path):
    mail_script(tmp_path, 'cat > /dev/null\necho "refused: connected to Tico read-only at Google" >&2\nexit 2\n')

    class Calendar:
        def __init__(self):
            self.queue, self.results = [{"id": "act-1", "calendar": "cara@acme.example"}], []

        def post(self, path, body):
            if path == "connectors/calendar/actions/claim":
                return {"action": self.queue.pop(0) if self.queue else None}
            self.results.append((path, body))
            return {"ok": True}
    hub = Calendar()
    publisher = ConnectorPublisher({"url": "http://localhost:8765", "token": "test", "owner_handle": "ana"},
                                   client=hub)
    publisher.script = tmp_path / "hub/scripts/mail.sh"
    assert publisher.calendar_action_tick() == 1
    path, body = hub.results[0]
    assert path == "connectors/calendar/actions/act-1/result" and body["status"] == "failed"
    assert "refused" in body["error"]
