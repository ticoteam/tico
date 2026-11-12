"""A bot's post in a task's own thread reaches a person who follows the task live, or whom it @mentions or replies to
(hubdb.counts_for_person, delivery_counts_sql). Anyone else gets no delivery (so no unread, Inbox row, digest or push).
Other threads, people's own posts and bot recipients are unchanged. Fictional people only: ana (requester, not
following), ben (the owner, who follows).
"""

from backend import threads, waiting_on
from backend.store import H
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.helpers.task_news_delivery import budget_thread, delivery, rows, unread_for


def task_thread(api):
    """Ben's task that Ana asked for, and its own thread: a task thread has no members list, so the people in it are its
    requester and owner."""
    with api.app.state.store.transaction() as c:
        made = H.task_create(
            c, "human:ben", "Budget review", "Details.", "human:ben", requester_actor="human:ana", lint=False
        )
    return made["conversation_id"]


def bot_say(api, thread_id, body, to="human:ben"):
    """The bot posts in the task thread the way its turn does (threads.post), so a mention in the body is delivered; a
    legacy write (H.say) in a task thread takes no mentions."""
    with api.app.state.store.transaction() as c:
        return threads.post(c, "bot:ops", thread_id, body, to_actor=to)


def test_a_bot_say_in_a_task_thread_without_a_mention_reaches_only_its_live_followers(api):
    thread_id = task_thread(api)
    said = bot_say(api, thread_id, "The budget draft is in the doc; the total still needs a check.", to="human:ana")
    assert rows(api, "SELECT conversation_id FROM messages WHERE id=?", said["id"]) == [{"conversation_id": thread_id}]
    assert delivery(api, said["id"], "human:ana") is None and unread_for(api, "human:ana", thread_id) == 0
    assert delivery(api, said["id"], "human:ben")["reason"] == "follower"
    assert unread_for(api, "human:ben", thread_id) == 1


def test_a_bot_say_that_mentions_the_person_in_a_task_thread_reaches_them(api):
    thread_id = task_thread(api)
    said = bot_say(api, thread_id, "@ben the budget total needs your check before Friday.")
    row = delivery(api, said["id"], "human:ben")
    assert row is not None and row["reason"] == "mention" and row["state"] == "pending"
    assert unread_for(api, "human:ben", thread_id) == 1


def test_a_bot_say_to_a_person_in_a_group_thread_still_reaches_them(api):
    thread_id = budget_thread(api)
    with api.app.state.store.transaction() as c:
        threads.add_member(c, "human:ana", thread_id, "bot:ops")
    said = bot_say(api, thread_id, "The budget draft is in the doc.")
    assert delivery(api, said["id"], "human:ben")["reason"] == "addressed"
    assert unread_for(api, "human:ben", thread_id) == 1


def test_a_person_comment_in_a_task_thread_is_still_unread_for_another_member(api):
    thread_id = task_thread(api)
    with api.app.state.store.transaction() as c:
        threads.post(c, "human:ana", thread_id, "Can you look at the budget?")
    assert unread_for(api, "human:ben", thread_id) == 1


def test_an_old_bot_delivery_in_a_task_thread_no_longer_counts_as_unread(api):
    thread_id = task_thread(api)
    said = bot_say(api, thread_id, "The budget draft is in the doc.", to="human:ana")
    with api.app.state.store.transaction() as c:
        # The row the trigger wrote before the rule: an 'addressed' delivery to Ana (who does not follow) for the post.
        c.execute(
            "INSERT INTO message_deliveries(id,message_id,recipient,thread_id,root_id,mode,reason,cause,state,"
            "revision,created_at,updated_at) SELECT 'old-' || id, id, 'human:ana', conversation_id, "
            "conversation_id, 'queue', 'addressed', 'bot', 'pending', 0, created_at, created_at "
            "FROM messages WHERE id=?",
            (said["id"],),
        )
    assert delivery(api, said["id"], "human:ana")["state"] == "pending"
    assert unread_for(api, "human:ana", thread_id) == 0


def test_a_bots_ask_for_a_person_who_stopped_following_still_needs_them(api):
    with api.app.state.store.transaction() as c:  # Ben's task for ops; he stops following its thread
        parent = H.task_create(c, "human:ben", "Budget review", "Details.", "bot:ops", lint=False)
        c.execute(
            "UPDATE thread_followers SET following=0 WHERE thread_id=? AND actor='human:ben'",
            (parent["conversation_id"],),
        )
        asked = H.task_ask(
            c,
            "bot:ops",
            parent["id"],
            "human:ben",
            "Is the total final? If yes, I'll send it to the board.",
            seen_version=H.task(c, parent["id"])["version"],
        )
        # The ask is on his task, so it reaches him through Needs you, not the thread.
        assert [(n["kind"], n["task_id"], n["requirement_id"]) for n in waiting_on.needs(c, "human:ben")] == [
            ("ask", parent["id"], asked["requirement_id"])
        ]
