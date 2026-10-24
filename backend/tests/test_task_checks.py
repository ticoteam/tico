"""Task checks in the Routines list: a duplicate blocked before create with a reason to override, fail-open Decide, the
waiting check switched from its row, and a dry run that changes nothing. Synthetic fixtures; Decide is faked."""
import time

import pytest

from backend import task_checks as TC
from backend.store import H
from backend.tests.test_api import api, get, headers, post, put  # noqa: F401


class Decide:
    """A Gateway Decide that answers the covered question with a fixed option, or sleeps past the budget."""

    def __init__(self, choice="task1", confidence=0.95, sleep=0.0):
        self.choice, self.confidence, self.sleep, self.calls = choice, confidence, sleep, 0

    def __call__(self, state, questions, label=None):
        self.calls += 1
        if self.sleep:
            time.sleep(self.sleep)
        return {"model": "tico-decide-1.0", "usage": {"input_tokens": 300, "output_tokens": 0}, "ms": 90,
                "answers": {"covered": {"type": "choice", "choice": self.choice, "confidence": self.confidence}}}


@pytest.fixture(autouse=True)
def duplicate_on(api):
    """The shared fixture switches the day-one Duplicate check off; here it is as shipped."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_rules SET enabled=1 WHERE id='task-check-duplicate'")


def events(api, action, target):
    with api.app.state.store.read() as c:
        return [r[0] for r in c.execute("SELECT detail_json FROM events WHERE action=? AND target=?", (action, target))]


def test_duplicate_is_refused_with_the_rule_and_the_match_and_a_reason_files_it(api):
    first = post(api, "tasks", {"owner": "ops", "title": "Prepare the quarterly export", "body": "Numbers for Q3."})
    api.app.state.judge = model = Decide()
    r = api.post("/api/v2/tasks", json={"owner": "ops", "title": "Prepare quarterly export file", "body": "Q3."},
                 headers=headers("ana-test", "dup-1"))
    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "task_check" and "'Duplicate'" in error["detail"] and first["id"][:8] in error["detail"]
    assert "Prepare the quarterly export" in error["detail"] and "--reason" in error["detail"] and "open" in error["detail"]
    assert error["task_check"]["match"]["id"] == first["id"] and model.calls == 1
    assert len(events(api, "task_check.caught", TC.DUPLICATE)) == 1         # counted though the write rolled back
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE title='Prepare quarterly export file'").fetchone()[0] == 0
    made = post(api, "tasks", {"owner": "ops", "title": "Prepare quarterly export file", "body": "Q3.",
                               "reason": "This one is the finance copy"})
    assert made["title"] == "Prepare quarterly export file"
    assert "finance copy" in events(api, "task_check.override", TC.DUPLICATE)[0]
    job = next(j for j in get(api, "system-routines")["jobs"] if j["key"] == TC.DUPLICATE)
    assert "1 catch" in job["stats"] and "1 override" in job["stats"] and job["check"] == {"key": "duplicate"}
    # Switched off from its row, the same create goes through with no Decide call.
    put(api, "task-rules/" + TC.DUPLICATE, {"on": False})
    post(api, "tasks", {"owner": "ops", "title": "Prepare the export for the quarter", "body": "Q3."})
    assert model.calls == 2


def test_decide_errors_or_slowness_fail_open_and_count_a_skipped_check(api, monkeypatch):
    post(api, "tasks", {"owner": "ops", "title": "Write the onboarding guide", "body": "For new staff."})
    monkeypatch.setattr(TC, "BUDGET", 0.2)
    api.app.state.judge = Decide(sleep=1.0)
    made = post(api, "tasks", {"owner": "ops", "title": "Write onboarding guide draft", "body": "Draft."})
    assert made["title"] == "Write onboarding guide draft"
    assert len(events(api, "task_check.skipped", TC.DUPLICATE)) == 1
    # No near match: no Decide call at all.
    api.app.state.judge = model = Decide()
    post(api, "tasks", {"owner": "ops", "title": "Book the venue", "body": "Venue."})
    assert model.calls == 0


def test_waiting_check_is_the_existing_validation_switched_and_named_by_its_row(api):
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Collect the logs", "Synthetic fixture", "bot:ops", deduplicate=False)
    with pytest.raises(H.Refused) as refused:
        with api.app.state.store.transaction() as c:
            H.task_update(c, "bot:ops", task["id"], status="waiting")
    assert "Task check 'Waiting must say on whom or what'" in refused.value.detail
    assert refused.value.task_check == {"rule": TC.WAITING, "task": task["id"]}
    # A member of another department cannot switch it; its department can.
    put(api, "task-rules/" + TC.WAITING, {"on": False}, token="cara-test", expected=403)
    put(api, "task-rules/" + TC.WAITING, {"on": False})
    with api.app.state.store.transaction() as c:
        assert H.task_update(c, "bot:ops", task["id"], status="waiting")["status"] == "waiting"
    # Check now lists it and changes nothing; the quiet-waits row cannot switch on without its time.
    found = post(api, f"task-rules/{TC.WAITING}/check", {})["found"]
    assert [f["task"]["id"] for f in found] == [task["id"]]
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "waiting"
    put(api, "task-rules/" + TC.QUIET, {"on": True}, expected=422)
    assert put(api, "task-rules/" + TC.QUIET, {"on": True, "check": {"key": "quiet_wait", "hours": 48}})["job"]["on"]
