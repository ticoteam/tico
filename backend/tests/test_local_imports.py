"""A local import must not rebind a module-level name that the same function reads before it.

`from .store import Store` inside one branch of a function makes Store local to the whole function, so every
earlier read raises UnboundLocalError. v0.3.52's first candidate shipped exactly that in backend/manage.py: the
startup commands crashed only as a real process (docker/entrypoint.sh), which the default suite does not start,
so it surfaced at hosted release validation. This check reads every tracked Python file, so the same mistake
fails in seconds anywhere, not at the release gate.
"""
import ast
from pathlib import Path
import re
import subprocess

REPO = Path(__file__).resolve().parents[2]


def tracked_python_files():
    listed = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True, check=True)
    return [REPO / name for name in listed.stdout.split() if (REPO / name).is_file()]


def module_names(tree):
    names = {alias.asname or alias.name.split(".")[0]
             for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    return names | {node.name for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


class LocalImportReads(ast.NodeVisitor):
    """One pass over the tree: each function's own imports and reads, not those of functions nested in it."""

    def __init__(self, module):
        self.module, self.scopes, self.found = module, [], []

    def visit_FunctionDef(self, node):
        self.scopes.append((node.name, [], []))
        self.generic_visit(node)
        name, imports, reads = self.scopes.pop()
        for imported, line in imports:
            if imported in self.module and any(read == imported and at < line for read, at in reads):
                self.found.append(f"{name}() reads {imported} before its local import on line {line}")

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.scopes.append(("", [], []))     # a class body or lambda is its own scope, not the function's
        self.generic_visit(node)
        self.scopes.pop()

    visit_Lambda = visit_ClassDef

    def visit_Import(self, node):
        if self.scopes:
            self.scopes[-1][1].extend((alias.asname or alias.name.split(".")[0], node.lineno) for alias in node.names)

    visit_ImportFrom = visit_Import

    def visit_Name(self, node):
        if self.scopes and isinstance(node.ctx, ast.Load):
            self.scopes[-1][2].append((node.id, node.lineno))


IMPORT = re.compile(r"^([ \t]*)(?:from[ \t]+\S+[ \t]+)?import[ \t]+(?:\(([^)]*)\)|([^#\n]*))", re.MULTILINE)
DEFINITION = re.compile(r"^(?:async[ \t]+)?(?:def|class)[ \t]+(\w+)", re.MULTILINE)


def may_shadow(source):
    """Cheap text pass: parse only files where an indented import names something imported or defined at the top."""
    top, local = set(DEFINITION.findall(source)), set()
    for indent, grouped, names in IMPORT.findall(source):
        bound = {part.split()[-1].split(".")[0] for part in (grouped or names).split(",") if part.split()}
        (local if indent else top).update(bound)
    return bool(top & local)


def reads_before_a_local_import(source):
    if not may_shadow(source):
        return []
    tree = ast.parse(source)
    visitor = LocalImportReads(module_names(tree))
    visitor.visit(tree)
    return visitor.found


def test_the_v0352_manage_crash_is_caught():
    source = ("from .store import Store\n\n"
              "def main(command):\n"
              "    if command == 'initialize':\n"
              "        return Store()\n"
              "    from .store import Store\n"
              "    return Store()\n")
    assert reads_before_a_local_import(source) == ["main() reads Store before its local import on line 6"]


def test_a_local_import_used_only_after_it_is_allowed():
    source = "import json\n\ndef dump(value):\n    import json\n    return json.dumps(value)\n"
    assert reads_before_a_local_import(source) == []


def test_no_tracked_function_reads_a_module_name_before_rebinding_it_locally():
    problems = []
    for path in tracked_python_files():
        try:
            source = path.read_text()
            found = reads_before_a_local_import(source)
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue    # fixtures and templates that are not Python 3 source
        problems += [f"{path.relative_to(REPO)}: {problem}" for problem in found]
    assert problems == []
