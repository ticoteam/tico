#!/usr/bin/env python3
"""Finance's tables belong to backend/finance: everything else writes through finance/actions.py and reads through
finance/reads.py. This fails when Python outside backend/finance writes a fin_* table, reads a fin_* business table
(reading an action's or a connection's status is allowed), names a fin_* table in quotes, or imports Finance's inner
layers.

    python3 scripts/finance_lint.py            # exit 1 and one line per problem
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN = ("backend", "clients", "connectors", "integrations", "scripts", "tools", "runner")
SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".venv", "venv"}
OWNED = ("backend/finance/",)
EXEMPT = ("scripts/finance_lint.py",)
EXEMPT_PREFIX = ("backend/tests/test_finance_",)
READ_OK = {"fin_actions", "fin_connections"}
WRITE = re.compile(r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM|ALTER\s+TABLE|"
                   r"DROP\s+(?:TABLE|INDEX|TRIGGER)(?:\s+IF\s+EXISTS)?|CREATE\s+(?:TABLE|INDEX|TRIGGER))\s+"
                   r"(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?(fin_\w+)", re.IGNORECASE)
READ = re.compile(r"\b(?:FROM|JOIN)\s+[\"`]?(fin_\w+)", re.IGNORECASE)
# A Finance table named in quotes outside Finance is a list that a delete loop could run (devcopy once had one).
NAMED = re.compile(r"[\"'`](fin_\w+)[\"'`]")
INNER = re.compile(r"(?:from|import)\s+(?:backend\.|\.+)?finance\.(core|store|handlers|budgeting|posting|context|setup)\b")
# The migration step creates the tables through the schema module; nothing else reaches past the two doors.
INNER_OK = {("backend/store.py", "store")}


def problems(root: Path = ROOT, files: list[Path] | None = None) -> list[str]:
    out: list[str] = []
    for path in files if files is not None else _files(root):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(OWNED) or rel in EXEMPT or rel.startswith(EXEMPT_PREFIX):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for m in WRITE.finditer(line):
                out.append(f"{rel}:{n}: writes {m.group(1)}; go through backend/finance/actions.py")
            for m in READ.finditer(line):
                if m.group(1) not in READ_OK:
                    out.append(f"{rel}:{n}: reads {m.group(1)}; go through backend/finance/reads.py")
            for m in NAMED.finditer(line):
                out.append(f"{rel}:{n}: names {m.group(1)}; Finance's tables are named in backend/finance/ only")
            for m in INNER.finditer(line):
                if (rel, m.group(1)) not in INNER_OK:
                    out.append(f"{rel}:{n}: imports finance.{m.group(1)}; use finance.actions or finance.reads")
    return out


def _files(root: Path) -> list[Path]:
    found: list[Path] = []
    for top in SCAN:
        base = root / top
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if not SKIP_DIRS.intersection(path.relative_to(root).parts):
                found.append(path)
    return sorted(found)


def main() -> int:
    found = problems()
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
