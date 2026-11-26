"""Ratchet for the shortcuts that make a check pass without the code being right: fixed sleeps and waits, retries,
raised timeouts, and mocks of our own code in tests. skills/honest-code/SKILL.md says why and what to do instead.

Each rule counts its hits per file. A file's counts live in baselines/landmines/<path>.json, in the layout of
baselines/ruff (scripts/baseline_files.py), and the count of each (file, rule) must equal its baseline:

- above the baseline: every hit in that file is printed with the rule's message, and the check fails;
- below the baseline: the check fails as stale until --write lowers it in the same commit;
- a `ban` rule has no baseline: any hit fails.

    python3 scripts/landmines.py --check                     compare every count with its baseline, and check LESSONS.md
    python3 scripts/landmines.py --check --base origin/main  also fail when a baseline count is higher than on the ref
    python3 scripts/landmines.py --write                     record today's counts (ban rules are never recorded)
    python3 scripts/landmines.py --lessons                   check skills/honest-code/LESSONS.md alone

Python files are read with tokenize, so a pattern inside a string or a comment is not a hit (rules that need the raw
line say so). There is no inline opt-out: a line a rule should not match is fixed in the rule, with a test case.
A rule the --base ref's copy of this script does not have yet may bring its own baseline: that is how a rule starts.

Three rules hold the architecture rather than the tests (CLAUDE.md, "Refactor rules"): Core code in backend/ imports no
Non-Core module, backend/ and clients/ import no more runner_support, and runner_support/ gets no new file. The import
rules read import statements token by token (one hit per Non-Core module a statement imports). The new-file rule compares
the tracked paths with the --base ref's tree, so it runs only with --base, as CI runs it. Two bans keep retired things
out: no-flight-in-hub (the flight recorder's tables are made only in hub.flight.db) and no-tauri (desktop/, Electron, is
the one desktop app).

Three more hold the lifespan and the routes, read from the syntax tree with each scope's imports: backend/app.py starts
no raw asyncio task (its loops are lifespan workers), backend/ adds no install(app, ...) / register*(app, ...) closure
that adds routes (a ratchet: modules move to api_routing.router()), and only backend/api_routing.py builds an
APIRouter, subclasses it or calls include_router.
"""

import argparse
import ast
import collections
import functools
import io
import json
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

import baseline_files

ROOT = Path(__file__).resolve().parent.parent
LAYOUT = "baselines/landmines"
LESSONS = "skills/honest-code/LESSONS.md"
# This script's own test holds every bad pattern as a fixture.
SELF = {"scripts/landmines.py", "scripts/tests/test_landmines.py"}

# The one deadline poll a test may use when the product exposes no event; its pause is the one allowed sleep.
WAIT_HELPER = "backend/tests/helpers/wait.py"
PY_TEST = re.compile(r"(^|/)(tests/.*\.py|test_[^/]*\.py|conftest\.py)$")
UI_TEST = re.compile(r"^ui/tests/.*\.(cts|ts|cjs|js|mjs)$")


def py_test(path):
    return bool(PY_TEST.search(path))


def py_any(path):
    return path.endswith(".py")


def ui_test(path):
    return bool(UI_TEST.match(path))


def retry_scope(path):
    return (
        py_test(path)
        or ui_test(path)
        or path == "pytest.ini"
        or path.startswith(".github/workflows/")
        or bool(re.match(r"(^|.*/)playwright\.config\.[a-z]+$", path))
    )


def big_t(match):
    return int(match.group(1).replace("_", "")) >= 40_000


# Product areas outside Tico's Core. Core backend code may not import them; they may import Core and each other. Matched
# against the first name under backend/ (a module or a package). finance.actions is the one bridge Core may import: the
# agent budget's hold and settle on a run (execution.finance_run_hold) and Finance's setup and grants go through it.
NON_CORE = re.compile(
    r"finance\w*|bookkeeper|bills_address|growth\w*|market\w*|company_page|lenses|calendar_\w+|call\w*"
    r"|live_meetings|meeting\w*|mail\w*|email_\w+|letters|postal_mail|slack_\w+|granola_mcp"
)
CORE_BRIDGE = ("finance", "actions")


def py_prod(path):
    """Shipped Python: not under a tests/ folder and not a conftest.py. A module named test_*.py elsewhere ships
    (backend/test_receipts.py)."""
    return path.endswith(".py") and "/tests/" not in f"/{path}" and path.rsplit("/", 1)[-1] != "conftest.py"


def core_backend(path):
    """Core's own backend code: a backend/ Python file, not a test, outside every Non-Core module and package."""
    parts = path.split("/")
    return (
        len(parts) >= 2
        and parts[0] == "backend"
        and py_prod(path)
        and not NON_CORE.fullmatch(parts[1].removesuffix(".py"))
    )


def runner_support_source(path):
    return path.startswith("runner_support/") and "/tests/" not in path and not py_test(path)


