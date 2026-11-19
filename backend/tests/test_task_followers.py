"""Who follows a task (backend/task_followers.py): the owner by default, a filer or commenter only by choice, a new
owner on reassignment, levels, who may change whose follow, and only readers of a private task."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from backend import task_followers as F
from backend.store import H
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.tasks_board import bot_token

TASK = {"title": "Ops weekly sweep", "body": "Check the queue and close what is done."}


@pytest.fixture(autouse=True)
def private_tasks_have_an_unrelated_manager(api):
    from backend.tests.helpers.api import unrelated_task_managers

    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)


def made(api, owner="ops", token="ana-test", **extra):
    return post(api, "tasks", {"owner": owner, **TASK, **extra}, token)


def private(api, requester, owner, title="Payroll review"):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, requester, title, f"Confidential: {title}.", owner, private=True, lint=False)


def followers(api, tid, token="ana-test"):
    return get(api, f"tasks/{tid}/followers", token)


def who(api, tid, token="ana-test"):
    return {r["actor"]: (r["notify"], r["reason"]) for r in followers(api, tid, token)["followers"]}


def follow(api, tid, body=None, token="ana-test"):
    return api.put(f"/api/v2/tasks/{tid}/followers", json={"following": True, **(body or {})}, headers=headers(token))


def unfollow(api, tid, body=None, token="ana-test"):
    return api.put(f"/api/v2/tasks/{tid}/followers", json={"following": False, **(body or {})}, headers=headers(token))


def rows(api, tid):
    with api.app.state.store.read() as c:
        thread = F.thread_id(c, H.task(c, tid))
        return {
            r[0]: (r[1], r[2])
            for r in c.execute("SELECT actor, following, reason FROM thread_followers WHERE thread_id=?", (thread,))
        }


def test_the_owner_follows_and_filing_does_not(api):
    row = made(api)
    assert who(api, row["id"]) == {"bot:ops": ("live", "owner")}
    mine = followers(api, row["id"])
    assert mine["following"] is False and mine["notify"] is None
    assert [(f["actor"], f["notify"], f["reason"]) for f in get(api, "tasks/" + row["id"])["task"]["followers"]] == [
        ("bot:ops", "live", "owner")
    ]
    chosen = made(api, follow=True, title="Ops monthly sweep", body="Check the month and close what is done.")
    assert who(api, chosen["id"]) == {"bot:ops": ("live", "owner"), "human:ana": ("live", "manual")}
    assert followers(api, chosen["id"])["following"] is True
    # A bot that files a task follows it only when it says so.
    with api.app.state.store.transaction() as c:
        quiet = H.task_create(c, "bot:cpo", "Zip intake", "Triage the zip advisories.", "bot:ops", lint=False)
        chose = H.task_create(
            c, "bot:cpo", "Zip intake again", "Triage them again.", "bot:ops", lint=False, follow=True
        )
    assert who(api, quiet["id"]) == {"bot:ops": ("live", "owner")}
    assert who(api, chose["id"]) == {"bot:ops": ("live", "owner"), "bot:cpo": ("live", "manual")}


def test_commenting_does_not_follow(api):
    row = made(api, owner="ben")
    post(api, f"tasks/{row['id']}/comments", {"text": "I looked at the queue; two are done."})
    post(api, f"tasks/{row['id']}/comments", {"text": "Thanks, closing them now."}, "cara-test")
    with api.app.state.store.transaction() as c:
        H.task_comment(c, "bot:cpo", row["id"], "The zip advisories are not in this queue.", wake=False)
    assert "human:cara" not in who(api, row["id"]) and "bot:cpo" not in who(api, row["id"])
    assert who(api, row["id"])["human:ben"] == ("live", "owner")
    assert not rows(api, row["id"]).get("bot:cpo", (0,))[0]
    assert not rows(api, row["id"]).get("human:cara", (0,))[0]


def test_a_new_owner_follows_and_the_old_one_stops_unless_they_chose(api):
    row = made(api)
    r = api.post(
        f"/api/v2/tasks/{row['id']}/owner", json={"version": row["version"], "owner": "ben"}, headers=headers()
    )
    assert r.status_code == 200, r.text
    assert who(api, row["id"]) == {"human:ben": ("live", "owner")}
    assert rows(api, row["id"])["bot:ops"] == (0, "read")
    # Ben chooses to follow; handing it on to Cara keeps him.
    assert follow(api, row["id"], {"notify": "digest"}, "ben-test").status_code == 200
    version = get(api, "tasks/" + row["id"])["task"]["version"]
    r = api.post(f"/api/v2/tasks/{row['id']}/owner", json={"version": version, "owner": "cara"}, headers=headers())
    assert r.status_code == 200, r.text
    assert who(api, row["id"]) == {"human:ben": ("digest", "manual"), "human:cara": ("live", "owner")}


def test_levels_unfollow_and_history(api):
    row = made(api)
    r = follow(api, row["id"], {"notify": "mentions"}, "ben-test")
    assert r.status_code == 200 and r.json()["following"] is True and r.json()["notify"] == "mentions", r.text
    assert follow(api, row["id"], {"notify": "muted"}, "ben-test").json()["notify"] == "muted"
    assert follow(api, row["id"], None, "ben-test").json()["notify"] == "muted"  # no level keeps yours
    r = follow(api, row["id"], {"notify": "loud"}, "ben-test")
    assert r.status_code == 422, r.text
    r = unfollow(api, row["id"], None, "ben-test")
    assert r.status_code == 200 and r.json()["following"] is False and "human:ben" not in who(api, row["id"]), r.text
    assert unfollow(api, row["id"], None, "ben-test").status_code == 200  # already off: nothing changes
    # The owner may stop following too, and come back at live.
    assert unfollow(api, row["id"], {"actor": "ops"}).status_code == 200
    assert who(api, row["id"]) == {}
    with api.app.state.store.read() as c:
        changes = c.execute(
            "SELECT actor FROM events WHERE action='task.followers' AND target=? ORDER BY created_at", (row["id"],)
        ).fetchall()
    assert [r[0] for r in changes] == ["human:ben"] * 3 + ["human:ana"], changes


def test_who_changes_whose_follow(api):
    row = made(api, owner="ben")
    # Cara is no party to the task: she follows herself, but changes nobody else.
    assert follow(api, row["id"], None, "cara-test").status_code == 200
    r = follow(api, row["id"], {"actor": "ana"}, "cara-test")
    assert r.status_code == 403, r.text
    r = unfollow(api, row["id"], {"actor": "ben"}, "cara-test")
    assert r.status_code == 403 and who(api, row["id"])["human:ben"] == ("live", "owner"), r.text
    # The requester adds and sets others; an editor's level change keeps their reason.
    assert follow(api, row["id"], {"actor": "bot:cpo"}).status_code == 200
    assert follow(api, row["id"], {"actor": "ben", "notify": "digest"}).status_code == 200
    assert who(api, row["id"]) == {
        "human:ben": ("digest", "owner"),
        "human:cara": ("live", "manual"),
        "bot:cpo": ("live", "manual"),
    }
    assert unfollow(api, row["id"], {"actor": "cara"}).status_code == 200
    r = follow(api, row["id"], {"actor": "nobody-here"})
    assert r.status_code in (404, 422), r.text


def test_only_readers_follow_a_private_task(api):
    row = private(api, "human:ana", "bot:ops")
    assert who(api, row["id"]) == {"bot:ops": ("live", "owner")}
    assert api.get(f"/api/v2/tasks/{row['id']}/followers", headers=headers("ben-test")).status_code in (403, 404)
    assert follow(api, row["id"], None, "ben-test").status_code in (403, 404)
    r = follow(api, row["id"], {"actor": "ben"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "private", r.text
    r = api.post(f"/api/v2/tasks/{row['id']}/readers", json={"add": ["ben"]}, headers=headers())
    assert r.status_code == 200, r.text
    assert follow(api, row["id"], {"actor": "ben"}).status_code == 200
    assert follow(api, row["id"], None, "ana-test").status_code == 200
    assert set(who(api, row["id"], "ben-test")) == {"bot:ops", "human:ben", "human:ana"}
    # Taken off Who can see: no longer listed (the row stays a read mark), and back on, following again.
    r = api.post(f"/api/v2/tasks/{row['id']}/readers", json={"remove": ["ben"]}, headers=headers())
    assert r.status_code == 200, r.text
    assert set(who(api, row["id"])) == {"bot:ops", "human:ana"}
    assert rows(api, row["id"])["human:ben"][0] == 1


def test_a_bot_adds_anyone_to_a_task_it_owns_and_others_only_in_its_reach(api):
    row = made(api)
    token = bot_token(api)
    assert follow(api, row["id"], {"actor": "cara"}, token).status_code == 200
    r = api.put("/api/v2/reach/settings/bot:ops", json={"mode": "closed"}, headers=headers())
    assert r.status_code == 200, r.text
    # Its own task: sharing it is never out of reach, whatever its reach rule.
    assert follow(api, row["id"], {"actor": "ben"}, token).status_code == 200
    with api.app.state.store.transaction() as c:  # a task ops asked for and does not own
        theirs = H.task_create(c, "bot:ops", "Vendor check", "Check the vendor.", "human:ana", lint=False)
    r = follow(api, theirs["id"], {"actor": "ben"}, token)
    assert r.status_code == 409 and r.json()["error"]["code"] == "reach_closed", r.text
    assert "reach rule, Closed (set for it) on Rules › Internal" in r.json()["error"]["detail"]
    assert unfollow(api, row["id"], {"actor": "ben"}, token).status_code == 200
    # Taking someone off, or its own level, needs no reach.
    assert unfollow(api, row["id"], {"actor": "cara"}, token).status_code == 200
    assert follow(api, row["id"], {"notify": "digest"}, token).status_code == 200
    assert who(api, row["id"]) == {"bot:ops": ("digest", "manual")}  # setting your own level is a choice


def test_a_task_in_a_shared_chat_has_no_followers(api):
    row = made(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET task_id=NULL WHERE id=?", (row["conversation_id"],))
    assert get(api, "tasks/" + row["id"])["task"]["followers"] is None
    assert followers(api, row["id"])["followers"] is None
    r = follow(api, row["id"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "no-thread", r.text


def test_a_chat_task_made_private_gets_a_room_its_owner_follows(api):
    """A task filed into a shared chat has no thread of its own; made private, it moves to its own room
    (hubdb.isolate_private_task), and its owner follows that room."""
    row = made(api, owner="ben")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET kind='chat', task_id=NULL WHERE id=?", (row["conversation_id"],))
    assert followers(api, row["id"])["followers"] is None
    post(api, "tasks/" + row["id"], {"version": row["version"], "private": True})
    moved = get(api, "tasks/" + row["id"])["task"]
    assert moved["conversation_id"] != row["conversation_id"]
    assert who(api, row["id"]) == {"human:ben": ("live", "owner")}
