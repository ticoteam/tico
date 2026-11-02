"""System routines 2/3 (backend/system_routines.py CONTROLS): on/off and a schedule preset for the six jobs that use AI or
reach people, Run now, who may change them (owner and admins; Health scan only the owner; enforced on the server), the
flat list's WHEN/THEN rows, department tag and search words, and the stale review and Topic changed switches moved from
Settings > General > Team. Routines spec tests 3-9 and 12, task-rules spec tests 1-3 (built-in rows). Ana owns the
team, Ben is an admin, Cara a member (backend/tests/test_api.py). Synthetic fixtures only."""

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend import learnings as L
from backend import stale_review
from backend import system_routines as SR
from backend import team_rules
from backend.scheduler import Scheduler, stamp
from backend.store import H
from backend.tests.test_api import api, get, post, put  # noqa: F401  (fixture)
from backend.tests.test_stale_review import FakeDecide

SIX = {"stale_review", "still_wanted", "kpi_asks", "learnings", "health_scan", "ask_deadlines", "task_manager_report",
       "engineering_exceptions", "bookkeeper_check", "memory_tidy"}   # six, the Task Manager's report, engineering exceptions, books


def rows(api, token="ana-test"):
    return {job["key"]: job for job in get(api, "system-routines", token=token)["jobs"]}


def test_admin_sees_a_switch_and_a_schedule_on_the_six_and_housekeeping_has_none(api):
    jobs = rows(api, "ben-test")
    timed = {k for k, j in jobs.items() if j["presets"]}
    assert timed == SIX | {"task-rule-close-after-done"}
    assert {k for k, j in jobs.items() if j["switch"]} == SIX | {"topic_fresh", "stall_triage", "task-rule-close-after-done"}
    for key in SIX - {"health_scan"}:
        assert jobs[key]["can_change"] is True, key
    for key, job in jobs.items():
        if job["needed"]:
            assert not job["switch"] and not job["presets"] and not job["can_change"], key
    assert jobs["stale_review"]["schedule"] == "Hourly" and jobs["still_wanted"]["schedule"] == "Daily at 09:00"
    assert jobs["learnings"]["on"] is True and jobs["learnings"]["schedule"] == "Nightly at 03:00"


def test_ai_jobs_are_never_offered_every_15_minutes_and_the_server_refuses_it(api):
    jobs = rows(api)
    for key in ("stale_review", "learnings"):
        assert "15m" not in [p["key"] for p in jobs[key]["presets"]], key
    assert [p["key"] for p in jobs["kpi_asks"]["presets"]] == ["15m", "hourly", "4h", "daily"]
    put(api, "system-routines/stale_review", {"preset": "15m"}, expected=422)
    put(api, "system-routines/learnings", {"preset": "hourly"}, expected=422)
    put(api, "system-routines/still_wanted", {"preset": "daily", "at": "25:00"}, expected=422)
    put(api, "system-routines/cleanup", {"on": False}, expected=404)        # housekeeping has no controls


def test_a_member_sees_the_list_with_nothing_to_change_and_the_server_refuses_them(api):
    jobs = rows(api, "cara-test")
    assert len(jobs) == len(SR.JOBS) + 1
    assert not any(j["can_change"] for j in jobs.values())
    put(api, "system-routines/stale_review", {"on": True}, token="cara-test", expected=403)
    put(api, "system-routines/kpi_asks", {"preset": "4h"}, token="cara-test", expected=403)
    post(api, "system-routines/kpi_asks/run", {}, token="cara-test", expected=403)
    post(api, "learnings/run", {}, token="cara-test", expected=403)      # the Learnings API's run is the same run


def test_health_scan_is_owner_only_an_admin_reads_it_and_the_owner_changes_it(api):
    assert rows(api, "ben-test")["health_scan"]["can_change"] is False
    assert rows(api, "ben-test")["health_scan"]["owner_only"] is True
    assert "Health Monitor is off" in rows(api)["health_scan"]["note"], "its switch alone does not make it find things"
    put(api, "system-routines/health_scan", {"on": False}, token="ben-test", expected=403)
    post(api, "system-routines/health_scan/run", {}, token="ben-test", expected=403)
    assert rows(api)["health_scan"]["can_change"] is True
    job = put(api, "system-routines/health_scan", {"preset": "4h"})["job"]
    assert job["schedule"] == "Every 4 hours"
    assert put(api, "system-routines/health_scan", {"on": False})["job"]["on"] is False
    assert rows(api)["health_scan"]["next"] is None, "off: no next run"


