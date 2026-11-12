"""New message's match (GET /api/v2/threads/match): the threads whose members, the viewer left out, are exactly the named
teammates, newest activity first; the viewer's own DM with one teammate as `dm`; a thread the viewer cannot read or an
archived one never shows; the candidates come through the membership index. Fictional people only: ana (owner), ben
and cara, and the bot ops."""

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.helpers.api import headers  # noqa: F401  (the fixture and its helper)
from backend.tests.helpers.thread_events import who
from backend import thread_api, threads


def new_thread(api, title, members, token="ana-test", audience="members"):
    payload = {"title": title, "audience": audience, "members": list(members)}
    response = api.post("/api/v2/threads", json=payload, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def say(api, thread_id, text, token="ana-test"):
    response = api.post(f"/api/v2/threads/{thread_id}/messages", json={"text": text}, headers=headers(token))
    assert response.status_code == 200, response.text


def match(api, *actors, token="ana-test"):
    response = api.get("/api/v2/threads/match", params=[("actor", a) for a in actors], headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def ids(body):
    return [t["id"] for t in body["threads"]]


def test_only_threads_with_exactly_those_teammates_match_newest_first_and_the_viewer_is_left_out(api):
    pair = new_thread(api, "Pricing", ["human:ben", "human:cara"])
    later = new_thread(api, "Launch", ["human:cara", "human:ben"])
    new_thread(api, "Ben alone", ["human:ben"])
    new_thread(api, "Bigger", ["human:ben", "human:cara", "bot:ops"])
    say(api, later["id"], "newest")
    say(api, pair["id"], "newer still")
    got = match(api, "human:ben", "human:cara")
    assert ids(got) == [pair["id"], later["id"]] and got["dm"] is None
    assert got["threads"][0]["title"] == "Pricing" and got["threads"][0]["audience"] == "members"
    # Naming the viewer too, or a teammate twice, is the same set.
    assert ids(match(api, "human:cara", "human:ana", "human:ben", "human:cara")) == [pair["id"], later["id"]]
    # Ben's view: the same thread, with ana and cara as its others.
    assert ids(match(api, "human:ana", "human:cara", token="ben-test")) == [pair["id"], later["id"]]
    assert ids(match(api, "human:ben", "human:cara", "bot:ops"))[0] != pair["id"]
    assert match(api, "human:nobody")["threads"] == [] and match(api, "human:nobody")["dm"] is None
    assert api.get("/api/v2/threads/match", headers=headers("ana-test")).status_code == 422


def test_a_thread_the_viewer_cannot_read_or_an_archived_one_never_matches(api):
    # ben and cara's thread, without ana: ana names exactly its members and gets nothing.
    theirs = new_thread(api, "Theirs", ["human:cara"], token="ben-test")
    say(api, theirs["id"], "private plan", token="ben-test")
    body = match(api, "human:ben", "human:cara")
    assert theirs["id"] not in str(body) and "Theirs" not in str(body)
    assert ids(match(api, "human:cara", token="ben-test")) == [theirs["id"]]
    shut = new_thread(api, "Shut", ["human:ben", "human:cara"])
    assert ids(match(api, "human:ben", "human:cara")) == [shut["id"]]
    archived = api.patch(f"/api/v2/threads/{shut['id']}", json={"archived": True}, headers=headers("ana-test"))
    assert archived.status_code == 200, archived.text
    assert match(api, "human:ben", "human:cara")["threads"] == []


def test_dm_is_the_viewers_open_dm_with_one_person_or_bot_and_is_not_listed_again(api):
    assert match(api, "human:ben")["dm"] is None
    dm = api.get("/api/v2/threads/dm", params={"with": "human:ben"}, headers=headers("ana-test")).json()
    named = new_thread(api, "Ben and me", ["human:ben"])
    got = match(api, "human:ben")
    assert got["dm"] == dm["id"] and ids(got) == [named["id"]]
    assert match(api, "human:ana", token="ben-test")["dm"] == dm["id"]
    assert match(api, "human:cara")["dm"] is None  # cara's DM with ana does not exist; ben's is not hers
    bot = api.get("/api/v2/threads/dm", params={"with": "bot:ops"}, headers=headers("ana-test")).json()
    assert match(api, "bot:ops")["dm"] == bot["id"]
    # Two teammates are never a DM answer, even with a group DM of exactly them: that is a thread match.
    group = api.post(
        "/api/v2/threads/dm",
        json={"members": ["human:ben", "human:cara"], "title": "Trio"},
        headers=headers("ana-test"),
    ).json()
    got = match(api, "human:ben", "human:cara")
    assert got["dm"] is None and ids(got) == [group["id"]]
    # Another's DM: cara cannot read ana and ben's, so it is no answer for her.
    assert match(api, "human:ben", token="cara-test")["dm"] is None


def test_the_match_reads_its_candidates_through_the_membership_index(api):
    new_thread(api, "Pricing", ["human:ben", "human:cara"])
    seen = []
    with api.app.state.store.read() as c:
        c.set_trace_callback(seen.append)
        try:
            thread_api._match(c, threads.viewer(api.app.state.auth, who(api, "ana-test")), ["human:ben", "human:cara"])
        finally:
            c.set_trace_callback(None)
        [sql] = [s for s in seen if "AS activity FROM conversations r WHERE r.id IN" in s]
        plan = " ".join(str(r[3]) for r in c.execute("EXPLAIN QUERY PLAN " + sql))
    assert "thread_members_actor" in plan, plan
