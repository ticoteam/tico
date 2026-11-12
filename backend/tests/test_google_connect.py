"""Tools > Settings > Email > Connect Google: a person connects their own mailbox through a Google app the server uses.

Tico's shared app (the server's environment) asks for read only. The company's own app (Tools) also asks for
the act scopes the mail CLI gates labels, drafts and calendar writes on. The browser comes back to the server; the
computer that syncs mail finishes the sign-in and keeps the grant.
No Workspace admin, no service account, no domain-wide delegation, and nobody connects anyone else's mailbox.
"""

import json
import subprocess
import time


from backend import google_connect
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import get, headers, post, runner, setup_attempt  # noqa: F401
from connectors.mail import oauth
from runner_support.connectors import ConnectorPublisher, failure_reason
from backend.tests.helpers.google_connect import (
    CLIENT,
    FakeGoogle,
    app,
    back,
    claimed_connect,
    exchanged,
    own_app,
    refresh,
    start,
)


def row(api, person="cara"):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM google_connects WHERE person_id=?", (person,)).fetchone())


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
    assert row(app)["status"] == "ready" and row(app)["oauth_state"] is None
    assert back(app, state=query["state"], code="c-2") == google_connect.INBOX  # spent
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
    assert r.status_code == 403  # a person never sees the code again
    machine = runner(app)
    with app.app.state.store.transaction() as c:
        assert google_connect.pending(c, machine["runner_id"], named=True)  # the owner's computer publishes
    assert get(app, "runners/connectors", machine["token"])["connectors"] == ["mail"]
    claimed = post(app, "connectors/mail/google/claim", {}, machine["token"])["connect"]
    assert claimed["id"].startswith("gc_")  # this connect, not the person: /exchange and /result answer only it
    assert claimed == {
        "id": claimed["id"],
        "action": "connect",
        "mailbox": "cara@acme.example",
        "client_id": CLIENT,
        "broker": "server",
    }  # no code, no verifier, no secret
    assert post(app, "connectors/mail/google/claim", {}, machine["token"])["connect"] is None
    other = runner(app, label="Other Mac")
    r = app.post(
        f"/api/v2/connectors/mail/google/{claimed['id']}/result",
        json={"status": "connected"},
        headers=headers(other["token"]),
    )
    assert r.status_code == 409
    post(
        app,
        f"connectors/mail/google/{claimed['id']}/result",
        {"status": "connected", "can": ["read"]},
        machine["token"],
    )
    seen = get(app, "mail/google", "cara-test")
    assert seen["status"] == "connected" and seen["can"] == ["read"]
    stored = row(app)
    assert stored["code"] is None and stored["verifier"] is None and stored["oauth_state"] is None
    assert get(app, "runners/connectors", machine["token"])["connectors"] == []


class RevokeDown(FakeGoogle):
    """Google answers the exchange but cannot be reached to revoke the grant it made."""

    def __call__(self, fields, uri=google_connect.TOKEN_URI):
        if uri != google_connect.REVOKE_URI:
            return super().__call__(fields, uri)
        self.calls.append(dict(fields))
        self.uris.append(uri)
        raise OSError("unreachable")


def test_the_server_trades_the_code_once_for_the_computer_that_claimed_it(app, monkeypatch):
    google = FakeGoogle(
        {
            "access_token": "ya29.first",
            "refresh_token": "1//grant",
            "expires_in": 3599,
            "scope": "openid " + oauth.G + "gmail.readonly",
            "id_token": "jwt",
        }
    )
    monkeypatch.setattr(google_connect, "post_token", google)
    machine, other = runner(app), runner(app, label="Other Mac")
    claimed = claimed_connect(app, machine)
    r = app.post(f"/api/v2/connectors/mail/google/{claimed['id']}/exchange", json={}, headers=headers(other["token"]))
    assert r.status_code == 409 and google.calls == []  # only the computer that claimed it
    r = app.post(f"/api/v2/connectors/mail/google/{claimed['id']}/exchange", json={}, headers=headers("cara-test"))
    assert r.status_code == 403  # never a person
    r = app.post(f"/api/v2/connectors/mail/google/{claimed['id']}/exchange", json={}, headers=headers(machine["token"]))
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    assert r.json() == {
        "access_token": "ya29.first",
        "refresh_token": "1//grant",
        "expires_in": 3599,
        "scope": "openid " + oauth.G + "gmail.readonly",
    }
    sent = google.calls[0]
    assert sent["grant_type"] == "authorization_code" and sent["code"] == "c-1" and sent["code_verifier"]
    assert sent["client_id"] == CLIENT and sent["client_secret"] == "tico-secret"
    assert sent["redirect_uri"].endswith("/api/v2/mail/google/callback")
    assert row(app)["code"] is None and row(app)["verifier"] is None
    r = app.post(f"/api/v2/connectors/mail/google/{claimed['id']}/exchange", json={}, headers=headers(machine["token"]))
    assert r.status_code == 409 and len(google.calls) == 1  # the code is spent


