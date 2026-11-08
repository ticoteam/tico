#!/usr/bin/env python3
"""Flaky-test quarantine for PR checks and the merge queue.

A test that fails and passes on the same commit is flaky: a rerun attempt of one run, or two runs that tested the same
commit. ci/quarantine/ holds one JSON file per quarantined test (entry_name), so two new entries never conflict:

  {"test": "<pytest node id>", "first_seen": "YYYY-MM-DD", "commit": "<sha>", "runs": ["<run or job url>", ...],
   "ticket": "<the fix's ticket, or empty>", "reason": "<one line>"}

An entry names one test, or a test function and every parameter of it (an id without [...]). PR checks and merge queue
groups pass --quarantine to pytest (scripts/pr_checks.py run --quarantine): a quarantined test still runs, but as a
non-strict xfail, so its failure does not fail the suite. Release validation never passes it, so it runs and fails on
quarantined tests as before. The fix for a quarantined test deletes its entry file.

  detect  reads PR-check runs from GitHub (gh api) and prints the tests that flipped; --write DIR writes an entry file
          for each one that is not quarantined yet (.github/workflows/flaky-tests.yml, daily)
  report  lists every entry with its age and ticket; exits 1 on an entry older than --max-age-days or a malformed one
          (the same workflow, weekly: it fails its own job, never the queue)
  check   exits 1 on a malformed entry
"""

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
QUARANTINE = "ci/quarantine"
MAX_AGE_DAYS = 14
# One job with more failing tests than this is a breakage or an infrastructure fault (a lost runner, a full disk), not
# a set of flaky tests: its failures are reported, never proposed.
MAX_FAILURES_PER_JOB = 5
WORKFLOW = "pr-checks.yml"
# The PR-check legs that run pytest (scripts/pr_checks.py run). Browser legs and the factory sim name no pytest ids.
TEST_JOB = re.compile(r"python-\d+|contracts|conformance")
FIELDS = ("test", "first_seen", "commit", "runs", "ticket", "reason")
REQUIRED = ("test", "first_seen", "runs", "ticket")
REASON = "failed and passed on the same commit"


def entry_name(test):
    """The entry file for a test id: the id with every run of unsafe characters as one dash, at most 100 characters,
    and a short hash of the exact id, so two ids that sanitize alike still get two files."""
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", test).strip("-")[:100].rstrip("-")
    return f"{readable}-{hashlib.sha256(test.encode()).hexdigest()[:10]}.json"


def _entry_problems(path, data, root):
    if not isinstance(data, dict):
        return [f"{path}: not a JSON object"]
    out = []
    test = data.get("test")
    if not isinstance(test, str) or "::" not in test:
        return [f"{path}: test must be a pytest node id (file.py::name), not {test!r}"]
    for key in REQUIRED:
        if key not in data:
            out.append(f"{path}: {key} is missing")
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        out.append(f"{path}: unknown field(s) {', '.join(unknown)}; the fields are {', '.join(FIELDS)}")
    try:
        date.fromisoformat(str(data.get("first_seen")))
    except ValueError:
        out.append(f"{path}: first_seen must be a YYYY-MM-DD date, not {data.get('first_seen')!r}")
    runs = data.get("runs")
    if not isinstance(runs, list) or not runs or not all(isinstance(u, str) and u.startswith("https://") for u in runs):
        out.append(f"{path}: runs must list at least one https:// run or job url")
    for key in ("ticket", "reason", "commit"):
        if key in data and not isinstance(data[key], str):
            out.append(f"{path}: {key} must be a string")
    if path.name != entry_name(test):
        out.append(f"{path}: {entry_name(test)} is the name for {test}")
    if not (root / test.split("::", 1)[0]).is_file():
        out.append(f"{path}: no such test file: {test.split('::', 1)[0]}")
    return out


def _read(root):
    """(path, data or None, problems) for each entry file."""
    folder = Path(root) / QUARANTINE
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.iterdir()):
        shown = path.relative_to(root)
        if path.suffix != ".json":
            out.append((shown, None, [f"{shown}: not an entry; ci/quarantine holds only <entry>.json files"]))
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            out.append((shown, None, [f"{shown}: {error}"]))
            continue
        out.append((shown, data, _entry_problems(path, data, Path(root))))
    return out


def problems(root=ROOT):
    """Every problem with the entries: unreadable, misnamed, missing a field, or naming a test file that is gone."""
    found = [problem for _, _, issues in _read(root) for problem in issues]
    tests = [data["test"] for _, data, issues in _read(root) if data is not None and not issues]
    found += [f"{QUARANTINE}: {test} is quarantined twice" for test in sorted(set(tests)) if tests.count(test) > 1]
    return found


