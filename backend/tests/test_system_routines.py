"""System routines (backend/system_routines.py): every built-in job in one registry, a run record per run with its result
in words or a plain failure, and for a Decide job the route that answered, the fallback and this month's spend, read back
by `GET /api/v2/system-routines` for Settings > Routines. Spec acceptance tests 1, 2, 10, 11 and 13; synthetic fixtures."""

from datetime import datetime, timedelta, timezone

from backend import judge as B
from backend import ask_deadlines
from backend import system_routines as SR
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.test_stale_review import FakeDecide, stale   # noqa: F401
from backend.tests.test_tasks_board import api, get, headers, post   # noqa: F401
from clients import judge as J


def rows(api, token="ana-test"):
    data = get(api, "system-routines", token=token)
    return {job["key"]: job for job in data["jobs"]}, data


def test_the_jobs_list_and_each_run_record(api):
    from backend import go_live
    from backend.store import encode
    store = api.app.state.store
    # every built in job is listed with its group word and control availability
    jobs, data = rows(api, token="ben-test")     # a member, not only the owner, sees the list
    assert "groups" not in data, "one flat list; a group is a word search finds, not a heading"
    store = api.app.state.store
    with store.read() as c:
        hidden = SR.not_applying(c, store.settings)     # a job that cannot run here is left out (Job.applies)
    assert "directory_sync" in hidden and "topic_fresh" not in hidden
    assert [j["key"] for j in data["jobs"]] == [job.key for job in SR.JOBS if job.key not in hidden] + [
        "task-rule-close-after-done"]
    assert {k for k, j in jobs.items() if j["controllable"]} == {
        "stale_review", "kpi_asks", "ask_deadlines", "task_manager_report",
        "bookkeeper_check", "memory_tidy"} - hidden
    assert {k for k, j in jobs.items() if j["ai"]} == {"stale_review", "thread_rank"}
    assert "learnings" not in jobs, "Learn runs in Maintenance"
    # Housekeeping has no switch; Task roundup and Learn run in Maintenance.
    assert all(j["needed"] == (j["group"] == "housekeeping" and not j["controllable"]) for j in jobs.values())
    assert "task_roundup" not in jobs
    assert jobs["thread_followups"]["name"] == "Earlier messages"
    assert jobs["thread_followups"]["then"] == [SR.ACTIONS["tell_earlier_messages"][0]]
    assert jobs["stale_review"]["schedule"] == "Hourly"
    for key in ("due_reminders", "ask_deadlines", "approval_expiry"):
        assert jobs[key]["group"] == "reminders"
    for key in ("stall_checks", "snooze_ends", "gateway_check", "billing_check", "directory_sync"):
        assert SR.BY_KEY[key].group == "housekeeping"
    assert all(j.get("records", True) for j in jobs.values()), "every job writes its runs"
    # a scheduler tick records each run with its result and next run
    store = api.app.state.store
    at = datetime.now(timezone.utc)
    Scheduler(store, api.app.state.execution).tick(at)
    jobs, _ = rows(api)
    for key in ("ask_deadlines", "approval_expiry", "stall_checks", "kpi_asks",
                "due_reminders", "snooze_ends"):
        assert jobs[key]["last"] and jobs[key]["last"]["ok"], key
        assert jobs[key]["last"]["result"], key
    nxt = H.parse_ts(jobs["ask_deadlines"]["next"])
    assert timedelta(minutes=59) < nxt - at <= timedelta(hours=1, seconds=5), "hourly: an hour after its last run"
    assert "learnings" not in jobs
    # a quiet job every tick keeps one row not one per tick
    scheduler = Scheduler(store, api.app.state.execution)
    for n in range(4):
        scheduler.tick(at + timedelta(seconds=10 * n))
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM system_runs WHERE job='snooze_ends'").fetchone()[0] == 1
    with store.transaction() as c:
        c.execute("DELETE FROM system_runs WHERE job='snooze_ends'")
    # a quiet repeat moves its row after the write interval and a busy run starts a new one
    long_ago = H.shift(H.now(), seconds=-(SR.QUIET_WRITE_SECONDS + 60))
    first = SR.Run("snooze_ends", started=long_ago).did("Nothing due", quiet=True)
    with store.transaction() as c:
        rid = SR.save(c, first, finished=first.started)
        moved = SR.Run("snooze_ends").did("Nothing due", quiet=True)
        assert SR.save(c, moved) == rid
        row = c.execute("SELECT runs,started_at FROM system_runs WHERE id=?", (rid,)).fetchone()
        assert row["runs"] == 2 and row["started_at"] == moved.started, "folded and moved forward"
        busy = SR.Run("snooze_ends").did("1 snooze ended")
        assert SR.save(c, busy) != rid
        assert c.execute("SELECT count(*) FROM system_runs WHERE job='snooze_ends'").fetchone()[0] == 2
    # old runs are pruned to the last ones per job
    with store.transaction() as c:
        for n in range(SR.KEEP + 5):
            run = SR.Run("cleanup", started=H.shift(H.now(), seconds=-n)).did(f"run {n}")
            SR.save(c, run)
        SR.prune(c)
        assert c.execute("SELECT count(*) FROM system_runs WHERE job='cleanup'").fetchone()[0] == SR.KEEP
    # an expired go live is recorded as ended not as nothing
    from backend.store import encode
    with store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES(?,?)", (go_live.PREFIX + "ops", encode(
            {"actor": "bot:ops", "computer": "Mac", "requested_at": H.shift(H.now(), days=-8)})))
    Scheduler(store, api.app.state.execution).tick()
    jobs, _ = rows(api)
    assert jobs["go_live_expiry"]["last"]["result"] == "1 go-live ended"


