"""hub.db's write layer: one passing and one refusing case for every rule in docs/history/hub-v2.md §4.

Every test runs against a fresh database in a temp directory with a small fake registry, so
nothing here reads `registry/` or the real `runtime/hub.db`.
"""
import json
import tempfile
import unittest
from pathlib import Path

from backend import hubdb as H
from backend import task_relations as TR

EMPLOYEES = {
    "coo": {"display_name": "COO", "status": "active", "host": "keeper", "runtime": "codex",
            "model": "gpt-6-astra", "reasoning_effort": "medium", "dir": "/tmp/emp-coo"},
    "cmo": {"display_name": "CMO", "status": "active", "host": "keeper", "runtime": "codex"},
    "seo": {"display_name": "SEO", "status": "active", "runtime": "grok"},
    "analytics": {"display_name": "Analytics", "status": "active"},
    "legal": {"display_name": "Legal", "status": "active"},
    "game": {"display_name": "Project Game", "status": "planned"},
    "coach": {"display_name": "Coach", "status": "paused"},
}
PEOPLE = {"people": [
    {"id": "ana", "name": "Ana Rivera", "email": "ana@acme.example", "team": "leadership",
     "primary_for": ["marketing"]},
    {"id": "ben", "name": "Ben", "email": "ben@acme.example", "team": "product",
     "primary_for": ["product"]}]}

ANA = H.human_actor("ana")
CMO = H.bot_actor("cmo")
SEO = H.bot_actor("seo")
COO = H.bot_actor("coo")
ANALYTICS = H.bot_actor("analytics")


class HubCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.conn = H.connect(Path(self.dir.name) / "hub.db")
        self.addCleanup(self.conn.close)
        H.sync_registry(self.conn, EMPLOYEES, PEOPLE)

    def refused(self, rule, fn, *a, **kw):
        with self.assertRaises(H.Refused) as got:
            fn(*a, **kw)
        self.assertEqual(got.exception.rule, rule, got.exception.detail)
        return got.exception


