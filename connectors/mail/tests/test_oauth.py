"""One person's own Google account (connectors/mail/oauth.py): read-only at Google, bound to the account that
signed in, never a door to anyone else's mailbox, and never a quiet fallback to the service account."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import types
import unittest
import urllib.parse
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from connectors.mail import Failure, Refused, auth, oauth
from connectors.mail import __main__ as cli
from connectors.mail import calendar as cal, gmail as gm

G = oauth.G
CLIENT = {"client_id": "123.apps.googleusercontent.com", "client_secret": "client-secret-value",
          "token_uri": oauth.TOKEN_URI}
READ_ONLY = ["openid", "https://www.googleapis.com/auth/userinfo.email", G + "gmail.readonly",
             G + "calendar.events.readonly", G + "calendar.freebusy", G + "calendar.calendarlist.readonly"]


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeGoogle:
    """Google's token, tokeninfo and revoke endpoints, as urlopen sees them."""

    def __init__(self, email="ana@acme.example", scopes=READ_ONLY, refresh="1//refresh-secret"):
        self.email, self.scopes, self.refresh = email, scopes, refresh
        self.calls, self.revoked = [], []
        self.revoke_fails = False

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.calls.append(url.split("?")[0])
        if url.split("?")[0] == oauth.TOKEN_URI:
            fields = urllib.parse.parse_qs(request.data.decode())
            assert fields["grant_type"] == ["authorization_code"] and fields["code_verifier"][0]
            body = {"access_token": "ya29.access-secret", "expires_in": 3599, "scope": " ".join(self.scopes)}
            if self.refresh:
                body["refresh_token"] = self.refresh
            return Response(json.dumps(body).encode())
        if url.startswith(oauth.TOKENINFO_URI):
            return Response(json.dumps({"email": self.email, "email_verified": "true",
                                        "scope": " ".join(self.scopes)}).encode())
        if url.startswith(oauth.REVOKE_URI):
            if self.revoke_fails:
                raise OSError("network down")
            self.revoked.append(urllib.parse.parse_qs(request.data.decode())["token"][0])
            return Response(b"{}")
        raise AssertionError(url)


class RefreshError(Exception):
    pass


class TransportError(Exception):
    pass


class FakeCredentials:
    """google.oauth2.credentials.Credentials: refresh() answers as the test says."""
    made = []
    refresh_error = None

    def __init__(self, token=None, refresh_token=None, token_uri=None, client_id=None, client_secret=None,
                 scopes=None, expiry=None):
        self.token, self.refresh_token, self.scopes, self.expiry = token, refresh_token, scopes, expiry
        self.client_id = client_id
        FakeCredentials.made.append(self)

    def refresh(self, _request):
        if FakeCredentials.refresh_error:
            raise FakeCredentials.refresh_error
        self.token = "ya29.minted-for-" + ",".join(s.replace(G, "") for s in self.scopes)


