"""draft, send, reply and sent-log through the CLI, with a fake Gmail service.

The things this file exists to hold still: a retried draft updates one draft instead of making
two, a retried send sends nothing, every send reaches Tico's queue (the CLI refuses none itself),
and a send Tico blocks is a downgrade with Tico's reason rather than an error or a silent drop.
"""

import json, os, sys, unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake                                                          # noqa: E402
from harness import GOOD_BODY, Stage2                                # noqa: E402

from connectors.mail import compose, db, policy as pl                # noqa: E402
from connectors.mail import __main__ as cli                          # noqa: E402

REAL_QUEUE_SEND = pl.queue_send                                      # before any test patches it
APPROVAL = "11111111-2222-3333-4444-555555555555"

AVA, BO = "ava@creator.example", "bo@creator.example"
INCOMING = {"From": "Ava Reyes <ava@creator.example>", "To": "ana@acme.example",
            "Subject": "Re: your note", "Message-ID": "<ava-1@creator.example>",
            "References": "<ana-0@acme.example>",
            "Date": "Tue, 02 Sep 2026 09:00:00 -0700"}


class Writing(Stage2):
    def corpus(self):
        return [fake.message("m-ava", "t-ava", INCOMING,
                             body="What is the offer exactly?", epoch_ms=1788364800000)]

    def draft(self, *extra, body=None, slug="influencer", to=(AVA,)):
        f = self.body_file(body or GOOD_BODY)
        return self.run_json("draft", "--as", slug, *(x for a in to for x in ("--to", a)), "--subject",
                             "Ava, 6 months of Acme on us", "--body-file", f,
                             "--issue", "42", "--json", *extra)


