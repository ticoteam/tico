"""Tools > Settings > Email > Connect Google: a person connects their own mailbox through a Google app the server uses.

Tico's shared app (the server's environment) asks for read only. The company's own app (Tools) also asks for the act
scopes. Google sends the browser back to the server, which trades the code for the grant at once and keeps the refresh
token sealed; every token for the mailbox then comes from the server (backend/google_tokens.py). No computer holds it.
No Workspace admin, no service account, no domain-wide delegation, and nobody connects anyone else's mailbox.
Google is never called: `post_token` is replaced.
"""

import importlib
import json
import sqlite3
import time

import pytest

from backend import google_connect, google_tokens
from backend import google_workspace as W
from backend.auth import Identity
from backend.credentials import SEALED
from backend.store import Problem
from backend.tests.helpers.api import get, headers, post, runner, setup_attempt  # noqa: F401
from backend.tests.helpers.google_connect import (
    CLIENT,
    FakeGoogle,
    app,
    back,
    door,
    grant_answer,
    own_app,
    signed_in,
    start,
)
from connectors.mail import oauth
from runner_support.connectors import ConnectorPublisher, failure_reason

G = google_connect.G
BOX = "cara@acme.example"


def row(api, person="cara"):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM google_connects WHERE person_id=?", (person,)).fetchone())


def authority(api, box=BOX):
    with api.app.state.store.read() as c:
        return W.authority(c, box)


def test_without_tico_s_google_app_connect_google_says_so(api):
    assert get(api, "mail/google", "cara-test")["configured"] is False
    r = api.post("/api/v2/mail/google/connect", json={}, headers=headers("cara-test"))
    assert r.status_code == 409 and "TICO_GOOGLE_OAUTH_CLIENT_ID" in r.text


def test_a_person_gets_google_s_read_only_sign_in_for_their_own_mailbox(app):
    answer, query = start(app)
    assert answer["mailbox"] == BOX
    assert query["client_id"] == CLIENT and query["login_hint"] == BOX
    assert query["redirect_uri"] == app.app.state.store.settings.public_url + "/api/v2/mail/google/callback"
    assert query["scope"].split() == google_connect.READ_SCOPES
    assert query["access_type"] == "offline" and query["code_challenge_method"] == "S256"
    assert "tico-secret" not in answer["auth_url"]
    assert get(app, "mail/google", "cara-test")["status"] == "signing_in"


def test_only_the_person_themself_connects(app):
    assert app.post("/api/v2/mail/google/connect", json={}, headers=headers("inbox-test")).status_code == 403
    assert app.post("/api/v2/mail/google/disconnect", json={}, headers=headers("inbox-test")).status_code == 403
    machine = runner(app)
    assert app.post("/api/v2/mail/google/connect", json={}, headers=headers(machine["token"])).status_code == 403


def test_nobody_acting_for_the_person_connects_or_disconnects(app):
    settings = app.app.state.store.settings
    settings.test_identities["cara-assistant"] = Identity("human:cara", "human", BOX, via="assistant")
    machine, _, attempt = setup_attempt(app, "ops")
    settings.test_identities["cara-botops"] = Identity(
        "human:cara", "human", BOX, task_actor="bot:ops", runner_id=machine["runner_id"], attempt_id=attempt["id"]
    )
    for token in ("cara-assistant", "cara-botops"):
        for path in ("connect", "disconnect"):
            r = app.post("/api/v2/mail/google/" + path, json={}, headers=headers(token))
            assert r.status_code == 403, (token, path, r.text)
    with app.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM google_connects").fetchone()[0] == 0


