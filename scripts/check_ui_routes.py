#!/usr/bin/env python3
"""Every API path the web UI calls must be a route the server serves.

The server's routes come from the app itself: backend/app.py's create_app, built with a temporary database. No server,
network or sign-in is involved. The UI's calls come from ui/**/*.js and ui/**/*.ts, excluding ui/vendor/, the built
ui/assets/ bundle and .d.ts declarations. A call is a helper that sends a request (get, post, put, patch, v2Get,
writeRequest, cloudCompose, fetch, formFetch, EventSource, WebSocket, and ui/support.js's call), or a string that
names a path under /api. Its path is read from string and template literals, `+` joins, `?:`, and one-expression
functions and constants. A name is read only when the UI declares it once and never takes it as a parameter, so a
local variable is not mistaken for a constant elsewhere. Each part of a path that is not known statically is a
run-time value.

A run-time value that fills a whole segment, as in /api/issues/${n}, matches one route segment, a literal or a
parameter. A value inside a segment, as in /api/tasks${query}, matches that segment with any text in its place. A
route's {name} matches one segment and {name:path} the rest. A call with a known method matches a route that serves
that method; a call whose method is not known matches on the path alone. A query string is not part of the path.

Unmatched calls are counted per file against ui/routes-baseline.json, as scripts/ui_baseline.py counts tsc and Biome
findings: a file may not exceed its count, and a count may only shrink. Fix the call, or delete the dead code, then
`--write` lowers the baseline.

    python3 scripts/check_ui_routes.py --check [--base origin/main]   fail on new unmatched calls or stale counts
    python3 scripts/check_ui_routes.py --write                        lower the baseline to the current counts
    python3 scripts/check_ui_routes.py --skipped                      list the calls whose path cannot be read

Exit status: 0 when the UI's calls agree with the baseline, 1 otherwise.
"""

import argparse
import bisect
import collections
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ui_baseline  # noqa: E402  (the baseline file format, shared with the tsc and Biome ratchets)

BASELINE_REL = "ui/routes-baseline.json"
EXCLUDED = ("ui/vendor/", "ui/assets/")  # third-party code, and the bundle built from it
WILD = None  # a part of a path that is a run-time value
MAX_ALTERNATIVES = 64
MAX_DEPTH = 24

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
# Helpers that take a path relative to /api (ui/app/api.js), and the method they send.
RELATIVE_HELPERS = {
    "get": "GET",
    "v2Get": "GET",
    "getRaw": "GET",
    "getPrefetch": "GET",
    "post": "POST",
    "postRequest": "POST",
    "put": "PUT",
    "putRequest": "PUT",
    "patch": "PATCH",
    "patchRequest": "PATCH",
    "cloudCompose": "POST",
}
# Helpers that take a full URL. `fetch` takes its method from its options.
ABSOLUTE_HELPERS = {"formFetch": "POST", "EventSource": "GET", "WebSocket": "WS"}
NEEDS_NEW = ("EventSource", "WebSocket")
HELPER_NAMES = set(RELATIVE_HELPERS) | set(ABSOLUTE_HELPERS) | {"writeRequest", "fetch"}
# ui/support.js's own `call(method, path)`, which fetches API_BASE() + path (that is /api/v2).
LOCAL_WRAPPERS = {("ui/support.js", "call"): "/api/v2"}
NOT_A_PARAMETER_LIST = ("if", "for", "while", "switch", "with")


def fail(message):
    sys.exit(message)


# --- the server's route table -----------------------------------------------------------------------------------

PARAM = re.compile(r"\{(\w+)(?::(\w+))?\}")


def route_segment(text):
    """('lit', text), ('path', None) for {name:path}, or ('param', regex) for a segment with a parameter."""
    if text == "":
        return ("lit", "")
    m = PARAM.fullmatch(text)
    if m and m.group(2) == "path":
        return ("path", None)
    if not PARAM.search(text):
        return ("lit", text)
    out, last = [], 0
    for m in PARAM.finditer(text):
        out.append(re.escape(text[last : m.start()]))
        out.append(".+" if m.group(2) == "path" else "[^/]+")
        last = m.end()
    out.append(re.escape(text[last:]))
    return ("param", re.compile("".join(out)))


def without_trailing_slash(path):
    return path.rstrip("/") or "/"


class Served:
    """One path the server answers: its methods, or the directory of a static mount."""

    def __init__(self, template, methods, directory=None):
        self.template = template
        self.methods = methods
        self.directory = directory
        self.segments = None if directory else [route_segment(s) for s in template.split("/")]


