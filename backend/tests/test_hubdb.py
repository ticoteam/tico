"""hub.db's write layer: one passing and one refusing case for every rule in docs/history/hub-v2.md §4.

Every test runs against a fresh database in a temp directory with a small fake registry, so
nothing here reads `registry/` or the real `runtime/hub.db`.
"""
import hashlib
import json
import re
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from backend import hubdb as H
from backend import market as MK
from backend import migrations
from backend import task_relations as TR
from backend import wake_presets as WP

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

    def before(self, version):
        """The database as an install from before migration `version` left it: H.connect has applied every migration
        file (hubdb.migrate), so the records from that one on are forgotten and migrations.apply runs them again."""
        self.conn.execute("DELETE FROM schema_migrations WHERE version>=? AND version NOT LIKE 'legacy%'", (version,))
        self.conn.commit()

    def refused(self, rule, fn, *a, **kw):
        with self.assertRaises(H.Refused) as got:
            fn(*a, **kw)
        self.assertEqual(got.exception.rule, rule, got.exception.detail)
        return got.exception


# ----------------------------------------------------------------------------- schema, connect
class Schema(HubCase):
    def test_published_schema_upgrade_adds_run_rotation_and_preserves_tasks(self):
        task = H.task_create(self.conn, ANA, "Prepare packet", "Keep this task through upgrade.", CMO, lint=False)
        # Main has already applied the purpose migration; rotation must be appended after it.
        published_version = H.MIGRATIONS.index(H.RUN_PURPOSE_SCHEMA) + 1
        self.conn.execute("INSERT INTO turns(id,bot,started_at,purpose) VALUES('before-rotation','cmo',?,'review PR')",
                          (H.now(),))
        self.conn.execute("ALTER TABLE turns DROP COLUMN rotated_at")
        self.conn.execute(f"PRAGMA user_version={published_version}")
        self.conn.commit()
        H.migrate(self.conn)
        self.assertIn("rotated", {r[1] for r in self.conn.execute("PRAGMA table_info(turns)")})
        self.assertEqual(self.conn.execute("SELECT title FROM tasks WHERE id=?", (task["id"],)).fetchone()[0],
                         "Prepare packet")
        self.assertEqual(self.conn.execute("SELECT purpose FROM turns WHERE id='before-rotation'").fetchone()[0],
                         "review PR")
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], len(H.MIGRATIONS))

    def test_an_install_that_ran_the_first_rule_events_schema_gets_the_task_trigger_tables(self):
        for table in ("rule_task_runs", "rule_task_state"):
            self.conn.execute(f"DROP TABLE {table}")
        self.conn.execute(f"PRAGMA user_version={H.MIGRATIONS.index(H.RULE_EVENTS_SCHEMA) + 1}")
        self.conn.commit()
        H.migrate(self.conn)
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertLessEqual({"rule_task_runs", "rule_task_state"}, names)

    def test_an_install_at_the_last_schema_before_task_run_models_gets_the_table_and_keeps_its_tasks(self):
        task = H.task_create(self.conn, ANA, "Prepare packet", "Keep this task through upgrade.", CMO, lint=False)
        self.conn.execute("DROP TABLE task_run_models")
        self.conn.execute(f"PRAGMA user_version={H.MIGRATIONS.index(H.TASK_RUN_MODELS_SCHEMA)}")
        self.conn.commit()
        H.migrate(self.conn)
        names = {r["name"] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("task_run_models", names)
        self.assertEqual(self.conn.execute("SELECT title FROM tasks WHERE id=?", (task["id"],)).fetchone()[0],
                         "Prepare packet")
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], len(H.MIGRATIONS))

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
                              "tasks_owner_status_cover", "tasks_requester_status_cover", "events_ts"},
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
                ("SELECT id FROM messages WHERE sender_id='bot:cmo' AND kind='ask'", "messages_from_kind"),
                ("SELECT id FROM messages WHERE conversation_id='task-thread' AND kind='ask' "
                 "AND answered_by_id IS NULL AND deleted_at IS NULL", "messages_pending_conversation")):
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
        self.assertEqual(H.answer(self.conn, SEO, ask["id"], "four")["recipient_id"], CMO)
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
        self.assertEqual((closed["status"], closed["closed_by_id"]), ("closed", CMO))
        woken = [m for m in H.messages(self.conn, row["conversation_id"])
                 if m["recipient_id"] == SEO and m["body"].startswith("Closed:")]
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
        self.conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), days=-2), row["id"]))

    def closed_notices(self, row, to):
        return [m for m in H.messages(self.conn, row["conversation_id"])
                if m["recipient_id"] == to and m["body"].startswith(("Closed:", "Child closed:"))]

    def test_a_person_finishes_a_bot_request_in_one_tap_and_the_bot_hears_who_did(self):
        tapped = H.task_create(self.conn, CMO, "Decide whether to release the draft", "Review it.", ANA)
        self.assertEqual(H.task_update(self.conn, ANA, tapped["id"], status="done")["status"], "done")
        [told] = self.finished(tapped, CMO)
        self.assertEqual(told["body"], "Ana Rivera marked this done: Decide whether to release the draft")
        noted = H.task_create(self.conn, CMO, "Pick a launch date", "Choose one.", ANA)
        H.task_update(self.conn, ANA, noted["id"], status="done", note="Keep the draft on hold; do not publish it.")
        [told] = self.finished(noted, CMO)
        self.assertEqual(told["body"], "Ana Rivera marked this done: Pick a launch date\nKeep the draft on hold; do not publish it.")
        dropped = H.task_create(self.conn, CMO, "Pick a venue", "Choose one.", ANA)
        self.assertEqual(H.task_close(self.conn, ANA, dropped["id"])["status"], "closed")
        self.assertTrue(self.closed_notices(dropped, CMO), "the bot that asked is told it was closed")

    def test_moving_a_task_to_review_wakes_the_bot_that_asked_for_it(self):
        row = H.task_create(self.conn, CMO, "Speed up the overview checks", "Make them pass alone.", SEO)
        H.task_update(self.conn, SEO, row["id"], status="review", note="Pushed; all changed scripts pass.")
        told = [m for m in H.messages(self.conn, row["conversation_id"])
                if m["recipient_id"] == CMO and m["body"].startswith("Ready for review:")]
        self.assertEqual(len(told), 1)
        self.assertIn("all changed scripts pass", told[0]["body"])

    def test_a_task_with_a_worktree_moves_to_review_and_done_where_no_github_app_is_set_up(self):
        """No App table, so no branch Tico could check: the hand-off is not held waiting for one."""
        row = H.task_create(self.conn, CMO, "Trim the overview", "x", SEO)
        self.conn.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,repo,branch,updated_at,"
                          "detail) VALUES('w1',?,'worktree','worktree:w1','acme/site','present','bot:seo',?,"
                          "'acme/site',?,?,'{}')", (row["id"], H.now(), f"tico/{row['id'][:8]}-trim", H.now()))
        self.assertEqual(H.task_update(self.conn, SEO, row["id"], status="review")["status"], "review")
        self.assertEqual(H.task_update(self.conn, SEO, row["id"], status="done")["status"], "done")

    def finished(self, row, to):
        return [m for m in H.messages(self.conn, row["conversation_id"])
                if m["recipient_id"] == to and m["body"].startswith("Ana Rivera marked this done:")]

    def test_a_person_finishing_a_task_tells_every_bot_on_it_once(self):
        from backend.store import SCHEMA
        # Superseding its ask card exercises the server's versioned unblock path.
        self.conn.execute("ALTER TABLE tasks ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        self.conn.execute(re.search(r"CREATE TABLE IF NOT EXISTS bot_config\(.*?\);", SCHEMA, re.S).group(0))
        self.conn.execute("ALTER TABLE bot_config ADD COLUMN access TEXT")
        self.conn.execute("ALTER TABLE bot_config ADD COLUMN onboarding_state TEXT")
        self.conn.execute("INSERT INTO bot_config(bot,config,owner_id) VALUES('cmo',?, 'ana')",
                          (json.dumps({"bot_contact": "tasks"}),))
        parent = H.task_create(self.conn, CMO, "Plan the launch week", "All of it.", ANALYTICS)
        row = H.task_create(self.conn, CMO, "Pick the hero image", "Two drafts.", SEO, parent_id=parent["id"])
        H.task_update(self.conn, SEO, row["id"], status="waiting", waiting_on="ana", note="Pick one image.")
        H.task_comment(self.conn, COO, row["id"], "Which image?", ask={"questions": [
            {"id": "pick", "header": "", "question": "Which image?", "options": [{"label": "A"}, {"label": "B"}],
             "multi": False, "other": True}], "who": ANA})
        H.task_update(self.conn, ANA, row["id"], status="done", note="Use B.")
        for bot in (CMO, SEO, COO):
            told = self.finished(row, bot)
            self.assertEqual(len(told), 1, bot)
            self.assertIn("Use B.", told[0]["body"])
        self.assertFalse(self.finished(row, ANA))
        # The parent's bot hears it once, on the parent, where its session continues.
        self.assertFalse(self.finished(row, ANALYTICS))
        [on_parent] = [m for m in H.messages(self.conn, parent["conversation_id"])
                       if m["recipient_id"] == ANALYTICS and "Ana Rivera marked this subtask done: Pick the hero image" in m["body"]]
        self.assertIn("Use B.", on_parent["body"])
        quiet = {m["recipient_id"]: bool(m["refs"].get("quiet")) for m in H.messages(self.conn, row["conversation_id"])
                 if m["body"].startswith("Ana Rivera marked this done:")}
        self.assertEqual(quiet, {CMO: True, SEO: False, COO: False}, "tasks-only bots hear it quietly")
        # A private task tells only its parties: the parent's owner cannot read it.
        secret = H.task_create(self.conn, ANA, "Review the settlement", "Read it.", SEO, parent_id=parent["id"], private=True)
        H.task_update(self.conn, ANA, secret["id"], status="done", note="Signed.")
        self.assertEqual(len(self.finished(secret, SEO)), 1)
        self.assertFalse(self.finished(secret, ANALYTICS))

    def test_a_person_finishing_a_bots_task_with_nothing_said_does_not_wake_it(self):
        row = H.task_create(self.conn, ANA, "Ship the overview", "x", SEO)
        H.task_update(self.conn, ANA, row["id"], status="done")
        [told] = self.finished(row, SEO)
        self.assertTrue(told["refs"].get("quiet"), "no run for a finished task with no note")

    def test_a_person_closing_work_not_started_with_nothing_said_does_not_wake_its_bot(self):
        quiet, noted = (H.task_create(self.conn, ANA, title, "x", SEO) for title in ("Old idea", "Other idea"))
        H.task_close(self.conn, ANA, quiet["id"])
        H.task_close(self.conn, ANA, noted["id"], note="Do the other one instead.")
        refs = {row["id"]: [m["refs"] for m in H.messages(self.conn, row["conversation_id"])
                            if m["recipient_id"] == SEO and m["body"].startswith("Closed:")] for row in (quiet, noted)}
        self.assertTrue(refs[quiet["id"]][0].get("quiet"), "a close with nothing said is news, not work")
        self.assertFalse(refs[noted["id"]][0].get("quiet"), "a person's close note can change what the bot does next")
        notes = self.conn.execute("SELECT body FROM notes WHERE to_actor=?", (SEO,)).fetchall()
        self.assertEqual(len(notes), 1)
        self.assertIn("1 task", notes[0][0])

    def test_an_answer_goes_back_to_the_bot_that_asked_and_clears_the_wait(self):
        row = H.task_create(self.conn, CMO, "Pick the hero image", "Two drafts.", SEO)
        H.task_update(self.conn, SEO, row["id"], status="waiting", waiting_on="ana", note="Pick one image.")
        asked = H.task_comment(self.conn, CMO, row["id"], "Which image?", ask={"questions": [
            {"id": "pick", "header": "", "question": "Which image?", "options": [{"label": "A"}, {"label": "B"}],
             "multi": False, "other": True}], "who": ANA})
        said = H.task_comment(self.conn, ANA, row["id"], "Ana answered: B.", answer_to=asked["id"],
                              extra_refs={"answer": {"target": {"comment": asked["id"]},
                                                     "answers": {"pick": ["B"]}, "by": ANA, "at": H.now()}})
        self.assertEqual((said["kind"], said["recipient_id"]), ("answer", CMO))
        self.assertIsNone(H.task(self.conn, row["id"])["waiting_on"])
        self.assertFalse(said["refs"].get("quiet"), "it wakes the asker")

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

    @staticmethod
    def publish(text, digest=None):
        return {"url": "hq-ticket:TK-0000AAAA", "text": text,
                "content_sha256": digest or hashlib.sha256(text.encode("utf-8")).hexdigest()}

    def test_a_publish_text_that_matches_its_hash_is_requested(self):
        body = self.publish("Thanks, the fix is in 0.3.41.")
        self.assertEqual(H.approval_request(self.conn, CMO, "publish", body)["kind"], "publish")
        self.assertEqual(H.refusals_for(self.conn, CMO), [])

    def test_an_upper_case_publish_hash_is_refused_because_the_publisher_compares_it_exactly(self):
        upper = self.publish("Upgrade to 0.3.41 and it works.")
        upper["content_sha256"] = upper["content_sha256"].upper()
        e = self.refused("lint", H.approval_request, self.conn, CMO, "publish", upper)
        self.assertEqual("content_sha256 must be 64 lower-case hex characters", e.detail)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)

    def test_a_publish_hash_that_is_not_lower_case_hex_is_refused_without_text_too(self):
        digest = hashlib.sha256(b"Upgrade to 0.3.41 and it works.").hexdigest()
        for given in (digest.upper(), digest[:63], " " + digest, "z" * 64, 42):
            e = self.refused("lint", H.approval_request, self.conn, CMO, "publish",
                             {"url": "hq-ticket:TK-0000AAAA", "content_sha256": given})
            self.assertEqual("content_sha256 must be 64 lower-case hex characters", e.detail)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)
        self.assertEqual(H.approval_request(self.conn, CMO, "publish",
                                            {"url": "hq-ticket:TK-0000AAAA", "content_sha256": digest})["kind"], "publish")

    def test_a_publish_url_with_a_user_name_is_refused_so_a_cut_card_cannot_hide_its_host(self):
        digest = hashlib.sha256(b"Launch notes").hexdigest()
        for url in ("https://acme.example" + "a" * 400 + "@evil.example/post", "https://ana@evil.example",
                    "https://acme.example:pw@evil.example/", "//acme.example@evil.example", "https://acme.example\\@evil.example",
                    "https://acme.example\t@evil.example", "HTTPS://acme.example%2F@evil.example", "https://[::1@evil.example",
                    # A browser skips any run of / and \ after a web scheme, or none, and a leading pair leaves the app.
                    "https:\\\\acme.example@evil.example", "https:/acme.example@evil.example", "https:acme.example@evil.example",
                    "https:///acme.example@evil.example", "https:\\/acme.example@evil.example", "HtTp:acme.example@evil.example",
                    "ws:acme.example@evil.example", "\\\\acme.example@evil.example", "/\\acme.example@evil.example"):
            e = self.refused("lint", H.approval_request, self.conn, CMO, "publish", {"url": url, "content_sha256": digest})
            self.assertEqual("url must not carry a user name (user@host); give the plain address", e.detail)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)
        # An @ in the path, query or fragment names nobody's account on the host.
        for url in ("https://medium.example/@acme/launch", "https://acme.example/share?to=ana@acme.example",
                    "https://acme.example/#@team", "hq-ticket:TK-0000AAAA", "mailto:ana@acme.example",
                    "/team@acme/notes"):
            self.assertEqual(H.approval_request(self.conn, CMO, "publish", {"url": url, "content_sha256": digest})["kind"],
                             "publish")

    def test_a_publish_text_that_is_not_a_string_is_refused_as_such(self):
        digest = hashlib.sha256(b"Thanks, the fix is in 0.3.41.").hexdigest()
        for text in (None, 42, ["Thanks"]):
            e = self.refused("lint", H.approval_request, self.conn, CMO, "publish",
                             {"url": "hq-ticket:TK-0000AAAA", "content_sha256": digest, "text": text})
            self.assertEqual("text must be a string", e.detail)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)

    def test_a_publish_text_that_does_not_match_its_hash_is_refused(self):
        other = hashlib.sha256(b"A different reply that would really be posted.").hexdigest()
        e = self.refused("lint", H.approval_request, self.conn, CMO, "publish",
                         self.publish("Thanks, the fix is in 0.3.41.", other))
        self.assertIn("the text doesn't match content_sha256; send the exact text that will be published", e.detail)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)

    def test_a_publish_with_no_text_is_not_checked(self):
        appr = H.approval_request(self.conn, CMO, "publish", {"url": "https://acme.example/notes",
                                                               "content_sha256": "b" * 64})
        self.assertIsNone(appr["decision"])

    def test_a_unicode_reply_with_a_trailing_newline_matches_the_file_bytes_hq_tickets_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reply.md"
            path.write_bytes("Merci, Zoë: café ✓ fixed in 0.3.41 \u2014 see you.\r\nLine two.\n".encode("utf-8"))
            data = path.read_bytes()                    # what hq-tickets hashes
            sent = json.loads(json.dumps({"url": "hq-ticket:TK-0000AAAA", "text": data.decode("utf-8"),
                                          "content_sha256": hashlib.sha256(data).hexdigest()}))
        self.assertTrue(sent["text"].endswith("\n"))
        H.approval_request(self.conn, CMO, "publish", sent)
        self.assertEqual(H.refusals_for(self.conn, CMO), [])


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

    def test_an_answer_in_a_chat_a_new_task_notice_opened_is_not_unsolicited(self):
        chat = H.open_conversation(self.conn, ANA, [ANA, CMO], kind="chat")["id"]
        H.task_create(self.conn, ANA, "Draft the report", "Write a report", CMO, lint=False, conversation_id=chat)
        H.say(self.conn, ANA, CMO, "What is 12 times 7?", conversation_id=chat)
        first = self.conn.execute("SELECT sender_id FROM messages WHERE conversation_id=? ORDER BY created_at LIMIT 1",
                                  (chat,)).fetchone()[0]
        self.assertEqual(first, H.KEEPER, "the task's notice opened the chat")
        reply = H.say(self.conn, CMO, ANA, "84.", conversation_id=chat)
        self.assertNotIn("unsolicited", reply["refs"])

    def test_a_bot_writing_in_a_chat_it_opened_is_unsolicited_even_after_a_new_task_notice(self):
        chat = H.open_conversation(self.conn, CMO, [CMO, ANA], kind="chat")["id"]
        H.task_create(self.conn, ANA, "Draft the report", "Write a report", CMO, lint=False, conversation_id=chat)
        senders = [r[0] for r in self.conn.execute("SELECT sender_id FROM messages WHERE conversation_id=?", (chat,))]
        self.assertEqual(senders, [H.KEEPER], "only the task's notice is in the chat")
        sent = H.say(self.conn, CMO, ANA, "Pick the launch date, or tell me to hold.", conversation_id=chat)
        self.assertTrue(sent["refs"].get("unsolicited"), "the bot opened it, so the person asked nothing")

