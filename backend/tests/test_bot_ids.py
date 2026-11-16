"""Stable bot ids: `bots.id` is the identity, `slug` a renameable label."""

import sqlite3
import tempfile
import unittest
import unittest.mock
import uuid
from pathlib import Path

from backend import hubdb as H

EMPLOYEES = {
    "assistant": {"display_name": "COO", "status": "active", "host": "keeper", "runtime": "codex"},
    "cmo": {"display_name": "CMO", "status": "active", "host": "keeper", "runtime": "codex"},
}
PEOPLE = {"people": [{"id": "ana", "name": "Ana Rivera", "email": "ana@acme.example", "team": "leadership"}]}
ANA = H.human_actor("ana")

# The foreign keys into bots.
CHILD_TABLES = (
    "bot_config",
    "bot_control",
    "bot_transitions",
    "settings_changes",
    "bot_session_epochs",
    "bot_sessions",
    "agents",
    "assignments",
    "jobs",
    "mail_agent_instructions",
    "bot_agent_instructions",
    "bot_tool_requests",
    "bot_files",
    "chat_goals",
)


class _Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.conn = H.connect(Path(self.dir.name) / "hub.db")
        self.addCleanup(self.conn.close)
        H.sync_registry(self.conn, EMPLOYEES, PEOPLE)

    def make_bot(self, slug, bot_id=None):
        """A bot made the way the app makes one: its id from H.new_bot_id unless given."""
        bot_id = bot_id or H.new_bot_id(slug)
        self.conn.execute(
            "INSERT INTO bots (id, slug, display_name, state, created_at) VALUES (?, ?, ?, 'active', ?)",
            (bot_id, slug, slug.title(), H.now()),
        )
        return bot_id