def test_stale_review_every_4_hours_spaces_its_passes_and_its_next_run_follows(api):
    store = api.app.state.store
    put(api, "system-routines/stale_review", {"on": True, "preset": "4h"}, token="ben-test")
    at = datetime.now(timezone.utc) - timedelta(minutes=30)
    with store.transaction() as c:
        stale_review._set_watermark(c, stamp(at))
        assert not stale_review.due(c, stamp(at + timedelta(hours=1)))
        assert not stale_review.due(c, stamp(at + timedelta(hours=3, minutes=59)))
        assert stale_review.due(c, stamp(at + timedelta(hours=4)))
        SR.save(c, SR.Run("stale_review", started=stamp(at)).did("3 checked"))
    with store.read() as c:
        row = SR.row_of(c, store.settings, "stale_review")
    assert row["schedule"] == "Every 4 hours"
    assert H.parse_ts(row["next"]) >= at + timedelta(hours=4) - timedelta(seconds=1)


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
    Scheduler(store, api.app.state.execution).tick(at + timedelta(minutes=30))     # a fresh process, half an hour on
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
    assert SR.next_due(cfg, morning + timedelta(minutes=1, seconds=5), morning + timedelta(hours=5)) == \
        datetime(2026, 10, 23, 9, 0, tzinfo=pt)


def test_nightly_learnings_has_an_off_switch_on_by_default_and_off_means_no_run_that_night(api, monkeypatch):
    store = api.app.state.store
    started = []
    monkeypatch.setattr(L, "start", lambda store, run_id: started.append(run_id))
    night = datetime(2026, 10, 22, 3, 30, tzinfo=SR.zone())
    server = SimpleNamespace(settings=SimpleNamespace(rehearsal=False, test_identities=None),
                             read=store.read, transaction=store.transaction)
    assert rows(api)["learnings"]["on"] is True
    put(api, "system-routines/learnings", {"on": False}, token="ben-test")
    assert L.nightly(server, night) is None and started == []
    post(api, "learnings/run", {}, token="ben-test", expected=409)        # the Learnings API's run obeys the switch too
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM learning_runs").fetchone()[0] == 0
    put(api, "system-routines/learnings", {"on": True, "at": "04:00"}, token="ben-test")
    assert L.nightly(server, night) is None, "before its chosen time"
    assert L.nightly(server, night + timedelta(minutes=31)) and len(started) == 1
    assert rows(api)["learnings"]["schedule"] == "Nightly at 04:00"
    with store.transaction() as c:
        c.execute("DELETE FROM learning_runs")                         # the route claims the real clock's night
    post(api, "learnings/run", {}, token="ben-test")                     # an admin starts it, as in Settings > Routines
    assert len(started) == 2
    following = H.parse_ts(rows(api)["learnings"]["next"])
    assert following > datetime.now(timezone.utc) + timedelta(minutes=1), "tonight has run: the next is tomorrow's"
    noon = datetime(2026, 1, 15, 12, 0, tzinfo=SR.zone())          # a night no other line claims
    with store.transaction() as c:
        c.execute("INSERT INTO learning_runs(id,night,status,started_at) VALUES('u',?,'unconfigured',?)",
                  (L.night_of(noon), H.now()))
        assert SR.next_night(c, {"at": "04:00"}, noon) == noon.replace(hour=4) + timedelta(days=1), \
            "an unconfigured night is not tried again before tomorrow"


