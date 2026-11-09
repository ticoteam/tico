#!/usr/bin/env python3
"""Stale PR keeper: brings back pull requests the merge queue dropped for a flake, and says why for the rest.

Hourly and on demand (.github/workflows/stale-pr-keeper.yml). For each open pull request to main:

1. Dropped by the queue (its last queue event is REMOVED_FROM_MERGE_QUEUE_EVENT and it is not back in the queue):
   - failed_checks, and every failure in the failing queue run is a quarantined test (ci/quarantine/, read as
     scripts/flake_quarantine.py reads it) or a pytest-xdist "worker ... crashed" in a pytest-only leg: re-enqueued
     (enqueuePullRequest with expectedHeadOid=<head>), at most once per head sha. Re-enqueueing needs QUEUE_TOKEN
     (GitHub refuses the workflow's own token); without it the comment says to re-enqueue by hand.
   - otherwise one comment per head sha naming the failing tests (or the job and step when no test is named). When the
     quarantine list or the queue run changes for that head, the same comment is updated, and a flake is re-enqueued.
   - merge_conflict is left to the label below; a manual removal is a person's choice and is left alone. A pull request
     pushed since its removal is left to its own checks. A queue run still finishing is read on a later run.
2. Conflicting with main and its own checks red: labelled needs-main-merge (a conflicting head runs no pull request
   checks, so nothing else can be judged until main is merged in). The label comes off once it merges cleanly again.
   Nothing merges main into a branch from CI.
3. Closes nothing. The run summary lists open pull requests with no push in 7 days (the head's first check suite, the
   GitHub record of its push; the commit date when it has none).

The keeper writes one comment per head, edited as things change. Its hidden marker
`<!-- stale-pr-keeper:comment <head> queue=<sha> quarantine=<hash>[ requeued][ deferred] -->` (after a line naming the
failed tests) records what it was written for, so a later run never repeats it, and never re-enqueues a head twice.
GitHub calls: one GraphQL page per 50 open pull requests; then, for dropped pull requests only, one GraphQL query per 25
for their markers, and only where the markers leave something to decide: the queue run (by the removal's merge group
commit), its jobs, and the log of each failed job (streamed; only summary lines are kept).
"""

import argparse
import ast
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
QUARANTINE = "ci/quarantine"
QUARANTINE_SCRIPT = "scripts/flake_quarantine.py"
WORKFLOW = "pr-checks.yml"
LABEL = "needs-main-merge"
STALE_DAYS = 7
BASE = "main"
TEST_STEP = "Run selected tests"
# The (job, step) pairs that run pytest alone, so pytest's summary tells the whole story of their failure. conformance
# also runs cargo tests in its "Run selected tests" step, and browser legs run UI scripts: neither can be a flake.
PYTEST_STEPS = [
    (re.compile(r"python-\d+|contracts"), TEST_STEP),
    (re.compile(r"caddy-linux-[\w-]+"), "Actual pinned Linux Caddy runtime and lifecycle"),
]
# A queue run whose failed test ids take more than this as JSON is a breakage, not flakes, and its comment's marker
# names only the tests that fit. Shown names are cut at MAX_SHOWN characters and MAX_LINES lines: a comment stays far
# under GitHub's 65,536 characters.
MAX_MARKED = 20_000
MAX_SHOWN = 300
MAX_LINES = 40
# The jobs that only report another job's failure: pr-checks.yml's required gate and its timing receipt.
REPORTING_JOBS = {"checks", "PR job timings"}
MARKER = re.compile(
    r"<!-- stale-pr-keeper:comment (?P<sha>[0-9a-f]{40})"
    r"(?: queue=(?P<queue>[0-9a-f]{40}|none))?(?: quarantine=(?P<quarantine>[0-9a-f]+))?"
    r"(?P<flags>(?: requeued| deferred)*) -->"
)
# The failed tests a comment named, as JSON (with "-->" escaped), on the line before its marker.
TESTS_LINE = re.compile(r"<!-- stale-pr-keeper:tests (?P<json>\[.*?\]) -->", re.S)
MAX_NAMED = 8
GONE = re.compile(r"HTTP 404|HTTP 410")  # an expired or deleted log; anything else is retried next run
# GitHub's primary and secondary rate limits (HTTP 403 or 429): the run stops reading and writing at once.
RATE_LIMITED = re.compile(r"rate limit|HTTP 429", re.I)
# An enqueue GitHub answered with a refusal that says why; any other failure (a timeout, a dropped connection, a garbled
# reply, an internal GraphQL error) may still have queued it, so its marker stays and the head is never re-enqueued.
REFUSED = re.compile(
    r"HTTP 4\d\d|(?i:not mergeable|unmergeable|already (?:queued|in the merge queue)|head branch was modified"
    r"|expected head|resource not accessible|does not have permission|could not resolve to a pullrequest"
    r"|no merge queue entry)"
)
# The removals whose queue run is read for the failing tests. Only failed_checks can be re-enqueued.
DIAGNOSED = ("failed_checks", "checks_timed_out")


