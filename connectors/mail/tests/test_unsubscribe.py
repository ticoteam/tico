"""Unsubscribe eligibility, network boundary and retry safety; no real mail or network."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from connectors.mail import unsubscribe as u, access, actions, db, gmail as gm, Refused
from connectors.mail.tests import fake

BOX = "ana@acme.example"
UNSUB_URL = "https://news.example.org/unsub?token=abc"

RAW = b'''From: Newsletter <news@example.org>\r
List-Unsubscribe: <https://example.org/unsub?token=private>\r
List-Unsubscribe-Post: List-Unsubscribe=One-Click\r
DKIM-Signature: v=1; h=from:list-unsubscribe:list-unsubscribe-post; b=fake\r
\r
Newsletter\r
'''


class Unsubscribe(unittest.TestCase):
    def test_reject_private_and_mixed_dns(self):
        for ips in [['127.0.0.1'], ['169.254.169.254'], ['::1'], ['8.8.8.8', '10.0.0.1']]:
            rows = [(2, 1, 6, '', (ip, 443)) for ip in ips]
            with patch.object(u.socket, 'getaddrinfo', return_value=rows):
                with self.assertRaises(u.Unsupported):
                    u.public_address('example.org')

class RulesPipeline(unittest.TestCase):
    def test_protected_mail_never_unsubscribes(self):
        from connectors.mail import rules
        rs = [{'id': 'protect', 'when': {}, 'do': {'never_archive': True}},
              {'id': 'subscription', 'when': {}, 'do': {'unsubscribe': True, 'archive': True}}]
        plan = rules.apply(rs, {})
        self.assertFalse(plan['unsubscribe'])
        self.assertFalse(plan['archive'])


def news(sender="news@example.org", labels=("INBOX", "UNREAD"), to=BOX):
    headers = {"From": f"Sender <{sender}>", "To": to, "Subject": "Weekly digest", "Message-ID": "<w-1@example.org>",
               "Date": "Tue, 27 Oct 2026 09:00:00 -0700"}
    return fake.message("m1", "t1", headers, body="Weekly news.", labels=labels)


class Ctx:
    """The parts of the mail CLI's context that perform() uses, over a fake Gmail and a real mail.db. `policy` is the
    install's mail policy (Ctx.policy in __main__.py), here with only the internal domains it reads."""

    def __init__(self, gmail, conn, audits, mailbox=BOX, internal=("acme.example",)):
        self.gmail, self.conn, self.audits = gmail, conn, audits
        self.mailbox, self.slug = mailbox, "ana"
        self.policy = {"internal_domains": list(internal)}

    def normalize(self, raw):
        return gm.normalize(raw, self.gmail.labels())

    def audit(self, action, target="", detail=None):
        self.audits.append((action, target, detail))


