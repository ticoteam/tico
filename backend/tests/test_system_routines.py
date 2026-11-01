"""System routines (backend/system_routines.py): every built-in job in one registry, a run record per run with its result
in words or a plain failure, and for a Decide job the route that answered, the fallback and this month's spend, read back
by `GET /api/v2/system-routines` for Settings > Routines. Spec acceptance tests 1, 2, 10, 11 and 13; synthetic fixtures."""

from datetime import datetime, timedelta, timezone

from backend import judge as B
from backend import still_wanted
from backend import system_routines as SR
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.test_stale_review import FakeDecide, stale   # noqa: F401
from backend.tests.test_tasks_board import api, get, headers, post   # noqa: F401
from clients import judge as J


def rows(api, token="ana-test"):
    data = get(api, "system-routines", token=token)
    return {job["key"]: job for job in data["jobs"]}, data


def test_every_built_in_job_is_listed_with_its_group_word_and_control_availability(api):
    jobs, data = rows(api, token="ben-test")     # a member, not only the owner, sees the list
    assert "groups" not in data, "one flat list; a group is a word search finds, not a heading"
    assert [j["key"] for j in data["jobs"]] == [job.key for job in SR.JOBS] + ["task-rule-close-after-done"]
    assert {k for k, j in jobs.items() if j["controllable"]} == {
        "stale_review", "still_wanted", "kpi_asks", "learnings", "health_scan", "ask_deadlines", "task_manager_report",
        "stall_triage", "engineering_exceptions", "bookkeeper_check", "memory_tidy"}
    assert {k for k, j in jobs.items() if j["ai"]} == {"stale_review", "learnings", "stall_triage"}
    assert all(j["needed"] == (j["group"] == "housekeeping") for j in jobs.values())
    assert jobs["stale_review"]["schedule"] == "Hourly" and jobs["learnings"]["schedule"] == "Nightly at 03:00"
    for key in ("due_reminders", "ask_deadlines", "approval_expiry"):
        assert jobs[key]["group"] == "reminders"
    for key in ("stall_checks", "snooze_ends", "cleanup", "gateway_check", "billing_check", "directory_sync"):
        assert jobs[key]["group"] == "housekeeping"


def test_a_scheduler_tick_records_each_run_with_its_result_and_next_run(api):
    store = api.app.state.store
    at = datetime.now(timezone.utc)
    Scheduler(store, api.app.state.execution).tick(at)
    jobs, _ = rows(api)
    for key in ("still_wanted", "ask_deadlines", "approval_expiry", "stall_checks", "health_scan", "kpi_asks",
                "due_reminders", "snooze_ends", "cleanup"):
        assert jobs[key]["last"] and jobs[key]["last"]["ok"], key
        assert jobs[key]["last"]["result"], key
    assert jobs["still_wanted"]["last"]["result"] == "No idle tasks"
    nxt = H.parse_ts(jobs["ask_deadlines"]["next"])
    assert timedelta(minutes=59) < nxt - at <= timedelta(hours=1, seconds=5), "hourly: an hour after its last run"
    assert jobs["learnings"]["next"], "the nightly run's next 03:00 is known before it has ever run"


def test_a_quiet_job_every_tick_keeps_one_row_not_one_per_tick(api):
    store = api.app.state.store
    scheduler = Scheduler(store, api.app.state.execution)
    at = datetime.now(timezone.utc)
    for n in range(4):
        scheduler.tick(at + timedelta(seconds=10 * n))
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM system_runs WHERE job='snooze_ends'").fetchone()[0] == 1


def test_a_failed_run_shows_failed_with_a_plain_reason_and_still_counts_as_a_scheduler_failure(api, monkeypatch):
    store = api.app.state.store

    def broken(conn, at=None):
        raise RuntimeError("Ana's private note text")
    monkeypatch.setattr(still_wanted, "ask_idle", broken)
    result = Scheduler(store, api.app.state.execution).tick()
    assert {"still_wanted": "RuntimeError"} in result["failures"]
    jobs, _ = rows(api)
    last = jobs["still_wanted"]["last"]
    assert last["ok"] is False and last["result"] == "Failed: unexpected error (RuntimeError)"
    assert "private" not in str(jobs), "an exception's own text never shows"


def test_a_stale_review_answered_by_the_gateway_shows_gateway_and_this_months_spend(stale):
    api, store, task = stale
    task("Ship the export button", pr="merged")
    run = SR.Run("stale_review")
    from backend import stale_review
    stale_review.words(run, stale_review.run_pass(store, engine=FakeDecide("update")))
    SR.write(store, run)
    with store.transaction() as c:      # what `judge.metered` writes for a paid call
        H.event(c, H.KEEPER, "judge.spend", "stale-review@1", {"label": "stale-review@1", "cost_usd": 0.42})
        H.event(c, H.KEEPER, "judge.spend", "listening@2", {"label": "listening@2", "cost_usd": 9.0})
    jobs, _ = rows(api)
    row = jobs["stale_review"]
    assert row["last"]["result"] == "1 checked, 1 suggestion" and row["last"]["ok"]
    assert row["route"] == "Gateway" and row["fallback"] is None
    assert row["spent_this_month_usd"] == 0.42, "only this job's question set"


