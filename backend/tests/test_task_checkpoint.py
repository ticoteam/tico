"""A paused task picks back up from its checkpoint."""

from backend.store import H
from backend.tests.helpers.api import post, get, runner, assign, ready, claim  # noqa: F401


def test_a_completed_task_turn_leaves_its_checkpoint_on_the_task(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Draft the price page", "body": "First pass."})
    attempt = claim(api, r)
    assert attempt["task"]["id"] == task["id"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t1"}, r["token"])
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Drafted sections 1-2; pricing table next."},
        r["token"],
    )
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
        post(
            api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": text}, r["token"]
        )
        post(api, f"tasks/{task['id']}/comments", {"text": "Next?"})

    turn("Sections 1-2 done.")
    monkeypatch.setattr(execution, "checkpoint_safe", lambda *a: False)
    turn("Read the private budget task too.")
    monkeypatch.undo()
    turn("Budget figures: 40k.")  # the session still holds the private task
    shown = get(api, "tasks/" + task["id"])["task"]
    assert shown["checkpoint"] == "Sections 1-2 done." and shown["checkpoint_blocked"] == 1


def idle(api, title, days=8):
    task = post(api, "tasks", {"owner": "ops", "title": title, "body": "Someday."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), days=-days), task["id"]))
    return task


def asks(api, task_id):
    """The question tasks put under a task (question tasks are its children now, not comments)."""
    with api.app.state.store.read() as c:
        return [
            row[0]
            for row in c.execute(
                "SELECT t.id FROM tasks t JOIN task_relations r ON r.from_task=t.id AND r.kind='parent' "
                "WHERE r.to_task=? AND t.questions IS NOT NULL",
                (task_id,),
            )
        ]
