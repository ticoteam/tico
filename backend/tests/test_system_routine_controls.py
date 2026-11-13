"""System routines 2/3 (backend/system_routines.py CONTROLS): on/off and a schedule preset for the timed jobs (those that use
AI or reach people, the books, and the nightly auto dream), Run now, who may change them (owner and admins; enforced on the server), the
flat list's WHEN/THEN rows, department tag and search words, and the Topic changed switch moved from
Settings > General > Team. Routines spec tests 3-9 and 12, task-rules spec tests 1-3 (built-in rows). Ana owns the
team, Ben is an admin, Cara a member (backend/tests/helpers/api_app.py). Synthetic fixtures only."""

import json
from datetime import datetime, timedelta, timezone

from backend import system_routines as SR
from backend import team_rules
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.helpers.api import get, post, put  # noqa: F401  (fixture)

TIMED = {
    "kpi_asks",
    "task_manager_report",
    "bookkeeper_check",
}  # the jobs with a schedule preset


def rows(api, token="ana-test"):
    return {job["key"]: job for job in get(api, "system-routines", token=token)["jobs"]}


def hidden(api):
    """The jobs that cannot run on this install, which the list leaves out (Job.applies): with no Bookkeeper,
    Books check."""
    store = api.app.state.store
    with store.read() as c:
        return SR.not_applying(c, store.settings)


def test_admin_sees_a_switch_and_a_schedule_on_the_timed_jobs_and_housekeeping_has_none(api):
    jobs = rows(api, "ben-test")
    here = TIMED - hidden(api)
    timed = {k for k, j in jobs.items() if j["presets"]}
    assert timed == here
    assert {k for k, j in jobs.items() if j["switch"]} == here | {"topic_fresh"}
    for key in here:
        assert jobs[key]["can_change"] is True, key
    for key, job in jobs.items():
        if job["needed"]:
            assert not job["switch"] and not job["presets"] and not job["can_change"], key
    assert jobs["kpi_asks"]["schedule"] == "Hourly" and "stale_review" not in jobs
    assert "learnings" not in jobs


def test_a_job_offers_only_its_own_presets_and_the_server_refuses_anything_else(api):
    jobs = rows(api)
    assert [p["key"] for p in jobs["kpi_asks"]["presets"]] == ["15m", "hourly", "4h", "daily"]
    put(api, "system-routines/kpi_asks", {"preset": "weekly"}, expected=422)
    put(api, "system-routines/learnings", {"preset": "hourly"}, expected=404)
    put(api, "system-routines/kpi_asks", {"preset": "daily", "at": "25:00"}, expected=422)
    put(api, "system-routines/cleanup", {"on": False}, expected=404)  # housekeeping has no controls


def test_a_member_sees_the_list_with_nothing_to_change_and_the_server_refuses_them(api):
    jobs = rows(api, "cara-test")
    assert len(jobs) == len(SR.JOBS) - len(hidden(api))
    assert not any(j["can_change"] for j in jobs.values())
    put(api, "system-routines/kpi_asks", {"on": True}, token="cara-test", expected=403)
    put(api, "system-routines/kpi_asks", {"preset": "4h"}, token="cara-test", expected=403)
    post(api, "system-routines/kpi_asks/run", {}, token="cara-test", expected=403)
    post(api, "learnings/run", {}, token="cara-test", expected=403)  # the Learnings API's run is the same run


def test_health_scan_is_replaced_by_the_maintenance_step(api):
    for token in ("ana-test", "ben-test", "cara-test"):
        assert "health_scan" not in rows(api, token)
        put(api, "system-routines/health_scan", {"on": True}, token=token, expected=404)
        post(api, "system-routines/health_scan/run", {}, token=token, expected=404)


def test_an_old_saved_health_scan_schedule_cannot_bypass_paused_maintenance(api, monkeypatch):
    from backend import maintenance, maintenance_health

    store = api.app.state.store
    scans = []
    monkeypatch.setattr(maintenance_health, "check", lambda *args, **kwargs: scans.append(True) or {})
    with store.transaction() as c:
        c.execute(
            "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (SR.CONFIG_KEY, json.dumps({"health_scan": {"on": True, "preset": "15m", "at": "09:00"}})),
        )
        maintenance.save_settings(c, "human:ana", paused=True)
    scheduler = Scheduler(store, api.app.state.execution)
    at = datetime.now(timezone.utc)
    scheduler.tick(at)
    scheduler.tick(at + timedelta(hours=1))
    assert scans == []
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM system_runs WHERE job='health_scan'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM maintenance_runs").fetchone()[0] == 0