def load(root=ROOT):
    """The well-formed entries by test id. A malformed entry quarantines nothing (problems() reports it)."""
    return {data["test"]: {**data, "file": str(path)} for path, data, issues in _read(root) if not issues}


def matching(nodeid, tests):
    """The quarantined id that covers a collected node id: the id itself, or its test function without parameters."""
    if nodeid in tests:
        return nodeid
    bare = nodeid.split("[", 1)[0]
    return bare if bare != nodeid and bare in tests else None


def addoption(parser):
    parser.addoption(
        "--quarantine",
        action="store_true",
        help="Run the tests in ci/quarantine/ as non-strict xfail (PR checks and the merge queue, never release)",
    )


def mark(items, entries):
    """Mark each quarantined item a non-strict xfail: it runs, and a failure or error does not fail the run."""
    import pytest

    marked = []
    for item in items:
        test = matching(item.nodeid, entries)
        if test is None:
            continue
        reason = f"quarantined: {entries[test]['file']}"
        if entries[test].get("ticket"):
            reason += f" ({entries[test]['ticket']})"
        item.add_marker(pytest.mark.xfail(reason=reason, strict=False, run=True))
        marked.append(item.nodeid)
    return marked


def modify(config, items):
    """The conftest hook's body: marks quarantined items only when --quarantine is given."""
    if config.getoption("--quarantine"):
        mark(items, load(Path(config.rootpath)))


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


def observations(gh, repo, runs):
    """One observation per attempt of each run: the commit it tested (`key`), its plan job, its pytest legs, and the
    tests that failed with the leg that failed them. A merge queue group's head sha is the commit it tested; a pull
    request run tested the branch merged into main, which only a job log names."""
    out = []
    for r in runs:
        commit = None if r["event"] == "pull_request" else r["head_sha"]
        attempts = []
        for attempt in range(1, r["run_attempt"] + 1):
            jobs = gh.api(f"repos/{repo}/actions/runs/{r['id']}/attempts/{attempt}/jobs?per_page=100")["jobs"]
            plan = next((j["id"] for j in jobs if j["name"] == "plan"), None)
            legs = [
                {"id": j["id"], "name": j["name"], "conclusion": j["conclusion"], "url": j["html_url"]}
                for j in jobs
                if TEST_JOB.fullmatch(j["name"])
            ]
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
SLOW_SUITES = ("conformance", "contracts")


def _passed(test, failure, failing, other, files):
    """Whether `other` (an attempt of the same commit) passed `test`: a leg of it that ran the test's file passed. A
    leg's log names its files; an older log does not, and then only the same leg of the same plan job counts (a
    "re-run failed jobs" attempt keeps the plan job, so each leg runs the files it ran before)."""
    path = test.split("::")[0]
    for leg in other["legs"]:
        if leg["conclusion"] != "success":
            continue
        # pytest runs only `-m slow` in the conformance and contracts suites and deselects them everywhere else, so a leg
        # of the other kind never ran this test: a slow test that fails every time is not proven passing by a python leg.
        if (leg["name"].split("-")[0] in SLOW_SUITES) != (failure["leg"].split("-")[0] in SLOW_SUITES):
            continue
        same_plan = other["run"] == failing["run"] and other["plan"] is not None and other["plan"] == failing["plan"]
        if same_plan and leg["name"] == failure["leg"]:
            return True
        listed = files(leg["id"])
        if listed is not None and path in listed:
            return True
    return False


def flips(observed, files):
    """The tests that failed in one observation and passed in another of the same commit, by test id. `files(job id)`
    is the list of files a leg ran, or None when its log does not say."""
    by_key = {}
    for o in observed:
        by_key.setdefault(o["key"], []).append(o)
    found = {}
    for key, group in by_key.items():
        for o in group:
            for test, failure in o["failed"].items():
                passes = [p["url"] for p in group if p is not o and _passed(test, failure, o, p, files)]
                if not passes:
                    continue
                flip = found.setdefault(test, {"commit": key, "failed": [], "passed": [], "first_seen": o["created"]})
                if flip["commit"] != key:
                    continue
                if failure["url"] not in flip["failed"]:
                    flip["failed"].append(failure["url"])
                flip["passed"] += [u for u in passes if u not in flip["passed"]]
                flip["first_seen"] = min(flip["first_seen"], o["created"])
    return found


def propose(found, quarantined, root=ROOT):
    """An entry for each flip that no entry covers yet, by test id. A test whose file this checkout lacks (a test only a
    pull request branch has) is left out: its entry would name a file main does not have."""
    out = []
    for test in sorted(found):
        if matching(test, quarantined) is not None or not (Path(root) / test.split("::", 1)[0]).is_file():
            continue
        flip = found[test]
        out.append(
            {
                "test": test,
                "first_seen": flip["first_seen"],
                "commit": flip["commit"],
                "runs": sorted(set(flip["failed"]) | set(flip["passed"])),
                "ticket": "",
                "reason": REASON,
            }
        )
    return out


