"""The public places pack: lookups and ancestors, and idempotent writes of referenced places into the market tables."""

import sqlite3

import pytest

from backend import hubdb as H
from backend.entities.places import pack
from backend.entities.places.market_adapter import market_adapter


def _market_tables() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(H.MARKET_SCHEMA)
    H.ensure_market_reads(conn)
    return conn


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_los_angeles_reaches_county_state_and_country_and_names_resolve():
    (city,) = pack.find("LA", kind="city")
    assert city.id == "place:us-ca-0644000"
    assert [place.id for place in pack.ancestors(city.id)] == ["place:us-ca-06037", "place:us-ca", "place:us"]
    assert [place.id for place in pack.find("Calif.", kind="state")] == ["place:us-ca"]
    assert [place.id for place in pack.find("Los Angeles", within="place:us")] == ["place:us-ca-0644000"]
    # Georgia is both a country and a state; results come country first, and the caller picks by kind.
    assert [place.id for place in pack.find("Georgia")] == ["place:ge", "place:us-ga"]


def test_ensure_places_writes_only_the_requested_places_and_their_ancestors_and_is_idempotent():
    conn = _market_tables()
    adapter = market_adapter()
    created = pack.ensure_places(conn, ["place:us-ca-0644000"], adapter)
    assert sorted(created) == ["place:us", "place:us-ca", "place:us-ca-06037", "place:us-ca-0644000"]
    assert _count(conn, "market_entities") == 4
    assert conn.execute("SELECT count(*) FROM market_entities WHERE type = 'geography'").fetchone()[0] == 4
    edges = conn.execute("SELECT src, dst FROM market_edges WHERE rel = 'located_in'").fetchall()
    assert sorted(edges) == sorted(
        [
            ("place:us-ca", "place:us"),
            ("place:us-ca-0644000", "place:us-ca-06037"),
            ("place:us-ca-06037", "place:us-ca"),
        ]
    )

    assert pack.ensure_places(conn, ["place:us-ca-0644000"], adapter) == []
    assert _count(conn, "market_entities") == 4
    assert _count(conn, "market_edges") == 3

    # A second city shares the country row. New York city spans several counties, so it sits in the state.
    assert sorted(pack.ensure_places(conn, ["place:us-ny-3651000"], adapter)) == [
        "place:us-ny",
        "place:us-ny-3651000",
    ]
    assert _count(conn, "market_entities") == 6

    with pytest.raises(KeyError):
        pack.ensure_places(conn, ["place:us-ca-0644000", "place:nowhere"], adapter)
    assert _count(conn, "market_entities") == 6


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


def test_pack_size_is_sane():
    kinds = [place.kind for place in pack.load()]
    assert kinds.count("country") == 249
    assert kinds.count("state") == 51  # 50 states and DC
    assert kinds.count("county") >= 3100
    assert kinds.count("city") > 0
