#!/usr/bin/env python3
"""Conservative PR selection, measured Python and browser groups, and read-only check receipts."""
import argparse
import ast
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
# Audited feature leaves only. A matching filename alone does not establish isolation.
PYTHON_MAP = {
    "backend/task_loops.py": ["backend/tests/test_task_loops.py", "backend/tests/test_health.py",
                              "backend/tests/test_system_routines.py", "backend/tests/test_scheduler.py"],
    "backend/aws_identity.py": ["backend/tests/test_aws_identity.py"],
    "backend/devcopy.py": ["backend/tests/test_devcopy.py"],
    "backend/operations.py": ["backend/tests/test_operations.py", "backend/tests/test_backup_bundle.py",
                               "backend/tests/test_company_storage.py"],
    "backend/code_hash.py": ["backend/tests/test_flight.py"],
}
# These consumers were audited with the tests above. New consumers invalidate the
# audit rather than silently inheriting a filename-based assumption of isolation.
AUDITED_IMPORTERS = {
    "backend/task_loops.py": {"backend/health.py", "backend/scheduler.py"},
    "backend/aws_identity.py": set(),
    "backend/devcopy.py": set(),
    "backend/operations.py": set(),
    "backend/code_hash.py": {"backend/flight.py"},
}
SHARED_HUBS = {"backend/app.py", "backend/models.py", "backend/hubdb.py", "backend/store.py",
               "backend/execution.py"}
UI_MAP = {"ui/app/chat.js": ["chat", "imported-history"],
          "ui/app/chat-outline.js": ["chat", "imported-history"],
          "ui/app/visual-cards.js": ["visual-cards", "visual-fallback", "visual-hosted"],
          "ui/app/visual-media.js": ["visual-media", "visual-media-app"]}
CONFORMANCE = "runner/tests/test_conformance.py"
# Each crate's `cargo test --locked` runs in the conformance job when any of its test inputs change:
# runner-rs tests read runner/ (harnesses, fixtures and the Python runner they compare against) and the
# backend's gateway enroll contract fixture, which gateway_relay's tests check the runner against.
RUST_INPUTS = {"relay-rs": ("relay-rs/",),
               "runner-rs": ("runner-rs/", "runner/", "backend/tests/fixtures/gateway_enroll_contract.json")}
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
    # A merge that writes notes of its own (a line added or removed against every parent) needs its own trailer;
    # bringing main's notes in, a clean union of the parents, stays allowed.
    commits += [merge for merge in git("log", "--merges", "--format=%H", f"{base}..{head}").splitlines()
                if merge_writes(git("show", "--cc", "--format=", merge, "--", "CHANGELOG.md"))]
    if changed == ["CHANGELOG.md"] and commits and all(
            RELEASE_TRAILER.search(git("show", "-s", "--format=%B", commit)) for commit in commits):
        return
    raise RuntimeError(CHANGELOG_REFUSAL)


def merge_writes(combined):
    """Whether a merge's combined diff (`git show --cc`) has a line that is new against every parent: in a hunk
    headed by n+1 @s, the first n columns all + or all -. A line from one parent marks only its own column."""
    parents = 0
    for line in combined.splitlines():
        if line.startswith("@@@"):
            parents = len(line) - len(line.lstrip("@")) - 1
        elif parents and line[:parents] in ("+" * parents, "-" * parents):
            return True
    return False


def is_test(name):
    path = Path(name)
    return "tests" in path.parts and path.name.startswith("test_") and path.suffix == ".py"


def dotted_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return dotted_name(node.value) + "." + node.attr
    return ""