def served_routes():
    try:
        from fastapi.routing import APIRoute, APIWebSocketRoute
        from starlette.routing import Mount
        from starlette.staticfiles import StaticFiles

        from backend.app import create_app
        from backend.config import Settings
    except ImportError as exc:
        fail(f"cannot import the app ({exc}); install backend/requirements-dev.txt in a virtualenv first")

    def walk(app_routes, prefix, out):
        for route in app_routes:
            if isinstance(route, Mount):
                inner = route.app
                if isinstance(inner, StaticFiles):
                    out.append(Served(without_trailing_slash(prefix + route.path), {"GET"}, Path(inner.directory)))
                elif hasattr(inner, "routes"):
                    walk(inner.routes, prefix + route.path, out)
                continue
            if isinstance(route, APIWebSocketRoute):
                methods = {"WS"}
            elif isinstance(route, APIRoute):
                methods = {m for m in route.methods if m not in ("HEAD", "OPTIONS")}
            else:
                continue
            if methods:
                out.append(Served(without_trailing_slash(prefix + route.path), methods))

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "registry").mkdir()
        app = create_app(Settings(db_path=base / "hub.db", registry_dir=base / "registry"))
        out = []
        walk(app.routes, "", out)
    return out


def leading_key(segments):
    """The first three segments as text when all three are literal. The routes are grouped by it."""
    if len(segments) < 3 or any(s[0] != "lit" for s in segments[:3]):
        return None
    return tuple(s[1] for s in segments[:3])


class RouteIndex:
    def __init__(self, served):
        self.by_key = collections.defaultdict(list)
        self.other = []  # static mounts, and routes whose first three segments hold a parameter
        for route in served:
            key = None if route.directory else leading_key(route.segments)
            (self.by_key[key] if key else self.other).append(route)

    def candidates(self, ui):
        key = leading_key(ui)
        if key is None:
            return [r for group in self.by_key.values() for r in group] + self.other
        return self.by_key.get(key, []) + self.other


# --- a path the UI writes: segments, each literal, a run-time value, or text around one ---------------------------


def ui_segment(pieces):
    if all(p is not WILD for p in pieces):
        return ("lit", "".join(pieces))
    if len(pieces) == 1:
        return ("wild",)
    return ("glob", re.compile("".join(".*" if p is WILD else re.escape(p) for p in pieces)))


def ui_path(parts):
    """(segments, tail). A run-time value at the end that does not follow a /, as in '/api/v2' + path, may be the rest
    of its segment and any segments after it: that segment is a glob that starts with the text before it, and tail
    says whatever follows may be anything."""
    tail = bool(parts) and parts[-1] is WILD and (len(parts) < 2 or parts[-2] is WILD or not parts[-2].endswith("/"))
    return ui_segments(parts), tail


def ui_segments(parts):
    """The segments of a path (the first is the empty text before its leading /)."""
    segs, cur = [], []
    for p in parts:
        if p is WILD:
            cur.append(WILD)
            continue
        for idx, chunk in enumerate(p.split("/")):
            if idx:
                segs.append(cur)
                cur = []
            if chunk:
                cur.append(chunk)
    segs.append(cur)
    return [ui_segment(pieces) for pieces in segs]


def segment_matches(u, r):
    kind, data = r
    if kind == "lit":
        if u[0] == "lit":
            return u[1] == data
        if u[0] == "wild":
            return True
        return u[1].fullmatch(data) is not None
    if u[0] == "lit":
        return data.fullmatch(u[1]) is not None
    return True  # a run-time value could be this parameter's value


def route_matches(ui, tail, route):
    def go(i, j):
        if i == len(ui):
            return tail or j == len(route) or (j == len(route) - 1 and route[j][0] == "path")
        if j == len(route):
            return False
        if route[j][0] == "path":
            return j == len(route) - 1 or any(go(k, j + 1) for k in range(i, len(ui) + 1))
        return segment_matches(ui[i], route[j]) and go(i + 1, j + 1)

    return go(0, 0)


def static_file_served(route, path):
    prefix = route.template.rstrip("/")
    if path != prefix and not path.startswith(prefix + "/"):
        return False
    target = (route.directory / path[len(prefix) + 1 :]).resolve()
    return target.is_file() and route.directory.resolve() in target.parents


def lookup_served(parts, method, index):
    """(True, set()) when a route serves the path with the method (any method when unknown). Otherwise (False,
    the methods the path is served with, for the report)."""
    ui, tail = ui_path(parts)
    literal = None if any(p is WILD for p in parts) else "".join(parts)
    served = set()
    for route in index.candidates(ui):
        if route.directory is not None:
            if literal is not None and method in (None, "GET") and static_file_served(route, literal):
                return True, served
            continue
        if route_matches(ui, tail, route.segments):
            if method is None or method in route.methods:
                return True, served
            served |= route.methods
    return False, served


def normalize(parts):
    """Adjacent text joined, the query string cut off, the trailing slash dropped. None when nothing is left."""
    merged = []
    for p in parts:
        if p is WILD:
            merged.append(WILD)
        elif p:
            if merged and merged[-1] is not WILD:
                merged[-1] += p
            else:
                merged.append(p)
    out = []
    for p in merged:
        if p is WILD:
            out.append(WILD)
            continue
        cut = [p.index(c) for c in "?#" if c in p]
        if cut:
            out.append(p[: min(cut)])
            break
        if out and out[-1] is WILD and ("=" in p or "&" in p):
            out.pop()  # a run-time separator before `key=value` starts the query string
            break
        out.append(p)
    out = [p for p in out if p is WILD or p]
    if out and out[-1] is not WILD:
        out[-1] = out[-1].rstrip("/")
    return out or None


