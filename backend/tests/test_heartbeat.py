"""The Heartbeat (backend/heartbeat.py, docs/heartbeat.md): one short beat runs, in order, the steps that must happen
within seconds; each step is timed and on its own, a beat is stored small (the beat's state row; a beat row only when
something happened, a repeated failure folded into it; step rows only with a write that happens anyway, a changed result
or every 5 minutes, their counts held in memory till then), its interval is a setting read each pass, the loop reads as
alive within one interval and a minute, and GET/PUT /api/v2/heartbeat show and set it.
Ana owns the team, Ben is an admin, Cara a member (backend/tests/helpers/api_app.py). Synthetic fixtures only."""

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import heartbeat as HB
from backend import system_routines as SR
from backend.scheduler import Scheduler
from backend.tests.helpers.api import get, put  # noqa: F401  (fixture)
from backend.tests.mirror_rows import dead_mirror, live, mirrors, sync

# Two hours ago: a beat's day counts are read against the clock, and every beat below stays inside the day.
AT = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)
ORDER = [
    "github_sync",
    "personal_alerts",
    "stall_marks",
    "turn_fixer",
    "run_housekeeping",
    "ship_deployed",
    "never_stuck",
    "deferred",
    "queue_check",
    "ci_sweep",
    "updates",
    "company_page",
    "thread_digest",
    "thread_bot_digest",
    "thread_loop_guard",
    "go_live_expiry",
    "drop_unreadable",
    "repository_waits",
    "entity_links",
    "call_sweeps",
    "item_metadata",
    "source_actions",
    "jev_verdicts",
]


class Telemetry:
    def __init__(self):
        self.seen = []

    def capture(self, name, exc):
        self.seen.append((name, type(exc).__name__))


@pytest.fixture
def fake(monkeypatch):
    """Every step replaced by one that notes its run and answers what `answers` says (quiet by default)."""
    ran, answers = [], {}

    def runner(key):
        def run(ctx):
            ran.append(key)
            answer = answers.get(key, ("Nothing due", True))
            if isinstance(answer, Exception):
                raise answer
            return answer

        return run

    steps = [dataclasses.replace(step, run=runner(step.key)) for step in HB.STEPS]
    monkeypatch.setattr(HB, "STEPS", steps)
    monkeypatch.setattr(HB, "BY_KEY", {step.key: step for step in steps})
    monkeypatch.setattr(HB, "CONTROLLED", {step.control: step for step in steps if step.control})
    return ran, answers


def beat(api, scheduler, at, telemetry=None, app=True):
    ctx = HB.Context(scheduler, at, app=api.app if app else None, telemetry=telemetry)
    return HB.beat(ctx), ctx


def scheduler_of(api):
    return Scheduler(api.app.state.store, api.app.state.execution)


def rows(api, sql, *args):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(sql, args)]


def test_the_steps_run_in_their_order_and_the_server_loops_own_need_the_app(api, fake):
    ran, _ = fake
    assert [step.key for step in HB.STEPS] == ORDER
    assert {step.kind for step in HB.STEPS} == {"due", "recovery", "poll"}
    beat(api, scheduler_of(api), AT)
    assert ran == ORDER, "every step is due on a first beat"
    ran.clear()
    beat(api, scheduler_of(api), AT + timedelta(hours=1), app=False)  # a bare Scheduler.tick (tests, a script)
    assert ran == [k for k in ORDER if not HB.BY_KEY[k].loop]
    assert {"github_sync", "personal_alerts", "repository_waits", "call_sweeps"}.isdisjoint(ran)


