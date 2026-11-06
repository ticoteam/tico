"""Settings > Inboxes > Settings > What Tico may do in the mail connector (connectors/mail/oauth.py company_allows).

Google's scopes cannot tell Organize mail from Send mail (both are gmail.modify), and a computer's own service-account
key or a standalone grant never asks the server for a narrowed token. So the connector holds each box itself, from the
bot's mail setting in Tico (company_organize, company_send, company_calendar): a label, archive or draft with Organize
mail off, a send with Send mail off and a calendar write with Change calendar off are refused before Google is asked,
whichever credential the run holds. A server that answers without the fields (an older one) leaves things as they were;
a setting that cannot be read refuses the run's writes and lets it read. `--as <owner handle>` is held the same way, from
Tico over the runner's registration (policy.runner_company): the settings apply to everyone who acts through Tico.
"""

import io, json, os, sys, types, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake, harness                                                 # noqa: E402
import test_workspace as tw                                          # noqa: E402
from harness import MANIFESTS, Stage2                                # noqa: E402

from connectors.mail import access, auth, oauth, policy as pl        # noqa: E402
from connectors.mail import __main__ as cli                          # noqa: E402

BOX = "ana@acme.example"
AVA = "ava@creator.example"
THREAD = "t-ava"
INCOMING = {"From": "Ava Reyes <ava@creator.example>", "To": BOX, "Subject": "Support question",
            "Message-ID": "<ava-1@creator.example>", "Date": "Tue, 02 Sep 2026 09:00:00 -0700"}
BODY = "Hi Ava,\n\nThanks for writing in. Support has it and will answer within a day.\n\nAna\nFounder, Acme\n"
INBOX = """
name: inbox
outbound_send: true
access:
  - service: gmail
    identity: "ana@acme.example"
    can: [read, draft, send]
  - service: google-calendar
    identity: "ana@acme.example"
    can: [read, schedule]
"""
ALL_ON = {"company_organize": True, "company_send": True, "company_calendar": True}


class Base(Stage2):
    """A bot (inbox) that may read, draft and send as ana@acme.example and schedule on her calendar."""
    manifests = dict(MANIFESTS, inbox=INBOX)

    def setUp(self):
        super().setUp()
        for name in ("HUB_BOT", "HUB_EMPLOYEE", "HUB_API_URL", "HUB_TOKEN", oauth.BROKER_URL_ENV, oauth.BROKER_AUTH_ENV):
            os.environ.pop(name, None)

    def corpus(self):
        return [fake.message("m-ava", THREAD, INCOMING, body="Can you help?", epoch_ms=1788364800000)]

    def company(self, **changes):
        """The bot's mail setting with What Tico may do as the test says; None leaves the fields out."""
        def get(slug):
            row = dict(self.confirmed_setting(slug), **ALL_ON)
            for field, value in changes.items():
                if value is None:
                    row.pop(field, None)
                else:
                    row[field] = value
            return row
        pl.SERVER_GET = get

    def archive(self):
        return self.run_cli("archive", "--as", "inbox", "--mailbox", BOX, "m-ava", "--json")

    def reply(self, *who):
        return self.run_cli("reply", *(who or ("--as", "inbox")), "--thread", THREAD,
                            "--body-file", self.body_file(BODY), "--issue", "1", "--json")

    def hold(self):
        return self.run_cli("hold", "--as", "inbox", "--for", BOX, "--start", "2026-09-22T09:00:00-07:00",
                            "--minutes", "30", "--summary", "Planning hold", "--json")

    def in_inbox(self):
        return "INBOX" in self.service.store["m-ava"]["labelIds"]


