"""Only backend/flight.py writes SQL against the flight recorder's tables (flight_events, flight_requests,
flight_process, flight_db, ...). Health, invariants and maintenance read them through its named helpers, so moving the
tables to their own database file is a change inside flight.py, plus the one reader named in ELSEWHERE."""

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# A statement naming a flight table, or a sqlite_master lookup of one. A docstring that mentions a table is not SQL.
RAW = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+flight_\w+|\bname\s*=\s*'flight_\w+'", re.I)
# The recorder itself and schema history; tests seed the tables on purpose.
OWNERS = ("backend/flight.py", "backend/migrations/")
SKIP_DIRS = {".git", ".venv", "node_modules", "build", "target", "tests", "__pycache__"}
# Readers that cannot call flight.py, each with the line count it holds. The cell host's stats push runs a fixed
# stdlib script inside each company's server container (`python -I`, no backend import) that opens the database file
# by path: moving flight_sql to its own file must change that path there too. Its two lines: the query, and the check
# that the table is there (a server on this code that has not made its flight file yet has it in neither database).
ELSEWHERE = {"setup/cell_stats.py": 2}


def raw_flight_sql():
    found = {}
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for file in files:
            if not file.endswith(".py"):
                continue
            path = Path(folder, file)
            name = path.relative_to(ROOT).as_posix()
            if name.startswith(OWNERS):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if RAW.search(line):
                    found.setdefault(name, []).append(f"{number}: {line.strip()}")
    return found


def test_no_module_but_flight_py_queries_a_flight_table():
    found = raw_flight_sql()
    assert {name: len(lines) for name, lines in found.items()} == ELSEWHERE, found


def test_the_pattern_matches_the_query_shapes_it_guards_and_not_prose():
    assert RAW.search("SELECT count(*) FROM flight_events WHERE kind='long_hold'")
    assert RAW.search("SELECT 1 FROM sqlite_master WHERE type='table' AND name='flight_requests'")
    assert RAW.search('f"FROM flight_process WHERE created_at>=?"')
    assert not RAW.search('"""Write-lock holds in the last hour (flight_events, kind long_hold)."""')