def test_a_refused_exchange_names_google_s_reason_only(app, monkeypatch):
    monkeypatch.setattr(google_connect, "post_token", FakeGoogle(error="admin_policy_enforced"))
    machine = runner(app)
    claimed = claimed_connect(app, machine)
    r = app.post(f"/api/v2/connectors/mail/google/{claimed['id']}/exchange", json={}, headers=headers(machine["token"]))
    assert r.status_code == 409 and r.json()["error"]["kind"] == "admin_policy"
    assert "tico-secret" not in r.text and "c-1" not in r.text


def holding(app, monkeypatch, google=None):
    machine = runner(app)
    exchanged(app, machine)
    google = google or FakeGoogle()
    monkeypatch.setattr(google_connect, "post_token", google)
    return machine, google


def test_the_server_refreshes_a_grant_and_returns_only_the_access_token(app, monkeypatch, caplog, capsys):
    machine, google = holding(app, monkeypatch)
    r = refresh(app, machine["token"])
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    answer = r.json()
    assert set(answer) == {"access_token", "expires_in", "expiry", "scopes"}
    assert answer["access_token"] == "ya29.fresh" and answer["expires_in"] == 3599
    assert answer["scopes"] == [oauth.G + "gmail.readonly"] and answer["expiry"].endswith("Z")
    sent = google.calls[-1]
    assert sent == {
        "grant_type": "refresh_token",
        "refresh_token": "1//grant",
        "scope": oauth.G + "gmail.readonly",
        "client_id": CLIENT,
        "client_secret": "tico-secret",
    }
    # Stored nowhere and logged nowhere: not in the database, not in a log line, not on stdout.
    with app.app.state.store.read() as c:
        dump = "\n".join(c.connection.iterdump()) if hasattr(c, "connection") else "\n".join(c.iterdump())
    assert "INSERT INTO \"google_connects\"" in dump
    for secret in ("1//grant", "ya29.fresh"):
        assert secret not in dump
        assert secret not in caplog.text
        assert secret not in "".join(capsys.readouterr())


def test_a_refresh_carries_one_service_s_read_only_scopes_only(app, monkeypatch):
    machine, google = holding(app, monkeypatch)
    for scopes, service in (
        ([oauth.G + "gmail.modify"], "gmail"),
        ([oauth.G + "gmail.compose"], "gmail"),
        ([oauth.G + "calendar.events"], "calendar"),
        (["https://mail.google.com/"], "gmail"),
        ([oauth.G + "gmail.readonly", oauth.G + "calendar.freebusy"], "gmail"),
        ([oauth.G + "gmail.readonly"], "calendar"),
        ([], "gmail"),
    ):
        r = refresh(app, machine["token"], service=service, scopes=scopes)
        assert r.status_code == 422, (scopes, service, r.text)
    assert google.calls == []
    google.answer = {"access_token": "ya29.cal", "expires_in": 3599, "scope": oauth.G + "calendar.freebusy"}
    r = refresh(
        app,
        machine["token"],
        service="calendar",
        scopes=[oauth.G + "calendar.freebusy", oauth.G + "calendar.events.readonly"],
    )
    assert r.status_code == 200 and r.json()["scopes"] == [oauth.G + "calendar.freebusy"]
    assert google.calls[-1]["scope"].split() == sorted(
        [oauth.G + "calendar.events.readonly", oauth.G + "calendar.freebusy"]
    )


def test_only_a_computer_holding_a_grant_from_this_server_may_refresh(app, monkeypatch):
    machine, google = holding(app, monkeypatch)
    stranger = runner(app, label="Laptop")
    assert refresh(app, stranger["token"]).status_code == 403
    assert refresh(app, "cara-test").status_code == 403
    assert refresh(app, "inbox-test").status_code == 403
    # The secret is added only to the grant this server issued, for its own mailbox: never to a token from elsewhere.
    r = refresh(app, machine["token"], refresh_token="1//someone-elses")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_this_grant"  # the CLI reads it as reconnect
    assert refresh(app, machine["token"], mailbox="ben@acme.example").status_code == 403
    assert refresh(app, machine["token"], mailbox="Cara@Acme.example").status_code == 200
    assert len(google.calls) == 1
    with app.app.state.store.read() as c:
        stored = dict(c.execute("SELECT grant_hash,pending_hash FROM google_connects").fetchone())
    assert stored == {"grant_hash": google_connect.fingerprint("1//grant"), "pending_hash": None}
    google.calls.clear()
    post(app, "mail/google/disconnect", {}, "cara-test")
    claimed = post(app, "connectors/mail/google/claim", {"holds_mail": True}, machine["token"])["connect"]
    post(app, f"connectors/mail/google/{claimed['id']}/result", {"status": "disconnected"}, machine["token"])
    assert refresh(app, machine["token"]).status_code == 403  # disconnected: nothing left to refresh
    assert row(app)["grant_hash"] is None
    assert google.calls == []


