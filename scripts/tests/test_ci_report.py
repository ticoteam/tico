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
    assert "| AWS $/day | not read |" in text
    assert "Not read: AWS Cost Explorer, profile tico-ci (NoCredentialsError)." in text


def test_compare_prints_before_after_and_change():
    runs, jobs, pulls = fixture()
    after = ci_report.summarize(runs, jobs, pulls, {"2026-11-10": 4.0})
    after.update(repo="acme/tico", since="2026-11-10T00:00:00Z", until="2026-11-17T00:00:00Z", not_read=[])
    before = json.loads(json.dumps(after))
    before.update(since="2026-11-03T00:00:00Z", until="2026-11-10T00:00:00Z", hosted_min_per_pr=20.5)
    text = ci_report.report(after, before)
    assert "| Measure | Before | After | Change |" in text
    assert "| Hosted job-min per merged PR, all workflows | 20.5 | 15.0 | -5.5 |" in text


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


def step(name, conclusion):
    return {"name": name, "conclusion": conclusion}


def test_a_run_that_cancelled_itself_after_a_failed_suite_counts_as_failed():
    leg = job("2026-11-10T10:00:00Z", "2026-11-10T10:05:00Z", conclusion="cancelled")
    failed_leg = dict(leg, steps=[step("Run tests", "failure"), step(ci_report.FAIL_FAST_STEP, "success")])
    newer_push = dict(leg, steps=[step("Run tests", "cancelled"), step(ci_report.FAIL_FAST_STEP, "skipped")])
    checks_only = dict(leg, name="checks", steps=[step("Report", "failure")])
    runs = [run(1, conclusion="cancelled"), run(2, conclusion="cancelled"), run(3, conclusion="cancelled"), run(4)]
    counts = ci_report.fail_rate(runs, {1: [failed_leg], 2: [newer_push], 3: [checks_only]})
    assert (counts["failed"], counts["cancelled"], counts["succeeded"], counts["fail_pct"]) == (1, 2, 1, 50.0)


def test_jobs_copied_into_a_rerun_count_once():
    passed = dict(job("2026-11-10T10:00:00Z", "2026-11-10T10:10:00Z"), name="plan")
    failed = dict(job("2026-11-10T10:00:00Z", "2026-11-10T10:20:00Z", conclusion="failure"), name="tests")
    rerun = dict(job("2026-11-10T11:00:00Z", "2026-11-10T11:20:00Z"), name="tests")
    numbers = ci_report.summarize([run(1)], {1: [passed, failed, dict(passed), rerun]}, [{"number": 1}], None)
    assert numbers["hosted_min"] == 50
    assert numbers["pr_checks_hosted_min_per_pr"] == 50.0


def test_runner_kind_by_label_alone_and_by_runner_name_alone():
    assert ci_report.runner_kind(job(None, None, ["runs-on=9/runner=2cpu-linux-x64"], None)) == "runs_on"
    assert ci_report.runner_kind(job(None, None, [], "runs-on--i-2--b-1")) == "runs_on"


class FakeCostExplorer:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def client(self, service, region_name):
        assert (service, region_name) == ("ce", "us-east-1")
        return self

    def get_cost_and_usage(self, **params):
        self.calls.append(params)
        return self.pages.pop(0)


def cost_page(days, token=None):
    rows = [{"TimePeriod": {"Start": day}, "Total": {"UnblendedCost": {"Amount": str(usd)}}} for day, usd in days]
    return {"ResultsByTime": rows, **({"NextPageToken": token} if token else {})}


def test_aws_cost_reads_whole_days_usage_only_and_every_page():
    fake = FakeCostExplorer([cost_page([("2026-11-10", 4.0)], "next"), cost_page([("2026-11-11", 5.5)])])
    since = datetime(2026, 11, 10, tzinfo=timezone.utc)
    days = ci_report.aws_cost("tico-ci", since, since + timedelta(days=2), tag="team=ci", session=fake)
    assert days == {"2026-11-10": 4.0, "2026-11-11": 5.5}
    first, second = fake.calls
    assert first["TimePeriod"] == {"Start": "2026-11-10", "End": "2026-11-12"}
    assert first["Filter"] == {
        "And": [
            {"Tags": {"Key": "team", "Values": ["ci"]}},
            {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Usage"]}},
        ]
    }
    assert second["NextPageToken"] == "next"


def test_aws_cost_skips_a_partial_first_day_and_can_keep_credits():
    fake = FakeCostExplorer([cost_page([])])
    since = datetime(2026, 11, 10, 6, tzinfo=timezone.utc)
    ci_report.aws_cost("p", since, datetime(2026, 11, 12, tzinfo=timezone.utc), usage_only=False, session=fake)
    assert fake.calls[0]["TimePeriod"] == {"Start": "2026-11-11", "End": "2026-11-12"}
    assert "Filter" not in fake.calls[0]


def test_github_reads_stop_one_second_before_the_window_ends():
    api = ci_report.GitHub("acme/tico", budget=100)
    seen = []
    api.get = lambda path, **params: seen.append(params["created"]) or {"total_count": 0, "workflow_runs": []}
    queries = []

    def graphql(query, **variables):
        queries.append(variables["q"])
        return {"search": {"issueCount": 0, "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}

    api.graphql = graphql
    start = datetime(2026, 11, 10, tzinfo=timezone.utc)
    api.runs(start, start + timedelta(days=1))
    api.merged_pulls(start, start + timedelta(days=2))
    assert seen == ["2026-11-10T00:00:00Z..2026-11-10T23:59:59Z"]
    assert [q.split("merged:")[1] for q in queries] == [
        "2026-11-10T00:00:00Z..2026-11-10T23:59:59Z",
        "2026-11-11T00:00:00Z..2026-11-11T23:59:59Z",
    ]


def test_only_the_latest_attempt_decides_whether_a_cancel_was_a_failure():
    def leg(attempt, *steps):
        return dict(
            job("2026-11-10T10:00:00Z", "2026-11-10T10:05:00Z", conclusion="cancelled"),
            run_attempt=attempt,
            steps=list(steps),
        )

    old_failure = leg(1, step("Run tests", "failure"), step(ci_report.FAIL_FAST_STEP, "success"))
    newer_push = leg(2, step("Run tests", "cancelled"), step(ci_report.FAIL_FAST_STEP, "skipped"))
    latest_fail_fast = leg(2, step("Run tests", "failure"), step(ci_report.FAIL_FAST_STEP, "success"))
    cancelled = dict(run(1, conclusion="cancelled"), run_attempt=2)
    counts = ci_report.fail_rate([cancelled], {1: [old_failure, newer_push]})
    assert (counts["failed"], counts["cancelled"], counts["fail_pct"]) == (0, 1, None)
    counts = ci_report.fail_rate([cancelled], {1: [old_failure, latest_fail_fast]})
    assert (counts["failed"], counts["cancelled"], counts["fail_pct"]) == (1, 0, 100.0)
