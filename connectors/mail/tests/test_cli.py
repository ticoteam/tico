"""End to end through the CLI with a fake Gmail service: no network, no key, no credentials.

Every test runs `main([...])` exactly as `scripts/mail.sh` does, with open_gmail, open_db and
the audit file redirected. That covers the argument parsing, the access gate, the audit lines,
the watermark, and the exit codes in one place.
"""

import contextlib, io, json, os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake  # noqa: E402,F401  (puts the hub on sys.path)

from connectors.mail import db, gmail as gm  # noqa: E402
from connectors.mail import access, audit as audit_log  # noqa: E402
from connectors.mail import __main__ as cli  # noqa: E402

MANIFESTS = {
    "influencer": """
name: influencer
access:
  - service: gmail
    identity: "ana@acme.example"
    can: [read, draft]
""",
    "doc-updater": """
name: doc-updater
access:
  - service: slack
    identity: "Acme workspace"
    can: [read, post]
""",
}

HEADERS = {
    "From": "Acme Growth <hello@acme.io>",
    "To": "ana@acme.example",
    "Subject": "Grow your rentals",
    "Date": "Tue, 02 Sep 2026 09:00:00 -0700",
    "List-Unsubscribe": "<https://acme.io/u/1>",
}
PLAIN = {
    "From": "Real Person <person@customer.example>",
    "To": "ana@acme.example",
    "Subject": "Pricing question",
    "Date": "Tue, 02 Sep 2026 09:30:00 -0700",
}
INVOICE = {
    "From": "billing@supplier.example",
    "To": "ana@acme.example",
    "Subject": "Invoice 4471 is past due",
    "Date": "Tue, 02 Sep 2026 08:00:00 -0700",
    "List-Unsubscribe": "<https://supplier.example/u>",
}

OLD, MID, NEW = 1788278400000, 1788364800000, 1788368400000  # Sep 1 09:00, Sep 2 09:00, 10:00


def corpus():
    return [
        fake.message("m-mkt", "t-mkt", HEADERS, body="Read the guide.", epoch_ms=MID),
        fake.message("m-plain", "t-plain", PLAIN, body="How does pricing work?", epoch_ms=NEW),
        fake.message("m-inv", "t-inv", INVOICE, body="Please pay.", epoch_ms=OLD),
    ]


class CLI(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        for slug, text in MANIFESTS.items():
            d = root / f"emp-{slug}"
            d.mkdir()
            (d / "employee.yaml").write_text(text)
        self._projects, access.PROJECTS = access.PROJECTS, root
        self._audit, audit_log.AUDIT_PATH = audit_log.AUDIT_PATH, root / "audit.jsonl"
        self.dbfile = root / "mail.db"
        self.service = fake.FakeGmailService(corpus())
        self._open_gmail, self._open_db = cli.open_gmail, cli.open_db
        cli.open_gmail = lambda mailbox: gm.Gmail(self.service, mailbox, sleep=lambda s: None)
        cli.open_db = lambda: db.connect(self.dbfile)
        os.environ.pop("HUB_EMPLOYEE", None)

    def tearDown(self):
        access.PROJECTS = self._projects
        audit_log.AUDIT_PATH = self._audit
        cli.open_gmail, cli.open_db = self._open_gmail, self._open_db
        self.dir.cleanup()

    # -- helpers ----------------------------------------------------
    def run_cli(self, *argv):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, buf.getvalue(), err.getvalue()

    def run_json(self, *argv):
        rc, out, err = self.run_cli(*argv)
        return rc, json.loads(out) if out.strip() else {}, err

    def audit_lines(self):
        p = Path(audit_log.AUDIT_PATH)
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


class Rules(CLI):
    def labels_on(self, mid):
        return {self.service.name_of(l) for l in self.service.store[mid]["labelIds"]}

    def test_dry_run_touches_nothing(self):
        rc, payload, _ = self.run_json(
            "rules", "run", "--as", "influencer", "--since", "2026-08-31T00:00:00Z", "--dry-run", "--json"
        )
        self.assertEqual(rc, 0)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(self.service.modified, [])
        self.assertEqual(payload["counts"]["unsubscribe-is-marketing"], 2)
        self.assertEqual(payload["counts"]["payment-risk-words"], 1)


if __name__ == "__main__":
    unittest.main()