def test_a_failed_reconnect_keeps_the_earlier_grant_refreshable_and_disconnect_ends_it(app, monkeypatch):
    machine, google = holding(app, monkeypatch)
    exchanged(app, machine, refresh_token="1//stray", status="failed", kind="identity_mismatch")
    monkeypatch.setattr(google_connect, "post_token", google)
    assert refresh(app, machine["token"]).status_code == 200
    assert refresh(app, machine["token"], refresh_token="1//stray").status_code == 403
    exchanged(app, machine, refresh_token="1//second")
    monkeypatch.setattr(google_connect, "post_token", google)
    assert refresh(app, machine["token"]).status_code == 403  # replaced by the reconnect
    assert refresh(app, machine["token"], refresh_token="1//second").status_code == 200


def test_a_refused_refresh_says_why_and_nothing_else_and_google_out_of_reach_or_down_is_a_network_refusal(
    app, monkeypatch
):
    machine, _ = holding(app, monkeypatch)
    for error, kind in [
        ("invalid_grant", "signin"),
        ("admin_policy_enforced", "admin_policy"),
        ("invalid_client", "app_config"),
        ("org_internal", "app_unavailable"),
        ("server_error", "error"),
    ]:
        monkeypatch.setattr(google_connect, "post_token", FakeGoogle(error=error))
        r = refresh(app, machine["token"])
        assert r.status_code == 409 and r.json()["error"]["code"] == "google_refused", (error, r.status_code, r.text)
        assert r.json()["error"]["kind"] == kind, (error, r.text)
        assert "1//grant" not in r.text and "tico-secret" not in r.text, error
    for name, google in [
        ("unreachable", FakeGoogle(error=OSError("unreachable"))),
        ("503", FakeGoogle(error="internal_failure", status=503)),
    ]:
        monkeypatch.setattr(google_connect, "post_token", google)
        r = refresh(app, machine["token"])
        assert r.status_code == 409 and r.json()["error"]["kind"] == "network", (name, r.status_code, r.text)


def test_an_answer_wider_than_asked_is_refused_not_passed_on(app, monkeypatch):
    machine, _ = holding(
        app,
        monkeypatch,
        FakeGoogle(
            {
                "access_token": "ya29.wide",
                "expires_in": 3599,
                "scope": oauth.G + "gmail.readonly " + oauth.G + "gmail.modify",
            }
        ),
    )
    r = refresh(app, machine["token"])
    assert r.status_code == 409 and "ya29.wide" not in r.text


def test_the_company_s_own_app_signs_in_for_the_act_scopes_too(own_app):
    _, query = start(own_app)
    assert query["client_id"] == "own-client"
    assert query["scope"].split() == google_connect.READ_SCOPES + google_connect.ACT_SCOPES
    seen = get(own_app, "mail/google", "cara-test")
    assert seen["app"] == "own" and seen["scopes"] == google_connect.READ_SCOPES + google_connect.ACT_SCOPES


def test_the_shared_app_asks_for_read_only(app):
    _, query = start(app)
    assert query["scope"].split() == google_connect.READ_SCOPES
    seen = get(app, "mail/google", "cara-test")
    assert seen["app"] == "shared" and seen["scopes"] == google_connect.READ_SCOPES


def test_an_own_app_grant_keeps_the_scopes_google_gave_and_refreshes_only_those(own_app, monkeypatch):
    scopes = google_connect.READ_SCOPES + google_connect.ACT_SCOPES
    machine = runner(own_app)
    exchanged(own_app, machine, scope=" ".join(scopes))
    seen = get(own_app, "mail/google", "cara-test")
    assert seen["acts"] == {"mail": True, "calendar": True} and seen["message"] is None
    assert set(seen["scopes"]) == set(scopes)
    google = FakeGoogle(
        {
            "access_token": "ya29.mail",
            "expires_in": 3599,
            "scope": oauth.G + "gmail.modify " + oauth.G + "gmail.readonly",
        }
    )
    monkeypatch.setattr(google_connect, "post_token", google)
    r = refresh(own_app, machine["token"], scopes=[oauth.G + "gmail.modify", oauth.G + "gmail.readonly"])
    assert r.status_code == 200 and r.json()["scopes"] == [oauth.G + "gmail.modify", oauth.G + "gmail.readonly"]
    sent = google.calls[-1]
    assert sent["scope"] == oauth.G + "gmail.modify " + oauth.G + "gmail.readonly"  # exactly what was asked
    assert sent["client_id"] == "own-client" and sent["client_secret"] == "own-secret"
    google.answer = {"access_token": "ya29.cal", "expires_in": 3599, "scope": oauth.G + "calendar.events"}
    r = refresh(own_app, machine["token"], service="calendar", scopes=[oauth.G + "calendar.events"])
    assert r.status_code == 200 and r.json()["scopes"] == [oauth.G + "calendar.events"]
    count = len(google.calls)
    assert refresh(own_app, machine["token"], scopes=[oauth.G + "gmail.compose"]).status_code == 422  # never granted
    assert len(google.calls) == count


