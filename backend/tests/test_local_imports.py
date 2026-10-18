"""A function must not read a name before its own local import of that name.

`from .store import Store` inside one branch of a function makes Store local to the whole function, so every
earlier read raises UnboundLocalError. v0.3.52's first candidate shipped exactly that in backend/manage.py: the
startup commands crashed only as a real process (docker/entrypoint.sh), which the default suite does not start,
so it surfaced at hosted release validation. This check reads every tracked Python file, so the same mistake
fails in seconds anywhere, not at the release gate. It compares lines, not control flow (see LocalImportReads), so it
complements tests that start the real process; it does not replace them.
"""
import ast
from pathlib import Path
import re
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[2]


def tracked_python_files():
    listed = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True, check=True)
    return [REPO / name for name in listed.stdout.split() if (REPO / name).is_file()]


COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


class LocalImportReads(ast.NodeVisitor):
    """Each function's own imports, bindings and reads, in source order.

    Python decides at compile time that a name imported anywhere in a function is local to all of it, so a read of
    that name with no binding of it earlier in the function raises UnboundLocalError. Expressions evaluated where the
    function is defined (decorators, defaults, annotations; a class's bases) belong to the enclosing scope, and so do
    comprehensions and class bodies, which run at once. A nested function's body is its own scope. Order is by line, not by
    control flow: `if reload: import json` followed by a later read of json is not caught, and a read earlier in a loop
    than the loop's own import would be flagged. Neither occurs in this repository today."""

    def __init__(self):
        self.scopes, self.hidden, self.found = [], [], []

    def scope(self, name, bound, body):
        outer_hidden, self.hidden = self.hidden, []
        self.scopes.append({"name": name, "imports": [], "binds": [(n, 0) for n in bound], "reads": [],
                            "declared": set()})
        for statement in body:
            self.visit(statement)
        self.hidden = outer_hidden
        return self.scopes.pop()

    def definition_time(self, node):
        args = node.args
        for expression in [*getattr(node, "decorator_list", []), *args.defaults, *args.kw_defaults,
                           getattr(node, "returns", None),
                           *[a.annotation for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg,
                                                    args.kwarg] if a is not None]]:
            if expression is not None:
                self.visit(expression)

    def visit_FunctionDef(self, node):
        self.definition_time(node)
        self.bind(node.name, node.lineno)
        args = node.args
        params = [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg] if a]
        scope = self.scope(node.name, params, node.body)
        for imported, line in scope["imports"]:
            if imported in scope["declared"]:
                continue
            earlier = [at for bound, at in scope["binds"] if bound == imported]
            if any(read == imported and at < line and not any(b < at for b in earlier)
                   for read, at in scope["reads"]):
                self.found.append(f"{node.name}() reads {imported} before its local import on line {line}")

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        self.definition_time(node)
        args = node.args
        self.scope("", [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
                        if a], [node.body])

    def visit_ClassDef(self, node):
        for expression in [*node.decorator_list, *node.bases, *[k.value for k in node.keywords]]:
            self.visit(expression)
        self.bind(node.name, node.lineno)
        body = self.scope("", [], node.body)
        if self.scopes:                     # the body runs at once: a name it does not bind first is the function's
            self.scopes[-1]["reads"] += [(read, at) for read, at in body["reads"]
                                         if not any(bound == read and b < at for bound, b in body["binds"])]

    def visit_comprehension_node(self, node):
        first, *rest = node.generators
        self.visit(first.iter)              # evaluated in the enclosing scope
        self.hidden.append({n.id for g in node.generators for n in ast.walk(g.target) if isinstance(n, ast.Name)})
        for generator in node.generators:
            for expression in ([] if generator is first else [generator.iter]) + generator.ifs:
                self.visit(expression)
        for part in ("elt", "key", "value"):
            if hasattr(node, part):
                self.visit(getattr(node, part))
        self.hidden.pop()

    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = visit_comprehension_node

    def visit_AnnAssign(self, node):        # a local variable's annotation is never evaluated
        self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)

    def bind(self, name, line):
        if self.scopes and name:
            self.scopes[-1]["binds"].append((name, line))

    def visit_Import(self, node):
        if self.scopes:
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                if name != "*":
                    self.scopes[-1]["imports"].append((name, node.lineno))
                    self.bind(name, node.lineno)

    visit_ImportFrom = visit_Import

    def visit_Global(self, node):
        if self.scopes:
            self.scopes[-1]["declared"].update(node.names)

    visit_Nonlocal = visit_Global

    def visit_ExceptHandler(self, node):
        self.bind(node.name, node.lineno)
        self.generic_visit(node)

    def visit_MatchAs(self, node):
        self.bind(node.name, node.lineno)
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node):
        self.bind(node.rest, node.lineno)
        self.generic_visit(node)

    def visit_Name(self, node):
        if not self.scopes:
            return
        if isinstance(node.ctx, ast.Store):
            self.bind(node.id, node.lineno)
        elif isinstance(node.ctx, ast.Load) and not any(node.id in names for names in self.hidden):
            self.scopes[-1]["reads"].append((node.id, node.lineno))


LOCAL_IMPORT = re.compile(r"^([ \t]+)(?:from[ \t]+\S+[ \t]+)?import[ \t]+(?:\(([^)]*)\)|([^#;\n]*))", re.MULTILINE)
DEFINITION = re.compile(r"^([ \t]*)(?:async[ \t]+)?def[ \t]", re.MULTILINE)


