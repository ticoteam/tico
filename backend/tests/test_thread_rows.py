"""A row in a thread list: a root speaks for its sub-threads and opens what is new in them; a DM says who it is with."""
from types import SimpleNamespace

from backend import cards, threads
from backend.auth import Identity
from backend.tests.test_api import api, headers  # noqa: F401  (the fixture and its helpers)
from backend.tests.test_teammate_access import NOBODY, acme, set_access, who  # noqa: F401
from backend.tests.test_thread_api import new_thread, row_of, say, sidebar
from backend.tests.test_thread_tasks import make_task
from backend.tests.threads_e2e import call, ok


def reply_under(api, thread_id, parent_id, text, token="ben-test"):
    response = api.post(f"/api/v2/threads/{thread_id}/messages", json={"text": text, "parent_message_id": parent_id},
                        headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def read(api, thread_id, seq, token):
    response = api.post(f"/api/v2/threads/{thread_id}/read", json={"seq": seq}, headers=headers(token))
    assert response.status_code == 200, response.text


def test_a_sub_thread_is_never_a_row_and_its_root_opens_the_new_reply(api):
    thread = new_thread(api, title="Launch", members=("human:ben",), body="Launch is Tuesday")
    first = say(api, thread["id"], "Kickoff notes")
    reply = reply_under(api, thread["id"], first["id"], "Risks?")
    listed = sidebar(api, token="ana-test")
    every = listed["attention"] + listed["threads"] + listed["dms"]
    assert reply["thread_id"] not in {t["id"] for t in every} and all(not t["parent_id"] for t in every)
    row = row_of(listed, thread["id"])
    assert row["me"]["unread"] == 1
    assert row["new_at"] == {"thread_id": reply["thread_id"], "message_id": reply["id"]}

    # The oldest new reply first; read, the row has nothing left to open.
    second = reply_under(api, thread["id"], first["id"], "And the date?")
    assert row_of(sidebar(api, token="ana-test"), thread["id"])["new_at"]["message_id"] == reply["id"]
    read(api, reply["thread_id"], second["seq"], "ana-test")
    row = row_of(sidebar(api, token="ana-test"), thread["id"])
    assert row["me"]["unread"] == 0 and "new_at" not in row


def test_a_root_with_its_own_older_unread_message_opens_at_the_root(api):
    thread = new_thread(api, title="Launch", members=("human:ben",), body="Launch is Tuesday")
    first = say(api, thread["id"], "Kickoff notes")
    say(api, thread["id"], "Ben's own note", token="ben-test")
    reply_under(api, thread["id"], first["id"], "Risks?")
    row = row_of(sidebar(api, token="ana-test"), thread["id"])
    assert row["me"]["unread"] == 2 and "new_at" not in row


def test_a_bot_reply_in_a_sub_thread_is_activity_and_the_row_opens_that_sub_thread(api):
    thread = new_thread(api, title="Launch", members=("human:ben", "bot:ops"), body="Launch is Tuesday")
    first = say(api, thread["id"], "Kickoff notes")
    reply = reply_under(api, thread["id"], first["id"], "Risks?", token="ana-test")
    read(api, reply["thread_id"], reply["seq"], "ana-test")
    with api.app.state.store.transaction() as c:
        threads.post(c, "bot:ops", reply["thread_id"], "Checked the risks")
    row = row_of(sidebar(api, token="ana-test"), thread["id"])
    assert row["me"]["unread"] == 0 and row["me"]["activity"] is True
    assert row["new_at"] == {"thread_id": reply["thread_id"], "message_id": None}


def test_following_only_a_sub_thread_lists_its_root_and_unfollowing_takes_it_away(api):
    # Cara's Home: the owner reads it, and it is in no list of hers until she follows a part of it.
    thread = api.get("/api/v2/threads/home/human:cara", headers=headers("cara-test")).json()
    first = say(api, thread["id"], "Kickoff notes", token="cara-test")
    reply = reply_under(api, thread["id"], first["id"], "Risks?", token="cara-test")
    assert row_of(sidebar(api, token="ana-test"), thread["id"]) is None
    follow = api.put(f"/api/v2/threads/{reply['thread_id']}/follow", json={"notify": "live"}, headers=headers("ana-test"))
    assert follow.status_code == 200, follow.text
    listed = sidebar(api, token="ana-test")
    assert row_of(listed, thread["id"]) is not None and row_of(listed, reply["thread_id"]) is None
    api.delete(f"/api/v2/threads/{reply['thread_id']}/follow", headers=headers("ana-test"))
    assert row_of(sidebar(api, token="ana-test"), thread["id"]) is None


def test_the_unread_threads_card_lists_roots_with_a_dms_people_and_the_place_to_open(api):
    thread = new_thread(api, title="Launch", members=("human:ben",), body="Launch is Tuesday")
    first = say(api, thread["id"], "Kickoff notes")
    reply = reply_under(api, thread["id"], first["id"], "Risks?")
    made = api.post("/api/v2/threads/dm", json={"members": ["human:ana"], "body": "Got a minute?"}, headers=headers("ben-test"))
    assert made.status_code == 200, made.text
    dm = made.json().get("thread") or made.json()
    with api.app.state.store.read() as c:
        ctx = SimpleNamespace(c=c, auth=api.app.state.auth, who=Identity("human:ana", "owner", "ana@acme.example"))
        rows = {r["id"]: r for r in cards.thread_rows(ctx, 50)}
    assert reply["thread_id"] not in rows
    assert rows[thread["id"]]["new_at"]["message_id"] == reply["id"]
    assert rows[dm["id"]]["audience"] == "personal"
    assert [p["actor"] for p in rows[dm["id"]]["dm_with"]] == ["human:ben"]


# ----------------------------------------------------------------------------- Threads and DMs are paged apart
def test_many_threads_leave_an_older_dm_on_the_first_page_and_each_list_pages_on(api):
    old = api.post("/api/v2/threads/dm", json={"members": ["human:ben"], "body": "An old DM"}, headers=headers("ana-test"))
    assert old.status_code == 200, old.text
    dm = old.json().get("thread") or old.json()
    made = [new_thread(api, title=f"Thread {i}", body="Newer") for i in range(5)]
    first = sidebar(api, token="ana-test", limit=3)
    assert [t["id"] for t in first["dms"]] == [dm["id"]]
    assert len(first["threads"]) == 3 and first["next_cursor"]
    second = sidebar(api, token="ana-test", limit=3, cursor=first["next_cursor"])
    assert second["dms"] == [] and second["next_cursor"] is None
    seen = [t["id"] for t in first["threads"] + second["threads"]]
    assert len(seen) == len(set(seen)) and {t["id"] for t in made} <= set(seen)


def test_a_long_dm_list_pages_on_its_own_and_a_page_reads_each_list_once(acme):
    api, _ = acme
    for peer in ("human:eve", "human:fay", "human:ben"):
        ok(call(api, "POST", "threads/dm", "cara-test", {"members": [peer]}))
    first = sidebar(api, token="cara-test", limit=2)
    assert len(first["dms"]) == 2
    second = sidebar(api, token="cara-test", limit=2, cursor=first["next_cursor"])
    assert len(second["dms"]) == 1 and second["next_cursor"] is None
    assert {t["id"] for t in first["dms"]}.isdisjoint(t["id"] for t in second["dms"])
    statements = []
    with api.app.state.store.read() as c:
        v = threads.Viewer(api.app.state.auth, who(api, "cara"))
        c.set_trace_callback(statements.append)
        try:
            threads.sidebar(c, v, limit=2)
        finally:
            c.set_trace_callback(None)
    assert len([s for s in statements if "ORDER BY place, activity DESC" in s]) == 2


def test_a_cursor_from_one_merged_page_still_reads_and_a_bad_one_is_refused(api):
    new_thread(api, title="One", body="Hello")
    assert sidebar(api, token="ana-test", cursor="2:0:zzz")["next_cursor"] is None
    bad = api.get("/api/v2/threads", params={"cursor": "a|b|c"}, headers=headers("ana-test"))
    assert bad.status_code == 422


# ----------------------------------------------------------------------------- who a DM is with
def dm_rows(api, token):
    listed = sidebar(api, token=token)
    return {t["id"]: t for t in listed["dms"]}


def test_a_dm_row_names_the_others_without_a_members_read(acme):
    api, _ = acme
    one = ok(call(api, "POST", "threads/dm", "cara-test", {"members": ["human:eve"]}))
    many = ok(call(api, "POST", "threads/dm", "cara-test", {"members": ["human:eve", "human:fay", "human:ben", "human:ana"]}))
    rows = dm_rows(api, "cara-test")
    assert [p["actor"] for p in rows[one["id"]]["dm_with"]] == ["human:eve"] and rows[one["id"]]["dm_with_count"] == 1
    assert "human:cara" not in {p["actor"] for p in rows[many["id"]]["dm_with"]}
    assert rows[many["id"]]["dm_with_count"] == 4
    # The thread's own page says the same, for its header.
    assert ok(call(api, "GET", f"threads/{one['id']}/timeline", "cara-test"))["thread"]["dm_with_count"] == 1


def test_a_hidden_person_is_named_only_to_someone_in_the_dm(acme):
    api, _ = acme
    made = ok(call(api, "POST", "threads/dm", "fay-test", {"members": ["human:eve"]}))
    set_access(api, "humans/eve", token="eve-test", see=NOBODY, read=NOBODY, write=NOBODY)
    # Fay is in the DM, and may be told who it is with (teammate_access.Known).
    assert [p["actor"] for p in dm_rows(api, "fay-test")[made["id"]]["dm_with"]] == ["human:eve"]
    # Anyone else who somehow holds the row is not told.
    with api.app.state.store.read() as c:
        row = dict(c.execute("SELECT * FROM conversations WHERE id=?", (made["id"],)).fetchone())
        outsider = threads.Viewer(api.app.state.auth, who(api, "cara"))
        found = threads.dm_with(c, outsider, [row])[made["id"]]
    assert "human:eve" not in {p["actor"] for p in found["dm_with"]}
    assert found["dm_with_count"] == 1 and {p["actor"] for p in found["dm_with"]} == {"human:fay"}


def test_a_page_of_dms_reads_its_people_once(acme):
    api, _ = acme
    for peer in ("human:eve", "human:fay", "human:ben"):
        ok(call(api, "POST", "threads/dm", "cara-test", {"members": [peer]}))
    statements = []
    with api.app.state.store.read() as c:
        v = threads.Viewer(api.app.state.auth, who(api, "cara"))
        c.set_trace_callback(statements.append)
        try:
            listed = threads.sidebar(c, v)
        finally:
            c.set_trace_callback(None)
    assert len(listed["dms"]) >= 3
    people = [s for s in statements if "SELECT thread_id, actor FROM thread_members" in s]
    names = [s for s in statements if "SELECT id, name FROM humans" in s]
    assert len(people) == 1 and len(names) == 1, statements


# ----------------------------------------------------------------------------- Task threads are listed only while unread
def task_for_ben(api, title="Ben's review"):
    """A task Ana makes for Ben: Ana is its requester and Ben its owner, both parties to its thread."""
    return make_task(api, {"owner": "human:ben", "title": title, "body": "Details."})


def test_a_task_thread_with_nothing_unread_is_not_a_sidebar_row_but_still_opens(api):
    task = task_for_ben(api)
    listed = sidebar(api, token="ana-test")
    assert row_of(listed, task["conversation_id"]) is None
    opened = api.get(f"/api/v2/threads/{task['conversation_id']}", headers=headers("ana-test"))
    assert opened.status_code == 200, opened.text


def test_a_task_thread_is_listed_while_unread_and_leaves_the_sidebar_once_read(api):
    task = task_for_ben(api)
    reply = say(api, task["conversation_id"], "On it", token="ben-test")
    row = row_of(sidebar(api, token="ana-test"), task["conversation_id"])
    assert row is not None and row["me"]["unread"] == 1
    read(api, task["conversation_id"], reply["seq"], "ana-test")
    assert row_of(sidebar(api, token="ana-test"), task["conversation_id"]) is None


def test_a_person_who_posted_in_a_task_thread_gets_no_row_from_it_as_any_reader_would_not(api):
    task = task_for_ben(api)
    say(api, task["conversation_id"], "Question for Ben")
    assert row_of(sidebar(api, token="ana-test"), task["conversation_id"]) is None
    say(api, task["conversation_id"], "Ben, ping", token="ben-test")
    assert row_of(sidebar(api, token="ana-test"), task["conversation_id"]) is not None


def test_bot_activity_in_a_task_thread_does_not_list_it(api):
    task = task_for_ben(api)
    with api.app.state.store.transaction() as c:
        threads.post(c, "bot:ops", task["conversation_id"], "Checked the work")
    listed = sidebar(api, token="ana-test")
    assert row_of(listed, task["conversation_id"]) is None


def test_task_threads_do_not_crowd_out_real_threads_and_each_page_reads_on(api):
    for n in range(12):
        task_for_ben(api, title=f"Task {n}")
    real = [new_thread(api, title=f"Real {n}", members=("human:ana",), body="Discussion") for n in range(2)]
    first = sidebar(api, token="ana-test", limit=1)
    listed = sidebar(api, token="ana-test", limit=10)
    assert sorted(t["id"] for t in listed["threads"]) == sorted(t["id"] for t in real)
    assert listed["next_cursor"] is None
    seen, cursor = [t["id"] for t in first["threads"]], first["next_cursor"]
    while cursor:
        page = sidebar(api, token="ana-test", limit=1, cursor=cursor)
        seen += [t["id"] for t in page["threads"]]
        cursor = page["next_cursor"]
    assert sorted(seen) == sorted(t["id"] for t in real)
