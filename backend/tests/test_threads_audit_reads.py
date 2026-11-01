"""Who may read what, after people leave, narrow access or are hidden: a DM's starter who leaves stops reading, an
existing DM stays open when Write narrows, an external bot added to a public thread reads it until removed, people a
reader may not see are not named, new DMs from the old routes are private to their members, Needs you and search keep to
what the viewer may read. Python, the hub SQL and the live stream answer alike where a read has all three."""

from types import SimpleNamespace

from backend import events as E
from backend import task_privacy, thread_attention, threads, updates, views
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, headers  # noqa: F401  (the fixture and its helper)
from backend.tests.test_teammate_access import NOBODY, acme, set_access, sql_column, who  # noqa: F401
from backend.tests.threads_e2e import add_member, call, create_thread, ok, remove_member, say, timeline


LEGAL = {"teams": ["legal"]}


def renderings(api, thread_id, identity):
    """Whether Python, the SQL rendering and the stream's rendering let this reader read the thread."""
    with api.app.state.store.read() as c:
        v = threads.Viewer(api.app.state.auth, identity)
        python = threads.readable(c, v, thread_id)
        sql = c.execute("SELECT 1 FROM conversations WHERE id=? AND " + threads.readable_sql(c, v, "conversations"),
                        (thread_id,)).fetchone() is not None
        sse = thread_id in threads.readable_ids(c, threads.Viewer(api.app.state.auth, identity), [thread_id])
    assert python == sql == sse, (python, sql, sse)
    return python


def stream(api, identity, after, topics):
    with api.app.state.store.read() as c:
        found, _, _, _ = E.read(c, api.app.state.auth, identity, after, topics=topics, threads_scope=(),
                                task_views=lambda rows, c, who, visible_sql="1": [], task_view=None)
    return found


def latest(api):
    with api.app.state.store.read() as c:
        return E.latest(c)


def task_thread(api, task):
    with api.app.state.store.read() as c:
        return c.execute("SELECT conversation_id FROM tasks WHERE id=?", ((task.get("task") or task)["id"],)).fetchone()[0]


def dm(api, token, *members):
    return ok(call(api, "POST", "threads/dm", token, {"members": list(members)}))


# ----------------------------------------------------------------------------- opening an existing DM (finding 5)
def test_narrowed_write_keeps_an_existing_dm_open_and_gates_only_starting_one(acme):
    api, _ = acme
    made = dm(api, "cara-test", "human:eve")
    set_access(api, "humans/eve", token="eve-test", write=NOBODY)
    assert ok(call(api, "GET", "threads/dm?with=human:eve", "cara-test"))["id"] == made["id"]
    posted = ok(call(api, "POST", "threads/dm", "cara-test", {"members": ["human:eve"], "body": "Still here"}))
    assert posted["id"] == made["id"]
    say(api, "cara-test", made["id"], "For you", to="human:eve")
    # Only a new DM needs Write, on either route.
    assert call(api, "POST", "threads/dm", "cara-test", {"members": ["human:eve", "human:fay"]}).status_code == 403
    assert call(api, "POST", "threads/dm", "fay-test", {"members": ["human:eve"], "body": "Hi"}).status_code == 403
    assert call(api, "GET", "threads/dm?with=human:eve", "fay-test").status_code == 403


def test_a_teammate_i_may_not_see_answers_as_one_who_does_not_exist(acme):
    api, _ = acme
    set_access(api, "humans/eve", token="eve-test", see=NOBODY, read=NOBODY, write=NOBODY)
    hidden = call(api, "GET", "threads/dm?with=human:eve", "fay-test")
    missing = call(api, "GET", "threads/dm?with=human:nobody", "fay-test")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()
    for peer in ("human:eve", "human:nobody"):
        assert call(api, "POST", "threads/dm", "fay-test", {"members": [peer]}).status_code == 404


def test_the_bot_page_dm_opens_the_personal_chat_room_i_already_have(acme):
    api, _ = acme
    room = ok(call(api, "POST", "conversations", "cara-test", {"participants": ["bot:counsel"], "kind": "chat"}))
    assert ok(call(api, "GET", "threads/dm?with=bot:counsel", "cara-test"))["id"] == room["id"]
    set_access(api, "bots/counsel", write={"everyone": False})
    assert ok(call(api, "GET", "threads/dm?with=bot:counsel", "cara-test"))["id"] == room["id"]


