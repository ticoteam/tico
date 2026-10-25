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
    """A file pytest collects by name (pytest's default python_files); prepare() keeps the ones in the default run."""
    base = Path(name).name
    return base.endswith(".py") and (base.startswith("test_") or base.endswith("_test.py"))


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
PATHLIKE = {"Path", "PurePath", "PosixPath", "open", "join", "joinpath", "with_name", "spec_from_file_location",
            "run_path", "copy", "copy2", "copyfile", "copytree", "exists", "isfile", "isdir", "listdir", "scandir",
            "walk", "read_text", "read_bytes", "relpath", "realpath", "abspath", "files"}
RUNNERS = {"run", "check_output", "check_call", "call", "Popen", "execv", "execvp", "execvpe", "spawn",
           "create_subprocess_exec", "create_subprocess_shell", "system"}
GLOBS = {"glob", "rglob", "iglob"}
LISTINGS = {"iterdir", "listdir", "scandir", "walk"}
OS_LISTINGS = {"os.listdir", "os.walk", "os.scandir", "glob.glob", "glob.iglob"}
# Calls that read, copy, run, hash or measure files; a listing in a function without one uses only names.
CONTENT = {"read_text", "read_bytes", "open", "load", "loads", "safe_load", "parse", "copy", "copy2", "copyfile",
           "copytree", "copyfileobj", "exec_module", "run", "check_output", "check_call", "Popen", "call", "stat",
           "lstat", "getsize", "getmtime", "digest", "hexdigest", "update", "compile", "import_module", "rmtree",
           "unlink", "rename", "replace", "move", "write_text", "write_bytes", "hash_file", "file_digest"}


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
        self.globs = defaultdict(set)      # file -> basename patterns it lists in folders it does not name
        self.listings = defaultdict(set)   # file -> (folder, pattern, recursive, names only) it lists
        self.everything, self.unparsed = set(), set()
        self.fixtures = defaultdict(dict)  # conftest -> {fixture: (runs, reads)}
        self.names = {}                    # Python file -> its identifiers and strings (fixture names it uses)
        trees = {}
        for path in self.files:
            if path.endswith(".py"):
                try:
                    trees[path] = ast.parse((root / path).read_bytes())
                except (OSError, SyntaxError, ValueError):
                    self.unparsed.add(path)
                    self.everything.add(path)
        # Top-level names imported through sys.path (`import hubcli` with clients/ on the path).
        self.bare = {a.name.split(".")[0] for tree in trees.values() for node in ast.walk(tree)
                     if isinstance(node, ast.Import) for a in node.names}
        self.bare |= {node.module.split(".")[0] for tree in trees.values() for node in ast.walk(tree)
                      if isinstance(node, ast.ImportFrom) and not node.level and node.module}
        self.bare = {name for name in self.bare - set(self.modules) if any(m.endswith("." + name) for m in self.modules)}
        for path, tree in trees.items():
            self.python(path, tree)
        for path in self.files:
            if Path(path).suffix in CODE - {".py"} or (not Path(path).suffix and self.shebang(path)):
                self.text(path)
        self.run_by, self.read_by = defaultdict(set), defaultdict(set)
        for edges, reverse in ((self.runs, self.run_by), (self.reads, self.read_by)):
            for path, targets in edges.items():
                for target in targets - {path}:
                    reverse[target].add(path)

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
                return {self.modules[".".join(parts[:i])] for i in range(1, end + 1) if ".".join(parts[:i]) in self.modules}
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
        if isinstance(node, ast.Call) and dotted_name(node.func).rsplit(".", 1)[-1] in ("Path", "PurePath", "PosixPath"):
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

    def python(self, path, tree):
        runs, reads, names = set(), set(), set()
        package = path.split("/")[:-1]
        self.consts = {}
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name) \
                    and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                self.consts[statement.targets[0].id] = statement.value.value
        bodies = [tree.body] + [n.body for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        docstrings = {id(b[0].value) for b in bodies if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant)}
        nested = {id(n.left) for n in ast.walk(tree) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)}
        scope = {}
        # Module-level names that hold a path found from __file__: ROOT = Path(__file__).parents[2]; DOCS = ROOT / "docs".
        # A name whose path joins a literal ("docs") names that folder, which its definition reads.
        located, named_here = set(), set()
        for statement in tree.body:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)) and statement.value is not None and any(
                    isinstance(n, ast.Name) and (n.id == "__file__" or n.id in located) for n in ast.walk(statement.value)):
                literal = any(isinstance(n, ast.Name) and n.id in named_here for n in ast.walk(statement.value)) or any(
                    isinstance(part, ast.Constant) and isinstance(part.value, str) for n in ast.walk(statement.value)
                    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div) for part in self.chain(n))
                for target in statement.targets if isinstance(statement, ast.Assign) else [statement.target]:
                    found = {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
                    located |= found
                    named_here |= found if literal else set()
        parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        # Names bound to a literal path (FIXTURES = Path(__file__).parent / "fixtures"): where such a name is joined
        # further, the joined path is what is read; where it is listed, only the listing.
        self.values, deferred = defaultdict(set), set()
        for _ in range(2):
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    found = self.paths_of(node.value)
                    if found:
                        self.values[node.targets[0].id] |= found
                        deferred.add(id(node.value))
        # A listing uses only names when nothing in the file reads file contents: a read elsewhere in the module (at
        # module level, in another function or a helper it calls) may read what was listed, so edits reach it too.
        reads_content = any(isinstance(n, ast.Call) and dotted_name(n.func).rsplit(".", 1)[-1] in CONTENT
                            for n in ast.walk(tree))
        listed = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in GLOBS | LISTINGS:
                listed.add(id(node.func.value))
            elif isinstance(node, ast.Call) and dotted_name(node.func) in OS_LISTINGS and node.args:
                listed.add(id(node.args[0]))
        if Path(path).name == "conftest.py":
            for node in ast.walk(tree):
                for decorator in getattr(node, "decorator_list", []) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else []:
                    call = decorator if isinstance(decorator, ast.Call) else None
                    keywords = {k.arg: k.value for k in call.keywords} if call else {}
                    autouse = keywords.get("autouse")
                    if dotted_name(call.func if call else decorator).split(".")[-1] == "fixture" and (
                            autouse is None or isinstance(autouse, ast.Constant) and autouse.value is False):
                        name = keywords.get("name")
                        name = name.value if isinstance(name, ast.Constant) else node.name
                        self.fixtures[path][name] = (set(), set())
                        scope.update({id(n): self.fixtures[path][name] for n in ast.walk(node)})
        for node in ast.walk(tree):
            runs_, reads_ = scope.get(id(node), (runs, reads))
            if isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    runs_ |= self.module(alias.name, bare=True)
            elif isinstance(node, ast.ImportFrom):
                prefix = package[:len(package) - node.level + 1] if node.level else []
                module = ".".join([*prefix, *([node.module] if node.module else [])])
                runs_ |= self.module(module, bare=not node.level)
                for alias in node.names:
                    runs_ |= self.module(module + "." + alias.name, bare=not node.level)
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and id(node) not in nested:
                parts = self.chain(node)
                if id(node) not in deferred | listed:
                    for found in self.paths_of(node):
                        self.path(found, runs_, reads_, bare_word=True)
                start = next((i for i, part in enumerate(parts[1:], 1) if not isinstance(part, ast.Constant)), len(parts))
                self.joined(parts if not self.paths_of(node) else parts[start:], runs_, reads_)
            elif isinstance(node, ast.Name) and node.id in self.values and isinstance(node.ctx, ast.Load) and id(node) not in listed:
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
                            runs_ |= self.module(".".join(package[:len(package) - level + 1] + [first.value.lstrip(".")]))
                        else:
                            runs_ |= self.module(first.value, bare=True)
                    elif isinstance(first, (ast.JoinedStr, ast.BinOp)) and isinstance(
                            head := first.values[0] if isinstance(first, ast.JoinedStr) else first.left, ast.Constant):
                        # f"backend.connectors.{name}", "." + name: every module under the fixed package
                        fixed = str(head.value)
                        if fixed.startswith("."):
                            level = len(fixed) - len(fixed.lstrip("."))
                            fixed = ".".join(package[:len(package) - level + 1] + [fixed.lstrip(".")])
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
                    receiver = (node.args[:1] if os_call else [node.func.value])
                    patterns = [t for a in node.args[(1 if os_call else 0):] for t in self.strings(a)]
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
                                    self.listings[path].update((found, t.rstrip("/").rsplit("/", 1)[-1] or "*", recursive, names_only)
                                                               for t in patterns)
                    elif any(isinstance(n, ast.Name) and (n.id == "__file__" or n.id in located) or
                             isinstance(n, ast.Attribute) and n.attr == "__file__" for r in receiver for n in ast.walk(r)) or (
                            os_call and function.startswith("glob.")):
                        self.globs[path].update(t.rstrip("/").rsplit("/", 1)[-1] or "*" for t in patterns)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                names.add(node.value)
                if "ls-files" in node.value and (is_test(path) or "tests" in Path(path).parts or path.startswith("scripts/")):
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
                users |= self.read_by.get(name, set())
                for parent in list(Path(name).parents)[:-1]:
                    users |= self.read_by.get(str(parent) + "/", set())
                users |= {p for p, patterns in self.globs.items()
                          if any(fnmatch.fnmatchcase(Path(name).name, g) for g in patterns)}
                folder = str(Path(name).parent)
                users |= {p for p, listings in self.listings.items() if any(
                    (folder == d or recursive and folder.startswith(d + "/")) and fnmatch.fnmatchcase(Path(name).name, g)
                    and (not names_only or name in added) for d, g, recursive, names_only in listings)}
            for conftest, fixtures in self.fixtures.items():
                used = {f for f, (runs, reads) in fixtures.items() if name in runs or name in changed and (
                    name in reads or any(name.startswith(d) for d in reads if d.endswith("/")))}
                if used:
                    folder = str(Path(conftest).parent)
                    users |= {p for p, names in self.names.items() if is_test(p) and used & names
                              and (folder == "." or p.startswith(folder + "/"))}
            if Path(name).name == "conftest.py":
                folder = str(Path(name).parent)
                users |= {p for p in self.files if is_test(p) and (folder == "." or p.startswith(folder + "/"))}
            for user in users - seen:
                seen.add(user)
                pending.append(user)
        return seen


# Changes that steer every test rather than particular ones: CI, dependency pins, pytest's own configuration
# (conftest hooks and autouse fixtures, pytest.ini) and this selector and its installer. Tests and the Assistant read
# docs pages (the committed OpenAPI spec, the API guide, the manual) and CHANGELOG.md's shipped sections, so those
# run everything too; no test or script reads screenshots, retired designs or the other top-level pages.
FULL_SUITE = ("CHANGELOG.md", "pytest.ini", "package.json", "package-lock.json", ".gitignore",
              "scripts/pr_checks.py", "scripts/ci_install.sh", "scripts/playwright-system-packages.json")
# The Caddy container job builds the server image (Dockerfile, docker/, the screen relay it compiles) and runs its
# entrypoint's caddy-config, backend/caddy_assets.py. The image also compiles and imports the Python source; a plan
# that skips the job runs those checks itself (image_checks).
CADDY_INPUTS = ("Dockerfile", ".dockerignore", "compose.yaml", "compose.screen-relay.yaml", "docker/", "relay-rs/")
CADDY_ROOTS = ("backend/caddy_assets.py", "backend/tests/test_caddy_container.py")
UI_RUNNER = "scripts/ui-tests.cjs"


def unread(name):
    return name.startswith(("docs/images/", "docs/history/")) or (
        "/" not in name and name.endswith(".md") and name != "CHANGELOG.md")


def full_suite(name):
    return (name in FULL_SUITE or name.startswith((".github/", "docs/")) or Path(name).name == "conftest.py"
            or re.fullmatch(r"requirements[\w.-]*\.txt", Path(name).name) is not None)


def selection(changed, event="pull_request", root=ROOT, graph=None, base=None, added=None, core=None):
    """None means the full default suite; an empty list means explicitly unaffected.

    A pull request runs the default tests that use a changed file (DependencyMap) and, of the browser scripts that
    do, the core ones the full suite runs; a UI change runs its mapped scripts or all of them. `added` names the new
    files (None: any may be new) and `core` the core browser scripts (None: read from scripts/ui-tests.cjs)."""
    full = {"python": None, "browser": None, "caddy": True, "reason": "full default suite"}
    rust = sorted(crate for crate, inputs in RUST_INPUTS.items() if any(p.startswith(inputs) for p in changed))
    runner = bool(rust) or any(p.startswith(("runner/", "runner-rs/", "relay-rs/")) or
                               p in ("backend/harness_actions.py", "backend/runner_versions.py") for p in changed)
    full["rust"] = rust
    if event != "pull_request":
        return {**full, "conformance": runner, "reason": "main runs the full default suite"}
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
        return {**full, "browser": ["--all"] if browser else None, "conformance": runner,
                "reason": "unmapped, shared, deleted or uncertain change: full default suite", "uncertain": uncertain}
    if not changed:
        return {"python": [], "browser": [], "conformance": False, "rust": [], "caddy": False,
                "reason": "documentation that no test or script reads"}
    affected = graph.affected(changed, added)
    python = sorted(p for p in affected if is_test(p) and not p.startswith("ui/"))
    if "--all" not in browser:
        scripts = {Path(p).stem for p in affected if Path(p).parent == Path("ui/tests") and p.endswith(".cjs")}
        if core is None:
            try:
                core = browser_scripts([], root)
            except (OSError, subprocess.CalledProcessError, RuntimeError):
                core = scripts
        browser |= scripts & set(core)
    caddy = any(n.startswith(CADDY_INPUTS) for n in changed) or bool(affected & set(CADDY_ROOTS))
    return {"python": python, "browser": sorted(browser), "conformance": runner, "rust": rust, "caddy": caddy,
            "reason": "measured dependency map"}


def changed_files(base, head="HEAD", root=ROOT, only=None):
    try:
        ancestor = subprocess.check_output(["git", "merge-base", base, head], cwd=root, text=True).strip()
        raw = subprocess.check_output(["git", "diff", "--name-only", "--no-renames", "-z",
                                       *([f"--diff-filter={only}"] if only else []), ancestor, head], cwd=root)
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


def prepare(event, base, output, timings_path=None):
    if event == "pull_request":
        base = merge_parent(base)
        guard_changelog(base)
    changed = changed_files(base) if base else []
    added = changed_files(base, only="A") if base else None
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
