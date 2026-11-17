"""CI report from fixture data, without network or AWS."""

from argparse import Namespace
from datetime import datetime, timedelta, timezone
import json

import pytest

from scripts import ci_report


def job(started, completed, labels=("ubuntu-24.04",), runner="GitHub Actions 1", conclusion="success"):
    return dict(
        started_at=started, completed_at=completed, labels=list(labels), runner_name=runner, conclusion=conclusion
    )


def run(
    number,
    path="pr-checks.yml",
    event="pull_request",
    conclusion="success",
    created="2026-11-10T10:00:00Z",
    finished="2026-11-10T10:12:00Z",
    title="t",
    status="completed",
):
    return dict(
        id=number,
        path=f".github/workflows/{path}",
        event=event,
        conclusion=conclusion,
        status=status,
        created_at=created,
        run_started_at=created,
        updated_at=finished,
        display_title=title,
    )


def test_runner_kind_tells_runs_on_hosted_and_macos_apart():
    assert ci_report.runner_kind(job(None, None, ["runs-on=1/runner=4cpu-linux-x64"], "runs-on--i-0--x-2")) == "runs_on"
    assert ci_report.runner_kind(job(None, None, ["ubuntu-24.04"], "GitHub Actions 7")) == "hosted"
    assert ci_report.runner_kind(job(None, None, ["macos-latest"], "GitHub Actions 8")) == "hosted_macos"
    assert ci_report.runner_kind(job(None, None, ["self-hosted"], "box")) == "other"


def test_job_minutes_skip_unstarted_and_skipped_jobs():
    assert ci_report.job_minutes(job("2026-11-10T10:00:00Z", "2026-11-10T10:03:30Z")) == 3.5
    assert ci_report.job_minutes(job(None, None)) == 0
    assert ci_report.job_minutes(job("2026-11-10T10:00:00Z", "2026-11-10T10:03:00Z", conclusion="skipped")) == 0


def test_percentile_is_nearest_rank():
    assert ci_report.percentile([], 0.5) is None
    assert ci_report.percentile([5, 1, 3, 2, 4], 0.5) == 3
    assert ci_report.percentile(list(range(1, 11)), 0.9) == 9


def test_fail_rate_leaves_cancelled_out_of_the_share():
    rows = [
        run(1, conclusion="failure"),
        run(2, conclusion="timed_out"),
        run(3),
        run(4),
        run(5, conclusion="cancelled"),
    ]
    counts = ci_report.fail_rate(rows)
    assert (counts["failed"], counts["succeeded"], counts["cancelled"], counts["fail_pct"]) == (2, 2, 1, 50.0)
    assert ci_report.fail_rate([])["fail_pct"] is None


def fixture():
    runs = [
        run(1),
        run(2, conclusion="failure"),
        run(3, created="2026-11-10T11:00:00Z", finished="2026-11-10T11:30:00Z"),
        run(4, event="merge_group", created="2026-11-10T12:00:00Z", finished="2026-11-10T12:20:00Z"),
        run(5, event="merge_group", conclusion="failure"),
        run(6, path="ci.yml", event="workflow_dispatch", title="Validate a as v1 from v0"),
        run(7, path="ci.yml", event="workflow_dispatch", title="Validate b as v1 from v0 (incremental on a)"),
        run(8, path="remote-run.yml", event="workflow_dispatch"),
        run(9, status="in_progress", conclusion=None),
    ]
    jobs = {
        1: [
            job("2026-11-10T10:00:00Z", "2026-11-10T10:10:00Z"),
            job("2026-11-10T10:00:00Z", "2026-11-10T10:05:00Z", conclusion="skipped"),
        ],
        3: [job("2026-11-10T11:00:00Z", "2026-11-10T11:20:00Z", ["macos-latest"])],
        8: [job("2026-11-10T10:00:00Z", "2026-11-10T10:40:00Z", ["runs-on=8/runner=4cpu"], "runs-on--i-1--a-1")],
    }
    pulls = [
        {
            "number": 1,
            "mergedAt": "2026-11-10T13:00:00Z",
            "timelineItems": {"nodes": [{"createdAt": "2026-11-10T12:00:00Z"}]},
        },
        {"number": 2, "mergedAt": "2026-11-10T14:00:00Z", "timelineItems": {"nodes": []}},
    ]
    return runs, jobs, pulls