# ----------------------------------------------------------------------------- rule 8
class Rule8Counting(HubCase):
    def setUp(self):
        super().setUp()
        # Rule 8's review tasks go to BotOps; without it every review is refused and none is ever counted.
        self.conn.execute("INSERT INTO bots(id, slug, display_name, state) VALUES(?,?,?, 'active')", (H.FLEET_MAINTAINER, H.FLEET_MAINTAINER, "BotOps"))

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
        self.conn.execute("UPDATE events SET created_at=? WHERE action='quarantine'", (H.shift(H.now(), seconds=-3700),))
        self.assertEqual(H.lift_cooled_quarantines(self.conn), ["cmo"])

    def test_a_quarantine_from_before_the_repeat_rule_still_cools_off(self):
        H.quarantine(self.conn, "cmo", "10 refused writes today")
        self.assertFalse(H.quarantine_is_escape(self.conn, "cmo"))

    def reviewed(self, slug):
        """Review tasks opened today for `slug`'s refusals. BotOps receives them, so it must exist."""
        self.assertTrue(H.bot(self.conn, H.FLEET_MAINTAINER), "no reviewer: a review count would be vacuous")
        return self.conn.execute("SELECT COUNT(*) FROM tasks WHERE title=?",
                                 (f"Review {slug}'s refused writes",)).fetchone()[0]

    def test_writes_to_a_quarantined_bot_are_kept_or_refused_without_quarantining_the_sender(self):
        handoff = H.task_create(self.conn, SEO, "Review the landing page", "Check the copy", CMO)
        self.refuse_reach(H.QUARANTINE_AT)
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "quarantined")
        self.assertTrue(H.say(self.conn, ANA, CMO, "are you stuck?"), "a person's message is kept for when it is back")
        for i in range(4):
            # A bot's message or comment is kept too: runners claim only active bots' work.
            self.assertTrue(H.say(self.conn, SEO, CMO, f"review {i} is posted"))
            self.assertTrue(H.task_comment(self.conn, SEO, handoff["id"], f"finding {i}"))
            # A new task for it is still refused, as the target's state, not as the sender reaching too far.
            got = self.refused("target_inactive", H.task_create, self.conn, SEO, "Check the pricing page", "Read it", CMO)
            self.assertIn("until about", got.detail)
            self.assertIn("is not a repeat", got.detail)
        self.assertEqual(H.bot(self.conn, "seo")["state"], "active", "one quarantine does not spread to its partners")
        self.assertEqual(self.reviewed("seo"), 0)
        self.assertIn("is kept", H.held_note(self.conn, CMO))
        self.assertIsNone(H.held_note(self.conn, SEO))

    def refuse_inactive(self, n):
        for _ in range(n):
            self.refused("target_inactive", H.task_create, self.conn, CMO, "Plan the week", "Draft it", "coach")

    def test_target_state_refusals_neither_advance_nor_delay_real_ones(self):
        self.conn.execute("UPDATE bots SET state='archived' WHERE slug='legal'")
        # Only a bot that exists but is not running is the target's state; a gone or unknown one is still reach.
        self.refused("target_inactive", H.say, self.conn, SEO, H.bot_actor("game"), "ready?")     # planned
        self.refused("reach", H.say, self.conn, SEO, H.bot_actor("legal"), "ready?")              # archived
        self.refused("reach", H.say, self.conn, SEO, "nobody@example.com", "ready?")              # not on the roster
        def real(n):            # the same refused write n times: the first is recorded, each after it a repeat
            for _ in range(n):
                self.refused("reach", H.say, self.conn, CMO, "outsider@example.com", "hello")
        self.refuse_inactive(12)
        real(1)
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        self.assertEqual(self.reviewed("cmo"), 0, "one real refusal and many target ones open no review")
        real(H.REVIEW_AT - 1)
        self.assertEqual(self.reviewed("cmo"), 1, "the real refusals alone open the review at the same count")
        real(H.QUARANTINE_AT - H.REVIEW_AT)
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active", "nine repeats: not yet")
        real(1)
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "quarantined", "the tenth repeat, as before")
        self.assertEqual(H.quarantine_reason(self.conn, "cmo"), f"{H.QUARANTINE_AT} repeated refusals today")

    def test_target_state_refusals_count_toward_the_backstop_of_a_real_one(self):
        self.refuse_inactive(H.QUARANTINE_TOTAL_AT - 1)
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        self.refused("reach", H.say, self.conn, CMO, "new-outsider@example.com", "hello")
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "quarantined")
        self.assertEqual(H.quarantine_reason(self.conn, "cmo"), f"{H.QUARANTINE_TOTAL_AT} refusals today")

    def test_retrying_a_write_to_a_paused_bot_meets_only_the_day_backstop(self):
        for i in range(H.QUARANTINE_TOTAL_AT - 1):
            got = self.refused("target_inactive", H.task_create, self.conn, SEO, "Plan the week", "Draft it", "coach")
        self.assertIn("until a person resumes it", got.detail)
        self.assertEqual(H.bot(self.conn, "seo")["state"], "active", "the same words every time are no repeat")
        self.assertEqual(self.reviewed("seo"), 0, "the target's state opens no review of the sender")
        self.refused("target_inactive", H.task_create, self.conn, SEO, "Plan the week", "Draft it", "coach")
        self.assertEqual(H.bot(self.conn, "seo")["state"], "quarantined", "a retry loop still stops")
        self.assertEqual(H.quarantine_reason(self.conn, "seo"), f"{H.QUARANTINE_TOTAL_AT} refusals today")

    def test_an_escape_quarantine_holds_until_a_human_clears_it(self):
        H.quarantine(self.conn, "cmo", "escape: the task note reaches outside the hub: secrets/mail.env")
        self.assertEqual(H.status(self.conn, "cmo")["state"], "quarantined")
        focus = H.status(self.conn, "cmo")["focus"]
        H.status_set(self.conn, H.KEEPER, "cmo", state="crashed", focus="Runner disconnected")
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
        self.conn.execute("UPDATE events SET created_at=? WHERE action='quarantine'", (H.shift(H.now(), seconds=-3700),))
        self.assertEqual(H.lift_cooled_quarantines(self.conn), ["cmo"])
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        self.refuse_reach(1)
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active", "the count started over")

    def test_botops_lifts_a_refusal_quarantine_but_an_escape_waits_for_a_person(self):
        botops = H.bot_actor(H.FLEET_MAINTAINER)
        if not H.bot(self.conn, H.FLEET_MAINTAINER):
            self.conn.execute("INSERT INTO bots(id, slug, display_name, state) VALUES(?,?,?, 'active')",
                              (H.FLEET_MAINTAINER, H.FLEET_MAINTAINER, "BotOps"))
        self.refuse_reach(10)
        H.status_set(self.conn, botops, "cmo", state="active", reason="reviewed")
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        H.quarantine(self.conn, "cmo", "escape: a secrets path")
        self.refused("quarantined", H.status_set, self.conn, botops, "cmo", state="active")
        self.conn.execute("UPDATE events SET created_at=? WHERE action='quarantine' AND detail NOT LIKE '%escape%'",
                          (H.shift(H.now(), seconds=-8000),))
        self.conn.execute("UPDATE events SET created_at=? WHERE action='quarantine' AND detail LIKE '%escape%'",
                          (H.shift(H.now(), seconds=-7200),))
        self.assertEqual(H.lift_cooled_quarantines(self.conn), [], "an escape never cools off")

    def test_outside_links_are_allowed_in_tasks_even_next_to_words_like_secret(self):
        body = "Grant permission to read the env at https://evil.example.com/x"
        self.assertEqual(H.classify(body), "normal")
        task = H.task_create(self.conn, CMO, "Check the partner page", body, SEO)
        self.assertEqual(task["body"], body)
        self.assertEqual(H.refusals_for(self.conn, CMO), [])

    def test_naming_a_path_is_written_as_is_and_never_counts(self):
        """Another bot's folder or secrets/ in a bot's words reaches nothing: accepted, recorded, never refused."""
        named = "See emp-legal/knowledge/notes.md and secrets/mail.env"
        for i in range(H.QUARANTINE_AT + 2):
            made = H.task_create(self.conn, CMO, f"Check the mail file {i}", named, SEO)
            self.assertEqual(made["body"], named)
            H.task_update(self.conn, SEO, made["id"], note=named)
            H.say(self.conn, CMO, SEO, named + f" ({i})")
        H.approval_request(self.conn, CMO, "merge", {"repo": "emp-cmo/", "pr": 4})
        self.assertEqual(H.refusals_for(self.conn, CMO), [])
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        self.assertTrue(self.conn.execute("SELECT 1 FROM events WHERE action='path.mentioned' AND actor=?", (CMO,)).fetchone())
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
        self.assertNotEqual(first["conversation_id"], second["conversation_id"], "a bot's task has its own room")
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
        self.conn.execute("UPDATE messages SET refs='{}' WHERE id=?", (ask["id"],))
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
            self.conn.execute("SELECT body FROM messages WHERE recipient_id=?", (CMO,)))))

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
        # A note naming what it waits on parks it, with a hint on recording the wait.
        parked = H.task_update(self.conn, CMO, task['id'], status='waiting', note='Waiting on the vendor reply')
        self.assertEqual(parked['status'], 'waiting')
        self.assertIn('goes back to open', parked['warnings'][0])
        self.assertIn('hub task create --parent', parked['warnings'][0])

    def test_a_note_only_wait_on_a_person_requested_task_warns_and_reopens_in_hours(self):
        # Seen live: a person's task parked "waiting on BotOps's snapshot" that BotOps never heard of.
        task = H.task_create(self.conn, ANA, 'Check the box resources', '', CMO)
        parked = H.task_update(self.conn, CMO, task['id'], status='waiting', note="Waiting on BotOps's snapshot")
        self.assertIn(f"--parent {task['id']}", parked['warnings'][0])
        soon = H.shift(H.now(), hours=H.STRANDED_GRACE_HOURS - 1)
        self.assertEqual(H.sweep_stranded(self.conn, at=soon), [])
        later = H.shift(H.now(), hours=H.STRANDED_GRACE_HOURS + 1)
        self.assertEqual(H.sweep_stranded(self.conn, at=later), [(task['id'], 'open')])
        # A due date says when to look again; a subtask is something to wait on.
        other = H.task_create(self.conn, ANA, 'Check the crawl', '', CMO)
        H.task_update(self.conn, CMO, other['id'], status='waiting', note='Waiting on the crawl',
                      due=H.shift(H.now(), hours=48))
        sub = H.task_create(self.conn, ANA, 'Check the disk', '', CMO)
        H.task_create(self.conn, CMO, 'Send the disk snapshot', '', SEO, parent_id=sub['id'])
        H.task_update(self.conn, CMO, sub['id'], status='waiting', note='Waiting on the snapshot')
        self.assertEqual(H.sweep_stranded(self.conn, at=later), [])

    def test_a_bot_marks_its_task_done_with_subtasks_still_open(self):
        parent = H.task_create(self.conn, ANA, 'Launch the pricing page', '', CMO)
        child = H.task_create(self.conn, CMO, 'Approve the final copy', 'Say yes or change it.', ANA,
                              parent_id=parent['id'])
        done = H.task_update(self.conn, CMO, parent['id'], status='done', note='Page is live')
        self.assertEqual(done['status'], 'done')
        self.assertEqual(H.task(self.conn, child['id'])['status'], 'open', 'the subtask stays open')
        said = [e['new'] for e in H._rows(self.conn.execute(
            "SELECT new FROM task_events WHERE task_id=? AND field='children'", (parent['id'],)))]
        self.assertEqual(said, ['Still open: Approve the final copy'])

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

    def test_a_bots_ask_to_a_person_is_one_small_subtask_and_its_task_stays_the_bots_until_they_answer(self):
        H.status_set(self.conn, H.KEEPER, "cmo", state="idle")
        needs = lambda: H.status(self.conn, "cmo")["needs_human"]
        ask = "Restart the build host? It refuses SSH, and I'll rerun the deploy after."
        task = H.task_create(self.conn, CMO, 'Fix the build host', 'The deploy fails at the SSH step.', CMO, private=False)
        after = H.task_update(self.conn, CMO, task['id'], status='waiting', waiting_on='ana', note=ask)
        # The task stays the bot's, blocked, and is not itself a card for Ana.
        self.assertEqual((after['owner_id'], after['status'], after['waiting_on'], after['note']),
                         (CMO, 'waiting', None, f'Blocked on Ana Rivera: {ask}'))
        card = H.task(self.conn, after['asked_in']['id'])
        self.assertEqual((card['owner_id'], card['requester_id'], card['title'], card['body'], card['status']),
                         (ANA, CMO, 'Restart the build host?', ask, 'open'))
        self.assertEqual((TR.parent_of(self.conn, card['id']), TR.blocker_ids(self.conn, task['id'])),
                         (task['id'], [card['id']]))
        mine = H.needs_you(self.conn, 'ana')
        self.assertEqual(([t['id'] for t in mine['tasks']], mine['waiting']), ([card['id']], []))
        self.assertEqual(needs(), 1)
        self.assertEqual(H.waiting_for(self.conn, H.task(self.conn, task['id'])), 'an open child task')
        self.assertEqual(H.sweep_stranded(self.conn, at=H.shift(H.now(), hours=48)), [])
        # Asking again, as the note reads now or as first said, makes no second card.
        for note in (None, ask):
            H.task_update(self.conn, CMO, task['id'], status='waiting', waiting_on='ana', note=note)
        self.assertEqual(TR.child_ids(self.conn, task['id']), [card['id']])
        self.assertEqual(H.task_comments(self.conn, card['id'], actor=ANA), [])
        # Ana says yes: the bot hears it once, on its task, which opens again with her answer. No second ask.
        H.task_update(self.conn, ANA, card['id'], status='done', note='Yes. Restarted it.')
        back = H.task(self.conn, task['id'])
        self.assertEqual((back['status'], back['note'], TR.blocker_ids(self.conn, task['id'])),
                         ('open', 'Ana Rivera answered: Yes. Restarted it.', []))
        told = [m['body'] for m in H.messages(self.conn, task['conversation_id']) if m['recipient_id'] == CMO
                and ('Restarted it' in m['body'] or m['body'].startswith('Unblocked'))]
        self.assertEqual(len(told), 1, told)
        self.assertEqual((needs(), H.needs_you(self.conn, 'ana')['tasks']), (0, []))
        # No is an answer too.
        other = H.task_create(self.conn, CMO, 'Buy a signing key', '', CMO, private=False)
        card = H.task_update(self.conn, CMO, other['id'], status='waiting', waiting_on='ana',
                             note='Buy a signing key for $5? If yes, I will order it today.')['asked_in']
        H.task_update(self.conn, ANA, card['id'], status='declined', note='No. Use the old one.')
        self.assertEqual((H.task(self.conn, other['id'])['status'], H.task(self.conn, other['id'])['note'],
                          TR.blocker_ids(self.conn, other['id'])), ('open', 'Ana Rivera answered: No. Use the old one.', []))
        # Closing the card settles it too: the task opens with her words, and its bot hears it once.
        moved = H.task_create(self.conn, CMO, 'Set up the new bots', '', CMO, private=False)
        card = H.task_update(self.conn, CMO, moved['id'], status='waiting', waiting_on='ana',
                             note='Start setup for the new bots? If yes, I will finish it today.')['asked_in']
        H.task_close(self.conn, ANA, card['id'], note='Replaced by a setup task I filed myself.')
        back = H.task(self.conn, moved['id'])
        self.assertEqual((back['status'], back['note'], TR.blocker_ids(self.conn, moved['id'])),
                         ('open', 'Ana Rivera closed the ask: Replaced by a setup task I filed myself.', []))
        told = [m['body'] for m in H.messages(self.conn, back['conversation_id']) if m['recipient_id'] == CMO
                and ('Replaced by' in m['body'] or m['body'].startswith('Unblocked'))]
        self.assertEqual(len(told), 1, told)
        # A question to a person counts too, once per task, until it is answered.
        asked = H.task_create(self.conn, ANA, 'Draft the launch post', '', CMO)
        question = H.task_ask(self.conn, CMO, asked['id'], 'Which date?')
        self.assertEqual(needs(), 1)
        H.answer(self.conn, ANA, question['id'], 'Friday')
        self.assertEqual(needs(), 0)
        # A bot's private task to itself reaches the one person it asks, its guest (Dev 172), and no second one.
        secret = H.task_create(self.conn, CMO, 'Review the payroll export', '', CMO, private=True)
        H.task_update(self.conn, CMO, secret['id'], status='waiting', waiting_on='ben',
                      note='Approve the payroll export? If yes, I will send it.')
        self.assertEqual(H.task(self.conn, secret['id'])['guest'], H.human_actor('ben'))
        self.refused('private', H.task_update, self.conn, CMO, secret['id'], status='waiting', waiting_on='ana',
                     note='Approve the payroll export? If yes, I will send it.')
        # A person's private task still stays with its two parties.
        theirs = H.task_create(self.conn, ANA, 'Review my payroll export', '', CMO, private=True)
        self.refused('private', H.task_update, self.conn, CMO, theirs['id'], status='waiting', waiting_on='ben',
                     note='Approve the payroll export? If yes, I will send it.')

    def test_one_question_one_card_when_bots_chain_on_the_same_person(self):
        ask = "Run the vendor trial once with the key you sent? If yes, I'll add the results to the report."
        root = H.task_create(self.conn, CMO, 'Assess the vendor trial', '', CMO, private=False)
        # The bot hands another bot part of the work, which then needs Ana for the same thing.
        part = H.task_create(self.conn, CMO, 'Store the vendor key', 'Store it for the test.', SEO,
                             parent_id=root['id'])
        card = H.task_update(self.conn, SEO, part['id'], status='waiting', waiting_on='ana', note=ask)['asked_in']
        # The first bot asking her the same: no second card, its task is blocked on hers.
        again = H.task_update(self.conn, CMO, root['id'], status='waiting', waiting_on='ana', note=ask)
        self.assertEqual(again['asked_in']['id'], card['id'])
        self.assertIn(card['id'], TR.blocker_ids(self.conn, root['id']))
        cards = lambda: [t['id'] for t in H.needs_you(self.conn, 'ana')['tasks']]
        self.assertEqual((cards(), H.needs_you(self.conn, 'ana')['waiting']), ([card['id']], []))
        self.assertEqual(H.task_comments(self.conn, card['id'], actor=ANA), [])
        # A different ask joins the same card.
        H.task_update(self.conn, CMO, root['id'], status='waiting', waiting_on='ana',
                      note='Can I share the results with the vendor? If yes, I will send them the report.')
        self.assertEqual(cards(), [card['id']])
        self.assertEqual([m['body'] for m in H.task_comments(self.conn, card['id'], actor=ANA)],
                         ['Can I share the results with the vendor? If yes, I will send them the report.'])
        # One answer reaches both bots once, each on its own task, and both carry on.
        H.task_update(self.conn, ANA, card['id'], status='done', note='Yes to both.')
        self.assertEqual((H.task(self.conn, part['id'])['status'], H.task(self.conn, root['id'])['status']),
                         ('open', 'waiting'))
        for row, bot in ((part, SEO), (root, CMO)):
            back = H.task(self.conn, row['id'])
            self.assertEqual(back['note'], 'Ana Rivera answered: Yes to both.')
            woken = [m for m in H.messages(self.conn, back['conversation_id'])
                     if m['recipient_id'] == bot and 'Yes to both.' in m['body']]
            self.assertEqual(len(woken), 1, bot)
        # The root still has its own open subtask (the other bot's part), so it stays waiting on that.

    def test_an_ask_joins_only_a_card_it_made_never_across_privacy_and_answers_by_the_link(self):
        cards = lambda: [t['id'] for t in H.needs_you(self.conn, 'ana')['tasks']]
        root = H.task_create(self.conn, CMO, 'Plan the offsite', '', CMO, private=False)
        part = H.task_create(self.conn, CMO, 'Book the rooms', 'Book them.', SEO, parent_id=root['id'])
        public = H.task_update(self.conn, SEO, part['id'], status='waiting', waiting_on='ana',
                               note='Approve the venue for Friday? If yes, I will book it.')['asked_in']
        # A private task's ask never lands on the public card: it gets its own private one.
        secret = H.task_create(self.conn, ANA, 'Pay the offsite staff', '', CMO, parent_id=root['id'], private=True)
        hidden = H.task_update(self.conn, CMO, secret['id'], status='waiting', waiting_on='ana',
                               note='Send the staff pay file? If yes, I will pay them today.')['asked_in']
        self.assertNotEqual(hidden['id'], public['id'])
        self.assertTrue(H.task_private(self.conn, H.task(self.conn, hidden['id'])))
        self.assertEqual(H.task_comments(self.conn, public['id'], actor=ANA), [])
        # The private task's own card is still one card: a re-sent wait and a second ask both join it.
        again = H.task_update(self.conn, CMO, secret['id'], status='waiting', waiting_on='ana',
                              note=H.task(self.conn, secret['id'])['note'])['asked_in']
        more = H.task_update(self.conn, CMO, secret['id'], status='waiting', waiting_on='ana',
                             note='Also send the tax form? If yes, I will file it with the pay.')['asked_in']
        self.assertEqual((again['id'], more['id'], TR.blocker_ids(self.conn, secret['id'])),
                         (hidden['id'], hidden['id'], [hidden['id']]))
        # Another task given to the person is not a card to join, and finishing it answers nothing.
        other = H.task_create(self.conn, CMO, 'Move the office', '', CMO, private=False)
        lease = H.task_create(self.conn, CMO, 'Sign the lease', 'Sign the lease by Friday? I will file it after.', ANA,
                              parent_id=other['id'])
        card = H.task_update(self.conn, CMO, other['id'], status='waiting', waiting_on='ana',
                             note='Approve the movers for Monday? If yes, I will book them.')['asked_in']
        self.assertNotEqual(card['id'], lease['id'])
        H.task_update(self.conn, ANA, lease['id'], status='done', note='Signed.')
        self.assertTrue(H.task(self.conn, other['id'])['note'].startswith('Blocked on Ana Rivera: Approve the movers'))
        # A progress note while waiting does not lose the answer, and the bot is woken once.
        H.task_update(self.conn, CMO, other['id'], note='Still waiting; checked prices meanwhile.')
        H.task_update(self.conn, ANA, card['id'], status='done', note='Yes.')
        back = H.task(self.conn, other['id'])
        self.assertEqual((back['status'], back['note']), ('open', 'Ana Rivera answered: Yes.'))
        woken = [m for m in H.messages(self.conn, back['conversation_id']) if m['recipient_id'] == CMO
                 and ('Yes.' in m['body'] or m['body'].startswith('Unblocked'))]
        self.assertEqual(len(woken), 1, [m['body'] for m in woken])
        # The bot's own question under its task is the card: no second one, and the note names the person.
        trial = H.task_create(self.conn, CMO, 'Assess the vendor trial', '', CMO, private=False)
        question = H.task_create(self.conn, CMO, 'Run the trial once?', 'Run the trial once?', CMO, parent_id=trial['id'])
        H.task_comment(self.conn, CMO, question['id'], 'Run the trial once?', ask={'questions': [
            {'id': 'decision', 'header': '', 'question': 'Run the trial once?', 'options': [{'label': 'Yes'}, {'label': 'No'}],
             'multi': False, 'other': False}], 'who': ANA})
        H.task_update(self.conn, CMO, question['id'], status='waiting', waiting_on='ana')
        before = cards()
        after = H.task_update(self.conn, CMO, trial['id'], status='waiting', waiting_on='ana',
                              note='Run the trial once? If yes, I will add the results to the report.')
        self.assertEqual((after['asked_in']['id'], after['note'], cards()),
                         (question['id'], 'Blocked on Ana Rivera: Run the trial once? If yes, I will add the results to the report.', before))
        H.task_update(self.conn, CMO, question['id'], status='done', note='Ana said yes.')
        self.assertEqual((H.task(self.conn, trial['id'])['status'], H.task(self.conn, trial['id'])['note']),
                         ('open', f"{H.person_name(self.conn, CMO)} closed the ask to Ana Rivera: Ana said yes."))

    def test_bot_tasks_already_waiting_on_a_person_move_to_the_subtask_shape(self):
        from backend import person_asks
        plain = "Approve the venue for Friday? If yes, I'll book it."
        moved = H.task_create(self.conn, CMO, 'Book the venue', 'All the venue notes.', CMO, private=False)
        unclear = H.task_create(self.conn, CMO, 'Renew the domain', '', CMO, private=False)
        for row, note in ((moved, plain), (unclear, 'Blocked on db14c907')):
            self.conn.execute("UPDATE tasks SET status='waiting', waiting_on=?, note=? WHERE id=?", (ANA, note, row['id']))
        self.assertEqual(person_asks.migrate(self.conn), [moved['id']])
        self.assertEqual(person_asks.migrate(self.conn), [], 'moved once')
        after = H.task(self.conn, moved['id'])
        self.assertEqual((after['owner_id'], after['status'], after['waiting_on'], after['note']),
                         (CMO, 'waiting', None, f'Blocked on Ana Rivera: {plain}'))
        [card] = [H.task(self.conn, t) for t in TR.child_ids(self.conn, moved['id'])]
        self.assertEqual((card['owner_id'], card['title'], card['body']), (ANA, 'Approve the venue for Friday?', plain))
        # The one with no plain ask stays for its bot to restate (backend/restate_asks.py).
        self.assertEqual(H.task(self.conn, unclear['id'])['waiting_on'], ANA)

    def test_a_plain_ask_passes_and_shorthand_does_not(self):
        # Abbreviations, initials, a year after a few letters, a standard's number and a product name are plain words.
        for ask in ["Can we meet at 3 p.m. on Friday? If yes, I will book the room.",
                    "Approve the invoice from Dr. Smith for $1,200? If yes, I pay it today.",
                    "Should we sell to U.S. customers first? If yes, I set up the store.",
                    "Approve the FY2026 budget? If yes, I send it to finance.",
                    "Move the site to ES2022? If yes, I ship it Monday.",
                    "Start the ISO27001 audit? If yes, I book the auditor.",
                    "Build the site with Node.js? If yes, I start today.",
                    "Approve the trial (it ends Oct. 20)? If yes, I sign up.",
                    "Fix the error in the invoice? If yes, I send it again."]:
            self.assertEqual(H.lint_person_ask(ask), [], ask)
        for ask, failed in [("Approve it. I send it. Then I book it.", "3 sentences"),
                            ("Approve PO-1234? If yes, I pay it.", 'id or hash: "PO-1234"'),
                            ("Approve invoice #4821?", 'id or hash: "#4821"'),
                            ("Approve invoice #4821.", 'id or hash: "#4821"'),
                            ("Ship the fix in app.js?", 'file path: "app.js"'),
                            # An error class name is the bot's record even with no message after it.
                            ("Retry the import? It stopped on a TypeError.", 'error output: "TypeError"')]:
            self.assertIn(failed, "; ".join(H.lint_person_ask(ask)), ask)

    def test_a_bot_gives_a_person_a_plain_ask_or_is_refused(self):
        plain = "Approve the video tool trial on Thursday? If yes, I'll send the invite."
        task = H.task_create(self.conn, CMO, 'Coordinate the external agent scenario', '', CMO, private=False)
        # No ask, or the bot's shorthand as the ask, is refused even with the style warnings off, saying what
        # failed, the rule and an example. Retrying it never quarantines the bot: it is a writing fix.
        with unittest.mock.patch.object(H, 'STYLE_LINT', 'off'):
            for note, failed in [(None, 'is missing'), ('Waiting on Ana re db14c907 / reports/2026-10-14-trial.md',
                                                        'has an id or hash: "db14c907"; has a file path')]:
                for _ in range(H.QUARANTINE_AT + 2):
                    with self.assertRaises(H.Refused) as refused:
                        H.task_update(self.conn, CMO, task['id'], status='waiting', waiting_on='ana', note=note)
                self.assertEqual(refused.exception.rule, 'lint')
                self.assertIn(f'Your ask to ana {failed}', refused.exception.detail)
                self.assertIn(f'Example: "{H.LINT_ASK_EXAMPLE}"', refused.exception.detail)
                self.assertIn('s3:// links, tool names or error output', refused.exception.detail)
        self.assertEqual(H.bot(self.conn, 'cmo')['state'], 'active')
        self.assertIsNone(H.task(self.conn, task['id'])['waiting_on'])
        after = H.task_update(self.conn, CMO, task['id'], status='waiting', waiting_on='ana', note=plain)
        self.assertEqual((after['waiting_on'], after['note'], after.get('warnings')),
                         (None, f'Blocked on Ana Rivera: {plain}', None))
        self.assertEqual(H.task(self.conn, after['asked_in']['id'])['body'], plain)
        # An open question to them on the task already asks (`hub task question`): no note is needed.
        asked = H.task_create(self.conn, ANA, 'Draft the launch post', '', CMO, private=False)
        H.task_ask(self.conn, CMO, asked['id'], 'Which date should it go out?')
        self.assertEqual(H.task_update(self.conn, CMO, asked['id'], status='waiting', waiting_on='ana')['waiting_on'], ANA)
        # A task given to a person opens its body with the ask; the rest of the body is details.
        with self.assertRaises(H.Refused) as refused:
            H.task_create(self.conn, CMO, 'Approve the video tool trial', 'Approve the video tool trial\n\nSee reports/x.md', ANA)
        self.assertIn('first paragraph of the task body', refused.exception.detail)
        made = H.task_create(self.conn, CMO, 'Approve the video tool trial', f'{plain}\n\nThe notes: reports/x.md', ANA)
        self.assertEqual(made['owner_id'], ANA)

    # The refused escalations of 2026-10-26: a pull request named in the ask, and a 71-word ask in four sentences.
    BLOCKER = "Approve the merge of PR #1555 so QA can test it? If yes, I will run the tests today."
    LONG_BLOCKER = ("The hosted checks for the Settings change failed three times today on the same browser test, "
                    "and I cannot tell yet whether the runner or the change is at fault. "
                    "Approve the merge of PR #1555 so QA can test it on the release candidate? "
                    "If yes, I will run the tests today and post the results. "
                    "If no, I will keep it parked and ask DevOps to check the runner.")

    def test_a_person_ask_may_name_a_pull_request_and_nothing_else(self):
        # "PR #<number>" is public and a person can look it up; it stays in the ask as written.
        for ask in [self.BLOCKER, "Merge PR #1555.", "Review PR #12 and PR #1555 today? If yes, I merge both."]:
            self.assertEqual(H.lint_person_ask(ask), [], ask)
        self.assertEqual(H.task_update(self.conn, CMO, H.task_create(self.conn, CMO, 'Merge the fix', '', CMO,
                                                                     private=False)['id'],
                                       status='waiting', waiting_on='ana', note=self.BLOCKER)['note'],
                         f'Blocked on Ana Rivera: {self.BLOCKER}')
        # Any other form is a reference number, as is a hash, a private task id, a path, a tool name or error output,
        # even beside a pull request; all of it goes under Details.
        for ask, failed in [("Approve the merge of #1555 so QA can test it?", '#1555'), ("Merge #1555.", '#1555'),
                            ("Merge PR#1555?", '#1555'), ("Merge pr #1555?", '#1555'), ("Merge PR #1555/files?", '#1555'),
                            ("Merge PR #1555 at 3f2a9c1d?", 'id or hash: "3f2a9c1d"'),
                            ("Merge PR #1555 for 8719e1a6-3f2a-4c1d-9e0b-5a6b7c8d9e0f?",
                             'id or hash: "8719e1a6-3f2a-4c1d-9e0b-5a6b7c8d9e0f"'),
                            ("Merge PR #1555 with backend/hubdb.py?", 'file path: "backend/hubdb.py"'),
                            ("Merge PR #1555 with hub task update?", 'tool or field name: "hub task"'),
                            ("Merge PR #1555? It failed with ValueError: bad date.", 'error output: "ValueError"')]:
            self.assertIn(failed, "; ".join(H.lint_person_ask(ask)), ask)
        made = H.task_create(self.conn, CMO, 'Approve the merge', f'{self.BLOCKER}\n\nDetails: head 3f2a9c1d, '
                             'backend/hubdb.py, ValueError: bad date.', ANA)
        self.assertEqual(made['owner_id'], ANA)

    def test_a_too_long_ask_is_refused_with_a_rewrite_the_bot_retries_once(self):
        task = H.task_create(self.conn, CMO, 'Get the Settings change tested', '', CMO, private=False)
        before = H.task(self.conn, task['id'])
        with self.assertRaises(H.Refused) as refused:
            H.task_update(self.conn, CMO, task['id'], status='waiting', waiting_on='ana', note=self.LONG_BLOCKER)
        detail = refused.exception.detail
        self.assertEqual(refused.exception.rule, 'lint')
        self.assertIn('Your ask to ana is 71 words in 4 sentences. Retry once with this ask', detail)
        rewrite = re.search(r'what happens after: "(.+?)" Put the rest of your text in a comment on the task', detail)[1]
        # The rewrite keeps the request, the pull request and what happens after, and passes on its own.
        self.assertEqual(rewrite, "Approve the merge of PR #1555 so QA can test it on the release candidate? "
                                  "If yes, I will run the tests today and post the results.")
        self.assertEqual(H.lint_person_ask(rewrite), [])
        self.assertEqual(H.task(self.conn, task['id'])['version'], before['version'], 'nothing written on refusal')
        # The one retry with it reaches the person; the bot's context goes in a comment.
        H.task_comment(self.conn, CMO, task['id'], self.LONG_BLOCKER)
        after = H.task_update(self.conn, CMO, task['id'], status='waiting', waiting_on='ana', note=rewrite)
        self.assertEqual((after['note'], H.task(self.conn, after['asked_in']['id'])['body']),
                         (f'Blocked on Ana Rivera: {rewrite}', rewrite))
        self.assertEqual(H.bot(self.conn, 'cmo')['state'], 'active')
        # A task given to a person: the rewrite opens the body and the original text stays below it.
        with self.assertRaises(H.Refused) as refused:
            H.task_create(self.conn, CMO, 'Approve the merge', self.LONG_BLOCKER, ANA)
        self.assertIn(f'"{rewrite}" Put the rest of your text in the body under it, after a blank line',
                      refused.exception.detail)
        made = H.task_create(self.conn, CMO, 'Approve the merge', f'{rewrite}\n\n{self.LONG_BLOCKER}', ANA)
        self.assertEqual((made['owner_id'], H.task(self.conn, made['id'])['body']),
                         (ANA, f'{rewrite}\n\n{self.LONG_BLOCKER}'))
        # A single sentence over the limit only by its context keeps the request; one with no outcome keeps it alone.
        self.assertEqual(H.person_ask_rewrite("Merge PR #1555. " + "The runner was slow all day. " * 8),
                         ("Merge PR #1555.", ""))
        # What happens after is the sentence that answers the request, not context that only opens like one.
        self.assertEqual(H.person_ask_rewrite("Approve the merge of PR #1555? Then the runner failed three times. "
                                              "If yes, I will run the tests today."),
                         ("Approve the merge of PR #1555? If yes, I will run the tests today.", ""))
        # A negative branch is context, not the outcome of granting the request.
        self.assertEqual(H.person_ask_rewrite("Approve PR #1555 so QA can test it? If no, I will keep it parked. "
                                              "The checks failed three times today and we still need to understand "
                                              "what caused that. If yes, I will run the tests today."),
                         ("Approve PR #1555 so QA can test it? If yes, I will run the tests today.", ""))
        # An outcome that lists the bot's next actions is one outcome, not more asks.
        context = "The vendor needs our answer today because the pricing offer ends tomorrow morning. " * 3
        for outcome in ("If yes, I'll sign it, send it and close this task.",
                        "If approved, I'll merge it, then deploy to production.",
                        "Then I'll book the venue, send invites and order food."):
            self.assertEqual(H.person_ask_rewrite(f"Approve the $5,000 budget for Acme? {context}{outcome}"),
                             (f"Approve the $5,000 budget for Acme? {outcome}", ""))

    def test_negative_conditions_do_not_replace_the_affirmative_outcome(self):
        request = "Approve PR #1555 so QA can test it?"
        outcome = "If yes, I will run the tests today."
        context = "The checks failed three times today and we still need to understand what caused that."
        for negative in ("If you do not approve, I will keep it parked.",
                         "If you agree not to merge, I will close it.",
                         "If you don't approve, I will keep it parked."):
            text = f"{request} {negative} {context} {outcome}"
            self.assertTrue(H.lint_person_ask(text))
            rewrite, repair = H.person_ask_rewrite(text)
            self.assertEqual((rewrite, repair), (f"{request} {outcome}", ""))
            self.assertEqual(H.lint_person_ask(rewrite), [])

    def test_shortening_keeps_indirect_and_conditional_requests(self):
        task = H.task_create(self.conn, CMO, 'Get the decisions', 'All the original details.', CMO, private=False)
        asks = [
            "Can you check the vendor numbers before Friday? I need you to approve the $5,000 budget for the Acme "
            "contract as well. The vendor needs our answer today because the pricing offer ends tomorrow morning. "
            "If yes, I'll sign it.",
            "Approve PR #1555 so QA can test it? Once approved, please also sign the vendor contract before Friday. "
            "The vendor needs our answer today because the pricing offer ends tomorrow morning. "
            "If yes, I will run the tests today.",
            "Approve PR #1555 so QA can test it? Once approved, sign the vendor contract before Friday. "
            "The vendor needs our answer today because the pricing offer ends tomorrow morning. "
            "If yes, I will run the tests today.",
            "Approve PR #1555 so QA can test it? If approved, I'll merge it; sign the vendor contract too. "
            "The vendor needs our answer today because the pricing offer ends tomorrow morning. "
            "If yes, I will run the tests today.",
            "Approve PR #1555 so QA can test it? And sign the vendor contract before Friday. "
            "The vendor needs our answer today because the pricing offer ends tomorrow morning. "
            "If yes, I will run the tests today.",
        ]
        before = H.task(self.conn, task['id'])
        for ask in asks:
            with self.assertRaises(H.Refused) as refused:
                H.task_update(self.conn, CMO, task['id'], status='waiting', waiting_on='ana', note=ask)
            self.assertEqual(refused.exception.rule, 'lint')
            self.assertIn('it asks 2 things', refused.exception.detail)
            self.assertIn('ask any other separately', refused.exception.detail)
            self.assertNotIn('Retry once with this ask', refused.exception.detail)
            after = H.task(self.conn, task['id'])
            self.assertEqual((after['version'], after['body'], after['status'], after['waiting_on']),
                             (before['version'], before['body'], before['status'], before['waiting_on']))
            self.assertEqual(TR.child_ids(self.conn, task['id']), [])
        # A sole indirect request is preserved verbatim rather than disappearing as background context.
        self.assertEqual(H.person_ask_rewrite("The vendor needs our answer today. I need you to approve the $5,000 "
                                              "budget for Acme. If yes, I'll sign the contract. "
                                              "The pricing offer ends tomorrow morning."),
                         ("I need you to approve the $5,000 budget for Acme. If yes, I'll sign the contract.", ""))

    def test_an_ask_no_shorter_wording_keeps_gets_a_repair_and_nothing_is_sent(self):
        task = H.task_create(self.conn, CMO, 'Get the Settings change tested', 'All the runner notes.', CMO,
                             private=False)
        context = " The runner failed three times today on the same browser test, and nobody knows why yet."
        for ask, repair in [
                # Two decisions: picking one would change what the person is asked.
                ("Approve the merge of PR #1555? Or should I close it and open a new one?" + context,
                 'it asks 2 things ("Approve the merge of PR #1555?"; "Or should I close it and open a new one?")'),
                # A question and a request are two decisions too: keeping only the question drops the merge.
                ("Can you approve the merge today? Approve PR #1555 so QA can test it. The hosted checks failed three "
                 "times today on the same browser test and I cannot tell yet whether the runner or the change is at "
                 "fault.",
                 'it asks 2 things ("Can you approve the merge today?"; "Approve PR #1555 so QA can test it.")'),
                ("The runner is slow." + context + context, 'none of its sentences asks for anything'),
                ("Approve the merge of PR #1555 " + "and the follow-up fixes " * 9 + "today?" + context,
                 'its request alone is 43 words: shorten'),
                ("Approve the merge of 3f2a9c1d today? If yes, I run the tests." + context,
                 'its request "Approve the merge of 3f2a9c1d today? If yes, I run the tests." has an id or hash')]:
            with self.assertRaises(H.Refused) as refused:
                H.task_update(self.conn, CMO, task['id'], status='waiting', waiting_on='ana', note=ask)
            self.assertIn('No shorter wording keeps the same decision, so nothing was sent and the task is unchanged. '
                          f'To fix it, {repair}', refused.exception.detail)
            self.assertIn('then retry once', refused.exception.detail)
            self.assertNotIn('Retry once with this ask', refused.exception.detail)
            after = H.task(self.conn, task['id'])
            self.assertEqual((after['status'], after['waiting_on'], after['body'], TR.child_ids(self.conn, task['id'])),
                             ('open', None, 'All the runner notes.', []))
        # The refusals are recorded, so the blocker is never invisible, and none counts toward a quarantine.
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM refusals WHERE actor=? AND rule='lint'",
                                           (CMO,)).fetchone()[0], 5)
        self.assertEqual(H.bot(self.conn, 'cmo')['state'], 'active')

    def test_a_waiting_note_flags_ids_and_hashes_not_amounts_dates_or_words(self):
        flagged = lambda note: [p for p in H.lint_waiting_note(note, ANA) if p.startswith('id in the note')]
        sha = '3f2a9c1d8e7b6a5f4e3d2c1b0a9f8e7d6c5b4a39'
        for note, ident in [('Approve the fix in db14c907.', 'db14c907'), (f'Approve the fix in {sha}.', sha),
                            ('Approve the fix in 3f2a9c1.', '3f2a9c1'), ('Review #388 before Friday.', '#388'),
                            ('Answer 8719e1a6-3f2a-4c1d-9e0b-5a6b7c8d9e0f.', '8719e1a6-3f2a-4c1d-9e0b-5a6b7c8d9e0f'),
                            ('Merge #1555.', '#1555'), ('Merge PR#1555 today.', '#1555'),
                            ('Read #1555/files today.', '#1555')]:
            self.assertEqual(flagged(note), [f'id in the note for ana: "{ident}" - say what it is in plain words'], note)
        for note in ['Approve the $250000 budget.', 'Approve the £1500000 offer.', 'Approve the €250000 and ¥9000000 quotes.',
                     'Name the deadbeef test host.', 'Pick a date after 2026-10-11 for the launch.',
                     'Approve upgrading to 0.3.40 and Python 3.12.', 'Tell me if the defaced page is back up.',
                     'Merge PR #1555.']:
            self.assertEqual(flagged(note), [], note)
        # A currency sign spares only the amount after it, in a note only: a title keeps the shared check.
        self.assertTrue(flagged('Approve the $250000 budget for 250000 seats.'))
        self.assertTrue(H.lint_title('Approve the $250000 budget'))
        self.assertEqual(H.lint_title('Fix the db14c907 crash'), [], 'lowercase hex is a waiting-note check, not a title one')
        # A colon that opens a plain clause is prose; a status code is still jargon.
        self.assertEqual(H.lint_waiting_note('Check the order status: it says pending.', ANA), [])
        self.assertEqual(H.lint_waiting_note('Approve the release; status: doing.', ANA),
                         ['internal jargon in the note for ana: "status: doing."'])
        self.assertEqual(H.lint_human_item('Approve the release\n\nowner: bot:cmo'), ['internal codes: "owner: bot:cmo"'])
        self.assertEqual(H.lint_human_item('Approve the release\n\nThe owner: the CMO, who signs it.'), [])

    def test_only_the_owner_names_who_it_waits_on_and_the_wait_is_counted_once(self):
        H.status_set(self.conn, H.KEEPER, "cmo", state="idle")
        H.status_set(self.conn, H.KEEPER, "seo", state="idle")
        stored = lambda bot: self.conn.execute("SELECT needs_human FROM bot_status WHERE bot=?", (bot,)).fetchone()[0]
        task = H.task_create(self.conn, ANA, 'Fix the build host', '', CMO, private=False)
        wait = lambda **kw: H.task_update(self.conn, CMO, task['id'], status='waiting', waiting_on='ana',
                                          note='Restart it', **kw)
        self.refused('identity', H.task_update, self.conn, ANA, task['id'], status='waiting', waiting_on='ana')
        self.refused('kind', wait, owner='seo')
        self.refused('private', H.task_update, self.conn, CMO, task['id'], status='waiting', waiting_on='ben',
                     private=True)
        # A task that waits on a person itself (set before asks became subtasks, or one with an open question to
        # them): a new note is detail beneath the wait and keeps it (backend/task_holds.py); a new owner clears it.
        legacy = lambda: self.conn.execute("UPDATE tasks SET status='waiting', waiting_on=? WHERE id=?", (ANA, task['id']))
        legacy()
        self.assertEqual(H.task_update(self.conn, CMO, task['id'], note='Still down')['waiting_on'], ANA)
        legacy()
        self.assertIsNone(H.task_update(self.conn, CMO, task['id'], owner='seo')['waiting_on'])
        self.assertEqual((stored('cmo'), stored('seo')), (0, 0), "the handoff recounts the old owner too")
        # Made private: the wait no longer goes in front of a person who cannot read it.
        own = H.task_create(self.conn, CMO, 'Rotate the deploy key', '', CMO, private=False)
        self.conn.execute("UPDATE tasks SET status='waiting', waiting_on=? WHERE id=?", ('human:ben', own['id']))
        self.assertIsNone(H.task_update(self.conn, CMO, own['id'], private=True)['waiting_on'])
        # An approval on a task that waits on a person is the same wait.
        key = H.task_create(self.conn, CMO, 'Buy a signing key', '', CMO, private=False)
        H.task_update(self.conn, CMO, key['id'], status='waiting', waiting_on='ana',
                      note='Approve the purchase? If yes, I will order the key.')
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


