"""Catch up's count alone (GET /api/v2/threads/catch-up/count) is the badge the rail and Catch up give, with the same
roots in the same order, as tasks finish, are posted in again and reopen; and one Catch up orders each phase's roots
once, however many batches it reads. Fictional people only: ana (owner), ben and cara (humans)."""

from backend import thread_rail
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.helpers.api import get, headers  # noqa: F401  (the fixture)
from backend.tests.helpers.task_privacy_reads import traced


def say(api, thread_id, text, token="ana-test"):
    response = api.post(f"/api/v2/threads/{thread_id}/messages", json={"text": text}, headers=headers(token))
    assert response.status_code == 200, response.text


def new_thread(api, title, members=("human:ben", "human:cara"), task=None):
    """A members thread Ana starts (tied to `task` when given)."""
    payload = {"title": title, "audience": "members", "members": list(members)}
    if task:
        payload["task_id"] = task["id"]
    response = api.post("/api/v2/threads", json=payload, headers=headers("ana-test"))
    assert response.status_code == 200, response.text
    return response.json()


def make_task(api, title):
    response = api.post(
        "/api/v2/tasks", json={"owner": "human:ben", "title": title, "body": "Details."}, headers=headers("ana-test")
    )
    assert response.status_code == 200, response.text
    return response.json().get("task", response.json())


def change_task(api, task, status):
    """A finish (done, closed) or a reopen (open) of the task at the version it was last read at."""
    if status == "done":
        path, body = f"/api/v2/tasks/{task['id']}/done", {"version": task["version"]}
    else:
        to = "closed" if status == "closed" else "reopen"
        path, body = f"/api/v2/tasks/{task['id']}/move", {"to": to, "why": "Plans changed.", "version": task["version"]}
    response = api.post(path, json=body, headers=headers("ana-test"))
    assert response.status_code == 200, response.text
    return response.json().get("task", response.json())


def task_thread(api, task, title):
    """A members thread on the task (ben and cara in it) with one message from Ana that Ben has not read."""
    thread = new_thread(api, title, task=task)
    say(api, thread["id"], "Numbers are in the sheet")
    return thread


def _badges(api, token="ben-test"):
    """(the count route's answer, the rail's count and ids, Catch up's count and its first page's roots)."""
    alone = get(api, "threads/catch-up/count", token)
    rail = get(api, "threads/rail", token)
    page = get(api, f"threads/catch-up?limit={thread_rail.CATCH_UP_MAX_LIMIT}", token)
    return alone, (rail["catch_up"], rail["catch_up_ids"]), (page["count"], [i["thread"]["id"] for i in page["items"]])


def _agree(api, token="ben-test"):
    alone, rail, page = _badges(api, token)
    assert (alone["count"], alone["ids"]) == rail, (alone, rail)
    assert alone["count"] == page[0] and alone["ids"][: len(page[1])] == page[1], (alone, page)
    return alone["ids"]


def test_the_count_is_the_rails_and_catch_ups_as_tasks_finish_revive_and_reopen(api):
    plain = make_task(api, title="Still open")
    open_room = task_thread(api, plain, title="Open")
    finished = make_task(api, title="Finished")
    room = task_thread(api, finished, title="Finished room")
    assert {open_room["id"], room["id"]} <= set(_agree(api))
    # Done: its unread message came before the finish, so the room leaves the count for everyone.
    finished = change_task(api, finished, "done")
    assert room["id"] not in _agree(api) and open_room["id"] in _agree(api)
    assert room["id"] not in _agree(api, "cara-test")
    # A person's post after the finish brings it back.
    say(api, room["id"], "One more number", token="cara-test")
    assert room["id"] in _agree(api)
    # Reopened, a finished task's room with only old unread messages is back too.
    other = make_task(api, title="Reopened")
    other_room = task_thread(api, other, title="Reopened room")
    other = change_task(api, other, "closed")
    assert other_room["id"] not in _agree(api)
    change_task(api, other, "open")
    assert other_room["id"] in _agree(api)


ORDER = "SELECT id, activity FROM (SELECT r.id"  # the walk's ordering of a phase's roots


def test_catch_up_orders_each_phases_roots_once_however_many_batches_it_reads(api):
    """250 threads with something new for Ben fill three of the walk's batches (a few mention him): one Catch up load
    orders the mentions' phase and the rest's once each, and its pages list each thread once."""
    made = []
    for n in range(250):
        thread = new_thread(api, f"Topic {n}")
        say(api, thread["id"], "@ben look" if n % 50 == 0 else "Numbers are in")
        made.append(thread["id"])
    assert thread_rail.CATCH_UP_BATCH < len(made) < thread_rail.CATCH_UP_SCAN
    alone, statements = traced(api, lambda: get(api, "threads/catch-up/count", "ben-test"))
    assert alone["count"] == thread_rail.CATCH_UP_SHOWN
    orders = [s for s in statements if ORDER in s]
    assert len(orders) == 2, len(orders)  # one a phase: mentions, then the rest
    page, statements = traced(api, lambda: get(api, "threads/catch-up?limit=50", "ben-test"))
    assert len([s for s in statements if ORDER in s]) == 2
    # Past the first batches: a page from the cursor orders from there, once a phase it still has.
    cursor = page["next_cursor"]
    assert cursor
    later, statements = traced(api, lambda: get(api, f"threads/catch-up?limit=50&cursor={cursor}", "ben-test"))
    assert later["items"] and len([s for s in statements if ORDER in s]) <= 4  # its page's walk and the count's
    shown = [i["thread"]["id"] for i in page["items"] + later["items"]]
    assert len(shown) == len(set(shown)) and set(shown) <= set(made)
