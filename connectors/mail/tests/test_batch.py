"""Reading and changing many messages in few Gmail calls (spec I26): reads go in Gmail batch calls of at most
gmail.READ_GROUP, one call at a time, and the same label change on many messages is one messages.batchModify.

The fake counts HTTPS calls (`round_trips`, a batch call is one) and its batch call answers the parts last first, as
Gmail may. Every message here is synthetic. `GoogleBatchCall` runs the real google client library against a local
server, for the no-redirect boundary; it is skipped where that library is not installed (scripts/mail.sh builds it).
"""

import json, os, re, sys, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake  # noqa: E402
from harness import MANIFESTS, Stage2  # noqa: E402

from connectors.mail import Failure, Refused, auth, gmail as gm  # noqa: E402
from connectors.mail import __main__ as cli  # noqa: E402

BOX, LEGAL = "ana@acme.example", "legal@acme.example"
BOT = """
name: inbox
access:
  - service: gmail
    identity: "ana@acme.example"
    can: [read, draft]
  - service: gmail
    identity: "legal@acme.example"
    can: [read, draft]
"""
START = 1788364800000  # 2026-09-02 09:00 America/Los_Angeles


def mail(n, prefix="m"):
    """n synthetic inbox messages, newest first as Gmail lists them; every other one has an unsubscribe link."""
    out = []
    for i in range(n):
        headers = {
            "From": f"Sender {i} <sender{i}@customer.example>",
            "To": BOX,
            "Subject": f"Message {i}",
            "Date": "Wed, 02 Sep 2026 09:00:00 -0700",
        }
        if i % 2:
            headers["List-Unsubscribe"] = f"<https://news.example.org/u/{i}>"
        out.append(
            fake.message(f"{prefix}-{i:03d}", f"t-{i:03d}", headers, body=f"Body {i}.", epoch_ms=START - i * 60000)
        )
    return out


class Batches(Stage2):
    manifests = dict(MANIFESTS, inbox=BOT)
    size = 50

    def corpus(self):
        return mail(self.size)

    def ids(self):
        return [m["id"] for m in self.corpus()]

    def one_by_one(self):
        """The same mailbox on a Gmail without batch calls: the read one message at a time, as before."""
        self.service = fake.FakeGmailService(self.corpus(), batch=False)