def test_the_server_trades_the_code_once_with_pkce_and_keeps_the_grant_sealed(app, monkeypatch):
    google = FakeGoogle(grant_answer(scope="openid " + G + "gmail.readonly"))
    monkeypatch.setattr(google_connect, "post_token", google)
    _, query = start(app)
    # Someone else's browser coming back with Cara's state, or a forged state, changes nothing.
    assert back(app, "ben-test", state=query["state"], code="c-1") == google_connect.INBOX
    assert back(app, state="forged", code="c-1") == google_connect.INBOX
    assert google.calls == [] and row(app)["status"] == "signing_in"
    assert back(app, state=query["state"], code="c-1") == google_connect.INBOX
    sent = google.calls[0]
    assert sent["grant_type"] == "authorization_code" and sent["code"] == "c-1" and sent["code_verifier"]
    assert sent["client_id"] == CLIENT and sent["client_secret"] == "tico-secret"
    assert sent["redirect_uri"].endswith("/api/v2/mail/google/callback")
    assert back(app, state=query["state"], code="c-2") == google_connect.INBOX  # the state is spent
    assert len(google.calls) == 1
    stored = row(app)
    assert stored["status"] == "connected" and stored["oauth_state"] is None and stored["verifier"] is None
    assert stored["grant_sealed"].startswith(SEALED) and "1//grant" not in stored["grant_sealed"]
    assert stored["grant_operation_id"] == stored["operation_id"] and stored["client_id"] == CLIENT
    assert json.loads(stored["grant_scopes"]) == sorted(["openid", G + "gmail.readonly"])
    seen = get(app, "mail/google", "cara-test")
    assert seen["status"] == "connected" and seen["can"] == ["read mail"]
    assert authority(app) == "personal"
    with app.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='mail.google.connected'").fetchone()[0] == 1


def test_the_sealed_grant_opens_only_under_its_own_row(app, monkeypatch):
    signed_in(app)
    signed_in(app, "ben-test", refresh_token="1//ben", email="ben@acme.example")
    cara, ben = row(app), row(app, "ben")
    with app.app.state.store.read() as c:
        assert google_tokens.unseal(c, cara) == "1//grant"
        assert google_tokens.unseal(c, ben) == "1//ben"
        # Cara's ciphertext copied onto Ben's row opens nowhere: the context names the person and the connect.
        with pytest.raises(Problem) as copied:
            google_tokens.unseal(c, dict(ben, grant_sealed=cara["grant_sealed"]))
        assert copied.value.code == "vault_unavailable"
        with pytest.raises(Problem):
            google_tokens.unseal(c, dict(cara, grant_operation_id="gc_other"))
        with pytest.raises(Problem):  # a plaintext token in the column is never used
            google_tokens.unseal(c, dict(cara, grant_sealed="1//grant"))


def test_no_token_appears_in_a_response_a_log_an_event_the_database_or_tico_sql(app, monkeypatch, caplog, capsys):
    google = signed_in(app, access_token="ya29.first")
    google.answer = {"access_token": "ya29.fresh", "expires_in": 3599, "scope": G + "gmail.readonly"}
    monkeypatch.setattr(google_connect, "post_token", google)
    responses = [app.get("/api/v2/mail/google", headers=headers("cara-test")).text]
    assert door(app)["access_token"] == "ya29.fresh"  # the server's own callers get it, nobody else
    responses.append(app.post("/api/v2/sql", json={"sql": "SELECT * FROM google_connects"}, headers=headers()).text)
    assert "prohibited" in responses[-1]
    responses.append(app.get("/api/v2/google", headers=headers()).text)
    responses.append(app.post("/api/v2/mail/google/disconnect", json={}, headers=headers("cara-test")).text)
    with app.app.state.store.read() as c:
        dump = "\n".join(c.iterdump())
    assert 'INSERT INTO "google_connects"' in dump
    for secret in ("1//grant", "ya29.first", "ya29.fresh", "tico-secret"):
        assert secret not in dump, secret
        assert secret not in caplog.text, secret
        assert secret not in "".join(capsys.readouterr()), secret
        assert not any(secret in text for text in responses), secret
    assert google.calls[-1] == {"token": "1//grant"}  # the disconnect revoked it at Google


