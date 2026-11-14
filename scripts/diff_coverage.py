#!/usr/bin/env python3
"""Diff coverage for PR checks and the merge queue: the Python statements a change adds to backend/ and clients/ must
run under tests.

    python3 scripts/diff_coverage.py pragmas --base BASE --head HEAD   # bare `# pragma: no cover` in added lines
    python3 scripts/diff_coverage.py extract --plan ci-plan.json --data .coverage --out ci-results/coverage-NAME.json
    python3 scripts/diff_coverage.py gate --plan ci-plan.json --results ci-results

The plan (scripts/pr_checks.py plan) records each changed file's added lines that coverage.py counts as statements:
comments, blank lines, docstrings and `# pragma: no cover` lines are not statements. Every pytest job runs under
coverage limited to those files and writes the plan lines that ran (`extract`). `gate` counts the statements no job ran,
prints each one, and fails when fewer than THRESHOLD of them ran. It reads JSON and git only; coverage.py is imported
by the plan and by `extract`, which run where the test requirements are installed.

A line opts out only with `# pragma: no cover` and a reason on the same line (`# pragma: no cover - why it cannot run
here`). `pragmas` fails a bare one, so the check runs on every push, before anything is queued.
"""

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
# The packages the gate measures. Tests, conftest files and migrations are not measured.
MEASURED = ("backend/", "clients/")
# Same pattern as coverage.py's default exclude_lines, so a pragma here excludes the line there.
PRAGMA = re.compile(r"#\s*(pragma|PRAGMA)[:\s]?\s*(no|NO)\s*(cover|COVER)")
MIN_REASON_WORDS = 3
# Fraction of the added statements that must run. Measured Nov 14 on nine recently merged pull requests that add
# backend/clients statements, under a ready pull request's light selection: all passed (lowest 83%, most 94-100%).
THRESHOLD = 0.8
MAX_LISTED = 200


def measured(name):
    """A Python file the gate counts: a source file under backend/ or clients/, not a test or a migration."""
    parts = Path(name).parts
    base = Path(name).name
    if not name.endswith(".py") or not name.startswith(MEASURED):
        return False
    # Path(name).stem, not a "conftest.py" literal: the dependency map reads that literal as running the root conftest.
    if "tests" in parts or "migrations" in parts or Path(name).stem == "conftest":
        return False
    return not (base.startswith("test_") or base.endswith("_test.py"))


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def added_lines(diff_text):
    """{file: [new-side line numbers]} for every line a `git diff -U0` text adds."""
    out, current = {}, None
    for line in diff_text.split("\n"):
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            if current is not None:
                out.setdefault(current, [])
        elif line.startswith("@@") and current is not None:
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            start = int(match.group(1))
            count = 1 if match.group(2) is None else int(match.group(2))
            out[current].extend(range(start, start + count))
    return out


def added_text(diff_text):
    """{file: [(line number, text)]} for the added lines, from a `git diff -U0` text."""
    out, current, number = {}, None, 0
    for line in diff_text.split("\n"):
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and current is not None:
            number = int(re.search(r"\+(\d+)", line).group(1)) - 1
        elif current is not None and line.startswith("+"):
            number += 1
            out.setdefault(current, []).append((number, line[1:]))
    return out


def diff_text(base, head, files=None):
    args = ["diff", "-U0", "--no-renames", "--no-color", base, head]
    if files:
        args += ["--", *files]
    return git(*args)


def changed_files(base, head):
    """Measured files the change adds lines to, that exist at head (a deleted file has nothing to count)."""
    names = git("diff", "--name-only", "--no-renames", "--diff-filter=d", base, head).split("\n")
    return sorted(n for n in names if measured(n) and (ROOT / n).is_file())


def bare_pragmas(text):
    """(line number, line) for each added line with a coverage pragma and fewer than MIN_REASON_WORDS words after it."""
    found = []
    for name, lines in added_text(text).items():
        if not measured(name):
            continue
        for number, line in lines:
            match = PRAGMA.search(line)
            if match is None:
                continue
            reason = line[match.end() :].strip(" \t-\N{EN DASH}\N{EM DASH}:;,.#")
            if len(reason.split()) < MIN_REASON_WORDS:
                found.append((name, number, line.strip()))
    return found


def statement_lines(files, root=ROOT):
    """{file: set of statement lines} by coverage.py's own analysis of each file (nothing runs)."""
    from coverage import Coverage

    cov = Coverage(data_file=None, config_file=False)  # no .coverage file in the working directory
    return {f: set(cov.analysis2(str(root / f))[1]) for f in files}


