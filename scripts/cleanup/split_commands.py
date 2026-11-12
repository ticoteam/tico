"""Move the tool table out of clients/hubtools.py into clients/commands/, one module per noun, changing no behaviour.

    python3 scripts/cleanup/split_commands.py               dry run: build the package in a scratch copy and verify it
    python3 scripts/cleanup/split_commands.py --write       the same, then write it into this tree
    python3 scripts/cleanup/split_commands.py --plan F.json also write which statement went where

Only code moves. Every top-level statement of clients/hubtools.py is copied into one module:
  1. A statement that registers tools (`@tool("tico_x", ...)`, `tool("tico_x", ...)(fn)`) goes to the module of its
     tools' noun (NOUN_MODULE, from the tool's `tico <noun> <verb>` command; `task` is split by verb, TASK_WORK_VERBS).
     A noun with no entry stops the script: add it.
  2. A statement binding a name in PINNED goes where PINNED says. The base modules hold what every tool module reads:
     `_registry` (TOOLS, `tool()`, the server's name and version), `_schemas` (argument schemas and their helpers),
     `_requester` (who asks: kinds, the Requester), `_shared` (helpers several modules call).
  3. Any other statement goes to the one module whose statements use what it binds, or to `_shared` when several do;
     repeated until nothing moves. A statement nothing uses goes to `_shared`.
  4. Statements that import or use a side module (clients/hubtools_<area>.py) stay at the end of the facade.
Each module keeps its statements in their original order, with the comments above each, and imports the names it
uses explicitly (no star imports). `_access` (who sees which tool) and `_mcp` (BY_NAME, the Protocol) load after every
tool module. clients/hubtools.py becomes a facade whose `__all__` re-exports every name another file reads from it
(`from clients.hubtools import X`, `hubtools.X`, `monkeypatch.setattr(hubtools, "X", ...)`), the same objects.

The text is copied as it is except for the NORMALIZATIONS in commands_identity.py (N1: `parents[N]` gains one level;
N2: a relative import names the clients package), and two edits outside the package: a test that patches, on the
facade, a name moved code reads as a global patches that code's module instead (PATCH_TARGETS), and the files that
list the CLI's code by path also list clients/commands/ (PATH_LISTS).

Verified before anything is written (a dry run stops here), on two scratch copies, before and after the move:
  a. the registry: every tool's name, description, schema, writes, local, kinds offered to, its BY_NAME entry, and its
     handler (qualified name, source AST, defaults, closure values)                                         (identical)
  b. `tools/list` bytes for both transports and every kind of caller                                        (identical)
  c. every moved statement's AST equals the original's after NORMALIZATIONS, and only those
  d. the `tico` command tree                                                                                 (identical)
  e. the importers load: clients.hubmcp, clients.remotecli, clients.hubcli, backend.mcp, ... ; no import cycle; every
     name a module reads is bound in it (symtable); no import left unused; no module over MAX_LINES lines
  f. the version a client sees (release_version, MCP initialize, `tico --version`), TICO_VERSION unset (read from the
     VERSION file) and set                                                                                   (identical)
  g. clients/tests/test_commands_package.py passes on the moved copy: every entry point in a fresh process and in
     different orders, the upload paths that import lazily, the facade's objects are the package's
(a, b, d, e, f come from scripts/cleanup/commands_identity.py.)
"""

import argparse
import ast
import builtins
import collections
import json
import os
import re
import shutil
import subprocess
import symtable
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from importlib import util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "clients/hubtools.py"
PACKAGE = "clients/commands"
MAX_LINES = 1200  # clients/tests/test_commands_package.py holds every clients/commands/ module to it
BEHAVIOUR_TEST = "clients/tests/test_commands_package.py"
VERSION_FILE = "v9.9.9-identity"  # written as VERSION into both scratch copies

