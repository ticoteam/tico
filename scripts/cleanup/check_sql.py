"""Prepare every SQL statement written in the code against a fresh database: a statement naming a missing column fails.

    python3 scripts/cleanup/check_sql.py [--list-unchecked]

A per-table rename cannot be proved by the codemod, and SQL naming a missing column only fails when it runs. This finds
those statements without running them. An f-string field is tried as `?`, as nothing and as `1`; a statement that does
not compile with any of them is counted as unchecked (it needs its field's value), not as a failure.
"""

import argparse
import ast
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIRS = ("backend", "clients", "scripts", "e2e", "runner")
SKIP = ("backend/migrations/", "backend/tests/fixtures/", "scripts/cleanup/", "backend/tests/test_schema_renames.py",
        "backend/tests/test_hubdb_upgrade")
DML = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH|REPLACE)\b", re.I)
MISSING = re.compile(r"no such column|has no column named", re.I)
FIELDS = ("?", "", "1", "1=1")


def fresh_database(path):
    sys.path.insert(0, str(ROOT))
    from backend.config import Settings
    from backend.store import Store
    Store(Settings(db_path=path)).initialize()


def text_of(node, field):
    """The text of a string expression with each f-string field written as `field`, or None when it has other parts."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else field for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = text_of(node.left, field), text_of(node.right, field)
        if left is None and right is None:
            return None
        return (left if left is not None else field) + (right if right is not None else field)
    return None


def statements(tree):
    """(lineno, node) for the outermost string expressions that start like a statement."""
    inner = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.BinOp, ast.JoinedStr)):
            for child in ast.walk(node):
                if child is not node:
                    inner.add(id(child))
    for node in ast.walk(tree):
        if id(node) in inner or not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            continue
        text = text_of(node, "?")
        if text and DML.match(text) and re.search(r"\b(FROM|INTO|SET|VALUES|SELECT)\b", text, re.I):
            yield node.lineno, node


def verdict(conn, node):
    """'ok', 'missing: <error>' or 'unchecked'."""
    missing = None
    for field in FIELDS:
        text = text_of(node, field)
        try:
            conn.execute("EXPLAIN " + text)
            return "ok", None
        except sqlite3.ProgrammingError:       # it compiled; the bindings are what is missing
            return "ok", None
        except (sqlite3.OperationalError, sqlite3.Warning, ValueError) as e:
            if MISSING.search(str(e)):
                missing = missing or str(e)
    return ("missing", missing) if missing else ("unchecked", None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-unchecked", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "hub.sqlite"
        fresh_database(path)
        conn = sqlite3.connect(path)
        counts = {"ok": 0, "missing": 0, "unchecked": 0}
        files = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.split()
        for rel in files:
            if not rel.startswith(DIRS) or rel.startswith(SKIP):
                continue
            try:
                tree = ast.parse((ROOT / rel).read_text())
            except (SyntaxError, UnicodeDecodeError):
                continue
            for lineno, node in statements(tree):
                result, error = verdict(conn, node)
                counts[result] += 1
                if result == "missing":
                    print(f"{rel}:{lineno}: {error}")
                elif result == "unchecked" and args.list_unchecked:
                    print(f"unchecked {rel}:{lineno}: {text_of(node, '?')[:120]!r}")
        conn.close()
    print(f"{counts['ok']} statements compile, {counts['missing']} name a missing column, "
          f"{counts['unchecked']} could not be checked")
    return 1 if counts["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
