#!/usr/bin/env python3
"""Conservative PR selection, measured Python and browser groups, and read-only check receipts."""

import argparse
import ast
from collections import Counter, defaultdict
import fnmatch
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
UI_MAP = {
    "ui/app/chat.js": ["chat", "imported-history"],
    "ui/app/chat-outline.js": ["chat", "imported-history"],
    "ui/app/visual-cards.js": ["visual-cards", "visual-fallback", "visual-hosted"],
    "ui/app/visual-media.js": ["visual-media", "visual-media-app"],
}
CONFORMANCE = "runner_support/tests/test_conformance.py"
# These release contracts are marked slow and absent from default collection. Run them together on a separate VM:
# recorded release times are CLI 108s, MCP 34s and goldens 3s; four workers keep it below the 180s job target with setup.
CONTRACT_TESTS = ("backend/tests/test_e2e_mcp.py", "backend/tests/test_e2e_cli.py", "e2e/tests/test_contract.py")
CONTRACT_INPUTS = (
    "backend/",
    "clients/",
    "e2e/",
    "templates/",
    "skills/",
    "roles/",
    "company/",
    "companies/",
    "company.yaml",
    "employees.yaml",
    "roles.yaml",
)
# Each crate's `cargo test --locked` runs in the conformance job when any of its test inputs change:
# runner-rs tests read runner_support/ and runner/ (the Python helpers they compare against: source_id, the redactor)
# and the backend's gateway enroll contract fixture, which gateway_relay's tests check the runner against, and the runner wire
# contract's recorded answers (runner-rs/src/wire.rs).
RUST_INPUTS = {
    "relay-rs": ("relay-rs/",),
    "runner-rs": (
        "runner-rs/",
        "runner_support/",
        "runner/",
        "backend/tests/fixtures/gateway_enroll_contract.json",
        "contracts/runner/",
    ),
}
CHANGELOG_REFUSAL = (
    "No PR edits CHANGELOG.md; the Technical Writer writes notes at the release cut. Remove your CHANGELOG.md changes."
)
RELEASE_TRAILER = re.compile(r"^Changelog-Release:[ \t]*v?(\d+\.\d+\.\d+)[ \t]*$", re.M)
# Each check VM has 4 vCPUs (the largest size these checks use) and runs 4 test workers. Groups are sized so a VM
# finishes in about TICO_CI_JOB_SECONDS, setup included. Short Spot jobs are rarely interrupted and lose little when one is.
# The cap only guards against a runaway plan. At 16 it held the suite to 16 VMs once the measured default suite grew
# past 16 x 540 s (Oct 23: 3,300 one-worker seconds; Nov 5: 39,900), so every full run's shards took 11-14 minutes.
WORKERS = 4
JOB_SECONDS = float(os.environ.get("TICO_CI_JOB_SECONDS", 300))
# Measured boot, checkout and warm install; "slow" is an estimate (boot, install, the cached relay build).
SETUP_SECONDS = {"python": 45, "browser": 70, "slow": 90}
MAX_JOBS = int(os.environ.get("TICO_CI_MAX_JOBS", 64))
# Python jobs pass the release gate's own flags: restore a worker-local schema snapshot instead of migrating a fresh
# database for every API test (about half of each file's time), and hand out at most two tests at a time so expensive
# tests spread across the workers.
PYTEST_FLAGS = ("--api-database-snapshots", "--maxschedchunk=2")
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
    commits += [
        merge
        for merge in git("log", "--merges", "--format=%H", f"{base}..{head}").splitlines()
        if merge_writes(git("show", "--cc", "--format=", merge, "--", "CHANGELOG.md"))
    ]
    if (
        changed == ["CHANGELOG.md"]
        and commits
        and all(RELEASE_TRAILER.search(git("show", "-s", "--format=%B", commit)) for commit in commits)
    ):
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
    """A file pytest collects by name (pytest's default python_files); prepare() keeps the ones in the default run."""
    base = Path(name).name
    return base.endswith(".py") and (base.startswith("test_") or base.endswith("_test.py"))


def is_check(name):
    """A Python test file or a browser script (ui/tests/*.cjs)."""
    return is_test(name) or (str(Path(name).parent) == "ui/tests" and name.endswith(".cjs"))


def dotted_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return dotted_name(node.value) + "." + node.attr
    if isinstance(node, ast.Call):
        return dotted_name(node.func)
    return ""


def git_files(root):
    try:
        out = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
        return [p for p in out if p and (root / p).is_file()]
    except (OSError, subprocess.CalledProcessError):
        skip = {".venv", "node_modules", ".git", "build", ".cache", "target", "__pycache__"}
        return [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and not skip & set(p.parts)]


TOKEN = re.compile(r"[\w.\-/]+")
IDENTIFIER = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")
CODE = {".py", ".sh", ".bash", ".js", ".cjs", ".mjs", ".ts"}
PATHLIKE = {
    "Path",
    "PurePath",
    "PosixPath",
    "open",
    "join",
    "joinpath",
    "with_name",
    "spec_from_file_location",
    "run_path",
    "copy",
    "copy2",
    "copyfile",
    "copytree",
    "exists",
    "isfile",
    "isdir",
    "listdir",
    "scandir",
    "walk",
    "read_text",
    "read_bytes",
    "relpath",
    "realpath",
    "abspath",
    "files",
}
RUNNERS = {
    "run",
    "check_output",
    "check_call",
    "call",
    "Popen",
    "execv",
    "execvp",
    "execvpe",
    "spawn",
    "create_subprocess_exec",
    "create_subprocess_shell",
    "system",
}
GLOBS = {"glob", "rglob", "iglob"}
LISTINGS = {"iterdir", "listdir", "scandir", "walk"}
OS_LISTINGS = {"os.listdir", "os.walk", "os.scandir", "glob.glob", "glob.iglob"}
# Calls that read, copy, run, hash or measure files; a listing in a function without one uses only names.
CONTENT = {
    "read_text",
    "read_bytes",
    "open",
    "load",
    "loads",
    "safe_load",
    "parse",
    "copy",
    "copy2",
    "copyfile",
    "copytree",
    "copyfileobj",
    "exec_module",
    "run",
    "check_output",
    "check_call",
    "Popen",
    "call",
    "stat",
    "lstat",
    "getsize",
    "getmtime",
    "digest",
    "hexdigest",
    "update",
    "compile",
    "import_module",
    "rmtree",
    "unlink",
    "rename",
    "replace",
    "move",
    "write_text",
    "write_bytes",
    "hash_file",
    "file_digest",
}


