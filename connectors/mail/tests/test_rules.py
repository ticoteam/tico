"""The rules engine: every condition, ordering, never_archive, stop, and the shipped file."""

import json, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake  # noqa: E402,F401  (puts the hub on sys.path)

from backend import people as P  # noqa: E402
from connectors.mail import RULES_FILE, access, gmail as gm, rules as rl

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def msg(**over):
    base = {
        "id": "m1",
        "thread_id": "t1",
        "from": "person@customer.example",
        "from_header": "Real Person <person@customer.example>",
        "to": ["ana@acme.example"],
        "cc": [],
        "subject": "hello",
        "body": "some words",
        "labels": ["INBOX"],
        "attachments": [],
        "unsubscribe": False,
        "list_id": "",
        "is_internal": False,
        "is_calendar_invite": False,
        "last_reply_by": "",
        "epoch": int(NOW.timestamp()),
    }
    base.update(over)
    return base


def rule(rid, when, do):
    return rl.validate({"id": rid, "when": when, "do": do})


class ShippedRules(unittest.TestCase):
    """registry/mail-rules.yaml against the fixture mailbox, the same run `rules test` does."""

    def test_every_fixture_gets_the_outcome_it_expects(self):
        files = sorted(FIXTURES.glob("*.json"))
        self.assertTrue(files, "no fixtures")
        for f in files:
            with self.subTest(fixture=f.name):
                case = json.loads(f.read_text())
                m = gm.normalize(case["message"])
                m["last_reply_by"] = case.get("last_reply_by", "")
                if case.get("judgments"):  # what the judge said, recorded with the fixture
                    m["judgments"] = case["judgments"]
                plan = rl.apply(rl.load(RULES_FILE, case.get("mailbox", "ana@acme.example")), m)
                got = {
                    "labels": sorted(plan["labels"]),
                    "archive": plan["archive"],
                    "mark_read": plan["mark_read"],
                    "star": plan["star"],
                    "never_archive": plan["never_archive"],
                    "rules": [h["rule"] for h in plan["hits"]],
                }
                for k, want in (case.get("expect") or {}).items():
                    norm = lambda v: sorted(v) if isinstance(v, list) else v
                    self.assertEqual(norm(want), norm(got[k]), k)


COMPANY = """mailboxes:
  common:
    - id: payment-risk-words
      when: {subject_or_body_matches: ["past due"]}
      do: {label: hub/needs-owner, never_archive: true}
  ana@acme.example:
    - id: registry-own
      when: {has_unsubscribe_link: true}
      do: {label: hub/registry}
"""
BOT = """mailboxes:
  ana@acme.example:
    - id: bot-own
      when: {has_unsubscribe_link: true}
      do: {label: hub/bot, archive: true}
"""
ROSTER = P.load(
    {
        "default_user": "ana",
        "teams": {},
        "people": [
            {
                "id": "ana",
                "name": "Ana",
                "email": "ana@acme.example",
                "team": "t",
                "reports_to": None,
                "inbox_bot": "inbox",
            },
            {"id": "ben", "name": "Ben", "email": "ben@acme.example", "team": "t", "reports_to": "ana"},
            {"id": "cy", "name": "Cy", "email": "cy@other.example", "team": "t", "reports_to": None},
        ],
    }
)


class BotRules(unittest.TestCase):
    """A mailbox uses its inbox bot's rules/mail-rules.yaml when the bot's repository has one."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        (self.root / "company.yaml").write_text(COMPANY)
        self._projects, access.PROJECTS = access.PROJECTS, self.root
        self.addCleanup(lambda: setattr(access, "PROJECTS", self._projects))
        self.addCleanup(self.dir.cleanup)

    def bot_file(self, text=BOT):
        (self.root / "emp-inbox" / "rules").mkdir(parents=True)
        (self.root / "emp-inbox" / "rules" / "mail-rules.yaml").write_text(text)

    def ids(self, mailbox):
        found = access.bot_rules_file(mailbox, ROSTER)
        return [r["id"] for r in rl.load(self.root / "company.yaml", mailbox, found)]

    def test_a_report_is_handled_by_the_managers_inbox_bot(self):
        self.bot_file(BOT.replace("ana@", "ben@"))
        self.assertEqual(access.inbox_bot_for("ben@acme.example", ROSTER), "inbox")
        self.assertEqual(self.ids("ben@acme.example"), ["payment-risk-words", "bot-own"])

    def test_a_company_protection_still_wins_over_a_bot_archive(self):
        self.bot_file()
        rules = rl.load(
            self.root / "company.yaml", "ana@acme.example", access.bot_rules_file("ana@acme.example", ROSTER)
        )
        m = msg(subject="Invoice past due", unsubscribe=True)
        plan = rl.apply(rules, m, NOW)
        self.assertFalse(plan["archive"])
        self.assertTrue(plan["never_archive"])
        self.assertEqual(plan["blocked"][0]["rule"], "bot-own")


if __name__ == "__main__":
    unittest.main()