def test_each_step_runs_at_most_as_often_as_it_did(api, fake):
    ran, _ = fake
    scheduler = scheduler_of(api)
    beat(api, scheduler, AT)
    ran.clear()
    beat(api, scheduler, AT + timedelta(seconds=10))
    assert "run_housekeeping" in ran and "deferred" in ran, "every beat"
    for key in ("stall_marks", "drop_unreadable", "go_live_expiry", "thread_digest"):
        assert key not in ran, key
    ran.clear()
    beat(api, scheduler, AT + timedelta(minutes=1, seconds=5))
    assert {"stall_marks", "drop_unreadable"} <= set(ran) and "go_live_expiry" not in ran
    ran.clear()
    beat(api, scheduler, AT + timedelta(minutes=10, seconds=5))
    assert "go_live_expiry" in ran and "thread_digest" not in ran
    ran.clear()
    beat(api, scheduler, AT + timedelta(hours=1, seconds=5))
    assert {"go_live_expiry", "thread_digest", "thread_bot_digest"} <= set(ran)
    # The digests are timed from their last recorded start, so a restarted server keeps the hour: their rows are
    # written on every run, not left in memory for the next flush.
    for key in ("thread_digest", "thread_bot_digest"):
        (row,) = rows(api, "SELECT last_run_at FROM heartbeat_steps WHERE key=?", key)
        assert row["last_run_at"] == HB.S.stamp(AT + timedelta(hours=1, seconds=5)), key
    ran.clear()
    beat(api, scheduler_of(api), AT + timedelta(hours=1, minutes=30))
    assert "thread_digest" not in ran and "thread_bot_digest" not in ran
    assert "go_live_expiry" in ran, "a new process times the in-process steps afresh, as the tick's marks did"


def test_a_poll_never_runs_more_often_than_every_10_seconds(api, fake):
    ran, _ = fake
    poll = dataclasses.replace(HB.BY_KEY["github_sync"], every=timedelta(seconds=2))
    assert poll.floor == HB.POLL_FLOOR == timedelta(seconds=10)
    assert HB.BY_KEY["github_sync"].floor == timedelta(seconds=10)
    assert dataclasses.replace(poll, every=timedelta(seconds=30)).floor == timedelta(seconds=30)
    assert HB.BY_KEY["deferred"].floor is None and HB.BY_KEY["stall_marks"].floor == timedelta(minutes=1)
    scheduler = scheduler_of(api)
    beat(api, scheduler, AT)
    ran.clear()
    beat(api, scheduler, AT + timedelta(seconds=5))  # a 5-second interval
    polls = {
        k for k in ORDER if HB.BY_KEY[k].kind == "poll" and HB.BY_KEY[k].floor <= HB.POLL_FLOOR
    }  # item_metadata: 1 min
    assert polls.isdisjoint(ran) and "run_housekeeping" in ran
    ran.clear()
    beat(api, scheduler, AT + timedelta(seconds=10))
    assert polls <= set(ran)


def test_one_failing_step_is_recorded_and_the_rest_still_run(api, fake):
    ran, answers = fake
    answers["ship_deployed"] = RuntimeError("Ana's private words")
    answers["deferred"] = ("1 deferred run started", False)
    telemetry = Telemetry()
    result, ctx = beat(api, scheduler_of(api), AT, telemetry)
    assert ran == ORDER, "the steps after it ran"
    assert result["failed"] == ["ship_deployed"] and result["did"] == ["deferred"]
    assert {"ship_deployed": "RuntimeError"} in ctx.failures, "the pass's failures, as the tick's raise was"
    assert telemetry.seen == [("scheduler", "RuntimeError")], "captured under the name it was before"
    (step,) = rows(api, "SELECT * FROM heartbeat_steps WHERE key='ship_deployed'")
    assert step["last_error"] == "Failed: unexpected error (RuntimeError)" and step["failed_count"] == 1
    assert step["last_error_at"] == step["last_run_at"]
    (row,) = rows(api, "SELECT * FROM heartbeat_beats")
    lines = {line["key"]: line for line in json.loads(row["steps"])}
    assert set(lines) == {"ship_deployed", "deferred"} and row["failed_count"] == 1 and row["did_count"] == 1
    assert (
        lines["ship_deployed"]["error"].startswith("Failed:") and lines["deferred"]["text"] == "1 deferred run started"
    )
    (run,) = rows(api, "SELECT * FROM system_runs WHERE job='heartbeat'")
    assert not run["ok"] and run["result"] == "Failed: Shipped merges"
    assert "private" not in json.dumps([step, row, run])
    # A step that names its own failure (no raise) is recorded, not captured.
    answers["ship_deployed"] = HB.Failed("Failed: the loop check could not finish")
    telemetry.seen.clear()
    beat(api, scheduler_of(api), AT + timedelta(seconds=10), telemetry)
    assert telemetry.seen == []
    (step,) = rows(api, "SELECT * FROM heartbeat_steps WHERE key='ship_deployed'")
    assert step["last_error"] == "Failed: the loop check could not finish" and step["failed_count"] == 2