# Noun (the first word of a tool's `tico` command) -> module.
NOUN_MODULE = {
    "thread": "threads",
    "message": "threads",
    "conversation": "threads",
    "note": "threads",
    "chat": "threads",
    "whoami": "threads",
    "task": "tasks",
    "ok": "tasks",
    "tag": "task_work",
    "replay": "task_work",
    "roadmap": "task_work",
    "feedback": "task_work",
    "task-manager": "task_work",
    "goal": "goals",
    "kpi": "goals",
    "proposal": "goals",
    "strategy": "strategy",
    "brand": "strategy",
    "growth": "strategy",
    "lens": "strategy",
    "market": "market",
    "contacts": "contacts",
    "listening": "listening",
    "inbound": "listening",
    "file": "content",
    "page": "content",
    "card": "content",
    "pin": "content",
    "doc": "content",
    "automation": "automations",
    "event-types": "automations",
    "trigger": "webhooks",
    "relay": "webhooks",
    "webhook": "webhooks",
    "notice": "webhooks",
    "sleep": "webhooks",
    "release": "webhooks",
    "bot": "bots",
    "agent": "bots",
    "skill": "bots",
    "template": "bots",
    "external": "bots",
    "grokbot": "bots",
    "human": "admin",
    "group": "admin",
    "seat": "admin",
    "org": "admin",
    "team": "admin",
    "setup": "admin",
    "settings": "admin",
    "alerts": "admin",
    "api": "admin",
    "sql": "admin",
    "audit": "admin",
    "mcp": "admin",
    "origin": "admin",
    "token": "admin",
    "auth": "admin",
    "events": "admin",
    "computer": "computers",
    "health": "computers",
    "maintenance": "computers",
    "support": "computers",
    "tool": "tools",
    "credential": "tools",
    "repo": "tools",
    "slack": "tools",
    "assistant": "assistant",
    "needs-you": "assistant",
    "brief": "assistant",
    "update": "assistant",
    "changelog": "assistant",
    "run": "assistant",
    "email": "mail",
    "mail": "mail",
    "letter": "mail",
    "call": "calls",
    "meeting": "calls",
    "calendar": "calls",
    "company-page": "company_page",
    "audiences": "company_page",
    "editions": "company_page",
    "readers": "company_page",
    "finance": "finance",
    "gateway": "gateway",
    "decision": "gateway",
    "classify": "gateway",
    "rules": "rules",
}
# `tico task <verb>` commands that are not the task's own lifecycle: tasks.py would pass MAX_LINES with them.
TASK_WORK_VERBS = {
    "comment",
    "comment-edit",
    "comment-delete",
    "comments",
    "link",
    "links",
    "unlink",
    "readers",
    "worktree",
    "caps",
    "cap",
    "attach",
    "attachments",
    "run",
    "runs",
    "thumbs",
    "deleted",
    "restore",
    "delete",
    "label",
    "labels",
    "follow",
    "unfollow",
    "roles",
    "role",
    "export",
    "import",
    "history",
    "log",
    "timeline",
    "artifacts",
}
MODULE_DOCS = {
    "_registry": "The table itself: TOOLS, `tool()` that fills it, CLI_TOOL_ALIASES, and the server's name, version "
    "and instructions.",
    "_schemas": "Argument schemas and the helpers that read arguments, shared by the tool modules.",
    "_requester": "Who asks: the kinds of caller, the Requester a tool calls the API through, and acting for a person.",
    "_shared": "Helpers that tools of several modules call.",
    "_access": "Who sees which tool: AUDIENCE, the Assistant's and the Rules Owner's lists, `offered_to` and `listing`.",
    "_mcp": "MCP over the table: BY_NAME, the JSON-RPC `Protocol` and the error shapes both clients print.",
    "threads": "Threads, messages, notes and conversations.",
    "tasks": "Tasks: create, list, show, move, done, close, asks and answers, OKs.",
    "task_work": "Work on a task beside its lifecycle (comments, links, readers, runs, worktrees, caps), tags, "
    "feedback, replays, the roadmap and the Task Manager.",
    "goals": "Goals, KPIs and proposals.",
    "strategy": "Strategy, brand, growth and lenses.",
    "market": "The market map and market spend.",
    "contacts": "Contacts.",
    "listening": "Listening and inbound mail.",
    "content": "Files, pages, cards, pins and docs.",
    "automations": "Automations.",
    "webhooks": "Webhooks, triggers, relays, notices, sleeps and releases.",
    "bots": "Bots, agents, skills and templates.",
    "admin": "People, groups, seats, settings, setup, alerts, SQL, audit, tokens, events and the raw API.",
    "computers": "Computers (list, detail, performance, turns at once, the browser exit), health, maintenance and "
    "support. Restarts, releases, this installation's update, the cloud machine and subscriptions are in "
    "computer_upkeep.",
    "tools": "Tools (the outside services bots reach through Tico), credentials, repositories and Slack.",
    "assistant": "The person's Assistant, Needs you, the brief, updates and runs.",
    "mail": "Email, physical mail and letters.",
    "calls": "Calls, meetings and the calendar.",
    "company_page": "The company page.",
    "finance": "Finance.",
    "gateway": "The model gateway and decisions.",
    "rules": "Org rules.",
}
PINNED = {
    "_registry": {
        "PROTOCOL_VERSIONS",
        "release_version",
        "SERVER_INFO",
        "INSTRUCTIONS",
        "TOOLS",
        "CLI_TOOL_ALIASES",
        "tool",
    },
    "_schemas": {
        "ASK_WAIT_MAX",
        "STATUSES",
        "GOAL_STATUSES",
        "GOAL_FILTERS",
        "PROPOSAL_KINDS",
        "_s",
        "TASK_ID",
        "DELIVERY",
        "DEFER_FOR",
        "_delivery",
        "_refs",
        "_target",
        "_key",
    },
    "_requester": {
        "APIError",
        "_Requester",
        "_as_person",
        "_asked",
        "ON_BEHALF_OF",
        "kind_of",
        "_for_person",
        "PEOPLE",
        "BOTS",
        "KINDS",
        "NOT_ASSISTANT",
        "BOTOPS",
        "REQUESTER",
        "REQUESTER_READ",
        "HUMANS_AND_ASSISTANT",
        "COUNSEL",
        "COUNSEL_BOT",
    },
    "_access": {
        "ASSISTANT_WRITES",
        "AUDIENCE",
        "COUNSEL_TOOLS",
        "ASSISTANT_HIDDEN",
        "ASSISTANT_TOOLS",
        "offered_to",
        "UNLISTED",
        "listing",
        "SHELL_ONLY",
    },
    "_mcp": {"BY_NAME", "REFUSAL_FIELDS", "error_payload", "refusal_text", "argument_error", "Protocol"},
    # Read only by remotecli/backend (never inside the table), and the loop that registers the email tools.
    "mail": {"EMAIL_ORGANIZE", "_register_email_organize"},
    "content": {"files_publish_path", "pages_publish_path"},
    "calls": {"meetings_import_file"},
    "tools": {"query_search"},
    "webhooks": {"webhook_options"},
    "assistant": {"write_fixture", "run_export_full"},
}
BASE = (
    "_registry",
    "_schemas",
    "_requester",
    "_shared",
)  # every tool module may import these; they import no tool module
LAST = ("_access", "_mcp")  # imported after every tool module: _mcp builds BY_NAME from TOOLS when it loads
# (test file, patched name) -> a facade name whose module the patch must reach: `monkeypatch.setattr(hubtools, N, v)`
# becomes `monkeypatch.setitem(hubtools.<that>.__globals__, N, v)`.
PATCH_TARGETS = {("backend/tests/test_support.py", "_as_person"): "support_file"}
# Files that list the CLI's code by path (to decide what a change reaches): each also lists the package.
PATH_LISTS = {
    "backend/tests/test_e2e_cli.py": ('"clients/remotecli.py", ', '"clients/remotecli.py", "clients/commands/", '),
    # rename.py's kept keys: a tool's input that takes a name or an id now sits in the tool modules
    "scripts/cleanup/keep.yaml": ("  clients/hubtools.py: [", "  clients/commands/*.py: ["),
}
SIDE_MODULE = re.compile(r"^hubtools_\w+$")


