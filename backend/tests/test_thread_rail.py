"""Pins, the rail, Browse and Catch up (docs/dev/threads-contract.md §10.1) over real SQLite: a pin needs read and only
a manager pins for everyone; a reader unpins a pin for everyone for themselves alone; the rail holds the threads one is
in and never a 1:1 DM or a Home; a hidden thread never shows in any of them; Catch up puts mentions first, shows new
messages in tree order and pages; every list keeps to its byte budget; a group chat needs a name, for people and bots.
Fictional people only: ana (owner), ben (bot admin) and cara."""

import importlib
import json
import sqlite3

import pytest

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.helpers.api import headers  # noqa: F401  (the fixture and its helper)
from backend.tests.helpers.thread_events import cursor, read_as, who
from backend import thread_rail, threads
from backend.config import Settings
from backend.store import H, Problem, Store


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
    for token in ("ben-test", "cara-test"):  # neither ever joined it
        assert hall["id"] in ids(listed(api, "rail", token)["pinned"])
    assert pin(api, hall["id"], "cara-test", "delete").json()["me"]["pinned"] is False
    assert hall["id"] not in ids(
        listed(api, "rail", "cara-test")["pinned"] + listed(api, "rail", "cara-test")["threads"]
    )
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
    listed_ids = ids(rail["pinned"] + rail["threads"])
    assert dm["id"] not in listed_ids and home["id"] not in listed_ids and zeta["id"] not in listed_ids
    assert dm["id"] in rail["catch_up_ids"]  # the DM's unread is Catch up's, not a rail row
    assert rail["dms"] == {"human:ana": 1} and rail["catch_up"] == len(rail["catch_up_ids"])
    followed = api.put(f"/api/v2/threads/{zeta['id']}/follow", json={}, headers=headers("ben-test"))
    assert followed.status_code == 200, followed.text
    seen, page = [], listed(api, "rail", "ben-test", limit=1)
    while True:
        seen += ids(page["threads"])
        if not page["next_cursor"]:
            break
        page = listed(api, "rail", "ben-test", limit=1, cursor=page["next_cursor"])
        assert page["pinned"] == [] and page["catch_up"] is None and page["catch_up_ids"] == [] and page["dms"] == {}
    assert seen == [alpha["id"], beta["id"], gamma["id"], zeta["id"]]
    assert api.get("/api/v2/threads/rail", params={"cursor": "nope"}, headers=headers("ben-test")).status_code == 422


def test_a_hidden_thread_leaves_no_trace_in_the_rail_browse_or_catch_up(api):
    secret = new_thread(api, title="Launch secret", members=())
    say(api, secret["id"], "Board only")
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
    assert listed(api, "browse", "ana-test")["threads"] == []  # ana is in it, so it is not Browse's
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
    rail = listed(api, "rail", "ben-test")
    assert rail["catch_up"] == 2 and sorted(rail["catch_up_ids"]) == sorted([asked["id"], plain["id"]])
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
    for thread in made:  # ben joins first: joining reads up to now
        api.put(f"/api/v2/threads/{thread['id']}/follow", json={}, headers=headers("ben-test"))
        say(api, thread["id"], "x" * 400)
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
    for path, payload in (
        ("/api/v2/threads", {"audience": "members", "members": ["human:ben"]}),
        ("/api/v2/threads", {"audience": "company", "title": "  "}),
        ("/api/v2/threads/dm", {"members": ["human:ben", "human:cara"]}),
    ):
        refused = api.post(path, json=payload, headers=headers("ana-test"))
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "name_required"
        assert refused.json()["error"]["detail"] == threads.NAME_REQUIRED
    assert (
        api.post("/api/v2/threads/dm", json={"members": ["human:ben"]}, headers=headers("ana-test")).status_code == 200
    )
    with api.app.state.store.transaction() as c:
        with pytest.raises(Problem) as caught:
            threads.dm(c, "bot:ops", ["human:ben", "human:cara"])
    assert caught.value.code == "name_required" and "tico_thread_dm_group" in caught.value.detail
    with api.app.state.store.read() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM conversations WHERE audience='personal' AND owner_id='bot:ops'").fetchone()[
                0
            ]
            == 0
        )


