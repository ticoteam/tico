"""Once the owner turns sending on (`outbound_send: true`), the bot follows its rules without a per-message approval.

Three kinds of recipient need none: an internal address, an address the owner lists in `forward_to:`, and the sender of
the thread being replied to (a reply, no added recipients). Anything else still needs an allowance or an approval, and
the caps, the blocklist and owner-handles-personally still apply. A computer with no registry has a built-in policy.
"""

import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake, harness                                                 # noqa: E402
from harness import MANIFESTS, POLICY, Stage2                        # noqa: E402

from connectors.mail import Refused, policy as pl, access            # noqa: E402

AVA, STRANGER, ME = "ava@creator.example", "stranger@elsewhere.example", "owner@personal-domain.example"
INBOX_ON = """
name: inbox
outbound_send: true
forward_to:
  - Owner@Personal-Domain.example
  - press@agency.example
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
FORWARD = ("A press request came in; the sender wrote: please see https://press.example/story about us.\n"
           "The original is in the mailbox.\n\nAna\n")


class Chain(Stage2):
    manifests = dict(MANIFESTS, inbox=INBOX_ON)

    def decide(self, to, cc=(), slug="inbox", policy=None, **kw):
        return pl.check_send(policy or self.policy(), slug, "ana@acme.example", list(to), list(cc),
                             root=self.root, **kw)

    def test_the_sender_of_the_thread_being_answered_goes_without_approval(self):
        d = self.decide([AVA], thread_id=THREAD, thread_senders=[AVA])
        self.assertTrue(d.allowed, d)
        self.assertIn("sender of the thread", [c for c in d["checks"] if c["gate"] == "recipient"][0]["detail"])

    def test_nothing_else_does(self):
        self.assertEqual(self.decide([STRANGER])["gate"], "recipient")
        # Not a reply: the thread's sender is just another outside address.
        self.assertEqual(self.decide([AVA], thread_senders=[AVA])["gate"], "recipient")
        # A reply that adds a recipient, or copies someone outside, is not "a reply to the sender".
        self.assertEqual(self.decide([AVA, STRANGER], thread_id=THREAD, thread_senders=[AVA])["gate"], "recipient")
        self.assertEqual(self.decide([AVA], cc=[STRANGER], thread_id=THREAD, thread_senders=[AVA])["gate"], "recipient")
        # Someone who never wrote into the thread is not its sender.
        self.assertEqual(self.decide([STRANGER], thread_id=THREAD, thread_senders=[AVA])["gate"], "recipient")

    def test_an_approval_still_covers_an_address_the_rules_do_not(self):
        pl.RUN = harness.gh_answer(body=f"Send to: {STRANGER}")
        d = self.decide([STRANGER], approval="77")
        self.assertTrue(d.allowed, d)

    def test_with_sending_off_none_of_it_applies(self):
        p = self.root / "emp-inbox" / "employee.yaml"
        p.write_text(p.read_text().replace("outbound_send: true", "outbound_send: false"))
        for to, extra in (([ME], {}), ([AVA], {"thread_id": THREAD, "thread_senders": [AVA]})):
            d = self.decide(to, **extra)
            self.assertEqual(d["gate"], "outbound_send", d)

    def test_caps_the_blocklist_and_owner_handles_personally_still_apply(self):
        blocked = harness.POLICY.replace("addresses: [blocked@nope.example]",
                                         f"addresses: [blocked@nope.example, {ME}]")
        self.write_policy(blocked)
        self.assertEqual(self.decide([ME])["gate"], "blocklist")
        self.write_policy(harness.POLICY.replace("addresses: [investor@fund.example]", f"addresses: [investor@fund.example, {AVA}]"))
        self.assertEqual(self.decide([AVA], thread_id=THREAD, thread_senders=[AVA])["gate"], "owner_handles_personally")
        self.write_policy(harness.POLICY)
        # Two outside forward targets on one message is over the external-recipient cap of one.
        self.assertEqual(self.decide([ME, "press@agency.example"])["gate"], "caps")
        conn = self.conn()
        day = pl.datetime.now(pl.zone("America/Los_Angeles")).strftime("%Y-%m-%d")
        from connectors.mail import db
        for i in range(20):
            db.claim_send(conn, f"k{i}", "inbox", "ana@acme.example", "1", [f"x{i}@acme.example"], "s", day=day)
        self.assertEqual(self.decide([ME], conn=conn)["gate"], "caps")

    def test_the_cooldown_a_registry_sets_still_applies(self):
        conn = self.conn()
        from connectors.mail import db
        day = pl.datetime.now(pl.zone("America/Los_Angeles")).strftime("%Y-%m-%d")
        db.claim_send(conn, "k", "inbox", "ana@acme.example", "1", [ME], "s", day=day)
        d = self.decide([ME], conn=conn)
        self.assertEqual((d["gate"], "cooldown" in d["reason"]), ("caps", True))

    def test_an_attachment_needs_an_approval_on_every_path_that_otherwise_needs_none(self):
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
        reply = {"thread_id": THREAD, "thread_senders": [AVA]}
        self.assertTrue(self.decide([AVA], **reply).allowed)             # no file: the sender needs no yes
        for to, extra in (([AVA], reply), ([ME], {}), (["colleague@acme.example"], {})):
            d = self.decide(to, attachments=1, **extra)
            self.assertEqual(d["gate"], "attachments", (to, d))
        pl.RUN = harness.gh_answer(body=f"Send to: {AVA}")
        self.assertTrue(self.decide([AVA], attachments=1, approval="77", **reply).allowed)
        pl.RUN = harness.gh_answer(state="OPEN", body=f"Send to: {AVA}")        # an open Issue is no yes
        self.assertEqual(self.decide([AVA], attachments=1, approval="77", **reply)["gate"], "attachments")

    def test_the_default_policy_refuses_attachments_and_an_explicit_true_allows_them(self):
        self.write_policy(harness.POLICY.replace("  allow_attachments: false\n", ""))
        self.assertFalse(self.policy()["defaults"]["allow_attachments"])
        self.assertFalse(pl.FALLBACK_DEFAULTS["allow_attachments"])
        with self.assertRaises(Refused):
            pl.check_draft(self.policy(), "inbox", "ana@acme.example", [AVA], attachments=1)
        pl.RUN = harness.gh_answer(body=f"Send to: {AVA}")
        self.assertEqual(self.decide([AVA], attachments=1, approval="77")["gate"], "attachments")
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
        pl.check_draft(self.policy(), "inbox", "ana@acme.example", [AVA], attachments=1)
        self.assertTrue(self.decide([AVA], attachments=1, approval="77").allowed)

    def test_forward_to_reads_a_list_or_a_comma_string_and_nothing_else(self):
        self.assertEqual(access.forward_to({"forward_to": "A@x.example, b@y.example;a@x.example, nope"}),
                         ["a@x.example", "b@y.example"])
        self.assertEqual(access.forward_to({"forward_to": [" C@z.example ", "", 5]}), ["c@z.example"])
        self.assertEqual(access.forward_to({}), [])


class Cli(Stage2):
    """The whole path: a reply to the sender, and a forward to the owner's other address, sent with no approval."""
    manifests = dict(MANIFESTS, inbox=INBOX_ON)

    def corpus(self):
        return [fake.message("m-ava", THREAD, INCOMING, body="Can you help?", epoch_ms=1788364800000)]

    def test_a_reply_to_the_sender_is_sent(self):
        rc, out, err = self.run_json("reply", "--as", "inbox", "--thread", THREAD, "--body-file", self.body_file(BODY),
                                     "--issue", "1", "--json")
        self.assertEqual((rc, out.get("sent"), out.get("gate")), (0, True, None), (out, err))
        self.assertEqual(len(self.service.sent), 1)

    def test_a_forward_to_the_owner_is_sent_and_may_quote_a_link_on_another_host(self):
        rc, p, err = self.run_json("draft", "--as", "inbox", "--to", ME, "--subject", "\U0001F514 Tico inbound: Press request",
                                   "--body-file", self.body_file(FORWARD), "--issue", "2", "--json")
        self.assertEqual(rc, 0, err)
        rc, out, _ = self.run_json("send", "--as", "inbox", "--draft", p["draft"], "--issue", "2")
        self.assertEqual((rc, out["sent"]), (0, True), out)

    def test_an_attached_reply_to_the_sender_waits_for_an_approval(self):
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
        (self.root / "emp-inbox" / "answer.pdf").write_bytes(b"%PDF-1.4 the answer")
        rc, p, err = self.run_json("draft", "--as", "inbox", "--reply-to", THREAD, "--body-file", self.body_file(BODY),
                                   "--attach", "answer.pdf", "--issue", "1", "--json")
        self.assertEqual((rc, p.get("send_needs_approval")), (0, True), err)
        rc, out, _ = self.run_json("send", "--as", "inbox", "--draft", p["draft"], "--issue", "1")
        self.assertEqual((out["sent"], out["gate"]), (False, "attachments"), out)
        self.assertEqual(self.service.sent, [])
        pl.RUN = harness.gh_answer(body=f"Send to: {AVA}")
        rc, out, _ = self.run_json("send", "--as", "inbox", "--draft", p["draft"], "--issue", "1",
                                   "--approval-issue", "77")
        self.assertEqual((rc, out["sent"]), (0, True), out)

    def test_a_stranger_is_still_a_draft(self):
        rc, p, err = self.run_json("draft", "--as", "inbox", "--to", STRANGER, "--subject", "Hello there",
                                   "--body-file", self.body_file(BODY), "--issue", "3", "--json")
        self.assertEqual(rc, 0, err)
        rc, out, _ = self.run_json("send", "--as", "inbox", "--draft", p["draft"], "--issue", "3")
        self.assertEqual((out["sent"], out["gate"]), (False, "recipient"))
        self.assertEqual(self.service.sent, [])