def _identity_module():
    spec = util.spec_from_file_location("commands_identity", Path(__file__).with_name("commands_identity.py"))
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_IDENTITY = _identity_module()
NORMALIZATIONS, SIBLINGS = _IDENTITY.NORMALIZATIONS, _IDENTITY.SIBLINGS


class Stop(Exception):
    pass


# ----------------------------------------------------------------------------- reading the source


def bound_names(n):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [n.name]
    if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = n.targets if isinstance(n, ast.Assign) else [n.target]
        out = []
        for t in targets:
            if isinstance(t, (ast.Name, ast.Tuple, ast.List)):
                out += [x.id for x in ast.walk(t) if isinstance(x, ast.Name)]
        return out
    if isinstance(n, (ast.Import, ast.ImportFrom)):
        return [a.asname or a.name.split(".")[0] for a in n.names]
    if isinstance(n, ast.For):
        return [x.id for x in ast.walk(n.target) if isinstance(x, ast.Name)]
    return []


def mutated_names(n):
    """Module names a statement changes in place: `X.update(...)`, `X[k] = v`, `X |= ...`."""
    out = set()
    for x in ast.walk(n):
        if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and isinstance(x.func.value, ast.Name):
            if x.func.attr in ("update", "add", "append", "extend", "setdefault", "discard", "pop", "remove"):
                out.add(x.func.value.id)
        if isinstance(x, (ast.Assign, ast.AugAssign)):
            for t in x.targets if isinstance(x, ast.Assign) else [x.target]:
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                    out.add(t.value.id)
    return out


def global_refs(text):
    """The module-level names `text` (one or more top-level statements) reads, from the compiler's own symbol table:
    names read at module level, and names every nested scope reads as globals."""
    table = symtable.symtable(text, "<moved>", "exec")
    top = {x for n in ast.parse(text).body for x in bound_names(n)}
    # A comprehension at module level is inlined (PEP 709): its loop variable shows as an assigned module symbol.
    out = {
        s.get_name()
        for s in table.get_symbols()
        if s.is_referenced() and not (s.is_assigned() and s.get_name() not in top)
    }

    def walk(t):
        for child in t.get_children():
            for s in child.get_symbols():
                if s.is_referenced() and (s.is_global() or s.is_declared_global()):
                    out.add(s.get_name())
            walk(child)

    walk(table)
    return out


def registered_tools(n):
    """Literal tool names the statement registers when it runs (a def registers only through its decorators)."""
    nodes = n.decorator_list if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else [n]
    names = []
    for root in nodes:
        for x in ast.walk(root):
            if isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == "tool" and x.args:
                a = x.args[0]
                names.append(a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else "<computed>")
    return names


def sibling_plan(root, side_names, strict=True):
    """{sibling module: (package module, late)}. Each clients/hubtools_<area>.py file moves whole into the package
    under its SIBLINGS name. One that imports clients.hubtools at module level (to call `hubtools.tool` and join
    AUDIENCE and BY_NAME itself) is late: the facade's tail imports it, as hubtools.py's last lines do today, since
    clients/commands/__init__.py runs before the facade has bound anything. Inside a function such an import is fine."""
    out = {}
    nouns = set(NOUN_MODULE.values()) | set(PINNED)
    facade = re.compile(
        r"^(from clients import [^\n]*\bhubtools\b|from clients\.hubtools import|import clients\.hubtools\b)", re.M
    )
    for name in sorted(side_names):
        path = root / "clients" / f"{name}.py"
        if not path.exists():
            raise Stop(f"{SOURCE} imports {name}, which is not clients/{name}.py")
        target = SIBLINGS.get(name)
        if not target:
            if strict:
                raise Stop(
                    f"clients/{name}.py is not in SIBLINGS (scripts/cleanup/commands_identity.py): name its module"
                )
            target = name.removeprefix("hubtools_")
            target = target + "_tools" if target in nouns else target
        if target in nouns:
            raise Stop(f"SIBLINGS puts {name} in {target}, a module the table's own tools already fill")
        out[name] = (target, bool(facade.search(path.read_text())))
    return out


def uses_side_module(n, side_names):
    """The statement imports or reads one of `side_names` (sibling modules, by the name they are bound to)."""
    short = {x.split(".")[-1] for x in side_names}
    if isinstance(n, ast.ImportFrom) and n.module == "clients":
        return any(a.name in short for a in n.names)
    if isinstance(n, ast.ImportFrom) and n.module:
        return n.module.split(".")[-1] in short
    if isinstance(n, ast.Import):
        return any(a.name.split(".")[-1] in short for a in n.names)
    return any(isinstance(x, ast.Name) and x.id in side_names for x in ast.walk(n)) or any(
        isinstance(x, ast.Attribute) and ast.unparse(x).startswith(tuple(f"{y}." for y in side_names if "." in y))
        for x in ast.walk(n)
    )


