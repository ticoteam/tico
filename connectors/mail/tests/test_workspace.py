"""Google access on a computer with no key file of its own (connectors/mail/auth.py credentials, oauth.server_workspace_token).

The order is: the runner's supervisor, a person's own connected account, this computer's key file, and only when there is
no key file and the runner gave this process its way to the server, the Google Workspace connection on the server. That
server refuses a mailbox it does not cover with a 403 whose kind mint-token prints as error_kind. A refusal never falls
back to another credential.
"""
import contextlib
import http.server
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from clients.tico import APIError
from connectors.mail import Failure, auth, oauth
from connectors.mail import __main__ as cli
from runner_support import credential_socket

# Imported before any test runs: unittest's patch.dict(sys.modules) drops modules first imported during a test, and
# importing cryptography's native module a second time in one process breaks its key serialization.
try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:             # the mail suite runs without it: the key file then holds a placeholder, read by path only
    serialization = rsa = None

GMAIL = auth.SERVICE_SCOPES["gmail"]
CALENDAR = auth.SERVICE_SCOPES["calendar"]
GRANTED = oauth.SERVICE_SCOPES["gmail"] + oauth.SERVICE_SCOPES["calendar"]
REFUSED = {"eve@acme.example": "not_this_mailbox", "ben@acme.example": "personal_grant",
           "cal@acme.example": "workspace_owned", "dan@acme.example": "not_connected"}


class RefreshError(Exception):
    pass


class TransportError(Exception):
    pass


class FakeCredentials:
    """google.oauth2.credentials.Credentials: refresh() answers as the test says."""
    refresh_error = None

    def __init__(self, token=None, refresh_token=None, token_uri=None, client_id=None, client_secret=None,
                 scopes=None, expiry=None):
        self.token, self.refresh_token, self.scopes, self.expiry = token, refresh_token, scopes, expiry
        self.client_id = client_id

    def refresh(self, _request):
        if FakeCredentials.refresh_error:
            raise FakeCredentials.refresh_error
        self.token = "ya29.refreshed"


class ServiceAccount:
    """google.oauth2.service_account.Credentials: the key file's credential, acting as one mailbox at a time."""
    loaded = []

    @classmethod
    def from_service_account_file(cls, path, scopes=None):
        cls.loaded.append((path, scopes))
        return cls()

    def with_subject(self, mailbox):
        return FakeCredentials(token="ya29.key-" + mailbox)


def google_modules():
    modules = {name: types.ModuleType(name) for name in (
        "google", "google.auth", "google.auth.exceptions", "google.auth.transport",
        "google.auth.transport.requests", "google.oauth2", "google.oauth2.credentials",
        "google.oauth2.service_account")}
    modules["google.auth.exceptions"].RefreshError = RefreshError
    modules["google.auth.exceptions"].TransportError = TransportError
    modules["google.auth.transport.requests"].Request = lambda: None
    modules["google.oauth2.credentials"].Credentials = FakeCredentials
    modules["google.oauth2.service_account"].Credentials = ServiceAccount
    modules["google.oauth2"].service_account = modules["google.oauth2.service_account"]
    modules["google.oauth2"].credentials = modules["google.oauth2.credentials"]
    return modules


class Server:
    """Tico's server as oauth.broker() reaches it, for the Google Workspace token route: recorded, and answering as the
    test says (`refuse` is the kind of a 403; `omit_scopes` is an answer that names no scopes)."""

    def __init__(self):
        self.calls, self.refuse, self.omit_scopes = [], None, False

    def post(self, path, body):
        self.calls.append((path, body))
        if self.refuse:
            raise APIError(self.refuse, f"refused {body['mailbox']}", status=403)
        answer = {"access_token": "ya29.workspace", "expiry": "2026-10-16T01:00:00Z"}
        if not self.omit_scopes:
            answer["scopes"] = list(auth.SERVICE_SCOPES[body["service"]])
        return answer


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.key = self.dir / "google-sa.json"
        self.grants = self.dir / "google-oauth.json"
        env = patch.dict(os.environ, {"GOOGLE_SA_KEY": str(self.key)})
        env.start()
        self.addCleanup(env.stop)
        for name in (auth.SOCKET_ENV, oauth.GRANTS_ENV, "HUB_TOKEN", oauth.BROKER_URL_ENV, oauth.BROKER_AUTH_ENV):
            os.environ.pop(name, None)
        modules = patch.dict(sys.modules, google_modules())
        modules.start()
        self.addCleanup(modules.stop)
        FakeCredentials.refresh_error = None
        ServiceAccount.loaded = []
        self.server = Server()

    def with_registration(self):
        """This computer's runner registration with the server, and the server as oauth.broker() gives it."""
        os.environ[oauth.BROKER_URL_ENV] = "https://tico.example"
        os.environ[oauth.BROKER_AUTH_ENV] = "runner-token"
        stub = patch.object(oauth, "broker", new=lambda retries=1: self.server)
        stub.start()
        self.addCleanup(stub.stop)

    def write_key(self):
        """A key file in the shape Google gives, with a throwaway generated RSA key; it is read by path, never sent anywhere."""
        pem = "-----BEGIN PRIVATE KEY-----\ntest-only placeholder\n-----END PRIVATE KEY-----\n"
        if rsa is not None:
            pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        self.key.write_text(json.dumps({
            "type": "service_account", "project_id": "acme-hub", "private_key_id": "k1",
            "private_key": pem, "client_email": "tico-mail@acme-hub.example.iam.gserviceaccount.com",
            "client_id": "100000000000000000001", "token_uri": oauth.TOKEN_URI}))
        self.key.chmod(0o600)

    def write_grant(self, mailbox="ana@acme.example"):
        """A person's own connected account, as oauth.save keeps it."""
        oauth.save({"grants": {mailbox: {
            "refresh_token": "1//refresh-secret", "client_id": "123.apps.googleusercontent.com",
            "client_secret": "client-secret-value", "token_uri": oauth.TOKEN_URI, "scopes": GRANTED}}}, self.grants)


