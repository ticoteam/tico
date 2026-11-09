"""Finance fold-in (Directory track 9, spec §3 "Fold in"): every fin_contacts row becomes an organization entity, and
the ledger points at entities.

Built on the Directory migrations (20261108011017_directory_core, 20261108020258_directory_tables). This migration:

- fin_book_entities: what one book keeps about a contact (kind, billing emails, default account, 1099 and tax refs,
  payment ref, active, seats, renewal date) and the entity it is. Each row keeps the contact's id, and Finance keeps
  using that id: the fin_contacts view reads a book row as a contact under it, with the name the book uses (the
  contact's own, kept on the book row; the entity's when the book has none). So no ledger
  value is rewritten: bills, invoices, estimates, purchase orders, projects, lines, budgets, rules and refs keep theirs.
- Each contact id is an entity id. A contact becomes an organization entity under its own id, unless it joins another:
  the same vendor in two books (a normalized email they share) becomes one entity, the oldest contact's, and every
  other contact of the group becomes a merged entity that redirects to it (status merged, merged_into_id, an
  entity_merges row). Two contacts of one book are never merged: Finance's own vendor merge decides that.
- An email that names exactly one live organization the Directory already holds (from mail, a sync, a rule source; not
  self, and not one a book of the group already holds) reuses that entity: every contact of the group redirects to it.
  An entity with no sources is never reused: it shows to everyone, and a restricted source would narrow it. The
  reused entity keeps its name; each book keeps its own on its row.
- Each book row gives its entity a restricted finance_book source (source_id: the book row's id, which is the contact
  id Finance's visibility reads), so a book-only contact shows only to readers who see a name for it in Finance
  (backend/entities/visible.py, backend/finance/visible.py). Its emails become email identifiers; its kind becomes
  vendor_to / customer_of links to self (when self exists; ensure_self adds them
  when self comes later); a new entity gets organization kind company.
- fin_vendor_aliases -> entity_identifiers of kind name, scope_type finance_book, scope_id the book, entity_id the
  contact id, unique per book and name (entity_identifiers_finance_alias). merged_from rides on source_type
  finance_merge / source_id. fin_vendor_labels (names with no contact) stay.
- Every table with a foreign key to fin_contacts (found by PRAGMA foreign_key_list, not a fixed list: on main
  fin_bills.vendor_id, fin_invoices.customer_id, fin_estimates.customer_id, fin_purchase_orders.vendor_id and
  projects.customer_id) is rebuilt with the key pointing at entities(id): its rows, columns, indexes and triggers kept.
- fin_recipients gains entity_id, filled with the entity of its contact_id.
- fin_contacts and fin_vendor_aliases are dropped, and made again as read-only views with the same columns
  (store/schema.py VIEWS), so Finance's reads keep their SQL.

TODO (track 9 follow-up): contact refs stay in fin_external_refs (local_table fin_contacts) rather than moving to
entity_external_refs. The mirror sweeps them by seen_gen and keys them per connection, and entity_external_refs' unique
(system, external_type, external_id) would collide when a book reconnects to the same company.

It never loses a row and never stops startup on odd data: a contact with no email or no name is still an entity; a
contact reference that names no contact (a dangling id) gets a placeholder entity under that id ("Unknown contact"),
whose finance_book source names no book row, so only Finance's full-detail readers see it, and every reference still
resolves. Ids are deterministic (the contact's id; src_finance_book_<id>, lnk_finance_<id>_<key>, val_finance_<id>,
mrg_finance_<id>), and every Directory insert skips a row that is already there, so a replay finds the same rows. Book
rows are plain inserts: they are made once, in the same transaction that drops the old table. A name alias keeps the
date it was made (valid_from_on is a date column; the old created_at's time of day is not kept). Once the old
tables are gone a replay has nothing to move.
"""

import json
import re

from backend import hubdb as H

FOREIGN_KEYS_OFF = True  # ledger tables are rebuilt with their keys pointing at entities