def tool_modules(root, strict=True):
    """tool name -> module, from its `tico` command (the first one that runs it) or else its name. A noun NOUN_MODULE
    does not name is an error, unless not `strict` (the dry-run test): it then gets a module of its own name."""
    sys.path.insert(0, str(root))
    os.environ.setdefault("TICO_VERSION", "split-commands")
    from clients import hubcli, hubtools

    first_cmd = {}

    def walk(p, words):
        subs = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]
        if not subs:
            name = "tico_" + "_".join(w.replace("-", "_") for w in words)
            for n in hubtools.CLI_TOOL_ALIASES.get(name, (name,)):
                first_cmd.setdefault(n, words)
            return
        seen = set()
        for word, sub in subs[0].choices.items():
            if id(sub) not in seen:  # aliases share one parser
                seen.add(id(sub))
                walk(sub, (*words, word))

    walk(hubcli.parser(), ())
    out = {}
    for t in hubtools.TOOLS:
        words = first_cmd.get(t["name"]) or tuple(t["name"].split("_")[1:3])
        noun = words[0]
        if noun == "task" and len(words) > 1 and words[1] in TASK_WORK_VERBS:
            out[t["name"]] = "task_work"
        elif noun in NOUN_MODULE:
            out[t["name"]] = NOUN_MODULE[noun]
        else:
            out[t["name"]] = ("?" if strict else "") + noun.replace("-", "_")
    return out


# ----------------------------------------------------------------------------- the plan


def plan(root, strict=True):
    src = (root / SOURCE).read_text()
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    module_of_tool = tool_modules(root, strict)
    body = tree.body
    doc = None
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        doc, body = body[0], body[1:]
    stmts, prev_end = [], doc.end_lineno if doc else 0
    for n in body:
        start = min([n.lineno] + [d.lineno for d in getattr(n, "decorator_list", [])])
        stmts.append(
            {
                "node": n,
                "lead": "".join(lines[prev_end : start - 1]),
                "code": "".join(lines[start - 1 : n.end_lineno]),
                "line": start,
                "names": bound_names(n),
            }
        )
        prev_end = n.end_lineno
    trailer = "".join(lines[prev_end:])
    pinned = {name: m for m, names in PINNED.items() for name in names}
    side_names = {
        a.asname or a.name
        for s in stmts
        if isinstance(s["node"], (ast.Import, ast.ImportFrom))
        for a in s["node"].names
        if SIDE_MODULE.match((a.asname or a.name).split(".")[-1]) or SIDE_MODULE.match(a.name.split(".")[-1])
    }
    siblings = sibling_plan(root, {x.split(".")[-1] for x in side_names}, strict)
    tail_names = {x for x in side_names if siblings[x.split(".")[-1]][1]}
    package_names = side_names - tail_names
    owner = {}  # name -> index of the statement binding it (first binding)
    for i, s in enumerate(stmts):
        imported = isinstance(s["node"], (ast.Import, ast.ImportFrom))
        s["uses"] = set() if imported else global_refs(s["code"]) - set(s["names"])
        for x in s["names"]:
            owner.setdefault(x, i)
    module = {}
    for i, s in enumerate(stmts):
        n = s["node"]
        regs = registered_tools(n)
        if tail_names and uses_side_module(n, tail_names):
            module[i] = "__facade__"
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            module[i] = "__import__"
        elif package_names and uses_side_module(n, package_names):
            module[i] = "_access"  # registers a sibling's tools or takes its AUDIENCE: after every tool module
        elif set(s["names"]) & set(pinned):
            module[i] = pinned[sorted(set(s["names"]) & set(pinned))[0]]
        elif isinstance(n, ast.For) and {x.id for x in ast.walk(n.iter) if isinstance(x, ast.Name)} & set(pinned):
            module[i] = pinned[sorted({x.id for x in ast.walk(n.iter) if isinstance(x, ast.Name)} & set(pinned))[0]]
        elif regs:
            if "<computed>" in regs:
                raise Stop(f"{SOURCE}:{s['line']} registers a tool under a computed name: pin it in PINNED")
            mods = {module_of_tool[r] for r in regs}
            if len(mods) != 1 or next(iter(mods)).startswith("?"):
                raise Stop(f"{SOURCE}:{s['line']} registers tools of {sorted(mods)}: add the noun to NOUN_MODULE")
            module[i] = mods.pop()
        elif not s["names"]:
            changed = mutated_names(n) & set(owner)
            if changed:
                s["follows"] = sorted(changed)
    # helpers: the module of their users, `_shared` when several use them; until nothing moves
    users_of = collections.defaultdict(set)
    for j, s in enumerate(stmts):
        for x in s["uses"]:
            if x in owner and owner[x] != j:
                users_of[owner[x]].add(j)
    for _ in range(50):
        changed = False
        for i, s in enumerate(stmts):
            if i in module and not s.get("floating"):
                continue
            if s.get("follows"):
                target = module.get(owner[s["follows"][0]])
            else:
                mods = {module[j] for j in users_of[i] if j in module and module[j] != "__facade__"}
                target = None if not mods else (mods.pop() if len(mods) == 1 else "_shared")
            if target and module.get(i) != target:
                module[i], s["floating"], changed = target, True, True
        if not changed:
            break
    for i, s in enumerate(stmts):
        rebinds = [x for x in s["names"] if owner.get(x, i) != i]
        if rebinds and i not in module or s.get("floating") and rebinds:
            module[i] = module.get(owner[rebinds[0]], "_shared")  # a name bound again stays with its first binding
    for i, s in enumerate(stmts):
        if i not in module:
            module[i] = "_shared"
            s["unused"] = True
    # What the base modules read must not live in a tool module (every tool module imports them).
    for _ in range(50):
        changed = False
        for i, s in enumerate(stmts):
            if module[i] in BASE:
                for x in s["uses"]:
                    j = owner.get(x)
                    if (
                        j is not None
                        and module[j] not in (*BASE, "__import__")
                        and not registered_tools(stmts[j]["node"])
                    ):
                        module[j], changed = "_shared", True
        if not changed:
            break
    # every binding of one name in one module
    where = collections.defaultdict(set)
    for i, s in enumerate(stmts):
        for x in s["names"]:
            if module[i] != "__import__":
                where[x].add(module[i])
    split = {x: sorted(m) for x, m in where.items() if len(m) > 1}
    if split:
        raise Stop(f"names bound in several modules: {split}")
    return {
        "doc": doc,
        "lines": lines,
        "stmts": stmts,
        "module": module,
        "owner": owner,
        "trailer": trailer,
        "src": src,
        "siblings": {k: v[0] for k, v in siblings.items()},
        "late": {v[0] for v in siblings.values() if v[1]},
    }