class NoRegistry(Stage2):
    """A Docker computer: there is no registry, so there is no mail-policy.yaml to read."""
    manifests = dict(MANIFESTS, inbox=INBOX_ON)

    def setUp(self):
        super().setUp()
        self._registry = pl.REGISTRY
        pl.REGISTRY = self.root / "no-registry"
        pl.POLICY_FILE = pl.REGISTRY / "mail-policy.yaml"
        pl.HUB_GET = lambda path: None

    def tearDown(self):
        pl.REGISTRY = self._registry
        super().tearDown()

    def test_the_built_in_policy_has_sending_on_the_usual_caps_and_no_blocklist(self):
        pol = pl.load(slug="inbox")
        self.assertTrue(pol["builtin"] and pol["send_enabled"])
        self.assertEqual(pol["blocklist"], {"addresses": [], "domains": []})
        self.assertEqual(pol["allowances"], [])
        self.assertEqual((pol["defaults"]["max_sends_per_day"], pol["defaults"]["max_external_recipients"],
                          pol["defaults"]["allow_attachments"]), (20, 1, False))
        self.assertEqual(pol["internal_domains"], ["acme.example"])            # the bot's own mailbox domain

    def test_internal_domains_come_from_the_roster_without_public_providers(self):
        pl.HUB_GET = lambda path: {"people": [{"id": "a", "email": "ana@gmail.com"}, {"id": "b", "email": "Bo@Team.example"},
                                               {"id": "c", "email": "c@acme.example"}]} if path == "org" else None
        self.assertEqual(pl.load(slug="inbox")["internal_domains"], ["acme.example", "team.example"])
        pl.HUB_GET = lambda path: {"people": [{"email": "x@gmail.com"}]}
        self.assertNotIn("gmail.com", pl.load(slug="inbox")["internal_domains"])
        import os
        os.environ["TICO_INTERNAL_DOMAINS"] = "own.example, Other.example"
        try:
            self.assertEqual(pl.load(slug="inbox")["internal_domains"], ["own.example", "other.example"])
        finally:
            del os.environ["TICO_INTERNAL_DOMAINS"]

    def test_the_send_chain_passes_with_no_registry(self):
        pol = pl.load(slug="inbox")
        for to in (["colleague@acme.example"], [ME]):
            d = pl.check_send(pol, "inbox", "ana@acme.example", to, root=self.root)
            self.assertTrue(d.allowed, d)
        self.assertEqual(pl.check_send(pol, "inbox", "ana@acme.example", [STRANGER], root=self.root)["gate"], "recipient")
        # The same owner-listed target can be written to again the same day: the built-in cooldown is 0.
        from connectors.mail import db
        conn = self.conn()
        day = pl.datetime.now(pl.zone("America/Los_Angeles")).strftime("%Y-%m-%d")
        db.claim_send(conn, "k", "inbox", "ana@acme.example", "1", [ME], "s", day=day)
        self.assertTrue(pl.check_send(pol, "inbox", "ana@acme.example", [ME], conn=conn, root=self.root).allowed)

    def test_a_registry_policy_file_wins_when_there_is_one(self):
        pl.REGISTRY.mkdir()
        pl.POLICY_FILE.write_text(POLICY.replace("send_enabled: true", "send_enabled: false"))
        pol = pl.load(slug="inbox")
        self.assertFalse(pol["builtin"] or pol["send_enabled"])
        self.assertEqual(pl.check_send(pol, "inbox", "ana@acme.example", [ME], root=self.root)["gate"], "global")


if __name__ == "__main__":
    unittest.main()