def test_addressing_someone_outside_the_thread_still_takes_the_teammate_gate(acme):
    api, _ = acme
    thread = create_thread(api, "cara-test", audience="company")
    set_access(api, "humans/eve", token="eve-test", see=NOBODY, read=NOBODY, write=NOBODY)
    r = call(api, "POST", f"threads/{thread['id']}/messages", "fay-test", {"text": "Hi", "to": "human:eve"})
    assert r.status_code == 404


# ----------------------------------------------------------------------------- "threads with" (finding 7)
def test_threads_with_a_teammate_finds_every_way_they_are_in_one(acme):
    api, _ = acme
    direct = create_thread(api, "ana-test", members=["human:cara"])
    through_group = create_thread(api, "ana-test", members=["group:legal"])
    other = create_thread(api, "ana-test", members=["human:eve"])
    with api.app.state.store.transaction() as c:
        legacy = H.open_conversation(c, "human:ana", ["human:ana", "human:cara"], scope="direct")["id"]
        c.execute("UPDATE conversations SET subject='Old DM' WHERE id=?", (legacy,))
    task = ok(call(api, "POST", "tasks", "ana-test", {"title": "File the brief", "body": "Do", "owner": "human:cara"}))

    def ids(with_, token="ana-test"):
        found = ok(call(api, "GET", f"threads?with={with_}", token))
        return {t["id"] for t in found["threads"] + found["dms"]}

    shared = ids("human:cara")
    assert {direct["id"], through_group["id"], legacy, task_thread(api, task)} <= shared
    assert other["id"] not in shared
    # A teammate the viewer may not see has no threads to show.
    set_access(api, "humans/eve", token="eve-test", see=NOBODY, read=NOBODY, write=NOBODY)
    public = create_thread(api, "ana-test", audience="company", members=["bot:analyst"])
    assert public["id"] in ids("bot:analyst")
    assert ids("human:eve", "fay-test") == set()
    assert other["id"] in ids("human:eve")


# ----------------------------------------------------------------------------- an external bot, public thread (finding 13)
def test_an_external_bot_reads_a_public_thread_exactly_while_it_is_added(acme):
    api, _ = acme
    agent = SimpleNamespace(actor="bot:analyst", role="bot", agent="agent-1", attempt_id="")
    named = create_thread(api, "ana-test", audience="company", members=["bot:analyst"])
    assert renderings(api, named["id"], agent)

    thread = create_thread(api, "ana-test", audience="company")
    assert not renderings(api, thread["id"], agent)
    add_member(api, "ana-test", thread["id"], "bot:analyst")
    assert renderings(api, thread["id"], agent)
    listed = ok(call(api, "GET", f"threads/{thread['id']}/members"))["members"]
    assert [m["actor"] for m in listed] == ["bot:analyst"]
    remove_member(api, "ana-test", thread["id"], "bot:analyst")
    assert not renderings(api, thread["id"], agent)
    assert ok(call(api, "GET", f"threads/{thread['id']}/members"))["members"] == []

    assert call(api, "POST", f"threads/{thread['id']}/members", "ana-test", {"actor": "human:eve"}).status_code == 422


def test_a_public_thread_and_its_bots_change_audience_together(acme):
    api, _ = acme
    agent = SimpleNamespace(actor="bot:analyst", role="bot", agent="agent-1", attempt_id="")
    thread = create_thread(api, "ana-test", audience="company", members=["bot:analyst"])
    ok(call(api, "PATCH", f"threads/{thread['id']}", "ana-test", {"audience": "members"}))
    assert renderings(api, thread["id"], agent)
    remove_member(api, "ana-test", thread["id"], "bot:analyst")
    assert not renderings(api, thread["id"], agent)
    add_member(api, "ana-test", thread["id"], "bot:analyst")
    ok(call(api, "PATCH", f"threads/{thread['id']}", "ana-test", {"audience": "company"}))
    assert renderings(api, thread["id"], agent)


