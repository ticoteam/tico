"""One definition of an open @mention (backend/open_mentions.py): Needs attention (the threads sidebar) lists it, and
Needs you never does: an @ alone is not a wait (C28, #4762).
Fictional people only: ana (the person read), ben (owner of the task), cara (the thread's maker), finance (a bot)."""

from backend import hubdb as H
from backend import threads
from backend import thread_attention as TA
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.helpers.api import get, setup_attempt  # noqa: F401
from backend.tests.helpers.thread_attention import members_thread, say


def attention_mentions(api):
    """The Needs attention ids of mentions for ana, as the threads sidebar reads them."""
    auth = api.app.state.auth
    who = auth.authenticate({"authorization": "Bearer ana-test"}, "/api/v2/threads", "GET")
    with api.app.state.store.read_transaction() as c:
        return sorted(i["id"] for i in TA.items(c, auth, who) if i["kind"] == "thread_mention")


def needs_you_mentions(api):
    """Any Needs you item for ana that stands for a message, as GET /api/v2/needs-you lists them (a ❓ aside)."""
    items = get(api, "needs-you", token="ana-test")["items"]
    return sorted(i["id"] for i in items if i.get("message_id") and i["kind"] != "question")


def task_thread(api):
    """Ben's task that Ana asked for; its own thread has the two of them in it."""
    with api.app.state.store.transaction() as c:
        made = H.task_create(
            c, "human:ben", "Budget review", "Details.", "human:ben", requester_actor="human:ana", lint=False
        )
    return made["conversation_id"]


def test_the_sidebar_lists_open_mentions_and_needs_you_lists_none(api):
    open_here = say(api, members_thread(api), "@ana can you look at the lease?")
    task = task_thread(api)
    with api.app.state.store.transaction() as c:
        task_post = threads.post(c, "human:ben", task, "@ana the budget total needs your check.")

    # Each of these is not an open @ of ana, so the sidebar does not list it.
    answered_thread = members_thread(api, title="Answered")
    say(api, answered_thread, "@ana the rent?")
    say(api, answered_thread, "Yes, on it.", actor="human:ana")
    edited = say(api, members_thread(api, title="Edited"), "@ana wait for the final figure.")
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE message_deliveries SET state='moot' WHERE message_id=? AND recipient='human:ana'", (edited["id"],)
        )
    closed = say(api, members_thread(api, title="Closed"), "@ana the old one.")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET closed_at=? WHERE id=?", (H.now(), closed["conversation_id"]))
    say(api, members_thread(api, title="Addressed"), "Can you look at this?", to_actor="human:ana")
    setup_attempt(api, "finance")
    say(api, members_thread(api, title="Bot"), "@ana the numbers are ready.", actor="bot:finance")

    expected = sorted([open_here["id"], task_post["id"]])
    assert attention_mentions(api) == expected
    assert needs_you_mentions(api) == []
    assert get(api, "needs-you?count=true", token="ana-test")["count"] == 0


def test_a_reply_to_the_person_is_on_the_sidebar_until_they_post(api):
    """A person's reply to a message of the person's is attention (the Nov 6 reply_needs_you intent), never Needs you, and
    it clears when they post in that thread."""
    tid = members_thread(api, members=("human:ana", "human:ben"))
    asked = say(api, tid, "Lease: sign it?", actor="human:ana")
    human = say(api, tid, "Yes, sign it.", actor="human:ben", in_reply_to=asked["id"])
    assert attention_mentions(api) == [human["id"]] and needs_you_mentions(api) == []
    say(api, tid, "Thanks.", actor="human:ana")
    assert needs_you_mentions(api) == attention_mentions(api) == []


def test_a_bots_reply_to_the_person_never_needs_them_even_with_an_at(api):
    """A bot's message needs a person only by its ❓ (backend/question_marks.py): its reply, with an @ or without, marks
    the thread unread and stays out of the sidebar's attention, on a task that has moved on as anywhere else."""
    setup_attempt(api, "finance")
    tid = members_thread(api)
    asked = say(api, tid, "Lease: sign it?", actor="human:ana")
    say(api, tid, "Signed copy filed.", actor="bot:finance", in_reply_to=asked["id"])
    say(api, tid, "@ana please confirm the lease date.", actor="bot:finance", in_reply_to=asked["id"])
    assert needs_you_mentions(api) == attention_mentions(api) == []


def test_a_sidebar_mention_clears_when_its_delivery_is_moot(api):
    """A mention whose delivery no longer waits on the person (an edit made it moot) leaves the sidebar."""
    tid = members_thread(api)
    said = say(api, tid, "@ana check the lease.")
    assert attention_mentions(api) == [said["id"]]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE message_deliveries SET state='moot' WHERE message_id=?", (said["id"],))
    assert attention_mentions(api) == needs_you_mentions(api) == []