def checkable(parts):
    """A path is checked when it starts with a written-out name (/api, /v2) and has written-out text after it. A path
    that is only run-time text after the name, such as a helper's own path parameter, or a string that only mentions
    /api/, has nothing to check."""
    segs, _ = ui_path(parts)
    return (
        len(segs) >= 3
        and segs[0] == ("lit", "")
        and segs[1][0] == "lit"
        and segs[1][1] != ""
        and any(s[0] == "glob" or (s[0] == "lit" and s[1]) for s in segs[2:])
    )


def finalize(alts, prefix=""):
    """The path parts for each spelling, with the helper's prefix; None when any spelling cannot be checked."""
    out = []
    for alt in alts:
        parts = list(alt)
        if prefix:
            if parts and parts[0] is not WILD and not parts[0].startswith("/"):
                parts[0] = "/" + parts[0]
            parts = [prefix] + parts
        parts = normalize(parts)
        if parts is not None and checkable(parts):
            out.append(parts)  # a spelling that cannot be read is run-time text; the others are still checked
    return out or None


def shown(parts):
    return "".join(p if p is not WILD else "{*}" for p in parts)


# --- the UI's JavaScript: a lexer for the subset the UI uses ----------------------------------------------------

PUNCT = sorted(
    [
        ">>>=",
        "...",
        "===",
        "!==",
        "**=",
        "<<=",
        ">>=",
        ">>>",
        "&&=",
        "||=",
        "??=",
        "=>",
        "==",
        "!=",
        "<=",
        ">=",
        "&&",
        "||",
        "??",
        "?.",
        "++",
        "--",
        "+=",
        "-=",
        "*=",
        "/=",
        "%=",
        "&=",
        "|=",
        "^=",
        "**",
        "<<",
        ">>",
    ],
    key=len,
    reverse=True,
)
IDENT = re.compile(r"(?:[^\W\d]|\$)[\w$]*")
NUMBER = re.compile(r"\d[\w.]*")
REGEX_AFTER_WORDS = {
    "return",
    "typeof",
    "instanceof",
    "in",
    "of",
    "new",
    "delete",
    "void",
    "throw",
    "case",
    "do",
    "else",
    "yield",
    "await",
}
ESCAPES = {"n": "\n", "t": "\t", "r": "\r"}


class Tok:
    __slots__ = ("kind", "value", "pos", "parts")

    def __init__(self, kind, value, pos, parts=None):
        self.kind, self.value, self.pos, self.parts = kind, value, pos, parts


def lex(src, i, in_expr=False):
    """Tokens from i. In a template's `${...}` (in_expr), stops after the brace that closes it."""
    toks, depth, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "'\"":
            value, j = read_quoted(src, i)
            toks.append(Tok("str", value, i))
            i = j
        elif c == "`":
            parts, j = read_template(src, i)
            toks.append(Tok("tpl", None, i, parts))
            i = j
        elif c == "/" and regex_allowed(toks):
            j = read_regex(src, i)
            toks.append(Tok("re", None, i))
            i = j
        elif c.isdigit():
            m = NUMBER.match(src, i)
            toks.append(Tok("num", m.group(), i))
            i = m.end()
        elif IDENT.match(src, i):
            m = IDENT.match(src, i)
            toks.append(Tok("id", m.group(), i))
            i = m.end()
        else:
            if c == "}":
                if in_expr and depth == 0:
                    return toks, i + 1
                depth -= 1
            elif c == "{":
                depth += 1
            op = next((p for p in PUNCT if src.startswith(p, i)), c)
            if op == "?." and src[i + 2 : i + 3].isdigit():
                op = "?"  # `a?.5:b` is a ternary
            toks.append(Tok("p", op, i))
            i += len(op)
    if in_expr:
        raise SyntaxError("unterminated template expression")
    return toks, i


def read_quoted(src, i):
    quote, out, j = src[i], [], i + 1
    while j < len(src) and src[j] != quote and src[j] != "\n":
        if src[j] == "\\" and j + 1 < len(src):
            out.append(ESCAPES.get(src[j + 1], src[j + 1]))
            j += 2
            continue
        out.append(src[j])
        j += 1
    return "".join(out), j + 1


def read_template(src, i):
    """The parts of a template literal: ("s", text) and ("e", tokens) for each ${...}."""
    parts, buf, j = [], [], i + 1
    while j < len(src):
        c = src[j]
        if c == "\\" and j + 1 < len(src):
            buf.append(src[j + 1])
            j += 2
        elif c == "`":
            break
        elif src.startswith("${", j):
            if buf:
                parts.append(("s", "".join(buf)))
                buf = []
            expr, j = lex(src, j + 2, in_expr=True)
            parts.append(("e", expr))
        else:
            buf.append(c)
            j += 1
    if buf:
        parts.append(("s", "".join(buf)))
    return parts, j + 1