class RateLimited(subprocess.CalledProcessError):
    """A gh call refused for the rate limit."""


def _checked(error):
    """The error to raise for a failed gh call: RateLimited when the rate limit refused it."""
    if RATE_LIMITED.search(error.stderr or ""):
        return RateLimited(error.returncode, error.cmd, error.output, error.stderr)
    return error


PRS_QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 50, after: $after, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id number title url isDraft baseRefName headRefOid mergeable isInMergeQueue
        labels(first: 30) { nodes { name } }
        commits(last: 1) {
          nodes { commit { committedDate statusCheckRollup { state } checkSuites(first: 10) { nodes { createdAt } } } }
        }
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
ENQUEUE = """
mutation($id: ID!, $head: GitObjectID!) {
  enqueuePullRequest(input: {pullRequestId: $id, expectedHeadOid: $head}) { mergeQueueEntry { id } }
}
"""
COMMENTS = (
    "comments(last: 100{before}) {{ pageInfo {{ hasPreviousPage startCursor }} "
    "nodes {{ databaseId viewerDidAuthor body }} }}"
)


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
    pushed: datetime | None  # when the head was pushed: its first check suite, else its commit date
    checks: str | None  # the head's status check rollup: SUCCESS, FAILURE, ERROR, PENDING, EXPECTED or None
    removed_reason: str | None = None  # set when the last queue event is a removal
    queue_commit: str | None = None  # the merge group commit the queue tested (the removal's beforeCommit)
    tested_head: str | None = None  # the pull request head that merge group merged, when it is a merge commit
    node_id: str | None = None  # the GraphQL id, for enqueuePullRequest


@dataclass
class Finding:
    """What one failed job of a queue run shows."""

    job: str
    step: str
    url: str
    attempt: int = 1
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
            suites = [_when(s["createdAt"]) for s in (commit.get("checkSuites") or {}).get("nodes", [])]
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
                pushed=min(suites) if suites else _when(commit.get("committedDate")),
                checks=rollup.get("state"),
                node_id=node.get("id"),
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


@dataclass
class Marker:
    sha: str  # the pull request head it was written for
    comment_id: int | None = None
    queue: str | None = None  # the merge group commit it diagnosed
    quarantine: str | None = None  # quarantine_key() of its failed tests when it was written
    requeued: bool = False  # this head has been re-enqueued
    deferred: bool = False  # a flake left for a person because there was no queue token
    tests: list = field(default_factory=list)  # the failed tests it named


def _marked_tests(body):
    hit = TESTS_LINE.search(body)
    try:
        return json.loads(hit.group("json")) if hit else []
    except ValueError:
        return []


def markers(comments):
    """The keeper's markers in [(comment id, body)]."""
    return [
        Marker(
            m["sha"],
            comment_id,
            m["queue"],
            m["quarantine"],
            " requeued" in m["flags"],
            " deferred" in m["flags"],
            _marked_tests(body),
        )
        for comment_id, body in comments
        for m in MARKER.finditer(body or "")
    ]


