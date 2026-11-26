"""Only backend/flight.py writes SQL against the flight recorder's tables (flight_events, flight_requests,
flight_process, flight_db, ...). Health, invariants and maintenance read them through its named helpers, so moving the
tables to their own database file is a change inside flight.py alone."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# A statement naming a flight table, or a sqlite_master lookup of one. A docstring that mentions a table is not SQL.
RAW = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+flight_\w+|\bname\s*=\s*'flight_\w+'", re.I)
# The recorder itself; schema history; tests seed the tables on purpose.
OWNERS = ("backend/flight.py", "backend/migrations/", "backend/tests/")


def test_no_backend_module_but_flight_py_queries_a_flight_table():
    found = []
    for path in sorted((ROOT / "backend").rglob("*.py")):
        name = path.relative_to(ROOT).as_posix()
        if name.startswith(OWNERS):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if RAW.search(line):
                found.append(f"{name}:{number}: {line.strip()}")
    assert found == []


def test_the_pattern_matches_the_query_shapes_it_guards_and_not_prose():
    assert RAW.search("SELECT count(*) FROM flight_events WHERE kind='long_hold'")
    assert RAW.search("SELECT 1 FROM sqlite_master WHERE type='table' AND name='flight_requests'")
    assert RAW.search('f"FROM flight_process WHERE created_at>=?"')
    assert not RAW.search('"""Write-lock holds in the last hour (flight_events, kind long_hold)."""')