def import_statements(text):
    """(statement index, line number, dotted target) for each name a Python file imports, read token by token so a string,
    a comment and a multi-line `from . import (...)` are read as Python reads them. A relative target keeps its leading
    dots ("..mail" from `from .. import mail`); resolve() makes it absolute."""
    out = []
    try:
        tokens = [
            t
            for t in tokenize.generate_tokens(io.StringIO(text).readline)
            if t.type not in (tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT)
        ]
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return out
    statement, index = [], 0
    for tok in [*tokens, None]:
        if (
            tok is None
            or tok.type in (tokenize.NEWLINE, tokenize.ENDMARKER)
            or (tok.type == tokenize.OP and tok.string in ";:")
        ):
            if statement and statement[0].type == tokenize.NAME and statement[0].string in ("import", "from"):
                out += [(index, statement[0].start[0], target) for target in _targets(statement)]
            statement, index = [], index + 1
            continue
        statement.append(tok)
    return out


def _dotted(words):
    """Split `words` (strings of one import clause) into dotted names separated by commas, dropping `as x` aliases."""
    names, current, skip = [], "", False
    for word in [*words, ","]:
        if word == ",":
            if current:
                names.append(current)
            current, skip = "", False
        elif word == "as":
            skip = True
        elif not skip and word not in "()":
            current += word
    return names


def _targets(statement):
    words = [t.string for t in statement]
    if words[0] == "import":
        return _dotted(words[1:])
    if "import" not in words:
        return []
    split = words.index("import")
    module = "".join(words[1:split])
    names = _dotted(words[split + 1 :])
    if names == ["*"] or not names:
        return [module]
    joiner = "" if module.endswith(".") else "."
    return [f"{module}{joiner}{name}" for name in names]


def resolve(path, target):
    """The absolute dotted name of `target`, imported by the file at `path` (`..mail` in backend/entities/x.py is
    backend.mail)."""
    level = len(target) - len(target.lstrip("."))
    if not level:
        return target
    package = path.split("/")[:-1]
    base = package[: len(package) - (level - 1)] if level > 1 else package
    rest = target[level:]
    return ".".join([*base, rest] if rest else base)


def imports_matching(path, text, wanted):
    """The line numbers of `path`'s import statements, one per distinct module `wanted` maps an absolute target to."""
    lines, seen = [], set()
    for index, number, target in import_statements(text):
        key = wanted(resolve(path, target).split("."))
        if key is not None and (index, key) not in seen:
            seen.add((index, key))
            lines.append(number)
    return lines


def non_core_module(parts):
    if len(parts) < 2 or parts[0] != "backend" or not NON_CORE.fullmatch(parts[1]):
        return None
    if tuple(parts[1:3]) == CORE_BRIDGE:
        return None
    return parts[1]


def core_imports_app(path, text):
    return imports_matching(path, text, non_core_module)


def runner_support_imports(path, text):
    return imports_matching(path, text, lambda parts: parts[0] if parts[0] == "runner_support" else None)


# The flight recorder's tables live in their own file, hub.flight.db (backend/flight.py), not in hub.sqlite. Shipped
# migrations are never edited (scripts/check_migration_order.py), so the ones that made or changed a flight table stay.
FLIGHT_SCHEMA = "backend/flight.py"
FLIGHT_SHIPPED = {"backend/migrations/baseline.sql", "backend/migrations/20261126013526_flight_slow_parts.sql"}
_FLIGHT_TABLE = r"""(?:["'`\[]?main["'`\]]?\s*\.\s*)?["'`\[]?flight_\w"""
# Read over the whole file, so a statement split over lines is one statement; a trigger's ON is looked for within its
# statement (no `;`) and its first 300 characters.
FLIGHT_IN_HUB = re.compile(
    rf"\bCREATE\s+(?:(?:TEMP|TEMPORARY|VIRTUAL)\s+)?(?:TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?{_FLIGHT_TABLE}"
    rf"|\bALTER\s+TABLE\s+{_FLIGHT_TABLE}"
    rf"|\bRENAME\s+TO\s+{_FLIGHT_TABLE}"
    rf"""|\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:"[^"]*"|'[^']*'|`[^`]*`|\[[^\]]*\]|[\w.])+\s+ON\s+{_FLIGHT_TABLE}"""
    rf"|\bCREATE\s+(?:(?:TEMP|TEMPORARY)\s+)?TRIGGER\b[^;]{{0,300}}?\bON\s+{_FLIGHT_TABLE}",
    re.I,
)
SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
# In Python a comment sits inside one string, so it holds no quote: a glob's `/*` in one string and `*/` in a later one
# are not a comment hiding the code between them, and a `--` ends at its string's end or an escaped `\n` too.
PY_SQL_COMMENT = re.compile(r"""--(?:(?!\\n)[^\n"'])*|/\*[^"']*?\*/""")


def hub_schema(path):
    """Where hub.sqlite's tables are made: SQL files and the server's Python, outside tests and the flight file."""
    if "/tests/" in f"/{path}" or path == FLIGHT_SCHEMA or path in FLIGHT_SHIPPED:
        return False
    return path.endswith(".sql") or (path.startswith("backend/") and py_prod(path))