# Quarantine: ci/quarantine/<entry>.json, one per test (scripts/flake_quarantine.py, on chris/ci-flake-quarantine until
# it merges, writes and checks them). When that script is in the checkout its load() decides which entries count;
# until then the same rules are applied here. An id without [...] covers every parameter of that test.
FIELDS = ("test", "first_seen", "commit", "runs", "ticket", "reason")
REQUIRED = ("test", "first_seen", "runs", "ticket")


def entry_name(test):
    """flake_quarantine.entry_name: the readable id, at most 100 characters, and a short hash of the exact id."""
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", test).strip("-")[:100].rstrip("-")
    return f"{readable}-{hashlib.sha256(test.encode()).hexdigest()[:10]}.json"


def _valid_entry(path, data, root):
    if not isinstance(data, dict) or not isinstance(data.get("test"), str) or "::" not in data["test"]:
        return False
    if any(key not in data for key in REQUIRED) or set(data) - set(FIELDS):
        return False
    try:
        date.fromisoformat(str(data["first_seen"]))
    except ValueError:
        return False
    runs = data["runs"]
    if not isinstance(runs, list) or not runs or not all(isinstance(u, str) and u.startswith("https://") for u in runs):
        return False
    if any(key in data and not isinstance(data[key], str) for key in ("ticket", "reason", "commit")):
        return False
    return path.name == entry_name(data["test"]) and (root / data["test"].split("::", 1)[0]).is_file()


def quarantined(root=ROOT):
    """The quarantined test ids: flake_quarantine.load() when the checkout has it, else the same rules here."""
    root = Path(root)
    script = root / QUARANTINE_SCRIPT
    if script.is_file():
        spec = importlib.util.spec_from_file_location("tico_flake_quarantine_for_keeper", script)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return set(module.load(root))
    folder = root / QUARANTINE
    tests = set()
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if _valid_entry(path, data, root):
            tests.add(data["test"])
    return tests


def quarantine_key(failed, tests):
    """Which of a run's failed tests are quarantined, as a short hash: a quarantine change that touches none of them
    leaves a comment current, so it costs no reads."""
    marked = sorted({test for test in failed if is_quarantined(test, tests)})
    return hashlib.sha256("\n".join(marked).encode()).hexdigest()[:12]


def is_quarantined(nodeid, tests):
    return nodeid in tests or nodeid.split("[", 1)[0] in tests


# Job logs: GitHub prefixes each line with a timestamp (the first with a byte order mark); pytest colours its summary.
LOG_PREFIX = re.compile(r"^﻿?\d{4}-\d\d-\d\dT[\d:.]+Z ")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
SUMMARY_HEADER = re.compile(r"^=+ short test summary info =+$")
# A parameter id may hold " - " itself: the shortest bracketed id that leaves either the end or " - message" wins
# (as flake_quarantine.py reads it).
SUMMARY = re.compile(r"^(?:FAILED|ERROR) (?P<test>[^\s:]+\.py::[^\s\[]+(?:\[.*?\])?)(?: - (?P<message>.*))?$")
UNLISTED = re.compile(r"^(?:FAILED|ERROR) (?P<name>\S+)")  # a collection error: a file, not a test id
RESULTS = re.compile(r"^=*\s*\d+ (?:failed|passed|errors?|skipped|xfailed|xpassed|deselected)\b")
COUNTS = re.compile(r"(\d+) (failed|errors?)\b")
INTERNAL = re.compile(r"^INTERNALERROR")
# pytest-xdist's own words, the whole line or message, with the test id as Python's repr() writes it: a test that merely
# quotes them is not a crash.
CRASH = re.compile(r"""^worker 'gw\d+' crashed while running (?P<test>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")$""")


def crashed_test(text):
    """The test id an xdist worker crash names in `text`, or None."""
    hit = CRASH.match(text)
    if not hit:
        return None
    try:
        return ast.literal_eval(hit.group("test"))
    except (SyntaxError, ValueError):
        return None