# ----------------------------------------------------------------------------- schema, connect
class Schema(HubCase):
    def test_every_table_and_index_in_the_contract_exists(self):
        names = {r["name"] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertLessEqual({"bots", "humans", "conversations", "messages", "tasks",
                              "task_events", "approvals", "bot_status", "bot_status_history",
                              "schedules", "turns", "deltas", "rate_limits", "refusals",
                              "events"}, names)
        indexes = {r["name"] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertLessEqual({"messages_to_delivered", "messages_conversation",
                              "messages_reply_kind", "messages_from_kind", "tasks_carried_by",
                              "messages_pending_conversation", "bot_file_versions_blob_media",
                              "tasks_owner_status", "tasks_requester_status", "events_ts"},
                             indexes)

    def test_existing_database_gains_reply_and_carried_task_indexes(self):
        row = H.task_create(self.conn, ANA, "Prepare packet", "Gather the source files.", CMO, lint=False)
        self.conn.execute("UPDATE tasks SET carried_by='existing-run' WHERE id=?", (row["id"],))
        self.conn.execute("DROP INDEX messages_reply_kind")
        self.conn.execute("DROP INDEX tasks_carried_by")
        self.conn.execute("DROP INDEX messages_from_kind")
        self.conn.execute("DROP INDEX messages_pending_conversation")
        self.conn.commit()
        self.conn.close()
        migrated = H.connect(Path(self.dir.name) / "hub.db")
        self.addCleanup(migrated.close)
        self.assertEqual(migrated.execute("SELECT id FROM tasks WHERE carried_by='existing-run'").fetchone()[0], row["id"])
        for query, index in (
                ("SELECT id FROM tasks WHERE carried_by='existing-run'", "tasks_carried_by"),
                ("SELECT 1 FROM messages WHERE in_reply_to='question' AND kind='answer'", "messages_reply_kind"),
                ("SELECT id FROM messages WHERE from_actor='bot:cmo' AND kind='ask'", "messages_from_kind"),
                ("SELECT id FROM messages WHERE conversation_id='task-thread' AND kind='ask' "
                 "AND answered_by IS NULL AND deleted_at IS NULL", "messages_pending_conversation")):
            plan = " ".join(r[3] for r in migrated.execute("EXPLAIN QUERY PLAN " + query))
            self.assertIn(index, plan)

    def test_existing_database_gains_the_media_version_lookup_index(self):
        self.conn.execute("DROP INDEX bot_file_versions_blob_media")
        self.conn.close()
        migrated = H.connect(Path(self.dir.name) / "hub.db")
        self.addCleanup(migrated.close)
        plan = " ".join(r[3] for r in migrated.execute(
            "EXPLAIN QUERY PLAN SELECT 1 FROM bot_file_versions "
            "WHERE blob_id='existing-blob' AND media_state='pending'"))
        self.assertIn("COVERING INDEX bot_file_versions_blob_media", plan)

class RegistrySync(HubCase):
    def test_sync_is_idempotent_and_keeps_tokens_threads_and_quarantine(self):
        token = H.issue_token(self.conn, "cmo")
        self.conn.execute("UPDATE bots SET thread_id='t-1' WHERE slug='cmo'")
        H.quarantine(self.conn, "seo", "test")
        H.sync_registry(self.conn, EMPLOYEES, PEOPLE)
        H.sync_registry(self.conn, EMPLOYEES, PEOPLE)
        self.assertEqual(len(H.bots(self.conn)), len(EMPLOYEES))
        self.assertEqual(len(H.humans(self.conn)), len(PEOPLE["people"]))
        self.assertEqual(H.bot(self.conn, "cmo")["thread_id"], "t-1")
        self.assertEqual(H.verify_bot(self.conn, "cmo", token)["slug"], "cmo")
        self.assertEqual(H.bot(self.conn, "seo")["state"], "quarantined")

class Tokens(HubCase):
    def test_a_wrong_or_rotated_token_is_refused(self):
        first = H.issue_token(self.conn, "cmo")
        self.refused("identity", H.verify_bot, self.conn, "cmo", "nope")
        H.issue_token(self.conn, "cmo")                     # the keeper rotates on resume
        self.refused("identity", H.verify_bot, self.conn, "cmo", first)
        self.refused("identity", H.verify_bot, self.conn, "seo", first)   # never issued one


# ----------------------------------------------------------------------------- rule 1
class Rule1Identity(HubCase):
    def test_a_bot_may_not_write_another_bots_status(self):
        self.refused("identity", H.status_set, self.conn, CMO, "seo", state="blocked")

    def test_only_the_addressee_answers_an_ask(self):
        ask = H.say(self.conn, CMO, SEO, "how many posts shipped?", kind="ask")
        self.assertEqual(H.answer(self.conn, SEO, ask["id"], "four")["to_actor"], CMO)
        self.refused("identity", H.answer, self.conn, ANALYTICS, ask["id"], "four")

# ----------------------------------------------------------------------------- rule 2


# ----------------------------------------------------------------------------- rule 3
class Rule3Caps(HubCase):
    def test_twenty_messages_an_hour_pass_and_the_twenty_first_is_capped(self):
        conv = H.open_conversation(self.conn, CMO, [CMO, SEO], subject="the blog")
        for i in range(H.CAP_PER_HOUR):
            H.say(self.conn, CMO, SEO, f"line {i}", conversation_id=conv["id"])
        self.refused("cap", H.say, self.conn, CMO, SEO, "one more", conversation_id=conv["id"])

    def test_an_ask_chain_reaches_depth_three_and_stops(self):
        first = H.say(self.conn, CMO, SEO, "what is the traffic?", kind="ask")
        self.assertEqual(first["refs"]["depth"], 1)
        second = H.say(self.conn, SEO, ANALYTICS, "what is the traffic?", kind="ask")
        self.assertEqual(second["refs"]["depth"], 2)
        third = H.say(self.conn, ANALYTICS, COO, "what is the traffic?", kind="ask")
        self.assertEqual(third["refs"]["depth"], 3)
        self.refused("depth", H.say, self.conn, COO, "legal", "and you?", kind="ask")

# ----------------------------------------------------------------------------- rule 4

# ----------------------------------------------------------------------------- rule 5
class Rule5Tasks(HubCase):
    def open_task(self, requester=CMO, owner=SEO, title="Write the September brief"):
        return H.task_create(self.conn, requester, title, "One page on what we ship.", owner)

    def test_the_owner_moves_it_but_may_not_close_it(self):
        row = self.open_task()
        self.assertEqual(H.task_update(self.conn, SEO, row["id"], status="doing")["status"], "doing")
        self.assertEqual(H.task_update(self.conn, SEO, row["id"], status="done",
                                       note="posted")["status"], "done")
        self.refused("close", H.task_close, self.conn, SEO, row["id"])
        self.refused("close", H.task_update, self.conn, SEO, row["id"], status="closed")

    def test_the_requester_closes_it_and_the_owner_is_woken(self):
        row = self.open_task()
        H.task_update(self.conn, SEO, row["id"], status="done", note="posted")
        closed = H.task_close(self.conn, CMO, row["id"], note="reads well")
        self.assertEqual((closed["status"], closed["closed_by"]), ("closed", CMO))
        woken = [m for m in H.messages(self.conn, row["conversation_id"])
                 if m["to_actor"] == SEO and m["body"].startswith("Closed:")]
        self.assertEqual(len(woken), 1)

    def test_reopening_clears_completion_and_preserves_history(self):
        row = self.open_task()
        H.task_update(self.conn, SEO, row["id"], status="done", note="posted")
        closed = H.task_close(self.conn, CMO, row["id"], note="read")
        opened = H.task_update(self.conn, CMO, row["id"], status="open")
        self.assertEqual(opened["status"], "open")
        for field in ("done_at", "closed_at", "closed_by"):
            self.assertIsNone(opened[field])
            event = self.conn.execute("SELECT old,new FROM task_events WHERE task_id=? AND field=? ORDER BY id DESC LIMIT 1",
                                      (row["id"], field)).fetchone()
            self.assertEqual(event["old"], closed[field])
            self.assertIsNone(event["new"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tasks WHERE done_at IS NOT NULL OR closed_at IS NOT NULL").fetchone()[0], 0)

    def test_only_one_unanswered_question_is_allowed_on_a_task(self):
        row = self.open_task()
        first = H.task_ask(self.conn, SEO, row["id"], "Which format?")
        self.refused("one-question", H.task_ask, self.conn, SEO, row["id"], "Which day?")
        H.answer(self.conn, CMO, first["id"], "Markdown.")
        second = H.task_ask(self.conn, SEO, row["id"], "Which day?")
        self.refused("one-question", H.task_ask, self.conn, SEO, row["id"], "Which audience?")
        H.answer(self.conn, CMO, second["id"], "Monday.")
        self.assertTrue(H.task_ask(self.conn, SEO, row["id"], "Which audience?"))

    def stale(self, row):
        self.conn.execute("UPDATE tasks SET updated=? WHERE id=?", (H.shift(H.now(), days=-2), row["id"]))

    def closed_notices(self, row, to):
        return [m for m in H.messages(self.conn, row["conversation_id"])
                if m["to_actor"] == to and m["body"].startswith(("Closed:", "Child closed:"))]

    def test_human_records_a_result_to_finish_a_bot_request_but_closes_without_a_reason(self):
        decision = H.task_create(self.conn, CMO, "Decide whether to release the draft", "Review it.", ANA)
        self.refused("lint", H.task_update, self.conn, ANA, decision["id"], status="done")
        self.assertEqual(H.task(self.conn, decision["id"])["status"], "open")
        dropped = H.task_create(self.conn, CMO, "Pick a launch date", "Choose one.", ANA)
        self.assertEqual(H.task_close(self.conn, ANA, dropped["id"])["status"], "closed")
        self.assertTrue(self.closed_notices(dropped, CMO), "the bot that asked is told it was closed")
        result = H.task_update(self.conn, ANA, decision["id"], status="done",
                               note="Keep the draft on hold; do not publish it.")
        self.assertEqual(result["status"], "done")
        told = [m for m in H.messages(self.conn, decision["conversation_id"])
                if m["to_actor"] == CMO and m["body"].startswith("Finished:")]
        self.assertEqual(len(told), 1)
        self.assertIn("Keep the draft on hold", told[0]["body"])

    def test_moving_a_task_to_review_wakes_the_bot_that_asked_for_it(self):
        row = H.task_create(self.conn, CMO, "Speed up the overview checks", "Make them pass alone.", SEO)
        H.task_update(self.conn, SEO, row["id"], status="review", note="Pushed; all changed scripts pass.")
        told = [m for m in H.messages(self.conn, row["conversation_id"])
                if m["to_actor"] == CMO and m["body"].startswith("Ready for review:")]
        self.assertEqual(len(told), 1)
        self.assertIn("all changed scripts pass", told[0]["body"])

# ----------------------------------------------------------------------------- rule 6
class Rule6Approvals(HubCase):
    SEND = {"to": "ops@acme.com", "cc": "", "subject": "Your September invoice",
            "body_sha256": "a" * 64, "mailbox": "ana@acme.example"}

    def test_an_exact_payload_is_requested_decided_and_spent_once(self):
        appr = H.approval_request(self.conn, CMO, "send", self.SEND)
        self.assertEqual(appr["payload_hash"], H.payload_hash(self.SEND))
        self.assertIsNone(appr["decision"])
        decided = H.approval_decide(self.conn, ANA, appr["id"], "approved", note="go")
        self.assertEqual((decided["decision"], decided["decided_by"]), ("approved", ANA))
        self.assertTrue(H.approval_consume(self.conn, CMO, appr["id"])["consumed_at"])
        self.refused("consumed", H.approval_consume, self.conn, CMO, appr["id"])

    def test_only_a_human_decides_and_only_once(self):
        appr = H.approval_request(self.conn, CMO, "spend",
                                  {"amount": 250, "account": "ads", "what": "one week of tests"})
        self.refused("identity", H.approval_decide, self.conn, COO, appr["id"], "approved")
        self.refused("identity", H.approval_decide, self.conn, H.KEEPER, appr["id"], "approved")
        H.approval_decide(self.conn, ANA, appr["id"], "approved")
        self.refused("duplicate", H.approval_decide, self.conn, ANA, appr["id"], "declined")

    def test_an_undecided_or_declined_approval_cannot_be_spent(self):
        appr = H.approval_request(self.conn, CMO, "merge", {"repo": "emp-seo", "pr": 12})
        self.refused("kind", H.approval_consume, self.conn, CMO, appr["id"])
        H.approval_decide(self.conn, ANA, appr["id"], "declined")
        self.refused("kind", H.approval_consume, self.conn, CMO, appr["id"])


# ----------------------------------------------------------------------------- rule 7
class Rule7Lint(HubCase):
    GOOD = ("Ship the 60/40 split to paid this month, or tell me to hold.\n"
            "It beat the 80/20 split on cost per lead in August.")

class Rule4Unsolicited(HubCase):
    def test_a_bot_may_start_ten_messages_a_day_to_a_person_and_the_eleventh_is_refused(self):
        self.assertEqual(H.UNSOLICITED_PER_DAY, 10)
        for i in range(H.UNSOLICITED_PER_DAY):
            H.say(self.conn, CMO, ANA, f"Pick the launch date {i}, or tell me to hold.")
        e = self.refused("unsolicited", H.say, self.conn, CMO, ANA, "Pick the launch date again, or tell me to hold.")
        self.assertIn("10", e.detail)

# ----------------------------------------------------------------------------- rule 8
class Rule8Counting(HubCase):
    def refuse_reach(self, n, actor=CMO):
        """`n` refusals that count: the same refused write made n + 1 times (the first is recorded, not counted)."""
        for _ in range(n + 1):
            with self.assertRaises(H.Refused):
                H.say(self.conn, actor, "outsider@example.com", "hello")

    def test_ten_different_first_refusals_do_not_quarantine(self):
        for i in range(H.QUARANTINE_AT + 2):
            with self.assertRaises(H.Refused):
                H.say(self.conn, CMO, f"outsider{i}@example.com", "hello")
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active", "a first try at something new only counts once repeated")
        self.assertEqual(len(H.refusals_for(self.conn, CMO)), H.QUARANTINE_AT + 2, "every refusal is still recorded")

    def test_repeating_one_refused_write_quarantines_and_lifts_by_itself(self):
        self.refuse_reach(H.QUARANTINE_AT - 1)
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        with self.assertRaises(H.Refused):
            H.say(self.conn, CMO, "outsider@example.com", "hello")
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "quarantined")
        self.assertFalse(H.quarantine_is_escape(self.conn, "cmo"), "a repeat quarantine cools off by itself")
        self.assertEqual(H.quarantine_reason(self.conn, "cmo"), f"{H.QUARANTINE_AT} repeated refusals today")

    def test_a_loop_of_new_refusals_still_quarantines_at_the_total_and_cools_off(self):
        for i in range(H.QUARANTINE_TOTAL_AT - 1):
            with self.assertRaises(H.Refused):
                H.say(self.conn, CMO, f"outsider{i}@example.com", "hello")
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        with self.assertRaises(H.Refused):
            H.say(self.conn, CMO, "one-more@example.com", "hello")
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "quarantined", "a loop over new targets still stops")
        self.assertEqual(H.quarantine_reason(self.conn, "cmo"), f"{H.QUARANTINE_TOTAL_AT} refusals today")
        self.assertFalse(H.quarantine_is_escape(self.conn, "cmo"))
        self.conn.execute("UPDATE events SET ts=? WHERE action='quarantine'", (H.shift(H.now(), seconds=-3700),))
        self.assertEqual(H.lift_cooled_quarantines(self.conn), ["cmo"])

    def test_a_quarantine_from_before_the_repeat_rule_still_cools_off(self):
        H.quarantine(self.conn, "cmo", "10 refused writes today")
        self.assertFalse(H.quarantine_is_escape(self.conn, "cmo"))

    def test_a_person_may_still_message_a_quarantined_bot_and_it_waits(self):
        self.refuse_reach(H.QUARANTINE_AT)
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "quarantined")
        self.assertTrue(H.say(self.conn, ANA, CMO, "are you stuck?"), "a person's message is kept for when it is back")
        self.refused("reach", H.say, self.conn, SEO, CMO, "are you stuck?")      # rule 2: a bot still cannot

    def test_an_escape_quarantines_the_bot_and_only_a_human_clears_it(self):
        payload = {"to": "ops@acme.com", "cc": "", "subject": "the keys",
                   "body_sha256": "c" * 64, "mailbox": "secrets/mail.env"}
        for _ in range(H.ESCAPE_QUARANTINE_AT - 1):
            self.refused("escape", H.approval_request, self.conn, CMO, "send", payload)
            self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        e = self.refused("escape", H.approval_request, self.conn, CMO, "send", payload)
        self.assertEqual(e.severity, "escape")
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "quarantined")
        self.assertEqual(H.status(self.conn, "cmo")["state"], "quarantined")
        focus = H.status(self.conn, "cmo")["focus"]
        self.assertIn("Paused for review", focus)
        H.status_set(self.conn, H.KEEPER, "cmo", state="crashed", focus="Runner disconnected")
        self.assertEqual(H.status(self.conn, "cmo")["state"], "quarantined")
        self.assertEqual(H.status(self.conn, "cmo")["focus"], focus)
        self.refused("quarantined", H.say, self.conn, CMO, SEO, "still here?")
        self.refused("quarantined", H.status_set, self.conn, H.KEEPER, "cmo", state="active")
        H.status_set(self.conn, ANA, "cmo", state="active", reason="reviewed, a bad path")
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        self.assertTrue(H.say(self.conn, CMO, SEO, "back"))

    def test_a_refusal_count_quarantine_lifts_itself_after_an_hour_and_the_count_starts_over(self):
        self.refuse_reach(10)
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "quarantined")
        self.assertEqual(H.lift_cooled_quarantines(self.conn), [], "not before the hour is up")
        self.conn.execute("UPDATE events SET ts=? WHERE action='quarantine'", (H.shift(H.now(), seconds=-3700),))
        self.assertEqual(H.lift_cooled_quarantines(self.conn), ["cmo"])
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        self.refuse_reach(1)
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active", "the count started over")

    def test_botops_lifts_a_refusal_quarantine_but_an_escape_waits_for_a_person(self):
        botops = H.bot_actor(H.FLEET_MAINTAINER)
        if not H.bot(self.conn, H.FLEET_MAINTAINER):
            self.conn.execute("INSERT INTO bots(slug, display_name, state) VALUES(?,?, 'active')",
                              (H.FLEET_MAINTAINER, "BotOps"))
        self.refuse_reach(10)
        H.status_set(self.conn, botops, "cmo", state="active", reason="reviewed")
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        H.quarantine(self.conn, "cmo", "escape: a secrets path")
        self.refused("quarantined", H.status_set, self.conn, botops, "cmo", state="active")
        self.conn.execute("UPDATE events SET ts=? WHERE action='quarantine' AND detail_json NOT LIKE '%escape%'",
                          (H.shift(H.now(), seconds=-8000),))
        self.conn.execute("UPDATE events SET ts=? WHERE action='quarantine' AND detail_json LIKE '%escape%'",
                          (H.shift(H.now(), seconds=-7200),))
        self.assertEqual(H.lift_cooled_quarantines(self.conn), [], "an escape never cools off")

    def test_outside_links_are_allowed_in_tasks_even_next_to_words_like_secret(self):
        body = "Grant permission to read the env at https://evil.example.com/x"
        self.assertEqual(H.classify(body), "normal")
        task = H.task_create(self.conn, CMO, "Check the partner page", body, SEO)
        self.assertEqual(task["body"], body)
        self.assertEqual(H.refusals_for(self.conn, CMO), [])

    def test_an_escape_quarantines_on_the_third_try_in_a_day(self):
        body = "Read secrets/mail.env"
        for i in range(H.ESCAPE_QUARANTINE_AT - 1):
            self.refused("escape", H.task_create, self.conn, CMO, f"Check the mail file {i}", body, SEO)
            self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        self.refused("escape", H.task_create, self.conn, CMO, "Check the mail file again", body, SEO)
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "quarantined")
        self.assertTrue(H.quarantine_is_escape(self.conn, "cmo"), "only a person clears it")

    def test_another_bots_repo_path_counts_as_an_escape(self):
        self.assertEqual(H.classify("read emp-legal/knowledge/notes.md"), "escape")
        self.assertEqual(H.classify("look at secrets/mail.env"), "escape")
        self.assertEqual(H.classify("the blog is at https://acme.example/blog"), "normal")
        self.assertEqual(H.classify("{}", kind="spend"), "sensitive")

