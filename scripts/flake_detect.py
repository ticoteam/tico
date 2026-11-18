#!/usr/bin/env python3
"""Flaky-test detection. A flaky test is a bug: it is fixed at its root cause the same day, or deleted. Nothing here
quarantines, skips or marks a test.

A test that fails and passes on the same commit is flaky. The flip can happen on a leg's retry (the queue-flaky
artifact, scripts/pr_checks.py run --retry), on a rerun attempt, or across two runs of one commit.

  detect  reads PR-check runs and queue-flaky artifacts from GitHub (gh api) and lists each test that flipped, with its
          commit and the run attempts it failed and passed in. It exits 1 when it finds one, so the daily run
          (.github/workflows/flaky-tests.yml) goes red, and when GitHub cannot be read.
"""

import argparse
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

# One job with more failing tests than this is a breakage or an infrastructure fault (a lost runner, a full disk), not
# a set of flaky tests: its failures are reported apart, never as flakes.
MAX_FAILURES_PER_JOB = 5
WORKFLOW = "pr-checks.yml"
# The PR-check legs that run pytest (scripts/pr_checks.py run). Browser legs name no pytest ids.
TEST_JOB = re.compile(r"python-\d+|contracts|conformance|caddy-linux-(?:amd64|arm64)")


# GitHub's job logs prefix every line with a timestamp (the first with a byte order mark); pytest colours its summary.
LOG_PREFIX = re.compile(r"^﻿?\d{4}-\d\d-\d\dT[\d:.]+Z ")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# A parameter id may hold " - " itself: the shortest bracketed id that leaves either the end or " - message" wins.
SUMMARY = re.compile(r"^(?:FAILED|ERROR) (?P<test>[^\s:]+\.py::[^\s\[]+(?:\[.*?\])?)(?: - .*)?$")
FILES = "Test files: "  # pr_checks.run prints the files a leg runs on this line


def _lines(log):
    for line in log.splitlines():
        yield ANSI.sub("", LOG_PREFIX.sub("", line)).rstrip()


def failed_tests(log):
    """The node ids pytest's short test summary lists as FAILED or ERROR, in order, each once. A collection error
    (`ERROR path.py - ...`) names no test and is left out."""
    found = []
    for line in _lines(log):
        hit = SUMMARY.match(line)
        if hit and hit.group("test") not in found:
            found.append(hit.group("test"))
    return found


def test_files(log):
    """The files a leg ran, from the line pr_checks.run prints; None for a log without one (older runs)."""
    for line in _lines(log):
        if line.startswith(FILES):
            return line[len(FILES) :].split()
    return None


def tested_commit(log):
    """The commit a job tested: actions/checkout prints it after `git log -1 --format=%H`."""
    lines = list(_lines(log))
    for i, line in enumerate(lines[:-1]):
        if line.endswith("git log -1 --format=%H") and re.fullmatch(r"[0-9a-f]{40}", lines[i + 1].strip()):
            return lines[i + 1].strip()
    return None


def candidates(runs):
    """The finished runs a pass could share a commit with: a run with a rerun attempt, and each run of a head sha that
    two or more runs checked when not all of them passed. Other runs cannot show a flip, so they are not read."""
    done = [r for r in runs if r.get("conclusion") in ("success", "failure", "cancelled")]
    heads = {}
    for r in done:
        heads.setdefault((r["event"], r["head_sha"]), []).append(r)
    shared = {
        ident
        for group in heads.values()
        if len(group) > 1 and any(r["conclusion"] != "success" for r in group)
        for ident in (r["id"] for r in group)
    }
    return [r for r in done if r["run_attempt"] > 1 or r["id"] in shared]