def regex_allowed(toks):
    if not toks:
        return True
    last = toks[-1]
    if last.kind == "id":
        return last.value in REGEX_AFTER_WORDS
    if last.kind == "p":
        return last.value not in (")", "]", "}")
    return False


def read_regex(src, i):
    j, in_class = i + 1, False
    while j < len(src):
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == "\n" or (c == "/" and not in_class):
            break
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        j += 1
    j += 1
    while j < len(src) and (src[j].isalnum() or src[j] == "_"):
        j += 1
    return j


# --- expressions: a small parser over the tokens. Anything it does not model is a run-time value ----------------

WILD_NODE = ("wild",)
UNKNOWN = [(WILD,)]
BINARY = {
    "??",
    "||",
    "&&",
    "|",
    "^",
    "&",
    "==",
    "!=",
    "===",
    "!==",
    "<",
    ">",
    "<=",
    ">=",
    "<<",
    ">>",
    ">>>",
    "+",
    "-",
    "*",
    "/",
    "%",
    "**",
}
OPEN = {"(": ")", "[": "]", "{": "}"}


def at(toks, i):
    return toks[i] if 0 <= i < len(toks) else None


def is_p(tok, *values):
    return tok is not None and tok.kind == "p" and tok.value in values


def is_id(tok, *names):
    return tok is not None and tok.kind == "id" and (not names or tok.value in names)


def skip_group(toks, i):
    """i is at an opening bracket: the index after its matching close, or the end."""
    depth = 0
    while i < len(toks):
        t = toks[i]
        if t.kind == "p":
            if t.value in OPEN:
                depth += 1
            elif t.value in (")", "]", "}"):
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return len(toks)


def skip_to(toks, i, stops):
    """The first index from i, skipping whole brackets, whose token is one of stops (or the end)."""
    while i < len(toks) and not is_p(toks[i], *stops):
        i = skip_group(toks, i) if is_p(toks[i], "(", "[", "{") else i + 1
    return i


def parse_expr(toks, i):
    node, i = parse_binary(toks, i)
    if is_p(at(toks, i), "?"):
        yes, i = parse_expr(toks, i + 1)
        if is_p(at(toks, i), ":"):
            no, i = parse_expr(toks, i + 1)
            return ("cond", node, yes, no), i
        return WILD_NODE, i
    return node, i


def parse_binary(toks, i):
    first, i = parse_unary(toks, i)
    operands, ops = [first], []
    while True:
        t = at(toks, i)
        if t is not None and ((t.kind == "p" and t.value in BINARY) or is_id(t, "instanceof", "in")):
            ops.append(t.value)
            operand, i = parse_unary(toks, i + 1)
            operands.append(operand)
        else:
            break
    if not ops:
        return first, i
    if all(op == "+" for op in ops):
        return ("plus", operands), i
    return WILD_NODE, i


def parse_unary(toks, i):
    t = at(toks, i)
    if is_p(t, "!", "-", "+", "~", "...") or is_id(t, "typeof", "void", "delete", "await", "new"):
        _, i = parse_unary(toks, i + 1)
        return WILD_NODE, i
    return parse_postfix(toks, i)


def parse_postfix(toks, i):
    node, i = parse_primary(toks, i)
    if node[0] == "id" and is_p(at(toks, i), "("):
        args, i = parse_args(toks, i)
        node = ("call", node[1], args)
    while True:
        t = at(toks, i)
        if is_p(t, ".", "?."):
            i += 1
            if is_id(at(toks, i)):
                i += 1
            node = WILD_NODE
        elif is_p(t, "["):
            i, node = skip_group(toks, i), WILD_NODE
        elif is_p(t, "("):
            _, i = parse_args(toks, i)
            node = WILD_NODE
        else:
            return node, i


def parse_primary(toks, i):
    t = at(toks, i)
    if t is None:
        return WILD_NODE, i
    if t.kind in ("str", "num"):
        return ("lit", t.value), i + 1
    if t.kind == "tpl":
        parts = [value if kind == "s" else parse_expr(value, 0)[0] for kind, value in t.parts]
        return ("tpl", parts), i + 1
    if t.kind == "id":
        return ("id", t.value), i + 1
    if is_p(t, "("):
        node, _ = parse_expr(toks, i + 1)
        return ("paren", node), skip_group(toks, i)
    if is_p(t, "{"):
        return parse_object(toks, i)
    if is_p(t, "["):
        return WILD_NODE, skip_group(toks, i)
    if t.kind == "re":
        return WILD_NODE, i + 1
    return WILD_NODE, i  # an operator or a closer: nothing is read


def parse_args(toks, i):
    """The arguments of the call whose ( is at i: (node, first token, end token) for each, and the index after ')'."""
    args, j = [], i + 1
    while j < len(toks) and not is_p(toks[j], ")"):
        start = j
        node, j = parse_expr(toks, j)
        j = skip_to(toks, j, (",", ")"))
        args.append((node, start, j))
        if is_p(at(toks, j), ","):
            j += 1
    return args, min(j + 1, len(toks))


