import contextlib
import io
import os
import sys
import types
import unittest
from argparse import Namespace
from unittest.mock import patch

from connectors.mail import Failure, auth
from connectors.mail import __main__ as cli
from runner import credential_socket


class DoctorSocket(unittest.TestCase):
    def setUp(self):
        self.originals = {
            "holders": cli.access.mailbox_holders,
            "open_gmail": cli.open_gmail,
            "open_calendar": cli.open_calendar,
            "wanted": cli.lb.wanted,
            "ensure": cli.lb.ensure,
        }
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.google_modules = patch.dict(sys.modules, {
            "google": types.ModuleType("google"),
            "google.oauth2": types.ModuleType("google.oauth2"),
            "googleapiclient": types.ModuleType("googleapiclient"),
        })
        self.google_modules.start()

    def tearDown(self):
        cli.access.mailbox_holders = self.originals["holders"]
        cli.open_gmail = self.originals["open_gmail"]
        cli.open_calendar = self.originals["open_calendar"]
        cli.lb.wanted = self.originals["wanted"]
        cli.lb.ensure = self.originals["ensure"]
        self.google_modules.stop()
        self.env.stop()

    def run_doctor(self, mailbox="ana@example.com"):
        output = io.StringIO()
        args = Namespace(e2e=False, mailbox=mailbox, all_mailboxes=False, json=False)
        with contextlib.redirect_stdout(output):
            result = cli.cmd_doctor(args)
        return result, output.getvalue()

    def use_socket_mode(self):
        os.environ[auth.SOCKET_ENV] = "/tmp/credential.sock"
        os.environ["HUB_TOKEN"] = "synthetic-token"

    def configure_doctor(self, gmail=None, wanted=(), existing_labels=None):
        class Gmail:
            def profile(self):
                return {"emailAddress": "ana@example.com", "messagesTotal": 3}
            def label_ids(self):
                return existing_labels or {}
        class CalendarList:
            def list(self, **_kwargs):
                return self
            def execute(self):
                return {}
        class Calendar:
            def calendarList(self):
                return CalendarList()
        cli.access.mailbox_holders = lambda: {"ana@example.com": ["influencer"]}
        cli.open_gmail = gmail or (lambda _box: Gmail())
        cli.open_calendar = lambda _box: Calendar()
        cli.lb.wanted = lambda _slug: list(wanted)
        cli.lb.ensure = lambda _g, _want: ([], [])

    def test_socket_doctor_never_reads_supervisor_key(self):
        os.environ[auth.SOCKET_ENV] = "/tmp/credential.sock"
        os.environ["HUB_TOKEN"] = "synthetic-token"
        self.configure_doctor()
        with patch.object(auth, "key_info", side_effect=AssertionError("must not read key")):
            result, output = self.run_doctor()
        self.assertEqual(result, 0)
        self.assertIn("runner supervisor socket", output)
        self.assertNotIn("service-account key", output)

    def test_direct_key_mode_keeps_key_diagnostics(self):
        self.configure_doctor()
        info = {"path": "/tmp/service.json", "client_email": "svc@example.com",
                "client_id": "123", "project_id": "project"}
        with patch.object(auth, "key_info", return_value=info) as key_info:
            result, output = self.run_doctor()
        self.assertEqual(result, 0)
        key_info.assert_called_once_with()
        self.assertIn("client id", output)

    def test_missing_labels_are_reported_with_repair_hint_without_writing(self):
        self.use_socket_mode()
        self.configure_doctor(
            wanted=["hub/drafted", "hub/noise"],
            existing_labels={"hub/drafted": "label-1"},
        )
        with patch.object(cli.lb, "ensure") as ensure:
            result, output = self.run_doctor()
        self.assertEqual(result, 1)
        self.assertIn("1 missing: hub/noise", output)
        self.assertIn("Create the missing hub/* labels in Gmail", output)
        ensure.assert_not_called()

    def test_labels_read_failure_is_a_clear_non_crashing_check(self):
        self.use_socket_mode()
        class BrokenGmail:
            def profile(self):
                return {"emailAddress": "ana@example.com", "messagesTotal": 3}
            def label_ids(self):
                raise RuntimeError("private service detail")
        self.configure_doctor(gmail=lambda _box: BrokenGmail(), wanted=["hub/drafted"])
        result, output = self.run_doctor()
        self.assertEqual(result, 1)
        self.assertIn("hub labels: unexpected RuntimeError", output)
        self.assertIn("Check mailbox label access", output)
        self.assertNotIn("private service detail", output)

    def test_denied_mailbox_and_unexpected_socket_failure_are_clear_and_non_crashing(self):
        os.environ[auth.SOCKET_ENV] = "/tmp/credential.sock"
        os.environ["HUB_TOKEN"] = "synthetic-token"
        def denied(_box):
            raise Failure("runner denied this mailbox", "Ask an owner to grant it.")
        self.configure_doctor(gmail=denied)
        result, output = self.run_doctor("outsider@example.com")
        self.assertEqual(result, 1)
        self.assertIn("runner denied this mailbox", output)
        self.configure_doctor(gmail=lambda _box: (_ for _ in ()).throw(RuntimeError("private details")))
        result, output = self.run_doctor()
        self.assertEqual(result, 1)
        self.assertIn("unexpected RuntimeError", output)
        self.assertNotIn("private details", output)

    def test_supervisor_token_is_scoped_and_denial_names_only_allowed_mailboxes(self):
        os.environ[auth.SOCKET_ENV] = "/tmp/credential.sock"
        os.environ["HUB_TOKEN"] = "synthetic-token"
        with patch.object(credential_socket, "request_mail",
                          return_value={"token": "synthetic-access-token"}) as request:
            self.assertEqual(auth.supervisor_token("ana@example.com", auth.SERVICE_SCOPES["gmail"]),
                             {"token": "synthetic-access-token"})
        request.assert_called_once_with("/tmp/credential.sock", "synthetic-token", "gmail", "ana@example.com")
        with patch.object(credential_socket, "request_mail",
                          side_effect=credential_socket.MailRefused("not this bot's mailbox",
                                                                     ["ana@example.com"])):
            with self.assertRaisesRegex(Failure, "may use ana@example.com"):
                auth.supervisor_token("outsider@example.com", auth.SERVICE_SCOPES["gmail"])

    def test_configured_socket_without_hub_token_fails_closed(self):
        os.environ[auth.SOCKET_ENV] = "/tmp/credential.sock"
        with patch.object(auth, "read_key", side_effect=AssertionError("must not read supervisor key")):
            with self.assertRaisesRegex(Failure, "HUB_TOKEN is missing"):
                auth.supervisor_token("ana@example.com", auth.SERVICE_SCOPES["gmail"])


if __name__ == "__main__":
    unittest.main()