def test_pin_for_members_pins_the_new_thread_for_each_person_in_it(api):
    thread = new_thread(api, title="Launch", members=("human:ben", "bot:ops"), pin_for_members=True)
    assert thread["me"]["pinned"] is True
    assert api.get(f"/api/v2/threads/{thread['id']}", headers=headers("ben-test")).json()["me"]["pinned"] is True
    with api.app.state.store.read() as c:
        actors = {
            r[0] for r in c.execute("SELECT actor FROM thread_pins WHERE thread_id=? AND pinned=1", (thread["id"],))
        }
    assert actors == {"human:ana", "human:ben"}


def test_the_sidebar_counts_unread_in_each_one_person_dm(api):
    dm = api.get("/api/v2/threads/dm", params={"with": "human:ben"}, headers=headers("ana-test")).json()
    say(api, dm["id"], "Lunch?")
    say(api, dm["id"], "Noon works")
    first = api.get("/api/v2/threads", headers=headers("ben-test")).json()
    assert first["person_dms"] == {"human:ana": 2} and "bot_dms" in first


@pytest.mark.parametrize("read_list", [thread_rail.rail, threads.sidebar])
def test_a_list_summarizes_its_roots_and_one_person_dms_in_one_statement(api, read_list):
    root = new_thread(api)
    say(api, root["id"], "Budget is ready")
    dm = group_dm(api, ["human:ben"], "Lunch")
    anchor = say(api, dm["id"], "Lunch?")
    read_up(api, dm["id"], "ben-test")
    reply = say(api, dm["id"], "Noon works", parent_message_id=anchor["id"])
    seen = []
    with api.app.state.store.read_transaction() as c:
        v = threads.viewer(api.app.state.auth, who(api, "ben-test"))
        c.set_trace_callback(seen.append)
        try:
            result = read_list(c, v)
        finally:
            c.set_trace_callback(None)
    row = next(r for r in result["threads"] if r["id"] == root["id"])
    assert row["me"]["unread"] == 1
    counts = result["dms"] if read_list is thread_rail.rail else result["person_dms"]
    assert counts == {"human:ana": 1} and reply["thread_id"] != dm["id"]
    summaries = [q for q in seen if "AS active, COUNT(m.seq)" in q or q.startswith("SELECT s.root_id AS root")]
    assert len(summaries) == 1, summaries


def test_subtree_unread_counts_a_delivery_in_any_state_once_and_leaves_out_bot_history(api):
    root = new_thread(api, members=("human:ben", "bot:ops"))
    human = post_as(api, root["id"], "human:ana", "@ben please check.", to_actor="human:ben")
    bot = post_as(api, root["id"], "bot:ops", "Numbers are ready", to_actor="human:ben")
    post_as(api, root["id"], "bot:ops", "Indexing finished")  # his @mention made him a live follower: it counts
    post_as(api, root["id"], "human:ana", "Imported note", refs={"import": "history"})
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE message_deliveries SET state='moot' WHERE message_id=? AND recipient='human:ben'", (bot["id"],)
        )
    with api.app.state.store.read_transaction() as c:
        v = threads.viewer(api.app.state.auth, who(api, "ben-test"))
        summary = threads.subtree_summaries(c, v, [root["id"]])[root["id"]]
        assert summary["unread"] == 3 and summary["new_at"] is None
        assert thread_rail._readable_new(c, v, [root["id"]]) == {root["id"]: 3}
        assert (
            c.execute(
                "SELECT COUNT(*) FROM message_deliveries WHERE message_id=? AND recipient='human:ben'", (human["id"],)
            ).fetchone()[0]
            == 1
        )  # the human half must not count its delivery again


def test_a_pin_event_goes_to_its_reader_and_a_pin_for_everyone_to_everyone_who_reads_it(api):
    thread = new_thread(api, members=("human:ben", "human:cara"))
    hidden = new_thread(api, title="Hidden", members=())
    after = cursor(api)
    pin(api, thread["id"], "ben-test")
    everyone(api, hidden["id"], True)

    def pins(token):
        return [d for _, topic, d in read_as(api, who(api, token), after, ("threads",)) if d.get("kind") == "pinned"]

    assert [(d["thread_id"], d["actor"], d["pinned"]) for d in pins("ben-test")] == [(thread["id"], "human:ben", True)]
    assert pins("cara-test") == []  # ben's pin is his alone; the hidden thread is not hers to read
    after = cursor(api)
    everyone(api, thread["id"], True)
    sent = pins("cara-test")
    assert [(d["thread_id"], d["pinned"], d["pinned_for_everyone"]) for d in sent] == [(thread["id"], True, True)]