class DependencyMap:
    """What each tracked file runs and reads, measured statically, so a change selects every test that can reach it.

    A file *runs* the Python modules it imports (at module level or in a function, with their packages' __init__
    files), literal importlib/__import__/pytest_plugins and `-m` names, dotted literals naming a tracked module
    (`"backend.app:app"`, monkeypatch targets) and code files it names by path (scripts, shell, JavaScript). It *reads*
    the other tracked files and folders it names: path joins, open(), Path(), subprocess arguments, cwd=, globs. Shell
    and JavaScript files are scanned for the paths and dotted modules they name. A change reaches whoever runs the
    changed file, whoever runs those, and so on; whoever reads it (directly, through a folder that holds it, or through
    a glob on an unknown folder) is reached too, but reading a file whose dependencies changed is not running it. `git
    ls-files` or a computed import makes a file depend on everything. Conftest code applies to the tests below it, a
    non-autouse conftest fixture body only to the tests that name the fixture.
    """

    def __init__(self, root):
        self.root = root
        self.files = git_files(root)
        self.tracked = set(self.files)
        self.consts = {}  # module-level NAME = "text" of the file being read, which path joins substitute
        modules = {str(Path(p).with_suffix("")).replace("/", "."): p for p in self.files if p.endswith(".py")}
        self.modules = {(m[:-9] if m.endswith(".__init__") else m): p for m, p in modules.items()}
        self.by_base, self.dirs = defaultdict(set), defaultdict(set)
        for path in self.files:
            self.by_base[Path(path).name].add(path)
            for parent in list(Path(path).parents)[:-1]:
                self.dirs[parent.name].add(str(parent))
        self.runs, self.reads = defaultdict(set), defaultdict(set)  # file -> files ("folder/" for a folder)
        self.globs = defaultdict(set)  # file -> basename patterns it lists in folders it does not name
        self.listings = defaultdict(set)  # file -> (folder, pattern, recursive, names only) it lists
        self.everything, self.unparsed = set(), set()
        self.fixtures = defaultdict(dict)  # conftest -> {fixture: (runs, reads)}
        self.names = {}  # Python file -> its identifiers and strings (fixture names it uses)
        trees = {}
        for path in self.files:
            if path.endswith(".py"):
                try:
                    trees[path] = ast.parse((root / path).read_bytes())
                except (OSError, SyntaxError, ValueError):
                    self.unparsed.add(path)
                    self.everything.add(path)
        # One breadth-first pass per tree (ast.walk's order) feeds every scan: walking each tree once per scan was
        # most of the map's build time, which every PR's plan pays and the selector's tests pay once per worker.
        walked = {path: self.walk(tree) for path, tree in trees.items()}
        # Top-level names imported through sys.path (`import hubcli` with clients/ on the path).
        self.bare = {
            a.name.split(".")[0]
            for nodes, _ in walked.values()
            for node in nodes
            if isinstance(node, ast.Import)
            for a in node.names
        }
        self.bare |= {
            node.module.split(".")[0]
            for nodes, _ in walked.values()
            for node in nodes
            if isinstance(node, ast.ImportFrom) and not node.level and node.module
        }
        self.bare = {
            name for name in self.bare - set(self.modules) if any(m.endswith("." + name) for m in self.modules)
        }
        for path, tree in trees.items():
            self.python(path, tree, walked.pop(path))
        for path in self.files:
            if Path(path).suffix in CODE - {".py"} or (not Path(path).suffix and self.shebang(path)):
                self.text(path)
        self.run_by, self.read_by = defaultdict(set), defaultdict(set)
        for edges, reverse in ((self.runs, self.run_by), (self.reads, self.read_by)):
            for path, targets in edges.items():
                for target in targets - {path}:
                    reverse[target].add(path)

    @staticmethod
    def walk(tree):
        """Every node, in ast.walk's order, and each node's parent by id."""
        nodes, parents = [tree], {}
        for node in nodes:
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node
                nodes.append(child)
        return nodes, parents

    def shebang(self, path):
        try:
            with open(self.root / path, "rb") as stream:
                return stream.read(2) == b"#!"
        except OSError:
            return False

    def code(self, path):
        return Path(path).suffix in CODE or (not Path(path).suffix and self.shebang(path))

    def module(self, name, bare=False):
        """The files importing a dotted name runs: the longest tracked prefix and its packages, else a sys.path module."""
        parts = name.split(".")
        for end in range(len(parts), 0, -1):
            if ".".join(parts[:end]) in self.modules:
                return {
                    self.modules[".".join(parts[:i])] for i in range(1, end + 1) if ".".join(parts[:i]) in self.modules
                }
        if bare and parts[0] in self.bare:
            for end in range(len(parts), 0, -1):
                found = {p for m, p in self.modules.items() if m.endswith("." + ".".join(parts[:end]))}
                if found:
                    return found
        return set()

    def path(self, token, runs, reads, bare_word=False, module=True):
        """A literal that names tracked files or folders: `a/b.json` by path suffix, `b.json` by name, and with
        bare_word (a path join, open(), subprocess argument) a plain name like `fixtures` or `tico` too."""
        token = token.strip()
        while token.startswith(("./", "../", "/")):
            token = token[3:] if token.startswith("../") else token[2:] if token.startswith("./") else token[1:]
        token = token.rstrip("/")
        if token in ("", ".", ".."):
            return
        name = token.rsplit("/", 1)[-1]
        if token in self.tracked:  # a whole repository path, even with no folder or extension: "LICENSE"
            (runs if self.code(token) else reads).add(token)
        if "/" in token or "." in name.strip(".") or bare_word:
            for found in self.by_base.get(name, ()):
                if found == token or found.endswith("/" + token):
                    (runs if self.code(found) else reads).add(found)
        if bare_word:  # a folder only where a path is built or opened, not in free text or a pattern
            reads.update(d + "/" for d in self.dirs.get(name, ()) if d == token or d.endswith("/" + token))
        if module and IDENTIFIER.match(token) and token.split(".")[0] in self.modules:
            runs.update(self.module(token))  # "backend.app:app", "backend.store.Store.method"

    @staticmethod
    def strings(node):
        """Literal text in an expression: whole constants and the fixed parts of f-strings."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    yield value.value

    def paths_of(self, node):
        """The literal paths an expression names: `ROOT / "docs" / "api.md"` -> docs/api.md, a name bound to one,
        Path("ui", "app"); the literal parts up to the first computed one."""
        if isinstance(node, ast.Name):
            return set(self.values.get(node.id, ()))
        if isinstance(node, ast.Call) and dotted_name(node.func).rsplit(".", 1)[-1] in (
            "Path",
            "PurePath",
            "PosixPath",
        ):
            parts = node.args
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            parts = self.chain(node)
        else:
            return set()
        if not parts:
            return set()
        head = parts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            prefixes, rest = {head.value.strip("/")}, parts[1:]
        else:
            prefixes, rest = self.paths_of(head) or {""}, parts[1:]
        literal = bool(self.paths_of(head)) or isinstance(head, ast.Constant)
        for part in rest:
            if not (isinstance(part, ast.Constant) and isinstance(part.value, str)):
                break
            prefixes = {"/".join(x for x in (prefix, part.value.strip("/")) if x) for prefix in prefixes}
            literal = True
        return {p for p in prefixes if p} if literal else set()

    def chain(self, node):
        """Operands of an a / "b" / "c" path expression, in order; a module-level text constant counts as its text."""
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self.chain(node.left) + self.chain(node.right)
        if isinstance(node, ast.Name) and node.id in self.consts:
            return [ast.Constant(self.consts[node.id])]
        return [node]

    def joined(self, parts, runs, reads):
        """Adjacent literal parts of a path join name one path, as `root / "docs" / "api.md"` names docs/api.md."""
        run = []
        for part in [*parts, None]:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                run.append(part.value.strip("/"))
                continue
            if run:
                self.path("/".join(run), runs, reads, bare_word=True)
                run = []
            for text in self.strings(part) if part is not None else ():
                for token in TOKEN.findall(text):
                    self.path(token, runs, reads, bare_word=True)

    def python(self, path, tree, walked):
        runs, reads, names = set(), set(), set()
        package = path.split("/")[:-1]
        nodes, parents = walked
        self.consts = {}
        for statement in tree.body:
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                self.consts[statement.targets[0].id] = statement.value.value
        bodies = [tree.body] + [
            n.body for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        docstrings = {
            id(b[0].value) for b in bodies if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant)
        }
        nested = {id(n.left) for n in nodes if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)}
        scope = {}
        # Module-level names that hold a path found from __file__: ROOT = Path(__file__).parents[2]; DOCS = ROOT / "docs".
        # A name whose path joins a literal ("docs") names that folder, which its definition reads.
        located, named_here = set(), set()
        for statement in tree.body:
            if (
                isinstance(statement, (ast.Assign, ast.AnnAssign))
                and statement.value is not None
                and any(
                    isinstance(n, ast.Name) and (n.id == "__file__" or n.id in located)
                    for n in ast.walk(statement.value)
                )
            ):
                literal = any(isinstance(n, ast.Name) and n.id in named_here for n in ast.walk(statement.value)) or any(
                    isinstance(part, ast.Constant) and isinstance(part.value, str)
                    for n in ast.walk(statement.value)
                    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
                    for part in self.chain(n)
                )
                for target in statement.targets if isinstance(statement, ast.Assign) else [statement.target]:
                    found = {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
                    located |= found
                    named_here |= found if literal else set()
        # Names bound to a literal path (FIXTURES = Path(__file__).parent / "fixtures"): where such a name is joined
        # further, the joined path is what is read; where it is listed, only the listing.
        self.values, deferred = defaultdict(set), set()
        for _ in range(2):
            for node in nodes:
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    found = self.paths_of(node.value)
                    if found:
                        self.values[node.targets[0].id] |= found
                        deferred.add(id(node.value))
        # A listing uses only names when nothing in the file reads file contents: a read elsewhere in the module (at
        # module level, in another function or a helper it calls) may read what was listed, so edits reach it too.
        reads_content = any(
            isinstance(n, ast.Call) and dotted_name(n.func).rsplit(".", 1)[-1] in CONTENT for n in nodes
        )
        listed = set()
        for node in nodes:
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in GLOBS | LISTINGS
            ):
                listed.add(id(node.func.value))
            elif isinstance(node, ast.Call) and dotted_name(node.func) in OS_LISTINGS and node.args:
                listed.add(id(node.args[0]))
        if Path(path).name == "conftest.py":
            for node in nodes:
                for decorator in (
                    getattr(node, "decorator_list", [])
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    else []
                ):
                    call = decorator if isinstance(decorator, ast.Call) else None
                    keywords = {k.arg: k.value for k in call.keywords} if call else {}
                    autouse = keywords.get("autouse")
                    if dotted_name(call.func if call else decorator).split(".")[-1] == "fixture" and (
                        autouse is None or isinstance(autouse, ast.Constant) and autouse.value is False
                    ):
                        name = keywords.get("name")
                        name = name.value if isinstance(name, ast.Constant) else node.name
                        self.fixtures[path][name] = (set(), set())
                        scope.update({id(n): self.fixtures[path][name] for n in ast.walk(node)})
        for node in nodes:
            runs_, reads_ = scope.get(id(node), (runs, reads))
            if isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    runs_ |= self.module(alias.name, bare=True)
            elif isinstance(node, ast.ImportFrom):
                prefix = package[: len(package) - node.level + 1] if node.level else []
                module = ".".join([*prefix, *([node.module] if node.module else [])])
                runs_ |= self.module(module, bare=not node.level)
                for alias in node.names:
                    runs_ |= self.module(module + "." + alias.name, bare=not node.level)
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and id(node) not in nested:
                parts = self.chain(node)
                if id(node) not in deferred | listed:
                    for found in self.paths_of(node):
                        self.path(found, runs_, reads_, bare_word=True)
                start = next(
                    (i for i, part in enumerate(parts[1:], 1) if not isinstance(part, ast.Constant)), len(parts)
                )
                self.joined(parts if not self.paths_of(node) else parts[start:], runs_, reads_)
            elif (
                isinstance(node, ast.Name)
                and node.id in self.values
                and isinstance(node.ctx, ast.Load)
                and id(node) not in listed
            ):
                parent = parents.get(id(node))
                if not (isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div) and parent.left is node):
                    for found in self.values[node.id]:
                        self.path(found, runs_, reads_, bare_word=True)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in targets):
                    for item in ast.walk(node.value):
                        for text in self.strings(item):
                            runs_ |= self.module(text, bare=True)
            elif isinstance(node, ast.Call):
                function = dotted_name(node.func)
                last = function.rsplit(".", 1)[-1]
                if last in PATHLIKE:
                    self.joined(node.args, runs_, reads_)
                if last in RUNNERS or any(k.arg == "cwd" for k in node.keywords):
                    previous = None
                    for argument in [*node.args, *(k.value for k in node.keywords)]:
                        for item in argument.elts if isinstance(argument, (ast.List, ast.Tuple)) else [argument]:
                            for text in self.strings(item):
                                if previous == "-m":
                                    runs_ |= self.module(text, bare=True)
                                for token in TOKEN.findall(text):
                                    self.path(token, runs_, reads_, bare_word=True)
                            previous = item.value if isinstance(item, ast.Constant) else None
                if last in ("import_module", "__import__"):
                    first = node.args[0] if node.args else None
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        if first.value.startswith("."):
                            level = len(first.value) - len(first.value.lstrip("."))
                            runs_ |= self.module(
                                ".".join(package[: len(package) - level + 1] + [first.value.lstrip(".")])
                            )
                        else:
                            runs_ |= self.module(first.value, bare=True)
                    elif isinstance(first, (ast.JoinedStr, ast.BinOp)) and isinstance(
                        head := first.values[0] if isinstance(first, ast.JoinedStr) else first.left, ast.Constant
                    ):
                        # f"backend.connectors.{name}", "." + name: every module under the fixed package
                        fixed = str(head.value)
                        if fixed.startswith("."):
                            level = len(fixed) - len(fixed.lstrip("."))
                            fixed = ".".join(package[: len(package) - level + 1] + [fixed.lstrip(".")])
                        fixed = fixed.rstrip(".")
                        runs_ |= {p for m, p in self.modules.items() if m == fixed or m.startswith(fixed + ".")}
                    else:
                        self.everything.add(path)
                if last in GLOBS | LISTINGS and isinstance(node.func, ast.Attribute) or function in OS_LISTINGS:
                    # A listing of a folder this file names (`(ROOT / "docs").glob("*.md")`) reads the files it
                    # matches, or only their names when the file reads no file contents anywhere. A listing of a
                    # folder found from a __file__ alone (Path(module.__file__).parent.glob("*.py"), ROOT.iterdir()) reads
                    # every tracked file its pattern matches; one of a folder found at run time (tmp_path) reads none.
                    os_call = function in OS_LISTINGS
                    receiver = node.args[:1] if os_call else [node.func.value]
                    patterns = [t for a in node.args[(1 if os_call else 0) :] for t in self.strings(a)]
                    if os_call and function.startswith("glob."):
                        patterns, receiver = [t for t in self.strings(node.args[0])] if node.args else [], []
                    if last not in GLOBS or not patterns:
                        patterns = ["*"]
                    recursive = last in ("rglob", "walk") or any("**" in t or "/" in t for t in patterns)
                    folders = {f for r in receiver for f in self.paths_of(r)}
                    names_only = not reads_content
                    if folders:
                        for folder in folders:
                            folder = folder.strip("/")
                            name = folder.rsplit("/", 1)[-1]
                            for found in self.dirs.get(name, ()):
                                if found == folder or found.endswith("/" + folder):
                                    self.listings[path].update(
                                        (found, t.rstrip("/").rsplit("/", 1)[-1] or "*", recursive, names_only)
                                        for t in patterns
                                    )
                    elif any(
                        isinstance(n, ast.Name)
                        and (n.id == "__file__" or n.id in located)
                        or isinstance(n, ast.Attribute)
                        and n.attr == "__file__"
                        for r in receiver
                        for n in ast.walk(r)
                    ) or (os_call and function.startswith("glob.")):
                        self.globs[path].update(t.rstrip("/").rsplit("/", 1)[-1] or "*" for t in patterns)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                names.add(node.value)
                if "ls-files" in node.value and (
                    is_test(path) or "tests" in Path(path).parts or path.startswith("scripts/")
                ):
                    self.everything.add(path)  # lists this repository (elsewhere git ls-files reads a bot's checkout)
                # Free text (a message naming scripts/tico) only reads what it names; a lone dotted name like
                # "backend.app:app" or a monkeypatch target loads that module.
                prose = len(node.value.split()) > 1
                for token in TOKEN.findall(node.value.replace(":", " ")):
                    self.path(token, reads_ if prose else runs_, reads_, module=not prose)
        self.runs[path] |= runs
        self.reads[path] |= reads
        self.names[path] = names

    def text(self, path):
        try:
            raw = (self.root / path).read_bytes()
        except OSError:
            return
        if len(raw) > 512 * 1024 or b"\0" in raw[:4096]:
            return
        folder = str(Path(path).parent)
        for token in TOKEN.findall(raw.decode("utf-8", "replace")):
            self.path(token, self.runs[path], self.reads[path])
            if token.startswith(("./", "../")) and folder != ".":
                self.path(os.path.normpath(folder + "/" + token), self.runs[path], self.reads[path])
            if IDENTIFIER.match(token) and "." in token:
                self.runs[path] |= self.module(token, bare=True)

    def readers(self, name, added):
        """The files that read a changed file: by its path, through a folder that holds it, a glob on an unknown
        folder, or a listing (one that uses only names sees it only when it is new)."""
        users = set(self.read_by.get(name, set()))
        for parent in list(Path(name).parents)[:-1]:
            users |= self.read_by.get(str(parent) + "/", set())
        users |= {
            p for p, patterns in self.globs.items() if any(fnmatch.fnmatchcase(Path(name).name, g) for g in patterns)
        }
        folder = str(Path(name).parent)
        users |= {
            p
            for p, listings in self.listings.items()
            if any(
                (folder == d or recursive and folder.startswith(d + "/"))
                and fnmatch.fnmatchcase(Path(name).name, g)
                and (not names_only or name in added)
                for d, g, recursive, names_only in listings
            )
        }
        return users

    def fixture_users(self, name, read):
        """Tests that use a non-autouse conftest fixture whose body runs `name` (or reads it, when `read`)."""
        users = set()
        for conftest, fixtures in self.fixtures.items():
            used = {
                f
                for f, (runs, reads) in fixtures.items()
                if name in runs or read and (name in reads or any(name.startswith(d) for d in reads if d.endswith("/")))
            }
            if used:
                folder = str(Path(conftest).parent)
                users |= {
                    p
                    for p, names in self.names.items()
                    if is_test(p) and used & names and (folder == "." or p.startswith(folder + "/"))
                }
        return users

    def below(self, conftest):
        folder = str(Path(conftest).parent)
        return {p for p in self.files if is_test(p) and (folder == "." or p.startswith(folder + "/"))}

    def affected(self, changed, added=None):
        """Changed files, and every tracked file that runs one of them, directly or through files that run each
        other, or that reads one; files that depend on everything and whatever runs them. A listing that uses only
        names sees added files (None: any changed file may be new); a removed file runs the full suite."""
        changed = set(changed)
        added = changed if added is None else set(added)
        seen = changed | self.everything
        pending = list(seen)
        while pending:
            name = pending.pop()
            users = set(self.run_by.get(name, ()))
            if name in changed:
                users |= self.readers(name, added)
            users |= self.fixture_users(name, name in changed)
            if Path(name).name == "conftest.py":
                users |= self.below(name)
            for user in users - seen:
                seen.add(user)
                pending.append(user)
        return seen

    def read_by_tests(self, changed, added=None):
        """For data that only code reads (read_only below): the files that read a changed one, and of the checks, the
        tests and browser scripts among those readers and the ones that run a reader directly (a conftest reader: the
        tests below it). Not everything that imports a reader: backend/manual.py reads docs/ and backend.app imports
        it, which would make every page edit run the suite. A test that sees such a file only through the app must
        name its path or folder literally. Files that depend on everything (they list the repository) read it too."""
        changed = set(changed)
        added = changed if added is None else set(added)
        readers, out = set(self.everything), set(changed)
        for name in changed:
            readers |= self.readers(name, added)
            out |= self.fixture_users(name, True)
        out |= readers
        for reader in readers:
            if Path(reader).name == "conftest.py":
                out |= self.below(reader)
            elif not is_check(reader):
                out |= {user for user in self.run_by.get(reader, ()) if is_check(user)}
                out |= self.fixture_users(reader, False)
        return out


# Changes that steer every test rather than particular ones: dependency pins and pytest's own configuration (conftest
# hooks and autouse fixtures, pytest.ini).
FULL_SUITE = ("pytest.ini", "package.json", "package-lock.json", ".gitignore")
# How the checks run: the workflow, its installer and the browser system packages. A change runs the tests that read or
# run them (the selector's own tests among them) and one short core browser script, so both kinds of check VM boot,
# install and run under the changed setup; a workflow change also builds the Caddy image, whose job only it defines.
# The selector, scripts/pr_checks.py, is code: its tests run like any other module's.
PR_WORKFLOW = ".github/workflows/pr-checks.yml"
CI_INPUTS = (PR_WORKFLOW, "scripts/ci_install.sh", "scripts/playwright-system-packages.json")
SMOKE_SCRIPT = "sign-in-redirect"
# The Caddy container job builds the server image (Dockerfile, docker/, the screen relay it compiles) and runs its
# entrypoint's caddy-config, backend/caddy_assets.py. The image also compiles and imports the Python source; a plan
# that skips the job runs those checks itself (image_checks).
CADDY_INPUTS = ("Dockerfile", ".dockerignore", "compose.yaml", "compose.screen-relay.yaml", "docker/", "relay-rs/")
CADDY_ROOTS = ("backend/caddy_assets.py", "backend/tests/test_caddy_container.py")
UI_RUNNER = "scripts/ui-tests.cjs"
# Slow tests (marked `slow`: absent from the default run, and run by the release gate) that a merge queue group runs
# on one serial VM. A slow test is a candidate only when it directly imports or reads a changed file (slow_candidates:
# depth one of the map, never through a conftest or a fixture). Collection keeps the candidates that hold a slow test,
# the exempt list drops the ones a group cannot run, and the budget (slow_fit: each file's measured slow seconds, or its
# slow share of its measured time) keeps the nearest that fit; the rest are deferred to release validation. SLOW_EXEMPT
# (scripts/pr_slow_exempt.json) names each exempt file with its reason: Docker, a binary or the network the queue
# lacks, a skip that would hide a gap, or a job that already runs the file for the same change. check_slow_exempt
# keeps that list honest on every push.
SLOW_EXEMPT = "scripts/pr_slow_exempt.json"


def slow_exempt(root=ROOT):
    """Slow test files a merge queue group does not run, each with the reason it cannot."""
    exempt = json.loads((root / SLOW_EXEMPT).read_text())
    if not isinstance(exempt, dict) or not all(
        isinstance(name, str) and isinstance(reason, str) and reason.strip() for name, reason in exempt.items()
    ):
        raise RuntimeError(f"{SLOW_EXEMPT} maps each test file to a non-empty reason")
    return exempt


def slow_marked(tree):
    """Whether a parsed test module marks a test slow: a pytestmark (module or class), or a slow mark on a function, a
    class or a parametrized case. A static reading for the exempt list; collection decides what the queue runs."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(target, "id", None) == "pytestmark" for target in node.targets):
            if "mark.slow" in ast.unparse(node.value):
                return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if any("mark.slow" in ast.unparse(decorator) for decorator in node.decorator_list):
                return True
    return False