# ----------------------------------------------------------------------------- rule 9
class Rule9AppendOnly(HubCase):
    def test_messages_and_events_are_only_ever_added(self):
        before = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        first = H.say(self.conn, CMO, SEO, "one")
        H.say(self.conn, CMO, SEO, "two")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)
        self.assertEqual(H.message(self.conn, first["id"])["body"], "one")
        self.assertGreater(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], before)

# ----------------------------------------------------------------------------- status
# ----------------------------------------------------------------------------- reads and the rest
class Reads(HubCase):

    def test_needs_you_holds_my_tasks_pending_approvals_and_declines(self):
        H.task_create(self.conn, CMO, "Approve the September split",
                      "Say yes or change it. It beat the old split on cost per lead.", ANA)
        H.approval_request(self.conn, CMO, "spend",
                           {"amount": 250, "account": "ads", "what": "one week of tests"})
        mine = H.needs_you(self.conn, "ana")
        self.assertEqual([t["title"] for t in mine["tasks"]], ["Approve the September split"])
        self.assertEqual(len(mine["approvals"]), 1)

class APersonsReplyAnswersWhatWasAsked(HubCase):
    """A reply resolves only the ask or task it identifies, leaving other work open."""

    def test_task_reply_allows_the_next_question_without_answering_another_task(self):
        room = H.say(self.conn, ANA, CMO, "Please write two drafts")["conversation_id"]
        first = H.task_create(self.conn, ANA, "Draft the report", "Write a report", CMO, conversation_id=room)
        second = H.task_create(self.conn, ANA, "Draft the update", "Write an update", CMO, conversation_id=room)
        question = H.task_ask(self.conn, CMO, first["id"], "Which format?")
        other = H.task_ask(self.conn, CMO, second["id"], "Which audience?")
        self.assertEqual(first["conversation_id"], second["conversation_id"])
        self.assertEqual(H.unanswered_ask(self.conn, first)["id"], question["id"])
        self.assertEqual(H.unanswered_ask(self.conn, second)["id"], other["id"])
        reply = H.task_comment(self.conn, ANA, first["id"], "Markdown")
        self.assertEqual(H.answers_to(self.conn, [question["id"]])[question["id"]]["id"], reply["id"])
        self.assertIsNone(H.unanswered_ask(self.conn, first))
        self.assertIsNone(H.waiting_for(self.conn, first))
        self.assertEqual(H.waiting_for(self.conn, second), "an unanswered question")
        self.assertEqual([row["id"] for row in H.tasks_asked_of(self.conn, ANA)], [second["id"]])
        self.assertTrue(H.task_ask(self.conn, CMO, first["id"], "Which date?"))
        self.refused("one-question", H.task_ask, self.conn, CMO, second["id"], "Another question?")

    def test_older_untagged_questions_still_belong_to_their_dedicated_task(self):
        task = H.task_create(self.conn, ANA, "Draft a report", "Write it", CMO)
        ask = H.task_ask(self.conn, CMO, task["id"], "Which format?")
        self.conn.execute("UPDATE messages SET refs_json='{}' WHERE id=?", (ask["id"],))
        self.assertEqual(H.unanswered_ask(self.conn, task)["id"], ask["id"])
        H.task_comment(self.conn, ANA, task["id"], "Markdown")
        self.assertIsNone(H.unanswered_ask(self.conn, task))
        self.assertTrue(H.task_ask(self.conn, CMO, task["id"], "Which date?"))

    def test_general_chat_preserves_asks_and_direct_reply_closes_only_named_ask(self):
        a = H.say(self.conn, CMO, ANA, "ship now or wait for the split?", kind="ask")
        b = H.say(self.conn, CMO, ANA, "is 7% the right warning level?", kind="ask")
        H.say(self.conn, ANA, CMO, "ship now, and 7% is right. also re-check Aug 13.")
        self.assertEqual(H.answers_to(self.conn, [a["id"], b["id"]]), {},
                         "a general message does not identify which questions it answers")
        reply = H.say(self.conn, ANA, CMO, "ship now. also re-check Aug 13.",
                      conversation_id=a["conversation_id"], in_reply_to=a["id"])
        got = H.answers_to(self.conn, [a["id"], b["id"]])
        self.assertEqual(set(got), {a["id"]}, "the sibling ask remains unanswered")
        self.assertEqual(got[a["id"]]["id"], reply["id"])
        self.assertIn("re-check Aug 13", got[a["id"]]["body"], "the whole reply is the answer")

