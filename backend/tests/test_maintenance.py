"""Maintenance foundation (docs/maintenance.md; plan 1-3 and Amendment v2 A, B, G, H, I): the fixed step order, each
"How often" on its own clock, Run now and the schedule as one run, the abandoned-run recovery, cursors that move only on
`ran`, a failing step that does not stop the run, the switch migration, the wake limiter and the API rights.
Synthetic fixtures: the api fixture's people (backend/tests/test_api.py; Ana owns the company, Ben is an admin, Cara is a
member)."""

import importlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from backend import hubdb as H
from backend import maintenance as M
from backend import maintenance_limits as L
from backend.maintenance_steps import collect
from backend.tests.test_api import api, get, headers  # noqa: F401  (fixtures and helpers)

mig = importlib.import_module("backend.migrations.0077_maintenance")


def la(day, hour, minute=0):
    """A company-time (Los Angeles) moment in November 2026."""
    return datetime(2026, 11, day, hour, minute, tzinfo=M.ZONE)


def result(status, reason=""):
    return {"status": status, "reason": reason, "checked": 0, "found": 0, "woke": 0, "cost_usd": 0.0, "details": {}}


def stores(api):
    return api.app.state.store, api.app.state.auth


def patch(api, path, body, token="ana-test", expected=200):
    r = api.patch("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def cursor(store, key):
    with store.read() as c:
        return c.execute("SELECT cursor_at FROM maintenance_steps WHERE key=?", (key,)).fetchone()[0]


def inline(store, auth, run_id, only, wait):
    M._execute(store, auth, run_id, only)


def test_the_steps_run_in_one_fixed_order_and_the_api_lists_them_so(api):
    assert M.ORDER == ("collect", "clean_up", "check_health", "find_stuck", "learn", "suggest", "update", "dream")
    assert [s["key"] for s in get(api, "maintenance")["steps"]] == list(M.ORDER)


def test_each_frequency_is_due_on_its_own_clock():
    def row(frequency, last=None, every=None, unit=None):
        return {"frequency": frequency, "last_started_at": M._stamp(last) if last else None,
                "custom_every": every, "custom_unit": unit}

    assert M.due(row("run", la(4, 9)), la(4, 9, 5)), "every run is due on each run"
    assert M.due(row("day"), la(4, 10)), "a step that never started is due"
    assert not M.due(row("day", la(4, 6)), la(4, 10)), "a day's run started after today's 05:00 is not due again"
    assert not M.due(row("day", la(4, 6)), la(5, 4, 30)), "04:30 is still yesterday's day"
    assert M.due(row("day", la(4, 6)), la(5, 5, 30)), "after 05:00 the day is new"
    old = datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)      # absolute time: the clocks changed on Nov 1
    assert not M.due(row("week", old), old + timedelta(days=7) - timedelta(minutes=1)), "six days and a bit is not a week"
    assert M.due(row("week", old), old + timedelta(days=7)), "seven days after the last attempt is due"
    assert not M.due(row("custom", la(4, 6), 3, "hours"), la(4, 8)), "every 3 hours: two hours is not enough"
    assert M.due(row("custom", la(4, 6), 3, "hours"), la(4, 9)), "every 3 hours: three hours is due"
    assert not M.due(row("custom", la(2, 6), 2, "days"), la(3, 6)), "every 2 days: one day is not enough"
    assert M.due(row("custom", la(2, 6), 2, "days"), la(4, 6)), "every 2 days: two days is due"


def test_the_schedule_buckets_and_the_next_run():
    assert M.bucket("hourly", la(4, 10, 40)) == la(4, 10)
    assert M.bucket("3h", la(4, 10)) == la(4, 9), "3-hour buckets start at 00, 03, 06, ..."
    assert M.bucket("daily", la(4, 4)) == la(3, 5), "before 05:00 company time is still yesterday's day"
    assert M.bucket("daily", la(4, 6)) == la(4, 5)
    assert M.next_bucket("2h", la(4, 7)) == la(4, 8)
    assert "weekly" not in M.SCHEDULES and "week" not in M.SCHEDULES, "there is no once-a-week schedule"


