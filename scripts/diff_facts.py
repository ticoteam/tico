#!/usr/bin/env python3
"""Dependency-free diff facts shared by verification gates.

`diff_facts(base, head="HEAD", repo=ROOT)` returns JSON-compatible facts for the
exact two commits, including a low/normal/high tier. The CLI takes --base REF
and optionally --head REF. It reads committed content, not the working tree.
Python hunks compare tokens with ordinary strings and comments removed; AST
context identifies SQL. Other code is conservatively treated as code.
`deleted_recent(base, days=14, ...)` blames only deleted ranges on that base.
"""

import argparse
import ast
from dataclasses import asdict, dataclass, field
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import tokenize

ROOT = Path(__file__).resolve().parents[1]
SQL_START = re.compile(r"^\s*\(?\s*(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|ALTER|DROP|REPLACE)\b", re.IGNORECASE)
SQL_CALLS = {"execute", "executescript", "executemany"}
LAYOUT = {tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    added: list[int] = field(default_factory=list)
    deleted: list[int] = field(default_factory=list)
    strings_only: bool = False  # Includes comments, even when there is no string.
    sql: bool = False
    copy_only: bool = False


@dataclass
class FileFacts:
    path: str
    kind: str
    status: str
    binary: bool = False
    added: list[int] = field(default_factory=list)
    deleted: list[int] = field(default_factory=list)
    hunks: list[Hunk] = field(default_factory=list)


def kind_of(path):
    """Classify sensitive paths before tests and documentation under them."""
    name = Path(path).name
    for prefix, kind in (
        ("backend/migrations/", "migration"),
        (".github/workflows/", "workflow"),
        ("scripts/rules/", "rules"),
        ("scripts/gates/", "gates"),
    ):
        if path.startswith(prefix):
            return kind
    if path in {"scripts/check.sh", "scripts/gates.sh"}:
        return "gates"
    if (
        re.search(r"(^|/)tests?/", path)
        or re.match(r"test_.*\.py$", name)
        or name.endswith("_test.py")
        or name == "conftest.py"
        or re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", name)
    ):
        return "test"
    if path.startswith("docs/") or name.endswith((".md", ".mdx", ".rst")):
        return "docs"
    return "code"


def git(repo, *args):
    return subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    ).stdout


def parse_hunks(patch, facts):
    """Parse only hunk bodies; paths come from NUL-delimited name-status output."""
    hunk = None
    old_line = new_line = 0
    for line in patch.split("\n"):
        match = HUNK.match(line)
        if match:
            old_start, old_count, new_start, new_count = match.groups()
            hunk = Hunk(int(old_start), int(old_count or 1), int(new_start), int(new_count or 1))
            facts.hunks.append(hunk)
            old_line, new_line = hunk.old_start, hunk.new_start
        elif hunk is not None:
            if line.startswith("+"):
                hunk.added.append(new_line)
                facts.added.append(new_line)
                new_line += 1
            elif line.startswith("-"):
                hunk.deleted.append(old_line)
                facts.deleted.append(old_line)
                old_line += 1
        elif line.startswith("Binary files "):
            facts.binary = True


def _span(node):
    return (node.lineno, node.col_offset), (node.end_lineno, node.end_col_offset)


def _contains(node, token):
    return _span(node)[0] <= token.start and token.end <= _span(node)[1]


def _python(source):
    """Tokens, ordinary literals and SQL spans, or None for unparseable Python."""
    try:
        tree = ast.parse(source)
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (SyntaxError, ValueError, tokenize.TokenError):
        return None
    # AST columns count UTF-8 bytes; tokenize columns count characters.
    lines = source.split("\n")
    for node in ast.walk(tree):
        for prefix in ("", "end_"):
            number = getattr(node, prefix + "lineno", None)
            if number is not None:
                column = getattr(node, prefix + "col_offset")
                text = lines[number - 1].encode("utf-8")[:column].decode("utf-8")
                setattr(node, prefix + "col_offset", len(text))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    strings = []
    sql = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            if isinstance(parents.get(node), ast.JoinedStr):
                continue
            value = node.value.decode(errors="replace") if isinstance(node.value, bytes) else node.value
            strings.append(node)
        elif isinstance(node, ast.JoinedStr):
            # Interpolation is code; only ordinary literals are removed from the token comparison.
            value = node.values[0].value if node.values and isinstance(node.values[0], ast.Constant) else ""
        else:
            continue
        is_sql = bool(SQL_START.match(value))
        child = node
        while child in parents:
            parent = parents[child]
            if isinstance(parent, ast.Call):
                func = parent.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                if name in SQL_CALLS and child is not func:
                    is_sql = True
            if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and child is parent.value:
                targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
                if any(
                    isinstance(t, ast.Name) and t.id.endswith("_SQL") for target in targets for t in ast.walk(target)
                ):
                    is_sql = True
            child = parent
        if is_sql:
            sql.append(node)
    return tokens, strings, sql


