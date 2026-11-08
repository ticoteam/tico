"""Helpers that other tests share, moved out of test_directory_tables_migration.py so they import no test file."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


FIXTURE = ROOT / "tests" / "fixtures" / "schema_pre_cleanup.sql"


SAMPLE = """
INSERT INTO market_entities (id, type, name, aliases, external_ids, tier, summary, properties, status, merged_into,
  last_verified, created, created_by, updated) VALUES
 ('company/acme', 'company', 'Acme Lights', '["Acme"]', '{"domain": "acme.example"}', 'core', '', '{}',
  'active', NULL, NULL, '2026-10-01T00:00:00Z', 'human:jane', '2026-10-01T00:00:00Z'),
 ('company/river', 'company', 'River Capital', '[]', '{}', NULL, '', '{}', 'active', NULL, NULL,
  '2026-09-01T00:00:00Z', 'bot:librarian', '2026-10-01T00:00:00Z'),
 ('person/jane', 'person', 'Jane Ortiz', '[]', '{}', NULL, '', '{}', 'active', NULL, NULL,
  '2026-09-01T00:00:00Z', 'bot:librarian', '2026-10-01T00:00:00Z'),
 ('geography/united-states', 'geography', 'United States', '[]', '{}', NULL, '', '{}', 'active', NULL, NULL,
  '2026-09-01T00:00:00Z', 'seed', '2026-10-01T00:00:00Z'),
 ('regulation/ul-1573', 'regulation', 'UL 1573', '[]', '{}', NULL, '', '{}', 'active', NULL, NULL,
  '2026-09-01T00:00:00Z', 'seed', '2026-10-01T00:00:00Z'),
 ('event/launch', 'event', 'Launch', '[]', '{}', NULL, '', '{}', 'active', NULL, NULL,
  '2026-09-01T00:00:00Z', 'seed', '2026-10-01T00:00:00Z');
INSERT INTO market_edges (id, src, rel, dst, since, until, confidence, properties, created, created_by, updated) VALUES
 ('edge-employs', 'company/acme', 'employs', 'person/jane', '2026-01-01', NULL, 'high', '{}',
  '2026-10-01T00:00:00Z', 'human:jane', '2026-10-01T00:00:00Z'),
 ('edge-regulated', 'company/acme', 'regulated_by', 'regulation/ul-1573', NULL, NULL, 'low', '{}',
  '2026-10-01T00:00:00Z', 'seed', '2026-10-01T00:00:00Z'),
 ('edge-competes', 'company/acme', 'competes_with', 'company/river', NULL, NULL, 'medium', '{}',
  '2026-10-01T00:00:00Z', 'seed', '2026-10-01T00:00:00Z');
"""
