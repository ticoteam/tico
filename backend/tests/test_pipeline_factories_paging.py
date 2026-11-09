"""GET /api/v2/factories is a page at a time and byte-capped (Track J4): each page's `next` cursor continues it until it is
null, and a page of long factories stops short of the limit rather than passing the cap."""

from backend.tests.test_api import api, headers  # noqa: F401  (api is a fixture)

STAMP = "2026-11-07T09:00:00.000000Z"


def add_factories(api, prefix, count, goal=""):
    with api.app.state.store.transaction() as c:
        for n in range(count):
            c.execute(
                "INSERT INTO factories (id, name, goal, owner_id, position, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (f"{prefix}-{n:03d}", f"{prefix} {n}", goal, None, 500000 + n, STAMP, STAMP),
            )
    return [f"{prefix}-{n:03d}" for n in range(count)]


def walk(api, limit):
    """Every factory the list gives, page by page, and how many each page held."""
    ids, sizes, cursor = [], [], None
    for _ in range(500):
        params = {"limit": limit, **({"after": cursor} if cursor else {})}
        body = api.get("/api/v2/factories", params=params, headers=headers("ana-test")).json()
        assert "next" in body, body.keys()
        ids += [f["id"] for f in body["factories"]]
        sizes.append(len(body["factories"]))
        cursor = body["next"]
        if cursor is None:
            return ids, sizes
    raise AssertionError("the factories never ended")


def test_the_factories_come_a_page_at_a_time_in_order(api):
    mine = add_factories(api, "page", 5)
    ids, sizes = walk(api, 2)
    assert max(sizes) <= 2 and len(sizes) >= 3
    assert len(ids) == len(set(ids))
    assert [i for i in ids if i in set(mine)] == mine


def test_a_page_of_long_factories_stops_before_the_byte_cap(api):
    mine = add_factories(api, "long", 40, goal="x" * 12_000)  # about 480 KB in all
    ids, sizes = walk(api, 200)
    assert sizes[0] < 40  # the first page is cut short by the cap, not by the limit
    assert len(ids) == len(set(ids))
    assert set(mine) <= set(ids)
