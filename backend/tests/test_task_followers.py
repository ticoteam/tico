"""Nobody follows a task (backend/task_followers.py): its comments are a log. Older clients keep working until the
upgrade floor passes: `follow` on create is accepted and ignored, the followers route lists nobody, and follow or
unfollow answers and changes nothing. Fictional people only: ana (owner) and ben."""

import pytest

from backend.store import H
from backend.tests.helpers.api import get, headers, post  # noqa: F401

TASK = {"title": "Ops weekly sweep", "body": "Check the queue and close what is done."}


@pytest.fixture(autouse=True)
def private_tasks_have_an_unrelated_manager(api):
    from backend.tests.helpers.api import unrelated_task_managers

    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)


def following_rows(api, tid):
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT actor FROM thread_followers WHERE thread_id=? AND following=1", (H.task(c, tid)["conversation_id"],)
        ).fetchall()


def test_nobody_follows_and_the_old_follower_api_changes_nothing(api):
    row = post(api, "tasks", {"owner": "ops", "follow": True, **TASK})
    assert following_rows(api, row["id"]) == [], "neither the holder nor a filer who asked to follow"
    assert get(api, f"tasks/{row['id']}/followers") == {
        "task_id": row["id"],
        "following": False,
        "notify": None,
        "followers": [],
    }
    for body in ({"following": True}, {"following": True, "actor": "ben", "notify": "live"}, {"following": False}):
        r = api.put(f"/api/v2/tasks/{row['id']}/followers", json=body, headers=headers())
        assert r.status_code == 200, r.text
        assert r.json()["followers"] == []
    assert following_rows(api, row["id"]) == []
    # A new holder follows nothing either.
    with api.app.state.store.transaction() as c:
        H.task_reassign(c, "human:ana", row["id"], "human:ben", "Ben takes it", mover=True)
    assert following_rows(api, row["id"]) == []
    assert get(api, "tasks/" + row["id"])["task"]["followers"] == []