def google_modules():
    modules = {name: types.ModuleType(name) for name in (
        "google", "google.auth", "google.auth.exceptions", "google.auth.transport",
        "google.auth.transport.requests", "google.oauth2", "google.oauth2.credentials",
        "google.oauth2.service_account")}
    modules["google.auth.exceptions"].RefreshError = RefreshError
    modules["google.auth.exceptions"].TransportError = TransportError
    modules["google.auth.transport.requests"].Request = lambda: None
    modules["google.oauth2.credentials"].Credentials = FakeCredentials

    class NoServiceAccount:
        @staticmethod
        def from_service_account_file(*_a, **_k):
            raise AssertionError("the service account must not be used for a connected mailbox")
    modules["google.oauth2.service_account"].Credentials = NoServiceAccount
    modules["google.oauth2"].service_account = modules["google.oauth2.service_account"]
    modules["google.oauth2"].credentials = modules["google.oauth2.credentials"]
    return modules


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.key = self.dir / "google-sa.json"
        self.grants = self.dir / "google-oauth.json"
        self.env = patch.dict(os.environ, {"GOOGLE_SA_KEY": str(self.key)}, clear=False)
        self.env.start()
        for name in (auth.SOCKET_ENV, oauth.GRANTS_ENV, "HUB_TOKEN", oauth.BROKER_URL_ENV, oauth.BROKER_AUTH_ENV):
            os.environ.pop(name, None)
        self.modules = patch.dict(sys.modules, google_modules())
        self.modules.start()
        FakeCredentials.made, FakeCredentials.refresh_error = [], None

    def tearDown(self):
        self.modules.stop()
        self.env.stop()
        for p in sorted(self.dir.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        self.dir.rmdir()

    def connect(self, google=None, mailbox="ana@acme.example", capabilities=()):
        google = google or FakeGoogle()
        flow = oauth.authorization(CLIENT, mailbox, capabilities)
        answer = f"http://127.0.0.1:8765/?state={flow['state']}&code=4/0-code&scope=x"
        return oauth.connect(mailbox, CLIENT, oauth.code_from(answer, flow["state"]), flow["verifier"],
                             capabilities, opener=google), google


class Connect(Base):
    def test_sign_in_asks_google_for_read_only_and_names_the_account(self):
        flow = oauth.authorization(CLIENT, "ana@acme.example", [])
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(flow["url"]).query)
        scopes = query["scope"][0].split()
        self.assertEqual(scopes, oauth.IDENTITY_SCOPES + oauth.READ_SCOPES)
        for broad in (G + "gmail.modify", G + "gmail.compose", G + "calendar", G + "calendar.events",
                      "https://mail.google.com/"):
            self.assertNotIn(broad, scopes)
        self.assertEqual(query["login_hint"], ["ana@acme.example"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["access_type"], ["offline"])
        with_labels = urllib.parse.parse_qs(urllib.parse.urlsplit(
            oauth.authorization(CLIENT, "ana@acme.example", ["labels"])["url"]).query)["scope"][0].split()
        self.assertEqual(set(with_labels) - set(scopes), {G + "gmail.modify"}, "a capability adds only its own scope")

    def test_a_grant_is_stored_private_and_shown_without_any_token(self):
        found, google = self.connect()
        self.assertEqual(stat.S_IMODE(self.grants.stat().st_mode), 0o600)
        self.assertEqual(found["can"], ["read mail", "read calendar"])
        shown = json.dumps(oauth.status())
        for secret in ("1//refresh-secret", "ya29.access-secret", "client-secret-value"):
            self.assertNotIn(secret, shown)
            self.assertNotIn(secret, json.dumps(found))
        self.assertEqual(google.revoked, [])

    def test_another_account_signing_in_is_refused_and_its_grant_revoked(self):
        google = FakeGoogle(email="someone-else@acme.example")
        with self.assertRaises(Failure) as caught:
            self.connect(google)
        self.assertIn("someone-else@acme.example, not ana@acme.example", caught.exception.msg)
        self.assertFalse(self.grants.exists(), "nothing stored")
        self.assertEqual(google.revoked, ["1//refresh-secret"], "the stray grant is revoked at Google")

    def test_an_answer_from_another_sign_in_is_refused(self):
        flow = oauth.authorization(CLIENT, "ana@acme.example", [])
        with self.assertRaises(Failure):
            oauth.code_from("http://127.0.0.1:8765/?state=forged&code=4/0-code", flow["state"])
        with self.assertRaises(Failure):
            oauth.code_from("http://127.0.0.1:8765/?error=access_denied&state=" + flow["state"], flow["state"])

    def test_a_service_account_key_is_not_an_oauth_client(self):
        client = self.dir / "client.json"
        client.write_text(json.dumps({"type": "service_account", "client_id": "1", "client_secret": "x"}))
        with self.assertRaises(Failure):
            oauth.read_client(client)
        client.write_text(json.dumps({"installed": CLIENT}))
        self.assertEqual(oauth.read_client(client)["client_id"], CLIENT["client_id"])

    def test_revoke_forgets_the_grant_even_when_google_cannot_be_reached(self):
        _, google = self.connect()
        google.revoke_fails = True
        self.assertFalse(oauth.revoke("ana@acme.example", opener=google))
        self.assertIsNone(oauth.grant("ana@acme.example"))
        with self.assertRaises(Failure):
            auth.credentials("ana@acme.example", [auth.GMAIL_SCOPE])     # and no service-account key behind it

    def test_a_grants_file_others_can_read_is_refused(self):
        self.connect()
        os.chmod(self.grants, 0o644)
        with self.assertRaises(Failure):
            oauth.grant("ana@acme.example")


class Tokens(Base):
    def test_each_service_gets_a_token_narrowed_to_what_was_granted(self):
        self.connect()
        token, _expiry, granted = auth.mint_token("Ana@Acme.example", "gmail")
        self.assertEqual(granted, [G + "gmail.readonly"])
        self.assertEqual(token, "ya29.minted-for-gmail.readonly")
        _token, _expiry, granted = auth.mint_token("ana@acme.example", "calendar")
        self.assertEqual(set(granted), {G + "calendar.events.readonly", G + "calendar.freebusy",
                                        G + "calendar.calendarlist.readonly"})
        self.assertEqual(FakeCredentials.made[-1].client_id, CLIENT["client_id"])

    def test_mint_token_prints_the_scopes_and_never_the_refresh_token(self):
        self.connect()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_mint_token(Namespace(mailbox="ana@acme.example", service="gmail"))
        printed = json.loads(out.getvalue())
        self.assertEqual(printed["scopes"], [G + "gmail.readonly"])
        self.assertNotIn("1//refresh-secret", out.getvalue())

    def test_an_expired_or_revoked_grant_says_reconnect_and_never_falls_back(self):
        self.connect()
        self.key.write_text("{}")                        # a service-account key on the same computer
        os.chmod(self.key, 0o600)
        FakeCredentials.refresh_error = RefreshError("invalid_grant: Token has been expired or revoked.",
                                                     {"error": "invalid_grant"})
        with self.assertRaises(Failure) as caught:
            auth.mint_token("ana@acme.example", "gmail")
        self.assertIn("(invalid_grant)", caught.exception.msg)
        self.assertEqual(caught.exception.kind, "signin")
        self.assertIn("Connect Google", caught.exception.hint)
        self.assertIn("does not fall back to a service account", caught.exception.hint)
        self.assertNotIn("1//refresh-secret", caught.exception.msg + caught.exception.hint)

    def test_one_persons_grant_never_acts_for_anyone_else(self):
        self.connect()
        self.assertIsNone(oauth.grant("ben@acme.example"))
        self.assertEqual(auth.source("ben@acme.example"), "service_account")
        with self.assertRaises(Failure) as caught:     # no key on this computer: nobody else is reachable
            auth.mint_token("ben@acme.example", "gmail")
        self.assertIn("service-account key is not at", caught.exception.msg)
        self.assertEqual([c.refresh_token for c in FakeCredentials.made], [], "ana's grant was never used")

    def test_a_grant_without_calendar_cannot_mint_calendar(self):
        self.connect(FakeGoogle(scopes=["openid", G + "gmail.readonly"]))
        with self.assertRaises(Failure) as caught:
            auth.mint_token("ana@acme.example", "calendar")
        self.assertIn("did not grant Google Calendar access", caught.exception.msg)


class NotGranted:
    """A Gmail/Calendar service that must never be called."""
    def __getattr__(self, name):
        raise AssertionError(f"Google was asked ({name}) for something the grant does not allow")


class ReadOnlyRefusals(Base):
    def service(self, granted):
        service = NotGranted.__new__(NotGranted)
        object.__setattr__(service, "_tico_granted", granted)
        return service

    def test_writes_on_a_read_only_account_are_refused_before_google(self):
        g = gm.Gmail(self.service([G + "gmail.readonly"]), "ana@acme.example")
        for attempt in (lambda: g.modify("m1", add=["L1"]), lambda: g.modify("m1", remove=["INBOX"]),
                        lambda: g.create_label("hub/x"), lambda: g.create_draft("cmF3"),
                        lambda: g.update_draft("d1", "cmF3"), lambda: g.delete_draft("d1"),
                        lambda: g.send_draft("d1")):
            with self.assertRaises(Refused) as caught:
                attempt()
            self.assertIn("read-only at Google", caught.exception.msg)
            self.assertIn("nothing was sent to Google", caught.exception.msg)
        c = cal.Calendar(self.service([G + "calendar.events.readonly", G + "calendar.freebusy"]),
                         "ana@acme.example")
        from datetime import datetime, timezone
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        for attempt in (lambda: c.create_event(now, now, "x"), lambda: c.delete_event("e1"),
                        lambda: c.create_hub_event(now, now, "x", [], "e1"),
                        lambda: c.create_scheduling_event(now, now, "a@b.example", "e1", "")):
            with self.assertRaises(Refused) as caught:
                attempt()
            self.assertIn("--allow calendar-write", caught.exception.hint)

    def test_a_refusal_exits_2_and_the_service_account_keeps_its_delegation(self):
        with patch.object(cli.oauth, "status", side_effect=Refused("read-only", "hint")), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["google", "status"]), 2)
        self.assertIsNone(oauth.require(None, "labels", "ana@acme.example", "label mail"))
        oauth.require([G + "gmail.modify"], "drafts", "ana@acme.example", "write drafts")   # modify covers drafts

    def test_a_turn_with_a_supervisor_token_refuses_the_same_writes(self):
        os.environ[auth.SOCKET_ENV], os.environ["HUB_TOKEN"] = "/tmp/none.sock", "attempt"
        with patch("runner.credential_socket.request_mail",
                   return_value={"token": "ya29.t", "expiry": "", "scopes": [G + "gmail.readonly"]}):
            creds = auth.credentials("ana@acme.example", [auth.GMAIL_SCOPE])
        self.assertEqual(creds._tico_granted, [G + "gmail.readonly"])
        with patch("runner.credential_socket.request_mail", return_value={"token": "ya29.t", "expiry": ""}):
            self.assertIsNone(getattr(auth.credentials("ana@acme.example", [auth.GMAIL_SCOPE]), "_tico_granted", None))
        from runner.credential_socket import MailRefused
        with patch("runner.credential_socket.request_mail", side_effect=MailRefused("signin")):
            with self.assertRaises(oauth.SignInFailed) as caught:
                auth.credentials("ana@acme.example", [auth.GMAIL_SCOPE])
        self.assertIn("Reconnect", caught.exception.hint)       # a revoked grant reads as reconnect in a turn


