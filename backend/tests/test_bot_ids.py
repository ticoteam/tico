"""Stable bot ids: `bots.id` is the identity, `slug` a renameable label (backend/migrations/0012_bot_ids.py).

The upgrade test builds a database the way v0.5 leaves one (user_version 71, no schema_migrations, bots keyed by slug,
every bot reference naming bots(slug)), fills it, runs the real migrations, and checks that nothing was lost or
rewritten.
"""
import importlib
import re
import sqlite3
import tempfile
import unittest
import unittest.mock
import uuid
from pathlib import Path

from backend import bot_tools, files, migrations, store, status_values
from backend import hubdb as H
from backend.config import Settings

BOT_IDS = importlib.import_module("backend.migrations.0012_bot_ids")

EMPLOYEES = {
    "coo": {"display_name": "COO", "status": "active", "host": "keeper", "runtime": "codex"},
    "cmo": {"display_name": "CMO", "status": "active", "host": "keeper", "runtime": "codex"},
}
PEOPLE = {"people": [{"id": "ana", "name": "Ana Rivera", "email": "ana@acme.example", "team": "leadership"}]}
ANA = H.human_actor("ana")

# bots as main defines them, before the bot id step
OLD_BOTS_DDL = """CREATE TABLE IF NOT EXISTS bots (
  slug TEXT PRIMARY KEY, display_name TEXT, runtime TEXT, model TEXT, effort TEXT,
  cwd TEXT, host TEXT DEFAULT 'dispatcher', thread_id TEXT, token_hash TEXT,
  state TEXT DEFAULT 'active', created_at TEXT, last_turn_at TEXT);"""
NEW_BOTS_DDL = """CREATE TABLE IF NOT EXISTS bots (
  id TEXT PRIMARY KEY NOT NULL, slug TEXT NOT NULL UNIQUE, display_name TEXT, runtime TEXT, model TEXT, effort TEXT,
  cwd TEXT, host TEXT DEFAULT 'dispatcher', thread_id TEXT, token_hash TEXT,
  state TEXT DEFAULT 'active', created_at TEXT, last_turn_at TEXT);"""

# The 14 foreign keys into bots: 11 in store.SCHEMA, bot_tool_requests, bot_files and chat_goals.
CHILD_TABLES = ("bot_config", "bot_control", "bot_transitions", "settings_changes", "bot_session_epochs",
                "bot_sessions", "agents", "assignments", "jobs", "mail_agent_instructions",
                "bot_agent_instructions", "bot_tool_requests", "bot_files", "chat_goals")
KEEPER_SYSTEM_ACTORS = ("system:company", "system:deploy", "system:finance", "system:gateway", "system:hosting",
                        "system:storage")


def _open(path):
    """hubdb's own steps, then the migration files: the first part of Store.initialize."""
    conn = H.connect(path)
    migrations.apply(conn)
    return conn


def _old_shape_database(path):
    """A hub.db with v0.5's shape: user_version 71 and no migration files yet, bots keyed by slug, every foreign key
    naming bots(slug). hubdb's steps run with the old bots definition; the store, bot tool and file tables are created
    from their own schema text with the reference moved back to bots(slug)."""
    old_first = H.SCHEMA.replace(NEW_BOTS_DDL, OLD_BOTS_DDL)
    assert old_first != H.SCHEMA
    # v0.5 had no migration files: hubdb.migrate, which now runs them after its numbered steps, leaves them out here.
    with unittest.mock.patch.object(H, "MIGRATIONS", [old_first, *H.MIGRATIONS[1:]]), \
            unittest.mock.patch.object(migrations, "apply", lambda conn, directory=None: None):
        conn = H.connect(path)
    conn.execute("PRAGMA foreign_keys=OFF")
    for schema in (store.SCHEMA, bot_tools.SCHEMA, files.SCHEMA):
        conn.executescript(schema.replace("REFERENCES bots(id)", "REFERENCES bots(slug)"))
    return conn


# values a CHECK constraint needs; every other NOT NULL column gets a placeholder
CHECKED_VALUES = {"chat_goals": {"status": "active"}}


