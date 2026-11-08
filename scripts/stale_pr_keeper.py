#!/usr/bin/env python3
"""Stale PR keeper: brings back pull requests the merge queue dropped for a flake, and says why for the rest.

Hourly and on demand (.github/workflows/stale-pr-keeper.yml). For each open pull request to main:

1. Dropped by the queue (its last queue event is REMOVED_FROM_MERGE_QUEUE_EVENT and it is not back in the queue):
   - failed_checks, and every failure in the failing queue run is a quarantined test (ci/quarantine/, the format
     scripts/flake_quarantine.py reads) or a pytest-xdist "worker ... crashed": re-enqueued (`gh pr merge N`), at most
     once per head sha.
   - otherwise one comment per head sha naming the failing tests (or the job and step when no test is named).
   - merge_conflict is left to the label below; a manual removal is a person's choice and is left alone. A pull request
     pushed since its removal is left to its own checks.
2. Conflicting with main and its own checks not green: labelled needs-main-merge. The label comes off once it merges
   cleanly again. Nothing merges main into a branch from CI.
3. Closes nothing. The run summary lists open pull requests with no push in 7 days.

Each re-enqueue and comment carries a hidden marker (`<!-- stale-pr-keeper:<kind> <head sha> -->`), so a later run
never repeats it for the same head. GitHub calls: one GraphQL page per 50 open pull requests, then, only for a dropped
pull request without a comment for its head: one GraphQL query for the markers, the queue run (by the removal's merge
group commit), its jobs, and the log of each failed job (streamed; only summary lines are kept).
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
QUARANTINE = "ci/quarantine"
WORKFLOW = "pr-checks.yml"
LABEL = "needs-main-merge"
STALE_DAYS = 7
BASE = "main"
TEST_STEP = "Run selected tests"
# The jobs that only report another job's failure: pr-checks.yml's required gate and its timing receipt.
REPORTING_JOBS = {"checks", "PR job timings"}
MARKER = re.compile(r"<!-- stale-pr-keeper:(?P<kind>requeue|comment) (?P<sha>[0-9a-f]{40}) -->")
MAX_NAMED = 8

PRS_QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 50, after: $after, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url isDraft baseRefName headRefOid mergeable isInMergeQueue
        labels(first: 30) { nodes { name } }
        commits(last: 1) { nodes { commit { committedDate statusCheckRollup { state } } } }
        timelineItems(last: 1, itemTypes: [ADDED_TO_MERGE_QUEUE_EVENT, REMOVED_FROM_MERGE_QUEUE_EVENT]) {
          nodes {
            __typename
            ... on RemovedFromMergeQueueEvent {
              createdAt reason
              beforeCommit { oid parents(first: 2) { nodes { oid } } }
            }
          }
        }
      }
    }
  }
}
"""


@dataclass
class PR:
    number: int
    title: str
    url: str
    draft: bool
    base: str
    head: str
    mergeable: str  # MERGEABLE, CONFLICTING or UNKNOWN
    in_queue: bool
    labels: list
    pushed: datetime | None  # the head commit's committer date
    checks: str | None  # the head's status check rollup: SUCCESS, FAILURE, ERROR, PENDING, EXPECTED or None
    removed_reason: str | None = None  # set when the last queue event is a removal
    queue_commit: str | None = None  # the merge group commit the queue tested (the removal's beforeCommit)
    tested_head: str | None = None  # the pull request head that merge group merged, when it is a merge commit


@dataclass
class Finding:
    """What one failed job of a queue run shows."""

    job: str
    step: str
    url: str
    tests: list = field(default_factory=list)  # [(pytest node id, worker crashed)]
    scripts: list = field(default_factory=list)  # browser UI scripts
    scenarios: list = field(default_factory=list)  # factory sim scenarios
    log_missing: bool = False