def check_slow_exempt(root=ROOT):
    """Problems with the exempt list: an entry that is not a test file in this checkout, or one with no slow test left
    (its exemption is stale and comes out with the change that removed the mark)."""
    problems = []
    for name in slow_exempt(root):
        path = root / name
        if not is_test(name) or not path.is_file():
            problems.append(f"{name}: not a test file in this checkout")
        elif not slow_marked(ast.parse(path.read_bytes())):
            problems.append(f"{name}: no test in it is marked slow, so its exemption is stale")
    return problems


def collect_counts(files, marker, root=ROOT):
    """Test counts per file as pytest collects them under `-m marker`, over these files (None: the whole suite)."""
    if files is not None and not files:
        return Counter()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-n", "0", "-m", marker, *(files or [])],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 5):  # 5: none of these files holds a test under the marker
        raise RuntimeError(f"Collection with -m {marker!r} failed:\n" + result.stdout[-4000:] + result.stderr[-4000:])
    return Counter(
        line.split("::", 1)[0] for line in result.stdout.splitlines() if "::" in line and not line.startswith(" ")
    )


def collect_slow(files=None, root=ROOT):
    """Slow test counts per file as pytest collects them: `-m slow` over these files (None: the whole suite)."""
    return collect_counts(files, "slow", root)


