"""The menu reads Catch up's count, not its page (spec: the menu counts Catch up without loading it): the count route's
`mentions` are exactly the mention items among Catch up's first 99, in order, through the same checks, for a plain
member who may not read every thread, who lost a DM after being mentioned in it, and who has over 200 unread in one
thread (#530); and a slow read's flight sample names where its time went. Fictional people only: ana, ben, cara."""

import json

import pytest

from backend import flight, thread_rail, threads
from backend.store import H
from backend.tests.helpers.api import as_member, get, headers  # noqa: F401  (the fixture and its helpers)
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)


@pytest.fixture
def recorded(tmp_path, api_database):
    """The `api` fixture's app with the flight recorder on. Only this body imports the app, as conftest's `api` does."""
    from backend.tests.helpers.api_app import api_client

    yield from api_client(tmp_path, api_database, flight_recorder=True)


def say(api, thread_id, text, token="ana-test"):
    response = api.post(f"/api/v2/threads/{thread_id}/messages", json={"text": text}, headers=headers(token))
    assert response.status_code == 200, response.text


def start(api, title, members, audience="members", token="ana-test"):
    response = api.post(
        "/api/v2/threads",
        json={"title": title, "audience": audience, "members": list(members)},
        headers=headers(token),
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def page_mentions(api, token="ben-test"):
    """Catch up's first 99 items (as many pages as it takes), and the mentions among them, in order."""
    items, cursor = [], None
    while len(items) < thread_rail.CATCH_UP_SHOWN:
        path = f"threads/catch-up?limit={thread_rail.CATCH_UP_MAX_LIMIT}" + (f"&cursor={cursor}" if cursor else "")
        page = get(api, path, token)
        items += page["items"]
        cursor = page["next_cursor"]
        if not cursor:
            break
    items = items[: thread_rail.CATCH_UP_SHOWN]
    return [i["thread"]["id"] for i in items], [i["thread"] for i in items if i["mention"]]


def test_mentions_are_catch_ups_mention_items_through_the_same_checks(api):
    as_member(api, "ben@acme.example")  # the fixture makes Ben an admin, who may read more than a member
    asks = start(api, "Launch", ["human:ben", "human:cara"])
    say(api, asks, "@ben can you check the numbers")
    plain = start(api, "Numbers", ["human:ben"])
    say(api, plain, "Numbers are in")
    dm = start(api, "", ["human:ben"], audience="personal")
    say(api, dm, "@ben a quick one")
    task_room = start(api, "Task room", ["human:ben"])
    with api.app.state.store.transaction() as c:  # a task's room: the menu leaves it to the task
        task = c.execute("SELECT id FROM tasks LIMIT 1").fetchone()
        if task:
            c.execute("UPDATE conversations SET task_id=? WHERE id=?", (task[0], task_room))
    say(api, task_room, "@ben over to you")
    # A thread Ben is not in, mentioning him: he may not read it, so it is no item and no mention.
    closed = start(api, "Board only", ["human:cara"])
    say(api, closed, "@ben not for you")
    # A DM Ben was mentioned in, then taken out of: gone, and nothing names Cara through it.
    lost = start(api, "", ["human:ben"], audience="personal", token="cara-test")
    say(api, lost, "@ben before you go", token="cara-test")
    with api.app.state.store.transaction() as c:
        threads.remove_member(c, "human:cara", lost, "human:ben")
    # Over 200 unread in one thread, with a mention (#530): the walk's roots are today's.
    busy = start(api, "Busy", ["human:ben"])
    with api.app.state.store.transaction() as c:
        for n in range(230):
            threads.post(c, "human:ana", busy, f"m{n}")
    say(api, busy, "@ben the last one")

    alone = get(api, "threads/catch-up/count", "ben-test")
    ids, mentioned = page_mentions(api)
    assert alone["ids"] == ids and alone["count"] == len(ids)
    assert [m["id"] for m in alone["mentions"]] == [t["id"] for t in mentioned]
    assert {asks, dm, busy} <= {m["id"] for m in alone["mentions"]} and plain in ids
    assert closed not in ids and lost not in ids
    assert "human:cara" not in json.dumps(alone["mentions"])
    # Each mention carries what the thread's view gives the menu: its audience, its task, a DM's other member.
    views = {t["id"]: t for t in mentioned}
    for m in alone["mentions"]:
        view = views[m["id"]]
        assert m["audience"] == view["audience"] and m["task_id"] == view.get("task_id")
        others = [p["actor"] for p in view.get("dm_with") or []]
        assert m["with"] == (others[0] if view["audience"] == "personal" and others else None)
    assert next(m for m in alone["mentions"] if m["id"] == dm)["with"] == "human:ana"


def test_a_slow_catch_up_read_names_the_time_of_its_parts(recorded, monkeypatch):
    """Criterion 4: the flight sample of a slow Catch up or count read names flags, walk, cards (or mentions) and
    encoding, and with sign-in they add up to within 10% of its time; `rest` is what no part names."""
    api = recorded
    with api.app.state.store.transaction() as c:
        for n in range(60):
            root = threads.create(c, "human:ana", title=f"T{n}", members=["human:ben"])
            ids = [threads.post(c, "human:ana", root["id"], f"m{k}")["id"] for k in range(5)]
            for part in H.chunks(ids):
                c.execute(f"UPDATE messages SET restricted=1 WHERE id IN ({','.join('?' * len(part))})", part)
    recorder = api.app.state.flight
    seen = []
    kept = recorder.request

    def request(route, caller, status, ms, nbytes=0, actor="", cost=None, client=""):
        if "catch-up" in route:
            seen.append((route, ms, dict(cost[4])))
        return kept(route, caller, status, ms, nbytes, actor, cost, client)

    monkeypatch.setattr(recorder, "request", request)
    monkeypatch.setattr(flight, "SLOW_MS", 0)  # every read is a slow sample
    for _ in range(3):  # warm: the first read of a test pays for imports and caches
        get(api, "threads/catch-up?limit=20", "ben-test")
        get(api, "threads/catch-up/count", "ben-test")
    seen.clear()
    get(api, "threads/catch-up?limit=20", "ben-test")
    get(api, "threads/catch-up/count", "ben-test")
    page, count = seen
    assert {"flags", "walk", "cards", "encode", "auth"} <= set(page[2])
    assert {"flags", "walk", "mentions", "encode", "auth"} <= set(count[2])
    for _, ms, parts in (page, count):
        assert sum(parts.values()) >= 0.9 * ms, (ms, parts)
    # The sample the recorder keeps carries them, with `rest`.
    recorder.drain()
    kept_slow = [s for s in recorder.slow if "catch-up" in s[1]]
    assert kept_slow and all(s[-1] and "rest" in json.loads(s[-1]) for s in kept_slow)
