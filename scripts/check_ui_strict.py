#!/usr/bin/env python3
"""The UI's strict rule: new UI code is TypeScript, and every .ts file type-checks strictly.

    python3 scripts/check_ui_strict.py                fail on the rules below, as the working tree stands
    python3 scripts/check_ui_strict.py --base REF     also fail when the legacy list grew against that git ref
    python3 scripts/check_ui_strict.py --write        lower each legacy count to today's (never raise one) and drop
                                                      the entries of files that now pass

Mirrors scripts/check_pyright_ratchet.py. The rules:

1. A new script under ui/ is TypeScript. A .js or .mjs file added since REF (or not yet in git) fails unless it is on
   ALLOWED_JS, a bundler entry. ui/vendor/ is third-party code and is not checked. A new .cjs fails outside ui/tests/,
   where the browser test scripts are Node programs, not UI code.
2. Every .ts file under ui/ is strict: tsc with ui/tsconfig.strict.json reports nothing in it. That config covers every
   .ts and .js file under ui/, so the globals the classic scripts share resolve as they do in ui/tsconfig.json; checkJs
   is off, so only .ts files report. A .ts file that has findings is listed in ui/strict-legacy.json with its count. The
   list only shrinks: a file leaves it once it is clean, a count only goes down, and no file is added to it.
3. A strict .ts file carries no // @ts-nocheck or // @ts-ignore. // @ts-expect-error, with its reason, is allowed: it
   fails once the error it covers goes away.
4. A file added since REF has no baseline file under ui/baselines/ (scripts/ui_baseline.py). A new file has no findings;
   it is not baselined.
5. ui/tsconfig.strict.json keeps every strict flag on, and its include and exclude lists stay as they are: a loosened
   config would silently skip files.
6. ui/index.html has a blank line between two <script src> tags. Two tags on adjacent lines conflict in a git merge
   when two PRs each edit one of them, so each pair of tags keeps one line between them.

Runs in about two seconds: one tsc pass over the UI. The pinned TypeScript is fetched with npx, as ui_baseline.py does.
"""

import argparse
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

import ui_baseline  # the per-file baseline paths and the script-tag rule, shared with the ratchets

ROOT = Path(__file__).resolve().parent.parent
CONFIG = "ui/tsconfig.strict.json"
LEGACY = "ui/strict-legacy.json"
TSC = ["npx", "--yes", "-p", "typescript@5.9.3", "tsc", "-p", CONFIG, "--pretty", "false"]
TSC_LINE = re.compile(r"^(?P<file>[^()\n]+)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): (?P<msg>.*)$")
TSC_GLOBAL = re.compile(r"^error (?P<code>TS\d+): (?P<msg>.*)$")
REQUIRED_FLAGS = (
    "strict",
    "noUncheckedIndexedAccess",
    "noImplicitOverride",
    "exactOptionalPropertyTypes",
    "noFallthroughCasesInSwitch",
    "noUnusedLocals",
)
REQUIRED_INCLUDE = ("./**/*.ts", "./**/*.js")
# Vendored, generated and npm-package code: the only folders the config leaves out.
ALLOWED_EXCLUDE = {"./vendor/**", "./assets/**", "./node_modules/**", "./observability-vendors.js"}
# The bundler entry and its generated output (npm run build:observability): the only JavaScript a new file may be.
ALLOWED_JS = {"ui/observability-vendors.js", "ui/assets/observability-vendors.js"}
SKIP_TS = ("ui/vendor/", "ui/assets/", "ui/node_modules/")
DIRECTIVE = re.compile(r"@ts-(nocheck|ignore)\b")


class Finding:
    def __init__(self, file, line, col, code, msg):
        self.file, self.line, self.col, self.code, self.msg = file, line, col, code, msg

    def text(self):
        return f"{self.file}:{self.line}:{self.col}: {self.code} {self.msg}"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


def git_show(ref, path):
    done = git("show", f"{ref}:{path}")
    return done.stdout if done.returncode == 0 else None


def ts_in_scope(rel):
    return rel.startswith("ui/") and rel.endswith(".ts") and not rel.endswith(".d.ts") and not rel.startswith(SKIP_TS)


def ts_on_disk():
    return sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "ui").rglob("*.ts")
        if ts_in_scope(path.relative_to(ROOT).as_posix())
    )


def fields(listing):
    return [field for field in listing.split("\0") if field]