def test_a_read_only_own_app_grant_or_a_legacy_one_never_refreshes_act_scopes(own_app, monkeypatch):
    machine = runner(own_app)
    exchanged(own_app, machine, scope=" ".join(google_connect.READ_SCOPES))  # the write boxes were not ticked
    seen = get(own_app, "mail/google", "cara-test")
    assert seen["status"] == "connected" and seen["app"] == "own" and seen["acts"] == {"mail": False, "calendar": False}
    assert seen["message"] == google_connect.UPGRADE["both"][0]  # a reconnect would add them; nothing forces it
    assert seen["hint"] == google_connect.UPGRADE["both"][1]
    google = FakeGoogle()
    monkeypatch.setattr(google_connect, "post_token", google)
    assert refresh(own_app, machine["token"], scopes=[oauth.G + "gmail.modify"]).status_code == 422
    assert (
        refresh(own_app, machine["token"], service="calendar", scopes=[oauth.G + "calendar.events"]).status_code == 422
    )
    assert google.calls == []
    with own_app.app.state.store.transaction() as c:  # a grant from before the scopes were recorded
        c.execute("UPDATE google_connects SET grant_scopes=NULL WHERE person_id='cara'")
    assert get(own_app, "mail/google", "cara-test")["acts"] == {"mail": False, "calendar": False}
    assert refresh(own_app, machine["token"], scopes=[oauth.G + "gmail.modify"]).status_code == 422
    assert google.calls == []


def test_the_company_s_app_says_which_write_access_a_grant_has_for_each_service(own_app):
    machine = runner(own_app)
    exchanged(own_app, machine, scope=" ".join(google_connect.READ_SCOPES + [oauth.G + "gmail.modify"]))
    seen = get(own_app, "mail/google", "cara-test")
    assert seen["acts"] == {"mail": True, "calendar": False}
    assert seen["message"] == google_connect.UPGRADE["calendar"][0]
    assert seen["hint"] == "Connect Google again to let Tico also add calendar events."
    exchanged(
        own_app,
        machine,
        refresh_token="1//cal",
        scope=" ".join(google_connect.READ_SCOPES + [oauth.G + "calendar.events"]),
    )
    seen = get(own_app, "mail/google", "cara-test")
    assert seen["acts"] == {"mail": False, "calendar": True}
    assert seen["message"] == google_connect.UPGRADE["mail"][0]
    assert seen["hint"] == "Connect Google again to let Tico also label, archive and draft mail."


def test_the_shared_app_refuses_an_answer_with_the_act_scopes_revokes_it_and_keeps_no_grant(app, monkeypatch):
    wide = {
        "access_token": "ya29.wide",
        "refresh_token": "1//wide",
        "expires_in": 3599,
        "scope": " ".join(google_connect.READ_SCOPES + google_connect.ACT_SCOPES),
    }
    google = FakeGoogle(wide)
    monkeypatch.setattr(google_connect, "post_token", google)
    machine = runner(app)
    claimed = claimed_connect(app, machine)
    r = app.post(f"/api/v2/connectors/mail/google/{claimed['id']}/exchange", json={}, headers=headers(machine["token"]))
    assert r.status_code == 409 and r.json()["error"]["kind"] == "scope"
    assert "ya29.wide" not in r.text and "1//wide" not in r.text  # no token reaches the computer
    assert google.uris == [google_connect.TOKEN_URI, google_connect.REVOKE_URI]  # and Google is asked to revoke it
    assert google.calls[-1] == {"token": "1//wide"}
    stored = row(app)
    assert stored["pending_hash"] is None and stored["pending_scopes"] is None and stored["grant_hash"] is None
    # The computer reports the refusal: nothing is connected, and no refresh can use the revoked grant.
    post(
        app,
        f"connectors/mail/google/{claimed['id']}/result",
        {
            "status": "failed",
            "kind": "error",
            "message": "Google granted more access than Tico asks for",
            "hint": "Start the sign-in again.",
        },
        machine["token"],
    )
    assert get(app, "mail/google", "cara-test")["status"] == "failed"
    assert refresh(app, machine["token"], refresh_token="1//wide").status_code == 403
    assert len(google.calls) == 2


def test_the_company_s_app_refuses_an_answer_wider_than_it_may_grant_even_if_the_revoke_cannot_be_sent(
    own_app, monkeypatch
):
    google = RevokeDown(
        {
            "access_token": "ya29.wide",
            "refresh_token": "1//wide",
            "expires_in": 3599,
            "scope": " ".join(google_connect.READ_SCOPES + google_connect.ACT_SCOPES + [oauth.G + "gmail.compose"]),
        }
    )
    monkeypatch.setattr(google_connect, "post_token", google)
    machine = runner(own_app)
    claimed = claimed_connect(own_app, machine)
    r = own_app.post(
        f"/api/v2/connectors/mail/google/{claimed['id']}/exchange", json={}, headers=headers(machine["token"])
    )
    assert r.status_code == 409 and r.json()["error"]["kind"] == "scope"
    assert "ya29.wide" not in r.text and "1//wide" not in r.text
    assert google.uris == [google_connect.TOKEN_URI, google_connect.REVOKE_URI]
    assert row(own_app)["pending_hash"] is None and row(own_app)["grant_hash"] is None