# ----------------------------------------------------------------------------- rendering


def moved_code(code):
    """`code` as it reads in the package: the original with NORMALIZATIONS, the only edits the move makes to it."""
    for _, _, edit in NORMALIZATIONS:
        code = edit(code)
    return code


def import_lines(node, wanted):
    """`node` (an import statement) keeping only the names in `wanted`, or None."""
    names = [a for a in node.names if (a.asname or a.name.split(".")[0]) in wanted]
    if not names:
        return None
    if isinstance(node, ast.Import):
        return "\n".join(ast.unparse(ast.Import(names=[a])) for a in names)
    return moved_code(ast.unparse(ast.ImportFrom(module=node.module, names=names, level=node.level)))


def order_of(p):
    first = {}
    for i in range(len(p["stmts"])):
        first.setdefault(p["module"][i], i)
    mods = [m for m in sorted(first, key=first.get) if not m.startswith("__")]
    mods += [m for m in p["siblings"].values() if m not in mods and m not in p["late"]]
    return [m for m in mods if m not in LAST] + [m for m in LAST if m in mods]


def module_doc(m):
    return MODULE_DOCS.get(m) or f"The `{m}` tools."


def render(p, root):
    stmts, module, owner = p["stmts"], p["module"], p["owner"]
    mods = order_of(p)
    by_module = collections.defaultdict(list)
    for i in range(len(stmts)):
        by_module[module[i]].append(i)
    out, edges = {}, collections.defaultdict(set)
    builtin = set(dir(builtins))
    moved_whole = set(p["siblings"].values())
    for name, m in p["siblings"].items():
        out[f"clients/{name}.py"] = None  # moves whole, normalized like the rest
        out[f"{PACKAGE}/{m}.py"] = moved_code((root / "clients" / f"{name}.py").read_text())
    for m in mods:
        if m in moved_whole:
            continue
        idx = by_module[m]
        used = set().union(*(stmts[i]["uses"] for i in idx)) - {x for i in idx for x in stmts[i]["names"]}
        std, cross = [], collections.defaultdict(list)
        for x in sorted(used):
            j = owner.get(x)
            if j is None:
                if x not in builtin and x != "__file__":
                    raise Stop(f"{m}: {x} is read but bound nowhere in {SOURCE}")
                continue
            if module[j] == "__import__":
                std.append(x)
            elif module[j] == "__facade__":
                raise Stop(f"{m}: reads {x}, which stays in the facade")
            else:
                cross[module[j]].append(x)
                edges[m].add(module[j])
        head = [
            f'"""{module_doc(m)}\n\nMoved from clients/hubtools.py; clients/commands/__init__.py says how the table is put '
            'together."""'
        ]
        imports = []
        for k, s in enumerate(stmts):
            if module[k] == "__import__":
                line = import_lines(s["node"], set(std))
                if line:
                    imports.append(line)
        if imports:
            head.append("\n".join(imports))
        if cross:
            head.append(
                "\n".join(
                    f"from clients.commands.{o} import {', '.join(sorted(cross[o]))}"
                    for o in sorted(cross, key=lambda o: mods.index(o) if o in mods else -1)
                )
            )
        text = "\n\n".join(head) + "\n"
        for i in idx:
            lead = stmts[i]["lead"].lstrip("\n")
            text += "\n\n" + lead + moved_code(stmts[i]["code"])
        out[f"{PACKAGE}/{m}.py"] = text
    cycles = find_cycles(edges)
    if cycles:
        raise Stop(f"import cycles: {cycles}")
    base_out = sorted(f"{m} -> {d}" for m in BASE for d in edges.get(m, ()) if d not in BASE)
    if base_out:
        raise Stop(f"base modules import tool modules: {base_out}")
    mcp_users = sorted(m for m, deps in edges.items() if "_mcp" in deps)
    if mcp_users:
        raise Stop(f"{mcp_users} import _mcp, which builds BY_NAME from TOOLS when it loads: it must load last")
    out[f"{PACKAGE}/__init__.py"] = render_init(mods)
    out[SOURCE] = render_facade(p, root, mods)
    return out, edges


def find_cycles(edges):
    out, stack, on, seen = [], [], set(), set()

    def dfs(u):
        seen.add(u)
        stack.append(u)
        on.add(u)
        for v in sorted(edges.get(u, ())):
            if v in on:
                out.append(stack[stack.index(v) :] + [v])
            elif v not in seen:
                dfs(v)
        stack.pop()
        on.discard(u)

    for u in sorted(edges):
        if u not in seen:
            dfs(u)
    return out


def render_init(mods):
    lines = ",\n    ".join(mods)
    return f'''"""Tico's tool table, one module per noun. clients/hubtools.py re-exports it for the files that import it there.

Importing any module here runs this file first, which loads them all in one order, so the table is always whole:
  1. the base modules: `_registry` (TOOLS and `tool()`), `_schemas`, `_requester`, `_shared`; they import no tool module
  2. the tool modules, one per noun: each registers its tools with `_registry.tool` as it loads
  3. `_access` (who sees which tool), then `_mcp`, which builds BY_NAME from TOOLS as it loads, and the Protocol
A module that imports clients.hubtools itself to join the table (`hubtools.tool`, then AUDIENCE and BY_NAME) is not
listed here: clients/hubtools.py imports it at its end, once every name it reads there is bound.
A new tool goes in its noun's module; a new noun gets a module and a line below, before `_access`. No module imports
`_mcp`; the tool modules import only the base modules.
"""

from clients.commands import (
    {lines},
)

MODULES = ({", ".join(mods)})
'''


