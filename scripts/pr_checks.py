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


def ui_map_key(name):
    """The UI_MAP key for a ui/ file. A script converted to TypeScript (ui/app/x.ts) keeps the entry its .js name has."""
    return name[:-3] + ".js" if name.startswith("ui/") and name.endswith(".ts") else name


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
SETUP_SECONDS = {"python": 45, "browser": 70}  # measured boot, checkout and warm install
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
        found = [p.relative_to(root) for p in root.rglob("*") if p.is_file()]
        return [str(p) for p in found if not skip & set(p.parts)]


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
    ls-files` or a computed import makes a file depend on everything. A conftest runs for the tests below it, but a file
    that reaches one only through it does not reach those tests (selection adds the smoke set instead): a changed conftest
    selects them itself (rule 2). A file that only a non-autouse conftest fixture body runs reaches its users through the conftest, so their
    tests are not selected: the smoke set runs instead (rule 2).
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
        self.imports = {}  # Python file -> the dotted names it imports (for a gone file's importers, named_by)
        self._tokens = None  # built on first use by tokens()
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
        imported = set()
        for node in nodes:
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                prefix = package[: len(package) - node.level + 1] if node.level else []
                module = ".".join([*prefix, *([node.module] if node.module else [])])
                imported.add(module)
                imported |= {".".join(filter(None, [module, alias.name])) for alias in node.names}
        self.imports[path] = imported
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

    def tokens(self):
        """Each text file's path-like tokens, indexed by token (built on first use). A relative token also indexes the
        path it names from its folder, so `../x.py` in scripts/a.py indexes x.py."""
        if self._tokens is None:
            self._tokens = defaultdict(set)
            for path in self.files:
                if Path(path).suffix not in CODE | {".html"}:
                    continue
                try:
                    raw = (self.root / path).read_bytes()
                except OSError:
                    continue
                if len(raw) > 512 * 1024 or b"\0" in raw[:4096]:
                    continue
                folder = str(Path(path).parent)
                for token in set(TOKEN.findall(raw.decode("utf-8", "replace"))):
                    self._tokens[token].add(path)
                    if token.startswith(("./", "../")) and folder != ".":
                        self._tokens[os.path.normpath(folder + "/" + token)].add(path)
        return self._tokens

    def named_by(self, name):
        """The other files that name a file in text (its path, its basename, or a longer suffix of it) or import its
        module. A gone file has no reads to follow, so its users are found this way (rule 1)."""
        parts = name.split("/")
        keys = {name, parts[-1], *("/".join(parts[i:]) for i in range(1, len(parts)))}
        found = set()
        for key in keys:
            found |= self.tokens().get(key, set())
        module = name[:-3].replace("/", ".") if name.endswith(".py") else ""
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        if module:
            found |= {
                p
                for p, imported in self.imports.items()
                if any(i == module or i.startswith(module + ".") for i in imported)
            }
        found.discard(name)
        return found

    def readers(self, name, added):
        """The files that read a changed file: by its path, through a folder that holds it, a glob on an unknown
        folder, or a listing (one that uses only names sees it only when it is new)."""
        users = set(self.read_by.get(name, set()))
        for parent in list(Path(name).parents)[:-1]:
            users |= self.read_by.get(str(parent) + "/", set())
        users |= {
            p for p, patterns in self.globs.items() if any(fnmatch.fnmatchcase(Path(name).name, g) for g in patterns)
        }
        users |= self.listed_by(name, added)
        return users

    def listed_by(self, name, added):
        """The files whose listing of a folder holds a changed file: `ui/*.js` lists ui/app/x.js too only when recursive
        (`rglob`, `**`), and a listing that uses only names sees it only when it is new."""
        folder = str(Path(name).parent)
        return {
            p
            for p, listings in self.listings.items()
            if any(
                (folder == d or recursive and folder.startswith(d + "/"))
                and fnmatch.fnmatchcase(Path(name).name, g)
                and (not names_only or name in added)
                for d, g, recursive, names_only in listings
            )
        }

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

    def affected(self, changed, added=None, via=None):
        """Changed files, and every tracked file that runs one of them, directly or through files that run each
        other, or that reads one; files that depend on everything and whatever runs them. A listing that uses only
        names sees added files (None: any changed file may be new). A test that reaches a file only through a conftest
        fixture is not followed (rule 2 runs the smoke set for it); each such file is added to `via`, when given."""
        changed = set(changed)
        added = changed if added is None else set(added)
        seen = changed | self.everything
        pending = list(seen)
        while pending:
            name = pending.pop()
            users = set(self.run_by.get(name, ()))
            if name in changed:
                users |= self.readers(name, added)
            if via is not None and self.fixture_users(name, name in changed):
                via.add(name)
            for user in users - seen:
                seen.add(user)
                pending.append(user)
        return seen

    def read_by_tests(self, changed, added=None, via=None):
        """For data that only code reads (read_only below): the files that read a changed one, and of the checks, the
        tests and browser scripts among those readers and the ones that run a reader directly. Not everything that
        imports a reader: backend/manual.py reads docs/ and backend.app imports it, which would make every page edit
        run the suite. A test that sees such a file only through the app must name its path or folder literally. Files
        that depend on everything (they list the repository) read it too. A reader reached only through a conftest
        fixture is added to `via`, when given (rule 2), and its tests are not selected."""
        changed = set(changed)
        added = changed if added is None else set(added)
        readers, out = set(self.everything), set(changed)
        for name in changed:
            readers |= self.readers(name, added)
            if via is not None and self.fixture_users(name, True):
                via.add(name)
        out |= readers
        for reader in readers:
            if not is_check(reader):
                out |= {user for user in self.run_by.get(reader, ()) if is_check(user)}
                if via is not None and self.fixture_users(reader, False):
                    via.add(reader)
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
# The smoke set (scripts/queue-smoke.json, checked by smoke_check): a few Python test files and the core browser scripts,
# each kind measured to fit one VM. An uncertain pull request or queue group runs it, so nothing widens to the full suite.
SMOKE_FILE = "scripts/queue-smoke.json"
# A changed conftest runs the tests below it while there are at most this many; more, and the smoke set runs instead.
CONFTEST_BELOW_LIMIT = 25
# The Caddy container job builds the server image (Dockerfile, docker/, the screen relay it compiles) and runs its
# entrypoint's caddy-config, backend/caddy_assets.py. The image also compiles and imports the Python source; a plan
# that skips the job runs those checks itself (image_checks).
CADDY_INPUTS = ("Dockerfile", ".dockerignore", "compose.yaml", "compose.screen-relay.yaml", "docker/", "relay-rs/")
CADDY_ROOTS = ("backend/caddy_assets.py", "backend/tests/test_caddy_container.py")
UI_RUNNER = "scripts/ui-tests.cjs"
# The factory simulation (evals/factory) runs the Dev board end to end, and the merge queue runs it when a change reaches
# what it runs. That reach is measured, not mapped: evals/factory/call_path.json lists the Python files a run of the sim
# executed, the non-Python files it read and the folders it listed (python -m evals.factory.run --seeds 1-3
# --save-call-path). The static map cannot say it: the sim imports backend.app, which imports every router, and a read of
# ui/ by backend/config.py would reach the sim from a stylesheet. The sim also loads every catalog template into the
# company file, so a new template under templates/catalog/ runs it; the pinned backend dependencies and this workflow
# (which defines the sim's job) are named too.
FACTORY_DIR = "evals/factory/"
FACTORY_CALL_PATH = FACTORY_DIR + "call_path.json"
FACTORY_TEMPLATES = ("templates/catalog/",)
FACTORY_PINS = re.compile(r"backend/requirements[\w.-]*\.(txt|in)")


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


def pinned(name):
    """A dependency pin or one of FULL_SUITE: a change runs the tests that read it, and the smoke set (rule 2)."""
    return name in FULL_SUITE or re.fullmatch(r"requirements[\w.-]*\.txt", Path(name).name) is not None


def is_browser(name):
    return Path(name).parent == Path("ui/tests") and name.endswith(".cjs")


def smoke(root=ROOT):
    """The smoke set's Python test files and browser scripts (SMOKE_FILE): what an uncertain change runs."""
    data = json.loads((root / SMOKE_FILE).read_text())
    return sorted(e["file"] for e in data["python"]), sorted(e["script"] for e in data["browser"])


def name_status(text):
    """(status, path, old path or None, similarity) for each record of `git diff --name-status -M -z`."""
    fields = text.split("\0")
    out, i = [], 0
    while i < len(fields) and fields[i]:
        code = fields[i]
        if code[:1] in ("R", "C") and i + 2 < len(fields):
            out.append((code[:1], fields[i + 2], fields[i + 1], int(code[1:] or 0)))
            i += 3
        elif i + 1 < len(fields):
            out.append((code[:1], fields[i + 1], None, 100))
            i += 2
        else:
            break
    return out


def pure_ui_rename(old, new, similarity):
    """A ui/ file moved to the same name with another extension (.js to .ts) and no other change. Only its new path is
    selected: nothing loads the old name any more, so it is not a gone file."""
    return (
        similarity == 100
        and old.startswith("ui/")
        and new.startswith("ui/")
        and Path(old).with_suffix("") == Path(new).with_suffix("")
        and Path(old).suffix != Path(new).suffix
    )


SCRIPT_MOUNTS = (("/tico/ui/", ""), ("/vendor/", "vendor/"))
SCRIPT_SRC = re.compile(r"""<script\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def script_srcs(path, diff):
    """The files an index.html diff's added lines load with <script src>, as repository paths beside index.html."""
    folder = str(Path(path).parent)
    out = set()
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for src in SCRIPT_SRC.findall(line):
            src = src.split("?", 1)[0].split("#", 1)[0]
            if src and not src.startswith("//") and not re.match(r"[a-z][a-z0-9+.-]*:", src, re.IGNORECASE):
                # The page names its scripts by URL: /tico/ui/x is ui/x and /vendor/x is ui/vendor/x
                # (backend/ui_bundle.py); a relative name sits beside the page.
                for mount, there in SCRIPT_MOUNTS:
                    if src.startswith(mount):
                        src = there + src[len(mount) :]
                        break
                out.add(os.path.normpath(os.path.join(folder, src.lstrip("/"))))
    return sorted(out)


def script_changes(base, head, changed, root=ROOT):
    """Rule 5: an index.html that changes <script src> names changes the files those names load, so each counts as
    changed (a renamed script runs as itself, not only by its old name). Git failing adds nothing."""
    found = set()
    for path in changed:
        if Path(path).name != "index.html" or not (root / path).is_file():
            continue
        try:
            ancestor = subprocess.check_output(["git", "merge-base", base, head], cwd=root, text=True).strip()
            diff = subprocess.check_output(
                ["git", "diff", "-U0", "--no-renames", ancestor, head, "--", path], cwd=root, text=True
            )
        except (subprocess.CalledProcessError, OSError):
            continue
        found |= set(script_srcs(path, diff))
    return sorted(found)


def selection(changed, event="pull_request", root=ROOT, graph=None, base=None, added=None, core=None):
    """The checks a change runs. A pull request or merge queue group never runs the full default suite or every browser
    script (release validation runs both). Main and scheduled runs keep the full suite (python None). A rule that departs
    from the measured dependency map says so in `uncertain`, and `reason` names the rules that applied:

    1. A file gone from the tree (a deletion, or the old side of a rename) runs the tests and browser scripts that name,
       import or read it directly. Data that code reads (docs, CHANGELOG.md, GitHub files) runs the tests that read it.
       A gone file that code other than a check names or reads adds the smoke set.
    2. A pin (FULL_SUITE, requirements*.txt) runs the tests and browser scripts that read it directly, and the smoke set.
       A conftest that changed or was deleted runs the tests below it while there are at most CONFTEST_BELOW_LIMIT (and the
       tests that use its fixtures), else the smoke set. A conftest that a change only reaches, through code it runs or
       reads, adds the smoke set; the tests below it are not reached through it.
    3. A Python file the map cannot parse, or does not know, runs the checks that name its path, else the smoke set.
    4. A ui/ file, or the browser runner, outside UI_MAP runs the browser scripts the map links to it, the tests that read
       it directly or run a module that lists its folder (a test that globs ui/*.js is reached through the module it
       runs), and the smoke set's browser scripts. A changed browser script runs itself. A UI_MAP file runs its scripts;
       a missing one runs the smoke set's browser scripts instead.
    5. An index.html that changes <script src> names adds the files it now names (prepare, through script_changes).

    `added` names the new files (None: any changed file may be new) and `core` the core browser scripts (None: read from
    scripts/ui-tests.cjs).
    """
    rust = sorted(crate for crate, inputs in RUST_INPUTS.items() if any(p.startswith(inputs) for p in changed))
    runner = bool(rust) or any(
        p.startswith(("runner/", "runner_support/", "runner-rs/", "relay-rs/"))
        or p in ("backend/harness_actions.py", "backend/runner_versions.py")
        for p in changed
    )
    if event not in ("pull_request", "merge_group"):
        return {
            "python": None,
            "browser": None,
            "caddy": True,
            "contracts": True,
            "rust": rust,
            "conformance": runner,
            "reason": "main runs the full default suite",
            "uncertain": [],
        }
    notes, uncertain = [], []

    def plan(python=(), browser=(), caddy=False, contracts=False, reason=None):
        return {
            "python": sorted(p for p in python if is_test(p) and not p.startswith("ui/")),
            "browser": sorted(browser),
            "conformance": runner,
            "rust": rust,
            "caddy": caddy,
            "contracts": contracts,
            "reason": reason or "; ".join(dict.fromkeys(["measured dependency map", *notes])),
            "uncertain": uncertain,
        }

    if not changed:
        uncertain.append("empty diff: nothing to map")
        return plan(*smoke(root), reason="empty diff: smoke set")
    names = [n for n in changed if not unread(n)]
    if not names:
        return plan(reason="documentation that no test or script reads")
    graph = graph or DependencyMap(root)
    added = set(names) if added is None else set(added)
    live = [n for n in names if (root / n).is_file()]
    gone = [n for n in names if n not in live]
    python, browser, seeds, touched = set(), set(), set(live), set()
    smoke_python = smoke_browser = False
    via = set()  # files that reach tests only through a conftest fixture (rule 2)
    for name in gone:  # rule 1
        uncertain.append(f"rule 1: {name} is gone")
        if read_only(name) and not graph.code(name):
            found = graph.read_by_tests([name], added, via)
            touched |= found
        else:
            found = graph.named_by(name) | graph.readers(name, added)
            if any(not is_check(p) for p in found):
                smoke_python = smoke_browser = True
                uncertain.append(f"rule 1: {name} is named or read by code that is not a check: smoke set")
        python |= {p for p in found if is_test(p)}
        browser |= {Path(p).stem for p in found if is_browser(p)}
    if gone:
        notes.append("rule 1: gone files")
    data = [n for n in live if read_only(n) and not graph.code(n)]
    seeds -= set(data)
    conftests = [n for n in names if Path(n).name == "conftest.py"]
    seeds -= set(conftests)
    for name in conftests:  # rule 2: a changed or deleted conftest
        below = graph.below(name)
        fixtures = set(graph.fixtures.get(name, {}))
        python |= {p for p in below if fixtures & graph.names.get(p, set())}
        if len(below) <= CONFTEST_BELOW_LIMIT:
            python |= below
            uncertain.append(f"rule 2: conftest {name} runs the {len(below)} tests below it")
        else:
            smoke_python = smoke_browser = True
            uncertain.append(f"rule 2: conftest {name} has {len(below)} tests below it: smoke set")
    pins = [n for n in names if pinned(n)]
    seeds -= set(pins)
    for name in pins:  # rule 2: a pin
        direct = graph.readers(name, added)
        python |= {p for p in direct if is_test(p)}
        browser |= {Path(p).stem for p in direct if is_browser(p)}
        smoke_python = smoke_browser = True
        uncertain.append(f"rule 2: pin {name}: the tests and browser scripts that read it, and the smoke set")
    if conftests or pins:
        notes.append("rule 2: conftest or pin")
    unknown = [n for n in live if n in graph.unparsed or n not in graph.files]
    seeds -= set(unknown)
    for name in unknown:  # rule 3
        direct = {t for t in graph.named_by(name) if is_check(t)}
        python |= {t for t in direct if is_test(t)}
        browser |= {Path(t).stem for t in direct if is_browser(t)}
        if direct:
            uncertain.append(f"rule 3: unknown {name} names {len(direct)} check(s)")
        else:
            smoke_python = smoke_browser = True
            uncertain.append(f"rule 3: unknown {name}: smoke set")
    if unknown:
        notes.append("rule 3: unknown files")
    unmapped = [n for n in live if ui_map_key(n) not in UI_MAP and (n.startswith("ui/") or n == UI_RUNNER)]
    seeds -= set(unmapped)
    for name in live:  # rule 4: a UI_MAP file runs its mapped scripts
        if ui_map_key(name) in UI_MAP:
            scripts = UI_MAP[ui_map_key(name)]
            missing = [s for s in scripts if not (root / "ui/tests" / f"{s}.cjs").is_file()]
            browser |= set(scripts) - set(missing)
            if missing:
                smoke_browser = True
                uncertain.append(f"rule 4: {name} maps to missing {', '.join(missing)}: smoke browser scripts")
    for name in unmapped:  # rule 4: a UI file the map does not list
        linked = set(graph.run_by.get(name, ())) | graph.readers(name, added)
        browser |= {Path(p).stem for p in linked if is_browser(p)}
        python |= {p for p in linked if is_test(p)}
        # A module that lists the folder (scripts/build-icon-font.py globs ui/*.js) reaches the tests that run it.
        python |= {t for lister in graph.listed_by(name, added) for t in graph.run_by.get(lister, ()) if is_test(t)}
        if is_browser(name):  # a changed browser script runs itself: it is the check that changed
            browser.add(Path(name).stem)
        smoke_browser = True
        uncertain.append(f"rule 4: {name} is not in UI_MAP: the scripts that name it, its readers and the smoke set")
        notes.append("rule 4: ui outside UI_MAP")
    touched |= graph.affected(sorted(seeds), added, via)
    if data:
        touched |= graph.read_by_tests(data, added, via)
    if via:
        smoke_python = True
        notes.append("rule 2: conftest fixture")
        uncertain.append(
            f"rule 2: {', '.join(sorted(via)[:3])}{' and more' if len(via) > 3 else ''} reach tests only through a "
            "conftest fixture: the direct readers and the smoke set"
        )
    reached = sorted(c for c in touched if Path(c).name == "conftest.py" and c not in names)
    for conftest in reached:  # rule 2: a conftest reached through code a change runs or reads, not changed itself
        smoke_python = smoke_browser = True
        uncertain.append(f"rule 2: {conftest} is reached, not changed: direct readers and smoke set")
    if reached:
        notes.append("rule 2: reached conftest")
    python |= {p for p in touched if is_test(p)}
    scripts = {Path(p).stem for p in touched if is_browser(p)}
    if core is None:
        try:
            core = browser_scripts([], root)
        except (OSError, subprocess.CalledProcessError, RuntimeError):
            core = scripts
    browser |= scripts & set(core)
    if smoke_python or smoke_browser:
        smoke_tests, smoke_scripts = smoke(root)
        if smoke_python:
            python |= set(smoke_tests)
        if smoke_browser:
            browser |= set(smoke_scripts)
    if any(n in CI_INPUTS for n in names) and not browser:
        browser.add(SMOKE_SCRIPT)
    caddy = any(n.startswith(CADDY_INPUTS) for n in names) or bool(touched & set(CADDY_ROOTS)) or PR_WORKFLOW in names
    contracts = any(n.startswith(CONTRACT_INPUTS) for n in names) or bool((touched | python) & set(CONTRACT_TESTS))
    return plan(python, browser, caddy=caddy, contracts=contracts)


def changed_files(base, head="HEAD", root=ROOT, only=None):
    """The paths a pull request or queue group changes, from base's merge point to head. Renames are found (-M): a moved
    file lists its new path and its old one, which is gone (rule 1), except a pure ui/ rename (pure_ui_rename), which
    lists only its new path. `only="A"` lists the files it adds: the new paths of added and renamed files. Git failing
    gives an empty list, which selection runs as the smoke set."""
    try:
        ancestor = subprocess.check_output(["git", "merge-base", base, head], cwd=root, text=True).strip()
        raw = subprocess.check_output(["git", "diff", "--name-status", "-M", "-z", ancestor, head], cwd=root)
    except (subprocess.CalledProcessError, OSError):
        return []
    out = set()
    for status, path, old, similarity in name_status(raw.decode()):
        if only == "A":
            if status in ("A", "R"):
                out.add(path)
            continue
        out.add(path)
        if old is not None and not pure_ui_rename(old, path, similarity):
            out.add(old)
    return sorted(out)


def smoke_check(root=ROOT):
    """Problems with SMOKE_FILE, for the static job: an unreadable file; an entry whose test file or core browser script
    is missing; an entry with no measured time or no one-line reason; a repeat; a kind whose total needs more than one VM;
    or a UI_MAP script that is missing. An empty list means the set is sound."""
    try:
        data = json.loads((root / SMOKE_FILE).read_text())
    except (OSError, ValueError) as error:
        return [f"{SMOKE_FILE}: {error}"]
    if not isinstance(data, dict):
        return [f"{SMOKE_FILE}: not a JSON object"]
    problems = []
    try:
        core = set(browser_scripts([], root))
    except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
        core = None
        problems.append(f"cannot list the core browser scripts: {error}")
    for kind, key in (("python", "file"), ("browser", "script")):
        entries = data.get(kind)
        if not isinstance(entries, list) or not entries:
            problems.append(f"{kind}: no entries")
            continue
        names, total = [], 0.0
        for entry in entries:
            name = entry.get(key) if isinstance(entry, dict) else None
            if not isinstance(name, str) or not name:
                problems.append(f"{kind}: an entry has no {key}")
                continue
            names.append(name)
            seconds, reason = entry.get("seconds"), entry.get("reason")
            if (
                isinstance(seconds, bool)
                or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds)
                or seconds <= 0
            ):
                problems.append(f"{kind} {name}: no measured seconds")
            else:
                total += seconds
            if not isinstance(reason, str) or not reason.strip() or "\n" in reason:
                problems.append(f"{kind} {name}: needs a one-line reason")
            if kind == "python" and (not is_test(name) or not (root / name).is_file()):
                problems.append(f"python {name}: no such test file")
            if kind == "browser" and (
                not (root / "ui/tests" / f"{name}.cjs").is_file() or (core is not None and name not in core)
            ):
                problems.append(f"browser {name}: no such core script")
        if len(set(names)) != len(names):
            problems.append(f"{kind}: an entry is repeated")
        if job_count(total, kind) != 1:
            problems.append(f"{kind} smoke set takes {total:.0f}s: more than one VM")
    for name, scripts in UI_MAP.items():
        for script in scripts:
            if not (root / "ui/tests" / f"{script}.cjs").is_file():
                problems.append(f"UI_MAP {name} names a missing script: {script}")
    return problems


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


def factory_call_path(root=ROOT):
    """What the sim's recorded run ran, read and listed (evals/factory/call_path.json), or None when nothing is recorded."""
    path = root / FACTORY_CALL_PATH
    if not path.is_file():
        return None
    recorded = json.loads(path.read_text())
    return {key: list(recorded.get(key, ())) for key in ("code", "data", "folders")}


def factory_reason(changed, call_path=None, root=ROOT):
    """Why a change runs the factory simulation, or None. An empty diff runs it too: the diff could not be read, and a
    change that is not known to miss the sim does not go unchecked."""
    if not changed:
        return "the diff is empty or could not be read"
    measured = factory_call_path(root) if call_path is None else call_path
    if measured is None:
        return f"{FACTORY_CALL_PATH} is missing"
    reached = set(measured["code"]) | set(measured["data"])
    folders = set(measured["folders"])
    for name in changed:
        if name.startswith(FACTORY_DIR):
            return f"{name} is the sim's own code"
        if name in reached:
            return f"the sim runs or reads {name}"
        # A listing sees the entries directly in a folder, so only a change to one of them moves it.
        parent = name.rsplit("/", 1)[0] + "/" if "/" in name else ""
        if parent in folders:
            return f"the sim lists {parent}, which {name} is in"
        if name.startswith(FACTORY_TEMPLATES):
            return f"{name} is a catalog template the sim loads"
        if name == PR_WORKFLOW:
            return f"{name} defines the sim's job"
        if FACTORY_PINS.fullmatch(name):
            return f"{name} pins what the sim installs"
    return None


def factory_shards():
    """The `i/n` groups of the sim's seed-1 runs that `run.py --shard` takes, one per RunsOn VM: the sim's own packing
    of its scenarios by timings.json. Imports only run.py, which sets up no scenario, server or environment."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from evals.factory import run as factory

    timings = factory.timings()
    count = factory.groups_for([(name, 1) for name in timings], timings)
    return [f"{index}/{count}" for index in range(1, count + 1)]


def factory_plan(event, changed):
    """The factory simulation's part of a plan: whether it runs, why, and its VM groups. A merge queue group and a pull
    request both run it on RunsOn, one VM per group, so both pack it; the workflow decides which pull requests get it."""
    reason = factory_reason(changed)
    shards = factory_shards() if reason and event in ("merge_group", "pull_request") else ["1/1"]
    return {"run": reason is not None, "reason": reason or "no change reaches the sim", "shards": shards}


def prepare(event, base, output, timings_path=None, head="HEAD"):
    """`head` is the commit under test: the checkout for a pull request, the queue group's merge commit for a
    merge_group. A group's base is its own base sha, so it needs no merge_parent or changelog guard."""
    if event == "pull_request":
        base = merge_parent(base)
        guard_changelog(base)
    changed = changed_files(base, head) if base else []
    added = changed_files(base, head, only="A") if base else None
    if base:
        changed = sorted(set(changed) | set(script_changes(base, head, changed)))
    picked = selection(changed, event, base=base, added=added)
    if picked["python"] is not None and not picked["caddy"]:
        image_checks(changed)
    counts = collect() if picked["python"] != [] else Counter()
    if picked["python"] is not None:
        # A selected file with no default test (a changed test whose tests are all slow, which the release gate runs;
        # a test helper the map follows) has nothing to run here.
        counts = Counter({f: counts[f] for f in picked["python"] if f in counts})
        picked["python"] = sorted(counts)
    timings = {}
    if timings_path:
        source = Path(timings_path)
        paths = sorted(source.rglob("durations.json")) if source.is_dir() else ([source] if source.is_file() else [])
        for path in paths:
            timings.update(json.loads(path.read_text()))
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
    # Actions rejects an empty matrix. Keep an explicit successful selection job
    # for documented no-test changes, without invoking bare pytest.
    if not jobs:
        jobs.append({"suite": "selection", "files": []})
    plan = {
        **picked,
        "changed": changed,
        "factory": factory_plan(event, changed),
        "matrix": {"include": jobs},
        "estimated_seconds": loads,
        "timed_files": len(set(counts) & timings.keys()),
    }
    Path(output).write_text(json.dumps(plan, indent=2) + "\n")
    print(json.dumps(plan, indent=2))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write("matrix=" + json.dumps(plan["matrix"]) + "\n")
            stream.write("caddy=" + str(plan["caddy"]).lower() + "\n")
            stream.write("factory=" + str(plan["factory"]["run"]).lower() + "\n")
            stream.write("factory_shards=" + json.dumps(plan["factory"]["shards"]) + "\n")
    return plan


def browser_scripts(args, root=ROOT):
    """The browser scripts these ui-tests.cjs arguments run (CORE for none), by name."""
    out = subprocess.run(
        ["node", "scripts/ui-tests.cjs", "--list", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split()
    if not out:
        raise RuntimeError(f"Browser selection {args} listed no scripts")
    return out


def run(plan_path, suite, root=None):
    """Runs one suite of a plan from `root`'s checkout: the merge queue's VMs, and the release gate's incremental ones.
    `root` defaults to ROOT as it is when the suite runs (a default argument would freeze the import-time value)."""
    root = ROOT if root is None else Path(root)
    plan = json.loads(Path(plan_path).read_text())
    # A merge queue plan lists its VMs as matrix.include (suite); an incremental release plan lists them as jobs (name).
    entries = plan["matrix"]["include"] if "matrix" in plan else plan["jobs"]
    job = next(j for j in entries if j.get("suite", j.get("name")) == suite)
    files = job["files"]
    if suite == "selection":
        if plan["python"] != [] or plan["browser"] != [] or plan["caddy"] or plan["conformance"] or plan["contracts"]:
            raise RuntimeError("Selection-only job requires an explicitly unaffected plan")
        print("Documentation-only change: no affected tests")
        return 0
    env = None
    if suite.startswith("browser-"):
        if not files:
            raise RuntimeError("A browser check requires an explicit nonempty script list")
        command = ["node", "scripts/ui-tests.cjs", "-j", str(WORKERS), *files]
        env = {**os.environ, "TICO_UI_TIMINGS": str(root / "ci-results" / f"{suite}.json")}
    else:
        if not files or any(not (root / f).is_file() for f in files):
            raise RuntimeError("Python check requires an explicit nonempty existing file list")
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            str(WORKERS),
            *PYTEST_FLAGS,
            "-o",
            "junit_family=xunit1",
            f"--junitxml=ci-results/{suite}.xml",
        ]
        if suite in ("conformance", "contracts"):
            command += ["-m", "slow"]
        command += files
    (root / "ci-results").mkdir(exist_ok=True)
    rust = cargo_tests(job.get("crates", [])) if suite == "conformance" else 0
    result = subprocess.run(command, cwd=root, env=env)
    if result.returncode == 0 and not suite.startswith("browser-"):
        cases = list(ET.parse(root / "ci-results" / f"{suite}.xml").iter("testcase"))
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
    execute.add_argument("--root", default=str(ROOT), help="the checkout the suite runs in")
    sub.add_parser("durations")
    sub.add_parser("receipt")
    sub.add_parser("smoke-check")
    args = parser.parse_args()
    if args.action == "plan" and not os.environ.get("GITHUB_ACTIONS") and not os.environ.get("TICO_PLAN_LOCAL"):
        # plan collects every test with pytest: minutes of load, and on a Mac without the CI packages it fails.
        # To see what a change selects, call selection() with the changed files; it reads files and runs nothing.
        print(
            "pr_checks.py plan runs in CI only (it collects every test). For a local answer use selection():\n"
            "  python3 -c 'import sys; sys.path.insert(0, \"scripts\"); import pr_checks; "
            "print(pr_checks.selection([\"path/changed.py\"]))'\n"
            "Set TICO_PLAN_LOCAL=1 to run it here anyway.",
            file=sys.stderr,
        )
        return 2
    if args.action == "plan":
        prepare(args.event, args.base, args.output, args.timings, args.head)
    elif args.action == "run":
        return run(args.plan, args.suite, Path(args.root))
    elif args.action == "durations":
        measured = durations(Path("ci-results").glob("python-*.xml"), Path("ci-results").glob("browser-*.json"))
        if not measured:
            raise RuntimeError("No Python timings were collected")
        Path("durations.json").write_text(json.dumps(measured, indent=2))
    elif args.action == "smoke-check":
        problems = smoke_check()
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    else:
        # gh --paginate --slurp emits one jobs response per page.
        jobs, incomplete = receipt_jobs(sys.stdin.buffer.read())
        print(receipt(jobs))
        if incomplete:
            print(RECEIPT_INCOMPLETE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
