#!/usr/bin/env python3
"""Which Python tests run each function: the measured Python test map, published to the ci/py-test-map branch.

The measuring workflow (.github/workflows/py-test-map.yml) runs every default-suite test file in its own pytest process,
under coverage.py's sysmon core, with the test file's name as the static context of that process's data. A function's
tests are the test files whose process ran one of the function's body lines. Lines outside any function body (imports,
constants, class bodies, decorators, signatures) belong to "<module>", and a change there is never narrowed.

Each test file runs alone, so code that runs at import or in a session fixture is attributed to every file that runs it,
not only to the first. A file whose process did not finish cleanly is "partial" and the selector treats it as unmeasured.
A subprocess a test file starts is measured as that file too (install_hook): a child that keeps the environment, or that
starts while the run has its rc file, takes the file from the run's context file.
The map is data: the workflow publishes it as the only file on ci/py-test-map, and nothing edits it by hand.

    python3 scripts/py_test_map.py measure --shard I [--of N] --out DIR [--data DIR]   one shard -> DIR/partial-I.json
    python3 scripts/py_test_map.py build --partials DIR --out PATH --sha SHA [--run URL]   partial maps -> the map
    python3 scripts/py_test_map.py check --map PATH                                     the schema of a published map
    python3 scripts/py_test_map.py overhead --sample N --out PATH                       coverage's cost on this machine
    python3 scripts/py_test_map.py shard I [--of N]                                     the files of measuring shard I

load_map() is what the selector reads: the file named by TICO_PY_TEST_MAP, or None (static selection) when it is missing,
older than MAX_AGE, or unreadable. owners() and function_hashes() are shared with the selector, which reads a changed
function's lines and its base-side hash the same way the builder does.
"""

import argparse
import ast
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import heapq
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import tokenize

ROOT = Path(__file__).resolve().parents[1]
MAP_ENV = "TICO_PY_TEST_MAP"
VERSION = 1
MODULE = "<module>"
HARNESS = "scripts/py_test_map.py"
# A map measured longer ago than this is not used: the selector runs the static map instead.
MAX_AGE = timedelta(days=3)
FORMAT = "%Y-%m-%dT%H:%M:%SZ"
COVERAGE = "7.17.0"
CORE = "sysmon"
SHARDS = 32
TEST_SECONDS = 900
# The flags the queue's Python jobs pass (scripts/pr_checks.py PYTEST_FLAGS); -n 0 replaces the queue's workers.
PYTEST_FLAGS = ("--api-database-snapshots", "--maxschedchunk=2")
UNTIMED_SECONDS = 20.0
TIMINGS = ROOT / "scripts" / "test-timings.json"
SHA = re.compile(r"[0-9a-f]{40}")
TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
DIGEST = re.compile(r"[0-9a-f]{16}(?:\|[0-9a-f]{16})*")
KEYS = {"version", "sha", "measured_at", "run", "tool", "tests", "partial", "files", "hashes"}
# The subprocess hook, written into the measuring venv by install_hook(). Every Python process there runs one .pth line
# that imports this module and calls start() with the measuring run's two files: the coverage config (the rc file the
# run writes into its data folder) and the context file (the test file and data folder of the run's current test,
# rewritten before each test file). A process that inherits COVERAGE_PROCESS_START (a test file's process, and children
# that keep the environment) is measured as before. A child started with its own environment, such as a CLI run with
# a minimal env dict, is measured too while the run has its rc: it takes the test and data folder from the context file,
# the same variables the rc reads. Any other process returns at once and imports no coverage.
HOOK_MODULE = '''"""Starts coverage in a process of a measuring run (scripts/py_test_map.py). Written into the measuring venv only."""

import json
import os


def start(config, context):
    if "COVERAGE_PROCESS_START" not in os.environ:
        if not os.path.exists(config):
            return
        try:
            with open(context, encoding="utf-8") as stream:
                info = json.load(stream)
            os.environ["COVERAGE_CORE"] = info["core"]
            os.environ["TICO_TEST_CONTEXT"] = info["test"]
            os.environ["TICO_COV_OUT"] = info["out"]
            os.environ["COVERAGE_PROCESS_START"] = config
        except (OSError, ValueError, KeyError):
            return  # between two test files, or the run is over: nothing to attribute
    import coverage

    coverage.process_startup()
'''
HOOK_LINE = "import tico_py_test_map_coverage as hook; hook.start({config!r}, {context!r})\n"
# Data files go to TICO_COV_OUT, one per process (parallel), labelled with the test file in TICO_TEST_CONTEXT.
# The driver runs from the repo root, as `python -m pytest` does, so the tests import `backend` the same way.
DRIVER = "import sys; from scripts.py_test_map import run_pytest_one; raise SystemExit(run_pytest_one(sys.argv[1]))"
RC = """[run]
parallel = True
relative_files = True
data_file = ${TICO_COV_OUT}/cov
context = ${TICO_TEST_CONTEXT}
"""


