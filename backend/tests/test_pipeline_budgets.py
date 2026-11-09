"""Per-pipeline spend caps (backend/pipeline_budgets.py)."""

from datetime import datetime, timezone

import pytest

from backend import hubdb as H, pipeline_budgets
from backend.store import Problem
from backend.tests.helpers.api import get, headers
from backend.tests.helpers.pipeline_settings_j import make_pipeline, settings

COPY = "Enter an amount above zero, or leave it blank."
AT = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def pipeline_task(c, pipeline_id, name="Run"):
    return H.task_create(
        c, H.KEEPER, name, "Synthetic task", "bot:ops", type=pipeline_id, deduplicate=False, lint=False
    )


def finished_run(c, task_id, run_id, cost, finished_at):
    c.execute(
        "INSERT INTO attempts(id,bot_id,task_id,created_at,settled_at,est_cost_usd) VALUES(?,?,?,?,?,?)",
        (run_id, "ops", task_id, finished_at, finished_at, cost),
    )


def decision_tasks(c, pipeline_name):
    return c.execute(
        "SELECT id,owner_id,body,status FROM tasks WHERE title=? ORDER BY created_at",
        (f"Pipeline cap decision: {pipeline_name} (daily)",),
    ).fetchall()


def test_caps_are_owner_admin_only_and_reject_bad_values_without_changing_storage(api):
    pipeline = make_pipeline(api, "Caps")
    settings(api, pipeline, {"daily_cap_usd": 12.5, "monthly_cap_usd": None})

    settings(api, pipeline, {"daily_cap_usd": 99}, token="cara-test", expected=403)
    for invalid in (0, -1, "12.5", 1e-7):
        response = api.patch(
            f"/api/v2/pipelines/{pipeline}/settings", json={"daily_cap_usd": invalid}, headers=headers("ana-test")
        )
        assert response.status_code == 422
        assert COPY in response.json()["error"]["detail"]
        with api.app.state.store.read() as c:
            assert (
                c.execute("SELECT daily_cap_nano FROM task_types WHERE id=?", (pipeline,)).fetchone()[0]
                == 12_500_000_000
            )

    # A pipeline manager may decide what to do, but cannot edit the cap.
    settings(api, pipeline, {"manager_id": "ops"})
    with api.app.state.store.read() as c, pytest.raises(Problem) as exc:
        from backend.pipeline_requirements import update_settings

        update_settings(c, "bot:ops", pipeline, {"daily_cap_usd": 20})
    assert exc.value.status == 403


def test_spend_read_is_pipeline_private_and_counts_finished_model_and_priced_ci_in_utc(api):
    pipeline = make_pipeline(api, "Spend")
    other = make_pipeline(api, "Other")
    settings(api, pipeline, {"daily_cap_usd": 20, "monthly_cap_usd": 40})
    with api.app.state.store.transaction() as c:
        task = pipeline_task(c, pipeline)
        other_task = pipeline_task(c, other, "Other run")
        finished_run(c, task["id"], "model-now", 3.25, "2026-11-09T11:00:00")
        finished_run(c, task["id"], "model-yesterday", 80, "2026-11-08T23:59:59")
        finished_run(c, other_task["id"], "other-model", 50, "2026-11-09T11:30:00")
        c.execute(
            "INSERT INTO ci_runs(kind,status,task_id,cost_cents,seen_at,created_at) VALUES(?,?,?,?,?,?)",
            ("builder", "recorded", task["id"], 475, "2026-11-09T11:45:00", "2026-11-09T11:45:00"),
        )
        c.execute(
            "INSERT INTO ci_runs(kind,status,task_id,cost_cents,seen_at,created_at) VALUES(?,?,?,?,?,?)",
            ("builder", "recorded", task["id"], 900, "", "2026-11-09T11:50:00"),
        )

    spend = get(api, f"pipelines/{pipeline}/spend", token="cara-test")
    assert spend == {
        "daily_cap_usd": 20.0,
        "monthly_cap_usd": 40.0,
        "today_usd": 8.0,
        "month_usd": 88.0,
    }

    settings(api, pipeline, {"visibility": "members", "members": ["human:ana"]})
    get(api, f"pipelines/{pipeline}/spend", token="cara-test", expected=404)
    assert get(api, f"pipelines/{other}/spend")["today_usd"] == 50.0