def test_a_quiet_beat_is_counted_not_stored(api, fake):
    _, answers = fake
    store = api.app.state.store
    scheduler = scheduler_of(api)
    for n in range(3):
        beat(api, scheduler, AT + timedelta(seconds=10 * n))
    assert rows(api, "SELECT * FROM heartbeat_beats") == []
    (state,) = rows(api, "SELECT * FROM heartbeat_states")
    assert state["beats_count"] == 3 and state["quiet_beats_count"] == 3
    assert state["quiet_since_at"] == HB.S.stamp(AT) and state["last_beat_at"] == HB.S.stamp(AT + timedelta(seconds=20))
    stored = lambda key: rows(api, "SELECT * FROM heartbeat_steps WHERE key=?", key)[0]  # noqa: E731
    assert stored("run_housekeeping")["runs_count"] == 1, "only the first beat wrote the step rows"
    with store.read() as c:
        step = HB.merged_steps(c, store)["run_housekeeping"]
    assert step["runs_count"] == 3 and step["did_count"] == 0 and step["last_result"] == "Nothing due"
    assert step["last_did_at"] is None and step["last_run_at"] == HB.S.stamp(AT + timedelta(seconds=20))
    assert next(s for s in get(api, "heartbeat")["steps"] if s["key"] == "run_housekeeping")["runs_24h"] == 3
    (run,) = rows(api, "SELECT * FROM system_runs WHERE job='heartbeat'")
    assert run["ok"] and run["result"] == "All quiet", "quiet repeats fold into one row"
    # A changed result is written at once; otherwise the counts go down every 5 minutes.
    answers["run_housekeeping"] = ("Nothing to clean", True)
    beat(api, scheduler, AT + timedelta(seconds=30))
    assert (
        stored("run_housekeeping")["runs_count"] == 4
        and stored("run_housekeeping")["last_result"] == "Nothing to clean"
    )
    beat(api, scheduler, AT + timedelta(seconds=40))
    assert stored("turn_fixer")["runs_count"] == 4, "flushed with the changed result's write"
    assert stored("run_housekeeping")["runs_count"] == 4
    beat(api, scheduler, AT + timedelta(minutes=5, seconds=30))
    assert stored("run_housekeeping")["runs_count"] == 6 and stored("turn_fixer")["runs_count"] == 6


def test_a_working_beat_stores_one_row_with_only_the_steps_that_did_something(api, fake):
    _, answers = fake
    scheduler = scheduler_of(api)
    beat(api, scheduler, AT)
    answers["deferred"] = ("2 deferred runs started", False)
    answers["queue_check"] = ("1 bot's queue noted", False)
    result, _ = beat(api, scheduler, AT + timedelta(seconds=10))
    assert result["did"] == ["deferred", "queue_check"]
    (row,) = rows(api, "SELECT * FROM heartbeat_beats")
    assert row["started_at"] == HB.S.stamp(AT + timedelta(seconds=10)) and row["did_count"] == 2
    assert [line["key"] for line in json.loads(row["steps"])] == ["deferred", "queue_check"]
    (state,) = rows(api, "SELECT * FROM heartbeat_states")
    assert state["quiet_since_at"] is None and state["quiet_beats_count"] == 1 and state["beats_count"] == 2
    (step,) = rows(api, "SELECT * FROM heartbeat_steps WHERE key='deferred'")
    assert step["last_did"] == "2 deferred runs started" and step["did_count"] == 1 and step["runs_count"] == 2
    runs = rows(api, "SELECT * FROM system_runs WHERE job='heartbeat' ORDER BY started_at")
    assert runs[-1]["result"] == "2 steps did something"