def test_run_now_runs_every_step_in_order_like_a_scheduled_run(api):
    store, auth = stores(api)
    run, started = M.run_now(store, auth, "human:ana", wait=True)
    assert started and run["trigger"] == "run_now"
    assert [s["step"] for s in run["steps"]] == list(M.ORDER), "one log row per step, in order"
    steps = {s["step"]: s for s in run["steps"]}
    assert steps["collect"]["status"] == "ran"
    assert steps["learn"]["status"] == "off", "learn is off by default"
    assert steps["check_health"]["status"] == "skipped" and steps["check_health"]["reason"] == "on its old timer"
    assert steps["suggest"]["reason"] == "on its old timer"
    assert steps["dream"]["status"] in ("ran", "skipped")
    assert run["finished_at"] and run["ok"] == 1, run


def test_the_schedule_starts_one_run_per_bucket(api, monkeypatch):
    store, auth = stores(api)
    monkeypatch.setattr(M, "_start", inline)
    monkeypatch.setitem(M.LIVE, "dream", lambda c, ctx: result("skipped", "test"))
    now = datetime.now(timezone.utc)
    assert M.tick(store, auth, now) is not None
    assert M.tick(store, auth, now) is None, "the same bucket starts no second run"
    assert M.tick(store, auth, now + timedelta(hours=2)) is not None, "the next bucket starts its run"


def test_run_now_and_the_schedule_make_one_run(api, monkeypatch):
    store, auth = stores(api)
    monkeypatch.setattr(M, "_start", lambda *args, **kwargs: None)     # nothing executes: the run stays live
    with store.read() as c:
        before = c.execute("SELECT count(*) FROM maintenance_runs").fetchone()[0]
    first, started = M.run_now(store, auth, "human:ana")
    assert started
    assert M.tick(store, auth, datetime.now(timezone.utc)) is None, "the schedule sees the live run and starts none"
    again, started_again = M.run_now(store, auth, "human:ana")
    assert not started_again and again["id"] == first["id"], "a second Run now returns the live run"
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM maintenance_runs").fetchone()[0] == before + 1


def test_an_abandoned_run_is_recovered_and_its_occurrence_can_run_again(api):
    store, auth = stores(api)
    old = H.shift(H.now(), hours=-2)
    with store.transaction() as c:
        c.execute("INSERT INTO maintenance_runs(id,occurrence_key,trigger,step,started_at,heartbeat_at,result) "
                  "VALUES('dead','schedule:old','schedule',NULL,?,?,'')", (old, old))
        c.execute("INSERT INTO maintenance_run_steps(id,maintenance_run_id,step,status,reason,started_at,details) "
                  "VALUES('deadstep','dead','collect','pending','',?,'{}')", (old,))
    run, started = M.run_now(store, auth, "human:ana", step="collect", wait=True)
    assert started, "the abandoned run does not block a new one"
    with store.read() as c:
        dead = c.execute("SELECT finished_at, ok, result, occurrence_key FROM maintenance_runs WHERE id='dead'").fetchone()
        step = c.execute("SELECT status, reason FROM maintenance_run_steps WHERE id='deadstep'").fetchone()
    assert dead["finished_at"] and dead["ok"] == 0 and dead["result"] == "abandoned", dict(dead)
    assert dead["occurrence_key"].startswith("schedule:old:abandoned:"), "its key is freed for the schedule to claim"
    assert step["status"] == "failed" and step["reason"] == "abandoned", dict(step)


def test_a_cursor_moves_only_when_its_step_ran(api, monkeypatch):
    store, auth = stores(api)
    monkeypatch.setitem(M.LIVE, "collect", lambda c, ctx: result("skipped", "nothing new"))
    M.run_now(store, auth, "human:ana", step="collect", wait=True)
    assert cursor(store, "collect") is None, "skipped leaves the cursor"
    monkeypatch.setitem(M.LIVE, "collect", lambda c, ctx: result("failed", "boom"))
    M.run_now(store, auth, "human:ana", step="collect", wait=True)
    assert cursor(store, "collect") is None, "failed leaves the cursor"
    monkeypatch.setitem(M.LIVE, "collect", lambda c, ctx: result("ran", "did work"))
    M.run_now(store, auth, "human:ana", step="collect", wait=True)
    assert cursor(store, "collect") is not None, "ran moves the cursor"


