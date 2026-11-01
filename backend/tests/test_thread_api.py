"""Thread routes over real SQLite: C1 on every read (a hidden thread answers 404 and never shows in a list or a search),
read state (unread is human or addressed only, a sibling is not cleared, max wins, joining reads up to now), and search
that follows edits and deletes. Fictional people only: ana (owner), ben and cara (humans)."""

from backend.tests.test_api import api, headers  # noqa: F401  (the fixture and its helper)
from backend import threads


def new_thread(api, title="Budget", members=("human:ben",), body=None, parent_message_id=None):
    payload = {"title": title, "audience": "members", "members": list(members)}
    if body:
        payload["body"] = body
    if parent_message_id:
        payload["parent_message_id"] = parent_message_id
    response = api.post("/api/v2/threads", json=payload, headers=headers("ana-test"))
    assert response.status_code == 200, response.text
    return response.json()


def say(api, thread_id, text, token="ana-test"):
    response = api.post(f"/api/v2/threads/{thread_id}/messages", json={"text": text}, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def get_thread(api, thread_id, token="ben-test"):
    return api.get(f"/api/v2/threads/{thread_id}", headers=headers(token))


def search(api, query, token):
    response = api.get("/api/v2/threads/search", params={"q": query}, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def test_a_member_reads_a_members_thread_and_a_stranger_gets_404_and_no_listing(api):
    thread = new_thread(api, body="Draft the budget")
    assert get_thread(api, thread["id"], "ben-test").status_code == 200
    stranger = get_thread(api, thread["id"], "cara-test")
    assert stranger.status_code == 404 and thread["id"] not in stranger.text
    listed = api.get("/api/v2/threads", headers=headers("cara-test")).json()
    assert thread["id"] not in str([listed["threads"], listed["attention"], listed["dms"]])


def test_search_finds_what_the_viewer_may_read_and_a_hidden_thread_leaves_no_trace(api):
    open_thread = new_thread(api, title="Budget", body="Quarterly budget review moved to Friday")
    new_thread(api, title="Launch", members=(), body="Launch secret for the board")
    hits = search(api, "budget", "ben-test")["hits"]
    assert [h["thread_id"] for h in hits] == [open_thread["id"]]
    assert "budget" in hits[0]["excerpt"].casefold()
    # Cara is in neither thread: her searches come back empty with no cursor, whatever the words.
    for query in ("secret", "launch", "budget"):
        assert search(api, query, "cara-test") == {"hits": [], "next_cursor": None}
    # Ben is not in the launch thread either, so its text is not his to find.
    assert search(api, "secret", "ben-test") == {"hits": [], "next_cursor": None}
    assert search(api, "secret", "ana-test")["hits"]              # the starter reads it


def test_unread_counts_people_and_addressed_messages_and_a_bot_post_is_activity_only(api):
    thread = new_thread(api, body="Kickoff")
    say(api, thread["id"], "Please review the draft")              # a person's message: unread for ben
    with api.app.state.store.transaction() as c:                   # a bot's status post, addressed to nobody
        threads.post(c, "bot:ops", thread["id"], "Indexing finished")
    me = get_thread(api, thread["id"], "ben-test").json()["me"]
    assert me["unread"] == 2 and me["activity"] is True
    last = get_thread(api, thread["id"], "ben-test").json()["last_seq"]
    assert api.post(f"/api/v2/threads/{thread['id']}/read", json={"seq": last}, headers=headers("ben-test")).status_code == 200
    me = get_thread(api, thread["id"], "ben-test").json()["me"]
    assert me["unread"] == 0 and me["last_read_seq"] == last


def test_reading_a_thread_leaves_its_sibling_unread_and_a_lower_mark_never_moves_back(api):
    root = new_thread(api, body="Parent")
    first_topic = say(api, root["id"], "First topic")
    second_topic = say(api, root["id"], "Second topic")
    sub_a = new_thread(api, title="A", members=(), parent_message_id=first_topic["id"])
    sub_b = new_thread(api, title="B", members=(), parent_message_id=second_topic["id"])
    say(api, sub_a["id"], "A one")
    say(api, sub_a["id"], "A two")
    say(api, sub_b["id"], "B one")
    assert get_thread(api, sub_a["id"], "ben-test").json()["me"]["unread"] == 2
    assert get_thread(api, sub_b["id"], "ben-test").json()["me"]["unread"] == 1
    last_a = get_thread(api, sub_a["id"], "ben-test").json()["last_seq"]
    assert api.post(f"/api/v2/threads/{sub_a['id']}/read", json={"seq": last_a},
                    headers=headers("ben-test")).status_code == 200
    assert get_thread(api, sub_a["id"], "ben-test").json()["me"]["unread"] == 0
    assert get_thread(api, sub_b["id"], "ben-test").json()["me"]["unread"] == 1      # the sibling is not cleared
    # Another device, behind, reads an older mark: the newest one stands.
    behind = api.post(f"/api/v2/threads/{sub_a['id']}/read", json={"seq": 1}, headers=headers("ben-test")).json()
    assert behind["last_read_seq"] == last_a


def test_joining_reads_up_to_now_and_the_backfill_is_never_unread(api):
    thread = new_thread(api, members=(), body="Before you joined")
    say(api, thread["id"], "Also before")
    added = api.post(f"/api/v2/threads/{thread['id']}/members", json={"actor": "human:cara"},
                     headers=headers("ana-test"))
    assert added.status_code == 200, added.text
    assert get_thread(api, thread["id"], "cara-test").json()["me"]["unread"] == 0


def test_edit_and_delete_leave_the_index_and_the_timeline_agree(api):
    thread = new_thread(api, body="Budget for Acme")
    message_id = api.get(f"/api/v2/threads/{thread['id']}/timeline", headers=headers("ben-test")).json()["messages"][0]["id"]
    edit = api.patch(f"/api/v2/messages/{message_id}", json={"text": "Pricing call with Acme"}, headers=headers("ana-test"))
    assert edit.status_code == 200, edit.text
    assert search(api, "budget", "ben-test")["hits"] == []
    assert search(api, "pricing", "ben-test")["hits"]
    assert api.delete(f"/api/v2/messages/{message_id}", headers=headers("ana-test")).status_code == 200
    assert search(api, "pricing", "ben-test")["hits"] == []


def test_context_search_finds_a_thread_message_the_viewer_may_read_and_never_a_hidden_one(api):
    new_thread(api, title="Budget", body="Quarterly budget review moved to Friday")
    new_thread(api, title="Launch", members=(), body="Launch secret for the board")

    def context(query, token):
        response = api.get("/api/v2/context/search", params={"q": query, "source": "threads"}, headers=headers(token))
        assert response.status_code == 200, response.text
        return response.json()["results"]

    hits = context("budget", "ben-test")
    assert [h["kind"] for h in hits] == ["thread_message"] and hits[0]["url"].startswith("#/thread/")
    assert context("secret", "cara-test") == [] and context("secret", "ben-test") == []
    assert context("secret", "ana-test")
