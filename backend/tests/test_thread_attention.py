"""Needs attention: a thread item is listed for the person it concerns and clears when they act on it (a reply, a read,
a release), a message outside the thread's audience never reaches its person, and a paused thread is listed only to
the people who can manage it."""

from backend import threads
from backend.auth import Identity
from backend import hubdb as H
from backend.tests.test_api import api, as_member, get, post, setup_attempt  # noqa: F401


def members_thread(api, members=("human:ana",), title="Launch"):
    with api.app.state.store.transaction() as c:
        return threads.create(c, "human:cara", title=title, audience="members", members=list(members))["id"]


def say(api, thread_id, text, *, actor="human:cara", **kw):
    with api.app.state.store.transaction() as c:
        return threads.post(c, actor, thread_id, text, **kw)


def needs(api, kind=None, token="ana-test"):
    items = get(api, "needs-you", token=token)["items"]
    return [i for i in items if kind is None or i["kind"] == kind]


def ana_viewer(api):
    return threads.viewer(api.app.state.auth, Identity("human:ana", "owner", "ana@acme.example"))


def test_a_message_to_the_person_is_listed_until_they_reply(api):
    tid = members_thread(api)
    say(api, tid, "Can you look at the lease?", to_actor="human:ana")
    listed = needs(api, "thread_mention")
    assert [i["conversation_id"] for i in listed] == [tid]
    say(api, tid, "On it.", actor="human:ana")
    assert needs(api, "thread_mention") == []


def test_a_question_is_listed_until_the_person_answers_in_the_thread(api):
    tid = members_thread(api)
    say(api, tid, "Which lease, the Main St one?", kind="ask", to_actor="human:ana")
    assert [i["conversation_id"] for i in needs(api, "thread_ask")] == [tid]
    say(api, tid, "The Main St one.", actor="human:ana")
    assert needs(api, "thread_ask") == []


def test_a_mention_from_outside_the_audience_reaches_no_one(api):
    tid = members_thread(api, members=())          # cara alone: a mention of ana does not add her
    say(api, tid, "@ana see this.")
    assert all(i["conversation_id"] != tid for i in needs(api))


def test_an_unread_dm_is_listed_until_it_is_read(api):
    with api.app.state.store.transaction() as c:
        dm = threads.dm(c, "human:cara", ["human:ana"], title="Lease")["id"]
    say(api, dm, "Call me when you can.")
    assert [i["conversation_id"] for i in needs(api, "thread_dm")] == [dm]
    with api.app.state.store.transaction() as c:
        threads.mark_all_read(c, ana_viewer(api), dm)
    assert needs(api, "thread_dm") == []


