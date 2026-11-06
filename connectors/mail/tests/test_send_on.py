"""A send goes to Tico whoever it is to: the CLI queues it and Tico's sending policy decides.

A reply to the thread's sender, a forward to the owner's other address and a first note to a stranger all reach
`queue_send`; the CLI keeps no recipient gate of its own. A draft's files go on the run's task and into the optional
approval summary, and the team's own domains (the "internal" the lint uses) come from the roster.
"""

import hashlib, os, sys, unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake, harness                                                 # noqa: E402,F401
from harness import MANIFESTS, Stage2                                # noqa: E402

from connectors.mail import policy as pl                             # noqa: E402
from connectors.mail import __main__ as cli                          # noqa: E402

AVA, STRANGER, ME = "ava@creator.example", "stranger@elsewhere.example", "owner@personal-domain.example"
INBOX_ON = """
name: inbox
access:
  - service: gmail
    identity: "ana@acme.example"
    can: [read, draft, send]
"""
INCOMING = {"From": "Ava Reyes <ava@creator.example>", "To": "ana@acme.example",
            "Subject": "Support question", "Message-ID": "<ava-1@creator.example>",
            "Date": "Tue, 02 Sep 2026 09:00:00 -0700"}
THREAD = "t-ava"
BODY = ("Hi Ava,\n\nThanks for writing in. Support has it and will answer within a day.\n\nAna\n"
        "Founder, Acme\n")
FORWARD = ("A press request came in; the sender wrote: please see the story they linked about us.\n"
           "The original is in the mailbox.\n\nAna\n")


def file_meta(name, data):
    return {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class Cli(Stage2):
    """The whole path from `mail draft` / `mail reply` to Tico's queue."""
    manifests = dict(MANIFESTS, inbox=INBOX_ON)

    def corpus(self):
        return [fake.message("m-ava", THREAD, INCOMING, body="Can you help?", epoch_ms=1788364800000)]

    def test_a_reply_to_the_sender_is_queued(self):
        rc, out, err = self.run_json("reply", "--as", "inbox", "--thread", THREAD, "--body-file", self.body_file(BODY),
                                     "--issue", "1", "--json")
        self.assertEqual((rc, out.get("queued"), out.get("gate")), (0, True, None), (out, err))
        self.assertEqual(len(self.queue_requests), 1)
        self.assertEqual(self.queue_requests[0]["thread_id"], THREAD)
        self.assertEqual(self.service.sent, [])                   # the connector dispatches after the undo window

    def test_a_forward_to_the_owner_and_a_note_to_a_stranger_both_reach_tico(self):
        for n, to, subject, body in (("2", ME, "Press request", FORWARD), ("3", STRANGER, "Hello there", BODY)):
            rc, p, err = self.run_json("draft", "--as", "inbox", "--to", to, "--subject", subject,
                                       "--body-file", self.body_file(body), "--issue", n, "--json")
            self.assertEqual(rc, 0, err)
            rc, out, _ = self.run_json("send", "--as", "inbox", "--draft", p["draft"], "--issue", n)
            self.assertEqual((rc, out["queued"], out["state"]), (0, True, "pending"), out)
        self.assertEqual(len(self.queue_requests), 2)
        self.assertEqual(self.service.sent, [])

    def test_attached_files_go_on_the_task_and_into_the_approval_summary(self):
        data = b"%PDF-1.4 the answer"
        (self.root / "emp-inbox" / "answer.pdf").write_bytes(data)
        task, uploads = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", []
        cli.TASK_ATTACH = lambda *call: uploads.append(call) or "f-answer-0001"
        rc, p, err = self.run_json("draft", "--as", "inbox", "--reply-to", THREAD, "--body-file", self.body_file(BODY),
                                   "--attach", "answer.pdf", "--issue", task, "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual([(t, n, d) for t, n, d, _ in uploads], [(task, "answer.pdf", data)])
        request = p["approval_request"]
        self.assertEqual(request["payload"]["attachments"],
                         [dict(file_meta("answer.pdf", data), file_id="f-answer-0001")])
        self.assertEqual(request["command"], f"mail send --as inbox --for ana@acme.example --draft {p['draft']}")
        rc, out, _ = self.run_json("send", "--as", "inbox", "--draft", p["draft"], "--issue", task)
        self.assertEqual((rc, out["queued"]), (0, True), out)
        # No credential or a failed upload: the draft and the summary still stand, without file ids.
        cli.TASK_ATTACH = lambda *call: (_ for _ in ()).throw(RuntimeError("offline"))
        (self.root / "emp-inbox" / "answer.pdf").write_bytes(data + b" v2")
        rc, p, err = self.run_json("draft", "--as", "inbox", "--reply-to", THREAD, "--body-file", self.body_file(BODY),
                                   "--attach", "answer.pdf", "--issue", task, "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(p["approval_request"]["payload"]["attachments"], [file_meta("answer.pdf", data + b" v2")])

    def test_a_bot_environment_never_reaches_a_server_from_the_tests(self):
        # The variables a bot's run carries, pointing at a port nothing listens on: the harness's stub takes the upload.
        (self.root / "emp-inbox" / "answer.pdf").write_bytes(b"%PDF-1.4 the answer")
        task = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        os.environ.update(HUB_API_URL="http://127.0.0.1:9", HUB_TOKEN="t", HUB_TASK_ID=task)
        attempts = []

        def refuse(*a, **k):
            attempts.append(a)
            raise OSError("network is off in the mail tests")
        with mock.patch("socket.socket.connect", refuse), mock.patch("socket.create_connection", refuse):
            rc, p, err = self.run_json("draft", "--as", "inbox", "--reply-to", THREAD, "--body-file", self.body_file(BODY),
                                       "--attach", "answer.pdf", "--issue", "1", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(attempts, [])
        self.assertEqual([(t, n) for t, n, _, _ in self.task_uploads], [(task, "answer.pdf")])

    def test_the_draft_text_lists_files_without_an_attachment_approval_request(self):
        (self.root / "emp-inbox" / "answer.pdf").write_bytes(b"%PDF-1.4 the answer")
        rc, out, err = self.run_cli("draft", "--as", "inbox", "--reply-to", THREAD, "--body-file", self.body_file(BODY),
                                    "--attach", "answer.pdf", "--issue", "1")
        self.assertEqual(rc, 0, err)
        self.assertIn("answer.pdf (19 bytes)", out)
        self.assertNotIn("hub approval request", out)


class TeamDomains(Stage2):
    """The team's own domains: the owner's list, else the bot's mailbox and the roster, never a public provider."""
    manifests = dict(MANIFESTS, inbox=INBOX_ON)

    def test_internal_domains_come_from_the_roster_without_public_providers(self):
        os.environ.pop("TICO_INTERNAL_DOMAINS", None)
        pl.HUB_GET = lambda path: {"people": [{"id": "a", "email": "ana@gmail.com"}, {"id": "b", "email": "Bo@Team.example"},
                                               {"id": "c", "email": "c@acme.example"}]} if path == "org" else None
        with mock.patch.object(pl, "REGISTRY", self.root / "no-registry"):
            self.assertEqual(pl.team_domains("inbox"), ["acme.example", "team.example"])
            pl.HUB_GET = lambda path: {"people": [{"email": "x@gmail.com"}]}
            self.assertNotIn("gmail.com", pl.team_domains("inbox"))
            os.environ["TICO_INTERNAL_DOMAINS"] = "own.example, Other.example"
            self.assertEqual(pl.team_domains("inbox"), ["own.example", "other.example"])


if __name__ == "__main__":
    unittest.main()
