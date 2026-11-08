"""Every read of the Directory goes through the one read rule (backend/entities/visible.py), as Finance's does
(backend/tests/test_finance_visible_lint.py). A module outside the store (backend/entities/store.py) and the rule
(visible.py) that selects from a Directory table would skip the rule, so the lint fails and names the file and line.

The new tables (entity_values, entity_notes, entity_sources and the rest) are held to the rule everywhere. The graph
tables (entities, entity_links, entity_identifiers, entity_watches) are read by Market and its neighbours today: those
files are listed in LEGACY_GRAPH_READERS and move to the store when they are next touched. A new file that reads the
graph tables fails the lint until it does either."""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
RULE_MODULES = {"entities/store.py", "entities/visible.py"}
EXCLUDED_DIRS = ("migrations", "tests")
NEW_TABLES = re.compile(
    r"\b(from|join)\s+(entity_types|link_types|entity_attributes|entity_values|entity_value_history|"
    r"entity_external_refs|entity_activities|entity_connections|entity_notes|entity_views|entity_mentions|"
    r"entity_suppressions|entity_sources|entity_merges)\b",
    re.IGNORECASE,
)
GRAPH_TABLES = re.compile(r"\b(from|join)\s+(entities|entity_links|entity_identifiers|entity_watches)\b", re.IGNORECASE)
LEGACY_GRAPH_READERS = {
    "lenses.py",
    "librarian.py",
    "market.py",
    "market_spend.py",
    "market_watch.py",
    "strategy_market.py",
    "system_routines.py",
}


def _hits(pattern, allowed):
    out = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if rel.parts[0] in EXCLUDED_DIRS or rel.as_posix() in RULE_MODULES or allowed(rel):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                out.append(f"{rel.as_posix()}:{number}: {line.strip()}")
    return out


def test_no_read_outside_the_rule_selects_a_directory_table():
    assert _hits(NEW_TABLES, allowed=lambda rel: False) == []


def test_no_new_read_of_the_graph_tables_skips_the_rule():
    assert _hits(GRAPH_TABLES, allowed=lambda rel: rel.name in LEGACY_GRAPH_READERS) == []


def test_the_lint_catches_a_read():
    assert NEW_TABLES.search('c.execute("SELECT name FROM entity_notes WHERE entity_id=?")')
    assert NEW_TABLES.search("LEFT JOIN entity_sources s ON s.entity_id = e.id")
    assert GRAPH_TABLES.search('c.execute("SELECT id FROM entities WHERE status=?")')
    assert not GRAPH_TABLES.search("the entities table is named in a comment without a read")
