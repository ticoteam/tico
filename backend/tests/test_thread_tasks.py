"""Make task from a thread message: the server reads the message (a stranger gets 404), the task keeps the message's
privacy and carries its words and a link back. Fictional people only: ana (owner), ben and cara (humans)."""

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.store import H
from backend.tests.helpers.api import headers, post  # noqa: F401  (the fixture and its helpers)
from backend.tests.helpers.thread_api import new_thread
from backend.tests.helpers.thread_tasks import make_task


def test_a_task_from_a_members_thread_message_is_private_with_a_link_back_and_a_stranger_gets_404(api):
    thread = new_thread(api, title="Pricing", members=("human:ben",), body="Ship the pricing page Friday")
    message_id = thread["message"]["id"]
    task = post(
        api, f"threads/messages/{message_id}/task", {"title": "Ship pricing", "owner": "assistant"}, token="ben-test"
    )
    assert task["private"] is True
    assert "Ship the pricing page Friday" in task["body"] and f"#/thread/{thread['id']}?m={message_id}" in task["body"]
    post(
        api,
        f"threads/messages/{message_id}/task",
        {"title": "Peek", "owner": "assistant"},
        token="cara-test",
        expected=404,
    )
    post(api, "threads/messages/nope/task", {"title": "Missing", "owner": "assistant"}, token="ben-test", expected=404)


def test_a_repeated_task_is_refused_with_the_task_it_repeats(api):
    thread = new_thread(api, title="Pricing", members=("human:ben",), body="Ship the pricing page Friday")
    message_id = thread["message"]["id"]
    first = post(
        api, f"threads/messages/{message_id}/task", {"title": "Ship pricing", "owner": "assistant"}, token="ben-test"
    )
    again = api.post(
        f"/api/v2/threads/messages/{message_id}/task",
        json={"title": "Ship pricing", "owner": "assistant"},
        headers=headers("ben-test"),
    )
    assert again.status_code == 422, again.text
    assert again.json()["error"]["code"] == "duplicate" and again.json()["error"]["task_id"] == first["id"]


# ----------------------------------------------------------------------------- A task's own thread: unread for its maker
def thread_me(api, thread_id, token="ana-test"):
    response = api.get(f"/api/v2/threads/{thread_id}", headers=headers(token))
    assert response.status_code == 200, response.text
    body = response.json()
    return (body.get("thread") or body)["me"]


def test_a_task_a_person_makes_for_a_bot_is_not_unread_for_them(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Own task", "body": "Details."})
    assert thread_me(api, task["conversation_id"])["unread"] == 0


def test_a_task_a_person_makes_for_themselves_is_not_unread_for_them(api):
    task = make_task(api, {"owner": "human:ana", "title": "My own task", "body": "Details."})
    assert thread_me(api, task["conversation_id"])["unread"] == 0


def test_a_task_made_from_a_thread_message_is_not_unread_for_its_maker(api):
    thread = new_thread(api, title="Pricing", members=("human:ben",), body="Ship the pricing page Friday")
    message_id = thread["message"]["id"]
    task = post(api, f"threads/messages/{message_id}/task", {"title": "Ship pricing", "owner": "ops"}, token="ana-test")
    assert thread_me(api, task["conversation_id"])["unread"] == 0


def test_the_maker_still_sees_what_others_post_to_their_task_later(api):
    task = post(api, "tasks", {"owner": "human:ben", "title": "Ben's task", "body": "Details."})
    assert thread_me(api, task["conversation_id"])["unread"] == 0
    response = api.post(
        f"/api/v2/threads/{task['conversation_id']}/messages", json={"text": "On it"}, headers=headers("ben-test")
    )
    assert response.status_code == 200, response.text
    assert thread_me(api, task["conversation_id"])["unread"] == 1


def test_a_bot_made_task_for_a_person_is_not_news_to_that_person(api):
    """A new task is the owner's turn (task_turn), not news for its requester: no unread, no Inbox row, no digest or push
    reaches Ana. No person is told about a task in a chat (Needs you is where a person learns)."""
    with api.app.state.store.transaction() as c:
        made = H.task_create(
            c, "bot:ops", "Filed for Ana", "Details.", "bot:ops", requester_actor="human:ana", lint=False
        )
    assert thread_me(api, made["conversation_id"])["unread"] == 0
    with api.app.state.store.read() as c:
        delivered = c.execute(
            "SELECT COUNT(*) FROM message_deliveries WHERE thread_id=? AND recipient=?",
            (made["conversation_id"], "human:ana"),
        ).fetchone()[0]
        to_ana = c.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id=? AND recipient_id=?",
            (made["conversation_id"], "human:ana"),
        ).fetchone()[0]
    assert delivered == 0 and to_ana == 0
