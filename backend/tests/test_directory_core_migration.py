"""The Directory's core tables (migration 20261108011017_directory_core) on a hub from before it.

The hub is backend/tests/fixtures/schema_pre_cleanup.sql (the last release before the renames) with a small market graph
written under the old names and relation keys (the old
market_keys triggers derive the identifier rows on insert). One boot moves it to the Directory's names: every id is kept, the
relations read the way the API reads them (a flipped key swaps its ends), the confidence labels become numbers, the
identifiers are the names, domains and aliases the graph was found by, and full-text search finds what it found before,
on stable rowids."""

import json
import sqlite3
from pathlib import Path

from backend import market as M
from backend.config import Settings
from backend.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "schema_pre_cleanup.sql"
OLD_TABLES = ("market_entities", "market_edges", "market_entity_keys", "market_watches")

SAMPLE = """
INSERT INTO market_entities (id, type, name, aliases, external_ids, tier, summary, properties, status, merged_into,
  last_verified, created, created_by, updated) VALUES
 ('company/acme', 'company', 'Acme Lights', '["Acme"]', '{"domain": "acme.example"}', 'core', 'Projector rentals', '{}',
  'active', NULL, '2026-10-01', '2026-10-01T00:00:00Z', 'human:ana', '2026-10-01T00:00:00Z'),
 ('company/old-acme', 'company', 'Acme Old', '[]', '{}', NULL, '', '{}', 'merged', 'company/acme', NULL,
  '2026-09-01T00:00:00Z', 'seed', '2026-10-01T00:00:00Z'),
 ('company/river', 'company', 'River Capital', '[]', '{}', NULL, '', '{}', 'active', NULL, NULL,
  '2026-09-01T00:00:00Z', 'bot:librarian', '2026-10-01T00:00:00Z'),
 ('person/jane', 'person', 'Jane Ortiz', '[]', '{}', NULL, '', '{}', 'active', NULL, NULL,
  '2026-09-01T00:00:00Z', 'bot:librarian', '2026-10-01T00:00:00Z'),
 ('person/sam', 'person', 'Sam Lee', '[]', '{}', NULL, '', '{}', 'active', NULL, NULL,
  '2026-09-01T00:00:00Z', 'bot:librarian', '2026-10-01T00:00:00Z'),
 ('regulation/ul-1573', 'regulation', 'UL 1573', '[]', '{}', NULL, '', '{}', 'active', NULL, NULL,
  '2026-09-01T00:00:00Z', 'seed', '2026-10-01T00:00:00Z');
INSERT INTO market_edges (id, src, rel, dst, since, until, confidence, properties, created, created_by, updated) VALUES
 ('edge-employs', 'company/acme', 'employs', 'person/jane', '2026-01-01', NULL, 'high', '{}',
  '2026-10-01T00:00:00Z', 'human:ana', '2026-10-01T00:00:00Z'),
 ('edge-led', 'company/acme', 'led_by', 'person/sam', NULL, NULL, 'medium', '{}',
  '2026-10-01T00:00:00Z', 'bot:librarian', '2026-10-01T00:00:00Z'),
 ('edge-regulated', 'company/acme', 'regulated_by', 'regulation/ul-1573', NULL, NULL, 'low', '{}',
  '2026-10-01T00:00:00Z', 'seed', '2026-10-01T00:00:00Z'),
 ('edge-invested', 'company/river', 'invested_in', 'company/acme', '2025-06-01', NULL, 'medium', '{}',
  '2026-10-01T00:00:00Z', 'bot:librarian', '2026-10-01T00:00:00Z'),
 ('edge-competes', 'company/acme', 'competes_with', 'company/old-acme', NULL, NULL, 'high', '{}',
  '2026-10-01T00:00:00Z', 'seed', '2026-10-01T00:00:00Z');
INSERT INTO market_watches (id, entity_id, url, kind, active, created_by, created, checked, changed, last_hash, last_text,
  last_status, error) VALUES
 ('watch-1', 'company/acme', 'https://acme.example/pricing', 'page', 1, 'bot:librarian', '2026-10-01T00:00:00Z',
  NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO market_evidence (id, source_url, source_kind, captured_at, captured_by, quote, our_read, blob_id) VALUES
 ('ev-1', 'https://acme.example/pricing', 'other', '2026-10-01T00:00:00Z', 'bot:librarian',
  'The pricing page lists three tiers', 'Strong fit', NULL);
"""