class MapError(Exception):
    """A map or a measurement that cannot be trusted. The build stops; the selector falls back to the static map."""


def parse_time(text):
    """The UTC time a map stores as YYYY-MM-DDTHH:MM:SSZ. Raises ValueError for anything else."""
    if not isinstance(text, str) or not TIME.fullmatch(text):
        raise ValueError(f"not a UTC time: {text!r}")
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def is_test(name):
    """A file pytest collects by name (pytest's default python_files), as scripts/pr_checks.py defines it."""
    base = Path(name).name
    return base.endswith(".py") and (base.startswith("test_") or base.endswith("_test.py"))


def _scopes(tree):
    """(kind, qualname, first line, body line, last line, node) for every function and class, outer scopes first.

    `first` is the first decorator's line (or the def line), `body` the first body statement's line: the lines from
    `first` to `body - 1` are the header (decorators, signature, class line).
    """
    found = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = prefix + child.name
                first = min([child.lineno, *(d.lineno for d in child.decorator_list)])
                kind = "class" if isinstance(child, ast.ClassDef) else "function"
                found.append((kind, qual, first, child.body[0].lineno, child.end_lineno, child))
                visit(child, qual + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def owners(source):
    """Who owns each line of a source file. Index = line number (index 0 is unused).

    ("function", qualname) for a line in a function's body; ("header", qualname) for a decorator, a signature or a
    class's header, with the qualname of the def or class it belongs to; ("module", "") for everything else (imports,
    constants, class bodies). An inner scope overwrites the outer scope it sits in, so a method's lines belong to the
    method and a nested def's header to the nested def.
    """
    scopes = _scopes(ast.parse(source))
    size = source.count("\n") + 2
    out = [("module", "")] * (size + 1)
    for kind, qual, first, body, last, _node in sorted(scopes, key=lambda s: s[4] - s[2], reverse=True):
        for line in range(first, min(body, size + 1)):
            out[line] = ("header", qual)
        tag = ("function", qual) if kind == "function" else ("module", "")
        for line in range(body, min(last, size) + 1):
            out[line] = tag
    return out


def inert_lines(source):
    """Line numbers (1-based, as git counts them) that hold no code: blank lines and comment-only lines outside every
    string literal. Editing one cannot change what runs, so a diff that only touches them keeps the map's answer."""
    inside, opened = set(), []
    fstring_start = getattr(tokenize, "FSTRING_START", None)
    fstring_end = getattr(tokenize, "FSTRING_END", None)
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == fstring_start:
            opened.append(token.start[0])
        elif token.type == fstring_end:
            inside.update(range(opened.pop(), token.end[0] + 1))
        elif token.type == tokenize.STRING and token.end[0] > token.start[0]:
            inside.update(range(token.start[0], token.end[0] + 1))
    lines = source.split("\n")
    if lines[-1] == "":  # the text after the final newline is not a line (git counts none there)
        lines.pop()
    out = set()
    for number, text in enumerate(lines, 1):
        stripped = text.strip()
        if number not in inside and (not stripped or stripped.startswith("#")):
            out.add(number)
    return out


def owner_names(own, lines):
    """The qualnames of the functions these lines run in; MODULE for a line outside every function body."""
    names = set()
    for line in lines:
        kind, qual = own[line] if 0 < line < len(own) else ("module", "")
        names.add(qual if kind == "function" else MODULE)
    return names


def function_hashes(source):
    """A digest of each function's syntax tree (line numbers and comments excluded): drift between two versions shows."""
    digests = defaultdict(list)
    for kind, qual, *_rest, node in _scopes(ast.parse(source)):
        if kind == "function":
            digests[qual].append(hashlib.sha256(ast.dump(node).encode()).hexdigest()[:16])
    return {qual: "|".join(sorted(values)) for qual, values in digests.items()}


class Unresolved(Exception):
    """A binding the static rules cannot name: a star import, or a target that is not a name or an attribute of one."""


DYNAMIC_GLOBALS = ("globals", "locals")
DYNAMIC_MODULE = ("getattr", "setattr", "hasattr", "delattr", "vars", "dir")


def top_statements(tree, lines):
    """The top-level statements of `tree` that hold any of `lines` (1-based), and the lines none of them holds."""
    holding, uncovered = {}, []
    for line in lines:
        found = [stmt for stmt in tree.body if stmt.lineno <= line <= stmt.end_lineno]
        if not found:
            uncovered.append(line)
        for stmt in found:
            holding[id(stmt)] = stmt
    return list(holding.values()), uncovered


def _targets(target):
    """The names a binding target binds or changes: a name, the names in a tuple or list, the root of an attribute or
    subscript (`cfg["k"] = 1` changes cfg)."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return set().union(*(_targets(element) for element in target.elts))
    if isinstance(target, ast.Starred):
        return _targets(target.value)
    if isinstance(target, (ast.Attribute, ast.Subscript)):
        root = target
        while isinstance(root, (ast.Attribute, ast.Subscript)):
            root = root.value
        if isinstance(root, ast.Name):
            return {root.id}
    raise Unresolved(type(target).__name__)


def bindings(stmt):
    """(names, runs) for a module-level statement: the names it binds or unbinds, and whether it runs code of its own.

    `names` is None when they cannot be resolved (a star import, an unusual target). `runs` is True for a call or any
    other expression, a def with decorators, a class (its body runs), a compound statement (if, try, with, for, while,
    match), an assert or a raise. An assignment whose value calls a function does not run by this rule: its value is
    what the names it binds hold.
    """
    names, runs = set(), False
    try:
        if isinstance(stmt, ast.Import):
            names = {alias.asname or alias.name.split(".")[0] for alias in stmt.names}
        elif isinstance(stmt, ast.ImportFrom):
            if any(alias.name == "*" for alias in stmt.names):
                raise Unresolved("star import")
            names = {alias.asname or alias.name for alias in stmt.names}
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                names |= _targets(target)
        elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
            names |= _targets(stmt.target)
        elif isinstance(stmt, ast.Delete):
            for target in stmt.targets:
                names |= _targets(target)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names, runs = {stmt.name}, bool(stmt.decorator_list)
        elif isinstance(stmt, ast.Expr):
            runs = not isinstance(stmt.value, ast.Constant)
        elif not isinstance(stmt, (ast.Pass, ast.Global, ast.Nonlocal)):
            runs = True
    except Unresolved:
        return None, True
    if "__all__" in names:  # the names a module exports are the strings in its __all__ value
        names |= {
            node.value for node in ast.walk(stmt) if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
    names |= {node.target.id for node in ast.walk(stmt) if isinstance(node, ast.NamedExpr)}
    return names, runs


def module_name(path):
    """The dotted module a repository path imports as; a package's __init__.py is the package itself."""
    name = path[: -len(".py")].replace("/", ".")
    return name[: -len(".__init__")] if name.endswith(".__init__") else name


def _dotted(node):
    """The dotted text of a name or attribute chain (`a.b.c`), or None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return ".".join([node.id, *reversed(parts)])


def import_source(importer, node):
    """The dotted module an ImportFrom reads, with its relative dots resolved against the importer's package."""
    if not node.level:
        return node.module or ""
    package = module_name(importer).split(".")
    if not importer.endswith("__init__.py"):
        package = package[:-1]
    package = package[: len(package) - (node.level - 1)]
    return ".".join([*package, *([node.module] if node.module else [])])


def links(tree, importer, target, wanted):
    """How `importer` reaches the module `target` (dotted): the names its code can use for the module, the names of
    `wanted` it imports (local alias -> imported name), and the names it re-exports at module level."""
    modules, local = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is None and (alias.name == target or alias.name.startswith(target + ".")):
                    modules.add(target)  # `import pkg.mod` binds pkg, and pkg.mod is then reachable as dotted text
                elif alias.asname and alias.name == target:
                    modules.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            source = import_source(importer, node)
            for alias in node.names:
                if source == target:
                    if alias.name == "*":
                        local.update({name: name for name in wanted})
                    elif alias.name in wanted:
                        local[alias.asname or alias.name] = alias.name
                elif f"{source}.{alias.name}" == target:
                    modules.add(alias.asname or alias.name)
    reexports = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and import_source(importer, stmt) == target:
            for alias in stmt.names:
                if alias.name == "*":
                    reexports |= set(wanted)
                elif alias.name in wanted:
                    reexports.add(alias.asname or alias.name)
    return modules, local, reexports


def _module_nodes(stmt):
    """The nodes a module-level statement evaluates when it runs: all of it, except a def's body (its decorators,
    defaults and return annotation run with the statement)."""
    stack = [stmt]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stack.extend(node.decorator_list)
            stack.extend(node.args.defaults)
            stack.extend(default for default in node.args.kw_defaults if default is not None)
            if node.returns is not None:
                stack.append(node.returns)
        else:
            stack.extend(ast.iter_child_nodes(node))


def uses(tree, names, local, modules=(), dotted=None, attr_any=False, same_file=False):
    """Where `tree` uses one of `names`, a set of module-level names: (the functions that do, by qualname; whether module
    code does; whether a use cannot be resolved).

    A load of a name in `local` (local alias -> imported name) is a use, and so is `m.NAME` when `m` is one of
    `modules` (dotted text), or any `x.NAME` with attr_any. A string `dotted + ".NAME..."` names NAME (a monkeypatch
    target). Unresolved: the module's own dotted name as a string, a dynamic lookup into the module (globals() in the
    module's own file, getattr(m, ...) on one of its modules).
    """
    unresolved = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if same_file and node.func.id in DYNAMIC_GLOBALS:
                unresolved = True
            if modules and node.func.id in DYNAMIC_MODULE and node.args and _dotted(node.args[0]) in modules:
                unresolved = True
        if dotted and isinstance(node, ast.Constant) and node.value == dotted:
            unresolved = True

    def use(node):
        if isinstance(node, ast.Name):
            return isinstance(node.ctx, ast.Load) and node.id in local
        if isinstance(node, ast.Attribute):
            return node.attr in names and (attr_any or _dotted(node.value) in modules)
        if dotted and isinstance(node, ast.Constant) and isinstance(node.value, str):
            prefix = dotted + "."
            return node.value.startswith(prefix) and node.value[len(prefix) :].split(".")[0] in names
        return False

    quals = set()
    for kind, qual, *_rest, node in _scopes(tree):
        if kind == "function" and any(use(child) for child in ast.walk(node)):
            quals.add(qual)
    module_level = any(use(child) for stmt in tree.body for child in _module_nodes(stmt))
    return quals, module_level, unresolved


def check_map(data):
    """Raise MapError unless `data` is a map this version of the selector reads."""
    if not isinstance(data, dict) or set(data) != KEYS:
        raise MapError(f"the map must have exactly the keys {sorted(KEYS)}")
    if data["version"] != VERSION:
        raise MapError(f"map version {data['version']!r}, expected {VERSION}")
    if not isinstance(data["sha"], str) or not SHA.fullmatch(data["sha"]):
        raise MapError("sha is not a full commit id")
    try:
        parse_time(data["measured_at"])
    except ValueError as error:
        raise MapError(f"measured_at: {error}") from error
    tests = data["tests"]
    if not isinstance(tests, list) or tests != sorted(set(tests)):
        raise MapError("tests must be a sorted list of unique names")
    if not all(isinstance(t, str) and is_test(t) for t in tests):
        raise MapError("every entry of tests must be a Python test file")
    if not isinstance(data["partial"], list) or data["partial"] != sorted(set(data["partial"])):
        raise MapError("partial must be a sorted list of unique test indexes")
    if not all(isinstance(i, int) and 0 <= i < len(tests) for i in data["partial"]):
        raise MapError("partial holds an index outside tests")
    files = data["files"]
    if not isinstance(files, dict):
        raise MapError("files must be an object")
    for rel, funcs in files.items():
        if not isinstance(funcs, dict) or not rel.endswith(".py") or rel.startswith("/") or ".." in rel.split("/"):
            raise MapError(f"bad file entry {rel!r}")
        for qual, ids in funcs.items():
            if qual != MODULE and not all(part.isidentifier() for part in qual.split(".")):
                raise MapError(f"bad function name {qual!r} in {rel}")
            if not isinstance(ids, list) or ids != sorted(set(ids)):
                raise MapError(f"{rel} {qual}: tests must be a sorted list of unique indexes")
            if not all(isinstance(i, int) and 0 <= i < len(tests) for i in ids):
                raise MapError(f"{rel} {qual}: index outside tests")
    hashes = data["hashes"]
    if not isinstance(hashes, dict):
        raise MapError("hashes must be an object")
    for rel, digests in hashes.items():
        if rel not in files or not isinstance(digests, dict):
            raise MapError(f"hashes name {rel!r}, which has no entry in files")
        for qual, digest in digests.items():
            if qual == MODULE or qual not in files[rel] or not isinstance(digest, str) or not DIGEST.fullmatch(digest):
                raise MapError(f"bad hash for {rel} {qual}")


def load_map(path=None, now=None):
    """(map, note). The map the selector may use, or None and the reason the static map applies. Never raises."""
    path = path or os.environ.get(MAP_ENV, "")
    if not path:
        return None, "no measured Python map (TICO_PY_TEST_MAP is unset)"
    if not Path(path).is_file():
        return None, "no measured Python map on this checkout"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        check_map(data)
    except (OSError, ValueError, MapError) as error:
        return None, f"measured Python map unreadable: {error}"
    measured = parse_time(data["measured_at"])
    now = now or datetime.now(timezone.utc)
    if now - measured > MAX_AGE:
        return None, f"measured Python map is {(now - measured).days} days old (limit {MAX_AGE.days})"
    return data, ""


def _relative(name):
    path = Path(name)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return None


def coverage_lines(data_dir, test):
    """{relative path: executed line numbers} from the data files one test file's processes wrote.

    Every executed line must carry this test file as its context. Any other context means the measurement is wrong, so
    the build stops rather than attribute the line to the wrong test.
    """
    from coverage import CoverageData

    lines = defaultdict(set)
    for path in sorted(Path(data_dir).glob("cov.*")):
        data = CoverageData(basename=str(path))
        data.read()
        for name in data.measured_files():
            contexts = data.contexts_by_lineno(name)
            for line, labels in contexts.items():
                stray = set(labels) - {test}
                if stray:
                    raise MapError(f"{test}: line {line} of {name} ran under context {sorted(stray)}")
            rel = _relative(name)
            if rel is None or not rel.endswith(".py") or is_test(rel) or rel.endswith("conftest.py"):
                continue
            if rel == HARNESS:  # the driver that runs the test runs in this process: its lines are not the test's code
                continue
            lines[rel].update(contexts.keys())
    return lines


def install_hook(config, context, folder=None):
    """Install the subprocess hook into the running interpreter's site-packages (the measuring venv), or into `folder`.

    `config` and `context` are the paths start() reads, baked into the .pth line.
    """
    folder = Path(folder or sysconfig.get_paths()["purelib"])
    (folder / "tico_py_test_map_coverage.py").write_text(HOOK_MODULE, encoding="utf-8")
    target = folder / "tico_py_test_map_coverage.pth"
    target.write_text(HOOK_LINE.format(config=str(config), context=str(context)), encoding="utf-8")
    return target


def run_one(test, data_dir, rc, log):
    """One test file in its own process, under coverage. Returns the exit code, or None when the process timed out."""
    env = {
        **os.environ,
        "COVERAGE_CORE": CORE,
        "COVERAGE_PROCESS_START": str(rc),
        "TICO_TEST_CONTEXT": test,
        "TICO_COV_OUT": str(data_dir),
    }
    driver = [sys.executable, "-c", DRIVER, test]
    with open(log, "w", encoding="utf-8") as stream:
        try:
            done = subprocess.run(
                driver, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=TEST_SECONDS, check=False
            )
        except subprocess.TimeoutExpired:
            return None
    return done.returncode


def collect_default():
    """The default suite's test files: what pytest collects with the repo's own addopts (-m "not slow")."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-n", "0"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise MapError("Default collection failed:\n" + result.stdout[-2000:] + result.stderr[-2000:])
    files = sorted(
        {line.split("::", 1)[0] for line in result.stdout.splitlines() if "::" in line and not line.startswith(" ")}
    )
    if not files:
        raise MapError("Default suite collected no tests")
    return files


def load_timings():
    """Measured seconds per default-suite file (scripts/test-timings.json); the balance of shards uses these."""
    return json.loads(TIMINGS.read_text(encoding="utf-8")).get("default", {})


def assign(files, timings, shard, of):
    """Shard `shard` of `of`: longest-first greedy, so every shard gets about the same measured seconds. Deterministic."""
    buckets = [[] for _ in range(of)]
    heap = [(0.0, index) for index in range(of)]
    for name in sorted(files, key=lambda f: (-timings.get(f, UNTIMED_SECONDS), f)):
        load, index = heapq.heappop(heap)
        buckets[index].append(name)
        heapq.heappush(heap, (load + timings.get(name, UNTIMED_SECONDS), index))
    return sorted(buckets[shard])


def measure(shard, of, out, data):
    """Measure one shard: each of its test files alone, under coverage. Writes out/partial-<shard>.json."""
    out.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    (data / "logs").mkdir(exist_ok=True)
    rc = data / "coverage.rc"
    rc.write_text(RC, encoding="utf-8")
    context = data / "coverage-context.json"
    install_hook(rc, context)
    collected = collect_default()
    mine = assign(collected, load_timings(), shard, of)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    tests, files, hashes = {}, defaultdict(lambda: defaultdict(set)), {}
    for index, test in enumerate(mine):
        run_dir = data / f"t{index}"
        run_dir.mkdir()
        log = data / "logs" / f"t{index}.log"
        context.write_text(json.dumps({"core": CORE, "test": test, "out": str(run_dir)}), encoding="utf-8")
        started = time.monotonic()
        code = run_one(test, run_dir, rc, log)
        seconds = round(time.monotonic() - started, 1)
        wrote = any(run_dir.glob("cov.*"))
        found = coverage_lines(run_dir, test) if wrote else {}
        partial = code != 0 or not wrote
        tests[test] = {"exit": code, "partial": partial, "seconds": seconds}
        if partial:
            tests[test]["tail"] = log.read_text(encoding="utf-8", errors="replace")[-1500:]
        try:
            for rel, executed in found.items():
                if not (ROOT / rel).is_file():
                    continue  # a file the test made and removed, or one the tree does not keep: nothing to map
                text = (ROOT / rel).read_text(encoding="utf-8")
                if rel not in hashes:
                    hashes[rel] = function_hashes(text)
                own = owners(text)
                for qual in owner_names(own, executed):
                    files[rel][qual].add(test)
        except (MapError, OSError, SyntaxError, ValueError) as error:
            # Coverage that cannot be attributed makes this test partial, not the whole shard: the selector treats a
            # partial test as unmeasured.
            tests[test]["partial"] = True
            tests[test]["tail"] = (tests[test].get("tail", "") + f"\ncoverage not attributed: {error}")[-1500:]
        shutil.rmtree(run_dir)
    context.unlink(missing_ok=True)  # the run is over: a process that starts later in this venv is not measured
    rc.unlink(missing_ok=True)
    payload = {
        "version": VERSION,
        "sha": sha,
        "shard": shard,
        "of": of,
        "collected": collected,
        "tests": tests,
        "files": {rel: {qual: sorted(names) for qual, names in funcs.items()} for rel, funcs in files.items()},
        "hashes": {rel: {qual: digest for qual, digest in hashes[rel].items() if qual in files[rel]} for rel in files},
    }
    (out / f"partial-{shard}.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    print(f"shard {shard}/{of}: {len(mine)} test files, {sum(t['partial'] for t in tests.values())} partial")


def build(partials, out, sha, run="", now=None):
    """The map from every shard's partial. Refuses unless the shards agree on the commit and cover the suite."""
    parts = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(Path(partials).rglob("partial-*.json"))]
    if not parts:
        raise MapError(f"no partial maps under {partials}")
    collected, of = parts[0]["collected"], parts[0]["of"]
    for part in parts:
        if part["version"] != VERSION or part["sha"] != sha or part["collected"] != collected or part["of"] != of:
            raise MapError("the partial maps disagree on the version, the commit or the test list")
    shards = sorted(part["shard"] for part in parts)
    if shards != list(range(of)):
        raise MapError(f"shards {shards} measured, but the run has {of}: each shard must be present once")
    statuses = {}
    for part in parts:
        for test, status in part["tests"].items():
            if test in statuses:
                raise MapError(f"{test} was measured twice")
            statuses[test] = status
    missing = [test for test in collected if test not in statuses]
    if missing:
        raise MapError(f"{len(missing)} collected test files were not measured, first {missing[0]}")
    index = {name: position for position, name in enumerate(collected)}
    files = defaultdict(lambda: defaultdict(set))
    hashes = defaultdict(dict)
    for part in parts:
        for rel, funcs in part["files"].items():
            for qual, names in funcs.items():
                files[rel][qual].update(index[name] for name in names)
        for rel, digests in part["hashes"].items():
            hashes[rel].update(digests)
    data = {
        "version": VERSION,
        "sha": sha,
        "measured_at": (now or datetime.now(timezone.utc)).strftime(FORMAT),
        "run": run,
        "tool": {
            "coverage": COVERAGE,
            "core": CORE,
            "isolation": "one pytest process per test file, -n 0",
            "addopts": "pytest.ini (-m 'not slow')",
        },
        "tests": collected,
        "partial": sorted(index[name] for name in collected if statuses[name]["partial"]),
        "files": {
            rel: {qual: sorted(ids) for qual, ids in sorted(funcs.items())} for rel, funcs in sorted(files.items())
        },
        "hashes": {rel: dict(sorted(hashes[rel].items())) for rel in sorted(files) if hashes.get(rel)},
    }
    check_map(data)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return data