class Schema(_Case):
    def test_a_new_database_has_the_final_shape(self):
        bots_sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='bots'").fetchone()[0]
        self.assertIn("id TEXT PRIMARY KEY NOT NULL", bots_sql)
        self.assertIn("slug TEXT NOT NULL UNIQUE", bots_sql)
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        for table in CHILD_TABLES:
            with self.subTest(table=table):
                targets = {
                    (r[2], (r[4] or "").lower())
                    for r in self.conn.execute(f'PRAGMA foreign_key_list("{table}")')
                    if r[2] == "bots"
                }
                self.assertEqual(targets, {("bots", "id")})

    def test_principals_table_rejects_an_unknown_kind(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO principals (id, kind) VALUES ('alien:x', 'alien')")

    def test_sync_registry_gives_a_registry_bot_its_slug_as_id(self):
        self.assertEqual(H.bot(self.conn, "cmo")["slug"], "cmo")
        self.assertEqual(H.resolve_actor(self.conn, "cmo"), "bot:cmo")


class Ids(_Case):
    def test_a_new_bot_gets_a_uuidv7_id_and_an_actor_on_that_id(self):
        bot_id = self.make_bot("ops-lead")
        parsed = uuid.UUID(bot_id)
        self.assertEqual(parsed.version, 7)
        self.assertEqual(parsed.variant, uuid.RFC_4122)
        self.assertEqual(H.bot_actor(bot_id), f"bot:{bot_id}")
        self.assertEqual(H.resolve_actor(self.conn, "ops-lead"), f"bot:{bot_id}")
        self.assertEqual(H.resolve_actor(self.conn, f"bot:{bot_id}"), f"bot:{bot_id}")

    def test_a_uuidv7_carries_its_creation_time_in_milliseconds(self):
        with unittest.mock.patch.object(H, "datetime") as clock:
            clock.now.return_value.timestamp.return_value = 2_000_000_000.25  # seconds since the Unix epoch
            early = H.uuid7()
            clock.now.return_value.timestamp.return_value = 2_000_000_001.5
            later = H.uuid7()
        self.assertEqual(uuid.UUID(early).version, 7)
        self.assertEqual(early.replace("-", "")[:12], format(2_000_000_000_250, "012x"))
        self.assertEqual(later.replace("-", "")[:12], format(2_000_000_001_500, "012x"))
        self.assertLess(early, later)  # ids sort by creation time

    def test_a_prefixed_slug_or_id_resolves_to_the_same_actor(self):
        bot_id = self.make_bot("ops-lead")
        self.assertEqual(H.resolve_actor(self.conn, "bot:ops-lead"), f"bot:{bot_id}")
        self.assertEqual(H.resolve_actor(self.conn, f"bot:{bot_id}"), f"bot:{bot_id}")
        self.assertIsNone(H.resolve_actor(self.conn, "bot:nobody"))

    def test_a_product_bot_keeps_its_slug_as_id(self):
        self.assertEqual(H.new_bot_id("botops"), "botops")
        self.assertEqual(H.new_bot_id("rules-owner"), "rules-owner")

    def test_resolve_bot_takes_an_id_or_a_slug_and_only_one_bot(self):
        bot_id = self.make_bot("ops-lead")
        by_id = H.resolve_bot(self.conn, bot_id)
        self.assertEqual(by_id["slug"], "ops-lead")
        self.assertEqual(H.resolve_bot(self.conn, "ops-lead")["id"], bot_id)
        self.assertEqual(H.resolve_bot(self.conn, f"bot:{bot_id}")["id"], bot_id)
        self.assertIsNone(H.resolve_bot(self.conn, "nobody"))
        # the lookup by id is strict: a slug is not an id
        self.assertIsNone(H.bot(self.conn, "ops-lead"))
        self.assertEqual(H.bot(self.conn, bot_id)["slug"], "ops-lead")

    def test_a_slug_cannot_name_another_bots_id_and_an_id_cannot_name_another_bots_slug(self):
        bot_id = self.make_bot("ops-lead")
        with self.assertRaises(sqlite3.IntegrityError):
            self.make_bot(bot_id)  # the new slug is the other bot's id
        with self.assertRaises(sqlite3.IntegrityError):
            self.make_bot("fresh-slug", bot_id="ops-lead")  # the new id is the other bot's slug
        other = self.make_bot("analyst")
        with self.assertRaises(sqlite3.IntegrityError):
            H.rename_bot(self.conn, other, bot_id)  # a rename cannot take another bot's id as its slug

    def test_rename_changes_only_the_slug(self):
        bot_id = self.make_bot("ops-lead")
        H.rename_bot(self.conn, bot_id, "ops-renamed")
        row = H.bot(self.conn, bot_id)
        self.assertEqual((row["id"], row["slug"]), (bot_id, "ops-renamed"))
        self.assertIsNone(H.resolve_bot(self.conn, "ops-lead"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE bots SET id='changed' WHERE id=?", (bot_id,))

    def test_rename_then_a_new_bot_takes_the_old_slug_and_the_history_stays_with_the_right_bot(self):
        old_id = self.make_bot("ops-lead")
        old_actor = H.bot_actor(old_id)
        task = H.task_create(
            self.conn, ANA, "Ship the packet", "Keep this with the bot that owned it.", old_actor, lint=False
        )
        H.event(self.conn, ANA, "test.note", old_actor, {"note": "before the rename"})

        H.rename_bot(self.conn, old_id, "ops-renamed")
        new_id = self.make_bot("ops-lead")  # the old slug, now free
        self.assertNotEqual(new_id, old_id)

        self.assertEqual(H.resolve_bot(self.conn, "ops-lead")["id"], new_id)
        self.assertEqual(H.bot(self.conn, old_id)["slug"], "ops-renamed")
        owner = self.conn.execute("SELECT owner_id FROM tasks WHERE id=?", (task["id"],)).fetchone()[0]
        self.assertEqual(owner, old_actor)
        self.assertEqual(H.bot(self.conn, H.actor_id(owner))["slug"], "ops-renamed")
        targets = [r[0] for r in self.conn.execute("SELECT target FROM events WHERE action='test.note'")]
        self.assertEqual(targets, [old_actor])
        self.assertEqual(H.resolve_actor(self.conn, "ops-lead"), H.bot_actor(new_id))

    def test_principals_follow_the_bots_and_people_they_name(self):
        def principal(actor):
            return self.conn.execute("SELECT kind, deleted_at FROM principals WHERE id=?", (actor,)).fetchone()

        bot_id = self.make_bot("ops-lead")
        self.assertEqual(tuple(principal(H.bot_actor(bot_id))), ("bot", None))
        self.conn.execute("DELETE FROM bot_settings WHERE bot_id=?", (bot_id,))
        self.conn.execute("DELETE FROM bots WHERE id=?", (bot_id,))
        gone = principal(H.bot_actor(bot_id))
        self.assertIsNotNone(gone)  # the row is kept
        self.assertIsNotNone(gone["deleted_at"])
        self.make_bot("ops-lead", bot_id=bot_id)  # the same id again: the principal is live again
        self.assertIsNone(principal(H.bot_actor(bot_id))["deleted_at"])

        self.conn.execute("INSERT INTO humans (id, name, email) VALUES ('ben', 'Ben', 'ben@acme.example')")
        self.assertEqual(tuple(principal("human:ben")), ("human", None))
        self.conn.execute("DELETE FROM humans WHERE id='ben'")
        self.assertIsNotNone(principal("human:ben")["deleted_at"])


if __name__ == "__main__":
    unittest.main()