UI_SCRIPTS = re.compile(r"^\d+ of \d+ UI scripts failed: (?P<names>.+?)(?: \(all .*\))?$")
SIM_HEADER = re.compile(r"^== (?P<scenario>\S+) seed (?P<seed>\d+)\b")
SIM_FAIL = re.compile(r"^\s+FAIL ")


def read_log(lines):
    """(tests, scripts, scenarios) a job log names: pytest's short test summary (FAILED and ERROR lines, each with
    whether an xdist worker crashed on it), the browser suite's failed UI scripts, and factory sim scenarios with a
    FAIL line. A collection error, an INTERNALERROR, or a summary listing fewer failures than its result line counts
    is named too, never as a crash, so it is never taken for a flake. Lines are read one at a time: a log can be
    hundreds of megabytes."""
    tests, crashes, scripts, scenarios, blockers = {}, {}, [], [], []
    in_summary, listed, scenario = False, 0, None
    for raw in lines:
        line = ANSI.sub("", LOG_PREFIX.sub("", raw)).rstrip()
        if INTERNAL.match(line):
            if "pytest INTERNALERROR" not in blockers:
                blockers.append("pytest INTERNALERROR")
            continue
        if SUMMARY_HEADER.match(line):
            in_summary, listed = True, 0
            continue
        if in_summary:
            hit = SUMMARY.match(line)
            if hit:
                crashed = crashed_test(hit.group("message") or "") == hit.group("test")
                tests[hit.group("test")] = tests.get(hit.group("test"), True) and crashed
                listed += 1
                continue
            other = UNLISTED.match(line)
            if other:
                blockers.append(other.group("name"))
                listed += 1
                continue
            if RESULTS.match(line):
                counted = sum(int(n) for n, _ in COUNTS.findall(line))
                if counted > listed:
                    blockers.append(f"{counted - listed} failure(s) the summary does not list")
                in_summary = False
            continue
        crash = crashed_test(line)
        if crash:
            crashes.setdefault(crash, True)
        ui = UI_SCRIPTS.match(line)
        if ui:
            scripts += [name.strip() for name in ui.group("names").split(",") if name.strip() not in scripts]
        header = SIM_HEADER.match(line)
        if header:
            scenario = f"{header.group('scenario')} seed {header.group('seed')}"
        elif scenario and SIM_FAIL.match(line) and scenario not in scenarios:
            scenarios.append(scenario)
    if in_summary:
        blockers.append("pytest's summary, cut off")  # the log ends inside it: later failures are unknown
    if not tests and crashes:
        # A crash pytest never summarised (the session itself went down) still names its test, but what else failed
        # is unknown: never a flake.
        tests = crashes
        blockers.append("no pytest summary")
    return list(tests.items()) + [(name, False) for name in blockers if name not in tests], scripts, scenarios


def failed_jobs(jobs):
    """(job, failed step name) for each failed step of each job that failed on its own, across every attempt of the run
    (a rerun, whole or partial, after the removal does not undo the failure that removed it). A failed shard cancels its
    run (pr-checks.yml), so its job ends cancelled; its failed step still says failure. The `checks` gate only repeats
    it."""
    out = []
    for job in jobs:
        if job["name"] in REPORTING_JOBS:
            continue
        steps = [s["name"] for s in job.get("steps") or [] if s.get("conclusion") == "failure"]
        if job.get("conclusion") == "timed_out" or (not steps and job.get("conclusion") == "failure"):
            steps.append(job["conclusion"].replace("_", " "))
        out += [(job, step) for step in steps]
    return out


def flaky(findings, tests):
    """True when every failure is a quarantined test or a crashed xdist worker in a pytest-only leg, and there is at
    least one."""
    if not findings or not marked(findings)[1]:
        return False
    for f in findings:
        if not any(job.fullmatch(f.job) and f.step == step for job, step in PYTEST_STEPS) or f.log_missing:
            return False
        if not f.tests or f.scripts or f.scenarios:
            return False
        if not all(crashed or is_quarantined(test, tests) for test, crashed in f.tests):
            return False
    return True