# ----------------------------------------------------------------------------- the Strategy Bot's old names, dropped
class StrategyOldNamesDropped(HubCase):
    """Installs from before v0.5.0 renamed the Strategy Bot's tables (ceo_* -> strategy_*) and kept the old names as
    mirrors of the new ones. Migration 0006 drops the mirrors and their triggers; the new tables keep
    every row."""

    def install_as_v050_left_it(self):
        """The old-named twins and the triggers that mirrored writes to them, as the v0.5.0 schema had them."""
        self.before("0006")
        for old, new in (("ceo_actions", "strategy_actions"), ("ceo_merges", "strategy_merges")):
            self.conn.execute(f"CREATE TABLE {old} AS SELECT * FROM {new} WHERE 0")
            self.conn.execute(f"INSERT INTO {old} SELECT * FROM {new}")
            for event in ("insert", "update", "delete"):
                self.conn.execute(f"CREATE TRIGGER {new}_to_old_{event} AFTER {event.upper()} ON {new} "
                                  f"BEGIN SELECT 1; END")
        self.conn.commit()

    def names(self, kind="table"):
        return {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))}

    def rows(self, table, columns="id, state"):
        return sorted(tuple(r) for r in self.conn.execute(f"SELECT {columns} FROM {table}"))

    def act(self, ident, state):
        self.conn.execute("INSERT INTO strategy_actions(id,created_at,actor,kind,state) VALUES(?,?,?,'level',?)",
                          (ident, H.now(), "bot:goal-manager", state))

    def test_the_upgrade_drops_the_old_names_and_their_triggers_and_keeps_the_rows(self):
        self.act("a1", "done")
        self.conn.execute("INSERT INTO strategy_merges(merged_into,original,person,asker,created_at) "
                          "VALUES('t1','t2','human:ana','bot:coo',?)", (H.now(),))
        self.install_as_v050_left_it()
        self.conn.commit()
        migrations.apply(self.conn)
        self.assertFalse({"ceo_actions", "ceo_merges"} & self.names())
        self.assertFalse({f"{t}_to_old_{e}" for t in ("strategy_actions", "strategy_merges")
                          for e in ("insert", "update", "delete")} & self.names("trigger"))
        self.assertEqual(self.rows("strategy_actions"), [("a1", "done")])
        self.assertEqual(self.rows("strategy_merges", "merged_into, original"), [("t1", "t2")])
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], len(H.MIGRATIONS))

    def test_writes_after_the_upgrade_go_to_the_new_tables_without_mirrors(self):
        self.install_as_v050_left_it()
        self.conn.commit()
        migrations.apply(self.conn)
        self.act("a2", "done")
        self.conn.execute("UPDATE strategy_actions SET state='undone' WHERE id='a2'")
        self.assertEqual(self.rows("strategy_actions"), [("a2", "undone")])

    def test_a_second_start_changes_nothing(self):
        self.act("a1", "done")
        self.install_as_v050_left_it()
        self.conn.commit()
        migrations.apply(self.conn)
        migrations.apply(self.conn)
        self.assertEqual(self.rows("strategy_actions"), [("a1", "done")])
        self.assertFalse({"ceo_actions", "ceo_merges"} & self.names())


