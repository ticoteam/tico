"""Every table the source can create, read from the source: a dump of a fresh hub misses the ones made on first use.

Used by backend/tests/test_schema_every_table.py and scripts/cleanup/gen_upgrade_fixture.py.
"""

import ast
import re
import sqlite3
from pathlib import Path

CREATE = re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)\"?\s*\(", re.I)
ALTER = re.compile(r"\bALTER\s+TABLE\s+\"?(\w+)\"?\s+ADD\s+COLUMN\s+\"?(\w+)\"?\s*([^;\"]*)", re.I)
# Files under backend/ whose SQL is about another database, or about the old shape of one.
NOT_THE_HUB = ("backend/tests/", "backend/migrations/")


def _texts(tree):
    """Every string in a module; an f-string's fields are NUL, so a statement built around one is left out."""
    inner = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            inner.update(id(child) for child in ast.walk(node) if child is not node)
    for node in ast.walk(tree):
        if id(node) in inner:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, ast.JoinedStr):
            yield "".join(v.value if isinstance(v, ast.Constant) else "\0" for v in node.values)


def _closing(text, start):
    """The index just after the `)` that closes the `(` at `start`, or -1."""
    depth, i, n = 0, start, len(text)
    while i < n:
        ch = text[i]
        if ch == "'":
            i = text.find("'", i + 1)
            if i < 0:
                return -1
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if not depth:
                return i + 1
        i += 1
    return -1


def statements(root, skip=()):
    """(creates, columns) in the Python under backend/ at `root`: creates {table: [CREATE TABLE statement]}, columns
    [(table, column, declaration)] from add_column calls and ALTER TABLE ... ADD COLUMN."""
    creates, columns = {}, []
    root = Path(root)
    for path in sorted((root / "backend").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(NOT_THE_HUB) or rel in skip:
            continue
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for text in _texts(tree):
            for m in CREATE.finditer(text):
                end = _closing(text, m.end() - 1)
                sql = text[m.start():end] if end > 0 else ""
                if sql and "\0" not in sql:
                    creates.setdefault(m.group(1), []).append(sql)
            for m in ALTER.finditer(text):
                declaration = m.group(3).strip().rstrip(";,")
                if "\0" not in m.group(0):
                    columns.append((m.group(1), m.group(2), declaration))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "add_column" \
                    and len(node.args) >= 4 and all(isinstance(a, ast.Constant) for a in node.args[1:4]):
                columns.append((node.args[1].value, node.args[2].value, node.args[3].value))
    return creates, columns


def table_columns(conn):
    """{table: {column names}} of the real tables; FTS shadow tables and sqlite_ tables left out."""
    rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()
    virtual = {name for name, sql in rows if sql and sql.upper().startswith("CREATE VIRTUAL")}
    shadow = {f"{v}_{s}" for v in virtual for s in ("data", "idx", "content", "docsize", "config")}
    return {name: {r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')}
            for name, _sql in rows if not name.startswith("sqlite_") and name not in shadow and name not in virtual}


def add_lazy_tables(conn, creates, columns, skip=()):
    """Make every table and column the source can make on first use that this database does not have yet."""
    have = table_columns(conn)
    for table, found in creates.items():
        if table not in have and table not in skip:
            conn.execute(found[0])
    have = table_columns(conn)
    for table, column, declaration in columns:
        if table in have and column not in have[table] and table not in skip:
            try:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {declaration}')
            except sqlite3.OperationalError:            # a NOT NULL without a default: only its name matters here
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}"')
            have[table].add(column)