class CredentialOrder(Base):
    def credentials(self, mailbox="ana@acme.example"):
        return auth.credentials(mailbox, GMAIL)

    def test_a_connected_account_is_served_before_the_key_and_the_server(self):
        self.with_registration()
        self.write_grant()
        self.write_key()
        creds = self.credentials()
        self.assertEqual(creds.token, "ya29.refreshed")
        self.assertEqual(creds._tico_granted, oauth.SERVICE_SCOPES["gmail"])   # the Gmail scopes only, not calendar
        self.assertEqual(self.server.calls, [])
        self.assertEqual(ServiceAccount.loaded, [])

    def test_a_refused_connected_account_never_falls_back_to_the_server_or_the_key(self):
        self.with_registration()
        self.write_grant()
        FakeCredentials.refresh_error = RefreshError("invalid_grant: Token has been expired or revoked.")
        with self.assertRaises(oauth.SignInFailed) as caught:
            self.credentials()
        self.assertEqual(caught.exception.kind, "signin")
        self.assertEqual(self.server.calls, [])
        self.assertEqual(ServiceAccount.loaded, [])

    def test_a_key_file_on_this_computer_is_used_and_the_server_is_not_asked(self):
        self.with_registration()
        self.write_key()
        creds = self.credentials()
        self.assertEqual(creds.token, "ya29.key-ana@acme.example")
        self.assertEqual(self.server.calls, [])
        self.assertEqual(ServiceAccount.loaded[0][0], str(self.key))

    def test_no_key_file_with_the_runner_s_registration_asks_the_server_for_a_workspace_token(self):
        self.with_registration()
        creds = self.credentials()
        self.assertEqual(creds.token, "ya29.workspace")
        self.assertEqual(self.server.calls, [("connectors/mail/workspace/token",
                                              {"mailbox": "ana@acme.example", "service": "gmail"})])
        self.assertEqual(creds._tico_granted, GMAIL)
        self.assertEqual(ServiceAccount.loaded, [])
        self.assertFalse(self.key.exists())

    def test_no_key_and_no_registration_says_where_the_key_goes(self):
        with self.assertRaises(Failure) as caught:
            self.credentials()
        self.assertIn("service-account key is not at", caught.exception.msg)
        self.assertEqual(self.server.calls, [])

    def test_the_server_s_refusal_reaches_the_caller_as_its_kind_and_no_key_is_used(self):
        self.with_registration()
        self.server.refuse = "personal_grant"
        with self.assertRaises(oauth.SignInFailed) as caught:
            self.credentials()
        self.assertEqual(caught.exception.kind, "personal_grant")
        self.assertIn("ana@acme.example", caught.exception.msg)
        self.assertEqual(ServiceAccount.loaded, [])


class MintWithoutKey(Base):
    def test_mint_token_on_a_computer_with_no_key_gives_the_server_s_token_for_each_service(self):
        self.with_registration()
        token, expiry, granted = auth.mint_token("ana@acme.example", "gmail")
        self.assertEqual((token, expiry, granted), ("ya29.workspace", "2026-10-16T01:00:00", GMAIL))
        token, _expiry, granted = auth.mint_token("ana@acme.example", "calendar")
        self.assertEqual((token, granted), ("ya29.workspace", CALENDAR))
        self.assertEqual([body["service"] for _path, body in self.server.calls], ["gmail", "calendar"])
        self.assertEqual(ServiceAccount.loaded, [])

    def test_a_server_answer_without_scopes_reads_as_no_narrowing(self):
        self.with_registration()
        self.server.omit_scopes = True
        _token, _expiry, granted = auth.mint_token("ana@acme.example", "gmail")
        self.assertIsNone(granted)

    def test_both_services_at_once_are_refused_before_the_server_is_asked(self):
        self.with_registration()
        with self.assertRaises(Failure) as caught:
            auth.credentials("ana@acme.example", GMAIL + CALENDAR)
        self.assertIn("one service at a time", caught.exception.msg)
        self.assertEqual(self.server.calls, [])


