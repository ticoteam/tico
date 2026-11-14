"""Shared setup for the stage-2 tests: a temp projects root, fake services, and a fake Tico send queue.

Not a test module (discovery only collects test*.py). `Stage2` gives every test its own
projects directory, its own mail.db and its own audit file. Tico's answers are seams: `queue_send`
records each request in `queue_requests` and answers `queue_answer` (pending unless a test changes it),
and the sending policy reads Ask first. The decision model is off throughout: the suite is offline.
"""

import contextlib, io, json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake  # noqa: E402,F401  (puts the hub on sys.path)

from connectors.mail import access, audit as audit_log, calendar as cal  # noqa: E402
from connectors.mail import db, gmail as gm, policy as pl  # noqa: E402
from connectors.mail import __main__ as cli  # noqa: E402

MANIFESTS = {
    "influencer": """
name: influencer
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
access:
  - service: gmail
    identity: "ana@acme.example"
    can: [read, draft]
""",
    "legal": """
name: legal
access:
  - service: gmail
    identity: "legal@acme.example"
    can: [read, draft]
""",
}

CREATORS = (
    "name,handle,platform,followers,email,fit,source,added\n"
    "Ava Reyes,@ava,instagram,41000,ava@creator.example,keep,upfluence,2026-08-26\n"
    "Bo Lin,@bo,youtube,12000,bo@creator.example,skip,upfluence,2026-08-26\n"
    "Cass Ng,@cass,tiktok,88000,cass@creator.example,\"keep: 40k rental owners\","
    "upfluence,2026-08-27\n"
)

CTA = "https://acme.example/rentals?utm_source=influencer&utm_medium=email&utm_campaign=ava-reyes"

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


class Stage2(unittest.TestCase):
    """A temp world: manifests, a fake Gmail, a fake Calendar and a fake Tico queue."""

    manifests = MANIFESTS
    creators = CREATORS

    # A bot's run carries these; with them a draft would upload its files to the run's live task.
    HUB_ENV = ("HUB_API_URL", "HUB_TOKEN", "HUB_TASK_ID")

    def setUp(self):
        self._env = mock.patch.dict(os.environ)
        self._env.start()
        for name in self.HUB_ENV:
            os.environ.pop(name, None)
        # Every `mail draft --attach` records its task uploads here and nothing leaves the process.
        self.task_uploads = []
        self._task_attach, cli.TASK_ATTACH = (
            cli.TASK_ATTACH,
            (lambda task, name, data, key: self.task_uploads.append((task, name, data, key)) or ""),
        )
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        for slug, text in self.manifests.items():
            d = self.root / f"emp-{slug}"
            d.mkdir()
            (d / "employee.yaml").write_text(text)
            (d / "AGENTS.md").write_text(f"# {slug}\n\n## Role\nYou own {slug} work.\n")
        data = self.root / "emp-influencer" / "data"
        data.mkdir()
        (data / "creators.csv").write_text(self.creators)

        self._saved = {
            "access": access.PROJECTS,
            "audit": audit_log.AUDIT_PATH,
            "hub_get": pl.HUB_GET,
            "hub_post": pl.HUB_POST,
            "server_get": pl.SERVER_GET,
            "runner_get": pl.RUNNER_GET,
            "gmail": cli.open_gmail,
            "db": cli.open_db,
            "cal": cli.open_calendar,
        }
        access.PROJECTS = self.root
        # The bot's mail setting as Tico answers it (backend/mail_settings.py): What Tico may do, all on.
        pl.SERVER_GET = self.confirmed_setting
        pl.HUB_GET = lambda path: {"effective": {"mode": "ask_first"}} if path.endswith("/sending-policy") else None
        pl.HUB_POST = lambda path, body, key=None: None  # nothing reaches a real Tico
        # An owner run reads What Tico may do over the runner's registration: all on here; test_company.py turns it off.
        pl.RUNNER_GET = lambda path: {"company": dict.fromkeys(pl.COMPANY_FIELDS, True)}
        audit_log.AUDIT_PATH = self.root / "audit.jsonl"
        # Tico answers who may use which mailbox (fake.mail_access: grants that match each bot.yaml); a test changes
        # it with fake.MAIL_LEVELS. Only mail-action / connector-event lease the connector its writes.
        fake.install_mail_access()
        self._levels = mock.patch.dict(fake.MAIL_LEVELS, clear=True)
        self._levels.start()
        self.addCleanup(self._levels.stop)
        os.environ["MAIL_DECISIONS"] = "none"  # no decision model; a test injects a fake engine
        os.environ["TICO_INTERNAL_DOMAINS"] = "acme.example"
        os.environ.pop("HUB_EMPLOYEE", None)

        self.dbfile = self.root / "mail.db"
        self.service = fake.FakeGmailService(self.corpus())
        self.queue_requests = []
        self.queue_answer = {"state": "pending"}  # what Tico answers each queued send; a test changes it

        def queued(
            mailbox,
            raw,
            thread_id="",
            reply_to_id="",
            approval_id="",
            automation_action_id="",
            draft_id="",
            calendar_action_id="",
        ):
            import hashlib

            self.queue_requests.append(
                {
                    "mailbox": mailbox,
                    "raw": raw,
                    "thread_id": thread_id,
                    "reply_to_id": reply_to_id,
                    "approval_id": approval_id,
                    "draft_id": draft_id,
                }
            )
            return {
                "id": hashlib.sha256(raw.encode()).hexdigest(),
                "approval_id": approval_id,
                "send_after": "2026-11-04T12:02:00Z",
                "detail": "",
                "decision": {"reasons": []},
                **self.queue_answer,
            }

        queue_patch = mock.patch.object(pl, "queue_send", side_effect=queued)
        queue_patch.start()
        self.addCleanup(queue_patch.stop)
        self.calendar = fake.FakeCalendarService()
        cli.open_gmail = lambda mailbox: gm.Gmail(self.service, mailbox, sleep=lambda s: None)
        cli.open_calendar = lambda mailbox: self.calendar
        cli.open_db = lambda: db.connect(self.dbfile)

    def tearDown(self):
        access.PROJECTS = self._saved["access"]
        pl.HUB_GET = self._saved["hub_get"]
        pl.HUB_POST = self._saved["hub_post"]
        pl.SERVER_GET = self._saved["server_get"]
        pl.RUNNER_GET = self._saved["runner_get"]
        audit_log.AUDIT_PATH = self._saved["audit"]
        cli.open_gmail, cli.open_db = self._saved["gmail"], self._saved["db"]
        cli.open_calendar = self._saved["cal"]
        cli.TASK_ATTACH = self._task_attach
        self._env.stop()
        self.dir.cleanup()

    def confirmed_setting(self, slug):
        """The bot's mail setting as Tico answers it, What Tico may do all on."""
        return {"bot": slug, "company_organize": True, "company_send": True, "company_calendar": True}

    # -- knobs ------------------------------------------------------
    def corpus(self):
        return []

    def body_file(self, text, name="body.txt"):
        p = self.root / name
        p.write_text(text)
        return str(p)

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