class Sending(Writing):
    def make(self):
        rc, p, err = self.draft()
        self.assertEqual(rc, 0, err)
        return p["draft"]

    def test_a_retried_send_queues_the_same_frozen_content_without_sending(self):
        did = self.make()
        command = ("send", "--as", "influencer", "--draft", did, "--issue", "42")
        rc, first, err = self.run_json(*command)
        self.assertEqual(rc, 0, err)
        rc, second, err = self.run_json(*command)
        self.assertEqual(rc, 0, err)
        self.assertTrue(first["queued"])
        self.assertFalse(first["sent"])
        self.assertEqual(first["send_id"], second["send_id"])
        self.assertEqual(self.service.sent, [])
        self.assertIn(did, self.service.drafts_by_id)

    def test_an_approval_id_reaches_tico_with_the_send(self):
        did = self.make()
        posted = []

        def post(path, body, key=None):
            posted.append((path, body))
            return {"send": {"id": "s1", "state": "waiting_approval", "approval_id": body["approval_id"],
                             "send_after": None, "detail": "", "decision": {"outcome": "ask", "reasons": ["approval"]}}}
        pl.HUB_POST = post
        with mock.patch.object(pl, "queue_send", REAL_QUEUE_SEND):
            rc, out, err = self.run_json("send", "--as", "influencer", "--draft", did, "--issue", "42",
                                         "--approval", APPROVAL)
        self.assertEqual(rc, 0, err)
        self.assertEqual([(path, body["approval_id"], body["draft_id"]) for path, body in posted],
                         [("email/sends", APPROVAL, did)])
        self.assertEqual((out["sent"], out["queued"], out["state"], out["approval_id"], out["reasons"]),
                         (False, True, "waiting_approval", APPROVAL, ["approval"]))
        self.assertIn("approve this send in Tico", out["note"])

    def test_a_send_tico_blocks_keeps_the_draft_and_says_why(self):
        did = self.make()
        self.queue_answer = {"state": "blocked", "detail": "Blocked recipient: ava@creator.example"}
        rc, out, err = self.run_json("send", "--as", "influencer", "--draft", did, "--issue", "42")
        self.assertEqual(rc, 0, err)
        self.assertEqual((out["ok"], out["sent"], out["downgraded"], out["reason"]),
                         (True, False, "draft", "Blocked recipient: ava@creator.example"))
        self.assertIn(did, self.service.drafts_by_id)
        self.assertEqual(self.service.sent, [])
        down = [a for a in self.audit_lines() if a["action"] == "send-downgraded"]
        self.assertEqual(down[-1]["detail"]["reason"], "Blocked recipient: ava@creator.example")
        self.assertIsNone(self.conn().execute("SELECT 1 FROM queued_drafts WHERE draft_id=?", (did,)).fetchone())

    def test_the_cli_refuses_no_send_itself_two_outside_recipients_reach_tico(self):
        rc, d, err = self.draft("--cc", "carol@partner.example", to=(AVA, BO))
        self.assertEqual(rc, 0, err)
        rc, out, err = self.run_json("send", "--as", "influencer", "--draft", d["draft"], "--issue", "42")
        self.assertEqual(rc, 0, err)
        self.assertTrue(out["queued"], out)
        self.assertEqual(len(self.queue_requests), 1)
        raw = compose.decode(self.queue_requests[0]["raw"])
        for addr in (AVA, BO, "carol@partner.example"):
            self.assertIn(addr, raw)

    def test_a_dry_run_builds_nothing_and_queues_nothing(self):
        rc, out, err = self.run_json("reply", "--as", "influencer", "--thread", "t-ava", "--body-file",
                                     self.body_file(GOOD_BODY), "--issue", "42", "--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertEqual((out["sent"], out["would_queue"]), (False, True))
        self.assertEqual(self.queue_requests, [])
        self.assertEqual(self.service.drafts_by_id, {})

    def test_queue_failure_keeps_the_draft_and_never_calls_the_provider(self):
        from connectors.mail import Failure
        did = self.make()
        with mock.patch.object(pl, "queue_send", side_effect=Failure("Queue unavailable")):
            rc, _, _ = self.run_cli("send", "--as", "influencer", "--draft", did, "--issue", "42")
        self.assertEqual(rc, 1)
        self.assertIn(did, self.service.drafts_by_id)
        self.assertEqual(self.service.sent, [])

class AttachAndDiscard(Writing):
    @property
    def home(self):
        return self.root / "emp-influencer"

    def refused(self, path):
        rc, _, err = self.run_cli("draft", "--as", "influencer", "--to", AVA, "--subject", "Files",
                                  "--body-file", self.body_file(GOOD_BODY), "--issue", "42",
                                  "--attach", str(path))
        self.assertEqual(rc, 2, (path, err))
        self.assertEqual(self.service.drafts_by_id, {}, path)
        return err

    def test_an_attached_draft_is_queued_like_any_other_and_tico_s_answer_names_the_files(self):
        pdf = self.root / "emp-influencer" / "letter.pdf"
        pdf.write_bytes(b"%PDF-1.4 response letter")
        rc, d, err = self.draft("--attach", str(pdf), "--reply-to", "t-ava")
        self.assertEqual(rc, 0, err)
        self.assertEqual([(a["name"], a["type"]) for a in d["attachments"]],
                         [("letter.pdf", "application/pdf")])
        raw = compose.decode(self.service.drafts_by_id[d["draft"]]["message"]["raw"])
        self.assertIn('filename="letter.pdf"', raw)
        self.assertIn("In-Reply-To: <ava-1@creator.example>", raw)
        self.queue_answer = {"state": "blocked", "detail": "Ask first: a person approves files"}
        rc, out, _ = self.run_json("send", "--as", "influencer", "--draft", d["draft"], "--issue", "42")
        self.assertEqual((rc, out["sent"], out["downgraded"], out["attachments"]),
                         (0, False, "draft", ["letter.pdf"]))
        self.assertIn('filename="letter.pdf"', compose.decode(self.queue_requests[0]["raw"]))
        self.assertEqual(self.service.sent, [])
        down = [a for a in self.audit_lines() if a["action"] == "send-downgraded"]
        self.assertEqual(down[-1]["detail"]["attachments"], ["letter.pdf"])

    def test_a_file_outside_the_bots_folder_is_refused_however_it_is_named(self):
        outside = self.root / "elsewhere.pdf"
        outside.write_bytes(b"%PDF-1.4 not the bot's")
        (self.home / "docs").mkdir()
        (self.home / "docs" / "link.pdf").symlink_to(outside)
        (self.root / "emp-inbox" / "theirs.pdf").write_bytes(b"%PDF-1.4 another bot's")
        for path in (outside, self.home / ".." / "elsewhere.pdf", "../elsewhere.pdf",
                     self.home / "docs" / "link.pdf", "docs/link.pdf", self.root / "emp-inbox" / "theirs.pdf",
                     self.home / "docs" / ".." / ".." / "emp-inbox" / "theirs.pdf"):
            err = self.refused(path)
            self.assertTrue("outside this bot's folder" in err or "'..'" in err, (path, err))

    def test_secret_looking_files_are_refused_inside_the_bots_folder(self):
        names = (".env", ".env.local", "prod.env", "server.pem", "tls.key", "id_rsa", "id_rsa.pub",
                 "id_ed25519", "cert.p12", "cert.pfx", ".netrc", ".npmrc", ".pypirc", "credentials.json",
                 "Credentials", "client_secret.json", "MY-SECRETS.txt", "github_token", "api-token.txt",
                 "secrets/report.pdf", "private keys/a.pdf", "private_keys/a.pdf", ".ssh/config",
                 ".aws/config", ".gnupg/pubring.kbx", ".config/gh/hosts.yml", "a/.ssh/notes.txt")
        for name in names:
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
            err = self.refused(path)
            self.assertIn("never attached", err, name)
        (self.home / "notes.pdf").symlink_to(self.home / ".env")        # a harmless name pointing at one
        self.assertIn("never attached", self.refused(self.home / "notes.pdf"))

    def test_an_oversized_file_is_refused_before_it_is_read(self):
        big = self.home / "big.pdf"
        with open(big, "wb") as f:
            f.truncate(cli.MAX_ATTACH_BYTES + 1)                       # sparse: nothing is written
        small = self.home / "small.pdf"
        small.write_bytes(b"x" * 1024)
        with mock.patch.object(cli, "open", create=True, side_effect=AssertionError("read")) as opened:
            self.assertIn("25 MB", self.refused(big))
            rc, _, err = self.draft("--attach", str(small), "--attach", str(big))
            self.assertEqual(rc, 2, err)                               # the total counts, before any read
        opened.assert_not_called()
        self.assertEqual(os.stat(big).st_size, cli.MAX_ATTACH_BYTES + 1)

    def test_a_draft_key_takes_the_files_and_stays_the_same_without_them(self):
        args = ("influencer", "42", ["Ava@creator.example"], "Ava, 6 months of Acme on us", "Hi Ava")
        old = "0b101c8a0bd8511311b4ab2fe7a6b20aa89e6fd66e86d1a4e6889dab9bb3691b"   # the key before files counted
        self.assertEqual((compose.draft_key(*args), compose.draft_key(*args, [])), (old, old))
        a, b = {"sha256": "a" * 64}, {"sha256": "b" * 64}
        self.assertNotEqual(compose.draft_key(*args, [a]), old)
        self.assertNotEqual(compose.draft_key(*args, [a]), compose.draft_key(*args, [b]))
        self.assertEqual(compose.draft_key(*args, [a, b]), compose.draft_key(*args, [b, a]))

    def test_a_bot_discards_only_its_own_unsent_draft(self):
        rc, d, err = self.draft()
        self.assertEqual(rc, 0, err)
        did = d["draft"]
        for slug, target in (("inbox", did), ("influencer", "m-ava")):   # not its draft; a received message
            rc, _, _ = self.run_cli("discard", target, "--as", slug)
            self.assertEqual(rc, 2)
        self.assertIn(did, self.service.drafts_by_id)
        self.assertIn("m-ava", self.service.store)
        rc, out, err = self.run_json("draft", "discard", did, "--as", "influencer", "--json")
        self.assertEqual((rc, out["discarded"]), (0, True), err)
        self.assertEqual(self.service.deleted_drafts, [did])
        self.assertIsNone(db.draft_by_gmail_id(self.conn(), "ana@acme.example", did))
        self.assertIn("discard", [a["action"] for a in self.audit_lines()])
        rc, s, _ = self.draft()                                          # sent mail is never deleted
        self.run_json("send", "--as", "influencer", "--draft", s["draft"], "--issue", "42")
        rc, _, _ = self.run_cli("discard", s["draft"], "--as", "influencer")
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
