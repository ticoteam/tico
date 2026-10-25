"""The Task Manager and the tasks page's right bar: it acts only with the chatting person's rights, every rule change and
bulk fix waits for that person's confirm, other departments' rules are read-only, and it never reads a task the person
cannot see. Synthetic fixtures; the model is faked."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import task_checks as TC
from backend import task_manager as TM
from backend.store import H
from backend.tests.test_api import api, as_member, get, post  # noqa: F401
from backend.tests.test_task_checks import Decide


class Model:
    """The Task Manager's model: answers each message with the next queued action and keeps what it was shown."""

    def __init__(self, *actions):
        self.actions, self.seen = list(actions), []

    def __call__(self, system, user):
        self.seen.append(user)
        return {"reply": "Here it is.", "action": self.actions.pop(0) if self.actions else None}


@pytest.fixture
def model(api):
    api.app.state.task_manager_writer = fake = Model()
    return fake


def say(api, text, token="ana-test", **extra):
    return post(api, "task-manager", {"text": text, **extra}, token=token)["messages"]


def card(messages):
    return next(m for m in messages if m.get("card"))


def rule(api, key):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM task_rules WHERE id=?", (key,)).fetchone())


def test_a_rule_change_waits_for_the_chatters_own_confirm_and_runs_with_their_rights_then(api, model):
    model.actions = [{"kind": "change_rule", "rule": TC.WAITING, "on": False}] * 2
    pending = card(say(api, "Stop refusing waits without a reason", token="ben-test"))
    assert pending["card"]["state"] == "pending" and rule(api, TC.WAITING)["enabled"] == 1
    # Nobody else confirms it, the owner included.
    post(api, f"task-manager/cards/{pending['id']}/confirm", {}, token="ana-test", expected=404)
    # Rights are the confirmer's at the click: Ben lost Admin since, so the route refuses and nothing changes.
    as_member(api, "ben@acme.example")
    out = post(api, f"task-manager/cards/{pending['id']}/confirm", {}, token="ben-test")
    assert not out["ok"] and out["messages"][0]["card"]["state"] == "failed"
    assert rule(api, TC.WAITING)["enabled"] == 1
    post(api, f"task-manager/cards/{pending['id']}/confirm", {}, token="ben-test", expected=409)
    # The owner's own card runs once confirmed.
    mine = card(say(api, "Switch the waiting check off"))
    assert rule(api, TC.WAITING)["enabled"] == 1
    assert post(api, f"task-manager/cards/{mine['id']}/confirm", {})["ok"]
    assert rule(api, TC.WAITING)["enabled"] == 0


def test_other_departments_rules_are_read_only_and_the_task_manager_declines_them(api, model):
    bar = get(api, "task-checks/bar?type=general", token="cara-test")
    waiting = next(r for r in bar["rules"] if r["key"] == TC.WAITING)
    assert not waiting["can_change"] and waiting["read_only"] and "Engineering" in waiting["read_only"]
    model.actions = [{"kind": "change_rule", "rule": TC.WAITING, "on": False},
                     {"kind": "propose_check", "check": "duplicate", "name": "Dupes", "then": ["warn"],
                      "department": "engineering"},
                     {"kind": "delete_task", "task": "anything"}]
    for text in ("Turn off the waiting check", "Warn on duplicates", "Delete my old tasks"):
        messages = say(api, text, token="cara-test")
        assert not any(m.get("card") for m in messages)
    assert rule(api, TC.WAITING)["enabled"] == 1
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM task_rules WHERE name='Dupes'").fetchone()[0] == 0


def test_a_new_check_is_shown_as_its_row_and_made_only_on_confirm(api, model):
    model.actions = [{"kind": "propose_check", "check": "quiet_wait", "name": "Chase quiet waits", "then": ["nudge"],
                      "hours": 48, "types": ["general"]}]
    pending = card(say(api, "Waiting tasks sit forever"))
    assert "WHEN" in pending["card"]["summary"] and "FOR" in pending["card"]["summary"]
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM task_rules WHERE name='Chase quiet waits'").fetchone()
    assert post(api, f"task-manager/cards/{pending['id']}/confirm", {})["ok"]
    made = next(j for j in get(api, "system-routines")["jobs"] if j["name"] == "Chase quiet waits")
    assert made["on"] and made["check"] == {"key": "quiet_wait", "hours": 48} and made["types"] == ["general"]