def summary(data):
    """Markdown lines for the job summary: sizes and the spread of tests per function."""
    counts = [len(ids) for funcs in data["files"].values() for qual, ids in funcs.items() if qual != MODULE]
    counts.sort()
    functions = len(counts)
    median = counts[functions // 2] if functions else 0
    return [
        f"Measured {data['sha']} at {data['measured_at']}",
        "",
        f"- test files: {len(data['tests'])} ({len(data['partial'])} partial, always treated as unmeasured)",
        f"- source files: {len(data['files'])}, functions with tests: {functions}",
        f"- tests per function: median {median}, max {counts[-1] if counts else 0}",
    ]


def overhead(sample, out):
    """Wall-clock cost of coverage: a spread of default-suite files, each run plain and then under coverage."""
    files = collect_default()
    step = max(1, len(files) // sample)
    picked = files[::step][:sample]
    out.parent.mkdir(parents=True, exist_ok=True)
    scratch = out.parent / "overhead"
    scratch.mkdir(exist_ok=True)
    install_hook(scratch / "coverage.rc", scratch / "coverage-context.json")  # no context file: nothing it measures
    rc = scratch / "coverage.rc"
    rc.write_text(RC, encoding="utf-8")
    rows = []
    for index, test in enumerate(picked):
        started = time.monotonic()
        plain = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-n", "0", *PYTEST_FLAGS, "-p", "no:cacheprovider", test],
            cwd=ROOT,
            capture_output=True,
            check=False,
        ).returncode
        plain_seconds = time.monotonic() - started
        run_dir = scratch / f"t{index}"
        run_dir.mkdir(exist_ok=True)
        started = time.monotonic()
        covered = run_one(test, run_dir, rc, scratch / f"t{index}.log")
        covered_seconds = time.monotonic() - started
        shutil.rmtree(run_dir)
        rows.append(
            {
                "test": test,
                "plain_seconds": round(plain_seconds, 2),
                "covered_seconds": round(covered_seconds, 2),
                "plain_exit": plain,
                "covered_exit": covered,
            }
        )
    plain_total = sum(row["plain_seconds"] for row in rows)
    covered_total = sum(row["covered_seconds"] for row in rows)
    result = {
        "coverage": COVERAGE,
        "core": CORE,
        "files": len(rows),
        "plain_seconds": round(plain_total, 1),
        "covered_seconds": round(covered_total, 1),
        "ratio": round(covered_total / plain_total, 3) if plain_total else None,
        "rows": rows,
    }
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def run_pytest_one(test):
    """The driver: pytest on one test file. Coverage is already running (the subprocess hook started it)."""
    import pytest

    return pytest.main(["-q", "-n", "0", *PYTEST_FLAGS, "-p", "no:cacheprovider", test])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Measure and check the Python test map (see the module docstring).")
    commands = parser.add_subparsers(dest="command", required=True)
    shard = commands.add_parser("measure", help="measure one shard of the default suite")
    shard.add_argument("--shard", type=int, required=True)
    shard.add_argument("--of", type=int, default=SHARDS)
    shard.add_argument("--out", type=Path, required=True)
    shard.add_argument(
        "--data", type=Path, default=Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())) / "py-test-map-data"
    )
    built = commands.add_parser("build", help="merge partial maps into the map")
    built.add_argument("--partials", type=Path, required=True)
    built.add_argument("--out", type=Path, required=True)
    built.add_argument("--sha", required=True)
    built.add_argument("--run", default="")
    checked = commands.add_parser("check", help="check a map's schema")
    checked.add_argument("--map", type=Path, required=True)
    timed = commands.add_parser("overhead", help="measure coverage's cost on a sample of test files")
    timed.add_argument("--sample", type=int, default=10)
    timed.add_argument("--out", type=Path, required=True)
    listed = commands.add_parser("shard", help="print the files of one measuring shard")
    listed.add_argument("index", type=int)
    listed.add_argument("--of", type=int, default=SHARDS)
    args = parser.parse_args(argv)
    if args.command == "measure":
        measure(args.shard, args.of, args.out, args.data)
    elif args.command == "build":
        data = build(args.partials, args.out, args.sha, args.run)
        print("\n".join(summary(data)))
    elif args.command == "check":
        check_map(json.loads(args.map.read_text(encoding="utf-8")))
        print(f"{args.map}: a valid map")
    elif args.command == "overhead":
        result = overhead(args.sample, args.out)
        print(f"coverage {result['coverage']} {result['core']}: {result['ratio']}x over {result['files']} files")
    elif args.command == "shard":
        print("\n".join(assign(collect_default(), load_timings(), args.index, args.of)))
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except MapError as error:
        print(f"::error::{error}" if os.environ.get("GITHUB_ACTIONS") else f"error: {error}", file=sys.stderr)
        code = 1
    raise SystemExit(code)
