"""A thread tied to a task leaves Threads and Catch up when the task is done, closed or declined, and comes back when the
task reopens or someone posts in it again (a close's own notice does not count); it stays readable. An archived thread
leaves both at once, comes back when unarchived, and only a manager archives. Fictional people only: ana (owner), ben
and cara (humans)."""

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.test_api import api, headers  # noqa: F401  (the fixture and its helper)
from backend.tests.test_thread_rail import ids, listed, new_thread, post_as, private_ref, say

FINISHES = ({"status": "done"}, {"status": "declined"}, {"close": True})


def make_task(api, title="Ship pricing", owner="human:ben"):
    response = api.post(
        "/api/v2/tasks", json={"owner": owner, "title": title, "body": "Details."}, headers=headers("ana-test")
    )
    assert response.status_code == 200, response.text
    return response.json().get("task", response.json())


def change_task(api, task, body, token="ana-test"):
    """A change to the task at the version it was last read at; returns the task as the server now holds it."""
    response = api.post(
        f"/api/v2/tasks/{task['id']}", json={"version": task["version"], **body}, headers=headers(token)
    )
    assert response.status_code == 200, response.text
    return response.json().get("task", response.json())


def shows(api, thread_id, token="ben-test"):
    """(in Threads, in Catch up) for the reader, through the public list routes."""
    rail = listed(api, "rail", token)
    catch_up = listed(api, "catch-up", token)
    in_threads = thread_id in ids(rail["pinned"] + rail["threads"])
    in_catch_up = thread_id in [item["thread"]["id"] for item in catch_up["items"]]
    return in_threads, in_catch_up


def view_of(api, thread_id, token="ben-test"):
    response = api.get(f"/api/v2/threads/{thread_id}", headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def task_thread(api, task, title="Pricing"):
    """A members thread tied to the task (ben and cara in it), with one message from ana that ben has not read yet."""
    response = api.post(
        "/api/v2/threads",
        json={
            "title": title,
            "audience": "members",
            "members": ["human:ben", "human:cara"],
            "task_id": task["id"],
        },
        headers=headers("ana-test"),
    )
    assert response.status_code == 200, response.text
    thread = response.json()
    say(api, thread["id"], "Numbers are in the sheet")
    return thread


def test_a_thread_on_an_open_task_shows_in_threads_and_catch_up(api):
    thread = task_thread(api, make_task(api))
    assert shows(api, thread["id"]) == (True, True)


def test_a_thread_on_a_task_that_is_done_closed_or_declined_leaves_threads_and_catch_up(api):
    for number, finish in enumerate(FINISHES):
        task = make_task(api, title=f"Finish {number}")
        thread = task_thread(api, task, title=f"Pricing {number}")
        assert shows(api, thread["id"]) == (True, True)
        change_task(api, task, finish)
        assert shows(api, thread["id"]) == (False, False), finish
        # Still reachable from the thread itself: nothing is deleted or shut.
        readable = api.get(f"/api/v2/threads/{thread['id']}", headers=headers("ben-test"))
        assert readable.status_code == 200, readable.text


def test_a_closed_task_thread_comes_back_when_the_task_reopens(api):
    task = make_task(api)
    thread = task_thread(api, task)
    task = change_task(api, task, {"status": "done"})
    assert shows(api, thread["id"]) == (False, False)
    change_task(api, task, {"status": "open"})
    assert shows(api, thread["id"]) == (True, True)


def test_a_post_after_the_task_closed_brings_its_thread_back_and_a_later_task_edit_does_not_hide_it(api):
    task = make_task(api)
    thread = task_thread(api, task)
    task = change_task(api, task, {"status": "done"})
    assert shows(api, thread["id"]) == (False, False)
    say(api, thread["id"], "One more number", token="cara-test")
    assert shows(api, thread["id"]) == (True, True)
    change_task(api, task, {"title": "Ship pricing (renamed)"})
    assert shows(api, thread["id"]) == (True, True)


def test_a_post_before_the_task_closed_does_not_bring_its_thread_back(api):
    task = make_task(api)
    thread = task_thread(api, task)
    say(api, thread["id"], "Last word before the close", token="cara-test")
    change_task(api, task, {"status": "done"})
    assert shows(api, thread["id"]) == (False, False)


def test_the_close_notice_in_the_tasks_own_thread_does_not_bring_it_back(api):
    task = make_task(api)
    conversation = task["conversation_id"]
    say(api, conversation, "Update from Ana")
    assert shows(api, conversation)[1] is True
    change_task(api, task, {"status": "done"})
    with api.app.state.store.read() as c:
        notices = c.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id=? AND kind='notice'", (conversation,)
        ).fetchone()[0]
    assert notices >= 1  # the close did write its notice into the thread
    assert shows(api, conversation)[1] is False


def test_a_restricted_post_brings_a_closed_task_thread_back_only_for_a_reader_who_may_read_it(api):
    # A post that names a private task is restricted: cara, who owns that task, reads it, and ben does not, so it
    # revives the thread for cara and not for ben (it would leak that the post exists to him otherwise).
    task = make_task(api)
    thread = task_thread(api, task)
    change_task(api, task, {"status": "done"})
    assert shows(api, thread["id"], "cara-test") == (False, False)
    secret = private_ref(api)
    post_as(api, thread["id"], "human:cara", "Private numbers", refs={"task": secret})
    assert shows(api, thread["id"], "cara-test") == (True, True)
    assert shows(api, thread["id"], "ben-test") == (False, False)


