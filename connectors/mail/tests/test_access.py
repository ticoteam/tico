"""Who may act as which mailbox. Manifests are written to a temp projects tree."""

import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake                    # noqa: E402,F401  (puts the hub on sys.path)

from connectors.mail import Refused                                  # noqa: E402
from connectors.mail import access                                   # noqa: E402
from backend.people import load as load_people                      # noqa: E402

INFLUENCER = """
name: influencer
outbound_send: false
access:
  - service: gmail
    identity: "ana@acme.example"
    can: [read, draft]
  - service: google-calendar
    identity: "ana@acme.example"
    can: [read]
"""
LEGAL = """
name: legal
access:
  - service: gmail
    identity: "legal@acme.example"
    can: [read, draft]
"""
BOTH = """
name: assistant
access:
  - service: gmail
    identity: "ana@acme.example"
    can: [read]
  - service: gmail
    identity: "legal@acme.example"
    can: [read]
"""
NO_MAIL = """
name: doc-updater
access:
  - service: slack
    identity: "Acme workspace"
    can: [read, post]
"""
CALENDAR_ONLY = """
name: recruiting
access:
  - service: google-calendar
    identity: "ana@acme.example"
    can: [read, draft]
"""


class Tree(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        for slug, text in (("influencer", INFLUENCER), ("legal", LEGAL), ("assistant", BOTH),
                           ("doc-updater", NO_MAIL), ("recruiting", CALENDAR_ONLY)):
            d = self.root / f"emp-{slug}"
            d.mkdir()
            (d / "employee.yaml").write_text(text)
        self._real, self._roster = access.PROJECTS, access._roster
        access.PROJECTS = self.root
        access._roster = lambda: load_people({"people": [{"id": "ana", "email": "ana@acme.example"}]})

    def tearDown(self):
        access.PROJECTS, access._roster = self._real, self._roster
        self.dir.cleanup()

    def manifest(self, slug):
        from connectors.mail import load_yaml
        return load_yaml(self.root / f"emp-{slug}" / "employee.yaml", slug)


class Resolution(Tree):
    def test_undeclared_employee_is_refused_with_the_fix(self):
        with self.assertRaises(Refused) as e:
            access.resolve("doc-updater", None, "read", self.manifest("doc-updater"))
        self.assertIn("does not declare any mail access", e.exception.msg)
        self.assertIn(f"owner:{access.OWNER}", e.exception.hint)

    def test_read_only_mailbox_refuses_every_write(self):
        m = {"access": [{"service": "gmail", "identity": "ana@acme.example",
                         "can": ["read", "draft", "send"], "read_only": True}]}
        self.assertEqual(access.resolve("legal", "ana@acme.example", "read", m)[0], "ana@acme.example")
        for verb in [*access.STATE_VERBS, "draft", "send"]:
            with self.subTest(verb=verb), self.assertRaises(Refused):
                access.resolve("legal", "ana@acme.example", verb, m)
        self.assertEqual(access.resolve("legal", "ana@acme.example", "calendar_read", m)[0],
                         "ana@acme.example")

class OrgRead(unittest.TestCase):
    ROSTER = None

    def setUp(self):
        from backend import people as P
        self.roster = P.load({
            "people": [
                {"id": "ana", "email": "ana@acme.example", "inbox_bot": "inbox"},
                {"id": "priya", "email": "priya@acme.example", "reports_to": "ana"},
                {"id": "omar", "email": "omar@acme.example", "reports_to": "priya"},
            ]
        })

    def test_inbox_bot_reads_only_its_own_mailbox_by_default(self):
        for extra in ({}, {"org_read": False}):
            m = {"access": [{"service": "gmail", "identity": "ana@acme.example",
                             "can": ["read", "draft"], **extra}]}
            self.assertEqual(sorted(access.holdings("inbox", m, self.roster)), ["ana@acme.example"])
            self.assertEqual(access.org_mailboxes("inbox", m, self.roster), [])
            with self.assertRaises(Refused):
                access.resolve("inbox", "priya@acme.example", "read", m, self.roster)

    def test_inbox_bot_without_a_gmail_entry_reads_its_person_only(self):
        held = access.holdings("inbox", {"access": []}, self.roster)
        self.assertEqual(held, {"ana@acme.example": {"gmail": ["read"]}})

    def test_org_read_true_reads_reports_but_cannot_send_as_them(self):
        m = {"access": [{"service": "gmail", "identity": "ana@acme.example",
                         "can": ["read", "draft"], "org_read": True}]}
        held = access.holdings("inbox", m, self.roster)
        self.assertEqual(sorted(held),
                         ["ana@acme.example", "omar@acme.example", "priya@acme.example"])
        self.assertEqual(held["ana@acme.example"]["gmail"], ["read", "draft"])
        self.assertEqual(held["priya@acme.example"]["gmail"], ["read"])
        access.resolve("inbox", "priya@acme.example", "read", m, self.roster)
        with self.assertRaises(Refused):
            access.resolve("inbox", "priya@acme.example", "draft", m, self.roster)
        with self.assertRaises(Refused):
            access.resolve("inbox", "priya@acme.example", "send", m, self.roster)


    def test_org_read_on_a_declared_mailbox_off_the_roster_reads_its_persons_reports(self):
        m = {"access": [{"service": "gmail", "identity": "ana@acme.team", "can": ["read"], "org_read": True}]}
        self.assertEqual(access.org_mailboxes("inbox", m, self.roster),
                         ["ana@acme.example", "priya@acme.example", "omar@acme.example"])
        self.assertEqual(access.org_mailboxes("other", m, self.roster), [])


if __name__ == "__main__":
    unittest.main()
