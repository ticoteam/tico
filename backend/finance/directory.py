"""What the Directory (backend/entities) asks of Finance's contacts, outside Finance's actions and reads."""

import json
import sqlite3
from collections.abc import Iterable, Sequence

from .. import hubdb as H
from .store import contacts as SC


def link_self(c: sqlite3.Connection) -> None:
    """Every contact's vendor_to / customer_of links to self, under the ids store/contacts.py link gives them. The
    Directory calls it when self is made or first flagged (contacts made or folded in before self have none yet). One
    statement for the links and one read of self's links, so it stays cheap on a big book; a link already there stays.
    A no-op without Finance's book rows or self."""
    has = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fin_book_entities'").fetchone()
    me = c.execute("SELECT id FROM entities WHERE self = 1").fetchone()
    if has is None or me is None:
        return
    me = str(me[0])
    now = H.now()  # the store's clock, which a demo's clock moves too
    pairs = [(kind, key) for kind, keys in SC.LINKS.items() for key in keys]
    values = ", ".join("(?, ?)" for _ in pairs)
    c.execute(
        f"WITH k(kind, link_key) AS (VALUES {values}) "
        "INSERT OR IGNORE INTO entity_links(id, from_entity_id, link_key, to_entity_id, confidence, source_type, "
        "source_id, properties, created_by, created_by_type, created_by_id, created_at, updated_at) "
        "SELECT 'lnk_finance_' || b.id || '_' || k.link_key, b.entity_id, k.link_key, ?, 0.9, ?, b.id, '{}', "
        "b.created_by, 'system', '', ?, ? FROM fin_book_entities b JOIN k ON k.kind = b.kind WHERE b.entity_id <> ?",
        [*(v for pair in pairs for v in pair), me, SC.SOURCE, now, now, me],
    )
    held: dict[str, set[str]] = {}
    for from_id, key in c.execute(
        "SELECT from_entity_id, link_key FROM entity_links WHERE to_entity_id = ? "
        "AND (valid_until_on IS NULL OR valid_until_on > ?)",
        (me, now[:10]),
    ):
        held.setdefault(str(from_id), set()).add(str(key))
    entities = {
        str(r[0]) for r in c.execute("SELECT DISTINCT entity_id FROM fin_book_entities WHERE entity_id <> ?", (me,))
    }
    c.executemany(
        "UPDATE entities SET self_link_keys = ? WHERE id = ? AND self_link_keys IS NOT ?",
        [(" ".join(sorted(held.get(e, ()))), e, " ".join(sorted(held.get(e, ())))) for e in sorted(entities)],
    )


def kept_for_books(c: sqlite3.Connection, entity_ids: Sequence[str], handled: Iterable[str] = ()) -> list[str]:
    """What keeps these entities for the books: a label for each table with a foreign key to entities(id) that names one
    of them ("contacts", "bills", "invoices", "projects", ...), found by PRAGMA foreign_key_list, so a table added later
    counts too. The Directory's own tables (entities, entity_*) and the ones `handled` lists (those an erase clears
    itself) are left out. A ledger's counterparty is kept for the accounting, so the Directory does not erase it."""
    if not entity_ids:
        return []
    ids = json.dumps(list(entity_ids))
    skip = set(handled)
    found: list[str] = []
    tables = [str(r[0]) for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for table in tables:
        if table == "entities" or table.startswith("entity_") or table in skip:
            continue
        for fk in c.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
            if fk[2] != "entities":
                continue
            if c.execute(
                f'SELECT 1 FROM "{table}" WHERE "{fk[3]}" IN (SELECT value FROM json_each(?)) LIMIT 1', (ids,)
            ).fetchone():
                label = LABELS.get(table, table.removeprefix("fin_").replace("_", " "))
                if label not in found:
                    found.append(label)
    return sorted(found, key=lambda label: (ORDER.get(label, len(ORDER)), label))


LABELS: dict[str, str] = {"fin_book_entities": "contacts", "fin_purchase_orders": "purchase orders"}
ORDER = {
    label: n for n, label in enumerate(("contacts", "bills", "invoices", "estimates", "purchase orders", "projects"))
}
