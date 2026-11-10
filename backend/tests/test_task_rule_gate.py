"""The org rule check on the v3 task routes (backend/task_rule_gate.py, backend/task_moves_routes.py): every task write
that says something is checked through the real route, held writes change nothing, Send anyway replays them for the
reviewer only, and a check that cannot run writes nothing."""

import json

import pytest

from backend.store import H
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.org_rules import rule
from backend.tests.test_org_rule_check import (  # noqa: F401 - fixtures
    COLOR,
    LAUNCH,
    _not_a_friday,
    hand_over,
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


def done_by_ops(api, task, note, expected=200):
    r = api.post(
        f"/api/v2/tasks/{task['id']}/done",
        json={"version": task["version"], "note": note},
        headers=headers("ops-test"),
    )
    assert r.status_code == expected, r.text
    return r.json()


def reasons(api):
    found = sql(api, "SELECT detail FROM events WHERE action='rules.unchecked' ORDER BY rowid")
    return [json.loads(e["detail"])["reason"] for e in found]


def test_a_fault_in_the_rule_lookup_is_a_503_and_writes_nothing(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api)
    from backend import org_rule_check as K

    def broken(*a, **k):
        raise ValueError("bad rule row")

    task = task_for_ops(api)
    monkeypatch.setattr(K.R, "matching", broken)
    done_by_ops(api, task, "Our favorite color is red.", 503)
    assert row(api, task["id"])["status"] != "done" and judge.calls == []


def test_the_judge_being_out_lets_the_write_go_with_an_unchecked_event(ruled):
    api, judge = ruled
    color_rule(api)
    judge.fail = RuntimeError("provider down")
    task = task_for_ops(api)
    got = done_by_ops(api, task, "Our favorite color is red.")
    assert "held" not in got and row(api, task["id"])["status"] == "done"
    assert reasons(api) == ["error"]


def problem(got):
    """(code, retryable) of an error answer, whichever shape it is in."""
    error = got["error"]
    if isinstance(error, dict):
        return error["code"], bool(error.get("retryable"))
    return error, bool(got.get("retryable"))


def no_calls_left(monkeypatch):
    from backend import org_rule_check as K

    monkeypatch.setattr(K.B, "daily_cap", lambda *a, **k: 0)


def all_slots_busy(monkeypatch):
    from backend import org_rule_check as K

    monkeypatch.setattr(K, "_ask", lambda *a, **k: (None, "busy"))


def bots_ask(api, task, line="May I say our favorite color is red?", expected=200):
    r = api.post(
        f"/api/v2/tasks/{task['id']}/asks",
        json={"to": "ana", "line": line, "version": task["version"]},
        headers=headers("ops-test"),
    )
    assert r.status_code == expected, r.text
    return r.json()


def asks_on(api, task):
    return sql(
        api, "SELECT id FROM requirements WHERE visit_id IN (SELECT id FROM visits WHERE task_id=?)", (task["id"],)
    )


@pytest.mark.parametrize("cause", ["day_cap", "busy", "too_large"])
def test_a_bots_ask_the_check_cannot_judge_here_is_refused_to_try_again(ruled, monkeypatch, cause):
    api, judge = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    before = asks_on(api, task)
    if cause == "day_cap":
        no_calls_left(monkeypatch)
    elif cause == "busy":
        all_slots_busy(monkeypatch)
    else:
        from clients import judge as J

        monkeypatch.setattr(J, "MAX_STATE_CHARS", 200)
    got = bots_ask(api, task, "Which launch day suits you, Monday or Tuesday or later this month?", 503)
    assert problem(got) == ("task_rule_unchecked", True), got
    assert asks_on(api, task) == before and row(api, task["id"])["version"] == task["version"]
    assert sql(api, "SELECT state FROM bots WHERE slug='ops'")[0]["state"] == "active"
    assert judge.calls == []


def test_a_bot_spending_the_days_calls_cannot_then_ask_unchecked(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api)
    from backend import org_rule_check as K

    monkeypatch.setattr(K.B, "daily_cap", lambda settings, who: 1)
    task = task_for_ops(api)
    bots_ask(api, task, "Which launch day suits you?")  # the day's one call
    assert len(judge.calls) == 1
    task = task_for_ops(api, "Write the second note")
    bots_ask(api, task, "May I say our favorite color is red?", 503)
    assert asks_on(api, task) == [] and len(judge.calls) == 1


def test_a_done_note_with_the_days_calls_spent_takes_the_hold_path(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api)
    no_calls_left(monkeypatch)
    task = task_for_ops(api)
    got = done_by_ops(api, task, "All shipped.")
    assert got["held"] and "could not be checked today" in got["held"]["text"]
    assert row(api, task["id"])["status"] != "done"


def test_a_done_note_with_the_days_calls_spent_under_warn_rules_goes_with_its_line(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api, if_broken="warn")
    no_calls_left(monkeypatch)
    task = task_for_ops(api)
    got = done_by_ops(api, task, "All shipped.")
    assert "held" not in got and row(api, task["id"])["status"] == "done"
    said = json.dumps(sql(api, "SELECT notes FROM answers ORDER BY rowid DESC LIMIT 1"))
    assert "could not be checked against the rule" in said
    assert reasons(api) == ["day_cap"]


def test_a_done_note_with_every_call_slot_busy_writes_nothing(ruled, monkeypatch):
    api, _ = ruled
    color_rule(api)
    all_slots_busy(monkeypatch)
    task = task_for_ops(api)
    got = done_by_ops(api, task, "All shipped.", 503)
    assert problem(got) == ("rule_check_busy", True), got
    assert row(api, task["id"])["status"] != "done"


def test_a_rule_past_what_one_call_judges_still_holds_the_write(ruled, monkeypatch):
    api, judge = ruled
    from clients import judge as J

    monkeypatch.setattr(J, "MAX_QUESTIONS", 2)
    for n in range(4):  # made first: the rules matched are read oldest first
        rule(api, {"kind": "internal", "text": f"Keep note {n} short.", "who": {"bots": ["ops"]}})
    color_rule(api)
    task = task_for_ops(api)
    assert done_by_ops(api, task, "Our favorite color is red.")["held"]
    assert len(judge.calls) == 3 and reasons(api) == []
    assert row(api, task["id"])["status"] != "done"


def between_check_and_write(monkeypatch, change):
    """`change(c)` runs once, after the route's check and before its write."""
    from backend import task_rule_gate

    real, done = task_rule_gate.TaskRules.check, []

    def check(self, *a, **k):
        verdict = real(self, *a, **k)
        if not done:
            done.append(1)
            with self.store.transaction() as c:
                change(c)
        return verdict

    monkeypatch.setattr(task_rule_gate.TaskRules, "check", check)


def test_a_hand_off_checked_safe_then_given_a_forbidden_body_is_not_delivered(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api)
    task = task_for_ops(api)
    between_check_and_write(
        monkeypatch,
        lambda c: c.execute(
            "UPDATE tasks SET body='Our favorite color is red.', version=version+1 WHERE id=?", (task["id"],)
        ),
    )
    # A person hands it on (no ask ingest, which has its own check of the task).
    rule(api, {"kind": "internal", "text": COLOR, "who": {"everyone": True}})
    r = api.post(f"/api/v2/tasks/{task['id']}/owner", json={"owner": "ben"}, headers=headers("ana-test"))
    assert r.status_code == 409 and problem(r.json())[0] == "task_changed", r.text
    assert row(api, task["id"])["owner_id"] == "bot:ops"
    # Sent again, it is checked again, on the body as it is now.
    r = api.post(f"/api/v2/tasks/{task['id']}/owner", json={"owner": "ben"}, headers=headers("ana-test"))
    assert r.status_code == 200 and r.json()["held"], r.text
    assert row(api, task["id"])["owner_id"] == "bot:ops"


def test_a_snooze_checked_for_some_people_is_not_written_once_the_task_has_others(ruled, monkeypatch):
    api, judge = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    between_check_and_write(
        monkeypatch,
        lambda c: c.execute("UPDATE tasks SET requester_id='human:cara' WHERE id=?", (task["id"],)),
    )
    r = api.post(
        f"/api/v2/tasks/{task['id']}/snooze", json={"why": "Waiting on the launch."}, headers=headers("ops-test")
    )
    assert r.status_code == 409 and problem(r.json())[0] == "task_changed", r.text
    assert sql(api, "SELECT 1 FROM snoozes WHERE task_id=?", (task["id"],)) == []


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


def private_task(api, body="Our favorite color is red."):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, "human:ana", "Palette plan", body, "bot:ops", lint=False, private=True)


