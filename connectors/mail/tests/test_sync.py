"""Local mail persist: messages / sync_state helpers, Gmail history, and `mail sync`."""

import contextlib, io, json, os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake                    # noqa: E402,F401

from connectors.mail import Failure, db, gmail as gm                 # noqa: E402
from connectors.mail import access, audit as audit_log              # noqa: E402
from connectors.mail import __main__ as cli                          # noqa: E402


HEADERS = {"From": "Acme Growth <hello@acme.io>", "To": "ana@acme.example",
           "Subject": "Grow your rentals", "Date": "Tue, 02 Sep 2026 09:00:00 -0700",
           "List-Unsubscribe": "<https://acme.io/u/1>"}
PLAIN = {"From": "Real Person <person@customer.example>", "To": "ana@acme.example",
         "Subject": "Pricing question", "Date": "Tue, 02 Sep 2026 09:30:00 -0700"}

OLD, MID, NEW = 1788278400000, 1788364800000, 1788368400000


def corpus():
    return [
        fake.message("m-mkt", "t-mkt", HEADERS, body="Read the guide.", epoch_ms=MID),
        fake.message("m-plain", "t-plain", PLAIN, body="How does pricing work?", epoch_ms=NEW),
    ]


def norm(mid="m-plain", **extra):
    base = {"id": mid, "thread_id": "t-" + mid, "epoch": 100, "date": "2026-09-02T09:30:00-07:00",
            "from": "person@customer.example", "from_header": "Real Person <person@customer.example>",
            "to": ["ana@acme.example"], "cc": [], "subject": "Pricing question",
            "snippet": "How does", "labels": ["INBOX", "UNREAD"], "body": "How does pricing work?",
            "body_truncated": False, "attachments": [], "list_id": "", "is_internal": False,
            "unsubscribe": False}
    base.update(extra)
    return base


class MessageStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "mail.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_upsert_inserts_then_is_idempotent(self):
        self.assertTrue(db.upsert_message(self.conn, "ana@acme.example", norm()))
        self.assertFalse(db.upsert_message(self.conn, "ana@acme.example", norm()))
        row = db.get_message(self.conn, "ana@acme.example", "m-plain")
        self.assertEqual(row["from_addr"], "person@customer.example")
        self.assertEqual(row["to"], ["ana@acme.example"])
        self.assertFalse(row["has_unsubscribe"])
        self.assertIsNone(row["pushed_at"])
        self.assertEqual(db.message_count(self.conn, "ana@acme.example"), 1)


class KnownSenders(unittest.TestCase):
    """known_sender: the owner had written to the sender before the message's own date, from the copy this
    computer holds. The computer sets it on each batch it pushes, so the hub reads it without a Gmail call."""

    OWNER = "ana@acme.example"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "mail.db")

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, mid, sender, to, epoch, cc=None):
        msg = norm(mid, epoch=epoch, **{"from": sender, "to": to, "cc": cc or []})
        db.upsert_message(self.conn, self.OWNER, msg)

    def known(self, *mids):
        rows = [r for r in db.unpushed(self.conn, mailbox=self.OWNER) if r["id"] in mids]
        return {r["id"]: r["known_sender"] for r in db.annotate_known_senders(self.conn, rows)}

    def test_a_sender_the_owner_wrote_to_earlier_is_known(self):
        self.put("sent", self.OWNER, ["pat@customer.example"], 100)
        self.put("later", "pat@customer.example", [self.OWNER], 200)
        self.assertEqual(self.known("later"), {"later": True})

    def test_a_message_older_than_the_first_send_is_not_known(self):
        self.put("sent", self.OWNER, ["pat@customer.example"], 100)
        self.put("early", "pat@customer.example", [self.OWNER], 50)
        self.assertEqual(self.known("early"), {"early": False})

    def test_a_copy_to_the_owner_counts_and_so_does_a_cc(self):
        self.put("sent", self.OWNER, [], 100, cc=["pat@customer.example"])
        self.put("later", "pat@customer.example", [self.OWNER], 200)
        self.assertEqual(self.known("later"), {"later": True})

    def test_a_new_sender_is_not_known_and_deleted_sends_do_not_count(self):
        self.put("sent", self.OWNER, ["pat@customer.example"], 100)
        db.mark_deleted(self.conn, self.OWNER, "sent")
        self.put("later", "pat@customer.example", [self.OWNER], 200)
        self.put("new", "kim@other.example", [self.OWNER], 300)
        self.assertEqual(self.known("later", "new"), {"later": False, "new": False})

    def test_mail_from_someone_else_does_not_count_as_a_send(self):
        self.put("sent", "ben@acme.example", ["pat@customer.example"], 100)
        self.put("later", "pat@customer.example", [self.OWNER], 200)
        self.assertEqual(self.known("later"), {"later": False})


if __name__ == "__main__":
    unittest.main()
