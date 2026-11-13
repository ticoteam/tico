"""A thread tied to a task leaves Threads and Catch up when the task is done or closed, and comes back when the
task reopens or someone posts in it again (a close's own notice does not count); it stays readable. An archived thread
leaves both at once, comes back when unarchived, and only a manager archives. Fictional people only: ana (owner), ben
and cara (humans)."""

import json
import uuid

import pytest

from backend import threads
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.helpers.api import headers  # noqa: F401
from backend.tests.test_thread_rail import ids, listed, new_thread, post_as, private_ref, say

FINISHES = ({"status": "done"}, {"status": "closed"})


def make_task(api, title="Ship pricing", owner="human:ben"):
    response = api.post(
        "/api/v2/tasks", json={"owner": owner, "title": title, "body": "Details."}, headers=headers("ana-test")
    )
    assert response.status_code == 200, response.text
    return response.json().get("task", response.json())


def change_task(api, task, body, token="ana-test"):
    """A change to the task at the version it was last read at; returns the task as the server now holds it. A
    `status` is a move: done by /done, closed and open (a reopen) by /move."""
    status = body.get("status")
    if status == "done":
        path, body = f"/api/v2/tasks/{task['id']}/done", {"version": task["version"]}
    elif status in ("closed", "open"):
        to = "closed" if status == "closed" else "reopen"
        path, body = f"/api/v2/tasks/{task['id']}/move", {"to": to, "why": "Plans changed.", "version": task["version"]}
    else:
        path, body = f"/api/v2/tasks/{task['id']}", {"version": task["version"], **body}
    response = api.post(path, json=body, headers=headers(token))
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


def test_a_thread_on_a_task_that_is_done_or_closed_leaves_threads_and_catch_up(api):
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


def test_finishing_a_task_writes_nothing_that_brings_its_own_thread_back(api):
    task = make_task(api)
    conversation = task["conversation_id"]
    say(api, conversation, "Update from Ana")
    assert shows(api, conversation)[1] is True
    change_task(api, task, {"status": "done"})
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
    assert deleted.json() == {"deleted": thread["id"]}
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