def test_a_failing_step_is_recorded_and_the_run_goes_on(api, monkeypatch):
    store, auth = stores(api)

    def boom(c, ctx):
        raise RuntimeError("a bad row")
    monkeypatch.setitem(M.LIVE, "collect", boom)
    monkeypatch.setitem(M.LIVE, "clean_up", lambda c, ctx: result("ran", "pruned"))
    monkeypatch.setitem(M.LIVE, "dream", lambda c, ctx: result("skipped", "test"))
    run, _ = M.run_now(store, auth, "human:ana", wait=True)
    steps = {s["step"]: s for s in run["steps"]}
    assert steps["collect"]["status"] == "failed" and steps["collect"]["reason"] == "RuntimeError"
    assert steps["clean_up"]["status"] == "ran", "the run goes on past a failed step"
    assert run["ok"] == 0 and run["finished_at"] and "failed" in run["result"], run


def test_the_switch_migration_keeps_an_explicit_choice_and_gives_an_inherited_one_the_new_default():
    assert mig.switches({}) == {}, "nothing chosen: every step takes its default, and Learn's default is off"
    assert mig.switches({"learnings": {"on": False}}) == {"learn": False}
    assert mig.switches({"learnings": {"on": True}}) == {"learn": True}
    assert mig.switches({"auto_dream": {"on": False}, "health_scan": {"on": True}}) == {
        "dream": False, "check_health": True}
    assert mig.switches({"task_roundup": {"on": False}}) == {"find_stuck": False}
    assert mig.switches({"task_roundup": {"on": True}}) == {"find_stuck": True}
    assert mig.switches({"stall_triage": {"on": False}}) == {}, "stall_triage is gone"