def _insert_child_row(conn, table, bot):
    """One row in `table` naming `bot` in its bot column. Other NOT NULL columns get placeholder values."""
    values = {}
    for _cid, name, typ, notnull, default, pk in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
        if name == "bot":
            values[name] = bot
        elif name in CHECKED_VALUES.get(table, {}):
            values[name] = CHECKED_VALUES[table][name]
        elif notnull and default is None:
            values[name] = f"{table}-{name}" if pk else ("x" if (typ or "").upper() != "INTEGER" else 0)
    columns = ", ".join(f'"{name}"' for name in values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({marks})', tuple(values.values()))


def _store_tables(conn):
    """The tables the app creates after the migrations (Store.initialize), with the final bot references."""
    for schema in (store.SCHEMA, bot_tools.SCHEMA, files.SCHEMA):
        conn.executescript(schema)


def _shape(conn, table):
    """What the upgrade must not change in a child table: its columns, its indexes and triggers as written, and its
    definition after the header (the name in the CREATE statement changes with the rebuild)."""
    columns = [tuple(row) for row in conn.execute(f'PRAGMA table_info("{table}")')]
    indexes = sorted((row[1], row[3]) for row in conn.execute(f'PRAGMA index_list("{table}")'))
    objects = sorted(row[0] for row in conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",
        (table,)) if not row[0].startswith("CREATE TRIGGER status_values_"))      # migration 0002's own triggers
    (create,) = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    body = create[create.index("("):]
    return columns, indexes, objects, body


def _without_status_checks(body):
    """A table definition without the status CHECKs migration 0002 adds (backend/status_values.py)."""
    body = re.sub(r",\s*CONSTRAINT status_values_\w+ CHECK \((?:[^()]|\([^()]*\))*\)", "", body)
    return re.sub(r"\s+\)$", ")", body)


class _Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.conn = _open(Path(self.dir.name) / "hub.db")
        self.addCleanup(self.conn.close)
        H.sync_registry(self.conn, EMPLOYEES, PEOPLE)

    def make_bot(self, slug, bot_id=None):
        """A bot made the way the app makes one: its id from H.new_bot_id unless given."""
        bot_id = bot_id or H.new_bot_id(slug)
        self.conn.execute("INSERT INTO bots (id, slug, display_name, state, created_at) VALUES (?, ?, ?, 'active', ?)",
                          (bot_id, slug, slug.title(), H.now()))
        return bot_id