# ----------------------------------------------------------------------------- who a payload names (finding 14)
def test_a_person_i_may_not_see_is_not_named_anywhere_a_public_thread_reads(acme):
    api, _ = acme
    set_access(api, "humans/cara", token="cara-test", see=LEGAL, read=LEGAL, write=LEGAL)
    thread = create_thread(api, "cara-test", audience="members", members=["human:ben"])
    ok(call(api, "PATCH", f"threads/{thread['id']}", "cara-test", {"audience": "company"}))
    after = latest(api)
    said = say(api, "cara-test", thread["id"], "Zanzibar budget")
    ok(call(api, "PUT", f"messages/{said['id']}/reactions/thumbsup", "cara-test"))
    ok(call(api, "PUT", f"messages/{said['id']}/reactions/thumbsup", "ben-test"))

    # REST: the thread, its timeline, its members and search say nothing of her; the message stays.
    assert ok(call(api, "GET", f"threads/{thread['id']}", "eve-test"))["owner_actor"] == ""
    mine = next(m for m in timeline(api, "eve-test", thread["id"]) if m["id"] == said["id"])
    assert mine["from_actor"] == "" and mine["body"] == "Zanzibar budget"
    assert mine["reactions"] == [{"emoji": "thumbsup", "count": 2, "actors": ["human:ben"]}]
    rows = ok(call(api, "GET", f"threads/{thread['id']}/members", "eve-test"))["members"]
    assert [(m["actor"], m["added_by"]) for m in rows] == [("human:ben", "")]
    hits = ok(call(api, "GET", "threads/search?q=zanzibar", "eve-test"))["hits"]
    assert [(h["message_id"], h["from_actor"]) for h in hits] == [(said["id"], "")]

    # The hub SQL says the same.
    where = f"'{thread['id']}'"
    assert sql_column(api, f"SELECT owner_actor FROM conversations WHERE id={where}", "eve-test") == [""]
    assert sql_column(api, f"SELECT DISTINCT from_actor FROM messages WHERE conversation_id={where}", "eve-test") == [""]
    assert sql_column(api, f"SELECT actor FROM thread_members WHERE thread_id={where}", "eve-test") == ["", "human:ben"]
    assert "human:cara" not in sql_column(api, f"SELECT actor FROM thread_followers WHERE thread_id={where}", "eve-test")

    # So does the stream.
    eve = who(api, "eve")
    sent = stream(api, eve, after, ("messages", "threads"))
    message = next(d for _, topic, d in sent if topic == "messages" and d.get("id") == said["id"])
    assert (message.get("message") or message)["from_actor"] != "human:cara"
    assert "human:cara" not in str([d for _, _, d in sent])


def test_whoever_is_in_the_dm_with_a_hidden_person_is_told_who_it_is(acme):
    api, _ = acme
    set_access(api, "humans/cara", token="cara-test", see=LEGAL, read=LEGAL, write=LEGAL)
    thread = dm(api, "cara-test", "human:eve")
    after = latest(api)
    said = say(api, "cara-test", thread["id"], "Hello Eve")
    assert ok(call(api, "GET", f"threads/{thread['id']}", "eve-test"))["owner_actor"] == "human:cara"
    assert next(m for m in timeline(api, "eve-test", thread["id"]) if m["id"] == said["id"])["from_actor"] == "human:cara"
    assert {m["actor"] for m in ok(call(api, "GET", f"threads/{thread['id']}/members", "eve-test"))["members"]} == {
        "human:cara", "human:eve"}
    where = f"'{thread['id']}'"
    assert sql_column(api, f"SELECT DISTINCT from_actor FROM messages WHERE conversation_id={where}", "eve-test") == [
        "human:cara"]
    sent = [d for _, topic, d in stream(api, who(api, "eve"), after, ("messages",)) if topic == "messages"]
    assert "human:cara" in str(sent)


# ----------------------------------------------------------------------------- old routes, new DMs (finding 15)
def test_a_dm_opened_through_the_old_routes_is_private_to_its_members(acme):
    api, _ = acme
    made = ok(call(api, "POST", "conversations", "cara-test", {"participants": ["human:eve"], "kind": "chat"}))
    assert made["audience"] == "personal"
    assert call(api, "GET", f"threads/{made['id']}", "ana-test").status_code == 404
    assert call(api, "GET", f"conversations/{made['id']}/messages", "ana-test").status_code in (403, 404)
    assert not renderings(api, made["id"], who(api, "ana"))
    assert renderings(api, made["id"], who(api, "eve"))
    # The native route finds the same DM, both ways round.
    assert dm(api, "cara-test", "human:eve")["id"] == dm(api, "eve-test", "human:cara")["id"] == made["id"]

    mixed = ok(call(api, "POST", "conversations", "cara-test", {"participants": ["bot:counsel", "bot:analyst"],
                                                                "kind": "chat"}))
    assert mixed["audience"] == "personal"
    assert not renderings(api, mixed["id"], who(api, "ana"))


