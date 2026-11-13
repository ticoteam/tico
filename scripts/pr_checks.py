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
import shlex
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
UI_MAP = {
    "ui/app/calendar.js": ["calendar-page"],
    "ui/app/calendar-row.js": ["calendar-page", "sidebar-first-paint"],
    "ui/app/chat.js": ["chat", "imported-history"],
    "ui/app/chat-outline.js": ["chat", "imported-history"],
    "ui/app/visual-cards.js": ["visual-cards", "visual-fallback", "visual-hosted"],
    "ui/app/visual-media.js": ["visual-media", "visual-media-app"],
    "ui/github-connect.js": ["github-after-connect", "github-personal", "settings-forms"],
}


def browser_script(root, stem):
    """The file a browser script name runs, ui/tests/<stem>.cjs or .cts; None when neither exists."""
    for suffix in (".cjs", ".cts"):
        path = root / "ui/tests" / f"{stem}{suffix}"
        if path.is_file():
            return path
    return None


def ui_map_key(name):
    """The UI_MAP key for a ui/ file. A script converted to TypeScript (ui/app/x.ts) keeps the entry its .js name has."""
    return name[:-3] + ".js" if name.startswith("ui/") and name.endswith(".ts") else name


CONFORMANCE = "runner_support/tests/test_conformance.py"
# A new or changed file under MIGRATIONS runs the tests that upgrade a legacy or fixture database through every
# migration, in a light plan too: they reach a migration only through the app's start, past a light plan's depth, and
# a migration that assumes a column a later legacy migration adds (#3469: an index on tasks.private) breaks only there.
MIGRATIONS = "backend/migrations/"
UPGRADE_TESTS = (
    "backend/tests/test_migrations.py",  # the migration authority: converge, rerun, roll back, refuse
    "backend/tests/test_schema_every_table.py",  # schema_old_main.sql, every table, one boot
    "backend/tests/test_schema_renames.py",  # schema_pre_cleanup.sql: the startup renames
    "backend/tests/test_task_privacy.py",  # the legacy cloud and native hub (user_version 23) upgrades
    "backend/tests/test_upgrade.py",  # a database an older release made boots twice
    "backend/tests/test_upgrade_boot.py",  # schema_pre_cleanup.sql through create_app and backend.boot
)
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
# Slow tests that sweep repository content: release-only by their marker, so a change to what one reads turns it on in
# the contracts job (-m slow), on a pull request and in its queue group, with the environment the release gate gives it.
# {test file: (path prefixes that turn it on, environment)}. The CLI sweep samples the commands it runs and compares
# refusals only when it runs every one, as the release gate does (scripts/release_checks.py TICO_E2E_CLI_ALL): a playbook
# that teaches a command whose refusal is not recorded (#3301) passes the sample and fails the release. Recorded release
# seconds: CLI 74 with every command (34 sampled), core templates 7.
CONTENT_SWEEPS = {
    "backend/tests/test_e2e_cli.py": (
        (
            "templates/",
            "skills/",
            "clients/commands/",
            "clients/cli_spec.py",
            "clients/hub",
            "clients/remotecli.py",
            "e2e/cli/",
        ),
        {"TICO_E2E_CLI_ALL": "1"},
    ),
    "backend/tests/test_e2e_templates.py": (("templates/", "clients/catalog.py"), {}),
}


def content_sweeps(names):
    """The CONTENT_SWEEPS test files the changed (or gone) paths turn on."""
    return sorted(test for test, (paths, _) in CONTENT_SWEEPS.items() if any(n.startswith(paths) for n in names))


def contract_files(sweeps=()):
    """The contracts job's files: CONTRACT_TESTS, then the content sweeps a change turned on."""
    return [*CONTRACT_TESTS, *(s for s in sorted(sweeps) if s not in CONTRACT_TESTS)]


def sweep_env(files, sweeps):
    """The environment the turned-on sweeps among `files` run with (release_checks.py gives the release gate the same)."""
    env = {}
    for name in files:
        if name in sweeps and name in CONTENT_SWEEPS:
            env.update(CONTENT_SWEEPS[name][1])
    return env


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
# Each check VM has 4 vCPUs (the largest size these checks use) and runs 4 test workers. Groups are sized so a VM
# finishes in about TICO_CI_JOB_SECONDS (330 s), setup included: under 7 minutes with room for estimate error. Measured
# on Nov 9 (queue run 42558912296): a Python VM's setup is 2.2-2.5 minutes (SETUP_SECONDS), and its tests ran about 1.3x
# the recorded seconds the plan packs by. Short Spot jobs are rarely interrupted and lose little when one is.
# GitHub Enterprise allows 500 concurrent hosted jobs for the organisation (it was about 60), so a run spreads its tests
# over more, shorter VMs rather than packing them into a few long ones. MAX_JOBS is a runaway guard, not a capacity limit.
WORKERS = 4
JOB_SECONDS = float(os.environ.get("TICO_CI_JOB_SECONDS", 330))
SETUP_SECONDS = {"python": 150, "browser": 120}  # measured boot, checkout, free disk and warm install
MAX_JOBS = int(os.environ.get("TICO_CI_MAX_JOBS", 40))
# The planner never stretches a plan past its target: MAX_JOB_SECONDS equals JOB_SECONDS, so every VM is planned at the
# target and CEILING only reports the job count. A full queue group needs about 36 Python VMs at this target (the
# recorded suite is about 25,800 one-worker seconds); CEILING and MAX_JOBS leave room for that.
MAX_JOB_SECONDS = JOB_SECONDS
QUEUE_MAX_JOB_SECONDS = JOB_SECONDS
TARGET_STEP_SECONDS = 30
CEILING = {"pull_request": 30, "merge_group": 30}
# Python jobs pass the release gate's own flags: restore a worker-local schema snapshot instead of migrating a fresh
# database for every API test (about half of each file's time), and hand out at most two tests at a time so expensive
# tests spread across the workers.
PYTEST_FLAGS = ("--api-database-snapshots", "--maxschedchunk=2")
UNTIMED_SCRIPT_SECONDS = 20  # a browser script with no measured time yet


def is_test(name):
    """A file pytest collects by name (pytest's default python_files); prepare() keeps the ones in the default run."""
    base = Path(name).name
    return base.endswith(".py") and (base.startswith("test_") or base.endswith("_test.py"))


def is_script_name(name):
    """A browser script name: ui/tests/<name>.cjs (JavaScript) or <name>.cts (TypeScript). A .d.cts is a declaration file."""
    return name.endswith((".cjs", ".cts")) and not name.endswith(".d.cts")