def observations(gh, repo, runs, skipped=None):
    """One observation per attempt of each run: the commit it tested (`key`), its plan job, its pytest legs, and the
    tests that failed with the leg that failed them. A merge queue group's head sha is the commit it tested; a pull
    request run tested the branch merged into main, which only a job log names.

    An attempt GitHub cannot read (a 404, an expired artifact, a 5xx) is left out, with a warning on stderr and its url
    and reason appended to `skipped`. An auth or rate-limit failure (GhError.systemic) is raised: every later call would
    fail too."""
    out = []
    for r in runs:
        commit = None if r["event"] == "pull_request" else r["head_sha"]
        attempts = []
        for attempt in range(1, r["run_attempt"] + 1):
            try:
                jobs = gh.api(f"repos/{repo}/actions/runs/{r['id']}/attempts/{attempt}/jobs?per_page=100")["jobs"]
                plan = next((j["id"] for j in jobs if j["name"] == "plan"), None)
                legs = [
                    {
                        "id": j["id"],
                        "name": j["name"],
                        "conclusion": j["conclusion"],
                        "url": j["html_url"],
                        "started_at": j.get("started_at"),
                        "completed_at": j.get("completed_at"),
                    }
                    for j in jobs
                    if TEST_JOB.fullmatch(j["name"])
                ]
                for leg in legs:
                    # Every leg's report counts, failed legs included: a test that passed in a leg that failed on
                    # another test is still proof that it passed.
                    leg["passed"] = gh.results(r["id"], leg)
            except GhError as error:
                if error.systemic:
                    raise
                url = f"{r['html_url']}/attempts/{attempt}"
                print(f"warning: skipped {url}: {error}", file=sys.stderr)
                if skipped is not None:
                    skipped.append((url, str(error)))
                continue
            failed, crowded = {}, []
            for leg in legs:
                if leg["conclusion"] != "failure":
                    continue
                log = gh.log(leg["id"])
                if log is None:
                    continue
                commit = commit or tested_commit(log)
                names = failed_tests(log)
                if len(names) > MAX_FAILURES_PER_JOB:
                    crowded.append(leg["url"])
                    continue
                for name in names:
                    failed.setdefault(name, {"url": leg["url"], "leg": leg["name"]})
            attempts.append((attempt, plan, legs, failed, crowded))
        if commit is None:
            # A pull request run with no failed leg to read: any pytest leg's log names its merge commit.
            legs = next((legs for _, _, legs, _, _ in reversed(attempts) if legs), [])
            log = gh.log(legs[0]["id"]) if legs else None
            commit = tested_commit(log) if log else None
        for attempt, plan, legs, failed, crowded in attempts:
            out.append(
                {
                    "key": commit or f"run {r['id']}",
                    "run": r["id"],
                    "plan": plan,
                    "url": f"{r['html_url']}/attempts/{attempt}",
                    "created": r["created_at"][:10],
                    "legs": legs,
                    "failed": failed,
                    "crowded": crowded,
                }
            )
    return out


# The suites pr_checks.run selects with `-m slow` (scripts/pr_checks.py); the others run `not slow`.
def _passed(test, other):
    """Whether `other` (an attempt of the same commit) passed `test`: one of its legs' JUnit reports shows that exact
    testcase as passed. A file that passed, or a sibling test in it, proves nothing about `test`."""
    return any(test in (leg.get("passed") or ()) for leg in other["legs"])


def _record_flip(found, test, commit, failed, passes, created):
    flip = found.setdefault(test, {"commit": commit, "failed": [], "passed": [], "first_seen": created})
    if flip["commit"] != commit:
        return
    if failed not in flip["failed"]:
        flip["failed"].append(failed)
    flip["passed"] += [url for url in passes if url not in flip["passed"]]
    flip["first_seen"] = min(flip["first_seen"], created)


def flips(observed):
    """The tests that failed in one observation and passed in another of the same commit, by test id."""
    by_key = {}
    for o in observed:
        by_key.setdefault(o["key"], []).append(o)
    found = {}
    for key, group in by_key.items():
        for o in group:
            for test, failure in o["failed"].items():
                passes = [p["url"] for p in group if p is not o and _passed(test, p)]
                if passes:
                    _record_flip(found, test, key, failure["url"], passes, o["created"])
    return found


