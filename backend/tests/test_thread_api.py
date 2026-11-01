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
    return api.get(f"/api/v2/threads/home/{actor}", headers=headers(token))


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
    assert got.status_code == 200 and "held" not in got.json()
    delivered = [m for m in message_in(api, thread["id"]) if "favorite color is gold" in str(m.get("body"))]
    assert len(delivered) == 1 and delivered[0]["body"].startswith("Warning:")   # the warning heads the recipient's copy


def test_a_group_dm_is_the_open_one_with_exactly_those_people_and_its_first_message_is_posted(api):
    got = api.post("/api/v2/threads/dm", json={"members": ["human:ben", "human:cara"], "body": "Budget call at 3"},
                   headers=headers("ana-test"))
    assert got.status_code == 200, got.text
    dm = got.json()
    assert dm["audience"] == "personal" and dm["message"]["body"] == "Budget call at 3"
    again = api.post("/api/v2/threads/dm", json={"members": ["human:cara", "human:ben"]}, headers=headers("ana-test"))
    assert again.status_code == 200 and again.json()["id"] == dm["id"] and "message" not in again.json()
    view = get_thread(api, dm["id"], token="cara-test").json()
    assert {m["actor"] for m in view["members"]["members"]} >= {"human:ben", "human:cara"}


def test_a_group_dm_needs_write_on_each_teammate_and_a_hidden_one_is_404(api):
    set_bot_access(api, "ops", {"people": ["cara"]}, {"people": ["cara"]}, {"people": ["ben"]})
    refused = api.post("/api/v2/threads/dm", json={"members": ["human:ben", "bot:ops"]}, headers=headers("cara-test"))
    assert refused.status_code == 403 and "Write" in refused.text
    assert api.post("/api/v2/threads/dm", json={"members": ["human:ben"]}, headers=headers("cara-test")).status_code == 200
    assert api.post("/api/v2/threads/dm", json={"members": ["bot:nobody"]}, headers=headers("ana-test")).status_code == 404
    assert api.post("/api/v2/threads/dm", json={"members": []}, headers=headers("ana-test")).status_code == 422


def test_a_block_holds_a_group_dm_first_message_and_writes_no_message(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"everyone": True}})
    calls = len(judge.calls)
    got = api.post("/api/v2/threads/dm", json={"members": ["human:ben"], "body": GOLD}, headers=headers("cara-test"))
    assert got.status_code == 200, got.text
    assert got.json()["held"]["outcome"] == "blocked" and "id" not in got.json()
    assert len(judge.calls) == calls + 1 and not any(judge.locked[calls:])   # checked as cara, outside the write
    assert api.get("/api/v2/threads", headers=headers("cara-test")).json()["dms"] == []     # no DM was made
    assert hold_row(api, got.json()["held"]["id"])["author"] == "human:cara"


def test_a_block_holds_a_new_threads_first_message_and_makes_no_thread(ruled):
    api, judge = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"everyone": True}})
    got = api.post("/api/v2/threads", json={"title": "Held", "members": ["human:ben"], "body": GOLD},
                   headers=headers("cara-test"))
    assert got.status_code == 200, got.text
    assert got.json()["held"]["outcome"] == "blocked" and "id" not in got.json()
    assert api.get("/api/v2/threads", headers=headers("cara-test")).json()["threads"] == []


def test_a_reaction_is_one_per_person_and_needs_read_on_the_message(api):
    thread = new_thread(api, members=("human:ben",))
    message = say(api, thread["id"], "Shall we ship?")
    path = f"/api/v2/messages/{message['id']}/reactions/:+1:"
    first = api.put(path, headers=headers("ben-test"))
    assert first.status_code == 200 and first.json() == [{"emoji": ":+1:", "count": 1, "actors": ["human:ben"]}], first.text
    assert api.put(path, headers=headers("ben-test")).json() == first.json()          # the same emoji twice is one
    both = api.put(path, headers=headers("ana-test")).json()
    assert both == [{"emoji": ":+1:", "count": 2, "actors": ["human:ben", "human:ana"]}]
    assert api.delete(path, headers=headers("ben-test")).json() == [{"emoji": ":+1:", "count": 1, "actors": ["human:ana"]}]
    assert api.put(path, headers=headers("cara-test")).status_code == 404               # not a member: unreadable
    assert api.put(f"/api/v2/messages/{message['id']}/reactions/bad%20emoji", headers=headers("ben-test")).status_code == 422