def test_threshold_notices_and_decisions_are_once_per_cap_and_only_hold_that_pipeline(api):
    from backend import execution

    pipeline = make_pipeline(api, "Alerts")
    other = make_pipeline(api, "Other queue")
    settings(api, pipeline, {"daily_cap_usd": 10, "manager_id": "ops"})
    with api.app.state.store.transaction() as c:
        task = pipeline_task(c, pipeline)
        other_task = pipeline_task(c, other)
        finished_run(c, task["id"], "at-80", 8, "2026-11-09T11:00:00")
        pipeline_budgets.after_run(c, task["id"], AT)
        pipeline_budgets.after_run(c, task["id"], AT)
        notice = c.execute(
            "SELECT count(*) FROM messages WHERE recipient_id='bot:ops' AND kind='notice' AND body=?",
            ("Alerts is at 80% of its daily cap.",),
        ).fetchone()[0]
        assert notice == 1

        finished_run(c, task["id"], "at-100", 2, "2026-11-09T11:30:00")
        pipeline_budgets.after_run(c, task["id"], AT)
        pipeline_budgets.after_run(c, task["id"], AT)
        decisions = decision_tasks(c, "Alerts")
        assert len(decisions) == 1 and decisions[0]["owner_id"] == "bot:ops"
        assert all(choice in decisions[0]["body"] for choice in ("Ask Chris to raise", "Pause", "Reprioritize"))
        assert pipeline_budgets.hold_reason(c, task["id"], AT) == "pipeline_cap_daily"
        assert pipeline_budgets.hold_reason(c, other_task["id"], AT) is None
        queued = c.execute(
            "SELECT j.* FROM jobs j JOIN messages m ON m.id=j.message_id "
            "WHERE json_extract(m.refs,'$.task')=? AND j.bot_id='ops' AND j.state='queued'",
            (task["id"],),
        ).fetchall()
        other_queued = c.execute(
            "SELECT j.* FROM jobs j JOIN messages m ON m.id=j.message_id "
            "WHERE json_extract(m.refs,'$.task')=? AND j.bot_id='ops' AND j.state='queued'",
            (other_task["id"],),
        ).fetchall()
        assert queued and all(
            execution.job_refusal(c, dict(job), {}, {}, set(), set(), set()) == "pipeline_cap_daily" for job in queued
        )
        assert other_queued and all(
            execution.job_refusal(c, dict(job), {}, {}, set(), set(), set()) is None for job in other_queued
        )

    # Queue diagnostics call this on read-only snapshots; explaining a hold must not create notices or tasks.
    with api.app.state.store.read() as c:
        assert pipeline_budgets.hold_reason(c, task["id"], AT) == "pipeline_cap_daily"
        assert pipeline_budgets.hold_reason(c, other_task["id"], AT) is None


def test_lowering_cap_holds_at_once_and_ask_chris_keeps_hold_until_owner_raises(api):
    pipeline = make_pipeline(api, "Lowered")
    settings(api, pipeline, {"daily_cap_usd": 20, "manager_id": "ops"})
    with api.app.state.store.transaction() as c:
        task = pipeline_task(c, pipeline)
        finished_run(c, task["id"], "over-new-cap", 8, "2026-11-09T11:00:00")

    settings(api, pipeline, {"daily_cap_usd": 5})
    with api.app.state.store.transaction() as c:
        task = H.task(c, task["id"])
        decisions = decision_tasks(c, "Lowered")
        assert len(decisions) == 1
        assert pipeline_budgets.hold_reason(c, task["id"], AT) == "pipeline_cap_daily"
        H.task_update(c, "bot:ops", decisions[0]["id"], status="done", note="Ask Chris to raise")
        alert = c.execute(
            "SELECT request_task_id FROM pipeline_budget_alerts WHERE pipeline_id=? AND level=100", (pipeline,)
        ).fetchone()
        request = H.task(c, alert[0])
        assert request["owner_id"] == "human:ana"
        assert pipeline_budgets.hold_reason(c, task["id"], AT) == "pipeline_cap_daily"

    settings(api, pipeline, {"daily_cap_usd": 10})
    with api.app.state.store.transaction() as c:
        assert pipeline_budgets.hold_reason(c, task["id"], AT) is None
        assert H.task(c, decisions[0]["id"])["status"] == "done"


