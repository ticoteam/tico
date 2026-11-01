"""Approvals and asks nobody will act on leave Needs you: the task ended, a newer request replaced them, or they aged."""
from backend import approval_expiry
from backend import hubdb as H
from backend.tests.test_hubdb import ANA, CMO, HubCase

MERGE = {"repo": "acme/site", "pr": 12}


class Withdrawn(HubCase):
    def pending(self):
        return [a["id"] for a in H.approvals(self.conn, pending=True)]

    def test_closing_or_finishing_a_task_withdraws_its_approvals_and_tells_the_bot_quietly(self):
        row = H.task_create(self.conn, ANA, "Ship the site fix", "Merge it.", CMO, lint=False)
        appr = H.approval_request(self.conn, CMO, "merge", MERGE, task_id=row["id"])
        H.task_update(self.conn, CMO, row["id"], status="done", note="shipped")
        after = H.approval(self.conn, appr["id"])
        self.assertEqual((after["decision"], after["decided_by"]), ("withdrawn", H.KEEPER))
        self.assertEqual(self.pending(), [])
        told = self.conn.execute("SELECT body, refs FROM messages WHERE recipient_id=? AND body LIKE 'Withdrawn:%'",
                                 (CMO,)).fetchall()
        self.assertEqual(len(told), 1)
        self.assertIn('"quiet": true', told[0]["refs"])
        self.refused("duplicate", H.approval_decide, self.conn, ANA, appr["id"], "approved")

    def test_a_newer_request_for_the_same_target_or_one_naming_it_replaces_the_older(self):
        row = H.task_create(self.conn, ANA, "Release", "Cut it.", CMO, lint=False)
        # A release is asked for by its release page, so a re-cut of the same version replaces the first ask.
        release = {"url": "https://github.com/acme/site/releases/tag/v1.2.0", "version": "1.2.0", "commit": "a1",
                   "content_sha256": "a" * 64}
        self.refused("lint", H.approval_request, self.conn, CMO, "publish", {**release, "url": ""}, task_id=row["id"])
        first = H.approval_request(self.conn, CMO, "publish", release, task_id=row["id"])
        recut = H.approval_request(self.conn, CMO, "publish", {**release, "commit": "b2", "content_sha256": "b" * 64},
                                   task_id=row["id"])
        other = H.approval_request(self.conn, CMO, "publish", {"url": "https://acme.example/blog",
                                                               "content_sha256": "c" * 64}, task_id=row["id"])
        loose = H.approval_request(self.conn, CMO, "spend", {"amount": 5, "account": "ads", "what": "a test"})
        named = H.approval_request(self.conn, CMO, "spend", {"amount": 6, "account": "ads", "what": "a test"},
                                   supersedes=loose["id"])
        self.assertEqual(H.approval(self.conn, first["id"])["decision"], "withdrawn")
        self.assertEqual(H.approval(self.conn, loose["id"])["decision"], "withdrawn")
        self.assertEqual(sorted(self.pending()), sorted([recut["id"], other["id"], named["id"]]))

    def test_an_approval_records_when_it_was_ready_and_whose_decision_it_is(self):
        row = H.task_create(self.conn, ANA, "Release", "Cut it.", CMO, lint=False)
        release = {"url": "https://github.com/acme/site/releases/tag/v1.2.0", "version": "1.2.0", "commit": "a1",
                   "content_sha256": "a" * 64}
        appr = H.approval_request(self.conn, CMO, "publish", release, task_id=row["id"])
        stored = self.conn.execute("SELECT ready_since, decision_owner FROM approvals WHERE id=?", (appr["id"],)).fetchone()
        self.assertEqual(tuple(stored), (appr["created_at"], ANA))           # recorded, not only read back
        self.assertEqual(appr["decision_owner_name"], H.person_name(self.conn, "ana"))
        self.assertTrue(H.is_release_approval("publish", appr["payload"]))
        self.assertFalse(H.is_release_approval("publish", {**release, "commit": ""}))
        # Ready 2 h 10 min ago, as Needs you and the batch read it; then its decision stops the clock.
        ready = H.shift(H.now(), hours=-2, minutes=-10)
        self.conn.execute("UPDATE approvals SET ready_since=? WHERE id=?", (ready, appr["id"]))
        (pending,) = H.needs_you(self.conn, ANA)["approvals"]
        self.assertEqual(pending["age_s"] // 60, 130)
        self.assertEqual(H.approval_wait_line(pending), f"Ready 2 h 10 min, waiting on {pending['decision_owner_name']}")
        decided = H.approval_decide(self.conn, ANA, appr["id"], "approved")
        self.conn.execute("UPDATE approvals SET decided_at=? WHERE id=?", (H.shift(ready, minutes=30), appr["id"]))
        self.assertEqual(H.approval(self.conn, appr["id"])["age_s"], 30 * 60)
        self.assertEqual(decided["decision"], "approved")
        # A row from before these were recorded reads its request time and the person it was sent to.
        old = H.approval_request(self.conn, CMO, "merge", MERGE)
        self.conn.execute("UPDATE approvals SET ready_since=NULL, decision_owner=NULL WHERE id=?", (old["id"],))
        (legacy,) = H.approvals(self.conn, pending=True)
        to = H.message(self.conn, old["message_id"])["recipient_id"]
        self.assertEqual((legacy["ready_since"], legacy["decision_owner"]), (old["created_at"], to))

    def test_an_approval_undecided_for_fourteen_days_is_withdrawn_and_may_be_asked_again(self):
        appr = H.approval_request(self.conn, CMO, "merge", MERGE)
        later = H.shift(appr["created_at"], days=H.APPROVAL_STALE_DAYS, minutes=1)
        self.assertEqual(approval_expiry.sweep(self.conn, H.shift(appr["created_at"], days=13))["stale"], [])
        self.assertEqual(approval_expiry.sweep(self.conn, later)["stale"], [appr["id"]])
        self.assertEqual(H.approval_request(self.conn, CMO, "merge", MERGE)["decision"], None)

    def test_the_sweep_clears_what_tasks_that_ended_earlier_left_open_but_a_done_task_keeps_its_review(self):
        notes = H.task_create(self.conn, ANA, "Write the notes", "Draft them.", CMO, lint=False)
        appr = H.approval_request(self.conn, CMO, "merge", MERGE, task_id=notes["id"])
        plain = H.task_ask(self.conn, CMO, notes["id"], "Should the notes cover the beta too?")
        site = H.task_create(self.conn, ANA, "Fix the site", "Fix it.", CMO, lint=False)
        review = H.task_comment(self.conn, CMO, site["id"], "Is this ready?", ask={"questions": [
            {"id": "ok", "header": "Review", "question": "Is this ready?", "options": [{"label": "Yes"}, {"label": "No"}]}]})
        # Ended before this change shipped: nothing withdrew or superseded anything then. Separate tasks, because a
        # newer ask from the same bot to the same person already replaces the plain one.
        self.conn.execute("UPDATE tasks SET status='done' WHERE id IN (?,?)", (notes["id"], site["id"]))
        out = approval_expiry.sweep(self.conn)
        self.assertEqual((out["ended"], out["asks"]), ([appr["id"]], [plain["id"]]))
        self.assertIsNone(H.message(self.conn, review["id"])["superseded_at"])
        # A task that has ended takes no new approval: nothing would act on it.
        self.refused("lint", H.approval_request, self.conn, CMO, "merge", {**MERGE, "pr": 99}, task_id=notes["id"])

    def test_a_bot_cannot_ask_a_person_about_an_ended_task_but_a_done_task_still_asks_for_acceptance(self):
        review = {"questions": [{"id": "ok", "header": "Review", "question": "Is this ready?",
                                 "options": [{"label": "Yes"}, {"label": "No"}]}]}
        done = H.task_create(self.conn, ANA, "Write the notes", "Draft them.", CMO, lint=False)
        H.task_update(self.conn, CMO, done["id"], status="done", note="drafted")
        # It would only be superseded within the hour, so it is refused when asked, however it is asked.
        self.refused("lint", H.task_ask, self.conn, CMO, done["id"], "Should the notes cover the beta too?")
        self.refused("lint", H.say, self.conn, CMO, ANA, "Should the notes cover the beta too?", kind="ask",
                     refs={"task": done["id"]})
        accepted = H.task_comment(self.conn, CMO, done["id"], "Is this ready?", ask=review)
        self.assertEqual(approval_expiry.sweep(self.conn)["asks"], [])
        self.assertIsNone(H.message(self.conn, accepted["id"])["superseded_at"])
        closed = H.task_create(self.conn, ANA, "Fix the site", "Fix it.", CMO, lint=False)
        H.task_close(self.conn, ANA, closed["id"])
        self.refused("lint", H.task_comment, self.conn, CMO, closed["id"], "Is this ready?", ask=review)
        # A bot's ask to another bot, and a person's ask, are not a person's queue.
        self.assertEqual(H.say(self.conn, ANA, CMO, "Why was it closed?", kind="ask",
                               refs={"task": closed["id"]})["kind"], "ask")
