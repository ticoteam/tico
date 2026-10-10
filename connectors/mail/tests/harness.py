"""Shared setup for the stage-2 tests: a temp projects root, a temp policy file, fake services.

Not a test module (discovery only collects test*.py). `Stage2` gives every test its own
projects directory, its own mail.db, its own audit file and its own copy of the policy, so a
test can pause a mailbox or flip the kill switch without touching the hub's real registry.

The reviewer is `none` throughout: the suite is offline, and the reviewer's own behaviour is
tested against a fake backend in test_review.py.
"""

import contextlib, io, json, os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake                    # noqa: E402,F401  (puts the hub on sys.path)

from connectors.mail import access, audit as audit_log, calendar as cal   # noqa: E402
from connectors.mail import db, gmail as gm, policy as pl, review as rv   # noqa: E402
from connectors.mail import __main__ as cli                              # noqa: E402

POLICY = """
global:
  send_enabled: true
sandbox_mailbox: hub-test@acme.example
internal_domains: [acme.example]
mailboxes:
  ana@acme.example: { paused: false }
defaults:
  max_sends_per_day: 20
  per_recipient_cooldown_days: 14
  max_external_recipients: 1
  allow_cc_external: false
  allow_attachments: false
scheduling:
  mailbox: ana@acme.example
  employee: inbox
blocklist:
  addresses: [blocked@nope.example]
  domains: [banned.example]
owner_handles_personally:
  addresses: [investor@fund.example]
  domains: [board.example]
allowances:
  - employee: influencer
    mailbox: ana@acme.example
    purpose: Cold outreach to creators.
    recipients:
      source: csv
      path: emp-influencer/data/creators.csv
      column: email
      require_column: { fit: keep }
    caps:
      per_day: 10
      per_recipient_cooldown_days: 365
    urls:
      required_pattern: '^https://acme\\.example/rentals\\?utm_source=influencer&utm_medium=email&utm_campaign=[a-z0-9][a-z0-9-]*$'
      forbidden: [calendly.com, acme.example/demo, go.acme.example]
"""

MANIFESTS = {
    "influencer": """
name: influencer
outbound_send: true
access:
  - service: gmail
    identity: "ana@acme.example"
    can: [read, draft, send]
  - service: google-calendar
    identity: "ana@acme.example"
    can: [read, draft]
""",
    "inbox": """
name: inbox
outbound_send: false
access:
  - service: gmail
    identity: "ana@acme.example"
    can: [read, draft]
""",
    "legal": """
name: legal
outbound_send: false
access:
  - service: gmail
    identity: "legal@acme.example"
    can: [read, draft]
""",
}

CREATORS = ("name,handle,platform,followers,email,fit,source,added\n"
            "Ava Reyes,@ava,instagram,41000,ava@creator.example,keep,upfluence,2026-08-26\n"
            "Bo Lin,@bo,youtube,12000,bo@creator.example,skip,upfluence,2026-08-26\n"
            "Cass Ng,@cass,tiktok,88000,cass@creator.example,\"keep: 40k rental owners\","
            "upfluence,2026-08-27\n")

CTA = ("https://acme.example/rentals?utm_source=influencer&utm_medium=email"
       "&utm_campaign=ava-reyes")

GOOD_BODY = f"""Hi Ava,

I'm Ana, the founder of Acme. We're the AI property manager: pricing, guest messaging,
turnovers, maintenance and rent, for 3.9% instead of the 30% a manager charges. Owners keep
their listings, their bank and their own pros.

I'd like to work with you. The offer is six months of Acme free on up to ten properties, plus a
fee. What is your rate for posts/collaborations?

Here's the product: {CTA}

Ana
Founder, Acme
"""


def gh_answer(state="CLOSED", labels=(f"owner:{access.OWNER}", "type:decision"), title="", body="",
              returncode=0, stdout=None):
    """A stand-in for subprocess.run(['gh', 'issue', 'view', ...])."""
    payload = {"state": state, "labels": [{"name": l} for l in labels],
               "title": title, "body": body}

    class R:
        pass
    r = R()
    r.returncode = returncode
    r.stdout = json.dumps(payload) if stdout is None else stdout
    r.stderr = ""
    return lambda *a, **k: r


