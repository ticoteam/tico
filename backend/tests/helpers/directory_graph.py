"""A small Directory graph on a new hub, booted once and copied per test: Jane Ortiz works at Acme Lights, which River
Capital competes with and UL 1573 regulates."""

import sqlite3
from contextlib import closing

from backend.config import Settings
from backend.store import Store

SAMPLE = """
INSERT INTO entities (id, type, name, name_key, aliases, external_ids, tier, primary_domain, created_by,
  created_by_type, created_by_id, created_at, updated_at) VALUES
 ('company/acme', 'organization', 'Acme Lights', 'acme lights', '["Acme"]', '{"domain": "acme.example"}', 'core',
  'acme.example', 'human:jane', 'human', 'jane', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'),
 ('company/river', 'organization', 'River Capital', 'river capital', '[]', '{}', NULL, NULL, 'bot:librarian', 'bot',
  'librarian', '2026-09-01T00:00:00Z', '2026-10-01T00:00:00Z'),
 ('person/jane', 'person', 'Jane Ortiz', 'jane ortiz', '[]', '{}', NULL, NULL, 'bot:librarian', 'bot', 'librarian',
  '2026-09-01T00:00:00Z', '2026-10-01T00:00:00Z'),
 ('geography/united-states', 'place', 'United States', 'united states', '[]', '{}', NULL, NULL, 'seed', 'system',
  'seed', '2026-09-01T00:00:00Z', '2026-10-01T00:00:00Z'),
 ('regulation/ul-1573', 'document', 'UL 1573', 'ul 1573', '[]', '{}', NULL, NULL, 'seed', 'system', 'seed',
  '2026-09-01T00:00:00Z', '2026-10-01T00:00:00Z'),
 ('event/launch', 'event', 'Launch', 'launch', '[]', '{}', NULL, NULL, 'seed', 'system', 'seed',
  '2026-09-01T00:00:00Z', '2026-10-01T00:00:00Z');
INSERT INTO entity_values (id, entity_id, entity_attribute_id, single_value, option_key, source_type, confidence,
  updated_at) VALUES
 ('val_company/acme', 'company/acme', 'attr_organization_kind', 1, 'company', 'market', 0.9, '2026-10-01T00:00:00Z'),
 ('val_company/river', 'company/river', 'attr_organization_kind', 1, 'company', 'market', 0.9, '2026-10-01T00:00:00Z');
INSERT INTO entity_links (id, from_entity_id, link_key, to_entity_id, valid_from_on, valid_until_on, confidence,
  properties, created_by, created_by_type, created_by_id, created_at, updated_at) VALUES
 ('edge-employs', 'person/jane', 'works_at', 'company/acme', '2026-01-01', NULL, 0.9, '{}', 'human:jane', 'human',
  'jane', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'),
 ('edge-regulated', 'regulation/ul-1573', 'regulates', 'company/acme', NULL, NULL, 0.3, '{}', 'seed', 'system', 'seed',
  '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'),
 ('edge-competes', 'company/acme', 'competes_with', 'company/river', NULL, NULL, 0.6, '{}', 'seed', 'system', 'seed',
  '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z');
"""


def boot(path):
    """A new hub with the sample graph."""
    Store(Settings(db_path=path)).initialize(seed_market=False)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(SAMPLE)
        conn.commit()


def copy_of(booted, path):
    """A private copy of a booted hub (sqlite's backup: every page, the WAL's included), so each check starts from the
    same boot without paying for its own."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(booted)) as source, closing(sqlite3.connect(path)) as target:
        source.backup(target)
    return path