class Upgrade(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "hub.db"
        self.conn = _old_shape_database(self.path)
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO humans (id, name, email) VALUES ('ana', 'Ana', 'ana@acme.example')")
        for slug in ("coo", "cmo", "ops-lead"):
            self.conn.execute("INSERT INTO bots (slug, display_name, state, created_at) VALUES (?, ?, 'active', ?)",
                              (slug, slug.upper(), H.now()))
        for table in CHILD_TABLES:
            _insert_child_row(self.conn, table, "ops-lead")

    def test_upgrade_keeps_every_bot_and_every_child_row_and_moves_references_to_id(self):
        # the fixture's table list is the 14 foreign keys into bots(slug), no more and no less
        self.assertEqual(sorted(BOT_IDS._slug_references(self.conn)), sorted(CHILD_TABLES))
        before_rows = {table: [tuple(r) for r in self.conn.execute(f'SELECT * FROM "{table}"')]
                       for table in CHILD_TABLES}
        before_shape = {table: _shape(self.conn, table) for table in CHILD_TABLES}
        before_violations = {table: len(self.conn.execute(f'PRAGMA foreign_key_check("{table}")').fetchall())
                             for table in CHILD_TABLES}
        bots_before = [tuple(r) for r in self.conn.execute("SELECT slug, display_name, state FROM bots ORDER BY slug")]
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], len(H.MIGRATIONS))
        self.assertFalse(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone())

        self.conn.close()
        conn = _open(self.path)
        self.addCleanup(conn.close)

        # every bot kept, and the id of an existing bot is its slug
        bots_after = [tuple(r) for r in conn.execute("SELECT slug, display_name, state FROM bots ORDER BY slug")]
        self.assertEqual(bots_after, bots_before)
        self.assertEqual({r["id"] for r in conn.execute("SELECT id FROM bots")}, {"coo", "cmo", "ops-lead"})
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(H.MIGRATIONS))
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

        for table in CHILD_TABLES:
            with self.subTest(table=table):
                # every row, with the same values
                after = [tuple(r) for r in conn.execute(f'SELECT * FROM "{table}"')]
                self.assertEqual(after, before_rows[table])
                # every column, index and trigger, and the same definition apart from the header
                columns, indexes, objects, body = _shape(conn, table)
                old_columns, old_indexes, old_objects, old_body = before_shape[table]
                self.assertEqual(columns, old_columns)
                self.assertEqual(indexes, old_indexes)
                self.assertEqual(objects, old_objects)
                # migration 0002 runs first and adds its status CHECKs; the rest of the definition is the same
                self.assertEqual(_without_status_checks(body),
                                 _without_status_checks(old_body.replace("REFERENCES bots(slug)", "REFERENCES bots(id)")))
                # no foreign key still names bots(slug)
                self.assertFalse([r for r in conn.execute(f'PRAGMA foreign_key_list("{table}")')
                                  if r[2] == "bots" and (r[4] or "").lower() == "slug"])
                # the same foreign key violations as before (the fixture has no runners or messages, so some exist
                # already; the upgrade must not add any)
                self.assertEqual(len(conn.execute(f'PRAGMA foreign_key_check("{table}")').fetchall()),
                                 before_violations[table])

    def test_upgrade_fills_principals_for_humans_bots_and_system_actors(self):
        self.conn.close()
        conn = _open(self.path)
        self.addCleanup(conn.close)
        kinds = {r["id"]: r["kind"] for r in conn.execute("SELECT id, kind FROM principals WHERE deleted_at IS NULL")}
        self.assertEqual(kinds["human:ana"], "human")
        self.assertEqual(kinds["bot:ops-lead"], "bot")
        self.assertEqual(kinds["keeper"], "system")
        for actor in KEEPER_SYSTEM_ACTORS:
            self.assertEqual(kinds[actor], "system")

    def test_rename_after_the_upgrade_keeps_the_legacy_id(self):
        self.conn.close()
        conn = _open(self.path)
        self.addCleanup(conn.close)
        renamed = H.rename_bot(conn, "ops-lead", "ops-renamed")
        self.assertEqual(renamed["id"], "ops-lead")
        self.assertEqual(H.bot(conn, "ops-lead")["slug"], "ops-renamed")

    def test_upgrade_called_again_on_an_upgraded_database_changes_nothing(self):
        self.conn.close()
        conn = _open(self.path)
        self.addCleanup(conn.close)
        schema = conn.execute("SELECT group_concat(sql) FROM sqlite_master").fetchone()[0]
        principals = [tuple(r) for r in conn.execute("SELECT * FROM principals ORDER BY id")]
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN")
        BOT_IDS.up(conn)
        conn.execute("COMMIT")
        self.assertEqual(conn.execute("SELECT group_concat(sql) FROM sqlite_master").fetchone()[0], schema)
        self.assertEqual([tuple(r) for r in conn.execute("SELECT * FROM principals ORDER BY id")], principals)

    def test_upgrade_runs_inside_the_callers_transaction_and_a_rollback_undoes_it(self):
        """The caller owns the transaction: `upgrade` neither begins nor commits, so a rollback leaves the old shape."""
        self.conn.execute("PRAGMA foreign_keys=OFF")
        legacy_before = self.conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
        columns_before = [row[1] for row in self.conn.execute("PRAGMA table_info(bots)")]
        self.conn.execute("BEGIN")
        BOT_IDS.up(self.conn)
        self.assertTrue(self.conn.in_transaction, "upgrade ended the caller's transaction")
        self.assertIn("id", [row[1] for row in self.conn.execute("PRAGMA table_info(bots)")])
        self.conn.execute("ROLLBACK")
        self.assertEqual([row[1] for row in self.conn.execute("PRAGMA table_info(bots)")], columns_before)
        self.assertEqual(sorted(BOT_IDS._slug_references(self.conn)), sorted(CHILD_TABLES))
        self.assertEqual(self.conn.execute("PRAGMA legacy_alter_table").fetchone()[0], legacy_before)


