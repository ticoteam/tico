#!/usr/bin/env python3
"""Conservative PR selection, measured Python groups, and read-only check receipts."""
import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
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


def groups(counts, timings, total=4):
    # Use measured per-file wall time, including setup/teardown. Unknown files use
    # the observed seconds/test, so adding files cannot put all unknown work in one VM.
    known = {name: float(timings[name]) for name in counts if name in timings
             and isinstance(timings[name], (int, float)) and math.isfinite(timings[name])
             and 0 < timings[name] < 86400}
    seconds_per_test = sum(known.values()) / sum(counts[n] for n in known) if known else 1.0
    weights = {n: known.get(n, counts[n] * seconds_per_test) for n in counts}
    out, loads = [[] for _ in range(total)], [0.0] * total
    for name in sorted(counts, key=lambda n: (-weights[n], n)):
        i = min(range(total), key=lambda i: (loads[i], i))
        out[i].append(name)
        loads[i] += weights[name]
    return [sorted(g) for g in out], loads


def prepare(event, base, output, timings_path=None):
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
        jobs.append({"suite": "browser", "files": picked["browser"]})
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


def run(plan_path, suite):
    plan = json.loads(Path(plan_path).read_text())
    job = next(j for j in plan["matrix"]["include"] if j["suite"] == suite)
    files = job["files"]
    if suite == "browser":
        command = ["node", "scripts/ui-tests.cjs", *(files or [])]
    else:
        if not files or any(not (ROOT / f).is_file() for f in files):
            raise RuntimeError("Python check requires an explicit nonempty existing file list")
        command = [sys.executable, "-m", "pytest", "-q", "-n", "2",
                   "-o", "junit_family=xunit1", f"--junitxml=ci-results/{suite}.xml"]
        if suite == "conformance":
            command += ["-m", "slow"]
        command += files
    (ROOT / "ci-results").mkdir(exist_ok=True)
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode == 0 and suite != "browser":
        cases = list(ET.parse(ROOT / "ci-results" / f"{suite}.xml").iter("testcase"))
        if not any(t.find("skipped") is None for t in cases):
            raise RuntimeError("A Python job must pass at least one test, not skip every test")
    return result.returncode


def durations(paths):
    measured = Counter()
    for path in paths:
        for test in ET.parse(path).iter("testcase"):
            name = test.get("file", "")
            seconds = float(test.get("time", "0"))
            if name and math.isfinite(seconds) and seconds >= 0:
                measured[name] += seconds
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
        measured = durations(Path("ci-results").glob("python-*.xml"))
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