def _when(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def parse_prs(pages):
    """The open pull requests in GraphQL pages of PRS_QUERY."""
    out = []
    for page in pages:
        for node in page["data"]["repository"]["pullRequests"]["nodes"]:
            commit = (node["commits"]["nodes"] or [{}])[0].get("commit") or {}
            rollup = commit.get("statusCheckRollup") or {}
            pr = PR(
                number=node["number"],
                title=node["title"],
                url=node["url"],
                draft=node["isDraft"],
                base=node["baseRefName"],
                head=node["headRefOid"],
                mergeable=node["mergeable"],
                in_queue=node["isInMergeQueue"],
                labels=[label["name"] for label in node["labels"]["nodes"]],
                pushed=_when(commit.get("committedDate")),
                checks=rollup.get("state"),
            )
            events = node["timelineItems"]["nodes"]
            if events and events[-1]["__typename"] == "RemovedFromMergeQueueEvent":
                event = events[-1]
                pr.removed_reason = (event.get("reason") or "unknown").lower()
                before = event.get("beforeCommit") or {}
                pr.queue_commit = before.get("oid")
                parents = [p["oid"] for p in (before.get("parents") or {}).get("nodes", [])]
                # A queue merge commit's parents are [main, the pull request head]; a squash has one parent.
                pr.tested_head = parents[1] if len(parents) == 2 else None
            out.append(pr)
    return out


def markers(bodies):
    """{(kind, head sha)} from comment bodies."""
    return {(m["kind"], m["sha"]) for body in bodies for m in MARKER.finditer(body or "")}


# Quarantine: ci/quarantine/<entry>.json, one per test, {"test": "<pytest node id>", ...} (scripts/flake_quarantine.py
# on chris/ci-flake-quarantine writes and checks them). An id without [...] covers every parameter of that test.
def quarantined(root=ROOT):
    folder = Path(root) / QUARANTINE
    tests = set()
    if not folder.is_dir():
        return tests
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("test"), str) and "::" in data["test"]:
            tests.add(data["test"])
    return tests


def is_quarantined(nodeid, tests):
    return nodeid in tests or nodeid.split("[", 1)[0] in tests


# Job logs: GitHub prefixes each line with a timestamp (the first with a byte order mark); pytest colours its summary.
LOG_PREFIX = re.compile(r"^﻿?\d{4}-\d\d-\d\dT[\d:.]+Z ")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
SUMMARY_HEADER = re.compile(r"^=+ short test summary info =+$")
SUMMARY = re.compile(r"^(?:FAILED|ERROR) (?P<test>[^\s:]+\.py::.+?)(?: - (?P<message>.*))?$")
RESULTS = re.compile(r"^=*\s*\d+ (?:failed|passed|errors?|skipped|xfailed|xpassed|deselected)\b")
CRASH = re.compile(r"worker '[^']+' crashed while running '(?P<test>[^']+)'")
UI_SCRIPTS = re.compile(r"^\d+ of \d+ UI scripts failed: (?P<names>.+?)(?: \(all .*\))?$")
SIM_HEADER = re.compile(r"^== (?P<scenario>\S+) seed (?P<seed>\d+)\b")
SIM_FAIL = re.compile(r"^\s+FAIL ")


def read_log(lines):
    """(tests, scripts, scenarios) a job log names: pytest's short test summary (FAILED and ERROR lines, each with
    whether an xdist worker crashed on it), the browser suite's failed UI scripts, and factory sim scenarios with a
    FAIL line. Lines are read one at a time: a log can be hundreds of megabytes."""
    tests, crashes, scripts, scenarios = {}, {}, [], []
    in_summary, scenario = False, None
    for raw in lines:
        line = ANSI.sub("", LOG_PREFIX.sub("", raw)).rstrip()
        if SUMMARY_HEADER.match(line):
            in_summary = True
            continue
        if in_summary:
            hit = SUMMARY.match(line)
            if hit:
                crashed = bool(CRASH.search(hit.group("message") or ""))
                tests[hit.group("test")] = tests.get(hit.group("test"), True) and crashed
                continue
            if RESULTS.match(line):
                in_summary = False
            continue
        crash = CRASH.search(line)
        if crash:
            crashes.setdefault(crash.group("test"), True)
        ui = UI_SCRIPTS.match(line)
        if ui:
            scripts += [name.strip() for name in ui.group("names").split(",") if name.strip() not in scripts]
        header = SIM_HEADER.match(line)
        if header:
            scenario = f"{header.group('scenario')} seed {header.group('seed')}"
        elif scenario and SIM_FAIL.match(line) and scenario not in scenarios:
            scenarios.append(scenario)
    if not tests:
        # A crash pytest never summarised (the session itself went down) still names its test.
        tests = crashes
    return list(tests.items()), scripts, scenarios