def test_gateway_down_falls_back_to_own_key_then_company_ai_and_the_row_says_so(stale):
    api, store, task = stale
    task("Ship the export button", pr="merged")

    def gateway(state, questions, label=None):
        raise J.JudgeError("unavailable", "down")
    gateway.provider = J.GATEWAY_PROVIDER
    own = FakeDecide("update", provider=J.TYPESAFE_PROVIDER)
    own.provider = J.TYPESAFE_PROVIDER
    chain = B.ordered([gateway, own])
    from backend import stale_review
    run = stale_review.words(SR.Run("stale_review"), stale_review.run_pass(store, engine=chain))
    SR.write(store, run)
    jobs, _ = rows(api)
    assert jobs["stale_review"]["route"] == "Company key"
    assert jobs["stale_review"]["fallback"] == "Ran on your own key: Gateway unavailable"

    # ...and with the own key down too, the company's AI answers.
    task("Ship the import button", pr="merged")
    own.fail = J.JudgeError("auth", "refused")
    llm = FakeDecide("update", provider=next(iter(J.LLM_PROVIDERS.values())))
    llm.provider = llm.provider
    with store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='stale_review'")    # the next hour's pass
    run = stale_review.words(SR.Run("stale_review"),
                             stale_review.run_pass(store, engine=B.ordered([gateway, own, llm])))
    SR.write(store, run)
    jobs, _ = rows(api)
    assert jobs["stale_review"]["route"] == "Company AI"
    assert jobs["stale_review"]["fallback"] == "Ran on company AI: Gateway unavailable"


def test_old_runs_are_pruned_to_the_last_ones_per_job(api):
    store = api.app.state.store
    with store.transaction() as c:
        for n in range(SR.KEEP + 5):
            run = SR.Run("cleanup", started=H.shift(H.now(), seconds=-n)).did(f"run {n}")
            SR.save(c, run)
        SR.prune(c)
        assert c.execute("SELECT count(*) FROM system_runs WHERE job='cleanup'").fetchone()[0] == SR.KEEP


def test_a_nightly_learning_run_with_no_decision_model_is_recorded_as_skipped(api):
    from backend import learnings as L
    store = api.app.state.store
    with store.transaction() as c:
        run_id = L.claim(c, "2026-10-12")
    L.recorded(store, run_id)
    jobs, _ = rows(api)
    assert jobs["learnings"]["last"]["result"] == "Skipped: no decision model"
    assert jobs["learnings"]["route"] is None, "a run that asked nothing names no route"


def test_a_failed_directory_sync_shows_members_fixed_words_never_the_vendors_text(api, monkeypatch):
    from backend.store import Problem
    store = api.app.state.store
    directory = api.app.state.directory
    monkeypatch.setattr(type(directory), "due", lambda self, c: True)

    def refused(self, *a, **k):
        raise Problem("directory_entra", "Entra refused the request (403). AADSTS65001 tenant acme-secret app 1234", 502)
    monkeypatch.setattr(type(directory), "run", refused)
    directory.tick(True)
    jobs, _ = rows(api, token="ben-test")
    assert jobs["directory_sync"]["last"]["result"] == "Failed: Microsoft Entra refused the request (403)"
    assert "acme-secret" not in str(jobs) and "AADSTS" not in str(jobs)


def test_an_expired_go_live_is_recorded_as_ended_not_as_nothing(api):
    from backend import go_live
    from backend.store import encode
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES(?,?)", (go_live.PREFIX + "ops", encode(
            {"actor": "bot:ops", "computer": "Mac", "requested_at": H.shift(H.now(), days=-8)})))
    Scheduler(store, api.app.state.execution).tick()
    jobs, _ = rows(api)
    assert jobs["go_live_expiry"]["last"]["result"] == "1 go-live ended"


def test_a_quiet_repeat_moves_its_row_after_a_minute_and_a_busy_run_starts_a_new_one(api):
    store = api.app.state.store
    first = SR.Run("snooze_ends", started=H.shift(H.now(), seconds=-200)).did("Nothing due", quiet=True)
    with store.transaction() as c:
        rid = SR.save(c, first, finished=first.started)
        moved = SR.Run("snooze_ends").did("Nothing due", quiet=True)
        assert SR.save(c, moved) == rid
        row = c.execute("SELECT runs,started FROM system_runs WHERE id=?", (rid,)).fetchone()
        assert row["runs"] == 2 and row["started"] == moved.started, "folded and moved forward"
        busy = SR.Run("snooze_ends").did("1 snooze ended")
        assert SR.save(c, busy) != rid
        assert c.execute("SELECT count(*) FROM system_runs WHERE job='snooze_ends'").fetchone()[0] == 2


def test_each_wrapped_block_still_reports_its_old_failure_name(api, monkeypatch):
    from backend import (approval_expiry, ask_deadlines, billing_check, go_live, goal_asks, health_monitor,
                         still_needed)
    from backend import goals as G
    store = api.app.state.store
    # The pass refreshes one live goal per transaction; an empty registry has no refresh to fail.
    with store.transaction() as c:
        G.create(c, "human:ana", "Exercise goal failure reporting", "company")
    store.settings.gateway_key = "synthetic"
    monkeypatch.setattr(billing_check, "env_key", lambda settings: "synthetic")

    def broken(*a, **k):
        raise RuntimeError("x")
    for module, name in ((G, "refresh"), (goal_asks, "sync"), (health_monitor, "check"), (go_live, "expire"),
                         (ask_deadlines, "sweep"), (approval_expiry, "sweep"), (still_needed, "sweep"),
                         (billing_check, "read_later"), (still_wanted, "ask_idle")):
        monkeypatch.setattr(module, name, broken)
    from backend import gateway_connection
    monkeypatch.setattr(gateway_connection, "check_soon", broken)
    failures = Scheduler(store, api.app.state.execution).tick()["failures"]
    for name in ("goals", "goal_asks", "health_monitor", "go_live", "ask_deadlines", "approval_expiry",
                 "still_needed", "billing_check", "still_wanted", "gateway"):
        assert {name: "RuntimeError"} in failures, name
    jobs, _ = rows(api)
    assert jobs["goal_colours"]["last"]["ok"] is False
    assert jobs["goal_colours"]["last"]["result"] == "Failed: unexpected error (RuntimeError)"