def test_old_beats_are_pruned_hourly_inside_the_beat(api, fake, monkeypatch):
    store = api.app.state.store
    _, answers = fake

    def stored(at):
        with store.transaction() as c:
            HB._beat_row(c, at, [HB.Ran("deferred", 1, "1 deferred run started", False)], 1)

    stored(AT - timedelta(days=8))
    beat(api, scheduler_of(api), AT)  # first beat: prunes
    assert rows(api, "SELECT * FROM heartbeat_beats") == []
    stored(AT - timedelta(days=8))
    beat(api, scheduler_of(api), AT + timedelta(minutes=30))  # inside the hour: not again
    assert len(rows(api, "SELECT * FROM heartbeat_beats")) == 1
    beat(api, scheduler_of(api), AT + timedelta(hours=1))
    assert rows(api, "SELECT * FROM heartbeat_beats") == []
    # ...and past the row cap, the newest are kept.
    monkeypatch.setattr(HB, "KEEP_BEATS", 3)
    for n in range(5):
        stored(AT + timedelta(minutes=n))
    with store.transaction() as c:
        HB.prune(c, AT + timedelta(minutes=10))
    assert [r["started_at"] for r in rows(api, "SELECT started_at FROM heartbeat_beats ORDER BY started_at")] == [
        HB.S.stamp(AT + timedelta(minutes=n)) for n in (2, 3, 4)
    ]


def test_the_interval_is_clamped_and_read_each_pass(api):
    store = api.app.state.store
    assert (HB.clamp(1), HB.clamp(5), HB.clamp(15), HB.clamp(60), HB.clamp(600), HB.clamp("x"), HB.clamp(None)) == (
        5,
        5,
        15,
        60,
        60,
        10,
        10,
    )
    assert HB.interval(store) == HB.INTERVAL_DEFAULT == 10
    with store.transaction() as c:
        assert HB.save_interval(c, "human:ana", 2) == 5
    assert HB.interval(store) == 5
    with store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value='900' WHERE key=?", (HB.INTERVAL_KEY,))
    assert HB.interval(store) == 60, "a stored value out of range is clamped when read"


def test_get_shows_the_steps_and_the_log_and_put_needs_an_owner_or_admin(api, fake):
    _, answers = fake
    answers["deferred"] = ("1 deferred run started", False)
    scheduler = scheduler_of(api)
    for n in range(3):
        beat(api, scheduler, AT + timedelta(seconds=10 * n))
    data = get(api, "heartbeat", token="cara-test")  # any signed-in person reads it
    assert data["interval_s"] == 10 and (data["interval_min"], data["interval_max"]) == (5, 60)
    assert data["can_change"] is False and data["last_beat_at"]
    assert [k["key"] for k in data["kinds"]] == ["due", "recovery", "poll"]
    assert [s["key"] for s in data["steps"]] == ORDER
    reminders = next(s for s in data["steps"] if s["key"] == "deferred")
    assert reminders["kind_name"] == "Due times" and reminders["every"] == "Every beat"
    assert reminders["last_did"] == "1 deferred run started" and reminders["runs_24h"] == reminders["did_24h"] == 3
    stalls = next(s for s in data["steps"] if s["key"] == "drop_unreadable")
    assert stalls["every"] == "Every minute" and stalls["every_s"] == 60 and stalls["runs_24h"] == 1
    assert next(s for s in data["steps"] if s["key"] == "github_sync")["every"] == "Every 10 s"
    assert len(data["beats"]) == 3 and data["next"] is None
    page = get(api, "heartbeat?limit=2")
    assert len(page["beats"]) == 2 and page["next"]
    rest = get(api, "heartbeat?limit=2&before=" + page["next"])
    assert [b["id"] for b in page["beats"] + rest["beats"]] == [b["id"] for b in data["beats"]] and rest["next"] is None
    assert rest["beats"][0]["steps"][0]["name"] == "Deferred runs"
    get(api, "heartbeat?before=nonsense", expected=422)

    put(api, "heartbeat", {"interval_s": 20}, token="cara-test", expected=403)
    assert put(api, "heartbeat", {"interval_s": 20}, token="ben-test")["interval_s"] == 20
    assert get(api, "heartbeat")["can_change"] is True
    put(api, "heartbeat", {"interval_s": 4}, token="ben-test", expected=422)
    put(api, "heartbeat", {"interval_s": 61}, expected=422)
    assert HB.interval(api.app.state.store) == 20
    (event,) = rows(api, "SELECT * FROM events WHERE action='heartbeat.updated'")
    assert json.loads(event["detail"]) == {"before": 10, "after": 20}