class WorkspaceRefusals(Base):
    def test_each_refusal_kind_is_a_failure_that_names_the_mailbox_and_the_fix(self):
        self.assertEqual(set(oauth.WORKSPACE_REFUSALS), set(REFUSED.values()))
        for kind in oauth.WORKSPACE_REFUSALS:
            error = oauth.workspace_refused(kind, "eve@acme.example")
            self.assertIsInstance(error, oauth.SignInFailed)
            self.assertIsInstance(error, Failure)
            self.assertEqual(error.kind, kind)
            self.assertIn("eve@acme.example", error.msg)
            self.assertTrue(error.hint)
            self.assertNotIn("{m}", error.msg + error.hint)

    def test_the_supervisor_names_each_workspace_refusal_as_that_kind(self):
        os.environ[auth.SOCKET_ENV] = "/run/tico/credential.sock"
        os.environ["HUB_TOKEN"] = "synthetic-token"
        for kind in oauth.WORKSPACE_REFUSALS:
            with patch.object(credential_socket, "request_mail", side_effect=credential_socket.MailRefused(kind)):
                with self.assertRaises(oauth.SignInFailed) as caught:
                    auth.supervisor_token("eve@acme.example", GMAIL)
            self.assertEqual(caught.exception.kind, kind)

    def test_an_unknown_supervisor_refusal_stays_a_plain_failure(self):
        os.environ[auth.SOCKET_ENV] = "/run/tico/credential.sock"
        os.environ["HUB_TOKEN"] = "synthetic-token"
        with patch.object(credential_socket, "request_mail", side_effect=credential_socket.MailRefused("new_kind")):
            with self.assertRaises(Failure) as caught:
                auth.supervisor_token("eve@acme.example", GMAIL)
        self.assertNotIsInstance(caught.exception, oauth.SignInFailed)


class TokenRoute(http.server.BaseHTTPRequestHandler):
    """POST /api/v2/connectors/mail/workspace/token, answered as Tico's server answers it."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.seen.append((self.path, self.headers.get("Authorization"), body))
        if self.path != "/api/v2/connectors/mail/workspace/token":
            return self.reply(404, {"error": {"code": "not_found", "detail": "no such route"}})
        kind = REFUSED.get(body.get("mailbox"))
        if kind:
            return self.reply(403, {"error": {"code": kind, "detail": f"refused {body['mailbox']}"}})
        return self.reply(200, {"access_token": "ya29.workspace-" + body["mailbox"].split("@")[0],
                                "expiry": "2026-10-16T01:00:00Z", "scopes": list(auth.SERVICE_SCOPES[body["service"]])})

    def reply(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


class MintTokenOverTheServer(Base):
    """`mint-token` on a computer with no key, against a stub of Tico's server on loopback (plain http is allowed there)."""

    def setUp(self):
        super().setUp()
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), TokenRoute)
        httpd.seen = []
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.httpd = httpd
        # The local stub must be reached directly, never through a proxy the runner or CI sets.
        env = patch.dict(os.environ, {
            oauth.BROKER_URL_ENV: f"http://127.0.0.1:{httpd.server_address[1]}",
            oauth.BROKER_AUTH_ENV: "runner-token", "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost"})
        env.start()
        self.addCleanup(env.stop)

    def mint(self, mailbox, service="gmail"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["mint-token", "--mailbox", mailbox, "--service", service])
        return code, out.getvalue(), err.getvalue()

    def test_mint_token_on_a_computer_with_no_key_prints_the_server_s_token(self):
        code, out, err = self.mint("ana@acme.example")
        self.assertEqual(code, 0, err)
        printed = json.loads(out)
        self.assertEqual(printed, {"token": "ya29.workspace-ana", "expiry": "2026-10-16T01:00:00",
                                   "scopes": GMAIL})
        self.assertEqual(self.httpd.seen, [("/api/v2/connectors/mail/workspace/token", "Bearer runner-token",
                                            {"mailbox": "ana@acme.example", "service": "gmail"})])
        self.assertNotIn("runner-token", out + err)
        self.assertFalse(self.key.exists())

    def test_each_refusal_exits_1_and_prints_its_kind_without_a_token(self):
        for mailbox, kind in REFUSED.items():
            code, out, err = self.mint(mailbox)
            self.assertEqual(code, 1, mailbox)
            self.assertEqual(json.loads(out), {"error_kind": kind}, mailbox)
            self.assertIn(mailbox, err)
            self.assertNotIn("ya29.", out + err)


if __name__ == "__main__":
    unittest.main()