def test_an_answer_naming_the_identity_scopes_in_full_is_not_refused(app, monkeypatch):
    google = FakeGoogle(
        {
            "access_token": "ya29.first",
            "refresh_token": "1//grant",
            "expires_in": 3599,
            "scope": " ".join(["openid", oauth.G + "userinfo.email", oauth.G + "gmail.readonly"]),
        }
    )
    monkeypatch.setattr(google_connect, "post_token", google)
    machine = runner(app)
    claimed = claimed_connect(app, machine)
    r = app.post(f"/api/v2/connectors/mail/google/{claimed['id']}/exchange", json={}, headers=headers(machine["token"]))
    assert r.status_code == 200 and google.uris == [google_connect.TOKEN_URI]
    assert json.loads(row(app)["pending_scopes"]) == sorted(["openid", oauth.G + "gmail.readonly"])


def test_a_failed_own_app_reconnect_keeps_the_act_grant_and_a_read_only_one_replaces_it(own_app, monkeypatch):
    machine = runner(own_app)
    exchanged(own_app, machine, scope=" ".join(google_connect.READ_SCOPES + google_connect.ACT_SCOPES))
    exchanged(
        own_app,
        machine,
        refresh_token="1//stray",
        status="failed",
        kind="identity_mismatch",
        scope=" ".join(google_connect.READ_SCOPES),
    )
    google = FakeGoogle({"access_token": "ya29.kept", "expires_in": 3599, "scope": oauth.G + "gmail.modify"})
    monkeypatch.setattr(google_connect, "post_token", google)
    assert refresh(own_app, machine["token"], scopes=[oauth.G + "gmail.modify"]).status_code == 200
    assert (
        refresh(own_app, machine["token"], refresh_token="1//stray", scopes=[oauth.G + "gmail.modify"]).status_code
        == 403
    )
    exchanged(own_app, machine, refresh_token="1//second", scope=" ".join(google_connect.READ_SCOPES))
    assert get(own_app, "mail/google", "cara-test")["acts"] == {"mail": False, "calendar": False}
    assert (
        refresh(own_app, machine["token"], refresh_token="1//second", scopes=[oauth.G + "gmail.modify"]).status_code
        == 422
    )


def test_another_account_signing_in_is_shown_as_refused(app):
    _, query = start(app)
    back(app, state=query["state"], code="c-1")
    machine = runner(app)
    claimed = post(app, "connectors/mail/google/claim", {}, machine["token"])["connect"]
    post(
        app,
        f"connectors/mail/google/{claimed['id']}/result",
        {
            "status": "failed",
            "kind": "identity_mismatch",
            "message": "the account that signed in is ben@acme.example, not cara@acme.example; nothing was stored",
            "hint": "Sign in to Google as cara@acme.example",
        },
        machine["token"],
    )
    seen = get(app, "mail/google", "cara-test")
    assert seen["status"] == "failed" and seen["kind"] == "identity_mismatch"
    assert "nothing was stored" in seen["message"] and "cara@acme.example" in seen["hint"]


def test_disconnect_is_queued_for_the_computer_without_the_client(app):
    seen = post(app, "mail/google/disconnect", {}, "cara-test")
    assert seen["status"] == "disconnecting"
    machine = runner(app)
    claimed = post(app, "connectors/mail/google/claim", {}, machine["token"])["connect"]
    assert claimed == {"id": claimed["id"], "action": "disconnect", "mailbox": "cara@acme.example"}
    r = app.post(
        f"/api/v2/connectors/mail/google/{claimed['id']}/result",
        json={"status": "connected"},
        headers=headers(machine["token"]),
    )
    assert r.status_code == 409  # a revoke cannot come back as a connect
    post(app, f"connectors/mail/google/{claimed['id']}/result", {"status": "disconnected"}, machine["token"])
    assert get(app, "mail/google", "cara-test")["status"] == "disconnected"


def connected(app, machine, token="cara-test"):
    """A finished connect, taken and answered by `machine`."""
    _, query = start(app, token)
    back(app, token, state=query["state"], code="c-1")
    claimed = post(app, "connectors/mail/google/claim", {"holds_mail": True}, machine["token"])["connect"]
    assert claimed and claimed["action"] == "connect"
    post(
        app,
        f"connectors/mail/google/{claimed['id']}/result",
        {"status": "connected", "can": ["read"]},
        machine["token"],
    )


def test_disconnect_and_reconnect_go_only_to_the_computer_holding_the_grant(app):
    mac, server = runner(app, label="Mac"), runner(app, label="Linux server")
    connected(app, mac)
    post(app, "mail/google/disconnect", {}, "cara-test")
    # The other computer is not told to run the job for it, and can't take it even saying it holds mail.
    assert get(app, "runners/connectors", server["token"])["connectors"] == []
    assert post(app, "connectors/mail/google/claim", {"holds_mail": True}, server["token"])["connect"] is None
    claimed = post(app, "connectors/mail/google/claim", {"holds_mail": True}, mac["token"])["connect"]
    assert claimed["action"] == "disconnect"
    post(app, f"connectors/mail/google/{claimed['id']}/result", {"status": "disconnected"}, mac["token"])
    assert row(app)["holder"] is None
    # A reconnect also goes to the holder, and a failed one leaves the earlier grant's holder in place.
    connected(app, mac)
    _, query = start(app)
    back(app, state=query["state"], code="c-2")
    assert post(app, "connectors/mail/google/claim", {"holds_mail": True}, server["token"])["connect"] is None
    claimed = post(app, "connectors/mail/google/claim", {"holds_mail": True}, mac["token"])["connect"]
    post(
        app,
        f"connectors/mail/google/{claimed['id']}/result",
        {"status": "failed", "kind": "identity_mismatch"},
        mac["token"],
    )
    assert row(app)["holder"] == mac["runner_id"]


