"""No Python file imports a test module (a `test_*.py` under a tests/ folder), so the test selector
(scripts/pr_checks.py) does not run one test file because another one changed.

    python scripts/check_test_imports.py
    python scripts/check_test_imports.py --base REF   only the Python files changed since REF (git diff REF...HEAD)

The --base form is for the merge queue and PR checks: a violation already on REF is not this change's to fix, so the
gate reports only what the change touches. Without it, every tracked file is checked.

Names that tests share live in the helpers/ package beside them: backend/tests/helpers/, setup/tests/helpers/,
runner_support/tests/helpers/. `from backend.tests.test_foo import x` becomes `from backend.tests.helpers.foo import x`,
with x moved there. The imports that stay on purpose are listed in scripts/test-imports-allowed.txt, one
`importer -> module` per line (`*` as the importer allows every importer of that module). Exit status: 0 when clean,
1 with each import listed.
"""

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = "scripts/test-imports-allowed.txt"


def is_test_module(dotted):
    """A test module: its last part is test_*, inside a `tests` package (backend.tests.test_api, setup.tests.test_x)."""
    parts = dotted.split(".")
    return len(parts) >= 2 and parts[-1].startswith("test_") and "tests" in parts[:-1]


def helper_for(dotted):
    """The helpers/ module a test module's shared names belong in."""
    parts = dotted.split(".")
    return ".".join([*parts[:-1], "helpers", parts[-1][len("test_") :]])


def package_of(path):
    """The dotted package a file sits in, for its relative imports."""
    return ".".join(Path(path).with_suffix("").parts[:-1])


def imported(path, tree):
    """(line, module) for every test module the file imports, at any depth (a function-level import counts too)."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name) for a in node.names if is_test_module(a.name)]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                package = package_of(path).split(".")
                package = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                base = ".".join(p for p in [*package, base] if p)
            if is_test_module(base):
                found.append((node.lineno, base))
            else:  # from backend.tests import test_api
                found += [(node.lineno, f"{base}.{a.name}") for a in node.names if is_test_module(f"{base}.{a.name}")]
    return found


def allowed(root=ROOT):
    pairs = set()
    path = Path(root) / ALLOWED
    for line in path.read_text().splitlines() if path.is_file() else []:
        line = line.split("#", 1)[0].strip()
        if line:
            importer, _, module = (part.strip() for part in line.partition("->"))
            pairs.add((importer, module))
    return pairs


def changed_since(base, root=ROOT):
    """The Python files the diff from BASE to HEAD touches (committed work, as the merge queue's merge commit does)."""
    out = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=d", f"{base}...HEAD", "--", "*.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split()


def problems(root=ROOT, files=None):
    root = Path(root)
    if files is None:
        files = subprocess.run(
            ["git", "ls-files", "*.py"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.split()
    allow = allowed(root)
    out = []
    for path in sorted(files):
        try:
            tree = ast.parse((root / path).read_text(), path)
        except (OSError, SyntaxError, ValueError):
            continue
        for line, module in sorted(set(imported(path, tree))):
            if (path, module) in allow or ("*", module) in allow:
                continue
            out.append(
                f"{path}:{line} imports the test module {module}: import from {helper_for(module)} instead "
                f"(move the shared names there), or list `{path} -> {module}` in {ALLOWED} if it stays on purpose"
            )
    return out


def main(argv=None, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--base", metavar="REF", help="check only the Python files changed since REF (e.g. origin/main)"
    )
    args = parser.parse_args(argv)
    files = None
    if args.base:
        exists = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if exists.returncode != 0:
            sys.exit(f"--base {args.base}: no such commit in this checkout (fetch it first)")
        files = changed_since(args.base, root)
    found = problems(root, files)
    for line in found:
        print(line)
    if found:
        print(
            f"{len(found)} import(s) of a test module: the test selector would run one test file for another's change"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