def nodeid_of(classname, name):
    """The pytest node id of a JUnit testcase: its classname is the dotted module path, then any classes (capitalized)."""
    files, classes = [], []
    for part in classname.split(".") if classname else []:
        (classes if classes or part[:1].isupper() else files).append(part)
    if not files:
        return name
    return "/".join(files) + ".py" + "".join(f"::{c}" for c in classes) + f"::{name}"


def passed_tests(report):
    """The node ids a JUnit report shows as passed: a testcase with no failure, error or skipped child."""
    passed = set()
    for case in ET.fromstring(report).iter("testcase"):
        if any(child.tag in ("failure", "error", "skipped") for child in case):
            continue
        passed.add(nodeid_of(case.get("classname", ""), case.get("name", "")))
    return passed


class GhError(RuntimeError):
    """A `gh api` call that failed, with gh's own message. `systemic` when no later call can succeed either: bad or
    missing credentials, no permission, or the rate limit (HTTP 401 or 403)."""

    SYSTEMIC = re.compile(r"HTTP 40[13]|Bad credentials|rate limit|gh auth login|authentication", re.IGNORECASE)

    def __init__(self, path, message):
        super().__init__(f"gh api {path}: {message.strip() or 'failed with no message'}")
        self.systemic = bool(self.SYSTEMIC.search(message))