SERVER_CONNECT = {"id": "ana", "mailbox": "Ana@Acme.example", "client_id": CLIENT["client_id"], "broker": "server"}
GRANTED = {"access_token": "ya29.access-secret", "refresh_token": "1//refresh-secret", "expires_in": 3599,
           "scope": " ".join(READ_ONLY)}


class Server:
    """Tico's server as oauth.broker() reaches it: the exchange and the refresh, recorded."""

    def __init__(self, error=None):
        self.calls, self.error = [], error

    def post(self, path, body):
        self.calls.append((path, body))
        if self.error:
            raise self.error
        if path.endswith("/exchange"):
            return dict(GRANTED)
        return {"access_token": "ya29.from-server", "expires_in": 3599, "expiry": "2026-10-16T01:00:00Z",
                "scopes": body["scopes"]}


class ServerRefreshed(Base):
    """A grant from Settings > Inbox > Connect Google: Tico's server holds the client secret and refreshes it."""

    def finish(self, server=None, google=None):
        server = server or Server()
        with patch.object(oauth, "broker", return_value=server):
            return oauth.finish(dict(SERVER_CONNECT), opener=google or FakeGoogle()), server

    def test_finish_asks_the_server_for_the_grant_and_keeps_no_secret(self):
        result, server = self.finish()
        self.assertEqual(result["status"], "connected")
        self.assertEqual(server.calls, [("connectors/mail/google/ana/exchange", {})])
        stored = json.loads(self.grants.read_text())
        self.assertNotIn("client", stored, "no client of this computer's own to keep")
        grant = stored["grants"]["ana@acme.example"]
        self.assertEqual((grant["client_id"], grant["broker"]), (CLIENT["client_id"], "server"))
        self.assertNotIn("client_secret", grant)
        self.assertNotIn("client-secret-value", self.grants.read_text())
        self.assertNotIn("refresh-secret", json.dumps(result))

    def test_finish_without_the_server_s_connect_is_refused(self):
        for request in ({**SERVER_CONNECT, "broker": None}, {**SERVER_CONNECT, "id": ""},
                        {"mailbox": "ana@acme.example", "code": "4/0", "verifier": "v", **CLIENT}):
            server = Server()
            with patch.object(oauth, "broker", return_value=server):
                result = oauth.finish(request, opener=FakeGoogle())
            self.assertEqual((result["status"], result["kind"]), ("failed", "app_config"))
            self.assertEqual(server.calls, [])
        self.assertFalse(self.grants.exists())

    def test_the_account_check_still_applies_to_a_server_exchange(self):
        google = FakeGoogle(email="eve@acme.example")
        result, _ = self.finish(google=google)
        self.assertEqual(result["kind"], "identity_mismatch")
        self.assertEqual(google.revoked, ["1//refresh-secret"])
        self.assertFalse(self.grants.exists())

    def test_each_service_s_token_comes_from_the_server_narrowed_to_what_was_granted(self):
        self.finish()
        server = Server()
        with patch.object(oauth, "broker", return_value=server):
            token, expiry, granted = auth.mint_token("ana@acme.example", "gmail")
            self.assertEqual((token, granted), ("ya29.from-server", [G + "gmail.readonly"]))
            self.assertEqual(expiry, "2026-10-16T01:00:00")
            _token, _expiry, granted = auth.mint_token("ana@acme.example", "calendar")
        self.assertEqual(server.calls[0], ("connectors/mail/google/token", {
            "mailbox": "ana@acme.example", "service": "gmail", "refresh_token": "1//refresh-secret", "scopes": [G + "gmail.readonly"]}))
        self.assertEqual(server.calls[1][1]["scopes"], [G + "calendar.events.readonly", G + "calendar.freebusy",
                                                        G + "calendar.calendarlist.readonly"])
        self.assertEqual(FakeCredentials.made[-1].refresh_token, None, "google-auth never refreshes it here")
        self.assertEqual([c for c in FakeCredentials.made if c.refresh_token], [])

    def test_a_server_refusal_says_reconnect_or_admin_and_never_falls_back(self):
        from clients.tico import APIError
        self.finish()
        self.key.write_text("{}")                        # a service-account key on the same computer
        os.chmod(self.key, 0o600)
        for kind, expected in (("signin", "signin"), ("admin_policy", "admin_policy"), ("app_config", "app_config"),
                               ("network", "network"), ("error", "signin")):
            refusal = APIError("google_refused", "Google refused it (x)", 409, extra={"kind": kind})
            with patch.object(oauth, "broker", return_value=Server(refusal)):
                with self.assertRaises(oauth.SignInFailed) as caught:
                    auth.mint_token("ana@acme.example", "gmail")
            self.assertEqual(caught.exception.kind, expected)
        with patch.object(oauth, "broker", return_value=Server(APIError("forbidden", "no", 403))):
            with self.assertRaises(Failure) as caught:
                auth.mint_token("ana@acme.example", "gmail")
        self.assertIn("forbidden", caught.exception.msg)

    def test_without_the_runner_s_registration_it_says_so(self):
        self.finish()
        for name in (oauth.BROKER_URL_ENV, oauth.BROKER_AUTH_ENV):
            os.environ.pop(name, None)
        with self.assertRaises(Failure) as caught:
            auth.mint_token("ana@acme.example", "gmail")
        self.assertIn("connected through Tico's server", caught.exception.msg)
        os.environ[oauth.BROKER_URL_ENV], os.environ[oauth.BROKER_AUTH_ENV] = "https://team.acme.example", "rt"
        self.assertEqual(oauth.broker().url, "https://team.acme.example")

    def test_a_self_hosted_desktop_client_grant_still_refreshes_here(self):
        self.connect()
        grant = oauth.grant("ana@acme.example")
        self.assertEqual(grant["client_secret"], CLIENT["client_secret"])
        self.assertNotIn("broker", grant)
        with patch.object(oauth, "broker", side_effect=AssertionError("the server must not be asked")):
            auth.mint_token("ana@acme.example", "gmail")
        self.assertEqual(FakeCredentials.made[-1].refresh_token, "1//refresh-secret")