def test_check_all_reads_only_visible_tasks_and_its_bulk_fix_waits_for_confirm(api, model):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_rules SET enabled=1 WHERE id=?", (TC.DUPLICATE,))
        old = H.task_create(c, "human:cara", "Prepare the quarterly export", "", "bot:ops", deduplicate=False)
        new = H.task_create(c, "human:cara", "Prepare the quarterly export now", "", "bot:ops", deduplicate=False)
        # The mail bot is Ana's alone: Cara reads none of its tasks.
        for title in ("Payroll secret reconciliation", "Payroll secret reconciliation now"):
            H.task_create(c, "human:ana", title, "", "bot:inbox", deduplicate=False)
    model.actions = [{"kind": "check_all"}]
    messages = say(api, "Anything off with my tasks?", token="cara-test")
    assert "Payroll" not in json.dumps(messages) and "Payroll" not in "".join(model.seen)
    fix = card(messages)
    assert fix["card"]["kind"] == "fix" and "Payroll" not in fix["card"]["summary"]
    with api.app.state.store.read() as c:
        assert H.task(c, new["id"])["status"] != "closed"
    out = post(api, f"task-manager/cards/{fix['id']}/confirm", {}, token="cara-test")
    assert out["ok"], out
    with api.app.state.store.read() as c:
        assert H.task(c, new["id"])["closed_at"] and not H.task(c, old["id"])["closed_at"]
        assert c.execute("SELECT count(*) FROM tasks WHERE owner='bot:inbox' AND closed_at IS NULL").fetchone()[0] == 2


def test_a_task_made_on_request_goes_through_the_checks_and_offers_a_comment_when_blocked(api, model):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_rules SET enabled=1 WHERE id=?", (TC.DUPLICATE,))
    first = post(api, "tasks", {"owner": "ops", "title": "Book the offsite venue", "body": "For May."})
    api.app.state.judge = Decide()
    model.actions = [{"kind": "create_task", "title": "Book the offsite venue for May", "owner": "ops", "body": "Near the office."}]
    pending = card(say(api, "Make a task for ops to book the offsite venue"))
    out = post(api, f"task-manager/cards/{pending['id']}/confirm", {})
    assert not out["ok"]
    comment = card(out["messages"][1:])
    assert comment["card"]["kind"] == "comment"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE title='Book the offsite venue'").fetchone()[0] == 1
    assert post(api, f"task-manager/cards/{comment['id']}/confirm", {})["ok"]
    comments = get(api, f"tasks/{first['id']}/comments")
    assert "Near the office." in json.dumps(comments)


def test_catches_show_and_take_marks_only_on_readable_tasks_and_feed_the_weekly_report(api):
    with api.app.state.store.transaction() as c:
        seen = H.task_create(c, "human:cara", "Collect the logs", "", "bot:ops", deduplicate=False)
        hidden = H.task_create(c, "human:ana", "Payroll secret run", "", "bot:inbox", deduplicate=False)
        TC.caught(c, TC.WAITING, seen["id"])
        TC.caught(c, TC.WAITING, hidden["id"])
        ids = {r[1]: r[0] for r in c.execute("SELECT id,json_extract(detail_json,'$.task') FROM events "
                                             "WHERE action='task_check.caught'")}
    bar = get(api, "task-checks/bar?type=general", token="cara-test")
    catches = next(r for r in bar["rules"] if r["key"] == TC.WAITING)["catches"]
    assert [x["task"]["id"] for x in catches] == [seen["id"]] and bar["today"] == 2
    post(api, f"task-checks/catches/{ids[hidden['id']]}/mark", {"verdict": "wrong"}, token="cara-test", expected=404)
    post(api, f"task-checks/catches/{ids[seen['id']]}/mark", {"verdict": "wrong"}, token="cara-test")
    with api.app.state.store.read() as c:
        week = TM.report(c, datetime.now(timezone.utc) + timedelta(days=7))
    assert week["catches"] == 2 and week["wrong"] == 1 and week["marked"] == 1 and week["suggestions"]


def test_a_saved_filter_lands_only_on_tabs_the_person_may_edit(api, model):
    tab = {"kind": "save_tab", "name": "Waiting on me", "conditions": [{"field": "waiting_on", "value": "me"}]}
    model.actions = [{**tab, "bot": "ops"}, {**tab, "bot": "ops"}]
    # Cara manages no bot: declined, no card.
    assert not any(m.get("card") for m in say(api, "Give ops a tab of what waits on me", token="cara-test"))
    pending = card(say(api, "Give ops a tab of what waits on me"))
    assert "Waiting on me" not in json.dumps(get(api, "bots/ops/task-tabs"))
    assert post(api, f"task-manager/cards/{pending['id']}/confirm", {})["ok"]
    tabs = get(api, "bots/ops/task-tabs")["tabs"]
    assert tabs[-1]["name"] == "Waiting on me" and len(tabs) == 5          # the four defaults are kept