def marked(findings):
    """(the failed test ids a comment's marker names, whether that is all of them)."""
    out, size = [], 2
    every = list(dict.fromkeys(test for f in findings for test, _ in f.tests))
    for test in every:
        size += len(_escaped(json.dumps(test))) + 2
        if size > MAX_MARKED:
            return out, False
        out.append(test)
    return out, True


def _escaped(text):
    """JSON safe inside an HTML comment."""
    return text.replace("-->", "--\\u003e")


def _short(name):
    return name if len(name) <= MAX_SHOWN else name[: MAX_SHOWN - 1] + "…"


def describe(findings, tests):
    """Markdown bullets naming each failure."""
    lines = []
    for f in findings:
        names = []
        for test, crashed in f.tests:
            note = " (worker crashed)" if crashed else " (quarantined)" if is_quarantined(test, tests) else ""
            names.append(f"`{_short(test)}`{note}")
        names += [f"`{_short(name)}` (UI script)" for name in f.scripts]
        names += [f"`{_short(name)}` (factory sim)" for name in f.scenarios]
        if not names:
            names = [f"{f.step} (no test named{'; log expired' if f.log_missing else ''})"]
        shown = names[:MAX_NAMED] + ([f"{len(names) - MAX_NAMED} more"] if len(names) > MAX_NAMED else [])
        job = f.job + (f" (attempt {f.attempt})" if f.attempt > 1 else "")
        lines += [f"- {name} in [{job}]({f.url})" for name in shown]
    if len(lines) > MAX_LINES:
        lines = lines[: MAX_LINES - 1] + [f"- {len(lines) - MAX_LINES + 1} more in the queue run"]
    return "\n".join(lines)


REASONS = {
    "failed_checks": "a check failed",
    "checks_timed_out": "its checks timed out",
    "invalid_merge_commit": "the queue could not build its merge commit",
}


def comment_body(pr, findings, tests, run_url, note="", requeued=False, deferred=False, header=None):
    """The keeper's one comment for a head. Its marker records the queue run and the failed tests it was written for,
    and whether the head was re-enqueued (requeued) or a flake was not re-enqueued for want of a token or because the
    pull request conflicted with main (deferred)."""
    why = REASONS.get(pr.removed_reason, pr.removed_reason.replace("_", " "))
    lines = [header or f"The merge queue removed this: {why}."]
    if findings:
        lines += ["", describe(findings, tests)]
    elif pr.removed_reason in DIAGNOSED:
        lines += ["", "No failed job found in the queue run." if run_url else "No queue run found for it."]
    if run_url:
        lines += ["", f"Queue run: {run_url}"]
    if note:
        lines += ["", note]
    failed = marked(findings)[0]
    flags = (" requeued" if requeued else "") + (" deferred" if deferred else "")
    marker = f"queue={pr.queue_commit or 'none'} quarantine={quarantine_key(failed, tests)}{flags}"
    named = _escaped(json.dumps(failed))
    lines += ["", f"<!-- stale-pr-keeper:tests {named} -->\n<!-- stale-pr-keeper:comment {pr.head} {marker} -->"]
    return "\n".join(lines)


def requeue_body(pr, findings, tests, run_url):
    header = "Back in the merge queue. The queue run failed only on flakes:"
    return comment_body(pr, findings, tests, run_url, requeued=True, header=header)


def needs_label(pr):
    """True to add needs-main-merge, False to remove it, None to leave the labels as they are."""
    if pr.draft or pr.base != BASE:
        return None
    if pr.mergeable == "CONFLICTING" and pr.checks in ("FAILURE", "ERROR"):
        return LABEL not in pr.labels or None
    if pr.mergeable == "MERGEABLE" and LABEL in pr.labels:
        return False
    return None


def dropped(pr):
    return not pr.draft and pr.base == BASE and not pr.in_queue and pr.removed_reason is not None


