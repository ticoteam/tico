#!/usr/bin/env python3
"""Weekly CI report: job-minutes per merged PR, PR and queue health, full validations and AWS $/day.

Read-only. GitHub through `gh api` (REST and GraphQL), AWS through Cost Explorer (boto3, the `tico-ci` profile by
default). A source that cannot be read is listed under "Not read" and its rows say so; it is never counted as zero.

What each row means (the window is [--since, --until), UTC, by creation time):
- Job-minutes: every completed job of every workflow run created in the window, all attempts, from its start to its end.
  RunsOn is a job whose runner is `runs-on--...` or whose label is `runs-on=...`; hosted is GitHub's own runners
  (macOS also shown alone: GitHub bills it at 10x Linux on a private repository). Per merged PR divides by the PRs
  merged into main in the window.
- PR fail % and queue fail %: completed pr-checks.yml runs on `pull_request` and on `merge_group`, failed (failure,
  timed_out, startup_failure) over failed plus succeeded. Cancelled and skipped runs are counted apart. A run's
  latest attempt only.
- PR to green: a successful pull_request pr-checks.yml run, from its attempt's start to its end (the wait after a push).
- Enqueue to merge: a PR merged in the window, from the first time it entered the merge queue to its merge (drops
  and re-enqueues included).
- Full validations: ci.yml runs dispatched in the window whose title is not "(incremental on ...)"; incremental apart.
- AWS $/day: Cost Explorer UnblendedCost over the window's whole UTC days, total over days.

Every CI change posts its before and after from this: `--json before.json` on the week before the change, then
`--compare before.json` on the week after. GitHub's API allows about 5,000 calls an hour for a person's or bot's token
and 1,000 for an Actions token; a busy week takes 2,000 to 3,000 (one per run), so run it with a personal or bot token.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from urllib.parse import urlencode

FAILED = {"failure", "timed_out", "startup_failure"}
QUEUE_BRANCH = re.compile(r"^gh-readonly-queue/[^/]+/pr-(\d+)-")
INCREMENTAL = " (incremental on "
MERGED_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { number mergedAt
      timelineItems(itemTypes: [ADDED_TO_MERGE_QUEUE_EVENT], first: 1) {
        nodes { ... on AddedToMergeQueueEvent { createdAt } } } } }
  }
}
"""


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso(value):
    return f"{value:%Y-%m-%dT%H:%M:%SZ}"