def collect_tests(files, root=ROOT):
    """All test counts per file, slow and default together, as pytest collects them: the denominator of a file's slow
    share (slow_seconds)."""
    return collect_counts(files, "slow or not slow", root)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def slow_seconds(counts, totals, timings, measured=None):
    """Estimated seconds of each file's slow tests on one serial VM. A file the timings job summed the slow tests of
    (`measured`: the junit testcases the conftest hook marks slow, per file) takes those seconds. Otherwise the file's
    measured time (`timings`: the whole file, default and slow tests together) times its slow share, the slow tests over
    all its tests (`counts` slow, `totals` all, both collected). The share assumes the file's tests take equal time: a
    heavy slow test in a file of light ones is underestimated, and a light slow test in a heavy file is overestimated.
    A file with no total counts as all slow. A file with no timing takes the seconds per slow test that weights()
    observes in the timed files."""
    measured = measured or {}
    adjusted = {}
    for name in counts:
        if name in measured and finite(measured[name]):
            adjusted[name] = float(measured[name])
        elif name in timings and finite(timings[name]):
            adjusted[name] = timings[name] * counts[name] / max(totals.get(name, counts[name]), counts[name], 1)
    return weights(counts, adjusted)


def slow_fit(nearest, counts, timings, totals, measured=None):
    """The nearest slow files that fit one serial VM within the job's budget, and the rest, deferred to release
    validation: (kept, deferred, seconds). A file's seconds are its measured slow seconds where the timings have them,
    else its slow share of its measured time (slow_seconds)."""
    seconds = slow_seconds({name: counts[name] for name in nearest}, totals, timings, measured)
    budget = JOB_SECONDS - SETUP_SECONDS["slow"]
    kept, deferred, total = [], [], 0.0
    for name in nearest:
        if total + seconds[name] <= budget:
            kept.append(name)
            total += seconds[name]
        else:
            deferred.append(name)
    return kept, deferred, total