class GitHub:
    """Read-only `gh api` calls. Job logs, which never change, are cached in `cache` when it is given."""

    def __init__(self, repo, cache=None):
        self.repo = repo
        self.cache = Path(cache) if cache else None

    def api(self, path, *, paginate=False):
        flags = ["--paginate", "--slurp"] if paginate else []
        done = subprocess.run(["gh", "api", *flags, path], capture_output=True, text=True, check=False)
        if done.returncode:
            raise GhError(path, done.stderr or done.stdout)
        return json.loads(done.stdout)

    def artifact(self, artifact, name):
        """Read one file from an artifact, if its archive is still available."""
        fetched = subprocess.run(["gh", "api", artifact["archive_download_url"]], capture_output=True, check=False)
        if fetched.returncode:
            return None
        try:
            with zipfile.ZipFile(io.BytesIO(fetched.stdout)) as archive:
                return archive.read(name)
        except (KeyError, zipfile.BadZipFile):
            return None

    def results(self, run_id, leg):
        """The node ids a leg's JUnit report shows as passed, from its `results-<leg>` artifact (uploaded by the leg, so
        it was made while the job ran: a re-run attempt makes its own). None when there is no report, and a leg with no
        report proves no test passed."""
        listed = self.api(f"repos/{self.repo}/actions/runs/{run_id}/artifacts?name=results-{leg['name']}&per_page=100")
        made = [
            a
            for a in listed.get("artifacts", [])
            if not a.get("expired")
            and leg.get("started_at")
            and leg.get("completed_at")
            and leg["started_at"] <= a["created_at"] <= leg["completed_at"]
        ]
        if not made:
            return None
        # The retry report is evidence of a flip. Only the first report supplies ordinary passes.
        report = self.artifact(made[-1], f"{leg['name']}.xml")
        if report is None:
            return None
        try:
            return passed_tests(report)
        except ET.ParseError:
            return None

    def log(self, job_id):
        cached = self.cache / f"{job_id}.log" if self.cache else None
        if cached and cached.is_file():
            return cached.read_text(errors="replace")
        result = subprocess.run(
            ["gh", "api", "--allow-escape-sequences", f"repos/{self.repo}/actions/jobs/{job_id}/logs"],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            return None  # expired or never written
        text = result.stdout.decode(errors="replace")
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(text)
        return text


def runs_since(gh, repo, days, today):
    """The PR-check runs created on each of the last `days` UTC days through today, one day per query (a listing stops
    at 1,000 runs)."""
    out = []
    for back in range(days, -1, -1):
        day = (date.fromisoformat(today) - timedelta(days=back)).isoformat()
        page = 1
        while True:
            data = gh.api(f"repos/{repo}/actions/workflows/{WORKFLOW}/runs?created={day}&per_page=100&page={page}")
            out += data["workflow_runs"]
            if len(data["workflow_runs"]) < 100 or page * 100 >= 1000:
                break
            page += 1
    return out


def _add_retry_flips(gh, repo, days, today, found):
    """Add the flips recorded by successful retries in recent merge queue legs."""
    cutoff = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    pages = gh.api(f"repos/{repo}/actions/artifacts?name=queue-flaky&per_page=100", paginate=True)
    for page in pages:
        for artifact in page["artifacts"]:
            created = artifact["created_at"][:10]
            run_id = (artifact.get("workflow_run") or {}).get("id")
            if artifact.get("expired") or not cutoff <= created <= today or not run_id:
                continue
            report = gh.artifact(artifact, "flaky.json")
            if report is None:
                continue
            try:
                records = json.loads(report)
            except ValueError:
                continue
            if not isinstance(records, list):
                continue
            url = f"https://github.com/{repo}/actions/runs/{run_id}"
            for record in records:
                if not isinstance(record, dict):
                    continue
                commit, tests = record.get("commit"), record.get("tests")
                if not isinstance(commit, str) or not commit or not isinstance(tests, list):
                    continue
                for test in tests:
                    if isinstance(test, str) and test:
                        _record_flip(found, test, commit, url, [url], created)


def detect(gh, repo, days, today):
    """(markdown, flips): each test that failed and passed on one commit, from PR-check runs and queue retries."""
    runs = candidates(runs_since(gh, repo, days, today))
    skipped = []
    observed = observations(gh, repo, runs, skipped)
    if skipped and len(skipped) == sum(r["run_attempt"] for r in runs):
        # Not one attempt could be read: GitHub is failing, not a run. Fail rather than report "no flips".
        raise GhError("(every run attempt)", f"all {len(skipped)} failed; the first: {skipped[0][1]}")
    found = flips(observed)
    _add_retry_flips(gh, repo, days, today, found)
    lines = [f"Read {len(runs)} PR-check run(s) that could flip, over {days + 1} day(s).", ""]
    if found:
        lines += [
            f"{len(found)} flaky test(s): each failed and passed on one commit. Fix each at its root cause today, or "
            "delete it (docs/releasing.md#who-fixes-a-red-or-flaky-test).",
            "",
        ]
        for test in sorted(found):
            flip = found[test]
            kind = "" if "::" in test else " (browser script)"
            failed = " ".join(flip["failed"])
            passed = " ".join(flip["passed"][:3])
            lines.append(f"- `{test}`{kind} on {flip['commit'][:10]}: failed in {failed}; passed in {passed}")
    else:
        lines.append("No test failed and passed on the same commit.")
    if skipped:
        lines += ["", f"Skipped {len(skipped)} run attempt(s) GitHub could not read:"]
        lines += [f"- {url}: {reason}" for url, reason in skipped]
    crowded = sorted({u for o in observed for u in o["crowded"]})
    if crowded:
        lines += ["", f"Jobs with more than {MAX_FAILURES_PER_JOB} failed tests (a breakage, not flakes):"]
        lines += [f"- {u}" for u in crowded]
    return "\n".join(lines) + "\n", found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    find = sub.add_parser("detect")
    find.add_argument("--repo", default="ticoteam/tico")
    find.add_argument("--days", type=int, default=1, help="days before today to read as well (UTC)")
    find.add_argument("--cache", help="keep job logs in this folder")
    find.add_argument("--today", default=datetime.now(timezone.utc).date().isoformat())
    args = parser.parse_args(argv)
    gh = GitHub(args.repo, args.cache)
    try:
        text, found = detect(gh, args.repo, args.days, args.today)
    except GhError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(text)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
