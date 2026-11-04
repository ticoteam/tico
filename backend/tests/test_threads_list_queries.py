"""The threads list reads a page of mixed threads in a flat number of statements: member rooms with unread, followed
roots, sub-threads, task threads, one DM per bot Ana owns, and group DMs. Each kind is seeded the way production makes it."""
import collections
import re

from backend import bot_settings as BS
from backend import hubdb
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.test_api import api, headers  # noqa: F401  (the fixture and its helper)
from backend.tests.test_health_reads import counted


def add_bots(api, start, count):
    """`count` more bots Ana owns, as a company has them: each gets a DM with Ana on her sidebar (bot_dm_unread)."""
    with api.app.state.store.transaction() as c:
        bots = {f"desk-{n}": {"name": f"desk-{n}", "runtime": "fake", "status": "active"}
                for n in range(start, start + count)}
        hubdb.sync_registry(c, bots, {"people": []})
        for slug, config in bots.items():
            c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?)", (slug, None, "ana"))
            BS.write(c, slug, config)
    return [f"bot:{slug}" for slug in bots]


def seed_bot_dms(api, bots):
    """Ana's DM with each bot, with a reply from the bot's side as Ana's own note (no turn runs here)."""
    for bot in bots:
        dm = api.post("/api/v2/threads/dm", json={"members": [bot], "title": "Dm", "body": "Hello"},
                      headers=headers("ana-test"))
        assert dm.status_code == 200, dm.text
        api.post(f"/api/v2/threads/{dm.json()['id']}/messages", json={"text": "Reply"}, headers=headers("ana-test"))


def seed_homes(api, bots):
    """Each bot's Home (made on first use), with a message in it from Ben that Ana has not read: the Home rows a
    teammate's sidebar lists for a bot she reads."""
    for bot in bots:
        home = api.get(f"/api/v2/threads/home/{bot}", headers=headers("ana-test"))
        assert home.status_code == 200, home.text
        api.post(f"/api/v2/threads/{home.json()['id']}/messages", json={"text": "Status"}, headers=headers("ben-test"))


def seed_group_dm(api):
    """A group DM of Ana, Ben and Cara with a reply from Ben, unread for Ana."""
    group = api.post("/api/v2/threads/dm", json={"members": ["human:ben", "human:cara"], "title": "Group",
                                                  "body": "Hello all"}, headers=headers("ana-test"))
    assert group.status_code == 200, group.text
    api.post(f"/api/v2/threads/{group.json()['id']}/messages", json={"text": "Reply"}, headers=headers("ben-test"))


def seed_mixed(api, start, count):
    """`count` roots per kind, Ana's view: member rooms with ben and cara (some with unread, some followed), a
    sub-thread under some, and a task of Ben's with unread for Ana. Each room has messages from all three people."""
    for i in range(start, start + count):
        starter = "ben-test" if i % 2 else "ana-test"
        room = api.post("/api/v2/threads", json={"title": f"Room {i}", "audience": "members",
                                                  "members": ["human:ana", "human:ben", "human:cara"],
                                                  "body": f"Opening {i}"}, headers=headers(starter))
        assert room.status_code == 200, room.text
        room_id, first = room.json()["id"], room.json()["message"]["id"]
        for token in ("ben-test", "cara-test", "ana-test"):
            api.post(f"/api/v2/threads/{room_id}/messages", json={"text": f"Note {i}"}, headers=headers(token))
        if i % 3 == 0:
            sub = api.post("/api/v2/threads", json={"title": f"Sub {i}", "audience": "members",
                                                     "members": ["human:ana", "human:ben"], "parent_message_id": first,
                                                     "body": "A sub-thread"}, headers=headers("ben-test"))
            assert sub.status_code == 200, sub.text
        if i % 2 == 0:
            api.put(f"/api/v2/threads/{room_id}/follow", headers=headers("ana-test"))
        if i % 4 == 0:
            api.put(f"/api/v2/threads/{room_id}/follow", headers=headers("cara-test"))
        if i % 5 == 0:
            task = api.post("/api/v2/tasks", json={"owner": "human:ben", "title": f"Task {i}", "body": "Details."},
                            headers=headers("ana-test"))
            assert task.status_code == 200, task.text
            body = task.json().get("task", task.json())
            api.post(f"/api/v2/threads/{body['conversation_id']}/messages", json={"text": "Update"},
                     headers=headers("ben-test"))
        if i % 7 == 0:
            api.post(f"/api/v2/threads/{room_id}/messages", json={"text": "Mention for Ben"},
                     headers=headers("cara-test"))


def repeated(trace, top=40):
    """The statements a request ran more than once, by text with its literals blanked: (count, text)."""
    counts = collections.Counter(re.sub(r"'[^']*'|\b[0-9a-f-]{36}\b|\b\d+\b", "?", " ".join(q.split()))[:150]
                                 for q in trace)
    return [(n, text) for text, n in counts.most_common(top) if n > 1]


def test_the_list_reads_the_same_statements_however_many_threads_and_bots_it_lists(api, monkeypatch):  # noqa: F811
    desks = add_bots(api, 0, 4)
    seed_bot_dms(api, ["bot:coo", "bot:ops", "bot:finance", "bot:doc-updater"] + desks)
    seed_homes(api, desks)
    seed_group_dm(api)
    seed_mixed(api, 0, 10)
    counted(api, monkeypatch, "/api/v2/threads?limit=100")                 # warm
    few_trace = []
    few_body, few = counted(api, monkeypatch, "/api/v2/threads?limit=100", trace=few_trace)
    assert len(few_body["threads"]) >= 10 and len(few_body["dms"]) >= 5, few_body
    desks = add_bots(api, 4, 12)                                           # 16 bots now: a company's bot fleet
    seed_bot_dms(api, desks)
    seed_homes(api, desks)
    seed_mixed(api, 10, 20)
    counted(api, monkeypatch, "/api/v2/threads?limit=100")                 # warm again: the writes emptied the caches
    many_trace = []
    many_body, many = counted(api, monkeypatch, "/api/v2/threads?limit=100", trace=many_trace)
    assert len(many_body["threads"]) >= 30 and len(many_body["dms"]) >= 15, many_body
    # Before the batch (origin/main): 110 statements at this size and 190 at three times the threads and bots, the
    # growth a thread's membership, DM and task reads made; now 53 at both.
    assert many - few <= 10, (few, many, repeated(many_trace))
