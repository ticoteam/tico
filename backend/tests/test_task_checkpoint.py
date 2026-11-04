"""A paused task picks back up from its checkpoint. An idle task is a line on its bot's roundup, not a question to a
person; a "Still wanted?" question put before that change is still answered by its person (backend/still_wanted.py)."""
from backend import still_wanted, task_roundup
from backend.store import H
from backend.tests.test_api import api, post, get, runner, assign, ready, claim  # noqa: F401


def test_a_completed_task_turn_leaves_its_checkpoint_on_the_task(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Draft the price page", "body": "First pass."})
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
    task = post(api, "tasks", {"owner": "ops", "title": "Draft the price page", "body": "First pass."})

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
    task = post(api, "tasks", {"owner": "ops", "title": title, "body": "Someday."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), days=-days), task["id"]))
    return task


def asks(api, task_id):
    with api.app.state.store.read() as c:
        return [m for m in H.task_comments(c, task_id) if m["kind"] == "ask"]


def legacy_ask(api, task):
    """A "Still wanted?" question as the nudge put it before it was removed: its answer still acts."""
    ask = {"questions": [{"id": still_wanted.QUESTION_ID, "header": "Still wanted?",
                          "question": f"Still wanted? {task['title']}",
                          "options": [{"label": still_wanted.KEEP, "description": "Keep it open"},
                                      {"label": still_wanted.CLOSE, "description": "Close it"}],
                          "multi": False, "other": False}], "who": "human:ana"}
    with api.app.state.store.transaction() as c:
        H.task_comment(c, H.KEEPER, task["id"], "Nothing has happened on this task for 7 days. Still wanted?",
                       wake=False, ask=ask, extra_refs={"still_wanted": True})
    return asks(api, task["id"])[-1]


def test_an_idle_task_is_not_asked_about_and_its_bot_lists_it_on_the_roundup(api):
    stale = idle(api, "Old idea")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops'")     # the creation wake ran: nothing is queued
    with api.app.state.store.read() as c:
        text, _, _ = task_roundup._list(c, "ops", H.now(), {})
    assert "Old idea" in text
    assert asks(api, stale["id"]) == [], "no question goes to a person about an idle task"


def test_keep_and_close_on_a_question_put_before_the_nudge_was_removed_still_act(api):
    kept, closed = idle(api, "Keep me"), idle(api, "Drop me")
    for task, label in ((kept, "Keep"), (closed, "Close")):
        ask = legacy_ask(api, task)
        post(api, f"tasks/{task['id']}/answers",
             {"target": {"comment": ask["id"]}, "answers": {"still_wanted": [label]}})
    with api.app.state.store.read() as c:
        kept_row, closed_row = H.task(c, kept["id"]), H.task(c, closed["id"])
        assert kept_row["status"] == "open"
        assert closed_row["status"] == "closed" and closed_row["closed_by_id"] == "human:ana"


def test_only_the_person_asked_decides_still_wanted():
    from types import SimpleNamespace
    ask = {"sender_id": H.KEEPER, "recipient_id": "human:ana", "refs": {"still_wanted": True}}
    close = SimpleNamespace(dismiss=False, answers={"still_wanted": ["Close"]})
    assert still_wanted.choice(ask, close, "human:ana") == "Close"
    # The bot on the task, or another person who may comment, answers without closing it.
    assert still_wanted.choice(ask, close, "bot:ops") is None
    assert still_wanted.choice(ask, close, "human:ben") is None
