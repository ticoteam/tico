"""Finance's contacts on the Directory (spec §3 "Fold in"): every vendor and customer is an organization entity, and what
belongs to one book stays in Finance's fin_book_entities (kind, billing emails, default account, 1099 and tax refs,
payment ref, active, seats, renewal date). A contact's id is its book row's id, and that id is an entity id too: the
entity itself, or a merged entity that redirects to it when the entity was in another book first or came from the
Directory. Bills, invoices, estimates, purchase orders and projects reference entities(id) and keep the contact id.

`fin_contacts` and `fin_vendor_aliases` are read-only views (store/schema.py VIEWS): every read keeps its SQL. Writes come
here:

- create: a new contact. A billing email that names exactly one live organization (not self) that has a source and that
  this book does not hold yet reuses that entity (the contact id becomes a merged entity redirecting to it); otherwise a
  new organization is made under the contact id. An entity with no sources shows to everyone, and a restricted source
  would narrow it, so it is never reused. The entity gets the emails as identifiers, a restricted finance_book source
  (so a book-only contact shows in the Directory only to readers who see a name for it in Finance, finance/visible.py),
  and vendor_to / customer_of links to self.
- names: the book row keeps the name the book uses (fin_contacts reads it, falling back to the entity's). The entity's
  own name changes only with the contact whose id is the entity, never from a book that reuses it.
- update: the book row's name and fields, the identifiers and links that follow the emails and the kind.
- aliases: a vendor's names in one book are entity_identifiers of kind name scoped to the book (scope_type
  finance_book), unique per book and name. merged_from rides on source_type/source_id (finance_merge).

Callers check rights and views themselves, as with the rest of the store.
"""

import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .ledger import new_id

SOURCE = "finance_book"  # entity_sources.source_type; source_id is the fin_book_entities row's id
ALIAS_SCOPE = "finance_book"  # entity_identifiers.scope_type of a book's vendor names; scope_id is the book's id
ALIAS = "finance_alias"  # source_type of a name alias that no merge added (merged_from NULL)
MERGE_ALIAS = "finance_merge"  # source_type of a name alias a vendor_merge added; source_id is merged_from
LINKS = {"vendor": ("vendor_to",), "customer": ("customer_of",), "both": ("customer_of", "vendor_to")}
BOOK_FIELDS = (
    "name",
    "kind",
    "emails",
    "default_account_id",
    "is_1099",
    "tax_id_ref",
    "tax_id_last4",
    "payment_ref",
    "active",
    "seats",
    "renewal_on",
)


def _emails(raw: object) -> list[str]:
    """A contact's emails as stored (a JSON list); anything else reads as none."""
    try:
        found = json.loads(str(raw or "[]"))
    except ValueError:
        return []
    if not isinstance(found, list):
        return []
    return [str(e).strip().lower() for e in found if str(e).strip()]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def book_row(c: sqlite3.Connection, book_id: str, entity_id: str) -> Any:
    return c.execute("SELECT * FROM fin_book_entities WHERE book_id=? AND entity_id=?", (book_id, entity_id)).fetchone()


def book_rows(c: sqlite3.Connection, ids: Sequence[str]) -> list[Any]:
    """fin_book_entities rows by their own ids (an entity source's id): (id, book_id, entity_id)."""
    if not ids:
        return []
    return c.execute(
        "SELECT id, book_id, entity_id FROM fin_book_entities WHERE id IN (SELECT value FROM json_each(?))",
        (json.dumps(list(ids)),),
    ).fetchall()


def _reusable(c: sqlite3.Connection, book_id: str, emails: Sequence[str]) -> str:
    """The one live organization these emails name, when there is exactly one, it is not self, it has a source and this
    book does not hold it yet; else ""."""
    if not emails:
        return ""
    from backend.entities import store as ES

    found = ES.identifier_entities(c, [("email", e) for e in emails])
    ids = sorted({i for hits in found.values() for i in hits})
    if len(ids) != 1:
        return ""
    facts = ES.entity_facts(c, ids)
    if facts.get(ids[0], {}).get("type") != "organization" or ids[0] == ES.self_id(c):
        return ""
    if not ES.has_sources(c, ids[0]):
        return ""
    return "" if book_row(c, book_id, ids[0]) is not None else ids[0]


def _identify(c: sqlite3.Connection, entity_id: str, row_id: str, emails: Sequence[str]) -> None:
    """The entity's email identifiers from this book row: the ones it no longer lists go, new ones are added."""
    c.execute(
        "DELETE FROM entity_identifiers WHERE entity_id=? AND kind='email' AND source_type=? AND source_id=?",
        (entity_id, SOURCE, row_id),
    )
    for n, email in enumerate(dict.fromkeys(emails)):
        c.execute(
            "INSERT INTO entity_identifiers(entity_id, kind, value, normalized_value, primary_identifier, source_type, "
            "source_id) VALUES(?,?,?,?,?,?,?)",
            (entity_id, "email", email, email, 1 if n == 0 else 0, SOURCE, row_id),
        )
    if emails:
        c.execute(
            "UPDATE entities SET primary_email=? WHERE id=? AND (primary_email IS NULL OR primary_email='')",
            (emails[0], entity_id),
        )