def failed_jobs(jobs):
    """(job, failed step name) for each job of a run that failed on its own. A failed shard cancels its run
    (pr-checks.yml), so its job ends cancelled; its failed step still says failure. The `checks` gate only repeats it."""
    out = []
    for job in jobs:
        if job["name"] in REPORTING_JOBS:
            continue
        step = next((s["name"] for s in job.get("steps") or [] if s.get("conclusion") == "failure"), None)
        if step is None and job.get("conclusion") in ("failure", "timed_out"):
            step = job["conclusion"].replace("_", " ")
        if step is not None:
            out.append((job, step))
    return out


def flaky(findings, tests):
    """True when every failure is a quarantined test or a crashed xdist worker, and there is at least one."""
    if not findings:
        return False
    for f in findings:
        if f.step != TEST_STEP or f.log_missing or not f.tests or f.scripts or f.scenarios:
            return False
        if not all(crashed or is_quarantined(test, tests) for test, crashed in f.tests):
            return False
    return True


def describe(findings, tests):
    """Markdown bullets naming each failure."""
    lines = []
    for f in findings:
        names = []
        for test, crashed in f.tests:
            note = " (worker crashed)" if crashed else " (quarantined)" if is_quarantined(test, tests) else ""
            names.append(f"`{test}`{note}")
        names += [f"`{name}` (UI script)" for name in f.scripts]
        names += [f"`{name}` (factory sim)" for name in f.scenarios]
        if not names:
            names = [f"{f.step} (no test named{'; log expired' if f.log_missing else ''})"]
        shown = names[:MAX_NAMED] + ([f"{len(names) - MAX_NAMED} more"] if len(names) > MAX_NAMED else [])
        lines += [f"- {name} in [{f.job}]({f.url})" for name in shown]
    return "\n".join(lines)


REASONS = {
    "failed_checks": "a check failed",
    "checks_timed_out": "its checks timed out",
    "invalid_merge_commit": "the queue could not build its merge commit",
}


def comment_body(pr, findings, tests, run_url, requeued_before):
    why = REASONS.get(pr.removed_reason, pr.removed_reason.replace("_", " "))
    lines = [f"The merge queue removed this: {why}."]
    if findings:
        lines += ["", describe(findings, tests)]
    elif pr.removed_reason == "failed_checks":
        lines += ["", "No failed job found in the queue run." if run_url else "No queue run found for it."]
    if run_url:
        lines += ["", f"Queue run: {run_url}"]
    if requeued_before:
        lines += ["", "Already re-queued once for this commit, so not again."]
    lines += ["", f"<!-- stale-pr-keeper:comment {pr.head} -->"]
    return "\n".join(lines)


def requeue_body(pr, findings, tests, run_url):
    return "\n".join(
        [
            "Back in the merge queue. The queue run failed only on flakes:",
            "",
            describe(findings, tests),
            "",
            f"Queue run: {run_url}",
            "",
            f"<!-- stale-pr-keeper:requeue {pr.head} -->",
        ]
    )


def needs_label(pr):
    """True to add needs-main-merge, False to remove it, None to leave the labels as they are."""
    if pr.draft or pr.base != BASE:
        return None
    if pr.mergeable == "CONFLICTING" and pr.checks != "SUCCESS":
        return LABEL not in pr.labels or None
    if pr.mergeable == "MERGEABLE" and LABEL in pr.labels:
        return False
    return None


def dropped(pr):
    return not pr.draft and pr.base == BASE and not pr.in_queue and pr.removed_reason is not None