def test_a_message_with_no_conversation_lands_in_the_one_dm_two_people_have(acme):
    api, _ = acme
    store = api.app.state.store
    native = dm(api, "fay-test", "human:eve")
    with store.transaction() as c:
        assert H.say(c, "human:eve", "human:fay", "Reply")["conversation_id"] == native["id"]
        first = H.say(c, "human:cara", "human:eve", "Hello")["conversation_id"]
        second = H.say(c, "human:eve", "human:cara", "Hello back")["conversation_id"]
    assert first == second
    assert not renderings(api, first, who(api, "ana"))


# ----------------------------------------------------------------------------- a DM's starter (finding 16)
def test_a_starter_who_leaves_a_dm_stops_reading_it_and_receiving_it(acme):
    api, _ = acme
    thread = dm(api, "cara-test", "human:eve", "human:fay")
    assert renderings(api, thread["id"], who(api, "cara"))
    remove_member(api, "cara-test", thread["id"], "human:cara")
    assert not renderings(api, thread["id"], who(api, "cara"))
    assert call(api, "GET", f"threads/{thread['id']}", "cara-test").status_code == 404
    assert thread["id"] not in sql_column(api, "SELECT id FROM conversations", "cara-test")
    with api.app.state.store.read() as c:
        assert "human:cara" not in threads._dm_members(c, threads.thread_row(c, thread["id"]))
        assert sorted(threads._dm_members(c, threads.thread_row(c, thread["id"]))) == ["human:eve", "human:fay"]
    # A new DM with the same people is a new thread.
    assert dm(api, "cara-test", "human:eve", "human:fay")["id"] != thread["id"]


def test_a_bot_starter_removed_from_a_thread_loses_its_participant_grant(acme):
    api, _ = acme
    store = api.app.state.store
    agent = SimpleNamespace(actor="bot:counsel", role="bot", agent="agent-1", attempt_id="")
    with store.transaction() as c:
        thread = threads.create(c, threads.Viewer(api.app.state.auth, agent), title="Brief", members=["human:ana"])
    assert renderings(api, thread["id"], agent)
    remove_member(api, "ana-test", thread["id"], "bot:counsel")
    assert not renderings(api, thread["id"], agent)
    with store.read() as c:
        assert not threads.audience_has(c, thread["id"], "bot:counsel")


def test_a_special_room_stays_its_owners_and_reaches_its_bot(acme):
    api, _ = acme
    room = ok(call(api, "POST", "conversations", "cara-test", {"participants": ["bot:counsel"], "kind": "chat"}))
    assert renderings(api, room["id"], who(api, "cara"))
    assert not renderings(api, room["id"], who(api, "eve"))
    with api.app.state.store.transaction() as c:
        # With its audience stored, as a room the migration or a later writer stamped.
        c.execute("UPDATE conversations SET audience='personal' WHERE id=?", (room["id"],))
    assert renderings(api, room["id"], who(api, "cara"))
    assert not renderings(api, room["id"], who(api, "eve"))
    with api.app.state.store.read() as c:
        assert set(threads._dm_members(c, threads.thread_row(c, room["id"]))) == {"human:cara", "bot:counsel"}


# ----------------------------------------------------------------------------- Needs you (finding 17)
def test_an_ask_leaves_needs_you_with_the_thread_and_comes_back_with_it(acme):
    api, tokens = acme
    store, auth = api.app.state.store, api.app.state.auth
    thread = create_thread(api, "ana-test", members=["bot:counsel", "human:cara"])
    agent = SimpleNamespace(actor="bot:counsel", role="bot", agent="agent-1", attempt_id="")
    with store.transaction() as c:
        asked = threads.post(c, threads.Viewer(auth, agent), thread["id"], "Which clause?", kind="ask",
                             to_actor="human:cara")

    def listed():
        with store.read() as c:
            assert (asked["id"] in {m["id"] for m in H.loose_asks_to(c, "human:cara")}) == (
                asked["id"] in {i["id"] for i in views.needs_items(c, auth, who(api, "cara"), lambda *a, **k: None)})
            return asked["id"] in {m["id"] for m in H.loose_asks_to(c, "human:cara")}

    assert listed()
    remove_member(api, "ana-test", thread["id"], "human:cara")
    assert not listed()
    add_member(api, "ana-test", thread["id"], "human:cara")
    assert listed()


