"""The menu reads Catch up's count route, not its page (spec #628): the count route names the roots among Catch up's
first 99 items that mention the reader (`mentions`, in Catch up's order, each with its audience and, for a 1:1 DM, the
other member), so the menu's asks need no card. And a slow Catch up or count read names where its time went (flags,
walk, cards, encode, sign-in) in the flight recorder's sample. Fictional people only: ana (owner), ben, cara."""

import dataclasses
import json
import sqlite3
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend import flight, thread_rail, threads
from backend.store import H
from backend.tests.helpers.api import as_member, get, headers  # noqa: F401  (the fixture and its helpers)
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)

HIDE = "UPDATE messages SET restricted=1, legacy_audience='shared:nobody' WHERE id IN ({})"


def say(api, thread_id, text, token="ana-test"):
    response = api.post(f"/api/v2/threads/{thread_id}/messages", json={"text": text}, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def members_thread(api, title, members=("human:ben", "human:cara"), token="ana-test"):
    payload = {"title": title, "audience": "members", "members": list(members)}
    response = api.post("/api/v2/threads", json=payload, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def group_dm(api, members, title, token="ana-test"):
    response = api.post("/api/v2/threads/dm", json={"members": list(members), "title": title}, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def catch_up_mentions(api, token="ben-test"):
    """The roots among Catch up's first CATCH_UP_SHOWN items, over its pages, that mention the reader, in order."""
    items, cursor = [], None
    while len(items) < thread_rail.CATCH_UP_SHOWN:
        path = f"threads/catch-up?limit={thread_rail.CATCH_UP_MAX_LIMIT}" + (f"&cursor={cursor}" if cursor else "")
        page = get(api, path, token)
        items += page["items"]
        cursor = page["next_cursor"]
        if not cursor:
            break
    return [it["thread"]["id"] for it in items[: thread_rail.CATCH_UP_SHOWN] if it["mention"]]


def test_the_count_names_the_mention_roots_catch_up_lists_in_its_order(api):
    """Ben, a plain member: threads that mention him among threads that only have news, a 1:1 DM from Cara that
    mentions him, a thread whose mention he may not read, a group DM he was mentioned in and then removed from, and a
    thread with over 200 unread for him that mentions him. The count route's `mentions` are Catch up's mention items,
    in order; the DM names Cara, by id only; the hidden mention and the DM he lost are in neither, and no name is."""
    as_member(api, "ben@acme.example")
    plain = [members_thread(api, f"News {n}") for n in range(4)]
    for t in plain:
        say(api, t["id"], "Numbers are in")
    mentioned = [members_thread(api, f"Ask {n}") for n in range(3)]
    for t in mentioned:
        say(api, t["id"], "@ben can you look")
    dm = api.get("/api/v2/threads/dm", params={"with": "human:ben"}, headers=headers("cara-test")).json()
    say(api, dm["id"], "@ben quick one", token="cara-test")
    hidden = members_thread(api, "Hidden ask")
    hidden_msg = say(api, hidden["id"], "@ben not for you")
    lost = group_dm(api, ["human:ben", "human:cara"], "Lost")
    say(api, lost["id"], "@ben before you go")
    gone = api.delete(f"/api/v2/threads/{lost['id']}/members/human:ben", headers=headers("ana-test"))
    assert gone.status_code == 200, gone.text
    busy = members_thread(api, "Busy")
    with api.app.state.store.transaction() as c:
        ids = [threads.post(c, "human:ana", busy["id"], f"m{n}")["id"] for n in range(210)]
        for part in H.chunks(ids):
            c.execute(f"UPDATE messages SET restricted=1 WHERE id IN ({','.join('?' * len(part))})", part)
        c.execute(HIDE.format("?"), (hidden_msg["id"],))
    say(api, busy["id"], "@ben the last one")

    raw = api.get("/api/v2/threads/catch-up/count", headers=headers("ben-test"))
    assert raw.status_code == 200, raw.text
    answer = raw.json()
    expected = catch_up_mentions(api)
    assert [m["id"] for m in answer["mentions"]] == expected
    assert {t["id"] for t in mentioned} | {dm["id"], busy["id"]} == set(expected)
    assert hidden["id"] not in expected and lost["id"] not in json.dumps(answer)
    # The count and its roots are the count route's as before.
    assert answer["count"] == len(answer["ids"]) and set(expected) <= set(answer["ids"])
    by_id = {m["id"]: m for m in answer["mentions"]}
    assert by_id[dm["id"]]["audience"] == "personal" and by_id[dm["id"]]["with"] == "human:cara"
    assert by_id[busy["id"]]["audience"] == "members" and "with" not in by_id[busy["id"]]
    assert '"name"' not in raw.text and "Cara" not in raw.text


def test_every_mentioned_thread_is_named_not_only_the_first_page(api):
    """25 threads mention Ben: the count route names all 25, where the first page of 20 cards held only 20."""
    made = [members_thread(api, f"Ask {n}") for n in range(25)]
    for t in made:
        say(api, t["id"], "@ben please")
    answer = get(api, "threads/catch-up/count", "ben-test")
    assert {m["id"] for m in answer["mentions"]} == {t["id"] for t in made}


@pytest.fixture
def recorded(tmp_path, api_database):
    """The api fixture's company on an app with the flight recorder on, and every request the recorder is handed."""
    from backend.app import create_app
    from backend.tests.helpers.api import api_settings
    from backend.tests.helpers.api_app import seed

    settings = dataclasses.replace(api_settings(tmp_path), flight_recorder=True)
    if api_database is not None:
        with closing(sqlite3.connect(api_database.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(settings.db_path)) as target:
                source.backup(target)
    app = create_app(settings)
    seen = []
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            seed(c)
        rec = app.state.flight
        handed = rec.request

        def request(route, caller, status, ms, nbytes=0, actor="", cost=None, client=""):
            seen.append((route, ms, cost))
            return handed(route, caller, status, ms, nbytes, actor, cost, client)

        rec.request = request
        yield client, seen


@pytest.mark.parametrize(
    ("path", "route", "names"),
    [
        ("threads/catch-up?limit=20", "GET /api/v2/threads/catch-up", {"flags", "walk", "cards", "encode"}),
        ("threads/catch-up/count", "GET /api/v2/threads/catch-up/count", {"flags", "walk", "mentions", "encode"}),
    ],
)
def test_a_catch_up_read_names_where_its_time_went(recorded, path, route, names):
    """A catch-up read names its own parts in its slow sample, and the sign-in is a part too."""
    api, seen = recorded
    with api.app.state.store.transaction() as c:
        for n in range(40):
            root = threads.create(c, "human:ana", title=f"T{n}", audience="members", members=["human:ben"])
            for m in range(8):
                threads.post(c, "human:ana", root["id"], ("@ben " if n % 5 == 0 and not m else "") + f"m{m}")
    seen.clear()
    get(api, path, "ben-test")
    ((_, _, cost),) = [s for s in seen if s[0] == route]
    parts = cost[4]
    assert names | {"auth"} <= set(parts), parts


def test_a_slow_samples_parts_reach_the_database_the_view_and_the_file(recorded, monkeypatch):
    api, _ = recorded
    rec = api.app.state.flight
    rec.request(
        "GET /api/v2/threads/catch-up", "human", 200, 1500, 10, "human:ben", [9.0, 0.0, 3, {}, {"walk": 1200.4}]
    )
    rec.request("GET /api/v2/tasks", "human", 200, 1600, 10, "human:ben", [9.0, 0.0, 3, {}])  # no parts named
    minute = int(time.time()) // 60 * 60
    rec.drain(minute)
    taken = rec.take(minute)
    store = api.app.state.store
    with store.transaction() as c:
        flight.write(c, taken)
        slow = flight.view(c, minutes=10, sections={"slow"}, now=int(time.time()) + 60)["slow"]
    named = [r for r in slow if r["route"] == "GET /api/v2/threads/catch-up"]
    assert named and named[0]["parts"] == {"walk": 1200.4}
    assert all("parts" not in r for r in slow if r["route"] == "GET /api/v2/tasks")
    filed = [r for r in rec.pop_slow() if r["route"] == "GET /api/v2/threads/catch-up"]
    assert filed and filed[0]["parts"] == {"walk": 1200.4}