def test_the_shared_app_refuses_an_answer_with_the_act_scopes_and_revokes_it(app):
    google = signed_in(
        app, refresh_token="1//wide", scope=" ".join(google_connect.READ_SCOPES + google_connect.ACT_SCOPES)
    )
    assert google.uris == [google_connect.TOKEN_URI, google_connect.REVOKE_URI]
    assert google.calls[-1] == {"token": "1//wide"}
    seen = get(app, "mail/google", "cara-test")
    assert seen["status"] == "failed" and seen["kind"] == "scope"
    assert row(app)["grant_sealed"] is None and authority(app) != "personal"


def test_an_answer_naming_the_identity_scopes_in_full_is_kept(app):
    google = signed_in(app, scope=" ".join(["openid", G + "userinfo.email", G + "gmail.readonly"]))
    assert google.uris == [google_connect.TOKEN_URI]
    assert json.loads(row(app)["grant_scopes"]) == sorted(["openid", G + "gmail.readonly"])


@pytest.mark.parametrize(
    "answer, kind, revoked",
    [
        ({"email": "ben@acme.example"}, "identity_mismatch", True),
        ({"refresh_token": None}, "no_refresh_token", False),
        ({"scope": "openid " + G + "calendar.freebusy"}, "no_gmail", True),
    ],
)
def test_a_grant_that_is_not_the_mailbox_s_own_reading_grant_is_not_kept(app, answer, kind, revoked):
    google = signed_in(app, **answer)
    assert (google_connect.REVOKE_URI in google.uris) is revoked
    seen = get(app, "mail/google", "cara-test")
    assert seen["status"] == "failed" and seen["kind"] == kind and seen["message"] and seen["hint"]
    assert row(app)["grant_sealed"] is None


def test_a_refused_exchange_names_google_s_reason_and_a_workspace_policy_as_the_admin_s(app):
    signed_in(app, google=FakeGoogle(error="invalid_grant"))
    assert get(app, "mail/google", "cara-test")["kind"] == "code_rejected"
    signed_in(app, google=FakeGoogle(error=OSError("unreachable")))
    assert get(app, "mail/google", "cara-test")["kind"] == "network"
    _, query = start(app)
    back(app, state=query["state"], error="admin_policy_enforced")
    seen = get(app, "mail/google", "cara-test")
    assert seen["status"] == "failed" and seen["kind"] == "admin_policy"
    assert "Google Workspace for acme.example has a policy that blocks Tico" in seen["message"]
    _, query = start(app)
    back(app, state=query["state"], error="access_denied")
    seen = get(app, "mail/google", "cara-test")
    assert seen["kind"] == "declined" and "policy" not in seen["message"]


def test_the_company_s_own_app_signs_in_for_the_act_scopes_and_keeps_what_google_gave(own_app):
    _, query = start(own_app)
    assert query["client_id"] == "own-client"
    assert query["scope"].split() == google_connect.READ_SCOPES + google_connect.ACT_SCOPES
    signed_in(own_app, scope=" ".join(google_connect.READ_SCOPES + [G + "calendar.events"]))
    seen = get(own_app, "mail/google", "cara-test")
    assert seen["acts"] == {"mail": False, "calendar": True}
    assert seen["message"] == google_connect.UPGRADE["mail"][0]
    assert row(own_app)["client_id"] == "own-client"


def test_a_refresh_asks_only_for_scopes_the_grant_holds_and_a_read_only_grant_refuses_writes(own_app, monkeypatch):
    google = signed_in(own_app, scope=" ".join(google_connect.READ_SCOPES))
    monkeypatch.setattr(google_connect, "post_token", google)
    google.answer = {"access_token": "ya29.read", "expires_in": 3599, "scope": G + "gmail.readonly"}
    assert door(own_app)["scopes"] == [G + "gmail.readonly"]
    sent = google.calls[-1]
    assert sent == {
        "grant_type": "refresh_token",
        "refresh_token": "1//grant",
        "scope": G + "gmail.readonly",
        "client_id": "own-client",
        "client_secret": "own-secret",
    }
    asked = len(google.calls)
    for service in ("gmail", "calendar"):
        with pytest.raises(Problem) as refused:
            door(own_app, service=service, scopes="write")
        assert refused.value.code == "read_only" and "read only" in refused.value.detail
    assert len(google.calls) == asked  # refused before Google is called
    google.answer = {"access_token": "ya29.cal", "expires_in": 3599}
    door(own_app, service="calendar")
    assert google.calls[-1]["scope"].split() == sorted(google_connect.SERVICE_SCOPES["calendar"])


