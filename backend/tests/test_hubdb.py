"""hub.db's write layer: one passing and one refusing case for every rule in docs/history/hub-v2.md §4.

Every test runs against a fresh database in a temp directory with a small fake registry, so
nothing here reads `registry/` or the real `runtime/hub.db`.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend import hubdb as H
from backend import bot_settings as BS
from backend.tests.helpers.hubdb import ANA, CMO, EMPLOYEES, HubCase, PEOPLE, with_legacy_run_tables


SEO = H.bot_actor("seo")
COO = H.bot_actor("assistant")


# ----------------------------------------------------------------------------- schema, connect
class Schema(HubCase):
    def test_published_schema_upgrade_adds_run_rotation(self):
        # Main has already applied the purpose migration; rotation must be appended after it.
        published_version = H.MIGRATIONS.index(H.RUN_PURPOSE_SCHEMA) + 1
        with_legacy_run_tables(self.conn)
        self.conn.execute(
            "INSERT INTO turns(id,bot,started_at,purpose) VALUES('before-rotation','cmo',?,'review PR')", (H.now(),)
        )
        self.conn.execute(f"PRAGMA user_version={published_version}")
        self.conn.commit()
        H.migrate(self.conn)
        self.assertNotIn(
            "turns", {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        )
        self.assertIn("rotated", {r[1] for r in self.conn.execute("PRAGMA table_info(attempts)")})
        self.assertEqual(
            self.conn.execute("SELECT purpose FROM attempts WHERE id='before-rotation'").fetchone()[0], "review PR"
        )
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], len(H.MIGRATIONS))


class RegistrySync(HubCase):
    def test_sync_is_idempotent_and_keeps_quarantine(self):
        H.quarantine(self.conn, "seo", "test")
        H.sync_registry(self.conn, EMPLOYEES, PEOPLE)
        H.sync_registry(self.conn, EMPLOYEES, PEOPLE)
        self.assertEqual(len(H.bots(self.conn)), len(EMPLOYEES))
        self.assertEqual(len(H.humans(self.conn)), len(PEOPLE["people"]))
        self.assertEqual(H.bot(self.conn, "seo")["state"], "quarantined")


# ----------------------------------------------------------------------------- rule 1
class Rule1Identity(HubCase):
    def test_a_bot_may_not_write_another_bots_status(self):
        self.refused("identity", H.status_set, self.conn, CMO, "seo", state="blocked")


# ----------------------------------------------------------------------------- rule 2
class Rule2Reach(HubCase):
    def test_a_template_name_reaches_the_one_live_bot_built_from_it_and_nothing_when_two_are(self):
        BS.update(self.conn, "cmo", {"template": "growth-manager"})  # a template's `--owner growth-manager`
        task = H.task_create(self.conn, SEO, "Review the landing page", "Check the copy", "growth-manager")
        self.assertEqual(H.task(self.conn, task["id"])["owner_id"], CMO)

        # A bot's slug or a person's id names that one first, whatever a template calls it.
        BS.update(self.conn, "seo", {"template": "assistant"})
        self.assertEqual(H.resolve_actor(self.conn, "assistant"), COO)
        self.assertEqual(H.resolve_actor(self.conn, "growth-manager"), CMO)

        # Two live bots built from the template: the name still reaches nobody.
        BS.update(self.conn, "seo", {"template": "growth-manager"})
        self.assertIsNone(H.resolve_actor(self.conn, "growth-manager"))
        self.refused(
            "reach", H.task_create, self.conn, SEO, "Review the landing page", "Check the copy", "growth-manager"
        )

        # An archived bot is off the roster, so the one live bot is the only match again.
        self.conn.execute("UPDATE bots SET state='archived' WHERE slug='seo'")
        self.assertEqual(H.resolve_actor(self.conn, "growth-manager"), CMO)

        # An open seat set up from the template claims the name too, until a bot fills it (the seat closes).
        now = H.now()
        self.conn.execute(
            "INSERT INTO seats(id,title,reports_to,created_at,created_by,updated_at,updated_by,template) "
            "VALUES('seat-marketing-lead','Marketing lead','',?,?,?,?,'growth-manager')",
            (now, CMO, now, CMO),
        )
        self.assertIsNone(H.resolve_actor(self.conn, "growth-manager"))
        self.conn.execute("UPDATE seats SET closed_at=? WHERE id='seat-marketing-lead'", (now,))
        self.assertEqual(H.resolve_actor(self.conn, "growth-manager"), CMO)

        # A template no bot is built from is still no one.
        self.assertIsNone(H.resolve_actor(self.conn, "product-lead"))


# ----------------------------------------------------------------------------- rule 3
class Rule3Caps(HubCase):
    def test_twenty_messages_an_hour_pass_and_the_twenty_first_is_capped(self):
        conv = H.open_conversation(self.conn, CMO, [CMO, SEO], subject="the blog")
        for i in range(H.CAP_PER_HOUR):
            H.say(self.conn, CMO, SEO, f"line {i}", conversation_id=conv["id"])
        self.refused("cap", H.say, self.conn, CMO, SEO, "one more", conversation_id=conv["id"])


# ----------------------------------------------------------------------------- rule 4


# ----------------------------------------------------------------------------- rule 5


# ----------------------------------------------------------------------------- rule 7
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
        first = self.conn.execute(
            "SELECT sender_id FROM messages WHERE conversation_id=? ORDER BY created_at LIMIT 1", (chat,)
        ).fetchone()[0]
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
        self.conn.execute(
            "INSERT INTO bots(id, slug, display_name, state) VALUES(?,?,?, 'active')",
            (H.FLEET_MAINTAINER, H.FLEET_MAINTAINER, "BotOps"),
        )

    def refuse_reach(self, n, actor=CMO):
        """`n` refusals that count: the same refused write made n + 1 times (the first is recorded, not counted)."""
        for _ in range(n + 1):
            with self.assertRaises(H.Refused):
                H.say(self.conn, actor, "outsider@example.com", "hello")

    def test_ten_different_first_refusals_do_not_quarantine(self):
        for i in range(H.QUARANTINE_AT + 2):
            with self.assertRaises(H.Refused):
                H.say(self.conn, CMO, f"outsider{i}@example.com", "hello")
        self.assertEqual(
            H.bot(self.conn, "cmo")["state"], "active", "a first try at something new only counts once repeated"
        )
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
        self.conn.execute(
            "UPDATE events SET created_at=? WHERE action='quarantine'", (H.shift(H.now(), seconds=-3700),)
        )
        self.assertEqual(H.lift_cooled_quarantines(self.conn), ["cmo"])

    def reviewed(self, slug):
        """Review tasks opened today for `slug`'s refusals. BotOps receives them, so it must exist."""
        self.assertTrue(H.bot(self.conn, H.FLEET_MAINTAINER), "no reviewer: a review count would be vacuous")
        name = H.bot(self.conn, slug)["display_name"]  # the title names the bot as people know it, not by its id
        return self.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE title=?", (f"Review {name}'s refused writes",)
        ).fetchone()[0]

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
            got = self.refused(
                "target_inactive", H.task_create, self.conn, SEO, "Check the pricing page", "Read it", CMO
            )
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
        self.refused("target_inactive", H.say, self.conn, SEO, H.bot_actor("game"), "ready?")  # planned
        self.refused("reach", H.say, self.conn, SEO, H.bot_actor("legal"), "ready?")  # archived
        self.refused("reach", H.say, self.conn, SEO, "nobody@example.com", "ready?")  # not on the roster

        def real(n):  # the same refused write n times: the first is recorded, each after it a repeat
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

    def test_a_person_refused_for_a_paused_bot_reads_when_it_is_back_not_how_bots_are_reviewed(self):
        got = self.refused("target_inactive", H.task_create, self.conn, ANA, "Plan the week", "Draft it", "coach")
        self.assertIn("until a person resumes it", got.detail)
        self.assertNotIn("review", got.detail)
        self.assertNotIn("refusals counts it", got.detail)
        bot = self.refused("target_inactive", H.task_create, self.conn, SEO, "Plan the week", "Draft it", "coach")
        self.assertIn("opens no review of you", bot.detail)

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
        self.conn.execute(
            "UPDATE events SET created_at=? WHERE action='quarantine'", (H.shift(H.now(), seconds=-3700),)
        )
        self.assertEqual(H.lift_cooled_quarantines(self.conn), ["cmo"])
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        self.refuse_reach(1)
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active", "the count started over")

    def test_botops_lifts_a_refusal_quarantine_but_an_escape_waits_for_a_person(self):
        botops = H.bot_actor(H.FLEET_MAINTAINER)
        if not H.bot(self.conn, H.FLEET_MAINTAINER):
            self.conn.execute(
                "INSERT INTO bots(id, slug, display_name, state) VALUES(?,?,?, 'active')",
                (H.FLEET_MAINTAINER, H.FLEET_MAINTAINER, "BotOps"),
            )
        self.refuse_reach(10)
        H.status_set(self.conn, botops, "cmo", state="active", reason="reviewed")
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        H.quarantine(self.conn, "cmo", "escape: a secrets path")
        self.refused("quarantined", H.status_set, self.conn, botops, "cmo", state="active")
        self.conn.execute(
            "UPDATE events SET created_at=? WHERE action='quarantine' AND detail NOT LIKE '%escape%'",
            (H.shift(H.now(), seconds=-8000),),
        )
        self.conn.execute(
            "UPDATE events SET created_at=? WHERE action='quarantine' AND detail LIKE '%escape%'",
            (H.shift(H.now(), seconds=-7200),),
        )
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
        self.assertEqual(H.refusals_for(self.conn, CMO), [])
        self.assertEqual(H.bot(self.conn, "cmo")["state"], "active")
        self.assertTrue(
            self.conn.execute("SELECT 1 FROM events WHERE action='path.mentioned' AND actor=?", (CMO,)).fetchone()
        )
        self.assertEqual(H.classify("{}", kind="spend"), "sensitive")