def flight_in_hub(path, text):
    """The line each statement that makes or changes a flight_* table starts on. The whole file is read as written (the
    SQL sits in Python strings, often over several lines), with Python's # comments and SQL's -- and /* */ comments
    blanked in place, so line numbers hold. A DROP is not a hit: it is how the old tables leave hub.sqlite."""
    if path.endswith(".py"):
        lines = text.splitlines(keepends=True)
        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.COMMENT:
                    (row, start), (_, end) = tok.start, tok.end
                    lines[row - 1] = lines[row - 1][:start] + " " * (end - start) + lines[row - 1][end:]
        except (tokenize.TokenError, SyntaxError, IndentationError):
            lines = [line if not line.lstrip().startswith("#") else "\n" for line in text.splitlines(keepends=True)]
        text = "".join(lines)
    comment = PY_SQL_COMMENT if path.endswith(".py") else SQL_COMMENT
    text = comment.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)
    if path.endswith(".py"):
        text = re.sub(r"\\[nrt]", "  ", text)  # a string's escaped newline separates words as the real one does
    return [text.count("\n", 0, m.start()) + 1 for m in FLIGHT_IN_HUB.finditer(text)]


# The Tauri app (app/) is gone; desktop/ (Electron) is the one desktop app. History stays: changelogs and plans here,
# and, outside the scope, the lessons and runner-rs/src/worktrees.rs, whose Tauri line is about customers' repositories.
TAURI_ROOTS = ("app/", "docs/", "scripts/", "backend/", "clients/", "desktop/", "web/", ".github/workflows/")
TAURI_BUILD = re.compile(
    r"tauri[.-](?:conf|override|apps|action|cli|build|plugin|bundler|runtime|utils)|cargo(?:\s+|-)tauri|src-tauri", re.I
)
# In a Cargo.toml: `tauri = ...`, `"tauri" = ...`, a `[...dependencies.tauri]` table (a target's included, however its
# cfg is quoted), or a renamed `x = { package = "tauri" }`.
TAURI_CRATE = re.compile(
    r"""^\s*["']?tauri["']?\s*=|^\s*\[.*\bdependencies\s*\.\s*["']?tauri["']?\s*\]|\bpackage\s*=\s*["']tauri["']"""
)
TAURI_WORD = re.compile(r"(?<![a-z])tauri", re.I)


def tauri_scope(path):
    name = path.rsplit("/", 1)[-1]
    if name.upper().startswith("CHANGELOG") or path.startswith("docs/plans/"):
        return False
    return (
        bool(TAURI_WORD.search(path))
        or path.startswith(TAURI_ROOTS)
        or ("/" not in path and path.endswith(".md"))
        or name in ("Cargo.toml", "package.json")
    )


def tauri_lines(path, text):
    """A file named for Tauri is a hit on its line 1. In any file a Tauri build input (its config, crates, npm packages,
    CLI or action) is a hit on every line; in a Markdown page or a workflow the word itself is, a workflow's comment
    lines aside (desktop.yml's header recalls the Tauri app)."""
    if TAURI_WORD.search(path):
        return [1]
    word = path.endswith(".md") or path.startswith(".github/workflows/")
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        prose = word and not (path.endswith((".yml", ".yaml")) and line.lstrip().startswith("#"))
        crate = path.endswith(".toml") and TAURI_CRATE.search(line)
        if TAURI_BUILD.search(line) or crate or (prose and TAURI_WORD.search(line)):
            found.append(number)
    return found


@functools.lru_cache(maxsize=1)
def parsed(text):
    """`text` as a Python syntax tree, or None when it does not parse. A string or a comment is never code here, a
    one-line suite (`if x: import y; y()`) is read as Python reads it, and the rules that read a file's tree run one
    after another on it, so they share one parse."""
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return None


SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


class Bindings(ast.NodeVisitor):
    """What each name means in each scope: `wanted[(module, name)]` for a `from <module> import <name> [as x]`, and
    None for any other binding (an assignment, a parameter, a def, another import), so a name bound in one function
    never colours another, and a local name hides a module-level import of the same name. `calls` and `classes` are
    each call and class with the scope it is read in: a def's or a class's body in its own scope, its decorators,
    defaults, annotations and bases in the scope around it."""

    def __init__(self, tree, wanted):
        self.wanted = wanted
        self.scopes = {tree: {}}  # scope node -> {name: meaning}
        self.parent = {tree: None}  # scope node -> enclosing scope node (a class scope is skipped, as Python skips it)
        self.stack = [tree]
        self.calls, self.classes = [], []
        for statement in tree.body:
            self.visit(statement)

    def bind(self, name, meaning=None):
        table = self.scopes[self.stack[-1]]
        if table.get(name) is None:
            table[name] = meaning

    def enter(self, node, outer, inner, args=None):
        for child in outer:
            if child is not None:
                self.visit(child)
        enclosing = [scope for scope in self.stack if not isinstance(scope, ast.ClassDef)]
        self.parent[node] = enclosing[-1]
        self.scopes[node] = {}
        self.stack.append(node)
        if args is not None:
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
                if arg is not None:
                    self.bind(arg.arg)
        for child in inner:
            self.visit(child)
        self.stack.pop()

    @staticmethod
    def outside(args):
        every = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
        return [*args.defaults, *args.kw_defaults, *(a.annotation for a in every if a is not None)]

    def visit_FunctionDef(self, node):
        self.bind(node.name)
        self.enter(node, [*node.decorator_list, *self.outside(node.args), node.returns], node.body, node.args)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        self.enter(node, self.outside(node.args), [node.body], node.args)

    def visit_ClassDef(self, node):
        self.bind(node.name)
        self.classes.append((node, self.stack[-1]))
        self.enter(node, [*node.decorator_list, *node.bases, *node.keywords], node.body)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            self.bind(
                alias.asname or alias.name, self.wanted.get((node.module if not node.level else None, alias.name))
            )

    def visit_Import(self, node):
        for alias in node.names:
            self.bind(alias.asname or alias.name.split(".")[0])

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Store):
            self.bind(node.id)

    def visit_Call(self, node):
        self.calls.append((node, self.stack[-1]))
        self.generic_visit(node)

    def meaning(self, scope, name):
        """What `name` means where `scope` reads it: the innermost scope that binds it decides."""
        while scope is not None:
            if name in self.scopes[scope]:
                return self.scopes[scope][name]
            scope = self.parent[scope]
        return None