# Each connect or disconnect is its own operation (SM-03): the claim names it, and /exchange and /result answer only it.
# An answer from an earlier operation, even on the same computer, is a conflict that changes nothing.


def claim(app, machine):
    return post(app, "connectors/mail/google/claim", {"holds_mail": True}, machine["token"])["connect"]


def signed_in(app, machine, code):
    """A connect started, back from Google with `code`, and claimed by `machine`."""
    _, query = start(app)
    back(app, state=query["state"], code=code)
    return claim(app, machine)


def answer(app, machine, claimed, body):
    return app.post(
        f"/api/v2/connectors/mail/google/{claimed['id']}/result", json=body, headers=headers(machine["token"])
    )


def exchange(app, machine, claimed):
    return app.post(
        f"/api/v2/connectors/mail/google/{claimed['id']}/exchange", json={}, headers=headers(machine["token"])
    )


def grant(refresh_token):
    return {"access_token": "ya29." + refresh_token, "expires_in": 3599, "refresh_token": refresh_token}


def test_each_connect_and_disconnect_is_claimed_under_its_own_id(app):
    machine = runner(app)
    first = signed_in(app, machine, "c-A")
    second = signed_in(app, machine, "c-B")
    post(app, "mail/google/disconnect", {}, "cara-test")
    third = claim(app, machine)
    ids = {first["id"], second["id"], third["id"]}
    assert len(ids) == 3 and "cara" not in ids


