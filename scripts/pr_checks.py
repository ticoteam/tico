#!/usr/bin/env python3
"""Conservative PR selection, measured Python and browser groups, and read-only check receipts."""
import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
# Audited feature leaves only. A matching filename alone does not establish isolation.
PYTHON_MAP = {"backend/task_loops.py": ["backend/tests/test_task_loops.py"]}
UI_MAP = {"ui/app/chat.js": ["chat", "imported-history"],
          "ui/app/chat-outline.js": ["chat", "imported-history"],
          "ui/app/visual-cards.js": ["visual-cards", "visual-fallback"],
          "ui/app/visual-media.js": ["visual-media", "visual-media-app"]}
CONFORMANCE = "runner/tests/test_conformance.py"
CHANGELOG_REFUSAL = ("No PR edits CHANGELOG.md; the Technical Writer writes notes at the release cut. "
                     "Remove your CHANGELOG.md changes.")
RELEASE_TRAILER = re.compile(r"^Changelog-Release:[ \t]*v?(\d+\.\d+\.\d+)[ \t]*$", re.M)
# Each check VM has 4 vCPUs and runs 4 test workers. Groups are sized so a VM finishes in about
# TICO_CI_JOB_SECONDS, setup included: short Spot jobs are rarely interrupted and lose little when one is.
WORKERS = 4
JOB_SECONDS = float(os.environ.get("TICO_CI_JOB_SECONDS", 180))
SETUP_SECONDS = {"python": 45, "browser": 70}  # measured boot, checkout and warm install
MAX_JOBS = 16
UNTIMED_SCRIPT_SECONDS = 20  # a browser script with no measured time yet


def guard_changelog(base, head="HEAD", root=None):
    """Refuse PR notes before test selection; allow the documented notes-only release cut.

    Compare the PR diff, not the last commit or the working tree. A trailer on an
    earlier commit cannot exempt a later ordinary notes edit. Direct release cuts
    on main never enter this PR-only gate; shipped-section guards still run.
    """
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root or ROOT, text=True)

    if not base:
        raise RuntimeError("Cannot check CHANGELOG.md without the PR base commit")
    ancestor = git("merge-base", base, head).strip()
    changed = git("diff", "--name-only", "--no-renames", "-z", ancestor, head).rstrip("\0").split("\0")
    if "CHANGELOG.md" not in changed:
        return
    # Release notes are a separate CHANGELOG-only cut. Do not exempt product PRs
    # that happen to contain a release trailer or a commit from an older cut.
    commits = git("log", "--no-merges", "--format=%H", f"{base}..{head}", "--", "CHANGELOG.md").splitlines()
    if changed == ["CHANGELOG.md"] and commits and all(
            RELEASE_TRAILER.search(git("show", "-s", "--format=%B", commit)) for commit in commits):
        return
    raise RuntimeError(CHANGELOG_REFUSAL)


def selection(changed, event="pull_request", root=ROOT):
    """None means the full default suite; an empty list means explicitly unaffected."""
    full = {"python": None, "browser": None, "caddy": True, "reason": "full default suite"}
    runner = any(p.startswith(("runner/", "runner-rs/", "relay-rs/")) or
                 p in ("backend/harness_actions.py", "backend/runner_versions.py") for p in changed)
    if event != "pull_request":
        return {**full, "conformance": runner, "reason": "main runs the full default suite"}
    python, browser = set(), set()
    unknown = not changed
    for name in changed:
        path = root / name
        if not path.is_file() or path.name in ("conftest.py", "__init__.py"):
            unknown = True
        elif name in PYTHON_MAP:
            python.update(PYTHON_MAP[name])
        elif name in UI_MAP:
            browser.update(UI_MAP[name])
        elif name.startswith("ui/"):
            # Shared UI and unclassified UI changes need non-core scripts too.
            browser.add("--all")
            unknown = True
        elif name.startswith(("runner/", "runner-rs/", "relay-rs/")):
            unknown = True
        elif name.startswith(("backend/tests/test_", "scripts/tests/test_")) and name.endswith(".py"):
            # Test modules can also supply fixtures or helpers to other tests.
            unknown = True
        else:
            unknown = True
    if any(not (root / f).is_file() for f in python):
        unknown = True
    if any(n != "--all" and not (root / "ui/tests" / f"{n}.cjs").is_file() for n in browser):
        unknown = True
        browser = {"--all"}
    if unknown:
        return {**full, "browser": ["--all"] if browser else None, "conformance": runner,
                "reason": "unmapped, shared, deleted or uncertain change: full default suite"}
    return {"python": sorted(python), "browser": sorted(browser), "conformance": runner,
            "caddy": False, "reason": "audited affected-test mapping"}


