#!/usr/bin/env python3
"""The TypeScript outside ui/: every .cts and .mts script Node runs under scripts/ and recorder/, the .d.ts files they
read, and the Recorder window's page scripts (recorder/ui: .ts files recorder/build-ui.cjs strips into recorder/dist for
the WebView), type-checks strictly, with no baseline.

    python3 scripts/check_node_types.py

Node, or esbuild for the Recorder's window, strips the types, so nothing else reads them: tsc here is the only check.
The Recorder's window lives here rather than in scripts/check_ui_strict.py, whose rules are about ui/ (its baseline,
its build, its routes): the window is one more leaf of the same strict options, with the DOM in place of Node. The
rules:

1. Each project (PROJECTS) extends tsconfig.node.json, the compiler options ui/tests/tsconfig.json shares too, and its
   EFFECTIVE options (tsc --showConfig, extends followed, not the raw JSON) keep strict, noEmit, erasableSyntaxOnly (Node
   can strip every annotation) and verbatimModuleSyntax true, moduleDetection "force" (each script is its own scope), no
   strict family flag turned off, and no noCheck.
   A page project (PAGE_PROJECTS) has the DOM library and no Node types: its scripts run in a WebView.
2. Every TypeScript file outside ui/ (ui/ is scripts/check_ui_strict.py's) is in one of the projects: a new folder needs
   its own leaf config, listed here.
3. A script never uses `declare global`: a project's scripts share one program, so a global one script declares changes
   the types every other script sees. Page globals are file-local
   (declare const window: Window & typeof globalThis & {...}).
4. tsc reports nothing in any project: any diagnostic fails.

Runs in a few seconds. The pinned TypeScript is fetched with npx, as scripts/check_ui_strict.py does.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TSC = ["npx", "--yes", "-p", "typescript@5.9.3", "tsc"]
BASE = "tsconfig.node.json"
PROJECTS = (
    "scripts/tsconfig.json",
    "recorder/tsconfig.json",
    "recorder/ui/tsconfig.json",
    "bench/tsconfig.json",
)
# Projects whose scripts run in a page (the Recorder's WebView), not in Node.
PAGE_PROJECTS = ("recorder/ui/tsconfig.json",)
REQUIRED_TRUE = ("strict", "noEmit", "erasableSyntaxOnly", "verbatimModuleSyntax")
# The flags strict turns on: --showConfig lists each, so one turned off reads false.
STRICT_FAMILY = (
    "noImplicitAny",
    "noImplicitThis",
    "strictNullChecks",
    "strictFunctionTypes",
    "strictBindCallApply",
    "strictPropertyInitialization",
    "strictBuiltinIteratorReturn",
    "alwaysStrict",
    "useUnknownInCatchVariables",
)
TS_FILE = re.compile(r"\.(ts|cts|mts|tsx)$")
UI = "ui/"
DECLARE_GLOBAL = re.compile(r"^\s*declare\s+global\b", re.M)


class ConfigError(Exception):
    """tsc could not read a config (a missing extends, bad JSON, an unknown option)."""


def tsc(*args, root=None):
    return subprocess.run([*TSC, *args], cwd=root or ROOT, capture_output=True, text=True, check=False)


def effective_config(config, root=None):
    """The config as tsc reads it, extends followed (tsc --showConfig): its compilerOptions, include and files. root is
    the folder config is relative to (ROOT unless given); scripts/check_ui_strict.py reads ui/tests/tsconfig.json here."""
    done = tsc("-p", config, "--showConfig", root=root)
    if done.returncode != 0:
        raise ConfigError(f"{config}: tsc --showConfig exited {done.returncode}: {(done.stdout + done.stderr).strip()}")
    try:
        return json.loads(done.stdout)
    except ValueError as error:
        raise ConfigError(f"{config}: tsc --showConfig printed no JSON ({error})") from error


def option_problems(config, options):
    """Rule 1 on the effective compilerOptions of one config."""
    problems = [
        f"{config}: {flag} must be true (in effect, extends followed)"
        for flag in REQUIRED_TRUE
        if options.get(flag) is not True
    ]
    if options.get("moduleDetection") != "force":
        problems.append(f'{config}: moduleDetection must be "force" (each script is its own scope)')
    problems += [
        f"{config}: {flag} may not be false (strict covers it)" for flag in STRICT_FAMILY if options.get(flag) is False
    ]
    if options.get("noCheck"):
        problems.append(f"{config}: noCheck is not allowed")
    if config in PAGE_PROJECTS:
        if "dom" not in [lib.lower() for lib in options.get("lib", [])]:
            problems.append(f"{config}: lib must include DOM (its scripts run in a page)")
        if options.get("types") != []:
            problems.append(f"{config}: types must be [] (its scripts run in a page, without Node's globals)")
    return problems


def extends_problems(config):
    """Rule 1: the leaf names the shared base, so the options live in one file."""
    try:
        raw = json.loads((ROOT / config).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"{config}: cannot be read as JSON ({error})"]
    extends = raw.get("extends")
    if not isinstance(extends, str) or ((ROOT / config).parent / extends).resolve() != (ROOT / BASE).resolve():
        return [f'{config}: extends must name {BASE} (its options are shared with ui/tests/tsconfig.json)']
    return []


def ts_files():
    """Every TypeScript file outside ui/, tracked or new (ignored files left out), relative to ROOT."""
    done = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(
        rel
        for rel in done.stdout.splitlines()
        if TS_FILE.search(rel) and not rel.startswith(UI) and (ROOT / rel).is_file()
    )


def project_files(config, effective):
    """The files a project's program holds, relative to ROOT."""
    folder = (ROOT / config).parent
    return {((folder / name).resolve()).relative_to(ROOT.resolve()).as_posix() for name in effective.get("files", [])}


def declare_global_problems(files):
    """Rule 3: no `declare global` in a script (a .d.ts file is declarations only, and may)."""
    return [
        f"{rel}: `declare global` is not allowed in a script; declare page globals file-locally"
        " (declare const window: Window & typeof globalThis & {...})"
        for rel in files
        if not re.search(r"\.d\.[cm]?ts$", rel) and DECLARE_GLOBAL.search((ROOT / rel).read_text(encoding="utf-8"))
    ]


def run_project(config):
    """(exit code, output) of tsc on one project."""
    done = tsc("-p", config, "--pretty", "false")
    return done.returncode, done.stdout + done.stderr


def check():
    """Every problem, one line each. tsc runs only once the configs and sources pass the rules before it."""
    problems, covered = [], set()
    for config in PROJECTS:
        problems += extends_problems(config)
        try:
            effective = effective_config(config)
        except ConfigError as error:
            problems.append(str(error))
            continue
        problems += option_problems(config, effective.get("compilerOptions", {}))
        covered |= project_files(config, effective)
    files = ts_files()
    problems += [
        f"{rel}: in no TypeScript project; add its folder's leaf config (extending {BASE}) to PROJECTS in"
        " scripts/check_node_types.py"
        for rel in files
        if rel not in covered
    ]
    problems += declare_global_problems(files)
    if problems:
        return problems
    for config in PROJECTS:
        code, output = run_project(config)
        if code != 0:
            problems.append(f"{config}: tsc exited {code}; fix every diagnostic (no baseline)")
            problems += [f"    {line}" for line in output.splitlines() if line.strip()]
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.parse_args(argv)
    problems = check()
    for problem in problems:
        print(problem)
    summary = f"node types: {', '.join(PROJECTS)}"
    print(f"{summary}; {len(problems)} problem(s)" if problems else f"{summary}; ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