def test_daily_and_monthly_windows_turn_over_on_utc_boundaries(api):
    daily = make_pipeline(api, "Daily window")
    monthly = make_pipeline(api, "Monthly window")
    settings(api, daily, {"daily_cap_usd": 5, "monthly_cap_usd": 100})
    settings(api, monthly, {"daily_cap_usd": 10, "monthly_cap_usd": 5})
    with api.app.state.store.transaction() as c:
        daily_task = pipeline_task(c, daily, "Daily run")
        monthly_task = pipeline_task(c, monthly, "Monthly run")
        finished_run(c, daily_task["id"], "daily-hit", 5, "2026-11-09T23:59:59")
        finished_run(c, monthly_task["id"], "month-end", 5, "2026-11-30T23:59:59")
        nov9 = datetime(2026, 11, 9, 23, 59, tzinfo=timezone.utc)
        nov30 = datetime(2026, 11, 30, 23, 59, tzinfo=timezone.utc)
        dec1 = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)
        assert pipeline_budgets.hold_reason(c, daily_task["id"], nov9) == "pipeline_cap_daily"
        assert (
            pipeline_budgets.hold_reason(c, daily_task["id"], datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)) is None
        )
        assert pipeline_budgets.hold_reason(c, monthly_task["id"], nov30) == "pipeline_cap_monthly"
        assert pipeline_budgets.hold_reason(c, monthly_task["id"], dec1) is None


def test_no_manager_gets_one_notice_at_the_team_owner_and_ci_pricing_runs_the_threshold_check(api):
    from backend import ci_runs

    fallback = make_pipeline(api, "Fallback owner")
    ci_pipeline = make_pipeline(api, "CI spend")
    settings(api, fallback, {"daily_cap_usd": 10})
    settings(api, ci_pipeline, {"daily_cap_usd": 0.1, "manager_id": "ops"})
    with api.app.state.store.transaction() as c:
        fallback_task = pipeline_task(c, fallback, "Fallback run")
        finished_run(c, fallback_task["id"], "fallback-80", 8, "2026-11-09T11:00:00")
        pipeline_budgets.after_run(c, fallback_task["id"], AT)
        assert (
            c.execute(
                "SELECT count(*) FROM messages WHERE recipient_id='human:ana' AND kind='notice' AND body=?",
                ("Fallback owner is at 80% of its daily cap.",),
            ).fetchone()[0]
            == 1
        )

        ci_task = pipeline_task(c, ci_pipeline, "CI task")
        c.execute(
            "INSERT INTO ci_runs(kind,status,github_run_id,task_id,created_at) VALUES('ci','recorded','77',?,?)",
            (ci_task["id"], "2026-11-09T11:00:00"),
        )
        ci_runs._record(
            c,
            {"id": 77, "path": ".github/workflows/ci.yml", "event": "push"},
            [
                {
                    "started_at": "2026-11-09T11:00:00Z",
                    "completed_at": "2026-11-09T11:50:00Z",
                    "labels": ["runs-on=1/runner=4cpu-linux-x64"],
                }
            ],
            0.0016,
            "2026-11-09T12:00:00Z",
        )
        assert pipeline_budgets.spend(c, ci_pipeline, "daily", AT) == pytest.approx(0.08)
        assert (
            c.execute(
                "SELECT count(*) FROM messages WHERE recipient_id='bot:ops' AND kind='notice' AND body=?",
                ("CI spend is at 80% of its daily cap.",),
            ).fetchone()[0]
            == 1
        )
