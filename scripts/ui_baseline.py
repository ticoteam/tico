#!/usr/bin/env python3
"""Per-file ratchet for the UI's JavaScript: type check (tsc, ui/tsconfig.json) and lint (Biome, biome.json).

Findings are counted per file in a baseline: ui/types-baseline.json for tsc, ui/lint-baseline.json for Biome.
A file missing from a baseline must have no findings, and a count may only shrink:

    python3 scripts/ui_baseline.py tsc --check              fail on new findings, and on a count below its baseline
    python3 scripts/ui_baseline.py tsc --write              lower each baseline to the current counts (never raise one)
    python3 scripts/ui_baseline.py tsc --check --base origin/main
                                                            also fail when a baseline grew against that git ref
    (the same three for `biome`)

The first run with no baseline file writes it. Findings under ui/vendor/ are third-party code that a type-checked
module imports (ui/overview/building.js imports three.js); tsc reads them, so they are dropped here, not counted.
"""
import argparse
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TSC = ["npx", "--yes", "-p", "typescript@5.9.3", "tsc", "-p", "ui/tsconfig.json", "--pretty", "false"]
TSC_LINE = re.compile(r"^(?P<file>[^()\n]+)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): (?P<msg>.*)$")
TSC_GLOBAL = re.compile(r"^error (?P<code>TS\d+): (?P<msg>.*)$")
VENDOR = "ui/vendor/"
TOP = 10


class Finding:
    def __init__(self, file, line, col, code, msg):
        self.file, self.line, self.col, self.code, self.msg = file, line, col, code, msg

    def text(self):
        return f"{self.file}:{self.line}:{self.col}: {self.code} {self.msg}"

    def sort_key(self):
        return (self.line, self.col, self.code)


def fail(message):
    sys.exit(message)


def run_tsc():
    done = subprocess.run(TSC, cwd=ROOT, capture_output=True, text=True, check=False)
    findings = []
    for raw in done.stdout.splitlines():
        m = TSC_LINE.match(raw)
        if m:
            file = m["file"].replace("\\", "/")
            if file.startswith(VENDOR):
                continue
            findings.append(Finding(file, int(m["line"]), int(m["col"]), m["code"], m["msg"]))
            continue
        m = TSC_GLOBAL.match(raw)
        if m:
            findings.append(Finding("<config>", 0, 0, m["code"], m["msg"]))
    if done.returncode not in (0, 1, 2) or (done.returncode != 0 and not findings):
        fail(f"tsc exited {done.returncode} without diagnostics:\n{done.stdout}{done.stderr}")
    return findings, "tsc " + TSC[3].split("@")[1]


def biome_command():
    pinned = json.loads((ROOT / "package.json").read_text())["devDependencies"]["@biomejs/biome"]
    local = ROOT / "node_modules" / ".bin" / "biome"
    if local.exists():
        version = subprocess.run([str(local), "--version"], cwd=ROOT, capture_output=True, text=True, check=False).stdout
        if pinned in version:
            return [str(local)], pinned
    # No install in this checkout: fetch the pinned version (the same one package.json pins).
    return ["npx", "--yes", "-p", f"@biomejs/biome@{pinned}", "biome"], pinned


def run_biome():
    cmd, pinned = biome_command()
    done = subprocess.run([*cmd, "lint", "--reporter=json", "--max-diagnostics=none", "ui"],
                          cwd=ROOT, capture_output=True, text=True, check=False)
    try:
        report = json.loads(done.stdout)
    except json.JSONDecodeError:
        fail(f"biome did not print JSON (exit {done.returncode}):\n{done.stdout[:2000]}{done.stderr[:2000]}")
    findings = []
    for item in report.get("diagnostics", []):
        path = item["location"]["path"]
        file = (path.get("file") if isinstance(path, dict) else path).replace("\\", "/")
        if file.startswith(VENDOR):
            continue
        start = item["location"]["start"]
        findings.append(Finding(file, start["line"], start["column"], item["category"], item["message"]))
    return findings, f"biome {pinned}"


def counts_of(findings):
    return collections.Counter(f.file for f in findings)