def test_a_plain_job_runs_on_its_saved_preset_not_hourly(api):
    store = api.app.state.store
    put(api, "system-routines/kpi_asks", {"preset": "4h"}, token="ben-test")
    scheduler = Scheduler(store, api.app.state.execution)
    at = datetime.now(timezone.utc)

    def runs():
        with store.read() as c:
            return c.execute("SELECT count(*) FROM system_runs WHERE job='kpi_asks'").fetchone()[0]

    scheduler.tick(at)
    assert runs() == 1
    for hours in (1, 2, 3):
        scheduler.tick(at + timedelta(hours=hours))
    assert runs() == 1, "nothing between its 4-hour runs"
    scheduler.tick(at + timedelta(hours=4, seconds=1))
    assert runs() == 2
    put(api, "system-routines/kpi_asks", {"on": False}, token="ben-test")
    scheduler.tick(at + timedelta(hours=9))
    assert runs() == 2, "off: no run"


def test_a_restarted_scheduler_reads_the_last_run_and_does_not_run_early(api):
    store = api.app.state.store
    at = datetime.now(timezone.utc)
    Scheduler(store, api.app.state.execution).tick(at)
    Scheduler(store, api.app.state.execution).tick(at + timedelta(minutes=30))  # a fresh process, half an hour on
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM system_runs WHERE job='kpi_asks'").fetchone()[0] == 1


def test_daily_at_a_time_runs_once_a_day_after_that_time():
    cfg = {"on": True, "preset": "daily", "at": "09:00"}
    pt = SR.zone()
    morning = datetime(2026, 10, 22, 8, 59, tzinfo=pt)
    ran = datetime(2026, 10, 21, 9, 0, 5, tzinfo=pt)
    assert not SR.due(cfg, morning, ran)
    assert SR.due(cfg, morning + timedelta(minutes=1), ran)
    assert not SR.due(cfg, morning + timedelta(hours=5), morning + timedelta(minutes=1, seconds=5))
    assert SR.next_due(cfg, morning + timedelta(minutes=1, seconds=5), morning + timedelta(hours=5)) == datetime(
        2026, 10, 23, 9, 0, tzinfo=pt
    )


def test_three_daily_presets_anchor_to_the_next_company_time():
    # Three times a day, at 6am, noon and 6pm company time (Pacific: PST on 3 November 2026, UTC-8).
    cfg = {"on": True, "preset": SR.THREE_DAILY, "at": "06:00"}
    pt = SR.zone()
    assert SR.next_due(cfg, None, datetime(2026, 11, 3, 3, tzinfo=pt)) == datetime(2026, 11, 3, 6, tzinfo=pt)
    assert SR.next_due(cfg, None, datetime(2026, 11, 3, 7, tzinfo=pt)) == datetime(2026, 11, 3, 12, tzinfo=pt)
    assert SR.next_due(cfg, None, datetime(2026, 11, 3, 19, tzinfo=pt)) == datetime(2026, 11, 4, 6, tzinfo=pt)
    assert SR.next_due(cfg, None, datetime(2026, 11, 3, 6, tzinfo=pt)) == datetime(2026, 11, 3, 12, tzinfo=pt)


def test_learnings_control_is_retired_and_only_maintenance_changes_its_switch(api):
    assert "learnings" not in rows(api)
    put(api, "system-routines/learnings", {"on": True}, token="ben-test", expected=404)
    put(api, "system-routines/learnings", {"at": "04:00"}, expected=404)
    steps = {row["key"]: row for row in get(api, "maintenance")["steps"]}
    assert steps["learn"]["enabled"] is False


def test_run_now_on_a_plain_job_lands_its_result_on_the_row_and_is_refused_while_off(api):
    reply = post(api, "system-routines/kpi_asks/run", {}, token="ben-test")
    assert reply["running"] is False
    assert reply["job"]["last"]["result"] == "Nothing to ask"
    assert reply["job"]["switch"] is True and reply["job"]["on"] is True
    put(api, "system-routines/kpi_asks", {"on": False}, token="ben-test")
    post(api, "system-routines/kpi_asks/run", {}, token="ben-test", expected=409)
    post(api, "system-routines/topic_fresh/run", {}, token="ben-test", expected=422)


def test_moved_switches_keep_their_values_and_write_the_same_team_rules(api, monkeypatch):
    store = api.app.state.store
    jobs = rows(api)
    assert jobs["topic_fresh"]["on"] is False  # off by default
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"topic_fresh": True})  # set in General > Team before
    assert rows(api)["topic_fresh"]["on"] is True
    assert put(api, "system-routines/topic_fresh", {"on": False}, token="ben-test")["job"]["on"] is False
    with store.read() as c:
        rules = team_rules.load(c)
    assert rules["topic_fresh"] is False
    put(api, "system-routines/topic_fresh", {"preset": "hourly"}, token="ben-test", expected=422)