def test_an_act_grant_writes_with_only_its_act_scope_and_what_tico_may_do_narrows_it(own_app, monkeypatch):
    google = signed_in(own_app, scope=" ".join(google_connect.READ_SCOPES + [G + "gmail.modify"]))
    monkeypatch.setattr(google_connect, "post_token", google)
    google.answer = {"access_token": "ya29.write", "expires_in": 3599, "scope": G + "gmail.modify"}
    assert door(own_app, scopes="write")["access_token"] == "ya29.write"
    assert google.calls[-1]["scope"] == G + "gmail.modify"
    with pytest.raises(Problem) as refused:
        door(own_app, service="calendar", scopes="write")  # the grant has no calendar events
    assert refused.value.code == "read_only"
    with own_app.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO registry_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (W.PERMISSIONS_KEY, json.dumps({"organize_mail": False, "send_mail": False, "change_calendar": True})),
        )
    asked = len(google.calls)
    with pytest.raises(Problem) as off:
        door(own_app, scopes="write")
    assert off.value.code == "read_only" and "What Tico may do" in off.value.detail and len(google.calls) == asked


def test_an_answer_wider_than_asked_and_a_refused_refresh_give_no_token(app, monkeypatch):
    google = signed_in(app)
    monkeypatch.setattr(google_connect, "post_token", google)
    google.answer = {
        "access_token": "ya29.wide",
        "expires_in": 3599,
        "scope": G + "gmail.readonly " + G + "gmail.modify",
    }
    with pytest.raises(Problem) as wide:
        door(app)
    assert "ya29.wide" not in wide.value.detail
    # The over-wide grant is ended at Google, and the connection asks the person to connect again.
    assert google.uris[-1] == google_connect.REVOKE_URI and google.calls[-1] == {"token": "1//grant"}
    seen = get(app, "mail/google", "cara-test")
    assert seen["status"] == "failed" and seen["kind"] == "revoked" and seen["message"]
    assert row(app)["grant_sealed"] is None and authority(app) != "personal"
    signed_in(app)  # connected again for the refusals below
    for error, kind, status in (("invalid_grant", "signin", 403), ("admin_policy_enforced", "admin_policy", 403)):
        monkeypatch.setattr(google_connect, "post_token", FakeGoogle(error=error))
        with pytest.raises(Problem) as refused:
            door(app, service="calendar")
        assert refused.value.extra["kind"] == kind and refused.value.status == status
        assert "1//grant" not in refused.value.detail
    monkeypatch.setattr(google_connect, "post_token", FakeGoogle(error=OSError("down")))
    with pytest.raises(Problem) as down:
        door(app, service="calendar")
    assert down.value.status == 503 and down.value.retryable


def test_tokens_are_cached_per_grant_and_a_new_grant_or_app_never_reuses_one(app, monkeypatch):
    google = signed_in(app)
    monkeypatch.setattr(google_connect, "post_token", google)
    google.answer = {"access_token": "ya29.one", "expires_in": 3599}
    assert door(app)["access_token"] == door(app)["access_token"] == "ya29.one"
    asked = len(google.calls)
    assert asked == 2  # the exchange, then one refresh
    signed_in(app, refresh_token="1//second")  # a reconnect
    google.answer = {"access_token": "ya29.two", "expires_in": 3599}
    assert door(app)["access_token"] == "ya29.two" and google.calls[-1]["refresh_token"] == "1//second"
    with app.app.state.store.transaction() as c:
        c.execute("UPDATE google_connects SET client_id='another-client' WHERE person_id='cara'")
    with pytest.raises(Problem) as moved:
        door(app)
    assert moved.value.extra["kind"] == "reconnect"