ASYNCIO = {("asyncio", "create_task"), ("asyncio", "ensure_future"), ("asyncio.tasks", "create_task")}


def task_groups(tree):
    """The names an `async with asyncio.TaskGroup() as g` binds: a TaskGroup waits for its tasks when it exits."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                made = item.context_expr
                func = made.func if isinstance(made, ast.Call) else None
                named = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if named == "TaskGroup" and isinstance(item.optional_vars, ast.Name):
                    names.add(item.optional_vars.id)
    return names


def lifespan_tasks(path, text):
    """Raw tasks: `asyncio.create_task(`, `loop.create_task(`, `asyncio.ensure_future(`, and a call of a name imported
    from asyncio in the scope that calls it. The app's own `create_task(...)` (it creates a Tico task) is not one, nor
    a TaskGroup's `group.create_task(...)`."""
    tree = parsed(text)
    if tree is None:
        return []
    names = Bindings(tree, dict.fromkeys(ASYNCIO, "asyncio"))
    groups = task_groups(tree)
    lines = []
    for node, scope in names.calls:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("create_task", "ensure_future"):
            if not (isinstance(func.value, ast.Name) and func.value.id in groups):
                lines.append(node.lineno)
        elif isinstance(func, ast.Name) and names.meaning(scope, func.id) == "asyncio":
            lines.append(node.lineno)
    return sorted(lines)


INSTALLER = re.compile(r"_?(install|register)\w*")
INSTALLER_DEF = re.compile(r"\bdef\s+_?(install|register)")  # a file without one needs no parse
# What an installer calls on the app to add a route: `@app.get(...)`, `app.add_api_route(...)`, `app.mount(...)`.
ROUTE_CALLS = frozenset(
    "get post put patch delete options head trace api_route route websocket websocket_route add_api_route add_route "
    "add_websocket_route add_api_websocket_route mount include_router".split()
)


def _first_parameter(func, method):
    args = func.args
    positional = [*args.posonlyargs, *args.args]
    static = any(getattr(d, "id", None) == "staticmethod" for d in func.decorator_list)
    if method and not static and positional:
        positional = positional[1:]  # self or cls, however it is named or typed
    if positional:
        return positional[0].arg
    return args.kwonlyargs[0].arg if args.kwonlyargs else None


def _adds_routes(func):
    """Whether `func`'s body (nested defs and their decorators included) calls a route method on `app`."""
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ROUTE_CALLS:
            root = node.func.value
            while isinstance(root, ast.Attribute):
                root = root.value  # app.router.add_api_route
            if isinstance(root, ast.Name) and root.id == "app":
                return True
    return False


def install_closures(path, text):
    """The line of each `def [_]install*(app, ...)` / `def [_]register*(app, ...)` that adds a route to `app`; in a
    method, `app` is first after self or cls, and a keyword-only `app` counts when nothing comes before it. An installer
    that only adds middleware, or a `register(app: str)` that adds no route, is not one."""
    if not INSTALLER_DEF.search(text):
        return []
    tree = parsed(text)
    if tree is None:
        return []
    methods = {
        id(node)
        for owner in ast.walk(tree)
        if isinstance(owner, ast.ClassDef)
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and INSTALLER.fullmatch(node.name)
        and _first_parameter(node, id(node) in methods) == "app"
        and _adds_routes(node)
    )


ROUTER_IMPORTS = {(module, "APIRouter"): "APIRouter" for module in ("fastapi", "fastapi.routing")}


def _is_router(node, scope, names):
    if isinstance(node, ast.Attribute):
        return node.attr == "APIRouter"
    if isinstance(node, ast.Name):
        meaning = names.meaning(scope, node.id)
        return meaning == "APIRouter" or (node.id == "APIRouter" and meaning is None)
    return False


def bare_routers(path, text):
    """`APIRouter(` however it is reached (`fastapi.APIRouter(`, an alias imported from fastapi or fastapi.routing), a
    class built on APIRouter (its routes are plain APIRoutes too), and `x.include_router(`."""
    if "APIRouter" not in text and "include_router" not in text:
        return []  # neither can be reached without its name in the file
    tree = parsed(text)
    if tree is None:
        return []
    names = Bindings(tree, ROUTER_IMPORTS)
    lines = [
        node.lineno
        for node, scope in names.calls
        if _is_router(node.func, scope, names)
        or (isinstance(node.func, ast.Attribute) and node.func.attr == "include_router")
    ]
    lines += [node.lineno for node, scope in names.classes if any(_is_router(b, scope, names) for b in node.bases)]
    return sorted(lines)