def outside_names(root):
    """Every name other files read from clients.hubtools: `from clients.hubtools import X`, `alias.X` for any alias
    bound to the module, and `getattr`/`setattr`/`hasattr`/`monkeypatch.setattr`/`patch.object(alias, "X", ...)`."""
    files = subprocess.run(["git", "-C", str(root), "ls-files", "*.py"], capture_output=True, text=True, check=True)
    names = set()
    for f in files.stdout.split():
        if f == SOURCE or f.startswith(PACKAGE + "/"):
            continue
        try:
            text = (root / f).read_text()
            if "hubtools" not in text:
                continue
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        # Code held in a string (a script a test or eval writes out and runs) reads the facade too.
        trees = [tree, *embedded_code(tree)]
        for tree in trees:
            aliases = set()
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom) and n.module == "clients.hubtools":
                    names.update(a.name for a in n.names if a.name != "*")
                elif isinstance(n, ast.ImportFrom) and n.module == "clients":
                    aliases.update(a.asname or a.name for a in n.names if a.name == "hubtools")
                elif isinstance(n, ast.ImportFrom) and n.module is None and n.level:
                    aliases.update(a.asname or a.name for a in n.names if a.name == "hubtools")
                elif isinstance(n, ast.Import):
                    for a in n.names:
                        if a.name == "hubtools":
                            aliases.add(a.asname or "hubtools")
                        elif a.name == "clients.hubtools" and a.asname:
                            aliases.add(a.asname)
            for n in ast.walk(tree):
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in aliases:
                    names.add(n.attr)
                elif (
                    isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Attribute)
                    and ast.unparse(n.value) == "clients.hubtools"
                ):
                    names.add(n.attr)
                elif isinstance(n, ast.Call) and len(n.args) >= 2 and _is_module(n.args[0], aliases):
                    func = ast.unparse(n.func)
                    if func.split(".")[-1] in ("getattr", "setattr", "hasattr", "delattr", "object"):
                        key = n.args[1]
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            names.add(key.value)
                        elif f != BEHAVIOUR_TEST:  # it walks `__all__`
                            raise Stop(f"{f}:{n.lineno} reads clients.hubtools by a computed name: name it")
                elif (
                    isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith("clients.hubtools.")
                ):
                    names.add(n.value.split(".")[2])
    return names


def embedded_code(tree):
    """The Python programs held in `tree`'s string constants that import clients.hubtools."""
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "import hubtools" in n.value:
            try:
                yield ast.parse(n.value)
            except SyntaxError:
                continue


def _is_module(node, aliases):
    return (isinstance(node, ast.Name) and node.id in aliases) or ast.unparse(node) == "clients.hubtools"


def render_facade(p, root, mods):
    stmts, module, owner = p["stmts"], p["module"], p["owner"]
    tail = [i for i in range(len(stmts)) if module[i] == "__facade__"]
    wanted = outside_names(root).union(*(stmts[i]["uses"] for i in tail))
    by_mod, imported = collections.defaultdict(list), collections.defaultdict(list)
    for x in sorted(wanted):
        j = owner.get(x)
        if j is None and x and not x.startswith("__"):
            raise Stop(f"another file reads clients.hubtools.{x}, which clients/hubtools.py does not bind")
        if j is None or module[j] == "__facade__":
            continue  # a module attribute (`__file__`), or bound by the tail itself
        if module[j] == "__import__":
            imported[j].append(x)  # a library name another file reads here (`hubtools.APIError`): imported again
            continue
        by_mod[module[j]].append(x)
    exported = sorted([x for names in by_mod.values() for x in names] + [x for xs in imported.values() for x in xs])
    doc = ast.get_docstring(ast.parse(p["src"])) or ""
    text = (
        '"""'
        + doc.split("\n\n")[0]
        + "\n\nThe table lives in clients/commands/, one module per noun (its __init__ says how it is put together). This"
        "\nmodule re-exports, in `__all__`, every name another file reads here: the same objects, so a change to"
        "\n`hubtools.TOOLS` or `hubtools.AUDIENCE` is a change to the table. A new name read through here goes in"
        "\n`__all__`; code that patches a function a moved module calls patches it in that module.\n" + '"""\n\n'
    )
    for j in sorted(imported):
        node = ast.parse(stmts[j]["code"]).body[0]
        node.names = [a for a in node.names if (a.asname or a.name).split(".")[0] in imported[j]]
        text += ast.unparse(node) + "\n"
    for m in mods:
        if by_mod.get(m):
            text += f"from clients.commands.{m} import {', '.join(sorted(by_mod[m]))}\n"
    text += "\n__all__ = [\n" + "".join(f'    "{x}",\n' for x in exported) + "]\n"
    for i in tail:  # late modules: they import the facade, so it imports them once it is whole (N3 names them)
        lead = stmts[i]["lead"].lstrip("\n")
        text += "\n\n" + _IDENTITY.sibling_imports(lead + stmts[i]["code"])
    text += p["trailer"] if p["trailer"].strip() else ""
    return text