class GitHub:
    """`gh` calls against one repository. GH_TOKEN reads, comments and labels; queue_token (QUEUE_TOKEN) enqueues."""

    def __init__(self, repo, queue_token=None):
        self.repo = repo
        self.owner, self.name = repo.split("/", 1)
        self.queue_token = queue_token

    @property
    def can_enqueue(self):
        return bool(self.queue_token)

    def _gh(self, *args, env=None, stdin=None):
        try:
            return subprocess.run(
                ["gh", *args], capture_output=True, text=True, check=True, env=env, input=stdin
            ).stdout
        except subprocess.CalledProcessError as error:
            raise _checked(error) from None

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

    def _comment_pages(self, numbers, before=None):
        fields = " ".join(
            f"pr{n}: pullRequest(number: {int(n)}) {{ "
            + COMMENTS.format(before=f', before: "{before}"' if before else "")
            + " }"
            for n in numbers
        )
        query = f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {fields} }} }}"
        data = self.graphql(query, owner=self.owner, name=self.name)["data"]["repository"]
        return {n: (data.get(f"pr{n}") or {}).get("comments") or {"nodes": [], "pageInfo": {}} for n in numbers}

    def comments(self, heads):
        """{number: [(comment id, body)]} for each pull request in {number: head}: the last 100 comments, 25 pull
        requests a query, and older pages one pull request at a time only while that head has no marker yet. Only the
        keeper's own comments (the token's) count: a marker anyone else pastes is ignored."""
        out = {}
        numbers = list(heads)
        for start in range(0, len(numbers), 25):
            for n, comments in self._comment_pages(numbers[start : start + 25]).items():
                out[n] = [(c["databaseId"], c["body"]) for c in comments["nodes"] if c.get("viewerDidAuthor")]
                info = comments["pageInfo"]
                while info.get("hasPreviousPage") and not any(m.sha == heads[n] for m in markers(out[n])):
                    older = self._comment_pages([n], before=info["startCursor"])[n]
                    out[n] = [(c["databaseId"], c["body"]) for c in older["nodes"] if c.get("viewerDidAuthor")] + out[n]
                    info = older["pageInfo"]
        return out

    def queue_run(self, commit):
        """The newest pr-checks run of a merge group commit, or None."""
        data = self.api(
            f"repos/{self.repo}/actions/workflows/{WORKFLOW}/runs?event=merge_group&head_sha={commit}&per_page=10"
        )
        runs = sorted(data["workflow_runs"], key=lambda r: r["id"])
        return runs[-1] if runs else None

    def jobs(self, run_id):
        """Every attempt's jobs: a rerun after the removal must not hide the attempt that failed."""
        jobs, page = [], 1
        while True:
            data = self.api(f"repos/{self.repo}/actions/runs/{run_id}/jobs?filter=all&per_page=100&page={page}")
            jobs += data["jobs"]
            if len(jobs) >= data["total_count"] or not data["jobs"]:
                return jobs
            page += 1

    def read_job_log(self, job_id):
        """read_log() of a job's log, streamed. None when the log is gone (expired or deleted); any other error raises,
        so the pull request is read again next run."""
        command = ["gh", "api", "--allow-escape-sequences", f"repos/{self.repo}/actions/jobs/{job_id}/logs"]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            assert proc.stdout is not None and proc.stderr is not None
            found = read_log(line.decode(errors="replace") for line in proc.stdout)
            error = proc.stderr.read().decode(errors="replace")
        if proc.returncode == 0:
            return found
        if GONE.search(error):
            return None
        raise _checked(subprocess.CalledProcessError(proc.returncode, command, stderr=error))

    def enqueue(self, pr):
        """Adds the pull request to the merge queue only while its head is still the inspected one. enqueuePullRequest,
        not `gh pr merge`: that turns on auto-merge instead when the pull request cannot enter the queue right now, and
        an auto-merge request is no queue entry."""
        env = {**os.environ, "GH_TOKEN": self.queue_token or ""}
        args = ["api", "graphql", "-f", f"query={ENQUEUE}", "-f", f"id={pr.node_id}", "-f", f"head={pr.head}"]
        data = json.loads(self._gh(*args, env=env))
        if not ((data.get("data") or {}).get("enqueuePullRequest") or {}).get("mergeQueueEntry"):
            raise subprocess.CalledProcessError(1, ["gh", *args[:3]], stderr="no merge queue entry in the reply")

    def comment(self, number, body):
        """Posts a comment; its id."""
        payload = json.dumps({"body": body})  # through stdin: a body is no command line argument
        return json.loads(
            self._gh("api", f"repos/{self.repo}/issues/{number}/comments", "--input", "-", stdin=payload)
        )["id"]

    def edit_comment(self, comment_id, body):
        payload = json.dumps({"body": body})
        self._gh("api", "-X", "PATCH", f"repos/{self.repo}/issues/comments/{comment_id}", "--input", "-", stdin=payload)

    def delete_comment(self, comment_id):
        self._gh("api", "-X", "DELETE", f"repos/{self.repo}/issues/comments/{comment_id}")

    def add_label(self, number, label):
        """Adds the label, creating it first only when the repository has none by that name."""
        try:
            self._gh("api", f"repos/{self.repo}/issues/{number}/labels", "-f", f"labels[]={label}")
        except RateLimited:
            raise
        except subprocess.CalledProcessError as error:
            try:
                self._gh("api", f"repos/{self.repo}/labels/{label}")
            except RateLimited:
                raise
            except subprocess.CalledProcessError as missing:
                if "HTTP 404" not in (missing.stderr or ""):
                    raise error from None
                description = "Conflicts with main: merge main into the branch"
                self._gh(
                    "label", "create", label, "--repo", self.repo, "--color", "fbca04", "--description", description
                )
                self._gh("api", f"repos/{self.repo}/issues/{number}/labels", "-f", f"labels[]={label}")
                return
            raise error from None  # the label exists: the failure was something else

    def remove_label(self, number, label):
        self._gh("api", "-X", "DELETE", f"repos/{self.repo}/issues/{number}/labels/{label}")