def every_word_but_the_hold(api, words):
    """Nothing but the hold's own input names `words`: not the events, not the hold's reviewer view."""
    for e in sql(api, "SELECT detail, target FROM events"):
        assert words not in json.dumps(e), e


def test_a_held_automation_hand_off_of_a_private_task_shows_the_reviewer_none_of_its_words(ruled):
    api, _ = ruled
    color_rule(api, if_broken="escalate", reviewer="ben")
    task = private_task(api)
    post(api, f"automations/{hand_over(api)}/run", {"input": {"task": {"id": task["id"]}}})
    [held] = sql(api, "SELECT id, payload FROM org_rule_holds")
    stored = json.loads(held["payload"])
    assert "color is red" not in stored["text"] and "Palette plan" not in stored["text"]
    assert "color is red" in stored["input"]["text"]  # kept for the recheck alone
    view = get(api, f"rule-holds/{held['id']}", token="ben-test")
    assert "color is red" not in json.dumps(view) and "Palette plan" not in json.dumps(view)
    listed = get(api, "rule-holds", token="ben-test")
    assert "color is red" not in json.dumps(listed) and "Palette plan" not in json.dumps(listed)
    every_word_but_the_hold(api, "color is red")
    assert row(api, task["id"])["owner_id"] == "bot:ops"


def test_send_anyway_of_a_private_hand_off_rechecks_a_new_rule_on_the_whole_task(ruled):
    api, judge = ruled
    rule(
        api,
        {
            "kind": "internal",
            "text": "Never say the word pineapple.",
            "who": {"bots": ["ops"]},
            "if_broken": "escalate",
            "reviewer": "ben",
        },
    )
    judge.needles["pineapple"] = "pineapple"
    task = private_task(api, "The launch date is May 5.")
    got = ops(api, f"tasks/{task['id']}/owner", {"owner": "ana", "why": "Pineapple time.", "version": task["version"]})
    held = got["held"]
    stored = json.loads(hold_row(api, held["id"])["payload"])
    assert "launch date" not in stored["text"] and "launch date" in stored["input"]["text"]
    # A rule added since the hold covers the private task's body, which the reviewer never saw.
    rule(api, {"kind": "internal", "text": LAUNCH, "who": {"bots": ["ops"]}})
    judge.needles[LAUNCH] = "launch date is"
    view = get(api, f"rule-holds/{held['id']}", token="ben-test")
    r = api.post(f"/api/v2/rule-holds/{held['id']}/send", json={"nonce": view["nonce"]}, headers=headers("ben-test"))
    assert r.status_code == 409 and "launch date" in r.text, r.text
    assert row(api, task["id"])["owner_id"] == "bot:ops"
