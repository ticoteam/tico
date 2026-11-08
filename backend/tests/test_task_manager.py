"""The Task Manager (backend/task_manager.py, docs/task-manager.md): built in for every team, narrow task rights that never
widen privacy, and an hourly weekday report built in code that opens one task per department only when it has something
(no task, no model call otherwise), absorbs into a still-open one, and never lists a private task. Synthetic fixtures."""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend import system_routines as SR
from backend import task_manager as TM
from backend import task_relations as TR
from backend.auth import Identity
from backend.onboarding import helper_templates
from backend.store import H
from backend.tests.test_goal_manager import BASE
from backend.tests.test_onboarding import draft, environment, machine, signed_in  # noqa: F401

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "task-manager"
SECRET = "Acme payroll for March"


def built(environment):
    api = environment(seed={}, cards=BASE)
    shutil.copytree(TEMPLATE, Path(api.app.state.store.settings.catalog_dir) / "task-manager")
    machine(api)
    draft(api)
    api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    return api


def as_manager(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
                  (TM.SLUG, "hermes", "hash-tm", "2026-01-01T00:00:00Z", "human:test"))
    api.app.state.store.settings.test_identities["tm"] = Identity(TM.ACTOR, "bot", agent="hermes")
    return signed_in("tm")


def quiet_task(api, title="Reconcile the Acme invoices", private=False, owner="bot:coo"):
    """A task its bot let go quiet three hours ago, with nothing running for the bot."""
    made = api.post("/api/v2/tasks", json={"title": title, "body": "Details for " + title, "owner": owner}, headers=signed_in())
    assert made.status_code == 200, made.text
    made = made.json()["task"]
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
        c.execute("UPDATE tasks SET updated_at=?, private=? WHERE id=?", (H.shift(H.now(), hours=-3), int(private), made["id"]))
    return made["id"]


def report(api):
    store = api.app.state.store
    with store.transaction() as c:
        return TM.report(c, api.app.state.auth, store.settings, H.now())


