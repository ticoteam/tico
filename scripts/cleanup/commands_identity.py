"""What the tool table looks like from outside, as hashes: two trees that print the same JSON serve the same tools.

    python3 scripts/cleanup/commands_identity.py --root <tree>
    python3 scripts/cleanup/commands_identity.py --root <tree> --only versions

Used by scripts/cleanup/split_commands.py to show that moving clients/hubtools.py into clients/commands/ changed
nothing a caller can see. Run it in a fresh process per tree (and per TICO_VERSION): it imports that tree's `clients`.

  registry   every tool: name, description, inputSchema, writes, local, the kinds it is offered to, and its handler:
             qualified name, source (as an AST, after NORMALIZATIONS on the unmoved tree), defaults and closure values
  listings   `tools/list` bytes for both transports (local False/True) and every kind of caller, plus no kind
  parser     the `tico` command tree: every command's words, arguments, flags, defaults, choices and help
  tables     AUDIENCE, SHELL_ONLY, UNLISTED, CLI_TOOL_ALIASES, the Assistant's and Counsel's lists
  imports    the modules that import the table load (clients.hubmcp, clients.remotecli, backend.mcp, ...)
  versions   release_version(), SERVER_INFO, MCP initialize's serverInfo and `tico --version`, under this process's
             TICO_VERSION (split_commands runs it with the variable unset and set)
"""

import argparse
import ast
import contextlib
import functools
import hashlib
import importlib
import inspect
import io
import json
import os
import re
import sys
import textwrap

IMPORTERS = (
    "clients.hubmcp",
    "clients.remotecli",
    "clients.hubcli",
    "clients.agent_skill",
    "backend.mcp",
    "backend.counsel",
    "backend.integrations",
    "backend.settings_catalog",
    "backend.decide_readout",
    "backend.outcome_stats",
)
TABLES = (
    "AUDIENCE",
    "SHELL_ONLY",
    "UNLISTED",
    "CLI_TOOL_ALIASES",
    "ASSISTANT_TOOLS",
    "ASSISTANT_WRITES",
    "ASSISTANT_HIDDEN",
    "COUNSEL_TOOLS",
)

# The only edits the move makes to code it copies; check (c) and the handler hashes allow these and nothing else.
_PARENTS = re.compile(r"(Path\(__file__\)\.resolve\(\)\.parents\[)(\d+)(\])")
_RELATIVE = re.compile(r"\bfrom \.(\w+(?:\.\w+)*)? import ")
NORMALIZATIONS = (
    (
        "N1",
        "`Path(__file__).resolve().parents[N]` becomes parents[N + 1]: the code sits one folder deeper",
        lambda code: _PARENTS.sub(lambda m: f"{m.group(1)}{int(m.group(2)) + 1}{m.group(3)}", code),
    ),
    (
        "N2",
        "`from . import X` / `from .x import Y` becomes `from clients import X` / `from clients.x import Y`: the code is"
        " no longer in the clients package itself",
        lambda code: _RELATIVE.sub(lambda m: f"from clients{'.' + m.group(1) if m.group(1) else ''} import ", code),
    ),
    (
        "N3",
        "an import of a clients/hubtools_<area>.py module names its place in the package (SIBLINGS) and binds the same"
        " name (`from clients import hubtools_org` becomes `from clients.commands import org as hubtools_org`), and"
        " the path clients/hubtools_org.py in a comment reads clients/commands/org.py",
        lambda code: sibling_imports(code),
    ),
)


# clients/hubtools_<area>.py modules move whole into the package, under these names.
SIBLINGS = {
    "hubtools_org": "org",
    "hubtools_pipelines": "pipelines",
    "hubtools_connections": "connections",
    "hubtools_computers": "machines",
}


def sibling_imports(code):
    """`code` with each import of a sibling module naming its new place, the name it binds unchanged:
    `from clients import hubtools_org` -> `from clients.commands import org as hubtools_org`,
    `from clients.hubtools_org import X` -> `from clients.commands.org import X`."""
    if not any(name in code for name in SIBLINGS):
        return code
    for name, module in SIBLINGS.items():  # where comments and docstrings point, and dotted references
        code = code.replace(f"clients/{name}.py", f"clients/commands/{module}.py")
        code = re.sub(rf"\bclients\.{name}\b", f"clients.commands.{module}", code)
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    lines = code.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    spans = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.ImportFrom) or n.level or not n.module:
            continue
        head, _, rest = n.module.partition(".")
        names = ", ".join(f"{a.name} as {a.asname}" if a.asname else a.name for a in n.names)
        if head == "clients" and rest.split(".")[0] in SIBLINGS:
            tail = rest.split(".", 1)[1:]
            module = ".".join(["clients.commands", SIBLINGS[rest.split(".")[0]], *tail])
            text = f"from {module} import {names}"
        elif n.module == "clients" and any(a.name in SIBLINGS for a in n.names):
            keep = [a for a in n.names if a.name not in SIBLINGS]
            parts = [f"from clients import {', '.join(a.name + (' as ' + a.asname if a.asname else '') for a in keep)}"]
            parts = parts if keep else []
            parts += [
                f"from clients.commands import {SIBLINGS[a.name]} as {a.asname or a.name}"
                for a in n.names
                if a.name in SIBLINGS
            ]
            text = ("\n" + " " * n.col_offset).join(parts)
        else:
            continue
        begin = starts[n.lineno - 1] + n.col_offset
        spans.append((begin, starts[n.end_lineno - 1] + n.end_col_offset, text))
    for begin, end, text in sorted(spans, reverse=True):
        code = code[:begin] + text + code[end:]
    return code