if __name__ == "__main__":
    unittest.main()




class WaitingWithDependency(HubCase):
    def setUp(self):
        super().setUp()
        # Server tasks carry the optimistic version used when completing a blocker.
        self.conn.execute("ALTER TABLE tasks ADD COLUMN version INTEGER NOT NULL DEFAULT 1")

    def test_self_requested_task_can_wait_once_its_blocker_is_related(self):
        blocker = H.task_create(self.conn, CMO, 'Prepare the build environment', '', CMO)
        task = H.task_create(self.conn, CMO, 'Validate the release candidate', '', CMO)
        TR.relate(self.conn, CMO, task['id'], blocker['id'], 'blocked_by')
        after = H.task_update(self.conn, CMO, task['id'], status='waiting', note='Needs the build environment')
        self.assertEqual((after['status'], TR.blocker_ids(self.conn, task['id'])), ('waiting', [blocker['id']]))
        self.assertEqual(H.waiting_for(self.conn, after), 'an open blocker')
        future = H.shift(H.now(), hours=48)
        self.assertNotIn(task['id'], [r['id'] for r in H.stalled_tasks(self.conn, at=future)])
        self.assertEqual(H.sweep_stranded(self.conn, at=future), [])
        H.task_update(self.conn, CMO, blocker['id'], status='done')
        self.assertEqual(TR.blocker_ids(self.conn, task['id']), [])
        self.assertTrue(any('Unblocked:' in m['body'] for m in H._rows(
            self.conn.execute("SELECT body FROM messages WHERE to_actor=?", (CMO,)))))

    def test_removed_or_finished_blocker_does_not_justify_waiting(self):
        blocker = H.task_create(self.conn, CMO, 'Prepare the build environment', '', CMO)
        task = H.task_create(self.conn, CMO, 'Validate the release candidate', '', CMO)
        TR.relate(self.conn, CMO, task['id'], blocker['id'], 'blocked_by')
        TR.relate(self.conn, CMO, task['id'], blocker['id'], 'blocked_by', remove=True)
        self.refused('lint', H.task_update, self.conn, CMO, task['id'], status='waiting')
        self.assertEqual(H.task(self.conn, task['id'])['status'], 'open')
        TR.relate(self.conn, CMO, task['id'], blocker['id'], 'blocked_by')
        H.task_update(self.conn, CMO, blocker['id'], status='done')
        self.refused('lint', H.task_update, self.conn, CMO, task['id'], status='waiting')

    def test_invalid_dependency_is_refused(self):
        task = H.task_create(self.conn, CMO, 'Validate the release candidate', '', CMO)
        for blocker in ('missing-task', task['id']):
            with self.assertRaises(H.Refused):
                TR.relate(self.conn, CMO, task['id'], blocker, 'blocked_by')
        self.assertEqual(TR.blocker_ids(self.conn, task['id']), [])

    def test_private_blocker_still_requires_access(self):
        blocker = H.task_create(self.conn, SEO, 'Prepare a private report', '', SEO, private=True)
        task = H.task_create(self.conn, CMO, 'Validate the release candidate', '', CMO)
        self.refused('not-found', TR.relate, self.conn, CMO, task['id'], blocker['id'], 'blocked_by')
        self.assertEqual(TR.blocker_ids(self.conn, task['id']), [])

    def test_a_task_waiting_on_a_person_stays_waiting_and_counts_until_they_answer(self):
        H.status_set(self.conn, H.KEEPER, "cmo", state="idle")
        needs = lambda: H.status(self.conn, "cmo")["needs_human"]
        task = H.task_create(self.conn, CMO, 'Fix the build host', '', CMO, private=False)
        after = H.task_update(self.conn, CMO, task['id'], status='waiting', waiting_on='ana',
                              note='Restart the build host; it refuses SSH')
        self.assertEqual((after['waiting_on'], needs()), (ANA, 1))
        self.assertEqual(H.waiting_for(self.conn, after), 'ana to act')
        self.assertEqual(H.sweep_stranded(self.conn, at=H.shift(H.now(), hours=48)), [])
        self.assertEqual([t['id'] for t in H.needs_you(self.conn, 'ana')['waiting']], [task['id']])
        H.task_comment(self.conn, ANA, task['id'], 'Restarted it')
        self.assertEqual((H.task(self.conn, task['id'])['waiting_on'], needs()), (None, 0))
        # A question to a person counts too, once per task, until it is answered.
        other = H.task_create(self.conn, ANA, 'Draft the launch post', '', CMO)
        ask = H.task_ask(self.conn, CMO, other['id'], 'Which date?')
        self.assertEqual(needs(), 1)
        H.answer(self.conn, ANA, ask['id'], 'Friday')
        self.assertEqual(needs(), 0)
        secret = H.task_create(self.conn, CMO, 'Review the payroll export', '', CMO, private=True)
        self.refused('private', H.task_update, self.conn, CMO, secret['id'], status='waiting', waiting_on='ben')

    def test_only_the_owner_names_who_it_waits_on_and_the_wait_is_counted_once(self):
        H.status_set(self.conn, H.KEEPER, "cmo", state="idle")
        H.status_set(self.conn, H.KEEPER, "seo", state="idle")
        stored = lambda bot: self.conn.execute("SELECT needs_human FROM bot_status WHERE bot=?", (bot,)).fetchone()[0]
        task = H.task_create(self.conn, ANA, 'Fix the build host', '', CMO, private=False)
        wait = lambda **kw: H.task_update(self.conn, CMO, task['id'], status='waiting', waiting_on='ana',
                                          note='Restart it', **kw)
        self.refused('identity', H.task_update, self.conn, ANA, task['id'], status='waiting', waiting_on='ana')
        self.refused('kind', wait, owner='seo')
        self.refused('escape', H.task_update, self.conn, CMO, task['id'], status='waiting', waiting_on='ana',
                     note='Read secrets/mail.env')
        self.refused('private', H.task_update, self.conn, CMO, task['id'], status='waiting', waiting_on='ben',
                     private=True)
        wait()
        self.assertIsNone(H.task_update(self.conn, CMO, task['id'], note='Still down')['waiting_on'])
        wait()
        self.assertIsNone(H.task_update(self.conn, CMO, task['id'], owner='seo')['waiting_on'])
        self.assertEqual((stored('cmo'), stored('seo')), (0, 0), "the handoff recounts the old owner too")
        # Made private: the wait no longer goes in front of a person who cannot read it.
        own = H.task_create(self.conn, CMO, 'Rotate the deploy key', '', CMO, private=False)
        H.task_update(self.conn, CMO, own['id'], status='waiting', waiting_on='ben', note='Approve the rotation')
        self.assertIsNone(H.task_update(self.conn, CMO, own['id'], private=True)['waiting_on'])
        # An approval on a task that waits on a person is the same wait.
        key = H.task_create(self.conn, CMO, 'Buy a signing key', '', CMO, private=False)
        H.task_update(self.conn, CMO, key['id'], status='waiting', waiting_on='ana', note='Approve the purchase')
        H.approval_request(self.conn, CMO, 'spend', {'amount': 5, 'account': 'ops', 'what': 'a key'}, task_id=key['id'])
        self.assertEqual((H.status_live(self.conn, 'cmo')['needs_human'], stored('cmo')), (1, 1))
        # A plain reply answers a question; deleting the reply reopens it.
        other = H.task_create(self.conn, ANA, 'Draft the launch post', '', SEO)
        H.task_ask(self.conn, SEO, other['id'], 'Which date?')
        self.assertEqual(stored('seo'), 1)
        reply = H.task_comment(self.conn, ANA, other['id'], 'Friday')
        self.assertEqual(stored('seo'), 0)
        H.task_comment_delete(self.conn, ANA, other['id'], reply['id'])
        self.assertEqual(stored('seo'), 1)