def test_catch_up_leaves_out_bot_chatter_own_messages_and_muted_threads_and_a_task_needs_a_mention(api):
    with api.app.state.store.transaction() as c:
        made = H.task_create(
            c, "human:ben", "Budget review", "Details.", "human:ben", requester_actor="human:ana", lint=False
        )
    task = made["conversation_id"]
    # Ben owns the task and follows it; at `mentions` a bot's post there reaches him only when it names him.
    r = api.put(
        f"/api/v2/tasks/{made['id']}/followers",
        json={"following": True, "notify": "mentions"},
        headers=headers("ben-test"),
    )
    assert r.status_code == 200, r.text
    post_as(api, task, "bot:ops", "The draft is in the doc.", to_actor="human:ben")
    chatter = new_thread(api, title="Chatter")
    post_as(api, chatter["id"], "bot:ops", "Indexing finished")
    own = new_thread(api, title="Mine")
    say(api, own["id"], "My own note", token="ben-test")
    muted = new_thread(api, title="Muted")
    api.put(f"/api/v2/threads/{muted['id']}/follow", json={"notify": "muted"}, headers=headers("ben-test"))
    say(api, muted["id"], "A plain update")

    def items():
        return [it["thread"]["id"] for it in listed(api, "catch-up", "ben-test")["items"]]

    assert items() == []
    post_as(api, task, "bot:ops", "@ben the total needs your check.", to_actor="human:ben")
    post_as(api, muted["id"], "human:ana", "@ben can you look?", to_actor="human:ben")
    assert sorted(items()) == sorted([task, muted["id"]])


def test_an_old_unnamed_group_dm_still_opens_shows_in_the_rail_and_takes_posts(api):
    made = group_dm(api, ["human:ben", "human:cara"], "Temp")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET subject='' WHERE id=?", (made["id"],))  # the way old data looks
        assert threads.dm(c, "human:ana", ["human:ben", "human:cara"])["id"] == made["id"]
    assert made["id"] in ids(listed(api, "rail", "ben-test")["threads"])
    say(api, made["id"], "Still here")
    again = api.post(
        "/api/v2/threads/dm",
        json={"members": ["human:cara", "human:ben"], "body": "And again"},
        headers=headers("ana-test"),
    )
    assert again.status_code == 200 and again.json()["id"] == made["id"]


def test_an_activity_event_says_whether_its_message_is_unread_for_each_viewer(api):
    thread = new_thread(api, members=("human:ben", "human:cara"))
    after = cursor(api)
    say(api, thread["id"], "A person's update")

    def activity(token):
        return [
            d
            for _, topic, d in read_as(api, who(api, token), after, ("threads",))
            if d.get("kind") == "activity" and d["thread_id"] == thread["id"]
        ]

    assert [d["unread"] for d in activity("ben-test")] == [True]
    assert [d["unread"] for d in activity("ana-test")] == [False]  # one's own message
    after = cursor(api)
    post_as(api, thread["id"], "bot:ops", "Indexing finished")
    assert [d["unread"] for d in activity("ben-test")] == [False]  # bot activity alone
    after = cursor(api)
    post_as(api, thread["id"], "bot:ops", "@ben please check", to_actor="human:ben")
    assert [d["unread"] for d in activity("ben-test")] == [True]
    assert [d["unread"] for d in activity("cara-test")] == [False]


def test_a_hundred_long_pins_page_through_the_cursor_and_none_is_lost(api, monkeypatch):
    with api.app.state.store.transaction() as c:
        made = [
            threads.create(c, "human:ana", title=f"Pinned {n:03d} " + "x" * 1500, audience="company")["id"]
            for n in range(100)
        ]
        c.executemany(
            "INSERT INTO thread_pins(id,thread_id,actor,pinned,created_at,updated_at) "
            "VALUES(?,?,'human:ben',1,'2026-11-03','2026-11-03')",
            [("pin-" + t, t) for t in made],
        )
    joined = new_thread(api, title="Joined")
    monkeypatch.setattr(threads, "MAX_PAGE_BYTES", 60000)  # a quarter of it holds about ten of these rows
    pinned, rest, page, pages = [], [], listed(api, "rail", "ben-test"), 1
    while True:
        pinned += ids(page["pinned"])
        rest += ids(page["threads"])
        if not page["next_cursor"]:
            break
        page, pages = listed(api, "rail", "ben-test", cursor=page["next_cursor"]), pages + 1
    assert pages > 1, len(pinned)
    assert pinned == made and rest == [joined["id"]]


