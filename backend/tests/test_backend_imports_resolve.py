"""Every import of a backend module names a module that exists.

The Pipelines v3 cut-over deleted whole modules (task_review, answer_task, task_holds, ...). An import inside a
function only fails when that function runs, so a missed caller passes every test that does not reach it and
breaks in production. This reads every backend file, function-level imports included, and fails in seconds. The
tests are read too: a test file that imports a deleted module fails to collect, so its cases silently stop running.
It checks modules, not names inside them, and resolves `backend.x`, `from backend import x`, `from backend.x
import y` and the relative forms.
"""

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def is_module(parts):
    path = REPO.joinpath(*parts)
    # A directory with no __init__.py is a namespace package (backend/tests, backend/tests/helpers).
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file() or path.is_dir()


def imported_modules(file):
    """(line, dotted module) for each backend module the file imports, wherever the import sits."""
    package = list(file.relative_to(REPO).parts[:-1])
    for node in ast.walk(ast.parse(file.read_text(), filename=str(file))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "backend":
                    yield node.lineno, alias.name.split(".")
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            base = package[: len(package) - node.level + 1]
            module = base + (node.module.split(".") if node.module else [])
        elif node.module and node.module.split(".")[0] == "backend":
            module = node.module.split(".")
        else:
            continue
        if not is_module(module):
            yield node.lineno, module
            continue
        # `from backend import x` and `from . import x` import x as a module when backend/x.py exists, and
        # otherwise as a name of the package. The package __init__ defines no names, so x must be a module.
        if module == ["backend"]:
            for alias in node.names:
                yield node.lineno, [*module, alias.name]


def test_every_backend_import_names_a_module_that_exists():
    from backend.migrations import SUPERSEDED  # recorded and never loaded: they may import what is gone

    missing = [
        f"{file.relative_to(REPO)}:{line}: {'.'.join(module)}"
        for file in sorted(BACKEND.rglob("*.py"))
        if not (file.parent.name == "migrations" and file.stem in SUPERSEDED)
        for line, module in imported_modules(file)
        if not is_module(module)
    ]
    assert not missing, "imports of modules that do not exist:\n" + "\n".join(missing)
