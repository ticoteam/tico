"""Who a thread message reaches, over real SQLite: following on a post or a mention, the unsolicited cap per delivery,
routing a message that names nobody, one batched run for a bot's digest, what never wakes a bot, what a join leaves
unread, what an edit or delete changes, retention, and one webhook event per message. Fictional people only: ana (owner),
ben and cara (humans); the bots come from the api fixture.
"""

import json

import pytest

from backend import thread_bot_digest, thread_retention, thread_routing, threads
from backend.store import H
from backend.tests.test_api import api, headers  # noqa: F401  (the fixture and its helper)


def new_thread(api, audience="members", members=("human:ben",), title="Budget", token="ana-test"):
    payload = {"title": title, "audience": audience}
    if audience == "members":
        payload["members"] = list(members)
    response = api.post("/api/v2/threads", json=payload, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def say(api, thread_id, text, token="ana-test", key=None, parent=None):
    payload = {"text": text, **({"parent_message_id": parent} if parent else {})}
    response = api.post(f"/api/v2/threads/{thread_id}/messages", json=payload, headers=headers(token, key))
    assert response.status_code == 200, response.text
    return response.json()


def bot_post(api, bot, thread_id, text, **kwargs):
    with api.app.state.store.transaction() as c:
        return threads.post(c, bot, thread_id, text, **kwargs)


def rows(api, sql, *args):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def follower(api, thread_id, actor):
    found = rows(api, "SELECT following, notify, last_read_seq FROM thread_followers WHERE thread_id=? AND actor=?",
                 thread_id, actor)
    return found[0] if found else None


def delivery(api, message_id, recipient):
    found = rows(api, "SELECT * FROM message_deliveries WHERE message_id=? AND recipient=?", message_id, recipient)
    return found[0] if found else None


def queued_job(api, carrier_id):
    found = rows(api, "SELECT state FROM jobs WHERE message_id=?", carrier_id)
    return found[0]["state"] if found else None


def on_digest(api, bot, thread_id):
    with api.app.state.store.transaction() as c:
        threads.follow(c, threads.actor_viewer(c, bot), thread_id, notify="digest")


def edit(api, message_id, text, token="ana-test"):
    response = api.patch(f"/api/v2/messages/{message_id}", json={"text": text}, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def delete(api, message_id, token="ana-test"):
    response = api.delete(f"/api/v2/messages/{message_id}", headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


# ----------------------------------------------------------------------------- following (18)
def test_posting_follows_a_thread_only_read_so_far_and_keeps_the_read_mark(api):
    thread = new_thread(api, audience="company")
    seq = say(api, thread["id"], "First")["seq"]
    api.post(f"/api/v2/threads/{thread['id']}/read", json={"seq": seq}, headers=headers("ben-test"))
    assert follower(api, thread["id"], "human:ben")["following"] == 0
    say(api, thread["id"], "Mine", token="ben-test")
    row = follower(api, thread["id"], "human:ben")
    assert row["following"] == 1 and row["last_read_seq"] >= seq


def test_a_mention_follows_and_keeps_the_notify_level_but_a_dm_delivery_never_undoes_an_unfollow(api):
    thread = new_thread(api, audience="company")
    seq = say(api, thread["id"], "First")["seq"]
    api.post(f"/api/v2/threads/{thread['id']}/read", json={"seq": seq}, headers=headers("cara-test"))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE thread_followers SET notify='digest' WHERE thread_id=? AND actor='human:cara'", (thread["id"],))
    say(api, thread["id"], "Over to @cara")
    row = follower(api, thread["id"], "human:cara")
    assert (row["following"], row["notify"], row["last_read_seq"]) == (1, "digest", seq)

    dm = api.post("/api/v2/threads/dm", json={"members": ["human:ben"], "text": "Hello"}, headers=headers()).json()
    assert api.delete(f"/api/v2/threads/{dm['id']}/follow", headers=headers("ben-test")).status_code == 200
    say(api, dm["id"], "Still there?")
    assert follower(api, dm["id"], "human:ben")["following"] == 0


# ----------------------------------------------------------------------------- the unsolicited cap (19)
def test_a_further_delivery_over_the_cap_is_not_delivered_and_its_author_is_told(api, monkeypatch):
    monkeypatch.setattr(H, "UNSOLICITED_PER_DAY", 1)
    thread = new_thread(api, audience="company")
    first = bot_post(api, "bot:ops", thread["id"], "One for @ben")
    assert delivery(api, first["id"], "human:ben")["unsolicited"] == 1
    second = bot_post(api, "bot:ops", thread["id"], "Two for @ben")
    assert delivery(api, second["id"], "human:ben") is None
    assert {"rule": "unsolicited_cap", "actor": "human:ben"} in second["warnings"]
    assert second["body"] == "Two for @ben"       # the message itself stays in the thread


def test_each_person_on_a_post_is_classified_on_their_own(api):
    thread = new_thread(api, audience="company")
    say(api, thread["id"], "@ops can you check this?", token="ben-test")
    answer = bot_post(api, "bot:ops", thread["id"], "Done @ben, and fyi @cara")
    assert delivery(api, answer["id"], "human:ben")["unsolicited"] == 0
    assert delivery(api, answer["id"], "human:cara")["unsolicited"] == 1


def test_opening_a_home_asks_for_nothing_but_opening_a_dm_does(api):
    with api.app.state.store.transaction() as c:
        home = threads.home(c, "bot:ops", create=True)
    say(api, home["id"], "Morning", token="ben-test")
    with api.app.state.store.transaction() as c:
        assert threads.solicited(c, home["id"], "bot:ops", "human:ben", addressed=False)
    bot_post(api, "bot:ops", home["id"], "Morning")
    with api.app.state.store.transaction() as c:
        assert not threads.solicited(c, home["id"], "bot:ops", "human:ben")
        assert not threads.solicited(c, home["id"], "bot:ops", "human:ben", addressed=False)

    dm = api.post("/api/v2/threads/dm", json={"members": ["bot:ops"], "text": "Hello"}, headers=headers("ben-test")).json()
    bot_post(api, "bot:ops", dm["id"], "Hello")
    with api.app.state.store.transaction() as c:
        assert threads.solicited(c, dm["id"], "bot:ops", "human:ben", addressed=False)


# ----------------------------------------------------------------------------- routing (20)
class Model:
    """Answers the routing questions with a score per bot; every other bot scores 0."""

    def __init__(self, scores, asks=1.0):
        self.scores, self.asks, self.calls = scores, asks, []

    def __call__(self, state, questions, label=None):
        self.calls.append((state, sorted(questions)))
        answers = {key: {"noul": 0.0} for key in questions}
        answers["asks"] = {"noul": self.asks}
        answers.update({"bot:" + slug: {"noul": score} for slug, score in self.scores.items()})
        return {"answers": answers, "usage": {"input_tokens": 10}}


def routed_thread(api, bots=("bot:ops", "bot:cpo", "bot:coo", "bot:finance")):
    return new_thread(api, members=("human:ben", *bots))


def test_a_message_naming_nobody_wakes_at_most_three_bots_through_the_wake_rule(api):
    store = api.app.state.store
    thread = routed_thread(api)
    message = say(api, thread["id"], "Who can look at the budget?")
    assert rows(api, "SELECT 1 FROM jobs j JOIN message_deliveries d ON d.carrier_id=j.message_id WHERE d.message_id=?",
                message["id"]) == []       # the post waits on no model
    model = Model({"ops": 0.9, "cpo": 0.95, "coo": 0.97, "finance": 0.99})
    woken = thread_routing.drain(store, primary=model)
    assert sorted(woken) == ["bot:coo", "bot:cpo", "bot:finance"]
    for bot in ("bot:coo", "bot:cpo", "bot:finance"):
        row = delivery(api, message["id"], bot)
        assert row["reason"] == "routing" and row["carrier_id"] and queued_job(api, row["carrier_id"]) == "queued"
    assert delivery(api, message["id"], "bot:ops") is None
    assert rows(api, "SELECT 1 FROM thread_routings") == []
    assert len(model.calls) == 1
    assert len(rows(api, "SELECT 1 FROM events WHERE action='judge.call' AND target=?", thread_routing.JUDGE_LABEL)) == 1
    assert thread_routing.drain(store, primary=model) == [] and len(model.calls) == 1


def test_routing_calls_no_model_with_fewer_than_two_bots_and_for_a_named_bot_or_a_deleted_message(api):
    store = api.app.state.store
    model = Model({"ops": 1.0, "cpo": 1.0})
    one = new_thread(api, members=("human:ben", "bot:ops"))
    say(api, one["id"], "Anyone?")
    several = routed_thread(api)
    named = say(api, several["id"], "@ops please")
    assert rows(api, "SELECT 1 FROM thread_routings WHERE message_id=?", named["id"]) == []
    gone = say(api, several["id"], "Never mind this")
    delete(api, gone["id"])
    thread_routing.drain(store, primary=model)
    assert model.calls == [] and rows(api, "SELECT 1 FROM thread_routings") == []


def test_routing_wakes_nobody_below_the_bar_or_when_nothing_is_asked_or_without_a_model(api):
    store = api.app.state.store
    thread = routed_thread(api)
    for label, model in (("low", Model({"ops": 0.01})), ("chat", Model({"ops": 1.0}, asks=0.0)), ("none", None)):
        message = say(api, thread["id"], f"Message {label}")
        store.settings.__dict__.setdefault("decisions_monthly_usd", 0)
        assert thread_routing.drain(store, primary=model) == []
        assert rows(api, "SELECT 1 FROM message_deliveries WHERE message_id=? AND reason='routing'", message["id"]) == []
        assert rows(api, "SELECT 1 FROM thread_routings") == []
    assert len(rows(api, "SELECT 1 FROM messages WHERE conversation_id=? AND body LIKE 'Message %'", thread["id"])) == 3


def test_a_bots_unaddressed_post_is_not_routed_and_a_failed_call_waits_and_retries(api):
    from clients import judge as J
    store = api.app.state.store
    thread = routed_thread(api)
    bot_post(api, "bot:ops", thread["id"], "A status line")
    assert rows(api, "SELECT 1 FROM thread_routings") == []
    message = say(api, thread["id"], "Who has this?")

    def down(state, questions, label=None):
        raise J.JudgeError("provider", "down")
    assert thread_routing.drain(store, primary=down) == []
    row = rows(api, "SELECT attempts, next_attempt_at FROM thread_routings WHERE message_id=?", message["id"])[0]
    assert row["attempts"] == 1 and row["next_attempt_at"] > H.now()
    assert rows(api, "SELECT 1 FROM events WHERE action='judge.call'") == []


# ----------------------------------------------------------------------------- bot digests (21)
def test_a_digest_bot_gets_one_run_for_all_its_deliveries_with_no_words_in_the_notice(api):
    store = api.app.state.store
    first = new_thread(api, audience="company", title="One")
    second = new_thread(api, audience="company", title="Two")
    on_digest(api, "bot:ops", first["id"])
    on_digest(api, "bot:ops", second["id"])
    sent = [say(api, first["id"], "Secret number 41"), say(api, first["id"], "Secret number 42"),
            say(api, second["id"], "Secret number 43")]
    assert all(delivery(api, m["id"], "bot:ops")["mode"] == "digest" for m in sent)
    jobs_before = rows(api, "SELECT id FROM jobs WHERE bot='ops'")

    assert thread_bot_digest.sweep(store) == 1
    settled = [delivery(api, m["id"], "bot:ops") for m in sent]
    assert {d["state"] for d in settled} == {"delivered"} and len({d["carrier_id"] for d in settled}) == 1
    notice = rows(api, "SELECT * FROM messages WHERE id=?", settled[0]["carrier_id"])[0]
    assert "Secret" not in notice["body"] and first["id"] in notice["body"] and second["id"] in notice["body"]
    jobs = rows(api, "SELECT id FROM jobs WHERE bot='ops'")
    assert len(jobs) == len(jobs_before) + 1

    # Nothing further pending, and a later message waits while this batch has not started: still one run.
    assert thread_bot_digest.sweep(store) == 0
    later = say(api, first["id"], "Secret number 44")
    assert thread_bot_digest.sweep(store) == 0
    assert delivery(api, later["id"], "bot:ops")["state"] == "pending"


def test_a_digest_delivery_the_bot_can_no_longer_read_goes_moot_and_a_quiet_run_writes_nothing(api):
    store = api.app.state.store
    thread = new_thread(api, members=("human:ben", "bot:ops"))
    on_digest(api, "bot:ops", thread["id"])
    message = say(api, thread["id"], "Numbers are in")
    assert delivery(api, message["id"], "bot:ops")["state"] == "pending"
    with store.transaction() as c:
        c.execute("DELETE FROM thread_members WHERE thread_id=? AND actor='bot:ops'", (thread["id"],))
    before = rows(api, "SELECT COUNT(*) AS n FROM messages")[0]["n"]
    assert thread_bot_digest.sweep(store) == 0
    assert delivery(api, message["id"], "bot:ops")["state"] == "moot"
    assert rows(api, "SELECT COUNT(*) AS n FROM messages")[0]["n"] == before

    events = rows(api, "SELECT COUNT(*) AS n FROM events")[0]["n"]
    assert thread_bot_digest.sweep(store) == 0
    assert rows(api, "SELECT COUNT(*) AS n FROM events")[0]["n"] == events


def test_a_paused_digest_bot_keeps_its_deliveries_and_a_mention_still_wakes_it_at_once(api):
    store = api.app.state.store
    thread = new_thread(api, audience="company")
    on_digest(api, "bot:ops", thread["id"])
    named = say(api, thread["id"], "@ops now please")
    assert delivery(api, named["id"], "bot:ops")["mode"] == "queue"
    message = say(api, thread["id"], "Numbers are in")
    with store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE slug='ops'")
    assert thread_bot_digest.sweep(store) == 0
    assert delivery(api, message["id"], "bot:ops")["state"] == "pending"


def test_the_run_a_digest_notice_starts_reads_only_the_threads_the_bot_is_still_in(api):
    store = api.app.state.store
    kept = new_thread(api, members=("human:ben", "bot:ops"), title="Kept")
    lost = new_thread(api, members=("human:ben", "bot:ops"), title="Lost")
    for thread in (kept, lost):
        on_digest(api, "bot:ops", thread["id"])
        say(api, thread["id"], "Numbers are in")
    assert thread_bot_digest.sweep(store) == 1
    notice = rows(api, "SELECT * FROM messages WHERE from_actor=? AND to_actor='bot:ops' "
                  "AND json_extract(refs_json,'$.wake')=?", H.KEEPER, thread_bot_digest.WAKE)[0]
    with store.transaction() as c:
        c.execute("DELETE FROM thread_members WHERE thread_id=? AND actor='bot:ops'", (lost["id"],))
        c.execute("PRAGMA defer_foreign_keys=ON")
        thread_bot_digest.grant(c, "att-digest", notice)
        granted = [r[0] for r in c.execute("SELECT conversation_id FROM attempt_conversations WHERE attempt_id='att-digest'")]
        assert granted == [kept["id"]]
        c.execute("DELETE FROM attempt_conversations WHERE attempt_id='att-digest'")


# ----------------------------------------------------------------------------- what never wakes (22)
@pytest.mark.parametrize("refs", [{"import": True}, {"replay": True}, {"origin": {"actor": "bot:ops", "id": "x@peer"}}])
def test_an_import_a_replay_or_a_bots_own_message_never_starts_its_run_even_addressed_to_it(api, refs):
    store = api.app.state.store
    with store.transaction() as c:
        direct = threads.dm(c, "human:ana", ["bot:ops"])
        message = H.say(c, "human:ana", "bot:ops", "Coming back", conversation_id=direct["id"], refs=refs)
    assert rows(api, "SELECT 1 FROM jobs WHERE message_id=?", message["id"]) == []
    assert delivery(api, message["id"], "bot:ops")["mode"] == "none"
    assert rows(api, "SELECT 1 FROM messages WHERE from_actor=? AND json_extract(refs_json,'$.carries')=?",
                H.KEEPER, message["id"]) == []


def test_a_bots_own_message_coming_back_does_not_reach_its_digest_or_its_mention(api):
    store = api.app.state.store
    thread = new_thread(api, audience="company")
    on_digest(api, "bot:ops", thread["id"])
    with store.transaction() as c:
        message = threads.post(c, "human:ana", thread["id"], "Back from @ops",
                               refs={"origin": {"actor": "bot:ops", "id": "x@peer"}})
    assert delivery(api, message["id"], "bot:ops") is None


def test_the_same_origin_still_wakes_another_bot(api):
    with api.app.state.store.transaction() as c:
        direct = threads.dm(c, "human:ana", ["bot:cpo"])
        message = H.say(c, "human:ana", "bot:cpo", "From ops", conversation_id=direct["id"],
                        refs={"origin": {"actor": "bot:ops", "id": "x@peer"}})
    assert rows(api, "SELECT 1 FROM jobs WHERE message_id=?", message["id"])


# ----------------------------------------------------------------------------- what a join leaves unread (24)
def viewer_of(api, c, token):
    return threads.viewer(api.app.state.auth, api.app.state.settings.test_identities[token])


def me(api, thread_id, token):
    response = api.get(f"/api/v2/threads/{thread_id}", headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()["me"]


def test_a_join_leaves_old_sub_thread_messages_read_and_a_reader_keeps_what_it_has_not_read(api):
    thread = new_thread(api, members=("human:ben",))
    root_message = say(api, thread["id"], "Top")
    reply = say(api, thread["id"], "In the sub-thread", parent=root_message["id"])
    sub = reply["conversation_id"]
    assert me(api, sub, "ben-test")["unread"] == 1
    for actor in ("human:cara", "human:ben"):
        added = api.post(f"/api/v2/threads/{thread['id']}/members", json={"actor": actor}, headers=headers())
        assert added.status_code == 200, added.text
    assert me(api, sub, "cara-test")["unread"] == 0
    row = follower(api, sub, "human:cara")
    assert row["following"] == 0                  # a read mark, not a follow
    assert me(api, sub, "ben-test")["unread"] == 1
    say(api, thread["id"], "New", parent=root_message["id"])
    assert me(api, sub, "cara-test")["unread"] == 1


def test_a_groups_people_join_with_it_by_their_own_read_marks(api, monkeypatch):
    from backend import teammate_access
    monkeypatch.setattr(teammate_access, "group_member_actors", lambda c, group: {"human:cara", "human:ben"})
    thread = new_thread(api, members=("human:ben",))
    say(api, thread["id"], "Before the group")
    with api.app.state.store.transaction() as c:
        joining = threads._newly_reading(c, threads.thread_row(c, thread["id"]), "group:design")
    assert joining == ["human:cara"]


def test_migrated_and_imported_messages_are_never_unread(api):
    store = api.app.state.store
    thread = new_thread(api, members=("human:ben",))
    carried = say(api, thread["id"], "Carried over")
    with store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)",
                  (threads.MIGRATION_KEY, json.dumps({"seq": carried["seq"]})))
    assert me(api, thread["id"], "ben-test")["unread"] == 0
    before = rows(api, "SELECT last_unread_seq FROM conversations WHERE id=?", thread["id"])[0]["last_unread_seq"]
    with store.transaction() as c:
        imported = threads.post(c, "human:ana", thread["id"], "Imported", refs={"import": True})
    assert imported["seq"] > carried["seq"]
    assert me(api, thread["id"], "ben-test")["unread"] == 0
    assert rows(api, "SELECT last_unread_seq FROM conversations WHERE id=?", thread["id"])[0]["last_unread_seq"] == before
    assert delivery(api, imported["id"], "human:ben") is None
    say(api, thread["id"], "Live")
    assert me(api, thread["id"], "ben-test")["unread"] == 1


# ----------------------------------------------------------------------------- edits and mentions (27)
def test_an_edit_that_adds_a_mention_delivers_it_and_the_run_starts_once(api):
    thread = new_thread(api, audience="company")
    message = say(api, thread["id"], "Budget is ready")
    assert delivery(api, message["id"], "human:cara") is None
    edit(api, message["id"], "Budget is ready @cara @ops")
    assert delivery(api, message["id"], "human:cara")["reason"] == "mention"
    woken = delivery(api, message["id"], "bot:ops")
    assert woken["reason"] == "mention" and queued_job(api, woken["carrier_id"]) == "queued"
    assert follower(api, thread["id"], "human:cara")["following"] == 1

    edit(api, message["id"], "Budget is ready, @cara and @ops please")
    assert delivery(api, message["id"], "bot:ops")["carrier_id"] == woken["carrier_id"]
    assert len(rows(api, "SELECT 1 FROM message_deliveries WHERE message_id=? AND recipient='bot:ops'", message["id"])) == 1


def test_a_mention_taken_out_stops_its_run_and_put_back_starts_one(api):
    thread = new_thread(api, audience="company")
    message = say(api, thread["id"], "For @ops")
    first = delivery(api, message["id"], "bot:ops")
    edit(api, message["id"], "For nobody")
    assert delivery(api, message["id"], "bot:ops")["state"] == "moot"
    assert queued_job(api, first["carrier_id"]) == "cancelled"
    edit(api, message["id"], "For @ops after all")
    again = delivery(api, message["id"], "bot:ops")
    assert again["state"] == "pending" and again["carrier_id"] != first["carrier_id"]
    assert queued_job(api, again["carrier_id"]) == "queued"


def test_an_edit_naming_someone_outside_the_thread_warns_its_author_and_delivers_nothing(api):
    thread = new_thread(api, members=("human:ben",))
    message = say(api, thread["id"], "Numbers")
    edited = edit(api, message["id"], "Numbers for @cara")
    assert [(w["rule"], w["actor"]) for w in edited["warnings"]] == [("mention_outside", "human:cara")]
    assert delivery(api, message["id"], "human:cara") is None


# ----------------------------------------------------------------------------- copies of the words (28)
def test_a_retried_post_after_an_edit_or_a_delete_never_replays_the_old_words(api):
    thread = new_thread(api, audience="company")
    message = say(api, thread["id"], "Old words", key="acme-retry-1")
    edit(api, message["id"], "New words")
    retried = say(api, thread["id"], "Old words", key="acme-retry-1")
    assert retried["id"] == message["id"] and retried["body"] == "New words"
    assert retried["thread_id"] == thread["id"]    # what the route added beside the message stays

    gone = say(api, thread["id"], "Taken back", key="acme-retry-2")
    delete(api, gone["id"])
    response = api.post(f"/api/v2/threads/{thread['id']}/messages", json={"text": "Taken back"},
                        headers=headers("ana-test", "acme-retry-2"))
    assert "Taken back" not in response.text


def test_a_carrier_and_a_note_follow_the_edit_and_go_with_the_delete(api):
    store = api.app.state.store
    thread = new_thread(api, audience="company")
    message = say(api, thread["id"], "Wire 41000 to @ops")
    carrier = delivery(api, message["id"], "bot:ops")["carrier_id"]
    with store.transaction() as c:
        threads._defer_note(c, H.message(c, message["id"]), "bot:cpo")

    edit(api, message["id"], "Wire 42000 to @ops")
    assert "42000" in rows(api, "SELECT body FROM messages WHERE id=?", carrier)[0]["body"]
    assert rows(api, "SELECT body FROM notes WHERE message_id=?", message["id"])[0]["body"] == "Wire 42000 to @ops"

    delete(api, message["id"])
    assert rows(api, "SELECT body FROM messages WHERE id=?", carrier)[0]["body"] == ""
    note = rows(api, "SELECT body, cancelled_at FROM notes WHERE message_id=?", message["id"])[0]
    assert note["body"] == "" and note["cancelled_at"]
    everywhere = json.dumps(rows(api, "SELECT body FROM message_revisions WHERE message_id=?", message["id"]))
    assert "41000" not in everywhere and "42000" not in everywhere


def test_a_deleted_message_leaves_its_sub_thread_no_title(api):
    thread = new_thread(api, audience="company")
    message = say(api, thread["id"], "Quarterly secret plan")
    reply = say(api, thread["id"], "Noted", parent=message["id"])
    delete(api, message["id"])
    assert rows(api, "SELECT subject FROM conversations WHERE id=?", reply["conversation_id"])[0]["subject"] == ""


# ----------------------------------------------------------------------------- retention (29)
def patch(api, thread_id, body, token="ana-test"):
    return api.patch(f"/api/v2/threads/{thread_id}", json=body, headers=headers(token))


def age(api, thread_id, created="2020-01-01T00:00:00Z"):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created=? WHERE conversation_id=? AND seq IS NOT NULL", (created, thread_id))


def test_only_a_threads_manager_sets_how_long_a_root_keeps_its_messages(api):
    thread = new_thread(api, members=("human:cara",))
    assert patch(api, thread["id"], {"retention_days": 30}, token="cara-test").status_code == 403
    for wrong in (0, -1, threads.RETENTION_MAX_DAYS + 1):
        answer = patch(api, thread["id"], {"retention_days": wrong})
        assert answer.status_code == 422 and "retention" in answer.text
    assert patch(api, thread["id"], {"retention_days": 30}).status_code == 200
    assert api.get(f"/api/v2/threads/{thread['id']}", headers=headers()).json()["retention_days"] == 30
    assert rows(api, "SELECT 1 FROM events WHERE action='thread.retention' AND target=?", thread["id"])

    assert patch(api, thread["id"], {"title": "Renamed"}).status_code == 200
    assert api.get(f"/api/v2/threads/{thread['id']}", headers=headers()).json()["retention_days"] == 30
    assert patch(api, thread["id"], {"retention_days": None}).status_code == 200
    assert api.get(f"/api/v2/threads/{thread['id']}", headers=headers()).json()["retention_days"] is None

    reply = say(api, thread["id"], "Top")
    sub = say(api, thread["id"], "Below", parent=reply["id"])["conversation_id"]
    assert patch(api, sub, {"retention_days": 30}).status_code == 422


def test_a_purge_clears_the_message_and_every_copy_of_its_words_and_leaves_a_tombstone(api):
    store = api.app.state.store
    thread = new_thread(api, audience="company")
    old = say(api, thread["id"], "Zebrawire 41000 for @ops @cara", key="acme-keep-1")
    edit(api, old["id"], "Zebrawire 42000 for @ops @cara")
    below = say(api, thread["id"], "Zebrareply", parent=old["id"])
    carrier = delivery(api, old["id"], "bot:ops")["carrier_id"]
    with store.transaction() as c:
        threads._defer_note(c, H.message(c, old["id"]), "bot:cpo")
        c.execute("INSERT INTO message_reactions(id,message_id,actor,emoji,created_at) VALUES('r1',?,'human:ben','ok',?)",
                  (old["id"], H.now()))
    assert patch(api, thread["id"], {"retention_days": 30}).status_code == 200
    age(api, thread["id"])
    age(api, below["conversation_id"])
    fresh = say(api, thread["id"], "Zebrafresh")

    assert thread_retention.sweep(store) == 2
    row = rows(api, "SELECT body, deleted_at, seq FROM messages WHERE id=?", old["id"])[0]
    assert row["body"] == "" and row["deleted_at"] and row["seq"]
    assert rows(api, "SELECT body FROM messages WHERE id=?", below["id"])[0]["body"] == ""
    assert rows(api, "SELECT body FROM messages WHERE id=?", fresh["id"])[0]["body"] == "Zebrafresh"
    revisions = json.dumps(rows(api, "SELECT body FROM message_revisions WHERE message_id=?", old["id"]))
    assert "Zebrawire" not in revisions
    assert rows(api, "SELECT 1 FROM message_mentions WHERE message_id=?", old["id"]) == []
    assert rows(api, "SELECT 1 FROM message_reactions WHERE message_id=?", old["id"]) == []
    assert rows(api, "SELECT body FROM messages WHERE id=?", carrier)[0]["body"] == ""
    assert queued_job(api, carrier) == "cancelled"
    assert rows(api, "SELECT body FROM notes WHERE message_id=?", old["id"])[0]["body"] == ""
    assert rows(api, "SELECT subject FROM conversations WHERE id=?", below["conversation_id"])[0]["subject"] == ""

    hits = api.get("/api/v2/threads/search", params={"q": "Zebrawire"}, headers=headers()).json()["hits"]
    assert hits == []
    assert api.get("/api/v2/threads/search", params={"q": "Zebrafresh"}, headers=headers()).json()["hits"]
    retried = api.post(f"/api/v2/threads/{thread['id']}/messages", json={"text": "x"},
                       headers=headers("ana-test", "acme-keep-1"))
    assert "Zebrawire" not in retried.text
    assert rows(api, "SELECT 1 FROM events WHERE action='thread.purged' AND target=?", thread["id"])
    assert "Zebrawire" not in json.dumps(rows(api, "SELECT * FROM events WHERE action='thread.purged'"))


def test_a_purge_runs_in_bounded_batches_and_the_next_run_goes_on(api, monkeypatch):
    store = api.app.state.store
    thread = new_thread(api, audience="company")
    for n in range(5):
        say(api, thread["id"], f"Old {n}")
    patch(api, thread["id"], {"retention_days": 1})
    age(api, thread["id"])
    monkeypatch.setattr(thread_retention, "BATCH", 2)
    monkeypatch.setattr(thread_retention, "MAX_BATCHES", 1)
    assert thread_retention.sweep(store) == 2
    assert len(rows(api, "SELECT 1 FROM messages WHERE conversation_id=? AND deleted_at IS NULL", thread["id"])) == 3
    assert thread_retention.sweep(store) == 2
    assert thread_retention.sweep(store) == 1
    events = rows(api, "SELECT COUNT(*) AS n FROM events")[0]["n"]
    assert thread_retention.sweep(store) == 0
    assert rows(api, "SELECT COUNT(*) AS n FROM events")[0]["n"] == events     # a run with nothing past its time writes nothing


def test_a_purge_sends_no_tombstone_and_nothing_not_yet_sent_to_an_outside_destination(api):
    store = api.app.state.store
    thread = new_thread(api, audience="company")
    sent, waiting = say(api, thread["id"], "Sent already"), say(api, thread["id"], "Not sent yet")
    with store.transaction() as c:
        for message, state, delivered in ((sent, "delivered", H.now()), (waiting, "pending", None)):
            c.execute("INSERT INTO message_deliveries(id,message_id,thread_id,root_id,recipient,mode,reason,state,revision,"
                      "delivered_at,created_at,updated_at) VALUES(?,?,?,?,'slack:acme','none','outside',?,0,?,?,?)",
                      ("d-" + message["id"], message["id"], thread["id"], thread["id"], state, delivered, H.now(), H.now()))
    patch(api, thread["id"], {"retention_days": 1})
    age(api, thread["id"])
    assert thread_retention.sweep(store) == 2
    assert delivery(api, sent["id"], "slack:acme")["state"] == "delivered"
    assert delivery(api, waiting["id"], "slack:acme")["state"] == "moot"


def test_retention_is_set_from_the_cli_and_the_tool(monkeypatch):
    from clients import hubcli, hubtools, remotecli
    calls = []

    class Api:
        def patch(self, path, body, key=None):
            calls.append((path, body))
            return {}

    hubtools.BY_NAME["hub_thread_update"]["fn"](Api(), {"thread": "t1", "retention_days": 30})
    hubtools.BY_NAME["hub_thread_update"]["fn"](Api(), {"thread": "t1", "retention_days": None})
    hubtools.BY_NAME["hub_thread_update"]["fn"](Api(), {"thread": "t1", "title": "Plan"})
    assert [body for _, body in calls] == [{"retention_days": 30}, {"retention_days": None}, {"title": "Plan"}]

    parser = hubcli.build_parser() if hasattr(hubcli, "build_parser") else hubcli.parser()
    calls.clear()
    remotecli.via_tool(Api(), parser.parse_args(["thread", "update", "t1", "--keep-days", "14"]))
    remotecli.via_tool(Api(), parser.parse_args(["thread", "update", "t1", "--keep-forever"]))
    assert [body for _, body in calls] == [{"retention_days": 14}, {"retention_days": None}]


# ----------------------------------------------------------------------------- webhooks (50)
@pytest.fixture
def published(monkeypatch):
    from backend import webhooks
    seen = []
    monkeypatch.setattr(webhooks, "publish", lambda c, type_, subject, data, actor, **kw: seen.append((type_, subject)))
    return seen


def test_a_legacy_write_publishes_one_message_created_as_a_thread_post_does(api, published):
    store = api.app.state.store
    thread = new_thread(api, audience="company")
    published.clear()
    posted = say(api, thread["id"], "For @ops")
    assert [e for e in published if e[0] == "message.created"] == [("message.created", posted["id"])]  # the carrier adds none
    published.clear()
    with store.transaction() as c:
        legacy = H.say(c, "human:ana", "bot:cpo", "Direct word", conversation_id=thread["id"])
    assert [e for e in published if e[0] == "message.created"] == [("message.created", legacy["id"])]


def test_a_home_made_on_first_use_publishes_thread_created_once(api, published):
    with api.app.state.store.transaction() as c:
        home = threads.home(c, "bot:ops", create=True)
        threads.home(c, "bot:ops", create=True)
    assert [e for e in published if e[0] == "thread.created"] == [("thread.created", home["id"])]
