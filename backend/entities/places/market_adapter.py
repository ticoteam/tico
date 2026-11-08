# pyright: strict
"""The place adapter for the current market tables (backend/market.py).

This file is the only place that knows those tables. A place is a `market_entities` row of type `geography` whose id is
the place id. A containment is a `market_edges` row with rel `located_in` (src: the inner place, dst: the outer one).
When the `entities` and `entity_links` tables land, point the two statements in MarketPlaces at them and nothing else
changes.

Server code only. It skips the Librarian's writer gate and writes no history rows, the way market seeds do: places are
reference data. `located_in` is not in market.RELATIONS, so the Librarian cannot write it and the Market graph shows it
as a raw relation until the Directory's link types land. The rest of backend/market.py is untouched.
"""

from __future__ import annotations

import json
import sqlite3

from backend import hubdb as H
from backend.entities.places.pack import Place

ACTOR = "places-pack"


class MarketPlaces:
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
            "INSERT OR IGNORE INTO market_entities (id, type, name, aliases, external_ids, tier, summary, properties, "
            "status, merged_into, last_verified_at, created_at, created_by, updated_at) "
            "VALUES (?, 'geography', ?, ?, ?, NULL, '', ?, 'active', NULL, NULL, ?, ?, ?)",
            (
                place.id,
                place.name,
                json.dumps(list(place.aliases), sort_keys=True, ensure_ascii=False),
                json.dumps(external, sort_keys=True, ensure_ascii=False),
                json.dumps(properties, sort_keys=True, ensure_ascii=False),
                now,
                ACTOR,
                now,
            ),
        )
        return cursor.rowcount == 1

    def locate(self, conn: sqlite3.Connection, place_id: str, parent_id: str) -> bool:
        """Add the located_in edge from a place to its parent, once."""
        now = H.now()
        cursor = conn.execute(
            "INSERT INTO market_edges (id, src, rel, dst, valid_from_on, valid_until_on, confidence, properties, "
            "created_at, created_by, updated_at) "
            "SELECT ?, ?, 'located_in', ?, NULL, NULL, 'high', '{}', ?, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM market_edges WHERE src = ? AND rel = 'located_in' AND dst = ?)",
            (H.new_id(), place_id, parent_id, now, ACTOR, now, place_id, parent_id),
        )
        return cursor.rowcount == 1


def market_adapter() -> MarketPlaces:
    """The adapter ensure_places takes for the current market tables."""
    return MarketPlaces()