SOURCE = "finance_book"
OLD = ("fin_contacts", "fin_vendor_aliases")
LINKS = {"vendor": ("vendor_to",), "customer": ("customer_of",), "both": ("customer_of", "vendor_to")}
BOOK_COLUMNS = (
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
    "created_at",
    "created_by",
    "updated_at",
)
BOOK_ENTITIES = """CREATE TABLE IF NOT EXISTS fin_book_entities (
  id TEXT PRIMARY KEY REFERENCES entities(id), book_id TEXT NOT NULL, entity_id TEXT NOT NULL REFERENCES entities(id),
  name TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'vendor' CHECK (kind IN ('vendor','customer','both')), emails TEXT NOT NULL DEFAULT '[]',
  default_account_id TEXT, is_1099 INTEGER NOT NULL DEFAULT 0, tax_id_ref TEXT,
  tax_id_last4 TEXT, payment_ref TEXT, active INTEGER NOT NULL DEFAULT 1, seats INTEGER, renewal_on TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT, UNIQUE (entity_id, book_id))"""
BOOK_INDEX = "CREATE INDEX IF NOT EXISTS fin_book_entities_book ON fin_book_entities(book_id)"
ALIAS_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS entity_identifiers_finance_alias ON entity_identifiers(scope_id, normalized_value) "
    "WHERE scope_type = 'finance_book' AND kind = 'name'"
)
# The views store/schema.py VIEWS makes, copied: a migration is a frozen snapshot.
VIEWS = (
    """CREATE VIEW IF NOT EXISTS fin_contacts AS
SELECT b.id AS id, b.book_id AS book_id, COALESCE(NULLIF(b.name, ''), e.name) AS name, b.kind AS kind,
  b.emails AS emails,
  b.default_account_id AS default_account_id, b.is_1099 AS is_1099, b.tax_id_ref AS tax_id_ref,
  b.payment_ref AS payment_ref, b.active AS active, b.created_at AS created_at, b.created_by AS created_by,
  b.updated_at AS updated_at, b.tax_id_last4 AS tax_id_last4, b.seats AS seats, b.renewal_on AS renewal_on
FROM fin_book_entities b JOIN entities e ON e.id = b.entity_id""",
    """CREATE VIEW IF NOT EXISTS fin_vendor_aliases AS
SELECT i.scope_id AS book_id, i.normalized_value AS name_key, i.entity_id AS contact_id, i.valid_from_on AS created_at,
  CASE WHEN i.source_type = 'finance_merge' THEN COALESCE(i.source_id, '') END AS merged_from
FROM entity_identifiers i WHERE i.scope_type = 'finance_book' AND i.kind = 'name'""",
)
REFERENCE = re.compile(r'REFERENCES\s+"?fin_contacts"?\s*\(\s*"?id"?\s*\)', re.IGNORECASE)