def test_catch_up_previews_in_tree_order_and_counts_every_other_new_message(api):
    thread = new_thread(api, title="Long")
    first = say(api, thread["id"], "Message 0")
    for n in range(1, 31):
        say(api, thread["id"], f"Message {n}")
    sub = new_thread(api, title="Under", members=(), parent_message_id=first["id"])
    say(api, sub["id"], "A late reply under the first")
    item = listed(api, "catch-up", "ben-test")["items"][0]
    assert [m["body"] for m in item["messages"]][:3] == ["Message 0", "A late reply under the first", "Message 1"]
    assert len(item["messages"]) == thread_rail.CATCH_UP_MESSAGES and item["more"] == 32 - 20


def test_catch_up_cuts_a_long_text_and_keeps_each_card_to_the_page_budget(api, monkeypatch):
    big = new_thread(api, title="Big")
    say(api, big["id"], "y" * (thread_rail.CATCH_UP_BODY + 500))
    for n in range(10):
        say(api, big["id"], f"{n} " + "z" * 900)
    other = new_thread(api, title="Other")
    say(api, other["id"], "Hello")
    first = listed(api, "catch-up", "ben-test")["items"]
    card = next(it for it in first if it["thread"]["id"] == big["id"])
    assert card["messages"][0]["clipped"] is True and len(card["messages"][0]["body"]) <= thread_rail.CATCH_UP_BODY + 1
    monkeypatch.setattr(threads, "MAX_PAGE_BYTES", 12000)
    page = listed(api, "catch-up", "ben-test")
    card = page["items"][0]
    assert card["thread"]["id"] == other["id"] or len(card["messages"]) < 11
    assert len(json.dumps(page)) < 12000 + 2000
    shown = next((it for it in page["items"] if it["thread"]["id"] == big["id"]), None)
    if shown:
        assert len(shown["messages"]) + shown["more"] == 11


def private_ref(api):
    """A private task of cara's that ben may not read: a person's post that names it is hidden from ben."""
    from backend.tests.helpers.api import unrelated_task_managers

    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
        return H.task_create(c, "human:cara", "Private plan", "Details.", "human:cara", private=True, lint=False)["id"]


def test_catch_up_counts_only_the_new_messages_the_reader_may_read(api):
    secret = private_ref(api)

    def hidden(thread_id, text, **kw):
        return post_as(api, thread_id, "human:cara", text, refs={"task": secret}, **kw)

    quiet = new_thread(api, title="Quiet", members=("human:ben", "human:cara"))
    first = hidden(quiet["id"], "Private numbers")
    hidden(quiet["id"], "@ben private numbers", to_actor="human:ben")
    with api.app.state.store.read() as c:
        assert H.message(c, first["id"])["restricted"] == 1
    body, rail = listed(api, "catch-up", "ben-test"), listed(api, "rail", "ben-test")
    assert body["items"] == [] and body["count"] == 0
    assert rail["catch_up"] == 0 and rail["catch_up_ids"] == []
    # 26 hidden ahead of the first one ben reads, and 20 he reads in all.
    mixed = new_thread(api, title="Mixed", members=("human:ben", "human:cara"))
    for n in range(26):
        hidden(mixed["id"], f"Private {n}")
    say(api, mixed["id"], "Visible 0")
    for n in range(26, 30):
        hidden(mixed["id"], f"Private {n}")
    for n in range(1, 20):
        say(api, mixed["id"], f"Visible {n}")
    body = listed(api, "catch-up", "ben-test")
    [item] = body["items"]
    assert item["thread"]["id"] == mixed["id"] and body["count"] == 1
    assert [m["body"] for m in item["messages"]] == [f"Visible {n}" for n in range(20)]
    assert item["new_count"] == 20 and item["more"] == 0
    assert "Private" not in json.dumps(body)
    assert listed(api, "rail", "ben-test")["catch_up_ids"] == [mixed["id"]]