class AdminPolicy(Base):
    def test_a_workspace_policy_block_is_named_as_the_admins_and_nothing_is_widened(self):
        def blocked(_person, mailbox):
            raise oauth.refused("admin_policy", mailbox, "admin_policy_enforced")
        result = oauth.finish(dict(SERVER_CONNECT), opener=FakeGoogle(), exchange_with=blocked)
        self.assertEqual((result["status"], result["kind"]), ("failed", "admin_policy"))
        self.assertIn("Workspace admin for acme.example", result["hint"])
        self.assertFalse(self.grants.exists())
        flow = oauth.authorization(CLIENT, "ana@acme.example", [])
        with self.assertRaises(oauth.SignInFailed) as caught:     # the redirect's own error code
            oauth.code_from(f"https://x/?error=admin_policy_enforced&state={flow['state']}", flow["state"])
        self.assertEqual(caught.exception.kind, "admin_policy")
        self.connect()
        FakeCredentials.refresh_error = RefreshError("admin_policy_enforced: blocked", {})
        with self.assertRaises(oauth.SignInFailed) as caught:     # a block added after connecting
            auth.mint_token("ana@acme.example", "gmail")
        self.assertEqual(caught.exception.kind, "admin_policy")
        self.assertEqual([c.scopes for c in FakeCredentials.made], [[G + "gmail.readonly"]], "never widened")

    def test_finish_connects_and_reports_no_token(self):
        result = oauth.finish(dict(SERVER_CONNECT), opener=FakeGoogle(), exchange_with=lambda *_: dict(GRANTED))
        self.assertEqual(result["status"], "connected")
        self.assertNotIn("refresh-secret", json.dumps(result))


class Doctor(Base):
    def test_doctor_passes_with_a_connected_account_and_no_service_account_key(self):
        self.connect()

        class Gmail:
            def profile(self):
                return {"emailAddress": "ana@acme.example", "messagesTotal": 7}

            def label_ids(self):
                return {}

        class Calendars:
            def calendarList(self):
                return self

            def list(self, **_k):
                return self

            def execute(self):
                return {}
        with patch.object(cli.access, "mailbox_holders", return_value={"ana@acme.example": ["inbox"]}), \
                patch.object(cli.lb, "wanted", return_value=[]), \
                patch.object(cli, "open_gmail", return_value=Gmail()), \
                patch.object(cli, "open_calendar", return_value=Calendars()), \
                patch.dict(sys.modules, {"googleapiclient": types.ModuleType("googleapiclient")}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli.cmd_doctor(Namespace(e2e=False, mailbox=None, all_mailboxes=False, json=True))
        rows = {r["name"] if "name" in r else r.get("check"): r for r in json.loads(out.getvalue())["checks"]}
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("ana@acme.example google account", json.dumps(rows))
        self.assertNotIn("1//refresh-secret", out.getvalue())


if __name__ == "__main__":
    unittest.main()
