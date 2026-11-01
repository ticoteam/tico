"""Thread routes over real SQLite: C1 on every read (a hidden thread answers 404 and never shows in a list or a search),
read state (unread is human or addressed only, a sibling is not cleared, max wins, joining reads up to now), and search
that follows edits and deletes. Fictional people only: ana (owner), ben and cara (humans)."""

from backend.tests.test_api import api, get, headers, post  # noqa: F401  (the fixture and its helpers)
from backend.tests.test_org_rule_check import COLOR, bodies, hold_row, ruled  # noqa: F401  (the Judge fixture)
from backend.tests.test_org_rules import rule
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


def set_bot_access(api, slug, see, read, write):
    revision = api.get(f"/api/v2/bots/{slug}/access", headers=headers()).json()["revision"]
    response = api.put(f"/api/v2/bots/{slug}/access", headers=headers(),
                       json={"see": see, "read": read, "write": write, "revision": revision})
    assert response.status_code == 200, response.text


def home_as(api, actor, token="ana-test"):
    return api.get("/api/v2/threads/home", params={"actor": actor}, headers=headers(token))


def dm_as(api, peer, token="ana-test"):
    return api.get("/api/v2/threads/dm", params={"with": peer}, headers=headers(token))


def test_a_teammates_home_is_made_once_and_its_owner_may_read_it(api):
    first = home_as(api, "human:ben")
    assert first.status_code == 200, first.text
    assert first.json()["audience"] == "home" and first.json()["owner_actor"] == "human:ben"
    assert home_as(api, "human:ben").json()["id"] == first.json()["id"]          # the same Home, not a second one
    assert home_as(api, "human:ben", "ben-test").json()["id"] == first.json()["id"]


def test_a_home_the_viewer_may_not_read_is_the_same_404_as_a_missing_teammate_and_makes_nothing(api):
    set_bot_access(api, "ops", {"people": ["ben"]}, {"people": ["ben"]}, {"people": ["ben"]})
    hidden = home_as(api, "bot:ops", "cara-test")
    missing = home_as(api, "bot:nobody", "cara-test")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()
    assert "ops" not in hidden.text and "nobody" not in missing.text
    with api.app.state.store.read() as c:       # a refused viewer leaves no Home behind
        assert c.execute("SELECT COUNT(*) FROM conversations WHERE audience='home' AND owner_actor='bot:ops'"
                         ).fetchone()[0] == 0
    assert home_as(api, "bot:ops", "ben-test").status_code == 200                # someone who may read it


def test_a_dm_is_the_open_one_with_exactly_those_two(api):
    first = dm_as(api, "human:ben")
    assert first.status_code == 200, first.text
    assert first.json()["audience"] == "personal"
    assert dm_as(api, "human:ben").json()["id"] == first.json()["id"]
    assert dm_as(api, "human:ana", "ben-test").json()["id"] == first.json()["id"]   # the other side opens the same DM
    assert dm_as(api, "human:cara").json()["id"] != first.json()["id"]               # a different pair, a different DM


def test_a_dm_needs_write_on_the_other_teammate_and_a_hidden_one_is_404(api):
    set_bot_access(api, "ops", {"people": ["cara"]}, {"people": ["cara"]}, {"people": ["ben"]})
    refused = dm_as(api, "bot:ops", "cara-test")                  # cara can see and read ops, not message it
    assert refused.status_code == 403 and "Write" in refused.text
    assert dm_as(api, "bot:ops", "ben-test").status_code == 200   # ben has Write
    assert dm_as(api, "human:nobody", "cara-test").status_code == 404
    assert dm_as(api, "human:ana", "ana-test").status_code == 422   # a DM with yourself names nobody


GOLD = "Our favorite color is gold."


def message_in(api, thread_id, token="ana-test"):
    return api.get(f"/api/v2/threads/{thread_id}/timeline", headers=headers(token)).json()["messages"]


def test_a_block_holds_a_persons_thread_post_through_rest_and_writes_nothing(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"everyone": True}})
    thread = new_thread(api, members=("human:cara",))
    calls = len(judge.calls)
    got = api.post(f"/api/v2/threads/{thread['id']}/messages", json={"text": GOLD}, headers=headers("cara-test"))
    assert got.status_code == 200, got.text
    assert got.json()["held"]["outcome"] == "blocked"
    assert len(judge.calls) == calls + 1 and not any(judge.locked[calls:])   # checked as cara, outside the write
    hold = hold_row(api, got.json()["held"]["id"])
    assert hold["author"] == "human:cara" and hold["state"] == "held"
    assert bodies(api, "favorite color is gold") == []                 # the hold is the only trace
    assert not any("favorite color" in str(m.get("body")) for m in message_in(api, thread["id"]))


def test_a_held_thread_post_is_sent_by_its_rule_author_as_its_person(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"everyone": True}})
    thread = new_thread(api, members=("human:cara",))
    held = api.post(f"/api/v2/threads/{thread['id']}/messages", json={"text": GOLD},
                    headers=headers("cara-test")).json()["held"]
    calls = len(judge.calls)
    view = get(api, f"rule-holds/{held['id']}")
    sent = post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]}, token="ana-test")
    assert sent["hold"]["state"] == "sent"
    assert len(judge.calls) == calls                                  # sent, not checked again
    posted = [m for m in message_in(api, thread["id"]) if "favorite color is gold" in str(m.get("body"))]
    assert len(posted) == 1 and posted[0]["from_actor"] == "human:cara"


def test_a_warn_delivers_a_persons_thread_post_with_the_warning_on_it(ruled):
    api, _ = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"everyone": True}, "if_broken": "warn"})
    thread = new_thread(api, members=("human:cara",))
    got = api.post(f"/api/v2/threads/{thread['id']}/messages", json={"text": GOLD}, headers=headers("cara-test"))
    assert got.status_code == 200 and "held" not in got.json() and got.json()["rule_warning"]
    delivered = [m for m in message_in(api, thread["id"]) if "favorite color is gold" in str(m.get("body"))]
    assert len(delivered) == 1 and delivered[0]["body"].startswith("Warning:")   # the warning heads the recipient's copy