def parse_object(toks, i):
    """An object literal: ("obj", {key: node}, has_spread). Keys it cannot read (computed, methods) are dropped."""
    entries, spread, j = {}, False, i + 1
    while j < len(toks) and not is_p(toks[j], "}"):
        t = toks[j]
        if is_p(t, ","):
            j += 1
        elif is_p(t, "..."):
            spread = True
            _, j = parse_expr(toks, j + 1)
            j = skip_to(toks, j, (",", "}"))
        elif t.kind in ("id", "str", "num") and not is_p(at(toks, j + 1), "("):
            key, k = t.value, j + 1
            if is_p(at(toks, k), ":"):
                entries[key], k = parse_expr(toks, k + 1)
            elif is_p(at(toks, k), ",", "}"):
                entries[key] = ("id", key)
            else:
                entries[key] = WILD_NODE
            j = skip_to(toks, k, (",", "}"))
        else:
            j = skip_to(toks, j, (",", "}"))
    return ("obj", entries, spread), min(j + 1, len(toks))


def arrow_body(toks, j):
    """An arrow function's expression body, or what a block body returns first (else None)."""
    if is_p(at(toks, j), "{"):
        if is_id(at(toks, j + 1), "return"):
            return parse_expr(toks, j + 2)[0]
        return None
    return parse_expr(toks, j)[0]


def body_after(toks, j):
    """A function declaration's body, when it is a single return (else None)."""
    if is_p(at(toks, j), "{") and is_id(at(toks, j + 1), "return"):
        return parse_expr(toks, j + 2)[0]
    return None


def group_params(toks, open_i, close_i):
    """[(name, default node or None)] for the parameter list in (open_i, close_i), or None when it has a pattern or
    a rest parameter."""
    params, j, end = [], open_i + 1, close_i - 1
    while j < end:
        t = toks[j]
        if t.kind != "id":
            return None
        if is_p(at(toks, j + 1), "="):
            default, nxt = parse_expr(toks, j + 2)
            params.append((t.value, default))
            j = skip_to(toks, nxt, (",", ")")) + 1
        elif j + 1 >= end or is_p(at(toks, j + 1), ","):
            params.append((t.value, None))
            j += 2
        else:
            return None
    return params


def classify_value(toks, start):
    """What a const's value is: ("builder", params, body) for an arrow function, else ("const", None, node)."""
    first = at(toks, start)
    if is_p(first, "("):
        close = skip_group(toks, start)
        if is_p(at(toks, close), "=>"):
            return ("builder", group_params(toks, start, close), arrow_body(toks, close + 1))
    if is_id(first) and is_p(at(toks, start + 1), "=>"):
        return ("builder", [(first.value, None)], arrow_body(toks, start + 2))
    return ("const", None, parse_expr(toks, start)[0])


# --- what the UI declares --------------------------------------------------------------------------------------


class Scope:
    """Declarations across the UI. A name is read only when it has one entry and is not a parameter or a
    let/var/destructured binding anywhere."""

    def __init__(self):
        self.entries = collections.defaultdict(list)  # name -> [(kind, params, node)]
        self.opaque = set()  # names that are parameters or have no single known value


def identifiers(toks, start, end):
    for j in range(start, end):
        if toks[j].kind == "id" and not is_p(at(toks, j - 1), ".", "?."):
            yield toks[j].value


def add_group_names(scope, toks, open_i, close_i):
    for name in identifiers(toks, open_i + 1, close_i - 1):
        scope.opaque.add(name)


def collect(scope, rel, toks):
    enclosing, _ = enclosing_closers(toks)
    for k, t in enumerate(toks):
        if t.kind == "id" and t.value in ("const", "let", "var"):
            nxt = at(toks, k + 1)
            if is_id(nxt):
                if t.value == "const" and is_p(at(toks, k + 2), "=") and enclosing[k] == len(toks):
                    scope.entries[nxt.value].append(classify_value(toks, k + 3))
                else:
                    scope.opaque.add(nxt.value)  # only a function or block sees it: never read by name elsewhere
            elif is_p(nxt, "{", "["):
                end = skip_group(toks, k + 1)
                for name in identifiers(toks, k + 2, end - 1):
                    scope.opaque.add(name)
        elif t.kind == "id" and t.value == "function":
            j = k + 1
            name = None
            if is_id(at(toks, j)):
                name = toks[j].value
                j += 1
            if is_p(at(toks, j), "("):
                close = skip_group(toks, j)
                if name and enclosing[k] == len(toks):
                    scope.entries[name].append(("builder", group_params(toks, j, close), body_after(toks, close)))
                elif name:
                    scope.opaque.add(name)
        elif t.kind == "p" and t.value == "(" and not is_id(at(toks, k - 1), *NOT_A_PARAMETER_LIST):
            close = skip_group(toks, k)
            if is_p(at(toks, close), "{", "=>"):
                add_group_names(scope, toks, k, close)
        elif t.kind == "id" and is_p(at(toks, k + 1), "=>"):
            scope.opaque.add(t.value)


