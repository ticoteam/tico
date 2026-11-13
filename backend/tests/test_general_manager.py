"""The General Manager (backend/general_manager.py, docs/general-manager.md): built in for every team, the manager of General,
narrow task rights that never widen privacy, and an hourly weekday report built in code that opens one task per
department only when it has something (no task, no model call otherwise), absorbs into a still-open one, and never
lists a private task. Synthetic fixtures."""

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend import bot_settings as BS
from backend import system_routines as SR
from backend import general_manager as TM
from backend.onboarding import helper_templates
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.goal_manager import BASE
from backend.tests.helpers.onboarding import draft, environment, machine, signed_in  # noqa: F401

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "general-manager"
SECRET = "Acme payroll for March"
ANA = "human:ana"
BUILDER, STALLED = "bot:builder", "bot:stalled"  # STALLED has no computer: its tasks say Paused


@pytest.fixture
def c(company):
    """The company with the General Manager built from its template and managing General, a bot that can run and one
    that cannot."""
    with company.transaction() as conn:
        X.bot(conn, TM.SLUG)
        BS.write(conn, TM.SLUG, {"name": TM.SLUG, "runtime": "fake", "status": "active", "template": "general-manager"})
        conn.execute("UPDATE pipelines SET manager_id=? WHERE id='general'", (TM.ACTOR,))
        X.bot(conn, "builder")
        X.bot(conn, "stalled", computer=False)
        yield conn


def _new(c, title="Reconcile the Acme invoices", owner=STALLED, by=ANA, **more):
    return H.task_create(c, by, title, "Details for " + title, owner, lint=False, **more)["id"]


def _report(c):
    return TM.report(c, None, None, H.now())


def _checks(c):
    rows = c.execute("SELECT id,title,body,status FROM tasks WHERE owner_id=? ORDER BY created_at, id", (TM.ACTOR,))
    return [dict(r) for r in rows]


def _version(c, task_id):
    return H.task(c, task_id)["version"]


def synthetic_report_items(count, *, why_size=180, fix_size=190):
    return [
        {
            "kind": "paused",
            "task": f"synthetic-{i:02}",
            "title": f"Synthetic task {i:02}",
            "owner_id": "bot:assistant",
            "why": "w" * why_size,
            "fix": "f" * fix_size,
            "stamp": f"stamp-{i:02}",
            "key": f"paused:synthetic-{i:02}",
        }
        for i in range(count)
    ]


class SyntheticChart:
    def order(self):
        return ["company"]

    def name(self, _dept):
        return "Company"


def use_synthetic_items(monkeypatch, items):
    monkeypatch.setattr(TM, "collect", lambda _c, _settings, _at: ({"company": items}, SyntheticChart()))


# ----------------------------------------------------------------------------- the report
def test_an_empty_report_opens_no_task_and_wakes_nothing(c):
    _new(c, owner=BUILDER)  # a task its bot can work is the engine's, not the report's
    assert _report(c)["words"] == "Nothing to do"
    assert _checks(c) == []
    assert c.execute("SELECT count(*) FROM jobs WHERE bot_id=?", (TM.SLUG,)).fetchone()[0] == 0


def test_a_paused_task_opens_one_check_that_a_later_report_absorbs_and_a_private_task_never_appears(c):
    paused = _new(c)
    private = _new(c, SECRET, private=True)
    first = _report(c)
    assert first["items"] == 1 and first["words"] == "1 department, 1 item"
    (check,) = _checks(c)
    assert check["title"] == "Task check: Company (1)" and paused in check["body"]
    assert "Paused: " in check["body"] and "has no computer" in check["body"] and "tico task show" in check["body"]
    assert private not in check["body"] and SECRET not in check["body"]
    assert _report(c)["items"] == 0 and len(_checks(c)) == 1  # reported once: left out for a day, nothing new
    second = _new(c, "Chase the Acme renewal")
    assert _report(c)["items"] == 1
    (check,) = _checks(c)  # absorbed: the same task, a new body
    assert check["title"] == "Task check: Company (1)" and second in check["body"] and paused not in check["body"]
    H.task_done(c, TM.ACTOR, check["id"], "1 fixed, 0 left", _version(c, check["id"]))
    third = _new(c, "Send the Acme statements")
    _report(c)
    assert [t["status"] for t in _checks(c)] == ["done", "open"] and third in _checks(c)[1]["body"]


