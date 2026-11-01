"""Make task from a thread message: the server reads the message (a stranger gets 404), the task keeps the message's
privacy and carries its words and a link back. Fictional people only: ana (owner), ben and cara (humans)."""

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