def test_run_now_on_stale_review_makes_at_most_30_decide_calls_and_the_row_shows_the_result(api, monkeypatch):
    store = api.app.state.store
    put(api, "system-routines/stale_review", {"on": True}, token="ben-test")
    model = FakeDecide("leave")
    monkeypatch.setattr(stale_review, "server_engine", lambda *a, **k: model)
    with store.transaction() as c:
        for n in range(35):
            row = H.task_create(c, "human:ana", f"Old export task {n}", "x", "bot:ops")
            when = H.shift(H.now(), days=-10)
            c.execute("UPDATE tasks SET status='open',updated_at=?,created_at=? WHERE id=?", (when, when, row["id"]))
        stale_review._set_watermark(c, H.now())          # a pass just ran: Run now runs anyway

    def run_once():
        before = (rows(api)["stale_review"]["last"] or {}).get("started_at")
        assert post(api, "system-routines/stale_review/run", {}, token="ben-test")["running"] is True
        for _ in range(200):
            last = rows(api)["stale_review"]["last"]
            if not stale_review._RUNNING.locked() and last and last["started_at"] != before:
                return last
            time.sleep(0.05)
        raise AssertionError("the run never finished")
    last = run_once()
    assert len(model.calls) == stale_review.CALLS_PER_HOUR == 30, "the hourly cap bites a manual run too"
    assert last["ok"] and "checked" in last["result"], last
    run_once()                                           # a second Run now in the same hour gets what is left: none
    assert len(model.calls) == 30


def test_run_now_on_a_plain_job_lands_its_result_on_the_row_and_is_refused_while_off(api):
    reply = post(api, "system-routines/ask_deadlines/run", {}, token="ben-test")
    assert reply["running"] is False
    assert reply["job"]["last"]["result"] == "No asks past their deadline"
    put(api, "system-routines/still_wanted", {"on": False}, token="ben-test")
    post(api, "system-routines/still_wanted/run", {}, token="ben-test", expected=409)
    post(api, "system-routines/topic_fresh/run", {}, token="ben-test", expected=422)


def test_moved_switches_keep_their_values_and_write_the_same_team_rules(api, monkeypatch):
    store = api.app.state.store
    jobs = rows(api)
    assert jobs["stale_review"]["on"] is False and jobs["topic_fresh"]["on"] is False   # off by default elsewhere
    monkeypatch.setenv("TICO_STALE_REVIEW", "1")                                       # Tico Team's own server
    assert rows(api)["stale_review"]["on"] is True
    monkeypatch.delenv("TICO_STALE_REVIEW")
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"topic_fresh": True})                          # set in General > Team before
    assert rows(api)["topic_fresh"]["on"] is True
    assert put(api, "system-routines/stale_review", {"on": True}, token="ben-test")["job"]["on"] is True
    assert put(api, "system-routines/topic_fresh", {"on": False}, token="ben-test")["job"]["on"] is False
    with store.read() as c:
        rules = team_rules.load(c)
    assert rules["stale_review"] is True and rules["topic_fresh"] is False
    put(api, "system-routines/topic_fresh", {"preset": "hourly"}, token="ben-test", expected=422)


def test_every_row_reads_when_then_with_one_department_from_the_chart_and_search_finds_done_and_close(api):
    store = api.app.state.store
    with store.transaction() as c:
        row = c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()
        roster = {**(json.loads(row[0]) if row else {}), "org_groups": {
            "engineering": {"name": "Engineering"}, "marketing": {"name": "Marketing"},
            "web": {"name": "Web", "parent": "engineering"}}}
        c.execute("INSERT INTO registry_metadata VALUES('people',?) ON CONFLICT(key) DO UPDATE SET "
                  "value=excluded.value", (json.dumps(roster),))
    data = get(api, "system-routines")
    assert {"Engineering", "Marketing"} <= set(data["departments"])
    assert "Web" not in data["departments"], "only fixed departments, never arbitrary subgroups"
    for job in data["jobs"]:
        assert job["when"] and job["then"], job["key"]
        assert job["department"]["name"] == "Engineering" and job["department"]["in_chart"], job["key"]
    jobs = {j["key"]: j for j in data["jobs"]}
    assert jobs["stale_review"]["when"] == ["every hour"]
    assert jobs["stale_review"]["then"][0] == "check stale tickets"
    assert jobs["topic_fresh"]["when"] == ["a message in a direct chat changes the topic"]
    done = {k for k, j in jobs.items() if "done" in j["search"]}
    assert {"stale_review", "approval_expiry"} <= done
    assert "close" in jobs["stale_review"]["search"] and "remind" in jobs["due_reminders"]["search"]
    assert "engineering" in jobs["cleanup"]["search"] and "housekeeping" in jobs["cleanup"]["search"]