def test_open_earlier_is_a_bot_homes_and_only_its_managers_open_it(api):
    home = api.get("/api/v2/threads/home/bot:ops", headers=headers()).json()
    opened = api.post(f"/api/v2/threads/{home['id']}/open-earlier", headers=headers())
    assert opened.status_code == 200 and opened.json() == {"home": "bot:ops", "messages": 0}, opened.text
    thread = new_thread(api, members=("human:ben",))
    assert api.post(f"/api/v2/threads/{thread['id']}/open-earlier", headers=headers()).status_code == 422
    set_bot_access(api, "ops", {"people": ["cara"]}, {"people": ["cara"]}, {"people": ["cara"]})
    assert api.post(f"/api/v2/threads/{home['id']}/open-earlier", headers=headers("cara-test")).status_code == 403
    set_bot_access(api, "ops", {"people": ["cara"]}, {"people": []}, {"people": []})
    assert api.post(f"/api/v2/threads/{home['id']}/open-earlier", headers=headers("cara-test")).status_code == 404


def test_the_sidebar_with_an_actor_lists_the_threads_they_are_in_that_the_viewer_reads(api):
    shared = new_thread(api, title="With Ben", members=("human:ben",))
    other = new_thread(api, title="Cara only", members=("human:cara",))
    listed = api.get("/api/v2/threads", params={"with": "human:ben"}, headers=headers("ana-test")).json()
    ids = {t["id"] for t in listed["threads"] + listed["dms"]}
    assert shared["id"] in ids and other["id"] not in ids and listed["attention"] == []
    stranger = api.get("/api/v2/threads", params={"with": "human:ben"}, headers=headers("cara-test")).json()
    assert shared["id"] not in {t["id"] for t in stranger["threads"] + stranger["dms"]}


def test_a_file_with_a_message_is_linked_to_its_thread_and_a_stranger_gets_404(api):
    thread = new_thread(api, members=("human:ben",))
    sent = api.post(f"/api/v2/uploads/threads/{thread['id']}/messages", data={"text": "Budget draft"},
                    files={"files": ("budget.txt", b"line items", "text/plain")}, headers=headers("ana-test"))
    assert sent.status_code == 200, sent.text
    message = sent.json()["message"]
    assert message["body"] == "Budget draft" and message["refs"]["attachments"][0]["name"] == "budget.txt"
    assert sent.json()["thread"]["id"] == thread["id"]
    assert any(m.get("id") == message["id"] for m in message_in(api, thread["id"], token="ben-test"))
    stranger = api.post(f"/api/v2/uploads/threads/{thread['id']}/messages", data={"text": "x"},
                        files={"files": ("x.txt", b"x", "text/plain")}, headers=headers("cara-test"))
    assert stranger.status_code == 404


def test_a_thread_view_carries_its_first_page_of_members(api):
    thread = new_thread(api, members=("human:ben", "human:cara"))
    page = get_thread(api, thread["id"], token="ana-test").json()["members"]
    assert {"human:ben", "human:cara"} <= {m["actor"] for m in page["members"]}
    assert "next_cursor" in page


def bot_rows(api, token):
    body = api.get("/api/employees", headers=headers(token)).json()
    rows = body if isinstance(body, list) else body["employees"]
    return {r["name"]: r for r in rows if "my_access" in r}