def test_jobs_have_the_heartbeat_not_its_steps_and_the_sync_retires_their_mirrors(api):
    assert SR.BY_KEY["heartbeat"].schedule == "Every 10 s" and SR.BY_KEY["heartbeat"].name == "Heartbeat"
    assert not set(SR.BY_KEY) & set(HB.BY_KEY), "a step is no longer a job of its own"
    sync(api)
    old = dead_mirror(api, "Due reminders", "job.due_reminders", {"kind": "system_job", "id": "job:due_reminders"})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM automations WHERE json_extract(flow,'$.trigger.name')='tico.builtin.job.heartbeat'")
    done = sync(api)
    assert done["retired"] == [old] and mirrors(api)[old][2]
    (made,) = done["made"]
    assert live(api, "tico.builtin.job.heartbeat") == [made]
    listed = {a["id"] for a in get(api, "automations?limit=500")["automations"]}
    assert made not in listed, "the list's Heartbeat row stands for it, as Maintenance's does"


def test_a_beat_shows_under_the_heartbeat_row_on_automations(api):
    Scheduler(api.app.state.store, api.app.state.execution).tick()
    runs = get(api, "automations/runs?range=1h&ids=heartbeat", token="cara-test")["runs"]
    assert runs and all(r["automation_id"] == "heartbeat" and r["id"].startswith("hb_") for r in runs)
    stats = {s["id"]: s for s in get(api, "automations/stats?range=1h")["automations"]}
    assert stats["heartbeat"]["runs"] >= 1


def test_a_late_outcome_lands_on_its_step_row(api):
    store = api.app.state.store
    HB.record_late(store, "personal_alerts", "Nothing to send", True, ms=3)
    assert rows(api, "SELECT * FROM heartbeat_steps") == [], "a quiet late outcome writes nothing"
    shown = next(s for s in get(api, "heartbeat")["steps"] if s["key"] == "personal_alerts")
    assert shown["last_result"] == "Nothing to send" and shown["last_run_at"] and shown["last_ms"] == 3, (
        "noted in memory"
    )
    HB.record_late(store, "personal_alerts", "2 alerts tried", False, ms=4)
    (step,) = rows(api, "SELECT * FROM heartbeat_steps")
    assert step["last_did"] == "2 alerts tried" and step["runs_count"] == 0, "its beat counted the run"
    assert len(rows(api, "SELECT * FROM heartbeat_beats")) == 1


def test_personal_alerts_busy_says_nothing(api, fake, monkeypatch):
    """The beat only starts the pass; while the last one still runs (the worker lock is busy) it has no words, so the
    step's last result stays what the last pass said, never "Nothing to send"."""
    monkeypatch.setattr(api.app.state.personal_alerts, "tick", lambda: False)
    steps = [dataclasses.replace(s, run=HB.personal_alerts) if s.key == "personal_alerts" else s for s in HB.STEPS]
    monkeypatch.setattr(HB, "STEPS", steps)
    monkeypatch.setattr(HB, "BY_KEY", {step.key: step for step in steps})
    beat(api, scheduler_of(api), AT)
    shown = next(s for s in get(api, "heartbeat")["steps"] if s["key"] == "personal_alerts")
    assert shown["last_result"] is None and shown["runs_24h"] == 1