def load_baseline(path):
    if not path.exists():
        return None
    return {k: int(v) for k, v in json.loads(path.read_text())["files"].items()}


def baseline_at(ref, rel):
    if subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                      cwd=ROOT, capture_output=True, check=False).returncode != 0:
        fail(f"--base {ref}: no such commit in this checkout (fetch it first)")
    shown = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=ROOT, capture_output=True, text=True, check=False)
    if shown.returncode != 0:
        return None
    return {k: int(v) for k, v in json.loads(shown.stdout)["files"].items()}


def write_baseline(path, counts):
    body = {"files": {f: counts[f] for f in sorted(counts) if counts[f] > 0}}
    path.write_text(json.dumps(body, indent=2) + "\n")


def print_top(counts, total_files):
    print(f"top {min(TOP, len(counts))} files of {total_files}:")
    for file, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP]:
        print(f"  {n:6d}  {file}")


def check(findings, label, baseline_path, base_ref, rel):
    counts = counts_of(findings)
    total = sum(counts.values())
    baseline = load_baseline(baseline_path)
    if baseline is None:
        fail(f"{rel} does not exist; run: python3 scripts/ui_baseline.py {label} --write")
    problems = 0
    by_file = collections.defaultdict(list)
    for f in findings:
        by_file[f.file].append(f)

    for file in sorted(set(counts) | set(baseline)):
        now, was = counts.get(file, 0), baseline.get(file, 0)
        if now > was:
            problems += 1
            print(f"NEW  {file}: {now} findings, baseline allows {was}")
            for f in sorted(by_file[file], key=Finding.sort_key):
                print(f"       {f.text()}")
        elif now < was:
            problems += 1
            print(f"LOWER {file}: baseline {was}, now {now}; run: python3 scripts/ui_baseline.py {label} --write")

    if base_ref:
        old = baseline_at(base_ref, rel)
        if old is None:
            print(f"note: {rel} is absent at {base_ref}; the growth check starts with the next change to it")
        else:
            for file in sorted(baseline):
                if baseline[file] > old.get(file, 0):
                    problems += 1
                    print(f"GREW {file}: {old.get(file, 0)} at {base_ref}, now {baseline[file]} in {rel}; "
                          "baselines only shrink, so fix the findings instead")

    print(f"{label}: {total} findings in {len(counts)} files; baseline {sum(baseline.values())} "
          f"in {len(baseline)} files; {'ok' if not problems else f'{problems} problem(s)'}")
    print_top(counts, len(counts))
    return problems


def write(findings, baseline_path, rel):
    counts = counts_of(findings)
    old = load_baseline(baseline_path)
    if old is not None:
        grown = [f for f in sorted(counts) if counts[f] > old.get(f, 0)]
        if grown:
            for file in grown:
                print(f"refusing to raise {file}: {counts[file]} findings, baseline {old.get(file, 0)}")
            fail("fix the new findings; a baseline only shrinks")
    write_baseline(baseline_path, counts)
    print(f"wrote {rel}: {sum(counts.values())} findings in {len(counts)} files")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("tool", choices=["tsc", "biome"])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="fail on new findings or stale counts")
    mode.add_argument("--write", action="store_true", help="lower the baseline to the current counts")
    parser.add_argument("--base", metavar="REF", help="git ref whose baseline must not be exceeded (e.g. origin/main)")
    args = parser.parse_args(argv)

    if args.tool == "tsc":
        findings, label = run_tsc()
        rel = "ui/types-baseline.json"
    else:
        findings, label = run_biome()
        rel = "ui/lint-baseline.json"
    baseline_path = ROOT / rel

    if args.write:
        if any(f.file == "<config>" for f in findings):
            fail("\n".join(f.text() for f in findings if f.file == "<config>"))
        return write(findings, baseline_path, rel)
    if any(f.file == "<config>" for f in findings):
        for f in findings:
            if f.file == "<config>":
                print(f"{f.code} {f.msg}")
        fail(f"{label} reported a configuration error")
    return 1 if check(findings, args.tool, baseline_path, args.base, rel) else 0


if __name__ == "__main__":
    sys.exit(main())