def test_a_snoozed_task_and_one_the_bot_can_work_are_not_listed(c):
    snoozed = _new(c)
    H.task_snooze(c, ANA, snoozed, "After the audit", None)
    _new(c, "Draft the Acme reply", owner=BUILDER)
    assert _report(c)["items"] == 0


def test_a_task_in_a_pipeline_with_another_active_manager_is_left_to_that_manager(c):
    X.bot(c, "lead-manager")
    X.pipeline(c, "leads", "Leads", manager="bot:lead-manager")
    _new(c, pipeline="leads")
    assert _report(c)["items"] == 0
    c.execute("UPDATE bots SET state='paused' WHERE id='lead-manager'")
    assert _report(c)["items"] == 1  # a manager that cannot act covers nothing: the General Manager does


def _twin(c, title):
    """Two open tasks Tico filed for the builder with one title (only Tico may file a second with a title in use)."""
    return tuple(_new(c, title, owner=BUILDER, by=H.KEEPER, deduplicate=False) for _ in range(2))


def test_two_open_tasks_with_one_title_requester_and_owner_are_a_duplicate_and_on_ice_ones_are_not(c):
    first, second = _twin(c, "Reconcile the Acme invoices")
    assert _report(c)["items"] == 1
    (check,) = _checks(c)
    assert f"same title, requester and owner as {first}" in check["body"]
    assert f"tico task relate {second} {first} --kind duplicate_of" in check["body"]
    third, _ = _twin(c, "Book the Acme review")
    H.task_snooze(c, H.KEEPER, third, "Not this quarter", None)
    assert _report(c)["items"] == 0


def test_byte_cap_keeps_whole_items_and_reserves_a_correct_remainder_line(c):
    items = synthetic_report_items(30, why_size=180, fix_size=180)
    body, included = TM.render(c, "Company", items[: TM.PER_DEPARTMENT], 0)
    assert len(body.encode()) <= TM.BODY_BYTES
    assert 0 < included < len(items)
    assert body.endswith(f"... and {len(items) - included} more, in the next report.")
    assert f"{included + 1}. synthetic-{included:02}" not in body
    assert f"{included}. synthetic-{included - 1:02}" in body
    assert body.count("   Fix: ") == included and body.count("   Fix: " + "f" * 180) == included


def test_cut_items_are_not_seen_and_appear_in_the_next_report(c, monkeypatch):
    items = synthetic_report_items(30)
    use_synthetic_items(monkeypatch, items)
    _report(c)
    first_body = _checks(c)[0]["body"]
    included = sum(f"synthetic-{i:02}" in first_body for i in range(len(items)))
    assert 0 < included < len(items)
    seen = {r["key"] for r in c.execute("SELECT key FROM task_manager_seen")}
    assert seen == {item["key"] for item in items[:included]}

    second = _report(c)
    second_body = _checks(c)[0]["body"]
    cut_ids = {item["task"] for item in items[included:]}
    assert second["items"] == len(cut_ids)
    assert all(task_id in second_body for task_id in cut_ids)


def test_render_is_unchanged_under_cap_and_report_keeps_the_thirty_item_cap(c, monkeypatch):
    items = synthetic_report_items(35, why_size=1, fix_size=1)
    use_synthetic_items(monkeypatch, items)
    _report(c)
    body = _checks(c)[0]["body"]
    assert "Task check for Company: 35 items." in body
    assert "... and 5 more, in the next report." in body
    assert all(f"synthetic-{i:02}" in body for i in range(30))
    assert all(f"synthetic-{i:02}" not in body for i in range(30, 35))