def test_a_repeated_failure_folds_into_the_newest_beat_row(api, fake):
    _, answers = fake
    scheduler = scheduler_of(api)
    answers["ship_deployed"] = HB.Failed("Failed: the loop check could not finish")
    for n in range(3):
        beat(api, scheduler, AT + timedelta(seconds=10 * n))
    (row,) = rows(api, "SELECT * FROM heartbeat_beats")
    assert row["repeat_count"] == 3 and row["started_at"] == HB.S.stamp(AT)
    assert row["last_repeated_at"] == HB.S.stamp(AT + timedelta(seconds=20))
    (shown,) = get(api, "heartbeat")["beats"]
    assert shown["repeats"] == 3 and shown["last_repeated_at"] == row["last_repeated_at"]
    step = next(s for s in get(api, "heartbeat")["steps"] if s["key"] == "ship_deployed")
    assert step["failed_24h"] == 3 and step["failing"] is True
    # A different error, or a beat that also did something, is a row of its own.
    answers["ship_deployed"] = HB.Failed("Failed: the merge list could not be read")
    beat(api, scheduler, AT + timedelta(seconds=30))
    answers["deferred"] = ("1 deferred run started", False)
    beat(api, scheduler, AT + timedelta(seconds=40))
    assert len(rows(api, "SELECT * FROM heartbeat_beats")) == 3
    # Once the step runs clean, its last error stays but is no longer failing.
    answers["ship_deployed"] = ("Nothing due", True)
    beat(api, scheduler, AT + timedelta(seconds=50))
    step = next(s for s in get(api, "heartbeat")["steps"] if s["key"] == "ship_deployed")
    assert step["last_error"] and step["failing"] is False


def test_the_loop_is_alive_within_one_interval_and_a_minute(api):
    store = api.app.state.store
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with store.transaction() as c:
        c.execute("DELETE FROM service_health WHERE service='scheduler'")
        HB.save_interval(c, "human:ana", 60)
        HB._state_write(c, now - timedelta(seconds=70), False)
    with store.read() as c:
        assert HB.liveness(c, now) == (HB.S.stamp(now - timedelta(seconds=70)), 70.0, True), "a 60 s beat, 70 s ago"
    with store.transaction() as c:
        HB._state_write(c, now - timedelta(seconds=200), False)
    with store.read() as c:
        assert HB.liveness(c, now)[2] is False, "200 s ago"
    # The fresher of the beat and the scheduler's pass counts; at 10 s the threshold is the minute floor plus 10.
    with store.transaction() as c:
        c.execute(
            "INSERT INTO service_health(service,last_succeeded_at) VALUES('scheduler',?)",
            (HB.S.stamp(now - timedelta(seconds=5)),),
        )
        HB.save_interval(c, "human:ana", 10)
    with store.read() as c:
        assert HB.liveness(c, now)[1:] == (5.0, True)
        assert HB.liveness(c, now + timedelta(seconds=66))[2] is False


def test_the_rest_of_the_pass_carries_a_skipped_passs_failures():
    clock = [0.0]
    rest = HB.Rest(floor=10, clock=lambda: clock[0])

    class Ctx:
        def __init__(self, *failures):
            self.failures = list(failures)

    assert rest.due(Ctx()) is True
    clock[0] = 5
    assert rest.due(Ctx({"ship_deployed": "RuntimeError"})) is False
    clock[0] = 10
    ctx = Ctx({"deferred": "ValueError"})
    assert rest.due(ctx) is True
    assert ctx.failures == [{"ship_deployed": "RuntimeError"}, {"deferred": "ValueError"}]
    clock[0] = 12
    assert rest.due(Ctx()) is False and rest.carried == []


def test_tick_ms_is_the_ticks_own_time(api):
    scheduler = scheduler_of(api)
    ctx = HB.Context(scheduler, AT)
    ctx.started -= 120  # a beat that took two minutes
    scheduler.tick(AT, ctx)
    (row,) = rows(api, "SELECT detail FROM service_health WHERE service='scheduler'")
    assert json.loads(row["detail"])["tick_ms"] < 120_000