@dataclass
class Rule:
    id: str
    mode: str  # "ratchet" or "ban"
    scope: object  # path -> bool
    patterns: list
    message: str
    lesson: str = ""
    view: str = "code"  # "code": strings and comments blanked (Python); "raw": the line as written
    keep: object = None  # match -> bool, to drop a match the pattern cannot exclude
    skip: list = field(default_factory=list)  # patterns that make a line not a hit
    find: object = None  # (path, text) -> [line number, ...]: the hits when a pattern per line cannot find them
    new_file: bool = False  # a hit is a file in scope that the --base ref's tree does not have (new_files)

    def text(self):
        see = f" See {LESSONS}#{self.lesson}." if self.lesson else ""
        return f"{self.message}{see}"


RULES = [
    Rule(
        "no-retry",
        "ban",
        retry_scope,
        [
            re.compile(r"pytest\.mark\.flaky"),
            re.compile(r"rerunfailures"),
            re.compile(r"--reruns\b"),
            re.compile(r"\breruns\s*="),
            re.compile(r"^\s*retries\s*:\s*[0-9]"),
        ],
        "Do not retry a test. A test that passes on a rerun is flaky: fix its cause or delete it with its reason "
        "(SKILL.md, 'A test failed, then passed').",
        "L01",
    ),
    Rule(
        "no-fixed-sleep-py",
        "ratchet",
        lambda path: py_test(path) and path != WAIT_HELPER,
        [re.compile(r"\b(time|asyncio)\.sleep\(\s*(?!0\s*\)|0\.0\s*\))")],
        "Fixed sleep in a test. Wait on the event the fake emits (threading.Event, Stream.until), or move the clock "
        "with H.now or time_machine.travel(..., tick=False). A sleep guesses how long real work takes and fails on a "
        "loaded runner.",
        "L02",
    ),
    Rule(
        "no-fixed-wait-ui",
        "ratchet",
        ui_test,
        [re.compile(r"\bwaitForTimeout\("), re.compile(r"new Promise\b.*\bsetTimeout\(")],
        "Fixed wait in a browser script. Use until(check, what) or drawn(...) from support/e2e.cts, one "
        "page.waitForFunction that returns the value, or page.waitForResponse registered before the action.",
        "L02",
    ),
    Rule(
        "no-raised-timeout",
        "ratchet",
        ui_test,
        [re.compile(r"\btimeout\s*:\s*([0-9][0-9_]{3,})"), re.compile(r"(?<![\w.])t\(\s*([0-9][0-9_]*)\s*\)")],
        "A longer timeout is not a fix. Wait for the event the step depends on. A deadline goes through t() and fails "
        "with a message that names what never arrived.",
        "L26",
        keep=lambda m: not m.re.pattern.startswith("(?<!") or big_t(m),
    ),
    Rule(
        "no-unittest-mock",
        "ratchet",
        py_test,
        [
            re.compile(r"\bunittest\.mock\b"),
            re.compile(r"\bfrom\s+unittest\s+import\b.*\bmock\b"),
            re.compile(r"\b(MagicMock|AsyncMock|NonCallableMock)\b"),
            re.compile(r"\bmock\.patch\b"),
        ],
        "Do not replace our own code in a test. Run the real code. For something outside Tico use a fake "
        "(skills/honest-code/references/examples.md lists them) or pass the dependency in.",
        # environment values are not a double: this is what a unittest.TestCase has for monkeypatch.setenv
        skip=[re.compile(r"^(?!.*mock\.patch(?!\.dict\(\s*os\.environ)).*mock\.patch\.dict\(\s*os\.environ")],
    ),
    Rule(
        "no-global-time-patch",
        "ban",
        py_test,
        [
            re.compile(r"\b(?:setattr|patch\.object)\(\s*[\w.]*\btime\s*,\s*['\"]\w+['\"]"),
            re.compile(r"\b(?:setattr|patch)\(\s*['\"][\w.]*\btime\.\w+['\"]"),
        ],
        "This replaces sleep, time or monotonic on the time module for the whole process, so every thread sees it. "
        "Give the module its own seam (def _sleep(seconds): time.sleep(seconds); def _now(): return time.time()), "
        "as in clients/tico.py and docker/updater.py, and patch that, or use time_machine for the wall clock.",
        "L31",
        view="raw",
    ),
    Rule(
        "timing-assert",
        "ratchet",
        lambda path: py_test(path) or ui_test(path),
        [
            re.compile(r"^\s*assert\b.*(?:monotonic\(\)|perf_counter\(\)|time\.time\(\)|\btook\b|\belapsed\b).*[<>]"),
            re.compile(r"\b(?:assert(?:\.\w+)?|expect)\(.*(?:Date\.now\(\)|performance\.now\(\)).*[<>]"),
        ],
        "An assertion on elapsed time flips on a loaded runner and misses a small regression. Assert the work done, "
        "as a count; prove 'it does not wait on X' by holding X on an Event and getting the answer while X is held.",
        "L03",
        skip=[re.compile(r"^\s*assert\s+time\.monotonic\(\)\s*<\s*deadline\b")],  # a wait's own deadline
    ),
    Rule(
        "no-assigned-clock-seam",
        "ban",
        py_any,
        [re.compile(r"^\w+\s*=\s*time\.(?:sleep|time|monotonic)\s*$")],
        "A module seam assigned at import (_sleep = time.sleep) keeps the real function, so a test that swaps the "
        "module's time for a fake clock never reaches it. Make it a def that calls time at each call: "
        "def _sleep(seconds): time.sleep(seconds).",
        "L31",
    ),
    Rule(
        "no-new-runner-support-module",
        "ban",
        runner_support_source,
        [],
        "runner_support/ is frozen: no new file goes in it (tests excepted). Build the runner side in runner-rs; a "
        "server or CLI helper goes in backend/ or clients/.",
        "L35",
        new_file=True,
    ),
    Rule(
        "no-runner-support-import",
        "ratchet",
        lambda path: path.startswith(("backend/", "clients/")) and py_prod(path),
        [],
        "backend/ and clients/ import runner_support in no new place: it is frozen and its jobs move to runner-rs. Ask "
        "the hub's API for what the runner knows, or move the helper you need into backend/ or clients/.",
        "L36",
        find=runner_support_imports,
    ),
    Rule(
        "core-imports-app",
        "ratchet",
        core_backend,
        [],
        "Core imports a Non-Core module (Finance, Growth, Market, Calendar, Calls, Meetings, Mail, Letters, Slack, "
        "Granola, the company page, lenses). Core never imports Non-Core: the module registers itself with Core "
        "(a route, a worker, an event handler) or Core calls it through finance.actions, the one bridge.",
        "L37",
        find=core_imports_app,
    ),
    Rule(
        "no-flight-in-hub",
        "ban",
        hub_schema,
        [],
        "The flight recorder's tables (flight_*) live in hub.flight.db, made by backend/flight.py; hub.sqlite gets no "
        "new or changed flight table. Change the table in backend/flight.py. DROP TABLE IF EXISTS flight_* is allowed: "
        "it takes the old copies out of hub.sqlite.",
        "L38",
        find=flight_in_hub,
    ),
    Rule(
        "no-tauri",
        "ban",
        tauri_scope,
        [],
        "The Tauri app (app/) is retired: desktop/ (Electron) is the one desktop app. No Tauri config, crate, npm "
        "package, CLI, workflow or doc comes back; build and describe the Electron app instead (docs/desktop.md).",
        "L39",
        find=tauri_lines,
    ),
    Rule(
        "no-lifespan-task",
        "ban",
        lambda path: path == "backend/app.py",
        [],
        "A raw asyncio task in the app dies silently and is never awaited at shutdown. Start background work as a "
        "lifespan worker (backend/lifespan_workers.py: Workers.task, Workers.thread or Workers.job): a crash is logged, "
        "recorded and raised on Health (worker_died) while the other workers run on, and shutdown unwinds every one.",
        "L40",
        find=lifespan_tasks,
    ),
    Rule(
        "no-install-closure",
        "ratchet",
        lambda path: path.startswith("backend/") and py_prod(path),
        [],
        "No new install(app, ...) or register*(app, ...) closure that adds routes to the app. Build the module's routes on api_routing.router(), "
        "take services as a deps.Injected parameter, and add them in create_app with api_routing.include(app, "
        "router), as backend/report_order.py does.",
        "L41",
        find=install_closures,
    ),
    Rule(
        "no-bare-apirouter",
        "ban",
        lambda path: path.startswith("backend/") and py_prod(path) and path != "backend/api_routing.py",
        [],
        "Build routes on api_routing.router() and add them with api_routing.include(app, router). A bare APIRouter's "
        "routes skip TicoRoute (orjson, display names, run names, shared reads, compression), and app.include_router "
        "adds one lazy entry that hides its routes from app.routes.",
        "L42",
        find=bare_routers,
    ),
]
BY_ID = {rule.id: rule for rule in RULES}