def _python_side(parsed, source, numbers):
    """Nonliteral tokens and SQL tokens on changed lines, retaining code indentation."""
    tokens, strings, sql_nodes = parsed
    lines = source.split("\n")
    skeleton = []
    sql = []
    for tok in tokens:
        if not any(tok.start[0] <= number <= tok.end[0] for number in numbers):
            continue
        if tok.type == tokenize.COMMENT or tok.type in LAYOUT:
            continue
        if any(_contains(node, tok) for node in sql_nodes):
            sql.append((tok.type, tok.string))
        if tok.type == tokenize.STRING and any(_contains(node, tok) for node in strings):
            continue
        line = lines[tok.start[0] - 1] if tok.start[0] <= len(lines) else ""
        indent = line[: len(line) - len(line.lstrip())]
        skeleton.append((indent, tok.type, tok.string))
    return skeleton, sql


def _tier(files):
    if any(item.kind in {"rules", "gates", "workflow", "migration"} for item in files):
        return "high"
    if all(
        item.kind in {"docs", "test"} or (not item.binary and item.hunks and all(hunk.copy_only for hunk in item.hunks))
        for item in files
    ):
        return "low"
    return "normal"


def diff_facts(base, head="HEAD", repo=ROOT):
    """JSON-compatible facts for the exact base and head commits; renames are deletion plus addition."""
    base = git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
    head = git(repo, "rev-parse", "--verify", f"{head}^{{commit}}").strip()
    changes = git(repo, "diff", "--no-ext-diff", "--no-renames", "--name-status", "-z", base, head, "--").split("\0")[
        :-1
    ]
    files = []
    for status, path in zip(changes[::2], changes[1::2], strict=True):
        facts = FileFacts(path, kind_of(path), {"A": "added", "D": "deleted"}.get(status, "modified"))
        patch = git(
            repo, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "-U0", base, head, "--", path
        )
        parse_hunks(patch, facts)
        if path.endswith(".py") and facts.hunks and not facts.binary:
            old = "" if status == "A" else git(repo, "show", f"{base}:{path}")
            new = "" if status == "D" else git(repo, "show", f"{head}:{path}")
            old_parsed, new_parsed = _python(old), _python(new)
            if old_parsed is not None and new_parsed is not None:
                for hunk in facts.hunks:
                    before, old_sql = _python_side(old_parsed, old, hunk.deleted)
                    after, new_sql = _python_side(new_parsed, new, hunk.added)
                    hunk.strings_only = before == after
                    hunk.sql = old_sql != new_sql
                    hunk.copy_only = hunk.strings_only and not hunk.sql
        files.append(facts)
    return {"base": base, "head": head, "tier": _tier(files), "files": [asdict(item) for item in files]}


def _ranges(numbers):
    runs = []
    for number in sorted(set(numbers)):
        if runs and number == runs[-1][1] + 1:
            runs[-1][1] = number
        else:
            runs.append([number, number])
    return runs


def deleted_recent(base, days=14, head="HEAD", repo=ROOT, now=None):
    """Deleted lines younger than days, by committer time on base, blaming only deleted ranges.

    Each result has path, line (the base line number), text, commit and age_days.
    `now` is an optional Unix timestamp for callers using a fixed clock.
    """
    now = time.time() if now is None else now
    facts = diff_facts(base, head, repo)
    found = []
    for item in facts["files"]:
        if item["binary"] or not item["deleted"]:
            continue
        ranges = [arg for first, last in _ranges(item["deleted"]) for arg in ("-L", f"{first},{last}")]
        out = git(repo, "blame", "--porcelain", *ranges, facts["base"], "--", item["path"])
        times = {}
        commit = None
        line = 0
        for row in out.split("\n"):
            if row.startswith("\t"):
                age = (now - times[commit]) / 86400
                if age < days:
                    found.append(
                        {
                            "path": item["path"],
                            "line": line,
                            "text": row[1:],
                            "commit": commit,
                            "age_days": round(age, 2),
                        }
                    )
                continue
            parts = row.split(" ")
            if len(parts) >= 3 and re.fullmatch(r"[0-9a-f]{40,64}", parts[0]):
                commit, line = parts[0], int(parts[2])
            elif row.startswith("committer-time "):
                times[commit] = int(parts[1])
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    try:
        facts = diff_facts(args.base, args.head)
    except subprocess.CalledProcessError as error:
        print(f"diff_facts: {error.stderr.strip()}", file=sys.stderr)
        return 2
    json.dump(facts, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