def test_the_heartbeat_jobs_schedule_follows_the_interval(api):
    put(api, "heartbeat", {"interval_s": 30}, token="ben-test")
    job = next(j for j in get(api, "system-routines")["jobs"] if j["key"] == "heartbeat")
    assert job["schedule"] == "Every 30 s"
    now = datetime.now(timezone.utc)
    with api.app.state.store.read() as c:
        following = SR.next_at(SR.BY_KEY["heartbeat"], {"started_at": HB.S.stamp(now)}, now - timedelta(minutes=1), c=c)
    assert following == now + timedelta(seconds=30)


# ----------------------------------------------------------------------------- the outside polls' own worker (P07)
OUTSIDE = ["github_sync", "ci_sweep", "repository_waits", "item_metadata", "source_actions"]


def outside_of(api, scheduler, **kw):
    worker = HB.Outside(scheduler, app=api.app, **kw)
    scheduler.outside = worker
    return worker


def test_the_github_steps_run_on_their_own_worker_and_the_beat_runs_the_rest(api, fake):
    ran, _ = fake
    assert [s.key for s in HB.STEPS if s.outside] == OUTSIDE
    scheduler = scheduler_of(api)
    worker = outside_of(api, scheduler)
    beat(api, scheduler, AT)
    assert ran == [k for k in ORDER if k not in OUTSIDE], "the beat leaves them to the worker"
    ran.clear()
    worker.wake(AT)
    result = worker.pass_once()
    assert ran == OUTSIDE and result["ran"] == OUTSIDE, "in the beat's order, each due on a first pass"
    (state,) = rows(api, "SELECT beats_count FROM heartbeat_states WHERE id=1")
    assert state["beats_count"] == 1, "the worker's pass is not a beat"


def test_a_slow_github_never_holds_up_a_due_run_and_one_pass_runs_at_a_time(api, fake, monkeypatch):
    """AC-1, AC-2, AC-4: GitHub answers after 10 s (here: hangs until released). For five minutes of 10-second beats
    each due run starts within the beat, the every-minute steps run 5 times, and the worker runs its one stuck pass,
    never a second beside it; released, it runs one more pass, not one per missed beat."""
    import threading
    import time

    ran, answers = fake
    release, started = threading.Event(), threading.Event()
    real = HB.BY_KEY["github_sync"].run

    def slow_github(ctx):
        started.set()
        release.wait(10)  # GitHub answers after 10 s
        return real(ctx)

    steps = [dataclasses.replace(s, run=slow_github) if s.key == "github_sync" else s for s in HB.STEPS]
    monkeypatch.setattr(HB, "STEPS", steps)
    monkeypatch.setattr(HB, "BY_KEY", {s.key: s for s in steps})
    answers["deferred"] = ("1 deferred run started", False)
    scheduler = scheduler_of(api)
    worker = outside_of(api, scheduler).start()
    try:
        beats, slowest = 0, 0.0
        for i in range(30):  # five minutes at a 10 s interval
            at = AT + timedelta(seconds=10 * i)
            worker.wake(at)
            if i == 0:
                assert started.wait(5), "the worker took the first pass"
            begun = time.monotonic()
            result, _ = beat(api, scheduler, at)
            slowest = max(slowest, time.monotonic() - begun)
            assert "deferred" in result["did"], at
            beats += 1
        assert beats >= 28 and slowest < 2, f"slowest beat {slowest:.1f} s"
        assert ran.count("deferred") == 30 and ran.count("stall_marks") == 5, "AC-2"
        assert worker.passes == 0 and worker.running, "one pass, still waiting on GitHub; no second beside it"
        assert ran.count("github_sync") == 0 and "ci_sweep" not in ran
        release.set()
        deadline = time.monotonic() + 10
        while worker.passes < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)  # a third pass, if one were coming, would start here
        assert worker.passes == 2, "the stuck pass, then one more for every wake it missed"
        assert ran.count("github_sync") == 2
    finally:
        worker.halt(5)
    assert not worker.thread.is_alive()