def _migrated(registry, rules):
    """The step switches after the migration runs on a bare database holding these two registry rows."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT)")
    conn.executemany("INSERT INTO registry_metadata VALUES(?,?)",
                     [("system_routines", json.dumps(registry)), ("rules", json.dumps(rules))])
    mig.up(conn)
    return dict(conn.execute("SELECT key, enabled FROM maintenance_steps").fetchall())


def test_only_an_explicit_roundup_switch_sets_find_stuck_and_stale_review_never_does(monkeypatch):
    """Stale review defaults off, so a saved off stale review, or TICO_STALE_REVIEW=0, must not turn Find stuck off."""
    monkeypatch.setenv("TICO_STALE_REVIEW", "0")
    assert _migrated({}, {"stale_review": False})["find_stuck"] == 1, "Find stuck keeps its default (on)"
    assert _migrated({}, {})["find_stuck"] == 1
    assert _migrated({}, {"stale_review": True})["find_stuck"] == 1
    assert _migrated({"task_roundup": {"on": False}}, {"stale_review": True})["find_stuck"] == 0
    assert _migrated({"task_roundup": {"on": True}}, {"stale_review": False})["find_stuck"] == 1
    assert _migrated({"learnings": {"on": True}}, {})["learn"] == 1, "a saved Learn switch carries over"


def test_replaying_the_migration_changes_nothing(api):
    store = api.app.state.store

    def rows():
        with store.read() as c:
            return [tuple(r) for r in c.execute("SELECT key,enabled,frequency,max_bots,updated_by_id FROM maintenance_steps "
                                                "ORDER BY key")]
    before = rows()
    with store.transaction() as c:
        mig.up(c)
    assert rows() == before


def test_the_limiter_allows_one_wake_per_bot_per_run_three_per_task_a_day_and_fifty_an_hour(api):
    store = api.app.state.store
    with store.transaction() as c:
        assert L.allow(c, "bot_a", "task_1")
        L.record(c, "bot_a", ("task_1",))
        assert not L.allow(c, "bot_a", "task_2"), "one Maintenance wake per bot per run"
        assert L.allow(c, "bot_b", "task_1")
        L.record(c, "bot_b", ("task_1",))
        L.record(c, "bot_c", ("task_1",))
        assert not L.allow(c, "bot_d", "task_1"), "three wakes per task a day"
        assert L.allow(c, "bot_d"), "a wake with no task is not counted against a task"
        for i in range(47):
            L.record(c, f"fleet_{i}")
        assert not L.allow(c, "bot_new"), "fifty wakes across the fleet an hour"


def test_a_wake_that_names_a_list_of_tasks_is_one_fleet_wake_and_one_per_task(api):
    store = api.app.state.store
    day_ago, hour_ago = H.shift(H.now(), days=-1), H.shift(H.now(), hours=-1)
    tasks = [f"list_task_{n}" for n in range(10)]
    with store.transaction() as c:
        L.record(c, "bot_list", tasks)
        assert c.execute(L.COUNT_FLEET, (H.KEEPER, L.BOT_ACTION, hour_ago)).fetchone()[0] == 1, \
            "ten tasks in one list are one wake toward the fleet cap"
        assert all(c.execute(L.COUNT_TARGET, (H.KEEPER, L.TASK_ACTION, task, day_ago)).fetchone()[0] == 1
                   for task in tasks), "each task counts its one wake"
        assert not L.allow(c, "bot_list"), "the bot's one wake for this run is used"
        L.record(c, "bot_dup", ["dup_task", "dup_task"])
        assert c.execute(L.COUNT_TARGET, (H.KEEPER, L.TASK_ACTION, "dup_task", day_ago)).fetchone()[0] == 1, \
            "a task named twice in one wake counts once"
        L.record(c, "bot_more", ["list_task_0"])
        L.record(c, "bot_more_2", ["list_task_0"])
        assert not L.allow(c, "bot_three", "list_task_0"), "three wakes per task a day, across lists"
        assert L.allow(c, "bot_three", "list_task_1"), "the other tasks are not used up"


def test_only_an_owner_or_an_admin_changes_maintenance_and_everyone_reads_it(api, monkeypatch):
    store, auth = stores(api)
    monkeypatch.setattr(M, "_start", lambda *args, **kwargs: None)
    assert get(api, "maintenance", token="cara-test")["settings"]["schedule"] == "hourly"
    assert get(api, "maintenance", token="cara-test")["can_change"] is False
    assert get(api, "maintenance", token="ana-test")["can_change"] is True
    patch(api, "maintenance", {"paused": True}, token="cara-test", expected=403)
    assert api.post("/api/v2/maintenance/run", json={}, headers=headers("cara-test")).status_code == 403
    patch(api, "maintenance", {"paused": True}, token="ben-test")
    assert get(api, "maintenance", token="ana-test")["settings"]["paused"] is True


def test_the_api_saves_the_schedule_cap_and_steps_and_refuses_bad_ones(api, monkeypatch):
    monkeypatch.setattr(M, "_start", lambda *args, **kwargs: None)
    body = patch(api, "maintenance", {"schedule": "2h", "monthly_cap_usd": 25, "steps": [
        {"key": "learn", "enabled": True, "frequency": "custom", "custom_every": 3, "custom_unit": "hours"},
        {"key": "dream", "max_bots": 5}]})
    assert body["settings"]["schedule"] == "2h"
    assert body["settings"]["monthly_cap_usd_in_effect"] == 25.0
    steps = {s["key"]: s for s in body["steps"]}
    assert steps["learn"]["enabled"] is True and steps["learn"]["frequency"] == "custom"
    assert steps["learn"]["custom_every"] == 3 and steps["learn"]["custom_unit"] == "hours"
    assert steps["dream"]["max_bots"] == 5
    patch(api, "maintenance", {"steps": [{"key": "find_stuck", "frequency": "day"}]}, expected=422)
    # suggest has no custom interval saved (learn kept the 3 hours above, so a frequency-only change to it is valid)
    patch(api, "maintenance", {"steps": [{"key": "suggest", "frequency": "custom"}]}, expected=422)
    patch(api, "maintenance", {"steps": [{"key": "learn", "max_bots": 2}]}, expected=422)
    patch(api, "maintenance", {"schedule": "weekly"}, expected=422)
    run = api.post("/api/v2/maintenance/run", json={"step": "learn"}, headers=headers("ana-test")).json()
    assert run["started"] is True and run["run"]["step"] == "learn"
    listed = get(api, "maintenance/runs")
    assert "runs" in listed and "next" in listed


def test_collect_counts_a_bot_only_when_a_settled_turn_wrote_something(api):
    """Update's zero-work skip reads Collect. A "nothing new" wake that committed memory and posted a reply wrote nothing
    Tico counts, so its bot does not count as having done work."""
    store, _ = stores(api)
    during = "2026-11-03T12:30:00.000000Z"
    with store.transaction() as c:
        c.execute("INSERT INTO attempts(id,bot_id,created_at,settled_at,did_nothing,outcome,state) "
                  "VALUES('att-quiet','quiet',?,?,1,'nothing_to_do','completed')", (during, during))
        c.execute("INSERT INTO attempts(id,bot_id,created_at,settled_at,did_nothing,outcome,state) "
                  "VALUES('att-busy','busy',?,?,0,'acted','completed')", (during, during))
        # a failed attempt does not count, even with a write
        c.execute("INSERT INTO attempts(id,bot_id,created_at,settled_at,did_nothing,outcome,state) "
                  "VALUES('att-failed','broken',?,?,0,'acted','failed')", (during, during))
        c.execute("INSERT INTO bot_memory_updates(bot,sha,subject,author,committed_at,files,runner_id,reported_at) "
                  "VALUES('quiet','abc123','memory','quiet',?,'[]','runner',?)", (during, during))
        c.execute("INSERT INTO messages(id,sender_id,kind,body,created_at) "
                  "VALUES('msg-quiet','bot:quiet','say','Nothing new.',?)", (during,))
    with store.read() as c:
        counted = collect.did_work(c, "2026-11-03T12:00:00.000000Z", "2026-11-03T13:00:00.000000Z")
    assert counted == {"busy"}, "only the turn that wrote something counts"


def test_the_limiter_counts_read_the_events_index(api):
    """Every limiter count is by actor (Keeper), so each one reads events_task_origin instead of scanning events."""
    store, _ = stores(api)
    since = "2026-11-03T00:00:00.000000Z"
    with store.read() as c:
        for sql, params in ((L.COUNT_TARGET, (H.KEEPER, L.BOT_ACTION, "bot", since)),
                            (L.COUNT_FLEET, (H.KEEPER, L.BOT_ACTION, since))):
            plan = " ".join(str(row[3]) for row in c.execute("EXPLAIN QUERY PLAN " + sql, params))
            assert "events_task_origin" in plan, plan


def _dream_started_now(store):
    """Dream ran a moment ago, so on its own clock it is not due."""
    with store.transaction() as c:
        c.execute("UPDATE maintenance_steps SET last_started_at=? WHERE key='dream'",
                  (M._stamp(datetime.now(timezone.utc)),))


def test_a_step_only_run_now_runs_that_step_even_when_it_is_not_due(api, monkeypatch):
    store, auth = stores(api)
    monkeypatch.setitem(M.LIVE, "dream", lambda c, ctx: result("ran", "stubbed"))
    _dream_started_now(store)
    run, started = M.run_now(store, auth, "human:ana", step="dream", wait=True)
    assert started and [s["step"] for s in run["steps"]] == ["dream"]
    assert run["steps"][0]["status"] == "ran" and run["steps"][0]["reason"] == "stubbed", "not due, but Run now runs it"
    run, _ = M.run_now(store, auth, "human:ana", step="learn", wait=True)
    assert run["steps"][0]["status"] == "off", "a step switched off stays off"
    run, _ = M.run_now(store, auth, "human:ana", step="suggest", wait=True)
    assert run["steps"][0]["status"] == "skipped" and run["steps"][0]["reason"] == "on its old timer"


def test_a_plain_run_now_keeps_the_due_rules(api, monkeypatch):
    store, auth = stores(api)
    monkeypatch.setitem(M.LIVE, "dream", lambda c, ctx: result("ran", "stubbed"))
    _dream_started_now(store)
    run, started = M.run_now(store, auth, "human:ana", wait=True)
    steps = {s["step"]: s for s in run["steps"]}
    assert started and steps["dream"]["status"] == "skipped" and steps["dream"]["reason"] == "not due"
