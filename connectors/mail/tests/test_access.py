"""Who may act as which mailbox: Tico's answer (GET /api/v2/mail/access), narrowed by the bot's own bot.yaml, and the
connector job's answer over the runner's registration. The server's own rules are backend/tests/test_mailbox_access.py."""

import os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake                    # noqa: E402  (puts the hub on sys.path)

from connectors.mail import Refused                                  # noqa: E402
from connectors.mail import access, policy as pl                     # noqa: E402
from backend.people import load as load_people                      # noqa: E402

INFLUENCER = """
name: influencer
access:
  - service: gmail
    identity: "ana@acme.example"
    can: [read, draft]
"""
READER = """
name: reader
access:
  - service: gmail
    identity: "ana@acme.example"
    can: [read, draft, send]
    read_only: true
"""
NO_MAIL = """
name: doc-updater
access:
  - service: slack
    identity: "Acme workspace"
    can: [read, post]
"""
ROSTER = {"people": [{"id": "ana", "email": "ana@acme.example"}, {"id": "ben", "email": "ben@acme.example"}]}
ANA, BEN = "ana@acme.example", "ben@acme.example"


class Tree(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        for slug, text in (("influencer", INFLUENCER), ("reader", READER), ("doc-updater", NO_MAIL)):
            d = self.root / f"emp-{slug}"
            d.mkdir()
            (d / "employee.yaml").write_text(text)
        self._saved = (access.PROJECTS, access._roster, access.SERVER_ACCESS, access.RUNNER_ACCESS,
                       pl.RUNNER_GET, pl.SERVER_GET)
        access.PROJECTS = self.root
        access._roster = lambda: load_people(ROSTER)
        self.answers = {}                                # slug -> what the fake Tico answers
        access.SERVER_ACCESS = lambda slug: self.answers.get(slug) or {"mailboxes": []}
        access.LEASED = False
        access.forget()

    def tearDown(self):
        (access.PROJECTS, access._roster, access.SERVER_ACCESS, access.RUNNER_ACCESS,
         pl.RUNNER_GET, pl.SERVER_GET) = self._saved
        access.LEASED = False
        access.forget()
        self.dir.cleanup()

    def grant(self, slug, boxes, company=None):
        """Tico answers `slug` with these {address: level}."""
        self.answers[slug] = {"principal": f"bot:{slug}", "company": company or dict(fake.MAIL_COMPANY),
                              "mailboxes": [{"address": a, "level": lv} for a, lv in boxes.items()]}


class Server(Tree):
    def test_nothing_from_tico_is_nothing_whatever_bot_yaml_declares(self):
        with self.assertRaises(Refused) as e:
            access.resolve("influencer", ANA, "read")
        self.assertIn("Tools > the mailbox > Who can use it", e.exception.hint)
        self.assertEqual(access.readable_mailboxes("influencer"), [])

    def test_read_reads_and_nothing_else(self):
        self.grant("doc-updater", {ANA: "read"})
        self.assertEqual(access.resolve("doc-updater", ANA, "read")[0], ANA)
        for verb in ("label", "archive", "triaged", "draft", "send", "unsubscribe", "calendar_schedule"):
            with self.assertRaises(Refused, msg=verb):
                access.resolve("doc-updater", ANA, verb)

    def test_read_and_write_organizes_drafts_and_sends(self):
        self.grant("doc-updater", {ANA: "read_write"})
        for verb in ("read", "label", "archive", "triaged", "draft", "send", "unsubscribe"):
            self.assertIn(access.STATE_VERBS.get(verb, verb), access.resolve("doc-updater", ANA, verb)[1], verb)
        self.assertEqual(access.resolve("doc-updater", ANA, "calendar_schedule")[1], ["read", "schedule"])

    def test_an_unknown_level_is_nothing(self):
        self.grant("doc-updater", {ANA: "admin"})
        with self.assertRaises(Refused):
            access.resolve("doc-updater", ANA, "read")

    def test_bot_yaml_only_narrows(self):
        self.grant("influencer", {ANA: "read_write"})
        _, verbs = access.resolve("influencer", ANA, "draft")
        self.assertNotIn("send", verbs)                  # can: [read, draft] keeps sending out
        self.assertIn("organize", verbs)
        self.grant("reader", {ANA: "read_write"})
        self.assertEqual(access.resolve("reader", ANA, "read")[1], ["read"])
        # and never widens: a Read grant stays Read whatever `can:` lists
        self.grant("influencer", {ANA: "read"})
        with self.assertRaises(Refused):
            access.resolve("influencer", ANA, "draft")

    def test_a_mailbox_tico_did_not_name_is_refused(self):
        self.grant("influencer", {ANA: "read_write"})
        with self.assertRaises(Refused) as e:
            access.resolve("influencer", BEN, "read")
        self.assertIn(ANA, e.exception.hint)

    def test_two_mailboxes_need_a_choice(self):
        self.grant("doc-updater", {ANA: "read", BEN: "read"})
        with self.assertRaises(Refused):
            access.resolve("doc-updater", None, "read")
        self.assertEqual(sorted(access.readable_mailboxes("doc-updater")), [ANA, BEN])

    def test_the_owner_handle_has_no_bypass(self):
        with self.assertRaises(Refused):
            access.resolve(access.OWNER, ANA, "read")
        self.assertEqual(access.load(access.OWNER), {"name": access.OWNER, "access": []})

    def test_company_calendars_stay_readable(self):
        self.assertEqual(access.resolve("doc-updater", BEN, "calendar_read")[1], ["read"])
        with self.assertRaises(Refused):
            access.resolve("doc-updater", BEN, "calendar_schedule")

    def test_describe_lists_levels(self):
        self.grant("influencer", {ANA: "read_write"})
        got = access.describe("influencer")
        self.assertFalse(got["owner"])
        self.assertEqual(got["mailboxes"][0]["level"], "read_write")
        self.assertNotIn("send", got["mailboxes"][0]["gmail"])

    def test_no_credential_is_refused_not_open(self):
        access.SERVER_ACCESS = None
        with mock.patch.dict(os.environ):
            os.environ.pop("HUB_API_URL", None)
            os.environ.pop("HUB_TOKEN", None)
            with self.assertRaises(Refused) as e:
                access.resolve("influencer", ANA, "read")
        self.assertIn("no owner bypass", e.exception.hint)

    def test_the_hub_answer_is_asked_as_the_run_once(self):
        access.SERVER_ACCESS = None
        calls = []

        class Client:
            def __init__(self, url, token):
                calls.append((url, token))

            def get(self, path):
                calls.append(path)
                return {"mailboxes": [{"address": ANA, "level": "read"}]}

        with mock.patch.dict(os.environ, {"HUB_API_URL": "http://tico", "HUB_TOKEN": "tok"}), \
                mock.patch("clients.tico.Client", Client):
            self.assertEqual(access.readable_mailboxes("influencer"), [ANA])
            access.readable_mailboxes("influencer")
        self.assertEqual(calls, [("http://tico", "tok"), "mail/access"])

    def test_a_hub_that_fails_is_refused(self):
        access.SERVER_ACCESS = None

        class Client:
            def __init__(self, url, token):
                pass

            def get(self, path):
                raise OSError("down")

        with mock.patch.dict(os.environ, {"HUB_API_URL": "http://tico", "HUB_TOKEN": "tok"}), \
                mock.patch("clients.tico.Client", Client):
            with self.assertRaises(Refused):
                access.resolve("influencer", ANA, "read")


class Connector(Tree):
    def setUp(self):
        super().setUp()
        access.RUNNER_ACCESS = lambda: {"mailboxes": [{"address": ANA}], "company": None}

    def test_connector_reads_its_targets_only(self):
        self.assertEqual(access.resolve(access.CONNECTOR, ANA, "read")[0], ANA)
        with self.assertRaises(Refused):
            access.resolve(access.CONNECTOR, BEN, "read")

    def test_connector_writes_only_under_a_lease(self):
        for verb in ("archive", "draft", "send"):
            with self.assertRaises(Refused, msg=verb):
                access.resolve(access.CONNECTOR, ANA, verb)
        access.LEASED = True
        for verb in ("archive", "draft", "send"):
            access.resolve(access.CONNECTOR, ANA, verb)

    def test_a_turn_cannot_call_itself_the_connector(self):
        access.RUNNER_ACCESS = None
        with mock.patch.dict(os.environ, {"HUB_TOKEN": "turn-token"}):
            with self.assertRaises(Refused):
                access.resolve(access.CONNECTOR, ANA, "read")

    def test_connector_company_comes_from_the_runner(self):
        pl.RUNNER_GET = lambda path: {"company": {"organize_mail": False}}
        self.assertFalse(pl.company(access.CONNECTOR)["organize_mail"])


class PersonCompany(Tree):
    def test_a_persons_run_reads_what_tico_may_do_from_its_answer(self):
        pl.SERVER_GET = lambda slug: None                # not a bot: no mail setting
        self.answers["ana"] = {"mailboxes": [], "company": {"organize_mail": True, "send_mail": False}}
        self.assertEqual(pl.company("ana"), {"organize_mail": True, "send_mail": False, "change_calendar": True})
        self.answers["ana"] = {"mailboxes": []}
        self.assertEqual(pl.company("ana"), pl.UNREAD)


class Holders(Tree):
    def test_holdings_and_holders_read_bot_yaml(self):
        self.assertEqual(access.holdings("influencer", None)[ANA]["gmail"], ["read", "draft"])
        self.assertEqual(sorted(access.mailbox_holders(self.root)[ANA]), ["influencer", "reader"])


if __name__ == "__main__":
    unittest.main()