class SourceGraph:
    """Static local imports, literal dynamic imports and fixture references, without executing code."""

    def __init__(self, root):
        self.trees, self.importers, self.references = {}, defaultdict(set), {}
        self.uncertain = False
        self.dynamic_fixtures = False
        try:
            paths = subprocess.check_output(["git", "ls-files", "-z", "--", "*.py"], cwd=root).decode().split("\0")
        except (OSError, subprocess.CalledProcessError):
            paths = [str(p.relative_to(root)) for p in root.rglob("*.py")
                     if not set(p.parts) & {".venv", "node_modules", ".git", "build", ".cache"}]
        modules = {str(Path(p).with_suffix("")).replace("/", "."): p for p in paths if p}
        for name in paths:
            if not name:
                continue
            try:
                self.trees[name] = ast.parse((root / name).read_text())
            except (OSError, SyntaxError, UnicodeError):
                self.uncertain = True
        for name, tree in self.trees.items():
            refs, imports = set(), set()
            loaders = {"__import__"}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in ("importlib", "builtins"):
                    loaders.update(a.asname or a.name for a in node.names if a.name in ("import_module", "__import__"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.startswith("test_") or node.decorator_list:
                        args = {arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
                        for decorator in node.decorator_list:
                            if not isinstance(decorator, ast.Call) or not dotted_name(decorator.func).endswith(".parametrize"):
                                continue
                            indirect = next((k.value for k in decorator.keywords if k.arg == "indirect"), ast.Constant(False))
                            if isinstance(indirect, ast.Constant) and indirect.value is False and decorator.args:
                                argnames = decorator.args[0]
                                args -= {part.strip() for n in ast.walk(argnames)
                                         if isinstance(n, ast.Constant) and isinstance(n.value, str)
                                         for part in n.value.split(",")}
                        refs.update(args)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    # Also catches pytest_plugins, import_module and __import__
                    # with literal names, including modules passed to subprocesses.
                    if node.value in modules or node.value.startswith("test_"):
                        imports.add(node.value)
                elif isinstance(node, ast.Call):
                    function = dotted_name(node.func)
                    if function.endswith((".getfixturevalue", ".usefixtures")):
                        refs.update(n.value for arg in node.args for n in ast.walk(arg)
                                    if isinstance(n, ast.Constant) and isinstance(n.value, str))
                        if function.endswith(".getfixturevalue") and (not node.args or not isinstance(node.args[0], ast.Constant)):
                            self.dynamic_fixtures = True
                    if function.endswith(".parametrize"):
                        for keyword in node.keywords:
                            if keyword.arg == "indirect" and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is False):
                                if not isinstance(keyword.value, (ast.Constant, ast.List, ast.Tuple)):
                                    self.dynamic_fixtures = True
                                args = node.args[:1] if isinstance(keyword.value, ast.Constant) else [keyword.value]
                                refs.update(part.strip() for arg in args for n in ast.walk(arg)
                                            if isinstance(n, ast.Constant) and isinstance(n.value, str)
                                            for part in n.value.split(","))
                    if function in loaders or function.endswith((".import_module", ".__import__")):
                        if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                            if name.startswith("backend/") or is_test(name):
                                self.uncertain = True
                        elif node.args[0].value.startswith("."):
                            module = node.args[0].value
                            package = node.args[1] if len(node.args) > 1 else next(
                                (k.value for k in node.keywords if k.arg == "package"), None)
                            if isinstance(package, ast.Name) and package.id == "__package__":
                                package = ast.Constant(".".join(name.split("/")[:-1]))
                            if isinstance(package, ast.Constant) and isinstance(package.value, str):
                                level = len(module) - len(module.lstrip("."))
                                parts = package.value.split(".")
                                imports.add(".".join(parts[:len(parts) - level + 1] + [module.lstrip(".")]))
                            else:
                                self.uncertain = True
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in targets):
                        value = node.value
                        if not isinstance(value, (ast.Constant, ast.List, ast.Tuple)) or any(
                                not isinstance(n, ast.Constant) or not isinstance(n.value, str)
                                for n in (value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value])):
                            self.uncertain = True
                elif isinstance(node, ast.Import):
                    imports.update(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    package = name.split("/")[:-1]
                    prefix = package[:len(package) - node.level + 1] if node.level else []
                    module = ".".join([*prefix, *([node.module] if node.module else [])])
                    imports.add(module)
                    imports.update(module + "." + a.name for a in node.names)
            self.references[name] = refs
            for module in imports:
                targets = {modules[module]} if module in modules else set()
                # Bare test imports are supported even with importlib collection.
                if module.split(".")[0].startswith("test_"):
                    targets.update(p for m, p in modules.items() if m.endswith("." + module))
                for target in targets - {name}:
                    self.importers[target].add(name)

    def test_leaf(self, name, previous=None):
        if self.uncertain or name not in self.trees or self.importers[name]:
            return False
        tree = ast.parse(previous) if previous is not None else self.trees[name]
        fixtures = set()
        aliases = {"pytest.fixture"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                aliases.update((a.asname or a.name) + ".fixture" for a in node.names if a.name == "pytest")
            elif isinstance(node, ast.ImportFrom) and node.module == "pytest":
                aliases.update(a.asname or a.name for a in node.names if a.name == "fixture")
            elif isinstance(node, ast.Name) and (node.id == "pytest_plugins" or node.id.startswith("pytest_")):
                return False
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("pytest_"):
                return False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                call = decorator if isinstance(decorator, ast.Call) else None
                decorator_name = dotted_name(call.func if call else decorator)
                if decorator_name not in aliases and ".mark." not in decorator_name:
                    return False
                if decorator_name in aliases:
                    fixture = node.name
                    if call:
                        for keyword in call.keywords:
                            if keyword.arg == "name":
                                if not isinstance(keyword.value, ast.Constant) or not isinstance(keyword.value.value, str):
                                    return False
                                fixture = keyword.value.value
                    fixtures.add(fixture)
        # Function arguments and fixture-call strings include usefixtures,
        # parametrize(indirect=...), fixture aliases and getfixturevalue literals.
        return not (fixtures and self.dynamic_fixtures) and not any(
            fixtures & refs for other, refs in self.references.items()
            if other != name and (is_test(other) or Path(other).name == "conftest.py"))

    def backend_tests(self, name):
        if self.uncertain or name not in AUDITED_IMPORTERS or name in SHARED_HUBS:
            return None
        importers = self.importers[name]
        if any(not is_test(p) and p not in AUDITED_IMPORTERS[name] for p in importers):
            return None
        tests = set(PYTHON_MAP[name]) | {p for p in importers if is_test(p)}
        # A mapped test can itself supply fixtures/helpers. Include that import
        # closure, or fall back when it reaches a non-test consumer.
        pending = list(tests)
        while pending:
            for consumer in self.importers[pending.pop()]:
                if not is_test(consumer):
                    return None
                if consumer not in tests:
                    tests.add(consumer)
                    pending.append(consumer)
        return tests


def selection(changed, event="pull_request", root=ROOT, graph=None, base=None):
    """None means the full default suite; an empty list means explicitly unaffected."""
    full = {"python": None, "browser": None, "caddy": True, "reason": "full default suite"}
    rust = sorted(crate for crate, inputs in RUST_INPUTS.items() if any(p.startswith(inputs) for p in changed))
    runner = bool(rust) or any(p.startswith(("runner/", "runner-rs/", "relay-rs/")) or
                               p in ("backend/harness_actions.py", "backend/runner_versions.py") for p in changed)
    full["rust"] = rust
    if event != "pull_request":
        return {**full, "conformance": runner, "reason": "main runs the full default suite"}
    python, browser, uncertain = set(), set(), []
    for name in changed:
        path = root / name
        if name.startswith("docs/") or ("/" not in name and name.endswith(".md")):
            continue
        if not path.is_file() or path.name in ("conftest.py", "__init__.py") or name in SHARED_HUBS:
            uncertain.append(name)
        elif name in PYTHON_MAP or (is_test(name) and name.startswith(("backend/tests/", "scripts/tests/"))):
            graph = graph or SourceGraph(root)
            tests = graph.backend_tests(name) if name in PYTHON_MAP else ({name} if graph.test_leaf(name) else None)
            if tests is not None and is_test(name) and base:
                try:
                    exists = subprocess.check_output(["git", "ls-tree", "--name-only", base, "--", name],
                                                     cwd=root, text=True).strip()
                    previous = subprocess.check_output(["git", "show", f"{base}:{name}"], cwd=root, text=True) if exists else None
                    if previous is not None and not graph.test_leaf(name, previous):
                        tests = None
                except (OSError, subprocess.CalledProcessError, SyntaxError):
                    tests = None
            if tests is None:
                uncertain.append(name)
            else:
                python.update(tests)
        elif name in UI_MAP:
            browser.update(UI_MAP[name])
        else:
            if name.startswith("ui/"):
                browser.add("--all")
            uncertain.append(name)
    if any(not (root / f).is_file() for f in python):
        uncertain.append("missing mapped Python test")
    if any(n != "--all" and not (root / "ui/tests" / f"{n}.cjs").is_file() for n in browser):
        uncertain.append("missing mapped browser test")
        browser = {"--all"}
    if uncertain or not changed:
        return {**full, "browser": ["--all"] if browser else None, "conformance": runner,
                "reason": "unmapped, shared, deleted or uncertain change: full default suite",
                "uncertain": uncertain or ["empty diff"]}
    return {"python": sorted(python), "browser": sorted(browser), "conformance": runner, "rust": rust,
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
    picked = selection(changed, event, base=base)
    counts = collect() if picked["python"] != [] else Counter()
    if picked["python"] is not None:
        missing = set(picked["python"]) - counts.keys()
        if missing:
            raise RuntimeError(f"Affected mapping selected no default tests: {sorted(missing)}")
        counts = Counter({f: counts[f] for f in picked["python"]})
    timings = {}
    if timings_path:
        source = Path(timings_path)
        paths = sorted(source.rglob("durations.json")) if source.is_dir() else ([source] if source.is_file() else [])
        for path in paths:
            timings.update(json.loads(path.read_text()))
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
        jobs.append({"suite": "conformance", "files": [CONFORMANCE], "crates": picked["rust"]})
    # Actions rejects an empty matrix. Keep an explicit successful selection job
    # for documented no-test changes, without invoking bare pytest.
    if not jobs:
        jobs.append({"suite": "selection", "files": []})
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
    if suite == "selection":
        if plan["python"] != [] or plan["browser"] != [] or plan["caddy"] or plan["conformance"]:
            raise RuntimeError("Selection-only job requires an explicitly unaffected plan")
        print("Documentation-only change: no affected tests")
        return 0
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
    rust = cargo_tests(job.get("crates", [])) if suite == "conformance" else 0
    result = subprocess.run(command, cwd=ROOT, env=env)
    if result.returncode == 0 and not suite.startswith("browser-"):
        cases = list(ET.parse(ROOT / "ci-results" / f"{suite}.xml").iter("testcase"))
        if not any(t.find("skipped") is None for t in cases):
            raise RuntimeError("A Python job must pass at least one test, not skip every test")
    return result.returncode or rust


def cargo_tests(crates):
    """Run every selected crate's tests; a red crate stops neither the next crate nor the conformance tests."""
    if crates and not shutil.which("cargo"):
        raise RuntimeError("Rust tests were selected but cargo is not installed")
    failed = []
    for crate in crates:
        if crate not in RUST_INPUTS:
            raise RuntimeError(f"Unknown Rust crate selected: {crate}")
        # runner-rs/target is the directory the conformance job caches; relay-rs builds in a nested target there.
        target = ROOT / "runner-rs" / "target" / ("" if crate == "runner-rs" else crate)
        print(f"cargo test --locked: {crate}", flush=True)
        if subprocess.run(["cargo", "test", "--locked"], cwd=ROOT / crate,
                          env={**os.environ, "CARGO_TARGET_DIR": str(target)}).returncode:
            failed.append(crate)
    if failed:
        print(f"cargo test failed: {', '.join(failed)}", flush=True)
    return 1 if failed else 0


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
