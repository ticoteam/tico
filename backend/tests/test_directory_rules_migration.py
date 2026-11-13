"""The directory_rules migration (track 5, 20261108044824): rule_sources moves onto organization entities, every row is
kept, org_rules names its From by entity, and a name the migration cannot place gets a new organization of its own.

The hub is the track 3 fixture with its small graph (test_directory_tables_migration). The pre-migration state is built
after it boots, since boot already ran this migration on an empty rule_sources table."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from backend.config import Settings
from backend.store import Store
from backend.tests.helpers.directory_tables_migration import FIXTURE, SAMPLE, copy_of  # noqa: F401

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "20261108044824_directory_rules.py"
NOW = "2026-10-01T00:00:00Z"


def _load():
    spec = importlib.util.spec_from_file_location("directory_rules_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hub(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "hub.db"
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text())
    conn.close()
    conn = sqlite3.connect(path)
    conn.executescript(SAMPLE)
    conn.close()
    Store(Settings(db_path=path)).initialize(seed_market=False)
    return path


def test_one_boot_and_each_case_migrates_its_own_copy(tmp_path, subtests):
    """The hub boots once (half a minute or more on CI); each case gets its own copy of the booted file and builds its
    own pre-migration state on it, as a boot of its own would have given it."""
    booted = _hub(tmp_path / "booted")
    fresh = lambda name: copy_of(booted, tmp_path / name / "hub.db")  # noqa: E731
    with subtests.test("rule sources move onto entities"):
        _rule_sources_move_onto_entities(fresh("moved"))
    for case in CANNOT_PLACE:
        name, kind, setup = case.values
        with subtests.test("a name it cannot place gets its own organization: " + case.id):
            _a_name_it_cannot_place_gets_its_own_organization_and_startup_goes_on(
                fresh(setup.__name__), name, kind, setup
            )
    with subtests.test("a fresh hub has the rule columns and no rule sources"):
        _a_fresh_hub_has_the_rule_columns_and_no_rule_sources(fresh("fresh"))


def _before(conn):
    """The state before the migration: rule_sources as it was, and org_rules.source_id."""
    conn.execute(
        "CREATE TABLE rule_sources(id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "kind TEXT NOT NULL CHECK (kind IN ('vendor','customer','government','other')), created_by TEXT NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    conn.execute("CREATE UNIQUE INDEX rule_sources_name ON rule_sources(lower(name))")
    conn.execute("ALTER TABLE org_rules ADD COLUMN source_id TEXT")
    conn.execute("CREATE INDEX org_rules_source ON org_rules(source_id) WHERE source_id IS NOT NULL")


def _rule(conn, rule_id, text, source_id, kind="external"):
    conn.execute(
        "INSERT INTO org_rules (id, kind, text, source_id, who, status, suggested_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '{\"everyone\": true}', 'active', 'human:jane', ?, ?)",
        (rule_id, kind, text, source_id, NOW, NOW),
    )


def _run(conn, module):
    conn.execute("BEGIN")
    try:
        module.up(conn)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _rule_sources_move_onto_entities(path):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("UPDATE entities SET self = 1 WHERE id = 'company/acme'")
    _before(conn)
    conn.executemany(
        "INSERT INTO rule_sources (id, name, kind, created_by, created_at) VALUES (?, ?, ?, 'human:jane', ?)",
        [
            ("src-vendor", "Acme Co", "vendor", NOW),
            ("src-gov", "California", "government", NOW),
            ("src-river", "River Capital", "customer", NOW),
            ("src-other", "Hooli", "other", NOW),
        ],
    )
    _rule(conn, "rule-vendor", "Pay Acme Co invoices in 30 days", "src-vendor")
    _rule(conn, "rule-gov", "Honor deletion requests in 45 days", "src-gov")
    _rule(conn, "rule-internal", "Only Sales bots email customers", None, kind="internal")

    _run(conn, _load())

    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rule_sources'").fetchone() is None
    provenance = dict(
        conn.execute(
            "SELECT json_extract(properties, '$.rule_source.id'), id FROM entities "
            "WHERE json_extract(properties, '$.rule_source.id') IS NOT NULL"
        ).fetchall()
    )
    # Every rule_sources row is kept on an entity: a new organization, or the one its name already named.
    assert provenance == {
        "src-vendor": "organization/acme-co",
        "src-gov": "organization/california",
        "src-river": "company/river",
        "src-other": "organization/hooli",
    }
    assert conn.execute(
        "SELECT option_key FROM entity_values WHERE entity_id = 'organization/california' "
        "AND entity_attribute_id = 'attr_organization_kind'"
    ).fetchone() == ("government",)
    # A reused entity keeps its visibility: the migration adds no source to it.
    assert conn.execute(
        "SELECT COUNT(*) FROM entity_sources WHERE entity_id = 'company/river' AND source_type = 'rule_source'"
    ).fetchone() == (0,)
    assert conn.execute(
        "SELECT COUNT(*) FROM entity_sources WHERE entity_id = 'organization/acme-co' AND source_type = 'rule_source'"
    ).fetchone() == (1,)
    # Self exists, so a vendor, a customer and a government link to it.
    assert set(
        conn.execute(
            "SELECT from_entity_id, link_key, to_entity_id FROM entity_links WHERE source_type = 'rule_source'"
        )
    ) == {
        ("organization/acme-co", "vendor_to", "company/acme"),
        ("organization/california", "governs", "company/acme"),
        ("company/river", "customer_of", "company/acme"),
    }
    # org_rules names its From by entity, and the source_id column is gone.
    assert "source_id" not in _columns(conn, "org_rules")
    assert dict(
        conn.execute("SELECT id, source_entity_id FROM org_rules WHERE suggested_by != 'keeper'").fetchall()
    ) == {
        "rule-vendor": "organization/acme-co",
        "rule-gov": "organization/california",
        "rule-internal": None,
    }


def _organization(conn, entity_id, name, kind=None):
    conn.execute(
        "INSERT INTO entities (id, type, name, name_key, created_by, created_at, updated_at) "
        "VALUES (?, 'organization', ?, lower(?), 'seed', ?, ?)",
        (entity_id, name, name, NOW, NOW),
    )
    if kind:
        conn.execute(
            "INSERT INTO entity_values (id, entity_id, entity_attribute_id, single_value, option_key, source_type, "
            "source_id, confidence, updated_at) VALUES (?, ?, 'attr_organization_kind', 1, ?, 'seed', '', 0.9, ?)",
            (f"val-{entity_id}", entity_id, kind, NOW),
        )


def _self(conn):
    conn.execute("UPDATE entities SET self = 1 WHERE id = 'company/acme'")


def _two_hoolis(conn):
    _organization(conn, "organization/hooli-1", "Hooli")
    _organization(conn, "organization/hooli-2", "Hooli")


def _company_oakridge(conn):
    _organization(conn, "organization/oakridge", "Oakridge", kind="company")


CANNOT_PLACE = (
    pytest.param("Acme Lights", "vendor", _self, id="a name that is the company itself"),
    pytest.param("Hooli", "vendor", _two_hoolis, id="a name two organizations carry"),
    pytest.param("Oakridge", "government", _company_oakridge, id="a government whose one match is a company"),
)


def _a_name_it_cannot_place_gets_its_own_organization_and_startup_goes_on(path, name, kind, setup):
    conn = sqlite3.connect(path, isolation_level=None)
    _before(conn)
    conn.execute(
        "INSERT INTO rule_sources (id, name, kind, created_by, created_at) VALUES ('src-ambiguous', ?, ?, 'human:jane', ?)",
        (name, kind, NOW),
    )
    _rule(conn, "rule-ambiguous", "Keep records for 7 years", "src-ambiguous")
    _rule(conn, "rule-dangling", "Pointed at nothing", "src-gone")
    setup(conn)
    entities_before = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    _run(conn, _load())
    placed = conn.execute("SELECT source_entity_id FROM org_rules WHERE id = 'rule-ambiguous'").fetchone()[0]
    row = conn.execute(
        "SELECT name, self, json_extract(properties, '$.rule_source.id') FROM entities WHERE id = ?", (placed,)
    )
    assert row.fetchone() == (name, 0, "src-ambiguous")
    assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == entities_before + 1
    assert conn.execute("SELECT source_entity_id FROM org_rules WHERE id = 'rule-dangling'").fetchone() == (None,)
    assert "rule_sources" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _a_fresh_hub_has_the_rule_columns_and_no_rule_sources(path):
    conn = sqlite3.connect(path)
    try:
        assert "rule_sources" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        columns = _columns(conn, "org_rules")
        assert {"source_entity_id", "document_entity_id", "toward_scope", "applies_when", "party_side"} <= columns
        assert "source_id" not in columns
    finally:
        conn.close()