def lookup(scope, name):
    if name in scope.opaque:
        return None
    entries = scope.entries.get(name, [])
    return entries[0] if len(entries) == 1 else None


def product(lists):
    out = [()]
    for alts in lists:
        out = [a + b for a in out for b in alts]
        if len(out) > MAX_ALTERNATIVES:
            return None
    return out


def dedupe(alts):
    seen = []
    for alt in alts:
        if alt not in seen:
            seen.append(alt)
    return seen


def alts(scope, node, env, depth=0):
    """The spellings a value may have, each a tuple of parts. env binds a builder's parameters to the caller's
    argument expressions (and the environment those were written in)."""
    if depth > MAX_DEPTH:
        return UNKNOWN
    kind = node[0]
    if kind == "lit":
        return [(node[1],)] if node[1] else UNKNOWN  # an empty text stands for whatever the value is at run time
    if kind == "tpl":
        lists = [[(part,)] if isinstance(part, str) else alts(scope, part, env, depth + 1) for part in node[1]]
        return product(lists) or UNKNOWN
    if kind == "id":
        if node[1] in env:
            bound, bound_env = env[node[1]]
            return alts(scope, bound, bound_env, depth + 1)
        entry = lookup(scope, node[1])
        if entry is None or entry[0] != "const":
            return UNKNOWN
        return alts(scope, entry[2], {}, depth + 1)
    if kind == "call":
        name, args = node[1], node[2]
        if name in env:
            return UNKNOWN
        entry = lookup(scope, name)
        if entry is None or entry[0] != "builder" or entry[1] is None or entry[2] is None:
            return UNKNOWN
        callee = {}
        for idx, (pname, default) in enumerate(entry[1]):
            if idx < len(args):
                callee[pname] = (args[idx][0], env)
            elif default is not None:
                callee[pname] = (default, callee)
            else:
                callee[pname] = (WILD_NODE, callee)
        return alts(scope, entry[2], callee, depth + 1)
    if kind == "paren":
        return alts(scope, node[1], env, depth + 1)
    if kind == "cond":
        return dedupe(alts(scope, node[2], env, depth + 1) + alts(scope, node[3], env, depth + 1))
    if kind == "plus":
        return product([alts(scope, op, env, depth + 1) for op in node[1]]) or UNKNOWN
    return UNKNOWN


def flatten(t):
    if t.kind == "str":
        return t.value
    return "".join(value if kind == "s" else "\x00" for kind, value in t.parts)


def token_lists(toks):
    yield toks
    for t in toks:
        if t.kind == "tpl":
            for kind, value in t.parts:
                if kind == "e":
                    yield from token_lists(value)


# --- the call sites ---------------------------------------------------------------------------------------------


class Call:
    def __init__(self, file, line, method, parts_list):
        self.file, self.line, self.method, self.parts_list = file, line, method, parts_list


class UiFile:
    def __init__(self, rel, path):
        self.rel = rel
        self.src = path.read_text(encoding="utf-8")
        try:
            self.toks, _ = lex(self.src, 0)
        except SyntaxError as exc:
            fail(f"{rel}: cannot read its JavaScript ({exc})")
        self.newlines = [m.start() for m in re.finditer("\n", self.src)]

    def line(self, tok):
        return bisect.bisect_left(self.newlines, tok.pos) + 1


def ui_files():
    for path in sorted((ROOT / "ui").rglob("*")):
        rel = path.relative_to(ROOT).as_posix()
        if path.suffix not in (".js", ".ts") or path.name.endswith(".d.ts") or rel.startswith(EXCLUDED):
            continue
        if path.is_file():
            yield UiFile(rel, path)


def fetch_method(args):
    """The method of fetch(url, options): GET without options, else the options' method (None when unknown)."""
    if len(args) < 2:
        return "GET"
    node = args[1][0]
    if node[0] != "obj":
        return None
    entries, spread = node[1], node[2]
    if "method" in entries:
        method = entries["method"]
        return method[1].upper() if method[0] == "lit" else None
    return None if spread else "GET"


def helper_method(name, rel, args, toks, k):
    """(method, prefix, index of the path argument) for a call to a helper, or None when it is not one."""
    prev = at(toks, k - 1)
    if name in RELATIVE_HELPERS:
        return None if is_p(prev, ".", "?.") else (RELATIVE_HELPERS[name], "/api", 0)
    if name == "writeRequest":
        if is_p(prev, ".", "?.") or not args:
            return None
        node = args[0][0]
        method = node[1].upper() if node[0] == "lit" and node[1].upper() in HTTP_METHODS else None
        return method, "/api", 1
    if name == "fetch":
        return fetch_method(args), "", 0
    if name in ABSOLUTE_HELPERS:
        if is_p(prev, ".", "?.") or (name in NEEDS_NEW and not is_id(prev, "new")):
            return None
        return ABSOLUTE_HELPERS[name], "", 0
    if (rel, name) in LOCAL_WRAPPERS and not is_p(prev, ".", "?."):
        if not args or args[0][0][0] != "lit":
            return None
        return args[0][0][1].upper(), LOCAL_WRAPPERS[(rel, name)], 1
    return None