# ----------------------------------------------------------------------------- rule 9
# ----------------------------------------------------------------------------- status
# ----------------------------------------------------------------------------- reads and the rest
class PersonAskLint(HubCase):
    def test_a_plain_ask_passes_and_shorthand_does_not(self):
        # Abbreviations, initials, a year after a few letters, a standard's number and a product name are plain words.
        for ask in [
            "Can we meet at 3 p.m. on Friday? If yes, I will book the room.",
            "Approve the invoice from Dr. Smith for $1,200? If yes, I pay it today.",
            "Should we sell to U.S. customers first? If yes, I set up the store.",
            "Approve the FY2026 budget? If yes, I send it to finance.",
            "Move the site to ES2022? If yes, I ship it Monday.",
            "Start the ISO27001 audit? If yes, I book the auditor.",
            "Build the site with Node.js? If yes, I start today.",
            "Approve the trial (it ends Oct. 20)? If yes, I sign up.",
            "Fix the error in the invoice? If yes, I send it again.",
        ]:
            self.assertEqual(H.lint_person_ask(ask), [], ask)
        for ask, failed in [
            ("Approve it. I send it. Then I book it.", "3 sentences"),
            ("Approve PO-1234? If yes, I pay it.", 'id or hash: "PO-1234"'),
            ("Approve invoice #4821?", 'id or hash: "#4821"'),
            ("Approve invoice #4821.", 'id or hash: "#4821"'),
            ("Ship the fix in app.js?", 'file path: "app.js"'),
            # An error class name is the bot's record even with no message after it.
            ("Retry the import? It stopped on a TypeError.", 'error output: "TypeError"'),
        ]:
            self.assertIn(failed, "; ".join(H.lint_person_ask(ask)), ask)

    # The refused escalations of 2026-10-26: a pull request named in the ask, and a 71-word ask in four sentences.
    BLOCKER = "Approve the merge of PR #1555 so QA can test it? If yes, I will run the tests today."
    LONG_BLOCKER = (
        "The hosted checks for the Settings change failed three times today on the same browser test, "
        "and I cannot tell yet whether the runner or the change is at fault. "
        "Approve the merge of PR #1555 so QA can test it on the release candidate? "
        "If yes, I will run the tests today and post the results. "
        "If no, I will keep it parked and ask DevOps to check the runner."
    )

    def test_negative_conditions_do_not_replace_the_affirmative_outcome(self):
        request = "Approve PR #1555 so QA can test it?"
        outcome = "If yes, I will run the tests today."
        context = "The checks failed three times today and we still need to understand what caused that."
        for negative in (
            "If you do not approve, I will keep it parked.",
            "If you agree not to merge, I will close it.",
            "If you don't approve, I will keep it parked.",
        ):
            text = f"{request} {negative} {context} {outcome}"
            self.assertTrue(H.lint_person_ask(text))
            rewrite, repair = H.person_ask_rewrite(text)
            self.assertEqual((rewrite, repair), (f"{request} {outcome}", ""))
            self.assertEqual(H.lint_person_ask(rewrite), [])


# ----------------------------------------------------------------------------- the Strategy Owner's old names, dropped


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


def test_task_ref_sql_matches_a_scalar_or_a_list_element_and_no_other_shape():
    """One predicate for both shapes of a task ref: the id itself, or an array holding it. Any other shape never matches,
    and the same id is bound to both `?`."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE m(id INTEGER PRIMARY KEY, refs TEXT)")
    shapes = {
        1: {"task": "T"},
        2: {"task": ["X", "T"]},
        3: {"task": ["X"]},
        4: {"task": "X"},
        5: {"task": {"id": "T"}},
        6: {"task": []},
        7: {"task": [["T"]]},
        8: {"task_id": "T"},
        9: {},
    }
    conn.executemany("INSERT INTO m VALUES(?,?)", [(i, json.dumps(refs)) for i, refs in shapes.items()])
    sql = f"SELECT id FROM m WHERE {H.task_ref_sql('refs')} ORDER BY id"
    assert [row[0] for row in conn.execute(sql, ("T", "T"))] == [1, 2]
    by_task_id = f"SELECT id FROM m WHERE {H.task_ref_sql('refs', '$.task_id')} ORDER BY id"
    assert [row[0] for row in conn.execute(by_task_id, ("T", "T"))] == [8]
