"""Persistent schema changes live in backend/migrations, one authority with checksums and a record of what ran.

The code outside it still has CREATE TABLE/INDEX/TRIGGER/VIEW, ALTER TABLE and add_column calls that run at every start.
backend/migrations/ddl_allowlist.txt counts them per file, and the count may only shrink: a file that grows, or a new file
with one, fails and is told to write a migration; an entry above what the code has fails too, so the list follows the code
down. Temporary objects (CREATE TEMP/TEMPORARY, backend/sql.py) never persist and are not counted.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ALLOWLIST = BACKEND / "migrations" / "ddl_allowlist.txt"
# CREATE TEMP and CREATE TEMPORARY are absent by design: they match none of these keywords.
DDL = re.compile(r"\bCREATE\s+(?:UNIQUE\s+|VIRTUAL\s+)?(?:TABLE|INDEX|TRIGGER|VIEW)\b|\bALTER\s+TABLE\b")


def _docstrings(tree):
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
    return skip


def count(path):
    """DDL keywords in the file's string literals (comments and docstrings are prose), plus its add_column calls."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _docstrings(tree)
    total = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            total += len(DDL.findall(node.value))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            total += name == "add_column"
    return total


def scan(root=BACKEND):
    """{repo-relative path: count} for every backend module outside the tests and the migrations package."""
    found = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if "tests" in relative.parts or relative.parts[0] == "migrations":
            continue
        total = count(path)
        if total:
            found[f"backend/{relative.as_posix()}"] = total
    return found


def allowlist():
    entries = {}
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        path, _, total = line.rpartition(":")
        assert path and total.isdigit(), f"{ALLOWLIST.name}: not path:count: {line!r}"
        entries[path] = int(total)
    return entries


def test_persistent_ddl_outside_migrations_only_shrinks():
    found, allowed = scan(), allowlist()
    grew = {path: (allowed.get(path, 0), total) for path, total in found.items() if total > allowed.get(path, 0)}
    stale = {path: (total, found.get(path, 0)) for path, total in allowed.items() if found.get(path, 0) < total}
    assert not grew, (
        "Persistent schema change outside backend/migrations (allowed, found): " + repr(grew) + ". Write a migration "
        "instead: a new backend/migrations/NNNN_name.sql (or .py with up(conn)), applied once and recorded in "
        "schema_migrations. Do not add to backend/migrations/ddl_allowlist.txt.")
    assert not stale, (
        "backend/migrations/ddl_allowlist.txt lists more than the code has (listed, found): " + repr(stale) + ". Lower "
        "those counts: the list only shrinks.")
