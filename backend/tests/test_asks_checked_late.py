"""A bot's ❓ to a person is checked right after it is saved when every rule it meets only warns (spec "Bots' questions
checked after save", 2026-11-25): the plain-words rule ("Asks to a person", a Warn) no longer makes the bot wait for the
judge. A Block or Escalate rule is still checked first and refuses it, nothing written; a ❓ Tico answers itself queues
no check (backend/task_comments_routes.py task_question, backend/task_rule_gate.py check)."""

import json
import threading

from backend import org_rule_check
from backend import org_rules as OR
from backend.tests.helpers.api import headers
from backend.tests.helpers.org_rule_check import (  # noqa: F401 - fixtures
    COLOR,
    checked_after,
    color_rule,
    notes_to,
    ruled,
    sql,
    task_for_ops,
)


def question(api, task, text, to="ana", token="ops-test"):
    return api.post(f"/api/v2/tasks/{task['id']}/questions", json={"to": to, "text": text}, headers=headers(token))


def comment(api, task, text, token="ops-test"):
    r = api.post(f"/api/v2/tasks/{task['id']}/comments", json={"text": text}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def age_late_checks(api, seconds):
    """Every waiting late check's write made `seconds` ago."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE org_rule_late SET created_at=?", (org_rule_check.H.shift(org_rule_check.H.now(), seconds=-seconds),)
        )


def settle(api):
    """Run the late checks still waiting, after the drain thread's pass (org_rule_check.drain_late holds one lane)."""
    checked_after(api)
    assert not sql(api, "SELECT 1 FROM org_rule_late LIMIT 1"), "a late rule check was still waiting"


def events(api, action):
    return [json.loads(e["detail"]) for e in sql(api, "SELECT detail FROM events WHERE action=?", (action,))]


def test_a_bots_questions_under_warn_rules_do_not_wait_for_the_judge(ruled, monkeypatch):
    """Criterion 1: with the built-in Warn ask rule, 20 ❓s each answer without asking the judge, and each is still
    judged right after it is saved. The late checks' drain thread is kept from starting while they are asked, so any
    call during them would be a request's own."""
    api, judge = ruled
    assert sql(api, "SELECT if_broken FROM org_rules WHERE text=?", (OR.ASKS_TO_A_PERSON,))[0]["if_broken"] == "warn"
    monkeypatch.setattr(org_rule_check, "kick_late", lambda store, primary: None)
    task = task_for_ops(api)
    for n in range(20):
        r = question(api, task, f"Which of the two launch note drafts goes out in week {n + 2}?")
        assert r.status_code == 200, r.text
        assert r.json()["question"] and "rule_warning" not in r.json()
    assert judge.calls == [], "no ❓ waited for the judge"
    assert sql(api, "SELECT count(*) AS n FROM org_rule_late")[0]["n"] == 20
    settle(api)
    assert len(judge.calls) == 20, "each ❓ is still checked, right after it is saved"


def test_a_question_a_warn_rule_finds_broken_reaches_its_person_and_the_bot_is_told_after(ruled):
    """Criterion 2: the ❓ is saved and marked as sent; within 10 s the bot has one note naming the rule and
    rules.warned is recorded once."""
    api, judge = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    text = "May I say our favorite color is red in the launch note?"
    r = question(api, task, text)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["question"]["line"] == text and "rule_warning" not in got
    assert sql(api, "SELECT person_id FROM question_marks")[0]["person_id"] == "human:ana"
    settle(api)
    told = [n for n in notes_to(api) if COLOR in n]
    assert len(told) == 1, notes_to(api)
    warned = events(api, "rules.warned")
    assert len(warned) == 1 and warned[0].get("late") is True, warned
    assert sql(api, "SELECT person_id FROM question_marks")[0]["person_id"] == "human:ana", "the ❓ stays asked"


def test_a_question_a_block_rule_finds_broken_is_refused_before_anything_is_written(ruled):
    """Criterion 3: a Block rule is checked before the write, as today: refused with the rule's words."""
    api, judge = ruled
    color_rule(api)  # block
    task = task_for_ops(api)
    text = "May I say our favorite color is red in the launch note?"
    r = question(api, task, text)
    assert r.status_code == 409, r.text
    said = r.json()
    detail = said["error"]["detail"] if isinstance(said.get("error"), dict) else said["detail"]
    assert COLOR in detail and len(judge.calls) == 1
    assert sql(api, "SELECT count(*) AS n FROM messages WHERE body LIKE ?", (f"%{text}%",))[0]["n"] == 0
    assert sql(api, "SELECT count(*) AS n FROM question_marks")[0]["n"] == 0
    assert sql(api, "SELECT count(*) AS n FROM org_rule_late")[0]["n"] == 0


def test_a_question_whose_late_check_times_out_stays_asked_and_is_recorded_unchecked(ruled):
    """Criterion 4: the judge out after the ❓ is saved: it stays, and rules.unchecked is recorded as today."""
    api, judge = ruled
    judge.fail = TimeoutError("judge timed out")
    task = task_for_ops(api)
    r = question(api, task, "Which tone should the launch note take, warm or plain?")
    assert r.status_code == 200, r.text
    settle(api)
    assert sql(api, "SELECT count(*) AS n FROM question_marks")[0]["n"] == 1
    unchecked = events(api, "rules.unchecked")
    assert len(unchecked) == 1 and unchecked[0]["operation"] == "question" and unchecked[0].get("late"), unchecked


def test_a_question_tico_answers_itself_saves_no_question_and_queues_no_check(ruled, monkeypatch):
    """Criterion 5: Tico's own answer comes back; no ❓ is saved and no check is queued."""
    api, judge = ruled
    monkeypatch.setattr("backend.stage_inputs.repositories", lambda c, task_id, readable: ["acme/app"])
    task = task_for_ops(api)
    r = question(api, task, "Which repo is this in?")
    assert r.status_code == 200, r.text
    assert r.json()["asked"] is False and "acme/app" in r.json()["answered_by_tico"]
    assert sql(api, "SELECT count(*) AS n FROM question_marks")[0]["n"] == 0
    assert sql(api, "SELECT count(*) AS n FROM org_rule_late")[0]["n"] == 0
    assert judge.calls == []


def test_an_older_clients_question_on_the_asks_route_is_checked_after_save_too(ruled):
    """The asks route (an older `tico`'s question) drops the same exception: its Warn check runs after the write."""
    api, judge = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    r = api.post(
        f"/api/v2/tasks/{task['id']}/asks",
        json={"to": "ana", "line": "May I say our favorite color is red?", "version": task["version"]},
        headers=headers("ops-test"),
    )
    assert r.status_code == 200, r.text
    assert "rule_warning" not in r.json() and r.json()["question"]
    settle(api)
    assert len(events(api, "rules.warned")) == 1
    assert [n for n in notes_to(api) if COLOR in n]


def test_a_questions_late_check_stops_waiting_for_a_busy_slot_in_time_for_its_10_seconds(ruled, monkeypatch):
    """Criterion 2 with every call slot busy: a ❓'s check waits for a slot only so long that a call started then still
    ends within 10 s of the save; past that it is recorded unchecked (reason `busy`), as a check before the save was.
    A comment's check of the same age keeps waiting (its own LATE_BUSY_SECONDS)."""
    K = org_rule_check
    assert K.LATE_QUESTION_BUSY_SECONDS + K.DEADLINES[K.RULE_LEVEL] + K.LATE_RETRY_SECONDS <= 10
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    monkeypatch.setattr(K, "kick_late", lambda store, primary: None)
    monkeypatch.setattr(K, "_ask", lambda *a, **k: (None, "busy"))
    assert question(api, task, "May I say our favorite color is red in the launch note?").status_code == 200
    comment(api, task, "Fun fact: our favorite color is red.")
    checked_after(api)
    assert sql(api, "SELECT count(*) AS n FROM org_rule_late")[0]["n"] == 2, "both wait for a slot at first"
    age_late_checks(api, K.LATE_QUESTION_BUSY_SECONDS + 1)
    checked_after(api)
    [unchecked] = events(api, "rules.unchecked")
    assert unchecked["operation"] == "question" and unchecked["reason"] == "busy" and unchecked["late"] is True
    assert sql(api, "SELECT count(*) AS n FROM question_marks")[0]["n"] == 1, "the ❓ stays asked"
    assert sql(api, "SELECT count(*) AS n FROM org_rule_late")[0]["n"] == 1, "the comment's check still waits"


def test_a_question_queued_behind_other_late_checks_is_checked_first(ruled, monkeypatch):
    """A backlog of other writes' late checks never holds a ❓'s back: the ❓, saved last, is in the first group judged."""
    api, judge = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    monkeypatch.setattr(org_rule_check, "kick_late", lambda store, primary: None)
    for n in range(12):
        comment(api, task, f"Step {n} is done.")
    asked = "Which of the two launch note drafts goes out first?"
    assert question(api, task, asked).status_code == 200
    settle(api)
    said = [c["state"]["output"] for c in judge.calls]
    assert len(said) == 13
    first = said[: org_rule_check._late_width(api.app.state.store)]  # one group, judged side by side
    assert any(asked in s for s in first), said


def test_late_checks_run_side_by_side(ruled, monkeypatch):
    """A backlog drains several checks at a time, not one by one: two calls are in the judge at once. One by one, the
    first would wait for a second that never comes, fail, and be recorded unchecked."""
    api, judge = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    monkeypatch.setattr(org_rule_check, "kick_late", lambda store, primary: None)
    for n in range(4):
        comment(api, task, f"Fun fact {n}: our favorite color is red.")
    together = threading.Barrier(2, timeout=4)
    answer = judge.__call__

    def side_by_side(state, questions, label=None):
        together.wait()
        return answer(state, questions, label)

    api.app.state.judge = side_by_side
    settle(api)
    assert events(api, "rules.unchecked") == []
    assert len(events(api, "rules.warned")) == 4