def test_a_hub_from_before_the_directory_keeps_its_graph_under_the_new_names(tmp_path):
    path = tmp_path / "hub.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text())
    conn.close()
    conn = sqlite3.connect(path)  # a new connection reads the FTS tables' schema rows the dump writes
    conn.executescript(SAMPLE)
    conn.close()

    Store(Settings(db_path=path)).initialize(seed_market=False)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert not names & set(OLD_TABLES)
        assert {"entities", "entity_links", "entity_identifiers", "entity_watches"} <= names

        # Every id is kept, and the rows that were there are all there.
        assert [r[0] for r in conn.execute("SELECT id FROM entities ORDER BY id")] == [
            "company/acme",
            "company/old-acme",
            "company/river",
            "person/jane",
            "person/sam",
            "regulation/ul-1573",
        ]
        assert conn.execute("SELECT count(*) FROM entity_links").fetchone()[0] == 5
        assert conn.execute("SELECT count(*) FROM entity_watches WHERE id='watch-1'").fetchone()[0] == 1

        acme = dict(conn.execute("SELECT * FROM entities WHERE id='company/acme'").fetchone())
        assert (acme["name_key"], acme["primary_domain"], acme["tier"], acme["last_verified_at"]) == (
            "acme lights",
            "acme.example",
            "core",
            "2026-10-01",
        )
        assert (acme["created_by"], acme["created_by_type"], acme["created_by_id"]) == ("human:ana", "human", "ana")
        old = dict(conn.execute("SELECT * FROM entities WHERE id='company/old-acme'").fetchone())
        assert (old["status"], old["merged_into_id"]) == ("merged", "company/acme")

        # The relations: employs, led_by and regulated_by read the other way round under the keys that mean them.
        links = {
            row["id"]: row
            for row in conn.execute(
                "SELECT id, from_entity_id, link_key, to_entity_id, confidence, properties FROM entity_links"
            )
        }
        assert (links["edge-employs"]["from_entity_id"], links["edge-employs"]["link_key"]) == (
            "person/jane",
            "works_at",
        )
        assert links["edge-employs"]["to_entity_id"] == "company/acme" and links["edge-employs"]["confidence"] == 0.9
        assert (links["edge-led"]["from_entity_id"], links["edge-led"]["link_key"]) == ("person/sam", "works_at")
        assert json.loads(links["edge-led"]["properties"]) == {"role": "leader"}
        assert links["edge-led"]["confidence"] == 0.6
        assert (links["edge-regulated"]["from_entity_id"], links["edge-regulated"]["link_key"]) == (
            "regulation/ul-1573",
            "regulates",
        )
        assert links["edge-regulated"]["confidence"] == 0.3
        assert (links["edge-invested"]["link_key"], links["edge-invested"]["from_entity_id"]) == (
            "invests_in",
            "company/river",
        )
        assert links["edge-competes"]["link_key"] == "competes_with" and links["edge-competes"]["confidence"] == 0.9
        by_actor = conn.execute(
            "SELECT created_by_type, created_by_id FROM entity_links WHERE id='edge-invested'"
        ).fetchone()
        assert tuple(by_actor) == ("bot", "librarian")

        # The API reads the same graph under its own names: the canonical key, the flipped ends, the confidence label.
        edge = M.edge(conn, "edge-employs")
        assert (edge["src"], edge["rel"], edge["dst"], edge["confidence"]) == (
            "person/jane",
            "works_at",
            "company/acme",
            "high",
        )
        assert [e["src"] for e in M.edges_of(conn, rel="works_at", dst="company/acme")] == ["person/jane", "person/sam"]
        assert M.entity(conn, "company/old-acme")["merged_into"] == "company/acme"

        # Identifiers: the name, the domain and the alias the graph was found by; the name and domain are primary.
        identifiers = conn.execute(
            "SELECT normalized_value, kind, primary_identifier FROM entity_identifiers WHERE entity_id='company/acme' "
            "ORDER BY normalized_value"
        ).fetchall()
        assert [tuple(r) for r in identifiers] == [
            ("acme", "name", 0),
            ("acme lights", "name", 1),
            ("acme.example", "domain", 1),
        ]

        # Full-text search: an entity and an evidence row are found by their words, stay found after an update, and an
        # entity's rowid is its seq, so the index never points at another row.
        def hits(text):
            return sorted(r[0] for r in conn.execute("SELECT ref FROM market_fts WHERE market_fts MATCH ?", (text,)))

        assert hits("Lights") == ["company/acme"]
        assert hits("pricing") == ["ev-1"]
        seq = conn.execute("SELECT seq FROM entities WHERE id='company/acme'").fetchone()[0]
        assert conn.execute("SELECT rowid FROM market_fts WHERE market_fts MATCH 'Lights'").fetchone()[0] == seq
        conn.execute("UPDATE entities SET name='Acme Holiday' WHERE id='company/acme'")
        assert hits("Holiday") == ["company/acme"] and hits("Lights") == []
        conn.execute("DELETE FROM market_evidence WHERE id='ev-1'")
        assert hits("pricing") == []
        conn.commit()
    finally:
        conn.close()
