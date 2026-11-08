"""Task news never reaches a person (hubdb.task_news): a keeper message about a task (refs.task) is written to the task's
conversation, but no person gets a delivery for it, so it is not unread, not in the Inbox, and not in a digest or a push.
A bot owner still gets it as before, and a keeper notice without a task (a budget alert) still reaches the person. An
old delivery already made for such a notice stops counting as unread without a migration.
Fictional people only: ana (owner), ben (a person); the bot ops comes from the api fixture.
"""

from backend import threads
from backend.store import H
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.test_api import api  # noqa: F401  (the fixture)


def rows(api, sql, *args):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def delivery(api, message_id, recipient):
    found = rows(api, "SELECT * FROM message_deliveries WHERE message_id=? AND recipient=?", message_id, recipient)
    return found[0] if found else None


def budget_thread(api):
    """A members thread Ana started with Ben in it."""
    with api.app.state.store.transaction() as c:
        return threads.create(c, "human:ana", title="Budget", audience="members", members=["human:ben"])["id"]


def unread_for(api, actor, thread_id):
    with api.app.state.store.read() as c:
        return threads.unread(c, threads.actor_viewer(c, actor), thread_id)["unread"]


def task_notice(api, thread_id, body="Started task: Budget review", kind="notice"):
    with api.app.state.store.transaction() as c:
        return H.say(c, H.KEEPER, "human:ben", body, conversation_id=thread_id, kind=kind, refs={"task": "tsk-budget"})


def test_a_task_notice_to_a_person_is_in_the_task_but_reaches_no_one(api):
    thread_id = budget_thread(api)
    notice = task_notice(api, thread_id)
    assert rows(api, "SELECT conversation_id FROM messages WHERE id=?", notice["id"]) == [{"conversation_id": thread_id}]
    assert delivery(api, notice["id"], "human:ben") is None
    assert unread_for(api, "human:ben", thread_id) == 0


def test_a_task_question_from_the_keeper_reaches_no_person_either(api):
    thread_id = budget_thread(api)
    question = task_notice(api, thread_id, "Still wanted? Budget review", kind="ask")
    assert delivery(api, question["id"], "human:ben") is None
    assert rows(api, "SELECT recipient_id FROM messages WHERE id=?", question["id"]) == [{"recipient_id": "human:ben"}]


def test_a_task_notice_still_wakes_its_bot_owner_as_before(api):
    with api.app.state.store.transaction() as c:
        direct = threads.dm(c, "human:ana", ["bot:ops"])
        notice = H.say(c, H.KEEPER, "bot:ops", "New task from human:ana: Budget review",
                       conversation_id=direct["id"], kind="notice", refs={"task": "tsk-budget"})
    row = delivery(api, notice["id"], "bot:ops")
    assert row is not None and row["mode"] == "queue" and row["state"] == "pending"


def test_a_keeper_notice_without_a_task_still_reaches_the_person(api):
    thread_id = budget_thread(api)
    with api.app.state.store.transaction() as c:
        alert = H.say(c, H.KEEPER, "human:ben", "The team is at 80% of its budget",
                      conversation_id=thread_id, kind="notice", refs={"budget": "team"})
    assert delivery(api, alert["id"], "human:ben")["reason"] == "addressed"
    assert unread_for(api, "human:ben", thread_id) == 1


def test_an_old_task_delivery_to_a_person_no_longer_counts_as_unread(api):
    thread_id = budget_thread(api)
    with api.app.state.store.transaction() as c:
        # A notice written before the rule: its delivery row exists, as the old trigger made it.
        notice = H.say(c, H.KEEPER, "human:ben", "Finished: Budget review", conversation_id=thread_id, kind="notice",
                       refs={"task": "tsk-budget"})
        c.execute("INSERT INTO message_deliveries(id,message_id,recipient,thread_id,root_id,mode,reason,cause,state,"
                  "revision,created_at,updated_at) SELECT 'old-' || id, id, 'human:ben', conversation_id, "
                  "conversation_id, 'queue', 'addressed', 'keeper', 'pending', 0, created_at, created_at "
                  "FROM messages WHERE id=?", (notice["id"],))
    assert delivery(api, notice["id"], "human:ben")["state"] == "pending"
    assert unread_for(api, "human:ben", thread_id) == 0
    with api.app.state.store.read() as c:
        waiting = c.execute("SELECT COUNT(*) FROM messages m WHERE m.id=? AND " + threads.unread_message_sql("m"),
                            (notice["id"], "human:ben", "human:ben")).fetchone()[0]
    assert waiting == 0


def test_a_person_message_in_the_same_thread_still_counts(api):
    thread_id = budget_thread(api)
    task_notice(api, thread_id)
    with api.app.state.store.transaction() as c:
        threads.post(c, "human:ana", thread_id, "Can you look at the budget?")
    assert unread_for(api, "human:ben", thread_id) == 1
