"""A thread is one timeline in tree order (docs/threads.md): each message is followed by its sub-thread, recursively, with
the same per-message read check; a long branch keeps its latest messages under an "N more" row that reads the rest."""

from backend import hubdb as H
from backend import threads
from backend.tests.test_api import api, headers  # noqa: F401  (the fixture and its helper)
from backend.tests.test_thread_api import new_thread, say, set_bot_access


def timeline(api, thread_id, token="ana-test", **params):
    response = api.get(f"/api/v2/threads/{thread_id}/timeline", params=params, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def reply(api, thread_id, message_id, text, token="ana-test"):
    response = api.post(f"/api/v2/threads/{thread_id}/messages", json={"text": text, "parent_message_id": message_id},
                        headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def bodies(rows):
    return [r.get("body") for r in rows]


def test_each_message_is_followed_by_its_sub_thread_in_tree_order_with_its_depth(api):
    t = new_thread(api)
    first = say(api, t["id"], "One")
    second = say(api, t["id"], "Two")
    deep = reply(api, t["id"], first["id"], "One a")
    nested = reply(api, deep["thread_id"], deep["id"], "One a i")
    reply(api, t["id"], first["id"], "One b")
    reply(api, t["id"], second["id"], "Two a")

    page = timeline(api, t["id"])
    assert bodies(page["messages"]) == ["One", "One a", "One a i", "One b", "Two", "Two a"]
    assert [r["depth"] for r in page["messages"]] == [0, 1, 2, 1, 0, 1]
    row = {r["body"]: r for r in page["messages"]}
    assert row["One a i"]["thread_id"] == nested["thread_id"] != row["One a"]["thread_id"]
    assert row["One"]["sub_thread"]["id"] == row["One a"]["thread_id"] and row["One"]["sub_thread"]["count"] == 2
    assert row["One a"]["sub_thread"]["id"] == nested["thread_id"]
    assert row["One"]["sub_thread"]["title"] == ""

    # A sub-thread opened on its own shows its own sub-threads the same way.
    opened = timeline(api, deep["thread_id"])
    assert bodies(opened["messages"]) == ["One a", "One a i", "One b"]
    assert [r["depth"] for r in opened["messages"]] == [1, 2, 1]
    assert [c["id"] for c in opened["breadcrumb"]] == [t["id"], deep["thread_id"]]


def test_a_branch_carries_a_title_only_when_it_was_given_one(api):
    t = new_thread(api)
    first = say(api, t["id"], "**Which** vendor?")
    second = say(api, t["id"], "Which price?")
    sub = reply(api, t["id"], first["id"], "Acme")["thread_id"]
    named = reply(api, t["id"], second["id"], "Forty")["thread_id"]
    assert api.patch(f"/api/v2/threads/{named}", json={"title": "Pricing"}, headers=headers("ana-test")).status_code == 200
    def titles():
        return [r["sub_thread"]["title"] for r in timeline(api, t["id"])["messages"] if r.get("sub_thread")]
    assert titles() == ["", "Pricing"]
    # An untitled branch's title is its parent's first line as it reads now: nothing is stored, so an edit shows through.
    edited = api.patch(f"/api/v2/messages/{first['id']}", json={"text": "Which supplier?"}, headers=headers("ana-test"))
    assert edited.status_code == 200, edited.text
    assert titles() == ["", "Pricing"]
    assert timeline(api, sub)["thread"]["title"] == "Which supplier?"


def test_pages_count_only_the_threads_own_messages(api):
    t = new_thread(api)
    ids = [say(api, t["id"], f"Own {i}")["id"] for i in range(3)]
    for i in range(3):
        reply(api, t["id"], ids[2], f"Reply {i}")
    newest = timeline(api, t["id"], limit=1)
    assert bodies(newest["messages"]) == ["Own 2", "Reply 0", "Reply 1", "Reply 2"]
    rest = timeline(api, t["id"], limit=5, cursor=newest["next_cursor"])
    assert bodies(rest["messages"]) == ["Own 0", "Own 1"] and rest["next_cursor"] is None


def test_a_long_branch_shows_its_latest_messages_and_n_more_reads_the_rest_in_place(api):
    t = new_thread(api)
    parent = say(api, t["id"], "Parent")
    sub = reply(api, t["id"], parent["id"], "Reply 0")["thread_id"]
    for i in range(1, threads.BRANCH_TAIL + 5):
        say(api, sub, f"Reply {i}")

    rows = timeline(api, t["id"], token="ben-test")["messages"]
    assert rows[0]["sub_thread"]["count"] == threads.BRANCH_TAIL + 5 and rows[0]["sub_thread"]["more"] == 5
    more = rows[1]
    assert more["collapsed"] is True and more["count"] == 5 and more["thread_id"] == sub and more["depth"] == 1
    assert more["unread"] is True and not more.get("body")
    assert bodies(rows[2:]) == [f"Reply {i}" for i in range(5, threads.BRANCH_TAIL + 5)]

    older = timeline(api, sub, token="ben-test", cursor=more["cursor"])["messages"]
    assert bodies(older) == [f"Reply {i}" for i in range(5)]

    # Read through the part on screen, the collapsed part is still unread; read through all of it, it is not.
    assert api.post(f"/api/v2/threads/{sub}/read", json={"seq": older[-1]["seq"]}, headers=headers("ben-test")).status_code == 200
    again = timeline(api, t["id"], token="ben-test")["messages"]
    assert again[1]["unread"] is False and again[0]["sub_thread"]["last_read_seq"] == older[-1]["seq"]


def test_a_restricted_message_in_a_branch_is_one_quiet_row_and_no_count_gives_it_away(api):
    t = new_thread(api, members=("human:ben", "human:cara"))
    parent = say(api, t["id"], "Parent")
    sub = reply(api, t["id"], parent["id"], "Open reply")["thread_id"]
    with api.app.state.store.transaction() as c:
        secret = H.task_create(c, "human:ana", "Acme acquisition", "", "human:ana", private=True, lint=False)
    posted = [api.post(f"/api/v2/threads/{sub}/messages", json={"text": f"Secret {i}", "refs": {"task": secret["id"]}},
                       headers=headers("ana-test")) for i in range(2)]
    assert all(p.status_code == 200 for p in posted), posted[0].text
    say(api, sub, "Last reply")

    mine = timeline(api, t["id"])["messages"]
    assert bodies(mine) == ["Parent", "Open reply", "Secret 0", "Secret 1", "Last reply"]
    assert mine[0]["sub_thread"]["count"] == 4

    rows = timeline(api, t["id"], token="ben-test")["messages"]
    assert [r.get("hidden", False) for r in rows] == [False, False, True, False]
    assert rows[2].keys() == {"id", "seq", "thread_id", "depth", "hidden"} and rows[2]["depth"] == 1
    assert rows[0]["sub_thread"]["count"] == 2
    assert "Secret" not in str(rows)


def test_a_sub_thread_under_a_hidden_message_does_not_show(api):
    t = new_thread(api, members=("human:ben",))
    with api.app.state.store.transaction() as c:
        secret = H.task_create(c, "human:ana", "Acme acquisition", "", "human:ana", private=True, lint=False)
    parent = api.post(f"/api/v2/threads/{t['id']}/messages", json={"text": "Secret", "refs": {"task": secret["id"]}},
                      headers=headers("ana-test")).json()
    reply(api, t["id"], parent["id"], "Under the secret")
    assert "Under the secret" in bodies(timeline(api, t["id"])["messages"])
    rows = timeline(api, t["id"], token="ben-test")["messages"]
    assert [r.get("hidden") for r in rows] == [True]


def test_a_see_only_viewer_reads_no_branch_of_a_home(api):
    home = api.get("/api/v2/threads/home/bot:ops", headers=headers("ana-test")).json()
    parent = say(api, home["id"], "Parent")
    sub = reply(api, home["id"], parent["id"], "Reply")["thread_id"]
    only_ana = {"everyone": False, "people": ["ana"], "teams": [], "bots": []}
    set_bot_access(api, "ops", {"everyone": True, "people": [], "teams": [], "bots": []}, only_ana, only_ana)
    for tid in (home["id"], sub):
        assert api.get(f"/api/v2/threads/{tid}/timeline", headers=headers("cara-test")).status_code == 404
    assert bodies(timeline(api, home["id"])["messages"]) == ["Parent", "Reply"]


def closed_home(api, slug="ops"):
    """A migrated Home: its shared-room history keeps its old readers (ana, in the room); what is new follows Home Read."""
    bot = "bot:" + slug
    with api.app.state.store.transaction() as c:
        room = H.open_conversation(c, "human:ana", ["human:ana", bot], kind="chat", subject="Ops room", scope="shared",
                                   room_key=slug, allow_held=True)
        for text in ("Old one", "Old two"):
            H.say(c, "human:ana", bot, text, conversation_id=room["id"])
        c.execute("UPDATE messages SET legacy_audience=?, restricted=1 WHERE conversation_id=?", ("shared:" + slug, room["id"]))
        threads._store_shared_homes(c, slug)
        threads.post(c, bot, room["id"], "New plan")
    return room["id"]


def test_closed_history_is_one_row_marked_earlier_and_only_a_manager_gets_the_count(api):
    home = closed_home(api)
    set_bot_access(api, "ops", {"people": ["cara"]}, {"people": ["cara"]}, {"people": []})

    # Cara reads the Home and not its history: one row that says what it is, with no text and no count.
    page = timeline(api, home, token="cara-test")
    rows = page["messages"]
    assert [r.get("hidden", False) for r in rows] == [True, False] and rows[0]["earlier"] is True
    assert rows[0].keys() == {"id", "seq", "thread_id", "depth", "hidden", "earlier"} and "Old" not in str(rows)
    assert "earlier" not in page["thread"]

    # The owner manages the bot and reads the history; the control to open it is still theirs.
    mine = timeline(api, home)
    assert bodies(mine["messages"])[:2] == ["Old one", "Old two"]
    assert mine["thread"]["earlier"] == {"count": 2}

    opened = api.post(f"/api/v2/threads/{home}/open-earlier", headers=headers("ana-test"))
    assert opened.status_code == 200 and opened.json()["messages"] == 2
    assert bodies(timeline(api, home, token="cara-test")["messages"]) == ["Old one", "Old two", "New plan"]
    assert "earlier" not in timeline(api, home)["thread"]


def test_a_task_private_message_stays_a_plain_hidden_row_not_earlier(api):
    t = new_thread(api, members=("human:ben",))
    with api.app.state.store.transaction() as c:
        secret = H.task_create(c, "human:ana", "Acme acquisition", "", "human:ana", private=True, lint=False)
    posted = api.post(f"/api/v2/threads/{t['id']}/messages", json={"text": "Secret", "refs": {"task": secret["id"]}},
                      headers=headers("ana-test"))
    assert posted.status_code == 200, posted.text
    assert "earlier" not in timeline(api, t["id"], token="ben-test")["messages"][0]