def test_a_late_result_from_an_earlier_connect_does_not_finish_the_next_one(app, monkeypatch):
    machine = runner(app)
    monkeypatch.setattr(google_connect, "post_token", FakeGoogle(grant("1//A")))
    a = signed_in(app, machine, "c-A")
    assert exchange(app, machine, a).status_code == 200
    # A's result is delayed. The person connects again, and the same computer claims it.
    b = signed_in(app, machine, "c-B")
    before = row(app)
    assert before["pending_hash"] is None  # A's grant is not left waiting for B's answer
    r = answer(app, machine, a, {"status": "connected", "can": ["read"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale"
    assert row(app) == before  # still B's: finishing, B's code kept, no grant
    assert before["status"] == "finishing" and before["code"] == "c-B" and before["grant_hash"] is None
    google = FakeGoogle(grant("1//B"))
    monkeypatch.setattr(google_connect, "post_token", google)
    assert exchange(app, machine, b).status_code == 200 and google.calls[0]["code"] == "c-B"
    assert answer(app, machine, b, {"status": "connected", "can": ["read"]}).status_code == 200
    assert row(app)["grant_hash"] == google_connect.fingerprint("1//B")


def test_a_delayed_exchange_from_an_earlier_connect_spends_nothing(app, monkeypatch):
    google = FakeGoogle(grant("1//B"))
    monkeypatch.setattr(google_connect, "post_token", google)
    machine = runner(app)
    a = signed_in(app, machine, "c-A")
    b = signed_in(app, machine, "c-B")
    r = exchange(app, machine, a)
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale" and google.calls == []
    assert row(app)["code"] == "c-B"  # B's code is still there for B
    assert exchange(app, machine, b).status_code == 200 and google.calls[0]["code"] == "c-B"


class ConnectsAgainMeanwhile(FakeGoogle):
    """Google answers the exchange only after the person has started another connect."""

    def __init__(self, app, answer):
        super().__init__(answer)
        self.app = app

    def __call__(self, fields, uri=google_connect.TOKEN_URI):
        if uri == google_connect.TOKEN_URI and not self.calls:
            _, query = start(self.app)
            back(self.app, state=query["state"], code="c-B")
        return super().__call__(fields, uri)


def test_an_exchange_answered_after_its_connect_was_replaced_is_not_passed_on_or_kept(app, monkeypatch, caplog):
    machine, _ = holding(app, monkeypatch)  # a grant connected earlier, "1//grant"
    a = signed_in(app, machine, "c-A")
    google = ConnectsAgainMeanwhile(app, grant("1//late"))
    monkeypatch.setattr(google_connect, "post_token", google)
    with caplog.at_level("WARNING", logger="backend.google_connect"):
        r = exchange(app, machine, a)
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale"
    assert "1//late" not in r.text and "ya29" not in r.text  # the computer gets nothing to keep
    # Not revoked at Google: one client and one person share one grant there, so a revoke would end the kept one too.
    assert google.uris == [google_connect.TOKEN_URI]
    stored = row(app)
    assert stored["status"] == "ready" and stored["code"] == "c-B" and stored["pending_hash"] is None
    assert stored["grant_hash"] == google_connect.fingerprint("1//grant")
    assert "stale_exchange" in caplog.text and a["id"] in caplog.text and "1//late" not in caplog.text


def test_a_failed_replacement_keeps_the_grant_connected_before_it(app, monkeypatch):
    machine, google = holding(app, monkeypatch)  # "1//grant" connected
    monkeypatch.setattr(google_connect, "post_token", FakeGoogle(grant("1//A")))
    a = signed_in(app, machine, "c-A")
    assert exchange(app, machine, a).status_code == 200
    b = signed_in(app, machine, "c-B")
    assert answer(app, machine, a, {"status": "connected", "can": ["read"]}).status_code == 409
    assert answer(app, machine, b, {"status": "failed", "kind": "identity_mismatch"}).status_code == 200
    monkeypatch.setattr(google_connect, "post_token", google)
    assert refresh(app, machine["token"]).status_code == 200
    assert refresh(app, machine["token"], refresh_token="1//A").status_code == 403
    stored = row(app)
    assert stored["holder"] == machine["runner_id"] and stored["pending_hash"] is None


def test_repeated_disconnects_on_one_computer_each_answer_only_their_own(app, monkeypatch):
    machine, _ = holding(app, monkeypatch)
    post(app, "mail/google/disconnect", {}, "cara-test")
    first = claim(app, machine)
    post(app, "mail/google/disconnect", {}, "cara-test")
    second = claim(app, machine)
    assert first["action"] == second["action"] == "disconnect" and first["id"] != second["id"]
    before = row(app)
    r = answer(app, machine, first, {"status": "failed", "kind": "not_here"})
    assert r.status_code == 409 and row(app) == before
    assert answer(app, machine, second, {"status": "disconnected"}).status_code == 200
    assert row(app)["status"] == "disconnected" and row(app)["grant_hash"] is None


def test_a_repeated_result_gets_the_recorded_answer_and_changes_nothing(app, monkeypatch):
    machine = runner(app)
    monkeypatch.setattr(google_connect, "post_token", FakeGoogle(grant("1//A")))
    a = signed_in(app, machine, "c-A")
    exchange(app, machine, a)
    first = answer(app, machine, a, {"status": "connected", "can": ["read"]})
    assert first.status_code == 200
    with app.app.state.store.read() as c:
        events = c.execute("SELECT count(*) FROM events WHERE action='mail.google.connected'").fetchone()[0]
    settled = row(app)
    again = answer(app, machine, a, {"status": "connected", "can": ["read"]})
    assert again.status_code == 200 and again.json() == first.json()
    assert row(app) == settled
    with app.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='mail.google.connected'").fetchone()[0] == events
    # A different answer for the same operation is not a repeat.
    other = answer(app, machine, a, {"status": "failed", "kind": "network"})
    assert other.status_code == 409 and row(app) == settled


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
        c.execute(
            "UPDATE google_connects SET started_at=? WHERE person_id='cara'",
            (time.time() - google_connect.STALLED_SECONDS - 1,),
        )
    seen = get(app, "mail/google", "cara-test")
    assert seen["status"] == "disconnecting" and seen["stalled"] is True
    assert "hasn't come online" in seen["message"] and "myaccount.google.com/permissions" in seen["hint"]
    # When the computer is back it still revokes.
    assert (
        post(app, "connectors/mail/google/claim", {"holds_mail": True}, mac["token"])["connect"]["action"]
        == "disconnect"
    )


def test_only_recent_live_computers_keep_a_holder_record(app):
    old, gone, now = runner(app, label="Old"), runner(app, label="Gone"), runner(app, label="Now")
    for machine in (old, gone):
        post(app, "connectors/mail/google/claim", {"holds_mail": True}, machine["token"])
    with app.app.state.store.transaction() as c:
        c.execute(
            "UPDATE google_mail_holders SET seen_at=? WHERE runner_id=?",
            (time.time() - google_connect.HOLDER_SECONDS - 1, old["runner_id"]),
        )
        c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), gone["runner_id"]))
    post(app, "connectors/mail/google/claim", {}, now["token"])
    with app.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT runner_id FROM google_mail_holders")] == [now["runner_id"]]


def test_nobody_acting_for_the_person_connects_or_disconnects(app):
    settings = app.app.state.store.settings
    settings.test_identities["cara-assistant"] = Identity("human:cara", "human", "cara@acme.example", via="assistant")
    machine, _, attempt = setup_attempt(app, "ops")
    settings.test_identities["cara-botops"] = Identity(
        "human:cara",
        "human",
        "cara@acme.example",
        task_actor="bot:ops",
        runner_id=machine["runner_id"],
        attempt_id=attempt["id"],
    )
    for token in ("cara-assistant", "cara-botops"):
        for path in ("connect", "disconnect"):
            r = app.post("/api/v2/mail/google/" + path, json={}, headers=headers(token))
            assert r.status_code == 403, (token, path, r.text)
    with app.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM google_connects").fetchone()[0] == 0


