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
    return {job["key"]: {**job, "group": g["key"]} for g in data["groups"] for job in g["jobs"]}, data


def test_every_built_in_job_is_listed_in_its_group_and_six_are_marked_controllable(api):
    jobs, data = rows(api, token="ben-test")     # a member, not only the owner, sees the list
    assert [g["name"] for g in data["groups"]] == ["Checks that suggest", "Learning", "Reminders and deadlines",
                                                   "Housekeeping"]
    assert {k for k, j in jobs.items() if j["controllable"]} == {
        "stale_review", "still_wanted", "kpi_asks", "learnings", "health_scan", "ask_deadlines"}
    assert {k for k, j in jobs.items() if j["ai"]} == {"stale_review", "learnings"}
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
                "due_reminders", "snooze_ends", "routine_timer", "cleanup"):
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