class GitHub:
    """Bounded gh reads. Exhausting the budget fails rather than reporting partial numbers."""

    def __init__(self, repo, budget):
        self.repo, self.budget, self.calls = repo, budget, 0
        self.lock = threading.Lock()

    def _call(self, args):
        with self.lock:
            self.calls += 1
            if self.calls > self.budget:
                raise ValueError(f"GitHub API budget of {self.budget} calls exceeded (raise --budget)")
        done = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=120, check=False)
        if done.returncode:
            raise ValueError(f"GitHub read failed: {args[0] if args[0] != 'graphql' else 'graphql search'}")
        return json.loads(done.stdout)

    def get(self, path, **params):
        query = "?" + urlencode(params) if params else ""
        return self._call([f"repos/{self.repo}/{path}{query}"])

    def graphql(self, query, **variables):
        args = ["graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            if value is not None:
                args += ["-f", f"{key}={value}"]
        return self._call(args)["data"]

    def runs(self, since, until, **params):
        """Every workflow run created in [since, until). A slice over GitHub's 1,000-result cap is halved."""
        first = self.get(
            "actions/runs", per_page=100, page=1, created=f"{iso(since)}..{iso(until - timedelta(seconds=1))}", **params
        )
        if first["total_count"] > 1000:
            if until - since <= timedelta(minutes=10):
                raise ValueError("over 1,000 workflow runs in ten minutes")
            middle = since + (until - since) / 2
            return self.runs(since, middle, **params) + self.runs(middle, until, **params)
        rows, page = list(first["workflow_runs"]), 1
        while len(rows) < first["total_count"]:
            page += 1
            more = self.get(
                "actions/runs",
                per_page=100,
                page=page,
                created=f"{iso(since)}..{iso(until - timedelta(seconds=1))}",
                **params,
            )
            if not more["workflow_runs"]:
                break
            rows.extend(more["workflow_runs"])
        return rows

    def jobs(self, run_id):
        rows, page = [], 1
        while True:
            data = self.get(f"actions/runs/{run_id}/jobs", filter="all", per_page=100, page=page)
            rows.extend(data["jobs"])
            if len(data["jobs"]) < 100:
                return rows
            page += 1

    def merged_pulls(self, since, until):
        """PRs merged into main in the window, with the first time each entered the merge queue. One day per search."""
        rows, day = [], since
        while day < until:
            end = min(until, day + timedelta(days=1))
            q = f"repo:{self.repo} is:pr is:merged base:main merged:{iso(day)}..{iso(end - timedelta(seconds=1))}"
            after = None
            while True:
                data = self.graphql(MERGED_QUERY, q=q, after=after)["search"]
                if data["issueCount"] > 1000:
                    raise ValueError("over 1,000 merged PRs in one day")
                rows.extend(node for node in data["nodes"] if node)
                if not data["pageInfo"]["hasNextPage"]:
                    break
                after = data["pageInfo"]["endCursor"]
            day = end
        return rows


def runner_kind(job):
    labels = [str(label).lower() for label in job.get("labels") or []]
    name = job.get("runner_name") or ""
    if name.startswith("runs-on--") or any(label.startswith("runs-on=") for label in labels):
        return "runs_on"
    if any("macos" in label for label in labels):
        return "hosted_macos"
    if name.startswith("GitHub Actions") or any(label.startswith(("ubuntu", "windows")) for label in labels):
        return "hosted"
    return "other"


def job_minutes(job):
    if not job.get("started_at") or not job.get("completed_at") or job.get("conclusion") == "skipped":
        return 0.0
    return max(0.0, (timestamp(job["completed_at"]) - timestamp(job["started_at"])).total_seconds() / 60)


def percentile(values, share):
    """Nearest rank; None for no values."""
    values = sorted(values)
    if not values:
        return None
    return values[max(0, math.ceil(share * len(values)) - 1)]


def fail_rate(runs):
    counts = {"failed": 0, "succeeded": 0, "cancelled": 0, "other": 0}
    for run in runs:
        conclusion = run.get("conclusion")
        key = (
            "failed"
            if conclusion in FAILED
            else "succeeded"
            if conclusion == "success"
            else ("cancelled" if conclusion == "cancelled" else "other")
        )
        counts[key] += 1
    decided = counts["failed"] + counts["succeeded"]
    counts["fail_pct"] = round(100 * counts["failed"] / decided, 1) if decided else None
    return counts


def minutes(start, end):
    return (timestamp(end) - timestamp(start)).total_seconds() / 60


def summarize(runs, jobs_by_run, pulls, cost):
    """The report's numbers from plain API rows: no network here."""
    completed = [run for run in runs if run.get("status") == "completed"]
    totals = {"hosted": 0.0, "hosted_macos": 0.0, "runs_on": 0.0, "other": 0.0}
    by_workflow = {}
    for run in completed:
        name = Path(run.get("path") or "unknown").name.split("@")[0]
        row = by_workflow.setdefault(
            name, {"runs": 0, "hosted": 0.0, "hosted_macos": 0.0, "runs_on": 0.0, "other": 0.0}
        )
        row["runs"] += 1
        for job in jobs_by_run.get(run["id"], []):
            kind, spent = runner_kind(job), job_minutes(job)
            totals[kind] += spent
            row[kind] += spent
    merged = len(pulls)
    hosted = totals["hosted"] + totals["hosted_macos"]
    checks = [run for run in completed if Path(run.get("path") or "").name.split("@")[0] == "pr-checks.yml"]
    on_pr = [run for run in checks if run.get("event") == "pull_request"]
    in_queue = [run for run in checks if run.get("event") == "merge_group"]
    green = [
        minutes(run.get("run_started_at") or run["created_at"], run["updated_at"])
        for run in on_pr
        if run.get("conclusion") == "success"
    ]
    queued = []
    for pull in pulls:
        events = (pull.get("timelineItems") or {}).get("nodes") or []
        if events and events[0].get("createdAt") and pull.get("mergedAt"):
            queued.append(minutes(events[0]["createdAt"], pull["mergedAt"]))
    validations = [
        run
        for run in runs
        if Path(run.get("path") or "").name.split("@")[0] == "ci.yml" and run.get("event") == "workflow_dispatch"
    ]
    full = [run for run in validations if INCREMENTAL not in (run.get("display_title") or "")]

    def per_pr(value):
        return round(value / merged, 1) if merged else None

    def rounded(value):
        return None if value is None else round(value, 1)

    return {
        "merged_prs": merged,
        "runs": len(runs),
        "runs_not_completed": len(runs) - len(completed),
        "hosted_min": round(hosted),
        "hosted_macos_min": round(totals["hosted_macos"]),
        "runs_on_min": round(totals["runs_on"]),
        "other_min": round(totals["other"]),
        "hosted_min_per_pr": per_pr(hosted),
        "runs_on_min_per_pr": per_pr(totals["runs_on"]),
        "pr_checks": fail_rate(on_pr),
        "queue_checks": fail_rate(in_queue),
        "pr_to_green_p50_min": rounded(percentile(green, 0.5)),
        "pr_to_green_p90_min": rounded(percentile(green, 0.9)),
        "pr_to_green_runs": len(green),
        "enqueue_to_merge_p50_min": rounded(percentile(queued, 0.5)),
        "enqueue_to_merge_p90_min": rounded(percentile(queued, 0.9)),
        "enqueue_to_merge_prs": len(queued),
        "full_validations": len(full),
        "incremental_validations": len(validations) - len(full),
        "full_validations_green": sum(run.get("conclusion") == "success" for run in full),
        "aws_usd_total": None if cost is None else round(sum(cost.values()), 2),
        "aws_usd_per_day": None if not cost else round(sum(cost.values()) / len(cost), 2),
        "aws_days": None if cost is None else len(cost),
        "by_workflow": {
            name: {key: round(value) if isinstance(value, float) else value for key, value in row.items()}
            for name, row in sorted(
                by_workflow.items(), key=lambda item: -sum(v for k, v in item[1].items() if k != "runs")
            )
        },
    }


def aws_cost(profile, since, until, tag=None):
    """Daily UnblendedCost over the window's whole UTC days: {date: usd}. Raises on any failure."""
    import boto3  # Only this source needs it; a missing boto3 is reported as not read.

    first = since.date() if since.time() == time(0) else since.date() + timedelta(days=1)
    last = until.date()
    if first >= last:
        return {}
    client = boto3.Session(profile_name=profile).client("ce", region_name="us-east-1")
    params = dict(
        TimePeriod={"Start": first.isoformat(), "End": last.isoformat()}, Granularity="DAILY", Metrics=["UnblendedCost"]
    )
    if tag:
        key, _, value = tag.partition("=")
        params["Filter"] = {"Tags": {"Key": key, "Values": [value]}}
    days, token = {}, None
    while True:
        data = client.get_cost_and_usage(**params, **({"NextPageToken": token} if token else {}))
        for row in data["ResultsByTime"]:
            days[row["TimePeriod"]["Start"]] = float(row["Total"]["UnblendedCost"]["Amount"])
        token = data.get("NextPageToken")
        if not token:
            return days


ROWS = [
    ("Merged PRs (main)", "merged_prs", ""),
    ("Hosted job-min per merged PR (target 150)", "hosted_min_per_pr", ""),
    ("RunsOn job-min per merged PR", "runs_on_min_per_pr", ""),
    ("Hosted job-min, total", "hosted_min", ""),
    ("of which macOS", "hosted_macos_min", ""),
    ("RunsOn job-min, total", "runs_on_min", ""),
    ("PR checks fail %", "pr_checks.fail_pct", "%"),
    ("Merge queue fail %", "queue_checks.fail_pct", "%"),
    ("PR to green p50 (min)", "pr_to_green_p50_min", ""),
    ("PR to green p90 (min)", "pr_to_green_p90_min", ""),
    ("Enqueue to merge p50 (min)", "enqueue_to_merge_p50_min", ""),
    ("Enqueue to merge p90 (min)", "enqueue_to_merge_p90_min", ""),
    ("Full validations", "full_validations", ""),
    ("Incremental validations", "incremental_validations", ""),
    ("AWS $/day (tico-ci)", "aws_usd_per_day", "$"),
    ("AWS $ total", "aws_usd_total", "$"),
]


def lookup(numbers, key):
    value = numbers
    for part in key.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def show(value, unit):
    if value is None:
        return "not read" if unit == "$" else "n/a"
    if unit == "$":
        return f"${value:,.2f}"
    if unit == "%":
        return f"{value}%"
    return f"{value:,}" if isinstance(value, int) else f"{value:,.1f}"


def report(numbers, before=None):
    lines = [f"# CI report: {numbers['since'][:10]} to {numbers['until'][:10]} ({numbers['repo']})", ""]
    if before:
        lines += [
            f"Before: {before['since'][:10]} to {before['until'][:10]}.",
            "",
            "| Measure | Before | After | Change |",
            "|---|---:|---:|---:|",
        ]
    else:
        lines += ["| Measure | Value |", "|---|---:|"]
    for label, key, unit in ROWS:
        after = lookup(numbers, key)
        if before:
            prior = lookup(before, key)
            change = "" if after is None or prior is None else f"{after - prior:+,.1f}"
            lines.append(f"| {label} | {show(prior, unit)} | {show(after, unit)} | {change} |")
        else:
            lines.append(f"| {label} | {show(after, unit)} |")
    pr, queue = numbers["pr_checks"], numbers["queue_checks"]
    lines += [
        "",
        f"PR checks: {pr['failed']} failed, {pr['succeeded']} passed, {pr['cancelled']} cancelled. "
        f"Merge queue: {queue['failed']} failed, {queue['succeeded']} passed, {queue['cancelled']} cancelled. "
        f"{numbers['runs']} runs in the window, {numbers['runs_not_completed']} not yet completed (left out). "
        f"Enqueue to merge from {numbers['enqueue_to_merge_prs']} of {numbers['merged_prs']} merged PRs.",
        "",
        "| Workflow | Runs | Hosted min | macOS min | RunsOn min |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, row in numbers["by_workflow"].items():
        lines.append(f"| {name} | {row['runs']:,} | {row['hosted']:,} | {row['hosted_macos']:,} | {row['runs_on']:,} |")
    if numbers["not_read"]:
        lines += ["", "Not read: " + "; ".join(numbers["not_read"]) + "."]
    return "\n".join(lines) + "\n"


def window(args):
    if args.since:
        since = datetime.combine(date.fromisoformat(args.since), time(0), timezone.utc)
        until = (
            datetime.combine(date.fromisoformat(args.until), time(0), timezone.utc)
            if args.until
            else (since + timedelta(days=args.days))
        )
    else:
        until = datetime.combine(
            date.fromisoformat(args.until) if args.until else datetime.now(timezone.utc).date(), time(0), timezone.utc
        )
        since = until - timedelta(days=args.days)
    if since >= until:
        raise ValueError("--since must be before --until")
    return since, until


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7, help="window length (default 7 whole UTC days to today)")
    parser.add_argument("--since", help="first day, YYYY-MM-DD (UTC)")
    parser.add_argument("--until", help="day after the last, YYYY-MM-DD (UTC, exclusive; default today)")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "ticoteam/tico"))
    parser.add_argument("--aws-profile", default="tico-ci", help="AWS profile for Cost Explorer (default tico-ci)")
    parser.add_argument("--aws-tag", help="KEY=VALUE cost allocation tag filter (default: the whole account)")
    parser.add_argument("--no-aws", action="store_true", help="skip Cost Explorer (reported as not read)")
    parser.add_argument("--budget", type=int, default=6000, help="most GitHub API calls (default 6000)")
    parser.add_argument("--json", type=Path, help="also write the numbers here, for a later --compare")
    parser.add_argument("--compare", type=Path, help="an earlier --json: print it as Before beside this window")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be positive")
    try:
        since, until = window(args)
        before = json.loads(args.compare.read_text()) if args.compare else None
        api = GitHub(args.repo, args.budget)
        runs = api.runs(since, until)
        wanted = [run["id"] for run in runs if run.get("status") == "completed" and run.get("conclusion") != "skipped"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs_by_run = dict(zip(wanted, pool.map(api.jobs, wanted), strict=True))
        pulls = api.merged_pulls(since, until)
        not_read, cost = [], None
        if args.no_aws:
            not_read.append("AWS Cost Explorer (--no-aws)")
        else:
            try:
                cost = aws_cost(args.aws_profile, since, until, args.aws_tag)
            except Exception as error:  # noqa: BLE001 - any AWS failure is one "not read", never a zero
                not_read.append(f"AWS Cost Explorer, profile {args.aws_profile} ({type(error).__name__})")
        numbers = summarize(runs, jobs_by_run, pulls, cost)
        numbers.update(repo=args.repo, since=iso(since), until=iso(until), not_read=not_read, github_calls=api.calls)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"ci_report: cannot compute: {error}", file=sys.stderr)
        return 2
    markdown = report(numbers, before)
    print(markdown, end="")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as stream:
            stream.write(markdown)
    if args.json:
        args.json.write_text(json.dumps(numbers, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
