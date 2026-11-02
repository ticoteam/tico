"""Make task from a thread message: the server reads the message (a stranger gets 404), the task keeps the message's
privacy and carries its words and a link back. Fictional people only: ana (owner), ben and cara (humans)."""

from backend.store import H
from backend.tests.test_api import api, headers, post  # noqa: F401  (the fixture and its helpers)
from backend.tests.test_thread_api import new_thread


def test_a_task_from_a_members_thread_message_is_private_with_a_link_back_and_a_stranger_gets_404(api):
    thread = new_thread(api, title="Pricing", members=("human:ben",), body="Ship the pricing page Friday")
    message_id = thread["message"]["id"]
    task = post(api, f"threads/messages/{message_id}/task", {"title": "Ship pricing", "owner": "coo"}, token="ben-test")
    assert task["private"] is True
    assert "Ship the pricing page Friday" in task["body"] and f"#/thread/{thread['id']}?m={message_id}" in task["body"]
    post(api, f"threads/messages/{message_id}/task", {"title": "Peek", "owner": "coo"}, token="cara-test", expected=404)
    post(api, "threads/messages/nope/task", {"title": "Missing", "owner": "coo"}, token="ben-test", expected=404)


# ----------------------------------------------------------------------------- A task's own thread: unread for its maker
def thread_me(api, thread_id, token="ana-test"):
    response = api.get(f"/api/v2/threads/{thread_id}", headers=headers(token))
    assert response.status_code == 200, response.text
    body = response.json()
    return (body.get("thread") or body)["me"]


def make_task(api, body, token="ana-test"):
    response = api.post("/api/v2/tasks", json=body, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json().get("task", response.json())


def test_a_task_a_person_makes_for_a_bot_is_not_unread_for_them(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Own task", "body": "Details."})
    assert thread_me(api, task["conversation_id"])["unread"] == 0


def test_a_task_a_person_makes_for_themselves_is_not_unread_for_them(api):
    task = make_task(api, {"owner": "human:ana", "title": "My own task", "body": "Details."})
    assert thread_me(api, task["conversation_id"])["unread"] == 0


def test_a_task_a_person_makes_for_another_person_is_not_unread_for_its_maker(api):
    task = make_task(api, {"owner": "human:ben", "title": "Ben's review", "body": "Details."})
    assert thread_me(api, task["conversation_id"])["unread"] == 0


def test_a_task_made_from_a_thread_message_is_not_unread_for_its_maker(api):
    thread = new_thread(api, title="Pricing", members=("human:ben",), body="Ship the pricing page Friday")
    message_id = thread["message"]["id"]
    task = post(api, f"threads/messages/{message_id}/task", {"title": "Ship pricing", "owner": "ops"}, token="ana-test")
    assert thread_me(api, task["conversation_id"])["unread"] == 0


def test_the_maker_still_sees_what_others_post_to_their_task_later(api):
    task = post(api, "tasks", {"owner": "human:ben", "title": "Ben's task", "body": "Details."})
    assert thread_me(api, task["conversation_id"])["unread"] == 0
    response = api.post(f"/api/v2/threads/{task['conversation_id']}/messages", json={"text": "On it"},
                        headers=headers("ben-test"))
    assert response.status_code == 200, response.text
    assert thread_me(api, task["conversation_id"])["unread"] == 1


def test_a_bot_made_task_for_a_person_is_new_to_that_person(api):
    with api.app.state.store.transaction() as c:
        made = H.task_create(c, "bot:ops", "Filed for Ana", "Details.", "bot:ops", requester_actor="human:ana", lint=False)
    assert thread_me(api, made["conversation_id"])["unread"] == 1