def is_check(name):
    """A Python test file or a browser script (ui/tests/*.cjs or *.cts)."""
    return is_test(name) or (str(Path(name).parent) == "ui/tests" and is_script_name(name))


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
JS_SOURCES = {".js", ".cjs", ".mjs", ".ts"}  # browser and Node code: no Python imports by bare name
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
        # Every scan needs `bare` (from all files' imports) first, so the Python files are parsed twice, one tree alive
        # at a time; the build takes no longer. Holding every tree at once peaked near 2.3 GB per map, and the
        # allocator kept it: a full merge queue VM whose four test workers each built one lost its runner (Nov 8).
        parsed, self.bare = [], set()
        for path in self.files:
            if path.endswith(".py") and (tree := self.parse(path)) is not None:
                parsed.append(path)
                for node in ast.walk(tree):
                    # Top-level names imported through sys.path (`import hubcli` with clients/ on the path).
                    if isinstance(node, ast.Import):
                        self.bare.update(a.name.split(".")[0] for a in node.names)
                    elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                        self.bare.add(node.module.split(".")[0])
        self.bare = {
            name for name in self.bare - set(self.modules) if any(m.endswith("." + name) for m in self.modules)
        }
        for path in parsed:
            # One breadth-first pass per tree (ast.walk's order) feeds every scan: walking each tree once per scan was
            # most of the map's build time, which every PR's plan pays and the selector's tests pay once per worker.
            if (tree := self.parse(path)) is not None:
                self.python(path, tree, self.walk(tree))
        for path in self.files:
            if Path(path).suffix in CODE - {".py"} or (not Path(path).suffix and self.shebang(path)):
                self.text(path)
        self.run_by, self.read_by = defaultdict(set), defaultdict(set)
        for edges, reverse in ((self.runs, self.run_by), (self.reads, self.read_by)):
            for path, targets in edges.items():
                for target in targets - {path}:
                    reverse[target].add(path)

    def parse(self, path):
        """The file's syntax tree, or None for one that cannot be read or parsed (it then reaches everything)."""
        try:
            return ast.parse((self.root / path).read_bytes())
        except (OSError, SyntaxError, ValueError):
            self.unparsed.add(path)
            self.everything.add(path)
            return None

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
            # A sys.path module (`python -m hubcli.x`) is named bare only from a shell script: in JavaScript or
            # TypeScript `run.closest` is a variable, and read as `import run` it ran every run.py (#3335's
            # e2e/website-tools/run.py reaches the conftest, so every UI file selected the whole suite).
            if IDENTIFIER.match(token) and "." in token:
                self.runs[path] |= self.module(token, bare=Path(path).suffix not in JS_SOURCES)

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
        """The files whose listing of a folder holds a changed file: `ui/*.ts` lists ui/app/x.js too only when recursive
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
UI_RUNNER = "scripts/ui-tests.cts"
# Browser scripts that only the release check runs (outside CORE) can break with a ui/ change. Rule 6 picks, for each
# changed ui/ path: a changed script itself; for a shared helper (ui/tests/support/), every script that runs it, directly
# or through other helpers; for UI source outside UI_MAP, the scripts named after it (ui/app/tools.ts: tools, bot-tools,
# redesign-e2e-tools; its stem as whole words of the script's name). The page loads every UI file, so no map from a
# source file to the scripts that can see it is sound: the name match is a cheap best effort beside the smoke set, and
# the release check still runs the rest. Rule 6 also reads the diff: a script that names a top-level function or const
# whose definition changed (task-code.cts calls taskModalOpen in ui/app/task-modal.ts) runs. When rule 6 picks more
# than BROWSER_ALL_OVER scripts (a rename across many scripts, a helper most scripts run), the change runs every script
# the release check runs, about four VMs.
SUPPORT = "ui/tests/support/"
BROWSER_ALL_OVER = 40
# The phone dead-end scripts (release-only) fail when a KNOWN break stops happening, so a fix in the app's own files
# (ui/app/, ui/styles/) breaks them until their KNOWN line goes. Rule 6 runs all of them for such a change, and for their
# rules file; no other change runs them, and the smoke set does not carry them.
PHONE_SCOPE = ("ui/app/", "ui/styles/", "ui/tests/support/phone-rules.cts")
PHONE_PREFIX = "phone-dead-ends-"


def runnable_scripts(root=ROOT):
    """Every script `ui-tests.cts --all` runs (the release check's list), or, without the runner, every script file."""
    try:
        return browser_scripts(["--all"], root)
    except (OSError, subprocess.CalledProcessError, RuntimeError):
        folder = root / "ui/tests"
        return sorted(p.name.rsplit(".", 1)[0] for p in folder.glob("*.c[jt]s") if is_script_name(p.name))


def support_scripts(root, files, name):
    """The browser scripts that use a ui/tests/support file, directly or through other support files: a file uses a
    helper whose file name it names (`require('./support/page.cts')`, `'ui/tests/support/real_hub_server.py'`). The
    dependency map scans no .cts file, so this reads ui/tests itself. `files` are the tracked files; a gone helper is
    found by the files that still name it."""
    folder = [f for f in files if f.startswith("ui/tests/")]
    helpers = defaultdict(set)  # file name -> the support files with that name
    for path in [*(f for f in folder if f.startswith(SUPPORT)), name]:
        helpers[Path(path).name].add(path)
    users = defaultdict(set)  # support file -> the ui/tests files that name it
    for path in folder:
        try:
            text = (root / path).read_text(errors="replace")
        except OSError:
            continue
        for token in set(TOKEN.findall(text)):
            for helper in helpers.get(token.rsplit("/", 1)[-1], ()):
                if helper != path:
                    users[helper].add(path)
    pending, seen, scripts = [name], {name}, set()
    while pending:
        for user in users.get(pending.pop(), ()):
            if user in seen:
                continue
            seen.add(user)
            if is_browser(user):
                scripts.add(Path(user).stem)
            elif user.startswith(SUPPORT):
                pending.append(user)
    return scripts


def named_after(name, scripts):
    """The scripts whose name holds a UI source file's stem as whole words: ui/app/settings-bots.ts names
    settings-bots-search-bulk, not settings."""
    words = Path(name).name.split(".")[0].split("-")
    found = set()
    for script in scripts:
        parts = script.split("-")
        if any(parts[i : i + len(words)] == words for i in range(len(parts) - len(words) + 1)):
            found.add(script)
    return found


# A top-level definition in a classic UI script: a function, const, let, var or class at column 0.
TOP_LEVEL = re.compile(
    r"(?:export\s+)?(?:async\s+)?(?:function\s*\*?\s*([A-Za-z_$][\w$]*)|(?:const|let|var|class)\s+([A-Za-z_$][\w$]*))"
)
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def top_level_spans(text):
    """(name, first line, last line) of each top-level definition in a UI script. A definition starts at column 0 and
    runs until the next line that starts at column 0 with anything but a closing bracket (its `}` stays inside it)."""
    spans, current, lines = [], None, text.splitlines()
    for number, line in enumerate(lines, 1):
        if not line or line[0].isspace() or line[0] in "})]":
            continue
        if current:
            spans.append((*current, number - 1))
            current = None
        match = TOP_LEVEL.match(line)
        if match:
            current = (match.group(1) or match.group(2), number)
    if current:
        spans.append((*current, len(lines)))
    return spans


def hunk_names(diff, old, new):
    """The top-level names whose definitions a `git diff -U0` of one file touches: removed lines name the old file's
    definitions, added lines the new file's."""
    names = set()
    sides = [(top_level_spans(old), []), (top_level_spans(new), [])]
    for match in HUNK.finditer(diff):
        for side, (start, count) in enumerate(((match[1], match[2]), (match[3], match[4]))):
            first, size = int(start), 1 if count is None else int(count)
            if size:
                sides[side][1].append((first, first + size - 1))
    for spans, ranges in sides:
        for name, first, last in spans:
            if any(a <= last and first <= b for a, b in ranges):
                names.add(name)
    return names


def changed_definitions(base, head, names, root=ROOT):
    """{UI source file: the top-level names whose definitions the diff from base's merge point to head changes}, for
    the ui/ source files in `names` (not tests or vendored code). Git failing names nothing."""
    out = {}
    sources = [
        n
        for n in names
        if n.startswith("ui/") and not n.startswith(("ui/tests/", "ui/vendor/")) and n.endswith((".ts", ".js"))
    ]
    if not base or not sources:
        return out
    try:
        ancestor = subprocess.check_output(["git", "merge-base", base, head], cwd=root, text=True).strip()
    except (subprocess.CalledProcessError, OSError):
        return out
    for path in sources:

        def show(rev, path=path):
            done = subprocess.run(
                ["git", "show", f"{rev}:{path}"], cwd=root, capture_output=True, text=True, check=False
            )
            return done.stdout if done.returncode == 0 else ""

        try:
            diff = subprocess.check_output(
                ["git", "diff", "-U0", "--no-renames", ancestor, head, "--", path], cwd=root, text=True
            )
            found = hunk_names(diff, show(ancestor), show(head))
        except (subprocess.CalledProcessError, OSError):
            continue
        if found:
            out[path] = found
    return out


def callers(root, defined, scripts):
    """The browser scripts among `scripts` that name any of `defined` (top-level UI names) as a whole word."""
    if not defined:
        return set()
    pattern = re.compile(r"(?<![\w$])(?:" + "|".join(sorted(map(re.escape, defined))) + r")(?![\w$])")
    found = set()
    for script in scripts:
        path = browser_script(root, script)
        try:
            if path and pattern.search(path.read_text(errors="replace")):
                found.add(script)
        except OSError:
            continue
    return found


# Data folders that one tool owns. A changed, added or deleted file under one of these folders runs exactly the tests
# listed for it, and no other test. The generic data path would instead run every module that globs *.json on an
# unknown folder, and those reach conftest: one baseline piece selected 930 Python tests. The gates do not depend on
# this table. The static job in .github/workflows/pr-checks.yml runs scripts/lint.sh (ruff against its baseline) and
# scripts/check_types.sh (basedpyright against its baseline) on every pull request that is not a draft, and on every
# merge group. A draft skips the static job until it is marked ready. The table chooses only the tests. No ui/ folder
# has an owner yet, so none is listed.
OWNED_DATA = {
    "baselines/": (
        "scripts/tests/test_lint_baseline.py",  # scripts/lint_baseline.py: baselines/ruff
        "scripts/tests/test_pyright_baseline.py",  # scripts/pyright_baseline.py and check_pyright_ratchet.py
        "scripts/tests/test_baseline_files.py",  # scripts/baseline_files.py: the folders the two scripts share
    ),
}


def owned_data(name):
    """(folder, owning tests) for a file under an OWNED_DATA folder, else None."""
    for folder, tests in OWNED_DATA.items():
        if name.startswith(folder):
            return folder, tests
    return None


def owned_reason(owned):
    """The reason line for the changed files that OWNED_DATA covers."""
    folders = sorted({owned_data(name)[0] for name in owned})
    return f"owned data: {', '.join(folders)} runs its owners' tests"


def unread(name):
    """No test or script reads screenshots, retired designs or the top-level pages."""
    return name.startswith(("docs/images/", "docs/history/")) or ("/" not in name and name.endswith(".md"))


def read_only(name):
    """Data that code reads but no test imports: docs pages (the manual, the OpenAPI copy, the API guide) and GitHub
    files (release tests parse the workflows). A change runs the tests that read it (DependencyMap.read_by_tests), not
    everything that imports a reader."""
    return name.startswith(("docs/", ".github/"))


def pinned(name):
    """A dependency pin or one of FULL_SUITE: a change runs the tests that read it, and the smoke set (rule 2)."""
    return name in FULL_SUITE or re.fullmatch(r"requirements[\w.-]*\.txt", Path(name).name) is not None


def is_browser(name):
    return Path(name).parent == Path("ui/tests") and is_script_name(name)


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


def selection(changed, event="pull_request", root=ROOT, graph=None, base=None, added=None, core=None, head="HEAD"):
    """The checks a change runs. A pull request or merge queue group never runs the full default suite or every browser
    script (release validation runs both). Main and scheduled runs keep the full suite (python None). A rule that departs
    from the measured dependency map says so in `uncertain`, and `reason` names the rules that applied:

    0. A file under an OWNED_DATA folder runs the tests listed for that folder, and no other test.
    1. A file gone from the tree (a deletion, or the old side of a rename) runs the tests and browser scripts that name,
       import or read it directly. Data that code reads (docs, GitHub files) runs the tests that read it.
       A gone file that code other than a check names or reads adds the smoke set.
    2. A pin (FULL_SUITE, requirements*.txt) runs the tests and browser scripts that read it directly, and the smoke set.
       A conftest that changed or was deleted runs the tests below it while there are at most CONFTEST_BELOW_LIMIT (and the
       tests that use its fixtures), else the smoke set. A conftest that a change only reaches, through code it runs or
       reads, adds the smoke set; the tests below it are not reached through it.
    3. A Python file the map cannot parse, or does not know, runs the checks that name its path, else the smoke set.
    4. A ui/ file, or the browser runner, outside UI_MAP runs the browser scripts the map links to it, the tests that read
       it directly or run a module that lists its folder (a test that globs ui/*.ts is reached through the module it
       runs), and the smoke set's browser scripts. A changed browser script runs itself. A UI_MAP file runs its scripts;
       a missing one runs the smoke set's browser scripts instead.
    5. An index.html that changes <script src> names adds the files it now names (prepare, through script_changes).
    6. A ui/ path picks browser scripts the release check runs (see SUPPORT): a changed script, every script that runs a
       changed or gone helper under ui/tests/support/, the scripts named after UI source outside UI_MAP, the scripts
       that name a top-level function or const whose definition the diff from `base` to `head` changes in a UI source
       file (changed_definitions), and every phone-dead-ends-* script for a change under PHONE_SCOPE. More than BROWSER_ALL_OVER picked runs every script
       `ui-tests.cts --all` runs. `browser_direct` lists them: a light pull request keeps them.
    7. A path a CONTENT_SWEEPS test reads turns that slow test on in the contracts job (`sweeps`), with its environment.
    8. A file under MIGRATIONS runs UPGRADE_TESTS, which a light plan keeps (narrow).

    `added` names the new files (None: any changed file may be new) and `core` the core browser scripts (None: read from
    scripts/ui-tests.cts).
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
            "sweeps": sorted(CONTENT_SWEEPS),
            "browser_direct": [],
            "rust": rust,
            "conformance": runner,
            "reason": "main runs the full default suite",
            "uncertain": [],
        }
    notes, uncertain = [], []
    sweeps = content_sweeps(changed)

    def plan(python=(), browser=(), caddy=False, contracts=False, reason=None, direct=()):
        return {
            "python": sorted(p for p in python if is_test(p) and not p.startswith("ui/")),
            "browser": sorted(browser),
            "conformance": runner,
            "rust": rust,
            "caddy": caddy,
            "contracts": contracts or bool(sweeps),
            "sweeps": sweeps,
            "browser_direct": sorted(direct),
            "reason": reason or "; ".join(dict.fromkeys(["measured dependency map", *notes])),
            "uncertain": uncertain,
        }

    if not changed:
        uncertain.append("empty diff: nothing to map")
        return plan(*smoke(root), reason="empty diff: smoke set")
    names = [n for n in changed if not unread(n)]
    owned = [n for n in names if owned_data(n)]  # OWNED_DATA first: these never reach the generic data path
    owned_tests = {test for n in owned for test in owned_data(n)[1]}
    names = [n for n in names if n not in owned]
    if not names:
        if owned:
            return plan(owned_tests, reason=owned_reason(owned))
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
            missing = [s for s in scripts if browser_script(root, s) is None]
            browser |= set(scripts) - set(missing)
            if missing:
                smoke_browser = True
                uncertain.append(f"rule 4: {name} maps to missing {', '.join(missing)}: smoke browser scripts")
    for name in unmapped:  # rule 4: a UI file the map does not list
        linked = set(graph.run_by.get(name, ())) | graph.readers(name, added)
        browser |= {Path(p).stem for p in linked if is_browser(p)}
        python |= {p for p in linked if is_test(p)}
        # A module that lists the folder (scripts/build-icon-font.py globs ui/*.ts) reaches the tests that run it.
        python |= {t for lister in graph.listed_by(name, added) for t in graph.run_by.get(lister, ()) if is_test(t)}
        if is_browser(name):  # a changed browser script runs itself: it is the check that changed
            browser.add(Path(name).stem)
        smoke_browser = True
        uncertain.append(f"rule 4: {name} is not in UI_MAP: the scripts that name it, its readers and the smoke set")
        notes.append("rule 4: ui outside UI_MAP")
    direct = set()
    if any(n.startswith("ui/") for n in names):  # rule 6
        runnable = set(runnable_scripts(root))
        phone = {s for s in runnable if s.startswith(PHONE_PREFIX)}
        for name in names:
            if name.startswith(PHONE_SCOPE):
                direct |= phone
            if is_browser(name) and name in live:
                direct.add(Path(name).stem)
            elif name.startswith(SUPPORT):
                direct |= support_scripts(root, graph.files, name) & runnable
            elif (
                name.startswith("ui/")
                and not name.startswith(("ui/tests/", "ui/vendor/"))
                and ui_map_key(name) not in UI_MAP
            ):
                direct |= named_after(name, runnable)
        defined = changed_definitions(base, head, names, root)  # a deleted file's old functions too
        direct |= callers(root, set().union(*defined.values()), runnable)
        if len(direct) > BROWSER_ALL_OVER:
            uncertain.append(f"rule 6: {len(direct)} browser scripts picked: every script the release check runs")
            direct |= runnable
        if direct:
            notes.append("rule 6: browser scripts the release check runs")
        browser |= direct
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
    if owned:
        python |= owned_tests
        notes.append(owned_reason(owned))
    if any(n.startswith(MIGRATIONS) for n in names):  # rule 8
        python |= {t for t in UPGRADE_TESTS if (root / t).is_file()}
        notes.append("rule 8: a migration runs the upgrade tests")
    if any(n in CI_INPUTS for n in names) and not browser:
        browser.add(SMOKE_SCRIPT)
    caddy = any(n.startswith(CADDY_INPUTS) for n in names) or bool(touched & set(CADDY_ROOTS)) or PR_WORKFLOW in names
    contracts = any(n.startswith(CONTRACT_INPUTS) for n in names) or bool((touched | python) & set(CONTRACT_TESTS))
    return plan(python, browser, caddy=caddy, contracts=contracts, direct=direct)


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
            if kind == "browser" and (browser_script(root, name) is None or (core is not None and name not in core)):
                problems.append(f"browser {name}: no such core script")
        if len(set(names)) != len(names):
            problems.append(f"{kind}: an entry is repeated")
        if job_count(total, kind) != 1:
            problems.append(f"{kind} smoke set takes {total:.0f}s: more than one VM")
    for name, scripts in UI_MAP.items():
        for script in scripts:
            if browser_script(root, script) is None:
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


def job_count(seconds, kind, target=JOB_SECONDS):
    """VMs for `seconds` of one-worker test time: each holds WORKERS x (target - setup) of it, so no VM is planned past
    `target`, setup included. A selection that fits one VM takes one VM. Never more than MAX_JOBS."""
    per_job = WORKERS * max(target - SETUP_SECONDS[kind], 30)
    return max(1, min(MAX_JOBS, math.ceil(seconds / per_job)))


def overrun_warnings(loads, setup, target, kind):
    """Annotation lines for a plan whose busiest VM runs past `target` (setup included): one file longer than a VM, or
    the MAX_JOBS cap holding the VMs. `loads` are one-worker seconds per VM; each VM runs WORKERS test workers."""
    worst = max(loads, default=0) / WORKERS + setup
    if worst <= target:
        return []
    return [
        f"::warning::A {kind} VM is planned for {worst:.0f}s against the {target:.0f}s target ({len(loads)} VMs, "
        f"cap {MAX_JOBS}): raise TICO_CI_MAX_JOBS or split the file"
    ]


def groups(counts, timings, total=None, kind="python", fallback=1.0, target=JOB_SECONDS):
    seconds = weights(counts, timings, fallback)
    if total is None:
        total = job_count(sum(seconds.values()), kind, target)
    out, loads = [[] for _ in range(total)], [0.0] * total
    for name in sorted(counts, key=lambda n: (-seconds[n], n)):
        i = min(range(total), key=lambda i: (loads[i], i))
        out[i].append(name)
        loads[i] += seconds[name]
    return [sorted(g) for g in out], loads


def size_groups(counts, timings, browser=None, fixed=0, ceiling=CEILING["pull_request"], cap=MAX_JOB_SECONDS):
    """The Python and browser VM groups of a plan at the smallest target that keeps its test jobs within `ceiling`.
    `browser` is (the scripts as a Counter, their measured times), `fixed` the jobs that always run (conformance and
    contracts). The target starts at JOB_SECONDS and rises by TARGET_STEP_SECONDS up to `cap`; past that the plan keeps
    the cap's VMs, and its count may exceed the ceiling."""
    target = JOB_SECONDS
    while True:
        split, loads = groups(counts, timings, target=target)
        if browser:
            browser_split, browser_loads = groups(
                browser[0], browser[1], kind="browser", fallback=UNTIMED_SCRIPT_SECONDS, target=target
            )
        else:
            browser_split, browser_loads = [], []
        tests = sum(1 for group in split + browser_split if group) + fixed
        if tests <= ceiling or target >= cap:
            return {
                "split": split,
                "loads": loads,
                "browser": browser_split,
                "browser_loads": browser_loads,
                "target": target,
                "tests": tests,
            }
        target = min(cap, target + TARGET_STEP_SECONDS)


def merge_parent(base, head="HEAD", root=None):
    """The base a pull request is checked against. GitHub checks out the PR merged into the current base branch, while
    the event's base sha is the branch tip when the PR was last updated; diffing from that older sha would count
    everything main gained since as the PR's own change."""

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


# The Rust lint, desktop app and recorder jobs check their own folders, so a merge queue group starts their VMs only when
# its change touches one (scripts/rust_checks.sh lists the Rust crates). A diff that could not be read runs them all.
QUEUE_JOB_PATHS = {
    "rust_lint": re.compile(
        r"(runner-rs|relay-rs|tools/release-command|tools/trust-list-check|contracts/runner)/|scripts/rust_checks\.sh$"
    ),
    "desktop": re.compile(r"app/"),
    # The root npm files pin esbuild, which builds the recorder's window (recorder/build-ui.cjs).
    "recorder": re.compile(r"recorder/|package(-lock)?\.json$"),
}


def queue_jobs(changed):
    """Which of QUEUE_JOB_PATHS' jobs a change runs: those whose folders it touches, or all of them for an empty diff."""
    return {job: not changed or any(paths.match(name) for name in changed) for job, paths in QUEUE_JOB_PATHS.items()}


# Light runs (`plan --light`). Pull request runs spent about 70% of the CI job-minutes (Nov 8: 418 pull request and 168
# queue job-minutes per merged pull request; superseded pull request runs were 57% of theirs); throughput (20+ merged
# pull requests an hour) needs both tiers smaller. A pull request run is an early signal (depth 1, ten VMs); a merge
# queue group goes one level further (depth 2, nineteen VMs). The VM counts keep each run's one-worker test seconds about
# what the four- and eight-VM caps of 480 s VMs held before the 330 s target (each VM now carries less work).
# For an event in NARROW, the plan keeps the Python tests of the selection above (unchanged, so release validation's
# incremental runs, scripts/release_increment.py, still select with it) that a change reaches within `depth` levels:
#   level 0: a changed test file;
#   level 1: a file that runs (imports, names) or reads a changed file directly, uses a conftest fixture that runs it,
#            reads the whole repository (DependencyMap.everything), or a rule above picks for it directly (owned data, a
#            changed conftest, docs readers, a gone file's namers);
#   level 2: a file that runs a level-1 file (a test helper or a shared test module included), unless that file is a
#            hub (more than HUB_TEST_USERS tests run it directly: backend/app.py, backend/store.py), whose closure is
#            what made every change select hundreds of tests.
# When it drops anything, by depth or by the cap below, it adds the smoke set (scripts/queue-smoke.json). Then it caps the
# Python tests to `shards` VMs of about NARROW_SHARD_SECONDS, by measured seconds (the run's timings over scripts/test-timings.json), keeping changed
# test files first, then the smoke set, then the nearer level, then the shorter file. What it leaves out is listed in the
# job summary and in the plan (narrow.left_transitive, narrow.left_over_budget). A light pull request also runs the browser
# scripts and the Caddy job only when the change touches their own inputs directly; a queue group keeps
# them, the contracts, the static checks on its merge commit and the Rust, desktop and recorder jobs as they were.
# What a queue group leaves out still runs before a release: release validation's incremental runs select with the full
# rules above (the whole transitive closure) over every change since the last full validation, which runs once every 24
# hours (scripts/release_increment.py), and a red one holds the release until a fix selects its failure.
NARROW = {"pull_request": {"depth": 1, "shards": 10}, "merge_group": {"depth": 2, "shards": 19}}
NARROW_SHARD_SECONDS = JOB_SECONDS
HUB_TEST_USERS = 40
TIMINGS_FILE = "scripts/test-timings.json"


def narrow_budget(shards):
    """One-worker test seconds that fit `shards` VMs of NARROW_SHARD_SECONDS, setup excluded."""
    return shards * WORKERS * (NARROW_SHARD_SECONDS - SETUP_SECONDS["python"])


def reach_levels(names, depth, added=None, root=ROOT, graph=None):
    """{file: level} for the files a change reaches within `depth` levels (see NARROW): 0 for the changed files, 1 for
    what uses one directly, 2 for what runs a level-1 file that is not a hub."""
    graph = graph or DependencyMap(root)
    added = set(names) if added is None else set(added)
    level = dict.fromkeys(names, 0)
    first = set()
    for name in names:
        first |= set(graph.run_by.get(name, ())) | graph.readers(name, added) | graph.fixture_users(name, True)
        if owned := owned_data(name):
            first |= set(owned[1])
        if name == "backend/openapi_v2.py" or (name.startswith("backend/") and name.endswith("_routes.py")):
            # The contract test reads every route through the app's imports (a hub, not followed within a depth) and
            # no longer reads a committed spec file, so a route or generator change names it directly.
            first.add("backend/tests/test_openapi_v2.py")
        if Path(name).name == "conftest.py":  # rule 2: the tests below it while they are few, and its fixtures' users
            below = graph.below(name)
            fixtures = set(graph.fixtures.get(name, {}))
            first |= {p for p in below if len(below) <= CONFTEST_BELOW_LIMIT or fixtures & graph.names.get(p, set())}
        if read_only(name) and not graph.code(name):
            first |= graph.read_by_tests([name], added)
        if name.startswith("ui/") or name == UI_RUNNER:
            first |= {t for lister in graph.listed_by(name, added) for t in graph.run_by.get(lister, ())}
        if not (root / name).is_file():
            first |= graph.named_by(name)
        elif name in graph.unparsed or name not in graph.files:
            first |= {t for t in graph.named_by(name) if is_check(t)}
    # A file that lists the repository or imports a computed name (DependencyMap.everything) reads every file directly.
    first |= graph.everything
    frontier = first - set(level)
    for f in frontier:
        level[f] = 1
    for step in range(2, depth + 1):
        found = set()
        for name in frontier:
            users = set(graph.run_by.get(name, ()))
            if is_browser(name) or sum(1 for user in users if is_test(user)) > HUB_TEST_USERS:
                continue
            found |= users
        frontier = found - set(level)
        for f in frontier:
            level[f] = step
    return level


def narrow(picked, changed, event, added=None, root=ROOT, graph=None):
    """A copy of `picked` (selection's plan for `changed`) narrowed for `event` as NARROW says, with a `narrow` record of
    what it left for later. `tier` ranks the kept Python tests for cap_tests. Main and scheduled plans pass unchanged."""
    if event not in NARROW or picked["python"] is None or not changed:
        return picked
    mode = NARROW[event]
    names = [n for n in changed if not unread(n)]
    level = reach_levels(names, mode["depth"], added, root, graph) if names else {}
    smoke_tests, smoke_scripts = smoke(root)
    full = set(picked["python"])
    # Rule 8: a migration's upgrade tests reach it only through the app's start; they always run, like a changed test.
    upgrade = set(UPGRADE_TESTS) & full if any(n.startswith(MIGRATIONS) for n in names) else set()
    level = {**level, **dict.fromkeys(upgrade, 0)}
    python = {p for p in full if p in level}
    left = sorted(full - python)
    # The smoke set runs when the plan drops anything: here by depth, or later by the time budget (prepare removes
    # `smoke_unless_capped` when the cap drops nothing).
    extra = sorted(set(smoke_tests) - python - full) if full and not left else []
    if full:
        python |= set(smoke_tests)
    # Changed tests 0, the smoke set 1, level 1 is 2, level 2 is 3.
    tier = {p: 0 if level.get(p) == 0 else 1 if p in smoke_tests else 1 + level[p] for p in python}
    out = {**picked, "python": sorted(python)}
    record = {
        "event": event,
        "depth": mode["depth"],
        "shards": mode["shards"],
        "budget_seconds": narrow_budget(mode["shards"]),
        "smoke": bool(left),
        "smoke_unless_capped": extra,
        "left_transitive": left,
        "tier": tier,
    }
    if event == "pull_request":
        direct = {Path(p).stem for p in level if is_browser(p)}
        direct |= {s for n in names if ui_map_key(n) in UI_MAP for s in UI_MAP[ui_map_key(n)]}
        direct |= set(picked.get("browser_direct", ()))
        if any(n.startswith("ui/") or n == UI_RUNNER or pinned(n) for n in names):
            direct |= set(smoke_scripts)
        browser = set(picked["browser"]) & direct
        if any(n in CI_INPUTS for n in names) and not browser:
            browser.add(SMOKE_SCRIPT)
        out["browser"] = sorted(browser)
        record["browser_left"] = sorted(set(picked["browser"]) - browser)
        out["caddy"] = picked["caddy"] and (
            any(n.startswith(CADDY_INPUTS) for n in names)
            or PR_WORKFLOW in names
            or bool(set(level) & set(CADDY_ROOTS))
        )
    out["narrow"] = record
    return out


def cap_tests(files, tier, seconds, budget):
    """(kept, left): `files` in priority order (tier, then measured seconds, then name) while they fit `budget` one-worker
    seconds. A file that does not fit is left and a shorter one after it may still fit. A changed test file (tier 0) and
    the smoke set (tier 1, about two minutes) are always kept: a change's own tests run, and the smoke set runs whenever
    anything is left."""
    kept, left, used = [], [], 0.0
    for name in sorted(files, key=lambda f: (tier.get(f, 1), seconds.get(f, 0.0), f)):
        if tier.get(name, 1) <= 1 or used + seconds.get(name, 0.0) <= budget:
            kept.append(name)
            used += seconds.get(name, 0.0)
        else:
            left.append(name)
    return sorted(kept), left


def committed_timings(root=ROOT):
    """The default suite's committed per-file seconds (TIMINGS_FILE), or {} when it cannot be read."""
    try:
        table = json.loads((root / TIMINGS_FILE).read_text()).get("default", {})
    except (OSError, ValueError, AttributeError):
        return {}
    return table if isinstance(table, dict) else {}


def narrow_summary(record):
    """Markdown for the job summary: what a narrowed plan left for later and why."""
    event = "pull request" if record["event"] == "pull_request" else "merge queue group"
    later = (
        "its merge queue group"
        if record["event"] == "pull_request"
        else "release validation (incremental and daily full)"
    )
    lines = [
        f"### Light {event} run",
        "",
        f"Python tests within {record['depth']} level(s) of the change"
        + (", and the smoke set" if record["smoke"] else "")
        + f", capped at {record['shards']} VMs ({record['budget_seconds']:.0f} one-worker seconds; "
        f"{record.get('selected_seconds', 0):.0f} selected). Left for {later}:",
        "",
    ]
    over = record.get("left_over_budget", [])
    lines.append(f"- {len(record['left_transitive'])} test files reached only through more levels")
    lines.append(f"- {len(over)} test files over the time budget" + (":" if over else ""))
    lines += [f"  - `{name}`" for name in over]
    if record.get("browser_left"):
        lines.append(f"- browser scripts not touched directly: {', '.join(record['browser_left'])}")
    if record["left_transitive"]:
        lines += ["", "<details><summary>Reached only through more levels</summary>", ""]
        lines += [f"- `{name}`" for name in record["left_transitive"]]
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def prepare(event, base, output, timings_path=None, head="HEAD", light=False):
    """`head` is the commit under test: the checkout for a pull request, the queue group's merge commit for a
    merge_group. A group's base is its own base sha, so it needs no merge_parent. `light` narrows the
    plan for an event in NARROW; any other event ignores it."""
    if event == "pull_request":
        base = merge_parent(base)
    changed = changed_files(base, head) if base else []
    added = changed_files(base, head, only="A") if base else None
    if base:
        changed = sorted(set(changed) | set(script_changes(base, head, changed)))
    light = light and event in NARROW
    # One map for the selection and the narrowing; selection builds none for a change it answers without one.
    graph = DependencyMap(ROOT) if light and any(not unread(n) for n in changed) else None
    picked = selection(changed, event, base=base, added=added, graph=graph, head=head)
    if light:
        picked = narrow(picked, changed, event, added=added, graph=graph)
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
    record = picked.pop("narrow", None)
    if record is not None:
        # A light plan caps and packs with the same seconds: the run's measured timings over the committed table.
        timings = {**committed_timings(), **timings}
        seconds = weights(counts, timings)
        kept, record["left_over_budget"] = cap_tests(counts, record.pop("tier"), seconds, record["budget_seconds"])
        if record["left_over_budget"]:
            record["smoke"] = True
        else:
            kept = [f for f in kept if f not in record["smoke_unless_capped"]]
        del record["smoke_unless_capped"]
        counts = Counter({f: counts[f] for f in kept})
        picked["python"] = sorted(counts)
        timings |= {f: seconds[f] for f in kept}  # packing reuses the cap's estimates, untimed files included
        record["selected_seconds"] = round(sum(seconds[f] for f in kept), 1)
        record["left_transitive"] = sorted(set(record["left_transitive"]) - set(kept))
    browser = None
    if picked["browser"] != []:
        scripts = browser_scripts(picked["browser"] or [])
        # Timings are keyed by script stem (the runner writes stems), so a .cts script keeps its .cjs-shaped key.
        browser_times = {name: timings.get(f"ui/tests/{name}.cjs") for name in scripts}
        browser = (Counter(dict.fromkeys(scripts, 1)), browser_times)
    fixed = int(bool(picked["conformance"])) + int(bool(picked["contracts"]))
    ceiling = CEILING["merge_group" if event == "merge_group" else "pull_request"]
    sized = size_groups(
        counts,
        timings,
        browser=browser,
        fixed=fixed,
        ceiling=ceiling,
        cap=QUEUE_MAX_JOB_SECONDS if event == "merge_group" else MAX_JOB_SECONDS,
    )
    split, loads, target = sized["split"], sized["loads"], sized["target"]
    if record is not None and sum(1 for group in split if group) > record["shards"]:
        split, loads = groups(counts, timings, total=record["shards"])  # a light plan's VM cap (NARROW)
    # Visible in the plan job's annotations: the cap (TICO_CI_MAX_JOBS) or one huge file holds a VM past the target.
    for line in overrun_warnings(loads, SETUP_SECONDS["python"], target, "Python"):
        print(line)
    jobs = [{"suite": f"python-{i + 1}", "files": files} for i, files in enumerate(split) if files]
    if browser:
        browser_split = sized["browser"]
        for line in overrun_warnings(sized["browser_loads"], SETUP_SECONDS["browser"], target, "browser"):
            print(line)
        jobs += [{"suite": f"browser-{i + 1}", "files": names} for i, names in enumerate(browser_split) if names]
        loads += sized["browser_loads"]
    if picked["conformance"]:
        jobs.append({"suite": "conformance", "files": [CONFORMANCE], "crates": picked["rust"]})
    if picked["contracts"]:
        # The release contracts, and the content sweeps the change turned on, run their slow tests (-m slow); packed by
        # measured seconds like the python VMs. A job holding a sweep names the environment it runs with (sweep_env).
        sweeps = picked.get("sweeps") or []
        files = contract_files(sweeps)
        contract_split, contract_loads = groups(Counter(dict.fromkeys(files, 1)), timings, target=target)
        for line in overrun_warnings(contract_loads, SETUP_SECONDS["python"], target, "contracts"):
            print(line)
        packed = [group for group in contract_split if group]
        named = [("contracts", files)] if len(packed) == 1 else [(f"contracts-{i}", g) for i, g in enumerate(packed, 1)]
        for suite, group in named:
            env = sweep_env(group, sweeps)
            jobs.append({"suite": suite, "files": list(group), **({"env": env} if env else {})})
    # Actions rejects an empty matrix. Keep an explicit successful selection job
    # for documented no-test changes, without invoking bare pytest.
    if not jobs:
        jobs.append({"suite": "selection", "files": []})
    plan = {
        **picked,
        "changed": changed,
        "queue_jobs": queue_jobs(changed),
        "matrix": {"include": jobs},
        "estimated_seconds": loads,
        "timed_files": len(set(counts) & timings.keys()),
        # The ceiling's arithmetic for the receipt: matrix jobs against the ceiling.
        "test_jobs": len(jobs),
        "ceiling": ceiling,
        "job_target_seconds": target,
    }
    if record is not None:
        plan["narrow"] = record
    Path(output).write_text(json.dumps(plan, indent=2) + "\n")
    print(json.dumps(plan, indent=2))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write("matrix=" + json.dumps(plan["matrix"]) + "\n")
            stream.write("caddy=" + str(plan["caddy"]).lower() + "\n")
            for job, on in plan["queue_jobs"].items():
                stream.write(f"{job}={str(on).lower()}\n")
    if record is not None and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(narrow_summary(record))
    return plan


def browser_scripts(args, root=ROOT):
    """The browser scripts these ui-tests.cts arguments run (CORE for none), by name."""
    out = subprocess.run(
        ["node", "scripts/ui-tests.cts", "--list", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split()
    if not out:
        raise RuntimeError(f"Browser selection {args} listed no scripts")
    return out


def run(plan_path, suite, root=None, quarantine=False):
    """Runs one suite of a plan from `root`'s checkout: the merge queue's VMs, and the release gate's incremental ones.
    `root` defaults to ROOT as it is when the suite runs (a default argument would freeze the import-time value).
    `quarantine` (PR checks and the merge queue only, never release validation) runs the tests in ci/quarantine/ as
    non-strict xfail (scripts/flake_quarantine.py)."""
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
        command = ["node", "scripts/ui-tests.cts", "-j", str(WORKERS), *files]
        # The UI helpers import the backend with TICO_PYTHON (ui/tests/support/page.cts); this interpreter has the
        # locked requirements, while a bare python3 on the runner's PATH may not (no cryptography).
        env = {
            **os.environ,
            "TICO_UI_TIMINGS": str(root / "ci-results" / f"{suite}.json"),
            "TICO_PYTHON": sys.executable,
        }
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
        if suite == "conformance" or suite.split("-")[0] == "contracts":
            command += ["-m", "slow"]
            # A content sweep runs as the release gate runs it (CONTENT_SWEEPS): the job names its environment, or, in an
            # incremental release plan, the plan's `sweeps` do.
            extra = job.get("env") or sweep_env(files, plan.get("sweeps") or ())
            if extra:
                env = {**os.environ, **extra}
                print("Environment: " + " ".join(f"{k}={v}" for k, v in sorted(extra.items())), flush=True)
        if quarantine:
            command.append("--quarantine")
        command += files
        print("Test files: " + " ".join(files), flush=True)  # flake_quarantine.test_files: what a rerun ran again
    (root / "ci-results").mkdir(exist_ok=True)
    rust = cargo_tests(job.get("crates", [])) if suite == "conformance" else 0
    result = subprocess.run(command, cwd=root, env=env)
    if result.returncode == 0 and not suite.startswith("browser-"):
        cases = list(ET.parse(root / "ci-results" / f"{suite}.xml").iter("testcase"))
        # A test that ran: no skipped element, or an xfail (a quarantined failure is one), which JUnit writes as one.
        ran = [t for t in cases if t.find("skipped") is None or t.find("skipped").get("type") == "pytest.xfail"]
        if not ran:
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


def superseded_runs(runs, head_ref, run_number):
    """Ids of the older merge queue runs of the same pull request as `head_ref` (refs/heads/gh-readonly-queue/<base>/
    pr-<number>-<sha>). A rebuilt queue entry has a new head, so its older group is superseded. Pull request runs, other
    queue groups and runs numbered `run_number` or later (this run and any newer one) are never returned."""
    found = re.search(r"/pr-(\d+)-", head_ref or "")
    if not found:
        return []
    number = found.group(1)
    ids = []
    for run in runs:
        branch = re.search(r"/pr-(\d+)-", run.get("head_branch") or "")
        if (
            run.get("event") == "merge_group"
            and run.get("run_number", 0) < run_number
            and branch
            and branch.group(1) == number
        ):
            ids.append(run["id"])
    return ids


def queue_ref_dropped(repo, branch, run=subprocess.run):
    """Whether the merge queue has dropped the group on `branch` (gh-readonly-queue/...): GitHub deletes a group's ref
    when it rebuilds or removes the group. Only a 404 counts; any other answer (the ref exists, an API error) keeps the
    run, which then finishes alone at worst."""
    if not branch.startswith("gh-readonly-queue/"):
        return False
    result = run(["gh", "api", f"repos/{repo}/git/ref/heads/{branch}", "--silent"], capture_output=True, text=True)
    return result.returncode != 0 and "HTTP 404" in (result.stderr or "")


def junit_ran(path):
    """Whether a pytest JUnit report holds a test that ran (passed or failed), not only skipped ones."""
    path = Path(path)
    return path.is_file() and any(case.find("skipped") is None for case in ET.parse(path).iter("testcase"))


def junit_failed(paths, limit=3):
    """The first failing tests in pytest JUnit reports, as "file.py::test", for the error a fail-fast cancel prints."""
    names = []
    for path in paths:
        try:
            cases = ET.parse(path).iter("testcase")
        except (ET.ParseError, OSError):
            continue
        for case in cases:
            if case.find("failure") is not None or case.find("error") is not None:
                module = case.get("classname", "").rsplit(".", 1)[-1]
                names.append(f"{module}.py::{case.get('name', '')}" if module else case.get("name", ""))
    return names[:limit] + ([f"and {len(names) - limit} more"] if len(names) > limit else [])


def pytest_leg(interpreter, jobs, suite, args):
    """pytest, then, where it passed, a check of its JUnit report in the same command (on the VM too): as in CI's
    run(), a leg whose every test skipped is no pass. pytest's 5 (no test collected) passes through: every selected
    file holds only slow tests, which the release gate runs and CI drops. The report path is relative and ignored
    (/ci-results/), so a RunsOn VM can write it."""
    report = f"ci-results/local-{suite}.xml"
    test = [interpreter, "-m", "pytest", "-q", "-n", str(jobs), *PYTEST_FLAGS, f"--junitxml={report}", *args]
    check = [interpreter, "scripts/pr_checks.py", "junit-ran", report]
    return ["bash", "-c", f'{shlex.join(test)}; code=$?; [ "$code" = 0 ] && exec {shlex.join(check)}; exit "$code"']


LOCAL_BASE = "origin/main"
LOCAL_JOBS = 2  # a builder's computer runs other bots' turns too
HEAVY = Path(__file__).resolve().parent / "heavy.sh"


def local(base=LOCAL_BASE, jobs=LOCAL_JOBS, ready=False, root=ROOT, execute=subprocess.run):
    """Run, on this computer, the tests a pull request's checks would select for this branch, before marking it ready.

    A draft pull request runs no CI, so a builder pushes freely, runs this once at the head it wants reviewed and marks
    the pull request ready (`--ready` does that after a pass). The selection is the plan's: the branch's own change
    against `base`, as a pull_request event. The Python files, the contract tests and the browser scripts run through
    scripts/heavy.sh (the computer's shared slots, or a RunsOn VM with TICO_HEAVY_REMOTE=1). What only CI runs (the
    Rust conformance job) is named, not run. The last line is a receipt naming the full head
    sha and the result, which the review gate accepts on the ticket (backend/test_receipts.py).

    Exit codes: 0 passed, 1 failed (or the pull request could not be marked ready), 2 nothing to check here.
    """
    root = Path(root)

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

    def stop(message, code=2):
        print(f"pr_checks local: {message}", file=sys.stderr)
        return code

    def unchanged():
        """The checkout is the commit and nothing else: no edit, and no untracked file a test could pick up."""
        return not git("status", "--porcelain", "--untracked-files=all")

    if not unchanged():
        return stop(
            "commit or set aside your changes and new files first: a receipt names a commit, not a working tree"
        )
    head = git("rev-parse", "HEAD").lower()
    try:
        git("rev-parse", "--verify", "--quiet", base + "^{commit}")
    except subprocess.CalledProcessError:
        return stop(f"no base {base}: fetch it (git fetch origin main) or pass --base")

    def pull_request():
        """This branch's pull request, or a reason it cannot be marked ready at this head."""
        view = execute(
            ["gh", "pr", "view", "--json", "number,headRefOid,isDraft,url"], cwd=root, capture_output=True, text=True
        )
        if view.returncode:
            return None, "no pull request for this branch: open it as a draft (gh pr create --draft) and push"
        found = json.loads(view.stdout)
        if str(found["headRefOid"]).lower() != head:
            return (
                None,
                f"the pull request's head is {found['headRefOid']}, not {head}: push first, then run this again",
            )
        return found, None

    if ready:
        _, problem = pull_request()  # fail before minutes of tests, not after
        if problem:
            return stop(problem, 1)
    changed = changed_files(base, "HEAD", root)
    if not changed:
        return stop(f"this branch changes nothing against {base}")
    changed = sorted(set(changed) | set(script_changes(base, "HEAD", changed, root)))
    picked = selection(changed, "pull_request", root=root, base=base, added=changed_files(base, "HEAD", root, "A"))
    python = [f for f in picked["python"] or () if (root / f).is_file()]
    browser = browser_scripts(picked["browser"], root) if picked["browser"] else []
    # On a RunsOn VM (scripts/heavy.sh with TICO_HEAVY_REMOTE=1) the venv is python3 on its PATH, not this path.
    remote = os.environ.get("TICO_HEAVY_REMOTE") == "1"
    interpreter = "python3" if remote else sys.executable

    legs, leg_env = [], {}
    if python:
        legs.append(
            (f"{len(python)} test file{'s' * (len(python) != 1)}", pytest_leg(interpreter, jobs, "python", python))
        )
    if picked["contracts"]:
        sweeps = picked.get("sweeps") or []
        files = contract_files(sweeps)
        legs.append(("the contract tests", pytest_leg(interpreter, jobs, "contracts", ["-m", "slow", *files])))
        leg_env["the contract tests"] = sweep_env(files, sweeps)
    if browser:
        legs.append(
            (
                f"{len(browser)} browser script{'s' * (len(browser) != 1)}",
                ["node", UI_RUNNER, "-j", str(jobs), *browser],
            )
        )
    ci_only = ["conformance"] if picked["conformance"] else []
    print(f"Selected for {head[:12]} against {base}: {picked.get('reason', '')}", flush=True)
    # CI sets no PYTEST_ADDOPTS; one inherited here (a -k, a -m) could deselect the selection.
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    failed = None
    for name, command in legs:
        extra = leg_env.get(name) or {}
        print("$ " + "".join(f"{k}={v} " for k, v in sorted(extra.items())) + shlex.join(command), flush=True)
        code = execute(["bash", str(HEAVY), *command], cwd=root, env={**env, **extra}).returncode
        if code in (75, 76):
            # scripts/remote.py (TICO_HEAVY_REMOTE=1) never ran it or lost its result: no result, so no receipt.
            return stop(f"{name} gave no result (exit {code}): a remote run needs this head pushed; run this again")
        if code:  # as in CI's run, 5 (pytest ran no test) is red too
            failed = name
            break
    if not unchanged() or git("rev-parse", "HEAD").lower() != head:
        return stop(f"the checkout changed during the run, so it did not test {head} alone: run this again")
    ran = ", ".join(name for name, _ in legs) or "no tests selected for this change"
    rest = f"; left to CI: {', '.join(ci_only)}" if ci_only else ""
    if failed:
        print(f"Local PR checks failed at head {head}: {failed} failed (scripts/pr_checks.py local)")
        return 1
    print(f"Local PR checks passed at head {head}: {ran}{rest} (scripts/pr_checks.py local)")
    if not ready:
        return 0
    # Read it again: a push during the run moves the head this receipt does not cover.
    pr, problem = pull_request()
    if problem:
        return stop(problem, 1)
    if pr["isDraft"]:
        if execute(["gh", "pr", "ready", str(pr["number"])], cwd=root).returncode:
            return stop(f"could not mark {pr['url']} ready; run gh pr ready {pr['number']}", 1)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--event", required=True)
    plan.add_argument("--base", default="")
    plan.add_argument("--head", default="HEAD")
    plan.add_argument("--output", default="ci-plan.json")
    plan.add_argument("--timings")
    plan.add_argument(
        "--light", action="store_true", help="narrow the plan for an event in NARROW (a pull request); others ignore it"
    )
    execute = sub.add_parser("run")
    execute.add_argument("suite")
    execute.add_argument("--plan", default="ci-plan.json")
    execute.add_argument("--root", default=str(ROOT), help="the checkout the suite runs in")
    execute.add_argument(
        "--quarantine",
        action="store_true",
        help="run ci/quarantine/ tests as non-strict xfail (PR checks and the queue)",
    )
    mine = sub.add_parser(
        "local", help="run the tests a pull request's checks would select, here, and print a receipt for its head"
    )
    mine.add_argument("--base", default=LOCAL_BASE)
    mine.add_argument("--jobs", type=int, default=LOCAL_JOBS, help="pytest workers and browser scripts at once")
    mine.add_argument("--ready", action="store_true", help="after a pass, mark this branch's draft pull request ready")
    ran = sub.add_parser("junit-ran", help="exit 1 unless a pytest JUnit report holds a test that was not skipped")
    ran.add_argument("report")
    failed = sub.add_parser("failed", help="print the first failing tests in the JUnit reports in a folder")
    failed.add_argument("folder")
    sub.add_parser("durations")
    sub.add_parser("receipt")
    sub.add_parser("smoke-check")
    cancel = sub.add_parser("cancel-superseded")
    cancel.add_argument("--head-ref", required=True)
    cancel.add_argument("--run-number", type=int, required=True)
    cancel.add_argument("pages", nargs="+", help="gh api --paginate --slurp output files of the runs to consider")
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
        prepare(args.event, args.base, args.output, args.timings, args.head, light=args.light)
    elif args.action == "run":
        return run(args.plan, args.suite, Path(args.root), quarantine=args.quarantine)
    elif args.action == "junit-ran":
        if not junit_ran(args.report):
            print(f"{args.report}: every test skipped or none ran", file=sys.stderr)
            return 1
    elif args.action == "failed":
        print(", ".join(junit_failed(sorted(Path(args.folder).glob("*.xml")))))
    elif args.action == "local":
        return local(base=args.base, jobs=args.jobs, ready=args.ready)
    elif args.action == "durations":
        measured = durations(Path("ci-results").glob("python-*.xml"), Path("ci-results").glob("browser-*.json"))
        if not measured:
            raise RuntimeError("No Python timings were collected")
        Path("durations.json").write_text(json.dumps(measured, indent=2))
    elif args.action == "cancel-superseded":
        # Never this run: it is numbered below its own run number and its id is excluded here as well.
        runs = [
            run
            for path in args.pages
            for page in json.loads(Path(path).read_text())
            for run in page.get("workflow_runs", [])
        ]
        own = os.environ.get("GITHUB_RUN_ID", "")
        repo = os.environ["GITHUB_REPOSITORY"]
        branches = {run["id"]: run.get("head_branch") or "" for run in runs}
        for run_id in superseded_runs(runs, args.head_ref, args.run_number):
            if str(run_id) == own:
                continue
            # A cancelled group that is still the queue's live entry fails `checks` and ejects its pull request, so
            # only a group GitHub has already dropped (its gh-readonly-queue ref deleted) is cancelled.
            if not queue_ref_dropped(repo, branches[run_id]):
                print(f"Keeping merge queue run {run_id}: its group {branches[run_id]} is still live or unknown")
                continue
            print(f"Cancelling superseded merge queue run {run_id}")
            subprocess.run(["gh", "run", "cancel", str(run_id), "--repo", repo], check=False)
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