def unread(name):
    """No test or script reads screenshots, retired designs or the top-level pages other than CHANGELOG.md."""
    return name.startswith(("docs/images/", "docs/history/")) or (
        "/" not in name and name.endswith(".md") and name != "CHANGELOG.md"
    )


def read_only(name):
    """Data that code reads but no test imports: docs pages (the manual, the OpenAPI copy, the API guide), CHANGELOG.md
    (the changelog API, release-notes guards) and GitHub files (release tests parse the workflows). A change runs the
    tests that read it (DependencyMap.read_by_tests), not everything that imports a reader."""
    return name == "CHANGELOG.md" or name.startswith(("docs/", ".github/"))


def full_suite(name):
    return (
        name in FULL_SUITE
        or Path(name).name == "conftest.py"
        or re.fullmatch(r"requirements[\w.-]*\.txt", Path(name).name) is not None
    )


def slow_candidates(graph, changed, added=None):
    """The test files a merge queue group may run slow tests of, nearest the change first.

    Only a test that directly runs or reads a changed file is a candidate (depth one of the map, not the closure), and
    a change never reaches a test through a conftest, a fixture or a file that depends on everything: those reach every
    test below them. Nearness, in tiers: a changed test file itself; a test named for the module it runs (test_x.py runs
    x.py); another test that runs the changed file; a test that reads it. Within a tier, a test that reaches more of the
    change comes first. `added` names the new files (None: any may be new), as in DependencyMap.readers.
    """
    tracked = [name for name in changed if name in graph.tracked]
    added = set(tracked) if added is None else set(added)
    tier, reach = {}, Counter()
    for name in tracked:
        if is_test(name):
            tier[name] = 0
            reach[name] += 1
    for name in tracked:
        stem = Path(name).stem
        runners = graph.run_by.get(name, set())
        for user in runners | graph.readers(name, added):
            if not is_test(user) or user not in graph.tracked or user.startswith("ui/"):
                continue
            if Path(user).name in (f"test_{stem}.py", f"{stem}_test.py"):
                level = 1
            else:
                level = 2 if user in runners else 3
            tier[user] = min(tier.get(user, 4), level)
            reach[user] += 1
    return sorted(tier, key=lambda test: (tier[test], -reach[test], test))