class Stage2(unittest.TestCase):
    """A temp world: manifests, a policy file, a fake Gmail and a fake Calendar."""

    manifests = MANIFESTS
    creators = CREATORS
    policy_yaml = POLICY

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        for slug, text in self.manifests.items():
            d = self.root / f"emp-{slug}"
            d.mkdir()
            (d / "employee.yaml").write_text(text)
            (d / "AGENT.md").write_text(f"# {slug}\n\n## Role\nYou own {slug} work.\n")
        data = self.root / "emp-influencer" / "data"
        data.mkdir()
        (data / "creators.csv").write_text(self.creators)
        self.policy_file = self.root / "mail-policy.yaml"
        self.write_policy(self.policy_yaml)

        self._saved = {"access": access.PROJECTS, "policy_projects": pl.PROJECTS,
                       "policy_file": pl.POLICY_FILE, "review_projects": rv.PROJECTS,
                       "audit": audit_log.AUDIT_PATH, "run": pl.RUN, "hub_get": pl.HUB_GET,
                       "server_get": pl.SERVER_GET,
                       "gmail": cli.open_gmail, "db": cli.open_db, "cal": cli.open_calendar,
                       "reviewer": os.environ.get("MAIL_REVIEWER"), "judge": os.environ.get("MAIL_DECISIONS")}
        access.PROJECTS = pl.PROJECTS = rv.PROJECTS = self.root
        pl.POLICY_FILE = self.policy_file
        # A person has already confirmed in Tico what each bot.yaml asks (backend/mail_settings.py); the tests of the
        # server-held switch itself replace this.
        pl.SERVER_GET = self.confirmed_setting
        audit_log.AUDIT_PATH = self.root / "audit.jsonl"
        os.environ["MAIL_REVIEWER"] = "none"
        os.environ["MAIL_DECISIONS"] = "none"        # no judge either; test_judge.py injects a fake engine
        os.environ.pop("HUB_EMPLOYEE", None)

        self.dbfile = self.root / "mail.db"
        self.service = fake.FakeGmailService(self.corpus())
        self.calendar = fake.FakeCalendarService()
        cli.open_gmail = lambda mailbox: gm.Gmail(self.service, mailbox, sleep=lambda s: None)
        cli.open_calendar = lambda mailbox: self.calendar
        cli.open_db = lambda: db.connect(self.dbfile)

    def tearDown(self):
        access.PROJECTS = self._saved["access"]
        pl.PROJECTS = self._saved["policy_projects"]
        pl.POLICY_FILE = self._saved["policy_file"]
        pl.RUN = self._saved["run"]
        pl.HUB_GET = self._saved["hub_get"]
        pl.SERVER_GET = self._saved["server_get"]
        rv.PROJECTS = self._saved["review_projects"]
        audit_log.AUDIT_PATH = self._saved["audit"]
        cli.open_gmail, cli.open_db = self._saved["gmail"], self._saved["db"]
        cli.open_calendar = self._saved["cal"]
        if self._saved["reviewer"] is None:
            os.environ.pop("MAIL_REVIEWER", None)
        else:
            os.environ["MAIL_REVIEWER"] = self._saved["reviewer"]
        if self._saved["judge"] is None:
            os.environ.pop("MAIL_DECISIONS", None)
        else:
            os.environ["MAIL_DECISIONS"] = self._saved["judge"]
        self.dir.cleanup()

    def confirmed_setting(self, slug):
        """The server's mail setting as if a person had turned on what `slug`'s manifest asks."""
        try:
            manifest = access.load(slug)
        except Exception:
            return {"bot": slug, "set": False, "outbound_send": False, "forward_to": []}
        return {"bot": slug, "set": True, "outbound_send": bool(manifest.get("outbound_send")),
                "forward_to": access.forward_to(manifest), "updated_by": "human:ana"}

    # -- knobs ------------------------------------------------------
    def corpus(self):
        return []

    def write_policy(self, text):
        self.policy_file.write_text(text)

    def body_file(self, text, name="body.txt"):
        p = self.root / name
        p.write_text(text)
        return str(p)

    def policy(self):
        return pl.load(self.policy_file)

    def conn(self):
        return db.connect(self.dbfile)

    # -- running ----------------------------------------------------
    def run_cli(self, *argv):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, buf.getvalue(), err.getvalue()

    def run_json(self, *argv):
        rc, out, err = self.run_cli(*argv)
        return rc, (json.loads(out) if out.strip().startswith(("{", "[")) else {}), err

    def audit_lines(self):
        p = Path(audit_log.AUDIT_PATH)
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def labels_on(self, mid):
        return {self.service.name_of(l) for l in self.service.store[mid]["labelIds"]}