def code_lines(text):
    """The lines of Python source with every string and comment blanked, so a pattern only matches code. Falls back to
    the raw lines when the file does not tokenize."""
    lines = text.splitlines()
    out = [list(line) for line in lines]
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT) and not (
                hasattr(tokenize, "FSTRING_MIDDLE") and tok.type == tokenize.FSTRING_MIDDLE
            ):
                continue
            (srow, scol), (erow, ecol) = tok.start, tok.end
            for row in range(srow, erow + 1):
                if row - 1 >= len(out):
                    break
                line = out[row - 1]
                start = scol if row == srow else 0
                end = ecol if row == erow else len(line)
                for i in range(start, min(end, len(line))):
                    line[i] = " "
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return lines
    return ["".join(line) for line in out]


def script_lines(text):
    """The lines of a JS or TS file, with whole-line comments blanked."""
    return ["" if line.lstrip().startswith(("//", "*", "/*")) else line for line in text.splitlines()]


def hits_in(path, text, rules):
    """{rule id: [(line number, line), ...]} for the rules whose scope takes `path`."""
    raw = text.splitlines()
    views = {}
    found = collections.defaultdict(list)
    for rule in rules:
        if rule.new_file or not rule.scope(path):
            continue
        if rule.find is not None:
            for number in rule.find(path, text):
                found[rule.id].append((number, raw[number - 1].strip() if number <= len(raw) else ""))
            continue
        if rule.view == "raw":
            lines = views.setdefault("raw", script_lines(text) if not path.endswith(".py") else _no_comment_lines(raw))
        elif path.endswith(".py"):
            lines = views.setdefault("code", code_lines(text))
        else:
            lines = views.setdefault("script", script_lines(text))
        for number, line in enumerate(lines, 1):
            if any(pattern.search(line) for pattern in rule.skip):
                continue
            for pattern in rule.patterns:
                if any(rule.keep is None or rule.keep(m) for m in pattern.finditer(line)):
                    found[rule.id].append((number, raw[number - 1].strip() if number <= len(raw) else ""))
                    break
    return found


