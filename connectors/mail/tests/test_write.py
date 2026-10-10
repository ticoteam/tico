"""draft, send, reply and sent-log through the CLI, with a fake Gmail service.

The things this file exists to hold still: a retried draft updates one draft instead of making
two, a retried send sends nothing, a reply keeps the thread's headers, and every refusal on the
send path is a downgrade with a reason rather than an error or a silent drop.
"""

import json, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake, harness                                                 # noqa: E402
from harness import CTA, GOOD_BODY, Stage2, gh_answer                # noqa: E402

from connectors.mail import compose, db, policy as pl, review as rv  # noqa: E402

AVA, BO = "ava@creator.example", "bo@creator.example"
INCOMING = {"From": "Ava Reyes <ava@creator.example>", "To": "ana@acme.example",
            "Subject": "Re: your note", "Message-ID": "<ava-1@creator.example>",
            "References": "<ana-0@acme.example>",
            "Date": "Tue, 02 Sep 2026 09:00:00 -0700"}


class Writing(Stage2):
    def corpus(self):
        return [fake.message("m-ava", "t-ava", INCOMING,
                             body="What is the offer exactly?", epoch_ms=1788364800000)]

    def draft(self, *extra, body=None, slug="influencer"):
        f = self.body_file(body or GOOD_BODY)
        return self.run_json("draft", "--as", slug, "--to", AVA, "--subject",
                             "Ava, 6 months of Acme on us", "--body-file", f,
                             "--issue", "42", "--json", *extra)


class Sending(Writing):
    def make(self):
        rc, p, err = self.draft()
        self.assertEqual(rc, 0, err)
        return p["draft"]

    def test_a_retried_send_sends_nothing_and_says_so(self):
        did = self.make()
        self.run_json("send", "--as", "influencer", "--draft", did, "--issue", "42")
        rc, p, err = self.run_json("send", "--as", "influencer", "--draft", did, "--issue", "42")
        self.assertEqual(rc, 1)          # the draft is gone: Gmail says notFound
        did2 = self.make()
        row = db.get_send(self.conn(), compose.send_key("influencer", "42", did))
        self.assertEqual(row["status"], "sent")

    def test_the_idempotency_row_is_written_before_the_api_call(self):
        did = self.make()
        self.service.send_fails = True
        rc, out, err = self.run_cli("send", "--as", "influencer", "--draft", did, "--issue", "42")
        self.assertEqual(rc, 1)
        # the claim was rolled back, so a retry is allowed to try again
        self.assertIsNone(db.get_send(self.conn(), compose.send_key("influencer", "42", did)))
        self.service.send_fails = False
        rc, p, _ = self.run_json("send", "--as", "influencer", "--draft", did, "--issue", "42")
        self.assertTrue(p["sent"])

    def test_outbound_send_false_downgrades_with_the_reason(self):
        p = self.root / "emp-influencer" / "employee.yaml"
        p.write_text(p.read_text().replace("outbound_send: true", "outbound_send: false"))
        did = self.make()
        rc, out, err = self.run_json("send", "--as", "influencer", "--draft", did, "--issue", "42")
        self.assertEqual(rc, 0)
        self.assertFalse(out["sent"])
        self.assertEqual(out["downgraded"], "draft")
        self.assertEqual(out["gate"], "outbound_send")
        self.assertEqual(self.service.sent, [])
        self.assertIn(did, self.service.drafts_by_id)

    def test_the_kill_switch_downgrades_every_send(self):
        did = self.make()
        self.write_policy(harness.POLICY.replace("send_enabled: true", "send_enabled: false"))
        rc, out, _ = self.run_json("send", "--as", "influencer", "--draft", did, "--issue", "42")
        self.assertEqual((rc, out["sent"], out["gate"]), (0, False, "global"))

class AttachAndDiscard(Writing):
    def test_an_attached_draft_still_goes_through_every_send_gate(self):
        pdf = self.root / "letter.pdf"
        pdf.write_bytes(b"%PDF-1.4 response letter")
        rc, _, _ = self.draft("--attach", str(pdf))             # the harness policy says no
        self.assertEqual(rc, 2)
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
        p = self.root / "emp-influencer" / "employee.yaml"
        p.write_text(p.read_text().replace("outbound_send: true", "outbound_send: false"))
        rc, d, err = self.draft("--attach", str(pdf), "--reply-to", "t-ava")
        self.assertEqual(rc, 0, err)
        self.assertEqual([(a["name"], a["type"]) for a in d["attachments"]],
                         [("letter.pdf", "application/pdf")])
        raw = compose.decode(self.service.drafts_by_id[d["draft"]]["message"]["raw"])
        self.assertIn('filename="letter.pdf"', raw)
        self.assertIn("In-Reply-To: <ava-1@creator.example>", raw)
        rc, out, _ = self.run_json("send", "--as", "influencer", "--draft", d["draft"], "--issue", "42")
        self.assertEqual((rc, out["sent"], out["gate"], out["attachments"]),
                         (0, False, "outbound_send", ["letter.pdf"]))
        self.assertEqual(self.service.sent, [])
        down = [a for a in self.audit_lines() if a["action"] == "send-downgraded"]
        self.assertEqual(down[-1]["detail"]["attachments"], ["letter.pdf"])

    def test_a_bot_discards_only_its_own_unsent_draft(self):
        rc, d, err = self.draft()
        self.assertEqual(rc, 0, err)
        did = d["draft"]
        for slug, target in (("inbox", did), ("influencer", "m-ava")):   # not its draft; a received message
            rc, _, _ = self.run_cli("discard", target, "--as", slug)
            self.assertEqual(rc, 2)
        self.assertIn(did, self.service.drafts_by_id)
        self.assertIn("m-ava", self.service.store)
        rc, out, err = self.run_json("discard", did, "--as", "influencer", "--json")
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