def test_a_failed_run_shows_a_plain_reason_and_each_wrapped_block_reports_its_old_failure_name(api, monkeypatch):
    store = api.app.state.store
    # a failed run shows failed with a plain reason and still counts as a scheduler failure

    def broken(conn, at=None):
        raise RuntimeError("Ana's private note text")
    monkeypatch.setattr(ask_deadlines, "sweep", broken)
    result = Scheduler(store, api.app.state.execution).tick()
    assert {"ask_deadlines": "RuntimeError"} in result["failures"]
    jobs, _ = rows(api)
    last = jobs["ask_deadlines"]["last"]
    assert last["ok"] is False and last["result"] == "Failed: unexpected error (RuntimeError)"
    assert "private" not in str(jobs), "an exception's own text never shows"
    # each wrapped block still reports its old failure name
    from backend import approval_expiry, billing_check, go_live, goal_asks, still_needed
    from backend import goals as G
    # The pass refreshes one live goal per transaction; an empty registry has no refresh to fail.
    with store.transaction() as c:
        G.create(c, "human:ana", "Exercise goal failure reporting", "company")
    store.settings.gateway_key = "synthetic"
    monkeypatch.setattr(billing_check, "env_key", lambda settings: "synthetic")

    def broken(*a, **k):
        raise RuntimeError("x")
    for module, name in ((G, "refresh"), (goal_asks, "sync"), (go_live, "expire"),
                         (ask_deadlines, "sweep"), (approval_expiry, "sweep"), (still_needed, "sweep"),
                         (billing_check, "read_later")):
        monkeypatch.setattr(module, name, broken)
    from backend import gateway_connection
    monkeypatch.setattr(gateway_connection, "check_soon", broken)
    later = datetime.now(timezone.utc) + timedelta(hours=2)     # the hourly blocks are due again
    failures = Scheduler(store, api.app.state.execution).tick(later)["failures"]
    for name in ("goals", "goal_asks", "go_live", "ask_deadlines", "approval_expiry",
                 "still_needed", "billing_check", "gateway"):
        assert {name: "RuntimeError"} in failures, name
    jobs, _ = rows(api)
    assert jobs["goal_colours"]["last"]["ok"] is False
    assert jobs["goal_colours"]["last"]["result"] == "Failed: unexpected error (RuntimeError)"


def test_a_stale_review_shows_its_route_spend_and_each_fallback(stale):
    # a stale review answered by the gateway shows gateway and this months spend
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
    # gateway down falls back to own key then company ai and the row says so

    task("Ship the export page", pr="merged")
    with store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='stale_review'")    # the next hour's pass

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


def test_learn_without_a_model_stays_in_maintenance_and_a_failed_directory_sync_shows_fixed_words(api, monkeypatch):
    store = api.app.state.store
    # learn with no decision model is recorded in maintenance
    from backend import learnings, maintenance
    monkeypatch.setattr(learnings, "engine_for", lambda *args: None)
    with store.transaction() as c:
        maintenance.save_step(c, "human:ana", {"key": "learn", "enabled": True})
    run, started = maintenance.run_now(store, api.app.state.auth, "human:ana", step="learn", wait=True)
    assert started
    [step] = run["steps"]
    assert step["status"] == "skipped" and step["reason"] == "no decision model"
    jobs, _ = rows(api)
    assert "learnings" not in jobs
    with store.read() as c:
        assert c.execute("SELECT status FROM learning_runs WHERE id=?", (run["id"],)).fetchone()[0] == "unconfigured"
        assert c.execute("SELECT count(*) FROM system_runs WHERE job='learnings'").fetchone()[0] == 0
    # a failed directory sync shows members fixed words never the vendors text
    from backend.store import Problem
    directory = api.app.state.directory
    monkeypatch.setattr(type(directory), "due", lambda self, c: True)
    # With no directory connected the list leaves the job out (Job.applies); here it is listed to show its failure.
    import dataclasses
    jobs = [dataclasses.replace(job, applies=None) if job.key == "directory_sync" else job for job in SR.JOBS]
    monkeypatch.setattr(SR, "JOBS", jobs)

    def refused(self, *a, **k):
        raise Problem("directory_entra", "Entra refused the request (403). AADSTS65001 tenant acme-secret app 1234", 502)
    monkeypatch.setattr(type(directory), "run", refused)
    directory.tick(True)
    jobs, _ = rows(api, token="ben-test")
    assert jobs["directory_sync"]["last"]["result"] == "Failed: Microsoft Entra refused the request (403)"
    assert "acme-secret" not in str(jobs) and "AADSTS" not in str(jobs)