def test_a_connect_for_another_mailbox_ends_the_old_grant_and_never_serves_it_for_the_new_one(app, monkeypatch):
    signed_in(app)
    real = google_connect.mailbox_for
    monkeypatch.setattr(google_connect, "mailbox_for", lambda c, pid: ("cara.new@acme.example", real(c, pid)[1]))
    google = FakeGoogle({"access_token": "ya29.old", "expires_in": 3599})
    monkeypatch.setattr(google_connect, "post_token", google)
    start(app)  # her mailbox is another one now: she starts connecting it
    assert google.calls == [{"token": "1//grant"}] and google.uris == [google_connect.REVOKE_URI]
    stored = row(app)
    assert stored["mailbox"] == "cara.new@acme.example" and stored["grant_sealed"] is None
    for box in (BOX, "cara.new@acme.example"):
        assert authority(app, box) != "personal"
        with pytest.raises(Problem):
            door(app, box)


def test_a_grant_opens_only_for_the_mailbox_it_was_issued_for(app):
    signed_in(app)
    cara = row(app)
    with app.app.state.store.transaction() as c:  # the mailbox column changed under a live grant
        c.execute("UPDATE google_connects SET mailbox='cara.new@acme.example' WHERE person_id='cara'")
    with pytest.raises(Problem):
        door(app, "cara.new@acme.example")
    with app.app.state.store.read() as c:
        assert google_tokens.unseal(c, cara) == "1//grant"
        with pytest.raises(Problem):
            google_tokens.unseal(c, dict(cara, mailbox="cara.new@acme.example"))


def test_a_failed_reconnect_keeps_the_earlier_grant(app, monkeypatch):
    signed_in(app)
    signed_in(app, refresh_token="1//stray", email="ben@acme.example")  # another account signed in
    assert get(app, "mail/google", "cara-test")["kind"] == "identity_mismatch"
    assert authority(app) == "personal"
    google = FakeGoogle({"access_token": "ya29.kept", "expires_in": 3599})
    monkeypatch.setattr(google_connect, "post_token", google)
    door(app)
    assert google.calls[-1]["refresh_token"] == "1//grant"


class DisconnectsMeanwhile(FakeGoogle):
    """Google answers the exchange only after the person has pressed Disconnect."""

    def __init__(self, api, answer):
        super().__init__(answer)
        self.api = api

    def __call__(self, fields, uri=google_connect.TOKEN_URI):
        if fields.get("grant_type") == "authorization_code":
            post(self.api, "mail/google/disconnect", {}, "cara-test")
        return super().__call__(fields, uri)


def test_a_disconnect_during_the_first_exchange_revokes_the_grant_google_then_gives(app):
    google = signed_in(app, google=DisconnectsMeanwhile(app, grant_answer(refresh_token="1//late")))
    assert google.uris == [google_connect.TOKEN_URI, google_connect.REVOKE_URI]
    assert google.calls[-1] == {"token": "1//late"}
    stored = row(app)
    assert stored["status"] == "disconnected" and stored["grant_sealed"] is None


class ConnectsAgainMeanwhile(FakeGoogle):
    """Google answers the exchange only after the person has started another connect."""

    def __init__(self, api, answer):
        super().__init__(answer)
        self.api = api

    def __call__(self, fields, uri=google_connect.TOKEN_URI):
        if fields.get("grant_type") == "authorization_code":
            start(self.api)
        return super().__call__(fields, uri)


def test_a_first_connect_replaced_during_its_exchange_revokes_the_losing_grant(app):
    google = signed_in(app, google=ConnectsAgainMeanwhile(app, grant_answer(refresh_token="1//late")))
    assert google.uris == [google_connect.TOKEN_URI, google_connect.REVOKE_URI]
    assert google.calls[-1] == {"token": "1//late"}
    assert row(app)["status"] == "signing_in" and row(app)["grant_sealed"] is None


