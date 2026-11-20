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
BATCH = "SELECT * FROM conversations WHERE id IN"  # a batch's rows, read whole


def test_catch_up_orders_each_phases_roots_once_however_many_batches_it_reads(api, monkeypatch):
    """120 threads with something new for Ben, a few mentioning him, read 20 roots a batch: the badge's walk reads the
    rest's phase in several batches and orders it, and the mentions' phase, once each. Catch up's pages go on from
    their cursor and list every thread once, the badge's first, in its order."""
    made = []
    for n in range(120):
        thread = new_thread(api, f"Topic {n}")
        say(api, thread["id"], "@ben look" if n % 40 == 0 else "Numbers are in")
        made.append(thread["id"])
    monkeypatch.setattr(thread_rail, "CATCH_UP_BATCH", 20)
    alone, statements = traced(api, lambda: get(api, "threads/catch-up/count", "ben-test"))
    assert alone["count"] == thread_rail.CATCH_UP_SHOWN and set(alone["ids"]) <= set(made)
    assert len([s for s in statements if ORDER in s]) == 2  # one a phase: mentions, then the rest
    assert len([s for s in statements if BATCH in s]) >= 5  # 99 roots, 20 a batch
    shown, cursor = [], None
    while True:
        query = "threads/catch-up?limit=30" + (f"&cursor={cursor}" if cursor else "")
        page, statements = traced(api, lambda q=query: get(api, q, "ben-test"))
        # The page's walk orders each phase it has left once; one from a cursor walks again from the start for the count.
        assert len([s for s in statements if ORDER in s]) <= (2 if cursor is None else 4)
        shown += [i["thread"]["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    # Pages go past the badge's 99, each thread once; the badge's are their first.
    assert sorted(shown) == sorted(made) and shown[: len(alone["ids"])] == alone["ids"]