def test_a_thread_view_says_when_its_task_hides_it_and_a_post_clears_that(api):
    # The live stream sends this view, so a client that patches a row from it hides the row the rail hides.
    task = make_task(api)
    thread = task_thread(api, task)
    assert not view_of(api, thread["id"]).get("task_hidden")
    change_task(api, task, {"status": "done"})
    assert view_of(api, thread["id"])["task_hidden"] is True
    say(api, thread["id"], "Back to it")
    assert not view_of(api, thread["id"]).get("task_hidden")


def test_archive_hides_a_thread_from_threads_and_catch_up_at_once_and_unarchive_brings_it_back(api):
    thread = new_thread(api, title="Old budget")
    say(api, thread["id"], "Numbers")
    assert shows(api, thread["id"]) == (True, True)
    archived = api.patch(f"/api/v2/threads/{thread['id']}", json={"archived": True}, headers=headers("ana-test"))
    assert archived.status_code == 200, archived.text
    assert shows(api, thread["id"]) == (False, False)
    restored = api.patch(f"/api/v2/threads/{thread['id']}", json={"archived": False}, headers=headers("ana-test"))
    assert restored.status_code == 200, restored.text
    assert shows(api, thread["id"]) == (True, True)


def test_only_a_manager_archives_a_thread(api):
    thread = new_thread(api, title="Budget", members=("human:ben", "human:cara"))
    say(api, thread["id"], "Numbers")
    refused = api.patch(f"/api/v2/threads/{thread['id']}", json={"archived": True}, headers=headers("cara-test"))
    assert refused.status_code == 403, refused.text
    assert shows(api, thread["id"]) == (True, True)


def archived_ids(api, token="ana-test"):
    response = api.get("/api/v2/threads/archived", headers=headers(token))
    assert response.status_code == 200, response.text
    return [t["id"] for t in response.json()["threads"]]


def delete_thread(api, thread_id, token="ana-test"):
    return api.delete(f"/api/v2/threads/{thread_id}", headers=headers(token))


def test_a_thread_with_a_live_message_is_not_deleted_and_says_why(api):
    thread = new_thread(api, title="Live")
    say(api, thread["id"], "Still here")
    refused = delete_thread(api, thread["id"])
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "not_deletable", refused.text
    assert view_of(api, thread["id"], token="ana-test")["deletable"] is False
    assert api.get(f"/api/v2/threads/{thread['id']}", headers=headers("ana-test")).status_code == 200


def test_a_thread_with_another_member_is_not_deleted_even_when_its_messages_are_gone(api):
    thread = new_thread(api, title="Shared", members=("human:ben",))
    message = say(api, thread["id"], "Gone soon")
    assert api.delete(f"/api/v2/messages/{message['id']}", headers=headers("ana-test")).status_code == 200
    refused = delete_thread(api, thread["id"])
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "not_deletable", refused.text
    assert shows(api, thread["id"], token="ben-test")[0] is True


def test_only_a_manager_deletes_a_thread(api):
    thread = new_thread(api, title="Budget", members=("human:ben", "human:cara"))
    refused = delete_thread(api, thread["id"], token="cara-test")
    assert refused.status_code == 403, refused.text


def test_an_empty_thread_alone_is_deleted_and_leaves_every_list(api):
    thread = new_thread(api, title="Release check (delete me)", members=())
    message = say(api, thread["id"], "Dead message")
    assert api.delete(f"/api/v2/messages/{message['id']}", headers=headers("ana-test")).status_code == 200
    assert view_of(api, thread["id"], token="ana-test")["deletable"] is True
    deleted = delete_thread(api, thread["id"])
    assert deleted.status_code == 200, deleted.text
    assert shows(api, thread["id"], token="ana-test") == (False, False)
    assert thread["id"] not in archived_ids(api)
    assert api.get(f"/api/v2/threads/{thread['id']}", headers=headers("ana-test")).status_code == 404


def test_an_archived_thread_is_listed_under_archived_and_unarchive_brings_it_back(api):
    thread = new_thread(api, title="Old budget")
    say(api, thread["id"], "Numbers")
    assert thread["id"] not in archived_ids(api)
    archived = api.patch(f"/api/v2/threads/{thread['id']}", json={"archived": True}, headers=headers("ana-test"))
    assert archived.status_code == 200, archived.text
    assert thread["id"] in archived_ids(api)
    restored = api.patch(f"/api/v2/threads/{thread['id']}", json={"archived": False}, headers=headers("ana-test"))
    assert restored.status_code == 200, restored.text
    assert thread["id"] not in archived_ids(api)
    assert shows(api, thread["id"], token="ana-test")[0] is True


def test_a_home_is_never_deleted(api):
    response = api.get("/api/v2/threads/home/human:ana", headers=headers("ana-test"))
    assert response.status_code == 200, response.text
    refused = delete_thread(api, response.json()["id"])
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "not_deletable", refused.text
