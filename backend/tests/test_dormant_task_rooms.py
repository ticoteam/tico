"""A finished task's room with nothing posted since is dormant (migration 20261119223603_dormant_task_rooms), so the live
reads skip it without reading its posts. Dormant only ever marks a room no reader is shown: the room stays open,
readable and postable, and anything that could bring it back (a post, a reopen, an earlier finish) clears the mark.
Fictional people only: ana (owner), ben and cara (humans)."""

import importlib

from backend.tests.helpers.api import headers
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.test_thread_archive_closed import change_task, make_task, shows, task_thread
from backend.tests.test_thread_rail import post_as, private_ref, say

MIGRATION = importlib.import_module("backend.migrations.20261119223603_dormant_task_rooms")


def dormant(api, thread_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT dormant_at FROM conversations WHERE id=?", (thread_id,)).fetchone()[0] is not None


def test_a_finished_task_room_is_dormant_until_a_post_and_stays_open_to_read_and_post(api):
    task = make_task(api)
    room, thread = task["conversation_id"], task_thread(api, task)
    assert not dormant(api, room) and not dormant(api, thread["id"])
    task = change_task(api, task, {"status": "done"})
    # Both of the task's roots, its own room and the thread tied to it; the finish's own notice does not wake them.
    assert dormant(api, room) and dormant(api, thread["id"])
    assert shows(api, thread["id"]) == (False, False)
    assert api.get(f"/api/v2/threads/{thread['id']}", headers=headers("ben-test")).status_code == 200
    say(api, thread["id"], "One more number", token="cara-test")
    assert not dormant(api, thread["id"]) and dormant(api, room)
    assert shows(api, thread["id"]) == (True, True)


def test_a_reopen_wakes_the_room_and_the_next_finish_marks_it_again(api):
    task = make_task(api)
    thread = task_thread(api, task)
    task = change_task(api, task, {"status": "closed"})
    assert dormant(api, thread["id"])
    task = change_task(api, task, {"status": "open"})
    assert not dormant(api, thread["id"])
    assert shows(api, thread["id"]) == (True, True)
    change_task(api, task, {"status": "done"})
    assert dormant(api, thread["id"])
    assert shows(api, thread["id"]) == (False, False)


def test_a_restricted_post_wakes_the_room_and_the_per_reader_check_still_decides(api):
    task = make_task(api)
    thread = task_thread(api, task)
    change_task(api, task, {"status": "done"})
    post_as(api, thread["id"], "human:cara", "Private numbers", refs={"task": private_ref(api)})
    assert not dormant(api, thread["id"])
    assert shows(api, thread["id"], "cara-test") == (True, True)
    assert shows(api, thread["id"], "ben-test") == (False, False)


def test_a_restored_post_wakes_the_room(api):
    task = make_task(api)
    thread = task_thread(api, task)
    task = change_task(api, task, {"status": "done"})
    late = say(api, thread["id"], "Late word", token="cara-test")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET deleted_at='2026-11-19T00:00:00Z' WHERE id=?", (late["id"],))
        c.execute(MIGRATION.judge("id=?")[:-1], (thread["id"],))
    assert dormant(api, thread["id"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET deleted_at=NULL WHERE id=?", (late["id"],))
    assert not dormant(api, thread["id"])
    assert shows(api, thread["id"], "ben-test") == (True, True)


def test_the_backfill_marks_only_rooms_no_reader_is_shown_and_running_it_again_changes_nothing(api):
    quiet, revived, live = make_task(api, "Quiet"), make_task(api, "Revived"), make_task(api, "Live")
    rooms = {
        name: task_thread(api, task, title=name)["id"] for name, task in (("q", quiet), ("r", revived), ("l", live))
    }
    change_task(api, quiet, {"status": "done"})
    change_task(api, revived, {"status": "closed"})
    say(api, rooms["r"], "After the close", token="cara-test")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET dormant_at=NULL")  # as the hub was before the migration
        MIGRATION.up(c)
        first = dict(c.execute("SELECT id, dormant_at FROM conversations").fetchall())
        MIGRATION.up(c)
        assert dict(c.execute("SELECT id, dormant_at FROM conversations").fetchall()) == first
    assert [dormant(api, rooms[k]) for k in "qrl"] == [True, False, False]
    assert [shows(api, rooms[k]) for k in "qrl"] == [(False, False), (True, True), (True, True)]