def _objects(conn):
    return dict(conn.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view')").fetchall())


def _tables(conn):
    return sorted(name for name, kind in _objects(conn).items() if kind == "table")


def _columns(conn, table):
    # Only names read from sqlite_master or fixed in this file reach here.
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _contact_keys(conn):
    """(table, column) of every foreign key to fin_contacts outside the old tables."""
    return [
        (table, row[3])
        for table in _tables(conn)
        if table not in OLD
        for row in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
        if row[2] == "fin_contacts"
    ]


def _emails(raw):
    try:
        found = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(found, list):
        return []
    return list(dict.fromkeys(str(e).strip().lower() for e in found if str(e or "").strip()))


def _key(name):
    return (name or "").casefold().strip()


def _contacts(conn):
    """Every contact, oldest first, with the columns this install has (an old one may lack the later ones)."""
    have = set(_columns(conn, "fin_contacts"))
    wanted = ("id", "book_id", "name", *BOOK_COLUMNS)
    select = ", ".join(column if column in have else f"NULL AS {column}" for column in wanted)
    rows = conn.execute(f"SELECT {select} FROM fin_contacts ORDER BY created_at, id").fetchall()
    return [dict(zip(wanted, row, strict=True)) for row in rows]


def _groups(contacts):
    """Contacts that share a normalized email across books, each group oldest first. A union that would put two contacts
    of one book together is skipped, so a group holds at most one contact per book."""
    rank = {c["id"]: n for n, c in enumerate(contacts)}
    parent = {c["id"]: c["id"] for c in contacts}
    books = {c["id"]: {c["book_id"]} for c in contacts}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    first = {}
    for contact in contacts:
        for email in _emails(contact["emails"]):
            if email not in first:
                first[email] = contact["id"]
                continue
            a, b = find(first[email]), find(contact["id"])
            if a == b or books[a] & books[b]:
                continue
            older, newer = (a, b) if rank[a] < rank[b] else (b, a)
            parent[newer] = older
            books[older] |= books.pop(newer)
    grouped = {}
    for contact in contacts:
        grouped.setdefault(find(contact["id"]), []).append(contact)
    return list(grouped.values())


SELF_ID = "company/self"  # Market's id for our company (backend/entities/store.py SELF_ID)


def _self_id(conn):
    """Our company, found as backend/entities/store.py self_id finds it (copied: a migration is a frozen snapshot): the
    row flagged self, else Market's company/self, which is flagged here as the first read would flag it."""
    row = conn.execute("SELECT id FROM entities WHERE self = 1").fetchone()
    if row:
        return row[0]
    if conn.execute("SELECT 1 FROM entities WHERE id = ?", (SELF_ID,)).fetchone():
        conn.execute("UPDATE entities SET self = 1 WHERE id = ?", (SELF_ID,))
        return SELF_ID
    return None


class _Directory:
    """What reuse asks of the Directory, read once and kept current as contacts fold in (one query per contact would
    scan the identifiers again for every contact): normalized email -> live organizations with it, entities with a
    source, and the books that hold each entity."""

    def __init__(self, conn, self_id):
        self.self_id = self_id
        self.by_email = {}
        for email, entity_id in conn.execute(
            "SELECT i.normalized_value, e.id FROM entity_identifiers i JOIN entities e ON e.id = i.entity_id "
            "WHERE i.kind = 'email' AND i.scope_type IS NULL AND e.type = 'organization' AND e.status <> 'merged' "
            "AND e.merged_into_id IS NULL AND e.deleted_at IS NULL"
        ):
            self.by_email.setdefault(email, set()).add(entity_id)
        self.sourced = {row[0] for row in conn.execute("SELECT DISTINCT entity_id FROM entity_sources")}
        self.held = {}
        if _objects(conn).get("fin_book_entities") == "table":
            for entity_id, book_id in conn.execute("SELECT entity_id, book_id FROM fin_book_entities"):
                self.held.setdefault(entity_id, set()).add(book_id)

    def reusable(self, group):
        """The one live organization the group's emails name, if exactly one: not self, not a contact's own id, one
        with a source (an entity with none shows to everyone, and a restricted source would narrow it), and not one a
        book of the group already holds."""
        ids = set()
        for email in {e for c in group for e in _emails(c["emails"])}:
            ids |= self.by_email.get(email, set())
        if len(ids) != 1:
            return None
        entity_id = ids.pop()
        if entity_id == self.self_id or entity_id in {c["id"] for c in group} or entity_id not in self.sourced:
            return None
        if self.held.get(entity_id, set()) & {c["book_id"] for c in group}:
            return None
        return entity_id

    def add(self, entity_id, book_id, emails):
        for email in emails:
            self.by_email.setdefault(email, set()).add(entity_id)
        self.sourced.add(entity_id)
        self.held.setdefault(entity_id, set()).add(book_id)


def _entity(conn, entity_id, name, by, at, now, *, merged_into=None, email=None):
    """An organization entity under this id, unless one is there; a new live one gets kind company."""
    if conn.execute("SELECT 1 FROM entities WHERE id = ?", (entity_id,)).fetchone():
        return
    conn.execute(
        "INSERT INTO entities (id, type, name, name_key, status, merged_into_id, primary_email, created_by, "
        "created_by_type, created_by_id, created_at, updated_at) "
        "VALUES (?, 'organization', ?, lower(trim(?)), ?, ?, ?, ?, 'system', '', ?, ?)",
        (
            entity_id,
            name,
            name,
            "merged" if merged_into else "active",
            merged_into,
            email,
            by or "system",
            at or now,
            now,
        ),
    )
    if merged_into is None:
        conn.execute(
            "INSERT OR IGNORE INTO entity_values (id, entity_id, entity_attribute_id, single_value, option_key, "
            "source_type, source_id, confidence, updated_at) "
            "VALUES (?, ?, 'attr_organization_kind', 1, 'company', ?, ?, 0.9, ?)",
            (f"val_finance_{entity_id}", entity_id, SOURCE, entity_id, now),
        )


def _source(conn, entity_id, row_id, now):
    conn.execute(
        "INSERT OR IGNORE INTO entity_sources (id, entity_id, source_type, source_id, visibility, created_at) "
        "VALUES (?, ?, ?, ?, 'restricted', ?)",
        (f"src_finance_book_{row_id}", entity_id, SOURCE, row_id, now),
    )


def _email(conn, entity_id, email, row_id, primary):
    if conn.execute(
        "SELECT 1 FROM entity_identifiers WHERE entity_id = ? AND kind = 'email' AND normalized_value = ? "
        "AND scope_type IS NULL AND source_type = ? AND source_id = ?",
        (entity_id, email, SOURCE, row_id),
    ).fetchone():
        return
    conn.execute(
        "INSERT INTO entity_identifiers (entity_id, kind, value, normalized_value, primary_identifier, source_type, "
        "source_id) VALUES (?, 'email', ?, ?, ?, ?, ?)",
        (entity_id, email, email, 1 if primary else 0, SOURCE, row_id),
    )


def _alias(conn, book_id, name_key, entity_id, on, merged_from=None):
    """A book's vendor name for an entity; the first name a book gives a key wins (the unique alias index)."""
    if not name_key:
        return
    source_type, source_id = ("finance_alias", None) if merged_from is None else ("finance_merge", merged_from)
    conn.execute(
        "INSERT OR IGNORE INTO entity_identifiers (entity_id, kind, value, normalized_value, scope_type, scope_id, "
        "source_type, source_id, valid_from_on) VALUES (?, 'name', ?, ?, ?, ?, ?, ?, ?)",
        (entity_id, name_key, name_key, SOURCE, book_id, source_type, source_id, on),
    )


def _links(conn, entity_id, row_id, kind, self_id, by, now):
    if self_id is None or self_id == entity_id:
        return
    for key in LINKS.get(kind, ()):
        conn.execute(
            "INSERT OR IGNORE INTO entity_links (id, from_entity_id, link_key, to_entity_id, confidence, source_type, "
            "source_id, properties, created_by, created_by_type, created_by_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0.9, ?, ?, '{}', ?, 'system', '', ?, ?)",
            (f"lnk_finance_{row_id}_{key}", entity_id, key, self_id, SOURCE, row_id, by or "system", now, now),
        )
    keys = sorted(
        {
            row[0]
            for row in conn.execute(
                # +to_entity_id: every Finance link points at self, so the to index would scan them all
                "SELECT link_key FROM entity_links WHERE from_entity_id = ? AND +to_entity_id = ? "
                "AND (valid_until_on IS NULL OR valid_until_on > ?)",
                (entity_id, self_id, now[:10]),
            )
        }
    )
    conn.execute("UPDATE entities SET self_link_keys = ? WHERE id = ?", (" ".join(keys), entity_id))


def _redirect_entity(conn, contact, target, now):
    """The contact's id as a merged entity pointing at `target`, logged as a merge."""
    _entity(
        conn,
        contact["id"],
        contact["name"] or "",
        contact["created_by"],
        contact["created_at"],
        now,
        merged_into=target,
    )
    prior = {
        "drop": {"status": "active", "merged_into_id": None, "mentions_count": 0},
        "keep": {"mentions_count": 0, "last_activity_at": None},
    }
    conn.execute(
        "INSERT OR IGNORE INTO entity_merges (id, entity_id, into_entity_id, moved, prior, actor_type, actor_id, "
        "created_at) VALUES (?, ?, ?, ?, ?, 'system', '', ?)",
        (
            f"mrg_finance_{contact['id']}",
            contact["id"],
            target,
            json.dumps({"links": [], "tables": {}, "values": []}, sort_keys=True),
            json.dumps(prior, sort_keys=True),
            now,
        ),
    )


def _fold_contacts(conn, now):
    """Each contact onto an entity. Returns {contact id: entity id}."""
    self_id = _self_id(conn)
    directory = _Directory(conn, self_id)
    moved = {}
    columns = ("id", "book_id", "entity_id", "name", *BOOK_COLUMNS)
    for group in _groups(_contacts(conn)):
        reused = directory.reusable(group)
        target = reused or group[0]["id"]
        if not reused:
            first = group[0]
            emails = _emails(first["emails"])
            _entity(
                conn,
                target,
                first["name"] or "",
                first["created_by"],
                first["created_at"],
                now,
                email=emails[0] if emails else None,
            )
        for contact in group:
            cid = contact["id"]
            values = {k: contact[k] for k in BOOK_COLUMNS}
            values["kind"] = values["kind"] if values["kind"] in LINKS else "vendor"
            values["emails"] = values["emails"] if values["emails"] is not None else "[]"
            values["is_1099"] = values["is_1099"] or 0
            values["active"] = 1 if values["active"] is None else values["active"]
            values["created_at"] = values["created_at"] or now
            values["created_by"] = values["created_by"] or "system"
            # the book keeps the name it uses; the view falls back to the entity's when it has none
            conn.execute(
                f"INSERT INTO fin_book_entities ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                (cid, contact["book_id"], target, contact["name"] or "", *(values[k] for k in BOOK_COLUMNS)),
            )
            _source(conn, target, cid, now)
            emails = _emails(contact["emails"])
            for n, email in enumerate(emails):
                _email(conn, target, email, cid, primary=n == 0 and cid == target)
            _links(conn, target, cid, values["kind"], self_id, values["created_by"], now)
            if cid != target:
                _redirect_entity(conn, contact, target, now)
            directory.add(target, contact["book_id"], emails)
            moved[cid] = target
    return moved


def _fold_aliases(conn):
    if _objects(conn).get("fin_vendor_aliases") != "table":
        return
    merged = "merged_from" if "merged_from" in _columns(conn, "fin_vendor_aliases") else "NULL"
    rows = conn.execute(
        f"SELECT book_id, name_key, contact_id, created_at, {merged} FROM fin_vendor_aliases ORDER BY created_at, rowid"
    ).fetchall()
    for book_id, name_key, contact_id, created_at, merged_from in rows:
        _alias(conn, book_id, name_key, contact_id, (created_at or "")[:10] or None, merged_from)


def _placeholders(conn, now):
    """A placeholder entity for each contact id a foreign key names that no entity has (a dangling reference), so the
    rebuilt keys still resolve. Its finance_book source names no book row, so only Finance's full-detail readers see
    it."""
    for table, column in _contact_keys(conn):
        dangling = conn.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" t WHERE "{column}" IS NOT NULL '
            f'AND NOT EXISTS (SELECT 1 FROM entities e WHERE e.id = t."{column}")'
        ).fetchall()
        for (value,) in dangling:
            _entity(conn, value, "Unknown contact", "system", now, now)
            _source(conn, value, value, now)