def write_entries(entries, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for data in entries:
        path = folder / entry_name(data["test"])
        path.write_text(json.dumps(data, indent=2) + "\n")
        written.append(path)
    return written


def report(entries, issues, today, max_age=MAX_AGE_DAYS):
    """(markdown, failing): each entry's age and ticket. Failing when an entry is older than max_age days or an entry
    file has a problem."""
    if not entries and not issues:
        return "No tests are quarantined.\n", False
    now = date.fromisoformat(today)
    rows = ["| Test | Quarantined for | Ticket |", "| --- | ---: | --- |"]
    stale = []
    for test in sorted(entries, key=lambda t: (entries[t]["first_seen"], t)):
        age = (now - date.fromisoformat(entries[test]["first_seen"])).days
        if age > max_age:
            stale.append(test)
        ticket = entries[test].get("ticket") or "no ticket"
        rows.append(f"| `{test}` | {age} days{' (too long)' if age > max_age else ''} | {ticket} |")
    lines = [f"{len(entries)} quarantined test(s).", "", *rows, ""]
    if stale:
        lines.append(
            f"{len(stale)} test(s) quarantined longer than {max_age} days. Fix each one and delete its entry file in "
            f"{QUARANTINE}/, or delete the test."
        )
    for issue in issues:
        lines.append(f"- {issue}")
    return "\n".join(lines) + "\n", bool(stale or issues)


class GitHub:
    """Read-only `gh api` calls. Job logs, which never change, are cached in `cache` when it is given."""

    def __init__(self, repo, cache=None):
        self.repo = repo
        self.cache = Path(cache) if cache else None

    def api(self, path):
        return json.loads(subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True).stdout)

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


def detect(gh, repo, days, today, quarantined):
    """(markdown, proposed entries)."""
    runs = candidates(runs_since(gh, repo, days, today))
    observed = observations(gh, repo, runs)
    listed = {}

    def files(job_id):
        if job_id not in listed:
            log = gh.log(job_id)
            listed[job_id] = test_files(log) if log else None
        return listed[job_id]

    found = flips(observed, files)
    proposed = propose(found, quarantined)
    lines = [f"Read {len(runs)} PR-check run(s) that could flip, over {days + 1} day(s).", ""]
    proposed_tests = {p["test"] for p in proposed}
    if found:
        lines += ["| Test | Commit | Failed | Passed | Quarantined |", "| --- | --- | --- | --- | --- |"]
        for test in sorted(found):
            flip = found[test]
            failed = " ".join(f"[job]({u})" for u in flip["failed"])
            passed = " ".join(f"[attempt]({u})" for u in flip["passed"][:3])
            status = "yes" if matching(test, quarantined) else "proposed" if test in proposed_tests else "not on main"
            lines.append(f"| `{test}` | {flip['commit'][:10]} | {failed} | {passed} | {status} |")
    else:
        lines.append("No test failed and passed on the same commit.")
    crowded = sorted({u for o in observed for u in o["crowded"]})
    if crowded:
        lines += ["", f"Jobs with more than {MAX_FAILURES_PER_JOB} failed tests (a breakage, not flakes):"]
        lines += [f"- {u}" for u in crowded]
    if proposed:
        lines += [
            "",
            f"{len(proposed)} proposed entr{'y' if len(proposed) == 1 else 'ies'}. To quarantine them, run this in a "
            f"branch from main and open a pull request:",
            "",
            f"    python3 scripts/flake_quarantine.py detect --days {days} --write {QUARANTINE}",
        ]
    return "\n".join(lines) + "\n", proposed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    find = sub.add_parser("detect")
    find.add_argument("--repo", default="ticoteam/tico")
    find.add_argument("--days", type=int, default=1, help="days before today to read as well (UTC)")
    find.add_argument("--write", help="write the proposed entries to this folder")
    find.add_argument("--cache", help="keep job logs in this folder")
    find.add_argument("--today", default=datetime.now(timezone.utc).date().isoformat())
    show = sub.add_parser("report")
    show.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS)
    show.add_argument("--today", default=datetime.now(timezone.utc).date().isoformat())
    sub.add_parser("check")
    args = parser.parse_args(argv)
    if args.action == "detect":
        gh = GitHub(args.repo, args.cache)
        text, proposed = detect(gh, args.repo, args.days, args.today, load())
        print(text)
        if args.write:
            for path in write_entries(proposed, args.write):
                print(f"wrote {path}")
        return 0
    issues = problems()
    if args.action == "check":
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1 if issues else 0
    text, failing = report(load(), issues, args.today, args.max_age_days)
    print(text)
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