def selection(changed, event="pull_request", root=ROOT, graph=None, base=None, added=None, core=None):
    """None means the full default suite; an empty list means explicitly unaffected.

    A pull request or a merge queue group (base to head) runs the default tests that use a changed file
    (DependencyMap) and, of the browser scripts that do, the core ones the full suite runs; a UI change runs its
    mapped scripts or all of them. A docs page, CHANGELOG.md or a GitHub file runs the tests that read it
    (read_only); a CI input adds a short browser smoke (CI_INPUTS). `slow` is the slow candidates that directly use a
    change, nearest first (slow_candidates); prepare confirms and budgets them. A fallback runs the changed test files
    as slow candidates and no others. `added` names the new files (None: any may be new) and `core` the core browser
    scripts (None: read from scripts/ui-tests.cjs)."""
    full = {
        "python": None,
        "browser": None,
        "caddy": True,
        "contracts": True,
        "slow": [],  # a fallback steers every test; only the changed test files are slow candidates (see the return)
        "reason": "full default suite",
    }
    rust = sorted(crate for crate, inputs in RUST_INPUTS.items() if any(p.startswith(inputs) for p in changed))
    runner = bool(rust) or any(
        p.startswith(("runner/", "runner_support/", "runner-rs/", "relay-rs/"))
        or p in ("backend/harness_actions.py", "backend/runner_versions.py")
        for p in changed
    )
    full["rust"] = rust
    if event not in ("pull_request", "merge_group"):
        return {**full, "slow": [], "conformance": runner, "reason": "main runs the full default suite"}
    if not changed:
        return {**full, "conformance": runner, "reason": "empty diff: full default suite", "uncertain": ["empty diff"]}
    changed = [n for n in changed if not unread(n)]
    uncertain = [n for n in changed if full_suite(n) or not (root / n).is_file()]
    browser = set()
    for name in changed:
        if name in UI_MAP:
            browser.update(UI_MAP[name])
        elif name.startswith("ui/") or name == UI_RUNNER:
            browser.add("--all")
    if any(n != "--all" and not (root / "ui/tests" / f"{n}.cjs").is_file() for n in browser):
        uncertain.append("missing mapped browser test")
        browser = {"--all"}
    if not uncertain and changed:
        graph = graph or DependencyMap(root)
        uncertain += [n for n in changed if n in graph.unparsed or n not in graph.files]
    if uncertain:
        return {
            **full,
            "browser": ["--all"] if browser else None,
            "conformance": runner,
            "reason": "unmapped, shared, deleted or uncertain change: full default suite",
            "uncertain": uncertain,
            "slow": [n for n in changed if is_test(n) and (root / n).is_file()],
        }
    if not changed:
        return {
            "python": [],
            "browser": [],
            "conformance": False,
            "rust": [],
            "caddy": False,
            "contracts": False,
            "slow": [],
            "reason": "documentation that no test or script reads",
        }
    data = [n for n in changed if read_only(n) and not graph.code(n)]
    affected = graph.affected([n for n in changed if n not in data], added) | (
        graph.read_by_tests(data, added) if data else set()
    )
    python = sorted(p for p in affected if is_test(p) and not p.startswith("ui/"))
    if "--all" not in browser:
        scripts = {Path(p).stem for p in affected if Path(p).parent == Path("ui/tests") and p.endswith(".cjs")}
        if core is None:
            try:
                core = browser_scripts([], root)
            except (OSError, subprocess.CalledProcessError, RuntimeError):
                core = scripts
        browser |= scripts & set(core)
        if any(n in CI_INPUTS for n in changed) and not browser:
            browser.add(SMOKE_SCRIPT)
    caddy = (
        any(n.startswith(CADDY_INPUTS) for n in changed) or bool(affected & set(CADDY_ROOTS)) or PR_WORKFLOW in changed
    )
    contracts = any(n.startswith(CONTRACT_INPUTS) for n in changed) or bool(affected & set(CONTRACT_TESTS))
    return {
        "python": python,
        "browser": sorted(browser),
        "conformance": runner,
        "rust": rust,
        "caddy": caddy,
        "contracts": contracts,
        # The slow tests that directly use a change, nearest first. prepare() keeps the ones with a slow test.
        "slow": slow_candidates(graph, changed, added),
        "reason": "measured dependency map",
    }


def changed_files(base, head="HEAD", root=ROOT, only=None):
    try:
        ancestor = subprocess.check_output(["git", "merge-base", base, head], cwd=root, text=True).strip()
        raw = subprocess.check_output(
            [
                "git",
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                *([f"--diff-filter={only}"] if only else []),
                ancestor,
                head,
            ],
            cwd=root,
        )
        return raw.decode().rstrip("\0").split("\0") if raw else []
    except (subprocess.CalledProcessError, OSError):
        return []  # The uncertain path runs everything, never nothing.


def collect(root=ROOT):
    # Reuse the plan's existing collection as the sharding coverage guard: no extra collection or CI job.
    # Direct script execution puts scripts/ on sys.path; package imports (including tests) use the relative form.
    if __package__:
        from .release_checks import validate_shard_coverage
    else:
        from release_checks import validate_shard_coverage
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-n", "0"], cwd=root, capture_output=True, text=True
    )
    if result.returncode:
        raise RuntimeError("Default collection failed:\n" + result.stdout[-4000:] + result.stderr[-4000:])
    counts = Counter(
        line.split("::", 1)[0] for line in result.stdout.splitlines() if "::" in line and not line.startswith(" ")
    )
    if not counts:
        raise RuntimeError("Default suite collected no tests")
    validate_shard_coverage(counts, selection=(), root=root)
    return counts


def weights(counts, timings, fallback=1.0):
    # Use measured per-file wall time, including setup/teardown. Unknown files use
    # the observed seconds/test, so adding files cannot put all unknown work in one VM.
    known = {
        name: float(timings[name])
        for name in counts
        if name in timings
        and isinstance(timings[name], (int, float))
        and math.isfinite(timings[name])
        and 0 < timings[name] < 86400
    }
    seconds_per_test = sum(known.values()) / sum(counts[n] for n in known) if known else fallback
    return {n: known.get(n, counts[n] * seconds_per_test) for n in counts}