class Reads(Batches):
    def test_an_inbox_of_50_is_list_labels_and_one_batch_call_with_the_same_messages_as_one_by_one(self):
        """AC-1, AC-4: at most 4 calls' worth of waiting, the same messages in the same order, every field equal."""
        rc, batched, err = self.run_json("inbox", "--as", "inbox", "--mailbox", BOX, "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual([m["id"] for m in batched["messages"]], self.ids())
        self.assertEqual(self.service.batches, [50])
        self.assertLessEqual(self.service.round_trips, 4)
        self.assertEqual(sorted(self.service.reads), sorted(self.ids()))  # each read once

        self.one_by_one()
        self.dbfile.unlink()
        rc, single, err = self.run_json("inbox", "--as", "inbox", "--mailbox", BOX, "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.service.round_trips, 52)  # list, labels and 50 reads: the baseline
        self.assertEqual(batched, single)

    def test_a_search_and_a_rules_run_read_in_batch_calls_too(self):
        rc, found, err = self.run_json(
            "search", "--as", "inbox", "--mailbox", BOX, "in:inbox", "--limit", "50", "--json"
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([m["id"] for m in found["messages"]], self.ids())
        rc, ran, err = self.run_json(
            "rules", "run", "--as", "inbox", "--mailbox", BOX, "--since", "2026-08-31T00:00:00Z", "--dry-run", "--json"
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual(ran["scanned"], 50)
        self.assertEqual(self.service.batches, [50, 50])

    def test_one_read_that_answers_429_twice_is_retried_alone_and_the_others_are_read_once(self):
        """AC-5, with today's backoff (1 s, then 2 s) and attempt limit."""
        ids, sleeps = self.ids(), []
        self.service.fail_reads = {ids[7]: [429, 429]}
        got = list(gm.Gmail(self.service, BOX, sleep=sleeps.append).read_many(ids))
        self.assertEqual([i for i, _ in got], ids)
        self.assertEqual([raw["id"] for _, raw in got], ids)
        self.assertEqual(self.service.batches, [50, 1, 1])
        self.assertEqual(sleeps, [1, 2])
        self.assertEqual(self.service.reads.count(ids[7]), 3)
        self.assertTrue(all(self.service.reads.count(i) == 1 for i in ids if i != ids[7]))

    def test_a_read_still_rate_limited_after_the_last_attempt_fails_with_today_s_error(self):
        ids, sleeps = self.ids()[:3], []
        self.service.fail_reads = {ids[1]: [429] * gm.MAX_ATTEMPTS}
        got = dict(gm.Gmail(self.service, BOX, sleep=sleeps.append).read_many(ids))
        self.assertIsInstance(got[ids[1]], Failure)
        self.assertEqual(got[ids[1]].status, 429)
        self.assertTrue(str(got[ids[1]]).startswith(f"Gmail messages.get failed for {BOX}"))
        self.assertEqual(sleeps, [1, 2, 4])
        self.assertEqual(self.service.batches, [3, 1, 1, 1])

    def test_a_batch_call_that_fails_as_a_whole_is_retried_and_then_fails_every_read_in_it(self):
        ids, sleeps = self.ids()[:3], []
        self.service.batch_errors = [fake.Boom(503)]
        got = list(gm.Gmail(self.service, BOX, sleep=sleeps.append).read_many(ids))
        self.assertEqual([raw["id"] for _, raw in got], ids)
        self.assertEqual((self.service.batches, sleeps), ([3, 3], [1]))
        self.service.batch_errors = [fake.Boom(400, "badRequest")]
        got = dict(gm.Gmail(self.service, BOX, sleep=sleeps.append).read_many(ids))
        self.assertTrue(all(isinstance(v, Failure) and v.status == 400 for v in got.values()))

    def test_a_message_deleted_before_it_is_read_fails_a_read_as_today_and_is_skipped_by_sync_and_the_backtest(self):
        """AC-6: the inbox, untriaged and search fail with the error one read at a time gave; sync and the backtest
        skip it and count it."""
        gone = self.ids()[5]

        def failure(service):
            service.fail_reads = {gone: [404]}
            ctx = SimpleNamespace(gmail=gm.Gmail(service, BOX, sleep=lambda s: None), dry=True)
            ctx.normalize = lambda raw: gm.normalize(raw, ctx.gmail.labels())
            with self.assertRaises(Failure) as caught:
                cli.fetch(ctx, self.ids())
            return caught.exception

        batched = failure(self.service)
        today = failure(fake.FakeGmailService(self.corpus(), batch=False))
        self.assertEqual((str(batched), batched.hint, batched.status), (str(today), today.hint, today.status))
        for argv in (("inbox",), ("inbox", "--untriaged"), ("search", "in:inbox", "--limit", "50")):
            self.service.fail_reads = {gone: [404]}
            rc, out, _ = self.run_cli(*argv[:1], "--as", "inbox", "--mailbox", BOX, *argv[1:], "--json")
            self.assertNotEqual(rc, 0, argv)
            self.assertIn("No such message", out)

        self.service.fail_reads = {gone: [404]}
        rc, synced, err = self.run_json("sync", "--as", "inbox", "--mailbox", BOX, "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual((synced["fetched"], synced["skipped"]), (49, 1))
        self.service.fail_reads = {gone: [404]}
        rc, replay, err = self.run_json(
            "rules", "backtest", "--as", "inbox", "--mailbox", BOX, "--since", "2026-08-31T00:00:00Z", "--json"
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([s["id"] for s in replay["skipped"]], [gone])

    def test_the_label_list_is_read_once_per_mailbox_in_one_command(self):
        """AC-7, also over two mailboxes (--all-mailboxes): once each, and again only after a label is created."""
        rc, payload, err = self.run_json("inbox", "--as", "inbox", "--all-mailboxes", "--untriaged", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual([b["mailbox"] for b in payload["mailboxes"]], [BOX, LEGAL])
        self.assertEqual(self.service.calls.count(("labels.list",)), 2)
        self.service.calls.clear()
        rc, _, err = self.run_json("label", "--as", "inbox", "--mailbox", BOX, "--json", "add", *self.ids(), "hub/new")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.service.calls.count(("labels.list",)), 2)  # read again after hub/new was created
        self.service.calls.clear()
        rc, _, err = self.run_json("search", "--as", "inbox", "--mailbox", BOX, "in:inbox", "--limit", "50", "--json")
        self.assertEqual(self.service.calls.count(("labels.list",)), 1)


class BigReads(Batches):
    size = 200

    def test_200_untriaged_messages_take_at_most_10_calls_and_no_batch_call_is_over_the_group_size(self):
        """AC-2."""
        self.assertTrue(25 <= gm.READ_GROUP <= 50)
        rc, payload, err = self.run_json("inbox", "--as", "inbox", "--mailbox", BOX, "--untriaged", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual([m["id"] for m in payload["messages"]], self.ids())
        self.assertLessEqual(self.service.round_trips, 10)
        self.assertTrue(self.service.batches and max(self.service.batches) <= gm.READ_GROUP)
        self.assertEqual(sum(self.service.batches), 200)

    def test_sync_reads_every_message_in_batch_calls(self):
        rc, synced, err = self.run_json("sync", "--as", "inbox", "--mailbox", BOX, "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual((synced["fetched"], synced["skipped"]), (200, 0))
        self.assertEqual(self.service.batches, [gm.READ_GROUP] * (200 // gm.READ_GROUP))


class Changes(Batches):
    def label(self, *ids, name="hub/billing"):
        return self.run_json("label", "--as", "inbox", "--mailbox", BOX, "--json", "add", *ids, name)

    def test_50_messages_that_get_the_same_label_and_archive_are_one_change_call_each_with_50_audit_lines(self):
        """AC-3."""
        ids = self.ids()
        rc, labelled, err = self.label(*ids)
        self.assertEqual(rc, 0, err)
        rc, archived, err = self.run_json("archive", "--as", "inbox", "--mailbox", BOX, *ids, "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.service.batch_modifies, [ids, ids])
        self.assertEqual((labelled["messages"], archived["messages"]), (ids, ids))
        self.assertTrue(all(self.labels_on(i) == {"hub/billing", "UNREAD"} for i in ids))
        lines = self.audit_lines()
        self.assertEqual([l["target"] for l in lines if l["action"] == "label-add"], ids)
        self.assertEqual([l["target"] for l in lines if l["action"] == "archive"], ids)

    def test_one_message_is_one_modify_as_before(self):
        rc, _, err = self.label(self.ids()[0])
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.service.batch_modifies, [])
        self.assertEqual([m["id"] for m in self.service.modified], [self.ids()[0]])

    def test_a_group_change_gmail_refuses_is_redone_one_by_one_up_to_the_message_that_fails(self):
        """AC-10: what one-by-one changes would have changed is changed and audited; the command fails as today on
        the first message that can't be changed."""
        ids = self.ids()[:5]
        del self.service.store[ids[3]]  # deleted since the bot listed it
        rc, out, _ = self.label(*ids)
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.service.batch_modifies, [ids])
        self.assertEqual([m["id"] for m in self.service.modified], ids[:3])
        self.assertEqual([l["target"] for l in self.audit_lines() if l["action"] == "label-add"], ids[:3])
        self.assertTrue(all("hub/billing" in self.labels_on(i) for i in ids[:3]))
        self.assertNotIn("hub/billing", self.labels_on(ids[4]))

    def test_organize_off_or_a_read_only_grant_refuses_many_messages_before_any_gmail_call(self):
        """AC-8."""
        ids = self.ids()
        archive = lambda: self.run_json("archive", "--as", "inbox", "--mailbox", BOX, *ids, "--json")
        setting = cli.pl.SERVER_GET
        cli.pl.SERVER_GET = lambda slug: dict(setting(slug), company_organize=False)
        rc, out, _ = archive()
        self.assertEqual(rc, 2, out)
        self.assertIn("Organize mail", out["error"])
        cli.pl.SERVER_GET = setting
        self.service._tico_granted = ["https://www.googleapis.com/auth/gmail.readonly"]
        rc, out, _ = archive()
        self.assertEqual(rc, 2, out)
        self.assertIn("read-only", out["error"])
        self.assertEqual((self.service.batch_modifies, self.service.modified), ([], []))
        self.assertTrue(all("INBOX" in self.service.store[i]["labelIds"] for i in ids))
        with self.assertRaises(Refused):
            gm.Gmail(self.service, BOX).batch_modify(ids, [], ["INBOX"])

    def test_a_rules_run_is_one_change_call_per_distinct_change_and_unsubscribes_before_it_archives(self):
        """AC-9 and Measure 3: each unsubscribe happens before its message is archived, each rule hit is recorded,
        and each changed message's local copy shows its new labels."""
        rules = self.root / "rules.yaml"
        rules.write_text(
            "mailboxes:\n  ana@acme.example:\n"
            "    - id: newsletters\n      when: {has_unsubscribe_link: true}\n"
            "      do: {label: hub/newsletter, unsubscribe: true, archive: true}\n"
            "    - id: everyone\n      when: {subject_matches: [Message]}\n      do: {label: hub/seen}\n"
        )
        timeline = []
        real_batch = fake._Messages.batchModify

        def unsubscribe(ctx, mid):
            timeline.append(("unsubscribe", mid, "INBOX" in self.service.store[mid]["labelIds"]))
            return {"accepted": True, "status": "accepted"}

        def batch_modify(messages, userId="me", body=None):
            timeline.append(("change", tuple(body["ids"])))
            return real_batch(messages, userId=userId, body=body)

        with (
            mock.patch.object(cli, "RULES_FILE", rules),
            mock.patch.object(cli, "unsubscribe_message", side_effect=unsubscribe),
            mock.patch.object(fake._Messages, "batchModify", batch_modify),
        ):
            rc, ran, err = self.run_json(
                "rules", "run", "--as", "inbox", "--mailbox", BOX, "--since", "2026-08-31T00:00:00Z", "--json"
            )
        self.assertEqual(rc, 0, err)
        ids = self.ids()
        news, plain = [i for n, i in enumerate(ids) if n % 2], [i for n, i in enumerate(ids) if not n % 2]
        self.assertEqual(ran["counts"], {"newsletters": 25, "everyone": 50})
        self.assertEqual(self.service.batch_modifies, [plain, news])
        self.assertEqual([t[1] for t in timeline if t[0] == "unsubscribe"], news)
        self.assertTrue(all(t[2] for t in timeline if t[0] == "unsubscribe"))  # still in the inbox
        self.assertLess(
            max(n for n, t in enumerate(timeline) if t[0] == "unsubscribe"),
            min(n for n, t in enumerate(timeline) if t[0] == "change" and news[0] in t[1]),
        )
        self.assertTrue(all("INBOX" not in self.service.store[i]["labelIds"] for i in news))
        conn = self.conn()
        hits = conn.execute("SELECT msg_id, rule_id FROM rule_hits").fetchall()
        self.assertEqual(len(hits), 75)
        for mid in news:
            stored = json.loads(conn.execute("SELECT labels_json FROM messages WHERE msg_id=?", (mid,)).fetchone()[0])
            self.assertNotIn("INBOX", stored)
            self.assertTrue({"hub/newsletter", "hub/seen"} <= set(stored))
        rule_lines = [l for l in self.audit_lines() if l["action"] == "rule"]
        self.assertEqual(len(rule_lines), 75)

    def test_an_unsubscribe_that_fails_stops_the_run_after_the_messages_before_it_are_changed(self):
        """As one message at a time: the messages before the failing unsubscribe are labelled, archived and recorded,
        the failing one and those after it are not."""
        rules = self.root / "rules.yaml"
        rules.write_text(
            "mailboxes:\n  ana@acme.example:\n"
            "    - id: newsletters\n      when: {has_unsubscribe_link: true}\n"
            "      do: {label: hub/newsletter, unsubscribe: true, archive: true}\n"
        )
        news = [i for n, i in enumerate(self.ids()) if n % 2]
        broken = news[3]

        def unsubscribe(ctx, mid):
            if mid == broken:
                raise Failure(f"Gmail messages.get failed for {BOX}: boom", "", status=500)
            return {"accepted": True, "status": "accepted"}

        with (
            mock.patch.object(cli, "RULES_FILE", rules),
            mock.patch.object(cli, "unsubscribe_message", side_effect=unsubscribe),
        ):
            rc, out, _ = self.run_cli(
                "rules", "run", "--as", "inbox", "--mailbox", BOX, "--since", "2026-08-31T00:00:00Z", "--json"
            )
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.service.batch_modifies, [news[:3]])
        self.assertEqual([l["target"] for l in self.audit_lines() if l["action"] == "rule"], news[:3])
        self.assertTrue(all("INBOX" not in self.service.store[i]["labelIds"] for i in news[:3]))
        self.assertTrue(all("INBOX" in self.service.store[i]["labelIds"] for i in news[3:]))
        hits = self.conn().execute("SELECT msg_id FROM rule_hits ORDER BY msg_id").fetchall()
        self.assertEqual([h[0] for h in hits], news[:3])


# ---------------------------------------------------------------- the real google client library


def _have_google():
    try:
        import google_auth_httplib2, googleapiclient.http  # noqa: F401
    except ImportError:
        return False
    return True


class _Recorder(BaseHTTPRequestHandler):
    """One local server: records each request; `answer(handler, body)` writes the response."""

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        self.server.seen.append((self.command, self.path, dict(self.headers), body))
        self.server.answer(self, body)

    do_GET = do_PUT = do_POST

    def log_message(self, *args):
        pass


def _serve(answer):
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    server.seen, server.answer = [], answer
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def batch_answer(handler, body, messages):
    """Answer a Gmail batch call: each part's message, last part first."""
    boundary = re.search(r'boundary="?([^";]+)"?', handler.headers["Content-Type"]).group(1)
    parts = []
    for chunk in body.split("--" + boundary)[1:]:
        if chunk.startswith("--"):
            break
        cid = re.search(r"(?im)^content-id:\s*<([^>]+)>", chunk).group(1)
        mid = re.search(r"(?m)^GET \S*/messages/([^?/\s]+)", chunk).group(1)
        doc = json.dumps(messages[mid])
        parts.append(
            f"--out\r\nContent-Type: application/http\r\nContent-ID: <response-{cid}>\r\n\r\n"
            f"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{doc}\r\n"
        )
    data = ("".join(reversed(parts)) + "--out--\r\n").encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "multipart/mixed; boundary=out")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


@unittest.skipUnless(_have_google(), "the google client library is not installed here (scripts/mail.sh builds it)")
class GoogleBatchCall(unittest.TestCase):
    """AC-11 against the real googleapiclient and httplib2: the batch goes to the test endpoint single calls use, and
    a redirect is never followed, so the parts (each carries the mailbox's bearer token) go nowhere else."""

    TOKEN = "ya29.synthetic-token"

    def gmail(self, endpoint):
        from google.oauth2.credentials import Credentials

        creds = Credentials(token=self.TOKEN)
        with (
            mock.patch.dict(os.environ, {"TICO_GOOGLE_API_ENDPOINT": endpoint}),
            mock.patch.object(auth, "credentials", return_value=creds),
        ):
            return gm.Gmail(auth.gmail_service(BOX), BOX, sleep=lambda s: None)

    def test_a_batch_call_goes_to_the_test_endpoint_and_answers_in_the_order_asked(self):
        messages = {m["id"]: m for m in mail(3)}
        server, url = _serve(lambda h, body: batch_answer(h, body, messages))
        self.addCleanup(server.shutdown)
        got = list(self.gmail(url).read_many(list(messages)))
        self.assertEqual([raw["id"] for _, raw in got], list(messages))
        self.assertEqual([(c, p) for c, p, _, _ in server.seen], [("POST", "/batch/gmail/v1")])
        self.assertEqual(server.seen[0][3].lower().count("authorization: bearer " + self.TOKEN.lower()), 3)

    def test_a_redirect_is_not_followed_and_no_credential_goes_anywhere_else(self):
        elsewhere, other = _serve(lambda h, body: batch_answer(h, body, {m["id"]: m for m in mail(3)}))
        self.addCleanup(elsewhere.shutdown)
        for status in (301, 302, 303, 307, 308):

            def redirect(handler, body, status=status):
                handler.send_response(status)
                handler.send_header("Location", other + "/batch/gmail/v1")
                handler.send_header("Content-Length", "0")
                handler.end_headers()

            server, url = _serve(redirect)
            self.addCleanup(server.shutdown)
            got = dict(self.gmail(url).read_many(["m-000", "m-001"]))
            with self.subTest(status=status):
                self.assertEqual(len(server.seen), 1)
                self.assertEqual(elsewhere.seen, [])
                for failure in got.values():
                    self.assertIsInstance(failure, Failure)
                    self.assertEqual(failure.status, status)
                    self.assertTrue(str(failure).startswith(f"Gmail messages.get failed for {BOX}"))


if __name__ == "__main__":
    unittest.main()