def test_with_the_general_manager_paused_the_report_opens_nothing(c):
    _new(c)
    c.execute("UPDATE bots SET state='paused' WHERE id=?", (TM.SLUG,))
    assert _report(c)["words"] == "The General Manager is off" and _checks(c) == []


# ----------------------------------------------------------------------------- the Settings card
CARD_TS = Path(__file__).resolve().parents[2] / "ui" / "app" / "settings-release.ts"

# Settings' General Manager card as the browser draws it: settings-release.ts with its types stripped. `ago` and `fmt`
# name the time they were given, so the test reads which field each row drew from.
CARD = r"""
const fs = require('fs'), vm = require('vm'), {stripTypeScriptTypes} = require('module');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const ctx = {console, S: {me: {role: 'member'}}, botPageView: () => null,
  esc: s => String(s ?? ''), ago: iso => `ago(${iso})`, fmt: iso => `fmt(${iso})`};
vm.createContext(ctx);
vm.runInContext(stripTypeScriptTypes(fs.readFileSync(input.file, 'utf8')), ctx);
ctx.view = input.view;
console.log(vm.runInContext('generalManagerHTML(view)', ctx));
"""


def _card_rows(tmp_path, view):
    """Each department row's name and its Checked text, from the page's own card for this view() payload."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node draws the page's own card")
    harness = tmp_path / "card.js"
    harness.write_text(CARD)
    run = subprocess.run(
        [node, str(harness)],
        input=json.dumps({"file": str(CARD_TS), "view": view}),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if "stripTypeScriptTypes is not a function" in run.stderr:
        pytest.skip("this node cannot strip TypeScript types")
    assert run.returncode == 0, run.stderr
    rows = re.findall(r'<strong>(.*?)</strong>\s*<span class="tnum[^"]*"[^>]*>(.*?)</span>', run.stdout)
    assert len(rows) == len(view["departments"]), run.stdout
    return rows


def test_the_settings_card_shows_when_each_department_was_last_checked(c, tmp_path):
    view = json.loads(json.dumps(TM.view(c, None)))  # what GET /api/v2/general-manager sends
    assert view["departments"] and all(d["checked_at"] is None for d in view["departments"])
    assert {text for _, text in _card_rows(tmp_path, view)} == {"Not checked"}
    _new(c)
    _report(c)
    view = json.loads(json.dumps(TM.view(c, None)))
    checked = [d["checked_at"] for d in view["departments"]]
    assert all(checked)
    assert [text for _, text in _card_rows(tmp_path, view)] == [f"ago({at})" for at in checked]
    c.execute("DELETE FROM task_manager_reports WHERE department=?", (view["departments"][0]["department"],))
    view = json.loads(json.dumps(TM.view(c, None)))
    rows = _card_rows(tmp_path, view)
    assert rows[0][1] == "Not checked" and [t for _, t in rows[1:]] == [f"ago({at})" for at in checked[1:]]


# ----------------------------------------------------------------------------- its rights
def test_it_closes_a_bots_request_with_a_note_but_not_a_persons_a_private_task_or_a_field_beyond_its_grant(c):
    asked_by_bot = _new(c, by=BUILDER)
    secret = _new(c, SECRET, by=BUILDER, private=True)
    persons = _new(c, "Pick the Acme venue")
    with pytest.raises(H.Refused, match="moves, notes and reassigns"):
        H.task_update(c, TM.ACTOR, asked_by_bot, title="Renamed", seen_version=_version(c, asked_by_bot))
    with pytest.raises(H.Refused):
        H.task_close(c, TM.ACTOR, secret, "Done long ago", _version(c, secret))
    with pytest.raises(H.Refused, match="task_why|say why"):
        H.task_close(c, TM.ACTOR, asked_by_bot, "", _version(c, asked_by_bot))  # never without saying why
    with pytest.raises(H.Refused) as refused:
        H.task_close(c, TM.ACTOR, persons, "Not needed", _version(c, persons))
    assert refused.value.rule == "task_needs_person"  # a person's request: it asks them instead
    closed = H.task_close(c, TM.ACTOR, asked_by_bot, "Done long ago", _version(c, asked_by_bot))
    assert closed["status"] == "closed"


def test_it_reassigns_only_within_the_owners_department(c):
    task = _new(c, owner=BUILDER)
    X.bot(c, "botops")
    BS.update(c, "botops", {"team": "sales"})
    groups = {"sales": {"name": "Sales", "parent": ""}, "support": {"name": "Support", "parent": ""}}
    c.execute(
        "INSERT OR REPLACE INTO registry_metadata VALUES('people', ?)",
        (json.dumps({"people": [], "org_groups": groups}),),
    )
    assert TM.same_department(c, BUILDER, BUILDER) and not TM.same_department(c, BUILDER, "bot:botops")
    with pytest.raises(H.Refused, match="department"):
        H.task_reassign(c, TM.ACTOR, task, "bot:botops", "Ops has room", _version(c, task))


# ----------------------------------------------------------------------------- the app
def built(environment):
    api = environment(seed={}, cards=BASE)
    shutil.copytree(TEMPLATE, Path(api.app.state.store.settings.catalog_dir) / "general-manager")
    machine(api)
    draft(api)
    api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    return api


def test_it_is_built_in_active_and_outside_the_org_chart(environment):
    api = built(environment)
    with api.app.state.store.read() as conn:
        assert H.bot(conn, TM.SLUG)["state"] == "active"
        assert api.app.state.auth.system_bot(TM.SLUG)
    assert "general-manager" in helper_templates(api.app.state.store.settings)


def test_the_business_hours_preset_runs_hourly_on_weekdays_only():
    zone = SR.zone()
    at = lambda day, hour: datetime(2026, 10, day, hour, 5, tzinfo=zone).astimezone(timezone.utc)  # Oct 23 is a Friday
    cfg = {"preset": SR.BUSINESS, "at": "09:00", "on": True}
    assert SR.due(cfg, at(23, 9), None) and not SR.due(cfg, at(23, 9), at(23, 9)) and SR.due(cfg, at(23, 10), at(23, 9))
    assert not SR.due(cfg, at(23, 19), None) and not SR.due(cfg, at(24, 10), None)  # after 18:00, and a Saturday
    after_close = SR.next_due(cfg, at(23, 17), at(23, 18))
    assert after_close.astimezone(zone).weekday() == 0 and after_close.astimezone(zone).hour == 8  # Monday 08:00


def test_the_switch_the_schedule_run_now_and_the_status_work_through_the_api(environment):
    api = built(environment)
    owner = signed_in  # a fresh Idempotency-Key per write
    status = api.get("/api/v2/general-manager", headers=owner()).json()
    assert status["on"] and status["routine"]["preset"] == "business"
    saved = api.put(
        "/api/v2/system-routines/task_manager_report", json={"preset": "daily", "at": "07:30"}, headers=owner()
    )
    assert saved.status_code == 200 and saved.json()["job"]["key"] == "task_manager_report"
    off = api.put("/api/v2/system-routines/task_manager_report", json={"on": False}, headers=owner())
    assert off.status_code == 200, off.text
    assert api.post("/api/v2/system-routines/task_manager_report/run", json={}, headers=owner()).status_code == 409
    api.put("/api/v2/system-routines/task_manager_report", json={"on": True, "preset": "business"}, headers=owner())
    assert api.post("/api/v2/system-routines/task_manager_report/run", json={}, headers=owner()).status_code == 200
    assert api.post("/api/v2/general-manager/turn-off", json={}, headers=owner()).status_code == 200
    with api.app.state.store.transaction() as conn:
        assert TM.report(conn, api.app.state.auth, api.app.state.store.settings, H.now())["words"] == (
            "The General Manager is off"
        )
    assert api.post("/api/v2/general-manager/turn-on", json={}, headers=owner()).status_code == 200
