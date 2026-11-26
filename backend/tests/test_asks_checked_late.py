"""A bot's ❓ to a person is checked right after it is saved when every rule it meets only warns (spec "Bots' questions
checked after save", 2026-11-25): the plain-words rule ("Asks to a person", a Warn) no longer makes the bot wait for the
judge. A Block or Escalate rule is still checked first and refuses it, nothing written; a ❓ Tico answers itself queues
no check (backend/task_comments_routes.py task_question, backend/task_rule_gate.py check)."""

import json

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