class WalLeftForLitestream(unittest.TestCase):
    def test_the_last_close_keeps_the_wal_so_litestream_can_resume(self):
        # Litestream resumes from its place in the WAL; a plain connection's last close would fold and delete it.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hub.db"
            conn = H.connect(path)
            conn.execute("CREATE TABLE probe(x)")
            conn.execute("INSERT INTO probe VALUES (1)")
            conn.close()
            self.assertTrue(Path(f"{path}-wal").exists())
            reader = sqlite3.connect(path)
            self.assertEqual(reader.execute("SELECT count(*) FROM probe").fetchone()[0], 1)
            reader.close()
            # The control: without the flag the same last close deletes the WAL.
            plain = Path(directory) / "plain.db"
            control = sqlite3.connect(plain, isolation_level=None)
            control.execute("PRAGMA journal_mode=WAL")
            control.execute("CREATE TABLE probe(x)")
            control.close()
            self.assertFalse(Path(f"{plain}-wal").exists())


class MarketNotesMoved(HubCase):
    """Installs from before the market pages had their own table kept them in `documents`, the repository mirror's
    table, with the mirror's versions beside it. Migration 0008 copies the market notes into
    market_pages and drops both tables. The mirror's other rows were never shown anywhere, so they go too."""

    def install_as_before_it_moved(self):
        self.before("0008")
        self.conn.execute("DROP TABLE IF EXISTS market_pages")
        self.conn.execute("CREATE TABLE documents(id TEXT PRIMARY KEY, visibility TEXT NOT NULL, collection TEXT NOT NULL, "
                          "payload_json TEXT NOT NULL, digest TEXT NOT NULL, updated TEXT NOT NULL)")
        self.conn.execute("CREATE TABLE document_versions(id TEXT NOT NULL, digest TEXT NOT NULL, "
                          "payload_json TEXT NOT NULL, imported_at TEXT NOT NULL, PRIMARY KEY(id,digest))")
        overview = {"id": "market/overview", "title": "Overview", "content": "The market is small.", "category": "Market",
                    "collection": "market", "search": "The market is small.", "format": "markdown",
                    "owner_id": MK.CURATOR, "fetched": "2026-10-01T00:00:00.000000Z", "seeded": True}
        raw = json.dumps(overview)
        self.conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",
                          ("market/overview", "external", "market", raw, "d1", "2026-10-01T00:00:00.000000Z"))
        self.conn.execute("INSERT INTO document_versions VALUES(?,?,?,?)",
                          ("market/overview", "d1", raw, "2026-10-01T00:00:00.000000Z"))
        self.conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",
                          ("repo/readme.md", "internal", "repo", json.dumps({"title": "Readme", "content": "x"}),
                           "d2", "2026-10-01T00:00:00.000000Z"))
        self.conn.commit()

    def test_the_upgrade_copies_the_market_notes_and_drops_the_mirror_tables(self):
        self.install_as_before_it_moved()
        migrations.apply(self.conn)
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("documents", names)
        self.assertNotIn("document_versions", names)
        self.assertEqual([tuple(r) for r in self.conn.execute(
            "SELECT id, title, category, body, seeded, updated_at FROM market_pages")],
            [("market/overview", "Overview", "Market", "The market is small.", 1, "2026-10-01T00:00:00.000000Z")])

    def test_the_pages_still_read_list_and_write_after_the_upgrade(self):
        self.install_as_before_it_moved()
        migrations.apply(self.conn)
        page = MK.read_page(self.conn, "market/overview")
        self.assertEqual((page["content"], page["seeded"], page["collection"]),
                         ("The market is small.", True, "market"))
        self.assertIsNone(MK.read_page(self.conn, "repo/readme.md"))
        MK.write_page(self.conn, MK.SEED_ACTOR, "market/theses", "Theses", "Our bet.", "Market", record=False)
        rewritten = MK.write_page(self.conn, MK.SEED_ACTOR, "market/overview", "Overview", "Rewritten.", "Market",
                                  replace=True, record=False)
        self.assertEqual([p["id"] for p in MK.list_pages(self.conn)], ["market/overview", "market/theses"])
        self.assertEqual((rewritten["content"], rewritten["seeded"]), ("Rewritten.", True))   # the seed's own write
        self.assertEqual(MK.read_page(self.conn, "market/theses")["content"], "Our bet.")