# ----------------------------------------------------------------------------- search (finding 30)
def test_search_keeps_to_a_private_task_and_a_restricted_message(acme, monkeypatch):
    api, _ = acme
    task = ok(call(api, "POST", "tasks", "cara-test", {"title": "Secret", "body": "Do", "owner": "human:cara",
                                                       "private": True}))
    say(api, "cara-test", task_thread(api, task), "Quokka plans")
    open_thread = create_thread(api, "ana-test", audience="company")
    plain = say(api, "ana-test", open_thread["id"], "Quokka forecast")
    held = say(api, "ana-test", open_thread["id"], "Quokka salary")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET restricted=1 WHERE id=?", (held["id"],))
    real = task_privacy.message_readable
    monkeypatch.setattr(task_privacy, "message_readable", lambda c, p, m: m["id"] != held["id"] and real(c, p, m))

    def hits(token, route="threads/search?q=quokka"):
        return ok(call(api, "GET", route, token))

    found = hits("eve-test")
    assert [h["message_id"] for h in found["hits"]] == [plain["id"]] and found["next_cursor"] is None
    assert {h["id"] for h in hits("eve-test", "context/search?q=quokka&source=threads")["results"]} == {plain["id"]}
    assert len(hits("cara-test")["hits"]) == 2      # she reads her own task's discussion


# ----------------------------------------------------------------------------- Needs you and Catch up name no one hidden
def needs(api, token, kind):
    return [i for i in ok(call(api, "GET", "needs-you", token))["items"] if i["kind"] == kind]


def test_needs_you_names_a_hidden_person_only_to_someone_in_the_thread_with_them(acme):
    api, _ = acme
    made = dm(api, "cara-test", "human:eve")
    set_access(api, "humans/cara", token="cara-test", see=LEGAL, read=LEGAL, write=LEGAL)
    thread = create_thread(api, "cara-test", audience="company")
    say(api, "cara-test", thread["id"], "Can you check the lease?", to="human:eve")
    say(api, "cara-test", made["id"], "A word?", to="human:eve")

    item = next(i for i in needs(api, "eve-test", "thread_mention") if i["conversation_id"] == thread["id"])
    assert item["first_line"] == "Can you check the lease?"
    assert (item["requester"], item["origin_actor"], item["from_person"]) == ("", "", None)
    # Eve is in the DM with her, so there she is told who it is.
    item = next(i for i in needs(api, "eve-test", "thread_mention") + needs(api, "eve-test", "thread_dm")
                if i["conversation_id"] == made["id"])
    assert (item["requester"], item["from_person"]) == ("human:cara", "human:cara")


def test_needs_you_names_a_held_writes_author_only_to_someone_who_may_see_them(acme, monkeypatch):
    api, _ = acme
    set_access(api, "humans/cara", token="cara-test", see=LEGAL, read=LEGAL, write=LEGAL)
    held = {"id": "hold-1", "author": "human:cara", "role": "reviewer", "created": "2026-11-01T00:00:00"}
    monkeypatch.setattr(thread_attention, "_holds", lambda c, person: [held])
    with api.app.state.store.read() as c:
        hidden = thread_attention.items(c, api.app.state.auth, who(api, "eve"))
        seen = thread_attention.items(c, api.app.state.auth, who(api, "fay"))
    assert [(i["requester"], i["from_person"]) for i in hidden] == [("", None)]
    assert [(i["requester"], i["from_person"]) for i in seen] == [("human:cara", "human:cara")]


def test_catch_up_never_gives_the_model_a_hidden_persons_name(acme, monkeypatch):
    api, _ = acme
    set_access(api, "humans/cara", token="cara-test", see=LEGAL, read=LEGAL, write=LEGAL)
    thread = create_thread(api, "cara-test", audience="company", members=["human:ben"])
    say(api, "cara-test", thread["id"], "Lease renewal is due Friday.")
    say(api, "fay-test", thread["id"], "Noted.")
    prompts = []

    def writer(system, user):
        prompts.append(user)
        return {"summary": "The lease renewal is due Friday."}

    # What the model reads.
    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: writer)
    ok(call(api, "POST", f"threads/{thread['id']}/catch-up", "eve-test", {"scope": "thread"}))
    assert len(prompts) == 1
    assert "Someone: Lease renewal is due Friday." in prompts[0]
    assert "cara" not in prompts[0].lower()
    assert "Fay: Noted." in prompts[0]
    # Ben is in the thread with her, so his catch-up says who it was.
    ok(call(api, "POST", f"threads/{thread['id']}/catch-up", "ben-test", {"scope": "thread"}))
    assert "Cara: Lease renewal is due Friday." in prompts[1]

    # With no model the reply is an excerpt of the same lines.
    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: None)
    posted = ok(call(api, "POST", f"threads/{thread['id']}/catch-up", "eve-test", {"scope": "thread"}))["posted"]
    assert "Someone: Lease renewal is due Friday." in posted["body"]
    assert "cara" not in posted["body"].lower()
