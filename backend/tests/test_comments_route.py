"""Comments route like posts (threads/tasks spec step 4): a task comment reaches whom it addresses and the task's
followers, each by its way and their level, and nothing is guessed from roles. Steven's case: a person's comment on a
ticket a monitor bot filed does not wake the filer. Fictional people only: ana (owner), ben and cara."""

import pytest

from backend import threads
from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (the fixture and its helpers)


@pytest.fixture(autouse=True)
def private_tasks_have_an_unrelated_manager(api):
    from backend.tests.test_api import unrelated_task_managers
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)


def rows(api, sql, *args):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def delivery(api, message_id, recipient):
    found = rows(api, "SELECT * FROM message_deliveries WHERE message_id=? AND recipient=?", message_id, recipient)
    return found[0] if found else None


def bot_jobs(api, bot):
    return rows(api, "SELECT id, message_id, state FROM jobs WHERE bot_id=? AND state='queued'", bot)


def follows(api, tid, actor):
    with api.app.state.store.read() as c:
        thread = H.task(c, tid)["conversation_id"]
        found = c.execute("SELECT following, notify, reason FROM thread_followers WHERE thread_id=? AND actor=?",
                          (thread, actor)).fetchone()
    return tuple(found) if found else None


def comment(api, tid, text, token="ana-test", expected=200, **more):
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": text, **more}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def follow(api, tid, actor, notify="live"):
    r = api.put(f"/api/v2/tasks/{tid}/followers", json={"following": True, "actor": actor, "notify": notify},
                headers=headers())
    assert r.status_code == 200, r.text


def filed_by_a_bot(api, owner="human:ben"):
    with api.app.state.store.transaction() as c:
        row = H.task_create(c, "bot:cpo", "Reservation 42 is double booked", "Two stays overlap on May 3.", owner,
                            lint=False)
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    return row


def test_a_comment_on_a_ticket_a_bot_filed_does_not_wake_the_filer(api):
    row = filed_by_a_bot(api)
    sent = comment(api, row["id"], "Looked into it: the second stay was cancelled.", token="ben-test")["comment"]
    assert delivery(api, sent["id"], "bot:cpo") is None and bot_jobs(api, "cpo") == []
    assert follows(api, row["id"], "bot:cpo") is None


def test_an_at_mention_wakes_and_follows_and_then_plain_comments_reach_it(api):
    row = filed_by_a_bot(api)
    sent = comment(api, row["id"], "@cpo can you recheck the calendar?", token="ben-test")["comment"]
    assert [j["message_id"] for j in bot_jobs(api, "cpo")] == [sent["id"]]
    assert follows(api, row["id"], "bot:cpo") == (1, "live", "mentioned")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    later = comment(api, row["id"], "Fixed on the channel side too.", token="ben-test")["comment"]
    assert later["recipient_id"] == "bot:cpo" and [j["message_id"] for j in bot_jobs(api, "cpo")] == [later["id"]]


