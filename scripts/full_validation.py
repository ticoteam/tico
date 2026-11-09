"""The trail of the full validation of main (.github/workflows/full-validation.yml): which tests fail now that passed
on the previous full run, and which pull requests merged between the two runs never ran them.

    python scripts/full_validation.py report --plan ci-plan.json --results ci-results --record record.json

A test is a pytest id (path::name) or a browser script (ui/tests/<name>.cjs). For each test failing now that passed on
the previous full run, every pull request merged between the two runs' commits is checked against the plans that
gated it: its merge queue group's (pr-checks.yml on the merge commit) and its pull request's last run. A pull request
whose plans all left the test out is one the selector skipped it for; one with no plan to read is listed as unknown.
The run summary lists both, and the issue ISSUE_TITLE gets the test, the commit range and those pull requests (a new
issue, or a comment on the open one). Nothing is reverted. Each run's record (its commit, failures and passes) is the
next run's previous one, written only once the run has reported. A test the run did not reach keeps the commit
where it last passed.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ISSUE_TITLE = "Full validation: tests the selector skipped"
WORKFLOW = "full-validation.yml"
RECORD = "full-validation-record"
PLAN_ARTIFACT = "pr-check-plan"
PR_WORKFLOW = ".github/workflows/pr-checks.yml"
LISTED = 50  # tests listed in the summary and the issue; an outage failing hundreds lists the first ones
MERGED = re.compile(r"^Merge pull request #(\d+)|\(#(\d+)\)$")


def outcomes(plan, results):
    """(failed, passed, missing): the tests that failed and passed in this run, and the suites with no results."""
    failed, passed, missing = set(), set(), []
    results = Path(results)
    for job in plan["matrix"]["include"]:
        suite = job["suite"]
        if suite.startswith("browser-"):
            path = results / f"{suite}.json"
            if not path.is_file():
                missing.append(suite)
                continue
            # ui-tests.cjs writes each passing script's seconds there: a planned script not in it failed.
            ok = set(json.loads(path.read_text()))
            for name in job["files"]:
                (passed if name in ok else failed).add(f"ui/tests/{name}.cjs")
        elif suite.startswith("python-") or suite == "contracts":
            path = results / f"{suite}.xml"
            if not path.is_file():
                missing.append(suite)
                continue
            for case in ET.parse(path).iter("testcase"):
                test = case_id(case)
                if case.find("failure") is not None or case.find("error") is not None:
                    failed.add(test)
                elif case.find("skipped") is None:
                    passed.add(test)
    return failed, passed - failed, missing


def case_id(case):
    """A junit (xunit1) testcase as its pytest id: path::name, or path::Class::name."""
    classname, name = case.get("classname", ""), case.get("name", "")
    path = case.get("file") or classname.replace(".", "/") + ".py"
    parts = classname.split(".")
    stem = Path(path).with_suffix("").name
    inner = parts[parts.index(stem) + 1 :] if stem in parts else []
    return "::".join([path, *inner, name])


def file_of(test):
    return test.split("::", 1)[0]


def included(plan, test):
    """Whether a pr-checks plan ran this test: its file was selected (python None: the full suite), or a job of the
    plan runs it (the contracts and conformance jobs run slow tests the default list leaves out)."""
    path = file_of(test)
    if path.startswith("ui/tests/") and path.endswith(".cjs"):
        return plan.get("browser") is None or Path(path).stem in plan["browser"]
    jobs = plan.get("matrix", {}).get("include", [])
    return plan.get("python") is None or path in plan["python"] or any(path in job.get("files", ()) for job in jobs)


def planned(plan, test):
    """Whether this run's plan has a job for the test's file or browser script (it still exists)."""
    path, jobs = file_of(test), plan["matrix"]["include"]
    if path.startswith("ui/tests/") and path.endswith(".cjs"):
        return any(Path(path).stem in job["files"] for job in jobs if job["suite"].startswith("browser-"))
    return any(path in job["files"] for job in jobs)


def passes(previous):
    """{test: the commit of the full run where it last passed} from a record."""
    if previous is None:
        return {}
    passed = previous.get("passed", {})
    return dict(passed) if isinstance(passed, dict) else dict.fromkeys(passed, previous["sha"])


def merged_prs(log):
    """[(sha, pr number or None, subject)] from `git log --first-parent --format=%H%x09%s` output, oldest first."""
    found = []
    for line in log.splitlines():
        sha, _, subject = line.partition("\t")
        if sha:
            match = MERGED.search(subject)
            found.append((sha, int(match.group(1) or match.group(2)) if match else None, subject))
    return found[::-1]


def trail(new, merges, plans_of):
    """{test: {"skipped": [(pr, sha)], "unknown": [(pr, sha)]}} for each newly failing test. plans_of(pr, sha) gives
    the plans that gated a merge (queue group and pull request, those still readable)."""
    plans = {sha: plans_of(pr, sha) for sha, pr, _ in merges}
    out = {}
    for test in sorted(new):
        entry = {"skipped": [], "unknown": []}
        for sha, pr, _ in merges:
            if not plans[sha]:
                entry["unknown"].append((pr, sha))
            elif not any(included(plan, test) for plan in plans[sha]):
                entry["skipped"].append((pr, sha))
        out[test] = entry
    return out


def label(pr, sha):
    return f"#{pr}" if pr else f"{sha[:12]} (no pull request)"


def report_text(head, previous, failed, new, found, missing, server, repo, run_url):
    lines = ["## Full validation of main", "", f"Commit {head[:12]}: {len(failed)} failing test(s). Run: {run_url}"]
    if missing:
        lines.append(f"No results from {', '.join(missing)} (the job did not finish): its tests are not compared.")
    if previous is None:
        lines += ["", "No earlier full run's record to compare with: nothing is traced this time."]
        return "\n".join(lines) + "\n"
    compare = f"{server}/{repo}/compare/{previous['sha'][:12]}...{head[:12]}"
    lines += ["", f"Previous full run: {previous['sha'][:12]} ({previous.get('url', '')}). Commits: {compare}"]
    if not new:
        lines.append("No test fails now that passed on the previous full run.")
        return "\n".join(lines) + "\n"
    lines += ["", f"{len(new)} test(s) fail now that passed on the previous full run:", ""]
    for test in sorted(new)[:LISTED]:
        entry = found[test]
        skipped = ", ".join(label(*m) for m in entry["skipped"]) or "none"
        lines.append(f"- `{test}` (passed at {entry['since'][:12]}): merged without running it: {skipped}")
        if entry["unknown"]:
            lines.append(
                f"  - no plan to read (direct pushes, expired runs): {', '.join(label(*m) for m in entry['unknown'])}"
            )
    if len(new) > LISTED:
        lines.append(f"- and {len(new) - LISTED} more")
    return "\n".join(lines) + "\n"


def issue_text(head, previous, new, found, server, repo, run_url):
    """The issue's text. Each test's range starts at the full run where it last passed (usually the previous one; an
    older one when a run since then did not reach it)."""
    lines = [
        f"The full validation of main at {head[:12]} ({run_url}) found tests failing that passed on an earlier full "
        "run.",
        "",
    ]
    for test in sorted(new)[:LISTED]:
        entry = found[test]
        since = entry["since"]
        compare = f"{server}/{repo}/compare/{since[:12]}...{head[:12]}"
        lines.append(f"- `{test}`")
        lines.append(f"  - commit range: {since[:12]}..{head[:12]} ({compare})")
        lines.append(f"  - merged without running it: {', '.join(label(*m) for m in entry['skipped']) or 'none'}")
        if entry["unknown"]:
            lines.append(f"  - no plan to read: {', '.join(label(*m) for m in entry['unknown'])}")
    if len(new) > LISTED:
        lines.append(f"- and {len(new) - LISTED} more: see the run summary")
    lines += [
        "",
        "If the selector should have picked a test, list the file whose function-level import it went through in "
        "scripts/lazy-imports-counted.txt.",
    ]
    return "\n".join(lines) + "\n"


class GitHub:
    """The gh calls the report makes, so tests can stand in for them."""

    def __init__(self, repo):
        self.repo = repo

    def api(self, path):
        out = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    def download(self, run_id, name):
        """The artifact's JSON file, or None when the run has no such artifact (expired, never uploaded). Any other
        failure (network, rate limit, server) raises: treating it as "no record" would make this run a first run and
        its failures the next run's baseline, so they would never be reported."""
        listed = self.api(f"repos/{self.repo}/actions/runs/{run_id}/artifacts?name={name}&per_page=10")
        if not any(a.get("name") == name and not a.get("expired") for a in listed.get("artifacts", [])):
            return None
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run(
                ["gh", "run", "download", str(run_id), "-R", self.repo, "-n", name, "-D", folder],
                capture_output=True,
                text=True,
                check=True,
            )
            files = sorted(Path(folder).glob("*.json"))
            return json.loads(files[0].read_text()) if files else None

    def runs(self, sha, event):
        """pr-checks.yml runs on a commit for one event, newest first (the API's event filter drops merge_group runs
        when combined with head_sha, so the event is matched here)."""
        runs = self.api(f"repos/{self.repo}/actions/runs?head_sha={sha}&per_page=50").get("workflow_runs", [])
        return [r for r in runs if r.get("event") == event and r.get("path") == PR_WORKFLOW]

    def previous(self, run_id):
        """The newest earlier full run's record, with its url."""
        for run in self.api(
            f"repos/{self.repo}/actions/workflows/{WORKFLOW}/runs?branch=main&status=completed&per_page=20"
        ).get("workflow_runs", []):
            if int(run["id"]) >= int(run_id):
                continue
            record = self.download(run["id"], RECORD)
            if record:
                return {**record, "url": run.get("html_url", "")}
        return None

    def plan(self, sha, event):
        """The plan of the newest run on that commit for that event that still has one."""
        for run in self.runs(sha, event):
            plan = self.download(run["id"], PLAN_ARTIFACT)
            if plan:
                return plan
        return None

    def plans_of(self, pr, sha):
        plans = [self.plan(sha, "merge_group")]
        if pr:
            plans.append(self.plan(self.api(f"repos/{self.repo}/pulls/{pr}")["head"]["sha"], "pull_request"))
        return [p for p in plans if p]

    def file_issue(self, body):
        """A comment on the open ISSUE_TITLE issue, or a new one. Returns its url."""
        found = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "-R",
                self.repo,
                "--state",
                "open",
                "--search",
                f'"{ISSUE_TITLE}" in:title',
                "--json",
                "number,title,url",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        issue = next((i for i in json.loads(found) if i["title"] == ISSUE_TITLE), None)
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as stream:
            stream.write(body)
        if issue:
            subprocess.run(
                ["gh", "issue", "comment", str(issue["number"]), "-R", self.repo, "--body-file", stream.name],
                check=True,
            )
            return issue["url"]
        return subprocess.run(
            ["gh", "issue", "create", "-R", self.repo, "--title", ISSUE_TITLE, "--body-file", stream.name],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()


def report(plan_path, results, record_path, gh, env, git_log):
    plan = json.loads(Path(plan_path).read_text())
    head, run_id = env["GITHUB_SHA"], env["GITHUB_RUN_ID"]
    server, repo = env.get("GITHUB_SERVER_URL", "https://github.com"), env["GITHUB_REPOSITORY"]
    run_url = f"{server}/{repo}/actions/runs/{run_id}"
    failed, passed, missing = outcomes(plan, results)
    previous = gh.previous(run_id)
    last = passes(previous)
    new, found = failed & set(last), {}
    known = {}

    def plans_of(pr, sha):  # ranges overlap when tests last passed on different runs: read each merge's plans once
        if sha not in known:
            known[sha] = gh.plans_of(pr, sha)
        return known[sha]

    for since in sorted({last[t] for t in new}):
        tests = {t for t in new if last[t] == since}
        for test, entry in trail(tests, merged_prs(git_log(since, head)), plans_of).items():
            found[test] = {**entry, "since": since}
    text = report_text(head, previous, failed, new, found, missing, server, repo, run_url)
    if new:
        text += f"\nIssue: {gh.file_issue(issue_text(head, previous, new, found, server, repo, run_url))}\n"
    # The record is the next run's baseline only once this run has reported: after a failed lookup or issue the next
    # run compares with the older record again, so a new failure is not lost.
    Path(record_path).write_text(json.dumps(record(failed, passed, plan, last, head)) + "\n")
    return text


def record(failed, passed, plan, last, head):
    """This run's record: each passing test with the commit where it last passed. A test this run passed has this
    commit. One it did not reach (a job with no results, a cut-short junit file) keeps its earlier commit, so a later
    failure is still new and its range starts where it last passed. A failing test, or one whose file is gone from the
    plan, drops out."""
    kept = {t: sha for t, sha in last.items() if t not in failed and t not in passed and planned(plan, t)}
    return {"sha": head, "failed": sorted(failed), "passed": {**kept, **dict.fromkeys(sorted(passed), head)}}


def git_log(base, head):
    return subprocess.run(
        ["git", "log", "--first-parent", "--format=%H%x09%s", f"{base}..{head}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("report")
    run.add_argument("--plan", default="ci-plan.json")
    run.add_argument("--results", default="ci-results")
    run.add_argument("--record", default="record.json")
    args = parser.parse_args()
    text = report(args.plan, args.results, args.record, GitHub(os.environ["GITHUB_REPOSITORY"]), os.environ, git_log)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