class UpgradeBoot(unittest.TestCase):
    def test_a_v05_database_boots_with_bot_ids_and_the_status_checks_on_bots(self):
        """The whole start on an old database: 0002 rebuilds bots with its state CHECK, 0012 rebuilds it again with
        the id, and status_values.enforce at the end of the start finds nothing left to change."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hub.db"
            conn = _old_shape_database(path)
            conn.execute("INSERT INTO bots (slug, display_name, state, created_at) VALUES ('ops-lead', 'Ops', 'paused', ?)",
                         (H.now(),))
            conn.commit()
            conn.close()
            registry = Path(__file__).parent / "fixtures" / "registry"
            store.Store(Settings(db_path=path, registry_dir=registry)).initialize(seed_market=False)
            conn = sqlite3.connect(path)
            try:
                versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
                self.assertIn("0012_bot_ids", versions)
                self.assertIn(f"legacy_user_version_{len(H.MIGRATIONS)}", versions)
                (bots_sql,) = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='bots'").fetchone()
                self.assertIn("id TEXT PRIMARY KEY NOT NULL", bots_sql)
                self.assertIn("CONSTRAINT status_values_state CHECK", bots_sql)
                self.assertEqual(conn.execute("SELECT id, slug, state FROM bots WHERE slug='ops-lead'").fetchone(),
                                 ("ops-lead", "ops-lead", "paused"))
                self.assertEqual(sorted(BOT_IDS._slug_references(conn)), [])
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("UPDATE bots SET state='nonsense' WHERE id='ops-lead'")
                # a second pass of the constraints changes nothing
                schema = conn.execute("SELECT group_concat(sql) FROM sqlite_master").fetchone()[0]
                status_values.enforce(conn)
                self.assertEqual(conn.execute("SELECT group_concat(sql) FROM sqlite_master").fetchone()[0], schema)
            finally:
                conn.close()


class Schema(_Case):
    def test_a_new_database_has_the_final_shape(self):
        _store_tables(self.conn)
        bots_sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='bots'").fetchone()[0]
        self.assertIn("id TEXT PRIMARY KEY NOT NULL", bots_sql)
        self.assertIn("slug TEXT NOT NULL UNIQUE", bots_sql)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], len(H.MIGRATIONS))
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        for table in CHILD_TABLES:
            with self.subTest(table=table):
                targets = {(r[2], (r[4] or "").lower()) for r in self.conn.execute(f'PRAGMA foreign_key_list("{table}")')
                           if r[2] == "bots"}
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
            clock.now.return_value.timestamp.return_value = 2_000_000_000.25      # seconds since the Unix epoch
            early = H.uuid7()
            clock.now.return_value.timestamp.return_value = 2_000_000_001.5
            later = H.uuid7()
        self.assertEqual(uuid.UUID(early).version, 7)
        self.assertEqual(early.replace("-", "")[:12], format(2_000_000_000_250, "012x"))
        self.assertEqual(later.replace("-", "")[:12], format(2_000_000_001_500, "012x"))
        self.assertLess(early, later)                                  # ids sort by creation time

    def test_a_prefixed_slug_or_id_resolves_to_the_same_actor(self):
        bot_id = self.make_bot("ops-lead")
        self.assertEqual(H.resolve_actor(self.conn, "bot:ops-lead"), f"bot:{bot_id}")
        self.assertEqual(H.resolve_actor(self.conn, f"bot:{bot_id}"), f"bot:{bot_id}")
        self.assertIsNone(H.resolve_actor(self.conn, "bot:nobody"))

    def test_a_product_bot_keeps_its_slug_as_id(self):
        self.assertEqual(H.new_bot_id("botops"), "botops")
        self.assertEqual(H.new_bot_id("tico-counsel"), "tico-counsel")

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
            self.make_bot(bot_id)                     # the new slug is the other bot's id
        with self.assertRaises(sqlite3.IntegrityError):
            self.make_bot("fresh-slug", bot_id="ops-lead")   # the new id is the other bot's slug
        other = self.make_bot("analyst")
        with self.assertRaises(sqlite3.IntegrityError):
            H.rename_bot(self.conn, other, bot_id)    # a rename cannot take another bot's id as its slug

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
        task = H.task_create(self.conn, ANA, "Ship the packet", "Keep this with the bot that owned it.", old_actor,
                             lint=False)
        H.event(self.conn, ANA, "test.note", old_actor, {"note": "before the rename"})

        H.rename_bot(self.conn, old_id, "ops-renamed")
        new_id = self.make_bot("ops-lead")            # the old slug, now free
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
        self.conn.execute("DELETE FROM bots WHERE id=?", (bot_id,))
        gone = principal(H.bot_actor(bot_id))
        self.assertIsNotNone(gone)                    # the row is kept
        self.assertIsNotNone(gone["deleted_at"])
        self.make_bot("ops-lead", bot_id=bot_id)      # the same id again: the principal is live again
        self.assertIsNone(principal(H.bot_actor(bot_id))["deleted_at"])

        self.conn.execute("INSERT INTO humans (id, name, email) VALUES ('ben', 'Ben', 'ben@acme.example')")
        self.assertEqual(tuple(principal("human:ben")), ("human", None))
        self.conn.execute("DELETE FROM humans WHERE id='ben'")
        self.assertIsNotNone(principal("human:ben")["deleted_at"])


class BotIdMigrationRoundTrip(unittest.TestCase):
    def test_the_step_runs_once_and_a_second_connect_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hub.db"
            _old_shape_database(path).close()
            first = _open(path)
            schema_once = first.execute("SELECT group_concat(sql) FROM sqlite_master").fetchone()[0]
            first.close()
            second = _open(path)
            try:
                self.assertEqual(second.execute("SELECT group_concat(sql) FROM sqlite_master").fetchone()[0],
                                 schema_once)
                self.assertEqual(second.execute("PRAGMA user_version").fetchone()[0], len(H.MIGRATIONS))
            finally:
                second.close()


if __name__ == "__main__":
    unittest.main()
