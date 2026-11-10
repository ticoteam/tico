"""The org rule check on the v3 task routes (backend/task_rule_gate.py, backend/task_moves_routes.py): every task write
that says something is checked through the real route, held writes change nothing, Send anyway replays them for the
reviewer only, and a check that cannot run writes nothing."""

import json

from backend.store import H
from backend.tests.helpers.api import get, headers, post
from backend.tests.test_org_rule_check import (  # noqa: F401 - fixtures
    _not_a_friday,
    color_rule,
    hold_row,
    ruled,
    sql,
    task_for_ops,
)


def row(api, task_id):
    return sql(api, "SELECT status, owner_id, note, version FROM tasks WHERE id=?", (task_id,))[0]


def ops(api, path, body, expected=200):
    r = api.post("/api/v2/" + path, json=body, headers=headers("ops-test"))
    assert r.status_code == expected, r.text
    return r.json()


def own_done(api, task):
    """The owner's own "done" requirement on the task's stage: what a bot answers on its own work."""
    view = get(api, f"tasks/{task['id']}")
    view = view.get("task", view)
    return next(r["requirement_id"] for r in view["to_leave"] if r["kind"] == "owner_done")


def test_a_held_move_why_snooze_why_and_answer_note_leave_the_task_unchanged(ruled):
    api, _ = ruled
    color_rule(api)
    task = task_for_ops(api)
    before = row(api, task["id"])
    for path, change in (
        (f"tasks/{task['id']}/move", {"to": "done", "why": "Our favorite color is red, so done."}),
        (f"tasks/{task['id']}/snooze", {"why": "Waiting: our favorite color is red."}),
        (
            f"tasks/{task['id']}/asks/{own_done(api, task)}/answer",
            {"value": "done", "note": "Shipped; favorite color is red."},
        ),
    ):
        got = ops(api, path, {"version": task["version"], **change})
        assert got["held"], got
    assert row(api, task["id"]) == before
    ops_ = {h["operation"] for h in sql(api, "SELECT operation FROM org_rule_holds")}
    assert ops_ == {"task_move", "task_snooze", "task_ask_answer"}


def test_a_warned_done_note_and_snooze_why_carry_the_line_and_go_through(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    got = ops(api, f"tasks/{task['id']}/done", {"version": task["version"], "note": "Done: favorite color is red."})
    assert got["rule_warning"] and row(api, task["id"])["status"] == "done"
    said = json.dumps(sql(api, "SELECT notes FROM answers ORDER BY rowid DESC LIMIT 1"))
    assert "Warning:" in said and "favorite color is red" in said
    task = task_for_ops(api, "Write the second note")
    got = ops(api, f"tasks/{task['id']}/snooze", {"version": task["version"], "why": "Our favorite color is red."})
    assert got["rule_warning"] and got["task"]["snooze"]
    [snooze] = sql(api, "SELECT why FROM snoozes WHERE task_id=?", (task["id"],))
    assert snooze["why"].startswith("Warning:") and snooze["why"].endswith("Our favorite color is red.")


def test_send_anyway_of_a_held_done_note_finishes_the_task_for_the_reviewer_only(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    task = task_for_ops(api)
    held = ops(api, f"tasks/{task['id']}/done", {"version": task["version"], "note": "Favorite color is red."})["held"]
    assert row(api, task["id"])["status"] != "done"
    # Cara is a plain member, not the reviewer: the hold is not hers to read or send.
    api_r = api.post(f"/api/v2/rule-holds/{held['id']}/send", json={"nonce": "x"}, headers=headers("cara-test"))
    assert api_r.status_code == 404
    view = get(api, f"rule-holds/{held['id']}", token="ben-test")
    assert view["can_send"] and "favorite color is red" in view["text"].lower()
    sent = post(api, f"rule-holds/{held['id']}/send", {"nonce": view["nonce"]}, token="ben-test")
    assert sent["hold"]["state"] == "sent"
    assert row(api, task["id"])["status"] == "done"


def test_withdrawing_an_ask_with_a_forbidden_why_is_held(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    asked = ops(api, f"tasks/{task['id']}/asks", {"to": "ana", "line": "Which launch day suits you?", "version": 1})
    color_rule(api)
    r = api.request(
        "DELETE",
        f"/api/v2/tasks/{task['id']}/asks/{asked['requirement_id']}",
        json={"why": "Never mind: our favorite color is red."},
        headers=headers("ops-test"),
    )
    assert r.status_code == 200 and r.json()["held"], r.text
    assert sql(api, "SELECT 1 FROM requirements WHERE id=?", (asked["requirement_id"],))


def test_a_bots_ok_ask_a_rule_covers_is_refused(ruled):
    api, _ = ruled
    color_rule(api)
    task = task_for_ops(api)
    r = api.post(
        f"/api/v2/tasks/{task['id']}/oks",
        json={
            "to": "ana",
            "line": "OK to post this note?",
            "subject": "hq_reply:t-1",
            "content": {"text": "Our favorite color is red."},
            "version": task["version"],
        },
        headers=headers("ops-test"),
    )
    assert r.status_code == 409 and r.json()["error"] == "task_rule", r.text
    assert "Never tell anyone" in r.text
    assert row(api, task["id"])["version"] == task["version"]


def test_a_check_that_cannot_run_writes_nothing(ruled, monkeypatch):
    api, _ = ruled
    color_rule(api)
    from backend import task_rule_gate

    def broken(*a, **k):
        raise RuntimeError("audience")

    monkeypatch.setattr(task_rule_gate.TaskRules, "audience", broken)
    task = task_for_ops(api)
    r = api.post(
        f"/api/v2/tasks/{task['id']}/done",
        json={"version": task["version"], "note": "All done."},
        headers=headers("ops-test"),
    )
    assert r.status_code == 503, r.text
    assert row(api, task["id"])["status"] != "done"


def test_a_held_hand_off_of_a_private_task_keeps_its_text_off_the_hold(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    with api.app.state.store.transaction() as c:
        task = H.task_create(
            c, "human:ana", "Palette plan", "Our favorite color is red.", "bot:ops", lint=False, private=True
        )
    got = ops(api, f"tasks/{task['id']}/owner", {"owner": "ana", "version": task["version"]})
    assert got["held"], got
    stored = json.loads(hold_row(api, got["held"]["id"])["payload"])
    assert "favorite color" not in stored["text"] and "Palette plan" not in stored["text"]
    assert row(api, task["id"])["owner_id"] == "bot:ops"