def added_files(base):
    """Files new since `base` (a pure add; a rename is a move, not a new file), plus files git does not know yet.
    The baseline files are data about the UI, not UI code, so they are left out."""
    found = set()
    if base:
        found.update(fields(git("diff", "--name-only", "-z", "-M", "--diff-filter=A", base, "--", "ui").stdout))
    found.update(fields(git("ls-files", "--others", "--exclude-standard", "-z", "--", "ui").stdout))
    return sorted(path for path in found if not path.startswith(ui_baseline.LAYOUT + "/"))


def renames_since(base):
    """Old path -> new path for each file git sees as renamed since `base`."""
    tokens = fields(git("diff", "--name-status", "-z", "-M", base, "--", "ui").stdout)
    renames, i = {}, 0
    while i < len(tokens):
        status = tokens[i]
        if status[:1] in ("R", "C") and i + 2 < len(tokens):
            if status[:1] == "R":
                renames[tokens[i + 1]] = tokens[i + 2]
            i += 3
        else:
            i += 2
    return renames


def run_tsc():
    """(findings in source files, configuration errors). A TS5xxx code is an option error, reported on the config
    file or with no file at all: a config error, whichever file tsc names."""
    done = subprocess.run(TSC, cwd=ROOT, capture_output=True, text=True, check=False)
    findings, config_errors = [], []
    for raw in done.stdout.splitlines():
        m = TSC_LINE.match(raw)
        if m:
            file = m["file"].replace("\\", "/")
            if file == CONFIG or m["code"].startswith("TS5"):
                config_errors.append(raw)
            else:
                findings.append(Finding(file, int(m["line"]), int(m["col"]), m["code"], m["msg"]))
            continue
        if TSC_GLOBAL.match(raw):
            config_errors.append(raw)
    if done.returncode not in (0, 1, 2) or (done.returncode != 0 and not findings and not config_errors):
        sys.exit(f"tsc exited {done.returncode} without diagnostics:\n{done.stdout}{done.stderr}")
    return findings, config_errors


def load_legacy(text):
    return {path: int(count) for path, count in json.loads(text).get("files", {}).items()}