def job_count(seconds, kind, workers=WORKERS):
    """VMs needed for `seconds` of one-worker test time to finish near JOB_SECONDS each, at `workers` per VM."""
    per_job = workers * max(JOB_SECONDS - SETUP_SECONDS[kind], 30)
    return max(1, min(MAX_JOBS, math.ceil(seconds / per_job)))


def groups(counts, timings, total=None, kind="python", fallback=1.0, workers=WORKERS):
    seconds = weights(counts, timings, fallback)
    if total is None:
        total = job_count(sum(seconds.values()), kind, workers)
    out, loads = [[] for _ in range(total)], [0.0] * total
    for name in sorted(counts, key=lambda n: (-seconds[n], n)):
        i = min(range(total), key=lambda i: (loads[i], i))
        out[i].append(name)
        loads[i] += seconds[name]
    return [sorted(g) for g in out], loads


def merge_parent(base, head="HEAD", root=None):
    """The base a pull request is checked against. GitHub checks out the PR merged into the current base branch, while
    the event's base sha is the branch tip when the PR was last updated; diffing from that older sha would count
    everything main gained since as the PR's own change, CHANGELOG.md included."""

    def git(*args):
        return subprocess.run(["git", *args], cwd=root or ROOT, capture_output=True, text=True)

    parents = git("rev-list", "--parents", "-n", "1", head).stdout.split()[1:]
    if base and len(parents) == 2 and git("merge-base", "--is-ancestor", base, parents[0]).returncode == 0:
        return parents[0]
    return base


def image_checks(changed, root=ROOT):
    """The image build's own Python checks, for a selective plan that skips the Caddy job: compile each changed file
    and run the Dockerfile's import line (backend.app and friends) here."""
    for name in changed:
        if name.endswith(".py") and (root / name).is_file():
            compile((root / name).read_bytes(), name, "exec")
    found = re.search(r"python -c '(import [^']+)'", (root / "Dockerfile").read_text())
    if not found:
        raise RuntimeError("Dockerfile import check not found; keep it in step with image_checks")
    subprocess.run([sys.executable, "-c", found.group(1)], cwd=root, check=True)


def read_timings(timings_path):
    """(timings, slow): the timings job's durations.json (per file, default and slow tests together) and its
    slow-durations.json (per file, the slow tests alone). A directory is searched for both. A file path reads the
    slow-durations.json beside it. An artifact from before the slow seconds were stored has none: slow is empty."""
    timings, slow = {}, {}
    if not timings_path:
        return timings, slow
    source = Path(timings_path)
    if source.is_dir():
        totals = sorted(source.rglob("durations.json"))
        slow_paths = sorted(source.rglob("slow-durations.json"))
    else:
        totals = [source] if source.is_file() else []
        slow_paths = [source.with_name("slow-durations.json")]
    for path in totals:
        timings.update(json.loads(path.read_text()))
    for path in slow_paths:
        if path.is_file():
            slow.update(json.loads(path.read_text()))
    return timings, slow


def prepare(event, base, output, timings_path=None, head="HEAD"):
    """`head` is the commit under test: the checkout for a pull request, the queue group's merge commit for a
    merge_group. A group's base is its own base sha, so it needs no merge_parent or changelog guard."""
    if event == "pull_request":
        base = merge_parent(base)
        guard_changelog(base)
    changed = changed_files(base, head) if base else []
    added = changed_files(base, head, only="A") if base else None
    picked = selection(changed, event, base=base, added=added)
    if picked["python"] is not None and not picked["caddy"]:
        image_checks(changed)
    counts = collect() if picked["python"] != [] else Counter()
    if picked["python"] is not None:
        # A selected file with no default test (a changed test whose tests are all slow, which the release gate runs;
        # a test helper the map follows) has nothing to run here.
        counts = Counter({f: counts[f] for f in picked["python"] if f in counts})
        picked["python"] = sorted(counts)
    timings, slow_measured = read_timings(timings_path)
    split, loads = groups(counts, timings)
    if loads and max(loads) / WORKERS + SETUP_SECONDS["python"] > 1.5 * JOB_SECONDS:
        # Visible in the plan job's annotations: the cap (TICO_CI_MAX_JOBS) or one huge file holds a VM past the target.
        print(
            f"::warning::A Python VM is planned for {max(loads) / WORKERS + SETUP_SECONDS['python']:.0f}s against the "
            f"{JOB_SECONDS:.0f}s target ({len(split)} VMs, cap {MAX_JOBS}): raise TICO_CI_MAX_JOBS or split the file"
        )
    jobs = [{"suite": f"python-{i + 1}", "files": files} for i, files in enumerate(split) if files]
    if picked["browser"] != []:
        scripts = browser_scripts(picked["browser"] or [])
        browser_times = {name: timings.get(f"ui/tests/{name}.cjs") for name in scripts}
        browser_split, browser_loads = groups(
            Counter(dict.fromkeys(scripts, 1)), browser_times, kind="browser", fallback=UNTIMED_SCRIPT_SECONDS
        )
        jobs += [{"suite": f"browser-{i + 1}", "files": names} for i, names in enumerate(browser_split) if names]
        loads += browser_loads
    if picked["conformance"]:
        jobs.append({"suite": "conformance", "files": [CONFORMANCE], "crates": picked["rust"]})
    if picked["contracts"]:
        jobs.append({"suite": "contracts", "files": list(CONTRACT_TESTS)})
    slow_loads, slow_reached, slow_deferred = [], [], []
    if picked.get("slow"):
        # Nearest first (slow_candidates). Collection keeps the files that hold a slow test, the exempt list drops the
        # ones a group cannot run, and one serial VM takes the nearest that fit; the rest wait for release validation.
        reached = collect_slow(picked["slow"])
        exempt = slow_exempt()
        slow_reached = sorted(name for name in reached if name in exempt)
        nearest = [name for name in picked["slow"] if name in reached and name not in exempt]
        kept, slow_deferred, seconds = slow_fit(nearest, reached, timings, collect_tests(nearest), slow_measured)
        picked["slow"] = sorted(kept)
        if kept:
            jobs.append({"suite": "slow-1", "files": sorted(kept)})
            slow_loads = [seconds]
        slow_deferred = sorted(slow_deferred)
    # Actions rejects an empty matrix. Keep an explicit successful selection job
    # for documented no-test changes, without invoking bare pytest.
    if not jobs:
        jobs.append({"suite": "selection", "files": []})
    plan = {
        **picked,
        "changed": changed,
        "slow_exempt": slow_reached,
        "slow_deferred": slow_deferred,
        "matrix": {"include": jobs},
        "estimated_seconds": loads + slow_loads,
        "timed_files": len(set(counts) & timings.keys()),
    }
    if slow_deferred and os.environ.get("GITHUB_STEP_SUMMARY"):
        # The job summary names what the queue does not run, so a skipped slow test is never a surprise.
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(
                f"### Slow tests deferred to release validation ({len(slow_deferred)})\n\n"
                f"One slow VM runs {len(picked['slow'])} file(s), about {sum(slow_loads):.0f} s of slow tests "
                f"(each file's measured time times its slow share), "
                f"nearest first within {JOB_SECONDS - SETUP_SECONDS['slow']:.0f} s. Not run here:\n\n"
            )
            stream.writelines(f"- `{name}`\n" for name in slow_deferred)
            stream.write("\n")
    Path(output).write_text(json.dumps(plan, indent=2) + "\n")
    print(json.dumps(plan, indent=2))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write("matrix=" + json.dumps(plan["matrix"]) + "\n")
            stream.write("caddy=" + str(plan["caddy"]).lower() + "\n")
    return plan