def test_an_exchange_answered_after_its_connect_was_replaced_keeps_nothing_and_revokes_nothing(app, caplog):
    signed_in(app)
    google = signed_in(app, google=ConnectsAgainMeanwhile(app, grant_answer(refresh_token="1//late")))
    assert google.uris == [google_connect.TOKEN_URI]  # not revoked: that would end the grant still connected
    assert row(app)["status"] == "signing_in"
    with app.app.state.store.read() as c:
        assert google_tokens.unseal(c, row(app)) == "1//grant"
    assert "is no longer current" in caplog.text and "1//late" not in caplog.text


def test_disconnect_forgets_the_grant_and_revokes_it_at_google(app, monkeypatch):
    signed_in(app)
    google = FakeGoogle({})
    monkeypatch.setattr(google_connect, "post_token", google)
    seen = post(app, "mail/google/disconnect", {}, "cara-test")
    assert seen["status"] == "disconnected" and seen["message"] is None
    assert google.uris == [google_connect.REVOKE_URI] and google.calls == [{"token": "1//grant"}]
    stored = row(app)
    assert stored["grant_sealed"] is None and stored["grant_scopes"] is None and stored["grant_operation_id"] is None
    assert authority(app) != "personal"
    with pytest.raises(Problem):
        door(app)
    # Google out of reach: the grant is forgotten all the same, and the person is told to remove Tico there.
    signed_in(app)
    monkeypatch.setattr(google_connect, "post_token", FakeGoogle(error=OSError("unreachable")))
    seen = post(app, "mail/google/disconnect", {}, "cara-test")
    assert seen["kind"] == "not_revoked" and "myaccount.google.com/permissions" in seen["hint"]
    assert row(app)["grant_sealed"] is None


def test_a_sign_in_nobody_finishes_expires_and_an_exchange_that_never_ended_fails_keeping_the_grant(app):
    _, query = start(app)
    with app.app.state.store.transaction() as c:
        c.execute("UPDATE google_connects SET started_at=? WHERE person_id='cara'", (time.time() - 3600,))
    assert back(app, state=query["state"], code="c-1") == google_connect.INBOX
    seen = get(app, "mail/google", "cara-test")
    assert seen["kind"] == "expired" and row(app)["verifier"] is None
    signed_in(app)
    with app.app.state.store.transaction() as c:
        c.execute(
            "UPDATE google_connects SET status='finishing',started_at=? WHERE person_id='cara'", (time.time() - 3600,)
        )
    assert get(app, "mail/google", "cara-test")["kind"] == "unknown"
    assert row(app)["grant_sealed"] is not None and authority(app) == "personal"


def test_the_computer_no_longer_finishes_sign_ins(app):
    signed_in(app)
    machine = runner(app)
    for path in ("claim", "token", "gc_x/exchange", "gc_x/result"):
        r = app.post("/api/v2/connectors/mail/google/" + path, json={}, headers=headers(machine["token"]))
        assert r.status_code in (404, 405), (path, r.status_code)
    assert get(app, "runners/connectors", machine["token"])["connectors"] in ([], ["mail", "calendar"])