def test_an_activity_event_counts_every_unread_message_it_stands_for(api):
    thread = new_thread(api, members=("human:ben", "human:cara"))
    after = cursor(api)
    say(api, thread["id"], "One")
    say(api, thread["id"], "Two")
    post_as(api, thread["id"], "bot:ops", "Indexing finished")  # a bot's post: unread only for its followers

    def activity(token):
        return [
            (d["unread"], d["unread_count"])
            for _, topic, d in read_as(api, who(api, token), after, ("threads",))
            if d.get("kind") == "activity" and d["thread_id"] == thread["id"]
        ]

    assert activity("ben-test") == [(True, 2)]
    assert activity("ana-test") == [(True, 1)], "Ana started the thread, so she follows it"


def test_held_writes_are_read_from_the_readers_own_candidates_through_an_index(api):
    from backend import org_rule_check
    from backend.tests.helpers.thread_attention import hold

    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='finance'")
    for _ in range(30):
        hold(api, reviewer="human:ana", author="human:cara", operation="threads")  # not ben's
    mine = hold(api, reviewer="human:ben", author="human:cara", operation="threads")
    for_him = hold(api, reviewer="human:ana", author="bot:finance", operation="threads")
    seen = []
    with api.app.state.store.read() as c:
        c.set_trace_callback(seen.append)
        try:
            found = org_rule_check.held_for(c, who(api, "ben-test"), ("threads",))
        finally:
            c.set_trace_callback(None)
        assert sorted(h["id"] for h in found) == sorted([mine, for_him])
        assert len(org_rule_check.held_for(c, who(api, "ben-test"), ("threads",), limit=1)) == 1
        [sql] = [s for s in seen if "FROM org_rule_holds" in s]
        plan = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql)]
    assert not any(p.startswith("SCAN org_rule_holds") for p in plan), plan
    assert any("org_rule_holds_reviewer" in p for p in plan) and any("org_rule_holds_author" in p for p in plan), plan


def test_catch_up_counts_each_open_mention_once(api):
    asked = new_thread(api, title="Asked")
    for n in range(30):
        post_as(api, asked["id"], "human:ana", f"@ben question {n}", to_actor="human:ben")
    [item] = listed(api, "catch-up", "ben-test")["items"]
    assert item["new_count"] == 30 and len(item["messages"]) == 20 and item["more"] == 10


def test_catch_up_sends_a_message_with_huge_refs_clipped_to_fit_the_page(api, monkeypatch):
    heavy = new_thread(api, title="Heavy")
    posted = say(api, heavy["id"], "Short")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET refs=? WHERE id=?", (json.dumps({"note": "r" * 30000}), posted["id"]))
    monkeypatch.setattr(threads, "MAX_PAGE_BYTES", 20000)
    body = listed(api, "catch-up", "ben-test")
    card = next(it for it in body["items"] if it["thread"]["id"] == heavy["id"])
    assert card["messages"][0]["clipped"] is True and card["messages"][0]["refs"] == {}
    assert len(json.dumps(body)) < 20000


def test_catch_up_scans_its_allowance_once_over_all_its_pages_and_muted_roots_take_none(api, monkeypatch):
    monkeypatch.setattr(thread_rail, "CATCH_UP_SCAN", 3)
    monkeypatch.setattr(thread_rail, "CATCH_UP_BATCH", 2)
    made = []
    for n in range(6):
        thread = new_thread(api, title=f"Root {n}")
        if n in (2, 3):
            api.put(f"/api/v2/threads/{thread['id']}/follow", json={"notify": "muted"}, headers=headers("ben-test"))
        else:
            made.append(thread["id"])
        say(api, thread["id"], "News")
    rail = listed(api, "rail", "ben-test")
    assert rail["catch_up"] == 3 and set(rail["catch_up_ids"]) <= set(made)
    seen, page = [], listed(api, "catch-up", "ben-test", limit=1)
    assert page["count"] == 3
    while True:
        seen += [it["thread"]["id"] for it in page["items"]]
        if not page["next_cursor"]:
            break
        page = listed(api, "catch-up", "ben-test", limit=1, cursor=page["next_cursor"])
    assert seen == rail["catch_up_ids"]