def checks(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT id,title,body,status FROM tasks WHERE owner_id=? ORDER BY created_at", (TM.ACTOR,))]


def test_it_is_built_in_active_and_outside_the_org_chart(environment):
    api = built(environment)
    with api.app.state.store.read() as c:
        assert H.bot(c, TM.SLUG)["state"] == "active"
        assert api.app.state.auth.system_bot(TM.SLUG)
    assert "task-manager" in helper_templates(api.app.state.store.settings)


def test_an_empty_report_opens_no_task_and_wakes_nothing(environment):
    api = built(environment)
    assert report(api)["words"] == "Nothing to do"
    assert checks(api) == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id=?", (TM.SLUG,)).fetchone()[0] == 0


def test_a_quiet_task_opens_one_check_that_a_later_report_absorbs_and_a_private_task_never_appears(environment):
    api = built(environment)
    quiet = quiet_task(api)
    private = quiet_task(api, SECRET, private=True)
    first = report(api)
    assert first["items"] == 1 and first["words"] == "1 department, 1 item"
    (check,) = checks(api)
    assert check["title"] == "Task check: Company (1)" and quiet in check["body"] and "hub task get" in check["body"]
    assert private not in check["body"] and SECRET not in check["body"]
    assert report(api)["items"] == 0 and len(checks(api)) == 1          # reported once: left out for a day, nothing new
    second = quiet_task(api, "Chase the Acme renewal")
    assert report(api)["items"] == 1
    (check,) = checks(api)                                              # absorbed: the same task, a new body
    assert check["title"] == "Task check: Company (1)" and second in check["body"] and quiet not in check["body"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (check["id"],))
    third = quiet_task(api, "Send the Acme statements")
    report(api)
    assert [t["status"] for t in checks(api)] == ["done", "open"] and third in checks(api)[1]["body"]


def act(api, name, *args, **kwargs):
    with api.app.state.store.transaction() as c:
        return getattr(H, name)(c, TM.ACTOR, *args, **kwargs)


def test_it_closes_with_a_note_but_cannot_touch_a_private_task_or_a_mover_field(environment):
    api = built(environment)
    open_id, secret_id = quiet_task(api), quiet_task(api, SECRET, private=True)
    assert api.post(f"/api/v2/tasks/{open_id}/comments", json={"text": "Looking"}, headers=as_manager(api)).status_code == 200
    assert api.post(f"/api/v2/tasks/{secret_id}/comments", json={"text": "Hi"}, headers=as_manager(api)).status_code == 404
    assert act(api, "task_update", open_id, status="doing", note="Picked up")["status"] == "doing"
    for field in ({"lane": "product"}, {"type": "Dev tickets"}, {"title": "Renamed"}, {"rank": 1}):
        with pytest.raises(H.Refused, match="Task Manager sets"):
            act(api, "task_update", open_id, **field)                         # beyond its grant, never the Task Manager's
    other = quiet_task(api, "Ship the Acme export")
    with api.app.state.store.transaction() as c:
        assert TR.add_blocker(c, TM.ACTOR, other, open_id, note="Export waits on the invoices")
        assert TR.remove_blocker(c, TM.ACTOR, other, open_id)                 # relations are inside its grant
    for call in (lambda: act(api, "task_update", secret_id, status="doing"), lambda: act(api, "task_close", secret_id, "x")):
        with pytest.raises(H.Refused):
            call()
    with pytest.raises(H.Refused):
        act(api, "task_close", open_id, "")                                    # never without saying why
    assert act(api, "task_close", open_id, "Done long ago")["status"] == "closed"


def test_it_reassigns_only_within_the_owners_department(environment):
    api = built(environment)
    task = quiet_task(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.team','sales') WHERE bot='botops'")
        groups = {"sales": {"name": "Sales", "parent": ""}, "support": {"name": "Support", "parent": ""}}
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('people', ?)",
                  (json.dumps({"people": [], "org_groups": groups}),))
    with api.app.state.store.read() as c:
        assert TM.same_department(c, "bot:coo", "bot:coo") and not TM.same_department(c, "bot:coo", "bot:botops")
    with pytest.raises(H.Refused, match="department"):
        act(api, "task_update", task, owner="bot:botops")


def test_the_business_hours_preset_runs_hourly_on_weekdays_only():
    zone = SR.zone()
    at = lambda day, hour: datetime(2026, 10, day, hour, 5, tzinfo=zone).astimezone(timezone.utc)   # Oct 23 is a Friday
    cfg = {"preset": SR.BUSINESS, "at": "09:00", "on": True}
    assert SR.due(cfg, at(23, 9), None) and not SR.due(cfg, at(23, 9), at(23, 9)) and SR.due(cfg, at(23, 10), at(23, 9))
    assert not SR.due(cfg, at(23, 19), None) and not SR.due(cfg, at(24, 10), None)       # after 18:00, and a Saturday
    after_close = SR.next_due(cfg, at(23, 17), at(23, 18))
    assert after_close.astimezone(zone).weekday() == 0 and after_close.astimezone(zone).hour == 8     # Monday 08:00


def test_the_switch_the_schedule_run_now_and_the_status_work_through_the_api(environment):
    api = built(environment)
    quiet_task(api)
    owner = signed_in      # a fresh Idempotency-Key per write
    status = api.get("/api/v2/task-manager", headers=owner()).json()
    assert status["on"] and status["routine"]["preset"] == "business"
    saved = api.put("/api/v2/system-routines/task_manager_report", json={"preset": "daily", "at": "07:30"}, headers=owner())
    assert saved.status_code == 200 and saved.json()["job"]["key"] == "task_manager_report"
    off = api.put("/api/v2/system-routines/task_manager_report", json={"on": False}, headers=owner())
    assert off.status_code == 200, off.text
    assert api.post("/api/v2/system-routines/task_manager_report/run", json={}, headers=owner()).status_code == 409
    api.put("/api/v2/system-routines/task_manager_report", json={"on": True, "preset": "business"}, headers=owner())
    assert api.post("/api/v2/system-routines/task_manager_report/run", json={}, headers=owner()).status_code == 200
    assert api.post("/api/v2/task-manager/turn-off", json={}, headers=owner()).status_code == 200
    assert report(api)["words"] == "The Task Manager is off"
    assert api.post("/api/v2/task-manager/turn-on", json={}, headers=owner()).status_code == 200
