"""One definition of an open @mention (backend/open_mentions.py): Needs you and Needs attention (the threads sidebar)
list the same messages, so a thread the sidebar flags always has an item on Needs you (C28, docs/dev/pipelines-v3-cutover.md).
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
    """The Needs you ids of mentions for ana, as GET /api/v2/needs-you lists them."""
    return sorted(i["id"] for i in get(api, "needs-you", token="ana-test")["items"] if i["kind"] == "mention")


def task_thread(api):
    """Ben's task that Ana asked for; its own thread has the two of them in it."""
    with api.app.state.store.transaction() as c:
        made = H.task_create(
            c, "human:ben", "Budget review", "Details.", "human:ben", requester_actor="human:ana", lint=False
        )
    return made["conversation_id"]


def test_needs_you_and_the_sidebar_list_the_same_open_mentions(api):
    open_here = say(api, members_thread(api), "@ana can you look at the lease?")
    task = task_thread(api)
    with api.app.state.store.transaction() as c:
        task_post = threads.post(c, "human:ben", task, "@ana the budget total needs your check.")

    # Each of these is not an open @ of ana, so neither reader lists it.
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
    assert needs_you_mentions(api) == expected
    assert attention_mentions(api) == expected


def test_a_reply_to_the_person_needs_them_on_both_readers_until_they_post(api):
    """A reply to a message of the person's needs them (the Nov 6 reply_needs_you intent), from a person or a bot,
    and it clears when they post in that thread."""
    setup_attempt(api, "finance")
    tid = members_thread(api, members=("human:ana", "human:ben"))
    asked = say(api, tid, "Lease: sign it?", actor="human:ana")
    human = say(api, tid, "Yes, sign it.", actor="human:ben", in_reply_to=asked["id"])
    bot = say(api, tid, "Signed copy filed.", actor="bot:finance", in_reply_to=asked["id"])
    expected = sorted([human["id"], bot["id"]])
    assert needs_you_mentions(api) == attention_mentions(api) == expected
    say(api, tid, "Thanks.", actor="human:ana")
    assert needs_you_mentions(api) == attention_mentions(api) == []


def test_a_bots_reply_that_ats_the_person_still_needs_them(api):
    """A reply keeps its winning reason in the delivery: the bot's @ in its reply to the person is a `mention` from a bot.
    Both readers still list it, because the reply target decides, not the reason."""
    setup_attempt(api, "finance")
    tid = members_thread(api)
    asked = say(api, tid, "Lease: sign it?", actor="human:ana")
    bot = say(api, tid, "@ana please confirm the lease date.", actor="bot:finance", in_reply_to=asked["id"])
    assert needs_you_mentions(api) == attention_mentions(api) == [bot["id"]]


def test_a_sidebar_mention_is_always_one_that_needs_you_lists(api):
    """The sidebar flags only what Needs you lists: a mention that Needs you drops, the sidebar drops too."""
    tid = members_thread(api)
    said = say(api, tid, "@ana check the lease.")
    assert attention_mentions(api) == needs_you_mentions(api) == [said["id"]]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE message_deliveries SET state='moot' WHERE message_id=?", (said["id"],))
    assert attention_mentions(api) == needs_you_mentions(api) == []
