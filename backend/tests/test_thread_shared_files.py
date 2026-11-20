"""A bot attaches a file it made to its message in a thread: in its Home with the person it talks to, and in its task's
thread. A file already in Tico goes by `<file id>@<version>` (thread_api.shared_files); a file from the bot's checkout
goes through the thread upload route, as a person's does. Either way the thread's readers open it from the message.

A run carrying a private task posts with files wherever a run on a public task may: its own task's thread and the
bot's Home alike. Fictional people only: ben chats with the bot ops.
"""

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.helpers.api import headers
from backend.tests.helpers.files import publish, turn
from backend.tests.helpers.threads_private_execution import (  # noqa: F401  (the autouse fixture)
    private_run,
    private_tasks_have_an_unrelated_manager,
    task,
)


def home(api, token="ben-test"):
    made = api.get("/api/v2/threads/home/bot:ops", headers=headers(token))
    assert made.status_code == 200, made.text
    return made.json()["id"]


def library_file(api, attempt, task_id):
    """A file the bot published on a task: in its Library, not in the thread it posts to."""
    made = publish(api, attempt, name="reply-letter.md", text="# Reply\n\nDraft for review.", task=task_id)
    assert made.status_code == 200, made.text
    return made.json()["file"]["id"]


def post(api, attempt, thread_id, attachments):
    return api.post(
        f"/api/v2/threads/{thread_id}/messages",
        json={"text": "The draft is attached.", "attachments": attachments},
        headers=headers(attempt["token"]),
    )


def opened(api, message, token):
    (item,) = message["refs"]["attachments"]
    return api.get(item["url"], headers=headers(token))


def test_a_bot_attaches_its_library_file_in_its_home_and_its_task_thread(api):
    _, attempt = turn(api, "ops", who="ben-test")
    work = task(api, requester="human:ben", owner="bot:ops", private=False, title="Reply letter")
    with api.app.state.store.transaction() as c:  # the run carries the company-visible task
        c.execute("UPDATE attempts SET task_id=? WHERE id=?", (work["id"], attempt["id"]))
    fid = library_file(api, attempt, work["id"])
    for thread_id in (home(api), work["conversation_id"]):
        sent = post(api, attempt, thread_id, [f"{fid}@1"])
        assert sent.status_code == 200, sent.text
        message = sent.json()
        assert message["refs"]["attachments"][0]["title"] == "reply-letter.md"
        got = opened(api, message, "ben-test")
        assert got.status_code == 200, got.text
        assert got.content == b"# Reply\n\nDraft for review."


def test_a_file_the_bot_cannot_open_is_refused_and_the_refusal_says_what_to_do(api):
    """Another bot's file on a private task ops is no party to: sharing it would hand its bytes to ops' readers."""
    row, other = private_run(api, bot="cpo")
    fid = library_file(api, other, row["id"])
    _, attempt = turn(api, "ops", who="ben-test")
    thread_id = home(api)
    for ref in (f"{fid}@1", "nosuchfile@1"):
        sent = post(api, attempt, thread_id, [ref])
        assert sent.status_code == 404, sent.text
        assert "tico thread upload" in sent.json()["error"]["detail"]
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM messages WHERE body='The draft is attached.'").fetchone()


def test_a_bot_streams_a_file_from_its_checkout_into_its_home(api):
    _, attempt = turn(api, "ops", who="ben-test")
    sent = api.post(
        f"/api/v2/uploads/threads/{home(api)}/messages",
        data={"text": "The draft is attached."},
        files={"files": ("letter.txt", b"Dear Acme,", "text/plain")},
        headers=headers(attempt["token"]),
    )
    assert sent.status_code == 200, sent.text
    message = sent.json()["message"]
    got = opened(api, message, "ben-test")
    assert (got.status_code, got.content) == (200, b"Dear Acme,")


def test_a_private_run_attaches_its_file_in_its_own_task_thread_and_elsewhere(api):
    row, attempt = private_run(api)
    fid = library_file(api, attempt, row["id"])
    for thread_id in (row["conversation_id"], home(api)):
        sent = post(api, attempt, thread_id, [f"{fid}@1"])
        assert sent.status_code == 200, sent.text
        assert opened(api, sent.json(), "ben-test").status_code == 200
        upload = api.post(
            f"/api/v2/uploads/threads/{thread_id}/messages",
            data={"text": "The letter"},
            files={"files": ("letter.txt", b"Dear Acme,", "text/plain")},
            headers=headers(attempt["token"]),
        )
        assert upload.status_code == 200, upload.text