def patch_rewrites(root, rendered, strict=True):
    """Tests that patch, on the facade, a name moved code reads as a global: such a patch would no longer reach it.
    `monkeypatch.setattr(hubtools, "N", v)` becomes `monkeypatch.setitem(hubtools.<PATCH_TARGETS>.__globals__, "N", v)`.
    """
    moved_globals = set()
    for path, text in rendered.items():
        if text is not None and path.startswith(PACKAGE + "/") and not path.endswith("__init__.py"):
            moved_globals |= global_refs(text)
    files = subprocess.run(["git", "-C", str(root), "ls-files", "*.py"], capture_output=True, text=True, check=True)
    edits, missing = {}, []
    for f in files.stdout.split():
        if "tests/" not in f or f.startswith(PACKAGE + "/"):
            continue
        text = (root / f).read_text()
        if "hubtools" not in text:
            continue
        tree = ast.parse(text)
        starts = [0]
        for line in text.splitlines(keepends=True):
            starts.append(starts[-1] + len(line))
        spans = []
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and ast.unparse(n.func) == "monkeypatch.setattr" and len(n.args) >= 2):
                continue
            target, key = n.args[0], n.args[1]
            if not (isinstance(target, ast.Name) and target.id == "hubtools" and isinstance(key, ast.Constant)):
                continue
            if key.value not in moved_globals:
                continue  # read through the facade (`hubtools.N`): the facade's patch reaches it
            via = PATCH_TARGETS.get((f, key.value))
            if not via:
                missing.append(f"{f}:{n.lineno} patches hubtools.{key.value}")
                continue
            begin = starts[n.lineno - 1] + n.col_offset
            end = starts[key.end_lineno - 1] + key.end_col_offset
            spans.append((begin, end, f"monkeypatch.setitem(hubtools.{via}.__globals__, {ast.unparse(key)}"))
        new = text
        for begin, end, repl in sorted(spans, reverse=True):
            new = new[:begin] + repl + new[end:]
        if new != text:
            edits[f] = new
    if missing and strict:
        raise Stop(
            "moved code reads these as globals, so a patch on the facade misses them; add each to "
            "PATCH_TARGETS:\n" + "\n".join(missing)
        )
    for f in files.stdout.split():  # N3 outside the package: importers of a sibling module that moves
        if f in rendered or f.startswith((PACKAGE + "/", "scripts/cleanup/")):
            continue
        text = edits.get(f) or (root / f).read_text()
        new = _IDENTITY.sibling_imports(text) if any(name in text for name in SIBLINGS) else text
        if new != text:
            edits[f] = new
    for f, (old, new) in PATH_LISTS.items():
        text = edits.get(f) or (root / f).read_text()
        if new not in text:
            if old not in text:
                raise Stop(f"{f} no longer lists {old.strip()}: update PATH_LISTS")
            edits[f] = text.replace(old, new, 1)
    return edits


# ----------------------------------------------------------------------------- verifying


def check_modules(p, rendered, strict=True):
    """c (the ASTs) and e (names bound, imports used, sizes). Not strict, a sibling module that moves whole may be over
    the cap: it was before the move, and the dry-run test must not fail the change that grew it."""
    problems = []
    originals = collections.defaultdict(list)
    for i, s in enumerate(p["stmts"]):
        if p["module"][i] not in ("__import__",):
            originals[p["module"][i]].append(ast.dump(ast.parse(moved_code(s["code"])).body[0]))
    builtin = set(dir(builtins))
    for path, text in rendered.items():
        if text is None or not path.startswith(PACKAGE + "/") or path.endswith("__init__.py"):
            continue
        m = Path(path).stem
        tree = ast.parse(text)
        body = [n for n in tree.body[1:] if not isinstance(n, (ast.Import, ast.ImportFrom))]
        if text.count("\n") > MAX_LINES and (strict or m not in p["siblings"].values()):
            problems.append(f"{path}: {text.count(chr(10))} lines, over the cap of {MAX_LINES}")
        if m in p["siblings"].values():
            continue  # moved whole: moved_code() of the file is all that changed, its imports are its own
        if [ast.dump(n) for n in body] != originals[m]:
            problems.append(f"{path}: its statements are not the original ones")
        bound = {x for n in tree.body for x in bound_names(n)}
        refs = global_refs(text)
        missing = sorted(refs - bound - builtin - {"__file__", "__name__", "__doc__"})
        if missing:
            problems.append(f"{path}: reads names it never binds: {missing}")
        imported = {x for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for x in bound_names(n)}
        unused = sorted(imported - refs)
        if unused:
            problems.append(f"{path}: imports it never reads: {unused}")
    return problems


def scratch_tree(root, files):
    """A copy of `root` that shares every file except `files` (links for the rest), with a VERSION file."""
    tmp = Path(tempfile.mkdtemp(prefix="split-commands-"))
    files = {**files, "VERSION": VERSION_FILE + "\n"}  # a path whose text is None is left out (the move deletes it)
    real_dirs = {str(Path(c).parent) for c in files}
    real_dirs |= {str(parent) for d in list(real_dirs) for parent in Path(d).parents}
    real_dirs.discard(".")

    def fill(rel):
        src_dir = root / rel if rel else root
        dst_dir = tmp / rel if rel else tmp
        dst_dir.mkdir(parents=True, exist_ok=True)
        for entry in src_dir.iterdir():
            r = f"{rel}/{entry.name}" if rel else entry.name
            if entry.name in ("__pycache__", ".git", ".pytest_cache"):
                continue
            if r in real_dirs:
                fill(r)
            elif r not in files:
                (dst_dir / entry.name).symlink_to(entry)

    fill("")
    for path, text in files.items():
        if text is not None:
            (tmp / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp / path).write_text(text)
    # `git ls-files` (the dry-run test, the import map) reads the tree's own repository.
    if (root / ".git").exists():
        (tmp / ".git").symlink_to(root / ".git")
    return tmp


def _python(tree, args, env_extra=None, drop=()):
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", **(env_extra or {})})
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, env=env, cwd=tree, check=False)