class GitHub:
    """`gh` calls against one repository."""

    def __init__(self, repo):
        self.repo = repo
        self.owner, self.name = repo.split("/", 1)

    def _gh(self, *args):
        return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout

    def api(self, path):
        return json.loads(self._gh("api", path))

    def graphql(self, query, **variables):
        args = ["api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            if value is not None:
                args += ["-f", f"{key}={value}"]
        return json.loads(self._gh(*args))

    def open_prs(self):
        pages, after = [], None
        while True:
            page = self.graphql(PRS_QUERY, owner=self.owner, name=self.name, after=after)
            pages.append(page)
            info = page["data"]["repository"]["pullRequests"]["pageInfo"]
            if not info["hasNextPage"]:
                return pages
            after = info["endCursor"]

    def comment_bodies(self, numbers):
        """{number: [body, ...]} for the last 100 comments of each pull request, 25 pull requests a query."""
        out = {}
        numbers = list(numbers)
        for start in range(0, len(numbers), 25):
            chunk = numbers[start : start + 25]
            fields = " ".join(
                f"pr{n}: pullRequest(number: {int(n)}) {{ comments(last: 100) {{ nodes {{ body }} }} }}" for n in chunk
            )
            query = (
                f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {fields} }} }}"
            )
            data = self.graphql(query, owner=self.owner, name=self.name)["data"]["repository"]
            for n in chunk:
                out[n] = [c["body"] for c in (data.get(f"pr{n}") or {"comments": {"nodes": []}})["comments"]["nodes"]]
        return out

    def queue_run(self, commit):
        """The newest pr-checks run of a merge group commit, or None."""
        data = self.api(
            f"repos/{self.repo}/actions/workflows/{WORKFLOW}/runs?event=merge_group&head_sha={commit}&per_page=10"
        )
        runs = sorted(data["workflow_runs"], key=lambda r: r["id"])
        return runs[-1] if runs else None

    def jobs(self, run_id):
        jobs, page = [], 1
        while True:
            data = self.api(f"repos/{self.repo}/actions/runs/{run_id}/jobs?per_page=100&page={page}")
            jobs += data["jobs"]
            if len(jobs) >= data["total_count"] or not data["jobs"]:
                return jobs
            page += 1

    def read_job_log(self, job_id):
        """read_log() of a job's log, streamed; None when the log is gone."""
        with subprocess.Popen(
            ["gh", "api", "--allow-escape-sequences", f"repos/{self.repo}/actions/jobs/{job_id}/logs"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as proc:
            assert proc.stdout is not None
            found = read_log(line.decode(errors="replace") for line in proc.stdout)
        return None if proc.returncode else found

    def enqueue(self, number):
        self._gh("pr", "merge", str(number), "--repo", self.repo)

    def comment(self, number, body):
        self._gh("api", f"repos/{self.repo}/issues/{number}/comments", "-f", f"body={body}")

    def add_label(self, number, label):
        try:
            self._gh("api", f"repos/{self.repo}/issues/{number}/labels", "-f", f"labels[]={label}")
        except subprocess.CalledProcessError:
            self._gh(
                "label",
                "create",
                label,
                "--repo",
                self.repo,
                "--force",
                "--color",
                "fbca04",
                "--description",
                "Conflicts with main: merge main into the branch",
            )
            self._gh("api", f"repos/{self.repo}/issues/{number}/labels", "-f", f"labels[]={label}")

    def remove_label(self, number, label):
        self._gh("api", "-X", "DELETE", f"repos/{self.repo}/issues/{number}/labels/{label}")


def findings_for(gh, run):
    out = []
    for job, step in failed_jobs(gh.jobs(run["id"])):
        finding = Finding(job=job["name"], step=step, url=job["html_url"])
        if step == TEST_STEP or job["name"].startswith("factory sim"):
            found = gh.read_job_log(job["id"])
            if found is None:
                finding.log_missing = True
            else:
                finding.tests, finding.scripts, finding.scenarios = found
        out.append(finding)
    return out


def requeue(gh, number, body):
    gh.comment(number, body)
    gh.enqueue(number)


def keep(gh, tests, now, dry_run=False):
    """(markdown summary, every action succeeded)."""
    prs = parse_prs(gh.open_prs())
    done = {"requeued": [], "commented": [], "labelled": [], "unlabelled": [], "skipped": [], "errors": []}

    def act(kind, pr, call, note=""):
        line = f"#{pr.number} {pr.title}" + (f": {note}" if note else "")
        if dry_run:
            done[kind].append(f"{line} (dry run)")
            return
        try:
            call()
        except subprocess.CalledProcessError as error:
            done["errors"].append(f"#{pr.number} {kind}: {(error.stderr or str(error)).strip()[:300]}")
            return
        done[kind].append(line)

    for pr in prs:
        want = needs_label(pr)
        if want is True:
            act("labelled", pr, lambda pr=pr: gh.add_label(pr.number, LABEL))
        elif want is False:
            act("unlabelled", pr, lambda pr=pr: gh.remove_label(pr.number, LABEL))

    candidates = []
    for pr in filter(dropped, prs):
        if pr.removed_reason in ("manual", "merged"):
            done["skipped"].append(f"#{pr.number}: removed from the queue as {pr.removed_reason}")
        elif pr.removed_reason == "merge_conflict":
            done["skipped"].append(f"#{pr.number}: removed for a merge conflict (labelled when it still conflicts)")
        elif pr.tested_head and pr.tested_head != pr.head:
            done["skipped"].append(f"#{pr.number}: pushed since the queue removed it; its own checks decide")
        else:
            candidates.append(pr)
    seen = gh.comment_bodies([pr.number for pr in candidates]) if candidates else {}

    for pr in candidates:
        marks = markers(seen.get(pr.number, []))
        if ("comment", pr.head) in marks:
            done["skipped"].append(f"#{pr.number}: already commented on {pr.head[:10]}")
            continue
        requeued_before = ("requeue", pr.head) in marks
        try:
            run = gh.queue_run(pr.queue_commit) if pr.queue_commit and pr.removed_reason == "failed_checks" else None
            findings = findings_for(gh, run) if run else []
        except subprocess.CalledProcessError as error:
            # Reading failed (a GitHub error or the rate limit): nothing is written, and the next run tries again.
            done["errors"].append(f"#{pr.number} reading its queue run: {(error.stderr or str(error)).strip()[:300]}")
            continue
        run_url = run["html_url"] if run else None
        if flaky(findings, tests) and not requeued_before and pr.mergeable != "CONFLICTING":
            # The marker goes first: a failed enqueue is reported here, and never repeated for the same head.
            body = requeue_body(pr, findings, tests, run_url)
            act("requeued", pr, lambda pr=pr, body=body: requeue(gh, pr.number, body))
            continue
        body = comment_body(pr, findings, tests, run_url, requeued_before)
        names = ", ".join(t for f in findings for t, _ in f.tests[:3]) or pr.removed_reason
        act("commented", pr, lambda pr=pr, body=body: gh.comment(pr.number, body), names)

    stale = sorted(
        (pr for pr in prs if pr.pushed and now - pr.pushed > timedelta(days=STALE_DAYS)), key=lambda pr: pr.pushed
    )
    return summary(done, stale, now, len(prs)), not done["errors"]


def summary(done, stale, now, total):
    titles = {
        "requeued": "Re-enqueued (flake)",
        "commented": "Commented",
        "labelled": f"Labelled {LABEL}",
        "unlabelled": f"Removed {LABEL}",
        "skipped": "Left alone",
        "errors": "Errors",
    }
    lines = ["## Stale PR keeper", "", f"{total} open pull request(s).", ""]
    for kind, title in titles.items():
        if done[kind]:
            lines += [f"### {title}", "", *[f"- {item}" for item in done[kind]], ""]
    lines += [f"### No push in {STALE_DAYS} days", ""]
    if stale:
        lines += ["| PR | Days since push | Draft |", "| --- | ---: | --- |"]
        for pr in stale:
            title = pr.title.replace("|", "\\|")
            lines.append(
                f"| [#{pr.number}]({pr.url}) {title} | {(now - pr.pushed).days} | {'yes' if pr.draft else ''} |"
            )
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default="ticoteam/tico")
    parser.add_argument("--dry-run", action="store_true", help="print what it would do; change nothing")
    args = parser.parse_args(argv)
    text, ok = keep(GitHub(args.repo), quarantined(), datetime.now(timezone.utc), args.dry_run)
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