def enclosing_closers(toks):
    """For each token, the index of the closing bracket around it (the list's length at the top level)."""
    n = len(toks)
    match, stack = {}, []
    for j, t in enumerate(toks):
        if t.kind == "p":
            if t.value in OPEN:
                stack.append(j)
            elif t.value in (")", "]", "}") and stack:
                match[stack.pop()] = j
    enclosing, stack = [n] * n, []
    for j, t in enumerate(toks):
        enclosing[j] = match.get(stack[-1], n) if stack else n
        if t.kind == "p":
            if t.value in OPEN:
                stack.append(j)
            elif t.value in (")", "]", "}") and stack:
                stack.pop()
    return enclosing, match


def shadow_ranges(toks):
    """(start, end, name, top) for each name this token list declares with const, let, var, function, a parameter or a
    single-parameter arrow: the name is in scope from start to end. top is set for a declaration at the top of the
    file, which is that file's own definition (api.js defines get, post and the rest). A destructured name is not
    listed: ui/assistant.js takes the page's get and post from deps, which are the helpers themselves."""
    n = len(toks)
    enclosing, match = enclosing_closers(toks)
    out = []

    def params(open_i):
        close = skip_group(toks, open_i)
        if is_p(at(toks, close), "{"):
            end = match.get(close, n)
        elif is_p(at(toks, close), "=>"):
            end = match.get(close + 1, n) if is_p(at(toks, close + 1), "{") else enclosing[open_i]
        else:
            return
        for name in identifiers(toks, open_i + 1, close - 1):
            out.append((open_i, end, name, False))

    for k, t in enumerate(toks):
        if t.kind == "id" and t.value in ("const", "let", "var") and is_id(at(toks, k + 1)):
            out.append((k, enclosing[k], toks[k + 1].value, enclosing[k] == n))
        elif t.kind == "id" and t.value == "function" and is_id(at(toks, k + 1)):
            out.append((k, enclosing[k], toks[k + 1].value, enclosing[k] == n))
        elif t.kind == "p" and t.value == "(" and not is_id(at(toks, k - 1), *NOT_A_PARAMETER_LIST):
            params(k)
        elif t.kind == "id" and is_p(at(toks, k + 1), "=>"):
            end = match.get(k + 2, n) if is_p(at(toks, k + 2), "{") else enclosing[k]
            out.append((k, end, t.value, False))
    return out


def helper_calls(scan, scope, calls, skipped, consumed):
    for toks in token_lists(scan.toks):
        ranges = shadow_ranges(toks)
        for k, t in enumerate(toks):
            if t.kind != "id" or not is_p(at(toks, k + 1), "("):
                continue
            prev = at(toks, k - 1)
            if is_p(prev, ".", "?.") and t.value != "fetch":
                continue
            if is_id(prev, "function", "class"):
                continue
            args, end = parse_args(toks, k + 1)
            if is_p(at(toks, end), "{"):
                continue  # a method's own definition: name(...) { ... }
            name = t.value
            shadows = [r for r in ranges if r[2] == name and r[0] < k < r[1]]
            if (scan.rel, name) not in LOCAL_WRAPPERS and name in HELPER_NAMES and shadows:
                # A declaration in scope takes the name, so the call is not the helper's. A declaration at the top of
                # the file is that file's own helper (api.js calls its own get and post), so it is not reported.
                if not all(r[3] for r in shadows):
                    skipped.append((scan.rel, scan.line(t), name, "a local declaration in scope takes the name"))
                continue
            spec = helper_method(name, scan.rel, args, toks, k)
            if spec is None:
                continue
            method, prefix, index = spec
            if len(args) <= index:
                continue
            node, start, stop = args[index]
            consumed.update(id(x) for x in toks[start:stop])
            spellings = alts(scope, node, {})
            parts_list = finalize(spellings, prefix)
            if parts_list is None:
                skipped.append((scan.rel, scan.line(t), name, "the path is a run-time value"))
                continue
            calls.append(Call(scan.rel, scan.line(t), method, parts_list))
            if name == "cloudCompose":  # with files, it posts to the uploads route instead
                uploads = [
                    ("/v2/uploads/" + alt[0][len("/v2/") :],) + alt[1:]
                    for alt in spellings
                    if alt and isinstance(alt[0], str) and alt[0].startswith("/v2/")
                ]
                up = finalize(uploads, "/api") if uploads else None
                if up:
                    calls.append(Call(scan.rel, scan.line(t), "POST", up))


FLAT_API = re.compile(
    r"(?<=[\"'`(=<>\x00])/api/"
)  # a path written inside a string, after a quote, = or a run-time value
PATH_END = re.compile(r"[\"'\s<>)`,;]")