def browser_scripts(args, root=ROOT):
    """The browser scripts these ui-tests.cjs arguments run (CORE for none), by name."""
    out = subprocess.run(
        ["node", "scripts/ui-tests.cjs", "--list", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split()
    if not out:
        raise RuntimeError(f"Browser selection {args} listed no scripts")
    return out


def run(plan_path, suite):
    plan = json.loads(Path(plan_path).read_text())
    job = next(j for j in plan["matrix"]["include"] if j["suite"] == suite)
    files = job["files"]
    if suite == "selection":
        if (
            plan["python"] != []
            or plan["browser"] != []
            or plan["caddy"]
            or plan["conformance"]
            or plan["contracts"]
            or plan.get("slow")
        ):
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
        serial = suite.startswith("slow-")
        # A node id names its file before the "::"; a file may carry one.
        if not files or any(not (ROOT / f.split("::", 1)[0]).is_file() for f in files):
            raise RuntimeError("Python check requires an explicit nonempty existing file list")
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            # One process for slow tests: the wall-clock budget tests among them must not share a CPU with a sibling.
            "0" if serial else str(WORKERS),
            *PYTEST_FLAGS,
            "-o",
            "junit_family=xunit1",
            f"--junitxml=ci-results/{suite}.xml",
        ]
        if suite in ("conformance", "contracts") or serial:
            command += ["-m", "slow"]
        command += files
    (ROOT / "ci-results").mkdir(exist_ok=True)
    rust = cargo_tests(job.get("crates", [])) if suite == "conformance" else 0
    result = subprocess.run(command, cwd=ROOT, env=env)
    if result.returncode == 0 and not suite.startswith("browser-"):
        cases = list(ET.parse(ROOT / "ci-results" / f"{suite}.xml").iter("testcase"))
        if not any(t.find("skipped") is None for t in cases):
            raise RuntimeError("A Python job must pass at least one test, not skip every test")
        skipped = [t.get("name", "") for t in cases if t.find("skipped") is not None]
        if suite.startswith("slow-") and skipped:
            print(f"::warning::{len(skipped)} slow test(s) skipped in {suite}: {', '.join(skipped[:10])}")
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
        if subprocess.run(
            ["cargo", "test", "--locked"], cwd=ROOT / crate, env={**os.environ, "CARGO_TARGET_DIR": str(target)}
        ).returncode:
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


def slow_durations(paths):
    """{test file: seconds} of the testcases the conftest hook marked slow (a `slow` property in the junit report),
    summed per file. The slow VM is charged for these alone, not for the file's whole time (slow_seconds)."""
    measured = Counter()
    for path in paths:
        for test in ET.parse(path).iter("testcase"):
            if test.find("properties/property[@name='slow']") is None:
                continue
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
        seconds = (
            (
                datetime.fromisoformat(end.replace("Z", "+00:00"))
                - datetime.fromisoformat(start.replace("Z", "+00:00"))
            ).total_seconds()
            if start and end
            else None
        )
        name = str(job["name"]).replace("|", r"\|").replace("\n", " ")
        rows.append(
            f"| [{name}]({job['html_url']}) | {job.get('conclusion') or job['status']} | "
            + (f"{seconds:.1f}" if seconds is not None else "running")
            + " |"
        )
    return "\n".join(rows) + "\n"


RECEIPT_INCOMPLETE = "The GitHub API returned an error for part of this receipt, so it may be incomplete."
JOB_FIELDS = ("name", "html_url", "status")


def receipt_jobs(text):
    """The jobs on the pages of `gh api --paginate --slurp` output that are jobs pages, and whether any page was not.

    An error object (GitHub's body for a 502), a page without a list of well-formed jobs, a top level that is not a
    list, and empty or invalid JSON are pages that were not read. The receipt is cosmetic: it never fails the gate.
    """
    try:
        data = json.loads(text)
    except ValueError:
        return [], True
    pages = data if isinstance(data, list) else [data]
    jobs, incomplete = [], False
    for page in pages:
        page_jobs = page.get("jobs") if isinstance(page, dict) else None
        if isinstance(page_jobs, list) and all(
            isinstance(j, dict) and all(f in j for f in JOB_FIELDS) for j in page_jobs
        ):
            jobs += page_jobs
        else:
            incomplete = True
    return jobs, incomplete


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--event", required=True)
    plan.add_argument("--base", default="")
    plan.add_argument("--head", default="HEAD")
    plan.add_argument("--output", default="ci-plan.json")
    plan.add_argument("--timings")
    execute = sub.add_parser("run")
    execute.add_argument("suite")
    execute.add_argument("--plan", default="ci-plan.json")
    slow = sub.add_parser("slow", help="print the slow test files that directly use a change, nearest first")
    slow.add_argument("--base", default="origin/main")
    slow.add_argument("--head", default="HEAD")
    sub.add_parser("slow-check", help="check the exempt list: real test files, each with a reason and a slow test")
    sub.add_parser("durations")
    sub.add_parser("receipt")
    args = parser.parse_args()
    if args.action == "plan":
        prepare(args.event, args.base, args.output, args.timings, args.head)
    elif args.action == "run":
        return run(args.plan, args.suite)
    elif args.action == "slow":
        picked = selection(
            changed_files(args.base, args.head),
            base=args.base,
            added=changed_files(args.base, args.head, only="A"),
        )
        print(json.dumps({"reason": picked["reason"], "candidates": picked["slow"], "exempt": slow_exempt()}, indent=2))
    elif args.action == "slow-check":
        problems = check_slow_exempt()
        for problem in problems:
            print(f"::error::{SLOW_EXEMPT}: {problem}")
        return 1 if problems else 0
    elif args.action == "durations":
        reports = sorted(Path("ci-results").glob("python-*.xml"))
        measured = durations(reports, Path("ci-results").glob("browser-*.json"))
        if not measured:
            raise RuntimeError("No Python timings were collected")
        Path("durations.json").write_text(json.dumps(measured, indent=2))
        # Written even when empty (an artifact from a run with no slow tests): the plan reads it beside durations.json.
        Path("slow-durations.json").write_text(json.dumps(slow_durations(reports), indent=2))
    else:
        # gh --paginate --slurp emits one jobs response per page.
        jobs, incomplete = receipt_jobs(sys.stdin.buffer.read())
        print(receipt(jobs))
        if incomplete:
            print(RECEIPT_INCOMPLETE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