def test_a_sign_in_nobody_finishes_expires(app):
    _, query = start(app)
    with app.app.state.store.transaction() as c:
        c.execute("UPDATE google_connects SET started_at=? WHERE person_id='cara'", (time.time() - 3600,))
    assert back(app, state=query["state"], code="c-1") == google_connect.INBOX
    seen = get(app, "mail/google", "cara-test")
    assert seen["kind"] == "expired" and row(app)["verifier"] is None


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


# The computer's side: runner_support/connectors.py


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


def test_the_computer_finishes_the_sign_in_through_the_server_with_the_request_on_stdin(tmp_path, monkeypatch):
    seen = tmp_path / "seen"
    mail_script(
        tmp_path,
        f'echo "$@" > {seen}.args\ncat > {seen}.stdin\nenv > {seen}.env\n'
        'echo \'{"status": "connected", "mailbox": "cara@acme.example", "can": ["read"]}\'\n',
    )
    hub = Hub(
        [{"id": "cara", "action": "connect", "mailbox": "cara@acme.example", "client_id": CLIENT, "broker": "server"}]
    )
    monkeypatch.setenv("HUB_TOKEN", "hub-token")
    publisher = ConnectorPublisher(
        {"url": "http://localhost:8765", "token": "runner-token", "projects_dir": str(tmp_path)}, client=hub
    )
    publisher.script = tmp_path / "hub/scripts/mail.sh"
    assert publisher.google_connect_tick() == 1
    assert hub.results == [("connectors/mail/google/cara/result", {"status": "connected", "can": ["read"]})]
    assert (tmp_path / "seen.args").read_text().split() == ["google", "finish"]
    assert json.loads((tmp_path / "seen.stdin").read_text()) == {
        "id": "cara",
        "mailbox": "cara@acme.example",
        "client_id": CLIENT,
        "broker": "server",
    }
    env = dict(line.split("=", 1) for line in (tmp_path / "seen.env").read_text().splitlines() if "=" in line)
    assert env[oauth.BROKER_URL_ENV] == "http://localhost:8765" and env[oauth.BROKER_AUTH_ENV] == "runner-token"
    assert "HUB_TOKEN" not in env


def test_a_failed_finish_carries_only_its_kind_and_words(tmp_path):
    mail_script(
        tmp_path,
        'cat > /dev/null\necho \'{"status": "failed", "kind": "admin_policy", "message": "m", '
        '"hint": "h", "mailbox": "cara@acme.example"}\'\nexit 1\n',
    )
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
    publisher = ConnectorPublisher({"url": "http://localhost:8765", "token": "test", "owner_handle": "ana"}, client=hub)
    publisher.script = tmp_path / "hub/scripts/mail.sh"
    assert publisher.calendar_action_tick() == 1
    path, body = hub.results[0]
    assert path == "connectors/calendar/actions/act-1/result" and body["status"] == "failed"
    assert "refused" in body["error"]


def test_the_migration_gives_each_existing_connect_its_own_operation_id():
    import importlib
    import sqlite3

    migration = importlib.import_module("backend.migrations.20261112160353_add_operation_to_google_connects")
    conn = sqlite3.connect(":memory:")
    migration.up(conn)  # a new file has no table yet: the module's CREATE carries the columns
    conn.execute(
        "CREATE TABLE google_connects(person_id TEXT PRIMARY KEY, mailbox TEXT NOT NULL, status TEXT NOT NULL, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    for person, status in (("cara", "disconnecting"), ("ben", "connected")):
        conn.execute("INSERT INTO google_connects VALUES(?,?,?,'t','t')", (person, person + "@acme.example", status))
    migration.up(conn)
    ids = [r[0] for r in conn.execute("SELECT operation_id FROM google_connects WHERE answered IS NULL")]
    assert len(set(ids)) == 2 and all(i.startswith("gc_") for i in ids)
    migration.up(conn)  # a second run keeps the ids
    assert [r[0] for r in conn.execute("SELECT operation_id FROM google_connects")] == ids


class StaleHub(Hub):
    """The server refuses the first result: the person connected again before it arrived."""

    def post(self, path, body):
        from clients.tico import APIError

        answer = super().post(path, body)
        if path.endswith("/result") and len(self.results) == 1:
            raise APIError("stale", "This Google connect was replaced", 409)
        return answer


def test_a_result_refused_as_stale_does_not_stop_the_computer_taking_the_next_connect(tmp_path):
    mail_script(tmp_path, "echo '{\"status\": \"connected\", \"can\": [\"read\"]}'\n")
    queue = [
        {"id": op, "action": "connect", "mailbox": "cara@acme.example", "client_id": CLIENT, "broker": "server"}
        for op in ("gc_a", "gc_b")
    ]
    hub = StaleHub(queue)
    publisher = ConnectorPublisher(
        {"url": "http://localhost:8765", "token": "runner-token", "projects_dir": str(tmp_path)}, client=hub
    )
    publisher.script = tmp_path / "hub/scripts/mail.sh"
    assert publisher.google_connect_tick() == 2
    assert [path for path, _ in hub.results] == [
        "connectors/mail/google/gc_a/result",
        "connectors/mail/google/gc_b/result",
    ]
