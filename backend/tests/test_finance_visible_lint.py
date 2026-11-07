"""The one view rule for records (backend/finance/visible.py) is the only way a read names a vendor. No module under
backend/finance selects from the contact tables (fin_contacts, fin_vendor_aliases, fin_vendor_labels) outside the store
layer, the one visibility module, and the actions that write (the allowlist below). A read that names a vendor by its
own SQL would skip the rule, so the lint fails naming the file and line."""

import re
from pathlib import Path

FINANCE = Path(__file__).resolve().parents[1] / "finance"
CONTACT_TABLES = re.compile(r"\b(from|join)\s+fin_(contacts|vendor_aliases|vendor_labels)\b", re.IGNORECASE)
# Store modules read the tables for everyone (the store is the only SQL home); visible.py decides what shows.
ALLOWED_DIRS = ("store",)
# Action modules: they read a contact to validate a write (not to show it). attribution.py checks that a rule's vendor
# exists before it saves the rule.
ALLOWED_FILES = {"visible.py", "attribution.py"}


def _hits() -> list[str]:
    out: list[str] = []
    for path in sorted(FINANCE.rglob("*.py")):
        rel = path.relative_to(FINANCE)
        if rel.parts[0] in ALLOWED_DIRS or rel.name in ALLOWED_FILES:
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if CONTACT_TABLES.search(line):
                out.append(f"{rel}:{number}: {line.strip()}")
    return out


def test_no_read_outside_the_store_selects_the_contact_tables():
    assert _hits() == []


def test_the_lint_catches_a_contact_read():
    assert CONTACT_TABLES.search('c.execute("SELECT name FROM fin_contacts WHERE book_id=?")')
    assert CONTACT_TABLES.search("LEFT JOIN fin_vendor_aliases a ON a.name_key=x")
    assert not CONTACT_TABLES.search("the contact table fin_contacts is named in a comment")