def test_the_migration_ends_computer_held_grants_with_one_reconnect_and_drops_the_handoff():
    migration = importlib.import_module("backend.migrations.20261116145802_google_grants_on_server")
    conn = sqlite3.connect(":memory:")
    migration.up(conn)  # a new file has no table yet: the module's CREATE makes the new shape
    conn.execute("CREATE TABLE google_mail_holders(runner_id TEXT PRIMARY KEY, holds INTEGER NOT NULL, seen_at REAL)")
    conn.execute(
        "CREATE TABLE google_connects(person_id TEXT PRIMARY KEY, mailbox TEXT NOT NULL, status TEXT NOT NULL, "
        "oauth_state TEXT, verifier TEXT, code TEXT, kind TEXT, message TEXT, hint TEXT, can TEXT, runner_id TEXT, "
        "holder TEXT, client_id TEXT, started_at REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "operation_id TEXT, answered TEXT, pending_hash TEXT, grant_hash TEXT, pending_scopes TEXT, grant_scopes TEXT)"
    )
    rows = [
        ("cara", "connected", "h1"),  # a computer holds her grant
        ("ben", "failed", "h2"),  # a failed reconnect, the earlier grant on a computer
        ("dan", "disconnecting", "h3"),  # a disconnect queued for its computer
        ("eve", "ready", None),  # a code waiting for a computer
        ("fay", "signing_in", None),  # still at Google: finishes on the server now
        ("gus", "failed", None),
    ]
    for person, status, grant in rows:
        conn.execute(
            "INSERT INTO google_connects(person_id,mailbox,status,grant_hash,holder,grant_scopes,client_id,created_at,"
            "updated_at) VALUES(?,?,?,?,?,?,?,'t','t')",
            (person, person + "@acme.example", status, grant, "r1" if grant else None, "[]" if grant else None, "x"),
        )
    migration.up(conn)
    found = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT person_id,status,kind FROM google_connects")}
    assert found == {
        "cara": ("disconnected", "reconnect"),
        "ben": ("disconnected", "reconnect"),
        "dan": ("disconnected", None),
        "eve": ("failed", "expired"),
        "fay": ("signing_in", None),
        "gus": ("failed", None),
    }
    columns = {r[1] for r in conn.execute("PRAGMA table_info(google_connects)")}
    assert {"grant_sealed", "grant_operation_id"} <= columns
    assert not columns & set(migration.GONE)
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='google_mail_holders'").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM google_connects WHERE grant_scopes IS NOT NULL").fetchone()[0] == 0
    migration.up(conn)  # a second run changes nothing


def test_a_connection_the_migration_ended_asks_once_to_connect_again(app):
    with app.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO google_connects(person_id,mailbox,status,kind,created_at,updated_at) "
            "VALUES('cara',?,'disconnected','reconnect','t','t')",
            (BOX,),
        )
    seen = get(app, "mail/google", "cara-test")
    assert seen["status"] == "disconnected" and seen["kind"] == "reconnect"
    assert seen["message"] == google_connect.ENDED["reconnect"][0]
    signed_in(app)
    assert get(app, "mail/google", "cara-test")["message"] is None


def test_admin_policy_at_refresh_reaches_health_as_the_admin_s():
    assert (
        failure_reason(
            RuntimeError(
                "Local mail lookup failed: error: the Google Workspace for acme.example has a policy that blocks Tico"
            )
        )
        == "admin_policy"
    )
    assert failure_reason(RuntimeError("error: invalid_grant")) == "signin"


def test_health_names_a_policy_block_as_the_workspace_admin_s(app):
    machine = runner(app)
    post(
        app,
        "connectors/health",
        {"service": "mail", "failing": [{"account": "cara@acme.example", "reason": "admin_policy"}]},
        machine["token"],
    )
    with app.app.state.store.read() as c:
        found = c.execute("SELECT last_error FROM service_health WHERE service='connector:mail'").fetchone()
    assert "Google Workspace for acme.example has a policy that blocks Tico" in found["last_error"]


def mail_script(tmp_path, body):
    script = tmp_path / "hub/scripts/mail.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    return script


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
    publisher = ConnectorPublisher({"url": "http://localhost:8765", "token": "test", "owner_handle": "ana"}, client=hub)
    publisher.script = tmp_path / "hub/scripts/mail.sh"
    assert publisher.calendar_action_tick() == 1
    path, body = hub.results[0]
    assert path == "connectors/calendar/actions/act-1/result" and body["status"] == "failed"
    assert "refused" in body["error"]


def test_the_mail_cli_scopes_match_the_server_s():
    assert google_connect.READ_SCOPES == oauth.IDENTITY_SCOPES + oauth.READ_SCOPES
