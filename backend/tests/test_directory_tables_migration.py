"""The Directory's tables (migration 20261108020258_directory_tables) on a hub that has run track 1's migration.

The hub is backend/tests/fixtures/schema_pre_cleanup.sql with a small graph written under the old names and types. One
boot moves every entity to its Directory type (company -> organization with kind company, geography -> place,
regulation -> document), makes the built-in types, link types and the organization kind, and every link key the graph
holds is a link type. The identifiers a store writes by hand survive a name change, because the derived ones are the
only ones the triggers replace. Running the migration again changes nothing."""

import importlib.util
import sqlite3

from backend.config import Settings
from backend.store import Store
from backend.tests.helpers.directory_tables_migration import FIXTURE, ROOT, SAMPLE

MIGRATION = ROOT / "migrations" / "20261108020258_directory_tables.py"


def _load():
    spec = importlib.util.spec_from_file_location("directory_tables_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _boot(path):
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text())
    conn.close()
    conn = sqlite3.connect(path)  # a new connection reads the FTS tables' schema rows the dump writes
    conn.executescript(SAMPLE)
    conn.close()
    Store(Settings(db_path=path)).initialize(seed_market=False)


def _counts(conn):
    return tuple(
        conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("entity_types", "link_types", "entity_attributes", "entity_values", "entity_identifiers")
    )


def test_the_graph_moves_to_directory_types_with_the_seeds_and_the_kind(tmp_path):
    path = tmp_path / "hub.sqlite"
    _boot(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        types = {r["id"]: r["type"] for r in conn.execute("SELECT id, type FROM entities")}
        assert types == {
            "company/acme": "organization",
            "company/river": "organization",
            "person/jane": "person",
            "geography/united-states": "place",
            "regulation/ul-1573": "document",
            "event/launch": "event",
        }
        kinds = conn.execute(
            "SELECT entity_id, option_key FROM entity_values WHERE entity_attribute_id = 'attr_organization_kind'"
        ).fetchall()
        assert sorted(tuple(r) for r in kinds) == [("company/acme", "company"), ("company/river", "company")]

        built_in = {r["key"]: (r["built_in"], r["archived_at"]) for r in conn.execute("SELECT * FROM entity_types")}
        assert {"person", "organization", "place", "document", "product", "segment", "channel", "event"} <= set(
            built_in
        )
        assert all(built_in[k] == (1, None) for k in ("person", "organization", "place", "document"))
        assert built_in["deal"][0] == 1 and built_in["deal"][1] is not None

        # Every type an entity holds is a type, and every link key the graph holds is a link type.
        assert (
            conn.execute(
                "SELECT DISTINCT type FROM entities WHERE type NOT IN (SELECT key FROM entity_types)"
            ).fetchall()
            == []
        )
        assert (
            conn.execute(
                "SELECT DISTINCT link_key FROM entity_links WHERE link_key NOT IN (SELECT key FROM link_types)"
            ).fetchall()
            == []
        )
        works_at = conn.execute(
            "SELECT from_entity_type, to_entity_type, inherits_rules FROM link_types WHERE key='works_at'"
        ).fetchone()
        assert tuple(works_at) == ("person", "organization", 1)
        assert conn.execute("SELECT symmetric FROM link_types WHERE key='competes_with'").fetchone()[0] == 1

        columns = {r[1] for r in conn.execute("PRAGMA table_info(entities)")}
        assert "self" in columns
        assert "evidence_id" in {r[1] for r in conn.execute("PRAGMA table_info(entity_links)")}
    finally:
        conn.close()


def test_a_store_written_identifier_survives_a_name_change_and_derived_ones_follow_it(tmp_path):
    path = tmp_path / "hub.sqlite"
    _boot(path)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT count(*) FROM entity_identifiers WHERE derived = 0").fetchone()[0] == 0
        manual = conn.execute(
            "INSERT INTO entity_identifiers (entity_id, kind, value, normalized_value, primary_identifier, derived) "
            "VALUES ('company/acme', 'email', 'jane@example.test', 'jane@example.test', 0, 0)"
        ).lastrowid
        conn.execute("UPDATE entities SET name = 'Acme Holiday' WHERE id = 'company/acme'")
        conn.commit()

        assert (
            conn.execute("SELECT value FROM entity_identifiers WHERE id = ?", (manual,)).fetchone()[0]
            == "jane@example.test"
        )
        names = [
            r[0]
            for r in conn.execute(
                "SELECT value FROM entity_identifiers WHERE entity_id = 'company/acme' AND derived = 1 AND kind = 'name'"
            )
        ]
        assert "acme holiday" in names and "acme lights" not in names  # the alias "Acme" stays a derived name too
    finally:
        conn.close()


def test_running_the_migration_again_changes_nothing(tmp_path):
    path = tmp_path / "hub.sqlite"
    _boot(path)
    conn = sqlite3.connect(path)
    try:
        before = _counts(conn)
        migration = _load()
        conn.execute("BEGIN IMMEDIATE")
        migration.up(conn)
        conn.execute("COMMIT")
        assert _counts(conn) == before
        assert conn.execute("SELECT count(*) FROM entities WHERE type = 'company'").fetchone()[0] == 0
    finally:
        conn.close()