def hold(api, reviewer="human:ana", author="bot:finance", operation="reply", state="held"):
    """A held write as org_rule_check stores it (its verdict and payload are not read by the list)."""
    with api.app.state.store.transaction() as c:
        hid = H.new_id()
        c.execute("INSERT INTO org_rule_holds(id, operation, payload_json, author, destinations_json, rule_ids_json, "
                  "verdict_json, nonce, state, reviewer, task_id, created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (hid, operation, "{}", author, "[]", "[]", "{}", "nonce", state, reviewer, None, H.now()))
    return hid


def test_a_held_message_is_listed_to_its_reviewer_until_it_is_released(api):
    hid = hold(api)
    assert [i["id"] for i in needs(api, "thread_held")] == [hid]
    assert needs(api, "thread_held", token="cara-test") == []        # the reviewer alone reads a hold
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE org_rule_holds SET state='sent' WHERE id=?", (hid,))
    assert needs(api, "thread_held") == []


def test_a_held_post_is_listed_to_the_person_its_bot_reports_to_until_they_withdraw_it(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='finance'")
    hid = hold(api, reviewer="human:ana", author="bot:finance")
    assert [i["id"] for i in needs(api, "thread_held", token="ben-test")] == [hid]
    assert needs(api, "thread_held", token="cara-test") == []             # a peer does not see it
    assert [i["id"] for i in needs(api, "thread_held")] == [hid]          # the reviewer does
    post(api, f"rule-holds/{hid}/keep", {}, token="ben-test")
    assert needs(api, "thread_held", token="ben-test") == []


def test_a_person_sees_a_hold_on_their_own_post_until_they_withdraw_it(api):
    hid = hold(api, reviewer="human:ana", author="human:cara")
    assert [i["id"] for i in needs(api, "thread_held", token="cara-test")] == [hid]
    assert needs(api, "thread_held", token="ben-test") == []
    post(api, f"rule-holds/{hid}/keep", {}, token="cara-test")
    assert needs(api, "thread_held", token="cara-test") == []


def test_a_held_message_outside_a_thread_write_is_not_listed(api):
    hold(api, operation="mail")
    assert needs(api, "thread_held") == []


def test_a_bots_mention_of_the_person_is_listed_and_its_plain_post_is_not(api):
    setup_attempt(api, "finance")
    tid = members_thread(api)
    say(api, tid, "Numbers are ready.", actor="bot:finance")
    assert needs(api, "thread_mention") == []
    say(api, tid, "@ana the Q3 numbers are ready.", actor="bot:finance")
    assert [i["conversation_id"] for i in needs(api, "thread_mention")] == [tid]


def test_an_unread_dm_shows_only_the_message_the_person_can_read(api):
    """A later message the person cannot read must not set the item's sender or time. Ben is a plain member here: the
    owner reads every migrated shared room, so Ana could read the hidden message too."""
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:
        dm = threads.dm(c, "human:cara", ["human:ben"], title="Lease")["id"]
    visible = say(api, dm, "Call me when you can.")
    with api.app.state.store.transaction() as c:
        hidden = threads.post(c, "human:cara", dm, "Ana's offer is confidential.")
        c.execute("UPDATE messages SET legacy_audience='shared:nobody', restricted=1 WHERE id=?", (hidden["id"],))
    item = needs(api, "thread_dm", token="ben-test")[0]
    assert item["created"] == visible["created"]


def test_a_group_dm_is_listed_like_a_two_person_one(api):
    with api.app.state.store.transaction() as c:
        group = threads.dm(c, "human:cara", ["human:ana", "human:ben"], title="Offer")["id"]
    say(api, group, "Ana and Ben, sign by Friday.")
    assert [i["conversation_id"] for i in needs(api, "thread_dm")] == [group]


def test_a_participants_dm_is_listed_like_a_personal_one(api):
    """A DM from a migrated room carries the participants audience; it reads and lists through its root the same way."""
    with api.app.state.store.transaction() as c:
        dm = threads.dm(c, "human:cara", ["human:ana"], title="Offer")["id"]
        c.execute("UPDATE conversations SET audience='participants' WHERE id=?", (dm,))
    say(api, dm, "Sign by Friday.")
    assert [i["conversation_id"] for i in needs(api, "thread_dm")] == [dm]


def test_every_thread_item_is_reachable_through_the_pages_of_needs_you(api):
    tids = [members_thread(api, title=f"Note {i}") for i in range(51)]
    for tid in tids:
        say(api, tid, "@ana please look.")
    seen, cursor = [], None
    while True:
        page = get(api, "needs-you?limit=50" + (f"&cursor={cursor}" if cursor else ""))
        seen += [i["conversation_id"] for i in page["items"] if i["kind"] == "thread_mention"]
        cursor = page.get("next")
        if not cursor:
            break
    assert sorted(seen) == sorted(tids)


def needs_changes(api, person):
    with api.app.state.store.read() as c:
        return c.execute("SELECT COUNT(*) FROM changes WHERE topic='needs' AND subject_id=?", (person,)).fetchone()[0]


def test_a_thread_change_marks_the_persons_needs_list_to_be_read_again(api):
    tid = members_thread(api)
    before = needs_changes(api, "human:ana")
    say(api, tid, "@ana look.")
    after_post = needs_changes(api, "human:ana")
    with api.app.state.store.transaction() as c:
        threads.mark_all_read(c, ana_viewer(api), tid)
    after_read = needs_changes(api, "human:ana")
    hid = hold(api)
    after_hold = needs_changes(api, "human:ana")
    assert before < after_post < after_read < after_hold, (before, after_post, after_read, after_hold)
    assert hid


def test_a_paused_thread_is_listed_to_its_manager_only(api):
    as_member(api, "ben@acme.example")          # the fixture makes Ben an admin, and an admin manages every thread
    tid = members_thread(api, members=("human:ana", "human:ben"))
    with api.app.state.store.transaction() as c:
        now = H.now()
        c.execute("INSERT INTO message_deliveries(id, message_id, recipient, thread_id, root_id, mode, reason, cause, "
                  "state, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (H.new_id(), H.new_id(), "bot:finance", tid, tid, "queue", "mention", "bot", "capped", now, now))
    assert [i["conversation_id"] for i in needs(api, "thread_capped", token="cara-test")] == [tid]
    assert needs(api, "thread_capped", token="ben-test") == []     # reads the thread, does not manage it


def test_a_bot_post_in_a_group_dm_is_listed_as_unread_for_its_people(api):
    with api.app.state.store.transaction() as c:
        tid = threads.dm(c, "human:cara", ["human:ana", "human:ben"], title="Numbers")["id"]
        c.execute("UPDATE conversations SET audience='participants' WHERE id=?", (tid,))
    say(api, tid, "Numbers are in.", actor="bot:finance")
    assert [i["conversation_id"] for i in needs(api, "thread_dm")] == [tid]
    with api.app.state.store.read() as c:
        row = c.execute("SELECT last_seq, last_unread_seq FROM conversations WHERE id=?", (tid,)).fetchone()
    assert row["last_unread_seq"] == row["last_seq"]
    with api.app.state.store.read() as c:
        assert threads.unread(c, ana_viewer(api), tid)["unread"] == 1