def _no_comment_lines(raw):
    return ["" if line.lstrip().startswith("#") else line for line in raw]


def tracked_files(root=ROOT):
    done = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    return sorted(name for name in done.stdout.decode().split("\0") if name)


def new_files(root, base, rules=RULES):
    """{path: {rule id: [(1, ...)]}} for each tracked file a new-file rule takes that the branch added: one the tree at
    the merge base of HEAD and `base` does not have (or `base` itself when there is none), so a file `base` deleted
    after the branch point is not new."""
    rules = [rule for rule in rules if rule.new_file]
    if not rules:
        return {}
    fork = baseline_files.git(root, "merge-base", "HEAD", base)
    tree = fork.stdout.strip() if fork.returncode == 0 and fork.stdout.strip() else base
    listed = baseline_files.git(root, "ls-tree", "-r", "-z", "--name-only", tree, text=False)
    if listed.returncode != 0:
        raise SystemExit(f"landmines: cannot list the tree at {base}: {listed.stderr.decode().strip()}")
    before = set(listed.stdout.decode().split("\0"))
    found = {}
    for path in tracked_files(root):
        for rule in rules:
            if path not in before and rule.scope(path):
                found.setdefault(path, {})[rule.id] = [(1, f"{path} is new (not in {base})")]
    return found


def scan(rules=RULES):
    """{path: {rule id: [(line number, line), ...]}} over every tracked file some rule takes."""
    result = {}
    for path in tracked_files():
        if path in SELF or not any(rule.scope(path) for rule in rules if not rule.new_file):
            continue
        file = ROOT / path
        if not file.is_file():
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = ""  # a binary file still counts by its path (an icon under src-tauri/)
        except OSError:
            continue
        found = hits_in(path, text, rules)
        if found:
            result[path] = dict(found)
    return result


def counts_of(found):
    return {
        path: {rule: len(lines) for rule, lines in sorted(rules.items()) if BY_ID[rule].mode != "ban"}
        for path, rules in found.items()
    }


def piece_path(path):
    return ROOT / LAYOUT / f"{path}.json"


def render_piece(path, rules):
    return json.dumps({path: dict(sorted(rules.items()))}, indent=2) + "\n"


def load_pieces(texts):
    counts = {}
    for text in texts:
        for path, rules in json.loads(text).items():
            counts[path] = {rule: int(n) for rule, n in rules.items()}
    return counts


def read_baseline():
    return load_pieces(piece.read_text(encoding="utf-8") for piece in baseline_files.pieces(ROOT / LAYOUT))


def baseline_at(ref):
    return load_pieces(baseline_files.pieces_at(ROOT, ref, LAYOUT))


def write():
    counts = {path: rules for path, rules in counts_of(scan()).items() if any(rules.values())}
    baseline_files.write_pieces(
        ROOT / LAYOUT, {piece_path(path): render_piece(path, rules) for path, rules in counts.items()}
    )
    total = sum(sum(rules.values()) for rules in counts.values())
    print(f"landmines: wrote {total} hits in {len(counts)} files to {LAYOUT}/")


def compare(found, baseline):
    """The problems of `found` against `baseline`, one string each."""
    problems = []
    for path in sorted(set(found) | set(baseline)):
        rules = found.get(path, {})
        allowed = baseline.get(path, {})
        for rule_id in sorted(set(rules) | set(allowed)):
            rule = BY_ID.get(rule_id)
            if rule is None:
                problems.append(f"{LAYOUT}/{path}.json: unknown rule {rule_id}; remove it")
                continue
            lines = rules.get(rule_id, [])
            limit = 0 if rule.mode == "ban" else allowed.get(rule_id, 0)
            if rule.mode == "ban" and rule_id in allowed:
                problems.append(f"{LAYOUT}/{path}.json: {rule_id} is a ban and has no baseline; remove the entry")
            if len(lines) > limit:
                head = f"{rule_id}: {path} has {len(lines)}, its baseline allows {limit}. {rule.text()}"
                problems.append("\n".join([head, *(f"  {path}:{n}: {line}" for n, line in lines)]))
            elif len(lines) < limit:
                problems.append(
                    f"{rule_id}: {path} now has {len(lines)}; lower its baseline to {len(lines)} with "
                    "`python3 scripts/landmines.py --write` in this commit"
                )
    return problems