class CompanyGates(Base):
    def test_organize_off_with_send_on_refuses_labels_and_archive_but_a_reply_still_sends(self):
        self.company(company_organize=False)
        rc, out, _ = self.archive()
        self.assertEqual(rc, 2, out)
        self.assertIn("Organize mail", out)
        self.assertTrue(self.in_inbox())
        rc, out, _ = self.run_cli("label", "--as", "inbox", "--mailbox", BOX, "--json", "add", "m-ava", "hub/billing")
        self.assertEqual(rc, 2, out)
        # A draft is the first step of every send, so Send mail on allows the reply's, and Tico's own markers on it.
        rc, out, err = self.reply()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.queue_requests), 1)
        self.assertEqual(self.service.sent, [])
        self.company()
        self.assertEqual(self.archive()[0], 0)
        self.assertFalse(self.in_inbox())

    def test_organize_and_send_off_refuse_a_draft_before_gmail_is_asked(self):
        self.company(company_organize=False, company_send=False)
        rc, out, _ = self.run_cli("draft", "--as", "inbox", "--reply-to", THREAD, "--body-file", self.body_file(BODY),
                                  "--issue", "1", "--json")
        self.assertEqual(rc, 2, out)
        self.assertIn("Organize mail or Send mail", out)
        self.assertEqual(self.service.drafts_by_id, {})

    def test_change_calendar_off_refuses_a_hold(self):
        self.company(company_calendar=False)
        rc, out, _ = self.hold()
        self.assertEqual(rc, 2, out)
        self.assertIn("Create and change calendar events", out)
        self.assertEqual(self.calendar.created_events, {})
        self.company()
        rc, out, err = self.hold()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.calendar.created_events), 1)

    def test_a_server_that_says_nothing_leaves_every_write_as_it_was(self):
        self.company(company_organize=None, company_send=None, company_calendar=None)
        self.assertEqual(self.archive()[0], 0)
        self.assertEqual(self.hold()[0], 0)

    def test_a_setting_tico_cannot_read_refuses_a_bot_s_writes_and_still_lets_it_read(self):
        def down(slug):
            raise OSError("Tico is unreachable")
        for get in (down, lambda slug: None):
            pl.SERVER_GET = get
            rc, out, _ = self.archive()
            self.assertEqual(rc, 1, out)
            self.assertIn("Tico couldn't check the company's mail settings; try again", out)
            self.assertEqual(self.hold()[0], 1)
            self.assertTrue(self.in_inbox())
            self.assertEqual(self.calendar.created_events, {})
            rc, out, err = self.run_cli("inbox", "--as", "inbox", "--mailbox", BOX, "--json")
            self.assertEqual(rc, 0, out + err)

    def test_a_runner_s_calendar_action_reads_change_calendar_as_the_runner_and_an_unread_one_is_refused(self):
        """connector-event runs as the connector, so What Tico may do comes from the runner's own registration
        (connectors/mail/targets), as a mail action's does; nothing on its stdin decides it."""
        action = json.dumps({"id": "ca-1", "calendar": BOX, "title": "Planning", "attendees": [],
                             "start": "2026-09-22T09:00:00-07:00", "end": "2026-09-22T09:30:00-07:00",
                             "company": ALL_ON})

        def event(answer):
            def get(path):
                if isinstance(answer, Exception):
                    raise answer
                return answer
            pl.RUNNER_GET = get
            with mock.patch.object(sys, "stdin", io.StringIO(action)):
                return self.run_cli("connector-event", "--as", access.CONNECTOR, "--json")
        off = {"organize_mail": True, "send_mail": True, "change_calendar": False}
        for answer, says in (({"company": off}, "Create and change calendar events"),
                             (OSError("Tico is unreachable"), "Tico couldn't check")):
            rc, out, _ = event(answer)
            self.assertEqual(rc, 2, out)            # exit 2: the runner reports it failed, not unknown
            self.assertIn(says, out)
            self.assertEqual(self.calendar.created_events, {})
        rc, out, err = event({"company": dict(off, change_calendar=True)})
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.calendar.created_events), 1)

    def test_a_computer_s_own_key_and_a_standalone_grant_are_held_to_it_too(self):
        """The real open_gmail: auth.gmail_service builds from this computer's own credential, which the server never
        narrowed, and the connector still refuses what the company turned off."""
        key, grants = self.root / "google-sa.json", self.root / "google-oauth.json"
        os.environ.update(GOOGLE_SA_KEY=str(key), **{oauth.GRANTS_ENV: str(grants)})
        for name in (auth.SOCKET_ENV, oauth.BROKER_URL_ENV, oauth.BROKER_AUTH_ENV):
            os.environ.pop(name, None)
        modules = tw.google_modules()
        modules["googleapiclient"] = types.ModuleType("googleapiclient")
        modules["googleapiclient.discovery"] = discovery = types.ModuleType("googleapiclient.discovery")
        built = []
        discovery.build = lambda api, version, credentials=None, **_: built.append(credentials) or self.service
        cli.open_gmail = self._saved["gmail"]
        self.company(company_organize=False)
        with mock.patch.dict(sys.modules, modules):
            # This computer's service-account key: Google would take gmail.modify, so only Tico can refuse.
            key.write_text(json.dumps({"type": "service_account", "project_id": "acme-hub", "private_key_id": "k1",
                                       "private_key": "-----BEGIN PRIVATE KEY-----\ntest-only\n-----END PRIVATE KEY-----\n",
                                       "client_email": "tico-mail@acme-hub.example.iam.gserviceaccount.com",
                                       "client_id": "100000000000000000001", "token_uri": oauth.TOKEN_URI}))
            key.chmod(0o600)
            rc, out, _ = self.archive()
            self.assertEqual(rc, 2, out)
            self.assertIn("Organize mail", out)
            self.assertEqual(getattr(built[-1], "token", ""), "ya29.key-" + BOX)
            # A person's own grant, refreshed on this computer with the write scope it holds.
            oauth.save({"grants": {BOX: {"refresh_token": "1//refresh-test", "client_id": "123.apps.example",
                                         "client_secret": "test-only", "token_uri": oauth.TOKEN_URI,
                                         "scopes": tw.GRANTED}}}, grants)
            rc, out, _ = self.archive()
            self.assertEqual(rc, 2, out)
            self.assertIn("Organize mail", out)                 # Google's scopes allow it; the company does not
            self.assertEqual(getattr(built[-1], "_tico_granted", None), oauth.SERVICE_SCOPES["gmail"])
            self.assertTrue(self.in_inbox())
            self.company()
            self.assertEqual(self.archive()[0], 0)
        self.assertFalse(self.in_inbox())