def plan_coverage(base, head, root=ROOT, changed=None):
    """The gate's part of the plan: each measured file's added lines that are statements, at head. `changed` is the
    change's file list when the caller has it (scripts/pr_checks.py prepare), so a change with no measured file asks
    git nothing; None reads it from git."""
    # From the merge point, as the test selection reads the change: a pull request's base can be ahead of its branch
    # point. A git failure raises, so the plan records an error instead of an empty, passing gate.
    base = git("merge-base", base, head).strip()
    if changed is None:
        files = changed_files(base, head)
    else:
        files = sorted(n for n in changed if measured(n) and (root / n).is_file())
    if not files:
        return {"lines": {}}
    added = {f: lines for f, lines in added_lines(diff_text(base, head, files)).items() if lines}
    statements = statement_lines(sorted(added), root)
    lines = {f: sorted(set(added[f]) & statements[f]) for f in sorted(added)}
    return {"lines": {f: ls for f, ls in lines.items() if ls}}


def extract(plan, data_file, out):
    """Write {file: lines that ran} for the plan's lines, from one job's coverage data (empty when nothing ran)."""
    from coverage import Coverage

    wanted = plan.get("coverage", {}).get("lines", {})
    ran = {}
    if Path(data_file).is_file():
        data = Coverage(data_file=str(data_file), config_file=False)
        data.load()
        measured_files = set(data.get_data().measured_files())
        for name, lines in wanted.items():
            # coverage.py records real paths: the checkout's own, whatever the working directory.
            path = os.path.realpath(ROOT / name)
            if path not in measured_files:
                continue
            # coverage.py's own analysis maps a recorded line to its statement's first line (a multi-line condition
            # can record a continuation line), as the plan's statement lines are.
            _, statements, _, missing, _ = data.analysis2(path)
            hits = sorted(set(lines) & (set(statements) - set(missing)))
            if hits:
                ran[name] = hits
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(ran, indent=1) + "\n")
    return ran


def load_results(results):
    """Every job's lines that ran, merged: {file: set of line numbers}."""
    ran = defaultdict(set)
    for path in sorted(Path(results).glob("coverage-*.json")):
        for name, lines in json.loads(path.read_text()).items():
            ran[name].update(lines)
    return ran


def source_line(root, name, number):
    """The text of one line of a checked-out file, for the gate's listing ("" when the file is not there)."""
    try:
        return (root / name).read_text(errors="replace").splitlines()[number - 1].strip()
    except (OSError, IndexError):
        return ""


def report(plan, results, threshold=THRESHOLD, root=ROOT):
    """(passed, text): the statements the plan's changes add and no job ran, listed with their source text."""
    if plan.get("coverage", {}).get("error"):
        return False, f"diff coverage: FAIL: {plan['coverage']['error']}"
    wanted = plan.get("coverage", {}).get("lines", {})
    total = sum(len(lines) for lines in wanted.values())
    if not total:
        return True, "diff coverage: the change adds no Python statement to backend/ or clients/"
    ran = load_results(results)
    missed = [(name, line) for name, lines in wanted.items() for line in lines if line not in ran.get(name, set())]
    covered = total - len(missed)
    ratio = covered / total
    passed = ratio >= threshold
    head = (
        f"diff coverage: {covered} of {total} added statements ran ({100 * ratio:.1f}%); "
        f"the gate needs {100 * threshold:.0f}%: {'pass' if passed else 'FAIL'}"
    )
    if passed:
        return True, head
    lines = [head, "Added statements no test ran:"]
    for name, line in missed[:MAX_LISTED]:
        lines.append(f"  {name}:{line}: {source_line(root, name, line)}".rstrip())
    if len(missed) > MAX_LISTED:
        lines.append(f"  ... and {len(missed) - MAX_LISTED} more")
    lines.append(
        "Add a test that runs each one, or mark it `# pragma: no cover - <reason>` on its line when no test can reach it."
    )
    return False, "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)
    pragma = sub.add_parser("pragmas")
    pragma.add_argument("--base", required=True)
    pragma.add_argument("--head", default="HEAD")
    take = sub.add_parser("extract")
    take.add_argument("--plan", required=True)
    take.add_argument("--data", default=".coverage")
    take.add_argument("--out", required=True)
    check = sub.add_parser("gate")
    check.add_argument("--plan", required=True)
    check.add_argument("--results", default="ci-results")
    check.add_argument("--threshold", type=float, default=THRESHOLD)
    args = parser.parse_args(argv)
    if args.action == "pragmas":
        found = bare_pragmas(diff_text(args.base, args.head))
        for name, number, line in found:
            print(f"{name}:{number}: `# pragma: no cover` needs a reason on the same line: {line}")
        if found:
            print("Write `# pragma: no cover - <why no test can run this line>`.")
            return 1
        print("pragmas: every added coverage pragma has a reason")
        return 0
    plan = json.loads(Path(args.plan).read_text())
    if args.action == "extract":
        extract(plan, args.data, args.out)
        return 0
    passed, text = report(plan, args.results, args.threshold)
    print(text)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