def link(c: sqlite3.Connection, entity_id: str, row_id: str, kind: str, at: str, by: str) -> None:
    """vendor_to / customer_of from the entity to self, resting on this book row; a link the kind no longer has goes."""
    from backend.entities import store as ES

    self_id = ES.self_id(c)
    keys = LINKS.get(kind, ())
    c.execute(
        "DELETE FROM entity_links WHERE source_type=? AND source_id=? AND from_entity_id=? "
        "AND link_key NOT IN (SELECT value FROM json_each(?))",
        (SOURCE, row_id, entity_id, json.dumps(list(keys))),
    )
    if self_id is None or self_id == entity_id:
        return
    for key in keys:
        c.execute(
            "INSERT OR IGNORE INTO entity_links(id, from_entity_id, link_key, to_entity_id, confidence, source_type, "
            "source_id, properties, created_by, created_by_type, created_by_id, created_at, updated_at) "
            "VALUES(?,?,?,?,0.9,?,?,'{}',?,'system','',?,?)",
            (f"lnk_finance_{row_id}_{key}", entity_id, key, self_id, SOURCE, row_id, by, at, at),
        )
    keys_now = sorted(
        {
            str(r[0])
            for r in c.execute(
                # +to_entity_id: every Finance link points at self, so the to index would scan them all
                "SELECT link_key FROM entity_links WHERE from_entity_id=? AND +to_entity_id=? "
                "AND (valid_until_on IS NULL OR valid_until_on > ?)",
                (entity_id, self_id, at[:10]),
            )
        }
    )
    c.execute("UPDATE entities SET self_link_keys=? WHERE id=?", (" ".join(keys_now), entity_id))


def _redirect(c: sqlite3.Connection, row_id: str, entity_id: str, name: str, at: str, by: str) -> None:
    """`row_id` as a merged entity that redirects to `entity_id`, logged as a merge, so the contact id resolves."""
    c.execute(
        "INSERT OR IGNORE INTO entities(id, type, name, name_key, status, merged_into_id, created_by, created_by_type, "
        "created_by_id, created_at, updated_at) VALUES(?, 'organization', ?, lower(trim(?)), 'merged', ?, ?, 'system', '', "
        "?, ?)",
        (row_id, name, name, entity_id, by, at, at),
    )
    prior = {
        "drop": {"status": "active", "merged_into_id": None, "mentions_count": 0},
        "keep": {"mentions_count": 0, "last_activity_at": None},
    }
    c.execute(
        "INSERT OR IGNORE INTO entity_merges(id, entity_id, into_entity_id, moved, prior, actor_type, actor_id, "
        "created_at) VALUES(?,?,?,?,?,'system','',?)",
        (
            f"mrg_finance_{row_id}",
            row_id,
            entity_id,
            json.dumps({"links": [], "tables": {}, "values": []}, sort_keys=True),
            json.dumps(prior, sort_keys=True),
            at,
        ),
    )


def create(
    c: sqlite3.Connection, book_id: str, fields: Mapping[str, object], at: str, by: str, contact_id: str = ""
) -> str:
    """A new contact in `book_id` (fields as fin_contacts had them: name, kind, emails JSON and the book fields). Returns
    its id (`contact_id` when given, else a new one), which is an entity id: a new organization's, or a redirect to the
    one live organization its emails name."""
    name = str(fields.get("name") or "").strip()
    emails = _emails(fields.get("emails"))
    row_id = contact_id or new_id()
    taken = c.execute("SELECT 1 FROM entities WHERE id=?", (row_id,)).fetchone() is not None
    entity_id = "" if taken else _reusable(c, book_id, emails)
    if entity_id:
        _redirect(c, row_id, entity_id, name, at, by)
    else:
        entity_id = row_id
        if not taken:
            c.execute(
                "INSERT INTO entities(id, type, name, name_key, primary_email, created_by, created_by_type, "
                "created_by_id, created_at, updated_at) VALUES(?, 'organization', ?, lower(trim(?)), ?, ?, 'system', '', "
                "?, ?)",
                (entity_id, name, name, emails[0] if emails else None, by, at, at),
            )
            c.execute(
                "INSERT OR IGNORE INTO entity_values(id, entity_id, entity_attribute_id, single_value, option_key, "
                "source_type, source_id, confidence, updated_at) VALUES(?, ?, 'attr_organization_kind', 1, 'company', "
                "?, ?, 0.9, ?)",
                (f"val_finance_{row_id}", entity_id, SOURCE, row_id, at),
            )
    book = {k: fields[k] for k in BOOK_FIELDS if k in fields}
    book["name"] = name
    book.setdefault("emails", json.dumps(emails))
    cols = ["id", "book_id", "entity_id", "created_at", "created_by", "updated_at", *sorted(book)]
    values = [row_id, book_id, entity_id, at, by, at, *(book[k] for k in sorted(book))]
    c.execute(f"INSERT INTO fin_book_entities({','.join(cols)}) VALUES({','.join('?' * len(cols))})", values)
    c.execute(
        "INSERT OR IGNORE INTO entity_sources(id, entity_id, source_type, source_id, visibility, created_at) "
        "VALUES(?,?,?,?,'restricted',?)",
        (f"src_finance_book_{row_id}", entity_id, SOURCE, row_id, at),
    )
    _identify(c, entity_id, row_id, emails)
    link(c, entity_id, row_id, str(fields.get("kind") or "vendor"), at, by)
    return row_id


