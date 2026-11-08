# pyright: strict
"""The place adapter for the Directory's tables: entities and entity_links.

This file is the only place that knows those tables for places. A place is an `entities` row of type `geography` whose
id is the place id. A containment is an `entity_links` row with link_key `located_in`: from_entity_id is the inner
place, to_entity_id the outer one. `located_in` is one of market.RELATIONS, so the Market graph and the Librarian read
and write it like any other link.

Server code only. It skips the Librarian's writer gate and writes no history rows, the way market seeds do: places are
reference data. The entities triggers keep entity_identifiers and the full-text index in step with each insert.
"""

from __future__ import annotations

import json
import sqlite3

from backend import hubdb as H
from backend.entities.places.pack import Place

ACTOR = "places-pack"
LOCATED_IN = "located_in"
LOCATED_IN_CONFIDENCE = 0.9


class DirectoryPlaces:
    def upsert_place(self, conn: sqlite3.Connection, place: Place) -> bool:
        """Insert the geography entity for a place. An existing row is left as it is."""
        now = H.now()
        external = {
            key: value
            for key, value in (
                ("usps", place.usps),
                ("iso_alpha3", place.iso_alpha3),
                ("census_geoid", place.census_geoid),
            )
            if value
        }
        properties = {"place_kind": place.kind}
        if place.governing_body:
            properties["governing_body"] = place.governing_body
        cursor = conn.execute(
            "INSERT OR IGNORE INTO entities (id, type, name, name_key, aliases, external_ids, tier, summary, properties, "
            "status, merged_into_id, last_verified_at, primary_domain, created_by, created_by_type, created_by_id, "
            "created_at, updated_at) "
            "VALUES (?, 'geography', ?, ?, ?, ?, NULL, '', ?, 'active', NULL, NULL, NULL, ?, 'system', ?, ?, ?)",
            (
                place.id,
                place.name,
                place.name.strip().lower(),
                json.dumps(list(place.aliases), sort_keys=True, ensure_ascii=False),
                json.dumps(external, sort_keys=True, ensure_ascii=False),
                json.dumps(properties, sort_keys=True, ensure_ascii=False),
                ACTOR,
                ACTOR,
                now,
                now,
            ),
        )
        return cursor.rowcount == 1

    def locate(self, conn: sqlite3.Connection, place_id: str, parent_id: str) -> bool:
        """Add the located_in link from a place to its parent, once."""
        now = H.now()
        cursor = conn.execute(
            "INSERT INTO entity_links (id, from_entity_id, link_key, to_entity_id, valid_from_on, valid_until_on, "
            "confidence, properties, created_by, created_by_type, created_by_id, created_at, updated_at) "
            "SELECT :id, :src, :key, :dst, NULL, NULL, :confidence, '{}', :actor, 'system', :actor, :now, :now "
            "WHERE NOT EXISTS (SELECT 1 FROM entity_links WHERE from_entity_id = :src AND link_key = :key "
            "AND to_entity_id = :dst)",
            {
                "id": H.new_id(),
                "src": place_id,
                "key": LOCATED_IN,
                "dst": parent_id,
                "confidence": LOCATED_IN_CONFIDENCE,
                "actor": ACTOR,
                "now": now,
            },
        )
        return cursor.rowcount == 1


def directory_adapter() -> DirectoryPlaces:
    """The adapter ensure_places takes for the Directory's tables."""
    return DirectoryPlaces()