def identities(trees):
    """commands_identity.py on each tree, TICO_VERSION unset and set, in parallel: {tree: merged JSON}."""
    script = str(Path(__file__).with_name("commands_identity.py"))
    jobs = []
    for tree in trees:
        jobs.append((tree, "unset", [script, "--root", str(tree)], {}, ("TICO_VERSION",)))
        jobs.append(
            (tree, "set", [script, "--root", str(tree), "--only", "versions"], {"TICO_VERSION": "v8.8.8-env"}, ())
        )

    def one(job):
        tree, label, args, extra, drop = job
        result = _python(tree, args, extra, drop)
        if result.returncode:
            raise Stop(f"commands_identity.py ({label}) failed on {tree}:\n{result.stderr[-3000:]}")
        return tree, label, json.loads(result.stdout)

    out = collections.defaultdict(dict)
    with ThreadPoolExecutor(len(jobs)) as pool:
        for tree, label, data in pool.map(one, jobs):
            if label == "set":
                out[tree]["versions set"] = data["versions"]
            else:
                out[tree].update(data)
    return out


def compare(before, after):
    problems = []
    for key in ("tools", "by_name", "registry", "parser", "versions", "versions set"):
        if before[key] != after[key]:
            problems.append(f"{key} differs: {before[key]} -> {after[key]}")
    for group in ("handlers", "listings", "tables", "imports"):
        for k in sorted(set(before[group]) | set(after[group])):
            if before[group].get(k) != after[group].get(k):
                problems.append(f"{group} {k}: {before[group].get(k)} -> {after[group].get(k)}")
    if not after["by_name"] or set(after["imports"].values()) != {"ok"}:
        problems.append(f"the moved table does not load whole: by_name {after['by_name']}, imports {after['imports']}")
    for label in ("versions", "versions set"):
        seen = after[label]
        if {seen["release_version"], seen["initialize"]["version"], seen["tico --version"]} != {
            (seen["TICO_VERSION"] or VERSION_FILE).removeprefix("v")
        }:
            problems.append(f"{label}: the version is not the one stamped: {seen}")
    return problems


def behaviour(tree):
    """g: the package's own test file on the moved copy."""
    result = _python(
        tree, ["-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", "-n", "0", BEHAVIOUR_TEST]
    )
    if result.returncode:
        raise Stop(f"{BEHAVIOUR_TEST} fails on the moved copy {tree}:\n{result.stdout[-4000:]}{result.stderr[-2000:]}")


# ----------------------------------------------------------------------------- main


def summary(p, rendered):
    tools = collections.Counter()
    for i, s in enumerate(p["stmts"]):
        tools[p["module"][i]] += len([r for r in registered_tools(s["node"]) if r != "<computed>"])
    return {
        path: {"lines": text.count("\n"), "tools": tools.get(Path(path).stem, 0)}
        for path, text in sorted(rendered.items())
        if text is not None
    }


def run(root, write=False, plan_out=None, keep=False, strict=True, tests=True):
    """The move, verified; `write` puts it in `root`. Not `strict` (the dry-run test): a noun NOUN_MODULE lacks gets a
    module of its own name, and a patch PATCH_TARGETS lacks is left as it is, so another change does not fail here."""
    root = Path(root).resolve()
    if not (root / SOURCE).exists() or (root / PACKAGE / "__init__.py").exists():
        raise Stop(f"nothing to move: {SOURCE} is already the facade over {PACKAGE}")
    p = plan(root, strict)
    rendered, edges = render(p, root)
    edits = patch_rewrites(root, rendered, strict)
    problems = check_modules(p, rendered, strict)
    if problems:
        raise Stop("\n".join(problems))
    before = scratch_tree(root, {SOURCE: p["src"]})  # a real copy: release_version reads VERSION beside its own file
    after = scratch_tree(root, {**rendered, **edits})
    try:
        found = identities((before, after))
        problems = compare(found[before], found[after])
        if problems:
            raise Stop("the moved table differs:\n" + "\n".join(problems))
        if tests and (root / BEHAVIOUR_TEST).exists():
            behaviour(after)
    finally:
        if not keep:
            shutil.rmtree(before, ignore_errors=True)
            shutil.rmtree(after, ignore_errors=True)
    report = {
        "modules": summary(p, rendered),
        "imports": {m: sorted(d) for m, d in sorted(edges.items())},
        "edits": sorted(edits),
        "normalizations": [f"{key}: {what}" for key, what, _ in NORMALIZATIONS],
        "identity": found[after],
        "scratch": [str(before), str(after)] if keep else [],
    }
    if plan_out:
        report["statements"] = [
            {"line": s["line"], "names": s["names"], "module": p["module"][i]} for i, s in enumerate(p["stmts"])
        ]
        Path(plan_out).write_text(json.dumps(report, indent=1) + "\n")
    if write:
        for path, text in {**rendered, **edits}.items():
            if text is None:
                (root / path).unlink()
                continue
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--write", action="store_true", help="write the package into the tree after the checks pass")
    ap.add_argument("--plan", help="write which statement went where, and the checks' results, to this JSON file")
    ap.add_argument("--keep", action="store_true", help="keep the scratch copy (its path is printed)")
    opts = ap.parse_args()
    try:
        report = run(opts.root, write=opts.write, plan_out=opts.plan, keep=opts.keep)
    except Stop as exc:
        print(f"split_commands: {exc}", file=sys.stderr)
        return 1
    for path, row in report["modules"].items():
        print(f"{path:40} {row['lines']:6} lines {row['tools']:4} tools")
    print("edits outside the package:", ", ".join(report["edits"]) or "none")
    print("normalizations:", "; ".join(report["normalizations"]))
    if report["scratch"]:
        print("scratch copies (before, after):", " ".join(report["scratch"]))
    print("checks a-g passed:", report["identity"]["tools"], "tools, registry", report["identity"]["registry"][:12])
    if opts.write:
        edited = " ".join(e for e in report["edits"] if e.endswith(".py"))
        print(
            f"written; now: uvx ruff@0.15.9 format clients/commands clients/hubtools.py {edited} && "
            "python3 scripts/lint_baseline.py --write"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
