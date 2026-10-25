"""Task checks in the Routines list: a duplicate blocked before create with a reason to override, fail-open Decide, the
waiting check switched from its row, and a dry run that changes nothing. Synthetic fixtures; Decide is faked."""
import time

import pytest

from backend import task_checks as TC
from backend.store import H
from backend import models as M
from backend.auth import Identity
from backend.tests.test_api import api, as_member, claim, get, headers, post, put  # noqa: F401
from backend.tests.test_member_bots import botops, turn  # noqa: F401  (fixture)


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


def access(api, bot, read=None):
    """Set a bot's access the way its runner does: everyone sees and writes; Read to `read` people (default everyone)."""
    everyone = {"everyone": True}
    current = get(api, f"bots/{bot}/access")
    put(api, f"bots/{bot}/access", {"see": everyone, "write": everyone, "revision": current["revision"],
                                    "read": {"people": read} if read else everyone})


def created_unnamed(api, token, title, owner="ops"):
    """The create goes through, and nothing in the reply names another task."""
    r = api.post("/api/v2/tasks", json={"owner": owner, "title": title, "body": "Synthetic."}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_a_match_the_caller_cannot_read_is_never_named_sent_to_decide_or_blocking(api):
    api.app.state.judge = model = Decide()
    as_member(api, "ben@acme.example")
    # Write without Read on the owner: Ana's task for ops is not Ben's to read, so it neither blocks nor is named.
    hidden = post(api, "tasks", {"owner": "ops", "title": "Audit the vendor contracts", "body": "Synthetic."})
    access(api, "ops", read=["ana"])
    reply = created_unnamed(api, "ben-test", "Audit the vendor contracts")
    assert hidden["id"][:8] not in str(reply) and "warning" not in reply
    # The other party is a bot Ben cannot read: a near match is not sent to Decide either.
    access(api, "ops")
    access(api, "finance", read=["ana"])
    with api.app.state.store.transaction() as c:
        H.task_create(c, "bot:finance", "Reconcile the payroll ledger", "Synthetic.", "bot:ops")
    created_unnamed(api, "ben-test", "Reconcile payroll ledger now")
    # A turn for Ana whose second principal (ops, the bot running it) cannot read Ana's private task with finance:
    # the before-create check finds nothing to name or ask Decide about (the HTTP create needs a live runner).
    private = post(api, "tasks", {"owner": "finance", "title": "Draft the severance letters", "body": "Synthetic.",
                                  "private": True})
    body = M.TaskCreate(owner="finance", title="Draft severance letters today", body="Synthetic.")
    run = Identity("human:ana", "owner", "ana@acme.example", task_actor="bot:ops")
    assert api.app.state.create_check(run, body) is None and model.calls == 0
    assert events(api, "task_check.caught", TC.DUPLICATE) == []
    # The same create as Ana herself, who can read it, is caught (a private match is not named).
    r = api.post("/api/v2/tasks", json={"owner": "finance", "title": "Draft severance letters soon", "body": "S."},
                 headers=headers("ana-test"))
    assert r.status_code == 422 and r.json()["error"]["task_check"]["match"] == {"private": True}, r.text
    assert private["id"][:8] not in r.text


def test_fix_never_links_to_a_task_the_person_cannot_read_and_missing_reads_the_same(api):
    as_member(api, "ben@acme.example")
    private = post(api, "tasks", {"owner": "ops", "title": "Plan the reorg", "body": "Synthetic.", "private": True})
    mine = post(api, "tasks", {"owner": "ops", "title": "Plan the team offsite", "body": "Synthetic."}, token="ben-test")
    tasks = [{"task": mine["id"], "duplicate_of": private["id"]},
             {"task": mine["id"], "duplicate_of": "00000000-0000-4000-8000-000000000000"}]
    results = post(api, f"task-rules/{TC.DUPLICATE}/fix", {"tasks": tasks}, token="ben-test")["results"]
    assert [r["ok"] for r in results] == [False, False] and results[0]["detail"] == results[1]["detail"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM task_relations").fetchone()[0] == 0
        assert H.task(c, mine["id"])["status"] == "open"


def test_a_private_match_is_never_named_in_the_refusal_or_its_audit(api):
    api.app.state.judge = Decide()
    private = post(api, "tasks", {"owner": "ops", "title": "Plan the reorg", "body": "Synthetic.", "private": True})
    r = api.post("/api/v2/tasks", json={"owner": "ops", "title": "Plan the reorg timeline", "body": "S."},
                 headers=headers("ana-test"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "task_check", r.text
    assert "existing private task" in r.json()["error"]["detail"]
    with api.app.state.store.read() as c:
        audit = str([tuple(row) for row in c.execute("SELECT detail_json FROM refusals")] +
                    [tuple(row) for row in c.execute("SELECT target,detail_json FROM events WHERE action IN "
                                                     "('refused','task_check.caught','judge.call')")])
    for leak in ("Plan the reorg\"", "Plan the reorg\\\"", private["id"][:8]):
        assert leak not in r.text and leak not in audit, leak
    assert len(events(api, "task_check.caught", TC.DUPLICATE)) == 1


def test_botops_filing_for_a_person_only_checks_what_both_can_read(api, botops):
    """A later BotOps run (its own rights) cites Cara's open task as the body's request_id, with no on-behalf-of header:
    the create is Cara's, BotOps the second principal, and the check reads as both before any Decide call - not as
    BotOps alone, which is on the private match."""
    from backend.tests.test_botops_parity import _wake, act
    from backend.tests.test_member_bots import finish
    cara = turn(api, botops, "cara-test", "Ask Ben about the badge printer")
    asked = act(api, cara, "POST", "tasks", {"owner": "botops", "title": "Ask Ben about the badge printer",
                                             "body": "For Cara.", "request_id": cara["message"]["id"]})
    assert asked.status_code == 200, asked.text
    cited = (asked.json().get("task") or asked.json())["id"]
    finish(api, botops, cara)
    finish(api, botops, claim(api, botops, "botops"))
    with api.app.state.store.transaction() as c:      # BotOps' own private task with Ben: Cara is not on it
        H.task_create(c, "bot:botops", "Rotate the signing keys", "Synthetic.", "human:ben", private=True)
    api.app.state.judge = model = Decide()
    later = _wake(api, botops)
    r = act(api, later, "POST", "tasks", {"owner": "ben", "title": "Rotate signing keys soon", "body": "Synthetic.",
                                          "request_id": cited}, ref=None)
    assert r.status_code == 200 and model.calls == 0, r.text
    assert r.json()["task"]["requester"] == "human:cara"