def rules_at(ref):
    """The rule ids the ref's copy of this script defines, or None when the ref has no copy."""
    text = baseline_files.show_at(ROOT, ref, "scripts/landmines.py")
    if text is None:
        return None
    return set(re.findall(r'^\s*Rule\(\s*\n\s*"([a-z0-9-]+)"', text, re.M))


def growth(baseline, base_baseline, base_rules):
    """A baseline count higher than on the base, for a rule the base already has."""
    if base_rules is None:
        return []
    problems = []
    for path, rules in sorted(baseline.items()):
        for rule_id, count in sorted(rules.items()):
            if rule_id not in base_rules:
                continue
            before = base_baseline.get(path, {}).get(rule_id, 0)
            if count > before:
                problems.append(
                    f"{rule_id}: the baseline for {path} grew from {before} to {count}. A baseline only goes down. "
                    f"{BY_ID[rule_id].text() if rule_id in BY_ID else ''}".rstrip()
                )
    return problems


# LESSONS.md

INCIDENT = re.compile(r"^\d{4}-\d{2}-\d{2} (#\d+|[0-9a-f]{10})\b")


def split_incidents(cell):
    """The incidents of a row: split on `;` outside brackets, so a note in brackets may hold a `;`."""
    parts, depth, current = [], 0, ""
    for char in cell:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == ";" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return [part.strip() for part in parts if part.strip()]


def lesson_rows(text):
    rows = []
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 7 and re.fullmatch(r"L\d+", cells[0]):
            rows.append(
                dict(zip(["id", "lesson", "incidents", "class", "seen", "status", "where"], cells, strict=True))
            )
    return rows


def lesson_problems(text, rule_ids, exists):
    problems = []
    rows = lesson_rows(text)
    ids = {row["id"] for row in rows}
    if not rows:
        return [f"{LESSONS}: no lesson rows found"]
    for row in rows:
        where = f"{LESSONS} {row['id']}"
        incidents = split_incidents(row["incidents"])
        for incident in incidents:
            if not INCIDENT.match(incident):
                problems.append(f"{where}: incident {incident!r} needs a date and a PR number or 10-character SHA")
        if not row["seen"].isdigit() or int(row["seen"]) != len(incidents):
            problems.append(f"{where}: seen is {row['seen']} but it lists {len(incidents)} incidents")
        if row["class"] not in ("script", "judgment"):
            problems.append(f"{where}: class must be script or judgment")
        status = row["status"]
        kind = status.split(" ", 1)[0]
        if kind not in ("open", "proposed", "encoded", "example", "retired"):
            problems.append(f"{where}: unknown status {status!r}")
        if row["class"] == "script" and kind == "open" and row["seen"].isdigit() and int(row["seen"]) >= 2:
            problems.append(
                f"{where}: a script lesson seen {row['seen']} times is still open. Add its rule to "
                "scripts/landmines.py (or ruff) and mark it encoded, or mark it proposed with the rule id"
            )
        if kind == "encoded":
            named = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)+", status.split("`")[0]) if "`" not in status else []
            paths = re.findall(r"`([^`]+)`", status)
            for name in named:
                if name not in rule_ids:
                    problems.append(f"{where}: encoded rule {name} is not in scripts/landmines.py")
            for path in paths:
                if not exists(path.split()[0]):
                    problems.append(f"{where}: encoded check {path} does not exist")
            if not named and not paths:
                problems.append(f"{where}: an encoded row names its rule id or `script path`")
        if row["where"] not in ("-", ""):
            path = row["where"].strip("`")
            if not exists(path):
                problems.append(f"{where}: where path {path} does not exist; fix it or retire the row")
    for rule in RULES:
        if rule.lesson and rule.lesson not in ids:
            problems.append(f"scripts/landmines.py {rule.id}: cites {rule.lesson}, which is not in {LESSONS}")
    return problems


def check_lessons():
    file = ROOT / LESSONS
    if not file.is_file():
        return [f"{LESSONS} is missing"]
    text = file.read_text(encoding="utf-8")
    problems = lesson_problems(text, set(BY_ID), lambda path: (ROOT / path).exists())
    proposed = [row["id"] for row in lesson_rows(text) if row["status"].startswith("proposed")]
    if proposed:
        print(f"landmines: {len(proposed)} lessons wait for a rule: {', '.join(proposed)}")
    return problems


def check(base):
    found = scan()
    if base:
        for path, rules in new_files(ROOT, base).items():
            found.setdefault(path, {}).update(rules)
    baseline = read_baseline()
    problems = compare(found, baseline)
    if base:
        problems += growth(baseline, baseline_at(base), rules_at(base))
    problems += check_lessons()
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    action.add_argument("--lessons", action="store_true")
    parser.add_argument("--base", metavar="REF", help="git ref to compare the baseline against (e.g. origin/main)")
    args = parser.parse_args(argv)
    if args.write:
        write()
        return 0
    problems = check_lessons() if args.lessons else check(args.base)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"landmines: {len(problems)} problems; skills/honest-code/SKILL.md says how to fix each", file=sys.stderr)
        return 1
    print("landmines: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
