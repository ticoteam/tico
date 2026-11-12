"""The public places pack: lookups and ancestors, and idempotent writes of referenced places into the Directory's tables."""

import sqlite3
from collections.abc import Iterator

import pytest

from backend import hubdb as H
from backend.entities.places import pack
from backend.entities.places.adapter import directory_adapter


@pytest.fixture
def hub(tmp_path) -> Iterator[sqlite3.Connection]:
    """A hub file that every migration has run on, the Directory's tables included. Nothing is hand-made."""
    conn = H.connect(tmp_path / "hub.db")
    yield conn
    conn.close()


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _rows(conn: sqlite3.Connection, sql: str) -> list[tuple]:
    """hub.db rows are sqlite3.Row; the tests compare plain tuples."""
    return [tuple(row) for row in conn.execute(sql).fetchall()]


def test_los_angeles_reaches_county_state_and_country_and_names_resolve():
    (city,) = pack.find("LA", kind="city")
    assert city.id == "place:us-ca-0644000"
    assert [place.id for place in pack.ancestors(city.id)] == ["place:us-ca-06037", "place:us-ca", "place:us"]
    assert [place.id for place in pack.find("Calif.", kind="state")] == ["place:us-ca"]
    assert [place.id for place in pack.find("Los Angeles", within="place:us")] == ["place:us-ca-0644000"]
    # Georgia is both a country and a state; results come country first, and the caller picks by kind.
    assert [place.id for place in pack.find("Georgia")] == ["place:ge", "place:us-ga"]


def test_ensure_places_writes_only_the_requested_places_and_their_ancestors_and_is_idempotent(hub):
    conn = hub
    adapter = directory_adapter()
    created = pack.ensure_places(conn, ["place:us-ca-0644000"], adapter)
    assert sorted(created) == ["place:us", "place:us-ca", "place:us-ca-06037", "place:us-ca-0644000"]
    assert _count(conn, "entities") == 4
    assert conn.execute("SELECT count(*) FROM entities WHERE type = 'place'").fetchone()[0] == 4
    links = _rows(conn, "SELECT from_entity_id, to_entity_id FROM entity_links WHERE link_key = 'located_in'")
    assert sorted(links) == sorted(
        [
            ("place:us-ca", "place:us"),
            ("place:us-ca-0644000", "place:us-ca-06037"),
            ("place:us-ca-06037", "place:us-ca"),
        ]
    )

    assert pack.ensure_places(conn, ["place:us-ca-0644000"], adapter) == []
    assert _count(conn, "entities") == 4
    assert _count(conn, "entity_links") == 3

    # A second city shares the country row. New York city spans several counties, so it sits in the state.
    assert sorted(pack.ensure_places(conn, ["place:us-ny-3651000"], adapter)) == [
        "place:us-ny",
        "place:us-ny-3651000",
    ]
    assert _count(conn, "entities") == 6

    with pytest.raises(KeyError):
        pack.ensure_places(conn, ["place:us-ca-0644000", "place:nowhere"], adapter)
    assert _count(conn, "entities") == 6


def test_ensure_places_fills_the_directory_columns_of_a_migrated_hub(hub):
    pack.ensure_places(hub, ["place:us-ca-06037"], directory_adapter())
    assert _rows(
        hub,
        "SELECT id, type, name, name_key, merged_into_id, primary_domain, created_by_type, created_by_id "
        "FROM entities WHERE id = 'place:us-ca'",
    ) == [("place:us-ca", "place", "California", "california", None, None, "system", "places-pack")]
    assert _rows(
        hub,
        "SELECT from_entity_id, link_key, to_entity_id, confidence, created_by_type, created_by_id "
        "FROM entity_links WHERE from_entity_id = 'place:us-ca-06037'",
    ) == [("place:us-ca-06037", "located_in", "place:us-ca", 0.9, "system", "places-pack")]


def test_ids_are_unique_and_every_parent_is_in_the_pack():
    rows = pack.load()
    ids = [place.id for place in rows]
    assert len(ids) == len(set(ids))
    known = set(ids)
    for place in rows:
        if place.kind == "country":
            assert place.parent is None
        else:
            assert place.parent in known, place.id