def check_config():
    try:
        config = json.loads((ROOT / CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"{CONFIG}: cannot be read as JSON ({error})"]
    problems = []
    options = config.get("compilerOptions", {})
    for flag in REQUIRED_FLAGS:
        if options.get(flag) is not True:
            problems.append(f"{CONFIG}: {flag} must be true (the strict rule)")
    include = config.get("include", [])
    for pattern in REQUIRED_INCLUDE:
        if pattern not in include:
            problems.append(f'{CONFIG}: include must list "{pattern}" (every file is checked, so the globals resolve)')
    for pattern in config.get("exclude", []):
        if pattern not in ALLOWED_EXCLUDE:
            problems.append(f"{CONFIG}: exclude {pattern} is not allowed (excluded code is not checked)")
    return problems


def check_new_files(new):
    problems = []
    for rel in new:
        if rel.endswith((".js", ".mjs")) and not rel.startswith("ui/vendor/") and rel not in ALLOWED_JS:
            problems.append(f"new JavaScript {rel}: write it as TypeScript (ui/app/name.ts, strict) instead")
        elif rel.endswith(".cjs") and not rel.startswith(("ui/tests/", "ui/vendor/")):
            problems.append(f"new Node script {rel}: UI code is TypeScript; only ui/tests/ holds .cjs scripts")
    return problems


def check_new_entries(new, legacy):
    problems = [
        f"{rel} is new and has a baseline file: a new file has no findings, so fix them instead of baselining"
        for rel in new
        if any(ui_baseline.baseline_path(tool, rel).is_file() for tool in ui_baseline.TOOLS)
    ]
    problems += [
        f"{rel} is new and on the legacy list: a file may not be added to {LEGACY}; make it strict"
        for rel in new
        if rel in legacy
    ]
    return problems


def check_script_tags():
    text = (ROOT / "ui/index.html").read_text(encoding="utf-8")
    return [
        f"ui/index.html:{number}: a <script src> directly after another; leave a blank line between them "
        "(python3 scripts/ui_baseline.py migrate adds it)"
        for number in ui_baseline.adjacent_script_lines(text)
    ]


def check_directives(legacy):
    problems = []
    for rel in ts_on_disk():
        if rel in legacy:
            continue
        for number, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            if DIRECTIVE.search(line):
                problems.append(
                    f"{rel}:{number}: a strict file may not use // @ts-nocheck or // @ts-ignore; "
                    "fix the error, or use // @ts-expect-error with the reason"
                )
    return problems


def check_strict(by_file, legacy, on_disk):
    problems = []
    for rel in sorted(by_file):
        found = by_file[rel]
        allowed = legacy.get(rel)
        if allowed is None:
            problems.append(f"NOT STRICT {rel}: {len(found)} finding(s); a .ts file not on the legacy list must pass")
        elif len(found) > allowed:
            problems.append(f"GREW {rel}: {len(found)} finding(s), the legacy list allows {allowed}; fix them instead")
        else:
            continue
        problems += [f"    {f.text()}" for f in sorted(found, key=lambda f: (f.line, f.col, f.code))]
    for rel, allowed in sorted(legacy.items()):
        now = len(by_file.get(rel, []))
        if rel not in on_disk:
            problems.append(f"STALE {rel}: no such .ts file; remove its line from {LEGACY}")
        elif now == 0:
            problems.append(f"CLEAN {rel}: passes strict now; remove it from {LEGACY}")
        elif now < allowed:
            problems.append(f"LOWER {rel}: {now} finding(s), the list says {allowed}; run --write")
    return problems


def check_growth(legacy, base):
    """Problems against `base`. Skipped while the ref has no legacy list (the first change that adds one)."""
    old_text = git_show(base, LEGACY)
    if old_text is None:
        print(f"ui strict: {base} has no {LEGACY} yet; growth checks skipped")
        return []
    renames = renames_since(base)
    old = {renames.get(path, path): count for path, count in load_legacy(old_text).items()}
    problems = []
    for rel, count in sorted(legacy.items()):
        was = old.get(rel)
        if was is None:
            problems.append(f"legacy list grew: {rel} is not listed at {base}; a file may not be added to the list")
        elif count > was:
            problems.append(f"legacy count grew: {rel} {was} -> {count} since {base}; fix the findings instead")
    return problems


def write(by_file, legacy, on_disk):
    grown = [rel for rel in by_file if rel not in legacy or len(by_file[rel]) > legacy[rel]]
    if grown:
        for rel in sorted(grown):
            print(f"refusing to raise or add {rel}: {len(by_file[rel])} finding(s); fix them instead")
        return 1
    kept = {rel: len(by_file[rel]) for rel in legacy if by_file.get(rel) and rel in on_disk}
    (ROOT / LEGACY).write_text(json.dumps({"files": dict(sorted(kept.items()))}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {LEGACY}: {len(kept)} file(s), {sum(kept.values())} finding(s)")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--base", metavar="REF", help="git ref whose legacy list must not be exceeded (e.g. origin/main)"
    )
    parser.add_argument("--write", action="store_true", help=f"lower {LEGACY} to today's counts (never raise one)")
    args = parser.parse_args(argv)
    if args.base and git("rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}").returncode != 0:
        sys.exit(f"--base {args.base}: no such commit in this checkout (fetch it first)")

    legacy_path = ROOT / LEGACY
    if not legacy_path.exists():
        sys.exit(f"{LEGACY} does not exist; create it as {{\"files\": {{}}}}")
    legacy = load_legacy(legacy_path.read_text(encoding="utf-8"))

    findings, config_errors = run_tsc()
    if config_errors:
        print("\n".join(config_errors))
        sys.exit(f"tsc reported a configuration error in {CONFIG}")
    by_file = collections.defaultdict(list)
    for finding in findings:
        if ts_in_scope(finding.file):
            by_file[finding.file].append(finding)
    on_disk = set(ts_on_disk())

    if args.write:
        return write(by_file, legacy, on_disk)

    new = added_files(args.base)
    problems = check_config()
    problems += check_new_files(new)
    problems += check_new_entries(new, legacy)
    problems += check_strict(by_file, legacy, on_disk)
    problems += check_directives(legacy)
    problems += check_script_tags()
    if args.base:
        problems += check_growth(legacy, args.base)

    for problem in problems:
        print(problem)
    total = sum(len(v) for v in by_file.values())
    summary = (
        f"ui strict: {len(on_disk)} .ts file(s) checked, {total} finding(s) in {len(by_file)}; legacy list {len(legacy)} file(s), "
        f"{sum(legacy.values())} finding(s); {len(new)} new file(s) checked"
    )
    if problems:
        print(f"{summary}; {len(problems)} problem(s)")
        return 1
    print(f"{summary}; ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