class UnsubscribeAction(unittest.TestCase):
    """perform() for the unsubscribe action: the guard, one POST to the message's own link, and what a retry or a
    refused answer says. The POST and the link are stand-ins; nothing touches the network."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.conn = db.connect(os.path.join(tmp.name, "mail.db"))
        self.addCleanup(self.conn.close)
        self.audits, self.posts, self.code, self.roster = [], [], 202, []
        self.real_one_click = actions._one_click_url
        for patcher in (patch.object(u, "post", side_effect=self._post),
                        patch.object(access, "roster_mailboxes", side_effect=lambda roster=None: list(self.roster)),
                        patch.object(actions, "_one_click_url", return_value=UNSUB_URL)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _post(self, url):
        self.posts.append(url)
        if isinstance(self.code, Exception):
            raise self.code
        return self.code

    def perform(self, *messages, action_id="ma_1", mailbox=BOX, internal=("acme.example",)):
        gmail = gm.Gmail(fake.FakeGmailService(list(messages) or [news()]), mailbox, sleep=lambda s: None)
        return actions.perform(Ctx(gmail, self.conn, self.audits, mailbox=mailbox, internal=internal),
                               {"id": action_id, "mailbox": mailbox, "msg_id": "m1", "action": "unsubscribe", "args": {}})

    def synced_send(self, to=(), cc=(), labels=("SENT",), deleted=None, sender=BOX):
        """One synced message of the mailbox in another thread, as the sync writes it to mail.db's messages table."""
        self.conn.execute("INSERT INTO messages (mailbox, msg_id, thread_id, from_addr, to_json, cc_json, labels_json, "
                          "deleted_at) VALUES (?,?,?,?,?,?,?,?)",
                          (BOX, "sent-1", "t-other", sender, json.dumps(list(to)), json.dumps(list(cc)),
                           json.dumps(list(labels)), deleted))

    def test_an_accepted_request_is_ok_and_posted_once(self):
        self.assertEqual(self.perform(), {"ok": True, "detail": "unsubscribed"})
        self.assertEqual(self.posts, [UNSUB_URL])
        self.assertEqual([a[0] for a in self.audits], ["unsubscribe"])

    def test_a_retry_answers_already_attempted_and_sends_nothing_again(self):
        self.perform()
        self.assertEqual(self.perform(), {"ok": True, "detail": "already unsubscribed"})
        self.assertEqual(len(self.posts), 1)

    def test_an_unknown_outcome_is_not_sent_again_on_retry(self):
        self.code = OSError("timeout")
        refused = {"ok": False, "retry": False, "detail": "not confirmed by the sender: unknown"}
        self.assertEqual(self.perform(), refused)
        self.assertEqual(self.perform(), refused)
        self.assertEqual(len(self.posts), 1)

    def test_a_sender_that_refuses_the_one_click_is_manual_required_and_not_retried(self):
        self.code = 400
        self.assertEqual(self.perform(), {"ok": False, "retry": False,
                                          "detail": "not confirmed by the sender: manual_required"})
        self.assertEqual(len(self.posts), 1)

    def test_a_message_without_a_one_click_link_is_refused_before_any_post(self):
        reason = "Sender does not advertise standard one-click unsubscribe."
        with patch.object(actions, "_one_click_url", side_effect=u.Unsupported(reason)):
            self.assertEqual(self.perform(), {"ok": False, "retry": False, "detail": reason})
        self.assertEqual(self.posts, [])

    def test_without_a_raw_copy_the_unsubscribe_needs_a_manual_step(self):
        with patch.object(actions, "_one_click_url", self.real_one_click):
            self.assertEqual(self.perform(), {"ok": False, "retry": False,
                                              "detail": "Raw message unavailable; manual unsubscribe needed."})
        self.assertEqual(self.posts, [])

    def test_starred_mail_is_kept_without_a_post(self):
        starred = news(labels=("INBOX", "UNREAD", "STARRED"))
        self.assertEqual(self.perform(starred), {"ok": False, "retry": False,
                                                 "detail": "not unsubscribing: the message is starred"})
        self.assertEqual(self.posts, [])

    def test_internal_mail_is_kept_without_a_post(self):
        self.assertEqual(self.perform(news(sender="ops@acme.example")),
                         {"ok": False, "retry": False, "detail": "not unsubscribing: the mail is internal"})
        self.assertEqual(self.posts, [])

    def test_a_sender_on_the_people_roster_is_kept_without_a_post(self):
        self.roster = ["News@Example.org"]
        self.assertEqual(self.perform(), {"ok": False, "retry": False,
                                          "detail": "not unsubscribing: the sender is on the people roster"})
        self.assertEqual(self.posts, [])

    def test_a_sender_the_mailbox_already_writes_to_is_kept_without_a_post(self):
        db.claim_send(self.conn, "send-1", "ana", BOX, "", ["news@example.org"], "Weekly digest")
        self.assertEqual(self.perform(), {"ok": False, "retry": False,
                                          "detail": "not unsubscribing: the mailbox already writes to the sender"})
        self.assertEqual(self.posts, [])

    def test_a_thread_the_mailbox_answered_counts_as_known_without_a_post(self):
        reply = fake.message("m2", "t1", {"From": f"Ana <{BOX}>", "To": "news@example.org",
                                          "Subject": "Re: Weekly digest", "Message-ID": "<r-1@acme.example>",
                                          "Date": "Tue, 27 Oct 2026 10:00:00 -0700"}, body="Thanks.", labels=("SENT",))
        self.assertEqual(self.perform(news(), reply), {"ok": False, "retry": False,
                                                       "detail": "not unsubscribing: the mailbox already writes to the sender"})
        self.assertEqual(self.posts, [])

    def test_internal_mail_from_a_domain_the_policy_names_is_kept_without_a_post(self):
        self.assertEqual(self.perform(news(sender="ops@team.example", to="ana@team.example"),
                                      mailbox="ana@team.example", internal=("team.example",)),
                         {"ok": False, "retry": False, "detail": "not unsubscribing: the mail is internal"})
        self.assertEqual(self.posts, [])

    def test_a_sender_the_mailbox_wrote_to_in_another_thread_is_kept_without_a_post(self):
        self.synced_send(to=["news@example.org"])
        self.assertEqual(self.perform(), {"ok": False, "retry": False,
                                          "detail": "not unsubscribing: the mailbox already writes to the sender"})
        self.assertEqual(self.posts, [])

    def test_a_sender_the_mailbox_copied_on_its_own_mail_is_kept_without_a_post(self):
        self.synced_send(to=["someone@example.org"], cc=["news@example.org"])
        self.assertEqual(self.perform(), {"ok": False, "retry": False,
                                          "detail": "not unsubscribing: the mailbox already writes to the sender"})
        self.assertEqual(self.posts, [])

    def test_a_second_recipient_of_a_send_counts_as_known_without_a_post(self):
        db.claim_send(self.conn, "send-1", "ana", BOX, "", ["first@x.example", "news@example.org"], "Weekly digest")
        self.assertEqual(self.perform(), {"ok": False, "retry": False,
                                          "detail": "not unsubscribing: the mailbox already writes to the sender"})
        self.assertEqual(self.posts, [])

    def test_a_draft_to_the_sender_does_not_count_as_known(self):
        self.synced_send(to=["news@example.org"], labels=("DRAFT",))
        self.assertEqual(self.perform(), {"ok": True, "detail": "unsubscribed"})
        self.assertEqual(self.posts, [UNSUB_URL])

    def test_inbound_mail_that_spoofs_the_owners_from_does_not_count_as_known(self):
        self.synced_send(to=["news@example.org"], labels=("INBOX", "UNREAD"))
        self.assertEqual(self.perform(), {"ok": True, "detail": "unsubscribed"})
        self.assertEqual(self.posts, [UNSUB_URL])

    def test_a_deleted_sent_message_does_not_count_as_known(self):
        self.synced_send(to=["news@example.org"], deleted="2026-10-30T10:00:00Z")
        self.assertEqual(self.perform(), {"ok": True, "detail": "unsubscribed"})
        self.assertEqual(self.posts, [UNSUB_URL])

    def test_a_longer_address_that_contains_the_sender_does_not_count_as_known(self):
        self.synced_send(to=["xnews@example.org"])
        self.assertEqual(self.perform(), {"ok": True, "detail": "unsubscribed"})
        self.assertEqual(self.posts, [UNSUB_URL])