def test_steer_wakes_every_live_bot_follower(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    follow(api, row["id"], "bot:coo")
    follow(api, row["id"], "bot:finance")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    sent = comment(api, row["id"], "The bank feed is back; go ahead.")
    assert sent["comment"]["recipient_id"] == "bot:ops" and len(bot_jobs(api, "ops")) == 1
    for bot in ("bot:coo", "bot:finance"):
        got = delivery(api, sent["comment"]["id"], bot)
        assert got["reason"] == "follower" and got["mode"] in ("steer", "queue") and got["carrier_id"], got
        assert [j["message_id"] for j in bot_jobs(api, H.actor_id(bot))] == [got["carrier_id"]]
    # A person woke each of them: each acts on the task with party rights for a day.
    delegates = {r["delegate"] for r in rows(api, "SELECT delegate FROM task_delegations WHERE task_id=?", row["id"])}
    assert delegates == {"bot:ops", "bot:coo", "bot:finance"}


def test_defer_never_reaches_no_follower_but_queues_the_mentioned(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    follow(api, row["id"], "ben")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    sent = comment(api, row["id"], "@coo fyi, no rush", delivery="defer", defer_for="never")["comment"]
    assert sent["recipient_id"] == "bot:coo" and len(bot_jobs(api, "coo")) == 1, "the @mention is reached, queued"
    assert bot_jobs(api, "ops") == [], "the owner bot waits for its next turn here"
    owner = delivery(api, sent["id"], "bot:ops")
    assert owner is not None and owner["mode"] == "defer"
    assert delivery(api, sent["id"], "human:ben") is None, "Defer never notifies no follower"
    assert follows(api, row["id"], "bot:coo")[0] == 1


def test_muted_and_mentions_followers_get_only_what_names_them(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    follow(api, row["id"], "ben", notify="muted")
    follow(api, row["id"], "cara", notify="mentions")
    plain = comment(api, row["id"], "Status: waiting on the bank.")["comment"]
    assert delivery(api, plain["id"], "human:ben") is None and delivery(api, plain["id"], "human:cara") is None
    named = comment(api, row["id"], "@ben and @cara, can you confirm the totals?")["comment"]
    assert delivery(api, named["id"], "human:ben")["reason"] == "mention"
    assert delivery(api, named["id"], "human:cara")["reason"] == "mention"


def test_a_live_person_follower_is_notified_of_a_bots_comment(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    follow(api, row["id"], "ben")
    with api.app.state.store.transaction() as c:
        said = H.task_comment(c, "bot:ops", row["id"], "Reconciled; two items left.")
    assert delivery(api, said["id"], "human:ben")["reason"] == "follower"


def test_the_loop_guard_caps_bots_waking_each_other_on_a_task(api, monkeypatch):
    monkeypatch.setattr(threads, "LOOP_GUARD_PER_HOUR", 2)
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    follow(api, row["id"], "bot:coo")
    follow(api, row["id"], "bot:finance")
    with api.app.state.store.transaction() as c:
        for n in range(4):
            H.task_comment(c, "bot:ops", row["id"], f"Step {n} done, over to you.", extra_refs={"needs_action": True})
    capped = rows(api, "SELECT 1 FROM message_deliveries WHERE root_id=(SELECT conversation_id FROM tasks WHERE id=?) "
                       "AND state='capped'", row["id"])
    woke = rows(api, "SELECT 1 FROM message_deliveries WHERE root_id=(SELECT conversation_id FROM tasks WHERE id=?) "
                     "AND cause='bot' AND recipient LIKE 'bot:%' AND mode IN ('steer','queue') AND state<>'capped'",
                row["id"])
    assert capped and len(woke) <= 2


def test_a_bots_at_mention_of_a_bot_wakes_it_and_its_plain_post_to_a_bot_follower_waits(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    follow(api, row["id"], "bot:finance")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
        plain = H.task_comment(c, "bot:ops", row["id"], "Reconciled the bank feed.")
    held = delivery(api, plain["id"], "bot:finance")
    assert held["reason"] == "follower" and held["mode"] == "defer" and not held["carrier_id"], "the bot-to-bot hold"
    assert bot_jobs(api, "finance") == []
    with api.app.state.store.transaction() as c:
        named = H.task_comment(c, "bot:ops", row["id"], "@coo can you sign off on the close?")
    jobs = bot_jobs(api, "coo")
    assert len(jobs) == 1 and jobs[0]["message_id"] in (named["id"], (delivery(api, named["id"], "bot:coo") or {})
                                                        .get("carrier_id")), "an @mention wakes by the way"
    assert follows(api, row["id"], "bot:coo")[:2] == (1, "live")


def test_the_loop_guard_caps_bots_at_mentioning_each_other(api, monkeypatch):
    monkeypatch.setattr(threads, "LOOP_GUARD_PER_HOUR", 2)
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
        for n in range(4):
            H.task_comment(c, "bot:ops", row["id"], f"@coo round {n}, over to you.")
    root = "(SELECT conversation_id FROM tasks WHERE id=?)"
    capped = rows(api, f"SELECT 1 FROM message_deliveries WHERE root_id={root} AND state='capped'", row["id"])
    woke = rows(api, f"SELECT 1 FROM message_deliveries WHERE root_id={root} AND cause='bot' AND recipient='bot:coo' "
                     "AND mode IN ('steer','queue') AND state<>'capped'", row["id"])
    assert capped and len(woke) <= 2


def test_a_bots_mention_on_a_private_task_adds_a_reader_only_within_its_reach(api):
    with api.app.state.store.transaction() as c:
        row = H.task_create(c, "human:ana", "Payroll review", "Confidential: payroll.", "bot:ops", private=True,
                            lint=False)
    r = api.put("/api/v2/reach/settings/bot:ops", json={"mode": "closed"}, headers=headers())
    assert r.status_code == 200, r.text
    with api.app.state.store.transaction() as c:
        said = H.task_comment(c, "bot:ops", row["id"], "@ben can you check the totals?")
        assert "human:ben" not in H.task_readers(H.task(c, row["id"]))
        assert c.execute("SELECT in_audience FROM message_mentions WHERE message_id=? AND actor='human:ben'",
                         (said["id"],)).fetchone()[0] == 0
    assert delivery(api, said["id"], "human:ben") is None
    # A person who reads the task widens it with an @mention, and the mentioned reader follows it.
    sent = comment(api, row["id"], "@ben please check the totals.")["comment"]
    with api.app.state.store.read() as c:
        assert "human:ben" in H.task_readers(H.task(c, row["id"]))
    assert delivery(api, sent["id"], "human:ben")["reason"] == "mention"
    assert follows(api, row["id"], "human:ben")[:2] == (1, "live")


def test_the_reach_preview_answers_who_a_message_would_reach(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    follow(api, row["id"], "ben", notify="muted")
    thread = get(api, "tasks/" + row["id"])["task"]["conversation_id"]

    def preview(**params):
        r = api.get(f"/api/v2/threads/{thread}/reach", params=params, headers=headers())
        assert r.status_code == 200, r.text
        return r.json()
    plain = preview(text="Status update")
    assert plain["way"] == "steer" and [(x["actor"], x["mode"]) for x in plain["reaches"]] == [("bot:ops", "steer")]
    named = preview(text="@coo and @ben, see this", delivery="defer", defer_for="never")
    assert {x["actor"]: (x["reason"], x["mode"]) for x in named["reaches"]} == {
        "bot:coo": ("mention", "queue"), "human:ben": ("mention", "queue"), "bot:ops": ("follower", "defer")}
    r = api.get(f"/api/v2/threads/{thread}/reach", params={"delivery": "steer", "defer_for": "4h"}, headers=headers())
    assert r.status_code == 422, r.text
    assert rows(api, "SELECT 1 FROM messages WHERE conversation_id=? AND body LIKE '%see this%'", thread) == []


def test_a_readers_comment_reaches_the_person_owner_and_waits_for_the_bots(api):
    """A reader who may not direct the task defers to its bots by default (Defer never), but the people following it
    are notified as their level says: the person who owns it hears about it."""
    row = post(api, "tasks", {"owner": "human:cara", "title": "Call the venue", "body": "Ask about Friday."})
    follow(api, row["id"], "bot:coo")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    assert follows(api, row["id"], "human:cara")[:2] == (1, "live")
    sent = comment(api, row["id"], "The venue called back: Friday works.", token="ben-test")
    assert delivery(api, sent["comment"]["id"], "human:cara")["reason"] == "follower"
    assert sent["delivered"] is True
    coo = delivery(api, sent["comment"]["id"], "bot:coo")
    assert coo["mode"] == "defer" and not coo["carrier_id"] and bot_jobs(api, "coo") == []
    # Defer never the reader chose binds people too.
    chosen = comment(api, row["id"], "No need to reply.", token="ben-test", delivery="defer", defer_for="never")
    assert delivery(api, chosen["comment"]["id"], "human:cara") is None


def test_a_readers_mention_reaches_the_bot_but_delegates_nothing(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
        c.execute("DELETE FROM task_delegations")
    sent = comment(api, row["id"], "@coo can you check the May totals?", token="ben-test")["comment"]
    assert sent["recipient_id"] == "bot:coo" and len(bot_jobs(api, "coo")) == 1, "the @mention is reached, queued"
    assert rows(api, "SELECT delegate FROM task_delegations WHERE task_id=?", row["id"]) == [], \
        "only someone who may direct the task hands its bots party rights"
    comment(api, row["id"], "@finance please check them too.")
    assert "bot:finance" in {r["delegate"] for r in rows(api, "SELECT delegate FROM task_delegations WHERE task_id=?",
                                                          row["id"])}


def test_a_defer_on_a_task_filed_into_a_shared_chat_keeps_its_deadline(api):
    from backend.tests.threads_e2e import create_thread
    room = create_thread(api, title="Billing", members=["bot:ops"])["id"]
    with api.app.state.store.transaction() as c:      # filed into the chat, with no task room of its own
        legacy = H.task_create(c, "human:ana", "Plan the billing notes", "For the team.", "bot:ops",
                               conversation_id=room, own_room=False)
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    assert legacy["conversation_id"] == room
    sent = comment(api, legacy["id"], "Whenever you get to it today.", delivery="defer", defer_for="4h")
    assert sent["comment"]["recipient_id"] == "bot:ops" and bot_jobs(api, "ops") == []
    due = rows(api, "SELECT due_at FROM deferrals WHERE item=?", sent["comment"]["id"])
    assert due and due[0]["due_at"], "the owner takes the deferral, with its deadline"
    assert sent["sent_as"] == {"delivery": "defer", "until": due[0]["due_at"]}


def test_a_reply_reaches_its_author_only_while_they_read_the_thread_and_asks_need_them(api):
    from backend.thread_attention import _addressed
    from backend.tests.threads_e2e import call, create_thread, ok, remove_member, say
    t = create_thread(api, title="Venue", members=["human:ben", "human:cara"])
    said = say(api, "cara-test", t["id"], "Friday or Saturday?")
    said = said.get("message", said)
    reply = ok(call(api, "POST", f"threads/{t['id']}/messages", "ana-test",
                    {"text": "Friday.", "in_reply_to": said["id"]}))
    reply = reply.get("message", reply)
    assert delivery(api, reply["id"], "human:cara")["reason"] == "reply"
    with api.app.state.store.read() as c:
        assert reply["id"] in [m["id"] for m in _addressed(c, "human:cara")], "a reply to a person is in Needs you"
    ok(remove_member(api, "ana-test", t["id"], "human:cara"))
    again = ok(call(api, "POST", f"threads/{t['id']}/messages", "ana-test",
                    {"text": "Actually, Saturday.", "in_reply_to": said["id"]}))
    again = again.get("message", again)
    assert delivery(api, again["id"], "human:cara") is None, "a past reader is reached no more"


def test_an_ask_on_a_task_reaches_only_whom_it_asks(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    follow(api, row["id"], "bot:finance")
    follow(api, row["id"], "cara")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
        asked = H.task_comment(c, "bot:ops", row["id"], "Which bank feed should I trust?", ask={"who": "human:ana"})
    assert asked["recipient_id"] == "human:ana"
    assert delivery(api, asked["id"], "bot:finance") is None and bot_jobs(api, "finance") == []
    assert delivery(api, asked["id"], "human:cara") is None


def test_joining_a_tasks_thread_follows_nobody(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    with api.app.state.store.transaction() as c:
        threads._joined(c, row["conversation_id"], "human:cara")
    assert follows(api, row["id"], "human:cara")[0] == 0