def test_summarize_counts_minutes_health_and_validations():
    runs, jobs, pulls = fixture()
    numbers = ci_report.summarize(runs, jobs, pulls, {"2026-11-10": 4.0, "2026-11-11": 5.0})
    assert numbers["merged_prs"] == 2
    assert (numbers["hosted_min"], numbers["hosted_macos_min"], numbers["runs_on_min"]) == (30, 20, 40)
    assert (numbers["hosted_min_per_pr"], numbers["runs_on_min_per_pr"]) == (15.0, 20.0)
    assert numbers["pr_checks"]["fail_pct"] == 33.3
    assert numbers["queue_checks"]["fail_pct"] == 50.0
    assert (numbers["pr_to_green_p50_min"], numbers["pr_to_green_p90_min"]) == (12.0, 30.0)
    assert (numbers["enqueue_to_merge_p50_min"], numbers["enqueue_to_merge_prs"]) == (60.0, 1)
    assert (numbers["full_validations"], numbers["incremental_validations"]) == (1, 1)
    assert (numbers["aws_usd_total"], numbers["aws_usd_per_day"]) == (9.0, 4.5)
    assert numbers["runs_not_completed"] == 1
    assert list(numbers["by_workflow"])[0] == "remote-run.yml"


def test_unread_aws_is_not_read_never_zero():
    runs, jobs, pulls = fixture()
    numbers = ci_report.summarize(runs, jobs, pulls, None)
    numbers.update(
        repo="acme/tico",
        since="2026-11-10T00:00:00Z",
        until="2026-11-17T00:00:00Z",
        not_read=["AWS Cost Explorer, profile tico-ci (NoCredentialsError)"],
    )
    text = ci_report.report(numbers)
    assert "| AWS $/day (tico-ci) | not read |" in text
    assert "Not read: AWS Cost Explorer, profile tico-ci (NoCredentialsError)." in text


def test_compare_prints_before_after_and_change():
    runs, jobs, pulls = fixture()
    after = ci_report.summarize(runs, jobs, pulls, {"2026-11-10": 4.0})
    after.update(repo="acme/tico", since="2026-11-10T00:00:00Z", until="2026-11-17T00:00:00Z", not_read=[])
    before = json.loads(json.dumps(after))
    before.update(since="2026-11-03T00:00:00Z", until="2026-11-10T00:00:00Z", hosted_min_per_pr=20.5)
    text = ci_report.report(after, before)
    assert "| Measure | Before | After | Change |" in text
    assert "| Hosted job-min per merged PR (target 150) | 20.5 | 15.0 | -5.5 |" in text


def test_window_defaults_to_seven_whole_days_and_rejects_reversed():
    since, until = ci_report.window(Namespace(since=None, until="2026-11-17", days=7))
    assert (since, until) == (datetime(2026, 11, 10, tzinfo=timezone.utc), datetime(2026, 11, 17, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        ci_report.window(Namespace(since="2026-11-17", until="2026-11-10", days=7))


def test_runs_halves_a_slice_over_the_search_cap():
    api = ci_report.GitHub("acme/tico", budget=100)
    seen = []

    def get(path, **params):
        seen.append(params["created"])
        wide = len(seen) == 1
        return {"total_count": 1500 if wide else 1, "workflow_runs": [] if wide else [{"id": len(seen)}]}

    api.get = get
    start = datetime(2026, 11, 10, tzinfo=timezone.utc)
    rows = api.runs(start, start + timedelta(days=2))
    assert [row["id"] for row in rows] == [2, 3]
    assert len(seen) == 3


def test_budget_is_a_hard_stop(monkeypatch):
    api = ci_report.GitHub("acme/tico", budget=0)
    with pytest.raises(ValueError, match="budget"):
        api.get("actions/runs")
