"""Once the owner turns sending on (`outbound_send: true`), the bot follows its rules without a per-message approval.

Three kinds of recipient need none: an internal address, an address the owner lists in `forward_to:`, and the sender of
the thread being replied to (a reply, no added recipients). Anything else still needs an allowance or an approval, and
the caps, the blocklist and owner-handles-personally still apply. A computer with no registry has a built-in policy.
"""

import base64, hashlib, os, sys, unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake, harness                                                 # noqa: E402
from harness import MANIFESTS, POLICY, Stage2                        # noqa: E402

from connectors.mail import Refused, compose, policy as pl, access   # noqa: E402
from connectors.mail import __main__ as cli                          # noqa: E402

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


APPROVAL, ORDER = "11111111-2222-3333-4444-555555555555", "66666666-7777-8888-9999-000000000000"


def file_meta(name, data):
    return {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


FILE_A, FILE_B = file_meta("a.pdf", b"%PDF-1.4 A"), file_meta("b.pdf", b"%PDF-1.4 B")
BODY_SHA = hashlib.sha256(BODY.strip().encode()).hexdigest()           # what `mail draft` reports for BODY


def send_approval(to, files, draft="d-1", **row):
    """A decided Tico `send` approval as GET approvals/<id> returns it."""
    return dict({"id": APPROVAL, "kind": "send", "decision": "approved", "consumed_at": None,
                 "payload": {"to": to, "cc": [], "subject": "s", "body_sha256": BODY_SHA,
                             "mailbox": "ana@acme.example", "draft": draft, "attachments": files}}, **row)


def owner_order(slug="inbox"):
    """The owner's Tico message telling the bot to send: a standing yes for mail without files."""
    return {"id": ORDER, "from_actor": f"human:{access.OWNER}", "to_actor": f"bot:{slug}",
            "body": "Please send it to them."}


def hub(*rows):
    found = {f"approvals/{r['id']}" if "kind" in r else f"messages/{r['id']}": r for r in rows}
    pl.HUB_GET = lambda path: found.get(path)


class Chain(Stage2):
    manifests = dict(MANIFESTS, inbox=INBOX_ON)

    def decide(self, to, cc=(), slug="inbox", policy=None, body=BODY, **kw):
        return pl.check_send(policy or self.policy(), slug, "ana@acme.example", list(to), list(cc),
                             root=self.root, body=body, **kw)

    def test_an_owner_message_to_a_bot_named_by_its_label_is_matched_by_the_bot_id(self):
        bot_id = "0190f2a1-7c3e-7d1a-9b2e-5f4a3c2b1a00"             # Tico writes to_actor with the id, not the label
        found = {f"messages/{ORDER}": owner_order(slug=bot_id), "bots/mira": {"id": bot_id, "stage": "active"}}
        pl.HUB_GET = lambda path: found.get(path)
        self.assertTrue(pl._hub_message_check(ORDER, "mira")["ok"])
        self.assertFalse(pl._hub_message_check(ORDER, "inbox")["ok"])     # the label of another bot

    def test_the_sender_of_the_thread_being_answered_goes_without_approval(self):
        d = self.decide([AVA], thread_id=THREAD, thread_senders=[AVA])
        self.assertTrue(d.allowed, d)
        self.assertIn("sender of the thread", [c for c in d["checks"] if c["gate"] == "recipient"][0]["detail"])

    def test_bot_yaml_alone_never_turns_sending_on(self):
        # bot.yaml says outbound_send: true and lists forward targets (a bot, or BotOps' own run, pushed it), but no
        # person has turned sending on in Tico: every outside send needs an approval.
        reply = {"thread_id": THREAD, "thread_senders": [AVA]}
        pl.SERVER_GET = lambda slug: {"bot": slug, "set": False, "outbound_send": False, "forward_to": []}
        for to, extra in (([AVA], reply), ([ME], {})):
            d = self.decide(to, **extra)
            self.assertEqual(d["gate"], "outbound_send", d)
            self.assertIn("Mail sending", d["reason"])
        self.assertFalse(pl.is_forward(self.policy(), "inbox", [ME]))
        # The owner's own per-message yes still sends that one message.
        pl.RUN = harness.gh_answer(body=f"Send to: {AVA}")
        self.assertTrue(self.decide([AVA], approval="77").allowed)

    def test_a_person_turning_it_on_in_tico_covers_only_the_targets_they_approved(self):
        pl.SERVER_GET = lambda slug: {"bot": slug, "set": True, "outbound_send": True,
                                      "forward_to": ["owner@personal-domain.example"], "updated_by": "human:ana"}
        self.assertTrue(self.decide([AVA], thread_id=THREAD, thread_senders=[AVA]).allowed)
        self.assertTrue(self.decide([ME]).allowed)
        # press@agency.example is in bot.yaml's forward_to but not approved in Tico: it needs a per-message approval.
        d = self.decide(["press@agency.example"])
        self.assertEqual(d["gate"], "recipient", d)
        self.assertIn("not approved in Tico", d["reason"])
        # The person turning it off wins over bot.yaml's outbound_send: true.
        pl.SERVER_GET = lambda slug: {"bot": slug, "set": True, "outbound_send": False, "forward_to": [ME]}
        self.assertEqual(self.decide([ME])["gate"], "outbound_send")

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

    def test_an_onboarding_bot_sends_nothing_even_with_a_matching_approval(self):
        pl.RUN = harness.gh_answer(body=f"Send to: {STRANGER}")
        hub(owner_order())
        off = {"set": True, "outbound_send": False, "forward_to": []}
        for approval in ("77", ORDER):                                   # a GitHub Issue, the owner's message
            pl.SERVER_GET = lambda slug: dict(off, bot=slug)
            self.assertTrue(self.decide([STRANGER], approval=approval).allowed, approval)   # it lifts sending off
            pl.SERVER_GET = lambda slug: dict(off, bot=slug, onboarding=True)
            d = self.decide([STRANGER], approval=approval)
            self.assertEqual((d.allowed, d["gate"]), (False, "onboarding"), d)

    def test_a_run_that_cannot_read_the_stage_sends_nothing_even_with_a_matching_approval(self):
        # No credential, Tico unreachable or a bad answer: the run cannot tell an onboarding bot from an Active one.
        pl.RUN = harness.gh_answer(body=f"Send to: {STRANGER}")
        def unreachable(slug):
            raise OSError("Tico is unreachable")
        for server in (lambda slug: None, unreachable, lambda slug: ["not", "a", "setting"], lambda slug: {"bot": slug}):
            pl.SERVER_GET = server
            d = self.decide([STRANGER], approval="77")
            self.assertEqual((d.allowed, d["gate"]), (False, "onboarding"), d)
        # The owner sends on their own word, with or without Tico.
        self.assertTrue(self.decide([STRANGER], slug=pl.OWNER, approval="77", verbs=["send"]).allowed)

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
            d = self.decide(to, attachments=[FILE_A], draft="d-1", **extra)
            self.assertEqual(d["gate"], "attachments", (to, d))
        hub(send_approval([AVA], [FILE_A]))
        self.assertTrue(self.decide([AVA], attachments=[FILE_A], draft="d-1", approval=APPROVAL, **reply).allowed)
        hub(send_approval(["colleague@acme.example"], [FILE_A]))              # all internal: the same approval
        self.assertTrue(self.decide(["colleague@acme.example"], attachments=[FILE_A], draft="d-1",
                                    approval=APPROVAL).allowed)
        hub(send_approval([AVA], [FILE_A], decision="pending"))                # undecided is no yes
        self.assertEqual(self.decide([AVA], attachments=[FILE_A], draft="d-1", approval=APPROVAL,
                                     **reply)["gate"], "attachments")
        # A count with no file details can never match an approval.
        hub(send_approval([AVA], [FILE_A]))
        self.assertEqual(self.decide([AVA], attachments=1, draft="d-1", approval=APPROVAL, **reply)["gate"],
                         "attachments")

    def test_an_approval_for_file_a_does_not_send_file_b_or_a_changed_a(self):
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
        reply = {"thread_id": THREAD, "thread_senders": [AVA], "approval": APPROVAL}
        hub(send_approval([AVA], [FILE_A]))
        changed = dict(FILE_A, sha256=hashlib.sha256(b"%PDF-1.4 A, edited").hexdigest())
        for files, draft in (([FILE_B], "d-1"), ([changed], "d-1"), ([dict(FILE_A, size=FILE_A["size"] + 1)], "d-1"),
                             ([dict(FILE_A, name="b.pdf")], "d-1"), ([FILE_A, FILE_B], "d-1"), ([FILE_A], "d-2")):
            d = self.decide([AVA], attachments=files, draft=draft, **reply)
            self.assertEqual(d["gate"], "attachments", (files, draft, d))
        hub(send_approval([AVA], [FILE_A, FILE_B]))                            # nor a subset of what was approved
        self.assertEqual(self.decide([AVA], attachments=[FILE_A], draft="d-1", **reply)["gate"], "attachments")
        self.assertTrue(self.decide([AVA], attachments=[FILE_B, FILE_A], draft="d-1", **reply).allowed)

    def test_an_owner_send_message_does_not_approve_files(self):
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
        hub(owner_order())
        self.assertTrue(self.decide([STRANGER], approval=ORDER).allowed)                   # without files: as before
        d = self.decide([AVA], attachments=[FILE_A], draft="d-1", approval=ORDER, thread_id=THREAD,
                        thread_senders=[AVA])
        self.assertEqual(d["gate"], "attachments", d)
        self.assertIn("not a Tico send approval", d["reason"])

    def test_a_send_approval_must_list_the_files(self):
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
        kw = {"attachments": [FILE_A], "draft": "d-1", "approval": APPROVAL,   # a reply: only the files need a yes
              "thread_id": THREAD, "thread_senders": [AVA]}
        row = send_approval([AVA], [FILE_A])
        del row["payload"]["attachments"]
        hub(row)                                                               # the old payload: recipients only
        self.assertTrue(self.decide([AVA], approval=APPROVAL).allowed)         # still fine without files
        self.assertEqual(self.decide([AVA], **kw)["gate"], "attachments")
        hub(send_approval([AVA], [{"name": "a.pdf", "size": FILE_A["size"]}]))  # no hash
        self.assertEqual(self.decide([AVA], **kw)["gate"], "attachments")
        hub(send_approval([AVA], [FILE_A]))
        self.assertTrue(self.decide([AVA], **kw).allowed)

    def test_a_github_issue_approves_files_only_when_it_names_each_one(self):
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
        kw = {"draft": "d-1", "approval": "77", "thread_id": THREAD, "thread_senders": [AVA]}
        pl.RUN = harness.gh_answer(body=f"Send to: {AVA}")
        self.assertTrue(self.decide([AVA], approval="77").allowed)                        # without files: as before
        self.assertEqual(self.decide([AVA], attachments=[FILE_A], **kw)["gate"], "attachments")
        pl.RUN = harness.gh_answer(body=f"Send to: {AVA}\n\nAttach: a.pdf sha256 {FILE_A['sha256']}")
        self.assertTrue(self.decide([AVA], attachments=[FILE_A], **kw).allowed)
        for files in ([FILE_A, FILE_B], [FILE_B], [dict(FILE_B, name="a.pdf")]):          # one unnamed, or changed
            self.assertEqual(self.decide([AVA], attachments=files, **kw)["gate"], "attachments", files)
        pl.RUN = harness.gh_answer(body=f"Send to: {AVA}\n\nAttach: {FILE_A['sha256']}")  # the hash, no name
        self.assertEqual(self.decide([AVA], attachments=[FILE_A], **kw)["gate"], "attachments")
        pl.RUN = harness.gh_answer(body=f"Send to: {AVA}\n\na.pdf {FILE_A['sha256']}\nb.pdf {FILE_B['sha256']}")
        self.assertEqual(self.decide([AVA], attachments=[FILE_A], **kw)["gate"], "attachments")   # not the set
        pl.RUN = harness.gh_answer(body=f"About {AVA}: a.pdf {FILE_A['sha256']}")           # no Send to: line
        self.assertEqual(self.decide([AVA], attachments=[FILE_A], **kw)["gate"], "attachments")

    def test_the_default_policy_refuses_attachments_and_an_explicit_true_allows_them(self):
        self.write_policy(harness.POLICY.replace("  allow_attachments: false\n", ""))
        self.assertFalse(self.policy()["defaults"]["allow_attachments"])
        self.assertFalse(pl.FALLBACK_DEFAULTS["allow_attachments"])
        with self.assertRaises(Refused):
            pl.check_draft(self.policy(), "inbox", "ana@acme.example", [AVA], attachments=1)
        hub(send_approval([AVA], [FILE_A]))
        kw = {"attachments": [FILE_A], "draft": "d-1", "approval": APPROVAL}
        self.assertEqual(self.decide([AVA], **kw)["gate"], "attachments")
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
        pl.check_draft(self.policy(), "inbox", "ana@acme.example", [AVA], attachments=1)
        self.assertTrue(self.decide([AVA], **kw).allowed)

    def test_a_tico_send_approval_covers_only_the_text_it_names(self):
        hub(send_approval([STRANGER], []))
        self.assertTrue(self.decide([STRANGER], approval=APPROVAL).allowed)
        # Gmail's CRLF line ends, or the final newline `sha256sum` of the body file counts, are the same text.
        for same in (BODY.replace("\n", "\r\n"), BODY.strip() + "\n", "\n" + BODY.strip()):
            self.assertTrue(self.decide([STRANGER], approval=APPROVAL, body=same).allowed, repr(same))
        # The same addresses with one character changed: refused, and the reason says why.
        for changed in (BODY.replace("a day", "a dag"), BODY.replace("Hi Ava,", "Hi Ava!"), BODY + "P.S. 50% off\n"):
            d = self.decide([STRANGER], approval=APPROVAL, body=changed)
            self.assertEqual(d["gate"], "recipient", d)
            self.assertIn("the draft changed after it was approved; ask for a new yes", d["reason"])
            self.assertIn(BODY_SHA[:12], d["reason"])
        self.assertEqual(self.decide([STRANGER], approval=APPROVAL, body=None)["gate"], "recipient")   # no text: no yes
        row = send_approval([STRANGER], [])                                    # hashed by `sha256sum body.txt`
        row["payload"]["body_sha256"] = hashlib.sha256((BODY.strip() + "\n").encode()).hexdigest()
        hub(row)
        self.assertTrue(self.decide([STRANGER], approval=APPROVAL, body=BODY.strip()).allowed)
        for digest in ("", None, "a" * 64):                                    # a placeholder hash approves nothing
            row = send_approval([STRANGER], [])
            row["payload"]["body_sha256"] = digest
            hub(row)
            self.assertEqual(self.decide([STRANGER], approval=APPROVAL)["gate"], "recipient", digest)
        # The yes no longer lifts what it lifted: sending off, the cap, the reviewer (approval_check's `full`).
        hub(send_approval([STRANGER], []))
        self.assertFalse(pl.approval_check(APPROVAL, [STRANGER], body=BODY + "!", slug="inbox")["full"])
        self.assertTrue(pl.approval_check(APPROVAL, [STRANGER], body=BODY, slug="inbox")["full"])

    def test_with_files_the_text_must_match_too(self):
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
        kw = {"attachments": [FILE_A], "draft": "d-1", "approval": APPROVAL, "thread_id": THREAD, "thread_senders": [AVA]}
        hub(send_approval([AVA], [FILE_A]))
        self.assertTrue(self.decide([AVA], **kw).allowed)
        d = self.decide([AVA], body=BODY.replace("Support", "Sales"), **kw)  # same draft id, same file, new words
        self.assertEqual(d["gate"], "attachments", d)
        self.assertIn("the draft changed after it was approved", d["reason"])

    def test_a_github_issue_or_the_owners_message_carries_no_hash_and_is_unchanged(self):
        pl.RUN = harness.gh_answer(body=f"Send to: {STRANGER}")
        self.assertTrue(self.decide([STRANGER], approval="77", body=BODY + "!").allowed)
        hub(owner_order())
        self.assertTrue(self.decide([STRANGER], approval=ORDER, body=BODY + "!").allowed)

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

    def test_a_text_approval_sends_its_draft_and_not_the_draft_edited_after_the_yes(self):
        rc, p, err = self.run_json("draft", "--as", "inbox", "--to", STRANGER, "--subject", "Support question",
                                   "--body-file", self.body_file(BODY), "--issue", "1", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(p["body_sha256"], BODY_SHA)                         # the value to put in the request
        hub(send_approval([STRANGER], []))                                    # approves BODY's sha256, no files
        def edit(text, crlf=False):
            raw = compose.decode(compose.build([STRANGER], "Support question", text, from_addr="ana@acme.example"))
            raw = raw.replace("\n", "\r\n") if crlf else raw
            self.service.drafts().update(id=p["draft"], body={"message": {
                "raw": base64.urlsafe_b64encode(raw.encode()).decode()}}).execute()
        edit(BODY.strip().replace("within a day", "within a week"))           # changed in Gmail after the yes
        rc, out, _ = self.run_json("send", "--as", "inbox", "--draft", p["draft"], "--issue", "1",
                                   "--approval-issue", APPROVAL)
        self.assertEqual((out["sent"], out["gate"]), (False, "recipient"), out)
        self.assertIn("the draft changed after it was approved; ask for a new yes", out["reason"])
        self.assertEqual(self.service.sent, [])
        edit(BODY.strip(), crlf=True)                                         # back as approved, with Gmail's CRLF
        rc, out, _ = self.run_json("send", "--as", "inbox", "--draft", p["draft"], "--issue", "1",
                                   "--approval-issue", APPROVAL)
        self.assertEqual((rc, out["sent"]), (0, True), out)

    def test_a_reply_with_a_text_approval_sends_only_the_approved_words(self):
        # Sending is off in Tico, so the reply to the thread's sender needs the per-message yes.
        pl.SERVER_GET = lambda slug: {"bot": slug, "set": True, "outbound_send": False, "forward_to": []}
        args = ("reply", "--as", "inbox", "--thread", THREAD, "--to", AVA, "--issue", "1", "--approval-issue", APPROVAL,
                "--json")
        hub(send_approval([AVA], []))
        rc, out, _ = self.run_json(*args, "--body-file", self.body_file(BODY.replace("a day", "an hour")))
        self.assertEqual((out["sent"], out["gate"]), (False, "outbound_send"), out)   # the yes no longer lifts it
        self.assertIn("the draft changed after it was approved", out["reason"])
        self.assertEqual(self.service.sent, [])
        rc, out, _ = self.run_json(*args, "--body-file", self.body_file(BODY))
        self.assertEqual((rc, out["sent"]), (0, True), out)

    def test_an_attached_reply_to_the_sender_waits_for_an_approval(self):
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
        (self.root / "emp-inbox" / "answer.pdf").write_bytes(b"%PDF-1.4 the answer")
        rc, p, err = self.run_json("draft", "--as", "inbox", "--reply-to", THREAD, "--body-file", self.body_file(BODY),
                                   "--attach", "answer.pdf", "--issue", "1", "--json")
        self.assertEqual((rc, p.get("send_needs_approval")), (0, True), err)
        request = p["approval_request"]
        self.assertEqual((request["kind"], request["payload"]["draft"], request["payload"]["to"]),
                         ("send", p["draft"], [AVA]))
        self.assertEqual(request["payload"]["attachments"], [file_meta("answer.pdf", b"%PDF-1.4 the answer")])
        self.assertIn("hub approval request --kind send --payload", request["command"])
        rc, out, _ = self.run_json("send", "--as", "inbox", "--draft", p["draft"], "--issue", "1")
        self.assertEqual((out["sent"], out["gate"]), (False, "attachments"), out)
        hub(owner_order())                                                    # last week's "send it" is not enough
        rc, out, _ = self.run_json("send", "--as", "inbox", "--draft", p["draft"], "--issue", "1",
                                   "--approval-issue", ORDER)
        self.assertEqual(out["sent"], False, out)
        self.assertEqual(self.service.sent, [])
        hub(dict(send_approval([AVA], []), payload=request["payload"]))       # the request, approved
        rc, out, _ = self.run_json("send", "--as", "inbox", "--draft", p["draft"], "--issue", "1",
                                   "--approval-issue", APPROVAL)
        self.assertEqual((rc, out["sent"]), (0, True), out)

    def test_the_same_text_with_another_file_is_a_new_draft_the_approval_does_not_cover(self):
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
        f = self.root / "emp-inbox" / "answer.pdf"
        f.write_bytes(b"%PDF-1.4 the answer")
        args = ("draft", "--as", "inbox", "--reply-to", THREAD, "--to", AVA, "--body-file", self.body_file(BODY),
                "--attach", "answer.pdf", "--issue", "1", "--json")
        rc, p, err = self.run_json(*args)
        self.assertEqual(rc, 0, err)
        first_raw = self.service.drafts_by_id[p["draft"]]["message"]["raw"]
        hub(dict(send_approval([AVA], []), payload=p["approval_request"]["payload"]))
        f.write_bytes(b"%PDF-1.4 something else entirely")                     # same name, same text
        rc, p2, err = self.run_json(*args)
        self.assertEqual(rc, 0, err)
        self.assertNotEqual(p2["draft"], p["draft"])                           # a new draft, not the approved one
        self.assertNotEqual(p2["key"], p["key"])
        self.assertEqual(self.service.drafts_by_id[p["draft"]]["message"]["raw"], first_raw)
        rc, out, _ = self.run_json("send", "--as", "inbox", "--draft", p2["draft"], "--issue", "1",
                                   "--approval-issue", APPROVAL)
        self.assertEqual((out["sent"], out["gate"]), (False, "attachments"), out)
        self.assertEqual(self.service.sent, [])
        rc, out, _ = self.run_json("send", "--as", "inbox", "--draft", p["draft"], "--issue", "1",
                                   "--approval-issue", APPROVAL)               # the approved draft is intact
        self.assertEqual((rc, out["sent"]), (0, True), out)

    def test_attached_files_go_on_the_task_and_the_file_id_is_not_part_of_the_approval(self):
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
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
        self.assertTrue(request["command"].endswith(f" --task {task}"))
        # The approval matches on name, size and sha256: another file_id, or none, approves the same files.
        approved = dict(request["payload"], attachments=[dict(file_meta("answer.pdf", data), file_id="f-other")])
        hub(dict(send_approval([AVA], []), payload=approved))
        rc, out, _ = self.run_json("send", "--as", "inbox", "--draft", p["draft"], "--issue", task,
                                   "--approval-issue", APPROVAL)
        self.assertEqual((rc, out["sent"]), (0, True), out)
        # No credential or a failed upload: the draft and the request still stand, without file ids.
        cli.TASK_ATTACH = lambda *call: (_ for _ in ()).throw(RuntimeError("offline"))
        (self.root / "emp-inbox" / "answer.pdf").write_bytes(data + b" v2")
        rc, p, err = self.run_json("draft", "--as", "inbox", "--reply-to", THREAD, "--body-file", self.body_file(BODY),
                                   "--attach", "answer.pdf", "--issue", task, "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(p["approval_request"]["payload"]["attachments"], [file_meta("answer.pdf", data + b" v2")])

    def test_a_bot_environment_never_reaches_a_server_from_the_tests(self):
        # The variables a bot's run carries, pointing at a port nothing listens on: the harness's stub takes the upload.
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
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

    def test_the_draft_text_shows_the_approval_request_with_the_files(self):
        self.write_policy(harness.POLICY.replace("allow_attachments: false", "allow_attachments: true"))
        (self.root / "emp-inbox" / "answer.pdf").write_bytes(b"%PDF-1.4 the answer")
        rc, out, err = self.run_cli("draft", "--as", "inbox", "--reply-to", THREAD, "--body-file", self.body_file(BODY),
                                    "--attach", "answer.pdf", "--issue", "1")
        self.assertEqual(rc, 0, err)
        digest = hashlib.sha256(b"%PDF-1.4 the answer").hexdigest()
        self.assertIn(f"Attach: answer.pdf (19 bytes, sha256 {digest[:12]})", out)
        self.assertIn(f"Send from ana@acme.example to {AVA}", out)
        self.assertIn("hub approval request --kind send --payload", out)
        self.assertIn(digest, out)                                              # the full hash, in the payload

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