class TheConnectorIsHeldToo(Base):
    """`--as connector` (the runner's connector job) reads What Tico may do over the runner's registration
    (connectors/mail/targets), and writes only what the server leased it; no token, name or answer on stdin exempts it.
    The owner's handle holds no mail rights of its own."""

    def runner(self, answer):
        def get(path):
            self.assertEqual(path, "connectors/mail/targets")
            if isinstance(answer, Exception):
                raise answer
            return answer
        pl.RUNNER_GET = get

    def leased(self, *argv):
        access.LEASED = True                            # as mail-action / connector-event set it (main() clears it)
        return self.run_cli(*argv)

    def connector_archive(self):
        return self.leased("archive", "--as", access.CONNECTOR, "--mailbox", BOX, "m-ava", "--json")

    def test_the_connector_s_leased_writes_follow_what_tico_may_do(self):
        self.runner({"company": {"organize_mail": False, "send_mail": False, "change_calendar": True}})
        rc, out, _ = self.connector_archive()
        self.assertEqual(rc, 2, out)
        self.assertIn("Organize mail", out)
        access.LEASED = True
        rc, out, _ = self.reply("--as", access.CONNECTOR, "--mailbox", BOX)
        self.assertEqual(rc, 2, out)
        self.assertEqual((self.service.sent, self.service.drafts_by_id), ([], {}))
        self.assertTrue(self.in_inbox())
        self.runner({"company": dict.fromkeys(pl.COMPANY_FIELDS, True)})
        rc, out, err = self.connector_archive()
        self.assertEqual(rc, 0, out + err)
        self.assertFalse(self.in_inbox())

    def test_the_connector_writes_nothing_without_a_lease(self):
        rc, out, _ = self.run_cli("archive", "--as", access.CONNECTOR, "--mailbox", BOX, "m-ava", "--json")
        self.assertEqual(rc, 2, out)
        self.assertTrue(self.in_inbox())
        self.assertFalse(access.LEASED)

    def test_the_owner_handle_holds_nothing(self):
        rc, out, _ = self.run_cli("inbox", "--as", access.OWNER, "--mailbox", BOX, "--json")
        self.assertEqual(rc, 2, out)
        self.assertIn("Settings > Inboxes", out)

    def test_a_connector_run_tico_cannot_be_asked_for_writes_nothing_and_still_reads(self):
        """Without the runner's registration (a terminal) or with Tico down, the connector's writes are refused like a
        bot's."""
        for answer in (OSError("Tico is unreachable"), None):
            if answer is None:
                pl.RUNNER_GET = None                        # the real read: no broker URL or registration here
                for name in (oauth.BROKER_URL_ENV, oauth.BROKER_AUTH_ENV):
                    os.environ.pop(name, None)
            else:
                self.runner(answer)
            rc, out, _ = self.connector_archive()
            self.assertEqual(rc, 1, out)
            self.assertIn("Tico couldn't check the company's mail settings; try again", out)
            self.assertTrue(self.in_inbox())
            rc, out, err = self.run_cli("inbox", "--as", access.CONNECTOR, "--mailbox", BOX, "--json")
            self.assertEqual(rc, 0, out + err)


if __name__ == "__main__":
    unittest.main()