def test_the_archived_view_pages_through_every_thread_the_reader_manages(api):
    made = [new_thread(api, title=f"Old {n}") for n in range(4)]
    for thread in made:
        archived = api.patch(f"/api/v2/threads/{thread['id']}", json={"archived": True}, headers=headers("ana-test"))
        assert archived.status_code == 200, archived.text
    seen, cursor = [], None
    for _ in range(4):
        query = "/api/v2/threads/archived?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = api.get(query, headers=headers("ana-test"))
        assert page.status_code == 200, page.text
        seen += [t["id"] for t in page.json()["threads"]]
        cursor = page.json()["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == sorted(t["id"] for t in made)
    assert len(seen) == len(set(seen)) == 4


def test_the_archived_view_fills_its_page_past_threads_the_reader_only_reads(api, monkeypatch):
    # Test identities include an admin, who manages every thread; here no one is one, so Ben is only a member of Ana's
    # twelve archived threads: he reads them but may not manage them. Ben's own archived thread is the oldest, so the
    # newest rows Ben reads first are all ones he cannot manage.
    monkeypatch.setattr(threads.Viewer, "admin", lambda self, c: False)
    mine = new_thread(api, title="Ben's old budget", members=("human:ana",), token="ben-test")
    archived = api.patch(f"/api/v2/threads/{mine['id']}", json={"archived": True}, headers=headers("ben-test"))
    assert archived.status_code == 200, archived.text
    for n in range(12):
        thread = new_thread(api, title=f"Ana's {n}", members=("human:ben",))
        archived = api.patch(f"/api/v2/threads/{thread['id']}", json={"archived": True}, headers=headers("ana-test"))
        assert archived.status_code == 200, archived.text
    page = api.get("/api/v2/threads/archived?limit=2", headers=headers("ben-test"))
    assert page.status_code == 200, page.text
    assert [t["id"] for t in page.json()["threads"]] == [mine["id"]]
    assert page.json()["next_cursor"] is None


def test_a_thread_delete_removes_its_words_attachment_references_and_files(api):
    thread = new_thread(api, title="Budget (to delete)", members=())
    sent = api.post(
        f"/api/v2/uploads/threads/{thread['id']}/messages",
        data={"text": "Budget draft"},
        files={"files": ("budget.txt", b"line items", "text/plain")},
        headers=headers("ana-test"),
    )
    assert sent.status_code == 200, sent.text
    message_id = sent.json()["message"]["id"]
    with api.app.state.store.read() as c:
        blob_id, digest = c.execute(
            "SELECT a.blob_id, b.digest FROM message_assets a JOIN blobs b ON b.id=a.blob_id WHERE a.message_id=?",
            (message_id,),
        ).fetchone()
    edited = api.patch(f"/api/v2/messages/{message_id}", json={"text": "Budget final"}, headers=headers("ana-test"))
    assert edited.status_code == 200, edited.text
    with api.app.state.store.read() as c:
        kept = c.execute(
            "SELECT COUNT(*) FROM message_revisions WHERE message_id=? AND body IS NOT NULL", (message_id,)
        ).fetchone()[0]
    assert kept >= 1  # the earlier words are stored before the delete, so the check below means something
    assert api.delete(f"/api/v2/messages/{message_id}", headers=headers("ana-test")).status_code == 200
    deleted = delete_thread(api, thread["id"])
    assert deleted.status_code == 200, deleted.text
    with api.app.state.store.read() as c:
        refs = json.loads(c.execute("SELECT refs FROM messages WHERE id=?", (message_id,)).fetchone()[0] or "{}")
        assert "attachments" not in refs
        assert c.execute("SELECT COUNT(*) FROM message_assets WHERE message_id=?", (message_id,)).fetchone()[0] == 0
        words = c.execute(
            "SELECT COUNT(*) FROM message_revisions WHERE message_id=? AND body IS NOT NULL", (message_id,)
        ).fetchone()[0]
        assert words == 0
        tombstone = c.execute("SELECT body, deleted_at FROM messages WHERE id=?", (message_id,)).fetchone()
        assert tombstone[0] == "" and tombstone[1] is not None
        # The file itself: its blob row is gone, no file version still points at it, and its bytes are queued for removal.
        assert c.execute("SELECT COUNT(*) FROM blobs WHERE id=?", (blob_id,)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM bot_file_versions WHERE blob_id=?", (blob_id,)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM blob_purges WHERE digest=?", (digest,)).fetchone()[0] == 1


@pytest.mark.parametrize("copy_is_live", [True, False], ids=["copy with words", "empty copy"])
def test_a_thread_delete_blanks_the_older_words_of_its_origins_copies_in_other_threads(api, copy_is_live):
    # The contract: a delete clears every copy of the words, and its origin's copies. A copy in another thread keeps
    # its own current text (live), or is already empty (deleted); either way its older revision words go with the
    # origin's delete. The schema gives inserted rows their own origin, so the shared origin here is set directly.
    thread = new_thread(api, title="Plan (to delete)", members=())
    original = say(api, thread["id"], "Plan v1")
    edited = api.patch(f"/api/v2/messages/{original['id']}", json={"text": "Plan v2"}, headers=headers("ana-test"))
    assert edited.status_code == 200, edited.text
    other = new_thread(api, title="Elsewhere", members=("human:ben",))
    copy = say(api, other["id"], "Live copy")
    with api.app.state.store.read() as c:
        origin = c.execute("SELECT origin_id FROM messages WHERE id=?", (original["id"],)).fetchone()[0]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET origin_id=? WHERE id=?", (origin, copy["id"]))
        if not copy_is_live:
            c.execute("UPDATE messages SET body='', deleted_at=? WHERE id=?", ("2026-11-08T00:00:00Z", copy["id"]))
        c.execute(
            "INSERT INTO message_revisions(id,message_id,origin_id,revision,body,deleted,actor,created_at) "
            "VALUES(?,?,?,?,?,0,'human:ana','2026-11-08T00:00:00Z')",
            (str(uuid.uuid4()), copy["id"], origin, 7, "Copy words before"),
        )
    with api.app.state.store.read() as c:
        before = c.execute(
            "SELECT COUNT(*) FROM message_revisions WHERE message_id=? AND origin_id=? AND body IS NOT NULL",
            (copy["id"], origin),
        ).fetchone()[0]
    assert before == 1  # the shared older words are stored before the delete, so the check below means something
    assert api.delete(f"/api/v2/messages/{original['id']}", headers=headers("ana-test")).status_code == 200
    assert delete_thread(api, thread["id"]).status_code == 200
    with api.app.state.store.read() as c:
        # The rows that share the deleted message's origin lose their words; the copy's own first revision (its own
        # origin) is its history and stays. Its current text stands.
        shared = c.execute(
            "SELECT COUNT(*) FROM message_revisions WHERE message_id=? AND origin_id=? AND body IS NOT NULL",
            (copy["id"], origin),
        ).fetchone()[0]
        text = c.execute("SELECT body FROM messages WHERE id=?", (copy["id"],)).fetchone()[0]
    assert shared == 0
    assert text == ("Live copy" if copy_is_live else "")


def test_a_home_is_never_deleted(api):
    response = api.get("/api/v2/threads/home/human:ana", headers=headers("ana-test"))
    assert response.status_code == 200, response.text
    refused = delete_thread(api, response.json()["id"])
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "not_deletable", refused.text