def _rebuild(conn, table):
    """`table` made again with its fin_contacts keys pointing at entities(id): columns, rows, indexes and triggers
    kept."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()[0]
    head = re.match(r'\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?("?)' + re.escape(table) + r"\1", sql, re.I)
    if head is None or not REFERENCE.search(sql):
        return
    folded = f"{table}_folded"
    keep = [
        row[0]
        for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('index', 'trigger') AND tbl_name = ? AND sql IS NOT NULL",
            (table,),
        ).fetchall()
    ]
    columns = ", ".join(f'"{c}"' for c in _columns(conn, table))
    conn.execute(f'DROP TABLE IF EXISTS "{folded}"')
    conn.execute(f'CREATE TABLE "{folded}"' + REFERENCE.sub("REFERENCES entities(id)", sql[head.end() :]))
    conn.execute(f'INSERT INTO "{folded}" ({columns}) SELECT {columns} FROM "{table}"')
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(f'ALTER TABLE "{folded}" RENAME TO "{table}"')
    for statement in keep:
        conn.execute(statement)


def _recipients(conn, moved):
    if _objects(conn).get("fin_recipients") != "table":
        return
    columns = _columns(conn, "fin_recipients")
    if "entity_id" not in columns:
        conn.execute("ALTER TABLE fin_recipients ADD COLUMN entity_id TEXT")
    if "contact_id" in columns:
        conn.executemany(
            "UPDATE fin_recipients SET entity_id = ? WHERE contact_id = ? AND entity_id IS NULL",
            [(new, old) for old, new in moved.items()],
        )


def up(conn):
    objects = _objects(conn)
    if objects.get("entities") != "table" or objects.get("entity_identifiers") != "table":
        return  # a file before the Directory's tables: Finance's schema makes the new shape once they exist
    conn.execute(ALIAS_INDEX)
    if objects.get("fin_contacts") != "table":
        # Nothing to fold: a new file, one already folded, or one whose Finance tables came before the Directory's
        # (its views wait for them: store/schema.py views).
        _recipients(conn, {})
        if objects.get("fin_book_entities") == "table":
            for statement in VIEWS:
                conn.execute(statement)
        return
    now = H.now()
    conn.execute(BOOK_ENTITIES)
    conn.execute(BOOK_INDEX)
    moved = _fold_contacts(conn, now)
    _fold_aliases(conn)
    _recipients(conn, moved)
    _placeholders(conn, now)
    for table in _tables(conn):
        if table not in OLD:
            _rebuild(conn, table)
    for table in OLD:
        if _objects(conn).get(table) == "table":
            conn.execute(f"DROP TABLE {table}")
    for statement in VIEWS:
        conn.execute(statement)