class TurnLegacyUsageDropped(HubCase):
    """Runs from before measured usage kept a one-number tokens_in and tokens_out, and a cost, on their turn row. The
    migration 0009 moves those into input_tokens, output_tokens and est_cost_usd where those are empty, and
    drops the three columns. A run with measured usage keeps its own numbers."""

    def install_as_before_measured_usage(self):
        self.before("0009")
        for column, kind in (("tokens_in", "INTEGER"), ("tokens_out", "INTEGER"), ("cost", "REAL")):
            self.conn.execute(f"ALTER TABLE turns ADD COLUMN {column} {kind}")
        now = H.now()
        self.conn.execute("INSERT INTO turns(id,bot,started_at,finished_at,exit,tokens_in,tokens_out,cost) "
                          "VALUES('old-run','ops',?,?,'completed',500,5,0.75)", (now, now))
        self.conn.execute("INSERT INTO turns(id,bot,started_at,finished_at,exit,tokens_in,tokens_out,cost,"
                          "input_tokens,cached_tokens,output_tokens,est_cost_usd) "
                          "VALUES('measured','ops',?,?,'completed',999,77,9,100,50,10,0.2)", (now, now))
        self.conn.execute("INSERT INTO turns(id,bot,started_at,finished_at,exit,cost) "
                          "VALUES('cost-only','ops',?,?,'completed',9)", (now, now))
        self.conn.commit()

    def test_the_upgrade_moves_the_legacy_tokens_and_cost_and_drops_the_columns(self):
        self.install_as_before_measured_usage()
        migrations.apply(self.conn)
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(turns)")}
        self.assertFalse({"tokens_in", "tokens_out", "cost"} & columns)
        self.assertEqual([tuple(r) for r in self.conn.execute(
            "SELECT id, input_tokens, cached_tokens, output_tokens, est_cost_usd FROM turns ORDER BY id")],
            [("cost-only", None, None, None, 9.0), ("measured", 100, 50, 10, 0.2), ("old-run", 500, None, 5, 0.75)])

    def test_the_run_totals_read_the_moved_numbers(self):
        self.install_as_before_measured_usage()
        migrations.apply(self.conn)
        totals = {r[0]: r[1] for r in self.conn.execute(f"SELECT t.id, {WP.RUN_TOKENS} FROM turns t")}
        self.assertEqual(totals, {"cost-only": 0, "measured": 160, "old-run": 505})