def test_the_people_list_says_my_access_with_see_read_write_and_manage(api):
    set_bot_access(api, "ops", {"people": ["cara"]}, {"people": ["cara"]}, {"people": ["cara"]})
    cara = bot_rows(api, "cara-test")
    assert cara["ops"]["my_access"] == {"see": True, "read": True, "write": True, "manage": False}
    assert "inbox" not in cara                                          # the mail bot is Ana's alone
    assert bot_rows(api, "ana-test")["ops"]["my_access"]["manage"] is True


def sidebar(api, token="ben-test", **params):
    response = api.get("/api/v2/threads", params=params, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def row_of(listed, thread_id):
    return next((t for t in listed["threads"] + listed["dms"] if t["id"] == thread_id), None)


def post_as(api, thread_id, actor, text, **kw):
    with api.app.state.store.transaction() as c:
        return threads.post(c, actor, thread_id, text, **kw)


def test_the_sidebar_ranks_attention_then_unread_then_recent_before_it_pages(api):
    mentioned = new_thread(api, title="Mentioned")
    post_as(api, mentioned["id"], "human:ana", "Can you check this?", to_actor="human:ben")
    unread = new_thread(api, title="Unread")
    say(api, unread["id"], "A plain update")            # a person's post is unread for Ben, and asks nothing of him
    recent = [new_thread(api, title=f"Recent {n}") for n in range(4)]
    for thread in recent:
        post_as(api, thread["id"], "bot:ops", "Newer than both, from a bot")
    first = sidebar(api, limit=1)
    # The oldest row, and a one-row page still holds it: it is ranked before the page is cut.
    assert [t["id"] for t in first["attention"]] == [mentioned["id"]] and row_of(first, mentioned["id"])
    second = sidebar(api, limit=1, cursor=first["next_cursor"])
    assert [t["id"] for t in second["threads"]] == [unread["id"]]
    assert second["attention"] == []                    # unread alone is not Needs attention
    seen, cursor = [mentioned["id"], unread["id"]], second["next_cursor"]
    while cursor:
        page = sidebar(api, limit=2, cursor=cursor)
        seen += [t["id"] for t in page["threads"] + page["dms"]]
        cursor = page["next_cursor"]
    assert seen == [mentioned["id"], unread["id"], *[t["id"] for t in reversed(recent)]]    # all of them, once each


def test_a_root_row_speaks_for_its_sub_threads_for_each_viewer(api):
    root = new_thread(api, title="Root", members=("human:ben", "human:cara"), body="Kickoff")
    assert api.post(f"/api/v2/threads/{root['id']}/read", json={"seq": root["last_seq"] or 1 << 40},
                    headers=headers("ben-test")).status_code == 200
    first = message_in(api, root["id"])[0]
    sub = new_thread(api, title="Sub", members=("human:ben", "human:cara"), parent_message_id=first["id"])
    other = new_thread(api, title="Other", members=("human:ben",))
    post_as(api, other["id"], "bot:ops", "Older than the reply below")
    for token in ("ben-test", "cara-test"):
        response = api.post(f"/api/v2/threads/{root['id']}/read-all", headers=headers(token))
        assert response.status_code == 200, response.text
    reply = say(api, sub["id"], "A reply below the root", token="cara-test")
    ben = row_of(sidebar(api), root["id"])
    assert ben["me"]["unread"] == 1 and ben["activity_seq"] == reply["seq"] and ben["activity_seq"] > ben["last_seq"]
    assert ben["last_message_at"] == reply["created"]
    assert [t["id"] for t in sidebar(api)["threads"]][0] == root["id"]            # newer than Other, by the sub-thread
    assert row_of(sidebar(api, "cara-test"), root["id"])["me"]["unread"] == 0      # her own reply is not unread for her
    post_as(api, sub["id"], "bot:ops", "A bot below the root")
    assert api.post(f"/api/v2/threads/{sub['id']}/read", json={"seq": 1 << 40}, headers=headers("ben-test")).status_code == 200
    post_as(api, sub["id"], "bot:ops", "Another bot below the root")
    quiet = row_of(sidebar(api), root["id"])["me"]
    assert quiet["unread"] == 0 and quiet["activity"] is True                      # a dot on the root, not a count
    assert row_of(sidebar(api, "ben-test", **{"with": "human:cara"}), root["id"])["me"]["activity"] is True


def test_a_reply_below_a_thread_a_person_cannot_read_lists_nothing_for_them(api):
    root = new_thread(api, title="Salary bands", members=("human:ben",), body="Kickoff")
    first = message_in(api, root["id"])[0]
    sub = new_thread(api, title="Below", members=(), parent_message_id=first["id"])
    say(api, sub["id"], "A reply below")
    assert row_of(sidebar(api), root["id"])["me"]["unread"] == 2         # Ben's: the kickoff and the reply
    assert root["id"] not in str(sidebar(api, "cara-test")) and sub["id"] not in str(sidebar(api, "cara-test"))


def test_releasing_a_held_upload_keeps_its_files_for_the_people_in_the_thread(ruled):
    api, _ = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"everyone": True}})
    thread = new_thread(api, members=("human:ben", "human:cara"))
    sent = api.post(f"/api/v2/uploads/threads/{thread['id']}/messages", data={"text": GOLD},
                    files={"files": ("gold.txt", b"swatches", "text/plain")}, headers=headers("cara-test"))
    assert sent.status_code == 200, sent.text
    held = sent.json()["held"]
    view = get(api, f"rule-holds/{held['id']}")
    assert post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]}, token="ana-test")["hold"]["state"] == "sent"
    with api.app.state.store.read() as c:
        row = c.execute("SELECT id, refs_json FROM messages WHERE body=?", (GOLD,)).fetchone()
        linked = [r[0] for r in c.execute("SELECT blob_id FROM message_assets WHERE message_id=?", (row["id"],))]
    [attached] = [m for m in message_in(api, thread["id"], token="ben-test") if m.get("id") == row["id"]]
    [file] = attached["refs"]["attachments"]
    assert file["name"] == "gold.txt" and linked == [file["id"]]
    got = api.get(f"/api/v2/files/{file['id']}", headers=headers("ben-test"))
    assert got.status_code == 200 and got.content == b"swatches"