def findings_for(gh, run):
    """A Finding per failed step. Each failed job's log is read once, for its first failed step: any step may run pytest
    (the Caddy leg does); only a PYTEST_STEPS step can make a flake."""
    out, read = [], set()
    for job, step in failed_jobs(gh.jobs(run["id"])):
        finding = Finding(job=job["name"], step=step, url=job["html_url"], attempt=job.get("run_attempt", 1))
        if job["id"] not in read:
            read.add(job["id"])
            found = gh.read_job_log(job["id"])
            if found is None:
                finding.log_missing = True
            else:
                finding.tests, finding.scripts, finding.scenarios = found
        out.append(finding)
    return out


def requeue(gh, pr, body, existing=None):
    """The requeued marker first, so a head is never re-enqueued twice: written into the head's comment, `existing`
    (comment id, body) when there is one. It is taken back only when GitHub answered with a refusal; any other failure
    may have queued it, so it stays (as it does under the rate limit, when another call would be refused too)."""
    if existing:
        gh.edit_comment(existing[0], body)
    else:
        comment_id = gh.comment(pr.number, body)
    try:
        gh.enqueue(pr)
    except subprocess.CalledProcessError as error:
        if not isinstance(error, RateLimited) and REFUSED.search(error.stderr or ""):
            try:
                if existing:
                    gh.edit_comment(*existing)
                else:
                    gh.delete_comment(comment_id)
            except RateLimited:
                raise
            except subprocess.CalledProcessError:
                pass  # the marker stays: this head is not re-enqueued again, and the error below says why
        raise


def _error(error):
    return (error.stderr or str(error)).strip()[:300]