def test_with_github_quick_each_poll_keeps_todays_cadence_and_none_runs_faster(api, fake):
    """AC-3: five minutes at a 10 s interval: GitHub sync 30 times, the re-read 5, the CI sweep once, as in the beat;
    at a 5 s interval none runs more often."""
    ran, _ = fake
    for seconds in (10, 5):
        ran.clear()
        scheduler = scheduler_of(api)
        worker = outside_of(api, scheduler)
        for i in range(300 // seconds):
            at = AT + timedelta(seconds=seconds * i)
            worker.wake(at)
            worker.pass_once()
            beat(api, scheduler, at)
        assert 27 <= ran.count("github_sync") <= 30, seconds
        assert ran.count("repository_waits") == ran.count("github_sync")
        assert ran.count("source_actions") == 300 // seconds, "the merge queue calls go every beat, as in the beat"
        assert ran.count("item_metadata") in (4, 5) and ran.count("ci_sweep") == 1, seconds
        assert ran.count("deferred") == 300 // seconds, "the beat keeps its own interval"


def test_a_failing_github_step_shows_on_its_row_and_reaches_the_schedulers_health(api, fake):
    """AC-5: as when it ran in the beat: its row on the Heartbeat screen, a beat-log row, telemetry, and the next
    scheduler pass's health row."""
    ran, answers = fake
    answers["item_metadata"] = RuntimeError("GitHub said no")
    telemetry = Telemetry()
    scheduler = scheduler_of(api)
    worker = outside_of(api, scheduler, telemetry=telemetry)
    worker.wake(AT)
    result = worker.pass_once()
    assert result["failed"] == ["item_metadata"] and ran == OUTSIDE, "the steps after it still ran"
    assert telemetry.seen == [("scheduler", "RuntimeError")]
    screen = get(api, "heartbeat")
    row = next(s for s in screen["steps"] if s["key"] == "item_metadata")
    assert row["failing"] and row["last_error"] and row["failed_24h"] == 1 and row["runs_24h"] == 1
    assert [s["key"] for s in screen["beats"][0]["steps"]] == ["item_metadata"]
    assert screen["beats_24h"] == 0, "not a beat"
    assert worker.take_failures() == [{"item_metadata": "RuntimeError"}]
    answers["item_metadata"] = RuntimeError("again")
    worker.wake(AT + timedelta(minutes=1))
    worker.pass_once()
    scheduler.tick(AT + timedelta(minutes=1))
    (health,) = rows(api, "SELECT detail FROM service_health WHERE service='scheduler'")
    assert {"item_metadata": "RuntimeError"} in json.loads(health["detail"])["failures"]


def test_the_release_check_rides_on_the_worker_at_most_every_10_seconds(monkeypatch):
    class _Scheduler:
        outside = None
        step_marks: dict = {}
        store = execution = github_app = None

    now, calls = [0.0], []
    telemetry = Telemetry()

    def also():
        calls.append(now[0])
        if len(calls) == 2:
            raise RuntimeError("releases unreachable")

    worker = HB.Outside(_Scheduler(), telemetry=telemetry, also=also, clock=lambda: now[0])
    monkeypatch.setattr(HB, "outside_pass", lambda ctx: {"ran": [], "did": [], "failed": []})
    for t in (0.0, 5.0, 10.0, 15.0, 20.0):
        now[0] = t
        worker.pass_once()
    assert calls == [0.0, 10.0, 20.0]
    assert telemetry.seen == [("scheduler", "RuntimeError")], "a failure is captured; the next pass tries again"


def test_a_worker_whose_thread_died_hands_the_github_steps_back_to_the_beat(api, fake):
    ran, _ = fake
    scheduler = scheduler_of(api)
    worker = outside_of(api, scheduler).start()
    beat(api, scheduler, AT)
    assert not set(OUTSIDE) & set(ran), "a live worker takes them"
    worker.halt(5)  # the thread ends, as a crash outside a pass would end it
    assert not worker.alive()
    ran.clear()
    beat(api, scheduler, AT + timedelta(minutes=10))
    assert [k for k in ran if k in OUTSIDE] == OUTSIDE, "the beat runs them again, in its order"