def literal_calls(scan, scope, calls, consumed):
    """Paths written out as text (a link, an image source, a download). Their method is not known."""
    for toks in token_lists(scan.toks):
        for k, t in enumerate(toks):
            if t.kind not in ("str", "tpl") or id(t) in consumed:
                continue
            if not is_p(at(toks, k - 1), "+"):  # a chain's head reads the whole chain
                node, _ = parse_expr(toks, k)
                for alt in alts(scope, node, {}):
                    if alt and isinstance(alt[0], str) and alt[0].startswith("/api/"):
                        parts_list = finalize([alt])
                        if parts_list:
                            calls.append(Call(scan.rel, scan.line(t), None, parts_list))
            text = flatten(t)
            for m in FLAT_API.finditer(text):
                rest = text[m.start() :]
                end = PATH_END.search(rest)
                found = (rest[: end.start()] if end else rest).rstrip(".")
                parts = []
                for index, piece in enumerate(found.split("\x00")):
                    if index:
                        parts.append(WILD)
                    if piece:
                        parts.append(piece)
                parts_list = finalize([tuple(parts)])
                if parts_list:
                    calls.append(Call(scan.rel, scan.line(t), None, parts_list))


def collect_calls(files):
    scope = Scope()
    for scan in files:
        for toks in token_lists(scan.toks):
            collect(scope, scan.rel, toks)
    calls, skipped = [], []
    for scan in files:
        consumed = set()
        helper_calls(scan, scope, calls, skipped, consumed)
        literal_calls(scan, scope, calls, consumed)
    return calls, skipped


# --- the check --------------------------------------------------------------------------------------------------


def build_report():
    served = served_routes()
    index = RouteIndex(served)
    files = list(ui_files())
    calls, skipped = collect_calls(files)
    unmatched = []
    for call in calls:
        for parts in call.parts_list:
            ok, served_as = lookup_served(parts, call.method, index)
            if not ok:
                unmatched.append((call, parts, served_as))
                break
    return served, calls, skipped, unmatched


def describe(call, parts, served_as):
    method = call.method or "ANY"
    note = f"  (served as {', '.join(sorted(served_as))})" if served_as and call.method else ""
    return f"{call.file}:{call.line} {method} {shown(parts)}{note}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="fail on new unmatched calls or stale counts (the default)")
    mode.add_argument("--write", action="store_true", help="lower the baseline to the current counts")
    mode.add_argument("--skipped", action="store_true", help="list the calls whose path cannot be read")
    parser.add_argument("--base", metavar="REF", help="git ref whose baseline must not be exceeded (e.g. origin/main)")
    args = parser.parse_args(argv)

    served, calls, skipped, unmatched = build_report()
    if args.skipped:
        for rel, line, name, reason in sorted(skipped):
            print(f"{rel}:{line} {name}(...) {reason}")
        print(f"ui routes: {len(skipped)} calls skipped")
        return 0

    found = sorted({(call.file, call.line, describe(call, parts, served_as)) for call, parts, served_as in unmatched})
    counts = collections.Counter(file for file, _, _ in found)
    for _, _, text in found:
        print(text)

    baseline_path = ROOT / BASELINE_REL
    if args.write:
        old = ui_baseline.load_baseline(baseline_path)
        grown = [f for f in sorted(counts) if old is not None and counts[f] > old.get(f, 0)]
        for file in grown:
            print(f"refusing to raise {file}: {counts[file]} unmatched calls, baseline {old.get(file, 0)}")
        if grown:
            fail("fix the new unmatched calls; a baseline only shrinks")
        ui_baseline.write_baseline(baseline_path, counts)
        print(f"wrote {BASELINE_REL}: {sum(counts.values())} unmatched calls in {len(counts)} files")
        return 0

    baseline = ui_baseline.load_baseline(baseline_path)
    if baseline is None:
        fail(f"{BASELINE_REL} does not exist; create it with: python3 scripts/check_ui_routes.py --write")
    problems = 0
    for file in sorted(set(counts) | set(baseline)):
        now, was = counts.get(file, 0), baseline.get(file, 0)
        if now > was:
            problems += 1
            print(f"NEW {file}: {now} unmatched calls, the baseline allows {was}")
        elif now < was:
            problems += 1
            print(
                f"STALE {file}: the baseline allows {was}, now {now}; remove from baseline: "
                f"python3 scripts/check_ui_routes.py --write"
            )
    if args.base:
        old = ui_baseline.baseline_at(args.base, BASELINE_REL)
        if old is None:
            print(f"note: {BASELINE_REL} is absent at {args.base}; the growth check starts once it exists there")
        else:
            for file in sorted(baseline):
                if baseline[file] > old.get(file, 0):
                    problems += 1
                    print(
                        f"GREW {file}: {old.get(file, 0)} at {args.base}, now {baseline[file]}; "
                        "baselines only shrink, so fix the calls instead"
                    )
    print(
        f"ui routes: {len(calls)} calls checked against {len(served)} served routes; {len(found)} unmatched in "
        f"{len(counts)} files; {len(skipped)} calls skipped (path or name cannot be read); baseline {sum(baseline.values())} in "
        f"{len(baseline)} files; {'ok' if not problems else f'{problems} problem(s)'}"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