def changed_files(base, head="HEAD", root=ROOT):
    try:
        ancestor = subprocess.check_output(["git", "merge-base", base, head], cwd=root, text=True).strip()
        raw = subprocess.check_output(["git", "diff", "--name-only", "--no-renames", "-z", ancestor, head], cwd=root)
        return raw.decode().rstrip("\0").split("\0") if raw else []
    except (subprocess.CalledProcessError, OSError):
        return []  # The uncertain path runs everything, never nothing.


def collect(root=ROOT):
    result = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-n", "0"],
                            cwd=root, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Default collection failed:\n" + result.stdout[-4000:] + result.stderr[-4000:])
    counts = Counter(line.split("::", 1)[0] for line in result.stdout.splitlines()
                     if "::" in line and not line.startswith(" "))
    if not counts:
        raise RuntimeError("Default suite collected no tests")
    return counts


def weights(counts, timings, fallback=1.0):
    # Use measured per-file wall time, including setup/teardown. Unknown files use
    # the observed seconds/test, so adding files cannot put all unknown work in one VM.
    known = {name: float(timings[name]) for name in counts if name in timings
             and isinstance(timings[name], (int, float)) and math.isfinite(timings[name])
             and 0 < timings[name] < 86400}
    seconds_per_test = sum(known.values()) / sum(counts[n] for n in known) if known else fallback
    return {n: known.get(n, counts[n] * seconds_per_test) for n in counts}


def job_count(seconds, kind):
    """VMs needed for `seconds` of one-worker test time to finish near JOB_SECONDS each."""
    per_job = WORKERS * max(JOB_SECONDS - SETUP_SECONDS[kind], 30)
    return max(1, min(MAX_JOBS, math.ceil(seconds / per_job)))


def groups(counts, timings, total=None, kind="python", fallback=1.0):
    seconds = weights(counts, timings, fallback)
    if total is None:
        total = job_count(sum(seconds.values()), kind)
    out, loads = [[] for _ in range(total)], [0.0] * total
    for name in sorted(counts, key=lambda n: (-seconds[n], n)):
        i = min(range(total), key=lambda i: (loads[i], i))
        out[i].append(name)
        loads[i] += seconds[name]
    return [sorted(g) for g in out], loads


def prepare(event, base, output, timings_path=None):
    if event == "pull_request":
        guard_changelog(base)
    changed = changed_files(base) if base else []
    picked = selection(changed, event)
    counts = collect()
    if picked["python"] is not None:
        missing = set(picked["python"]) - counts.keys()
        if missing:
            raise RuntimeError(f"Affected mapping selected no default tests: {sorted(missing)}")
        counts = Counter({f: counts[f] for f in picked["python"]})
    timings = json.loads(Path(timings_path).read_text()) if timings_path and Path(timings_path).exists() else {}
    split, loads = groups(counts, timings)
    jobs = [{"suite": f"python-{i + 1}", "files": files} for i, files in enumerate(split) if files]
    if picked["browser"] != []:
        scripts = browser_scripts(picked["browser"] or [])
        browser_times = {name: timings.get(f"ui/tests/{name}.cjs") for name in scripts}
        browser_split, browser_loads = groups(Counter(dict.fromkeys(scripts, 1)), browser_times, kind="browser",
                                              fallback=UNTIMED_SCRIPT_SECONDS)
        jobs += [{"suite": f"browser-{i + 1}", "files": names} for i, names in enumerate(browser_split) if names]
        loads += browser_loads
    if picked["conformance"]:
        jobs.append({"suite": "conformance", "files": [CONFORMANCE]})
    # An empty matrix is invalid in Actions; fail rather than silently omit a gate.
    if not jobs:
        raise RuntimeError("Selection produced no check jobs")
    plan = {**picked, "changed": changed, "matrix": {"include": jobs}, "estimated_seconds": loads,
            "timed_files": len(set(counts) & timings.keys())}
    Path(output).write_text(json.dumps(plan, indent=2) + "\n")
    print(json.dumps(plan, indent=2))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write("matrix=" + json.dumps(plan["matrix"]) + "\n")
            stream.write("caddy=" + str(plan["caddy"]).lower() + "\n")
    return plan


