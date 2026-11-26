"""Persistent schema changes live in backend/migrations, one authority with checksums and a record of what ran.

The code outside it still has CREATE TABLE/INDEX/TRIGGER/VIEW, ALTER TABLE and add_column calls that run at every start.
backend/migrations/ddl_allowlist.txt counts them per file, and the count may only shrink: a file that grows, or a new file
with one, fails and is told to write a migration; an entry above what the code has fails too, so the list follows the code
down. Temporary objects (CREATE TEMP/TEMPORARY, backend/sql.py) never persist and are not counted. Nor is the schema of
a database file of its own (OWN_FILES): it is never the server's database, and it versions itself with PRAGMA
user_version and is remade in place when that changes, so migrations never run on it.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ALLOWLIST = BACKEND / "migrations" / "ddl_allowlist.txt"
# CREATE TEMP and CREATE TEMPORARY are absent by design: they match none of these keywords.
# module: the one module-level constant holding its own file's schema. Every other literal in the module still counts.
OWN_FILES = {"backend/flight.py": "SCHEMA"}  # <hub>.flight.db, the flight recorder's disposable file
DDL = re.compile(r"\bCREATE\s+(?:UNIQUE\s+|VIRTUAL\s+)?(?:TABLE|INDEX|TRIGGER|VIEW)\b|\bALTER\s+TABLE\b")


def _docstrings(tree):
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
    return skip


def _own_schema(tree, name):
    """The literals of the module-level `name = ...` assignment."""
    skip = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            skip.update(id(n) for n in ast.walk(node.value))
    return skip


def count(path, own=None):
    """DDL keywords in the file's string literals (comments and docstrings are prose), plus its add_column calls."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _docstrings(tree) | (_own_schema(tree, own) if own else set())
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
        name = f"backend/{relative.as_posix()}"
        total = count(path, OWN_FILES.get(name))
        if total:
            found[name] = total
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
        "instead: run python3 scripts/new_migration.py name (or add --py for up(conn)), applied once and recorded in "
        "schema_migrations. Do not add to backend/migrations/ddl_allowlist.txt."
    )
    assert not stale, (
        "backend/migrations/ddl_allowlist.txt lists more than the code has (listed, found): " + repr(stale) + ". Lower "
        "those counts: the list only shrinks."
    )


def test_only_the_named_constant_of_an_own_file_module_is_exempt(tmp_path):
    module = tmp_path / "own.py"
    module.write_text('SCHEMA = ("CREATE TABLE a(x)", "CREATE INDEX a_x ON a(x)")\nOTHER = "CREATE TABLE b(x)"\n')
    assert count(module) == 3 and count(module, "SCHEMA") == 1
    flight = BACKEND / "flight.py"
    assert count(flight) > count(flight, OWN_FILES["backend/flight.py"]) == 0  # the name still matches flight.py