def entity_of(c: sqlite3.Connection, contact_id: str) -> str:
    """The entity a contact id is (its book row's), or ""."""
    row = c.execute("SELECT entity_id FROM fin_book_entities WHERE id=?", (contact_id,)).fetchone()
    return str(row[0]) if row is not None else ""


def update(c: sqlite3.Connection, contact_id: str, fields: Mapping[str, object]) -> int:
    """Set `fields` on a contact's book row; a name goes on the entity too when the entity is this contact's own (its id
    is the contact id), never on one another book or the Directory made. How many rows changed."""
    row = c.execute("SELECT * FROM fin_book_entities WHERE id=?", (contact_id,)).fetchone()
    if row is None:
        return 0
    entity_id = str(row["entity_id"])
    at = str(fields.get("updated_at") or row["updated_at"] or row["created_at"])
    book = {k: fields[k] for k in (*BOOK_FIELDS, "updated_at") if k in fields}
    if "name" in book:
        name = book["name"] = str(book["name"] or "").strip()
        if entity_id == contact_id:
            c.execute(
                "UPDATE entities SET name=?, name_key=lower(trim(?)), updated_at=? WHERE id=? AND name IS NOT ?",
                (name, name, at, entity_id, name),
            )
    if book:
        cols = sorted(book)
        c.execute(
            f"UPDATE fin_book_entities SET {','.join(k + '=?' for k in cols)} WHERE id=?",
            [*(book[k] for k in cols), contact_id],
        )
    if "emails" in fields:
        _identify(c, entity_id, contact_id, _emails(fields["emails"]))
    if "kind" in fields:
        link(c, entity_id, contact_id, str(fields["kind"]), at, str(row["created_by"]))
    return 1


# ------------------------------------------------------------------------------------------------ aliases
def alias_add(c: sqlite3.Connection, book_id: str, name_key: str, contact_id: str, at: str) -> None:
    """A name a vendor goes by in this book; a name the book already maps is left as it is."""
    c.execute(
        "INSERT OR IGNORE INTO entity_identifiers(entity_id, kind, value, normalized_value, scope_type, scope_id, "
        "source_type, valid_from_on) VALUES(?, 'name', ?, ?, ?, ?, ?, ?)",
        (contact_id, name_key, name_key, ALIAS_SCOPE, book_id, ALIAS, at[:10]),
    )


def alias_put(
    c: sqlite3.Connection, book_id: str, name_key: str, contact_id: str, at: str, merged_from: str | None
) -> None:
    """A vendor merge's alias: a new name, or an alias moved to `contact_id` from the contact it was on."""
    source_type, source_id = (ALIAS, None) if merged_from is None else (MERGE_ALIAS, merged_from)
    moved = c.execute(
        "UPDATE entity_identifiers SET entity_id=?, source_type=?, source_id=? WHERE scope_type=? AND scope_id=? "
        "AND kind='name' AND normalized_value=?",
        (contact_id, source_type, source_id, ALIAS_SCOPE, book_id, name_key),
    ).rowcount
    if moved:
        return
    c.execute(
        "INSERT INTO entity_identifiers(entity_id, kind, value, normalized_value, scope_type, scope_id, source_type, "
        "source_id, valid_from_on) VALUES(?, 'name', ?, ?, ?, ?, ?, ?, ?)",
        (contact_id, name_key, name_key, ALIAS_SCOPE, book_id, source_type, source_id, at[:10]),
    )


def alias_delete(c: sqlite3.Connection, book_id: str, name_key: str) -> None:
    c.execute(
        "DELETE FROM entity_identifiers WHERE scope_type=? AND scope_id=? AND kind='name' AND normalized_value=?",
        (ALIAS_SCOPE, book_id, name_key),
    )


def alias_drop_contact(c: sqlite3.Connection, contact_ids: Sequence[str]) -> None:
    """Every book alias of these contacts (a test's or a reset's cleanup)."""
    c.execute(
        "DELETE FROM entity_identifiers WHERE scope_type=? AND kind='name' AND entity_id IN (SELECT value FROM json_each(?))",
        (ALIAS_SCOPE, json.dumps(list(contact_ids))),
    )