def normalize(code):
    for _, _, edit in NORMALIZATIONS:
        code = edit(code)
    return code


def plain(value):
    """`value` with every set sorted and every callable named, so the hash depends on neither the process's string
    hashing nor addresses."""
    if isinstance(value, (set, frozenset)):
        return sorted((plain(v) for v in value), key=repr)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if callable(value):
        return getattr(value, "__qualname__", None) or getattr(value, "__name__", None) or type(value).__name__
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return type(value).__name__


def sha(value):
    return hashlib.sha256(json.dumps(plain(value), default=repr).encode()).hexdigest()


def handler(fn, moved):
    """A tool's function as callers meet it: name, source, defaults and the values it closes over."""
    out = {}
    if isinstance(fn, functools.partial):
        out["partial"] = plain([fn.args, fn.keywords])
        fn = fn.func
    out["qualname"] = getattr(fn, "__qualname__", None)
    try:
        src = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        src = None
    if src is not None:
        src = src if moved else normalize(src)
        try:
            out["source"] = sha(ast.dump(ast.parse(src)))
        except SyntaxError:  # a lambda inside a longer statement: its lines as they are
            out["source"] = sha(src)
    code = getattr(fn, "__code__", None)
    if code is not None:
        cells = [c.cell_contents for c in fn.__closure__ or ()]
        out["closure"] = plain(dict(zip(code.co_freevars, cells, strict=True)))
        out["defaults"] = plain([fn.__defaults__, fn.__kwdefaults__])
    return out


def parser_tree(hubcli):
    rows = []

    def walk(p, words):
        for action in p._actions:
            if isinstance(action, argparse._SubParsersAction):
                for word, sub in action.choices.items():
                    walk(sub, (*words, word))
                continue
            rows.append(
                [
                    " ".join(words),
                    action.dest,
                    list(action.option_strings),
                    repr(action.default),
                    repr(action.nargs),
                    repr(action.choices),
                    action.help,
                    action.required,
                    type(action).__name__,
                    repr(action.const),
                ]
            )
        rows.append([" ".join(words), "<defaults>", repr(sorted(p._defaults.items(), key=str))])

    walk(hubcli.parser(), ())
    return rows


def versions(hubtools, hubcli):
    init = hubtools.Protocol(None).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
    )
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed), contextlib.suppress(SystemExit):
        hubcli.parser().parse_args(["--version"])
    return {
        "TICO_VERSION": os.environ.get("TICO_VERSION"),
        "release_version": hubtools.release_version(),
        "SERVER_INFO": hubtools.SERVER_INFO,
        "initialize": init["result"]["serverInfo"],
        "tico --version": printed.getvalue().strip(),
    }


def identity(root, only=None):
    sys.path.insert(0, root)
    from clients import hubcli, hubtools

    if only == "versions":
        return {"versions": versions(hubtools, hubcli)}
    moved = os.path.isdir(os.path.join(root, "clients", "commands"))
    tools = sorted(hubtools.TOOLS, key=lambda t: t["name"])
    registry = [
        {
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["inputSchema"],
            "writes": t.get("writes"),
            "local": t.get("local"),
            "offered_to": sorted(hubtools.offered_to(t)),
            "handler": handler(t.get("fn"), moved),
            "bound": hubtools.BY_NAME.get(t["name"]) is t,
        }
        for t in tools
    ]
    listings = {}
    for local in (False, True):
        for kind in (None, *sorted(hubtools.KINDS)):
            body = json.dumps({"tools": hubtools.listing(local, kind)}, default=str)
            listings[f"{'stdio' if local else 'http'}:{kind or 'unknown'}"] = hashlib.sha256(body.encode()).hexdigest()
    holders = [hubtools]
    if moved:
        from clients import commands

        holders += list(commands.MODULES)  # a table nothing reads through the facade is not re-exported there
    tables = {
        name: sha(next(getattr(h, name) for h in holders if hasattr(h, name)))
        for name in TABLES
        if any(hasattr(h, name) for h in holders)
    }
    imports = {}
    for name in IMPORTERS:
        try:
            importlib.import_module(name)
            imports[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - reported, the comparison fails on it
            imports[name] = f"{type(exc).__name__}: {exc}"
    return {
        "tools": len(tools),
        "by_name": sorted(hubtools.BY_NAME) == [t["name"] for t in tools],
        "registry": sha(registry),
        "handlers": {t["name"]: sha(r["handler"]) for t, r in zip(tools, registry, strict=True)},
        "listings": listings,
        "parser": sha(parser_tree(hubcli)),
        "tables": tables,
        "imports": imports,
        "versions": versions(hubtools, hubcli),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="the tree whose clients package to read")
    ap.add_argument("--only", choices=("versions",), help="print only this part")
    opts = ap.parse_args()
    print(json.dumps(identity(os.path.abspath(opts.root), opts.only), indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