def may_read_before_a_local_import(source):
    """Cheap text pass: a flagged read sits between the function's `def` and its import, so parse only a file where a
    name an indented import binds already appears there. Over-matching (comments, strings) only costs a parse."""
    source = source.replace("\\\n", " ")
    definitions = [(found.start(), len(found.group(1))) for found in DEFINITION.finditer(source)]
    for match in LOCAL_IMPORT.finditer(source):
        indent = len(match.group(1))
        start = max([at for at, depth in definitions if at < match.start() and depth < indent], default=0)
        names = re.sub(r"#[^\n]*", "", match.group(2) or match.group(3))
        for part in names.split(","):
            if part.split() and re.search(rf"\b{re.escape(part.split()[-1].split('.')[0])}\b",
                                          source[start:match.start()].split("\n", 1)[-1]):
                return True
    return False


def reads_before_a_local_import(source):
    if not may_read_before_a_local_import(source):
        return []
    visitor = LocalImportReads()
    visitor.visit(ast.parse(source))
    return visitor.found


def test_the_v0352_manage_crash_is_caught():
    source = ("from .store import Store\n\n"
              "def main(command):\n"
              "    if command == 'initialize':\n"
              "        return Store()\n"
              "    from .store import Store\n"
              "    return Store()\n")
    assert reads_before_a_local_import(source) == ["main() reads Store before its local import on line 6"]


FLAGGED = {
    "a default of a nested function runs in the enclosing one":
        ("from .store import Store\n\ndef main():\n    def helper(factory=Store):\n        return factory\n"
         "    from .store import Store\n    return helper()\n", "main() reads Store before its local import on line 6"),
    "a comprehension runs at once in the function":
        ("import json\n\ndef dump(values):\n    out = [json.dumps(v) for v in values]\n    import json\n"
         "    return out\n", "dump() reads json before its local import on line 5"),
    "a builtin is shadowed too":
        ("def read(name):\n    data = open(name).read()\n    from io import open\n    return data\n",
         "read() reads open before its local import on line 3"),
    "a decorator inside the function":
        ("import functools\n\ndef outer():\n    @functools.cache\n    def inner():\n        return 1\n"
         "    import functools\n    return inner\n", "outer() reads functools before its local import on line 7"),
    "a class body runs at once in the function":
        ("import json\n\ndef outer():\n    class Codec:\n        dumps = json.dumps\n    import json\n    return Codec\n",
         "outer() reads json before its local import on line 6"),
}

ALLOWED = {
    "used only after the import": "import json\n\ndef dump(value):\n    import json\n    return json.dumps(value)\n",
    "a default is read where the function is defined":
        "import json\n\ndef dump(encode=json.dumps):\n    import json\n    return encode({})\n",
    "an annotation is read where the function is defined":
        "from .store import Store\n\ndef open_store(path) -> Store:\n    from .store import Store\n    return Store(path)\n",
    "a parameter of the same name": "def dump(json, value):\n    json.dumps(value)\n    import json\n    return json\n",
    "bound earlier in the function":
        "def dump(value):\n    json = None\n    print(json)\n    import json\n    return json.dumps(value)\n",
    "declared global": "import json\n\ndef reload():\n    global json\n    print(json)\n    import json\n",
    "a nested function's body is its own scope":
        "import json\n\ndef outer():\n    def inner():\n        return json.dumps(1)\n    import json\n    return inner\n",
    "a comprehension's own loop variable":
        "def names(rows):\n    out = [json for json in rows]\n    import json\n    return out, json\n",
    "a local variable's annotation is never evaluated":
        "def load(path):\n    store: Store = None\n    from .store import Store\n    return Store(path), store\n",
    "a class's own names in its body":
        "def outer():\n    class Codec:\n        json = None\n        dumps = json\n    import json\n    return Codec\n",
}


@pytest.mark.parametrize("source, problem", FLAGGED.values(), ids=FLAGGED)
def test_a_read_before_the_local_import_is_flagged(source, problem):
    assert reads_before_a_local_import(source) == [problem]


@pytest.mark.parametrize("source", ALLOWED.values(), ids=ALLOWED)
def test_a_read_that_cannot_hit_the_unbound_local_is_allowed(source):
    visitor = LocalImportReads()        # without the text pass, which would hide some of these from the visitor
    visitor.visit(ast.parse(source))
    assert visitor.found == [] and reads_before_a_local_import(source) == []


@pytest.mark.parametrize("source", [
    "import json\n\ndef dump(v):\n    print(json)\n    from json import (  # the codec\n        dumps as json,  # alias\n    )\n",
    "import json\n\ndef dump(v):\n    print(json)\n    from os import path, \\\n        sep as json\n",
], ids=["a commented, parenthesized import", "a backslash continuation"])
def test_the_text_pass_reads_every_import_form(source):
    assert reads_before_a_local_import(source) == ["dump() reads json before its local import on line 5"]


def test_no_tracked_function_reads_a_name_before_importing_it_locally():
    problems = []
    for path in tracked_python_files():
        try:
            source = path.read_text()
            found = reads_before_a_local_import(source)
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue    # fixtures and templates that are not Python 3 source
        problems += [f"{path.relative_to(REPO)}: {problem}" for problem in found]
    assert problems == []
