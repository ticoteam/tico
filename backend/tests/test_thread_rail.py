"""Pins, the rail, Browse and Catch up (docs/dev/threads-contract.md §10.1) over real SQLite: a pin needs read and only
a manager pins for everyone; a reader unpins a pin for everyone for themselves alone; the rail holds the threads one is
in and never a 1:1 DM or a Home; a hidden thread never shows in any of them; Catch up puts mentions first, shows new
messages in tree order and pages; every list keeps to its byte budget; a group chat needs a name, for people and bots.
Fictional people only: ana (owner), ben (bot admin) and cara."""

import importlib
import sqlite3
from pathlib import Path

import pytest

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.test_api import api, headers  # noqa: F401  (the fixture and its helper)
from backend.tests.test_thread_events import cursor, read_as, who
from backend import thread_rail, threads
from backend.config import Settings
from backend.store import Problem, Store

FIXTURE = Path(__file__).parent / "fixtures" / "schema_old_main.sql"


def new_thread(api, title="Budget", members=("human:ben",), audience="members", token="ana-test", **extra):
    payload = {"title": title, "audience": audience, **extra}
    if audience == "members":
        payload["members"] = list(members)
    response = api.post("/api/v2/threads", json=payload, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def say(api, thread_id, text, token="ana-test", **extra):
    response = api.post(f"/api/v2/threads/{thread_id}/messages", json={"text": text, **extra}, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def post_as(api, thread_id, actor, text, **kw):
    with api.app.state.store.transaction() as c:
        return threads.post(c, actor, thread_id, text, **kw)


def pin(api, thread_id, token, method="put"):
    return getattr(api, method)(f"/api/v2/threads/{thread_id}/pin", headers=headers(token))


def everyone(api, thread_id, value, token="ana-test"):
    return api.patch(f"/api/v2/threads/{thread_id}", json={"pinned_for_everyone": value}, headers=headers(token))


def listed(api, path, token, **params):
    response = api.get(f"/api/v2/threads/{path}", params=params, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def ids(rows):
    return [r["id"] for r in rows]


def read_up(api, thread_id, token):
    last = api.get(f"/api/v2/threads/{thread_id}", headers=headers(token)).json()["last_seq"]
    assert api.post(f"/api/v2/threads/{thread_id}/read", json={"seq": last}, headers=headers(token)).status_code == 200


def group_dm(api, members, title, token="ana-test"):
    response = api.post("/api/v2/threads/dm", json={"members": list(members), "title": title}, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def test_a_pin_needs_read_is_the_readers_own_and_a_sub_thread_is_refused(api):
    thread = new_thread(api)
    refused = pin(api, thread["id"], "cara-test")
    assert refused.status_code == 404 and thread["id"] not in refused.text
    got = pin(api, thread["id"], "ben-test")
    assert got.status_code == 200, got.text
    assert got.json()["me"]["pinned"] is True and got.json()["pinned_for_everyone"] is False
    assert api.get(f"/api/v2/threads/{thread['id']}", headers=headers("ana-test")).json()["me"]["pinned"] is False
    topic = say(api, thread["id"], "First topic")
    sub = new_thread(api, title="Sub", members=(), parent_message_id=topic["id"])
    assert pin(api, sub["id"], "ben-test").status_code == 422
    assert pin(api, thread["id"], "ben-test", "delete").json()["me"]["pinned"] is False
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM thread_pins WHERE thread_id=?", (thread["id"],)).fetchone()[0] == 0
        audits = c.execute("SELECT COUNT(*) FROM audit_log WHERE object=?", ("thread:" + thread["id"],)).fetchone()[0]
    assert audits >= 2


def test_only_a_manager_pins_for_everyone_and_never_a_one_to_one_dm_a_home_a_task_or_a_meeting(api):
    thread = new_thread(api, members=("human:ben", "human:cara"))
    assert everyone(api, thread["id"], True, "cara-test").status_code == 403
    got = everyone(api, thread["id"], True)
    assert got.status_code == 200, got.text
    assert got.json()["pinned_for_everyone"] is True
    assert api.get(f"/api/v2/threads/{thread['id']}", headers=headers("cara-test")).json()["me"]["pinned"] is True
    dm = api.get("/api/v2/threads/dm", params={"with": "human:ben"}, headers=headers("ana-test")).json()
    home = api.get("/api/v2/threads/home/human:ana", headers=headers("ana-test")).json()
    for refused in (dm, home):
        answer = everyone(api, refused["id"], True)
        assert answer.status_code == 422 and answer.json()["error"]["code"] == "pin_everyone"
    assert everyone(api, group_dm(api, ["human:ben", "human:cara"], "Ops")["id"], True).status_code == 200
    for audience in ("task", "meeting"):
        other = new_thread(api, title=audience)
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE conversations SET audience=? WHERE id=?", (audience, other["id"]))
            with pytest.raises(Problem) as caught:
                thread_rail.set_pinned_for_everyone(c, "human:ana", other["id"], True)
        assert caught.value.status == 422 and caught.value.code == "pin_everyone"


def test_a_pin_for_everyone_reaches_every_reader_and_one_reader_unpins_it_for_themselves(api):
    hall = new_thread(api, title="All hands", audience="company")
    assert everyone(api, hall["id"], True).status_code == 200
    for token in ("ben-test", "cara-test"):          # neither ever joined it
        assert hall["id"] in ids(listed(api, "rail", token)["pinned"])
    assert pin(api, hall["id"], "cara-test", "delete").json()["me"]["pinned"] is False
    assert hall["id"] not in ids(listed(api, "rail", "cara-test")["pinned"] + listed(api, "rail", "cara-test")["threads"])
    assert hall["id"] in ids(listed(api, "rail", "ben-test")["pinned"])
    # Taking the pin off drops the unpin; pinning it again reaches cara too.
    assert everyone(api, hall["id"], False).json()["pinned_for_everyone"] is False
    assert everyone(api, hall["id"], True).status_code == 200
    assert hall["id"] in ids(listed(api, "rail", "cara-test")["pinned"])


def test_the_rail_is_the_threads_one_is_in_by_title_and_never_a_one_to_one_dm_or_a_home(api):
    beta = new_thread(api, title="Beta")
    alpha = new_thread(api, title="alpha")
    gamma = group_dm(api, ["human:ben", "human:cara"], "Gamma")
    zeta = new_thread(api, title="Zeta", audience="company")
    dm = api.get("/api/v2/threads/dm", params={"with": "human:ben"}, headers=headers("ana-test")).json()
    say(api, dm["id"], "Lunch?")
    home = api.get("/api/v2/threads/home/human:ben", headers=headers("ben-test")).json()
    rail = listed(api, "rail", "ben-test")
    assert ids(rail["threads"]) == [alpha["id"], beta["id"], gamma["id"]]
    assert rail["pinned"] == [] and rail["next_cursor"] is None
    assert dm["id"] not in str(rail) and home["id"] not in str(rail) and zeta["id"] not in str(rail)
    assert rail["dms"] == {"human:ana": 1} and isinstance(rail["catch_up"], int)
    followed = api.put(f"/api/v2/threads/{zeta['id']}/follow", json={}, headers=headers("ben-test"))
    assert followed.status_code == 200, followed.text
    seen, page = [], listed(api, "rail", "ben-test", limit=1)
    while True:
        seen += ids(page["threads"])
        if not page["next_cursor"]:
            break
        page = listed(api, "rail", "ben-test", limit=1, cursor=page["next_cursor"])
        assert page["pinned"] == [] and page["catch_up"] is None and page["dms"] == {}
    assert seen == [alpha["id"], beta["id"], gamma["id"], zeta["id"]]
    assert api.get("/api/v2/threads/rail", params={"cursor": "nope"}, headers=headers("ben-test")).status_code == 422


def test_a_hidden_thread_leaves_no_trace_in_the_rail_browse_or_catch_up(api):
    secret = new_thread(api, title="Launch secret", members=())
    say(api, secret["id"], "Board only", to="human:cara")
    everyone(api, secret["id"], True)
    hall = new_thread(api, title="Open house", audience="company")
    say(api, hall["id"], "Welcome all")
    for path in ("rail", "browse", "catch-up"):
        body = listed(api, path, "cara-test")
        assert secret["id"] not in str(body) and "Launch secret" not in str(body) and "Board only" not in str(body)
    browse = listed(api, "browse", "cara-test")["threads"]
    assert ids(browse) == [hall["id"]] and browse[0]["member_count"] >= 1
    assert ids(listed(api, "browse", "cara-test", q="OPEN")["threads"]) == [hall["id"]]
    assert listed(api, "browse", "cara-test", q="100%")["threads"] == []
    assert listed(api, "browse", "ana-test")["threads"] == []                    # ana is in it, so it is not Browse's
    api.put(f"/api/v2/threads/{hall['id']}/follow", json={}, headers=headers("cara-test"))
    assert listed(api, "browse", "cara-test")["threads"] == []
    assert hall["id"] in ids(listed(api, "rail", "cara-test")["threads"])


def test_catch_up_puts_mentions_first_shows_new_messages_in_tree_order_and_pages(api):
    plain = new_thread(api, title="Plain")
    first = say(api, plain["id"], "First topic")
    say(api, plain["id"], "Second topic")
    sub = new_thread(api, title="Under", members=(), parent_message_id=first["id"])
    say(api, sub["id"], "Under the first")
    asked = new_thread(api, title="Asked")
    post_as(api, asked["id"], "human:ana", "Can you check this?", to_actor="human:ben")
    caught = new_thread(api, title="Read")
    say(api, caught["id"], "Already seen")
    read_up(api, caught["id"], "ben-test")
    quiet = new_thread(api, title="Bots only")
    post_as(api, quiet["id"], "bot:ops", "Indexing finished")
    body = listed(api, "catch-up", "ben-test")
    assert [it["thread"]["id"] for it in body["items"]] == [asked["id"], plain["id"]]
    assert body["count"] == 2 and body["next_cursor"] is None
    assert body["items"][0]["mention"] is True and body["items"][1]["mention"] is False
    item = body["items"][1]
    assert [m["body"] for m in item["messages"]] == ["First topic", "Under the first", "Second topic"]
    assert [m["depth"] for m in item["messages"]] == [0, 1, 0]
    assert item["new_count"] == 3 and item["more"] == 0
    assert listed(api, "rail", "ben-test")["catch_up"] == 2
    page = listed(api, "catch-up", "ben-test", limit=1)
    assert [it["thread"]["id"] for it in page["items"]] == [asked["id"]] and page["count"] == 2
    rest = listed(api, "catch-up", "ben-test", limit=1, cursor=page["next_cursor"])
    assert [it["thread"]["id"] for it in rest["items"]] == [plain["id"]] and rest["next_cursor"] is None
    # Muting leaves a thread out unless it mentions the reader; reading it all clears it.
    api.put(f"/api/v2/threads/{plain['id']}/follow", json={"notify": "muted"}, headers=headers("ben-test"))
    assert [it["thread"]["id"] for it in listed(api, "catch-up", "ben-test")["items"]] == [asked["id"]]
    assert listed(api, "catch-up", "cara-test")["items"] == []


def test_catch_up_shows_twenty_messages_counts_the_rest_and_caps_its_count(api, monkeypatch):
    busy = new_thread(api, title="Busy")
    for n in range(23):
        say(api, busy["id"], f"Update {n}")
    other = new_thread(api, title="Other")
    say(api, other["id"], "Hello")
    body = listed(api, "catch-up", "ben-test")
    item = next(it for it in body["items"] if it["thread"]["id"] == busy["id"])
    assert len(item["messages"]) == thread_rail.CATCH_UP_MESSAGES and item["more"] == 3
    assert [m["body"] for m in item["messages"]][:2] == ["Update 0", "Update 1"]
    monkeypatch.setattr(thread_rail, "CATCH_UP_SHOWN", 1)
    capped = listed(api, "catch-up", "ben-test")
    assert capped["count"] == 1 and len(capped["items"]) == 2


def test_every_list_keeps_to_its_byte_budget_and_its_cursor_still_reaches_every_row(api, monkeypatch):
    made = [new_thread(api, title=f"Thread {n}", audience="company") for n in range(4)]
    for thread in made:
        say(api, thread["id"], "x" * 400)
        api.put(f"/api/v2/threads/{thread['id']}/follow", json={}, headers=headers("ben-test"))
    monkeypatch.setattr(threads, "MAX_PAGE_BYTES", 2400)
    for path, key in (("rail", "threads"), ("catch-up", "items")):
        seen, page = [], listed(api, path, "ben-test")
        assert len(page[key]) < len(made) and page["next_cursor"]
        while True:
            seen += [r.get("thread", r)["id"] for r in page[key]]
            if not page["next_cursor"]:
                break
            page = listed(api, path, "ben-test", cursor=page["next_cursor"])
        assert sorted(seen) == sorted(ids(made))
    seen, page = [], listed(api, "browse", "cara-test")
    assert len(page["threads"]) < len(made)
    while True:
        seen += ids(page["threads"])
        if not page["next_cursor"]:
            break
        page = listed(api, "browse", "cara-test", cursor=page["next_cursor"])
    assert sorted(seen) == sorted(ids(made))


def test_a_group_chat_needs_a_name_and_a_bot_is_told_how_to_give_one(api):
    for path, payload in (("/api/v2/threads", {"audience": "members", "members": ["human:ben"]}),
                          ("/api/v2/threads", {"audience": "company", "title": "  "}),
                          ("/api/v2/threads/dm", {"members": ["human:ben", "human:cara"]})):
        refused = api.post(path, json=payload, headers=headers("ana-test"))
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "name_required"
        assert refused.json()["error"]["detail"] == threads.NAME_REQUIRED
    assert api.post("/api/v2/threads/dm", json={"members": ["human:ben"]}, headers=headers("ana-test")).status_code == 200
    with api.app.state.store.transaction() as c:
        with pytest.raises(Problem) as caught:
            threads.dm(c, "bot:ops", ["human:ben", "human:cara"])
    assert caught.value.code == "name_required" and "hub_thread_dm_group" in caught.value.detail
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM conversations WHERE audience='personal' AND owner_id='bot:ops'"
                         ).fetchone()[0] == 0


def test_pin_for_members_pins_the_new_thread_for_each_person_in_it(api):
    thread = new_thread(api, title="Launch", members=("human:ben", "bot:ops"), pin_for_members=True)
    assert thread["me"]["pinned"] is True
    assert api.get(f"/api/v2/threads/{thread['id']}", headers=headers("ben-test")).json()["me"]["pinned"] is True
    with api.app.state.store.read() as c:
        actors = {r[0] for r in c.execute("SELECT actor FROM thread_pins WHERE thread_id=? AND pinned=1", (thread["id"],))}
    assert actors == {"human:ana", "human:ben"}


def test_the_sidebar_counts_unread_in_each_one_person_dm(api):
    dm = api.get("/api/v2/threads/dm", params={"with": "human:ben"}, headers=headers("ana-test")).json()
    say(api, dm["id"], "Lunch?")
    say(api, dm["id"], "Noon works")
    first = api.get("/api/v2/threads", headers=headers("ben-test")).json()
    assert first["person_dms"] == {"human:ana": 2} and "bot_dms" in first


def test_a_pin_event_goes_to_its_reader_and_a_pin_for_everyone_to_everyone_who_reads_it(api):
    thread = new_thread(api, members=("human:ben", "human:cara"))
    hidden = new_thread(api, title="Hidden", members=())
    after = cursor(api)
    pin(api, thread["id"], "ben-test")
    everyone(api, hidden["id"], True)

    def pins(token):
        return [d for _, topic, d in read_as(api, who(api, token), after, ("threads",)) if d.get("kind") == "pinned"]

    assert [(d["thread_id"], d["actor"], d["pinned"]) for d in pins("ben-test")] == [(thread["id"], "human:ben", True)]
    assert pins("cara-test") == []                    # ben's pin is his alone; the hidden thread is not hers to read
    after = cursor(api)
    everyone(api, thread["id"], True)
    sent = pins("cara-test")
    assert [(d["thread_id"], d["pinned"], d["pinned_for_everyone"]) for d in sent] == [(thread["id"], True, True)]


def test_the_migration_adds_pins_to_an_existing_database_once(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text())
    conn.close()
    Store(Settings(db_path=path)).initialize()
    conn = sqlite3.connect(path)
    try:
        assert {r[1] for r in conn.execute("PRAGMA table_info(thread_pins)")} >= {"thread_id", "actor", "pinned"}
        assert "pinned_for_everyone_at" in {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
        importlib.import_module("backend.migrations.0058_thread_pins").up(conn)       # once more changes nothing
    finally:
        conn.close()