def test_a_native_held_post_is_on_the_needs_list_and_moves_its_thread_up_for_who_reads_it(ruled):
    api, _ = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"everyone": True}})
    quiet = new_thread(api, title="Held here", members=("human:ben",))
    say(api, quiet["id"], "Hello.")
    for later in ("Newer one", "Newer two"):
        new_thread(api, title=later, members=("human:ben",))
    held = api.post(f"/api/v2/threads/{quiet['id']}/messages", json={"text": GOLD}, headers=headers("ben-test")).json()["held"]
    mine = [i for i in get(api, "needs-you", token="ben-test")["items"] if i["kind"] == "thread_held"]
    assert [(i["id"], i["thread_id"]) for i in mine] == [(held["id"], quiet["id"])]
    assert [t["id"] for t in sidebar(api, limit=1)["attention"]] == [quiet["id"]]
    reviewing = [i for i in get(api, "needs-you", token="ana-test")["items"] if i["kind"] == "thread_held"]
    assert [i["id"] for i in reviewing] == [held["id"]]                   # Ana wrote the rule: it names her reviewer
    assert quiet["id"] in {t["id"] for t in sidebar(api, "ana-test")["attention"]}
    # A reviewer outside the thread: her list says a hold waits on her, and her sidebar says nothing of the thread.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE org_rule_holds SET reviewer='human:cara' WHERE id=?", (held["id"],))
    review = [i for i in get(api, "needs-you", token="cara-test")["items"] if i["kind"] == "thread_held"]
    assert [i["id"] for i in review] == [held["id"]] and review[0]["thread_id"] is None
    assert quiet["id"] not in str(sidebar(api, "cara-test"))
    assert post(api, f"rule-holds/{held['id']}/keep", {}, token="ben-test")
    assert sidebar(api, limit=1)["attention"] == []