def browser_scripts(args, root=ROOT):
    """The browser scripts these ui-tests.cjs arguments run (CORE for none), by name."""
    out = subprocess.run(["node", "scripts/ui-tests.cjs", "--list", *args], cwd=root, capture_output=True,
                         text=True, check=True).stdout.split()
    if not out:
        raise RuntimeError(f"Browser selection {args} listed no scripts")
    return out


def run(plan_path, suite):
    plan = json.loads(Path(plan_path).read_text())
    job = next(j for j in plan["matrix"]["include"] if j["suite"] == suite)
    files = job["files"]
    env = None
    if suite.startswith("browser-"):
        if not files:
            raise RuntimeError("A browser check requires an explicit nonempty script list")
        command = ["node", "scripts/ui-tests.cjs", "-j", str(WORKERS), *files]
        env = {**os.environ, "TICO_UI_TIMINGS": str(ROOT / "ci-results" / f"{suite}.json")}
    else:
        if not files or any(not (ROOT / f).is_file() for f in files):
            raise RuntimeError("Python check requires an explicit nonempty existing file list")
        command = [sys.executable, "-m", "pytest", "-q", "-n", str(WORKERS),
                   "-o", "junit_family=xunit1", f"--junitxml=ci-results/{suite}.xml"]
        if suite == "conformance":
            command += ["-m", "slow"]
        command += files
    (ROOT / "ci-results").mkdir(exist_ok=True)
    result = subprocess.run(command, cwd=ROOT, env=env)
    if result.returncode == 0 and not suite.startswith("browser-"):
        cases = list(ET.parse(ROOT / "ci-results" / f"{suite}.xml").iter("testcase"))
        if not any(t.find("skipped") is None for t in cases):
            raise RuntimeError("A Python job must pass at least one test, not skip every test")
    return result.returncode


def durations(paths, browser_paths=()):
    measured = Counter()
    for path in paths:
        for test in ET.parse(path).iter("testcase"):
            name = test.get("file", "")
            seconds = float(test.get("time", "0"))
            if name and math.isfinite(seconds) and seconds >= 0:
                measured[name] += seconds
    # Browser scripts are keyed by path, like Python files, so one timings file serves both splits.
    for path in browser_paths:
        for name, seconds in json.loads(Path(path).read_text()).items():
            if isinstance(seconds, (int, float)) and math.isfinite(seconds) and seconds >= 0:
                measured[f"ui/tests/{name}.cjs"] += seconds
    return dict(measured)


def receipt(jobs):
    from datetime import datetime
    rows = ["| Job | Result | Seconds |", "| --- | --- | ---: |"]
    for job in jobs:
        start, end = job.get("started_at"), job.get("completed_at")
        seconds = (datetime.fromisoformat(end.replace("Z", "+00:00")) -
                   datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds() if start and end else None
        name = str(job["name"]).replace("|", r"\|").replace("\n", " ")
        rows.append(f"| [{name}]({job['html_url']}) | {job.get('conclusion') or job['status']} | "
                    + (f"{seconds:.1f}" if seconds is not None else "running") + " |")
    return "\n".join(rows) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--event", required=True)
    plan.add_argument("--base", default="")
    plan.add_argument("--output", default="ci-plan.json")
    plan.add_argument("--timings")
    execute = sub.add_parser("run")
    execute.add_argument("suite")
    execute.add_argument("--plan", default="ci-plan.json")
    sub.add_parser("durations")
    sub.add_parser("receipt")
    args = parser.parse_args()
    if args.action == "plan":
        prepare(args.event, args.base, args.output, args.timings)
    elif args.action == "run":
        return run(args.plan, args.suite)
    elif args.action == "durations":
        measured = durations(Path("ci-results").glob("python-*.xml"), Path("ci-results").glob("browser-*.json"))
        if not measured:
            raise RuntimeError("No Python timings were collected")
        Path("durations.json").write_text(json.dumps(measured, indent=2))
    else:
        data = json.load(sys.stdin)
        # gh --paginate --slurp emits one jobs response per page.
        print(receipt([j for page in data for j in page["jobs"]]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
