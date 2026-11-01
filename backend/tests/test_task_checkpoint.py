"""A paused task picks back up from its checkpoint, and an idle one gets one "Still wanted?" ask
that only the person's answer acts on (backend/still_wanted.py)."""
from backend import still_wanted
from backend.store import H
from backend.tests.test_api import api, post, get, runner, assign, ready, claim  # noqa: F401


def test_a_completed_task_turn_leaves_its_checkpoint_on_the_task(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    task = post(api, "tasks", {"owner_id": "ops", "title": "Draft the price page", "body": "First pass."})
    attempt = claim(api, r)
    assert attempt["task"]["id"] == task["id"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t1"}, r["token"])
    post(api, f"attempts/{attempt['id']}/complete",
         {"outcome": "completed", "last_seq": 0, "text": "Drafted sections 1-2; pricing table next."}, r["token"])
    shown = get(api, "tasks/" + task["id"])["task"]
    assert shown["checkpoint"] == "Drafted sections 1-2; pricing table next."
    assert shown["checkpoint_by_id"] == "bot:ops" and shown["checkpoint_at"]


def test_once_a_task_session_takes_in_another_private_task_it_never_checkpoints_again(api, monkeypatch):
    from backend import execution
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    task = post(api, "tasks", {"owner_id": "ops", "title": "Draft the price page", "body": "First pass."})

    def turn(text):
        attempt = claim(api, r)
        post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t1"}, r["token"])
        post(api, f"attempts/{attempt['id']}/complete",
             {"outcome": "completed", "last_seq": 0, "text": text}, r["token"])
        post(api, f"tasks/{task['id']}/comments", {"text": "Next?"})

    turn("Sections 1-2 done.")
    monkeypatch.setattr(execution, "checkpoint_safe", lambda *a: False)
    turn("Read the private budget task too.")
    monkeypatch.undo()
    turn("Budget figures: 40k.")       # the session still holds the private task
    shown = get(api, "tasks/" + task["id"])["task"]
    assert shown["checkpoint"] == "Sections 1-2 done." and shown["checkpoint_blocked"] == 1


def idle(api, title, days=8):
    task = post(api, "tasks", {"owner_id": "ops", "title": title, "body": "Someday."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), days=-days), task["id"]))
    return task


def ask_idle(api):
    with api.app.state.store.transaction() as c:
        return still_wanted.ask_idle(c)


def asks_on(api, task_id):
    with api.app.state.store.read() as c:
        return [m for m in H.task_comments(c, task_id) if m["kind"] == "ask" and m["refs"].get("still_wanted")]


def test_an_idle_task_gets_one_still_wanted_ask_and_an_active_one_none(api):
    stale = idle(api, "Old idea")
    fresh = post(api, "tasks", {"owner_id": "ops", "title": "Current work", "body": "Now."})
    assert ask_idle(api) == [stale["id"]]
    [ask] = asks_on(api, stale["id"])
    assert ask["recipient_id"] == "human:ana" and ask["sender_id"] == H.KEEPER
    assert asks_on(api, fresh["id"]) == []
    # Another week passes with nobody answering: still the one ask for this stretch.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET updated_at=?,stale_asked_at=? WHERE id=?",
                  (H.shift(H.now(), days=-8), H.shift(H.now(), days=-8), stale["id"]))
    assert ask_idle(api) == []
    assert len(asks_on(api, stale["id"])) == 1


def test_keep_restarts_the_clock_and_close_closes(api):
    kept, closed = idle(api, "Keep me"), idle(api, "Drop me")
    ask_idle(api)
    for task, label in ((kept, "Keep"), (closed, "Close")):
        [ask] = asks_on(api, task["id"])
        post(api, f"tasks/{task['id']}/answers",
             {"target": {"comment": ask["id"]}, "answers": {"still_wanted": [label]}})
    with api.app.state.store.read() as c:
        kept_row, closed_row = H.task(c, kept["id"]), H.task(c, closed["id"])
        assert kept_row["status"] == "open" and kept_row["updated_at"] > kept_row["stale_asked_at"]
        assert closed_row["status"] == "closed" and closed_row["closed_by_id"] == "human:ana"
        assert still_wanted.idle_tasks(c) == []


def test_only_the_person_asked_decides_still_wanted():
    from types import SimpleNamespace
    ask = {"sender_id": H.KEEPER, "recipient_id": "human:ana", "refs": {"still_wanted": True}}
    close = SimpleNamespace(dismiss=False, answers={"still_wanted": ["Close"]})
    assert still_wanted.choice(ask, close, "human:ana") == "Close"
    # The bot on the task, or another person who may comment, answers without closing it.
    assert still_wanted.choice(ask, close, "bot:ops") is None
    assert still_wanted.choice(ask, close, "human:ben") is None