def keep(gh, tests, now, dry_run=False):
    """(markdown summary, every action succeeded)."""
    prs = parse_prs(gh.open_prs())
    done = {"requeued": [], "commented": [], "labelled": [], "unlabelled": [], "skipped": [], "errors": []}

    halted = []  # set once the rate limit refuses a call: nothing more is read or written this run

    def act(kind, pr, call, note=""):
        line = f"#{pr.number} {pr.title}" + (f": {note}" if note else "")
        if dry_run:
            done[kind].append(f"{line} (dry run)")
            return
        if halted:
            return
        try:
            call()
        except subprocess.CalledProcessError as error:
            done["errors"].append(f"#{pr.number} {kind}: {_error(error)}")
            if isinstance(error, RateLimited):
                halted.append(pr.number)
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
    try:
        seen = gh.comments({pr.number: pr.head for pr in candidates}) if candidates and not halted else {}
    except subprocess.CalledProcessError as error:
        done["errors"].append(f"reading comments: {_error(error)}")
        candidates, seen = [], {}

    for pr in candidates:
        if halted:
            done["errors"].append(f"#{pr.number}: not read (rate limit)")
            continue
        mine = [m for m in markers(seen.get(pr.number, [])) if m.sha == pr.head]
        comment = mine[-1] if mine else None
        requeued = any(m.requeued for m in mine)
        current = (
            comment is not None
            and comment.queue == (pr.queue_commit or "none")
            and comment.quarantine == quarantine_key(comment.tests, tests)
            # a deferred flake is read again once it could be re-enqueued: a token was added, or the conflict is gone
            and not (comment.deferred and gh.can_enqueue and pr.mergeable == "MERGEABLE")
        )
        if current:  # a re-enqueue only stops another enqueue: a new failure is still diagnosed
            done["skipped"].append(f"#{pr.number}: already commented on {pr.head[:10]}")
            continue
        try:
            run = gh.queue_run(pr.queue_commit) if pr.queue_commit and pr.removed_reason in DIAGNOSED else None
            if run is not None and run.get("status", "completed") != "completed":
                done["skipped"].append(f"#{pr.number}: its queue run is still finishing")
                continue
            findings = findings_for(gh, run) if run else []
        except subprocess.CalledProcessError as error:
            # Reading failed (a GitHub error or the rate limit): nothing is written, and the next run tries again.
            done["errors"].append(f"#{pr.number} reading its queue run: {_error(error)}")
            if isinstance(error, RateLimited):
                halted.append(pr.number)
            continue
        run_url = run["html_url"] if run else None
        is_flake = pr.removed_reason == "failed_checks" and flaky(findings, tests)
        if is_flake and not requeued and pr.mergeable != "CONFLICTING" and gh.can_enqueue:
            body = requeue_body(pr, findings, tests, run_url)
            existing = (comment.comment_id, dict(seen[pr.number])[comment.comment_id]) if comment else None
            act("requeued", pr, lambda pr=pr, body=body, existing=existing: requeue(gh, pr, body, existing))
            continue
        note, deferred = "", False
        if requeued:
            note = "Already re-queued once for this commit, so not again."
        elif is_flake and pr.mergeable == "CONFLICTING":
            note, deferred = "Only flakes failed. It is re-enqueued once it no longer conflicts with main.", True
        elif is_flake:
            note, deferred = f"Only flakes failed. Re-enqueue it: `gh pr merge {pr.number}`.", True
        body = comment_body(pr, findings, tests, run_url, note, requeued, deferred)
        names = ", ".join(_short(t) for f in findings for t, _ in f.tests[:3]) or pr.removed_reason
        if comment is None:
            act("commented", pr, lambda pr=pr, body=body: gh.comment(pr.number, body), names)
        else:
            # The quarantine list or the queue run changed since the comment: it is brought up to date, not repeated.
            act("commented", pr, lambda c=comment, body=body: gh.edit_comment(c.comment_id, body), f"updated; {names}")

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
    gh = GitHub(args.repo, os.environ.get("QUEUE_TOKEN") or None)
    text, ok = keep(gh, quarantined(), datetime.now(timezone.utc), args.dry_run)
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
