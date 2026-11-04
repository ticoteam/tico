"""Settings > Google > What Tico may do in the mail connector (connectors/mail/oauth.py company_allows).

Google's scopes cannot tell Organize mail from Send mail (both are gmail.modify), and a computer's own service-account
key or a standalone grant never asks the server for a narrowed token. So the connector holds each box itself, from the
bot's mail setting in Tico (company_organize, company_send, company_calendar): a label, archive or draft with Organize
mail off, a send with Send mail off and a calendar write with Change calendar off are refused before Google is asked,
whichever credential the run holds. A server that says nothing leaves things as they were. A bot acts only as itself:
`--as <owner handle>` from a bot's run is refused, so the owner's exemptions are a person's alone.
"""

import json, os, sys, types, unittest
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
        for name in ("HUB_BOT", "HUB_EMPLOYEE", "HUB_API_URL", "HUB_TOKEN"):
            os.environ.pop(name, None)
        access._ME.clear()
        self.addCleanup(access._ME.clear)

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
        self.assertEqual(len(self.service.sent), 1)
        self.assertIn("hub/handled/inbox", self.service.labels_by_id.values())
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


class NoBotActsAsTheOwner(Base):
    """`--as <owner handle>` holds every verb and skips the company's Send mail: a person's only."""

    def test_a_bot_run_as_the_owner_is_refused_and_sends_nothing_while_send_mail_is_off(self):
        self.company(company_send=False)
        os.environ["HUB_BOT"] = "inbox"
        rc, out, _ = self.reply("--as", access.OWNER, "--mailbox", BOX)
        self.assertEqual(rc, 2, out)
        self.assertIn("may not act as the owner", out)
        self.assertEqual(self.service.sent, [])
        # Without HUB_BOT the bot's token still says whose run it is.
        os.environ.pop("HUB_BOT")
        os.environ.update(HUB_API_URL="http://127.0.0.1:9", HUB_TOKEN="bot-turn-token")
        pl.HUB_GET = lambda path: {"actor": "bot:inbox", "role": "bot", "slug": "inbox"} if path == "me" else None
        rc, out, _ = self.reply("--as", access.OWNER, "--mailbox", BOX)
        self.assertEqual(rc, 2, out)
        self.assertEqual(self.service.sent, [])
        # Whatever reaches the policy as the owner from a bot's run is read as that bot.
        sw = pl.switch(access.OWNER)
        self.assertTrue(sw["company_off"])
        self.assertEqual(pl.company(access.OWNER)["send_mail"], False)

    def test_a_person_s_run_is_still_the_owner(self):
        self.company(company_send=False)
        rc, out, err = self.run_cli("whoami", "--as", access.OWNER, "--json")
        self.assertEqual(rc, 0, out + err)
        os.environ.update(HUB_API_URL="http://127.0.0.1:9", HUB_TOKEN="person-token")
        pl.HUB_GET = lambda path: {"actor": "human:ana", "role": "human"} if path == "me" else None
        self.assertEqual(access.employee(access.OWNER), access.OWNER)
        self.assertTrue(pl.switch(access.OWNER)["on"])
        self.assertIsNone(pl.company(access.OWNER))


if __name__ == "__main__":
    unittest.main()
